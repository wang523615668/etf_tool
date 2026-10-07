#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聚宽(JoinQuant)研究环境导出脚本 —— 历史时点成分股 + 全A上市名单
用法：登录 https://www.joinquant.com/ → 研究环境(Jupyter) → 新建 Notebook → 把本文件全部内容粘进一个 cell → 运行
产出：/home/jquser/研究环境/  下 3 个 CSV（用右侧"文件"面板下载后发我）
说明：仅用于一次性导出静态名单，导完即弃；无账号依赖，不需要 API key。
"""
import csv, datetime as dt

# ── 需要导出的指数（列名 → 聚宽代码）────────────────────────────
IDX = {
    "上证50": "000016.XSHG", "沪深300": "000300.XSHG", "中证500": "000905.XSHG",
    "上证180": "000010.XSHG", "中证1000": "000852.XSHG", "创业板指": "399006.XSHE",
    "创业板综": "399102.XSHE", "科创50": "000688.XSHG", "中证红利": "000922.XSHG",
    "中证医疗": "399989.XSHE", "中证军工": "399967.XSHE", "中证环保": "000827.XSHG",
    "全指医药": "000991.XSHG", "全指消费": "000990.XSHG", "全指信息": "000993.XSHG",
    "全指金融": "000992.XSHG", "养老产业": "399812.XSHE", "中证传媒": "399971.XSHE",
    "证券公司": "399975.XSHE", "中证银行": "399986.XSHE", "中证白酒": "399997.XSHE",
    "中证A500": "000510.XSHG", "食品饮料(中证)": "000807.XSHG",
    "食品饮料(申万)": "801120.XSHG", "红利低波": "H30269.CSI", "红利低波100": "930955.CSI",
}
# ── 采样日期：每年 6/15、12/15、年末（够还原任意时点，含调仓生效前后）──
DATES = [f"{y}-{md}" for y in range(2015, 2027) for md in ("06-15", "12-15", "12-31")]
DATES = [d for d in DATES if d <= dt.date.today().isoformat()]

rows, allrows, fails = [], [], []
for name, code in IDX.items():
    for d in DATES:
        try:
            lst = get_index_stocks(code, date=d)          # 聚宽内置，研究环境可直接调用
        except Exception as e:
            fails.append((name, code, d, str(e)[:80])); continue
        for s in lst:
            rows.append([name, code, d, s])
# 全A上市名单（含后来退市的股票，用于"全市场"两列）
for d in DATES:
    try:
        secs = list(get_all_securities(types=["stock"], date=d).index)
    except Exception as e:
        fails.append(("全市场", "ALL", d, str(e)[:80])); continue
    for s in secs:
        allrows.append([d, s])

F1, F2 = "jq_index_cons.csv", "jq_all_listed.csv"
with open(F1, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["index_name", "index_code", "date", "stock"]); w.writerows(rows)
with open(F2, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["date", "stock"]); w.writerows(allrows)

print(f"指数成分 {len(rows)} 行 → {F1}")
print(f"全A名单  {len(allrows)} 行 → {F2}")
print(f"采样日期 {len(DATES)} 个（{DATES[0]} … {DATES[-1]}），指数 {len(IDX)} 个")
if fails:
    print(f"\n⚠️ 失败 {len(fails)} 条（前10条）:")
    for x in fails[:10]:
        print("  ", x)
print("\n→ 在左侧/右侧『文件』面板找到这两个 CSV，下载后发给助手。")
