# Echter Fremdtest: heutige Regeln UNVERAENDERT auf Binance-Daten 2019 bis 08/2024 (vor den Entwicklungsdaten)
import pandas as pd, numpy as np, glob, os, json, sys
import bt
from bt import run, stats
import exits_spot as xs
START=pd.Timestamp("2019-01-01"); END=pd.Timestamp("2024-08-05")
def load_h(coins):
    px={}
    for c in coins:
        d=pd.read_csv(f"bn1h/{c}.csv")
        if len(d)<2000: continue
        d.index=pd.to_datetime(d.t,unit="ms"); px[c]=d[["o","h","l","c"]].astype(float)
    idx=pd.date_range(min(d.index[0] for d in px.values()),END-pd.Timedelta(hours=1),freq="1h"); S=list(px)
    A={k:np.column_stack([px[s][k].reindex(idx).values for s in S]) for k in "ohlc"}
    F=np.zeros(A["c"].shape); F[(idx.hour%8==0),:]=0.0001      # Annahme: 0,01 % je 8 h, Longs zahlen
    return idx,S,A,F
U_NOW="BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC BCH DOT NEAR AAVE UNI TRX".split()
U_2019="BTC ETH XRP LTC BCH BNB XLM ADA TRX ETC NEO XTZ LINK ATOM DASH EOS XMR".split()   # grosse Coins aus Sicht von 2019
def fut(idx,S,A,F,**kw):
    k=dict(stop_k=12,risk=5/300,min_notional=100,max_risk=10/300,max_pos=3.0,max_gross=5.0,eq0=300.0,part_r=1.0,be_r=1.0,be_lock=0.05,t0=START)
    k.update(kw); return run(idx,S,A,F,"donchian",dict(n=960),**k)
def yearly(e):
    y=e.resample("YE").last(); prev=np.r_[e.iloc[0],y.values[:-1]]
    return " ".join(f"{d.year}:{(v/p-1)*100:+.0f}%" for d,v,p in zip(y.index,y.values,prev))
if "fut" in sys.argv or len(sys.argv)==1:
    print("=== Futures-Teil (Trendfolge), Start 300 USDT, 2019-01 bis 2024-08")
    for name,U in (("heutige 16 Coins",U_NOW),("Coins aus Sicht von 2019",U_2019)):
        idx,S,A,F=load_h(U)
        for tag,kw in (("wie live (mit Teilgewinn)",{}),("ohne Teilgewinn",dict(part_r=None,be_r=None)),("nur long",dict(side="long")),("nur short",dict(side="short")),("doppelte Kosten",None)):
            if kw is None:
                bt.FEE,bt.SLIP=0.0012,0.0008; e,t=fut(idx,S,A,F); bt.FEE,bt.SLIP=0.0006,0.0004
            else: e,t=fut(idx,S,A,F,**kw)
            s=stats(e,t); print(f"{name:26}{tag:26} Ende {s['end']:6.0f} CAGR {s['cagr']:6} DD {s['dd']:6} Sharpe {s['sharpe']:5} n {s['n']:4} win {s['win']:5} pf {s['pf']:5} | {yearly(e)}",flush=True)
if "spot" in sys.argv or len(sys.argv)==1:
    print("=== Spot-Teil (Momentum-Scanner), Start 700 USDT, alle Binance-USDT-Paare inkl. geloeschter")
    meta={s["baseAsset"]:s["status"] for s in json.load(open("bn_symbols.json"))}
    FIAT={"EUR","GBP","AUD","BRL","TRY","RUB","UAH","NGN","IDRT","BIDR","BVND","DAI","USDC","BUSD","TUSD","PAX","USDP","FDUSD","UST","USTC","SUSD","USDS","USDSB","AEUR","EURI","PAXG","WBTC","WBETH","BETH","XUSD","USD1","BFUSD","USDE"}
    D={}
    for f in glob.glob("bnd/*.csv"):
        c=os.path.basename(f)[:-4]
        if c in FIAT or c.endswith(("UP","DOWN","BULL","BEAR")): continue
        d=pd.read_csv(f)
        if len(d)<80: continue
        d.index=pd.to_datetime(d.t,unit="ms"); D[c]=d[["o","h","l","c","q"]].astype(float)
    print(len(D),"Coins, davon inzwischen nicht mehr handelbar:",sum(1 for c in D if meta.get(c)!="TRADING"))
    xs.D=D; xs.days=pd.date_range(START,END-pd.Timedelta(days=1),freq="1D"); xs.STABLE=set()
    P=xs.prep()
    def spot(**kw):
        k=dict(partials=((0.20,0.5),),be_at=0.20,be_lock=0.005,maxpos=7,eq0=700.0); k.update(kw); return xs.sim(P,**k)
    for tag,kw in (("wie live",{}),("doppelte Slippage",dict(slip=0.006)),("alte Regel: alles bei +40 %",dict(partials=(),be_at=None,tp=0.40)),("5 Plaetze",dict(maxpos=5,eq0=500.0)),("10 Plaetze",dict(maxpos=10,eq0=1000.0))):
        c,e,t=spot(**kw); print(f"{tag:30} {c} | {yearly(e)}",flush=True)
    c,e,t=spot(); e.to_csv("oos_spot_eq.csv")
