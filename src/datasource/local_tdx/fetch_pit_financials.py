# -*- coding: utf-8 -*-
"""Point-in-time 财务数据:按报告期批量拉东方财富业绩报表,以**公告日**为可见日落盘。

解决什么问题:research/R5(0AMV 迟到)与 R3(判别力)记的"基本面腿进不来"——mootdx Affair 的财报
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
  uv run python src/datasource/local_tdx/fetch_pit_financials.py --periods 2024-03-31 2024-06-30
  # 批量补历史(2014 年起所有季度末)
  # ⚠️ 需留足一年跑道:2015 年信号的同比特征要用 2014 各期**当时可见**的版本,
  #    2015 年初的信号本身也要有已可见的 2014 财报,故起点必须比首个信号年早一年。
  uv run python src/datasource/local_tdx/fetch_pit_financials.py --since 2014 --all-quarters
  # as-of 查询:某日可见的最新一期
  uv run python src/datasource/local_tdx/fetch_pit_financials.py --as-of 2024-05-06 --code 600000
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

import requests

TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(TOOLS), str(TOOLS.parent / "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from paths import BASE, cn_today, DATA  # noqa: E402

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UA = {"User-Agent": "Mozilla/5.0"}
OUT_DIR = DATA / "fundamentals"
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

PAGE_SLEEP = 0.35        # 翻页间隔（秒）：东财 datacenter 无官方配额，连打会被静默限流


class FetchIncomplete(RuntimeError):
    """分页未拉完 / 响应残缺。残缺样本绝不能按成功落盘（宁可整期重拉）。"""


def _as_int(v):
    """转 int；不可解析或非有限值返回 None。

    ⚠️ 必须捕 `OverflowError`：这个函数解析东财响应自报的 `pages` / `count`，
    而 Python 的 `json.loads` **接受** `Infinity`（非标准但 json 模块允许），
    `int(float("inf"))` 抛的是 OverflowError —— 只 catch TypeError/ValueError
    会让一个畸形响应直接崩掉整次抓取，而本函数的全部用途就是「坏输入返回 None」。

    ⚠️ 为什么不并入 `code_utils.fnum`：语义不同 —— 这里要的是**整数截断**
    （`pages`/`count` 是页数），`fnum` 返回 float。同名不同语义的合并踩过一次
    （`b1_holding_state.finite` 与 `code_utils.finite` 失败语义相反），不再重复。
    """
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None


def _is_empty_ok(payload: dict) -> bool:
    """响应自称成功但 result 为空 ⇒ 该期/该日**确实**没有数据（未披露、非交易日）。

    只有这一种形态才允许当成「空但正常」；限流响应通常 success=False 或带非 0 code，
    以及翻页中途出现的空响应，都必须报残缺。
    """
    if payload.get("success") is False:
        return False
    if str(payload.get("code", 0)) not in ("0", "None"):
        return False
    return "result" in payload


def _brief(payload: dict, n: int = 160) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)[:n]
    except (TypeError, ValueError):
        return str(payload)[:n]


def _d(v) -> str:
    return str(v or "")[:10]


def quarter_ends(since_year: int, until: str | None = None) -> list[str]:
    """since_year 起的所有季度末(YYYY-03-31/06-30/09-30/12-31),不超过 until(默认今天)。"""
    stop = until or cn_today().isoformat()
    out = []
    for y in range(since_year, cn_today().year + 1):
        for mmdd in ("03-31", "06-30", "09-30", "12-31"):
            d = f"{y}-{mmdd}"
            if d <= stop:
                out.append(d)
    return out


def fetch_period(report_date: str, page_size: int = 500, max_pages: int = 40,
                 session=None, sleep: float = PAGE_SLEEP) -> list[dict]:
    """拉一个报告期的全市场业绩报表原始行。接口与 akshare stock_yjbb_em 同源。

    **分页完整性由接口自报的 pages/count 校验，残缺一律抛 FetchIncomplete。**
    原实现是 `if not data: break` —— 东财限流时返回 200 + 空 data，这个 break
    把限流当成「翻完了」：于是某期只拿到前 500 只也照样按 `[OK]` 落盘，
    回测拿这期算因子时全然不知样本残缺（`verify_ledger` 只能事后看出「行数偏少」，
    而且要等到有邻期可比）。宁可整期失败重拉，也不能落一份残缺样本。
    """
    s = session or requests.Session()
    rows: list[dict] = []
    pages = count = None
    for page in range(1, max_pages + 1):
        if page > 1 and sleep:
            time.sleep(sleep)                  # 主动限速：连打几十页必被静默限流
        params = {
            "sortColumns": "UPDATE_DATE,SECURITY_CODE", "sortTypes": "-1,-1",
            "pageSize": page_size, "pageNumber": page,
            "reportName": "RPT_LICO_FN_CPD", "columns": "ALL",
            "filter": f"(REPORTDATE='{report_date}')",
        }
        r = s.get(API, params=params, headers=UA, timeout=30,
                  proxies={"http": None, "https": None})
        r.raise_for_status()
        payload = r.json() or {}
        result = payload.get("result")
        if not isinstance(result, dict):
            if page == 1 and _is_empty_ok(payload):
                return []                      # 该报告期确实还没有数据（未到披露期）
            raise FetchIncomplete(
                f"{report_date} 第 {page} 页无 result 段（限流/异常响应），"
                f"已拿 {len(rows)} 行: {_brief(payload)}")
        data = result.get("data") or []
        pages, count = _as_int(result.get("pages")), _as_int(result.get("count"))
        if not data:
            if page == 1 and not count:
                return []                      # 真的没有这一期
            raise FetchIncomplete(
                f"{report_date} 第 {page} 页空响应，但接口声明 pages={pages} count={count}"
                f"（已拿 {len(rows)} 行）—— 疑似限流，不是翻完了")
        rows.extend(data)
        if pages is not None:
            if page >= pages:
                break
        elif count is not None and len(rows) >= count:
            break                              # 没给 pages 但行数已够
        else:
            raise FetchIncomplete(
                f"{report_date} 第 {page} 页未给 pages/count，无法判断是否翻完")
    else:
        raise FetchIncomplete(
            f"{report_date} 翻到 max_pages={max_pages} 仍未结束（声明 pages={pages}），"
            f"提高 max_pages 后重拉")
    if count is not None and len(rows) < count:
        raise FetchIncomplete(
            f"{report_date} 只拿到 {len(rows)}/{count} 行，样本残缺不落盘")
    return rows


def normalize(rows: list[dict], report_date: str,
              a_share_only: bool = True) -> tuple[list[dict], dict]:
    """原始行 → PIT 记录。返回 (记录, 统计)。

    记录键 = (code, report_date, notice_date);notice_date 缺失或早于报告期的行**丢弃**
    (公告日不晚于报告期在现实中不可能,宁可不要也不能拿一条错的可见日进回测)。
    """
    out, stats = [], {"raw": len(rows), "dropped_type": 0, "dropped_no_notice": 0,
                      "dropped_bad_lag": 0, "kept": 0, "bad_value": 0, "types": {}}
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
            try:
                rec[dst] = None if v is None else float(v)
            except (TypeError, ValueError):        # 脏值(如 "--")按缺失处理,绝不让一行炸掉批量
                rec[dst] = None
                stats["bad_value"] += 1
        out.append(rec)
        stats["kept"] += 1
    return out, stats


def merge_write(records: list[dict], path: str | Path = LEDGER) -> dict:
    """按 (code, report_date, notice_date) 去重合并写 JSONL(原子写)。

    保留同一报告期的**多个公告日版本**:同一 (code, report_date) 若出现新的 notice_date
    (更正/补披露),两条都留下,as-of 查询时按 notice_date <= T 取最新一条即可重现当时视角。
    """
    path = Path(path)
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


def load_ledger(path: str | Path = LEDGER) -> list[dict]:
    path = Path(path)
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


def verify_ledger(records: list[dict], since_year: int | None = None,
                  until: str | None = None, low_ratio: float = 0.6) -> dict:
    """台账完整性自检:应有报告期 vs 实有,列缺口 + 逐期行数异常 + 一年跑道检查。

    为什么必须自检:`as_of()` 遇到台账**缺期不会报错** —— 它只会静默返回上一个可见期,
    回测里就变成"用了 3 个月前的财报却以为是最新的"。缺口是静默的 look-behind,
    比抛异常危险得多。同理某期行数远低于邻期(分页中断/接口限流)也会让该期样本残缺。

    口径:
      - since_year 缺省时按台账最早报告期的年份推断,且**只从台账实际首期起对齐检查**
        (台账从年中起步时不误报年初各期);
      - until 缺省 = **今天**,不是台账最后一期 —— 台账停更两季也会在尾部把缺期报出来,
        报告单列"台账末至今"的缺期;
      - 行数低于**相邻 2~4 期中位数** low_ratio 倍即告警(全期中位会被 A 股扩容带偏,
        早期各期天然只数少,不算残缺);
      - 跑道检查:首个信号年的同比特征需要上一年度各期的当时可见版本,最早期上一年度
        各期缺失时 WARN(不判失败,但报告里明示)。
    """
    by_period: dict[str, set] = {}
    for r in records:
        rd = r.get("report_date")
        if rd:
            by_period.setdefault(rd, set()).add(r.get("code"))
    have = sorted(by_period)
    if not have:
        return {"ok": False, "error": "台账为空", "missing": [], "periods": []}
    y0 = since_year or int(have[0][:4])
    stop = until or cn_today().isoformat()
    expect = quarter_ends(y0, until=stop)
    if since_year is None:                            # 按实际首期对齐:年中起步不误报
        expect = [p for p in expect if p >= have[0]]
    missing = [p for p in expect if p not in by_period]
    tail_missing = [p for p in missing if p > have[-1]]     # 台账末至今的缺期(停更信号)
    counts = {p: len(by_period[p]) for p in have}
    med_all = int(statistics.median(counts.values())) if counts else 0
    thin = []
    for i, p in enumerate(have):                      # 邻期中位数:相邻 2~4 期(不含自身)
        neigh = [counts[q] for j, q in enumerate(have) if j != i and abs(j - i) <= 2]
        if not neigh:
            continue
        med = int(statistics.median(neigh))
        if med and counts[p] < med * low_ratio:
            thin.append({"period": p, "n_codes": counts[p], "neighbor_median": med})
    runway = [f"{y0 - 1}-{mmdd}" for mmdd in ("03-31", "06-30", "09-30", "12-31")]
    runway_missing = [p for p in runway if p not in by_period]
    out = {
        "ok": not missing and not thin,
        "n_records": len(records),
        "n_periods_have": len(have),
        "n_periods_expect": len(expect),
        "first": have[0], "last": have[-1], "checked_until": stop,
        "missing": missing,
        "tail_missing": tail_missing,
        "runway_missing": runway_missing,
        "median_codes_per_period": med_all,
        "thin_periods": thin,
        "periods": [{"period": p, "n_codes": counts[p]} for p in have],
    }
    lines = [f"台账 {len(records)} 条;报告期 实有 {len(have)} / 应有 {len(expect)} "
             f"({have[0]} ~ {have[-1]},自检终点 {stop});每期去重代码全期中位 {med_all}"]
    if missing:
        lines.append(f"  ⚠️ **缺 {len(missing)} 期**: {', '.join(missing)}")
        if tail_missing:
            lines.append(f"     其中**台账末({have[-1]})至今**缺 {len(tail_missing)} 期: "
                         f"{', '.join(tail_missing)} —— 台账疑似停更,必须补拉到最新")
        lines.append("     缺期不会让 as_of() 报错,只会静默返回上一期 ⇒ 回测会用陈旧财报,必须补拉")
    else:
        lines.append("  ✅ 报告期无缺口")
    if thin:
        lines.append(f"  ⚠️ **{len(thin)} 期行数异常偏少**(低于相邻 2~4 期中位的 {low_ratio:.0%}): "
                     + ", ".join(f"{t['period']}={t['n_codes']}(邻期中位 {t['neighbor_median']})"
                                 for t in thin))
        lines.append("     多为分页中断/接口限流导致该期样本残缺,建议 --periods 单独重拉")
    else:
        lines.append("  ✅ 各期行数无异常偏少")
    if runway_missing:
        lines.append(f"  ⚠️ **一年跑道缺失**: 最早期 {have[0]} 的上一年度({y0 - 1})缺 "
                     f"{len(runway_missing)} 期: {', '.join(runway_missing)}")
        lines.append(f"     若信号窗口从 {y0} 年起,其同比特征需要 {y0 - 1} 各期的当时可见版本"
                     f"(补拉:--since {y0 - 1} --all-quarters)")
    else:
        lines.append(f"  ✅ 一年跑道充足({y0 - 1} 年各期在台账中)")
    out["text"] = "\n".join(lines)
    return out


def prior_year_period(report_date: str) -> str:
    """上年同期报告期。同比必须**同口径比**:报告期是累计口径(一季报=Q1、半年报=H1 累计、
    三季报=前三季累计、年报=全年),拿一季报比半年报是错的。"""
    y, rest = str(report_date)[:4], str(report_date)[4:10]
    return f"{int(y) - 1}{rest}"


def as_of_period(records: list[dict], day: str, report_date: str,
                 code: str | None = None, visible_next_day: bool = True) -> dict[str, dict]:
    """**取某个特定报告期**在 day 时可见的版本(同比计算必需)。

    与 `as_of()` 的区别:`as_of` 给"最新可见期",本函数给"指定期的可见版本"。
    算同比要的是后者 —— 且上年同期也必须取**当时可见的版本**,不能拿今天的最终版
    (那是数值维度的 look-ahead)。同期多版本时取 notice_date 最大且已可见者。
    """
    best: dict[str, dict] = {}
    for r in records:
        if r.get("report_date") != report_date:
            continue
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
        if cur is None or nd > (cur.get("notice_date") or ""):
            best[c] = r
    return best


def _ratio(cur, prev):
    """同比 = cur/prev - 1。分母 <=0 或缺失一律返回 None(负基数的同比无经济含义)。"""
    if cur is None or prev is None:
        return None
    try:
        cur, prev = float(cur), float(prev)
    except (TypeError, ValueError):
        return None
    if prev <= 0:
        return None
    return cur / prev - 1


def pit_features(records: list[dict], day: str, code: str,
                 visible_next_day: bool = True) -> dict:
    """信号日 day 时**可见**的基本面特征(A 组:纯财务比率,不需市值)。

    全部 as-of:先取该 code 截至 day 可见的最新一期,再取**同口径上年同期的可见版本**算同比。
    取不到时返回空 dict(调用方按缺失处理),绝不用今天的最终版兜底 —— 那是数值维度的 look-ahead。

    f_pit_lag_days = 信号日距该期公告日的天数(财报新鲜度)。它本身是**元特征**:
    刚出财报与财报已过期三个月,市场反应机制不同,不把它入模会把两种情形混为一谈。
    """
    cur = as_of_period_latest(records, day, code, visible_next_day=visible_next_day)
    if not cur:
        return {}
    out: dict[str, float] = {}
    if cur.get("roe_waa") is not None:
        out["f_roe"] = round(float(cur["roe_waa"]), 4)
    if cur.get("gross_margin") is not None:
        out["f_gross_margin"] = round(float(cur["gross_margin"]), 4)
    if cur.get("ocf_ps") is not None:
        out["f_ocf_ps"] = round(float(cur["ocf_ps"]), 4)
    eps, eps_d = cur.get("eps"), cur.get("eps_deduct")
    # 盈利质量:扣非/基本。仅 eps>0 才出 —— 负 eps 的比值无经济含义
    # (与负基数同比抑制口径一致),给 None 而不是算出个数。
    if eps is not None and float(eps) > 0 and eps_d is not None:
        out["f_deduct_ratio"] = round(float(eps_d) / float(eps), 4)
    prior = as_of_period(records, day, prior_year_period(cur["report_date"]),
                         code=code, visible_next_day=visible_next_day).get(code)
    if prior:
        rv = _ratio(cur.get("revenue"), prior.get("revenue"))
        np_ = _ratio(cur.get("net_profit"), prior.get("net_profit"))
        if rv is not None:
            out["f_rev_yoy"] = round(rv, 4)
        if np_ is not None:
            out["f_np_yoy"] = round(np_, 4)
    try:
        lag = (date.fromisoformat(str(day)[:10])
               - date.fromisoformat(cur["notice_date"])).days
        out["f_pit_lag_days"] = float(lag)
    except (ValueError, KeyError, TypeError):
        pass
    return out


def as_of_period_latest(records: list[dict], day: str, code: str,
                        visible_next_day: bool = True) -> dict | None:
    """单只:截至 day 可见的最新一期(as_of 的单 code 便捷版)。"""
    got = as_of(records, day, code=code, visible_next_day=visible_next_day)
    return got.get(code)


def build_pit_feature_fn(records: list[dict], visible_next_day: bool = True):
    """构造 `(code, as_of_day) -> dict` 回调,直接挂 launch_point_study 的 extra_feature_fn。

    预先按 (code, report_date) 建索引,避免每个信号日都全表扫描(12 窗数万信号 × 21 万条台账
    不做索引会跑到天荒地老)。
    """
    by_code: dict[str, list[dict]] = {}
    for r in records:
        c = r.get("code")
        if c:
            by_code.setdefault(c, []).append(r)
    for c in by_code:
        by_code[c].sort(key=lambda r: (r.get("report_date") or "", r.get("notice_date") or ""))

    def fn(code: str, as_of_day) -> dict:
        recs = by_code.get(str(code))
        if not recs:
            return {}
        return pit_features(recs, str(as_of_day)[:10], str(code),
                            visible_next_day=visible_next_day)

    return fn


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PIT 财务:按报告期拉取,以公告日为可见日")
    ap.add_argument("--periods", nargs="*", help="报告期,如 2024-03-31(可多个)")
    ap.add_argument("--since", type=int,
                    help="从该年起拉所有季度末(需留一年跑道:首个信号年为 2015 时应从 2014 起,"
                         "同比特征需要上一年度各期的当时可见版本)")
    ap.add_argument("--all-quarters", action="store_true", help="配合 --since 使用")
    ap.add_argument("--out", default=str(LEDGER), help="输出 JSONL 路径")
    ap.add_argument("--include-non-ashare", action="store_true",
                    help="保留新三板/B股/CDR(默认只留 A 股;不加过滤会把 5890 只新三板灌进宇宙)")
    ap.add_argument("--as-of", help="PIT 查询:该日可见的最新一期(不拉网络)")
    ap.add_argument("--code", help="配合 --as-of 限定单只")
    ap.add_argument("--visible-same-day", action="store_true",
                    help="--as-of 时把公告当日算作可见(默认次日,因公告多在盘后发布)")
    ap.add_argument("--verify", action="store_true",
                    help="台账完整性自检:报告期缺口 + 逐期行数异常(有问题 exit 1)")
    ap.add_argument("--verify-since", type=int,
                    help="--verify 的应有报告期起始年(缺省按台账最早期推断,且只从台账实际首期起对齐)")
    ap.add_argument("--verify-until",
                    help="--verify 的自检终点(缺省=今天:台账停更两季也会在尾部报出缺期)")
    args = ap.parse_args(argv)
    out_path = Path(args.out)

    if args.verify:
        if args.periods or args.since or args.as_of:
            print("[WARN] --verify 为纯自检模式,--periods/--since/--as-of 被忽略;"
                  "补拉请先不带 --verify 运行", file=sys.stderr)
        recs = load_ledger(out_path)
        if not recs:
            print(f"[ERR] 台账为空: {out_path}", file=sys.stderr)
            return 2
        rep = verify_ledger(recs, since_year=args.verify_since, until=args.verify_until)
        print("\n=== PIT 台账完整性自检 ===")
        print(rep["text"])
        if not rep["ok"]:
            missing = rep.get("missing") or []
            if missing:
                print(f"\n补拉命令:\n  uv run python src/datasource/local_tdx/{Path(__file__).name} "
                      f"--periods {' '.join(missing)}", file=sys.stderr)
            return 1
        return 0

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
        except FetchIncomplete as exc:
            # 残缺期整期丢弃：写一半样本进台账，as_of() 会把它当完整期用，
            # 静默污染回测；缺期至少能被 verify_ledger 报出来。
            print(f"[WARN] {p} 分页残缺，整期丢弃不落盘（请重拉该期）: {exc}", file=sys.stderr)
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {p} 拉取失败: {exc}", file=sys.stderr)
            continue
        recs, st = normalize(raw, p, a_share_only=not args.include_non_ashare)
        try:
            res = merge_write(recs, out_path)
        except Exception as exc:  # noqa: BLE001  # per-period 容错:一期失败不炸整个批量
            print(f"[WARN] {p} 写台账失败: {exc}", file=sys.stderr)
            continue
        total += res["added"]
        lags = sorted(r["lag_days"] for r in recs)
        med = lags[len(lags) // 2] if lags else None
        print(f"[OK] {p}: 原始 {st['raw']} → A股保留 {st['kept']} "
              f"(非A股剔 {st['dropped_type']} / 无公告日剔 {st['dropped_no_notice']} / "
              f"公告日不晚于报告期剔 {st['dropped_bad_lag']}"
              + (f" / 脏值置空 {st['bad_value']}" if st.get("bad_value") else "")
              + f"), 滞后中位 {med} 天, "
              f"新增 {res['added']} 条(台账 {res['after']})")
    print(f"\n[OK] 共新增 {total} 条 → {out_path}")
    print("提示:同比请用上年同期绝对值自行计算,勿用接口的 YSTZ/SJLTZ(次年同期发布时会被重算)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
