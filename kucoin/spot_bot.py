#!/usr/bin/env python3
"""Paper-Trading-Bot fuer den KuCoin-Spotmarkt -- ohne Konto, ohne Orders.

Liest nur oeffentliche KuCoin-Spotdaten und fuehrt ein Scheinkonto:
  * Kauf nach einer Trendfolge-Regel auf 1h-Kerzen (nur long, kein Hebel)
  * feste Positionsgroesse: 10 % des Kontos, mindestens 100 USDT
  * Stop wird mit Minutenkerzen seit dem letzten Lauf nachgeprueft
  * Taker-Gebuehr und Slippage werden abgezogen

Nur Standardbibliothek, laeuft mit python3 >= 3.9.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ----------------------------------------------------------------------------
# Einstellungen
# ----------------------------------------------------------------------------
START_EQUITY = 1000.0     # USDT
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "LINK",
         "AVAX", "LTC", "BCH", "DOT", "NEAR", "AAVE", "UNI", "TRX"]

DONCHIAN_N = 960          # Kauf ueber dem Hoch der letzten 40 Tage (in Stunden)
DONCHIAN_EXIT = 480       # Verkauf unter dem Tief der letzten 20 Tage
ATR_N = 96                # ATR der Stundenkerzen, geglaettet ueber 4 Tage
STOP_ATR = 12.0           # Stop = 12 x Stunden-ATR (rund 2,5 Tagesspannen), wird nachgezogen

POSITION_FRACTION = 0.10  # 10 % des Kontos je Position ...
MIN_POSITION = 100.0      # ... aber mindestens 100 USDT; darunter kein Kauf

TAKER_FEE = 0.001         # 0,1 % je Seite (KuCoin Spot, Standardstufe)
SLIPPAGE = 0.0002         # zusaetzlich zum Kauf am Brief- / Verkauf am Geldkurs

# Ergebnis des Ruecktests dieser Einstellungen (kucoin/research/spot.py, KuCoin-Stundenkerzen,
# 16 Coins, 05.08.2024 bis 12.11.2026, mit Gebuehr und Slippage).
BACKTEST = {"from": "2024-08-05", "to": "2026-11-12", "cagr": 0.149, "max_dd": -0.305,
            "trades": 151, "win_rate": 0.37, "cagr_first": 0.266, "cagr_last": 0.042,
            "months_up": 9, "months": 27, "best_month": 0.484,
            "hold_total": 0.059, "hold_dd": -0.694, "hold_last": -0.44}

API = "https://api.kucoin.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "spot")
STATE_JSON = os.path.join(DATA, "state.json")
TRADES_JSON = os.path.join(DATA, "trades.json")
EQUITY_CSV = os.path.join(DATA, "equity.csv")
ERRORS_LOG = os.path.join(DATA, "errors.log")
DASH_JSON = os.path.join(ROOT, "docs", "spot.json")
HOUR = 3_600_000
GRAN = {1: "1min", 5: "5min", 15: "15min", 60: "1hour", 240: "4hour", 1440: "1day"}


# ----------------------------------------------------------------------------
# Reine Rechenfunktionen (in tests/ geprueft)
# ----------------------------------------------------------------------------
def atr_series(bars: list, n: int) -> list:
    """Wilder-ATR je Kerze; bars = [t, o, h, l, c, ...]. None, solange zu wenig Daten."""
    out, a, prev_c = [], None, None
    for i, b in enumerate(bars):
        h, l, c = b[2], b[3], b[4]
        tr = h - l if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c))
        a = tr if a is None else a + (tr - a) / n
        out.append(a if i >= n - 1 else None)
        prev_c = c
    return out


def signals(bars: list) -> dict | None:
    """Signal am Schluss der letzten Kerze: Kauf, Verkauf, Kanalgrenzen und ATR."""
    if len(bars) < max(DONCHIAN_N + 1, ATR_N + 1):
        return None
    c = bars[-1][4]
    a = atr_series(bars[-max(ATR_N * 5, 200):], ATR_N)[-1]
    prev = bars[:-1]
    hh = max(b[2] for b in prev[-DONCHIAN_N:])
    xl = min(b[3] for b in prev[-DONCHIAN_EXIT:])
    return {"enter": c > hh, "exit": c < xl, "close": c, "atr": a, "high": hh, "exit_low": xl,
            "why": f"Schluss {c:g} über dem 40-Tage-Hoch {hh:g}"}


def position_size(equity: float, free_cash: float, price: float, increment: float) -> tuple[float, float]:
    """Stueckzahl und Kaufwert: 10 % des Kontos, mindestens 100 USDT, nie mehr als frei ist."""
    target = max(equity * POSITION_FRACTION, MIN_POSITION)
    budget = free_cash / (1 + TAKER_FEE)              # die Gebuehr muss auch bezahlt werden
    if price <= 0 or budget < MIN_POSITION:
        return 0.0, 0.0
    qty = round(math.floor(min(target, budget) / price / increment + 1e-9) * increment, 10)
    return (qty, qty * price) if qty * price >= MIN_POSITION * 0.98 else (0.0, 0.0)


def stop_hit(stop: float, candles: list) -> tuple[int, float] | None:
    """Erste Kerze, in der der Stop erreicht wurde -> (Zeit, Ausfuehrungskurs vor Slippage)."""
    for b in candles:
        if b[3] <= stop:
            return b[0], min(b[1], stop)       # Kurslücke: schlechter als der Stop
    return None


def trail(stop: float, close: float, atr: float) -> float:
    return max(stop, close - STOP_ATR * atr)


def parse_candle(r: list) -> list:
    """KuCoin Spot liefert [Zeit s, Eroeffnung, Schluss, Hoch, Tief, Volumen, Umsatz]."""
    return [int(r[0]) * 1000, float(r[1]), float(r[3]), float(r[4]), float(r[2]), float(r[5])]


# ----------------------------------------------------------------------------
# Netz
# ----------------------------------------------------------------------------
def api(path: str, tries: int = 4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(API + path, headers={"User-Agent": "paper-bot/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                d = json.load(r)
            if d.get("code") == "200000":
                return d["data"]
            last = d
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"KuCoin-Abruf fehlgeschlagen: {path} -> {last}")


def klines(sym: str, gran_min: int, start_ms: int, end_ms: int) -> list:
    """Kerzen [t_ms, o, h, l, c, v] aufsteigend; holt in 1500er-Bloecken."""
    out, t, step = {}, start_ms // 1000, 1500 * gran_min * 60
    end = end_ms // 1000 + 1
    while t < end:
        q = urllib.parse.urlencode({"symbol": sym, "type": GRAN[gran_min],
                                    "startAt": t, "endAt": min(end, t + step)})
        for r in api("/api/v1/market/candles?" + q) or []:
            b = parse_candle(r)
            out[b[0]] = b
        t += step
    return [out[k] for k in sorted(out)]


def candles_since(sym: str, since_ms: int, now_ms: int) -> list:
    """Feinste Kerzen, die den Zeitraum in einem Abruf abdecken (fuer die Stop-Pruefung)."""
    mins = max(1, (now_ms - since_ms) // 60_000 + 1)
    gran = next(g for g in (1, 5, 15, 60, 240, 1440) if mins / g <= 1400 or g == 1440)
    return klines(sym, gran, since_ms - since_ms % (gran * 60_000), now_ms)


# ----------------------------------------------------------------------------
# Dateien
# ----------------------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_error(msg: str, now_ms: int) -> None:
    os.makedirs(DATA, exist_ok=True)
    lines = []
    if os.path.exists(ERRORS_LOG):
        with open(ERRORS_LOG, encoding="utf-8") as f:
            lines = f.read().splitlines()
    lines.append(f"{iso(now_ms)} {msg}")
    with open(ERRORS_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines[-200:]) + "\n")
    print("FEHLER:", msg, file=sys.stderr)


# ----------------------------------------------------------------------------
# Scheinkonto
# ----------------------------------------------------------------------------
class Account:
    def __init__(self, state: dict, trades: list, now_ms: int):
        self.s, self.trades, self.now = state, trades, now_ms
        self.s.setdefault("cash", START_EQUITY)        # freies Guthaben in USDT
        self.s.setdefault("positions", {})
        self.s.setdefault("blocked", [])               # nach Stop: erst wieder nach neuem Signal
        self.s.setdefault("last_eval", 0)
        self.s.setdefault("fees", 0.0)
        self.s.setdefault("watch", {})
        self.s.setdefault("started", iso(now_ms))
        self.marks: dict[str, float] = {}

    @property
    def pos(self) -> dict:
        return self.s["positions"]

    def invested(self) -> float:
        return sum(p["qty"] * self.marks.get(c, p["entry"]) for c, p in self.pos.items())

    def equity(self) -> float:
        return self.s["cash"] + self.invested()

    def buy(self, coin, qty, price, stop, atr, reason):
        cost, fee = qty * price, qty * price * TAKER_FEE
        self.s["cash"] -= cost + fee
        self.s["fees"] += fee
        self.pos[coin] = {"qty": qty, "entry": price, "stop": stop, "stop0": stop, "atr": atr,
                          "ts": self.now, "checked": self.now, "fee": fee, "reason": reason}
        self.marks[coin] = price
        print(f"  KAUF {coin} {qty:g} @ {price:g} = {cost:.2f} USDT, Stop {stop:g} ({reason})")

    def sell(self, coin, price, ts, why):
        p = self.pos.pop(coin)
        proceeds, fee = p["qty"] * price, p["qty"] * price * TAKER_FEE
        self.s["cash"] += proceeds - fee
        self.s["fees"] += fee
        net = p["qty"] * (price - p["entry"]) - fee - p["fee"]
        self.trades.append({
            "coin": coin, "qty": p["qty"], "entry": p["entry"], "exit": price,
            "entry_ts": iso(p["ts"]), "exit_ts": iso(ts),
            "notional": round(p["qty"] * p["entry"], 2), "fees": round(fee + p["fee"], 4),
            "pnl": round(net, 4), "ret": round(net / (p["qty"] * p["entry"]), 4),
            "reason": p["reason"], "exit_reason": why})
        print(f"  VERKAUF {coin} @ {price:g} ({why}) Ergebnis {net:+.2f} USDT")
        return net


def check_stops(acc: Account) -> None:
    for coin in list(acc.pos):
        p = acc.pos[coin]
        cs = [b for b in candles_since(coin + "-USDT", p["checked"], acc.now) if b[0] >= p["checked"]]
        # Die Minute des Kaufs zaehlt nicht mit: ihr Tief kann vor dem Kauf liegen.
        hit = stop_hit(p["stop"], [b for b in cs if b[0] >= p["ts"]])
        if cs:
            p["checked"] = cs[-1][0]
            acc.marks[coin] = cs[-1][4]
        if hit:
            t, px = hit
            trailing = p["stop"] != p["stop0"]
            acc.sell(coin, px * (1 - SLIPPAGE), t, "nachgezogener Stop" if trailing else "Stop")
            if coin not in acc.s["blocked"]:
                acc.s["blocked"].append(coin)


def evaluate(acc: Account, meta: dict) -> int:
    """Einmal je Stunde: Signale der letzten fertigen 1h-Kerze auswerten."""
    hour = acc.now - acc.now % HOUR
    if acc.s["last_eval"] >= hour:
        return 0
    bars_needed = max(DONCHIAN_N, ATR_N * 5) + 5
    errors = 0
    for coin in COINS:
        sym = coin + "-USDT"
        try:
            bars = [b for b in klines(sym, 60, hour - bars_needed * HOUR, hour) if b[0] < hour]
            if not bars or bars[-1][0] != hour - HOUR:
                raise RuntimeError("letzte 1h-Kerze fehlt")
            s = signals(bars)
            if s is None or s["atr"] is None or coin not in meta:
                continue
            acc.s["watch"][coin] = {"close": s["close"], "high": s["high"], "exit_low": s["exit_low"]}
            p = acc.pos.get(coin)
            if p and s["exit"]:
                bid = float(api(f"/api/v1/market/orderbook/level1?symbol={sym}")["bestBid"])
                acc.sell(coin, bid * (1 - SLIPPAGE), acc.now, "Trend gebrochen")
                p = None
            elif p:
                p["stop"] = trail(p["stop"], s["close"], s["atr"])
            if coin in acc.s["blocked"] and not s["enter"]:
                acc.s["blocked"].remove(coin)
            if p is None and s["enter"] and coin not in acc.s["blocked"]:
                ask = float(api(f"/api/v1/market/orderbook/level1?symbol={sym}")["bestAsk"])
                px = ask * (1 + SLIPPAGE)
                qty, _ = position_size(acc.equity(), acc.s["cash"], px, meta[coin])
                if qty:
                    acc.buy(coin, qty, px, px - STOP_ATR * s["atr"], s["atr"], s["why"])
                else:
                    acc.s["watch"][coin]["skipped"] = iso(acc.now)   # kein freies Guthaben
        except Exception as e:
            errors += 1
            log_error(f"{coin}: {e}", acc.now)
    acc.s["last_eval"] = hour
    return errors


def build_dashboard(acc: Account, run_errors: int) -> None:
    eq_now = acc.equity()
    os.makedirs(DATA, exist_ok=True)
    new = not os.path.exists(EQUITY_CSV)
    with open(EQUITY_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "equity", "invested"])
        w.writerow([iso(acc.now), round(eq_now, 2), round(acc.invested(), 2)])
    with open(EQUITY_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    eqs = [float(r["equity"]) for r in rows]
    peak, dd = START_EQUITY, 0.0
    for e in eqs:
        peak = max(peak, e)
        dd = min(dd, e / peak - 1)
    step = max(1, len(rows) // 400)
    curve = [{"ts": r["ts"], "eq": float(r["equity"])} for r in rows[::step]]
    if rows and curve[-1]["ts"] != rows[-1]["ts"]:
        curve.append({"ts": rows[-1]["ts"], "eq": eqs[-1]})
    t = acc.trades
    wins = [x["pnl"] for x in t if x["pnl"] > 0]
    losses = [-x["pnl"] for x in t if x["pnl"] <= 0]
    positions = []
    for coin, p in acc.pos.items():
        mark = acc.marks.get(coin, p["entry"])
        positions.append({
            "coin": coin, "qty": p["qty"], "entry": p["entry"], "mark": mark, "stop": p["stop"],
            "since": iso(p["ts"]), "value": round(p["qty"] * mark, 2),
            "pnl": round(p["qty"] * (mark - p["entry"]) - p["fee"], 2),
            "risk": round(max(0.0, mark - p["stop"]) * p["qty"], 2), "reason": p["reason"]})
    errs = []
    if os.path.exists(ERRORS_LOG):
        with open(ERRORS_LOG, encoding="utf-8") as f:
            errs = f.read().splitlines()[-5:]
    save_json(DASH_JSON, {
        "updated": iso(acc.now), "started": acc.s["started"],
        "config": {"start": START_EQUITY, "fraction": POSITION_FRACTION, "min_position": MIN_POSITION,
                   "stop_atr": STOP_ATR, "n": DONCHIAN_N, "exit": DONCHIAN_EXIT, "fee": TAKER_FEE,
                   "coins": COINS, "backtest": BACKTEST},
        "summary": {
            "equity": round(eq_now, 2), "pnl": round(eq_now - START_EQUITY, 2),
            "ret": round(eq_now / START_EQUITY - 1, 4), "max_dd": round(dd, 4),
            "trades": len(t), "wins": len(wins),
            "win_rate": round(len(wins) / len(t), 4) if t else None,
            "profit_factor": round(sum(wins) / sum(losses), 2) if losses and sum(losses) > 0 else None,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "fees": round(acc.s["fees"], 2), "cash": round(acc.s["cash"], 2),
            "invested": round(acc.invested(), 2),
            "invested_share": round(acc.invested() / eq_now, 4) if eq_now > 0 else None,
            "open": len(acc.pos),
        },
        "positions": positions, "curve": curve,
        "watch": [{"coin": c, **w, "held": c in acc.pos, "blocked": c in acc.s["blocked"]}
                  for c, w in acc.s["watch"].items()],
        "trades": sorted(t, key=lambda x: x["exit_ts"], reverse=True)[:80],
        "errors": errs, "run_errors": run_errors,
    })


def main() -> int:
    now_ms = int(time.time() * 1000)
    acc = Account(load_json(STATE_JSON, {}), load_json(TRADES_JSON, []), now_ms)
    errors = 0
    try:
        meta = {x["baseCurrency"]: float(x["baseIncrement"]) for x in api("/api/v2/symbols")
                if x["quoteCurrency"] == "USDT" and x["enableTrading"] and x["baseCurrency"] in COINS}
        check_stops(acc)
        errors += evaluate(acc, meta)
    except Exception as e:
        errors += 1
        log_error(f"Lauf: {e}", now_ms)
    save_json(STATE_JSON, acc.s)
    save_json(TRADES_JSON, acc.trades)
    build_dashboard(acc, errors)
    print(f"{iso(now_ms)} Konto {acc.equity():.2f} USDT · {len(acc.pos)} Positionen · "
          f"investiert {acc.invested():.2f} · frei {acc.s['cash']:.2f} · {len(acc.trades)} Trades")
    return 0


if __name__ == "__main__":
    sys.exit(main())
