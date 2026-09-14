#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打分基因组裁决驱动（TODO #61 v1 试跑 / R34 预注册的执行载体）。

打分系统优化 = 进化「分轴集合 × 权重」的基因组，**增量在 LLM 造新轴**（四轮
证伪 R22/R24/R29/R30 都是旧轴调权）。复合打分编译成一条普通 DSL 表达式
（``score_genome.compile_composite``：每腿裹 TS_RANK 个股自身历史分位归一后
加权求和），于是每个基因组 = strategy_grid 的一个普通单元格（``expr:`` 形态
scorer），双窗/灵敏度/随机对照全部复用既有机制——本工具只有编排与判定。

流程（对照 R34 判据的对应关系写死在此）：

1. **输入腿集合**：``--legs-file``（表达式清单 JSON，或进化 trajectory_pool.json
   自动取 decision=pass 且 gate 非空者的 expression）或 ``--legs``（逗号分隔，
   括号深度感知）；``--max-legs``（默认 6）防组合爆炸。
2. **评估执行**：权重格（``score_genome.weight_lattice``，≤--max-combos）每个
   权重元组 = 一次 strategy_grid 单元格（cell_runner 可注入/monkeypatch，默认
   实现仿 evolution_loop._make_cell_runner：subprocess 调 backtest_factors
   --trade-sim --portfolio，scorer=expr:复合式，gate=--gate（默认 j_low），
   出场=DEFAULT_EXIT_GRID 中档（pct5_trail08），--top-n 20 选择压力，
   0AMV 做多基底钉死）。对照臂：**等权复合 / 各单腿**（恒在格子保底集里，
   按名字从格子结果取，零额外预算）/ **随机腿复合**（random_expr 同腿数采样
   × 同权重格同待遇，--n-random 条，--random-seed）/ **s_shape**（注册表现成
   参照）。**V0 臂本轮不落地**：V0=live 技术分由 enrich factor_contrib 重建
   （score_variants_study 口径），非 DSL 可表达，调入 scorer 超 v1 一小时口径
   ——报告里 ``arms.v0`` 标 deferred 留待下轮。
3. **适应度与分层语义**：读格子的 margin/expectancy_R/盈亏比/胜率/笔数（交易层
   读数；R11：只作相对排序，量级不引用）。**「A 层 vs 其余」的操作化 = --top-n 20
   选中子集 vs 全池基线的对照**：scorer 只排序，top_n 把排序截成「A 层」（选中
   子集），margin 即「A 层胜率 − 盈亏平衡胜率」相对全池口径的读数——这就是
   R34-C2「相对基准 margin 方向」的度量载体（预注册页按此口径披露）。
4. **闸门（全部确定性）**：① 双窗——挖掘窗寻优，--judgment-start/end 给出时
   top/等权/s_shape 三臂判定窗独立复测，窗重叠/倒挂拒跑；② 灵敏度——top
   基因组权重 ±--sens-pct（默认 0.5）扰动 --sens-arms（默认 4）臂重跑挖掘窗，
   方向翻转计数（R29 零翻转纪律：扰动臂 objective 跌破等权基准 = 翻转；
   读数缺失按翻转计，保守）；③ 随机对照——top 基因组 objective 必须打过
   随机臂最高分（#71 纪律，否则标「筛选假象嫌疑」）；④ pre2019 untouched
   终审段（2010-2016）与任何窗口相交直接拒跑（口径同 evolution_loop；
   终审本身不在本工具跑，留给生产机单独终步，R34-C5）。

产物：``{out_dir}/{tag}/_score_evolution__{tag}.json``（schema 由
tests/test_score_evolution_study.py 钉住）+ stdout 汇总表。
空数据 / 0 合法腿 / 全部格子失败 → 非零退出不落盘（空产物会被误读成
「基因组全灭」）。

用法（生产机；本机无通达信数据只能跑测试）::

    uv run python -m custos.research score_evolution_study \
        --legs "MA(close,20)/close,volume/MA(volume,20)" \
        --mining-start 2022-01-01 --mining-end 2024-07-31 \
        --judgment-start 2024-08-01 --judgment-end 2026-09-04 \
        --codes-file artifacts/logs/r34_codes_s3000_seed0.txt --tag r34_smoke
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

