#!/usr/bin/env python3
"""预计算: 主动基金重仓股行业配置时序(申万一级) → data/ts_fund_industry.json
结构: {"updated": ..., "quarters": {
    "20260630": {"n_funds": 5106, "mkv": 34466e8, "pct": {行业: 占比}},
    ...}}
重点组: 医药生物 / 科技(电子+计算机+通信+传媒) / 消费 / 金融 / 周期 等
"""
import json, os
from collections import defaultdict
os.chdir("/vol1/1000/openzl/etf_tool")
fp = json.load(open("data/ts_fund_portfolio.json"))
ind = json.load(open("data/ts_sw_industry_map.json"))

GROUPS = {
    "科技": ("电子", "计算机", "通信", "传媒"),
    "医药": ("医药生物",),
    "消费": ("食品饮料", "家用电器", "纺织服饰", "商贸零售", "社会服务", "美容护理", "农林牧渔"),
    "金融": ("银行", "非银金融",),
    "制造": ("电力设备", "机械设备", "汽车", "国防军工",),
    "周期": ("基础化工", "钢铁", "有色金属", "煤炭", "石油石化", "建筑材料",),
}
per = defaultdict(lambda: defaultdict(float))
nf = defaultdict(int); tot = defaultdict(float)
for c, q in fp.items():
    for d, rows in q.items():
        if not rows: continue
        nf[d] += 1
        for sym, mkv, ratio in rows:
            if sym.endswith(".HK"): continue
            per[d][ind.get(sym, "其他")] += mkv
            tot[d] += mkv

out = {"updated": "2026-10-07", "quarters": {}}
for d in sorted(per):
    t = tot[d]
    if t < 1e9 or nf[d] < 100: continue
    pct = {k: round(v / t * 100, 2) for k, v in sorted(per[d].items(), key=lambda x: -x[1]) if v / t > 0.05}
    groups = {g: round(sum(per[d].get(i, 0) for i in members) / t * 100, 2) for g, members in GROUPS.items()}
    out["quarters"][d] = {"n_funds": nf[d], "mkv_yi": round(t / 1e8, 0), "pct": pct, "groups": groups}

# 摘要: 医药/科技的历史高低
med = [(d, v["groups"]["医药"]) for d, v in out["quarters"].items()]
if med:
    out["summary"] = {
        "range": [out["quarters"][med[0][0]] and med[0][0], med[-1][0]],
        "med_min": min(med, key=lambda x: x[1]), "med_max": max(med, key=lambda x: x[1]),
        "tech_max": max(((d, v["groups"]["科技"]) for d, v in out["quarters"].items()), key=lambda x: x[1]),
    }
json.dump(out, open("data/ts_fund_industry.json", "w"), ensure_ascii=False)
print(f"完成: {len(out['quarters'])} 个季度 → data/ts_fund_industry.json")
print("医药:", out.get("summary", {}).get("med_min"), "→", out.get("summary", {}).get("med_max"))
