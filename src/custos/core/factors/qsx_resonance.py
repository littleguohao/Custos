# -*- coding: utf-8 -*-
"""QSX/DKX 共振 v2 检测器（owner 2026-08-26 逐条定稿六要素，不许自由改动）。

口径出处与判读（先读 `governance/research/R23_qsx_resonance_filter.md`）：

- **六要素**（五成立 + 一排除，owner 定稿）：① 真碰线（low ≤ 线，无容差）且
  前一日收在在线；② 线下收盘 ≤1 根；③ 收回 = 收盘严格 > 线；④ 自触线最低点
  起 5 根内反弹 ≥3%；⑤ 缩量 = 触线日量 < 前 5 日均量；⑥ 排除态（优先级高于
  成立条件）= 「跌破 QSX 或 DKX 未收复」（碰线且收在线下 ⇒ 进入，收盘 > 线 ⇒
  解除，两线任一则排除）。
- **as-of 无未来函数**：事件在 confirm_bar = max（收回根, 反弹达标根）之前
  对后续 bar 不可见——严格因果。
- ⚠️ **R23 结论警示**：共振**计数本身零筛选价值**（v1 命中 99.2%≈恒真，v2
  预注册线字面不过）；「跌破未收复」**排除态是全部边际**，且其「加值」依赖
  出场族（BBI 族下最优 = 无过滤基底 A）。⇒ 本模块在标注层只作**观察记录**，
  标注不是交易依据（同 `signal_labels` 模块头声明）。

v0.168 从 `research/qsx_resonance_study.py` 搬走（v1 检测器留在研究脚本仅复现用）。
QSX/DKS 序列用 `core/indicators.py` 唯一实现 `qsx_series`/`dks_series`
（DKS=(MA14+MA28+MA57+MA114)/4 ⇒ ≥114 根才成形）。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from custos.core import indicators as ind

# v2 口径默认参数（owner 2026-08-26 定稿）：反弹窗 5 根、反弹幅度 ≥3%、缩量 = 触线日量
# < 前 5 日均量、近 60 根内 ≥2 次干净反弹；排除 = 跌破未收复状态
LOOKBACK = 60
MIN_EVENTS = 2
BOUNCE_BARS = 5
BOUNCE_PCT = 0.03
VOL_MA = 5


def _line_episodes_v2(
    low: np.ndarray,
    close: np.ndarray,
    high: np.ndarray,
    volume: np.ndarray,
    line: np.ndarray,
    bounce_bars: int,
    bounce_pct: float,
    vol_ma: int,
) -> list[tuple[int, int]]:
    """单条线的「干净的跌线反弹」事件列表 [(t_touch, confirm_bar), ...]（v2 口径）。

    五要素逐条（owner 定稿，不许自由改动）：
    ① 碰线 ``low[t] ≤ line[t]`` 且前一日收盘在在线（从上往下碰，线下运行不算）；
    ② 线下收盘 ≤1 根：``close[t] ≤ line[t]`` 则必须 ``close[t+1] > line[t+1]``
       （reclaim=t+1），连破 2 根即无效；碰线当日收在线上即当日收回（reclaim=t）；
    ③ 收回 = reclaim_bar（收盘 > 线，严格大于）；
    ④ 反弹幅度：``min_low = min(low[t..reclaim])``，窗 ``[t, t+bounce_bars]`` 内
       首个 ``high[u] ≥ min_low × (1+bounce_pct)`` ⇒ bounce_bar；缺 ⇒ 无效；
    ⑤ 缩量：``volume[t] < mean(volume[t-vol_ma..t-1])``（t < vol_ma 无前窗 ⇒ 无效）。
    confirm_bar = max(reclaim_bar, bounce_bar)——之前不可见，严格因果。
    """
    n = len(close)
    events: list[tuple[int, int]] = []
    for t in range(max(1, vol_ma), n):  # ①要前一日收盘、⑤要前 vol_ma 根量
        lv = line[t]
        if lv != lv or not (low[t] <= lv):  # ① 碰线（真碰，无容差）
            continue
        prev = line[t - 1]
        if prev != prev or close[t - 1] <= prev:  # 前一日须收在在线（从上往下）
            continue
        if not volume[t] < float(np.mean(volume[t - vol_ma : t])):  # ⑤ 缩量
            continue
        if close[t] > lv:  # ③ 当日收回（线下收盘 0 根）
            reclaim = t
        elif t + 1 < n and line[t + 1] == line[t + 1] and close[t + 1] > line[t + 1]:
            reclaim = t + 1  # ② 线下收盘恰好 1 根后收回
        else:
            continue  # 连破 ≥2 根 ⇒ 不是干净反弹
        min_low = float(np.min(low[t : reclaim + 1]))  # 触线最低点
        bounce_bar = None
        for u in range(t, min(t + bounce_bars, n - 1) + 1):  # ④ 随后 N 根内
            if high[u] >= min_low * (1 + bounce_pct):
                bounce_bar = u
                break
        if bounce_bar is None:
            continue
        events.append((t, max(reclaim, bounce_bar)))
    return events


def _unrecovered_line(
    low: np.ndarray, close: np.ndarray, line: np.ndarray
) -> np.ndarray:
    """逐 bar「跌破未收复」状态（v2 排除条件）：碰线且收在线下 ⇒ 进入；收盘 > 线 ⇒ 解除。

    owner 原话：「触线后没有站上反而继续下跌，说明 QSX/DKX 空头有效，需要排除」。
    线未成形（NaN）根不判。
    """
    n = len(close)
    out = np.zeros(n, dtype=bool)
    broken = False
    for i in range(n):
        lv = line[i]
        if lv != lv:  # NaN 守卫
            continue
        if close[i] > lv:
            broken = False
        elif low[i] <= lv:
            broken = True
        out[i] = broken
    return out


def qsx_dks_resonance_v2(
    close: pd.Series,
    low: pd.Series,
    high: pd.Series,
    volume: pd.Series,
    qsx: pd.Series,
    dks: pd.Series,
    lookback: int = LOOKBACK,
    bounce_bars: int = BOUNCE_BARS,
    bounce_pct: float = BOUNCE_PCT,
    vol_ma: int = VOL_MA,
    min_events: int = MIN_EVENTS,
) -> tuple[np.ndarray, np.ndarray]:
    """过滤② v2「干净共振」：返回 ``(hit, excluded)`` 两个布尔序列（as-of 无未来函数）。

    - ``hit[i]``：近 ``lookback`` 根内（触线根 ≥ i−lookback+1）已确认
      （confirm_bar ≤ i）的干净跌线反弹事件 ≥ ``min_events``；
      两线事件按触线段重叠去重（同一次下跌碰两线只算一次）。
    - ``excluded[i]``：QSX 或 DKS 任一处于「跌破未收复」状态。
    进场用 ``hit & ~excluded``（排除条件优先级高于成立条件，owner 定）。
    """
    c = close.astype(float).to_numpy()
    lo = low.astype(float).to_numpy()
    hi = high.astype(float).to_numpy()
    vol = volume.astype(float).to_numpy()
    qs = qsx.astype(float).to_numpy()
    dk = dks.astype(float).to_numpy()
    ev = _line_episodes_v2(lo, c, hi, vol, qs, bounce_bars, bounce_pct, vol_ma)
    ev += _line_episodes_v2(lo, c, hi, vol, dk, bounce_bars, bounce_pct, vol_ma)
    ev.sort(key=lambda e: (e[0], e[1]))
    dedup: list[tuple[int, int]] = []
    for e in ev:
        if dedup and e[0] <= dedup[-1][0]:  # 同一根触线碰两线 ⇒ 同一次下跌
            continue
        dedup.append(e)
    n = len(c)
    cnt = np.zeros(n, dtype=int)
    for t_touch, confirm in dedup:
        hi_i = min(t_touch + lookback - 1, n - 1)  # 事件对 [confirm, t+lookback-1] 可见
        if confirm <= hi_i:
            cnt[confirm : hi_i + 1] += 1
    hit = cnt >= min_events
    excluded = _unrecovered_line(lo, c, qs) | _unrecovered_line(lo, c, dk)
    return hit, excluded


def resonance_v2_snapshot(df: pd.DataFrame) -> dict[str, Any]:
    """标注层入口：全序列**末根**的共振 v2 快照（绝不 raise——异常由调用方兜底）。

    返回 ``{"available", "hit", "excluded", "events", "reason"}``：
    - ``hit``：末根近 LOOKBACK 根内已确认干净反弹事件 ≥ MIN_EVENTS（成立条件，
      **未减排除态**——排除与否由 ``excluded`` 单独给）；
    - ``excluded``：末根处于「跌破未收复」状态（QSX 或 DKS 任一）；
    - ``events``：末根近 LOOKBACK 根内已确认事件数（与检测器同去重口径）；
    - 数据不足（DKS 未成形 = <114 根有效，或无前窗量）⇒ ``available=False`` + reason。
    ⚠️ R23：计数零筛选价值、排除项是全部边际——本快照仅作观察记录，非交易依据。
    """
    na: dict[str, Any] = {
        "available": False,
        "hit": False,
        "excluded": False,
        "events": 0,
    }
    if df is None or not len(df):
        return {**na, "reason": "no_bars"}
    close = df["close"].astype(float)
    qsx = ind.qsx_series(close)  # 序列级唯一实现
    dks = ind.dks_series(close)  # DKS=(MA14+MA28+MA57+MA114)/4 ⇒ ≥114 根才成形
    if pd.isna(qsx.iloc[-1]) or pd.isna(dks.iloc[-1]):
        return {**na, "reason": "dks_not_formed"}
    i = len(close) - 1
    hit, excluded = qsx_dks_resonance_v2(
        close,
        df["low"].astype(float),
        df["high"].astype(float),
        df["volume"].astype(float),
        qsx,
        dks,
    )
    # 末根近 LOOKBACK 根内已确认事件数（与 qsx_dks_resonance_v2 同双线去重口径）
    c = close.to_numpy()
    lo = df["low"].astype(float).to_numpy()
    hi = df["high"].astype(float).to_numpy()
    vol = df["volume"].astype(float).to_numpy()
    ev = _line_episodes_v2(
        lo, c, hi, vol, qsx.to_numpy(), BOUNCE_BARS, BOUNCE_PCT, VOL_MA
    )
    ev += _line_episodes_v2(
        lo, c, hi, vol, dks.to_numpy(), BOUNCE_BARS, BOUNCE_PCT, VOL_MA
    )
    ev.sort(key=lambda e: (e[0], e[1]))
    dedup: list[tuple[int, int]] = []
    for e in ev:
        if dedup and e[0] <= dedup[-1][0]:  # 同一根触线碰两线 ⇒ 同一次下跌
            continue
        dedup.append(e)
    events = sum(1 for t, confirm in dedup if confirm <= i <= t + LOOKBACK - 1)
    return {
        "available": True,
        "hit": bool(hit[i]),
        "excluded": bool(excluded[i]),
        "events": events,
        "reason": None,
    }
