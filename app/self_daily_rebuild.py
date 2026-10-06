#!/usr/bin/env python3
# self_daily_rebuild.py — 按「逐指数最优组合(算法×名单源)」重算 self_daily 历史序列
#
# 背景: 旧版所有序列统一用「当日快照×冻结名单×剔亏中位TTM」, 与 E大 表平均最大偏差 14.3%。
#       本脚本改用 data/ed_perindex_best.json 里每列各自的最优组合(22列×15算法×4名单源网格搜索得到),
#       平均最大偏差降到 5.7%。
# 快照源: data/ed_hist_full/{date}.json  (东财全A日频, 字段 [PE_TTM, PE_LAR, PB_MRQ, 市值])
# 备份:   *.bak-unified (旧统一算法版) — 只在首次运行时生成
# 用法:   python app/self_daily_rebuild.py [--dry]
import json, os, sys, shutil, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ed_algo

BASE = "/vol1/1000/openzl/etf_tool"
HIST = f"{BASE}/data/self_daily"
FULL = f"{BASE}/data/ed_hist_full"
# app 序列名 → 配置列名(不同才需要)
ALIAS = {"全市场·主板(E大口径)": "全市场", "全市场·沪深京": "全市场加权"}
DRY = "--dry" in sys.argv

def load_snap(date):
    with open(f"{FULL}/{date}.json") as f:
        j = json.load(f)
    return {c: {"PE_TTM": v[0], "PE_LAR": v[1], "PB_MRQ": v[2], "TOTAL_MARKET_CAP": v[3]}
            for c, v in j.items()}

def pctile(rows, val, years, date):
    d0 = (dt.date.fromisoformat(date) - dt.timedelta(days=int(365.25 * years))).isoformat()
    w = sorted(r["pe"] for r in rows if r["date"] >= d0 and r.get("pe"))
    if len(w) < 50 or not val:
        return None
    return round(sum(1 for x in w if x <= val) / len(w) * 100, 1)

def main():
    cfg = ed_algo.load_config()
    dates = sorted(f[:-5] for f in os.listdir(FULL) if f.endswith(".json"))
    print(f"快照 {len(dates)} 天: {dates[0]} → {dates[-1]} | 配置 {len(cfg)} 列")
    # 收集要重算的序列
    targets = []
    for fn in sorted(os.listdir(HIST)):
        if not fn.endswith(".json"):
            continue
        d = json.load(open(f"{HIST}/{fn}"))
        if not (d.get("code") or d.get("scope")):
            continue
        key = ALIAS.get(d.get("name"), d.get("name"))
        if key in cfg:
            targets.append((fn, d, key, cfg[key]))
    print(f"待重算序列 {len(targets)} 条:")
    for fn, d, key, c in targets:
        print(f"  {fn:<14} {d.get('name'):<20} ← {key:<8} {c['algo']}·{c['list']}")
    if DRY:
        return
    # 备份(一次性)
    for fn, d, key, c in targets:
        bak = f"{HIST}/{fn}.bak-unified"
        if not os.path.exists(bak):
            shutil.copy(f"{HIST}/{fn}", bak)
    print(f"\n已备份为 *.bak-unified (仅首次)")
    # 逐日重算: 每天只读一次快照, 当天把所有序列算完
    ctx = ed_algo.build_ctx(dates[-1])
    oldmap = {fn: {r["date"]: r for r in (d.get("rows") or [])} for fn, d, _, _ in targets}
    acc = {fn: [] for fn, _, _, _ in targets}
    for i, date in enumerate(dates):
        snap = load_snap(date)
        ctx.date = date
        for fn, d, key, c in targets:
            pe, pb, n = ed_algo.series_value(snap, key, cfg, ctx)
            if pe is None:
                o = oldmap[fn].get(date)
                if o:
                    acc[fn].append(o)
                continue
            acc[fn].append({"date": date, "pe": pe, "pb": pb, "n": n})
        if i % 250 == 0:
            print(f"    进度 {i}/{len(dates)} {date}", flush=True)
    print(f"  重算完成, 写盘…")
    done = 0
    for fn, d, key, c in targets:
        rows = acc[fn]
        old = oldmap[fn]
        miss = len(rows) - sum(1 for r in rows if r.get("n"))
        d["rows"] = rows
        d["latest"] = rows[-1] if rows else None
        d["algo"] = c["algo"]
        d["list_mode"] = c["list"]
        d["method"] = ed_algo.describe(c["algo"], c["list"])
        d["pit"] = c["list"] in ("pit", "bspit")
        if rows:
            d["p5y"] = pctile(rows, rows[-1]["pe"], 5, rows[-1]["date"])
            d["p10y"] = pctile(rows, rows[-1]["pe"], 10, rows[-1]["date"])
        json.dump(d, open(f"{HIST}/{fn}", "w"), ensure_ascii=False)
        done += 1
        print(f"  ✓ {fn:<14} {d.get('name'):<20} 行{len(rows)}(沿用旧值{miss}) "
              f"最新 PE={rows[-1]['pe'] if rows else '-'} p5y={d.get('p5y')} p10y={d.get('p10y')}")
    print(f"\n完成 {done} 条序列")

if __name__ == "__main__":
    main()
