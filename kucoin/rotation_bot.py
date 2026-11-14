#!/usr/bin/env python3
"""Strategie 2 des Paper-Bots: Bitcoin-Kern mit Trendfilter plus Rotation in die staerksten Coins.

Eigenes Scheinkonto, nur Spot, kein Hebel, keine Orders. Entwickelt auf Binance-Daten 2019-2022,
geprueft auf 2023-08/2024 und einmalig auf KuCoin 08/2024-11/2026 (kucoin/research/rot*.py).

  * Marktlage: Wie viele der drei Bitcoin-Trends (50, 100, 200 Tage) zeigen nach oben? -> 0, 1/3, 2/3, 1
  * Kern (halbes Konto): Bitcoin, investiert im Anteil der Marktlage
  * Rotation (halbes Konto): je nach Marktlage 0, 2, 3 oder 5 der staerksten unter den 20
    meistgehandelten Coins; woechentlich neu gewaehlt, taeglich raus, wenn der Trend bricht
  * Groesse der Rotations-Positionen nach Schwankung: ruhige Coins groesser, wilde kleiner

Gehandelt wird einmal am Tag kurz nach Mitternacht UTC. Nur Standardbibliothek.
"""
from __future__ import annotations

import csv
import math
import os
import statistics
import sys
import time

import spot_bot as sb          # Netz- und Dateifunktionen des Spot-Teils mitbenutzen

# ----------------------------------------------------------------------------
# Einstellungen (eingefroren -- Aenderungen nur ueber einen neuen Dreistufen-Test)
# ----------------------------------------------------------------------------
START_EQUITY = 1000.0
CORE_SHARE = 0.5              # Anteil des Startkapitals im Bitcoin-Kern
REGIME_MAS = (50, 100, 200)   # Bitcoin-Trends in Tagen
UNIVERSE = 20                 # die 20 meistgehandelten Coins (Median-Umsatz 30 Tage)
MOM_LOOKBACKS = (30, 90)      # Staerke = Mittel aus 30- und 90-Tage-Rendite
COIN_MA = 50                  # Coin muss ueber seinem 50-Tage-Schnitt liegen
MIN_HISTORY = 120             # lueckenlose Tage, bevor ein Coin in Frage kommt
TOP_N = 5                     # hoechstens 5 Rotations-Positionen
REBALANCE_DAYS = 7
VOL_TARGET = 0.8              # Positionsgroesse = 20 % x min(1, 80 % / Jahresschwankung des Coins)
MIN_WEIGHT = 0.10             # aber mindestens 10 % des Rotations-Kontos
PREFILTER = 110               # so viele Coins (nach 24h-Umsatz) werden taeglich geprueft

TAKER_FEE = 0.001
SLIPPAGE = 0.001              # zusaetzlich zu Brief-/Geldkurs

# Ergebnis der drei Stufen (kucoin/research/rot_core.py), Rendite pro Jahr / groesster Rueckgang / Sharpe
TESTS = [
    {"stage": "Entwicklung", "data": "Binance 2019–2022", "cagr": 0.729, "dd": -0.372, "sharpe": 1.48,
     "btc_cagr": 0.454, "btc_dd": -0.766},
    {"stage": "Prüfung", "data": "Binance 2023–08/2024", "cagr": 0.584, "dd": -0.294, "sharpe": 1.24,
     "btc_cagr": 1.201, "btc_dd": -0.236},
    {"stage": "Schlusskontrolle", "data": "KuCoin 08/2024–11/2026", "cagr": 0.101, "dd": -0.28, "sharpe": 0.47,
     "btc_cagr": 0.096, "btc_dd": -0.53},
]

DATA = os.path.join(sb.ROOT, "data", "rotation")
STATE_JSON = os.path.join(DATA, "state.json")
TRADES_JSON = os.path.join(DATA, "trades.json")
EQUITY_CSV = os.path.join(DATA, "equity.csv")
ERRORS_LOG = os.path.join(DATA, "errors.log")
DASH_JSON = os.path.join(sb.ROOT, "docs", "rotation.json")
DAY = 86_400_000
FIAT = {"EUR", "GBP", "AUD", "BRL", "TRY", "RUB", "UAH", "NGN", "IDRT", "BIDR", "BVND", "DAI", "USDC",
        "BUSD", "TUSD", "PAX", "USDP", "FDUSD", "UST", "USTC", "SUSD", "USDS", "USDSB", "AEUR", "EURI",
        "PAXG", "XAUT", "WBTC", "WBETH", "BETH", "XUSD", "USD1", "BFUSD", "USDE", "USDD", "PYUSD",
        "USDJ", "GUSD", "EURT"}


