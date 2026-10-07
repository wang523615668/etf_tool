#!/usr/bin/env python3
"""门票股组合构建：E大规则筛出候选 → 行业分散约束 + 沪深均衡 → 等权组合建议
输出 data/menpiao_result.json（网页与推送共用）

约束（E大未给具体数值，这里按他"组合化、单一品种不可致命、十只以上、等权"的原则落地）：
  · 单行业上限 2 只（银行类放宽到 3，否则无法凑够十只以上：规则天然偏向银行）
  · 沪深两市各自至少 3 只（打新额度两市分开计算）
  · 最新增速 >100% 的异常项默认排除（可能一次性损益），单列观察
  · 排序分 = PE − 5年复合增速（越小=越便宜相对成长，等价于 PEG 视角）
"""
import json
import os
import sys

sys.path.insert(0, '/vol1/1000/openzl/etf_tool/scripts')
from menpiao_screen import STRICT_KINDS, load_periods, load_pe, screen

BASE = '/vol1/1000/openzl/etf_tool'
NAME_FP = f'{BASE}/data/stock_names.json'
SW_FP = f'{BASE}/data/sw_industry_cons.json'
OUT_FP = f'{BASE}/data/menpiao_result.json'


def industry_map() -> dict:
    d = json.load(open(SW_FP, encoding='utf-8'))
    m = {}
    for ind, rows in d.items():
        for it in rows:
            code = it[0] if isinstance(it, (list, tuple)) else None
            if code:
                m[str(code)] = ind
    return m


def market(code: str) -> str:
    return 'SH' if code.startswith(('60', '68')) else 'SZ'


def score(r) -> float:
    return r['pe'] - r['cagr5']


def build(rows10, rows5, ind_of, target=15, cap=2, cap_bank=3,
          relax_cap=True, min_target=10):
    """relax_cap=False：严格执行行业上限，凑不到 target 就少要几只（E大原话"十只以上"即可），
    只有连 min_target 都不到时才逐步放宽——避免为凑数破自己的分散约束。"""
    picked, picked_codes = [], set()
    counts = {}

    def lim_for(ind, extra):
        base = cap_bank if ('银行' in ind or '金融' in ind) else cap
        return base + extra

    def take(r, note=''):
        ind = ind_of.get(r['code'], '未分类')
        counts[ind] = counts.get(ind, 0) + 1
        picked.append({**r, 'industry': ind, 'market': market(r['code']),
                       'score': round(score(r), 2), 'pick_note': note})
        picked_codes.add(r['code'])

    def try_take(rows, extra, note, upto):
        for r in sorted(rows, key=score):
            if len(picked) >= upto:
                break
            if r['code'] in picked_codes or r['last_growth'] > 100:
                continue
            ind = ind_of.get(r['code'], '未分类')
            if counts.get(ind, 0) < lim_for(ind, extra):
                take(r, note)

    # 1) 10年组优先（条件最严），2) 5年组按分补足
    try_take(rows10, 0, '10年组', target)
    try_take(rows5, 0, '5年组', target)

    # 3) 沪深均衡：某市不足 3 只 → 补该市（打新额度两市分开）
    for mk in ('SH', 'SZ'):
        if len([p for p in picked if p['market'] == mk]) >= 3:
            continue
        pool = [r for r in sorted(rows5, key=score)
                if market(r['code']) == mk and r['code'] not in picked_codes
                and r['last_growth'] <= 100]
        for r in pool:
            if len([p for p in picked if p['market'] == mk]) >= 3:
                break
            take(r, '补' + mk + '市值')

    # 4) 逐步放宽行业上限，但只在不够 min_target 时才动
    if len(picked) < min_target:
        for extra in (1, 2, 99):
            if len(picked) >= min_target:
                break
            try_take(rows10, extra, f'放宽行业上限+{extra}', target)
            try_take(rows5, extra, f'放宽行业上限+{extra}', target)

    picked.sort(key=lambda x: x['score'])
    return picked


