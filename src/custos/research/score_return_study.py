# -*- coding: utf-8 -*-
"""研究：0AMV 做多区间 J<13 信号的 **live 技术分** 与 BBI 止盈收益是否正相关。

> ⚠️ **R11 警示（先读）**：B1 基准在 3000 样本下**已实现口径为负期望**
> （governance/research/R11_baseline_margin_collapse.md：剔除未平仓浮盈后 −32R，
> 且仍带幸存者偏差，去偏只会更低）。本研究的收益读数**仅供相对排序与相关性
> 分析**，任何均收/期望数字都不得引用为策略量级预期。

研究问题（owner 2026-08-20）：
每次 0AMV 做多 regime 期间，J<13 信号（BBI 止盈 = 站上后连破 2 根收盘清仓）
收益最好的 50% 股票，其信号日的 **live 技术分分布**；得分与涨幅是否正相关。

口径（全部复用既有实现，不重写）：

- **信号**：日 KDJ 的 J<13 —— `backtest_factors.j_low_gate`（J_LOW_THRESHOLD=13.0，
  与 live 1800 进池硬门槛同口径）。
- **区间过滤**：只在 0AMV「做多」regime 进场 —— `backtest_factors.load_amv_regime`
  （指南针 compass_amv 日线 + 人工台账 → 状态机 >4% 做多 / <-2.3% 空头 / 之间粘滞）。
- **出场**：`backtest_factors.simulate_b1_trade`，bbi_exit_consec=2（站上 BBI 后
  连续 2 日收盘跌破清仓）；time_stop=0 / trail=0 全关。
  初始止损 ``--stop-pct``（pct 固定空间）：**默认 5%**（R10「5% 是崖」下沿；
  50 ≈ 无止损基线口径，``STOP_PCT_WIDE`` 保留以复现）。
  ``--cost-zone-bars 3`` 可叠加「不涨就拍」（R10 冠军组合 pct_05_amv_cz3 的出场侧）。
  v0.118 起 ``--breakeven``（保本止损触发浮盈）/ ``--scale-out``（BBI 上方双中大阳
  分批止盈，R9 档 0.5）/ ``--top-frac``（赢家组分位，默认 0.5）可配。
- **技术分**：信号日的 **live technical_score**——截断到信号日的 df（tail 260，
  同 live ``OHLCV_LOAD_BARS``）+ df_long（tail 1200，周/月 MACD 红柱腿用，
  同 live ``OHLCV_LOAD_BARS_LONG``）喂 `enrich_candidates.compute_metrics`，
  再喂 `score_candidates.technical_score`（weights=None = DEFAULT_TECH_WEIGHTS）。
  **严禁未来函数**：截断只取 ≤ 信号日的数据；as-of 口径由
  tests/test_score_return_study.py 钉住，并与 enrich 真实落盘分对拍（--spot-check）。
  v0.175 起 cand 走 `asof_candidate` 内容键缓存：同一 (票, 信号日) 的三层截断帧
  逐字节相同时复用首次计算结果（与逐笔重算逐位一致，等价性钉测见
  tests/test_asof_cache_equivalence.py）。
- **成本**：往返 25bps（--cost-bps，从每笔收益扣除）。

统计：按每个 0AMV 做多区间分组，组内按该笔交易净收益降序取 top-50% / bottom-50%
比较技术分分布；全体信号算 Spearman（主）+ Pearson（辅）；按 live 弱/中/强档
（<30 / 30-59 / ≥60）给各档均收/胜率/盈亏比；并按前后半窗核对一致性
（R10/R4 教训：前后半窗翻转是本仓库反复出现的坑）。

CLI::

    uv run python src/custos/research/score_return_study.py --max-stocks 400 --seed 0
    uv run python src/custos/research/score_return_study.py --spot-check 2026-08-19 --n 5
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import statistics
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from custos.pipeline.screening import enrich_candidates as ec  # noqa: E402
from custos.pipeline.screening import score_candidates as sc  # noqa: E402
from custos.research import backtest_factors as bf  # noqa: E402

# 出场固定口径：BBI 止盈（连破 2 根）+ pct 初始止损。
# 昨晚基线（score_return_study_s0_n400.json）用 STOP_PCT_WIDE=50 宽设占位（≈无止损，
# 实测 stop 系仅 21/14469 笔）；owner 2026-08-21 要求带真止损重跑 ⇒ 默认改为
# STOP_PCT_DEFAULT=5（R10 验证过的止损下界最优档「5% 是崖」的下沿），
# 并可叠加 cost_zone_bars=3（R10 冠军组合 pct_05_amv_cz3 的出场侧）。
STOP_MODE = "pct"
STOP_PCT_DEFAULT = 5.0
STOP_PCT_WIDE = 50.0  # 仅用于复现「≈无止损」基线口径
COST_BPS = 25.0
INDEX_CODE = "999999"  # 上证指数（compute_metrics 的 20 日相对强度用，同 enrich）

# live 技术分三档阈值（score_candidates.TECH_STRONG_FALLBACK / TECH_MID_FALLBACK）
BANDS = ("<30", "30-59", ">=60")

R11_WARNING = (
    "⚠️ R11 警示：B1 基准已实现口径（3000 样本、剔除未平仓浮盈）为负期望，"
    "且 vipdoc 宇宙带幸存者偏差——本研究读数仅供相对排序/相关性分析，"
    "不得引用为策略量级预期。"
)


# ---------------------------------------------------------------------------
# 纯函数段（钉测覆盖）
# ---------------------------------------------------------------------------


def long_intervals(regime: dict[str, str]) -> list[tuple[str, str]]:
    """date→regime 映射 → 连续「做多」区间列表 [(start, end), ...]（含端点）。

    regime 的键是 0AMV 有读数的交易日；连续做多日之间被非做多日打断即分段。
    """
    out: list[tuple[str, str]] = []
    start: Optional[str] = None
    prev: Optional[str] = None
    for d in sorted(regime):
        if regime[d] == "做多":
            if start is None:
                start = d
            prev = d
        else:
            if start is not None:
                out.append((start, prev or start))
                start, prev = None, None
    if start is not None:
        out.append((start, prev or start))
    return out


def interval_of(date: str, intervals: list[tuple[str, str]]) -> Optional[int]:
    """entry_date 所属做多区间下标；不属于任何区间返回 None。"""
    starts = [s for s, _ in intervals]
    i = bisect.bisect_right(starts, date) - 1
    if i >= 0 and intervals[i][0] <= date <= intervals[i][1]:
        return i
    return None


def split_top_half(
    trades: list[dict[str, Any]], key: str = "ret"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按 key 降序排序后切 top-50% / bottom-50%（奇数时 top 多拿一笔：(n+1)//2）。"""
    ordered = sorted(trades, key=lambda t: t[key], reverse=True)
    n_top = (len(ordered) + 1) // 2
    return ordered[:n_top], ordered[n_top:]


