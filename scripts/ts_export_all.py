#!/usr/bin/env python3
"""tushare中转站(jiaoch.top) 批量落盘
用法: ts_export_all.py <mode>
  weight_early : 各指数 2004-2014 早期成分权重(补全历史)
  funds        : 主动权益基金 逐季十大重仓股(全历史) -> data/ts_fund_portfolio.json
  flows        : ETF份额 + 两融 + 北向 + 指数官方估值
"""
import json, os, sys, time
import tushare as ts
os.chdir("/vol1/1000/openzl/etf_tool")
pro = ts.pro_api('90861eb43672f06ce6c5b3a890471d0bb8bf5be3fc749280485d2364')
pro._DataApi__http_url = "https://jiaoch.top/"
mode = sys.argv[1] if len(sys.argv) > 1 else "weight_early"

IDX = {"上证50":"000016.SH","上证180":"000010.SH","沪深300":"000300.SH","中证500":"000905.SH",
       "中证1000":"000852.SH","中证A500":"000510.SH","科创50":"000688.SH","创业板指":"399006.SZ",
       "中证红利":"000922.CSI","中证军工":"399967.SZ","中证医疗":"399989.SZ","中证环保":"000827.SH",
       "中证传媒":"399971.SZ","养老产业":"399812.SZ","全指医药":"000991.SH","全指消费":"000990.SH",
       "全指信息":"000993.SH","全指金融":"000992.SH","食品饮料":"000807.CSI","中证白酒":"399997.SZ",
       "证券公司":"399975.SZ","中证银行":"399986.SZ","红利低波100":"930955.CSI","创业板综":"399102.SZ"}

def save(fp, obj):
    json.dump(obj, open(fp, "w"), ensure_ascii=False)

def month_range(y, m):
    import datetime as dt
    s = dt.date(y, m, 1)
    e = dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1)
    return s.strftime("%Y%m%d"), e.strftime("%Y%m%d")

# ---------------- mode: weight_early ----------------
if mode == "weight_early":
    OUT = "data/ts_index_weight.json"
    store = json.load(open(OUT)) if os.path.exists(OUT) else {}
    for name, code in IDX.items():
        store.setdefault(name, {})
        added = 0
        for y in range(2004, 2015):
            for m in range(1, 13):
                key = f"{y}-{m:02d}"
                if store[name].get(key): continue
                sd, ed = month_range(y, m)
                try:
                    df = pro.index_weight(index_code=code, start_date=sd, end_date=ed)
                except Exception:
                    time.sleep(1); continue
                if df is not None and len(df):
                    last = sorted(df.trade_date.unique())[-1]
                    sub = df[df.trade_date == last]
                    store[name][key] = {str(r[1]): float(r[3]) for r in sub.itertuples()}
                    added += 1
                else:
                    store[name].setdefault(key, None)
                save(OUT, store)
                time.sleep(0.1)
        ok = sum(1 for v in store[name].values() if v)
        print(f"[{name}] 累计 {ok} 期 (早期新增 {added})", flush=True)
    print("weight_early 完成")

# ---------------- mode: funds ----------------
elif mode == "funds":
    OUT = "data/ts_fund_portfolio.json"
    store = json.load(open(OUT)) if os.path.exists(OUT) else {}
    fb = pro.fund_basic(market="O", fields="ts_code,name,fund_type,invest_type,found_date,status")
    act = fb[(fb.fund_type.isin(["股票型", "混合型"])) &
             (~fb.invest_type.astype(str).str.contains("被动|指数|ETF|联接"))]
    codes = sorted(act.ts_code.unique())
    print(f"主动权益基金 {len(codes)} 只", flush=True)
    for i, c in enumerate(codes, 1):
        if store.get(c): continue
        try:
            df = pro.fund_portfolio(ts_code=c)
        except Exception:
            time.sleep(1); continue
        if df is not None and len(df):
            g = {}
            for r in df.itertuples():
                g.setdefault(str(r[3]), []).append([str(r[4]), float(r[5] or 0), float(r[6] or 0)])
            store[c] = g
        else:
            store[c] = {}
        if i % 25 == 0:
            save(OUT, store); print(f"  {i}/{len(codes)} …", flush=True)
        time.sleep(0.08)
    save(OUT, store)
    print(f"funds 完成: {len(store)} 只基金 → {OUT}")

# ---------------- mode: flows ----------------
elif mode == "flows":
    # 1) ETF 份额(每日, 全历史)
    OUT = "data/ts_fund_share.json"
    store = json.load(open(OUT)) if os.path.exists(OUT) else {}
    fb = pro.fund_basic(market="E", fields="ts_code,name,fund_type,list_date,status")
    etfs = sorted(fb.ts_code.unique())
    print(f"ETF/场内基金 {len(etfs)} 只", flush=True)
    for i, c in enumerate(etfs, 1):
        if store.get(c): continue
        try:
            df = pro.fund_share(ts_code=c)
        except Exception:
            time.sleep(1); continue
        if df is not None and len(df):
            store[c] = {str(r[1]): float(r[2]) for r in df.itertuples()}
        else:
            store[c] = {}
        if i % 50 == 0:
            json.dump(store, open(OUT, "w"), ensure_ascii=False); print(f"  ETF {i}/{len(etfs)}", flush=True)
        time.sleep(0.06)
    json.dump(store, open(OUT, "w"), ensure_ascii=False)
    print(f"ETF份额完成 {len(store)} 只", flush=True)
    # 2) 两融(每日)
    m = pro.margin(start_date="20100101", end_date="20261231")
    json.dump(m.to_dict("records"), open("data/ts_margin.json", "w"), ensure_ascii=False, default=str)
    print("两融:", len(m), "行", flush=True)
    # 3) 北向
    h = pro.moneyflow_hsgt(start_date="20140101", end_date="20261231")
    json.dump(h.to_dict("records"), open("data/ts_hsgt.json", "w"), ensure_ascii=False, default=str)
    print("北向:", len(h), "行", flush=True)
    # 4) 指数官方估值(逐日)
    out2 = {}
    for name, code in IDX.items():
        try:
            df = pro.index_dailybasic(ts_code=code, start_date="20050101", end_date="20261231",
                                      fields="trade_date,pe,pe_ttm,pb,total_mv,float_mv")
        except Exception as e:
            print(f"  {name} 失败 {str(e)[:60]}", flush=True); continue
        if df is not None and len(df):
            out2[name] = {str(r[0]): [r[1], r[2], r[3]] for r in df.itertuples()}
            print(f"  {name}: {len(df)} 天", flush=True)
        time.sleep(0.15)
    json.dump(out2, open("data/ts_index_dailybasic.json", "w"), ensure_ascii=False)
    print("flows 完成")
