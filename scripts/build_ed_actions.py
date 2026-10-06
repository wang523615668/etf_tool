#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 app 的 E大发车动作列表 data_ed_actions.js（纯脚本，无大模型）
源1: long_win_positions.json 官方发车 actions（150份/S定投）
源2: signals.json 中 source=ed_talks 的发言发车信号（A500这类组合外补入）
去重: (date, 品种名近似) 同键只留一条，官方优先。
"""
import json
import re
from datetime import datetime
from pathlib import Path

ETL = Path("/vol1/1000/openzl/etf_tool")
OUT = Path("/vol1/1000/openzl/finance_app/static/data_ed_actions.js")
PLAN_CN = {"long_win_150": "长赢150份", "long_win_s": "长赢S定投"}
ACT = {"buy": "buy", "sell": "sell", "买入": "buy", "卖出": "sell", "加仓": "buy", "减仓": "sell"}


def norm(n: str) -> str:
    n = re.sub(r"[（(].*?[)）]", "", n or "")
    n = re.sub(r"[A-Z]-[ABC]$", "", n)
    return re.sub(r"(联接|指数增强|ETF|发起|-[ABC]|\s)", "", n)[:10]


rows, seen = [], set()
lw = json.loads((ETL / "data/long_win_positions.json").read_text("utf-8"))
for pk, plan in (lw.get("plans") or {}).items():
    for a in plan.get("actions") or []:
        k = (a.get("date"), norm(a.get("name")))
        if k in seen or not a.get("date"):
            continue
        seen.add(k)
        rows.append({"d": a["date"], "t": ACT.get(a.get("action"), "buy"),
                     "f": a.get("name"), "s": a.get("shares", 1),
                     "p": PLAN_CN.get(pk, pk), "u": a.get("url") or ""})

sg = json.loads((ETL / "data/signals.json").read_text("utf-8"))
for s in sg.get("signals") or []:
    if s.get("source") != "ed_talks" or not s.get("date"):
        continue
    k = (s["date"], norm(s.get("name")))
    if k in seen:
        continue
    seen.add(k)
    rows.append({"d": s["date"], "t": ACT.get(s.get("action"), "buy"),
                 "f": s.get("name"), "s": s.get("shares", 1),
                 "p": "E大发言", "u": s.get("url") or ""})

rows.sort(key=lambda r: r["d"], reverse=True)
# 同时输出 JSON 镜像给 vault_sync 用（APP 在线更新通道）
mirror = Path("/vol1/1000/openzl/etf_tool/data/ed_actions.json")
mirror.write_text(json.dumps(rows, ensure_ascii=False), "utf-8")
meta = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
body = f"window.ED_ACTIONS={json.dumps(rows, ensure_ascii=False)};\n" \
       f"window.ED_ACTIONS_META={json.dumps(meta, ensure_ascii=False)};\n"
tmp = OUT.with_suffix(".tmp")
tmp.write_text(body, "utf-8")
tmp.replace(OUT)
print(f"ED_ACTIONS {len(rows)} 条 → {OUT.name}")
