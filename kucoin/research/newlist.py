# Neu-Listing-Short: neu gelistete Coins nach einigen Tagen leerverkaufen (Tageskerzen)
# TRAIN/VAL: Binance-Spot inkl. geloeschter Coins (Kurs als Stellvertreter fuer den Future). FINAL: KuCoin-Futures mit echtem Funding.
import pandas as pd, numpy as np, glob, os, json, sys, pickle
import rot
TRAIN=(pd.Timestamp("2019-01-01"),pd.Timestamp("2023-01-01")); VAL=(pd.Timestamp("2023-01-01"),pd.Timestamp("2024-08-05"))
def load_bn():
    out={}
    for f in glob.glob("bnd/*.csv"):
        c=os.path.basename(f)[:-4]
        if c in rot.FIAT or c.endswith(("UP","DOWN","BULL","BEAR")): continue
        d=pd.read_csv(f)
        if len(d)<5: continue
        d.index=pd.to_datetime(d.t,unit="ms"); d=d[["o","h","l","c","q"]].astype(float)
        if d.index[0]<pd.Timestamp("2018-10-01"): continue          # schon vor Datenbeginn gelistet
        # Luecke > 3 Tage = Neustart unter gleichem Kuerzel (z. B. LUNA): nur den ersten Abschnitt nehmen
        gap=np.where(np.diff(d.index.values).astype("timedelta64[D]").astype(int)>3)[0]
        if len(gap): d=d.iloc[:gap[0]+1]
        out[c]=d
    return out
def trades(L,e=7,H=60,stop=1.0,t0=None,t1=None,cost=0.004,fund=0.0003,min_q=0.0,tp=None,F=None):
    """Je Listing ein Short: Einstieg zur Eroeffnung von Tag e+1, Ausstieg nach H Tagen, bei +stop (Verlust) oder -tp (Gewinnziel).
       fund: angenommene Funding-Kosten je Tag (Short zahlt); F: echte Funding-Reihen je Coin (dann statt fund)."""
    out=[]
    for c,d in L.items():
        if len(d)<=e+2: continue
        t_in=d.index[e+1]
        if (t0 is not None and t_in<t0) or (t1 is not None and t_in>=t1): continue
        if min_q and d.q.iloc[1:e+1].median()<min_q: continue
        o,h,l,cl=d.o.values,d.h.values,d.l.values,d.c.values
        p0=o[e+1]; r=None; j_out=min(len(d)-1,e+H)
        for j in range(e+1,min(len(d),e+1+H)):
            if h[j]>=p0*(1+stop): r=-(max(o[j],p0*(1+stop))/p0-1) if j>e+1 else -stop; j_out=j; break
            if tp and l[j]<=p0*(1-tp): r=tp if o[j]>p0*(1-tp) else -(o[j]/p0-1); j_out=j; break
        if r is None: r=-(cl[j_out]/p0-1)
        days=j_out-e
        fc=fund*days
        if F is not None and c in F:
            f=F[c]; f=f[(f.index>=t_in)&(f.index<=d.index[j_out]+pd.Timedelta(days=1))]; fc=-float(f.sum())   # Short erhaelt positive Raten
        out.append(dict(coin=c,t=t_in,t_out=d.index[j_out],ret=r-cost-fc,raw=r,days=days,fund=fc))
    return pd.DataFrame(out)
def summary(T):
    if not len(T): return "keine Trades"
    r=T.ret; by=" ".join(f"{y}:{x.mean()*100:+.0f}%({len(x)})" for y,x in r.groupby(T.t.dt.year))
    return f"n {len(T):4} Ø {r.mean()*100:+6.1f}% Median {r.median()*100:+6.1f}% Gewinner {(r>0).mean()*100:4.0f}% schlechtester {r.min()*100:+5.0f}% | je Jahr {by}"
def portfolio(T,w=0.05,maxpos=10,t0=None,t1=None,eq0=1000.0):
    """Kontoverlauf: je Trade w des Kontos als Positionswert, hoechstens maxpos gleichzeitig (vereinfachend zum Ausstieg gebucht)."""
    T=T.sort_values("t"); eq=eq0; open_=[]; curve=[]; taken=0
    ev=[]
    for _,x in T.iterrows(): ev.append((x.t,1,x)); 
    days=pd.date_range(t0,t1,freq="1D"); ti=0; T2=T.reset_index(drop=True)
    for day in days:
        for p in [p for p in open_ if p["t_out"]<=day]:
            eq+=p["size"]*p["ret"]; open_.remove(p)
        while ti<len(T2) and T2.t[ti]<=day:
            if len(open_)<maxpos: open_.append(dict(t_out=T2.t_out[ti],ret=T2.ret[ti],size=eq*w)); taken+=1
            ti+=1
        curve.append(eq)
    e=pd.Series(curve,index=days); yrs=(days[-1]-days[0]).days/365.25
    return dict(ende=round(e.iloc[-1]),cagr=round(((e.iloc[-1]/eq0)**(1/yrs)-1)*100,1),dd=round((e/e.cummax()-1).min()*100,1),genommen=taken),e
if __name__=="__main__":
    L=load_bn(); pickle.dump(L,open("bn_listings.pkl","wb"))
    first=pd.Series({c:d.index[0] for c,d in L.items()})
    print("Binance-Listings je Jahr:",first.dt.year.value_counts().sort_index().to_dict())
    t0,t1=TRAIN
    print("=== TRAIN 2019-2022: Kursverlauf nach dem Listing (Schluss Tag 1 = 100)")
    rows=[]
    for c,d in L.items():
        if not (t0<=d.index[0]<t1) or len(d)<3: continue
        x={"y":d.index[0].year}
        for k in (7,30,60,90,180): x[k]=d.c.iloc[k]/d.c.iloc[1]-1 if len(d)>k else np.nan
        rows.append(x)
    R=pd.DataFrame(rows)
    for k in (7,30,60,90,180):
        v=R[k].dropna(); print(f"nach {k:3} Tagen: n {len(v):3} Mittel {v.mean()*100:+6.1f}% Median {v.median()*100:+6.1f}% im Plus {(v>0).mean()*100:3.0f}% | Median je Jahr "+" ".join(f"{y}:{g[k].median()*100:+.0f}%" for y,g in R.groupby('y')))
    print("=== TRAIN: Short je Listing (nach 0,4 % Kosten und 0,03 %/Tag Funding)")
    for e in (3,7,14):
        for H in (30,60,90):
            for st in (0.5,1.0):
                print(f"Einstieg Tag {e:2}, {H} Tage, Stop +{int(st*100):3}%: "+summary(trades(L,e=e,H=H,stop=st,t0=t0,t1=t1)))
