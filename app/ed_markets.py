#!/usr/bin/env python3
"""E大体系：单基金 → 市场归属（A股/港股/海外股票/债券/商品）。

用户2026-09-24指出：111份里混着债券和美股港股，算A股仓位必须剔除。
E大原话依据：
  「股票分为境内股票与境外股票。境内就是A股，境外是QDII。
    股票类资产占比最大不要超过80%，最低不要低于20%-30%。」
  「将A股的最高仓位，从之前的80%降一些。比如降到75%，或者70%…
    A股港股相关性越来越高，80%+20%的最高配置，风险明显不够分散。」
  「所有海外市场的仓位加起来上限应该可以是30%」
  港股处理：「已经越来越难以把它归为"海外"市场了」→ 单列，不计入海外30%上限，
  但计入 股票类80% 大口径。
"""
from __future__ import annotations

from typing import Any

MARKETS = ("A股", "港股", "海外股票", "债券", "商品")

# 长赢计划里易被品类名误导的基金 —— 逐只人工判定（2026-09-24 全表核对）
EXPLICIT: dict[str, str] = {
    # "其他"类里的债券基金
    "易方达安心回报A": "债券",
    "景顺长城景泰纯利A": "债券",
    "兴全可转债": "债券",
    # 美元债/全球债 QDII → 债券
    "富国全球债券(QDII)-A-CNY": "债券",
    "富国全球债券人民币C": "债券",
    "中银美元债人民币A": "债券",
    "汇添富精选美元债A人民币": "债券",
    # "其他"里的 A股宽基
    "广发中证A500ETF联接-A": "A股",
    "500增强LOF": "A股",
    # 中概=港股上市为主，E大口径算"中国资产"，与A股相关性高 → 港股
    "易方达中证海外中国互联网50ETF联接(QDII)-A-CNY": "港股",
    "中概互联网LOF": "港股",
    # "医药"里的海外QDII
    "广发全球医疗保健A人民币": "海外股票",
    # 海外宽基
    "博时标普500ETF联接A": "海外股票",
    "博时标普500ETF联接": "海外股票",
    # 商品
    "华安易富黄金ETF联接A": "商品",
    "黄金": "商品",
    "南方原油LOF": "商品",
    "华宝油气LOF": "商品",
    "石油基金LOF": "商品",
    "易方达黄金ETF联接A": "商品",
}

# 品类默认归属（EXPLICIT 未命中时）
CAT_DEFAULT = {
    "上证50": "A股", "沪深300": "A股", "中证500": "A股", "创业板": "A股",
    "红利低波": "A股", "医药": "A股", "消费": "A股", "金融": "A股",
    "养老": "A股", "券商": "A股", "白酒": "A股", "银行": "A股",
    "港股": "港股", "港股科技": "港股", "中概互联": "港股",
    "海外": "海外股票", "债券现金": "债券", "商品": "商品",
}

_OVERSEAS_KW = ("标普", "纳斯达克", "道指", "德国", "法国", "日本", "DAX",
                "美国", "全球", "海外", "越南", "印度", "大摩健康")  # 全球/海外系QDII股票


def classify_market(name: str, category: str | None = None) -> str:
    """返回 MARKETS 之一。未知宽基默认 A股。"""
    n = (name or "").strip()
    if n in EXPLICIT:
        return EXPLICIT[n]
    for k, v in EXPLICIT.items():
        if k and (k in n or n in k):
            return v
    cat = (category or "").strip()
    if cat in CAT_DEFAULT:
        # 医药类里名字含海外关键词的已在 EXPLICIT；这里直接采用品类默认
        return CAT_DEFAULT[cat]
    if any(k in n for k in _OVERSEAS_KW):
        return "海外股票"
    if any(k in n for k in ("恒生", "港股", "H股", "香港")):
        return "港股"
    if "债" in n or "国开" in n:
        return "债券"
    if any(k in n for k in ("黄金", "原油", "油气")):
        return "商品"
    return "A股"


def market_totals(positions: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {m: 0.0 for m in MARKETS}
    for p in positions or []:
        m = classify_market(str(p.get("name") or ""), str(p.get("category") or ""))
        try:
            out[m] += float(p.get("shares") or 0)
        except (TypeError, ValueError):
            pass
    return out
