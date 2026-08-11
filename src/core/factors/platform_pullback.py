# -*- coding: utf-8 -*-
"""平台突破回踩(平台不破)形态检测器。

结构(以永兴材料 002756 为原型,2026-01~03):
  ① 平台:回看窗内 ≥2 个相近摆动高点(容差 3%),构成震荡上沿;
  ② 突破:其后某日**收盘有效站上**平台高(≥+1%),且其后最高收盘离开 ≥+5%(真离开);
  ③ 回踩不破:最近若干日最低**回落到平台高附近**(≤+6%)但**未有效跌破**
    (最低 ≥ 平台高×(1-2%)),当日收盘守在平台高之上(≥×0.98)。
选稳器(可选):当日 J<20 或 缩量(量比≤70%)——企稳而非下杀中继。

as-of 严格(只用当日及以前数据);绝不 raise。参数尽量少(防过拟合),阈值待跨年验证。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import pandas as pd

FACTOR: dict[str, Any] = {
    "id": "platform_pullback",
    "name": "平台突破回踩",
    "kind": "pattern",
    "status": "needs_work",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "R2：识别有术、盈利无效；净值 3 窗方向随环境摆动。证据层保留（候选表「平台回踩」列）",
    "min_bars": 60,
    "live_use": "evidence_only",
    "stage": "release",
}


_TOOLS = Path(__file__).resolve().parents[1]
for _bp in (_TOOLS, _TOOLS.parent / "core"):  # core/: paths 等 L0 模块
    if str(_bp) not in sys.path:
        sys.path.insert(0, str(_bp))   # indicators 在 src 根；__main__ 段还用 local_tdx
# 原先的 screening 一项已于 2026-08-08 删除：本模块只依赖 src 根
# （因子层惯例：sys.path 由消费方设置，见 factors/__init__.py）。


from indicators import j_series as _j_canonical  # noqa: E402
PLATFORM_LOOKBACK = 60      # 平台回看窗
BREAKOUT_WITHIN = 40        # 突破须发生在近 40 日内
PULLBACK_WITHIN = 15        # 回踩发生在近 15 日内
TOUCH_TOL = 0.03            # 平台高点容差(相近高点)
BREAKOUT_BUF = 0.01         # 收盘站上平台高的缓冲
LEAVE_MIN = 0.05            # 突破后最高收盘至少离开平台高的幅度
NEAR_TOL = 0.06             # 回踩低点距平台高的"附近"上界
BREAK_TOL = 0.02            # 允许刺破深度(不破=不低于 ×(1-2%))


def detect_platform_pullback(df: pd.DataFrame,
                             lookback: int = PLATFORM_LOOKBACK,
                             breakout_within: int = BREAKOUT_WITHIN,
                             pullback_within: int = PULLBACK_WITHIN,
                             touch_tol: float = TOUCH_TOL,
                             touch_min: int = 2,
                             zone_ratio: float = 1.6,
                             near_tol: float = NEAR_TOL,
                             break_tol: float = BREAK_TOL,
                             top_vol_mult: float = 0.0,
                             stabilize: bool = False) -> Optional[dict[str, Any]]:
    """检测"平台突破→回踩不破"形态,命中返回形态细节,否则 None。绝不 raise。

    输入 df:截至当日的日线(升序,含 date/open/high/low/close/volume)。
    参数(可扫):touch_tol 上沿触碰容差;touch_min 最少触碰次数(1=单高点也算);
    zone_ratio 平台区振幅上限(ph/zone_min);near_tol 回踩"附近"上界;break_tol 允许刺破深度;
    top_vol_mult>0 时启用**顶部放量出货过滤**(突破后最高收盘日量≥该倍数×20日均量→排除)。
    """
    try:
        n = len(df)
        if n < lookback + 5:
            return None
        close = df["close"].astype(float).values
        high = df["high"].astype(float).values
        low = df["low"].astype(float).values
        vol = df["volume"].astype(float).values

        # ① 平台:回看 [n-lookback-breakout_within, n-breakout_within) 的上沿
        p_lo = max(0, n - lookback - breakout_within)
        p_hi = n - breakout_within
        if p_hi - p_lo < 10:
            return None
        ph = float(high[p_lo:p_hi].max())                    # 平台高
        ph_idx = p_lo + int(high[p_lo:p_hi].argmax())
        touches = int((high[p_lo:p_hi] >= ph * (1 - touch_tol)).sum())
        if touches < touch_min:                              # 上沿触碰次数(1=单高点即可)
            return None
        zone_min = float(close[p_lo:p_hi].min())
        if zone_min <= 0 or ph / zone_min > zone_ratio:      # 非震荡(单边趋势区不算平台)
            return None

        # ② 突破:近 breakout_within 日内某日收盘站上 ph×(1+buf),且其后最高收盘 ≥ ph×(1+LEAVE_MIN)
        b_lo = p_hi
        brk_idx = None
        for j in range(b_lo, n):
            if close[j] > ph * (1 + BREAKOUT_BUF):
                brk_idx = j
                break
        if brk_idx is None:
            return None
        if float(close[brk_idx:].max()) < ph * (1 + LEAVE_MIN):
            return None                                       # 没有真离开,只是擦边

        # ③ 回踩:突破后曾离开,最近 pullback_within 日低点回到平台附近但未破
        q_lo = max(brk_idx + 1, n - pullback_within)
        if q_lo >= n:
            return None
        pb_low = float(low[q_lo:].min())
        if not (ph * (1 - break_tol) <= pb_low <= ph * (1 + near_tol)):
            return None
        if close[-1] < ph * (1 - break_tol):                  # 当日收盘必须守在平台高上
            return None
        # ③b 顶部放量出货过滤(可选):突破后最高收盘日若放天量(≥top_vol_mult×20日均量)
        # ——天量见天价=主力借突破出货的假突破,排除;永兴材料式缩量/平量上涨才保留
        if top_vol_mult > 0:
            top_i = brk_idx + int(close[brk_idx:].argmax())
            vma20 = vol[max(brk_idx, top_i - 20):top_i].mean() if top_i > brk_idx else 0.0
            if vma20 > 0 and vol[top_i] / vma20 >= top_vol_mult:
                return None
        # 回踩须真的"回过高位附近"(当前离平台高不远,否则形态已走样)
        if close[-1] > ph * 1.5:
            return None

        out: dict[str, Any] = {"platform_high": round(ph, 3), "touches": touches,
                               "breakout_date": str(df["date"].iloc[brk_idx])[:10],
                               "pullback_low": round(pb_low, 3),
                               "close": round(float(close[-1]), 3),
                               "ph_date": str(df["date"].iloc[ph_idx])[:10]}
        if stabilize:                                         # 可选企稳过滤
            vma5 = vol[-6:-1].mean() if len(vol) >= 6 else 0.0
            out["vol_shrink"] = bool(vma5 > 0 and vol[-1] / vma5 <= 0.7)
            # ⚠️ 2026-08-07 破环：这里原本惰性 `import backtest_factors as bt`
            # 只为拿 `bt._kdj` —— 而 `bt._kdj` 就是 `technical_monitor.kdj`
            # （backtest_factors:64 `from technical_monitor import kdj as _kdj`）。
            # 代价是 import 一个 1959 行、连带 40+ 模块的回测器，且构成
            # `factors/ → screening/ → factors/` 的环。
            # 这里只需要 J 的**数值**，直接用底层 `indicators.j_series`
            # （与 b1_pullback_fit / main_rally_factor / b2_surge_factor 同一路径）。
            j_series_ = _j_canonical(df)
            j_val = None
            if j_series_ is not None and len(j_series_):
                last = j_series_.iloc[-1]
                j_val = float(last) if last == last else None
            out["j"] = j_val
            out["stabilized"] = bool(out["vol_shrink"] or (j_val is not None and j_val < 20))
        return out
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    sys.path.insert(0, str(_TOOLS.parent / "datasource" / "local_tdx"))
    import local_tdx_data  # noqa: E402

    df = local_tdx_data.get_ohlcv_table("002756", count=200)
    df["date"] = df["date"].astype(str).str[:10]
    df = df.sort_values("date").reset_index(drop=True)
    # 逐日 as-of 扫描:哪些天命中
    hits = []
    for i in range(80, len(df)):
        r = detect_platform_pullback(df.iloc[:i + 1], stabilize=True)
        if r:
            d = str(df["date"].iloc[i])[:10]
            if not hits or hits[-1]["date"] != d:
                hits.append({"date": d, **r})
    for h in hits[:15]:
        print(h)
    print(f"\n002756 命中 {len(hits)} 天")
