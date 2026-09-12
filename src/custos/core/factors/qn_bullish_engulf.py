# -*- coding: utf-8 -*-
"""QN·阳包阴/单阳包（骑牛登山体系，规则出处
`governance/strategy/qn/01_general.md` §一「骑牛原版」、§四）。

源规则（经验规律；R31 双窗跑数否决（C2 加值未双窗过线，2026-09-08），status=needs_work、live_use=none——不得进 live 链）：
- **买入优先选择单阳包形态**：阳线实体包住前阴线，且上穿 5 日/10 日线、
  量能略大于左侧。
- 阴线被后续阳线完全覆盖（解放）则不再构成压力。
- 「MACD 柱红后等小绿，小绿后阳包阴是买点」（与 qn_ma25_state 联动语境）。

确定性转译（待回测）：
- 阳包阴 = 当日阳线（收>开）且收 > 前阴开盘 且开 ≤ 前阴收盘（实体完全覆盖前阴实体）
- 上穿 5/10 日线 = 前收在两线之下、当日收站上两线（与 qn_volume_surge_cut
  的「切断」同口径，但**不要求倍量**——源规则是「量能略大于左侧」）
- 量略大于左侧 = 当日量 ≥ 前日量 ×``QN_VOL_EDGE``（≥1.0 即略大，默认 1.1）

pattern 类；绝不 raise。
"""

from __future__ import annotations

from typing import Any

from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays

FACTOR: dict[str, Any] = {
    "id": "qn_bullish_engulf",
    "name": "QN·阳包阴/单阳包",
    "kind": "pattern",
    "status": "needs_work",  # R31 双窗跑数否决（C2 加值未双窗过线，2026-09-08）
    "evidence": "governance/research/R31_qn_factor_validation.md",
    "research_ref": ["R31"],  # TODO #73 谱系回填（与 evidence 同源）
    "note": "规则出处 governance/strategy/qn/01_general.md §一；阳线实体完全覆盖前阴实体 + 上穿 MA5/MA10 + 量略大于左侧；与倍量切的差别：量门槛是「略大」非倍量",
    "min_bars": 30,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数 ----
QN_VOL_EDGE = 1.1  # 待回测：量能「略大于左侧」的下界倍数（源规则非倍量）


def detect(df, code: str = "", _arr: dict | None = None) -> dict[str, Any]:
    """阳包阴/单阳包。绝不 raise。

    ``_arr``：研究侧预计算序列（close/open/volume/ma5/ma10），给定时不读 df
    任何列。两路逐位一致（rolling 从第 0 根递归同序）。

    返回键：
        hit        三腿全中（阳包阴 + 上穿 MA5/MA10 + 量略大）
        legs       engulf / cross_ma / vol_edge 明细
    """
    try:
        n = len(df)
        if n < FACTOR["min_bars"]:
            return {
                "available": False,
                "hit": False,
                "reason": f"少于{FACTOR['min_bars']}根K线（{n}）",
            }
        if _arr is None:
            close, _high, _low, vol = _ohlcv_arrays(df)
            open_ = df["open"].astype(float).to_numpy()
            c = df["close"].astype(float)
            ma5 = c.rolling(5).mean().to_numpy()
            ma10 = c.rolling(10).mean().to_numpy()
        else:
            close = _arr["close"][:n]
            open_ = _arr["open"][:n]
            vol = _arr["volume"][:n]
            ma5 = _arr["ma5"][:n]
            ma10 = _arr["ma10"][:n]
        # 腿① 阳包阴：前根阴线 + 当日阳线实体完全覆盖前阴实体
        prev_yin = bool(close[-2] < open_[-2])
        engulf = bool(
            prev_yin
            and close[-1] > open_[-1]
            and close[-1] > open_[-2]
            and open_[-1] <= close[-2]
        )
        # 腿② 上穿 MA5/MA10：前收在线下、当日收站上
        cross_ma = bool(
            close[-2] < ma5[-2]
            and close[-2] < ma10[-2]
            and close[-1] > ma5[-1]
            and close[-1] > ma10[-1]
        )
        # 腿③ 量略大于左侧
        vol_ratio = float(vol[-1] / vol[-2]) if vol[-2] else 0.0
        vol_edge = bool(vol_ratio >= QN_VOL_EDGE)
        return {
            "available": True,
            "hit": bool(engulf and cross_ma and vol_edge),
            "legs": {
                "engulf": {"hit": engulf, "prev_yin": prev_yin},
                "cross_ma": {"hit": cross_ma},
                "vol_edge": {"hit": vol_edge, "vol_ratio": round(vol_ratio, 3)},
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
