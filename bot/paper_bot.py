#!/usr/bin/env python3
"""Paper-Trading-Bot fuer die stuendlichen "Bitcoin Up or Down"-Maerkte auf Polymarket.

Liest ausschliesslich oeffentliche Daten (kein Konto, kein Wallet, keine Orders):
  * Binance BTC/USDT-Kerzen (Abrechnungsquelle des Marktes)
  * Polymarket-Marktdaten und Orderbuch

Idee: Der Markt ist eine digitale Option. "Up" gewinnt, wenn der Schlusskurs der
1h-Kerze >= Eroeffnungskurs ist. Aus aktuellem Kurs, Eroeffnungskurs, Restlaufzeit
und kurzfristiger Volatilitaet folgt eine faire Wahrscheinlichkeit. Ist sie nach
Spread und Gebuehr deutlich besser als der Marktpreis, wird ein Scheintrade geloggt.

Nur Standardbibliothek, laeuft mit python3 >= 3.9.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

# ----------------------------------------------------------------------------
# Einstellungen
# ----------------------------------------------------------------------------
STAKE_USD = 10.0          # Einsatz pro Scheintrade
START_BANKROLL = 1000.0   # Startkapital (nur fuer die Anzeige)
MIN_EDGE = 0.05           # Mindestvorteil pro Anteil NACH Gebuehr (5 Cent)
MIN_TAU_MIN = 3.0         # nicht in den letzten 3 Minuten handeln
MAX_TAU_MIN = 57.0        # nicht in den ersten 3 Minuten handeln
MIN_PRICE, MAX_PRICE = 0.05, 0.95   # keine Lotterielose / Fast-Sicher-Wetten
VOL_WINDOW_MIN = 240      # Volatilitaet aus den letzten 4 h (1-Minuten-Renditen)
DEFAULT_FEE = {"rate": 0.07, "exponent": 1}   # falls der Markt nichts liefert

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
BINANCE = "https://data-api.binance.vision"   # oeffentlicher Marktdaten-Spiegel
SYMBOL = "BTCUSDT"
ET = ZoneInfo("America/New_York")
HOUR_MS = 3_600_000

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DOCS = os.path.join(ROOT, "docs")
OBS_CSV = os.path.join(DATA, "observations.csv")
TRADES_JSON = os.path.join(DATA, "trades.json")
ROUNDS_JSON = os.path.join(DATA, "rounds.json")
ERRORS_LOG = os.path.join(DATA, "errors.log")
DASH_JSON = os.path.join(DOCS, "data.json")

OBS_FIELDS = ["ts", "round_start", "tau_min", "spot", "open", "sigma_min",
              "p_model", "up_bid", "up_ask", "down_bid", "down_ask"]


# ----------------------------------------------------------------------------
# Reine Rechenfunktionen (in tests/ geprueft)
# ----------------------------------------------------------------------------
def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def sigma_per_minute(closes: list[float]) -> float:
    """Volatilitaet je Minute aus 1-Minuten-Schlusskursen (Mittelwert = 0 angenommen)."""
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 30:
        raise ValueError("zu wenige Kursdaten fuer die Volatilitaet")
    return math.sqrt(sum(r * r for r in rets) / len(rets))


def prob_up(spot: float, open_: float, sigma_min: float, tau_min: float) -> float:
    """P(Schlusskurs >= Eroeffnung) bei driftloser Zufallsbewegung."""
    if tau_min <= 0 or sigma_min <= 0:
        return 1.0 if spot >= open_ else 0.0
    z = math.log(spot / open_) / (sigma_min * math.sqrt(tau_min))
    return min(0.99, max(0.01, norm_cdf(z)))


def fee_per_share(price: float, fee: dict) -> float:
    """Taker-Gebuehr je Anteil: rate * (p * (1 - p)) ^ exponent."""
    return float(fee.get("rate", 0.0)) * (price * (1.0 - price)) ** float(fee.get("exponent", 1))


def best_levels(book: dict) -> dict:
    """Bestes Gebot/Angebot aus einem CLOB-Orderbuch (Reihenfolge egal)."""
    bids = [(float(x["price"]), float(x["size"])) for x in book.get("bids", [])]
    asks = [(float(x["price"]), float(x["size"])) for x in book.get("asks", [])]
    bid = max(bids) if bids else (None, 0.0)
    ask = min(asks) if asks else (None, 0.0)
    return {"bid": bid[0], "bid_size": bid[1], "ask": ask[0], "ask_size": ask[1]}


def decide(p_up: float, up: dict, down: dict, fee: dict, tau_min: float,
           min_order: float = 5.0) -> dict | None:
    """Liefert den Scheintrade mit dem groessten Vorteil oder None."""
    if not (MIN_TAU_MIN <= tau_min <= MAX_TAU_MIN):
        return None
    best = None
    for side, p, lv in (("Up", p_up, up), ("Down", 1.0 - p_up, down)):
        ask = lv.get("ask")
        if ask is None or not (MIN_PRICE <= ask <= MAX_PRICE):
            continue
        f = fee_per_share(ask, fee)
        edge = p - ask - f
        shares = math.floor(STAKE_USD / ask)
        if edge < MIN_EDGE or shares < min_order or lv.get("ask_size", 0) < shares:
            continue
        if best is None or edge > best["edge"]:
            best = {"side": side, "price": ask, "shares": shares, "fee": round(f * shares, 4),
                    "cost": round(shares * ask + f * shares, 4), "p_model": round(p, 4),
                    "edge": round(edge, 4)}
    return best


def slug_for(round_start: dt.datetime, with_year: bool = True) -> str:
    t = round_start.astimezone(ET)
    h12 = t.hour % 12 or 12
    ap = "am" if t.hour < 12 else "pm"
    year = f"-{t.year}" if with_year else ""
    return f"bitcoin-up-or-down-{t.strftime('%B').lower()}-{t.day}{year}-{h12}{ap}-et"


# ----------------------------------------------------------------------------
# Netz
# ----------------------------------------------------------------------------
def http_json(url: str, tries: int = 3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paper-bot/1.0",
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"Abruf fehlgeschlagen: {url} -> {last}")


def klines(interval: str, limit: int, start_ms: int | None = None) -> list:
    q = {"symbol": SYMBOL, "interval": interval, "limit": limit}
    if start_ms is not None:
        q["startTime"] = start_ms
    return http_json(f"{BINANCE}/api/v3/klines?{urllib.parse.urlencode(q)}")


def find_market(round_start: dt.datetime) -> dict | None:
    for with_year in (True, False):
        ev = http_json(f"{GAMMA}/events?slug={slug_for(round_start, with_year)}")
        for e in ev or []:
            for m in e.get("markets", []):
                st = (m.get("eventStartTime") or "").replace("Z", "+00:00")
                if st and dt.datetime.fromisoformat(st) != round_start:
                    continue   # gleicher Slug aus einem anderen Jahr
                outcomes = json.loads(m["outcomes"])
                tokens = json.loads(m["clobTokenIds"])
                tok = dict(zip(outcomes, tokens))
                if "Up" not in tok or "Down" not in tok:
                    continue
                fee = m.get("feeSchedule") if m.get("feesEnabled") else {"rate": 0.0, "exponent": 1}
                return {"slug": e.get("slug"), "up": tok["Up"], "down": tok["Down"],
                        "fee": fee or DEFAULT_FEE, "accepting": bool(m.get("acceptingOrders")),
                        "min_order": float(m.get("orderMinSize") or 5)}
    return None


# ----------------------------------------------------------------------------
# Dateien
# ----------------------------------------------------------------------------
def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def append_obs(row: dict) -> None:
    os.makedirs(DATA, exist_ok=True)
    new = not os.path.exists(OBS_CSV)
    with open(OBS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OBS_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def read_obs() -> list[dict]:
    if not os.path.exists(OBS_CSV):
        return []
    with open(OBS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def log_error(msg: str) -> None:
    os.makedirs(DATA, exist_ok=True)
    lines = []
    if os.path.exists(ERRORS_LOG):
        with open(ERRORS_LOG, encoding="utf-8") as f:
            lines = f.read().splitlines()
    lines.append(f"{iso(now_utc())} {msg}")
    with open(ERRORS_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines[-200:]) + "\n")
    print("FEHLER:", msg, file=sys.stderr)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


# ----------------------------------------------------------------------------
# Ablauf
# ----------------------------------------------------------------------------
def sample(trades: list) -> None:
    """Eine Messung: Daten holen, Beobachtung loggen, ggf. Scheintrade eroeffnen."""
    t = now_utc()
    round_start = t.replace(minute=0, second=0, microsecond=0)
    rs_ms = int(round_start.timestamp() * 1000)
    tau = (round_start + dt.timedelta(hours=1) - t).total_seconds() / 60.0

    k1 = klines("1m", VOL_WINDOW_MIN + 2)
    spot = float(k1[-1][4])                       # laufende Minute = aktueller Kurs
    sigma = sigma_per_minute([float(k[4]) for k in k1[:-1]])
    kh = [k for k in klines("1h", 2) if int(k[0]) == rs_ms]
    if not kh:
        raise RuntimeError("1h-Kerze der laufenden Runde noch nicht verfuegbar")
    open_ = float(kh[0][1])
    p = prob_up(spot, open_, sigma, tau)

    mkt = find_market(round_start)
    if mkt is None:
        raise RuntimeError(f"Markt nicht gefunden: {slug_for(round_start)}")
    up = best_levels(http_json(f"{CLOB}/book?token_id={mkt['up']}"))
    down = best_levels(http_json(f"{CLOB}/book?token_id={mkt['down']}"))

    append_obs({"ts": iso(t), "round_start": iso(round_start), "tau_min": round(tau, 2),
                "spot": spot, "open": open_, "sigma_min": f"{sigma:.6g}", "p_model": round(p, 4),
                "up_bid": up["bid"], "up_ask": up["ask"],
                "down_bid": down["bid"], "down_ask": down["ask"]})
    print(f"{iso(t)} tau={tau:5.1f}min spot={spot:.2f} open={open_:.2f} "
          f"p_up={p:.3f} markt_up={up['bid']}/{up['ask']}")

    if not mkt["accepting"] or any(x["round_start"] == iso(round_start) for x in trades):
        return                                     # hoechstens ein Trade je Runde
    d = decide(p, up, down, mkt["fee"], tau, mkt["min_order"])
    if d:
        d.update({"ts": iso(t), "round_start": iso(round_start), "slug": mkt["slug"],
                  "tau_min": round(tau, 2), "spot": spot, "open": open_, "status": "open",
                  "reason": (f"Modell {d['p_model']:.0%} fuer {d['side']}, Markt verlangt "
                             f"{d['price']:.2f}, Vorteil nach Gebuehr {d['edge'] * 100:.1f} Cent")})
        trades.append(d)
        print("  SCHEINTRADE:", d["reason"])


def settle(trades: list, rounds: dict) -> None:
    """Beendete Runden mit der fertigen Binance-Kerze abrechnen."""
    now_ms = int(now_utc().timestamp() * 1000)
    need = {o["round_start"] for o in read_obs()} | {x["round_start"] for x in trades}
    need = sorted(r for r in need if r not in rounds
                  and int(parse_iso(r).timestamp() * 1000) + HOUR_MS + 20_000 < now_ms)
    if need:
        recent = {int(k[0]): k for k in klines("1h", 100)}
        for r in need[-150:]:
            ms = int(parse_iso(r).timestamp() * 1000)
            k = recent.get(ms)
            if k is None:
                got = klines("1h", 1, ms)
                k = got[0] if got and int(got[0][0]) == ms else None
            if k is None:
                continue
            o, c = float(k[1]), float(k[4])
            rounds[r] = {"open": o, "close": c, "result": "Up" if c >= o else "Down"}
    for x in trades:
        res = rounds.get(x["round_start"])
        if x["status"] == "open" and res:
            win = x["side"] == res["result"]
            x.update({"status": "settled", "result": res["result"], "win": win,
                      "pnl": round((x["shares"] if win else 0.0) - x["cost"], 4)})


def brier(pairs: list[tuple[float, int]]) -> float | None:
    return round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None


def calibration(pairs: list[tuple[float, int]], bins: int = 10) -> list[dict]:
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        sel = [(p, y) for p, y in pairs if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        if sel:
            out.append({"mid": round((lo + hi) / 2, 2), "n": len(sel),
                        "pred": round(sum(p for p, _ in sel) / len(sel), 4),
                        "actual": round(sum(y for _, y in sel) / len(sel), 4)})
    return out


def build_dashboard(trades: list, rounds: dict, run_errors: int) -> None:
    obs = read_obs()
    model, market = [], []
    for o in obs:
        res = rounds.get(o["round_start"])
        if not res or not o["up_bid"] or not o["up_ask"]:
            continue
        y = 1 if res["result"] == "Up" else 0
        model.append((float(o["p_model"]), y))
        market.append(((float(o["up_bid"]) + float(o["up_ask"])) / 2, y))
    done = [x for x in trades if x["status"] == "settled"]
    wins = sum(1 for x in done if x["win"])
    pnl = sum(x["pnl"] for x in done)
    staked = sum(x["cost"] for x in done)
    equity, run = [], 0.0
    for x in done:
        run += x["pnl"]
        equity.append({"ts": x["ts"], "pnl": round(run, 2)})
    n = len(done)
    # 95-%-Bereich der Trefferquote (Wilson) -- zeigt, wie unsicher die Zahl noch ist
    ci = None
    if n:
        z, ph = 1.96, wins / n
        c = (ph + z * z / (2 * n)) / (1 + z * z / n)
        h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        ci = [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]
    last = obs[-1] if obs else None
    errs = []
    if os.path.exists(ERRORS_LOG):
        with open(ERRORS_LOG, encoding="utf-8") as f:
            errs = f.read().splitlines()[-5:]
    save_json(DASH_JSON, {
        "updated": iso(now_utc()),
        "config": {"stake": STAKE_USD, "min_edge": MIN_EDGE, "start_bankroll": START_BANKROLL},
        "summary": {
            "trades": n, "open": len(trades) - n, "wins": wins,
            "win_rate": round(wins / n, 4) if n else None, "win_rate_ci": ci,
            "breakeven": round(staked / sum(x["shares"] for x in done), 4) if n else None,
            "pnl": round(pnl, 2), "fees": round(sum(x["fee"] for x in done), 2),
            "roi": round(pnl / staked, 4) if staked else None,
            "bankroll": round(START_BANKROLL + pnl, 2),
            "observations": len(obs), "rounds": len(rounds),
            "brier_model": brier(model), "brier_market": brier(market),
        },
        "equity": equity[-500:],
        "calibration": {"model": calibration(model), "market": calibration(market)},
        "latest": last,
        "trades": list(reversed(trades))[:60],
        "errors": errs, "run_errors": run_errors,
    })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=1, help="Messungen pro Lauf")
    ap.add_argument("--interval", type=float, default=60, help="Sekunden zwischen Messungen")
    a = ap.parse_args()

    trades = load_json(TRADES_JSON, [])
    rounds = load_json(ROUNDS_JSON, {})
    errors = 0
    for i in range(a.samples):
        try:
            sample(trades)
        except Exception as e:                      # ein Fehler darf den Lauf nicht abbrechen
            errors += 1
            log_error(f"Messung: {e}")
            traceback.print_exc()
        save_json(TRADES_JSON, trades)
        if i < a.samples - 1:
            time.sleep(a.interval)
    try:
        settle(trades, rounds)
    except Exception as e:
        errors += 1
        log_error(f"Abrechnung: {e}")
        traceback.print_exc()
    save_json(TRADES_JSON, trades)
    save_json(ROUNDS_JSON, rounds)
    build_dashboard(trades, rounds, errors)
    return 0 if errors < a.samples else 1           # nur rot, wenn gar nichts ging


if __name__ == "__main__":
    sys.exit(main())
