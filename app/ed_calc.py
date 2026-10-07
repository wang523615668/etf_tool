#!/usr/bin/env python3
# ed_calc.py — 自算估值查询引擎（供 /self-calc 页面）
# 数据全部本地：
#   序列   data/self_daily/{code}.json  (rows=[{date,pe,pb,n}], 逐指数最优口径, 对E大表标定)
#   逐股   data/ed_hist_full/{date}.json (5282个交易日 2005-01-04→今, {code:[PE_TTM,PE_LAR,PB_MRQ,市值]})
#   口径   data/ed_perindex_best.json → ed_algo.load_config()
#   名单   与 ed_algo.pool 完全同逻辑（本模块仅额外保留股票代码，供成分股明细表）
# 股票名称：腾讯 qt.gtimg.cn 批量实时查 + 磁盘缓存 data/stock_names.json（全离线页面也秒开）
from __future__ import annotations

import bisect
import datetime as dt
import json
import os
import re
import threading
import urllib.request
from statistics import median, mean

try:  # 直接脚本运行(app 目录在 path)
    import ed_algo
except ImportError:  # 由 app.main (uvicorn app.main:app) 引入
    from app import ed_algo

BASE = "/vol1/1000/openzl/etf_tool"
HIST = f"{BASE}/data/ed_hist_full"
SERIES_DIR = f"{BASE}/data/self_daily"
NAMES_FP = f"{BASE}/data/stock_names.json"

_dates: list[str] | None = None
_snap_cache: dict[str, dict] = {}
_snap_order: list[str] = []
_series_cache: dict[str, dict] = {}
_cfg: dict | None = None
_names: dict[str, str] | None = None
_lock = threading.Lock()


def _load_names() -> dict:
    global _names
    if _names is None:
        try:
            _names = json.load(open(NAMES_FP, encoding="utf-8"))
        except Exception:
            _names = {}
    return _names


def _save_names(d: dict) -> None:
    try:
        tmp = NAMES_FP + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, NAMES_FP)
    except Exception:
        pass


def _tencent_prefix(code: str) -> str:
    if code.startswith(("60", "68")):
        return "sh"
    if code.startswith(("00", "30")):
        return "sz"
    return "bj"


def stock_names(codes: list[str]) -> dict[str, str]:
    """{code: 名称}，缺失的批量查腾讯并落盘；查不到回退为代码本身。"""
    names = _load_names()
    miss = [c for c in codes if c not in names]
    if miss:
        got: dict[str, str] = {}
        url = "https://qt.gtimg.cn/q=" + ",".join(_tencent_prefix(c) + c for c in miss[:240])
        try:
            raw = urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=8
            ).read().decode("gbk", "ignore")
            for m in re.finditer(r'v_(?:sh|sz|bj)(\d{6})="([^"]*)"', raw):
                parts = m.group(2).split("~")
                if len(parts) > 1 and parts[1]:
                    got[m.group(1)] = parts[1]
        except Exception:
            pass
        names.update(got)
        for c in miss:
            names.setdefault(c, got.get(c, c))
        with _lock:
            _save_names(names)
    return {c: names.get(c, c) for c in codes}


def trade_dates() -> list[str]:
    global _dates
    if _dates is None:
        _dates = sorted(f[:10] for f in os.listdir(HIST) if f.endswith(".json"))
    return _dates


def nearest_trade_day(date: str) -> str:
    ds = trade_dates()
    i = bisect.bisect_right(ds, date) - 1
    return ds[max(i, 0)]


def load_snap(date: str) -> dict:
    """{code: {PE_TTM,PE_LAR,PB_MRQ,TOTAL_MARKET_CAP}}，进程内 LRU(4)"""
    if date in _snap_cache:
        return _snap_cache[date]
    raw = json.load(open(f"{HIST}/{date}.json", encoding="utf-8"))
    snap = {c: {"PE_TTM": v[0], "PE_LAR": v[1], "PB_MRQ": v[2], "TOTAL_MARKET_CAP": v[3]}
            for c, v in raw.items()}
    _snap_cache[date] = snap
    _snap_order.append(date)
    while len(_snap_order) > 4:
        _snap_cache.pop(_snap_order.pop(0), None)
    return snap


