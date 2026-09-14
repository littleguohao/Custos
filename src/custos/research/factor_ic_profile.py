#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""因子 IC 画像（factor_ic_profile）：研究层**分诊镜**。

对注册 SCORERS 与 DSL 表达式，在统一宇宙/窗口上计算截面 RankIC/ICIR 与
horizon 衰减曲线，出**全因子可比表**。它回答「这个因子还有没有截面信号、
值不值得花 trade-sim 预算」——**不是晋级判据**：因子晋级 live 的判定永远走
双窗 + 三轴交易语义（R19：live 技术分对信号后涨幅无预测力，预测口径不能当
主判据；R21：富集 ≠ 可交易）。读数带幸存者偏差（R14），只作 L3− 分诊参考。

口径（全部复用既有地基，不另造）：

- 截面 IC：`evolution/ic_eval._ic_by_day`——逐截面日 score 与
  ``close[t+H]/close[t]-1`` 前向收益的 Spearman（rank_ic）+ Pearson（ic）双列；
  非有限值成对剔除、有效股票 <5 跳过、零方差日跳过（前向收益口径已钉死）。
- 聚合：`ic_eval.ic_stats_from_series`（mean / ICIR）；前后半窗均值复用
  `operators._half_window_means`（R3 半窗纪律在 IC 序列形态下的同族切法）。
- scorer 逐日分值：`scorer_score_series`——as-of 前缀切片调注册 scorer；
  ``_SCORER_PRECOMPUTE`` 有注册（b1_pullback/kdj_j/rsi_state 三键）走
  预计算旁路（每股一次，v0.212 口径），其余逐切片直算（O(n²)，研究侧求正确
  不求快，docstring 见函数）。
- DSL 表达式：`ic_eval.score_frame`（每股独立求值，rolling/shift 只看 ≤t）。
- 无未来函数：``--start/--end`` 切片发生在**任何计算之前**（ic_eval
  ``_slice_bars`` 同口径——滚动窗口的 warmup 从窗口起点重算）。

用法（在有本地通达信日线的机器上跑）::

    uv run python -m custos.research factor_ic_profile \
        --start 2022-01-01 --end 2024-07-31 \
        --universe-local --universe-sample 300 --tag ic_smoke

    uv run python -m custos.research factor_ic_profile \
        --start 2022-01-01 --end 2024-07-31 \
        --scorers s_shape,rsi_state --expr "MA(close,20)/close" --tag ic_mix

产物：{out_dir}/{tag}/_factor_ic_profile__{tag}.json（schema 由
tests/test_factor_ic_profile.py 钉住）+ stdout 全因子可比表。
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

# GBK（cp936）终端/管道打不了 ⚠️/⛔ 等符号 —— 不 reconfigure 会 UnicodeEncodeError。
# 惯例同 random_baseline_study / score_evolution_study（hasattr 守卫）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from custos.core.paths import LOGS  # noqa: E402
from custos.research import backtest_factors as bt  # noqa: E402
from custos.research.evolution import ic_eval, operators  # noqa: E402
from custos.research.evolution.expr_dsl import ExprError, parse  # noqa: E402

OUTDIR = LOGS / "factor_ic_profile"

SCHEMA_VERSION = 1
DEFAULT_UNIVERSE_SEED = 42  # 同 random_baseline_study 口径
DEFAULT_HORIZONS = "1,5,10,20"
DEFAULT_PRIMARY_HORIZON = 5


