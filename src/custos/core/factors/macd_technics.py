# -*- coding: utf-8 -*-
"""MACD 十大技术因子（macd_technics）—— 区间状态机/背离/拐离/柱体增长的确定性检测族。

2026-08-20（v0.86，因子化批 A）从
`pipeline/screening/enrich_candidates.py` 迁入（原 1046-1251 行段 +
MACD_* 常量段）。**行为零变化**：函数体逐字未动；enrich_candidates 改为
import 调用（`enrich_candidates.check_macd_technics` 仍是同一函数对象，
tests 的 `ec.check_macd_technics` 通道不变）。

## status 定档理由（candidate）

- `live_use=scorer` / `stage=release` 是**事实**：本因子是技术分 7 条打分腿
  （zone1/zone1_restart/bottom_divergence/above_water/bar_grow/wm_bar_grow/
  top_divergence −8）的唯一生产者，并驱动 2 条 cap 判定（顶背离留痕、
  三打白骨精封顶 C）。
- `NOT_FOR_LIVE={needs_work, untested}` 由
  `tests/test_factor_registry.py::test_needs_work_cannot_be_gate_or_scorer`
  机械禁止与 scorer 共存 ⇒ 合规候选只剩 active / candidate。
- `active` 语义是「已验证可用」——本轮只是**搬迁**（零行为变化），没有新增
  任何回测证据，MACD_* 参数仍是「待回测」启发式 ⇒ 不能标 active。
- ⇒ 取 **candidate**（有依据未终审），阈值/分值校准挂回测 TODO。
"""

from __future__ import annotations

from typing import Any

from custos.core.indicators import ema, resample  # noqa: E402

FACTOR: dict[str, Any] = {
    "id": "macd_technics",
    "name": "MACD 十大技术（区间状态机/背离/拐离/柱体增长）",
    "kind": "state",
    # 见模块 docstring「status 定档理由」：scorer ⇒ 不能是 needs_work/untested；
    # 无回测证据 ⇒ 不能标 active；取 candidate 并挂回测 TODO。
    "status": "candidate",
    "evidence": "",
    "note": "v0.86 自 enrich_candidates 迁入（零行为变化）；喂技术分 7 条打分腿"
    "（zone1/zone1_restart/bottom_div/above_water/bar_grow/wm_bar_grow/top_div）"
    "+ 2 条 cap 判定（顶背离留痕、三打白骨精封顶 C），参数待回测校准",
    "min_bars": 40,
    "live_use": "scorer",
    "stage": "release",
}

# --- MACD 十大技术（macd十大技术精讲）待回测参数 ---
MACD_SWING_FRACTAL = 2  # 摆动高/低点分型：左右各 N 根确认
MACD_DIV_LOOKBACK = 60  # 背离观察窗口（日）
MACD_OVEREXT_PCTL = 0.9  # 开口/空间拐离：|DIF| 近 120 日分位上限
MACD_OVEREXT_WIN = 120  # 拐离分位窗口（日）


def _macd_zone_state(dif_last: float, dea_last: float, h0: float, h1: float):
    """三区间动能状态机 + zone1_restart（回调后再启动的强信号）。"""
    if dif_last > 0 and dea_last > 0:
        if h0 > 0:
            zone = 1 if h0 >= h1 else 2  # 扩张=第一区间；收缩（脱离DIF）=第二区间
        else:
            zone = 3  # 柱体脱离 DEA（≤0）=第三区间
    else:
        zone = 0  # 零轴下方，不做多区间分级
    zone1_restart = bool(dif_last > 0 and h0 > 0 and h0 > h1 and h1 <= 0)
    return zone, zone1_restart


def _macd_swings(close, n: int):
    """摆动高/低点（左右各 MACD_SWING_FRACTAL 根分型，右确认避免未来函数）。

    唯一或近唯一峰/谷：窗口内其余 2f 根中至少 2f-1 根严格更低/更高
    （允许至多 1 根等值，兼容双顶平台；>=2f-1 而非 <=，写反会导致唯一峰永不被检出）。
    """
    f = MACD_SWING_FRACTAL
    w0 = max(f, n - MACD_DIV_LOOKBACK)
    swing_hi = [
        i
        for i in range(w0, n - f)
        if close[i] == close[i - f : i + f + 1].max()
        and (close[i - f : i + f + 1] < close[i]).sum() >= 2 * f - 1
    ]
    swing_lo = [
        i
        for i in range(w0, n - f)
        if close[i] == close[i - f : i + f + 1].min()
        and (close[i - f : i + f + 1] > close[i]).sum() >= 2 * f - 1
    ]
    return swing_hi, swing_lo


