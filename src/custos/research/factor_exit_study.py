# -*- coding: utf-8 -*-
"""R39 因子×出场交互研究终端（预注册 ``governance/research/R39_factor_exit_interaction.md``）。

问法：「这因子能不能告诉我该用哪套出场」——因子不排序选股，**分配出场**。
实验单元 =（因子连续值 × 分桶 × 映射 → 出场档）；主基准 = **uniform-best**
（同一档集内单一最优档应用于全部交易，选择同样只在挖掘窗——隔离「条件化
增量」与「只是找到了更好的单档」）。

做的事（一次一单）：
  ① 双窗 V0 重放预热（``exit_campaign.warm_v0_signals`` 同引擎：j_low+0AMV
     信号 + as-of V0 分，与出场参数无关每窗只算一次）；
  ② 首对象（写死）= 信号日 Wilder ADX(14) 连续值
     （``custos.core.indicators.dmi_arrays`` 单一实现；as-of 无未来数据——
     dmi 数组比 df **短 1**，bar i ↔ adx[i-1]，与 _j_low_adx_gate 同偏移）；
  ③ 分位切点**只在挖掘窗估计**（B2 中位数 / B3 三分位），判定窗直接应用
     （双窗硬隔离）；
  ④ 档集 K=4（R39 策展：基准/快抽身/慢趋势/保本，qsx 双杀不进），映射
     全枚举 4²+4³=**80 格**；重放优化 = 按（档×桶）预放 12 组交易再按映射
     合并，uniform 档 = 全桶合并 ⇒ 枚举成本 ≈ 每窗 12 组重放而非 80×4；
  ⑤ C1~C4 判定（R39 跑数前写死）：C1 top **逐桶**每窗 n_taken≥50（含 0
     笔空桶）且全窗≥100——**C1 不过 ⇒ untested（不可判）不判 falsified**
     （v0.301，样本不足≠否定证据）；C2 top Δmargin vs uniform-best
     **双窗同向为正**；C3 分位切点**加性** ±U(0, 0.2/n_buckets)×4 扰动
     （B2 ±10pp / B3 ±6.7pp，与预注册文字对齐）C2 结论**零翻转**；C4
     **随机分桶臂** N=50 同预算（保持桶大小打乱「交易→桶」归属，同 80
     格取 max mining Δmargin）q95 门，池≥50 才 confirmed 否则
     provisional（v0.297 式 rdd 过门率记账）；结局四态
     candidate/falsified/untested/provisional；
  ⑥ 产物自含（因子定义/切点/映射/档集/逐格读数/随机池）——供 C5 终端
     自含读取，禁手工转录。

CLI 护栏：任一窗口与 pre2019 untouched 段（2010-01-01..2016-12-31）有交集
即拒（与挖掘/判定侧研究工具同族镜像）。LLM 不碰数值；搜索标量 v2 口径
（margin 单量 + rdd **相对门** v2.1——rdd(候选)≥rdd(参照档 P1_base)，同窗同信号同口径；只相对排序，引用连带 R11 声明）。
"""

from __future__ import annotations

import argparse
import bisect
import itertools
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.paths import RESEARCH_DIR
from custos.research.evolution import exit_genome as eg
from custos.research import criteria_kit as kit
from custos.research import window_usage as wu
from custos.research.exit_c5_terminal import (
    FORWARD_HOLDOUT_START,
    PRE2019_END,
    PRE2019_START,
)

#: R39 写死档集（K=4，机制象限策展；全部经 exit_genome.validate 合法）
PROFILES: dict[str, dict[str, Any]] = {
    "P1_base": {"stop_pct": 5.0, "trail_pct": 0.08},  # =pct5_trail08 基准档
    "P2_fast": {"stop_pct": 4.0, "time_stop_bars": 10.0},  # 快抽身
    "P3_slow": {"stop_pct": 8.0, "trail_pct": 0.15},  # 慢趋势
    "P4_breakeven": {"stop_pct": 5.0, "breakeven_trigger": 0.05, "trail_pct": 0.08},
}

#: 分桶方案（分位点只估挖掘窗；B2 中位数二分 / B3 三分位）
BUCKETINGS: dict[str, tuple[float, ...]] = {"B2": (0.5,), "B3": (1 / 3, 2 / 3)}

