import json,urllib.request,time,os,csv
from concurrent.futures import ThreadPoolExecutor
B="https://api.kucoin.com"
def get(u):
    for i in range(7):
        try:
            d=json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"x"}),timeout=30))
            if d.get("code")=="200000": return d["data"]
        except Exception as e: pass
        time.sleep(2+3*i)
    raise RuntimeError(u)
syms=[x for x in get(B+"/api/v2/symbols") if x["quoteCurrency"]=="USDT" and x["enableTrading"]]
json.dump(syms,open("spot_symbols.json","w")); print(len(syms),flush=True)
now=int(time.time()); start=1704067200  # 2024-01-01
def one(x):
    s=x["symbol"]; fn=f"spotd/{s}.csv"
    if os.path.exists(fn): return
    try:
        d=get(f"{B}/api/v1/market/candles?type=1day&symbol={s}&startAt={start}&endAt={now}")
        rows=sorted([[int(r[0]),r[1],r[3],r[4],r[2],r[5],r[6]] for r in d])
        with open(fn,"w",newline="") as f:
            w=csv.writer(f); w.writerow(["t","o","h","l","c","v","q"]); w.writerows(rows)
    except Exception as e: print("FAIL",s,e,flush=True)
    time.sleep(0.25)
with ThreadPoolExecutor(3) as ex: list(ex.map(one,syms))
print("DONE",len(os.listdir("spotd")))
