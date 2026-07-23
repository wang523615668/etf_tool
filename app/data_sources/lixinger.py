"""理杏仁 Open API 估值数据源。

Token 读取顺序：
1. 环境变量 LIXINGER_TOKEN
2. <repo>/jztz/token.conf
3. <repo>/data/lixinger_token.conf

成功响应 code==1。CN 指数必须逐个请求，metrics 需带后缀：
- A股: pe_ttm.ewpvo / pb.ewpvo / cp
- 港股: pe_ttm.mcw / pb.mcw / cp
"""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = _REPO_ROOT / "cache" / "lixinger"
SNAPSHOT_CACHE = _REPO_ROOT / "cache" / "lixinger_valuations.json"
TOKEN_CANDIDATES = [
    _REPO_ROOT / "jztz" / "token.conf",
    _REPO_ROOT / "data" / "lixinger_token.conf",
]

# dashboard name -> (area, stockCode, pe_metric, pb_metric)
INDEX_CONFIG: dict[str, dict[str, str]] = {
    "沪深300": {"area": "cn", "code": "000300", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "中证500": {"area": "cn", "code": "000905", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "上证50": {"area": "cn", "code": "000016", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "创业板指": {"area": "cn", "code": "399006", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "科创50": {"area": "cn", "code": "000688", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "中证红利": {"area": "cn", "code": "000922", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "红利低波": {"area": "cn", "code": "H30269", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "红利低波100": {"area": "cn", "code": "930955", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "中证白酒": {"area": "cn", "code": "399997", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "中证医疗": {"area": "cn", "code": "399989", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "中证传媒": {"area": "cn", "code": "399971", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "证券公司": {"area": "cn", "code": "399975", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "中证银行": {"area": "cn", "code": "399986", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "中证环保": {"area": "cn", "code": "000827", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "全指消费": {"area": "cn", "code": "000990", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "全指医药": {"area": "cn", "code": "000991", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "全指金融": {"area": "cn", "code": "000992", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "全指信息": {"area": "cn", "code": "000993", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "养老产业": {"area": "cn", "code": "399812", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "A股全指": {"area": "cn", "code": "000985", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "中概互联": {"area": "cn", "code": "H11136", "pe": "pe_ttm.ewpvo", "pb": "pb.ewpvo"},
    "恒生指数": {"area": "hk", "code": "HSI", "pe": "pe_ttm.mcw", "pb": "pb.mcw"},
    "恒生科技": {"area": "hk", "code": "HSTECH", "pe": "pe_ttm.mcw", "pb": "pb.mcw"},
}

# 红利类 PE 失真，温度只用 PB
PB_ONLY_INDICES = {"中证红利", "红利低波", "红利低波100", "中证银行"}
# 本地序列尽量拉 20 年；指数成立不足 20 年时以实际可取最长历史为准。
# 分位默认仍用近 5 年（首页温度计），详情页按 UI 窗口切。
HISTORY_YEARS = 20
PERCENTILE_YEARS = 5
REQUEST_GAP = 0.35
# 理杏仁对超长区间可能 403，分块拉取
FETCH_CHUNK_DAYS = 730
# series files can be reused across many page views; network refresh is daily/cron only
SERIES_TTL = 24 * 3600
# homepage must NOT re-hit 理杏仁 every open — serve snapshot forever until forced refresh
SNAPSHOT_TTL = 7 * 24 * 3600
# hard stop: web paths pass allow_network=False
DEFAULT_ALLOW_NETWORK = False


def _load_token() -> str:
    env = (os.environ.get("LIXINGER_TOKEN") or "").strip()
    if env:
        return env
    for path in TOKEN_CANDIDATES:
        if path.exists():
            token = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            if token:
                return token
    raise RuntimeError(
        "未找到理杏仁 token，请写入 jztz/token.conf 或 data/lixinger_token.conf，"
        "或设置环境变量 LIXINGER_TOKEN"
    )


def _post(url: str, body: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 etf-tool/lixinger",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    # 直连即可；代理环境变量有时反而干扰
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def _series_path(code: str) -> Path:
    return CACHE_DIR / f"{code}.json"


def _normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if "T" in text:
        text = text.split("T", 1)[0]
    return text[:10] if len(text) >= 10 else None


def _percentile(values: list[float], current: float | None) -> float | None:
    if current is None or not values:
        return None
    # 当前值在历史中的分位：小于等于 current 的比例
    below = sum(1 for v in values if v <= current)
    return round(below / len(values), 4)


def _valuation_action(
    pe_pct: float | None, pb_pct: float | None, *, pb_only: bool, stale: bool
) -> tuple[str, str]:
    if stale:
        return "pause", "数据过期，暂停自动买卖判断"
    values = [v for v in ((pb_pct,) if pb_only else (pe_pct, pb_pct)) if v is not None]
    if not values:
        return "watch", "估值字段不足，先观察"
    score = sum(values) / len(values)
    if score <= 0.18:
        return "buy", "分位处于深度低估区"
    if score <= 0.30:
        return "buy", "分位处于低估区"
    if score >= 0.88:
        return "reduce", "分位处于高估区"
    if score >= 0.68:
        return "hold", "估值偏高，控制加仓"
    return "watch", "估值中性，等待更好赔率"


def fetch_index_series(
    name: str,
    force: bool = False,
    years: int = HISTORY_YEARS,
    allow_network: bool | None = None,
) -> list[dict[str, Any]]:
    """Load index PE/PB/CP series.

    Web/dashboard path should use allow_network=False so page views never burn 理杏仁 token.
    Network refresh is reserved for cron / explicit force.
    """
    if name not in INDEX_CONFIG:
        raise KeyError(f"未知指数: {name}")
    if allow_network is None:
        allow_network = DEFAULT_ALLOW_NETWORK if not force else True
    if force:
        allow_network = True

    cfg = INDEX_CONFIG[name]
    code = cfg["code"]
    area = cfg["area"]
    pe_key = cfg["pe"]
    pb_key = cfg["pb"]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _series_path(code)

    cached_rows: list[dict[str, Any]] = []
    fetched_ts = 0.0
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached_rows = list(payload.get("rows") or [])
            fetched_ts = float(payload.get("fetched_ts") or 0)
        except Exception:
            cached_rows = []
            fetched_ts = 0.0

    today = date.today()
    start = (today - timedelta(days=int(years * 365.25) + 30)).isoformat()
    end = today.isoformat()

    # Prefer local cache: never network unless explicitly allowed/forced
    if cached_rows and not force:
        max_date = max((_normalize_date(r.get("date")) or "" for r in cached_rows), default="")
        fresh_enough = bool(fetched_ts) and time.time() - fetched_ts < SERIES_TTL and max_date >= (
            today - timedelta(days=3)
        ).isoformat()
        if not allow_network or fresh_enough:
            return sorted(cached_rows, key=lambda r: r.get("date") or "")

    if not allow_network:
        # cache-only mode: return whatever we have, never call API
        return sorted(cached_rows, key=lambda r: r.get("date") or "")

    # incremental network fetch
    if cached_rows and not force:
        last_date = max((_normalize_date(r.get("date")) or "" for r in cached_rows), default="")
        if last_date:
            try:
                d0 = datetime.strptime(last_date, "%Y-%m-%d").date() - timedelta(days=5)
                start = d0.isoformat()
            except Exception:
                pass

    token = _load_token()
    url = f"https://open.lixinger.com/api/{area}/index/fundamental"
    # chunked fetch — long ranges may 403
    try:
        start_d = datetime.strptime(start, "%Y-%m-%d").date()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
    except Exception:
        start_d = today - timedelta(days=int(years * 365.25) + 30)
        end_d = today

    new_rows: list[dict[str, Any]] = []
    cur = start_d
    last_err: Exception | None = None
    while cur <= end_d:
        chunk_end = min(cur + timedelta(days=FETCH_CHUNK_DAYS), end_d)
        body = {
            "token": token,
            "startDate": cur.isoformat(),
            "endDate": chunk_end.isoformat(),
            "stockCodes": [code],
            "metricsList": [pe_key, pb_key, "cp"],
        }
        time.sleep(REQUEST_GAP)
        try:
            res = _post(url, body)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                raw = exc.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                detail = raw.decode("utf-8", "replace")[:300]
            except Exception:
                pass
            last_err = RuntimeError(f"理杏仁请求失败 {code}: HTTP {exc.code} {detail}")
            # shrink chunk and retry once
            if chunk_end > cur + timedelta(days=120):
                chunk_end = cur + timedelta(days=365)
                body["endDate"] = chunk_end.isoformat()
                time.sleep(REQUEST_GAP)
                try:
                    res = _post(url, body)
                except Exception as exc2:
                    last_err = exc2  # type: ignore[assignment]
                    cur = chunk_end + timedelta(days=1)
                    continue
            else:
                cur = chunk_end + timedelta(days=1)
                continue

        if res.get("code") != 1:
            last_err = RuntimeError(f"理杏仁返回失败 {code}: {res}")
            cur = chunk_end + timedelta(days=1)
            continue

        for item in res.get("data") or []:
            d = _normalize_date(item.get("date"))
            if not d:
                continue
            pe = item.get(pe_key)
            pb = item.get(pb_key)
            cp = item.get("cp")
            try:
                pe = float(pe) if pe not in (None, "") else None
            except Exception:
                pe = None
            try:
                pb = float(pb) if pb not in (None, "") else None
            except Exception:
                pb = None
            try:
                cp = float(cp) if cp not in (None, "") else None
            except Exception:
                cp = None
            new_rows.append({"date": d, "pe": pe, "pb": pb, "cp": cp, "code": code, "name": name})
        cur = chunk_end + timedelta(days=1)

    if not new_rows and not cached_rows:
        if last_err:
            raise last_err
        raise RuntimeError(f"理杏仁无数据 {code}")

    by_date: dict[str, dict[str, Any]] = {}
    for row in cached_rows + new_rows:
        d = _normalize_date(row.get("date"))
        if not d:
            continue
        by_date[d] = {
            "date": d,
            "pe": row.get("pe"),
            "pb": row.get("pb"),
            "cp": row.get("cp"),
            "code": code,
            "name": name,
        }
    rows = [by_date[k] for k in sorted(by_date.keys())]
    # 只保留 years 窗口（默认 12 年，覆盖长赢买卖点）
    cutoff = (today - timedelta(days=int(years * 365.25))).isoformat()
    rows = [r for r in rows if (r.get("date") or "") >= cutoff]

    path.write_text(
        json.dumps(
            {
                "name": name,
                "code": code,
                "area": area,
                "fetched_ts": time.time(),
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "rows": rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return rows


def get_index_detail(
    name: str,
    window_years: float = 20.0,
    allow_network: bool = False,
) -> dict[str, Any]:
    # keep long series for marks; percentile window follows UI window
    rows = fetch_index_series(
        name,
        years=max(int(window_years) + 1, int(HISTORY_YEARS)),
        allow_network=allow_network,
    )
    if not rows:
        return {
            "name": name,
            "rows": [],
            "latest": None,
            "cache_only": not allow_network,
            "data_mode": "cache_only" if not allow_network else "live",
            "available_years": 0,
            "available_start": None,
            "available_end": None,
        }
    full_start = rows[0].get("date")
    full_end = rows[-1].get("date")
    available_years = None
    try:
        d0 = datetime.strptime(str(full_start)[:10], "%Y-%m-%d").date()
        d1 = datetime.strptime(str(full_end)[:10], "%Y-%m-%d").date()
        available_years = round((d1 - d0).days / 365.25, 2)
    except Exception:
        available_years = None

    # window_years > available → use full actual history
    cutoff = (date.today() - timedelta(days=int(float(window_years) * 365.25))).isoformat()
    window = [r for r in rows if (r.get("date") or "") >= cutoff] or rows
    # percentile hist uses same UI window (user selected 3/5/10/20y, capped by actual)
    pe_hist = [float(r["pe"]) for r in window if r.get("pe") is not None]
    pb_hist = [float(r["pb"]) for r in window if r.get("pb") is not None]
    latest = window[-1]
    pe = latest.get("pe")
    pb = latest.get("pb")
    return {
        "name": name,
        "code": INDEX_CONFIG[name]["code"],
        "pb_only": name in PB_ONLY_INDICES,
        "window_years": window_years,
        "requested_window_years": window_years,
        "available_years": available_years,
        "available_start": full_start,
        "available_end": full_end,
        "effective_start": window[0].get("date") if window else None,
        "effective_end": window[-1].get("date") if window else None,
        "cache_only": not allow_network,
        "data_mode": "cache_only" if not allow_network else "live",
        "latest": {
            **latest,
            "pe_percentile": _percentile(pe_hist, pe if pe is None else float(pe)),
            "pb_percentile": _percentile(pb_hist, pb if pb is None else float(pb)),
        },
        "rows": window,
    }


def build_valuation_snapshot(
    names: list[str] | None = None,
    force: bool = False,
    allow_network: bool | None = None,
) -> dict[str, Any]:
    if allow_network is None:
        allow_network = True if force else DEFAULT_ALLOW_NETWORK
    if force:
        allow_network = True

    # Always prefer snapshot file for web: no network rebuild on page open
    if not force and SNAPSHOT_CACHE.exists():
        try:
            cached = json.loads(SNAPSHOT_CACHE.read_text(encoding="utf-8"))
            snap = cached.get("snapshot") or {}
            if snap.get("rows"):
                age = time.time() - float(cached.get("fetched_ts") or 0)
                snap = dict(snap)
                snap["cache_age_seconds"] = int(age)
                snap["cache_only"] = True
                snap.setdefault("freshness", {})
                snap["freshness"] = dict(snap.get("freshness") or {})
                snap["freshness"]["served_from"] = "local_snapshot"
                snap["freshness"]["cache_age_seconds"] = int(age)
                # even if TTL expired, still serve cache when network disallowed
                if age < SNAPSHOT_TTL or not allow_network:
                    return snap
        except Exception:
            pass

    if not allow_network:
        # last-resort empty/stale cache already handled; try one more read
        if SNAPSHOT_CACHE.exists():
            try:
                cached = json.loads(SNAPSHOT_CACHE.read_text(encoding="utf-8"))
                snap = cached.get("snapshot") or {}
                if snap.get("rows"):
                    snap = dict(snap)
                    snap["cache_only"] = True
                    snap.setdefault("freshness", {})["served_from"] = "stale_local_snapshot"
                    return snap
            except Exception:
                pass
        return {
            "freshness": {
                "warning": "本地估值缓存缺失，请运行日更脚本刷新（不在网页路径调用理杏仁）",
                "source": "lixinger_cache_missing",
            },
            "total": 0,
            "counts": {},
            "top": {"buy": [], "watch": [], "hold": [], "reduce": [], "pause": []},
            "rows": [],
            "data_source": "lixinger_cache_missing",
            "cache_only": True,
        }

    targets = names or list(INDEX_CONFIG.keys())
    rows: list[dict[str, Any]] = []
    dates: list[str] = []
    errors: list[str] = []
    for name in targets:
        try:
            series = fetch_index_series(name, force=force, allow_network=True)
            if not series:
                errors.append(f"{name}: empty")
                continue
            # 分位用近 PERCENTILE_YEARS（默认5年）；序列本身可更长
            cutoff = (date.today() - timedelta(days=int(PERCENTILE_YEARS * 365.25))).isoformat()
            window = [r for r in series if (r.get("date") or "") >= cutoff] or series
            latest = window[-1]
            pe = latest.get("pe")
            pb = latest.get("pb")
            pe_hist = [float(r["pe"]) for r in window if r.get("pe") is not None]
            pb_hist = [float(r["pb"]) for r in window if r.get("pb") is not None]
            pe_pct = _percentile(pe_hist, float(pe) if pe is not None else None)
            pb_pct = _percentile(pb_hist, float(pb) if pb is not None else None)
            pb_only = name in PB_ONLY_INDICES
            pe_pct_use = None if pb_only else pe_pct
            snap_date = latest.get("date")
            if snap_date:
                dates.append(snap_date)
            stale = False
            if snap_date:
                try:
                    stale = datetime.strptime(snap_date, "%Y-%m-%d").date() < (
                        date.today() - timedelta(days=3)
                    )
                except Exception:
                    stale = False
            action, reason = _valuation_action(pe_pct_use, pb_pct, pb_only=pb_only, stale=stale)
            vals = [v for v in ((pb_pct,) if pb_only else (pe_pct_use, pb_pct)) if v is not None]
            temperature = round(sum(vals) / len(vals) * 100) if vals else None
            rows.append(
                {
                    "name": name,
                    "code": INDEX_CONFIG[name]["code"],
                    "pe": pe,
                    "pe_percentile": pe_pct_use,
                    "pb": pb,
                    "pb_percentile": pb_pct,
                    "temperature": temperature,
                    "action": action,
                    "reason": reason + ("（PB优先）" if pb_only else ""),
                    "snapshot_date": snap_date,
                    "pb_only": pb_only,
                    "cp": latest.get("cp"),
                    "source": "lixinger",
                    "history_points": len(window),
                }
            )
        except Exception as exc:
            errors.append(f"{name}: {exc}")

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
    cn_names = {n for n, c in INDEX_CONFIG.items() if c["area"] == "cn"}
    hk_names = {n for n, c in INDEX_CONFIG.items() if c["area"] == "hk"}
    cn_dates = [r["snapshot_date"] for r in rows if r["name"] in cn_names and r.get("snapshot_date")]
    hk_dates = [r["snapshot_date"] for r in rows if r["name"] in hk_names and r.get("snapshot_date")]
    warning = None
    if not rows:
        warning = "理杏仁估值为空: " + ("; ".join(errors[:3]) if errors else "无数据")
    elif max_date and max_date < (date.today() - timedelta(days=3)).isoformat():
        warning = f"理杏仁最新数据日 {max_date} 超过3天，仅供参考"

    freshness = {
        "snapshot_date": max_date,
        "snapshot_mtime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_min_date": min_date,
        "market_max_date": max_date,
        "cn_min_date": min(cn_dates) if cn_dates else None,
        "cn_max_date": max(cn_dates) if cn_dates else None,
        "cn_aligned": bool(cn_dates) and len(set(cn_dates)) == 1,
        "hk_max_date": max(hk_dates) if hk_dates else None,
        "overseas_max_date": max(hk_dates) if hk_dates else None,
        "index_count": len(rows),
        "warning": warning,
        "source": "lixinger",
        "served_from": "live_refresh",
        "note": "估值来自理杏仁 Open API，本地计算五年分位；红利类按 PB-only。网页默认只读缓存。",
        "errors": errors[:8],
    }
    groups = {k: [] for k in ("buy", "watch", "hold", "reduce", "pause")}
    for row in rows:
        groups.setdefault(row["action"], []).append(row)
    snapshot = {
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
        "data_source": "lixinger",
        "fetched_at": freshness["snapshot_mtime"],
        "cache_only": False,
    }
    SNAPSHOT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_CACHE.write_text(
        json.dumps(
            {"fetched_ts": time.time(), "snapshot": snapshot},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return snapshot


if __name__ == "__main__":
    snap = build_valuation_snapshot(force=True)
    print("rows", snap.get("total"), "date", (snap.get("freshness") or {}).get("snapshot_date"))
    print(json.dumps({
        "total": snap["total"],
        "source": snap["data_source"],
        "freshness": snap["freshness"],
        "sample": snap["rows"][:3],
    }, ensure_ascii=False, indent=2))
