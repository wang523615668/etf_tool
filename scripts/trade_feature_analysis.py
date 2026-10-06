#!/usr/bin/env python3
"""逐笔解剖 E大 422 笔买卖当时可得的 6 维特征，找出估值之外他真正参考的信号。

特征（全部是"当日收盘后才知道"的口径，不含未来函数）：
  A_val_self   品种自身估值温度（PE/PB全历史分位均值，与引擎同口径）
  B_val_market 市场水位：全A正数等权PE 5年滚动分位（quanzhi_ewpvo，日更）
  C_trend_self 品种技术：cp vs MA3y/MA5y（E大的双均线语言）、vs 850线(3年线)
  D_mom_self   品种动量：60日涨跌幅、距252日高点回撤
  E_trend_market 市场技术：全A cp vs MA250、20日斜率
  F_pace       节奏：距该品种上一笔同方向操作的间隔天数、当日组合总份数(他的"仓位进程")
输出 data/trade_features.json + 分组统计。
"""
import json
import statistics
from bisect import bisect_right
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
LX = ROOT / "cache" / "lixinger"
POS = ROOT / "data" / "long_win_positions.json"
OUT = ROOT / "data" / "trade_features.json"

import sys
sys.path.insert(0, str(ROOT / "scripts"))
from calibrate_per_index import find_idx, load_idx, temp_on  # noqa: E402

QZ = json.load(open("/vol1/1000/openzl/finance_app/data/quanzhi_ewpvo.json"))["rows"]
QZ.sort(key=lambda r: r["date"])
QZ_D = [r["date"] for r in QZ]
QZ_PE = [r["pe"] for r in QZ]
QZ_CP = [r["cp"] for r in QZ]

def qz_pct5y(date: str):
    """全A等权PE 在该日前5年窗口内的分位（E大习惯看5/10年百分位）。"""
    i = bisect_right(QZ_D, date)
    if i == 0:
        return None
    pe = QZ_PE[i - 1]
    lo = max(0, i - 1 - 1250)  # ~5年
    win = QZ_PE[lo:i]
    if len(win) < 250:
        return None
    return sum(1 for v in win if v <= pe) / len(win) * 100

def qz_vs_ma250(date: str):
    i = bisect_right(QZ_D, date)
    if i < 260:
        return None
    cp = QZ_CP[i - 1]
    ma = statistics.mean(QZ_CP[i - 250:i])
    return (cp / ma - 1) * 100

def ma_series(rows, key, w):
    """cp 的均线序列（按日）。"""
    vals = [r.get("cp") for r in rows]
    out = [None] * len(vals)
    s = 0.0
    from collections import deque
    dq = deque()
    for i, v in enumerate(vals):
        if v is None:
            continue
        dq.append(v); s += v
        if len(dq) > w:
            s -= dq.popleft()
        if len(dq) == w:
            out[i] = s / w
    return out

IX_CACHE: dict[str, dict] = {}
def ix_full(code):
    if code not in IX_CACHE:
        ix = load_idx(code)
        if not ix:
            IX_CACHE[code] = None
        else:
            rows = ix["rows"]
            IX_CACHE[code] = {
                "ix": ix,
                "ma3y": ma_series(rows, "cp", 750),
                "ma5y": ma_series(rows, "cp", 1250),
            }
    return IX_CACHE[code]

def self_feats(code: str, date: str):
    fx = ix_full(code)
    if not fx:
        return {}
    ix = fx["ix"]; rows = ix["rows"]
    i = bisect_right(ix["dates"], date)
    if i == 0:
        return {}
    cp = rows[i - 1].get("cp")
    out = {}
    for tag, series in (("vs_ma3y", fx["ma3y"]), ("vs_ma5y", fx["ma5y"])):
        j = max(k for k in range(i) if series[k] is not None) if any(series[:i]) else -1
        if j >= 0 and cp:
            out[tag] = (cp / series[j] - 1) * 100
    lo60 = max(0, i - 61)
    if cp and rows[lo60].get("cp"):
        out["mom60"] = (cp / rows[lo60]["cp"] - 1) * 100
    hi252 = max((r["cp"] for r in rows[max(0, i - 252):i] if r.get("cp")), default=None)
    if cp and hi252:
        out["off_hi"] = (cp / hi252 - 1) * 100
    return out

