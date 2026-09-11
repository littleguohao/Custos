#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 因子进化循环入口（DSL 白名单 + 挖掘/判定双窗 + 轨迹池）。

用法（在有本地通达信日线的机器上跑；演示/无 LLM 环境用 --mock-llm）::

    uv run python -m custos.research evolution_loop \
        --direction 量价背离 --rounds 3 --candidates-per-round 2 \
        --mining-start 2023-01-01 --mining-end 2024-12-31 \
        --final-judge --judgment-start 2025-01-01 --judgment-end 2025-06-30 \
        --universe-sample 300 --seed 0 --tag demo

    # 演示（无 LLM 配置也能跑通全流程）：
    uv run python -m custos.research evolution_loop \
        --direction 动量 --mining-start 2023-01-01 --mining-end 2024-12-31 \
        --codes-file codes.txt --mock-llm --tag mock_demo

LLM 配置走环境变量 CUSTOS_LLM_BASE_URL / CUSTOS_LLM_API_KEY / CUSTOS_LLM_MODEL
（+可选 CUSTOS_LLM_TIMEOUT；--timeout 可覆盖）。未配置且未给 --mock-llm →
fail-closed 报错退出。

双窗物理隔离（双窗制度的核心，见 evolution/dual_window.py）：
  ① 数据加载后**立刻按 mining_end 截尾**一份副本给进化循环 —— 挖掘全程
     判定窗数据不在内存对象里；
  ② --final-judge 时**重新从磁盘加载**完整数据再切判定窗 —— 与挖掘用的
     截尾副本无任何共享对象，物理隔离强调到加载层；
  ③ --grid-judge 的三轴终审由 strategy_grid/backtest_factors **子进程**
     自行按判定窗加载数据，与挖掘期数据副本无共享（进程级隔离）。

反过拟合纪律（R12 判据纪律 / R24 pre2019 untouched 终审段）：挖掘窗与判定窗
只要与 pre2019 untouched 终审段（2010-01-01..2016-12-31）相交，CLI 直接硬拒绝
（exit 2）——该段是打分族唯一没碰过的终审窗，进化挖掘/判定一律不许碰。

产物：artifacts/logs/evolution/{tag}/trajectory_pool.json + _summary__{tag}.json。
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

# GBK（cp936）终端/管道打不了 ⇒/⚠️ 等符号 —— 不 reconfigure 会 UnicodeEncodeError。
# 惯例同 backtest_factors（hasattr 守卫：pytest 捕获替换过 stdout）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from custos.core.paths import BASE, LOGS  # noqa: E402
from custos.research import backtest_factors as bt  # noqa: E402
from custos.research.evolution.dual_window import (  # noqa: E402
    DualWindowResult,
    Window,
    validate_windows,
)
from custos.research.evolution.llm_client import (  # noqa: E402
    LLMClient,
    LLMConfig,
)
from custos.research.evolution.loop import (  # noqa: E402
    JointVerdict,
    LoopConfig,
    clip_tail,
    final_judgment,
    run_loop,
)
from custos.research.evolution.mock_llm import MockLLM  # noqa: E402
from custos.research.evolution.trajectory import TrajectoryPool  # noqa: E402

OUTDIR = LOGS / "evolution"

DEFAULT_SEED = 20260909  # 与 LoopConfig.seed 默认一致

# pre2019 untouched 终审段（打分族反过拟合纪律，R12 判据纪律 / R24-R30 沿用）：
# 2010-2016 段是唯一没被挖掘/调参碰过的终审窗（R22/R24 两轮候选全部死于该段
# 半窗翻转），进化挖掘/判定窗与之相交一律硬拒绝，保持其 untouched。
PRE2019_START = "2010-01-01"
PRE2019_END = "2016-12-31"


class _BudgetExceeded(RuntimeError):
    """--max-tokens-budget 超支：从 on_event 抛出，让循环提前收敛。"""


