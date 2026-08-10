# -*- coding: utf-8 -*-
"""「主升始发点」因子（来源：微信文章的通达信公式，2026-08-04）。

原文核心逻辑：资金流入占比 + 超卖金叉 + 乖离率极度偏离 = 主升始发点。逐条识别源码后
发现它其实是**四个标准指标的组合**，其中两个我们已有、两个是新增::

    D5  = SMA(MAX(C-REF(C,1),0),7,1)/SMA(ABS(C-REF(C,1)),7,1)*100   → 标准 RSI(7)
    D11 = 3*SMA(RSV9,3,1) - 2*SMA(SMA(RSV9,3,1),3,1)                → **就是 KDJ 的 J 值**
    偏差 = ((H+L+C)/3 - MA(TP,14)) / (0.015*AVEDEV(TP,14))           → **标准 CCI(14)**
    主升 = D1/(D1+D2)，D1=15日高点抬升累计、D2=15日低点下降累计       → 基于高低点的趋势强度

已有：J 值（同口径）、J 拐头（s_reversal 的 j_turn_up）、RSI（rsi_state 新加）。
新增：**CCI(14)** 与 **资金流入占比**（基于高低点，与基于收盘的 RSI 互补）。

⚠️ **原文有两处矛盾，实现时都保留两种口径由回测判定**：

1. `D3 := CROSS(0.8, 主升)` —— 通达信 `CROSS(A,B)` 是 A 上穿 B，所以这是"常数 0.8 上穿
   主升线"＝**主升线跌破 0.8**；而文字说"上涨占比突破 80%…主力资金大规模流入"，那该写
   `CROSS(主升, 0.8)`。两者语义相反。从整体逻辑看源码更可信：其余三条（RSI7<20、
   CCI<-100、J拐头）都是**极度超卖**，那种状态下"占比刚从 80% 上方跌破"合理，而"突破
   80%"（强势）与极度超卖并存几乎不可能。故 ``cross_mode`` 参数化，默认 "below"（源码口径）。
2. 文章称"4 大核心条件"，源码只有 3 个（`选股:=风控 AND D3 AND D6 AND T1`）——
   第 4 条"突破前期关键压力位且成交量放大"**在源码里不存在**。此处不擅自补，
   需要时可叠加已有的 `platform_pullback`。

阈值全部沿用原文并**待回测**：占比 0.8、RSI7<20、CCI<-100、去重窗 20 日。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from indicators import avedev, cci, rsi  # noqa: E402  指标唯一实现
from numpy.lib.stride_tricks import sliding_window_view

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))   # indicators 在 07_tools 根
# 原先的 screening/market_timing 两项已于 2026-08-08 删除：本模块只依赖
# 07_tools 根与同目录（`from rsi_state import rsi`，扁平 import 惯例见 factors/__init__.py）。

from indicators import j_series as _j_series  # noqa: E402


FACTOR: dict[str, Any] = {
    "id": "main_rally_factor",
    "name": "主升始发点（H4）",
    "kind": "pattern",
    "status": "untested",
    "evidence": "00_governance/research/R8_hypothesis_H3_H4_pending.md",
    "note": "R8：已实现未跑；原文两处矛盾，两种 CROSS 口径都实现由回测判定",
    "min_bars": 60,
    "live_use": "evidence_only",  # signal_labels 出标签落候选表；该模块头部已声明「标注不是交易依据」
    "stage": "release",
}


# ---- 原文参数（待回测）----
FLOW_WIN = 15                # D1/D2 的统计窗（原文 15；文章说想更灵敏可改 10）
FLOW_THRESHOLD = 0.8         # 主升占比阈值（原文 0.8；想过滤弱势反弹可上调 0.85）
RSI_N = 7                    # 原文 D5 用 7 日
RSI_OVERSOLD = 20.0          # 原文 REF(D5,1)<20（震荡市可放宽到 30）
CCI_N = 14                   # 原文 AVEDEV/MA 都用 14
CCI_EXTREME = -100.0         # 原文 偏差<-100（想提高安全边际可下调到 -120）
DEDUP_WIN = 20               # 原文 FILTER(基础信号,20)：20 天内只取第一次
MAIN_RALLY_MIN_BARS = 60     # 原文 BARSCOUNT(C)>60


def flow_ratio(df: pd.DataFrame, win: int = FLOW_WIN) -> pd.Series:
    """资金流入占比 = D1/(D1+D2)。

    D1 = win 日内**高点抬高**的累计幅度；D2 = win 日内**低点降低**的累计幅度。
    与基于收盘的 RSI 互补——它衡量的是区间（高低点）在往哪边移动。
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    up = (high - high.shift(1)).clip(lower=0.0)              # IF(H>REF(H,1), H-REF(H,1), 0)
    dn = (low.shift(1) - low).clip(lower=0.0)                # IF(L>REF(L,1), 0, REF(L,1)-L)
    d1 = up.rolling(win).sum()
    d2 = dn.rolling(win).sum()
    tot = d1 + d2
    return (d1 / tot.replace(0, np.nan))


