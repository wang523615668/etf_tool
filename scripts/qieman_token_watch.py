#!/usr/bin/env python3
"""且慢 token 过期提醒：剩余 <3 天时输出告警（no_agent cron 推送）。

token 存储: data/qieman_token.json（或环境变量 QIEMAN_TOKEN）
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

BASE = Path("/vol1/1000/openzl/etf_tool")
TOKEN_FILE = BASE / "data" / "qieman_token.json"
EXPIRED_DAYS = 3  # 提前 3 天提醒


def main() -> int:
    # 读 token
    token = None
    if TOKEN_FILE.exists():
        try:
            d = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            token = d.get("token")
        except Exception:
            pass
    if not token:
        token = os.environ.get("QIEMAN_TOKEN")

    if not token:
        print("⚠️ 且慢 token 未配置（data/qieman_token.json 或 QIEMAN_TOKEN），无法自动续期！")
        return 0

    # 解析 JWT exp
    try:
        import base64
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if not exp:
            print("⚠️ token 无 exp 字段，无法判断有效期")
            return 0
        remain_days = (exp - datetime.now().timestamp()) / 86400
        if remain_days <= 0:
            print(f"⛔ 且慢 token 已过期（{datetime.fromtimestamp(exp).strftime('%m-%d')}）！需重新抓包。")
            return 0
        if remain_days <= EXPIRED_DAYS:
            print(f"⚠️ 且慢 token 剩余 {remain_days:.1f} 天到期（{datetime.fromtimestamp(exp).strftime('%m-%d %H:%M')}），请重新抓包！")
            return 0
        # 未到期，静默
        return 0
    except Exception as e:
        print(f"⚠️ 且慢 token 解析失败: {e}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
