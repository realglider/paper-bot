from bt import *
import bt
idx,S,A,F=load()
OLD="XBT ETH SOL XRP DOGE BNB ADA LINK AVAX LTC BCH DOT NEAR AAVE UNI TRX".split()
old=[S.index(s) for s in OLD]; F0=F*0; bt.FEE=0.001
SPLIT=pd.Timestamp("2025-11-01")
def line(tag,p,cols=old,**kw):
    kw={**dict(stop_k=12,side="long",max_gross=1.0,max_pos=1.0),**kw}
    r=[stats(*run(idx,S,A,F0,"donchian",p,cols=cols,**w,**kw)) for w in (dict(t1=SPLIT),dict(t0=SPLIT),dict())]
    a,b,c=r
    print(f"{tag:42} IS {a['cagr']:6} dd{a['dd']:6} pf{a['pf']:5} n{a['n']:4} | OOS {b['cagr']:6} dd{b['dd']:6} pf{b['pf']:5} n{b['n']:4} | ALL {c['cagr']:6} dd{c['dd']:6} sh{c['sharpe']:5} win{c['win']:5} end{c['end']:6}",flush=True)
for n in (720,960,1320):
    line(f"n={n} Risiko 0,5 % (ohne Mindestgroesse)",dict(n=n),risk=0.005)
    line(f"n={n} mind. 100 $ je Trade",dict(n=n),risk=0.005,min_notional=100)
    line(f"n={n} 10 % des Kontos, mind. 100 $",dict(n=n),risk=0.005,min_notional=100,frac=0.10)
for k in (8,16): line(f"n=960 10 %/100 $, Stop {k}",dict(n=960),risk=0.005,min_notional=100,frac=0.10,stop_k=k)
e,t=run(idx,S,A,F0,"donchian",dict(n=960),cols=old,stop_k=12,side="long",max_gross=1.0,max_pos=1.0,risk=0.005,min_notional=100,frac=0.10)
m=e.resample("ME").last().pct_change().dropna()*100
print("Monate im Plus:",int((m>0.05).sum()),"flach:",int((abs(m)<=0.05).sum()),"im Minus:",int((m<-0.05).sum()),"von",len(m)); print(m.round(1).to_string())
print("Top-5-Anteil:",round(np.sort(t)[-5:].sum()/t.sum(),2),"groesster Gewinn/Verlust:",t.max().round(1),t.min().round(1),"Schnitt Verlust:",t[t<0].mean().round(2))
# Vergleich: Kaufen und Halten (gleichgewichtet, 16 Coins)
c=pd.DataFrame(A["c"][:,old],index=idx).ffill().dropna(); bh=(c/c.iloc[0]).mean(axis=1)
for nm,x in (("gesamt",bh),("bis 11/2025",bh[:SPLIT]),("ab 11/2025",bh[SPLIT:])):
    print("Kaufen+Halten",nm,round((x.iloc[-1]/x.iloc[0]-1)*100,1),"% maxDD",round(((x/x.cummax()-1).min())*100,1))
