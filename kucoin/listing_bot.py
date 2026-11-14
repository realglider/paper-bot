#!/usr/bin/env python3
"""Strategie 3 des Paper-Bots: neu gelistete Krypto-Futures leerverkaufen, abgesichert mit Bitcoin.

Eigenes Scheinkonto, keine Orders. Hintergrund: Neu gelistete Coins verlieren in den ersten Wochen
meist deutlich an Wert, einzelne steigen aber stark. Deshalb kleine Positionen, fester Stop und ein
Bitcoin-Long in gleicher Hoehe, damit nicht die allgemeine Marktrichtung das Ergebnis bestimmt.

  * Einstieg: am vierten Tag nach dem Listing (nach vier Tageskerzen), Short per Future
  * Ausstieg: nach 30 Tagen oder bei +50 % gegen die Position
  * Groesse: 5 % des Kontos je Coin, hoechstens 10 gleichzeitig
  * Absicherung: ein Bitcoin-Long in Hoehe aller offenen Shorts
  * Tokenisierte Aktien und ETFs sind ausgeschlossen (dort gibt es den Effekt nicht)

Entwickelt auf Binance-Listings 2019-2022, geprueft auf 2023-08/2024 und auf KuCoin-Futures
08/2024-11/2026 (kucoin/research/newlist.py). Nur Standardbibliothek.
"""
from __future__ import annotations

import csv
import math
import os
import sys
import time
import urllib.parse

import futures_bot as fb       # Netz- und Kerzenfunktionen des Futures-Teils mitbenutzen

# ----------------------------------------------------------------------------
# Einstellungen (eingefroren)
# ----------------------------------------------------------------------------
START_EQUITY = 1000.0
ENTRY_DAY = 3                 # Einstieg nach Tageskerze Nr. 3 (die Listing-Kerze ist Nr. 0)
HOLD_DAYS = 30
STOP = 0.50                   # Ausstieg, wenn der Kurs 50 % ueber dem Einstieg liegt
WEIGHT = 0.05                 # Positionswert je Short als Anteil am Konto
MAX_POSITIONS = 10
MAX_LATE_DAYS = 1             # einen verpassten Tag noch nachholen, danach auslassen
HEDGE = "XBT"

TAKER_FEE = 0.0006
SLIPPAGE = 0.0014             # junge Futures haben duenne Orderbuecher
HEDGE_SLIPPAGE = 0.0002

# Ergebnis je Trade (Short + Bitcoin-Long) nach Kosten
TESTS = [
    {"stage": "Entwicklung", "data": "Binance-Listings 2019–2022", "n": 333, "avg": 0.057, "win": 0.64},
    {"stage": "Prüfung", "data": "Binance-Listings 2023–08/2024", "n": 85, "avg": 0.092, "win": 0.71},
    {"stage": "Schlusskontrolle", "data": "KuCoin-Futures 08/2024–11/2026, echtes Funding", "n": 340,
     "avg": 0.034, "win": 0.59},
]
ACCOUNT_TEST = {"cagr": 0.104, "dd": -0.318, "months_up": 14, "months": 27}

DATA = os.path.join(fb.ROOT, "data", "listing")
STATE_JSON = os.path.join(DATA, "state.json")
TRADES_JSON = os.path.join(DATA, "trades.json")
EQUITY_CSV = os.path.join(DATA, "equity.csv")
ERRORS_LOG = os.path.join(DATA, "errors.log")
DASH_JSON = os.path.join(fb.ROOT, "docs", "listing.json")
DAY = 86_400_000


# ----------------------------------------------------------------------------
# Reine Rechenfunktionen (in tests/ geprueft)
# ----------------------------------------------------------------------------
def is_stock(contract: dict) -> bool:
    """Tokenisierte Aktien/ETFs erkennt man an einem Boersendaten-Anbieter unter den Kursquellen."""
    return any("finnhub" in str(s).lower() for s in (contract.get("sourceExchanges") or []))