def _build_parser() -> argparse.ArgumentParser:
    """全部 CLI 参数定义（返回未解析的 parser）。

    ⚠️ add_argument 定义必须留在**本文件**内：`research/__main__.py._modes()`
    用 AST 解析本文件找 store_true 开关生成模式清单。互斥/必填校验在 main 里
    parse 之后做（fail-closed）。
    """
    ap = argparse.ArgumentParser(
        description="因子 IC 画像：SCORERS/DSL 表达式的截面 RankIC/ICIR 与 horizon "
        "衰减全因子可比表（分诊镜 L3−，非晋级判据）"
    )
    ap.add_argument(
        "--scorers",
        default="",
        help="逗号分隔的 SCORERS 键清单（默认全部 19 键；未知键 fail-closed）",
    )
    ap.add_argument(
        "--expr",
        action="append",
        default=[],
        help="DSL 表达式（可重复；走 ic_eval.score_frame 路径，与 --scorers 可混用）",
    )
    ap.add_argument(
        "--horizons",
        default=DEFAULT_HORIZONS,
        help=f"逗号分隔前向 horizon 清单（默认 {DEFAULT_HORIZONS}）",
    )
    ap.add_argument(
        "--primary-horizon",
        type=int,
        default=DEFAULT_PRIMARY_HORIZON,
        help=f"排序主键 horizon（默认 {DEFAULT_PRIMARY_HORIZON}；必须在 --horizons 内）",
    )
    ap.add_argument("--start", required=True, help="窗口起点 YYYY-MM-DD（必填）")
    ap.add_argument("--end", required=True, help="窗口终点 YYYY-MM-DD（必填）")
    # ---- 宇宙（参数面同 random_baseline_study）----
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
    ap.add_argument(
        "--count", type=int, default=0, help="每股加载 K 线根数（0=loader 默认）"
    )
    ap.add_argument("--tag", default="", help="运行标识（默认时间戳）；产物目录名")
    ap.add_argument(
        "--out-dir", default="", help=f"产物根目录（默认 {OUTDIR}），tag 作子目录"
    )
    return ap


# ---------------------------------------------------------------------------
# scorer 逐日分值序列构建器
# ---------------------------------------------------------------------------


def scorer_score_series(scorer_key: str, df: pd.DataFrame, code: str) -> pd.Series:
    """每股逐 bar 的 scorer 分值序列（index=date，float；不可评 = NaN）。

    - as-of 前缀切片 ``df.iloc[:i+1]`` 调 scorer 取 ``["score"]``；scorer 返回
      None / 抛异常 / 值 NaN·±inf → 该 bar NaN（不参与截面，口径同
      scorer_bridge 的末行 isnan/isinf 检查）；短数据段（< scorer 的 min 需求）
      留 NaN 是 scorers 自身返回 None 的自然结果，不特判；
    - ``_SCORER_PRECOMPUTE`` 有注册的走预计算旁路：每股一次 ``pre_fn(df)``，
      逐 bar 三参点查（v0.212 口径——只对从第 0 根开始的前缀切片有效，本函数
      恒如此；逐位等价由 tests/test_scorer_precompute_equivalence.py 钉住）；
      旁路返回 None（异常）自动回退逐切片直算，行为与旧路径一致；
    - 两参/三参 scorer 由 ``_dual_form_scorer`` 归一（引擎同款包装）；
    - **绝不 raise**（研究热循环惯例：一个 scorer 的异常不得炸掉整批画像）；
      未知 scorer_key → ValueError（fail-closed，CLI 层已校验，这里是双保险）。

    性能注记：无旁路的 scorer 是 O(n²) 逐切片重算（19 键里仅
    b1_pullback/kdj_j/rsi_state 有旁路）——分诊镜求正确不求快；要提速请先给
    该 scorer 登记预计算（等价性钉测先行），不要在这里抄近路。
    """
    scorer = bt.SCORERS.get(scorer_key)
    if scorer is None:
        raise ValueError(
            f"scorer 未注册: {scorer_key}（SCORERS: {sorted(bt.SCORERS)}）"
        )
    dual = bt._dual_form_scorer(scorer)
    pre = None
    pre_fn = bt._SCORER_PRECOMPUTE.get(scorer)
    if pre_fn is not None:
        try:
            pre = pre_fn(df)
        except Exception:  # noqa: BLE001
            pre = None  # 回退逐切片（引擎同语义）
    n = len(df)
    out = np.full(n, np.nan)
    for i in range(n):
        try:
            res = dual(df.iloc[: i + 1], code, pre)
            if res is None:
                continue
            v = res.get("score")
            if v is None:
                continue
            fv = float(v)
            if not math.isfinite(fv):
                continue
            out[i] = fv
        except Exception:  # noqa: BLE001
            continue
    return pd.Series(out, index=ic_eval._date_index(df), name=code)