def detect_main_rally_start(df: pd.DataFrame, code: str = "",
                            cross_mode: str = "below",
                            flow_threshold: float = FLOW_THRESHOLD,
                            rsi_oversold: float = RSI_OVERSOLD,
                            cci_extreme: float = CCI_EXTREME) -> dict[str, Any]:
    """「主升始发点」三条件（原文源码口径）。绝不 raise。

    ``cross_mode``：
      - ``"below"``（默认，**源码口径**）：主升占比**跌破** flow_threshold（CROSS(0.8,主升)）
      - ``"above"``（**文字口径**）：主升占比**突破** flow_threshold（CROSS(主升,0.8)）
      - ``"either"``：任一方向穿越
    两种都实现是因为原文源码与文字描述相反（见模块 docstring），必须由回测判定。
    """
    try:
        n = len(df) if df is not None else 0
        if n < MAIN_RALLY_MIN_BARS:
            return {"available": False, "hit": False,
                    "reason": f"少于{MAIN_RALLY_MIN_BARS}根K线（原文 BARSCOUNT>60）"}
        # ① 资金流入占比穿越
        fr = flow_ratio(df, FLOW_WIN)
        cur_f = fr.iloc[-1]
        prev_f = fr.iloc[-2] if n >= 2 else np.nan
        if cur_f != cur_f or prev_f != prev_f:
            return {"available": False, "hit": False, "reason": "flow_ratio 不可用"}
        cur_f, prev_f = float(cur_f), float(prev_f)
        cross_below = bool(prev_f >= flow_threshold > cur_f)   # 主升跌破（源码口径）
        cross_above = bool(prev_f <= flow_threshold < cur_f)   # 主升突破（文字口径）
        flow_ok = {"below": cross_below, "above": cross_above,
                   "either": cross_below or cross_above}.get(cross_mode, cross_below)

        # ② 超卖区金叉：前一日 RSI7 < 20 且今日上行
        r = rsi(df["close"], RSI_N)
        cur_r, prev_r = r.iloc[-1], r.iloc[-2]
        if cur_r != cur_r or prev_r != prev_r:
            return {"available": False, "hit": False, "reason": "RSI 不可用"}
        cur_r, prev_r = float(cur_r), float(prev_r)
        rsi_ok = bool(prev_r < rsi_oversold and cur_r > prev_r)

        # ③ CCI 极度偏离 + 上行；J 拐头向上（原文 T1 的核心两项）
        cc = cci(df, CCI_N)
        cur_c, prev_c = cc.iloc[-1], cc.iloc[-2]
        if cur_c != cur_c or prev_c != prev_c:
            return {"available": False, "hit": False, "reason": "CCI 不可用"}
        cur_c, prev_c = float(cur_c), float(prev_c)
        cci_ok = bool(cur_c < cci_extreme and cur_c > prev_c)

        j = _j_series(df)
        j0, j1, j2 = j.iloc[-1], j.iloc[-2], j.iloc[-3] if n >= 3 else np.nan
        j_turn = bool(j0 == j0 and j1 == j1 and j2 == j2 and j0 > j1 and j1 < j2)

        t1_ok = bool(cci_ok and j_turn)
        hit = bool(flow_ok and rsi_ok and t1_ok)
        return {"available": True, "hit": hit,
                "cross_mode": cross_mode,
                "flow_ratio": round(cur_f, 4), "flow_ratio_prev": round(prev_f, 4),
                "flow_cross_below": cross_below, "flow_cross_above": cross_above,
                "flow_ok": flow_ok,
                "rsi7": round(cur_r, 2), "rsi7_prev": round(prev_r, 2), "rsi_ok": rsi_ok,
                "cci": round(cur_c, 2), "cci_prev": round(prev_c, 2), "cci_ok": cci_ok,
                "j": round(float(j0), 2) if j0 == j0 else None, "j_turn_up": j_turn,
                "t1_ok": t1_ok,
                "conditions_met": int(flow_ok) + int(rsi_ok) + int(t1_ok)}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "hit": False,
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


def main_rally_score(df: pd.DataFrame, code: str = "",
                     cross_mode: str = "below") -> dict[str, Any]:
    """三条件命中数 ×33.3（0-100），仅用于回测排序。原文没给权重，故等权。"""
    r = detect_main_rally_start(df, code, cross_mode=cross_mode)
    if not r.get("available"):
        return {"available": False, "score": None, "reason": r.get("reason")}
    return {"available": True, "score": round(r["conditions_met"] * 100.0 / 3.0, 1),
            "hit": r["hit"], "detail": r}
