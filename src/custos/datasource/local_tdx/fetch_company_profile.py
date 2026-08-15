# -*- coding: utf-8 -*-
"""公司「地位」证据采集：东财 F10 公司概况 → 关键词扫描 → JSONL 台账（2026-08-14，v0.59，owner 拍板）。

做什么：拉全市场 A 股的 F10「公司概况」（公司简介 ORG_PROFILE + 经营范围
BUSINESS_SCOPE），按行业地位关键词（唯一/龙头/最大/第一/领先/市占率…）扫描，
命中词与代表句落 `data/fundamentals/company_profile.jsonl`，供选股链作
**基本面证据字段**（company_position，evidence_only——不进技术分/分层）。

⚠️ 证据等级自知（写进每条记录的口径注释）：
- 文本是**公司自述/第三方整理**，不是独立事实核查——「本公司是行业龙头」谁都能写。
  所以只作 evidence 层参考，**不得**给硬权重、不得进 gate。
- 无 PIT 属性：简介随时会被公司更新，无法回溯「当时怎么写的」⇒ 只能用于 live/近端，
  不得用于历史回测特征（与 pit_financials 的公告日口径不同，那边是 PIT 这边不是）。

口径选择：通达信本地没有公司介绍文本（vipdoc 只有行情 .day 与财务 cw，F10 文字
不落地）⇒ 走东财 F10 接口，与 fetch_market_cap / fetch_pit_financials 同一体系。
实测（2026-08-14）：`PageAjax?code=SH600519` 返回 jbzl[0].ORG_PROFILE / BUSINESS_SCOPE。

用法：
  # 全市场增量拉取（已在台账的跳过；~5500 只约 15-25 分钟）
  uv run python src/custos/datasource/local_tdx/fetch_company_profile.py
  # 只扫关键词不联网（台账文本变了/词表改了之后重扫）
  uv run python src/custos/datasource/local_tdx/fetch_company_profile.py --rescan
  # 指定代码 / 全量重拉
  uv run python ... --codes 600000,600519
  uv run python ... --force
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

from custos.core.code_utils import market_of  # noqa: E402
from custos.core.paths import DATA, cn_today  # noqa: E402

API = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax"
UA = {"User-Agent": "Mozilla/5.0"}
_NO_PROXIES: dict = {
    "http": None,
    "https": None,
}  # 禁用环境代理（同 fetch_pit_financials）

OUT_DIR = DATA / "fundamentals"
LEDGER = OUT_DIR / "company_profile.jsonl"

# 行业地位关键词（owner 2026-08-14 定方向：唯一/龙头/最大 等）。
# 长词在前（先匹配长词，避免「全球领先」被「领先」截走 snippet 定位）。
POSITION_KEYWORDS = [
    "全球领先",
    "世界领先",
    "国际领先",
    "国内领先",
    "全国领先",
    "行业领先",
    "全球第一",
    "世界第一",
    "全国第一",
    "行业第一",
    "全球之最",
    "市占率第一",
    "市场占有率第一",
    "唯一",
    "龙头",
    "最大",
    "第一",
    "领先",
    "市占率",
    "市场占有率",
    "寡头",
    "垄断",
    "独角兽",
]


def scan_position_keywords(text: str) -> dict[str, Any]:
    """在简介/经营范围文本里扫行业地位关键词。

    返回 {keywords: [命中词...], snippet: 首个命中句}。无命中 keywords=[]。
    snippet 取**最长命中词**所在句（长词信息量高），截断到 120 字。
    """
    hits = [kw for kw in POSITION_KEYWORDS if kw in (text or "")]
    snippet = ""
    if hits:
        longest = max(hits, key=len)
        for seg in (text or "").replace("\n", "").replace(" ", "").split("。"):
            if longest in seg:
                snippet = seg.strip()[:120]
                break
    return {"keywords": hits, "snippet": snippet}


def fetch_one(code6: str, session: Optional[requests.Session] = None) -> dict[str, Any]:
    """拉一只票的公司概况。失败返回 {"available": False, "error": ...}，绝不 raise。"""
    mkt = market_of(code6)
    if not mkt:
        return {"available": False, "error": f"无法判交易所: {code6}"}
    try:
        req = session or requests
        r = req.get(
            API,
            params={"code": f"{mkt}{code6}"},
            headers=UA,
            timeout=15,
            proxies=_NO_PROXIES,
        )
        r.encoding = "utf-8"  # EM 返回 UTF-8；requests 按头猜会错（中文变乱码）
        d = r.json()
        rows = d.get("jbzl") or []
        if not rows:
            return {"available": False, "error": "jbzl 为空"}
        j = rows[0]
        profile = (j.get("ORG_PROFILE") or "").strip()
        scope = (j.get("BUSINESS_SCOPE") or "").strip()
        scan = scan_position_keywords(profile + "。" + scope)
        return {
            "available": True,
            "name": j.get("SECURITY_NAME_ABBR") or "",
            "industry_em": j.get("EM2016") or "",
            "profile": profile,
            "business_scope": scope,
            **scan,
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


def load_ledger(path: Optional[Path] = None) -> dict[str, dict]:
    """读台账 → {code6: record}。缺失/坏行跳过（坏行不炸全量）。"""
    p = path or LEDGER
    out: dict[str, dict] = {}
    if not p.is_file():
        return out
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        code = str(rec.get("code") or "")[:6]
        if code:
            out[code] = rec
    return out


def _write_ledger(records: dict[str, dict], path: Optional[Path] = None) -> None:
    """全量原子落盘（tmp + replace）——中断不留半截 JSONL。"""
    p = path or LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for code in sorted(records):
            f.write(
                json.dumps(records[code], ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
    tmp.replace(p)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--codes", default="", help="逗号分隔 6 位代码；缺省=vipdoc 全宇宙")
    ap.add_argument("--limit", type=int, default=0, help="只拉前 N 只（试跑用）")
    ap.add_argument("--force", action="store_true", help="忽略台账全量重拉")
    ap.add_argument("--rescan", action="store_true", help="不联网，只重扫关键词")
    ap.add_argument("--sleep", type=float, default=0.12, help="请求间隔秒（默认 0.12）")
    args = ap.parse_args(argv)

    ledger = load_ledger()

    if args.rescan:
        n_hit = 0
        for rec in ledger.values():
            scan = scan_position_keywords(
                (rec.get("profile") or "") + "。" + (rec.get("business_scope") or "")
            )
            rec.update(scan)
            n_hit += bool(scan["keywords"])
        _write_ledger(ledger)
        print(f"[rescan] {len(ledger)} 只重扫完成，命中关键词 {n_hit} 只 → {LEDGER}")
        return 0

    if args.codes:
        codes = [c.strip()[:6] for c in args.codes.split(",") if c.strip()]
    else:
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

        codes = local_tdx_data.list_local_vipdoc_codes()
    if not args.force:
        codes = [c for c in codes if c not in ledger]
    if args.limit:
        codes = codes[: args.limit]
    if not codes:
        print("[done] 无待拉取代码（台账已覆盖；--force 全量重拉）")
        return 0

    print(f"[fetch] 待拉 {len(codes)} 只（台账已有 {len(ledger)} 只）")
    sess = requests.Session()
    t0 = time.time()
    n_ok = 0
    for i, code in enumerate(codes, 1):
        rec = fetch_one(code, sess)
        rec["code"] = code
        rec["fetched_at"] = cn_today().isoformat()
        ledger[code] = rec
        n_ok += bool(rec.get("available"))
        if i % 200 == 0 or i == len(codes):
            _write_ledger(ledger)  # 周期落盘：中断不丢进度
            dt = time.time() - t0
            print(
                f"[fetch] {i}/{len(codes)}（成功 {n_ok}，{dt:.0f}s）",
                file=sys.stderr,
                flush=True,
            )
        time.sleep(args.sleep)
    n_kw = sum(1 for r in ledger.values() if r.get("keywords"))
    print(
        f"[done] 本轮 {len(codes)} 只（成功 {n_ok}）；台账总 {len(ledger)} 只，"
        f"命中地位关键词 {n_kw} 只 → {LEDGER}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
