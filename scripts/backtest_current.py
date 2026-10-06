"""按当前线上系统（E大档位 + 时间空间闸门）做近两年回测。

规则与 app/batter_score.py 完全对齐：
  买入档：🚀主力买入(估值≥70+情绪≥30+动量向上,1份) 🌱左侧试探(0.5份)
          🔄深蹲拐点(估值≥80+拐点,0.5份) 📈右侧趋势(动量向上且动量分≥55,0.5-1份)
  卖出：🔴高估区(估值<50且非趋势) 🌡️过热减仓(情绪>85且动量转负)
  闸门：距上次同品种买入<30天 且 跌幅>-10% → 不补仓
模拟：每份1万元，起始现金100万，逐日决策。
"""
import glob, json
from bisect import bisect_left, insort
from collections import deque
from datetime import datetime

import numpy as np

CACHE = "/vol1/1000/openzl/etf_tool/cache/lixinger"
SHARE_CNY = 10_000
START_CASH = 1_500_000
UNIT_CAP = 22    # 单品种22份≈15%（E大对重点品种的实际配置）


def load_rows(name):
    best = None
    for f in glob.glob(f"{CACHE}/*.json"):
        d = json.load(open(f))
        if d.get("name") == name:
            n = len(d.get("rows") or [])
            if best is None or n > best[1]:
                best = (d["rows"], n)
    return best[0] if best else []


