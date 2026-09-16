#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""完美 B1 买点的 live 八段（V0 技术分）口径对照（R36 Phase 1 的 live 侧）。

`factor_ic_profile --marks` 覆盖的是 SCORERS 研究键，而 live 的分层打分是
**八段技术分**（``score_candidates.technical_score`` 合成，无单一 SCORERS 键）。
本工具补这一侧：对每个正例 (code, buy_date)，在**全历史** bars 上 as-of 计算
当日 V0 技术分（``score_return_study.asof_technical_score``——与 live 逐位
一致有钉测，score_variants_study 的 V0 重建口径同源），并在**同日全宇宙**
as-of V0 分布上给出分位——回答「live 现行打分当天把这些完美 B1 放在第几
分位」（诊断：被漏掉的是分值低，还是分值高但被别的门挡）。

分位口径与 factor_ic_profile --marks 一致（写死）：``percentile = 当日全宇宙
**有效** V0 分 ≤ 案例股 V0 分的比例``，并列按 ≤ 计；案例股/宇宙股当日
as-of 不可评 → 双侧剔除。案例 CSV 只提供 (code, buy_date) 锚点——**V0 评分
用全历史 as-of**（``_load_one_bars(count=--count, end=buy_date)``，enrich 腿
的 260/1200 根 warmup 由全历史供给；指数 bars 同 score_return_study 的
INDEX_CODE 口径）。每条腿的 as-of 明细直接取 as-of 返回的 ``factor_contrib``
（V0 重建的既有面，无需另造）。

⚠️ 诊断指标非判据：落点/召回不作晋级依据（R36 判据节写死；后视标注，
选择偏差，L1 发现材料）。

用法（生产机）::

    uv run python -m custos.research b1_marks_v0 \
        --universe-local --universe-sample 3000 --universe-seed 0 \
        --marks /home/gh/agent/ZGNB/B1_DATA --tag r36_p1_v0

产物：{out_dir}/{tag}/_b1_marks_v0__{tag}.json + stdout 分位表。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

# GBK（cp936）终端/管道打不了 ⚠️/⛔ 等符号 —— 不 reconfigure 会 UnicodeEncodeError。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from custos.core.paths import LOGS  # noqa: E402
from custos.research import backtest_factors as bt  # noqa: E402
from custos.research import score_return_study as srs  # noqa: E402

OUTDIR = LOGS / "b1_marks_v0"

SCHEMA_VERSION = 1
DEFAULT_UNIVERSE_SEED = 42
DEFAULT_B1_DIR = "/home/gh/agent/ZGNB/B1_DATA"


def _build_parser() -> argparse.ArgumentParser:
    """全部 CLI 参数定义（⚠️ add_argument 必须留本文件：_modes() 用 AST 抽取）。"""
    ap = argparse.ArgumentParser(
        description="完美 B1 买点的 live 八段（V0 技术分）口径对照"
        "（诊断指标非判据——落点/召回不作晋级依据）"
    )
    ap.add_argument(
        "--marks",
        default=DEFAULT_B1_DIR,
        help=f"正例目录（B1_DATA，默认 {DEFAULT_B1_DIR}）或 marks JSON"
        "（[{{'code': '600000', 'buy_date': 'YYYY-MM-DD'}}]）",
    )
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
        "--count",
        type=int,
        default=2000,
        help="每股加载 K 线根数（默认 2000：enrich 腿 260/1200 根 warmup 需要）",
    )
    ap.add_argument("--tag", default="", help="运行标识（默认时间戳）；产物目录名")
    ap.add_argument(
        "--out-dir", default="", help=f"产物根目录（默认 {OUTDIR}），tag 作子目录"
    )
    return ap


# ---------------------------------------------------------------------------
# marks 加载（形态同 factor_ic_profile._load_marks，工具各自留本文件口径）
# ---------------------------------------------------------------------------


