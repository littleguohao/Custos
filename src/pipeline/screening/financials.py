# -*- coding: utf-8 -*-
"""财务维度（CZ §四 抄底三条件代理）脚手架 —— 只读、best-effort、绝不 raise、不驱动分层。

CZ 抄底三条件：① 业绩预增 ≥100%；② 收入/利润/现金流真实支撑；③ 高壁垒赛道。
①② 可用财务数据代理（净利同比、经营现金流为正等）；③ 为定性赛道判断，不在此处理。

数据源为 mootdx Affair（local_tdx_data.get_financial_data，约 585 列）。**列含义随 TDX 版本
而变，本模块不硬编码列号**：由 registry `financials.columns` 把逻辑字段映射到实际列名/序号，
映射不全或数据缺失 → available=False（默认整段关闭，不影响现有流程）。校准前不视为定型。

逻辑字段：code(必填,用于定位)、net_profit、op_cashflow(②必需)、revenue、net_profit_yoy、
revenue_yoy、roe、total_shares(× 价格 → 市值)。
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Optional

_TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(_TOOLS.parent / "core"), str(_TOOLS), str(_TOOLS.parent / "datasource" / "local_tdx")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REQUIRED = ("code", "net_profit", "op_cashflow")   # 缺任一 → available=False
DIXI_NET_PROFIT_YOY = 100.0                          # 待回测：业绩预增代理阈值（净利同比%）

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

_fin_cache: dict[str, Any] = {}


def auto_colmap(columns) -> dict[str, str]:
    """按中文列名关键词自动识别逻辑字段 → 列名（Affair 命名列）。找不到 code 列则用 '__index__'。

    仅作首选/建议，务必用 --inspect 确认；不准时在 registry.financials.columns 显式覆盖。
    """
    cols = [str(c) for c in (list(columns) if columns is not None else [])]

    def find(groups, excludes=()):
        # groups: 优先级列表，每组是"全部子串都要在列名里"的元组；返回首个命中列
        for group in groups:
            for c in cols:
                if all(s in c for s in group) and not any(x in c for x in excludes):
                    return c
        return None

    m: dict[str, Optional[str]] = {
        "code": find([("证券代码",), ("股票代码",), ("代码",), ("code",), ("symbol",)]),
        "report_date": find([("report_date",), ("报告期",), ("报表日期",)]),
        "net_profit": find([("归属于母公司", "净利润"), ("归母净利润",), ("净利润",)],
                            excludes=("同比", "增长", "率", "比率", "每股", "现金")),
        "net_profit_yoy": find([("净利润", "同比"), ("归母净利润", "同比"), ("净利润", "增长率"), ("净利润", "增长")]),
        "revenue": find([("营业总收入",), ("营业收入",)],
                        excludes=("同比", "增长", "成本", "率", "每股", "EBITDA", "%", "/", "比率", "占比")),
        "revenue_yoy": find([("营业总收入", "同比"), ("营业收入", "同比"), ("营业收入", "增长")]),
        "op_cashflow": find([("经营活动", "现金流量净额"), ("经营活动产生的现金流量净额",), ("经营", "现金流")],
                            excludes=("每股",)),
        "roe": find([("净资产收益率", "加权"), ("净资产收益率",), ("ROE",)], excludes=("同比", "增长")),
        "total_shares": find([("总股本",)], excludes=("流通",)),
    }
    if m["code"] is None:
        m["code"] = "__index__"   # 无代码列 → 假定行索引即代码，financial_factor 用 index 定位
    return {k: v for k, v in m.items() if v}


def load_financials(report_period: str = ""):
    """加载 TDX 财务（Affair）；失败/无数据返回 None。best-effort、绝不 raise、带缓存。"""
    key = report_period or "latest"
    if key in _fin_cache:
        return _fin_cache[key]
    df = None
    try:
        import local_tdx_data  # noqa: PLC0415
        df = local_tdx_data.get_financial_data(report_period)
    except Exception:  # noqa: BLE001
        df = None
    _fin_cache[key] = df
    return df


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
    if v is None or v != v:            # None / NaN
        return ""
    return str(v).strip()


def _parse_day(s) -> Optional[_dt.date]:
    """宽松解析 YYYY-MM-DD / YYYYMMDD / 带时分秒 的日期；解析不出返回 None。"""
    t = str(s or "").strip().replace("/", "-")[:19]
    if not t:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _dt.datetime.strptime(t[:len(_dt.datetime.now().strftime(fmt))], fmt).date()
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


def financial_factor(code: str, fin_df, colmap: dict, price: Optional[float] = None,
                     as_of=None, max_age_days: int = REPORT_MAX_AGE_DAYS) -> dict[str, Any]:
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
    code_col = colmap.get("code")
    code6 = str(code).split(".")[0].zfill(6)
    try:
        if code_col == "__index__":
            idx = fin_df.index.astype(str).str.split(".").str[0].str.zfill(6)
            sub = fin_df[idx.values == code6]
        elif code_col in getattr(fin_df, "columns", []):
            sub = fin_df[fin_df[code_col].astype(str).str.split(".").str[0].str.zfill(6) == code6]
        else:
            return {"available": False, "reason": "code_col_missing"}
        if sub.empty:
            return {"available": False, "reason": "code_not_found"}
        row = sub.iloc[0]
    except Exception:  # noqa: BLE001
        return {"available": False, "reason": "lookup_failed"}

    net_profit = _cell(row, colmap, "net_profit")
    # 时效先判:陈旧财报直接不可用,免得下面的代理条件在三年前的数据上"成立"
    rpt_date = _cell_text(row, colmap, "report_date")
    age = report_age_days(rpt_date, as_of) if rpt_date else None
    if not max_age_days:
        stale, stale_check = None, "disabled"
    elif not rpt_date:
        stale, stale_check = None, "no_report_date"
    elif age is None:
        stale, stale_check = None, "unparsable_report_date"
    else:
        stale = age > max_age_days
        stale_check = "stale" if stale else "ok"
    if stale:
        return {"available": False, "reason": "report_stale",
                "report_date": rpt_date, "report_age_days": age,
                "report_stale": True, "stale_check": stale_check,
                "max_age_days": max_age_days}
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

    perf_surge = bool(np_yoy is not None and np_yoy >= DIXI_NET_PROFIT_YOY)   # ① 扣非同比≥100% 代理
    np_pos = bool(net_profit is not None and net_profit > 0)                   # ②a 净利为正
    ocf_available = op_cf is not None
    ocf_pos = bool(ocf_available and op_cf > 0)                                # ②b 经营现金流为正(缺失→未确认)
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
        "available": True, "cashflow_available": ocf_available,
        "report_date": rpt_date or None, "report_age_days": age,
        "report_stale": stale, "stale_check": stale_check, "max_age_days": max_age_days,
        "net_profit": net_profit, "op_cashflow": op_cf, "revenue": revenue,
        "net_profit_yoy": np_yoy, "revenue_yoy": rev_yoy, "roe": roe,
        "market_cap": mkt_cap, "market_cap_yi": mkt_cap_yi,
        "dixi_proxy": proxy,
        "hits": [k for k, v in proxy.items() if v is True],
    }


def main(argv=None) -> int:
    """--inspect：加载 Affair 财务、打印自动列映射 + 抽样一只，供人工确认后写入 registry。"""
    import argparse
    import json
    ap = argparse.ArgumentParser(description="财务维度脚手架：--inspect 打印自动列映射供确认")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--code", default="600000", help="抽样验证的股票代码")
    ap.add_argument("--report-period", default="")
    args = ap.parse_args(argv)
    df = load_financials(args.report_period)
    if df is None or getattr(df, "empty", True):
        print(json.dumps({"available": False, "reason": "no_financials"}, ensure_ascii=False))
        return 0
    cm = auto_colmap(getattr(df, "columns", []))
    override = {}
    try:
        from paths import SCREEN_FORMULA_REGISTRY_FILE  # noqa: PLC0415
        reg = json.loads(SCREEN_FORMULA_REGISTRY_FILE.read_text(encoding="utf-8"))
        override = (reg.get("financials") or {}).get("columns") or {}
    except Exception:  # noqa: BLE001
        override = {}
    final = dict(cm)
    final.update(override)
    print("[自动识别 auto_colmap]:")
    print(json.dumps(cm, ensure_ascii=False, indent=2))
    if override:
        print("[registry.financials.columns 覆盖]:", json.dumps(override, ensure_ascii=False))
    print("[最终映射(enrich 实际使用)]:")
    print(json.dumps(final, ensure_ascii=False, indent=2))
    print(f"shape={df.shape}  code定位={'行索引' if final.get('code') == '__index__' else final.get('code')}")
    print(f"[抽样 {args.code}] {json.dumps(financial_factor(args.code, df, final), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
