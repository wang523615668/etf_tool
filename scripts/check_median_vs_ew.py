#!/usr/bin/env python3
"""E大估值口径体检: ①中位数 vs 等权(调和/算术/截尾) ②名单完整性

用途：回答"某指数和E大偏差大，是不是成份股不全"和"中位改等权会差多少"。
真值来源：data/ed_truth_ocr.txt（E大邮件表 OCR，2022-12-01~12-20 五个全列日 + 2023-01-06 留出日）。
用法：python3 scripts/check_median_vs_ew.py [指数名...]
"""
import json, statistics as st, sys, os
sys.path.insert(0, "/vol1/1000/openzl/etf_tool/app")
import ed_algo

B = "/vol1/1000/openzl/etf_tool"
# E大真值 PE（摘自 ed_truth_ocr.txt；2023-01-06 作留出检验日，选优时不要用）
TRUTH = {
    "2022-12-01": {"全市场":30.41,"上证50":17.00,"沪深300":21.11,"中证500":24.19,"上证180":20.10,"深证100":25.04,"科创50":54.98,"创业板综":38.52,"创业板指":34.82,"全指医药":35.67,"全指金融":13.96,"全指消费":36.68,"中证环保":31.29,"全指信息":44.96,"养老产业":27.95,"中证1000":31.92,"全市场加权":23.33,"食品饮料":40.78,"中证红利":10.01,"中证军工":55.01,"中证医疗":35.54},
    "2022-12-05": {"全市场":30.46,"上证50":17.32,"沪深300":21.53,"中证500":24.60,"上证180":20.45,"深证100":25.21,"科创50":54.40,"创业板综":39.25,"创业板指":34.68,"全指医药":35.59,"全指金融":14.48,"全指消费":37.68,"中证环保":31.23,"全指信息":45.33,"养老产业":28.88,"中证1000":32.08,"全市场加权":23.57,"食品饮料":42.74,"中证红利":10.21,"中证军工":54.82,"中证医疗":35.53},
    "2022-12-12": {"全市场":29.88,"上证50":16.50,"沪深300":20.93,"中证500":25.38,"上证180":18.11,"深证100":26.75,"科创50":59.90,"创业板综":38.76,"创业板指":38.31,"全指医药":36.70,"全指金融":15.52,"全指消费":37.88,"中证环保":30.24,"全指信息":47.33,"养老产业":29.05,"中证1000":32.00,"全市场加权":23.21,"食品饮料":42.30,"中证红利":8.06,"中证军工":54.27,"中证医疗":33.66},
    "2022-12-19": {"全市场":28.82,"上证50":16.40,"沪深300":20.21,"中证500":24.31,"上证180":17.75,"深证100":26.82,"科创50":56.42,"创业板综":36.99,"创业板指":36.76,"全指医药":34.47,"全指金融":15.12,"全指消费":37.56,"中证环保":29.59,"全指信息":44.78,"养老产业":28.65,"中证1000":30.62,"全市场加权":22.50,"食品饮料":42.03,"中证红利":7.87,"中证军工":52.29,"中证医疗":31.83},
    "2022-12-20": {"全市场":28.56,"上证50":16.15,"沪深300":20.04,"中证500":24.04,"上证180":17.68,"深证100":25.63,"科创50":56.91,"创业板综":37.17,"创业板指":36.72,"全指医药":33.99,"全指金融":14.88,"全指消费":36.02,"中证环保":29.46,"全指信息":44.67,"养老产业":27.97,"中证1000":30.28,"全市场加权":22.34,"食品饮料":41.31,"中证红利":7.79,"中证军工":52.12,"中证医疗":31.36},
    "2023-01-06": {"全市场":29.41,"上证50":16.81,"沪深300":20.79,"中证500":24.96,"上证180":17.99,"深证100":28.25,"科创50":56.28,"创业板综":37.83,"创业板指":37.06,"全指医药":34.78,"全指金融":14.64,"全指消费":37.99,"中证环保":29.01,"全指信息":46.89,"养老产业":29.28,"中证1000":30.90,"全市场加权":22.78,"食品饮料":43.28,"中证红利":7.93,"中证军工":53.20,"中证医疗":34.40},
}
HOLDOUT = "2023-01-06"
P = ed_algo._pos
def med(p):
    v = [x for x in (P(r.get("PE_TTM")) for r in p) if x]; return st.median(v) if v else None
def med_all(p):
    v = [r["PE_TTM"] for r in p if isinstance(r.get("PE_TTM"), (int, float))]; return st.median(v) if v else None
def harm(p):
    v = [x for x in (P(r.get("PE_TTM")) for r in p) if x]; return len(v)/sum(1/x for x in v) if v else None
def amean(p):
    v = [x for x in (P(r.get("PE_TTM")) for r in p) if x]; return st.mean(v) if v else None
def trim(p, fr=0.2):
    v = sorted(x for x in (P(r.get("PE_TTM")) for r in p) if x)
    if len(v) < 10: return None
    k = max(1, int(len(v)*fr)); return st.mean(v[k:-k] or v)
