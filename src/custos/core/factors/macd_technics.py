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

from custos.core.indicators import macd_series, resample  # noqa: E402

FACTOR: dict[str, Any] = {
    "id": "macd_technics",
    "name": "MACD 十大技术（区间状态机/背离/拐离/柱体增长）",
    "kind": "state",
    # 见模块 docstring「status 定档理由」：scorer ⇒ 不能是 needs_work/untested；
    # 无回测证据 ⇒ 不能标 active；取 candidate 并挂回测 TODO。
    "status": "candidate",
    "evidence": "",
    "free_params": 4,  # v0.594：MACD2_MIN_GROUP / MARGIN / WAVE_FRACTAL / WAVE_VALLEY 四个参数；candidate 不受 active 规则约束
    "note": "v0.86 自 enrich_candidates 迁入（零行为变化）；喂技术分 7 条打分腿"
    "（zone1/zone1_restart/bottom_div/above_water/bar_grow/wm_bar_grow/top_div）"
    "+ 2 条 cap 判定（顶背离留痕、三打白骨精封顶 C），参数待回测校准",
    "min_bars": 40,
    "live_use": "scorer",
    "stage": "release",
}

# --- MACD 十大技术（macd十大技术精讲）待回测参数 ---


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


def _macd_overextended(d, e, h) -> dict[str, Any]:
    """乖离过大（qn/01_general.md §二「乖离过大 = 失去支撑力量，追高易站岗」；owner 2026-11-24 定义）：
    MACD 双线还在上涨（DIF、DEA 都比昨天高），但红柱开始滞涨或下降（当根红柱 ≤ 昨天），
    且双线已经超过红柱范围（DEA 高于当根红柱顶端；DIF > DEA，故两线都在柱顶之上）。纯数值判定，钉测管。
    🔄 v0.571 前是「|DIF| 近 120 根分位 ≥90% 且柱体与 DIF 同号」，绿柱也会命中，与原文不符，已停用。"""
    if len(h) < 2:
        return {"hit": False}
    d0, d1, e0, e1, h0, h1 = (
        float(x) for x in (d[-1], d[-2], e[-1], e[-2], h[-1], h[-2])
    )
    lines_up = d0 > d1 and e0 > e1
    bar_stall = 0 < h0 <= h1
    lines_above_bar = e0 > h0
    return {
        "hit": bool(lines_up and bar_stall and lines_above_bar),
        "lines_up": bool(lines_up),
        "bar_stall": bool(bar_stall),
        "lines_above_bar": bool(lines_above_bar),
    }


# ---------------------------------------------------------------------------
# 背离 v2 候选（owner 2026-11-23：「绿柱和绿柱比、红柱和红柱比」——同色柱组的最长柱比较；未接 live）
# ---------------------------------------------------------------------------
MACD2_MIN_GROUP = (
    2  # 夹在两组之间、短于 2 根的异色柱并入两侧（避免一根翻色把一组拆成两组）
)
MACD2_MARGIN = 0.0  # 柱子「更短」的门槛：v0.566 起 0（owner 18 张标注上 10% 会漏掉同组内略短的背离）
MACD2_WAVE_FRACTAL = 2  # 同一组柱子里按柱长再切「波」：柱长局部峰（左右各 2 根）各算一个波（owner：同组有多个波峰就在波之间比）
MACD2_WAVE_VALLEY = 0.7  # v0.569：近距矮峰补回须峰间谷底 ≤0.7×较矮峰（v0.568 不限深度，5% 抖动也切波，owner 标注 50→47/51）


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
        pk_set = {
            k
            for k in range(m)
            if a[k] > 0 and a[k] == a[max(0, k - f) : k + f + 1].max()
        }
        # 左右 2 根窗会吞掉靠近大峰的矮峰；峰间谷底 ≤ MACD2_WAVE_VALLEY × 较矮峰（真回落）才补回，小抖动不切波。
        for k in range(1, m - 1):
            if k in pk_set or a[k] <= 0:
                continue
            if not (
                a[k] >= a[k - 1]
                and a[k] >= a[k + 1]
                and (a[k] > a[k - 1] or a[k] > a[k + 1])
            ):
                continue
            for prev in tuple(pk_set):
                lo, hi = sorted((prev, k))
                if not 2 <= hi - lo <= f:
                    continue
                if a[lo + 1 : hi].min() <= MACD2_WAVE_VALLEY * min(a[lo], a[hi]):
                    pk_set.add(k)
                    break
        pk = sorted(pk_set)
        merged: list[int] = []
        for k in pk:
            # 只合并连续同值平台峰；近距两峰之间已有谷底时必须保留，否则会把两波并成一波。
            if merged:
                prev = merged[-1]
                if a[k] == a[prev] and np.all(a[prev : k + 1] == a[k]):
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
    return macd_divergence_v2_arrays(close, hist_s.to_numpy())


def macd_divergence_v2_arrays(close, hist) -> dict[str, Any]:
    """背离 v2 的数组版（MACD 背离**全项目唯一判定**，v0.594）：``close`` / ``hist`` 同长、末根 = as-of 当根。
    板块相位、QN ADX、W 底的背离腿都调这里（传预先算好的整段柱子切片，避免逐日重算 MACD）。"""
    import numpy as np  # noqa: PLC0415

    close = np.asarray(close, dtype=float)
    hist = np.asarray(hist, dtype=float)
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
        h2 = macd_series(c)[2]  # v0.594：MACD 唯一实现
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
      owner 标注 7 张错 5 张已停用；v0.594 起 v1 代码与 ``*_v1`` 字段删除（分型摆点去重）。
    - overextended 乖离过大（v0.571 owner 定义）：双线还在上涨，红柱滞涨或下降，且双线已超过红柱范围。

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
    dif, dea, hist = macd_series(
        close_s
    )  # v0.594：MACD 唯一实现（原本地 ema 重算，同式）
    d, h = dif.to_numpy(), hist.to_numpy()

    # 区间状态机
    dif_last, dea_last = float(dif.iloc[-1]), float(dea.iloc[-1])
    h0, h1 = float(h[-1]), float(h[-2])
    zone, zone1_restart = _macd_zone_state(dif_last, dea_last, h0, h1)

    v2 = check_macd_divergence_v2(df)  # v0.567 live 口径
    top_div = v2.get("top_divergence") or {"hit": False}
    three_peaks = v2.get("three_peaks") or {"hit": False}
    bottom_div = v2.get("bottom_divergence") or {"hit": False}

    # 乖离过大（owner 2026-11-24）：双线上涨 + 红柱滞涨 / 下降 + 双线超过红柱
    overextended = _macd_overextended(d, dea.to_numpy(), h)

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


# v0.223（TODO #67 B4）：规范入口别名（同对象）。live 的 check_macd_technics(
# df, df_long=df_long) 带上下文参数，调用面不变（见设计稿勘误）。
detect = check_macd_technics
