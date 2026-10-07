#!/usr/bin/env python3
"""tushare中转站(jiaoch.top)全量拉取: 25个指数 按月 index_weight(历史成分+权重) 2015-2026
输出: data/ts_index_weight.json {指数名: {月份日: {con_code: weight}}}"""
import json, os, sys, time
import tushare as ts
os.chdir("/vol1/1000/openzl/etf_tool")
pro = ts.pro_api('90861eb43672f06ce6c5b3a890471d0bb8bf5be3fc749280485d2364')
pro._DataApi__http_url = "https://jiaoch.top/"
OUT = "data/ts_index_weight.json"
store = json.load(open(OUT)) if os.path.exists(OUT) else {}

IDX = {"上证50":"000016.SH","上证180":"000010.SH","沪深300":"000300.SH","中证500":"000905.SH",
       "中证1000":"000852.SH","中证A500":"000510.SH","科创50":"000688.SH","创业板指":"399006.SZ",
       "中证红利":"000922.CSI","中证军工":"399967.SZ","中证医疗":"399989.SZ","中证环保":"000827.SH",
       "中证传媒":"399971.SZ","养老产业":"399812.SZ","全指医药":"000991.SH","全指消费":"000990.SH",
       "全指信息":"000993.SH","全指金融":"000992.SH","食品饮料":"000807.CSI","中证白酒":"399997.SZ",
       "证券公司":"399975.SZ","中证银行":"399986.SZ","红利低波100":"930955.CSI","创业板综":"399102.SZ"}

YEARS = [(y, m) for y in range(2015, 2027) for m in range(1, 13)]
def month_range(y, m):
    import datetime as dt
    s = dt.date(y, m, 1)
    e = (dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1))
    return s.strftime("%Y%m%d"), e.strftime("%Y%m%d")

total = 0
for name, code in IDX.items():
    if name not in store: store[name] = {}
    got = 0
    for y, m in YEARS:
        if (y, m) > (2026, 9): break
        key = f"{y}-{m:02d}"
        if store[name].get(key): continue
        sd, ed = month_range(y, m)
        try:
            df = pro.index_weight(index_code=code, start_date=sd, end_date=ed)
        except Exception as e:
            print(f"  {name} {key} 异常 {str(e)[:60]}", flush=True); time.sleep(1); continue
        if df is not None and len(df):
            # 该月最后一个快照日
            last = sorted(df.trade_date.unique())[-1]
            sub = df[df.trade_date == last]
            store[name][key] = {str(r[1]): float(r[3]) for r in sub.itertuples()}
            got += 1; total += 1
        else:
            store[name][key] = None
        json.dump(store, open(OUT, "w"), ensure_ascii=False)
        time.sleep(0.12)
    n_ok = sum(1 for v in store[name].values() if v)
    print(f"[{name}] 有效 {n_ok} 期 (本次新增 {got})", flush=True)

print(f"\n完成 → {OUT}, 总计 {total} 期新增")
