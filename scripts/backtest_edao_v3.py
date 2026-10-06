# E大完整打法复刻 v3 —— 单位制记账（买入金额/价格=份额），杜绝口径错位
# 三层：长期左侧(prem阈值) + 波段右侧(MA250突破/+15%止盈/-8%接回) + 月度轮动(贵换便宜)
# 价格用全收益口径(股息再投资近似)，份额随权益复利(E大按份买入，1份=当前权益1%)
import sys
import json
sys.path.insert(0, '/vol1/1000/openzl/etf_tool/scripts')
import numpy as np
from backtest_current import load_rows

START = 1_500_000.
DIV = {"沪深300":0.025,"中证500":0.015,"创业板指":0.007,"恒生指数":0.035,"证券公司":0.02,
       "中证医疗":0.010,"中证红利":0.060,"全指消费":0.015,"养老产业":0.020,"全指医药":0.015,
       "中概互联":0.010,"中证传媒":0.015}
NAMES = list(DIV)

# 大类资产扩展：港股(恒生科技)、纳指、日经、黄金 —— 无PE/PB，用价格分位做估值
# 数据源: cache/global_idx.json (纳指/黄金/日经) + lixinger缓存(恒生科技)
GLOBAL = "/vol1/1000/openzl/etf_tool/cache/global_idx.json"
# 股息率(全收益近似): 纳指~0.8%, 黄金0, 日经~1.8%, 恒生科技~0.5%
EXTRA_DIV = {"纳斯达克":0.008,"黄金":0.0,"日经225":0.018,"恒生科技":0.005}

def build_feats():
    feats={}
    for nm in NAMES:
        rows=load_rows(nm)
        if not rows or len(rows)<1300: continue
        dates=[r["date"] for r in rows]
        cps=np.array([r.get("cp") or np.nan for r in rows],dtype=float)
        pes=np.array([r.get("pe") or np.nan for r in rows],dtype=float)
        pbs=np.array([r.get("pb") or np.nan for r in rows],dtype=float)
        n=len(rows)
        y=DIV.get(nm,0.01)
        fac=(1+y/250)**np.arange(n)          # 全收益调整
        trp=cps*fac
        ma=np.full(n,np.nan)
        for i in range(249,n):
            v=trp[i-249:i+1]; v=v[~np.isnan(v)]
            if len(v)>=200: ma[i]=v.mean()
        prem=np.full(n,np.nan)
        for i in range(1300,n):
            devs=[]
            for arr in (pes,pbs):
                lo=max(0,i-1225); v=arr[lo:i+1]; v=v[~np.isnan(v)]
                if len(v)<500 or np.isnan(arr[i]) or arr[i]<=0: continue
                devs.append(v.mean()/arr[i]-1)
            if devs: prem[i]=-sum(devs)/len(devs)
        feats[nm]={'dates':dates,'cps':trp,'prem':prem,'ma250':ma,
                   'idx':{d:j for j,d in enumerate(dates)}}
    # ---- 大类资产：恒生科技(lixinger有PE/PB) + 纳指/日经/黄金(价格分位估值) ----
    hs_rows=load_rows('恒生科技')
    if hs_rows and len(hs_rows)>1000:
        _add_extra(feats,'恒生科技',[r["date"] for r in hs_rows],
                   [r.get("cp") or np.nan for r in hs_rows],
                   [r.get("pe") for r in hs_rows],[r.get("pb") for r in hs_rows])
    try:
        g=json.load(open(GLOBAL))
    except Exception:
        g={}
    for nm,data in g.items():
        _add_extra(feats,nm,data['date'],data['close'],None,None)
    return feats

