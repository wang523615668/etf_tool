#!/usr/bin/env python3
# ed_algo.py — E大估值口径复刻: 逐指数最优组合(算法 × 名单源)
# 配置来源: data/ed_perindex_best.json (由 /root/.hermes/cache/scratch 的 s54 网格搜索产出)
# 用法:
#   import ed_algo
#   cfg = ed_algo.load_config()                 # {显示名: {algo, list}}
#   ctx = ed_algo.build_ctx(snapshot, date)     # 名单上下文
#   pe, pb, n = ed_algo.series_value(snap, name, cfg, ctx)
import json, os, statistics as st
BASE = "/vol1/1000/openzl/etf_tool"
DATA = f"{BASE}/data"

# ---------- 名单源 ----------
# 市值重建规则: 前缀(含) / 排名区间[lo,hi)
MKRULE = {
    "上证50":   (("60",), 0, 50),
    "上证180":  (("60",), 0, 180),
    "沪深300":  (("60", "00", "30"), 0, 300),
    "中证500":  (("60", "00", "30"), 300, 800),
    "中证1000": (("60", "00", "30"), 800, 1800),
    "深证100":  (("00",), 0, 100),
    "创业板指": (("30",), 0, 100),
    "创业板综": (("30",), 0, 99999),
    "科创50":   (("68",), 0, 50),
}
BSKEY = {"沪深300": "hs300", "中证500": "zz500", "上证50": "sz50"}
# 显示名 → 指数代码(用于新浪纳入日期等按代码索引的名单源)
INDEX_CODE = {
    "上证50": "000016", "沪深300": "000300", "中证500": "000905", "上证180": "000010",
    "深证100": "399004", "科创50": "000688", "创业板指": "399006", "全指医药": "000991",
    "全指金融": "000992", "全指消费": "000990", "全指信息": "000993", "中证环保": "000827",
    "养老产业": "399812", "中证1000": "000852", "食品饮料": "000815", "中证红利": "000922",
    "中证军工": "399967", "中证医疗": "399989",
}

def _load(fp, default):
    try:
        return json.load(open(fp))
    except Exception:
        return default

_LIX = None
def _lix():
    """理杏仁历史时点成分表(命名注意: 里面存的是{指数名:{asof:{日期:[代码]}}})"""
    global _LIX
    if _LIX is None:
        _LIX = _load(f"{DATA}/ed_cons_lixinger.json", {})
    return _LIX

def load_config():
    j = _load(f"{DATA}/ed_perindex_best.json", {})
    out = {}
    for name, v in (j.get("indices") or {}).items():
        out[name] = {"algo": v.get("algo"), "list": v.get("list", "current")}
    return out

class Ctx:
    """一次快照/一天所需的名单数据"""
    __slots__ = ("date", "current", "sina_pit", "bspit", "cur_map")
    def __init__(self, date, current, sina_pit, bspit, code_names):
        self.date = date
        self.current = current          # {显示名: [codes]}
        self.sina_pit = sina_pit        # {index_code: {stock: 纳入日期}}
        self.bspit = bspit              # {月末: {hs300/zz500/sz50: [sh.xxxxxx]}}
        self.cur_map = code_names       # {显示名: 指数代码}

def build_ctx(date, current=None, sina_pit=None, bspit=None, code_names=None):
    cur = current if current is not None else _load(f"{DATA}/ed_cons_current.json", {})
    pit = sina_pit if sina_pit is not None else _load(f"{DATA}/ed_sina_pit.json", {})
    bs  = bspit   if bspit   is not None else _load(f"{DATA}/pit_cons_monthly.json", {})
    return Ctx(date, cur, pit, bs, code_names or INDEX_CODE)

# ---------- 算法 ----------
def _pos(v):
    return v if isinstance(v, (int, float)) and v > 0 else None

def _med(k):
    def f(p):
        v = [_pos(r.get(k)) for r in p]
        v = [x for x in v if x]
        return st.median(v) if v else None
    return f

def _med_all(k):
    def f(p):
        v = [r.get(k) for r in p if isinstance(r.get(k), (int, float))]
        return st.median(v) if v else None
    return f

def _trim(k, frac):
    def f(p):
        v = sorted(x for x in (_pos(r.get(k)) for r in p) if x)
        if len(v) < 10: return None
        kk = max(1, int(len(v) * frac))
        w = v[kk:-kk] or v
        return st.mean(w)
    return f

def _harm(p):
    v = [x for x in (_pos(r.get("PE_TTM")) for r in p) if x]
    return len(v) / sum(1 / x for x in v) if v else None

def _wagg(p):
    w = [(r["TOTAL_MARKET_CAP"], _pos(r.get("PE_TTM"))) for r in p if r.get("TOTAL_MARKET_CAP")]
    if not w: return None
    num = sum(a for a, _ in w)
    den = sum(a / b for a, b in w if b)
    return num / den if den else None