def _build_parser() -> argparse.ArgumentParser:
    """全部 CLI 参数定义（返回未解析的 parser）。

    ⚠️ add_argument 定义必须留在**本文件**内：`research/__main__.py._modes()`
    用 AST 解析本文件找 store_true 开关生成模式清单。互斥/必填校验在 main 里
    parse 之后做（fail-closed，见 main）。
    """
    ap = argparse.ArgumentParser(
        description="LLM 因子进化循环：DSL 白名单 + 挖掘/判定双窗 + 轨迹池"
    )
    ap.add_argument(
        "--direction",
        action="append",
        default=[],
        help="探索方向（一句话），可多次给出；至少一个",
    )
    ap.add_argument("--rounds", type=int, default=3, help="进化轮数（默认 3）")
    ap.add_argument(
        "--candidates-per-round", type=int, default=2, help="每方向每轮候选数（默认 2）"
    )
    ap.add_argument(
        "--mining-start", required=True, help="挖掘窗起点 YYYY-MM-DD（必填）"
    )
    ap.add_argument("--mining-end", required=True, help="挖掘窗终点 YYYY-MM-DD（必填）")
    ap.add_argument(
        "--judgment-start", default="", help="判定窗起点（仅 --final-judge 需要）"
    )
    ap.add_argument(
        "--judgment-end", default="", help="判定窗终点（仅 --final-judge 需要）"
    )
    ap.add_argument("--horizon", type=int, default=5, help="前向收益 horizon（默认 5）")
    ap.add_argument("--codes", default="", help="逗号分隔代码（宇宙解析的兜底分支）")
    ap.add_argument("--codes-file", default="", help="从文件读代码（钉死宇宙，优先）")
    ap.add_argument(
        "--universe-sample", type=int, default=0, help="全市场抽样 N 只（配合 --seed）"
    )
    ap.add_argument(
        "--universe-local",
        action="store_true",
        help="用本地 vipdoc 代码清单作抽样母体（同 backtest_factors）",
    )
    ap.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="抽样/父代选择种子（可复现）"
    )
    ap.add_argument(
        "--count",
        type=int,
        default=0,
        help="每票加载 K 线根数（0=loader 默认约 2000 根≈8 年，早期窗口需显式加大）",
    )
    ap.add_argument("--tag", default="", help="运行标识（默认时间戳）；产物目录名")
    ap.add_argument(
        "--final-judge",
        action="store_true",
        help="循环结束后对 top-N pass 轨迹跑判定窗双窗终审",
    )
    ap.add_argument(
        "--top-n", type=int, default=5, help="终审/汇总展示的轨迹数（默认 5）"
    )
    ap.add_argument(
        "--mock-llm",
        action="store_true",
        help="用内置确定性脚本化 proposer（演示/无 LLM 环境）",
    )
    ap.add_argument(
        "--timeout", type=int, default=None, help="LLM HTTP 超时秒数（覆盖环境变量）"
    )
    ap.add_argument(
        "--max-tokens-budget",
        type=int,
        default=0,
        help="LLM total_tokens 预算，超支提前收敛（0=不限）",
    )
    ap.add_argument(
        "--grid-judge",
        action="store_true",
        help="双窗终审之后，把 pass 表达式提交 strategy_grid 三轴（因子×止损×止盈）"
        "终审（隐含 --final-judge；需要判定窗参数）",
    )
    ap.add_argument(
        "--grid-exit-grid",
        default="",
        help="三轴终审的出场网格 JSON 文件（缺省用 strategy_grid 内置小网格）",
    )
    ap.add_argument(
        "--grid-max-runs",
        type=int,
        default=0,
        help="三轴终审的子进程预算透传（0=不透传，用 strategy_grid 默认）",
    )
    ap.add_argument(
        "--joint",
        action="store_true",
        help="联合演化第一档：基因组=（表达式×gate×出场参数），IC 过门后跑"
        "三轴单元格适应度（参数走确定性档位格点，LLM 不碰数值调参）",
    )
    ap.add_argument(
        "--joint-min-objective",
        type=float,
        default=0.0,
        help="joint 的三轴适应度阈值（objective 低于则 fail；默认 0.0）",
    )
    ap.add_argument(
        "--out-dir", default="", help=f"产物根目录（默认 {OUTDIR}），tag 作子目录"
    )
    return ap


