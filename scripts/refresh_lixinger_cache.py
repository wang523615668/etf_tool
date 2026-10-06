#!/usr/bin/env python3
"""理杏仁缓存刷新（配额感知版）。

背景与约束
----------
理杏仁开放接口按「调用次数 / 访问时长」计数，token 池里既有免费层也有按次购买的额度，
用户口径是 **总共 1000 次估值数据调用**，必须省着用。历史问题：
- 旧版每次把 **24 个指数全部刷一遍**（含 20 年历史的冷启动），一次运行可吃掉上百次调用；
- 免费层 token 用完后仍继续对每个指数重试、轮换 token，最终撞 600s 超时（无产出）；
- 没有任何用量账本，无法回答「还剩多少次」。

策略
----
1. **分层**：`CORE_INDICES` 每个交易日都刷；其余指数按游标轮转（cursor 持久化，多天覆盖一轮）。
2. **硬预算**：每次运行最多刷新 `--budget` 个指数（默认 12），到顶即停，绝不超额。
3. **早退**：token 池当日全部耗尽时立即退出（缓存继续服务），不空转、不撞超时。
4. **账本**：`data/lixinger_call_usage.json` 记录累计/按日用量与轮转游标，随时可查「已用 X 次」。
5. **只在真正需要时打网络**：缓存已新鲜（< SERIES_TTL 且数据不落后于 STALE_DAYS）的指数会被跳过。

用法
----
    python3 scripts/refresh_lixinger_cache.py                # 正常刷新（核心 + 轮转）
    python3 scripts/refresh_lixinger_cache.py --budget 3     # 只刷核心前 3 个（极省）
    python3 scripts/refresh_lixinger_cache.py --dry-run      # 只打印计划，不调接口
    python3 scripts/refresh_lixinger_cache.py --all          # 忽略分层，按预算刷全部（手动补数据用）
    python3 scripts/refresh_lixinger_cache.py --force        # 透传给 fetch_index_series（强制网络）
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data_sources.lixinger import (  # noqa: E402
    CACHE_DIR,
    FETCH_CHUNK_DAYS,
    HISTORY_YEARS,
    INDEX_CONFIG,
    SERIES_TTL,
    STALE_DAYS,
    build_valuation_snapshot,
    fetch_index_series,
    _cache_metric_mismatch,
    _series_path,
)

# 核心指数：每个交易日必刷（名字需与 INDEX_CONFIG 的键一致）
CORE_INDICES = [
    "沪深300",
    "中证500",
    "红利低波",
    "红利低波100",
    "中证红利",
    "A股全指",
    "恒生指数",
    "中概互联",
]

LEDGER = ROOT / "data" / "lixinger_call_usage.json"
DEFAULT_BUDGET = 8  # 核心 8 个/交易日 → 1000 次可撑约 125 个交易日（半年）


# ---------------------------------------------------------------- 账本
def load_ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except Exception:
        return {"total_calls": 0, "days": {}, "cursor": 0}


def save_ledger(d: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    d["updated_at"] = datetime.now().isoformat(timespec="seconds")
    LEDGER.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def ledger_summary(d: dict) -> str:
    today = date.today().isoformat()
    used_today = (d.get("days") or {}).get(today, {}).get("calls", 0)
    return f"累计 {d.get('total_calls', 0)} 次｜今日 {used_today} 次｜轮转游标 {d.get('cursor', 0)}"


# ---------------------------------------------------------------- 调用估算
def est_calls(name: str) -> int:
    """估算刷新该指数要花几次调用。

    缓存命中且口径未变 = 1 次（增量拉最近一段）；冷启动/口径变更 ≈ 20 年 ÷ 730 天分块。
    """
    cfg = INDEX_CONFIG.get(name) or {}
    path = _series_path(cfg.get("code", ""))
    if not path.exists():
        return int(math.ceil(HISTORY_YEARS * 365.25 / FETCH_CHUNK_DAYS)) + 1
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return int(math.ceil(HISTORY_YEARS * 365.25 / FETCH_CHUNK_DAYS)) + 1
    if _cache_metric_mismatch(payload, cfg.get("pe", ""), cfg.get("pb", "")):
        return int(math.ceil(HISTORY_YEARS * 365.25 / FETCH_CHUNK_DAYS)) + 1
    return 1


def cache_fresh(name: str) -> bool:
    """缓存是否已经新鲜到不需要再打网络。"""
    cfg = INDEX_CONFIG.get(name) or {}
    path = _series_path(cfg.get("code", ""))
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    rows = payload.get("rows") or []
    if not rows or _cache_metric_mismatch(payload, cfg.get("pe", ""), cfg.get("pb", "")):
        return False
    ts = float(payload.get("fetched_ts") or 0)
    last = max((str(r.get("date") or "") for r in rows), default="")
    fresh_ts = bool(ts) and time.time() - ts < SERIES_TTL
    fresh_data = last >= (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    return fresh_ts and fresh_data


# ---------------------------------------------------------------- token 早退
def all_tokens_exhausted_today() -> bool:
    """token 池当日全部 403 耗尽 → 直接退出，避免空转超时。"""
    try:
        from app.data_sources.lixinger import _load_token_pool, _load_token_state

        pool = _load_token_pool()
        if not pool:
            return False
        state = _load_token_state()
        failed = state.get("failed") or {}
        today = date.today().isoformat()
        exhausted = 0
        for tok in pool:
            meta = failed.get(tok[:8]) or failed.get(tok) or {}
            if meta.get("exhausted") and meta.get("day") == today:
                exhausted += 1
        return exhausted >= len(pool)
    except Exception:
        return False


# ---------------------------------------------------------------- 选目标
def pick_targets(budget: int, use_all: bool, ledger: dict, force: bool = False) -> tuple[list[str], int, list[str]]:
    """返回 (本次要刷的指数名, 新的游标, 被跳过的已新鲜指数)。

    force=True 时不做新鲜度过滤（手动补数据场景）。
    """
    if force:
        use_all = use_all or False
    all_names = list(INDEX_CONFIG.keys())
    skipped_fresh: list[str] = []
    def _stale(n: str) -> bool:
        return True if force else (not cache_fresh(n))

    if use_all:
        pending = [n for n in all_names if _stale(n)]
        skipped_fresh = [n for n in all_names if n not in pending]
        return pending[:budget], ledger.get("cursor", 0), skipped_fresh

    core = [n for n in CORE_INDICES if n in INDEX_CONFIG]
    core_pending = [n for n in core if _stale(n)]
    skipped_fresh = [n for n in core if n not in core_pending]

    rest = [n for n in all_names if n not in core]
    cursor = int(ledger.get("cursor", 0)) % max(len(rest), 1)
    rotated = rest[cursor:] + rest[:cursor]
    rotated_pending = [n for n in rotated if _stale(n)]

    room = max(budget - len(core_pending), 0)
    # 预算不足时先保核心（核心列表在前），多余的截断
    chosen = (core_pending + rotated_pending[:room])[:budget]
    chosen_set = set(chosen)
    advanced = sum(1 for n in rotated_pending[:room] if n in chosen_set)
    new_cursor = (cursor + advanced) % max(len(rest), 1)
    return chosen, new_cursor, skipped_fresh


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="本次最多刷新多少个指数（默认 12）")
    ap.add_argument("--force", action="store_true", help="透传 force 给 fetch_index_series")
    ap.add_argument("--all", dest="use_all", action="store_true", help="忽略分层，按预算刷全部指数")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不调接口")
    args = ap.parse_args()

    ledger = load_ledger()
    targets, new_cursor, skipped_fresh = pick_targets(args.budget, args.use_all, ledger, force=args.force)
    est = sum(est_calls(n) for n in targets)

    print(
        f"[*] 理杏仁刷新 budget={args.budget} 目标={len(targets)} 个 预估调用≈{est} 次 "
        f"（{ledger_summary(ledger)}；已新鲜跳过 {len(skipped_fresh)} 个）",
        flush=True,
    )
    print(f"    计划: {', '.join(targets) if targets else '（无，全部已新鲜）'}", flush=True)
    if args.dry_run:
        return 0
    if not targets:
        print("[*] 无需刷新，退出", flush=True)
        return 0

    if all_tokens_exhausted_today():
        print("[*] token 池今日额度已全部耗尽，跳过刷新（缓存继续服务）", flush=True)
        return 0

    ok, fail, spent = 0, 0, 0
    for name in targets:
        cost = est_calls(name)
        try:
            rows = fetch_index_series(name, force=args.force, years=HISTORY_YEARS, allow_network=True)
            print(f"  ✓ {name}: {len(rows)} rows (≈{cost} 次调用)", flush=True)
            ok += 1
            spent += cost
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {name}: {exc}", flush=True)
            fail += 1
            spent += 1  # 失败也占一次尝试额度
            if all_tokens_exhausted_today():
                print("[!] token 池已耗尽，提前结束本次刷新", flush=True)
                break

    # 快照只在核心刷新成功后重建，避免为了快照再多打接口
    snap = {}
    if ok:
        try:
            snap = build_valuation_snapshot(force=True, allow_network=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! snapshot 失败: {exc}", flush=True)

    today = date.today().isoformat()
    days = ledger.setdefault("days", {})
    rec = days.setdefault(today, {"calls": 0, "indices": []})
    rec["calls"] = int(rec.get("calls", 0)) + spent
    rec["indices"] = sorted(set(list(rec.get("indices") or []) + targets))
    ledger["total_calls"] = int(ledger.get("total_calls", 0)) + spent
    ledger["cursor"] = new_cursor
    # 只保留最近 60 天明细，避免账本无限膨胀
    for d in sorted(days.keys())[:-60]:
        days.pop(d, None)
    save_ledger(ledger)

    out = {
        "targets": len(targets),
        "ok": ok,
        "fail": fail,
        "est_calls_spent": spent,
        "ledger": ledger_summary(ledger),
        "snapshot_total": snap.get("total"),
        "snapshot_freshness": snap.get("freshness"),
        "next_cursor": new_cursor,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
