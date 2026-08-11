# -*- coding: utf-8 -*-
"""B2 战法与底部异动因子（来源：`other/B1.pdf`，2026-08-03）。

原文给出的 B1-B2-B3 体系（B1.pdf p16-17）::

    B1  不同**时间**周期下的多个相对低点     确认：3 个交易日内有效上涨
    B2  不同**空间**维度下的多个相对低点     确认：2 个交易日内必须有效放量
    B3  不同时间维度下持续上涨趋势的中部位置  确认：不破坏 + 突破前高

    优先级三种排序：位置 B1>B2>B3；上涨确定性 B3>B2>B1；赔率 B2>B3>B1

**B2 核心指标（原文原话）**：B1 之后 / 涨幅大于 4% / 比前一交易日放量 / J<55 /
无上影线最好。

B2 对本项目的用途不止是"多一个入场点"——它是 **B1 是否真的启动的验证信号**。把 B1
样本按"N 日内是否出现 B2"分成两组对比，能直接回答"什么样的 B1 会启动"，比继续找
排序因子更接近"提升 B1 成功率"这个目标。

底部异动（B1.pdf p12「异动选股」+ p19「底部暴力K / 击穿对手盘」）::

    异动选股：① 突然放量、量随价升 ② 异动之后上涨趋势波段内，**地量才是地价**
              ③ **找异动之后的 B1** ④ 穿越 60 日线的异动，幅度越大后续空间越大
    底部暴力K：① 巨量点火 ② 后续 4 天量不能低于巨量的一半 ③ 9 个月的新高
               ④ 新闻媒介煽风点火（无法编码，跳过）
    均线体系：30 日观察 / 60 日建仓 / 120 日必守

第 ③ 条"找异动之后的 B1"正是本项目 B1 选股的天然前置——异动确认"主力进过场"，
B1 给出回调买点。这比 b1_dual_factor 里那个简版"放量启动段"更完整（多了穿越 60 日线、
9 个月新高、点火后量能维持三个维度）。

**已接入选股链，但只作描述性证据**（`signal_labels` 出标签落候选表，
`live_use="evidence_only"`、`stage="release"`）：标注不是交易依据，不驱动分层/gate/排序。
原先这里写「未接入选股链」，2026-08-08 订正 —— 它**在跑**，只是不作决策。
阈值均标"待回测"。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))   # indicators 在 07_tools 根
# 原先的 screening/market_timing 两项已于 2026-08-08 删除：本模块只依赖 07_tools 根
# （因子层惯例：sys.path 由消费方设置，见 factors/__init__.py）。

from indicators import j_series as _j_canonical  # noqa: E402
from b1_thresholds import J_LOW_THRESHOLD  # noqa: E402  L0 唯一来源；见 B2_J_LOW 注释

FACTOR: dict[str, Any] = {
    "id": "b2_surge_factor",
    "name": "B2 异动 / 底部异动",
    "kind": "pattern",
    "status": "needs_work",
    "evidence": "governance/research/R7_hypothesis_H2_b1b2b3.md",
    "note": "R7：全否决；B2 全中≡追高，surge_strict_then_b1 跨区间零信号",
    "min_bars": 12,
    "live_use": "evidence_only",  # signal_labels 出标签落候选表；该模块头部已声明「标注不是交易依据」
    "stage": "release",
}


def _j_series(df: pd.DataFrame):
    """委托给 `indicators.j_series`，保留本模块的 `n<12` 守卫。

    守卫的意义：不足 12 根时 J 序列几乎全是 NaN，判据会退化成「永不命中」，
    返回 None 让调用方显式知道「数据不足」而不是「没信号」（审计 E9：空结果不得静默）。
    """
    if len(df) < 12:
        return None
    return _j_canonical(df).to_numpy()


# ---- B2 参数（原文给了明确数值，其余待回测）----
B2_GAIN_PCT = 4.0            # 原文:涨幅大于 4%
B2_J_MAX = 55.0              # 原文:J < 55
B2_B1_WITHIN = 5             # "B1 之后"的回看窗:原文未给天数,B1 确认条件是"3 个交易日内
                             # 有效上涨",故取 5 日留一点余量（待回测）
# B1 的 J 阈值：自 2026-08-09 起从 `b1_thresholds`（L0 唯一来源）导入 ——
# 本因子是 release 标注因子（live 候选表标签），标注应反映 live 口径 ⇒ 跟随 live
# （含 B1_J_LOW env 覆盖）；默认值 13.0 由测试钉住
# （tests/test_enrich_b1cz.py::TestReversalKThresholdSingleSource）。
B2_J_LOW = J_LOW_THRESHOLD
B2_NO_UPPER_SHADOW_FRAC = 0.15   # "无上影线最好":上影 ≤ 实体×此值算无上影（待回测）
B2_MIN_BARS = 30

# ---- 底部异动参数 ----
SURGE_VOL_MULT = 3.0         # "巨量点火":量 ≥ 此倍数 × 前 20 日均量（待回测）
SURGE_GAIN_PCT = 5.0         # 点火日涨幅下限（量随价升）（待回测）
SURGE_HOLD_DAYS = 4          # 原文:后续 4 天
SURGE_HOLD_FRAC = 0.5        # 原文:4 天量不能低于巨量的一半
SURGE_MA_CROSS = 60          # 原文:穿越 60 日线
SURGE_NEW_HIGH_DAYS = 180    # 原文:9 个月新高 ≈ 180 交易日
SURGE_LOOKBACK = 60          # 异动回看窗（"异动之后找 B1"的有效期）（待回测）
SURGE_MIN_BARS = 200         # 需 180 日新高 + 余量


def _arr(df: pd.DataFrame):
    return (df["close"].astype(float).to_numpy(),
            df["high"].astype(float).to_numpy(),
            df["low"].astype(float).to_numpy(),
            df["volume"].astype(float).to_numpy(),
            df["open"].astype(float).to_numpy())



def detect_b2(df: pd.DataFrame, code: str = "",
              b1_within: int = B2_B1_WITHIN,
              j_series: Optional[np.ndarray] = None) -> dict[str, Any]:
    """B2：B1 之后 + 涨幅>4% + 比前一交易日放量 + J<55 + 无上影线（加分）。绝不 raise。

    "B1 之后"= 近 ``b1_within`` 根内（含当日之前）出现过 J<13。
    """
    try:
        n = len(df)
        if n < B2_MIN_BARS:
            return {"available": False, "hit": False, "reason": f"少于{B2_MIN_BARS}根K线"}
        close, high, low, vol, open_ = _arr(df)
        j = j_series if j_series is not None else _j_series(df)
        if j is None:
            return {"available": False, "hit": False, "reason": "kdj_unavailable"}
        t = n - 1
        if not close[t - 1] or not vol[t - 1]:
            return {"available": False, "hit": False, "reason": "bad_prev_bar"}

        # ① B1 之后:近 b1_within 根(不含当日)出现过 J<13
        lo = max(0, t - b1_within)
        prior_j = [x for x in j[lo:t] if x == x]              # 剔除 NaN
        b1_before = bool(any(x < B2_J_LOW for x in prior_j))
        b1_bars_ago = None
        for k_ in range(t - 1, lo - 1, -1):
            if j[k_] == j[k_] and j[k_] < B2_J_LOW:
                b1_bars_ago = t - k_
                break

        # ② 涨幅 > 4%
        gain = float((close[t] / close[t - 1] - 1) * 100)
        gain_ok = bool(gain > B2_GAIN_PCT)

        # ③ 比前一交易日放量
        vol_up = bool(vol[t] > vol[t - 1])

        # ④ J < 55
        j_now = float(j[t]) if j[t] == j[t] else None
        j_ok = bool(j_now is not None and j_now < B2_J_MAX)

        # ⑤ 无上影线最好（加分项,不作硬条件）
        body = float(abs(close[t] - open_[t]))
        upper = float(high[t] - max(close[t], open_[t]))
        no_upper = bool(body > 0 and upper <= body * B2_NO_UPPER_SHADOW_FRAC)

        hit = bool(b1_before and gain_ok and vol_up and j_ok)
        return {"available": True, "hit": hit,
                "b1_before": b1_before, "b1_bars_ago": b1_bars_ago,
                "gain_pct": round(gain, 2), "gain_ok": gain_ok,
                "vol_ratio_prev": round(float(vol[t] / vol[t - 1]), 3) if vol[t - 1] else None,
                "vol_up": vol_up, "j": round(j_now, 2) if j_now is not None else None,
                "j_ok": j_ok, "no_upper_shadow": no_upper,
                "upper_shadow_frac": round(float(upper / body), 3) if body > 0 else None}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "hit": False,
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


def detect_bottom_surge(df: pd.DataFrame, code: str = "",
                        lookback: int = SURGE_LOOKBACK) -> dict[str, Any]:
    """底部异动（巨量点火 + 量能维持 + 穿越 60 日线 + 9 个月新高）。绝不 raise。

    四个维度分开报告，便于回测消融——原文四条的相对重要性未知，不该先合成一个分数。
    """
    try:
        n = len(df)
        if n < SURGE_MIN_BARS:
            return {"available": False, "hit": False,
                    "reason": f"少于{SURGE_MIN_BARS}根K线（需{SURGE_NEW_HIGH_DAYS}日新高）"}
        close, high, low, vol, _ = _arr(df)
        ma60 = pd.Series(close).rolling(SURGE_MA_CROSS).mean().to_numpy()

        best = None
        start = max(SURGE_NEW_HIGH_DAYS, n - lookback)
        for t in range(start, n):
            prev_ma20 = float(vol[t - 20:t].mean())
            if not prev_ma20 or not close[t - 1]:
                continue
            vr = float(vol[t] / prev_ma20)
            gain = float((close[t] / close[t - 1] - 1) * 100)
            # ① 巨量点火 + 量随价升
            if not (vr >= SURGE_VOL_MULT and gain >= SURGE_GAIN_PCT):
                continue
            # ② 点火后 4 天量不低于巨量的一半（不足 4 天则按已有天数判定）
            after = vol[t + 1:min(n, t + 1 + SURGE_HOLD_DAYS)]
            hold_ok = bool(len(after) and float(after.min()) >= vol[t] * SURGE_HOLD_FRAC)
            # ③ 穿越 60 日线（当日站上、前一日在下方）
            cross60 = bool(ma60[t] == ma60[t] and ma60[t - 1] == ma60[t - 1]
                           and close[t] > ma60[t] and close[t - 1] <= ma60[t - 1])
            # ④ 9 个月新高
            new_high = bool(close[t] >= float(close[t - SURGE_NEW_HIGH_DAYS:t + 1].max()))
            cand = {"bars_ago": n - 1 - t, "vol_ratio_ma20": round(float(vr), 2),
                    "gain_pct": round(gain, 2), "hold_4d_ok": hold_ok,
                    "cross_ma60": cross60, "new_high_9m": new_high,
                    "conditions_met": int(hold_ok) + int(cross60) + int(new_high)}
            # 取"满足条件最多、其次量比最大"的那次异动
            if best is None or (cand["conditions_met"], vr) > (best["conditions_met"],
                                                               best["vol_ratio_ma20"]):
                best = cand
        if best is None:
            return {"available": True, "hit": False, "reason": "no_surge"}
        return {"available": True, "hit": True, **best,
                "strict_hit": bool(best["hold_4d_ok"] and best["cross_ma60"]
                                   and best["new_high_9m"])}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "hit": False,
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


def detect_surge_then_b1(df: pd.DataFrame, code: str = "",
                         lookback: int = SURGE_LOOKBACK,
                         strict_surge: bool = False) -> dict[str, Any]:
    """原文「找异动之后的 B1」：回看窗内有底部异动 且 当日在 B1 区间（J<13）。

    ``strict_surge=True`` 要求异动同时满足量能维持 + 穿越60日线 + 9个月新高三条；
    默认只要求"巨量点火 + 量随价升"（宽口径），由回测决定该收多严。
    """
    try:
        surge = detect_bottom_surge(df, code, lookback=lookback)
        if not surge.get("available"):
            return {"available": False, "hit": False, "reason": surge.get("reason")}
        ok_surge = surge.get("strict_hit") if strict_surge else surge.get("hit")
        j = _j_series(df)
        if j is None or len(j) == 0 or j[-1] != j[-1]:
            return {"available": False, "hit": False, "reason": "kdj_unavailable",
                    "surge": surge}
        j_now = float(j[-1])
        in_b1 = j_now < B2_J_LOW
        return {"available": True, "hit": bool(ok_surge and in_b1),
                "surge_hit": bool(ok_surge), "j": round(j_now, 2), "in_b1_zone": in_b1,
                "strict_surge": strict_surge, "surge": surge}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "hit": False,
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}
