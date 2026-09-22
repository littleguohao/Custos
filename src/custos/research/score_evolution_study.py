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

**--v0-lattice 模式（R36 Phase 3 调权路）**：腿轴固定为 live V0 计分键
（``score_calibration_study.CONTRIB_LEG_KEYS`` 权威清单），格子定义在**倍率
空间**（每腿 multiplier ∈ --lattice-levels，overrides = DEFAULT_TECH_WEIGHTS
× 倍率——V0 有负腿，倍率缩放罚分幅度、永不变号；repair_signals 是 each/cap
合成腿，倍率同乘双键）。与 DSL 模式的工程差别：collect/score 拆分——
``evaluate_trades(collect_all)`` + ``asof_candidate`` 每窗只跑一遍（与权重无关，
按窗缓存），逐格只做 ``technical_score`` 重打分 + ``simulate_portfolio_topn``
+ ``summarize/objective``（64 格 × 2 窗不再慢 64 倍）。读数口径：margin/胜率/
盈亏比/expectancy_R/n 取 **top_n 选中子集**（A层）——全候选池读数是权重
不变量（打分只排序不过滤），收 ``pool_baseline`` 审计块。闸门同族：双窗/
灵敏度（扰动倍率向量）/随机对照（**仍走 DSL 随机臂**，R34 标尺口径：腿数
= --max-legs；V0 30 腿 DSL 复合超 expr_dsl AST 节点上限不可表达，随机化
V0 腿不是本模式口径）/pre2019 拒跑。与 --legs/--legs-file/--two-stage/
--v0-arm 互斥。

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
import math
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
from custos.pipeline.screening import score_candidates as sc  # noqa: E402
from custos.research import backtest_factors as bt  # noqa: E402
from custos.research.evolution.dual_window import Window, validate_windows  # noqa: E402
from custos.research.evolution.expr_dsl import ExprError, parse  # noqa: E402
from custos.research.evolution.random_expr import sample_expression  # noqa: E402
from custos.research.evolution.score_genome import (  # noqa: E402
    DEFAULT_LEVELS,
    _fmt_weight,
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
        default=None,
        help="权重格组合数上限（默认 64；--quick 时 24；显式给值优先；截断保含单腿/等权基线）",
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
        "--n-random",
        type=int,
        default=None,
        help="随机臂条数（默认 3，同腿数同格同待遇；--quick 时 1；显式给值优先）",
    )
    ap.add_argument("--sens-arms", type=int, default=4, help="灵敏度扰动臂数（默认 4）")
    ap.add_argument(
        "--sens-pct", type=float, default=0.5, help="灵敏度扰动幅度（默认 0.5 = ±50%%）"
    )
    # ---- 两阶段省钱模式（v0.231：生产机 r34_v1 单阶段 264 格 ≈15h 的降本）----
    ap.add_argument(
        "--two-stage",
        action="store_true",
        help="两阶段省钱模式：阶段1 粗筛宇宙×全权重格×全对照臂取 top K 基因组，"
        "阶段2 仅 top K × 原宇宙终筛（对照臂终筛宇宙重跑；灵敏度/双窗在阶段2）",
    )
    ap.add_argument(
        "--coarse-sample",
        type=int,
        default=500,
        help="阶段1 粗筛宇宙抽样数（默认 500；同 --universe-seed 抽样，≥宇宙则全量）",
    )
    ap.add_argument(
        "--stage1-top-k",
        type=int,
        default=8,
        help="阶段1 晋级基因组装数（默认 8；对照臂不占名额，只作读数背景）",
    )
    ap.add_argument(
        "--quick",
        action="store_true",
        help="试跑档：= --n-random 1 --max-combos 24 的语义糖（显式给值优先；"
        "终审请补满随机臂）",
    )
    ap.add_argument(
        "--v0-arm",
        action="store_true",
        help="启用 V0 对照臂（live 现行技术分；独立载体实跑——run_cell 之外，"
        "同窗同宇宙同出场档读数口径）",
    )
    ap.add_argument(
        "--v0-lattice",
        action="store_true",
        help="V0 调权格模式（R36 Phase 3）：腿轴=V0 计分键（CONTRIB_LEG_KEYS 权威清单），"
        "格子=倍率向量（overrides=DEFAULT_TECH_WEIGHTS×倍率，负腿不变号）；"
        "collect/score 拆分（每窗收集一次、逐格重打分）；与 --legs/--legs-file/"
        "--two-stage/--v0-arm 互斥；随机对照仍走 DSL 随机臂（腿数=--max-legs）",
    )
    ap.add_argument(
        "--addon-leg",
        action="append",
        default=[],
        help="骨架加腿模式（R36 思路二，须配 --v0-lattice）：V0 等权骨架（live "
        "默认权重，不再调——P3 已杀调权路）上叠加 DSL 腿，score = V0分 + "
        "λ·TS_RANK(expr, --rank-window)；可重复 ≤4 条；基因组 = 无腿基准 + "
        "各腿×λ档（--addon-levels）——回答「这腿加进骨架有没有 Δmargin」，"
        "与单腿独立终审（r2 独苗机械退化口径）互补",
    )
    ap.add_argument(
        "--addon-levels",
        default="6,12,24",
        help="加腿 λ 档位（逗号分隔正数，默认 6,12,24——对齐 V0 腿分值量级 "
        "j_low=24；λ=0 不必给，无腿基准基因组恒在作 C2 参照）",
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

    ⚠️ 失败现场捕获（v0.234，R34 r34_v1 教训）：此前串行调用 run_cell
    （capture=False）——子进程 stdout/stderr **只透传终端**，报告里只剩
    「failed」没有任何文本（s_shape 臂 exit=2×2 无现场可查）。现改
    capture=True：失败格的子进程日志尾段（默认 40 行）收进
    ``cell_runner.failures``，报告 ``cell_failures`` 块落盘——下次失败
    有现场，不用再上生产机翻终端。
    """
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    cells_dir.mkdir(parents=True, exist_ok=True)
    failures: list[dict[str, Any]] = []

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
        status, path, log = sg.run_cell(ns, cell, cells_dir, capture=True)
        if status == "failed" or path is None:
            failures.append(
                {
                    "scorer": scorer,
                    "gate": gate,
                    "start": start,
                    "end": end,
                    "log_tail": "\n".join(log.splitlines()[-_FAILURE_LOG_TAIL_LINES:]),
                }
            )
            return None
        return _reading_of(
            sg, sg.load_cell_row(cell, path, reused=(status == "reused"))
        )

    cell_runner.failures = failures  # type: ignore[attr-defined]  # 侧信道：报告层只读
    return cell_runner


#: 失败格子日志尾段行数（cell_runner.failures / 报告 cell_failures 块共用）。
_FAILURE_LOG_TAIL_LINES = 40


def _classify_cell_failure(log_tail: str) -> str:
    """失败根因分类：「合法空」与「格子失败」必须分清（R34 r34_v1 s_shape 臂教训）。

    - ``empty_result``：子进程日志含空结果护栏的指纹（``产出 0 `` + ``拒绝落盘``，
      backtest_factors._empty_result_guard 原文）——该宇宙×窗内 0 信号是
      **合法空**（门槛过严使然，如 s_shape 可买阈值 s_star≥70 与 j_low 超卖池
      近互斥），不是格子坏了；
    - ``cell_failed``：其他一切失败（含无日志——injected runner / 旧产物）。
    """
    if "产出 0 " in log_tail and "拒绝落盘" in log_tail:
        return "empty_result"
    return "cell_failed"


def _cell_failures_report(runner: CellRunner) -> list[dict[str, Any]]:
    """cell_runner.failures 侧信道 → 报告块（injected runner 无侧信道 → 空表）。"""
    out = []
    for f in getattr(runner, "failures", None) or []:
        out.append({**f, "root_cause": _classify_cell_failure(f.get("log_tail", ""))})
    return out


def _root_cause_for(runner: CellRunner, scorer: str, window: Window) -> str:
    """指定 (scorer, 窗口) 的失败根因（无记录 → cell_failed）。"""
    for f in _cell_failures_report(runner):
        if (
            f["scorer"] == scorer
            and f["start"] == window.start
            and f["end"] == window.end
        ):
            return f["root_cause"]
    return "cell_failed"


# ---------------------------------------------------------------------------
# V0 对照臂（v0.231：独立载体——run_cell 之外，但读数与 cell 同函数同公式）
# ---------------------------------------------------------------------------

# V0 臂的组合层参数：与 backtest_factors argparse 默认逐字一致
# （cell 不传这三个旗标 ⇒ 子进程用的就是这组默认）。
# 注解成 dict[str, Any]：不注解 mypy 会把值 join 成 dict[str, float]
# （5 → 5.0），**展开进 simulate_portfolio_topn 时撞 top_n: int 的形参。
_V0_PORTFOLIO: dict[str, Any] = {
    "risk_pct": 0.01,
    "max_concurrent": 5,
    "max_pos_frac": 0.20,
}


def _make_v0_runner(args: Any, codes: list[str], exit_spec: dict) -> CellRunner:
    """构造 V0 对照臂执行器（--v0-arm 时启用；生产机专用，测试注入 fake）。

    V0 = live 现行技术分（``score_variants_study.v0_score`` 的同一份判定：
    ``score_return_study.asof_technical_score`` 的 as-of 技术分，与落盘分逐位
    一致有钉测）。它**不是** DSL 表达式（依赖 enrich compute_metrics：指数
    20 日相对强度 + df_long 周/月 MACD 腿），编不进 expr: scorer——所以走
    run_cell 之外的独立载体，但读数口径与 cell **同函数同公式**：

      ① 交易集：``evaluate_trades(collect_all=True, scorer=baseline(恒可买),
         entry_gate=j_low, amv_regime, **exit_spec.params, cost_bps, collect_all)``
         ——与 cell 子进程（--trade-sim --top-n）同引擎同参数；
      ② A 层选择：每笔候选的 ``score`` 改写为 V0 as-of 技术分后
         ``simulate_portfolio_topn(top_n=--top-n)``——与 cell 的组合层同函数；
      ③ 读数：``summarize_trades`` + ``objective_of(DEFAULT_OBJ_WEIGHTS)``
         ——margin/expectancy_R/ret_over_dd/objective 与 cell 同公式。

    ⚠️ warmup 口径注记：逐股加载走 cell 同款 ``_load_one_bars(count,start,end)``
    （研究窗口径），V0 评分内部的 MACD/周月腿 warmup 限于窗口内——live 链是
    全历史（count=100000），残差已如实注记（读数块 notes）。
    """

    def v0_runner(*, start: str, end: str) -> Optional[dict]:
        try:
            from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415
            from custos.research import score_return_study as srs  # noqa: PLC0415
            from custos.research import strategy_grid as sg  # noqa: PLC0415

            regime = bt.load_amv_regime(since=start)
            if not regime:
                print(
                    "[WARN] V0 臂：0AMV regime 读不到（compass_amv）——本机无数据？",
                    file=sys.stderr,
                )
                return None
            index_df = (
                local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
                .sort_values("date")
                .reset_index(drop=True)
            )
            cands: list[dict[str, Any]] = []
            for code in codes:
                df = bt._load_one_bars(code, args.count, start, end)
                if df is None or not len(df):
                    continue
                code_trades = bt.evaluate_trades(
                    {code: df},
                    scorer=bt.SCORERS["baseline"],  # 恒可买——进场只由 j_low gate 决定
                    entry_gate=bt.j_low_gate,
                    amv_regime=regime,
                    cost_bps=args.cost_bps,
                    collect_all=True,  # 全候选（同 cell 的 --top-n>0 口径）
                    **dict(exit_spec["params"]),
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
                        score, _level, _contrib = srs.asof_technical_score(
                            df, index_df, i, code
                        )
                    except Exception:  # noqa: BLE001
                        continue  # 单笔评分失败丢该笔（WARN 在 srs 内部已打）
                    cands.append({**tr, "score": score})
            if not cands:
                print("[WARN] V0 臂：0 候选（宇宙/窗口/数据有问题？）", file=sys.stderr)
                return None
            tsum = bt.summarize_trades(cands)
            pf = bt.simulate_portfolio_topn(cands, top_n=args.top_n, **_V0_PORTFOLIO)
            margin = sg._margin(
                {"win": tsum.get("win_rate"), "payoff": tsum.get("payoff_ratio")}
            )
            ret_dd = sg._ret_over_dd(pf)
            row = {
                "margin": margin,
                "expectancy_R": tsum.get("expectancy_R"),
                "ret_over_dd": ret_dd,
            }
            return {
                "objective": sg.objective_of(row, sg.DEFAULT_OBJ_WEIGHTS),
                "margin": margin,
                "expectancy_R": tsum.get("expectancy_R"),
                "payoff_ratio": tsum.get("payoff_ratio"),
                "win_rate": tsum.get("win_rate"),
                "n": tsum.get("n"),
                "cell_signature": None,  # 独立载体无 cell_signature（不参与签名复用）
                "ret_over_dd": ret_dd,
                "selected_win_rate": pf.get("selected_win_rate"),
                "selected_expectancy": pf.get("selected_expectancy"),
                "n_taken": pf.get("n_taken"),
                "n_candidates": len(cands),
            }
        except Exception as exc:  # noqa: BLE001
            print(
                f"[WARN] V0 臂失败: {type(exc).__name__}: {exc}（读数缺失留痕）",
                file=sys.stderr,
            )
            return None

    return v0_runner


# ---------------------------------------------------------------------------
# V0 调权格模式（--v0-lattice，R36 Phase 3：V0 腿轴 × 倍率格 × 双窗闸门）
# ---------------------------------------------------------------------------

#: repair_signals 是「每项分/上限」合成腿（score_calibration_study._NON_LEG_WEIGHT_KEYS
#: 口径）——倍率同乘 each/cap 双键，合成 contrib 恰好线性缩放。
_V0_REPAIR_KEYS = ("repair_signals_each", "repair_signals_cap")


def _v0_leg_weight_keys(leg: str) -> tuple[str, ...]:
    """V0 腿 → DEFAULT_TECH_WEIGHTS 权重键（多数腿同名直映；合成腿一对多）。"""
    if leg == "repair_signals":
        return _V0_REPAIR_KEYS
    return (leg,)


def _validate_v0_legs(legs: Sequence[str], ap: argparse.ArgumentParser) -> None:
    """V0 腿轴健全性（fail-closed）：非空 + 每条腿的权重键都在 DEFAULT_TECH_WEIGHTS。"""
    if not legs:
        ap.error(
            "V0 腿轴为空（CONTRIB_LEG_KEYS 为空？）——空腿轴的裁决产物只会被误读，拒跑"
        )
    bad = [
        k
        for leg in legs
        for k in _v0_leg_weight_keys(leg)
        if k not in sc.DEFAULT_TECH_WEIGHTS
    ]
    if bad:
        ap.error(
            f"V0 腿键不在 DEFAULT_TECH_WEIGHTS: {bad}（CONTRIB_LEG_KEYS 与权重表漂移？）"
        )


def _v0_mult_overrides(mult: Sequence[float], legs: Sequence[str]) -> dict[str, float]:
    """倍率向量 → ``technical_score`` 权重覆盖表：``overrides[键] = 默认权重 × 倍率``。

    负腿（macd_top_divergence/volume_yy_bear/distribution_*）倍率缩放的是**罚分
    幅度**，永不变号（默认权重本身是负的，乘非负倍率保持负）；0 = 该腿关闭。
    全键输出（mult=1 的腿也在，数值=默认）——覆盖表是倍率向量的确定性展开，
    可还原性靠倍率向量本身（产物 ``lattice.weights`` + ``default_weights_snapshot``
    + ``leg_weight_keys`` 三者可完整重建）。
    """
    if len(mult) != len(legs):
        raise ValueError(f"倍率向量维数 {len(mult)} 与腿数 {len(legs)} 不一致")
    out: dict[str, float] = {}
    for m, leg in zip(mult, legs):
        for key in _v0_leg_weight_keys(leg):
            if key not in sc.DEFAULT_TECH_WEIGHTS:
                raise ValueError(
                    f"V0 腿 {leg!r} 的权重键 {key!r} 不在 DEFAULT_TECH_WEIGHTS"
                )
            out[key] = sc.DEFAULT_TECH_WEIGHTS[key] * float(m)
    return out


def _resolve_addon(args: Any, ap: argparse.ArgumentParser) -> list[str]:
    """--addon-leg 清单解析（fail-closed）：≤4 条、DSL 白名单过、复杂度不违规、
    TS_RANK 包装后不超节点上限；``--addon-levels`` 解析为正浮点元组
    （λ=0 = 无腿基准基因组冗余，拒）。结果回写 ``args.addon_legs/addon_levels``。
    """
    exprs = [str(e).strip() for e in args.addon_leg if str(e).strip()]
    if len(exprs) > 4:
        ap.error(f"--addon-leg 最多 4 条（防组合爆炸），实际 {len(exprs)}")
    from custos.research.evolution import expr_dsl  # noqa: PLC0415

    bad: list[str] = []
    for e in exprs:
        try:
            comp = expr_dsl.complexity(e)
            viol = expr_dsl.violations(comp)
            if viol:
                bad.append(f"{e}: 复杂度违规 {'；'.join(viol)}")
                continue
            expr_dsl.parse(f"TS_RANK({e},{args.rank_window})")  # 包装后节点预检
        except expr_dsl.ExprError as exc:
            bad.append(f"{e}: {exc}")
    if bad:
        ap.error(
            "--addon-leg 校验失败（fail-closed，不部分受理）：\n  " + "\n  ".join(bad)
        )
    try:
        levels = tuple(
            float(x) for x in str(args.addon_levels).split(",") if str(x).strip()
        )
    except ValueError:
        ap.error(f"--addon-levels 形态非法: {args.addon_levels!r}")
    if not levels or any(not math.isfinite(v) or v <= 0 for v in levels):
        ap.error(
            f"--addon-levels 必须全为正数（λ=0 是无腿基准基因组，冗余）: {levels!r}"
        )
    args.addon_legs = list(dict.fromkeys(exprs))  # 保序去重
    args.addon_levels = levels
    return args.addon_legs


def _addon_series(
    df: pd.DataFrame, exprs: list[str], rank_window: int
) -> dict[str, Optional[pd.Series]]:
    """加腿 TS_RANK 归一序列（每股每腿一次，collect 期预计算）。

    归一口径同 DSL 基因组编译（TS_RANK(leg, K)，K=--rank-window 默认 250）——
    逐股时序自指分位 ∈ (0,1]，与 V0 分值可加（λ 档对齐腿分值量级）。
    评估失败 → None（该股全窗缺席，加腿基因组如实记 n_addon_missing）。
    """
    from custos.research.evolution import expr_dsl  # noqa: PLC0415

    out: dict[str, Optional[pd.Series]] = {}
    for e in exprs:
        try:
            out[e] = expr_dsl.evaluate(f"TS_RANK({e},{rank_window})", df)
        except Exception as exc:  # noqa: BLE001 — 单腿失败不拖死收集
            print(
                f"[WARN] 加腿序列评估失败 {e[:48]}: {type(exc).__name__}: {exc}"
                "（该股该腿全窗缺席）",
                file=sys.stderr,
            )
            out[e] = None
    return out


def _addon_value(series: Optional[pd.Series], i: int) -> Optional[float]:
    """加腿序列的第 i 根取值：None 序列/越界/NaN/±inf（warmup 与除零）→ None。"""
    if series is None or i >= len(series):
        return None
    try:
        v = float(series.iloc[i])
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _v0_mult_lattice(
    n_legs: int, levels: tuple[float, ...], *, max_combos: int
) -> list[tuple[float, ...]]:
    """V0 倍率格（确定性；与 weight_lattice 的分歧理由见模块 docstring 的 v0-lattice 段）。

    - **保底集恒在**（可超 max_combos，语义同 weight_lattice 截断）：等倍率向量
      （全 1 = live 默认权重，C2 基准）+ 各单腿向量（腿 i=1 其余 0，对照臂）；
    - 填充 = 单腿变档边际臂（腿 i 取 L、其余保持 1）：L 升序（levels 剔 1；0 档
      优先——「关掉这条腿」是信息量最大的边际测试）外循环 × 腿声明序内循环，
      任意截断点各腿覆盖尽量均匀；
    - 不做 gcd 比例等价去重：technical_score 的 0-100 clamp 使尺度不变性不成立
      （全乘 2 改变 clamp 后分布 ⇒ 排序可变），比例不同的向量是不同格点；
    - 返回 float 元组清单（与 weight_lattice 产物同型）。
    """
    # 形态校验复用 weight_lattice 的入参门（整数刻度/非负/非全零/max_combos 正整数）
    weight_lattice(1, levels, max_combos=max_combos)
    if isinstance(n_legs, bool) or not isinstance(n_legs, int) or n_legs < 1:
        raise ValueError(f"n_legs 必须是正整数，得到 {n_legs!r}")
    must: list[tuple[float, ...]] = [
        tuple(1.0 for _ in range(n_legs))
    ]  # 等倍率=V0 基准
    must += [
        tuple(1.0 if j == i else 0.0 for j in range(n_legs)) for i in range(n_legs)
    ]
    fill_levels = sorted({float(lv) for lv in levels if float(lv) != 1.0})
    fills: list[tuple[float, ...]] = []
    for lv in fill_levels:
        for i in range(n_legs):
            fills.append(tuple(lv if j == i else 1.0 for j in range(n_legs)))
    room = max(0, max_combos - len(must))
    return must + fills[:room]


def _collect_v0_window(
    args: Any, codes: list[str], exit_spec: dict, window: Window
) -> Optional[list[dict]]:
    """v0-lattice 每窗一次的**权重无关**收集：全候选交易 + 每笔的 as-of cand。

    与 V0 臂（``_make_v0_runner``）同引擎同参数（``evaluate_trades(collect_all)``
    + baseline 恒可买 scorer + j_low gate + amv_regime + 同出场档 + 同窗同宇宙），
    差别只在不打分——cand 留给逐格重打分（collect/score 拆分）。返回元素
    ``{"trade": 交易dict, "cand": as-of cand, "code": str}``；任一致命失败 → None
    （调用方空结果护栏非零退出不落盘）。
    """
    try:
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415
        from custos.research import score_return_study as srs  # noqa: PLC0415

        regime = bt.load_amv_regime(since=window.start)
        if not regime:
            print(
                "[WARN] v0-lattice：0AMV regime 读不到（compass_amv）——本机无数据？",
                file=sys.stderr,
            )
            return None
        index_df = (
            local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
            .sort_values("date")
            .reset_index(drop=True)
        )
        collected: list[dict[str, Any]] = []
        addon_exprs = list(getattr(args, "addon_legs", []) or [])
        for code in codes:
            df = bt._load_one_bars(code, args.count, window.start, window.end)
            if df is None or not len(df):
                continue
            # 加腿 TS_RANK 序列每股每腿一次（collect/score 拆分：逐格只是重打分）
            addon_map = (
                _addon_series(df, addon_exprs, args.rank_window) if addon_exprs else {}
            )
            code_trades = bt.evaluate_trades(
                {code: df},
                scorer=bt.SCORERS["baseline"],  # 恒可买——进场只由 j_low gate 决定
                entry_gate=bt.j_low_gate,
                amv_regime=regime,
                cost_bps=args.cost_bps,
                collect_all=True,  # 全候选（同 cell 的 --top-n>0 口径）
                **dict(exit_spec["params"]),
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
                    cand = srs.asof_candidate(df, index_df, i, code)
                except Exception:  # noqa: BLE001
                    continue  # 单笔 cand 失败丢该笔（WARN 在 srs 内部已打）
                item: dict[str, Any] = {"trade": tr, "cand": cand, "code": code}
                if addon_exprs:
                    item["addon"] = {
                        e: _addon_value(s, i) for e, s in addon_map.items()
                    }
                collected.append(item)
        if not collected:
            print(
                f"[WARN] v0-lattice：窗口 {window.start}~{window.end} 0 候选"
                "（宇宙/窗口/数据有问题？）",
                file=sys.stderr,
            )
        return collected
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WARN] v0-lattice 收集失败: {type(exc).__name__}: {exc}（读数缺失留痕）",
            file=sys.stderr,
        )
        return None


class _V0LatticeEvaluator:
    """v0-lattice 执行器：collect 每窗一次（按窗缓存）+ score 每格一次。

    ``collector`` 可注入（测试合成数据；签名 ``collector(window) -> list|None``，
    元素形状同 ``_collect_v0_window`` 产物）；None = 生产收集器。``collect_calls``
    是审计/钉测 spy（收集次数 = 窗数，与格数无关——拆分的证据）。
    """

    def __init__(
        self,
        args: Any,
        codes: list[str],
        exit_spec: dict,
        collector: Optional[Callable[[Window], Optional[list[dict]]]] = None,
    ) -> None:
        self._args = args
        self._codes = codes
        self._exit_spec = exit_spec
        self._collector = collector
        self._cache: dict[tuple[str, str], Optional[list[dict]]] = {}
        self._pool_cache: dict[tuple[str, str], Optional[dict]] = {}
        self.collect_calls: list[tuple[str, str]] = []

    def collect(self, window: Window) -> Optional[list[dict]]:
        key = (window.start, window.end)
        if key not in self._cache:
            self.collect_calls.append(key)
            if self._collector is not None:
                self._cache[key] = self._collector(window)
            else:
                self._cache[key] = _collect_v0_window(
                    self._args, self._codes, self._exit_spec, window
                )
        return self._cache[key]

    def pool_summary(self, window: Window) -> Optional[dict]:
        """全候选池汇总（**权重不变量**审计块——打分只排序不过滤，池读数与倍率无关）。"""
        from custos.research import strategy_grid as sg  # noqa: PLC0415

        key = (window.start, window.end)
        if key not in self._pool_cache:
            collected = self.collect(window)
            if not collected:
                self._pool_cache[key] = None
            else:
                tsum = bt.summarize_trades([dict(it["trade"]) for it in collected])
                self._pool_cache[key] = {
                    "n": tsum.get("n"),
                    "win_rate": tsum.get("win_rate"),
                    "payoff_ratio": tsum.get("payoff_ratio"),
                    "expectancy_R": tsum.get("expectancy_R"),
                    "margin": sg._margin(
                        {
                            "win": tsum.get("win_rate"),
                            "payoff": tsum.get("payoff_ratio"),
                        }
                    ),
                }
        return self._pool_cache[key]

    def evaluate(
        self,
        mult: Sequence[float],
        legs: Sequence[str],
        window: Window,
        addon: Optional[tuple[str, float]] = None,
    ) -> Optional[dict]:
        """单格：technical_score 按倍率重打分 → top_n 组合 → **选中子集**读数。

        读数键形与 V0 臂/cell 一致（objective/margin/expectancy_R/payoff_ratio/
        win_rate/n + ret_over_dd/n_taken/n_candidates 等）；margin/胜率/盈亏比/
        expectancy_R/n 取 top_n 选中子集（A层）——全候选池读数是权重不变量
        （V0 臂公式下 Δmargin 恒 0，C2 会机械退化），池统计收 ``pool_baseline``。
        收集为空 → None；0 笔被选中 ⇒ objective None（不参与 argmax，语义同
        DSL 格读数缺失）。

        ``addon=(expr, λ)``（R36 思路二骨架加腿）：score = V0(mult) + λ·加腿值
        （collect 期按笔预计算的 TS_RANK 归一值）；无加腿读数的交易
        （warmup/评估失败）**不进**加腿基因组——``n_addon_missing`` 如实记录
        （缺席≠零值，硬塞 0 会把「没读数」伪装成「最低分」）。
        """
        from custos.research import strategy_grid as sg  # noqa: PLC0415

        collected = self.collect(window)
        if not collected:
            return None
        overrides = _v0_mult_overrides(mult, legs)
        addon_expr, lam = addon if addon else (None, 0.0)
        cands: list[dict[str, Any]] = []
        n_missing = 0
        for it in collected:
            av: Optional[float] = None
            if addon_expr is not None:
                av = (it.get("addon") or {}).get(addon_expr)
                if av is None:
                    n_missing += 1
                    continue
            score, _level, _contrib = sc.technical_score(it["cand"], overrides)
            if addon_expr is not None:
                score = score + lam * float(av)
            cands.append({**it["trade"], "score": score})
        taken: list[dict] = []
        pf = bt.simulate_portfolio_topn(
            cands, top_n=self._args.top_n, taken_out=taken, **_V0_PORTFOLIO
        )
        tsum = bt.summarize_trades(taken)
        margin = sg._margin(
            {"win": tsum.get("win_rate"), "payoff": tsum.get("payoff_ratio")}
        )
        ret_dd = sg._ret_over_dd(pf)
        row = {
            "margin": margin,
            "expectancy_R": tsum.get("expectancy_R"),
            "ret_over_dd": ret_dd,
        }
        out = {
            "objective": sg.objective_of(row, sg.DEFAULT_OBJ_WEIGHTS),
            "margin": margin,
            "expectancy_R": tsum.get("expectancy_R"),
            "payoff_ratio": tsum.get("payoff_ratio"),
            "win_rate": tsum.get("win_rate"),
            "n": tsum.get("n"),
            "cell_signature": None,  # 独立载体无 cell_signature（不参与签名复用）
            "ret_over_dd": ret_dd,
            "selected_win_rate": pf.get("selected_win_rate"),
            "selected_expectancy": pf.get("selected_expectancy"),
            "n_taken": pf.get("n_taken"),
            "n_candidates": len(cands),
        }
        if addon_expr is not None:
            out["n_addon_missing"] = n_missing
        return out


def _run_v0_lattice_study(
    args: Any,
    legs: list[str],
    legs_source: str,
    mining: Window,
    judgment: Optional[Window],
    exit_spec: dict,
    codes: list[str],
    runner: CellRunner,
    evaluator: _V0LatticeEvaluator,
) -> Optional[dict]:
    """v0-lattice 评估编排（闸门与 _run_study 同族；全部格子读数缺失 → None）。

    随机对照臂走 **DSL expr 路**（R34 同口径标尺：--max-legs 条随机腿 × 标准
    ``weight_lattice`` 权重格——V0 30 腿 DSL 复合超 expr_dsl AST 节点上限不可
    表达，随机化 V0 腿不是本模式口径），复用 cell_runner/_eval_random_arms。
    """
    levels = tuple(float(x) for x in args.lattice_levels)
    addon_legs = list(getattr(args, "addon_legs", []) or [])
    arms_ref = baseline_arms(len(legs))
    exit_params = dict(exit_spec["params"])

    if addon_legs:
        # 骨架加腿模式（R36 思路二）：V0 等权骨架固定 live 默认（P3 已杀调权路，
        # 不再碰倍率格）——基因组 = 无腿基准 + 各加腿×λ档，全部 collect 一遍。
        equal_w = tuple(1.0 for _ in legs)
        lattice = [equal_w]  # 报告自含可还原用（基因组权重全等权，差异在 addon）
        genome_list = [(equal_w, None)] + [
            (equal_w, (e, lam)) for e in addon_legs for lam in args.addon_levels
        ]
        mining_rows = [
            {
                "weights": list(w),
                "addon": ({"expr": a[0], "lambda": a[1]} if a else None),
                "reading": evaluator.evaluate(w, legs, mining, addon=a),
            }
            for w, a in genome_list
        ]
    else:
        lattice = _v0_mult_lattice(len(legs), levels, max_combos=args.max_combos)
        # 挖掘窗逐格（collect 在 evaluator 内按窗缓存——全部格子只跑一遍收集）
        mining_rows = [
            {"weights": list(w), "reading": evaluator.evaluate(w, legs, mining)}
            for w in lattice
        ]
    top = _best(mining_rows)
    if top is None:
        print(
            "[ERR] v0-lattice 全部格子读数缺失（数据源/窗口有问题？）——"
            "空产物会被误读成「权重格全灭」，拒绝落盘",
            file=sys.stderr,
        )
        return None
    if addon_legs:
        equal_row = mining_rows[0]  # 无腿基准基因组（λ=0）即 C2 参照
        single_rows: list[tuple[str, Optional[dict]]] = []
    else:
        equal_row = _find_by_weights(mining_rows, arms_ref["equal"])
        single_rows = [
            (name, _find_by_weights(mining_rows, w))
            for name, w in arms_ref.items()
            if name != "equal"
        ]

    # 随机对照臂（DSL expr 路，R34 标尺口径；与 V0 倍率格不同载体）
    random_lattice = weight_lattice(args.max_legs, levels, max_combos=args.max_combos)
    random_arms = _eval_random_arms(
        runner, random_lattice, args.max_legs, args, exit_params, mining
    )
    random_best_obj = _random_best_obj(random_arms)

    # 判定窗：top / 等倍率基准两臂复测（判定窗 collect 同样只跑一遍）
    top_addon = (
        (top["addon"]["expr"], float(top["addon"]["lambda"]))
        if top.get("addon")
        else None
    )
    judgment_block: Optional[dict[str, Any]] = None
    if judgment is not None:
        judgment_block = {
            "top": evaluator.evaluate(
                tuple(top["weights"]), legs, judgment, addon=top_addon
            ),
            "equal": (
                evaluator.evaluate(arms_ref["equal"], legs, judgment)
                if equal_row is not None
                else None
            ),
            "s_shape": None,  # 形状对齐 DSL 模式；s_shape 参照臂不属本模式
        }

    # 灵敏度：top 格 ±pct 扰动重打分挖掘窗（不重新 collect）——加腿模式扰动
    # 30 维倍率 + λ 的 31 维向量（λ 也是基因组参数，同受扰）；
    # 翻转 = 扰动臂 objective 跌破等倍率基准（读数缺失按翻转计，保守）
    base_obj = (
        equal_row["reading"].get("objective")
        if equal_row and equal_row.get("reading")
        else None
    )
    top_obj = top["reading"]["objective"]
    srng = random.Random(args.seed)
    sens_rows, flips = [], 0
    for _ in range(args.sens_arms):
        if addon_legs:
            lam0 = float(top["addon"]["lambda"]) if top.get("addon") else 0.0
            pw = perturb_weights(
                tuple(top["weights"]) + (lam0,), pct=args.sens_pct, rng=srng
            )
            pexpr = top_addon[0] if top_addon else None
            preading = evaluator.evaluate(
                pw[: len(legs)],
                legs,
                mining,
                addon=(pexpr, pw[len(legs)]) if pexpr else None,
            )
            pw_show = pw
        else:
            pw = perturb_weights(top["weights"], pct=args.sens_pct, rng=srng)
            preading = evaluator.evaluate(pw, legs, mining)
            pw_show = pw
        pobj = preading.get("objective") if preading else None
        flip = (pobj is None) if base_obj is None else (pobj is None or pobj < base_obj)
        flips += int(flip)
        sens_rows.append(
            {
                "weights": [round(x, 6) for x in pw_show],
                "reading": preading,
                "flip": flip,
            }
        )

    # 随机对照判定（#71 同族：top 必须打过随机臂最高分）
    if random_best_obj is None:
        verdict = "indeterminate"  # 随机臂全灭（读不出）——无法裁决，留痕
    else:
        verdict = "pass" if top_obj > random_best_obj else "suspect"

    # 判据机械读数（R34-C1~C5 结构沿用——R36-C2~C4 直接吃本块，映射见
    # config.r36_mapping；判定本身按预注册页执行，这里只出机械读数）
    grid_word = "加腿格" if addon_legs else "倍率格"
    base_word = (
        "V0 等权骨架（live 默认权重，无腿基准基因组）"
        if addon_legs
        else "等倍率基准（=live V0 默认权重）"
    )
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
    j_top = (judgment_block or {}).get("top") or {}
    j_equal = (judgment_block or {}).get("equal") or {}
    d_judgment = None
    if judgment_block is not None:
        jm, em = j_top.get("margin"), j_equal.get("margin")
        d_judgment = (jm - em) if jm is not None and em is not None else None
    criteria = {
        "R34-C1": {
            "rule": "单窗单格笔数 ≥100（v0-lattice 口径：笔数 = top_n 选中子集"
            " n_taken，非全候选池）",
            "top_n_mining": top["reading"].get("n"),
            "top_n_judgment": j_top.get("n") if judgment_block else None,
            "threshold": 100,
        },
        "R34-C2": {
            "rule": f"top {grid_word}相对{base_word} margin > 0 且"
            "双窗同向（R36-C2 晋级线的机械读数）",
            "delta_mining": d_mining,
            "delta_judgment": d_judgment,
            "judgment_window": judgment is not None,
        },
        "R34-C3": {
            "rule": f"灵敏度 ±{args.sens_pct:.0%} 零翻转（扰动臂 objective 跌破"
            f"{base_word} = 翻转；读数缺失按翻转计）",
            "arms": args.sens_arms,
            "flips": flips,
        },
        "R34-C4": {
            "rule": f"top {grid_word} objective > 随机 DSL 臂最高分（#71 纪律；随机臂"
            "腿数 = --max-legs，R34 标尺口径）",
            "top_objective": top_obj,
            "random_best_objective": random_best_obj,
            "verdict": verdict,
        },
        "R34-C5": {
            "rule": "pre2019 untouched 终审段单独终步（一票否决）",
            "status": "not_run",
            "note": "本工具窗口与 pre2019 段相交即拒跑；终审留生产机单独终步（R36-C5）",
        },
    }

    # arms.v0：等倍率格（全 1 倍率 = live 默认权重）即 V0 本臂——读数取自
    # lattice 等倍率行，不另跑（口径注记：本模式读数是选中子集口径，与 DSL
    # 模式 --v0-arm 的全池 margin 口径不同，见 config.reading_basis）
    equal_reading = equal_row["reading"] if equal_row else None
    v0_block: dict[str, Any] = {
        "status": "run" if equal_reading else "failed",
        "vehicle": "等倍率格（全 1 倍率 = live 默认权重）即 V0 本臂——读数取自 "
        "lattice 等倍率行（选中子集口径），不另跑独立载体",
        "reading": equal_reading,
        "notes": [
            "V0 评分指标 warmup 限于研究窗口（cell 同款 _load_one_bars "
            "count/start/end）；live 链全历史（count=100000）口径的残差如实注记"
            "——同窗同宇宙同出场档的对比成立，绝对值不可与 live 互引",
        ],
    }
    if not equal_reading:
        v0_block["reason"] = "等倍率格读数缺失（0 笔被选中/收集为空？见 stderr WARN）"

    vs_v0 = None
    if equal_reading:
        v0_obj = equal_reading.get("objective")
        v0_margin = equal_reading.get("margin")
        vs_v0 = {
            "delta_objective": (top_obj - v0_obj if v0_obj is not None else None),
            "delta_margin": (
                top_margin - v0_margin
                if top_margin is not None and v0_margin is not None
                else None
            ),
        }

    top_mult = {leg: float(top["weights"][i]) for i, leg in enumerate(legs)}
    rep: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "tag": args.tag,
        "config": {
            "mode": "v0_lattice_addon" if addon_legs else "v0_lattice",
            "legs_source": legs_source,
            "leg_axis": "V0 计分键（score_calibration_study.CONTRIB_LEG_KEYS 权威"
            "清单；repair_signals = each/cap 合成腿，倍率同乘双键）",
            "multiplier_space": "倍率格：overrides[键] = DEFAULT_TECH_WEIGHTS[键] × "
            "倍率；负腿倍率缩放罚分幅度、永不变号；0 = 关腿。不做 gcd 比例去重"
            "（technical_score 的 0-100 clamp 使尺度不变性不成立）",
            "reading_basis": "margin/胜率/盈亏比/expectancy_R/n = top_n 选中子集"
            "（A层）口径；全候选池是权重不变量，收 pool_baseline 审计块",
            "random_control_vehicle": "DSL 随机臂（R34 同口径：--max-legs 条随机腿 × "
            "weight_lattice 标准权重格）——V0 30 腿 DSL 复合超 expr_dsl AST 节点"
            "上限不可表达，随机化 V0 腿非本模式口径",
            "r36_mapping": {
                "R34-C1": "样本量护栏（结局③判读用）",
                "R34-C2": "R36-C2（晋级线）",
                "R34-C3": "R36-C3（灵敏度）",
                "R34-C4": "R36-C4（随机对照）",
                "R34-C5": "R36-C5（终审）",
            },
            "max_legs": args.max_legs,  # 本模式只约束随机 DSL 臂腿数（V0 腿轴固定）
            "rank_window": args.rank_window,  # 只用于随机 DSL 臂复合编译
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
            "quick": bool(args.quick),
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
            "kind": "v0_addon" if addon_legs else "v0_multiplier",
            "n_legs": len(legs),
            "n_combos": len(mining_rows),
            "weights": [list(w) for w in lattice],
            # 产物自含可还原：weights（倍率）× default_weights_snapshot 经
            # leg_weight_keys 展开 = 每格的 technical_score 覆盖表
            "default_weights_snapshot": dict(sc.DEFAULT_TECH_WEIGHTS),
            "leg_weight_keys": {leg: list(_v0_leg_weight_keys(leg)) for leg in legs},
            # 加腿模式专有键（键集合按模式钉死，非加腿模式不出现）
            **(
                {"addon_legs": addon_legs, "addon_levels": list(args.addon_levels)}
                if addon_legs
                else {}
            ),
        },
        "arms": {
            "lattice": mining_rows,
            "equal_weight": equal_row,
            "single_legs": [
                {"name": name, "leg": legs[int(name.split("_", 1)[1])], "row": row}
                for name, row in single_rows
            ],
            # 加腿模式专有键：每加腿的最佳档行（非加腿模式不出现）
            **(
                {
                    "addon_per_expr": [
                        {
                            "expr": e,
                            "best_row": _best(
                                [
                                    r
                                    for r in mining_rows
                                    if r.get("addon") and r["addon"]["expr"] == e
                                ]
                            ),
                        }
                        for e in addon_legs
                    ]
                }
                if addon_legs
                else {}
            ),
            "s_shape": {
                "status": "not_applicable",
                "reason": "s_shape 参照臂属 DSL 基因组模式；v0-lattice 的对照基准"
                "是等倍率格（= V0 本臂）",
            },
            "random": random_arms,
            "v0": v0_block,
        },
        "pool_baseline": {
            "mining": evaluator.pool_summary(mining),
            "judgment": evaluator.pool_summary(judgment) if judgment else None,
        },
        "top_genome": {
            "weights": top["weights"],
            # DSL 模式的 expr 槽位：放可还原的倍率向量描述（JSON 字符串）
            "expr": json.dumps(top_mult, ensure_ascii=False),
            "multipliers": top_mult,
            "addon": top.get("addon"),  # 加腿基因组标识（expr×λ；非加腿模式 None）
            "overrides": _v0_mult_overrides(top["weights"], legs),
            "mining": top["reading"],
            "judgment": judgment_block["top"] if judgment_block else None,
            "vs_v0": vs_v0,
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
        "cell_failures": [
            {"source": "final", **f} for f in _cell_failures_report(runner)
        ],
        "criteria_readings": criteria,
    }
    return rep


# ---------------------------------------------------------------------------
# 评估编排（DSL 基因组模式）
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


def _top_k_rows(rows: list[dict], k: int) -> list[dict]:
    """阶段 1 晋级：按挖掘窗 objective 降序取前 k 行（稳定序，并列保格子序）。

    对照臂（等权/单腿/随机/s_shape/V0）**不占 k 的名额**——调用方只把真腿
    lattice 行传进来；读数缺失的行不晋级（留在阶段 1 报告里作背景）。
    """
    ok = [
        r
        for r in rows
        if r.get("reading") and r["reading"].get("objective") is not None
    ]
    ok.sort(key=lambda r: r["reading"]["objective"], reverse=True)  # sort 稳定
    return ok[: max(k, 0)]


def _find_by_weights(rows: list[dict], weights: Sequence[float]) -> Optional[dict]:
    wl = [float(x) for x in weights]
    for r in rows:
        if r["weights"] == wl:
            return r
    return None


def _eval_random_arms(
    runner: CellRunner,
    lattice: Sequence[tuple[float, ...]],
    n_legs: int,
    args: Any,
    exit_params: dict,
    mining: Window,
) -> list[dict]:
    """随机对照臂：同腿数随机表达式 × 同权重格同待遇（#71 纪律）。

    两阶段模式在阶段 1（粗筛宇宙）与阶段 2（终筛宇宙）各跑一次——口径一致
    才有对照意义；采样 rng 在每批内部重放（random_seed 钉死，批间逐位一致）。
    """
    rng = random.Random(args.random_seed)
    random_arms: list[dict[str, Any]] = []
    for _ in range(args.n_random):
        rlegs = [sample_expression(rng) for _ in range(n_legs)]
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
    return random_arms


def _random_best_obj(random_arms: list[dict]) -> Optional[float]:
    return max(
        (
            a["best"]["reading"]["objective"]
            for a in random_arms
            if a["best"] is not None
        ),
        default=None,
    )


def _run_study(
    args: Any,
    legs: list[str],
    legs_source: str,
    mining: Window,
    judgment: Optional[Window],
    exit_spec: dict,
    codes: list[str],
    runner: CellRunner,
    *,
    v0_runner: Optional[CellRunner] = None,
    coarse_runner: Optional[CellRunner] = None,
    coarse_codes: Optional[list[str]] = None,
) -> Optional[dict]:
    """评估编排主体；全部格子失败 → None（调用方非零退出不落盘）。

    两阶段（--two-stage，v0.231）：阶段 1 粗筛宇宙 × 全权重格 × 全对照臂，
    按挖掘窗 objective 取 top K 基因组（对照臂不占名额，只作读数背景）；
    阶段 2 仅晋级基因组 × 原宇宙终筛——对照臂（等权/单腿/随机/s_shape/V0）
    在终筛宇宙**重跑**（口径一致才有对照意义）；灵敏度/随机对照判定/双窗
    复测都在阶段 2（与单阶段口径一致）。单阶段（默认）行为逐位不变。
    """
    levels = tuple(float(x) for x in args.lattice_levels)
    lattice = weight_lattice(len(legs), levels, max_combos=args.max_combos)
    exit_params = dict(exit_spec["params"])
    arms_ref = baseline_arms(len(legs))

    two_stage_block: Optional[dict[str, Any]] = None
    if args.two_stage:
        assert coarse_runner is not None and coarse_codes is not None
        # ---- 阶段 1：粗筛宇宙 × 全权重格 × 全对照臂 ----
        s1_rows = _eval_lattice(
            coarse_runner,
            legs,
            lattice,
            args.gate,
            exit_params,
            args.rank_window,
            mining,
        )
        survivors = _top_k_rows(s1_rows, args.stage1_top_k)
        s1_random = _eval_random_arms(
            coarse_runner, lattice, len(legs), args, exit_params, mining
        )
        s1_s_shape = coarse_runner(
            "s_shape", args.gate, exit_params, start=mining.start, end=mining.end
        )
        s1_v0 = v0_runner(start=mining.start, end=mining.end) if v0_runner else None
        if not survivors:
            print(
                "[ERR] 阶段 1 全部格子失败或读数缺失（粗筛宇宙/数据源有问题？）——"
                "空产物会被误读成「基因组全灭」，拒绝落盘",
                file=sys.stderr,
            )
            return None
        s1_equal = _find_by_weights(s1_rows, arms_ref["equal"])
        two_stage_block = {
            "coarse_n": len(coarse_codes),
            "coarse_digest": hashlib.sha1(
                ",".join(coarse_codes).encode("utf-8")
            ).hexdigest()[:12],
            "stage1_top_k": args.stage1_top_k,
            "stage1_cells": len(s1_rows)
            + sum(a["n_cells"] for a in s1_random)
            + 1
            + (1 if v0_runner else 0),
            "stage1_survivors": [
                {"weights": r["weights"], "stage1_objective": r["reading"]["objective"]}
                for r in survivors
            ],
            "stage1_rows": s1_rows,
            "stage1_random": s1_random,
            "stage1_arms": {
                "equal_objective": (
                    s1_equal["reading"].get("objective")
                    if s1_equal and s1_equal.get("reading")
                    else None
                ),
                "s_shape_objective": (
                    s1_s_shape.get("objective") if s1_s_shape else None
                ),
                "random_best_objective": _random_best_obj(s1_random),
                "v0_objective": s1_v0.get("objective") if s1_v0 else None,
            },
        }
        # ---- 阶段 2：晋级基因组 ∪ 对照权重（未晋级部分）× 原宇宙终筛 ----
        survivor_weights = [tuple(r["weights"]) for r in survivors]
        missing_ctls: list[tuple[float, ...]] = [
            w for w in arms_ref.values() if w not in survivor_weights
        ]
        survivor_rows = _eval_lattice(
            runner,
            legs,
            survivor_weights,
            args.gate,
            exit_params,
            args.rank_window,
            mining,
        )
        ctl_rows = (
            _eval_lattice(
                runner,
                legs,
                missing_ctls,
                args.gate,
                exit_params,
                args.rank_window,
                mining,
            )
            if missing_ctls
            else []
        )
        mining_rows = survivor_rows + ctl_rows  # 报告序：晋级组在前，对照重跑在后
        top = _best(survivor_rows)  # top 只从晋级基因组出（对照重跑不占名额）
        stage2_genome_cells = len(survivor_rows) + len(ctl_rows)
    else:
        mining_rows = _eval_lattice(
            runner, legs, lattice, args.gate, exit_params, args.rank_window, mining
        )
        top = _best(mining_rows)
        stage2_genome_cells = len(mining_rows)
    if top is None:
        print(
            "[ERR] 全部格子失败或读数缺失（数据源/窗口有问题？）——"
            "空产物会被误读成「基因组全灭」，拒绝落盘",
            file=sys.stderr,
        )
        return None

    equal_row = _find_by_weights(mining_rows, arms_ref["equal"])
    single_rows = [
        (name, _find_by_weights(mining_rows, w))
        for name, w in arms_ref.items()
        if name != "equal"
    ]
    s_shape_reading = runner(
        "s_shape", args.gate, exit_params, start=mining.start, end=mining.end
    )
    s_shape_block: dict[str, Any] = {"reading": s_shape_reading}
    if s_shape_reading is None:
        # 失败语义区分（v0.234）：「合法空」（empty_result：门槛过严致 0 信号，
        # 空结果护栏 fail-closed）与「格子失败」（cell_failed）必须分清——
        # r34_v1 的 s_shape 臂 exit=2×2 即前者（s_star≥70 ∧ j_low 超卖池近互斥）。
        s_shape_block["root_cause"] = _root_cause_for(runner, "s_shape", mining)

    # 随机对照臂（终筛宇宙；两阶段时 = 阶段 2 重跑）
    random_arms = _eval_random_arms(
        runner, lattice, len(legs), args, exit_params, mining
    )
    random_best_obj = _random_best_obj(random_arms)

    # V0 对照臂（--v0-arm；两阶段时 = 阶段 2 重跑）
    v0_reading = v0_runner(start=mining.start, end=mining.end) if v0_runner else None

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

    # V0 对照判定读数（不进预注册判据 C1~C5，只作对照块——判据定义不动）
    vs_v0 = None
    if v0_reading:
        v0_obj = v0_reading.get("objective")
        v0_margin = v0_reading.get("margin")
        vs_v0 = {
            "delta_objective": (top_obj - v0_obj if v0_obj is not None else None),
            "delta_margin": (
                top_margin - v0_margin
                if top_margin is not None and v0_margin is not None
                else None
            ),
        }

    if args.v0_arm:
        v0_block: dict[str, Any] = {
            "status": "run" if v0_reading else "failed",
            "vehicle": "evaluate_trades(collect_all) + asof_technical_score "
            "+ simulate_portfolio_topn（与 cell 同函数同公式；"
            "scorer 槽位 = V0 live 技术分）",
            "reading": v0_reading,
            "notes": [
                "V0 评分指标 warmup 限于研究窗口（cell 同款 _load_one_bars "
                "count/start/end）；live 链全历史（count=100000）口径的残差"
                "如实注记——同窗同宇宙同出场档的对比成立，绝对值不可与 live 互引",
            ],
        }
        if not v0_reading:
            v0_block["reason"] = (
                "执行器返回空（regime/指数/个股数据缺失？见 stderr WARN）"
            )
    else:
        v0_block = {
            "status": "off",
            "reason": "未启用（--v0-arm 开启）。v0.230 的 deferred 已于 v0.231 "
            "实现为独立载体实跑；生产机跑数加 --v0-arm",
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
            "two_stage": bool(args.two_stage),
            "coarse_sample": args.coarse_sample,
            "stage1_top_k": args.stage1_top_k,
            "quick": bool(args.quick),
            "v0_arm": bool(args.v0_arm),
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
            "s_shape": s_shape_block,
            "random": random_arms,
            "v0": v0_block,
        },
        "two_stage": (
            {
                **two_stage_block,
                "stage2_cells": stage2_genome_cells
                + sum(a["n_cells"] for a in random_arms)
                + 1
                + args.sens_arms
                + (3 if judgment else 0)
                + (1 if v0_runner else 0),
            }
            if two_stage_block is not None
            else None
        ),
        "top_genome": {
            "weights": top["weights"],
            "expr": top["expr"],
            "mining": top["reading"],
            "judgment": judgment_block["top"] if judgment_block else None,
            "vs_v0": vs_v0,
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
        # 失败格现场（v0.234）：子进程日志尾段 + 根因分类（empty_result=合法空 /
        # cell_failed=格子失败）——串行透传时代「failed 无文本」的洞已补
        "cell_failures": [
            {"source": "coarse", **f}
            for f in (
                _cell_failures_report(coarse_runner)
                if args.two_stage and coarse_runner is not None
                else []
            )
        ]
        + [{"source": "final", **f} for f in _cell_failures_report(runner)],
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
    if rep["config"].get("mode") in ("v0_lattice", "v0_lattice_addon"):
        _print_summary_v0l(rep)
        return
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
    s_arm = rep["arms"]["s_shape"]
    s_line = _arm_line("s_shape 参照·挖掘", s_arm["reading"])
    if s_arm["reading"] is None and s_arm.get("root_cause"):
        cause = {
            "empty_result": "合法空（该宇宙×窗内 0 信号：s_shape 可买阈值 s_star≥70 "
            "与 j_low 超卖池近互斥，空结果护栏 fail-closed）",
            "cell_failed": "格子失败（现场见报告 cell_failures 块）",
        }.get(s_arm["root_cause"], s_arm["root_cause"])
        s_line += f"  ⚠️ {cause}"
    print(s_line)
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
    v0 = rep["arms"]["v0"]
    if v0["status"] == "run":
        print(_arm_line("V0 对照·挖掘", v0["reading"]))
        vs = rep["top_genome"].get("vs_v0") or {}
        print(
            f"  top vs V0：Δobjective {_fmt(vs.get('delta_objective'))}"
            f"｜Δmargin {_fmt(vs.get('delta_margin'))}"
        )
    elif v0["status"] == "failed":
        print(f"  V0 对照：⚠️ 执行失败（{v0.get('reason', '')}）")
    else:
        print("  V0 臂：off（--v0-arm 开启；v0.231 已实现独立载体实跑）")
    ts = rep.get("two_stage")
    if ts:
        print(
            f"  两阶段：阶段1 粗筛 {ts['coarse_n']} 只×{ts['stage1_cells']} 格 "
            f"→ 晋级 {len(ts['stage1_survivors'])} 基因组；"
            f"阶段2 终筛 ×{ts['stage2_cells']} 格"
        )


def _print_summary_v0l(rep: dict[str, Any]) -> None:
    """v0-lattice 模式的 stdout 汇总（读数口径：top_n 选中子集；池读数另列）。"""
    w = rep["windows"]
    cfg = rep["config"]
    print(
        f"\n[R36-P3] V0 调权格裁决（v0-lattice）｜挖掘窗 "
        f"{w['mining']['start']}~{w['mining']['end']}"
        + (
            f"｜判定窗 {w['judgment']['start']}~{w['judgment']['end']}"
            if w["judgment"]
            else ""
        )
        + f"｜宇宙 {rep['universe']['n_codes']} 只 digest={rep['universe']['digest']}"
    )
    print(
        f"  腿轴({rep['lattice']['n_legs']})=V0 计分键｜倍率格 "
        f"{rep['lattice']['n_combos']} 组合（max {cfg['max_combos']}）"
        f"｜gate={cfg['gate']}｜出场={cfg['exit']['name']}｜top_n={cfg['top_n']}"
    )
    top = rep["top_genome"]
    if rep["config"].get("mode") == "v0_lattice_addon":
        addon = top.get("addon")
        addon_txt = (
            f"{addon['expr'][:44]} ×λ={addon['lambda']:g}"
            if addon
            else "无腿基准基因组（λ=0，加腿全输）"
        )
        print(f"  ─ 骨架加腿模式（R36 思路二）：top = {addon_txt}")
    mult = top["multipliers"]
    boosted = {k: v for k, v in mult.items() if v > 1.0}
    reduced = {k: v for k, v in mult.items() if 0.0 < v < 1.0}
    off = [k for k, v in mult.items() if v == 0.0]
    parts = []
    if boosted:
        parts.append(
            "加权 " + " ".join(f"{k}×{_fmt_weight(v)}" for k, v in boosted.items())
        )
    if reduced:
        parts.append(
            "降权 " + " ".join(f"{k}×{_fmt_weight(v)}" for k, v in reduced.items())
        )
    if off:
        parts.append(
            f"关腿 {len(off)} 条" + (f"（{'/'.join(off)}）" if len(off) <= 6 else "")
        )
    nz_txt = "｜".join(parts) if parts else "（全 1 = live 默认权重）"
    print(f"  ─ top 倍率格: {nz_txt}")
    print(_arm_line("top·挖掘", top["mining"]))
    eq = rep["arms"]["equal_weight"]
    print(_arm_line("等倍率基准(=V0)·挖掘", eq["reading"] if eq else None))
    pool = rep["pool_baseline"]["mining"]
    if pool:
        print(
            f"  {'全候选池·挖掘（权重不变量）':<22} margin={_fmt(pool.get('margin'))} "
            f"wr={_fmt(pool.get('win_rate'), pct=True)} "
            f"payoff={_fmt(pool.get('payoff_ratio'))} n={pool.get('n', '-')}"
        )
    singles = [
        (s["leg"], s["row"]["reading"])
        for s in rep["arms"]["single_legs"]
        if s["row"] and s["row"].get("reading")
    ]
    if singles:
        b_leg, b_read = max(
            singles, key=lambda x: x[1].get("objective") or float("-inf")
        )
        print(_arm_line(f"单腿最佳({b_leg})·挖掘", b_read))
    rc = rep["random_control"]
    mark = {
        "pass": "✅ 打过",
        "suspect": "⚠️ 筛选假象嫌疑",
        "indeterminate": "⚠️ 随机臂读不出",
    }[rc["verdict"]]
    print(
        _arm_line("随机 DSL 臂最佳·挖掘", {"objective": rc["random_best_objective"]})
        + f" ⇒ {mark}"
    )
    sens = rep["sensitivity"]
    print(
        f"  灵敏度 ±{sens['pct']:.0%} ×{sens['n_arms']}：翻转 {sens['flips']} "
        + ("✅" if sens["flips"] == 0 else "⚠️")
    )
    if rep["judgment"]:
        print(_arm_line("top·判定窗", rep["judgment"]["top"]))
        print(_arm_line("等倍率基准·判定窗", rep["judgment"]["equal"]))
        c2 = rep["criteria_readings"]["R34-C2"]
        print(
            f"  Δmargin（top−基准）：挖掘 {_fmt(c2['delta_mining'])}"
            f"｜判定 {_fmt(c2['delta_judgment'])}"
        )
    vs = top.get("vs_v0") or {}
    if vs:
        print(
            f"  top vs V0（=等倍率基准）：Δobjective {_fmt(vs.get('delta_objective'))}"
            f"｜Δmargin {_fmt(vs.get('delta_margin'))}"
        )


def main(
    argv: Optional[list[str]] = None,
    *,
    cell_runner: Optional[CellRunner] = None,
    cell_runner_coarse: Optional[CellRunner] = None,
    v0_runner: Optional[CellRunner] = None,
    v0l_collector: Optional[Callable[[Window], Optional[list[dict]]]] = None,
) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    # --quick 语义糖：未显式给值的 --n-random/--max-combos 落试跑档（显式给值优先）
    if args.n_random is None:
        args.n_random = 1 if args.quick else 3
    if args.max_combos is None:
        args.max_combos = 24 if args.quick else 64
    if args.max_combos < 1:
        ap.error("--max-combos 必须 >= 1")
    if args.two_stage:
        if args.coarse_sample < 1:
            ap.error("--coarse-sample 必须 >= 1")
        if args.stage1_top_k < 1:
            ap.error("--stage1-top-k 必须 >= 1")
    levels_raw = args.lattice_levels
    try:
        args.lattice_levels = tuple(
            float(x) for x in str(levels_raw).split(",") if str(x).strip()
        )
        weight_lattice(1, args.lattice_levels)  # 形态预检（整数刻度/非负/非全零）
    except ValueError as exc:
        ap.error(f"--lattice-levels 形态非法: {levels_raw!r}（{exc}）")
    if args.v0_lattice:
        if args.legs or args.legs_file:
            ap.error(
                "--v0-lattice 与 --legs/--legs-file 互斥（腿轴固定为 V0 计分键 "
                "CONTRIB_LEG_KEYS 权威清单）"
            )
        if args.two_stage:
            ap.error(
                "--v0-lattice 与 --two-stage 互斥（collect/score 拆分后逐格只是"
                "重打分，两阶段没有降本对象）"
            )
        if args.v0_arm:
            ap.error("--v0-lattice 模式下等倍率格即 V0 臂，--v0-arm 冗余")
        if args.max_legs < 1:
            ap.error("--max-legs 必须 >= 1（v0-lattice 模式下约束随机 DSL 臂腿数）")
        _resolve_addon(args, ap)  # 骨架加腿清单校验（无 --addon-leg 时空清单直过）
        from custos.research.score_calibration_study import (  # noqa: PLC0415
            CONTRIB_LEG_KEYS,
        )

        legs = list(CONTRIB_LEG_KEYS)
        legs_source = "v0_contrib_leg_keys(score_calibration_study 权威清单)"
        _validate_v0_legs(legs, ap)
    else:
        if args.addon_leg:
            ap.error(
                "--addon-leg 须配 --v0-lattice（骨架=V0 等权，DSL 模式无骨架可加）"
            )
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
    if args.v0_lattice:
        evaluator = _V0LatticeEvaluator(args, codes, exit_spec, collector=v0l_collector)
        rep = _run_v0_lattice_study(
            args,
            legs,
            legs_source,
            mining,
            judgment,
            exit_spec,
            codes,
            runner,
            evaluator,
        )
    else:
        coarse_runner_final: Optional[CellRunner] = None
        coarse_codes: Optional[list[str]] = None
        if args.two_stage:
            # 阶段 1 粗筛宇宙：从终筛宇宙同 seed 抽样（≥宇宙时退化为全量并注记）
            coarse_codes = bt.sample_codes(
                codes, min(args.coarse_sample, len(codes)), seed=args.universe_seed
            )
            if len(coarse_codes) == len(codes):
                print(
                    f"[WARN] 粗筛抽样数 {args.coarse_sample} ≥ 宇宙 {len(codes)} 只——"
                    "阶段 1 退化为全量（两阶段无降本效果，仅供口径验证）",
                    file=sys.stderr,
                )
            coarse_path = out_dir / f"_codes_coarse__{tag}.txt"
            coarse_path.write_text("\n".join(coarse_codes) + "\n", encoding="utf-8")
            coarse_runner_final = (
                cell_runner_coarse
                or cell_runner
                or _make_cell_runner(
                    args, str(coarse_path), out_dir / "grid_cells_coarse"
                )
            )
        v0_runner_final = v0_runner
        if v0_runner_final is None and args.v0_arm:
            v0_runner_final = _make_v0_runner(args, codes, exit_spec)
        rep = _run_study(
            args,
            legs,
            legs_source,
            mining,
            judgment,
            exit_spec,
            codes,
            runner,
            v0_runner=v0_runner_final,
            coarse_runner=coarse_runner_final,
            coarse_codes=coarse_codes,
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
