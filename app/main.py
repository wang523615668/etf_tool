from __future__ import annotations

import csv
import json
import yaml
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.data_sources.long_win import load_long_win
from app.data_sources.danjuan_valuations import (
    build_valuation_snapshot as build_danjuan_valuation_snapshot,
)
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
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def load_json(name: str) -> dict[str, Any]:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


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
    """Prefer 理杏仁; fallback 且慢; then local JZTZ matrix if present."""
    source = (source or "lixinger").lower()
    if source in {"lixinger", "auto", "default"}:
        try:
            dash = build_lixinger_valuation_snapshot()
            rows = dash.get("rows") or []
            if rows:
                return rows
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


def valuation_dashboard(source: str = "lixinger") -> dict[str, Any]:
    source = (source or "lixinger").lower()
    errors: list[str] = []
    if source in {"lixinger", "auto", "default"}:
        try:
            # Web path: cache only — never burn 理杏仁 token on page open
            dash = build_lixinger_valuation_snapshot(allow_network=False)
            if dash.get("rows"):
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
            row["cp"] = cp_by_name.get(row.get("name")) or cp_by_code.get(code)
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
        "valuation_dashboard": valuation,
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
    return action_sheet(
        signals=filtered.get("signals") or [],
        suppressed=filtered.get("suppressed_signals") or [],
        valuation_rows=valuation.get("rows") or [],
    )


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


@app.get("/api/sunburst/{plan_key}")
def api_sunburst(plan_key: str) -> dict[str, Any]:
    if plan_key not in {"long_win_150", "long_win_s"}:
        plan_key = "long_win_150"
    return sunburst_data(plan_key)


@app.get("/api/compare")
def api_compare() -> list[dict[str, Any]]:
    return compare_signals()


@app.get("/api/calibration")
def api_calibration() -> dict[str, Any]:
    """规则校准池：本地提醒 vs E大操作匹配统计。"""
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


@app.get("/api/valuations")
def api_valuations(source: str = "lixinger") -> dict[str, Any]:
    return valuation_dashboard(source=source)


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
