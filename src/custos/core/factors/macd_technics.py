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
    "free_params": 4,  # TODO #74 示范：MACD_SWING_FRACTAL/DIV_LOOKBACK/
    # OVEREXT_PCTL/OVEREXT_WIN 四个待回测参数；candidate 不受 active 规则约束
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


# ---------------------------------------------------------------------------
# 背离 v2 候选（owner 2026-11-23：「绿柱和绿柱比、红柱和红柱比」——同色柱组的最长柱比较；未接 live）
# ---------------------------------------------------------------------------
MACD2_MIN_GROUP = (
    2  # 夹在两组之间、短于 2 根的异色柱并入两侧（避免一根翻色把一组拆成两组）
)
MACD2_MARGIN = 0.0  # 柱子「更短」的门槛：v0.566 起 0（owner 18 张标注上 10% 会漏掉同组内略短的背离）
MACD2_WAVE_FRACTAL = 2  # 同一组柱子里按柱长再切「波」：柱长局部峰（左右各 2 根）各算一个波（owner：同组有多个波峰就在波之间比）


def macd_bar_waves(hist, color: int) -> list[list[int]]:
    """同色柱组内按柱长波峰再切成「波」（时间顺序，跨组排列）；``color`` = −1 绿 / +1 红。
    相邻两峰之间的柱长谷底作为分界。"""
    import numpy as np  # noqa: PLC0415

    h = np.asarray(hist, float)
    f = MACD2_WAVE_FRACTAL
    out: list[list[int]] = []
    for col, idx in macd_bar_groups(h):
        if col != color:
            continue
        a = np.abs(h[idx])
        m = len(a)
        pk = [
            k
            for k in range(m)
            if a[k] > 0 and a[k] == a[max(0, k - f) : k + f + 1].max()
        ]
        merged: list[int] = []
        for k in pk:  # 平台上相邻的同值峰只留一个
            if merged and k - merged[-1] <= f:
                if a[k] > a[merged[-1]]:
                    merged[-1] = k
                continue
            merged.append(k)
        if len(merged) <= 1:
            out.append(list(idx))
            continue
        cuts = [
            merged[j] + int(np.argmin(a[merged[j] : merged[j + 1] + 1]))
            for j in range(len(merged) - 1)
        ]
        bounds = [0, *cuts, m - 1]
        for j in range(len(merged)):
            out.append(
                list(
                    idx[bounds[j] : bounds[j + 1] + (1 if j == len(merged) - 1 else 0)]
                )
            )
    return out


def macd_bar_groups(hist) -> list[tuple[int, list[int]]]:
    """MACD 柱按颜色分组 → [(±1, [下标...]), ...]（时间顺序；0 柱归绿）。"""
    import itertools  # noqa: PLC0415

    sign = [1 if float(x) > 0 else -1 for x in hist]
    runs = [
        [k, [i for i, _ in g]]
        for k, g in itertools.groupby(enumerate(sign), key=lambda t: t[1])
    ]
    j = 1
    while j < len(runs) - 1:
        if len(runs[j][1]) < MACD2_MIN_GROUP:
            runs[j - 1][1] += runs[j][1] + runs[j + 1][1]
            del runs[j : j + 2]
            j = max(1, j - 1)
        else:
            j += 1
    return [(int(k), idx) for k, idx in runs]


def _grp(close, hist, idx, color: int) -> dict[str, Any]:
    ip = (
        min(idx, key=lambda i: close[i])
        if color < 0
        else max(idx, key=lambda i: close[i])
    )
    ib = (
        min(idx, key=lambda i: hist[i])
        if color < 0
        else max(idx, key=lambda i: hist[i])
    )
    return {
        "start": idx[0],
        "end": idx[-1],
        "price_i": ip,
        "price": float(close[ip]),
        "bar": float(hist[ib]),
    }


