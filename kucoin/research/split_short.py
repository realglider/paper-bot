# Zwei vorab festgelegte Tests:
#  A) Aufteilung 700 Spot (7 Plaetze) / 300 Futures statt 500/500
#  B) Short-Scanner: liquide Coins leerverkaufen, die mit Volumen unter ihr 20-Tage-Tief fallen
# Uebernahme nur, wenn die Aenderung in BEIDEN Phasen getrennt hilft (B zusaetzlich: bei doppelter Slippage im Plus).
import json, pandas as pd, numpy as np
from bt import *
import exits_spot as xs
idx,S,A,F=load()
OLD="XBT ETH SOL XRP DOGE BNB ADA LINK AVAX LTC BCH DOT NEAR AAVE UNI TRX".split(); old=[S.index(s) for s in OLD]
SPLIT=pd.Timestamp("2025-11-01")
def fut(eq0,**w):
    # gleiche Dollar-Risiken wie bisher (Ziel 5, hoechstens 10, mind. 100 Positionswert, hoechstens 3000 gesamt)
    return run(idx,S,A,F,"donchian",dict(n=960),cols=old,stop_k=12,risk=5/eq0,min_notional=100,max_risk=10/eq0,max_pos=1500/eq0,max_gross=3000/eq0,eq0=eq0,part_r=1.0,be_r=1.0,be_lock=0.05,**w)
P=xs.prep()
def spot(n,**w):
    return xs.sim(P,partials=((0.20,0.5),),be_at=0.20,be_lock=0.005,maxpos=n,eq0=100.0*n,**w)
def combo(fe,se):
    fd=fe.resample("1D").last(); tot=(fd+se.reindex(fd.index,method="ffill")).dropna()
    yrs=(tot.index[-1]-tot.index[0]).days/365.25; r=tot.pct_change().dropna()
    return dict(ende=round(tot.iloc[-1]),cagr=round(((tot.iloc[-1]/tot.iloc[0])**(1/yrs)-1)*100,1),dd=round((tot/tot.cummax()-1).min()*100,1),sharpe=round(r.mean()/r.std()*np.sqrt(365),2))
print("=== A) Aufteilung")
for fe0,n in ((500,5),(300,7),(200,8),(0,10)):
    row=[]
    for tag,w in (("bis 10/25",dict(t1=SPLIT)),("ab 11/25",dict(t0=SPLIT)),("gesamt",dict())):
        c,se,_=spot(n,**w)
        if fe0: fe,_=fut(float(fe0),**w); row.append(f"{tag} {combo(fe,se)}")
        else:
            yrs=(se.index[-1]-se.index[0]).days/365.25; row.append(f"{tag} {{'ende': {round(se.iloc[-1])}, 'cagr': {c['cagr']}, 'dd': {c['dd']}}}")
    print(f"Futures {fe0} / Spot {n*100}: "+" | ".join(row),flush=True)
fe,_=fut(300.0); print("Futures-Konto 300: tiefster Stand",round(fe.min()),"Ende",round(fe.iloc[-1]))

print("=== B) Short-Scanner")
D={}
import glob, os
con={c["symbol"][:-5] for c in json.load(open("fut_contracts.json"))}
con|={"BTC"} if "XBT" in con else set()
for f in glob.glob("spotd/*.csv"):
    s=os.path.basename(f)[:-9]
    if s not in con: continue
    d=pd.read_csv(f)
    if len(d)<80: continue
    d.index=pd.to_datetime(d.t,unit="s"); D[s]=d[["o","h","l","c","q"]].astype(float)
print(len(D),"Coins mit Future")
days=pd.date_range("2024-08-05","2026-11-12",freq="1D")
def prep_s(n=20,vm=3,sma=50,minq=1e6):
    Q={}
    for s,d in D.items():
        if s in xs.STABLE: continue
        ll=d.l.rolling(n).min().shift(1); va=d.q.rolling(20).mean().shift(1); ma=d.c.rolling(sma).mean(); medq=d.q.rolling(20).median().shift(1)
        sig=((d.c<ll)&(d.q>vm*va)&(d.c<ma)&(medq>minq)&(d.c>d.o*0.6)).values
        Q[s]=dict(idx={t:i for i,t in enumerate(d.index)},o=d.o.values,h=d.h.values,l=d.l.values,c=d.c.values,q=d.q.values,sig=sig)
    return Q