# ----------------------------------------------------------------------------
# Reine Rechenfunktionen (in tests/ geprueft und gegen den Ruecktest abgeglichen)
# ----------------------------------------------------------------------------
def excluded(coin: str) -> bool:
    return coin in FIAT or coin.endswith(("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "2L", "2S"))


def contiguous(days: list, today: int, n: int) -> list:
    """Die letzten n Tageskerzen bis gestern, nur wenn lueckenlos vorhanden; sonst []."""
    tail = [b for b in days if b[0] < today][-n:]
    if len(tail) < n or tail[-1][0] != today - DAY or tail[0][0] != today - n * DAY:
        return []
    return tail


def regime(btc_closes: list) -> float:
    """Anteil der Bitcoin-Trends, die nach oben zeigen (Schluss ueber dem gleitenden Schnitt)."""
    c = btc_closes[-1]
    return sum(c > sum(btc_closes[-m:]) / m for m in REGIME_MAS) / len(REGIME_MAS)


def coin_stats(days: list) -> dict:
    """Kennzahlen eines Coins aus MIN_HISTORY lueckenlosen Tageskerzen [t, o, h, l, c, v, umsatz]."""
    closes = [b[4] for b in days]
    c = closes[-1]
    mom = sum(c / closes[-1 - L] - 1 for L in MOM_LOOKBACKS) / len(MOM_LOOKBACKS)
    trend = c > sum(closes[-COIN_MA:]) / COIN_MA
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - 30, len(closes))]
    vol = statistics.stdev(rets) * math.sqrt(365)
    return {"close": c, "mom": mom, "trend": trend, "vol": vol,
            "volume": statistics.median(b[6] for b in days[-30:]), "hold_ok": mom > 0 and trend}


def rank(stats: dict) -> dict:
    """Coin -> Rang (1 = staerkster) unter den UNIVERSE meistgehandelten mit Aufwaertstrend."""
    by_volume = sorted(stats, key=lambda c: -stats[c]["volume"])[:UNIVERSE]
    ok = [c for c in by_volume if stats[c]["mom"] > 0 and stats[c]["trend"]]
    return {c: i + 1 for i, c in enumerate(sorted(ok, key=lambda c: -stats[c]["mom"]))}


def slots_for(frac: float) -> int:
    return 0 if frac <= 0 else max(1, int(round(frac * TOP_N)))


def vol_scale(vol: float) -> float:
    return min(1.0, VOL_TARGET / vol) if vol > 0 else 0.0


def plan(frac: float, ranks: dict, stats: dict, held: dict, rebalance: bool) -> tuple[list, list]:
    """(Verkaeufe [(Coin, Grund)], Kaeufe [Coin]) fuer das Rotations-Konto -- wie im Ruecktest."""
    sells, keep = [], []
    for coin in held:
        if frac <= 0:
            sells.append((coin, "Marktlage"))
        elif coin not in stats or not stats[coin]["hold_ok"]:
            sells.append((coin, "Trend gebrochen"))
        else:
            keep.append(coin)
    n = slots_for(frac)
    if len(keep) > n:                                 # Marktlage schlechter: schwaechste abgeben
        keep.sort(key=lambda c: ranks.get(c, 10 ** 9))
        sells += [(c, "Marktlage") for c in keep[n:]]
        keep = keep[:n]
    buys = []
    if rebalance and frac > 0:
        for coin in sorted(ranks, key=ranks.get):
            if len(keep) + len(buys) >= n or ranks[coin] > TOP_N:
                break
            if coin not in keep:
                buys.append(coin)
    return sells, buys


# ----------------------------------------------------------------------------
# Scheinkonto
# ----------------------------------------------------------------------------
def log_error(msg: str, now_ms: int) -> None:
    os.makedirs(DATA, exist_ok=True)
    lines = []
    if os.path.exists(ERRORS_LOG):
        with open(ERRORS_LOG, encoding="utf-8") as f:
            lines = f.read().splitlines()
    lines.append(f"{sb.iso(now_ms)} {msg}")
    with open(ERRORS_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines[-200:]) + "\n")
    print("FEHLER:", msg, file=sys.stderr)


