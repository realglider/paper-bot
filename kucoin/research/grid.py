import sys,time,json
from bt import *
idx,S,A,F=load()
print(len(idx),idx[0],idx[-1],S,flush=True)
SPLIT=pd.Timestamp("2025-11-01")
G=[]
for n in (120,220,480):
    for k in (3,5): G.append(("donchian",dict(n=n),dict(stop_k=k)))
for f,s in ((48,200),(100,400),(200,800)):
    for k in (3,5): G.append(("ema",dict(f=f,s=s),dict(stop_k=k)))
for L in (168,336,720): G.append(("tsmom",dict(L=L),dict(stop_k=5)))
for L in (168,336,720): G.append(("xsmom",dict(L=L,k=4),dict(stop_k=8,trail=False)))
for lo,hi in ((25,75),(20,80)): G.append(("rsi_mr",dict(lo=lo,hi=hi),dict(stop_k=3,trail=False)))
for z in (2.0,2.5,3.0): G.append(("bb_mr",dict(z=z),dict(stop_k=3,trail=False)))
out=[]
for name,p,kw in G:
    t=time.time()
    e1,t1=run(idx,S,A,F,name,p,t1=SPLIT,**kw); e2,t2=run(idx,S,A,F,name,p,t0=SPLIT,**kw)
    a,b=stats(e1,t1),stats(e2,t2)
    out.append(dict(name=name,p=p,kw=kw,IS=a,OOS=b))
    print(f"{name:9}{str(p):22}{str(kw):32} IS {a['cagr']:7} dd{a['dd']:6} sh{a['sharpe']:6} n{a['n']:5} pf{a['pf']:5} | OOS {b['cagr']:7} dd{b['dd']:6} sh{b['sharpe']:6} n{b['n']:5} pf{b['pf']:5}  {time.time()-t:.0f}s",flush=True)
json.dump(out,open("grid.json","w"))
