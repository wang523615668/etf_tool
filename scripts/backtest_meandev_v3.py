# 均值偏离版v3（2026-08-23）：按各指数历史溢价分布定制买卖阈值
# 通用骨架：低于均值开始买、每低X%加一份；高于P90附近开始卖、每高Y%卖一份；
# 深度低估(低于P10)加倍买入；成本价以下不卖。阈值 = 各品种历史分位数。
import sys
sys.path.insert(0, '/vol1/1000/openzl/etf_tool/scripts')
import numpy as np
from backtest_current import load_rows, START_CASH, SHARE_CNY

UNIT_CAP = 37.5  # 单品种25%（150万×25%/1万）

# 每个品种的定制阈值：(buy_start, buy_step, deep_buy, sell_start, sell_step)
#   buy_start: 溢价低于此值开始买（取历史P25~中位之间）
#   buy_step : 每再便宜多少加一份（约P5-P25跨度的1/4）
#   deep_buy : 溢价低于此值每次买2份（历史P10以下）
#   sell_start: 溢价高于此值开始卖（≈历史P90）
#   sell_step: 每再贵多少卖一份
RULES = {
    "沪深300":  (-0.05, 0.12, -0.45, +0.08, 0.10),
    "中证500":  (-0.05, 0.13, -0.55, +0.15, 0.12),
    "创业板指":  (-0.05, 0.14, -0.55, +0.18, 0.13),
    "恒生指数":  (-0.03, 0.11, -0.35, +0.13, 0.10),
    "证券公司":  (-0.05, 0.12, -0.38, +0.06, 0.09),
    "中证医疗":  (-0.15, 0.13, -0.58, +0.00, 0.10),
    "中证红利":  (-0.05, 0.12, -0.40, +0.06, 0.10),
    "全指消费":  (-0.04, 0.10, -0.34, +0.08, 0.09),
    "中证银行":  (-0.04, 0.10, -0.33, +0.09, 0.10),
    "养老产业":  (-0.06, 0.11, -0.36, +0.06, 0.09),
    "全指医药":  (-0.08, 0.13, -0.52, +0.00, 0.09),
    "中概互联":  (-0.05, 0.16, -0.56, +0.20, 0.14),
    "中证传媒":  (-0.06, 0.15, -0.62, +0.11, 0.12),
}