def closed_days(first_open_ms: int, today_ms: int) -> int:
    """Anzahl fertiger Tageskerzen seit dem Listing (die Listing-Kerze zaehlt mit)."""
    first_day = first_open_ms - first_open_ms % DAY
    return max(0, (today_ms - first_day) // DAY)


def entry_due(first_open_ms: int, today_ms: int) -> str:
    """'wait' (noch zu frueh), 'enter' (heute oder mit erlaubter Verspaetung) oder 'missed'."""
    n = closed_days(first_open_ms, today_ms)
    if n < ENTRY_DAY + 1:
        return "wait"
    return "enter" if n <= ENTRY_DAY + 1 + MAX_LATE_DAYS else "missed"


def entry_date(first_open_ms: int) -> int:
    return first_open_ms - first_open_ms % DAY + (ENTRY_DAY + 1) * DAY


def short_lots(equity: float, price: float, multiplier: float, lot: int = 1) -> int:
    if price <= 0 or multiplier <= 0:
        return 0
    return int(math.floor(equity * WEIGHT / (price * multiplier) / lot + 1e-9) * lot)


def hedge_lots(total_short_notional: float, btc_price: float, multiplier: float) -> int:
    """Bitcoin-Kontrakte, die dem Wert aller offenen Shorts am naechsten kommen."""
    if btc_price <= 0 or multiplier <= 0:
        return 0
    return int(round(total_short_notional / (btc_price * multiplier)))


# ----------------------------------------------------------------------------
# Scheinkonto
# ----------------------------------------------------------------------------
def log_error(msg: str, now_ms: int) -> None:
    os.makedirs(DATA, exist_ok=True)
    lines = []
    if os.path.exists(ERRORS_LOG):
        with open(ERRORS_LOG, encoding="utf-8") as f:
            lines = f.read().splitlines()
    lines.append(f"{fb.iso(now_ms)} {msg}")
    with open(ERRORS_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines[-200:]) + "\n")
    print("FEHLER:", msg, file=sys.stderr)


class Account:
    def __init__(self, state: dict, trades: list, now_ms: int):
        self.s, self.trades, self.now = state, trades, now_ms
        s = self.s
        s.setdefault("cash", START_EQUITY)             # Startkapital + alles Realisierte
        s.setdefault("positions", {})
        s.setdefault("hedge", {"lots": 0, "qty": 0.0, "entry": 0.0, "funded": now_ms, "realized": 0.0})
        s.setdefault("seen", {})                       # Coin -> wie mit dem Listing verfahren wurde
        s.setdefault("last_scan", 0)
        s.setdefault("fees", 0.0)
        s.setdefault("funding", 0.0)
        s.setdefault("started", fb.iso(now_ms))
        s.setdefault("upcoming", [])
        self.marks: dict[str, float] = {}

    def short_notional(self) -> float:
        return sum(p["notional"] for p in self.s["positions"].values())

    def equity(self) -> float:
        s = self.s
        v = s["cash"]
        for c, p in s["positions"].items():
            v -= p["qty"] * (self.marks.get(c, p["entry"]) - p["entry"])
        h = s["hedge"]
        return v + h["qty"] * (self.marks.get(HEDGE, h["entry"]) - h["entry"])

    def open_short(self, coin, lots, mult, price, listed_ms, exit_at):
        s = self.s
        qty = lots * mult
        fee = qty * price * TAKER_FEE
        s["cash"] -= fee
        s["fees"] += fee
        s["positions"][coin] = {"lots": lots, "qty": qty, "entry": price, "stop": price * (1 + STOP),
                                "notional": qty * price, "ts": self.now, "checked": self.now,
                                "funded": self.now, "exit_at": exit_at, "listed": listed_ms,
                                "fee": fee, "funding": 0.0}
        self.marks[coin] = price
        print(f"  SHORT {coin} {qty:g} @ {price:g} = {qty * price:.2f} USDT, Stop {price * (1 + STOP):g}")

    def close_short(self, coin, price, ts, why):
        s = self.s
        p = s["positions"].pop(coin)
        gross = -p["qty"] * (price - p["entry"])
        fee = p["qty"] * price * TAKER_FEE
        s["cash"] += gross - fee
        s["fees"] += fee
        net = gross - fee - p["fee"] - p["funding"]
        self.trades.append({"coin": coin, "entry": p["entry"], "exit": price, "entry_ts": fb.iso(p["ts"]),
                            "exit_ts": fb.iso(ts), "notional": round(p["notional"], 2),
                            "funding": round(p["funding"], 4), "pnl": round(net, 4),
                            "ret": round(net / p["notional"], 4),
                            "days": round((ts - p["ts"]) / DAY, 1), "exit_reason": why})
        print(f"  SHORT ZU {coin} @ {price:g} ({why}) Ergebnis {net:+.2f} USDT")

    def set_hedge(self, lots: int, mult: float, bid: float, ask: float) -> None:
        """Bitcoin-Long auf die Zielzahl an Kontrakten bringen."""
        s, h = self.s, self.s["hedge"]
        diff = lots - h["lots"]
        if diff == 0:
            return
        if diff > 0:
            px = ask * (1 + HEDGE_SLIPPAGE)
            q = diff * mult
            h["entry"] = (h["entry"] * h["qty"] + px * q) / (h["qty"] + q)
            h["qty"] += q
        else:
            px = bid * (1 - HEDGE_SLIPPAGE)
            q = -diff * mult
            gain = q * (px - h["entry"])
            s["cash"] += gain
            h["realized"] += gain
            h["qty"] = max(0.0, h["qty"] - q)
        fee = abs(diff) * mult * px * TAKER_FEE
        s["cash"] -= fee
        s["fees"] += fee
        h["realized"] -= fee
        h["lots"] = lots
        if lots == 0:
            h["qty"], h["entry"] = 0.0, 0.0
        print(f"  ABSICHERUNG Bitcoin-Long auf {lots} Kontrakte ({diff:+d})")


