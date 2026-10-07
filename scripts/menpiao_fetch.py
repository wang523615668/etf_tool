#!/usr/bin/env python3
"""抓 E大门票股筛选所需的半年报净利润全量数据（东财业绩报表）。
每个报告期 23 页 × 500 行；结果缓存到 data/menpiao/{date}.json
字段：PARENT_NETPROFIT=归母净利润(元), SJLTZ=净利润同比(%), YSTZ=营收同比(%)
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

BASE = '/vol1/1000/openzl/etf_tool'
OUT = f'{BASE}/data/menpiao'
os.makedirs(OUT, exist_ok=True)
PROXY = 'http://127.0.0.1:7890'
COLS = 'SECURITY_CODE,SECURITY_NAME_ABBR,REPORTDATE,PARENT_NETPROFIT,SJLTZ,YSTZ,BASIC_EPS,TOTAL_OPERATE_INCOME'

# 全部报告期（含一季报/三季报，供季度重筛）：截止日往前推 20 天，只取"已公布"的期
import datetime as _dt
CANDS = []
for y in range(2015, 2027):
    for md in ('03-31', '06-30', '09-30', '12-31'):
        CANDS.append(f'{y}-{md}')
_CUT = (_dt.date.today() - _dt.timedelta(days=20)).isoformat()
PERIODS = [d for d in CANDS if d <= _CUT]
# 连续增长考察只用半年度（6-30/12-31）
HALF_YEAR = [d for d in PERIODS if d.endswith(('06-30', '12-31'))]


def fetch(page, date, tries=4):
    q = urllib.parse.urlencode({
        'reportName': 'RPT_LICO_FN_CPD',
        'columns': COLS,
        'filter': f"(REPORTDATE='{date}')",
        'pageNumber': page,
        'pageSize': 500,
        'sortColumns': 'SECURITY_CODE',
        'sortTypes': 1,
    })
    url = 'https://datacenter-web.eastmoney.com/api/data/v1/get?' + q
    last = None
    for _ in range(tries):
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY}))
            raw = op.open(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=25).read()
            j = json.loads(raw)
            if j.get('success'):
                return j['result']['data'], j['result'].get('pages', 1)
            last = j.get('message')
        except Exception as e:
            last = str(e)[:80]
        time.sleep(1.2)
    raise RuntimeError(f'{date} p{page}: {last}')


def do_period(date):
    fp = f'{OUT}/{date}.json'
    if os.path.exists(fp):
        return date, 'cached', 0
    rows, pages = fetch(1, date)
    allrows = list(rows)
    for p in range(2, pages + 1):
        r, _ = fetch(p, date)
        if not r:
            break
        allrows.extend(r)
    seen, uniq = set(), []
    for r in allrows:
        c = r.get('SECURITY_CODE')
        if c and c not in seen:
            seen.add(c)
            uniq.append({'code': c, 'name': r.get('SECURITY_NAME_ABBR'),
                         'np': r.get('PARENT_NETPROFIT'), 'sjltz': r.get('SJLTZ'),
                         'ystz': r.get('YSTZ'), 'eps': r.get('BASIC_EPS'),
                         'rev': r.get('TOTAL_OPERATE_INCOME')})
    json.dump({'date': date, 'rows': uniq}, open(fp, 'w'), ensure_ascii=False)
    return date, 'ok', len(uniq)


if __name__ == '__main__':
    todo = [d for d in PERIODS if not os.path.exists(f'{OUT}/{d}.json')]
    print(f'待抓 {len(todo)}/{len(PERIODS)} 期', flush=True)
    with ThreadPoolExecutor(max_workers=3) as ex:
        for date, st, n in ex.map(do_period, todo):
            print(f'  {date} {st} {n}', flush=True)
    print('DONE', flush=True)
