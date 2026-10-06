#!/usr/bin/env python3
"""把 qieman_etf 归档（E大且慢发言 raw/*.json + index.json）合并进 etf_tool/data/ed_talks.json。

- 只增不减：已有条目保留（防且慢删帖后本地丢数据）
- 新条目补齐 text_full（读 raw/{id}.json）
- 幂等：可每天重复跑
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from html import unescape
from pathlib import Path

QG = Path("/vol1/1000/openzl/qieman_etf")
RAW = QG / "raw"
IDX = QG / "index.json"
TALKS = Path("/vol1/1000/openzl/etf_tool/data/ed_talks.json")


def post_text(detail: dict) -> str:
    content = detail.get("content") or {}
    parts: list[str] = []
    title = (detail.get("title") or content.get("title") or "").strip()
    if title:
        parts.append(title)
    for block in content.get("contents") or []:
        if isinstance(block, dict):
            t = str(block.get("detail") or block.get("text") or "").strip()
            if t:
                parts.append(t)
    rich = detail.get("richContent")
    if isinstance(rich, str) and rich.strip() and not parts[1:]:
        # 专栏帖(v2归档)正文在 richContent,不在 content.contents
        rich = re.sub(r"<br\s*/?>", "\n", rich)
        rich = re.sub(r"</p>", "\n\n", rich)
        rich = re.sub(r"<[^>]+>", "", rich)
        parts.append(unescape(rich).strip())
    text = "\n\n".join(parts).strip()
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def created_at(detail: dict) -> str:
    for k in ("createdTime", "createTime", "createdAt", "time", "postTime", "publishedAt"):
        v = detail.get(k)
        if v:
            s = str(v)
            m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
            if m:
                return m.group(1)
            if s.isdigit() and len(s) >= 10:
                return datetime.fromtimestamp(int(s[:10])).strftime("%Y-%m-%d")
    return ""


def main() -> int:
    idx = json.loads(IDX.read_text("utf-8"))
    items = idx.get("items") or []
    arch = json.loads(TALKS.read_text("utf-8")) if TALKS.exists() else {"items": []}
    have = {str(x.get("id") or x.get("item_id") or "") for x in (arch.get("items") or [])}
    added = 0
    for it in items:
        pid = str(it.get("id") or "")
        if not pid or pid in have:
            continue
        text = ""
        rawf = RAW / f"{pid}.json"
        if rawf.exists():
            try:
                text = post_text(json.loads(rawf.read_text("utf-8")))
            except Exception:
                pass
        if not text:
            text = (it.get("title") or "").strip()
        date = it.get("date") or ""
        if not date and rawf.exists():
            try:
                date = created_at(json.loads(rawf.read_text("utf-8")))
            except Exception:
                pass
        arch.setdefault("items", []).append({
            "id": pid,
            "item_id": pid,
            "title": (it.get("title") or "")[:120],
            "date": date,
            "url": it.get("url") or f"https://qieman.com/community/postDetail/{pid}",
            "content_url": "",
            "text_full": text,
            "text_excerpt": text[:300],
            "author": "ETF拯救世界",
            "source": "qieman_post",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "summary": "",
        })
        have.add(pid)
        added += 1
    if added:
        arch["items"].sort(key=lambda x: x.get("date") or "", reverse=True)
        arch["total"] = len(arch["items"])
        arch["generated_at"] = datetime.now().isoformat(timespec="seconds")
        TALKS.write_text(json.dumps(arch, ensure_ascii=False), "utf-8")
    print(f"且慢发言合并: 新增 {added} 条 → 共 {len(arch.get('items') or [])} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
