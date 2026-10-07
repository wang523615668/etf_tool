#!/usr/bin/env python3
"""按 E大 门票股规则筛选（原文见 data/ed_articles_body.json /a/580.html）

E大原文规则：
  10年计划：过去十年每个半年利润都在增加；PE<25；PE<5年年均增长率；PE<最近一期年报增长率
  5年计划 ：过去5年每个半年利润都在增加；PE<25；PE<5年年均增长率；PE<最近一期季报增长率

实现口径（明示，便于复核）：
  · "每个半年利润都在增加" = 每个半年度报告期的归母净利润同比(SJLTZ) > 0
  · "5年年均增长率" = 年度归母净利润5年复合增速 (%)，末期=最新年报
  · PE 取最新交易日逐股 PE_TTM（本地 ed_hist_full 快照，与自算估值页同源）
  · 仅沪深A股(60/68/00/30)、剔 ST/退市、剔 PE<=0
"""
import json
import os
import sys

BASE = '/vol1/1000/openzl/etf_tool'
MP = f'{BASE}/data/menpiao'


def load_periods():
    out = {}
    for fn in sorted(os.listdir(MP)):
        if not fn.endswith('.json'):
            continue
        d = json.load(open(f'{MP}/{fn}'))
        out[d['date']] = {r['code']: r for r in d['rows'] if r.get('code')}
    return out


def load_pe(date='2026-09-30'):
    raw = json.load(open(f'{BASE}/data/ed_hist_full/{date}.json'))
    return {c: (v[0] if v else None) for c, v in raw.items()}


def half_periods(end, years, kinds=('06-30', '12-31')):
    """从 end 往前 years 年内的报告期列表（含末期）。
    kinds=('06-30','12-31') → E大原文的"每半年"口径
    kinds=STRICT_KINDS      → 收紧口径"每一期报告都正增长"
    """
    y0 = int(end[:4]) - years
    return [p for p in ALL_PERIODS
            if f'{y0}' <= p[:4] <= end[:4] and p <= end and p.endswith(kinds)]


def screen(years, end_period, latest_for_growth, pe_map, periods, kinds=('06-30', '12-31')):
    """years: 5/10; end_period: 连续增长考察的末期; latest_for_growth: 'A'=年报 or 'H'=当期"""
    hp = half_periods(end_period, years, kinds)
    rows = []
    for code, cur in periods[end_period].items():
        if not code.startswith(('60', '68', '00', '30')):
            continue
        nm = (cur.get('name') or '')
        if 'ST' in nm.upper() or '退' in nm:
            continue
        pe = pe_map.get(code)
        if not pe or pe <= 0:
            continue
        # 1) 每期归母净利润同比 > 0
        seq = []
        ok = True
        for p in hp:
            r = periods.get(p, {}).get(code)
            if not r or r.get('sjltz') is None:
                ok = False
                break
            seq.append(round(r['sjltz'], 2))
            if r['sjltz'] <= 0:
                ok = False
        if not ok:
            continue
        # 2) 5年复合增速（年度净利润，末期=最新年报）
        y_end = latest_for_growth if latest_for_growth.endswith('12-31') else f'{int(latest_for_growth[:4])-1}-12-31'
        y_base = f'{int(y_end[:4])-5}-12-31'
        a = periods.get(y_end, {}).get(code)
        b = periods.get(y_base, {}).get(code)
        if not a or not b or not a.get('np') or not b.get('np') or b['np'] <= 0:
            continue
        cagr = ((a['np'] / b['np']) ** (1 / 5) - 1) * 100
        # 3) 最近一期增速
        last = periods[latest_for_growth].get(code)
        if not last or last.get('sjltz') is None:
            continue
        # 4) 三条硬条件
        if pe >= 25 or pe >= cagr or pe >= last['sjltz']:
            continue
        rows.append({
            'code': code, 'name': nm, 'pe': round(pe, 2),
            'cagr5': round(cagr, 2), 'last_growth': round(last['sjltz'], 2),
            'last_period': latest_for_growth, 'n_periods': len(seq),
            'min_growth': min(seq), 'avg_growth': round(sum(seq) / len(seq), 2),
            'np_yi': round((a['np'] or 0) / 1e8, 2),
            'recent': seq[-6:],
        })
    rows.sort(key=lambda x: (x['pe'] - x['cagr5']))
    return rows, hp


ALL_PERIODS = []
for y in range(2015, 2027):
    for md in ('03-31', '06-30', '09-30', '12-31'):
        d = f'{y}-{md}'
        if d <= '2026-06-30':
            ALL_PERIODS.append(d)
# E大原文口径 = 半年报（6-30 上半年累计 / 12-31 全年累计）
PERIODS = [p for p in ALL_PERIODS if p.endswith(('06-30', '12-31'))]
# 收紧口径 = 每一期报告（含一季报/三季报）同比都为正
STRICT_KINDS = ('03-31', '06-30', '09-30', '12-31')

if __name__ == '__main__':
    periods = load_periods()
    pe_map = load_pe()
    print(f'报告期 {len(periods)} 个 | PE 覆盖 {len(pe_map)} 只\n')

    for label, years, end, lf in [
        ('10年计划 (每半年增长·10年, 最新一期=2025年报)', 10, '2026-06-30', '2025-12-31'),
        ('5年计划  (每半年增长·5年,  最新一期=2026中报)', 5, '2026-06-30', '2026-06-30'),
    ]:
        rows, hp = screen(years, end, lf, pe_map, periods)
        print('=' * 100)
        print(f'{label}  →  {len(rows)} 只   (考察期 {hp[0]} ~ {hp[-1]}, 共 {len(hp)} 个半年报)')
        print('=' * 100)
        print(f"{'代码':8}{'名称':10}{'PE':>7}{'5年复合%':>10}{'最新增速%':>10}{'净利(亿)':>10}{'最低期增速%':>12}")
        for r in rows[:40]:
            nm = r['name'][:8]
            print(f"{r['code']:8}{nm:10}{r['pe']:>7}{r['cagr5']:>10}{r['last_growth']:>10}{r['np_yi']:>10}{r['min_growth']:>12}")
        if len(rows) > 40:
            print(f'  ... 另 {len(rows)-40} 只')
        print()
