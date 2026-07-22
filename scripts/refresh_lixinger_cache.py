#!/usr/bin/env python3
"""Daily/manual 理杏仁 cache refresh.

Web pages only read local cache (allow_network=False).
This script is the ONLY path that should call 理杏仁 API for valuations.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data_sources.lixinger import INDEX_CONFIG, build_valuation_snapshot, fetch_index_series  # noqa: E402


def main() -> int:
    force = "--force" in sys.argv
    print(f"[*] refresh lixinger cache force={force} indices={len(INDEX_CONFIG)}", flush=True)
    # series first so snapshot builds from fresh files
    ok = 0
    fail = 0
    for name in INDEX_CONFIG:
        try:
            rows = fetch_index_series(name, force=force, allow_network=True)
            print(f"  series {name}: {len(rows)} rows", flush=True)
            ok += 1
        except Exception as exc:
            print(f"  series FAIL {name}: {exc}", flush=True)
            fail += 1
    snap = build_valuation_snapshot(force=True, allow_network=True)
    out = {
        "total": snap.get("total"),
        "data_source": snap.get("data_source"),
        "freshness": snap.get("freshness"),
        "series_ok": ok,
        "series_fail": fail,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if ok and snap.get("rows") else 1


if __name__ == "__main__":
    raise SystemExit(main())
