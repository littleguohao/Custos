# -*- coding: utf-8 -*-
"""owner 批语规则的机械化定义（v0.517，单一来源：买点练习复盘 + 后续 R52 共用）。

口径出处：``governance/research/calibration_inventory.md`` 校准循环第 6 条「批语复盘口径」及其修订
（2026-11-13，owner 指正：0 轴远近相对 MACD 自身摆幅，不按价格；等待条件未触发 = 放弃）。

- **状态陈述**（当根核实）：绿柱增大 / 刚死叉 / 离 0 轴远 / 放量下跌·阴量大于阳量；
- **等待条件**（前向触发，窗口内首个触发日）：绿柱缩短 / 双线回 0 轴附近 / 底分型 / 企稳；
- **前向读数**：自某日收盘买入，之后 ``FWD_N`` 日收盘涨跌、窗内最大涨幅（最高价）与最大回撤（最低价）。

全部只用截至判定日的数据判条件（触发判定在 j 日只读 ≤j），前向读数才读之后的 K 线。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

import numpy as np

FWD_N = 20
WAIT_N = 20
ZERO_LOOKBACK = 120
ZERO_K = 0.2

#: 批语关键词归类（与 2026-11-13 首轮复盘同一组正则；一条批语可多类）
TAGS: dict[str, str] = {
    "s_green_grow": r"绿柱.{0,4}(增大|增加|变长|变大|增长)|水下还在变长|绿柱长且还在变长",
    "s_dead_cross": r"刚死叉|准备死叉|可能死叉",
    "s_far_zero": r"(离|距离)\s*0轴.{0,4}(远|有点远|太远|较远|很远)|双线离0轴",
    "s_vol_down": r"放量下跌|放量下杀|阴量.{0,4}(远)?(大于|长于)阳量|放量大阴|阴线放量|阴量放量|放量阴线"
    r"|阴量放巨量|跳空放量阴线|绿柱放量|顶部放量|高位有放量",
    "s_bottom_div": r"底背离",
    "w_green_shrink": r"绿柱.{0,6}(缩短|变短|缩小)|macd\s*缩短|0轴缩短|绿柱开始缩短",
    "w_zero": r"回到0轴附近|双线回到0轴",
    "w_fractal": r"底分型",
    "w_stable": r"企稳",
    "try": r"可以试试|可以买入|可以做",
}
TAG_NAME = {
    "s_green_grow": "绿柱还在增大",
    "s_dead_cross": "刚死叉",
    "s_far_zero": "双线离 0 轴远",
    "s_vol_down": "放量下跌 / 阴量大于阳量",
    "s_bottom_div": "底背离",
    "w_green_shrink": "等绿柱缩短",
    "w_zero": "等双线回 0 轴附近",
    "w_fractal": "等底分型",
    "w_stable": "等企稳",
    "try": "可以试",
}


def tags_of(note: str) -> list[str]:
    return [t for t, p in TAGS.items() if re.search(p, note or "", re.I)]


def arrays(df: Any) -> dict[str, np.ndarray]:
    from custos.core.indicators import macd_series  # noqa: PLC0415

    out = {
        k: df[k].astype(float).to_numpy()
        for k in ("open", "high", "low", "close", "volume")
    }
    dif, dea, hist = (s.to_numpy() for s in macd_series(df["close"]))
    out.update(dif=dif, dea=dea, hist=hist)
    return out


def zero_amp(a: dict[str, np.ndarray], j: int) -> float:
    lo = max(0, j - ZERO_LOOKBACK + 1)
    return float(
        max(np.abs(a["dif"][lo : j + 1]).max(), np.abs(a["dea"][lo : j + 1]).max())
    )


# ---- 状态陈述（当根 i 核实）----


def green_grow(a: dict[str, np.ndarray], i: int) -> bool:
    return bool(a["hist"][i] < 0 and a["hist"][i] < a["hist"][i - 1])


def dead_cross(a: dict[str, np.ndarray], i: int) -> bool:
    d, e = a["dif"], a["dea"]
    return any(
        d[k - 1] >= e[k - 1] and d[k] < e[k] for k in range(max(1, i - 2), i + 1)
    )


def far_zero(a: dict[str, np.ndarray], i: int) -> bool:
    return bool(abs(a["dif"][i]) > ZERO_K * zero_amp(a, i))


def vol_down(a: dict[str, np.ndarray], i: int) -> bool:
    o, c, v = a["open"], a["close"], a["volume"]
    big = any(
        c[k] < o[k] and c[k] / c[k - 1] - 1 < -0.03 and v[k] > 1.5 * v[k - 5 : k].mean()
        for k in range(max(6, i - 4), i + 1)
    )
    lo = max(0, i - 19)
    seg_o, seg_c, seg_v = o[lo : i + 1], c[lo : i + 1], v[lo : i + 1]
    dn, up = seg_v[seg_c < seg_o].sum(), seg_v[seg_c >= seg_o].sum()
    return bool(big or dn > 1.5 * up)


STATE_CHECK: dict[str, Callable[[dict[str, np.ndarray], int], bool]] = {
    "s_green_grow": green_grow,
    "s_dead_cross": dead_cross,
    "s_far_zero": far_zero,
    "s_vol_down": vol_down,
}


# ---- 等待条件（j 日触发判定，只读 ≤j）----


def c_green_shrink(a: dict[str, np.ndarray], j: int) -> bool:
    return bool(a["hist"][j] < 0 and a["hist"][j] > a["hist"][j - 1])


def c_zero(a: dict[str, np.ndarray], j: int) -> bool:
    amp = zero_amp(a, j)
    return bool(abs(a["dif"][j]) <= ZERO_K * amp and abs(a["dea"][j]) <= ZERO_K * amp)


def c_fractal(a: dict[str, np.ndarray], j: int) -> bool:
    lw = a["low"]
    return bool(j >= 2 and lw[j - 1] < lw[j - 2] and lw[j - 1] < lw[j])


def c_stable(a: dict[str, np.ndarray], j: int) -> bool:
    lw = a["low"]
    return bool(
        j >= 12 and all(lw[k] >= lw[k - 10 : k].min() for k in range(j - 2, j + 1))
    )


WAIT_COND: dict[str, Callable[[dict[str, np.ndarray], int], bool]] = {
    "w_green_shrink": c_green_shrink,
    "w_zero": c_zero,
    "w_fractal": c_fractal,
    "w_stable": c_stable,
}


def first_trigger(
    a: dict[str, np.ndarray], i: int, cond: Callable, *, limit: int, start: int = 1
) -> Optional[int]:
    """i 之后第 ``start``..``WAIT_N`` 根内首个满足 cond 的下标；``limit`` = 可用最后下标（段末）。"""
    for j in range(i + start, min(limit, i + WAIT_N) + 1):
        if cond(a, j):
            return j
    return None


def forward(
    a: dict[str, np.ndarray], j: Optional[int], limit: int
) -> Optional[dict[str, float]]:
    """j 日收盘买入的前向读数；窗口越过 ``limit``（段末下标）⇒ None。"""
    if j is None or j + FWD_N > limit:
        return None
    c, h, lw = a["close"], a["high"], a["low"]
    return {
        "r20": float((c[j + FWD_N] / c[j] - 1) * 100),
        "mfe": float((h[j + 1 : j + FWD_N + 1].max() / c[j] - 1) * 100),
        "mae": float((lw[j + 1 : j + FWD_N + 1].min() / c[j] - 1) * 100),
    }


def main(_argv: Optional[list[str]] = None) -> int:
    """诊断：打印批语归类与条件定义（本模块是库）。"""
    import sys  # noqa: PLC0415

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(
        f"前向 {FWD_N} 日；等待窗 {WAIT_N} 根；0 轴附近 = 双线 ≤ {ZERO_K:g} × 近 {ZERO_LOOKBACK} 根摆幅"
    )
    for t, p in TAGS.items():
        print(f"{t:16s} {TAG_NAME[t]:<14s} {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
