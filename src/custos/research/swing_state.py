# -*- coding: utf-8 -*-
"""走势三态 v2 候选（owner 2026-11-19，TODO #121）：B1 之前的走势 = 震荡上涨 / 横盘震荡 / 震荡下跌三态来回切换，
有转折记「X 转 Y」。研究侧候选，只服务定义校准图集（calib_probe trend_shape），**不进 live**；校准通过后再迁到因子层。

判法（全部只读 as-of 收盘）：

1. 逐根状态：近 ``SW_WIN`` 根收盘涨跌幅 ≥ ``SW_THR`` 记上涨、≤ −``SW_THR`` 记下跌，其余横盘；只看最近 ``SW_LOOKBACK`` 根。
2. 游程：相邻同状态合并；短于 ``SW_MIN_RUN`` 根的游程并入前一段（第一段并入后一段），再合并相邻同状态，直到全部 ≥ ``SW_MIN_RUN``。
3. 结论：只剩一段 ⇒ 该三态；否则取最后两段 ⇒「X 转 Y」（上涨 / 横盘 / 下跌）。

参数来源：在 owner 第一份三态标注（``trend_shape__mining_v1/labels3.csv``，48 张）上扫 320 组，取平坦区中心
（10 根 / 8% / 5 根 / 90 根：整条标签一致 22/48、后段一致 31/48；回看 60~120 根读数不变）。参数由这 48 张调出 ⇒
必须用新抽样图集复验，复验前不得引用为证据。
"""

from __future__ import annotations

import itertools
from typing import Any

SW_WIN = 10  # 逐根状态的回看根数
SW_THR = 0.08  # 涨跌幅阈值（8%）
SW_MIN_RUN = 5  # 一段走势最短根数
SW_LOOKBACK = 90  # 只看最近 90 根

_FULL = {"上涨": "震荡上涨", "横盘": "横盘震荡", "下跌": "震荡下跌"}


def runs(closes: Any) -> list[tuple[str, int]]:
    """[(状态短名, 根数), ...]（时间顺序）。"""
    c = [float(x) for x in closes][-(SW_LOOKBACK + SW_WIN) :]
    st = []
    for t in range(SW_WIN, len(c)):
        r = c[t] / c[t - SW_WIN] - 1 if c[t - SW_WIN] else 0.0
        st.append("上涨" if r >= SW_THR else ("下跌" if r <= -SW_THR else "横盘"))
    names: list[str] = []
    lens: list[int] = []
    for k, g in itertools.groupby(st):
        names.append(k)
        lens.append(len(list(g)))
    while len(names) > 1:
        j = next((k for k, n in enumerate(lens) if n < SW_MIN_RUN), None)
        if j is None:
            break
        lens[j - 1 if j > 0 else j + 1] += lens[j]
        del names[j], lens[j]
        mn: list[str] = []
        ml: list[int] = []
        for nm, n in zip(names, lens):
            if mn and mn[-1] == nm:
                ml[-1] += n
            else:
                mn.append(nm)
                ml.append(n)
        names, lens = mn, ml
    return list(zip(names, lens))


def swing_state(df: Any) -> dict[str, Any]:
    """{"available", "state"（菜单同名：震荡上涨 / … / 上涨转横盘 …）, "runs"}。"""
    if df is None or len(df) < SW_WIN + SW_MIN_RUN + 1:
        return {"available": False, "state": None, "runs": []}
    rs = runs(df["close"].to_numpy())
    if not rs:
        return {"available": False, "state": None, "runs": []}
    state = _FULL[rs[-1][0]] if len(rs) == 1 else f"{rs[-2][0]}转{rs[-1][0]}"
    return {"available": True, "state": state, "runs": rs}
