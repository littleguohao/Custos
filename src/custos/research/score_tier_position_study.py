# -*- coding: utf-8 -*-
"""R42 打分→仓位分层 Phase 1 工具（预注册
``governance/research/R42_score_tier_position.md``——判据 v0.312 落档写死；
v0.326 功效节在案：owner 拍板采 A=保 3 档+MDE 判读条款，**C3 升主判据**）。

问法（#61 改问「分层行动语义」）：出场钉死 pct5_trail08 时——①**单调性
（硬前提）**：V0 分数三档 margin 是否双窗满足 高≥中≥低 且 高−低>0；
②**仓位增量（问法本体+主判据）**：同一批信号按档分配仓位（加权
expectancy_R=Σw·R/Σw）对比等权基准是否有增量。单调性不成立就不再测
仓位增量（C2 不过 ⇒ falsified 当场收口）。

口径（判据全冻结，LLM 不碰数值）：
- **入场信号集** = V0 + j_low + 0AMV 多头区间（``exit_campaign.warm_v0_signals``
  单源，与 R36/R37/R39 同基底）；**档内交易 = collect_all 全候选分档**
  （⚠️ 非 top_n——top_n 截断下判定窗每档 ~49 笔会贴 C1 线，R39 B3 桶同款坑
  「样本不足误判成否定」，v0.301 在案）；
- **分档切点只在挖掘窗估计**（1/3、2/3 经验分位），判定窗直接应用——
  判定窗泄漏是死罪（双窗纪律）；
- **出场钉死 pct5_trail08**（``strategy_grid.DEFAULT_EXIT_GRID`` 那档参数
  原样取出——R10 策展基准档，单档隔离分档效应）；执行语义 T+1/跌停停牌
  顺延沿用引擎既有口径（单源）；
- **C1**：每档每窗 n ≥ 50，不过 ⇒ untested（样本不足≠否定证据，优先于
  其他判决）。**修订（v0.335，owner review）**：阈值不变（按笔数），但
  collect_all 口径下交易成簇（同票连续信号）——每档每窗**簇数入报告**
  （``n_clusters``）供判读打折（有效样本≈簇数，见功效节修订段）；
- **C2**：双窗都满足 margin(高)≥margin(中)≥margin(低) 且 高−低>0
  （打平读法写死：相邻打平不算倒挂，但 高−低 必须严格 >0）。**MDE 判读
  条款（v0.326）**：相邻档差 |Δ| < 0.086 ⇒ 标「低置信（形态读数非结论）」
  ——C2 问的是符号序，MDE 不改变判定本身；
- **C3（主判据）**：加权 expectancy_R（trades 的 r_multiple 加权 Σw·R/Σw）
  对比等权基准；权重格三组写死（归一化后平均权重=1——总敞口与等权基准
  一致，Δ 不被敞口驱动）：W1{高1.5,中1.0,低0.5} / W2{2.0,1.0,0.0} /
  W3{1.29,0.86,0.86}（=1.5/1/1 归一化 9/7,6/7,6/7）。**MDE 判读条款**：
  任一组双窗 |ΔexpR| < 0.035 ⇒ 按「与零无法区分」（不读增量也不读证伪），
  |Δ|≥MDE 才按符号判读；任一组双窗可读为正 ⇒ 过。⚠️ **W2 低档权重 0 =
  R36 Phase 4 已证伪的过滤器路线**（v0.292 在案），仅作对照——W2「过线」
  而 W1/W3 不过读作「过滤器效应残存」而非「分层成立」。⚠️ **组合层未测**：
  组合层加权 rdd 需要逐笔仓位权重，``simulate_portfolio_topn`` 是固定
  risk_pct 制不支持——按 owner 拍板不改引擎，本单元只报交易层加权 expR；
- **C4**：保持各档样本数不变、打乱「交易→档」归属，N=50 臂；每臂取同
  一套权重格的 max（与 C3 同预算）；C3 最佳组（挖掘窗 ΔexpR）> 池 q95 才
  算过；池构造/状态机 = ``criteria_kit`` 单源（空池 indeterminate、池未满
  provisional 不放行不判死）。**修订（v0.335，owner review——跑数前）**：
  打乱粒度从逐笔改**整簇**——簇 = 同 code、信号点相邻（间隔 ≤5 根 bar）
  的连续段（``kit.cluster_ids``/``kit.cluster_draw`` 单源）；每簇独立
  均匀抽一档（同簇交易同档），各档笔数组成在臂间自然波动（这正是零
  假设该含的方差，不再强行对齐笔数——逐笔打乱把簇拆散、q95 系统性
  偏低，owner 零假设模拟簇大小 20 时假过线 15/40≈38%）；**v0.336
  补充**：交易回挂信号簇的映射**命中率入报告且 <0.99 fail-closed**
  （不落盘——映射缺失会把 C4 静默退回逐笔打乱，口径缺陷不猜）；
- **verdict** = ``kit.verdict_four_state`` 四态（C1 不过=untested 优先；
  C2 False 或 C4 confirmed_fail=falsified——预注册四态写死 falsified 只
  来自 C2/C4，故 C3 不过只放行 None=provisional 不判死）。**不建 C5**
  （pre2019 终审只能杀不能确认——C2 和 C3 都过线才由 owner 拍板发令）。

窗口布局（TODO #90 owner 定案，v2 三段）：窗口四参缺省（全 None）⇒
**v2 布局**——挖掘 = 挖掘 A（2000~2016）∪ 挖掘 B（2022~2026-09-04）
两段各 warm/collect 后**合并一池**算挖掘读数（C1~C4 判据形态不变），
判定 = 受保护段 2017~2021（``load_window`` 段常量单源；台账键
judgment_2017_2021 单独记）；三段末各吃 ``clip_entries`` 隔离带一刀
（段末 90 自然日截止开仓，在 replay/判据之前，剔除数入 segments 块）。
四参全显式 ⇒ **legacy 单段**（逐窗 segment_violation + 双窗不相交——
取代旧「挖掘终 < 判定始」次序规则：v2 判定段天然夹在挖掘 A/B 之间，
旧规则作废；无隔离带，行为与切换前逐位一致）。只给一部分 ⇒ 非法
（半截布局，CLI ap.error / 直调 ValueError）。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.paths import LOGS, RESEARCH_DIR, cn_now  # noqa: E402
from custos.research import criteria_kit as kit  # noqa: E402
from custos.research import factor_exit_study as fes  # noqa: E402
from custos.research import load_window as lw  # noqa: E402
from custos.research import strategy_grid as sg  # noqa: E402
from custos.research import window_usage as wu  # noqa: E402

#: 出场钉死档（R10 策展基准；参数从 DEFAULT_EXIT_GRID 原样取出，不抄一份）
EXIT_NAME = "pct5_trail08"
EXIT_PARAMS: dict[str, Any] = next(
    dict(g["params"]) for g in sg.DEFAULT_EXIT_GRID if g["name"] == EXIT_NAME
)
#: 三档分位（切点只在挖掘窗估计，判定窗直接应用）
TIER_QS: tuple[float, float] = (1 / 3, 2 / 3)
TIERS: tuple[str, str, str] = ("low", "mid", "high")
#: C1：每档每窗最小笔数（collect_all 口径）
MIN_TIER_N = 50
#: C2 MDE 判读条款（v0.326 采 A）：相邻档差 |Δ| < 0.086 ⇒ 低置信（形态读数非结论）
C2_MDE = 0.086
#: C3 MDE 判读条款（主判据）：双窗 |ΔexpR| < 0.035 ⇒ 与零无法区分
C3_MDE = 0.035
#: C3 权重格三组写死（原始权重；归一化到平均权重=1）
WEIGHT_GRID_RAW: dict[str, dict[str, float]] = {
    "W1": {"high": 1.5, "mid": 1.0, "low": 0.5},  # 线性倾斜
    "W2": {"high": 2.0, "mid": 1.0, "low": 0.0},  # 强倾斜（低档不做=过滤器对照）
    "W3": {"high": 1.5, "mid": 1.0, "low": 1.0},  # 只加码高档
}
DEFAULT_N_RANDOM = 50  # C4 随机打乱归属臂数
C4_MIN_POOL = 50  # C4 最小池（池满才许 confirmed）
#: 簇映射命中率下限（v0.336，owner review——fail-closed）：交易经
#: (code, entry_date) 回挂信号所属簇，命中率低于此 ⇒ RuntimeError 不落盘
#: （映射缺失=口径缺陷不猜——负 id 单元素簇兜底会把 C4 静默退回逐笔打乱，
#: 正是 v0.335 修掉的 bug；同空结果护栏哲学）
CLUSTER_MAP_MIN_RATE = 0.99
COST_BPS_DEFAULT = 25.0

#: W2 对照注明（报告必带；预注册写死读法）
W2_NOTE = (
    "W2 低档权重 0 = R36 Phase 4 已证伪的过滤器路线（v0.292 在案）——保留它"
    "是做对照：W2 若「过线」而 W1/W3 不过，读作「过滤器效应残存」而非"
    "「分层成立」，不与分层结论混读"
)
#: 组合层未测注明（报告必带；预注册写死边界）
PORTFOLIO_NOTE = (
    "组合层加权 rdd + v2.1 相对门（参照=等仓位）需要逐笔仓位权重——"
    "simulate_portfolio_topn 是固定 risk_pct 制、不支持逐笔权重；按 owner "
    "拍板不改引擎，本单元 C3 只报交易层加权 expR，组合层标注「未测」"
    "（引擎支持后补测属新单元）"
)


def _normalized_grid() -> dict[str, dict[str, float]]:
    """权重格归一化（平均权重=1；W3 = 1.5/1/1 ⇒ 9/7, 6/7, 6/7 ≈ 1.29/0.86/0.86）。"""
    out: dict[str, dict[str, float]] = {}
    for g, w in WEIGHT_GRID_RAW.items():
        m = sum(w.values()) / len(w)
        out[g] = {k: v / m for k, v in w.items()}
    return out


#: C3 权重格（归一化后；判定用这份——Δ 不被敞口驱动）
WEIGHT_GRID: dict[str, dict[str, float]] = _normalized_grid()


# ---------------------------------------------------------------------------
# 信号收集 / 分档 / 重放
# ---------------------------------------------------------------------------


def collect_scored(per_code: dict[str, dict]) -> tuple[list[dict], dict[str, int]]:
    """每窗信号清单：code/date/i/score/sig（缺 V0 分的丢弃并记账——分档
    自变量缺失的信号进不了任何档）。"""
    out: list[dict] = []
    n_signals = 0
    n_score_missing = 0
    for code, pack in per_code.items():
        for s in pack["signals"]:
            n_signals += 1
            score = pack["scores"].get(s["date"])
            if score is None:
                n_score_missing += 1
                continue
            out.append(
                {
                    "code": code,
                    "date": s["date"],
                    "i": s["i"],
                    "score": score,
                    "sig": s,
                }
            )
    return out, {
        "n_signals": n_signals,
        "n_score_missing": n_score_missing,
        "n_subset": len(out),
    }


def _replay(
    per_code: dict[str, dict],
    subset: list[dict],
    regime: dict[str, str],
    cost_bps: float,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]],
) -> list[dict]:
    """全候选（collect_all）按钉死档重放一次——分档效应的隔离靠「同一批
    交易按分数归档」，不靠多档重放。"""
    if replay_fn is not None:
        return replay_fn(subset, dict(EXIT_PARAMS))
    return fes.replay_signals(per_code, subset, dict(EXIT_PARAMS), regime, cost_bps)


def _margin_of(trades: list[dict]) -> Optional[float]:
    """交易集 margin（胜率−盈亏平衡胜率；与 combine_readings 同公式单源）。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    if not trades:
        return None
    tsum = bt.summarize_realized(trades)  # 已实现口径（v0.383）
    return sg._margin({"win": tsum.get("win_rate"), "payoff": tsum.get("payoff_ratio")})