def simulate(rows, start_date='2023-01-01', rule=None):
    buy_start, buy_step, deep_buy, sell_start, sell_step = rule
    dates = [r["date"] for r in rows]
    cps = np.array([r.get("cp") if r.get("cp") else np.nan for r in rows])
    pes = np.array([r.get("pe") if r.get("pe") else np.nan for r in rows])
    pbs = np.array([r.get("pb") if r.get("pb") else np.nan for r in rows])
    n = len(rows)
    cand = next((k for k, d in enumerate(dates) if d >= start_date), None)
    if cand is None or cand < 1300:
        return None
    start_i = cand
    cash = START_CASH; units = 0.; entry_px = None
    last_buy_i = -999; last_buy_px = None; last_buy_prem = None; last_sell_i = -999
    trades = []; eq = []

    def prem_at(i):
        devs = []
        for arr in (pes, pbs):
            lo = max(0, i - 1225); v = arr[lo:i+1]; v = v[~np.isnan(v)]
            if len(v) < 500 or np.isnan(arr[i]) or arr[i] <= 0:
                continue
            devs.append(v.mean() / arr[i] - 1)  # 正=现价便宜
        return (-sum(devs)/len(devs)) if devs else None  # 返回溢价

    for i in range(start_i, n):
        c = cps[i]
        if np.isnan(c): continue
        prem = prem_at(i)
        if prem is None: continue
        stance = None; shares_f = 0.
        # 卖出：溢价超sell_start，每高sell_step卖1份，超过sell_start+3*step后加倍
        sell_lvl = None
        if prem > sell_start:
            steps = int((prem - sell_start) / sell_step) + 1
            sell_lvl = 2.0 if steps >= 4 else 1.0
        if sell_lvl and units > 0:
            stance = "sell"
        elif prem < -(-buy_start) and False:
            pass
        # 买入：溢价 < buy_start（注意buy_start为负表示需低于均值|buy_start|）
        if stance is None and (prem - buy_start) < 0 and units < UNIT_CAP * (1.6 if prem < deep_buy else 1.0):
            stance = "buy"
            step = 2.0 if prem < deep_buy else 1.0
            shares_f = min(step, UNIT_CAP * (1.6 if prem < deep_buy else 1.0) - units)

        if stance == "sell":
            if entry_px and c <= entry_px:
                pass
            elif last_sell_i > 0 and i - last_sell_i < 20:
                pass
            else:
                su = min(sell_lvl, units)
                pr = su * SHARE_CNY * c / entry_px
                pnl = int(round(pr - su * SHARE_CNY))
                cash += pr; units -= su; last_sell_i = i
                trades.append((dates[i], "SELL", round(su, 2), int(c), pnl))
                if 0 < units < 0.25:
                    cash += units * SHARE_CNY * c / entry_px
                    trades.append((dates[i], "SELL", round(units, 2), int(c), "尾"))
                    units = 0.
        elif stance == "buy" and shares_f > 0:
            days_since = i - last_buy_i if last_buy_i > 0 else 10**9
            lvl_ok = last_buy_prem is not None and (last_buy_prem - prem) >= buy_step
            t_ok = days_since >= 75
            deep_ok = bool(last_buy_px) and (c / last_buy_px - 1) <= -0.10
            if lvl_ok or t_ok or deep_ok:
                cost = shares_f * SHARE_CNY
                if cash >= cost:
                    tc = units * SHARE_CNY + cost
                    entry_px = (entry_px * units * SHARE_CNY + c * cost) / tc if units > 0 else c
                    cash -= cost; units += shares_f
                    trades.append((dates[i], "BUY", round(shares_f, 2), int(c), f"prem={prem:+.0%}"))
                    last_buy_i = i; last_buy_px = c; last_buy_prem = prem
        eq.append(cash + units * SHARE_CNY * (c / entry_px if units > 0 and entry_px else 1.))

    fe = cash + units * SHARE_CNY * (cps[n-1] / entry_px if units > 0 and entry_px else 1.)
    sells = [t for t in trades if t[1] == "SELL"]
    wins = [t for t in sells if len(t) > 4 and isinstance(t[4], (int, float)) and t[4] > 0]
    peak = max(eq); mdd = (min(eq) / peak - 1) * 100
    invested = sum(t[2] for t in trades if t[1] == "BUY")
    return {"ret": round((fe/START_CASH-1)*100,1), "bh": round((cps[n-1]/cps[start_i]-1)*100,1),
            "nb": len([t for t in trades if t[1]=="BUY"]), "ns": len(sells),
            "wr": round(len(wins)/len(sells)*100,1) if sells else None,
            "mdd": round(mdd,1), "left": round(units,1), "inv": round(invested,1),
            "trades": trades}


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else '2023-01-01'
    print(f"===== {start}起 均值v3（按各指数历史分布定制阈值） =====")
    ts = th = cnt = 0
    for nm, rule in RULES.items():
        rows = load_rows(nm)
        if not rows: continue
        r = simulate(rows, start, rule)
        if not r: print(f"{nm} 数据不足"); continue
        ex = r['ret'] - r['bh']
        print(f"{nm:8s} 系统{r['ret']:7.1f}% 持有{r['bh']:7.1f}% 超额{ex:+7.1f}% 买{r['nb']}/卖{r['ns']} "
              f"胜率{str(r['wr']):>6s}% 回撤{r['mdd']:>6}% 余{r['left']}份 投入{r['inv']}份")
        ts += r['ret']; th += r['bh']; cnt += 1
    print(f"\n平均: 系统 +{ts/cnt:.1f}% vs 持有 +{th/cnt:.1f}%")
