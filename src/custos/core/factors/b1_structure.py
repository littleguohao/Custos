# -*- coding: utf-8 -*-
"""B1 结构检测器族（b1_structure）—— 非一波流 / 修复信号 / 五日战法 / 流动性 / 止损参考位。

2026-08-20（v0.86，因子化批 B）从
`pipeline/screening/enrich_candidates.py` 迁入（check_non_one_wave 及 _now_* 族、
check_repair_signals 及 _repair_* 族、check_five_day_entry、check_liquidity、
_stop_ref，及 NOW_* / REPAIR_* / FIVE_DAY_* / LIQUIDITY_WIN / STOP_LOOKBACK 常量）。
**行为零变化**：函数体逐字未动；enrich_candidates 改为 import 调用
（`enrich_candidates.check_*` / `_stop_ref` / `STOP_LOOKBACK` 仍是同一对象，
tests 的 monkeypatch 通道不变）。

`_stop_ref`（建议止损位=近 STOP_LOOKBACK 日最低价）逻辑自包含（纯 df→float），
随批 B 一并迁入本模块；enrich 的 _base_scalars / _assemble_metrics 改从这里导入
（STOP_LOOKBACK 常量在组装 stop_loss_ref.basis 文案时也要用）。

## status 定档理由（candidate）

- `live_use=scorer` / `stage=release` 是**事实**：five_day_entry 喂技术分
  「five_day_entry +8」腿、repair_signals 每项 +4（上限 +8）、non_one_wave
  confirmed +5；non_one_wave revoked 另喂封顶 C cap 判定；check_liquidity
  默认仅打 low_liquidity flag（是否降档由 cap_rules.liquidity_floor 控制）。
- `NOT_FOR_LIVE={needs_work, untested}` 由
  `tests/test_factor_registry.py::test_needs_work_cannot_be_gate_or_scorer`
  机械禁止与 scorer 共存 ⇒ 合规候选只剩 active / candidate。
- `active` 语义是「已验证可用」——本轮只是**搬迁**（零行为变化），没有新增
  任何回测证据，NOW_*/REPAIR_*/FIVE_DAY_* 参数仍是「待回测」启发式
  ⇒ 不能标 active。
- ⇒ 取 **candidate**（有依据未终审），阈值/分值校准挂回测 TODO。
"""

from __future__ import annotations

from typing import Any, Optional

from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays  # noqa: E402
from custos.core.factors.wave_type import (
    WAVE_MIN_BARS,
    _find_rally_segment,  # noqa: E402
)
from custos.core.indicators import kdj  # noqa: E402

FACTOR: dict[str, Any] = {
    "id": "b1_structure",
    "name": "B1 结构检测器族（check_non_one_wave / check_repair_signals / "
    "check_five_day_entry / check_liquidity / _stop_ref）",
    "kind": "pattern",
    # 见模块 docstring「status 定档理由」：scorer ⇒ 不能是 needs_work/untested；
    # 无回测证据 ⇒ 不能标 active；取 candidate 并挂回测 TODO。
    "status": "candidate",
    "evidence": "",
    "note": "v0.86 自 enrich_candidates 迁入（零行为变化）；喂技术分 five_day_entry +8 / "
    "repair_signals 每项 +4(上限+8) / non_one_wave confirmed +5 打分腿；non_one_wave "
    "revoked 另喂封顶 C cap；check_liquidity 默认仅 low_liquidity flag，参数待回测校准",
    "min_bars": 21,
    "live_use": "scorer",
    "stage": "release",
}

# --- B1/CZ 策略对齐参数 -------------------------------------------------
# 以下阈值全部标注"待回测参数"：策略原文（B1 §四、CZ §十六）
# 要求阈值可配置、实际值随候选落盘，不得静默使用；完成样本回测前不得
# 视为已校准。口径出处见 governance/contracts/SCREENING_WORKFLOW.md "策略对齐"章。