# GBK（cp936）终端/管道打不了 ⚠️/⛔ 等符号 —— 不 reconfigure 会 UnicodeEncodeError。
# 惯例同 evolution_loop / random_baseline_study（hasattr 守卫：pytest 捕获替换过）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from custos.core.paths import LOGS  # noqa: E402
from custos.research import backtest_factors as bt  # noqa: E402
from custos.research.evolution.dual_window import Window, validate_windows  # noqa: E402
from custos.research.evolution.expr_dsl import ExprError, parse  # noqa: E402
from custos.research.evolution.random_expr import sample_expression  # noqa: E402
from custos.research.evolution.score_genome import (  # noqa: E402
    DEFAULT_LEVELS,
    baseline_arms,
    compile_composite,
    perturb_weights,
    weight_lattice,
)
from custos.research.evolution_loop import (  # noqa: E402  # pre2019 段单源（终审段口径只有这一处）
    PRE2019_END,
    PRE2019_START,
    _overlaps_pre2019,
)

OUTDIR = LOGS / "score_evolution"

SCHEMA_VERSION = 1
DEFAULT_SEED = 20260914  # 灵敏度扰动种子
DEFAULT_RANDOM_SEED = 20260915  # 随机臂采样种子（与灵敏度种子分开，独立可复现）
DEFAULT_UNIVERSE_SEED = 42  # 宇宙抽样种子（同 random_baseline_study 口径）

CellRunner = Callable[..., Optional[dict]]  # (scorer, gate, exit_params, *, start, end)


def _build_parser() -> argparse.ArgumentParser:
    """全部 CLI 参数定义（返回未解析的 parser）。

    ⚠️ add_argument 定义必须留在**本文件**内：`research/__main__.py._modes()`
    用 AST 解析本文件找 store_true 开关生成模式清单。互斥/必填校验在 main 里
    parse 之后做（fail-closed）。
    """
    ap = argparse.ArgumentParser(
        description="打分基因组裁决驱动（TODO #61 / R34；复合打分=DSL 表达式，复用 strategy_grid 单元格机制）"
    )
    # ---- 腿集合输入（互斥，恰一）----
    ap.add_argument(
        "--legs",
        default="",
        help="逗号分隔的 DSL 腿表达式（括号深度感知：MA(close,20) 内的逗号不切）",
    )
    ap.add_argument(
        "--legs-file",
        default="",
        help="腿集合 JSON：表达式清单，或进化 trajectory_pool.json"
        "（自动取 decision=pass 且 gate 非空者）",
    )
    ap.add_argument(
        "--max-legs", type=int, default=6, help="腿数上限（默认 6，防组合爆炸）"
    )
    # ---- 基因组编译 ----
    ap.add_argument(
        "--rank-window",
        type=int,
        default=250,
        help="TS_RANK 归一窗 K（默认 250 = MAX_WINDOW）",
    )
    ap.add_argument(
        "--lattice-levels",
        default=",".join(str(x) for x in DEFAULT_LEVELS),
        help="权重格档位（逗号分隔非负整数，默认 0,1,2,3）",
    )
    ap.add_argument(
        "--max-combos",
        type=int,
        default=64,
        help="权重格组合数上限（默认 64；截断保含单腿/等权基线）",
    )
    # ---- 单元格口径 ----
    ap.add_argument(
        "--gate", default="j_low", help="entry gate（默认 j_low = 0AMV∧J<13 基底）"
    )
    ap.add_argument(
        "--exit-name",
        default="",
        help="出场档名（默认 DEFAULT_EXIT_GRID 中档 pct5_trail08；全集见 strategy_grid）",
    )
    ap.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="横截面择优（默认 20 = 选择压力/A 层容量）",
    )
    ap.add_argument(
        "--count",
        type=int,
        default=2000,
        help="每股回溯 K 线根数（默认 2000 防尾部截断）",
    )
    ap.add_argument(
        "--cost-bps", type=float, default=25.0, help="往返成本基点（默认 25）"
    )
    # ---- 窗口 ----
    ap.add_argument(
        "--mining-start", required=True, help="挖掘窗起点 YYYY-MM-DD（必填）"
    )
    ap.add_argument("--mining-end", required=True, help="挖掘窗终点 YYYY-MM-DD（必填）")
    ap.add_argument(
        "--judgment-start", default="", help="判定窗起点（与 --judgment-end 同给）"
    )
    ap.add_argument(
        "--judgment-end", default="", help="判定窗终点（与 --judgment-start 同给）"
    )
    # ---- 宇宙 ----
    ap.add_argument("--codes", default="", help="逗号分隔代码（宇宙解析的兜底分支）")
    ap.add_argument("--codes-file", default="", help="从文件读代码（钉死宇宙，优先）")
    ap.add_argument(
        "--universe-sample",
        type=int,
        default=0,
        help="全市场抽样 N 只（默认 0=不抽；与 --codes/--codes-file 三选一）",
    )
    ap.add_argument(
        "--universe-local",
        action="store_true",
        help="用本地 vipdoc 代码清单作抽样母体（同 backtest_factors）",
    )
    ap.add_argument(
        "--universe-seed",
        type=int,
        default=DEFAULT_UNIVERSE_SEED,
        help="宇宙抽样种子（默认 42）",
    )
    # ---- 闸门参数 ----
    ap.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="灵敏度扰动种子（可复现）"
    )
    ap.add_argument(
        "--random-seed", type=int, default=DEFAULT_RANDOM_SEED, help="随机臂采样种子"
    )
    ap.add_argument(
        "--n-random", type=int, default=3, help="随机臂条数（默认 3；同腿数同格同待遇）"
    )
    ap.add_argument("--sens-arms", type=int, default=4, help="灵敏度扰动臂数（默认 4）")
    ap.add_argument(
        "--sens-pct", type=float, default=0.5, help="灵敏度扰动幅度（默认 0.5 = ±50%%）"
    )
    # ---- 运行控制 ----
    ap.add_argument(
        "--timeout", type=int, default=0, help="单格超时秒（0=strategy_grid 默认 1800）"
    )
    ap.add_argument("--tag", default="", help="运行标识（默认时间戳）；产物目录名")
    ap.add_argument(
        "--out-dir", default="", help=f"产物根目录（默认 {OUTDIR}），tag 作子目录"
    )
    return ap


