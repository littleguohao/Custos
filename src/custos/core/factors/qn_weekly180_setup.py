# -*- coding: utf-8 -*-
"""QN·周线翻倍·180 周线大悬空（骑牛登山体系，规则出处
`governance/strategy/qn/07_doubling_swing.md` §一）。

源规则（经验规律，未回测）——四要素：
1. **大悬空**：股价长期在 180 周均线下方运行，跌幅超 50% 且时间跨度足够长；
2. **脚踩巨量**：180 周线下方出现相对历史巨量的量能柱（洗盘/建仓动作）；
3. **放量突破** 180 周均线及平台整理区（180 标杆）；
4. MACD 出现肉眼可识别标杆（金叉或零轴上方走强）。

确定性转译（待回测；全部在 resample("W-FRI") 周线上判定，含进行中部分周）：
- 大悬空 = 近 ``QN_BELOW_BARS`` 根周 K 收盘均在 180 周线下方，且
  相对历史最高收盘回撤 ≥ ``QN_DRAWDOWN_PCT``（50%）
- 脚踩巨量 = 悬空期内存在周量 ≥ 此前全部周量均值 ×``QN_HUGE_VOL`` 的量柱
- 突破 = 当周收盘首次站上 180 周线，且当周量 ≥ 前周 ×``QN_BREAK_SURGE``
- MACD 标杆 = 周线 DIF>DEA（金叉态）或 DIF>0（零轴上）

⚠️ 数据要求苛刻：180 周 ≈ 3.5 年（~840 交易日），`min_bars=900`；
数据不足的标的 available=False 如实标注（源规则亦言「潜伏期漫长」）。

pattern 类；绝不 raise。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from custos.core.indicators import macd_series, resample

FACTOR: dict[str, Any] = {
    "id": "qn_weekly180_setup",
    "name": "QN·周线翻倍 180 周线大悬空",
    "kind": "pattern",
    "status": "untested",  # 新实现未回测（骑牛体系口径 + 合成用例）
    "evidence": "",
    "note": "规则出处 governance/strategy/qn/07_doubling_swing.md §一；大悬空（长期低于180周线+回撤≥50%）+ 脚踩巨量 + 放量突破180线 + 周线MACD标杆；数据需求 ~900 根日 K",
    "min_bars": 900,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数 ----
QN_MA_WEEK = 180  # 待回测：源规则 180 周均线
QN_BELOW_BARS = 52  # 待回测：大悬空=近 N 根周 K 全在线下（≈1 年，「时间跨度足够长」）
QN_DRAWDOWN_PCT = 50.0  # 待回测：相对历史最高收盘的最小回撤%（源规则「跌幅超 50%」）
QN_HUGE_VOL = 3.0  # 待回测：脚踩巨量 = 悬空期内周量 ≥ 此前均量 ×此值
QN_BREAK_SURGE = 1.5  # 待回测：突破周放量倍数（相对前周）


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """resample 前置：date 转日期型（to_datetime 幂等）+ 补 amount 列。"""
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"])
    if "amount" not in x.columns:
        x["amount"] = x["close"].astype(float) * x["volume"].astype(float)
    return x


def _legs(close, vol, ma180, dv: float, ev: float) -> tuple[dict[str, Any], int]:
    """四腿判定（两路共享的唯一实现）：输入为 as-of 周线数组
    （close/vol/ma180 等长、末根可为进行中部分周）与周 MACD 末点。"""
    nw = len(close)
    # 腿① 大悬空：突破前的连续线下周数 ≥ QN_BELOW_BARS（从倒数第 2 周向前数，
    # 突破周本身不计）+ 悬空期最低周收相对历史最高周收回撤 ≥50%
    # （回撤量的是「悬空底部 vs 历史峰」——突破当下价格已修复，不能量当前）
    run = 0
    for i in range(nw - 2, QN_MA_WEEK - 1, -1):
        if ma180[i] == ma180[i] and close[i] < ma180[i]:
            run += 1
        else:
            break
    below = run >= QN_BELOW_BARS
    susp_low = close[-run - 1 : -1].min() if run else close[-2]
    dd = (1 - float(susp_low) / float(close[:-1].max())) * 100
    leg_susp = {
        "hit": bool(below and dd >= QN_DRAWDOWN_PCT),
        "below_run_weeks": run,
        "below_all": bool(below),
        "drawdown_pct": round(dd, 2),
    }
    # 腿② 脚踩巨量：悬空期（倒数 run+1 根到倒数第 2 根）内存在
    # ≥ 全部周量均值 ×QN_HUGE_VOL 的量柱
    vol_ma = float(vol[:-1].mean())
    susp_vol_max = float(vol[-run - 1 : -1].max()) if run else float(vol[-2])
    leg_huge = {
        "hit": bool(vol_ma and susp_vol_max >= vol_ma * QN_HUGE_VOL),
        "huge_vol_mult": QN_HUGE_VOL,
        "vol_max_in_suspension": round(susp_vol_max, 1),
        "vol_ma_all": round(vol_ma, 1),
    }
    # 腿③ 突破：前周收 < 线、当周收 ≥ 线，且当周量 ≥ 前周 ×QN_BREAK_SURGE
    cross_up = bool(close[-2] < ma180[-2] and close[-1] >= ma180[-1])
    surge = bool(vol[-2] and vol[-1] >= vol[-2] * QN_BREAK_SURGE)
    leg_break = {
        "hit": bool(cross_up and surge),
        "cross_up": cross_up,
        "vol_surge": surge,
        "vol_ratio": round(float(vol[-1] / vol[-2]), 3) if vol[-2] else None,
    }
    # 腿④ MACD 标杆：周线 DIF>DEA 或 DIF>0
    leg_macd = {
        "hit": bool(dv > ev or dv > 0),
        "dif_gt_dea": bool(dv > ev),
        "dif_pos": bool(dv > 0),
    }
    legs = {
        "suspension": leg_susp,
        "huge_vol": leg_huge,
        "breakout": leg_break,
        "macd_mark": leg_macd,
    }
    return legs, nw


def detect(df, code: str = "", _arr: dict | None = None) -> dict[str, Any]:
    """180 周线大悬空四要素。绝不 raise。

    ``_arr``：研究侧预计算序列（close/day_w/weekly_close/weekly_vol/
    weekly_part_vol/weekly_ma180（含部分周收盘的 as-of 值）/weekly_ma180_nat
    （自然周序列）/weekly_dif/weekly_dea），给定时不读 df 任何列。
    as-of 周线框 = 完整周 0..w-1 + 部分周（close=当日收盘、量=周内累计），
    与慢路径的前缀 resample("W-FRI") 逐位一致（见 backtest_factors
    _weekly_ma180_asof / _weekly_macd_step 的复刻口径）。

    返回键：
        hit        四要素全中（突破发生在当周）
        legs       suspension / huge_vol / breakout / macd_mark 明细
        weekly_bars 实际周线根数（数据覆盖自查）
    """
    try:
        n = len(df)
        if n < FACTOR["min_bars"]:
            return {
                "available": False,
                "hit": False,
                "reason": f"少于{FACTOR['min_bars']}根日K（{n}）",
            }
        if _arr is None:
            wk = resample(_prepare(df), "W-FRI")
            nw = len(wk)
            if nw < QN_MA_WEEK + 2:
                return {
                    "available": False,
                    "hit": False,
                    "reason": f"周线不足{QN_MA_WEEK + 2}根（{nw}）",
                }
            close = wk["close"].astype(float).to_numpy()
            vol = wk["volume"].astype(float).to_numpy()
            ma180 = wk["close"].astype(float).rolling(QN_MA_WEEK).mean().to_numpy()
            dif, dea, _h = macd_series(wk["close"])
            dv, ev = float(dif.iloc[-1]), float(dea.iloc[-1])
        else:
            import numpy as np

            i = n - 1
            w = int(_arr["day_w"][i])
            nw = w + 1
            if nw < QN_MA_WEEK + 2:
                return {
                    "available": False,
                    "hit": False,
                    "reason": f"周线不足{QN_MA_WEEK + 2}根（{nw}）",
                }
            close = np.append(_arr["weekly_close"][:w], _arr["close"][i])
            vol = np.append(_arr["weekly_vol"][:w], _arr["weekly_part_vol"][i])
            ma180 = np.append(_arr["weekly_ma180_nat"][:w], _arr["weekly_ma180"][i])
            dv = float(_arr["weekly_dif"][i])
            ev = float(_arr["weekly_dea"][i])
        legs, nw = _legs(close, vol, ma180, dv, ev)
        return {
            "available": True,
            "hit": bool(all(leg["hit"] for leg in legs.values())),
            "weekly_bars": nw,
            "legs": legs,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
