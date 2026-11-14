# Short-Baustein fuer schlechte Marktlagen -- Entwicklung nur auf TRAIN
import rot, pandas as pd, numpy as np, pickle
def sleeve(C,Q,mode="btc",w=0.3,vol_target=0.8,K=20,n=3,mas=(50,100,200),need=0.0,cost=0.0012,fund=0.0,stop=None,extra=None):
    """Tagesrenditen eines Short-Bausteins (Anteil am Gesamtkonto). Aktiv, wenn der Anteil erfuellter BTC-Trends <= need ist.
       mode 'btc': Bitcoin leerverkaufen; 'weak': die n schwaechsten der K meistgehandelten Coins (woechentlich neu gewaehlt)."""
    btc=C["BTC"]; frac=sum((btc>btc.rolling(m).mean()).astype(float) for m in mas)/len(mas)
    act=(frac<=need)
    if extra is not None: act&=extra(C)
    r=C.pct_change(fill_method=None)
    vol=np.log(C/C.shift(1)).rolling(30).std()*np.sqrt(365)
    if mode=="btc":
        W=pd.DataFrame(0.0,index=C.index,columns=["BTC"]); W["BTC"]=(w*np.minimum(1.0,vol_target/vol["BTC"])).where(act,0.0)
        R=r[["BTC"]]
    else:
        medq=Q.rolling(30,min_periods=30).median(); hist=C.notna().rolling(120,min_periods=120).sum()==120
        uni=medq.where(hist).rank(axis=1,ascending=False)<=K
        mom=(C/C.shift(30)-1+C/C.shift(90)-1)/2
        weak=mom.where(uni&(mom<0)&(C<C.rolling(50).mean())).rank(axis=1,ascending=True)<=n
        weekly=weak[::7].reindex(C.index,method="ffill").fillna(False)
        W=(weekly.astype(float)*(w/n)*np.minimum(1.0,vol_target/vol)).where(act,0.0).fillna(0.0); R=r
    Wl=W.shift(1).fillna(0.0)                                  # Signal von gestern, heute im Markt
    ret=-(Wl*R.reindex(columns=W.columns)).sum(axis=1)
    if stop:                                                   # Tagesverlust je Position begrenzen (Stop im Tagesverlauf)
        ret=-(Wl*R.reindex(columns=W.columns).clip(upper=stop)).sum(axis=1)
    ret-=(W-Wl).abs().sum(axis=1).shift(1).fillna(0)*cost+Wl.sum(axis=1)*fund
    return ret,act,Wl.sum(axis=1)
def st(r):
    e=(1+r).cumprod(); yrs=len(r)/365.25
    return dict(cagr=round((e.iloc[-1]**(1/yrs)-1)*100,1),dd=round((e/e.cummax()-1).min()*100,1),sharpe=round(r.mean()/r.std()*np.sqrt(365),2) if r.std()>0 else 0)
def yr(r): return " ".join(f"{y}:{((1+x).prod()-1)*100:+.0f}%" for y,x in r.groupby(r.index.year))
if __name__=="__main__":
    O,C,Q=pickle.load(open("bn_panel.pkl","rb")); t0,t1=rot.TRAIN
    P3=rot.prep2(C,Q); e,m=rot.sim2(O,C,P3,t0=t0,t1=t1,vol_target=0.8,slots=True,min_w=0.10); rl=e.pct_change().fillna(0)
    print(f"{'Long-Teil allein':52}",st(rl),yr(rl))
    V=[("BTC short, wenn alle 3 Trends negativ",dict(mode="btc")),
       ("BTC short, wenn mind. 2 von 3 negativ",dict(mode="btc",need=0.34)),
       ("3 schwaechste Coins short, alle 3 negativ",dict(mode="weak")),
       ("3 schwaechste, mind. 2 von 3 negativ",dict(mode="weak",need=0.34)),
       ("5 schwaechste aus 40, alle 3 negativ",dict(mode="weak",K=40,n=5)),
       ("3 schwaechste, doppeltes Gewicht (60 %)",dict(mode="weak",w=0.6)),
       ("3 schwaechste, Tagesverlust je Coin max 15 %",dict(mode="weak",stop=0.15)),
       ("3 schwaechste + Funding 0,03 %/Tag Kosten",dict(mode="weak",fund=0.0003)),
       ("BTC short nur wenn zusaetzlich 20-Tage-Tief",dict(mode="btc",extra=lambda C:(C["BTC"]<=C["BTC"].rolling(20).min().shift(1)*1.03)))]
    for tag,kw in V:
        rs,act,wsum=sleeve(C,Q,**kw); rs=rs[t0:t1].iloc[:-1].reindex(rl.index).fillna(0); tot=rl+rs
        a,b=st(rs),st(tot)
        print(f"{tag:52} Baustein {a['cagr']:6}% DD {a['dd']:6} | zusammen CAGR {b['cagr']:6} DD {b['dd']:6} Sharpe {b['sharpe']:5} | Baustein je Jahr {yr(rs)} | aktiv {act[t0:t1].mean()*100:.0f}% der Tage")
