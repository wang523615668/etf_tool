#!/usr/bin/env python3
"""每日估值日报：生成估值温度图 + 文字摘要，输出 MEDIA: 供 no_agent cron 投递微信。

用法: /usr/bin/python3 daily_valuation_push.py
输出: stdout = 文字 + MEDIA:<png>；无数据时输出告警退出码 1。
数据源: 本地 etf_tool API（理杏仁/且慢估值缓存 + 决策引擎）。
"""
import json
import os
import sys
import urllib.request
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle

# 中文字体
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
if os.path.exists(FONT):
    font_manager.fontManager.addfont(FONT)
    plt.rcParams["font.family"] = "WenQuanYi Zen Hei"
plt.rcParams["axes.unicode_minus"] = False

BASE = os.environ.get("ETF_TOOL_API", "http://127.0.0.1:8888")
OUT_DIR = "/vol1/1000/openzl/etf_tool/daily_outputs"

ACTION_META = {
    "buy": ("低估", "#2ecc71"),
    "watch": ("观察", "#f1c40f"),
    "hold": ("持有", "#3498db"),
    "reduce": ("减仓", "#e74c3c"),
    "pause": ("暂停", "#95a5a6"),
}


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def temp_color(t: float) -> str:
    """温度 0-100 → 绿(低) 黄(中) 红(高)"""
    t = max(0.0, min(100.0, t))
    if t < 33:
        return "#2ecc71"
    if t < 66:
        return "#f1c40f"
    return "#e74c3c"


def render_chart(rows: list[dict], out_png: str) -> None:
    rows = sorted(rows, key=lambda r: float(r.get("temperature") or 0))
    n = len(rows)
    fig_h = max(6.0, n * 0.42 + 1.6)
    fig, ax = plt.subplots(figsize=(7.4, fig_h), dpi=130)
    ax.set_facecolor("#0f1420")
    fig.patch.set_facecolor("#0f1420")

    y = 0
    for r in rows:
        name = r.get("name") or "?"
        t = float(r.get("temperature") or 0)
        pe_pct = r.get("pe_percentile")
        pb_pct = r.get("pb_percentile")
        pe_s = f"{pe_pct*100:.0f}%" if pe_pct is not None else "-"
        pb_s = f"{pb_pct*100:.0f}%" if pb_pct is not None else "-"
        act = r.get("action") or "watch"
        act_s, _ = ACTION_META.get(act, ("", "#95a5a6"))
        color = temp_color(t)

        # 背景轨道 + 温度条
        ax.add_patch(Rectangle((0, y + 0.12), 100, 0.76, facecolor="#1c2536", edgecolor="none"))
        ax.add_patch(Rectangle((0, y + 0.12), t, 0.76, facecolor=color, edgecolor="none", alpha=0.92))
        # 名称
        ax.text(-2.0, y + 0.5, f"{name}", ha="right", va="center", fontsize=10.5,
                color="#e6edf3", fontweight="bold")
        # 动作标记（纯文字，避免 emoji 缺字形）
        if act_s:
            ax.text(-2.0 - max(len(name) * 1.05, 20), y + 0.5, act_s,
                    ha="right", va="center", fontsize=9.5, color="#8b949e")
        # 温度值
        ax.text(t + 1.5, y + 0.5, f"{t:.0f}°", ha="left", va="center", fontsize=11,
                color=color, fontweight="bold")
        # PE/PB 分位
        ax.text(100.0, y + 0.5, f"PE {pe_s}  PB {pb_s}", ha="right", va="center",
                fontsize=8.5, color="#8b949e")
        y += 1

    ax.set_xlim(-2.0 - max(len(r.get("name") or "") for r in rows) * 1.05 - 4.0, 112)
    ax.set_ylim(-0.4, n + 0.4)
    ax.axis("off")
    fig.tight_layout(pad=0.4)
    fig.savefig(out_png, facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        v = get("/api/valuations")
        d = get("/api/decision")
    except Exception as e:
        print(f"⚠️ 估值日报生成失败: {e}")
        return 1

    rows = v.get("rows") or []
    if not rows:
        print("⚠️ 估值日报：估值数据为空，跳过推送")
        return 1

    freshness = v.get("freshness") or {}
    snap = freshness.get("snapshot_date") or "?"
    src = v.get("data_source") or freshness.get("source") or "?"
    note = freshness.get("note") or freshness.get("warning") or ""

    # 决策行（含 ed_hint）
    d_rows = {r.get("name"): r for r in (d.get("rows") or [])}

    # 文字摘要
    now = datetime.now().strftime("%m-%d %H:%M")
    lines = [f"📊 估值日报 {now}"]
    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"数据截至 {snap} · 源: {src}")

    for act, label in [("buy", "低估/买入"), ("hold", "持有"), ("reduce", "减仓/卖出"), ("watch", "观察")]:
        sub = [r for r in rows if (r.get("action") or "watch") == act]
        if not sub:
            continue
        items = []
        for r in sorted(sub, key=lambda x: float(x.get("temperature") or 0)):
            nm = r.get("name") or "?"
            t = float(r.get("temperature") or 0)
            hint = ""
            dr = d_rows.get(nm)
            if dr and dr.get("ed_hint"):
                h = dr["ed_hint"]
                hint = f" · {h[:22]}{'…' if len(h) > 22 else ''}"
            items.append(f"{nm} {t:.0f}°{hint}")
        lines.append(f"{label} ({len(items)}):")
        lines.append("  " + " / ".join(items[:8]))

    lines.append("━━━━━━━━━━━━━━")
    # 最低/最高温度
    ts = sorted(rows, key=lambda r: float(r.get("temperature") or 0))
    lines.append(f"🧊 最低: {ts[0]['name']} {float(ts[0].get('temperature') or 0):.0f}° · 🔥 最高: {ts[-1]['name']} {float(ts[-1].get('temperature') or 0):.0f}°")
    if note and "fallback" in str(note):
        lines.append(f"ℹ️ {note[:50]}")
    lines.append("（完整数据看 etf 网站 🤖自主决策）")

    # 生成图片
    png = os.path.join(OUT_DIR, f"valuation_daily_{datetime.now().strftime('%Y%m%d')}.png")
    try:
        render_chart(rows, png)
    except Exception as e:
        print("⚠️ 估值图生成失败: " + str(e))
        # 图片失败仍推文字
        print("\n".join(lines))
        return 0

    print("\n".join(lines))
    print(f"MEDIA:{png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