#: 判据数值（R39 跑数前写死）
MIN_BUCKET_N_TAKEN = 50  # C1：top 每桶每窗最小选中笔数
MIN_N_TAKEN = 100  # C1：top 全窗最小选中笔数（C1 同族）
C3_DRAWS = 4  # C3 扰动臂数
C3_PERTURB = 0.2  # C3 分位点**加性**扰动幅度：±U(0, 0.2/n_buckets)——
# B2 ±10pp（0.5±0.1）、B3 ±6.7pp（1/3±0.067）；v0.301 由乘性 ±20% 改正
# （乘性下上切点实际扰 ±13.3pp，与预注册「±6.7pp」文字不符——跑数前统一）
DEFAULT_N_RANDOM = 50  # C4 随机分桶臂数
C4_MIN_POOL = 50  # C4 最小池（池满才许 confirmed）

FACTOR_DEF = {
    "name": "adx14",
    "definition": "信号日 Wilder ADX(14) 连续值（dmi_arrays 单一实现；as-of "
    "无未来数据：dmi 数组比 df 短 1，bar i ↔ adx[i-1]）",
}

_R11_NOTE = (
    "目标函数口径 v2.1（margin 单量 + rdd 相对门：候选≥参照档 P1_base 同窗 rdd）——按 R11 纪律只相对排序，"
    "绝对读数引用时连带本声明"
)


# ---------------------------------------------------------------------------
# 因子与分桶
# ---------------------------------------------------------------------------


def adx14_series(df: Any) -> dict[int, float]:
    """bar index → Wilder ADX(14)（as-of；dmi 数组短 1：bar i ↔ adx[i-1]）。"""
    from custos.core.indicators import dmi_arrays  # noqa: PLC0415

    _, _, adx = dmi_arrays(df["high"], df["low"], df["close"], 14)
    out: dict[int, float] = {}
    if adx is None:
        return out
    for i in range(1, len(df)):
        v = float(adx[i - 1])
        if v == v:  # NaN 跳过
            out[i] = v
    return out


def collect_signals(per_code: dict[str, dict]) -> list[dict]:
    """每窗信号清单：code/date/i/score/factor/sig（缺分或缺因子值的丢弃）。"""
    out: list[dict] = []
    for code, pack in per_code.items():
        f_by_bar = adx14_series(pack["df"])
        for s in pack["signals"]:
            score = pack["scores"].get(s["date"])
            f = f_by_bar.get(s["i"])
            if score is None or f is None:
                continue
            out.append(
                {
                    "code": code,
                    "date": s["date"],
                    "i": s["i"],
                    "score": score,
                    "factor": f,
                    "sig": s,
                }
            )
    return out


def quantile_cuts(values: list[float], qs: tuple[float, ...]) -> list[float]:
    """经验分位切点（线性插值；values 排序后取）。"""
    vs = sorted(values)
    if not vs:
        return []

    def _q(p: float) -> float:
        pos = p * (len(vs) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(vs) - 1)
        frac = pos - lo
        return vs[lo] * (1 - frac) + vs[hi] * frac

    return [_q(p) for p in qs]


def bucket_of(v: float, cuts: list[float]) -> int:
    """切点右侧开区间分桶：返回 0..len(cuts)。"""
    return bisect.bisect_right(cuts, v)


def buckets_of(signals: list[dict], cuts: list[float]) -> list[int]:
    return [bucket_of(r["factor"], cuts) for r in signals]


# ---------------------------------------------------------------------------
# 重放与读数
# ---------------------------------------------------------------------------


