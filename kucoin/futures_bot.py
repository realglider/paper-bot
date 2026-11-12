#!/usr/bin/env python3
"""Paper-Trading-Bot fuer KuCoin-USDT-Perpetuals (Futures) -- ohne Konto, ohne Orders.

Liest nur oeffentliche KuCoin-Futures-Daten und fuehrt ein Scheinkonto:
  * Einstieg/Ausstieg nach einer Trendfolge-Regel auf 1h-Kerzen
  * Positionsgroesse nach Risiko: Abstand zum Stop bestimmt die Groesse
  * Stop wird mit Minutenkerzen seit dem letzten Lauf nachgeprueft
  * Taker-Gebuehr, Slippage und Funding werden abgezogen

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
START_EQUITY = 1000.0
COINS = ["XBT", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "LINK",
         "AVAX", "LTC", "BCH", "DOT", "NEAR", "AAVE", "UNI", "TRX"]

STRATEGY = "donchian"     # "donchian" oder "ema"
DONCHIAN_N = 960          # Ausbruch ueber das Hoch/Tief der letzten 40 Tage (in Stunden)
DONCHIAN_EXIT = 480       # Ausstieg am Gegenkanal der letzten 20 Tage
EMA_FAST, EMA_SLOW = 100, 400
ATR_N = 96                # ATR der Stundenkerzen, geglaettet ueber 4 Tage
STOP_ATR = 12.0           # Stop = 12 x Stunden-ATR (rund 2,5 Tagesspannen), wird nachgezogen
SIDES = "both"            # "both", "long" oder "short"

RISK_PER_TRADE = 0.005    # Zielgroesse: 0,5 % des Kontos gehen verloren, wenn der Stop greift
MIN_NOTIONAL = 100.0      # jede Position ist mindestens 100 USDT gross ...
MAX_RISK_PER_TRADE = 0.01 # ... und wird ausgelassen, wenn sie damit mehr als 1 % riskieren wuerde
MAX_POS_LEVERAGE = 1.5    # eine Position hoechstens 1,5 x Konto
MAX_GROSS_LEVERAGE = 3.0  # alle Positionen zusammen hoechstens 3 x Konto
ACCOUNT_LEVERAGE = 10     # eingestellter Hebel (Obergrenze, bestimmt nur die Margin)

TAKER_FEE = 0.0006        # 0,06 % je Seite
SLIPPAGE = 0.0002         # zusaetzlich zum Kauf am Brief- / Verkauf am Geldkurs
MAINT_MARGIN = 0.01       # Zwangsliquidation, wenn Konto < 1 % des Positionswerts

# Ergebnis des Ruecktests dieser Einstellungen (kucoin/research/, KuCoin-Stundenkerzen,
# 16 Coins, 05.08.2024 bis 12.11.2026, mit Gebuehr, Slippage und Funding).
BACKTEST = {"from": "2024-08-05", "to": "2026-11-12", "cagr": 0.139, "max_dd": -0.105,
            "trades": 131, "win_rate": 0.41, "pf_first": 1.33, "pf_last": 1.77,
            "cagr_first": 0.018, "cagr_last": 0.249, "months_up": 13, "months": 27,
            "top5_share": 0.64}

API = "https://api-futures.kucoin.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "futures")
STATE_JSON = os.path.join(DATA, "state.json")
TRADES_JSON = os.path.join(DATA, "trades.json")
EQUITY_CSV = os.path.join(DATA, "equity.csv")
ERRORS_LOG = os.path.join(DATA, "errors.log")
DASH_JSON = os.path.join(ROOT, "docs", "futures.json")
HOUR = 3_600_000


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


def ema_last(values: list, span: int):
    if len(values) < span:
        return None
    k, e = 2.0 / (span + 1), values[0]
    for v in values[1:]:
        e += (v - e) * k
    return e


def signals(bars: list) -> dict | None:
    """Signal am Schluss der letzten Kerze: Einstieg/Ausstieg je Richtung und ATR."""
    need = max(DONCHIAN_N + 1, ATR_N + 1) if STRATEGY == "donchian" else EMA_SLOW * 3
    if len(bars) < need:
        return None
    c = bars[-1][4]
    a = atr_series(bars[-max(ATR_N * 5, 200):], ATR_N)[-1]
    if STRATEGY == "donchian":
        prev = bars[:-1]
        hh = max(b[2] for b in prev[-DONCHIAN_N:])
        ll = min(b[3] for b in prev[-DONCHIAN_N:])
        xh = max(b[2] for b in prev[-DONCHIAN_EXIT:])
        xl = min(b[3] for b in prev[-DONCHIAN_EXIT:])
        s = {"enter_long": c > hh, "enter_short": c < ll, "exit_long": c < xl, "exit_short": c > xh,
             "why_long": f"Schluss {c:g} über dem {DONCHIAN_N}-h-Hoch {hh:g}",
             "why_short": f"Schluss {c:g} unter dem {DONCHIAN_N}-h-Tief {ll:g}",
             "high": hh, "low": ll}
    else:
        closes = [b[4] for b in bars]
        f, sl = ema_last(closes, EMA_FAST), ema_last(closes, EMA_SLOW)
        s = {"enter_long": f > sl and c > sl, "enter_short": f < sl and c < sl,
             "exit_long": f < sl, "exit_short": f > sl,
             "why_long": f"EMA{EMA_FAST} über EMA{EMA_SLOW}", "why_short": f"EMA{EMA_FAST} unter EMA{EMA_SLOW}"}
    s.update({"close": c, "atr": a})
    return s


def position_size(equity: float, price: float, stop_dist: float, gross_now: float,
                  multiplier: float, lot: int = 1) -> tuple[int, float]:
    """Anzahl Kontrakte und Positionswert.

    Ziel: Verlust am Stop = RISK_PER_TRADE x Konto, aber mindestens MIN_NOTIONAL gross.
    Ist der Stop so weit weg, dass schon die Mindestgroesse mehr als MAX_RISK_PER_TRADE
    riskiert, wird der Trade ausgelassen.
    """
    if equity <= 0 or price <= 0 or stop_dist <= 0:
        return 0, 0.0
    stop_pct = stop_dist / price
    notional = max(equity * RISK_PER_TRADE / stop_pct, MIN_NOTIONAL)
    notional = min(notional, equity * MAX_POS_LEVERAGE, equity * MAX_GROSS_LEVERAGE - gross_now)
    step = price * multiplier * lot
    lots = math.floor(notional / step + 1e-9) * lot
    if lots * multiplier * price < MIN_NOTIONAL:          # auf die Mindestgroesse aufrunden
        lots = math.ceil(MIN_NOTIONAL / step - 1e-9) * lot
    value = lots * multiplier * price
    if (lots <= 0 or value * stop_pct > equity * MAX_RISK_PER_TRADE
            or value > equity * MAX_GROSS_LEVERAGE - gross_now):
        return 0, 0.0
    return lots, value


def stop_hit(side: int, stop: float, candles: list) -> tuple[int, float] | None:
    """Erste Kerze, in der der Stop erreicht wurde -> (Zeit, Ausfuehrungskurs vor Slippage)."""
    for b in candles:
        t, o, h, l = b[0], b[1], b[2], b[3]
        if side > 0 and l <= stop:
            return t, min(o, stop)       # Kurslücke: schlechter als der Stop
        if side < 0 and h >= stop:
            return t, max(o, stop)
    return None


def trail(side: int, stop: float, close: float, atr: float) -> float:
    return max(stop, close - STOP_ATR * atr) if side > 0 else min(stop, close + STOP_ATR * atr)


def close_pnl(side: int, qty: float, entry: float, exit_px: float) -> tuple[float, float]:
    """(Gewinn vor Gebuehr, Ausstiegsgebuehr)."""
    return side * qty * (exit_px - entry), qty * exit_px * TAKER_FEE


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
    """Kerzen [t, o, h, l, c, v] aufsteigend; holt in 200er-Bloecken."""
    out, t, step = {}, start_ms, 200 * gran_min * 60_000
    while t < end_ms:
        q = urllib.parse.urlencode({"symbol": sym, "granularity": gran_min,
                                    "from": t, "to": min(end_ms, t + step - 1)})
        for r in api("/api/v1/kline/query?" + q) or []:
            out[r[0]] = r[:6]
        t += step
    return [out[k] for k in sorted(out)]


def candles_since(sym: str, since_ms: int, now_ms: int) -> list:
    """Feinste Kerzen, die den Zeitraum in einem Abruf abdecken (fuer die Stop-Pruefung)."""
    mins = max(1, (now_ms - since_ms) // 60_000 + 1)
    gran = next(g for g in (1, 5, 15, 60, 240, 1440) if mins / g <= 200 or g == 1440)
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
        self.s.setdefault("cash", START_EQUITY)        # realisiertes Konto
        self.s.setdefault("positions", {})
        self.s.setdefault("blocked", {})               # nach Stop: erst wieder nach neuem Signal
        self.s.setdefault("last_eval", 0)
        self.s.setdefault("fees", 0.0)
        self.s.setdefault("funding", 0.0)
        self.s.setdefault("started", iso(now_ms))
        self.marks: dict[str, float] = {}

    @property
    def pos(self) -> dict:
        return self.s["positions"]

    def equity(self) -> float:
        return self.s["cash"] + sum(p["side"] * p["qty"] * (self.marks.get(c, p["entry"]) - p["entry"])
                                    for c, p in self.pos.items())

    def gross(self) -> float:
        return sum(p["qty"] * self.marks.get(c, p["entry"]) for c, p in self.pos.items())

    def open(self, coin, side, lots, mult, price, stop, atr, reason):
        qty = lots * mult
        fee = qty * price * TAKER_FEE
        self.s["cash"] -= fee
        self.s["fees"] += fee
        self.pos[coin] = {"side": side, "lots": lots, "qty": qty, "entry": price, "stop": stop,
                          "stop0": stop, "atr": atr, "ts": self.now, "checked": self.now,
                          "funded": self.now, "fee": fee, "funding": 0.0, "reason": reason,
                          "margin": qty * price / ACCOUNT_LEVERAGE}
        print(f"  EINSTIEG {coin} {'long' if side > 0 else 'short'} {qty:g} @ {price:g} "
              f"Stop {stop:g} ({reason})")

    def close(self, coin, price, ts, why):
        p = self.pos.pop(coin)
        gross, fee = close_pnl(p["side"], p["qty"], p["entry"], price)
        self.s["cash"] += gross - fee
        self.s["fees"] += fee
        net = gross - fee - p["fee"] - p["funding"]
        self.trades.append({
            "coin": coin, "side": "long" if p["side"] > 0 else "short", "qty": p["qty"],
            "entry": p["entry"], "exit": price, "entry_ts": iso(p["ts"]), "exit_ts": iso(ts),
            "notional": round(p["qty"] * p["entry"], 2), "fees": round(fee + p["fee"], 4),
            "funding": round(p["funding"], 4), "pnl": round(net, 4),
            "r": round(net / (p["qty"] * abs(p["entry"] - p["stop0"])), 2) if p["entry"] != p["stop0"] else None,
            "reason": p["reason"], "exit_reason": why})
        print(f"  AUSSTIEG {coin} @ {price:g} ({why}) Ergebnis {net:+.2f} $")
        return net


def check_stops(acc: Account) -> None:
    for coin in list(acc.pos):
        p, sym = acc.pos[coin], coin + "USDTM"
        cs = [b for b in candles_since(sym, p["checked"], acc.now) if b[0] >= p["checked"]]
        # Die Minute des Einstiegs zaehlt nicht mit: ihr Tief kann vor dem Einstieg liegen.
        hit = stop_hit(p["side"], p["stop"], [b for b in cs if b[0] >= p["ts"]])
        if cs:
            p["checked"] = cs[-1][0]
            acc.marks[coin] = cs[-1][4]
        if hit:
            t, px = hit
            px *= 1 - p["side"] * SLIPPAGE
            trailing = p["stop"] != p["stop0"]
            acc.close(coin, px, t, "nachgezogener Stop" if trailing else "Stop")
            acc.s["blocked"][coin] = p["side"]


def apply_funding(acc: Account) -> None:
    for coin, p in acc.pos.items():
        if acc.now - p["funded"] < 60_000:
            continue
        q = urllib.parse.urlencode({"symbol": coin + "USDTM", "from": p["funded"] + 1, "to": acc.now})
        for r in api("/api/v1/contract/funding-rates?" + q) or []:
            if r["timepoint"] > p["ts"]:
                cost = p["side"] * p["qty"] * acc.marks.get(coin, p["entry"]) * float(r["fundingRate"])
                acc.s["cash"] -= cost            # long zahlt bei positiver Rate
                acc.s["funding"] += cost
                p["funding"] += cost
        p["funded"] = acc.now


def evaluate(acc: Account, meta: dict) -> int:
    """Einmal je Stunde: Signale der letzten fertigen 1h-Kerze auswerten."""
    hour = acc.now - acc.now % HOUR
    if acc.s["last_eval"] >= hour:
        return 0
    bars_needed = (max(DONCHIAN_N, ATR_N * 5) + 5) if STRATEGY == "donchian" else EMA_SLOW * 3 + 5
    errors = 0
    for coin in COINS:
        sym = coin + "USDTM"
        try:
            bars = [b for b in klines(sym, 60, hour - bars_needed * HOUR, hour - 1) if b[0] < hour]
            if not bars or bars[-1][0] != hour - HOUR:
                raise RuntimeError("letzte 1h-Kerze fehlt")
            s = signals(bars)
            if s is None or s["atr"] is None or coin not in meta:
                continue
            if "high" in s:
                acc.s.setdefault("watch", {})[coin] = {"close": s["close"], "high": s["high"],
                                                       "low": s["low"], "atr": s["atr"]}
            p = acc.pos.get(coin)
            tk = api(f"/api/v1/ticker?symbol={sym}")
            bid, ask = float(tk["bestBidPrice"]), float(tk["bestAskPrice"])
            acc.marks[coin] = (bid + ask) / 2
            if p and ((p["side"] > 0 and s["exit_long"]) or (p["side"] < 0 and s["exit_short"])):
                px = (bid if p["side"] > 0 else ask) * (1 - p["side"] * SLIPPAGE)
                acc.close(coin, px, acc.now, "Trend gebrochen")
                p = None
            elif p:
                p["stop"] = trail(p["side"], p["stop"], s["close"], s["atr"])
            blk = acc.s["blocked"].get(coin)
            if blk and not s["enter_long" if blk > 0 else "enter_short"]:
                acc.s["blocked"].pop(coin)
                blk = None
            if p is None:
                side = (1 if s["enter_long"] and SIDES != "short" else
                        -1 if s["enter_short"] and SIDES != "long" else 0)
                if side and side != blk:
                    px = (ask if side > 0 else bid) * (1 + side * SLIPPAGE)
                    dist = STOP_ATR * s["atr"]
                    m = meta[coin]
                    lots, notional = position_size(acc.equity(), px, dist, acc.gross(),
                                                   m["multiplier"], m["lot"])
                    if not lots:
                        acc.s.setdefault("watch", {}).setdefault(coin, {})["skipped"] = iso(acc.now)
                    if lots:
                        acc.open(coin, side, lots, m["multiplier"], px, px - side * dist, s["atr"],
                                 s["why_long" if side > 0 else "why_short"])
        except Exception as e:
            errors += 1
            log_error(f"{coin}: {e}", acc.now)
    acc.s["last_eval"] = hour
    return errors


def liquidation_check(acc: Account) -> None:
    if acc.pos and acc.equity() <= acc.gross() * MAINT_MARGIN:
        for coin in list(acc.pos):
            acc.close(coin, acc.marks.get(coin, acc.pos[coin]["entry"]), acc.now, "Zwangsliquidation")
        acc.s["liquidated"] = iso(acc.now)


def build_dashboard(acc: Account, run_errors: int) -> None:
    eq_now = acc.equity()
    os.makedirs(DATA, exist_ok=True)
    new = not os.path.exists(EQUITY_CSV)
    with open(EQUITY_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "equity", "gross"])
        w.writerow([iso(acc.now), round(eq_now, 2), round(acc.gross(), 2)])
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
            "coin": coin, "side": "long" if p["side"] > 0 else "short", "qty": p["qty"],
            "entry": p["entry"], "mark": mark, "stop": p["stop"], "since": iso(p["ts"]),
            "notional": round(p["qty"] * mark, 2), "margin": round(p["margin"], 2),
            "pnl": round(p["side"] * p["qty"] * (mark - p["entry"]) - p["fee"] - p["funding"], 2),
            "risk": round(max(0.0, p["side"] * (mark - p["stop"])) * p["qty"], 2), "reason": p["reason"]})
    errs = []
    if os.path.exists(ERRORS_LOG):
        with open(ERRORS_LOG, encoding="utf-8") as f:
            errs = f.read().splitlines()[-5:]
    save_json(DASH_JSON, {
        "updated": iso(acc.now), "started": acc.s["started"],
        "config": {"start": START_EQUITY, "risk": RISK_PER_TRADE, "stop_atr": STOP_ATR,
                   "max_gross": MAX_GROSS_LEVERAGE, "max_pos": MAX_POS_LEVERAGE,
                   "account_leverage": ACCOUNT_LEVERAGE, "strategy": STRATEGY,
                   "min_notional": MIN_NOTIONAL, "max_risk": MAX_RISK_PER_TRADE,
                   "n": DONCHIAN_N, "exit": DONCHIAN_EXIT, "coins": COINS,
                   "backtest": BACKTEST},
        "summary": {
            "equity": round(eq_now, 2), "pnl": round(eq_now - START_EQUITY, 2),
            "ret": round(eq_now / START_EQUITY - 1, 4), "max_dd": round(dd, 4),
            "trades": len(t), "wins": len(wins),
            "win_rate": round(len(wins) / len(t), 4) if t else None,
            "profit_factor": round(sum(wins) / sum(losses), 2) if losses and sum(losses) > 0 else None,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "fees": round(acc.s["fees"], 2), "funding": round(acc.s["funding"], 2),
            "gross": round(acc.gross(), 2),
            "leverage": round(acc.gross() / eq_now, 2) if eq_now > 0 else None,
            "open": len(acc.pos), "liquidated": acc.s.get("liquidated"),
        },
        "positions": positions, "curve": curve,
        "watch": [{"coin": c, **w, "held": c in acc.pos, "blocked": acc.s["blocked"].get(c, 0)}
                  for c, w in acc.s.get("watch", {}).items()],
        "trades": sorted(t, key=lambda x: x["exit_ts"], reverse=True)[:80],
        "errors": errs, "run_errors": run_errors,
    })


def main() -> int:
    now_ms = int(time.time() * 1000)
    acc = Account(load_json(STATE_JSON, {}), load_json(TRADES_JSON, []), now_ms)
    errors = 0
    if not acc.s.get("liquidated"):
        try:
            meta = {c["symbol"][:-5]: {"multiplier": float(c["multiplier"]), "lot": int(c["lotSize"]),
                                       "mark": float(c["markPrice"])}
                    for c in api("/api/v1/contracts/active")
                    if c["symbol"].endswith("USDTM") and c.get("status") == "Open"}
            for coin in acc.pos:
                if coin in meta:
                    acc.marks[coin] = meta[coin]["mark"]
            check_stops(acc)
            apply_funding(acc)
            errors += evaluate(acc, meta)
            liquidation_check(acc)
        except Exception as e:
            errors += 1
            log_error(f"Lauf: {e}", now_ms)
    save_json(STATE_JSON, acc.s)
    save_json(TRADES_JSON, acc.trades)
    build_dashboard(acc, errors)
    print(f"{iso(now_ms)} Konto {acc.equity():.2f} $ · {len(acc.pos)} offen · "
          f"Hebel {acc.gross() / max(acc.equity(), 1e-9):.2f}x · {len(acc.trades)} Trades")
    return 0


if __name__ == "__main__":
    sys.exit(main())
