# -*- coding: utf-8 -*-
"""财务维度（CZ §四 抄底三条件代理）脚手架 —— 只读、best-effort、绝不 raise、不驱动分层。

CZ 抄底三条件：① 业绩预增 ≥100%；② 收入/利润/现金流真实支撑；③ 高壁垒赛道。
①② 可用财务数据代理（净利同比、经营现金流为正等）；③ 为定性赛道判断，不在此处理。

数据源为 mootdx Affair（local_tdx_data.get_financial_data，约 585 列）。**列含义随 TDX 版本
而变，本模块不硬编码列号**：由 registry `financials.columns` 把逻辑字段映射到实际列名/序号，
映射不全或数据缺失 → available=False（默认整段关闭，不影响现有流程）。校准前不视为定型。

逻辑字段：code(必填,用于定位)、net_profit、op_cashflow(②必需)、revenue、net_profit_yoy、
revenue_yoy、roe、total_shares(× 价格 → 市值)。

2026-08-20（v0.84，Phase D 因子化）：**纯评估实现迁到因子注册表**
`core/factors/fundamentals.py`（`financial_factor` 及取数 helper、常量），本模块
只留数据加载（`load_financials`/`_fin_cache`）、列映射（`auto_colmap`）与 CLI，
并 re-export 迁出的名字（`fin.financial_factor` 等仍是同一函数对象，零行为变化）。
⚠️ 方向只能是「实现上迁、本模块 re-export」：factors/ 是 L2，不得依赖 L3 的
screening（tests/test_architecture_layers.py 强制）。
"""

from __future__ import annotations

from typing import Any, Optional

# re-export：因子实现的全项目唯一一份在 core/factors/fundamentals.py（v0.84）。
from custos.core.factors.fundamentals import (  # noqa: E402,F401
    DIXI_NET_PROFIT_YOY,
    REPORT_MAX_AGE_DAYS,
    REQUIRED,
    _cell,
    _cell_text,
    _dixi_metrics,
    _locate_row,
    _parse_day,
    _stale_status,
    financial_factor,
    report_age_days,
)

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
        "net_profit": find(
            [("归属于母公司", "净利润"), ("归母净利润",), ("净利润",)],
            excludes=("同比", "增长", "率", "比率", "每股", "现金"),
        ),
        "net_profit_yoy": find(
            [
                ("净利润", "同比"),
                ("归母净利润", "同比"),
                ("净利润", "增长率"),
                ("净利润", "增长"),
            ]
        ),
        "revenue": find(
            [("营业总收入",), ("营业收入",)],
            excludes=(
                "同比",
                "增长",
                "成本",
                "率",
                "每股",
                "EBITDA",
                "%",
                "/",
                "比率",
                "占比",
            ),
        ),
        "revenue_yoy": find(
            [("营业总收入", "同比"), ("营业收入", "同比"), ("营业收入", "增长")]
        ),
        "op_cashflow": find(
            [
                ("经营活动", "现金流量净额"),
                ("经营活动产生的现金流量净额",),
                ("经营", "现金流"),
            ],
            excludes=("每股",),
        ),
        "roe": find(
            [("净资产收益率", "加权"), ("净资产收益率",), ("ROE",)],
            excludes=("同比", "增长"),
        ),
        "total_shares": find([("总股本",)], excludes=("流通",)),
    }
    if m["code"] is None:
        m["code"] = (
            "__index__"  # 无代码列 → 假定行索引即代码，financial_factor 用 index 定位
        )
    return {k: v for k, v in m.items() if v}


def load_financials(report_period: str = ""):
    """加载 TDX 财务（Affair）；失败/无数据返回 None。best-effort、绝不 raise、带缓存。"""
    key = report_period or "latest"
    if key in _fin_cache:
        return _fin_cache[key]
    df = None
    try:
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

        df = local_tdx_data.get_financial_data(report_period)
    except Exception:  # noqa: BLE001
        df = None
    _fin_cache[key] = df
    return df


def main(argv=None) -> int:
    """--inspect：加载 Affair 财务、打印自动列映射 + 抽样一只，供人工确认后写入 registry。"""
    import argparse
    import json

    ap = argparse.ArgumentParser(
        description="财务维度脚手架：--inspect 打印自动列映射供确认"
    )
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--code", default="600000", help="抽样验证的股票代码")
    ap.add_argument("--report-period", default="")
    args = ap.parse_args(argv)
    df = load_financials(args.report_period)
    if df is None or getattr(df, "empty", True):
        print(
            json.dumps(
                {"available": False, "reason": "no_financials"}, ensure_ascii=False
            )
        )
        return 0
    cm = auto_colmap(getattr(df, "columns", []))
    override: dict = {}
    try:
        from custos.core.paths import SCREEN_FORMULA_REGISTRY_FILE  # noqa: PLC0415

        reg = json.loads(SCREEN_FORMULA_REGISTRY_FILE.read_text(encoding="utf-8"))
        override = (reg.get("financials") or {}).get("columns") or {}
    except Exception:  # noqa: BLE001
        override = {}
    final = dict(cm)
    final.update(override)
    print("[自动识别 auto_colmap]:")
    print(json.dumps(cm, ensure_ascii=False, indent=2))
    if override:
        print(
            "[registry.financials.columns 覆盖]:",
            json.dumps(override, ensure_ascii=False),
        )
    print("[最终映射(enrich 实际使用)]:")
    print(json.dumps(final, ensure_ascii=False, indent=2))
    print(
        f"shape={df.shape}  code定位={'行索引' if final.get('code') == '__index__' else final.get('code')}"
    )
    print(
        f"[抽样 {args.code}] {json.dumps(financial_factor(args.code, df, final), ensure_ascii=False)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
