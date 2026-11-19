# -*- coding: utf-8 -*-
"""走势三态候选（owner 2026-11-19，TODO #121）：B1 之前的走势 = 震荡上涨 / 横盘震荡 / 震荡下跌三态来回切换，
有转折记「X 转 Y」。研究侧候选，只服务定义校准图集（calib_probe trend_shape），**不进 live**；校准通过后再迁到因子层。

v3 判法（owner 2026-11-19「显示多少根 K 线就看多少 K 线」）：只看图上那 ``SW_BARS`` 根收盘（对数价）。

1. 整段做一条直线；再找一个转折点把它切成两段（每段 ≥ ``SW_MIN_SEG`` 根）各做一条直线，取误差最小的切法。
2. 两段误差和 > 整段误差 × ``SW_SPLIT_RATIO`` ⇒ 视为没有转折，只有一段。
3. 每段按直线算出的段内涨跌幅：≥ ``SW_THR`` 上涨、≤ −``SW_THR`` 下跌、其余横盘；两段相同 ⇒ 该三态，否则「X 转 Y」。

参数来源：v1（labels3）与 v2（labels4）两份各 48 张交叉验证（一份调参、另一份验证）：最短段 40~60 根两向都选中，
阈值 5%~20% 读数平坦 ⇒ 取 10% / 40 根 / 0.5。两份合计整条标签一致 39/96、后段一致 63/96；交叉验证的验证份整条 15~23/48。
仍须新抽样复验。

🔄 v2（10 根 ±8% 逐根三态 + 游程）已失效：调参样本 22/48，复验 6/48（过拟合，10 根尺度把 B1 前短回调读成一段）。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

SW_BARS = 120  # = calib_probe.CHART_BARS（图上显示的根数）
SW_MIN_SEG = 40  # 一段走势最短根数
SW_SPLIT_RATIO = 0.5  # 两段误差和须 ≤ 整段误差的一半才算有转折
SW_THR = 0.10  # 段内涨跌幅阈值（10%）

_FULL = {"上涨": "震荡上涨", "横盘": "横盘震荡", "下跌": "震荡下跌"}


def _line(y: np.ndarray) -> tuple[float, float]:
    """(斜率, 残差平方和)。"""
    x = np.arange(len(y), dtype=float)
    slope, icpt = np.polyfit(x, y, 1)
    return float(slope), float(((slope * x + icpt - y) ** 2).sum())


def _cls(slope: float, n: int) -> str:
    ch = math.exp(slope * n) - 1
    return "上涨" if ch >= SW_THR else ("下跌" if ch <= -SW_THR else "横盘")


def segments(closes: Any) -> list[tuple[str, int]]:
    """[(状态短名, 根数), ...]（时间顺序，1 或 2 段）。"""
    y = np.log(np.asarray([float(c) for c in closes][-SW_BARS:], float))
    n = len(y)
    s_all, e_all = _line(y)
    best: tuple[float, int, float, float] | None = None
    for k in range(SW_MIN_SEG, n - SW_MIN_SEG + 1):
        sa, ea = _line(y[:k])
        sb, eb = _line(y[k:])
        if best is None or ea + eb < best[0]:
            best = (ea + eb, k, sa, sb)
    if best is None or best[0] > SW_SPLIT_RATIO * e_all:
        return [(_cls(s_all, n), n)]
    _e, k, sa, sb = best
    a, b = _cls(sa, k), _cls(sb, n - k)
    return [(a, n)] if a == b else [(a, k), (b, n - k)]


def swing_state(df: Any) -> dict[str, Any]:
    """{"available", "state"（菜单同名：震荡上涨 / … / 上涨转横盘 …）, "segments"}。"""
    if df is None or len(df) < 2 * SW_MIN_SEG:
        return {"available": False, "state": None, "segments": []}
    closes = df["close"].to_numpy()
    if not np.all(np.asarray(closes[-SW_BARS:], float) > 0):
        return {"available": False, "state": None, "segments": []}
    seg = segments(closes)
    state = _FULL[seg[0][0]] if len(seg) == 1 else f"{seg[0][0]}转{seg[1][0]}"
    return {"available": True, "state": state, "segments": seg}