NOW_MILD_VOL_BURST = 2.0  # 待回测参数：上涨段单日量/段均量上限（温和放量）
NOW_BEAR_DROP_PCT = -3.0  # 待回测参数：放量大阴跌幅%
NOW_BEAR_VOL_RATIO = 1.5  # 待回测参数：放量大阴量比（量/前5日均量）
NOW_PULLBACK_VOL_RATIO = 0.7  # 待回测参数：回调段均量/上涨段均量上限
NOW_TOP_ZONE = 3  # 待回测参数：阶段高点观察区±N日

REPAIR_J_PREV_MAX = 20.0  # 待回测参数：J拐头向上（昨日J上限）
REPAIR_VOL_SHRINK = 0.7  # 待回测参数：缩量止跌量比上限
REPAIR_CHANGE_PCT = 2.0  # 待回测参数：止跌涨跌幅区间±%

FIVE_DAY_SPIKE_RATIO = 1.45  # 五日战法：近7日巨量倍数（CZ §十六）。原文"前一交易日均量"存歧义，按前一交易日单日量实现（vol[t]/vol[t-1]），待策略 owner 确认
FIVE_DAY_SPIKE_WINDOW = 7  # 五日战法：巨量观察窗口（CZ §十六）

# --- 正交因子（非量价形态）待回测参数 ---
# 方向A(2026-07-23)：全市场回测证实突破式打分非短周期 alpha，转接正交维度。
LIQUIDITY_WIN = 20  # 待回测：近N日均成交额窗口

STOP_LOOKBACK = 10  # 建议止损位：近10日最低价


def _now_top_zone_scan(close, vol, i_high: int, n: int):
    """(b) 阶段高点±NOW_TOP_ZONE 日扫描：最差跌幅 / 最大量比 / 是否放量大阴。"""
    worst_drop = None
    worst_vol_ratio = None
    big_bear = False
    for t in range(max(1, i_high - NOW_TOP_ZONE), min(n, i_high + NOW_TOP_ZONE + 1)):
        drop = (close[t] / close[t - 1] - 1) * 100
        base = vol[max(0, t - 5) : t].mean()
        vr = float(vol[t] / base) if base else None
        if worst_drop is None or drop < worst_drop:
            worst_drop = drop
        if vr is not None and (worst_vol_ratio is None or vr > worst_vol_ratio):
            worst_vol_ratio = vr
        if drop <= NOW_BEAR_DROP_PCT and vr is not None and vr >= NOW_BEAR_VOL_RATIO:
            big_bear = True
    return worst_drop, worst_vol_ratio, big_bear


def _now_break_with_volume(
    close, vol, i_low: int, i_high: int, n: int, up_vol_mean: float
) -> bool:
    """撤销：回调放量破位（跌回启动位且量>=上涨段均量）。"""
    return bool(
        len(vol[i_high + 1 :])
        and up_vol_mean
        and any(
            close[t] < close[i_low] and vol[t] >= up_vol_mean
            for t in range(i_high + 1, n)
        )
    )


def _now_status(big_bear: bool, break_with_vol: bool, mild, no_big_bear, shrink) -> str:
    if big_bear or break_with_vol:
        return "revoked"
    if mild and no_big_bear and shrink:
        return "confirmed"
    return "insufficient"


def _now_conditions(
    mild,
    max_burst,
    no_big_bear,
    worst_drop,
    worst_vol_ratio,
    shrink,
    pull_ratio,
) -> dict[str, Any]:
    return {
        "mild_volume": {
            "hit": bool(mild),
            "max_vol_burst": round(max_burst, 3) if max_burst is not None else None,
        },
        "no_top_big_bear": {
            "hit": bool(no_big_bear),
            "worst_drop_pct": round(worst_drop, 2) if worst_drop is not None else None,
            "worst_vol_ratio": round(worst_vol_ratio, 3)
            if worst_vol_ratio is not None
            else None,
        },
        "pullback_shrink": {
            "hit": bool(shrink),
            "pullback_vol_ratio": round(pull_ratio, 3)
            if pull_ratio is not None
            else None,
        },
    }