def split_top_frac(
    trades: list[dict[str, Any]], frac: float = 0.5, key: str = "ret"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按 key 降序切「前 frac 赢家组 / 其余对照组」（n_top=ceil(n×frac)，至少 1）。

    frac=0.5 时与 ``split_top_half`` 逐位一致（ceil(n/2)==(n+1)//2）——
    与 winner_factor_study.split_top_frac 同规则（两处各自钉测钉住，防漂移）。
    """
    ordered = sorted(trades, key=lambda t: t[key], reverse=True)
    n_top = max(1, math.ceil(len(ordered) * frac)) if ordered else 0
    return ordered[:n_top], ordered[n_top:]


def band_of(score: float) -> str:
    """live 技术分层阈值：强>=60 / 中30-59 / 弱<30。"""
    if score >= sc.TECH_STRONG_FALLBACK:
        return ">=60"
    if score >= sc.TECH_MID_FALLBACK:
        return "30-59"
    return "<30"


def dist_stats(scores: list[float]) -> dict[str, Any]:
    """技术分分布：n / 均值 / 中位 / p25 / p75 / 三档计数。"""
    if not scores:
        return {"n": 0}
    qs = pd.Series(scores, dtype=float)
    return {
        "n": len(scores),
        "mean": round(float(qs.mean()), 2),
        "median": round(float(qs.median()), 2),
        "p25": round(float(qs.quantile(0.25)), 2),
        "p75": round(float(qs.quantile(0.75)), 2),
        "min": round(float(qs.min()), 2),
        "max": round(float(qs.max()), 2),
        "bands": {b: sum(1 for s in scores if band_of(s) == b) for b in BANDS},
    }


def band_stats(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按技术分三档统计收益：n / 均收 / 中位收 / 胜率 / 盈亏比 / 均分。"""
    out: dict[str, dict[str, Any]] = {}
    for b in BANDS:
        ts = [t for t in trades if band_of(t["tech_score"]) == b]
        if not ts:
            out[b] = {"n": 0}
            continue
        rets = [t["ret"] for t in ts]
        wins = [r for r in rets if r > 0]
        losses = [-r for r in rets if r < 0]
        avg_win = statistics.mean(wins) if wins else 0.0
        avg_loss = statistics.mean(losses) if losses else 0.0
        out[b] = {
            "n": len(ts),
            "avg_ret": round(statistics.mean(rets), 4),
            "median_ret": round(statistics.median(rets), 4),
            "win_rate": round(len(wins) / len(rets), 4),
            "payoff_ratio": round(avg_win / avg_loss, 3) if avg_loss > 0 else None,
            "avg_score": round(statistics.mean([t["tech_score"] for t in ts]), 2),
        }
    return out


def ret_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """整体收益指标：n / 均收 / 中位收 / 胜率 / 盈亏比（band_stats 的全样本版）。"""
    if not trades:
        return {"n": 0}
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [-r for r in rets if r < 0]
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    return {
        "n": len(rets),
        "avg_ret": round(statistics.mean(rets), 4),
        "median_ret": round(statistics.median(rets), 4),
        "win_rate": round(len(wins) / len(rets), 4),
        "payoff_ratio": round(avg_win / avg_loss, 3) if avg_loss > 0 else None,
    }


def exit_reason_dist(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """出场原因分布：笔数 / 占比 / 该原因均收（对照止损臂与无止损基线的结构变化）。"""
    import collections

    out: dict[str, dict[str, Any]] = {}
    n = len(trades)
    for reason in sorted(collections.Counter(str(t["reason"]) for t in trades)):
        rr = [t["ret"] for t in trades if str(t["reason"]) == reason]
        out[reason] = {
            "n": len(rr),
            "frac": round(len(rr) / n, 4) if n else None,
            "avg_ret": round(statistics.mean(rr), 4),
        }
    return out


def correlations(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """技术分 vs 净收益的 Spearman（主）+ Pearson（辅）。n<3 返回 None。

    ⚠️ Spearman 手工实现（平均秩上的 Pearson）：环境**没有 scipy**，pandas 的
    ``corr(method='spearman')`` 会 ModuleNotFoundError。平均秩口径与 scipy 默认一致
    （同分取平均秩）；零方差（全同分）时相关无定义，如实返回 None。
    """
    if len(trades) < 3:
        return {"n": len(trades), "spearman": None, "pearson": None}
    s = pd.Series([t["tech_score"] for t in trades], dtype=float)
    r = pd.Series([t["ret"] for t in trades], dtype=float)
    pearson = float(s.corr(r, method="pearson"))
    rs, rr = s.rank(method="average"), r.rank(method="average")
    spearman = (
        float(rs.corr(rr, method="pearson"))
        if rs.std() > 0 and rr.std() > 0
        else float("nan")
    )
    return {
        "n": len(trades),
        "spearman": None if math.isnan(spearman) else round(spearman, 4),
        "pearson": None if math.isnan(pearson) else round(pearson, 4),
    }


def half_window_check(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """前后半窗一致性：按 entry_date 中位切两半，各算 Spearman，报方向是否一致。

    （R10/R4 教训：edge 集中在单一 regime、前后半窗翻转是本仓库反复出现的坑。）
    """
    if len(trades) < 6:
        return {"n": len(trades), "skipped": "样本不足(<6)"}
    dates = sorted(t["entry_date"] for t in trades)
    mid = dates[len(dates) // 2]
    first = [t for t in trades if t["entry_date"] <= mid]
    second = [t for t in trades if t["entry_date"] > mid]
    c1, c2 = correlations(first), correlations(second)
    s1, s2 = c1.get("spearman"), c2.get("spearman")
    return {
        "split_date": mid,
        "first_half": c1,
        "second_half": c2,
        "consistent": (
            None
            if s1 is None or s2 is None
            else (s1 > 0) == (s2 > 0)  # 同向（含同为 0 边界按非同向处理）
        ),
    }


# ---------------------------------------------------------------------------
# as-of 技术分（严禁未来函数：只取 ≤ 信号日的数据）
# ---------------------------------------------------------------------------
#
# --- 性能（v0.175，2026-09-04）：per-trade 全管线重算的去重，口径逐位不变 ---
#
# 背景：主流程对**每笔交易**调 ec.compute_metrics（live 全套因子管线，实测
# ~35ms/笔）。方案调研结论（合成数据实测，详见 tests/test_asof_cache_equivalence.py）：
#   ✗「逐股全序列算一次 + 按信号日点查询」**不合法**：live 口径是 tail(260)
#     起点**重新播种**，EMA 系递归指标（MACD/KDJ/ADX/QSX/DKS）与全序列同位点
#     不逐位相等（84 个采样窗口：MACD/KDJ/ADX 84/84 不一致、QSX/DKS ~9 成不一致；
#     仅 rolling-MA 族的 BBI 100% 逐位一致）⇒ 不能换口径，只能精确去重。
#   ✓ 精确内容键缓存（asof_candidate）：compute_metrics 是
#     (df截断帧, index截断帧, code) 的确定性纯函数 ⇒ 三帧内容摘要为键，
#     命中即返回同一份 cand（与重算逐位一致）。单遍 study 重复率实测 0.0%
#     （每股每日最多一笔），收益集中在 resonance3「两臂 + gate④ 技术分腿」
#     这类同 (票,信号日) 复算模式（实测 22% 调用是重复）。
#   ✓ index as-of 快速路径：指数 ≤ 信号日的 tail(260) 帧只依赖 entry_date；
#     日期列字符串化每帧对象只做一次（旧版每笔全列 astype(str)，占
#     asof_frames 约一半耗时），日期升序时 bisect 定位与旧布尔掩码 tail(260)
#     选出同一批行同一顺序（数学恒等；非升序回退旧路径，钉测钉住）。
#
# 两块进程内缓存（都不写盘、可随意清空，语义不变）：
# - _IDX_DATE_CACHE：指数帧日期字符串列，id(帧) 键 + 强引用防 id 复用，FIFO 封顶；
# - _CAND_CACHE：as-of cand，(code, compute_metrics 函数身份, 三层截断帧内容摘要)
#   键，LRU 封顶（实测单股 gate 扫描→hook 的在途候选 ~10² 条 ≪ 封顶值；
#   cand 深大小 ~40KB ⇒ 封顶时 ~80MB，内存有界）。函数身份进键：缓存值是
#   「**当前** ec.compute_metrics 在该内容上的输出」——测试 monkeypatch 替身
#   与原函数各有各的键空间，互不串味。
_IDX_DATE_CACHE_MAX = 8
_IDX_DATE_CACHE: dict[int, tuple[list[str], bool, pd.DataFrame]] = {}
_CAND_CACHE_MAX = 2048
_CAND_CACHE: OrderedDict[tuple[str, Any, bytes, bytes, bytes], dict[str, Any]] = (
    OrderedDict()
)


def _index_dates(index_full: pd.DataFrame) -> tuple[list[str], bool]:
    """index_full 的日期字符串列（YYYY-MM-DD）+ 是否升序，每帧对象只算一次。

    以 id(帧) 为键缓存并**持强引用**：帧活着 ⇒ id 唯一不复用 ⇒ 绝不命中别帧；
    表满 FIFO 逐出（逐出后帧若还在，下次重算重插，语义不变）。
    """
    key = id(index_full)
    hit = _IDX_DATE_CACHE.get(key)
    if hit is not None and hit[2] is index_full:
        return hit[0], hit[1]
    dates = index_full["date"].astype(str).str[:10].tolist()
    sorted_ok = dates == sorted(dates)
    while len(_IDX_DATE_CACHE) >= _IDX_DATE_CACHE_MAX:
        _IDX_DATE_CACHE.pop(next(iter(_IDX_DATE_CACHE)))
    _IDX_DATE_CACHE[key] = (dates, sorted_ok, index_full)
    return dates, sorted_ok


def _index_asof(index_full: pd.DataFrame, entry_date: str) -> pd.DataFrame:
    """上证指数 ≤ entry_date 的 tail(260) 帧（与旧布尔掩码口径逐位一致）。

    日期升序（各 study 均排序后传入）时 bisect_right 定位 k = ≤ entry_date 的行数，
    iloc[k-260:k] 与旧版 ``掩码 + tail(260)`` 选出同一批行、同一顺序；传入未排序
    帧时原样回退旧掩码路径，行为不变。
    """
    dates, sorted_ok = _index_dates(index_full)
    if sorted_ok:
        k = bisect.bisect_right(dates, entry_date)
        return index_full.iloc[max(0, k - ec.OHLCV_LOAD_BARS) : k].reset_index(
            drop=True
        )
    idx_dates = index_full["date"].astype(str).str[:10]
    return (
        index_full[idx_dates <= entry_date]
        .tail(ec.OHLCV_LOAD_BARS)
        .reset_index(drop=True)
    )


def asof_frames(
    df_full: pd.DataFrame, index_full: pd.DataFrame, i: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """信号日（df_full 第 i 根）的三层 as-of 截断（**无未来函数**：只取 ≤ 信号日）。

    - ``df``      = df_full.iloc[:i+1].tail(ec.OHLCV_LOAD_BARS)      （260 根）
    - ``df_long`` = df_full.iloc[:i+1].tail(ec.OHLCV_LOAD_BARS_LONG) （1200 根，
      仅供 check_macd_technics 周/月红柱腿；不足时 wm_available=False 如实标注）
    - ``index``   = 上证指数 ≤ 信号日 tail 260（20 日相对强度用；
      v0.175 起走 _index_asof 快速路径，选出帧与旧掩码口径逐位一致）

    截断规则与 live 1800 链逐位对齐（已对拍验证，见 --spot-check）。
    winner_factor_study 的因子面板复用同一截断（保证两套研究口径一致）。
    """
    pre = df_full.iloc[: i + 1]
    df = pre.tail(ec.OHLCV_LOAD_BARS).reset_index(drop=True)
    df_long = pre.tail(ec.OHLCV_LOAD_BARS_LONG).reset_index(drop=True)
    entry_date = str(df_full["date"].iloc[i])[:10]
    return df, df_long, _index_asof(index_full, entry_date)


def _frame_digest(df: pd.DataFrame) -> bytes:
    """df 全内容 blake2b-16 摘要：形状 + 列名 + dtype + 逐列底层字节（object 列走 repr 拼接）。

    同内容 ⇒ 同摘要 ⇒ compute_metrics（确定性纯函数）输出逐位相同；内容/形状/dtype
    有任何差异 ⇒ 摘要不同（16 字节摘要碰撞概率 ~2⁻¹²⁸/帧对，远低于内存位翻转率，
    且下游钉测逐字段对拍兜底）。
    """
    h = hashlib.blake2b(digest_size=16)
    h.update(str(df.shape).encode())
    for name in df.columns:
        col = df[name]
        h.update(str(name).encode())
        h.update(str(col.dtype).encode())
        arr = col.to_numpy()
        if arr.dtype == object:
            h.update("\x1f".join(map(repr, arr.tolist())).encode())
        else:
            h.update(np.ascontiguousarray(arr).tobytes())
    return h.digest()


def _asof_candidate_uncached(
    df_full: pd.DataFrame, index_full: pd.DataFrame, i: int, code: str
) -> dict[str, Any]:
    """asof_candidate 的无缓存原路（旧版逐笔直算；等价性钉测的对照路径）。"""
    df, df_long, index_asof = asof_frames(df_full, index_full, i)
    return ec.compute_metrics(df, index_asof, code=code, df_long=df_long)


def asof_candidate(
    df_full: pd.DataFrame, index_full: pd.DataFrame, i: int, code: str
) -> dict[str, Any]:
    """信号日（df_full 第 i 根）的 as-of cand（= ec.compute_metrics 输出），带内容键去重缓存。

    截断/计算走 asof_frames + ec.compute_metrics 原路，与旧版逐笔直算**逐位一致**；
    缓存命中（同一票同一信号日的三层截断帧内容摘要相同）时返回同一份 cand。
    ⚠️ 返回值是跨调用共享对象：消费方（sc.technical_score /
    winner_factor_study.build_factor_panel）均为纯读，调用方**不得**改写返回 dict。
    """
    df, df_long, index_asof = asof_frames(df_full, index_full, i)
    compute = ec.compute_metrics  # 现取：monkeypatch 替身有独立键空间（见段头注释）
    key = (
        code,
        compute,
        _frame_digest(df),
        _frame_digest(df_long),
        _frame_digest(index_asof),
    )
    cand = _CAND_CACHE.get(key)
    if cand is not None:
        _CAND_CACHE.move_to_end(key)
        return cand
    cand = compute(df, index_asof, code=code, df_long=df_long)
    _CAND_CACHE[key] = cand
    while len(_CAND_CACHE) > _CAND_CACHE_MAX:
        _CAND_CACHE.popitem(last=False)  # LRU 逐出最久未用
    return cand


def asof_technical_score(
    df_full: pd.DataFrame,
    index_full: pd.DataFrame,
    i: int,
    code: str,
) -> tuple[int, str, dict]:
    """信号日（df_full 第 i 根）的 live 技术分（as-of 口径，截断见 asof_frames）。

    返回 (score, level, factor_contrib)，权重 = DEFAULT_TECH_WEIGHTS。
    cand 走 asof_candidate 内容键缓存（v0.175，逐位不变）。
    """
    cand = asof_candidate(df_full, index_full, i, code)
    return sc.technical_score(cand, None)


# ---------------------------------------------------------------------------
# 回测主流程
# ---------------------------------------------------------------------------


def run_study(
    codes: list[str],
    regime: dict[str, str],
    index_df: pd.DataFrame,
    *,
    cost_bps: float = COST_BPS,
    stop_pct: float = STOP_PCT_DEFAULT,
    cost_zone_bars: int = 0,
    cost_zone_pct: float = 3.0,
    breakeven_trigger: float = 0.0,
    scale_out_frac: float = 0.0,
    end: Optional[str] = None,
    trade_hook: Optional[Any] = None,
    entry_gate_factory: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """逐股流式：加载全历史 → evaluate_trades（j_low gate + baseline scorer +
    仅做多区间 + BBI 跌破2根止盈 + pct 初始止损 [+ cost_zone] [+ 保本/分批止盈]）
    → 信号日 as-of 技术分。

    ``breakeven_trigger``/``scale_out_frac``（v0.118）：保本止损触发浮盈 /
    BBI 上方双中大阳分批止盈比例，0=关（旧行为逐位不变）。
    ``end``（可选，YYYY-MM-DD）：K 线截到该日（跨窗复核用；None=全历史）。
    ``trade_hook``（可选）：``hook(df, index_df, i, code) -> dict``，返回键并入该笔
    记录（替代默认的 tech_score/tech_level/factor_contrib 三键）——
    winner_factor_study 用它注入因子面板，回测引擎/截断口径零重复。
    ``entry_gate_factory``（可选，v0.155）：``factory(code) -> gate(df_slice, precomputed)``
    逐股构造进场门槛（resonance3_study 的三面共振 gate 需要 per-code 的 PIT 财务
    闭包）；None = 默认 j_low gate（旧行为逐位不变）。
    """
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    trades: list[dict[str, Any]] = []
    t0 = time.time()
    for k, code in enumerate(codes):
        try:
            raw = local_tdx_data.get_ohlcv_table(code, count=100000)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 加载 {code} 失败: {exc}", file=sys.stderr)
            continue
        if raw is None or not len(raw):
            continue
        # ⚠️ date 列**不得**转字符串：enrich 侧若干检测器（周线 resample 等）
        # 依赖 datetime64 的 date 列，转 str 会炸（冒烟实测）。日期串比较一律
        # 用 astype(str).str[:10] 现算，不改列本体。
        df = raw.sort_values("date").reset_index(drop=True)
        if end:
            df = df[df["date"].astype(str).str[:10] <= end].reset_index(drop=True)
            if not len(df):
                continue
        gate = entry_gate_factory(code) if entry_gate_factory else bf.j_low_gate
        code_trades = bf.evaluate_trades(
            {code: df},
            scorer=bf.SCORERS["baseline"],  # 恒「可买」——进场只由 j_low gate 决定
            entry_gate=gate,  # 信号 = 日 KDJ 的 J<13（或 factory 给的复合 gate）
            amv_regime=regime,  # 只在 0AMV 做多区间进场
            bbi_exit_consec=2,  # BBI 止盈：站上后连破 2 根收盘清仓
            stop_mode=STOP_MODE,  # pct 固定空间止损（昨晚基线=50 宽设≈无止损）
            stop_pct=stop_pct,
            cost_bps=cost_bps,
            time_stop_bars=0,
            scale_out_frac=scale_out_frac,  # BBI 上方双中大阳分批止盈（R9 档 0.5）
            breakeven_trigger=breakeven_trigger,  # 盈转亏保本止损（本轮 0.05）
            trail_pct=0.0,
            cost_zone_bars=cost_zone_bars,  # 「不涨就拍」（R10 冠军组合 pct_05_amv_cz3 的出场侧）
            cost_zone_pct=cost_zone_pct,
        )
        if not code_trades:
            continue
        dates = df["date"].astype(str).str[:10].tolist()
        date2i = {d: i for i, d in enumerate(dates)}
        for tr in code_trades:
            i = date2i.get(tr["entry_date"])
            if i is None:
                continue
            try:
                if trade_hook is not None:
                    extra = trade_hook(df, index_df, i, code)
                else:
                    score, level, contrib = asof_technical_score(df, index_df, i, code)
                    extra = {
                        "tech_score": score,
                        "tech_level": level,
                        "factor_contrib": contrib,
                    }
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[WARN] {code} {tr['entry_date']} 技术分计算失败: {exc}",
                    file=sys.stderr,
                )
                continue
            trades.append({**tr, **extra})
        if (k + 1) % 25 == 0:
            print(
                f"[INFO] 已处理 {k + 1}/{len(codes)} 只，累计 {len(trades)} 笔，"
                f"耗时 {time.time() - t0:.0f}s",
                file=sys.stderr,
            )
    return trades


# ---------------------------------------------------------------------------
# 统计与报告
# ---------------------------------------------------------------------------


def _per_interval_stats(
    trades: list[dict[str, Any]],
    intervals: list[tuple[str, str]],
    top_frac: float,
) -> list[dict[str, Any]]:
    """分区间 top/bottom 分布 + 相关性（无样本区间跳过）。"""
    per_interval: list[dict[str, Any]] = []
    for idx, (s, e) in enumerate(intervals):
        ts = [t for t in trades if t["interval_idx"] == idx]
        if not ts:
            continue
        top, bottom = split_top_frac(ts, top_frac)
        per_interval.append(
            {
                "interval": [s, e],
                "n": len(ts),
                "corr": correlations(ts),
                "top50_score_dist": dist_stats([t["tech_score"] for t in top]),
                "bottom50_score_dist": dist_stats([t["tech_score"] for t in bottom]),
                "top50_avg_ret": round(statistics.mean([t["ret"] for t in top]), 4),
                "bottom50_avg_ret": round(
                    statistics.mean([t["ret"] for t in bottom]), 4
                )
                if bottom
                else None,
            }
        )
    return per_interval


def _interval_sign_consistency(per_interval: list[dict[str, Any]]) -> dict[str, Any]:
    """区间间方向一致性：各区间 Spearman 符号统计（翻转 = 本仓库的老坑）。"""
    signs = [
        (iv["corr"].get("spearman"), iv["interval"])
        for iv in per_interval
        if iv["corr"].get("spearman") is not None
    ]
    n_pos = sum(1 for s, _ in signs if s and s > 0)
    n_neg = sum(1 for s, _ in signs if s and s < 0)
    return {
        "n_intervals": len(signs),
        "n_positive": n_pos,
        "n_negative": n_neg,
        "flipped": n_pos > 0 and n_neg > 0,
    }


def _top_bottom_overall(
    trades: list[dict[str, Any]], top_frac: float
) -> dict[str, Any]:
    """全体样本 top/bottom 切分的技术分分布。"""
    top, bottom = split_top_frac(trades, top_frac)
    return {
        "top50_score_dist": dist_stats([t["tech_score"] for t in top]),
        "bottom50_score_dist": dist_stats([t["tech_score"] for t in bottom]),
    }


def build_report(
    trades: list[dict[str, Any]],
    intervals: list[tuple[str, str]],
    top_frac: float = 0.5,
) -> dict[str, Any]:
    """逐笔 → 全量统计：分区间 top/bottom 分布 + 相关性 + 分档 + 半窗核对。

    ``top_frac``（v0.118）：赢家组分位（0.5=旧行为 top50%；0.20=TOP20%）。
    """
    for t in trades:
        t["interval_idx"] = interval_of(t["entry_date"], intervals)

    per_interval = _per_interval_stats(trades, intervals, top_frac)
    realized = [t for t in trades if not str(t["reason"]).startswith("open_end")]
    return {
        "r11_warning": R11_WARNING,
        "n_trades": len(trades),
        "n_realized": len(realized),
        "n_open_end": len(trades) - len(realized),
        "intervals": [[s, e] for s, e in intervals],
        "per_interval": per_interval,
        "overall_stats": ret_stats(trades),
        "realized_stats": ret_stats(realized),
        "exit_reasons": exit_reason_dist(trades),
        "overall_corr": correlations(trades),
        "realized_corr": correlations(realized),
        "half_window": half_window_check(trades),
        "interval_sign_consistency": _interval_sign_consistency(per_interval),
        "band_stats_all": band_stats(trades),
        "band_stats_realized": band_stats(realized),
        "top_frac": top_frac,
        "top_bottom_overall": _top_bottom_overall(trades, top_frac),
        "score_dist_all": dist_stats([t["tech_score"] for t in trades]),
    }


def print_report(rep: dict[str, Any]) -> None:
    """stdout 中文摘要。"""
    print("\n" + "=" * 72)
    print("0AMV 做多区间 J<13 信号：技术分 vs BBI止盈收益 相关性研究")
    print("=" * 72)
    print(rep["r11_warning"])
    print(
        f"\n样本：{rep['n_trades']} 笔（已实现 {rep['n_realized']} / "
        f"open_end {rep['n_open_end']}），覆盖 {len(rep['intervals'])} 个做多区间"
    )
    os_, rs_ = rep["overall_stats"], rep["realized_stats"]
    print(
        f"整体收益：均收 {os_['avg_ret'] * 100:.2f}% / 胜率 {os_['win_rate'] * 100:.1f}% / "
        f"盈亏比 {os_['payoff_ratio']}（已实现：均收 {rs_['avg_ret'] * 100:.2f}% / "
        f"胜率 {rs_['win_rate'] * 100:.1f}% / 盈亏比 {rs_['payoff_ratio']}）"
    )
    print(
        "出场原因分布："
        + "，".join(
            f"{k} {v['n']}笔({v['frac'] * 100:.1f}%,均收{v['avg_ret'] * 100:.2f}%)"
            for k, v in rep["exit_reasons"].items()
        )
    )
    oc, rc = rep["overall_corr"], rep["realized_corr"]
    print(
        f"全体相关性：Spearman={oc.get('spearman')} Pearson={oc.get('pearson')} "
        f"（n={oc.get('n')}）｜剔除 open_end：Spearman={rc.get('spearman')} "
        f"（n={rc.get('n')}）"
    )
    hw = rep["half_window"]
    if "skipped" not in hw:
        print(
            f"前后半窗（切于 {hw['split_date']}）：前半 Spearman="
            f"{hw['first_half'].get('spearman')}（n={hw['first_half'].get('n')}）/ "
            f"后半 {hw['second_half'].get('spearman')}（n={hw['second_half'].get('n')}）"
            f" ⇒ 方向{'一致' if hw['consistent'] else '⚠️ 翻转'}"
        )
    isc = rep["interval_sign_consistency"]
    print(
        f"区间间一致性：{isc['n_positive']} 正 / {isc['n_negative']} 负 "
        f"（共 {isc['n_intervals']} 区间）{'⚠️ 有翻转' if isc['flipped'] else ''}"
    )
    print("\n── 技术分分档（全体 / 已实现）：档 | n | 均收% | 胜率 | 盈亏比 | 均分")
    for label, key in (("全体", "band_stats_all"), ("已实现", "band_stats_realized")):
        for b in BANDS:
            st = rep[key][b]
            if not st.get("n"):
                continue
            print(
                f"  {label} {b:>6} | {st['n']:>5} | {st['avg_ret'] * 100:>8.2f} | "
                f"{st['win_rate'] * 100:>5.1f}% | {st['payoff_ratio']} | {st['avg_score']}"
            )
    top_pct = round(rep.get("top_frac", 0.5) * 100)
    bot_pct = 100 - top_pct
    print(
        f"\n── 分区间 top-{top_pct}%（收益最好一组）vs bottom-{bot_pct}% 的技术分分布"
    )
    tb = rep["top_bottom_overall"]
    print(
        f"  全体切分：top{top_pct}% 均分 {tb['top50_score_dist'].get('mean')} / "
        f"bottom{bot_pct}% 均分 {tb['bottom50_score_dist'].get('mean')}"
    )
    print(f"区间 | n | Spearman | top{top_pct}% 均分/中位 | bottom{bot_pct}% 均分/中位")
    for iv in rep["per_interval"]:
        s, e = iv["interval"]
        td, bd = iv["top50_score_dist"], iv["bottom50_score_dist"]
        print(
            f"  {s}~{e} | {iv['n']:>4} | {iv['corr'].get('spearman')} | "
            f"{td.get('mean')}/{td.get('median')} | {bd.get('mean')}/{bd.get('median')}"
        )


# ---------------------------------------------------------------------------
# 对拍：enrich 真实落盘分 vs as-of 计算
# ---------------------------------------------------------------------------


def spot_check(date: str, n: int = 5) -> int:
    """抽 n 只票，拿 data/stock_pool/{date}_stock_pool.json 的落盘技术分，
    与 asof_technical_score（截断到同日）逐只对比。全部一致返回 0。"""
    from custos.core.paths import STOCK_POOL_DIR  # noqa: PLC0415
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    pool_path = STOCK_POOL_DIR / f"{date}_stock_pool.json"
    if not pool_path.is_file():
        print(f"⛔ 对拍需要 {pool_path}（先跑当日 1800 链）", file=sys.stderr)
        return 2
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    cands = [
        c
        for c in pool.get("candidates") or []
        if str(c.get("code", "")).startswith(("60", "00", "30"))
    ][:n]
    if not cands:
        print("⛔ 池里没有可对拍的候选", file=sys.stderr)
        return 2
    index_df = (
        local_tdx_data.get_ohlcv_table(INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )
    bad = 0
    for c in cands:
        code = str(c["code"])
        live = (c.get("score_detail") or {}).get("technical_score")
        df = (
            local_tdx_data.get_ohlcv_table(code, count=100000)
            .sort_values("date")
            .reset_index(drop=True)
        )
        hits = df.index[df["date"].astype(str).str[:10] == date].tolist()
        if not hits:
            print(f"  {code} 当日无K线，跳过")
            continue
        score, level, _ = asof_technical_score(df, index_df, hits[-1], code)
        ok = live == score
        bad += 0 if ok else 1
        print(
            f"  {code} 落盘={live} as-of={score}（{level}） {'OK' if ok else '⛔ 不一致'}"
        )
    print(f"对拍 {len(cands)} 只：{'全部一致' if not bad else f'{bad} 只不一致'}")
    return 0 if not bad else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--max-stocks", type=int, default=400, help="宇宙抽样只数（0=全市场）"
    )
    ap.add_argument("--seed", type=int, default=0, help="抽样种子（可复现）")
    ap.add_argument(
        "--start",
        default="2010-01-01",
        help="0AMV regime 起点（指南针数据起点约 2010；信号只在做多区间产生）",
    )
    ap.add_argument("--cost-bps", type=float, default=COST_BPS, help="往返成本基点")
    ap.add_argument(
        "--stop-pct",
        type=float,
        default=STOP_PCT_DEFAULT,
        help="初始止损 %%（pct 固定空间；默认 5 = R10「5%%是崖」下沿最优档；"
        "50 ≈ 无止损，仅用于复现昨晚基线口径）",
    )
    ap.add_argument(
        "--cost-zone-bars",
        type=int,
        default=0,
        help="「不涨就拍」：进场 N+1 根仍三维度平淡则平仓（默认 0=关；"
        "3 = R10 冠军组合 pct_05_amv_cz3 的出场侧）",
    )
    ap.add_argument(
        "--cost-zone-pct",
        type=float,
        default=3.0,
        help="脱离成本区的涨幅阈值 %%（默认 3，同引擎默认）",
    )
    ap.add_argument(
        "--breakeven",
        type=float,
        default=0.0,
        help="保本止损：浮盈达该比例后止损上移到成本价（breakeven_trigger；"
        "默认 0=关；本轮 0.05，v0.118）",
    )
    ap.add_argument(
        "--scale-out",
        type=float,
        default=0.0,
        help="分批止盈比例：BBI 上方连续两根中大阳线减仓比例（scale_out_frac；"
        "默认 0=关；R9 档 0.5）",
    )
    ap.add_argument(
        "--top-frac",
        type=float,
        default=0.5,
        help="赢家组分位（默认 0.5=旧行为 top50%%；本轮 0.20=TOP20%%，v0.118）",
    )
    ap.add_argument(
        "--out",
        default="",
        help="结果 JSON 路径（默认 artifacts/logs/score_return_study/）",
    )
    ap.add_argument("--spot-check", default="", help="对拍模式：给定日期 YYYY-MM-DD")
    ap.add_argument("--n", type=int, default=5, help="对拍抽样只数")
    return ap


def main(argv: Optional[list] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    if not 0 < args.top_frac < 1:
        ap.error("--top-frac 必须在 (0, 1) 开区间（0.5=top50%，0.20=top20%）")

    if args.spot_check:
        return spot_check(args.spot_check, args.n)

    regime = bf.load_amv_regime(since=args.start)
    if not regime:
        ap.error("读不到指南针 0AMV 数据（compass_amv）；请在有指南针的机器运行")
    intervals = long_intervals(regime)
    print(
        f"[INFO] 0AMV regime {len(regime)} 个交易日，做多区间 {len(intervals)} 段",
        file=sys.stderr,
    )

    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    base = local_tdx_data.list_local_vipdoc_codes()
    codes = bf.sample_codes(base, args.max_stocks, args.seed)
    print(
        f"[INFO] universe=local_vipdoc 共 {len(base)} 只，取 {len(codes)} 只（seed={args.seed}）",
        file=sys.stderr,
    )
    index_df = (
        local_tdx_data.get_ohlcv_table(INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )

    trades = run_study(
        codes,
        regime,
        index_df,
        cost_bps=args.cost_bps,
        stop_pct=args.stop_pct,
        cost_zone_bars=args.cost_zone_bars,
        cost_zone_pct=args.cost_zone_pct,
        breakeven_trigger=args.breakeven,
        scale_out_frac=args.scale_out,
    )
    if not trades:
        print("⛔ 0 笔交易——检查 regime 数据与宇宙", file=sys.stderr)
        return 1

    rep = build_report(trades, intervals, args.top_frac)
    rep["config"] = {
        "signal": "日KDJ J<13（j_low_gate，J_LOW_THRESHOLD=13.0）",
        "regime": "仅 0AMV 做多区间（compass_amv 状态机 >4%/-2.3% 粘滞）",
        "exit": "BBI 止盈：站上后连破 2 根收盘清仓；time_stop/trail 全关",
        "initial_stop": f"pct {args.stop_pct}%（50 ≈ 无止损基线口径）",
        "breakeven_trigger": args.breakeven,
        "scale_out_frac": args.scale_out,
        "cost_zone_bars": args.cost_zone_bars,
        "cost_zone_pct": args.cost_zone_pct,
        "cost_bps": args.cost_bps,
        "top_frac": args.top_frac,
        "scorer": "baseline（恒可买；进场只由 j_low gate 决定）",
        "tech_score": "live technical_score（DEFAULT_TECH_WEIGHTS，as-of 截断，已与 enrich 落盘对拍）",
        "max_stocks": args.max_stocks,
        "seed": args.seed,
        "start": args.start,
        "n_codes": len(codes),
    }
    rep["trades"] = trades

    # 文件名带出场/赢家组参数标签，与历史臂（..._s0_n400*.json）区分
    stop_tag = f"_stop{args.stop_pct:g}"
    cz_tag = f"_cz{args.cost_zone_bars}" if args.cost_zone_bars else ""
    be_tag = f"_be{args.breakeven:g}" if args.breakeven else ""
    so_tag = f"_so{args.scale_out:g}" if args.scale_out else ""
    top_tag = f"_top{round(args.top_frac * 100):g}" if args.top_frac != 0.5 else ""
    out = (
        Path(args.out)
        if args.out
        else (
            Path("artifacts/logs/score_return_study")
            / f"score_return_study_s{args.seed}_n{len(codes)}"
            f"{stop_tag}{cz_tag}{be_tag}{so_tag}{top_tag}.json"
        )
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(out, rep, big=len(trades) > 20000)
    print(f"[OK] 写出 {out}（{len(trades)} 笔）")
    print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
