import json,urllib.request,time,sys,os,csv
from concurrent.futures import ThreadPoolExecutor
B="https://api-futures.kucoin.com"
SYMS="ZEC PEPE APT ENA FIL TAO XMR TIA SUI ONDO PAXG CRV JUP OP INJ ARB 1000BONK DASH APE WLD XLM LDO HBAR JASMY ICP SEI SNX FET QNT".split()
G=60; STEP=200*G*60000
def get(u):
    for i in range(6):
        try:
            d=json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"x"}),timeout=30))
            if d.get("code")=="200000": return d["data"]
            time.sleep(2+i*2)
        except Exception as e:
            time.sleep(2+i*2)
    raise RuntimeError(u)
now=int(time.time()//3600*3600*1000)
start=1722816000000  # 2024-08-05
def chunk(a):
    s,t=a
    return get(f"{B}/api/v1/kline/query?symbol={s}USDTM&granularity={G}&from={t}&to={t+STEP-1}")
def one(s):
    ts=list(range(start,now,STEP)); rows={}
    with ThreadPoolExecutor(3) as ex:
        for d in ex.map(chunk,[(s,t) for t in ts]):
            for r in d: rows[r[0]]=r
    rows=[rows[k] for k in sorted(rows)]
    with open(f"data/{s}_1h.csv","w",newline="") as f:
        w=csv.writer(f); w.writerow(["t","o","h","l","c","v"]); w.writerows([r[:6] for r in rows])
    # funding
    fr={}
    t=start
    while t<now:
        d=get(f"{B}/api/v1/contract/funding-rates?symbol={s}USDTM&from={t}&to={min(now,t+30*86400000)}")
        for r in d or []: fr[r["timepoint"]]=r["fundingRate"]
        t+=30*86400000
    with open(f"data/{s}_fund.csv","w",newline="") as f:
        w=csv.writer(f); w.writerow(["t","rate"]); w.writerows(sorted(fr.items()))
    print(s,len(rows),time.strftime("%Y-%m-%d",time.gmtime(rows[0][0]/1000)),len(fr),flush=True)
def safe(s):
    if os.path.exists(f"data/{s}_fund.csv"): return
    try: one(s)
    except Exception as e: print("FAIL",s,e,flush=True)
with ThreadPoolExecutor(3) as ex: list(ex.map(safe,SYMS))
print("DONE")
