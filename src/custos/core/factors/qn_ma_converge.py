# -*- coding: utf-8 -*-
"""QN·均线收拢发散（骑牛登山体系，规则出处
`governance/strategy/qn/01_general.md` §五「均线的收拢和发散」）。

源规则（经验规律；R31 双窗跑数否决（C2 加值未双窗过线，2026-09-08），status=needs_work、live_use=none——不得进 live 链）：
- MA5/10/25/144 四线**收拢粘合成一股绳后首次向上发散** = 主升浪启动点，
  买在发散初期而非发散之后。
- 前提：MA144 明显走平上翘；144 线仍下行时收拢发散多为反弹非反转。
- 已大幅发散（间距过大）的位置不追高；等收拢后第一根放量阳线突破再介入。

确定性转译（待回测）：
- 粘合 = 四线带宽 (max−min)/mid ≤ ``QN_CONVERGE_PCT``（带宽持续
  ``QN_CONVERGE_BARS`` 根以上才算「收拢成绳」）
- 首次向上发散 = 收拢后当日带宽较上一根扩张（一根扩张即算：发散初期的带宽
  天然还小，方向由多头排列 + 站上四线 + 放量阳线承担，不靠带宽绝对值）、
  四线多头排列（MA5>MA10>MA25）且收盘站上四线 + 放量阳线
  （量 ≥ 前日 ×``QN_BREAK_SURGE``、收>开）
- MA144 走平上翘腿（记录）：近 ``QN_MA144_RISE_WIN`` 根上移

state 类：输出粘合/发散状态与启动信号。绝不 raise。
"""

from __future__ import annotations

import numpy as np

from typing import Any

from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays

FACTOR: dict[str, Any] = {
    "id": "qn_ma_converge",
    "name": "QN·均线收拢发散（四线粘合后首次向上发散）",
    "kind": "state",
    "status": "needs_work",  # R31 双窗跑数否决（C2 加值未双窗过线，2026-09-08）
    "evidence": "governance/research/R31_qn_factor_validation.md",
    "note": "规则出处 governance/strategy/qn/01_general.md §五；MA5/10/25/144 粘合（带宽≤阈值持续 N 根）后首次放量向上发散=启动点；MA144 走平上翘为前提记录腿",
    "min_bars": 170,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数 ----
QN_MA_WINDOWS = (5, 10, 25, 144)  # 源规则四线（与 v0.7 均线口径一致）
QN_CONVERGE_PCT = 0.05  # 待回测：粘合带宽上限（四线 max−min / mid）
QN_CONVERGE_BARS = 10  # 待回测：收拢持续最少根数（「成一股绳」）
QN_BREAK_SURGE = 1.5  # 待回测：发散日放量倍数（第一根放量阳线）
QN_MA144_RISE_WIN = 5  # 待回测：MA144 上翘确认根数


def _bandwidth_from_mas(mas) -> tuple[Any, list]:
    """四线带宽序列：(max−min)/mid，输入为四条 MA 数组。"""
    mx = mas[0].copy()
    mn = mas[0].copy()
    for m in mas[1:]:
        mx = np.maximum(mx, m)
        mn = np.minimum(mn, m)
    mid = (mx + mn) / 2
    return (mx - mn) / mid, mas


def _bandwidth(df) -> Any:
    """慢路径：由 df 算四条 MA 再求带宽。"""
    c = df["close"].astype(float)
    mas = [c.rolling(w).mean().to_numpy() for w in QN_MA_WINDOWS]
    return _bandwidth_from_mas(mas)


def detect(df, code: str = "", _arr: dict | None = None) -> dict[str, Any]:
    """均线收拢发散状态。绝不 raise。

    ``_arr``：研究侧预计算序列（close/open/volume/ma5/ma10/ma25/ma144），
    给定时不读 df 任何列。两路逐位一致（rolling 从第 0 根递归同序；
    带宽是四条 MA 的 elementwise 组合，输入同则输出同）。

    返回键：
        converged         当前处于粘合（带宽 ≤ 阈值）
        hit               「首次向上发散」启动信号：此前 QN_CONVERGE_BARS 根
                          持续粘合，当日带宽一根扩张（bw[-1] > bw[-2]）
                          + 多头排列 + 收盘站上四线 + 放量阳线
        legs              bandwidth / first_divergence / ma144_up 明细
                          （bandwidth.bw_prev_max_pct 为收拢期最大带宽，
                          仅记录供回测消融，不参与判定）
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
            bw, mas = _bandwidth(df)
        else:
            close = _arr["close"][:n]
            open_ = _arr["open"][:n]
            vol = _arr["volume"][:n]
            mas = [
                _arr["ma5"][:n],
                _arr["ma10"][:n],
                _arr["ma25"][:n],
                _arr["ma144"][:n],
            ]
            bw, mas = _bandwidth_from_mas(mas)
        if bw[-1] != bw[-1]:  # NaN 防御（MA144 不足）
            return {"available": False, "hit": False, "reason": "均线不可得"}

        b = QN_CONVERGE_BARS
        prev_seg = bw[-1 - b : -1]
        converged_before = bool((prev_seg <= QN_CONVERGE_PCT).all())
        converged_now = bool(bw[-1] <= QN_CONVERGE_PCT)
        # 首次向上发散：收拢后带宽当日扩张（发散初期的带宽天然还小，
        # 方向由多头排列 + 站上四线 + 放量阳线承担，不靠带宽绝对值）
        diverge_up = bool(converged_before and bw[-1] > bw[-2])
        aligned = bool(mas[0][-1] > mas[1][-1] > mas[2][-1])  # MA5>MA10>MA25
        above_all = bool(all(close[-1] > m[-1] for m in mas))
        surge_yang = bool(
            close[-1] > open_[-1] and vol[-2] and vol[-1] >= vol[-2] * QN_BREAK_SURGE
        )
        ma144 = mas[3]
        ma144_up = bool(ma144[-1] > ma144[-1 - QN_MA144_RISE_WIN])
        hit = bool(diverge_up and aligned and above_all and surge_yang)
        return {
            "available": True,
            "hit": hit,
            "converged": converged_now,
            "legs": {
                "bandwidth": {
                    "hit": converged_before,
                    "bw_now_pct": round(float(bw[-1]) * 100, 2),
                    "bw_prev_max_pct": round(float(prev_seg.max()) * 100, 2),
                },
                "first_divergence": {
                    "hit": diverge_up,
                    "aligned": aligned,
                    "above_all": above_all,
                    "surge_yang": surge_yang,
                },
                "ma144_up": {"hit": ma144_up},
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
