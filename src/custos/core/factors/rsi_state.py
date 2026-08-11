# -*- coding: utf-8 -*-
"""RSI 状态因子：区间四态 / 底背离 / 多周期关系。

**为什么加 RSI 而不是替代 J 值**——两者互补::

              J 值 (3K-2D)              RSI
    性质      无界(可负、可>100)         有界 0-100
    特性      极敏感、剧烈跳动           平滑、有记忆
    擅长      捕捉**极值时点**           判断**趋势状态**
    本项目    J<13 = 入场触发(门槛)      区间状态 = 打分/标记

**RSI 的价值不在 70/30 超买超卖，而在区间行为**（Cardwell 的 RSI 区间理论）：强势股
RSI 会长期停在 70 上方，用 70/30 会一直误判。真正有判别力的是"**RSI 回调的低点在哪**"：

    牛市区间   RSI 在 40~80 波动，回调到 40~50 获支撑  → B1 买点质量最高
    熊市区间   RSI 在 20~60 波动，反弹到 50~60 遇阻    → 反弹是卖点，不该买 B1

这正好区分"健康回调"与"下跌中继"，而这是 B1 最需要区分的两件事。与 good_b1 的
`QSX>DKS`（8/9 命中）讲同一件事，但 RSI 区间更细腻：QSX>DKS 是二元、只看当下；
RSI 区间是连续的、看的是这只票**过去一段时间的回调行为模式**。两者并存，由回测消融决定。

阈值取 Cardwell 经典值（牛市 40-80 / 熊市 20-60）作起点，全部**待回测**校准。
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from custos.core.indicators import rsi  # noqa: E402  RSI 唯一实现（本模块只保留对它的解读）

FACTOR: dict[str, Any] = {
    "id": "rsi_state",
    "name": "RSI 状态因子（H3）",
    "kind": "state",
    "status": "untested",
    "evidence": "governance/research/R8_hypothesis_H3_H4_pending.md",
    "note": "R8：已实现未跑；与 J 互补而非替代",
    "min_bars": 30,
    "live_use": "evidence_only",  # signal_labels 出标签落候选表；该模块头部已声明「标注不是交易依据」
    "stage": "release",
}


# 原先残留的 _TOOLS/screening/market_timing 三行已于 2026-08-08 删除

# ---- Cardwell 区间边界（待回测）----
BULL_RANGE_LOW = 40.0        # 牛市区间下沿：回调低点 ≥ 此值
BULL_RANGE_CONFIRM = 70.0    # 牛市确认：区间内曾突破此值
BEAR_RANGE_HIGH = 60.0       # 熊市区间上沿：反弹高点 ≤ 此值
BEAR_RANGE_LOW = 30.0        # 熊市区间：低点曾 < 此值
DEEP_OVERSOLD = 25.0         # 深水区
REGIME_LOOKBACK = 60         # 区间行为的观察窗
# 判区间行为时排除最近几根。**存在时间窗错配,待回测扫参**:RSI 跌进深水区(<25)通常需要
# ≥8 根急跌,若排除窗小于回调长度,回调段就会溢出进历史段、把区间低点打穿 40,使
# "上涨中的深度回调"(B1 最想要的形态)判不出牛市区间。实测:回调 3~5 根判 strong 但还没
# 进深水;回调 ≥8 根进了深水但已判 neutral。取 12 作起点(覆盖两周左右的回调)。
REGIME_EXCLUDE_RECENT = 12

# 多周期 RSI（B1.pdf 的四线归零思想同源，但这里只做打分/状态，不作入场门槛）
RSI_FAST, RSI_MID, RSI_SLOW = 6, 14, 24

# 底背离
DIV_LOOKBACK = 30            # 背离观察窗
DIV_MIN_GAP = 5              # 两个低点之间至少间隔的根数

RSI_MIN_BARS = 40


def rsi_regime(df: pd.DataFrame, n: int = RSI_MID,
               lookback: int = REGIME_LOOKBACK,
               exclude_recent: int = REGIME_EXCLUDE_RECENT) -> dict[str, Any]:
    """RSI 区间状态 + 当前深度，**两个维度分开报告**。绝不 raise。

    ``state`` 描述**长期结构**（这只票过去的回调行为模式）::

        strong                回调低点 ≥ BULL_RANGE_LOW 且曾 > BULL_RANGE_CONFIRM（牛市区间）
        weak_rebound          低点曾 < BEAR_RANGE_LOW 且高点 ≤ BEAR_RANGE_HIGH（反弹受阻）
        decline_continuation  同上且**反弹高点递降**（一次比一次弱）
        neutral               其余

    ``deep_oversold`` 描述**当前位置**（RSI < DEEP_OVERSOLD），与 state 独立。

    为什么必须分开：`strong + deep_oversold` 才是 B1 最理想的形态（长期向上 + 短期深跌），
    而把 deep_oversold 当成一种 state 会让它和"结构已坏的深跌"归为同类、还压低了分数——
    那正是 s_shape 用"买强分"给"买弱买点"打分的同一个错误。

    ``exclude_recent``：判区间行为时**排除最近若干根**。否则当前这次回调本身会把窗口低点
    打穿 40，使"上涨中的深度回调"永远判不出牛市区间——而那恰是要找的形态。
    """
    try:
        if df is None or len(df) < RSI_MIN_BARS:
            return {"available": False, "state": None,
                    "reason": f"少于{RSI_MIN_BARS}根K线"}
        r = rsi(df["close"], n)
        seg_all = r.iloc[-lookback:].dropna()
        if len(seg_all) < 10:
            return {"available": False, "state": None, "reason": "RSI 有效样本不足"}
        cur = float(r.iloc[-1]) if r.iloc[-1] == r.iloc[-1] else None
        if cur is None:
            return {"available": False, "state": None, "reason": "RSI 末值不可用"}
        # 区间行为用"历史段"（排除最近 exclude_recent 根）
        hist = seg_all.iloc[:-exclude_recent] if (exclude_recent
                                                 and len(seg_all) > exclude_recent + 10) else seg_all
        lo, hi = float(hist.min()), float(hist.max())

        # 反弹高点是否递降（下跌中继的特征）：把历史段分两半比高点
        half = len(hist) // 2
        hi_first = float(hist.iloc[:half].max()) if half >= 3 else None
        hi_second = float(hist.iloc[half:].max()) if len(hist) - half >= 3 else None
        lower_highs = bool(hi_first is not None and hi_second is not None
                           and hi_second < hi_first)

        if lo >= BULL_RANGE_LOW and hi > BULL_RANGE_CONFIRM:
            state = "strong"
        elif lo < BEAR_RANGE_LOW and hi <= BEAR_RANGE_HIGH:
            state = "decline_continuation" if lower_highs else "weak_rebound"
        else:
            state = "neutral"
        return {"available": True, "state": state, "rsi": round(cur, 2),
                "deep_oversold": bool(cur < DEEP_OVERSOLD),
                "range_low": round(lo, 2), "range_high": round(hi, 2),
                "lower_highs": lower_highs,
                "in_bull_range": bool(lo >= BULL_RANGE_LOW and hi > BULL_RANGE_CONFIRM),
                "n": n, "lookback": lookback, "exclude_recent": exclude_recent}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "state": None,
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


def rsi_divergence(df: pd.DataFrame, n: int = RSI_MID,
                   lookback: int = DIV_LOOKBACK,
                   min_gap: int = DIV_MIN_GAP) -> dict[str, Any]:
    """RSI 底背离：价格创新低而 RSI 不创新低（卖压衰竭）。绝不 raise。

    系统已有 MACD 底背离但没有 RSI 的，而 RSI 更敏感、通常更早。
    对 B1 的意义：`J<13 + RSI 底背离` = 超卖**且**动能衰竭，比单纯 J<13 强。
    """
    try:
        if df is None or len(df) < RSI_MIN_BARS:
            return {"available": False, "bullish": False,
                    "reason": f"少于{RSI_MIN_BARS}根K线"}
        r = rsi(df["close"], n)
        low = df["low"].astype(float)
        seg_r = r.iloc[-lookback:]
        seg_l = low.iloc[-lookback:]
        if seg_r.isna().all():
            return {"available": False, "bullish": False, "reason": "RSI 不可用"}
        # 当前是否为区间内价格新低（允许 0.5% 容差，贴近"创新低"的实际观感）
        cur_low = float(seg_l.iloc[-1])
        prior_low_idx = int(seg_l.iloc[:-min_gap].idxmin()) if len(seg_l) > min_gap else None
        if prior_low_idx is None:
            return {"available": True, "bullish": False, "reason": "窗口不足"}
        prior_low = float(low.loc[prior_low_idx])
        price_new_low = bool(cur_low <= prior_low * 1.005)
        cur_r = float(r.iloc[-1]) if r.iloc[-1] == r.iloc[-1] else None
        prior_r = float(r.loc[prior_low_idx]) if r.loc[prior_low_idx] == r.loc[prior_low_idx] else None
        if cur_r is None or prior_r is None:
            return {"available": False, "bullish": False, "reason": "RSI 端点不可用"}
        rsi_higher = bool(cur_r > prior_r)
        return {"available": True, "bullish": bool(price_new_low and rsi_higher),
                "price_new_low": price_new_low, "rsi_higher": rsi_higher,
                "cur_low": round(cur_low, 4), "prior_low": round(prior_low, 4),
                "cur_rsi": round(cur_r, 2), "prior_rsi": round(prior_r, 2),
                "bars_between": int(len(low) - 1 - low.index.get_loc(prior_low_idx))}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "bullish": False,
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


def rsi_multi(df: pd.DataFrame) -> dict[str, Any]:
    """多周期 RSI(6/14/24) 关系。绝不 raise。

    与 B1.pdf「四线归零买」同源（多周期同时超卖），但 owner 已裁定那是跟随策略、
    不属于 B1，故这里**只作状态/打分**，不注册为入场门槛。

    ``all_low``：三个周期同时 < 阈值（深度超卖）；``fast_cross_mid``：RSI6 上穿 RSI14
    （短期动能反转，类似 J 拐头但更平滑）。
    """
    try:
        if df is None or len(df) < RSI_MIN_BARS:
            return {"available": False, "reason": f"少于{RSI_MIN_BARS}根K线"}
        c = df["close"]
        rf, rm, rs = rsi(c, RSI_FAST), rsi(c, RSI_MID), rsi(c, RSI_SLOW)
        vals = [rf.iloc[-1], rm.iloc[-1], rs.iloc[-1]]
        if any(v != v for v in vals):
            return {"available": False, "reason": "RSI 末值含 NaN"}
        f, m, s = (float(x) for x in vals)
        f_prev = float(rf.iloc[-2]) if rf.iloc[-2] == rf.iloc[-2] else None
        m_prev = float(rm.iloc[-2]) if rm.iloc[-2] == rm.iloc[-2] else None
        cross = bool(f_prev is not None and m_prev is not None
                     and f_prev <= m_prev and f > m)
        return {"available": True, "rsi6": round(f, 2), "rsi14": round(m, 2),
                "rsi24": round(s, 2),
                # 短<中<长 = 下跌**加速**（近期比中期更弱）。注意匀速下跌时三者趋同、
                # 不必然有序——那种情况看 all_low_30/all_low_20。
                "stacked_low": bool(f < m < s),
                "all_low_30": bool(f < 30 and m < 30 and s < 30),
                "all_low_20": bool(f < 20 and m < 20 and s < 20),
                "fast_cross_mid": cross,
                "fast_rising": bool(f_prev is not None and f > f_prev)}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


def rsi_state_score(df: pd.DataFrame, code: str = "") -> dict[str, Any]:
    """把三部分合成 0-100 的 RSI 状态分（**仅用于回测排序**，权重待校准）。

    权重按"最少假设"分配：区间状态 50（判别健康回调 vs 下跌中继，信息量最大）、
    底背离 30（卖压衰竭的直接证据）、多周期 20（辅助确认）。
    """
    try:
        reg = rsi_regime(df, lookback=REGIME_LOOKBACK)
        div = rsi_divergence(df)
        mul = rsi_multi(df)
        if not reg.get("available"):
            return {"available": False, "score": None, "reason": reg.get("reason")}
        # 长期结构（区间行为）0-50 —— 权重最高：它决定"这票值不值得等回调"
        state_pts = {"strong": 50.0, "neutral": 10.0,
                     "weak_rebound": 3.0, "decline_continuation": 0.0}
        s = state_pts.get(reg["state"], 0.0)
        deep = bool(reg.get("deep_oversold"))
        ideal = bool(reg["state"] == "strong" and deep)
        # 短期深度 0-20 + **交互奖励 0-15**。加交互项是因为纯相加会让"结构好但没到买点"
        # 与"是买点但结构一般"同分（实测两者都是 40），而 B1 要的恰是二者**同时**成立。
        if deep:
            s += 20.0
        if ideal:
            s += 15.0
        s += 15.0 if div.get("bullish") else 0.0          # 底背离：卖压衰竭的直接证据
        if mul.get("available"):
            s += 5.0 if mul.get("fast_cross_mid") else 0.0
            s += 5.0 if mul.get("fast_rising") else 0.0
        return {"available": True, "score": round(min(100.0, s), 1),
                "regime": reg.get("state"), "rsi": reg.get("rsi"),
                "deep_oversold": bool(reg.get("deep_oversold")),
                "ideal_b1": ideal,
                "bullish_divergence": bool(div.get("bullish")),
                "regime_detail": reg, "divergence_detail": div, "multi_detail": mul}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "score": None,
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}
