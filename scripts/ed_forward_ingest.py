#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E大(ETF拯救世界)且慢小组发言入库解析器 — 用户手动转发入库。

且慢小组发言是投顾圈内容，公开渠道抓不到，唯一通道是用户转发。
此脚本把转发的文本：
  1) 解析关键信号(品种/操作/观点/红利动量等主题)
  2) 写入 data/ed_talks.json (source=qieman_group_forward, vip=True)
  3) 追加到 events 或单独归档, 供看板查询
  4) 若含"买/卖/加仓/减仓 X份" → 记录到 E大操作日志

用法:
  python3 ed_forward_ingest.py --text "且慢小组发言原文" [--note "备注"]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TALKS = DATA / "ed_talks.json"
ED_OPS = DATA / "ed_operations.json"
CST = timezone(timedelta(hours=8))

# E大体系关键词 → 主题
THEME_KEYWORDS = {
    "红利": ["红利", "股息", "高股息", "分红"],
    "动量": ["动量", "趋势", "右侧", "强者恒强"],
    "低估": ["低估", "便宜", "估值", "百分位"],
    "发车": ["发车", "买入", "加仓", "一份", "份"],
    "卖出": ["卖出", "减仓", "止盈", "兑现"],
    "现金": ["现金", "仓位", "防御"],
    "医疗": ["医疗", "医药"],
    "消费": ["消费"],
    "科技": ["科技", "AI", "科创", "半导体"],
}


def detect_themes(text: str) -> list[str]:
    return [t for t, kws in THEME_KEYWORDS.items() if any(k in text for k in kws)]


def extract_operations(text: str) -> list[dict]:
    """提取 '买入一份XX' / '卖出一份XX' / '加仓XX' 等操作"""
    ops = []
    for m in re.finditer(r"(买入|卖出|加仓|减仓)(?:一份|二份)?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,12})(?:（场外\d+）)?", text):
        action, name = m.group(1), m.group(2)
        ops.append({"action": action, "target": name})
    return ops


def load_talks():
    try:
        return json.loads(TALKS.read_text(encoding="utf-8"))
    except Exception:
        return {"items": [], "total": 0}


def ingest(text: str, note: str = "") -> dict:
    now = datetime.now(CST)
    report = {
        "themes": detect_themes(text),
        "operations": extract_operations(text),
    }

    talks = load_talks()
    items = talks.get("items", [])
    # 去重：同文本不再入库
    dedup = any(str(it.get("text", ""))[:60] == text[:60] for it in items)
    if not dedup:
        items.insert(0, {
            "date": now.strftime("%Y-%m-%d"),
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "qieman_group_forward",
            "vip": True,
            "text": text,
            "themes": report["themes"],
            "operations": report["operations"],
            "note": note or "E大且慢小组-用户转发",
        })
        talks["items"] = items
        talks["total"] = len(items)
        TALKS.write_text(json.dumps(talks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["archived"] = True
    else:
        report["archived"] = False

    # 操作日志（如果有买卖）
    if report["operations"]:
        try:
            ops = json.loads(ED_OPS.read_text(encoding="utf-8"))
        except Exception:
            ops = []
        ops.append({
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "operations": report["operations"],
            "text": text,
            "note": note or "E大且慢小组-用户转发",
        })
        ED_OPS.write_text(json.dumps(ops, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["ops_logged"] = True

    return report


if __name__ == "__main__":
    text, note = None, ""
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--text" and i + 1 < len(args):
            text = args[i + 1]; i += 2
        elif args[i] == "--note" and i + 1 < len(args):
            note = args[i + 1]; i += 2
        else:
            if text is None and not args[i].startswith("-"):
                text = args[i]
            i += 1

    if not text:
        print("用法: python3 ed_forward_ingest.py --text \"发言原文\" [--note \"备注\"]")
        sys.exit(1)

    r = ingest(text, note=note)
    print(json.dumps(r, ensure_ascii=False, indent=2))