def funding_between(sym: str, start_ms: int, end_ms: int) -> list:
    q = urllib.parse.urlencode({"symbol": sym, "from": start_ms + 1, "to": end_ms})
    return fb.api("/api/v1/contract/funding-rates?" + q) or []


def manage(acc: Account, meta: dict) -> int:
    """Stops, Zeitablauf und Funding der offenen Shorts; Funding der Absicherung."""
    errors = 0
    s = acc.s
    for coin in list(s["positions"]):
        p, sym = s["positions"][coin], coin + "USDTM"
        try:
            cs = [b for b in fb.candles_since(sym, p["checked"], acc.now) if b[0] >= p["checked"]]
            if cs:
                p["checked"] = cs[-1][0]
                acc.marks[coin] = cs[-1][4]
            hit = fb.stop_hit(-1, p["stop"], [b for b in cs if b[0] >= p["ts"]])
            if acc.now - p["funded"] >= 60_000:
                for r in funding_between(sym, p["funded"], acc.now):
                    if r["timepoint"] > p["ts"]:
                        cost = -p["qty"] * acc.marks.get(coin, p["entry"]) * float(r["fundingRate"])
                        s["cash"] -= cost                # Short erhaelt bei positiver Rate
                        s["funding"] += cost
                        p["funding"] += cost
                p["funded"] = acc.now
            if hit:
                acc.close_short(coin, hit[1] * (1 + SLIPPAGE), hit[0], "Stop +50 %")
            elif acc.now >= p["exit_at"] or coin not in meta:
                ask = float(fb.api(f"/api/v1/ticker?symbol={sym}")["bestAskPrice"]) if coin in meta \
                    else acc.marks.get(coin, p["entry"])
                acc.close_short(coin, ask * (1 + SLIPPAGE), acc.now,
                                "30 Tage um" if coin in meta else "Future eingestellt")
        except Exception as e:
            errors += 1
            log_error(f"{coin}: {e}", acc.now)
    h = s["hedge"]
    if h["qty"] > 0 and acc.now - h["funded"] >= 60_000:
        try:
            for r in funding_between(HEDGE + "USDTM", h["funded"], acc.now):
                cost = h["qty"] * acc.marks.get(HEDGE, h["entry"]) * float(r["fundingRate"])
                s["cash"] -= cost                        # Long zahlt bei positiver Rate
                s["funding"] += cost
                h["realized"] -= cost
            h["funded"] = acc.now
        except Exception as e:
            errors += 1
            log_error(f"Absicherung Funding: {e}", acc.now)
    elif h["qty"] == 0:
        h["funded"] = acc.now
    return errors