# ---------------------------------------------------------------------------
# 校验与宇宙
# ---------------------------------------------------------------------------


def _valid_date(s: str) -> bool:
    try:
        from datetime import date  # noqa: PLC0415

        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def _resolve_scorer_keys(args: Any, ap: argparse.ArgumentParser) -> list[str]:
    """--scorers 逗号清单（默认全部）；未知键 fail-closed。"""
    if not args.scorers.strip():
        return sorted(bt.SCORERS)
    keys = [k.strip() for k in args.scorers.split(",") if k.strip()]
    bad = [k for k in keys if k not in bt.SCORERS]
    if bad:
        ap.error(f"--scorers 含未注册键: {bad}（SCORERS: {sorted(bt.SCORERS)}）")
    return keys


def _validate(args: Any, ap: argparse.ArgumentParser) -> list[int]:
    for name in ("start", "end"):
        if not _valid_date(getattr(args, name)):
            ap.error(f"--{name} 须为 YYYY-MM-DD: {getattr(args, name)}")
    if args.start > args.end:
        ap.error(f"窗口倒挂: --start {args.start} > --end {args.end}")
    try:
        horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    except ValueError:
        ap.error(f"--horizons 形态非法: {args.horizons!r}（逗号分隔正整数）")
    if not horizons or any(h < 1 for h in horizons):
        ap.error(f"--horizons 必须全是正整数: {args.horizons!r}")
    horizons = sorted(set(horizons))
    if args.primary_horizon not in horizons:
        ap.error(
            f"--primary-horizon {args.primary_horizon} 不在 --horizons {horizons} 内"
        )
    for expr in args.expr:
        try:
            parse(expr)
        except ExprError as exc:
            ap.error(f"--expr 非法: {expr!r}（{exc}）")
    return horizons


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
# 画像主流程
# ---------------------------------------------------------------------------


def _factor_frames(
    scorer_keys: list[str], exprs: list[str], sliced: dict[str, pd.DataFrame]
) -> list[dict[str, Any]]:
    """每因子出 (name, kind, score DataFrame)——scorer 走构建器，expr 走 score_frame。"""
    factors: list[dict[str, Any]] = []
    for key in scorer_keys:
        cols = {code: scorer_score_series(key, df, code) for code, df in sliced.items()}
        frame = pd.DataFrame(cols).sort_index()
        frame.index.name = "date"
        factors.append({"name": key, "kind": "scorer", "frame": frame})
    for expr in exprs:
        frame = ic_eval.score_frame(expr, sliced)
        factors.append({"name": expr, "kind": "expr", "frame": frame})
    return factors


def _profile_factor(
    frame: pd.DataFrame,
    sliced: dict[str, pd.DataFrame],
    horizons: list[int],
    args: Any,
) -> dict[str, Any]:
    """单因子的多 horizon IC 聚合 + 前后半窗（ic_eval 双列口径）。"""
    per_horizon: dict[str, Any] = {}
    for h in horizons:
        ic_df = ic_eval._ic_by_day(frame, sliced, h)
        stats = ic_eval.ic_stats_from_series(
            ic_df["rank_ic"], h, args.start, args.end, pearson_series=ic_df["ic"]
        )
        m1, m2 = operators._half_window_means(ic_df["rank_ic"])
        per_horizon[str(h)] = {
            "rank_ic_mean": stats.rank_ic_mean,
            "rank_icir": stats.rank_icir,
            "ic_mean": stats.ic_mean,
            "icir": stats.icir,
            "n_days": stats.n_days,
            "half1": m1,
            "half2": m2,
        }
    return per_horizon


def _ranking(factors: list[dict[str, Any]], primary: int) -> list[dict[str, Any]]:
    """按 primary horizon 的 rank_icir 降序（NaN/缺失垫底；同分按名字保序稳定）。"""
    rows = []
    for f in factors:
        cell = f["per_horizon"].get(str(primary)) or {}
        rows.append(
            {
                "name": f["name"],
                "primary_rank_icir": cell.get("rank_icir"),
                "primary_rank_ic_mean": cell.get("rank_ic_mean"),
            }
        )

    def _key(r: dict) -> tuple:
        v = r["primary_rank_icir"]
        ok = isinstance(v, float) and not math.isnan(v)
        return (ok, v if ok else 0.0)

    return sorted(rows, key=_key, reverse=True)


