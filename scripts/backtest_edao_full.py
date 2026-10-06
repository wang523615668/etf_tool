# E大完整打法复刻v2：长期仓 + 波段腿 + 轮动腿（组合级、逐日）
import sys
sys.path.insert(0, '/vol1/1000/openzl/etf_tool/scripts')
import numpy as np
from backtest_current import load_rows

START = 1500000.; SHARE = 10000.
NAMES = ["沪深300","中证500","创业板指","恒生指数","证券公司","中证医疗","中证红利",
         "全指消费","养老产业","全指医药","中概互联","中证传媒"]

def build_feats():
    feats = {}
    for nm in NAMES:
        rows = load_rows(nm)
        if not rows or len(rows) < 1300: continue
        dates=[r["date"] for r in rows]
        cps=np.array([r.get("cp") if r.get("cp") else np.nan for r in rows])
        pes=np.array([r.get("pe") if r.get("pe") else np.nan for r in rows])
        pbs=np.array([r.get("pb") if r.get("pb") else np.nan for r in rows])
        n=len(rows)
        ma250=np.full(n,np.nan)
        for i in range(249,n):
            v=cps[i-249:i+1]; v=v[~np.isnan(v)]
            if len(v)>=200: ma250[i]=v.mean()
        prem=np.full(n,np.nan)
        for i in range(1300,n):
            devs=[]
            for arr in (pes,pbs):
                lo=max(0,i-1225); v=arr[lo:i+1]; v=v[~np.isnan(v)]
                if len(v)<500 or np.isnan(arr[i]) or arr[i]<=0: continue
                devs.append(v.mean()/arr[i]-1)
            if devs: prem[i]=-sum(devs)/len(devs)
        feats[nm]={'dates':dates,'cps':cps,'prem':prem,'ma250':ma250,
                   'idx':{d:j for j,d in enumerate(dates)}}
    return feats


