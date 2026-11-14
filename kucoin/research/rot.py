# Eigene Strategie: Marktlage-Filter + Rotation in die staerksten liquiden Coins (Tageskerzen, nur Spot)
# Entwicklung NUR auf TRAIN (2019-2022). VAL (2023-08/2024) und FINAL (KuCoin 08/2024-11/2026) bleiben bis zur Pruefung zu.
import pandas as pd, numpy as np, glob, os, json, sys, itertools
FIAT={"EUR","GBP","AUD","BRL","TRY","RUB","UAH","NGN","IDRT","BIDR","BVND","DAI","USDC","BUSD","TUSD","PAX","USDP","FDUSD","UST","USTC","SUSD","USDS","USDSB","AEUR","EURI","PAXG","XAUT","WBTC","WBETH","BETH","XUSD","USD1","BFUSD","USDE","USDD","PYUSD","USDJ","GUSD","EURT"}
def panel(folder, unit):
    O={};C={};Q={}
    for f in glob.glob(f"{folder}/*.csv"):
        c=os.path.basename(f)[:-4].replace("-USDT","")
        if c in FIAT or c.endswith(("UP","DOWN","BULL","BEAR","3L","3S","2L","2S")): continue
        d=pd.read_csv(f)
        if len(d)<60: continue
        i=pd.to_datetime(d.t,unit=unit); O[c]=pd.Series(d.o.astype(float).values,i); C[c]=pd.Series(d.c.astype(float).values,i); Q[c]=pd.Series(d.q.astype(float).values,i)
    O=pd.DataFrame(O).sort_index(); C=pd.DataFrame(C).reindex(O.index); Q=pd.DataFrame(Q).reindex(O.index)
    idx=pd.date_range(O.index[0],O.index[-1],freq="1D")
    return O.reindex(idx),C.reindex(idx),Q.reindex(idx)
def signals(C,Q,K=20,ma=100,looks=(30,90),topn=5,coin_ma=50,min_hist=120):
    """Zielliste je Tag (Stand Tagesschluss). Marktlage: BTC ueber seinem gleitenden Schnitt."""
    btc=C["BTC"]; on=(btc>btc.rolling(ma).mean())
    medq=Q.rolling(30,min_periods=30).median()
    hist=C.notna().rolling(min_hist,min_periods=min_hist).sum()==min_hist      # lueckenlose Historie
    uni=medq.where(hist).rank(axis=1,ascending=False)<=K
    mom=sum(C/C.shift(L)-1 for L in looks)/len(looks)
    ok=uni&(mom>0)
    if coin_ma: ok&=C>C.rolling(coin_ma).mean()
    rk=mom.where(ok).rank(axis=1,ascending=False)
    return on, rk<=topn, rk
def simulate(O,C,on,pick,rk,topn=5,reb=7,fee=0.001,slip=0.002,t0=None,t1=None,eq0=1000.0,lev=1.0,keep=None):
    """Handel zur Eroeffnung des Folgetags. Bestehende Positionen bleiben, solange sie in der Zielliste (bzw. unter Rang `keep`) sind."""
    days=C.index; a=0 if t0 is None else days.searchsorted(t0); b=len(days) if t1 is None else days.searchsorted(t1)
    cash=eq0; pos={}; eq=[]; ntr=0; last_reb=-10**9; cost=fee+slip; turn=0.0; inv=0
    Ov=O.values; Cv=C.values; cols=list(C.columns); ci={c:i for i,c in enumerate(cols)}
    onv=on.values; pk=pick.values; rkv=rk.values
    for d in range(max(a,1),b):
        # geloeschte / ausgesetzte Coins: zum letzten Schlusskurs raus
        for c in list(pos):
            if np.isnan(Ov[d,ci[c]]):
                px=Cv[d-1,ci[c]]; px=px if not np.isnan(px) else pos[c][1]
                cash+=pos[c][0]*px*(1-cost); turn+=pos[c][0]*px; del pos[c]; ntr+=1
        risk_on=bool(onv[d-1])
        if not risk_on:
            for c in list(pos):
                px=Ov[d,ci[c]]; cash+=pos[c][0]*px*(1-cost); turn+=pos[c][0]*px; del pos[c]; ntr+=1
            last_reb=-10**9
        elif d-last_reb>=reb:
            last_reb=d
            target=[cols[i] for i in np.where(pk[d-1])[0] if not np.isnan(Ov[d,i])]
            target.sort(key=lambda c: rkv[d-1,ci[c]])
            stay=set(target)
            if keep:
                stay|={c for c in pos if rkv[d-1,ci[c]]<=keep}
            for c in list(pos):
                if c not in stay:
                    px=Ov[d,ci[c]]; cash+=pos[c][0]*px*(1-cost); turn+=pos[c][0]*px; del pos[c]; ntr+=1
            equity=cash+sum(q*Ov[d,ci[c]] for c,(q,_) in pos.items())
            for c in target:
                if c in pos or len(pos)>=topn: continue
                val=min(equity*lev/topn, cash/(1+cost)) if lev<=1 else equity*lev/topn
                if val<equity*0.02: continue
                px=Ov[d,ci[c]]; pos[c]=(val/px,px); cash-=val*(1+cost); turn+=val
        v=cash
        for c,(q,_) in pos.items():
            px=Cv[d,ci[c]]; v+=q*(px if not np.isnan(px) else Cv[d-1,ci[c]])
        eq.append(v); inv+=bool(pos)
        if v<=0: eq+= [0.0]*(b-d-1); break
    e=pd.Series(eq,index=days[max(a,1):max(a,1)+len(eq)])
    return e,dict(trades=ntr,turnover=turn,invested=inv/max(len(eq),1))