def check_non_one_wave(df) -> dict[str, Any]:
    """非一波流确认（B1 §四）：三条件各自布尔+实际值。

    confirmed=三全；revoked=顶部放量大阴或回调放量破位；其余 insufficient。
    """
    close, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    seg = _find_rally_segment(df)
    if seg is None or n < WAVE_MIN_BARS or seg[2] >= n - 2:
        return {
            "status": "insufficient",
            "available": False,
            "conditions": {},
            "reason": "无完整上涨段+回调段",
        }
    _, i_low, i_high, _ = seg

    up_vol = vol[i_low : i_high + 1]
    up_vol_mean = float(up_vol.mean()) if len(up_vol) else 0.0
    # (a) 上涨段温和放量：无单日爆量（单日量/段均量 < 2）
    max_burst = float(up_vol.max() / up_vol_mean) if up_vol_mean else None
    mild = max_burst is not None and max_burst < NOW_MILD_VOL_BURST
    # (b) 阶段高点±3日内无放量大阴（跌幅>3% 且 量/前5日均量>1.5）
    worst_drop, worst_vol_ratio, big_bear = _now_top_zone_scan(close, vol, i_high, n)
    no_big_bear = not big_bear
    # (c) 回调段缩量：回调段均量/上涨段均量 < 0.7
    pull_vol = vol[i_high + 1 :]
    pull_ratio = (
        float(pull_vol.mean() / up_vol_mean) if len(pull_vol) and up_vol_mean else None
    )
    shrink = pull_ratio is not None and pull_ratio < NOW_PULLBACK_VOL_RATIO
    break_with_vol = _now_break_with_volume(close, vol, i_low, i_high, n, up_vol_mean)
    status = _now_status(big_bear, break_with_vol, mild, no_big_bear, shrink)
    return {
        "status": status,
        "available": True,
        "conditions": _now_conditions(
            mild,
            max_burst,
            no_big_bear,
            worst_drop,
            worst_vol_ratio,
            shrink,
            pull_ratio,
        ),
        "break_with_volume": break_with_vol,
    }


def _repair_j_turn_up(j: dict) -> tuple[bool, Any, Any]:
    """修复信号①：J 拐头向上（今日 J > 昨日 J 且昨日 J < REPAIR_J_PREV_MAX）。"""
    j_now = j.get("j") if j.get("available") else None
    j_prev = j.get("j_prev") if j.get("available") else None
    hit = bool(
        j_now is not None
        and j_prev is not None
        and j_now > j_prev
        and j_prev < REPAIR_J_PREV_MAX
    )
    return hit, j_now, j_prev


def _repair_shrink_stop(
    close, vol, n: int
) -> tuple[bool, Optional[float], Optional[float]]:
    """修复信号②：缩量止跌（量比 <= REPAIR_VOL_SHRINK 且涨跌幅在 ±REPAIR_CHANGE_PCT 内）。"""
    vol_ma5_prev = float(vol[-6:-1].mean()) if n >= 6 else None
    vol_ratio = float(vol[-1] / vol_ma5_prev) if vol_ma5_prev else None
    change = (close[-1] / close[-2] - 1) * 100 if n >= 2 and close[-2] else None
    hit = bool(
        vol_ratio is not None
        and vol_ratio <= REPAIR_VOL_SHRINK
        and change is not None
        and abs(change) <= REPAIR_CHANGE_PCT
    )
    return hit, vol_ratio, change


def _repair_rs_turn(
    close, n: int, index_df
) -> tuple[bool, Optional[float], Optional[float]]:
    """修复信号③：5日相对强度由负转正（对上证指数）。"""
    rs_turn = False
    rs5_now = rs5_prev = None
    if index_df is not None and not index_df.empty and n >= 7 and len(index_df) >= 7:
        ic = index_df["close"].astype(float).to_numpy()
        rs5_now = (close[-1] / close[-6] - 1) * 100 - (ic[-1] / ic[-6] - 1) * 100
        rs5_prev = (close[-2] / close[-7] - 1) * 100 - (ic[-2] / ic[-7] - 1) * 100
        rs_turn = bool(rs5_now >= 0 > rs5_prev)
    return rs_turn, rs5_now, rs5_prev


