from bt import *
import bt
idx,S,A,F=load()
OLD="XBT ETH SOL XRP DOGE BNB ADA LINK AVAX LTC BCH DOT NEAR AAVE UNI TRX".split()
old=[S.index(s) for s in OLD]; SPLIT=pd.Timestamp("2025-11-01")
base=SIG["donchian"]
def regime(A,idx,n=960,ema_n=1200,**k):
    el,es,xl,xs=base(A,idx,n=n); b=A["c"][:,S.index("XBT")]; e=ema(b[:,None],ema_n)[:,0]
    up=(b>e)[:,None]; dn=(b<e)[:,None]
    return el&up, es&dn, xl, xs
def ens(A,idx,**k):   # Ensemble: Ausbruch auf 20, 40 und 55 Tage, Einstieg wenn mind. 2 von 3
    r=[base(A,idx,n=n) for n in (480,960,1320)]
    el=sum(x[0].astype(int) for x in r)>=2; es=sum(x[1].astype(int) for x in r)>=2
    return el,es,r[1][2],r[1][3]
SIG["regime"]=regime; SIG["ens"]=ens
def line(tag,name,p,**kw):
    kw={**dict(stop_k=12,risk=0.005,cols=old),**kw}
    a,b,c=[stats(*run(idx,S,A,F,name,p,**w,**kw)) for w in (dict(t1=SPLIT),dict(t0=SPLIT),dict())]
    print(f"{tag:40} IS {a['cagr']:6} dd{a['dd']:6} pf{a['pf']:5} n{a['n']:4} | OOS {b['cagr']:6} dd{b['dd']:6} pf{b['pf']:5} n{b['n']:4} | ALL {c['cagr']:6} dd{c['dd']:6} sh{c['sharpe']:5} win{c['win']:5}",flush=True)
line("Basis (40-Tage-Ausbruch)","donchian",dict(n=960))
line("Basis, mind. 100 $ je Trade","donchian",dict(n=960),min_notional=100)
line("+ BTC-Trendfilter 50 Tage","regime",dict(n=960,ema_n=1200))
line("+ BTC-Trendfilter 100 Tage","regime",dict(n=960,ema_n=2400))
line("+ BTC-Trendfilter 25 Tage","regime",dict(n=960,ema_n=600))
line("Ensemble 20/40/55 Tage","ens",dict())
line("BTC-Filter 50 T., mind. 100 $","regime",dict(n=960,ema_n=1200),min_notional=100)
line("BTC-Filter 50 T., alle 45 Coins","regime",dict(n=960,ema_n=1200),cols=list(range(len(S))))
print("--- mind. 100 $, Trades mit zu weitem Stop auslassen")
for mr in (0.0075,0.01,0.0125):
    line(f"mind. 100 $, Risiko je Trade max {mr*100:.2f} %","donchian",dict(n=960),min_notional=100,max_risk=mr)
for eq in (2000,3000):
    line(f"mind. 100 $, Konto {eq} $","donchian",dict(n=960),min_notional=100,eq0=eq)
