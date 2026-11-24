#!/usr/bin/env python3
"""Solana Edge: scheduled, wallet-free paper-trading tick.

Persists SQLite in this repository. Never signs or submits transactions.
Source: GeckoTerminal public pool endpoint. API failures => NO TRADE.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import pathlib
import sqlite3
import time
import math
import urllib.request
from urllib.error import HTTPError, URLError

HERE = pathlib.Path(__file__).resolve().parent
DB = pathlib.Path(os.environ.get("PAPER_DATA_DIR", str(HERE))) / "state.sqlite3"
STATUS = DB.parent / "status.json"
API_ROOT = "https://api.geckoterminal.com/api/v2/networks/solana/"
START_EUR = 200.0
STAKE_EUR = 15.0
COST_PER_SIDE = 0.012   # illustrative 0.2% fees + 1.0% slippage
FIXED_ROUNDTRIP_EUR = 0.08
MAX_POSITIONS = 2
MAX_DAILY_LOSS_EUR = 6.0
MAX_TOTAL_LOSS_EUR = 20.0
MIN_LIQ_USD = 40_000.0
MIN_VOLUME_USD = 150_000.0
MIN_OBSERVATIONS = 3
OBS_WINDOW_SEC = 90 * 60
MAX_HOLD_SEC = 2 * 3600
COOLDOWN_SEC = 3 * 3600

def now() -> int:
    return int(time.time())

def get_json(path):
    request = urllib.request.Request(API_ROOT + path, headers={
        "Accept": "application/json", "User-Agent": "SolanaEdgePaper/2.0"
    })
    with urllib.request.urlopen(request, timeout=18) as response:
        data = json.load(response)
    if not isinstance(data.get("data"), list):
        raise ValueError("Unexpected GeckoTerminal response")
    return data["data"]


def get_market(db):
    """Track existing pools even after they drop out of Trending.

    Discovery is a point-in-time universe; multi lookup keeps continuity for
    tracked pools and prevents open positions from becoming invisible.
    """
    trending = get_json("new_pools?page=1" if int(now()/60)%2 else "trending_pools?page=1")
    known = {str((p.get("attributes") or {}).get("address") or
                 str(p.get("id", "")).removeprefix("solana_")): p
             for p in trending}
    old = [r["pool"] for r in db.execute(
        """SELECT pool FROM prices WHERE ts>=? GROUP BY pool
           ORDER BY MAX(ts) DESC LIMIT 20""", (now()-48*3600,))]
    active = [r["pool"] for r in db.execute("SELECT pool FROM positions")]
    active += [r["pool"] for r in db.execute("SELECT pool FROM pending")]
    follow = list(dict.fromkeys(active + old))
    missing = [address for address in follow
               if address not in known and 32 <= len(address) <= 55][:20]
    if missing and int(now()/30)%2 == 0:
        try:
            for p in get_json("pools/multi/" + ",".join(missing)):
                a = p.get("attributes") or {}
                addr = str(a.get("address") or str(p.get("id", "")).removeprefix("solana_"))
                known[addr] = p
        except (HTTPError, URLError, TimeoutError, ValueError) as err:
            # Do not lose the normal trending scan when enrichment is rate-limited.
            print("WARN existing-pool refresh: " + str(err)[:150])
    return list(known.values())

def connect():
    db = sqlite3.connect(DB, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=DELETE")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS prices(
        ts INTEGER NOT NULL,
        pool TEXT NOT NULL,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        liquidity REAL NOT NULL,
        volume24 REAL NOT NULL,
        change5m REAL,
        PRIMARY KEY(ts,pool)
    );
    CREATE INDEX IF NOT EXISTS ix_prices_pool_time ON prices(pool,ts);
    CREATE TABLE IF NOT EXISTS positions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pool TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        entry_ts INTEGER NOT NULL,
        entry_price REAL NOT NULL,
        stake_eur REAL NOT NULL,
        strategy TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pool TEXT NOT NULL,
        name TEXT NOT NULL,
        entry_ts INTEGER NOT NULL,
        exit_ts INTEGER NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL NOT NULL,
        stake_eur REAL NOT NULL,
        pnl_eur REAL NOT NULL,
        reason TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS pending(
        pool TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        signal_ts INTEGER NOT NULL,
        action TEXT NOT NULL,
        reason TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS meta(
        k TEXT PRIMARY KEY,
        v TEXT NOT NULL
    );
    """)
    return db

