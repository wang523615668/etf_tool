#!/usr/bin/env python3
"""补拉: 分页/分年拉取被截断的数据 + 指数代码后缀校正 + 申万映射补全
用法: ts_fix.py <macro|idxbasic|weight_fix|swmap|all>
"""
import json, os, sys, time, threading
import tushare as ts
from concurrent.futures import ThreadPoolExecutor
os.chdir("/vol1/1000/openzl/etf_tool")
TOKEN = "90861eb43672f06ce6c5b3a890471d0bb8bf5be3fc749280485d2364"
NW = int(os.environ.get("TS_NW", "6"))
_tl = threading.local()
def pro():
    if not hasattr(_tl, "p"):
        _tl.p = ts.pro_api(TOKEN); _tl.p._DataApi__http_url = "https://jiaoch.top/"
    return _tl.p
def retry(fn, n=4, d=2.0):
    for i in range(n):
        try:
            r = fn()
            if r is not None: return r
        except Exception:
            time.sleep(d * (i + 1))
    return None
mode = sys.argv[1] if len(sys.argv) > 1 else "all"

IDX = {"上证50":"000016","上证180":"000010","沪深300":"000300","中证500":"000905","中证1000":"000852",
       "中证A500":"000510","科创50":"000688","创业板指":"399006","中证红利":"000922","中证军工":"399967",
       "中证医疗":"399989","中证环保":"000827","中证传媒":"399971","养老产业":"399812","全指医药":"000991",
       "全指消费":"000990","全指信息":"000993","全指金融":"000992","食品饮料":"000807","中证白酒":"399997",
       "证券公司":"399975","中证银行":"399986","红利低波100":"930955","创业板综":"399102"}
SUF = [".SH", ".SZ", ".CSI"]

def macro():
    # 两融: 分年
    recs = []
    for y in range(2010, 2027):
        df = retry(lambda y=y: pro().margin(start_date=f"{y}0101", end_date=f"{y}1231"))
        if df is not None and len(df): recs += df.to_dict("records")
        print(f"  两融 {y}: {0 if df is None else len(df)} 行", flush=True)
        time.sleep(0.2)
    json.dump(recs, open("data/ts_margin.json", "w"), ensure_ascii=False, default=str)
    print(f"两融合计 {len(recs)} 行", flush=True)
    # 北向: 分年
    recs = []
    for y in range(2014, 2027):
        df = retry(lambda y=y: pro().moneyflow_hsgt(start_date=f"{y}0101", end_date=f"{y}1231"))
        if df is not None and len(df): recs += df.to_dict("records")
        print(f"  北向 {y}: {0 if df is None else len(df)} 行", flush=True)
        time.sleep(0.2)
    json.dump(recs, open("data/ts_hsgt.json", "w"), ensure_ascii=False, default=str)
    print(f"北向合计 {len(recs)} 行", flush=True)

def idxbasic():
    out = json.load(open("data/ts_index_dailybasic.json")) if os.path.exists("data/ts_index_dailybasic.json") else {}
    for name, base in IDX.items():
        got = out.get(name) or {}
        for suf in SUF:
            code = base + suf
            for y in range(2005, 2027):
                df = retry(lambda code=code, y=y: pro().index_dailybasic(
                    ts_code=code, start_date=f"{y}0101", end_date=f"{y}1231",
                    fields="trade_date,pe,pe_ttm,pb,total_mv,float_mv"))
                if df is not None and len(df):
                    for r in df.itertuples():
                        got[str(r[0])] = [r[1], r[2], r[3]]
                time.sleep(0.1)
            if got: break
        out[name] = got
        json.dump(out, open("data/ts_index_dailybasic.json", "w"), ensure_ascii=False)
        print(f"  {name}: {len(got)} 天", flush=True)

def weight_fix():
    OUT = "data/ts_index_weight.json"
    store = json.load(open(OUT))
    jobs = []
    for name, base in IDX.items():
        for suf in SUF:
            code = base + suf
            for y in range(2004, 2027):
                for m in range(1, 13):
                    k = f"{y}-{m:02d}"
                    if (y, m) > (2026, 9): continue
                    if not store.get(name, {}).get(k):
                        jobs.append((name, code, k, y, m))
    print(f"稀疏补拉候选 {len(jobs)} 条", flush=True)
    lock = threading.Lock(); done = [0]
    def work(j):
        name, code, k, y, m = j
        import datetime as dt
        s = dt.date(y, m, 1); e = dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1)
        df = retry(lambda: pro().index_weight(index_code=code, start_date=s.strftime("%Y%m%d"), end_date=e.strftime("%Y%m%d")))
        if df is not None and hasattr(df, "trade_date") and len(df):
            last = sorted(df.trade_date.unique())[-1]
            sub = df[df.trade_date == last]
            val = {str(r[1]): float(r[3]) for r in sub.itertuples()}
            with lock:
                if not store[name].get(k): store[name][k] = val
        with lock:
            done[0] += 1
            if done[0] % 100 == 0:
                json.dump(store, open(OUT, "w"), ensure_ascii=False); print(f"  {done[0]}/{len(jobs)}", flush=True)
    with ThreadPoolExecutor(max_workers=NW) as ex: list(ex.map(work, jobs))
    json.dump(store, open(OUT, "w"), ensure_ascii=False)
    for n in IDX: print(f"[{n}] {sum(1 for v in store[n].values() if v)} 期", flush=True)

def swmap():
    m = {}
    cls = retry(lambda: pro().index_classify(level="L1", src="SW2021"))
    l1s = list(cls.index_code)
    cls2 = retry(lambda: pro().index_classify(level="L2", src="SW2021"))
    l2s = list(cls2.index_code) if cls2 is not None else []
    l3s = []
    cls3 = retry(lambda: pro().index_classify(level="L3", src="SW2021"))
    l3s = list(cls3.index_code) if cls3 is not None else []
    def pull(code, lname):
        df = retry(lambda: pro().index_member_all(l2_code=code))
        if df is None or not len(df): return []
        return [(str(r[7]), lname) for r in df.itertuples()]
    with ThreadPoolExecutor(max_workers=NW) as ex:
        for res in ex.map(lambda c: pull(c, None), l2s):
            for code, _ in res: pass
    # 用 l2 结果补全(带 l1 名)
    for code in l2s:
        df = retry(lambda code=code: pro().index_member_all(l2_code=code))
        if df is not None and len(df):
            for r in df.itertuples():
                m[str(r[7])] = str(r[2])   # ts_code -> l1_name
        time.sleep(0.05)
    json.dump(m, open("data/ts_sw_industry_map.json", "w"), ensure_ascii=False)
    print(f"申万映射 {len(m)} 只", flush=True)

if mode in ("macro", "all"): macro()
if mode in ("idxbasic", "all"): idxbasic()
if mode in ("weight_fix", "all"): weight_fix()
if mode in ("swmap", "all"): swmap()
print(f"{mode} 完成", flush=True)
