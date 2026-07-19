from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[2]
DATA = BASE / "data"
CACHE = BASE / "cache"
LONG_WIN_JSON = DATA / "long_win_positions.json"
SIGNALS_JSON = DATA / "signals.json"
SYNC_SCRIPT = BASE / "scripts" / "sync_long_win.py"
CACHE_FILE = CACHE / "long_win_live.json"
DEFAULT_TTL_SECONDS = 20 * 60


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cache(payload: dict[str, Any], source: str, ttl_seconds: int) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    wrapped = {
        "source": source,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ttl_seconds": ttl_seconds,
        "payload": payload,
    }
    CACHE_FILE.write_text(json.dumps(wrapped, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_cache(ttl_seconds: int) -> dict[str, Any] | None:
    if not CACHE_FILE.exists():
        return None
    wrapped = _load_json(CACHE_FILE, {})
    fetched_at = wrapped.get("fetched_at")
    try:
        fetched_ts = time.mktime(time.strptime(fetched_at, "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None
    if time.time() - fetched_ts > ttl_seconds:
        return None
    return wrapped


def _load_sync_module() -> Any:
    spec = importlib.util.spec_from_file_location("etf_sync_long_win", SYNC_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 sync_long_win.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _local_payload() -> dict[str, Any]:
    long_win = _load_json(LONG_WIN_JSON, {"generated_at": None, "plans": {}})
    signals = _load_json(SIGNALS_JSON, {"signals": [], "ed_actions": []})
    return {"long_win": long_win, "ed_actions": signals.get("ed_actions", [])}


def load_long_win(ttl_seconds: int = DEFAULT_TTL_SECONDS, force_refresh: bool = False) -> dict[str, Any]:
    if not force_refresh:
        cached = _read_cache(ttl_seconds)
        if cached:
            return {**cached["payload"], "data_source": "cache", "cache_meta": {k: cached.get(k) for k in ("source", "fetched_at", "ttl_seconds")}}
    try:
        module = _load_sync_module()
        summary = module.sync()
        payload = _local_payload()
        payload["sync_summary"] = summary
        _write_cache(payload, "live_supabase", ttl_seconds)
        return {**payload, "data_source": "live", "cache_meta": {"source": "live_supabase", "ttl_seconds": ttl_seconds}}
    except Exception as exc:
        payload = _local_payload()
        cached = _load_json(CACHE_FILE, None)
        if cached and cached.get("payload"):
            payload = cached["payload"]
            source = "stale_cache"
        else:
            source = "local_file"
        return {**payload, "data_source": source, "data_warning": f"联网同步失败，当前使用{source}：{str(exc)[:160]}"}
