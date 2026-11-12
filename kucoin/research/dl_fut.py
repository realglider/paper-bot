import json,urllib.request,time,os,csv
from concurrent.futures import ThreadPoolExecutor
B="https://api-futures.kucoin.com"
def get(u):
    for i in range(7):
        try:
            d=json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"x"}),timeout=30))
            if d.get("code")=="200000": return d["data"]
        except Exception as e: pass
        time.sleep(2+3*i)
    raise RuntimeError(u)
c=[x for x in get(B+"/api/v1/contracts/active") if x["symbol"].endswith("USDTM")]
json.dump([{k:x.get(k) for k in ("symbol","firstOpenDate","turnoverOf24h","multiplier","status")} for x in c],open("fut_contracts.json","w"))
new=[x for x in c if x["firstOpenDate"]>=1722470400000]; print(len(c),len(new),flush=True)
now=int(time.time()*1000)
def one(x):
    s=x["symbol"]; fn=f"futd/{s}.csv"
    if os.path.exists(fn): return
    try:
        rows={}; t=x["firstOpenDate"]-86400000
        while t<now:
            for r in get(f"{B}/api/v1/kline/query?symbol={s}&granularity=1440&from={t}&to={min(now,t+200*86400000-1)}") or []: rows[r[0]]=r[:6]
            t+=200*86400000
        with open(fn,"w",newline="") as f:
            w=csv.writer(f); w.writerow(["t","o","h","l","c","v"]); w.writerows([rows[k] for k in sorted(rows)])
    except Exception as e: print("FAIL",s,e,flush=True)
    time.sleep(0.2)
with ThreadPoolExecutor(3) as ex: list(ex.map(one,new))
print("DONE",len(os.listdir("futd")))
