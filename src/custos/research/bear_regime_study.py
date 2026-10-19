# -*- coding: utf-8 -*-
"""R40 · 0AMV 空头区间做多策略全栈研究终端
（预注册 ``governance/research/R40_bear_regime_long_strategy.md``）。

问法（owner 2026-10-08 立项）：0AMV **空头区间**内主动开仓的
（入场门 × 止损/止盈档）全栈最优——现状=空头区间纯防守（只卖不买+
反弹减仓），主动开仓从未被研究。

做的事（一次一单）：
  ① 逐段逐股加载一次（v2=挖掘 A/判定/挖掘 B 三段，legacy=挖掘/判定双窗）
     → **全量 46 门 ENTRY_GATES** 逐门扫信号（
     ``_amv_checker`` 反转：``invert_regime_bearish`` 把「空头」改标
     「做多」其余标「空头」——仅空头日放行；**无映射日=粘滞/neutral
     不放行**（问的是「空头区间」不是「非做多区间」，写死））→ as-of
     V0 分（与门无关，按 (code, date) 去重只算一次）；
  ② 出场轴 = ``strategy_grid.DEFAULT_EXIT_GRID`` 5 档 ⇒ 46×5=**230 格**
     全枚举（重放缓存 per（门, 档, 窗），与 factor_exit_study 同骨架）；
  ③ C1~C4 判定（R40 跑数前写死，v0.301/v0.302/v0.307 修订在案）：
     C1 top 每窗 n_taken≥100——**不过 ⇒ untested（不可判）不判 falsified**
     （样本不足≠否定证据，稀疏门被 max-of-230 挑中再判死=重演 v0.299
     纠正过的错）；C2 top margin 双窗同向为正 **且双窗 > 随机臂对应窗池
     q95**（基准=随机入场臂非空仓——同宇宙同出场格，幸存者偏差两边抵消）；
     C3 top 出场参数 ±50%×4（exit_genome.perturb_50 吸附档位）C2 结论
     零翻转——**top 出场无可扰参数轴（base_low params={}）⇒ not_applicable
     不空转不放行 candidate**（v0.307）；C4 top 挖掘窗 margin > 随机臂
     挖掘池 q95（与 C2 挖掘半窗同池，重合是设计使然），池≥50 才
     confirmed 否则 provisional；
  ④ **随机臂对等纪律（v0.302）+ 预算对等（v0.307）**：每条臂在**挖掘
     窗内**完整复刻 top 的 max-of-230 选型（逐门按该门实测信号数 n_g
     空头日随机 (code, bar) 入场——0 信号门跳过、无放回（v0.308）——
     × 5 档取 max ⇒ **冻结（n_g, 出场配置）**），判定窗读数由冻结配置
     产生——**选择只做一次，样本外比较；禁止每窗重新取 max**（top 冻结
     vs 臂当窗重选不对等）。原 max-of-5 构造标尺系统性偏低（top 享受
     230 格选择效应、臂只有 5 格，q95 过易）。性能（v0.309，结果逐位
     不变）：母体每窗预建一次 + as-of 打分 (code,i) 缓存跨臂跨门共用
     （打分次数上限=母体大小）；n_requested/n_returned 记账入报告。
     C5 的 pre2019 池同理（Phase 3 工具时同纪律）。

CLI 护栏（v2 窗口布局，TODO #90，owner 2026-10-13 拍板——本模块
``_check_windows``）：窗口四参**全不给** ⇒ v2 三段（挖掘 A 2000~2016 /
判定 2017~2021 / 挖掘 B 2022~2026-09-04——load_window 段常量单源；
挖掘读数 = A∪B **合并一池**，C1~C4 判据形态不变；三段末各
``clip_entries`` 一刀 = 段末 90 自然日隔离带，在 replay/判据之前）；
**全给** ⇒ legacy 单段双窗（逐窗 ``segment_violation`` 段校验 + 双窗
**不相交**——取代旧 ``mining_end < judgment_start`` 次序规则：v2 判定段
天然夹在挖掘 A/B 之间，旧规则作废；R40 旧窗 ⊆ 挖掘 B 自动合法，兼容通道
在案）；只给一部分 ⇒ ap.error（半截布局拒绝）。``--count``/check_reach
以最早段起点推算（v2=2000-01-01）。判定窗使用台账：只有判定窗=受保护段
2017~2021 才登记 ``judgment_2017_2021``；legacy 判定窗已并入挖掘 B ⇒
不入台账（读挖掘段自由，v2 口径）。LLM 不碰数值；v2 口径（margin 单量
+ rdd 相对门 v2.1——候选≥同窗同信号 pct5_trail08 参照档，只相对排序，引用连带 R11 声明）；**绝对 margin 读数不进
live 决策**（幸存者宇宙抬高绝对读数，相对结论才有效——R14 同族）。
"""

from __future__ import annotations

import argparse
import bisect
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.paths import RESEARCH_DIR
from custos.research import criteria_kit as kit
from custos.research import factor_exit_study as fes
from custos.research import load_window as lw
from custos.research import window_usage as wu
from custos.research.evolution import exit_genome as eg

#: 判据数值（R40 跑数前写死）
MIN_N_TAKEN = 100  # C1：top 每窗最小选中笔数
C3_DRAWS = 4  # C3 扰动臂数（±50% 同族）
DEFAULT_N_RANDOM = 50  # 随机臂数
C4_MIN_POOL = 50  # 池满才许 confirmed

_R11_R14_NOTE = (
    "目标函数 v2.1（margin 单量 + rdd 相对门：候选≥同窗 trail08 参照档）只相对排序；幸存者宇宙抬高绝对 "
    "margin 读数（退市票不在宇宙里）——相对结论才有效，绝对读数不进 live 决策"
)


# ---------------------------------------------------------------------------
# regime 反转与信号收集
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# R45 模式（v0.397，TODO #103）：空头区间 J<13 单门 × 总敞口 ≤30% × 4×4 出场网格
# ---------------------------------------------------------------------------

#: 止损宽度（信号日最低价 ×(1−x%)，同 R44 锚点）
R45_STOPS: tuple[float, ...] = (5.0, 8.0, 12.0, 15.0)
#: 止盈 / 清仓方案（stop_trigger=close；BBI 两连破为引擎缺省，T2 也保留）
R45_TPS: dict[str, dict[str, Any]] = {
    "T1": {"scale_out_frac": 0.5, "bbi_exit_consec": 2},  # 现行：两阳分批 + BBI 清仓
    "T2": {"trail_pct": 0.08, "bbi_exit_consec": 2},  # trail 8%（无分批）
    "T3": {"cost_zone_bars": 3, "bbi_exit_consec": 2},  # 成本区不涨就拍（cz3）
    "T4": {"time_stop_bars": 10, "bbi_exit_consec": 2},  # 时间止损 10 日
}
R45_EXIT_GRID: list[dict[str, Any]] = [
    {
        "name": f"x{x:g}_{t}",
        "params": {**tp, "stop_trigger": "close", "_anchor_x": x},
        "stop_x": x,
        "tp": t,
    }
    for x in R45_STOPS
    for t, tp in R45_TPS.items()
]
#: rdd 相对门参照格（事先固定、不经挑选：现行止盈 × 8% 中间档）
R45_REF = "x8_T1"
#: 总敞口 ≤30%：同时 ≤3 只 × 单仓名义 ≤10%，单笔风险 1%
R45_PORTFOLIO: dict[str, Any] = {
    "risk_pct": 0.01,
    "max_concurrent": 3,
    "max_pos_frac": 0.10,
}

#: 模块级口径（run_study 在 R45 模式下切换，finally 复位——R40 默认值逐位不变）
_REF_NAME = "pct5_trail08"
_PORTFOLIO: Optional[dict[str, Any]] = None


def _combine(trades: list[dict], top_n: int, ref: Any = None) -> Optional[dict]:
    """读数单源：R40 = V0 组合口径；R45 = 30% 敞口上限口径（_PORTFOLIO）。"""
    return fes.combine_readings(trades, top_n, ref=ref, portfolio=_PORTFOLIO)


def _anchor_subset(view: dict[str, dict], subset: list[dict], x: float) -> list[dict]:
    """信号级止损 = 信号日最低价 ×(1−x%)（stop_override 钩子，同 R41/R44）；
    df 缺失 / 越界 / 止损 ≥ 入场收盘的信号剔除（两侧同口径，不静默回退）。"""
    out = []
    for r in subset:
        df = (view.get(r["code"]) or {}).get("df")
        i = r.get("i")
        if df is None or i is None or not (0 <= int(i) < len(df)):
            continue
        low = float(df["low"].iloc[int(i)])
        close = float(df["close"].iloc[int(i)])
        stop = low * (1.0 - x / 100.0)
        if not (stop < close):
            continue
        out.append({**r, "sig": {**r["sig"], "stop_override": stop}})
    return out


