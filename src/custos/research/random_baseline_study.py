#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""随机 DSL 表达式 baseline 裁决实验（TODO #71 / R32 结论：冒烟三门读数无对照）。

问题：进化引擎冒烟里 LLM 候选的 IC 门过门率（r1 67% / r3 100%）没有对照组 ——
若**同 DSL 空间随机采样**的表达式过门率接近 LLM 实测，则「LLM 假设生成有增量」
暂无证据。本工具就是那个对照组载体：同 DSL 文法随机采 N 条表达式，过同一套
judge_mining 确定性门（n_days / RankIC / ICIR / R3 半窗同正），报过门率。

纯确定性、不烧 token（无 LLM 调用）。采样分布口径见
``evolution/random_expr.py`` 模块 docstring（可复现）。

用法（在有本地通达信日线的机器上跑）::

    uv run python -m custos.research random_baseline \
        --mining-start 2018-01-01 --mining-end 2020-12-31 \
        --universe-sample 300 --count 2500 --n 12 --tag r1_baseline

产物：{out_dir}/{tag}/_random_baseline__{tag}.json（schema 由
tests/test_random_baseline_study.py 钉住）。
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# GBK（cp936）终端/管道打不了 ⚠️/⛔ 等符号 —— 不 reconfigure 会 UnicodeEncodeError
# 直接退出。惯例同 evolution_loop / backtest_factors（hasattr 守卫）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from custos.core.paths import LOGS  # noqa: E402
from custos.research import backtest_factors as bt  # noqa: E402
from custos.research.evolution import operators  # noqa: E402
from custos.research.evolution.expr_dsl import complexity, violations  # noqa: E402
from custos.research.evolution.ic_eval import (  # noqa: E402
    evaluate_expression_with_series,
)
from custos.research.evolution.random_expr import sample_expression  # noqa: E402

OUTDIR = LOGS / "random_baseline"

DEFAULT_SEED = 20260911  # 表达式采样种子
DEFAULT_UNIVERSE_SEED = 42  # 宇宙抽样种子（R32 smoke_r1 口径；与表达式种子分开）
SCHEMA_VERSION = 1


def _build_parser() -> argparse.ArgumentParser:
    """全部 CLI 参数定义（返回未解析的 parser）。

    ⚠️ add_argument 定义必须留在**本文件**内：`research/__main__.py._modes()`
    用 AST 解析本文件找 store_true 开关生成模式清单。互斥校验在 main 里
    parse 之后做（fail-closed）。
    """
    ap = argparse.ArgumentParser(
        description="随机 DSL 表达式 baseline 裁决实验（R32 / TODO #71；纯确定性）"
    )
    ap.add_argument("--n", type=int, default=12, help="采样表达式条数（默认 12）")
    ap.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="表达式采样种子（可复现）"
    )
    ap.add_argument("--mining-start", required=True, help="挖掘窗起点 YYYY-MM-DD")
    ap.add_argument("--mining-end", required=True, help="挖掘窗终点 YYYY-MM-DD")
    ap.add_argument("--horizon", type=int, default=5, help="前向收益 horizon（默认 5）")
    ap.add_argument("--codes", default="", help="逗号分隔代码（宇宙解析的兜底分支）")
    ap.add_argument("--codes-file", default="", help="从文件读代码（钉死宇宙，优先）")
    ap.add_argument("--universe-sample", type=int, default=0, help="全市场抽样 N 只")
    ap.add_argument(
        "--universe-local",
        action="store_true",
        help="用本地 vipdoc 代码清单作抽样母体（同 backtest_factors）",
    )
    ap.add_argument(
        "--universe-seed",
        type=int,
        default=DEFAULT_UNIVERSE_SEED,
        help="宇宙抽样种子（默认 42；与表达式 --seed 分开，两个维度独立可复现）",
    )
    ap.add_argument(
        "--count", type=int, default=0, help="每票加载 K 线根数（0=loader 默认）"
    )
    ap.add_argument("--tag", default="", help="运行标识（默认时间戳）；产物目录名")
    ap.add_argument(
        "--out-dir", default="", help=f"产物根目录（默认 {OUTDIR}），tag 作子目录"
    )
    ap.add_argument(
        "--llm-pass-rate",
        type=float,
        default=None,
        help="LLM 实测过门率参考线（仅写进 summary 供对照，不参与任何计算）",
    )
    return ap


def _valid_date(s: str) -> bool:
    try:
        from datetime import date  # noqa: PLC0415

        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def _resolve_universe(args: Any, ap: argparse.ArgumentParser) -> list[str]:
    """宇宙解析复用 backtest_factors（--universe-seed 是本工具专属的抽样种子）。"""
    ns = argparse.Namespace(
        codes_file=args.codes_file,
        universe_local=args.universe_local,
        universe_sample=args.universe_sample,
        seed=args.universe_seed,
        codes=args.codes,
    )
    return bt._resolve_universe(ns, ap)


