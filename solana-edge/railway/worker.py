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
import email.utils
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

# One upstream request per scan, with a shared cooldown after HTTP 429.
_next_request=0.0
_rate_strikes=0
_market_turn=0

class RateLimited(Exception):
    pass

def get_json(path):
    global _next_request, _rate_strikes
    if time.monotonic() < _next_request:
        raise RateLimited("GeckoTerminal cooldown active")
    # Start-to-start spacing, even when called by a different code path.
    _next_request=time.monotonic()+30
    request=urllib.request.Request(API_ROOT+path,headers={
        "Accept":"application/json","User-Agent":"SolanaEdgePaper/2.1"
    })
    try:
        with urllib.request.urlopen(request,timeout=18) as response:
            data=json.load(response)
    except HTTPError as exc:
        if exc.code!=429:
            raise
        _rate_strikes+=1
        delay=min(600,60*2**min(_rate_strikes-1,4))
        header=exc.headers.get("Retry-After") if exc.headers else None
        if header:
            try:
                delay=max(delay,float(header))
            except ValueError:
                try:
                    retry=email.utils.parsedate_to_datetime(header)
                    delay=max(delay,retry.timestamp()-time.time())
                except (ValueError,TypeError,OverflowError):
                    pass
        _next_request=time.monotonic()+delay
        raise RateLimited("GeckoTerminal HTTP 429: pause requested") from exc
    if not isinstance(data.get("data"),list):
        raise ValueError("Unexpected GeckoTerminal response")
    _rate_strikes=0
    return data["data"]


