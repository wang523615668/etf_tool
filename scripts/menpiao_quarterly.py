#!/usr/bin/env python3
"""季度重筛执行器：抓新报告期 → 重筛 → 与上次结果 diff → 输出微信可用摘要。
用法：.venv/bin/python scripts/menpiao_quarterly.py [--no-fetch]
每次运行把快照存 data/menpiao_history/{YYYY-MM-DD}.json，保留最近 8 份。
"""
import json
import os
import subprocess
import sys
import datetime

BASE = '/vol1/1000/openzl/etf_tool'
PY = f'{BASE}/.venv/bin/python'
HIST = f'{BASE}/data/menpiao_history'
RESULT = f'{BASE}/data/menpiao_result.json'
os.makedirs(HIST, exist_ok=True)

today = datetime.date.today().isoformat()
prev_fp = None
snaps = sorted(f for f in os.listdir(HIST) if f.endswith('.json'))
if snaps:
    prev_fp = os.path.join(HIST, snaps[-1])


def run(cmd):
    r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, timeout=1200)
    if r.returncode != 0:
        print(f'!! {" ".join(cmd)} 失败\n{r.stdout[-800:]}\n{r.stderr[-800:]}')
        sys.exit(1)
    return r.stdout


if '--no-fetch' not in sys.argv:
    run([PY, 'scripts/menpiao_fetch.py'])
run([PY, 'scripts/menpiao_portfolio.py'])

cur = json.load(open(RESULT, encoding='utf-8'))
prev = json.load(open(prev_fp, encoding='utf-8')) if prev_fp else None

# 存快照
snap_fp = f'{HIST}/{today}.json'
if not os.path.exists(snap_fp) or True:
    json.dump(cur, open(snap_fp, 'w'), ensure_ascii=False)
    for old in sorted(f for f in os.listdir(HIST) if f.endswith('.json'))[:-8]:
        os.remove(os.path.join(HIST, old))

# ---- 摘要 ----
L = []
L.append(f'🎫 门票股季度重筛 · {today}')
L.append('（E大规则：报告期利润持续增长 + PE<25 + PE<5年复合增速 + PE<最新一期增速）')
L.append('')
p10, p5 = cur['plan_10y'], cur['plan_5y']
s10, s5 = cur.get('plan_10y_strict', []), cur.get('plan_5y_strict', [])
L.append(f'半年口径：10年组 {len(p10)} 只 · 5年组 {len(p5)} 只')
L.append(f'季度收紧口径：10年组 {len(s10)} 只 · 5年组 {len(s5)} 只')
port = cur.get('portfolio_strict') or cur['portfolio_robust']
L.append(f'推荐组合C（季度收紧 + 剔低基数，行业≤2·银行≤3，不硬凑）{len(port)} 只')
L.append('')

if prev:
    pp = prev.get('portfolio_strict') or prev.get('portfolio_robust') or []
    pk = {x['code'] for x in pp}
    ck = {x['code'] for x in port}
    added = [x for x in port if x['code'] not in pk]
    dropped = [x for x in pp if x['code'] not in ck]
    if added:
        L.append('✅ 本次新进：' + '、'.join(f"{x['name']}({x['code']})" for x in added))
    if dropped:
        L.append('❌ 本次移出：' + '、'.join(f"{x['name']}({x['code']})" for x in dropped))
    if not added and not dropped:
        L.append('➖ 组合无变动')
    L.append('')

L.append('组合明细：')
for i, x in enumerate(port, 1):
    L.append(f"{i:2d}. {x['name']}({x['code']}) {x['industry']} {x['market']} "
             f"PE {x['pe']} / 5年复合 {x['cagr5']}% / 最新 {x['last_growth']}%")
d = cur['dist']
L.append('')
L.append(f"沪深分布 {d.get('strict_market', d['robust_market'])} · 行业分布 {d.get('strict_industry', d['robust_industry'])}")
if cur.get('low_base_excluded'):
    L.append('低基数剔除：' + '、'.join(f"{x['name']}(基期{x['np_base_yi']}亿)" for x in cur['low_base_excluded']))
if cur.get('anomalies_excluded'):
    L.append('增速异常剔除：' + '、'.join(f"{x['name']}({x['last_growth']}%)" for x in cur['anomalies_excluded']))
L.append('')
L.append('E大原话「我不荐股」——本结果为规则复刻，不构成投资建议。')
L.append('详情 https://ef.523615668.xyz/menpiao')

print('\n'.join(x for x in L if x is not None))
