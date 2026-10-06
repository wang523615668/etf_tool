#!/usr/bin/env python3
"""回测：E大长赢150 每笔【卖出】当日，该品种的 PE/PB 历史分位是多少？
验证现行"分位≥88%才逐步卖"的阈值是否贴合他的实际行为。
分位口径：理杏仁缓存全历史（与他"五年、十年百分位"最接近的可得数据），
卖单日取"≤该日的最后一行"，分位= 历史中 ≤ 当日PE 的比例。"""
import json
from bisect import bisect_right
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
LX = ROOT / "cache" / "lixinger"
POS = ROOT / "data" / "long_win_positions.json"

# 卖出记录里出现过的基金名 → 理杏仁指数文件
NAME2IDX = {
    "华夏广发创业板": "399006",  # 占位，不用
}
def find_idx(name: str, cat: str) -> str | None:
    n = name
    if "创业板" in n: return "399006"
    if "恒生ETF联接" in n or n.startswith("华夏恒生"): return "HSI"
    if "恒生科技" in n or "HSTECH" in n or "恒科" in n: return "HSTECH"
    if "500增强" in n or "中证500" in n: return "000905"
    if "上证50" in n: return "000016"
    if "医疗" in n: return "399989"
    if "传媒" in n: return "399971"
    if "环保" in n: return "000827"
    if "证券" in n or "券商" in n: return "399975"
    if "白酒" in n: return "399997"
    if "消费" in n: return "000990"
    if "金融地产" in n: return "000992"
    if "银行" in n: return "399986"
    if "红利低波100" in n: return "930955"
    if "红利" in n: return "000922"
    if "养老" in n: return "399812"
    if "医药" in n or "全指医药" in n: return "000991"
    if "沪深300" in n or "300增强" in n: return "000300"
    if "中概" in n or "海外互联网" in n: return "H11136"
    if "科创" in n: return "000688"
    return None

IDX: dict[str, dict] = {}
def load_idx(code: str):
    if code not in IDX:
        f = LX / f"{code}.json"
        if not f.exists():
            IDX[code] = None
            return None
        rows = json.load(open(f))["rows"]
        rows.sort(key=lambda r: r["date"])
        pe_sorted = sorted(r["pe"] for r in rows if r.get("pe"))
        pb_sorted = sorted(r["pb"] for r in rows if r.get("pb"))
        IDX[code] = {
            "name": json.load(open(f))["name"],
            "by_date": {r["date"]: r for r in rows},
            "dates": [r["date"] for r in rows],
            "pe_sorted": pe_sorted,
            "pb_sorted": pb_sorted,
        }
    return IDX[code]

import datetime as dt
def le_date(dates: list[str], d: str) -> str | None:
    i = bisect_right(dates, d)
    return dates[i - 1] if i > 0 else None

def pct_of(sorted_vals, v):
    return bisect_right(sorted_vals, v) / len(sorted_vals) * 100 if sorted_vals and v else None

def main():
    d = json.load(open(POS))
    acts = d["plans"]["long_win_150"]["actions"]
    sells = [a for a in acts if a["action"] == "sell"]
    sells.sort(key=lambda a: a["date"])

    rows_out, unmapped = [], []
    for a in sells:
        idxc = find_idx(a["name"], a.get("category") or "")
        if not idxc:
            unmapped.append(f"{a['date']} {a['name']}")
            continue
        ix = load_idx(idxc)
        if not ix:
            unmapped.append(f"{a['date']} {a['name']} (无缓存{idxc})")
            continue
        dd = le_date(ix["dates"], a["date"])
        if not dd:
            continue
        r = ix["by_date"][dd]
        pe_p = pct_of(ix["pe_sorted"], r.get("pe"))
        pb_p = pct_of(ix["pb_sorted"], r.get("pb"))
        rows_out.append({
            "date": a["date"], "name": ix["name"], "cat": a.get("category"),
            "shares": a["shares"], "pe": r.get("pe"),
            "pe_pct": round(pe_p, 1) if pe_p is not None else None,
            "pb_pct": round(pb_p, 1) if pb_p is not None else None,
        })

    print(f"卖出 {len(sells)} 笔，可回测 {len(rows_out)} 笔，无法映射 {len(unmapped)} 笔")
    for u in unmapped[:12]:
        print("  unmapped:", u)

    pvs = [x["pe_pct"] for x in rows_out if x["pe_pct"] is not None]
    pbs = [x["pb_pct"] for x in rows_out if x["pe_pct"] is not None]
    pvs.sort()
    import statistics
    def q(vals, f):
        i = int(f * (len(vals) - 1))
        return vals[i]
    print("\n=== 卖出时 PE 历史分位 分布 ===")
    print(f"n={len(pvs)} min={q(pvs,0):.0f}% p10={q(pvs,.1):.0f}% p25={q(pvs,.25):.0f}% 中位={q(pvs,.5):.0f}% p75={q(pvs,.75):.0f}% p90={q(pvs,.9):.0f}% max={q(pvs,1):.0f}% 均值={statistics.mean(pvs):.0f}%")
    print("分位区间占比:")
    for lo, hi in [(0,30),(30,50),(50,60),(60,70),(70,80),(80,88),(88,95),(95,101)]:
        c = sum(1 for v in pvs if lo <= v < hi)
        print(f"  {lo:>3}-{hi:<3}%: {c:3d}笔 {c/len(pvs)*100:4.0f}%  {'█'*int(c/len(pvs)*50)}")

    print("\n=== 2020年以来 全部卖出席位 ===")
    recent = [x for x in rows_out if x["date"] >= "2020-01-01"]
    for x in recent:
        print(f"{x['date']}  {x['name']:8} PE分位{x['pe_pct']:>5.0f}%  PB分位{(x['pb_pct'] or 0):>5.0f}%")
    rp = sorted(x["pe_pct"] for x in recent if x["pe_pct"] is not None)
    if rp:
        print(f"\n2020以来: n={len(rp)} 中位={q(rp,.5):.0f}% p25={q(rp,.25):.0f}% min={rp[0]:.0f}%  ≥88%占比={sum(1 for v in rp if v>=88)/len(rp)*100:.0f}%  ≥80%占比={sum(1 for v in rp if v>=80)/len(rp)*100:.0f}%  ≥70%占比={sum(1 for v in rp if v>=70)/len(rp)*100:.0f}%")

    # 逐年中位
    print("\n=== 逐年卖出PE分位(中位/笔数) ===")
    byyear = defaultdict(list)
    for x in rows_out:
        if x["pe_pct"] is not None:
            byyear[x["date"][:4]].append(x["pe_pct"])
    for y in sorted(byyear):
        vs = sorted(byyear[y])
        print(f"{y}: n={len(vs):2d} 中位={vs[len(vs)//2]:5.0f}% 范围{vs[0]:.0f}-{vs[-1]:.0f}%")

    json.dump(rows_out, open(ROOT/"data/backtest_sell_percentiles.json","w"), ensure_ascii=False, indent=1)
    print("\n→ data/backtest_sell_percentiles.json")

if __name__ == "__main__":
    main()
