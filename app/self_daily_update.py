#!/usr/bin/env python3
# 自算估值日更 — 拉当日东财全A快照 → 追加/更新 self_daily/*.json 序列(重算5y/10y分位)
# 挂 cron: 交易日17:30。代理7890走东财datacenter。
#
# v2(2026-10-06): 改为「逐指数最优组合」—— 与 app/self_daily_rebuild.py 共用 app/ed_algo.py，
#   配置见 data/ed_perindex_best.json(每列算法×名单源各自最优, 对 E大 表平均最大偏差 5.7%)。
#   快照多取 PE_LAR(静态PE) 与 TOTAL_MARKET_CAP(市值), 供 截尾/静态/整体法 类算法使用。
import requests, json, os, sys, datetime as dt, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ed_algo

BASE = "/vol1/1000/openzl/etf_tool"
HIST = f"{BASE}/data/self_daily"
SNAPDIR = f"{BASE}/data/self_daily/snaps"
SHADOW_DIR = f"{BASE}/data/self_daily_shadow"
os.makedirs(SNAPDIR, exist_ok=True)
P = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
HD = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
ALIAS = {"全市场·主板(E大口径)": "全市场", "全市场·沪深京": "全市场加权"}
COLS4 = "SECURITY_CODE,PE_TTM,PE_LAR,PB_MRQ,TOTAL_MARKET_CAP"
SHADOW_ALT = {"current": "pit", "mktcap": "pit", "pit": "current", "bspit": "current"}  # 影子=另一名单口径

def trade_day():
    """最近一个有数据的交易日: 从今天往前探(count>3000)"""
    for back in range(10):
        d = (dt.date.today() - dt.timedelta(days=back)).isoformat()
        u = ("https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_VALUEANALYSIS_DET"
             f"&columns=SECURITY_CODE&filter=(TRADE_DATE%3D%27{d}%27)&pageNumber=1&pageSize=1")
        try:
            j = requests.get(u, timeout=20, proxies=P, headers=HD).json()
            cnt = (j.get('result') or {}).get('count')
            if cnt and cnt > 3000:
                return d, os.path.exists(f"{SNAPDIR}/{d}.json")
        except Exception:
            pass
    return None, False

def _norm(v):
    """兼容旧版2字段快照缓存 [PE_TTM, PB_MRQ] → dict"""
    if isinstance(v, dict):
        return v
    if isinstance(v, list) and len(v) >= 2:
        return {"PE_TTM": v[0], "PB_MRQ": v[1], "PE_LAR": None, "TOTAL_MARKET_CAP": None}
    return {"PE_TTM": None, "PE_LAR": None, "PB_MRQ": None, "TOTAL_MARKET_CAP": None}

def day_snapshot(date):
    fp = f"{SNAPDIR}/{date}.json"
    if os.path.exists(fp):
        st = {c: _norm(v) for c, v in json.load(open(fp)).items()}
        # 旧版2字段缓存(缺 PE_LAR/TOTAL_MARKET_CAP): 会让静态PE/市值类口径静默算不出 → 视为陈旧, 重新抓
        if st and any(v.get("PE_LAR") is not None for v in list(st.values())[:80]):
            return st
        print(f"  快照 {date} 为旧版2字段缓存, 重新抓取")
    out, page = {}, 1
    while True:
        u = ("https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_VALUEANALYSIS_DET"
             f"&columns={COLS4}&filter=(TRADE_DATE%3D%27{date}%27)"
             f"&pageNumber={page}&pageSize=5000&sortColumns=SECURITY_CODE&sortTypes=1")
        for att in range(3):
            try:
                j = requests.get(u, timeout=30, proxies=P, headers=HD).json(); break
            except Exception:
                import time; time.sleep(2)
        else:
            return None
        rows = (j.get('result') or {}).get('data') or []
        for r in rows:
            out[r['SECURITY_CODE']] = {"PE_TTM": r.get('PE_TTM'), "PE_LAR": r.get('PE_LAR'),
                                       "PB_MRQ": r.get('PB_MRQ'), "TOTAL_MARKET_CAP": r.get('TOTAL_MARKET_CAP')}
        if len(rows) < 5000:
            break
        page += 1
    if len(out) < 3000:
        return None
    json.dump(out, open(fp, 'w'))
    return out

def legacy_value(d, snap):
    """未配置逐指数算法的序列: 沿用旧口径(剔亏中位TTM / 剔亏中位PB)"""
    codes = d.get('codes')
    if codes:
        pes = [snap[c]["PE_TTM"] for c in codes if c in snap and snap[c]["PE_TTM"] and snap[c]["PE_TTM"] > 0]
        pbs = [snap[c]["PB_MRQ"] for c in codes if c in snap and snap[c]["PB_MRQ"] and snap[c]["PB_MRQ"] > 0]
    else:
        scope = d.get('scope', 'all')
        pre = ('60', '00') if scope == 'main' else None
        it = ((c, v) for c, v in snap.items() if (pre is None or c.startswith(pre)))
        pes, pbs = [], []
        for c, v in it:
            if v["PE_TTM"] and v["PE_TTM"] > 0: pes.append(v["PE_TTM"])
            if v["PB_MRQ"] and v["PB_MRQ"] > 0: pbs.append(v["PB_MRQ"])
    if len(pes) < 8:      # 白酒等小样本指数只有 13~20 只, 旧阈值20会让日更永远追不上
        return None, None, 0
    return round(st.median(pes), 3), (round(st.median(pbs), 3) if pbs else None), len(pes)

