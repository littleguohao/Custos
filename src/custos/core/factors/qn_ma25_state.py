# -*- coding: utf-8 -*-
"""QN·MA25 多空分界状态（骑牛登山体系，规则出处 `governance/strategy/qn/01_general.md` §四、
`qn/08_main_wave_launch.md`）。

源规则（经验规律，未回测）：
- **MA25 是多空分界**：买前必须先站上 MA25，未站上不买；跌破 MA25 卖。
- **线上阴线买、线下阳线抛**：MA25 上方的（缩量）阴线是买点候选；
  MA25 下方出现阳线是抛点候选（「抛错了也要抛」）。
- 辅助：MACD 柱不红不看（柱体口径 = 中式 ×2 的符号，与 ×1 同向，符号判断无差异）。

本因子只判**状态**（线上/线下 × 阴/阳 × 量缩），不作买卖建议；
「线上缩量阴线」子状态是研究侧可交易化的入场候选（gate 转译口径见
`research/backtest_factors.qn_ma25_state_gate`）。

⚠️ 与 B1 的关系：MA25 与现行 v0.7 均线口径天然对齐，但「线上阴线买」是 QN 语境的
经验规则，R31 双窗跑数否决（C2 加值未双窗过线，2026-09-08），status=needs_work、live_use=none——不得进 live 链。
"""

from __future__ import annotations

from typing import Any

from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays
from custos.core.indicators import macd_series

FACTOR: dict[str, Any] = {
    "id": "qn_ma25_state",
    "name": "QN·MA25 多空分界状态（线上阴线买/线下阳线抛）",
    "kind": "state",
    "status": "needs_work",  # R31 双窗跑数否决（C2 加值未双窗过线，2026-09-08）
    "evidence": "governance/research/R31_qn_factor_validation.md",
    "research_ref": ["R31"],  # TODO #73 谱系回填（与 evidence 同源）
    "note": "规则出处 governance/strategy/qn/01_general.md §四；MA25 多空分界 + 线上阴线买/线下阳线抛状态判定，MACD 柱红绿辅助；研究侧 gate=线上缩量阴线候选",
    "min_bars": 30,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数（骑牛体系口径的确定性转译）----
QN_MA25_WIN = 25  # 待回测：多空分界均线窗口（源规则=25 日线，与 v0.7 口径一致）


def detect(df, code: str = "", _arr: dict | None = None) -> dict[str, Any]:
    """MA25 多空分界状态。绝不 raise。

    ``_arr``：研究侧预计算序列（`backtest_factors._precompute_gate_series` 的
    close/open/volume/ma25/macd_dif/macd_dea，全序列数组、按 i=len(df)-1 取点）——
    给定时不读 df 任何列（可无切片占位路径）。两路逐位一致（rolling/EMA 从第 0 根
    递归同序；hist>0 ⇔ dif>dea，×2 是 2 的幂缩放符号不变）。

    返回键：
        above_ma25        收盘在 MA25 上方（多空分界）
        macd_red          MACD 柱 > 0（红柱，「柱不红不看」的辅助）
        candle            "yang" / "yin" / "flat"（当日 K 线阴阳）
        vol_shrink        当日量 < 前日量（缩量）
        state             四态之一：线上阴线 / 线上阳线 / 线下阴线 / 线下阳线
        hit               「线上缩量阴线」= 源规则的买点候选（可交易化子状态）
        sell_candidate    「线下阳线」= 源规则的抛点候选（出场侧，供研究对照）
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
            ma25 = df["close"].astype(float).rolling(QN_MA25_WIN).mean().to_numpy()
            _dif, _dea, hist = macd_series(df["close"])
            macd_red = bool(float(hist.iloc[-1]) > 0)
        else:
            i = n - 1
            close = _arr["close"][:n]
            open_ = _arr["open"][:n]
            vol = _arr["volume"][:n]
            ma25 = _arr["ma25"][:n]
            macd_red = bool(_arr["macd_dif"][i] > _arr["macd_dea"][i])
        if ma25[-1] != ma25[-1] or ma25[-1] == 0:  # NaN 防御
            return {"available": False, "hit": False, "reason": "MA25 不可得"}
        above = bool(close[-1] > ma25[-1])
        if close[-1] > open_[-1]:
            candle = "yang"
        elif close[-1] < open_[-1]:
            candle = "yin"
        else:
            candle = "flat"
        vol_shrink = bool(vol[-1] < vol[-2])
        candle_cn = {"yang": "阳线", "yin": "阴线", "flat": "平盘"}[candle]
        state = f"{'线上' if above else '线下'}{candle_cn}"
        return {
            "available": True,
            "hit": bool(above and candle == "yin" and vol_shrink),
            "sell_candidate": bool((not above) and candle == "yang"),
            "above_ma25": above,
            "macd_red": macd_red,
            "candle": candle,
            "vol_shrink": vol_shrink,
            "state": state,
            "detail": {
                "close": round(float(close[-1]), 4),
                "ma25": round(float(ma25[-1]), 4),
                "dist_ma25_pct": round((close[-1] / ma25[-1] - 1) * 100, 2),
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