def _valid_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def _validate_mining_window(args: Any, ap: argparse.ArgumentParser) -> None:
    for name in ("mining_start", "mining_end"):
        if not _valid_date(getattr(args, name)):
            ap.error(
                f"--{name.replace('_', '-')} 须为 YYYY-MM-DD: {getattr(args, name)}"
            )
    if args.mining_start > args.mining_end:
        ap.error(f"挖掘窗倒挂: {args.mining_start} > {args.mining_end}")


def _validate_judgment_window(args: Any, ap: argparse.ArgumentParser) -> None:
    if not (args.judgment_start and args.judgment_end):
        ap.error("--final-judge 需要 --judgment-start 与 --judgment-end")
    try:
        validate_windows(
            Window(args.mining_start, args.mining_end),
            Window(args.judgment_start, args.judgment_end),
        )
    except ValueError as exc:
        ap.error(str(exc))


def _overlaps_pre2019(start: str, end: str) -> bool:
    """闭区间相交判定（ISO 日期字符串序即时间序；端点相接也算碰）。"""
    return start <= PRE2019_END and end >= PRE2019_START


def _reject_pre2019(args: Any, ap: argparse.ArgumentParser) -> None:
    """pre2019 untouched 终审段硬拒绝：挖掘/判定窗与之相交 → ap.error（exit 2）。

    风格对齐 score_combo_search_study.py 的 pre2019 守卫（⛔ 反过拟合纪律）。
    """
    if _overlaps_pre2019(args.mining_start, args.mining_end):
        ap.error(
            "⛔ 反过拟合纪律：进化挖掘/判定不许碰 pre2019 untouched 终审段"
            "（2010-2016）——"
            f"挖掘窗 {args.mining_start}..{args.mining_end} 与之相交"
        )
    if args.final_judge and _overlaps_pre2019(args.judgment_start, args.judgment_end):
        ap.error(
            "⛔ 反过拟合纪律：进化挖掘/判定不许碰 pre2019 untouched 终审段"
            "（2010-2016）——"
            f"判定窗 {args.judgment_start}..{args.judgment_end} 与之相交"
        )


def _validate_args(args: Any, ap: argparse.ArgumentParser) -> None:
    """互斥/必填/窗口校验（fail-closed：一律 ap.error，exit 2）。"""
    if not args.direction:
        ap.error("至少需要一个 --direction（可多次给出）")
    if args.rounds < 1 or args.candidates_per_round < 1:
        ap.error("--rounds 与 --candidates-per-round 必须 >= 1")
    if args.top_n < 1:
        ap.error("--top-n 必须 >= 1")
    _validate_mining_window(args, ap)
    if args.grid_judge:
        args.final_judge = True  # --grid-judge 隐含双窗终审（三轴终审吃它的产出）
    if args.final_judge:
        _validate_judgment_window(args, ap)
    if (args.joint or args.grid_judge) and args.count <= 0:
        # R32 / TODO #69：单元格子进程不继承 loader 默认深度——不显式 --count 则
        # strategy_grid 默认 500 只回溯约两年，早窗口的格子被尾部截断护栏全灭。
        ap.error(
            "--joint/--grid-judge 必须显式 --count 盖住窗口"
            "（单元格子进程不继承 loader 默认深度，默认 500 ≈ 两年）"
        )
    _reject_pre2019(args, ap)


def _assemble_llm(args: Any, ap: argparse.ArgumentParser) -> Any:
    """LLM 装配：--mock-llm → 内置 MockLLM；否则环境变量，缺失 fail-closed。"""
    if args.mock_llm:
        return MockLLM()
    cfg = LLMConfig.from_env()
    if cfg is None:
        ap.error(
            "未配置 CUSTOS_LLM_BASE_URL/CUSTOS_LLM_API_KEY/CUSTOS_LLM_MODEL，"
            "或用 --mock-llm 演示"
        )
    assert cfg is not None  # ap.error 已 SystemExit，窄化类型
    if args.timeout is not None:
        cfg = replace(cfg, timeout=args.timeout)
    return LLMClient(cfg)