def judge_c3_r45(configs: list[dict], top: Optional[dict]) -> dict[str, Any]:
    """R45-C3（预注册写死）：①top 的止损邻档（同止盈、相邻 x，端点单侧）两窗
    margin 均 >0；②两窗各自最优格（过门）的止盈方案与 top 相同。"""
    if top is None:
        return {"ok": None, "reason": "无 top"}
    by = {c["exit"]: c for c in configs}
    tp = top["exit"].split("_", 1)[1]
    xi = R45_STOPS.index(float(top["exit"].split("_", 1)[0][1:]))
    nbrs = [
        f"x{R45_STOPS[j]:g}_{tp}" for j in (xi - 1, xi + 1) if 0 <= j < len(R45_STOPS)
    ]
    smooth = all(
        (by.get(n, {}).get(w) or {}).get("margin") is not None
        and by[n][w]["margin"] > 0
        for n in nbrs
        for w in ("mining", "judgment")
    )
    best_tp: dict[str, Optional[str]] = {}
    for w in ("mining", "judgment"):
        ok = [c for c in configs if c.get(w) and c[w].get("objective") is not None]
        best_tp[w] = (
            max(ok, key=lambda c: c[w]["margin"])["exit"].split("_", 1)[1]
            if ok
            else None
        )
    consistent = all(v == tp for v in best_tp.values())
    return {
        "ok": bool(smooth and consistent),
        "neighbors": nbrs,
        "neighbors_positive": smooth,
        "best_tp_by_window": best_tp,
        "tp_consistent": consistent,
        "rule": "止损邻档两窗 margin>0 且两窗各自最优格止盈方案与 top 相同（R45 预注册）",
    }


def invert_regime_bearish(regime: dict[str, str]) -> dict[str, str]:
    """空头区间放行适配：「空头」→「做多」、其余（做多/中性）→「空头」。

    配合 ``_amv_checker`` 的 as-of 语义：无映射日取到最近的「非做多」⇒
    不放行——**粘滞/neutral 日不放行**（「空头区间」≠「非做多区间」，写死）。
    """
    return {d: ("做多" if v == "空头" else "空头") for d, v in regime.items()}