class Account:
    def __init__(self, state: dict, trades: list, now_ms: int):
        self.s, self.trades, self.now = state, trades, now_ms
        s = self.s
        s.setdefault("core_cash", START_EQUITY * CORE_SHARE)
        s.setdefault("btc", 0.0)
        s.setdefault("btc_cost", 0.0)                 # Einstandswert des Bitcoin-Bestands
        s.setdefault("rot_cash", START_EQUITY * (1 - CORE_SHARE))
        s.setdefault("positions", {})
        s.setdefault("last_scan", 0)
        s.setdefault("last_reb", 0)
        s.setdefault("frac", None)
        s.setdefault("fees", 0.0)
        s.setdefault("view", {})
        s.setdefault("started", sb.iso(now_ms))
        self.marks: dict[str, float] = {}

    def px(self, coin: str, fallback: float) -> float:
        return self.marks.get(coin, fallback)

    def core_equity(self) -> float:
        return self.s["core_cash"] + self.s["btc"] * self.px("BTC", self.s.get("btc_mark", 0.0))

    def rot_equity(self) -> float:
        return self.s["rot_cash"] + sum(p["qty"] * self.px(c, p["entry"]) for c, p in self.s["positions"].items())

    def equity(self) -> float:
        return self.core_equity() + self.rot_equity()

    def trade_core(self, value: float, bid: float, ask: float, why: str) -> None:
        """value > 0 kauft Bitcoin fuer diesen Betrag, value < 0 verkauft."""
        s = self.s
        if value > 0:
            px = ask * (1 + SLIPPAGE)
            value = min(value, s["core_cash"] / (1 + TAKER_FEE))
            if value < 5:
                return
            s["btc"] += value / px
            s["btc_cost"] += value
            s["core_cash"] -= value * (1 + TAKER_FEE)
            s["fees"] += value * TAKER_FEE
            self.trades.append({"ts": sb.iso(self.now), "part": "Kern", "coin": "BTC", "side": "Kauf",
                                "price": px, "value": round(value, 2), "pnl": None, "reason": why})
        elif value < 0 and s["btc"] > 0:
            px = bid * (1 - SLIPPAGE)
            qty = min(s["btc"], -value / px)
            if s["btc"] - qty < 1e-8 or (s["btc"] - qty) * px < 5:
                qty = s["btc"]
            cost = s["btc_cost"] * qty / s["btc"]
            proceeds = qty * px
            s["btc"] -= qty
            s["btc_cost"] -= cost
            s["core_cash"] += proceeds * (1 - TAKER_FEE)
            s["fees"] += proceeds * TAKER_FEE
            self.trades.append({"ts": sb.iso(self.now), "part": "Kern", "coin": "BTC", "side": "Verkauf",
                                "price": px, "value": round(proceeds, 2),
                                "pnl": round(proceeds * (1 - TAKER_FEE) - cost * (1 + TAKER_FEE), 2), "reason": why})
        print(f"  KERN {why}: {value:+.2f} USDT in Bitcoin")

    def buy(self, coin: str, value: float, ask: float, vs: float, why: str) -> bool:
        s = self.s
        value = min(value, s["rot_cash"] / (1 + TAKER_FEE))
        if value < 5:
            return False
        px = ask * (1 + SLIPPAGE)
        s["positions"][coin] = {"qty": value / px, "entry": px, "vs": vs, "ts": self.now, "value": value}
        s["rot_cash"] -= value * (1 + TAKER_FEE)
        s["fees"] += value * TAKER_FEE
        self.marks[coin] = px
        self.trades.append({"ts": sb.iso(self.now), "part": "Rotation", "coin": coin, "side": "Kauf",
                            "price": px, "value": round(value, 2), "pnl": None, "reason": why})
        print(f"  ROTATION Kauf {coin} für {value:.2f} USDT ({why})")
        return True

    def sell(self, coin: str, bid: float, why: str) -> None:
        s = self.s
        p = s["positions"].pop(coin)
        px = bid * (1 - SLIPPAGE)
        proceeds = p["qty"] * px
        s["rot_cash"] += proceeds * (1 - TAKER_FEE)
        s["fees"] += proceeds * TAKER_FEE
        pnl = proceeds * (1 - TAKER_FEE) - p["value"] * (1 + TAKER_FEE)
        self.trades.append({"ts": sb.iso(self.now), "part": "Rotation", "coin": coin, "side": "Verkauf",
                            "price": px, "value": round(proceeds, 2), "pnl": round(pnl, 2),
                            "ret": round(pnl / p["value"], 4), "held_days": round((self.now - p["ts"]) / DAY, 1),
                            "reason": why})
        print(f"  ROTATION Verkauf {coin} ({why}) Ergebnis {pnl:+.2f} USDT")


def book(coin: str) -> tuple[float, float]:
    t = sb.api(f"/api/v1/market/orderbook/level1?symbol={coin}-USDT")
    return float(t["bestBid"]), float(t["bestAsk"])