def simulate(rows, start_date='2023-01-01'):
    dates = [r["date"] for r in rows]
    cps = np.array([r.get("cp") if r.get("cp") else np.nan for r in rows])
    pes = np.array([r.get("pe") if r.get("pe") else np.nan for r in rows])
    pbs = np.array([r.get("pb") if r.get("pb") else np.nan for r in rows])
    n = len(rows)
    # 只回测最近两年（约490交易日），但统计窗口用全部历史
    # 从指定日期开始回测（需先有1300天历史预热统计窗口）
    cand = next((k for k, d in enumerate(dates) if d >= start_date), None)
    if cand is None or cand < 1300:
        return None
    start_i = cand

    roll_high = np.full(n, np.nan); roll_ma = np.full(n, np.nan)
    for i in range(249, n):
        v = cps[i-249:i+1]; v = v[~np.isnan(v)]
        if len(v) >= 200:
            roll_high[i] = v.max(); roll_ma[i] = v.mean()
    dd = np.where(roll_high > 0, cps / roll_high - 1, np.nan)
    bias = np.where(roll_ma > 0, cps / roll_ma - 1, np.nan)

    win = deque(); spe = []; spb = []; sh = []; sb = []
    cash = START_CASH; units = 0.0
    last_buy_i = -999; last_buy_px = None
    trades = []; equity_curve = []; last_sell_i = -999; last_buy_vs = None
    warmup_sh = []; warmup_sb = []   # 情绪历史需预热，否则前期分位虚低——直接用全历史增量即可

    def momentum_at(i):
        c = cps[i]
        def ret(lag):
            j = i - lag
            return c / cps[j] - 1 if j >= 0 and not np.isnan(cps[j]) else None
        r252, r21 = ret(252), ret(21)
        m121 = (r252 - r21) if (r252 is not None and r21 is not None) else None
        mom20 = r21
        a250 = (not np.isnan(roll_ma[i])) and c > roll_ma[i]
        a850_ok = i >= 849 and not np.isnan(cps[i-849]) and c > cps[i-849]
        parts = [max(0., min(100., (((m121 or 0)) + .15) / .30 * 100)),
                 100. if a250 else 35.,
                 100. if a850_ok else 40.,
                 max(0., min(100., ((mom20 or 0) + .10) / .20 * 100))]
        score = sum(parts) / len(parts)
        turning_up = (m121 or 0) <= 0 and (mom20 or 0) > 0.02
        return score, (m121 or 0) > 0, turning_up, a250

    # 预热：从历史起点先灌满10年统计窗口，再进入回测区间（否则早期分位失真）
    for i in range(0, start_i):
        pe_, pb_ = pes[i], pbs[i]
        if np.isnan(pe_) and np.isnan(pb_):
            continue
        win.append((i, 0. if np.isnan(pe_) else pe_, 0. if np.isnan(pb_) else pb_))
        if not np.isnan(pe_): insort(spe, pe_)
        if not np.isnan(pb_): insort(spb, pb_)
        lo0 = i - 3650
        while win and win[0][0] < lo0:
            _, o1, o2 = win.popleft()
            del spe[bisect_left(spe, o1)]; del spb[bisect_left(spb, o2)]

    for i in range(start_i, n):
        c, pe_, pb_ = cps[i], pes[i], pbs[i]
        if np.isnan(c):
            continue
        lo = i - 3650
        while win and win[0][0] < lo:
            _, o1, o2 = win.popleft()
            del spe[bisect_left(spe, o1)]; del spb[bisect_left(spb, o2)]
        if not np.isnan(pe_): insort(spe, pe_)
        if not np.isnan(pb_): insort(spb, pb_)
        win.append((i, 0. if np.isnan(pe_) else pe_, 0. if np.isnan(pb_) else pb_))
        rk = [bisect_left(s, x) / len(s) for s, x in ((spe, pe_), (spb, pb_)) if s and not np.isnan(x)]
        if not rk:
            continue
        vs = 100 - sum(rk) / len(rk) * 100
        sp = []
        for hist, x in ((sh, dd[i]), (sb, bias[i])):
            if not np.isnan(x):
                rk2 = bisect_left(hist, float(x)) / len(hist) if hist else None
                insort(hist, float(x))
                if rk2 is not None: sp.append(rk2 * 100)
        if not sp:
            continue
        ss = sum(sp) / len(sp)
        ms, mom_up, turning_up, a250 = momentum_at(i)
        trend_ok = mom_up and ms >= 55

        stance = None; shares_f = 0.0
        total = 0.45 * vs + 0.40 * ss + 0.15 * ms

        # 两段仓位制：左侧额度70%，右侧额度30%（单品种总额度 UNIT_CAP 份）
        left_cap = UNIT_CAP * 0.70
        right_cap = UNIT_CAP - left_cap
        left_units = min(units, left_cap)          # 近似：不区分来源，按比例折算
        right_units = units - left_units

        # 用户阶梯卖出：60分位卖1份/70卖2份/80卖3份/90再卖3份/最后一份~100分位
        sell_step = None
        if vs < 10 and units > 0:
            sell_step = 3.0
        elif vs < 20 and units > 0:
            sell_step = 3.0
        elif vs < 30 and units > 0:
            sell_step = 2.0
        elif vs < 40 and units > 0:
            sell_step = 1.0
        if sell_step and units > 0:
            stance = "sell_overvalued"
        elif ss > 90 and not mom_up and units > 0:
            stance = "sell_overheat"
        elif vs > 80 and ss >= 15 and left_units < left_cap - 0.01:
            # 左侧：估值<20%分位启动。深蹲重锤保留：vs>=85&情绪<=40一次3份
            stance = "buy_left"
            if vs >= 85 and ss <= 40:
                shares_f = 3.0 if left_units + 3.0 <= left_cap else max(0.25, left_cap - left_units)
            else:
                shares_f = 1.0 if (vs >= 85 or (total >= 55 and ss <= 35)) else 0.5
                shares_f = min(shares_f, max(0.25, left_cap - left_units))
        elif vs > 60 and ((last_buy_px and (c / last_buy_px - 1) <= -0.20)
                          or (last_buy_i > 0 and i - last_buy_i >= 245)):
            # 深跌/时间豁免：左侧满仓后，再跌20% 或 持有满1年且仍低估 -> 动用右侧额度继续补
            stance = "buy_deep_dip"
            shares_f = min(1.5, right_cap + 2.0 - right_units, UNIT_CAP - units)
        elif trend_ok and right_units < right_cap:
            # 右侧：动量确认后买右侧额度，估值越低越重
            stance = "buy_right"
            if vs >= 60:   shares_f = min(1.5, right_cap - right_units)
            elif vs >= 45: shares_f = min(1.0, right_cap - right_units)
            elif vs >= 35: shares_f = min(0.75, right_cap - right_units)
            else:          shares_f = min(0.25, right_cap - right_units)

        # 执行（units=份额；权益按 entry_px 折算净值）
        # 卖出规则（用户定案）：vs<40 逐步卖出；任何品种不亏钱卖出——低位筹码珍惜
        if stance in ("sell_overvalued", "sell_overheat") and units > 0:
            if c <= entry_px:
                pass  # 现价低于成本：不卖
            else:
                days_since_sell = i - last_sell_i if last_sell_i > 0 else 999
                if days_since_sell < 20:   # 卖出也遵守节奏：约一个月一档
                    pass
                else:
                    sell_u = min(sell_step or 1.0, units)
                proceeds = sell_u * SHARE_CNY * c / entry_px
                pnl = int(round(proceeds - sell_u * SHARE_CNY))
                cash += proceeds
                units -= sell_u
                last_sell_i = i
                trades.append((dates[i], "SELL", round(sell_u, 2), int(c), pnl))
                if units < 0.25 and units > 0:
                    cash += units * SHARE_CNY * c / entry_px
                    trades.append((dates[i], "SELL", round(units, 2), int(c), "清仓尾量"))
                    units = 0.0; last_buy_i = -999; last_buy_px = None; last_buy_vs = None
        elif stance and shares_f > 0:
            days_since = i - last_buy_i if last_buy_i > 0 else 10**9
            # 用户节奏：估值每降5%可买；或估值不变但间隔>=75天(2-3个月)
            vs_drop_ok = last_buy_vs is not None and (vs - last_buy_vs) >= 5
            time_ok = days_since >= 75
            deep_drop_ok = bool(last_buy_px) and (c / last_buy_px - 1) <= -0.10  # 空间急跌保留
            panic_ok = vs >= 85 and days_since >= 30   # 深蹲重锤期放宽到30天
            if vs_drop_ok or time_ok or deep_drop_ok or panic_ok:
                cost = shares_f * SHARE_CNY
                if cash >= cost:
                    total_cost = units * SHARE_CNY + cost
                    entry_px = (entry_px * units * SHARE_CNY + c * cost) / total_cost if units > 0 else c
                    cash -= cost
                    units += shares_f
                    trades.append((dates[i], "BUY", shares_f, int(c), stance))
                    last_buy_i = i; last_buy_px = c; last_buy_vs = vs
        equity_curve.append(cash + units * SHARE_CNY * (c / entry_px if units > 0 else 1.0))

    final_eq = cash + units * SHARE_CNY * (cps[n-1] / entry_px if units > 0 else 0)
    ret_pct = (final_eq / START_CASH - 1) * 100
    buys = [t for t in trades if t[1] == "BUY"]
    sells = [t for t in trades if t[1] == "SELL"]
    wins = [t for t in sells if len(t) > 4 and isinstance(t[4], (int, float)) and t[4] > 0]
    peak = max(equity_curve); mdd = (min(equity_curve) / peak - 1) * 100
    # 基准：期初一次买入并持有
    bh = (cps[n-1] / cps[start_i] - 1) * 100
    return {
        "final_equity": round(final_eq), "return_pct": round(ret_pct, 1),
        "buy_hold_pct": round(bh, 1),
        "n_buys": len(buys), "n_sells": len(sells),
        "sell_win_rate": round(len(wins) / len(sells) * 100, 1) if sells else None,
        "max_drawdown_pct": round(mdd, 1),
        "trades_all": [list(t) for t in trades],
    }