def cfg() -> dict:
    global _cfg
    if _cfg is None:
        _cfg = ed_algo.load_config()
    return _cfg


def load_series(code: str) -> dict | None:
    if code in _series_cache:
        return _series_cache[code]
    fp = f"{SERIES_DIR}/{code}.json"
    if ".bak" in code or not os.path.isfile(fp):
        return None
    j = json.load(open(fp, encoding="utf-8"))
    if j.get("degenerate"):
        return None
    _series_cache[code] = j
    return j


def series_index() -> list[dict]:
    out = []
    for fn in sorted(os.listdir(SERIES_DIR)):
        if not fn.endswith(".json") or ".bak" in fn:
            continue
        j = load_series(fn[:-5])
        if not j or not j.get("rows"):
            continue
        rows = j["rows"]
        last = rows[-1]
        c = cfg().get(j.get("name"), {})
        out.append({
            "code": j["code"], "name": j["name"],
            "date": last["date"], "pe": last.get("pe"), "pb": last.get("pb"), "n": last.get("n"),
            "algo": j.get("algo") or c.get("algo"), "list": j.get("list_mode") or c.get("list"),
            "note": j.get("note"), "first": rows[0]["date"], "days": len(rows),
            "ed_dev_med": j.get("ed_dev_med"), "ed_anch_med": j.get("ed_anch_med"),
        })
    broad = {"上证50", "上证180", "沪深300", "中证500", "中证1000", "中证A500", "中证A50", "中证A100",
             "创业板指", "创业板50", "创业板综", "科创50", "科创100", "中证红利", "上证红利", "深证红利"}
    for x in out:
        x["group"] = "全市场" if x["code"].startswith("MKT") else ("宽基/风格" if x["name"] in broad else "行业/主题")
    order = {"全市场": 0, "宽基/风格": 1, "行业/主题": 2}
    out.sort(key=lambda x: (order[x["group"]], x["name"] or ""))
    return out


def percentile(rows: list[dict], val, years: float, upto: str):
    """分位 = 年限窗口内(截至 upto，不含未来数据) ≤ val 的比例。返回 (分位, 样本天数)"""
    if not val:
        return None, 0
    d0 = (dt.date.fromisoformat(upto) - dt.timedelta(days=round(365.25 * years))).isoformat()
    w = sorted(r["pe"] for r in rows if d0 <= r["date"] <= upto and r.get("pe"))
    if len(w) < 30:
        return None, len(w)
    return round(sum(1 for x in w if x <= val) / len(w) * 100, 1), len(w)


def pb_percentile(rows: list[dict], val, years: float, upto: str):
    if not val:
        return None, 0
    d0 = (dt.date.fromisoformat(upto) - dt.timedelta(days=round(365.25 * years))).isoformat()
    w = sorted(r["pb"] for r in rows if d0 <= r["date"] <= upto and r.get("pb"))
    if len(w) < 30:
        return None, len(w)
    return round(sum(1 for x in w if x <= val) / len(w) * 100, 1), len(w)


