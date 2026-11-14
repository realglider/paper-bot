import json,urllib.request,time,os,csv,glob
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
con={c["symbol"]:c["firstOpenDate"] for c in json.load(open("fut_contracts.json"))}
syms=[os.path.basename(f)[:-4] for f in glob.glob("futd/*.csv")]
def one(s):
    fn=f"futf/{s}.csv"
    if os.path.exists(fn): return
    try:
        t0=con[s]; rows={}
        for a in (t0,t0+25*86400000):
            for r in get(f"{B}/api/v1/contract/funding-rates?symbol={s}&from={a}&to={a+25*86400000}") or []: rows[r["timepoint"]]=r["fundingRate"]
        with open(fn,"w",newline="") as f:
            w=csv.writer(f); w.writerow(["t","rate"]); w.writerows(sorted(rows.items()))
    except Exception as e: print("FAIL",s,e,flush=True)
    time.sleep(0.15)
with ThreadPoolExecutor(3) as ex: list(ex.map(one,syms))
print("DONE",len(os.listdir("futf")))
