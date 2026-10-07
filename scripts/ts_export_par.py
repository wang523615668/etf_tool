#!/usr/bin/env python3
"""tushare中转站(jiaoch.top) 并行批量落盘 (8线程, 实测安全上限)
用法: ts_export_par.py <weight_early|funds|flows>
"""
import json, os, sys, time, threading
import tushare as ts
from concurrent.futures import ThreadPoolExecutor
os.chdir("/vol1/1000/openzl/etf_tool")
TOKEN = [l.split("=", 1)[1].strip() for l in open("/root/.hermes/.env") if l.startswith("TS_TOKEN=")]
TOKEN = TOKEN[0] if TOKEN else "90861eb43672f06ce6c5b3a890471d0bb8bf5be3fc749280485d2364"
NW = 8
_tl = threading.local()
def pro():
    if not hasattr(_tl, "p"):
        _tl.p = ts.pro_api(TOKEN); _tl.p._DataApi__http_url = "https://jiaoch.top/"
    return _tl.p

mode = sys.argv[1]
IDX = {"上证50":"000016.SH","上证180":"000010.SH","沪深300":"000300.SH","中证500":"000905.SH",
       "中证1000":"000852.SH","中证A500":"000510.SH","科创50":"000688.SH","创业板指":"399006.SZ",
       "中证红利":"000922.CSI","中证军工":"399967.SZ","中证医疗":"399989.SZ","中证环保":"000827.SH",
       "中证传媒":"399971.SZ","养老产业":"399812.SZ","全指医药":"000991.SH","全指消费":"000990.SH",
       "全指信息":"000993.SH","全指金融":"000992.SH","食品饮料":"000807.CSI","中证白酒":"399997.SZ",
       "证券公司":"399975.SZ","中证银行":"399986.SZ","红利低波100":"930955.CSI","创业板综":"399102.SZ"}

def mrange(y, m):
    import datetime as dt
    s = dt.date(y, m, 1); e = dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1)
    return s.strftime("%Y%m%d"), e.strftime("%Y%m%d")

def retry(fn, n=3, delay=1.5):
    for i in range(n):
        try: return fn()
        except Exception:
            time.sleep(delay * (i + 1))
    return None

# ---------- weight_early: 2004-2014 各指数按月 ----------
if mode == "weight_early":
    OUT = "data/ts_index_weight.json"
    store = json.load(open(OUT)) if os.path.exists(OUT) else {}
    jobs = []
    for name, code in IDX.items():
        store.setdefault(name, {})
        for y in range(2004, 2015):
            for m in range(1, 13):
                k = f"{y}-{m:02d}"
                if store[name].get(k) is None and not store[name].get(k):
                    jobs.append((name, code, k, y, m))
    print(f"待抓 {len(jobs)} 期 (8线程)", flush=True)
    lock = threading.Lock(); done = [0]
    def work(j):
        name, code, k, y, m = j
        sd, ed = mrange(y, m)
        df = retry(lambda: pro().index_weight(index_code=code, start_date=sd, end_date=ed))
        val = None
        if df is not None and len(df):
            last = sorted(df.trade_date.unique())[-1]
            sub = df[df.trade_date == last]
            val = {str(r[1]): float(r[3]) for r in sub.itertuples()}
        with lock:
            store[name][k] = val if val else None
            done[0] += 1
            if done[0] % 100 == 0:
                json.dump(store, open(OUT, "w"), ensure_ascii=False)
                print(f"  {done[0]}/{len(jobs)}", flush=True)
    with ThreadPoolExecutor(max_workers=NW) as ex: list(ex.map(work, jobs))
    json.dump(store, open(OUT, "w"), ensure_ascii=False)
    for name in IDX:
        ok = sum(1 for v in store[name].values() if v)
        print(f"[{name}] 累计 {ok} 期", flush=True)
    print("weight_early 完成", flush=True)

