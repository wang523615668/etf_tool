#!/usr/bin/env python3
"""lixinger_cons_update.py — 刷新「理杏仁历史时点成分」表
用途: 自算估值依赖 data/ed_cons_lixinger.json (逐指数按月快照). 指数半年调样+临时调整,
      需定期(月度)追加最新快照, 否则新调整日之后会用旧名单 → 名单漂移回归。
          追加: 每月最后交易日 + 最近一个月内每个交易日(补掉月末到当日的空档)
输出: data/ed_cons_lixinger.json (原地追加, 只增不减; 结构 {指数名:{code,snapshots,changes,asof}})
用法: python app/lixinger_cons_update.py [--days 40]
"""
import json, os, sys, time, argparse, datetime as dt
import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = f"{BASE}/data"
FP = f"{DATA}/ed_cons_lixinger.json"
API = "https://open.lixinger.com/api/cn/index/constituents"

def token():
    for p in [f"{BASE}/jztz/token.conf", f"{DATA}/lixinger_token.conf"]:
        if os.path.exists(p):
            t = open(p).read().strip()
            try:
                j = json.loads(t)
                return j.get("token") or list(j.values())[0]
            except Exception:
                return t
    raise SystemExit("找不到理杏仁 token (jztz/token.conf)")

def fetch(codes, date, tok, tries=3):
    for k in range(tries):
        try:
            r = requests.post(API, json={"token": tok, "stockCodes": codes, "date": date}, timeout=120)
            j = r.json()
            if j.get("data") is not None:
                return {it["stockCode"]: [c["stockCode"] for c in (it.get("constituents") or [])]
                        for it in j["data"]}
        except Exception as e:
            print(f"    {date} 重试{k+1}: {str(e)[:70]}")
        time.sleep(3 + 3 * k)
    return {}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=40, help="向前回补多少个自然日(默认40)")
    a = ap.parse_args()
    j = json.load(open(FP))
    tok = token()
    today = dt.date.today()
    days = [(today - dt.timedelta(days=i)).isoformat() for i in range(a.days, -1, -1)]
    days = [d for d in days if d >= "2018-01-01"]
    print(f"刷新 {len(j)} 个指数 × {len(days)} 个交易日")
    added = 0
    for idx, rec in j.items():
        code = rec.get("code")
        asof = rec.get("asof") or {}
        new = 0
        for d in days:
            if d in asof:
                continue                      # 已有该日快照
            r = fetch([code], d, tok)
            v = r.get(code)
            if not v:
                continue
            # 只在成员集合变化时落点(压缩存储)
            prev = [asof[k] for k in sorted(asof) if k <= d]
            if prev and sorted(v) == sorted(prev[-1]):
                continue
            asof[d] = sorted(v)
            new += 1
            time.sleep(0.3)
        if new:
            rec["asof"] = asof
            rec["changes"] = len(asof)
            added += new
            print(f"  {idx:<10} 新增 {new} 个变换点")
    if added:
        bak = f"{FP}.bak-{dt.datetime.now():%Y%m%d-%H%M}"
        json.dump(json.load(open(FP)), open(bak, "w"), ensure_ascii=False)   # 备份原文件
        json.dump(j, open(FP, "w"), ensure_ascii=False)
        print(f"完成: 新增 {added} 个变换点; 备份 {os.path.basename(bak)}")
    else:
        print("无新增(名单无变化或已是最新)")

if __name__ == "__main__":
    main()