def replay_signals(
    per_code: dict[str, dict],
    subset: list[dict],
    profile_params: dict[str, Any],
    regime: dict[str, str],
    cost_bps: float,
) -> list[dict]:
    """信号子集按一个出场档重放（与 exit_campaign 评估器同调用序同公式）。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    by_code: dict[str, list[dict]] = {}
    for rec in subset:
        by_code.setdefault(rec["code"], []).append(rec)
    trades: list[dict] = []
    for code, recs in by_code.items():
        pack = per_code[code]
        trs = bt.evaluate_trades(
            {code: pack["df"]},
            signals_in={code: [r["sig"] for r in recs]},
            amv_regime=regime,
            cost_bps=cost_bps,
            collect_all=True,
            **profile_params,
        )
        score_of = {r["date"]: r["score"] for r in recs}
        for tr in trs:
            score = score_of.get(tr["entry_date"])
            if score is None:
                continue
            trades.append({**tr, "score": score, "code": code})
    return trades


def combine_readings(
    trades: list[dict],
    top_n: int,
    ref: Optional[dict[str, Any]] = None,
) -> Optional[dict]:
    """交易集 → 读数块（与 exit_campaign 评估器同形状 + taken 供 C1 桶级计数）。

    ``ref``：v2.1 相对 rdd 门的参照读数（须含 ret_over_dd；参照档本体传
    ``ref="self"`` ⇒ 自参照门恒真）；None = 绝对门兼容通道（勿用于新研究）。
    ⚠️ 口径注记（v2.1 在案）：margin/胜率/盈亏比 = **全候选交易**（collect_all）
    口径，rdd/ret_over_dd = **top-N 组合实际选中子集**口径——两批交易不同
    （「margin 为正、组合 rdd 为负」可以同时成立），引用时连带本注记。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import strategy_grid as sg  # noqa: PLC0415
    from custos.research.exit_campaign import V0_PORTFOLIO  # noqa: PLC0415

    if not trades:
        return None
    tsum = bt.summarize_trades(trades)
    taken: list[dict] = []
    pf = bt.simulate_portfolio_topn(
        trades, top_n=top_n, taken_out=taken, **V0_PORTFOLIO
    )
    row = {
        "margin": sg._margin(
            {"win": tsum.get("win_rate"), "payoff": tsum.get("payoff_ratio")}
        ),
        "expectancy_R": tsum.get("expectancy_R"),
        "ret_over_dd": sg._ret_over_dd(pf),
    }
    ref_eff = row if ref == "self" else ref
    return {
        "objective": sg.search_objective(row, ref_eff),
        "objective_version": sg.OBJECTIVE_VERSION,
        "rdd_gate": sg.rdd_gate_ok(row, ref_eff),
        "margin": row["margin"],
        "expectancy_R": row["expectancy_R"],
        "payoff_ratio": tsum.get("payoff_ratio"),
        "win_rate": tsum.get("win_rate"),
        "n": tsum.get("n"),
        "n_taken": pf.get("n_taken"),
        "ret_over_dd": row["ret_over_dd"],
        "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集"
        "——两批交易不同（v2.1 注明在案）",
        "taken": taken,
    }


# ---------------------------------------------------------------------------
# 单窗研究：80 格枚举 + uniform 基准
# ---------------------------------------------------------------------------


def enumerate_mappings(n_buckets: int) -> list[tuple[str, ...]]:
    """桶 → 档映射全枚举（4^n_buckets；顺序确定）。"""
    return list(itertools.product(list(PROFILES), repeat=n_buckets))


def study_window(
    per_code: dict[str, dict],
    signals: list[dict],
    buckets: list[int],
    regime: dict[str, str],
    cost_bps: float,
    top_n: int,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
    ref_readings: Optional[dict] = None,
) -> dict[str, Any]:
    """单窗研究：按（档×桶）预放交易组，80 映射合并 + uniform 基准。

    ``replay_fn`` 可注入（测试合成）；None = 生产重放（与评估器同引擎）。
    ``ref_readings``（v2.1 相对门参照）：None = 用本窗 uniform P1_base 读数
    （参照档=事先固定的基准档）；C3/C4 臂须传**主研究的 P1_base 读数**
    （口径对称——随机臂也和同一个参照档比，owner v2.1 指令）。
    返回 {configs: [...], uniform: {...}}；config 读数含 per-bucket n_taken。
    """
    if replay_fn is None:

        def replay_fn(subset: list[dict], params: dict[str, Any]) -> list[dict]:
            return replay_signals(per_code, subset, params, regime, cost_bps)

    n_b = (max(buckets) + 1) if buckets else 0
    cache: dict[tuple[str, int], list[dict]] = {}

    def _group(pk: str, b: int) -> list[dict]:
        key = (pk, b)
        if key not in cache:
            subset = [r for r, bb in zip(signals, buckets) if bb == b]
            if not subset:
                cache[key] = []
            else:
                params = {**eg.FIXED_PARAMS, **eg.normalize(PROFILES[pk])}
                cache[key] = [{**tr, "_bucket": b} for tr in replay_fn(subset, params)]
        return cache[key]

    def _merge(pk: str) -> list[dict]:
        trades: list[dict] = []
        for b in range(n_b):
            trades.extend(_group(pk, b))
        return trades

    # ── 参照档 P1_base 先行（自参照恒真；v2.1：事先固定不经过挑选的档）──
    ref_rd = ref_readings or combine_readings(_merge("P1_base"), top_n, ref="self")
    uniform: dict[str, Optional[dict]] = {"P1_base": ref_rd}
    for pk in PROFILES:
        if pk == "P1_base":
            continue
        uniform[pk] = combine_readings(_merge(pk), top_n, ref=ref_rd)

    def _config_reading(mapping: tuple[str, ...]) -> Optional[dict]:
        trades: list[dict] = []
        for b, pk in enumerate(mapping):
            trades.extend(_group(pk, b))
        rd = combine_readings(trades, top_n, ref=ref_rd)
        if rd is not None:
            per_bucket: dict[str, int] = {}
            for t in rd["taken"]:
                per_bucket[t["_bucket"]] = per_bucket.get(t["_bucket"], 0) + 1
            rd["n_taken_by_bucket"] = per_bucket
        return rd

    configs = [
        {"mapping": list(mapping), "readings": _config_reading(mapping)}
        for mapping in enumerate_mappings(n_b)
    ]
    return {"configs": configs, "uniform": uniform, "n_buckets": n_b}


