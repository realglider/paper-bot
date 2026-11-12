from bt import *
import bt
idx,S,A,F=load(); print(len(S))
OLD="XBT ETH SOL XRP DOGE BNB ADA LINK AVAX LTC BCH DOT NEAR AAVE UNI TRX".split()
old=[S.index(s) for s in OLD]; new=[i for i,s in enumerate(S) if s not in OLD]; allc=list(range(len(S)))
print("neu:",[S[i] for i in new]); print("Balken je neuem Coin:",{S[i]:int((~np.isnan(A['c'][:,i])).sum()) for i in new})
SPLIT=pd.Timestamp("2025-11-01"); P=dict(n=960)
def line(tag,cols,**kw):
    kw={**dict(stop_k=12,risk=0.005),**kw}
    r=[stats(*run(idx,S,A,F,"donchian",P,cols=cols,**w,**kw)) for w in (dict(t1=SPLIT),dict(t0=SPLIT),dict())]
    a,b,c=r
    print(f"{tag:40} IS {a['cagr']:6} dd{a['dd']:6} pf{a['pf']:5} n{a['n']:4} | OOS {b['cagr']:6} dd{b['dd']:6} pf{b['pf']:5} n{b['n']:4} | ALL {c['cagr']:6} dd{c['dd']:6} sh{c['sharpe']:5} win{c['win']:5}",flush=True)
line("16 bisherige",old); line(f"{len(new)} neue",new); line(f"alle {len(S)}",allc)
line("alle, Gesamthebel max 5x",allc,max_gross=5.0)
bt.SLIP=0.0010; line("neue, Slippage 0,10 %",new); line("alle, Slippage 0,10 %",allc); bt.SLIP=0.0004
line("alle, Risiko 0,35 %",allc,risk=0.0035)
line("alle, Risiko 1 %",allc,risk=0.01)
for nn in (720,1320):
    P=dict(n=nn); line(f"alle, n={nn}",allc)
P=dict(n=960)
print("--- je neuem Coin (allein, Risiko 1 %)")
for i in new:
    e,t=run(idx,S,A,F,"donchian",P,stop_k=12,cols=[i]); print(f"{S[i]:9} end {e.iloc[-1]:8.1f} n {len(t):3}")
e,t=run(idx,S,A,F,"donchian",P,stop_k=12,risk=0.005,cols=allc)
print("Top-5-Anteil:",round(np.sort(t)[-5:].sum()/t.sum(),2),"Trades/Monat:",round(len(t)/27,1))
