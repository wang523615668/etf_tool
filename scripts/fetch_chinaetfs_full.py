#!/usr/bin/env python3
"""全量抓取 chinaetfs.cn（E大/ETF拯救世界）文章目录。

数据源：sitemap.xml（4291 篇 /a/N.html）+ /a/N（无后缀）
产出：/vol1/1000/openzl/etf_tool/data/ed_chinaetfs_full.json
      结构 [{t:标题, u:url, g:分组, d:日期}]
可反复运行（增量：已抓过的 URL 跳过，只补新文章）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "http://www.chinaetfs.cn"
OUT = Path("/vol1/1000/openzl/etf_tool/data/ed_chinaetfs_full.json")
CACHE = Path("/vol1/1000/openzl/etf_tool/data/ed_chinaetfs_cache.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def fetch(url: str, timeout: int = 25) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    for attempt in range(3):
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
            if attempt == 2:
                return None
            time.sleep(1.2 * (attempt + 1))
    return None


def sitemap_urls() -> list[str]:
    xml = fetch(f"{BASE}/sitemap.xml", timeout=40) or ""
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    if not urls:
        return []
    arts = []
    for u in urls:
        m = re.search(r"/a/(\d+)(\.html)?$", u)
        if m:
            arts.append(f"{BASE}/a/{m.group(1)}.html")
    # 去重保序
    seen, out = set(), []
    for u in arts:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


TAG_MAP = {
    "雪球": "雪球专栏",
    "微信": "微信公众号",
    "网易": "网易博客",
    "博客": "网易博客",
    "且慢": "且慢",
    "图书": "图书",
    "计划": "ETF计划",
    "发车": "ETF计划",
}


def parse_article(url: str, html: str) -> dict | None:
    if not html:
        return None
    # 标题
    t = ""
    m = re.search(r"<title>\s*(.*?)\s*</title>", html, re.S | re.I)
    if m:
        t = re.sub(r"\s+", " ", m.group(1)).strip()
        # 去掉站点后缀与来源标记（"再见，我喜欢的球球。 2018年7月7日 - E学院" → "再见，我喜欢的球球。"）
        t = re.sub(r"[-–—_|]\s*(?:E学院|E大|ETF拯救世界|chinaetfs\.cn|中国ETF网|雪球|微信公众号|网易博客)\s*$", "", t).strip()
        t = re.sub(r"\s+\d{4}年\d{1,2}月\d{1,2}日\s*$", "", t).strip()
        t = re.sub(r"\s+\d{4}-\d{1,2}-\d{1,2}\s*$", "", t).strip()
    if not t:
        m = re.search(r"<h1[^>]*>\s*(.*?)\s*</h1>", html, re.S | re.I)
        if m:
            t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    if not t or len(t) < 2:
        return None
    # 日期
    d = ""
    for pat in (r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", r"(20\d{2})[-/年](\d{1,2})"):
        m = re.search(pat, html)
        if m:
            g = m.groups()
            d = f"{g[0]}-{int(g[1]):02d}" + (f"-{int(g[2]):02d}" if len(g) > 2 else "")
            break
    # 分组：优先看面包屑/分类链接，其次正文关键词
    g = ""
    crumb = re.search(r"(?:当前位置|您的位置|面包屑)[^<]*</?[^>]*>(.{0,400})", html, re.S | re.I)
    scope = crumb.group(1) if crumb else html[:8000]
    for k, v in TAG_MAP.items():
        if k in scope:
            g = v
            break
    if not g:
        g = "其他"
    return {"t": t, "u": url, "g": g, "d": d}


def main() -> int:
    urls = sitemap_urls()
    print(f"[sitemap] {len(urls)} 篇", flush=True)
    if not urls:
        print("sitemap 获取失败", flush=True)
        return 1

    cache: dict[str, dict] = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text("utf-8"))
        except Exception:
            cache = {}
    print(f"[cache] 已有 {len(cache)} 篇", flush=True)

    todo = [u for u in urls if u not in cache]
    print(f"[todo] 待抓 {len(todo)} 篇", flush=True)

    done = [0]
    lock_out = []

    def work(u: str):
        html = fetch(u)
        rec = parse_article(u, html) if html else None
        if rec:
            cache[u] = rec
            done[0] += 1
            if done[0] % 100 == 0:
                print(f"  … {done[0]}/{len(todo)}", flush=True)
                # 增量落盘，防进程被杀丢进度
                try:
                    CACHE.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")
                except Exception:
                    pass

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(work, todo))

    # 只保留 sitemap 中仍存在的
    items = [cache[u] for u in urls if u in cache]
    items.sort(key=lambda x: -(int(re.search(r"/(\d+)\.html", x["u"]).group(1)) if re.search(r"/(\d+)\.html", x["u"]) else 0))
    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=0), "utf-8")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")
    print(f"[done] 写出 {len(items)} 篇 → {OUT}", flush=True)
    from collections import Counter
    print("[groups]", dict(Counter(x["g"] for x in items)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