def mapping_trades(
    per_code: dict[str, dict],
    signals: list[dict],
    buckets: list[int],
    mapping: tuple | list,
    regime: dict[str, str],
    cost_bps: float,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
) -> list[dict]:
    """指定（桶→档）映射的交易集**定向重建**（成本副读数 review #6 用）。

    与 study_window._group 同参数路径同引擎（重放确定性 ⇒ 逐位一致），
    只放映射需要的 (档×桶) 组，不枚举 80 格——主研究选完 top 后再补交易
    集走这里，比给 study_window 加 out 参保留全量缓存省内存。
    """
    if replay_fn is None:

        def replay_fn(subset: list[dict], params: dict[str, Any]) -> list[dict]:
            return replay_signals(per_code, subset, params, regime, cost_bps)

    n_b = (max(buckets) + 1) if buckets else 0
    trades: list[dict] = []
    for b in range(n_b):
        subset = [r for r, bb in zip(signals, buckets) if bb == b]
        if not subset:
            continue
        params = {**eg.FIXED_PARAMS, **eg.normalize(PROFILES[mapping[b]])}
        trades.extend(replay_fn(subset, params))
    return trades


# ---------------------------------------------------------------------------
# 判据（R39 跑数前写死）
# ---------------------------------------------------------------------------


def pick_top(configs: list[dict]) -> Optional[dict]:
    """top 选择：挖掘窗 margin 最大（rdd 门不过 ⇒ objective None 不参与，v2）。"""
    ok = [
        c for c in configs if c["readings"] and c["readings"]["objective"] is not None
    ]
    if not ok:
        return None
    return max(ok, key=lambda c: c["readings"]["margin"])


def pick_uniform_best(uniform: dict[str, Optional[dict]]) -> Optional[dict]:
    """uniform-best：档集内挖掘窗 margin 最大（rdd 门同待遇；选择只在挖掘窗）。"""
    ok = [
        {"profile": pk, "readings": rd}
        for pk, rd in uniform.items()
        if rd and rd["objective"] is not None
    ]
    if not ok:
        return None
    return max(ok, key=lambda r: r["readings"]["margin"])


def judge_c1(
    top_mining: Optional[dict], top_judgment: Optional[dict], n_buckets: int
) -> dict[str, Any]:
    """C1：top **逐桶**每窗 n_taken≥50（含 0 笔空桶——v0.301 修订：
    n_taken_by_bucket 只统计有成交的桶，缺 key=0 笔必须照判）且全窗≥100。

    ⚠️ C1 不过的读法是 **untested（不可判）不是 falsified**——样本不足≠
    否定证据（v0.299 可疑闸同哲学；owner review：B3 判定窗 n~146/3≈49
    贴线，稀疏桶被 max-of-80 挑中再判死 = 重演「数据坏了误判成候选坏了」）。
    """
    out: dict[str, Any] = {
        "min_bucket": MIN_BUCKET_N_TAKEN,
        "min_total": MIN_N_TAKEN,
        "n_buckets": n_buckets,
    }
    for wname, rd in (("mining", top_mining), ("judgment", top_judgment)):
        if not rd:
            out[wname] = {"ok": False, "reason": "无读数"}
            continue
        per_b = rd.get("n_taken_by_bucket") or {}
        lows = {
            b: per_b.get(b, 0)
            for b in range(n_buckets)
            if per_b.get(b, 0) < MIN_BUCKET_N_TAKEN
        }
        total = rd.get("n_taken") or 0
        out[wname] = {
            "ok": not lows and total >= MIN_N_TAKEN,
            "n_taken": total,
            "by_bucket": per_b,
            "below_floor": lows,
        }
    out["ok"] = bool(out["mining"].get("ok") and out["judgment"].get("ok"))
    return out


def judge_c2(
    dm_mining: Optional[float], dm_judgment: Optional[float]
) -> dict[str, Any]:
    """C2：Δmargin vs uniform-best 双窗同向为正。"""
    ok = (
        dm_mining is not None
        and dm_judgment is not None
        and dm_mining > 0
        and dm_judgment > 0
    )
    return {
        "ok": ok,
        "d_margin_mining": dm_mining,
        "d_margin_judgment": dm_judgment,
        "rule": "双窗同向为正（vs uniform-best）",
    }


