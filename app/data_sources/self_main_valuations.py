# 自算主列表估值源(source=self)
# 用 data/self_daily/*.json 的自算序列(E大表标定, 留出组中位误差 1.56%)覆盖
# 理杏仁快照的 pe/pb/分位/动作; 保留原行其余字段(cp/tech_signals 等)保证下游面板兼容。
# 序列由 cron 17:30 日更(self_daily_update.py), 本模块只读文件, 零网络。
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
SERIES_DIR = _REPO_ROOT / "data" / "self_daily"

# 自算序列名 → 主列表行名(理杏仁行的 name 体系); 不同的才映射
NAME_ALIAS = {"全市场·主板(E大口径)": "全市场", "全市场·沪深京": "全市场加权"}

# E大标定中位误差(留出组), 展示用
CALIB_NOTE = "自算引擎·E大估值表标定·留出组中位误差1.56%"


def _load_series() -> dict[str, dict[str, Any]]:
    out = {}
    if not SERIES_DIR.is_dir():
        return out
    for fp in sorted(SERIES_DIR.glob("*.json")):
        if ".bak" in fp.name:
            continue
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = d.get("name")
        rows = d.get("rows") or []
        if not name or not rows:
            continue
        out[NAME_ALIAS.get(name, name)] = d
    return out


def _p5y(rows: list[dict], pe: float | None, latest_date: str) -> float | None:
    if pe is None:
        return None
    d0 = (datetime.date.fromisoformat(latest_date) - datetime.timedelta(days=int(365.25 * 5))).isoformat()
    w = sorted(r["pe"] for r in rows if r["date"] >= d0 and r.get("pe"))
    if len(w) < 50:
        return None
    return round(sum(1 for x in w if x <= pe) / len(w), 4)


def _p5y_pb(rows: list[dict], pb: float | None, latest_date: str) -> float | None:
    if pb is None:
        return None
    d0 = (datetime.date.fromisoformat(latest_date) - datetime.timedelta(days=int(365.25 * 5))).isoformat()
    w = sorted(r["pb"] for r in rows if r["date"] >= d0 and r.get("pb"))
    if len(w) < 50:
        return None
    return round(sum(1 for x in w if x <= pb) / len(w), 4)


def ed_analysis(rows: list[dict], latest_date: str) -> dict[str, Any]:
    """E大估值表同款分析行(月末口径): 历史高低/五十年均/现÷五十年比值/PE·PB五十年百分位"""
    out: dict[str, Any] = {}
    pes = [r["pe"] for r in rows if r.get("pe")]
    if not pes:
        return out
    out["pe_hist_max"] = round(max(pes), 2)
    out["pe_hist_min"] = round(min(pes), 2)
    me = []
    bym: dict[str, dict] = {}
    for r in rows:
        ym = r["date"][:7]
        if r["date"] <= latest_date and r.get("pe"):
            bym[ym] = r
    me = sorted(bym.values(), key=lambda x: x["date"])
    def _win(years):
        d0 = (datetime.date.fromisoformat(latest_date) - datetime.timedelta(days=int(365.25 * years))).isoformat()
        return [m["pe"] for m in me if m["date"] >= d0]
    def _winpb(years):
        d0 = (datetime.date.fromisoformat(latest_date) - datetime.timedelta(days=int(365.25 * years))).isoformat()
        return [m["pb"] for m in me if m["date"] >= d0 and m.get("pb")]
    cur = (rows[-1].get("pe") if rows[-1]["date"] <= latest_date else None)
    cur = next((r["pe"] for r in reversed(rows) if r["date"] == latest_date), None) or cur
    for yrs, tag in ((5, "5"), (10, "10")):
        w = _win(yrs)
        if len(w) >= 24:
            avg = sum(w) / len(w)
            out[f"pe_avg{tag}y"] = round(avg, 2)
            if cur:
                out[f"pe_vs{tag}y"] = round((cur / avg - 1) * 100, 1)      # 现/五 比值%
                out[f"pe_pct{tag}y"] = round(sum(1 for x in w if x <= cur) / len(w) * 100, 1)  # 百分位%
            wpb = _winpb(yrs)
            if len(wpb) >= 24:
                avgpb = sum(wpb) / len(wpb)
                curpb = next((r["pb"] for r in reversed(rows) if r["date"] == latest_date and r.get("pb")), None)
                if curpb:
                    out[f"pb_vs{tag}y"] = round((curpb / avgpb - 1) * 100, 1)
                    out[f"pb_pct{tag}y"] = round(sum(1 for x in wpb if x <= curpb) / len(wpb) * 100, 1)
    return out


