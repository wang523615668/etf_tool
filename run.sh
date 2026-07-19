#!/usr/bin/env bash
set -euo pipefail
cd /vol1/1000/openzl/etf_tool
exec ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8888
