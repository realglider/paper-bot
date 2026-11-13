# Ausstiegsvarianten fuer den Spot-Teil: Teilgewinne, Stop auf Einstand, Gewinner laufen lassen
import pandas as pd, numpy as np, glob, os
D={}
for f in glob.glob("spotd/*.csv"):
    d=pd.read_csv(f)
    if len(d)<80: continue
    d.index=pd.to_datetime(d.t,unit="s"); D[os.path.basename(f)[:-9]]=d[["o","h","l","c","q"]].astype(float)
days=pd.date_range("2024-08-05","2026-11-12",freq="1D")
STABLE={"USDC","DAI","TUSD","FDUSD","USDD","USDE","PYUSD","EUR","USD1","USDP","PAXG","XAUT","BUSD","USDJ","GUSD","EURT","AEUR"}
def prep(n=20,vm=3,sma=50,minq=1e6):
    P={}
    for s,d in D.items():
        if s in STABLE or s.endswith(("3L","3S","2L","2S","UP","DOWN")): continue
        hh=d.h.rolling(n).max().shift(1); va=d.q.rolling(20).mean().shift(1); ma=d.c.rolling(sma).mean(); medq=d.q.rolling(20).median().shift(1)
        sig=((d.c>hh)&(d.q>vm*va)&(d.c>ma)&(medq>minq)&(d.c<d.o*1.5)).values
        P[s]=dict(idx={t:i for i,t in enumerate(d.index)},o=d.o.values,h=d.h.values,l=d.l.values,c=d.c.values,q=d.q.values,sig=sig)
    return P
def sim(P,hard=0.12,trail=0.15,tp=None,partials=(),be_at=None,be_lock=0.0,trail2=None,size=100.0,maxpos=5,fee=0.001,slip=0.003,cooldown=2,t0=None,t1=None,eq0=500.0):
    """partials: ((Schwelle, Anteil), ...) vom urspruenglichen Bestand; be_at: ab diesem Gewinn Stop auf Einstand+be_lock;
       trail2: Abstand des nachgezogenen Stops, sobald der erste Teilgewinn/BE erreicht ist."""
    cash=eq0; pos={}; last={}; tr=[]; eq=[]
    for day in days:
        if t0 is not None and day<t0: continue
        if t1 is not None and day>=t1: break
        for s in list(pos):
            X=P[s]; i=X["idx"].get(day)
            if i is None: continue
            p=pos[s]; o,h,l,c=X["o"][i],X["h"][i],X["l"][i],X["c"][i]
            tr_w=trail2 if (trail2 and p["armed"]) else trail
            stop=max(p["entry"]*(1-hard),p["peak"]*(1-tr_w))
            if p["be"]: stop=max(stop,p["entry"]*(1+be_lock))
            if l<=stop:                                   # Stop zuerst (vorsichtige Annahme)
                px=min(o,stop)*(1-slip); cash+=p["qty"]*px*(1-fee); p["pnl"]+=p["qty"]*(px-p["entry"])-p["qty"]*px*fee
                tr.append(p["pnl"]/size); last[s]=day; del pos[s]; continue
            done=False
            for k,(lvl,frac) in enumerate(partials):
                if k>=p["np"] and h>=p["entry"]*(1+lvl):
                    px=max(o,p["entry"]*(1+lvl)); q=min(p["qty"],p["qty0"]*frac); cash+=q*px*(1-fee); p["pnl"]+=q*(px-p["entry"])-q*px*fee; p["qty"]-=q; p["np"]=k+1; p["armed"]=True
            if tp and h>=p["entry"]*(1+tp):
                px=max(o,p["entry"]*(1+tp)); cash+=p["qty"]*px*(1-fee); p["pnl"]+=p["qty"]*(px-p["entry"])-p["qty"]*px*fee; done=True
            if done or p["qty"]<=p["qty0"]*1e-6:
                tr.append(p["pnl"]/size); last[s]=day; del pos[s]; continue
            if be_at is not None and h>=p["entry"]*(1+be_at): p["be"]=True; p["armed"]=True
            p["peak"]=max(p["peak"],c)
        cands=[]
        for s,X in P.items():
            if s in pos: continue
            i=X["idx"].get(day)
            if i is None or i<1 or not X["sig"][i-1]: continue
            if s in last and (day-last[s]).days<cooldown: continue
            cands.append((X["q"][i-1],s,X["o"][i]))
        for q,s,o in sorted(cands,reverse=True):
            if len(pos)>=maxpos or cash<size*(1+fee): break
            px=o*(1+slip); qty=size/px; cash-=size*(1+fee)
            pos[s]=dict(entry=px,qty=qty,qty0=qty,peak=px,pnl=-size*fee,np=0,be=False,armed=False)
        val=cash
        for s,p in pos.items():
            X=P[s]; i=X["idx"].get(day); val+=p["qty"]*(X["c"][i] if i is not None else p["entry"])
        eq.append((day,val))
    e=pd.Series(dict(eq)); t=np.array(tr); yrs=(e.index[-1]-e.index[0]).days/365.25; r=e.pct_change().dropna()
    return dict(cagr=round(((e.iloc[-1]/e.iloc[0])**(1/yrs)-1)*100,1),dd=round((e/e.cummax()-1).min()*100,1),sh=round(r.mean()/r.std()*np.sqrt(365),2) if r.std()>0 else 0,n=len(t),win=round((t>0).mean()*100,1) if len(t) else 0,avg=round(t.mean()*100,2) if len(t) else 0,end=round(e.iloc[-1])),e,t
