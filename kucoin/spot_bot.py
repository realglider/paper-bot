#!/usr/bin/env python3
"""Spot-Teil des Paper-Bots: Momentum-Scanner ueber den ganzen KuCoin-Spotmarkt.

Liest nur oeffentliche KuCoin-Spotdaten und fuehrt ein Scheinkonto (kein Konto, keine Orders):
  * einmal am Tag alle liquiden USDT-Paare durchsuchen -- neue Coins kommen von selbst dazu,
    sobald sie genug Historie und Umsatz haben
  * Kauf bei Ausbruch ueber das 20-Tage-Hoch mit Volumenspitze und ueber dem 50-Tage-Schnitt
  * feste 100 USDT je Position, Stop, nachgezogener Stop und Gewinnmitnahme
  * Stops und Gewinnziel werden mit Minutenkerzen seit dem letzten Lauf nachgeprueft

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
START_EQUITY = 500.0      # USDT im Spot-Konto
POSITION_SIZE = 100.0     # USDT je Kauf
MAX_POSITIONS = 5

HIGH_N = 20               # Schlusskurs ueber dem Hoch der letzten 20 Tage ...
VOL_MULT = 3.0            # ... bei mindestens dreifachem Tagesumsatz (gegen den 20-Tage-Schnitt)
SMA_N = 50                # ... und ueber dem 50-Tage-Durchschnitt
MIN_MEDIAN_VOLUME = 1_000_000   # nur Coins mit mind. 1 Mio. USDT Tagesumsatz (Median 20 Tage)
MAX_DAY_GAIN = 0.50       # Tageskerzen mit mehr als +50 % nicht hinterherkaufen
PREFILTER_VOLUME = 300_000      # 24h-Umsatz, ab dem ein Coin ueberhaupt geprueft wird

HARD_STOP = 0.12          # Verkauf 12 % unter dem Kaufkurs
TRAIL_STOP = 0.15         # oder 15 % unter dem hoechsten Tagesschluss seit dem Kauf
TAKE_PROFIT = 0.40        # Gewinnmitnahme bei +40 %
COOLDOWN_DAYS = 2         # so lange nach einem Verkauf kein neuer Kauf desselben Coins

TAKER_FEE = 0.001         # 0,1 % je Seite (KuCoin Spot, Standardstufe)
SLIPPAGE = 0.001          # zusaetzlich zum Kauf am Brief- / Verkauf am Geldkurs

EXCLUDE = {"USDC", "DAI", "TUSD", "FDUSD", "USDD", "USDE", "PYUSD", "EUR", "USD1", "USDP",
           "PAXG", "XAUT", "BUSD", "USDJ", "GUSD", "EURT", "AEUR"}     # Stablecoins, Gold

# Ergebnis des Ruecktests dieser Einstellungen (kucoin/research/spotmom3.py, Tageskerzen aller
# heute handelbaren KuCoin-USDT-Paare, 05.08.2024 bis 12.11.2026, 0,1 % Gebuehr, 0,3 % Slippage).
BACKTEST = {"from": "2024-08-05", "to": "2026-11-12", "cagr": 0.117, "max_dd": -0.311,
            "trades": 130, "win_rate": 0.33, "cagr_first": 0.104, "cagr_last": 0.058,
            "months_up": 8, "months": 27, "best_month": 0.467, "avg_win": 26.3, "avg_loss": -11.3}

API = "https://api.kucoin.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "spot")
STATE_JSON = os.path.join(DATA, "state.json")
TRADES_JSON = os.path.join(DATA, "trades.json")
EQUITY_CSV = os.path.join(DATA, "equity.csv")
ERRORS_LOG = os.path.join(DATA, "errors.log")
DASH_JSON = os.path.join(ROOT, "docs", "spot.json")
HOUR = 3_600_000
DAY = 86_400_000
GRAN = {1: "1min", 5: "5min", 15: "15min", 60: "1hour", 240: "4hour", 1440: "1day"}


# ----------------------------------------------------------------------------
# Reine Rechenfunktionen (in tests/ geprueft)
# ----------------------------------------------------------------------------
def is_excluded(base: str) -> bool:
    """Stablecoins und Hebel-Token (BTC3L, ETH3S, ...UP/DOWN) gehoeren nicht ins Universum."""
    return (base in EXCLUDE or base.endswith(("3L", "3S", "2L", "2S", "UP", "DOWN")))


def signal(days: list) -> dict | None:
    """Kaufsignal am Schluss der letzten fertigen Tageskerze. days = [t, o, h, l, c, v, umsatz]."""
    need = max(HIGH_N, SMA_N) + 1
    if len(days) < need:
        return None
    d, prev = days[-1], days[:-1]
    hh = max(b[2] for b in prev[-HIGH_N:])
    vols = [b[6] for b in prev[-20:]]
    vol_avg, vol_med = sum(vols) / len(vols), sorted(vols)[len(vols) // 2]
    sma = sum(b[4] for b in days[-SMA_N:]) / SMA_N
    ok = (d[4] > hh and d[6] > VOL_MULT * vol_avg and d[4] > sma
          and vol_med > MIN_MEDIAN_VOLUME and d[4] < d[1] * (1 + MAX_DAY_GAIN))
    return {"buy": ok, "close": d[4], "high": hh, "vol_ratio": d[6] / vol_avg if vol_avg else 0.0,
            "sma": sma, "vol_med": vol_med, "volume": d[6], "liquid": vol_med > MIN_MEDIAN_VOLUME}


def stop_level(entry: float, peak: float) -> float:
    return max(entry * (1 - HARD_STOP), peak * (1 - TRAIL_STOP))


def exit_hit(stop: float, target: float, candles: list) -> tuple[int, float, str] | None:
    """Erste Kerze mit Stop oder Gewinnziel. Liegt beides in einer Kerze, zaehlt der Stop."""
    for b in candles:
        if b[3] <= stop:
            return b[0], min(b[1], stop), "stop"       # Kurslücke: schlechter als der Stop
        if b[2] >= target:
            return b[0], max(b[1], target), "target"
    return None


def buy_qty(cash: float, price: float, increment: float) -> float:
    """Stueckzahl fuer POSITION_SIZE, 0 wenn das Guthaben nicht reicht."""
    if price <= 0 or cash < POSITION_SIZE * (1 + TAKER_FEE):
        return 0.0
    return round(math.floor(POSITION_SIZE / price / increment + 1e-9) * increment, 10)


def parse_candle(r: list) -> list:
    """KuCoin Spot liefert [Zeit s, Eroeffnung, Schluss, Hoch, Tief, Volumen, Umsatz]."""
    return [int(r[0]) * 1000, float(r[1]), float(r[3]), float(r[4]), float(r[2]), float(r[5]),
            float(r[6])]


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
        self.s.setdefault("last_exit", {})             # Coin -> Zeit des letzten Verkaufs
        self.s.setdefault("last_scan", 0)
        self.s.setdefault("fees", 0.0)
        self.s.setdefault("scan", {})
        self.s.setdefault("started", iso(now_ms))
        self.marks: dict[str, float] = {}

    @property
    def pos(self) -> dict:
        return self.s["positions"]

    def invested(self) -> float:
        return sum(p["qty"] * self.marks.get(c, p["entry"]) for c, p in self.pos.items())

    def equity(self) -> float:
        return self.s["cash"] + self.invested()

    def buy(self, coin, qty, price, reason):
        cost, fee = qty * price, qty * price * TAKER_FEE
        self.s["cash"] -= cost + fee
        self.s["fees"] += fee
        self.pos[coin] = {"qty": qty, "entry": price, "peak": price, "ts": self.now,
                          "checked": self.now, "fee": fee, "reason": reason}
        self.marks[coin] = price
        print(f"  KAUF {coin} {qty:g} @ {price:g} = {cost:.2f} USDT ({reason})")

    def sell(self, coin, price, ts, why):
        p = self.pos.pop(coin)
        proceeds, fee = p["qty"] * price, p["qty"] * price * TAKER_FEE
        self.s["cash"] += proceeds - fee
        self.s["fees"] += fee
        self.s["last_exit"][coin] = ts
        net = p["qty"] * (price - p["entry"]) - fee - p["fee"]
        self.trades.append({
            "coin": coin, "qty": p["qty"], "entry": p["entry"], "exit": price,
            "entry_ts": iso(p["ts"]), "exit_ts": iso(ts),
            "notional": round(p["qty"] * p["entry"], 2), "fees": round(fee + p["fee"], 4),
            "pnl": round(net, 4), "ret": round(net / (p["qty"] * p["entry"]), 4),
            "reason": p["reason"], "exit_reason": why})
        print(f"  VERKAUF {coin} @ {price:g} ({why}) Ergebnis {net:+.2f} USDT")
        return net


def check_exits(acc: Account) -> None:
    for coin in list(acc.pos):
        p = acc.pos[coin]
        cs = [b for b in candles_since(coin + "-USDT", p["checked"], acc.now) if b[0] >= p["checked"]]
        stop, target = stop_level(p["entry"], p["peak"]), p["entry"] * (1 + TAKE_PROFIT)
        # Die Minute des Kaufs zaehlt nicht mit: ihr Tief kann vor dem Kauf liegen.
        hit = exit_hit(stop, target, [b for b in cs if b[0] >= p["ts"]])
        if cs:
            p["checked"] = cs[-1][0]
            acc.marks[coin] = cs[-1][4]
        if hit:
            t, px, kind = hit
            if kind == "target":
                acc.sell(coin, px, t, "Gewinnziel +40 %")          # Limit-Order, keine Slippage
            else:
                trailing = p["peak"] * (1 - TRAIL_STOP) > p["entry"] * (1 - HARD_STOP)
                acc.sell(coin, px * (1 - SLIPPAGE), t, "nachgezogener Stop" if trailing else "Stop")


def scan(acc: Account) -> int:
    """Einmal am Tag nach dem Tagesschluss (00:00 UTC): Universum durchsuchen und kaufen."""
    today = acc.now - acc.now % DAY
    if acc.s["last_scan"] >= today or acc.now - today < 120_000:   # Tageskerze erst fertig werden lassen
        return 0
    errors = 0
    meta = {x["baseCurrency"]: float(x["baseIncrement"]) for x in api("/api/v2/symbols")
            if x["quoteCurrency"] == "USDT" and x["enableTrading"]}
    tick = {t["symbol"][:-5]: t for t in api("/api/v1/market/allTickers")["ticker"]
            if t["symbol"].endswith("-USDT")}
    cands = [c for c, t in tick.items() if c in meta and not is_excluded(c)
             and float(t.get("volValue") or 0) >= PREFILTER_VOLUME]
    cands = sorted(set(cands) | set(acc.pos))
    signals, liquid, checked = [], 0, 0
    for coin in cands:
        try:
            days = [b for b in klines(coin + "-USDT", 1440, today - (SMA_N + 12) * DAY, today)
                    if b[0] < today]
            checked += 1
            if coin in acc.pos:                         # hoechsten Tagesschluss nachfuehren
                p = acc.pos[coin]
                since = [b[4] for b in days if b[0] + DAY > p["ts"]]
                p["peak"] = max([p["peak"]] + since)
            if not days or days[-1][0] != today - DAY:
                continue
            s = signal(days)
            if s is None:
                continue
            liquid += s["liquid"]
            if s["buy"]:
                signals.append({"coin": coin, **{k: s[k] for k in ("close", "high", "vol_ratio", "volume")}})
        except Exception as e:
            errors += 1
            log_error(f"{coin}: {e}", acc.now)
    signals.sort(key=lambda x: -x["volume"])
    for sg in signals:
        coin = sg["coin"]
        last = acc.s["last_exit"].get(coin, 0)
        if coin in acc.pos:
            sg["status"] = "schon im Bestand"
        elif acc.now - last < COOLDOWN_DAYS * DAY:
            sg["status"] = "Sperrfrist nach Verkauf"
        elif len(acc.pos) >= MAX_POSITIONS:
            sg["status"] = "kein Platz (5 Positionen)"
        else:
            try:
                ask = float(api(f"/api/v1/market/orderbook/level1?symbol={coin}-USDT")["bestAsk"])
                px = ask * (1 + SLIPPAGE)
                qty = buy_qty(acc.s["cash"], px, meta[coin])
                if qty:
                    acc.buy(coin, qty, px, f"über 20-Tage-Hoch {sg['high']:g} bei "
                                           f"{sg['vol_ratio']:.1f}-fachem Umsatz")
                    sg["status"] = "gekauft"
                else:
                    sg["status"] = "kein freies Guthaben"
            except Exception as e:
                errors += 1
                sg["status"] = "Fehler beim Kauf"
                log_error(f"Kauf {coin}: {e}", acc.now)
    acc.s["scan"] = {"ts": iso(acc.now), "day": iso(today - DAY)[:10], "checked": checked,
                     "liquid": liquid, "signals": signals[:15]}
    acc.s["last_scan"] = today
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
        stop = stop_level(p["entry"], p["peak"])
        positions.append({
            "coin": coin, "qty": p["qty"], "entry": p["entry"], "mark": mark, "stop": stop,
            "target": p["entry"] * (1 + TAKE_PROFIT), "since": iso(p["ts"]),
            "value": round(p["qty"] * mark, 2),
            "pnl": round(p["qty"] * (mark - p["entry"]) - p["fee"], 2),
            "ret": round(mark / p["entry"] - 1, 4), "reason": p["reason"]})
    errs = []
    if os.path.exists(ERRORS_LOG):
        with open(ERRORS_LOG, encoding="utf-8") as f:
            errs = f.read().splitlines()[-5:]
    save_json(DASH_JSON, {
        "updated": iso(acc.now), "started": acc.s["started"],
        "config": {"start": START_EQUITY, "size": POSITION_SIZE, "max_positions": MAX_POSITIONS,
                   "high_n": HIGH_N, "vol_mult": VOL_MULT, "sma_n": SMA_N,
                   "min_volume": MIN_MEDIAN_VOLUME, "hard_stop": HARD_STOP, "trail_stop": TRAIL_STOP,
                   "take_profit": TAKE_PROFIT, "fee": TAKER_FEE, "backtest": BACKTEST},
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
        "scan": acc.s["scan"],
        "trades": sorted(t, key=lambda x: x["exit_ts"], reverse=True)[:80],
        "errors": errs, "run_errors": run_errors,
    })


def main() -> int:
    now_ms = int(time.time() * 1000)
    acc = Account(load_json(STATE_JSON, {}), load_json(TRADES_JSON, []), now_ms)
    errors = 0
    try:
        check_exits(acc)
        errors += scan(acc)
    except Exception as e:
        errors += 1
        log_error(f"Lauf: {e}", now_ms)
    save_json(STATE_JSON, acc.s)
    save_json(TRADES_JSON, acc.trades)
    build_dashboard(acc, errors)
    print(f"{iso(now_ms)} Spot-Konto {acc.equity():.2f} USDT · {len(acc.pos)} Positionen · "
          f"investiert {acc.invested():.2f} · frei {acc.s['cash']:.2f} · {len(acc.trades)} Trades")
    return 0


if __name__ == "__main__":
    sys.exit(main())
