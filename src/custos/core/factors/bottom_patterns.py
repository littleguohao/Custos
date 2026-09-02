# -*- coding: utf-8 -*-
"""底部识别形态（25chuhuo 讲义底部侧，2026-08-13 owner 指示）——W 底与红肥绿瘦。

出货五方式（`distribution.py`）是**顶部**侧；本模块是它的底部镜像。
两个检测器：

- **W 底**（`detect_w_bottom`）：双底结构 + 底部区域 + 底部放量 + MACD 底背离
  四腿合成确认。双底结构镜像 `distribution.double_top_vol_bear` 的双顶容差
  （分型窗口与容差同构反向）；底背离口径与 `enrich.check_macd_technics` 的
  bottom_divergence 一致（两个收盘摆低 L2<L1、DIF 低点抬高——本模块用 L0 的
  `indicators.macd_series` 自重算：分层上 factors 不得 import pipeline/screening，
  分型判定逻辑如两边漂移需收敛）。
- **红肥绿瘦**（`detect_red_fat_green_thin`）：底部区间阳线占优，两个维度都要
  （讲义原话「面积和数量」）——①数量（阳多阴少）②面积（阳实体/阳量大于
  阴实体/阴量）。镜像顶部「绿肥红瘦」。

**证据层**（阶段 A/B 定案口径）：只落盘/展示，不进技术分/分层/gate。
阈值全部标「待回测」。两个函数绝不 raise。
"""

from __future__ import annotations

from typing import Any

from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays  # noqa: E402
from custos.core.indicators import macd_series  # noqa: E402  DIF/DEA 唯一实现

FACTOR: dict[str, Any] = {
    "id": "bottom_patterns",
    "name": "底部识别形态（W 底 / 红肥绿瘦）",
    "kind": "pattern",
    "status": "untested",  # 新实现未回测（25chuhuo 讲义口径 + 合成用例）
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "W 底（双底+底部放量+MACD 底背离合成）与红肥绿瘦（数量+面积两维）——25chuhuo 讲义底部侧，distribution 的底部镜像；证据层。⚠️ 2026-08-20 审计补记：同模块 `bull_bear_volume` 的产出 volume_yy 是技术分打分腿（阳量>阴量 +7 / 阴量>阳量 −5，v0.58/v0.61 owner 定向），live_use=evidence_only 仅覆盖 W 底/红肥绿瘦两检测器——注册表粒度是模块级，打分腿的准确状态见 #62 因子化缺口",
    "min_bars": 60,
    "live_use": "evidence_only",  # 证据层（阶段 A/B 定案口径）：W 底/红肥绿瘦只落盘展示；⚠️ volume_yy 例外（打分腿，见 note）
    "stage": "release",  # enrich 引用（证据字段 + volume_yy 打分腿）
}

# ---- W 底（待回测参数；镜像出货侧同义常量的取值口径）----
WBOT_TOL_PCT = 3.0  # 待回测：双底两低点相近容差%（镜像双顶 DIST_DOUBLE_TOP_TOL）
WBOT_MIN_GAP = 3  # 待回测：两底最少间隔根数（镜像双顶 p2-p1>=3）
WBOT_FRACTAL = 2  # 待回测：分型左右确认根数（镜像双顶的 2 左 2 右窗口）
WBOT_WINDOW = 20  # 待回测：双底观察窗口（近 N 根，镜像 DIST_TOP_WINDOW×2）
WBOT_DRAWDOWN_PCT = (
    40.0  # 待回测：底部区域=距 250 日高点回撤≥此值（同底部巨量 low_price 口径）
)
WBOT_VOL_RATIO = 2.0  # 待回测：底部放量=量 ≥ 250 日均量×此值（同 check_bottom_volume BOTTOM_VOL_RATIO）
WBOT_VOL_WIN = 5  # 待回测：第二底之后几根内出现放量（含当日）