def main():
    # 口径自愈: 若序列里存的口径与配置不一致(改过标定配置但没重建), 先按新口径重建, 防止混口径
    try:
        import self_daily_sync
        self_daily_sync.main()
    except Exception as exc:
        print(f"口径自愈跳过: {exc}")
    # 防御性清理: 名单表的空快照(理杏仁对未发布指数会返回空) 必须在口径自愈前移除,
    # 否则空名单点会让该指数所有 as-of 查询拿到 [] → 序列断点 / 名单源退化
    try:
        fp = f"{BASE}/data/ed_cons_lixinger.json"
        lj = json.load(open(fp))
        cleaned = 0
        for rec in lj.values():
            a = rec.get("asof") or {}
            empt = [d for d, v in a.items() if not v]
            for d in empt:
                del a[d]
            if empt:
                rec["changes"] = len(a)
                rec["snapshots"] = len(a)
                cleaned += len(empt)
        if cleaned:
            import shutil, datetime as _dt
            ts = _dt.datetime.now().strftime("%Y%m%d-%H%M")
            shutil.copy(fp, f"{fp}.bak-empt-{ts}")
            json.dump(lj, open(fp, "w"), ensure_ascii=False)
            print(f"名单表清理: 移除 {cleaned} 个空快照点 (备份 .bak-empt-{ts})")
    except Exception as exc:
        print(f"名单表清理跳过: {exc}")
    date, _ = trade_day()
    if not date:
        print("无新交易日数据"); return
    snap = day_snapshot(date)
    if not snap:
        print(f"快照失败 {date}"); sys.exit(1)
    cfg = ed_algo.load_config()
    ctx = ed_algo.build_ctx(date)
    n_upd = 0
    for fp in sorted(os.listdir(HIST)):
        if not fp.endswith('.json'):
            continue
        d = json.load(open(f"{HIST}/{fp}"))
        if not (d.get('code') or d.get('scope')):
            continue   # 非序列文件(配置/索引等)跳过, 防止被当成序列塞入 rows
        rows = d.get('rows') or []
        if rows and rows[-1]['date'] >= date:
            continue
        key = ALIAS.get(d.get('name'), d.get('name'))
        if key in cfg:
            ctx.date = date
            pe, pb, n = ed_algo.series_value(snap, key, cfg, ctx)
            c = cfg[key]
            d['algo'] = c['algo']; d['list_mode'] = c['list']
            d['method'] = ed_algo.describe(c['algo'], c['list'])
            d['pit'] = c['list'] in ('pit', 'bspit')
        else:
            pe, pb, n = legacy_value(d, snap)
        if not pe:
            print(f"  跳过 {fp} (算不出)"); continue
        rows.append({"date": date, "pe": pe, "pb": pb, "n": n})
        d['rows'] = rows; d['latest'] = rows[-1]
        def pct(yrs):
            d0 = (dt.date.fromisoformat(date) - dt.timedelta(days=int(365.25 * yrs))).isoformat()
            w = sorted(r['pe'] for r in rows if r['date'] >= d0 and r['pe'])
            return round(sum(1 for x in w if x <= rows[-1]['pe']) / len(w) * 100, 1) if len(w) >= 50 else None
        d['p5y'] = pct(5); d['p10y'] = pct(10)
        json.dump(d, open(f"{HIST}/{fp}", 'w'), ensure_ascii=False)
        n_upd += 1
        # --- 影子序列(同算法×另一名单口径, 供分位敏感区间) ---
        shadow = f"{SHADOW_DIR}/{fp}"
        if os.path.exists(shadow) and key in cfg:
            try:
                sd = json.load(open(shadow))
                srows = sd.get('rows') or []
                if not srows or srows[-1]['date'] < date:
                    # 影子序列自带口径(可能是「换名单」也可能是「换算法」), 按其记录继续追加
                    amode = sd.get('list_mode') or SHADOW_ALT.get(cfg[key]['list'], 'current')
                    aalgo = sd.get('algo') or cfg[key]['algo']
                    spe, spb, sn = ed_algo.series_value(snap, key, {key: {"algo": aalgo, "list": amode}}, ctx)
                    if spe:
                        srows.append({"date": date, "pe": spe, "pb": spb, "n": sn})
                        sd['rows'] = srows; sd['latest'] = srows[-1]
                        json.dump(sd, open(shadow, 'w'), ensure_ascii=False)
            except Exception as exc:
                print(f"  影子序列失败 {fp}: {exc}")
    print(f"{date} 快照{len(snap)}只, 更新序列{n_upd}条")
    # --- 测算: 与E大对照偏差 + 分位敏感区间 ---
    try:
        import self_daily_calib
        self_daily_calib.main()
    except Exception as exc:
        print(f"测算失败: {exc}")

if __name__ == '__main__':
    main()
