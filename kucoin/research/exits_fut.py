# Ausstiegsvarianten fuer den Futures-Teil
from bt import *
idx,S,A,F=load()
OLD="XBT ETH SOL XRP DOGE BNB ADA LINK AVAX LTC BCH DOT NEAR AAVE UNI TRX".split(); old=[S.index(s) for s in OLD]
SPLIT=pd.Timestamp("2025-11-01")
base=dict(cols=old,stop_k=12,risk=0.01,min_notional=100,max_risk=0.02,max_pos=3.0,max_gross=6.0,eq0=500.0)
V=[("Basis: Stop 12 ATR nachgezogen",{}),
   ("Einstand ab +1 R",dict(be_r=1.0,be_lock=0.05)),
   ("Einstand ab +1,5 R",dict(be_r=1.5,be_lock=0.05)),
   ("Einstand ab +2 R",dict(be_r=2.0,be_lock=0.05)),
   ("Einstand ab +2 R, +0,5 R sichern",dict(be_r=2.0,be_lock=0.5)),
   ("1/2 bei +1 R",dict(part_r=1.0)),
   ("1/2 bei +2 R",dict(part_r=2.0)),
   ("1/2 bei +3 R",dict(part_r=3.0)),
   ("1/3 bei +2 R",dict(part_r=2.0,part_frac=1/3)),
   ("1/2 bei +2 R, Einstand",dict(part_r=2.0,be_r=2.0,be_lock=0.05)),
   ("1/2 bei +1 R, Einstand ab +1 R",dict(part_r=1.0,be_r=1.0,be_lock=0.05)),
   ("1/2 bei +2 R, Einstand, Rest Stop 16 ATR",dict(part_r=2.0,be_r=2.0,be_lock=0.05,trail_k2=16)),
   ("1/2 bei +2 R, Rest ohne Nachziehen-Verengung 20",dict(part_r=2.0,be_r=2.0,be_lock=0.05,trail_k2=20)),
   ("Einstand ab +2 R, danach Stop 16 ATR",dict(be_r=2.0,be_lock=0.05,trail_k2=16)),
   ("Einstand ab +2 R, danach Stop 8 ATR",dict(be_r=2.0,be_lock=0.05,trail_k2=8)),
   ("Stop 8 ATR",dict(stop_k=8)),("Stop 16 ATR",dict(stop_k=16))]
for tag,kw in V:
    k={**base,**kw}
    a,b,c=[stats(*run(idx,S,A,F,"donchian",dict(n=960),**w,**k)) for w in (dict(t1=SPLIT),dict(t0=SPLIT),dict())]
    print(f"{tag:46} IS {a['cagr']:6} dd{a['dd']:6} | OOS {b['cagr']:6} dd{b['dd']:6} | ALL {c['cagr']:6} dd{c['dd']:6} sh{c['sharpe']:5} n{c['n']:4} win{c['win']:5} pf{c['pf']:5}",flush=True)