# ---- 红肥绿瘦（待回测参数；镜像绿肥红瘦）----
RF_WINDOW = 10  # 待回测：底部区间窗口（镜像 DIST_TOP_WINDOW）
RF_BOTTOM_FRAC = 1.02  # 待回测：底部=窗口最低 ≤ 近60根最低×此值（镜像 near_top 0.98）


def _wbot_leg_double_bottom(low, n: int, w0: int, f: int) -> dict[str, Any]:
    """腿① 双底：镜像 double_top 的分型结构——窗口内摆动低点（WBOT_FRACTAL 左右
    确认的局部最低，且窗口内唯一），取最近两个，容差 WBOT_TOL_PCT、间隔 ≥ WBOT_MIN_GAP"""
    troughs = [
        i
        for i in range(w0 + f, n - f)
        if low[i] == low[i - f : i + f + 1].min()
        and float((low[i - f : i + f + 1] == low[i]).sum()) == 1
    ]
    leg_double: dict[str, Any] = {"hit": False}
    b1 = b2 = None
    if len(troughs) >= 2:
        b2 = troughs[-1]
        # 2026-08-16 review 修复：b1 取「与 b2 价位最接近」的前谷（双底配对的
        # 本义），此前取 max(low)=此前**最浅**的谷——教科书 W 底（左深右浅/
        # 等深）会跳过一个无关紧要的小谷去比容差，3% 容差腿系统性假阴性。
        b1 = min(troughs[:-1], key=lambda i: abs(low[i] / low[b2] - 1), default=None)
        if b1 is not None and b2 - b1 >= WBOT_MIN_GAP:
            gap_pct = abs(low[b1] / low[b2] - 1) * 100 if low[b2] else 999.0
            leg_double = {
                "hit": bool(gap_pct <= WBOT_TOL_PCT),
                "bottom1_bars_ago": n - 1 - b1,
                "bottom2_bars_ago": n - 1 - b2,
                "bottoms_gap_pct": round(gap_pct, 2),
            }
    return leg_double


def _wbot_leg_bottom_zone(close, high, n: int) -> dict[str, Any]:
    """腿② 底部区域：距 250 日高点回撤 ≥ WBOT_DRAWDOWN_PCT（同底部巨量 low_price 口径）

    ⚠️ 2026-08-16 review 修复（口径如实标注）：n<250 时 high[-250:]/vol[-250:]
    实际取的是**全部已加载根数**——detail 落 window_bars 供判读，不再让
    60 根均量冒充「250 日均量」而不留痕。
    """
    window_bars = min(n, 250)
    high_250 = float(high[-250:].max()) if n else 0.0
    dd = (1 - close[-1] / high_250) * 100 if high_250 else 0.0
    return {
        "hit": bool(dd >= WBOT_DRAWDOWN_PCT),
        "drawdown_from_250d_high_pct": round(dd, 2),
        "window_bars": window_bars,
    }


def _wbot_leg_bottom_volume(low, vol, n: int, window_bars: int) -> dict[str, Any]:
    """腿③ 底部放量：最近 WBOT_VOL_WIN 根内存在「量 ≥ 250日均量×WBOT_VOL_RATIO
    且不创新低（当日最低 ≥ 此前 20 日最低）」的 K（同 check_bottom_volume 阈值）"""
    vol_ma250 = float(vol[-250:].mean()) if n else 0.0
    vol_hits = []
    for t in range(max(21, n - WBOT_VOL_WIN), n):
        huge = bool(vol_ma250) and vol[t] >= vol_ma250 * WBOT_VOL_RATIO
        low20 = float(low[t - 20 : t].min())
        if huge and low[t] >= low20:
            vol_hits.append(
                {
                    "bars_ago": n - 1 - t,
                    "vol_ratio_vs_ma250": round(float(vol[t] / vol_ma250), 3),
                }
            )
    return {
        "hit": bool(vol_hits),
        "hits": vol_hits,
        "vol_ma250": round(vol_ma250, 1) if vol_ma250 else None,
        "window_bars": window_bars,  # <250 时该均量是全部已加载根数的近似
    }