def warm_gates(
    codes: list[str],
    regime_bear: dict[str, str],
    index_df: Any,
    *,
    gates: list[str],
    count: int,
    cost_bps: float,
    start: str,
    end: str,
    warmup_bars: int = 0,
) -> dict[str, dict]:
    """R40 预热：逐股加载一次 → 逐门扫信号（门在内层 ⇒ df 只加载一次）→
    as-of V0 分（与门无关，按 (code, date) 去重）。

    与 ``exit_campaign.warm_v0_signals`` 同引擎同调用序（baseline scorer +
    entry_gate + amv + collect_all），差异只在循环嵌套。df 加载成功的票
    即使 0 信号也保留（随机臂母体=全宇宙空头日 bar，不是门信号集）。
    ``warmup_bars``（v0.359，owner review #3）：>0 时加载侧保留 start 前
    N 根作指标预热（定案豁免），**各门信号仍只收 date ≥ start**。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    _t0 = _time.time()  # TODO #92 warm 耗时埋点（[TIME] warm 行，return 前打印）
    per_code: dict[str, dict] = {}
    for i, code in enumerate(codes, 1):
        if i % 200 == 0:
            print(f"[warmup] {start}~{end} {i}/{len(codes)}", file=sys.stderr)
        df = bt._load_one_bars(code, count, start, end, warmup_bars=warmup_bars)
        if df is None or not len(df):
            continue
        gate_signals: dict[str, list] = {}
        scores: dict[str, float] = {}
        for g in gates:
            sigs: list[dict] = []
            bt.evaluate_trades(
                {code: df},
                scorer=bt.SCORERS["baseline"],
                entry_gate=bt.ENTRY_GATES[g],
                amv_regime=regime_bear,
                cost_bps=cost_bps,
                collect_all=True,
                signals_out=sigs,
            )
            if warmup_bars > 0:
                sigs = [s for s in sigs if str(s.get("date") or "")[:10] >= start]
            if sigs:
                gate_signals[g] = sigs
            for s in sigs:
                if s["date"] in scores:
                    continue
                try:
                    score, _level, _contrib = srs.asof_technical_score(
                        df, index_df, s["i"], code
                    )
                except Exception:  # noqa: BLE001 — 单信号评分失败丢该信号  # pylint: disable=broad-exception-caught
                    continue
                scores[s["date"]] = score
        per_code[code] = {"df": df, "gate_signals": gate_signals, "scores": scores}
    # 分子=任一门有信号的票（per_code 含 df 加载成功但 0 信号的票，母体口径）
    n_sig = sum(1 for p in per_code.values() if p["gate_signals"])
    print(  # TODO #92 新增行（既有 [warmup] 进度行逐字不动）
        f"[TIME] warm {start}~{end} {_time.time() - _t0:.0f}s"
        f"（{n_sig}/{len(codes)} 票有信号）",
        file=sys.stderr,
    )
    return per_code


def collect_gate_signals(per_code: dict[str, dict], gate: str) -> list[dict]:
    """单门信号清单：code/date/i/score/sig/gate（缺分丢弃）。"""
    out: list[dict] = []
    for code, pack in per_code.items():
        for s in pack["gate_signals"].get(gate, ()):
            score = pack["scores"].get(s["date"])
            if score is None:
                continue
            out.append(
                {
                    "code": code,
                    "date": s["date"],
                    "i": s["i"],
                    "score": score,
                    "sig": s,
                    "gate": gate,
                }
            )
    return out


def _gate_view(per_code: dict[str, dict], gate: str) -> dict[str, dict]:
    """replay_signals 适配视图：{code: {df, signals, scores}}。"""
    return {
        code: {
            "df": p["df"],
            "signals": p["gate_signals"].get(gate, []),
            "scores": p["scores"],
        }
        for code, p in per_code.items()
        if p["gate_signals"].get(gate)
    }


# ---------------------------------------------------------------------------
# 随机臂（对等纪律 v0.302：挖掘窗选型冻结，判定窗读数由冻结配置产生）
# ---------------------------------------------------------------------------


def bear_bar_pool(
    per_code: dict[str, dict], regime_bear: dict[str, str]
) -> list[tuple[str, int, str]]:
    """空头日 (code, i, date) 母体（=宇宙内**全部**空头日 bar，不是门信号集）。

    **每窗只建一次**（v0.309 性能：预算对等后每臂逐门抽 46 次 × 50 臂，
    母体从头重建 ~1.4s/次 ⇒ 纯重建成本 ~54min；预建注入后归零）。"""
    dates_sorted = sorted(regime_bear)
    pool: list[tuple[str, int, str]] = []
    for code, pack in per_code.items():
        df = pack["df"]
        dts = df["date"].astype(str).str[:10].tolist()
        for i, d in enumerate(dts):
            idx = bisect.bisect_right(dates_sorted, d) - 1
            if idx >= 0 and regime_bear[dates_sorted[idx]] == "做多":  # 真空头日
                pool.append((code, i, d))
    return pool


_V2_SEG_END = {
    "mining_a": lw.SEG_MINING_A[1],
    "judgment": lw.SEG_JUDGMENT[1],
    "mining_b": lw.SEG_MINING_B[1],
}

_V2_SEG_START = {
    "mining_a": lw.SEG_MINING_A[0],
    "judgment": lw.SEG_JUDGMENT[0],
    "mining_b": lw.SEG_MINING_B[0],
}


def _seg_bar_pool_clipped(
    per_code: dict[str, dict], regime_bear: dict[str, str], seg: str
) -> list[tuple[str, int, str]]:
    """v2 段空头日母体（滤到段末隔离带截止日）：随机臂零假设与门信号
    同纪律——候选信号三段末各一刀（``lw.clip_entries``，防 open_end 强制
    平仓偏差，owner review #4 更正口径），随机入场的母体按同一截止日滤。
    v0.359 段前预热后 df 带 start 前 bar：母体同步只收 ≥段起点
    （预热 bar 只作指标预热，不出随机入场——与门信号同口径）。"""
    dl = lw.entry_deadline(_V2_SEG_END[seg])
    lo = _V2_SEG_START[seg]
    return [t for t in bear_bar_pool(per_code, regime_bear) if lo <= t[2] <= dl]


def random_entries(
    per_code: dict[str, dict],
    regime_bear: dict[str, str],
    n: int,
    rng: random.Random,
    index_df: Any = None,
    *,
    pool: Optional[list[tuple[Any, ...]]] = None,
    score_cache: Optional[dict[Any, Optional[float]]] = None,
    shapes: Optional[list[list[int]]] = None,
) -> list[dict]:
    """空头日随机 (code, bar) 入场 n 个；**无放回抽样**（v0.308：``rng.sample``，
    母体不够就截断——有放回会让同一 (code, bar) 重复计入）；score 缺失时
    生产路径现算（注入路径由测试给）。

    v0.309 性能（结果逐位不变）：``pool`` 可预建注入（每窗一次，免逐臂
    重建）；``score_cache`` 按 (code, i) 跨臂跨门共用——抽样总量百万级时
    绝大部分命中，打分次数上限=母体大小；None（打分失败）同样缓存，
    跳过语义与未缓存逐位一致。

    v2 合并挖掘池（TODO #90 三段布局）：``pool`` 元素可为
    ``(seg, code, i, d)`` 4 元组——首元素是段名，此时 ``per_code`` 按
    **段键**（{seg: {code: pack}}）、缓存键带段前缀，产出记录带
    ``"_seg"`` 段溯源标签（合并池重放须按段拆回各自 df——同票 A/B 两段
    各有一份 df）；3 元组形态逐位不变。"""
    if pool is None:
        pool = bear_bar_pool(per_code, regime_bear)
    if not pool or (n <= 0 and not shapes):
        return []
    out: list[dict] = []
    # shapes 给出 ⇒ 成簇抽样（v0.399）；否则逐 bar 无放回（旧行为逐位不变）
    items = (
        clustered_draw(pool, shapes, rng)
        if shapes
        else rng.sample(pool, min(n, len(pool)))
    )
    for item in items:
        seg: Optional[str] = None
        if len(item) == 4:  # (seg, code, i, d)——v2 合并挖掘池段溯源形态
            seg, code, i, d = item
        else:
            code, i, d = item
        pack = per_code[code] if seg is None else per_code[seg][code]
        score = pack["scores"].get(d)
        if score is None and index_df is not None:
            key = (seg, code, i) if seg is not None else (code, i)
            if score_cache is not None and key in score_cache:
                score = score_cache[key]
            else:
                try:
                    from custos.research import score_return_study as srs  # noqa: PLC0415

                    score, _level, _contrib = srs.asof_technical_score(
                        pack["df"], index_df, i, code
                    )
                except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                    score = None
                if score_cache is not None:
                    score_cache[key] = score
        if score is None:
            continue
        rec = {
            "code": code,
            "date": d,
            "i": i,
            "score": score,
            "sig": {"code": code, "i": i, "date": d, "score": score},
            "gate": "__random__",
        }
        if seg is not None:
            rec["_seg"] = seg
        out.append(rec)
    return out


def cluster_shapes(sigs: list[dict]) -> list[list[int]]:
    """候选信号的簇形状（v0.399，owner 2026-10-16 拍板 3b）：簇划分 = ``kit.cluster_ids``
    单源（同票、bar 间隔 ≤5 的连续段；v2 簇键含段），每簇 = 相对首 bar 的偏移清单。"""
    by: dict[int, list[int]] = {}
    for r, cid in zip(sigs, kit.cluster_ids(sigs)):
        by.setdefault(cid, []).append(int(r["i"]))
    out = []
    for cid in sorted(by):
        ii = sorted(by[cid])
        out.append([x - ii[0] for x in ii])
    return out


def clustered_draw(
    pool: list[tuple[Any, ...]],
    shapes: list[list[int]],
    rng: random.Random,
    *,
    max_retry: int = 20,
) -> list[tuple[Any, ...]]:
    """成簇随机抽样（v0.399）：每个候选簇形状独立抽一个母体锚点，同票按偏移连续取，
    只收落在母体（空头日 ∩ 段内 ∩ 隔离带前）且未被占用的 bar——**整体无放回**。
    锚点重抽至该簇至少取到 1 个 bar（上限 max_retry 次，放不下的簇丢弃、由
    entry_accounting 的 requested/returned 如实记账）。

    ⚠️ 为什么：j_low 等门信号在空头期同票连续多日触发、成簇；逐 bar 独立抽样的随机臂
    不成簇 ⇒ 零假设方差被低估、q95 偏低、判定线偏松（R39/R42 v0.335 同病，owner 模拟
    簇长 20 ⇒ 假过线 38%）。"""
    if not pool or not shapes:
        return []
    key_of = (
        (lambda t: (t[0], t[1], t[2]))
        if len(pool[0]) == 4
        else (lambda t: (t[0], t[1]))
    )
    index = {key_of(t): t for t in pool}
    used: set = set()
    out: list[tuple[Any, ...]] = []
    for shape in shapes:
        # v0.409 审计 2 T4：整簇放得下才收（原「取到 1 根就收」⇒ 长簇被截短、臂 n 系统性偏少，
        # 违背预算对等）；max_retry 次都放不全 ⇒ 收找到的最长部分
        best: list = []
        for _ in range(max_retry):
            a = pool[rng.randrange(len(pool))]
            ka = key_of(a)
            got = []
            for off in shape:
                k = (*ka[:-1], ka[-1] + off)
                t = index.get(k)
                if t is not None and k not in used:
                    got.append((k, t))
            if len(got) > len(best):
                best = got
            if len(got) == len(shape):
                break
        for k, t in best:
            used.add(k)
            out.append(t)
    return out


def _entry_view(per_code_w: dict[str, dict], entries: list[dict]) -> dict[str, dict]:
    """随机入场集的重放视图（replay_signals 适配）：{code: {df, signals, scores}}。"""
    view: dict[str, dict] = {}
    for r in entries:
        code = r["code"]
        pack = per_code_w[code]
        v = view.setdefault(
            code, {"df": pack["df"], "signals": [], "scores": dict(pack["scores"])}
        )
        v["signals"].append(r["sig"])
        v["scores"][r["date"]] = r["score"]
    return view


def run_arm(
    gate_counts: dict[str, int],
    exits: list[dict],
    draw_entries: Callable[[int, str], list[dict]],
    replay_mining: Callable[[list[dict], dict[str, Any]], list[dict]],
    top_n: int,
    judgment_reader: Optional[Callable[[int, dict], Optional[dict]]] = None,
    judgment_counts: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    """单条随机臂：**预算对等**（v0.307）——完整复刻 top 的 max-of-230
    选型：逐门按该门实测信号数 n_g 抽随机入场（0 信号门跳过）× 全档重放，
    max 过全部门×档 ⇒ **冻结（n_g, 出场配置）**；判定窗读数由
    judgment_reader（冻结配置的重抽回放）产生——对等纪律（v0.302）的
    代码化身。原 max-of-5 构造的标尺系统性偏低（top 享受 230 格选择效应、
    臂只有 5 格），C4/C2 的 q95 会过易。

    v2.1：参照档 = **同一批入场信号** × pct5_trail08（同窗同信号同口径——
    随机臂与主研究同族对称）。
    """
    best: Optional[dict] = None
    for g, n_g in gate_counts.items():
        if n_g <= 0:
            continue  # 0 信号的门不能当模板（n=0 的臂无意义）
        entries = (
            draw_entries(n_g, "mining", g)  # type: ignore[call-arg]
            if getattr(draw_entries, "_gate_aware", False)
            else draw_entries(n_g, "mining")
        )
        ref_rd: Optional[dict] = None
        for e in exits:
            if e["name"] == _REF_NAME:
                ref_rd = _combine(
                    replay_mining(entries, e["params"]), top_n, ref="self"
                )
                break
        for e in exits:
            trades = replay_mining(entries, e["params"])
            rd = _combine(trades, top_n, ref=ref_rd)
            if rd and rd["objective"] is not None and rd["margin"] is not None:
                if (
                    best is None or rd["margin"] > best["readings"]["margin"]
                ):  # best is None 短路守卫（误报）  # pylint: disable=unsubscriptable-object
                    best = {
                        "n_signals": n_g,
                        "gate_template": g,
                        "exit": e["name"],
                        "params": e["params"],
                        "readings": rd,
                    }
    out: dict[str, Any] = {"mining": best}
    if best is not None and judgment_reader is not None:
        # 判定窗样本量 = 冻结门模板在**判定窗**的实际信号数（v0.386 审计 recent#1）：
        # 候选判定窗读数用该门判定窗真实信号，臂若沿用挖掘窗 n_g（挖掘熊段信号
        # 往往多数倍）⇒ 臂分布偏窄、q95_j 偏低、C2 判定半窗过松。只用计数不碰收益。
        n_j = (
            judgment_counts.get(best["gate_template"], 0)
            if judgment_counts is not None
            else best["n_signals"]
        )
        out["judgment_n"] = n_j
        out["judgment"] = (
            (
                judgment_reader(n_j, best["params"], best["gate_template"])  # type: ignore[call-arg]
                if getattr(judgment_reader, "_gate_aware", False)
                else judgment_reader(n_j, best["params"])
            )
            if n_j > 0
            else None
        )  # 冻结出场配置，不重选
    return out


# ---------------------------------------------------------------------------
# C3：出场参数 ±50% 扰动（吸附档位；无可扰参数原样留痕）
# ---------------------------------------------------------------------------


def _perturbable(params: dict[str, Any]) -> bool:
    """该出场参数集是否存在可扰参数轴（C3 适用性判定——base_low
    params={} 无 genome 语义主止损轴 ⇒ False）。"""
    return "stop_pct" in params and "stop_pct" in eg.LEVELS


def perturb_exit_params(
    params: dict[str, Any], rng: random.Random
) -> tuple[dict[str, Any], bool]:
    """LEVELS 内且开启的参数经 ``exit_genome.perturb_50``（±50% 吸附最近档）；
    档位外键原样保留。返回（新参数, 是否有可扰参数）——base_low（params={}）
    无 genome 语义主止损轴 ⇒ 原样重评留痕。"""
    if not _perturbable(params):
        return dict(params), False
    g = {k: float(v) for k, v in params.items() if k in eg.LEVELS}
    if "cost_zone_bars" in g and "cost_zone_pct" not in g:
        g["cost_zone_pct"] = 3.0  # 引擎默认值（evaluate_trades 形参默认）
    perturbed = eg.perturb_50(g, rng)
    out = dict(params)
    for k in params:
        if k in perturbed:
            out[k] = perturbed[k]
    return out, True


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------


def judge_c1(top_m: Optional[dict], top_j: Optional[dict]) -> dict[str, Any]:
    """C1：top 每窗 n_taken≥100——**不过 ⇒ untested 不判 falsified**。"""
    out: dict[str, Any] = {"min_n_taken": MIN_N_TAKEN}
    oks = []
    for wname, rd in (("mining", top_m), ("judgment", top_j)):
        n = (rd or {}).get("n_taken") or 0
        ok = bool(rd) and n >= MIN_N_TAKEN
        oks.append(ok)
        out[wname] = {"ok": ok, "n_taken": n}
    out["ok"] = all(oks)
    return out


def judge_c2(
    top_m: Optional[dict],
    top_j: Optional[dict],
    q95_m: Optional[float],
    q95_j: Optional[float],
) -> dict[str, Any]:
    """C2：margin 双窗同向为正 且 双窗 > 随机臂对应窗池 q95（池缺=未评）。"""
    m_m = (top_m or {}).get("margin")
    m_j = (top_j or {}).get("margin")
    pos_ok = m_m is not None and m_j is not None and m_m > 0 and m_j > 0
    pool_ok_m = None if q95_m is None or m_m is None else m_m > q95_m
    pool_ok_j = None if q95_j is None or m_j is None else m_j > q95_j
    pools_available = q95_m is not None and q95_j is not None
    ok: Optional[bool]
    if not pos_ok:
        ok = False
    elif not pools_available:
        ok = None  # 池未建 = 量级比较未评（ provisional 通道，不是 falsified）
    else:
        ok = bool(pool_ok_m and pool_ok_j)
    return {
        "ok": ok,
        "pos_ok": pos_ok,
        "pool_ok_mining": pool_ok_m,
        "pool_ok_judgment": pool_ok_j,
        "pools_available": pools_available,
        "margin_mining": m_m,
        "margin_judgment": m_j,
        "q95_mining": q95_m,
        "q95_judgment": q95_j,
        "rule": "margin 双窗同向为正 且 双窗>随机臂对应窗池 q95（基准=随机臂非空仓）",
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _resolve_gates(args: Any) -> list[str]:
    """门清单解析：``all``=全量 ENTRY_GATES 字母序（46 门），否则逗号清单。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    return (
        sorted(bt.ENTRY_GATES)
        if args.gates == "all"
        else [g.strip() for g in args.gates.split(",") if g.strip()]
    )