def stats(e):
    yrs=(e.index[-1]-e.index[0]).days/365.25; r=e.pct_change().dropna()
    cagr=(e.iloc[-1]/e.iloc[0])**(1/yrs)-1 if e.iloc[-1]>0 else -1
    return dict(cagr=round(cagr*100,1),dd=round((e/e.cummax()-1).min()*100,1),sharpe=round(r.mean()/r.std()*np.sqrt(365),2) if r.std()>0 else 0.0,end=round(e.iloc[-1]))
def yearly(e):
    y=e.resample("YE").last(); prev=np.r_[e.iloc[0],y.values[:-1]]
    return " ".join(f"{d.year}:{(v/p-1)*100:+.0f}%" for d,v,p in zip(y.index,y.values,prev))
TRAIN=(pd.Timestamp("2019-01-01"),pd.Timestamp("2023-01-01")); VAL=(pd.Timestamp("2023-01-01"),pd.Timestamp("2024-08-05"))
def bench(O,C,t0,t1,ma=None):
    c=C["BTC"][t0:t1]
    if ma is None: return c/c.iloc[0]*1000
    on=(C["BTC"]>C["BTC"].rolling(ma).mean()).shift(1).fillna(False)[t0:t1]
    r=c.pct_change().fillna(0)*on; sw=on.astype(int).diff().abs().fillna(0)*0.003
    return (1+r-sw).cumprod()*1000

# ---------------------------------------------------------------------------------------------
# Version 2: Marktlage als Stufen (mehrere Trendlaengen), taegliche Ausstiege, Groesse nach Schwankung
def prep2(C,Q,K=20,mas=(50,100,200),looks=(30,90),coin_ma=50,min_hist=120,vol_n=30):
    btc=C["BTC"]; frac=sum((btc>btc.rolling(m).mean()).astype(float) for m in mas)/len(mas)
    medq=Q.rolling(30,min_periods=30).median()
    hist=C.notna().rolling(min_hist,min_periods=min_hist).sum()==min_hist
    uni=medq.where(hist).rank(axis=1,ascending=False)<=K
    mom=sum(C/C.shift(L)-1 for L in looks)/len(looks)
    trend=(C>C.rolling(coin_ma).mean()) if coin_ma else pd.DataFrame(True,index=C.index,columns=C.columns)
    ok=uni&(mom>0)&trend
    rk=mom.where(ok).rank(axis=1,ascending=False)
    vol=np.log(C/C.shift(1)).rolling(vol_n).std()*np.sqrt(365)
    hold_ok=(mom>0)&trend&hist                      # zum Halten reicht: Trend intakt (Universum darf verlassen werden)
    return dict(frac=frac,rk=rk,vol=vol,hold_ok=hold_ok)
