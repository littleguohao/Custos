# -*- coding: utf-8 -*-
"""量能检测器族（volume_detectors）—— 量能持续性 / 龙头量能 / 底部巨量。

2026-08-20（v0.86，因子化批 B）从
`pipeline/screening/enrich_candidates.py` 迁入（check_volume_sustain 及 _vs_* 族 +
_round3_or_none、check_leader_volume、check_bottom_volume，及 VOLUME_SUSTAIN_* /
LEADER_VOL_* / BOTTOM_* 常量）。**行为零变化**：函数体逐字未动；enrich_candidates
改为 import 调用（`enrich_candidates.check_*` 仍是同一函数对象，tests 的
monkeypatch 通道不变）。

共享件一并迁入、由本模块拥有（常量跟因子走，enrich 回导）：
- `_drawdown_250d` / `CZ_MIN_BARS` / `THREE_LOWS_DRAWDOWN_PCT`：check_bottom_volume
  与**留在 enrich 的 check_three_lows** 共用（L2 不得 import L3，只能下移到本模块，
  enrich 的 check_three_lows 改从这里导入）。THREE_LOWS_VOL_RATIO 只被 three_lows
  用，留在 enrich。

## status 定档理由（candidate）

- `live_use=scorer` / `stage=release` 是**事实**：bottom_volume 喂技术分
  「bottom_volume +10」腿、leader_volume 喂「leader_volume +6」腿，两者另作
  capital_intent 资金意图证据（ci_leader_volume/ci_bottom_volume）；
  volume_sustain 的 mainline_confirmed 作 ci_volume_sustain_mainline 证据、
  retreat 喂 cap 判定（量能撤退封顶）。
- `NOT_FOR_LIVE={needs_work, untested}` 由
  `tests/test_factor_registry.py::test_needs_work_cannot_be_gate_or_scorer`
  机械禁止与 scorer 共存 ⇒ 合规候选只剩 active / candidate。
- `active` 语义是「已验证可用」——本轮只是**搬迁**（零行为变化），没有新增
  任何回测证据，VOLUME_SUSTAIN_*/LEADER_VOL_*/BOTTOM_* 参数仍是「待回测」
  启发式 ⇒ 不能标 active。
- ⇒ 取 **candidate**（有依据未终审），阈值/分值校准挂回测 TODO。
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays  # noqa: E402

FACTOR: dict[str, Any] = {
    "id": "volume_detectors",
    "name": "量能检测器族（check_volume_sustain / check_leader_volume / check_bottom_volume）",
    "kind": "pattern",
    # 见模块 docstring「status 定档理由」：scorer ⇒ 不能是 needs_work/untested；
    # 无回测证据 ⇒ 不能标 active；取 candidate 并挂回测 TODO。
    "status": "candidate",
    "evidence": "",
    "note": "v0.86 自 enrich_candidates 迁入（零行为变化）；喂技术分 bottom_volume +10 / "
    "leader_volume +6 打分腿 + capital_intent 资金意图证据（ci_leader/ci_bottom/"
    "ci_volume_sustain_mainline）；volume_sustain 的 retreat 另喂封顶 cap，参数待回测校准",
    "min_bars": 250,
    "live_use": "scorer",
    "stage": "release",
}

# --- B1/CZ 策略对齐参数 -------------------------------------------------
# 以下阈值全部标注"待回测参数"：策略原文（CZ §九/§14.6）
# 要求阈值可配置、实际值随候选落盘，不得静默使用；完成样本回测前不得
# 视为已校准。口径出处见 governance/contracts/SCREENING_WORKFLOW.md "策略对齐"章。

VOLUME_SUSTAIN_WINDOW = 13  # 量能持续性窗口（CZ §14.6：7-13日）
VOLUME_SUSTAIN_MIN_POST_DAYS = 7  # 待回测参数：峰值日后确认主线最少观察日数
VOLUME_SUSTAIN_RATIO = 0.55  # 峰值55%（CZ §14.6）
VOLUME_SUSTAIN_RETREAT_DAYS = 3  # 连续N日<峰值55%判撤退（CZ §14.6）
LEADER_VOL_BASE_DAYS = 20  # 龙头量能基准窗口（CZ §九）
LEADER_VOL_RATIO = 1.7  # 地量1.7倍（CZ §九）
THREE_LOWS_DRAWDOWN_PCT = 40.0  # 待回测参数：三低之低价格（自250日高点回撤%）
BOTTOM_VOL_RATIO = 2.0  # 待回测参数：底部巨量（≥250日均量×2，CZ §14.6）
BOTTOM_NO_NEW_LOW_DAYS = 20  # 待回测参数：不再创新低观察窗口
CZ_MIN_BARS = 250  # CZ 三低/底部巨量最少K线数（不足→available=false）


def _vs_peak_and_post(
    df, vol: np.ndarray, n: int
) -> tuple[np.ndarray, float, int, str, np.ndarray]:
    """窗口内峰值定位：返回 (win, peak, days_since, peak_date, post)。"""
    win = vol[-VOLUME_SUSTAIN_WINDOW:]
    peak_rel = int(win.argmax())
    peak = float(win[peak_rel])
    days_since = VOLUME_SUSTAIN_WINDOW - 1 - peak_rel
    peak_pos = n - VOLUME_SUSTAIN_WINDOW + peak_rel
    peak_date = str(df["date"].iloc[peak_pos])[:10]
    post = vol[peak_pos + 1 :]
    return win, peak, days_since, peak_date, post


def _vs_retreat(vol: np.ndarray, peak: float, days_since: int) -> bool:
    """撤退判定：峰值日起连续 N 日量 < 峰值×55%。"""
    return bool(
        days_since >= VOLUME_SUSTAIN_RETREAT_DAYS
        and peak
        and all(
            v < peak * VOLUME_SUSTAIN_RATIO for v in vol[-VOLUME_SUSTAIN_RETREAT_DAYS:]
        )
    )


def _vs_confirmed(
    post: np.ndarray, peak: float, days_since: int, retreat: bool
) -> bool:
    """主线确认判定。

    与 01_cognition_framework.md §14.6 一致：峰值日后窗口内"逐日"量都必须 ≥ 峰值×55%
    （均值达标但有单日跌破不算主线确认）。
    """
    return bool(
        not retreat
        and days_since >= VOLUME_SUSTAIN_MIN_POST_DAYS
        and len(post)
        and peak
        and all(v >= peak * VOLUME_SUSTAIN_RATIO for v in post)
    )


def _vs_ratios(
    win: np.ndarray, post: np.ndarray, peak: float
) -> tuple[Optional[float], Optional[float], list]:
    """峰后均值/最低量比 + 窗口逐日量比（落盘证据列）。"""
    post_mean_ratio = float(post.mean() / peak) if len(post) and peak else None
    post_min_ratio = float(post.min() / peak) if len(post) and peak else None
    ratios_last13 = [round(float(v / peak), 3) if peak else None for v in win]
    return post_mean_ratio, post_min_ratio, ratios_last13


def _round3_or_none(v: Optional[float]) -> Optional[float]:
    """round(v, 3)，None 透传。"""
    return round(v, 3) if v is not None else None


def check_volume_sustain(df) -> dict[str, Any]:
    """量能持续性（CZ §14.6）：mainline_confirmed / retreat / neutral。"""
    _, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    if n < VOLUME_SUSTAIN_WINDOW + 1:
        return {"status": "neutral", "available": False}
    win, peak, days_since, peak_date, post = _vs_peak_and_post(df, vol, n)
    retreat = _vs_retreat(vol, peak, days_since)
    confirmed = _vs_confirmed(post, peak, days_since, retreat)
    status = (
        "retreat" if retreat else ("mainline_confirmed" if confirmed else "neutral")
    )
    post_mean_ratio, post_min_ratio, ratios_last13 = _vs_ratios(win, post, peak)
    return {
        "status": status,
        "available": True,
        "peak_date": peak_date,
        "days_since_peak": days_since,
        "post_mean_ratio": _round3_or_none(post_mean_ratio),
        "post_min_ratio": _round3_or_none(post_min_ratio),
        "vol_ratios_last13": ratios_last13,
    }


def check_leader_volume(df) -> dict[str, Any]:
    """龙头量能（CZ §九）：连续3日量 >= 前20日最低日量×1.7。"""
    _, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    if n < LEADER_VOL_BASE_DAYS + 3:
        return {"hit": False, "available": False}
    base = float(vol[-(LEADER_VOL_BASE_DAYS + 3) : -3].min())
    ratios = [float(v / base) if base else None for v in vol[-3:]]
    hit = bool(base and all(v >= base * LEADER_VOL_RATIO for v in vol[-3:]))
    return {
        "hit": hit,
        "available": True,
        "base_vol": base,
        "vol_ratios_last3": [round(r, 3) if r is not None else None for r in ratios],
    }


def _drawdown_250d(close, high) -> tuple[Optional[float], Optional[float]]:
    if len(close) < CZ_MIN_BARS:
        return None, None
    high250 = float(high[-CZ_MIN_BARS:].max())
    dd = (1 - float(close[-1]) / high250) * 100 if high250 else None
    return high250, dd


def check_bottom_volume(df) -> dict[str, Any]:
    """底部巨量（CZ §14.6）：回撤>=40% + 当日量>=250日均量×2 + 不再创新低。

    不再创新低 = 今日最低未跌破"此前"20 日最低（不含当日；含当日则恒真）。
    """
    close, high, low, vol = _ohlcv_arrays(df)
    _, dd = _drawdown_250d(close, high)
    if dd is None or len(close) < BOTTOM_NO_NEW_LOW_DAYS + 1:
        return {"hit": False, "available": False}
    vol_ma250 = float(vol[-CZ_MIN_BARS:].mean())
    huge_vol = bool(vol_ma250 and vol[-1] >= vol_ma250 * BOTTOM_VOL_RATIO)
    low20 = float(low[-(BOTTOM_NO_NEW_LOW_DAYS + 1) : -1].min())
    no_new_low = bool(low[-1] >= low20)
    return {
        "hit": bool(dd >= THREE_LOWS_DRAWDOWN_PCT and huge_vol and no_new_low),
        "available": True,
        "conditions": {
            "deep_drawdown": {
                "hit": bool(dd >= THREE_LOWS_DRAWDOWN_PCT),
                "drawdown_from_250d_high_pct": round(dd, 2),
            },
            "huge_volume": {
                "hit": huge_vol,
                "vol_ratio_vs_ma250": round(float(vol[-1] / vol_ma250), 3)
                if vol_ma250
                else None,
            },
            "no_new_low": {
                "hit": no_new_low,
                "low_today": float(low[-1]),
                "low_20d": low20,
            },
        },
    }
