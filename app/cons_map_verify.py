#!/usr/bin/env python3
"""cons_map_verify.py — 指数代码映射的三重验证(防 000928=800能源 这类映射错误)

背景: 指数代码一旦映射错, 只要标定器没选该名单源就不会暴露(潜伏雷)。本脚本用三个独立证据面核对:
  A) 官方成分重合: 中证官网当前成分 vs 名单表最新快照 (要求 ≥90%)
  B) 名称核对: 官方指数名称应含约定关键词 (如 000815 应叫"细分食品"而非"食品饮料")
  C) 空点检查: 名单表里不得有空快照(理杏仁无数据时落空=数据污染)

用法: python app/cons_map_verify.py           # 全量核对, 有问题退出码 1
定时: 已接入日更 cron(self_daily_update.py 开头), 异常只告警不阻断
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
LIVE = BASE / "data" / "ed_cons_lixinger.json"

# 期望官方名称关键词(与代码绑定, 改了代码就要改这里)
NAME_KEYWORD = {
    "上证50": "上证50", "沪深300": "沪深300", "中证500": "中证500", "中证1000": "1000",
    "上证180": "上证180", "创业板指": "创业板指", "科创50": "科创", "中证A500": "A500",
    "中证红利": "红利", "红利低波": "红利低波", "红利低波100": "低波100",
    "全指医药": "医药", "全指金融": "金融", "全指消费": "消费", "全指信息": "信息",
    "中证环保": "环保", "中证军工": "军工", "中证医疗": "医疗", "养老产业": "养老",
    "食品饮料": "食品", "中证传媒": "传媒", "证券公司": "证券", "中证银行": "银行",
    "中证白酒": "白酒",
}


def _official(code: str) -> tuple[str, set[str] | None]:
    """中证官网(失败则国证)当前成分。返回 (名称, 代码集合) / ('ERR:...', None)"""
    try:
        import akshare as ak
        df = ak.index_stock_cons_csindex(symbol=code)
        col = [c for c in df.columns if "成分券代码" in c]
        if col:
            nm = str(df["指数名称"].iloc[0]) if "指数名称" in df.columns else "?"
            return nm, {str(x).zfill(6) for x in df[col[0]]}
    except Exception:
        pass
    try:
        import akshare as ak
        df = ak.index_stock_cons(symbol=code)
        c0 = [c for c in df.columns if "代码" in c][0]
        return f"国证{code}", {str(x).zfill(6) for x in df[c0]}
    except Exception as e:
        return f"ERR:{str(e)[:60]}", None


def verify(strict: bool = False) -> tuple[bool, list[str]]:
    """返回 (是否通过, 问题清单)。strict=True 时官方取数失败也计问题(网络差时 cron 用 False)。"""
    issues: list[str] = []
    if not LIVE.exists():
        return False, ["名单表不存在"]
    table = json.loads(LIVE.read_text(encoding="utf-8"))
    for name, rec in sorted(table.items(), key=lambda x: str(x[1].get("code"))):
        code = str(rec.get("code") or "")
        asof = rec.get("asof") or {}
        # C) 空点
        empties = [d for d, v in asof.items() if not v]
        if empties:
            issues.append(f"{name}({code}) 空快照 {len(empties)} 个, 最早 {min(empties)}")
            continue
        if not asof:
            continue
        latest = set(asof[max(asof)])
        nm, off = _official(code)
        if off is None:
            if strict:
                issues.append(f"{name}({code}) 官方成分取数失败: {nm}")
            continue
        # A) 重合
        ov = len(latest & off) / len(latest) * 100 if latest else 0
        if ov < 90:
            issues.append(f"{name}({code}) 官方成分重合仅 {ov:.0f}% (官方{len(off)}/表内{len(latest)})")
        # B) 名称
        kw = NAME_KEYWORD.get(name, "")
        if kw and kw not in nm and "国证" not in nm:
            issues.append(f"{name}({code}) 官方名称「{nm}」不含关键词「{kw}」→ 疑似映射错")
    return (not issues, issues)


def main() -> int:
    ok, issues = verify(strict=True)
    if ok:
        print("映射验证通过: 官方成分/名称/空点 全部一致")
        return 0
    print(f"发现 {len(issues)} 个问题:")
    for i in issues:
        print("  ⚠ " + i)
    return 1


if __name__ == "__main__":
    sys.exit(main())