def _action(pe_pct: float | None, pb_pct: float | None, hist_short: bool = False) -> tuple[str, str]:
    values = [v for v in (pe_pct, pb_pct) if v is not None]
    if not values:
        return "watch", "自算历史不足5年，先观察"
    if hist_short:
        return "watch", "自算历史不足5年，分位不可用，先观察"
    score = sum(values) / len(values)
    if score <= 0.25:
        return "buy", "自算分位处于低估区"
    if score >= 0.85:
        return "reduce", "自算分位处于高估区"
    if score >= 0.65:
        return "hold", "自算分位偏高，控制加仓"
    return "watch", "自算分位中性，等待更好赔率"


def apply_self_valuations(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """就地覆盖: 行名命中自算序列 → pe/pb/分位/动作换成自算值。返回 (rows, 覆盖数)"""
    series = _load_series()
    if not series:
        return rows, 0
    covered = 0
    for i, r in enumerate(rows):
        name = r.get("name") or ""
        d = series.get(name)
        if not d:
            continue
        latest = (d.get("rows") or [])[-1]
        pe, pb = latest.get("pe"), latest.get("pb")
        date = latest.get("date") or ""
        rows_all = d.get("rows") or []
        # 5年分位窗口至少 1200 个交易日(~5年)才可信；不足(如A500只有2年) → 分位置空
        enough = len(rows_all) >= 1200
        pe_pct = _p5y(rows_all, pe, date) if enough else None
        pb_pct = _p5y_pb(rows_all, pb, date) if enough else None
        action, reason = _action(pe_pct, pb_pct, hist_short=not enough)
        rr = dict(r)
        rr.update({
            "pe": pe, "pb": pb,
            "pe_percentile": pe_pct, "pb_percentile": pb_pct,
            "temperature": round(pe_pct * 100) if pe_pct is not None else None,
            "action": action, "reason": reason,
            "snapshot_date": date, "source": "self", "self_engine": True,
            "self_list_mode": d.get("list_mode"), "self_algo": d.get("algo"),
            "self_days": len(d.get("rows") or []),
            "self_dev": d.get("ed_dev_med"),  # 对E大表的中位偏差%
        })
        rr.update(ed_analysis(rows_all, date))
        rows[i] = rr
        covered += 1
    # 自算独有、理杏仁行里没有的指数(如自建全市场) → 追加
    have = {r.get("name") for r in rows}
    for name, d in series.items():
        if name in have:
            continue
        latest = (d.get("rows") or [])[-1]
        enough = len(d.get("rows") or []) >= 1200
        pe_pct = d.get("p5y") if enough else None
        rr = {
            "name": name, "code": d.get("code") or name,
            "pe": latest.get("pe"), "pb": latest.get("pb"),
            "pe_percentile": round(pe_pct / 100, 4) if pe_pct is not None else None,
            "pb_percentile": None,
            "temperature": round(pe_pct) if pe_pct is not None else None,
            "snapshot_date": latest.get("date"), "source": "self", "self_engine": True,
        }
        rr["action"], rr["reason"] = _action(rr["pe_percentile"], None, hist_short=not enough)
        rr.update(ed_analysis(d.get("rows") or [], latest.get("date") or ""))
        rows.append(rr)
    return rows, covered