def scan(acc: Account, meta: dict) -> int:
    """Einmal am Tag: neue Krypto-Listings pruefen und faellige Shorts eroeffnen."""
    today = acc.now - acc.now % DAY
    s = acc.s
    if s["last_scan"] >= today or acc.now - today < 120_000:
        return 0
    errors = 0
    first_run = s["last_scan"] == 0
    upcoming = []
    for coin, m in sorted(meta.items(), key=lambda kv: kv[1]["first"]):
        if coin in s["seen"] or coin in s["positions"] or coin == HEDGE:
            continue
        if m["stock"]:
            s["seen"][coin] = "Aktien-Token"
            continue
        due = entry_due(m["first"], today)
        if due == "wait":
            upcoming.append({"coin": coin, "listed": fb.iso(m["first"]), "entry": fb.iso(entry_date(m["first"]))[:10],
                             "price": m["mark"]})
            continue
        if due == "missed" or first_run and closed_days(m["first"], today) > ENTRY_DAY + 1 + MAX_LATE_DAYS:
            s["seen"][coin] = "vor Start gelistet" if first_run else "verpasst"
            continue
        if len(s["positions"]) >= MAX_POSITIONS:
            s["seen"][coin] = "kein Platz"
            continue
        try:
            bid = float(fb.api(f"/api/v1/ticker?symbol={coin}USDTM")["bestBidPrice"])
            lots = short_lots(acc.equity(), bid, m["multiplier"], m["lot"])
            if lots < 1:
                s["seen"][coin] = "Kontrakt zu groß"
                continue
            acc.open_short(coin, lots, m["multiplier"], bid * (1 - SLIPPAGE), m["first"], today + HOLD_DAYS * DAY)
            s["seen"][coin] = "gehandelt"
        except Exception as e:
            errors += 1
            log_error(f"Einstieg {coin}: {e}", acc.now)
    s["upcoming"] = upcoming
    s["last_scan"] = today
    return errors


def rebalance_hedge(acc: Account, meta: dict) -> None:
    if HEDGE not in meta:
        return
    target = hedge_lots(acc.short_notional(), acc.marks.get(HEDGE, meta[HEDGE]["mark"]), meta[HEDGE]["multiplier"])
    if target != acc.s["hedge"]["lots"]:
        t = fb.api(f"/api/v1/ticker?symbol={HEDGE}USDTM")
        acc.set_hedge(target, meta[HEDGE]["multiplier"], float(t["bestBidPrice"]), float(t["bestAskPrice"]))


