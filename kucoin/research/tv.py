# Bekannte TradingView-Strategien, nachgebaut und auf 16 KuCoin-Perpetuals getestet
from bt import *
import bt
idx,S,A,F=load()
OLD="XBT ETH SOL XRP DOGE BNB ADA LINK AVAX LTC BCH DOT NEAR AAVE UNI TRX".split()
old=[S.index(s) for s in OLD]; SPLIT=pd.Timestamp("2025-11-01")

def resample(tf):
    out={}
    for k,fn in (("o","first"),("h","max"),("l","min"),("c","last")):
        out[k]=pd.DataFrame(A[k],index=idx).resample(f"{tf}h").agg(fn)
    return out
def back(sig,tf):
    """Signal der fertigen tf-Kerze steht ab deren letzter Stunde zur Verfuegung."""
    s=sig.copy(); s.index=s.index+pd.Timedelta(hours=tf-1)
    return s.reindex(idx,method="ffill").fillna(False).values.astype(bool)
def atr_df(R,n):
    c1=R["c"].shift(1); tr=np.maximum(R["h"]-R["l"],np.maximum((R["h"]-c1).abs(),(R["l"]-c1).abs()))
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

def supertrend_dir(R,n=10,m=3.0):
    a=atr_df(R,n).values; h,l,c=R["h"].values,R["l"].values,R["c"].values
    mid=(h+l)/2; ub=mid+m*a; lb=mid-m*a; d=np.ones_like(c); fu=ub.copy(); fl=lb.copy()
    for t in range(1,len(c)):
        fu[t]=np.where(np.isnan(fu[t-1])|(ub[t]<fu[t-1])|(c[t-1]>fu[t-1]),ub[t],fu[t-1]); fl[t]=np.where(np.isnan(fl[t-1])|(lb[t]>fl[t-1])|(c[t-1]<fl[t-1]),lb[t],fl[t-1])
        d[t]=np.where(c[t]>fu[t-1],1,np.where(c[t]<fl[t-1],-1,d[t-1]))
    d[np.isnan(a)]=0
    return pd.DataFrame(d,index=R["c"].index)
def utbot_dir(R,key=2.0,n=10):
    a=(key*atr_df(R,n)).values; c=R["c"].values; ts=np.zeros_like(c); d=np.zeros_like(c)
    for t in range(1,len(c)):
        p=ts[t-1]; up=(c[t]>p)&(c[t-1]>p); dn=(c[t]<p)&(c[t-1]<p)
        ts[t]=np.where(up,np.maximum(p,c[t]-a[t]),np.where(dn,np.minimum(p,c[t]+a[t]),np.where(c[t]>p,c[t]-a[t],c[t]+a[t])))
        ts[t]=np.where(np.isnan(ts[t]),0,ts[t]); d[t]=np.where(c[t]>ts[t],1,-1)
    d[np.isnan(a)]=0
    return pd.DataFrame(d,index=R["c"].index)
def chandelier_dir(R,n=22,m=3.0):
    a=(m*atr_df(R,n)).values; c=R["c"].values
    ls=(R["c"].rolling(n).max().values-a); ss=(R["c"].rolling(n).min().values+a); d=np.ones_like(c)
    for t in range(1,len(c)):
        ls[t]=np.where(c[t-1]>ls[t-1],np.fmax(ls[t],ls[t-1]),ls[t]); ss[t]=np.where(c[t-1]<ss[t-1],np.fmin(ss[t],ss[t-1]),ss[t])
        d[t]=np.where(c[t]>ss[t-1],1,np.where(c[t]<ls[t-1],-1,d[t-1]))
    d[np.isnan(a)]=0
    return pd.DataFrame(d,index=R["c"].index)
def mk_dir(fn,tf,**p):
    def sig(A_,idx_,**k):
        d=fn(resample(tf),**p); up=back(d>0,tf); dn=back(d<0,tf)
        return up,dn,dn,up
    return sig
def squeeze(tf,n=20,bbm=2.0,kcm=1.5):
    def sig(A_,idx_,**k):
        R=resample(tf); c=R["c"]; ma=c.rolling(n).mean(); sd=c.rolling(n).std(ddof=0)
        c1=c.shift(1); tr=np.maximum(R["h"]-R["l"],np.maximum((R["h"]-c1).abs(),(R["l"]-c1).abs())); rng=tr.rolling(n).mean()
        on=((ma-bbm*sd)>(ma-kcm*rng))&((ma+bbm*sd)<(ma+kcm*rng))
        base=c-((R["h"].rolling(n).max()+R["l"].rolling(n).min())/2+ma)/2
        x=np.arange(n); xm=x.mean(); w=(x-xm)/((x-xm)**2).sum()
        mom=base.rolling(n).apply(lambda v:(v.mean()+(w*v).sum()*(n-1-xm)),raw=True)
        fired=(~on)&(on.shift(1).rolling(3).max()>0)        # Squeeze hat sich gerade geloest
        up=mom>mom.shift(1)
        return back(fired&(mom>0)&up,tf),back(fired&(mom<0)&(~up),tf),back((mom<mom.shift(1)),tf),back((mom>mom.shift(1)),tf)
    return sig
def macd(tf,f=12,s=26,g=9,trend=200):
    def sig(A_,idx_,**k):
        c=resample(tf)["c"]; m=c.ewm(span=f,adjust=False).mean()-c.ewm(span=s,adjust=False).mean(); sg=m.ewm(span=g,adjust=False).mean(); e=c.ewm(span=trend,adjust=False,min_periods=trend).mean()
        return back((m>sg)&(c>e),tf),back((m<sg)&(c<e),tf),back(m<sg,tf),back(m>sg,tf)
    return sig
def line(tag,name,**kw):
    kw={**dict(stop_k=12,risk=0.005,cols=old,trail=False),**kw}
    a,b,c=[stats(*run(idx,S,A,F,name,{},**w,**kw)) for w in (dict(t1=SPLIT),dict(t0=SPLIT),dict())]
    print(f"{tag:34} IS {a['cagr']:6} dd{a['dd']:6} pf{a['pf']:5} n{a['n']:4} | OOS {b['cagr']:6} dd{b['dd']:6} pf{b['pf']:5} n{b['n']:4} | ALL {c['cagr']:6} dd{c['dd']:6} sh{c['sharpe']:5} win{c['win']:5}",flush=True)
T=[]
for tf in (4,24):
    T+= [(f"SuperTrend(10,3) {tf}h",mk_dir(supertrend_dir,tf)),(f"SuperTrend(10,4) {tf}h",mk_dir(supertrend_dir,tf,m=4.0)),
         (f"UT Bot (2, ATR10) {tf}h",mk_dir(utbot_dir,tf)),(f"UT Bot (3, ATR10) {tf}h",mk_dir(utbot_dir,tf,key=3.0)),
         (f"Chandelier Exit(22,3) {tf}h",mk_dir(chandelier_dir,tf)),
         (f"Squeeze Momentum {tf}h",squeeze(tf)),(f"MACD + EMA200 {tf}h",macd(tf))]
SIG["donchian0"]=lambda A_,idx_,**k: SIG["donchian"](A_,idx_,n=960)
line("Vergleich: 40-Tage-Ausbruch","donchian0",trail=True)
for tag,fn in T:
    SIG["x"]=fn; line(tag,"x")
