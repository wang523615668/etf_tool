from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SUPABASE_URL = "https://supabase.maxmeng.top"
ENV_PAGE_URL = "https://etf.maxmeng.top/long-win/150"

PLANS = {
    "long_win_150": {
        "advisor_code": "LONG_WIN",
        "fallback_name": "长赢150份",
        "url": "https://etf.maxmeng.top/long-win/150",
    },
    "long_win_s": {
        "advisor_code": "LONG_WIN_S",
        "fallback_name": "长赢S定投",
        "url": "https://etf.maxmeng.top/long-win/s",
    },
}

CATEGORY_RULES = [
    ("中证500", "中证500"), ("沪深300", "沪深300"), ("上证50", "上证50"),
    ("红利", "红利低波"), ("恒生科技", "港股科技"), ("恒生", "港股"),
    ("海外互联", "中概互联"), ("中概", "中概互联"), ("医疗", "医药"),
    ("医药", "医药"), ("健康", "医药"), ("养老", "医药"), ("证券", "券商"),
    ("金融", "金融地产"), ("地产", "金融地产"), ("消费", "消费"), ("传媒", "传媒"),
    ("文体", "传媒"), ("信息", "科技"), ("纳斯达克", "海外"), ("纳指", "海外"),
    ("标普", "海外"), ("美元债", "债券现金"), ("债", "债券现金"), ("国开", "债券现金"),
    ("黄金", "商品"), ("油", "商品"), ("环保", "新能源环保"), ("创业板", "创业板"),
    ("可转债", "债券现金"),
]


def fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
    errors: list[str] = []
    for proxy in (None, "http://127.0.0.1:7890"):
        try:
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                )
            else:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            errors.append(f"{proxy or 'direct'}: {exc}")
    raise RuntimeError("; ".join(errors))


def supabase_anon_key() -> str:
    html = fetch_text(ENV_PAGE_URL)
    chunks = sorted(set(re.findall(r"/_next/static/chunks/[^\"']+\.js", html)))
    for chunk in chunks:
        try:
            source = fetch_text("https://etf.maxmeng.top" + chunk)
        except Exception:
            continue
        match = re.search(r'supabaseAnonKey:"([^"]+)"', source)
        if match:
            return match.group(1)
    raise RuntimeError("未能从 ETF 投资分析网页 chunk 中提取 Supabase anon key")


def supabase_get(path: str, key: str) -> list[dict[str, Any]]:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept-Profile": "finance",
    }
    return json.loads(fetch_text(f"{SUPABASE_URL}/rest/v1{path}", headers=headers))


def category_for(name: str) -> str:
    for needle, category in CATEGORY_RULES:
        if needle in name:
            return category
    return "其他"


def normalize_date(value: str | None) -> str | None:
    return value[:10] if value else None


def normalize_action(action: dict[str, Any], plan_key: str) -> dict[str, Any]:
    name = action.get("fund_name") or ""
    shares = int(action.get("share") or 0)
    return {
        "date": normalize_date(action.get("action_time")),
        "plan": plan_key,
        "action": action.get("action_type"),
        "name": name,
        "code": action.get("fund_code") or "",
        "shares": shares,
        "category": category_for(name),
        "url": action.get("url"),
    }


def build_positions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    holdings: dict[str, dict[str, Any]] = {}
    for action in sorted(actions, key=lambda item: item.get("date") or ""):
        code = action["code"]
        item = holdings.setdefault(
            code,
            {
                "code": code,
                "name": action["name"],
                "shares": 0,
                "latest": action["date"],
                "category": action["category"],
            },
        )
        if action["action"] == "buy":
            item["shares"] += action["shares"]
        elif action["action"] == "sell":
            item["shares"] -= action["shares"]
        item["name"] = action["name"] or item["name"]
        item["latest"] = action["date"] or item["latest"]
        item["category"] = action["category"]
    for item in holdings.values():
        item["shares"] = max(0, int(item["shares"]))
        item["status"] = "active" if item["shares"] else "cleared"
    return sorted(
        holdings.values(),
        key=lambda x: (x["shares"], x["latest"] or ""),
        reverse=True,
    )


def fetch_plan(plan_key: str, plan_config: dict[str, str], key: str) -> dict[str, Any]:
    code = urllib.parse.quote(plan_config["advisor_code"])
    plans = supabase_get(f"/advisor_plans?select=*&advisor_code=eq.{code}", key)
    if not plans:
        raise RuntimeError(f"未找到投顾计划: {plan_config['advisor_code']}")
    plan = plans[0]
    plan_id = plan["id"]
    raw_actions = supabase_get(
        f"/advisor_actions?select=*&advisor_plan_id=eq.{plan_id}&order=action_time.desc&limit=1000",
        key,
    )
    actions = [normalize_action(action, plan_key) for action in raw_actions]
    positions = build_positions(actions)
    buy = sum(action["shares"] for action in actions if action["action"] == "buy")
    sell = sum(action["shares"] for action in actions if action["action"] == "sell")
    return {
        "name": plan.get("advisor_name") or plan_config["fallback_name"],
        "url": plan_config["url"],
        "stats": {"buy": buy, "sell": sell, "current": buy - sell},
        "positions": positions,
        "actions": actions,
    }


def sync() -> dict[str, Any]:
    DATA.mkdir(parents=True, exist_ok=True)
    key = supabase_anon_key()
    generated_at = datetime.now().isoformat(timespec="seconds")
    result = {"generated_at": generated_at, "plans": {}}
    all_actions: list[dict[str, Any]] = []
    for plan_key, plan_config in PLANS.items():
        plan = fetch_plan(plan_key, plan_config, key)
        result["plans"][plan_key] = plan
        all_actions.extend(plan["actions"])
    (DATA / "long_win_positions.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    signals_path = DATA / "signals.json"
    signals = (
        json.loads(signals_path.read_text(encoding="utf-8"))
        if signals_path.exists()
        else {"signals": []}
    )
    signals["ed_actions"] = sorted(
        all_actions, key=lambda item: item.get("date") or "", reverse=True
    )
    signals["generated_at"] = generated_at
    signals_path.write_text(
        json.dumps(signals, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "generated_at": generated_at,
        "plans": {
            key: {
                "positions": len(value["positions"]),
                "actions": len(value["actions"]),
                "current": value["stats"]["current"],
                "latest": value["actions"][0]["date"] if value["actions"] else None,
            }
            for key, value in result["plans"].items()
        },
        "actions": len(all_actions),
    }


if __name__ == "__main__":
    print(json.dumps(sync(), ensure_ascii=False, indent=2))
