# -*- coding: utf-8 -*-
"""基本面因子（fundamentals）—— CZ §四 抄底三条件代理 + 公司品质档。

2026-08-20（v0.84，因子×止盈×止损架构 Phase D）建立：纯评估逻辑从
`pipeline/screening/financials.py`（`financial_factor` 及其取数 helper）与
`score_candidates.py`（`fundamental_quality` 品质档/三无判定）迁入本模块——
因子实现全项目唯一一份（同 v0.50 INLINE_EXTRACTED 原则）。
⚠️ 分层约束：factors/ 是 L2，不得依赖 L3 的 screening（tests/test_architecture_layers.py
强制），所以是「实现迁上来、原模块 re-export」，不是反向包装。
**行为零变化**：`financials.financial_factor` / `score_candidates.fundamental_quality`
仍是同一函数对象（re-export），返回值逐字段未动。

live 消费方式（**evidence_only，行为不变**）：
- enrich → `cand["financials"]`（🐂 展示列）；
- score_candidates 四面共振的基本面腿 + `fundamental_quality` 品质档落盘；
- **不进技术分、不驱动分层/gate/排序**。

status 定档：untested（证据层因子无独立回测证据；先例 rsi_state 同为
untested + evidence_only + release）。evidence_only 不受 NOT_FOR_LIVE 限制
（它本来就不驱动决策）。

⚠️ live 用（当前快照，无未来函数）干净；**历史回测不可用**（Affair=最新快照
→ look-ahead），见 financial_factor docstring。

数据加载/列映射（`load_financials`/`auto_colmap`/CLI）留在
`pipeline/screening/financials.py`（L3，依赖 datasource）。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

FACTOR: dict[str, Any] = {
    "id": "fundamentals",
    "name": "基本面（CZ 抄底三条件代理 + 品质档/三无标记）",
    "kind": "state",
    "status": "untested",  # 证据层因子，无独立回测证据（先例 rsi_state 同档）
    "evidence": "",
    "note": "只进 🐂 展示与四面共振基本面腿，不进分不驱动分层；Affair 快照口径，历史回测不可用",
    "min_bars": 1,
    "live_use": "evidence_only",
    "stage": "release",  # enrich（financials 列）与 score_candidates（fundamental_quality）引用
}

REQUIRED = ("code", "net_profit", "op_cashflow")  # 缺任一 → available=False
DIXI_NET_PROFIT_YOY = 100.0  # 待回测：业绩预增代理阈值（净利同比%）

# 财报时效上限（日）：报告期距 as-of 超过它就不再算"有效财报"。
#
# 为什么必须有上限：Affair 快照只给"最新一期"，长期停牌/失去持续披露能力的壳公司会一直
# 挂着三年前的报表，代理条件(净利>0 / 现金流>0 / ROE>0)照样成立 → 一只早已空壳的票被判
# "品质优"并进入 ⭐ 四面共振（审计 E11）。系统不会报错，只会安静地把空壳标成优质标的。
#
# 270 是怎么定的：A 股法定披露截止为 年报次年 4-30 / 一季报 4-30 / 半年报 8-31 /
# 三季报 10-31。据此推演正常公司在任意日期回看，"最新已披露报告期"距当日的天数上限是
# **211 天**——出现在 4 月 29 日（年报与一季报都还没到截止，最新可得仍是上年三季报
# 2025-09-30 → 2026-04-29 = 211 天）。270 = 211 + 59 天余量，覆盖法定截止日遇周末顺延
# 与短期延期，同时把"已停止披露却仍被判优"的灰色窗口从 400 天口径的 189 天压到 59 天。
# 真正延期超过 59 天（即 6 月底仍未出年报）的公司基本已被 ST/退市风险警示，本就不该进候选。
REPORT_MAX_AGE_DAYS = 270


def _cell(row, colmap: dict, logical: str) -> Optional[float]:
    col = colmap.get(logical)
    if col is None or row is None:
        return None
    try:
        v = row.get(col)
    except Exception:  # noqa: BLE001
        return None
    try:
        # TDX Affair 存在重复列名(如『经营活动产生的现金流量净额』×2):
        # .get 返回 Series 而非标量,float(Series) 会抛 → 此前全场现金流 None、tier优永不成立
        if hasattr(v, "iloc") and not isinstance(v, (int, float, str, bool)):
            v = next((x for x in v if x is not None and x == x), None)
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _cell_text(row, colmap: dict, logical: str) -> str:
    """取字符串型单元格（如报告期）。重复列名同样取首个非空值。绝不 raise。"""
    col = colmap.get(logical)
    if col is None or row is None:
        return ""
    try:
        v = row.get(col)
    except Exception:  # noqa: BLE001
        return ""
    if hasattr(v, "iloc") and not isinstance(v, (int, float, str, bool)):
        try:
            v = next((x for x in v if x is not None and x == x), None)
        except Exception:  # noqa: BLE001
            return ""
    if v is None or v != v:  # None / NaN
        return ""
    return str(v).strip()


def _parse_day(s) -> Optional[_dt.date]:
    """宽松解析 YYYY-MM-DD / YYYYMMDD / 带时分秒 的日期；解析不出返回 None。"""
    t = str(s or "").strip().replace("/", "-")[:19]
    if not t:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _dt.datetime.strptime(
                t[: len(_dt.datetime.now().strftime(fmt))], fmt
            ).date()
        except ValueError:
            continue
    try:
        return _dt.date.fromisoformat(t[:10])
    except ValueError:
        return None


def report_age_days(report_date, as_of=None) -> Optional[int]:
    """报告期距 as-of 的天数；任一侧无法解析 → None（"无法判定"，不得当作"新鲜"）。"""
    d = _parse_day(report_date)
    if d is None:
        return None
    ref = _parse_day(as_of) or _dt.date.today()
    return (ref - d).days


def _locate_row(fin_df, colmap: dict, code6: str):
    """按 6 位代码定位财务行；返回 (row, None)，失败返回 (None, 失败结果dict)。绝不 raise。"""
    code_col = colmap.get("code")
    try:
        if code_col == "__index__":
            idx = fin_df.index.astype(str).str.split(".").str[0].str.zfill(6)
            sub = fin_df[idx.values == code6]
        elif code_col in getattr(fin_df, "columns", []):
            sub = fin_df[
                fin_df[code_col].astype(str).str.split(".").str[0].str.zfill(6) == code6
            ]
        else:
            return None, {"available": False, "reason": "code_col_missing"}
        if sub.empty:
            return None, {"available": False, "reason": "code_not_found"}
        return sub.iloc[0], None
    except Exception:  # noqa: BLE001
        return None, {"available": False, "reason": "lookup_failed"}


def _stale_status(
    rpt_date: str, age: Optional[int], max_age_days: int
) -> tuple[Optional[bool], str]:
    """时效判定：返回 (stale, stale_check)。`max_age_days=0` 关闭该检查。"""
    if not max_age_days:
        return None, "disabled"
    if not rpt_date:
        return None, "no_report_date"
    if age is None:
        return None, "unparsable_report_date"
    stale = age > max_age_days
    return stale, ("stale" if stale else "ok")


def _dixi_metrics(
    row, colmap: dict, net_profit: Optional[float], price: Optional[float]
) -> dict[str, Any]:
    """提取其余财务指标并计算 CZ ①② 代理；返回指标 dict。"""
    op_cf = _cell(row, colmap, "op_cashflow")
    revenue = _cell(row, colmap, "revenue")
    np_yoy = _cell(row, colmap, "net_profit_yoy")
    rev_yoy = _cell(row, colmap, "revenue_yoy")
    roe = _cell(row, colmap, "roe")
    shares = _cell(row, colmap, "total_shares")
    # 总股本(股) × 现价(元) = 总市值(元)。Affair 总股本口径为"股"（已由 000008/000028 等实测量级校验：
    # 神州高铁 ~28亿股 × ~2.3元 ≈ 64亿元，与落盘一致）。若换用"万股"列需 ×1e4 修正。
    mkt_cap = (shares * price) if (shares is not None and price) else None
    mkt_cap_yi = round(mkt_cap / 1e8, 2) if mkt_cap is not None else None

    perf_surge = bool(
        np_yoy is not None and np_yoy >= DIXI_NET_PROFIT_YOY
    )  # ① 扣非同比≥100% 代理
    np_pos = bool(net_profit is not None and net_profit > 0)  # ②a 净利为正
    ocf_available = op_cf is not None
    ocf_pos = bool(
        op_cf is not None and op_cf > 0
    )  # 与 ocf_available and ... 同值，显式判空便于收窄  # ②b 经营现金流为正(缺失→未确认)
    roe_positive = bool(roe is not None and roe > 0)
    # ②综合(CZ 真实盈利+现金流)：净利与现金流同为正才成立；现金流缺失(季报常见)时不冒充成立，
    # 但 net_profit_positive 仍独立可用 —— 优雅降级而非整项作废。
    real_support = bool(np_pos and ocf_pos)
    proxy = {
        "perf_surge_ge_100": perf_surge,
        "net_profit_positive": np_pos,
        "op_cashflow_positive": (ocf_pos if ocf_available else None),
        "real_earnings_cashflow": real_support,
        "roe_positive": roe_positive,
    }
    return {
        "cashflow_available": ocf_available,
        "op_cashflow": op_cf,
        "revenue": revenue,
        "net_profit_yoy": np_yoy,
        "revenue_yoy": rev_yoy,
        "roe": roe,
        "market_cap": mkt_cap,
        "market_cap_yi": mkt_cap_yi,
        "dixi_proxy": proxy,
        "hits": [k for k, v in proxy.items() if v is True],
    }


def financial_factor(
    code: str,
    fin_df,
    colmap: dict,
    price: Optional[float] = None,
    as_of=None,
    max_age_days: int = REPORT_MAX_AGE_DAYS,
) -> dict[str, Any]:
    """CZ 抄底三条件代理（①②）。colmap 不全、数据缺失或定位不到 → available=False。绝不 raise。

    时效上限（审计 E11）：报告期距 `as_of`（缺省=今天）超过 `max_age_days` → available=False
    且 reason="report_stale" —— 陈旧财报不得无限期视为有效。`max_age_days=0` 关闭该检查。
    colmap 里没有 report_date 时**不假定新鲜**：report_stale=None + stale_check="no_report_date"，
    由调用方决定是否采信（下游 fundamental_quality 对 available=False 归"未知"，非"差"）。
    """
    if not colmap or fin_df is None or getattr(fin_df, "empty", True):
        return {"available": False, "reason": "no_financials_or_colmap"}
    if any(colmap.get(f) is None for f in REQUIRED):
        return {"available": False, "reason": "required_cols_unmapped"}
    code6 = str(code).split(".")[0].zfill(6)
    row, fail = _locate_row(fin_df, colmap, code6)
    if fail is not None:
        return fail

    net_profit = _cell(row, colmap, "net_profit")
    # 时效先判:陈旧财报直接不可用,免得下面的代理条件在三年前的数据上"成立"
    rpt_date = _cell_text(row, colmap, "report_date")
    age = report_age_days(rpt_date, as_of) if rpt_date else None
    stale, stale_check = _stale_status(rpt_date, age, max_age_days)
    if stale:
        return {
            "available": False,
            "reason": "report_stale",
            "report_date": rpt_date,
            "report_age_days": age,
            "report_stale": True,
            "stale_check": stale_check,
            "max_age_days": max_age_days,
        }
    m = _dixi_metrics(row, colmap, net_profit, price)
    return {
        "available": True,
        "cashflow_available": m["cashflow_available"],
        "report_date": rpt_date or None,
        "report_age_days": age,
        "report_stale": stale,
        "stale_check": stale_check,
        "max_age_days": max_age_days,
        "net_profit": net_profit,
        "op_cashflow": m["op_cashflow"],
        "revenue": m["revenue"],
        "net_profit_yoy": m["net_profit_yoy"],
        "revenue_yoy": m["revenue_yoy"],
        "roe": m["roe"],
        "market_cap": m["market_cap"],
        "market_cap_yi": m["market_cap_yi"],
        "dixi_proxy": m["dixi_proxy"],
        "hits": m["hits"],
    }


def fundamental_quality(fin: Optional[dict]) -> dict:
    """基于 financials(CZ抄底代理)判公司品质档 + 三无标记(cz:无主业/无业绩/无现金流→回避)。
    优=真业绩+现金流+ROE;中=净利为正(现金流缺/未确认);差=净利非正。三无需净利非正**且现金流确认为负**(保守)。
    ⚠️ live 用(当前快照,无未来函数)干净;**历史回测不可用**(Affair=最新快照→look-ahead)。

    2026-08-20（v0.84）从 score_candidates 迁入本因子模块（零行为变化）。"""
    f = fin or {}
    if not f.get("available"):
        return {"tier": "未知", "sanwu": False, "available": False}
    dp = f.get("dixi_proxy") or {}
    np_pos = bool(dp.get("net_profit_positive"))
    ocf_pos = dp.get("op_cashflow_positive")  # True/False/None(未确认)
    roe_pos = bool(dp.get("roe_positive"))
    real = bool(dp.get("real_earnings_cashflow"))  # 净利+现金流双正
    tier = "优" if (real and roe_pos) else ("中" if np_pos else "差")
    sanwu = bool(
        (not np_pos) and (ocf_pos is False)
    )  # 净利非正 且 现金流确认为负 → 三无
    return {
        "tier": tier,
        "sanwu": sanwu,
        "available": True,
        "net_profit_positive": np_pos,
        "cashflow_positive": ocf_pos,
        "roe_positive": roe_pos,
    }