def sim2(O,C,P,topn=5,reb=7,vol_target=None,trail=None,daily_exit=True,fee=0.001,slip=0.002,t0=None,t1=None,eq0=1000.0,max_w=None,detail=False,slots=False,min_w=0.0):
    days=C.index; a=max(1,0 if t0 is None else days.searchsorted(t0)); b=len(days) if t1 is None else days.searchsorted(t1)
    cost=fee+slip; Ov=O.values; Cv=C.values; cols=list(C.columns); ci={c:i for i,c in enumerate(cols)}
    fr=P["frac"].values; rkv=P["rk"].values; volv=P["vol"].values; hk=P["hold_ok"].values
    cash=eq0; pos={}; eq=[]; ntr=0; turn=0.0; last_reb=-10**9; last_frac=None; expo=[]; log=[]
    max_w=max_w or 1.0/topn
    def sell(c,d,px,why):
        nonlocal cash,ntr,turn
        p=pos.pop(c); cash+=p["q"]*px*(1-cost); turn+=p["q"]*px; ntr+=1
        if detail: log.append((days[d],c,"sell",px,p["q"]*(px-p["e"]),why))
    for d in range(a,b):
        for c in list(pos):                                   # geloescht / ausgesetzt
            if np.isnan(Ov[d,ci[c]]):
                px=Cv[d-1,ci[c]]; sell(c,d,px if not np.isnan(px) else pos[c]["e"],"geloescht")
        f=fr[d-1]; f=0.0 if np.isnan(f) else f
        for c in list(pos):
            i=ci[c]; p=pos[c]
            if f==0: sell(c,d,Ov[d,i],"Marktlage"); continue
            if daily_exit and not hk[d-1,i]: sell(c,d,Ov[d,i],"Trend gebrochen"); continue
            if trail and Cv[d-1,i]<p["peak"]*(1-trail): sell(c,d,Ov[d,i],"Abstand zum Hoch"); continue
        do_reb=f>0 and (d-last_reb>=reb)
        nslots=topn if not slots else (0 if f==0 else max(1,int(round(f*topn))))
        if slots and len(pos)>nslots:                         # Marktlage schlechter: schwaechste Positionen abgeben
            for c in sorted(pos,key=lambda c:-(rkv[d-1,ci[c]] if not np.isnan(rkv[d-1,ci[c]]) else 1e9))[:len(pos)-nslots]: sell(c,d,Ov[d,ci[c]],"Marktlage")
        if do_reb or (last_frac is not None and f!=last_frac and f>0):
            equity=cash+sum(p["q"]*Ov[d,ci[c]] for c,p in pos.items())
            if do_reb:
                last_reb=d
                order=np.argsort(np.where(np.isnan(rkv[d-1]),1e9,rkv[d-1]))
                for i in order:
                    if len(pos)>=nslots or np.isnan(rkv[d-1,i]) or rkv[d-1,i]>topn: break
                    c=cols[i]
                    if c in pos or np.isnan(Ov[d,i]): continue
                    vs=1.0
                    if vol_target: vs=min(1.0,vol_target/volv[d-1,i]) if volv[d-1,i]>0 else 0.0
                    pos[c]=dict(q=0.0,e=Ov[d,i],peak=Ov[d,i],vs=vs)
            for c,p in pos.items():                           # auf Zielgroesse bringen (neu oder Marktlage geaendert)
                i=ci[c]; px=Ov[d,i]; tgt=equity*max(min_w,max_w*p["vs"]) if slots else equity*f*max_w*p["vs"]; cur=p["q"]*px
                if p["q"]==0 or (f!=last_frac and not slots):
                    dv=tgt-cur
                    if dv>0: dv=min(dv,max(cash,0)/(1+cost))
                    if abs(dv)<equity*0.01 and p["q"]>0: continue
                    cash-=dv+abs(dv)*cost; p["q"]+=dv/px; turn+=abs(dv); ntr+=p["q"]>0 and cur==0
                    if detail and cur==0: log.append((days[d],c,"buy",px,0.0,""))
            for c in [c for c,p in pos.items() if p["q"]<=0]: pos.pop(c)
        last_frac=f
        v=cash
        for c,p in pos.items():
            px=Cv[d,ci[c]]; px=px if not np.isnan(px) else Cv[d-1,ci[c]]; v+=p["q"]*px; p["peak"]=max(p["peak"],px)
        eq.append(v); expo.append((v-cash)/v if v>0 else 0)
    e=pd.Series(eq,index=days[a:a+len(eq)])
    m=dict(trades=int(ntr),turnover=round(turn),avg_expo=round(float(np.mean(expo)),2))
    return (e,m,log) if detail else (e,m)
