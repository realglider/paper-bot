# Kern-Satellit: Bitcoin (optional + Ether) mit gestuftem Trendfilter als Kern, Coin-Rotation als Beimischung
import rot, rot_short as rs, pandas as pd, numpy as np, pickle
def core(C,coins=("BTC",),mas=(50,100,200),cost=0.003,own=False):
    """Tagesrenditen: Anteil = erfuellte Trends (0, 1/3, 2/3, 1). own=True: jeder Coin nach seinem eigenen Trend."""
    out=0
    for c in coins:
        ref=C[c] if own else C["BTC"]
        frac=sum((ref>ref.rolling(m).mean()).astype(float) for m in mas)/len(mas)
        w=frac.shift(1).fillna(0)
        out=out+(w*C[c].pct_change(fill_method=None).fillna(0)-(w-w.shift(1).fillna(0)).abs()*cost)/len(coins)
    return out
def rotation(O,C,Q,t0,t1):
    P=rot.prep2(C,Q); e,m=rot.sim2(O,C,P,t0=t0,t1=t1,vol_target=0.8,slots=True,min_w=0.10); return e.pct_change().fillna(0)
def table(O,C,Q,t0,t1,label):
    ra=rotation(O,C,Q,t0,t1); idx=ra.index
    cb=core(C).reindex(idx).fillna(0); cbe=core(C,("BTC","ETH"),own=True).reindex(idx).fillna(0)
    hold=C["BTC"].pct_change(fill_method=None).reindex(idx).fillna(0)
    print(f"=== {label}: {idx[0].date()} bis {idx[-1].date()}")
    rows={"Rotation allein (A)":ra,"Kern: BTC gestuft":cb,"Kern: BTC+ETH gestuft":cbe,"70 % Kern BTC + 30 % Rotation":0.7*cb+0.3*ra,"50 % Kern BTC + 50 % Rotation":0.5*cb+0.5*ra,"50 % Kern BTC+ETH + 50 % Rotation":0.5*cbe+0.5*ra,"Bitcoin halten":hold}
    for nm,r in rows.items():
        s=rs.st(r); print(f"{nm:36} Rendite/Jahr {s['cagr']:6}%  Rueckgang {s['dd']:6}%  Sharpe {s['sharpe']:5} | {rs.yr(r)}")
    return rows
if __name__=="__main__":
    import sys
    O,C,Q=pickle.load(open("bn_panel.pkl","rb"))
    for w in sys.argv[1:]:
        if w=="train": table(O,C,Q,*rot.TRAIN,"TRAIN")
        if w=="val": table(O,C,Q,*rot.VAL,"VALIDIERUNG")
