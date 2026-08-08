# -*- coding: utf-8 -*-
"""共享技术指标：J 值与 BBI 的**唯一实现**。

## 为什么单独一个模块

2026-08-06 清点：`_j_series` 有 **3 份**（screening/enrich_candidates、b2_surge_factor、
main_rally_factor），BBI 公式在 **4 处**代码里各写一遍（backtest_factors ×2、
market_timing/technical_monitor ×2）。这两个指标恰好是 B1 最核心的两个：
（2026-08-08 更新：enrich 的那份包装已作死代码删除 —— 无调用方；它的 J 走
`kdj()`，内部 `fill_na=50`，行为与原来的本地包装一致。）

    J < 13      —— 入场触发（B1 候选的唯一硬条件）
    BBI         —— 移动止盈与持仓状态（`bbi_above` / 连破 N 日清仓）

⇒ 它们分散在 live 选股链、研究回测器、持仓状态机三处。**只要有一处被单独修改，
回测与 live 就会对同一根 K 线算出不同的 J/BBI，而两边的结论再也无法互相印证。**

实测（合成数据 60 根）当时**尚未发散**：
· BBI 四处公式完全一致
· `b2_surge_factor` 与 `main_rally_factor` 的 J **逐点相同**（max diff 0.0000）
· `enrich_candidates` 的 J 因多一步 `fillna(50)` 最大差 1.44、中位 0.0016，
  但 J<13 触发面 0 根不一致 —— 且它的用法是 `min()`（跳过 NaN），
  填 50 只在整段都是 NaN（序列短于 9 根）时改变结果 ⇒ 当前无实际影响。

**趁还没发散就合并**，而不是等某次改动之后再去比对。

## NaN 策略是显式参数，不是隐含默认

`fill_na` 保留原 `enrich_candidates` 本地实现的行为（填 50 = 中性），其余调用方传 None
（保持 NaN）。做成参数而不是统一取一种，是为了**那次合并零行为变化** ——
指标语义的改动应该单独立项、单独回测，不该搭在重构里。
（2026-08-08：enrich 的本地包装已删，它经 `kdj()` 仍走 fill 50；
`j_series(fill_na=50.0)` 的现存调用方是 `factors/b1_pullback_fit`。）
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

# `_infer_price_limit` 用它取前缀基准（涨跌幅上限的唯一来源）。
# code_utils 不导入 indicators，故无环。
from code_utils import price_limit_pct

J_N, J_M1, J_M2 = 9, 3, 3
DKS_MA_WINDOWS = (14, 28, 57, 114)  # 知行多空线的四均线（good_b1 图上参数）          # KDJ 标准参数；com = m - 1 ⇒ com=2


def pct_change(a, b):
    """从 b 到 a 的涨跌幅（百分数，保留 4 位）；b 为 None/0 或 a 为 None 时返回 None。

    2026-08-07 从 `market_timing_collector` 与 `refresh_market_indices` 两份
    逐字相同的私有 `pct(a, b)` 收敛而来。

    返回 None 而不是 0：**「涨跌幅是 0」与「算不出涨跌幅」必须可区分**
    （同 `code_utils.fnum` 的理由）。
    """
    if b in (None, 0) or a is None:
        return None
    return round((a / b - 1) * 100, 4)


def kdj_series(df: pd.DataFrame, *, n: int = J_N, m1: int = J_M1, m2: int = J_M2,
               fill_na: Optional[float] = None) -> tuple[pd.Series, pd.Series, pd.Series]:
    """返回 `(K, D, J)` 三条序列。

    需要 K/D 的调用方（如 `technical_monitor.kdj` 要输出 k/d 字段）用这个，
    只要 J 的用 `j_series`。**两者共用同一段计算**，不会因为「只暴露 J」
    而逼调用方自己再算一遍 K/D —— 那正是重复实现的起点。
    """
    c = df["close"].astype(float)
    low_n = df["low"].astype(float).rolling(n).min()
    high_n = df["high"].astype(float).rolling(n).max()
    rng = (high_n - low_n).replace(0, np.nan)
    rsv = (c - low_n) / rng * 100
    rsv = rsv.replace([np.inf, -np.inf], np.nan)
    if fill_na is not None:
        rsv = rsv.fillna(fill_na)
    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    return k, d, 3 * k - 2 * d


def j_series(df: pd.DataFrame, *, n: int = J_N, m1: int = J_M1, m2: int = J_M2,
             fill_na: Optional[float] = None) -> pd.Series:
    """KDJ 的 J 序列：`RSV → K(EWM) → D(EWM) → J = 3K − 2D`。

    ``fill_na``：``None``（默认）保持 NaN；给数值则在 RSV 层填充
    （`factors/b1_pullback_fit` 传 50.0，即"数据不足按中性处理"）。

    ⚠️ **零振幅要先变 NaN 再决定怎么填**。`high == low`（一字板/停牌）时
    `(close-low)/(high-low)` 是 0/0：不先 replace 会得到 inf/NaN 混杂，
    而 inf 进了 EWM 会把后续所有值污染成 NaN —— 那是"一根一字板毁掉整条 J 序列"。
    """
    return kdj_series(df, n=n, m1=m1, m2=m2, fill_na=fill_na)[2]


def bbi_series(close: pd.Series) -> pd.Series:
    """BBI = (MA3 + MA6 + MA12 + MA24) / 4。

    B1 的移动止盈与持仓状态都建立在它上面（`bbi_above`、连破 N 日清仓），
    所以它必须在 live 选股链、研究回测器、持仓状态机三处完全一致。
    """
    c = close.astype(float)
    return sum(c.rolling(k).mean() for k in (3, 6, 12, 24)) / 4


def dks_series(close: pd.Series, windows: tuple[int, ...] = DKS_MA_WINDOWS) -> pd.Series:
    """DKS（知行多空线）= (MA14+MA28+MA57+MA114)/4。

    2026-08-06 收敛第 3 份重复指标（前两个是 J 与 BBI）。此前有两处：
    · `screening/enrich_candidates.dks_series` —— docstring 自称「**唯一实现**」，
      并记录了它当初就是为了收敛 `technical_monitor.zhixing_state` 才建的
    · `factors/b1_dual_factor._dks_series` —— **但这份它没收进去**