def main():
    d = json.load(open(POS))
    acts = sorted(d["plans"]["long_win_150"]["actions"], key=lambda a: a["date"])
    # 组合进程：逐日累计净份数
    per_day_delta = defaultdict(float)
    for a in acts:
        per_day_delta[a["date"]] += a["shares"] * (1 if a["action"] == "buy" else -1)
    running, dates = 0.0, sorted(per_day_delta)
    cum = {}
    for dt_ in dates:
        running += per_day_delta[dt_]
        cum[dt_] = running

    last_same = {}
    feats = []
    unmapped = 0
    for a in acts:
        code = find_idx(a["name"])
        if not code:
            unmapped += 1
            continue
        t = temp_on(code, a["date"])
        if t is None:
            continue
        f = {
            "date": a["date"], "dir": a["action"], "idx": code, "name": a["name"][:16],
            "shares": a["shares"], "val_self": round(t, 1),
            "val_mkt5y": None if not (v := qz_pct5y(a["date"])) else round(v, 1),
            "mkt_ma250": None if (v := qz_vs_ma250(a["date"])) is None else round(v, 1),
            "book_pos": round(cum.get(a["date"], 0), 0),
            "gap_days": None,
        }
        f.update({k: round(v, 1) for k, v in self_feats(code, a["date"]).items()})
        key = (code, a["action"])
        if key in last_same:
            from datetime import date as D
            d1 = D(*map(int, last_same[key].split("-"))); d2 = D(*map(int, a["date"].split("-")))
            f["gap_days"] = (d2 - d1).days
        last_same[key] = a["date"]
        feats.append(f)

    OUT.write_text(json.dumps(feats, ensure_ascii=False), encoding="utf-8")
    B = [f for f in feats if f["dir"] == "buy"]
    S = [f for f in feats if f["dir"] == "sell"]
    print(f"可分析: 买{len(B)} 卖{len(S)}（未映射{unmapped}）→ {OUT.name}")

    def dist(rows_, k, bins):
        vs = sorted(f[k] for f in rows_ if f.get(k) is not None)
        if not vs: return "无"
        n = len(vs)
        seg = [sum(1 for v in vs if lo <= v < hi) for lo, hi in bins]
        med = vs[n // 2]
        return f"中位{med:5.1f} p25={vs[n//4]:5.0f} p75={vs[3*n//4]:5.0f} | " + " ".join(
            f"{lo:.0f}~{hi:.0f}:{c*100//n}%" for (lo, hi), c in zip(bins, seg) if c)

    VB = [(0,20),(20,40),(40,60),(60,80),(80,101)]
    print("\n──── 市场水位（全A等权PE·5年分位）────")
    print("买入时:", dist(B, "val_mkt5y", VB))
    print("卖出时:", dist(S, "val_mkt5y", VB))
    print("\n──── 全A vs MA250（市场趋势）────")
    MB = [(-100,-5),(-5,0),(0,5),(5,15),(15,100)]
    print("买入时:", dist(B, "mkt_ma250", MB))
    print("卖出时:", dist(S, "mkt_ma250", MB))
    print("\n──── 品种 vs 3年线(850线) ────")
    TB = [(-100,-15),(-15,-5),(-5,5),(5,15),(15,40),(40,1000)]
    print("买入时:", dist(B, "vs_ma3y", TB))
    print("卖出时:", dist(S, "vs_ma3y", TB))
    print("\n──── 品种60日动量% ────")
    PB = [(-100,-10),(-10,-3),(-3,3),(3,10),(10,30),(30,1000)]
    print("买入时:", dist(B, "mom60", PB))
    print("卖出时:", dist(S, "mom60", PB))
    print("\n──── 距252日高点回撤% ────")
    HB = [(-100,-40),(-40,-25),(-25,-12),(-12,0),(0,100)]
    print("买入时:", dist(B, "off_hi", HB))
    print("卖出时:", dist(S, "off_hi", HB))
    print("\n──── 组合进程（操作时已投入份数）────")
    SB = [(0,30),(30,60),(60,90),(90,120),(120,160)]
    print("买入时:", dist(B, "book_pos", SB))
    print("卖出时:", dist(S, "book_pos", SB))
    print("\n──── 同品种同方向间隔天数 ────")
    GB = [(0,15),(15,45),(45,120),(120,400),(400,100000)]
    print("买入:", dist(B, "gap_days", GB))
    print("卖出:", dist(S, "gap_days", GB))

    # 交叉：低位卖（调仓）与高位卖的品种技术差
    lo_s = [f for f in S if f["val_self"] < 40]
    hi_s = [f for f in S if f["val_self"] >= 40]
    print(f"\n──── 低位卖(调仓,{len(lo_s)}笔) vs 高位卖(止盈,{len(hi_s)}笔) ────")
    print("调仓卖 距3年线:", dist(lo_s, "vs_ma3y", TB), " 60日动量:", dist(lo_s, "mom60", PB))
    print("止盈卖 距3年线:", dist(hi_s, "vs_ma3y", TB), " 60日动量:", dist(hi_s, "mom60", PB))

if __name__ == "__main__":
    main()
