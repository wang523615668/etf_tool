#!/bin/bash
# 串行驱动: 等主抓取结束 → 早期权重 → 基金持仓 → 资金流/指数估值
cd /vol1/1000/openzl/etf_tool
V=./.venv/bin/python
L=/root/.hermes/cache/scratch
while pgrep -f "ts_export_weight.py" > /dev/null; do sleep 20; done
echo "=== 主抓取结束, 开始早期权重补全 $(date +%H:%M) ===" | tee -a $L/tsjobs.out
$V scripts/ts_export_all.py weight_early >> $L/tsjobs.out 2>&1
echo "=== 早期权重完成, 开始基金持仓 $(date +%H:%M) ===" | tee -a $L/tsjobs.out
$V scripts/ts_export_all.py funds >> $L/tsjobs.out 2>&1
echo "=== 基金持仓完成, 开始资金流 $(date +%H:%M) ===" | tee -a $L/tsjobs.out
$V scripts/ts_export_all.py flows >> $L/tsjobs.out 2>&1
echo "=== 全部完成 $(date +%H:%M) ===" | tee -a $L/tsjobs.out