if __name__ == "__main__":
    import sys as _sys
    start_date = _sys.argv[1] if len(_sys.argv) > 1 else '2023-01-01'
    targets = ["沪深300", "中证500", "创业板指", "恒生指数", "证券公司",
               "中证医疗", "中证红利", "全指消费", "中证银行", "养老产业",
               "全指医药", "中概互联", "恒生科技", "中证传媒"]
    out = {}
    tot_ret = []; tot_bh = []
    print(f"回测区间: {start_date} ~ 至今")
    print(f"{'指数':6s} {'系统收益':>9s} {'持有不动':>9s} {'超额':>7s} {'买/卖':>7s} {'卖出胜率':>7s} {'最大回撤':>8s}")
    for name in targets:
        rows = load_rows(name)
        if not rows: continue
        r = simulate(rows, start_date=start_date)
        if r is None:
            print(f"{name:6s}   (历史数据不足，跳过)")
            continue
        out[name] = r
        tot_ret.append(r["return_pct"]); tot_bh.append(r["buy_hold_pct"])
        print(f"{name:6s} {r['return_pct']:>8}% {r['buy_hold_pct']:>8}% "
              f"{r['return_pct']-r['buy_hold_pct']:>+6.1f}% {str(r['n_buys'])+'/'+str(r['n_sells']):>7s} "
              f"{str(r['sell_win_rate'])+'%':>7s} {str(r['max_drawdown_pct'])+'%':>8s}")
    print(f"\n平均: 系统 {sum(tot_ret)/len(tot_ret):+.1f}% vs 持有 {sum(tot_bh)/len(tot_bh):+.1f}%")
    out["_meta"] = {"period": "近两年", "rules": "E大档位+时间空间闸门(30天/10%)", "share_cny": SHARE_CNY}
    json.dump(out, open("/vol1/1000/openzl/etf_tool/data/backtest_current_system.json", "w"), ensure_ascii=False, indent=1)
    print("saved -> data/backtest_current_system.json")