def build_dashboard(acc: Account, run_errors: int) -> None:
    s, eq = acc.s, acc.equity()
    os.makedirs(DATA, exist_ok=True)
    new = not os.path.exists(EQUITY_CSV)
    with open(EQUITY_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "equity", "shorts", "hedge"])
        w.writerow([fb.iso(acc.now), round(eq, 2), round(acc.short_notional(), 2),
                    round(s["hedge"]["qty"] * acc.marks.get(HEDGE, s["hedge"]["entry"]), 2)])
    with open(EQUITY_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    peak, dd = START_EQUITY, 0.0
    for r in rows:
        peak = max(peak, float(r["equity"]))
        dd = min(dd, float(r["equity"]) / peak - 1)
    step = max(1, len(rows) // 400)
    pick = rows[::step] + ([rows[-1]] if rows and (len(rows) - 1) % step else [])
    t = acc.trades
    wins = [x for x in t if x["pnl"] > 0]
    h = s["hedge"]
    hmark = acc.marks.get(HEDGE, h["entry"])
    positions = []
    for c, p in s["positions"].items():
        mark = acc.marks.get(c, p["entry"])
        positions.append({"coin": c, "listed": fb.iso(p["listed"])[:10], "since": fb.iso(p["ts"]),
                          "entry": p["entry"], "mark": mark, "stop": p["stop"],
                          "exit": fb.iso(p["exit_at"])[:10], "notional": round(p["notional"], 2),
                          "pnl": round(-p["qty"] * (mark - p["entry"]) - p["fee"] - p["funding"], 2),
                          "move": round(mark / p["entry"] - 1, 4), "funding": round(p["funding"], 2)})
    errs = []
    if os.path.exists(ERRORS_LOG):
        with open(ERRORS_LOG, encoding="utf-8") as f:
            errs = f.read().splitlines()[-5:]
    skipped = {}
    for v in s["seen"].values():
        skipped[v] = skipped.get(v, 0) + 1
    fb.save_json(DASH_JSON, {
        "updated": fb.iso(acc.now), "started": s["started"],
        "config": {"start": START_EQUITY, "entry_day": ENTRY_DAY + 1, "hold_days": HOLD_DAYS, "stop": STOP,
                   "weight": WEIGHT, "max_positions": MAX_POSITIONS, "tests": TESTS, "account_test": ACCOUNT_TEST},
        "summary": {"equity": round(eq, 2), "pnl": round(eq - START_EQUITY, 2),
                    "ret": round(eq / START_EQUITY - 1, 4), "max_dd": round(dd, 4),
                    "open": len(positions), "short_notional": round(acc.short_notional(), 2),
                    "hedge_value": round(h["qty"] * hmark, 2), "hedge_lots": h["lots"],
                    "hedge_pnl": round(h["realized"] + h["qty"] * (hmark - h["entry"]), 2),
                    "trades": len(t), "wins": len(wins),
                    "win_rate": round(len(wins) / len(t), 4) if t else None,
                    "avg_ret": round(sum(x["ret"] for x in t) / len(t), 4) if t else None,
                    "shorts_pnl": round(sum(x["pnl"] for x in t) + sum(p["pnl"] for p in positions), 2),
                    "fees": round(s["fees"], 2), "funding": round(s["funding"], 2)},
        "positions": positions, "upcoming": s["upcoming"], "skipped": skipped,
        "curve": [{"ts": r["ts"], "eq": float(r["equity"])} for r in pick],
        "trades": sorted(t, key=lambda x: x["exit_ts"], reverse=True)[:80],
        "errors": errs, "run_errors": run_errors,
    })


def main() -> int:
    now_ms = int(time.time() * 1000)
    acc = Account(fb.load_json(STATE_JSON, {}), fb.load_json(TRADES_JSON, []), now_ms)
    errors = 0
    try:
        meta = {}
        for c in fb.api("/api/v1/contracts/active"):
            if c["symbol"].endswith("USDTM") and c.get("status") == "Open":
                meta[c["symbol"][:-5]] = {"multiplier": float(c["multiplier"]), "lot": int(c["lotSize"]),
                                          "mark": float(c["markPrice"] or 0), "first": int(c["firstOpenDate"]),
                                          "stock": is_stock(c)}
        for coin in list(acc.s["positions"]) + [HEDGE]:
            if coin in meta:
                acc.marks[coin] = meta[coin]["mark"]
        errors += manage(acc, meta)
        errors += scan(acc, meta)
        rebalance_hedge(acc, meta)
    except Exception as e:
        errors += 1
        log_error(f"Lauf: {e}", now_ms)
    fb.save_json(STATE_JSON, acc.s)
    fb.save_json(TRADES_JSON, acc.trades)
    build_dashboard(acc, errors)
    print(f"{fb.iso(now_ms)} Strategie 3: {acc.equity():.2f} USDT · {len(acc.s['positions'])} Shorts "
          f"({acc.short_notional():.2f}) · Bitcoin-Long {acc.s['hedge']['lots']} Kontrakte · {len(acc.trades)} Trades")
    return 0


if __name__ == "__main__":
    sys.exit(main())
