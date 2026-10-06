#!/usr/bin/env python3
"""把 E大文章 / 且慢文章 / wu2198归档 打包进 APP assets。

产出（/vol1/1000/openzl/android-build/FinanceApp/app/src/main/assets/）：
  data_ed_index.js    全量文章索引（标题/URL/分组/日期）→ window.ED_INDEX
  data_ed_body.txt.gz 全量正文（JSON，gzip）→ 前端 DecompressionStream 解压，供搜索+阅读
  data_ed_talks.js    且慢站内 E大文章（qieman items，含正文）→ window.ED_TALKS
  data_wu_archive.js  wu2198 微博永久归档 → window.WU_ARCHIVE
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

ETL = Path("/vol1/1000/openzl/etf_tool/data")
WU = Path("/vol1/1000/openzl/wu2198")
AST = Path("/vol1/1000/openzl/android-build/FinanceApp/app/src/main/assets")
WEB = Path("/vol1/1000/openzl/finance_app/static")
AST.mkdir(parents=True, exist_ok=True)
WEB.mkdir(parents=True, exist_ok=True)


def js_array(name: str, data, compact: bool = True) -> str:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":") if compact else None)
    return f"window.{name}={body};\n"


def build_index() -> int:
    idx = json.loads((ETL / "ed_chinaetfs_full.json").read_text("utf-8"))
    out = [{"t": x.get("t", ""), "u": x.get("u", ""), "g": x.get("g", ""), "d": x.get("d", "")}
           for x in idx if x.get("t") and x.get("u")]
    (AST / "data_ed_index.js").write_text(js_array("ED_INDEX", out), "utf-8")
    (WEB / "data_ed_index.js").write_text(js_array("ED_INDEX", out), "utf-8")
    return len(out)


def build_body() -> tuple[int, int]:
    """正文分块成 JS 文件（不用 fetch+gz：APK 是 file:// 加载，fetch 会被拦；
    script 标签加载 file:// 资源是安全的，且 http 下同样可用）。"""
    body = json.loads((ETL / "ed_articles_body.json").read_text("utf-8"))
    slim = sorted((u, v.get("text", "")) for u, v in body.items() if v.get("text"))
    for d in (AST, WEB):
        for old in d.glob("data_ed_body_p*.js"):
            old.unlink()
    CHUNK = 1_400_000  # 每块原始字节上限
    chunks, cur, cursz = [], {}, 0
    for u, t in slim:
        entry = json.dumps({u: t}, ensure_ascii=False, separators=(",", ":"))[1:-1]
        if cursz and cursz + len(entry) > CHUNK:
            chunks.append(cur)
            cur, cursz = {}, 0
        cur[u] = t
        cursz += len(entry) + 1
    if cur:
        chunks.append(cur)
    total = 0
    for i, ch in enumerate(chunks, 1):
        name = f"data_ed_body_p{i:02d}.js"
        js = f"window.__edbAdd&&window.__edbAdd({i},{json.dumps(ch, ensure_ascii=False, separators=(',', ':'))});\n"
        total += len(js.encode("utf-8"))
        for d in (AST, WEB):
            (d / name).write_text(js, "utf-8")
    meta = {"n": len(chunks), "bytes": total}
    for d in (AST, WEB):
        (d / "data_ed_body_meta.js").write_text(f"window.ED_BODY_META={json.dumps(meta)};\n", "utf-8")
        gz = d / "data_ed_body.txt.gz"
        if gz.exists():
            gz.unlink()
    return len(slim), total


def build_talks() -> int:
    """且慢站内 E大文章（含正文），合并进索引一起展示。"""
    src = ETL / "ed_talks.json"
    if not src.exists():
        return 0
    d = json.loads(src.read_text("utf-8"))
    out = []
    for x in d.get("items") or []:
        txt = (x.get("text_full") or x.get("text_excerpt") or "").strip()
        if not txt:
            continue
        out.append({
            "t": x.get("title", ""),
            "u": x.get("url") or x.get("content_url") or "",
            "g": "且慢",
            "d": x.get("date", ""),
            "text": txt,
        })
    (AST / "data_ed_talks.js").write_text(js_array("ED_TALKS", out), "utf-8")
    (WEB / "data_ed_talks.js").write_text(js_array("ED_TALKS", out), "utf-8")
    return len(out)


def build_wx_posts() -> int:
    """WU-小明 公众号（徐小明+WU2198 合并帖）→ ED_TALKS 同构数据，分组=微信公众号"""
    src = ETL / "wu_xiaoming.json"
    if not src.exists():
        return 0
    items = json.loads(src.read_text("utf-8"))
    out = []
    for x in items:
        head = []
        if x.get('xm_pos') is not None:
            head.append(f"【小明仓位 {x['xm_pos']:.0%}{'·' + x['xm_op'] if x.get('xm_op') else ''}】")
        if x.get('wu_pos') is not None:
            head.append(f"【WU仓位 {x['wu_pos']:.0%}{'·' + x['wu_op'] if x.get('wu_op') else ''}】")
        text = "\n".join(head + [x.get('wu_text', ''), "小明：" + x.get('xm_text', '')]).strip()
        out.append({"t": x.get("title", ""), "u": x.get("url", ""), "d": x.get("date", ""),
                    "g": "微信公众号", "text": text[:2800]})
    (AST / "data_wx_posts.js").write_text(js_array("WX_POSTS", out), "utf-8")
    (WEB / "data_wx_posts.js").write_text(js_array("WX_POSTS", out), "utf-8")
    return len(out)


def build_wu_archive() -> int:
    src = WU / "archive" / "wu2198_weibo_archive.json"
    if not src.exists():
        return 0
    d = json.loads(src.read_text("utf-8"))
    out = []
    for p in d.get("posts") or []:
        ts = p.get("ts") or 0
        dt = ""
        if ts:
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromtimestamp(ts, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        out.append({"text": p.get("text", ""), "d": dt, "del": bool(p.get("deleted_later"))})
    (AST / "data_wu_archive.js").write_text(js_array("WU_ARCHIVE", out), "utf-8")
    (WEB / "data_wu_archive.js").write_text(js_array("WU_ARCHIVE", out), "utf-8")
    return len(out)


if __name__ == "__main__":
    n1 = build_index()
    n2, gz = build_body()
    n3 = build_talks()
    n4 = build_wu_archive()
    n5 = build_wx_posts()
    print(f"索引 {n1} 篇 | 正文 {n2} 篇 ({gz // 1024}KB gz) | 且慢文章 {n3} 篇 | wu归档 {n4} 条 | 公众号 {n5} 篇")
