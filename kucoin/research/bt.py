import numpy as np, pandas as pd, glob, os, sys, itertools, json
FEE=0.0006; SLIP=0.0004   # je Seite: Taker-Gebuehr + Slippage
def load():
    px={}; fu={}
    for f in sorted(glob.glob("data/*_fund.csv")):
        f=f.replace("_fund","_1h")
        s=os.path.basename(f).split("_")[0]
        d=pd.read_csv(f); d.index=pd.to_datetime(d.t,unit="ms"); px[s]=d[["o","h","l","c","v"]]
        g=pd.read_csv(f"data/{s}_fund.csv"); g.index=pd.to_datetime(g.t,unit="ms"); fu[s]=g.rate
    idx=pd.date_range(min(d.index[0] for d in px.values()),max(d.index[-1] for d in px.values()),freq="1h")
    S=list(px)
    A={k:np.column_stack([px[s][k].reindex(idx).values for s in S]) for k in "ohlc"}
    F=np.column_stack([fu[s].reindex(idx).fillna(0).values for s in S])
    return idx,S,A,F
def roll(x,n,fn):
    return getattr(pd.DataFrame(x).rolling(n,min_periods=n),fn)().values
def ema(x,n): return pd.DataFrame(x).ewm(span=n,adjust=False,min_periods=n).mean().values
def atr(A,n):
    c1=np.vstack([A["c"][:1],A["c"][:-1]])
    tr=np.maximum(A["h"]-A["l"],np.maximum(abs(A["h"]-c1),abs(A["l"]-c1)))
    return pd.DataFrame(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().values
def rsi(c,n):
    d=np.diff(c,axis=0,prepend=c[:1]); up=pd.DataFrame(np.maximum(d,0)).ewm(alpha=1/n,adjust=False,min_periods=n).mean().values
    dn=pd.DataFrame(np.maximum(-d,0)).ewm(alpha=1/n,adjust=False,min_periods=n).mean().values
    return 100-100/(1+up/np.where(dn==0,np.nan,dn))

# ---- Signale: liefern el,es (Einstieg long/short), xl,xs (Ausstieg) als bool [T,S], berechnet am Schluss von t
def sig_donchian(A,idx,n=220,m=None,**k):
    m=m or n//2; c=A["c"]
    hh=np.vstack([np.full((1,c.shape[1]),np.nan),roll(A["h"],n,"max")[:-1]]); ll=np.vstack([np.full((1,c.shape[1]),np.nan),roll(A["l"],n,"min")[:-1]])
    xh=np.vstack([np.full((1,c.shape[1]),np.nan),roll(A["h"],m,"max")[:-1]]); xl_=np.vstack([np.full((1,c.shape[1]),np.nan),roll(A["l"],m,"min")[:-1]])
    return c>hh, c<ll, c<xl_, c>xh
def sig_ema(A,idx,f=80,s=400,**k):
    c=A["c"]; a=ema(c,f); b=ema(c,s)
    return (a>b)&(c>b),(a<b)&(c<b),a<b,a>b
def sig_tsmom(A,idx,L=336,thr=0.0,**k):
    c=A["c"]; r=c/np.vstack([np.full((L,c.shape[1]),np.nan),c[:-L]])-1
    day=(idx.hour==0)[:,None]
    return (r>thr)&day,(r<-thr)&day,(r<0)&day,(r>0)&day
def sig_xsmom(A,idx,L=336,k=4,**kw):
    c=A["c"]; r=c/np.vstack([np.full((L,c.shape[1]),np.nan),c[:-L]])-1
    rk=pd.DataFrame(r).rank(axis=1,ascending=False).values; n=np.sum(~np.isnan(r),axis=1)[:,None]
    day=((idx.hour==0)&(idx.dayofweek==0))[:,None]
    top=rk<=k; bot=rk>n-k
    return top&day, bot&day, (~top)&day, (~bot)&day
def sig_rsi_mr(A,idx,n=14,lo=25,hi=75,trend=800,**k):
    c=A["c"]; r=rsi(c,n); t=ema(c,trend)
    return (r<lo)&(c>t),(r>hi)&(c<t),r>50,r<50
def sig_bb_mr(A,idx,n=96,z=2.5,**k):
    c=A["c"]; m=roll(c,n,"mean"); s=roll(c,n,"std")
    return c<m-z*s, c>m+z*s, c>m, c<m
SIG={"donchian":sig_donchian,"ema":sig_ema,"tsmom":sig_tsmom,"xsmom":sig_xsmom,"rsi_mr":sig_rsi_mr,"bb_mr":sig_bb_mr}

def run(idx,S,A,F,name,p,risk=0.01,stop_k=3.0,trail=True,atr_n=96,max_pos=1.5,max_gross=3.0,side="both",min_notional=0.0,frac=0.0,max_risk=1.0,eq0=1000.0,t0=None,t1=None,cols=None):
    el,es,xl,xs=SIG[name](A,idx,**p)
    at=atr(A,atr_n); o,h,l,c=A["o"],A["h"],A["l"],A["c"]
    T,N=c.shape; cols=range(N) if cols is None else cols
    blk=np.zeros(N); qty=np.zeros(N); stop=np.zeros(N); ent=np.zeros(N); cash=eq0; eq=np.full(T,np.nan); trades=[]
    a=0 if t0 is None else int(np.searchsorted(idx,t0)); b=T if t1 is None else int(np.searchsorted(idx,t1))
    lastc=np.where(np.isnan(c[a]),0,c[a]) if a<T else np.zeros(N)
    equity=eq0
    for t in range(max(a,1),b):
        for i in cols:
            if np.isnan(o[t,i]): continue
            q=qty[i]
            # 1) Signal vom Vorbalken -> Ausfuehrung zur Eroeffnung
            if q!=0 and ((q>0 and xl[t-1,i]) or (q<0 and xs[t-1,i])):
                px=o[t,i]*(1-SLIP*np.sign(q)); pnl=q*(px-ent[i])-abs(q)*px*FEE
                cash+=pnl; trades.append(pnl); qty[i]=q=0
            if blk[i]==1 and not el[t-1,i]: blk[i]=0
            if blk[i]==-1 and not es[t-1,i]: blk[i]=0
            if q==0 and not np.isnan(at[t-1,i]):
                d=1 if (el[t-1,i] and side!="short") else (-1 if (es[t-1,i] and side!="long") else 0)
                if d and d!=blk[i]:
                    dist=stop_k*at[t-1,i]; px=o[t,i]*(1+SLIP*d)
                    notional=min(max(equity*risk/(dist/px),min_notional,equity*frac), equity*max_pos)
                    gross=np.nansum(np.abs(qty)*lastc)
                    if gross+notional<=equity*max_gross and notional>5 and notional*dist/px<=equity*max_risk:
                        qty[i]=q=d*notional/px; ent[i]=px; stop[i]=px-d*dist; cash-=notional*FEE
            # 2) Stop innerhalb des Balkens
            if q>0 and l[t,i]<=stop[i]:
                px=min(o[t,i],stop[i])*(1-SLIP); pnl=q*(px-ent[i])-q*px*FEE; cash+=pnl; trades.append(pnl); qty[i]=q=0; blk[i]=1
            elif q<0 and h[t,i]>=stop[i]:
                px=max(o[t,i],stop[i])*(1+SLIP); pnl=q*(px-ent[i])-abs(q)*px*FEE; cash+=pnl; trades.append(pnl); qty[i]=q=0; blk[i]=-1
            # 3) Funding, Stop nachziehen
            if q!=0:
                if F[t,i]!=0: cash-=q*c[t,i]*F[t,i]
                if trail and not np.isnan(at[t,i]):
                    stop[i]=max(stop[i],c[t,i]-stop_k*at[t,i]) if q>0 else min(stop[i],c[t,i]+stop_k*at[t,i])
            lastc[i]=c[t,i]
        equity=cash+np.sum(qty*(lastc-ent)); eq[t]=equity
        if equity<=0: eq[t:]=0; break
    return pd.Series(eq[max(a,1):b],index=idx[max(a,1):b]).dropna(),np.array(trades)

def stats(eq,tr):
    d=eq.resample("1D").last().dropna(); r=d.pct_change().dropna()
    yrs=(d.index[-1]-d.index[0]).days/365.25
    cagr=(d.iloc[-1]/d.iloc[0])**(1/yrs)-1 if d.iloc[-1]>0 else -1
    dd=(d/d.cummax()-1).min(); sh=r.mean()/r.std()*np.sqrt(365) if r.std()>0 else 0
    pf=tr[tr>0].sum()/-tr[tr<0].sum() if (tr<0).any() else np.inf
    return dict(cagr=round(cagr*100,1),dd=round(dd*100,1),sharpe=round(sh,2),n=len(tr),win=round((tr>0).mean()*100,1) if len(tr) else 0,pf=round(pf,2),end=round(d.iloc[-1],0))