def check_repair_signals(
    df, index_df, kdj_state: Optional[dict] = None
) -> dict[str, Any]:
    """B1 修复信号（B1 §四.2）：输出命中数组+各信号实际值。

    kdj_state 可传调用方已算好的 kdj(df)（compute_metrics 就有一份），避免同一只票
    把日线 KDJ 算两遍；不传则自己算，结果一致。
    """
    close, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    j = kdj_state if kdj_state is not None else kdj(df)
    j_turn_up, j_now, j_prev = _repair_j_turn_up(j)
    shrink_stop, vol_ratio, change = _repair_shrink_stop(close, vol, n)
    rs_turn, rs5_now, rs5_prev = _repair_rs_turn(close, n, index_df)

    signals = []
    if j_turn_up:
        signals.append("j_turn_up")
    if shrink_stop:
        signals.append("volume_shrink_stop_fall")
    if rs_turn:
        signals.append("rs_turn_strong")
    return {
        "signals": signals,
        "detail": {
            "j_turn_up": {"hit": j_turn_up, "j": j_now, "j_prev": j_prev},
            "volume_shrink_stop_fall": {
                "hit": shrink_stop,
                "vol_ratio": round(vol_ratio, 3) if vol_ratio is not None else None,
                "change_pct": round(change, 2) if change is not None else None,
            },
            "rs_turn_strong": {
                "hit": rs_turn,
                "rs5_now_pp": round(rs5_now, 2) if rs5_now is not None else None,
                "rs5_prev_pp": round(rs5_prev, 2) if rs5_prev is not None else None,
            },
        },
    }


def check_five_day_entry(df) -> dict[str, Any]:
    """五日战法入场三条件（CZ §十六，缺一不可）。"""
    close, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    if n < 21:
        return {"hit": False, "available": False, "conditions": {}}
    ma5 = float(close[-5:].mean())
    cond1 = bool(close[-1] > ma5)
    vol_ma20 = float(vol[-20:].mean())
    cond2 = bool((vol[-1] > vol[-2] > vol[-3]) or all(v >= vol_ma20 for v in vol[-3:]))
    spike_ratios = [
        float(vol[t] / vol[t - 1])
        for t in range(max(1, n - FIVE_DAY_SPIKE_WINDOW), n)
        if vol[t - 1]
    ]
    max_spike = max(spike_ratios) if spike_ratios else None
    cond3 = bool(max_spike is not None and max_spike >= FIVE_DAY_SPIKE_RATIO)
    return {
        "hit": bool(cond1 and cond2 and cond3),
        "available": True,
        "conditions": {
            "close_above_ma5": {
                "hit": cond1,
                "close": round(float(close[-1]), 4),
                "ma5": round(ma5, 4),
            },
            "three_day_volume_up": {
                "hit": cond2,
                "vols_last3": [float(v) for v in vol[-3:]],
                "vol_ma20": round(vol_ma20, 2),
            },
            "spike_within_7d": {
                "hit": cond3,
                "max_spike_ratio": round(max_spike, 3)
                if max_spike is not None
                else None,
            },
        },
    }


def check_liquidity(df, win: int = LIQUIDITY_WIN) -> dict[str, Any]:
    """流动性：近 win 日均成交额（亿元）。仅计算值，底线判定在 score 层（可配）。"""
    if "amount" not in df.columns or len(df) < 5:
        return {"available": False}
    amt = df["amount"].astype(float).to_numpy()
    avg = float(amt[-win:].mean())
    return {
        "available": bool(avg > 0),
        "avg_amount_yi": round(avg / 1e8, 4),
        "avg_amount": round(avg, 0),
        "window": win,
    }


def _stop_ref(df) -> Optional[float]:
    """建议止损位：近 STOP_LOOKBACK 日最低价（根数不足 → None）。"""
    if len(df) < STOP_LOOKBACK:
        return None
    return round(float(df["low"].tail(STOP_LOOKBACK).min()), 4)