SPLIT=pd.Timestamp("2025-11-01")
V=[("Basis: Stop 12, Trail 15, Ziel +40",dict(tp=0.40)),
   ("ohne Ziel, nur Trail 15",dict()),
   ("ohne Ziel, Trail 20",dict(trail=0.20)),
   ("ohne Ziel, Trail 25",dict(trail=0.25)),
   ("Einstand ab +10 %",dict(be_at=0.10,be_lock=0.005)),
   ("Einstand ab +15 %",dict(be_at=0.15,be_lock=0.005)),
   ("Einstand ab +20 %, +2 % sichern",dict(be_at=0.20,be_lock=0.02)),
   ("1/2 bei +20, Rest Trail 15",dict(partials=((0.20,0.5),))),
   ("1/2 bei +20, Einstand, Rest Trail 20",dict(partials=((0.20,0.5),),be_at=0.20,be_lock=0.005,trail2=0.20)),
   ("1/2 bei +20, Einstand, Rest Trail 25",dict(partials=((0.20,0.5),),be_at=0.20,be_lock=0.005,trail2=0.25)),
   ("1/3 +15, 1/3 +30, Einstand, Rest Trail 25",dict(partials=((0.15,1/3),(0.30,1/3)),be_at=0.15,be_lock=0.005,trail2=0.25)),
   ("1/2 bei +40, Einstand, Rest Trail 25",dict(partials=((0.40,0.5),),be_at=0.40,be_lock=0.005,trail2=0.25)),
   ("1/4 bei +25, Einstand ab +15, Rest Trail 25",dict(partials=((0.25,0.25),),be_at=0.15,be_lock=0.005,trail2=0.25)),
   ("Einstand ab +15, danach Trail 25",dict(be_at=0.15,be_lock=0.005,trail2=0.25)),
   ("Einstand ab +15, danach Trail 30",dict(be_at=0.15,be_lock=0.005,trail2=0.30)),
   ("Stop 8, Trail 15, Ziel +40",dict(hard=0.08,tp=0.40)),
   ("Stop 15, Trail 20, ohne Ziel",dict(hard=0.15,trail=0.20))]
if __name__=="__main__":
    for mp in (5,10):
        P=prep(); print(f"--- max {mp} Positionen, Konto {mp*100}")
        for tag,kw in V:
            a=sim(P,maxpos=mp,eq0=100.0*mp,t1=SPLIT,**kw)[0]; b=sim(P,maxpos=mp,eq0=100.0*mp,t0=SPLIT,**kw)[0]; c=sim(P,maxpos=mp,eq0=100.0*mp,**kw)[0]
            print(f"{tag:44} IS {a['cagr']:6} dd{a['dd']:6} | OOS {b['cagr']:6} dd{b['dd']:6} | ALL {c['cagr']:6} dd{c['dd']:6} sh{c['sh']:5} n{c['n']:4} win{c['win']:5} avg{c['avg']:6}",flush=True)