def _fmt_cell(v: Any) -> str:
    return f"{v:+.3f}" if isinstance(v, float) and not math.isnan(v) else "  -  "


def _print_table(
    factors: list[dict[str, Any]], ranking: list[dict[str, Any]], horizons: list[int]
) -> None:
    """全因子可比表：按 primary rank_icir 降序，衰减形状（逐 H rank_ic_mean）一眼可读。"""
    by_name = {f["name"]: f for f in factors}
    print(
        "\n因子 IC 画像（分诊参考 L3−；**晋级判据走双窗+三轴交易语义**，"
        "本表不作晋级依据——R19 预测口径非主判据 / R21 富集≠可交易 / R14 幸存者偏差）"
    )
    head = " | ".join(f"H{h} ric" for h in horizons)
    print(f"{'因子':<44} | {head} | rank_icir | n_days")
    print("-" * (44 + len(horizons) * 9 + 22))
    for r in ranking:
        f = by_name[r["name"]]
        cells = " | ".join(
            f"{(f['per_horizon'][str(h)]['rank_ic_mean']):+.3f}"
            if not math.isnan(f["per_horizon"][str(h)]["rank_ic_mean"])
            else "  -  "
            for h in horizons
        )
        icir = _fmt_cell(r["primary_rank_icir"])
        n_days = f["per_horizon"][str(horizons[0])]["n_days"]
        name = r["name"] if len(r["name"]) <= 44 else r["name"][:41] + "..."
        print(f"{name:<44} | {cells} | {icir} | {n_days}")


def main(
    argv: Optional[list[str]] = None,
    *,
    loader: Optional[Callable[[list[str], int], dict]] = None,
) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    horizons = _validate(args, ap)
    scorer_keys = _resolve_scorer_keys(args, ap)
    if not scorer_keys and not args.expr:
        ap.error("因子清单为空（--scorers 与 --expr 至少给一个）")
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
        # 空结果护栏（同 backtest_factors._empty_result_guard 语义）
        print(
            "[ERR] 未加载到任何 K 线（数据源/代码列表/日期区间有问题？），拒绝运行",
            file=sys.stderr,
        )
        return 2
    # [start,end] 切片发生在任何计算之前（ic_eval._slice_bars 同口径：
    # 滚动窗口 warmup 从窗口起点重算，expr 与 scorer 两路共享同一批切片帧）
    sliced: dict[str, pd.DataFrame] = {}
    for code, df in bars.items():
        d = ic_eval._slice_bars(df, args.start, args.end)
        if len(d):
            sliced[code] = d
    if not sliced:
        print(
            f"[ERR] 窗口 {args.start}~{args.end} 内无任何 K 线（窗口与数据错位？），拒绝运行",
            file=sys.stderr,
        )
        return 2

    factors = _factor_frames(scorer_keys, args.expr, sliced)
    for f in factors:
        f["per_horizon"] = _profile_factor(f.pop("frame"), sliced, horizons, args)
    ranking = _ranking(factors, args.primary_horizon)

    tag = args.tag or _now_tag()
    report = {
        "version": SCHEMA_VERSION,
        "tag": tag,
        "window": {"start": args.start, "end": args.end},
        "horizons": horizons,
        "primary_horizon": args.primary_horizon,
        "universe": {"n_codes": len(codes)},
        "factors": [
            {"name": f["name"], "kind": f["kind"], "per_horizon": f["per_horizon"]}
            for f in factors
        ],
        "ranking": ranking,
    }
    out_dir = (Path(args.out_dir) if args.out_dir else OUTDIR) / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_factor_ic_profile__{tag}.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, allow_nan=True)
    _print_table(factors, ranking, horizons)
    print(f"\n[INFO] 汇总 → {out}")
    return 0


def _now_tag() -> str:
    from datetime import datetime  # noqa: PLC0415

    return datetime.now().strftime("%Y%m%d_%H%M%S")


if __name__ == "__main__":
    raise SystemExit(main())