def weighted_expr(
    labels: list[str], trades: list[dict], weights: dict[str, float]
) -> Optional[float]:
    """加权 expectancy_R = Σw·R/Σw（trades 的 r_multiple 按档加权；
    r_multiple 缺失的笔跳过；权重和=0 ⇒ None）。"""
    num = 0.0
    den = 0.0
    for lab, t in zip(labels, trades):
        r = t.get("r_multiple")
        if r is None:
            continue
        w = weights[lab]
        num += w * r
        den += w
    return (num / den) if den > 0 else None


def arm_tier_draw(
    _labels: list[str], clusters: list[int], rng: random.Random
) -> list[str]:
    """C4 臂档归属（v0.335 owner review 修）：**整簇抽签换档**——
    ``kit.cluster_draw`` 单源，同簇交易同档，各档笔数组成在臂间自然
    波动（不再逐笔 shuffle 对齐笔数：逐笔打乱把簇拆散、零假设方差被
    低估、q95 偏低——owner 零假设模拟簇大小 20 时假过线 15/40≈38%）。
    ``_labels`` 仅供哨兵/诊断对照（逐笔打乱旧法以它为母体），本函数
    不按它计数对齐。"""
    return kit.cluster_draw(clusters, TIERS, rng)


def window_readings(
    trades: list[dict],
    cuts: list[float],
    cluster_of: Optional[dict[tuple[Any, Any], int]] = None,
) -> dict[str, Any]:
    """单窗读数：按挖掘窗切点归档 → 逐档 n/n_clusters/margin + 三组权重
    加权 expR 与 Δ。

    等权基准 = 同批交易 r_multiple 均值（归一化权重格保证总敞口一致——
    Δ 只来自「把钱挪到哪一档」，不被敞口驱动）。``cluster_of`` = (code,
    entry_date) → 簇 id（v0.335：每档簇数入报告供判读打折；映射缺失的
    交易按单元素簇计——负 id 占位不与真实簇撞号）。**v0.336**：映射
    命中率入报告（``cluster_map``；缺失=负 id 计数），由 run_study
    按 ``CLUSTER_MAP_MIN_RATE`` fail-closed——缺失会把 C4 静默退回
    逐笔打乱（v0.335 修掉的 bug），口径缺陷不猜。"""
    trades = [t for t in trades if t.get("score") is not None]
    labels = [TIERS[fes.bucket_of(t["score"], cuts)] for t in trades]
    cluster_of = cluster_of or {}
    clusters = [
        cluster_of.get((t.get("code"), t.get("entry_date")), -(k + 1))
        for k, t in enumerate(trades)
    ]
    n_miss = sum(1 for cid in clusters if cid < 0)  # 负 id = 映射缺失兜底
    tiered = {
        tier: [t for t, lb in zip(trades, labels) if lb == tier] for tier in TIERS
    }
    rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    equal = (sum(rs) / len(rs)) if rs else None
    groups: dict[str, Any] = {}
    for g, w in WEIGHT_GRID.items():
        we = weighted_expr(labels, trades, w)
        groups[g] = {
            "weighted_expR": we,
            "delta_expR": (we - equal)
            if (we is not None and equal is not None)
            else None,
        }
    return {
        "labels": labels,
        "clusters": clusters,
        "trades": trades,
        "tiered": tiered,
        "equal_expR": equal,
        "cluster_map": {
            "mapped": len(trades) - n_miss,
            "total": len(trades),
            "rate": (1.0 - n_miss / len(trades)) if trades else 1.0,  # 空集真空率 1.0
        },
        "tiers": {
            tier: {
                "n": len(tiered[tier]),
                "n_clusters": len(
                    {cid for cid, lb in zip(clusters, labels) if lb == tier}
                ),
                "margin": _margin_of(tiered[tier]),
            }
            for tier in TIERS
        },
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# 判据（R42 跑数前写死；判据件=criteria_kit 单源）
# ---------------------------------------------------------------------------


def judge_c1(readings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """C1：每档每窗 n ≥ 50——不过 ⇒ untested（样本不足≠否定证据，优先）。

    修订（v0.335，owner review）：阈值不变（按笔数），每档每窗**簇数**
    一并入报告（n_clusters）供判读打折——collect_all 口径下交易成簇，
    有效样本≈簇数（见 R42 功效节修订段；改阈值=判据变更，不擅自改）。"""
    per = {
        w: {tier: rd["tiers"][tier]["n"] for tier in TIERS}
        for w, rd in readings.items()
    }
    per_clusters = {
        w: {tier: rd["tiers"][tier]["n_clusters"] for tier in TIERS}
        for w, rd in readings.items()
    }
    ok = all(n >= MIN_TIER_N for ns in per.values() for n in ns.values())
    return {
        "ok": ok,
        "n_taken": per,
        "n_clusters": per_clusters,
        "min_tier_n": MIN_TIER_N,
        "rule": f"每档每窗 n ≥ {MIN_TIER_N}（collect_all 全候选分档口径；"
        "不过=untested 不判 falsified；v0.335 修订：阈值不变、簇数入报告"
        "供判读打折）",
    }


def judge_c2(readings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """C2（单调性硬前提）：双窗 margin(高)≥margin(中)≥margin(低) 且 高−低>0。

    打平读法写死：按字面「≥」——「高=中>低」「高>中=低」都算过（只要
    高−低>0）；任何一处倒挂（<）即不过。MDE 条款（v0.326）：相邻档差
    |Δ| < 0.086 ⇒ 标低置信（形态读数非结论）——C2 问的是符号序，MDE 不
    改变判定本身。任一档 margin 缺失（如全胜组 payoff 无定义）⇒ 该窗
    不可评 ⇒ ok=None（provisional，不把数据问题判成候选问题）。"""
    windows: dict[str, Any] = {}
    unevaluable = False
    ok = True
    for w, rd in readings.items():
        m = {tier: rd["tiers"][tier]["margin"] for tier in TIERS}
        h, md, lo = m["high"], m["mid"], m["low"]
        if h is None or md is None or lo is None:
            windows[w] = {"margins": m, "evaluable": False}
            unevaluable = True
            continue
        adj = {"high_mid": h - md, "mid_low": md - lo}
        mono = (h >= md >= lo) and (h - lo) > 0
        windows[w] = {
            "margins": m,
            "high_low": h - lo,
            "adjacent": adj,
            "monotone": mono,
            "mde_low_confidence": any(abs(d) < C2_MDE for d in adj.values()),
            "evaluable": True,
        }
        ok = ok and mono
    return {
        "ok": (None if unevaluable else bool(ok)),
        "windows": windows,
        "mde": C2_MDE,
        "low_confidence": any(wd.get("mde_low_confidence") for wd in windows.values()),
        "rule": "双窗 margin(高)≥margin(中)≥margin(低) 且 高−低>0（相邻打平不算"
        "倒挂，高−低必须严格>0）；MDE 条款：相邻档差 |Δ|<0.086 ⇒ 低置信"
        "（形态读数非结论，MDE 不改变判定本身）",
    }


def judge_c3(readings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """C3（仓位增量，问法本体+主判据）：任一组双窗加权 expR > 等权 ⇒ 过。

    MDE 判读条款（写死）：逐窗按 |ΔexpR| 与 0.035 的关系定读法——
    |Δ|<MDE ⇒ indistinguishable（与零无法区分：不读增量也不读证伪）；
    |Δ|≥MDE 才按符号读 positive/negative。任一组双窗都 positive ⇒ 过。
    预注册四态写死 falsified 只来自 C2/C4 ⇒ C3 不过只放行 None
    （provisional，不判死）；可读负增量如实记录在 groups 里供判读。"""
    groups: dict[str, Any] = {}
    any_pass = False
    best_g: Optional[str] = None
    best_d: Optional[float] = None
    for g in WEIGHT_GRID:
        deltas = {w: rd["groups"][g]["delta_expR"] for w, rd in readings.items()}
        reading: dict[str, str] = {}
        for w, d in deltas.items():
            if d is None:
                reading[w] = "unevaluable"
            elif abs(d) < C3_MDE:
                reading[w] = "indistinguishable"
            elif d > 0:
                reading[w] = "positive"
            else:
                reading[w] = "negative"
        passed = all(r == "positive" for r in reading.values())
        any_pass = any_pass or passed
        d_m = deltas.get("mining")
        if d_m is not None and (best_d is None or d_m > best_d):
            best_g, best_d = g, d_m
        groups[g] = {"delta_expR": deltas, "reading": reading, "pass": passed}
    return {
        "ok": (True if any_pass else None),
        "groups": groups,
        "mde": C3_MDE,
        "best_group_mining": best_g,
        "best_delta_mining": best_d,
        "rule": "任一组双窗加权 expR（Σw·R/Σw）> 等权 ⇒ 过（主判据）；MDE 条款："
        "|ΔexpR|<0.035 ⇒ 与零无法区分（不读增量也不读证伪），|Δ|≥MDE 才按"
        "符号判读；不过不放行不判死（预注册四态：falsified 只来自 C2/C4）",
        "w2_note": W2_NOTE,
        "portfolio_note": PORTFOLIO_NOTE,
    }


# ---------------------------------------------------------------------------
# 主研究
# ---------------------------------------------------------------------------


def _layout_of(args: Any) -> tuple[str, list[tuple[str, str, str]], tuple[str, str]]:
    """窗口四参 ⇒ (layout, [(挖掘段名, start, end)…], (判定 start, end))。

    全 None ⇒ **v2 布局**（挖掘=段并集 A∪B，判定=受保护段 2017~2021——
    load_window 段常量单源）；四个都显式 ⇒ **legacy 单段**（R40 兼容通道，
    段内子窗）；只给一部分 ⇒ ValueError（不许半截布局，TODO #90）。
    """
    quad = (args.mining_start, args.mining_end, args.judgment_start, args.judgment_end)
    n_given = sum(v is not None for v in quad)
    if n_given == 0:
        return (
            "v2",
            [("mining_a", *lw.SEG_MINING_A), ("mining_b", *lw.SEG_MINING_B)],
            lw.SEG_JUDGMENT,
        )
    if n_given == 4:
        return (
            "legacy",
            [("mining", str(args.mining_start), str(args.mining_end))],
            (str(args.judgment_start), str(args.judgment_end)),
        )
    raise ValueError(
        "窗口四参（--mining-start/end 与 --judgment-start/end）须全给或全不给"
        "（全不给=v2 布局：挖掘 A∪B / 判定 2017~2021）——不许半截布局（TODO #90）"
    )


def run_study(
    args: Any,
    *,
    warm_fn: Optional[Callable[[str, str], dict[str, dict]]] = None,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
) -> dict[str, Any]:
    """双窗研究：预热 → 挖掘窗分位切档 → collect_all 分档重放 → C1~C4 → 四态。

    v2 布局：挖掘 A/B 两段各 warm/collect、（v2）段末隔离带 clip（在
    replay/判据之前，剔除数入报告 segments 块）后 A∪B **合并一池**算挖掘
    读数，判定段单独读数；legacy：单段挖掘窗不 clip（R40 兼容通道，行为
    与切换前逐位一致）。"""
    # 合成守卫在入口快照：下方生产分支会把 warm_fn 重绑成真实闭包
    # （noqa: F811），事后再判恒 True ⇒ 生产跑被误判合成、判定窗读取
    # 不入台账（r42_probe 事故根因，v0.338 排查的实锤答案）。
    injected = warm_fn is not None or replay_fn is not None
    layout, mining_segs, judgment_span = _layout_of(args)
    # 判定段批次锁（v0.374，TODO #95）：数据加载之前硬门——合成运行（注入件
    # 非 None）/legacy 窗不查；v2 缺省窗生产方向必须带合法批次
    from custos.research import judgment_batch as jb  # noqa: PLC0415

    jb.gate_judgment_read(
        args,
        _build_parser(),
        synthetic=injected,
        judgment_span=judgment_span,
        tool="score_tier_position_study",
    )
    earliest_start = min(s for _name, s, _e in mining_segs)
    regime: dict[str, str] = {}
    if warm_fn is None:
        from custos.research import backtest_factors as bt  # noqa: PLC0415
        from custos.research import exit_campaign as ec  # noqa: PLC0415
        from custos.research import score_return_study as srs  # noqa: PLC0415

        ns = argparse.Namespace(
            codes_file=args.codes_file,
            universe_local=args.universe_local,
            universe_sample=args.universe_sample,
            seed=args.universe_seed,
            codes=args.codes,
        )
        codes = bt._resolve_universe(ns, _build_parser())
        args.universe_meta = getattr(
            ns, "universe_meta", None
        )  # 回塞：provenance 三件套（v0.356 族）
        regime = bt.load_amv_regime(since=earliest_start)
        if not regime:
            raise RuntimeError("0AMV regime 读不到（compass_amv）——本机无数据？")
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

        index_df = (
            local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
            .sort_values("date")
            .reset_index(drop=True)
        )

        def warm_fn(start: str, end: str) -> dict[str, dict]:  # noqa: F811
            return ec.warm_v0_signals(
                codes,
                regime,
                index_df,
                count=args.count,
                cost_bps=args.cost_bps,
                start=start,
                end=end,
                # v2 段前预热 300 根（v0.359，owner review #3）；legacy 0 根
                warmup_bars=300 if layout == "v2" else 0,
            )

    # ── 逐段 warm/collect →（v2）段末隔离带 clip（段末 SEGMENT_BUFFER_DAYS
    # 自然日截止开仓——防 open_end 强制平仓偏差（owner review #4；数据本已截到段末，
    # 不存在跨段泄露；R11 已证 open_end 系统性偏正）；在 replay/判据之前；剔除数
    # 入报告 segments 块；legacy 兼容通道不 clip，逐位保真）──
    segments: dict[str, dict[str, Any]] = {}
    for name, s, e in [*mining_segs, ("judgment", *judgment_span)]:
        pc = warm_fn(s, e)
        n_signals = sum(len(pack["signals"]) for pack in pc.values())
        subset, acc = collect_scored(pc)
        n_clipped = 0
        if layout == "v2":
            clipped_subset = lw.clip_entries(subset, e, key="date")
            n_clipped = len(subset) - len(clipped_subset)
            subset = clipped_subset
        segments[name] = {
            "span": (s, e),
            "per_code": pc,
            "subset": subset,
            "accounting": acc,
            "n_signals": n_signals,
            "n_clipped": n_clipped,
        }

    # ── 读数池分组：v2 挖掘 = A∪B 合并一池（判据形态不变）；legacy 单段 ──
    mining_groups = [segments[name] for name, _s, _e in mining_segs]
    groups = {"mining": mining_groups, "judgment": [segments["judgment"]]}
    pools = {w: [r for g in gs for r in g["subset"]] for w, gs in groups.items()}
    if not pools["mining"] or not pools["judgment"]:
        raise RuntimeError(
            f"空结果护栏：挖掘 {len(pools['mining'])} / 判定 "
            f"{len(pools['judgment'])} 信号——不落盘（宇宙/窗口/数据有问题？）"
        )

    # ── 分档切点只在挖掘窗估计（双窗硬隔离——判定窗泄漏是死罪）──
    cuts = fes.quantile_cuts([r["score"] for r in pools["mining"]], TIER_QS)

    # ── 簇划分（kit.cluster_ids 单源，v0.335）：(code, date)→簇 id——
    # 交易经 (code, entry_date) 回挂信号所属簇（信号⇄交易一一对应）──
    cluster_maps = {
        w: {
            (r["code"], r["date"]): cid
            for r, cid in zip(pools[w], kit.cluster_ids(pools[w]))
        }
        for w in ("mining", "judgment")
    }

    # ── 各读数池重放（钉死档 collect_all；v2 挖掘池逐段重放后合并一池）→
    # 按切点归档出读数 ──
    readings = {
        w: window_readings(
            [
                t
                for g in gs
                for t in _replay(
                    g["per_code"], g["subset"], regime, args.cost_bps, replay_fn
                )
            ],
            cuts,
            cluster_of=cluster_maps[w],
        )
        for w, gs in groups.items()
    }

    # ── 簇映射命中率护栏（v0.336，owner review）：映射缺失 ⇒ 交易被当
    # 单元素簇 ⇒ C4 静默退回逐笔打乱（v0.335 修掉的 bug）——fail-closed
    # 不落盘（口径缺陷不猜，同空结果护栏哲学）──
    for w in ("mining", "judgment"):
        cm = readings[w]["cluster_map"]
        if cm["rate"] < CLUSTER_MAP_MIN_RATE:
            raise RuntimeError(
                f"簇映射命中率护栏：{w} 窗 {cm['mapped']}/{cm['total']} = "
                f"{cm['rate']:.3f} < {CLUSTER_MAP_MIN_RATE}——交易回挂不上信号簇"
                "（键格式漂移？），不落盘"
            )

    c1 = judge_c1(readings)
    c2 = judge_c2(readings)
    c3 = judge_c3(readings)

    # ── C4：**整簇**打乱「交易→档」归属 N=50 臂（v0.335 owner review 修：
    # 逐笔打乱把簇拆散、q95 偏低；每簇独立均匀抽一档、各档笔数自然波动——
    # 每臂取同套权重格 max，与 C3 同预算）；纯算术不重放 ──
    m_rd = readings["mining"]

    def _arm(i: int) -> Optional[float]:
        arm_rng = random.Random(f"{args.seed}-arm{i}")  # 臂级种子写死可复现
        perm = arm_tier_draw(m_rd["labels"], m_rd["clusters"], arm_rng)
        best: Optional[float] = None
        for wts in WEIGHT_GRID.values():
            we = weighted_expr(perm, m_rd["trades"], wts)
            if we is None or m_rd["equal_expR"] is None:
                continue
            d = we - m_rd["equal_expR"]
            best = d if best is None else max(best, d)
        return best

    c4_pool = kit.assemble_c4_pool(args.n_random, _arm)
    pool = c4_pool["pool"]
    c4_state = kit.c4_state_of(
        pool, min_pool=args.c4_min_pool, plan_delta=c3["best_delta_mining"]
    )
    c4 = {
        "state": c4_state,
        "pool": pool,
        "pool_size": len(pool),
        "target_pool": c4_pool["target_pool"],
        "max_arms": c4_pool["max_arms"],
        "evaluated": c4_pool["evaluated"],
        "q95": kit.q95(pool),
        "candidate_delta_mining": c3["best_delta_mining"],
        "candidate_group": c3["best_group_mining"],
        "min_pool": args.c4_min_pool,
        "note": "整簇打乱「交易→档」归属（v0.335 修订：簇=同 code 信号点相邻 "
        "≤5 根 bar 连续段，kit.cluster_ids/cluster_draw 单源；每簇独立均匀"
        "抽一档、同簇同档，各档笔数臂间自然波动——不逐笔对齐笔数）；每臂取"
        "同套权重格 max（与 C3 同预算）；C3 最佳组（挖掘窗 ΔexpR）> 池 q95 才 "
        "confirmed；池构造/状态机=criteria_kit 单源（v0.322）",
    }

    # ── 四态结局（criteria_kit 单源：C1 优先；C3 None ⇒ provisional 不判死）──
    verdict = kit.verdict_four_state(
        c1_ok=c1["ok"], c2_ok=c2["ok"], c3_ok=c3["ok"], c4_state=c4_state
    )

    # ── 成本副读数（owner review #6，v0.329）：高档/低档交易集 50bps 解析
    # 双报（高−低 margin 是 C2 的关键量——绝对口径翻号主战场）──
    from custos.research.cost_sensitivity import cost_side_block  # noqa: PLC0415

    cost_sens = cost_side_block(
        {
            f"{tier}_{w}": readings[w]["tiered"][tier]
            for w in ("mining", "judgment")
            for tier in ("high", "low")
        },
        base_bps=args.cost_bps,
        deltas={
            f"d_margin_high_low_{w}": (f"high_{w}", f"low_{w}")
            for w in ("mining", "judgment")
        },
    )

    # 判定窗台账 v2 口径（TODO #90，同 exit_campaign/plan_rules_replay）：
    # 只有判定窗=受保护段（SEG_JUDGMENT 2017~2021）才登记（新键
    # judgment_2017_2021——旧键 judgment/pre2019 已冻结，永不新写）；legacy
    # 判定窗已并入挖掘 B，读挖掘段不入台账（挖掘数据自由可读）。
    _is_protected_judgment = lw.touches_judgment(*judgment_span)
    _wu_k = (
        wu.record_use(
            "R42",
            "judgment_2017_2021",
            args.tag,
            "C1~C4 判定窗读数",
            synthetic=injected,
        )
        if _is_protected_judgment
        else None
    )
    if getattr(args, "_judgment_gated", False):
        # 批次锁过闸（v0.374）⇒ 登记读完（末位候选读完自动关批）
        jb.mark_read(
            args.batch_id,
            args.candidate_id,
            tool="score_tier_position_study",
            tag=args.tag,
        )
    from custos.research import provenance as pv  # noqa: PLC0415

    report = {
        "schema": "score_tier_position/v1",
        "tag": args.tag,
        "verdict": verdict,
        "window_usage": {
            "window": (
                "judgment_2017_2021" if _is_protected_judgment else "mining_segment"
            ),
            "k": _wu_k,
            "note": (
                wu.usage_note("R42", "judgment_2017_2021", _wu_k)
                if _is_protected_judgment
                else "判定窗在挖掘段内——不入台账（v2 口径）"
            ),
        },
        "provenance": pv.build(
            args,
            unit="R42",
            criteria_version="v0.312/v0.326",
            pre_reg_doc=RESEARCH_DIR / "R42_score_tier_position.md",
            data_last_date=max(
                filter(
                    None,
                    (pv.last_date_of(seg["per_code"]) for seg in segments.values()),
                ),
                default=None,
            ),
        ),
        "windows": {
            "mining": {"start": mining_segs[0][1], "end": mining_segs[-1][2]},
            "judgment": {"start": judgment_span[0], "end": judgment_span[1]},
        },
        "params": {
            "exit": {"name": EXIT_NAME, "params": EXIT_PARAMS},
            "tier_qs": list(TIER_QS),
            "weight_grid_raw": WEIGHT_GRID_RAW,
            "weight_grid": WEIGHT_GRID,
            "cost_bps": args.cost_bps,
            "top_n": args.top_n,
            "note": "档内交易=collect_all 全候选分档（非 top_n——top_n 截断下判定窗"
            "每档 ~49 笔会贴 C1 线，v0.301 在案）；--top-n 不参与任何读数"
            "（组合层未测，见 notes）；出场=pct5_trail08 参数原样取自 "
            "strategy_grid.DEFAULT_EXIT_GRID",
        },
        "cuts": {
            "qs": list(TIER_QS),
            "values_mining_estimated": cuts,
            "note": "切点只在挖掘窗估计——判定窗直接应用（判定窗泄漏=死罪，双窗纪律）",
        },
        "accounting": {name: seg["accounting"] for name, seg in segments.items()},
        "cluster_map": {
            **{w: readings[w]["cluster_map"] for w in ("mining", "judgment")},
            "min_rate": CLUSTER_MAP_MIN_RATE,
            "note": "簇映射命中率（v0.336）：交易经 (code, entry_date) 回挂信号"
            "所属簇——低于 min_rate fail-closed 不落盘（映射缺失 ⇒ C4 静默退回"
            "逐笔打乱，口径缺陷不猜）",
        },
        "tiers": {
            w: {tier: rd["tiers"][tier] for tier in TIERS} for w, rd in readings.items()
        },
        "readings": {
            w: {"equal_expR": rd["equal_expR"], "groups": rd["groups"]}
            for w, rd in readings.items()
        },
        "cost_sensitivity": cost_sens,
        "forward_holdout_note": lw.EXIT_BARS_HOLDOUT_NOTE,
        "criteria": {
            "C1": c1,
            "C2": c2,
            "C3": c3,
            "C4": c4,
            "rule_note": "R42-C1~C4 跑数前写死（v0.312 落档；v0.326 功效节采 A=保 3 档"
            "+MDE 判读条款，C3 升主判据）；C5=pre2019 单独终步只能杀——C2 和 C3 "
            "都过线才启动，owner 拍板发令（本工具不建 C5）",
        },
        "objective_version": sg.OBJECTIVE_VERSION,
        "notes": [W2_NOTE, PORTFOLIO_NOTE],
        "generated_at": cn_now().isoformat(timespec="seconds"),
    }
    if layout == "v2":
        report["layout"] = "v2"
        report["segments"] = {
            **{
                name: {
                    "start": seg["span"][0],
                    "end": seg["span"][1],
                    "entry_deadline": lw.entry_deadline(seg["span"][1]),
                    "n_signals": seg["n_signals"],
                    "n_clipped": seg["n_clipped"],
                }
                for name, seg in segments.items()
            },
            "mining_total": {
                "n_signals": sum(g["n_signals"] for g in mining_groups),
                "n_clipped": sum(g["n_clipped"] for g in mining_groups),
                "n_pooled": len(pools["mining"]),
                "note": "挖掘读数=A∪B 合并一池（判定段独立；段末 90 天隔离带"
                "禁开仓——entry_deadline 为各段入场截止日，n_signals 为隔离带"
                "前收集数，n_pooled 为合并入池数）",
            },
        }
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R42 打分→仓位分层 Phase 1：V0 分数挖掘窗分位切 3 档 + collect_all 全候选分档（pct5_trail08 钉死）+ C1~C4 机械读数"
    )
    ap.add_argument(
        "--mining-start",
        default=None,
        help="挖掘窗起点（四窗参全缺省 ⇒ v2 布局：挖掘 A 2000~2016 ∪ B "
        "2022~2026-09-04 合并池 / 判定 2017~2021；全显式=legacy 单段）",
    )
    ap.add_argument("--mining-end", default=None)
    ap.add_argument("--judgment-start", default=None)
    ap.add_argument("--judgment-end", default=None)
    ap.add_argument("--codes", default="")
    ap.add_argument("--codes-file", default="")
    ap.add_argument("--universe-sample", type=int, default=0)
    ap.add_argument("--universe-local", action="store_true")
    ap.add_argument("--universe-seed", type=int, default=42)
    ap.add_argument(
        "--count",
        type=int,
        default=None,
        help="每股回溯 K 线根数（缺省=按最早挖掘段起点自动推算，显式值覆盖）",
    )
    ap.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="横截面择优（⚠️ R42 口径=collect_all 全候选分档，本项不参与读数，仅为 CLI 同族形状保留）",
    )
    ap.add_argument("--cost-bps", type=float, default=COST_BPS_DEFAULT)
    ap.add_argument("--n-random", type=int, default=DEFAULT_N_RANDOM)
    ap.add_argument("--c4-min-pool", type=int, default=C4_MIN_POOL)
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out-dir", default=str(LOGS))
    # 判定段批次锁（v0.374，TODO #95）：读判定段 2017~2021 须带批次身份
    ap.add_argument("--batch-id", default="", help="判定段批次号（攒批协议）")
    ap.add_argument("--candidate-id", default="", help="批内候选 id")
    ap.add_argument(
        "--print-config-hash",
        action="store_true",
        help="只打印 <tool>:<配置哈希> 并退出（冻结入批流程；不碰数据）",
    )
    return ap


def _check_windows(args: Any, ap: argparse.ArgumentParser) -> None:
    """legacy 显式四窗校验：逐窗段检查（load_window.segment_violation 单源——
    越段/触 holdout 硬拒绝）+ 判定窗∩挖掘窗**不相交**（取代旧
    mining_end<judgment_start 次序规则：v2 判定段天然夹在挖掘 A/B 之间，
    旧规则作废，owner 2026-10-13 定案 TODO #90）。v2 缺省布局=段常量，
    天然合法；半截布局硬拒绝。"""
    try:
        layout, mining_segs, judgment = _layout_of(args)
    except ValueError as exc:
        ap.error(str(exc))
    if layout == "v2":
        return
    _, ms, me = mining_segs[0]
    js, je = judgment
    for wname, s, e in (("mining", ms, me), ("judgment", js, je)):
        msg = (
            lw.mining_violation(s, e)
            if wname == "mining"
            else lw.segment_violation(s, e)
        )
        if msg:
            ap.error(f"{wname} {msg}")
    if not (me < js or je < ms):
        ap.error(
            f"挖掘窗 {ms}~{me} 与判定窗 {js}~{je} 相交——双窗硬隔离"
            "（v2 口径：不相交即可，不再要求 mining_end < judgment_start 次序）"
        )


def main(
    argv: Optional[list[str]] = None,
    *,
    warm_fn: Optional[Callable[[str, str], dict[str, dict]]] = None,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = _build_parser()
    args = ap.parse_args(argv)
    args.cmdline = " ".join(argv) if argv is not None else " ".join(sys.argv[1:])
    _check_windows(args, ap)  # legacy 显式四窗：段检查 + 双窗不相交（v2 口径）
    if warm_fn is None:  # 生产路径才做加载到达校验（注入路径无真实加载）
        from custos.research.exit_c5_terminal import check_reach  # noqa: PLC0415
        from custos.research.load_window import resolve_count  # noqa: PLC0415

    try:
        if warm_fn is None:
            # --count 缺省按最早挖掘段起点推算（v2=2000-01-01 全历史量级）
            earliest = min(s for _n, s, _e in _layout_of(args)[1])
            args.count = resolve_count(args.count, earliest)  # v0.328 缺省自动推算
            check_reach(args.count, earliest)
        if args.print_config_hash:
            # 冻结入批流程（TODO #95）：哈希必须与批次门同状态（参数归一化之后）
            from custos.research import judgment_batch as jb  # noqa: PLC0415

            return jb.print_config_hash_and_exit(args, "score_tier_position_study")
        rep = run_study(args, warm_fn=warm_fn, replay_fn=replay_fn)
    except (RuntimeError, ValueError) as exc:
        print(f"[score_tier_position] {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir) / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_score_tier_position__{args.tag}.json"
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    c = rep["criteria"]
    print(
        f"[score_tier_position] verdict={rep['verdict']} C1={c['C1']['ok']} "
        f"C2={c['C2']['ok']} C3={c['C3']['ok']} C4={c['C4']['state']} ⇒ {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