def scan(acc: Account, tick: dict) -> int:
    """Einmal am Tag nach dem Tagesschluss: Marktlage, Rangliste, Verkaeufe, Kaeufe."""
    today = acc.now - acc.now % DAY
    s = acc.s
    if s["last_scan"] >= today or acc.now - today < 120_000:
        return 0
    errors = 0
    btc_days = contiguous(sb.klines("BTC-USDT", 1440, today - (max(REGIME_MAS) + 5) * DAY, today),
                          today, max(REGIME_MAS))
    if not btc_days:
        raise RuntimeError("Bitcoin-Tageskerzen unvollständig")
    closes = [b[4] for b in btc_days]
    frac = regime(closes)
    trends = {str(m): closes[-1] > sum(closes[-m:]) / m for m in REGIME_MAS}
    averages = {str(m): sum(closes[-m:]) / m for m in REGIME_MAS}

    cands = sorted((c for c in tick if not excluded(c)), key=lambda c: -tick[c]["vol"])[:PREFILTER]
    stats = {}
    for coin in sorted(set(cands) | set(s["positions"])):
        try:
            days = contiguous(sb.klines(f"{coin}-USDT", 1440, today - (MIN_HISTORY + 5) * DAY, today),
                              today, MIN_HISTORY)
            if days:
                stats[coin] = coin_stats(days)
        except Exception as e:
            errors += 1
            log_error(f"{coin}: {e}", acc.now)
    ranks = rank(stats)
    rebalance = frac > 0 and today - s["last_reb"] >= REBALANCE_DAYS * DAY
    sells, buys = plan(frac, ranks, stats, s["positions"], rebalance)

    for coin, why in sells:
        try:
            acc.sell(coin, book(coin)[0], why)
        except Exception as e:
            errors += 1
            log_error(f"Verkauf {coin}: {e}", acc.now)
    if frac <= 0:
        s["last_reb"] = 0                               # nach einer Pause sofort neu waehlen
    elif rebalance:
        s["last_reb"] = today
        eq = acc.rot_equity()
        for coin in buys:
            try:
                vs = vol_scale(stats[coin]["vol"])
                acc.buy(coin, eq * max(MIN_WEIGHT, vs / TOP_N), book(coin)[1], vs,
                        f"Rang {ranks[coin]} von {UNIVERSE}, Stärke {stats[coin]['mom'] * 100:+.0f} %")
            except Exception as e:
                errors += 1
                log_error(f"Kauf {coin}: {e}", acc.now)

    if s["frac"] is None or frac != s["frac"]:          # Kern nur anpassen, wenn sich die Marktlage aendert
        bid, ask = book("BTC")
        acc.marks["BTC"] = (bid + ask) / 2
        target = acc.core_equity() * frac
        diff = target - s["btc"] * acc.marks["BTC"]
        acc.trade_core(diff, bid, ask, f"Marktlage {round(frac * 3)} von 3")
    s["frac"] = frac
    s["last_scan"] = today
    volume_rank = {c: i + 1 for i, c in enumerate(sorted(stats, key=lambda c: -stats[c]["volume"]))}
    s["view"] = {
        "day": sb.iso(today - DAY)[:10], "frac": frac, "trends": trends, "averages": averages,
        "btc_close": closes[-1], "checked": len(stats), "slots": slots_for(frac),
        "next_rebalance": sb.iso(s["last_reb"] + REBALANCE_DAYS * DAY)[:10] if s["last_reb"] else None,
        "ranking": [{"coin": c, "rank": ranks.get(c), "mom": round(stats[c]["mom"], 4),
                     "trend": stats[c]["trend"], "vol": round(stats[c]["vol"], 3),
                     "volume": round(stats[c]["volume"]), "held": c in s["positions"]}
                    for c in sorted(stats, key=lambda c: volume_rank[c])[:UNIVERSE]],
    }
    return errors


