from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ARCHIVE = DATA / "ed_talks.json"
SIGNALS = DATA / "signals.json"


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_html(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def summarize_reason(text: str) -> dict[str, Any]:
    needles = ["买入", "卖出", "估值", "仓位", "低估", "高估", "风险", "计划", "长赢", "份"]
    sentences = re.split(r"(?<=[。！？；])", text)
    picked = [s.strip() for s in sentences if any(n in s for n in needles)][:5]
    reason = "".join(picked)[:420] if picked else text[:260]
    return {
        "operation_reason": reason or "暂未从正文中抽取到明确原因",
        "reason_evidence": picked[:3],
        "valuation_context": "低估/高估/估值" if "估值" in reason or "低估" in reason or "高估" in reason else "未明确",
        "position_context": "仓位/份数/计划" if "仓位" in reason or "份" in reason or "计划" in reason else "未明确",
        "risk_note": next((s.strip() for s in sentences if "风险" in s), ""),
    }


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
