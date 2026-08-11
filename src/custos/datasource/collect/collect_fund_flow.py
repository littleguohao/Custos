# -*- coding: utf-8 -*-
"""Collect fund flow rank from East Money direct API (no akshare dependency)."""
from __future__ import annotations
import json, sys, time
from datetime import date, datetime
from pathlib import Path
import requests

# ⚠️ 本文件在 src 的**子目录**里：作为 __main__ 跑时 sys.path[0] 是本目录，
# 必须把 src 自己加进 sys.path，否则本地模块导入会失败。
# ⚠️ 必须放在**第一个本地模块导入之前** —— 放在 `from paths import` 前是不够的，
#    若有更早的本地导入（如 net_retry）会先失败。
_TOOLS = Path(__file__).resolve().parents[1]
for _bp in (_TOOLS, _TOOLS.parent / "core"):  # core/: paths 等 L0 模块
    if str(_bp) not in sys.path:
        sys.path.insert(0, str(_bp))

from custos.core.net_retry import fetch_with_retry

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import BASE, cn_today, cn_now, MARKET_DIR
from custos.core.contracts import require  # noqa: E402

# 东方财富 push2 的**公开** ut 参数(网页端硬编码在前端 JS 里,非账号凭据、非密钥,
# 全网通用)。抽成常量只为不再散落三处魔法串;它不是 secret,无需进环境变量。
EM_UT = "b2884a393a59ad64002292a3e90d46a5"

_STOCK_FIELDS = "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124"
_SECTOR_FIELDS = "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f124"

EM_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?fid=f62&po=1&pz=200&pn=1&np=1&fltt=2&invt=2"
    f"&ut={EM_UT}"
    "&fs=m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2"
    f"&fields={_STOCK_FIELDS}"
)

# Sector fund flow: industry + concept
SECTOR_URLS = {
    "industry": (
        "https://push2.eastmoney.com/api/qt/clist/get"
        "?fid=f62&po=1&pz=50&pn=1&np=1&fltt=2&invt=2"
        f"&ut={EM_UT}"
        "&fs=m:90+t:2+f:!50"
        f"&fields={_SECTOR_FIELDS}"
    ),
    "concept": (
        "https://push2.eastmoney.com/api/qt/clist/get"
        "?fid=f62&po=1&pz=100&pn=1&np=1&fltt=2&invt=2"
        f"&ut={EM_UT}"
        "&fs=m:90+t:3+f:!50"
        f"&fields={_SECTOR_FIELDS}"
    ),
}


def fetch_json(url: str) -> dict:
    s = requests.Session()
    s.trust_env = False  # ignore system proxy
    r = fetch_with_retry(url, timeout=15, session=s,
                         headers={"User-Agent": "Mozilla/5.0"}, proxies={"http": None, "https": None})
    r.raise_for_status()
    return r.json()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=cn_today().strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)
    today = args.date
    OUT = MARKET_DIR / f"{today}_fund_flow_rank.json"

    # Individual stock fund flow rank (top 200)
    try:
        data = fetch_json(EM_URL)
    except Exception as e:
        print(f"[WARN] stock fund flow fetch failed: {e}", file=sys.stderr)
        return 1
    stocks = []
    for item in data.get("data", {}).get("diff", []):
        stocks.append({
            "code": str(item.get("f12", "")),
            "name": item.get("f14", ""),
            "price": item.get("f2"),
            "change_pct": item.get("f3"),
            "main_net_inflow": item.get("f62"),
            "main_net_pct": item.get("f184"),
            "super_large_net": item.get("f66"),
            "super_large_pct": item.get("f69"),
            "large_net": item.get("f72"),
            "large_pct": item.get("f75"),
            "medium_net": item.get("f78"),
            "medium_pct": item.get("f81"),
            "small_net": item.get("f84"),
            "small_pct": item.get("f87"),
        })

    # Sector fund flow
    # 失败与"今天真的没有资金流入"必须可区分:以前失败写 []，下游(enrich_candidates
    # 的 load_fund_flow / 板块共振打分)会把「拉取失败」读成「该板块无净流入」，
    # 直接影响候选打分。sector_rank 保持 {类型: list} 形态(向后兼容)，
    # 另写 sector_rank_status 显式标 ok/failed + error。
    sectors: dict[str, list] = {}
    sector_status: dict[str, dict] = {}
    for sec_type, sec_url in SECTOR_URLS.items():
        try:
            sec_data = fetch_json(sec_url)
            sec_list = []
            for item in sec_data.get("data", {}).get("diff", []):
                sec_list.append({
                    "code": str(item.get("f12", "")),
                    "name": item.get("f14", ""),
                    "change_pct": item.get("f3"),
                    "main_net_inflow": item.get("f62"),
                    "main_net_pct": item.get("f184"),
                })
            sectors[sec_type] = sec_list
            sector_status[sec_type] = {"status": "ok", "count": len(sec_list)}
            time.sleep(1)  # rate limit
        except Exception as e:
            sectors[sec_type] = []
            sector_status[sec_type] = {"status": "failed", "count": 0, "error": str(e)}
            print(f"[WARN] sector {sec_type} failed: {e}")

    failed = [k for k, v in sector_status.items() if v["status"] != "ok"]
    result = {
        "date": today,
        "collected_at": cn_now().isoformat(),
        "status": "partial" if failed else "ok",
        "stock_rank": stocks,
        "sector_rank": sectors,
        "sector_rank_status": sector_status,
        "source": "eastmoney_direct_api",
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    require("fund_flow_rank", result)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] fund_flow_rank: {len(stocks)} stocks, {len(sectors.get('industry',[]))} industry sectors, {len(sectors.get('concept',[]))} concept sectors -> {OUT.name}")
    if failed:
        print(f"[WARN] 板块资金流拉取失败: {', '.join(failed)}（已在 sector_rank_status 标记 failed，"
              f"下游不得把空列表读成「今天没有资金流入」）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
