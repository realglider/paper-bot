# Fibonacci-0,882-Ruecklauf und Liquidity-Sweep + Order-Block als feste Regeln (1h-Kerzen)
# Ergebnis je Trade in R (Vielfache des Anfangsrisikos), nach Kosten. Entwicklung nur auf TRAIN.
import pandas as pd, numpy as np, glob, os, sys
COST=2*(0.0006+0.0002)          # Gebuehr + Slippage, Hin- und Rueckweg, als Anteil am Positionswert
TRAIN=(pd.Timestamp("2019-01-01"),pd.Timestamp("2023-01-01")); VAL=(pd.Timestamp("2023-01-01"),pd.Timestamp("2024-08-05"))
def load(folder,unit="ms",suffix=".csv"):
    out={}
    for f in sorted(glob.glob(f"{folder}/*{suffix}")):
        c=os.path.basename(f)[:-len(suffix)]
        d=pd.read_csv(f); d.index=pd.to_datetime(d.t,unit=unit); d=d[~d.index.duplicated()]
        out[c]=d[["o","h","l","c"]].astype(float)
    return out
def pivots(h,l,N):
    """Bestaetigte Wendepunkte: Hoch/Tief, das N Kerzen links und rechts nicht uebertroffen wird (bekannt ab i+N)."""
    n=len(h); ph=np.zeros(n,bool); pl=np.zeros(n,bool)
    H=pd.Series(h).rolling(2*N+1,center=True).max().values; L=pd.Series(l).rolling(2*N+1,center=True).min().values
    ph[(h==H)]=True; pl[(l==L)]=True
    return ph,pl
