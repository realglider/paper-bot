import sys,json
from bt import *
import bt
idx,S,A,F=load(); print(len(S),S)
SPLIT=pd.Timestamp("2025-11-01")
def line(tag,name,p,**kw):
    e1,t1=run(idx,S,A,F,name,p,t1=SPLIT,**kw); e2,t2=run(idx,S,A,F,name,p,t0=SPLIT,**kw); e3,t3=run(idx,S,A,F,name,p,**kw)
    a,b,c=stats(e1,t1),stats(e2,t2),stats(e3,t3)
    print(f"{tag:34} IS {a['cagr']:6} dd{a['dd']:6} pf{a['pf']:5} n{a['n']:4} | OOS {b['cagr']:6} dd{b['dd']:6} pf{b['pf']:5} n{b['n']:4} | ALL {c['cagr']:6} dd{c['dd']:6} sh{c['sharpe']:5} win{c['win']:5} end{c['end']:6}",flush=True)
    return e3,t3
print("--- Nachbarschaft Donchian")
for n in (720,960,1200,1320):
    for k in (10,12,14,16): line(f"donchian n={n} k={k}","donchian",dict(n=n),stop_k=k)
print("--- gewaehlt: n=960 k=12, Varianten")
P=dict(n=960)
for r in (0.005,0.01): line(f"risk={r}","donchian",P,stop_k=12,risk=r)
for sd in ("long","short"): line(f"nur {sd}","donchian",P,stop_k=12,side=sd)
bt.SLIP=0.0010; line("Slippage 0,10 % je Seite","donchian",P,stop_k=12); bt.SLIP=0.0004
bt.FEE=0.0012; line("doppelte Gebuehr","donchian",P,stop_k=12); bt.FEE=0.0006
print("--- je Coin (ALL, risk 1 %)")
for i,s in enumerate(S):
    e,t=run(idx,S,A,F,"donchian",P,stop_k=12,cols=[i]); print(f"{s:5} end {e.iloc[-1]:8.1f} n {len(t):3} win {(t>0).mean()*100 if len(t) else 0:5.1f}")
e,t=run(idx,S,A,F,"donchian",P,stop_k=12,risk=0.005)
m=e.resample("ME").last(); print("Monatsrenditen % (risk 0,5 %):"); print((m.pct_change().dropna()*100).round(1).to_string())
print("groesster Einzelgewinn/-verlust $:",t.max().round(1),t.min().round(1),"Top-5-Anteil am Gewinn:",round(np.sort(t)[-5:].sum()/t.sum(),2) if t.sum()>0 else None)
