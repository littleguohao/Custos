# -*- coding: utf-8 -*-
"""QN·MACD 柱体大小变化（买小绿/卖小红，骑牛登山体系，规则出处
`governance/strategy/qn/01_general.md` §八「八字要诀·抓大放小」）。

源规则（经验规律；R31 双窗跑数否决（C2 加值未双窗过线，2026-09-08），status=needs_work、live_use=none——不得进 live 链）：
- **买小绿**：MACD 绿柱由大变小（连续缩短）且股价回踩不破前低。
- **卖小红**：MACD 红柱由大变小（连续缩短）且价格滞涨（低于最近高点）——
  出场侧信号，本因子只记录、不作卖出建议。
- 注意（源规则自注）：红柱变小不一定是卖点，需结合趋势位置；柱体变化有滞后性。

确定性转译（待回测）：
- 柱 = `indicators.macd_series` hist（中式 ×2；形态判断与 ×1 无差异）
- 绿柱连续缩短 = 末 ``QN_SHRINK_BARS`` 根 hist<0 且逐根上行（绝对值变小）
- 不破前低 = 当日收盘 ≥ 此前 ``QN_LOW_WIN`` 根最低收盘
- 红柱连续缩短 = 末 ``QN_SHRINK_BARS`` 根 hist>0 且逐根下行
- 滞涨 = 当日收盘 < 此前 ``QN_HIGH_WIN`` 根最高收盘

pattern 类；绝不 raise。
"""

from __future__ import annotations

from typing import Any

from custos.core.indicators import macd_series

FACTOR: dict[str, Any] = {
    "id": "qn_macd_bar_shift",
    "name": "QN·MACD 柱体大小变化（买小绿/卖小红）",
    "kind": "pattern",
    "status": "needs_work",  # R31 双窗跑数否决（C2 加值未双窗过线，2026-09-08）
    "evidence": "governance/research/R31_qn_factor_validation.md",
    "research_ref": ["R31"],  # TODO #73 谱系回填（与 evidence 同源）
    "note": "规则出处 governance/strategy/qn/01_general.md §八；绿柱连缩+不破前低=买小绿（hit）；红柱连缩+滞涨=卖小红（出场侧记录）",
    "min_bars": 40,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数 ----
QN_SHRINK_BARS = 3  # 待回测：连续缩短根数（末 N 根逐根收缩）
QN_LOW_WIN = 5  # 待回测：买小绿的「前低」参照窗（此前 N 根最低收盘）
QN_HIGH_WIN = 5  # 待回测：卖小红的「滞涨」参照窗（此前 N 根最高收盘）


def _shrinking_green(h) -> bool:
    """末 QN_SHRINK_BARS 根绿柱连续缩短（值<0 且逐根上行）。"""
    seg = h[-QN_SHRINK_BARS:]
    return all(v < 0 for v in seg) and all(
        seg[i] > seg[i - 1] for i in range(1, len(seg))
    )


def _shrinking_red(h) -> bool:
    """末 QN_SHRINK_BARS 根红柱连续缩短（值>0 且逐根下行）。"""
    seg = h[-QN_SHRINK_BARS:]
    return all(v > 0 for v in seg) and all(
        seg[i] < seg[i - 1] for i in range(1, len(seg))
    )


def detect(df, code: str = "", _arr: dict | None = None) -> dict[str, Any]:
    """买小绿/卖小红。绝不 raise。

    ``_arr``：研究侧预计算序列（close/macd_dif/macd_dea），给定时不读 df 任何列。
    两路逐位一致（柱的逐根比较：h=(dif−dea)×2 是 2 的幂缩放，大小关系不变）。

    返回键：
        hit            买小绿（绿柱连缩 + 收盘不破前低）—— 可交易化子状态
        sell_small_red 卖小红（红柱连缩 + 滞涨）—— 出场侧记录
        legs           shrink_green / hold_low / shrink_red / stall 四腿明细
    """
    try:
        n = len(df)
        need = max(FACTOR["min_bars"], QN_SHRINK_BARS + QN_LOW_WIN + 1)
        if n < need:
            return {
                "available": False,
                "hit": False,
                "reason": f"少于{need}根K线（{n}）",
            }
        if _arr is None:
            close = df["close"].astype(float).to_numpy()
            dif, dea, _hist = macd_series(df["close"])
            h = (dif - dea).to_numpy() * 2
        else:
            close = _arr["close"][:n]
            h = (_arr["macd_dif"][:n] - _arr["macd_dea"][:n]) * 2
        shrink_green = _shrinking_green(h)
        shrink_red = _shrinking_red(h)
        hold_low = bool(close[-1] >= close[-1 - QN_LOW_WIN : -1].min())
        stall = bool(close[-1] < close[-1 - QN_HIGH_WIN : -1].max())
        return {
            "available": True,
            "hit": bool(shrink_green and hold_low),
            "sell_small_red": bool(shrink_red and stall),
            "legs": {
                "shrink_green": {
                    "hit": shrink_green,
                    "tail": [round(float(v), 6) for v in h[-QN_SHRINK_BARS:]],
                },
                "hold_low": {"hit": hold_low, "win": QN_LOW_WIN},
                "shrink_red": {"hit": shrink_red},
                "stall": {"hit": stall, "win": QN_HIGH_WIN},
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
