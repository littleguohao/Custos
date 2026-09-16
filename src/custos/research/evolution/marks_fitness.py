# -*- coding: utf-8 -*-
"""marks 分离适应度（R36 Phase 2 监督模式）：因子在正例买点上是否点火。

**自指口径**（写死）：分离代理 = 因子在买点相对**该股自身历史**是否点火——
``TS_RANK(expr, K)`` 在案例 bars（约 3 个月窗口）上求值，取 buy_date 当日值
∈ (0,1]（当日值在自身窗口的分位）。**无需全宇宙数据**，这是它与
``factor_ic_profile --marks``（全宇宙当日分位）的分工：本模块是**发现侧**
的廉价排序依据；那不是、也永远不是判据——最终晋级仍走全宇宙双窗+三轴
交易语义（R36-C2~C5 不变）。

``contrast`` 提供案例内对照：买点均值 − 案例内全日均值（同一个股同一窗口，
买点相对窗口其余日子是否更点火）。mean_rank 高但 contrast 低 = 因子在该股
全程高位（没有买点特异性）；两者联合比单看 mean_rank 更难糊弄。

窗口纪律：``rank_window`` 必须远小于案例窗长（61~78 根）——默认 20
（``DEFAULT_RANK_WINDOW``，约一个月；TS_RANK warmup 吃 19 根后每个案例
还剩 ≥40 个有效点）。**K=250 在全案例上恒 NaN**（warmup 覆盖全窗），
是本口径的反例钉。

⚠️ 后视标注声明：买点是事后标注（选择偏差，B1_DATA 10 例，L1 发现材料）——
本模块的读数只用于候选排序，任何「召回 X/10」都不作判据。

**案例口径（owner 2026-09-16 拍板）**：案例身份 = (code, buy_date) 二元组，
权威且仅此；CSV 窗口只是材料片段，**观察窗自由**（买点前任意周期，数据允许
为限——``bars_provider`` 给全历史则用全历史）。评估用 bars 经
``b1_perfect_dataset.resolve_bars`` 解析（provider 全历史物理截到 buy_date 含
当日 ⇒ provider 路径天然无未来函数；excerpt 回退是 CSV 片段，买点即末根）。
本函数在 evaluate 之前对解析结果**再物理截断一次**到 buy_date（含）——
幂等双保险（防未来函数纪律按物理截断执行，不靠算子因果性自觉）；
截断点之后的数据被改动，结果逐位不变（篡改钉测在案）。
"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd

from custos.research.evolution import expr_dsl

#: 默认 rank_window：20（约一个月交易日）。⚠️ 不许照抄 MAX_WINDOW=250——
#: B1_DATA 案例窗只有 61~78 根，TS_RANK(expr,250) 的 warmup 覆盖全窗 ⇒
#: 全案例恒 NaN（钉测在案）；20 根在每个案例上都有 ≥40 个有效点，
#: 买点分位与 contrast 的分母都站得住。要更长窗请显式给（≤案例窗长）。
DEFAULT_RANK_WINDOW = 20


def _buy_pos(bars: pd.DataFrame, buy_date: str) -> Optional[int]:
    """买点在 bars 里的行号（bars 是 RangeIndex、date 在列——B1_DATA 装载约定）。"""
    d = bars["date"].astype(str).str[:10]
    hit = d[d == buy_date]
    return int(hit.index[-1]) if len(hit) else None


def _ts_rank_at_buy(
    expr: str, bars: pd.DataFrame, buy_date: str, rank_window: int
) -> Optional[float]:
    """TS_RANK(expr, K) 全序列上取 buy_date 当日值；无值/越界/非法 → None。"""
    pos = _buy_pos(bars, buy_date)
    if pos is None:
        return None
    try:
        tree = expr_dsl.parse(f"TS_RANK(({expr}),{rank_window})")
        v = float(expr_dsl.evaluate(tree, bars).iloc[pos])
    except Exception:  # noqa: BLE001  # 发现侧不硬算：非法/数据坏 → 该点 None
        return None
    if not math.isfinite(v):
        return None
    return v


def marks_score(
    expr: str,
    cases: list,
    *,
    rank_window: int = DEFAULT_RANK_WINDOW,
    bars_provider: Any = None,
) -> dict[str, Any]:
    """表达式在正例买点上的分离读数（自指口径，见模块 docstring）。

    返回::

        {"mean_rank": 有效买点均值, "min_rank": 有效买点最小值,
         "contrast": 买点均值 − 案例内全日均值（同样本对差后取均值的口径：
                     逐案例 (rank_buy − mean(rank_all)) 再对案例求均值，
                     双侧同样本才可比——与全均值直接相减不同，写死此口径）,
         "per_case": [{code, buy_date, rank, status, bars_source, n_bars}],
         "n_hit": 有效点数, "n_cases": 案例数}

    案例当日无值（warmup/NaN/数据缺）→ 该点记 None 不计入均值，per_case
    status="no_value" 照实记录（不静默跳过）；全部无值 → 汇总全 None，
    n_hit=0（调用方按 fail-closed 处理，不许当高分）。

    ``bars_provider``（Callable[[str], DataFrame|None]）：给全历史则评估在
    「买点前任意周期」上做（观察窗自由）；None/空帧回退 excerpt（CSV 片段，
    观察窗受限是数据妥协不是设计）。**物理截断于 buy_date（含当日）**——
    resolve_bars 与本函数双保险；买点后的数据不得进入求值。
    excerpt 回退时 contrast/均值的口径不变（在可用历史上算）。
    """
    from custos.research.b1_perfect_dataset import resolve_bars  # noqa: PLC0415

    per_case: list[dict[str, Any]] = []
    buy_ranks: list[float] = []
    contrasts: list[float] = []
    for c in cases:
        bars, source = resolve_bars(c, bars_provider)
        # 物理截断双保险（resolve_bars 已截 provider 路径；excerpt 末根即买点）：
        bars = bars[bars["date"].astype(str).str[:10] <= c.buy_date].reset_index(
            drop=True
        )
        n_bars = len(bars)
        buy_rank = _ts_rank_at_buy(expr, bars, c.buy_date, rank_window)
        if buy_rank is None:
            per_case.append(
                {
                    "code": c.code,
                    "buy_date": c.buy_date,
                    "rank": None,
                    "status": "no_value",
                    "bars_source": source,
                    "n_bars": n_bars,
                }
            )
            continue
        # 案例内全日均值（同 expr 同窗口全部有效 TS_RANK 值的均值）
        try:
            tree = expr_dsl.parse(f"TS_RANK(({expr}),{rank_window})")
            s_all = expr_dsl.evaluate(tree, bars).to_numpy(dtype=float)
            valid = s_all[np.isfinite(s_all)]
            day_mean = float(valid.mean()) if len(valid) else float("nan")
        except Exception:  # noqa: BLE001
            day_mean = float("nan")
        contrast_i = buy_rank - day_mean if not math.isnan(day_mean) else None
        per_case.append(
            {
                "code": c.code,
                "buy_date": c.buy_date,
                "rank": buy_rank,
                "status": "hit",
                "bars_source": source,
                "n_bars": n_bars,
            }
        )
        buy_ranks.append(buy_rank)
        if contrast_i is not None:
            contrasts.append(contrast_i)
    n_hit = len(buy_ranks)
    return {
        "mean_rank": (sum(buy_ranks) / n_hit) if n_hit else None,
        "min_rank": min(buy_ranks) if n_hit else None,
        "contrast": (sum(contrasts) / len(contrasts)) if contrasts else None,
        "per_case": per_case,
        "n_hit": n_hit,
        "n_cases": len(cases),
    }