def judge_c3(draws: list[dict]) -> dict[str, Any]:
    """C3：分位切点 ±20%×4 扰动，C2 结论零翻转。"""
    flips = [d for d in draws if not d["c2_holds"]]
    return {
        "ok": not flips and len(draws) == C3_DRAWS,
        "draws": draws,
        "n_flips": len(flips),
        "rule": f"分位切点 ±20% ×{C3_DRAWS} 扰动零翻转",
    }


def judge_c4(
    top_dm_mining: Optional[float],
    pool: list[float],
    evaluated: int,
    gate_pass: int,
    min_pool: int = C4_MIN_POOL,
) -> dict[str, Any]:
    """C4：随机分桶臂池 q95 门（状态机=criteria_kit 单源 v0.322——空池
    indeterminate / 池<min_pool provisional / Δ>q95 confirmed_pass）。"""
    state = kit.c4_state_of(pool, min_pool=min_pool, plan_delta=top_dm_mining)
    return {
        "state": state,
        "ok": (
            True
            if state == "confirmed_pass"
            else (False if state == "confirmed_fail" else None)
        ),
        "pool_size": len(pool),
        "evaluated": evaluated,
        "gate_pass": gate_pass,
        "gate_pass_rate": (gate_pass / evaluated if evaluated else None),
        "q95": kit.q95(pool),
        "min_pool": min_pool,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _d_margin(rd: Optional[dict], base: Optional[dict]) -> Optional[float]:
    if not rd or not base:
        return None
    m, b = rd.get("margin"), base.get("margin")
    if m is None or b is None:
        return None
    return m - b


def run_study(
    args: Any,
    warm_fn: Optional[Callable[[str, str], dict[str, dict]]] = None,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
) -> dict[str, Any]:
    """研究主流程：双窗预热 → 因子/分桶 → 80 格 → uniform → C1~C4 → 报告。

    ``warm_fn(start, end) → per_code`` 与 ``replay_fn(subset, params)`` 可注入
    （测试合成）；None = 生产路径（exit_campaign 同引擎预热/重放）。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import exit_campaign as ec  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    windows = {
        "mining": (args.mining_start, args.mining_end),
        "judgment": (args.judgment_start, args.judgment_end),
    }
    regime: dict[str, str] = {}
    if warm_fn is None:
        ns = argparse.Namespace(
            codes_file=args.codes_file,
            universe_local=args.universe_local,
            universe_sample=args.universe_sample,
            seed=args.universe_seed,
            codes=args.codes,
        )
        codes = bt._resolve_universe(ns, _build_parser())
        regime = bt.load_amv_regime(since=args.mining_start)
        if not regime:
            raise RuntimeError("0AMV regime 读不到（compass_amv）——本机无数据？")
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

        index_df = (
            local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
            .sort_values("date")
            .reset_index(drop=True)
        )

        def warm_fn(start: str, end: str) -> dict[str, dict]:
            return ec.warm_v0_signals(
                codes,
                regime,
                index_df,
                count=args.count,
                cost_bps=args.cost_bps,
                start=start,
                end=end,
            )

    per_code = {w: warm_fn(*se) for w, se in windows.items()}
    signals = {w: collect_signals(pc) for w, pc in per_code.items()}
    if not signals["mining"] or not signals["judgment"]:
        raise RuntimeError(
            f"空结果护栏：挖掘 {len(signals['mining'])} / 判定 "
            f"{len(signals['judgment'])} 信号（宇宙/窗口/数据有问题？）——不落盘"
        )

    # ── 分位切点只在挖掘窗估计（双窗硬隔离）──
    mining_values = [r["factor"] for r in signals["mining"]]
    cuts = {name: quantile_cuts(mining_values, qs) for name, qs in BUCKETINGS.items()}
    buckets: dict[str, dict[str, list[int]]] = {
        name: {w: buckets_of(signals[w], c) for w in windows}
        for name, c in cuts.items()
    }

    # ── 双窗研究（每种分桶方案一组映射枚举；uniform 与分桶无关——全桶合并
    # = 全信号同一档重放，只算一次复用）──
    studies: dict[str, dict[str, Any]] = {}
    for name in BUCKETINGS:
        studies[name] = {
            w: study_window(
                per_code[w],
                signals[w],
                buckets[name][w],
                regime,
                args.cost_bps,
                args.top_n,
                replay_fn=replay_fn,
            )
            for w in windows
        }

    all_configs: list[dict] = []
    for name, stud in studies.items():
        for c in stud["mining"]["configs"]:
            jd = next(
                x for x in stud["judgment"]["configs"] if x["mapping"] == c["mapping"]
            )
            all_configs.append(
                {
                    "bucketing": name,
                    "mapping": c["mapping"],
                    "mining": c["readings"],
                    "judgment": jd["readings"],
                    "readings": c["readings"],  # pick_top 契约键（=挖掘窗读数）
                }
            )

    top = pick_top(all_configs)
    # uniform-best：档集内挖掘窗 margin 最大（rdd 门同待遇；选择只在挖掘窗。
    # uniform 读数与分桶方案无关——取 B2 研究的 uniform 块即可）
    uniform_best = pick_uniform_best(studies["B2"]["mining"]["uniform"])
    uniform_jd = (
        studies["B2"]["judgment"]["uniform"][uniform_best["profile"]]
        if uniform_best
        else None
    )
    # v2.1 相对门参照（口径对称）：C3 扰动臂/C4 随机臂与主研究同一参照档
    # （同窗 P1_base 读数——事先固定不经过挑选）
    ref_by_window = {w: studies["B2"][w]["uniform"]["P1_base"] for w in windows}

    dm_m = (
        _d_margin(top["mining"], uniform_best["readings"])
        if top and uniform_best
        else None
    )
    dm_j = _d_margin(top["judgment"], uniform_jd) if top and uniform_best else None

    c1 = judge_c1(
        top["mining"] if top else None,
        top["judgment"] if top else None,
        len(top["mapping"]) if top else 0,
    )
    c2 = judge_c2(dm_m, dm_j)

    # ── C3：分位切点加性 ±U(0, 0.2/n_buckets) ×4 扰动（top 映射不变，
    # 切点重估重分桶；v0.301 由乘性改正——与预注册「±6.7pp」对齐）──
    rng = random.Random(args.seed)
    c3_draws: list[dict] = []
    if top is not None:
        n_b_top = len(top["mapping"])
        delta = C3_PERTURB / n_b_top
        for draw_i in range(C3_DRAWS):
            name = top["bucketing"]
            qs = BUCKETINGS[name]
            qs_p = tuple(
                sorted(min(0.99, max(0.01, q + rng.uniform(-delta, delta))) for q in qs)
            )
            cuts_p = quantile_cuts(mining_values, qs_p)
            stud_p = {
                w: study_window(
                    per_code[w],
                    signals[w],
                    buckets_of(signals[w], cuts_p),
                    regime,
                    args.cost_bps,
                    args.top_n,
                    replay_fn=replay_fn,
                    ref_readings=ref_by_window[w],  # v2.1 同参照口径对称
                )
                for w in windows
            }
            rd_m = next(
                c for c in stud_p["mining"]["configs"] if c["mapping"] == top["mapping"]
            )["readings"]
            rd_j = next(
                c
                for c in stud_p["judgment"]["configs"]
                if c["mapping"] == top["mapping"]
            )["readings"]
            dm_p_m = _d_margin(rd_m, uniform_best["readings"]) if uniform_best else None
            dm_p_j = _d_margin(rd_j, uniform_jd) if uniform_best else None
            c3_draws.append(
                {
                    "draw": draw_i,
                    "qs_perturbed": list(qs_p),
                    "cuts": cuts_p,
                    "d_margin_mining": dm_p_m,
                    "d_margin_judgment": dm_p_j,
                    "c2_holds": bool(
                        dm_p_m is not None
                        and dm_p_m > 0
                        and dm_p_j is not None
                        and dm_p_j > 0
                    ),
                }
            )
    c3 = judge_c3(c3_draws)

    # ── C4：随机分桶臂 N=50 同预算（保持桶大小打乱归属，同 80 格取 max）──
    # v0.322：池构造改 **criteria_kit 重抽单源**（v0.317 族——过门臂满 N
    # 或评估上限 10×N；原单遍 50 臂在一臂不过门时池 49 永 provisional，
    # owner 方法论 review #4/#7）。
    if top is not None and uniform_best is not None:
        ub_margin_m = uniform_best["readings"]["margin"]

        def _arm(_i: int) -> Optional[float]:
            arm_best: Optional[float] = None
            arm_ok = False
            for name in BUCKETINGS:
                perm = list(buckets[name]["mining"])
                rng.shuffle(perm)
                stud_a = study_window(
                    per_code["mining"],
                    signals["mining"],
                    perm,
                    regime,
                    args.cost_bps,
                    args.top_n,
                    replay_fn=replay_fn,
                    ref_readings=ref_by_window["mining"],  # v2.1 随机臂同参照档
                )
                top_a = pick_top(
                    [
                        {"mapping": c["mapping"], "readings": c["readings"]}
                        for c in stud_a["configs"]
                    ]
                )
                if top_a and top_a["readings"]["margin"] is not None:
                    arm_ok = True
                    dm = top_a["readings"]["margin"] - ub_margin_m
                    arm_best = dm if arm_best is None else max(arm_best, dm)
            return arm_best if (arm_ok and arm_best is not None) else None

        c4_pool = kit.assemble_c4_pool(args.n_random, _arm)
        pool = c4_pool["pool"]
        evaluated = c4_pool["evaluated"]
        gate_pass = c4_pool["gate_pass"]
    else:
        pool, evaluated, gate_pass = [], 0, 0
    c4 = judge_c4(dm_m, pool, evaluated, gate_pass, args.c4_min_pool)

    # ── 总结局（criteria_kit 单源：C1 不过 ⇒ untested 优先）──
    verdict = kit.verdict_four_state(
        c1_ok=c1["ok"], c2_ok=c2["ok"], c3_ok=c3["ok"], c4_state=c4["state"]
    )

    def _slim(rd: Optional[dict]) -> Optional[dict]:
        if rd is None:
            return None
        return {k: v for k, v in rd.items() if k != "taken"}

    # ── 成本副读数（owner review #6）：top/uniform-best 交易集定向重建
    # （mapping_trades 同引擎同参数路径），50bps 解析双报 + Δ 翻号标记 ──
    cost_sens: Optional[dict] = None
    if top is not None and uniform_best is not None:
        from custos.research.cost_sensitivity import cost_side_block  # noqa: PLC0415

        n_b_top = len(top["mapping"])
        named: dict[str, list[dict]] = {}
        for w in windows:
            named[f"top_{w}"] = mapping_trades(
                per_code[w],
                signals[w],
                buckets[top["bucketing"]][w],
                top["mapping"],
                regime,
                args.cost_bps,
                replay_fn,
            )
            named[f"uniform_{w}"] = mapping_trades(
                per_code[w],
                signals[w],
                buckets[top["bucketing"]][w],
                (uniform_best["profile"],) * n_b_top,
                regime,
                args.cost_bps,
                replay_fn,
            )
        cost_sens = cost_side_block(
            named,
            base_bps=args.cost_bps,
            deltas={
                "d_margin_mining": ("top_mining", "uniform_mining"),
                "d_margin_judgment": ("top_judgment", "uniform_judgment"),
            },
        )

    # 判定窗使用台账（v0.321，owner 方法论 review #1）：本报告=该窗第 k 次被读
    _wu_k = wu.record_use(
        "R39",
        "judgment",
        args.tag,
        "C1~C4 判定窗读数",
        synthetic=(warm_fn is not None or replay_fn is not None),
    )
    from custos.research import provenance as pv  # noqa: PLC0415
    from custos.research.load_window import EXIT_BARS_HOLDOUT_NOTE  # noqa: PLC0415

    return {
        "schema": "factor_exit_report/v1",
        "tag": args.tag,
        "verdict": verdict,
        "window_usage": {
            "window": "judgment",
            "k": _wu_k,
            "note": wu.usage_note("R39", "judgment", _wu_k),
        },
        "provenance": pv.build(
            args,
            unit="R39",
            criteria_version="v0.301/v2.1",
            pre_reg_doc=RESEARCH_DIR / "R39_factor_exit_interaction.md",
            data_last_date=max(
                filter(None, (pv.last_date_of(per_code[w]) for w in windows)),
                default=None,
            ),
        ),
        "factor": FACTOR_DEF,
        "profiles": {
            pk: {"genome": eg.normalize(g), "key": eg.genome_key(g)}
            for pk, g in PROFILES.items()
        },
        "bucketings": {
            name: {"qs": list(BUCKETINGS[name]), "cuts_mining_estimated": cuts[name]}
            for name in BUCKETINGS
        },
        "windows": {w: {"start": se[0], "end": se[1]} for w, se in windows.items()},
        "n_signals": {w: len(signals[w]) for w in windows},
        "top": (
            {
                "bucketing": top["bucketing"],
                "mapping": top["mapping"],
                "mining": _slim(top["mining"]),
                "judgment": _slim(top["judgment"]),
            }
            if top
            else None
        ),
        "uniform_best": (
            {
                "profile": uniform_best["profile"],
                "mining": _slim(uniform_best["readings"]),
                "judgment": _slim(uniform_jd),
            }
            if uniform_best
            else None
        ),
        "cost_sensitivity": cost_sens,
        "forward_holdout_note": EXIT_BARS_HOLDOUT_NOTE,
        "configs": [
            {
                "bucketing": c["bucketing"],
                "mapping": c["mapping"],
                "mining": _slim(c["mining"]),
                "judgment": _slim(c["judgment"]),
            }
            for c in all_configs
        ],
        "criteria": {
            "C1": c1,
            "C2": c2,
            "C3": c3,
            "C4": {**c4, "pool": pool},
            "rule_note": "R39-C1~C4 跑数前写死；C5（pre2019 终审）= C2 过线才启动，owner 拍板发令",
        },
        "objective_version": sg.OBJECTIVE_VERSION,
        "notes": [_R11_NOTE],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R39 因子×出场交互研究终端（判据 C1~C4 跑数前写死；主基准=uniform-best）"
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
        help="每股回溯 K 线根数（缺省=按挖掘窗起点自动推算，显式值覆盖）",
    )
    ap.add_argument("--cost-bps", type=float, default=25.0, help="往返成本基点")
    ap.add_argument("--mining-start", default="2022-01-01", help="挖掘窗起点")
    ap.add_argument("--mining-end", default="2024-07-31", help="挖掘窗终点")
    ap.add_argument("--judgment-start", default="2024-08-01", help="判定窗起点")
    ap.add_argument("--judgment-end", default="2026-09-04", help="判定窗终点")
    ap.add_argument(
        "--n-random", type=int, default=DEFAULT_N_RANDOM, help="C4 随机分桶臂数"
    )
    ap.add_argument("--c4-min-pool", type=int, default=C4_MIN_POOL, help="C4 最小池")
    ap.add_argument("--seed", type=int, default=20261008, help="全局种子（C3/C4 派生）")
    ap.add_argument(
        "--out-dir",
        default="artifacts/logs/factor_exit",
        help="产物根目录（默认 artifacts/logs/factor_exit）",
    )
    return ap


def _check_windows(args: Any, ap: argparse.ArgumentParser) -> None:
    """窗口护栏：与 pre2019 untouched 段交集即拒（同族镜像）+ 双窗次序 +
    **前向 holdout 冻结**（v0.321，owner 方法论 review #1②；v0.330 起
    校验本体=load_window 共享单源——owner review #2②）。"""
    from custos.research.load_window import forward_holdout_violation  # noqa: PLC0415

    for wname in ("mining", "judgment"):
        s = getattr(args, f"{wname}_start")
        e = getattr(args, f"{wname}_end")
        if s > e:
            ap.error(f"{wname} 窗口起终点颠倒: {s}~{e}")
        if s <= PRE2019_END and e >= PRE2019_START:
            ap.error(
                f"{wname} 窗口 {s}~{e} 与 pre2019 untouched 段"
                f"（{PRE2019_START}~{PRE2019_END}）交集——研究工具对 pre2019 硬拒绝"
            )
        msg = forward_holdout_violation(s, e)
        if msg:
            ap.error(f"{wname} {msg}")
    if args.mining_end >= args.judgment_start:
        ap.error(
            f"挖掘窗终点 {args.mining_end} 须早于判定窗起点 {args.judgment_start}（双窗硬隔离）"
        )


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = _build_parser()
    args = ap.parse_args(argv)
    args.cmdline = " ".join(argv) if argv is not None else " ".join(sys.argv[1:])
    _check_windows(args, ap)
    # 加载到达校验（v0.316，R41 指导顺手补）：逐股 _load_one_bars 不经批量
    # 截断护栏——count 不够会把窗口静默剪空（r36_c5 碎片宇宙教训）
    from custos.research.exit_c5_terminal import check_reach  # noqa: PLC0415
    from custos.research.load_window import resolve_count  # noqa: PLC0415

    try:
        args.count = resolve_count(args.count, args.mining_start)  # v0.328 缺省自动推算
        check_reach(args.count, args.mining_start)
        rep = run_study(args)
    except (RuntimeError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir) / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_factor_exit__{args.tag}.json"
    # 研究产物允许 NaN（区别于生产侧 paths.write_json 的 allow_nan=False）
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    c = rep["criteria"]
    top = rep["top"]
    print(
        f"[R39] verdict={rep['verdict']} "
        f"top={top['mapping'] if top else None}@{top['bucketing'] if top else '-'} "
        f"C1={c['C1']['ok']} C2={c['C2']['ok']} C3={c['C3']['ok']} C4={c['C4']['state']}"
    )
    print(f"[R39] 报告 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
