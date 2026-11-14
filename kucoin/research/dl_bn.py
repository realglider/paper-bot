import json,urllib.request,time,os,csv
from concurrent.futures import ThreadPoolExecutor
B="https://data-api.binance.vision"
def get(u):
    for i in range(7):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"x"}),timeout=30))
        except Exception as e: time.sleep(2+3*i)
    raise RuntimeError(u)
END=1722816000000  # 2024-08-05: ab hier beginnen die KuCoin-Entwicklungsdaten
def series(sym,iv,start,step):
    rows={}; t=start
    while t<END:
        k=get(f"{B}/api/v3/klines?symbol={sym}&interval={iv}&startTime={t}&endTime={END-1}&limit=1000")
        if not k: break
        for r in k: rows[r[0]]=[r[0],r[1],r[2],r[3],r[4],r[5],r[7]]
        t=k[-1][0]+step
        if len(k)<1000: break
    return [rows[x] for x in sorted(rows)]
def save(fn,rows):
    with open(fn,"w",newline="") as f:
        w=csv.writer(f); w.writerow(["t","o","h","l","c","v","q"]); w.writerows(rows)
H="BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC BCH DOT NEAR AAVE UNI TRX XLM ETC NEO XTZ ATOM DASH EOS XMR".split()
def one_h(c):
    fn=f"bn1h/{c}.csv"
    if os.path.exists(fn): return
    r=series(c+"USDT","1h",1535760000000,3600000); save(fn,r); print("1h",c,len(r),flush=True)
with ThreadPoolExecutor(4) as ex: list(ex.map(one_h,H))
syms=[s for s in get(B+"/api/v3/exchangeInfo")["symbols"] if s["quoteAsset"]=="USDT"]
json.dump([{k:s[k] for k in ("symbol","baseAsset","status")} for s in syms],open("bn_symbols.json","w"))
def one_d(s):
    fn=f"bnd/{s['baseAsset']}.csv"
    if os.path.exists(fn): return
    try: r=series(s["symbol"],"1d",1535760000000,86400000); save(fn,r)
    except Exception as e: print("FAIL",s["symbol"],e,flush=True)
with ThreadPoolExecutor(4) as ex: list(ex.map(one_d,syms))
print("DONE",len(os.listdir("bn1h")),len(os.listdir("bnd")))
