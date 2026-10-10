# -*- coding: utf-8 -*-
"""R40 · 0AMV 空头区间做多策略全栈研究终端
（预注册 ``governance/research/R40_bear_regime_long_strategy.md``）。

问法（owner 2026-10-08 立项）：0AMV **空头区间**内主动开仓的
（入场门 × 止损/止盈档）全栈最优——现状=空头区间纯防守（只卖不买+
反弹减仓），主动开仓从未被研究。

做的事（一次一单）：
  ① 双窗逐股加载一次 → **全量 46 门 ENTRY_GATES** 逐门扫信号（
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

CLI 护栏：窗口与 pre2019 untouched 段交集即拒（镜像同族，复用
factor_exit_study._check_windows）。LLM 不碰数值；v2 口径（margin 单量
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

from custos.research import factor_exit_study as fes
from custos.research import window_usage as wu
from custos.research.evolution import exit_genome as eg
from custos.research.exit_campaign import _q95

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
) -> dict[str, dict]:
    """R40 预热：逐股加载一次 → 逐门扫信号（门在内层 ⇒ df 只加载一次）→
    as-of V0 分（与门无关，按 (code, date) 去重）。

    与 ``exit_campaign.warm_v0_signals`` 同引擎同调用序（baseline scorer +
    entry_gate + amv + collect_all），差异只在循环嵌套。df 加载成功的票
    即使 0 信号也保留（随机臂母体=全宇宙空头日 bar，不是门信号集）。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415

    per_code: dict[str, dict] = {}
    for i, code in enumerate(codes, 1):
        if i % 200 == 0:
            print(f"[warmup] {start}~{end} {i}/{len(codes)}", file=sys.stderr)
        df = bt._load_one_bars(code, count, start, end)
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
            if sigs:
                gate_signals[g] = sigs
            for s in sigs:
                if s["date"] in scores:
                    continue
                try:
                    score, _level, _contrib = srs.asof_technical_score(
                        df, index_df, s["i"], code
                    )
                except Exception:  # noqa: BLE001 — 单信号评分失败丢该信号
                    continue
                scores[s["date"]] = score
        per_code[code] = {"df": df, "gate_signals": gate_signals, "scores": scores}
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


def random_entries(
    per_code: dict[str, dict],
    regime_bear: dict[str, str],
    n: int,
    rng: random.Random,
    index_df: Any = None,
    *,
    pool: Optional[list[tuple[str, int, str]]] = None,
    score_cache: Optional[dict[tuple[str, int], Optional[float]]] = None,
) -> list[dict]:
    """空头日随机 (code, bar) 入场 n 个；**无放回抽样**（v0.308：``rng.sample``，
    母体不够就截断——有放回会让同一 (code, bar) 重复计入）；score 缺失时
    生产路径现算（注入路径由测试给）。

    v0.309 性能（结果逐位不变）：``pool`` 可预建注入（每窗一次，免逐臂
    重建）；``score_cache`` 按 (code, i) 跨臂跨门共用——抽样总量百万级时
    绝大部分命中，打分次数上限=母体大小；None（打分失败）同样缓存，
    跳过语义与未缓存逐位一致。"""
    if pool is None:
        pool = bear_bar_pool(per_code, regime_bear)
    if not pool or n <= 0:
        return []
    out: list[dict] = []
    for code, i, d in rng.sample(pool, min(n, len(pool))):
        score = per_code[code]["scores"].get(d)
        if score is None and index_df is not None:
            key = (code, i)
            if score_cache is not None and key in score_cache:
                score = score_cache[key]
            else:
                try:
                    from custos.research import score_return_study as srs  # noqa: PLC0415

                    score, _level, _contrib = srs.asof_technical_score(
                        per_code[code]["df"], index_df, i, code
                    )
                except Exception:  # noqa: BLE001
                    score = None
                if score_cache is not None:
                    score_cache[key] = score
        if score is None:
            continue
        out.append(
            {
                "code": code,
                "date": d,
                "i": i,
                "score": score,
                "sig": {"code": code, "i": i, "date": d, "score": score},
                "gate": "__random__",
            }
        )
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
        entries = draw_entries(n_g, "mining")
        ref_rd: Optional[dict] = None
        for e in exits:
            if e["name"] == "pct5_trail08":
                ref_rd = fes.combine_readings(
                    replay_mining(entries, e["params"]), top_n, ref="self"
                )
                break
        for e in exits:
            trades = replay_mining(entries, e["params"])
            rd = fes.combine_readings(trades, top_n, ref=ref_rd)
            if rd and rd["objective"] is not None and rd["margin"] is not None:
                if best is None or rd["margin"] > best["readings"]["margin"]:
                    best = {
                        "n_signals": n_g,
                        "gate_template": g,
                        "exit": e["name"],
                        "params": e["params"],
                        "readings": rd,
                    }
    out: dict[str, Any] = {"mining": best}
    if best is not None and judgment_reader is not None:
        out["judgment"] = judgment_reader(
            best["n_signals"], best["params"]
        )  # 冻结配置（含胜出格 n_g），不重选
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