def _load_marks(args: Any, ap: argparse.ArgumentParser) -> tuple[list[dict], str]:
    from custos.research.b1_perfect_dataset import load_cases  # noqa: PLC0415

    p = Path(args.marks)
    if p.is_dir():
        try:
            cases = load_cases(p)
        except ValueError as exc:
            ap.error(f"--marks 目录加载失败: {exc}")
        return (
            [{"code": c.code, "buy_date": c.buy_date} for c in cases],
            f"b1_data_dir({p})",
        )
    if p.is_file():
        try:
            payload = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            ap.error(f"--marks JSON 不可解析: {p}（{type(exc).__name__}: {exc}）")
        if isinstance(payload, dict):
            # 权威清单信封形态（version/note/marks，R36_perfect_b1_marks.json）
            payload = payload.get("marks")
        if not isinstance(payload, list):
            ap.error(f"--marks JSON 顶层必须是 list 或含 marks 清单的信封: {p}")
        bad = [
            m
            for m in payload
            if not isinstance(m, dict)
            or not isinstance(m.get("code"), str)
            or not isinstance(m.get("buy_date"), str)
        ]
        if bad:
            ap.error(f"--marks 条目形态非法（须含 str code/buy_date）: {bad[:3]}")
        if not payload:
            ap.error("--marks 0 打点（空清单）——打点为空只会被误读，拒跑")
        return payload, f"marks_json({p.name})"
    ap.error(f"--marks 路径不存在: {p}")
    raise AssertionError("ap.error 不返回")  # pragma: no cover


# ---------------------------------------------------------------------------
# as-of V0 计算（可注入：v0_scorer/bars_loader/index_loader 测试换 fake）
# ---------------------------------------------------------------------------

V0Scorer = Callable[..., Any]  # (df, index_df, i, code) -> (score, level, contrib)


def _default_index_loader() -> pd.DataFrame:
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    return (
        local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )


def _v0_at(
    scorer: V0Scorer,
    bars_loader: Callable[[str, int, Optional[str], Optional[str]], Any],
    index_df: pd.DataFrame,
    code: str,
    date: str,
    count: int,
) -> Optional[tuple[float, dict]]:
    """(code, date) 的 as-of V0 技术分 + contrib；不可评 → None（不硬算）。

    加载失败/当日无 bar/评分异常 → None；score 非有限值 → None。
    """
    df = bars_loader(code, count, None, date)
    if df is None or not len(df):
        return None
    df = df.copy()
    # date 列归一为 datetime64：真实 _load_one_bars 带窗口裁剪时把 date 转成
    # 字符串（backtest_factors.py:4935），而 enrich 检测器（weekly_j 周线
    # resample 等）依赖 datetime64——转 str 会炸（score_return_study.py:504
    # 冒烟实测教训）；生产机 2026-09-16 实测：str 列 ⇒ scorer 全炸 ⇒
    # 异常吞成 None ⇒ 0 有效打点拒跑。比较仍走 astype(str) 现算（见下）。
    df["date"] = pd.to_datetime(df["date"])
    dates = df["date"].astype(str).str[:10].tolist()
    pos = {d: i for i, d in enumerate(dates)}.get(date)
    if pos is None:
        return None
    try:
        score, _level, contrib = scorer(df, index_df, pos, code)
    except Exception:  # noqa: BLE001
        return None
    if score is None or not math.isfinite(float(score)):
        return None
    return float(score), dict(contrib or {})


def _percentile_le(valid: list[float], v: float) -> float:
    """分位口径（与 factor_ic_profile --marks 一致）：有效值 ≤ v 的比例，并列按 ≤ 计。"""
    arr = np.asarray(valid, dtype=float)
    arr = arr[np.isfinite(arr)]
    if not len(arr):
        return float("nan")
    return float((arr <= v).mean())


def _print_table(cases: list[dict], summary: dict[str, Any]) -> None:
    print(
        "\n完美 B1 买点 × live 八段（V0 技术分）口径对照（**诊断指标非判据**——"
        "R36：落点/召回不作晋级依据，10 点必过拟合；口径 = 当日全宇宙有效 V0 分 "
        "≤ 案例股 V0 分的比例，并列按 ≤ 计；u=unavailable）"
    )
    print(f"{'code':<8} {'buy_date':<12} {'v0':>6} {'percentile':>10} {'status':<14}")
    print("-" * 56)
    for c in cases:
        v0 = f"{c['v0_score']:.1f}" if c["v0_score"] is not None else "  -"
        pct = f"{c['percentile']:.2f}" if c["percentile"] is not None else "  -"
        print(f"{c['code']:<8} {c['buy_date']:<12} {v0:>6} {pct:>10} {c['status']:<14}")
    m, md = summary["mean"], summary["median"]
    print(
        f"汇总：mean={m if m is None else round(m, 2)} median={md if md is None else round(md, 2)}"
        f" ≥0.8={summary['ge_0_8']} ≥0.9={summary['ge_0_9']}（hit {summary['n_hit']}/{summary['n_cases']}）"
    )


