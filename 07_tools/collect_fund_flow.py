# -*- coding: utf-8 -*-
"""Collect fund flow rank from East Money direct API (no akshare dependency)."""
from __future__ import annotations
import json, sys, time
from datetime import date, datetime
from pathlib import Path
import requests

from net_retry import fetch_with_retry

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from paths import BASE

EM_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?fid=f62&po=1&pz=200&pn=1&np=1&fltt=2&invt=2"
    "&ut=b2884a393a59ad64002292a3e90d46a5"
    "&fs=m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2"
    "&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124"
)

# Sector fund flow: industry + concept
SECTOR_URLS = {
    "industry": (
        "https://push2.eastmoney.com/api/qt/clist/get"
        "?fid=f62&po=1&pz=50&pn=1&np=1&fltt=2&invt=2"
        "&ut=b2884a393a59ad64002292a3e90d46a5"
        "&fs=m:90+t:2+f:!50"
        "&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f124"
    ),
    "concept": (
        "https://push2.eastmoney.com/api/qt/clist/get"
        "?fid=f62&po=1&pz=100&pn=1&np=1&fltt=2&invt=2"
        "&ut=b2884a393a59ad64002292a3e90d46a5"
        "&fs=m:90+t:3+f:!50"
        "&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f124"
    ),
}


def fetch_json(url: str) -> dict:
    s = requests.Session()
    s.trust_env = False  # ignore system proxy
    r = fetch_with_retry(url, timeout=15, session=s,
                         headers={"User-Agent": "Mozilla/5.0"}, proxies={"http": None, "https": None})
    r.raise_for_status()
    return r.json()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    today = args.date
    OUT = BASE / "01_data" / "market" / f"{today}_fund_flow_rank.json"

    # Individual stock fund flow rank (top 200)
    try:
        data = fetch_json(EM_URL)
    except Exception as e:
        print(f"[WARN] stock fund flow fetch failed: {e}", file=sys.stderr)
        sys.exit(1)
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
    sectors = {}
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
            time.sleep(1)  # rate limit
        except Exception as e:
            sectors[sec_type] = []
            print(f"[WARN] sector {sec_type} failed: {e}")

    result = {
        "date": today,
        "collected_at": datetime.now().isoformat(),
        "stock_rank": stocks,
        "sector_rank": sectors,
        "source": "eastmoney_direct_api",
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] fund_flow_rank: {len(stocks)} stocks, {len(sectors.get('industry',[]))} industry sectors, {len(sectors.get('concept',[]))} concept sectors -> {OUT.name}")


if __name__ == "__main__":
    main()
