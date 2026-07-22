#!/usr/bin/env python3
"""Generate local buy/hold/watch signals from lixinger valuations + long-win + E大.

Applies personal execution memory (my_trades): after you buy, suppress re-buy
until cooldown days pass OR price drops enough (time OR space).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.data_sources.lixinger import build_valuation_snapshot  # noqa: E402
from app.decision_memory import apply_execution_filter  # noqa: E402

DATA = ROOT / "data"
LONG_WIN = DATA / "long_win_positions.json"
SIGNALS = DATA / "signals.json"


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def temp_of_row(row: dict) -> float | None:
    t = row.get("temperature")
    if t is not None:
        return float(t)
    pe = row.get("pe_percentile")
    pb = row.get("pb_percentile")
    pb_only = bool(row.get("pb_only"))
    vals = [v for v in ((pb,) if pb_only else (pe, pb)) if v is not None]
    if not vals:
        return None
    return sum(float(v) for v in vals) / len(vals) * 100


def main() -> int:
    long_win = load_json(LONG_WIN, {"plans": {}})
    old = load_json(SIGNALS, {"signals": [], "ed_actions": []})
    ed_actions = old.get("ed_actions") or []
    cutoff = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    recent = [a for a in ed_actions if str(a.get("date", "")) >= cutoff]
    recent_buy_codes = {str(a.get("code", "")).split(".")[0] for a in recent if a.get("action") == "buy"}
    recent_buy_cats = {str(a.get("category") or "") for a in recent if a.get("action") == "buy"}

    # cache-only: do not burn 理杏仁 token in signal generation
    snap = build_valuation_snapshot(allow_network=False)
    rows = snap.get("rows") or []

    name_to_cat = {
        "沪深300": "沪深300", "中证500": "中证500", "上证50": "上证50",
        "创业板": "创业板", "中证红利": "红利低波", "红利低波": "红利低波",
        "红利低波100": "红利低波", "全指消费": "消费", "养老产业": "医药",
        "中证医药": "医药", "中证医疗": "医药", "恒生指数": "港股",
        "恒生科技": "港股科技", "中概互联": "中概互联", "中证银行": "金融地产",
        "证券公司": "券商", "中证环保": "新能源环保", "中证传媒": "传媒",
        "标普500": "海外", "纳斯达克100": "海外",
    }

    signals = []
    today = datetime.now().strftime("%Y-%m-%d")
    seen = set()
    for row in rows:
        name = row.get("name")
        action = row.get("action")
        temp = temp_of_row(row)
        if action not in {"buy", "watch", "hold", "reduce"}:
            continue
        if temp is None:
            continue
        cat = name_to_cat.get(name, name)
        if action == "buy":
            conf = 75 if temp <= 18 else 68 if temp <= 30 else 60
        elif action == "reduce":
            conf = 70
        elif action == "hold":
            conf = 55
        else:
            conf = 52
        if action == "buy" and (cat in recent_buy_cats or str(row.get("code") or "").split(".")[0] in recent_buy_codes):
            conf = min(90, conf + 8)
            reason_extra = "；近45天E大同类有买入"
        else:
            reason_extra = ""
        key = (action, name)
        if key in seen:
            continue
        seen.add(key)
        if action == "buy" or (action in {"hold", "watch"} and temp <= 40) or action == "reduce":
            signals.append({
                "date": today,
                "source": "local_model",
                "action": action,
                "name": name,
                "code": row.get("code") or "",
                "shares": 1 if action == "buy" else 0,
                "reason": (row.get("reason") or f"估值温度{temp}") + reason_extra,
                "confidence": conf,
                "category": cat,
                "temperature": temp,
                "cp": row.get("cp"),
            })

    prio = {"buy": 0, "reduce": 1, "watch": 2, "hold": 3}
    signals.sort(
        key=lambda s: (
            prio.get(s["action"], 9),
            s.get("temperature") if s.get("action") == "buy" else -(s.get("temperature") or 0),
        )
    )
    signals = signals[:12]

    filtered = apply_execution_filter(signals)
    active = filtered["signals"]
    suppressed = filtered.get("suppressed_signals") or []

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "signals": active,
        "suppressed_signals": suppressed,
        "ed_actions": ed_actions,
        "method": "lixinger_percentile + long_win_context + ed_45d_weight + personal_execution_memory",
        "decision_memory": {
            "settings": filtered.get("settings"),
            "my_trade_count": filtered.get("my_trade_count"),
            "rule": "买入后冷却N天；或较买入点再跌≥X%才恢复（时间或空间）",
        },
        "long_win_positions_at": long_win.get("generated_at"),
    }
    SIGNALS.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(active)} active, suppressed={len(suppressed)}, ed_actions={len(ed_actions)}")
    for s in active[:8]:
        print("ACTIVE", s["action"], s["name"], s.get("temperature"), s["confidence"])
    for s in suppressed[:6]:
        print("COOL", s["action"], s["name"], (s.get("cooldown") or {}).get("reason", "")[:80])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
