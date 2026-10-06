# 均值偏离版回测（用户2026-08-23新规则）：
# 买入：估值低于5年均值开始买；每再低5%（相对均值）买一份；低于均值30%后加倍买；
#       单品种上限25%总资金（37.5份）；偏离超50%可突破上限。
# 卖出：对称映射原阶梯——高于均值20%/30%/40%/50%分别卖1/2/3/3份；成本价以下不卖。
import sys
sys.path.insert(0, '/vol1/1000/openzl/etf_tool/scripts')
import numpy as np
from bisect import bisect_left

from backtest_current import load_rows, START_CASH, SHARE_CNY

UNIT_CAP = 150 * 0.25 / (SHARE_CNY / SHARE_CNY)  # 150万*25% / 1万 = 37.5份


def simulate(rows, start_date='2023-01-01', warmup_days=1300):
    dates = [r["date"] for r in rows]
    cps = np.array([r.get("cp") if r.get("cp") else np.nan for r in rows])
    pes = np.array([r.get("pe") if r.get("pe") else np.nan for r in rows])
    pbs = np.array([r.get("pb") if r.get("pb") else np.nan for r in rows])
    n = len(rows)
    cand = next((k for k, d in enumerate(dates) if d >= start_date), None)
    if cand is None or cand < warmup_days:
        return None
    start_i = cand

    cash = START_CASH; units = 0.0; entry_px = None
    last_buy_i = -999; last_buy_px = None; last_buy_dev = None
    last_sell_i = -999
    trades = []; equity_curve = []

    def mean5y(i):
        """PE/PB相对各自5年(约1225交易日)均值的加权偏离：dev = 均值/现价 - 1（正=便宜）"""
        devs = []
        for arr in (pes, pbs):
            lo = max(0, i - 1225)
            v = arr[lo:i+1]; v = v[~np.isnan(v)]
            if len(v) < 500 or np.isnan(arr[i]) or arr[i] <= 0:
                continue
            m = v.mean()
            devs.append(m / arr[i] - 1)  # 现价比均值低10% -> dev=0.111
        return sum(devs) / len(devs) if devs else None

    for i in range(start_i, n):
        c = cps[i]
        if np.isnan(c):
            continue
        dev = mean5y(i)
        if dev is None:
            continue

        # ---- 用户买入规则 ----
        stance = None; shares_f = 0.0
        cap_now = UNIT_CAP if dev >= 0.50 else UNIT_CAP * 2 if False else UNIT_CAP
        # dev>=50% 可突破上限：上限放宽到40%
        hard_cap = UNIT_CAP * 1.6 if dev >= 0.50 else UNIT_CAP
        step = 2.0 if dev >= 0.30 else 1.0   # 低于均值30%加倍
        if dev > 0.05 and units < hard_cap:
            stance = "buy_underval"
            shares_f = min(step, hard_cap - units)

        # ---- 对称阶梯卖出（高于均值溢价）----
        sell_step = None
        prem = -dev  # 溢价
        if prem > 0.50 and units > 0: sell_step = 3.0
        elif prem > 0.40 and units > 0: sell_step = 3.0
        elif prem > 0.30 and units > 0: sell_step = 2.0
        elif prem > 0.20 and units > 0: sell_step = 1.0
        if sell_step:
            stance = "sell_premium"

        if stance == "sell_premium" and units > 0:
            if entry_px and c <= entry_px:
                pass
            elif i - last_sell_i < 20 if last_sell_i > 0 else False:
                pass
            else:
                sell_u = min(sell_step, units)
                proceeds = sell_u * SHARE_CNY * c / entry_px
                pnl = int(round(proceeds - sell_u * SHARE_CNY))
                cash += proceeds; units -= sell_u; last_sell_i = i
                trades.append((dates[i], "SELL", round(sell_u, 2), int(c), pnl))
                if 0 < units < 0.25:
                    cash += units * SHARE_CNY * c / entry_px
                    trades.append((dates[i], "SELL", round(units, 2), int(c), "清仓尾量"))
                    units = 0.0
        elif stance == "buy_underval" and shares_f > 0:
            days_since = i - last_buy_i if last_buy_i > 0 else 10**9
            dev_drop_ok = last_buy_dev is not None and (dev - last_buy_dev) >= 0.05
            time_ok = days_since >= 75
            deep_ok = bool(last_buy_px) and (c / last_buy_px - 1) <= -0.10
            if dev_drop_ok or time_ok or deep_ok:
                cost = shares_f * SHARE_CNY
                if cash >= cost:
                    total_cost = units * SHARE_CNY + cost
                    entry_px = (entry_px * units * SHARE_CNY + c * cost) / total_cost if units > 0 else c
                    cash -= cost; units += shares_f
                    trades.append((dates[i], "BUY", round(shares_f, 2), int(c), f"dev={dev:.0%}"))
                    last_buy_i = i; last_buy_px = c; last_buy_dev = dev
        equity_curve.append(cash + units * SHARE_CNY * (c / entry_px if units > 0 and entry_px else 1.0))

    final_eq = cash + units * SHARE_CNY * (cps[n-1] / entry_px if units > 0 and entry_px else 1.0)
    ret_pct = (final_eq / START_CASH - 1) * 100
    bh = (cps[n-1] / cps[start_i] - 1) * 100
    sells = [t for t in trades if t[1] == "SELL"]
    wins = [t for t in sells if len(t) > 4 and isinstance(t[4], (int, float)) and t[4] > 0]
    peak = max(equity_curve); mdd = (min(equity_curve) / peak - 1) * 100
    return {
        "return_pct": round(ret_pct, 1), "buy_hold_pct": round(bh, 1),
        "n_buys": len([t for t in trades if t[1] == "BUY"]),
        "n_sells": len(sells),
        "sell_win_rate": round(len(wins) / len(sells) * 100, 1) if sells else None,
        "max_drawdown_pct": round(mdd, 1),
        "final_units": round(units, 1),
    }


if __name__ == "__main__":
    import json, glob
    names = ["沪深300", "中证500", "创业板指", "恒生指数", "证券公司", "中证医疗",
             "中证红利", "全指消费", "中证银行", "养老产业", "全指医药", "中概互联",
             "中证传媒"]
    start = sys.argv[1] if len(sys.argv) > 1 else '2023-01-01'
    print(f"区间 {start} ~ 至今 | 单品种上限37.5份(25%) | 阶梯: 低于均值5%起步/30%加倍")
    tot_s = tot_h = cnt = 0
    for name in names:
        rows = load_rows(name)
        if not rows:
            continue
        r = simulate(rows, start)
        if not r:
            print(f"{name} 数据不足"); continue
        ex = r['return_pct'] - r['buy_hold_pct']
        print(f"{name:8s} 系统{r['return_pct']:7.1f}%  持有{r['buy_hold_pct']:7.1f}%  "
              f"超额{ex:+7.1f}%  买{r['n_buys']}/卖{r['n_sells']}  "
              f"胜率{str(r['sell_win_rate']):>6s}%  回撤{r['max_drawdown_pct']}%  余{r['final_units']}份")
        tot_s += r['return_pct']; tot_h += r['buy_hold_pct']; cnt += 1
    print(f"\n平均: 系统 +{tot_s/cnt:.1f}% vs 持有 +{tot_h/cnt:.1f}%")
