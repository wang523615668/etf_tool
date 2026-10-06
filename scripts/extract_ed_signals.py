#!/usr/bin/env python3
"""从 E大且慢小组发言提取结构化发车信号（买入/卖出/加仓/减仓 + 品种）→ etf_tool data/signals.json"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

RAW = Path("/vol1/1000/openzl/qieman_etf/raw")
OUT = Path("/vol1/1000/openzl/etf_tool/data/signals.json")

# 品种名映射（常见指数/基金简称 → 标准名）
KNOWN_PRODS = [
    "沪深300", "中证500", "中证A500", "A500", "中证1000", "中证800", "上证50", "创业板", "科创50",
    "中证红利", "红利低波", "红利低波100", "消费红利", "标普红利",
    "中证消费", "全指消费", "中证白酒", "中证医药", "全指医药", "中证医疗", "医疗C",
    "中证银行", "证券公司", "券商", "中证环保", "全指信息", "科技", "半导体", "芯片",
    "中概", "恒生", "恒生科技", "恒生国企", "H股", "纳指", "纳斯达克", "标普500",
    "美债", "短债", "债券", "黄金", "原油", "豆粕", "可转债",
    "养老产业", "全指金融", "传媒", "军工", "新能源", "光伏", "电池",
    "食品饮料", "家电", "地产", "基建", "有色", "钢铁", "煤炭",
]

ACTION_PAT = re.compile(r"(买入|卖出|加仓|减仓|申购|赎回|清仓|建仓|换仓|调仓|买到|补入|接回)")
ACTION_NORM = {"买到": "买入", "补入": "买入", "接回": "买入"}
PROD_PAT = re.compile("|".join(sorted(KNOWN_PRODS, key=len, reverse=True)))

# 发言噪音过滤：科普/假设/回顾性句子里的动作词不是发车信号
NOISE_PAT = re.compile(
    r"(回顾|当时|此前|之前|当年|已经|距今|历史上|简单来说|所谓|比如|例如|譬如"
    r"|如果|那么|假如|假设|可以买入|继续买入|各买|到期|收益率|分成几份"
    r"|先卖后买|其后|随后|后来|没有买到|没买到|差一点|可能|也许|或者)"
)

# 品种 → ETF/指数代码（用于关联 ETF 网站）
PROD_CODE = {
    "沪深300": "510300", "中证500": "510500", "中证A500": "159339", "A500": "159339", "中证1000": "512100", "中证800": "515800",
    "上证50": "510050", "创业板": "159915", "科创50": "588000",
    "中证红利": "515180", "红利低波": "512890", "红利低波100": "515100", "消费红利": "159990",
    "中证消费": "159928", "全指消费": "159928", "中证白酒": "512690", "中证医药": "159929",
    "全指医药": "159938", "中证医疗": "512170", "医疗C": "512170",
    "中证银行": "512800", "证券公司": "512880", "券商": "512880",
    "中证环保": "512580", "全指信息": "159939", "科技": "159807", "半导体": "512480", "芯片": "159995",
    "中概": "513050", "恒生": "159920", "恒生科技": "513180", "恒生国企": "159954", "H股": "159954",
    "纳指": "513100", "纳斯达克": "513100", "标普500": "513500",
    "美债": "511090", "短债": "511260", "债券": "511010", "黄金": "518880", "原油": "501018",
    "养老产业": "516820", "全指金融": "512070", "传媒": "512980", "军工": "512660",
    "新能源": "516160", "光伏": "515790", "电池": "512730",
    "食品饮料": "515170", "家电": "159996", "地产": "512200", "基建": "516950",
    "有色": "512400", "钢铁": "515210", "煤炭": "515220",
}

# 信号品种名 → 估值表标准名（让 cp 匹配）
NAME_NORM = {
    "创业板": "创业板指", "传媒": "中证传媒", "恒生": "恒生指数",
    "券商": "证券公司", "科技": "全指信息", "中概": "中概互联",
    "医疗C": "中证医疗", "中证消费": "全指消费",
}


def extract_signals(text: str, date: str) -> list[dict]:
    signals = []
    for sent in re.split(r"(?<=[。！？；\n])", text):
        s = sent.strip()
        if not s or len(s) > 150:
            continue
        m = ACTION_PAT.search(s)
        if not m:
            continue
        action = ACTION_NORM.get(m.group(1), m.group(1))
        if NOISE_PAT.search(s):
            continue  # 科普/假设/回顾句，不是发车
        prods = set(PROD_PAT.findall(s))
        # 同句多品种: 只取离触发词最近的一个(避免"补入A500因卖出500太多"误报500买入)
        if len(prods) > 1:
            pos = m.end()
            prods = {min(prods, key=lambda pr: min((mm.start() for mm in re.finditer(pr, s) if mm.start() >= pos), default=9999))}
        if not prods:
            # 尝试上下文补品种: 前面一行/同段
            continue
        for prod in prods:
            signals.append({
                "date": date,
                "action": action,
                "name": prod,          # 兼容 etf_tool 网站 schema（name 用于匹配估值表）
                "norm_name": NAME_NORM.get(prod, prod),  # 归一化名（匹配估值表）
                "product": prod,
                "code": PROD_CODE.get(prod),
                "sentence": s[:120],
                "source": "ed_talks",
            })
    return signals


# 长赢官方发车 JSON（actions 含 date/plan/action/name/code/shares/category/url）
LONG_WIN_JSON = Path("/vol1/1000/openzl/etf_tool/data/long_win_positions.json")


def merge_official_actions() -> list[dict]:
    """并入选赢官方发车（actions），解决"发言提取漏掉官方发车"问题（如2026-08-24卖红利）。
    官方发车不经过且慢小组发言，signals.json 只覆盖发言 → 漏操作。"""
    sigs = []
    try:
        d = json.loads(LONG_WIN_JSON.read_text(encoding="utf-8"))
    except Exception:
        return sigs
    # 动作词归一：buy→买入, sell→卖出
    act_map = {"buy": "买入", "sell": "卖出", "add": "加仓", "reduce": "减仓"}
    for plan_name, plan in (d.get("plans") or {}).items():
        for a in plan.get("actions") or []:
            act = act_map.get(str(a.get("action", "")).lower())
            if not act:
                continue
            nm = a.get("name", "")
            # 品种简称（取名称核心，用于匹配 PROD_CODE / norm_name）
            prod = _short_prod_name(nm)
            sigs.append({
                "date": str(a.get("date", ""))[:10],
                "action": act,
                "name": nm,  # 完整基金名
                "norm_name": NAME_NORM.get(prod, prod),
                "product": prod,
                "code": a.get("code", ""),
                "shares": a.get("shares", 1),
                "plan": plan_name,
                "category": a.get("category", ""),
                "url": a.get("url", ""),
                "sentence": f"{act} {a.get('shares',1)}份 {nm}（{plan_name}）",
                "source": "long_win_official",
            })
    return sigs


def _short_prod_name(full_name: str) -> str:
    """从基金全名提取品种简称（如'富国中证红利指数增强-A'→'中证红利'）用于映射。"""
    for cand in sorted(KNOWN_PRODS, key=len, reverse=True):
        if cand in full_name:
            return cand
    return full_name


def main() -> int:
    all_signals = []
    for f in sorted(RAW.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        content = d.get("content") or {}
        text = "\n".join(str(b.get("detail", "")) for b in (content.get("contents") or []) if isinstance(b, dict))
        if not text:
            continue
        date = str(d.get("createdAt") or d.get("publishTime") or "")[:10]
        sigs = extract_signals(text, date)
        all_signals.extend(sigs)

    # 并入长赢官方发车（解决发言漏掉官方发车，如2026-08-24卖红利）
    all_signals.extend(merge_official_actions())

    # 去重 + 排序
    seen = set()
    uniq = []
    for s in all_signals:
        key = (s["date"], s["action"], s["product"], s["sentence"], s.get("code", ""))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    uniq.sort(key=lambda x: x["date"])

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "qieman_ed_talks + long_win_official",
        "note": "从E大且慢小组发言 + 长赢官方发车提取的买卖信号，仅供参考",
        "total": len(uniq),
        "signals": uniq,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # 推送只展示最近 10 笔【官方发车】——真信号；发言提及不混排
    official = [s for s in uniq if s["source"] == "long_win_official"]
    recent = official[-10:]
    print(f"✅ 提取 {len(uniq)} 条信号 → {OUT}")
    print(f"🕐 最近 {len(recent)} 笔官方发车信号\n")
    for s in recent:
        # 动作emoji
        emoji = {"买入": "🟢", "卖出": "🔴", "加仓": "🟢", "减仓": "🟡"}.get(s["action"], "⚪")
        sent = f"{s['action']} {s.get('shares', 1)}份 {s['name']}"
        print(f"{emoji} **{s['date']}** {sent}（{s.get('plan', '')}）")
    print(f"\n（发言类提及另存 signals.json，共 {len(uniq)} 条，仅供参考）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
