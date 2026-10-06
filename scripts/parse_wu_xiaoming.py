#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析"WU-小明"公众号文章（徐小明+WU2198 合并策略帖）→ 结构化 JSON 存档
用法:
  python3 parse_wu_xiaoming.py <mp.weixin URL 或本地html> [--out ARCHIVE.json]
输出条目: {id, date, title, xm_pos, wu_pos, xm_op, wu_op, wu_text, xm_text, url, ts}
存档: /vol1/1000/openzl/etf_tool/data/wu_xiaoming.json (按 id 去重, 幂等)
"""
import json
import re
import sys
import time
import hashlib
import urllib.request
from html import unescape
from pathlib import Path

ARCHIVE = Path('/vol1/1000/openzl/etf_tool/data/wu_xiaoming.json')
UA = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'}
CN_NUM = {'一': 0.1, '二': 0.2, '两': 0.2, '三': 0.3, '四': 0.4, '五': 0.5, '六': 0.6, '七': 0.7, '八': 0.8, '九': 0.9, '十': 1.0, '半': 0.5, '满': 1.0, '空': 0.0}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'ignore')


def strip_body(h):
    m = re.search(r'<div[^>]*class="[^"]*rich_media_content[^"]*"[^>]*>(.*?)</div>\s*<script', h, re.S)
    body = m.group(1) if m else h
    body = re.sub(r'<br[^>]*>|</p>|</section>', '\n', body)
    body = re.sub(r'<[^>]+>', '', body)
    body = unescape(body)
    body = body.replace('\u200d', '').replace('\xa0', ' ')
    lines = [l.strip() for l in body.split('\n')]
    return '\n'.join([l for l in lines if l])


def pos_frac(s):
    """'八成仓'→0.8, '2米仓底'→0.2, '满仓'→1.0, '空仓'→0"""
    if not s:
        return None
    s = s.strip()
    m = re.search(r'([一二两三四五六七八九十半满空])[成米]仓', s)
    if m:
        return CN_NUM.get(m.group(1))
    m = re.search(r'([0-9]+)[成米]仓', s)
    if m:
        v = float(m.group(1))
        return v / 10 if v <= 10 else v / 100
    return None


def parse(html_src, is_url=False):
    h = fetch(html_src) if is_url else open(html_src, encoding='utf-8').read()
    title = ''
    mt = re.search(r'<h1[^>]*rich_media_title[^>]*>(.*?)</h1>', h, re.S) or re.search(r'<meta property="og:title" content="([^"]+)"', h) or re.search(r'<title>([^<]+)</title>', h)
    if mt:
        title = re.sub(r'<[^>]+>', '', mt.group(1)).strip()
    url = html_src if is_url else (re.search(r'<meta property="og:url" content="([^"]+)"', h) or [None, ''])[1]
    txt = strip_body(h)
    # 日期：标题里 2026/09/22 或 (0922)；正文顶部
    date = ''
    md = re.search(r'(202[0-9])[/年-](\d{1,2})[/月-](\d{1,2})', title) or re.search(r'(202[0-9])[/年-](\d{1,2})[/月-](\d{1,2})', txt[:200])
    if md:
        date = f'{md.group(1)}-{int(md.group(2)):02d}-{int(md.group(3)):02d}'
    # 大V仓位行: "小明：八成仓（无操作）" / "WU：二成仓（无操作）"
    item = {'id': hashlib.md5((url or title + date).encode()).hexdigest()[:12],
            'date': date, 'title': title, 'url': url, 'ts': int(time.time())}
    mx = re.search(r'小明[：:]\s*([^\n]{0,20}仓)([（(][^)）]*[)）])?', txt)
    mw = re.search(r'WU[：:]\s*([^\n]{0,20}仓)([（(][^)）]*[)）])?', txt)
    if mx:
        item['xm_pos'] = pos_frac(mx.group(1)); item['xm_pos_raw'] = mx.group(1); item['xm_op'] = (mx.group(2) or '').strip('（()）')
    if mw:
        item['wu_pos'] = pos_frac(mw.group(1)); item['wu_pos_raw'] = mw.group(1); item['wu_op'] = (mw.group(2) or '').strip('（()）')
    # 发言分段: WU: ... 小明: ...
    iw = re.search(r'\nWU[：:]\s*\n', txt)
    ix = re.search(r'\n小明[：:]\s*\n', txt)
    if iw:
        end = ix.start() if ix and ix.start() > iw.end() else len(txt)
        seg = txt[iw.end():end]
        if item.get('xm_pos_raw'):
            seg = seg.split(item['xm_pos_raw'] + ('（' + item['xm_op'] + '）' if item.get('xm_op') else ''))[0]
        item['wu_text'] = seg.strip()[:1200]
    if ix:
        item['xm_text'] = txt[ix.end():].strip()[:2500]
    return item


def main():
    src = sys.argv[1]
    is_url = src.startswith('http')
    item = parse(src, is_url)
    arch = []
    if ARCHIVE.exists():
        arch = json.loads(ARCHIVE.read_text(encoding='utf-8'))
    old = {x['id'] for x in arch}
    if item['id'] in old:
        print(json.dumps({'saved': False, 'reason': 'dup', 'item': item}, ensure_ascii=False)[:400])
        return
    arch.append(item)
    arch.sort(key=lambda x: x.get('date') or '')
    ARCHIVE.write_text(json.dumps(arch, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({'saved': True, 'total': len(arch), 'item': {k: (v[:60] if isinstance(v, str) else v) for k, v in item.items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