def _fmt_ic(v: Any) -> str:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(float(v)):
        return "-"
    return f"{float(v):+.4f}"


def _print_grid_summary(grid: dict[str, Any]) -> None:
    """三轴终审段（strategy_grid：因子×止损×止盈，判定窗口径）。"""
    if not grid or grid.get("skipped"):
        return
    print("\n== 三轴终审（strategy_grid：因子×止损×止盈，判定窗口径） ==")
    if grid.get("error"):
        print(f"⚠️ {grid['error']}")
    print(f"{'rank':>4} {'objective':>9} {'margin':>8} {'expR':>7}  表达式")
    for expr, row in grid.get("expressions", {}).items():
        print(
            f"{row.get('rank') or '-':>4} {_fmt_ic(row.get('objective')):>9} "
            f"{_fmt_ic(row.get('margin')):>8} {_fmt_ic(row.get('expectancy_R')):>7}"
            f"  {expr[:56]}"
        )


def _print_summary(
    pool: TrajectoryPool,
    top_n: int,
    dual: list[DualWindowResult],
    grid: dict[str, Any],
) -> None:
    """结尾汇总表（best top-N + 判定窗双窗终审 + 三轴终审列）。"""
    best = pool.best(top_n)
    joint = any(t.gate for t in best)  # 有 joint 轨迹 → 加基因组列
    print("\n== 进化汇总（best 按 rank_icir 降序） ==")
    if joint:
        print(f"{'决策':<6} {'RankIC':>8} {'ICIR':>8} {'objective':>9}  gate / 表达式")
    else:
        print(f"{'决策':<6} {'RankIC':>8} {'ICIR':>8}  表达式")
    for t in best:
        mm = t.mining_metrics
        line = (
            f"{t.decision:<6} {_fmt_ic(mm.get('rank_ic_mean')):>8} "
            f"{_fmt_ic(mm.get('rank_icir')):>8}"
        )
        if joint:
            line += f" {_fmt_ic(mm.get('objective')):>9}  {t.gate or '-'} /"
        print(f"{line}  {t.expression[:56]}")
    counts = _decision_counts(pool)
    print(f"\n池规模 {len(pool)}：pass {counts['pass']} / fail {counts['fail']}")
    if dual:
        print("\n== 判定窗终审（双窗） ==")
        print(f"{'通过':<4} {'判定RankIC':>10} {'判定ICIR':>9}  表达式 / 未过原因")
        for row in dual:
            r = _dual_of(row)  # joint 模式是 JointVerdict 包装
            mark = "✓" if r.passed else "✗"
            reasons = "" if r.passed else f"（{'；'.join(r.reasons)}）"
            print(
                f"{mark:<4} {_fmt_ic(r.judgment.rank_ic_mean):>10} "
                f"{_fmt_ic(r.judgment.rank_icir):>9}  {r.expression[:56]}{reasons}"
            )
    _print_grid_summary(grid)


def _decision_counts(pool: TrajectoryPool) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "pending": 0}
    for t in pool.all():
        counts[t.decision] = counts.get(t.decision, 0) + 1
    return counts


def _best_rows(pool: TrajectoryPool, top_n: int) -> list[dict]:
    return [
        {
            "id": t.id,
            "direction": t.direction,
            "phase": t.phase,
            "expression": t.expression,
            "hypothesis": t.hypothesis,
            "decision": t.decision,
            "rank_ic_mean": t.mining_metrics.get("rank_ic_mean"),
            "rank_icir": t.mining_metrics.get("rank_icir"),
            "gate": t.gate,  # joint 基因组分量（非 joint 为 "" / {} / None）
            "exit_params": dict(t.exit_params),
            "objective": t.mining_metrics.get("objective"),
            "parent_ids": list(t.parent_ids),
        }
        for t in pool.best(top_n)
    ]