# ---------------------------------------------------------------------------
# 输入解析与校验（全部 fail-closed）
# ---------------------------------------------------------------------------


def _split_legs(text: str) -> list[str]:
    """--legs 的逗号分隔：**括号深度感知**——DSL 表达式自身含逗号（MA(close,20)），
    只在深度 0 的逗号处切；深度为负（括号不配对）留给 parse 拒。"""
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _legs_from_file(path: str, ap: argparse.ArgumentParser) -> tuple[list[str], str]:
    """读 --legs-file：表达式清单，或 trajectory_pool.json（pass 且 gate 非空者）。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        ap.error(f"--legs-file 不可解析: {path}（{type(exc).__name__}: {exc}）")
    if isinstance(payload, list):
        return payload, "expression_list"
    if isinstance(payload, dict) and isinstance(payload.get("trajectories"), list):
        legs = [
            t["expression"]
            for t in payload["trajectories"]
            if isinstance(t, dict)
            and t.get("decision") == "pass"
            and t.get("gate")
            and isinstance(t.get("expression"), str)
        ]
        return legs, "trajectory_pool(pass∧gate非空)"
    ap.error("--legs-file 形态非法：须为表达式清单 JSON 或 trajectory_pool.json")
    raise AssertionError("ap.error 不返回")  # pragma: no cover


def _resolve_legs(args: Any, ap: argparse.ArgumentParser) -> tuple[list[str], str]:
    """互斥解析 + 逐条 parse 校验 + 去重（保序）+ 腿数上限。0 合法腿 → ap.error。"""
    if bool(args.legs) == bool(args.legs_file):
        ap.error("--legs 与 --legs-file 必须恰给一个")
    if args.legs:
        legs, source = _split_legs(args.legs), "--legs(内联)"
    else:
        legs, source = _legs_from_file(args.legs_file, ap)
    bad = []
    for i, leg in enumerate(legs):
        if not isinstance(leg, str) or not leg.strip():
            bad.append(f"legs[{i}] 非字符串/空: {leg!r}")
            continue
        try:
            parse(leg)
        except ExprError as exc:
            bad.append(f"legs[{i}] 非法: {leg!r}（{exc}）")
    if bad:
        ap.error("腿表达式校验失败（fail-closed，不部分受理）：\n  " + "\n  ".join(bad))
    # 去重（空白归一后按字符串判等，保序）——轨迹池跨 run 合并常有重复表达式
    seen, uniq = set(), []
    for leg in legs:
        key = "".join(leg.split())
        if key not in seen:
            seen.add(key)
            uniq.append(leg.strip())
    if not uniq:
        ap.error(f"0 合法腿（来源 {source}）——空腿集合的裁决产物只会被误读，拒跑")
    if len(uniq) > args.max_legs:
        ap.error(
            f"腿数 {len(uniq)} 超 --max-legs {args.max_legs}（组合爆炸护栏；"
            "确要更多请先想清多重比较税）"
        )
    return uniq, source


def _validate_windows(args: Any, ap: argparse.ArgumentParser) -> tuple[Window, Any]:
    """双窗校验 + pre2019 硬拒绝（mining 必填；judgment 同给或同缺）。"""
    if bool(args.judgment_start) != bool(args.judgment_end):
        ap.error("--judgment-start 与 --judgment-end 必须同时给出")
    mining = Window(args.mining_start, args.mining_end)
    judgment = (
        Window(args.judgment_start, args.judgment_end) if args.judgment_start else None
    )
    try:
        if judgment is None:
            _validate_single_window(mining)  # 单窗：格式+倒挂
        else:
            validate_windows(mining, judgment)
    except ValueError as exc:
        ap.error(str(exc))
    for name, w in (("挖掘窗", mining), ("判定窗", judgment)):
        if w is not None and _overlaps_pre2019(w.start, w.end):
            ap.error(
                f"⛔ 反过拟合纪律：打分进化挖掘/判定不许碰 pre2019 untouched 终审段"
                f"（{PRE2019_START}..{PRE2019_END}）——{name} {w.start}..{w.end} 与之相交"
            )
    return mining, judgment


def _validate_single_window(w: Window) -> None:
    """单窗校验：日期格式 + 起止不倒挂（无第二窗，不做重叠判定）。"""
    from datetime import date  # noqa: PLC0415

    try:
        s, e = date.fromisoformat(w.start), date.fromisoformat(w.end)
    except ValueError as exc:
        raise ValueError(f"窗口日期须为 YYYY-MM-DD: {w}") from exc
    if s > e:
        raise ValueError(f"窗口起止倒挂: start={w.start} > end={w.end}")


def _resolve_exit(args: Any, ap: argparse.ArgumentParser) -> dict:
    """出场档：默认 DEFAULT_EXIT_GRID 中档；--exit-name 显式给出时查名。"""
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    grid = sg.DEFAULT_EXIT_GRID
    if not args.exit_name:
        return copy_of(grid[len(grid) // 2])
    for e in grid:
        if e["name"] == args.exit_name:
            return copy_of(e)
    ap.error(
        f"--exit-name 未知: {args.exit_name}（DEFAULT_EXIT_GRID: "
        f"{[e['name'] for e in grid]}）"
    )
    raise AssertionError("ap.error 不返回")  # pragma: no cover


def copy_of(e: dict) -> dict:
    return {"name": e["name"], "params": dict(e.get("params") or {})}


def _validate_gate(args: Any, ap: argparse.ArgumentParser) -> None:
    """gate 查名——打错的 gate 不该烧完整个格子才暴毙。"""
    if args.gate not in bt.ENTRY_GATES:
        ap.error(
            f"--gate 未注册: {args.gate}（ENTRY_GATES 共 {len(bt.ENTRY_GATES)} 个，"
            "全集见 backtest_factors）"
        )


def _resolve_universe(args: Any, ap: argparse.ArgumentParser) -> list[str]:
    ns = argparse.Namespace(
        codes_file=args.codes_file,
        universe_local=args.universe_local,
        universe_sample=args.universe_sample,
        seed=args.universe_seed,
        codes=args.codes,
    )
    return bt._resolve_universe(ns, ap)


# ---------------------------------------------------------------------------
# cell_runner（默认实现仿 evolution_loop._make_cell_runner；测试注入 fake）
# ---------------------------------------------------------------------------


def _reading_of(sg: Any, row: dict) -> dict:
    """格子结果行 → 本工具的读数块（键名固定，测试钉住）。"""
    return {
        "objective": sg.objective_of(row, sg.DEFAULT_OBJ_WEIGHTS),
        "margin": row.get("margin"),
        "expectancy_R": row.get("expectancy_R"),
        "payoff_ratio": row.get("payoff_ratio"),
        "win_rate": row.get("win_rate"),
        "n": row.get("n"),
        "cell_signature": _row_signature(row),
    }


def _row_signature(row: dict) -> Optional[str]:
    """从结果文件名尾段还原 cell_signature（``<名>__<sig>.json``）。"""
    name = str(row.get("result_file", ""))
    if "__" not in name:
        return None
    return Path(name).stem.rsplit("__", 1)[-1]


def _make_cell_runner(args: Any, codes_file: str, cells_dir: Path) -> CellRunner:
    """构造默认 cell_runner：每格 = 一次 backtest_factors --trade-sim 子进程。

    复用 strategy_grid 的 run_cell 机制（cell_signature 复用跳过免费获得）；
    子进程自行按给定窗口加载数据（进程级隔离，双窗无共享内存对象）。
    """
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    cells_dir.mkdir(parents=True, exist_ok=True)

    def cell_runner(
        scorer: str, gate: str, exit_params: dict, *, start: str, end: str
    ) -> Optional[dict]:
        ns = argparse.Namespace(  # strategy_grid._cell_args/run_cell 的最小面
            cost_bps=args.cost_bps,
            no_amv_pin=False,  # 0AMV 研究基底钉死（口径同 strategy_grid 默认）
            codes_file=codes_file,
            sample=0,
            start=start,
            end=end,
            count=args.count,
            top_n=args.top_n,
            force=False,
            timeout=args.timeout or sg.CELL_TIMEOUT_S,
            universe_digest="",
        )
        cell = {
            "scorer": scorer,
            "gate": gate,
            "exit": "score_evolution",
            "params": dict(exit_params),
        }
        status, path, _log = sg.run_cell(ns, cell, cells_dir)
        if status == "failed" or path is None:
            return None
        return _reading_of(
            sg, sg.load_cell_row(cell, path, reused=(status == "reused"))
        )

    return cell_runner


# ---------------------------------------------------------------------------
# 评估编排
# ---------------------------------------------------------------------------


def _eval_lattice(
    runner: CellRunner,
    legs: Sequence[str],
    lattice: Sequence[tuple[float, ...]],
    gate: str,
    exit_params: dict,
    rank_window: int,
    window: Window,
) -> list[dict]:
    """权重格逐组编译 + 评估（挖掘窗）；编译失败/格子失败 → reading=None 留痕。"""
    rows: list[dict[str, Any]] = []
    for w in lattice:
        try:
            expr = compile_composite(list(legs), list(w), rank_window=rank_window)
        except ValueError as exc:  # 复合越界（如 symbol_len）：该组留痕不评
            rows.append(
                {
                    "weights": list(w),
                    "expr": None,
                    "compile_error": str(exc),
                    "reading": None,
                }
            )
            continue
        reading = runner(
            f"expr:{expr}", gate, exit_params, start=window.start, end=window.end
        )
        rows.append({"weights": list(w), "expr": expr, "reading": reading})
    return rows


def _best(rows: list[dict]) -> Optional[dict]:
    """objective 最高的行（None 读数/None objective 不参与；并列取格子序在前者）。"""
    ok = [
        r
        for r in rows
        if r.get("reading") and r["reading"].get("objective") is not None
    ]
    if not ok:
        return None
    return max(ok, key=lambda r: r["reading"]["objective"])


def _find_by_weights(rows: list[dict], weights: Sequence[float]) -> Optional[dict]:
    wl = [float(x) for x in weights]
    for r in rows:
        if r["weights"] == wl:
            return r
    return None


def _run_study(
    args: Any,
    legs: list[str],
    legs_source: str,
    mining: Window,
    judgment: Optional[Window],
    exit_spec: dict,
    codes: list[str],
    runner: CellRunner,
) -> Optional[dict]:
    """评估编排主体；全部格子失败 → None（调用方非零退出不落盘）。"""
    levels = tuple(float(x) for x in args.lattice_levels)
    lattice = weight_lattice(len(legs), levels, max_combos=args.max_combos)
    exit_params = dict(exit_spec["params"])

    mining_rows = _eval_lattice(
        runner, legs, lattice, args.gate, exit_params, args.rank_window, mining
    )
    top = _best(mining_rows)
    if top is None:
        print(
            "[ERR] 全部格子失败或读数缺失（数据源/窗口有问题？）——"
            "空产物会被误读成「基因组全灭」，拒绝落盘",
            file=sys.stderr,
        )
        return None

    arms_ref = baseline_arms(len(legs))
    equal_row = _find_by_weights(mining_rows, arms_ref["equal"])
    single_rows = [
        (name, _find_by_weights(mining_rows, w))
        for name, w in arms_ref.items()
        if name != "equal"
    ]
    s_shape_reading = runner(
        "s_shape", args.gate, exit_params, start=mining.start, end=mining.end
    )

    # 随机对照臂：同腿数随机表达式 × 同权重格同待遇（#71 纪律）
    rng = random.Random(args.random_seed)
    random_arms: list[dict[str, Any]] = []
    for _ in range(args.n_random):
        rlegs = [sample_expression(rng) for _ in range(len(legs))]
        rrows = _eval_lattice(
            runner, rlegs, lattice, args.gate, exit_params, args.rank_window, mining
        )
        rbest = _best(rrows)
        random_arms.append(
            {
                "legs": rlegs,
                "n_cells": len(rrows),
                "best": (
                    {"weights": rbest["weights"], "reading": rbest["reading"]}
                    if rbest
                    else None
                ),
                "cells": rrows,
            }
        )
    random_best_obj = max(
        (
            a["best"]["reading"]["objective"]
            for a in random_arms
            if a["best"] is not None
        ),
        default=None,
    )

    # 判定窗：top / 等权 / s_shape 三臂独立复测（子进程自行加载判定窗数据）
    judgment_block: Optional[dict[str, Any]] = None
    if judgment is not None:
        judgment_block = {
            "top": runner(
                f"expr:{top['expr']}",
                args.gate,
                exit_params,
                start=judgment.start,
                end=judgment.end,
            ),
            "equal": (
                runner(
                    f"expr:{equal_row['expr']}",
                    args.gate,
                    exit_params,
                    start=judgment.start,
                    end=judgment.end,
                )
                if equal_row and equal_row.get("expr")
                else None
            ),
            "s_shape": runner(
                "s_shape",
                args.gate,
                exit_params,
                start=judgment.start,
                end=judgment.end,
            ),
        }

    # 灵敏度：top 权重 ±pct 扰动重跑挖掘窗；翻转 = 扰动臂 objective 跌破等权基准
    base_obj = (
        equal_row["reading"].get("objective")
        if equal_row and equal_row.get("reading")
        else None
    )
    top_obj = top["reading"]["objective"]
    srng = random.Random(args.seed)
    sens_rows, flips = [], 0
    for _ in range(args.sens_arms):
        pw = perturb_weights(top["weights"], pct=args.sens_pct, rng=srng)
        try:
            pexpr = compile_composite(legs, list(pw), rank_window=args.rank_window)
            preading = runner(
                f"expr:{pexpr}",
                args.gate,
                exit_params,
                start=mining.start,
                end=mining.end,
            )
        except ValueError:
            pexpr, preading = None, None
        # top 必 ≥ 基准（等权在格子内、top 是 argmax）⇒ 翻转 = 扰动臂 < 基准；
        # 读数缺失按翻转计（保守）；基准自身缺失时翻转不可判 ⇒ 不计（留痕）。
        pobj = preading.get("objective") if preading else None
        flip = (pobj is None) if base_obj is None else (pobj is None or pobj < base_obj)
        flips += int(flip)
        sens_rows.append(
            {
                "weights": [round(x, 6) for x in pw],
                "expr": pexpr,
                "reading": preading,
                "flip": flip,
            }
        )

    # 随机对照判定（#71：top 必须打过随机臂最高分，否则「筛选假象嫌疑」）
    if random_best_obj is None:
        verdict = "indeterminate"  # 随机臂全灭（读不出）——无法裁决，留痕
    else:
        verdict = "pass" if top_obj > random_best_obj else "suspect"

    # R34 判据读数（判定本身按预注册页执行；这里只出机械读数）
    top_margin = top["reading"].get("margin")
    equal_margin = (
        equal_row["reading"].get("margin")
        if equal_row and equal_row.get("reading")
        else None
    )
    d_mining = (
        (top_margin - equal_margin)
        if top_margin is not None and equal_margin is not None
        else None
    )
    j_top_margin = (judgment_block or {}).get("top", {}) or {}
    j_equal_margin = (judgment_block or {}).get("equal", {}) or {}
    d_judgment = None
    if judgment_block is not None:
        jm, em = j_top_margin.get("margin"), j_equal_margin.get("margin")
        d_judgment = (jm - em) if jm is not None and em is not None else None
    criteria = {
        "R34-C1": {
            "rule": "单窗单格笔数 ≥100",
            "top_n_mining": top["reading"].get("n"),
            "top_n_judgment": j_top_margin.get("n") if judgment_block else None,
            "threshold": 100,
        },
        "R34-C2": {
            "rule": "top 基因组相对等权基准 margin > 0 且双窗同向",
            "delta_mining": d_mining,
            "delta_judgment": d_judgment,
            "judgment_window": judgment is not None,
        },
        "R34-C3": {
            "rule": f"灵敏度 ±{args.sens_pct:.0%} 零翻转",
            "arms": args.sens_arms,
            "flips": flips,
        },
        "R34-C4": {
            "rule": "top 基因组 objective > 随机臂最高分（#71 纪律）",
            "top_objective": top_obj,
            "random_best_objective": random_best_obj,
            "verdict": verdict,
        },
        "R34-C5": {
            "rule": "pre2019 untouched 终审段单独终步（一票否决）",
            "status": "not_run",
            "note": "本工具窗口与 pre2019 段相交即拒跑；终审留生产机单独终步",
        },
    }

    return {
        "version": SCHEMA_VERSION,
        "tag": args.tag,
        "config": {
            "legs_source": legs_source,
            "max_legs": args.max_legs,
            "rank_window": args.rank_window,
            "lattice_levels": list(levels),
            "max_combos": args.max_combos,
            "gate": args.gate,
            "exit": exit_spec,
            "top_n": args.top_n,
            "count": args.count,
            "cost_bps": args.cost_bps,
            "seed": args.seed,
            "random_seed": args.random_seed,
            "n_random": args.n_random,
            "sens_arms": args.sens_arms,
            "sens_pct": args.sens_pct,
        },
        "windows": {
            "mining": {"start": mining.start, "end": mining.end},
            "judgment": (
                {"start": judgment.start, "end": judgment.end} if judgment else None
            ),
        },
        "universe": {
            "n_codes": len(codes),
            "digest": hashlib.sha1(",".join(codes).encode("utf-8")).hexdigest()[:12],
            "source": _universe_source(args),
        },
        "legs": legs,
        "lattice": {
            "n_legs": len(legs),
            "n_combos": len(lattice),
            "weights": [list(w) for w in lattice],
        },
        "arms": {
            "lattice": mining_rows,
            "equal_weight": equal_row,
            "single_legs": [{"name": n, "row": r} for n, r in single_rows],
            "s_shape": {"reading": s_shape_reading},
            "random": random_arms,
            "v0": {
                "status": "deferred",
                "reason": "V0=live 技术分由 enrich factor_contrib 重建"
                "（score_variants_study 口径），非 DSL 可表达；调入 scorer 超 v1 "
                "一小时口径，留待下轮（本报告以 s_shape 注册表现成参照替代）",
            },
        },
        "top_genome": {
            "weights": top["weights"],
            "expr": top["expr"],
            "mining": top["reading"],
            "judgment": judgment_block["top"] if judgment_block else None,
        },
        "judgment": judgment_block,
        "sensitivity": {
            "pct": args.sens_pct,
            "n_arms": args.sens_arms,
            "baseline_objective": base_obj,
            "arms": sens_rows,
            "flips": flips,
        },
        "random_control": {
            "top_objective": top_obj,
            "random_best_objective": random_best_obj,
            "verdict": verdict,
        },
        "criteria_readings": criteria,
    }


def _universe_source(args: Any) -> str:
    if args.codes_file:
        return f"codes_file({Path(args.codes_file).name})"
    if args.codes:
        return "codes(内联)"
    src = "local_vipdoc" if args.universe_local else "online_get_stock_list"
    return f"{src} sample={args.universe_sample} seed={args.universe_seed}"


# ---------------------------------------------------------------------------
# stdout 汇总
# ---------------------------------------------------------------------------


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "-"
    return f"{v:+.1%}" if pct else f"{v:+.4f}"


def _arm_line(name: str, reading: Optional[dict]) -> str:
    if not reading:
        return f"  {name:<22} （读数缺失）"
    return (
        f"  {name:<22} objective={_fmt(reading.get('objective'))} "
        f"margin={_fmt(reading.get('margin'))} "
        f"wr={_fmt(reading.get('win_rate'), pct=True)} "
        f"payoff={_fmt(reading.get('payoff_ratio'))} n={reading.get('n', '-')}"
    )


def _print_summary(rep: dict[str, Any]) -> None:
    w = rep["windows"]
    print(
        f"\n[R34] 打分基因组裁决｜挖掘窗 {w['mining']['start']}~{w['mining']['end']}"
        + (
            f"｜判定窗 {w['judgment']['start']}~{w['judgment']['end']}"
            if w["judgment"]
            else ""
        )
        + f"｜宇宙 {rep['universe']['n_codes']} 只 digest={rep['universe']['digest']}"
    )
    print(f"  腿({rep['lattice']['n_legs']}): " + " / ".join(rep["legs"]))
    print(
        f"  权重格 {rep['lattice']['n_combos']} 组合（max {rep['config']['max_combos']}）"
        f"｜gate={rep['config']['gate']}｜出场={rep['config']['exit']['name']}"
        f"｜top_n={rep['config']['top_n']}"
    )
    top = rep["top_genome"]
    print(f"  ─ top 基因组 w={top['weights']}")
    print(_arm_line("top·挖掘", top["mining"]))
    eq = rep["arms"]["equal_weight"]
    print(_arm_line("等权基准·挖掘", eq["reading"] if eq else None))
    for s in rep["arms"]["single_legs"]:
        row = s["row"]
        print(_arm_line(f"{s['name']}·挖掘", row["reading"] if row else None))
    print(_arm_line("s_shape 参照·挖掘", rep["arms"]["s_shape"]["reading"]))
    rc = rep["random_control"]
    mark = {
        "pass": "✅ 打过",
        "suspect": "⚠️ 筛选假象嫌疑",
        "indeterminate": "⚠️ 随机臂读不出",
    }[rc["verdict"]]
    print(
        _arm_line("随机臂最佳·挖掘", {"objective": rc["random_best_objective"]})
        + f" ⇒ {mark}"
    )
    sens = rep["sensitivity"]
    print(
        f"  灵敏度 ±{sens['pct']:.0%} ×{sens['n_arms']}：翻转 {sens['flips']} "
        + ("✅" if sens["flips"] == 0 else "⚠️")
    )
    if rep["judgment"]:
        print(_arm_line("top·判定窗", rep["judgment"]["top"]))
        print(_arm_line("等权基准·判定窗", rep["judgment"]["equal"]))
        c2 = rep["criteria_readings"]["R34-C2"]
        print(
            f"  Δmargin（top−等权）：挖掘 {_fmt(c2['delta_mining'])}"
            f"｜判定 {_fmt(c2['delta_judgment'])}"
        )
    print("  V0 臂：deferred（V0 非 DSL 可表达，留待下轮；本轮以 s_shape 参照）")


def main(
    argv: Optional[list[str]] = None, *, cell_runner: Optional[CellRunner] = None
) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    levels_raw = args.lattice_levels
    try:
        args.lattice_levels = tuple(
            float(x) for x in str(levels_raw).split(",") if str(x).strip()
        )
        weight_lattice(1, args.lattice_levels)  # 形态预检（整数刻度/非负/非全零）
    except ValueError as exc:
        ap.error(f"--lattice-levels 形态非法: {levels_raw!r}（{exc}）")
    legs, legs_source = _resolve_legs(args, ap)
    mining, judgment = _validate_windows(args, ap)
    exit_spec = _resolve_exit(args, ap)
    _validate_gate(args, ap)
    codes = _resolve_universe(args, ap)

    tag = args.tag or _now_tag()
    args.tag = tag
    out_dir = (Path(args.out_dir) if args.out_dir else OUTDIR) / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    # 宇宙钉死：落一份 codes 表供单元格子进程复用（同 evolution_loop 的 joint 口径）
    codes_path = out_dir / f"_codes__{tag}.txt"
    codes_path.write_text("\n".join(codes) + "\n", encoding="utf-8")

    runner = cell_runner or _make_cell_runner(
        args, str(codes_path), out_dir / "grid_cells"
    )
    rep = _run_study(
        args, legs, legs_source, mining, judgment, exit_spec, codes, runner
    )
    if rep is None:
        return 2  # 全格失败护栏（不落盘）

    out = out_dir / f"_score_evolution__{tag}.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=2, allow_nan=True)
    _print_summary(rep)
    print(f"\n[INFO] 汇总 → {out}")
    return 0


def _now_tag() -> str:
    from datetime import datetime  # noqa: PLC0415

    return datetime.now().strftime("%Y%m%d_%H%M%S")


if __name__ == "__main__":
    raise SystemExit(main())
