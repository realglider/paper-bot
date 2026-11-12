# Was passiert nach einem neuen Listing? (KuCoin-Futures und KuCoin-Spot, Tageskerzen)
import pandas as pd, numpy as np, glob, os, json, sys
def study(files, first_ok, label, tcol_ms):
    rows=[]
    for f in files:
        try: d=pd.read_csv(f)
        except Exception: continue
        if len(d)<3: continue
        t=pd.to_datetime(d.t,unit="ms" if tcol_ms else "s"); c=d.c.astype(float).values; h=d.h.astype(float).values
        if not first_ok(t.iloc[0]): continue
        r={"sym":os.path.basename(f)[:-4],"start":t.iloc[0],"n":len(c)}
        for k in (1,3,7,30,60,90,180):
            r[f"d{k}"]=c[k]/c[0]-1 if len(c)>k else np.nan
        for k in (30,90):
            r[f"max{k}"]=h[1:k+1].max()/c[0]-1 if len(c)>k else np.nan     # schlimmster Anstieg gegen einen Short
        rows.append(r)
    D=pd.DataFrame(rows); print(f"\n=== {label}: {len(D)} Listings ===")
    for k in (1,3,7,30,60,90,180):
        x=D[f"d{k}"].dropna()
        print(f"nach {k:3} Tagen: n={len(x):4} Mittel {x.mean()*100:7.1f} %  Median {x.median()*100:7.1f} %  Anteil im Plus {(x>0).mean()*100:5.1f} %  schlechtestes 10tel {x.quantile(.1)*100:6.1f} %  bestes 10tel {x.quantile(.9)*100:7.1f} %")
    for k in (30,90):
        x=D[f"max{k}"].dropna(); print(f"hoechster Kurs in {k} Tagen ueber Tag-0-Schluss: Median +{x.median()*100:.0f} %, in {(x>0.5).mean()*100:.0f} % der Faelle ueber +50 %, in {(x>1).mean()*100:.0f} % ueber +100 %")
    D["year"]=D.start.dt.year; print(D.groupby("year")[["d30","d90"]].agg(["count","mean","median"]).round(3).to_string())
    return D
which=sys.argv[1]
if which=="fut":
    D=study(sorted(glob.glob("futd/*.csv")),lambda t:True,"KuCoin-Futures, neu seit 08/2024",True)
    # Short-Strategie: am Schluss von Tag e leerverkaufen, nach H Tagen eindecken, Stop bei +S %
    print("\n--- Short neuer Futures-Listings: Einstieg Tag e, Haltedauer H, Stop S (je Trade, vor Funding, nach 0,2 % Kosten)")
    for e in (1,3,7):
        for H in (30,60,90):
            for Sx in (0.5,1.0):
                res=[]
                for f in sorted(glob.glob("futd/*.csv")):
                    d=pd.read_csv(f)
                    if len(d)<=e+H: continue
                    c=d.c.values; h=d.h.values; p0=c[e]; r=None
                    for k in range(e+1,e+H+1):
                        if h[k]>=p0*(1+Sx): r=-Sx; break
                    if r is None: r=-(c[e+H]/p0-1)
                    res.append(r-0.002)
                x=np.array(res)
                print(f"e={e} H={H:2} Stop +{int(Sx*100):3} %: n={len(x):3} Mittel {x.mean()*100:6.1f} %  Median {np.median(x)*100:6.1f} %  Gewinner {(x>0).mean()*100:4.0f} %  gestoppt {(x<=-Sx+0.01).mean()*100:4.0f} %")
else:
    D=study(sorted(glob.glob("spotd/*.csv")),lambda t:t>pd.Timestamp("2024-01-15"),"KuCoin-Spot, neu seit 01/2024 (nur heute noch handelbare Coins)",False)