def _evaluate_all(exprs: list[str], bars: dict, args: Any) -> list[dict[str, Any]]:
    """逐表达式：合法性断言 → 挖掘窗评估 → judge_mining 确定性门 → 记录行。"""
    rows = []
    for expr in exprs:
        comp = complexity(expr)
        assert not violations(comp), (
            f"采样器产出了违规表达式（genome 层 bug）: {expr!r} {violations(comp)}"
        )
        stats, ic_series = evaluate_expression_with_series(
            expr,
            bars,
            start=args.mining_start,
            end=args.mining_end,
            horizon=args.horizon,
        )
        decision, reasons = operators.judge_mining(
            stats, comp, rank_ic_series=ic_series
        )
        m1, m2 = operators._half_window_means(ic_series)
        rows.append(
            {
                "expression": expr,
                "decision": decision,
                "reasons": reasons,
                "rank_ic_mean": stats.rank_ic_mean,
                "rank_icir": stats.rank_icir,
                "n_days": stats.n_days,
                "half_mean_1": m1,
                "half_mean_2": m2,
            }
        )
    return rows


def _print_lines(rows: list[dict[str, Any]], llm_ref: float | None) -> None:
    """每条一行 + 末尾 pass_rate 与 LLM 参考线对比。"""
    for r in rows:
        reasons = "" if r["decision"] == "pass" else f"（{'；'.join(r['reasons'])}）"
        print(
            f"[rand] {r['decision']:<4} RankIC={r['rank_ic_mean']:+.4f} "
            f"ICIR={r['rank_icir']:+.3f} 半窗={r['half_mean_1']:+.4f}/"
            f"{r['half_mean_2']:+.4f} {r['expression'][:64]}{reasons}"
        )
    n_pass = sum(1 for r in rows if r["decision"] == "pass")
    rate = n_pass / len(rows) if rows else 0.0
    ref = (
        f"；LLM 参考线 {llm_ref:.0%} → 差值 {rate - llm_ref:+.0%}"
        if llm_ref is not None
        else ""
    )
    print(f"\n[rand] 随机 baseline 过门率 {rate:.0%}（{n_pass}/{len(rows)}）{ref}")
    print("（若随机过门率接近 LLM 实测，则 LLM 假设生成无增量证据 —— R32/#71）")


def _validate(args: Any, ap: argparse.ArgumentParser) -> None:
    for name in ("mining_start", "mining_end"):
        if not _valid_date(getattr(args, name)):
            ap.error(
                f"--{name.replace('_', '-')} 须为 YYYY-MM-DD: {getattr(args, name)}"
            )
    if args.mining_start > args.mining_end:
        ap.error(f"挖掘窗倒挂: {args.mining_start} > {args.mining_end}")
    if args.n < 1:
        ap.error("--n 必须 >= 1")


def _write(args: Any, codes: list[str], tag: str, rows: list[dict[str, Any]]) -> Path:
    """组装 summary 并落盘（allow_nan：IC 读数 NaN 是合法研究读数）。"""
    n_pass = sum(1 for r in rows if r["decision"] == "pass")
    summary = {
        "version": SCHEMA_VERSION,
        "tag": tag,
        "seed": args.seed,
        "n": args.n,
        "window": {"start": args.mining_start, "end": args.mining_end},
        "horizon": args.horizon,
        "universe": {
            "n_codes": len(codes),
            "digest": hashlib.sha1(",".join(codes).encode("utf-8")).hexdigest()[:12],
            "source": _universe_source(args),
        },
        "per_expression": rows,
        "pass_count": n_pass,
        "pass_rate": n_pass / len(rows) if rows else 0.0,
        "llm_pass_rate_ref": args.llm_pass_rate,
    }
    out_dir = (Path(args.out_dir) if args.out_dir else OUTDIR) / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_random_baseline__{tag}.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, allow_nan=True)
    return out


def main(
    argv: Optional[list[str]] = None,
    loader: Optional[Callable[[list[str], int], dict]] = None,
) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    _validate(args, ap)

    codes = _resolve_universe(args, ap)
    load = (
        loader
        if loader is not None
        else functools.partial(
            bt._load_bars_local, start=None, end=None, allow_tail_clip=False
        )
    )
    bars = load(codes, args.count)
    if not bars:
        # 空结果护栏（同 backtest_factors._empty_result_guard 语义）：0 数据
        # → 非零退出且不写产物（空 baseline 会被误读成「随机全灭」）。
        print(
            "[ERR] 未加载到任何 K 线（数据源/代码列表/日期区间有问题？），拒绝运行",
            file=sys.stderr,
        )
        return 2

    tag = args.tag or _now_tag()
    rng = random.Random(args.seed)
    rows = _evaluate_all([sample_expression(rng) for _ in range(args.n)], bars, args)
    _print_lines(rows, args.llm_pass_rate)
    out = _write(args, codes, tag, rows)
    print(f"\n[INFO] 汇总 → {out}")
    return 0


def _now_tag() -> str:
    from datetime import datetime  # noqa: PLC0415

    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _universe_source(args: Any) -> str:
    if args.codes_file:
        return f"codes_file({Path(args.codes_file).name})"
    if args.codes:
        return "codes(内联)"
    src = "local_vipdoc" if args.universe_local else "online_get_stock_list"
    return f"{src} sample={args.universe_sample} seed={args.universe_seed}"


if __name__ == "__main__":
    raise SystemExit(main())