def simulate(feats, start_date):
    all_dates=sorted(set(d for f in feats.values() for d in f['dates'] if d>=start_date))
    pos={nm:{'L':0.,'S':0.,'e':None} for nm in feats}
    cash=START; trades=[]; eq=[]
    last_l_buy=-999  # 组合级买入间隔（E大按计划表买，不按品种）
    last_rot=0
    swing_high={}   # 波段仓的持仓期高点
    for d in all_dates:
        gi=all_dates.index(d)   # 全局日序号（组合级间隔用这个，不用品种内索引）
        day={}
        for nm,f in feats.items():
            j=f['idx'].get(d)
            if j is None: continue
            c=f['cps'][j]
            if np.isnan(c): continue
            day[nm]=(j,c,f['prem'][j],f['ma250'][j])
        if not day: continue
        # ---- 1) 波段止盈：S份额浮盈≥15% → 落袋，等回调8%接回 ----
        for nm,(j,c,p,ma) in day.items():
            po=pos[nm]
            if po['S']>0:
                hi=swing_high.get(nm,c)
                swing_high[nm]=max(hi,c); hi=swing_high[nm]
                if po['e'] and c/po['e']-1>=0.15:
                    units=po['S']*SHARE/po['e']          # 1份=1万元本金→成本价折股数
                    pr=units*c; pnl=int(units*(c-po['e']))
                    cash+=pr
                    trades.append((d,nm,'波段止盈',round(po['S'],2),pnl))
                    po['_await_drop']=hi*0.92  # 回调到高点-8%再接回
                    po['S']=0.
        # 接回：从浮盈落袋点回调≥8% 且溢价仍<15%
        for nm,(j,c,p,ma) in day.items():
            po=pos[nm]
            aw=po.get('_await_drop')
            if aw and c<=aw and (np.isnan(p) or p<0.15):
                buy=min(2., 3.75-po['S'])
                cost=buy*SHARE
                if cash>=cost and buy>0.25:
                    tot=(po['L']+po['S'])
                    po['e']=(po['e']*tot+c*buy)/(tot+buy) if tot>0 else c
                    cash-=cost; po['S']+=buy
                    trades.append((d,nm,'波段接回',round(buy,2),f"回{c/aw-1:+.0%}"))
                del po['_await_drop']
        # ---- 2) 轮动：每月检查一次。卖掉溢价>+15%品种各1份L，买入当前溢价最低且<-10%品种 ----
        j0=all_dates.index(d)
        if j0 is None: j0=gi
        if gi-last_rot>=20:
            hot=[nm for nm,(j,c,p,ma) in day.items() if not np.isnan(p) and p>0.15 and pos[nm]['L']>0]
            cold=sorted([nm for nm,(j,c,p,ma) in day.items() if not np.isnan(p) and p<-0.10],
                        key=lambda nm: day[nm][2])
            if hot and cold and cold[0]!=hot[0]:
                src=hot[0]; dst=cold[0]
                su=min(1.,pos[src]['L'])
                ps=pos[src]; pd_=pos[dst]
                c_s=day[src][1]; c_d=day[dst][1]
                pr=su*SHARE*c_s/ps['e'] if ps['e'] else su*SHARE
                pnl=int(pr-su*SHARE) if ps['e'] else 0
                # 只在赚钱时换（不亏钱卖出原则）
                if pnl>0:
                    cash+=pr; ps['L']-=su
                    trades.append((d,src,'轮动卖出',round(su,2),pnl))
                    buy=su; cost=buy*SHARE
                    if cash>=cost:
                        tot=pd_['L']+pd_['S']
                        pd_['e']=(pd_['e']*tot+c_d*buy)/(tot+buy) if tot>0 else c_d
                        cash-=cost; pd_['L']+=buy
                        trades.append((d,dst,'轮动买入',round(buy,2),f"p={day[dst][2]:+.0%}"))
            last_rot=j0
        # ---- 3) 波段建仓：右侧确认（价站上MA250）且溢价<15%，买1份S；同品种间隔≥40日 ----
        for nm,(j,c,p,ma) in day.items():
            po=pos[nm]
            if po['S']>0 or np.isnan(ma) or c<=ma: continue
            if not np.isnan(p) and p>=0.15: continue
            if gi-po.get('last_s',-999)<40: continue
            buy=min(1., max(0., 2.-po['S']))
            cost=buy*SHARE
            if cash>=cost and buy>0.25:
                tot=po['L']+po['S']
                po['e']=(po['e']*tot+c*buy)/(tot+buy) if tot>0 else c
                cash-=cost; po['S']+=buy; po['last_s']=gi
                swing_high[nm]=c
                trades.append((d,nm,'波段建仓',round(buy,2),f"p={p:+.0%} 右侧"))
        # ---- 4) 长期左侧：溢价<5%起步（组合级每20天最多一笔，优先最便宜）----
        cand=[nm for nm,(j,c,p,ma) in day.items() if not np.isnan(p) and p<0.05]
        if cand:
            nm=min(cand,key=lambda n:day[n][2])
            j,c,p,ma=day[nm]
            po=pos[nm]; cap=37.5 if p<=-0.50 else 28.125
            if po['L']<cap and gi-last_l_buy>=20:
                step=2. if p<=-0.30 else 1.
                buy=min(step,cap-po['L']); cost=buy*SHARE
                if cash>=cost:
                    tot=po['L']+po['S']
                    po['e']=(po['e']*tot+c*buy)/(tot+buy) if tot>0 else c
                    cash-=cost; po['L']+=buy
                    trades.append((d,nm,'长期买入',round(buy,2),f"p={p:+.0%}"))
                    last_l_buy=gi
        # ---- 4) 高估清仓：溢价>45% 卖长期仓2份/次（+15%以上部分由轮动处理）----
        for nm,(j,c,p,ma) in day.items():
            po=pos[nm]
            if not np.isnan(p) and p>0.45 and po['L']>0:
                if po['e'] and c<=po['e']: continue
                su=min(2.,po['L'])
                pr=su*SHARE*c/po['e']; pnl=int(pr-su*SHARE)
                cash+=pr; po['L']-=su
                trades.append((d,nm,'高估减',round(su,2),pnl))
        tv=sum((pos[nm]['L']+pos[nm]['S'])*SHARE*(day[nm][1]/pos[nm]['e'] if pos[nm]['e'] else 1.)
               for nm in day if pos[nm]['e'])
        eq.append(cash+tv)
    final=eq[-1]
    import os
    if os.environ.get('BT_AUDIT'):
        from collections import Counter
        agg=Counter()
        for nm in pos:
            po=pos[nm]
            if po['L']+po['S']>0 and po['e']:
                f=feats[nm]; jj=f['idx'].get(all_dates[-1])
                c=f['cps'][jj] if jj is not None else np.nan
                if np.isnan(c): continue
                mv=(po['L']+po['S'])*SHARE*(c/po['e'])
                agg[nm]=(round(po['L'],1),round(po['S'],1),int(po['e']),int(c),int(mv),int((po['L']+po['S'])*SHARE))
        print('AUDIT cash=',int(cash))
        for k,v in agg.items(): print('  ',k,'L,S,e,价,市值,成本 =',v)
    sells=[t for t in trades if t[2] in('波段止盈','高估减')]
    wins=[t for t in sells if isinstance(t[4],int) and t[4]>0]
    peak=max(eq); mdd=(min(eq)/peak-1)*100
    inv=sum(t[2] for t in trades if '买入' in t[2] and isinstance(t[2],(int,float)))
    return {"ret":round((final/START-1)*100,1),"trades":trades,"eq":eq,"mdd":round(mdd,1),
            "nsw":len([t for t in trades if t[2]=='波段止盈']),
            "sw_pnl":sum(t[4] for t in trades if t[2]=='波段止盈' and isinstance(t[4],int)),
            "nrot":len([t for t in trades if t[2]=='轮动卖出'])}


if __name__=="__main__":
    feats=build_feats()
    for start,tag in [('2023-01-01','近3年'),('2016-01-01','10年')]:
        r=simulate(feats,start)
        yrs=(2026-int(start[:4]))
        ann=((1+r['ret']/100)**(1/yrs)-1)*100
        print(f"\n===== {tag} E大完整打法 =====")
        print(f"总收益 {r['ret']}%  年化≈{ann:.1f}%  回撤{r['mdd']}%")
        print(f"波段止盈{r['nsw']}笔共赚{r['sw_pnl']:,}元 | 轮动{r['nrot']}笔")
        from collections import Counter
        cc=Counter(t[2] for t in r['trades'])
        print(dict(cc))
