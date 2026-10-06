# 自算兜底估值：蛋卷也没有的指数（全指金融 000992、红利低波100 930955）
# 原理：理杏仁缓存里有 PE/PB 历史序列（ewpvo 口径，截至 07-28/29）。
# PE/PB 与价格线性相关（盈利/净资产短期不变），用今日收盘价 / 缓存日收盘价 缩放到今天：
#   pe_today = pe_last * close_now / cp_last；pb 同理。
# 分位 = 过去 5 年滚动窗口内 ≤ 当前值的比例（与理杏仁快照 default_5y 一致，
# 已用 000300/000992/930955 验证：与理杏仁自发布分位误差 < 0.005）。
# 价格源：
#   000992 → 腾讯 sh000992 实时指数
#   930955 → 无直接行情，用跟踪 ETF 景顺长城红利低波100ETF(515100) 收盘比缩放
from __future__ import annotations

import datetime
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = _REPO_ROOT / "cache" / "lixinger"

SELF_FALLBACK: dict[str, dict[str, str]] = {
    "全指金融": {"cache": "000992", "quote": "sh000992"},
    "红利低波100": {"cache": "930955", "quote": "sh515100"},  # ETF 代理价
}

_UA = {"User-Agent": "Mozilla/5.0"}


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=12) as resp:
        return resp.read()


def _tencent_realtime(symbol: str, attempts: int = 3) -> tuple[float | None, str | None]:
    """返回 (最新价, 日期YYYY-MM-DD)；失败返回 (None, None)。带重试。"""
    for i in range(attempts):
        try:
            text = _http_get(f"https://qt.gtimg.cn/q={symbol}").decode("gbk", "ignore")
            fields = text.split("~")
            if len(fields) > 33 and fields[3]:
                raw_date = fields[30][:8]
                date_str = (
                    f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                    if len(raw_date) == 8 and raw_date.isdigit()
                    else None
                )
                return float(fields[3]), date_str
        except Exception:
            if i < attempts - 1:
                time.sleep(1.0)
    return None, None


def _etf_close_on(symbol: str, date_str: str) -> float | None:
    """ETF 在指定日期的前复权收盘价（用于代理基准对齐）。date_str: YYYY-MM-DD"""
    try:
        start_dt = datetime.date.fromisoformat(date_str) - datetime.timedelta(days=12)
        start = start_dt.strftime("%Y-%m-%d")
        url = (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={symbol},day,{start},{date_str},15,qfq"
        )
        d = json.loads(_http_get(url).decode("utf-8", "ignore"))
        node = (d.get("data") or {}).get(symbol) or {}
        klines = node.get("qfqday") or node.get("day") or []
        for row in klines:  # 取 ≤ 目标日的最近一根（前复权收盘）
            if len(row) > 2 and row[0] <= date_str:
                close = float(row[2])
                if row[0] == date_str:
                    return close
                last_close = close  # 无精确日则用之前最近交易日
        return locals().get("last_close")
    except Exception:
        pass
    return None


def build_self_rows(paused_names: list[str]) -> list[dict[str, Any]]:
    """对指定 pause 指数生成自算行（仅当缓存与实时价都可用）。"""
    rows: list[dict[str, Any]] = []
    for name in paused_names:
        cfg = SELF_FALLBACK.get(name)
        if not cfg:
            continue
        cache_path = CACHE_DIR / f"{cfg['cache']}.json"
        if not cache_path.exists():
            continue
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            hist = data.get("rows") or []
            last = hist[-1]
            cp_last = float(last["cp"])
            price_now, quote_date = _tencent_realtime(cfg["quote"])
            if not price_now or not cp_last or not quote_date:
                continue
            quote_date = quote_date[:10]
            if cfg["quote"].startswith("sh000992"):
                # 直接指数行情：PE/PB 随指数点位等比缩放
                scale = price_now / cp_last
            else:
                # ETF 代理：用 ETF 今收 / ETF 缓存日收盘，再乘指数 PE/PB
                base = _etf_close_on(cfg["quote"], last["date"])
                if not base:
                    continue
                scale = price_now / base
            pe_now = last.get("pe")
            pb_now = last.get("pb")

            def _pct(key: str, cur: float) -> float | None:
                end = datetime.date.fromisoformat(last["date"])
                start = end - datetime.timedelta(days=int(5 * 365.25))
                vals = [
                    r[key]
                    for r in hist
                    if r.get(key) is not None
                    and datetime.date.fromisoformat(r["date"]) >= start
                ]
                if len(vals) < 500:
                    return None
                below = sum(1 for v in vals if v <= cur)
                return round(below / len(vals), 4)

            pe_pct = pb_pct = None
            if pe_now:
                pe_now *= scale
                pe_pct = _pct("pe", pe_now)
            if pb_now:
                pb_now *= scale
                pb_pct = _pct("pb", pb_now)

            values = [v for v in (pe_pct, pb_pct) if v is not None]
            score = sum(values) / len(values) if values else None
            if score is None:
                continue
            if score <= 0.25:
                action, reason = "buy", "PE/PB 分位处于低估区（自算兜底）"
            elif score >= 0.85:
                action, reason = "reduce", "PE/PB 分位处于高估区（自算兜底）"
            elif score >= 0.65:
                action, reason = "hold", "估值偏高，谨慎持有（自算兜底）"
            else:
                action, reason = "watch", "估值中性，继续观察（自算兜底）"

            is_proxy = not cfg["quote"].startswith("sh000992")
            note = (
                "理杏仁历史+实时价格自算（PE/PB 随价格等比缩放）"
                if not is_proxy
                else f"理杏仁历史+实时价自算，以 {cfg['quote'][2:]}ETF 收盘比代理指数点位"
            )
            rows.append(
                {
                    "name": name,
                    "code": cfg["cache"],
                    "pe": round(pe_now, 4) if pe_now else None,
                    "pe_percentile": pe_pct,
                    "pb": round(pb_now, 4) if pb_now else None,
                    "pb_percentile": pb_pct,
                    "temperature": int(round(score * 100)),
                    "action": action,
                    "reason": reason,
                    "snapshot_date": quote_date,
                    "source_override": "self",
                    "orig_reason": "数据过期，暂停自动买卖判断",
                    "note": note,
                }
            )
        except Exception:
            continue
    return rows