def _wbot_leg_macd_divergence(df, close, n: int, w0: int, f: int) -> dict[str, Any]:
    """腿④ MACD 底背离：两个收盘摆低 L2<L1（分型口径同①），DIF 低点抬高
    （口径同 enrich.check_macd_technics 的 bottom_divergence；本模块用 L0 的
    macd_series 自重算——factors 不得 import pipeline/screening）。"""
    leg_div: dict[str, Any] = {"hit": False}
    dif, _dea, _h = macd_series(df["close"])
    d = dif.to_numpy()
    swing_lo = [
        i
        for i in range(w0 + f, n - f)
        if close[i] == close[i - f : i + f + 1].min()
        and (close[i - f : i + f + 1] > close[i]).sum() >= 2 * f - 1
    ]
    if len(swing_lo) >= 2:
        a, b = swing_lo[-2], swing_lo[-1]
        if close[b] < close[a] and d[b] > d[a]:
            leg_div = {
                "hit": True,
                "close_a": round(float(close[a]), 4),
                "close_b": round(float(close[b]), 4),
                "dif_a": round(float(d[a]), 4),
                "dif_b": round(float(d[b]), 4),
            }
    return leg_div


def detect_w_bottom(df, code: str = "") -> dict[str, Any]:
    """W 底：双底 + 底部区域 + 底部放量 + MACD 底背离 四腿合成。绝不 raise。

    四腿单独落 detail（供回测消融）；hit=四腿全中。
    ⚠️ 「250 日」窗口在 n<250 时按全部已加载根数近似，detail 落 window_bars
    如实标注（2026-08-16 review 修复）；需要严格 250 日口径的调用方请先保证
    输入 ≥250 根。
    """
    try:
        close, high, low, vol = _ohlcv_arrays(df)
        n = len(df)
        if n < 60:
            return {
                "available": False,
                "hit": False,
                "reason": f"少于60根K线（{n}）",
            }

        w0 = max(0, n - WBOT_WINDOW * 2)
        f = WBOT_FRACTAL
        leg_double = _wbot_leg_double_bottom(low, n, w0, f)
        leg_zone = _wbot_leg_bottom_zone(close, high, n)
        leg_volume = _wbot_leg_bottom_volume(low, vol, n, leg_zone["window_bars"])
        leg_div = _wbot_leg_macd_divergence(df, close, n, w0, f)

        hit = bool(
            leg_double["hit"]
            and leg_zone["hit"]
            and leg_volume["hit"]
            and leg_div["hit"]
        )
        return {
            "available": True,
            "hit": hit,
            "legs": {
                "double_bottom": leg_double,
                "bottom_zone": leg_zone,
                "bottom_volume": leg_volume,
                "macd_bottom_divergence": leg_div,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }


def _rf_count_area(close, open_, vol, seg) -> dict[str, Any]:
    """红肥绿瘦窗口内的数量/面积两维统计：①数量（阳多阴少）②面积（阳实体/
    阳量均值 > 阴实体/阴量均值）。"""
    bulls = [t for t in seg if close[t] > open_[t]]
    bears = [t for t in seg if close[t] < open_[t]]
    bull_bodies = [abs(close[t] / open_[t] - 1) * 100 for t in bulls if open_[t]]
    bear_bodies = [abs(close[t] / open_[t] - 1) * 100 for t in bears if open_[t]]
    count_hit = bool(bulls and bears and len(bulls) > len(bears))
    area_hit = bool(
        bull_bodies
        and bear_bodies
        and sum(bull_bodies) / len(bull_bodies) > sum(bear_bodies) / len(bear_bodies)
        and sum(vol[t] for t in bulls) / len(bulls)
        > sum(vol[t] for t in bears) / len(bears)
    )
    return {
        "bulls": bulls,
        "bears": bears,
        "bull_bodies": bull_bodies,
        "bear_bodies": bear_bodies,
        "count_hit": count_hit,
        "area_hit": area_hit,
    }


def detect_red_fat_green_thin(df, code: str = "") -> dict[str, Any]:
    """红肥绿瘦（底部镜像绿肥红瘦）：底部区间阳线占优——数量与面积**两维都要**。

    ①数量：窗口内阳线数 > 阴线数；②面积：阳实体均值 > 阴实体均值 且
    阳量均值 > 阴量均值。底部=窗口最低 ≤ 近 60 根最低 × ``RF_BOTTOM_FRAC``
    （镜像顶部的 near_top 0.98 口径）。绝不 raise。

    ⚠️ 短样本口径（2026-08-16 review 标注）：本函数 n≥30 即 available（FACTOR
    元数据的 min_bars=60 是 W 底的门槛）；n<60 时 near_bottom 恒 True（底部
    位置判据空转），detail 落 near_bottom_degenerated 如实标注。
    """
    try:
        close, high, low, vol = _ohlcv_arrays(df)
        open_ = df["open"].astype(float).to_numpy()
        n = len(df)
        if n < 30:
            return {"available": False, "hit": False, "reason": f"少于30根K线（{n}）"}
        seg = range(n - RF_WINDOW, n)
        near_bottom = (
            True
            if n < 60
            else low[-RF_WINDOW:].min() <= low[-60:].min() * RF_BOTTOM_FRAC
        )
        st = _rf_count_area(close, open_, vol, seg)
        bulls = st["bulls"]
        bears = st["bears"]
        bull_bodies = st["bull_bodies"]
        bear_bodies = st["bear_bodies"]
        count_hit = st["count_hit"]
        area_hit = st["area_hit"]
        return {
            "available": True,
            "hit": bool(near_bottom and count_hit and area_hit),
            "near_bottom": bool(near_bottom),
            "near_bottom_degenerated": bool(n < 60),  # 短样本时底部判据空转，如实标注
            "detail": {
                "bull_count": len(bulls),
                "bear_count": len(bears),
                "count_hit": count_hit,
                "bull_body_mean_pct": round(sum(bull_bodies) / len(bull_bodies), 3)
                if bull_bodies
                else None,
                "bear_body_mean_pct": round(sum(bear_bodies) / len(bear_bodies), 3)
                if bear_bodies
                else None,
                "area_hit": area_hit,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }


def bull_bear_volume(df, window: int = 10) -> dict[str, Any]:
    """近 ``window`` 根 K 线的阳量/阴量总量对比（2026-08-14，v0.58，owner）。

    与红肥绿瘦（``detect_red_fat_green_thin``）的区别：那里是**底部区间**语义
    （near_bottom 门槛 + 数量/面积两维合成），这里是**中性窗口**的纯量能对比——
    不看位置、不看实体，只回答「最近 10 根里买量还是卖量占上风」，供技术分
    加/减分用（阳量>阴量 +5 / 阴量>阳量 −5）。平盘（收=开）两边都不计。绝不 raise。
    """
    try:
        close, _, _, vol = _ohlcv_arrays(df)
        open_ = df["open"].astype(float).to_numpy()
        n = len(df)
        if n < window:
            return {
                "available": False,
                "reason": f"少于{window}根K线（{n}）",
            }
        bull_vol = sum(vol[t] for t in range(n - window, n) if close[t] > open_[t])
        bear_vol = sum(vol[t] for t in range(n - window, n) if close[t] < open_[t])
        if bull_vol == 0 and bear_vol == 0:
            return {"available": False, "reason": "窗口内全平盘/无量"}
        return {
            "available": True,
            "window": window,
            "bull_vol": round(float(bull_vol), 2),
            "bear_vol": round(float(bear_vol), 2),
            "bull_gt_bear": bool(bull_vol > bear_vol),
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}:{str(exc)[:80]}"}
