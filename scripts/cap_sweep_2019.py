#!/usr/bin/env python3
"""cap 阈值稳健性: 在【旧真值】与【2019独立集】上分别扫阈值"""
import sys, os, json, collections, statistics as st, pickle as pk
sys.path.insert(0, "/vol1/1000/openzl/etf_tool/app")
os.chdir("/vol1/1000/openzl/etf_tool")
import ed_algo
FULL = "data/ed_hist_full"; have = set(f[:-5] for f in os.listdir(FULL) if f.endswith(".json"))
old = json.load(open("data/ed_truth_multi.json"))
TA = collections.defaultdict(dict)
for d, cm in old.items():
    for c, v in cm.items():
        if isinstance(v, dict) and v.get("pe"): TA[d][c] = v["pe"]
vote = pk.load(open("/tmp/2019vote.pkl", "rb"))
TB = collections.defaultdict(dict)
for c, dm in vote.items():
    for d, vs in dm.items():
        v, cnt = collections.Counter(vs).most_common(1)[0]
        if cnt >= 2: TB[d][c] = v
need = sorted({d for d in list(TA) + list(TB) if d in have})
SNAP = {}
for d in need:
    with open(f"{FULL}/{d}.json") as f: j = json.load(f)
    SNAP[d] = {c: {"PE_TTM": v[0], "PE_LAR": v[1], "PB_MRQ": v[2], "TOTAL_MARKET_CAP": v[3]} for c, v in j.items()}
CTX = {}
def ctx(d):
    if d not in CTX: CTX[d] = ed_algo.build_ctx(d)
    return CTX[d]
CFG = {"创业板综": {"algo": "中位TTM剔亏", "list": "current"},
       "全市场加权": {"algo": "等权整体法(调和)", "list": "current"}}
def run(name, cap, TR):
    devs = []
    for d in sorted(TR):
        if name not in TR[d] or d not in SNAP: continue
        c = dict(CFG[name]);
        if cap: c["pe_cap"] = cap
        pe, _, _ = ed_algo.series_value(SNAP[d], name, {name: c}, ctx(d))
        if pe: devs.append(abs((pe - TR[d][name]) / TR[d][name] * 100))
    return devs
for name in CFG:
    print(f"\n【{name}】 cap 阈值扫描")
    for cap in (None, 150, 175, 200, 225, 250, 300):
        a = run(name, cap, TA); b = run(name, cap, TB)
        sa = f"旧 n={len(a):3d} 均{sum(a)/len(a):6.2f}% 中{st.median(a):6.2f}%" if a else "旧--"
        sb = f"19 n={len(b):3d} 均{sum(b)/len(b):6.2f}% 中{st.median(b):6.2f}%" if b else "19--"
        print(f"  cap={str(cap):5s} {sa} | {sb}", flush=True)