def _macd_top_divergence(close, d, h, swing_hi, n: int) -> dict[str, Any]:
    """顶背离（高度/线型）：两个收盘摆高 B>A，但 DIF_B<DIF_A 或 hist_B<hist_A。"""
    top_div: dict[str, Any] = {"hit": False}
    if len(swing_hi) >= 2:
        a, b = swing_hi[-2], swing_hi[-1]
        if close[b] > close[a] and (d[b] < d[a] or h[b] < h[a]):
            top_div = {
                "hit": True,
                "a_bars_ago": n - 1 - a,
                "b_bars_ago": n - 1 - b,
                "close_a": round(float(close[a]), 4),
                "close_b": round(float(close[b]), 4),
                "dif_a": round(float(d[a]), 4),
                "dif_b": round(float(d[b]), 4),
                "hist_a": round(float(h[a]), 4),
                "hist_b": round(float(h[b]), 4),
            }
    return top_div


def _macd_three_peaks(close, d, swing_hi, n: int) -> dict[str, Any]:
    """三打白骨精：连续 3 个摆高递增 + DIF 连续 3 峰递减。"""
    three_peaks: dict[str, Any] = {"hit": False}
    if len(swing_hi) >= 3:
        p1, p2, p3 = swing_hi[-3], swing_hi[-2], swing_hi[-1]
        if close[p1] < close[p2] < close[p3] and d[p1] > d[p2] > d[p3]:
            three_peaks = {
                "hit": True,
                "peaks_bars_ago": [n - 1 - p1, n - 1 - p2, n - 1 - p3],
                "dif_peaks": [
                    round(float(d[p1]), 4),
                    round(float(d[p2]), 4),
                    round(float(d[p3]), 4),
                ],
            }
    return three_peaks


def _macd_bottom_divergence(close, d, swing_lo, n: int) -> dict[str, Any]:
    """底背离：窗口内两个收盘价摆低 L2<L1，但 DIF 低点抬高。"""
    bottom_div: dict[str, Any] = {"hit": False}
    if len(swing_lo) >= 2:
        a, b = swing_lo[-2], swing_lo[-1]
        if close[b] < close[a] and d[b] > d[a]:
            bottom_div = {
                "hit": True,
                "a_bars_ago": n - 1 - a,
                "b_bars_ago": n - 1 - b,
                "close_a": round(float(close[a]), 4),
                "close_b": round(float(close[b]), 4),
                "dif_a": round(float(d[a]), 4),
                "dif_b": round(float(d[b]), 4),
            }
    return bottom_div


def _macd_overextended(d, n: int, h0: float, dif_last: float) -> dict[str, Any]:
    """开口/空间拐离：|DIF| 分位 + 柱体仍在。"""
    win = min(MACD_OVEREXT_WIN, n)
    abs_dif = [abs(float(x)) for x in d[-win:]]
    pctl = (
        float(sum(1 for x in abs_dif if x <= abs_dif[-1]) / len(abs_dif))
        if win >= 20
        else None
    )
    return {
        "hit": bool(
            pctl is not None and pctl >= MACD_OVEREXT_PCTL and h0 * dif_last > 0
        ),  # “下面还有柱体”＝柱体与 DIF 同号
        "dif_abs_percentile": round(pctl, 3) if pctl is not None else None,
    }


def _hist_growing(dfx) -> "bool | None":
    """重采样序列的 MACD 红柱是否增长（柱>0 且大于上一根）。
    历史不足/计算失败 → None（算不出，与「没增长」区分）。"""
    try:
        if dfx is None or len(dfx) < 40:  # EMA26 需 ~35 根才稳定
            return None
        c = dfx["close"].astype(float).reset_index(drop=True)
        dif2 = ema(c, 12) - ema(c, 26)
        h2 = (dif2 - ema(dif2, 9)) * 2
        return bool(h2.iloc[-1] > 0 and h2.iloc[-1] > h2.iloc[-2])
    except Exception:  # noqa: BLE001
        return None


