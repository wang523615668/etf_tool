#!/usr/bin/env python3
"""Build per-index knowledge packs: related articles + E大 buy/sell marks.

Sources:
- qieman markdown archive + ed_articles/ed_talks
- chinaetfs_archive.json
- topic_index.json (multi-label)
- long_win / signals ed_actions (buy/sell points)

Output:
  data/index_knowledge.json
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "index_knowledge.json"

QIEMAN_ROOT = Path("/vol1/1000/openzl/qieman_etf")
CHINA_JSON = QIEMAN_ROOT / "chinaetfs_archive" / "chinaetfs_archive.json"
TOPIC_JSON = QIEMAN_ROOT / "topic整理" / "topic_index.json"
if not TOPIC_JSON.exists():
    TOPIC_JSON = DATA / "topic_index.json"
MD_DIR = QIEMAN_ROOT / "markdown"

# dashboard index name -> matching keywords / long-win categories
INDEX_KEYWORDS: dict[str, dict[str, Any]] = {
    "沪深300": {
        "keywords": ["沪深300", "300价值", "300指数", "大盘价值", "HS300"],
        "categories": ["沪深300"],
        "name_tokens": ["沪深300", "300增强", "富国沪深300"],
    },
    "中证500": {
        "keywords": ["中证500", "500质量", "500指数", "中证500增强"],
        "categories": ["中证500"],
        "name_tokens": ["中证500", "500增强", "建信中证500", "华夏中证500", "广发中证500"],
    },
    "上证50": {
        "keywords": ["上证50", "50指数", "大盘蓝筹"],
        "categories": ["上证50"],
        "name_tokens": ["上证50", "华夏上证50"],
    },
    "创业板指": {
        "keywords": ["创业板", "创业板指", "创业板指数", "双创"],
        "categories": ["创业板"],
        "name_tokens": ["创业板", "广发创业板"],
    },
    "科创50": {
        "keywords": ["科创50", "科创板", "科创"],
        "categories": ["科创", "科技"],
        "name_tokens": ["科创50", "科创"],
    },
    "中证红利": {
        "keywords": ["中证红利", "红利指数", "高股息", "分红"],
        "categories": ["红利低波"],
        "name_tokens": ["中证红利", "红利指数增强", "富国中证红利"],
    },
    "红利低波": {
        "keywords": ["红利低波", "低波红利", "大盘红利低波", "红利低波50"],
        "categories": ["红利低波"],
        "name_tokens": ["红利低波", "大盘红利低波", "南方标普中国A股大盘红利低波"],
    },
    "红利低波100": {
        "keywords": ["红利低波100", "低波100"],
        "categories": ["红利低波"],
        "name_tokens": ["红利低波100"],
    },
    "中证白酒": {
        "keywords": ["白酒", "中证白酒", "酒类"],
        "categories": ["消费"],
        "name_tokens": ["白酒"],
    },
    "中证医疗": {
        "keywords": ["中证医疗", "医疗", "医疗器械", "医药"],
        "categories": ["医药"],
        "name_tokens": ["医疗", "华宝中证医疗", "恒生医疗"],
    },
    "中证传媒": {
        "keywords": ["传媒", "中证传媒", "文娱"],
        "categories": ["传媒"],
        "name_tokens": ["传媒", "广发中证传媒"],
    },
    "证券公司": {
        "keywords": ["证券", "券商", "证券公司"],
        "categories": ["券商"],
        "name_tokens": ["证券", "券商"],
    },
    "中证银行": {
        "keywords": ["银行", "中证银行", "银行指数"],
        "categories": ["金融地产", "金融"],
        "name_tokens": ["银行"],
    },
    "中证环保": {
        "keywords": ["环保", "中证环保", "新能源环保", "环保产业"],
        "categories": ["新能源环保"],
        "name_tokens": ["环保", "广发中证环保"],
    },
    "全指消费": {
        "keywords": ["全指消费", "消费", "主要消费", "可选消费", "食品饮料"],
        "categories": ["消费"],
        "name_tokens": ["消费", "富国消费", "易方达消费"],
    },
    "全指医药": {
        "keywords": ["全指医药", "医药卫生", "医药", "生物医药"],
        "categories": ["医药"],
        "name_tokens": ["医药", "广发中证全指医药", "养老产业"],
    },
    "全指金融": {
        "keywords": ["全指金融", "金融地产", "金融指数"],
        "categories": ["金融地产"],
        "name_tokens": ["金融地产", "全指金融"],
    },
    "全指信息": {
        "keywords": ["全指信息", "信息技术", "科技", "软件", "半导体"],
        "categories": ["科技"],
        "name_tokens": ["信息技术", "全指信息"],
    },
    "养老产业": {
        "keywords": ["养老产业", "养老"],
        "categories": ["医药"],
        "name_tokens": ["养老产业", "广发中证养老"],
    },
    "A股全指": {
        "keywords": ["A股全指", "中证全指", "全市场"],
        "categories": ["其他"],
        "name_tokens": ["全指", "A股全指"],
    },
    "中概互联": {
        "keywords": ["中概互联", "中概", "海外互联", "互联网"],
        "categories": ["中概互联"],
        "name_tokens": ["中概", "中概互联", "海外互联"],
    },
    "恒生指数": {
        "keywords": ["恒生指数", "恒生", "港股", "H股"],
        "categories": ["港股"],
        "name_tokens": ["恒生ETF", "华夏恒生", "恒生指数"],
    },
    "恒生科技": {
        "keywords": ["恒生科技", "港股科技", "HSTECH"],
        "categories": ["港股科技", "港股"],
        "name_tokens": ["恒生科技", "天弘恒生科技"],
    },
}

TOPIC_TO_INDEX: dict[str, list[str]] = {
    "沪深300/300价值": ["沪深300"],
    "中证500/500质量": ["中证500"],
    "中证1000/小盘成长": [],
    "创业板/科创板/双创": ["创业板指", "科创50"],
    "红利/低波/高股息": ["中证红利", "红利低波", "红利低波100"],
    "消费/食品饮料": ["全指消费", "中证白酒"],
    "医药医疗": ["全指医药", "中证医疗", "养老产业"],
    "科技/半导体/AI": ["全指信息", "科创50"],
    "新能源/光伏/电动车": ["中证环保"],
    "金融/地产/基建": ["全指金融", "中证银行", "证券公司"],
    "港股/恒生/中概": ["恒生指数", "恒生科技", "中概互联"],
    "海外/美股/纳指": [],
    "估值/低估高估": list(INDEX_KEYWORDS.keys()),  # soft attach later by keyword only
    "调仓动态/操作记录": [],  # handled via ed_actions
}

FOOTER_MARKERS = (
    "上涨赚金额，下跌攒份额",
    "震荡布网格，不懂决不做",
    "声明：本站所收集文章仅供学习",
    "本站所收集文章仅供学习",
)


def clean_text(text: str) -> str:
    text = text or ""
    cut = len(text)
    for marker in FOOTER_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    text = text[:cut]
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def score_doc(index_name: str, title: str, text: str, keywords: list[str]) -> tuple[int, list[str]]:
    blob = f"{title}\n{(text or '')[:8000]}"
    matched = [kw for kw in keywords if kw and kw in blob]
    if not matched:
        return 0, []
    score = 0
    for kw in matched:
        if kw in title:
            score += 8
        else:
            score += 2
        # longer / more specific keyword is better
        score += min(len(kw) // 2, 4)
    # prefer docs that mention index name itself
    if index_name in title:
        score += 12
    elif index_name in blob:
        score += 4
    return score, matched[:8]


def collect_articles() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []

    # 1) ChinaETFS archive
    china = load_json(CHINA_JSON, {})
    pages = china.get("pages") if isinstance(china, dict) else []
    for p in pages or []:
        if str(p.get("kind") or "") != "article":
            continue
        if str(p.get("status") or "ok") not in {"ok", "None", ""}:
            continue
        title = str(p.get("title") or "").replace(" - E学院", "").strip()
        text = clean_text(str(p.get("text") or ""))
        if len(text) < 80 and len(title) < 4:
            continue
        # try extract date from text head (YYYY-MM-DD)
        date = ""
        m = re.search(r"(20\d{2}-\d{2}-\d{2})", text[:400])
        if m:
            date = m.group(1)
        aid = p.get("article_id")
        if aid in (None, "None", ""):
            aid = None
        docs.append(
            {
                "source": "chinaetfs",
                "id": str(aid or p.get("url") or ""),
                "title": title or "未命名",
                "date": date,
                "url": p.get("url") or "",
                "text": text[:12000],
                "excerpt": text[:220],
            }
        )

    # 2) Qieman markdown posts
    if MD_DIR.exists():
        for path in sorted(MD_DIR.glob("*.md")):
            text_raw = path.read_text(encoding="utf-8", errors="replace")
            title = path.stem
            date = ""
            url = ""
            pid = path.stem
            for line in text_raw.splitlines()[:40]:
                if line.startswith("# "):
                    title = line[2:].strip()
                elif line.startswith("- date:"):
                    date = line.split(":", 1)[1].strip()[:10]
                elif line.startswith("- url:"):
                    url = line.split(":", 1)[1].strip()
                elif line.startswith("- id:"):
                    pid = line.split(":", 1)[1].strip()
            text = clean_text(text_raw)
            docs.append(
                {
                    "source": "qieman",
                    "id": pid,
                    "title": title,
                    "date": date,
                    "url": url or f"https://qieman.com/content/content-detail?postId={pid}&cash_masked=0",
                    "text": text[:12000],
                    "excerpt": text[:220],
                }
            )

    # 3) ed_articles (发车正文)
    ed_arts = load_json(DATA / "ed_articles.json", {})
    for item in ed_arts.get("items") or []:
        text = clean_text(str(item.get("text_full") or item.get("text_excerpt") or item.get("operation_reason") or ""))
        docs.append(
            {
                "source": "ed_article",
                "id": str(item.get("item_id") or item.get("id") or ""),
                "title": item.get("title") or "发车文章",
                "date": str(item.get("date") or "")[:10],
                "url": item.get("url") or item.get("content_url") or "",
                "text": text[:12000],
                "excerpt": (item.get("text_excerpt") or text)[:220],
                "operation_reason": item.get("operation_reason"),
            }
        )

    # 4) topic_index as lightweight extras (may duplicate; de-dupe later)
    topic = load_json(TOPIC_JSON, {})
    topics = topic.get("topics") or {}
    if isinstance(topics, dict):
        for topic_name, items in topics.items():
            for item in items or []:
                docs.append(
                    {
                        "source": item.get("source") or "topic",
                        "id": str(item.get("id") or ""),
                        "title": item.get("title") or "",
                        "date": str(item.get("date") or "")[:10],
                        "url": item.get("url") or "",
                        "text": item.get("excerpt") or "",
                        "excerpt": (item.get("excerpt") or "")[:220],
                        "topic": topic_name,
                        "keywords": item.get("keywords") or [],
                    }
                )

    return docs


# 买卖点/图上标记只展示长赢 150（不含 S 定投）
ALLOWED_PLANS = {"long_win_150", "150", "长赢150", "长赢 150"}


def _is_150_plan(plan: Any) -> bool:
    text = str(plan or "").strip().lower()
    if not text:
        return False
    if text in {p.lower() for p in ALLOWED_PLANS}:
        return True
    # common variants from qieman payloads
    return text in {"long_win_150", "150"} or "long_win_150" in text or text.endswith("_150")


def collect_actions() -> list[dict[str, Any]]:
    """Only 长赢 150 buy/sell ops — never S plan."""
    actions: list[dict[str, Any]] = []
    signals = load_json(DATA / "signals.json", {})
    for a in signals.get("ed_actions") or []:
        if _is_150_plan(a.get("plan")):
            actions.append(dict(a))

    lw = load_json(DATA / "long_win_positions.json", {})
    plans = lw.get("plans") or {}
    # prefer explicit 150 plan key; fall back to any plan matching 150
    plan_items = []
    if "long_win_150" in plans:
        plan_items.append(("long_win_150", plans["long_win_150"]))
    else:
        for plan_key, plan in plans.items():
            if _is_150_plan(plan_key) or _is_150_plan((plan or {}).get("name")):
                plan_items.append((plan_key, plan))

    for plan_key, plan in plan_items:
        for a in (plan or {}).get("actions") or []:
            row = dict(a)
            row.setdefault("plan", plan_key)
            if not _is_150_plan(row.get("plan")):
                continue
            actions.append(row)

    # de-dupe by date+code+action+plan
    seen = set()
    uniq = []
    for a in actions:
        if not _is_150_plan(a.get("plan")):
            continue
        key = (
            str(a.get("date") or "")[:10],
            str(a.get("code") or "").split(".")[0],
            str(a.get("action") or ""),
            str(a.get("plan") or ""),
            str(a.get("shares") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append(a)
    return uniq


def match_actions(index_name: str, cfg: dict[str, Any], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cats = set(cfg.get("categories") or [])
    tokens = cfg.get("name_tokens") or []
    kws = cfg.get("keywords") or []
    out = []
    for a in actions:
        cat = str(a.get("category") or "")
        name = str(a.get("name") or "")
        hit = False
        if cat and cat in cats:
            hit = True
        if not hit:
            for t in tokens:
                if t and t in name:
                    hit = True
                    break
        if not hit:
            blob = f"{name} {cat}"
            for kw in kws:
                if kw and kw in blob:
                    hit = True
                    break
        if not hit:
            continue
        act = str(a.get("action") or "").lower()
        if act not in {"buy", "sell", "reduce"}:
            continue
        out.append(
            {
                "date": str(a.get("date") or "")[:10],
                "action": "sell" if act == "reduce" else act,
                "raw_action": act,
                "name": name,
                "code": a.get("code"),
                "shares": a.get("shares"),
                "category": cat,
                "plan": a.get("plan"),
                "url": a.get("url"),
            }
        )
    # sort newest first
    out.sort(key=lambda x: x.get("date") or "", reverse=True)
    return out


def match_articles(index_name: str, cfg: dict[str, Any], docs: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    kws = list(cfg.get("keywords") or [])
    scored: list[tuple[int, dict[str, Any]]] = []
    for doc in docs:
        # topic soft map: if only topic and no body, still allow if topic maps
        title = str(doc.get("title") or "")
        text = str(doc.get("text") or "")
        score, matched = score_doc(index_name, title, text, kws)
        topic = doc.get("topic")
        if topic and topic in TOPIC_TO_INDEX:
            mapped = TOPIC_TO_INDEX[topic]
            if index_name in mapped:
                score += 3
                matched = list(dict.fromkeys((matched or []) + [topic]))
        if score <= 0:
            continue
        scored.append(
            (
                score,
                {
                    "source": doc.get("source"),
                    "id": doc.get("id"),
                    "title": title,
                    "date": str(doc.get("date") or "")[:10],
                    "url": doc.get("url"),
                    "excerpt": (doc.get("excerpt") or text)[:240],
                    "keywords": matched,
                    "score": score,
                    "operation_reason": doc.get("operation_reason"),
                },
            )
        )

    # de-dupe by url or title+date
    scored.sort(key=lambda x: (-x[0], x[1].get("date") or ""), reverse=False)
    scored.sort(key=lambda x: (-x[0], x[1].get("date") or "0000"), reverse=False)
    # actually want high score then recent date
    scored.sort(key=lambda x: (x[0], x[1].get("date") or ""), reverse=True)

    seen = set()
    out = []
    for score, item in scored:
        key = (item.get("url") or "").rstrip("/") or f"{item.get('title')}|{item.get('date')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def actions_to_marks(actions: list[dict[str, Any]], limit: int = 80) -> list[dict[str, Any]]:
    """Flatten for chart markPoints (date + side)."""
    marks = []
    for a in actions[:limit]:
        day = a.get("date")
        if not day:
            continue
        marks.append(
            {
                "date": day,
                "side": a.get("action"),  # buy / sell
                "label": "买" if a.get("action") == "buy" else "卖",
                "name": a.get("name"),
                "shares": a.get("shares"),
                "plan": a.get("plan"),
                "url": a.get("url"),
            }
        )
    return marks


def main() -> None:
    print("collect articles...")
    docs = collect_articles()
    print(f"  docs={len(docs)}")
    print("collect actions...")
    actions = collect_actions()
    print(f"  actions={len(actions)}")

    indices: dict[str, Any] = {}
    for name, cfg in INDEX_KEYWORDS.items():
        arts = match_articles(name, cfg, docs, limit=40)
        acts = match_actions(name, cfg, actions)
        indices[name] = {
            "name": name,
            "keywords": cfg.get("keywords") or [],
            "categories": cfg.get("categories") or [],
            "article_count": len(arts),
            "action_count": len(acts),
            "buy_count": sum(1 for a in acts if a.get("action") == "buy"),
            "sell_count": sum(1 for a in acts if a.get("action") == "sell"),
            "articles": arts,
            "actions": acts[:120],
            "marks": actions_to_marks(acts, limit=100),
        }
        print(f"  {name}: articles={len(arts)} actions={len(acts)} buy={indices[name]['buy_count']} sell={indices[name]['sell_count']}")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": {
            "chinaetfs": str(CHINA_JSON),
            "topic_index": str(TOPIC_JSON),
            "qieman_markdown": str(MD_DIR),
            "ed_articles": str(DATA / "ed_articles.json"),
            "signals": str(DATA / "signals.json"),
            "long_win": str(DATA / "long_win_positions.json"),
        },
        "index_count": len(indices),
        "total_articles_linked": sum(v["article_count"] for v in indices.values()),
        "total_actions_linked": sum(v["action_count"] for v in indices.values()),
        "indices": indices,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
