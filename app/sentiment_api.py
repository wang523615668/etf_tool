#!/usr/bin/env python3
"""情绪面板数据 API:
  /api/sentiment  →  基金行业仓位(季度) + 两融余额(月) + 北向(月) + 医药/科技拥挤度标注
数据源(静态): data/ts_fund_industry.json / ts_margin.json / ts_hsgt.json
"""
import json, os
from collections import defaultdict
from fastapi import APIRouter
BASE = "/vol1/1000/openzl/etf_tool"

router = APIRouter()

def _load(fp):
    try: return json.load(open(f"{BASE}/data/{fp}"))
    except Exception: return None

@router.get("/api/sentiment")
def sentiment():
    fi = _load("ts_fund_industry.json") or {"quarters": {}}
    # 两融: 按月聚合(沪深合计)
    m = _load("ts_margin.json") or []
    rz = defaultdict(float)
    for r in m:
        try:
            rz[r["trade_date"][:6]] += float(r.get("rzrqye") or 0)
        except (TypeError, ValueError, KeyError):
            pass
    rz_m = [{"m": f"{k[:4]}-{k[4:]}", "v": round(v / 1e12, 3)} for k, v in sorted(rz.items())]
    # 北向: 持股市值(亿元), 按月
    h = _load("ts_hsgt.json") or []
    nm = {}
    for r in h:
        try:
            d = str(r.get("trade_date")); v = float(r.get("north_money"))
            if v: nm[d[:6]] = v / 1e4   # 万元→亿
        except (TypeError, ValueError): pass
    hgt_m = [{"m": f"{k[:4]}-{k[4:]}", "v": round(v, 0)} for k, v in sorted(nm.items())]
    qs = sorted(fi["quarters"])
    cur = fi["quarters"].get(qs[-1]) if qs else None
    prev = fi["quarters"].get(qs[-2]) if len(qs) > 1 else None
    if cur: cur = dict(cur); cur["q"] = qs[-1]
    if prev: prev = dict(prev); prev["q"] = qs[-2]
    return {
        "updated": fi.get("updated"),
        "fund": {
            "quarters": [fi["quarters"][q] | {"q": q} for q in qs],
            "current": cur, "prev": prev,
            "summary": fi.get("summary"),
        },
        "margin_yi": rz_m,
        "north_yi": hgt_m,
    }
