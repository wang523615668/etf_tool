#!/usr/bin/env python3
"""Generate local buy/hold/watch signals from lixinger valuations + long-win + E大.

Applies personal execution memory (my_trades): after you buy, suppress re-buy
until cooldown days pass OR price drops enough (time OR space).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.data_sources.lixinger import build_valuation_snapshot  # noqa: E402
from app.decision_memory import apply_execution_filter  # noqa: E402

# 市场水位（E大七档·等权PE分位主锚）——复用 finance_app/ed_quant 同一数据与判定
sys.path.insert(0, "/vol1/1000/openzl/finance_app")
try:
    import ed_quant  # noqa: E402
except Exception:
    ed_quant = None

DATA = ROOT / "data"
LONG_WIN = DATA / "long_win_positions.json"
SIGNALS = DATA / "signals.json"
ED_FOLLOW = DATA / "ed_follow.json"

# ── 品种(类)目标仓位模型 —— 全部取自E大邮件原话 ──────────────────────
# 目标份数 = 150 × min(类上限, 1−类分位)   ("仓位…100%-现处百分位""根据五年、十年
#   百分位精确布置仓位")；持有已达目标 → 即使低估也停买 ("医药不能再买了。……我们的
#   医药已经买了2份。……如果在这个位置买3份，恐怕以后会比较麻烦")。
CAT_CAP = {           # 类仓位上限（占本金比例），有原话的标原话
    "消费": 0.20,      # "（全指消费）上限依然是20%"
    "医药": 0.25,      # 消费+医药基石"至少15%-20%…除非极度高估不清仓"，上限取保守档
    "海外": 0.30,      # "所有海外市场的仓位加起来上限应该可以是30%"
}
DEFAULT_CAT_CAP = 0.25  # 计划既有"同类≤25%"约束
TOTAL_SHARES = 150.0

# ── 相关性组（E大原话："比如配置过多180、50、红利就不行"、"50、180和300区别不是特别大"、
#    "医疗和医药"同质、他实际持仓里消费/白酒、科技/创业板从不双买）──
# 同一轮买入信号里，每个相关性组只放行 1 只（temp 最低=最便宜的那只，符合他"买最便宜"），
# 组内其余降级 watch 并标注原因。品种自身分位仍是第一道门槛，这只是第二道"别扎堆"。
CORR_GROUP = {
    "消费": "大消费", "中证白酒": "大消费", "白酒": "大消费", "主要消费": "大消费",
    "医药": "医药医疗", "全指医药": "医药医疗", "养老产业": "医药医疗", "中证医疗": "医药医疗",
    "医疗": "医药医疗", "香港医疗": "医药医疗",
    "中证红利": "红利价值", "红利低波": "红利价值", "红利低波100": "红利价值",
    "上证50": "红利价值", "沪深300": "红利价值",   # "50/180/300/红利同质"——180≈300
    "中证500": "小盘宽基", "中证1000": "小盘宽基", "创业50": "小盘宽基",
    # 精确表必须先于一切关键词兜底：'中概互联50'endswith50、'标普500'含500——曾因兜底错组
    # （中概被当成红利价值与50/300互相挤兑，却能跟恒科同轮双买；审计修复2026-09-25）
    "中概互联50": "港股", "中概互联": "港股", "标普500": "海外", "纳斯达克100": "海外",
    "恒生指数": "港股", "港股": "港股", "港股科技": "港股", "恒生科技": "港股",
    "创业板": "科技成长", "科创50": "科技成长", "全指信息": "科技成长", "科技": "科技成长",
    "中证环保": "科技成长", "新能源环保": "科技成长", "中证传媒": "传媒",
    "证券公司": "非银", "金融地产": "银行地产", "中证银行": "银行地产", "全指金融": "银行地产",
    "恒生指数": "港股", "港股": "港股", "港股科技": "港股", "中概互联": "港股",
    "标普500": "海外", "纳斯达克100": "海外", "海外": "海外",
}


def corr_group(cat_or_name: str) -> str:
    g = CORR_GROUP.get(cat_or_name or "")
    if g:
        return g
    n = cat_or_name or ""
    if any(k in n for k in ("消费", "白酒")): return "大消费"
    if any(k in n for k in ("医药", "医疗", "养老")): return "医药医疗"
    if any(k in n for k in ("500", "1000")): return "小盘宽基"   # 必须先于"50"判断
    if any(k in n for k in ("红利", "300")): return "红利价值"
    if n.endswith("50") or "上证50" in n or "科创50" in n: return "科技成长" if "科创" in n else "红利价值"
    if any(k in n for k in ("科技", "信息", "创业板", "科创", "环保", "新能源")): return "科技成长"
    if any(k in n for k in ("传媒",)): return "传媒"
    if any(k in n for k in ("券商", "证券")): return "非银"
    if any(k in n for k in ("银行", "金融", "地产")): return "银行地产"
    if any(k in n for k in ("恒生", "港股", "中概", "海外互联")): return "港股"
    return "其他"  # 不限制


def stock_split() -> dict[str, float]:
    """E大跟随账本按市场拆分份数 {A股,港股,海外股票,债券,商品}。
    用户2026-09-24指出：111份含债券与美股港股，算A股仓位必须分开。"""
    try:
        from app.ed_markets import market_totals
    except Exception:
        return {}
    d = load_json(ED_FOLLOW, {})
    return market_totals(d.get("positions") or [])


def stock_caps() -> tuple[float, float, float]:
    """三道硬上限（份数），全部有E大原话依据：
      股票类(A+港+海外) ≤ 80%（「股票类资产占比最大不要超过80%」）
      A+港股 ≤ 75%（「将A股的最高仓位从80%降到75%…A股港股相关性越来越高」）
      海外 ≤ 30%（「所有海外市场的仓位加起来上限应该可以是30%」）"""
    return TOTAL_SHARES * 0.80, TOTAL_SHARES * 0.75, TOTAL_SHARES * 0.30


def stock_gate(split: dict[str, float]) -> str:
    """返回 ''=可买；否则返回拦截原因。"""
    if not split:
        return ""
    all_cap, ah_cap, os_cap = stock_caps()
    stocks = split.get("A股", 0) + split.get("港股", 0) + split.get("海外股票", 0)
    ah = split.get("A股", 0) + split.get("港股", 0)
    over = split.get("海外股票", 0)
    if stocks >= all_cap:
        return f"股票类已持有{stocks:g}/150份，触及E大80%({all_cap:g}份)硬上限"
    if ah >= ah_cap:
        return f"A+港股已持有{ah:g}/150份，触及E大75%({ah_cap:g}份)上限"
    if over >= os_cap:
        return f"海外已持有{over:g}份，触及E大30%({os_cap:g}份)上限"
    return ""


def stock_soft_note(split: dict[str, float], pe_pct: float | None) -> str:
    """软提醒：股票持有超过 1−PE分位 理想仓位时，提示慎重（E大'估值高的时候保守配置'）。"""
    if not split or pe_pct is None:
        return ""
    stocks = split.get("A股", 0) + split.get("港股", 0) + split.get("海外股票", 0)
    target = TOTAL_SHARES * max(0.0, min(1.0, 1 - pe_pct / 100.0))
    if stocks > target:
        return (f"股票已持有{stocks:g}/150份({stocks / TOTAL_SHARES * 100:.0f}%)，"
                f"高于1−PE分位的理想仓位{target:.0f}份({(target / TOTAL_SHARES) * 100:.0f}%)，宜慎重")
    return ""


def ed_held_by_cat() -> dict[str, float]:
    """E大跟随账本(data/ed_follow.json)按【组】汇总的当前持有份数。
    ⚠️必须用 account_ledger.category_group 同一套合并口径（医药/医疗/养老→医药医疗 等），
    否则信号层按 raw category、动作单层按 group，两套预算不闭合（审计修复2026-09-25）。"""
    d = load_json(ED_FOLLOW, {})
    try:
        from app.account_ledger import category_group as _cg
    except Exception:
        _cg = lambda c, n="": str(c or "") or "其他"
    out: dict[str, float] = {}
    for p in d.get("positions") or []:
        g = _cg(str(p.get("category") or "").strip(), str(p.get("name") or "").strip())
        if g:
            out[g] = out.get(g, 0.0) + float(p.get("shares") or 0)
    return out


# 组→上限（CAT_CAP 的 raw cat 键经 category_group 映射后的等价表）
G_CAP = {"消费": 0.20, "医药医疗": 0.25, "海外": 0.30}


def cat_target_shares(cat: str, temp01: float, group: str | None = None) -> float:
    g = group or cat
    cap = G_CAP.get(g, CAT_CAP.get(cat, DEFAULT_CAT_CAP))
    ideal = max(0.0, min(1.0, 1.0 - temp01))
    return TOTAL_SHARES * min(cap, ideal)


_QZ: dict = {}

def qz_feats() -> dict:
    """全A等权(正数等权序列)的市场级技术特征：vs MA250、5年PE分位。
    数据=E大主锚同源的 quanzhi_ewpvo.json。"""
    if _QZ:
        return _QZ
    try:
        rows = json.loads(Path("/vol1/1000/openzl/finance_app/data/quanzhi_ewpvo.json"
                               ).read_text(encoding="utf-8"))["rows"]
        rows.sort(key=lambda r: r["date"])
        cp = [r["cp"] for r in rows]
        pe = [r["pe"] for r in rows]
        ma250 = sum(cp[-250:]) / 250
        win = pe[-1250:]
        pct5 = sum(1 for v in win if v <= pe[-1]) / len(win) * 100
        _QZ.update({"vs_ma250": (cp[-1] / ma250 - 1) * 100, "pe_pct5y": pct5,
                    "date": rows[-1]["date"]})
    except Exception:
        _QZ.update({"vs_ma250": None, "pe_pct5y": None})
    return _QZ


def stock_tech(code: str, name: str) -> dict:
    """品种技术特征（理杏仁缓存cp）：vs 3年线、60日动量。用于『不弱势卖』闸。"""
    key = str(code or "").split(".")[0]
    f = ROOT / "cache" / "lixinger" / f"{key}.json"
    if not f.exists():
        return {}
    try:
        import hashlib
        sig = hashlib.md5(str(f.stat().st_size).encode()).hexdigest()[:8]
        if getattr(stock_tech, "_sig", {}).get(key) != sig:
            stock_tech.__dict__.setdefault("_cache", {})[key] = json.loads(f.read_text(encoding="utf-8"))["rows"]
            stock_tech.__dict__.setdefault("_sig", {})[key] = sig
        rows = stock_tech._cache[key]
        cp = [r.get("cp") for r in rows if r.get("cp")]
        if len(cp) < 80:
            return {}
        w = cp[-750:] if len(cp) >= 750 else cp
        return {"vs_ma3y": (cp[-1] / (sum(w) / len(w)) - 1) * 100,
                "mom60": (cp[-1] / cp[-61] - 1) * 100 if len(cp) >= 61 else None}
    except Exception:
        return {}


PER_INDEX: dict[str, dict] = {}

def load_per_index() -> dict[str, dict]:
    """逐指数个性化线（scripts/calibrate_per_index.py 从E大422笔真实买卖校准）。
    first_line: 首仓买入上限（他没底仓时建仓的温度中位）
    add_line:   趋势加仓上限（有底仓/E大近45天买过 → 越涨越买允许到他的P90）
    sell_line:  止盈起卖线（只按高位卖样本，剔除调仓卖）
    样本不足的品种/字段 → 回退全局默认。"""
    f = ROOT / "data" / "per_index_thresholds.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def per_index_for(code: str, name: str) -> dict:
    """按指数代码前缀匹配（399812.SZ→399812）；再按名称兜底。"""
    key = str(code or "").split(".")[0]
    rec = PER_INDEX.get(key)
    if rec is None:
        for k, v in PER_INDEX.items():
            if v.get("name") and (v["name"] in str(name or "") or str(name or "") in v["name"]):
                rec = v
                break
    return rec or {}


def market_factor() -> tuple[float, str, float | None]:
    """激进档位系数（用户2026-09-24定：「按激进仓位配置」）。
    E大口径：(1−分位)/1 满额、低估侧放大、>70%才收。
      <35%(低估侧) →1.5 加倍；35-70%(含现在65%偏高区) →1.0 照买；
      70-85% →0.7；>85% →0.5 半份。
    返回 (系数, 档位说明, PE分位)。数据不可用时 (1.0, '', None)——不影响信号。"""
    if ed_quant is None:
        return 1.0, "", None
    try:
        mz = ed_quant.market_zone()
    except Exception:
        return 1.0, "", None
    if not mz or mz.get("pe_pct") is None:
        return 1.0, "", None
    p = float(mz["pe_pct"])
    zone = str(mz.get("zone", ""))
    # 激进档位（用户2026-09-24定）：E大原话——
    #   「进攻型的朋友仓位可以是(100-80)/1=20%」→ 分位以上不减半
    #   「在历史30%的时候应该配置70%仓位，但可以在这个时候加大投入，
    #     配置75%乃至80%」→ 低估侧放大
    if p < 35:
        return 1.5, f"全市场{zone}(PE分位{p:.0f}%低估侧)，激进档：本次可加倍", p
    if p < 70:
        return 1.0, "", p
    if p < 85:
        return 0.7, f"⚠全市场{zone}(PE分位{p:.0f}%)明显高估，激进档也收着买(×0.7)", p
    return 0.5, f"⚠全市场{zone}(PE分位{p:.0f}%)极高估，只留半份仓位(×0.5)", p


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def temp_of_row(row: dict) -> float | None:
    t = row.get("temperature")
    if t is not None:
        return float(t)
    pe = row.get("pe_percentile")
    pb = row.get("pb_percentile")
    pb_only = bool(row.get("pb_only"))
    vals = [v for v in ((pb,) if pb_only else (pe, pb)) if v is not None]
    if not vals:
        return None
    return sum(float(v) for v in vals) / len(vals) * 100


def main() -> int:
    long_win = load_json(LONG_WIN, {"plans": {}})
    old = load_json(SIGNALS, {"signals": [], "ed_actions": []})
    ed_actions = old.get("ed_actions") or []
    cutoff = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    recent = [a for a in ed_actions if str(a.get("date", "")) >= cutoff]
    recent_buy_codes = {str(a.get("code", "")).split(".")[0] for a in recent if a.get("action") == "buy"}
    recent_buy_cats = {str(a.get("category") or "") for a in recent if a.get("action") == "buy"}

    global PER_INDEX
    PER_INDEX = load_per_index()

    # 底仓名单：E大当前持仓(ed_follow 38只)里已有该品种 → 允许趋势加仓(add_line)；无底仓 → 只认首仓线
    _held_recs: list[str] = [str(p.get("name") or "") for p in load_json(ED_FOLLOW, {}).get("positions") or []]

    def _has_base(code: str, name: str) -> bool:
        n = str(name or "")
        keys = {"上证50": "上证50", "沪深300": "300", "中证500": "500", "中证红利": "红利",
                "全指消费": "消费", "养老产业": "养老", "全指医药": "医药", "中证医疗": "医疗",
                "恒生指数": "恒生ETF", "恒生科技": "恒生科技", "中概互联": "中概",
                "创业板指": "创业板", "中证环保": "环保", "中证传媒": "传媒",
                "全指金融": "金融", "证券公司": "证券", "香港医疗": "医疗"}
        kw = keys.get(n, n[:2])
        return any(kw in h for h in _held_recs)

    # cache-only: do not burn 理杏仁 token in signal generation
    snap = build_valuation_snapshot(allow_network=False)
    rows = snap.get("rows") or []

    name_to_cat = {
        "沪深300": "沪深300", "中证500": "中证500", "上证50": "上证50",
        "创业板": "创业板", "中证红利": "红利低波", "红利低波": "红利低波",
        "红利低波100": "红利低波", "全指消费": "消费", "养老产业": "医药",
        "中证医药": "医药", "中证医疗": "医药", "恒生指数": "港股",
        "恒生科技": "港股科技", "中概互联": "中概互联", "中证银行": "金融地产",
        "证券公司": "券商", "中证环保": "新能源环保", "中证传媒": "传媒",
        "标普500": "海外", "纳斯达克100": "海外",
    }

    signals = []
    today = datetime.now().strftime("%Y-%m-%d")
    seen = set()
    mfac, mnote, mpe_pct = market_factor()
    qz = qz_feats()  # 市场级技术（全A vs MA250）——他回测:亢奋市(>MA250+15%)里买入仅2%
    mkt_hot = qz.get("vs_ma250") is not None and qz["vs_ma250"] > 15
    held_by_cat = ed_held_by_cat()  # E大跟随账本(150份)按【组】持有 → 品种目标仓位闸
    try:
        from app.account_ledger import category_group as _CG
    except Exception:
        _CG = lambda c, n="": str(c or "") or "其他"
    split = stock_split()           # A股/港股/海外/债券 拆分（用户：算A股仓位要单独算）
    hard_gate = stock_gate(split)   # E大80%/75%/30%三道硬上限
    soft_note = stock_soft_note(split, mpe_pct)  # 高于理想仓位→慎重软提醒
    for row in rows:
        name = row.get("name")
        action = row.get("action")
        temp = temp_of_row(row)
        if action not in {"buy", "watch", "hold", "reduce"}:
            continue
        if temp is None:
            continue
        cat = name_to_cat.get(name, name) or "其他"
        gate_note = ""
        # ── 逐指数个性化线（E大422笔真实买卖校准，样本不足回退默认） ──
        prec = per_index_for(row.get("code") or "", name)
        pi_note = ""
        # ── 闸·不弱势卖（逐笔回测：他107笔卖出中 0 笔发生在品种深度弱势时——
        #    弱势时他只会"越跌越买"或用调仓换股，从不割肉。估值触线但技术已崩 → 缓一步） ──
        if action == "reduce" or (action in {"buy", "hold", "watch"} and prec.get("sell_line")
                                  and temp >= prec["sell_line"]):
            _tk = stock_tech(row.get("code") or "", name)
            if _tk and (_tk.get("vs_ma3y") or 0) < -10 and (_tk.get("mom60") or 0) < -10:
                action = "hold"
                pi_note = (f" · 🛡不弱势卖：品种在3年线下方{abs(_tk['vs_ma3y']):.0f}%且60日动量"
                           f"{_tk['mom60']:.0f}%——E大从不在此形态卖出(107笔回测0例)，先持有等企稳")
        if (not pi_note and action in {"buy", "hold", "watch"} and prec.get("sell_line")
                and temp >= prec["sell_line"]):
            action = "reduce"
            pi_note = f" · 🎯个性化止盈：E大对此品种{prec['sell_line']:g}°起卖(他卖中位{prec.get('sell_med','?')}°)，现价{temp:.0f}°"
        if action == "buy" and prec:
            fl, al = prec.get("first_line"), prec.get("add_line")
            if _has_base(row.get("code") or "", name) and fl and al and temp > fl and temp <= al:
                # 无首仓资格但有底仓 → 趋势加仓（他真实行为：越涨越买，如环保他61°还买）
                # 注：必须 fl 存在才能走此分支——fl=None 时 temp>fl 直接 TypeError（审计修复）
                pi_note = f" · 📈趋势加仓：有底仓，E大对此品种买至{al:g}°(P90)"
            elif fl and temp > fl:
                action = "watch"
                pi_note = f" · 🎯个性化：E大对此品种首仓只建在{fl:g}°以下(他买中位{prec.get('buy_med','?')}°)，现价{temp:.0f}°偏贵，等回落"
        if action == "reduce" and prec and prec.get("sell_line") and temp < prec["sell_line"] and temp < 70:
            # 个性化止盈线更低（如创业板他49°就卖）时提前提示；仍高于线才保留reduce
            pass
        if action == "buy":
            # ── 市场级硬闸：E大股票80%/A+港75%/海外30%（按市场拆分后的真实份数）──
            if hard_gate:
                gate_note = f"⛔{hard_gate}，停止一切买入"
                action = "hold"
            else:
                # ── 品种目标仓位闸（E大"医药不能再买了，已经买了2份…买3份以后会比较麻烦"）──
                grp = _CG(cat, name)   # 与动作单层同一合并口径
                target = cat_target_shares(cat, temp / 100.0, group=grp)
                held = held_by_cat.get(grp, 0.0)
                if target - held < 1:
                    gate_note = (f"⛔[{grp}]已持有{held:g}/目标{target:g}份(=150×min(上限,1−分位))，"
                                 f"仓位已满只持有不再买")
                    action = "hold"  # 降级为观察，UI仍可见原因
                else:
                    held_by_cat[grp] = held + 1  # 当日多信号共享同一预算
        if action == "buy":
            conf = 75 if temp <= 18 else 68 if temp <= 30 else 60
            # 激进档（用户定）：E大"进攻型仓位=(100-分位)/1"，分位>70%才开始降置信
            if mpe_pct is not None and mpe_pct > 70:
                conf = max(40, conf - 10)
            # 市场亢奋闸：全A>MA250+15% 时他几乎停买(191笔仅3笔) → 置信降10
            if mkt_hot:
                conf = max(40, conf - 10)
            # 激进档：超理想仓位线只展示提示、不再降置信度（保守档才扣10）
            if soft_note and mfac < 1.0:
                conf = max(35, conf - 10)
        elif action == "reduce":
            conf = 70
        elif action == "hold":
            conf = 55
        else:
            conf = 52
        if action == "buy" and (cat in recent_buy_cats or str(row.get("code") or "").split(".")[0] in recent_buy_codes):
            conf = min(90, conf + 8)
            reason_extra = "；近45天E大同类有买入"
        else:
            reason_extra = ""
        key = (action, name)
        if key in seen:
            continue
        seen.add(key)
        if (action == "buy" or pi_note or (action in {"hold", "watch"} and temp <= 40)
                or action == "reduce"):
            sig_reason = (row.get("reason") or f"估值温度{temp}") + reason_extra
            if action == "buy" and mnote:
                sig_reason += f" · {mnote}"
            if action == "buy" and mkt_hot:
                sig_reason += (f" · ⚡市场亢奋(全A高于年线{qz['vs_ma250']:.0f}%)：E大此形态下191笔仅3笔买入，"
                               f"追高需谨慎")
            if gate_note:
                sig_reason += f" · {gate_note}"
            elif soft_note:
                sig_reason += f" · ⚠{soft_note}"
            sig_reason += pi_note
            signals.append({
                "date": today,
                "source": "local_model",
                "action": action,
                "name": name,
                "code": row.get("code") or "",
                "shares": 1 if action == "buy" else 0,
                "market_factor": mfac if action == "buy" else 1.0,
                "market_pe_pct": mpe_pct,
                "reason": sig_reason,
                "confidence": conf,
                "category": cat,
                "temperature": temp,
                "cp": row.get("cp"),
                "pi_first": prec.get("first_line"),
                "pi_add": prec.get("add_line"),
                "pi_sell": prec.get("sell_line"),
            })

    # ── 相关性闸（第二道）：同一相关性组，一轮只买最便宜的一只，其余降观察 ──
    # E大："配置过多180、50、红利就不行"；他持仓里消费/白酒、医疗/医药、科技/创业板从不双买。
    group_best: dict[str, dict] = {}
    for sig in sorted((x for x in signals if x["action"] == "buy"),
                      key=lambda x: (x.get("temperature") or 999)):
        g = corr_group(str(sig.get("category") or sig.get("name") or ""))
        sig["corr_group"] = g
        if g == "其他":
            continue
        if g in group_best:
            winner = group_best[g]
            sig["action"] = "watch"
            sig["confidence"] = min(sig["confidence"], 55)
            sig["reason"] += (f" · 🔗相关性：与{winner['name']}同属[{g}]，"
                              f"一轮只买最便宜的一只，本只排后（等它涨上来或换仓时再看）")
        else:
            group_best[g] = sig

    prio = {"buy": 0, "reduce": 1, "watch": 2, "hold": 3}
    signals.sort(
        key=lambda s: (
            prio.get(s["action"], 9),
            s.get("temperature") if s.get("action") == "buy" else -(s.get("temperature") or 0),
        )
    )
    signals = signals[:12]

    filtered = apply_execution_filter(signals)
    active = filtered["signals"]
    suppressed = filtered.get("suppressed_signals") or []

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "signals": active,
        "suppressed_signals": suppressed,
        "ed_actions": ed_actions,
        "method": "lixinger_percentile + long_win_context + ed_45d_weight + personal_execution_memory",
        "market_context": {"pe_pct": mpe_pct, "factor": mfac, "note": mnote,
                          "stock_split": split, "hard_gate": hard_gate, "soft_note": soft_note},
        "decision_memory": {
            "settings": filtered.get("settings"),
            "my_trade_count": filtered.get("my_trade_count"),
            "rule": "买入后冷却N天；或较买入点再跌≥X%才恢复（时间或空间）",
        },
        "long_win_positions_at": long_win.get("generated_at"),
    }
    SIGNALS.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(active)} active, suppressed={len(suppressed)}, ed_actions={len(ed_actions)}")
    for s in active[:8]:
        print("ACTIVE", s["action"], s["name"], s.get("temperature"), s["confidence"])
    for s in suppressed[:6]:
        print("COOL", s["action"], s["name"], (s.get("cooldown") or {}).get("reason", "")[:80])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