def check_macd_divergence_v2(df) -> dict[str, Any]:
    """背离 v2（同色柱比较，owner 2026-11-23；v0.566 比较单位细化为「波」：同色柱组内柱长每个波峰算一波）：

    - 底背离：当根处在绿柱组；本组最低收盘 < 上一组绿柱的最低收盘（股价创新低），而本组最长绿柱
      比上一组最长绿柱短 ≥ ``MACD2_MARGIN``。当根还是红柱 ⇒ 不判（owner：「当前还没有绿柱」）。
    - 顶背离：最近两组红柱，后一组最高收盘 > 前一组，而后一组最长红柱短 ≥ ``MACD2_MARGIN``（当根可在绿柱，B1 多在回调里）。
    - 三打白骨精：最近三组红柱最高收盘逐组抬高、最长红柱逐组缩短（各短 ≥ ``MACD2_MARGIN``）。
    返回各项 {hit, groups:[{start,end,price_i,price,bar}, ...]}（下标相对 df）。"""
    from custos.core.indicators import macd_series  # noqa: PLC0415

    if df is None or len(df) < 60:
        return {"available": False}
    close = df["close"].astype(float).to_numpy()
    _dif, _dea, hist_s = macd_series(df["close"])
    hist = hist_s.to_numpy()
    greens = macd_bar_waves(
        hist, -1
    )  # v0.566：比较单位由「组」改为「波」（同组多个波峰时在波之间比）
    reds = macd_bar_waves(hist, 1)
    k = 1 - MACD2_MARGIN
    out: dict[str, Any] = {"available": True}
    bd: dict[str, Any] = {"hit": False}
    if hist[-1] < 0 and len(greens) >= 2:
        a, b = _grp(close, hist, greens[-2], -1), _grp(close, hist, greens[-1], -1)
        bd = {
            "hit": bool(b["price"] < a["price"] and abs(b["bar"]) <= abs(a["bar"]) * k),
            "groups": [a, b],
        }
    out["bottom_divergence"] = bd
    td: dict[str, Any] = {"hit": False}
    if len(reds) >= 2:
        a, b = _grp(close, hist, reds[-2], 1), _grp(close, hist, reds[-1], 1)
        td = {
            "hit": bool(b["price"] > a["price"] and b["bar"] <= a["bar"] * k),
            "groups": [a, b],
        }
    out["top_divergence"] = td
    tp: dict[str, Any] = {"hit": False}
    if len(reds) >= 3:
        g3 = [_grp(close, hist, r, 1) for r in reds[-3:]]
        tp = {
            "hit": bool(
                g3[0]["price"] < g3[1]["price"] < g3[2]["price"]
                and g3[1]["bar"] <= g3[0]["bar"] * k
                and g3[2]["bar"] <= g3[1]["bar"] * k
            ),
            "groups": g3,
        }
    out["three_peaks"] = tp
    return out


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
    - bottom_divergence / top_divergence / three_peaks：v0.567（owner 2026-11-23）起用 v2
      （``check_macd_divergence_v2``：同色柱按波比较，绿柱和绿柱比、红柱和红柱比）；🔄 原 v1（收盘 2 根分型摆点 + DIF 比较）
      owner 标注 7 张错 5 张已停用，结果只留在 ``*_v1`` 字段备查。
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

    top_div_v1 = _macd_top_divergence(close, d, h, swing_hi, n)
    three_peaks_v1 = _macd_three_peaks(close, d, swing_hi, n)
    bottom_div_v1 = _macd_bottom_divergence(close, d, swing_lo, n)
    v2 = check_macd_divergence_v2(df)  # v0.567 live 口径
    top_div = v2.get("top_divergence") or {"hit": False}
    three_peaks = v2.get("three_peaks") or {"hit": False}
    bottom_div = v2.get("bottom_divergence") or {"hit": False}

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
        "bottom_divergence_v1": bottom_div_v1,
        "top_divergence_v1": top_div_v1,
        "three_peaks_v1": three_peaks_v1,
        "overextended": overextended,
    }


# v0.223（TODO #67 B4）：规范入口别名（同对象）。live 的 check_macd_technics(
# df, df_long=df_long) 带上下文参数，调用面不变（见设计稿勘误）。
detect = check_macd_technics
