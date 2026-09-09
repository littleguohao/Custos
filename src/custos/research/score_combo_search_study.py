# -*- coding: utf-8 -*-
"""研究：R30 打分权重有界组合搜索（预注册 governance/research/R30_score_combo_search.md）。

> ⚠️ **R11 警示**：量级不作数，读数仅供相对排序。**R3 纪律**：单窗证据不作数，
> 须三窗（主窗/跨窗/pre2019）一致才进结论；pre2019 终审窗在 ``--final`` 前**不许看**
> （``--search`` CLI 对 pre2019 输入硬拒绝——反过拟合纪律第 4 条代码化，同 R24/R29）。

R29 证明了「证据设计的 4 个候选」在 40%/2.4 双线下没有参数稳健的窗内解；owner
（2026-09-08）质疑「组合空间并未穷举」成立，遂有本轮：在不碰 pre2019 的前提下把
证据腿池的组合空间**有界枚举**一遍（gcd 排序等价去重），用「加严筛选线」支付多重
比较的显著性税，幸存者 ≤3 进 pre2019 untouched 一次终审。⚠️ 搜索族 ⇒ 证据等级封顶
**L3−**（终审通过也带「经搜索」注记，进 live 前必须影子观察）。

搜索空间（与预注册页逐字一致）：

- 正腿池 5 条（rsi_deep_oversold / weekly_j_low / rsi_bull_div /
  macd_bottom_divergence / leader_volume），权重各 ∈ **{0, 10, 20, 30, 40}**
  （0 = 删腿，至少一条非零）
- 负腿块 6 条（rsi_strong / b1_ignition / volume_contraction /
  relative_strength_strong / macd_top_divergence / ignition）整体二态：关/开（每条 −5）
- 规模：5^5 − 1 = 3124，×2（负腿块）= 6248 原始组合；gcd 排序等价去重后
  **2851 个等价类 ×2 = 5702**（确切数 = 落盘 ``n_combos_evaluated``）

筛选线（调参双窗 = 主窗+跨窗，**两窗各自全过**；括号内为 R29 基线）：

- **R30-F1**：篮子胜率 ≥ 45%（基线 40%；+5pp 显著性税）且 > V0 篮子胜率
- **R30-F2**：篮子盈亏比 ≥ 2.6（基线 2.4；+0.2 显著性税）
- **R30-F3**：Spearman > 0 且前后半窗同正（= ``svs.judge`` 的 C1_spearman_positive）
- **R30-F4**：强档（≥60）占比 ≤ 15%（= ``scs.c5_strong_frac``；A 桶离线不可算）
- **R30-F5**：每条非零 panel 腿 ±50% 扰动，**R29 基线 pass_all**（40%/2.4/C3/C4）零翻转
- pre2019 输入 CLI **硬拒绝**（终审前不许碰）

幸存者规则（写死，不许跑数后换键）：过 F1~F5 的组合按**两窗（篮子 margin − 全样本
margin）的较小值**降序取 top 3（不足 3 个全取；0 个 ⇒ 判负收口）。终审线 = R29 基线
（篮子胜率 ≥40% 且 >V0 ∧ 盈亏比 ≥2.4 ∧ Spearman>0 且半窗同正，一票否决；C4 只作
参考列）。

性能：``svs.evaluate_variant`` 在 15k 笔单 eval ≈108ms（2026-09-08 本机实测）⇒
全量 F1~F4 扫描 ≈5702×2×108ms ≈ 20 分钟，超 15 分钟预算——故 ``--search`` 用
**fast path**（每窗一次性预计算 11 条 panel 腿的命中布尔矩阵 + ret 秩，组合分 =
矩阵 @ 权重向量后逐元素 rint + clamp 0-100；与 :func:`eval_combo` 慢路径**逐位等价**，
50 组合随机对拍钉测锁定，见 tests/test_score_combo_search_study.py。等价域精确化：
rets 全有限时整份产出逐位一致；rets 含 NaN/±inf 时，corr/半窗与判定列（F1~F4/
pass_all）仍与慢路径同口径——成对剔除 NaN、±inf 保留（pearson 得 None、spearman
排两端，见 :func:`_fast_corr`）；唯 ret 降序选取的 winner/bottom 分布（C2 参考列，
不进 R30 判定）不在对拍域——慢路径 ``sorted`` 遇 NaN 本是未定序，fast path 固定
NaN 排尾）。``--final``（≤3 组合单窗）走慢路径原口径。V0 篮子与全样本统计每窗
只算一次（:func:`prepare_window`）。

CLI（生产机 Phase 1/2 照抄预注册页）::

    uv run python src/custos/research/score_combo_search_study.py --search \\
        --from-trades artifacts/logs/score_variants_study/score_variants_study_s0_n400.rejudged.json \\
                      artifacts/logs/score_variants_study/score_variants_study_s0_n1000_cw.rejudged.json
    uv run python src/custos/research/score_combo_search_study.py --final \\
        --from-trades artifacts/logs/score_variants_study/score_variants_study_s0_n1000_pre2019.rejudged.json

产物：`artifacts/logs/score_combo_search_study/r30_search.json`（config/搜索规模/
过线清单/灵敏度/survivors top 3）、`r30_final_pre2019.json`（终审判定；证伪也是
结论——判定（通过/证伪）不影响退出码，运行性错误（输入缺失/守卫拒绝等）返回 1/2）。
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import reduce
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402

from custos.pipeline.screening import score_candidates as sc  # noqa: E402
from custos.research import backtest_factors as bf  # noqa: E402
from custos.research import score_calibration_study as scs  # noqa: E402
from custos.research import score_return_study as srs  # noqa: E402
from custos.research import score_variants_study as svs  # noqa: E402

# ---------------------------------------------------------------------------
# 搜索空间（预注册写死，与 R30 页「搜索空间」节逐字一致）
# ---------------------------------------------------------------------------

POS_LEGS = [
    "rsi_deep_oversold",
    "weekly_j_low",
    "rsi_bull_div",
    "macd_bottom_divergence",
    "leader_volume",
]
POS_LEVELS = (0, 10, 20, 30, 40)  # 0=删腿，至少一条非零
NEG_LEGS = (
    "rsi_strong",
    "b1_ignition",
    "volume_contraction",
    "relative_strength_strong",
    "macd_top_divergence",
    "ignition",
)
NEG_BLOCK_WEIGHT = -5  # 负腿块整体二态：关/开

ALL_LEGS = tuple(POS_LEGS) + NEG_LEGS  # 命中矩阵列序（5 正 + 6 负 = 11 列）

# 筛选线（预注册写死；F5 与终审用 R29 基线，作为参数传入判据函数——不改 R29 模块常量）
SEARCH_WIN_RATE_FLOOR = 0.45  # R30-F1（基线 0.40 + 5pp 显著性税）
SEARCH_PAYOFF_FLOOR = 2.6  # R30-F2（基线 2.4 + 0.2 显著性税）
BASE_WIN_RATE_FLOOR = 0.40  # R29 基线（F5 扰动判定 + pre2019 终审线）
BASE_PAYOFF_FLOOR = 2.4
TOP_SURVIVORS = 3  # 幸存者名额（预注册：top 3，不足全取）

# 产物路径（相对仓库根；--final 的终审名单从 --search 落盘读——跑数前已定）
SEARCH_OUT = Path("artifacts/logs/score_combo_search_study/r30_search.json")
FINAL_OUT = Path("artifacts/logs/score_combo_search_study/r30_final_pre2019.json")

ScoreFn = Callable[[dict[str, Any]], int]

_LEG_ABBR = {  # 组合命名缩写（确定性，落盘可溯源）
    "rsi_deep_oversold": "rd",
    "weekly_j_low": "wj",
    "rsi_bull_div": "bd",
    "macd_bottom_divergence": "md",
    "leader_volume": "lv",
}


# ---------------------------------------------------------------------------
# 网格生成（gcd 排序等价去重）
# ---------------------------------------------------------------------------


def _combo_name(canonical: tuple[int, ...], neg_block: bool) -> str:
    """确定性名字：非零正腿缩写+代表权重，负腿块开加 _nb 后缀（如 rd10_wj10_bd20_nb）。"""
    name = "_".join(
        f"{_LEG_ABBR[leg]}{c * 10}"
        for leg, c in zip(POS_LEGS, canonical)
        if c  # 全零已排除 ⇒ 至少一段
    )
    return name + "_nb" if neg_block else name


def generate_grid() -> list[dict[str, Any]]:
    """枚举搜索网格（gcd 排序等价去重；与预注册页「规模与去重」节逐字一致）。

    去重正确性论证（尺度不变性）：``make_candidate_score({}, panel_weights)`` 是
    panel 命中布尔 × 权重的**纯线性加权和**（clamp 100 前）。正权重同乘正常数 ⇒
    每笔得分同比例缩放 ⇒ 相对排序与并列结构逐位不变（线性映射严格单调；权重为整数
    刻度，精确算术无舍入漂移）。top-20% 篮子按分数降序选取（稳定排序，并列保持原
    顺序）、Spearman 是秩相关——二者都**尺度不变**，故同比例组合属于同一排序等价
    类，只评一次。⚠️ clamp(0,100) 在 100 处非线性 ⇒ 等价只在「clamp 前」严格成立；
    代表权重取**原始向量 ×10**（类内最小 ×10 整数刻度，把 clamp 影响压到最小），
    强档占比 F4 依预注册在代表权重上度量。负腿块是真实加减分（改变排序），
    **不参与去重**——每个等价类 × {关, 开} 各评一次。
    """
    combos: list[dict[str, Any]] = []
    seen: set[tuple[int, ...]] = set()
    for levels in itertools.product(POS_LEVELS, repeat=len(POS_LEGS)):
        if not any(levels):
            continue  # 全零 = 无正腿，预注册排除
        vec = tuple(x // 10 for x in levels)  # {0..4} 元组
        g = reduce(math.gcd, (x for x in vec if x))  # 非零项的最大公约数
        canonical = tuple(x // g for x in vec)
        if canonical in seen:
            continue  # 排序等价类已评
        seen.add(canonical)
        pos_weights = {leg: c * 10 for leg, c in zip(POS_LEGS, canonical)}
        for neg_on in (False, True):
            panel_weights: dict[str, float] = dict(pos_weights)
            for leg in NEG_LEGS:
                panel_weights[leg] = NEG_BLOCK_WEIGHT if neg_on else 0
            combos.append(
                {
                    "name": _combo_name(canonical, neg_on),
                    "panel_weights": panel_weights,
                    "canonical": list(canonical),  # JSON 落盘为数组，读回可溯源
                    "neg_block": neg_on,
                }
            )
    return combos


# ---------------------------------------------------------------------------
# R30-F1~F4 判据（结构模仿 R29 eval_r29_candidate；胜率/盈亏比线作参数传入）
# ---------------------------------------------------------------------------


def _r30_f1(
    basket: dict[str, Any], v0_basket: dict[str, Any], wr_floor: float
) -> dict[str, Any]:
    """R30-F1：篮子胜率 ≥ wr_floor 且 > V0 篮子胜率（None 安全：任一缺省判 False）。"""
    b_wr = basket.get("win_rate")
    v0_wr = v0_basket.get("win_rate")
    note = None
    if b_wr is None or v0_wr is None:
        passed = False
        note = "篮子或 V0 篮子胜率缺省 ⇒ 判 False（如实标注，不编数）"
    else:
        passed = bool(b_wr >= wr_floor and b_wr > v0_wr)
    return {
        "pass": passed,
        "basket_win_rate": b_wr,
        "v0_basket_win_rate": v0_wr,
        "floor": wr_floor,
        "note": note,
    }


def _r30_f2(basket: dict[str, Any], payoff_floor: float) -> dict[str, Any]:
    """R30-F2：篮子盈亏比 ≥ payoff_floor（篮子无亏单 ⇒ payoff 无定义 ⇒ 判 False）。"""
    b_payoff = basket.get("payoff_ratio")
    return {
        "pass": bool(b_payoff is not None and b_payoff >= payoff_floor),
        "basket_payoff_ratio": b_payoff,
        "floor": payoff_floor,
        "note": (
            "篮子盈亏比缺省（无亏单，payoff 无定义）⇒ 判 False（如实标注）"
            if b_payoff is None
            else None
        ),
    }


def _eval_from_rep(
    name: str,
    n_trades: int,
    rep: dict[str, Any],
    v0_basket: dict[str, Any],
    universe_stats: dict[str, Any],
    wr_floor: float,
    payoff_floor: float,
) -> dict[str, Any]:
    """从 evaluate_variant 形态的报告组装 per-window 判定（快/慢两条路径共用）。

    F3 = judge 的 C1_spearman_positive；F4 = c5_strong_frac；C3★（全样本天然基准
    + Wilson 注记）只作参考列，不进判定；pass_all = F1∧F2∧F3∧F4。
    """
    vd = svs.judge(rep, v0_basket, universe_stats)
    f4 = scs.c5_strong_frac(rep["band_stats"], n_trades)
    b = rep["basket_top20_by_variant"]
    f1 = _r30_f1(b, v0_basket, wr_floor)
    f2 = _r30_f2(b, payoff_floor)
    f3 = {
        "pass": bool(vd["C1_spearman_positive"]),
        "spearman": rep["corr"].get("spearman"),
        "half_window_consistent": rep["half_window"].get("consistent"),
    }
    return {
        "combo": name,
        "n_trades": n_trades,
        "corr": rep["corr"],
        "half_window": rep["half_window"],
        "basket": b,
        "band_stats": rep["band_stats"],
        "basket_margin": vd.get("basket_margin"),
        "universe_margin": vd.get("universe_margin"),
        "C3_star": vd["C3_natural_vs_universe"],
        "C3_star_wilson_overlap": vd.get("wilson_overlap_universe"),
        "F1": f1,
        "F2": f2,
        "F3": f3,
        "F4": f4,
        "pass_all": bool(f1["pass"] and f2["pass"] and f3["pass"] and f4["pass"]),
    }


def eval_combo(
    trades: list[dict[str, Any]],
    name: str,
    score_fn: ScoreFn,
    wr_floor: float = SEARCH_WIN_RATE_FLOOR,
    payoff_floor: float = SEARCH_PAYOFF_FLOOR,
) -> dict[str, Any]:
    """单组合单窗口（慢路径 = svs.evaluate_variant 原口径；--final 与对拍钉测用）。

    判据机械全部复用 svs/scs 现成函数，零口径重写；胜率/盈亏比线作参数传入
    （搜索线 0.45/2.6；F5 与终审传 R29 基线 0.40/2.4）。
    """
    rep = svs.evaluate_variant(trades, name, score_fn)
    v0_basket = svs.basket_stats(trades, svs.v0_score, svs.TOP_FRAC)
    return _eval_from_rep(
        name,
        len(trades),
        rep,
        v0_basket,
        scs._universe_stats_of(trades),
        wr_floor,
        payoff_floor,
    )


# ---------------------------------------------------------------------------
# fast path（--search 用；与慢路径逐位等价，50 组合随机对拍钉测锁定）
# ---------------------------------------------------------------------------


@dataclass
class FastWindow:
    """单窗预计算（每窗一次）：命中矩阵 / ret 数组与秩 / 半窗掩码 / V0 篮子 / 全样本统计。"""

    n: int
    rets: list[float]  # Python float 列表（statistics 复算与 srs 同口径）
    rets_arr: np.ndarray
    hit_mat: np.ndarray  # (n, 11)：panel 腿命中 True=1.0，None/False=0.0
    mid_date: str  # 半窗切分日（= srs.half_window_check 的 dates[n//2]）
    first_mask: np.ndarray  # entry_date <= mid_date
    second_mask: np.ndarray
    rr: np.ndarray  # ret 平均秩（全体；Spearman 的被秩化一臂，与分数无关）
    rr_first: np.ndarray  # 前半窗内 ret 平均秩
    rr_second: np.ndarray
    ret_order: np.ndarray  # ret 降序稳定序（rets 全有限时 = sorted(reverse=True) 的等价类选择；NaN 固定排尾）
    n_top: int  # ceil(n × TOP_FRAC)，至少 1（与 srs.split_top_frac 同规则）
    v0_basket: dict[str, Any]
    universe_stats: dict[str, Any]


def _avg_ranks(values: np.ndarray) -> np.ndarray:
    """平均秩（同分取平均），与 ``pd.Series.rank(method='average')`` 逐位一致。

    组的平均秩 = (首末 1-based 秩之和)/2 = (s+e+1)/2（s/e 为 0-based 排序位置的
    组区间 [s, e)）；pandas 是「序号精确求和后除以组大小」，两者在 n < 2^53 内
    逐位相等（对拍钉测 200 组随机含重并列样本锁定）。NaN 不参与排名、秩记 NaN
    （= pandas 默认 na_option='keep'——相关计算时再成对剔除，见 :func:`_fast_corr`）；
    ±inf 按数值排名（+inf 取最大秩，同 pandas）。
    """
    n = len(values)
    ranks = np.full(n, np.nan)
    idx = np.flatnonzero(~np.isnan(values))  # NaN 不参与排名（pandas 同口径）
    m = len(idx)
    if not m:
        return ranks
    sub = values[idx]
    order = np.argsort(sub, kind="stable")
    sv = sub[order]
    is_new = np.ones(m, dtype=bool)
    is_new[1:] = sv[1:] != sv[:-1]
    starts = np.flatnonzero(is_new)
    ends = np.concatenate([starts[1:], [m]])
    means = (starts + ends + 1) / 2.0
    sub_ranks = np.empty(m, dtype=np.float64)
    sub_ranks[order] = np.repeat(means, ends - starts)
    ranks[idx] = sub_ranks
    return ranks


def prepare_window(trades: list[dict[str, Any]]) -> FastWindow:
    """每窗一次性预计算（V0 篮子与全样本统计只在这里算一次，全搜索复用）。"""
    n = len(trades)
    rets = [float(t["ret"]) for t in trades]
    rets_arr = np.asarray(rets, dtype=np.float64)
    hit = np.array(
        [
            [
                1.0 if (t.get("panel") or {}).get(leg) is True else 0.0
                for leg in ALL_LEGS
            ]
            for t in trades
        ],
        dtype=np.float64,
    ).reshape(n, len(ALL_LEGS))
    dates = sorted(str(t["entry_date"]) for t in trades)
    mid_date = dates[n // 2] if dates else ""
    first_mask = np.array([str(t["entry_date"]) <= mid_date for t in trades])
    second_mask = ~first_mask
    ret_order = np.argsort(-rets_arr, kind="stable")
    return FastWindow(
        n=n,
        rets=rets,
        rets_arr=rets_arr,
        hit_mat=hit,
        mid_date=mid_date,
        first_mask=first_mask,
        second_mask=second_mask,
        rr=_avg_ranks(rets_arr),
        rr_first=_avg_ranks(rets_arr[first_mask]),
        rr_second=_avg_ranks(rets_arr[second_mask]),
        ret_order=ret_order,
        n_top=max(1, math.ceil(n * svs.TOP_FRAC)) if n else 0,
        v0_basket=svs.basket_stats(trades, svs.v0_score, svs.TOP_FRAC),
        universe_stats=scs._universe_stats_of(trades),
    )


def _weights_vector(panel_weights: dict[str, Any]) -> np.ndarray:
    return np.array(
        [float(panel_weights.get(k, 0.0)) for k in ALL_LEGS], dtype=np.float64
    )


def _fast_corr(
    scores: np.ndarray, rets: np.ndarray, ret_ranks: np.ndarray
) -> dict[str, Any]:
    """srs.correlations 的数组版（pandas corr 内部就是 np.corrcoef，同输入逐位一致）。

    NaN/±inf 口径与 pandas 严格对齐：pearson 成对剔除 NaN（``Series.corr`` 默认，
    x/y 任一缺即剔）；spearman 先在**全样本**上取平均秩（NaN 秩记 NaN），再对秩
    成对剔除后算 pearson（``rs.corr(rr)`` 同口径）——剔除只认 NaN，±inf 保留
    （pearson 随之得 NaN→None；spearman 把 ±inf 排在两端，均同 pandas）。
    """
    n = len(scores)
    if n < 3:
        return {"n": n, "spearman": None, "pearson": None}
    with np.errstate(
        invalid="ignore", divide="ignore"
    ):  # 常数输入/±inf ⇒ nan（同 pandas 路径）
        ok = ~np.isnan(scores) & ~np.isnan(rets)
        pearson = (
            float(np.corrcoef(scores[ok], rets[ok])[0, 1])
            if ok.sum() >= 2
            else float("nan")
        )
        rs = _avg_ranks(scores)  # 全样本秩（scores 恒有限，无 NaN）
        ok_r = ~np.isnan(ret_ranks)
        rr_ok = ret_ranks[ok_r]
        spearman = (
            float(np.corrcoef(rs[ok_r], rr_ok)[0, 1])
            if len(rr_ok) >= 2 and np.std(rs, ddof=1) > 0 and np.std(rr_ok, ddof=1) > 0
            else float("nan")
        )
    return {
        "n": n,
        "spearman": None if math.isnan(spearman) else round(spearman, 4),
        "pearson": None if math.isnan(pearson) else round(pearson, 4),
    }


def _fast_half_window(win: FastWindow, scores: np.ndarray) -> dict[str, Any]:
    """srs.half_window_check 的数组版（切分日/子集口径一致；Spearman 用半窗内秩）。"""
    if win.n < 6:
        return {"n": win.n, "skipped": "样本不足(<6)"}
    c1 = _fast_corr(scores[win.first_mask], win.rets_arr[win.first_mask], win.rr_first)
    c2 = _fast_corr(
        scores[win.second_mask], win.rets_arr[win.second_mask], win.rr_second
    )
    s1, s2 = c1.get("spearman"), c2.get("spearman")
    return {
        "split_date": win.mid_date,
        "first_half": c1,
        "second_half": c2,
        "consistent": (None if s1 is None or s2 is None else (s1 > 0) == (s2 > 0)),
    }


def _ret_stats_of(rets: list[float]) -> dict[str, Any]:
    """srs.ret_stats 的列表版（同一组 statistics 调用 + 同样的 round，逐位一致）。"""
    if not rets:
        return {"n": 0}
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


def _fast_basket(win: FastWindow, scores: np.ndarray) -> dict[str, Any]:
    """svs.basket_stats 的数组版：分数降序稳定序取 top-frac（并列保持原顺序）。"""
    order = np.argsort(-scores, kind="stable")
    idx = order[: win.n_top].tolist()
    rets = [win.rets[i] for i in idx]
    n_win = sum(1 for r in rets if r > 0)
    return {**_ret_stats_of(rets), "n": len(idx), "n_win": n_win}


def _fast_band_stats(win: FastWindow, scores: np.ndarray) -> dict[str, dict[str, Any]]:
    """srs.band_stats 的数组版（分档阈值 = score_candidates 同一常量）。"""
    strong = scores >= sc.TECH_STRONG_FALLBACK
    mid = (~strong) & (scores >= sc.TECH_MID_FALLBACK)
    masks = {">=60": strong, "30-59": mid, "<30": ~(strong | mid)}
    out: dict[str, dict[str, Any]] = {}
    for b in srs.BANDS:
        idx = np.flatnonzero(masks[b]).tolist()
        if not idx:
            out[b] = {"n": 0}
            continue
        rets = [win.rets[i] for i in idx]
        wins = [r for r in rets if r > 0]
        losses = [-r for r in rets if r < 0]
        avg_win = statistics.mean(wins) if wins else 0.0
        avg_loss = statistics.mean(losses) if losses else 0.0
        out[b] = {
            "n": len(idx),
            "avg_ret": round(statistics.mean(rets), 4),
            "median_ret": round(statistics.median(rets), 4),
            "win_rate": round(len(wins) / len(rets), 4),
            "payoff_ratio": round(avg_win / avg_loss, 3) if avg_loss > 0 else None,
            "avg_score": round(statistics.mean(scores[idx].tolist()), 2),
        }
    return out


def eval_combo_fast(
    win: FastWindow,
    name: str,
    panel_weights: dict[str, Any],
    wr_floor: float = SEARCH_WIN_RATE_FLOOR,
    payoff_floor: float = SEARCH_PAYOFF_FLOOR,
) -> dict[str, Any]:
    """单组合单窗口（fast path）：命中矩阵 @ 权重向量 + 逐元素 rint/clamp。

    复刻 ``make_candidate_score({}, panel_weights)`` 语义：panel 命中（True）加权、
    None/False 不计（命中矩阵预置 0）、``np.rint`` = Python round（半到偶，组合权重
    为 2.5 的倍数 ⇒ 加和 float64 精确、并列恰在 .5，二者同规则）后 clamp 0-100——
    clamp 在 100 处非线性，故矩阵乘法之后仍逐元素 clamp。产出与 :func:`eval_combo`
    逐位等价（对拍钉测锁定；rets 含 NaN/±inf 时的等价域见模块 docstring）。
    """
    raw = win.hit_mat @ _weights_vector(panel_weights)
    scores = np.clip(np.rint(raw), 0.0, 100.0).astype(np.int64)
    scores_f = scores.astype(np.float64)
    top_idx = win.ret_order[: win.n_top]
    rep = {
        "variant": name,
        "corr": _fast_corr(scores_f, win.rets_arr, win.rr),
        "half_window": _fast_half_window(win, scores_f),
        "winner_top20_dist": srs.dist_stats(scores[top_idx].tolist()),
        "bottom80_dist": srs.dist_stats(scores[win.ret_order[win.n_top :]].tolist()),
        "basket_top20_by_variant": _fast_basket(win, scores),
        "band_stats": _fast_band_stats(win, scores),
    }
    return _eval_from_rep(
        name, win.n, rep, win.v0_basket, win.universe_stats, wr_floor, payoff_floor
    )


def combo_sensitivity(
    windows: dict[str, FastWindow],
    name: str,
    panel_weights: dict[str, Any],
) -> dict[str, Any]:
    """R30-F5：每条非零 panel 腿 ±50% 扰动（round 2 位；负腿同；零权重腿不扰动）。

    判定对象 = **R29 基线 pass_all**（胜率≥0.40 且 >V0 ∧ 盈亏比≥2.4 ∧ Spearman 半窗
    同正 ∧ 强档≤0.15），逐窗查翻转。翻转 = 扰动后与基线**不一致**；基线本就不过的
    窗里扰动失败不算翻转（那是「本来就不行」，不是参数敏感——同 R24/R29 语义）。
    只对过 F1~F4 的组合跑（省算力）。
    """
    perturbations: list[tuple[str, float]] = []  # (panel 腿, 扰动后权重)
    for leg, w in panel_weights.items():
        if w == 0:
            continue  # 零权重腿不扰动（预注册：非零腿 ±50%）
        perturbations += [(leg, round(w * 0.5, 2)), (leg, round(w * 1.5, 2))]

    # 先定基线（未扰动组合在每窗的 R29 基线 pass_all）
    base_pass = {
        label: eval_combo_fast(
            win, name, panel_weights, BASE_WIN_RATE_FLOOR, BASE_PAYOFF_FLOOR
        )["pass_all"]
        for label, win in windows.items()
    }
    flips: list[dict[str, Any]] = []
    n_checks = 0
    for leg, val in perturbations:
        pw = dict(panel_weights)
        pw[leg] = val
        for label, win in windows.items():
            ev = eval_combo_fast(win, name, pw, BASE_WIN_RATE_FLOOR, BASE_PAYOFF_FLOOR)
            n_checks += 1
            if ev["pass_all"] != base_pass[label]:
                flips.append(
                    {
                        "leg": leg,
                        "perturbed_to": val,
                        "window": label,
                        "base_pass": base_pass[label],
                        "basket_win_rate": (ev["basket"] or {}).get("win_rate"),
                        "basket_payoff_ratio": (ev["basket"] or {}).get("payoff_ratio"),
                    }
                )
    return {
        "combo": name,
        "n_perturbations": len(perturbations),
        "n_checks": n_checks,
        "base_pass_by_window": base_pass,
        "n_flip": len(flips),
        "parameter_sensitive": bool(flips),
        "flips": flips,
    }


# ---------------------------------------------------------------------------
# --search：双窗筛选（F1~F4 全量扫描 + F5 只对过线组合）+ 幸存者 top 3
# ---------------------------------------------------------------------------


def _margin_vs_universe(w: dict[str, Any]) -> Optional[float]:
    """篮子 margin − 全样本 margin（预注册「篮子 margin（vs 全样本）」）；缺省 None。"""
    bm, um = w.get("basket_margin"), w.get("universe_margin")
    if bm is None or um is None:
        return None
    return round(bm - um, 4)


def _survivor_rank_key(rec: dict[str, Any]) -> float:
    """幸存者排名键（预注册写死，跑数后不许换键挑好看的）：

    逐窗（篮子 margin − 全样本 margin），取**两窗较小值**，降序。
    任一窗 margin 缺省 ⇒ 记 −∞（排尾，如实不编数）。sort 稳定 ⇒ 同键保持网格序。
    """
    vals = []
    for w in (rec["per_window"] or {}).values():
        v = _margin_vs_universe(w)
        if v is None:
            return float("-inf")  # 该窗缺省 ⇒ 整个组合排尾
        vals.append(v)
    return min(vals) if vals else float("-inf")


def search_report(trades_by_window: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """--search 总报告：网格枚举 + 双窗 F1~F4 筛选 + F5 灵敏度 + survivors top 3。"""
    grid = generate_grid()
    windows = {label: prepare_window(t) for label, t in trades_by_window.items()}
    passed: list[dict[str, Any]] = []
    for i, combo in enumerate(grid):
        if (i + 1) % 500 == 0:
            print(
                f"[INFO] 搜索进度 {i + 1}/{len(grid)}（已过线 {len(passed)}）",
                file=sys.stderr,
            )
        per_window = {
            label: eval_combo_fast(win, combo["name"], combo["panel_weights"])
            for label, win in windows.items()
        }
        if all(w["pass_all"] for w in per_window.values()):
            passed.append({**combo, "per_window": per_window})
    sensitivity: dict[str, Any] = {}
    survivor_pool: list[dict[str, Any]] = []
    for rec in passed:  # F5 只对过 F1~F4 的组合跑（省算力）
        s = combo_sensitivity(windows, rec["name"], rec["panel_weights"])
        sensitivity[rec["name"]] = s
        if not s["parameter_sensitive"]:
            survivor_pool.append(rec)
    survivor_pool.sort(key=_survivor_rank_key, reverse=True)
    survivors = [rec["name"] for rec in survivor_pool[:TOP_SURVIVORS]]
    for rec in passed:
        rec["margin_min_across_windows"] = (
            None if not math.isfinite(k := _survivor_rank_key(rec)) else round(k, 4)
        )
    return {
        "r30_phase": (
            "search（有界组合搜索；调参只用主窗+跨窗，pre2019 终审前不许碰——CLI 硬拒绝）"
        ),
        "preregistration": "governance/research/R30_score_combo_search.md",
        "config": {
            "pos_legs": POS_LEGS,
            "pos_levels": list(POS_LEVELS),
            "neg_legs": list(NEG_LEGS),
            "neg_block_weight": NEG_BLOCK_WEIGHT,
            "top_frac": svs.TOP_FRAC,
            "search_floors": {
                "win_rate": SEARCH_WIN_RATE_FLOOR,
                "payoff": SEARCH_PAYOFF_FLOOR,
                "strong_frac_max": scs.C5_STRONG_FRAC_MAX,
            },
            "baseline_floors_for_F5_and_final": {
                "win_rate": BASE_WIN_RATE_FLOOR,
                "payoff": BASE_PAYOFF_FLOOR,
            },
            "dedup": (
                "gcd 排序等价去重：打分纯线性加权（clamp 100 前），正权重同比例缩放"
                "不改变排序（top-20% 篮子/Spearman 均尺度不变）⇒ 原始向量（//10 后除以"
                "非零项 gcd）同类只评一次，代表权重=原始向量×10；负腿块不参与去重"
            ),
            "survivor_rule": (
                "过 F1~F5 按「两窗（篮子margin−全样本margin）较小值」降序取 top 3"
                "（不足全取；排名键写死不许换）"
            ),
            "scorer": "scs.make_candidate_score({}, panel_weights)（证据重构，现行腿全归零）",
        },
        "windows": list(trades_by_window),
        "n_trades_by_window": {label: len(t) for label, t in trades_by_window.items()},
        "n_combos_raw": (len(POS_LEVELS) ** len(POS_LEGS) - 1) * 2,
        "n_combos_evaluated": len(grid),
        "n_pass_f1_f4": len(passed),
        "n_survivor_pool": len(survivor_pool),
        "passed": passed,
        "sensitivity": sensitivity,
        "survivors": survivors,
    }


def _pct(x: Optional[float]) -> str:
    return f"{x * 100:.1f}%" if x is not None else "—"


def _pp(x: Optional[float]) -> str:
    return f"{x * 100:+.1f}pp" if x is not None else "—"


def _window_cell(w: dict[str, Any]) -> str:
    """单窗读数格：篮子 胜率/盈亏比/margin vs 全样本 margin / Spearman。"""
    b = w["basket"]
    return (
        f"{_pct(b.get('win_rate'))}/{b.get('payoff_ratio')}/"
        f"{_pp(w.get('basket_margin'))} vs {_pp(w.get('universe_margin'))} "
        f"Sp={w['corr'].get('spearman')}"
    )


def print_search(rep: dict[str, Any]) -> None:
    """stdout 中文摘要：搜索规模 + 过线数 + survivors 表（组合/逐窗读数/翻转数）。"""
    print("\n" + "=" * 78)
    print(f"R30 有界组合搜索：--search（调参双窗 = {'/'.join(rep['windows'])}）")
    print("=" * 78)
    print(
        "⚠️ R11：量级不作数。⚠️ 纪律：pre2019 终审前不许碰（本表不含）；"
        "搜索族证据等级封顶 L3−（多重比较残余风险不掩饰）。"
    )
    cfg = rep["config"]
    print(
        f"\n搜索规模：{len(cfg['pos_legs'])}腿×{len(cfg['pos_levels'])}档−全零，"
        f"×2（负腿块 关/开 各 {cfg['neg_block_weight']}）= 原始 {rep['n_combos_raw']}；"
        f"gcd 排序等价去重后评估 {rep['n_combos_evaluated']} 个等价类"
    )
    print(
        f"筛选线：F1 胜率≥{cfg['search_floors']['win_rate']}且>V0 / "
        f"F2 盈亏比≥{cfg['search_floors']['payoff']} / F3 Spearman半窗同正 / "
        f"F4 强档≤{cfg['search_floors']['strong_frac_max']} / "
        f"F5 ±50%扰动 R29基线pass_all 零翻转（两窗各自全过）"
    )
    print(
        f"过线（F1~F4）：{rep['n_pass_f1_f4']} 个；F5 零翻转后存活 "
        f"{rep['n_survivor_pool']} 个；survivors top {TOP_SURVIVORS} 见下"
    )
    by_name = {c["name"]: c for c in rep["passed"]}
    print(
        f"\n── survivors（排名键写死：两窗（篮子margin−全样本margin）较小值降序 "
        f"top {TOP_SURVIVORS}）"
    )
    print(
        "组合 | "
        + " | ".join(f"{label} 胜率/盈亏比/margin/Sp" for label in rep["windows"])
        + " | 翻转数"
    )
    for name in rep["survivors"]:
        rec = by_name[name]
        cells = " | ".join(
            _window_cell(rec["per_window"][label]) for label in rep["windows"]
        )
        s = rep["sensitivity"][name]
        print(f"  {name:<28} {cells} | {s['n_flip']}")
    if not rep["survivors"]:
        print("  （空——无组合过 F1~F5，判负收口，如实回填预注册页）")


# ---------------------------------------------------------------------------
# --final：pre2019 untouched 终审（终审线 = R29 基线 F1∧F2∧F3，一票否决）
# ---------------------------------------------------------------------------


def final_report(
    trades: list[dict[str, Any]],
    finalists: list[str],
    combo_specs: dict[str, dict[str, Any]],
    window_label: str = "pre2019",
    search_rep: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """pre2019 终审：survivors 逐一过 R29 基线终审线（一票否决）。

    终审线（预注册）：**篮子胜率 ≥40% 且 >V0 ∧ 盈亏比 ≥2.4 ∧ Spearman>0 且半窗同正**
    同时保持 ⇒ 通过（带「经搜索」L3− 注记）；任一失线 ⇒ 如实判负（证伪也是结论）。
    ⚠️ F4（强档占比）只作参考列，不进终审线。
    ``finalists``/``combo_specs`` 必须来自 r30_search.json 落盘（终审名单不许临时
    指定）；``search_rep``（可选）用于三窗并排对照（不再重算）。
    """
    candidates: dict[str, Any] = {}
    for name in finalists:
        spec = combo_specs[
            name
        ]  # 未知组合 ⇒ KeyError（CLI 层已预检，见 _read_search_finalists）
        fn = scs.make_candidate_score({}, spec["panel_weights"])
        ev = eval_combo(trades, name, fn, BASE_WIN_RATE_FLOOR, BASE_PAYOFF_FLOOR)
        terminal_pass = bool(ev["F1"]["pass"] and ev["F2"]["pass"] and ev["F3"]["pass"])
        candidates[name] = {
            "panel_weights": spec["panel_weights"],
            "canonical": spec.get("canonical"),
            "neg_block": spec.get("neg_block"),
            "eval": ev,
            "terminal_pass": terminal_pass,
        }
    passed = [n for n, c in candidates.items() if c["terminal_pass"]]
    return {
        "r30_phase": (
            "final（pre2019 untouched 终审——终审窗第一次也是唯一一次读取；终审线："
            "R29 基线 胜率≥40%且>V0 ∧ 盈亏比≥2.4 ∧ Spearman>0且半窗同正，一票否决）"
        ),
        "evidence_note": (
            "⚠️ 搜索族候选：即使终审通过也带「经搜索」注记，证据等级封顶 L3−，"
            "进 live 前必须影子观察（多重比较残余风险不掩饰）"
        ),
        "window": window_label,
        "n_trades": len(trades),
        "finalists": list(finalists),
        "candidates": candidates,
        "passed": passed,
        "verdict": "通过" if passed else "证伪",
        "fallback": None
        if passed
        else "如实判负回填 R30 预注册页（组合空间无稳健解 / 终审失线——与 R22/R24/R29 同款结局也是合格产出）",
        "search_reference": search_rep,
    }


def _final_row(name: str, c: dict[str, Any]) -> str:
    """终审表单行：组合 | F1 | F2 | F3 | F4(参考) | 篮子读数 | 终审判定。"""
    ev = c["eval"]
    b = ev["basket"]
    return (
        f"  {name:<28} {'✓' if ev['F1']['pass'] else '✗'}   "
        f"{'✓' if ev['F2']['pass'] else '✗'}   "
        f"{'✓' if ev['F3']['pass'] else '✗'}    "
        f"{'✓' if ev['F4']['pass'] else '✗'}   "
        f"{_pct(b.get('win_rate'))}/{b.get('payoff_ratio')}/"
        f"{_pp(ev.get('basket_margin'))} vs {_pp(ev.get('universe_margin'))} | "
        f"{'✅ 通过' if c['terminal_pass'] else '❌ 不通过'}"
    )


def print_final(rep: dict[str, Any]) -> None:
    """stdout 中文终审表：终审判定 +（有 search 参照时）三窗并排。"""
    print("\n" + "=" * 78)
    print(f"R30 终审：pre2019 untouched（{rep['n_trades']} 笔）")
    print("=" * 78)
    print(
        "⚠️ 终审线（预注册）：胜率≥40%且>V0 ∧ 盈亏比≥2.4 ∧ Spearman>0且半窗同正"
        " 同时保持，一票否决；F4 只作参考列。⚠️ R11：量级不作数。"
        "⚠️ 通过也带「经搜索」L3− 注记。"
    )
    print(
        "\n组合 | F1 | F2 | F3 | F4(参考) | 篮子胜率/盈亏比/margin vs 全样本 | 终审判定"
    )
    for name, c in rep["candidates"].items():
        print(_final_row(name, c))
        ev = c["eval"]
        hw = ev["half_window"]
        h1 = (hw.get("first_half") or {}).get("spearman")
        h2 = (hw.get("second_half") or {}).get("spearman")
        print(
            f"    Spearman={ev['corr'].get('spearman')}（半窗 {h1}/{h2}"
            f"{'' if hw.get('consistent') else ' ⚠️翻'}），"
            f"强档占比 {ev['F4']['strong_frac']}，"
            f"C3★{'✓' if ev['C3_star'] else '✗'}(参考列)"
        )
    sref = rep.get("search_reference")
    if sref:
        print("\n── 三窗并排（篮子 胜率/盈亏比/margin vs 全样本 margin / Spearman）")
        by_name = {c["name"]: c for c in sref.get("passed") or []}
        for name in rep["finalists"]:
            cells = []
            rec = by_name.get(name) or {}
            for label in ("主窗", "跨窗"):
                w = (rec.get("per_window") or {}).get(label)
                if w:
                    cells.append(f"[{label}] {_window_cell(w)}")
            cells.append(f"[pre2019] {_window_cell(rep['candidates'][name]['eval'])}")
            print(f"  {name:<28} " + " | ".join(cells))
    print(
        f"\n终审结论：{rep['verdict']}"
        + (f"——通过组合 {rep['passed']}" if rep["passed"] else f"——{rep['fallback']}")
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--search",
        action="store_true",
        help="有界组合搜索（gcd 去重 + 双窗筛选 F1~F5 + survivors top 3）："
        "需要且仅需要主窗+跨窗两份输入（预注册：调参双窗；pre2019 输入**硬拒绝**——"
        "终审前不许碰，纪律代码化）",
    )
    ap.add_argument(
        "--final",
        action="store_true",
        help="pre2019 untouched 终审：**只接受** pre2019 单文件输入，终审名单机械读自 "
        "r30_search.json 的 survivors（不许临时指定）——这是终审窗第一次也是唯一一次"
        "允许读它（R30 预注册终审线 = R29 基线，一票否决）",
    )
    ap.add_argument(
        "--from-trades",
        nargs="+",
        default=[],
        help="已落盘的研究 JSON（含 trades 键，含 factor_contrib + panel）；"
        "--search = 主窗+跨窗两份，--final = pre2019 一份",
    )
    return ap


def _load_trades(path: str) -> list[dict[str, Any]]:
    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    return stored.get("trades") or []


def _search_main(paths: list[str]) -> int:
    """--search 驱动：两窗离线筛选（pre2019 硬拒绝）→ 落盘 + stdout。"""
    for p in paths:  # pre2019 硬拒绝只看文件名、不读文件（终审前不许碰）
        if "pre2019" in Path(p).name:
            print(
                f"⛔ 反过拟合纪律：--search 调参不许碰 pre2019（{p}）——"
                "它是 untouched 终审窗（R30 --final）",
                file=sys.stderr,
            )
            return 2
    if len(paths) != 2:
        print(
            "⛔ --search 需要且仅需要主窗+跨窗两份输入（预注册：调参双窗）",
            file=sys.stderr,
        )
        return 2
    trades_by_window: dict[str, list[dict[str, Any]]] = {}
    for p in paths:
        trades = _load_trades(p)
        if not trades:
            print(f"⛔ 复用文件无 trades: {p}", file=sys.stderr)
            return 1
        label = (
            "主窗"
            if "n400" in Path(p).name
            else ("跨窗" if "cw" in Path(p).name else Path(p).stem)
        )
        trades_by_window[label] = trades
        print(f"[INFO] 复用 {p}（{len(trades)} 笔，标签={label}）", file=sys.stderr)
    rep = search_report(trades_by_window)
    SEARCH_OUT.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(SEARCH_OUT, rep, big=False)
    print(f"[OK] 写出 {SEARCH_OUT}")
    print_search(rep)
    return 0


def _read_search_finalists() -> Optional[
    tuple[list[str], dict[str, Any], dict[str, Any]]
]:
    """终审名单 = r30_search.json 落盘的 survivors（跑数前已定；None = 不可用）。"""
    if not SEARCH_OUT.is_file():
        print(
            f"⛔ 找不到搜索落盘 {SEARCH_OUT}——终审名单必须先跑 --search 定下"
            "（终审名单不许临时指定）",
            file=sys.stderr,
        )
        return None
    rep = json.loads(SEARCH_OUT.read_text(encoding="utf-8"))
    finalists = rep.get("survivors") or []
    specs = {c["name"]: c for c in rep.get("passed") or []}
    unknown = [n for n in finalists if n not in specs]
    if not finalists or unknown:
        print(
            f"⛔ 搜索幸存者名单不可用（survivors={finalists}"
            f"{f'，未知组合 {unknown}' if unknown else ''}）——无可终审组合，如实上报",
            file=sys.stderr,
        )
        return None
    return list(finalists), specs, rep


def _final_main(paths: list[str]) -> int:
    """--final 驱动：**只接受** pre2019 单文件输入（与 --search 硬拒绝互为镜像）。"""
    if len(paths) != 1:
        print("⛔ --final 只跑 untouched 终审窗一个输入", file=sys.stderr)
        return 2
    p = paths[0]
    if "pre2019" not in Path(p).name:
        print(
            f"⛔ --final 只接受 pre2019 untouched 窗输入（{p}）；"
            "主窗/跨窗请用 --search",
            file=sys.stderr,
        )
        return 2
    loaded = _read_search_finalists()  # 守卫先行：名单不可用则不读终审窗文件
    if loaded is None:
        return 2
    trades = _load_trades(p)
    if not trades:
        print(f"⛔ 复用文件无 trades: {p}", file=sys.stderr)
        return 1
    finalists, specs, search_rep = loaded
    print(
        f"[INFO] 终审窗 {p}（{len(trades)} 笔）——第一次也是唯一一次读取；"
        f"终审名单（r30_search.json 落盘）={finalists}",
        file=sys.stderr,
    )
    rep = final_report(trades, finalists, specs, "pre2019", search_rep)
    FINAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(FINAL_OUT, rep, big=False)
    print(f"[OK] 写出 {FINAL_OUT}")
    print_final(rep)
    return 0  # 证伪也是结论（退出码不区分通过/证伪，verdict 见 JSON/stdout）


def main(argv: Optional[list[str]] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    if args.search and args.final:
        ap.error("--search 与 --final 互斥（调参与终审是两步，不许一次跑）")
    if args.search:
        if not args.from_trades:
            ap.error("--search 需要 --from-trades <主窗json> <跨窗json>")
        return _search_main(args.from_trades)
    if args.final:
        if not args.from_trades:
            ap.error("--final 需要 --from-trades <pre2019 json>")
        return _final_main(args.from_trades)
    ap.error(
        "本工具两个互斥模式：--search（主窗+跨窗有界组合搜索）/ "
        "--final（pre2019 untouched 终审），均需 --from-trades"
    )
    return 2  # pragma: no cover（ap.error 先抛 SystemExit）


if __name__ == "__main__":
    raise SystemExit(main())
