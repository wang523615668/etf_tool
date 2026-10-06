#!/usr/bin/env python3
"""逐指数个性化买卖阈值（用户2026-09-24：「给每个指数设置不同的买入卖出标准，不要千篇一律」）。

方法：对E大长赢150的每笔【买入】/【卖出】，回测当日该指数的估值温度
（与引擎同口径：PE分位与PB分位的平均，全历史分位），得到他的实际行为分布：
  buy_max  = 买入温度分布的 P90（90%的买入发生在该温度之下 → 他对该品种的"肯买线"）
  sell_min = 卖出温度分布的 P10（90%的卖出发生在该温度之上 → 他的"起卖线"）
样本 <5 笔 → 不设个性化值，回退全局默认（buy≤18 / sell≥70）。
输出 data/per_index_thresholds.json，由 generate_local_signals / account_ledger 读取。
"""
import json
from bisect import bisect_right
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
LX = ROOT / "cache" / "lixinger"
POS = ROOT / "data" / "long_win_positions.json"
OUT = ROOT / "data" / "per_index_thresholds.json"

# 组合基金名 → 理杏仁指数代码（按 E大 actions 里实际出现的名字，尽量全覆盖）
def find_idx(name: str):
    n = name
    if "创业板" in n: return "399006"
    if "恒生ETF联接" in n or n.startswith("华夏恒生"): return "HSI"
    if "恒生科技" in n or "HSTECH" in n or "恒科" in n: return "HSTECH"
    if "恒生医疗" in n or "海外医疗" in n: return "HSHCI"
    if "H股" in n or "恒生国企" in n or "H11136" in n: return "HSCEI"
    if "500增强" in n or "中证500" in n: return "000905"
    if "上证50" in n: return "000016"
    if "300增强" in n or "沪深300" in n: return "000300"
    if "医疗" in n: return "399989"
    if "传媒" in n: return "399971"
    if "环保" in n: return "000827"
    if "证券" in n or "券商" in n: return "399975"
    if "白酒" in n: return "399997"
    if "消费" in n: return "000990"
    if "金融地产" in n or "全指金融" in n: return "000992"
    if "银行" in n: return "399986"
    if "红利低波100" in n: return "930955"
    if "红利低波" in n or "红利" in n: return "000922"
    if "养老" in n: return "399812"
    if "医药" in n or "健康" in n: return "000991"
    if "中概" in n or "海外互联" in n or "海外互联网" in n: return "H11136"
    if "科创" in n: return "000688"
    if "A500" in n: return "000510"
    return None

IDX: dict[str, dict] = {}

def load_idx(code: str):
    if code not in IDX:
        f = LX / f"{code}.json"
        if not f.exists():
            IDX[code] = None
            return None
        d = json.load(open(f))
        rows = d["rows"]
        rows.sort(key=lambda r: r["date"])
        pe_sorted = sorted(r["pe"] for r in rows if r.get("pe"))
        pb_sorted = sorted(r["pb"] for r in rows if r.get("pb"))
        IDX[code] = {"name": d.get("name", code), "rows": rows,
                     "by_date": {r["date"]: r for r in rows},
                     "dates": [r["date"] for r in rows],
                     "pe_sorted": pe_sorted, "pb_sorted": pb_sorted}
    return IDX[code]

def pct_of(sorted_vals, v):
    return bisect_right(sorted_vals, v) / len(sorted_vals) * 100 if sorted_vals and v else None

def temp_on(code: str, date: str):
    ix = load_idx(code)
    if not ix:
        return None
    i = bisect_right(ix["dates"], date)
    if i == 0:
        return None
    r = ix["by_date"][ix["dates"][i - 1]]
    pe_p = pct_of(ix["pe_sorted"], r.get("pe"))
    pb_p = pct_of(ix["pb_sorted"], r.get("pb"))
    vals = [v for v in (pe_p, pb_p) if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)

def q(vals, f):
    vs = sorted(vals)
    return vs[min(len(vs) - 1, int(f * (len(vs) - 1)))]

# 全局默认（个性化样本不足时用）
DEF_FIRST, DEF_SELL = 18.0, 70.0

def main():
    d = json.load(open(POS))
    acts = d["plans"]["long_win_150"]["actions"]
    buys_by_idx, sells_by_idx = defaultdict(list), defaultdict(list)
    unmapped = 0
    for a in acts:
        code = find_idx(a["name"])
        if not code:
            unmapped += 1
            continue
        t = temp_on(code, a["date"])
        if t is None:
            continue
        (buys_by_idx if a["action"] == "buy" else sells_by_idx)[code].append(t)

    out = {}
    for code in sorted(set(buys_by_idx) | set(sells_by_idx)):
        ix = load_idx(code)
        b, s = buys_by_idx.get(code, []), sells_by_idx.get(code, [])
        rec = {"name": ix["name"] if ix else code, "n_buy": len(b), "n_sell": len(s)}
        # 首仓买入线：他不高于中位温度建仓，但封顶40°（首仓必须便宜）
        if len(b) >= 5:
            rec["first_line"] = round(min(40.0, max(12.0, q(b, 0.5))), 1)
            # 趋势加仓线：他实际最高买入温度(P90)，封顶85——已有底仓才允许加
            rec["add_line"] = round(min(85.0, max(rec["first_line"] + 5.0, q(b, 0.9))), 1)
            rec["buy_med"] = round(q(b, 0.5), 1)
        # 止盈卖出线：只统计"高位卖"(temp≥40，剔除调仓/换仓卖出)的 P25，样本≥3
        hi = [v for v in s if v >= 40]
        if len(hi) >= 3:
            rec["sell_line"] = round(min(85.0, max(45.0, q(hi, 0.25))), 1)
            rec["sell_med"] = round(q(hi, 0.5), 1)
            rec["n_sell_tp"] = len(hi)
        out[code] = rec

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {OUT.name}: {len(out)} 个指数（默认: 首仓≤{DEF_FIRST}° / 卖≥{DEF_SELL}°）")
    print(f"{'代码':9}{'名称':8}{'买n':>3}{'卖n':>3}  首仓线   加仓线   止盈线")
    for code, r in out.items():
        f = r.get("first_line", "—"); a = r.get("add_line", "—"); sl = r.get("sell_line", "—")
        st = r.get("sell_med", "")
        print(f"{code:9}{r['name']:8}{r['n_buy']:>3}{r['n_sell']:>3}  {f!s:>6}° {a!s:>6}° {sl!s:>6}°" +
              (f"  (他卖中位{st})" if st else ""))
    print(f"\n未映射(组合/债/QDII, 不参与): {unmapped} 笔")

if __name__ == "__main__":
    main()
