#!/bin/bash
cd /vol1/1000/openzl/etf_tool
V=./.venv/bin/python
L=/root/.hermes/cache/scratch
pkill -f "ts_export_all.py weight_early" 2>/dev/null
pkill -f ts_jobs_driver.sh 2>/dev/null
sleep 2
echo "=== 并行早期权重补全 $(date +%H:%M:%S) ===" >> $L/tspar.out
$V scripts/ts_export_par.py weight_early >> $L/tspar.out 2>&1
echo "=== 并行基金持仓 $(date +%H:%M:%S) ===" >> $L/tspar.out
$V scripts/ts_export_par.py funds >> $L/tspar.out 2>&1
echo "=== 并行资金流 $(date +%H:%M:%S) ===" >> $L/tspar.out
$V scripts/ts_export_par.py flows >> $L/tspar.out 2>&1
echo "=== 全部完成 $(date +%H:%M:%S) ===" >> $L/tspar.out
