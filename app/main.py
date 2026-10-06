from __future__ import annotations

import csv
import json
import time
import yaml
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.data_sources.long_win import load_long_win
from app.data_sources.danjuan_valuations import (
    build_valuation_snapshot as build_danjuan_valuation_snapshot,
)
from app.data_sources.self_valuations import build_self_rows
from app.data_sources.lixinger import (
    INDEX_CONFIG as LIXINGER_INDEX_CONFIG,
    build_valuation_snapshot as build_lixinger_valuation_snapshot,
    get_index_detail as get_lixinger_index_detail,
)
from app.decision_memory import (
    add_trade,
    apply_execution_filter,
    delete_trade,
    load_my_trades,
    update_settings as update_my_trade_settings,
)
from app.account_ledger import (
    action_sheet,
    format_daily_push,
    ledger_snapshot,
    load_account,
    market_position_guide,
    save_account,
)
from app import pending_reminders


BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
STATIC = BASE / "app" / "static"
CONFIG_FILE = BASE / "config.yaml"

# 加载配置
def load_config() -> dict[str, Any]:
    """加载配置文件，返回配置字典，文件不存在时返回默认配置"""
    default_config = {
        "paths": {
            "jztz_base": "/vol1/1000/openzl/jztz",
            "topic_index": str(BASE / "data" / "topic_index.json"),
            "cache_dir": "cache",
            "data_dir": "data",
        },
        "api": {
            "host": "127.0.0.1",
            "port": 8888,
        },
        "decision": {
            "buy_cooldown_days": 30,
            "buy_drop_resume_pct": 0.10,
            "sell_cooldown_days": 30,
            "sell_rise_resume_pct": 0.10,
            "match_by_category": True,
            "category_cap_pct": 0.25,
        },
        "market_position": {
            "index_name": "A股全指",
            "window_years": 10,
        },
        "data_sources": {
            "lixinger": {
                "history_years": 20,
                "percentile_years": 5,
                "fetch_chunk_days": 730,
                "series_ttl": 86400,
                "snapshot_ttl": 604800,
            }
        },
    }
    
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
                # 合并用户配置到默认配置
            merged = {**default_config, **user_config}
            # 合并嵌套字典
            for key, value in user_config.items():
                if isinstance(value, dict) and isinstance(default_config.get(key), dict):
                    merged[key] = {**default_config[key], **value}
            return merged
        except Exception as e:
            # 配置文件解析失败时使用默认配置
            print(f"Warning: 配置文件解析失败，使用默认配置: {e}")
            return default_config
    return default_config

CONFIG = load_config()

# 从配置解析路径，支持相对路径
def resolve_path(path_str: str | None) -> Path:
    """解析路径字符串，支持相对于BASE的相对路径"""
    if not path_str:
        return BASE
    path = Path(path_str)
    if not path.is_absolute():
        path = BASE / path
    return path

JZTZ_BASE = resolve_path(CONFIG["paths"]["jztz_base"])
JZTZ_OUTPUTS = JZTZ_BASE / "daily_outputs"
JZTZ_MARKET_DATA = JZTZ_BASE / "market_data"
TOPIC_INDEX = resolve_path(CONFIG["paths"]["topic_index"])
CACHE_DIR = resolve_path(CONFIG["paths"]["cache_dir"])
ED_TALKS = DATA / "ed_talks.json"
INDEX_KNOWLEDGE = DATA / "index_knowledge.json"
CALIBRATION_WINDOW_DAYS = 45

app = FastAPI(title="ETF 拯救世界投资仪表盘")
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)