def parse_pools(raw, ts):
    pool_map = {}
    for item in raw:
        a = item.get("attributes") or {}
        addr = str(a.get("address") or str(item.get("id", "")).removeprefix("solana_"))
        try:
            price = float(a.get("base_token_price_usd") or 0)
            liquidity = float(a.get("reserve_in_usd") or 0)
            volume = float((a.get("volume_usd") or {}).get("h24") or 0)
            change5m = float((a.get("price_change_percentage") or {}).get("m5") or 0)
            created = a.get("pool_created_at")
            age_ts = int(dt.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()) if created else 0
        except (TypeError, ValueError, AttributeError, OverflowError):
            continue
        if not 32 <= len(addr) <= 55 or price <= 0 or liquidity <= 0 or volume <= 0:
            continue
        if age_ts and ts - age_ts < 600:
            continue
        name = str(a.get("name") or "Unknown pool")[:80]
        tx = (a.get("transactions") or {}).get("m5") or {}
        try:
            buys, sells = int(tx.get("buys") or 0), int(tx.get("sells") or 0)
            v5 = float((a.get("volume_usd") or {}).get("m5") or 0)
        except (ValueError, TypeError):
            continue
        if not all(math.isfinite(v) for v in (price,liquidity,volume,change5m,v5)):
            continue
        pool_map[addr] = dict(pool=addr, name=name, price=price, liquidity=liquidity,
                              volume=volume, change5m=change5m, buys=buys, sells=sells, v5=v5)
    return pool_map

def realized(db):
    return float(db.execute("SELECT COALESCE(SUM(pnl_eur),0) AS s FROM trades").fetchone()["s"])

def daily_realized(db, ts):
    start = int(dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())
    return float(db.execute("SELECT COALESCE(SUM(pnl_eur),0) AS s FROM trades WHERE exit_ts>=?", (start,)).fetchone()["s"])

def net_pnl(stake, entry, exit):
    """Simple roundtrip model; assumes adverse fills, no MEV or failure."""
    return stake * (exit / entry) * (1 - COST_PER_SIDE) - stake * (1 + COST_PER_SIDE) - FIXED_ROUNDTRIP_EUR

