#!/usr/bin/env python3
"""抓取且慢每日估值数据（qieman.com/idx-eval）→ 存 JSON

页面是 canvas/CSR 渲染，但 innerText 包含全部数据（加载完成瞬间可读）。
策略: 打开页面 → 等待数据出现 → 提取 innerText → 解析成结构化 JSON。
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("/vol1/1000/openzl/etf_tool/cache/qieman_valuations.json")


def parse_eval_text(text: str) -> dict:
    """解析页面 innerText → 指数估值列表。
    格式(每指数): 名称\n代码\n(空行)\n值1\n(空行)\n值2... 共5个值: PE/PB,百分位,最高,最低,ROE
    """
    lines = [l.strip() for l in text.split("\n")]
    try:
        start = lines.index("指数名称")
    except ValueError:
        return {"error": "未找到数据区", "raw_len": len(lines)}

    body = lines[start + 1:]
    # 去掉所有空行，紧凑化（保留顺序）
    compact = [l for l in body if l]
    indices = []
    i = 0
    n = len(compact)
    while i < n:
        name = compact[i]
        # 名称后应该是代码(带 . 和交易所后缀)
        if i + 1 < n and re.match(r"^[A-Z0-9]+\.(SH|SZ|CSI|GI|HI|CNI)", compact[i + 1]):
            code = compact[i + 1]
            # 代码后的 5 个数值（可能不足，如 ROE 缺失）
            vals = []
            j = i + 2
            while j < n and len(vals) < 5:
                v = compact[j]
                if re.match(r"^-?\d+\.\d+$|^--$|^-$", v):
                    vals.append(v)
                else:
                    break
                j += 1
            if len(vals) >= 2:
                indices.append({
                    "name": name,
                    "code": code,
                    "value": float(vals[0]) if vals[0] not in ("--", "-") else None,
                    "percentile": float(vals[1]) if vals[1] not in ("--", "-") else None,
                    "high": float(vals[2]) if len(vals) > 2 and vals[2] not in ("--", "-") else None,
                    "low": float(vals[3]) if len(vals) > 3 and vals[3] not in ("--", "-") else None,
                    "roe": float(vals[4]) if len(vals) > 4 and vals[4] not in ("--", "-") else None,
                })
            i = j
        else:
            i += 1
    return {"indices": indices, "count": len(indices)}


def main() -> int:
    url = "https://qieman.com/idx-eval"
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path="/usr/bin/chromium",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        # 等待数据渲染
        for _ in range(15):
            time.sleep(1)
            text = page.evaluate("document.body.innerText")
            if "百分位" in text and len(text) > 500:
                break
        text = page.evaluate("document.body.innerText")
        print(f"[抓取] innerText len={len(text)}", flush=True)

        data = parse_eval_text(text)
        if data.get("count", 0) < 5:
            print(f"[抓取] 解析失败 count={data.get('count')}", flush=True)
            print(text[:800], flush=True)
            browser.close()
            return 1

        payload = {
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "qieman",
            "url": url,
            "note": "且慢每日估值（页面标注: 采用近10年数据，不满10年用全部历史；强周期用PB）",
            "indices": data["indices"],
            "count": data["count"],
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[抓取] ✅ 保存 {data['count']} 个指数 → {OUT}", flush=True)
        for idx in data["indices"][:8]:
            print(f"  {idx['name']}: val={idx['value']} pct={idx['percentile']}%", flush=True)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
