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

import sys
from pathlib import Path
from typing import Any, Optional

_TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(_TOOLS), str(_TOOLS / "local_tdx")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REQUIRED = ("code", "net_profit", "op_cashflow")   # 缺任一 → available=False
DIXI_NET_PROFIT_YOY = 100.0                          # 待回测：业绩预增代理阈值（净利同比%）

_fin_cache: dict[str, Any] = {}


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
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def financial_factor(code: str, fin_df, colmap: dict, price: Optional[float] = None) -> dict[str, Any]:
    """CZ 抄底三条件代理（①②）。colmap 不全、数据缺失或定位不到 → available=False。绝不 raise。"""
    if not colmap or fin_df is None or getattr(fin_df, "empty", True):
        return {"available": False, "reason": "no_financials_or_colmap"}
    if any(colmap.get(f) is None for f in REQUIRED):
        return {"available": False, "reason": "required_cols_unmapped"}
    code_col = colmap.get("code")
    code6 = str(code).split(".")[0].zfill(6)
    try:
        if code_col not in getattr(fin_df, "columns", []):
            return {"available": False, "reason": "code_col_missing"}
        sub = fin_df[fin_df[code_col].astype(str).str.zfill(6) == code6]
        if sub.empty:
            return {"available": False, "reason": "code_not_found"}
        row = sub.iloc[0]
    except Exception:  # noqa: BLE001
        return {"available": False, "reason": "lookup_failed"}

    net_profit = _cell(row, colmap, "net_profit")
    op_cf = _cell(row, colmap, "op_cashflow")
    revenue = _cell(row, colmap, "revenue")
    np_yoy = _cell(row, colmap, "net_profit_yoy")
    rev_yoy = _cell(row, colmap, "revenue_yoy")
    roe = _cell(row, colmap, "roe")
    shares = _cell(row, colmap, "total_shares")
    mkt_cap = (shares * price) if (shares is not None and price) else None

    perf_surge = bool(np_yoy is not None and np_yoy >= DIXI_NET_PROFIT_YOY)        # ① 业绩预增≥100% 代理
    real_support = bool(net_profit is not None and net_profit > 0
                        and op_cf is not None and op_cf > 0)                        # ② 真实盈利+经营现金流
    roe_positive = bool(roe is not None and roe > 0)
    proxy = {"perf_surge_ge_100": perf_surge, "real_earnings_cashflow": real_support, "roe_positive": roe_positive}
    return {
        "available": True,
        "net_profit": net_profit, "op_cashflow": op_cf, "revenue": revenue,
        "net_profit_yoy": np_yoy, "revenue_yoy": rev_yoy, "roe": roe, "market_cap": mkt_cap,
        "dixi_proxy": proxy,
        "hits": [k for k, v in proxy.items() if v],
    }
