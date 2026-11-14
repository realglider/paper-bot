# Einordnung des Spot-Fremdtests: Umsatzschwelle an Binance angepasst, und Effekt geloeschter Coins
import pandas as pd, numpy as np, glob, os, json, sys
sys.argv=["x","none"]
import oos_binance as o
import exits_spot as xs
meta={s["baseAsset"]:s["status"] for s in json.load(open("bn_symbols.json"))}
FIAT={"EUR","GBP","AUD","BRL","TRY","RUB","UAH","NGN","IDRT","BIDR","BVND","DAI","USDC","BUSD","TUSD","PAX","USDP","FDUSD","UST","USTC","SUSD","USDS","USDSB","AEUR","EURI","PAXG","WBTC","WBETH","BETH","XUSD","USD1","BFUSD","USDE"}
D={}
for f in glob.glob("bnd/*.csv"):
    c=os.path.basename(f)[:-4]
    if c in FIAT or c.endswith(("UP","DOWN","BULL","BEAR")): continue
    d=pd.read_csv(f)
    if len(d)<80: continue
    d.index=pd.to_datetime(d.t,unit="ms"); D[c]=d[["o","h","l","c","q"]].astype(float)
xs.days=pd.date_range(o.START,o.END-pd.Timedelta(days=1),freq="1D"); xs.STABLE=set()
med=pd.DataFrame({c:d.q for c,d in D.items()}).rolling(20).median()
print("Coins ueber Schwelle (Median je Jahr):")
for th in (1e6,1e7,2e7,5e7): print(f"  {th/1e6:4.0f} Mio:",(med>th).sum(axis=1).groupby(med.index.year).median().astype(int).to_dict())
def go(tag,Duse,minq):
    xs.D=Duse; P=xs.prep(minq=minq)
    c,e,t=xs.sim(P,partials=((0.20,0.5),),be_at=0.20,be_lock=0.005,maxpos=7,eq0=700.0)
    print(f"{tag:44} Ende {c['end']:5} CAGR {c['cagr']:6} DD {c['dd']:6} n {c['n']:4} win {c['win']:5} avg {c['avg']:6} | {o.yearly(e)}",flush=True)
alive={c:d for c,d in D.items() if meta.get(c)=="TRADING"}
go("woertlich: ab 1 Mio Umsatz, alle Coins",D,1e6)
go("ab 1 Mio, nur heute noch handelbare",alive,1e6)
for th in (1e7,2e7,5e7,1e8):
    go(f"ab {th/1e6:.0f} Mio, alle Coins",D,th)
go("ab 20 Mio, nur heute noch handelbare",alive,2e7)
