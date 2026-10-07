#!/usr/bin/env python3
"""重拉 index_weight(修正列错位 bug: 用 DataFrame 列名取值):
输出 data/ts_index_weight.json {指数名: {"YYYY-MM": {con_code6: weight}}}
"""
import json, os, sys, time, threading, datetime as dt
import tushare as ts
from concurrent.futures import ThreadPoolExecutor
os.chdir("/vol1/1000/openzl/etf_tool")
TOKEN = "90861eb43672f06ce6c5b3a890471d0bb8bf5be3fc749280485d2364"
NW = int(os.environ.get("TS_NW", "16"))
_tl = threading.local()
def pro():
    if not hasattr(_tl, "p"):
        _tl.p = ts.pro_api(TOKEN); _tl.p._DataApi__http_url = "https://jiaoch.top/"
    return _tl.p
def retry(fn, n=4, d=1.5):
    for i in range(n):
        try:
            r = fn()
            if r is not None: return r
        except Exception:
            time.sleep(d * (i + 1))
    return None
IDX = {"上证50":"000016","上证180":"000010","沪深300":"000300","中证500":"000905","中证1000":"000852",
       "中证A500":"000510","科创50":"000688","创业板指":"399006","中证红利":"000922","中证军工":"399967",
       "中证医疗":"399989","中证环保":"000827","中证传媒":"399971","养老产业":"399812","全指医药":"000991",
       "全指消费":"000990","全指信息":"000993","全指金融":"000992","食品饮料":"000807","中证白酒":"399997",
       "证券公司":"399975","中证银行":"399986","红利低波100":"930955","创业板综":"399102"}

def one(name, base, suf, y, m):
    if (y, m) > (2026, 9): return None
    s = dt.date(y, m, 1); e = dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1)
    df = retry(lambda: pro().index_weight(index_code=base + suf,
                                          start_date=s.strftime("%Y%m%d"), end_date=e.strftime("%Y%m%d")))
    if df is None or not hasattr(df, "columns") or not len(df) or "con_code" not in df.columns:
        return None
    last = sorted(df["trade_date"].unique())[-1]
    sub = df[df["trade_date"] == last]
    out = {}
    for code, w in zip(sub["con_code"], sub["weight"]):
        try: out[str(code).split(".")[0]] = float(w)
        except (TypeError, ValueError): pass
    return out or None

OUT = "data/ts_index_weight.json"
store = {n: {} for n in IDX}
jobs = []
for name, base in IDX.items():
    for suf in (".SH", ".SZ", ".CSI"):
        for y in range(2004, 2027):
            for m in range(1, 13):
                if (y, m) > (2026, 9): continue
                jobs.append((name, base, suf, y, m))
print(f"重新抓取 {len(jobs)} 个 (指数×后缀×月), {NW} 线程", flush=True)
lock = threading.Lock(); done = [0]; hit = [0]
def work(j):
    name, base, suf, y, m = j
    k = f"{y}-{m:02d}"
    with lock:
        if store[name].get(k): return
    v = one(name, base, suf, y, m)
    with lock:
        if v and not store[name].get(k):
            store[name][k] = v; hit[0] += 1
        done[0] += 1
        if done[0] % 250 == 0:
            json.dump(store, open(OUT, "w"), ensure_ascii=False)
            print(f"  {done[0]}/{len(jobs)} 命中 {hit[0]}", flush=True)
with ThreadPoolExecutor(max_workers=NW) as ex: list(ex.map(work, jobs))
json.dump(store, open(OUT, "w"), ensure_ascii=False)
for n in IDX: print(f"[{n}] {len(store[n])} 期", flush=True)
print(f"完成: 总 {sum(len(store[n]) for n in IDX)} 期", flush=True)