def atr(h,l,c,n=24):
    c1=np.r_[c[0],c[:-1]]; tr=np.maximum(h-l,np.maximum(abs(h-c1),abs(l-c1)))
    return pd.Series(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().values
def resolve(side,entry,stop,target,h,l,o,i0,max_hold=240):
    """Ab Kerze i0 (Einstiegskerze) Stop/Ziel pruefen; in derselben Kerze zaehlt der Stop zuerst. -> (R vor Kosten, Ausstiegsindex)"""
    risk=abs(entry-stop)
    for j in range(i0,min(len(h),i0+max_hold)):
        if side>0:
            if l[j]<=stop: return -1.0 if o[j]>stop or j==i0 else (o[j]-entry)/risk, j
            if h[j]>=target and j>i0: return (target-entry)/risk, j
        else:
            if h[j]>=stop: return -1.0 if o[j]<stop or j==i0 else (entry-o[j])/risk, j
            if l[j]<=target and j>i0: return (entry-target)/risk, j
    j=min(len(h)-1,i0+max_hold-1); return side*(h[j]*0+ (0.5*(h[j]+l[j])) -entry)/risk, j
def fib(d,N=10,level=0.882,tgt=0.0,buf=0.05,min_leg=4.0,expiry=72,trend=0,sides=(1,-1)):
    """Nach einem Schwung Tief->Hoch (>= min_leg x ATR): Limit-Kauf beim Ruecklauf auf `level`, Stop bei 1+buf, Ziel bei `tgt` (0 = altes Hoch)."""
    o,h,l,c=(d[k].values for k in "ohlc"); n=len(c); ph,pl=pivots(h,l,N); a=atr(h,l,c)
    ema=pd.Series(c).ewm(span=trend,adjust=False).mean().values if trend else None
    out=[]; lastL=lastH=None; pend=None; busy_until=-1
    for i in range(N,n):
        k=i-N                                   # an Kerze i wird der Wendepunkt bei k bekannt
        newleg=None
        if pl[k]:
            if lastH is not None and lastH[0]<k and (lastH[1]-l[k])>=min_leg*a[k] : newleg=(-1,lastH,(k,l[k]))
            lastL=(k,l[k])
        if ph[k]:
            if lastL is not None and lastL[0]<k and (h[k]-lastL[1])>=min_leg*a[k]: newleg=(1,lastL,(k,h[k]))
            lastH=(k,h[k])
        if newleg and newleg[0] in sides:
            s,A,B=newleg; leg=abs(B[1]-A[1])
            entry=B[1]-s*level*leg; stop=A[1]-s*buf*leg; target=B[1]-s*tgt*leg
            # nur gueltig, wenn der Kurs seit dem Wendepunkt den Einstieg noch nicht beruehrt hat
            seg_l=l[k+1:i+1].min() if i>k else np.inf; seg_h=h[k+1:i+1].max() if i>k else -np.inf
            if (s>0 and seg_l>entry and seg_h<=B[1]) or (s<0 and seg_h<entry and seg_l>=B[1]):
                pend=dict(s=s,entry=entry,stop=stop,target=target,until=i+expiry,ext=B[1]); 
            continue
        if pend and i>busy_until:
            p=pend
            if i>p["until"] or (p["s"]>0 and h[i]>p["ext"]) or (p["s"]<0 and l[i]<p["ext"]): pend=None; continue
            if trend and ((p["s"]>0 and c[i-1]<ema[i-1]) or (p["s"]<0 and c[i-1]>ema[i-1])): continue
            if (p["s"]>0 and l[i]<=p["entry"]) or (p["s"]<0 and h[i]>=p["entry"]):
                e=min(o[i],p["entry"]) if p["s"]>0 else max(o[i],p["entry"])
                R,j=resolve(p["s"],e,p["stop"],p["target"],h,l,o,i); risk=abs(e-p["stop"])
                out.append((d.index[i],p["s"],R-COST*e/risk,risk/e)); busy_until=j; pend=None
    return out
def lsob(d,N=10,rr=2.0,W=12,K=5,expiry=48,buf=0.1,trend=0,entry_mode="open",sides=(1,-1),max_risk=0.05):
    """Sweep: Tief unter dem letzten bestaetigten Wendetief, Schluss wieder darueber. Dreh: Schluss ueber dem Hoch der K Kerzen vor dem Sweep
       (binnen W Kerzen). Order Block: letzte Abwaertskerze vor dem Dreh. Limit am Block, Stop unter dem Sweep-Tief, Ziel rr x Risiko."""
    o,h,l,c=(d[k].values for k in "ohlc"); n=len(c); ph,pl=pivots(h,l,N); a=atr(h,l,c)
    ema=pd.Series(c).ewm(span=trend,adjust=False).mean().values if trend else None
    out=[]; lastL=lastH=None; sw=None; pend=None; busy_until=-1
    for i in range(N+K,n):
        k=i-N
        if pl[k]: lastL=(k,l[k])
        if ph[k]: lastH=(k,h[k])
        if i<=busy_until: continue
        # Sweep erkennen
        if lastL and l[i]<lastL[1] and c[i]>lastL[1] and 1 in sides:
            sw=dict(s=1,i=i,ext=l[i],lvl=h[i-K:i].max()); lastL=None
        elif lastH and h[i]>lastH[1] and c[i]<lastH[1] and -1 in sides:
            sw=dict(s=-1,i=i,ext=h[i],lvl=l[i-K:i].min()); lastH=None
        if sw and i>sw["i"]:
            s=sw["s"]
            if i-sw["i"]>W or (s>0 and l[i]<sw["ext"]) or (s<0 and h[i]>sw["ext"]): sw=None
            elif (s>0 and c[i]>sw["lvl"]) or (s<0 and c[i]<sw["lvl"]):
                ob=None
                for j in range(i-1,sw["i"]-1,-1):               # letzte Gegenkerze vor dem Dreh
                    if (s>0 and c[j]<o[j]) or (s<0 and c[j]>o[j]): ob=j; break
                if ob is not None:
                    entry={"open":o[ob],"mid":(o[ob]+c[ob])/2,"edge":(h[ob] if s>0 else l[ob])}[entry_mode]
                    stop=sw["ext"]-s*buf*a[i]; risk=abs(entry-stop)
                    if risk>0 and risk/entry<=max_risk and ((s>0 and c[i]>entry) or (s<0 and c[i]<entry)):
                        pend=dict(s=s,entry=entry,stop=stop,target=entry+s*rr*risk,until=i+expiry)
                sw=None
            continue
        if pend:
            p=pend
            if i>p["until"] or (p["s"]>0 and h[i]>=p["target"]) or (p["s"]<0 and l[i]<=p["target"]): pend=None; continue
            if trend and ((p["s"]>0 and c[i-1]<ema[i-1]) or (p["s"]<0 and c[i-1]>ema[i-1])): continue
            if (p["s"]>0 and l[i]<=p["entry"]) or (p["s"]<0 and h[i]>=p["entry"]):
                e=min(o[i],p["entry"]) if p["s"]>0 else max(o[i],p["entry"])
                R,j=resolve(p["s"],e,p["stop"],p["target"],h,l,o,i); risk=abs(e-p["stop"])
                out.append((d.index[i],p["s"],R-COST*e/risk,risk/e)); busy_until=j; pend=None
    return out
def summary(tr,t0,t1):
    T=pd.DataFrame(tr,columns=["t","side","R","risk"]); T=T[(T.t>=t0)&(T.t<t1)]
    if not len(T): return dict(n=0)
    yrs=(t1-t0).days/365.25; R=T.R
    by=" ".join(f"{y}:{x.sum():+.0f}R" for y,x in R.groupby(T.t.dt.year))
    return dict(n=len(T),win=round((R>0).mean()*100,1),avgR=round(R.mean(),3),sumR=round(R.sum(),1),perYear=round(R.sum()/yrs,1),long=round(T[T.side>0].R.mean(),3) if (T.side>0).any() else None,short=round(T[T.side<0].R.mean(),3) if (T.side<0).any() else None,risk=round(T.risk.median()*100,2),by=by)
def run_all(D,fn,t0,t1,**kw):
    tr=[]
    for c,d in D.items():
        d=d[(d.index>=t0-pd.Timedelta(days=30))&(d.index<t1)]
        if len(d)<500: continue
        tr+=fn(d,**kw)
    return summary(tr,t0,t1),tr
