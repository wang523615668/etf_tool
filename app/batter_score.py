"""击球分数引擎（Batter Score）— 三维量化合成买卖参数。

设计（2026-08-22 与用户确认）：
  击球分数 = 估值(40%) + 情绪(30%) + 动量(30%)，各维度 0~100。

  ① 赔率·估值（40%）：PE/PB 长窗口分位越低分越高（复用现有快照温度）。
  ② 恐慌·情绪（30%）："所有都悲观才是买入好时机"
     - 成交额 5 年分位（腾讯 ifzq 日线 volume；理杏仁免费层无成交额）
     - 距 250 日高点回撤深度
     - 连续下跌周数 / 价格距 250 日均线偏离
     自定义 CSI 指数（H30269/930955/H11136）腾讯无数据 → 回退仅用 cp 序列情绪项。
  ③ 趋势·动量（30%）：解决"低估但躺平数年"
     - 12-1 月动量（过去 12 个月剔除最近 1 月收益）
     - 价格 vs MA250 / MA850 相对位置
     - 20 日动量方向（短期确认）

决策档位（价值选品种 + 动量定时机）：
  value>=80 & sent>=70 & mom_up   -> 主力买入（1份/次）
  value>=80 & sent>=70 & !mom_up  -> 左侧试探（0.5份/次，严格分批）
  value>=80 & sent<40             -> 观察（还有人抢，不追）
  value<50                        -> 高估区只卖不买
  其余                            -> 等待
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE / "cache"

# 腾讯无 kline 的自定义中证指数 → 情绪只用本地 cp 序列
TENCENT_UNSUPPORTED = {"H30269", "930955", "H11136"}

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def _tencent_symbol(area: str, code: str) -> str | None:
    """映射到腾讯行情符号；不支持的返回 None。"""
    if code in TENCENT_UNSUPPORTED:
        return None
    if area == "hk":
        return f"hk{code}"
    # cn: 399xxx 深市，其余按 sh 处理（000/930 开头的中证指数在腾讯走 sh 前缀可取到的已验证；
    # H 开头自定义代码不支持 → None）
    if code.startswith("399"):
        return f"sz{code}"
    return f"sh{code}"


_KLINE_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_KLINE_TTL = 6 * 3600.0


def fetch_kline_years(symbol: str, years: int = 6) -> list[dict[str, Any]]:
    """腾讯 ifzq 日线（含成交量），返回 [{date, close, volume}, ...] 升序。
    分段拉取（每年一段）避免单请求截断。失败返回 []。"""
    hit = _KLINE_CACHE.get(symbol)
    now = time.time()
    if hit and now - hit[0] < _KLINE_TTL:
        return hit[1]

    out: dict[str, dict[str, Any]] = {}
    today = datetime.now().date()
    for y in range(years):
        end = today - timedelta(days=365 * y)
        start = end - timedelta(days=365)
        url = (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
            f"param={symbol},day,{start.isoformat()},{end.isoformat()},320,qfq"
        )
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=12) as resp:
                payload = json.loads(resp.read().decode())
            node = (payload.get("data") or {}).get(symbol) or {}
            for row in node.get("day") or node.get("qfqday") or []:
                # [date, open, close, high, low, volume]
                if len(row) >= 6:
                    out[row[0]] = {
                        "date": row[0],
                        "close": float(row[2]),
                        "volume": float(row[5]),
                    }
        except Exception:
            continue
    rows = [out[k] for k in sorted(out.keys())]
    if rows:
        _KLINE_CACHE[symbol] = (now, rows)
    return rows


def _pct_rank(values: list[float], current: float) -> float | None:
    """current 在 values 中的分位（0~1）。"""
    clean = [v for v in values if v is not None and v == v]
    if not clean or current is None or current != current:
        return None
    below = sum(1 for v in clean if v <= current)
    return below / len(clean)


def _rolling_mean(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _sentiment_from_kline(klines: list[dict[str, Any]]) -> dict[str, Any] | None:
    closes = [k["close"] for k in klines]
    vols = [k["volume"] for k in klines]
    if len(closes) < 250:
        return None
    cur = closes[-1]

    vol_20d = sum(vols[-20:]) / 20
    vol_rank = _pct_rank(vols, vol_20d)

    high_250 = max(closes[-250:])
    drawdown = cur / high_250 - 1.0  # 负值，越负越恐慌

    # 连续下跌周数（用周五收盘近似）
    weekly = closes[4::5]
    down_weeks = 0
    for i in range(len(weekly) - 1, 0, -1):
        if weekly[i] < weekly[i - 1]:
            down_weeks += 1
        else:
            break

    ma250 = _rolling_mean(closes, 250)
    ma_bias = (cur / ma250 - 1.0) if ma250 else None

    # 打分：三项都处于历史悲观区才给高分
    parts: list[float] = []
    detail: dict[str, Any] = {}

    if vol_rank is not None:
        s_vol = (1 - vol_rank) * 100  # 成交越冷清分越高
        parts.append(s_vol)
        detail["vol_rank_pct"] = round(vol_rank * 100, 1)

    # 回撤深度：历史回撤分布的分位（当前回撤越接近历史最深，恐慌分越高）
    dd_series = []
    for i in range(250, len(closes)):
        w = closes[i - 250 : i + 1]
        dd_series.append(closes[i] / max(w) - 1)
    if dd_series and drawdown == drawdown:
        dd_rank = _pct_rank(dd_series, drawdown)  # 当前回撤在历史的深度位置
        s_dd = dd_rank * 100  # 分位高 = 当前比历史上多数时间更惨
        parts.append(s_dd)
        detail["drawdown_pct"] = round(drawdown * 100, 1)
        detail["drawdown_hist_rank"] = round((dd_rank or 0) * 100, 1)

    if ma_bias is not None:
        bias_series = []
        for i in range(250, len(closes)):
            w = closes[i - 250 : i + 1]
            m = sum(w) / len(w)
            bias_series.append(closes[i] / m - 1)
        b_rank = _pct_rank(bias_series, ma_bias)
        s_bias = (b_rank or 0.5) * 100
        parts.append(s_bias)
        detail["ma250_bias_pct"] = round(ma_bias * 100, 1)

    detail["down_weeks"] = down_weeks

    if not parts:
        return None
    return {"score": round(sum(parts) / len(parts), 1), "detail": detail}


def _momentum(klines: list[dict[str, Any]]) -> dict[str, Any] | None:
    closes = [k["close"] for k in klines]
    if len(closes) < 260:
        return None
    cur = closes[-1]

    p_1m = closes[-21] if len(closes) > 21 else closes[0]
    p_12m = closes[-252] if len(closes) > 252 else closes[0]
    mom_12_1 = (cur / p_1m - 1) if p_1m else None          # 近11个月
    mom_full = (cur / p_12m - 1) if p_12m else None         # 12个月全量
    mom_20d = (cur / closes[-21] - 1)                       # 20日短期动量

    ma250 = _rolling_mean(closes, 250)
    ma850 = _rolling_mean(closes, 850)

    above_ma250 = bool(ma250 and cur > ma250)
    above_ma850 = bool(ma850 and cur > ma850)

    score_parts = []
    if mom_12_1 is not None:
        # 12-1 动量：>15% 满分，<-15% 零分，线性
        score_parts.append(max(0.0, min(100.0, (mom_12_1 + 0.15) / 0.30 * 100)))
    score_parts.append(100.0 if above_ma250 else 35.0)
    score_parts.append(100.0 if above_ma850 else 40.0)
    score_parts.append(max(0.0, min(100.0, (mom_20d + 0.10) / 0.20 * 100)))

    # 动量拐点：20日动量由负转正且仍低于 MA250（左侧转右侧的早期信号）
    prev_20d = closes[-22] / closes[-42] - 1 if len(closes) > 42 else None
    turning_up = bool(
        mom_20d is not None and mom_20d > 0
        and prev_20d is not None and prev_20d <= 0
    )

    return {
        "score": round(sum(score_parts) / len(score_parts), 1),
        "mom_12_1_pct": round(mom_12_1 * 100, 1) if mom_12_1 is not None else None,
        "mom_20d_pct": round(mom_20d * 100, 1),
        "above_ma250": above_ma250,
        "above_ma850": above_ma850,
        "turning_up": turning_up,
        "mom_positive": bool((mom_12_1 or 0) > 0 or turning_up),
    }


def build_batter_scores(valuation_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """主入口：输入估值快照 rows（含 name/code/area/temperature/action），
    输出每个品种的三维分数 + 合成击球分数 + 决策档位。"""
    from app.data_sources.lixinger import INDEX_CONFIG

    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for row in valuation_rows:
        name = row.get("name")
        cfg = INDEX_CONFIG.get(name)
        if not cfg:
            continue
        area, code = cfg.get("area", "cn"), cfg.get("code")

        # --- 估值分：温度越低分越高（温度=PE/PB分位均值×100）
        temp = row.get("temperature")
        value_score = max(0.0, min(100.0, 100 - temp)) if temp is not None else None

        # --- 行情序列
        symbol = _tencent_symbol(area, code)
        klines = fetch_kline_years(symbol) if symbol else []
        sentiment = _sentiment_from_kline(klines) if klines else None
        momentum = _momentum(klines) if klines else None

        # 腾讯不可用的指数：用本地 lixinger cp 序列做降级情绪/动量（无成交量项）
        fallback_note = None
        if not klines:
            fallback_note = "自定义指数无公开行情，情绪/动量基于点位序列降级计算"
            try:
                from app.data_sources.lixinger import get_index_detail
                det = get_index_detail(name, allow_network=False)
                cps = [(r.get("date"), r.get("cp")) for r in (det.get("rows") or []) if r.get("cp")]
                klines_fb = [{"date": d, "close": c, "volume": 0.0} for d, c in cps]
                sentiment = _sentiment_from_kline(klines_fb)
                momentum = _momentum(klines_fb)
                if sentiment:
                    sentiment["detail"].pop("vol_rank_pct", None)
            except Exception as exc:
                errors.append(f"{name}: {exc}")

        if value_score is None or sentiment is None or momentum is None:
            results.append({
                "name": name, "code": code,
                "value_score": value_score,
                "sentiment_score": sentiment["score"] if sentiment else None,
                "momentum_score": momentum["score"] if momentum else None,
                "total_score": None, "stance": "数据不足",
                "note": fallback_note or "行情序列不足",
            })
            continue

        total = round(value_score * 0.45 + sentiment["score"] * 0.40 + momentum["score"] * 0.15, 1)

        # --- 时间空间闸门（E大原则，2026-08-22 用户定参：间隔≥30天 或 再跌≥10%）
        # 读取账本该品种最近一次买入；未满足则降级买入档为"等待补仓窗口"
        gate_note = None
        try:
            from app.decision_memory import load_my_trades
            trades = load_my_trades().get("trades") or []
            my_buys = [t for t in trades
                       if t.get("action") == "buy" and (t.get("name") == name or t.get("code") == str(code))]
            if my_buys:
                last = max(my_buys, key=lambda t: t.get("date", ""))
                from datetime import date as _date
                ld = _date.fromisoformat(str(last.get("date"))[:10])
                days_since = (datetime.now().date() - ld).days
                last_px = last.get("price")
                cur_px = momentum.get("close") or (klines[-1]["close"] if klines else None)
                drop_pct = (cur_px / last_px - 1) if (last_px and cur_px) else None
                cooldown_ok = days_since >= 30
                drop_ok = drop_pct is not None and drop_pct <= -0.10
                if not (cooldown_ok or drop_ok):
                    gate_note = f"时间空间未到：上次买入{days_since}天前@{last_px}，现价距上次{drop_pct*100 if drop_pct is not None else '?':.1f}%（需≥30天或再跌10%）"
        except Exception:
            pass

        # --- 决策档位（2026-08-22 按 E大330篇发言归纳重构）
        # E大规则：① 共振区买入 = 技术+价值双确认，不要求极端恐慌
        #          ② 时间空间原则：距上次同品种买入 ≥30天 或 再跌10% 即可补一份
        #          ③ 右侧加仓吃主升浪：动量向上+站上均线即买，不限估值
        #          ④ 卖出双通道：估值<50；或 情绪>85 且动量转负（右侧卖出）
        vs, ss = value_score, sentiment["score"]
        mom_up = momentum["mom_positive"]
        turning_up = momentum.get("turning_up", False)
        trend_ok = mom_up and momentum["score"] >= 55   # 右侧趋势确认

        # 时间空间闸门：只压制买入档（不覆盖卖出/过热信号）
        if gate_note:
            stance, shares = "⏳ 补仓窗口未到", "时间/空间未满足"
            results.append({
                "name": name, "code": code, "temperature": temp,
                "value_score": round(vs, 1),
                "sentiment_score": sentiment["score"],
                "sentiment_detail": sentiment["detail"],
                "momentum_score": momentum["score"],
                "momentum_detail": {k: v for k, v in momentum.items() if k != "score"},
                "total_score": total,
                "stance": stance, "suggested_shares": shares,
                "note": gate_note,
            })
            continue

        if vs < 50 and not trend_ok:
            stance, shares = "🔴 高估区", "只卖不买"
        elif ss > 85 and not mom_up:
            stance, shares = "🌡️ 过热减仓", "右侧卖出信号"
        elif vs >= 70 and ss >= 30:
            # 共振区：低估(放宽到70)+情绪开始降温即可买，不再要求70恐慌
            stance, shares = ("🚀 主力买入", "1 份/次") if mom_up else ("🌱 左侧试探", "0.5 份/次")
        elif vs >= 80 and turning_up:
            stance, shares = "🔄 深蹲拐点", "0.5 份/次"
        elif trend_ok and momentum.get("above_ma250") is not False and vs < 50 and value_score >= 35:
            stance, shares = "📈 右侧趋势", "1 份/次（趋势仓）"
        elif trend_ok:
            stance, shares = "📈 右侧趋势", "0.5-1 份/次（趋势仓）"
        elif vs >= 80:
            stance, shares = "👀 低估未共振", "等情绪降温或拐点"
        else:
            stance, shares = "⏳ 等待", "—"

        results.append({
            "name": name,
            "code": code,
            "temperature": temp,
            "value_score": round(vs, 1),
            "sentiment_score": sentiment["score"],
            "sentiment_detail": sentiment["detail"],
            "momentum_score": momentum["score"],
            "momentum_detail": {k: v for k, v in momentum.items() if k != "score"},
            "total_score": total,
            "stance": stance,
            "suggested_shares": shares,
        })

    order = {"🚀 主力买入": 0, "🌱 左侧试探": 1, "🔄 深蹲拐点": 2, "📈 右侧趋势": 3,
             "👀 低估未共振": 4, "⏳ 等待": 5, "🌡️ 过热减仓": 2, "🔴 高估区": 6, "数据不足": 9}
    results.sort(key=lambda r: (order.get(r.get("stance"), 8), -(r.get("total_score") or 0)))

    # --- 现金管理（C方案）：闲置资金建议
    try:
        from app.decision_memory import load_my_trades
        acct_cash = 1_500_000.0
        invested_shares = 0.0
        trades = load_my_trades().get("trades") or []
        held: dict[str, float] = {}
        for t in trades:
            if t.get("action") == "buy":
                held[t.get("name")] = held.get(t.get("name"), 0.0) + (t.get("shares") or 0)
            elif t.get("action") in ("sell", "减仓", "清仓"):
                held[t.get("name")] = held.get(t.get("name"), 0.0) - (t.get("shares") or 0)
        invested_shares = sum(v for v in held.values() if v > 0)
        idle_ratio = max(0.0, 1 - invested_shares / 150.0)   # 150份总盘
        if idle_ratio >= 0.7:
            cash_advice = f"现金占比{idle_ratio*100:.0f}%，建议将闲置资金的70%配置短债/货基（如易方达1-3年国开债），保留30%等待击球"
        elif idle_ratio >= 0.4:
            cash_advice = f"现金占比{idle_ratio*100:.0f}%，可将一半闲置资金配置短债打底，保留弹药等低估区"
        else:
            cash_advice = f"仓位{100-idle_ratio*100:.0f}%，现金管理正常"
    except Exception:
        cash_advice = None

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "weights": {"value": 0.45, "sentiment": 0.40, "momentum": 0.15},
        "buy_rhythm": "估值<20%分位启动；每降5%买一份；估值不变间隔≥75天；深跌10%豁免",
        "sell_ladder": "60分位卖1份/70卖2份/80卖3份/90再卖3份；成本价以下不卖出",
        "cash_management": cash_advice,
        "rows": results,
        "errors": errors,
    }


_BATTER_CACHE: tuple[float, dict[str, Any]] | None = None
_BATTER_TTL = 1800.0  # 30 分钟（行情日内缓变，腾讯拉取有成本）


def batter_dashboard(force: bool = False) -> dict[str, Any]:
    global _BATTER_CACHE
    now = time.time()
    if not force and _BATTER_CACHE and now - _BATTER_CACHE[0] < _BATTER_TTL:
        return _BATTER_CACHE[1]
    from app.main import valuation_rows  # 延迟导入避免循环
    dash = build_batter_scores(valuation_rows(source="lixinger"))
    _BATTER_CACHE = (now, dash)
    return dash
