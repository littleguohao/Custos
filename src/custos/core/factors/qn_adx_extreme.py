# -*- coding: utf-8 -*-
"""QN·DMI ADX≥60 极端位（骑牛登山体系，规则出处
`governance/strategy/qn/04_tape_volume_auction.md` §三）。

源规则（经验规律，未回测）：
- **ADX ≥ 60 = 一波相对区间的顶或底**（极端位置，方向未定），停止追涨杀跌。
- 方向须结合 MACD 背离 / K 线位置确认：下跌末端 ADX 见 60 + MACD 底背离 → 分批买；
  顶部区 ADX≥60 + 滞涨/顶背离 → 减仓。
- DMI 准确率 70-80%，不能单独作顶底绝对信号（源规则自注）。

确定性转译（待回测）：
- ADX = `indicators.dmi_arrays`（Wilder 口径，全项目唯一实现）
- 方向辅助腿：MACD 顶/底背离——分型摆点法（与 `bottom_patterns` 的底背离
  口径同构：两个收盘摆低 L2<L1 且 DIF 低点抬高 = 底背离；反向为顶背离）

state 类：只标极端位与方向腿，不作买卖建议。绝不 raise。
"""

from __future__ import annotations

from typing import Any

from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays
from custos.core.indicators import dmi_arrays, macd_series

FACTOR: dict[str, Any] = {
    "id": "qn_adx_extreme",
    "name": "QN·DMI ADX≥60 极端位（顶/底待定）",
    "kind": "state",
    "status": "untested",  # 新实现未回测（骑牛体系口径 + 合成用例）
    "evidence": "",
    "note": "规则出处 governance/strategy/qn/04_tape_volume_auction.md §三；ADX≥60=相对区间顶/底（方向未定），方向腿=MACD 顶/底背离 + DI 多空",
    "min_bars": 40,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数 ----
QN_ADX_EXTREME = 60.0  # 待回测：极端位阈值（源规则「ADX 大于等于 60」）
QN_DIV_WINDOW = 20  # 待回测：背离分型观察窗（近 N 根，同 bottom_patterns WBOT_WINDOW）
QN_DIV_FRACTAL = 2  # 待回测：分型左右确认根数（同 bottom_patterns WBOT_FRACTAL）


def _swing_points(arr, n: int, w0: int, f: int, find_max: bool) -> list[int]:
    """分型摆点：窗口内左右 f 根确认的局部极值（唯一）。"""
    out = []
    for i in range(w0 + f, n - f):
        seg = arr[i - f : i + f + 1]
        is_extreme = arr[i] == (seg.max() if find_max else seg.min())
        if is_extreme and float((seg == arr[i]).sum()) == 1:
            out.append(i)
    return out


def _divergence(close, dif, n: int, find_top: bool) -> dict[str, Any]:
    """MACD 背离腿：顶背离=两个收盘摆高 H2>H1 而 DIF 高点降低；
    底背离=两个收盘摆低 L2<L1 而 DIF 低点抬高（口径同 bottom_patterns 腿④）。"""
    w0 = max(0, n - QN_DIV_WINDOW * 2)
    pts = _swing_points(close, n, w0, QN_DIV_FRACTAL, find_max=find_top)
    if len(pts) < 2:
        return {"hit": False, "points": len(pts)}
    a, b = pts[-2], pts[-1]
    if find_top:
        hit = bool(close[b] > close[a] and dif[b] < dif[a])
    else:
        hit = bool(close[b] < close[a] and dif[b] > dif[a])
    return {
        "hit": hit,
        "points": len(pts),
        "close_a": round(float(close[a]), 4),
        "close_b": round(float(close[b]), 4),
        "dif_a": round(float(dif[a]), 4),
        "dif_b": round(float(dif[b]), 4),
    }


def detect(df, code: str = "", _arr: dict | None = None) -> dict[str, Any]:
    """ADX 极端位状态。绝不 raise。

    ``_arr``：研究侧预计算序列（dmi_pdi/dmi_mdi/adx——DMI 数组比 df 短 1，
    bar i 读 [i-1]，与慢路径对前缀算 dmi_arrays 取 [-1] 同位；macd_dif、close），
    给定时不读 df 任何列。两路逐位一致（Wilder 递归从第 0 根同序）。

    返回键：
        hit            ADX ≥ 60（极端位成立；hit 只标「极端」，不判方向）
        extreme_side   "top" / "bottom" / None —— 方向腿投票（背离腿优先，DI 辅助）
        legs           adx / top_divergence / bottom_divergence / di_cross 明细
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
            close, high, low, _vol = _ohlcv_arrays(df)
            pdi, mdi, adx = dmi_arrays(high, low, close)
            if pdi is None or mdi is None or adx is None:
                return {"available": False, "hit": False, "reason": "DMI 数据不足"}
            dif, _dea, _hist = macd_series(df["close"])
            d = dif.to_numpy()
            a_last, p_last, m_last = adx[-1], pdi[-1], mdi[-1]
        else:
            close = _arr["close"][:n]
            d = _arr["macd_dif"][:n]
            if _arr["adx"] is None or _arr["dmi_pdi"] is None:
                return {"available": False, "hit": False, "reason": "DMI 数据不足"}
            a_last = _arr["adx"][n - 2]  # DMI 数组短 1：bar i ↔ [i-1]
            p_last = _arr["dmi_pdi"][n - 2]
            m_last = _arr["dmi_mdi"][n - 2]
        extreme = bool(a_last >= QN_ADX_EXTREME)
        legs = {
            "adx": {"hit": extreme, "adx": round(float(a_last), 3)},
            "top_divergence": _divergence(close, d, n, find_top=True),
            "bottom_divergence": _divergence(close, d, n, find_top=False),
            "di_cross": {
                "hit": True,
                "bull": bool(p_last > m_last),
                "pdi": round(float(p_last), 3),
                "mdi": round(float(m_last), 3),
            },
        }
        side = None
        if extreme:
            if legs["top_divergence"]["hit"]:
                side = "top"
            elif legs["bottom_divergence"]["hit"]:
                side = "bottom"
        return {
            "available": True,
            "hit": extreme,
            "extreme_side": side,
            "legs": legs,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