def _make_on_event(args: Any, llm: Any) -> Callable[[dict], None]:
    """进度打印 + token 预算闸（超支 raise _BudgetExceeded 让循环提前收敛）。"""

    def _on_event(ev: dict) -> None:
        err = f" ⚠️{ev['error'][:80]}" if ev.get("error") else ""
        gate = f" {ev['gate']}" if ev.get("gate") else ""
        obj = (
            f" obj={_fmt_ic(ev['objective'])}"
            if ev.get("objective") is not None
            else ""
        )
        print(
            f"[evo] r{ev['round_i']}c{ev['cand_i']} {ev['direction']} "
            f"{ev['phase']:<9} {ev['decision']:<9} "
            f"RankIC={_fmt_ic(ev.get('rank_ic_mean')):>8}{gate}{obj} "
            f"{ev.get('expression', '')[:56]}{err}",
            flush=True,
        )
        used = int(getattr(llm, "total_tokens", 0))
        if args.max_tokens_budget and used > args.max_tokens_budget:
            raise _BudgetExceeded(
                f"token 预算 {args.max_tokens_budget} 已超（累计 {used}），提前收敛"
            )

    return _on_event


def _run_final_judge(
    args: Any,
    load: Callable[[list[str], int], dict],
    codes: list[str],
    pool: TrajectoryPool,
    cell_runner: Any,
) -> list[Any]:
    """判定窗终审：未开 --final-judge 返回 []；joint 时重跑判定窗单元格。"""
    if not args.final_judge:
        return []
    # 双窗物理隔离 ②：判定窗**重新从磁盘加载**完整数据再切片 —— 与挖掘用的
    # 截尾副本无任何共享内存对象（物理隔离强调到加载层）。
    full_bars = load(codes, args.count)
    return final_judgment(
        pool,
        full_bars,
        mining=Window(args.mining_start, args.mining_end),
        judgment=Window(args.judgment_start, args.judgment_end),
        top_n=args.top_n,
        horizon=args.horizon,
        cell_runner=cell_runner,
    )


# ---------- 三轴终审（--grid-judge：strategy_grid 子进程） ----------


def _dual_of(row: Any) -> DualWindowResult:
    """final_judgment 行统一取双窗部分（joint 模式是 JointVerdict 包装）。"""
    return row.dual if isinstance(row, JointVerdict) else row


def _make_cell_runner(
    args: Any, codes: list[str], out_dir: Path
) -> Callable[..., dict | None]:
    """构造 cell_runner（--joint 的循环内三轴适应度执行器，可 monkeypatch）。

    复用 strategy_grid 的单元格机制：每格 = 一次 backtest_factors --trade-sim
    子进程（scorer=expr:<expr>，gate/出场参数=基因组分量，窗口 start/end 透传，
    宇宙复用本 run codes）；out 目录 ``{out_dir}/grid_cells/``，cell_signature
    复用跳过免费获得（同签名格子不重跑）。子进程非零/结果缺 → None。
    ⚠️ 物理隔离：格子子进程自行按给定窗口加载数据，与挖掘期数据副本无共享。
    """
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    cells_dir = out_dir / "grid_cells"
    cells_dir.mkdir(parents=True, exist_ok=True)
    if args.codes_file:
        codes_file = args.codes_file
    else:  # 抽样/裸 codes → 落一份代码表钉死（同 --grid-judge 的宇宙口径）
        codes_path = cells_dir / f"_joint_codes__{out_dir.name}.txt"
        codes_path.write_text("\n".join(codes) + "\n", encoding="utf-8")
        codes_file = str(codes_path)

    def cell_runner(
        expr: str, gate: str, exit_params: dict, *, start: str, end: str
    ) -> dict | None:
        ns = argparse.Namespace(  # strategy_grid._cell_args/run_cell 的最小面
            cost_bps=25.0,  # strategy_grid 默认往返成本
            no_amv_pin=False,  # 0AMV 研究基底钉死（口径同 strategy_grid 默认）
            codes_file=codes_file,
            sample=0,
            start=start,
            end=end,
            count=args.count or 500,
            top_n=0,
            force=False,
            timeout=sg.CELL_TIMEOUT_S,
            universe_digest="",
        )
        cell = {
            "scorer": f"expr:{expr}",
            "gate": gate,
            "exit": "joint_cell",
            "params": dict(exit_params),
        }
        status, path, _log = sg.run_cell(ns, cell, cells_dir)
        if status == "failed" or path is None:
            return None
        row = sg.load_cell_row(cell, path, reused=(status == "reused"))
        return {
            "objective": sg.objective_of(row, sg.DEFAULT_OBJ_WEIGHTS),
            "margin": row.get("margin"),
            "expectancy_R": row.get("expectancy_R"),
            "cell_signature": _row_signature(row),
        }

    return cell_runner


