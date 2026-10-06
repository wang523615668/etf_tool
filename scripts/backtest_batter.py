
"""击球分数历史回测（numpy 优化版）。

关键优化：dd/bias 序列只算一次（O(n)），逐日分位用增量插入排序数组 +
bisect 二分（O(log n)），整体 O(n log n) 而不是 O(n²)。
"""
import glob, json, sys
import numpy as np
from bisect import bisect_left, insort

CACHE = "/vol1/1000/openzl/etf_tool/cache/lixinger"


def load_rows(name):
    best = None
    for f in glob.glob(f"{CACHE}/*.json"):
        d = json.load(open(f))
        if d.get("name") == name:
            n = len(d.get("rows") or [])
            if best is None or n > best[1]:
                best = (d["rows"], n)
    return best[0] if best else []


def backtest_index(rows, *, window_days=3650, w_val=0.40, w_sent=0.30, w_mom=0.30,
                   val_th=80.0, sent_th=70.0, buy_interval_days=30):
    dates = [r["date"] for r in rows]
    cps = np.array([r.get("cp") if r.get("cp") else np.nan for r in rows])
    pes = np.array([r.get("pe") if r.get("pe") else np.nan for r in rows])
    pbs = np.array([r.get("pb") if r.get("pb") else np.nan for r in rows])
    n = len(rows)
    if n < 1500:
        return None

    # ---- 预计算全序列 rolling 统计（向量化）
    with np.errstate(invalid="ignore"):
        # PE/PB 10年滚动分位：用月度采样历史窗近似（每月一个基准点，rank 在窗内算）
        pe_r = np.full(n, np.nan)
        pb_r = np.full(n, np.nan)
        # 回撤序列 & bias 序列（250日滚动高点/均值）
        roll_high = np.full(n, np.nan)
        roll_ma250 = np.full(n, np.nan)
        for i in range(249, n):
            w250 = cps[i-249:i+1]
            v = w250[~np.isnan(w250)]
            if len(v) >= 200:
                roll_high[i] = v.max()
                roll_ma250[i] = v.mean()
        dd = np.where(roll_high > 0, cps / roll_high - 1, np.nan)
        bias = np.where(roll_ma250 > 0, cps / roll_ma250 - 1, np.nan)
        # 均线850
        csum = np.nancumsum(np.where(np.isnan(cps), 0, cps))
        ccnt = np.cumsum(~np.isnan(cps))
        ma850 = np.full(n, np.nan)
        ma850[849:] = (csum[849:] - csum[:-849]) / (ccnt[849:] - ccnt[:-849])

    trades = []
    last_buy_idx = -9999
    holding = False
    entry_cp = entry_i = None

    # 增量分位结构
    from collections import deque
    win = deque()          # (idx, pe, pb) 窗口内样本
    sorted_pe, sorted_pb = [], []
    dd_hist_sorted, bias_hist_sorted = [], []

    def pct(sorted_list, x):
        return bisect_left(sorted_list, x) / len(sorted_list) if sorted_list and not np.isnan(x) else None

    for i in range(1300, n):
        cur_cp, cur_pe, cur_pb = cps[i], pes[i], pbs[i]
        if np.isnan(cur_cp):
            continue

        lo = i - window_days
        # 剔除窗口外旧样本
        while win and win[0][0] < lo:
            _, ope, opb = win.popleft()
            del sorted_pe[bisect_left(sorted_pe, ope)]
            del sorted_pb[bisect_left(sorted_pb, opb)]
        # 加入当日
        if not np.isnan(cur_pe):
            insort(sorted_pe, cur_pe); 
        if not np.isnan(cur_pb):
            insort(sorted_pb, cur_pb)
        win.append((i, cur_pe if not np.isnan(cur_pe) else 0.0, cur_pb if not np.isnan(cur_pb) else 0.0))
        # 注意：nan 样本也进队列以维持窗口边界，但值记 0 不参与 sorted —— 上面已处理

        pe_rk = pct(sorted_pe, cur_pe) if not np.isnan(cur_pe) else None
        pb_rk = pct(sorted_pb, cur_pb) if not np.isnan(cur_pb) else None
        ranks = [r for r in (pe_rk, pb_rk) if r is not None]
        if not ranks:
            continue
        temp = sum(ranks) / len(ranks) * 100
        value_score = 100 - temp

        # 情绪：dd/bias 的历史全序分位（增量维护）
        d = dd[i]; b = bias[i]
        s_parts = []
        if not np.isnan(d):
            rk = bisect_left(dd_hist_sorted, d) / max(1, len(dd_hist_sorted)) if dd_hist_sorted else None
            insort(dd_hist_sorted, float(d))
            if rk is not None:
                s_parts.append(rk * 100)
        if not np.isnan(b):
            rk = bisect_left(bias_hist_sorted, b) / max(1, len(bias_hist_sorted)) if bias_hist_sorted else None
            insort(bias_hist_sorted, float(b))
            if rk is not None:
                s_parts.append(rk * 100)
        if not s_parts:
            continue
        sent_score = sum(s_parts) / len(s_parts)

        # 动量
        def ret(lag):
            j = i - lag
            return cur_cp / cps[j] - 1 if j >= 0 and not np.isnan(cps[j]) else None
        r252, r21 = ret(252), ret(21)
        mom121 = (r252 - r21) if (r252 is not None and r21 is not None) else None
        mom20 = r21
        above250 = (not np.isnan(roll_ma250[i])) and cur_cp > roll_ma250[i]
        above850 = (not np.isnan(ma850[i])) and cur_cp > ma850[i]
        m_parts = [max(0., min(100., ((mom121 or 0) + .15) / .30 * 100)),
                   100. if above250 else 35.,
                   100. if above850 else 40.,
                   max(0., min(100., ((mom20 or 0) + .10) / .20 * 100))]
        mom_score = sum(m_parts) / len(m_parts)

        total = w_val*value_score + w_sent*sent_score + w_mom*mom_score
        stance_buy = value_score >= val_th and sent_score >= sent_th
        stance_sell = value_score < 50 and above250

        if holding and stance_sell:
            trades.append({"date": dates[i], "action": "sell", "cp": float(cur_cp),
                           "entry_cp": entry_cp, "ret_3m": float(cur_cp/entry_cp - 1)})
            holding = False
        elif stance_buy and not holding and i - last_buy_idx >= buy_interval_days:
            last_buy_idx = i; holding = True; entry_cp = float(cur_cp)
            trades.append({"date": dates[i], "action": "buy", "cp": float(cur_cp),
                           "value": round(value_score), "sent": round(sent_score),
                           "mom": round(mom_score), "total": round(total, 1)})

    results = []
    date_index = {d: k for k, d in enumerate(dates)}
    for t in trades:
        if t["action"] != "buy":
            continue
        bi = date_index.get(t["date"])
        if bi is None:
            continue
        def fwd(days):
            j = min(bi + days, n - 1)
            return cps[j]/t["cp"] - 1 if not np.isnan(cps[j]) else None
        results.append({"date": t["date"], "cp": t["cp"], "value": t["value"],
                        "sent": t["sent"], "mom": t["mom"], "total": t["total"],
                        "ret_1y": fwd(245), "ret_3y": fwd(730)})
    sells = [t for t in trades if t["action"] == "sell"]
    wins = [s for s in sells if s["ret_3m"] > 0]

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals)/len(vals)*100, 2) if vals else None
    r1y = [r["ret_1y"] for r in results]
    r3y = [r["ret_3y"] for r in results]
    return {
        "buys": len(results), "sells": len(sells),
        "sell_win_rate": round(len(wins)/len(sells)*100, 1) if sells else None,
        "avg_ret_1y_pct": avg(r1y), "avg_ret_3y_pct": avg(r3y),
        "pos_rate_1y": round(sum(1 for v in r1y if v and v > 0)/len(r1y)*100, 1) if r1y else None,
        "pos_rate_3y": round(sum(1 for v in r3y if v and v > 0)/len(r3y)*100, 1) if r3y else None,
        "trades_sample": [{k: (round(v*100, 1) if isinstance(v, float) and abs(v) < 5 else v)
                           for k, v in r.items()} for r in results[-6:]],
    }