⇒ 「唯一实现」的声明与事实不符，而两份实测逐点相同（尚未发散）。
    移到这里后才真的唯一，并顺带**断开一处循环依赖**：
    `factors/perfect_b1_fit` 需要 DKS，若从 `enrich_candidates` 取就成了
    factors → screening → factors 的环。
    """
    c = close.astype(float)
    return sum(c.rolling(w).mean() for w in windows) / len(windows)

# ══════════════════════════════════════════════════════════════════════════
# 以下 7 个函数 2026-08-07 从 `market_timing/technical_monitor.py` 下移。
#
# 为什么必须下移（架构审查实测）：technical_monitor 在 market_timing/（**决策层**），
# 552 行、17 个顶层函数，但**只有这 7 个被模块外使用**，且全部被
# `factors/`（因子层，本该是底层）与 `screening/` 调用 —— 底层依赖决策层，
# 是分层反转。后果是 import 任一因子都会拖进整个持仓状态机及其依赖。
#
# 它们与上面的 `*_series` 函数的分工：
#   `*_series`（kdj_series / bbi_series / dks_series / j_series）
#       → 返回**序列**，供需要逐 bar 计算的回测与因子用
#   下面这些（kdj / macd / bbi_state / zhixing_state）
#       → 返回**最新一根的字典 + 状态文本**，供报告与状态机用
# 两层都要留：前者是数据，后者是对数据的分类判断。
# ══════════════════════════════════════════════════════════════════════════

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df.empty:
        return df
    x = df.set_index("date").resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "amount": "sum",
        "volume": "sum",
    }).dropna().reset_index()
    return x

def kdj(df: pd.DataFrame, n=9, m1=3, m2=3) -> dict[str, Any]:
    if len(df) < n + 3:
        return {"available": False}
    # J 用共享实现（2026-08-06 收敛第 4 份重复）。`fill_na=50` 保留本模块原行为。
    # ⚠️ 为什么要和 live 选股链共用：同一只票可能同时被 enrich_candidates（选股）
    # 与本模块（持仓状态机）评估 —— 两边算出不同的 J，就会出现
    # 「选股说 J<13 可进，持仓说 J 不低」这类无法解释的矛盾。
    k, d, j = kdj_series(df, n=n, m1=m1, m2=m2, fill_na=50.0)
    jv = float(j.iloc[-1])
    if jv < 13:
        state = "低位调整到位观察"
    elif jv > 90:
        state = "高位过热"
    elif j.iloc[-1] > j.iloc[-2] and jv < 30:
        state = "低位拐头"
    else:
        state = "中性"
    return {
        "available": True,
        "k": round(float(k.iloc[-1]), 4),
        "d": round(float(d.iloc[-1]), 4),
        "j": round(jv, 4),
        "j_prev": round(float(j.iloc[-2]), 4),
        "golden_cross": bool(k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] > d.iloc[-1]),
        "death_cross": bool(k.iloc[-2] >= d.iloc[-2] and k.iloc[-1] < d.iloc[-1]),
        "state": state,
    }

def macd(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 35:
        return {"available": False}
    close = df["close"]
    dif = ema(close, 12) - ema(close, 26)
    dea = ema(dif, 9)
    hist = (dif - dea) * 2
    return {
        "available": True,
        "dif": round(float(dif.iloc[-1]), 4),
        "dea": round(float(dea.iloc[-1]), 4),
        "hist": round(float(hist.iloc[-1]), 4),
        "hist_prev": round(float(hist.iloc[-2]), 4),
        "hist_direction": "扩张" if hist.iloc[-1] > hist.iloc[-2] else "收缩",
        "golden_cross": bool(dif.iloc[-2] <= dea.iloc[-2] and dif.iloc[-1] > dea.iloc[-1]),
        "death_cross": bool(dif.iloc[-2] >= dea.iloc[-2] and dif.iloc[-1] < dea.iloc[-1]),
    }

def bbi_state(df: pd.DataFrame) -> dict[str, Any]:
    """Return the standard TDX BBI state used by the B1 holding rules."""
    if len(df) < 24:
        return {"available": False, "reason": "少于24根K线"}
    close = df["close"]
    bbi = bbi_series(close)
    valid = bbi.notna()
    if not valid.any():
        return {"available": False, "reason": "BBI无法计算"}

    c = float(close.iloc[-1])
    value = float(bbi.iloc[-1])
    below = close < bbi
    consecutive_below = 0
    for is_below in reversed(below.tolist()):
        if not is_below:
            break
        consecutive_below += 1

    distance_pct = (c / value - 1) * 100 if value else None
    return {
        "available": True,
        "formula": "(MA3+MA6+MA12+MA24)/4",
        "value": round(value, 4),
        "close_above": bool(c >= value),
        "distance_pct": round(distance_pct, 4) if distance_pct is not None else None,
        "consecutive_closes_below": consecutive_below,
        "previous_close_above": bool(close.iloc[-2] >= bbi.iloc[-2]) if len(df) >= 25 else None,
    }

def zhixing_state(df: pd.DataFrame, m1: int = 14, m2: int = 28, m3: int = 57, m4: int = 114) -> dict[str, Any]:
    """知行趋势线（通达信 ZSDKX）：快线 QSX 上穿慢线 DKS 为多头/金叉。

    - QSX = EMA(EMA(CLOSE,10),10)（短期趋势线，图上白线）。
    - DKS = (MA(CLOSE,m1)+MA(CLOSE,m2)+MA(CLOSE,m3)+MA(CLOSE,m4))/4（多空线，图上黄线）。
    - 多头状态 QSX>DKS；金叉=QSX 由下向上穿越 DKS 当日。
    - 辅助：MA1=MA(CLOSE,60)、MA2=EMA(CLOSE,13)。
    需 >= m4 根 K 线才能计算 DKS，否则 available=False。
    """
    if len(df) < m4:
        return {"available": False, "reason": f"少于{m4}根K线，DKS(MA{m4})无法计算"}
    close = df["close"].astype(float).reset_index(drop=True)
    qsx = ema(ema(close, 10), 10)
    dks = sum(close.rolling(n).mean() for n in (m1, m2, m3, m4)) / 4
    valid = qsx.notna() & dks.notna()
    if not valid.any():
        return {"available": False, "reason": "QSX/DKS 无有效值"}
    gt = (qsx > dks) & valid
    prev_gt = gt.shift(1, fill_value=False)
    cross_up = gt & (~prev_gt) & valid.shift(1, fill_value=False)
    idxs = [i for i, v in enumerate(cross_up.tolist()) if v]
    days_since = (len(close) - 1 - idxs[-1]) if idxs else None
    qsx_last = float(qsx.iloc[-1])
    dks_last = float(dks.iloc[-1])
    c = float(close.iloc[-1])
    ma1 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else None
    ma2 = float(ema(close, 13).iloc[-1])
    return {
        "available": True,
        "qsx": round(qsx_last, 4),
        "dks": round(dks_last, 4),
        "qsx_gt_dks": bool(qsx_last > dks_last),
        "golden_cross_today": bool(cross_up.iloc[-1]),
        "days_since_golden_cross": days_since,
        "close_above_qsx": bool(c >= qsx_last),
        "ma1_ma60": round(ma1, 4) if ma1 is not None else None,
        "ma2_ema13": round(ma2, 4),
        "params": {"m1": m1, "m2": m2, "m3": m3, "m4": m4},
    }

def _infer_price_limit(code: str, df: pd.DataFrame) -> int:
    """Infer the daily price-limit percentage for a stock.

    Base comes from ``code_utils.price_limit_pct`` (single source), then
    validates against observed historical daily changes: if any of the
    **last 20 trading days** shows |change_pct| > 9.9 for a 10%-prefix stock,
    upgrade to 20%. This catches edge cases without relying solely on static
    prefix rules. ST/special-treatment stocks typically have 5% limits; we
    detect those by checking if observed max |change_pct| is consistently
    <= 5.2. The ST downgrade only applies to 10%-prefix stocks — a quiet
    20-day window must never demote a 300/301/688/920 stock to 5%.
    """
    # ⚠️ base 必须来自 `code_utils.price_limit_pct`（唯一来源）。此前这里内联写
    # `20 if startswith(("688","920","300","301")) else 10`，对北交所给 20 而
    # 实际限制是 **30**，且漏了 83/87/43 前缀。下面的数据自纠只能把 10 升到 20、
    # **永远到不了 30**，所以那个偏差不会被历史波动纠正 —— 详见唯一实现的 docstring。
    base = int(price_limit_pct(code))
    if len(df) >= 20:
        # ⚠️ 自纠只看**最近 20 个交易日**（docstring 一直写「近20日」，实现却取整条
        # 序列）：10% 板块新股的窗口若含上市首日 +44%，max_change 会被它永久顶穿
        # 9.9 ⇒ 该股被**永久**升级为 20% 口径，再也回不去。tail(20) 后首日异动
        # 滚出窗口即恢复（2026-08-08 修复）。
        changes = (df["close"] / df["close"].shift(1) - 1).abs() * 100
        max_change = float(changes.tail(20).dropna().max())
        if base == 10 and max_change > 9.9:
            base = 20
        if base == 10 and max_change <= 5.2:
            # ST 降级仅适用于 10% 前缀品种；20% 前缀（300/301/688/920）
            # 即使近 20 日波动很小也不得降级为 5%。
            base = 5
    return base
