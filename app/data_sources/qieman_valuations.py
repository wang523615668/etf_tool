"""且慢每日估值数据源（qieman.com/idx-eval 抓取缓存读取）。

抓取脚本: scripts/fetch_qieman_valuations.py（Playwright + 系统 chromium，每日 cron 更新）
缓存文件: cache/qieman_valuations.json
本模块只读缓存，不联网（网页路径零成本）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_FILE = _REPO_ROOT / "cache" / "qieman_valuations.json"

# 且慢口径说明（来自页面 footer 原文）
NOTE = (
    "且慢每日估值：采用近10年数据计算，若不满10年则采用全部历史数据；"
    "强周期行业（券商/银行等）用PB；标普500/纳斯达克100用2011年起数据；"
    "A股当日数据，非A股滞后1天。加*为且慢按编制规则独立计算PE-TTM，加H为恒生聚源数据。"
)

# 已知使用 PB 的强周期指数（且慢口径，用于前端展示区分）
PB_INDICES = {"证券公司", "中证银行", "中证环保", "300价值"}


def load_qieman_valuations() -> dict[str, Any] | None:
    """读取且慢估值缓存；不存在/损坏返回 None。"""
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("indices"):
            return payload
    except Exception:
        return None
    return None


def build_valuation_snapshot() -> dict[str, Any]:
    """构建且慢估值快照（与 lixinger 快照同构，便于前端复用）。"""
    payload = load_qieman_valuations()
    if not payload:
        return {
            "freshness": {
                "warning": "且慢估值缓存缺失，请运行 scripts/fetch_qieman_valuations.py",
                "source": "qieman_cache_missing",
            },
            "total": 0,
            "counts": {},
            "top": {"buy": [], "watch": [], "hold": [], "reduce": [], "pause": []},
            "rows": [],
            "data_source": "qieman_cache_missing",
        }

    indices = payload.get("indices") or []
    rows = []
    for idx in indices:
        name = idx["name"]
        val = idx.get("value")
        pct = idx.get("percentile")
        pb_only = name in PB_INDICES
        # 温度 = 百分位（且慢口径直接给出）
        temperature = int(round(pct)) if pct is not None else None
        # 简易行动建议（与站内 _valuation_action 口径一致）
        if pct is None:
            action, reason = "hold", "数据缺失"
        elif pct < 20:
            action, reason = "buy", f"且慢百分位{pct:.0f}%，历史低位"
        elif pct < 40:
            action, reason = "watch", f"且慢百分位{pct:.0f}%，偏低区域"
        elif pct < 60:
            action, reason = "hold", f"且慢百分位{pct:.0f}%，中枢区域"
        elif pct < 80:
            action, reason = "reduce", f"且慢百分位{pct:.0f}%，偏高区域"
        else:
            action, reason = "pause", f"且慢百分位{pct:.0f}%，历史高位"

        rows.append({
            "name": name,
            "code": idx.get("code"),
            "pe": val if not pb_only else None,
            "pb": val if pb_only else None,
            "pe_percentile": None if pb_only else pct / 100 if pct is not None else None,
            "pb_percentile": pct / 100 if pct is not None else None,
            "value": val,
            "percentile": pct,
            "high_10y": idx.get("high"),
            "low_10y": idx.get("low"),
            "roe": idx.get("roe"),
            "temperature": temperature,
            "action": action,
            "reason": reason + ("（PB优先）" if pb_only else ""),
            "pb_only": pb_only,
            "snapshot_date": payload.get("fetched_at", ""),
            "source": "qieman",
            "metric": "PB" if pb_only else "PE",
        })

    rows.sort(key=lambda r: ({"buy": 0, "watch": 1, "hold": 2, "reduce": 3, "pause": 4}.get(r["action"], 9), r["temperature"] or 999))
    groups = {k: [] for k in ("buy", "watch", "hold", "reduce", "pause")}
    for row in rows:
        groups.setdefault(row["action"], []).append(row)

    fetched_at = payload.get("fetched_at", "")
    freshness = {
        "snapshot_date": fetched_at,
        "snapshot_mtime": fetched_at,
        "market_max_date": fetched_at,
        "index_count": len(rows),
        "source": "qieman",
        "served_from": "qieman_cache",
        "note": NOTE,
        "percentile_mode": "qieman_10y",
    }

    return {
        "freshness": freshness,
        "total": len(rows),
        "counts": {k: len(v) for k, v in groups.items()},
        "top": {
            "buy": groups["buy"][:6],
            "watch": groups["watch"][:6],
            "hold": groups["hold"][:6],
            "reduce": groups["reduce"][:6],
            "pause": groups["pause"][:10],
        },
        "rows": rows,
        "data_source": "qieman",
        "fetched_at": fetched_at,
        "cache_only": True,
    }
