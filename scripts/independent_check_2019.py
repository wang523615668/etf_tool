#!/usr/bin/env python3
"""独立盲测: 每列现有配置 vs 备选口径, 在【旧真值】与【2019全年新真值】上各测一次。
2019 年真值来自 E大 2017-2019 邮件合集文字层(pdftotext), 未参与过任何参数选择。"""
import sys, os, json, collections, statistics as st, pickle as pk
sys.path.insert(0, "/vol1/1000/openzl/etf_tool/app")
os.chdir("/vol1/1000/openzl/etf_tool")
import ed_algo

FULL = "data/ed_hist_full"
have = sorted(f[:-5] for f in os.listdir(FULL) if f.endswith(".json"))

# ---- 真值A: 旧真值(124日, 2019-12~2023-03, 源自 2020/2023 表 OCR) ----
old = json.load(open("data/ed_truth_multi.json"))
ALIAS = {"深十100": "深证100", "深让100": "深证100", "深i正100": "深证100"}
TA = collections.defaultdict(dict)
for d, cm in old.items():
    for c, v in cm.items():
        if isinstance(v, dict) and v.get("pe"):
            TA[d][ALIAS.get(c, c)] = v["pe"]

# ---- 真值B: 2019 全年(独立盲测集, 同日期多次出现投票, >=2票才收) ----
vote = pk.load(open("/tmp/2019vote.pkl", "rb"))
TB = collections.defaultdict(dict)
for c, dm in vote.items():
    for d, vs in dm.items():
        v, cnt = collections.Counter(vs).most_common(1)[0]
        if cnt >= 2:
            TB[d][c] = v

cfg = ed_algo.load_config()
cols = sorted({c for d in TB for c in TB[d]} | {c for d in TA for c in TA[d]})

# ---- 快照缓存 ----
need = sorted({d for d in list(TA) + list(TB) if d in set(have)})
print(f"快照装载 {len(need)} 天 ...", flush=True)
SNAP = {}
for d in need:
    with open(f"{FULL}/{d}.json") as f:
        j = json.load(f)
    SNAP[d] = {c: {"PE_TTM": v[0], "PE_LAR": v[1], "PB_MRQ": v[2], "TOTAL_MARKET_CAP": v[3]} for c, v in j.items()}
CTX = {}
def ctx(d):
    if d not in CTX:
        CTX[d] = ed_algo.build_ctx(d)
    return CTX[d]

def dev(col, cfgs, TR):
    out = []
    for d in sorted(TR):
        if col not in TR[d] or d not in SNAP:
            continue
        pe, _, _ = ed_algo.series_value(SNAP[d], col, {col: cfgs}, ctx(d))
        if pe:
            out.append(abs((pe - TR[d][col]) / TR[d][col] * 100))
    return out

def show(name, cfgs):
    a = dev(name, cfgs, TA); b = dev(name, cfgs, TB)
    sa = f"n={len(a):3d} 均{sum(a)/len(a):5.2f}% 中{st.median(a):5.2f}%" if a else "        --              "
    sb = f"n={len(b):3d} 均{sum(b)/len(b):5.2f}% 中{st.median(b):5.2f}%" if b else "        --              "
    tag = cfgs.get("algo", "?") + (f"·{cfgs['list']}" if cfgs.get("list") else "") + (f"·cap{cfgs['pe_cap']}" if cfgs.get("pe_cap") else "")
    print(f"  {tag:34s} 旧:{sa}   |  19:{sb}", flush=True)

print("\n" + "=" * 118)
print("逐列: 现有配置 / 备选口径  在 旧真值A 与 2019独立集B 上的偏差")
print("=" * 118)
for col in cols:
    cur = cfg.get(col)
    if not cur:
        continue
    print(f"\n【{col}】 现有配置 = {json.dumps(cur, ensure_ascii=False)}")
    show(col, dict(cur))
    base_list = cur.get("list", "current")
    for alt in ({"algo": "中位TTM剔亏", "list": base_list},
                {"algo": "中位TTM剔亏", "list": "current"},
                {"algo": cur.get("algo"), "list": "current"},
                {"algo": "截尾10%均TTM", "list": base_list},
                {"algo": "中位TTM剔亏", "list": "lixinger"}):
        if alt == dict(cur) or (alt.get("algo") == cur.get("algo") and alt.get("list") == cur.get("list")):
            continue
        show(col, alt)
    if cur.get("pe_cap"):
        nc = {k: v for k, v in cur.items() if k != "pe_cap"}
        print("  ↓ 去掉 pe_cap(验证剔除微利股规则)")
        show(col, nc)
