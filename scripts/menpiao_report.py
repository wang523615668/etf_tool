#!/usr/bin/env python3
"""交叉校验 + 成品清单（沪深分布/双重通过/等权方案）"""
import json, os, sys
sys.path.insert(0, '/vol1/1000/openzl/etf_tool/scripts')
from menpiao_screen import load_periods, load_pe, screen, PERIODS

BASE = '/vol1/1000/openzl/etf_tool'
periods = load_periods()
pe_map = load_pe()
names = json.load(open(f'{BASE}/data/stock_names.json'))

r10, hp10 = screen(10, '2026-06-30', '2025-12-31', pe_map, periods)
r5, hp5 = screen(5, '2026-06-30', '2026-06-30', pe_map, periods)

# 1) 代码↔名称 双源校验
print('== 代码↔名称一致性校验（东财 vs 本地名称库）==')
bad = []
for r in r10 + r5:
    local = names.get(r['code'])
    if local and local.replace(' ', '') != r['name'].replace(' ', ''):
        bad.append((r['code'], r['name'], local))
print(f'  校验 {len(r10)+len(r5)} 条，不一致 {len(bad)} 条', bad if bad else '✓ 全部一致')

# 2) 双重通过（10年+5年都入选）
both = {r['code']: r for r in r10 if r['code'] in {x['code'] for x in r5}}
print(f'\n== 双重通过（10年+5年 全部条件）: {len(both)} 只 ==')
for c, r in both.items():
    print(f"  {c} {r['name']}  PE {r['pe']}  5年复合 {r['cagr5']}%  最新增速 {r['last_growth']}%")

# 3) 沪深分布（打新额度两市分开，必须均衡）
def mk(code):
    return 'SH' if code.startswith(('60', '68')) else 'SZ'
for label, rows in (('10年', r10), ('5年', r5)):
    sh = [r for r in rows if mk(r['code']) == 'SH']
    sz = [r for r in rows if mk(r['code']) == 'SZ']
    print(f'\n== {label}计划 沪深分布 == 沪 {len(sh)} 只 / 深 {len(sz)} 只')
    print('  沪:', ', '.join(f"{r['name']}({r['code']})" for r in sh))
    print('  深:', ', '.join(f"{r['name']}({r['code']})" for r in sz))

# 4) 异常增速标记（可能含非经常性损益/一次性）
print('\n== 需要留意的异常项 ==')
for r in r5:
    if r['last_growth'] > 100:
        print(f"  {r['code']} {r['name']}: 最新增速 {r['last_growth']}%（10年组最低期 {r['min_growth']}%）——跳变过大，建议查是否一次性损益")
for r in r10 + r5:
    if r['min_growth'] < 1:
        print(f"  {r['code']} {r['name']}: 窗口内最低期增速仅 {r['min_growth']}%——增速贴地，随时可能破位")

# 5) 落盘
out = {'generated': '2026-10-07', 'rule_src': '/a/580',
       'plan_10y': r10, 'plan_5y': r5, 'both': list(both.values())}
json.dump(out, open(f'{BASE}/data/menpiao_result.json', 'w'), ensure_ascii=False, indent=1)
print('\n结果已存 data/menpiao_result.json')
