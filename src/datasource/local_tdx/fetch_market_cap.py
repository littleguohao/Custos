# -*- coding: utf-8 -*-
"""真市值/总股本:按交易日取东方财富估值分析表,压成**股本变动事件**落盘。

解决什么问题:research/R15(偏差警示)与 R3(判别力)记着"⚠️ 市值代理不是真市值:qlib bundle
无总股本,成交额只是流动性/规模的代理"。`RPT_VALUEANALYSIS_DET` 直接给 `TOTAL_SHARES`(总股本)
与 `TOTAL_MARKET_CAP`(总市值),一次调用一个交易日全市场。

实测(2026-07-31):
  - 自洽:总股本 × 收盘价 = 总市值,三只样本分毫不差
    (平安银行 194.06亿股 × 10.15 = 1970亿;浦发 293.52亿 × 8.23 = 2416亿;茅台 12.56亿 × 1467.39 = 18433亿);
  - **历史起点 2018-01-02**(二分探明:2017-12-29 及更早返回空;2018-01-02 有 3250 条)⇒ `MV_START`;
  - 构成干净:代码前缀只有 60/00/30/68/92,**无新三板**(不像 PIT 财务那个接口要额外过滤);
  - 反推路子不可用:`净利润/EPS` 对茅台精确(无优先股),但平安银行偏高 8.4%、浦发偏高 3.3%
    —— 银行有优先股,EPS 分子要扣优先股股息。故必须取真股本。

为什么存**事件**而不是日频市值:日频全市场 5300 行 × 2018~2026 约 2100 个交易日 ≈ 1100 万行。
而总股本只在增发/回购/送转/解禁时变,极稀疏 ⇒ 只在观测到变化时写一行,
市值由 `market_cap()` 用 `股本 × 当日收盘价` 现算(价格本来就有)。

PIT 性质:总股本是**当日事实**(某天就是那么多股),不存在财务数据那种"次年重算"的重述问题。
采样间隔带来的误差方向也是安全的 —— 变动只会被**延后**观测到(见 `shares_as_of` 说明),
是 stale 而非 look-ahead。

⚠️ 采样只允许**时间序前进**:早于已采样末日的日期会被拒绝并 WARN(乱序补采会拿台账
最终股本当 diff 基准、还会覆盖原事件的 prev_sample/prev_shares 元数据);要补更早的日期
只能清空台账与采样记录后按时间序重放。已知无数据的日期(非交易日)会记入采样记录的
`empty` 列表,重跑时直接跳过、不再重复请求。

⚠️ 本模块**不采纳**接口给的 `PE_TTM`/`PB_MRQ`/`PS_TTM`:它们依赖财报,而东财用的是当时已披露
口径还是最新重述后的,接口层面无从确认。既然已有 PIT 财务库(`fetch_pit_financials.py`),
估值一律用"自己的 PIT 财务 + 本模块的当日市值"自算,口径可控。

用法:
  # 按月采样建库(2018 起,约 100 次调用)
  uv run python src/datasource/local_tdx/fetch_market_cap.py --since 2018 --freq month
  # 指定交易日补采
  uv run python src/datasource/local_tdx/fetch_market_cap.py --dates 2024-06-28 2024-09-30
  # 查询某日股本/市值
  uv run python src/datasource/local_tdx/fetch_market_cap.py --as-of 2024-07-15 --code 600000
  # 自检
  uv run python src/datasource/local_tdx/fetch_market_cap.py --verify
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
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
LEDGER = OUT_DIR / "share_changes.jsonl"
SAMPLES = OUT_DIR / "share_change_samples.json"

# 市值/估值数据的历史起点(二分探明)。早于此日无数据,窗口护栏须据此剔除。
# 月采样首点已对齐到本日(见 sample_dates),否则 2018-01-02~27 会"护栏放行但 shares 全空"。
MV_START = "2018-01-02"


PAGE_SLEEP = 0.35        # 翻页间隔（秒）：东财 datacenter 无官方配额，连打会被静默限流


class FetchIncomplete(RuntimeError):
    """分页未拉完 / 响应残缺 —— 对本模块尤其危险，见 fetch_trade_date 说明。"""


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
    """响应自称成功但 result 为空 ⇒ 该日**确实**无数据（非交易日、早于 MV_START）。"""
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


def _f(v):
    return None if v is None else float(v)


def fetch_trade_date(trade_date: str, page_size: int = 500, max_pages: int = 40,
                     session=None, sleep: float = PAGE_SLEEP) -> list[dict]:
    """拉一个交易日的全市场总股本/总市值。非交易日返回空。

    **分页完整性由接口自报的 pages/count 校验，残缺一律抛 FetchIncomplete。**
    原实现是 `if not data: break`，把限流的空响应当「翻完了」。在本模块这比
    PIT 财务更严重：残缺行会直接进 `diff_events`，而 diff 是「只对变化的代码写事件」，
    **没返回的代码等于被判定为「股本未变」**——
      · 本次采样丢了某票 ⇒ 它这次的真实变动被吞掉；
      · 下次采样它再出现时，写出的 change 事件带的 prev_shares / prev_sample
        指向错误的基准与区间，`shares_as_of` 从此给出错的股本、市值全错；
      · `verify()` 只查 MV_START 越界与事件量级，查不出这种污染。
    宁可整日丢弃重跑（该日不会被记为已采样），也不能写一份错的事件。
    """
    s = session or requests.Session()
    rows: list[dict] = []
    pages = count = None
    for page in range(1, max_pages + 1):
        if page > 1 and sleep:
            time.sleep(sleep)
        params = {
            "sortColumns": "SECURITY_CODE", "sortTypes": "1",
            "pageSize": page_size, "pageNumber": page,
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": ("SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,CLOSE_PRICE,"
                        "TOTAL_SHARES,FREE_SHARES_A,TOTAL_MARKET_CAP,NOTLIMITED_MARKETCAP_A"),
            "filter": f"(TRADE_DATE='{trade_date}')",
        }
        r = s.get(API, params=params, headers=UA, timeout=30,
                  proxies={"http": None, "https": None})
        r.raise_for_status()
        payload = r.json() or {}
        result = payload.get("result")
        if not isinstance(result, dict):
            if page == 1 and _is_empty_ok(payload):
                return []                      # 非交易日
            raise FetchIncomplete(
                f"{trade_date} 第 {page} 页无 result 段（限流/异常响应），"
                f"已拿 {len(rows)} 行: {_brief(payload)}")
        data = result.get("data") or []
        pages, count = _as_int(result.get("pages")), _as_int(result.get("count"))
        if not data:
            if page == 1 and not count:
                return []                      # 非交易日
            raise FetchIncomplete(
                f"{trade_date} 第 {page} 页空响应，但接口声明 pages={pages} count={count}"
                f"（已拿 {len(rows)} 行）—— 疑似限流，不是翻完了")
        rows.extend(data)
        if pages is not None:
            if page >= pages:
                break
        elif count is not None and len(rows) >= count:
            break
        else:
            raise FetchIncomplete(
                f"{trade_date} 第 {page} 页未给 pages/count，无法判断是否翻完")
    else:
        raise FetchIncomplete(
            f"{trade_date} 翻到 max_pages={max_pages} 仍未结束（声明 pages={pages}）")
    if count is not None and len(rows) < count:
        raise FetchIncomplete(
            f"{trade_date} 只拿到 {len(rows)}/{count} 行，样本残缺不进 diff")
    return rows


def sample_dates(since_year: int, freq: str = "month", until: str | None = None) -> list[str]:
    """采样日期序列。freq=month 取每月 28 日、week 取每周一、day 逐日(慎用:调用量×20)。

    取 28 日而非月末是为了避开月末休市;非交易日接口返回空,由调用方跳过。
    起点被 MV_START 截断时,月采样首点对齐到 MV_START 本身(而非等到 1 月 28 日),
    否则 2018-01-02~27 会被窗口护栏放行却拿不到任何股本。
    """
    stop = until or cn_today().isoformat()
    start = max(f"{since_year}-01-01", MV_START)
    d0, d1 = date.fromisoformat(start), date.fromisoformat(stop)
    out: list[str] = []
    if freq == "month":
        y, m = d0.year, d0.month
        while True:
            cand = date(y, m, 28)
            if cand > d1:
                break
            if cand >= d0:
                out.append(cand.isoformat())
            m += 1
            if m > 12:
                y, m = y + 1, 1
        if start == MV_START and out and out[0] > MV_START:
            out.insert(0, MV_START)          # 首采样点对齐数据起点
    elif freq == "week":
        cur = d0 + timedelta(days=(7 - d0.weekday()) % 7)     # 下一个周一
        while cur <= d1:
            out.append(cur.isoformat())
            cur += timedelta(days=7)
    else:
        cur = d0
        while cur <= d1:
            if cur.weekday() < 5:
                out.append(cur.isoformat())
            cur += timedelta(days=1)
    return out


def diff_events(prev: dict[str, float], rows: list[dict], observed_on: str,
                prev_sample: str | None) -> list[dict]:
    """把一次采样与上次快照比对,只对**变化的**代码产出事件行。

    prev: {code: total_shares}(上次快照);返回事件列表。
    `observed_on` 是本次采样日,`prev_sample` 是上次采样日 —— 两者共同界定
    "真实变动发生在 (prev_sample, observed_on] 之间",消费方据此判断分辨率,不得过度声称精度。
    """
    out = []
    for x in rows:
        code = str(x.get("SECURITY_CODE") or "").strip()
        sh = _f(x.get("TOTAL_SHARES"))
        if not code or sh is None or sh <= 0:
            continue
        old = prev.get(code)
        if old is not None and abs(old - sh) < 1e-6:
            continue                                  # 未变化,不写
        out.append({
            "code": code,
            "name": str(x.get("SECURITY_NAME_ABBR") or ""),
            "observed_on": observed_on,
            "prev_sample": prev_sample,
            "total_shares": sh,
            "prev_shares": old,
            "free_shares": _f(x.get("FREE_SHARES_A")),
            "close": _f(x.get("CLOSE_PRICE")),
            "market_cap": _f(x.get("TOTAL_MARKET_CAP")),
            "kind": "first_seen" if old is None else "change",
        })
    return out


def build_from_tdx(codes: list[str], *, progress_every: int = 200,
                   refresh: bool = False) -> list[dict]:
    """**本地 TDX 路径**：从通达信 xdxr 权息数据提取股本变动全史 → 同一事件契约。

    owner 原则「尽量用本地 TDX 接口，HTTP 不稳定」（2026-08-04）。这条路比东财优越
    在于**契约天然匹配**：本模块的设计就是「总股本只在增发/回购/送转/解禁时变，
    极稀疏 ⇒ 只在观测到变化时写一行」，而 xdxr 的 `category=5`「股本变化」
    **本身就是事件流**；东财那边要逐交易日拉全市场快照、再 diff 压成事件，
    既要处理采样频率（月频采样会把变动日期界定成一个月的区间），又要应付限流。

    实测（2026-08-04）与真实值对照：万科 A 总股本 119.31 亿**分毫不差**、
    流通 97.17 亿（正确扣掉不流通的 B 股）；茅台 12.50 亿；浦发 333.06 亿。
    PIT 也对：万科 2020 年 113.02 亿 → 2023 年 116.31 亿 → 2026 年 119.31 亿。

    与东财路径的差异（如实记录，不要假装等价）：
      · `close` / `market_cap` 字段为 None —— xdxr 只给股本，市值需自己用
        当日收盘价乘（`total_shares_at()` + K 线）。东财那边直接给这两个值。
      · `name` 为空 —— 权息数据不含名称，需要时从名称表取。
      · `prev_sample` 为 None，`observed_on` 就是**精确的股本变动日**
        （比东财的月频采样区间更准）。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    # ⚠️ 必须与调用方走同一条导入路径，否则同一个文件会被加载成两个模块
    # （`adjust_factors` 与 `local_tdx.adjust_factors`），`AdjustError` 就成了两个
    # 不同的类，下面的 except 静默失效、异常直接穿透上抛。
    # 本模块既可能被当脚本跑（python fetch_market_cap.py）也可能被当包模块导入，
    # 故按包内优先、脚本模式回退的顺序尝试。
    try:
        from .adjust_factors import AdjustError, get_shares_events  # noqa: PLC0415
    except ImportError:                                             # 脚本模式
        from adjust_factors import AdjustError, get_shares_events   # noqa: PLC0415

    events: list[dict] = []
    failed = 0
    for i, code in enumerate(sorted({str(c)[:6] for c in codes}), 1):
        try:
            evs = get_shares_events(code, refresh=refresh)
        except AdjustError as e:
            failed += 1
            if failed <= 5:
                print(f"[WARN] {code} 股本取数失败: {e}", file=sys.stderr)
            continue
        prev = None
        for e in evs:
            ts = e.get("total_shares")
            if not ts or ts <= 0:
                continue
            if prev is not None and abs(prev - ts) < 1e-6:
                continue                                        # 未变化，不写
            events.append({
                "code": code,
                "name": "",                                     # xdxr 不含名称
                "observed_on": e["date"],                       # 精确变动日
                "prev_sample": None,
                "total_shares": float(ts),
                "prev_shares": prev,
                "free_shares": e.get("float_shares"),
                "close": None,                                  # xdxr 不含价格
                "market_cap": None,
                "kind": "first_seen" if prev is None else "change",
                "source": "tdx_xdxr",
            })
            prev = float(ts)
        if progress_every and i % progress_every == 0:
            print(f"[tdx] {i} 只 | 事件 {len(events)} | 失败 {failed}",
                  file=sys.stderr, flush=True)
    if failed:
        print(f"[WARN] {failed} 只股本取数失败（这些票没有股本事件）", file=sys.stderr)
    return events