# ---------- 成分股明细（与 ed_algo.pool 同名单逻辑，额外保留代码） ----------
def _pool_codes(name: str, snap: dict, ctx, mode: str) -> list[str]:
    if name in ("全市场", "全市场·主板(E大口径)"):
        return [c for c in snap if c.startswith(("60", "00"))]
    if name in ("全市场加权", "全市场·沪深京"):
        return list(snap)
    if mode == "mktcap" and name in ed_algo.MKRULE:
        prefs, lo, hi = ed_algo.MKRULE[name]
        cand = sorted(((c, r) for c, r in snap.items()
                       if c.startswith(prefs) and r.get("TOTAL_MARKET_CAP")),
                      key=lambda x: -x[1]["TOTAL_MARKET_CAP"])
        return [c for c, _ in cand[lo:hi]]
    if mode == "lixinger":
        j = (ed_algo._lix().get(name) or {}).get("asof") or {}
        ds = [d for d in j if d <= ctx.date]
        if not ds:
            return []
        return [c for c in j[max(ds)] if c in snap]
    if name == "创业板综":
        return [c for c in snap if c.startswith("30")]
    idx_code = ctx.cur_map.get(name)
    codes = ctx.current.get(name)
    if mode == "bspit" and name in ed_algo.BSKEY:
        months = [m for m in ctx.bspit if ctx.bspit[m].get(ed_algo.BSKEY[name]) and m <= ctx.date]
        if not months:
            return []
        return [c.split(".")[-1] for c in ctx.bspit[max(months)][ed_algo.BSKEY[name]]
                if c.split(".")[-1] in snap]
    if not codes:
        return []
    if mode == "pit":
        m = ctx.sina_pit.get(idx_code or "")
        if not m:
            return []
        codes = [c for c in codes if c in m and m[c] <= ctx.date]
    return [c for c in codes if c in snap]


def detail(code: str, date: str, sort: str = "pe", years: float = 5.0):
    d = load_series(code)
    if not d:
        return {"error": f"无序列 {code}"}
    date = nearest_trade_day(date)
    name = d["name"]
    c = cfg().get(name) or {}
    algo = c.get("algo") or "中位TTM剔亏"
    mode = c.get("list", "current")
    snap = load_snap(date)
    ctx = ed_algo.build_ctx(date, code_names={name: d["code"], **ed_algo.INDEX_CODE})
    codes = _pool_codes(name, snap, ctx, mode)
    if not codes:
        hint = ""
        try:
            j = (ed_algo._lix().get(name) or {}).get("asof") or {}
            if j:
                hint = f"（该指数名单库始于 {min(j)}）"
        except Exception:
            pass
        return {"error": f"该期成分名单缺失{hint}，可改用全市场口径查看逐股明细", "date": date, "name": name}
    names = stock_names(codes)
    fn = ed_algo.algo_fn(algo) or ed_algo.ALGOS["中位TTM剔亏"]
    rowsdata = [{"code": c, **snap[c]} for c in codes]
    agg_pe = agg_pb = None
    try:
        agg_pe = fn(rowsdata)
        agg_pb = ed_algo.pb_fn(rowsdata)
    except Exception:
        pass
    # 序列里当日的官方行(与日更管线一致，优先用它)
    srow = next((r for r in reversed(d["rows"]) if r["date"] <= date), None)
    pe_val, pb_val = (srow or {}).get("pe"), (srow or {}).get("pb")
    items = []
    for c in codes:
        r = snap[c]
        pt, pl, pb, mc = r.get("PE_TTM"), r.get("PE_LAR"), r.get("PB_MRQ"), r.get("TOTAL_MARKET_CAP")
        items.append({
            "code": c, "name": names.get(c, c),
            "pe": round(pt, 2) if pt else pt, "pe_lar": round(pl, 2) if pl else pl,
            "pb": round(pb, 3) if pb else pb,
            "mktcap_yi": round(mc / 1e8, 1) if mc else None,
            "loss": not (pt and pt > 0),
        })
    # 权重列：市值缺失的老快照(2018年前)整列 None
    total_mc = sum(i["mktcap_yi"] for i in items if i["mktcap_yi"]) or None
    if total_mc:
        for i in items:
            i["weight_pct"] = round(i["mktcap_yi"] / total_mc * 100, 2) if i["mktcap_yi"] else None
    else:
        for i in items:
            i["weight_pct"] = None

    sort_note = ""
    if sort == "mktcap" and not total_mc:
        sort_note = "该日期尚无成分市值数据(逐股市值库始于2018-01)，已自动按代码排序"
        sort = "code"
    key = {"pe": lambda x: (x["pe"] is None or x["pe"] <= 0, x["pe"] or 9e9),
           "pe_desc": lambda x: (-(x["pe"] or -9e9)),
           "pb": lambda x: (x["pb"] is None or x["pb"] <= 0, x["pb"] or 9e9),
           "mktcap": lambda x: (-(x["mktcap_yi"] or 0)),
           "code": lambda x: x["code"]}.get(sort, lambda x: x["code"])
    items.sort(key=key)
    n_pe = sum(1 for i in items if i["pe"] and i["pe"] > 0)
    n_pb = sum(1 for i in items if i["pb"] and i["pb"] > 0)
    pos_pe = sorted(i["pe"] for i in items if i["pe"] and i["pe"] > 0)
    pos_pb = sorted(i["pb"] for i in items if i["pb"] and i["pb"] > 0)
    pe_pct, _ = percentile(d["rows"], pe_val, years, date)
    pb_pct, _ = pb_percentile(d["rows"], pb_val, years, date)
    return {
        "code": d["code"], "name": name, "date": date,
        "algo": algo, "algo_cn": ed_algo.ALGO_CN.get(algo, algo),
        "list_mode": mode, "list_cn": ed_algo.MODE_CN.get(mode, mode),
        "pit": bool(d.get("pit")),
        "total": len(items), "n_pe": n_pe, "n_pb": n_pb, "n_loss": len(items) - n_pe,
        "pe": pe_val, "pb": pb_val, "pe_pct": pe_pct, "pb_pct": pb_pct, "years": years,
        "seq_date": (srow or {}).get("date"),
        "median_pe": round(median(pos_pe), 2) if pos_pe else None,
        "mean_pe": round(mean(pos_pe), 2) if pos_pe else None,
        "median_pb": round(median(pos_pb), 3) if pos_pb else None,
        "agg_pe_check": round(agg_pe, 3) if agg_pe else None,
        "seq_pe": (srow or {}).get("pe"),
        "sort_note": sort_note,
        "items": items,
    }


