#!/usr/bin/env python3
"""把 150 份账本按 E大长赢150 当前真实持仓补齐（同步器，可重复跑）。

背景（用户2026-09决定）：
  「我昨天添加的买卖记录和价值投资这个用两套150万本金去管理，各是各的仓位。」
  → 用户手工 my_trades.json = 自己的 150万/150份 独立账本，本脚本绝不写入。
  → 「价值投资」跟随 E大 的那套 150万/150份 由本脚本维护：
    持仓 = E大最新持仓 − 跟随者手工操作(可选 ledger 文件)，之后每早08:06
    在发车同步(cron_vault_daily.sh)之后自动对齐。

同步台账: data/ed_follow.json
  - ed_snapshot: 上次同步时的 E大持仓 {code: shares}
  - manual_deltas: 从 E大仓位中扣减的用户手工操作 [{date,action,name,code,shares}]
      （想要自己手动操作 E大 账本时，往这里追加；不想要就留空，
        手动单只属于用户自己那套150万账本）
  - baseline: 首次补齐时的基准记录
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
LW = BASE / "data" / "long_win_positions.json"
ED_FOLLOW = BASE / "data" / "ed_follow.json"
USER_TRADES = BASE / "data" / "my_trades.json"

# E大持仓里不占"指数仓位"的现金管理工具（货基/纯债/美元债），保持与E大口径一致：
# 债券现金也是150份计划的一部分，E大怎么配我们怎么跟。
SKIP_NEG = 0.0


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def ed_positions() -> dict[str, dict]:
    d = load_json(LW, {})
    plans = (d.get("plans") or {}).get("long_win_150") or {}
    out = {}
    for x in plans.get("positions") or []:
        code = str(x.get("code") or x.get("name"))
        out[code] = {
            "code": code,
            "name": x.get("name") or code,
            "shares": max(0.0, float(x.get("shares") or 0)),
            "category": x.get("category") or "",
            "ed_date": x.get("latest") or "",
            "plan_generated_at": d.get("generated_at") or "",
        }
    return out


def build_ledger(prev: dict) -> tuple[dict, list]:
    """返回 (新 ed_follow, 本次动作日志)。"""
    ed = ed_positions()
    if not ed:
        raise SystemExit("long_win_positions.json 无持仓数据，中止（不覆盖台账）")

    manual = prev.get("manual_deltas") or []
    # 手工扣减：action=buy → 跟随账本多买(+), reduce/sell → 少持(-)
    adj: dict[str, float] = {}
    adjname: dict[str, str] = {}
    for m in manual:
        key = str(m.get("code") or m.get("name"))
        sign = 1.0 if m.get("action") == "buy" else -1.0
        adj[key] = adj.get(key, 0.0) + sign * float(m.get("shares") or 0)
        adjname.setdefault(key, str(m.get("name") or key))

    positions = {}
    for code, p in ed.items():
        sh = p["shares"] + adj.get(code, 0.0)
        if adj.get(code):
            adj.pop(code)  # 已消化
        if sh > SKIP_NEG:
            positions[code] = {**p, "shares": sh}
    # 手工买过但 E大已清仓的品种 → 保留
    for code, delta in adj.items():
        if delta > SKIP_NEG:
            positions[code] = {
                "code": code, "name": adjname.get(code, code), "shares": delta,
                "category": "", "ed_date": "", "plan_generated_at": "",
                "note": "手工保留(E大已清)",
            }

    total = sum(p["shares"] for p in positions.values())

    baseline = prev.get("baseline") or {
        "at": datetime.now().isoformat(timespec="seconds"),
        "ed_total": total,
        "note": "首次按E大当前持仓补齐150份",
    }
    new = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "long_win_positions.json (cron_vault_daily 06:30同步)",
        "positions": sorted(positions.values(), key=lambda x: -x["shares"]),
        "total_shares": total,
        "manual_deltas": manual,
        "baseline": baseline,
    }

    old_map = {p["code"]: p["shares"] for p in (prev.get("positions") or [])}
    log = []
    for code, p in positions.items():
        d = p["shares"] - old_map.get(code, 0.0)
        if abs(d) > 1e-9:
            log.append(f"{'+' if d > 0 else ''}{d:g} {p['name']}")
    return new, log


def sanity_check(new: dict) -> None:
    """用户手工账本必须未被触碰（两套本金红线）。"""
    t = load_json(USER_TRADES, {})
    n = len(t.get("trades") or [])
    print(f"[红线] 用户手工 my_trades.json 共 {n} 笔，本脚本只读不写 ✓")


def main() -> int:
    prev = load_json(ED_FOLLOW, {})
    new, log = build_ledger(prev)
    if "--dry-run" in sys.argv:
        print("[dry-run] 将变更:", "; ".join(log) if log else "无")
        print("[dry-run] E大总份数:", new["total_shares"])
        return 0
    ED_FOLLOW.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sanity_check(new)
    print(f"wrote {ED_FOLLOW.name}: {len(new['positions'])}个品种, 合计{new['total_shares']:g}份")
    print("本次对齐:", "; ".join(log) if log else "无变化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