def _resolve_warm(
    args: Any,
    gates: list[str],
    warm_fn: Optional[Callable[[str, str], dict[str, dict]]],
    regime_since: str,
) -> tuple[dict[str, str], Any, Callable[[str, str], dict[str, dict]]]:
    """生产路径装配：宇宙解析 + 0AMV 反转 + 指数表 + warm 闭包；注入路径
    原样透传（regime 空、index_df=None）。

    ``regime_since`` = 最早段起点（legacy=挖掘窗起点；v2=挖掘 A 起点——
    0AMV 对挖掘 A 的数据缺口**不做任何特殊处理**，无映射日按既有语义
    不放行，TODO #90 定案由基线重测定夺）。"""
    regime_bear: dict[str, str] = {}
    index_df: Any = None
    if warm_fn is None:
        from custos.research import backtest_factors as bt  # noqa: PLC0415

        ns = argparse.Namespace(
            codes_file=args.codes_file,
            universe_local=args.universe_local,
            universe_sample=args.universe_sample,
            seed=args.universe_seed,
            codes=args.codes,
        )
        codes = bt._resolve_universe(ns, _build_parser())
        args.universe_meta = (
            ns.universe_meta  # pylint: disable=no-member  # _resolve_universe 副作用塞入（同 exit_campaign）
        )  # 回塞 CLI args：provenance 宇宙三件套（v0.356 同族，TODO #89）
        regime = bt.load_amv_regime(since=regime_since)
        if not regime:
            raise RuntimeError("0AMV regime 读不到（compass_amv）——本机无数据？")
        regime_bear = invert_regime_bearish(regime)
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415
        from custos.research import score_return_study as srs  # noqa: PLC0415

        index_df = (
            local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
            .sort_values("date")
            .reset_index(drop=True)
        )

        def warm_fn(start: str, end: str) -> dict[str, dict]:  # noqa: F811
            _sw, _ms, _is_v2 = _resolve_layout(args)
            return warm_gates(
                codes,
                regime_bear,
                index_df,
                gates=gates,
                count=args.count,
                cost_bps=args.cost_bps,
                start=start,
                end=end,
                # v2 段前预热 300 根（v0.359，owner review #3）；legacy 0 根
                warmup_bars=300 if _is_v2 else 0,
            )

    return regime_bear, index_df, warm_fn


def _enumerate_grid(
    gates: list[str],
    windows: dict[str, tuple[str, str]],
    exits: list[dict],
    gate_sigs: dict[str, dict[str, list[dict]]],
    replay: Callable[[str, str, list[dict], dict[str, Any]], list[dict]],
    top_n: int,
) -> tuple[list[dict], dict[tuple[str, str], Optional[dict]], dict[str, Any]]:
    """46 门 × 5 档 × 双窗全枚举：参照档 pct5_trail08 每（门,窗）先算存 refs
    （自参照），其余档重放缓存 per（门, 档, 窗）。"""
    configs: list[dict] = []
    ref_trail = next(e["params"] for e in exits if e["name"] == _REF_NAME)
    refs: dict[tuple[str, str], Optional[dict]] = {}
    for g in gates:
        for w in windows:
            trades = replay(w, g, gate_sigs[w][g], ref_trail)
            refs[(g, w)] = _combine(trades, top_n, ref="self")
        for e in exits:
            row: dict[str, Any] = {"gate": g, "exit": e["name"]}
            for w in windows:
                if e["name"] == _REF_NAME:
                    rd = refs[(g, w)]  # 参照档本体=自参照读数（复用不重复重放）
                else:
                    trades = replay(w, g, gate_sigs[w][g], e["params"])
                    rd = _combine(trades, top_n, ref=refs[(g, w)])
                if rd is not None:
                    rd.pop("taken", None)  # 报告不落交易明细
                row[w] = rd
            configs.append(row)
    return configs, refs, ref_trail