def build_dashboard(acc: Account, run_errors: int) -> None:
    s, eq = acc.s, acc.equity()
    os.makedirs(DATA, exist_ok=True)
    new = not os.path.exists(EQUITY_CSV)
    with open(EQUITY_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "equity", "core", "rotation", "btc_price"])
        w.writerow([sb.iso(acc.now), round(eq, 2), round(acc.core_equity(), 2), round(acc.rot_equity(), 2),
                    round(acc.px("BTC", 0.0), 2)])
    with open(EQUITY_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    peak, dd = START_EQUITY, 0.0
    for r in rows:
        peak = max(peak, float(r["equity"]))
        dd = min(dd, float(r["equity"]) / peak - 1)
    step = max(1, len(rows) // 400)
    pick = rows[::step] + ([rows[-1]] if rows and (len(rows) - 1) % step else [])
    btc0 = next((float(r["btc_price"]) for r in rows if float(r["btc_price"]) > 0), 0.0)
    curve = [{"ts": r["ts"], "eq": float(r["equity"]),
              "btc": round(START_EQUITY * float(r["btc_price"]) / btc0, 2) if btc0 and float(r["btc_price"]) > 0 else None}
             for r in pick]
    closed = [t for t in acc.trades if t.get("pnl") is not None]
    wins = [t["pnl"] for t in closed if t["pnl"] > 0]
    losses = [-t["pnl"] for t in closed if t["pnl"] <= 0]
    btc_px = acc.px("BTC", 0.0)
    positions = [{"coin": c, "since": sb.iso(p["ts"]), "entry": p["entry"], "mark": acc.px(c, p["entry"]),
                  "value": round(p["qty"] * acc.px(c, p["entry"]), 2),
                  "pnl": round(p["qty"] * acc.px(c, p["entry"]) - p["value"] * (1 + TAKER_FEE), 2),
                  "ret": round(acc.px(c, p["entry"]) / p["entry"] - 1, 4), "vs": round(p["vs"], 2)}
                 for c, p in s["positions"].items()]
    errs = []
    if os.path.exists(ERRORS_LOG):
        with open(ERRORS_LOG, encoding="utf-8") as f:
            errs = f.read().splitlines()[-5:]
    invested = s["btc"] * btc_px + sum(p["value"] for p in positions)
    sb.save_json(DASH_JSON, {
        "updated": sb.iso(acc.now), "started": s["started"],
        "config": {"start": START_EQUITY, "core_share": CORE_SHARE, "mas": REGIME_MAS, "universe": UNIVERSE,
                   "top_n": TOP_N, "rebalance_days": REBALANCE_DAYS, "vol_target": VOL_TARGET,
                   "min_weight": MIN_WEIGHT, "coin_ma": COIN_MA, "lookbacks": MOM_LOOKBACKS,
                   "fee": TAKER_FEE, "tests": TESTS},
        "summary": {"equity": round(eq, 2), "pnl": round(eq - START_EQUITY, 2),
                    "ret": round(eq / START_EQUITY - 1, 4), "max_dd": round(dd, 4),
                    "core": round(acc.core_equity(), 2), "rotation": round(acc.rot_equity(), 2),
                    "btc_value": round(s["btc"] * btc_px, 2), "btc_qty": s["btc"],
                    "btc_pnl": round(s["btc"] * btc_px - s["btc_cost"], 2) if s["btc"] else 0.0,
                    "invested": round(invested, 2), "invested_share": round(invested / eq, 4) if eq > 0 else 0,
                    "open": len(positions), "trades": len(closed), "wins": len(wins),
                    "win_rate": round(len(wins) / len(closed), 4) if closed else None,
                    "profit_factor": round(sum(wins) / sum(losses), 2) if losses and sum(losses) > 0 else None,
                    "fees": round(s["fees"], 2),
                    "btc_hold": curve[-1]["btc"] if curve else None},
        "view": s["view"], "positions": positions, "curve": curve,
        "trades": list(reversed(acc.trades))[:80], "errors": errs, "run_errors": run_errors,
    })


def main() -> int:
    now_ms = int(time.time() * 1000)
    acc = Account(sb.load_json(STATE_JSON, {}), sb.load_json(TRADES_JSON, []), now_ms)
    errors = 0
    try:
        tick = {}
        for t in sb.api("/api/v1/market/allTickers")["ticker"]:
            if t["symbol"].endswith("-USDT") and t.get("last"):
                tick[t["symbol"][:-5]] = {"last": float(t["last"]), "vol": float(t.get("volValue") or 0)}
        for coin in list(acc.s["positions"]) + ["BTC"]:
            if coin in tick:
                acc.marks[coin] = tick[coin]["last"]
        acc.s["btc_mark"] = acc.marks.get("BTC", acc.s.get("btc_mark", 0.0))
        errors += scan(acc, tick)
    except Exception as e:
        errors += 1
        log_error(f"Lauf: {e}", now_ms)
    sb.save_json(STATE_JSON, acc.s)
    sb.save_json(TRADES_JSON, acc.trades)
    build_dashboard(acc, errors)
    print(f"{sb.iso(now_ms)} Strategie 2: {acc.equity():.2f} USDT · Kern {acc.core_equity():.2f} · "
          f"Rotation {acc.rot_equity():.2f} · {len(acc.s['positions'])} Positionen · Marktlage {acc.s['frac']}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
