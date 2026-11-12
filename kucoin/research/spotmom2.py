# Breites Spot-Universum: Momentum-Ausbruch mit Volumenspitze (Tageskerzen, alle KuCoin-USDT-Paare)
import pandas as pd, numpy as np, glob, os, itertools
D={}
for f in glob.glob("spotd/*.csv"):
    d=pd.read_csv(f)
    if len(d)<80: continue
    d.index=pd.to_datetime(d.t,unit="s"); D[os.path.basename(f)[:-9]]=d[["o","h","l","c","q"]].astype(float)
print(len(D),"Coins mit mind. 80 Tagen")
days=pd.date_range("2024-08-05","2026-11-12",freq="1D")
STABLE={"USDC","DAI","TUSD","FDUSD","USDD","USDE","PYUSD","EUR","USD1","USDP","PAXG","XAUT","BUSD","USDJ","GUSD","EURT","AEUR"}
def prep(n,vm,sma,minq):
    P={}
    for s,d in D.items():
        if s in STABLE or s.endswith("3L") or s.endswith("3S") or s.endswith("2L") or s.endswith("2S") or s.endswith("UP") or s.endswith("DOWN"): continue
        hh=d.h.rolling(n).max().shift(1); va=d.q.rolling(20).mean().shift(1); ma=d.c.rolling(sma).mean(); medq=d.q.rolling(20).median().shift(1)
        sig=(d.c>hh)&(d.q>vm*va)&(d.c>ma)&(medq>minq)&(d.c<d.o*1.5)
        P[s]=(d,sig)
    return P
def sim(P,hard=0.12,trailp=0.15,tp=None,size=100.0,maxpos=10,fee=0.001,slip=0.003,cooldown=2,t0=None,t1=None,eq0=1000.0):
    cash=eq0; pos={}; last={}; tr=[]; eq=[]
    for day in days:
        if t0 is not None and day<t0: continue
        if t1 is not None and day>=t1: break
        # Ausstiege
        for s in list(pos):
            d,_=P[s]
            if day not in d.index: continue
            b=d.loc[day]; p=pos[s]; stop=max(p["entry"]*(1-hard),p["peak"]*(1-trailp))
            px=None
            if b.l<=stop: px=min(b.o,stop)
            elif tp and b.h>=p["entry"]*(1+tp): px=p["entry"]*(1+tp)
            if px is not None:
                px*=1-slip; pnl=p["qty"]*(px-p["entry"])-p["qty"]*px*fee-p["fee"]; cash+=p["qty"]*px*(1-fee); tr.append(pnl/size); last[s]=day; del pos[s]
            else: p["peak"]=max(p["peak"],b.c)
        # Einstiege: Signal vom Vortag, Kauf zur Eroeffnung
        cands=[]
        for s,(d,sig) in P.items():
            if s in pos or day not in d.index: continue
            i=d.index.get_loc(day)
            if i<1 or not sig.iloc[i-1]: continue
            if s in last and (day-last[s]).days<cooldown: continue
            cands.append((d.q.iloc[i-1],s,d.o.iloc[i]))
        for q,s,o in sorted(cands,reverse=True):
            if len(pos)>=maxpos or cash<size*(1+fee): break
            px=o*(1+slip); qty=size/px; cash-=size*(1+fee); pos[s]={"entry":px,"qty":qty,"peak":px,"fee":size*fee}
        val=cash+sum(p["qty"]*(P[s][0].c.asof(day) if day>=P[s][0].index[0] else p["entry"]) for s,p in pos.items()); eq.append((day,val))
    e=pd.Series(dict(eq)); t=np.array(tr); yrs=(e.index[-1]-e.index[0]).days/365.25
    r=e.pct_change().dropna()
    return dict(cagr=round(((e.iloc[-1]/e.iloc[0])**(1/yrs)-1)*100,1),dd=round((e/e.cummax()-1).min()*100,1),sh=round(r.mean()/r.std()*np.sqrt(365),2) if r.std()>0 else 0,n=len(t),win=round((t>0).mean()*100,1) if len(t) else 0,avg=round(t.mean()*100,2) if len(t) else 0,end=round(e.iloc[-1]))
SPLIT=pd.Timestamp("2025-11-01")
for n,vm,sma,minq in [(20,3,50,5e5),(20,3,50,1e6),(20,3,50,2e6),(20,3,50,5e6),(20,2,50,1e6),(20,5,50,1e6),(55,3,50,1e6),(10,3,50,1e6),(20,3,100,1e6),(20,2,50,2e6),(55,2,50,2e6)]:
    P=prep(n,vm,sma,minq)
    nl=sum(1 for s_,(d,sig) in P.items() if (d.q.rolling(20).median().iloc[-1] or 0)>minq)
    for hard,trl,tp in [(0.12,0.15,None),(0.12,0.15,0.40)]:
        a=sim(P,hard,trl,tp,t1=SPLIT); b=sim(P,hard,trl,tp,t0=SPLIT); c=sim(P,hard,trl,tp)
        print(f"Hoch {n:2}T Vol x{vm} SMA{sma:3} min {minq/1e6:3.1f}M ({nl:3} Coins heute) TP {str(tp):4} | IS {a['cagr']:6} dd{a['dd']:6} n{a['n']:4} | OOS {b['cagr']:6} dd{b['dd']:6} n{b['n']:4} | ALL {c['cagr']:6} dd{c['dd']:6} sh{c['sh']:5} win{c['win']:5} avg{c['avg']:6}",flush=True)
