"""提醒追踪：信号发出后——买了就停推，没买就一直提醒（带连续天数）。

数据文件: data/pending_reminders.json
{
  "reminders": [
    {"key": "buy:创业板指", "name": "创业板指", "action": "buy",
     "first_date": "2026-08-21", "last_date": "2026-08-23",
     "count": 3, "price_first": 2100.5, "note": "..."}
  ]
}

生命周期：
- 每日推送时：把当日 actionable 信号 upsert 进 pending；已存在的 count+1 并显示「第N次提醒」
- 用户回复"买了XX"→ 记入 my_trades.json → 冷却过滤自动压制 + pending 移除
- 用户回复"不买了/忽略XX" → pending 标记 dismissed（30天后同信号重新出现）
- 冷却期结束且信号仍 actionable → 自动重新加入 pending
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
PENDING = DATA / "pending_reminders.json"

DISMISS_COOLDOWN_DAYS = 30   # 忽略后多少天内不再提


def _today() -> str:
    return date.today().isoformat()


def _load() -> dict[str, Any]:
    if not PENDING.exists():
        return {"updated_at": None, "reminders": []}
    try:
        raw = json.loads(PENDING.read_text(encoding="utf-8"))
    except Exception:
        return {"updated_at": None, "reminders": []}
    return {"updated_at": raw.get("updated_at"), "reminders": list(raw.get("reminders") or [])}


def _save(payload: dict[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    PENDING.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def reminder_key(sig: dict[str, Any]) -> str:
    action = _norm(sig.get("action"))
    if action in ("卖出", "sell", "reduce", "减仓"):
        action = "sell"
    else:
        action = "buy"
    name = sig.get("norm_name") or sig.get("name") or sig.get("code")
    return f"{action}:{_norm(name)}"


def upsert_from_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把今日 actionable 信号并入 pending。返回当前全部未决提醒（含新更新）。"""
    payload = _load()
    rems = {r["key"]: r for r in payload["reminders"]}
    today = _today()
    for sig in signals:
        key = reminder_key(sig)
        if not key or key == "buy:":
            continue
        r = rems.get(key)
        price = sig.get("cp") if sig.get("cp") is not None else sig.get("price")
        if r is None:
            rems[key] = {
                "key": key,
                "action": "sell" if key.startswith("sell") else "buy",
                "name": sig.get("name") or sig.get("code"),
                "first_date": today,
                "last_date": today,
                "count": 1,
                "price_first": price,
                "note": (sig.get("reason") or "")[:120],
            }
        else:
            r["last_date"] = today
            r["count"] = int(r.get("count") or 0) + 1
            if r.get("price_first") is None and price is not None:
                r["price_first"] = price
    out = sorted(rems.values(), key=lambda x: (-int(x.get("count") or 0), x["key"]))
    _save({"reminders": out})
    return out


def mark_bought(name_or_code: str) -> dict[str, Any] | None:
    """用户买入后移除对应提醒。"""
    payload = _load()
    target = _norm(name_or_code)
    kept, removed = [], None
    for r in payload["reminders"]:
        hay = _norm(r.get("name")) + "|" + _norm(r.get("key"))
        if target and target in hay:
            removed = r
        else:
            kept.append(r)
    _save({"reminders": kept})
    return removed


def dismiss(name_or_code: str) -> dict[str, Any] | None:
    """用户明确忽略：标记 dismiss 时间，冷却期内不再重提。"""
    removed = mark_bought(name_or_code)
    if removed is None:
        return None
    df = DATA / "dismissed_reminders.json"
    try:
        d = json.loads(df.read_text(encoding="utf-8")) if df.exists() else {"items": []}
    except Exception:
        d = {"items": []}
    items = [x for x in (d.get("items") or [])
             if not (_parse_day(x.get("date")) and
                     (_parse_day(x.get("date")) - date.today()).days > DISMISS_COOLDOWN_DAYS)]
    items.append({"key": removed["key"], "date": _today()})
    df.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    return removed


def _parse_day(v: Any):
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def filter_dismissed(reminders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤掉处于忽略冷却期的提醒。"""
    df = DATA / "dismissed_reminders.json"
    if not df.exists():
        return reminders
    try:
        d = json.loads(df.read_text(encoding="utf-8"))
    except Exception:
        return reminders
    today = date.today()
    active_deny: dict[str, int] = {}
    for x in d.get("items") or []:
        day = _parse_day(x.get("date"))
        if not day:
            continue
        if (today - day).days <= DISMISS_COOLDOWN_DAYS:
            active_deny[x["key"]] = (today - day).days
    return [r for r in reminders if r["key"] not in active_deny]


def format_pending_lines(reminders: list[dict[str, Any]]) -> list[str]:
    """生成推送文案里的「持续提醒」段落。"""
    lines = []
    today = date.today()
    for r in reminders:
        first = _parse_day(r.get("first_date")) or today
        days = max((today - first).days, 0)
        n = int(r.get("count") or 1)
        act = "买入" if r["action"] == "buy" else "卖出/减仓"
        pf = r.get("price_first")
        pf_s = f" 首次提示价{pf}" if pf else ""
        suffix = "（首次）" if n <= 1 else f"（已连续提醒{n}天{days}天前首次）"
        lines.append(f"- {act} {r['name']}{pf_s} {suffix}")
    return lines


def reconcile_with_trades() -> int:
    """对账：my_trades 里近2天的成交如果对应某个 pending，自动移除（防漏报）。"""
    try:
        from app.decision_memory import load_my_trades
    except Exception:
        return 0
    trades = (load_my_trades().get("trades") or [])[:10]
    removed = 0
    for t in trades:
        day = _parse_day(t.get("date"))
        if not day:
            continue
        if (date.today() - day).days > 2:
            continue
        action = _norm(t.get("action"))
        key = ("sell" if action in ("sell", "reduce") else "buy") + ":" + _norm(
            t.get("category") or t.get("name") or t.get("code"))
        payload = _load()
        before = len(payload["reminders"])
        payload["reminders"] = [r for r in payload["reminders"] if r["key"] != key]
        if len(payload["reminders"]) != before:
            _save(payload)
            removed += 1
    return removed