def _macd_wm_bar_grow(df, df_long):
    """周/月红柱增长 (wm_available, wm_bar_grow)。

    周/月腿用 df_long（live 传 1200 根）；没有则退回 df 自身（研究注入路径）。
    resample 也包进 try：df 缺 date 列等坏输入不得炸出（本模块绝不 raise 惯例）。
    """
    try:
        dfl = df_long if df_long is not None else df
        wg = _hist_growing(resample(dfl, "W-FRI"))
        mg = _hist_growing(resample(dfl, "ME"))
        wm_available = wg is not None and mg is not None
        return wm_available, bool(wm_available and wg and mg)
    except Exception:  # noqa: BLE001
        return False, False


def check_macd_technics(df, df_long=None) -> dict[str, Any]:
    """MACD 十大技术（macd十大技术精讲）→ 确定性因子。

    - zone：三区间动能状态机。做多口径：DIF/DEA 在零轴上且红柱扩张=第一区间
      （强势）；红柱脱离 DIF（收缩）=第二区间；红柱脱离 DEA（≤0）=第三区间。
      zone1_restart：昨日 hist≤0（或收缩后）今日重新扩张且 DIF>0——"3浪/5浪
      的第一区间"，回调后再启动的强信号。
    - bottom_divergence 底背离：窗口内两个收盘价摆低 L2<L1，但 DIF 低点抬高。
    - top_divergence 顶背离（高度/线型）：两个收盘摆高 B>A，但 DIF_B<DIF_A
      或 hist_B<hist_A。
    - three_peaks 三打白骨精：连续 3 个摆高递增 + DIF 连续 3 峰递减。
    - overextended 开口/空间拐离：|DIF| 处于近 120 日 90%+ 分位且柱体仍在。

    ``df_long``（v0.60 修复，2026-08-16 review 发现）：周/月红柱（wm_bar_grow）
    的 EMA26 需要 ≥40 根月线 ⇒ ~800 根日线，而生产 df 恒为 260 根（~13 根月线）
    ⇒ 不给长历史时月线腿**结构性恒 False**（死字段）。live 链传 df_long
    （count=1200）；研究/注入路径不传则退回用 df 自身——此时若月线根数不足，
    ``wm_available=False`` 如实标注（与「红柱没增长」区分）。⚠️ 周/月末 bin 是
    半成品 K 线（当月柱会随月内新数据翻转），与 weekly_j 同口径、有意接受。
    """
    close_s = df["close"].astype(float).reset_index(drop=True)
    n = len(df)
    if n < 40:
        return {"available": False}
    dif = ema(close_s, 12) - ema(close_s, 26)
    dea = ema(dif, 9)
    hist = (dif - dea) * 2
    d, h = dif.to_numpy(), hist.to_numpy()
    close = close_s.to_numpy()

    # 区间状态机
    dif_last, dea_last = float(dif.iloc[-1]), float(dea.iloc[-1])
    h0, h1 = float(h[-1]), float(h[-2])
    zone, zone1_restart = _macd_zone_state(dif_last, dea_last, h0, h1)

    # 摆动高/低点
    swing_hi, swing_lo = _macd_swings(close, n)

    top_div = _macd_top_divergence(close, d, h, swing_hi, n)
    three_peaks = _macd_three_peaks(close, d, swing_hi, n)
    bottom_div = _macd_bottom_divergence(close, d, swing_lo, n)

    # 开口/空间拐离：|DIF| 分位 + 柱体仍在
    overextended = _macd_overextended(d, n, h0, dif_last)

    # v0.60（2026-08-14，owner）：MACD 位置/柱体加分项的判定字段。
    # above_water=白黄线水上（DIF>0 且 DEA>0，即 zone≠0 的条件）；bar_grow=
    # 日线红柱增长（hist>0 且大于昨值）；wm_bar_grow=周线与月线红柱都在增长。
    wm_available, wm_bar_grow = _macd_wm_bar_grow(df, df_long)

    return {
        "available": True,
        "zone": zone,
        "zone1_restart": zone1_restart,
        "above_water": bool(dif_last > 0 and dea_last > 0),
        "bar_red": bool(h0 > 0),
        "bar_grow": bool(h0 > 0 and h0 > h1),
        "wm_bar_grow": bool(wm_bar_grow),
        "wm_available": bool(wm_available),  # 周/月历史不足时 False（≠红柱没增长）
        "dif": round(dif_last, 4),
        "dea": round(dea_last, 4),
        "hist": round(h0, 4),
        "bottom_divergence": bottom_div,
        "top_divergence": top_div,
        "three_peaks": three_peaks,
        "overextended": overextended,
    }
