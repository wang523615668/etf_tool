#!/usr/bin/env python3
# self_daily_sync.py — 序列口径自愈: 检测「序列文件里存的口径」与「配置里的口径」不一致的序列,
#   用 data/ed_hist_full/ 全历史按新口径重建(重建前备份 .bak-sync-<时间戳>)。
#
# 为什么需要: 改标定配置后若只更新了配置、没重建序列, 就会出现「历史用旧口径 + 日更用新口径」的
#   混口径序列 —— 分位会产生断点且算法静默不一致。此脚本每次日更前后都可安全运行(幂等)。
# 用法: python app/self_daily_sync.py [--dry]
import json, os, sys, shutil, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ed_algo

ET = "/vol1/1000/openzl/etf_tool"
HIST = f"{ET}/data/self_daily"
FULL = f"{ET}/data/ed_hist_full"
ALIAS = {"全市场·主板(E大口径)": "全市场", "全市场·沪深京": "全市场加权"}
DRY = "--dry" in sys.argv

def pctile(rows, val, years, date):
    d0 = (dt.date.fromisoformat(date) - dt.timedelta(days=int(365.25 * years))).isoformat()
    w = sorted(r["pe"] for r in rows if r["date"] >= d0 and r.get("pe"))
    if len(w) < 50 or not val:
        return None
    return round(sum(1 for x in w if x <= val) / len(w) * 100, 1)

def main():
    cfg = ed_algo.load_config()
    dates = sorted(f[:-5] for f in os.listdir(FULL) if f.endswith(".json"))
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
        c = cfg[key]
        if d.get("algo") == c["algo"] and (d.get("list_mode") or "current") == c["list"]:
            continue
        targets.append((fn, key, c, d))
    if not targets:
        print("口径一致, 无需自愈")
        return
    print(f"发现 {len(targets)} 条混口径序列:")
    for fn, key, c, d in targets:
        print(f"  {fn:<14}{d.get('name'):<20} 序列内 {d.get('algo')}·{d.get('list_mode')} → 配置 {c['algo']}·{c['list']}")
    if DRY:
        return
    ctx = ed_algo.build_ctx(dates[-1])
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M")
    acc = {fn: [] for fn, _, _, _ in targets}
    for i, date in enumerate(dates):
        with open(f"{FULL}/{date}.json") as f:
            j = json.load(f)
        snap = {c: {"PE_TTM": v[0], "PE_LAR": v[1], "PB_MRQ": v[2], "TOTAL_MARKET_CAP": v[3]} for c, v in j.items()}
        ctx.date = date
        for fn, key, c, d in targets:
            pe, pb, n = ed_algo.series_value(snap, key, cfg, ctx)
            if pe is not None:
                acc[fn].append({"date": date, "pe": pe, "pb": pb, "n": n})
        if i % 500 == 0:
            print(f"   {i}/{len(dates)} {date}", flush=True)
    for fn, key, c, d in targets:
        rows = acc[fn]
        shutil.copy(f"{HIST}/{fn}", f"{HIST}/{fn}.bak-sync-{ts}")
        d["rows"] = rows
        d["latest"] = rows[-1] if rows else None
        d["algo"] = c["algo"]; d["list_mode"] = c["list"]
        d["method"] = ed_algo.describe(c["algo"], c["list"], c.get("pe_cap"))
        d["pit"] = c["list"] in ("pit", "bspit")
        if rows:
            d["p5y"] = pctile(rows, rows[-1]["pe"], 5, rows[-1]["date"])
            d["p10y"] = pctile(rows, rows[-1]["pe"], 10, rows[-1]["date"])
        json.dump(d, open(f"{HIST}/{fn}", "w"), ensure_ascii=False)
        print(f"  ✓ {fn:<14}{d.get('name'):<20} 行{len(rows)} 最新 PE={rows[-1]['pe'] if rows else '-'} "
              f"p5y={d.get('p5y')} p10y={d.get('p10y')} (备份 .bak-sync-{ts})")
    print("自愈完成")

if __name__ == "__main__":
    main()
