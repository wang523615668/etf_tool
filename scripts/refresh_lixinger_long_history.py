#!/usr/bin/env python3
"""Force-refresh all index series with up to 20y history (chunked)."""
from __future__ import annotations
import json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.data_sources.lixinger import INDEX_CONFIG, fetch_index_series, build_valuation_snapshot
YEARS = 20

def main() -> int:
    force = False   # 增量：从缓存末日-5天续拉(秒级)。旧代码 force=True 全量重拉20年×25指数≈60min，被 timeout 900 掐死 → 数据常年卡旧值(2026-10-01 修复)
    print(f"[*] long history refresh years={YEARS} indices={len(INDEX_CONFIG)} (incremental)", flush=True)
    ok = fail = 0
    for name in INDEX_CONFIG:
        t0 = time.time()
        try:
            rows = fetch_index_series(name, force=force, years=YEARS, allow_network=True)
            span = f"{rows[0]['date']}->{rows[-1]['date']}" if rows else "empty"
            print(f"  OK {name}: n={len(rows)} {span} ({time.time()-t0:.1f}s)", flush=True)
            ok += 1
        except Exception as exc:
            print(f"  FAIL {name}: {exc}", flush=True)
            fail += 1
    snap = build_valuation_snapshot(force=True, allow_network=True)
    print(json.dumps({
        "series_ok": ok,
        "series_fail": fail,
        "snap_total": snap.get("total"),
        "snapshot_date": (snap.get("freshness") or {}).get("snapshot_date"),
    }, ensure_ascii=False), flush=True)
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
