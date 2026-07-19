#!/usr/bin/env python3
"""Archive E大发车 article bodies from content.qieman.com/items/{id}.

Source of truth for operation reasons is the article SPA:
  https://content.qieman.com/items/<itemId>
which embeds full article HTML in __NEXT_DATA__.props.pageProps.item.article.

Merges into data/ed_talks.json so /api compare_signals can exact-match by URL.
Community posts (content-detail?postId=) remain untouched.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ARCHIVE = DATA / "ed_talks.json"
SIGNALS = DATA / "signals.json"
ARTICLES = DATA / "ed_articles.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
ITEM_RE = re.compile(r"/items/(\d+)")


def fetch_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json",
            "Referer": "https://qieman.com/",
        },
    )
    errors: list[str] = []
    for proxy in (None, "http://127.0.0.1:7890"):
        try:
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                )
            else:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{proxy or 'direct'}: {exc}")
    raise RuntimeError("; ".join(errors))


def strip_html(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>|</div>|</section>|</h[1-6]>|</li>|</tr>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def summarize_reason(text: str) -> dict[str, Any]:
    needles = ["买入", "卖出", "估值", "仓位", "低估", "高估", "风险", "计划", "长赢", "份", "调仓", "发车"]
    # Prefer line-based extraction for 发车调仓清单
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    op_lines = [ln for ln in lines if any(n in ln for n in ("买入", "卖出", "调仓", "份"))]
    sentences = re.split(r"(?<=[。！？；\n])", text)
    picked = [s.strip() for s in sentences if any(n in s for n in needles) and len(s.strip()) > 2][:8]
    if op_lines:
        reason = "\n".join(op_lines)[:500]
        evidence = op_lines[:5]
    else:
        reason = "".join(picked)[:420] if picked else text[:260]
        evidence = picked[:3]
    return {
        "operation_reason": reason or "暂未从正文中抽取到明确原因",
        "reason_evidence": evidence,
        "valuation_context": "低估/高估/估值" if any(k in text for k in ("估值", "低估", "高估")) else "未明确",
        "position_context": "仓位/份数/计划" if any(k in text for k in ("仓位", "份", "计划", "调仓")) else "未明确",
        "risk_note": next((s.strip() for s in sentences if "风险" in s), ""),
    }


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def item_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = ITEM_RE.search(url)
    return m.group(1) if m else None


def collect_item_meta(signals: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """item_id -> {dates, sample_urls, fund_names, actions}"""
    meta: dict[str, dict[str, Any]] = {}
    for action in signals.get("ed_actions") or []:
        iid = item_id_from_url(action.get("url"))
        if not iid:
            continue
        row = meta.setdefault(
            iid,
            {
                "item_id": iid,
                "dates": set(),
                "urls": set(),
                "names": set(),
                "actions": set(),
            },
        )
        if action.get("date"):
            row["dates"].add(str(action["date"])[:10])
        if action.get("url"):
            row["urls"].add(action["url"])
        if action.get("name"):
            row["names"].add(action["name"])
        if action.get("action"):
            row["actions"].add(str(action["action"]))
    # freeze sets
    out: dict[str, dict[str, Any]] = {}
    for iid, row in meta.items():
        dates = sorted(row["dates"])
        out[iid] = {
            "item_id": iid,
            "dates": dates,
            "date": dates[-1] if dates else None,
            "urls": sorted(row["urls"]),
            "names": sorted(row["names"]),
            "actions": sorted(row["actions"]),
        }
    return out


def fetch_article(item_id: str) -> dict[str, Any]:
    content_url = f"https://content.qieman.com/items/{item_id}"
    html = fetch_text(content_url)
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">([\s\S]*?)</script>',
        html,
    )
    if not m:
        raise RuntimeError(f"item {item_id}: missing __NEXT_DATA__")
    data = json.loads(m.group(1))
    item = (data.get("props") or {}).get("pageProps", {}).get("item") or {}
    art = item.get("article") or {}
    content_html = art.get("content") or ""
    text = strip_html(content_html)
    if not text and not art.get("title"):
        raise RuntimeError(f"item {item_id}: empty article body")
    summary = summarize_reason(text)
    qieman_url = f"https://qieman.com/content/items/{item_id}"
    return {
        "id": str(item.get("itemId") or item_id),
        "item_id": str(item.get("itemId") or item_id),
        "item_type": item.get("itemType"),
        "title": art.get("title") or f"发车文章 {item_id}",
        "summary": art.get("summary") or "",
        "author": art.get("authorName") or "ETF拯救世界",
        "cover_url": art.get("coverUrl"),
        "like_count": item.get("likeCount"),
        "comment_count": item.get("commentCount"),
        "collect_count": item.get("collectCount"),
        "get_count": item.get("getCount"),
        "url": qieman_url,
        "content_url": content_url,
        "alt_urls": [
            qieman_url,
            content_url,
            f"https://qieman.com/content/items/{item_id}?preview=1",
            f"https://content.qieman.com/items/{item_id}?preview=1",
        ],
        "text_full": text,
        "text_excerpt": text[:800],
        "operation_reason": summary["operation_reason"],
        "reason_evidence": summary["reason_evidence"],
        "valuation_context": summary["valuation_context"],
        "position_context": summary["position_context"],
        "risk_note": summary["risk_note"],
        "source": "qieman_content_item",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def merge_into_ed_talks(articles: list[dict[str, Any]], archive: dict[str, Any]) -> dict[str, Any]:
    items = list(archive.get("items") or [])
    by_key: dict[str, dict[str, Any]] = {}
    for it in items:
        # index existing by id and by item path
        if it.get("id"):
            by_key[f"id:{it['id']}"] = it
        iid = item_id_from_url(it.get("url"))
        if iid:
            by_key[f"item:{iid}"] = it
        if it.get("item_id"):
            by_key[f"item:{it['item_id']}"] = it

    merged = 0
    for art in articles:
        iid = str(art.get("item_id") or art.get("id"))
        existing = by_key.get(f"item:{iid}")
        talk = {
            "id": iid,
            "item_id": iid,
            "title": art.get("title"),
            "date": art.get("date"),
            "url": art.get("url"),
            "content_url": art.get("content_url"),
            "alt_urls": art.get("alt_urls") or [],
            "operation_reason": art.get("operation_reason"),
            "reason_evidence": art.get("reason_evidence") or [],
            "valuation_context": art.get("valuation_context"),
            "position_context": art.get("position_context"),
            "risk_note": art.get("risk_note") or "",
            "text_excerpt": art.get("text_excerpt") or "",
            "text_full": art.get("text_full") or "",
            "summary": art.get("summary") or "",
            "author": art.get("author"),
            "source": "qieman_content_item",
            "fetched_at": art.get("fetched_at"),
            "like_count": art.get("like_count"),
            "get_count": art.get("get_count"),
        }
        if existing is None:
            items.append(talk)
            by_key[f"item:{iid}"] = talk
            by_key[f"id:{iid}"] = talk
            merged += 1
        else:
            # prefer article body over community weak text when same key
            if existing.get("source") != "qieman_content_item" and art.get("text_full"):
                # keep community post as separate id; article is new id (item id)
                items.append(talk)
                by_key[f"item:{iid}"] = talk
                merged += 1
            else:
                existing.update({k: v for k, v in talk.items() if v not in (None, "", [], {})})
                merged += 1

    # sort: articles first by date desc, then rest
    def sort_key(x: dict[str, Any]) -> tuple:
        is_art = 0 if x.get("source") == "qieman_content_item" else 1
        return (is_art, str(x.get("date") or ""), str(x.get("id") or ""))

    items_sorted = sorted(items, key=sort_key, reverse=True)
    # reverse=True puts latest dates first but is_art 1 before 0 — fix:
    items_sorted = sorted(
        items,
        key=lambda x: (
            0 if x.get("source") == "qieman_content_item" else 1,
            # date desc within group
            str(x.get("date") or "0000-00-00"),
            str(x.get("id") or ""),
        ),
        reverse=False,
    )
    # Within articles put newest first
    arts = [x for x in items if x.get("source") == "qieman_content_item"]
    posts = [x for x in items if x.get("source") != "qieman_content_item"]
    arts = sorted(arts, key=lambda x: (str(x.get("date") or ""), str(x.get("id") or "")), reverse=True)
    posts = sorted(posts, key=lambda x: (str(x.get("date") or ""), str(x.get("id") or "")), reverse=True)
    items = arts + posts

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(items),
        "article_count": len(arts),
        "post_count": len(posts),
        "items": items,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive E大发车 article bodies")
    ap.add_argument("--limit", type=int, default=0, help="max items to fetch (0=all)")
    ap.add_argument("--since", default="", help="only items with action date >= YYYY-MM-DD")
    ap.add_argument("--sleep", type=float, default=0.35, help="sleep between requests")
    ap.add_argument("--force", action="store_true", help="refetch even if already archived")
    args = ap.parse_args()

    signals = load_json(SIGNALS, {"ed_actions": []})
    meta = collect_item_meta(signals)
    if args.since:
        meta = {
            k: v
            for k, v in meta.items()
            if any(d >= args.since for d in (v.get("dates") or []))
            or (v.get("date") or "") >= args.since
        }
    item_ids = sorted(meta.keys(), key=lambda x: int(x), reverse=True)
    if args.limit and args.limit > 0:
        item_ids = item_ids[: args.limit]

    existing_articles = load_json(ARTICLES, {"items": []})
    existing_by_id = {
        str(it.get("item_id") or it.get("id")): it for it in (existing_articles.get("items") or [])
    }

    print(f"[*] unique content/items to fetch: {len(item_ids)} (from signals)", flush=True)
    ok: list[dict[str, Any]] = []
    fail: list[dict[str, Any]] = []
    skipped = 0

    for i, iid in enumerate(item_ids, 1):
        if not args.force and iid in existing_by_id and existing_by_id[iid].get("text_full"):
            art = dict(existing_by_id[iid])
            art.setdefault("date", meta[iid].get("date"))
            ok.append(art)
            skipped += 1
            print(f"[{i}/{len(item_ids)}] skip cached {iid}", flush=True)
            continue
        try:
            art = fetch_article(iid)
            art["date"] = meta[iid].get("date")
            art["related_funds"] = meta[iid].get("names") or []
            art["related_actions"] = meta[iid].get("actions") or []
            # index all action urls observed
            for u in meta[iid].get("urls") or []:
                if u not in (art.get("alt_urls") or []):
                    art.setdefault("alt_urls", []).append(u)
            ok.append(art)
            print(
                f"[{i}/{len(item_ids)}] OK {iid} title={art.get('title')!r} "
                f"text_len={len(art.get('text_full') or '')} date={art.get('date')}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            fail.append({"item_id": iid, "error": str(exc)})
            print(f"[{i}/{len(item_ids)}] FAIL {iid}: {exc}", flush=True)
        time.sleep(max(0.0, args.sleep))

    # keep previously cached articles not in this run
    kept = []
    seen = {str(a.get("item_id") or a.get("id")) for a in ok}
    for old in existing_articles.get("items") or []:
        oid = str(old.get("item_id") or old.get("id"))
        if oid not in seen:
            kept.append(old)
    all_articles = ok + kept

    ARTICLES.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "total": len(all_articles),
                "fetched_ok": len(ok) - skipped,
                "cached_skip": skipped,
                "failed": fail,
                "items": all_articles,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    archive = load_json(ARCHIVE, {"items": []})
    merged = merge_into_ed_talks(ok, archive)
    ARCHIVE.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"=== done: articles_ok={len(ok)} new_or_refetched={len(ok)-skipped} "
        f"cached_skip={skipped} fail={len(fail)} "
        f"ed_talks_total={merged['total']} article_count={merged['article_count']} ===",
        flush=True,
    )
    if fail:
        print("failures:", json.dumps(fail[:10], ensure_ascii=False), flush=True)
    return 0 if not fail or ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