def history(code: str, years: float = 5.0, upto: str | None = None, points: int = 260):
    """序列片段(降采样) + 当前值分位 + 历史高低"""
    d = load_series(code)
    if not d:
        return {"error": f"无序列 {code}"}
    rows = d["rows"]
    end = upto or rows[-1]["date"]
    end = nearest_trade_day(min(end, trade_dates()[-1]))
    d0 = (dt.date.fromisoformat(end) - dt.timedelta(days=round(365.25 * years))).isoformat()
    w = [r for r in rows if d0 <= r["date"] <= end]
    if not w:
        return {"error": "窗口内无数据", "code": code, "end": end}
    cur = w[-1]
    pe_pct, n1 = percentile(rows, cur.get("pe"), years, end)
    pb_pct, n2 = pb_percentile(rows, cur.get("pb"), years, end)
    pes = [r["pe"] for r in w if r.get("pe")]
    pbs = [r["pb"] for r in w if r.get("pb")]
    step = max(1, len(w) // points)
    pts = [{"d": r["date"], "pe": r.get("pe"), "pb": r.get("pb")} for r in w[::step]]
    if w and pts and pts[-1]["d"] != w[-1]["date"]:
        pts.append({"d": w[-1]["date"], "pe": w[-1].get("pe"), "pb": w[-1].get("pb")})
    return {
        "code": d["code"], "name": d["name"], "years": years, "start": w[0]["date"], "end": cur["date"],
        "pe": cur.get("pe"), "pb": cur.get("pb"), "n": cur.get("n"),
        "pe_pct": pe_pct, "pb_pct": pb_pct, "win_days": len(w),
        "pe_min": round(min(pes), 2) if pes else None, "pe_max": round(max(pes), 2) if pes else None,
        "pe_avg": round(sum(pes) / len(pes), 2) if pes else None,
        "pb_min": round(min(pbs), 3) if pbs else None, "pb_max": round(max(pbs), 3) if pbs else None,
        "algo": d.get("algo"), "list_mode": d.get("list_mode"),
        "note": d.get("note"),
        "series": pts,
    }
