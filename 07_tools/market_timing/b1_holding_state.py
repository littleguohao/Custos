# -*- coding: utf-8 -*-
"""Single deterministic B1 holding-state and action contract."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
LOCAL_TDX_DIR = TOOLS_DIR / "local_tdx"
if str(LOCAL_TDX_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_TDX_DIR))

from paths import BASE  # noqa: E402
from code_utils import norm_code  # noqa: E402

DATA = BASE / "01_data"

# 次新股前置排除阈值：上市日历天数 < 20 标记
NEW_LISTING_DAYS = 20


def finite(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def action_rank(priority: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(priority, 9)


SIGNAL_ORDER = {
    "hard_loss": 0, "n_l1_breach": 1, "trend_box_break": 2, "desc_n_confirmed": 3,
    "n_l2_breach": 10, "bbi_two_close_breach": 11, "heavy_large_bear": 12,
    "downtrend": 13, "bear_rebound_reduce": 14, "loss_reduction": 15,
    "bbi_first_breach": 20, "two_bull_profit_take": 21, "kdj_death_cross": 22,
    "shrink_small_bear": 30, "reversal_k_candidate": 31,
}


def evaluate(row: dict[str, Any], market_regime: str = "未知", price: Any = None, price_date: str | None = None) -> dict[str, Any]:
    current = finite(price)
    if current is None:
        current = finite(row.get("close"))
    pnl = finite(row.get("holding_pnl_pct"))
    trend = str(row.get("trend_state") or "未知")
    box = str(row.get("box20_position") or "未知")
    pv = row.get("price_volume") or {}
    technical_date = str(row.get("latest_date") or "") or None
    price_volume_current = not price_date or not technical_date or price_date == technical_date
    structure = row.get("n_structure") or {}
    desc_structure = row.get("descending_n_structure") or {}
    signals: list[dict[str, Any]] = []
    unavailable: list[str] = []

    def add(signal: str, priority: str, action: str, reason: str) -> None:
        signals.append({"signal": signal, "priority": priority, "action": action, "reason": reason})

    if pnl is not None and pnl <= -0.10:
        add("hard_loss", "P0", "止损/清仓评估", f"持有盈亏{pnl:.2%}达到-10%硬风控阈值")
    elif pnl is not None and pnl <= -0.07:
        add("loss_reduction", "P1", "减仓评估", f"持有盈亏{pnl:.2%}低于-7%")

    l1 = finite(structure.get("prior_low"))
    l2 = finite(structure.get("pullback_low"))
    if structure.get("available") and current is not None and l1 is not None:
        if current < l1:
            add("n_l1_breach", "P0", "N型主结构清仓评估", f"价格{current:.2f}跌破L1主结构前低{l1:.2f}")
        elif l2 is not None and current < l2:
            add("n_l2_breach", "P1", "N型回踩失守评估", f"价格{current:.2f}跌破L2更高回踩低点{l2:.2f}，但L1尚未失守")
    else:
        unavailable.append("n_structure")

    # Descending N-structure: H1 -> L1 -> lower H2 -> close below L1
    if desc_structure.get("available") and current is not None:
        structural_low = finite(desc_structure.get("structural_low"))
        if structural_low is not None and current < structural_low:
            add("desc_n_confirmed", "P0", "下降N型结构清仓评估", f"价格{current:.2f}跌破下降N型结构低点{structural_low:.2f}")
    elif not desc_structure.get("available"):
        unavailable.append("descending_n_structure")

    below_days = int(finite(row.get("consecutive_closes_below_bbi")) or 0)
    if row.get("above_bbi") is False:
        if below_days >= 2:
            add("bbi_two_close_breach", "P1", "BBI清仓评估", f"连续{below_days}日收盘跌破BBI")
        else:
            add("bbi_first_breach", "P2", "次日收复观察", "首日收盘跌破BBI，等待次日收复确认")

    if trend == "下跌" and "破位" in box:
        add("trend_box_break", "P0", "趋势破位退出评估", "下跌趋势且跌破20日箱体")
    elif trend == "下跌":
        add("downtrend", "P1", "反弹减仓", "日线处于下跌趋势")

    if price_volume_current and pv.get("heavy_large_bear"):
        add("heavy_large_bear", "P1", "放量长阴减仓/清仓评估", "放量中大阴线，量价风险显著")
    elif price_volume_current and pv.get("shrink_small_bear"):
        add("shrink_small_bear", "P3", "条件持有一天", "缩量小阴，未触发硬风险时观察次日修复")
    if price_volume_current and pv.get("two_medium_large_bull") and row.get("above_bbi") is True:
        add("two_bull_profit_take", "P2", "分批止盈", "BBI上方连续两根中大阳，按B1保护利润")
    if row.get("daily_kdj_death_cross"):
        add("kdj_death_cross", "P2", "动能转弱观察", "日线KDJ死叉，需结合趋势和结构确认")

    j = finite(row.get("daily_j"))
    reversal = bool(price_volume_current and pv.get("reversal_k_candidate_without_j") and j is not None and j < 13)
    if reversal:
        add("reversal_k_candidate", "P3", "反转K候选观察", "J<13、极致缩量、收盘±2%且振幅<=7%；仍需后续修复确认")

    if price_volume_current and market_regime == "空头" and finite(pv.get("change_pct")) is not None and finite(pv.get("change_pct")) > 0:
        add("bear_rebound_reduce", "P1", "空头反弹减仓", "0AMV空头区间出现反弹，优先降低风险敞口")

    if not pv.get("available"):
        unavailable.append("price_volume")
    if not price_volume_current:
        unavailable.append("current_price_volume")
    if pv.get("two_medium_large_bull") is None:
        unavailable.append("price_limit_for_medium_large_bull")
    unavailable += ["wave_stage", "opening_volume_ratio", "trade_execution_feedback", "max_favorable_excursion"]
    signals.sort(key=lambda item: (action_rank(item["priority"]), SIGNAL_ORDER.get(item["signal"], 99)))
    if signals:
        final = signals[0]
    else:
        final = {"priority": "P3", "action": "条件持有", "reason": "未触发B1减仓、止损或止盈信号"}
    reduction_range = {"P0": [100, 100], "P1": [10, 25], "P2": [10, 20]}.get(final["priority"], [0, 0])
    return {
        "version": "B1-holding-v1",
        "code": str(row.get("code") or "").split(".")[0],
        "as_of": row.get("latest_date"),
        "price_date": price_date or technical_date,
        "price_volume_current": price_volume_current,
        "market_regime": market_regime or "未知",
        "final_priority": final["priority"],
        "final_action": final["action"],
        "final_reason": final["reason"],
        "action_plan": {
            "suggested_reduction_pct_of_holding": reduction_range,
            "exact_quantity": None,
            "exact_quantity_reason": "精确数量必须由目标日完整行情、确认持仓基线和运行门控另行授权",
        },
        "signals": signals,
        "facts": {
            "trend_state": trend,
            "box20_position": box,
            "above_bbi": row.get("above_bbi"),
            "consecutive_closes_below_bbi": below_days,
            "n_structure": structure,
            "price_volume": pv,
            "daily_j": j,
            "holding_pnl_pct": pnl,
        },
        "permissions": {
            "allow_add": False if market_regime == "空头" else None,
            "allow_reduce": True,
            "allow_signal_override_hard_risk": False,
        },
        "unavailable": sorted(set(unavailable)),
    }


def _parse_listing_date(value: Any) -> date | None:
    s = str(value or "").strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def build_pre_checks(code: Any, as_of: date | None = None, tq: Any = None) -> dict[str, Any]:
    """B1 前置排除预检（只做加法，不影响既有信号计算）。

    通过 TQ-Local HTTP 补充：listing_date（get_stock_info.J_start）、
    is_suspended（get_more_info.TPFlag）、limit_up/limit_down_price（ZTPrice/DTPrice）。
    TQ 不可用时返回 {"available": False, ...}，调用方主流程不受影响。
    """
    if tq is None:
        import tq_http as tq  # noqa: PLC0415 —— 惰性导入，TQ 缺失不影响模块加载
    tq_code = norm_code(str(code or ""))
    info = tq.stock_info(tq_code)
    more = tq.more_info(tq_code, fields=["TPFlag", "ZTPrice", "DTPrice", "HqDate"])
    if not info["ok"] and not more["ok"]:
        return {"available": False, "error": (info["error"] or more["error"])}
    info_v = info["value"] if isinstance(info.get("value"), dict) else {}
    more_v = more["value"] if isinstance(more.get("value"), dict) else {}

    listing_date = str(info_v.get("J_start") or "").strip() or None
    listed = _parse_listing_date(listing_date)
    listing_days = (as_of - listed).days if (listed and as_of) else None
    tp_flag = str(more_v.get("TPFlag") or "").strip()
    return {
        "available": True,
        "source": "tq_http",
        "partial": not (info["ok"] and more["ok"]),
        "listing_date": listing_date,
        "listing_days": listing_days,  # 日历天数（上市日至 --date）
        "new_listing_lt20": (listing_days < NEW_LISTING_DAYS) if listing_days is not None else None,
        "is_suspended": (tp_flag not in ("", "0")) if more["ok"] else None,
        "limit_up_price": finite(more_v.get("ZTPrice")),
        "limit_down_price": finite(more_v.get("DTPrice")),
        "hq_date": str(more_v.get("HqDate") or "").strip() or None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--market-regime", default="")
    args = ap.parse_args()
    technical_path = DATA / "holdings" / f"{args.date}_holding_technical_summary.json"
    rows = json.loads(technical_path.read_text(encoding="utf-8"))
    market = json.loads((DATA / "market" / f"{args.date}_market_timing_input.json").read_text(encoding="utf-8"))
    regime = args.market_regime or str((market.get("amv_0") or {}).get("effective_state") or (market.get("amv_0") or {}).get("amv_zone") or "未知")
    as_of = datetime.strptime(args.date, "%Y-%m-%d").date()
    result = []
    for row in rows:
        state = evaluate(row, regime, price_date=args.date)
        try:
            state["pre_checks"] = build_pre_checks(row.get("code"), as_of=as_of)
        except Exception as exc:  # noqa: BLE001 —— 预检失败不影响 B1 主流程
            state["pre_checks"] = {"available": False, "error": {"code": "pre_checks_failed", "detail": str(exc)}}
        result.append(state)
    out = DATA / "holdings" / f"{args.date}_b1_holding_state.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