class _VersionedStaticFiles(StaticFiles):
    """带 ?v= 版本号的静态资源返回长缓存头（1年），无版本号资源走默认 ETag。"""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code == 200 and scope.get("query_string"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


app.mount("/static", _VersionedStaticFiles(directory=STATIC), name="static")


@app.get("/sw.js", response_class=PlainTextResponse)
async def service_worker():
    """根路径 Service Worker：允许 scope=/ 控制首页，支持离线打开。"""
    sw_path = STATIC / "sw.js"
    if not sw_path.exists():
        return PlainTextResponse("", status_code=404)
    return PlainTextResponse(
        sw_path.read_text(encoding="utf-8"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


def load_json(name: str) -> dict[str, Any]:
    # mtime-based process cache: these files are read many times per request
    # (summary calls load_json + compare_signals + ed_talk_archive repeatedly);
    # re-reading & re-parsing multi-MB JSON every call was a measured hotspot.
    path = DATA / name
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        return {}
    hit = _JSON_CACHE.get(name)
    if hit and hit[0] == mt:
        return hit[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    if len(_JSON_CACHE) > 64:
        _JSON_CACHE.clear()
    _JSON_CACHE[name] = (mt, data)
    return data


_JSON_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}


def long_win_payload() -> dict[str, Any]:
    return load_long_win()


def plan_summary(plan_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = (payload or long_win_payload())["long_win"]
    plan = data["plans"][plan_key]
    positions = plan["positions"]
    total = sum(float(p.get("shares", 0)) for p in positions)
    by_cat: dict[str, float] = defaultdict(float)
    for p in positions:
        by_cat[p["category"]] += float(p.get("shares", 0))
    return {
        "name": plan["name"],
        "total_shares": total,
        "category_count": len(by_cat),
        "positions_count": len(positions),
        "categories": dict(sorted(by_cat.items(), key=lambda kv: kv[1], reverse=True)),
        "positions": positions,
    }


def sunburst_data(plan_key: str) -> dict[str, Any]:
    summary = plan_summary(plan_key)
    children = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in summary["positions"]:
        grouped[p["category"]].append(p)
    for category, items in sorted(grouped.items(), key=lambda kv: sum(i["shares"] for i in kv[1]), reverse=True):
        children.append({
            "name": category,
            "value": sum(i["shares"] for i in items),
            "children": [
                {"name": f"{i['name']}({i['code']})", "value": i["shares"], "code": i["code"]}
                for i in sorted(items, key=lambda x: x["shares"], reverse=True)
            ],
        })
    return {"name": summary["name"], "children": children}


def code_key(code: str) -> str:
    return (code or "").split(".")[0]


def action_date(value: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def days_between(left: Any, right: Any) -> int | None:
    left_date = action_date(left)
    right_date = action_date(right)
    if not left_date or not right_date:
        return None
    return abs((left_date - right_date).days)


def action_family(value: Any) -> str:
    mapping = {"reduce": "sell", "sell": "sell", "buy": "buy"}
    return mapping.get(str(value or ""), str(value or ""))


def category_key(row: dict[str, Any]) -> str:
    value = row.get("index_name") or row.get("category") or row.get("name") or ""
    text = str(value)
    aliases = [
        ("全指消费", "消费"),
        ("主要消费", "消费"),
        ("全指医药", "医药"),
        ("中证医疗", "医药"),
        ("养老产业", "医药"),
        ("券商", "证券"),
        ("证券公司", "证券"),
        ("中证红利", "红利低波"),
        ("金融地产", "金融"),
        ("港股科技", "恒生科技"),
        ("海外互联", "中概互联"),
    ]
    for old, new in aliases:
        text = text.replace(old, new)
    return text


def ed_talk_archive(limit: int | None = None, include_full: bool = False) -> dict[str, Any]:
    if not ED_TALKS.exists():
        return {"generated_at": None, "total": 0, "items": []}
    raw = json.loads(ED_TALKS.read_text(encoding="utf-8"))
    items = list(raw.get("items", []))
    total = len(items)
    if limit is not None:
        items = items[: max(0, int(limit))]
    if not include_full:
        slim = []
        for item in items:
            slim.append(
                {
                    "id": item.get("id"),
                    "item_id": item.get("item_id"),
                    "title": item.get("title"),
                    "date": item.get("date"),
                    "url": item.get("url") or item.get("content_url"),
                    "operation_reason": item.get("operation_reason"),
                    "reason_evidence": (item.get("reason_evidence") or [])[:3],
                    "valuation_context": item.get("valuation_context"),
                    "position_context": item.get("position_context"),
                    "risk_note": item.get("risk_note"),
                    "text_excerpt": item.get("text_excerpt") or item.get("summary"),
                    "action": item.get("action"),
                    "source": item.get("source"),
                }
            )
        items = slim
    return {"generated_at": raw.get("generated_at"), "total": total, "items": items}


def _norm_url(url: str | None) -> str:
    if not url:
        return ""
    return str(url).strip().rstrip("/").replace("http://", "https://")


def reason_by_url() -> dict[str, dict[str, Any]]:
    """Exact URL index. Note: 发车 content/items/* 与社区 content-detail?postId=* 是两套 ID。"""
    out: dict[str, dict[str, Any]] = {}
    for item in ed_talk_archive(include_full=True).get("items", []):
        candidates: list[str] = []
        for key in ("url", "content_url"):
            u = item.get(key)
            if u:
                candidates.append(str(u))
        for u in item.get("alt_urls") or []:
            if u:
                candidates.append(str(u))
        # also accept bare item-id aliases if present
        iid = item.get("item_id") or item.get("id")
        if iid and str(item.get("source") or "").startswith("qieman_content"):
            candidates.extend(
                [
                    f"https://qieman.com/content/items/{iid}",
                    f"https://content.qieman.com/items/{iid}",
                    f"https://qieman.com/content/items/{iid}?preview=1",
                    f"https://content.qieman.com/items/{iid}?preview=1",
                ]
            )
        for url in candidates:
            out[url] = item
            out[_norm_url(url)] = item
            # strip query for preview/cash_masked variants
            base = str(url).split("?", 1)[0]
            out[base] = item
            out[_norm_url(base)] = item
    return out


def reason_by_date() -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in ed_talk_archive(include_full=True).get("items", []):
        day = str(item.get("date") or "")[:10]
        if day:
            grouped[day].append(item)
    return grouped


def _talk_relevance_score(action: dict[str, Any], talk: dict[str, Any]) -> int:
    """Prefer same-day talks that look like operation notes for this action."""
    text = " ".join(
        str(talk.get(k) or "")
        for k in ("title", "operation_reason", "text_excerpt", "valuation_context", "position_context")
    )
    score = 0
    name = str(action.get("name") or "")
    category = str(action.get("category") or "")
    code = str(action.get("code") or "").split(".")[0]
    action_word = "买入" if action_family(action.get("action")) == "buy" else "卖出"
    for token in (name, category, code, action_word, "发车", "长赢", "份", "调仓", "低估", "高估", "估值", "仓位"):
        if token and token in text:
            score += 3 if token in {name, category, code} else 1
    # longer body is usually more useful as context
    score += min(len(str(talk.get("operation_reason") or "")) // 80, 3)
    return score


def resolve_action_reason(
    action: dict[str, Any],
    reasons: dict[str, dict[str, Any]] | None = None,
    by_date: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Match E大发车 reason:
    1) exact/normalized content URL
    2) same-day community archive posts (content/items 与 postId 体系不同，按日回退)
    """
    reasons = reasons if reasons is not None else reason_by_url()
    by_date = by_date if by_date is not None else reason_by_date()
    url = action.get("url")
    detail = reasons.get(url) or reasons.get(_norm_url(url))
    if detail:
        return detail, "url"
    day = str(action.get("date") or "")[:10]
    candidates = by_date.get(day) or []
    if not candidates:
        return None, None
    ranked = sorted(candidates, key=lambda t: _talk_relevance_score(action, t), reverse=True)
    best = ranked[0]
    # only accept weak same-day context if it has some text
    if not (best.get("operation_reason") or best.get("text_excerpt") or best.get("title")):
        return None, None
    return best, "same_day"


def reason_quality(detail: dict[str, Any] | None, match_basis: str | None = None) -> dict[str, Any]:
    if not detail:
        return {"level": "missing", "label": "待归档", "score": 0}
    score = 0
    reason = detail.get("operation_reason") or ""
    evidence = detail.get("reason_evidence") or []
    if len(reason) >= 120:
        score += 2
    elif len(reason) >= 40:
        score += 1
    if evidence:
        score += 1
    if detail.get("valuation_context") not in (None, "", "未明确"):
        score += 1
    if detail.get("position_context") not in (None, "", "未明确"):
        score += 1
    # same-day community posts are useful context, but weaker than exact article match
    if match_basis == "same_day":
        score = max(1, score - 1)
        if score >= 3:
            return {"level": "medium", "label": "同日发言可参考", "score": score}
        return {"level": "low", "label": "同日发言偏弱", "score": score}
    if score >= 4:
        return {"level": "high", "label": "原因较完整", "score": score}
    if score >= 2:
        return {"level": "medium", "label": "原因可参考", "score": score}
    return {"level": "low", "label": "原因偏弱", "score": score}


def enrich_action_reason(
    action: dict[str, Any],
    reasons: dict[str, dict[str, Any]] | None = None,
    by_date: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    detail, match_basis = resolve_action_reason(action, reasons=reasons, by_date=by_date)
    if not detail:
        return {**action, "reason_detail": None, "reason_quality": reason_quality(None), "reason_match": None}
    reason_text = detail.get("operation_reason") or detail.get("text_excerpt") or detail.get("title") or ""
    if match_basis == "same_day":
        prefix = f"【同日发言参考 · {detail.get('title') or '无标题'}】\n"
        if not str(reason_text).startswith("【同日发言参考"):
            reason_text = prefix + str(reason_text)
    reason_detail = {
        "operation_reason": reason_text,
        "reason_evidence": detail.get("reason_evidence", []),
        "valuation_context": detail.get("valuation_context"),
        "position_context": detail.get("position_context"),
        "risk_note": detail.get("risk_note"),
        "fetched_at": detail.get("fetched_at"),
        "url": detail.get("url") or action.get("url"),
        "match_basis": match_basis,
        "source_title": detail.get("title"),
    }
    return {
        **action,
        "reason_detail": reason_detail,
        "reason_quality": reason_quality(reason_detail, match_basis=match_basis),
        "reason_match": match_basis,
    }


def find_ed_match(signal: dict[str, Any], ed_actions: list[dict[str, Any]], max_days: int = CALIBRATION_WINDOW_DAYS) -> tuple[dict[str, Any] | None, str | None, int | None]:
    best: tuple[dict[str, Any] | None, str | None, int | None] = (None, None, None)
    signal_code = code_key(signal.get("code", ""))
    signal_category = category_key(signal)
    for action in ed_actions:
        if action_family(action.get("action")) != action_family(signal.get("action")):
            continue
        delta = days_between(signal.get("date"), action.get("date"))
        if delta is None or delta > max_days:
            continue
        basis = None
        if signal_code and signal_code == code_key(action.get("code", "")):
            basis = "code"
        elif signal_category and signal_category == category_key(action):
            basis = "category"
        if not basis:
            continue
        if best[0] is None or delta < (best[2] or 9999) or basis == "code" and best[1] != "code":
            best = (action, basis, delta)
    return best


def compare_signals(ed_actions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    sig = load_json("signals.json")
    reasons = reason_by_url()
    by_date = reason_by_date()
    source_actions = ed_actions if ed_actions is not None else sig.get("ed_actions", [])
    enriched_actions = [enrich_action_reason(action, reasons, by_date) for action in source_actions]
    rows = []
    matched_action_ids: set[tuple[str, str, str]] = set()
    for signal in sig.get("signals", []):
        matched, basis, delta = find_ed_match(signal, enriched_actions)
        if matched:
            matched_action_ids.add((matched.get("date", ""), matched.get("action", ""), code_key(matched.get("code", ""))))
        rows.append({
            **signal,
            "ed_matched": bool(matched),
            "ed_reference": matched,
            "match_basis": basis,
            "match_days": delta,
            "gap": f"一致（按{basis}，相差{delta}天）" if matched else "本地提醒未见 E大近期同向操作",
        })
    for action in enriched_actions[:30]:
        action_id = (action.get("date", ""), action.get("action", ""), code_key(action.get("code", "")))
        if action_id in matched_action_ids:
            continue
        if find_ed_match(action, sig.get("signals", []), max_days=CALIBRATION_WINDOW_DAYS)[0]:
            continue
        rows.append({
            "date": action["date"],
            "source": "ed_action",
            "action": action["action"],
            "name": action["name"],
            "code": action["code"],
            "shares": action["shares"],
            "category": action.get("category"),
            "reason": "E大已有操作，本地模型未提醒；需要复盘规则是否漏判",
            "confidence": None,
            "ed_matched": False,
            "ed_reference": action,
            "match_basis": None,
            "match_days": None,
            "gap": "E大操作但本地未提醒",
        })
    return rows


def _latest_csv_date(path: Path) -> str | None:
    if not path.exists():
        return None
    latest: str | None = None
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            value = (row.get("date") or "")[:10]
            if value and (latest is None or value > latest):
                latest = value
    return latest


def valuation_freshness() -> dict[str, Any]:
    snapshots = sorted(JZTZ_OUTPUTS.glob("matrix_snapshot_*.csv"))
    latest_snapshot = snapshots[-1] if snapshots else None
    market_files = sorted(JZTZ_MARKET_DATA.glob("*.csv"))
    market_dates = {path.stem: _latest_csv_date(path) for path in market_files}
    valid_dates = [value for value in market_dates.values() if value]
    cn_dates = [
        value for name, value in market_dates.items()
        if value and not name.startswith(("恒生", "标普", "中概"))
    ]
    hk_dates = [value for name, value in market_dates.items() if value and name.startswith("恒生")]
    overseas_dates = [value for name, value in market_dates.items() if value and name.startswith(("中概", "标普"))]
    snapshot_date = latest_snapshot.stem.removeprefix("matrix_snapshot_") if latest_snapshot else None
    min_cn_date = min(cn_dates) if cn_dates else None
    max_cn_date = max(cn_dates) if cn_dates else None
    cn_aligned = bool(cn_dates) and len(set(cn_dates)) == 1
    warning = None
    if min_cn_date and max_cn_date and min_cn_date < max_cn_date:
        warning = f"A股理杏仁底层数据不一致：最新为 {max_cn_date}，部分指数最旧为 {min_cn_date}；暂停自动买卖判断。"
    return {
        "snapshot_date": snapshot_date,
        "snapshot_mtime": datetime.fromtimestamp(latest_snapshot.stat().st_mtime).isoformat(timespec="seconds") if latest_snapshot else None,
        "market_min_date": min(valid_dates) if valid_dates else None,
        "market_max_date": max(valid_dates) if valid_dates else None,
        "cn_min_date": min_cn_date,
        "cn_max_date": max_cn_date,
        "cn_aligned": cn_aligned,
        "hk_max_date": max(hk_dates) if hk_dates else None,
        "overseas_max_date": max(overseas_dates) if overseas_dates else None,
        "index_count": len(market_dates),
        "warning": warning,
    }


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_snapshot_path() -> Path | None:
    snapshots = sorted(JZTZ_OUTPUTS.glob("matrix_snapshot_*.csv"))
    return snapshots[-1] if snapshots else None


def _read_matrix(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    if not rows:
        return {}
    columns = rows[0][1:]
    matrix = {column: {} for column in columns}
    for row in rows[1:]:
        if not row:
            continue
        metric = row[0]
        for column, value in zip(columns, row[1:]):
            matrix[column][metric] = value
    return matrix


def _valuation_action(pe_pct: float | None, pb_pct: float | None, stale: bool) -> tuple[str, str]:
    values = [value for value in (pe_pct, pb_pct) if value is not None]
    if not values:
        return "watch", "估值字段不足，先观察"
    score = sum(values) / len(values)
    if stale:
        return "pause", "数据过期，暂停自动买卖判断"
    if score <= 0.25:
        return "buy", "PE/PB 分位处于低估区"
    if score >= 0.85:
        return "reduce", "PE/PB 分位处于高估区"
    if score >= 0.65:
        return "hold", "估值偏高，控制加仓"
    return "watch", "估值中性，等待更好赔率"


def valuation_rows(source: str = "lixinger") -> list[dict[str, Any]]:
    """Prefer 理杏仁; fallback 且慢; then local JZTZ matrix if present.
    source='qieman' 时用且慢口径（8年百分位 + 强周期PB）"""
    source = (source or "lixinger").lower()
    if source == "qieman":
        # 且慢真实估值数据（每日 Playwright 抓取缓存）
        try:
            from app.data_sources.qieman_valuations import build_valuation_snapshot as build_qieman_snapshot
            dash = build_qieman_snapshot()
            rows = dash.get("rows") or []
            if rows:
                return rows
        except Exception:
            pass
        return valuation_rows(source="lixinger")
    if source in {"lixinger", "auto", "default"}:
        try:
            dash = build_lixinger_valuation_snapshot(allow_network=False)
            rows = dash.get("rows") or []
            if rows:
                dash = apply_valuation_rescue(dash)
                return dash.get("rows") or []
        except Exception:
            pass
    if source in {"danjuan", "auto", "default", "lixinger"}:
        try:
            dash = build_danjuan_valuation_snapshot()
            rows = dash.get("rows") or []
            if rows:
                return rows
        except Exception:
            pass
    if source not in {"jztz", "auto", "default", "lixinger", "danjuan"}:
        return []
    path = _latest_snapshot_path()
    if not path:
        return []
    freshness = valuation_freshness()
    stale = bool(freshness.get("warning"))
    matrix = _read_matrix(path)
    rows = []
    for name, metrics in matrix.items():
        pe = _float_or_none(metrics.get("当前PE (正数等权)"))
        pe_pct = _float_or_none(metrics.get("PE五年百分位"))
        pb = _float_or_none(metrics.get("当前PB (正数等权)"))
        pb_pct = _float_or_none(metrics.get("PB五年百分位"))
        action, reason = _valuation_action(pe_pct, pb_pct, stale)
        values = [value for value in (pe_pct, pb_pct) if value is not None]
        temperature = round(sum(values) / len(values) * 100) if values else None
        rows.append({
            "name": name,
            "pe": pe,
            "pe_percentile": pe_pct,
            "pb": pb,
            "pb_percentile": pb_pct,
            "temperature": temperature,
            "action": action,
            "reason": reason,
            "snapshot_date": freshness.get("snapshot_date"),
            "freshness_warning": freshness.get("warning"),
            "source": "jztz",
        })
    priority = {"buy": 0, "watch": 1, "hold": 2, "reduce": 3, "pause": 4}
    return sorted(
        rows,
        key=lambda row: (
            priority.get(row["action"], 9),
            row["temperature"] if row["temperature"] is not None else 999,
        ),
    )


def apply_valuation_rescue(dash: dict[str, Any]) -> dict[str, Any]:
    """两层兜底：理杏仁过期(pause)的指数先由蛋卷顶上，蛋卷也没有的用
    理杏仁历史+实时价格自算（self）。就地修改并返回 dash。"""
    try:
        dj = build_danjuan_valuation_snapshot()
        dj_by_name = {r.get("name"): r for r in (dj.get("rows") or [])}
        replaced = 0
        for i, r in enumerate(dash.get("rows") or []):
            if r.get("action") == "pause":
                alt = dj_by_name.get(r.get("name"))
                if alt and alt.get("action") != "pause":
                    merged = dict(alt)
                    merged["source_override"] = "danjuan"
                    merged["orig_reason"] = r.get("reason")
                    dash["rows"][i] = merged
                    replaced += 1
        fr = dash.setdefault("freshness", {})
        if replaced:
            counts2: dict[str, int] = {}
            for r in dash["rows"]:
                counts2[r.get("action")] = counts2.get(r.get("action"), 0) + 1
            dash["counts"] = counts2
            fr["danjuan_rescued"] = replaced
            fr["note"] = f"理杏仁CN数据滞后，{replaced} 个指数估值由蛋卷基金兜底"
        # 第二层兜底：蛋卷也没有的（全指金融/红利低波100）用理杏仁历史+实时价自算
        still_paused = [
            r.get("name") for r in dash.get("rows") or [] if r.get("action") == "pause"
        ]
        self_rows = build_self_rows(still_paused)
        if self_rows:
            self_by_name = {r["name"]: r for r in self_rows}
            for i, r in enumerate(dash["rows"]):
                if r.get("action") == "pause" and self_by_name.get(r.get("name")):
                    dash["rows"][i] = self_by_name[r["name"]]
            fr["self_rescued"] = len(self_rows)
            note = fr.get("note") or ""
            extra = (
                f"；另 {len(self_rows)} 个由理杏仁历史+实时价格自算兜底"
                "（红利低波100 以 515100ETF 价代理）"
            )
            fr["note"] = (note + extra) if note else extra.strip("；")
        if self_rows:
            counts3: dict[str, int] = {}
            for r in dash["rows"]:
                counts3[r.get("action")] = counts3.get(r.get("action"), 0) + 1
            if counts3 != dash.get("counts"):
                dash["counts"] = counts3
    except Exception:
        pass  # 兜底失败不影响主快照
    return dash


# 估值快照进程内 TTL 缓存：估值数据日频（17:30 刷新），180s 缓存避免首页
# 多 API（summary/valuations/decision/market-position/ledger/action-sheet）并发时重复重算（实测 /api/summary 11s）
_VAL_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_VAL_CACHE_TTL = 180.0


def valuation_dashboard(source: str = "lixinger") -> dict[str, Any]:
    source = (source or "lixinger").lower()
    now = time.time()
    hit = _VAL_CACHE.get(source)
    if hit and now - hit[0] < _VAL_CACHE_TTL:
        return hit[1]
    dash = _valuation_dashboard_impl(source)
    _VAL_CACHE[source] = (now, dash)
    return dash


def _valuation_dashboard_impl(source: str = "lixinger") -> dict[str, Any]:
    source = (source or "lixinger").lower()
    errors: list[str] = []
    if source == "qieman":
        try:
            from app.data_sources.qieman_valuations import build_valuation_snapshot as build_qieman_snapshot
            dash = build_qieman_snapshot()
            if dash.get("rows"):
                return dash
        except Exception:
            pass
        source = "lixinger"
    if source in {"lixinger", "auto", "default"}:
        try:
            # Web path: cache only — never burn 理杏仁 token on page open
            dash = build_lixinger_valuation_snapshot(allow_network=False)
            if dash.get("rows"):
                # 理杏仁免费层 CN 数据滞后可达数周：过期暂停(pause)的指数用蛋卷当前数据顶上，
                # 蛋卷也没有的保留原行（pause）
                dash = apply_valuation_rescue(dash)
                return dash
            errors.append("lixinger empty/cache-missing")
        except Exception as exc:
            errors.append(f"lixinger: {exc}")
    if source in {"danjuan", "auto", "default", "lixinger"}:
        try:
            dash = build_danjuan_valuation_snapshot()
            if dash.get("rows"):
                if errors:
                    dash.setdefault("freshness", {})["fallback_from"] = "; ".join(errors[:3])
                return dash
            errors.append("danjuan empty")
        except Exception as exc:
            errors.append(f"danjuan: {exc}")

    rows = valuation_rows(source="jztz")
    groups = {key: [] for key in ("buy", "watch", "hold", "reduce", "pause")}
    for row in rows:
        groups.setdefault(row["action"], []).append(row)
    freshness = valuation_freshness()
    if errors and not rows:
        freshness["warning"] = f"估值源不可用: {'; '.join(errors)[:160]}"
    elif errors:
        freshness["note"] = "fallback sources: " + "; ".join(errors[:3])
    return {
        "freshness": freshness,
        "total": len(rows),
        "counts": {key: len(value) for key, value in groups.items()},
        "top": {
            "buy": groups["buy"][:6],
            "watch": groups["watch"][:6],
            "hold": groups["hold"][:6],
            "reduce": groups["reduce"][:6],
            "pause": groups["pause"][:10],
        },
        "rows": rows,
        "data_source": "jztz_fallback" if rows else "none",
        "cache_only": True,
    }


def plan_exposure(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {}
    for key in ("long_win_150", "long_win_s"):
        summary = plan_summary(key, payload)
        total = summary["total_shares"] or 1
        categories = [
            {"name": name, "shares": shares, "weight": round(shares / total, 4)}
            for name, shares in summary["categories"].items()
        ]
        result[key] = {
            "name": summary["name"],
            "total_shares": summary["total_shares"],
            "positions_count": summary["positions_count"],
            "category_count": summary["category_count"],
            "top_categories": categories[:8],
        }
    return result


def topic_library(limit_per_topic: int = 20) -> dict[str, Any]:
    if not TOPIC_INDEX.exists():
        return {"generated_at": None, "total_items": 0, "topic_count": 0, "topics": []}
    raw = json.loads(TOPIC_INDEX.read_text(encoding="utf-8"))
    topics = []
    for name, items in raw.get("topics", {}).items():
        picked = []
        for item in items[:limit_per_topic]:
            picked.append({
                "source": item.get("source"),
                "id": item.get("id"),
                "title": item.get("title"),
                "date": item.get("date"),
                "url": item.get("url"),
                "keywords": item.get("keywords", [])[:8],
                "excerpt": item.get("excerpt", "")[:260],
            })
        topics.append({"name": name, "count": len(items), "items": picked})
    topics.sort(key=lambda topic: topic["count"], reverse=True)
    return {
        "generated_at": raw.get("generated_at"),
        "total_items": raw.get("total_items", 0),
        "topic_count": raw.get("topic_count", len(topics)),
        "topics": topics,
    }


def load_index_knowledge() -> dict[str, Any]:
    if not INDEX_KNOWLEDGE.exists():
        return {"generated_at": None, "indices": {}}
    try:
        return json.loads(INDEX_KNOWLEDGE.read_text(encoding="utf-8"))
    except Exception:
        return {"generated_at": None, "indices": {}}


def _is_150_plan(plan: Any) -> bool:
    text = str(plan or "").strip().lower()
    if not text:
        return False
    return text in {"long_win_150", "150", "长赢150", "长赢 150"} or "long_win_150" in text


def index_knowledge_for(name: str, article_limit: int = 30, action_limit: int = 80) -> dict[str, Any]:
    pack = (load_index_knowledge().get("indices") or {}).get(name) or {}
    if not pack:
        return {
            "article_count": 0,
            "action_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "articles": [],
            "actions": [],
            "marks": [],
            "knowledge_generated_at": load_index_knowledge().get("generated_at"),
            "plan_filter": "long_win_150",
        }
    articles = list(pack.get("articles") or [])[:article_limit]
    # safety: only 150 plan buy/sell on chart + list (exclude S)
    actions = [a for a in (pack.get("actions") or []) if _is_150_plan(a.get("plan"))][:action_limit]
    marks = [m for m in (pack.get("marks") or []) if _is_150_plan(m.get("plan"))][:action_limit]
    return {
        "article_count": int(pack.get("article_count") or len(articles)),
        "action_count": len(actions),
        "buy_count": sum(1 for a in actions if a.get("action") == "buy"),
        "sell_count": sum(1 for a in actions if a.get("action") == "sell"),
        "articles": articles,
        "actions": actions,
        "marks": marks,
        "knowledge_generated_at": load_index_knowledge().get("generated_at"),
        "keywords": pack.get("keywords") or [],
        "plan_filter": "long_win_150",
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/index-detail", response_class=HTMLResponse)
def index_detail_page() -> str:
    page = STATIC / "index_detail.html"
    if not page.exists():
        return "<h1>index detail page missing</h1>"
    return page.read_text(encoding="utf-8")


@app.get("/self-calib", response_class=HTMLResponse)
def self_calib_page() -> str:
    """估值口径标定总览: 逐指数「与E大真值」的偏差 + 分位敏感区间。"""
    page = STATIC / "self_calib.html"
    if not page.exists():
        return "<h1>self calib page missing</h1>"
    return page.read_text(encoding="utf-8")

@app.get("/api/summary")
def api_summary() -> dict[str, Any]:
    long_win = load_json("long_win_positions.json")
    signals = load_json("signals.json")
    valuation = valuation_dashboard()
    reasons = reason_by_url()
    by_date = reason_by_date()
    # personal execution memory: suppress re-buy after you already bought
    # merge previously suppressed so cooldown status can be recomputed each request
    raw_signals = []
    seen_keys = set()
    for s in list(signals.get("signals") or []) + list(signals.get("suppressed_signals") or []):
        key = (str(s.get("action") or ""), str(s.get("name") or ""), str(s.get("code") or "")
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        # strip stale cooldown fields; recompute below
        row = {k: v for k, v in dict(s).items() if k not in {"status", "cooldown"}}
        raw_signals.append(row)

    # attach latest cp from valuation cache so space-rule can work
    cp_by_name = {r.get("name"): r.get("cp") for r in (valuation.get("rows") or []) if r.get("name")}
    cp_by_code = {
        str(r.get("code") or "").split(".")[0]: r.get("cp")
        for r in (valuation.get("rows") or [])
        if r.get("code")
    }
    priced = []
    for s in raw_signals:
        row = dict(s)
        code = str(row.get("code") or "").split(".")[0]
        if row.get("cp") is None:
            # 优先 norm_name（归一化名），再 name
            row["cp"] = (
                cp_by_name.get(row.get("norm_name"))
                or cp_by_name.get(row.get("name"))
                or cp_by_code.get(code)
            )
        priced.append(row)
    filtered = apply_execution_filter(priced)
    suppressed = filtered.get("suppressed_signals") or []
    active = filtered.get("signals") or []
    ledger_snap = ledger_snapshot(valuation.get("rows") or [])
    return {
        "generated_at": long_win.get("generated_at"),
        "data_source": valuation.get("data_source") or long_win.get("source") or "local",
        "long_win_150": plan_summary("long_win_150"),
        "long_win_s": plan_summary("long_win_s"),
        "stats": {
            "long_win_150": long_win["plans"]["long_win_150"].get("stats", {}),
            "long_win_s": long_win["plans"]["long_win_s"].get("stats", {}),
        },
        "recent_actions": [
            enrich_action_reason(action, reasons, by_date) for action in signals.get("ed_actions", [])[:12]
        ],
        "signals": active,
        "suppressed_signals": suppressed,
        "decision_memory": {
            "settings": filtered.get("settings"),
            "my_trade_count": filtered.get("my_trade_count"),
            "rule": "买入后默认冷却30天（约1个月）；或较买入点再跌≥10%才恢复同向买入（时间或空间二选一）",
        },
        "valuation_freshness": valuation.get("freshness") or valuation_freshness(),
        "valuation_rows": (valuation.get("rows") or [])[:12],
        "valuation_dashboard": {**valuation, "rows": (valuation.get("rows") or [])[:12]},
        "plan_exposure": plan_exposure(),
        "calibration": api_calibration(),
        "topic_library": {
            key: value for key, value in topic_library(limit_per_topic=0).items() if key != "topics"
        },
        "ledger": ledger_snap,
        "action_sheet": action_sheet(
            signals=active,
            suppressed=suppressed,
            valuation_rows=valuation.get("rows") or [],
        ),
        "market_position": ledger_snap.get("market_position"),
    }


@app.get("/api/ledger")
def api_ledger() -> dict[str, Any]:
    valuation = valuation_dashboard()
    return ledger_snapshot(valuation.get("rows") or [])


@app.get("/api/market-position")
def api_market_position() -> dict[str, Any]:
    """A股全指 → 目标总仓位（只读本地缓存）。"""
    valuation = valuation_dashboard()
    snap = ledger_snapshot(valuation.get("rows") or [])
    return snap.get("market_position") or market_position_guide(
        positions_summary=snap.get("summary")
    )


@app.get("/api/account")
def api_account_get() -> dict[str, Any]:
    return load_account()


@app.post("/api/account")
def api_account_set(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        return {"ok": True, "account": save_account(body or {})}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/action-sheet")
def api_action_sheet() -> dict[str, Any]:
    valuation = valuation_dashboard()
    long_win = load_json("long_win_positions.json")
    signals = load_json("signals.json")
    raw_signals = []
    seen = set()
    for s in list(signals.get("signals") or []) + list(signals.get("suppressed_signals") or []):
        key = (str(s.get("action") or ""), str(s.get("name") or ""), str(s.get("code") or ""))
        if key in seen:
            continue
        seen.add(key)
        raw_signals.append({k: v for k, v in dict(s).items() if k not in {"status", "cooldown"}})
    cp_by_name = {r.get("name"): r.get("cp") for r in (valuation.get("rows") or []) if r.get("name")}
    priced = []
    for s in raw_signals:
        row = dict(s)
        if row.get("cp") is None:
            row["cp"] = cp_by_name.get(row.get("name"))
        priced.append(row)
    filtered = apply_execution_filter(priced)
    # ---- 提醒追踪：今日 actionable 信号并入 pending（没买就持续提醒） ----
    actionable = filtered.get("signals") or []
    pending = pending_reminders.upsert_from_signals(actionable)
    pending = pending_reminders.filter_dismissed(pending)
    sheet = action_sheet(
        signals=actionable,
        suppressed=filtered.get("suppressed_signals") or [],
        valuation_rows=valuation.get("rows") or [],
    )
    sheet["pending_reminders"] = pending
    return sheet


@app.get("/api/daily-push")
def api_daily_push() -> dict[str, Any]:
    """Build WeChat daily action push text. silent=true when no actionable items."""
    sheet = api_action_sheet()
    ledger = ledger_snapshot((valuation_dashboard().get("rows") or []))
    text = format_daily_push(sheet, ledger)
    return {
        "silent": text is None,
        "text": text or "",
        "action_count": len([a for a in (sheet.get("actions") or []) if a.get("status") == "actionable"]),
        "blocked_count": len(sheet.get("blocked") or []),
        "generated_at": sheet.get("generated_at"),
    }


@app.get("/api/my-trades")
def api_my_trades() -> dict[str, Any]:
    return load_my_trades()


@app.post("/api/my-trades")
def api_my_trades_add(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        # default shares to account setting (1 share)
        acc = load_account()
        if body is None:
            body = {}
        if body.get("shares") is None:
            body = {**body, "shares": acc.get("shares_per_buy") or 1}
        result = add_trade(body)
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.delete("/api/my-trades/{trade_id}")
def api_my_trades_delete(trade_id: str) -> dict[str, Any]:
    return delete_trade(trade_id)


@app.post("/api/my-trades/settings")
def api_my_trades_settings(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return update_my_trade_settings(body or {})


@app.get("/api/pending-reminders")
def api_pending_reminders() -> dict[str, Any]:
    """当前未决提醒（发了信号但你还没执行/忽略的）。"""
    rems = pending_reminders.filter_dismissed(pending_reminders._load().get("reminders") or [])
    return {"reminders": rems,
            "lines": pending_reminders.format_pending_lines(rems)}


@app.post("/api/pending-reminders/bought")
def api_pending_bought(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """告知已买入：移除提醒 + 写入 my_trades（触发冷却压制）。"""
    name = str(body.get("name") or "")
    if not name:
        raise ValueError("name 必填")
    removed = pending_reminders.mark_bought(name)
    trade = add_trade({
        "action": str(body.get("action") or "buy"),
        "name": name,
        "code": body.get("code"),
        "category": body.get("category"),
        "shares": body.get("shares", 1),
        "price": body.get("price"),
        "note": "回复确认买入",
    })
    return {"ok": True, "removed_reminder": removed, "trade": trade["trade"]}


@app.post("/api/pending-reminders/dismiss")
def api_pending_dismiss(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """明确忽略：30天内不再提。"""
    name = str(body.get("name") or "")
    if not name:
        raise ValueError("name 必填")
    removed = pending_reminders.dismiss(name)
    return {"ok": removed is not None, "dismissed": removed}


@app.get("/api/sunburst/{plan_key}")
def api_sunburst(plan_key: str) -> dict[str, Any]:
    if plan_key not in {"long_win_150", "long_win_s"}:
        plan_key = "long_win_150"
    return sunburst_data(plan_key)


@app.get("/api/compare")
def api_compare() -> list[dict[str, Any]]:
    return compare_signals()


_CALIB_CACHE: tuple[float, dict[str, Any]] | None = None
_CALIB_TTL = 600.0  # 校准池统计低频变化，10 分钟缓存（compare_signals 每次全量重算很贵）


@app.get("/api/calibration")
def api_calibration() -> dict[str, Any]:
    """规则校准池：本地提醒 vs E大操作匹配统计。"""
    global _CALIB_CACHE
    now = time.time()
    if _CALIB_CACHE and now - _CALIB_CACHE[0] < _CALIB_TTL:
        return _CALIB_CACHE[1]
    rows = compare_signals()
    matched = [r for r in rows if r.get("ed_matched")]
    local_unmatched = [r for r in rows if r.get("source") == "local_model" and not r.get("ed_matched")]
    ed_unmatched = [r for r in rows if r.get("source") == "ed_action" and not r.get("ed_matched")]
    total = len(rows)
    match_rate = round(len(matched) / total, 4) if total else 0.0
    return {
        "total": total,
        "matched": len(matched),
        "local_unmatched": len(local_unmatched),
        "ed_unmatched": len(ed_unmatched),
        "match_rate": match_rate,
        "window_days": CALIBRATION_WINDOW_DAYS,
        "samples": {
            "matched": matched[:8],
            "local_unmatched": local_unmatched[:8],
            "ed_unmatched": ed_unmatched[:8],
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _CALIB_CACHE = (now, result)
    return result


@app.get("/api/batter-backtest")
def api_batter_backtest() -> dict[str, Any]:
    """击球分数回测结果（scripts/backtest_batter.py 产物）。"""
    path = DATA / "backtest_batter.json"
    if not path.exists():
        return {"error": "尚未运行回测"}
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/batter-score")
def api_batter_score(force: int = 0) -> dict[str, Any]:
    """击球分数：估值40% + 情绪30% + 动量30% → 合成买卖档位。"""
    from app.batter_score import batter_dashboard
    return batter_dashboard(force=bool(force))


@app.get("/api/valuations")
def api_valuations(source: str = "lixinger") -> dict[str, Any]:
    return valuation_dashboard(source=source)


@app.get("/api/decision")
def api_decision(source: str = "lixinger") -> dict[str, Any]:
    """自主决策引擎：估值温度 + E大历史模式 + 我的持仓 → 品种级建议"""
    try:
        from app.decision_engine import build_decision_table
        rows = valuation_rows(source=source)
        return build_decision_table(rows)
    except Exception as exc:
        return {"error": str(exc), "total": 0, "rows": []}


@app.get("/api/index-list")
def api_index_list() -> dict[str, Any]:
    items = [
        {
            "name": name,
            "code": cfg["code"],
            "area": cfg["area"],
            "pb_only": name in {"中证红利", "红利低波", "红利低波100", "中证银行"},
        }
        for name, cfg in LIXINGER_INDEX_CONFIG.items()
    ]
    return {"total": len(items), "items": items}


@app.get("/api/self-daily")
def api_self_daily() -> dict[str, Any]:
    """自算估值序列索引: 全部自选指数 + 全市场主板(A股E大口径), 2018→今日频.
    方法=逐指数最优组合(算法×名单源, 见 data/ed_perindex_best.json):
    真值源=E大 邮件估值表 OCR(2019-12~2023-03, 72 日期) + 他微博亲口读数(2018~2025, 18 锚点),
    逐指数按「逐年偏差中位」挑口径(见 data/ed_calibration.json)。
    p5y_shadow/p10y_shadow=另一名单口径(当时成分↔当前成分)下的分位, 即口径不确定度区间。"""
    d = BASE / "data" / "self_daily"
    out = []
    if not d.exists():
        return {"items": [], "note": "自算序列尚未生成"}
    for fp in sorted(d.glob("*.json")):
        try:
            j = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if j.get("degenerate"):
            continue
        out.append({
            "name": j.get("name"), "code": j.get("code"),
            "latest": j.get("latest"), "p5y": j.get("p5y"), "p10y": j.get("p10y"),
            "days": len(j.get("rows") or []),
            "pit": j.get("pit", False), "method": j.get("method"), "note": j.get("note"),
            "algo": j.get("algo"), "list_mode": j.get("list_mode"),
            "ed_dev_max": j.get("ed_dev_max"), "ed_dev_med": j.get("ed_dev_med"),
            "ed_dev_n": j.get("ed_dev_n"), "ed_dev_six": j.get("ed_dev_six"),
            "ed_anch_n": j.get("ed_anch_n"), "ed_anch_med": j.get("ed_anch_med"),
            "ed_anch_max": j.get("ed_anch_max"),
            "p5y_shadow": j.get("p5y_s"), "p10y_shadow": j.get("p10y_s"),
            "pe_shadow": j.get("pe_shadow"), "shadow_algo": j.get("shadow_algo"),
            "shadow_list": j.get("shadow_list"), "shadow_days": j.get("shadow_days"),
            "calib_at": j.get("calib_at"),
        })
    out.sort(key=lambda x: (x["code"] in ("000985", "MKT_MAIN", "MKT_ALL"), x["name"] or ""))
    return {"total": len(out), "items": out,
            "generated_max": max((x["latest"]["date"] if x.get("latest") else "" for x in out), default="")}


@app.get("/api/self-calibration")
def api_self_calibration() -> dict[str, Any]:
    """自算估值的标定报告: 逐指数口径 + 与E大真值的偏差(邮件估值表72日期 + 他微博亲口读数18条锚点)。
    由 app/self_daily_calib.py 日更写入 data/ed_calibration.json。"""
    fp = BASE / "data" / "ed_calibration.json"
    if not fp.exists():
        return {"items": [], "note": "标定报告尚未生成"}
    try:
        j = json.loads(fp.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"items": [], "error": str(exc)}
    items = []
    for name, v in j.items():
        items.append({
            "name": name, "algo": v.get("algo"), "list_mode": v.get("list"),
            "score": v.get("score"), "peryear": v.get("peryear"),
            "hist_mad": v.get("hist_mad"), "n_hist": v.get("n_hist"),
            "anchor": v.get("anchor"), "n_anch": v.get("n_anch"),
            "years": v.get("yr"), "anchors": v.get("anchors"),
        })
    items.sort(key=lambda x: (x["score"] is None, x["score"] or 99))
    ok = [x for x in items if (x["peryear"] or 99) <= 6]
    return {"total": len(items), "within6pct": len(ok), "items": items,
            "truth": {"email_dates": 72, "prose_anchors": 18,
                      "window": "2019-12~2023-03(邮件表) + 2018-01~2025-09(微博锚点)"}}


@app.get("/api/self-daily/{code}")
def api_self_daily_detail(code: str) -> dict[str, Any]:
    fp = BASE / "data" / "self_daily" / f"{code}.json"
    if not fp.exists():
        return {"error": f"no self-calc series for {code}"}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/index/{name}")
def api_index_detail(name: str, window_years: float = 20.0) -> dict[str, Any]:
    if name not in LIXINGER_INDEX_CONFIG:
        # allow code lookup
        for n, cfg in LIXINGER_INDEX_CONFIG.items():
            if cfg["code"] == name or n.replace(" ", "") == name:
                name = n
                break
        else:
            return {"error": f"unknown index: {name}", "name": name, "rows": []}
    try:
        # detail page: local cache only (no 理杏仁 token on every open)
        detail = get_lixinger_index_detail(name, window_years=window_years, allow_network=False)
    except Exception as exc:
        return {"error": str(exc), "name": name, "rows": []}
    knowledge = index_knowledge_for(name)
    # Align marks to chart date axis: only keep marks that fall inside row window,
    # and attach nearest close (cp) for tooltip / y placement.
    rows = detail.get("rows") or []
    date_to_cp: dict[str, float | None] = {}
    dates = []
    for r in rows:
        d = str(r.get("date") or "")[:10]
        if not d:
            continue
        dates.append(d)
        cp = r.get("cp")
        try:
            date_to_cp[d] = float(cp) if cp is not None else None
        except (TypeError, ValueError):
            date_to_cp[d] = None
    date_set = set(dates)
    marks_out = []
    marks_outside = 0
    for m in knowledge.get("marks") or []:
        day = str(m.get("date") or "")[:10]
        if not day:
            continue
        # if exact trading day missing, keep mark only when inside window range
        if dates and (day < dates[0] or day > dates[-1]):
            marks_outside += 1
            continue
        cp = date_to_cp.get(day)
        day_use = day
        if day not in date_set and dates:
            # nearest previous trading day; if none, next
            prev = [d for d in dates if d <= day]
            nxt = [d for d in dates if d >= day]
            if prev:
                day_use = prev[-1]
            elif nxt:
                day_use = nxt[0]
            cp = date_to_cp.get(day_use)
        marks_out.append({**m, "date": day_use, "cp": cp, "orig_date": day})
    detail["articles"] = knowledge.get("articles") or []
    detail["actions"] = knowledge.get("actions") or []
    detail["marks"] = marks_out
    detail["article_count"] = knowledge.get("article_count") or len(detail["articles"])
    detail["action_count"] = knowledge.get("action_count") or len(detail["actions"])
    detail["buy_count"] = knowledge.get("buy_count") or 0
    detail["sell_count"] = knowledge.get("sell_count") or 0
    detail["marks_visible"] = len(marks_out)
    detail["marks_outside_window"] = marks_outside
    detail["knowledge_generated_at"] = knowledge.get("knowledge_generated_at")
    detail["knowledge_keywords"] = knowledge.get("keywords") or []
    detail["plan_filter"] = knowledge.get("plan_filter") or "long_win_150"
    return detail


@app.get("/api/topics")
def api_topics() -> dict[str, Any]:
    return topic_library()


@app.get("/api/ed-talks")
def api_ed_talks(limit: int = 30, include_full: bool = False) -> dict[str, Any]:
    # Homepage only needs recent slim cards; full text is huge (~0.7MB) and freezes mobile.
    return ed_talk_archive(limit=limit, include_full=include_full)


@app.get("/api/index-knowledge/{name}")
def api_index_knowledge(name: str) -> dict[str, Any]:
    if name not in LIXINGER_INDEX_CONFIG:
        for n, cfg in LIXINGER_INDEX_CONFIG.items():
            if cfg["code"] == name or n.replace(" ", "") == name:
                name = n
                break
    pack = index_knowledge_for(name, article_limit=50, action_limit=120)
    pack["name"] = name
    return pack