def _row_signature(row: dict) -> str | None:
    """格子行的 cell_signature：从结果文件名尾段还原（``<名>__<sig>.json``）。"""
    name = str(row.get("result_file", ""))
    if "__" not in name:
        return None
    return Path(name).stem.rsplit("__", 1)[-1]


def _grid_rows_by_expr(
    report: dict, exprs: list[str]
) -> tuple[dict[str, dict], list[str]]:
    """每个表达式取排名最靠前的格子行；报告查无此行（格子失败/截断）进 missing。"""
    best: dict[str, dict] = {}
    for row in report.get("results", []):
        scorer = str(row.get("scorer", ""))
        if not scorer.startswith("expr:"):
            continue
        expr = scorer[len("expr:") :]
        if expr not in best or row.get("rank", 1 << 30) < best[expr].get(
            "rank", 1 << 30
        ):
            best[expr] = row
    out: dict[str, dict] = {}
    for expr in exprs:
        row = best.get(expr)
        if row is None:
            continue
        out[expr] = {
            "objective": row.get("objective"),
            "margin": row.get("margin"),
            "expectancy_R": row.get("expectancy_R"),
            "rank": row.get("rank"),
            "cell_signature": _row_signature(row),
        }
    return out, [e for e in exprs if e not in out]


def _grid_command(
    args: Any, out_dir: Path, exprs: list[str], codes: list[str], grid_tag: str
) -> list[str]:
    """拼 strategy_grid 子进程 CLI：scorers 用 expr: 形态；窗口 = 判定窗。"""
    sg_script = Path(bt.__file__).resolve().parent / "strategy_grid.py"
    cmd = [
        sys.executable,
        str(sg_script),
        "--scorers",
        ",".join(f"expr:{e}" for e in exprs),
        "--start",
        args.judgment_start,
        "--end",
        args.judgment_end,
        "--tag",
        grid_tag,
        "--out-dir",
        str(out_dir),
    ]
    # 宇宙 = 本 run 的 codes：--codes-file 直接透传；抽样/裸 codes 则落一份
    # 代码表到产物目录钉死（宇宙漂移 ⇒ 格子签名变 ⇒ 不误复用，同 strategy_grid
    # 的「隐式宇宙转显式」口径）。
    if args.codes_file:
        cmd += ["--codes-file", args.codes_file]
    else:
        codes_path = out_dir / f"_grid_codes__{grid_tag}.txt"
        codes_path.parent.mkdir(parents=True, exist_ok=True)
        codes_path.write_text("\n".join(codes) + "\n", encoding="utf-8")
        cmd += ["--codes-file", str(codes_path)]
    # --count 随窗口透传（R32 / TODO #69 缺口）：不转发则子进程默认 500，
    # 尾部只回溯约两年，早窗口的判定格会被尾部截断护栏 fail-closed 全灭。
    if args.count > 0:
        cmd += ["--count", str(args.count)]
    if args.grid_exit_grid:
        cmd += ["--exit-grid", args.grid_exit_grid]
    if args.grid_max_runs > 0:
        cmd += ["--max-runs", str(args.grid_max_runs)]
    return cmd