def sim_s(Q,hard=0.12,trail=0.15,part=0.20,size=100.0,maxpos=5,fee=0.0006,slip=0.003,fund=0.0,cooldown=2,t0=None,t1=None,eq0=500.0):
    """Spiegelbild des Spot-Scanners: Short zur naechsten Eroeffnung, Stop 12 % ueber Einstieg bzw. 15 % ueber dem tiefsten
       Tagesschluss, bei -20 % die Haelfte eindecken und Stop auf Einstand. fund = Funding-Kosten je Tag auf den Positionswert."""
    cash=eq0; pos={}; last={}; tr=[]; eq=[]
    for day in days:
        if t0 is not None and day<t0: continue
        if t1 is not None and day>=t1: break
        for s in list(pos):
            X=Q[s]; i=X["idx"].get(day)
            if i is None: continue
            p=pos[s]; o,h,l,c=X["o"][i],X["h"][i],X["l"][i],X["c"][i]
            stop=min(p["entry"]*(1+hard),p["trough"]*(1+trail))
            if p["part"]: stop=min(stop,p["entry"]*(1-0.005))
            p["pnl"]-=p["qty"]*c*fund
            if h>=stop:
                px=max(o,stop)*(1+slip); p["pnl"]+=p["qty"]*(p["entry"]-px)-p["qty"]*px*fee; cash+=p["pnl"]; tr.append(p["pnl"]/size); last[s]=day; del pos[s]; continue
            if not p["part"] and l<=p["entry"]*(1-part):
                px=min(o,p["entry"]*(1-part)); q=p["qty"]*0.5; p["pnl"]+=q*(p["entry"]-px)-q*px*fee; p["qty"]-=q; p["part"]=True
            p["trough"]=min(p["trough"],c)
        cands=[]
        for s,X in Q.items():
            if s in pos: continue
            i=X["idx"].get(day)
            if i is None or i<1 or not X["sig"][i-1]: continue
            if s in last and (day-last[s]).days<cooldown: continue
            cands.append((X["q"][i-1],s,X["o"][i]))
        for q,s,o in sorted(cands,reverse=True):
            if len(pos)>=maxpos: break
            px=o*(1-slip); pos[s]=dict(entry=px,qty=size/px,trough=px,pnl=-size*fee,part=False)
        val=cash
        for s,p in pos.items():
            X=Q[s]; i=X["idx"].get(day); val+=p["pnl"]+p["qty"]*(p["entry"]-(X["c"][i] if i is not None else p["entry"]))
        eq.append((day,val))
    e=pd.Series(dict(eq)); t=np.array(tr); yrs=(e.index[-1]-e.index[0]).days/365.25
    return dict(gewinn=round(e.iloc[-1]-eq0),cagr=round(((max(e.iloc[-1],1)/e.iloc[0])**(1/yrs)-1)*100,1),dd=round((e/e.cummax()-1).min()*100,1),n=len(t),win=round((t>0).mean()*100,1) if len(t) else 0,avg=round(t.mean()*100,2) if len(t) else 0)
Q=prep_s()
for tag,kw in (("Grundvariante",dict()),("doppelte Slippage",dict(slip=0.006)),("mit Funding 0,03 %/Tag",dict(fund=0.0003)),("10 Plaetze",dict(maxpos=10,eq0=1000.0))):
    print(f"{tag:26}"+" | ".join(f"{t} {sim_s(Q,**w,**kw)}" for t,w in (("bis 10/25",dict(t1=SPLIT)),("ab 11/25",dict(t0=SPLIT)),("gesamt",dict()))),flush=True)
print("--- Nachbarschaft (nur zur Einordnung, nicht zur Auswahl)")
for n,vm,minq in ((20,2,1e6),(20,5,1e6),(55,3,1e6),(10,3,1e6),(20,3,5e5),(20,3,2e6)):
    Q2=prep_s(n=n,vm=vm,minq=minq)
    print(f"Tief {n}T Vol x{vm} min {minq/1e6}M  "+" | ".join(f"{t} {sim_s(Q2,**w)}" for t,w in (("bis 10/25",dict(t1=SPLIT)),("ab 11/25",dict(t0=SPLIT)),("gesamt",dict()))),flush=True)
