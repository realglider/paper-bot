# Eingefrorene Varianten A (nur long) und B (long + Short-Baustein) -- Pruefung auf VAL und FINAL
import rot, rot_short as rs, pandas as pd, numpy as np, pickle, sys
def run(O,C,Q,t0,t1,label):
    P=rot.prep2(C,Q); e,m=rot.sim2(O,C,P,t0=t0,t1=t1,vol_target=0.8,slots=True,min_w=0.10)
    ra=e.pct_change().fillna(0)
    sl,act,_=rs.sleeve(C,Q,mode="weak",fund=0.0003); sl=sl.reindex(ra.index).fillna(0); rb=ra+sl
    b0=rot.bench(O,C,t0,t1).pct_change().fillna(0); b1=rot.bench(O,C,t0,t1,100).pct_change().fillna(0)
    print(f"=== {label}: {ra.index[0].date()} bis {ra.index[-1].date()}  (Trades {m['trades']}, im Schnitt {m['avg_expo']*100:.0f} % investiert)")
    for nm,r in (("A  nur long",ra),("B  long + Short-Baustein",rb),("   Short-Baustein allein",sl),("   Bitcoin halten",b0.reindex(ra.index).fillna(0)),("   Bitcoin mit 100-Tage-Filter",b1.reindex(ra.index).fillna(0))):
        s=rs.st(r); print(f"{nm:32} Rendite/Jahr {s['cagr']:6}%  groesster Rueckgang {s['dd']:6}%  Sharpe {s['sharpe']:5}  gesamt {((1+r).prod()-1)*100:+7.1f}% | {rs.yr(r)}")
    return ra,rb,sl,e
if __name__=="__main__":
    which=sys.argv[1]
    if which=="val":
        O,C,Q=pickle.load(open("bn_panel.pkl","rb")); run(O,C,Q,*rot.VAL,"VALIDIERUNG (Binance)")
    elif which=="final":
        O,C,Q=rot.panel("spotd","s"); print("KuCoin-Panel",O.shape,O.index[0].date(),O.index[-1].date())
        ra,rb,sl,e=run(O,C,Q,pd.Timestamp("2024-08-05"),pd.Timestamp("2026-11-13"),"SCHLUSSKONTROLLE (KuCoin)")
        pickle.dump((ra,rb,sl),open("rot_final.pkl","wb"))
