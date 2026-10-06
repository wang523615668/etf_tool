#!/usr/bin/env python3
# self_daily_calib.py — 「与E大对照」测算: 把偏差统计与分位敏感区间写进 self_daily/*.json
#
# 真值源(全部本地, 无需联网):
#   data/ed_truth_multi.json    邮件估值表 OCR 真值(2019-12~2023-03, 72个日期)
#   data/ed_anchors_prose.json  E大 散文/微博亲口读数(2018~2025, 18条锚点)
# 快照源: data/ed_hist_full/{date}.json (自算复算用)
#
# 写回字段:
#   ed_dev_n / ed_dev_med / ed_dev_max   邮件真值: 样本数 / 平均绝对偏差 / 最大偏差
#   ed_anch_n / ed_anch_med / ed_anch_max 散文锚点: 同上(带权重)
#   p5y_s / p10y_s / shadow_days         分位敏感区间: 统一基准口径(中位TTM剔亏×current)的分位
#   calib_at                              测算时间
# 用法: python app/self_daily_calib.py [--dry]
import json, os, sys, statistics as st, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ed_algo

ET = "/vol1/1000/openzl/etf_tool"
HIST = f"{ET}/data/self_daily"
SHADOW_DIR = f"{ET}/data/self_daily_shadow"
FULL = f"{ET}/data/ed_hist_full"
ALIAS = {"全市场·主板(E大口径)": "全市场", "全市场·沪深京": "全市场加权"}
DRY = "--dry" in sys.argv
SHADOW_ALGO, SHADOW_LIST = "中位TTM剔亏", "current"   # 统一基准口径(=旧统一口径)

def load_truth():
    email = json.load(open("/root/.hermes/cache/scratch/ed_truth_multi.json"))
    anchors = json.load(open(f"{ET}/data/ed_anchors_prose.json"))["items"]
    return email, anchors

_snap = {}
def snap(d):
    if d not in _snap:
        p = f"{FULL}/{d}.json"
        _snap[d] = None
        if os.path.exists(p):
            try:
                j = json.load(open(p))
                _snap[d] = {c: {"PE_TTM": v[0], "PE_LAR": v[1], "PB_MRQ": v[2], "TOTAL_MARKET_CAP": v[3]}
                            for c, v in j.items() if isinstance(v, list) and len(v) >= 4}
            except Exception:
                pass
    return _snap[d]

def pct_of(rows, val, years, date):
    d0 = (dt.date.fromisoformat(date) - dt.timedelta(days=int(365.25 * years))).isoformat()
    w = sorted(r["pe"] for r in rows if r["date"] >= d0 and r.get("pe"))
    if len(w) < 50 or not val:
        return None
    return round(sum(1 for x in w if x <= val) / len(w) * 100, 1)

def main():
    email, anchors = load_truth()
    ctx = ed_algo.build_ctx(dt.date.today().isoformat())
    n_done = 0
    for fp in sorted(os.listdir(HIST)):
        if not fp.endswith(".json"):
            continue
        path = f"{HIST}/{fp}"
        d = json.load(open(path))
        if not (d.get("code") or d.get("scope")):
            continue
        rows = d.get("rows") or []
        if not rows:
            continue
        name = ALIAS.get(d.get("name"), d.get("name"))
        # ---------- 1. 邮件真值偏差 ----------
        devs, ds = [], []
        for date, cols in sorted(email.items()):
            if name not in cols:
                continue
            r = next((x for x in rows if x["date"] == date), None)
            if not r or not r.get("pe"):
                continue
            tv = cols[name]["pe"]
            dev = (r["pe"] - tv) / tv * 100
            devs.append(dev); ds.append({"date": date, "ours": r["pe"], "ed": tv, "dev": round(dev, 1)})
        # ---------- 2. 散文锚点偏差 ----------
        adevs, ads = [], []
        for a in anchors:
            if a["index"] != name:
                continue
            if a["kind"] == "point":
                d0 = a["date"]
                ahead = [x for x in rows if x["date"] >= d0]
                v = ahead[0]["pe"] if ahead else None
            else:
                w0, w1 = a.get("win", [a["date"], a["date"]])
                cand = [x["pe"] for x in rows if w0 <= x["date"] <= w1 and x.get("pe")]
                v = max(cand) if cand else None
            if not v:
                continue
            e = (v - a["val"]) / a["val"] * 100
            adevs.append(abs(e) * a.get("weight", 1.0))
            ads.append({"date": a["date"], "ours": round(v, 1), "ed": a["val"], "dev": round(e, 1),
                        "kind": a["kind"], "src": a.get("src", "")[:60]})
        out = {}
        if devs:
            out.update(ed_dev_n=len(devs), ed_dev_med=round(st.mean(abs(x) for x in devs), 1),
                       ed_dev_max=round(max(abs(x) for x in devs), 1), ed_dev_detail=ds)
        if adevs:
            out.update(ed_anch_n=len(adevs), ed_anch_med=round(st.mean(adevs), 1),
                       ed_anch_max=round(max(adevs), 1), ed_anch_detail=ads)
        # ---------- 3. 分位敏感区间(影子序列: 同算法×另一名单口径) ----------
        shadow = f"{SHADOW_DIR}/{fp}"
        if os.path.exists(shadow):
            try:
                sd = json.load(open(shadow))
                sh = sd.get("rows") or []
                if len(sh) >= 200:
                    cur_date = rows[-1]["date"]
                    sv = [x["pe"] for x in sh if x["date"] == cur_date]
                    out["shadow_days"] = len(sh)
                    out["shadow_list"] = sd.get("list_mode")
                    out["shadow_algo"] = sd.get("algo")
                    if sv:
                        out["p5y_s"] = pct_of(sh, sv[0], 5, cur_date)
                        out["p10y_s"] = pct_of(sh, sv[0], 10, cur_date)
                    out["pe_shadow"] = sv[0] if sv else None
            except Exception as exc:
                print(f"   shadow 失败 {fp}: {exc}")
        out["calib_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        d.update(out)
        if DRY:
            print(f"  [dry] {fp:<16}{d.get('name'):<22} 邮件{out.get('ed_dev_n','-')}样本 平均{out.get('ed_dev_med','-')}% "
                  f"锚点{out.get('ed_anch_n','-')} 平均{out.get('ed_anch_med','-')}% p5y {d.get('p5y')}→{out.get('p5y_s','-')}")
        else:
            json.dump(d, open(path, "w"), ensure_ascii=False)
            print(f"  ✓ {fp:<16}{d.get('name'):<22} 邮件{out.get('ed_dev_n','-')} 平均{out.get('ed_dev_med','-')}% 最大{out.get('ed_dev_max','-')}% "
                  f"| 锚点{out.get('ed_anch_n','-')} 平均{out.get('ed_anch_med','-')}% | 5y {d.get('p5y')}~{out.get('p5y_s','-')}")
        n_done += 1
    print(f"\n测算完成 {n_done} 条序列")

if __name__ == "__main__":
    main()
