"""Personal capital ledger for practical decision system.

Rules (user-defined):
- principal 1,500,000 RMB split into 150 shares
- each buy = 1 share by default
- single category group cap = 25% of principal
- medical / 医药 / 医疗 merge into one group
- show P&L and return rate
- overall equity target from A股全指 10y PE/PB percentile: target = 1 - avg_percentile
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.decision_memory import apply_execution_filter, load_my_trades

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
ACCOUNT_FILE = DATA / "my_account.json"

# 决策参数配置（从配置读取，有默认值）
try:
    from app.main import CONFIG
    MARKET_INDEX_NAME = CONFIG["market_position"]["index_name"]
    MARKET_WINDOW_YEARS = float(CONFIG["market_position"]["window_years"])
    BUY_COOLDOWN_DAYS = CONFIG["decision"]["buy_cooldown_days"]
    BUY_DROP_RESUME_PCT = CONFIG["decision"]["buy_drop_resume_pct"]
    SELL_COOLDOWN_DAYS = CONFIG["decision"]["sell_cooldown_days"]
    SELL_RISE_RESUME_PCT = CONFIG["decision"]["sell_rise_resume_pct"]
    CATEGORY_CAP_PCT = CONFIG["decision"]["category_cap_pct"]
except ImportError:
    # 默认值，当app.main不可用时使用
    MARKET_INDEX_NAME = "A股全指"
    MARKET_WINDOW_YEARS = 10.0
    BUY_COOLDOWN_DAYS = 30
    BUY_DROP_RESUME_PCT = 0.10
    SELL_COOLDOWN_DAYS = 30
    SELL_RISE_RESUME_PCT = 0.10
    CATEGORY_CAP_PCT = 0.25

DEFAULT_ACCOUNT = {
    "principal": 1_500_000.0,
    "total_shares": 150,
    "shares_per_buy": 1.0,
    "category_cap_pct": CATEGORY_CAP_PCT,
    "cash": 1_500_000.0,
    "note": "本金150万 / 150份 / 每次1份 / 同类≤25% / 总仓=1−全A等权PE分位",
}

# 更新默认配置中的cap值
DEFAULT_ACCOUNT["category_cap_pct"] = CATEGORY_CAP_PCT

# map raw category/name keywords → exposure group (for 25% cap)
CATEGORY_GROUP_ALIASES: dict[str, str] = {
    "医药": "医药医疗",
    "医疗": "医药医疗",
    "全指医药": "医药医疗",
    "中证医疗": "医药医疗",
    "养老产业": "医药医疗",
    "医药卫生": "医药医疗",
    "创新药": "医药医疗",
    "生物医药": "医药医疗",
    "消费": "消费",
    "全指消费": "消费",
    "中证白酒": "消费",
    "白酒": "消费",
    "红利低波": "红利",
    "中证红利": "红利",
    "红利": "红利",
    "红利低波100": "红利",
    "金融地产": "金融",
    "全指金融": "金融",
    "中证银行": "金融",
    "银行": "金融",
    "券商": "金融",
    "证券公司": "金融",
    "中概互联": "中概互联",
    "港股科技": "港股科技",
    "港股": "港股",
    "全指信息": "科技",
    "科技": "科技",
    "新能源环保": "新能源",
    "债券现金": "债券现金",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def share_unit_value(account: dict[str, Any] | None = None) -> float:
    acc = account or load_account()
    principal = float(acc.get("principal") or DEFAULT_ACCOUNT["principal"])
    total = float(acc.get("total_shares") or DEFAULT_ACCOUNT["total_shares"]) or 150.0
    return principal / total


def category_group(category: str | None = None, name: str | None = None) -> str:
    cat = str(category or "").strip()
    nm = str(name or "").strip()
    if cat in CATEGORY_GROUP_ALIASES:
        return CATEGORY_GROUP_ALIASES[cat]
    if nm in CATEGORY_GROUP_ALIASES:
        return CATEGORY_GROUP_ALIASES[nm]
    for key, group in CATEGORY_GROUP_ALIASES.items():
        if key and (key in cat or key in nm):
            return group
    return cat or nm or "其他"


def load_account() -> dict[str, Any]:
    if not ACCOUNT_FILE.exists():
        acc = dict(DEFAULT_ACCOUNT)
        acc["updated_at"] = None
        return acc
    try:
        raw = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
    except Exception:
        acc = dict(DEFAULT_ACCOUNT)
        acc["updated_at"] = None
        return acc
    out = {**DEFAULT_ACCOUNT, **(raw or {})}
    # keep cash if present; otherwise default principal
    if raw.get("cash") is None:
        out["cash"] = float(out["principal"])
    return out


def save_account(payload: dict[str, Any]) -> dict[str, Any]:
    DATA.mkdir(parents=True, exist_ok=True)
    base = load_account()
    out = {**DEFAULT_ACCOUNT, **base, **(payload or {})}
    out["principal"] = float(out.get("principal") or DEFAULT_ACCOUNT["principal"])
    out["total_shares"] = int(out.get("total_shares") or DEFAULT_ACCOUNT["total_shares"])
    out["shares_per_buy"] = float(out.get("shares_per_buy") or DEFAULT_ACCOUNT["shares_per_buy"])
    out["category_cap_pct"] = float(out.get("category_cap_pct") or DEFAULT_ACCOUNT["category_cap_pct"])
    if out.get("cash") is None:
        out["cash"] = out["principal"]
    else:
        out["cash"] = float(out["cash"])
    out["updated_at"] = _now()
    ACCOUNT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def reset_account_to_defaults() -> dict[str, Any]:
    return save_account(dict(DEFAULT_ACCOUNT))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def build_positions(
    trades: list[dict[str, Any]] | None = None,
    price_map: dict[str, float] | None = None,
    account: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rebuild positions from trade history (oldest→newest).

    持仓源（account.position_source）：
      ed_follow → 「价值投资」跟随E大的150份：直接镜像 data/ed_follow.json
        （由 scripts/sync_ed_follow.py 每早随发车同步自动对齐），与用户手工
        my_trades（那是用户自己独立的150万账本）互不干扰。
      默认/other → 回放 my_trades.json。
    """
    acc = account or load_account()
    unit = share_unit_value(acc)
    principal = float(acc.get("principal") or DEFAULT_ACCOUNT["principal"])
    cap_pct = float(acc.get("category_cap_pct") or 0.25)

    if str(acc.get("position_source") or "") == "ed_follow" and trades is None:
        follow = DATA / "ed_follow.json"
        try:
            fd = json.loads(follow.read_text(encoding="utf-8"))
        except Exception:
            fd = {}
        fl = list(fd.get("positions") or [])
        if fl:
            rows = []
            for p in fl:
                sh = float(p.get("shares") or 0)
                if sh <= 0:
                    continue
                rows.append({
                    "action": "buy", "name": p.get("name"), "code": p.get("code"),
                    "category": p.get("category") or "", "shares": sh,
                    "date": p.get("ed_date"),
                })
            # build_positions 按 oldest→newest 回放，这里单笔全买无需排序
            trades = rows
    trades_payload = load_my_trades() if trades is None else {"trades": trades}
    rows = list(trades_payload.get("trades") or [])
    # process chronological
    ordered = list(reversed(rows))

    # key by name preferentially
    pos: dict[str, dict[str, Any]] = {}
    cash = principal

    for t in ordered:
        action = str(t.get("action") or "buy").lower()
        name = str(t.get("name") or "").strip() or str(t.get("code") or "unknown")
        code = str(t.get("code") or "").split(".")[0].strip()
        cat = str(t.get("category") or "").strip()
        group = category_group(cat, name)
        shares = _as_float(t.get("shares"), 1.0)
        entry_cp = t.get("price")
        if entry_cp is None:
            entry_cp = t.get("cp")
        entry_cp = _as_float(entry_cp, 0.0) or None

        if name not in pos:
            pos[name] = {
                "name": name,
                "code": code,
                "category": cat,
                "group": group,
                "shares": 0.0,
                "cost_rmb": 0.0,
                "avg_entry_cp": None,
                "last_trade_date": None,
            }
        p = pos[name]
        if code and not p.get("code"):
            p["code"] = code
        if cat and not p.get("category"):
            p["category"] = cat
        p["group"] = group

        if action == "buy":
            invest = shares * unit
            # weighted avg entry cp
            old_sh = p["shares"]
            old_avg = p["avg_entry_cp"]
            new_sh = old_sh + shares
            if entry_cp and new_sh > 0:
                if old_sh > 0 and old_avg:
                    p["avg_entry_cp"] = (old_avg * old_sh + entry_cp * shares) / new_sh
                else:
                    p["avg_entry_cp"] = entry_cp
            p["shares"] = new_sh
            p["cost_rmb"] = p["shares"] * unit
            cash -= invest
            p["last_trade_date"] = t.get("date")
        elif action in {"sell", "reduce"}:
            sell_sh = min(shares, p["shares"])
            if sell_sh <= 0:
                continue
            # realize at current mark if available else entry
            cur = None
            if price_map:
                cur = price_map.get(name) or price_map.get(code)
            ratio = 1.0
            if cur and p.get("avg_entry_cp"):
                ratio = float(cur) / float(p["avg_entry_cp"])
            proceeds = sell_sh * unit * ratio
            cash += proceeds
            p["shares"] -= sell_sh
            p["cost_rmb"] = p["shares"] * unit
            if p["shares"] <= 1e-9:
                p["shares"] = 0.0
                p["cost_rmb"] = 0.0
                p["avg_entry_cp"] = None
            p["last_trade_date"] = t.get("date")

    price_map = price_map or {}
    positions: list[dict[str, Any]] = []
    total_cost = 0.0
    total_market = 0.0
    group_market: dict[str, float] = defaultdict(float)

    for name, p in pos.items():
        if p["shares"] <= 0:
            continue
        code = p.get("code") or ""
        cur = price_map.get(name) or price_map.get(code)
        avg = p.get("avg_entry_cp")
        if cur and avg:
            mkt = p["shares"] * unit * (float(cur) / float(avg))
            pnl = mkt - p["cost_rmb"]
            ret = pnl / p["cost_rmb"] if p["cost_rmb"] else 0.0
        else:
            mkt = p["cost_rmb"]
            pnl = 0.0
            ret = 0.0
        row = {
            **p,
            "current_cp": cur,
            "market_rmb": round(mkt, 2),
            "pnl_rmb": round(pnl, 2),
            "return_pct": round(ret, 4),
            "weight_pct": 0.0,  # filled later
            "unit_value": unit,
        }
        positions.append(row)
        total_cost += p["cost_rmb"]
        total_market += mkt
        group_market[p["group"]] += mkt

    equity = cash + total_market
    for row in positions:
        row["weight_pct"] = round(row["market_rmb"] / equity, 4) if equity else 0.0

    positions.sort(key=lambda x: (-x["market_rmb"], x["name"]))

    group_rows = []
    for g, mkt in sorted(group_market.items(), key=lambda x: -x[1]):
        cap_limit = principal * cap_pct
        group_rows.append(
            {
                "group": g,
                "market_rmb": round(mkt, 2),
                "weight_pct": round(mkt / principal, 4) if principal else 0.0,
                "cap_pct": cap_pct,
                "cap_rmb": round(cap_limit, 2),
                "headroom_rmb": round(max(0.0, cap_limit - mkt), 2),
                "at_cap": mkt >= cap_limit - 1e-6,
            }
        )

    total_pnl = total_market - total_cost
    # vs principal: equity - principal
    equity_pnl = equity - principal
    return {
        "account": {
            "principal": principal,
            "total_shares": int(acc.get("total_shares") or 150),
            "shares_per_buy": float(acc.get("shares_per_buy") or 1),
            "category_cap_pct": cap_pct,
            "unit_value": unit,
            "cash": round(cash, 2),
            "note": acc.get("note") or DEFAULT_ACCOUNT["note"],
            "updated_at": acc.get("updated_at"),
        },
        "positions": positions,
        "groups": group_rows,
        "summary": {
            "invested_cost_rmb": round(total_cost, 2),
            "market_value_rmb": round(total_market, 2),
            # E大"总仓=1−分位"只约束权益仓位：剔除债券现金组（他原话股债分开配置）
            "stock_market_value_rmb": round(sum(
                mkt for g, mkt in group_market.items() if g not in {"债券现金"}
            ), 2),
            "cash_rmb": round(cash, 2),
            "equity_rmb": round(equity, 2),
            "position_pnl_rmb": round(total_pnl, 2),
            "position_return_pct": round(total_pnl / total_cost, 4) if total_cost else 0.0,
            "equity_pnl_rmb": round(equity_pnl, 2),
            "equity_return_pct": round(equity_pnl / principal, 4) if principal else 0.0,
            "position_count": len(positions),
            "used_shares": round(sum(p["shares"] for p in positions), 2),
            "remaining_shares": round(max(0.0, float(acc.get("total_shares") or 150) - sum(p["shares"] for p in positions)), 2),
        },
        "generated_at": _now(),
    }