ALGOS = {
    "中位TTM剔亏":      _med("PE_TTM"),
    "中位TTM含亏":      _med_all("PE_TTM"),
    "中位静态剔亏":     _med("PE_LAR"),
    "中位静态含亏":     _med_all("PE_LAR"),
    "截尾10%均TTM":     _trim("PE_TTM", 0.10),
    "截尾20%均TTM":     _trim("PE_TTM", 0.20),
    "截尾10%均静态":    _trim("PE_LAR", 0.10),
    "截尾20%均静态":    _trim("PE_LAR", 0.20),
    "等权整体法(调和)": _harm,
    "加权整体法":       _wagg,
}
# 兼容 s38 网格里的历史命名
ALGAS_ALIAS = {"加权TTM分子全": "加权整体法", "加权TTM剔亏": "加权整体法"}

def algo_fn(name):
    return ALGOS.get(ALGAS_ALIAS.get(name, name))

# 面向界面的中文说明
MODE_CN = {
    "current": "当前成分(固定)",
    "pit": "当时成分(按纳入日期还原)",
    "mktcap": "当时成分(按市值排名还原)",
    "bspit": "当时成分(月度名单)",
    "lixinger": "当时成分(理杏仁历史时点名单)",
}
ALGO_CN = {
    "中位TTM剔亏": "滚动PE·剔除亏损·取中位",
    "中位静态剔亏": "静态PE·剔除亏损·取中位",
    "中位TTM含亏": "滚动PE·含亏损·取中位",
    "中位静态含亏": "静态PE·含亏损·取中位",
    "截尾10%均TTM": "滚动PE·去高低各10%·取均值",
    "截尾20%均TTM": "滚动PE·去高低各20%·取均值",
    "截尾10%均静态": "静态PE·去高低各10%·取均值",
    "截尾20%均静态": "静态PE·去高低各20%·取均值",
    "等权整体法(调和)": "等权整体法(收益加权)",
    "加权整体法": "市值加权整体法",
}

def describe(algo, mode):
    return f"{ALGO_CN.get(algo, algo)} · {MODE_CN.get(mode, mode)} · PB=剔亏中位"

def pb_fn(p):
    """PB 统一用 剔亏中位(与E大PB列一致, 已验证 ±1%)"""
    v = [x for x in (_pos(r.get("PB_MRQ")) for r in p) if x]
    return st.median(v) if v else None

# ---------- 名单池 ----------
def pool(name, snap, ctx, mode="current"):
    """snap: {股票代码: {'PE_TTM','PE_LAR','PB_MRQ','TOTAL_MARKET_CAP'}}"""
    if name == "全市场":
        return [r for c, r in snap.items() if c.startswith(("60", "00"))]
    if name == "全市场加权":
        return list(snap.values())
    if name == "全市场·沪深京":
        return list(snap.values())
    if name == "全市场·主板(E大口径)":
        return [r for c, r in snap.items() if c.startswith(("60", "00"))]
    if mode == "mktcap" and name in MKRULE:
        prefs, lo, hi = MKRULE[name]
        cand = sorted(((c, r) for c, r in snap.items()
                       if c.startswith(prefs) and r.get("TOTAL_MARKET_CAP")),
                      key=lambda x: -x[1]["TOTAL_MARKET_CAP"])
        return [r for _, r in cand[lo:hi]]
    if mode == "lixinger":
        # 理杏仁历史时点成分(按月/真值日快照, 取不晚于当日的最近一次)
        j = (_lix().get(name) or {}).get("asof") or {}
        ds = [d for d in j if d <= ctx.date]
        if not ds: return []
        return [snap[c] for c in j[max(ds)] if c in snap]
    if name == "创业板综":
        return [r for c, r in snap.items() if c.startswith("30")]
    idx_code = ctx.cur_map.get(name)
    codes = ctx.current.get(name)
    if mode == "bspit" and name in BSKEY:
        months = [m for m in ctx.bspit if ctx.bspit[m].get(BSKEY[name]) and m <= ctx.date]
        if not months: return []
        return [snap[c.split(".")[-1]] for c in ctx.bspit[max(months)][BSKEY[name]]
                if c.split(".")[-1] in snap]
    if not codes:
        return []
    if mode == "pit":
        m = ctx.sina_pit.get(idx_code or "")
        if not m: return []
        codes = [c for c in codes if c in m and m[c] <= ctx.date]
    return [snap[c] for c in codes if c in snap]

def series_value(snap, name, cfg, ctx):
    """返回 (pe, pb, n)。cfg[name] 缺省时退回统一口径 中位TTM剔亏/current"""
    c = cfg.get(name) or {}
    mode = c.get("list", "current")
    fn = algo_fn(c.get("algo") or "中位TTM剔亏") or ALGOS["中位TTM剔亏"]
    p = pool(name, snap, ctx, mode)
    if not p: return None, None, 0
    try:
        pe = fn(p)
    except Exception:
        pe = None
    if not pe: return None, None, len(p)
    return round(pe, 3), (round(pb_fn(p), 3) if pb_fn(p) else None), len(p)

if __name__ == "__main__":
    cfg = load_config()
    print("配置指数数:", len(cfg))
    for k, v in list(cfg.items())[:5]: print("  ", k, v)
