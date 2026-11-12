import sys,time,json
from bt import *
idx,S,A,F=load()
print(len(idx),idx[0],idx[-1],len(S),S,flush=True)
SPLIT=pd.Timestamp("2025-11-01")
G=[]
for n in (240,480,960,1320):
    for k in (8,12,16): G.append(("donchian",dict(n=n),dict(stop_k=k)))
for f,s in ((100,400),(200,800),(300,1200)):
    for k in (8,12,16): G.append(("ema",dict(f=f,s=s),dict(stop_k=k)))
for L in (336,720,1440):
    for thr in (0.0,0.1): G.append(("tsmom",dict(L=L,thr=thr),dict(stop_k=12)))
for L in (336,720,1440): G.append(("xsmom",dict(L=L,k=4),dict(stop_k=30,trail=False)))
out=[]
for name,p,kw in G:
    e1,t1=run(idx,S,A,F,name,p,t1=SPLIT,**kw); e2,t2=run(idx,S,A,F,name,p,t0=SPLIT,**kw); e3,t3=run(idx,S,A,F,name,p,**kw)
    a,b,c=stats(e1,t1),stats(e2,t2),stats(e3,t3)
    out.append(dict(name=name,p=p,kw=kw,IS=a,OOS=b,ALL=c))
    print(f"{name:9}{str(p):24}{str(kw):30} IS {a['cagr']:6} dd{a['dd']:6} sh{a['sharpe']:5} n{a['n']:4} pf{a['pf']:5} | OOS {b['cagr']:6} dd{b['dd']:6} sh{b['sharpe']:5} n{b['n']:4} pf{b['pf']:5} | ALL {c['cagr']:6} dd{c['dd']:6} sh{c['sharpe']:5}",flush=True)
json.dump(out,open("grid2.json","w"),default=float)