def _run_random_arms(
    args: Any,
    gates: list[str],
    per_code: dict[str, dict[str, dict]],
    mining_segs: list[str],
    gate_sigs: dict[str, dict[str, list[dict]]],
    regime_bear: dict[str, str],
    index_df: Any,
    exits: list[dict],
    ref_trail: dict[str, Any],
    replay: Callable[[str, str, list[dict], dict[str, Any]], list[dict]],
    random_entry_fn: Optional[Callable[[int, str], list[dict]]],
) -> dict[str, Any]:
    """随机臂双窗池：每臂完整复刻 top 的 46 门 × 5 档 max-of-230 选型（逐门
    n_g 对齐抽样，0 信号门跳过），冻结（n_g, 出场配置）进判定窗。

    ``per_code`` 按**段**键（legacy=mining/judgment 两段，v2=mining_a/
    judgment/mining_b 三段）；v2 挖掘池 = A∪B 合并母体（pool 元素带段
    标签 ``(seg, code, i, d)``，random_entries 段溯源随记录走）。报告侧
    记账/缓存恒按**逻辑窗**（mining/judgment）键，形态不随布局变。"""
    rng = random.Random(args.seed)
    counts_m = {g: len(gate_sigs["mining"][g]) for g in gates}
    counts_j = {g: len(gate_sigs["judgment"][g]) for g in gates}  # 臂判定窗 n（v0.386）
    pool_j: list[float] = []
    index_df_ref: dict[str, Any] = {"df": index_df}  # 生产路径才有

    # v0.309 性能（结果逐位不变）：母体每窗惰性预建一次（random_entry_fn
    # 注入路径不建——合成 per_code 无 df）；as-of 打分 (code,i) 缓存双窗
    # 各一、跨臂跨门共用（打分次数上限=母体大小）。
    bar_pools: dict[str, list] = {}
    bar_pool_pc: dict[str, dict] = {}
    score_caches: dict[str, dict] = {w: {} for w in ("mining", "judgment")}
    entry_accounting: dict[str, dict[str, int]] = {
        w: {"requested": 0, "returned": 0} for w in ("mining", "judgment")
    }

    def _pool_of(w: str) -> tuple[list, dict]:
        """逻辑窗 → 空头日母体 + random_entries 的 per_code 形态（单段 =
        code 键 3 元组；v2 合并挖掘池 = 段键 per_code + 段标签 4 元组）。
        v2 母体滤到段末隔离带截止日（``_seg_bar_pool_clipped``——随机臂
        与门信号同纪律）；legacy 不滤（R40 兼容逐位不变）。"""
        segs = mining_segs if w == "mining" else ["judgment"]
        if len(segs) == 1:
            pc = per_code[segs[0]]
            if set(per_code) == {"mining_a", "judgment", "mining_b"}:
                return _seg_bar_pool_clipped(pc, regime_bear, segs[0]), pc
            return bear_bar_pool(pc, regime_bear), pc
        pool: list = []
        for seg in segs:
            pool.extend(
                (seg, code, i, d)
                for code, i, d in _seg_bar_pool_clipped(per_code[seg], regime_bear, seg)
            )
        return pool, per_code

    # 候选簇形状（v0.399）：每（窗, 门）一次，随机臂按同簇结构抽（成簇零假设）
    shape_cache: dict[tuple[str, str], list[list[int]]] = {}

    def _shapes(w: str, g: Optional[str]) -> Optional[list[list[int]]]:
        if g is None:
            return None
        if (w, g) not in shape_cache:
            shape_cache[(w, g)] = cluster_shapes(gate_sigs[w][g])
        return shape_cache[(w, g)]

    def _rand_entries(n: int, w: str, g: Optional[str] = None) -> list[dict]:
        entry_accounting[w]["requested"] += n
        if random_entry_fn is not None:
            out = random_entry_fn(n, w)
        else:
            if w not in bar_pools:
                bar_pools[w], bar_pool_pc[w] = _pool_of(w)
            out = random_entries(
                bar_pool_pc[w],
                regime_bear,
                n,
                rng,
                index_df=index_df_ref["df"],
                pool=bar_pools[w],
                score_cache=score_caches[w],
                shapes=_shapes(w, g),
            )
        entry_accounting[w]["returned"] += len(out)
        return out

    _rand_entries._gate_aware = True  # type: ignore[attr-defined]

    def _arm(_i: int) -> Optional[float]:
        def _judgment_reader(
            n_g: int, frozen_params: dict[str, Any], gate: Optional[str] = None
        ) -> Optional[dict]:
            """冻结配置（含胜出格 n_g）的判定窗读数（不重选——对等纪律 v0.302）。

            ⚠️ 冻结的是**出场配置与胜出格 n_g**；判定窗入场集 = 同分布重抽
            （随机入场无门信号可携带——对等的是「臂冻结配置 vs top 冻结配置」，
            入场随机性两臂同分布）。v2.1 参照 = 同批判定入场 × trail08。"""
            entries_j = _rand_entries(n_g, "judgment", gate)  # 冻结门判定窗簇形状
            ref_j = _combine(
                replay("judgment", "__random__", entries_j, ref_trail),
                args.top_n,
                ref="self",
            )
            trades = replay("judgment", "__random__", entries_j, frozen_params)
            return _combine(trades, args.top_n, ref=ref_j)

        _judgment_reader._gate_aware = True  # type: ignore[attr-defined]
        arm = run_arm(
            counts_m,
            exits,
            _rand_entries,
            lambda subset, params: replay("mining", "__random__", subset, params),
            args.top_n,
            judgment_reader=_judgment_reader,
            judgment_counts=counts_j,
        )
        if arm["mining"] is None:
            return None
        jd = arm.get("judgment")
        if jd and jd.get("margin") is not None:
            pool_j.append(jd["margin"])  # 侧信道：判定窗池（与主池同臂）
        return arm["mining"]["readings"]["margin"]

    c4_pool = kit.assemble_c4_pool(args.n_random, _arm)
    pool_m = c4_pool["pool"]
    # 判定半窗判定线（v0.392，owner 选 B）：入批读数（带 --batch-id）⇒ 冻结臂判定窗
    # 重读池的攒批判定线 q(1−0.05/m)，m 只从名册读；否则（legacy / 合成）q95。
    batch_id = str(getattr(args, "batch_id", "") or "")
    if batch_id:
        from custos.research import judgment_batch as jb  # noqa: PLC0415

        m_roster = jb.batch_m(batch_id)
        batch_m: Optional[int] = m_roster
        q95_j = kit.batch_threshold(pool_j, m_roster)
    else:
        batch_m = None
        q95_j = kit.q95(pool_j)
    return {
        "rng": rng,
        "pool_m": pool_m,
        "pool_j": pool_j,
        "q95_m": kit.q95(pool_m),
        "q95_j": q95_j,
        "batch_m": batch_m,
        "evaluated": c4_pool["evaluated"],
        "gate_pass": c4_pool["gate_pass"],
        "entry_accounting": entry_accounting,
        "score_caches": score_caches,
    }


def _judge_c3(
    top: Optional[dict],
    top_params: Optional[dict],
    gate_sigs: dict[str, dict[str, list[dict]]],
    replay: Callable[[str, str, list[dict], dict[str, Any]], list[dict]],
    refs: dict[tuple[str, str], Optional[dict]],
    top_n: int,
    q95_m: Optional[float],
    q95_j: Optional[float],
    rng: random.Random,
) -> dict[str, Any]:
    """C3：top 出场参数 ±50%×4 零翻转（池固定不重估）；top 出场无可扰
    参数轴（base_low params={}）⇒ not_applicable 不空转（v0.307）。"""
    c3_rule = f"出场参数 ±50% ×{C3_DRAWS} 扰动 C2 结论零翻转（池固定）"
    c3_draws: list[dict] = []
    c3: dict[str, Any]
    if top is not None and not _perturbable(top_params or {}):
        c3 = {
            "ok": None,  # 无灵敏度证据：不降 falsified 也不放行 candidate
            "state": "not_applicable",
            "reason": f"top 出场 {top['exit']} 无可扰参数轴（params="
            f"{top_params}）——C3 不适用：不空转零信息复评、不伪装零翻转放行",
            "draws": [],
            "n_flips": None,
            "rule": c3_rule,
        }
    else:
        if top is not None:
            for draw_i in range(C3_DRAWS):
                params_p, moved = perturb_exit_params(dict(top_params or {}), rng)
                rd_m = _combine(
                    replay(
                        "mining",
                        top["gate"],
                        gate_sigs["mining"][top["gate"]],
                        params_p,
                    ),
                    top_n,
                    ref=refs[(top["gate"], "mining")],  # v2.1 同参照口径对称
                )
                rd_j = _combine(
                    replay(
                        "judgment",
                        top["gate"],
                        gate_sigs["judgment"][top["gate"]],
                        params_p,
                    ),
                    top_n,
                    ref=refs[(top["gate"], "judgment")],
                )
                c2d = judge_c2(rd_m, rd_j, q95_m, q95_j)
                c3_draws.append(
                    {
                        "draw": draw_i,
                        "params_perturbed": params_p,
                        "perturbable": moved,
                        "c2_holds": c2d["ok"],
                    }
                )
        c3 = {
            "ok": bool(c3_draws) and all(d["c2_holds"] for d in c3_draws),
            "draws": c3_draws,
            "n_flips": sum(1 for d in c3_draws if not d["c2_holds"]),
            "rule": c3_rule,
        }
    return c3


def _judge_c4(
    args: Any,
    pool_m: list[float],
    top: Optional[dict],
    q95_m: Optional[float],
    arm_evaluated: int,
    arm_gate_pass: int,
) -> tuple[str, dict[str, Any]]:
    """C4 状态机 = criteria_kit 单源（v0.322：空池 indeterminate / 池未满
    provisional / top 挖掘窗 margin > q95 confirmed_pass）。"""
    c4_state = kit.c4_state_of(
        pool_m,
        min_pool=args.c4_min_pool,
        plan_delta=(top["mining"]["margin"] if top else None),
    )
    c4 = {
        "state": c4_state,
        "pool_size": len(pool_m),
        "evaluated": arm_evaluated,
        "gate_pass": arm_gate_pass,
        "gate_pass_rate": (arm_gate_pass / arm_evaluated if arm_evaluated else None),
        "q95": q95_m,
        "min_pool": args.c4_min_pool,
        "note": "与 C2 挖掘半窗同池（重合是设计使然——R40 量级线与选择效应"
        "对的是同一个零假设）",
    }
    return c4_state, c4


