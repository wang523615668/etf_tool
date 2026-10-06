#!/usr/bin/env python3
"""每日决策简报：读取决策引擎，生成微信推送文本。

输出 stdout（no_agent cron 直接推送）：简短决策摘要
"""
import json
import urllib.request
from datetime import datetime

BASE = "http://127.0.0.1:8888"


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    try:
        # 理杏仁决策
        lx = get("/api/decision?source=lixinger")
        # 且慢决策
        qm = get("/api/decision?source=qieman")
    except Exception as e:
        print(f"⚠️ 决策简报生成失败: {e}")
        return 1

    rows_lx = lx.get("rows", [])
    rows_qm = qm.get("rows", [])

    # 买入/可买入/持有/减仓分类（理杏仁）
    buy = [r for r in rows_lx if r.get("decision") in ("买入", "可买入")]
    hold = [r for r in rows_lx if r.get("decision") == "持有"]
    reduce = [r for r in rows_lx if r.get("decision") in ("卖出", "减仓")]
    # 且慢买入
    qm_buy = [r for r in rows_qm if r.get("decision") in ("买入", "可买入")]
    qm_reduce = [r for r in rows_qm if r.get("decision") in ("卖出", "减仓")]

    lines = [f"📊 自主决策简报 {datetime.now().strftime('%m-%d %H:%M')}"]
    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"🔥 买入候选（理杏仁 {len(buy)}）:")
    if buy:
        for r in buy[:8]:
            tag = "🔥" if r.get("confidence") == "高" else ""
            lines.append(f"  {tag}{r['name']} {r['temperature']}° {r.get('ed_hint','')[:20]}")
    else:
        lines.append("  无")
    lines.append(f"📌 持有参考（{len(hold)}）:")
    for r in hold[:5]:
        lines.append(f"  {r['name']} {r['temperature']}°")
    lines.append(f"⚠️ 减仓/卖出（{len(reduce)}）:")
    for r in reduce[:5]:
        lines.append(f"  {r['name']} {r['temperature']}°")
    lines.append("━━━━━━━━━━━━━━")
    # 击球分数（估值40+情绪30+动量30）
    try:
        bt = get("/api/batter-score")
        brows = [r for r in (bt.get("rows") or []) if r.get("total_score") is not None]
        actionable = [r for r in brows if "买入" in str(r.get("stance", "")) or "试探" in str(r.get("stance", ""))]
        watchlist = [r for r in brows if "未恐慌" in str(r.get("stance", "")) or "拐点" in str(r.get("stance", ""))]
        lines.append(f"🎯 击球分数 TOP5:")
        for r in brows[:5]:
            lines.append(f"  {r['name']} 总{r['total_score']}（估{r['value_score']}/情{r['sentiment_score']}/动{r['momentum_score']}）{r['stance']}")
        if actionable:
            lines.append(f"⚡ 可执行: {'、'.join(r['name'] for r in actionable[:6])}")
        if watchlist:
            lines.append(f"👁 观察池(等情绪/动量确认): {'、'.join(r['name'] for r in watchlist[:6])}")
        lines.append("━━━━━━━━━━━━━━")
    except Exception:
        pass
    lines.append(f"🌐 且慢10年口径：买入{len(qm_buy)} / 减仓{len(qm_reduce)}")
    for r in qm_buy[:5]:
        lines.append(f"  {r['name']} {r['temperature']}°")
    lines.append("（完整看 etf 网站 🤖自主决策）")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
