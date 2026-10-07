#!/usr/bin/env python3
# self_daily_shadow.py — 为每条序列算「影子序列」: 同算法 × 另一名单口径
#   用途: 分位敏感区间(口径不确定度)。主口径用 current/mktcap 时影子取 pit, 反之取 current。
#   产出: data/self_daily/{code}.shadow.json  (日更会追加, 见 self_daily_update.py)
# 用法: python app/self_daily_shadow.py
import json, os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ed_algo

ET = "/vol1/1000/openzl/etf_tool"
HIST = f"{ET}/data/self_daily"
SHADOW_DIR = f"{ET}/data/self_daily_shadow"
FULL = f"{ET}/data/ed_hist_full"
ALIAS = {"全市场·主板(E大口径)": "全市场", "全市场·沪深京": "全市场加权"}

def alt_list(mode):
    return "pit" if mode in ("current", "mktcap") else "current"

# 名单口径无差别时(全市场类 pool 不看名单), 退化为「改算法」的定义性口径差
ALGO_ALT = {"中位TTM剔亏": "中位TTM含亏", "中位TTM含亏": "中位TTM剔亏",
            "中位静态剔亏": "中位静态含亏", "中位静态含亏": "中位静态剔亏",
            "等权整体法(调和)": "加权整体法", "加权整体法": "等权整体法(调和)"}

def pick_alt(snap, key, algo, primary, ctx):
    """返回 (list_mode, algo)。优先换名单源; 若换名单无差别(如全市场/科创50缺pit)则换算法。"""
    order = ["pit", "mktcap"] if primary in ("current", "mktcap") else ["current"]
    base, _, _ = ed_algo.series_value(snap, key, {key: {"algo": algo, "list": primary}}, ctx)
    for m in order:
        pe, _, _ = ed_algo.series_value(snap, key, {key: {"algo": algo, "list": m}}, ctx)
        if pe and (not base or abs(pe - base) / base * 100 >= 0.1):
            return m, algo
    # 换算法(定义性口径差): 表内映射, 否则退到统一基准口径
    a2 = ALGO_ALT.get(algo) or ("中位TTM含亏" if algo == "中位TTM剔亏" else "中位TTM剔亏")
    pe2, _, _ = ed_algo.series_value(snap, key, {key: {"algo": a2, "list": primary}}, ctx)
    if pe2 and (not base or abs(pe2 - base) / base * 100 >= 0.1):
        return primary, a2
    # 最后兜底: 名单口径差异极小也要给出一条影子(如宽基 bspit≈current)
    for m in order:
        pe, _, _ = ed_algo.series_value(snap, key, {key: {"algo": algo, "list": m}}, ctx)
        if pe:
            return m, algo
    return primary, a2

def main():
    cfg = ed_algo.load_config()
    only = None
    if "--only" in sys.argv:
        only = {x.strip() for x in sys.argv[sys.argv.index("--only") + 1].split(",") if x.strip()}
    globals()["ONLY"] = only
    dates = sorted(f[:-5] for f in os.listdir(FULL) if f.endswith(".json"))
    print(f"快照 {len(dates)} 天, 配置 {len(cfg)} 列")
    targets = []
    for fn in sorted(os.listdir(HIST)):
        if not fn.endswith(".json"):
            continue
        d = json.load(open(f"{HIST}/{fn}"))
        if not (d.get("code") or d.get("scope")):
            continue
        key = ALIAS.get(d.get("name"), d.get("name"))
        if key not in cfg:
            continue
        if ONLY and key not in ONLY:      # --only 名称1,名称2 (改单列时不必全量)
            continue
        targets.append((fn, key, cfg[key]))
    print(f"影子序列 {len(targets)} 条")
    ctx = ed_algo.build_ctx(dates[-1])
    acc = {fn: [] for fn, _, _ in targets}
    # 口径探测(用最新一天, 在循环外完成): 影子口径必须全程可用
    with open(f"{FULL}/{dates[-1]}.json") as f:
        _j = json.load(f)
    _snap = {c: {"PE_TTM": v[0], "PE_LAR": v[1], "PB_MRQ": v[2], "TOTAL_MARKET_CAP": v[3]} for c, v in _j.items()}
    ctx.date = dates[-1]
    chosen = {fn: pick_alt(_snap, key, c["algo"], c["list"], ctx) for fn, key, c in targets}
    print("  影子口径:", {fn[:-5]: f"{chosen[fn][0]}/{chosen[fn][1]}" for fn, _, _ in targets})
    for i, date in enumerate(dates):
        with open(f"{FULL}/{date}.json") as f:
            j = json.load(f)
        snap = {c: {"PE_TTM": v[0], "PE_LAR": v[1], "PB_MRQ": v[2], "TOTAL_MARKET_CAP": v[3]} for c, v in j.items()}
        ctx.date = date
        for fn, key, c in targets:
            m, a = chosen.get(fn)
            if not m:
                m, a = alt_list(c["list"]), c["algo"]
            _c = {"algo": a, "list": m}
            if c.get("pe_cap"):
                _c["pe_cap"] = c["pe_cap"]     # 与主序列同口径剔除微利股
            pe, pb, n = ed_algo.series_value(snap, key, {key: _c}, ctx)
            if pe is not None:
                acc[fn].append({"date": date, "pe": pe, "pb": pb, "n": n})
        if i % 250 == 0:
            print(f"   {i}/{len(dates)} {date}", flush=True)
    for fn, key, c in targets:
        rows = acc[fn]
        if not rows:
            print(f"  ✗ {fn} 无数据"); continue
        d0 = json.load(open(f"{HIST}/{fn}"))
        out = {"name": d0.get("name"), "code": d0.get("code"), "kind": "shadow",
               "algo": (chosen.get(fn) or (None, c["algo"]))[1],
               "list_mode": (chosen.get(fn) or (alt_list(c["list"]), None))[0],
               "method": ed_algo.describe((chosen.get(fn) or (None, c["algo"]))[1],
                                          (chosen.get(fn) or (alt_list(c["list"]), None))[0]),
               "note": "影子序列: 同一算法、另一名单口径, 仅用于分位敏感区间",
               "rows": rows, "latest": rows[-1]}
        def pct(yrs):
            dd = (dt.date.fromisoformat(rows[-1]["date"]) - dt.timedelta(days=int(365.25*yrs))).isoformat()
            w = sorted(r["pe"] for r in rows if r["date"] >= dd and r.get("pe"))
            return round(sum(1 for x in w if x <= rows[-1]["pe"]) / len(w) * 100, 1) if len(w) >= 50 else None
        out["p5y"] = pct(5); out["p10y"] = pct(10)
        os.makedirs(SHADOW_DIR, exist_ok=True)
        json.dump(out, open(f"{SHADOW_DIR}/{fn}", "w"), ensure_ascii=False)
        print(f"  ✓ {fn:<14}{d0.get('name'):<20} 影子{out['algo']}/{out['list_mode']:<8} 行{len(rows)} 最新PE={rows[-1]['pe']} p5y={out['p5y']}")
    print("完成")

if __name__ == "__main__":
    main()