# ---------- funds: 主动权益基金 逐季重仓股 ----------
elif mode == "funds":
    OUT = "data/ts_fund_portfolio.json"
    store = json.load(open(OUT)) if os.path.exists(OUT) else {}
    fb = pro().fund_basic(market="O", fields="ts_code,name,fund_type,invest_type,found_date")
    act = fb[(fb.fund_type.isin(["股票型", "混合型"])) &
             (~fb.invest_type.astype(str).str.contains("被动|指数|ETF|联接"))]
    codes = [c for c in sorted(act.ts_code.unique()) if not store.get(c)]
    print(f"主动权益基金待抓 {len(codes)} 只 (8线程)", flush=True)
    lock = threading.Lock(); done = [0]
    def work(c):
        df = retry(lambda: pro().fund_portfolio(ts_code=c))
        g = {}
        if df is not None and len(df):
            for r in df.itertuples():
                g.setdefault(str(r[3]), []).append([str(r[4]), float(r[5] or 0), float(r[6] or 0)])
        with lock:
            store[c] = g
            done[0] += 1
            if done[0] % 200 == 0:
                json.dump(store, open(OUT, "w"), ensure_ascii=False)
                print(f"  基金 {done[0]}/{len(codes)}", flush=True)
    with ThreadPoolExecutor(max_workers=NW) as ex: list(ex.map(work, codes))
    json.dump(store, open(OUT, "w"), ensure_ascii=False)
    print(f"funds 完成: {len(store)} 只 → {OUT}", flush=True)

# ---------- flows: ETF份额(并行) + 两融/北向/指数估值(单次) ----------
elif mode == "flows":
    OUT = "data/ts_fund_share.json"
    store = json.load(open(OUT)) if os.path.exists(OUT) else {}
    fb = pro().fund_basic(market="E", fields="ts_code,name,fund_type,list_date")
    etfs = [c for c in sorted(fb.ts_code.unique()) if not store.get(c)]
    print(f"场内基金待抓 {len(etfs)} 只", flush=True)
    lock = threading.Lock(); done = [0]
    def work(c):
        df = retry(lambda: pro().fund_share(ts_code=c))
        v = {}
        if df is not None and len(df):
            v = {str(r[1]): float(r[2]) for r in df.itertuples()}
        with lock:
            store[c] = v; done[0] += 1
            if done[0] % 200 == 0:
                json.dump(store, open(OUT, "w"), ensure_ascii=False)
                print(f"  ETF {done[0]}/{len(etfs)}", flush=True)
    with ThreadPoolExecutor(max_workers=NW) as ex: list(ex.map(work, etfs))
    json.dump(store, open(OUT, "w"), ensure_ascii=False)
    print(f"ETF份额 {len(store)} 只完成", flush=True)
    for name, fn, fp in [("两融", lambda: pro().margin(start_date="20100101", end_date="20261231"), "data/ts_margin.json"),
                         ("北向", lambda: pro().moneyflow_hsgt(start_date="20140101", end_date="20261231"), "data/ts_hsgt.json")]:
        df = retry(fn)
        if df is not None and len(df):
            json.dump(df.to_dict("records"), open(fp, "w"), ensure_ascii=False, default=str)
            print(f"{name}: {len(df)} 行 → {fp}", flush=True)
        else:
            print(f"{name}: 失败/空", flush=True)
    out2 = {}
    def work2(nc):
        name, code = nc
        df = retry(lambda: pro().index_dailybasic(ts_code=code, start_date="20050101", end_date="20261231",
                                                  fields="trade_date,pe,pe_ttm,pb,total_mv,float_mv"))
        if df is not None and len(df):
            return name, {str(r[0]): [r[1], r[2], r[3]] for r in df.itertuples()}
        return name, None
    with ThreadPoolExecutor(max_workers=NW) as ex:
        for name, v in ex.map(work2, list(IDX.items())):
            if v: out2[name] = v; print(f"  指数估值 {name}: {len(v)} 天", flush=True)
    json.dump(out2, open("data/ts_index_dailybasic.json", "w"), ensure_ascii=False)
    print("flows 完成", flush=True)
