#!/usr/bin/env python3
# 月度追加 as-of 成分名单(baostock): 补最近3个月内缺失的月末 hs300/zz500/sz50 → pit_cons_monthly.json
# 挂 cron: 每月 1-3 日 08:30 (幂等: 已存在的月份跳过)
import baostock as bs, json, os, datetime as dt, sys
BASE="/vol1/1000/openzl/etf_tool"
FP=f"{BASE}/data/pit_cons_monthly.json"
m=json.load(open(FP)) if os.path.exists(FP) else {}
today=dt.date.today()
targets=[]
for back in range(1,4):                      # 上个月末、上上个月末、再前一个
    last=dt.date(today.year, today.month, 1)-dt.timedelta(days=back)
    if last.isoformat() not in m: targets.append(last.isoformat())
if not targets:
    print("无需更新(近3个月已有)"); sys.exit(0)
lg=bs.login()
if lg.error_code!='0': print("baostock login 失败", lg.error_msg); sys.exit(1)
added=0
for key in targets:
    rec={}
    for k,q in (("hs300",bs.query_hs300_stocks),("zz500",bs.query_zz500_stocks),("sz50",bs.query_sz50_stocks)):
        rs=q(date=key); rows=[]
        while (rs.error_code=='0') and rs.next(): rows.append(rs.get_row_data()[1])
        rec[k]=rows
    if len(rec['hs300'])>=250 and len(rec['zz500'])>=400 and len(rec['sz50'])>=40:
        m[key]=rec; added+=1
        print(f"+ {key} hs300={len(rec['hs300'])} zz500={len(rec['zz500'])} sz50={len(rec['sz50'])}")
bs.logout()
if added:
    json.dump(m, open(FP,"w"))
    print(f"已写入 {FP} 共{len(m)}个月, 新增{added}")
else:
    print("未取到有效名单(可能非交易日/批次未发布)")
