"""Personal execution memory for the investment decision system.

Goal: if you bought on a buy signal today, do NOT re-fire the same buy tomorrow
unless enough time has passed OR the market dropped enough (time OR space).
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
MY_TRADES = DATA / "my_trades.json"

DEFAULT_SETTINGS = {
    # after a personal buy, suppress same-direction buy for N calendar days
    "buy_cooldown_days": 3,
    # re-open buy only if price fell this much from entry (0.03 = 3%)
    "buy_drop_resume_pct": 0.03,
    # after a personal sell/reduce, suppress same-direction reduce for N days
    "sell_cooldown_days": 2,
    "sell_rise_resume_pct": 0.03,
    # match by name / code / category
    "match_by_category": True,
}


def _today() -> str:
    return date.today().isoformat()


def load_my_trades() -> dict[str, Any]:
    if not MY_TRADES.exists():
        return {
            "updated_at": None,
            "settings": dict(DEFAULT_SETTINGS),
            "trades": [],
        }
    try:
        raw = json.loads(MY_TRADES.read_text(encoding="utf-8"))
    except Exception:
        return {
            "updated_at": None,
            "settings": dict(DEFAULT_SETTINGS),
            "trades": [],
        }
    settings = {**DEFAULT_SETTINGS, **(raw.get("settings") or {})}
    trades = list(raw.get("trades") or [])
    return {
        "updated_at": raw.get("updated_at"),
        "settings": settings,
        "trades": trades,
    }


def save_my_trades(payload: dict[str, Any]) -> dict[str, Any]:
    DATA.mkdir(parents=True, exist_ok=True)
    out = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "settings": {**DEFAULT_SETTINGS, **(payload.get("settings") or {})},
        "trades": list(payload.get("trades") or []),
    }
    MY_TRADES.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _norm_code(code: Any) -> str:
    return str(code or "").split(".")[0].strip()


def _parse_day(value: Any) -> date | None:
    text = str(value or "")[:10]
    if len(text) < 10:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except Exception:
        return None


def _match_trade(signal: dict[str, Any], trade: dict[str, Any], match_by_category: bool) -> bool:
    s_code = _norm_code(signal.get("code"))
    t_code = _norm_code(trade.get("code"))
    if s_code and t_code and s_code == t_code:
        return True
    s_name = str(signal.get("name") or "").strip()
    t_name = str(trade.get("name") or "").strip()
    if s_name and t_name and s_name == t_name:
        return True
    if match_by_category:
        s_cat = str(signal.get("category") or "").strip()
        t_cat = str(trade.get("category") or "").strip()
        if s_cat and t_cat and s_cat == t_cat:
            return True
    return False


def _price_of(signal: dict[str, Any], trade: dict[str, Any]) -> tuple[float | None, float | None]:
    entry = trade.get("price")
    current = signal.get("price")
    if current is None:
        current = signal.get("cp")
    try:
        entry_f = float(entry) if entry is not None else None
    except Exception:
        entry_f = None
    try:
        cur_f = float(current) if current is not None else None
    except Exception:
        cur_f = None
    return entry_f, cur_f


def cooldown_status_for_signal(
    signal: dict[str, Any],
    trades_payload: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any] | None:
    """Return cooldown info if this signal should be suppressed; else None."""
    payload = trades_payload or load_my_trades()
    settings = payload.get("settings") or DEFAULT_SETTINGS
    trades = payload.get("trades") or []
    action = str(signal.get("action") or "").lower()
    if action not in {"buy", "reduce", "sell"}:
        return None

    as_of_d = _parse_day(as_of or _today()) or date.today()
    match_cat = bool(settings.get("match_by_category", True))

    # only same-direction personal executions matter
    want_actions = {"buy"} if action == "buy" else {"sell", "reduce"}
    candidates = [
        t
        for t in trades
        if str(t.get("action") or "").lower() in want_actions and _match_trade(signal, t, match_cat)
    ]
    if not candidates:
        return None

    # latest personal execution
    candidates.sort(key=lambda t: str(t.get("date") or ""), reverse=True)
    trade = candidates[0]
    t_day = _parse_day(trade.get("date"))
    if not t_day:
        return None
    days_since = (as_of_d - t_day).days
    if days_since < 0:
        days_since = 0

    if action == "buy":
        cool_days = int(settings.get("buy_cooldown_days") or 3)
        drop_need = float(settings.get("buy_drop_resume_pct") or 0.03)
        entry, current = _price_of(signal, trade)
        drop_pct = None
        if entry and current and entry > 0:
            drop_pct = (entry - current) / entry
        # re-open if enough days OR enough drop (time OR space)
        time_ok = days_since >= cool_days
        space_ok = drop_pct is not None and drop_pct >= drop_need
        if time_ok or space_ok:
            return None
        remain = max(cool_days - days_since, 0)
        reason = (
            f"你已在 {trade.get('date')} 买入{trade.get('name') or trade.get('category') or ''}；"
            f"冷却还剩约 {remain} 天"
        )
        if drop_pct is not None:
            reason += f"（距买入点跌幅 {drop_pct*100:.1f}% < 再买入阈值 {drop_need*100:.0f}%）"
        else:
            reason += f"（或较买入点再跌 ≥{drop_need*100:.0f}% 可提前恢复）"
        return {
            "active": True,
            "kind": "buy_cooldown",
            "trade": trade,
            "days_since": days_since,
            "cooldown_days": cool_days,
            "days_remaining": remain,
            "drop_pct": drop_pct,
            "drop_resume_pct": drop_need,
            "reason": reason,
        }

    # sell / reduce
    cool_days = int(settings.get("sell_cooldown_days") or 2)
    rise_need = float(settings.get("sell_rise_resume_pct") or 0.03)
    entry, current = _price_of(signal, trade)
    rise_pct = None
    if entry and current and entry > 0:
        rise_pct = (current - entry) / entry
    time_ok = days_since >= cool_days
    space_ok = rise_pct is not None and rise_pct >= rise_need
    if time_ok or space_ok:
        return None
    remain = max(cool_days - days_since, 0)
    reason = (
        f"你已在 {trade.get('date')} 卖出/减仓；冷却还剩约 {remain} 天"
        f"（或较卖点再涨 ≥{rise_need*100:.0f}% 可提前恢复）"
    )
    return {
        "active": True,
        "kind": "sell_cooldown",
        "trade": trade,
        "days_since": days_since,
        "cooldown_days": cool_days,
        "days_remaining": remain,
        "rise_pct": rise_pct,
        "rise_resume_pct": rise_need,
        "reason": reason,
    }


def apply_execution_filter(
    signals: list[dict[str, Any]],
    trades_payload: dict[str, Any] | None = None,
    as_of: str | None = None,
    attach_suppressed: bool = True,
) -> dict[str, Any]:
    """Split signals into active vs suppressed by personal execution memory."""
    payload = trades_payload or load_my_trades()
    active: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for sig in signals:
        row = dict(sig)
        st = cooldown_status_for_signal(row, payload, as_of=as_of)
        if st:
            row["status"] = "cooldown"
            row["cooldown"] = st
            row["reason"] = f"{row.get('reason') or ''}｜{st['reason']}".strip("｜")
            suppressed.append(row)
        else:
            row.setdefault("status", "active")
            active.append(row)
    out = {
        "signals": active,
        "suppressed_signals": suppressed if attach_suppressed else [],
        "settings": payload.get("settings") or DEFAULT_SETTINGS,
        "my_trade_count": len(payload.get("trades") or []),
    }
    return out


def add_trade(body: dict[str, Any]) -> dict[str, Any]:
    payload = load_my_trades()
    action = str(body.get("action") or "buy").lower()
    if action not in {"buy", "sell", "reduce"}:
        action = "buy"
    day = str(body.get("date") or _today())[:10]
    trade = {
        "id": str(body.get("id") or uuid.uuid4().hex[:12]),
        "date": day,
        "action": action,
        "name": str(body.get("name") or "").strip(),
        "code": _norm_code(body.get("code")),
        "category": str(body.get("category") or "").strip(),
        "shares": body.get("shares") if body.get("shares") is not None else 1,
        "price": body.get("price"),
        "note": str(body.get("note") or "").strip(),
        "source_signal_date": body.get("source_signal_date"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if not trade["name"] and not trade["code"] and not trade["category"]:
        raise ValueError("name/code/category 至少一个")
    trades = list(payload.get("trades") or [])
    trades.insert(0, trade)
    # keep recent 500
    trades = trades[:500]
    saved = save_my_trades({"settings": payload.get("settings") or {}, "trades": trades})
    return {"ok": True, "trade": trade, "total": len(saved.get("trades") or [])}


def delete_trade(trade_id: str) -> dict[str, Any]:
    payload = load_my_trades()
    trades = [t for t in (payload.get("trades") or []) if str(t.get("id")) != str(trade_id)]
    saved = save_my_trades({"settings": payload.get("settings") or {}, "trades": trades})
    return {"ok": True, "total": len(saved.get("trades") or [])}


def update_settings(settings: dict[str, Any]) -> dict[str, Any]:
    payload = load_my_trades()
    merged = {**DEFAULT_SETTINGS, **(payload.get("settings") or {}), **(settings or {})}
    # clamp
    merged["buy_cooldown_days"] = max(0, int(merged.get("buy_cooldown_days") or 3))
    merged["sell_cooldown_days"] = max(0, int(merged.get("sell_cooldown_days") or 2))
    merged["buy_drop_resume_pct"] = max(0.0, float(merged.get("buy_drop_resume_pct") or 0.03))
    merged["sell_rise_resume_pct"] = max(0.0, float(merged.get("sell_rise_resume_pct") or 0.03))
    saved = save_my_trades({"settings": merged, "trades": payload.get("trades") or []})
    return saved