def main(
    argv: Optional[list[str]] = None,
    *,
    v0_scorer: Optional[V0Scorer] = None,
    bars_loader: Optional[Callable] = None,
    index_loader: Optional[Callable[[], pd.DataFrame]] = None,
) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    marks, source = _load_marks(args, ap)
    ns = argparse.Namespace(
        codes_file=args.codes_file,
        universe_local=args.universe_local,
        universe_sample=args.universe_sample,
        seed=args.universe_seed,
        codes=args.codes,
    )
    codes = bt._resolve_universe(ns, ap)

    scorer = v0_scorer or srs.asof_technical_score
    if bars_loader is None:
        bars_loader = bt._load_one_bars  # (code, count, start, end)
    index_df = index_loader() if index_loader is not None else _default_index_loader()
    if index_df is None or not len(index_df):
        print("[ERR] 指数 K 线缺失（V0 的 RS 腿需要），拒绝运行", file=sys.stderr)
        return 2

    cases: list[dict[str, Any]] = []
    pcts: list[float] = []
    for m in marks:
        code, buy_date = m["code"], m["buy_date"]
        hit = _v0_at(scorer, bars_loader, index_df, code, buy_date, args.count)
        if hit is None:
            cases.append(
                {
                    "code": code,
                    "buy_date": buy_date,
                    "v0_score": None,
                    "percentile": None,
                    "status": "unavailable",
                    "contrib": None,
                }
            )
            continue
        v0, contrib = hit
        universe_vals: list[float] = []
        for u in codes:
            uh = _v0_at(scorer, bars_loader, index_df, u, buy_date, args.count)
            if uh is not None:
                universe_vals.append(uh[0])
        pct = _percentile_le(universe_vals, v0)
        if np.isnan(pct):
            cases.append(
                {
                    "code": code,
                    "buy_date": buy_date,
                    "v0_score": v0,
                    "percentile": None,
                    "status": "no_universe",
                    "contrib": contrib,
                }
            )
            continue
        pcts.append(pct)
        cases.append(
            {
                "code": code,
                "buy_date": buy_date,
                "v0_score": v0,
                "percentile": pct,
                "status": "hit",
                "contrib": contrib,
            }
        )
    arr = np.asarray(pcts, dtype=float) if pcts else np.asarray([], dtype=float)
    summary = {
        "mean": float(arr.mean()) if len(arr) else None,
        "median": float(np.median(arr)) if len(arr) else None,
        "ge_0_8": int((arr >= 0.8).sum()),
        "ge_0_9": int((arr >= 0.9).sum()),
        "n_hit": len(pcts),
        "n_cases": len(cases),
    }
    if not pcts:
        print(
            "[ERR] 0 个有效打点（案例/宇宙全不可评——数据源/代码表有问题？），拒绝落盘",
            file=sys.stderr,
        )
        return 2

    tag = args.tag or _now_tag()
    report = {
        "version": SCHEMA_VERSION,
        "tag": tag,
        "source": source,
        "universe": {
            "n_codes": len(codes),
            "source": (
                f"codes_file({Path(args.codes_file).name})"
                if args.codes_file
                else (
                    "codes(内联)"
                    if args.codes
                    else f"{'local_vipdoc' if args.universe_local else 'online_get_stock_list'} sample={args.universe_sample} seed={args.universe_seed}"
                )
            ),
        },
        "cases": cases,
        "summary": summary,
        "discipline_note": "诊断指标非判据（R36：落点/召回不作晋级依据；后视标注，选择偏差，L1 发现材料）",
    }
    out_dir = (Path(args.out_dir) if args.out_dir else OUTDIR) / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_b1_marks_v0__{tag}.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, allow_nan=True)
    _print_table(cases, summary)
    print(f"\n[INFO] 汇总 → {out}")
    return 0


def _now_tag() -> str:
    from datetime import datetime  # noqa: PLC0415

    return datetime.now().strftime("%Y%m%d_%H%M%S")


if __name__ == "__main__":
    raise SystemExit(main())
