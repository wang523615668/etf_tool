from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = _REPO_ROOT / "cache"
CACHE_FILE = CACHE_DIR / "danjuan_valuations.json"
DEFAULT_TTL = 30 * 60
URL = "https://danjuanfunds.com/djapi/index_eva/dj"

# map dashboard names -> danjuan codes / aliases
INDEX_ALIASES = {
    "沪深300": ["SH000300", "000300", "沪深300"],
    "中证500": ["SH000905", "000905", "中证500"],
    "上证50": ["SH000016", "000016", "上证50"],
    "创业板指": ["SZ399006", "399006", "创业板", "创业板指"],
    "科创50": ["SH000688", "000688", "科创50"],
    "中证红利": ["SH000922", "000922", "中证红利"],
    "红利低波": ["CSIH30269", "H30269", "红利低波"],
    "中证白酒": ["SZ399997", "399997", "中证白酒"],
    "中证医疗": ["SH000991", "000991", "全指医药", "中证医疗", "医药100"],
    "中证传媒": ["SZ399971", "399971", "中证传媒"],
    "证券公司": ["SZ399975", "399975", "证券公司"],
    "中证银行": ["SZ399986", "399986", "中证银行"],
    "中证环保": ["SH000827", "000827", "中证环保"],
    "全指消费": ["SH000932", "000932", "主要消费", "全指消费"],
    "全指信息": ["SH000993", "000993", "全指信息"],
    "养老产业": ["SZ399812", "399812", "养老产业"],
    "中概互联": ["CSIH30533", "H30533", "中概互联", "中概互联50"],
    "恒生指数": ["HKHSI", "HSI", "恒生指数"],
    "恒生科技": ["HKHSTECH", "HSTECH", "恒生科技"],
}

PB_ONLY = {"中证红利", "红利低波", "中证银行"}


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    errors = []
    for proxy in (None, "http://127.0.0.1:7890"):
        try:
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                )
            else:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("; ".join(errors))


def _ms_to_date(ts: Any) -> str | None:
    try:
        ts = int(ts)
        # danjuan currently returns future-ish ms epoch; still convertible
        if ts > 10_000_000_000:
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return None


def fetch_raw(force: bool = False) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not force and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - float(cached.get("fetched_ts") or 0) < DEFAULT_TTL:
                return cached
        except Exception:
            pass
    raw = json.loads(_http_get(URL))
    items = (raw.get("data") or {}).get("items") or []
    payload = {
        "fetched_ts": time.time(),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "danjuanfunds",
        "url": URL,
        "items": items,
    }
    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def _match_item(items: list[dict[str, Any]], aliases: list[str]) -> dict[str, Any] | None:
    alias_set = set(aliases)
    # exact code/name first
    for it in items:
        code = str(it.get("index_code") or "")
        name = str(it.get("name") or "")
        if code in alias_set or name in alias_set:
            return it
    # fuzzy name contains
    for it in items:
        name = str(it.get("name") or "")
        if any(a in name for a in aliases if not a[:1].isdigit() and not a.startswith(("SH", "SZ", "HK", "CSI"))):
            return it
    return None


def _valuation_action(pe_pct: float | None, pb_pct: float | None, pb_only: bool = False) -> tuple[str, str]:
    if pb_only:
        values = [v for v in (pb_pct,) if v is not None]
    else:
        values = [v for v in (pe_pct, pb_pct) if v is not None]
    if not values:
        return "watch", "估值字段不足，先观察"
    score = sum(values) / len(values)
    if score <= 0.25:
        return "buy", "分位处于低估区"
    if score >= 0.85:
        return "reduce", "分位处于高估区"
    if score >= 0.65:
        return "hold", "估值偏高，控制加仓"
    return "watch", "估值中性，等待更好赔率"


def build_valuation_snapshot(force: bool = False) -> dict[str, Any]:
    payload = fetch_raw(force=force)
    items = payload.get("items") or []
    rows = []
    dates = []
    for name, aliases in INDEX_ALIASES.items():
        it = _match_item(items, aliases)
        if not it:
            continue
        pe = it.get("pe")
        pb = it.get("pb")
        pe_pct = it.get("pe_percentile")
        pb_pct = it.get("pb_percentile")
        try:
            pe = float(pe) if pe not in (None, "", 0) else None
        except Exception:
            pe = None
        try:
            pb = float(pb) if pb not in (None, "") else None
        except Exception:
            pb = None
        try:
            pe_pct = float(pe_pct) if pe_pct not in (None, "") else None
        except Exception:
            pe_pct = None
        try:
            pb_pct = float(pb_pct) if pb_pct not in (None, "") else None
        except Exception:
            pb_pct = None

        pb_only = name in PB_ONLY
        if pb_only:
            pe_pct_use = None
        else:
            pe_pct_use = pe_pct
        action, reason = _valuation_action(pe_pct_use, pb_pct, pb_only=pb_only)
        vals = [v for v in ((pb_pct,) if pb_only else (pe_pct_use, pb_pct)) if v is not None]
        temperature = round(sum(vals) / len(vals) * 100) if vals else None
        d = _ms_to_date(it.get("ts")) or it.get("date")
        if d:
            dates.append(str(d)[:10])
        rows.append(
            {
                "name": name,
                "code": it.get("index_code"),
                "pe": pe,
                "pe_percentile": pe_pct_use,
                "pb": pb,
                "pb_percentile": pb_pct,
                "temperature": temperature,
                "action": action,
                "reason": reason + ("（PB优先）" if pb_only else ""),
                "snapshot_date": str(d)[:10] if d else None,
                "pb_only": pb_only,
                "roe": it.get("roe"),
                "yeild": it.get("yeild"),
                "eva_type": it.get("eva_type"),
                "source": "danjuan",
            }
        )

    priority = {"buy": 0, "watch": 1, "hold": 2, "reduce": 3, "pause": 4}
    rows = sorted(
        rows,
        key=lambda row: (
            priority.get(row["action"], 9),
            row["temperature"] if row["temperature"] is not None else 999,
        ),
    )
    max_date = max(dates) if dates else None
    min_date = min(dates) if dates else None
    freshness = {
        "snapshot_date": max_date,
        "snapshot_mtime": payload.get("fetched_at"),
        "market_min_date": min_date,
        "market_max_date": max_date,
        "cn_min_date": min_date,
        "cn_max_date": max_date,
        "cn_aligned": bool(dates) and len(set(dates)) == 1,
        "hk_max_date": max_date,
        "overseas_max_date": max_date,
        "index_count": len(rows),
        "warning": None,
        "source": "danjuanfunds",
        "note": "估值来自且慢指数估值接口（非理杏仁原始API）",
    }
    groups = {k: [] for k in ("buy", "watch", "hold", "reduce", "pause")}
    for row in rows:
        groups.setdefault(row["action"], []).append(row)
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
        "data_source": "danjuan",
        "fetched_at": payload.get("fetched_at"),
    }


if __name__ == "__main__":
    print(json.dumps(build_valuation_snapshot(force=True), ensure_ascii=False, indent=2)[:1500])