def _top_extended(
    trades_by_w: dict[str, list[dict]], top_n: int, with_dist: bool
) -> dict[str, Any]:
    """top 交易集逐窗必报扩展（R45 预注册「必报」v0.422 补齐；R40 需 --trade-dist）：
    平均每笔收益 / 出场原因占比 / 平均持有（trade_dist 画像）+ 组合层 CAGR·最大回撤·收益回撤比
    （同判据组合口径）+ 不可卖笔数（``*_unfillable``：连续跌停 / 停牌持到数据末；逐次顺延
    天数引擎不记录，如实缺）。描述性读数，不进判据。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import trade_dist as td  # noqa: PLC0415
    from custos.research.exit_campaign import V0_PORTFOLIO  # noqa: PLC0415

    out: dict[str, Any] = {}
    for w, tr in trades_by_w.items():
        pf = bt.simulate_portfolio_topn(tr, top_n=top_n, **(_PORTFOLIO or V0_PORTFOLIO))
        d = td.describe(tr)
        if not with_dist:
            d = {
                k: d.get(k)
                for k in (
                    "n",
                    "win_rate",
                    "payoff_ratio",
                    "expectancy",
                    "avg_win",
                    "avg_loss",
                    "holding",
                    "by_reason",
                )
            }
        out[w] = {
            "dist": d,
            "portfolio": {
                k: pf.get(k)
                for k in (
                    "n_taken",
                    "total_return",
                    "cagr",
                    "max_drawdown",
                    "return_over_maxdd",
                )
            },
            "n_unfillable": sum(
                1 for t in tr if str(t.get("reason", "")).endswith("_unfillable")
            ),
        }
    return out


def _cost_side(
    top: Optional[dict],
    top_params: Optional[dict],
    gate_sigs: dict[str, dict[str, list[dict]]],
    windows: dict[str, tuple[str, str]],
    replay: Callable[[str, str, list[dict], dict[str, Any]], list[dict]],
    cost_bps: float,
    top_trades: Optional[dict[str, list[dict]]] = None,
) -> Optional[dict]:
    """成本副读数（owner review #6）：top 交易集双窗定向重建（replay 同
    路径），50bps 解析双报——R40 C2 是**绝对口径**（margin>0），本副读数
    是主战场：空头期做多 margin 薄，翻号 ⇒ 判读降权。"""
    if top is None or top_params is None:
        return None
    from custos.research.cost_sensitivity import cost_side_block  # noqa: PLC0415

    return cost_side_block(
        top_trades
        if top_trades is not None
        else {
            w: replay(w, top["gate"], gate_sigs[w][top["gate"]], top_params)
            for w in windows
        },
        base_bps=cost_bps,
        deltas={f"top_margin_vs_0_{w}": (w, None) for w in windows},
    )


def _build_report(
    args: Any,
    windows: dict[str, tuple[str, str]],
    gates: list[str],
    exits: list[dict],
    per_code: dict[str, dict[str, dict]],
    gate_sigs: dict[str, dict[str, list[dict]]],
    top: Optional[dict],
    configs: list[dict],
    verdict: str,
    cost_sens: Optional[dict],
    arms: dict[str, Any],
    criteria: dict[str, Any],
    injected: bool,
    segments: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """报告装配：判定窗使用台账（v2 口径，TODO #90——**只有判定窗=受保护段
    2017~2021 才登记** ``judgment_2017_2021``，本报告=该窗第 k 次被读；
    legacy 判定窗已并入挖掘 B，读挖掘段不入台账，照 exit_campaign 条件
    登记语义接线）+ provenance + 自含产物块。"""
    _is_protected_judgment = lw.touches_judgment(*windows["judgment"])
    _wu_k = (
        wu.record_use(
            "R40",
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
        from custos.research import judgment_batch as jb  # noqa: PLC0415

        jb.mark_read(
            args.batch_id, args.candidate_id, tool="bear_regime_study", tag=args.tag
        )
    entry_accounting = arms["entry_accounting"]
    from custos.research import provenance as pv  # noqa: PLC0415
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    return {
        "schema": "bear_regime_report/v1",
        "tag": args.tag,
        "verdict": verdict,
        "window_usage": {
            "window": (
                "judgment_2017_2021" if _is_protected_judgment else "mining_segment"
            ),
            "k": _wu_k,
            "note": (
                wu.usage_note("R40", "judgment_2017_2021", _wu_k)
                if _is_protected_judgment
                else "判定窗在挖掘段内（非受保护段 2017~2021）——读挖掘段不入台账（v2 口径）"
            ),
        },
        "provenance": pv.build(
            args,
            unit="R40",
            criteria_version="v0.301/v0.302/v0.307",
            pre_reg_doc=RESEARCH_DIR / "R40_bear_regime_long_strategy.md",
            data_last_date=max(
                filter(None, (pv.last_date_of(pc) for pc in per_code.values())),
                default=None,
            ),
        ),
        "gates": gates,
        "exit_grid": [e["name"] for e in exits],
        "cost_sensitivity": cost_sens,
        "forward_holdout_note": lw.EXIT_BARS_HOLDOUT_NOTE,
        "windows": {w: {"start": se[0], "end": se[1]} for w, se in windows.items()},
        **({"segments": segments} if segments is not None else {}),
        "amv_mode": "bearish（invert_regime_bearish：仅空头日放行，无映射不放行）",
        "n_signals_by_gate": {
            w: {g: len(gate_sigs[w][g]) for g in gates} for w in windows
        },
        "top": top,
        "configs": configs,
        "pools": {
            "mining": arms["pool_m"],
            "judgment": arms["pool_j"],
            "q95_mining": arms["q95_m"],
            "q95_judgment": arms["q95_j"],
            # 有值 ⇒ q95_judgment 实为攒批判定线 q(1−0.05/m)（v0.392）
            **({"batch_m": arms["batch_m"]} if arms.get("batch_m") else {}),
            "entry_accounting": entry_accounting,  # n_requested/n_returned（打分失败跳过照实记）
            "score_cache_size": {w: len(c) for w, c in arms["score_caches"].items()},
            "arm_construction": "对等纪律（v0.302）+ 预算对等（v0.307）："
            "每臂完整复刻 top 的 46 门 × 5 档 max-of-230 选型（逐门 n_g "
            "对齐抽样，0 信号门跳过），冻结出场配置进判定窗（判定窗抽样量 = 冻结门"
            "判定窗实际信号数，v0.386），禁每窗重新取 max",
        },
        "criteria": criteria,
        "objective_version": sg.OBJECTIVE_VERSION,
        "notes": [_R11_R14_NOTE],
    }


def run_study(
    args: Any,
    warm_fn: Optional[Callable[[str, str], dict[str, dict]]] = None,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
    random_entry_fn: Optional[Callable[[int, str], list[dict]]] = None,
) -> dict[str, Any]:
    """R40 主流程：双逻辑窗 46 门×5 档枚举 + 随机臂冻结池 + C1~C4 → 报告。

    布局（``_resolve_layout``，TODO #90）：四参全 None ⇒ v2 三段（挖掘 A/
    判定/挖掘 B——挖掘逐段 warm/collect 各一次，三段末各 ``clip_entries``
    一刀（段末 90 自然日隔离带，在 replay/判据**之前**），A∪B 信号合并
    一池算挖掘读数）；全显式 ⇒ legacy 单段双窗（逐位同 v2 前行为，唯
    window_usage 按 v2 口径不入台账）。

    ``warm_fn(start, end) → per_code`` / ``replay_fn(subset, params)`` /
    ``random_entry_fn(n, window)`` 可注入（测试合成）；None = 生产路径。
    """
    # 合成守卫在入口快照：下方 _resolve_warm 生产分支会把 warm_fn 重绑成
    # 真实闭包（noqa: F811），事后再判恒 True ⇒ 生产跑被误判合成、判定窗
    # 读取不入台账（r42_probe 事故根因，v0.338 排查的实锤答案）。
    injected = (
        warm_fn is not None or replay_fn is not None or random_entry_fn is not None
    )
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    seg_windows, mining_segs, is_v2 = _resolve_layout(args)
    # 逻辑窗（判据/报告形态不变）：mining=v2 时=A∪B 合并池（区间只作展示，
    # 真值在 segments 块），judgment 恒单段
    windows = {
        "mining": (seg_windows[mining_segs[0]][0], seg_windows[mining_segs[-1]][1]),
        "judgment": seg_windows["judgment"],
    }
    # 判定段批次锁（v0.374，TODO #95）：数据加载（_resolve_warm）之前硬门——
    # 合成运行/legacy 窗不查；v2 缺省窗生产方向必须带合法批次
    from custos.research import judgment_batch as jb  # noqa: PLC0415

    jb.gate_judgment_read(
        args,
        _build_parser(),
        synthetic=injected,
        judgment_span=windows["judgment"],
        tool="bear_regime_study",
    )
    r45 = bool(getattr(args, "r45", False))
    gates = ["j_low"] if r45 else _resolve_gates(args)
    exits = R45_EXIT_GRID if r45 else sg.DEFAULT_EXIT_GRID
    global _REF_NAME, _PORTFOLIO
    _prev = (_REF_NAME, _PORTFOLIO)
    if r45:
        _REF_NAME, _PORTFOLIO = R45_REF, R45_PORTFOLIO
    try:
        return _run_study_body(
            args,
            warm_fn,
            replay_fn,
            random_entry_fn,
            injected,
            sg,
            seg_windows,
            mining_segs,
            is_v2,
            windows,
            gates,
            exits,
            r45,
        )
    finally:
        _REF_NAME, _PORTFOLIO = _prev


def _run_study_body(
    args: Any,
    warm_fn: Any,
    replay_fn: Any,
    random_entry_fn: Any,
    injected: bool,
    sg: Any,
    seg_windows: dict[str, tuple[str, str]],
    mining_segs: list[str],
    is_v2: bool,
    windows: dict[str, tuple[str, str]],
    gates: list[str],
    exits: list[dict],
    r45: bool,
) -> dict[str, Any]:
    """run_study 主体（v0.397 拆出：R45 模式的模块级口径在外层 try/finally 复位）。"""
    regime_bear, index_df, warm_fn = _resolve_warm(
        args, gates, warm_fn, regime_since=min(se[0] for se in seg_windows.values())
    )

    per_code = {seg: warm_fn(*se) for seg, se in seg_windows.items()}
    gate_sigs_seg: dict[str, dict[str, list[dict]]] = {}
    segments: dict[str, Any] = {}
    for seg, se in seg_windows.items():
        collected = {g: collect_gate_signals(per_code[seg], g) for g in gates}
        if not is_v2:
            gate_sigs_seg[seg] = collected  # legacy 单段：无隔离带无标签，逐位不变
            continue
        # v2：段末隔离带一刀（entry_deadline=段末−90 自然日，load_window
        # 单源）+ ``_seg`` 段溯源标签（合并池重放按段拆回各自 df——同票
        # A/B 两段各有一份 df，跨段混放会用错价格序列）
        clipped: dict[str, list[dict]] = {}
        n_kept = n_clipped = 0
        for g in gates:
            kept = lw.clip_entries(collected[g], se[1], key="date")
            for r in kept:
                r["_seg"] = seg
            clipped[g] = kept
            n_kept += len(kept)
            n_clipped += len(collected[g]) - len(kept)
        gate_sigs_seg[seg] = clipped
        segments[seg] = {
            "start": se[0],
            "end": se[1],
            "entry_deadline": lw.entry_deadline(se[1]),
            "n_signals": n_kept,  # 逐门合计（与 n_signals_by_gate 同口径）
            # 段内空头日数（台账反转后 = "做多"；R45 必报，v0.422）
            "n_bear_days": sum(
                1
                for d, v in regime_bear.items()
                if se[0] <= str(d)[:10] <= se[1] and v == "做多"
            ),
            "n_clipped": n_clipped,
        }
    gate_sigs: dict[str, dict[str, list[dict]]] = {
        "mining": {
            g: [r for seg in mining_segs for r in gate_sigs_seg[seg][g]] for g in gates
        },
        "judgment": gate_sigs_seg["judgment"],
    }
    if is_v2:
        segments["mining_merged_n_signals"] = sum(
            len(gate_sigs["mining"][g]) for g in gates
        )
    total_signals = sum(len(v) for w in gate_sigs.values() for v in w.values())
    if not total_signals:
        raise RuntimeError(
            "空结果护栏：46 门双窗 0 信号（宇宙/窗口/数据有问题？）——不落盘"
        )

    def _replay(w: str, g: str, subset: list[dict], params: dict[str, Any]):
        if replay_fn is not None:
            return replay_fn(subset, params)
        anchor_x = params.get("_anchor_x")
        if anchor_x is not None:  # R45：信号日最低价锚定止损（逐信号 stop_override）
            params = {k: v for k, v in params.items() if k != "_anchor_x"}
        segs = mining_segs if w == "mining" else ["judgment"]
        if len(segs) == 1:
            seg = segs[0]
            if g == "__random__":
                view = _entry_view(per_code[seg], subset)  # 随机入场集专用视图
            else:
                view = _gate_view(per_code[seg], g)
            sub = (
                _anchor_subset(view, subset, anchor_x)
                if anchor_x is not None
                else subset
            )
            return fes.replay_signals(view, sub, params, regime_bear, args.cost_bps)
        # v2 合并挖掘池：按 ``_seg`` 拆回各段 df 重放后合并交易集
        trades: list[dict] = []
        for seg in segs:
            seg_subset = [r for r in subset if r.get("_seg") == seg]
            if not seg_subset:
                continue
            if g == "__random__":
                view = _entry_view(per_code[seg], seg_subset)  # 随机入场集专用视图
            else:
                view = _gate_view(per_code[seg], g)
            if anchor_x is not None:
                seg_subset = _anchor_subset(view, seg_subset, anchor_x)
            trades.extend(
                fes.replay_signals(view, seg_subset, params, regime_bear, args.cost_bps)
            )
        return trades

    # ── 230 格 × 双窗（重放缓存 per（门, 档, 窗）；参照档 = 同窗同信号
    # pct5_trail08——v2.1 事先固定不经过挑选的档，每（门,窗）先算并存 refs）──
    configs, refs, ref_trail = _enumerate_grid(
        gates, windows, exits, gate_sigs, _replay, args.top_n
    )

    def _top(rows: list[dict]) -> Optional[dict]:
        ok = [c for c in rows if c["mining"] and c["mining"]["objective"] is not None]
        return max(ok, key=lambda c: c["mining"]["margin"]) if ok else None

    top = _top(configs)

    # ── 随机臂（对等纪律 v0.302 + **预算对等 v0.307**：每臂完整复刻 top 的
    # 46 门 × 5 档 max-of-230 选型——逐门 n_g 对齐抽样，冻结配置进判定窗；
    # v0.322：池构造=**criteria_kit 重抽单源**（v0.317 族））──
    arms = _run_random_arms(
        args,
        gates,
        per_code,
        mining_segs,
        gate_sigs,
        regime_bear,
        index_df,
        exits,
        ref_trail,
        _replay,
        random_entry_fn,
    )
    rng = arms["rng"]
    pool_m = arms["pool_m"]
    q95_m = arms["q95_m"]
    q95_j = arms["q95_j"]

    c1 = judge_c1(top["mining"] if top else None, top["judgment"] if top else None)
    c2 = judge_c2(
        top["mining"] if top else None,
        top["judgment"] if top else None,
        q95_m,
        q95_j,
    )

    if r45:
        c2.setdefault("beats_no_trade", None)  # 报告字段稳定（C2 先不过时也在）
    if r45 and c2.get("ok") is not False:
        # R45-C2：打过「不交易」——两窗组合总收益 > 0（v0.409 审计 2 P1-3：直接读 total_return；
        # 原用 rdd>0 代理，零回撤时 rdd=None ⇒ 正收益零回撤的最好结局被判死）
        rets = [
            ((top or {}).get(w) or {}).get("total_return")
            for w in ("mining", "judgment")
        ]
        beats = all(v is not None and v > 0 for v in rets)
        c2["beats_no_trade"] = beats
        if not beats:
            c2["ok"] = False
    top_params = next(
        (e["params"] for e in exits if top and e["name"] == top["exit"]), None
    )
    # ── C3：top 出场参数 ±50%×4 零翻转（池固定不重估）；top 出场无可扰
    # 参数轴（base_low params={}）⇒ not_applicable 不空转（v0.307）──
    c3 = (
        judge_c3_r45(configs, top)
        if r45
        else _judge_c3(
            top,
            top_params,
            gate_sigs,
            _replay,
            refs,
            args.top_n,
            q95_m,
            q95_j,
            rng,
        )
    )
    # C4 状态机 = criteria_kit 单源（v0.322：空池 indeterminate / 池未满
    # provisional / top 挖掘窗 margin > q95 confirmed_pass）
    c4_state, c4 = _judge_c4(
        args, pool_m, top, q95_m, arms["evaluated"], arms["gate_pass"]
    )

    # ── 总结局（criteria_kit 单源：C1 不过 ⇒ untested 优先；C2 池未建=None /
    # C3 not_applicable（v0.307）⇒ provisional 不放行不判死）──
    verdict = kit.verdict_four_state(
        c1_ok=c1["ok"], c2_ok=c2["ok"], c3_ok=c3["ok"], c4_state=c4_state
    )

    # ── 成本副读数（owner review #6）：top 交易集双窗定向重建（_replay 同
    # 路径），50bps 解析双报——R40 C2 是**绝对口径**（margin>0），本副读数
    # 是主战场：空头期做多 margin 薄，翻号 ⇒ 判读降权 ──
    top_trades = (
        {
            w: _replay(w, top["gate"], gate_sigs[w][top["gate"]], top_params)
            for w in windows
        }
        if top is not None and top_params is not None
        else None
    )
    cost_sens = _cost_side(
        top, top_params, gate_sigs, windows, _replay, args.cost_bps, top_trades
    )
    want_dist = bool(getattr(args, "trade_dist", False))
    if top is not None and top_trades is not None and (r45 or want_dist):
        top["extended"] = _top_extended(top_trades, args.top_n, with_dist=want_dist)

    criteria = {
        "C1": c1,
        "C2": c2,
        "C3": c3,
        "C4": c4,
        **(
            {
                "r45": {
                    "unit": "R45",
                    "exit_grid": [e["name"] for e in exits],
                    "ref_cell": R45_REF,
                    "portfolio": R45_PORTFOLIO,
                    "note": "R45 模式（v0.397）：j_low 单门 × 4 止损（信号日低点锚定）× 4 止盈；"
                    "C2 另须两窗组合收益>0（打过不交易）；C3=止损邻档平滑+最优止盈一致",
                }
            }
            if r45
            else {}
        ),
        "rule_note": "R40-C1~C4 跑数前写死（v0.301/v0.302/v0.307 修订在案——"
        "v0.307：随机臂预算对等 max-of-230 镜像；C3 无可扰参数轴="
        "not_applicable 不空转不放行）；"
        "C5（pre2019 同段随机池标尺）= C2 过线才启动，owner 拍板发令",
    }
    return _build_report(
        args,
        windows,
        gates,
        exits,
        per_code,
        gate_sigs,
        top,
        configs,
        verdict,
        cost_sens,
        arms,
        criteria,
        injected,
        segments=segments if is_v2 else None,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R40 0AMV 空头区间做多全栈研究终端（46 门×5 档 230 格；判据 C1~C4 跑数前写死）"
    )
    ap.add_argument("--tag", required=True, help="研究标签（产物目录名）")
    ap.add_argument("--codes", default="", help="逗号分隔代码（宇宙解析兜底）")
    ap.add_argument("--codes-file", default="", help="钉死宇宙 codes 表（优先）")
    ap.add_argument("--universe-sample", type=int, default=0, help="全市场抽样 N 只")
    ap.add_argument(
        "--universe-local", action="store_true", help="本地 vipdoc 清单抽样"
    )
    ap.add_argument("--universe-seed", type=int, default=42, help="宇宙抽样种子")
    ap.add_argument("--top-n", type=int, default=20, help="横截面择优（默认 20）")
    ap.add_argument(
        "--count",
        type=int,
        default=None,
        help="每股回溯 K 线根数（缺省=按最早段起点自动推算——v2=挖掘 A 起点，显式值覆盖）",
    )
    ap.add_argument("--cost-bps", type=float, default=25.0, help="往返成本基点")
    ap.add_argument(
        "--mining-start",
        default=None,
        help="挖掘窗起点（四参全不给=v2 三段布局：挖掘 A 2000~2016 / 判定 "
        "2017~2021 / 挖掘 B 2022~2026-09-04，挖掘读数=A∪B 合并池；全给="
        "legacy 单段，须 ⊆ 段内且双窗不相交；只给一部分=拒绝）",
    )
    ap.add_argument("--mining-end", default=None, help="挖掘窗终点")
    ap.add_argument("--judgment-start", default=None, help="判定窗起点")
    ap.add_argument("--judgment-end", default=None, help="判定窗终点")
    ap.add_argument(
        "--gates", default="all", help="逗号分隔门名（默认 all=全量 46 门 ENTRY_GATES）"
    )
    ap.add_argument("--n-random", type=int, default=DEFAULT_N_RANDOM, help="随机臂数")
    ap.add_argument(
        "--r45",
        action="store_true",
        help="R45 模式（v0.397）：j_low 单门 × 4 止损（信号日最低价×(1−x%%)，x∈5/8/12/15）"
        "× 4 止盈（现行/trail8/cz3/时间止损10）；总敞口 ≤30%%（并发 3 × 单仓 10%%）；"
        "参照格 x8_T1；C2 另须两窗组合收益>0（打过不交易）；忽略 --gates",
    )
    ap.add_argument(
        "--trade-dist",
        action="store_true",
        help="top 交易集逐窗附加完整分布画像（描述性副读数，v0.422，不进判据；R45 模式恒报必报子集）",
    )
    ap.add_argument("--c4-min-pool", type=int, default=C4_MIN_POOL, help="C4 最小池")
    ap.add_argument("--seed", type=int, default=20261008, help="全局种子（C3/C4 派生）")
    ap.add_argument(
        "--out-dir",
        default="artifacts/logs/bear_regime",
        help="产物根目录（默认 artifacts/logs/bear_regime）",
    )
    # 判定段批次锁（v0.374，TODO #95）：读判定段 2017~2021 须带批次身份
    ap.add_argument("--batch-id", default="", help="判定段批次号（攒批协议）")
    ap.add_argument("--candidate-id", default="", help="批内候选 id")
    ap.add_argument(
        "--print-config-hash",
        action="store_true",
        help="只打印 <tool>:<配置哈希> 并退出（冻结入批流程；不碰数据）",
    )
    return ap


def _resolve_layout(args: Any) -> tuple[dict[str, tuple[str, str]], list[str], bool]:
    """窗口布局解析（v2 布局，TODO #90，owner 2026-10-13 拍板）：

    - 四参全 None ⇒ **v2 三段**：段窗口表 {mining_a, judgment, mining_b}
      （load_window 段常量单源），挖掘段名序列=[mining_a, mining_b]（合并
      一池算挖掘读数）；
    - 四参全显式 ⇒ **legacy 单段**：{mining, judgment}（段合法性由 CLI
      ``_check_windows`` 钉死，这里原样沿用——R40 旧窗 ⊆ 挖掘 B 自动合法）；
    - 只给一部分 ⇒ ValueError（半截布局不许进跑数；CLI 层同规则 ap.error
      在先，本分支兜 run_study 直调）。

    返回 (段窗口表, 挖掘段名序列, is_v2)。
    """
    ms = args.mining_start
    me = args.mining_end
    js = args.judgment_start
    je = args.judgment_end
    if ms is not None and me is not None and js is not None and je is not None:
        return ({"mining": (ms, me), "judgment": (js, je)}, ["mining"], False)
    if ms is None and me is None and js is None and je is None:
        return (
            {
                "mining_a": lw.SEG_MINING_A,
                "judgment": lw.SEG_JUDGMENT,
                "mining_b": lw.SEG_MINING_B,
            },
            ["mining_a", "mining_b"],
            True,
        )
    raise ValueError(
        "窗口四参须全给或全不给（全不给=v2 三段布局：挖掘 A/判定/挖掘 B；"
        "全给=legacy 单段双窗）——只给一部分是半截布局，拒绝跑数（TODO #90）"
    )


def _check_windows(args: Any, ap: argparse.ArgumentParser) -> None:
    """CLI 窗口护栏（v2 布局，TODO #90——本模块单源，不再复用
    factor_exit_study._check_windows）：

    - 全 None ⇒ v2 三段（段常量即布局，无需再校）；
    - 全显式 ⇒ legacy：逐窗 ``segment_violation``（须 ⊆ 挖掘 A/判定/挖掘 B
      之一且不触前向 holdout，load_window 单源——v0.356 起 pre2019 交集
      硬拒绝已拆除：C5 退役、挖掘 A 与该段天然重叠）+ 双窗**不相交**
      （取代旧 ``mining_end < judgment_start`` 次序规则：v2 判定段天然夹在
      挖掘 A/B 之间，旧规则作废）；
    - 只给一部分 ⇒ ap.error（半截布局拒绝）。
    """
    ms = args.mining_start
    me = args.mining_end
    js = args.judgment_start
    je = args.judgment_end
    if ms is None and me is None and js is None and je is None:
        return
    if ms is None or me is None or js is None or je is None:
        ap.error(
            "窗口四参须全给或全不给（全不给=v2 三段布局：挖掘 A 2000~2016 / "
            "判定 2017~2021 / 挖掘 B 2022~2026-09-04）——只给一部分是半截布局"
        )
    for wname, s, e in (("mining", ms, me), ("judgment", js, je)):
        msg = (
            lw.mining_violation(s, e)
            if wname == "mining"
            else lw.segment_violation(s, e)
        )
        if msg:
            ap.error(f"{wname} {msg}")
    if max(ms, js) <= min(me, je):
        ap.error(
            f"挖掘窗 {ms}~{me} 与判定窗 {js}~{je} 相交——双窗须不相交"
            "（v2 布局：旧次序规则作废，相交硬拒绝）"
        )


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = _build_parser()
    args = ap.parse_args(argv)
    args.cmdline = " ".join(argv) if argv is not None else " ".join(sys.argv[1:])
    _check_windows(args, ap)  # v2 布局护栏（段校验+双窗不相交/半截拒绝）
    # 加载到达校验（v0.316，R41 指导顺手补）：逐股 _load_one_bars 不经批量
    # 截断护栏——count 不够会把窗口静默剪空（r36_c5 碎片宇宙教训）
    from custos.research.exit_c5_terminal import check_reach  # noqa: PLC0415

    try:
        # --count/check_reach 以最早段起点推算（v2=挖掘 A 2000-01-01）
        earliest = (
            lw.SEG_MINING_A[0] if args.mining_start is None else args.mining_start
        )
        args.count = lw.resolve_count(args.count, earliest)  # v0.328 缺省自动推算
        check_reach(args.count, earliest)
        if args.print_config_hash:
            # 冻结入批流程（TODO #95）：哈希必须与批次门同状态（参数归一化之后）
            from custos.research import judgment_batch as jb  # noqa: PLC0415

            return jb.print_config_hash_and_exit(args, "bear_regime_study")
        rep = run_study(args)
    except (RuntimeError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir) / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_bear_regime__{args.tag}.json"
    # 研究产物允许 NaN（区别于生产侧 paths.write_json 的 allow_nan=False）
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    c = rep["criteria"]
    top = rep["top"]
    top_txt = f"{top['gate']}×{top['exit']}" if top else "-"
    print(
        f"[R40] verdict={rep['verdict']} "
        f"top={top_txt} "
        f"C1={c['C1']['ok']} C2={c['C2']['ok']} C3={c['C3']['ok']} C4={c['C4']['state']}"
    )
    print(f"[R40] 报告 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
