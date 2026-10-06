#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WU-小明公众号文章接收口：给 URL（每行一个）→ 抓取解析入库 → 重建 APP 数据。
用法:  echo "https://mp.weixin.qq.com/s/xxx" | ./ingest_wu_xiaoming.sh
或由 hermes cron/消息管道调用。已存在的 id 自动跳过（幂等）。
"""
import subprocess
import sys
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARSER = HERE / 'parse_wu_xiaoming.py'
ARCHIVE = Path('/vol1/1000/openzl/etf_tool/data/wu_xiaoming.json')
BUILDER = HERE / 'build_app_ed_assets.py'

MP_RE = re.compile(r'https?://mp\.weixin\.qq\.com/s[/A-Za-z0-9_\-?=&.]*')


def main():
    raw = sys.stdin.read() if len(sys.argv) < 2 else ' '.join(sys.argv[1:])
    urls = list(dict.fromkeys(MP_RE.findall(raw)))
    if not urls:
        print(json.dumps({'ok': False, 'reason': '未发现 mp.weixin 链接', 'input_len': len(raw)}, ensure_ascii=False))
        return 1
    added = []
    for u in urls:
        try:
            out = subprocess.run([sys.executable, str(PARSER), u], capture_output=True, text=True, timeout=90)
            line = (out.stdout or '').strip().split('\n')[-1] if out.stdout else ''
            try:
                d = json.loads(line)
            except Exception:
                d = {'saved': False, 'reason': ('dup' if '"dup"' in line else 'parse失败: ' + (out.stderr or line)[-160:])}
            if d.get('saved'):
                it = d['item']
                added.append({'date': it.get('date'), 'xm_pos': it.get('xm_pos'), 'wu_pos': it.get('wu_pos')})
            print(json.dumps(d, ensure_ascii=False)[:300])
        except Exception as e:
            print(json.dumps({'ok': False, 'url': u[:60], 'err': str(e)[:200]}, ensure_ascii=False))
    if added:
        subprocess.run([sys.executable, str(BUILDER)], capture_output=True, text=True, timeout=300)
        print(json.dumps({'rebuilt': True, 'added_n': len(added), 'added': added}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
