#!/usr/bin/env bash
# 每日抓取且慢估值数据（19:30 执行，且慢页面 20:00 更新前抓当天数据）
cd /vol1/1000/openzl/etf_tool
/usr/bin/python3 scripts/fetch_qieman_valuations.py 2>&1