def _run_grid_judge(
    args: Any, out_dir: Path, dual: list[DualWindowResult], codes: list[str]
) -> dict[str, Any]:
    """--grid-judge：双窗 pass 的表达式提交 strategy_grid 三轴终审（子进程）。

    返回 summary 的 ``grid_judge`` 块。未开 / 双窗 0 pass → skipped=True 不
    spawn（此时已有真实产物，exit 仍 0）。
    ⚠️ 物理隔离：终审格子由 strategy_grid/backtest_factors 子进程自行按判定窗
    加载数据，与挖掘期用的截尾副本无任何共享对象（进程级隔离）。
    """
    if not args.grid_judge:
        return {"skipped": True, "reason": "未开 --grid-judge"}
    exprs = list(
        dict.fromkeys(_dual_of(r).expression for r in dual if _dual_of(r).passed)
    )
    if not exprs:
        return {"skipped": True, "reason": "双窗终审 0 pass，无可提交三轴终审的表达式"}
    grid_tag = f"{out_dir.name}__grid"
    cmd = _grid_command(args, out_dir, exprs, codes, grid_tag)
    print(f"[grid-judge] {len(exprs)} 个双窗 pass 表达式 → strategy_grid 三轴终审")
    rc = subprocess.run(cmd, cwd=str(BASE)).returncode
    report_path = out_dir / f"_ranked__{grid_tag}.json"
    block: dict[str, Any] = {
        "skipped": False,
        "tag": grid_tag,
        "returncode": rc,
        "report": str(report_path),
    }
    if rc != 0 or not report_path.exists():
        block["error"] = f"strategy_grid 退出码 {rc} 或报告缺失: {report_path.name}"
        return block
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows, missing = _grid_rows_by_expr(report, exprs)
    block["expressions"] = rows
    if missing:
        block["missing"] = missing
    return block


@dataclass(frozen=True)
class _RunResult:
    """main 尾部的产物汇总上下文（聚合原先 _write_summary 的 8 个散参）。

    tag 不单独存字段：产物目录即 out_dir = <root>/<tag>，tag 取 out_dir.name。
    """

    out_dir: Path
    args: Any
    cfg: LoopConfig
    pool: TrajectoryPool
    llm: Any
    dual: list[DualWindowResult]
    codes_digest: str


def _write_summary(res: _RunResult, grid: dict[str, Any]) -> Path:
    counts = _decision_counts(res.pool)
    tag = res.out_dir.name
    summary = {
        "tag": tag,
        "directions": list(res.args.direction),
        "config": asdict(res.cfg),
        "codes_digest": res.codes_digest,
        "pool_size": len(res.pool),
        "n_pass": counts["pass"],
        "n_fail": counts["fail"],
        "total_tokens": int(getattr(res.llm, "total_tokens", 0)),
        "mock_llm": bool(res.args.mock_llm),
        "best": _best_rows(res.pool, res.args.top_n),
        "final_judgment": [asdict(r) for r in res.dual],
        "grid_judge": grid,
    }
    res.out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = res.out_dir / f"_summary__{tag}.json"
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, allow_nan=True)
    return summary_path


def _report(res: _RunResult, grid: dict[str, Any]) -> None:
    """落 summary + 打印产物路径与结尾汇总表。"""
    summary_path = _write_summary(res, grid)
    print(f"\n[INFO] 轨迹池 → {res.out_dir / 'trajectory_pool.json'}")
    print(f"[INFO] 汇总 → {summary_path}")
    _print_summary(res.pool, res.args.top_n, res.dual, grid)


@dataclass(frozen=True)
class _Prepared:
    """_prepare 的产出聚合（main 的局部变量预算友好）。

    bars 在截尾后被 ``replace(prep, bars=None)`` 丢弃（双窗物理隔离 ①），
    故类型允许 None。
    """

    llm: Any
    codes: list[str]
    load: Callable[[list[str], int], dict]
    bars: dict | None


