# -*- coding: utf-8 -*-
"""研究：J<13 基底上加两层过滤（QSX>DKS 多头结构 + QSX/DKX 共振）的边际贡献。

> ⚠️ **R11 警示（先读）**：B1 基准在 3000 样本下**已实现口径为负期望**
> （governance/research/R11_baseline_margin_collapse.md：剔除未平仓浮盈后 −32R，
> 且仍带幸存者偏差，去偏只会更低）。本研究的收益读数**仅供相对排序**，
> 任何均收/期望数字都不得引用为策略量级预期。

研究问题（owner 2026-08-25）：
「在当前打分的基础上加两层过滤：① QSX > DKX（知行趋势线多头）；② 该股票必须与
QSX 或 DKX 共振——每次跌到 QSX 或 DKX 就反弹。止损 −12% + 盈转亏；止盈 = 连涨 2 根
大阳线部分止盈 + 跌破 QSX 清仓。」

口径（基底 = v0.93 钉死：0AMV 做多区间 + J<13 + 25bps + 400 只 seed=0 全历史）：

- **指标**：QSX/DKS 用 `core/indicators.py` 唯一实现 `qsx_series`/`dks_series`
  （QSX=EMA(EMA(C,10),10)，DKS=(MA14+MA28+MA57+MA114)/4；live 候选表 QD 与
  知行三态同口径）。
- **过滤②「共振」检测器**（`qsx_dks_resonance`，as-of 无未来函数）：一段连续
  「贴线」bar（最低价在该线 ±1% 内，且段前一日收盘在在线=从上往下回踩）之后
  ``reclaim_bars`` 根内出现收盘重新站回该线 ⇒ 记一次「回踩→反弹」事件；
  近 ``lookback``（默认 60）根内已确认事件 ≥ ``min_events``（默认 2）⇒ 共振=True。
  事件在 confirm_bar（首次站回那根）才可被后续 bar 看见——严格因果。
  两线各自检测后按时间重叠去重（同一次下跌贴到两条线只算一次）。
- **三对照臂**（同一出场配置，过滤递增，边际贡献可拆）：
  A = j_low 基底（无两层过滤）；B = j_low ∧ qsx_gt_dks；C = B ∧ 共振。
- **出场**（owner 本轮口径，**不用 BBI 连破清仓**——bbi_exit_consec=0 关闭）：
  初始止损 pct −12%（收盘判）＋ 保本止损 0.05（盈转亏，盘中判）＋
  双中大阳分批止盈 0.5（BBI 上方连续两根中大阳减一半，只减仓不退出）＋
  **跌破 QSX 清仓**（连续 ``qsx_exit_consec``=1 根收盘 < QSX ⇒ 次日开盘清，
  `simulate_b1_trade` 新通道，reason=qsx_exit）。
  同一根 bar 内优先级：① 止损系（保本先判且盘中）→ ②a 分批止盈（减仓）→
  ②b BBI 连破（本轮关）→ ②c QSX 跌破清仓 → ③ 成本区（关）→ ④ 时间止损（关）。
  **出场族切换**（v0.163，R23 后续对照）：``--bbi-exit-consec 2
  --qsx-exit-consec 0`` 即「止盈=分批止盈+BBI 跌破两根清仓」口径（止损/保本/
  分批不变）；产物 tag 带 ``_bbi{n}`` 与 QSX 族区分。⚠️ 两出场族基线不同
  （QSX 清仓更紧更早，见 R23），跨族数字不直接比。
- **技术分**：信号日 as-of live technical_score（复用 score_return_study 截断，
  无未来函数，已与 enrich 落盘对拍），用于 TOP20% 赢家分布对照。

统计：各臂 笔数/胜率（Wilson95）/盈亏比/均收/expectancy_R、出场原因分布、
TOP20% 赢家 vs 其余的技术分分布、前后半窗一致性；``--compare`` 汇总三臂边际贡献
（B−A = 过滤①的值，C−B = 过滤②的值）与共振命中率（信号级 n_C/n_B、票级 codes 比）。
过滤后笔数缩，Wilson 区间如实标。

## v2 口径（owner 2026-08-26 逐条定稿，第二轮；v1 因命中率 99.2%≈无过滤被证伪）

**成立条件**（近 60 根内 ≥2 次「干净的跌线反弹」），一次干净反弹 =
`qsx_dks_resonance_v2` 逐条判定：

1. 碰线：当日最低价 ≤ QSX 或 DKX（**真碰线**，不是 ±1% 贴近），且前一日收盘在在线
   （从上往下碰，线下运行不算）；
2. 线下收盘 ≤1 根：碰线日收破线 ⇒ 次日必须收回（收盘 > 线），连破 2 根即出局；
3. 收回：收盘 > 该线（reclaim_bar，碰线当日收在线上即当日收回）；
4. 反弹幅度：自触线最低点（min low[触线..收回]）起，随后 N=5 根内（窗 [触线, 触线+5]）
   最高价反弹 ≥3%；
5. 缩量：触线日成交量 < 前 5 日均量。

confirm_bar = max（收回根, 反弹达标根）——之前不可见，严格因果。

**排除条件**（优先级高于成立条件）：信号日处于「跌破 QSX 或 DKX 后未收复」状态
（碰线且收在线下 ⇒ 进入，收盘 > 线 ⇒ 解除；两线任一则排除）。
owner 原话：「触线后没有站上反而继续下跌，说明 QSX/DKX 空头有效，需要排除」。

**臂**（出场不变）：A 基底 / B +QSX>DKS / C +QSX>DKS+共振v2 / C'（=Cp）j_low+共振v2
（拆共振脱离结构过滤的独立贡献）。A/B 复用第一轮产物（宇宙 5549→抽样 400 逐码核对
一致，配置逐位相同）。

**预注册判据**（写在跑数之前）：C/B 或 C'/A 胜率提升 >3pp 且盈亏比不降 + 半窗一致；
命中率报告（v2 应显著低于 v1 的 99.2%）。

CLI::

    uv run python src/custos/research/qsx_resonance_study.py --arm A --max-stocks 400 --seed 0
    uv run python src/custos/research/qsx_resonance_study.py --arm B --max-stocks 400 --seed 0
    uv run python src/custos/research/qsx_resonance_study.py --arm C --max-stocks 400 --seed 0
    uv run python src/custos/research/qsx_resonance_study.py --arm Cp --max-stocks 400 --seed 0
    uv run python src/custos/research/qsx_resonance_study.py --compare armA.json armB.json armC.json armCp.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from custos.core import indicators as ind  # noqa: E402
from custos.research import backtest_factors as bf  # noqa: E402
from custos.research import score_return_study as srs  # noqa: E402

# 共振检测器默认参数（owner 口径：近 60 根、贴线 ±1%、N 根内站回、≥2 次）
LOOKBACK = 60
TOUCH_TOL = 0.01
RECLAIM_BARS = 5
MIN_EVENTS = 2

# v2 口径默认参数（owner 2026-08-26 定稿）：反弹窗 5 根、反弹幅度 ≥3%、缩量 = 触线日量
# < 前 5 日均量、近 60 根内 ≥2 次干净反弹；排除 = 跌破未收复状态
BOUNCE_BARS = 5
BOUNCE_PCT = 0.03
VOL_MA = 5

# 预注册判据（写在跑数之前，compare 报告头部照原文展示）
PREREG_CRITERIA = (
    "预注册判据：C/B 或 C'/A 胜率提升 >3pp 且盈亏比不降 + 半窗一致；"
    "命中率报告（v2 应显著低于 v1 的 99.2%）"
)

# 出场固定口径（owner 本轮）：止损 −12% + 保本 0.05 + 双中大阳分批 0.5 + 跌破 QSX 清仓
STOP_PCT = 12.0
BREAKEVEN = 0.05
SCALE_OUT = 0.5
QSX_EXIT_CONSEC = 1
COST_BPS = 25.0
TOP_FRAC = 0.2

ARMS = ("A", "B", "C", "Cp")
ARM_DESC = {
    "A": "j_low 基底（0AMV做多 ∧ J<13，无两层过滤）",
    "B": "j_low ∧ qsx_gt_dks（过滤① 知行多头结构）",
    "C": "j_low ∧ qsx_gt_dks ∧ 共振（过滤①+② 全过滤）",
    "Cp": "C' = j_low ∧ 共振（过滤② 独立贡献，不要求 QSX>DKS）",
}


# ---------------------------------------------------------------------------
# 纯函数段（钉测覆盖）
# ---------------------------------------------------------------------------


def _line_episodes(
    low: np.ndarray,
    close: np.ndarray,
    line: np.ndarray,
    tol: float,
    reclaim_bars: int,
) -> list[tuple[int, int, int]]:
    """单条线的「回踩→站回」事件列表 [(t_start, t_end, confirm_bar), ...]。

    事件 = 一段连续「贴线」bar（``|low/line − 1| ≤ tol``，且段前一日收盘在在线
    =从上往下回踩、不是线下贴线），段内或段后 ``reclaim_bars`` 根内出现
    收盘重新站回该线 ⇒ confirm_bar = 首次站回的 bar（含段内当日——盘中踩线
    收回线上也算反弹）；窗口内没站回 = 贴线假摔，丢弃。
    """
    n = len(close)
    events: list[tuple[int, int, int]] = []
    i = 1  # 段前一日收盘要在在线 ⇒ 从 1 开始
    while i < n:
        lv = line[i]
        if lv == lv and abs(low[i] / lv - 1.0) <= tol:  # 贴线
            prev = line[i - 1]
            if prev == prev and close[i - 1] >= prev:  # 从上往下回踩
                t_start = i
                while (
                    i + 1 < n
                    and line[i + 1] == line[i + 1]
                    and abs(low[i + 1] / line[i + 1] - 1.0) <= tol
                ):
                    i += 1
                t_end = i
                confirm = None
                for u in range(t_end, min(t_end + reclaim_bars, n - 1) + 1):
                    if line[u] == line[u] and close[u] > line[u]:
                        confirm = u
                        break
                if confirm is not None:
                    events.append((t_start, t_end, confirm))
        i += 1
    return events


def qsx_dks_resonance(
    close: pd.Series,
    low: pd.Series,
    qsx: pd.Series,
    dks: pd.Series,
    lookback: int = LOOKBACK,
    tol: float = TOUCH_TOL,
    reclaim_bars: int = RECLAIM_BARS,
    min_events: int = MIN_EVENTS,
) -> np.ndarray:
    """过滤②「QSX/DKX 共振」布尔序列（as-of 无未来函数）。

    bar i 为 True ⟺ 近 ``lookback`` 根内（事件起点 ≥ i−lookback+1）已确认
    （confirm_bar ≤ i）的「回踩 QSX 或 DKS → N 根内站回」事件 ≥ ``min_events``。
    两线事件按时间重叠去重：后事件起点落在前一事件的贴线段内 ⇒ 同一次下跌，只算一次。
    """
    c = close.astype(float).to_numpy()
    lo = low.astype(float).to_numpy()
    ev = _line_episodes(lo, c, qsx.astype(float).to_numpy(), tol, reclaim_bars)
    ev += _line_episodes(lo, c, dks.astype(float).to_numpy(), tol, reclaim_bars)
    ev.sort(key=lambda e: (e[0], e[1]))
    dedup: list[tuple[int, int, int]] = []
    for e in ev:
        if dedup and e[0] <= dedup[-1][1]:  # 与上一事件贴线段重叠 ⇒ 同一次下跌
            continue
        dedup.append(e)
    n = len(c)
    cnt = np.zeros(n, dtype=int)
    for t_start, _t_end, confirm in dedup:
        hi = min(
            t_start + lookback - 1, n - 1
        )  # 事件对 [confirm, t_start+lookback-1] 可见
        if confirm <= hi:
            cnt[confirm : hi + 1] += 1
    return cnt >= min_events


# ---------------------------------------------------------------------------
# v2 口径（owner 2026-08-26 逐条定稿；v1 因 99.2% 命中≈无过滤被证伪，保留复现）
# ---------------------------------------------------------------------------


def _line_episodes_v2(
    low: np.ndarray,
    close: np.ndarray,
    high: np.ndarray,
    volume: np.ndarray,
    line: np.ndarray,
    bounce_bars: int,
    bounce_pct: float,
    vol_ma: int,
) -> list[tuple[int, int]]:
    """单条线的「干净的跌线反弹」事件列表 [(t_touch, confirm_bar), ...]（v2 口径）。

    五要素逐条（owner 定稿，不许自由改动）：
    ① 碰线 ``low[t] ≤ line[t]`` 且前一日收盘在在线（从上往下碰，线下运行不算）；
    ② 线下收盘 ≤1 根：``close[t] ≤ line[t]`` 则必须 ``close[t+1] > line[t+1]``
       （reclaim=t+1），连破 2 根即无效；碰线当日收在线上即当日收回（reclaim=t）；
    ③ 收回 = reclaim_bar（收盘 > 线，严格大于）；
    ④ 反弹幅度：``min_low = min(low[t..reclaim])``，窗 ``[t, t+bounce_bars]`` 内
       首个 ``high[u] ≥ min_low × (1+bounce_pct)`` ⇒ bounce_bar；缺 ⇒ 无效；
    ⑤ 缩量：``volume[t] < mean(volume[t-vol_ma..t-1])``（t < vol_ma 无前窗 ⇒ 无效）。
    confirm_bar = max(reclaim_bar, bounce_bar)——之前不可见，严格因果。
    """
    n = len(close)
    events: list[tuple[int, int]] = []
    for t in range(max(1, vol_ma), n):  # ①要前一日收盘、⑤要前 vol_ma 根量
        lv = line[t]
        if lv != lv or not (low[t] <= lv):  # ① 碰线（真碰，无容差）
            continue
        prev = line[t - 1]
        if prev != prev or close[t - 1] <= prev:  # 前一日须收在在线（从上往下）
            continue
        if not volume[t] < float(np.mean(volume[t - vol_ma : t])):  # ⑤ 缩量
            continue
        if close[t] > lv:  # ③ 当日收回（线下收盘 0 根）
            reclaim = t
        elif t + 1 < n and line[t + 1] == line[t + 1] and close[t + 1] > line[t + 1]:
            reclaim = t + 1  # ② 线下收盘恰好 1 根后收回
        else:
            continue  # 连破 ≥2 根 ⇒ 不是干净反弹
        min_low = float(np.min(low[t : reclaim + 1]))  # 触线最低点
        bounce_bar = None
        for u in range(t, min(t + bounce_bars, n - 1) + 1):  # ④ 随后 N 根内
            if high[u] >= min_low * (1 + bounce_pct):
                bounce_bar = u
                break
        if bounce_bar is None:
            continue
        events.append((t, max(reclaim, bounce_bar)))
    return events


def _unrecovered_line(
    low: np.ndarray, close: np.ndarray, line: np.ndarray
) -> np.ndarray:
    """逐 bar「跌破未收复」状态（v2 排除条件）：碰线且收在线下 ⇒ 进入；收盘 > 线 ⇒ 解除。

    owner 原话：「触线后没有站上反而继续下跌，说明 QSX/DKX 空头有效，需要排除」。
    线未成形（NaN）根不判。
    """
    n = len(close)
    out = np.zeros(n, dtype=bool)
    broken = False
    for i in range(n):
        lv = line[i]
        if lv != lv:  # NaN 守卫
            continue
        if close[i] > lv:
            broken = False
        elif low[i] <= lv:
            broken = True
        out[i] = broken
    return out


def qsx_dks_resonance_v2(
    close: pd.Series,
    low: pd.Series,
    high: pd.Series,
    volume: pd.Series,
    qsx: pd.Series,
    dks: pd.Series,
    lookback: int = LOOKBACK,
    bounce_bars: int = BOUNCE_BARS,
    bounce_pct: float = BOUNCE_PCT,
    vol_ma: int = VOL_MA,
    min_events: int = MIN_EVENTS,
) -> tuple[np.ndarray, np.ndarray]:
    """过滤② v2「干净共振」：返回 ``(hit, excluded)`` 两个布尔序列（as-of 无未来函数）。

    - ``hit[i]``：近 ``lookback`` 根内（触线根 ≥ i−lookback+1）已确认
      （confirm_bar ≤ i）的干净跌线反弹事件 ≥ ``min_events``；
      两线事件按触线段重叠去重（同一次下跌碰两线只算一次）。
    - ``excluded[i]``：QSX 或 DKS 任一处于「跌破未收复」状态。
    进场用 ``hit & ~excluded``（排除条件优先级高于成立条件，owner 定）。
    """
    c = close.astype(float).to_numpy()
    lo = low.astype(float).to_numpy()
    hi = high.astype(float).to_numpy()
    vol = volume.astype(float).to_numpy()
    qs = qsx.astype(float).to_numpy()
    dk = dks.astype(float).to_numpy()
    ev = _line_episodes_v2(lo, c, hi, vol, qs, bounce_bars, bounce_pct, vol_ma)
    ev += _line_episodes_v2(lo, c, hi, vol, dk, bounce_bars, bounce_pct, vol_ma)
    ev.sort(key=lambda e: (e[0], e[1]))
    dedup: list[tuple[int, int]] = []
    for e in ev:
        if dedup and e[0] <= dedup[-1][0]:  # 同一根触线碰两线 ⇒ 同一次下跌
            continue
        dedup.append(e)
    n = len(c)
    cnt = np.zeros(n, dtype=int)
    for t_touch, confirm in dedup:
        hi_i = min(t_touch + lookback - 1, n - 1)  # 事件对 [confirm, t+lookback-1] 可见
        if confirm <= hi_i:
            cnt[confirm : hi_i + 1] += 1
    hit = cnt >= min_events
    excluded = _unrecovered_line(lo, c, qs) | _unrecovered_line(lo, c, dk)
    return hit, excluded


def wilson_ci(
    wins: int, n: int, z: float = 1.96
) -> tuple[Optional[float], Optional[float]]:
    """胜率 Wilson 置信区间（小样本不放水；n=0 返回 (None, None)）。"""
    if n <= 0:
        return (None, None)
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


def arm_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """单臂整体指标：ret_stats + expectancy_R + 胜率 Wilson95。"""
    st = srs.ret_stats(trades)
    if not st.get("n"):
        return st
    rmults = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    st["expectancy_R"] = round(statistics.mean(rmults), 3) if rmults else None
    wins = sum(1 for t in trades if t["ret"] > 0)
    lo, hi = wilson_ci(wins, len(trades))
    st["win_rate_wilson95"] = [lo, hi]
    st["n_codes"] = len({t["code"] for t in trades})
    return st


def half_window_ret(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """前后半窗一致性（收益口径）：按 entry_date 中位切半，各算 arm_stats。

    （R10/R4 教训：edge 集中在单一 regime、前后半窗翻转是本仓库反复出现的坑。）
    """
    if len(trades) < 6:
        return {"n": len(trades), "skipped": "样本不足(<6)"}
    dates = sorted(t["entry_date"] for t in trades)
    mid = dates[len(dates) // 2]
    first = [t for t in trades if t["entry_date"] <= mid]
    second = [t for t in trades if t["entry_date"] > mid]
    s1, s2 = arm_stats(first), arm_stats(second)
    return {
        "split_date": mid,
        "first_half": s1,
        "second_half": s2,
        # ⚠️ 必须加括号：不加会被 Python 链式比较解析成
        # ``a > 0 and 0 == b and b > 0``（v0.120 曾因此把「同号」误报成翻转，
        # 该轮三臂「半窗翻转」结论以 v0.124 修正重算为准）。
        "consistent": ((s1.get("avg_ret") or 0) > 0) == ((s2.get("avg_ret") or 0) > 0),
    }


def build_arm_report(
    trades: list[dict[str, Any]], arm: str, top_frac: float = TOP_FRAC
) -> dict[str, Any]:
    """单臂全量统计：整体指标 + 出场原因 + TOP20% 赢家技术分对照 + 半窗。"""
    realized = [t for t in trades if not str(t["reason"]).startswith("open_end")]
    top, bottom = srs.split_top_frac(trades, top_frac)
    top_pct = round(top_frac * 100)
    return {
        "r11_warning": srs.R11_WARNING,
        "arm": arm,
        "arm_desc": ARM_DESC[arm],
        "n_trades": len(trades),
        "n_realized": len(realized),
        "overall_stats": arm_stats(trades),
        "realized_stats": arm_stats(realized),
        "exit_reasons": srs.exit_reason_dist(trades),
        "half_window": half_window_ret(trades),
        "top_frac": top_frac,
        f"top{top_pct}_score_dist": srs.dist_stats([t["tech_score"] for t in top]),
        "rest_score_dist": srs.dist_stats([t["tech_score"] for t in bottom]),
        f"top{top_pct}_avg_ret": (
            round(statistics.mean([t["ret"] for t in top]), 4) if top else None
        ),
        "score_dist_all": srs.dist_stats([t["tech_score"] for t in trades]),
    }


def compare_arms(reps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """四臂对照：边际贡献（B−A=过滤①，C−B=过滤②，Cp−A=共振独立）+ 命中率 + 预注册判读。"""
    out: dict[str, Any] = {
        "r11_warning": srs.R11_WARNING,
        "preregistered": PREREG_CRITERIA,
        "arms": {},
    }
    for arm in ARMS:
        rep = reps.get(arm)
        if rep is None:
            continue
        st = rep["overall_stats"]
        # 半窗优先用逐笔重算（v0.120 落盘的 consistent 受链式比较 bug 污染，见 v0.124 勘误）
        hw = half_window_ret(rep["trades"]) if rep.get("trades") else rep["half_window"]
        out["arms"][arm] = {
            "desc": rep["arm_desc"],
            "n_trades": rep["n_trades"],
            "n_codes": st.get("n_codes"),
            "win_rate": st.get("win_rate"),
            "win_rate_wilson95": st.get("win_rate_wilson95"),
            "payoff_ratio": st.get("payoff_ratio"),
            "avg_ret": st.get("avg_ret"),
            "expectancy_R": st.get("expectancy_R"),
            "exit_reasons": rep["exit_reasons"],
            "half_window_consistent": hw.get("consistent"),
        }
    a, b, c, cp = (out["arms"].get(x) for x in ARMS)

    def _delta(x: Optional[dict], y: Optional[dict], key: str) -> Optional[float]:
        if not x or not y or x.get(key) is None or y.get(key) is None:
            return None
        return round(x[key] - y[key], 4)

    def _marginal(x: Optional[dict], y: Optional[dict]) -> dict[str, Any]:
        return {
            k: _delta(x, y, k)
            for k in ("win_rate", "payoff_ratio", "avg_ret", "expectancy_R")
        }

    def _rate(x: Optional[dict], y: Optional[dict]) -> dict[str, Any]:
        if not x or not y:
            return {"signal_rate": None, "code_rate": None}
        return {
            "signal_rate": (
                round(x["n_trades"] / y["n_trades"], 4) if y["n_trades"] else None
            ),
            "code_rate": (
                round(x["n_codes"] / y["n_codes"], 4) if y["n_codes"] else None
            ),
        }

    def _prereg(x: Optional[dict], y: Optional[dict]) -> Optional[dict[str, Any]]:
        """预注册判读：胜率提升 >3pp 且盈亏比不降 + 半窗一致。"""
        if not x or not y:
            return None
        wr_d = _delta(x, y, "win_rate")
        pf_d = _delta(x, y, "payoff_ratio")
        checks = {
            "win_rate_+3pp": wr_d is not None and wr_d > 0.03,
            "payoff_not_worse": pf_d is not None and pf_d >= 0,
            "half_window_consistent": x.get("half_window_consistent") is True,
        }
        return {
            **checks,
            "win_rate_delta": wr_d,
            "payoff_delta": pf_d,
            "pass": all(checks.values()),
        }

    if b and a:
        out["marginal_filter1_B_minus_A"] = _marginal(b, a)
    if c and b:
        out["marginal_filter2_C_minus_B"] = _marginal(c, b)
        out["resonance_hit_C_over_B"] = _rate(c, b)
        out["prereg_C_over_B"] = _prereg(c, b)
    if cp and a:
        out["marginal_resonance_only_Cp_minus_A"] = _marginal(cp, a)
        out["resonance_hit_Cp_over_A"] = _rate(cp, a)
        out["prereg_Cp_over_A"] = _prereg(cp, a)
    return out


# ---------------------------------------------------------------------------
# 回测主流程
# ---------------------------------------------------------------------------


def _zhixing_arrays(
    df: pd.DataFrame,
    lookback: int,
    tol: float,
    reclaim_bars: int,
    min_events: int,
    resonance_version: str = "v2",
    bounce_bars: int = BOUNCE_BARS,
    bounce_pct: float = BOUNCE_PCT,
    vol_ma: int = VOL_MA,
    no_exclusion: bool = False,
) -> dict[str, np.ndarray]:
    """逐股预计算知行数组（各算一次，gate 点查询；切片是前缀 ⇒ 下标对齐）。

    返回 ``{"gt", "res", "hit", "excluded"}``：
    ``gt`` = QSX>DKS；``res`` = 共振进场判定（v2 = hit & ~excluded，排除优先；
    ``no_exclusion=True`` 时 res = hit——仅用于「排除项贡献」对照臂）；
    ``hit``/``excluded`` 仅 v2 有实值（v1 下分别为 res 与全 False），供逐笔记录。
    EMA/rolling 与共振检测器都严格因果（bar i 只用 ≤i 的数据）⇒ 全序列预计算
    与逐切片重算逐位一致，无未来函数。
    """
    close = df["close"].astype(float)
    qsx = ind.qsx_series(close)  # 序列级唯一实现
    dks = ind.dks_series(close)  # 同上
    gt = ((qsx > dks) & qsx.notna() & dks.notna()).to_numpy()
    if resonance_version == "v1":
        res = qsx_dks_resonance(
            close,
            df["low"].astype(float),
            qsx,
            dks,
            lookback=lookback,
            tol=tol,
            reclaim_bars=reclaim_bars,
            min_events=min_events,
        )
        return {"gt": gt, "res": res, "hit": res, "excluded": np.zeros(len(df), bool)}
    hit, excluded = qsx_dks_resonance_v2(
        close,
        df["low"].astype(float),
        df["high"].astype(float),
        df["volume"].astype(float),
        qsx,
        dks,
        lookback=lookback,
        bounce_bars=bounce_bars,
        bounce_pct=bounce_pct,
        vol_ma=vol_ma,
        min_events=min_events,
    )
    return {
        "gt": gt,
        "res": hit if no_exclusion else hit & ~excluded,
        "hit": hit,
        "excluded": excluded,
    }


def _make_gate(arm: str, zx: dict[str, np.ndarray]) -> Any:
    """四臂进场 gate（双形态 ``(df_slice, precomputed)``；切片是前缀 ⇒ i=末根下标）。

    A = j_low；B = j_low ∧ qsx_gt_dks；C = j_low ∧ qsx_gt_dks ∧ 共振；
    Cp（=C'）= j_low ∧ 共振（共振独立贡献，不要求 qsx_gt_dks）。
    """
    gt, res = zx["gt"], zx["res"]

    def _gate(df_slice: pd.DataFrame, pre: Optional[dict] = None) -> bool:
        if not bf.j_low_gate(df_slice, pre):
            return False
        if arm == "A":
            return True
        i = len(df_slice) - 1
        if arm == "Cp":
            return bool(res[i])  # 过滤②独立臂：不要求过滤①
        if not bool(gt[i]):  # 过滤①：QSX > DKS（DKS 未成形=NaN 时 False 不放行）
            return False
        if arm == "B":
            return True
        return bool(res[i])  # 过滤②：共振（v2 = hit & ~excluded）

    return _gate


def run_arm(
    codes: list[str],
    regime: dict[str, str],
    index_df: pd.DataFrame,
    arm: str,
    *,
    cost_bps: float = COST_BPS,
    stop_pct: float = STOP_PCT,
    breakeven_trigger: float = BREAKEVEN,
    scale_out_frac: float = SCALE_OUT,
    qsx_exit_consec: int = QSX_EXIT_CONSEC,
    bbi_exit_consec: int = 0,
    lookback: int = LOOKBACK,
    tol: float = TOUCH_TOL,
    reclaim_bars: int = RECLAIM_BARS,
    min_events: int = MIN_EVENTS,
    resonance_version: str = "v2",
    bounce_bars: int = BOUNCE_BARS,
    bounce_pct: float = BOUNCE_PCT,
    vol_ma: int = VOL_MA,
    no_exclusion: bool = False,
) -> list[dict[str, Any]]:
    """逐股流式：加载全历史 → 预计算知行数组 → evaluate_trades（本臂 gate +
    owner 出场口径）→ 信号日 as-of 技术分。出场配置各臂完全一致（过滤可拆）。
    ``resonance_version``：v2（默认，owner 2026-08-26 定稿六要素）/ v1（第一轮口径，
    仅复现用）。``no_exclusion``：共振判定不减排除态（仅「排除项贡献」对照用）。
    ``bbi_exit_consec``>0（v0.163）：启用 BBI 连破清仓（默认 0=关，R23 口径不变）；
    配 ``qsx_exit_consec=0`` 即「BBI 跌破两根清仓」出场族。"""
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
        # ⚠️ date 列不得转字符串（同 score_return_study 注释：enrich 检测器依赖 datetime64）
        df = raw.sort_values("date").reset_index(drop=True)
        zx = _zhixing_arrays(
            df,
            lookback,
            tol,
            reclaim_bars,
            min_events,
            resonance_version,
            bounce_bars,
            bounce_pct,
            vol_ma,
            no_exclusion,
        )
        code_trades = bf.evaluate_trades(
            {code: df},
            scorer=bf.SCORERS["baseline"],  # 恒「可买」——进场只由 gate 决定
            entry_gate=_make_gate(arm, zx),
            amv_regime=regime,  # 只在 0AMV 做多区间进场
            bbi_exit_consec=bbi_exit_consec,  # 默认 0=关（R23 口径）；2=BBI 跌破两根清仓族
            stop_mode="pct",
            stop_pct=stop_pct,  # 初始止损 −12%（收盘判）
            cost_bps=cost_bps,
            time_stop_bars=0,
            scale_out_frac=scale_out_frac,  # 双中大阳分批止盈 0.5（BBI 上方）
            breakeven_trigger=breakeven_trigger,  # 盈转亏保本 0.05（盘中判）
            trail_pct=0.0,
            cost_zone_bars=0,
            qsx_exit_consec=qsx_exit_consec,  # 跌破 QSX 清仓（次日开盘）
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
                score, level, contrib = srs.asof_technical_score(df, index_df, i, code)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[WARN] {code} {tr['entry_date']} 技术分计算失败: {exc}",
                    file=sys.stderr,
                )
                continue
            trades.append(
                {
                    **tr,
                    "tech_score": score,
                    "tech_level": level,
                    "factor_contrib": contrib,
                    "qsx_gt_dks": bool(zx["gt"][i]),
                    "resonance": bool(zx["res"][i]),
                    "resonance_hit": bool(zx["hit"][i]),  # v2：成立条件（未减排除）
                    "excluded": bool(zx["excluded"][i]),  # v2：排除态（跌破未收复）
                }
            )
        if (k + 1) % 25 == 0:
            print(
                f"[INFO] arm {arm} 已处理 {k + 1}/{len(codes)} 只，"
                f"累计 {len(trades)} 笔，耗时 {time.time() - t0:.0f}s",
                file=sys.stderr,
            )
    return trades


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def print_arm_report(rep: dict[str, Any]) -> None:
    """stdout 中文摘要（单臂）。"""
    print("\n" + "=" * 72)
    print(f"QSX/DKX 两层过滤研究 —— 臂 {rep['arm']}：{rep['arm_desc']}")
    print("=" * 72)
    print(rep["r11_warning"])
    st = rep["overall_stats"]
    if not st.get("n"):
        print("⛔ 0 笔交易")
        return
    w = st.get("win_rate_wilson95") or [None, None]
    print(
        f"\n样本：{rep['n_trades']} 笔（已实现 {rep['n_realized']}），覆盖 {st['n_codes']} 只"
        f"\n整体：均收 {st['avg_ret'] * 100:+.2f}% / 中位 {st['median_ret'] * 100:+.2f}% / "
        f"胜率 {st['win_rate'] * 100:.1f}%（Wilson95 [{w[0]},{w[1]}]）/ "
        f"盈亏比 {st['payoff_ratio']} / 期望R {st.get('expectancy_R')}"
    )
    print(
        "出场原因分布："
        + "，".join(
            f"{k} {v['n']}笔({v['frac'] * 100:.1f}%,均收{v['avg_ret'] * 100:+.2f}%)"
            for k, v in rep["exit_reasons"].items()
        )
    )
    hw = rep["half_window"]
    if "skipped" not in hw:
        f_, s_ = hw["first_half"], hw["second_half"]
        print(
            f"前后半窗（切于 {hw['split_date']}）：前半 均收 {f_['avg_ret'] * 100:+.2f}%/"
            f"胜率 {f_['win_rate'] * 100:.1f}%（n={f_['n']}）｜后半 "
            f"{s_['avg_ret'] * 100:+.2f}%/{s_['win_rate'] * 100:.1f}%（n={s_['n']}）"
            f" ⇒ 方向{'一致' if hw['consistent'] else '⚠️ 翻转'}"
        )
    top_pct = round(rep.get("top_frac", 0.2) * 100)
    td, bd = rep[f"top{top_pct}_score_dist"], rep["rest_score_dist"]
    print(
        f"TOP{top_pct}% 赢家技术分：均 {td.get('mean')} / 中位 {td.get('median')} "
        f"（n={td.get('n')}）｜其余：均 {bd.get('mean')} / 中位 {bd.get('median')} "
        f"（n={bd.get('n')}）"
    )


def print_compare(cmp: dict[str, Any]) -> None:
    """stdout 中文摘要（四臂对照 + 预注册判读）。"""
    print("\n" + "=" * 72)
    print("QSX/DKX 两层过滤研究 —— 四臂对照（B−A=过滤①，C−B=过滤②，C'−A=共振独立）")
    print("=" * 72)
    print(cmp["r11_warning"])
    print(cmp.get("preregistered", ""))
    print("\n臂 | 笔数 | 只数 | 胜率(Wilson95) | 盈亏比 | 均收 | 期望R | 半窗")
    for arm in ARMS:
        a = cmp["arms"].get(arm)
        if not a:
            continue
        w = a.get("win_rate_wilson95") or [None, None]
        hw = a.get("half_window_consistent")
        print(
            f"  {arm} | {a['n_trades']:>5} | {a['n_codes']:>4} | "
            f"{a['win_rate'] * 100:.1f}% [{w[0]},{w[1]}] | {a['payoff_ratio']} | "
            f"{a['avg_ret'] * 100:+.2f}% | {a.get('expectancy_R')} | "
            f"{'一致' if hw else ('⚠️翻转' if hw is False else '-')}"
        )
    for key, label in (
        ("marginal_filter1_B_minus_A", "过滤①（QSX>DKS）边际 B−A"),
        ("marginal_filter2_C_minus_B", "过滤②（共振）边际 C−B"),
        ("marginal_resonance_only_Cp_minus_A", "共振独立边际 C'−A"),
    ):
        m = cmp.get(key)
        if m:
            print(
                f"\n{label}：胜率 {fmt_pp(m.get('win_rate'))} / 盈亏比 "
                f"{fmt_delta(m.get('payoff_ratio'))} / 均收 {fmt_pp(m.get('avg_ret'))} / "
                f"期望R {fmt_delta(m.get('expectancy_R'))}"
            )
    for key, label in (
        ("resonance_hit_C_over_B", "共振命中率 C/B"),
        ("resonance_hit_Cp_over_A", "共振命中率 C'/A"),
    ):
        hit = cmp.get(key)
        if hit:
            print(
                f"{label}：信号级 {hit.get('signal_rate')}，票级 {hit.get('code_rate')}"
            )
    for key, label in (
        ("prereg_C_over_B", "预注册判读 C/B"),
        ("prereg_Cp_over_A", "预注册判读 C'/A"),
    ):
        p = cmp.get(key)
        if p:
            print(
                f"{label}：胜率 {fmt_pp(p.get('win_rate_delta'))}（>3pp? "
                f"{'✅' if p['win_rate_+3pp'] else '❌'}）/ 盈亏比 "
                f"{fmt_delta(p.get('payoff_delta'))}（不降? "
                f"{'✅' if p['payoff_not_worse'] else '❌'}）/ 半窗"
                f"{'✅' if p['half_window_consistent'] else '❌'} ⇒ "
                f"{'✅ 过线' if p['pass'] else '❌ 不过线'}"
            )


def fmt_pp(x: Optional[float]) -> str:
    return f"{x * 100:+.2f}pp" if x is not None else "-"


def fmt_delta(x: Optional[float]) -> str:
    return f"{x:+.3f}" if x is not None else "-"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", choices=ARMS, default="", help="跑哪一臂（A/B/C/Cp=C'）")
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
        "--stop-pct", type=float, default=STOP_PCT, help="初始止损 %%（pct）"
    )
    ap.add_argument(
        "--breakeven", type=float, default=BREAKEVEN, help="保本止损触发浮盈"
    )
    ap.add_argument(
        "--scale-out", type=float, default=SCALE_OUT, help="双中大阳分批止盈比例"
    )
    ap.add_argument(
        "--qsx-exit-consec", type=int, default=QSX_EXIT_CONSEC, help="跌破 QSX 连破根数"
    )
    ap.add_argument(
        "--bbi-exit-consec",
        type=int,
        default=0,
        help="BBI 连破清仓根数（0=关，R23 默认口径；"
        "配 --qsx-exit-consec 0 即「BBI 跌破两根清仓」出场族，v0.163）",
    )
    ap.add_argument(
        "--resonance-version",
        choices=("v1", "v2"),
        default="v2",
        help="共振口径（默认 v2 = owner 2026-08-26 六要素定稿；v1 仅复现第一轮）",
    )
    ap.add_argument("--lookback", type=int, default=LOOKBACK, help="共振回看根数")
    ap.add_argument(
        "--tol", type=float, default=TOUCH_TOL, help="贴线容差（±比例，仅 v1）"
    )
    ap.add_argument(
        "--reclaim-bars", type=int, default=RECLAIM_BARS, help="站回窗口根数（仅 v1）"
    )
    ap.add_argument(
        "--bounce-bars", type=int, default=BOUNCE_BARS, help="v2 反弹窗口根数"
    )
    ap.add_argument(
        "--bounce-pct", type=float, default=BOUNCE_PCT, help="v2 反弹幅度阈值（比例）"
    )
    ap.add_argument("--vol-ma", type=int, default=VOL_MA, help="v2 缩量均量前窗根数")
    ap.add_argument(
        "--no-exclusion",
        action="store_true",
        help="共振判定不减「跌破未收复」排除态（仅排除项贡献对照臂用）",
    )
    ap.add_argument("--min-events", type=int, default=MIN_EVENTS, help="共振最少事件数")
    ap.add_argument(
        "--top-frac", type=float, default=TOP_FRAC, help="赢家组分位（默认 0.2）"
    )
    ap.add_argument(
        "--out",
        default="",
        help="结果 JSON 路径（默认 artifacts/logs/qsx_resonance_study/）",
    )
    ap.add_argument(
        "--compare",
        nargs="+",
        metavar="ARM_JSON",
        help="对照模式：读各臂结果 JSON（臂名取自文件内 arm 字段）出边际贡献+预注册判读",
    )
    return ap


def _default_out(arm: str, seed: int, n_codes: int, args: argparse.Namespace) -> Path:
    rv = f"_r{args.resonance_version}" if arm in ("C", "Cp") else ""
    if args.no_exclusion:
        rv += "_noexcl"
    bbi = f"_bbi{args.bbi_exit_consec}" if args.bbi_exit_consec > 0 else ""
    tag = (
        f"qsx_resonance_study_arm{arm}_s{seed}_n{n_codes}"
        f"_stop{args.stop_pct:g}_be{args.breakeven:g}_so{args.scale_out:g}"
        f"_qx{args.qsx_exit_consec}{rv}{bbi}"
    )
    return Path("artifacts/logs/qsx_resonance_study") / f"{tag}.json"


def main(argv: Optional[list] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)

    if args.compare:
        reps = {}
        for path in args.compare:
            rep = json.loads(Path(path).read_text(encoding="utf-8"))
            arm = rep.get("arm") or (rep.get("config") or {}).get("arm")
            if arm not in ARMS:
                ap.error(f"{path} 里读不到臂名（arm 字段）")
            reps[arm] = rep
        cmp = compare_arms(reps)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(cmp, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[OK] 写出 {out}")
        print_compare(cmp)
        return 0

    if not args.arm:
        ap.error("必须给 --arm A/B/C/Cp（或 --compare 各臂结果 JSON）")

    regime = bf.load_amv_regime(since=args.start)
    if not regime:
        ap.error("读不到指南针 0AMV 数据（compass_amv）；请在有指南针的机器运行")

    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    base = local_tdx_data.list_local_vipdoc_codes()
    codes = bf.sample_codes(base, args.max_stocks, args.seed)
    print(
        f"[INFO] universe=local_vipdoc 共 {len(base)} 只，取 {len(codes)} 只（seed={args.seed}）",
        file=sys.stderr,
    )
    index_df = (
        local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )

    trades = run_arm(
        codes,
        regime,
        index_df,
        args.arm,
        cost_bps=args.cost_bps,
        stop_pct=args.stop_pct,
        breakeven_trigger=args.breakeven,
        scale_out_frac=args.scale_out,
        qsx_exit_consec=args.qsx_exit_consec,
        lookback=args.lookback,
        tol=args.tol,
        reclaim_bars=args.reclaim_bars,
        min_events=args.min_events,
        resonance_version=args.resonance_version,
        bounce_bars=args.bounce_bars,
        bounce_pct=args.bounce_pct,
        vol_ma=args.vol_ma,
        no_exclusion=args.no_exclusion,
        bbi_exit_consec=args.bbi_exit_consec,
    )
    if not trades:
        print(f"⛔ 臂 {args.arm} 0 笔交易——检查 regime 数据与宇宙", file=sys.stderr)
        return 1

    rep = build_arm_report(trades, args.arm, args.top_frac)
    rep["config"] = {
        "arm": args.arm,
        "signal": "日KDJ J<13（j_low_gate，J_LOW_THRESHOLD=13.0）",
        "regime": "仅 0AMV 做多区间（compass_amv 状态机 >4%/-2.3% 粘滞）",
        "filter1_qsx_gt_dks": args.arm in ("B", "C"),
        "filter2_resonance": {
            "enabled": args.arm in ("C", "Cp"),
            "version": args.resonance_version,
            "lookback": args.lookback,
            "tol": args.tol,
            "reclaim_bars": args.reclaim_bars,
            "bounce_bars": args.bounce_bars,
            "bounce_pct": args.bounce_pct,
            "vol_ma": args.vol_ma,
            "min_events": args.min_events,
            "no_exclusion": bool(args.no_exclusion),
        },
        "preregistered": PREREG_CRITERIA,
        "exit": (
            f"pct 初始止损 {args.stop_pct}%（收盘判）+ 保本 {args.breakeven}（盘中判）+ "
            f"双中大阳分批止盈 {args.scale_out}（BBI 上方）"
            + (
                f"+ 跌破 QSX 清仓（连破 {args.qsx_exit_consec} 根收盘，次日开盘）"
                if args.qsx_exit_consec > 0
                else ""
            )
            + (
                f"+ BBI 连破 {args.bbi_exit_consec} 根清仓"
                if args.bbi_exit_consec > 0
                else "；BBI 连破清仓关闭"
            )
        ),
        "cost_bps": args.cost_bps,
        "top_frac": args.top_frac,
        "scorer": "baseline（恒可买；进场只由 gate 决定）",
        "tech_score": "live technical_score（as-of 截断，复用 score_return_study）",
        "max_stocks": args.max_stocks,
        "seed": args.seed,
        "start": args.start,
        "n_codes": len(codes),
    }
    rep["trades"] = trades

    out = (
        Path(args.out)
        if args.out
        else _default_out(args.arm, args.seed, len(codes), args)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(out, rep, big=len(trades) > 20000)
    print(f"[OK] 写出 {out}（{len(trades)} 笔）")
    print_arm_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