def main():
    periods = load_periods()
    pe_map = load_pe()
    ind_of = industry_map()
    names = json.load(open(NAME_FP, encoding='utf-8'))
    mp = periods

    def base_np(code, y='2020-12-31'):
        r = mp.get(y, {}).get(code)
        return (r.get('np') or 0) / 1e8 if r else None

    r10, hp10 = screen(10, '2026-06-30', '2025-12-31', pe_map, periods)
    r5, hp5 = screen(5, '2026-06-30', '2026-06-30', pe_map, periods)
    # 收紧口径：每一期报告（含一季报/三季报）归母净利同比都为正
    r10q, hp10q = screen(10, '2026-06-30', '2025-12-31', pe_map, periods, kinds=STRICT_KINDS)
    r5q, hp5q = screen(5, '2026-06-30', '2026-06-30', pe_map, periods, kinds=STRICT_KINDS)
    for r in r10 + r5 + r10q + r5q:
        r['industry'] = ind_of.get(r['code'], '未分类')
        r['market'] = market(r['code'])
        srv = names.get(r['code'])
        r['name_check'] = 'ok' if (not srv or srv.replace(' ', '') == r['name'].replace(' ', '')) else f'名称库={srv}'
        bn = base_np(r['code'])
        r['np_base_yi'] = round(bn, 2) if bn is not None else None
        # 基期年净利 < 1 亿 → 5年CAGR 由低基数放大，PE<增速 这条失去意义
        r['low_base'] = bool(bn is not None and bn < 1.0)

    both = [r for r in r10 if r['code'] in {x['code'] for x in r5}]
    port = build(r10, r5, ind_of)
    r10s = [r for r in r10 if not r['low_base']]
    r5s = [r for r in r5 if not r['low_base']]
    port_robust = build(r10s, r5s, ind_of)
    # 收紧口径组合：每期报告全正 + 剔低基数；严格执行行业上限，不硬凑（E大原话"十只以上"即可）
    port_strict = build([r for r in r10q if not r['low_base']],
                        [r for r in r5q if not r['low_base']], ind_of, relax_cap=False)
    anomalies = [r for r in r5 if r['last_growth'] > 100]

    # 分布统计
    def dist(rows, key):
        d = {}
        for r in rows:
            d[r[key]] = d.get(r[key], 0) + 1
        return dict(sorted(d.items(), key=lambda kv: -kv[1]))

    data = {
        'generated': __import__('datetime').date.today().isoformat(),
        'rule_src': 'chinaetfs.cn/a/580 (+a/1891 方法论)',
        'window': {'plan_10y': [hp10[0], hp10[-1], len(hp10)],
                   'plan_5y': [hp5[0], hp5[-1], len(hp5)],
                   'plan_10y_strict': [hp10q[0], hp10q[-1], len(hp10q)],
                   'plan_5y_strict': [hp5q[0], hp5q[-1], len(hp5q)]},
        'plan_10y': sorted(r10, key=score),
        'plan_5y': sorted(r5, key=score),
        'plan_10y_strict': sorted(r10q, key=score),
        'plan_5y_strict': sorted(r5q, key=score),
        'both': both,
        'portfolio': port,
        'portfolio_robust': port_robust,
        'portfolio_strict': port_strict,
        'low_base_excluded': [r for r in r5 if r['low_base']],
        'anomalies_excluded': anomalies,
        'dist': {
            'plan_10y_market': dist(r10, 'market'),
            'plan_5y_market': dist(r5, 'market'),
            'portfolio_market': dist(port, 'market'),
            'portfolio_industry': dist(port, 'industry'),
            'robust_market': dist(port_robust, 'market'),
            'robust_industry': dist(port_robust, 'industry'),
            'strict_market': dist(port_strict, 'market'),
            'strict_industry': dist(port_strict, 'industry'),
        },
    }
    json.dump(data, open(OUT_FP, 'w'), ensure_ascii=False, indent=1)

    print(f'规则来源 {data["rule_src"]}')
    print(f'10年组 {len(r10)} 只 | 5年组 {len(r5)} 只 | 双重通过 {len(both)} 只')

    def show(rows, title):
        print(f'\n== {title}（{len(rows)} 只）==')
        print(f"{'代码':8}{'名称':11}{'行业':11}{'市':4}{'PE':>7}{'5年复合':>9}{'基期净利':>10}{'最新增速':>9}{'分':>8}  来源")
        for p in rows:
            print(f"{p['code']:8}{p['name'][:9]:11}{p['industry'][:9]:11}{p['market']:4}"
                  f"{p['pe']:>7}{p['cagr5']:>9}{(p['np_base_yi'] if p['np_base_yi'] is not None else 0):>10}"
                  f"{p['last_growth']:>9}{p['score']:>8}  {p['pick_note']}")

    show(port, '组合A：严格按E大原文规则')
    print('  沪深', data['dist']['portfolio_market'], '| 行业', data['dist']['portfolio_industry'])
    show(port_robust, '组合B：加"基期年净利≥1亿"约束（半年口径）')
    print('  沪深', data['dist']['robust_market'], '| 行业', data['dist']['robust_industry'])
    show(port_strict, '组合C：收紧口径 = 每一期报告(含季报)同比全正 + 剔低基数（推荐）')
    print('  沪深', data['dist']['strict_market'], '| 行业', data['dist']['strict_industry'])
    print(f"\n收紧口径筛出：10年组 {len(r10q)} 只（半年口径 {len(r10)} 只）"
          f" | 5年组 {len(r5q)} 只（半年口径 {len(r5)} 只）")
    print(f"\n低基数剔除 {len(data['low_base_excluded'])} 只: "
          + ', '.join(f"{r['name']}(基期{r['np_base_yi']}亿)" for r in data['low_base_excluded']))
    print(f"\n异常剔除 {len(anomalies)} 只: " + ', '.join(f"{r['name']}({r['last_growth']}%)" for r in anomalies))
    print(f'\n已写入 {OUT_FP}')


if __name__ == '__main__':
    main()