if __name__ == "__main__":
    targets = ["沪深300", "中证500", "创业板指", "恒生指数", "证券公司",
               "中证医疗", "中证红利", "全指消费"]
    out = {}
    import time
    for name in targets:
        t0 = time.time()
        rows = load_rows(name)
        full = backtest_index(rows)
        base = backtest_index(rows, sent_th=-1.0)   # 裸估值基准
        out[name] = {"batter": full, "baseline_value_only": base}
        print(f"\n=== {name} ({time.time()-t0:.1f}s) ===")
        if full:
            print(f"  击球分数: 买{full['buys']}卖{full['sells']} 卖出胜率{full['sell_win_rate']}% | "
                  f"买入后1年均{full['avg_ret_1y_pct']}%(正{full['pos_rate_1y']}%) 3年均{full['avg_ret_3y_pct']}%(正{full['pos_rate_3y']}%)")
        if base:
            print(f"  裸估值  : 买{base['buys']}卖{base['sells']} 卖出胜率{base['sell_win_rate']}% | "
                  f"买入后1年均{base['avg_ret_1y_pct']}%(正{base['pos_rate_1y']}%) 3年均{base['avg_ret_3y_pct']}%(正{base['pos_rate_3y']}%)")
        sys.stdout.flush()
    with open("/vol1/1000/openzl/etf_tool/data/backtest_batter.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved -> data/backtest_batter.json")