def _prepare(
    args: Any,
    ap: argparse.ArgumentParser,
    loader: Optional[Callable[[list[str], int], dict]],
) -> _Prepared:
    """LLM 装配 + 宇宙解析 + 数据加载。

    宇宙解析与本地加载**复用** backtest_factors 的既有实现（codes_file 钉死 >
    universe-sample/local 抽样 > --codes；为空 fail-closed ap.error）。
    """
    llm = _assemble_llm(args, ap)
    codes = bt._resolve_universe(args, ap)
    load = (
        loader
        if loader is not None
        else functools.partial(
            bt._load_bars_local, start=None, end=None, allow_tail_clip=False
        )
    )
    return _Prepared(llm, codes, load, load(codes, args.count))


@dataclass(frozen=True)
class _ExecCtx:
    """_execute 的入参聚合（避免 6+ 散参）。"""

    args: Any
    cfg: LoopConfig
    mining_bars: dict
    llm: Any
    pool: TrajectoryPool
    cell_runner: Any  # joint 的循环内三轴适应度执行器（非 joint 为 None）


def _execute(ctx: _ExecCtx) -> None:
    """跑进化循环；token 预算超支 → 警告后带着已落池的轨迹继续走收尾。"""
    try:
        run_loop(
            ctx.cfg,
            ctx.mining_bars,
            ctx.llm,
            ctx.pool,
            on_event=_make_on_event(ctx.args, ctx.llm),
            cell_runner=ctx.cell_runner,
        )
    except _BudgetExceeded as exc:
        print(f"[WARN] {exc}", file=sys.stderr)


def main(
    argv: Optional[list[str]] = None,
    loader: Optional[Callable[[list[str], int], dict]] = None,
) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    _validate_args(args, ap)
    prep = _prepare(args, ap, loader)
    if not prep.bars:
        print(
            "[ERR] 未加载到任何 K 线（数据源/代码列表/日期区间有问题？），拒绝运行",
            file=sys.stderr,
        )
        return 2

    tag = args.tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = (Path(args.out_dir) if args.out_dir else OUTDIR) / tag
    # 不存在 → 空池；损坏 → raise（fail-closed）
    pool = TrajectoryPool.load(out_dir / "trajectory_pool.json")

    # 双窗物理隔离 ①：加载后立刻按 mining_end 截尾一份副本给循环；全量 bars
    # 的引用当场丢弃（判定窗数据在挖掘阶段物理不在场）。
    mining_bars = clip_tail(prep.bars, args.mining_end)
    prep = replace(prep, bars=None)

    cfg = LoopConfig(
        directions=tuple(args.direction),
        rounds=args.rounds,
        candidates_per_round=args.candidates_per_round,
        mining_start=args.mining_start,
        mining_end=args.mining_end,
        horizon=args.horizon,
        seed=args.seed,
        run_tag=tag,
        joint=bool(args.joint),
        min_objective=args.joint_min_objective,
    )
    cell_runner = _make_cell_runner(args, prep.codes, out_dir) if args.joint else None
    pool_size_before = len(pool)  # 本 run 新增轨迹数的基线（空结果护栏用）
    _execute(_ExecCtx(args, cfg, mining_bars, prep.llm, pool, cell_runner))

    # 空结果护栏（对照 backtest_factors._empty_result_guard 语义）：本次运行什么都没
    # 产出（LLM 全挂等）→ 非零退出且不写产物；有 fail 轨迹属正常研究产出，落盘。
    # ⚠️ 必须对照循环前的池规模：从上一轮产物 load 回来的旧轨迹让 len(pool) > 0，
    # 但那不是本 run 的产出 —— 只看本 run 新增了几条。
    if len(pool) == pool_size_before:
        print(
            "[ERR] 本次进化循环 0 条新轨迹落池（LLM 不可用/全失败？）；拒绝落盘——"
            "空产物会被误读成'该方向无有效因子'。",
            file=sys.stderr,
        )
        return 2
    pool.save()

    dual = _run_final_judge(args, prep.load, prep.codes, pool, cell_runner)
    res = _RunResult(
        out_dir=out_dir,
        args=args,
        cfg=cfg,
        pool=pool,
        llm=prep.llm,
        dual=dual,
        codes_digest=f"{len(prep.codes)} 只",
    )
    _report(res, _run_grid_judge(args, out_dir, dual, prep.codes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
