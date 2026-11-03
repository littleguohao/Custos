# -*- coding: utf-8 -*-
"""信号参数**定义校准**探针（TODO #121，owner 2026-11-03）：核对检测器是否测出规则原文的形态。

两种模式（同一套 as-of 口径 = live 1800 链 ``enrich_candidates.compute_metrics``，逐 bar 截断无未来函数）：

- ``--mode sample``：在挖掘段（A ∪ B）随机抽 bar 池，逐字段分「命中 / 未命中 / 边界」（边界 = 任一参数
  ×0.8 或 ×1.2 后判定翻转），每类抽 N 例出**抽检图**（纯 SVG：K 线 + 量 + J + MACD，只画到 as-of 当根）
  与**标注表**（CSV，owner 填「对 / 错 / 不确定」）；
- ``--mode sensitivity``：每个参数按 ``--steps`` 倍数逐个改动，报每字段命中数与命中集合 Jaccard（相对现值）。
  **只描述，不挑最优**——这是定义校准，不是效果寻优（B 类等 A 全部做完再议）。

参数改动机制：把定义模块里的常量连同所有 ``from x import NAME`` 绑定（同对象）一并替换；**函数默认参数 /
函数体内联常量改不到**——敏感性表里「全部步长零变化」的参数标 ``no_effect``，须人工确认是不敏感还是没改到。
不读收益、不碰判定段 / holdout（抽样日期限挖掘段；段前预热只读价格，同 v2 纪律）。生产机运行。

用法（生产机）::

    uv run python -m custos.research calib_probe --module b1_thresholds --mode sample --universe-sample 1000
    uv run python -m custos.research calib_probe --module b1_thresholds --mode sensitivity --universe-sample 1000
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import html
import json
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

#: 每个 P0 模块：参数定义模块 + 要核对的 cand 字段（路径用「.」分隔；值为布尔 / 档位字符串）
MODULES: dict[str, dict[str, Any]] = {
    # 持仓侧大形态（P0H，v0.485）：live 持仓链 technical_monitor.analyze，as-of 长历史帧（1200 根）
    "n_structure": {
        "defmod": "custos.pipeline.market_timing.technical_monitor",
        "eval": "technical_monitor",
        "fields": [
            "n_structure.status",
            "n_structure.fresh_breach",
            "descending_n_structure.status",
            "descending_n_structure.fresh_breach",
        ],
        "params": [
            "N_PIVOT_LEFT",
            "N_PIVOT_RIGHT",
            "N_LOOKBACK",
            "N_STALE_BREACH_BARS",
        ],
    },
    "trend_box": {
        "defmod": "custos.pipeline.market_timing.technical_monitor",
        "eval": "technical_monitor",
        "fields": ["trend.state", "box_20d.position", "box_60d.position"],
        "params": [
            "BOX_UPPER_Q",
            "BOX_LOWER_Q",
            "BOX_SHORT",
            "BOX_LONG",
            "TREND_MA_FAST",
            "TREND_MA_SLOW",
            "TREND_FAST_SLOPE_BARS",
            "TREND_HL_WINDOW",
        ],
    },
    "b1_thresholds": {
        "defmod": "custos.core.b1_thresholds",
        "fields": [
            "patterns.j_low",
            "patterns.reversal_k_candidate",
            "patterns.volume_contraction",
            "weekly_j_low",
        ],
    },
    "entry_patterns": {
        "defmod": "custos.core.factors.entry_patterns",
        "fields": ["patterns.bbi_above", "patterns.relative_strength_strong"],
    },
    "b1_structure": {
        "defmod": "custos.core.factors.b1_structure",
        "fields": [
            "non_one_wave.status",
            "repair_signals.signals",
            "five_day_entry.hit",
            "stop_loss_ref.price",
            "liquidity.avg_amount_yi",
        ],
    },
    "volume_detectors": {
        "defmod": "custos.core.factors.volume_detectors",
        "fields": [
            "volume_sustain.status",
            "leader_volume.hit",
            "three_lows.hit",
            "bottom_volume.hit",
        ],
    },
    "ignition": {
        "defmod": "custos.core.factors.ignition",
        "fields": ["ignition.hit", "pullback_shrink.hit", "b1_ignition.hit"],
    },
    "macd_technics": {
        "defmod": "custos.core.factors.macd_technics",
        "fields": [
            "macd_technics.zone",
            "macd_technics.bottom_divergence.hit",
            "macd_technics.top_divergence.hit",
            "macd_technics.three_peaks.hit",
            "macd_technics.overextended.hit",
        ],
    },
    "distribution": {
        "defmod": "custos.core.factors.distribution",
        "fields": ["distribution.risk_level", "distribution_confirm.revoked"],
    },
    "wave_type": {
        "defmod": "custos.core.factors.wave_type",
        "fields": ["wave.wave_type"],
    },
    "capital_intent": {
        "defmod": "custos.core.factors.capital_intent",
        "fields": ["@capital_intent.level"],
        # 阈值在 import 时抄进默认权重表（live 还可被 registry scoring.weights 覆盖）——改常量须同步改表
        "aliases": {
            "CAP_STRONG": ("DEFAULT_EVIDENCE_WEIGHTS", "cap_strong"),
            "CAP_MID": ("DEFAULT_EVIDENCE_WEIGHTS", "cap_mid"),
        },
    },
}

DEFAULT_STEPS = (0.5, 0.75, 0.9, 1.1, 1.25, 1.5)
BOUNDARY_STEPS = (0.8, 1.2)
CHART_BARS = 120


# ---------------------------------------------------------------------------
# 参数改动
# ---------------------------------------------------------------------------


def param_names(module: str) -> list[str]:
    """该模块在定义校准清单里的数值参数（与 scripts/dev/calibration_inventory 同口径）。"""
    import ast  # noqa: PLC0415
    import importlib  # noqa: PLC0415

    if MODULES[module].get("params"):
        return list(MODULES[module]["params"])
    mod = importlib.import_module(MODULES[module]["defmod"])
    src = Path(mod.__file__ or "").read_text(encoding="utf-8")
    out = []
    for n in ast.parse(src).body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            t = n.targets[0] if isinstance(n, ast.Assign) else n.target
            if isinstance(t, ast.Name) and t.id.isupper() and t.id != "FACTOR":
                v = getattr(mod, t.id, None)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append(t.id)
    return out


def scaled(v: Any, k: float) -> Any:
    x = v * k
    return int(round(x)) if isinstance(v, int) else round(float(x), 6)


_DEFAULT_REFS: dict[str, dict[str, set[str]]] = {}


def _default_refs(m: Any) -> dict[str, set[str]]:
    """模块源码里「默认值写的是常量名」的函数：{常量名: {函数名}}（AST，按模块缓存）。
    只替换这些函数的默认参数——防止同值同对象的别的常量（如派生的 MIN/MAX）被串改。"""
    import ast  # noqa: PLC0415

    name = m.__name__
    if name not in _DEFAULT_REFS:
        refs: dict[str, set[str]] = {}
        try:
            tree: Any = ast.parse(Path(m.__file__).read_text(encoding="utf-8"))
        except (OSError, TypeError, SyntaxError, ValueError):
            tree = None
        for node in ast.walk(tree) if tree is not None else []:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                for dft in [*a.defaults, *[x for x in a.kw_defaults if x is not None]]:
                    if isinstance(dft, ast.Name):
                        refs.setdefault(dft.id, set()).add(node.name)
        _DEFAULT_REFS[name] = refs
    return _DEFAULT_REFS[name]


@contextlib.contextmanager
def patched(
    defmod: str,
    overrides: dict[str, Any],
    aliases: Optional[dict[str, tuple[str, str]]] = None,
) -> Iterator[int]:
    """把 ``defmod.NAME`` 与所有 custos 模块里同对象的 ``NAME`` 绑定一并替换；退出还原。产出被改的绑定数。"""
    import importlib  # noqa: PLC0415

    import types  # noqa: PLC0415

    src = importlib.import_module(defmod)
    saved: list[tuple[Any, str, Any]] = []
    saved_defaults: list[tuple[Any, Any, Any]] = []
    for name, val in overrides.items():
        orig = getattr(src, name)
        for m in list(sys.modules.values()):
            if m is None or not getattr(m, "__name__", "").startswith("custos."):
                continue
            if getattr(m, name, None) is orig:
                saved.append((m, name, orig))
                setattr(m, name, val)
            # 函数默认参数在 def 时捕获（如 ``def f(x, t=J_LOW_THRESHOLD)``）——同对象一并替换
            owners = _default_refs(m).get(name, set())
            for fn in [vars(m).get(f) for f in sorted(owners)]:
                if (
                    not isinstance(fn, types.FunctionType)
                    or fn.__module__ != m.__name__
                ):
                    continue
                d, kd = fn.__defaults__, fn.__kwdefaults__
                hit_d = d is not None and any(x is orig for x in d)
                hit_k = kd is not None and any(x is orig for x in kd.values())
                if hit_d or hit_k:
                    saved_defaults.append((fn, d, kd))
                    if d is not None and hit_d:
                        fn.__defaults__ = tuple(val if x is orig else x for x in d)
                    if kd is not None and hit_k:
                        fn.__kwdefaults__ = {
                            k: (val if x is orig else x) for k, x in kd.items()
                        }
    saved_items: list[tuple[dict, str, Any]] = []
    for name, val in overrides.items():
        if aliases and name in aliases:
            dname, key = aliases[name]
            tab = getattr(src, dname)
            saved_items.append((tab, key, tab[key]))
            tab[key] = val
    try:
        yield len(saved) + len(saved_defaults) + len(saved_items)
    finally:
        for tab, key, old in reversed(saved_items):
            tab[key] = old
        for fn, d, kd in reversed(saved_defaults):
            fn.__defaults__, fn.__kwdefaults__ = d, kd
        for m, name, orig in reversed(saved):
            setattr(m, name, orig)


# ---------------------------------------------------------------------------
# 取值
# ---------------------------------------------------------------------------


def field_value(cand: dict, path: str) -> Any:
    if path.startswith("@capital_intent"):
        from custos.core.factors import capital_intent as ci  # noqa: PLC0415

        return ci.capital_intent_strength(cand)[0]
    cur: Any = cand
    for k in path.split("."):
        cur = cur.get(k) if isinstance(cur, dict) else None
    if isinstance(cur, list):
        return "、".join(sorted(map(str, cur))) or "-"
    return cur


def is_hit(v: Any) -> bool:
    """命中口径：布尔 True / 非空非否定档位（"none"/"unknown"/"-"/0 视为未命中）。"""
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    return str(v) not in (
        "",
        "-",
        "none",
        "unknown",
        "neutral",
        "insufficient",
        "弱",
        "横盘震荡",
        "数据不足",
    )


def evaluate(
    frames: tuple, defmod: str, overrides: dict[str, Any], fields: list[str]
) -> dict[str, Any]:
    from custos.pipeline.screening import enrich_candidates as ec  # noqa: PLC0415

    df, df_long, index_asof, code = frames
    spec = next(
        (v for v in MODULES.values() if v["defmod"] == defmod and v.get("eval")), None
    )
    if spec is not None and spec["eval"] == "technical_monitor":
        from custos.pipeline.market_timing import technical_monitor as tm  # noqa: PLC0415

        with patched(defmod, overrides):
            an = tm.analyze(df_long.reset_index(drop=True), code)
            return {f: field_value(an, f) for f in fields}
    aliases = next(
        (v.get("aliases") for v in MODULES.values() if v["defmod"] == defmod), None
    )
    with patched(defmod, overrides, aliases):
        cand = ec.compute_metrics(df, index_asof, code=code, df_long=df_long)
        return {f: field_value(cand, f) for f in fields}


def _eval_bar(job: tuple) -> tuple:
    """worker：一根 bar × 全部参数变体（帧只传一次）→ (key, [每变体 {field: value}])。"""
    key, frames, defmod, variants, fields = job
    out = []
    for ov in variants:
        try:
            out.append(evaluate(frames, defmod, ov, fields))
        except Exception as exc:  # noqa: BLE001 — 逐 bar 容错，记错不中断
            out.append({"__error__": repr(exc)})
    return key, out


def run_jobs(
    jobs: list[tuple], workers: int, executor_factory: Optional[Callable] = None
) -> dict:
    """⚠️ 参数改动是进程级全局状态：只能串行或**多进程**（每 worker 串行执行），不得用线程池。"""
    if workers <= 1 or len(jobs) < 2:
        return dict(_eval_bar(j) for j in jobs)
    factory = executor_factory or (lambda n: ProcessPoolExecutor(max_workers=n))
    with factory(workers) as ex:
        return dict(ex.map(_eval_bar, jobs, chunksize=4))


# ---------------------------------------------------------------------------
# 抽样
# ---------------------------------------------------------------------------


def bar_pool(
    bars: dict[str, Any],
    spans: list[tuple[str, str]],
    n: int,
    rng: random.Random,
    *,
    jlow_only: bool,
    min_history: int = 260,
) -> list[tuple[str, int]]:
    """挖掘段内随机 (code, i)；``jlow_only`` 只取日 J<13 的 bar（live 进池门之后才评估其他检测器）。"""
    from custos.core import b1_thresholds as bth  # noqa: PLC0415
    from custos.core.indicators import j_series  # noqa: PLC0415

    cand: list[tuple[str, int]] = []
    for code in sorted(bars):
        df = bars[code]
        ds = df["date"].astype(str).str[:10].tolist()
        ok = [
            i
            for i, d in enumerate(ds)
            if i >= min_history and any(s <= d <= e for s, e in spans)
        ]
        if jlow_only and ok:
            j = j_series(df).to_numpy()
            ok = [i for i in ok if j[i] == j[i] and j[i] < bth.J_LOW_THRESHOLD]
        cand.extend((code, i) for i in ok)
    rng.shuffle(cand)
    return sorted(cand[:n])


def frames_of(bars: dict[str, Any], index_df: Any, key: tuple[str, int]) -> tuple:
    from custos.research import score_return_study as srs  # noqa: PLC0415

    bkey, i = key
    df, df_long, idx = srs.asof_frames(bars[bkey], index_df, i)
    return (
        df,
        df_long,
        idx,
        bkey.split("|")[0],
    )  # bars 键 = 代码|段起年；compute_metrics 要真实代码


# ---------------------------------------------------------------------------
# 抽检图（纯 SVG，无第三方依赖）
# ---------------------------------------------------------------------------


def svg_chart(df: Any, title: str, width: int = 760) -> str:
    """末 CHART_BARS 根（截至 as-of 当根）：K 线（红涨绿跌）+ 量 + J（13 线）+ MACD（DIF/DEA/柱）。"""
    from custos.core.indicators import j_series, macd_series  # noqa: PLC0415

    t = df.tail(CHART_BARS).reset_index(drop=True)
    jj = j_series(df).tail(CHART_BARS).to_numpy()
    dif, dea, hist = (s.tail(CHART_BARS).to_numpy() for s in macd_series(df["close"]))
    n = len(t)
    if not n:
        return ""
    o, h, lo, c, v = (
        t[k].astype(float).to_numpy()
        for k in ("open", "high", "low", "close", "volume")
    )
    pad, gap = 40, 8
    hp, hv, hj, hm = 220, 50, 70, 70
    H = hp + hv + hj + hm + gap * 4 + 24
    dx = (width - pad - 10) / max(n, 1)

    def X(i: float) -> float:
        return pad + (i + 0.5) * dx

    def scale(vals: Any, top: float, height: float) -> Callable[[float], float]:
        fin = [float(x) for x in vals if x == x]
        a, b = (min(fin), max(fin)) if fin else (0.0, 1.0)
        if b == a:
            b = a + 1.0
        return lambda y: top + height - (y - a) / (b - a) * height

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{H}" font-size="10">',
        f'<text x="{pad}" y="12">{html.escape(title)}</text>',
    ]
    y0 = 18
    yp = scale(list(lo) + list(h), y0, hp)
    for i in range(n):
        col = "#d33" if c[i] >= o[i] else "#2a2"
        out.append(
            f'<line x1="{X(i):.1f}" y1="{yp(h[i]):.1f}" x2="{X(i):.1f}" y2="{yp(lo[i]):.1f}" stroke="{col}"/>'
        )
        top, bot = yp(max(o[i], c[i])), yp(min(o[i], c[i]))
        out.append(
            f'<rect x="{X(i) - dx * 0.35:.1f}" y="{top:.1f}" width="{dx * 0.7:.1f}" height="{max(bot - top, 0.6):.1f}" fill="{col}"/>'
        )
    y1 = y0 + hp + gap
    yv = scale([0.0, *v], y1, hv)
    for i in range(n):
        col = "#d33" if c[i] >= o[i] else "#2a2"
        out.append(
            f'<rect x="{X(i) - dx * 0.35:.1f}" y="{yv(v[i]):.1f}" width="{dx * 0.7:.1f}" height="{yv(0) - yv(v[i]):.1f}" fill="{col}" opacity="0.6"/>'
        )
    y2 = y1 + hv + gap
    yj = scale([*jj, 13.0, 0.0, 100.0], y2, hj)
    out.append(
        f'<line x1="{pad}" y1="{yj(13):.1f}" x2="{width - 10}" y2="{yj(13):.1f}" stroke="#999" stroke-dasharray="3"/>'
    )
    out.append(f'<text x="2" y="{yj(13):.1f}">J13</text>')
    pts = " ".join(f"{X(i):.1f},{yj(x):.1f}" for i, x in enumerate(jj) if x == x)
    out.append(f'<polyline points="{pts}" fill="none" stroke="#a0a"/>')
    y3 = y2 + hj + gap
    ym = scale([*dif, *dea, *hist, 0.0], y3, hm)
    out.append(
        f'<line x1="{pad}" y1="{ym(0):.1f}" x2="{width - 10}" y2="{ym(0):.1f}" stroke="#999"/>'
    )
    out.append(f'<text x="2" y="{ym(0):.1f}">MACD0</text>')
    for i, x in enumerate(hist):
        if x == x:
            col = "#d33" if x >= 0 else "#2a2"
            a, b = sorted((ym(0), ym(x)))
            out.append(
                f'<rect x="{X(i) - dx * 0.3:.1f}" y="{a:.1f}" width="{dx * 0.6:.1f}" height="{max(b - a, 0.4):.1f}" fill="{col}"/>'
            )
    for series, col in ((dif, "#333"), (dea, "#e90")):
        pts = " ".join(
            f"{X(i):.1f},{ym(x):.1f}" for i, x in enumerate(series) if x == x
        )
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}"/>')
    out.append(
        f'<line x1="{X(n - 1):.1f}" y1="{y0}" x2="{X(n - 1):.1f}" y2="{y3 + hm}" stroke="#36f" stroke-dasharray="2"/>'
    )
    out.append(
        f'<text x="{pad}" y="{H - 4}">{html.escape(str(t["date"].iloc[0])[:10])} → {html.escape(str(t["date"].iloc[-1])[:10])}（蓝虚线 = as-of 当根；只画到当根）</text>'
    )
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _load(args: Any) -> tuple[dict[str, Any], Any, list[tuple[str, str]]]:
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import load_window as lw  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415

    ns = argparse.Namespace(
        codes_file=args.codes_file,
        universe_local=args.universe_local,
        universe_sample=args.universe_sample,
        seed=args.universe_seed,
        codes=args.codes,
    )
    codes = bt._resolve_universe(ns, _build_parser())
    spans = [lw.SEG_MINING_A, lw.SEG_MINING_B]
    bars: dict[str, Any] = {}
    for s, e in spans:

        def _one(code: str, s: str = s, e: str = e) -> Any:
            df = bt._load_one_bars(code, args.count, s, e, warmup_bars=300)
            return df if df is not None and len(df) else None

        got = (
            bt.run_codes_parallel(codes, _one, args.load_workers)
            if args.load_workers > 1
            else {c: d for c in codes if (d := _one(c)) is not None}
        )
        for c, df in got.items():
            bars[f"{c}|{s[:4]}"] = df  # 段内各自一份（同 v2：段间不拼接）
    index_df = (
        local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )
    return bars, index_df, [(str(a), str(b)) for a, b in spans]


def review_pool(
    args: Any,
    *,
    pool_files: Optional[list[Path]] = None,
    load_fn: Optional[Callable[[str], Any]] = None,
    index_df: Any = None,
) -> tuple[dict[str, Any], Any, list[tuple[str, int]]]:
    """review 模式（owner 2026-11-03 定的校准循环）：读 ``[--since, --until]`` 的 1800 ``stock_pool``，
    每个 (代码, 选股日) 一根 bar（as-of 当日，与 live 同口径重算）。⚠️ 这些日期落在 holdout——只作定义校准，
    升版时须 ``factor_versions.py --holdout-use`` 登记窗口。"""
    source = getattr(args, "review_source", "stock_pool")
    if pool_files is None:
        from custos.core.paths import HOLDINGS_DIR, STOCK_POOL_DIR  # noqa: PLC0415

        pool_files = (
            sorted(HOLDINGS_DIR.glob("*_holding_technical_summary.json"))
            if source == "holdings"
            else sorted(STOCK_POOL_DIR.glob("*_stock_pool.json"))
        )
    pairs: set[tuple[str, str]] = set()
    for f in pool_files:
        d = f.name[:10]
        if not (args.since <= d <= args.until):
            continue
        try:
            js = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        rows = js if isinstance(js, list) else (js.get("candidates") or [])
        for c in rows:
            if isinstance(c, dict) and c.get("code"):
                pairs.add((str(c["code"]).split(".")[0], d))
    if load_fn is None:
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

        def load_fn(code: str) -> Any:  # noqa: F811
            df = local_tdx_data.get_ohlcv_table(code, count=args.count)
            return (
                None
                if df is None or not len(df)
                else df.sort_values("date").reset_index(drop=True)
            )

    if index_df is None:
        from custos.research import score_return_study as srs  # noqa: PLC0415

        index_df = load_fn(srs.INDEX_CODE)
    bars: dict[str, Any] = {}
    keys: list[tuple[str, int]] = []
    for code in sorted({c for c, _d in pairs}):
        df = load_fn(code)
        if df is None:
            continue
        bk = f"{code}|full"
        bars[bk] = df
        pos = {str(x)[:10]: i for i, x in enumerate(df["date"])}
        keys += [(bk, pos[d]) for c, d in sorted(pairs) if c == code and d in pos]
    return bars, index_df, sorted(keys)


def run_study(
    args: Any,
    *,
    bars: Optional[dict[str, Any]] = None,
    index_df: Any = None,
    spans: Optional[list[tuple[str, str]]] = None,
    executor_factory: Optional[Callable] = None,
) -> dict[str, Any]:
    spec = MODULES[args.module]
    defmod, fields = spec["defmod"], spec["fields"]
    rng = random.Random(args.seed)
    if args.mode == "review":
        if bars is None:
            bars, index_df, pool = review_pool(args)
        else:  # 注入：bars 内 [since, until] 的全部 bar
            pool = sorted(
                (k, i)
                for k, df in bars.items()
                for i in range(len(df))
                if args.since <= str(df["date"].iloc[i])[:10] <= args.until
            )
        spans = [(args.since, args.until)]
    else:
        if bars is None:
            bars, index_df, spans = _load(args)
        assert spans is not None
        pool = bar_pool(bars, spans, args.n_bars, rng, jlow_only=(args.pool == "jlow"))
    if not pool:
        raise RuntimeError(
            "空结果护栏：抽样 bar 池为空（宇宙 / 段 / J 门槛无 bar）——不落盘"
        )
    frames = {k: frames_of(bars, index_df, k) for k in pool}
    params = param_names(args.module)
    from custos.core import b1_thresholds  # noqa: F401,PLC0415 — 预加载以便 patch 找到全部绑定
    import importlib  # noqa: PLC0415

    importlib.import_module("custos.pipeline.screening.enrich_candidates")
    defm = importlib.import_module(defmod)
    base_vals = {p: getattr(defm, p) for p in params}
    steps = BOUNDARY_STEPS if args.mode in ("sample", "review") else tuple(args.steps)
    variants: list[tuple[str, float, dict]] = [("__base__", 1.0, {})]
    for p in params:
        for k in steps:
            nv = scaled(base_vals[p], k)
            if nv != base_vals[p]:
                variants.append((p, k, {p: nv}))
    ovs = [ov for _p, _k, ov in variants]
    per_bar = run_jobs(
        [(key, frames[key], defmod, ovs, fields) for key in pool],
        args.workers,
        executor_factory,
    )
    res = {(vi, key): per_bar[key][vi] for key in pool for vi in range(len(variants))}
    base = {key: res[(0, key)] for key in pool}
    errors = sum(1 for v in base.values() if "__error__" in v)
    hits = {f: {k for k, v in base.items() if is_hit(v.get(f))} for f in fields}
    sens: list[dict[str, Any]] = []
    flips: dict[tuple, set] = {}
    for vi, (p, k, ov) in enumerate(variants[1:], start=1):
        for f in fields:
            hs = {key for key in pool if is_hit(res[(vi, key)].get(f))}
            changed = {key for key in pool if res[(vi, key)].get(f) != base[key].get(f)}
            flips.setdefault((f, p), set()).update(changed)
            u = hs | hits[f]
            sens.append(
                {
                    "param": p,
                    "step": k,
                    "value": ov[p],
                    "base_value": base_vals[p],
                    "field": f,
                    "n_hit": len(hs),
                    "n_hit_base": len(hits[f]),
                    "n_changed": len(changed),
                    "jaccard": (len(hs & hits[f]) / len(u)) if u else 1.0,
                }
            )
    no_effect = sorted(
        p for p in params if not any(r["n_changed"] for r in sens if r["param"] == p)
    )
    rep: dict[str, Any] = {
        "schema": "calib_probe/v1",
        "tool": "calib_probe",
        "module": args.module,
        "mode": args.mode,
        "tag": args.tag,
        "pool": args.pool,
        "n_bars": len(pool),
        "n_errors": errors,
        "spans": spans,
        "fields": fields,
        "params": base_vals,
        "steps": list(steps),
        "hit_rate": {f: len(hits[f]) / len(pool) for f in fields},
        "no_effect_params": no_effect,
        "sensitivity": sens if args.mode == "sensitivity" else None,
        "note": "定义校准（A 类）：只描述命中集合随参数的变化，不挑最优；no_effect = 全部步长零变化（不敏感或改不到，须人工确认）",
    }
    if args.mode == "review":
        rep["values"] = [
            {
                "key": list(k),
                "date": str(frames[k][0]["date"].iloc[-1])[:10],
                **{f: base[k].get(f) for f in fields},
            }
            for k in pool
        ]
        rep["factor_version"] = _version_of(args.module)
        rep["samples"] = _review_rows(pool, base, hits, flips, fields)
        rep["_frames"] = {
            k: frames[k]
            for s in rep["samples"].values()
            for k in [tuple(x["key"]) for x in s]
        }
    if args.mode == "sample":
        rep["samples"] = _samples(pool, base, hits, flips, fields, args, rng)
        rep["_frames"] = {
            k: frames[k]
            for s in rep["samples"].values()
            for k in [tuple(x["key"]) for x in s]
        }
    return rep


def _version_of(module: str) -> Optional[int]:
    from custos.core import factor_versions as fv  # noqa: PLC0415

    return fv.versions().get(module)


def _review_rows(pool, base, hits, flips, fields) -> dict[str, list[dict]]:
    """review：**全部**命中 + 全部近似命中（未命中但任一参数 ±20% 即翻成命中），不抽样。"""
    out: dict[str, list[dict]] = {}
    for f in fields:
        boundary = (
            set().union(*[v for (ff, _p), v in flips.items() if ff == f])
            if flips
            else set()
        )
        rows = []
        for c, keys in (
            ("hit", sorted(hits[f])),
            ("near_miss", sorted(boundary - hits[f])),
        ):
            for key in keys:
                ps = sorted(p for (ff, p), v in flips.items() if ff == f and key in v)
                rows.append(
                    {
                        "key": list(key),
                        "class": c,
                        "value": base[key].get(f),
                        "flip_params": ps,
                    }
                )
        out[f] = rows
    return out


def diff_reports(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """新旧版本（同一批 (代码, 日)）逐字段差异：新增命中 / 消失命中 / 取值变化。"""
    ov = {(v["key"][0].split("|")[0], v["date"]): v for v in old.get("values") or []}
    nv = {(v["key"][0].split("|")[0], v["date"]): v for v in new.get("values") or []}
    common = sorted(set(ov) & set(nv))
    out: dict[str, Any] = {
        "n_common": len(common),
        "old_version": old.get("factor_version"),
        "new_version": new.get("factor_version"),
        "fields": {},
    }
    for f in new.get("fields") or []:
        add = [k for k in common if is_hit(nv[k].get(f)) and not is_hit(ov[k].get(f))]
        gone = [k for k in common if is_hit(ov[k].get(f)) and not is_hit(nv[k].get(f))]
        chg = [
            k
            for k in common
            if nv[k].get(f) != ov[k].get(f) and k not in add and k not in gone
        ]
        out["fields"][f] = {"added": add, "removed": gone, "changed": chg}
    return out


def _diff_md(d: dict[str, Any], module: str) -> str:
    lines = [
        f"# {module} 新旧版本命中差异（v{d['old_version']} → v{d['new_version']}，共同 {d['n_common']} 个 (代码, 日)）",
        "",
    ]
    for f, v in d["fields"].items():
        lines.append(
            f"## {f}：新增 {len(v['added'])} / 消失 {len(v['removed'])} / 取值变 {len(v['changed'])}"
        )
        for tag in ("added", "removed", "changed"):
            if v[tag]:
                lines.append(f"- {tag}：" + "、".join(f"{c} {d_}" for c, d_ in v[tag]))
        lines.append("")
    return "\n".join(lines)


def _samples(pool, base, hits, flips, fields, args, rng) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in fields:
        boundary = (
            set().union(*[v for (ff, _p), v in flips.items() if ff == f])
            if flips
            else set()
        )
        cls = {
            "hit": sorted(hits[f] - boundary),
            "miss": sorted(set(pool) - hits[f] - boundary),
            "boundary": sorted(boundary),
        }
        rows = []
        for c, keys in cls.items():
            for key in rng.sample(keys, min(args.n_show, len(keys))):
                ps = sorted(p for (ff, p), v in flips.items() if ff == f and key in v)
                rows.append(
                    {
                        "key": list(key),
                        "class": c,
                        "value": base[key].get(f),
                        "flip_params": ps,
                    }
                )
        out[f] = rows
    return out


def write_outputs(rep: dict[str, Any], out_dir: Path) -> Path:
    d = out_dir / f"{rep['module']}__{rep['tag']}"
    d.mkdir(parents=True, exist_ok=True)
    frames = rep.pop("_frames", {})
    if rep["mode"] in ("sample", "review"):
        parts = [
            f"<html><meta charset='utf-8'><title>{rep['module']} 抽检</title><body>",
            f"<h1>{html.escape(rep['module'])} 定义校准抽检（{rep['n_bars']} bar 池，{html.escape(rep['pool'])}）</h1>",
            "<p>只画到 as-of 当根（无未来数据）。按标注表 labels.csv 的 id 填判定。</p>",
        ]
        with (d / "labels.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "id",
                    "字段",
                    "代码",
                    "日期",
                    "类别",
                    "检测值",
                    "边界翻转参数",
                ]
                + (
                    ["应命中(Y/N)", "形态理由（写形态，不写后来涨跌）"]
                    if rep["mode"] == "review"
                    else ["判定(对/错/不确定)", "备注"]
                )
            )
            n = 0
            for f, rows in rep["samples"].items():
                parts.append(f"<h2>{html.escape(f)}</h2>")
                for r in rows:
                    n += 1
                    key = tuple(r["key"])
                    fr = frames.get(key)
                    code = key[0].split("|")[0]
                    date = str(fr[0]["date"].iloc[-1])[:10] if fr else ""
                    w.writerow(
                        [
                            n,
                            f,
                            code,
                            date,
                            r["class"],
                            r["value"],
                            "、".join(r["flip_params"]),
                            "",
                            "",
                        ]
                    )
                    title = f"#{n} {code} {date} [{r['class']}] {f}={r['value']}"
                    if r["flip_params"]:
                        title += f"（边界：{'、'.join(r['flip_params'])}）"
                    parts.append(f"<div>{svg_chart(fr[1], title) if fr else ''}</div>")
        parts.append("</body></html>")
        (d / "gallery.html").write_text("".join(parts), encoding="utf-8")
    else:
        lines = [
            f"# {rep['module']} 敏感性（{rep['n_bars']} bar，{rep['pool']}，只描述不挑最优）",
            "",
            "| 参数 | 现值 | 步长 | 新值 | 字段 | 命中（新/现） | 变化 bar 数 | Jaccard |",
            "|---|---:|---:|---:|---|---:|---:|---:|",
        ]
        for r in rep["sensitivity"] or []:
            lines.append(
                f"| `{r['param']}` | {r['base_value']} | ×{r['step']} | {r['value']} | {r['field']} | "
                f"{r['n_hit']}/{r['n_hit_base']} | {r['n_changed']} | {r['jaccard']:.3f} |"
            )
        if rep["no_effect_params"]:
            lines += [
                "",
                f"> ⚠️ no_effect（全部步长零变化，须人工确认是不敏感还是改不到）：{'、'.join(rep['no_effect_params'])}",
            ]
        (d / "sensitivity.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "summary.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    return d


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="信号参数定义校准探针（TODO #121；生产机运行）"
    )
    ap.add_argument("--module", required=True, choices=sorted(MODULES))
    ap.add_argument(
        "--mode", choices=("sample", "sensitivity", "review"), default="sample"
    )
    ap.add_argument(
        "--review-source",
        choices=("stock_pool", "holdings"),
        default="stock_pool",
        help="review 名单来源：stock_pool=1800 选股池；holdings=持仓技术面汇总（n_structure / trend_box 用）",
    )
    ap.add_argument("--since", default="", help="review：1800 选股日起（含）")
    ap.add_argument("--until", default="9999-12-31", help="review：1800 选股日止（含）")
    ap.add_argument(
        "--diff", default="", help="review：与旧版 summary.json 对比，写 diff.md"
    )
    ap.add_argument(
        "--pool",
        choices=("jlow", "all"),
        default="jlow",
        help="jlow=只取日 J<13 的 bar（live 进池门之后）；all=挖掘段全部 bar（校准 J 门槛本身用）",
    )
    ap.add_argument("--n-bars", type=int, default=2000, help="bar 池大小")
    ap.add_argument("--n-show", type=int, default=12, help="每字段每类抽检图数")
    ap.add_argument(
        "--steps",
        type=lambda s: [float(x) for x in s.split(",")],
        default=list(DEFAULT_STEPS),
    )
    ap.add_argument("--workers", type=int, default=1, help="compute_metrics 进程数")
    ap.add_argument("--load-workers", type=int, default=1)
    ap.add_argument("--count", type=int, default=100000)
    ap.add_argument("--codes", default="")
    ap.add_argument("--codes-file", default=None)
    ap.add_argument("--universe-local", action="store_true")
    ap.add_argument("--universe-sample", type=int, default=0)
    ap.add_argument("--universe-seed", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20261103)
    ap.add_argument("--tag", default="v1")
    ap.add_argument("--out-dir", default="")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    try:
        rep = run_study(args)
    except RuntimeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    from custos.core.paths import ARTIFACTS  # noqa: PLC0415

    d = write_outputs(
        rep,
        Path(args.out_dir) if args.out_dir else ARTIFACTS / "reports" / "calibration",
    )
    if args.mode == "review" and args.diff:
        old = json.loads(Path(args.diff).read_text(encoding="utf-8-sig"))
        new = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        (d / "diff.md").write_text(
            _diff_md(diff_reports(old, new), rep["module"]), encoding="utf-8"
        )
        print(f"[calib_probe] 差异 ⇒ {d / 'diff.md'}")
    print(
        f"[calib_probe] {rep['module']} {rep['mode']} n_bars={rep['n_bars']} errors={rep['n_errors']} "
        f"no_effect={rep['no_effect_params']} ⇒ {d}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
