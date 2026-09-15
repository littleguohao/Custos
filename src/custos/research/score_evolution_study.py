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


def main(
    argv: Optional[list[str]] = None,
    *,
    cell_runner: Optional[CellRunner] = None,
    cell_runner_coarse: Optional[CellRunner] = None,
    v0_runner: Optional[CellRunner] = None,
) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    # --quick 语义糖：未显式给值的 --n-random/--max-combos 落试跑档（显式给值优先）
    if args.n_random is None:
        args.n_random = 1 if args.quick else 3
    if args.max_combos is None:
        args.max_combos = 24 if args.quick else 64
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
            or _make_cell_runner(args, str(coarse_path), out_dir / "grid_cells_coarse")
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
