"""理杏仁 Open API 估值数据源。

Token 读取顺序（支持多 token 自动轮换）：
1. 环境变量 LIXINGER_TOKENS（逗号/换行分隔）或 LIXINGER_TOKEN
2. <repo>/data/lixinger_tokens.conf（每行一个）
3. <repo>/jztz/token.conf / data/lixinger_token.conf（单行兼容）
额度/鉴权失败（HTTP 401/403 或业务 code 提示 token）时自动切换下一个。

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
    _REPO_ROOT / "data" / "lixinger_tokens.conf",
    _REPO_ROOT / "jztz" / "token.conf",
    _REPO_ROOT / "data" / "lixinger_token.conf",
]
TOKEN_STATE_PATH = _REPO_ROOT / "data" / "lixinger_token_state.json"

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


def _split_tokens(raw: str) -> list[str]:
    parts: list[str] = []
    for chunk in raw.replace(";", ",").replace("\n", ",").split(","):
        t = chunk.strip()
        if t and t not in parts:
            parts.append(t)
    return parts


def _load_token_pool() -> list[str]:
    """Return ordered unique tokens from env + conf files."""
    tokens: list[str] = []
    env_multi = (os.environ.get("LIXINGER_TOKENS") or "").strip()
    if env_multi:
        tokens.extend(_split_tokens(env_multi))
    env = (os.environ.get("LIXINGER_TOKEN") or "").strip()
    if env:
        for t in _split_tokens(env):
            if t not in tokens:
                tokens.append(t)
    for path in TOKEN_CANDIDATES:
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for t in _split_tokens(raw):
            if t not in tokens:
                tokens.append(t)
    if not tokens:
        raise RuntimeError(
            "未找到理杏仁 token，请写入 data/lixinger_tokens.conf（每行一个）"
            "或设置 LIXINGER_TOKENS / LIXINGER_TOKEN"
        )
    return tokens


def _load_token_state() -> dict[str, Any]:
    if TOKEN_STATE_PATH.exists():
        try:
            return json.loads(TOKEN_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"active_index": 0, "failed": {}, "updated_at": None}


def _save_token_state(state: dict[str, Any]) -> None:
    TOKEN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    TOKEN_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        TOKEN_STATE_PATH.chmod(0o600)
    except Exception:
        pass


def _load_token() -> str:
    """Compat: return current active token."""
    return _select_token()


def _select_token(exclude: set[str] | None = None) -> str:
    pool = _load_token_pool()
    exclude = exclude or set()
    state = _load_token_state()
    idx = int(state.get("active_index") or 0) % len(pool)
    # prefer active, then rotate through pool skipping exclude/failed-hard
    failed = state.get("failed") or {}
    order = list(range(idx, len(pool))) + list(range(0, idx))
    for i in order:
        tok = pool[i]
        if tok in exclude:
            continue
        # soft-skip tokens marked exhausted today
        meta = failed.get(tok[:8]) or failed.get(tok) or {}
        if meta.get("exhausted") and meta.get("day") == date.today().isoformat():
            continue
        if i != idx:
            state["active_index"] = i
            _save_token_state(state)
        return tok
    # all excluded/exhausted — still return first non-excluded
    for i in order:
        tok = pool[i]
        if tok not in exclude:
            state["active_index"] = i
            _save_token_state(state)
            return tok
    return pool[idx]


def _mark_token_failure(token: str, reason: str, *, exhausted: bool = False) -> None:
    pool = _load_token_pool()
    state = _load_token_state()
    failed = dict(state.get("failed") or {})
    key = token[:8]
    failed[key] = {
        "reason": reason[:200],
        "day": date.today().isoformat(),
        "exhausted": bool(exhausted),
        "ts": time.time(),
    }
    state["failed"] = failed
    # advance active index to next different token
    try:
        cur = pool.index(token)
    except ValueError:
        cur = int(state.get("active_index") or 0)
    if len(pool) > 1:
        state["active_index"] = (cur + 1) % len(pool)
    _save_token_state(state)


def _is_token_error(http_code: int | None, detail: str, res: dict[str, Any] | None = None) -> bool:
    if http_code in (401, 403, 429):
        return True
    blob = (detail or "").lower()
    if any(k in blob for k in ("token", "quota", "limit", "额度", "权限", "unauthorized", "forbidden", "too many")):
        return True
    if res is not None:
        code = res.get("code")
        msg = str(res.get("message") or res.get("msg") or "").lower()
        if code not in (1, None) and any(k in msg for k in ("token", "quota", "limit", "额度", "权限")):
            return True
    return False


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



def _rolling_mean_series(
    rows: list[dict[str, Any]],
    field: str,
    *,
    window_days: int = 365 * 5,
    min_points: int = 200,
) -> list[float | None]:
    """Trailing calendar-window mean for field; None until min_points filled."""
    n = len(rows)
    means: list[float | None] = [None] * n
    if n == 0:
        return means
    dates: list[date] = []
    for r in rows:
        try:
            dates.append(datetime.strptime(str(r.get("date") or "")[:10], "%Y-%m-%d").date())
        except Exception:
            dates.append(date.min)
    s = 0.0
    cnt = 0
    j = 0
    for i in range(n):
        v = rows[i].get(field)
        try:
            fv = float(v) if v is not None else None
        except (TypeError, ValueError):
            fv = None
        if fv is not None and fv > 0:
            s += fv
            cnt += 1
        cutoff = dates[i] - timedelta(days=int(window_days))
        while j <= i and dates[j] < cutoff:
            vj = rows[j].get(field)
            try:
                fj = float(vj) if vj is not None else None
            except (TypeError, ValueError):
                fj = None
            if fj is not None and fj > 0:
                s -= fj
                cnt -= 1
            j += 1
        if cnt >= min_points:
            means[i] = s / cnt
    return means


def _dev_pct(value: float | None, mean: float | None) -> float | None:
    if value is None or mean is None or mean <= 0:
        return None
    try:
        return (float(value) - float(mean)) / float(mean)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _metric_mean_dev_profile(
    rows: list[dict[str, Any]],
    field: str,
    *,
    mean_years: float = 5.0,
    min_points: int = 200,
) -> dict[str, Any] | None:
    """Per-index (val-mean)/mean vs own history: current + neg/pos extremes + interest."""
    if not rows:
        return None
    window_days = int(float(mean_years) * 365.25)
    means = _rolling_mean_series(rows, field, window_days=window_days, min_points=min_points)
    samples: list[tuple[str, float, float, float]] = []  # date, value, mean, dev
    for i, r in enumerate(rows):
        m = means[i]
        v = r.get(field)
        try:
            fv = float(v) if v is not None else None
        except (TypeError, ValueError):
            fv = None
        d = _dev_pct(fv, m)
        if d is None or fv is None or m is None:
            continue
        samples.append((str(r.get("date") or "")[:10], fv, float(m), float(d)))
    if len(samples) < max(min_points // 2, 50):
        return None

    # current = last sample with valid dev
    cur_date, cur_val, cur_mean, cur_dev = samples[-1]
    # extremes on this index alone
    min_i = min(range(len(samples)), key=lambda k: samples[k][3])
    max_i = max(range(len(samples)), key=lambda k: samples[k][3])
    hist_min = samples[min_i][3]
    hist_max = samples[max_i][3]
    sorted_devs = sorted(s[3] for s in samples)

    def _q(q: float) -> float:
        if not sorted_devs:
            return 0.0
        idx = min(len(sorted_devs) - 1, max(0, int(round(q * (len(sorted_devs) - 1)))))
        return sorted_devs[idx]

    p10, p25, p50, p75, p90 = _q(0.10), _q(0.25), _q(0.50), _q(0.75), _q(0.90)
    span = hist_max - hist_min
    if span > 1e-9:
        # 0 = at historical neg extreme (cheapest vs own 5y-mean history)
        # 1 = at historical pos extreme
        range_pos = (cur_dev - hist_min) / span
    else:
        range_pos = 0.5
    range_pos = max(0.0, min(1.0, float(range_pos)))
    interest_score = round((1.0 - range_pos) * 100)  # higher = closer to own bottom extreme

    if range_pos <= 0.12:
        level, note = "极低区", "接近本指数历史负偏离极值，参考重仓/加速兴趣区"
    elif range_pos <= 0.30:
        level, note = "偏低区", "低于本指数常见偏离，布局兴趣区"
    elif range_pos <= 0.70:
        level, note = "中性区", "在本指数历史偏离带中部"
    elif range_pos <= 0.88:
        level, note = "偏高区", "高于本指数常见偏离，控制加仓"
    else:
        level, note = "极高区", "接近本指数历史正偏离极值，参考减仓/谨慎区"

    return {
        "field": field,
        "mean_years": mean_years,
        "sample_count": len(samples),
        "current_date": cur_date,
        "value": round(cur_val, 4),
        "mean": round(cur_mean, 4),
        "dev": round(cur_dev, 4),
        "dev_pct": round(cur_dev * 100, 2),
        "hist_min": round(hist_min, 4),
        "hist_min_pct": round(hist_min * 100, 2),
        "hist_min_date": samples[min_i][0],
        "hist_min_value": round(samples[min_i][1], 4),
        "hist_max": round(hist_max, 4),
        "hist_max_pct": round(hist_max * 100, 2),
        "hist_max_date": samples[max_i][0],
        "hist_max_value": round(samples[max_i][1], 4),
        "p10": round(p10, 4),
        "p25": round(p25, 4),
        "p50": round(p50, 4),
        "p75": round(p75, 4),
        "p90": round(p90, 4),
        "range_pos": round(range_pos, 4),
        "interest_score": interest_score,
        "interest_level": level,
        "interest_note": note,
    }


def compute_mean_deviation_interest(
    rows: list[dict[str, Any]],
    *,
    mean_years: float = 5.0,
    pb_only: bool = False,
    min_points: int = 200,
) -> dict[str, Any] | None:
    """Per-index 5y-mean deviation extremes as reference interest zones.

    Formula: dev = (valuation - trailing_mean) / trailing_mean
    Interest is relative to THIS index's own historical min/max, not cross-index -55%.
    """
    pe_prof = None if pb_only else _metric_mean_dev_profile(
        rows, "pe", mean_years=mean_years, min_points=min_points
    )
    pb_prof = _metric_mean_dev_profile(
        rows, "pb", mean_years=mean_years, min_points=min_points
    )
    if not pe_prof and not pb_prof:
        return None

    # Combined interest: average of available metric scores (higher = cheaper vs own extremes)
    scores = []
    if pe_prof:
        scores.append(pe_prof["interest_score"])
    if pb_prof:
        scores.append(pb_prof["interest_score"])
    combined_score = round(sum(scores) / len(scores)) if scores else None

    # Combined level from averaged range_pos
    positions = []
    if pe_prof:
        positions.append(pe_prof["range_pos"])
    if pb_prof:
        positions.append(pb_prof["range_pos"])
    avg_pos = sum(positions) / len(positions) if positions else 0.5
    if avg_pos <= 0.12:
        level, note = "极低区", "相对本指数历史偏离极值偏底，参考重仓兴趣"
    elif avg_pos <= 0.30:
        level, note = "偏低区", "相对本指数历史偏离偏低，布局兴趣"
    elif avg_pos <= 0.70:
        level, note = "中性区", "相对本指数历史偏离中性"
    elif avg_pos <= 0.88:
        level, note = "偏高区", "相对本指数历史偏离偏高"
    else:
        level, note = "极高区", "相对本指数历史偏离极值偏顶，参考减仓"

    return {
        "mean_years": mean_years,
        "formula": "(估值 - 本指数N年滚动均值) / 均值；兴趣区=相对本指数历史正/负偏离极值",
        "pb_only": pb_only,
        "pe": pe_prof,
        "pb": pb_prof,
        "interest_score": combined_score,
        "interest_level": level,
        "interest_note": note,
        "range_pos": round(avg_pos, 4),
        # compact for list cards
        "pe_dev_pct": None if not pe_prof else pe_prof["dev_pct"],
        "pb_dev_pct": None if not pb_prof else pb_prof["dev_pct"],
        "pe_hist_min_pct": None if not pe_prof else pe_prof["hist_min_pct"],
        "pe_hist_max_pct": None if not pe_prof else pe_prof["hist_max_pct"],
        "pb_hist_min_pct": None if not pb_prof else pb_prof["hist_min_pct"],
        "pb_hist_max_pct": None if not pb_prof else pb_prof["hist_max_pct"],
    }


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


def _comprehensive_signal(
    *,
    val_action: str,
    val_reason: str,
    double_avg_buy: bool,
    double_avg_buy_note: str | None,
    tech_signals: list[str],
    mean_dev_level: str | None,
    temperature: int | None,
    pb_only: bool,
) -> tuple[str, str]:
    """Merge valuation 分位 + 双均线 + 850线 + 偏离兴趣区 → unified signal.

    Returns (action, detailed_reason) where action extends the base valuation
    with up/down arrows indicating technical confirmation/divergence.
    """
    parts = [val_reason]
    action = val_action
    arrows = []

    # 双均线 buy reinforcement
    if double_avg_buy and val_action in ("buy", "watch"):
        arrows.append("↓双均线确认")
        parts.append(f"📉 {double_avg_buy_note}")
    elif not double_avg_buy and val_action == "buy":
        arrows.append("↗估值但双均线未确认")

    # 850线趋势 direction
    tech_bearish = any("低于" in t for t in tech_signals)
    tech_bullish = any("高于" in t for t in tech_signals)
    tech_significant = any("显著" in t for t in tech_signals)
    tech_close = any("接近" in t for t in tech_signals)

    if tech_significant and val_action == "buy":
        # 显著弱势+低估 → 左侧机会但需右侧确认
        arrows.append("⚠850弱势")
        parts.append("💡 850线显著偏弱，左侧机会但建议等待右侧确认信号")
    elif tech_bearish and val_action == "buy":
        arrows.append("↓850弱势")
        parts.append("💡 点位在850线下方，左侧布局区间")
    elif tech_bullish and val_action == "buy":
        arrows.append("↗趋势偏强")
        parts.append("✅ 850线上方趋势偏强，右侧确认")
    elif tech_bearish and val_action in ("hold", "watch"):
        arrows.append("↓850弱势")
        parts.append("💡 850线下偏弱，等待企稳")

    # 偏离兴趣区
    if mean_dev_level and ("极低区" in mean_dev_level or "偏低区" in mean_dev_level):
        if val_action == "buy":
            arrows.append("↓兴趣区")
            parts.append(f"📊 {mean_dev_level}，相对本指数历史偏低")

    if val_action == "buy" and len(arrows) >= 2:
        action = "buy"
    elif val_action == "watch" and double_avg_buy:
        action = "buy"

    full_reason = " · ".join(p for p in parts if p)
    if arrows:
        return action, f"{''.join(arrows)}: {full_reason}"
    return action, full_reason


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

    token = _select_token()
    url = f"https://open.lixinger.com/api/{area}/index/fundamental"
    # chunked fetch — long ranges may 403; token errors rotate pool
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
        res: dict[str, Any] | None = None
        tried: set[str] = set()
        while True:
            body["token"] = token
            try:
                res = _post(url, body)
                if res.get("code") != 1 and _is_token_error(None, str(res), res):
                    tried.add(token)
                    _mark_token_failure(token, f"biz {res}", exhausted=True)
                    nxt = _select_token(exclude=tried)
                    if nxt in tried or nxt == token:
                        last_err = RuntimeError(f"理杏仁返回失败 {code}: {res}")
                        res = None
                        break
                    token = nxt
                    time.sleep(REQUEST_GAP)
                    continue
                break
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
                if _is_token_error(exc.code, detail, None):
                    tried.add(token)
                    _mark_token_failure(
                        token,
                        f"HTTP {exc.code} {detail}",
                        exhausted=exc.code in (401, 403, 429),
                    )
                    nxt = _select_token(exclude=tried)
                    if nxt not in tried and nxt != token:
                        token = nxt
                        time.sleep(REQUEST_GAP)
                        continue
                # shrink chunk and retry once with current token
                if chunk_end > cur + timedelta(days=120):
                    chunk_end = cur + timedelta(days=365)
                    body["endDate"] = chunk_end.isoformat()
                    time.sleep(REQUEST_GAP)
                    try:
                        res = _post(url, body)
                        break
                    except Exception as exc2:
                        last_err = exc2  # type: ignore[assignment]
                        res = None
                        break
                else:
                    res = None
                    break
            except Exception as exc:
                last_err = exc  # type: ignore[assignment]
                res = None
                break

        if not res:
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
    pb_only = name in PB_ONLY_INDICES
    # Full local series for per-index mean extremes + rolling mean lines on chart
    mean_dev = compute_mean_deviation_interest(rows, mean_years=5.0, pb_only=pb_only)

    # Rolling PE/PB means (calendar-based: 3y ≈ 1095d, 5y ≈ 1826d)
    pe_ma3 = _rolling_mean_series(rows, "pe", window_days=int(3 * 365.25), min_points=120)
    pe_ma5 = _rolling_mean_series(rows, "pe", window_days=int(5 * 365.25), min_points=200)
    pb_ma3 = _rolling_mean_series(rows, "pb", window_days=int(3 * 365.25), min_points=120)
    pb_ma5 = _rolling_mean_series(rows, "pb", window_days=int(5 * 365.25), min_points=200)

    # 850 MA (点位): 850 trading days ≈ 3.4 calendar years, use ~3.4 * 365
    # E大用850日均线作为长期趋势支撑/压力线
    cp_ma850 = _rolling_mean_series(rows, "cp", window_days=int(3.4 * 365.25), min_points=400)
    # Also compute 250 MA (年线) for mid-term
    cp_ma250 = _rolling_mean_series(rows, "cp", window_days=int(1.0 * 365.25), min_points=120)

    # index full rows by date for window join
    full_by_date: dict[str, dict[str, Any]] = {}
    for i, r in enumerate(rows):
        d = str(r.get("date") or "")[:10]
        if not d:
            continue
        full_by_date[d] = {
            "pe_ma3y": None if pe_ma3[i] is None else round(float(pe_ma3[i]), 4),
            "pe_ma5y": None if pe_ma5[i] is None else round(float(pe_ma5[i]), 4),
            "pb_ma3y": None if pb_ma3[i] is None else round(float(pb_ma3[i]), 4),
            "pb_ma5y": None if pb_ma5[i] is None else round(float(pb_ma5[i]), 4),
            "cp_ma250": None if cp_ma250[i] is None else round(float(cp_ma250[i]), 2),
            "cp_ma850": None if cp_ma850[i] is None else round(float(cp_ma850[i]), 2),
        }
    window_out: list[dict[str, Any]] = []
    for r in window:
        rr = dict(r)
        d = str(r.get("date") or "")[:10]
        extra = full_by_date.get(d) or {}
        rr.update(extra)
        window_out.append(rr)
    latest = window_out[-1] if window_out else (window[-1] if window else {})
    pe = latest.get("pe")
    pb = latest.get("pb")

    # 双均线买入信号
    # E大核心规则: 当前PE低于3年均值 AND 低于5年均值 → 低估确认
    # PB-only指数用PB替代PE
    pe_curr = float(pe) if pe is not None else None
    pb_curr = float(pb) if pb is not None else None
    signal_metric = pb_curr if pb_only else pe_curr
    ma3_val = latest.get("pb_ma3y" if pb_only else "pe_ma3y")
    ma5_val = latest.get("pb_ma5y" if pb_only else "pe_ma5y")
    double_avg_buy = False
    double_avg_buy_note = None
    if signal_metric is not None and ma3_val is not None and ma5_val is not None:
        if signal_metric < ma3_val and signal_metric < ma5_val:
            double_avg_buy = True
            metric_name = "PB" if pb_only else "PE"
            double_avg_buy_note = (
                f"{metric_name}={round(signal_metric,2)} < MA3y={round(ma3_val,2)} "
                f"且 < MA5y={round(ma5_val,2)}，双均线确认低估"
            )

    # 850线技术位判断
    cp_curr = latest.get("cp")
    cp850_curr = latest.get("cp_ma850")
    cp250_curr = latest.get("cp_ma250")
    tech_signals = []
    if cp_curr is not None and cp850_curr is not None:
        cp_f = float(cp_curr)
        cp850_f = float(cp850_curr)
        if cp_f < cp850_f * 0.95:
            tech_signals.append(f"点位低于850线{round(100*(1-cp_f/cp850_f),1)}%，显著弱势")
        elif cp_f < cp850_f:
            tech_signals.append("点位在850线下方，偏弱")
        elif cp_f < cp850_f * 1.05:
            tech_signals.append("点位接近850线，方向待确认")
        else:
            tech_signals.append(f"点位高于850线{round(100*(cp_f/cp850_f-1),1)}%，趋势偏强")
    if cp_curr is not None and cp250_curr is not None:
        cp_f = float(cp_curr)
        cp250_f = float(cp250_curr)
        if cp_f < cp250_f:
            tech_signals.append("点位低于250年线")

    # Compute basic score for comprehensive signal
    pe_pct_use = None if pb_only else _percentile(pe_hist, float(pe) if pe is not None else None)
    pb_pct_use = _percentile(pb_hist, float(pb) if pb is not None else None)
    val_action, val_reason = _valuation_action(pe_pct_use, pb_pct_use, pb_only=pb_only, stale=False)
    vals_for_temp = [v for v in ((pb_pct_use,) if pb_only else (pe_pct_use, pb_pct_use)) if v is not None]
    temperature = round(sum(vals_for_temp) / len(vals_for_temp) * 100) if vals_for_temp else None
    comp_action, comp_reason = _comprehensive_signal(
        val_action=val_action, val_reason=val_reason,
        double_avg_buy=double_avg_buy, double_avg_buy_note=double_avg_buy_note,
        tech_signals=tech_signals,
        mean_dev_level=None if not mean_dev else mean_dev.get("interest_level"),
        temperature=temperature, pb_only=pb_only,
    )

    return {
        "name": name,
        "code": INDEX_CONFIG[name]["code"],
        "pb_only": pb_only,
        "window_years": window_years,
        "requested_window_years": window_years,
        "available_years": available_years,
        "available_start": full_start,
        "available_end": full_end,
        "effective_start": window_out[0].get("date") if window_out else None,
        "effective_end": window_out[-1].get("date") if window_out else None,
        "cache_only": not allow_network,
        "data_mode": "cache_only" if not allow_network else "live",
        "latest": {
            **latest,
            "pe_percentile": _percentile(pe_hist, pe if pe is None else float(pe)),
            "pb_percentile": _percentile(pb_hist, pb if pb is None else float(pb)),
            "double_avg_buy": double_avg_buy,
            "double_avg_buy_note": double_avg_buy_note,
            "tech_signals": tech_signals,
            "comp_action": comp_action,
            "comp_reason": comp_reason,
        },
        "mean_deviation": mean_dev,
        "rows": window_out,
    }



def _enrich_snapshot_mean_dev(snap: dict[str, Any]) -> dict[str, Any]:
    """Attach per-index 5y mean-deviation interest onto snapshot rows (cache-only safe)."""
    rows = list(snap.get("rows") or [])
    if not rows:
        return snap
    changed = False
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        if r.get("mean_deviation") is not None and r.get("mean_dev_interest_score") is not None:
            out_rows.append(r)
            continue
        name = r.get("name")
        if not name or name not in INDEX_CONFIG:
            out_rows.append(r)
            continue
        try:
            series = fetch_index_series(name, allow_network=False)
            pb_only = bool(r.get("pb_only")) or name in PB_ONLY_INDICES
            mean_dev = compute_mean_deviation_interest(
                series, mean_years=5.0, pb_only=pb_only
            )
        except Exception:
            mean_dev = None
        r["mean_deviation"] = mean_dev
        r["mean_dev_interest_score"] = None if not mean_dev else mean_dev.get("interest_score")
        r["mean_dev_interest_level"] = None if not mean_dev else mean_dev.get("interest_level")
        r["pe_dev_pct"] = None if not mean_dev else mean_dev.get("pe_dev_pct")
        r["pb_dev_pct"] = None if not mean_dev else mean_dev.get("pb_dev_pct")
        r["pe_hist_min_pct"] = None if not mean_dev else mean_dev.get("pe_hist_min_pct")
        r["pe_hist_max_pct"] = None if not mean_dev else mean_dev.get("pe_hist_max_pct")
        r["pb_hist_min_pct"] = None if not mean_dev else mean_dev.get("pb_hist_min_pct")
        r["pb_hist_max_pct"] = None if not mean_dev else mean_dev.get("pb_hist_max_pct")
        changed = True
        out_rows.append(r)
    if not changed:
        return snap
    snap = dict(snap)
    snap["rows"] = out_rows
    # refresh top slices so boards see enriched fields
    top: dict[str, list] = {"buy": [], "watch": [], "hold": [], "reduce": [], "pause": []}
    for r in out_rows:
        a = r.get("action") or "watch"
        if a in top and len(top[a]) < 8:
            top[a].append(r)
    snap["top"] = top
    return snap



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
                    return _enrich_snapshot_mean_dev(snap)
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
                    return _enrich_snapshot_mean_dev(snap)
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
            # Later compute comprehensive signal after double_avg_buy/tech_signals are available
            vals = [v for v in ((pb_pct,) if pb_only else (pe_pct_use, pb_pct)) if v is not None]
            temperature = round(sum(vals) / len(vals) * 100) if vals else None
            mean_dev = compute_mean_deviation_interest(
                series, mean_years=5.0, pb_only=pb_only
            )

            # Compute latest-point MA values for double-avg signal + 850 line
            pe_ma3_latest = None
            pe_ma5_latest = None
            pb_ma3_latest = None
            pb_ma5_latest = None
            cp_ma250_latest = None
            cp_ma850_latest = None
            try:
                if len(series) >= 120:
                    _pe_ma3 = _rolling_mean_series(series, "pe", window_days=int(3 * 365.25), min_points=120)
                    _pe_ma5 = _rolling_mean_series(series, "pe", window_days=int(5 * 365.25), min_points=200)
                    _pb_ma3 = _rolling_mean_series(series, "pb", window_days=int(3 * 365.25), min_points=120)
                    _pb_ma5 = _rolling_mean_series(series, "pb", window_days=int(5 * 365.25), min_points=200)
                    _cp_ma250 = _rolling_mean_series(series, "cp", window_days=int(1.0 * 365.25), min_points=120)
                    _cp_ma850 = _rolling_mean_series(series, "cp", window_days=int(3.4 * 365.25), min_points=400)
                    if _pe_ma3 and _pe_ma3[-1] is not None: pe_ma3_latest = round(float(_pe_ma3[-1]), 4)
                    if _pe_ma5 and _pe_ma5[-1] is not None: pe_ma5_latest = round(float(_pe_ma5[-1]), 4)
                    if _pb_ma3 and _pb_ma3[-1] is not None: pb_ma3_latest = round(float(_pb_ma3[-1]), 4)
                    if _pb_ma5 and _pb_ma5[-1] is not None: pb_ma5_latest = round(float(_pb_ma5[-1]), 4)
                    if _cp_ma250 and _cp_ma250[-1] is not None: cp_ma250_latest = round(float(_cp_ma250[-1]), 2)
                    if _cp_ma850 and _cp_ma850[-1] is not None: cp_ma850_latest = round(float(_cp_ma850[-1]), 2)
            except Exception:
                pass

            # 双均线买入信号
            signal_metric = float(pb) if pb is not None and pb_only else (float(pe) if pe is not None else None)
            ma3_val = pb_ma3_latest if pb_only else pe_ma3_latest
            ma5_val = pb_ma5_latest if pb_only else pe_ma5_latest
            double_avg_buy = False
            double_avg_buy_note = None
            if signal_metric is not None and ma3_val is not None and ma5_val is not None:
                if signal_metric < ma3_val and signal_metric < ma5_val:
                    double_avg_buy = True
                    metric_name = "PB" if pb_only else "PE"
                    double_avg_buy_note = (
                        f"{metric_name}={round(signal_metric,2)} < MA3y={round(ma3_val,2)} "
                        f"且 < MA5y={round(ma5_val,2)}，双均线确认低估"
                    )

            # 850线技术位
            tech_signals = []
            cp_curr = latest.get("cp")
            if cp_curr is not None and cp_ma850_latest is not None:
                cp_f = float(cp_curr)
                cp850_f = float(cp_ma850_latest)
                if cp_f < cp850_f * 0.95:
                    tech_signals.append(f"点位低于850线{round(100*(1-cp_f/cp850_f),1)}%，显著弱势")
                elif cp_f < cp850_f:
                    tech_signals.append("点位在850线下方，偏弱")
                elif cp_f < cp850_f * 1.05:
                    tech_signals.append("点位接近850线，方向待确认")
                else:
                    tech_signals.append(f"点位高于850线{round(100*(cp_f/cp850_f-1),1)}%，趋势偏强")
            if cp_curr is not None and cp_ma250_latest is not None:
                cp_f = float(cp_curr)
                cp250_f = float(cp_ma250_latest)
                if cp_f < cp250_f:
                    tech_signals.append("点位低于250年线")

            # 综合信号: 合并估值分位 + 双均线 + 850线 + 兴趣区
            comp_action, comp_reason = _comprehensive_signal(
                val_action=action, val_reason=reason,
                double_avg_buy=double_avg_buy, double_avg_buy_note=double_avg_buy_note,
                tech_signals=tech_signals,
                mean_dev_level=None if not mean_dev else mean_dev.get("interest_level"),
                temperature=temperature, pb_only=pb_only,
            )
            # Override with comprehensive signal when it differs from raw valuation
            if comp_action != action or comp_reason != reason:
                action = comp_action
                reason = comp_reason

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
                    "mean_deviation": mean_dev,
                    "mean_dev_interest_score": None if not mean_dev else mean_dev.get("interest_score"),
                    "mean_dev_interest_level": None if not mean_dev else mean_dev.get("interest_level"),
                    "pe_dev_pct": None if not mean_dev else mean_dev.get("pe_dev_pct"),
                    "pb_dev_pct": None if not mean_dev else mean_dev.get("pb_dev_pct"),
                    "pe_hist_min_pct": None if not mean_dev else mean_dev.get("pe_hist_min_pct"),
                    "pe_hist_max_pct": None if not mean_dev else mean_dev.get("pe_hist_max_pct"),
                    "pb_hist_min_pct": None if not mean_dev else mean_dev.get("pb_hist_min_pct"),
                    "pb_hist_max_pct": None if not mean_dev else mean_dev.get("pb_hist_max_pct"),
                    "double_avg_buy": double_avg_buy,
                    "double_avg_buy_note": double_avg_buy_note,
                    "tech_signals": tech_signals,
                    "pe_ma3y": pe_ma3_latest,
                    "pe_ma5y": pe_ma5_latest,
                    "pb_ma3y": pb_ma3_latest,
                    "pb_ma5y": pb_ma5_latest,
                    "cp_ma250": cp_ma250_latest,
                    "cp_ma850": cp_ma850_latest,
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
