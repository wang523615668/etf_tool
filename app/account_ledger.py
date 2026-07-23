"""Personal capital ledger for practical decision system.

Rules (user-defined):
- principal 1,500,000 RMB split into 150 shares
- each buy = 1 share by default
- single category group cap = 25% of principal
- medical / 医药 / 医疗 merge into one group
- show P&L and return rate
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

DEFAULT_ACCOUNT = {
    "principal": 1_500_000.0,
    "total_shares": 150,
    "shares_per_buy": 1.0,
    "category_cap_pct": 0.25,
    "cash": 1_500_000.0,
    "note": "本金150万 / 150份 / 每次1份 / 同类≤25%",
}

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
    """Rebuild positions from trade history (oldest→newest)."""
    acc = account or load_account()
    unit = share_unit_value(acc)
    principal = float(acc.get("principal") or DEFAULT_ACCOUNT["principal"])
    cap_pct = float(acc.get("category_cap_pct") or 0.25)
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


def ledger_snapshot(valuation_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    prices = _price_map_from_valuation_rows(valuation_rows)
    return build_positions(price_map=prices)


def action_sheet(
    signals: list[dict[str, Any]] | None = None,
    suppressed: list[dict[str, Any]] | None = None,
    valuation_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build today's executable checklist with share sizing + 25% group cap."""
    acc = load_account()
    unit = share_unit_value(acc)
    principal = float(acc["principal"])
    cap_pct = float(acc["category_cap_pct"])
    per_buy = float(acc.get("shares_per_buy") or 1)
    snap = build_positions(price_map=_price_map_from_valuation_rows(valuation_rows), account=acc)
    group_mkt = {g["group"]: g["market_rmb"] for g in snap["groups"]}
    cash = snap["summary"]["cash_rmb"]
    remaining_shares = snap["summary"]["remaining_shares"]

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

        if action == "buy":
            allowed = min(int(per_buy), max_shares_by_cap, max_shares_by_cash, max_shares_by_pool)
            reasons = []
            if max_shares_by_cap <= 0:
                reasons.append(f"同类[{group}]已达/超过{int(cap_pct*100)}%上限")
            if max_shares_by_cash <= 0:
                reasons.append("现金不足1份")
            if max_shares_by_pool <= 0:
                reasons.append("150份额度已用尽")
            if allowed <= 0:
                blocked.append(
                    {
                        **row,
                        "group": group,
                        "suggested_shares": 0,
                        "block_reason": "；".join(reasons) or "风控拦截",
                        "cap_headroom_rmb": round(headroom, 2),
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
                    "group_weight_pct": round(used / principal, 4) if principal else 0.0,
                    "checklist": f"买入 {name} {allowed} 份（约{int(amount)}元）｜{group}敞口头寸{round(headroom,0):.0f}元",
                    "status": "actionable",
                }
            )
            # optimistic consume for multi-signal same day ordering
            cash -= amount
            remaining_shares -= allowed
            group_mkt[group] = used + amount
        elif action in {"reduce", "sell"}:
            # allow reduce always if held
            held = next((p for p in snap["positions"] if p["name"] == name or p.get("code") == str(row.get("code") or "").split(".")[0]), None)
            sh = int(per_buy)
            if held:
                sh = min(int(per_buy), int(held["shares"]) if held["shares"] >= 1 else 0) or int(min(per_buy, held["shares"]))
            actions.append(
                {
                    **row,
                    "group": group,
                    "suggested_shares": max(sh, 0),
                    "suggested_amount_rmb": round(max(sh, 0) * unit, 2),
                    "unit_value": unit,
                    "checklist": f"{'卖出' if action=='sell' else '减仓'} {name} {max(sh,0)} 份",
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
        },
        "generated_at": _now(),
    }


def format_daily_push(sheet: dict[str, Any], ledger: dict[str, Any] | None = None) -> str | None:
    """Return WeChat-ready text, or None if silent (no actionable buys/reduces)."""
    actions = [a for a in (sheet.get("actions") or []) if a.get("status") == "actionable" and a.get("action") in {"buy", "sell", "reduce"}]
    blocked = sheet.get("blocked") or []
    cool = sheet.get("cooldown") or []
    if not actions and not blocked:
        # truly nothing — silent
        return None

    sm = (ledger or {}).get("summary") or sheet.get("ledger_summary") or {}
    acc = sheet.get("account") or {}
    lines = []
    lines.append(f"ETF动作单 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(
        f"权益 {sm.get('equity_rmb', '—')}｜盈亏 {sm.get('equity_pnl_rmb', '—')}（{round((sm.get('equity_return_pct') or 0)*100, 2)}%）"
        f"｜现金 {sm.get('cash_rmb', '—')}｜已用份 {sm.get('used_shares', '—')}/{acc.get('total_shares', 150)}"
    )
    lines.append("")
    if actions:
        lines.append("【今日可执行】")
        for a in actions:
            lines.append(f"- {a.get('checklist') or a.get('name')}")
            if a.get("reason"):
                lines.append(f"  原因：{a.get('reason')}")
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
    lines.append(
        f"纪律：每次{acc.get('shares_per_buy', 1)}份·同类≤{int(float(acc.get('category_cap_pct') or 0.25)*100)}%·冷却30天/跌10%"
    )
    return "\n".join(lines)
