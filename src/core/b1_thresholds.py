# -*- coding: utf-8 -*-
"""B1 反转 K 的判定阈值 —— **live 链的唯一来源**（L0，零内部依赖）。

## 为什么单独一个模块

同一组阈值原先散在三处 live 代码里：

    screening/enrich_candidates.py    读环境变量（唯一可配置的那份）
    market_timing/technical_monitor.py  硬编码 -2 <= change_pct <= 2、amplitude <= 7
    holdings/b1_holding_state.py        硬编码 j < 13

三处都是 L3（screening / market_timing / holdings），彼此不能互相 import
（同层会引入环），所以共享常量只能放到 L0 或 L2 —— 这里是 L0。

## 这个分散造成过什么

owner 2026-08-06 要求反转 K「对称 ±2% **且可配置**」。实测（2026-08-07）：
设 `B1_REVK_CHG_PCT=1.0` 只收紧了**选股链**，14:45/17:00 报告走的**持仓链**
仍按 ±2 判定，而 `technical_monitor` 的 `thresholds` 字典还会把
`reversal_close_change_pct: [-2.0, 2.0]` 当成「当前阈值」上报 —— 配置一改它就是假话。
即「可配置」这个能力只覆盖了一半的链，且另一半会谎报自己的阈值。

## 研究侧刻意**不**读这里

豁免清单（刻意钉死默认值、不跟随环境变量；两边相等由
`tests/test_enrich_b1cz.py::TestReversalKThresholdSingleSource` 钉住）：

    factors/reversal_quality.py     REVK_* 钉死。理由是它的阈值钉死才能复现既有
                                    回测数字（R2 P1 重跑清单依赖那些数字）。
    research/backtest_factors.py    REVK_* / J_LOW_THRESHOLD 钉死 = 本模块默认值，
                                    同理由（2026-08-09 登记）。判定逻辑（round-2
                                    涨跌幅、prev_close 振幅分母、`<` 量分位）
                                    已与 live 对齐，只有「不读 env」是刻意的。

两边默认值相同（对称 ±2%），但覆盖环境变量时**只有 live 会变** —— 这是有意的。

反向（**跟随**本模块）：release 标注因子 `b1_dual_factor.J_LOW_THRESHOLD` /
`b2_surge_factor.B2_J_LOW` 自 2026-08-09 起从这里导入 —— live 候选表的标注
应反映 live 口径，其默认值 13.0 由同一测试类钉住。

⚠️ 之前两边的文档朝**相反方向**说错：`reversal_quality` 的 docstring 说
「本因子与 live 的反转 K 不是同一个东西」（owner 08-06 统一后已过时），
而 `enrich_candidates` 的注释说「两边读同一处」（实测不是，只是默认值恰好相同）。
两处都已订正。

## 配置方式

    B1_REVK_CHG_PCT=2.5                    → ±2.5%（对称）
    B1_REVK_CHG_MIN=-2 B1_REVK_CHG_MAX=1.8 → 回到不对称（2026-08-04 的旧口径）
    B1_REVK_AMP_PCT=6                      → 振幅上限 6%
    B1_J_LOW=10                            → J 低位阈值 10
    B1_REVK_VOL_RATIO=0.4                  → 极致缩量：量比 vs MA5 <= 0.4
    B1_REVK_VOL_PCTILE=5                   → 极致缩量：20 日量分位 <= 5（单位：%）

⚠️ 全部在**模块导入时**求值。运行中改 `os.environ` 对已导入的模块无效，
必须 `importlib.reload`。这不是疏漏 —— 阈值在一次运行内保持恒定，
否则同一份报告里不同股票可能按不同阈值判定。
"""
from __future__ import annotations

import os


def _f(name: str, default: float) -> float:
    """读环境变量为 float；空值或非法值回落到默认值。

    ⚠️ 不用 `float(os.environ.get(name, default))` 直接转 —— 环境变量被设成
    空串（`export B1_J_LOW=`）时那样会 ValueError 并**打断整次运行**，
    而一个配置项写错不该让报告产不出来。
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


# --- J 低位 ---
J_LOW_THRESHOLD = _f("B1_J_LOW", 13.0)

# --- 极致缩量 ---
VOL_RATIO_MAX = _f("B1_REVK_VOL_RATIO", 0.5)      # 量比 vs MA5 <= 0.5
VOL_PCTILE_MAX = _f("B1_REVK_VOL_PCTILE", 10.0)   # 20 日量分位 <= 10（单位：%）

# --- 收盘涨跌幅区间（默认对称，owner 2026-08-06 拍板）---
REVERSAL_CHANGE_PCT = _f("B1_REVK_CHG_PCT", 2.0)
REVERSAL_CHANGE_MIN_PCT = _f("B1_REVK_CHG_MIN", -REVERSAL_CHANGE_PCT)
REVERSAL_CHANGE_MAX_PCT = _f("B1_REVK_CHG_MAX", REVERSAL_CHANGE_PCT)

# --- 振幅 ---
REVERSAL_AMPLITUDE_PCT = _f("B1_REVK_AMP_PCT", 7.0)


def change_in_range(change_pct: float | None) -> bool:
    """收盘涨跌幅是否落在反转 K 区间内。

    ⚠️ 比较前 `round(..., 2)`，与候选落盘上报的 `change_pct` 同一精度。

    不取整会让边界随价位任意漂：`(last/prev - 1) * 100` 在 10.00→9.80 得
    -1.9999999999999907（判进），在 50.00→49.00 得 -2.0000000000000018（判出）。
    枚举 1.00~100.00 的两位价格，**显示为恰好 ±2.00% 的价格对里有 5143 组**
    落在区间外，而候选 JSON 里写的是 `-2.0` —— 读报告的人看到「-2.0%，在 ±2% 内」
    却找不到反转 K 标记，无从解释。取整后判定与显示一致。

    2026-08-07 定：**以显示精度（2 位）为判定精度**。
    """
    if change_pct is None:
        return False
    return REVERSAL_CHANGE_MIN_PCT <= round(float(change_pct), 2) <= REVERSAL_CHANGE_MAX_PCT
