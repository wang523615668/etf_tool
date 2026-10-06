"""自主决策引擎：估值温度 + E大历史操作模式 → 品种级买卖建议。

E大不发车时，系统根据：
1. 当前估值温度（PE/PB 百分位）
2. E大在该品种上的历史买卖温度区间（从 signals + 估值历史学习）
3. 我的持仓/冷却期（decision_memory）
给出自主决策建议。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"

# 信号品种名 → 估值表标准名
NAME_NORM = {
    "创业板": "创业板指", "传媒": "中证传媒", "恒生": "恒生指数",
    "券商": "证券公司", "科技": "全指信息", "中概": "中概互联",
    "医疗C": "中证医疗", "中证消费": "全指消费",
}


def load_json(name: str, default: Any = None) -> Any:
    p = DATA / name
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def build_decision_table(valuation_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """构建自主决策表：每个品种 = 当前温度 + E大模式 + 建议"""
    signals = load_json("signals.json", {}).get("signals", [])
    my_trades = load_json("my_trades.json", {}).get("trades", [])

    # 1. 按估值表品种分组 E大信号（用 norm_name 匹配）
    ed_by_name: dict[str, list[dict]] = {}
    for s in signals:
        norm = s.get("norm_name") or NAME_NORM.get(s.get("name"), s.get("name"))
        if norm:
            ed_by_name.setdefault(norm, []).append(s)

    rows = []
    for row in valuation_rows:
        name = row.get("name", "")
        temp = row.get("temperature")
        action = row.get("action")
        reason = row.get("reason", "")

        # 2. E大历史操作模式
        ed_sigs = ed_by_name.get(name, [])
        ed_buys = [s for s in ed_sigs if s.get("action") in ("买入", "加仓", "建仓", "申购")]
        ed_sells = [s for s in ed_sigs if s.get("action") in ("卖出", "清仓", "减仓", "赎回")]
        last_ed = max(ed_sigs, key=lambda s: s.get("date", "")) if ed_sigs else None

        # 3. 我的持仓
        my_pos = [t for t in my_trades if str(t.get("name", "")).lower() == name.lower()]
        my_bought = bool(my_pos)

        # 4. 自主建议（估值层 + E大模式层 + 执行层）
        ed_hint = ""
        if ed_buys and not ed_sells:
            ed_hint = f"E大近期仅买入（{len(ed_buys)}次），未卖出"
        elif ed_sells and not ed_buys:
            ed_hint = f"E大近期仅卖出（{len(ed_sells)}次），未买入"
        elif ed_buys and ed_sells:
            ed_hint = f"E大买卖均有（买{len(ed_buys)}/卖{len(ed_sells)}）"
        if last_ed:
            ed_hint += f"，最近操作 {last_ed.get('date')} {last_ed.get('action')}"

        decision, confidence, basis = _decide(temp, action, ed_buys, ed_sells, my_bought)

        rows.append({
            **row,
            "ed_signals": len(ed_sigs),
            "ed_buys": len(ed_buys),
            "ed_sells": len(ed_sells),
            "ed_hint": ed_hint or "E大无信号",
            "my_bought": my_bought,
            "decision": decision,
            "confidence": confidence,
            "decision_basis": basis,
        })

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "自主决策引擎：估值温度 + E大历史模式 + 我的持仓。E大不发车时的决策参考。",
        "total": len(rows),
        "rows": rows,
    }


def _decide(temp, action, ed_buys, ed_sells, my_bought) -> tuple[str, str, str]:
    """决策规则：
    - 低估 + E大买过 → 强买入
    - 低估 + 无E大信号 → 可买入（E大无信号时自主决策）
    - 高估 + E大卖过 → 卖出/减仓
    - 高估 + 无信号 → 减仓/观望
    - 中性 → 观望
    """
    if temp is None:
        return "观望", "低", "无温度数据"
    if action == "pause":
        return "暂停", "低", "数据过期"

    if temp <= 25:
        if my_bought:
            return "持有", "中", f"已持有+低估({temp}°)，可考虑加仓，勿清仓"
        if ed_buys and not ed_sells:
            return "买入", "高", f"低估({temp}°)+E大历史买入{len(ed_buys)}次"
        if ed_sells and not ed_buys:
            return "观望", "中", f"低估({temp}°)但E大近期仅卖出，尊重趋势"
        if ed_buys and ed_sells:
            return "可买入", "中", f"低估({temp}°)+E大买卖均有(买{len(ed_buys)}/卖{len(ed_sells)})"
        return "可买入", "中", f"低估({temp}°)，E大无信号，自主决策"
    if temp >= 85:
        if my_bought:
            return "减仓", "高", f"已持有+高估({temp}°)，落袋为安"
        if ed_sells and not ed_buys:
            return "卖出", "高", f"高估({temp}°)+E大历史卖出{len(ed_sells)}次"
        return "减仓", "中", f"高估({temp}°)，E大无信号，控制风险"
    if temp >= 65:
        return "观望", "中", f"偏高({temp}°)，不追高"
    return "观望", "低", f"中性({temp}°)，等待更好赔率"
