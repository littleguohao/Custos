# -*- coding: utf-8 -*-
"""Point-in-time 财务数据:按报告期批量拉东方财富业绩报表,以**公告日**为可见日落盘。

解决什么问题:B1_BACKTEST_FINDINGS 结论#11/#14 的"基本面腿进不来"——mootdx Affair 的财报
**没有发布日期**,任何基本面特征在回测里都是 look-ahead,只能 live 验证。本脚本取的
`NOTICE_DATE`(公告日期)就是缺的那把 PIT 钥匙:一季报报告期 3/31,公告日中位在 4/29
(滞后 ~29 天),年报滞后中位 ~113 天。用报告期当可见日等于白拿一个月的未来信息。

数据质量(2026-07-31 四个报告期全市场实测,见 §数据质量):
  - `NOTICE_DATE` 覆盖率 100%、零缺失、**零逆序**(无一条公告日早于报告期);
  - 返回混入新三板,**必须按 SECURITY_TYPE 过滤**(2025-12-31 共 11514 行里新三板 5890、A股仅 5544);
  - 同比字段(YSTZ/SJLTZ/YSHZ/SJLHZ)会在**次年同期报告发布时被重算**,不是 PIT 值:
    2024-12-31 重述比例 100.0% vs 2025-12-31(次年未到)3.2%;2025-03-31 99.1% vs 2026-03-31 0.5%。
    ⇒ **本脚本只存绝对值字段,同比留给特征层用上年同期绝对值自己算**(各期各带自己的公告日)。
  - `EITIME`(入库时刻)常早于公告日一天(晚间披露),故可见日取**公告日的次一交易日**。

⚠️ 限度:`UPDATE_DATE` 只是"最后更新时间",证明不了绝对值字段从未被改。真实重述率上界由
上面 0.5%/3.2% 给出(次年同期未发布时的重述比例),不是零。PIT 库**只能向前积累、无法回溯
重建**;要更强保证需 tushare `income`/`balancesheet` 的 `f_ann_date` + `update_flag`(需积分)。

用法:
  # 拉指定报告期(可多个)
  uv run python 07_tools/local_tdx/fetch_pit_financials.py --periods 2024-03-31 2024-06-30
  # 批量补历史(2015 年起所有季度末)
  uv run python 07_tools/local_tdx/fetch_pit_financials.py --since 2015 --all-quarters
  # as-of 查询:某日可见的最新一期
  uv run python 07_tools/local_tdx/fetch_pit_financials.py --as-of 2024-05-06 --code 600000
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import requests

TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(TOOLS),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from paths import BASE  # noqa: E402

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UA = {"User-Agent": "Mozilla/5.0"}
OUT_DIR = BASE / "01_data" / "fundamentals"
LEDGER = OUT_DIR / "pit_financials.jsonl"

# 只保留**绝对值**字段:同比/环比会被次年同期报告重算,不是 PIT 值(见模块 docstring)
VALUE_FIELDS = {
    "BASIC_EPS": "eps",                     # 基本每股收益
    "DEDUCT_BASIC_EPS": "eps_deduct",       # 扣非每股收益
    "TOTAL_OPERATE_INCOME": "revenue",      # 营业总收入
    "PARENT_NETPROFIT": "net_profit",       # 归母净利润
    "WEIGHTAVG_ROE": "roe_waa",             # 加权平均净资产收益率
    "BPS": "bps",                           # 每股净资产
    "MGJYXJJE": "ocf_ps",                   # 每股经营现金流
    "XSMLL": "gross_margin",                # 销售毛利率
}
# 派生字段一律不存,列出来是为了防止以后有人"顺手"加回去
_REFUSED_FIELDS = ("YSTZ", "SJLTZ", "YSHZ", "SJLHZ")
A_SHARE_TYPES = {"A股"}


def _d(v) -> str:
    return str(v or "")[:10]


def quarter_ends(since_year: int, until: str | None = None) -> list[str]:
    """since_year 起的所有季度末(YYYY-03-31/06-30/09-30/12-31),不超过 until(默认今天)。"""
    stop = until or date.today().isoformat()
    out = []
    for y in range(since_year, date.today().year + 1):
        for mmdd in ("03-31", "06-30", "09-30", "12-31"):
            d = f"{y}-{mmdd}"
            if d <= stop:
                out.append(d)
    return out


def fetch_period(report_date: str, page_size: int = 500, max_pages: int = 40,
                 session=None) -> list[dict]:
    """拉一个报告期的全市场业绩报表原始行。接口与 akshare stock_yjbb_em 同源。"""
    s = session or requests.Session()
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "sortColumns": "UPDATE_DATE,SECURITY_CODE", "sortTypes": "-1,-1",
            "pageSize": page_size, "pageNumber": page,
            "reportName": "RPT_LICO_FN_CPD", "columns": "ALL",
            "filter": f"(REPORTDATE='{report_date}')",
        }
        r = s.get(API, params=params, headers=UA, timeout=30,
                  proxies={"http": None, "https": None})
        r.raise_for_status()
        result = (r.json() or {}).get("result") or {}
        data = result.get("data") or []
        if not data:
            break
        rows.extend(data)
        if page >= (result.get("pages") or 1):
            break
    return rows


def normalize(rows: list[dict], report_date: str,
              a_share_only: bool = True) -> tuple[list[dict], dict]:
    """原始行 → PIT 记录。返回 (记录, 统计)。

    记录键 = (code, report_date, notice_date);notice_date 缺失或早于报告期的行**丢弃**
    (公告日不晚于报告期在现实中不可能,宁可不要也不能拿一条错的可见日进回测)。
    """
    out, stats = [], {"raw": len(rows), "dropped_type": 0, "dropped_no_notice": 0,
                      "dropped_bad_lag": 0, "kept": 0, "types": {}}
    rd = date.fromisoformat(report_date)
    for x in rows:
        stype = str(x.get("SECURITY_TYPE") or "")
        stats["types"][stype] = stats["types"].get(stype, 0) + 1
        if a_share_only and stype not in A_SHARE_TYPES:
            stats["dropped_type"] += 1
            continue
        code = str(x.get("SECURITY_CODE") or "").strip()
        notice = _d(x.get("NOTICE_DATE"))
        if not code or not notice:
            stats["dropped_no_notice"] += 1
            continue
        try:
            lag = (date.fromisoformat(notice) - rd).days
        except ValueError:
            stats["dropped_no_notice"] += 1
            continue
        if lag <= 0:
            stats["dropped_bad_lag"] += 1
            continue
        rec = {
            "code": code,
            "name": str(x.get("SECURITY_NAME_ABBR") or ""),
            "report_date": report_date,
            "notice_date": notice,
            "lag_days": lag,
            "eitime": str(x.get("EITIME") or "")[:19],
            "update_date": _d(x.get("UPDATE_DATE")),
            "board": str(x.get("TRADE_MARKET") or ""),
            "industry": str(x.get("PUBLISHNAME") or ""),
        }
        for src, dst in VALUE_FIELDS.items():
            v = x.get(src)
            rec[dst] = None if v is None else float(v)
        out.append(rec)
        stats["kept"] += 1
    return out, stats


def merge_write(records: list[dict], path: Path = LEDGER) -> dict:
    """按 (code, report_date, notice_date) 去重合并写 JSONL(原子写)。

    保留同一报告期的**多个公告日版本**:同一 (code, report_date) 若出现新的 notice_date
    (更正/补披露),两条都留下,as-of 查询时按 notice_date <= T 取最新一条即可重现当时视角。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[tuple, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            existing[(r.get("code"), r.get("report_date"), r.get("notice_date"))] = r
    before = len(existing)
    for r in records:
        existing[(r["code"], r["report_date"], r["notice_date"])] = r
    rows = sorted(existing.values(), key=lambda r: (r["report_date"], r["code"],
                                                    r["notice_date"]))
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    return {"before": before, "after": len(rows), "added": len(rows) - before}


def load_ledger(path: Path = LEDGER) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def as_of(records: list[dict], day: str, code: str | None = None,
          visible_next_day: bool = True) -> dict[str, dict]:
    """**PIT 查询**:截至 day 可见的最新一期财报,按 code 返回。

    可见判定:`notice_date < day`(visible_next_day=True,默认)——公告常在**盘后/晚间**发布
    (实测 EITIME 多早于公告日一天),故公告当日不算可见,次一交易日才可用。
    要严格复现"公告当日收盘后可用"可传 visible_next_day=False(即 notice_date <= day)。

    同一 code 若有多期,取 report_date 最大者;同报告期多版本取 notice_date 最大且已可见者。
    """
    best: dict[str, dict] = {}
    for r in records:
        if code and r.get("code") != code:
            continue
        nd = r.get("notice_date") or ""
        if not nd:
            continue
        visible = (nd < day) if visible_next_day else (nd <= day)
        if not visible:
            continue
        c = r["code"]
        cur = best.get(c)
        key = (r.get("report_date") or "", nd)
        if cur is None or key > (cur.get("report_date") or "", cur.get("notice_date") or ""):
            best[c] = r
    return best


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PIT 财务:按报告期拉取,以公告日为可见日")
    ap.add_argument("--periods", nargs="*", help="报告期,如 2024-03-31(可多个)")
    ap.add_argument("--since", type=int, help="从该年起拉所有季度末")
    ap.add_argument("--all-quarters", action="store_true", help="配合 --since 使用")
    ap.add_argument("--out", default=str(LEDGER), help="输出 JSONL 路径")
    ap.add_argument("--include-non-ashare", action="store_true",
                    help="保留新三板/B股/CDR(默认只留 A 股;不加过滤会把 5890 只新三板灌进宇宙)")
    ap.add_argument("--as-of", help="PIT 查询:该日可见的最新一期(不拉网络)")
    ap.add_argument("--code", help="配合 --as-of 限定单只")
    ap.add_argument("--visible-same-day", action="store_true",
                    help="--as-of 时把公告当日算作可见(默认次日,因公告多在盘后发布)")
    args = ap.parse_args(argv)
    out_path = Path(args.out)

    if args.as_of:
        recs = load_ledger(out_path)
        if not recs:
            print(f"[ERR] 台账为空: {out_path}(先拉取)", file=sys.stderr)
            return 2
        got = as_of(recs, args.as_of, code=args.code,
                    visible_next_day=not args.visible_same_day)
        print(f"截至 {args.as_of} 可见:{len(got)} 只"
              f"({'公告当日即可见' if args.visible_same_day else '公告次日起可见'})")
        for c, r in sorted(got.items())[:20]:
            print(f"  {c} {r['name']:<8} 报告期={r['report_date']} 公告={r['notice_date']}"
                  f"(滞后{r['lag_days']}天) eps={r.get('eps')} roe={r.get('roe_waa')}")
        if len(got) > 20:
            print(f"  ...(共 {len(got)} 只)")
        return 0

    periods = list(args.periods or [])
    if args.since:
        periods += quarter_ends(args.since)
    periods = sorted(set(periods))
    if not periods:
        ap.error("需提供 --periods 或 --since,或用 --as-of 查询")

    session = requests.Session()
    total = 0
    for p in periods:
        try:
            raw = fetch_period(p, session=session)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {p} 拉取失败: {exc}", file=sys.stderr)
            continue
        recs, st = normalize(raw, p, a_share_only=not args.include_non_ashare)
        res = merge_write(recs, out_path)
        total += res["added"]
        lags = sorted(r["lag_days"] for r in recs)
        med = lags[len(lags) // 2] if lags else None
        print(f"[OK] {p}: 原始 {st['raw']} → A股保留 {st['kept']} "
              f"(非A股剔 {st['dropped_type']} / 无公告日剔 {st['dropped_no_notice']} / "
              f"公告日不晚于报告期剔 {st['dropped_bad_lag']}), 滞后中位 {med} 天, "
              f"新增 {res['added']} 条(台账 {res['after']})")
    print(f"\n[OK] 共新增 {total} 条 → {out_path}")
    print("提示:同比请用上年同期绝对值自行计算,勿用接口的 YSTZ/SJLTZ(次年同期发布时会被重算)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