def _price_map_from_valuation_rows(rows: list[dict[str, Any]] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows or []:
        cp = r.get("cp")
        if cp is None:
            continue
        try:
            cp_f = float(cp)
        except Exception:
            continue
        if r.get("name"):
            out[str(r["name"])] = cp_f
        code = str(r.get("code") or "").split(".")[0]
        if code:
            out[code] = cp_f
    return out


def market_position_guide(
    window_years: float = MARKET_WINDOW_YEARS,
    allow_network: bool = False,
    account: dict[str, Any] | None = None,
    positions_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A股全指 valuation → overall equity target.

    Rule: avg_pct = mean(PE_percentile, PB_percentile) on window (default 10y).
    target_position_pct = 1 - avg_pct  (high valuation → lower equity exposure).
    Uses cache only by default (no 理杏仁 token on page open).
    """
    acc = account or load_account()
    principal = float(acc.get("principal") or DEFAULT_ACCOUNT["principal"])
    total_shares = float(acc.get("total_shares") or DEFAULT_ACCOUNT["total_shares"]) or 150.0
    unit = share_unit_value(acc)

    pe_pct = pb_pct = avg_pct = None
    pe = pb = None
    snapshot_date = available_years = effective_start = effective_end = None
    error = None
    try:
        from app.data_sources.lixinger import get_index_detail

        detail = get_index_detail(
            MARKET_INDEX_NAME,
            window_years=float(window_years),
            allow_network=allow_network,
        )
        latest = detail.get("latest") or {}
        pe = latest.get("pe")
        pb = latest.get("pb")
        pe_pct = latest.get("pe_percentile")
        pb_pct = latest.get("pb_percentile")
        snapshot_date = latest.get("date") or detail.get("effective_end") or detail.get("available_end")
        available_years = detail.get("available_years")
        effective_start = detail.get("effective_start")
        effective_end = detail.get("effective_end")
        vals = [float(v) for v in (pe_pct, pb_pct) if v is not None]
        if vals:
            avg_pct = sum(vals) / len(vals)
    except Exception as exc:
        error = str(exc)

    # 与 APP 顶部档位卡统一主锚：全A等权PE分位(15年)优先。
    # ed_quant.market_zone 读同一日更缓存，失败/无数据则回退上面的加权口径。
    try:
        import sys as _sys
        if "/vol1/1000/openzl/finance_app" not in _sys.path:
            _sys.path.insert(0, "/vol1/1000/openzl/finance_app")
        import ed_quant as _eq
        _mz = _eq.market_zone()
        if _mz and _mz.get("pe_pct") is not None:
            avg_pct = float(_mz["pe_pct"]) / 100.0
            pe_pct = avg_pct
            error = None
    except Exception:
        pass

    target_pct = None if avg_pct is None else max(0.0, min(1.0, 1.0 - float(avg_pct)))
    target_rmb = None if target_pct is None else principal * target_pct
    target_shares = None if target_pct is None else total_shares * target_pct

    # current equity exposure = market_value / principal (not total equity which includes cash)
    used_shares = None
    market_value = None
    current_pct = None
    if positions_summary:
        used_shares = positions_summary.get("used_shares")
        market_value = positions_summary.get("stock_market_value_rmb")
        if market_value is None:
            market_value = positions_summary.get("market_value_rmb")
        if market_value is not None and principal:
            current_pct = max(0.0, float(market_value) / principal)
        elif used_shares is not None and total_shares:
            current_pct = max(0.0, float(used_shares) / total_shares)

    headroom_pct = None
    headroom_rmb = None
    headroom_shares = None
    over_target = False
    if target_pct is not None and current_pct is not None:
        headroom_pct = target_pct - current_pct
        headroom_rmb = principal * headroom_pct
        headroom_shares = total_shares * headroom_pct
        over_target = headroom_pct < -1e-9

    # shares still allowed by market cap (floor, non-negative)
    max_new_shares_by_market = 0
    if headroom_shares is not None and not over_target:
        max_new_shares_by_market = max(0, int(headroom_shares // 1))  # whole shares

    return {
        "index": MARKET_INDEX_NAME,
        "code": "000985",
        "window_years": float(window_years),
        "rule": "目标总仓位 = 1 − A股全指(PE分位+PB分位)/2，默认10年窗口",
        "snapshot_date": snapshot_date,
        "available_years": available_years,
        "effective_start": effective_start,
        "effective_end": effective_end,
        "pe": pe,
        "pb": pb,
        "pe_percentile": None if pe_pct is None else round(float(pe_pct), 4),
        "pb_percentile": None if pb_pct is None else round(float(pb_pct), 4),
        "avg_percentile": None if avg_pct is None else round(float(avg_pct), 4),
        "target_position_pct": None if target_pct is None else round(float(target_pct), 4),
        "target_rmb": None if target_rmb is None else round(float(target_rmb), 2),
        "target_shares": None if target_shares is None else round(float(target_shares), 2),
        "current_position_pct": None if current_pct is None else round(float(current_pct), 4),
        "current_market_rmb": None if market_value is None else round(float(market_value), 2),
        "current_used_shares": used_shares,
        "headroom_pct": None if headroom_pct is None else round(float(headroom_pct), 4),
        "headroom_rmb": None if headroom_rmb is None else round(float(headroom_rmb), 2),
        "headroom_shares": None if headroom_shares is None else round(float(headroom_shares), 2),
        "max_new_shares_by_market": max_new_shares_by_market,
        "over_target": over_target,
        "unit_value": unit,
        "error": error,
        "cache_only": not allow_network,
    }


def ledger_snapshot(valuation_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    prices = _price_map_from_valuation_rows(valuation_rows)
    snap = build_positions(price_map=prices)
    market = market_position_guide(account=load_account(), positions_summary=snap.get("summary"))
    snap["market_position"] = market
    # fold key fields into summary for cards
    sm = snap["summary"]
    sm["target_position_pct"] = market.get("target_position_pct")
    sm["current_position_pct"] = market.get("current_position_pct")
    sm["market_headroom_shares"] = market.get("headroom_shares")
    sm["market_avg_percentile"] = market.get("avg_percentile")
    return snap


def action_sheet(
    signals: list[dict[str, Any]] | None = None,
    suppressed: list[dict[str, Any]] | None = None,
    valuation_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build today's executable checklist with share sizing + 25% group + A股全指总仓."""
    acc = load_account()
    unit = share_unit_value(acc)
    principal = float(acc["principal"])
    cap_pct = float(acc["category_cap_pct"])
    per_buy = float(acc.get("shares_per_buy") or 1)
    per_buy_base = per_buy  # 卖出/止盈份数基准，不受市场水位系数影响
    # 市场水位系数：generate_local_signals 按E大"均值以上保守一些"在信号行写入
    # market_factor(0.5/0.7/1.0/1.5)。⚠️份数允许小数（1份=1万，0.7份=7000元）——
    # 之前 round() 把 1×0.7、1×0.5 又抬回 1 份，减量只存在于文案里（2026-09-25 审计修复）。
    _mf = 1.0
    for _s in signals or []:
        try:
            _mf = float(_s.get("market_factor") or 1.0)
            break
        except Exception:
            continue
    if _mf not in (1.0,) and 0.4 <= _mf <= 1.6:
        per_buy = max(0.5, round(per_buy * _mf, 2))
    snap = build_positions(price_map=_price_map_from_valuation_rows(valuation_rows), account=acc)
    market = market_position_guide(account=acc, positions_summary=snap.get("summary"))
    group_mkt = {g["group"]: g["market_rmb"] for g in snap["groups"]}
    cash = snap["summary"]["cash_rmb"]
    remaining_shares = snap["summary"]["remaining_shares"]
    # market-level remaining capacity (shares), optimistic for multi-buy same day
    market_headroom_shares = float(market.get("headroom_shares") or 0.0)
    if market.get("over_target"):
        market_headroom_shares = 0.0
    market_ok = market.get("target_position_pct") is not None and market.get("error") is None
    # E大跟随账本：总仓闸不硬拦。镜像持仓天然"已满"（E大十年攒的95份权益 > 1−分位
    # 公式的34.8%），若硬拦则永远无法跟随他的下一笔买入——他本人也仍在月月买。
    # 保守职责已由 ①市场水位系数(×0.5~0.7) ②品种目标仓位闸(signal生成期⛔) 承担。
    follow_mode = str(acc.get("position_source") or "") == "ed_follow"
    if follow_mode:
        market_ok = False

    # E大口径市场拆分（用户：111份含债券/美股港股，A股仓位要单独算）：
    # 股票类(A+港+海外)≤80%、A+港≤75%、海外≤30% —— 三道硬上限逐条拦截。
    stock_split: dict[str, float] = {}
    stock_gate_note = ""
    try:
        from app import ed_markets as _em
        _fl = []
        _fp = DATA / "ed_follow.json"
        if _fp.exists():
            _fl = (json.loads(_fp.read_text(encoding="utf-8")).get("positions") or [])
        if _fl:
            stock_split = _em.market_totals(_fl)
            _allc, _ahc, _osc = 150*0.80, 150*0.75, 150*0.30
            _stocks = stock_split.get("A股", 0) + stock_split.get("港股", 0) + stock_split.get("海外股票", 0)
            _ah = stock_split.get("A股", 0) + stock_split.get("港股", 0)
            if _stocks >= _allc:
                stock_gate_note = f"股票类{_stocks:g}份≥E大80%上限({_allc:g}份)"
            elif _ah >= _ahc:
                stock_gate_note = f"A+港股{_ah:g}份≥E大75%上限({_ahc:g}份)"
            elif stock_split.get("海外股票", 0) >= _osc:
                stock_gate_note = f"海外{stock_split.get('海外股票'):g}份≥E大30%上限({_osc:g}份)"
    except Exception:
        pass

    # recompute filter if raw signals given without cooldown
    if signals is None and suppressed is None:
        # caller should pass; empty fallback
        signals, suppressed = [], []

    actions: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for s in signals or []:
        row = dict(s)
        action = str(row.get("action") or "").lower()
        name = str(row.get("name") or "")
        cat = str(row.get("category") or "")
        group = category_group(cat, name)
        used = float(group_mkt.get(group) or 0.0)
        cap_rmb = principal * cap_pct
        headroom = max(0.0, cap_rmb - used)
        max_shares_by_cap = int(headroom // unit) if unit else 0
        max_shares_by_cash = int(cash // unit) if unit else 0
        max_shares_by_pool = int(remaining_shares)
        max_shares_by_market = int(max(0.0, market_headroom_shares)) if market_ok else max_shares_by_pool

        if action == "buy":
            allowed = min(
                per_buy,
                float(max_shares_by_cap),
                float(max_shares_by_cash),
                float(max_shares_by_pool),
                per_buy if not market_ok else float(max_shares_by_market),
            )
            allowed = max(0.0, round(allowed, 2))  # 小数份=部分仓（0.7份≈7000元）
            if stock_gate_note:
                allowed = 0
            reasons = []
            if stock_gate_note:
                reasons.append(stock_gate_note)
            if max_shares_by_cap <= 0:
                reasons.append(f"同类[{group}]已达/超过{int(cap_pct*100)}%上限")
            if max_shares_by_cash <= 0:
                reasons.append("现金不足1份")
            if max_shares_by_pool <= 0:
                reasons.append("150份额度已用尽")
            if market_ok and max_shares_by_market <= 0:
                tgt = market.get("target_position_pct")
                cur = market.get("current_position_pct")
                reasons.append(
                    f"总仓已达/超过A股全指目标"
                    f"（目标{None if tgt is None else round(tgt*100,1)}%"
                    f"/当前{None if cur is None else round(cur*100,1)}%）"
                )
            if allowed <= 0:
                blocked.append(
                    {
                        **row,
                        "group": group,
                        "suggested_shares": 0,
                        "block_reason": "；".join(reasons) or "风控拦截",
                        "cap_headroom_rmb": round(headroom, 2),
                        "market_headroom_shares": round(market_headroom_shares, 2),
                    }
                )
                continue
            amount = allowed * unit
            actions.append(
                {
                    **row,
                    "group": group,
                    "suggested_shares": allowed,
                    "suggested_amount_rmb": round(amount, 2),
                    "unit_value": unit,
                    "cap_headroom_rmb": round(headroom, 2),
                    "market_headroom_shares": round(market_headroom_shares, 2),
                    "group_weight_pct": round(used / principal, 4) if principal else 0.0,
                    "checklist": (
                        f"买入 {name} {allowed} 份（约{int(amount)}元）"
                        f"｜{group}余{round(headroom,0):.0f}元"
                        f"｜总仓余{int(market_headroom_shares)}份"
                    ),
                    "status": "actionable",
                }
            )
            # optimistic consume for multi-signal same day ordering
            cash -= amount
            remaining_shares -= allowed
            market_headroom_shares = max(0.0, market_headroom_shares - allowed)
            group_mkt[group] = used + amount
        elif action in {"reduce", "sell"}:
            # allow reduce always if held
            held = next((p for p in snap["positions"] if p["name"] == name or p.get("code") == str(row.get("code") or "").split(".")[0]), None)
            # 分档减仓（回测校准）：≥88%极高估→每次2份；70-88%→1份"逐步卖出"
            # ⚠️卖出量以 per_buy_base(每次基准份数) 为尺度，不吃市场水位减量；
            # 之前 min(int(per_buy)...) 把 2 份又 clamp 回 1 份，分档失效（2026-09-25 审计修复）。
            _t = row.get("temperature")
            sh = per_buy_base * (2 if (_t is not None and float(_t) >= 88) else 1)
            if held:
                sh = min(sh, float(held["shares"]))
            actions.append(
                {
                    **row,
                    "group": group,
                    "suggested_shares": round(max(sh, 0), 2),
                    "suggested_amount_rmb": round(max(sh, 0) * unit, 2),
                    "unit_value": unit,
                    "checklist": f"{'卖出' if action=='sell' else '减仓'} {name} {round(max(sh,0),2):g} 份",
                    "status": "actionable" if sh > 0 else "no_position",
                }
            )
        else:
            actions.append(
                {
                    **row,
                    "group": group,
                    "suggested_shares": 0,
                    "checklist": f"观察 {name}",
                    "status": "watch",
                }
            )

    cool_list = []
    for s in suppressed or []:
        cool_list.append(
            {
                **s,
                "group": category_group(s.get("category"), s.get("name")),
                "checklist": f"观望 {s.get('name')}｜冷却中",
                "status": "cooldown",
            }
        )

    return {
        "account": snap["account"],
        "ledger_summary": snap["summary"],
        "market_position": market,
        "stock_split": stock_split,
        "stock_gate_note": stock_gate_note,
        "groups": snap["groups"],
        "actions": actions,
        "blocked": blocked,
        "cooldown": cool_list,
        "rules": {
            "principal": principal,
            "total_shares": int(acc.get("total_shares") or 150),
            "shares_per_buy": per_buy,
            "unit_value": unit,
            "category_cap_pct": cap_pct,
            "category_merge": "医药/医疗/全指医药/养老 → 医药医疗",
            "cooldown": "买入后30天或跌≥10%恢复",
            "market_cap": "总仓目标=1−A股全指10年(PE分位+PB分位)/2",
        },
        "generated_at": _now(),
    }


def format_daily_push(sheet: dict[str, Any], ledger: dict[str, Any] | None = None) -> str | None:
    """Return WeChat-ready text, or None if silent (no actionable buys/reduces)."""
    actions = [a for a in (sheet.get("actions") or []) if a.get("status") == "actionable" and a.get("action") in {"buy", "sell", "reduce"}]
    blocked = sheet.get("blocked") or []
    cool = sheet.get("cooldown") or []
    pending = sheet.get("pending_reminders") or []
    if not actions and not blocked and not pending:
        # truly nothing — silent
        return None

    sm = (ledger or {}).get("summary") or sheet.get("ledger_summary") or {}
    acc = sheet.get("account") or {}
    mp = sheet.get("market_position") or (ledger or {}).get("market_position") or {}
    lines = []
    lines.append(f"ETF动作单 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(
        f"权益 {sm.get('equity_rmb', '—')}｜盈亏 {sm.get('equity_pnl_rmb', '—')}（{round((sm.get('equity_return_pct') or 0)*100, 2)}%）"
        f"｜现金 {sm.get('cash_rmb', '—')}｜已用份 {sm.get('used_shares', '—')}/{acc.get('total_shares', 150)}"
    )
    sp = sheet.get("stock_split") or {}
    if sp:
        _st = sp.get("A股", 0) + sp.get("港股", 0) + sp.get("海外股票", 0)
        _tot = _st + sp.get("债券", 0) + sp.get("商品", 0)
        seg = (f"A股 {sp.get('A股', 0):g}份｜港股 {sp.get('港股', 0):g}｜海外 {sp.get('海外股票', 0):g}"
               f"｜债券 {sp.get('债券', 0):g}｜股票合计 {_st:g}/{_tot:g}份"
               f"（E大上限: 股票80%=120 · A+港75%=112.5 · 海外30%=45）")
        if sheet.get("stock_gate_note"):
            seg += f" ⛔{sheet['stock_gate_note']}"
        lines.append(seg)
    if mp.get("target_position_pct") is not None:
        tgt = round(float(mp["target_position_pct"]) * 100, 1)
        cur = mp.get("current_position_pct")
        cur_s = "—" if cur is None else f"{round(float(cur)*100, 1)}%"
        avg = mp.get("avg_percentile")
        avg_s = "—" if avg is None else f"{round(float(avg)*100, 1)}%"
        head = mp.get("headroom_shares")
        head_s = "—" if head is None else f"{round(float(head), 1)}份"
        lines.append(
            f"全A等权PE分位(主锚) {avg_s} → 目标总仓 {tgt}% / 当前 {cur_s} / 总仓余量 {head_s}"
            f"（数据 {mp.get('snapshot_date') or '—'}）"
        )
    lines.append("")
    if actions:
        lines.append("【今日可执行】")
        for a in actions:
            lines.append(f"- {a.get('checklist') or a.get('name')}")
            if a.get("reason"):
                lines.append(f"  原因：{a.get('reason')}")
    # 持续提醒：之前提示过但还没执行/忽略的（买了自动停，没买一直催）
    try:
        from app.pending_reminders import format_pending_lines
        plines = format_pending_lines(pending)
    except Exception:
        plines = []
    if plines:
        lines.append("")
        lines.append("【仍未处理·持续提醒】回复「买了<名称>」停止提醒 / 「不买<名称>」忽略30天")
        lines.extend(plines)
    if blocked:
        lines.append("【风控拦截】")
        for a in blocked[:8]:
            lines.append(f"- {a.get('name')}：{a.get('block_reason')}")
    if cool:
        lines.append("【冷却中】")
        for a in cool[:6]:
            reason = (a.get("cooldown") or {}).get("reason") or a.get("reason") or ""
            lines.append(f"- {a.get('name')}：{reason[:80]}")
    lines.append("")
    cap_show = int(float(acc.get("category_cap_pct") or 0.25) * 100)
    lines.append(
        f"纪律：每次{acc.get('shares_per_buy', 1)}份·同类≤{cap_show}%·"
        f"总仓=1−全A等权PE分位·冷却30天/跌10%"
    )
    return "\n".join(lines)
