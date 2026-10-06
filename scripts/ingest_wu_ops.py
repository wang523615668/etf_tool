#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wu2198 操作跟踪入库：我(助手)把用户发来的操作文本解析成结构化 JSON，逐行喂给本脚本。
输入：每行一个 JSON（或整体一个 JSON 数组），字段：
  {"date":"2026-09-23","act":"买入|卖出|加仓|减仓|发车|调仓|持有","target":"中证传媒",
   "code":"004752","plan":"150|S|全部|", "amount":"1份","note":"原文摘录","src":"公众号|微博|且慢"}
按 date+target+act 去重（同日同类覆盖）。
入库后自动重建 vault 数据包（调用 build_app_ed_assets + vault_sync）。
用法：echo '{"date":...}' | python3 ingest_wu_ops.py
"""
import sys
import os
import json
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # etf_tool/
ARCHIVE = os.path.join(ROOT, 'data', 'wu_ops.json')


def _key(x):
    return (x.get('date', ''), (x.get('target') or '').strip(), x.get('act', ''))


def main():
    raw = sys.stdin.read().strip()
    if not raw:
        print('no input')
        return
    items = []
    if raw[0] == '[':
        items = json.loads(raw)
    else:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                print('skip bad line:', line[:60])
    if not items:
        print('no items')
        return
    existing = []
    if os.path.exists(ARCHIVE):
        try:
            existing = json.load(open(ARCHIVE, encoding='utf-8'))
        except Exception:
            existing = []
    idx = {_key(x): i for i, x in enumerate(existing)}
    added = updated = 0
    for x in items:
        if not isinstance(x, dict) or not x.get('date'):
            continue
        k = _key(x)
        if k in idx:
            existing[idx[k]] = x
            updated += 1
        else:
            idx[k] = len(existing)
            existing.append(x)
            added += 1
    existing.sort(key=lambda x: x.get('date', ''), reverse=True)
    existing = existing[:2000]
    json.dump(existing, open(ARCHIVE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'ops stored: +{added} ~{updated} total={len(existing)}')
    # 重建数据包并同步
    for script in [os.path.join(ROOT, 'scripts', 'build_app_ed_assets.py'),
                   '/vol1/1000/openzl/finance_app/vault_sync.py']:
        if os.path.exists(script):
            subprocess.run([sys.executable, script], cwd=ROOT, timeout=600)
    print('rebuilt & synced')


if __name__ == '__main__':
    main()