def run_study(
    args: Any,
    warm_fn: Optional[Callable[[str, str], dict[str, dict]]] = None,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
    random_entry_fn: Optional[Callable[[int, str], list[dict]]] = None,
) -> dict[str, Any]:
    """R40 主流程：双窗 46 门×5 档枚举 + 随机臂冻结池 + C1~C4 → 报告。

    ``warm_fn(start, end) → per_code`` / ``replay_fn(subset, params)`` /
    ``random_entry_fn(n, window)`` 可注入（测试合成）；None = 生产路径。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    windows = {
        "mining": (args.mining_start, args.mining_end),
        "judgment": (args.judgment_start, args.judgment_end),
    }
    gates = (
        sorted(bt.ENTRY_GATES)
        if args.gates == "all"
        else [g.strip() for g in args.gates.split(",") if g.strip()]
    )
    exits = sg.DEFAULT_EXIT_GRID
    regime_bear: dict[str, str] = {}
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
        regime_bear = invert_regime_bearish(regime)
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415
        from custos.research import score_return_study as srs  # noqa: PLC0415

        index_df = (
            local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
            .sort_values("date")
            .reset_index(drop=True)
        )

        def warm_fn(start: str, end: str) -> dict[str, dict]:  # noqa: F811
            return warm_gates(
                codes,
                regime_bear,
                index_df,
                gates=gates,
                count=args.count,
                cost_bps=args.cost_bps,
                start=start,
                end=end,
            )

    per_code = {w: warm_fn(*se) for w, se in windows.items()}
    gate_sigs: dict[str, dict[str, list[dict]]] = {
        w: {g: collect_gate_signals(per_code[w], g) for g in gates} for w in windows
    }
    total_signals = sum(len(v) for w in gate_sigs.values() for v in w.values())
    if not total_signals:
        raise RuntimeError(
            "空结果护栏：46 门双窗 0 信号（宇宙/窗口/数据有问题？）——不落盘"
        )

    def _replay(w: str, g: str, subset: list[dict], params: dict[str, Any]):
        if replay_fn is not None:
            return replay_fn(subset, params)
        if g == "__random__":
            view = _entry_view(per_code[w], subset)  # 随机入场集专用视图
        else:
            view = _gate_view(per_code[w], g)
        return fes.replay_signals(view, subset, params, regime_bear, args.cost_bps)

    # ── 230 格 × 双窗（重放缓存 per（门, 档, 窗）；参照档 = 同窗同信号
    # pct5_trail08——v2.1 事先固定不经过挑选的档，每（门,窗）先算并存 refs）──
    configs: list[dict] = []
    ref_trail = next(e["params"] for e in exits if e["name"] == "pct5_trail08")
    refs: dict[tuple[str, str], Optional[dict]] = {}
    for g in gates:
        for w in windows:
            trades = _replay(w, g, gate_sigs[w][g], ref_trail)
            refs[(g, w)] = fes.combine_readings(trades, args.top_n, ref="self")
        for e in exits:
            row: dict[str, Any] = {"gate": g, "exit": e["name"]}
            for w in windows:
                if e["name"] == "pct5_trail08":
                    rd = refs[(g, w)]  # 参照档本体=自参照读数（复用不重复重放）
                else:
                    trades = _replay(w, g, gate_sigs[w][g], e["params"])
                    rd = fes.combine_readings(trades, args.top_n, ref=refs[(g, w)])
                if rd is not None:
                    rd.pop("taken", None)  # 报告不落交易明细
                row[w] = rd
            configs.append(row)

    def _top(rows: list[dict]) -> Optional[dict]:
        ok = [c for c in rows if c["mining"] and c["mining"]["objective"] is not None]
        return max(ok, key=lambda c: c["mining"]["margin"]) if ok else None

    top = _top(configs)

    # ── 随机臂（对等纪律 v0.302 + **预算对等 v0.307**：每臂完整复刻 top 的
    # 46 门 × 5 档 max-of-230 选型——逐门 n_g 对齐抽样，冻结配置进判定窗）──
    rng = random.Random(args.seed)
    counts_m = {g: len(gate_sigs["mining"][g]) for g in gates}
    pool_m: list[float] = []
    pool_j: list[float] = []
    arm_evaluated = 0
    arm_gate_pass = 0
    index_df_ref: dict[str, Any] = {"df": locals().get("index_df")}  # 生产路径才有

    # v0.309 性能（结果逐位不变）：母体每窗惰性预建一次（random_entry_fn
    # 注入路径不建——合成 per_code 无 df）；as-of 打分 (code,i) 缓存双窗
    # 各一、跨臂跨门共用（打分次数上限=母体大小）。
    bar_pools: dict[str, list] = {}
    score_caches: dict[str, dict] = {w: {} for w in per_code}
    entry_accounting: dict[str, dict[str, int]] = {
        w: {"requested": 0, "returned": 0} for w in per_code
    }

    def _rand_entries(n: int, w: str) -> list[dict]:
        entry_accounting[w]["requested"] += n
        if random_entry_fn is not None:
            out = random_entry_fn(n, w)
        else:
            if w not in bar_pools:
                bar_pools[w] = bear_bar_pool(per_code[w], regime_bear)
            out = random_entries(
                per_code[w],
                regime_bear,
                n,
                rng,
                index_df=index_df_ref["df"],
                pool=bar_pools[w],
                score_cache=score_caches[w],
            )
        entry_accounting[w]["returned"] += len(out)
        return out

    for _arm in range(args.n_random):
        arm_evaluated += 1

        def _judgment_reader(n_g: int, frozen_params: dict[str, Any]) -> Optional[dict]:
            """冻结配置（含胜出格 n_g）的判定窗读数（不重选——对等纪律 v0.302）。

            ⚠️ 冻结的是**出场配置与胜出格 n_g**；判定窗入场集 = 同分布重抽
            （随机入场无门信号可携带——对等的是「臂冻结配置 vs top 冻结配置」，
            入场随机性两臂同分布）。v2.1 参照 = 同批判定入场 × trail08。"""
            entries_j = _rand_entries(n_g, "judgment")
            ref_j = fes.combine_readings(
                _replay("judgment", "__random__", entries_j, ref_trail),
                args.top_n,
                ref="self",
            )
            trades = _replay("judgment", "__random__", entries_j, frozen_params)
            return fes.combine_readings(trades, args.top_n, ref=ref_j)

        arm = run_arm(
            counts_m,
            exits,
            _rand_entries,
            lambda subset, params: _replay("mining", "__random__", subset, params),
            args.top_n,
            judgment_reader=_judgment_reader,
        )
        if arm["mining"] is not None:
            arm_gate_pass += 1
            pool_m.append(arm["mining"]["readings"]["margin"])
            jd = arm.get("judgment")
            if jd and jd.get("margin") is not None:
                pool_j.append(jd["margin"])

    q95_m = _q95(pool_m)
    q95_j = _q95(pool_j)

    c1 = judge_c1(top["mining"] if top else None, top["judgment"] if top else None)
    c2 = judge_c2(
        top["mining"] if top else None,
        top["judgment"] if top else None,
        q95_m,
        q95_j,
    )

    # ── C3：top 出场参数 ±50%×4 零翻转（池固定不重估）；top 出场无可扰
    # 参数轴（base_low params={}）⇒ not_applicable 不空转（v0.307）──
    c3_rule = f"出场参数 ±50% ×{C3_DRAWS} 扰动 C2 结论零翻转（池固定）"
    c3_draws: list[dict] = []
    top_params = next(
        (e["params"] for e in exits if top and e["name"] == top["exit"]), None
    )
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
                rd_m = fes.combine_readings(
                    _replay(
                        "mining",
                        top["gate"],
                        gate_sigs["mining"][top["gate"]],
                        params_p,
                    ),
                    args.top_n,
                    ref=refs[(top["gate"], "mining")],  # v2.1 同参照口径对称
                )
                rd_j = fes.combine_readings(
                    _replay(
                        "judgment",
                        top["gate"],
                        gate_sigs["judgment"][top["gate"]],
                        params_p,
                    ),
                    args.top_n,
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

    c4_state = (
        "provisional"
        if len(pool_m) < args.c4_min_pool
        else (
            "confirmed_pass"
            if top
            and top["mining"]["margin"] is not None
            and top["mining"]["margin"] > (q95_m or 0)
            else "confirmed_fail"
        )
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

    # ── 总结局（C1 不过 ⇒ untested 优先——样本不足≠否定证据；C2 三态：
    # 池未建=None ⇒ 既不 falsified 也不 candidate，落 provisional；C3
    # not_applicable（v0.307）= 灵敏度证据缺失 ⇒ provisional 不放行 candidate）──
    if not c1["ok"]:
        verdict = "untested"
    elif c2["ok"] is False or c3["ok"] is False or c4_state == "confirmed_fail":
        verdict = "falsified"
    elif c2["ok"] is True and c3["ok"] is True and c4_state == "confirmed_pass":
        verdict = "candidate"
    else:
        verdict = "provisional"

    # 判定窗使用台账（v0.321，owner 方法论 review #1）：本报告=该窗第 k 次被读
    _wu_k = wu.record_use("R40", "judgment", args.tag, "C1~C4 判定窗读数")

    return {
        "schema": "bear_regime_report/v1",
        "tag": args.tag,
        "verdict": verdict,
        "window_usage": {
            "window": "judgment",
            "k": _wu_k,
            "note": wu.usage_note("R40", "judgment", _wu_k),
        },
        "gates": gates,
        "exit_grid": [e["name"] for e in exits],
        "windows": {w: {"start": se[0], "end": se[1]} for w, se in windows.items()},
        "amv_mode": "bearish（invert_regime_bearish：仅空头日放行，无映射不放行）",
        "n_signals_by_gate": {
            w: {g: len(gate_sigs[w][g]) for g in gates} for w in windows
        },
        "top": top,
        "configs": configs,
        "pools": {
            "mining": pool_m,
            "judgment": pool_j,
            "q95_mining": q95_m,
            "q95_judgment": q95_j,
            "entry_accounting": entry_accounting,  # n_requested/n_returned（打分失败跳过照实记）
            "score_cache_size": {w: len(c) for w, c in score_caches.items()},
            "arm_construction": "对等纪律（v0.302）+ 预算对等（v0.307）："
            "每臂完整复刻 top 的 46 门 × 5 档 max-of-230 选型（逐门 n_g "
            "对齐抽样，0 信号门跳过），冻结（n_g, 出场配置）进判定窗，"
            "禁每窗重新取 max",
        },
        "criteria": {
            "C1": c1,
            "C2": c2,
            "C3": c3,
            "C4": c4,
            "rule_note": "R40-C1~C4 跑数前写死（v0.301/v0.302/v0.307 修订在案——"
            "v0.307：随机臂预算对等 max-of-230 镜像；C3 无可扰参数轴="
            "not_applicable 不空转不放行）；"
            "C5（pre2019 同段随机池标尺）= C2 过线才启动，owner 拍板发令",
        },
        "objective_version": sg.OBJECTIVE_VERSION,
        "notes": [_R11_R14_NOTE],
    }


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
    ap.add_argument("--count", type=int, default=2000, help="每股回溯 K 线根数")
    ap.add_argument("--cost-bps", type=float, default=25.0, help="往返成本基点")
    ap.add_argument("--mining-start", default="2022-01-01", help="挖掘窗起点")
    ap.add_argument("--mining-end", default="2024-07-31", help="挖掘窗终点")
    ap.add_argument("--judgment-start", default="2024-08-01", help="判定窗起点")
    ap.add_argument("--judgment-end", default="2026-09-04", help="判定窗终点")
    ap.add_argument(
        "--gates", default="all", help="逗号分隔门名（默认 all=全量 46 门 ENTRY_GATES）"
    )
    ap.add_argument("--n-random", type=int, default=DEFAULT_N_RANDOM, help="随机臂数")
    ap.add_argument("--c4-min-pool", type=int, default=C4_MIN_POOL, help="C4 最小池")
    ap.add_argument("--seed", type=int, default=20261008, help="全局种子（C3/C4 派生）")
    ap.add_argument(
        "--out-dir",
        default="artifacts/logs/bear_regime",
        help="产物根目录（默认 artifacts/logs/bear_regime）",
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = _build_parser()
    args = ap.parse_args(argv)
    fes._check_windows(args, ap)  # pre2019 交集/双窗次序硬拒绝（同族单源）
    # 加载到达校验（v0.316，R41 指导顺手补）：逐股 _load_one_bars 不经批量
    # 截断护栏——count 不够会把窗口静默剪空（r36_c5 碎片宇宙教训）
    from custos.research.exit_c5_terminal import check_reach  # noqa: PLC0415

    check_reach(args.count, args.mining_start)
    try:
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
