#!/usr/bin/env python3
"""抓取申万行业成分（含计入日期）—— 用于还原"当时成分"的行业类估值列

背景：E大估值表的行业列（食品饮料等）用的不是中证细分指数，而是**申万口径**。
实测：食品饮料列用申万122只（中位剔亏TTM）对 E大真值 2022-12-01 差 -0.1% / 2022-12-19 差 +0.1%，
而用中证细分食品饮料(000815) 差 -25~-29%。见 app/data_sources 与 skill 参考文档。

用法：
  python3 scripts/fetch_sw_cons.py                # 刷新缓存 data/sw_industry_cons.json
  python3 scripts/fetch_sw_cons.py --asof 2022-12-01 食品饮料   # 打印当日成分（按计入日期过滤）

注意：
- 需用 etf_tool 的 venv（akshare 在里面）：/vol1/1000/openzl/etf_tool/.venv/bin/python
- 「计入日期」多数是 2021-12-13 那批分类修订日 → **2021-12 之前不能按它做 PIT**，
  更早日期只能用冻结名单（有幸存者偏差，但中位法影响有限）。
- 申万行业只覆盖行业指数；宽基/主题（沪深300/中证医疗等）仍用理杏仁 as-of 或 baostock。
"""
import json, os, sys, subprocess

BASE = "/vol1/1000/openzl/etf_tool"
OUT = f"{BASE}/data/sw_industry_cons.json"
VENV_PY = f"{BASE}/.venv/bin/python"

# 申万一级行业（与 E大估值表可能对应的列）；二级可按需追加
L1 = {
    "食品饮料": "801120", "电子": "801080", "计算机": "801750", "通信": "801770",
    "传媒": "801760", "医药生物": "801150", "银行": "801780", "非银金融": "801790",
    "家用电器": "801110", "机械设备": "801890", "电力设备": "801730", "汽车": "801880",
    "有色金属": "801050", "基础化工": "801030", "房地产": "801180", "建筑装饰": "801720",
    "农林牧渔": "801010", "纺织服饰": "801130", "轻工制造": "801140", "商贸零售": "801200",
    "交通运输": "801170", "公用事业": "801160", "环保": "801970", "国防军工": "801740",
    "煤炭": "801950", "石油石化": "801960", "钢铁": "801040", "建筑材料": "801710",
    "社会服务": "801210", "综合": "801230", "美容护理": "801980",
}

def fetch(codes=None):
    """在 venv 里跑 akshare（系统 python 没装），返回 {行业: [[code,name,计入日期], ...]}"""
    codes = codes or L1
    py = f'''
import akshare as ak, json
out = {{}}
for nm, sym in {json.dumps(codes, ensure_ascii=False)}.items():
    try:
        df = ak.index_component_sw(symbol=sym)
        out[nm] = [[str(r["证券代码"]).zfill(6), str(r["证券名称"]), str(r.get("计入日期") or "")] for _, r in df.iterrows()]
    except Exception as e:
        out[nm] = {{"err": str(e)[:120]}}
print(json.dumps(out, ensure_ascii=False))
'''
    r = subprocess.run([VENV_PY, "-c", py], capture_output=True, text=True, timeout=1800)
    if not r.stdout.strip().startswith("{"):
        raise RuntimeError(f"akshare 抓取失败: {r.stderr[-300:]}")
    return json.loads(r.stdout)

def load(refresh=False):
    if refresh or not os.path.exists(OUT):
        d = fetch()
        bad = {k: v for k, v in d.items() if not isinstance(v, list)}
        json.dump(d, open(OUT, "w"), ensure_ascii=False)
        print(f"已刷新 {OUT}: {len(d)} 个行业，异常 {len(bad)} 个 {list(bad)[:5]}")
        return d
    return json.load(open(OUT))

def asof(industry, date, data=None):
    """当日成分（按计入日期过滤）。注意 2021-12 之前的日期会大量落空，见文件头说明。"""
    data = data or load()
    lst = data.get(industry)
    if not isinstance(lst, list):
        raise KeyError(f"{industry} 无数据")
    return [c for c, nm, incl in lst if (not incl) or incl <= date]

def frozen(industry, data=None):
    """冻结名单（全部当前成分）——历史区间用这个，中位法对幸存者偏差不敏感"""
    data = data or load()
    lst = data.get(industry)
    if not isinstance(lst, list):
        raise KeyError(f"{industry} 无数据")
    return [c for c, nm, incl in lst]

if __name__ == "__main__":
    if "--asof" in sys.argv:
        i = sys.argv.index("--asof")
        date, ind = sys.argv[i+1], sys.argv[i+2]
        codes = asof(ind, date)
        print(f"{ind} @ {date}: {len(codes)} 只")
        print(" ".join(codes[:40]), "...")
    else:
        d = load(refresh=True)
        for k, v in sorted(d.items(), key=lambda x: -(len(x[1]) if isinstance(x[1], list) else 0)):
            print(f"  {k:<8} {len(v) if isinstance(v, list) else v}")