def run_tick(db, market, ts):
    by_pool = parse_pools(market, ts)
    for p in by_pool.values():
        db.execute("""INSERT OR REPLACE INTO prices
            (ts,pool,name,price,liquidity,volume24,change5m)
            VALUES(?,?,?,?,?,?,?)""",
            (ts,p["pool"],p["name"],p["price"],p["liquidity"],p["volume"],p["change5m"]))
    db.commit()
    messages = []
    diagnostics = {"observed": len(by_pool), "liquid": 0, "volume_ok": 0,
                   "history_ok": 0, "momentum_ok": 0, "queued": 0}
    for pp in by_pool.values():
        if pp["liquidity"] >= MIN_LIQ_USD:
            diagnostics["liquid"] += 1
            if pp["volume"] >= MIN_VOLUME_USD:
                diagnostics["volume_ok"] += 1
    # Fill existing exit orders only when a fresh independent snapshot arrives.
    for pend in db.execute("SELECT * FROM pending WHERE action='SELL'").fetchall():
        p = by_pool.get(pend["pool"])
        if p is None or pend["signal_ts"] >= ts:
            continue
        pos = db.execute("SELECT * FROM positions WHERE pool=?", (pend["pool"],)).fetchone()
        if pos:
            pl = net_pnl(pos["stake_eur"], pos["entry_price"], p["price"])
            db.execute("""INSERT INTO trades
                (pool,name,entry_ts,exit_ts,entry_price,exit_price,stake_eur,pnl_eur,reason)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (pos["pool"],pos["name"],pos["entry_ts"],ts,pos["entry_price"],
                 p["price"],pos["stake_eur"],pl,pend["reason"]))
            db.execute("DELETE FROM positions WHERE pool=?", (pos["pool"],))
            messages.append(f"CLOSE {pos['name']}: {pl:+.2f} EUR")
        db.execute("DELETE FROM pending WHERE pool=?", (pend["pool"],))
    # Do not open new virtual positions after reaching circuit-breaker limits.
    can_buy = (realized(db) > -MAX_TOTAL_LOSS_EUR
               and daily_realized(db, ts) > -MAX_DAILY_LOSS_EUR)
    # Fill pending BUY one tick later; recheck current liquidity.
    for pend in db.execute("SELECT * FROM pending WHERE action='BUY'").fetchall():
        p = by_pool.get(pend["pool"])
        if pend["signal_ts"] >= ts:
            continue
        count = db.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"]
        if (can_buy and p and p["liquidity"] >= MIN_LIQ_USD
                and p["volume"] >= MIN_VOLUME_USD and count < MAX_POSITIONS
                and not db.execute("SELECT 1 FROM positions WHERE pool=?", (pend["pool"],)).fetchone()):
            db.execute("""INSERT INTO positions
                (pool,name,entry_ts,entry_price,stake_eur,strategy)
                VALUES(?,?,?,?,?,?)""",
                (p["pool"],p["name"],ts,p["price"],STAKE_EUR,"momentum_90m"))
            messages.append(f"OPEN PAPER {p['name']} at {p['price']:.8g}")
        db.execute("DELETE FROM pending WHERE pool=?", (pend["pool"],))
    # Create exit signal at current snapshot; fill on *next* snapshot.
    for pos in db.execute("SELECT * FROM positions").fetchall():
        p = by_pool.get(pos["pool"])
        if not p:
            continue
        move = p["price"] / pos["entry_price"] - 1
        age = ts - pos["entry_ts"]
        reason = ("stop" if move <= -0.05 else
                  "take" if move >= 0.10 else
                  "timeout" if age >= MAX_HOLD_SEC else None)
        if reason:
            db.execute("INSERT OR IGNORE INTO pending VALUES(?,?,?,?,?)",
                       (p["pool"],p["name"],ts,"SELL",reason))
    # Signal: liquid, volume-supported pool, observed repeatedly over >= 45min,
    # 3-12% rise over observation period; not a verified trading edge.
    n = db.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"]
    queued = db.execute("SELECT COUNT(*) AS n FROM pending WHERE action='BUY'").fetchone()["n"]
    if can_buy and n + queued < MAX_POSITIONS:
        selected = sorted(by_pool.values(), key=lambda x: x["volume"], reverse=True)
        for p in selected[:40]:
            if p["liquidity"] < MIN_LIQ_USD or p["volume"] < MIN_VOLUME_USD:
                continue
            if db.execute("SELECT 1 FROM positions WHERE pool=?", (p["pool"],)).fetchone():
                continue
            if db.execute("SELECT 1 FROM pending WHERE pool=?", (p["pool"],)).fetchone():
                continue
            prev = db.execute("""SELECT ts,price FROM prices WHERE pool=? AND ts>=? AND ts<?
                ORDER BY ts ASC LIMIT 1""", (p["pool"], ts - OBS_WINDOW_SEC, ts - 30*60)).fetchone()
            count = db.execute("""SELECT COUNT(*) AS n FROM prices
                WHERE pool=? AND ts>=?""", (p["pool"], ts - OBS_WINDOW_SEC)).fetchone()["n"]
            last = db.execute("SELECT MAX(exit_ts) AS x FROM trades WHERE pool=?", (p["pool"],)).fetchone()["x"]
            if not prev or count < MIN_OBSERVATIONS or (last and ts-last < COOLDOWN_SEC):
                continue
            diagnostics["history_ok"] += 1
            change = p["price"]/prev["price"]-1
            if 0.02 <= change <= 0.10 and -15 <= p["change5m"] <= 12:
                diagnostics["momentum_ok"] += 1
                db.execute("INSERT OR IGNORE INTO pending VALUES(?,?,?,?,?)",
                           (p["pool"],p["name"],ts,"BUY","momentum_90m"))
                messages.append(f"SIGNAL {p['name']} +{change*100:.1f}%")
                queued += 1
                diagnostics["queued"] += 1
                if n + queued >= MAX_POSITIONS:
                    break
    db.execute("DELETE FROM prices WHERE ts<?", (ts - 14*86400,))
    db.commit()
    db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", ("diagnostics", json.dumps(diagnostics)))
    db.commit()
    return by_pool, messages

def publish(db, ts, pools, messages, error=None):
    closed = db.execute("SELECT * FROM trades ORDER BY exit_ts DESC LIMIT 50").fetchall()
    positions = db.execute("SELECT * FROM positions ORDER BY entry_ts DESC").fetchall()
    unrealized = sum(net_pnl(p["stake_eur"], p["entry_price"], pools[p["pool"]]["price"])
                     for p in positions if p["pool"] in pools)
    pending = db.execute("SELECT * FROM pending").fetchall()
    real = realized(db)
    total = real + unrealized
    out = {
        "version": "paper-2", "updated_at": dt.datetime.fromtimestamp(ts,dt.timezone.utc).isoformat(),
        "mode": "PAPER", "wallet_connected": False, "status": "DEGRADED" if error else "OK",
        "error": error, "start_eur": START_EUR, "realized_eur": round(real,4),
        "unrealized_eur": round(unrealized,4), "equity_estimate_eur": round(START_EUR+total,4),
        "daily_realized_eur": round(daily_realized(db,ts),4),
        "positions": [dict(p) for p in positions],
        "closed_trades": [dict(t) for t in closed],
        "pending": [dict(p) for p in pending],
        "observed_pools": len(pools), "notes": messages,
        "diagnostics": json.loads((db.execute("SELECT v FROM meta WHERE k='diagnostics'").fetchone() or {"v":"{}"})["v"]),
        "limits": {"max_positions": MAX_POSITIONS, "stake_eur": STAKE_EUR,
                   "daily_loss_eur": MAX_DAILY_LOSS_EUR, "total_loss_eur": MAX_TOTAL_LOSS_EUR},
        "caveat": "Paper fills are based on snapshots and illustrative slippage; not executable quotes."
    }
    STATUS.write_text(json.dumps(out, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return out

# V7 experiments use a separate database; original account is retained unchanged.
STRATEGIES = ("momentum", "volume_breakout", "trailing_trend")
V7 = DB.parent / "strategies.sqlite3"
def experiment_tick(market, ts):
    pools = parse_pools(market, ts)
    with sqlite3.connect(V7, timeout=20) as db:
        db.row_factory=sqlite3.Row
        db.executescript("""
        CREATE TABLE IF NOT EXISTS samples(t INTEGER,pool TEXT,price REAL,v5 REAL,PRIMARY KEY(t,pool));
        CREATE TABLE IF NOT EXISTS signals(strategy TEXT,pool TEXT,name TEXT,t INTEGER,price REAL,PRIMARY KEY(strategy,pool));
        CREATE TABLE IF NOT EXISTS opens(strategy TEXT,pool TEXT,name TEXT,t INTEGER,entry REAL,peak REAL,mark REAL,marked INTEGER,PRIMARY KEY(strategy,pool));
        CREATE TABLE IF NOT EXISTS closes(strategy TEXT,pool TEXT,name TEXT,t INTEGER,entry REAL,exit REAL,pnl REAL,reason TEXT);
        """)
        # Observe current snapshots before deciding; signals from earlier ticks only can fill.
        for p in pools.values():
            db.execute("INSERT OR IGNORE INTO samples VALUES(?,?,?,?)",(ts,p["pool"],p["price"],p["v5"]))
        accounts=[]
        for strategy in STRATEGIES:
            notes=[]
            total=db.execute("SELECT COALESCE(SUM(pnl),0) FROM closes WHERE strategy=?",(strategy,)).fetchone()[0]
            day=ts-ts%86400
            daily=db.execute("SELECT COALESCE(SUM(pnl),0) FROM closes WHERE strategy=? AND t>=?",(strategy,day)).fetchone()[0]
            # Exit on newly observed market price, not a guaranteed stop-price fill.
            for pos in db.execute("SELECT * FROM opens WHERE strategy=?",(strategy,)).fetchall():
                p=pools.get(pos["pool"])
                if not p:
                    continue
                px=p["price"]; peak=max(px,pos["peak"])
                move=px/pos["entry"]-1
                reason="stop" if move<=-.05 else ("take" if strategy!="trailing_trend" and move>=.12 else ("trail" if strategy=="trailing_trend" and peak/pos["entry"]>=1.06 and px/peak<=.95 else ("timeout" if ts-pos["t"]>=7200 else None)))
                if reason:
                    pnl=net_pnl(15,pos["entry"],px)
                    db.execute("INSERT INTO closes VALUES(?,?,?,?,?,?,?,?)",(strategy,pos["pool"],pos["name"],ts,pos["entry"],px,pnl,reason))
                    db.execute("DELETE FROM opens WHERE strategy=? AND pool=?",(strategy,pos["pool"]))
                    total+=pnl;daily+=pnl
                    notes.append("CLOSE "+pos["name"]+" "+str(round(pnl,2)))
                else:
                    db.execute("UPDATE opens SET peak=?,mark=?,marked=? WHERE strategy=? AND pool=?",(peak,px,ts,strategy,pos["pool"]))
            positions=db.execute("SELECT * FROM opens WHERE strategy=?",(strategy,)).fetchall()
            unreal=sum(net_pnl(15,r["entry"],r["mark"]) for r in positions)
            stale=sum(ts-r["marked"]>120 for r in positions)
            can_buy=total+min(0,unreal)>-20 and daily+min(0,unreal)>-6 and stale==0
            count=len(positions)
            for sig in db.execute("SELECT * FROM signals WHERE strategy=?",(strategy,)).fetchall():
                if sig["t"]>=ts:continue
                p=pools.get(sig["pool"])
                if can_buy and count<2 and ts-sig["t"]<=120 and p and p["liquidity"]>=40000 and p["volume"]>=150000 and abs(p["price"]/sig["price"]-1)<=.03:
                    db.execute("INSERT OR IGNORE INTO opens VALUES(?,?,?,?,?,?,?,?)",(strategy,p["pool"],p["name"],ts,p["price"],p["price"],p["price"],ts))
                    count+=1;notes.append("OPEN "+p["name"])
                db.execute("DELETE FROM signals WHERE strategy=? AND pool=?",(strategy,sig["pool"]))
            eligible=0; ready=0; matches=0
            if can_buy:
                for p in sorted(pools.values(),key=lambda q:q["v5"],reverse=True):
                    if p["liquidity"]<40000 or p["volume"]<150000:continue
                    eligible+=1
                    if db.execute("SELECT 1 FROM opens WHERE strategy=? AND pool=?",(strategy,p["pool"])).fetchone():continue
                    if db.execute("SELECT 1 FROM closes WHERE strategy=? AND pool=? AND t>?",(strategy,p["pool"],ts-1800)).fetchone():continue
                    history=db.execute("SELECT * FROM samples WHERE pool=? AND t BETWEEN ? AND ? ORDER BY t",(p["pool"],ts-900,ts-120)).fetchall()
                    if len(history)<3:continue
                    ready+=1
                    change=p["price"]/history[0]["price"]-1
                    n=p["buys"]+p["sells"]; pressure=p["buys"]/max(n,1)
                    mean=sum(q["v5"] for q in history)/len(history)
                    if strategy=="momentum":
                        match=.02<=change<=.12 and 1<=p["change5m"]<=10 and n>=15 and pressure>=.60
                    elif strategy=="volume_breakout":
                        match=mean>0 and p["v5"]>=1.8*mean and p["v5"]>=1000 and 1<=p["change5m"]<=12 and n>=15 and pressure>=.60
                    else:
                        match=.015<=change<=.10 and p["price"]>max(q["price"] for q in history) and n>=10 and pressure>=.55
                    if match:
                        matches+=1
                        if count<2:
                            db.execute("INSERT OR IGNORE INTO signals VALUES(?,?,?,?,?)",(strategy,p["pool"],p["name"],ts,p["price"]))
                            count+=1
            positions=[dict(q) for q in db.execute("SELECT * FROM opens WHERE strategy=?",(strategy,))]
            trades=[dict(q) for q in db.execute("SELECT * FROM closes WHERE strategy=? ORDER BY t DESC LIMIT 30",(strategy,))]
            n=db.execute("SELECT COUNT(*) FROM closes WHERE strategy=?",(strategy,)).fetchone()[0]
            unreal=sum(net_pnl(15,r["entry"],r["mark"]) for r in positions)
            accounts.append(dict(strategy=strategy,start_eur=200,realized_eur=round(total,4),equity_estimate_eur=round(200+total+unreal,4),positions=positions,closed_trades=trades,trade_count=n,eligible=eligible,history_ready=ready,signals=matches,stale_positions=stale,notes=notes))
        db.execute("DELETE FROM samples WHERE t<?",(ts-2*86400,))
    return accounts

def main():
    DB.parent.mkdir(parents=True, exist_ok=True)
    ts = now()
    db = connect()
    try:
        market = get_market(db)
        pools, messages = run_tick(db, market, ts)
        summary = publish(db, ts, pools, messages)
        summary["strategies"] = experiment_tick(market, ts)
        summary["version"] = "paper-3"
        tmp=STATUS.with_suffix(".tmp")
        tmp.write_text(json.dumps(summary,ensure_ascii=False),encoding="utf-8")
        tmp.replace(STATUS)
        print(json.dumps({"experiments": [{"strategy":a["strategy"],"signals":a["signals"],"positions":len(a["positions"]),"trades":a["trade_count"]} for a in summary["strategies"]]}),flush=True)
        print(json.dumps({"status": "OK", "observed": len(pools),
                          "open": len(summary["positions"]), "closed": len(summary["closed_trades"]),
                          "equity": summary["equity_estimate_eur"], "messages": messages, "diagnostics":summary["diagnostics"]}))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        # Explicitly record outage; no paper orders on incomplete snapshots.
        summary = publish(db, ts, {}, [], f"{type(exc).__name__}: {str(exc)[:140]}")
        print(json.dumps({"status": "DEGRADED", "error": summary["error"]}))
    finally:
        db.close()

if __name__ == "__main__":
    main()
