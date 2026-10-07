#!/usr/bin/env python3
"""用 2019 独立真值集, 判别 E大"等权"到底对应哪种聚合:
全市场列: 中位 / 中和(等权整体法) / 算术均 / 各种 cap 变体"""
import sys, os, json, collections, statistics as st, pickle as pk
sys.path.insert(0, "/vol1/1000/openzl/etf_tool/app")
os.chdir("/vol1/1000/openzl/etf_tool")
import ed_algo

FULL = "data/ed_hist_full"
have = set(f[:-5] for f in os.listdir(FULL) if f.endswith(".json"))
old = json.load(open("data/ed_truth_multi.json"))
ALIAS = {"深十100": "深证100", "深让100": "深证100", "深i正100": "深证100"}
TA = collections.defaultdict(dict)
for d, cm in old.items():
    for c, v in cm.items():
        if isinstance(v, dict) and v.get("pe"):
            TA[d][ALIAS.get(c, c)] = v["pe"]
vote = pk.load(open("/tmp/2019vote.pkl", "rb"))
TB = collections.defaultdict(dict)
for c, dm in vote.items():
    for d, vs in dm.items():
        v, cnt = collections.Counter(vs).most_common(1)[0]
        if cnt >= 2:
            TB[d][c] = v

cfg = ed_algo.load_config()
print("全市场 配置:", json.dumps(cfg.get("全市场"), ensure_ascii=False))
print("全市场加权 配置:", json.dumps(cfg.get("全市场加权"), ensure_ascii=False))

need = sorted({d for d in list(TA) + list(TB) if d in have})
SNAP = {}
for d in need:
    with open(f"{FULL}/{d}.json") as f:
        j = json.load(f)
    SNAP[d] = {c: {"PE_TTM": v[0], "PE_LAR": v[1], "PB_MRQ": v[2], "TOTAL_MARKET_CAP": v[3]} for c, v in j.items()}
CTX = {}
def ctx(d):
    if d not in CTX: CTX[d] = ed_algo.build_ctx(d)
    return CTX[d]

# 直接对 pool 施加各种聚合
def agg(pool, kind):
    v = [r["PE_TTM"] for r in pool if isinstance(r.get("PE_TTM"), (int, float)) and r["PE_TTM"] > 0]
    if len(v) < 5: return None
    if kind == "中位": return st.median(v)
    if kind == "算术均": return sum(v)/len(v)
    if kind == "调和": return len(v)/sum(1/x for x in v)
    if kind == "中位cap200": 
        w=[x for x in v if x<=200]; return st.median(w) if len(w)>=5 else None
    if kind == "调和cap200":
        w=[x for x in v if x<=200]; return len(w)/sum(1/x for x in w) if len(w)>=5 else None
    if kind == "调和cap500":
        w=[x for x in v if x<=500]; return len(w)/sum(1/x for x in w) if len(w)>=5 else None
    if kind == "中位cap500":
        w=[x for x in v if x<=500]; return st.median(w) if len(w)>=5 else None
    if kind == "加权整体":
        num = sum((r["TOTAL_MARKET_CAP"] or 0) for r in pool if isinstance(r.get("PE_TTM"),(int,float)) and r["PE_TTM"]>0)
        den = sum((r["TOTAL_MARKET_CAP"] or 0)/r["PE_TTM"] for r in pool if isinstance(r.get("PE_TTM"),(int,float)) and r["PE_TTM"]>0)
        return num/den if den else None

def evaluate(name, kind, TR):
    devs = []
    for d in sorted(TR):
        if name not in TR[d] or d not in SNAP: continue
        mode = (cfg.get(name) or {}).get("list", "current")
        p = ed_algo.pool(name, SNAP[d], ctx(d), mode)
        v = agg(p, kind)
        if v: devs.append(abs((v - TR[d][name]) / TR[d][name] * 100))
    return devs

for name in ("全市场", "全市场加权", "创业板综"):
    print(f"\n{'='*70}\n【{name}】  (list={(cfg.get(name) or {}).get('list')})")
    for kind in ("中位", "调和", "算术均", "中位cap200", "调和cap200", "中位cap500", "调和cap500", "加权整体"):
        a = evaluate(name, kind, TA); b = evaluate(name, kind, TB)
        sa = f"旧 n={len(a):3d} 均{sum(a)/len(a):6.2f}% 中{st.median(a):6.2f}%" if a else "旧 --"
        sb = f"19 n={len(b):3d} 均{sum(b)/len(b):6.2f}% 中{st.median(b):6.2f}%" if b else "19 --"
        print(f"  {kind:12s} {sa}  |  {sb}", flush=True)