ALG = {"中位TTM剔亏": med, "中位TTM含亏": med_all, "等权整体法(调和)": harm,
       "等权均值(算术)": amean, "截尾20%均TTM": trim}

def snap_of(d):
    raw = json.load(open(f"{B}/data/ed_hist_full/{d}.json"))
    return {c: {"PE_TTM": v[0], "PE_LAR": v[1], "PB_MRQ": v[2], "TOTAL_MARKET_CAP": v[3]}
            for c, v in raw.items()}

def pool_stats(name, date="2022-12-01"):
    """名单完整性: 池子规模 / 有PE / PE>0 / 该日as-of名单里缺快照的代码

    注意: 不要拿"今天"的成分名单去比历史快照(2026成员2022当然不存在)，
    要比的是该指数**当日**的 as-of 名单(理杏仁/新浪/baostock)。
    """
    raw = json.load(open(f"{B}/data/ed_hist_full/{date}.json"))
    snap = snap_of(date); ctx = ed_algo.build_ctx(date)
    cfg = ed_algo.load_config().get(name) or {}
    mode = cfg.get("list", "current")
    p = ed_algo.pool(name, snap, ctx, mode)
    # 该日 as-of 名单长度及其在快照中的覆盖率
    asof_n, miss = None, []
    if mode == "lixinger":
        j = (ed_algo._lix().get(name) or {}).get("asof") or {}
        ds = [d for d in j if d <= date]
        if ds:
            codes = j[max(ds)]; asof_n = (max(ds), len(codes))
            miss = [c for c in codes if c not in raw]
    elif mode == "current":
        cur = json.load(open(f"{B}/data/ed_cons_current.json"))
        codes = cur.get(name) or []
        miss = [c for c in codes if c not in raw]
    return {"algo": cfg.get("algo"), "list": mode, "pool": len(p),
            "has_pe": sum(1 for r in p if isinstance(r.get("PE_TTM"), (int, float))),
            "pe_pos": sum(1 for r in p if P(r.get("PE_TTM"))),
            "asof": asof_n, "缺快照": miss}

def compare(name, dates=None):
    """中位 vs 等权: 返回各口径的|偏差|中位与中位-调和数值差"""
    dates = dates or [d for d in TRUTH if d != HOLDOUT]
    mode = (ed_algo.load_config().get(name) or {}).get("list", "current")
    out = {}
    for an, fn in ALG.items():
        devs, vals = [], {}
        for d in dates:
            p = ed_algo.pool(name, snap_of(d), ed_algo.build_ctx(d), mode)
            if not p: continue
            v = fn(p); t = TRUTH[d].get(name)
            if v is None or t is None: continue
            devs.append(abs((v-t)/t*100)); vals[d] = v
        out[an] = {"dev": st.median(devs) if devs else None, "vals": vals}
    gaps = [ (out["等权整体法(调和)"]["vals"][d] - out["中位TTM剔亏"]["vals"][d]) / out["中位TTM剔亏"]["vals"][d] * 100
             for d in dates if d in out["等权整体法(调和)"]["vals"] and d in out["中位TTM剔亏"]["vals"] ]
    out["_中位vs调和数值差%"] = st.median(gaps) if gaps else None
    return out

def holdout_check(name, mode, algo):
    p = ed_algo.pool(name, snap_of(HOLDOUT), ed_algo.build_ctx(HOLDOUT), mode)
    v = ALG[algo](p) if p else None
    t = TRUTH[HOLDOUT][name]
    return None if v is None else round((v-t)/t*100, 1)

if __name__ == "__main__":
    names = sys.argv[1:] or ["沪深300","中证500","上证180","中证1000","全指医药","全指信息",
                             "中证红利","中证军工","中证医疗","创业板指","全指消费","中证环保","科创50"]
    print("【名单完整性】(2022-12-01)  —— 池子=当日as-of名单∩快照")
    for n in names:
        s = pool_stats(n)
        extra = f" as-of名单={s['asof'][0]}的{len(ed_algo._lix().get(n,{}).get('asof',{}).get(s['asof'][0],[]))}只" if s.get("asof") else ""
        print(f"  {n:<9} 配置={str(s['algo']):<15}{s['list']:<9} 池={s['pool']:>5} PE>0={s['pe_pos']:>5}"
              f" 名单缺快照={len(s['缺快照'])}只{extra}")
    print(f"\n【中位 vs 等权】|偏差|中位%（5真值日）  留出日={HOLDOUT}")
    for n in names:
        c = compare(n)
        g = c["_中位vs调和数值差%"]
        print(f"  {n:<9} 中位={c['中位TTM剔亏']['dev']:5.2f}%  等权调和={c['等权整体法(调和)']['dev']:6.2f}%  "
              f"等权均值={c['等权均值(算术)']['dev']:7.2f}%  截尾20%={c['截尾20%均TTM']['dev']:6.2f}%  "
              f"中位比等权{'低' if g and g<0 else '高'}{abs(g or 0):.1f}%")
