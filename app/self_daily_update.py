#!/usr/bin/env python3
# s11: 自算估值日更 — 拉当日东财全A快照 → 更新 self_daily/*.json 序列(追加+重算5y/10y分位)
# 挂 cron: 交易日17:30。代理7890走东财datacenter。
import requests, json, os, sys, datetime as dt, statistics as st
BASE="/vol1/1000/openzl/etf_tool"
HIST=f"{BASE}/data/self_daily"
SNAPDIR=f"{BASE}/data/self_daily/snaps"
os.makedirs(SNAPDIR, exist_ok=True)
P={"http":"http://127.0.0.1:7890","https":"http://127.0.0.1:7890"}
HD={"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}

def trade_day():
    # 最近一个有数据的交易日: 从今天往前探(count>3000)
    for back in range(10):
        d=(dt.date.today()-dt.timedelta(days=back)).isoformat()
        u=("https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_VALUEANALYSIS_DET"
           f"&columns=SECURITY_CODE&filter=(TRADE_DATE%3D%27{d}%27)&pageNumber=1&pageSize=1")
        try:
            j=requests.get(u,timeout=20,proxies=P,headers=HD).json()
            cnt=(j.get('result') or {}).get('count')
            if cnt and cnt>3000:
                fp=f"{SNAPDIR}/{d}.json"
                return d, (os.path.exists(fp) or False)
        except Exception:
            pass
    return None, False

def day_snapshot(date):
    fp=f"{SNAPDIR}/{date}.json"
    if os.path.exists(fp): return json.load(open(fp))
    out={}; page=1
    while True:
        u=("https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_VALUEANALYSIS_DET"
           f"&columns=SECURITY_CODE,PE_TTM,PB_MRQ&filter=(TRADE_DATE%3D%27{date}%27)"
           f"&pageNumber={page}&pageSize=5000&sortColumns=SECURITY_CODE&sortTypes=1")
        for att in range(3):
            try:
                j=requests.get(u,timeout=30,proxies=P,headers=HD).json(); break
            except Exception:
                import time; time.sleep(2)
        else: return None
        rows=(j.get('result') or {}).get('data') or []
        for r in rows: out[r['SECURITY_CODE']]=[r.get('PE_TTM'), r.get('PB_MRQ')]
        if len(rows)<5000: break
        page+=1
    if len(out)<3000: return None
    json.dump(out, open(fp,'w'))
    return out

def main():
    date,_=trade_day()
    if not date: print("无新交易日数据"); return
    snap=day_snapshot(date)
    if not snap: print(f"快照失败 {date}"); sys.exit(1)
    # as-of 成分名单(baostock 月度PIT) —— 宽基序列用当时名单而非冻结名单
    PITMAP={}
    pitfp=f"{BASE}/data/pit_cons_monthly.json"
    if os.path.exists(pitfp):
        try: PITMAP=json.load(open(pitfp))
        except Exception as e: print("PIT名单读取失败", e)
    MAPKEY={"000300":"hs300","000905":"zz500","000016":"sz50"}
    def pit_codes(code):
        if not PITMAP: return None
        months=[m for m in PITMAP if m<=date]
        if not months: return None
        return PITMAP[max(months)].get(MAPKEY.get(code,'')) or None
    n_upd=0
    for fp in sorted(os.listdir(HIST)):
        if not fp.endswith('.json'): continue
        d=json.load(open(f"{HIST}/{fp}"))
        if not (d.get('code') or d.get('scope')):
            continue   # 非序列文件(配置/索引等)跳过, 防止被当成序列塞入 rows
        codes=d.get('codes')
        if d.get('pit') and d.get('code'):
            pc=pit_codes(d['code'])
            if pc: codes=pc
        rows=d.get('rows') or []
        if rows and rows[-1]['date']>=date:
            continue
        if codes:
            pes=[snap[c][0] for c in codes if c in snap and snap[c][0] and snap[c][0]>0]
            pbs=[snap[c][1] for c in codes if c in snap and snap[c][1] and snap[c][1]>0]
        else:  # MKT_MAIN / MKT_ALL
            scope=d.get('scope','all')
            if scope=='main':
                pes=[v[0] for c,v in snap.items() if c.startswith(('60','00')) and v[0] and v[0]>0]
                pbs=[v[1] for c,v in snap.items() if c.startswith(('60','00')) and v[1] and v[1]>0]
            else:
                pes=[v[0] for v in snap.values() if v[0] and v[0]>0]
                pbs=[v[1] for v in snap.values() if v[1] and v[1]>0]
        if len(pes)<20: continue
        rows.append({"date":date,"pe":round(st.median(pes),3),"pb":round(st.median(pbs),3) if pbs else None,"n":len(pes)})
        d['rows']=rows; d['latest']=rows[-1]
        # 重算分位
        import bisect
        def pct(yrs):
            d0=(dt.date.fromisoformat(date)-dt.timedelta(days=int(365.25*yrs))).isoformat()
            w=sorted(r['pe'] for r in rows if r['date']>=d0 and r['pe'])
            return round(sum(1 for x in w if x<=rows[-1]['pe'])/len(w)*100,1) if len(w)>=50 else None
        d['p5y']=pct(5); d['p10y']=pct(10)
        json.dump(d, open(f"{HIST}/{fp}",'w'), ensure_ascii=False)
        n_upd+=1
    print(f"{date} 快照{len(snap)}只, 更新序列{n_upd}条")

if __name__=='__main__':
    main()