def get_market(db):
    """Use one request per scan. Alternate discovery and active-pool refresh."""
    global _market_turn
    turn=_market_turn%4
    active=[r["pool"] for r in db.execute("SELECT pool FROM positions")]
    active += [r["pool"] for r in db.execute("SELECT pool FROM pending")]
    # Experiments have separate persistent tables. Prioritize their open
    # positions and pending signals rather than only the legacy portfolio.
    if V7.exists():
        with sqlite3.connect(V7,timeout=20) as research:
            tables={r[0] for r in research.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("opens","rb_opens","signals","rb_signals"):
                if table in tables:
                    active += [r[0] for r in research.execute("SELECT DISTINCT pool FROM "+table)]
    old=[r["pool"] for r in db.execute(
        """SELECT pool FROM prices WHERE ts>=? AND liquidity>=40000
           GROUP BY pool ORDER BY MAX(ts) DESC, MAX(volume24) DESC LIMIT 20""",
        (now()-1800,))]
    tracked=[a for a in dict.fromkeys(active+old)
             if 32<=len(a)<=55 and a.isalnum()][:20]
    if turn in (0,2) and tracked:
        path="pools/multi/"+",".join(tracked)
    else:
        path="new_pools?page=1" if turn==3 else "trending_pools?page=1"
    result=get_json(path)
    _market_turn+=1
    return result

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
    return out

# V7 experiments use a separate database; original account is retained unchanged.
STRATEGIES = ("momentum", "volume_breakout", "trailing_trend", "pullback_hunter", "smart_accumulation")
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
                if strategy in ("pullback_hunter","smart_accumulation"):
                    reason="stop" if move<=-.05 else ("trail" if peak/pos["entry"]>=1.06 and px/peak<=.96 else ("timeout" if ts-pos["t"]>=7200 else None))
                else:
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
                    elif strategy=="trailing_trend":
                        match=.015<=change<=.10 and p["price"]>max(q["price"] for q in history) and n>=10 and pressure>=.55
                    elif strategy=="pullback_hunter":
                        # Chronological peak, later trough, then recovery. No future observations.
                        prices=[q["price"] for q in history]
                        peak_i=max(range(len(prices)),key=lambda i:prices[i])
                        high=prices[peak_i]
                        low=min(prices[peak_i+1:],default=high)
                        drop=1-low/high
                        match=(len(prices)>=6 and history[-1]["t"]-history[0]["t"]>=300
                               and peak_i>=1 and peak_i<len(prices)-1
                               and high/prices[0]>=1.06 and .05<=drop<=.12
                               and 1.02<=p["price"]/low<=1.07 and p["price"]<high
                               and n>=15 and pressure>=.60 and p["change5m"]>0)
                    elif strategy=="smart_accumulation":
                        # Compare early and late rolling volume observations, then require breakout.
                        mid=len(history)//2
                        early=history[:mid]; late=history[mid:]
                        top=max(q["price"] for q in history)
                        bottom=min(q["price"] for q in history)
                        ve=sum(q["v5"] for q in early)/max(len(early),1)
                        vl=sum(q["v5"] for q in late)/max(len(late),1)
                        match=(len(history)>=6 and history[-1]["t"]-history[0]["t"]>=300
                               and top/bottom<=1.05 and 1.005<=p["price"]/top<=1.04
                               and ve>0 and vl>=1.25*ve and p["v5"]>=1.15*vl
                               and p["v5"]>=1000 and n>=15 and pressure>=.60)
                    else:
                        match=False
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

# Range Breaker: separate experimental account. Uses sampled minute highs,
# not exchange OHLCV; never makes up missing intervals or fills at target prices.
def range_tick(market, ts):
    pools = parse_pools(market, ts)
    notes=[]
    with sqlite3.connect(V7, timeout=20) as db:
        db.row_factory=sqlite3.Row
        db.executescript("""
        CREATE TABLE IF NOT EXISTS rb_signals(pool TEXT PRIMARY KEY,name TEXT,t INTEGER,price REAL,ceiling REAL,stop REAL);
        CREATE TABLE IF NOT EXISTS rb_opens(pool TEXT PRIMARY KEY,name TEXT,t INTEGER,entry REAL,peak REAL,mark REAL,marked INTEGER,stop REAL,risk REAL,remaining REAL,stage INTEGER,partial REAL);
        CREATE TABLE IF NOT EXISTS rb_legs(pool TEXT,t INTEGER,pnl REAL);
        CREATE TABLE IF NOT EXISTS rb_closes(pool TEXT,name TEXT,t INTEGER,entry REAL,exit REAL,pnl REAL,reason TEXT);
        """)
        def liquid(p):
            return p and p["liquidity"]>=50000 and p["volume"]>=150000
        def leg(pos,px,fraction):
            pnl=net_pnl(15,pos["entry"],px)*fraction
            db.execute("INSERT INTO rb_legs VALUES(?,?,?)",(pos["pool"],ts,pnl))
            return pnl
        for pos in db.execute("SELECT * FROM rb_opens").fetchall():
            p=pools.get(pos["pool"])
            if not p:
                continue
            px=p["price"]; peak=max(pos["peak"],px)
            remaining=pos["remaining"]; stage=pos["stage"]; partial=pos["partial"]
            # Stop has priority. Fill at the observed price, including gaps.
            stop=pos["stop"]
            if stage>=1: stop=max(stop,pos["entry"])
            if stage>=3: stop=max(stop,peak*.96)
            reason="stop" if px<=stop else ("timeout" if ts-pos["t"]>=7200 else None)
            if reason:
                partial+=leg(pos,px,remaining)
                db.execute("INSERT INTO rb_closes VALUES(?,?,?,?,?,?,?)",(pos["pool"],pos["name"],ts,pos["entry"],px,partial,reason))
                db.execute("DELETE FROM rb_opens WHERE pool=?",(pos["pool"],))
                notes.append("CLOSE "+pos["name"]+" "+str(round(partial,2)))
                continue
            while stage<3 and px>=pos["entry"]+(stage+1)*pos["risk"]:
                partial+=leg(pos,px,.25)
                remaining-=.25
                stage+=1
                notes.append("TP"+str(stage)+" "+pos["name"])
            db.execute("UPDATE rb_opens SET peak=?,mark=?,marked=?,remaining=?,stage=?,partial=? WHERE pool=?",(peak,px,ts,remaining,stage,partial,pos["pool"]))
        def account():
            positions=[dict(r) for r in db.execute("SELECT * FROM rb_opens")]
            realized=db.execute("SELECT COALESCE(SUM(pnl),0) FROM rb_legs").fetchone()[0]
            daily=db.execute("SELECT COALESCE(SUM(pnl),0) FROM rb_legs WHERE t>=?",(ts-ts%86400,)).fetchone()[0]
            unreal=sum(net_pnl(15,r["entry"],r["mark"])*r["remaining"] for r in positions)
            stale=sum(ts-r["marked"]>120 for r in positions)
            return positions,realized,daily,unreal,stale
        positions,real,daily,unreal,stale=account()
        can_buy=real+min(0,unreal)>-20 and daily+min(0,unreal)>-6 and stale==0
        count=len(positions)
        for sig in db.execute("SELECT * FROM rb_signals").fetchall():
            if sig["t"]>=ts: continue
            p=pools.get(sig["pool"])
            if can_buy and count<2 and ts-sig["t"]<=120 and liquid(p):
                px=p["price"]; risk=px-sig["stop"]
                if px>=sig["ceiling"] and abs(px/sig["price"]-1)<=.03 and .015<=risk/px<=.05:
                    db.execute("INSERT OR IGNORE INTO rb_opens VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(sig["pool"],sig["name"],ts,px,px,px,ts,sig["stop"],risk,1.,0,0.))
                    count+=1
                    notes.append("OPEN "+sig["name"])
            db.execute("DELETE FROM rb_signals WHERE pool=?",(sig["pool"],))
        eligible=ready=matches=0
        cutoff=ts-ts%60
        if can_buy:
            for p in sorted(pools.values(),key=lambda x:x["v5"],reverse=True):
                if not liquid(p): continue
                eligible+=1
                if db.execute("SELECT 1 FROM rb_opens WHERE pool=?",(p["pool"],)).fetchone(): continue
                if db.execute("SELECT 1 FROM rb_closes WHERE pool=? AND t>?",(p["pool"],ts-1800)).fetchone(): continue
                # Last 20 distinct completed sampled minute intervals.
                hist=db.execute("""SELECT CAST(t/60 AS INTEGER) AS minute,MAX(price) AS hi,MIN(price) AS lo,AVG(v5) AS v
                    FROM samples WHERE pool=? AND t>=? AND t<? GROUP BY CAST(t/60 AS INTEGER)
                    ORDER BY minute DESC LIMIT 20""",(p["pool"],cutoff-2400,cutoff)).fetchall()
                if len(hist)!=20 or cutoff//60-hist[0]["minute"]>2 or any(hist[i]["minute"]-hist[i+1]["minute"]>3 for i in range(19)): continue
                ready+=1
                ceiling=max(r["hi"] for r in hist); floor=min(r["lo"] for r in hist)
                avg=sum(r["v"] for r in hist)/20
                n=p["buys"]+p["sells"]
                # Break above the prior range, not a late chase.
                if (ceiling>0 and floor>0 and ceiling/floor<=1.15 and 1.003<=p["price"]/ceiling<=1.03
                    and avg>0 and p["v5"]>=1.5*avg and n>=15 and p["buys"]/n>=.60):
                    matches+=1
                    if count<2:
                        db.execute("INSERT OR IGNORE INTO rb_signals VALUES(?,?,?,?,?,?)",(p["pool"],p["name"],ts,p["price"],ceiling,ceiling*.98))
                        count+=1
        positions,real,daily,unreal,stale=account()
        trades=[dict(r) for r in db.execute("SELECT * FROM rb_closes ORDER BY t DESC LIMIT 30")]
        trade_count=db.execute("SELECT COUNT(*) FROM rb_closes").fetchone()[0]
        return dict(strategy="range_breaker",start_eur=200,realized_eur=round(real,4),equity_estimate_eur=round(200+real+unreal,4),
                    positions=positions,closed_trades=trades,trade_count=trade_count,eligible=eligible,history_ready=ready,signals=matches,stale_positions=stale,notes=notes,
                    data_basis="20 completed sampled minute intervals; rolling 5m volume proxy; partial fills at observed prices")

def write_status(summary):
    tmp=STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(summary,ensure_ascii=False),encoding="utf-8")
    tmp.replace(STATUS)

def preserve_status(ts, state, error):
    # The timestamp of the last successful market observation must remain
    # unchanged. Do not recalculate equity or execute orders using stale data.
    try:
        summary=json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        summary={"mode":"PAPER","wallet_connected":False,"updated_at":None,"strategies":[]}
    summary["status"]=state
    summary["error"]=error
    summary["last_attempt_at"]=dt.datetime.fromtimestamp(ts,dt.timezone.utc).isoformat()
    summary["retry_at"]=dt.datetime.fromtimestamp(time.time()+max(0,_next_request-time.monotonic()),dt.timezone.utc).isoformat()
    for a in summary.get("strategies",[]):
        a["stale_positions"]=sum(ts-p.get("marked",0)>120 for p in a.get("positions",[]))
    write_status(summary)
    print(json.dumps({"status":state,"error":error,"retry_at":summary["retry_at"],"last_success_at":summary.get("updated_at")}),flush=True)

def main():
    DB.parent.mkdir(parents=True, exist_ok=True)
    ts = now()
    if time.monotonic() < _next_request:
        # The previous 429 status already contains its next retry time.
        return
    db = connect()
    try:
        market = get_market(db)
        if not parse_pools(market,ts):
            preserve_status(ts,"NO_DATA","No eligible fresh market observations")
            return
        pools, messages = run_tick(db, market, ts)
        summary = publish(db, ts, pools, messages)
        summary["strategies"] = experiment_tick(market, ts)
        summary["strategies"].append(range_tick(market, ts))
        summary["version"] = "paper-6"
        summary["last_attempt_at"]=summary["updated_at"]
        summary["retry_at"]=None
        write_status(summary)
        print(json.dumps({"experiments": [{"strategy":a["strategy"],"signals":a["signals"],"positions":len(a["positions"]),"trades":a["trade_count"]} for a in summary["strategies"]]}),flush=True)
        print(json.dumps({"status": "OK", "observed": len(pools),
                          "open": len(summary["positions"]), "closed": len(summary["closed_trades"]),
                          "equity": summary["equity_estimate_eur"], "messages": messages, "diagnostics":summary["diagnostics"]}))
    except RateLimited as exc:
        preserve_status(ts,"RATE_LIMITED",str(exc))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        preserve_status(ts,"DEGRADED",f"{type(exc).__name__}: {str(exc)[:140]}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