def _add_extra(feats,nm,dates,closes,pes,pbs):
    """大类资产特征。有PE/PB走估值偏离；否则用5年滚动价格分位(低=便宜)。"""
    import json as _json
    dates=list(dates); n=len(closes)
    if n<600: 
        if nm!='日经225' and n<600: return   # 日经历史短，特殊放行
    cps=np.array([np.nan if c in (None,'') else float(c) for c in closes],dtype=float)
    pes=np.array(pes,dtype=float) if pes is not None else np.full(n,np.nan)
    pbs=np.array(pbs,dtype=float) if pbs is not None else np.full(n,np.nan)
    y=EXTRA_DIV.get(nm,0.0)
    fac=(1+y/250)**np.arange(n)
    trp=cps*fac
    ma=np.full(n,np.nan)
    for i in range(249,n):
        v=trp[i-249:i+1]; v=v[~np.isnan(v)]
        if len(v)>=200: ma[i]=v.mean()
    prem=np.full(n,np.nan)
    has_val=not np.all(np.isnan(pes)) and np.nanmax(pes)>0
    for i in range(max(750,n//2),n):
        devs=[]
        if has_val:
            for arr in (pes,pbs):
                lo=max(0,i-1225); v=arr[lo:i+1]; v=v[~np.isnan(v)]
                if len(v)<300 or np.isnan(arr[i]) or arr[i]<=0: continue
                devs.append(v.mean()/arr[i]-1)
        else:
            # 价格分位法：当前价在近5年区间位置 → 便宜度 = -(位置-0.5)
            lo=max(0,i-1225); w=cps[lo:i+1]; w=w[~np.isnan(w)]
            if len(w)<400 or np.isnan(cps[i]): continue
            pct=(w<=cps[i]).mean()
            devs.append(pct-0.5)
        if devs: prem[i]=-sum(devs)/len(devs)
    feats[nm]={'dates':dates,'cps':trp,'prem':prem,'ma250':ma,
               'idx':{d:j for j,d in enumerate(dates)}}

def simulate(feats,start_date):
    all_dates=sorted(set(d for f in feats.values() for d in f['dates'] if d>=start_date))
    gidx={d:i for i,d in enumerate(all_dates)}
    # pos[nm] = {'L':units,'S':units,'Le':成本额,'Se':成本额}  全部按"投入金额"记
    pos={nm:{'Lu':0.,'Su':0.,'Lc':0.,'Sc':0.} for nm in feats}
    cash=START; trades=[]; eq=[]
    bond=0.                                  # 债券仓位（金额），近似中债综合年化3.5%
    MMF=0.015/250                            # 货币基金日收益
    BOND=0.035/250                           # 债券日收益
    last_l_buy=-999; last_rot=-999; await_drop={}; swing_hi={}
    equity=START
    _mark=[None, -999]; last_day={}; last_tv=0.
    for d in all_dates:
        gi=gidx[d]; day={}
        for nm,f in feats.items():
            j=f['idx'].get(d)
            if j is None: continue
            c=f['cps'][j]
            if np.isnan(c) or c<=0: continue
            day[nm]=(c,f['prem'][j],f['ma250'][j])
        if not day: continue
        # ---- 0) 现金计息(货基) + 债券计息；现金闲置过多时买入债券，股票缺钱时赎回债券 ----
        cash*= (1+MMF); bond*= (1+BOND)
        unit=equity*0.05                      # 1份=当前权益5%（复利）

        def redeem_bond(amount):
            nonlocal bond
            got=min(bond,amount); bond-=got
            return got

        def sell(nm,bucket,u_frac_or_units,tag):
            nonlocal cash
            po=pos[nm]; u=po[bucket+'u']
            if u<=0: return
            su=min(u,u_frac_or_units) if isinstance(u_frac_or_units,float) else u
            c=day[nm][0]
            cost_share=po[bucket+'c']/u
            pr=su*c; pnl=pr-su*cost_share
            cash+=pr; po[bucket+'u']-=su; po[bucket+'c']-=su*cost_share
            trades.append((d,nm,tag,round(su*c,0),int(pnl)))
            return pnl

        def buy(nm,bucket,amount,note=''):
            nonlocal cash,equity,bond
            po=pos[nm]
            if amount>cash:                   # 现金不够先赎债券
                cash+=redeem_bond(amount-cash)
            amt=min(amount,cash)
            if amt<unit*0.25: return False
            c=day[nm][0]; u=amt/c
            cash-=amt
            old_u=po[bucket+'u']; old_c=po[bucket+'c']
            po[bucket+'u']=old_u+u; po[bucket+'c']=old_c+amt
            trades.append((d,nm,'BUY_'+bucket.replace('u',''),round(amt,0),note))
            return True

        # ---- 1) 波段止盈：S浮盈≥15% ----
        for nm,(c,p,ma) in day.items():
            po=pos[nm]
            if po['Su']>0 and po['Sc']>0 and c*po['Su']/po['Sc']-1>=0.15:
                hi=max(swing_hi.get(nm,c),c); swing_hi[nm]=hi
                sell(nm,'S',po['Su'],'波段止盈')
                await_drop[nm]=hi*0.92
        # ---- 2) 接回：回落≥8%、溢价<15%，且价格仍在MA250的95%以上（熊市不接飞刀）----
        for nm,(c,p,ma) in day.items():
            aw=await_drop.get(nm)
            if aw and c<=aw and (np.isnan(p) or p<0.15):
                if not np.isnan(ma) and c<ma*0.95:
                    continue   # 熊市中保持空仓等待
                sc=pos[nm]['Sc']
                if sc<unit*2.5: buy(nm,'S',unit*1.0,f"回{c/aw-1:+.0%}")
                del await_drop[nm]
        # ---- 3) 轮动（每20交易日）：卖p>15%且有盈利的L一份，买最便宜p<-10% ----
        if gi-last_rot>=20:
            last_rot=gi
            hots=[nm for nm,(c,p,ma) in day.items()
                  if not np.isnan(p) and p>0.15 and pos[nm]['Lu']>0
                  and c*pos[nm]['Lu']>pos[nm]['Lc']]
            colds=sorted([nm for nm,(c,p,ma) in day.items() if not np.isnan(p) and p<-0.10],
                         key=lambda n:day[n][2])
            if hots and colds and colds[0]!=hots[0]:
                src,dst=hots[0],colds[0]
                u1=pos[src]['Lu']; pr_before=sell(src,'L',u1*0.34,'轮动卖')  # ~1份当量
                if pr_before is not None:
                    buy(dst,'L',unit*1.0,f"轮入p={day[dst][2]:+.0%}")
        # ---- 4) 波段建仓：价上MA250且p<15%，同品种间隔40日 ----
        for nm,(c,p,ma) in day.items():
            po=pos[nm]
            if po['Su']*c>po['Sc']*0.5: continue      # 已有相当仓位不重复建
            if np.isnan(ma) or c<=ma: continue
            if not np.isnan(p) and p>=0.20: continue
            key=('SB',nm)
            if gi-last_l_buy>=0 and gi-(po.get('_ls',-999))>=40:
                if buy(nm,'S',unit*1.0,f"右p={p:+.0%}"):
                    po['_ls']=gi; swing_hi[nm]=c
        # ---- 5) 长期左侧：组合级每20日一笔，分散买入最便宜的3个品种(p<5%) ----
        cand=sorted([nm for nm,(c,p,ma) in day.items() if not np.isnan(p) and p<0.05],
                    key=lambda n:day[n][2])[:3]
        if cand and gi-last_l_buy>=20:
            # 轮流挑当前长期仓成本占比最低的那个
            nm=min(cand,key=lambda n:pos[n]['Lc'])
            c,p,ma=day[nm]; po=pos[nm]
            # 单品种长期仓上限：总投入的 20%（防止单品种吸走全部资金）
            if po['Lc'] < equity*0.20:
                amt=min(unit*2 if p<=-0.30 else unit*1, equity*0.20-po['Lc'])
                if buy(nm,'L',amt,f"Lp={p:+.0%}"):
                    last_l_buy=gi
        # ---- 6) 高估减：p>45% 且有盈利，减2份当量 ----
        for nm,(c,p,ma) in day.items():
            po=pos[nm]
            if not np.isnan(p) and p>0.45 and po['Lu']>0 and c*po['Lu']>po['Lc']:
                sell(nm,'L',min(po['Lu'], unit*2/day[nm][0]),'高估减')
        # ---- 7) 长期仓右侧转弱：跌破MA250且浮亏>10%，减1份当量；收回MA250上方且溢价<15%时接回 ----
        if gi-_mark[1]>=20:
            for nm,(c,p,ma) in day.items():
                po=pos[nm]
                if np.isnan(ma) or po['Lu']<=0: continue
                if c<ma and c*po['Lu']<po['Lc']*0.85:
                    u1=min(po['Lu'], unit*1.0/day[nm][0])
                    if sell(nm,'L',u1,'右侧减'):
                        _mark[1]=gi
                        break
        # 接回：右侧减过的品种，价格收复MA250 且 溢价<15%，每12日最多一笔
        if gi-_mark[1]>=12:
            for nm,(c,p,ma) in day.items():
                po=pos[nm]
                if np.isnan(ma) or c<=ma: continue
                if not np.isnan(p) and p>=0.15: continue
                if buy(nm,'L',unit*1.0,f"右接p={p:+.0%}"):
                    _mark[1]=gi
                    break
        # ---- 8) 现金管理：现金>权益40%时超出部分买债(留30%)；现金<10%时赎回债券补到20% ----
        equity_now=cash+bond+sum((pos[nm]['Su']+pos[nm]['Lu'])*day[nm][0] for nm in day)
        if cash>equity_now*0.40:
            bond+=cash-equity_now*0.30; cash=equity_now*0.30
        elif cash<equity_now*0.10 and bond>0:
            cash+=redeem_bond(min(bond,equity_now*0.20-cash))
        # ---- 结算权益 ----
        # 节假日/缺数据日：用最近一次有效市值（避免 tv 只算到个别品种导致权益假跳水）
        if len(day)>=len(feats)*0.8:
            last_tv=sum((pos[nm]['Su']+pos[nm]['Lu'])*day[nm][0] for nm in day)
            last_day=day
        else:
            last_tv=sum((pos[nm]['Su']+pos[nm]['Lu'])*last_day[nm][0]
                        for nm in pos if nm in last_day and (pos[nm]['Su']+pos[nm]['Lu'])>0)
        tv=last_tv
        equity=cash+bond+tv
        eq.append(equity)
        if False and d[8:10]<='03' and (d[:4]+d[5:7])!=_mark[0]:
            _mark[0]=d[:4]+d[5:7]
            print(f'  {d} cash={int(cash):,} tv={int(tv):,} eq={int(equity):,}')
    final=eq[-1]
    sells=[t for t in trades if t[2] in('波段止盈','高估减','轮动卖')]
    wins=[t for t in sells if isinstance(t[4],int) and t[4]>0]
    eqa=np.array(eq,dtype=float); peak=np.maximum.accumulate(eqa)
    mdd=float((eqa/peak-1).min())*100
    from collections import Counter
    cc=Counter(t[2] for t in trades)
    return {"ret":round((final/START-1)*100,1),"mdd":round(mdd,1),
            "sw_pnl":sum(t[4] for t in trades if t[2]=='波段止盈' and isinstance(t[4],int)),
            "nsw":len([t for t in trades if t[2]=='波段止盈']),
            "nrot":len([t for t in trades if t[2]=='轮动卖']),
            "final":int(final),"cc":dict(cc)}

if __name__=="__main__":
    feats=build_feats()
    for start,tag,yrs in [('2016-01-01','10年',10),('2023-01-01','近3年',3)]:
        r=simulate(feats,start)
        ann=((1+r['ret']/100)**(1/yrs)-1)*100
        print(f"\n== {tag} E大完整打法v3 ==")
        print(f"总收益 {r['ret']}%  年化≈{ann:.1f}%  回撤 {r['mdd']}%")
        print(f"波段止盈{r['nsw']}笔共赚{r['sw_pnl']:,}元 | 轮动{r['nrot']}笔 | 期末{r['final']:,}")
        print(r['cc'])