EM_F10_EQUITY_API = "https://datacenter.eastmoney.com/securities/api/data/v1/get"


def fetch_equity_history(code: str, session=None, timeout: int = 15) -> list[dict]:
    """东财 F10 股本变动全史(IPO 起,**含增发/送转/债转股**)→ [{observed_on, total_shares, kind, name}]。
    这是补 2018 前股本缺口的权威来源(送转因子表缺定增,此表不缺)。失败返回 []。绝不 raise。"""
    try:
        s = session or requests.Session()
        rows: list[dict] = []
        page = 1
        while True:
            r = s.get(EM_F10_EQUITY_API, params={
                "reportName": "RPT_F10_EH_EQUITY", "columns": "ALL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": page, "pageSize": 100,
                "sortTypes": 1, "sortColumns": "END_DATE"},
                timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            d = r.json().get("result") or {}
            for x in d.get("data") or []:
                d10 = str(x.get("END_DATE") or "")[:10]
                ts = x.get("TOTAL_SHARES")
                if d10 and ts:
                    rows.append({"observed_on": d10, "total_shares": float(ts),
                                 "kind": str(x.get("CHANGE_REASON") or ""),
                                 "name": str(x.get("SECURITY_NAME_ABBR") or "")})
            if page >= int(d.get("pages") or 1):
                break
            page += 1
        return rows
    except Exception:  # noqa: BLE001
        return []


def backfill_equity_history(codes: list[str], before: str = MV_START,
                            out_path: str | Path = LEDGER, limit: int = 0,
                            progress: int = 200, session=None) -> dict:
    """把 before 之前的股本事件回填进台账(merge_write 原子去重)。
    返回 {added, failed, codes}。单只失败不中断。绝不 raise。"""
    session = session or requests.Session()
    events: list[dict] = []
    failed = 0
    n = 0
    for c in codes:
        n += 1
        if limit and n > limit:
            break
        try:
            rows = fetch_equity_history(c, session=session)
            for r in rows:
                if r["observed_on"] < before:
                    events.append({"code": c, "name": r["name"], "observed_on": r["observed_on"],
                                   "prev_sample": None, "total_shares": r["total_shares"],
                                   "prev_shares": None, "free_shares": None,
                                   "close": None, "market_cap": None,
                                   "kind": r["kind"] or "backfill_f10"})
        except Exception:  # noqa: BLE001
            failed += 1
        if progress and n % progress == 0:
            print(f"[backfill] {n} 只 | 事件 {len(events)} | 失败 {failed}", file=sys.stderr, flush=True)
    res = merge_write(events, out_path) if events else {"added": 0}
    return {"added": res["added"], "failed": failed, "codes": min(n, limit or n)}


def merge_write(events: list[dict], path: str | Path = LEDGER) -> dict:
    """按 (code, observed_on) 去重合并写 JSONL(原子写)。"""
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
            existing[(r.get("code"), r.get("observed_on"))] = r
    before = len(existing)
    for e in events:
        existing[(e["code"], e["observed_on"])] = e
    rows = sorted(existing.values(), key=lambda r: (r["observed_on"], r["code"]))
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    return {"before": before, "after": len(rows), "added": len(rows) - before}


def load_events(path: str | Path = LEDGER) -> list[dict]:
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


def shares_as_of(events: list[dict], day: str, code: str | None = None) -> dict[str, dict]:
    """截至 day 的总股本,按 code 返回最后一个 `observed_on <= day` 的事件。

    ⚠️ 分辨率:采样间隔内发生的变动只会被**延后**观测到,故此处返回的可能是变动前的旧股本
    —— 方向是 **stale 而非 look-ahead**(不会拿到当时还不存在的股本),对回测是安全的一侧。
    事件行里的 `prev_sample`/`observed_on` 界定了真实变动的时间区间。
    """
    best: dict[str, dict] = {}
    for e in events:
        if code and e.get("code") != code:
            continue
        obs = e.get("observed_on") or ""
        if not obs or obs > day:
            continue
        cur = best.get(e["code"])
        if cur is None or obs > (cur.get("observed_on") or ""):
            best[e["code"]] = e
    return best


def market_cap(events: list[dict], day: str, closes: dict[str, float],
               code: str | None = None) -> dict[str, dict]:
    """市值 = 截至 day 的总股本 × 当日收盘价。closes: {code: close}(由价格链提供)。

    不直接用事件里的 `market_cap` 字段:那是**采样日**的市值,不是查询日的。
    股本才是应该跨日沿用的量,价格必须用查询日的。
    """
    sh = shares_as_of(events, day, code=code)
    out = {}
    for c, e in sh.items():
        px = closes.get(c)
        if px is None or e.get("total_shares") is None:
            continue
        out[c] = {"code": c, "shares": e["total_shares"], "close": float(px),
                  "market_cap": e["total_shares"] * float(px),
                  "shares_observed_on": e.get("observed_on"),
                  "shares_prev_sample": e.get("prev_sample")}
    return out


def before_mv_start(day: str) -> bool:
    """该日是否早于市值数据起点(2018-01-02)。窗口护栏用。"""
    return str(day)[:10] < MV_START


def verify(events: list[dict], samples: list[str]) -> dict:
    """自检:采样覆盖、事件量级、是否有早于 MV_START 的可疑事件。"""
    codes = {e.get("code") for e in events if e.get("code")}
    firsts = [e for e in events if e.get("kind") == "first_seen"]
    changes = [e for e in events if e.get("kind") == "change"]
    early = [e for e in events if before_mv_start(e.get("observed_on") or "9999")]
    obs = sorted({e.get("observed_on") for e in events if e.get("observed_on")})
    out = {
        "ok": not early,
        "n_events": len(events), "n_codes": len(codes),
        "n_first_seen": len(firsts), "n_changes": len(changes),
        "n_samples_recorded": len(samples),
        "first_obs": obs[0] if obs else None, "last_obs": obs[-1] if obs else None,
        "n_before_mv_start": len(early),
    }
    lines = [f"股本事件 {len(events)} 条 / 覆盖 {len(codes)} 只;"
             f"首见 {len(firsts)} + 变动 {len(changes)};"
             f"观测区间 {out['first_obs']} ~ {out['last_obs']};已采样 {len(samples)} 个日期"]
    if early:
        lines.append(f"  ⚠️ {len(early)} 条事件早于 MV_START({MV_START}) —— 数据源在该日前无数据,可疑")
    else:
        lines.append(f"  ✅ 无早于 MV_START({MV_START}) 的事件")
    if changes:
        lines.append(f"  股本变动率:{len(changes)}/{len(codes)} = 每只平均 "
                     f"{len(changes) / max(len(codes), 1):.2f} 次变动")
    lines.append("  注:采样间隔内的变动只会被延后观测到(stale 而非 look-ahead);"
                 "精度由事件行的 prev_sample~observed_on 界定")
    out["text"] = "\n".join(lines)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="真市值/总股本:按交易日取数,压成股本变动事件")
    ap.add_argument("--dates", nargs="*", help="交易日,如 2024-06-28(可多个)")
    ap.add_argument("--since", type=int, help="从该年起按 --freq 采样")
    ap.add_argument("--freq", choices=["month", "week", "day"], default="month",
                    help="采样频率(默认 month:约 100 次调用覆盖 2018~今)")
    ap.add_argument("--out", default=str(LEDGER))
    ap.add_argument("--samples-out", default=str(SAMPLES))
    ap.add_argument("--as-of", help="查询该日的总股本(不拉网络)")
    ap.add_argument("--code", help="配合 --as-of 限定单只")
    ap.add_argument("--verify", action="store_true", help="自检(有问题 exit 1)")
    ap.add_argument("--backfill-history", action="store_true",
                    help=f"回填 {MV_START} 之前的股本变动史(东财 F10 全史含增发;补 2015-2017 市值缺口)")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只(排障用)")
    ap.add_argument("--from-tdx", action="store_true",
                    help="**本地 TDX 路径**(推荐):从通达信 xdxr 提取股本变动全史。"
                         "契约天然匹配(xdxr 的 category=5 本身就是事件流),"
                         "且 observed_on 是精确变动日而非月频采样区间。"
                         "代价:不含 close/market_cap/name(需自己乘收盘价)")
    ap.add_argument("--codes", default="", help="配合 --from-tdx:逗号分隔;留空=本地全市场")
    args = ap.parse_args(argv)

    if args.from_tdx:
        if args.codes:
            codes = [c.strip()[:6] for c in args.codes.split(",") if c.strip()]
        else:
            try:
                import local_tdx_data
                codes = sorted(local_tdx_data.list_local_vipdoc_codes())
            except Exception as e:                             # noqa: BLE001
                print(f"[ERR] 读不到本地代码表: {e}", file=sys.stderr)
                return 2
        try:
            from code_utils import is_index
            codes = [c for c in codes if not is_index(c)]       # 指数没有股本
        except Exception:                                      # noqa: BLE001, S110
            pass
        if args.limit:
            codes = codes[:args.limit]
        evs = build_from_tdx(codes)
        res = merge_write(evs, args.out) if evs else {"added": 0}
        print(json.dumps({"source": "tdx_xdxr", "codes": len(codes),
                          "events": len(evs), **res}, ensure_ascii=False))
        return 0

    out_path, sp_path = Path(args.out), Path(args.samples_out)

    if args.backfill_history:
        import local_tdx_data  # noqa: PLC0415
        codes = local_tdx_data.list_local_vipdoc_codes()
        print(f"[INFO] 回填 {MV_START} 前股本史: universe {len(codes)} 只", file=sys.stderr)
        res = backfill_equity_history(codes, before=MV_START, out_path=out_path,
                                      limit=args.limit)
        print(f"[OK] 回填完成: +{res['added']} 事件(失败 {res['failed']} 只) → {out_path}")
        return 0 if res["added"] or res["failed"] == 0 else 2

    def _samples() -> dict:
        """采样台账:{"sampled": 有数据的日期, "empty": 已知无数据(非交易日)的日期}。
        两类都跳过重复请求 —— 空日期不重打,避免每次重跑空转。"""
        if sp_path.exists():
            try:
                data = json.loads(sp_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {"sampled": data.get("sampled") or [],
                            "empty": data.get("empty") or []}
            except ValueError:
                pass
        return {"sampled": [], "empty": []}

    if args.verify:
        events = load_events(out_path)
        if not events:
            print(f"[ERR] 股本事件台账为空: {out_path}", file=sys.stderr)
            return 2
        rep = verify(events, _samples()["sampled"])
        print("\n=== 股本/市值台账自检 ===")
        print(rep["text"])
        return 0 if rep["ok"] else 1

    if args.as_of:
        events = load_events(out_path)
        if not events:
            print(f"[ERR] 股本事件台账为空: {out_path}", file=sys.stderr)
            return 2
        if before_mv_start(args.as_of):
            print(f"[ERR] {args.as_of} 早于市值数据起点 {MV_START},无数据", file=sys.stderr)
            return 2
        got = shares_as_of(events, args.as_of, code=args.code)
        print(f"截至 {args.as_of}:{len(got)} 只有股本记录")
        for c, e in sorted(got.items())[:20]:
            print(f"  {c} {e.get('name', ''):<8} 总股本={e['total_shares'] / 1e8:.2f}亿股 "
                  f"(观测于 {e['observed_on']}, 上次采样 {e.get('prev_sample')})")
        if len(got) > 20:
            print(f"  ...(共 {len(got)} 只)")
        return 0

    dates = list(args.dates or [])
    if args.since:
        dates += sample_dates(args.since, freq=args.freq)
    dates = sorted(set(d for d in dates if not before_mv_start(d)))
    if not dates:
        ap.error(f"需提供 --dates 或 --since(且不早于 {MV_START}),或用 --as-of / --verify")

    # 以台账里已有的最后状态为起点,避免重复写事件
    events = load_events(out_path)
    prev: dict[str, float] = {}
    for e in sorted(events, key=lambda r: r.get("observed_on") or ""):
        if e.get("total_shares") is not None:
            prev[e["code"]] = float(e["total_shares"])
    sp = _samples()
    sampled = sp["sampled"]
    known_empty = set(sp["empty"])
    last_sample = sampled[-1] if sampled else None

    session = requests.Session()
    total = 0
    for d in dates:
        if d in sampled or d in known_empty:
            continue
        if last_sample is not None and d < last_sample:
            # 乱序补采会拿台账**最终股本**当 prev:diff 基准错误、且会覆盖原事件的
            # prev_sample/prev_shares 元数据。拒绝比写错好 —— 要补只能清空台账按时间序重放。
            print(f"[WARN] {d} 早于已采样末日 {last_sample},拒绝乱序补采"
                  f"(prev 基准会是台账最终股本,diff 与元数据都会错);"
                  f"如需补采请清空台账与采样记录后按时间序重放", file=sys.stderr)
            continue
        try:
            rows = fetch_trade_date(d, session=session)
        except FetchIncomplete as exc:
            # 整日丢弃：既不写事件、也不记入 sampled/empty，下次重跑会重新采这一天。
            # 残缺行进 diff 会把「没返回」当「股本未变」，污染台账且 verify 查不出来。
            print(f"[WARN] {d} 分页残缺，整日丢弃不落盘（下次重跑会重采）: {exc}",
                  file=sys.stderr)
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {d} 拉取失败: {exc}", file=sys.stderr)
            continue
        if not rows:
            known_empty.add(d)
            print(f"[--] {d}: 无数据(非交易日或数据源缺失),记入已知空日期并跳过")
            continue
        evs = diff_events(prev, rows, d, last_sample)
        res = merge_write(evs, out_path)
        total += res["added"]
        for e in evs:
            prev[e["code"]] = e["total_shares"]
        sampled.append(d)
        last_sample = d
        n_new = sum(1 for e in evs if e["kind"] == "first_seen")
        print(f"[OK] {d}: 全市场 {len(rows)} 只 → 事件 {len(evs)} 条"
              f"(首见 {n_new} / 变动 {len(evs) - n_new}), 台账 {res['after']} 条")

    sp_path.parent.mkdir(parents=True, exist_ok=True)
    sp_path.write_text(json.dumps({"sampled": sorted(set(sampled)),
                                   "empty": sorted(known_empty), "freq": args.freq,
                                   "mv_start": MV_START}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(f"\n[OK] 共新增 {total} 条事件 → {out_path}")
    print(f"     采样记录 {len(set(sampled))} 个日期(+{len(known_empty)} 个已知空日期) → {sp_path}")
    print("提示:市值请用 market_cap()(股本 × 查询日收盘价),勿直接用事件里采样日的 market_cap 字段")
    return 0


if __name__ == "__main__":
    sys.exit(main())
