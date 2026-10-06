#!/usr/bin/env python3
"""抓取 chinaetfs.cn 文章正文（供 APP 内离线阅读 + 关键词搜索）。

选择器（已实测 /a/2300.html）：
  article.article-content  → 正文 HTML
  .article-meta            → 作者/时间/来源
  .article-tags            → 标签
产出：/vol1/1000/openzl/etf_tool/data/ed_articles_body.json
      {u: {t, d, src, tags, text}}  text 为纯文本正文（搜索用）
可反复运行（增量：已抓过的 URL 跳过）。
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "http://www.chinaetfs.cn"
INDEX = Path("/vol1/1000/openzl/etf_tool/data/ed_chinaetfs_full.json")
OUT = Path("/vol1/1000/openzl/etf_tool/data/ed_articles_body.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def fetch(url: str, timeout: int = 25) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for a in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            for enc in ("utf-8", "gbk", "gb18030"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", "ignore")
        except Exception:
            if a == 2:
                return None
            time.sleep(1.0 * (a + 1))
    return None


def clean_text(html: str) -> str:
    t = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def parse(url: str, html: str) -> dict | None:
    if not html:
        return None
    m = re.search(r"<article[^>]*class=\"[^\"]*article-content[^\"]*\"[^>]*>([\s\S]*?)</article>", html, re.I)
    body = m.group(1) if m else ""
    if not body:
        m = re.search(r"<div[^>]*class=\"[^\"]*article-content[^\"]*\"[^>]*>([\s\S]*?)</div>\s*</div>", html, re.I)
        body = m.group(1) if m else ""
    text = clean_text(body)
    if len(text) < 30:
        return None

    # 作者/时间/来源
    src, author = "", ""
    mm = re.search(r"class=\"[^\"]*article-meta[^\"]*\"[^>]*>([\s\S]{0,600}?)</div>", html, re.I)
    if mm:
        meta_txt = clean_text(mm.group(1))
        for k in ("雪球", "微信", "网易", "且慢", "博客", "ETF拯救世界"):
            if k in meta_txt:
                src = k
                break
        a = re.search(r"作者[：:]\s*([^\s<]{2,20})", meta_txt)
        if a:
            author = a.group(1)
    tags = re.findall(r"class=\"[^\"]*article-tags[^\"]*\"[^>]*>([\s\S]{0,400}?)</div>", html, re.I)
    tag_list = re.findall(r">([^<>]{2,12})</a>", tags[0]) if tags else []

    # 日期
    d = ""
    for pat in (r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", r"(20\d{2})[-/年](\d{1,2})"):
        m2 = re.search(pat, html)
        if m2:
            g = m2.groups()
            d = f"{g[0]}-{int(g[1]):02d}" + (f"-{int(g[2]):02d}" if len(g) > 2 else "")
            break

    return {"text": text, "src": src, "author": author, "tags": tag_list[:6], "d": d}


def main() -> int:
    idx = json.loads(INDEX.read_text("utf-8"))
    urls = [x["u"] for x in idx]
    meta = {x["u"]: x for x in idx}
    print(f"[index] {len(urls)} 篇", flush=True)

    store: dict[str, dict] = {}
    if OUT.exists():
        try:
            store = json.loads(OUT.read_text("utf-8"))
        except Exception:
            store = {}
    todo = [u for u in urls if u not in store]
    print(f"[todo] 待抓正文 {len(todo)} 篇（已有 {len(store)}）", flush=True)

    cnt = [0]

    def work(u: str):
        h = fetch(u)
        rec = parse(u, h) if h else None
        if rec:
            rec["t"] = meta.get(u, {}).get("t", "")
            rec["g"] = meta.get(u, {}).get("g", "")
            store[u] = rec
            cnt[0] += 1
            if cnt[0] % 100 == 0:
                print(f"  … {cnt[0]}/{len(todo)}", flush=True)
                try:
                    OUT.write_text(json.dumps(store, ensure_ascii=False), "utf-8")
                except Exception:
                    pass

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(work, todo))

    OUT.write_text(json.dumps(store, ensure_ascii=False), "utf-8")
    total_txt = sum(len(v.get("text") or "") for v in store.values())
    print(f"[done] {len(store)} 篇正文, 合计 {total_txt // 1024}KB → {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
