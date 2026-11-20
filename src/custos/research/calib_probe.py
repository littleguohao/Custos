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
            # v0.507 候选：MACD 平台结构（owner 通达信公式转译；只在探针里并排，不进 live analyze）
            "n_structure_macd.state",
            "n_structure_macd.transition",
            "n_structure_macd.breached",
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
        # owner 2026-11-15：20/60 日箱体位置只是收盘与滚动高低分位的比较，退出校准。
        "fields": ["trend.state"],
        "params": [
            "TREND_MA_FAST",
            "TREND_MA_SLOW",
            "TREND_FAST_SLOPE_BARS",
            "TREND_HL_WINDOW",
        ],
    },
    "b1_thresholds": {
        "defmod": "custos.core.b1_thresholds",
        # 周 J、日 J、极致缩量均为纯数值比较，退出 A 类人工校准；反转 K 是复合形态，保留。
        "fields": ["patterns.reversal_k_candidate"],
    },
    # owner 2026-11-14：BBI 与 20 日相对上证强度均为确定性数值比较，
    # 退出 A 类人工校准；不再提供 entry_patterns 抽检入口。live 判定不变。
    "b1_structure": {
        "defmod": "custos.core.factors.b1_structure",
        # 止损参考价（滚动最低）与流动性（滚动均值）退出；其余为复合形态。
        "fields": [
            "non_one_wave.status",
            "repair_signals.signals",
            "five_day_entry.hit",
        ],
        "params": [
            "NOW_MILD_VOL_BURST",
            "NOW_BEAR_DROP_PCT",
            "NOW_BEAR_VOL_RATIO",
            "NOW_PULLBACK_VOL_RATIO",
            "NOW_TOP_ZONE",
            "REPAIR_J_PREV_MAX",
            "REPAIR_VOL_SHRINK",
            "REPAIR_CHANGE_PCT",
            "FIVE_DAY_SPIKE_RATIO",
            "FIVE_DAY_SPIKE_WINDOW",
        ],
    },
    # v0.531（owner 2026-11-16，TODO #123③）：live 保留的两项研究标注做定义校准
    "label_rd": {
        "defmod": "custos.core.factors.rsi_state",
        "eval": "signal",
        "fields": ["signal.rsi_deep_oversold"],
    },
    "label_sb": {
        "defmod": "custos.core.factors.b2_surge_factor",
        "eval": "signal",
        "fields": ["signal.surge_then_b1"],
    },
    # v0.533（owner 2026-11-17，TODO #123④）：两个展示用评分的重新校准——按分档抽样，问「分档与图形是否相符」
    "perfect_b1_fit": {
        "defmod": "custos.core.factors.perfect_b1_fit",
        "fields": ["@pb1_level"],
    },
    "s_shape": {
        "defmod": "custos.core.factors.s_shape",
        "fields": ["@sshape_suggest"],
    },
    # v0.548 前置走势形态合并校准（owner 2026-11-18）：一张图只问「B1 之前是什么走势」，owner 从固定菜单单选
    # + 附加勾「顶部放量出货」；同图并排一波流 v3 / 趋势三态 / wave_type / 出货 / N 结构读数，一份标注评估全部检测器。
    # 抽样按「一波流 v3 × 趋势三态」组合分层；无参数（不做边界翻转）。
    "trend_shape": {
        "defmod": "custos.core.factors.b1_structure",
        "eval": "trend_shape",
        "fields": ["trend_shape.combo"],
        "params": [],
        "label_mode": "shape",
    },
    "b1_structure_v3": {  # 一波流 v3 候选（owner 2026-11-17）
        "defmod": "custos.core.factors.b1_structure",
        "eval": "one_wave_v3",
        "fields": ["one_wave_v3.status"],
    },
    "hourglass_b1": {  # v0.535 沙漏后的 B1（owner 2026-11-17：只看沙漏形态）
        "defmod": "custos.core.factors.s_shape",
        "eval": "hourglass_b1",
        "fields": ["hourglass_b1.hit"],
    },
    "red_green_v2": {  # 阴阳量 + 红肥绿瘦合并定义候选（owner 2026-11-16）
        "defmod": "custos.core.factors.bottom_patterns",
        "eval": "red_green_v2",
        "fields": ["red_green_v2.status"],
    },
    "b1_structure_v2": {  # v0.520 候选对照（owner 2026-11-13 校准确认后的非一波流 v2）
        "defmod": "custos.core.factors.b1_structure",
        "eval": "b1_structure_v2",
        "fields": ["non_one_wave_v2.status"],
    },
    "volume_detectors": {
        "defmod": "custos.core.factors.volume_detectors",
        # 龙头量能只是连续 3 根与滚动最低量比较，退出；其余保留时间序列 / 多条件形态。
        "fields": [
            "volume_sustain.status",
            "three_lows.hit",
            "bottom_volume.hit",
        ],
        "params": [
            "VOLUME_SUSTAIN_WINDOW",
            "VOLUME_SUSTAIN_MIN_POST_DAYS",
            "VOLUME_SUSTAIN_RATIO",
            "VOLUME_SUSTAIN_RETREAT_DAYS",
            "THREE_LOWS_DRAWDOWN_PCT",
            "BOTTOM_VOL_RATIO",
            "BOTTOM_NO_NEW_LOW_DAYS",
            "CZ_MIN_BARS",
        ],
    },
    "ignition": {
        "defmod": "custos.core.factors.ignition",
        "fields": ["ignition.hit", "pullback_shrink.hit", "b1_ignition.hit"],
    },
    "macd_technics": {
        "defmod": "custos.core.factors.macd_technics",
        # owner 2026-11-15：zone / overextended 恢复抽检，供 owner 逐项复核。
        "fields": [
            "macd_technics.zone",
            "macd_technics.bottom_divergence.hit",
            "macd_technics.top_divergence.hit",
            "macd_technics.three_peaks.hit",
            "macd_technics.overextended.hit",
        ],
        "params": [
            "MACD_SWING_FRACTAL",
            "MACD_DIV_LOOKBACK",
            "MACD_OVEREXT_PCTL",
            "MACD_OVEREXT_WIN",
        ],
    },
    "distribution": {
        "defmod": "custos.core.factors.distribution",
        # revoked 是①/②的撤销名单；空名单=无撤销或不适用，不是普通 miss，
        # 不再进入 hit/miss/boundary 人工抽检。只校准实际风险等级。
        "fields": ["distribution.risk_level"],
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

    if "params" in MODULES[module]:  # 显式空表 = 无参数（trend_shape）
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


PB1_HIGH, PB1_MID = 6.0, 3.0  # 完美 B1 贴合分（0-8）分档：≥6 高 / ≥3 中 / 其余低


def field_value(cand: dict, path: str) -> Any:
    if path == "@pb1_level":
        sc = (cand.get("perfect_b1_fit") or {}).get("score")
        if sc is None:
            return None
        return "高" if sc >= PB1_HIGH else ("中" if sc >= PB1_MID else "低")
    if path == "@sshape_suggest":
        ss = cand.get("s_shape") or {}
        return ss.get("suggestion") if ss.get("available") else None
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
        "unclear",
        "弱",
        "横盘震荡",
        "数据不足",
        "低",  # v0.533 分档字段：低档 / 不买 视为未命中
        "不买",
    )


def evaluate(
    frames: tuple, defmod: str, overrides: dict[str, Any], fields: list[str]
) -> dict[str, Any]:
    from custos.pipeline.screening import enrich_candidates as ec  # noqa: PLC0415

    df, df_long, index_asof, code = frames
    spec = next(
        (
            v
            for v in MODULES.values()
            if v["defmod"] == defmod
            and v.get("eval")
            and set(fields) & set(v["fields"])
        ),
        None,
    )
    if spec is not None and spec["eval"] == "technical_monitor":
        from custos.pipeline.market_timing import technical_monitor as tm  # noqa: PLC0415

        with patched(defmod, overrides):
            dfl = df_long.reset_index(drop=True)
            an = tm.analyze(dfl, code)
            if any(f.startswith("n_structure_macd.") for f in fields):
                an["n_structure_macd"] = tm.macd_platform_structure(dfl)
            return {f: field_value(an, f) for f in fields}
    if spec is not None and spec["eval"] == "signal":
        from custos.pipeline.screening import signal_labels as sl  # noqa: PLC0415

        keys = tuple(f.split(".", 1)[1] for f in fields)
        with patched(defmod, overrides):
            sig = sl.compute_signals(df.reset_index(drop=True), code, keys=keys)
        # 三态 → 命中布尔；unavailable ⇒ None（is_hit 记未命中，抽样时如实显示 None）
        an = {
            "signal": {
                k: (
                    None
                    if (sig.get(k) or {}).get("state") == "unavailable"
                    else (sig.get(k) or {}).get("state") == "hit"
                )
                for k in keys
            }
        }
        return {f: field_value(an, f) for f in fields}
    if spec is not None and spec["eval"] == "trend_shape":
        rd = trend_shape_readings(frames)
        return {"trend_shape.combo": _shape_combo(rd), **rd}
    if spec is not None and spec["eval"] == "one_wave_v3":
        from custos.core.factors import b1_structure as bsm  # noqa: PLC0415

        with patched(defmod, overrides):
            an = {"one_wave_v3": bsm.check_one_wave_v3(df.reset_index(drop=True))}
        return {f: field_value(an, f) for f in fields}
    if spec is not None and spec["eval"] == "hourglass_b1":
        from custos.core.factors import s_shape as ssm  # noqa: PLC0415

        with patched(defmod, overrides):
            an = {
                "hourglass_b1": ssm.detect_hourglass_then_b1(df.reset_index(drop=True))
            }
        return {f: field_value(an, f) for f in fields}
    if spec is not None and spec["eval"] == "red_green_v2":
        from custos.core.factors import bottom_patterns as bp  # noqa: PLC0415

        with patched(defmod, overrides):
            an = {"red_green_v2": bp.detect_red_green_v2(df.reset_index(drop=True))}
        return {f: field_value(an, f) for f in fields}
    if spec is not None and spec["eval"] == "b1_structure_v2":
        from custos.core.factors import b1_structure as bs  # noqa: PLC0415

        with patched(defmod, overrides):
            an = {
                "non_one_wave_v2": bs.check_non_one_wave_v2(df.reset_index(drop=True))
            }
            return {f: field_value(an, f) for f in fields}
    aliases = next(
        (v.get("aliases") for v in MODULES.values() if v["defmod"] == defmod), None
    )
    with patched(defmod, overrides, aliases):
        cand = ec.compute_metrics(df, index_asof, code=code, df_long=df_long)
        return {f: field_value(cand, f) for f in fields}


#: 合并图集并排展示的检测器读数（键 → 中文名）
SHAPE_READINGS: dict[str, str] = {
    "one_wave_v3": "一波流 v3",
    "one_wave_v3_distribution": "一波流 v3 顶部出货",
    "swing_state": "走势三态 v4",
    "trend": "趋势三态",
    "wave_type": "前置拉升波",
    "distribution": "出货五方式",
    "n_structure": "上升 N 结构",
    "n_breached": "N 结构收盘破位",
}


def trend_shape_readings(frames: tuple) -> dict[str, Any]:
    """前置走势形态各检测器读数（只读 as-of 帧）：短帧给因子层检测器，长帧给 technical_monitor。"""
    from custos.core.factors import b1_structure as bsm  # noqa: PLC0415
    from custos.core.factors import distribution as dm  # noqa: PLC0415
    from custos.core.factors import wave_type as wt  # noqa: PLC0415
    from custos.pipeline.market_timing import technical_monitor as tm  # noqa: PLC0415

    df, df_long, _idx, code = frames
    d = df.reset_index(drop=True)
    ow = bsm.check_one_wave_v3(d)
    an = tm.analyze(df_long.reset_index(drop=True), code)
    ns = an.get("n_structure") or {}
    dist = dm.detect_distribution(d, code)
    from custos.research import swing_state as sws  # noqa: PLC0415

    return {
        "_readings": {
            "swing_state": sws.swing_state(df_long.reset_index(drop=True))["state"],
            "one_wave_v3": ow.get("status") if ow.get("available") else None,
            "one_wave_v3_distribution": ow.get("distribution"),
            "trend": (an.get("trend") or {}).get("state"),
            "wave_type": (wt.detect_wave_type(d) or {}).get("wave_type"),
            "distribution": dist.get("risk_level") if dist.get("hits") else "none",
            "n_structure": ns.get("status") if ns.get("available") else None,
            "n_breached": ns.get("breached_on_close") if ns.get("available") else None,
        }
    }


def _shape_combo(r: dict[str, Any]) -> str:
    rd = r.get("_readings") or {}
    # v0.553：复验一波流（owner 2026-11-19「再验证一次」）⇒ 只按一波流 v3 四态分层，三态自然分布
    return str(rd.get("one_wave_v3"))


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
    from custos.core import b1_thresholds  # noqa: PLC0415,F401 — 预加载以便 patch 找到全部绑定
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
    flip_steps: dict[
        tuple, list
    ] = {}  # (field, key) → ["PARAM×k", ...]（v0.502：带方向）
    for vi, (p, k, ov) in enumerate(variants[1:], start=1):
        for f in fields:
            hs = {key for key in pool if is_hit(res[(vi, key)].get(f))}
            changed = {key for key in pool if res[(vi, key)].get(f) != base[key].get(f)}
            flips.setdefault((f, p), set()).update(changed)
            for key in changed:
                flip_steps.setdefault((f, key), []).append(f"{p}×{k:g}")
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
        "blind": bool(getattr(args, "blind", False)),
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
        _attach_steps(rep["samples"], flip_steps)
        rep["_frames"] = {
            k: frames[k]
            for s in rep["samples"].values()
            for k in [tuple(x["key"]) for x in s]
        }
    if args.mode == "sample":
        rep["samples"] = _samples(pool, base, hits, flips, fields, args, rng)
        _attach_steps(rep["samples"], flip_steps)
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
                row = {
                    "key": list(key),
                    "class": c,
                    "value": base[key].get(f),
                    "flip_params": ps,
                }
                if base[key].get("_readings") is not None:
                    row["readings"] = base[key]["_readings"]
                rows.append(row)
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


def _stratified(keys: list, base: dict, f: str, n: int, rng: Any) -> list:
    """按检测值分层抽 n 个（v0.539：多档结论如 一波流 / 健康拉升 各有样本；单一取值时与原随机抽样等价）。"""
    groups: dict[str, list] = {}
    for k in keys:
        groups.setdefault(str(base[k].get(f)), []).append(k)
    if len(groups) <= 1:
        return rng.sample(keys, min(n, len(keys)))
    per = -(-n // len(groups))
    picked: list = []
    for v in sorted(groups):
        picked += rng.sample(groups[v], min(per, len(groups[v])))
    rest = [k for k in keys if k not in set(picked)]
    if len(picked) < n and rest:
        picked += rng.sample(rest, min(n - len(picked), len(rest)))
    return picked[:n]


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
        one_per_code = MODULES.get(args.module, {}).get("label_mode") == "shape"
        seen_codes: set[str] = set()
        for c, keys in cls.items():
            if one_per_code:  # v0.548：同票相邻日几乎同图 ⇒ 每票随机留一根
                by: dict[str, list] = {}
                for k in keys:
                    by.setdefault(str(k[0]).split("|")[0], []).append(k)
                keys = sorted(
                    rng.choice(v)
                    for cd, v in sorted(by.items())
                    if cd not in seen_codes
                )
            for key in _stratified(keys, base, f, args.n_show, rng):
                seen_codes.add(str(key[0]).split("|")[0])
                ps = sorted(p for (ff, p), v in flips.items() if ff == f and key in v)
                row = {
                    "key": list(key),
                    "class": c,
                    "value": base[key].get(f),
                    "flip_params": ps,
                }
                if base[key].get("_readings") is not None:
                    row["readings"] = base[key]["_readings"]
                rows.append(row)
        out[f] = rows
    return out


def _attach_steps(
    samples: dict[str, list[dict]], flip_steps: dict[tuple, list]
) -> None:
    """样本行补 ``flip_steps``（哪个参数往哪个方向改会让该 bar 判定改变，v0.502）。"""
    for f, rows in samples.items():
        for r in rows:
            r["flip_steps"] = sorted(flip_steps.get((f, tuple(r["key"])), []))


#: 三态（owner 2026-11-19）与转折菜单：先三态，再 6 种「X 转 Y」，最后不确定
SHAPE_STATES = ("震荡上涨", "横盘震荡", "震荡下跌")
_SHAPE_SHORT = {"震荡上涨": "上涨", "横盘震荡": "横盘", "震荡下跌": "下跌"}
SHAPE_MENU = (
    *SHAPE_STATES,
    *(
        f"{_SHAPE_SHORT[a]}转{_SHAPE_SHORT[b]}"
        for a in SHAPE_STATES
        for b in SHAPE_STATES
        if a != b
    ),
    "不确定",
)
#: v0.548 旧菜单 → 新菜单（labels2.csv 等旧标注换算；一波流 = 快涨后转跌，owner 待确认）
SHAPE_LEGACY = {
    "一波流": "上涨转下跌",
    "健康拉升": "震荡上涨",
    "拉升后横盘震荡": "上涨转横盘",
    "下跌后横盘震荡": "下跌转横盘",
    "下跌中": "震荡下跌",
}


def shape_now(shape: str) -> Optional[str]:
    """形态标签的「当前」状态（转折取后段）：上涨转横盘 ⇒ 横盘震荡。"""
    shape = SHAPE_LEGACY.get(shape, shape)
    if shape in SHAPE_STATES:
        return shape
    if "转" in shape:
        b = shape.split("转", 1)[1]
        return {"上涨": "震荡上涨", "下跌": "震荡下跌", "横盘": "横盘震荡"}.get(b)
    return None


#: 标注选项（网页 UI 与 ingest 同源）
LABEL_CHOICES = {
    "sample": ("对", "错", "不确定"),
    "review": ("Y", "N", "不确定"),
    "buypoint": ("买", "不买", "等"),  # v0.517 买点练习
    # v0.552 前置走势形态（owner 2026-11-19）：浓缩为三态 震荡上涨 / 横盘震荡 / 震荡下跌，
    # 中间有转折记「X 转 Y」（三态来回切换）；单选 + 「顶部放量出货」有 / 无
    "shape": SHAPE_MENU,
}
#: 形态标注的附加勾选项（可与任一形态同时勾）
SHAPE_EXTRA = "顶部放量出货"
#: v0.553 附加项（每项 有 / 无，都选才算标完）：一波流改为附加属性（owner 2026-11-19）
SHAPE_EXTRAS = (SHAPE_EXTRA, "一波流")
SHAPE_HINT = (
    "每张图两步：① 从菜单选一个形态；② 第二行「顶部放量出货」选 有 / 无（两项都选才算标完）。"
    "按你看到的走势选，不用逐个判检测器对错；备注写形态，不写后来涨跌"
)
CSV_HEAD = ["id", "字段", "代码", "日期", "类别", "检测值", "边界翻转参数"]
CSV_TAIL = {
    "sample": ["判定(对/错/不确定)", "备注"],
    "review": ["应命中(Y/N)", "形态理由（写形态，不写后来涨跌）"],
    "buypoint": ["判断(买/不买/等)", "批语"],
    "shape": ["形态", *(f"{x}(Y/N)" for x in SHAPE_EXTRAS), "备注"],
}


def label_ui(
    rows: list[list[Any]],
    mode: str,
    store_key: str,
    *,
    head: Optional[list[str]] = None,
    hint: Optional[str] = None,
    prefill: Optional[dict[str, str]] = None,
    hidden: Optional[list[str]] = None,
) -> str:
    """网页内标注（v0.502，owner 2026-11-09）：每图下「判定按钮 + 形态理由」，浏览器本地存档
    （localStorage，可分多次标），一键导出与 labels.csv 同列的 CSV；可导入已有 labels.csv 续标。
    纯静态、无网络。``rows`` = labels.csv 的前 7 列（id 起）。"""
    meta = json.dumps(
        {
            "rows": rows,
            "choices": list(LABEL_CHOICES[mode]),
            "head": (head or CSV_HEAD) + CSV_TAIL[mode],
            "key": store_key,
            "ph": "批语（看到什么、等什么条件）" if mode == "buypoint" else "形态理由",
            "prefill": prefill or {},
            "hidden": [str(x) for x in (hidden or [])],
            "extras": list(SHAPE_EXTRAS) if mode == "shape" else [],
        },
        ensure_ascii=False,
    )
    css = (
        "<style>.lbl{margin:2px 0 18px;padding:6px;background:#f6f6f6;font:14px sans-serif}"
        ".lbl button{margin-right:6px;padding:3px 12px}.lbl button.on{background:#2b6;color:#fff}"
        ".lbl input{width:70%;margin-left:8px}#bar{position:sticky;top:0;background:#fffbe6;"
        "padding:6px;border-bottom:1px solid #ccc;font:14px sans-serif;z-index:9}"
        ".done{opacity:.55}</style>"
    )
    bar = (
        "<div id='bar'>进度 <b id='prog'></b>　"
        "<label><input type='checkbox' id='onlyTodo'>只看未标</label>　"
        "<button id='exp'>导出 labels.csv</button>　"
        "导入续标 <input type='file' id='imp' accept='.csv'>　"
        "<span style='color:#888'>（"
        + (
            hint
            or "判定 = 检测器测得对不对；买点判断请写在备注开头「买点：」，作选股练习素材。理由写形态，不写后来涨跌"
        )
        + "；标注存在本浏览器，换机器请先导出）</span></div>"
    )
    js = r"""
<script>
const M = __META__;
const ls = window.localStorage;
const K = id => M.key + ':' + id;
function get(id){ try { return JSON.parse(ls.getItem(K(id)) || '{}'); } catch(e) { return {}; } }
function put(id, v){ ls.setItem(K(id), JSON.stringify(v)); }
Object.entries(M.prefill || {}).forEach(([id, r]) => { if (!ls.getItem(K(id))) put(id, {c: '', r: r}); });
const HID = new Set(M.hidden || []);
function refresh(){
  let n = 0;
  document.querySelectorAll('div.s').forEach(d => {
    const v = get(d.dataset.id), xs = v.xs || {}, done = !!v.c && M.extras.every(e => !!xs[e]);
    n += (done && !HID.has(d.dataset.id)) ? 1 : 0;
    d.classList.toggle('done', done);
    d.style.display = (HID.has(d.dataset.id) || (document.getElementById('onlyTodo').checked && done)) ? 'none' : '';
    d.querySelectorAll('button:not(.xb)').forEach(b => b.classList.toggle('on', b.textContent === v.c));
    d.querySelectorAll('button.xb').forEach(b => b.classList.toggle('on', b.dataset.x === (xs[b.dataset.e] || '')));
    const inp = d.querySelector('input:not(.ex)'); if (document.activeElement !== inp) inp.value = v.r || '';
  });
  document.getElementById('prog').textContent = n + ' / ' + (M.rows.length - HID.size) + (HID.size ? '（另 ' + HID.size + ' 张已隐藏，不需逐张标）' : '');
}
document.querySelectorAll('div.s').forEach(d => {
  const id = d.dataset.id, box = document.createElement('div');
  box.className = 'lbl';
  M.choices.forEach(c => {
    const b = document.createElement('button'); b.textContent = c;
    b.onclick = () => { const v = get(id); v.c = (v.c === c ? '' : c); put(id, v); refresh(); };
    box.appendChild(b);
  });
  M.extras.forEach(e => {  // v0.551 独立一行「有 / 无」；v0.553 多项
    const row = document.createElement('div'); row.style.margin = '6px 0';
    const t = document.createElement('b'); t.textContent = e + '：'; row.appendChild(t);
    [['有', 'Y'], ['无', 'N']].forEach(([lab, x]) => {
      const b = document.createElement('button'); b.textContent = lab; b.className = 'xb'; b.dataset.x = x; b.dataset.e = e;
      b.onclick = () => { const v = get(id); v.xs = v.xs || {}; v.xs[e] = (v.xs[e] === x ? '' : x); put(id, v); refresh(); };
      row.appendChild(b);
    });
    box.appendChild(row);
  });
  const inp = document.createElement('input'); inp.placeholder = M.ph || '形态理由';
  inp.oninput = () => { const v = get(id); v.r = inp.value; put(id, v); };
  box.appendChild(inp); d.appendChild(box);
});
const q = s => /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
document.getElementById('exp').onclick = () => {
  const out = [M.head.map(q).join(',')];
  M.rows.forEach(r => { const v = get(r[0]); out.push([...r.map(x => String(x ?? '')), v.c || '', ...M.extras.map(e => (v.xs || {})[e] || ''), v.r || ''].map(q).join(',')); });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['\ufeff' + out.join('\r\n') + '\r\n'], {type: 'text/csv'}));
  a.download = 'labels.csv'; a.click();
};
function parseCsv(t){
  const rows = []; let row = [], f = '', i = 0, inq = false;
  t = t.replace(/^\ufeff/, '');
  for (; i < t.length; i++) { const ch = t[i];
    if (inq) { if (ch === '"') { if (t[i+1] === '"') { f += '"'; i++; } else inq = false; } else f += ch; }
    else if (ch === '"') inq = true; else if (ch === ',') { row.push(f); f = ''; }
    else if (ch === '\n' || ch === '\r') { if (ch === '\r' && t[i+1] === '\n') i++; row.push(f); rows.push(row); row = []; f = ''; }
    else f += ch; }
  if (f !== '' || row.length) { row.push(f); rows.push(row); }
  return rows;
}
document.getElementById('imp').onchange = ev => {
  const fr = new FileReader();
  fr.onload = () => { parseCsv(fr.result).slice(1).forEach(r => {
    if (M.extras.length) { const k = M.extras.length, xs = {};
      M.extras.forEach((e, j) => { xs[e] = r[8 + j] || ''; });
      if (r[0] && r.slice(7, 9 + k).some(Boolean)) put(r[0], {c: r[7] || '', xs: xs, r: r[8 + k] || ''}); }
    else if (r[0] && (r[7] || r[8])) put(r[0], {c: r[7] || '', r: r[8] || ''});
  }); refresh(); };
  fr.readAsText(ev.target.files[0], 'utf-8');
};
document.getElementById('onlyTodo').onchange = refresh;
refresh();
</script>"""
    return css + bar + js.replace("__META__", meta)


def _csv_rows(rep: dict[str, Any], frames: dict) -> list[list[Any]]:
    out = []
    n = 0
    for f, rows in rep["samples"].items():
        for r in rows:
            n += 1
            key = tuple(r["key"])
            fr = frames.get(key)
            code = key[0].split("|")[0]
            date = str(fr[0]["date"].iloc[-1])[:10] if fr else ""
            steps = r.get("flip_steps") or r["flip_params"]
            out.append([n, f, code, date, r["class"], r["value"], "、".join(steps)])
    return out


#: 每个字段「检测器在测什么」的中文说明（v0.516，owner 2026-11-13：放在每张图最上方）。
#: 只说定义，不说好坏；出处见各检测器 docstring。键 = summary 里的字段名。
FIELD_DEF: dict[str, str] = {
    "trend_shape.combo": "前置走势形态（合并校准）：B1 点之前的走势属于哪种形态；抽样按一波流 v3 结论分层（v0.553）",
    "n_structure.status": "上升 N 型结构（分型版）：前低 → 前高 → 回踩不破前低形成更高的低点；状态为成立 / 确认 / 破位等",
    "n_structure.fresh_breach": "上升 N 型结构是否刚刚被跌破（收盘跌破结构前低）",
    "descending_n_structure.status": "下降 N 型结构（分型版）：高点、低点依次下移",
    "descending_n_structure.fresh_breach": "下降 N 型结构是否刚刚被向上突破",
    "n_structure_macd.state": "MACD 平台结构（往回匹配三组平台点）：最近两组平台底 / 顶都抬高 = 上升，都下移 = 下降，其余 = 横盘震荡",
    "n_structure_macd.transition": "MACD 平台结构：当前状态是延续，还是刚由另一状态转来（第三组平台点判定）",
    "n_structure_macd.breached": "MACD 平台上升结构里，当根收盘是否跌破最新平台底 L3",
    "trend.state": "趋势三态：收盘与 MA25 / MA60 的排列、MA25 斜率、近 20 日高低点抬升或下移，判上涨 / 下跌 / 横盘震荡",
    "box_20d.position": "收盘在近 20 日高低点箱体里的位置（上沿 / 中部 / 下沿等）",
    "box_60d.position": "收盘在近 60 日高低点箱体里的位置（上沿 / 中部 / 下沿等）",
    "patterns.j_low": "日线 KDJ 的 J 值是否低于 13（B1 硬门槛）",
    "patterns.reversal_k_candidate": "是否出现反转 K 线候选（低位止跌的反转形态 K 线）",
    "patterns.volume_contraction": "是否缩量（当根量能相对近期均量明显收缩）",
    "weekly_j_low": "周线 KDJ 的 J 值是否处于低位",
    "patterns.bbi_above": "收盘是否站上 BBI 多空线",
    "patterns.relative_strength_strong": "近 20 日涨幅是否明显强于上证指数（相对强势）",
    "signal.rsi_deep_oversold": "研究标注 RD（RSI 深水区）：RSI14 < 25（rsi_state.DEEP_OVERSOLD），R21 / R27 跨窗成立的唯一 RSI 标注",
    "signal.surge_then_b1": "研究标注 SB（异动后的 B1）：近 60 根内出现过底部异动（巨量点火：量 ≥ 前 20 日均量 3 倍且涨幅 ≥5%，量随价升），且当根在 B1 区间（J<13）",
    "@pb1_level": "完美 B1 贴合分（0-8）分档：J 深度（J<0 记 2、<7 记 1.5、<13 记 1）+ 收盘贴 QSX/DKS 线（≤3% 记 2、≤6% 记 1）+ 回调缩量（回调段/上涨段均量 ≤0.5 记 2、≤0.8 记 1）+ MACD 零轴上 + DKS 上行；≥6 = 高、≥3 = 中、其余 = 低",
    "@sshape_suggest": "S_shape 沙漏评分 S**（0-100）的建议档：压缩收敛 + 枢轴邻近 / 突破 + 量能 + 口袋支点 + 上方套牢 + 均线结构 + 催化 − 放量阴线惩罚；≥70 = 可买、60-69 = 观望、<60 = 不买",
    "hourglass_b1.hit": "沙漏后的 B1（只看形态）：近 60 根内出现过「先收敛、后放量突破」——突破日前 10 根相对再前 10 根日均振幅收敛到 ≤0.75 倍、均量缩到 ≤0.8 倍，突破日收盘站上前 20 根最高价且量 ≥ 前 20 根均量 1.2 倍；当根日 J<13。图上黄线 = 突破的枢轴价（前 20 根最高），标「沙漏突破」",
    "red_green_v2.status": "红肥绿瘦 v2 候选（阴阳量 + 红肥绿瘦合并，owner 2026-11-16）：近 30 根剔除爆量根（量 ≥ 窗口均量 3 倍）后，阳线总量 > 阴线总量、阳线平均实体更大、阳线根数更多 = 阳占优；三项全反 = 阴占优。阶段底部（收盘距近 120 根最高回撤 ≥30%）+ 阳占优 = red_fat（红肥绿瘦）；阶段顶部（距最高 ≤5%）+ 阴占优 = green_fat（绿肥红瘦）；其余 neutral",
    "one_wave_v3.status": "一波流判定 v3 候选（owner 2026-11-17：一波流 = 短期快速大涨后下跌，A 上 A 下）。结论三种——一波流：上涨段里 20 根内涨幅 ≥50%（A 上），且回调不缩量（回调段均量 ≥ 上涨段 0.9 倍，A 下）；健康拉升：上涨段涨幅 ≥45% 但不是一波流，且回调缩量（< 上涨段 0.7 倍）、上升 N 型结构不破（高点后收盘不跌破上涨段内最后一个回踩低点）、无出货；判断不了：没有明显上涨段（横盘 / 单边下跌 / 左侧），或回调不缩量 / 有出货。另标「出货」（高点 / 次高点前后 3 日放量大阴）",
    "non_one_wave_v2.status": "一波流判定 v2 候选（owner 2026-11-13）：健康拉升 / 疑似一波流或出货 / 判断不了三种结论；相对 v1：放量统一为 ≥ 前 5 日均量 1.5 倍且大于区内最大阳线量，次高点放量大阴也算出货，上涨段涨幅 >50% 判断不了",
    "non_one_wave.status": "一波流判定（B1 §四，现行 v1）：看最近一段上涨 + 回调。结论三种——健康拉升（confirmed：上涨温和放量、高点前后 3 日无放量大阴、回调缩量三条全满足）；疑似一波流 / 出货（revoked：高点附近有放量大阴，或回调中放量跌破启动位）；判断不了（insufficient：其余或找不到完整上涨 + 回调）",
    "repair_signals.signals": "B1 修复信号（B1 §四.2）：J 拐头向上（昨日 J≤20 今日上拐）、缩量止跌（量比≤0.7 且涨跌幅在 ±2% 内）是否出现",
    "five_day_entry.hit": "五日战法入场三条件（CZ §十六，缺一不可）：①收盘 > 5 日均价 ②近 3 日量逐日放大，或 3 日都 ≥ 20 日均量 ③近 7 日内有一天量 ≥ 前一日的 1.45 倍",
    "stop_loss_ref.price": "建议止损参考位 = 近 10 个交易日（含当根）的最低价",
    "liquidity.avg_amount_yi": "流动性 = 近 20 个交易日的日均成交额（亿元）",
    "volume_sustain.status": "量能持续性（CZ §14.6）：主线放量确认 / 量能退潮 / 中性",
    "leader_volume.hit": "龙头量能（CZ §九）：连续 3 日成交量都 ≥ 前 20 日最低日量的 1.7 倍",
    "three_lows.hit": "三低形态是否成立",
    "bottom_volume.hit": "底部巨量（CZ §14.6）：从高点回撤 ≥40%，当日量 ≥250 日均量的 2 倍，且最低价未跌破此前 20 日最低",
    "ignition.hit": "放量点火：前段缩量之后，出现放量收阳的启动 K 线",
    "pullback_shrink.hit": "回调缩量企稳：自窗口高点回撤，回调段缩量，收盘守住多空线",
    "b1_ignition.hit": "B1 点火：B1 低位之后出现放量启动 K 线",
    "macd_technics.zone": "MACD 区间：0 = 双线在水下（DIF / DEA 不全在 0 轴上方）；1 = 水上且红柱放长（本根 ≥ 上根）；2 = 水上但红柱缩短；3 = 水上但柱体已转绿或为 0",
    "macd_technics.bottom_divergence.hit": "MACD 底背离：价格创新低而 MACD 没有创新低",
    "macd_technics.top_divergence.hit": "MACD 顶背离：价格创新高而 MACD 没有创新高",
    "macd_technics.three_peaks.hit": "MACD 三峰形态是否出现",
    "macd_technics.overextended.hit": "MACD 过度伸展：|DIF| 近 120 根分位 ≥90%，且当根柱体与 DIF 同号（至少 20 根）",
    "distribution.risk_level": "主力出货风险等级（B1 §七.3）：none=无命中；watch=至少 1 腿但未达 high；high=命中①/②任一或同时 ≥2 腿",
    "distribution_confirm.revoked": "出货信号失效名单：之前命中 ① / ② 出货、后来被走势否定的信号；名单为空表示没有失效的信号",
    "wave.wave_type": "拉升波三分类（B1 §四.0）：冲刺波 sprint / 拉升波 rally / 建仓波 buildup，冲突时取保守",
    "@capital_intent.level": "资金意图 = 10 项正向量价/资金流证据按 registry 的 ci_* 权重加分：≥5 强、≥2 中、否则弱；不是单看主力净流入",
}

_CLASS_ASK = {
    "hit": "检测器判定：<b>成立</b>（{v}）。请判断：图上当根是否真的成立？成立 = 对；不该成立 = 错。",
    "miss": "检测器判定：<b>不成立</b>（{v}）。请判断：图上当根是否确实不成立？其实成立（漏判）= 错。",
    "boundary": "边界样本（{v}；参数小幅变动就会翻转：{b}）。请判断：当前判定对不对？",
}


#: 检测值的中文显示（只影响图集文字，labels.csv 与产物仍存原值；owner 2026-11-17）
VALUE_ZH: dict[str, str] = {
    "True": "成立",
    "False": "不成立",
    "true": "成立",
    "false": "不成立",
    "None": "无数据",
    "revoked": "撤销",
    "confirmed": "确认",
    "insufficient": "证据不足",
    "red_fat": "红肥绿瘦",
    "green_fat": "绿肥红瘦",
    "neutral": "中性",
    "none": "无",
    "watch": "观察",
    "high": "高",
    "mainline_confirmed": "主线确认",
    "retreat": "撤退",
    "sprint": "冲刺波",
    "rally": "拉升波",
    "buildup": "建仓波",
    "unknown": "未知",
}


#: 字段专属取值（同一个英文词在不同字段含义不同时用；owner 2026-11-17：不要双重否定）
FIELD_VALUE_ZH: dict[str, dict[str, str]] = {
    "non_one_wave.status": {
        "confirmed": "健康拉升",
        "revoked": "疑似一波流 / 出货",
        "insufficient": "判断不了",
    },
    "non_one_wave_v2.status": {
        "confirmed": "健康拉升",
        "revoked": "疑似一波流 / 出货",
        "insufficient": "判断不了",
    },
    "one_wave_v3.status": {
        "one_wave": "一波流",
        "healthy": "健康拉升",
        "unclear": "判断不了",
    },
    "distribution_confirm.revoked": {"-": "无失效信号"},
}


def value_zh(value: Any, field: str = "") -> str:
    """原值 → 「中文（原值）」；字段专属对照优先；无对照的原样返回。"""
    v = str(value)
    zh = FIELD_VALUE_ZH.get(field, {}).get(v) or VALUE_ZH.get(v)
    return f"{zh}（{v}）" if zh else v


def ask_html(field: str, cls: str, value: Any, boundary: str = "") -> str:
    """图块顶部「检测器在问什么」说明（v0.516）。数值型字段问「这个数算得对吗」。"""
    d = FIELD_DEF.get(field, f"{field}（定义见检测器源码）")
    is_bool0 = str(value).strip().lower() in ("true", "false")
    v = html.escape(str(value) if is_bool0 else value_zh(value, field))
    is_bool = str(value).strip().lower() in ("true", "false")
    if field in ("stop_loss_ref.price", "liquidity.avg_amount_yi"):
        q = f"检测器算出：<b>{v}</b>。请判断：按上面的定义，这个数算得对吗？"
    elif not is_bool:  # 状态 / 分类型字段：直接问判定对不对（revoked 等不说「成立」）
        q = f"检测器判定：<b>{v}</b>。请判断：按上面的定义，这个判定对不对？"
        if cls == "boundary" and boundary:
            q += f"（边界样本，参数小幅变动会翻转：{html.escape(boundary)}）"
    else:
        q = _CLASS_ASK.get(cls, "检测器结果：{v}。请判断是否测对。").format(
            v=v, b=html.escape(boundary or "—")
        )
    return (
        "<div class='q' style='font:14px sans-serif;background:#f4f7fb;border-left:4px solid #3b6fb6;"
        f"padding:6px 10px;margin:6px 0'><b>检测器在问：</b>{html.escape(d)}<br>{q}</div>"
    )


def shape_ask_html(readings: dict[str, Any], blind: bool = False) -> str:
    """合并图集图块顶部：问题 + 各检测器读数（只作参考）；``blind`` = 复验盲标，不显示读数（防锚定，v0.553）。"""
    cells = "　".join(
        f"{html.escape(nm)}：<b>{html.escape(value_zh(readings.get(k), k))}</b>"
        for k, nm in SHAPE_READINGS.items()
    )
    return (
        "<div class='q' style='font:14px sans-serif;background:#f4f7fb;border-left:4px solid #3b6fb6;"
        "padding:6px 10px;margin:6px 0'><b>请判断：</b>这个 B1 点（最后一根）之前是什么走势？从下方菜单选一个；"
        "再选「顶部放量出货」「一波流」各 有 / 无。"
        + (
            "<br><span style='color:#666'>复验盲标：不显示检测器读数</span>"
            if blind
            else f"<br><span style='color:#666'>检测器读数（只作参考）：{cells}</span>"
        )
        + "</div>"
    )


def upgrade_gallery(d: Path) -> int:
    """给既有产物目录的 gallery.html 加标注 UI（不重算）：第 n 个图块 ⇔ labels.csv 第 n 行。返回行数。"""
    import re as _re  # noqa: PLC0415

    summ = json.loads((d / "summary.json").read_text(encoding="utf-8-sig"))
    with (d / "labels.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = [r[:7] for r in list(csv.reader(fh))[1:] if r]
    g = (d / "gallery.html").read_text(encoding="utf-8")
    g = _re.sub(
        r"\n*<script>\nconst M = .*?</script>", "", g, flags=_re.S
    )  # 幂等：去旧 UI
    g = _re.sub(r"<style>\.lbl.*?</style><div id='bar'>.*?</div>", "", g, flags=_re.S)
    if summ.get("label_mode") == "shape" or summ.get("module") == "trend_shape":
        # v0.551 形态图集：只换标注 UI（读数说明与标题保留），同一 localStorage 键 ⇒ 已标形态不丢
        # v0.552 菜单换过 ⇒ 换存储键（旧形态值与新按钮对不上）；旧标注经 SHAPE_LEGACY 换算后导入续标
        ui = label_ui(rows, "shape", f"calib:{d.name}:v552", hint=SHAPE_HINT)
        g = g.replace("</body></html>", ui + "</body></html>")
        (d / "gallery.html").write_text(g, encoding="utf-8")
        return len(rows)
    g = _re.sub(r"<div class='q'[^>]*>.*?</div>", "", g, flags=_re.S)  # v0.516 幂等

    # 标题行检测值中文化（owner 2026-11-17）：已中文化的「中文（原值）」取括注原值重算 ⇒ 幂等、对照表改了也能刷新
    def _title(m: Any) -> str:
        head, shown, raw, tail = m.group(1), m.group(2), m.group(3), m.group(4)
        field = head.rsplit("] ", 1)[-1].rstrip("=")
        return head + html.escape(value_zh(html.unescape(raw or shown), field)) + tail

    g = _re.sub(
        r"(<div class='t'>#\d+ [^<]*?=)([^<（]+?)(?:（(?!边界)([^<（）]+)）)?((?:（边界：[^<]*）)?</div>)",
        _title,
        g,
    )
    if "const KC = " in g:  # v0.506 KLineChart 版：图块已带 data-id，不再包裹
        n = g.count("<div class='s' data-id=")
    else:
        cnt = iter(range(1, 10**9))
        g = _re.sub(
            r"<div(?: class='s' data-id='\d+')?>(?=<svg|</div>)",
            lambda m: f"<div class='s' data-id='{next(cnt)}'>",
            g,
        )
        n = next(cnt) - 1
    if n != len(rows):
        raise RuntimeError(
            f"gallery 图块数 {n} ≠ labels.csv 行数 {len(rows)}——产物不配套，拒绝升级"
        )
    # v0.516：每图顶部「检测器在问」说明（幂等：旧说明已在计数前去掉，再按 labels.csv 同序注入）
    by_id = {r[0]: r for r in rows}

    def _inject(m: Any) -> str:
        r = by_id.get(m.group(1))
        if r is None:
            return m.group(0)
        return m.group(0) + ask_html(r[1], r[4], r[5], r[6])

    g = _re.sub(r"<div class='s' data-id='(\d+)'>", _inject, g)
    # v0.518 重标：目录内 relabel.json = {key_suffix, prefill: {id: 备注}, hidden: [id], note}
    rl_path = d / "relabel.json"
    rl = json.loads(rl_path.read_text(encoding="utf-8-sig")) if rl_path.exists() else {}
    key = f"calib:{d.name}" + (f":{rl['key_suffix']}" if rl.get("key_suffix") else "")
    ui = label_ui(
        rows,
        summ["mode"],
        key,
        prefill=rl.get("prefill"),
        hidden=rl.get("hidden"),
    )
    g = _re.sub(r"<div class='rl'[^>]*>.*?</div>", "", g, flags=_re.S)  # 幂等
    if rl.get("note"):
        note = (
            "<div class='rl' style='font:14px sans-serif;background:#fff3cd;padding:8px;margin:6px 0'>"
            f"{html.escape(rl['note'])}</div>"
        )
        k = g.find("<h2>")
        g = g[:k] + note + g[k:] if k >= 0 else g.replace("<body>", "<body>" + note, 1)
    g = g.replace("</body></html>", ui + "</body></html>")
    (d / "gallery.html").write_text(g, encoding="utf-8")
    return n


def ingest(d: Path, labels: Optional[Path] = None) -> dict[str, Any]:
    """读回标注（v0.502）：逐字段 × 类别统计「对 / 错」，并按「错」样本的翻转方向汇总参数改向。

    判定口径：sample 模式直接读「对/错」；review 模式「应命中 Y/N」与当前是否命中比较（不一致 = 错）。
    参数改向表：错样本里某 ``PARAM×k`` 会翻转它 ⇒ 修正票 +1；对样本里同一 ``PARAM×k`` 也会翻转 ⇒ 误伤票 +1。
    只描述、不拍板（owner 2026-11-09：不设固定门槛，持续进化——每条「错」都进候选讨论）。"""
    summ = json.loads((d / "summary.json").read_text(encoding="utf-8-sig"))
    if summ.get("label_mode") == "shape":
        return ingest_shape(d, summ, labels)
    mode = summ["mode"]
    path = labels or d / "labels.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))[1:]
    by_field: dict[str, dict[str, Any]] = {}
    votes: dict[str, dict[str, int]] = {}
    wrong: list[dict[str, Any]] = []
    n_labeled = 0
    for r in rows:
        if len(r) < 9 or not r[7].strip():
            continue
        lid, f, code, date, cls, val, flip, lab, why = (x.strip() for x in r[:9])
        n_labeled += 1
        now_hit = cls == "hit" or (cls == "boundary" and is_hit(_parse_val(val)))
        if mode == "review":
            if lab not in ("Y", "N"):
                verdict = "不确定"
            else:
                verdict = "对" if (lab == "Y") == now_hit else "错"
        else:
            verdict = lab if lab in ("对", "错") else "不确定"
        st = by_field.setdefault(f, {}).setdefault(cls, {"对": 0, "错": 0, "不确定": 0})
        st[verdict] += 1
        steps = [x for x in flip.split("、") if x]
        for stp in steps:
            v = votes.setdefault(f"{f}｜{stp}", {"修正": 0, "误伤": 0})
            if verdict == "错":
                v["修正"] += 1
            elif verdict == "对":
                v["误伤"] += 1
        if verdict == "错":
            wrong.append(
                {
                    "id": lid,
                    "field": f,
                    "code": code,
                    "date": date,
                    "class": cls,
                    "now_hit": now_hit,
                    "should_hit": not now_hit,
                    "flip": steps,
                    "reason": why,
                }
            )
    out = {
        "module": summ["module"],
        "tag": summ.get("tag"),
        "mode": mode,
        "factor_version": summ.get("factor_version"),
        "n_rows": len(rows),
        "n_labeled": n_labeled,
        "by_field": by_field,
        "param_votes": dict(sorted(votes.items(), key=lambda kv: -kv[1]["修正"])),
        "wrong": wrong,
        "note": "只描述：误中 = 现命中但标错；漏中 = 现未命中但应命中。参数改向票只来自边界 / 近似命中样本，改不改由 owner 定",
    }
    (d / "ingest.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (d / "ingest.md").write_text(_ingest_md(out), encoding="utf-8")
    return out


#: owner 形态 → 各检测器的期望值（缺省 = 该形态对这个检测器不作要求，只进交叉表）。
#: 一波流 v3 三态：一波流 / 健康拉升 / 其余都是 unclear；趋势三态：上涨 / 下跌 / 横盘震荡。
SHAPE_EXPECT: dict[str, dict[str, str]] = {
    # v0.552：趋势三态对「当前」状态（转折取后段）；一波流 v3 不再有一一对应的形态，只进交叉表
    "trend": {
        m: {"震荡上涨": "上涨", "震荡下跌": "下跌", "横盘震荡": "横盘震荡"}[
            shape_now(m) or "横盘震荡"
        ]
        for m in (*SHAPE_MENU[:-1], *SHAPE_LEGACY)
    },
}


def _shape_checks(
    shape: str,
    top: Optional[bool],
    rd: dict[str, Any],
    ow: Optional[bool] = None,
) -> dict[str, tuple[Optional[str], str]]:
    """(期望, 检测) 对；期望 None = 不计一致率。"""
    out: dict[str, tuple[Optional[str], str]] = {
        k: (m.get(shape), str(rd.get(k))) for k, m in SHAPE_EXPECT.items()
    }
    want: Optional[str] = None if top is None else ("有" if top else "无")
    out["distribution"] = (
        want,
        "无" if rd.get("distribution") in (None, "none") else "有",
    )
    out["one_wave_v3_distribution"] = (
        want,
        "有" if rd.get("one_wave_v3_distribution") else "无",
    )
    if ow is not None:  # v0.553 一波流附加项 ⇔ 一波流 v3 判 one_wave
        out["one_wave_v3"] = (
            "有" if ow else "无",
            "有" if rd.get("one_wave_v3") == "one_wave" else "无",
        )
    if "swing_state" in rd:  # v0.553 走势三态 v2：整条标签（含转折）精确比对
        out["swing_state"] = (
            None if shape == "不确定" else shape,
            str(rd.get("swing_state")),
        )
    if shape == "不确定":
        out = {
            k: (None, g) if k in SHAPE_EXPECT or k == "swing_state" else (w, g)
            for k, (w, g) in out.items()
        }
    return out


def ingest_shape(
    d: Path, summ: dict[str, Any], labels: Optional[Path] = None
) -> dict[str, Any]:
    """合并形态图集读回（v0.548）：owner 形态换算到各检测器口径出一致率；「顶部放量出货」勾选对两个出货读数；
    wave_type、N 结构只出交叉表。只描述，改不改由 owner 定。"""
    path = labels or d / "labels.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        allrows = list(csv.reader(fh))
    head, rows = allrows[0], allrows[1:]

    def col(
        prefix: str,
    ) -> Optional[int]:  # 按表头取列（兼容 v0.548 单附加项 / v0.553 双附加项）
        return next((k for k, h in enumerate(head) if h.startswith(prefix)), None)

    c_shape, c_top, c_ow, c_why = (
        col("形态"),
        col(SHAPE_EXTRA),
        col("一波流"),
        col("备注"),
    )
    readings: dict[str, dict[str, Any]] = {}
    n = 0
    for _f, rs in summ["samples"].items():
        for r in rs:
            n += 1
            readings[str(n)] = r.get("readings") or {}
    acc: dict[str, dict[str, int]] = {}
    cross: dict[str, dict[str, dict[str, int]]] = {}
    wrong: list[dict[str, Any]] = []
    n_labeled = 0

    def yn(r: list[str], c: Optional[int]) -> Optional[bool]:
        v = r[c].strip() if c is not None and c < len(r) else ""
        return None if v not in ("Y", "N") else v == "Y"  # 未选 = 不计

    for r in rows:
        if c_shape is None or len(r) <= c_shape or not r[c_shape].strip():
            continue
        lid, code, date = r[0].strip(), r[2].strip(), r[3].strip()
        shape = SHAPE_LEGACY.get(r[c_shape].strip(), r[c_shape].strip())
        top, ow = yn(r, c_top), yn(r, c_ow)
        why = r[c_why].strip() if c_why is not None and c_why < len(r) else ""
        n_labeled += 1
        rd = readings.get(lid, {})
        key = shape + ("+顶部出货" if top else "") + ("+一波流" if ow else "")
        for k in SHAPE_READINGS:
            c = cross.setdefault(k, {}).setdefault(key, {})
            v = str(rd.get(k))
            c[v] = c.get(v, 0) + 1
        for k, (want, got) in _shape_checks(shape, top, rd, ow).items():
            if want is None:
                continue
            st = acc.setdefault(k, {"一致": 0, "不一致": 0})
            ok = want == got
            st["一致" if ok else "不一致"] += 1
            if not ok:
                wrong.append(
                    {
                        "id": lid,
                        "code": code,
                        "date": date,
                        "detector": k,
                        "owner": key,
                        "want": want,
                        "got": got,
                        "reason": why,
                    }
                )
    if summ.get("retest_of"):
        rt = retest_agreement(summ, rows, head)
        (d / "ingest.json").write_text(
            json.dumps(rt, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        L = [
            "# 形态盲标复测：owner 自洽率",
            "",
            "| 项 | 一致 | 不一致 |",
            "|---|---:|---:|",
        ]
        L += [
            f"| {k} | {v['一致']} | {v['不一致']} |"
            for k, v in rt["self_agreement"].items()
        ]
        L += ["", "## 形态改判", ""] + [
            f"- #{x['id']}（{x['src']} #{x['src_id']}）：{x['old']} → {x['new']}"
            for x in rt["shape_changed"]
        ]
        (d / "ingest.md").write_text("\n".join(L) + "\n", encoding="utf-8")
        return {"mode": "retest", "n_labeled": n_labeled, **rt}
    out = {
        "module": summ["module"],
        "tag": summ.get("tag"),
        "mode": "shape",
        "n_rows": len(rows),
        "n_labeled": n_labeled,
        "agreement": acc,
        "crosstab": cross,
        "wrong": wrong,
        "note": "只描述：owner 形态按 SHAPE_EXPECT 换算成各检测器期望值；「不确定」与无要求的形态不计一致率，只进交叉表",
    }
    (d / "ingest.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (d / "ingest.md").write_text(_ingest_shape_md(out), encoding="utf-8")
    return out


def build_retest(
    sources: list[tuple[Path, Path]], n_each: int, seed: int, out_dir: Path
) -> Path:
    """B 口径盲标复测（v0.556，校准清单第 6 条）：从已标形态图集各抽 ``n_each`` 张，打乱顺序、隐去代码 / 日期 /
    检测器读数重出，owner 再标一遍；读回时与原标注比对 = owner 自洽率。只服务 label_mode=shape 的图集。"""
    rng = random.Random(seed)
    picked: list[dict[str, Any]] = []
    for d, lab in sources:
        summ = json.loads((d / "summary.json").read_text(encoding="utf-8-sig"))
        cd = json.loads((d / "chart_data.json").read_text(encoding="utf-8"))
        with lab.open(encoding="utf-8-sig", newline="") as fh:
            allrows = list(csv.reader(fh))
        head = allrows[0]
        idx = {h.split("(")[0]: k for k, h in enumerate(head)}
        flat = [r for rs in summ["samples"].values() for r in rs]
        rows = [r for r in allrows[1:] if r and r[idx["形态"]].strip() and r[0] in cd]
        for r in rng.sample(rows, min(n_each, len(rows))):
            picked.append(
                {
                    "src": d.name,
                    "src_id": r[0],
                    "code": r[2],
                    "date": r[3],
                    "orig": {
                        k: r[idx[k]]
                        if idx.get(k) is not None and idx[k] < len(r)
                        else ""
                        for k in ("形态", *SHAPE_EXTRAS)
                    },
                    "readings": flat[int(r[0]) - 1].get("readings") or {},
                    "chart": cd[r[0]],
                }
            )
    rng.shuffle(picked)
    out = out_dir
    out.mkdir(parents=True, exist_ok=True)
    csv_rows: list[list[Any]] = []
    chart_data: dict[str, Any] = {}
    parts = [
        "<html><meta charset='utf-8'><title>形态盲标复测</title><body>",
        f"<h1>前置走势形态 · 盲标复测（{len(picked)} 张，B 口径）</h1>",
        "<p>这些图你之前标过；这次隐去代码、日期和检测器读数，顺序打乱，请按当下的判断重新标。</p>",
    ]
    for n, p in enumerate(picked, 1):
        csv_rows.append(
            [n, "trend_shape.retest", p["code"], p["date"], "retest", "", ""]
        )
        chart_data[str(n)] = {
            **p["chart"],
            "title": f"#{n}",
            "code": f"复测{n}",
            "marks": [],
        }
        parts.append(
            f"<div class='s' data-id='{n}'>{shape_ask_html({}, blind=True)}"
            f"<div class='t'>#{n}</div><div class='kc'></div></div>"
        )
    if kc_assets() is not None:
        parts.append(kc_section(chart_data))
    parts.append(label_ui(csv_rows, "shape", f"calib:{out.name}", hint=SHAPE_HINT))
    parts.append("</body></html>")
    (out / "gallery.html").write_text("".join(parts), encoding="utf-8")
    (out / "chart_data.json").write_text(
        json.dumps(chart_data, ensure_ascii=False), encoding="utf-8"
    )
    with (out / "labels.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEAD + CSV_TAIL["shape"])
        for row in csv_rows:
            w.writerow([*row, *([""] * len(CSV_TAIL["shape"]))])
    summ_out = {
        "schema": "calib_probe/v1",
        "module": "trend_shape",
        "mode": "sample",
        "label_mode": "shape",
        "tag": out.name,
        "blind": True,
        "samples": {
            "trend_shape.retest": [
                {
                    "key": [p["code"], 0],
                    "class": "retest",
                    "value": None,
                    "flip_params": [],
                    "readings": p["readings"],
                }
                for p in picked
            ]
        },
        "retest_of": [
            {k: p[k] for k in ("src", "src_id", "code", "date", "orig")} for p in picked
        ],
    }
    (out / "summary.json").write_text(
        json.dumps(summ_out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return out


def retest_agreement(
    summ: dict[str, Any], rows: list[list[str]], head: list[str]
) -> dict[str, Any]:
    """复测读回：owner 两次标注的一致率（形态整条 / 后段 / 各附加项），只描述。"""
    idx = {h.split("(")[0]: k for k, h in enumerate(head)}
    by_id = {r[0]: r for r in rows if r}
    acc: dict[str, dict[str, int]] = {}
    diff: list[dict[str, Any]] = []
    for n, p in enumerate(summ["retest_of"], 1):
        r = by_id.get(str(n))
        if r is None or not r[idx["形态"]].strip():
            continue
        new = {
            k: r[idx[k]].strip() if idx.get(k) is not None and idx[k] < len(r) else ""
            for k in ("形态", *SHAPE_EXTRAS)
        }
        old = {k: SHAPE_LEGACY.get(v, v) for k, v in p["orig"].items()}
        new["形态"] = SHAPE_LEGACY.get(new["形态"], new["形态"])
        checks = {
            "形态整条": (old["形态"], new["形态"]),
            "形态后段": (shape_now(old["形态"]), shape_now(new["形态"])),
            **{k: (old.get(k, ""), new.get(k, "")) for k in SHAPE_EXTRAS},
        }
        for k, (a, b) in checks.items():
            if not a or not b or a == "不确定" or b == "不确定":
                continue
            st = acc.setdefault(k, {"一致": 0, "不一致": 0})
            st["一致" if a == b else "不一致"] += 1
        if old["形态"] != new["形态"]:
            diff.append(
                {
                    "id": n,
                    "src": p["src"],
                    "src_id": p["src_id"],
                    "old": old["形态"],
                    "new": new["形态"],
                }
            )
    return {"self_agreement": acc, "shape_changed": diff}


def _ingest_shape_md(o: dict[str, Any]) -> str:
    L = [
        f"# 前置走势形态标注汇总（{o['tag']}；已标 {o['n_labeled']}/{o['n_rows']}）",
        "",
        "| 检测器 | 一致 | 不一致 |",
        "|---|---:|---:|",
    ]
    for k, st in o["agreement"].items():
        L.append(f"| {SHAPE_READINGS.get(k, k)} | {st['一致']} | {st['不一致']} |")
    for k, c in o["crosstab"].items():
        vals = sorted({v for m in c.values() for v in m})
        L += [
            "",
            f"## {SHAPE_READINGS.get(k, k)} × owner 形态",
            "",
            "| owner 形态 | " + " | ".join(vals) + " |",
            "|---|" + "---:|" * len(vals),
        ]
        for sh, m in c.items():
            L.append(f"| {sh} | " + " | ".join(str(m.get(v, 0)) for v in vals) + " |")
    L += ["", "## 不一致样本", ""]
    for w in sorted(o["wrong"], key=lambda x: (x["detector"], int(x["id"]))):
        L.append(
            f"- #{w['id']} {w['code']} {w['date']} {SHAPE_READINGS.get(w['detector'], w['detector'])}："
            f"owner {w['owner']} ⇒ 期望 {w['want']}，检测 {w['got']}｜{w['reason'] or '—'}"
        )
    L += ["", f"> {o['note']}"]
    return "\n".join(L) + "\n"


def _parse_val(v: str) -> Any:
    if v in ("True", "False"):
        return v == "True"
    try:
        return float(v)
    except ValueError:
        return v or None


def _ingest_md(o: dict[str, Any]) -> str:
    L = [
        f"# {o['module']} 标注汇总（{o['tag']}，{o['mode']}；已标 {o['n_labeled']}/{o['n_rows']}）",
        "",
        "| 字段 | 类别 | 对 | 错 | 不确定 |",
        "|---|---|---:|---:|---:|",
    ]
    for f, cs in o["by_field"].items():
        for c, st in cs.items():
            L.append(f"| {f} | {c} | {st['对']} | {st['错']} | {st['不确定']} |")
    L += [
        "",
        "## 参数改向票（错样本可被它修正 vs 对样本会被它误伤）",
        "",
        "| 字段｜参数×步长 | 修正 | 误伤 |",
        "|---|---:|---:|",
    ]
    for k, v in o["param_votes"].items():
        if v["修正"] or v["误伤"]:
            L.append(f"| {k} | {v['修正']} | {v['误伤']} |")
    L += ["", "## 标「错」的样本（按字段）", ""]
    for w in sorted(o["wrong"], key=lambda x: (x["field"], x["class"])):
        kind = "误中" if w["now_hit"] else "漏中"
        L.append(
            f"- #{w['id']} {w['field']} {w['code']} {w['date']} [{w['class']}·{kind}]"
            f"{'（翻转：' + '、'.join(w['flip']) + '）' if w['flip'] else ''}：{w['reason'] or '（未写理由）'}"
        )
    L += ["", f"> {o['note']}"]
    return "\n".join(L) + "\n"


#: 终端图宽（根数）——终端 80 列放得下，比网页图（CHART_BARS）短
TERM_BARS = 60


def _rsi14(df: Any) -> Any:
    from custos.core.indicators import rsi  # noqa: PLC0415

    return rsi(df["close"].astype(float), 14)


def chart_payload(df: Any, title: str) -> dict[str, Any]:
    """终端标注用的紧凑图数据（v0.503）：末 TERM_BARS 根 OHLCV + J + MACD，只到 as-of 当根。"""
    from custos.core.indicators import j_series, macd_series  # noqa: PLC0415

    t = df.tail(
        CHART_BARS
    )  # v0.506：存网页图的 CHART_BARS 根，终端图只画末 TERM_BARS 根
    dif, dea, hist = (x.tail(CHART_BARS) for x in macd_series(df["close"]))

    def r(xs: Any) -> list[float]:
        return [round(float(x), 4) for x in xs]

    return {
        "title": title,
        "dates": [str(x)[:10] for x in t["date"]],
        **{k: r(t[k]) for k in ("open", "high", "low", "close", "volume")},
        "j": r(j_series(df).tail(CHART_BARS)),
        "dif": r(dif),
        "dea": r(dea),
        "hist": r(hist),
        # v0.531：RSI14（indicators.rsi 单源）——RD 校准图集画 RSI 窗格
        "rsi": [
            None if x != x else round(float(x), 4) for x in _rsi14(df).tail(CHART_BARS)
        ],
    }


def _half(up: str, lo: str) -> str:
    """一格 = 上下两个半格：b=实体 w=影线 ' '=空。实体优先（ccharts 同法），纵向分辨率翻倍。"""
    if up == "b" and lo == "b":
        return "█"
    if up == "b":
        return "▀"
    if lo == "b":
        return "▄"
    if up == "w" and lo == "w":
        return "│"
    if up == "w":
        return "╵"
    if lo == "w":
        return "╷"
    return " "


def term_chart(cd: dict[str, Any], color: bool = True) -> str:
    """终端 K 线（v0.504 半格版，画法借鉴 ccharts；零依赖）。

    价格 12 行（=24 个半格：█▀▄ 实体、│╵╷ 影线，红涨绿跌）+ 量 3 行 + J 4 行（┄ 为 13 线）+
    MACD 6 行（半格柱红绿、DIF 黄点 / DEA 白点叠在柱上、┄ 为零轴）；最末一根 = as-of 当根，底部 ▲ 竖标。"""
    cd = {k: (v[-TERM_BARS:] if isinstance(v, list) else v) for k, v in cd.items()}
    R, G, Y, W, D, Z = (
        ("\033[31m", "\033[32m", "\033[33m", "\033[37m", "\033[2m", "\033[0m")
        if color
        else ("",) * 6
    )
    o, h, lo, c = cd["open"], cd["high"], cd["low"], cd["close"]
    n = len(c)
    if not n:
        return "（无图数据）"
    out = [cd["title"]]

    def scale(vals: list[float], lo_v: float, hi_v: float, sub: int) -> list[int]:
        sp = (hi_v - lo_v) or 1.0
        return [int(round((v - lo_v) / sp * (sub - 1))) for v in vals]

    # ── 价格：24 个半格 ──
    rows, sub = 12, 24
    top, bot = max(h), min(lo)
    hi_i, lo_i = scale(h, bot, top, sub), scale(lo, bot, top, sub)
    o_i, c_i = scale(o, bot, top, sub), scale(c, bot, top, sub)
    cell: list[list[str]] = [[" "] * sub for _ in range(n)]
    for i in range(n):
        for y in range(lo_i[i], hi_i[i] + 1):
            cell[i][y] = "w"
        for y in range(min(o_i[i], c_i[i]), max(o_i[i], c_i[i]) + 1):
            cell[i][y] = "b"
    span = (top - bot) or 1.0
    for r in range(rows - 1, -1, -1):
        line = []
        for i in range(n):
            ch = _half(cell[i][2 * r + 1], cell[i][2 * r])
            line.append((R if c[i] >= o[i] else G) + ch + Z if ch != " " else " ")
        px = bot + span * (2 * r + 1) / (sub - 1)
        out.append(f"{px:8.2f} " + "".join(line))
    # ── 量：3 行 = 6 半格，柱色随 K 线 ──
    vm = max(cd["volume"]) or 1.0
    vh = [int(round(v / vm * 6)) for v in cd["volume"]]
    for r in (2, 1, 0):
        line = []
        for i, k in enumerate(vh):
            ch = "█" if k >= 2 * r + 2 else ("▄" if k == 2 * r + 1 else " ")
            line.append((R if c[i] >= o[i] else G) + ch + Z if ch != " " else " ")
        out.append(("      量 " if r == 2 else " " * 9) + "".join(line))

    # ── 点线面板（J / MACD）：半格点，参考线 ┄ ──
    def dots(
        series: list[tuple[list[float], str]], ref: float, rows_n: int, bars: Any = None
    ) -> tuple[list[str], float, float]:
        allv = [v for s, _c in series for v in s] + [ref] + ([*bars] if bars else [])
        vb, vt = min(allv), max(allv)
        subn = rows_n * 2
        ref_i = scale([ref], vb, vt, subn)[0]
        grid = [[" "] * n for _ in range(rows_n)]
        if bars:
            b_i = scale(list(bars), vb, vt, subn)
            for i, bi in enumerate(b_i):
                a, z = sorted((bi, ref_i))
                fill = [a <= y <= z for y in range(subn)]
                col = R if bars[i] >= 0 else G
                for r in range(rows_n):
                    ch = _half(
                        "b" if fill[2 * r + 1] else " ", "b" if fill[2 * r] else " "
                    )
                    if ch != " ":
                        grid[r][i] = col + ch + Z
        for i in range(n):
            if grid[ref_i // 2][i] == " ":
                grid[ref_i // 2][i] = D + "┄" + Z
        for s, col in series:
            for i, yi in enumerate(scale(s, vb, vt, subn)):
                grid[yi // 2][i] = col + ("▀" if yi % 2 else "▄") + Z
        return ["".join(grid[r]) for r in range(rows_n - 1, -1, -1)], vb, vt

    jl, jb, jt = dots([(cd["j"], Y)], 13.0, 4)
    for k, ln in enumerate(jl):
        lab = (
            f"  J{jt:5.0f} "
            if k == 0
            else (f"   {jb:5.0f} " if k == len(jl) - 1 else " " * 9)
        )
        out.append(lab + ln)
    ml, _mb, _mt = dots([(cd["dea"], W), (cd["dif"], Y)], 0.0, 6, bars=cd["hist"])
    for k, ln in enumerate(ml):
        out.append(("   MACD " if k == 0 else " " * 9) + ln)
    out.append(" " * (9 + n - 1) + Y + "▲" + Z + " 当根")
    out.append(" " * 9 + cd["dates"][0] + " " * max(1, n - 20) + cd["dates"][-1])
    return "\n".join(out)


def _write_labels(path: Path, head: list[str], rows: list[list[str]]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(head)
        w.writerows(rows)
    tmp.replace(path)


def label_cli(
    d: Path,
    *,
    only_todo: bool = True,
    boundary_first: bool = True,
    color: bool = True,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
) -> int:
    """终端标注（v0.503，owner 2026-11-09）：逐条画图 → 按键判定 → 写形态理由，每条立刻写回 labels.csv
    （原子替换，可随时退出、下次续标）。按键：sample ``1``=对 ``2``=错 ``3``=不确定；review ``y`` / ``n`` / ``?``；
    ``s``=跳过 ``b``=上一条 ``q``=退出。边界 / 近似命中样本排前面。返回本次标注条数。"""
    summ = json.loads((d / "summary.json").read_text(encoding="utf-8-sig"))
    mode = summ["mode"]
    cdp = d / "chart_data.json"
    if not cdp.exists():
        raise RuntimeError(
            f"{d} 缺 chart_data.json（v0.503 前的产物）——重跑该模块 sample / review 生成后再标"
        )
    charts = json.loads(cdp.read_text(encoding="utf-8"))
    path = d / "labels.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        allr = list(csv.reader(fh))
    head = allr[0]
    rows = [r + [""] * (9 - len(r)) for r in allr[1:] if r]
    keys = {
        "sample": {"1": "对", "2": "错", "3": "不确定"},
        "review": {"y": "Y", "n": "N", "?": "不确定"},
    }[mode]
    order = list(range(len(rows)))
    if boundary_first:
        order.sort(key=lambda i: (rows[i][4] not in ("boundary", "near_miss"), i))
    if only_todo:
        order = [i for i in order if not rows[i][7].strip()]
    hint = "  ".join(f"{k}={v}" for k, v in keys.items()) + "  s=跳过 b=上一条 q=退出"
    pos = done = 0
    while 0 <= pos < len(order):
        r = rows[order[pos]]
        cd = charts.get(str(r[0]))
        say("\n" + "=" * 72)
        say(
            f"[{pos + 1}/{len(order)}]  #{r[0]}  {r[1]}  {r[2]} {r[3]}  类别={r[4]}  检测值={r[5]}"
            + (f"  边界={r[6]}" if r[6] else "")
            + (f"  已标={r[7]}" if r[7] else "")
        )
        say(term_chart(cd, color) if cd else "（无图数据）")
        k = ask(f"判定 [{hint}] > ").strip().lower()
        if k == "q":
            break
        if k == "b":
            pos = max(0, pos - 1)
            continue
        if k in ("s", ""):
            pos += 1
            continue
        if k not in keys:
            say(f"无效按键：{k!r}")
            continue
        r[7] = keys[k]
        r[8] = ask("形态理由（写形态，不写后来涨跌；回车留空）> ").strip()
        _write_labels(path, head, rows)
        done += 1
        pos += 1
    n_done = sum(1 for x in rows if x[7].strip())
    say(f"\n本次标注 {done} 条，已写回 {path}；已标 {n_done}/{len(rows)}")
    return done


#: KLineChart vendored 资源（v0.506，owner 2026-11-10 同意引入；Apache-2.0，见目录 README）
KC_DIR = Path(__file__).resolve().parent / "vendor" / "klinecharts"
KC_JS = KC_DIR / "klinecharts.min.js"
KC_SHA256 = "428b7c3bff6a74e121b4da8093bd0187d1c81b9fae8826f1143097280667a8a1"
_KC_RENDER = r"""<script>
const KC = __DATA__;
const UP = '#F23645', DN = '#089981';
const nz = v => (v === v ? v : null);
klinecharts.registerIndicator({
  name: 'CVOL', shortName: '量', precision: 0,
  figures: [{ key: 'v', title: 'VOL: ', type: 'bar', baseValue: 0,
    styles: ({ data }) => ({ color: data.current && data.current.up ? UP : DN, style: 'fill' }) }],
  calc: l => l.map(d => ({ v: d.volume, up: d.close >= d.open }))
});
klinecharts.registerIndicator({
  name: 'CJ', shortName: 'J', precision: 2,
  figures: [{ key: 'j', title: 'J: ', type: 'line' }, { key: 'l13', title: '13: ', type: 'line' }],
  calc: l => l.map(d => ({ j: nz(d.j), l13: 13 }))
});
klinecharts.registerIndicator({
  name: 'CRSI', shortName: 'RSI14', precision: 2,
  figures: [{ key: 'r', title: 'RSI14: ', type: 'line' }, { key: 'l25', title: '25: ', type: 'line' }],
  calc: l => l.map(d => ({ r: nz(d.rsi), l25: 25 }))
});
klinecharts.registerIndicator({
  name: 'CMACD', shortName: 'MACD', precision: 3,
  figures: [
    { key: 'dif', title: 'DIF: ', type: 'line' }, { key: 'dea', title: 'DEA: ', type: 'line' },
    { key: 'h', title: 'MACD: ', type: 'bar', baseValue: 0,
      styles: ({ data }) => ({ color: data.current && data.current.h >= 0 ? UP : DN, style: 'fill' }) }],
  calc: l => l.map(d => ({ dif: nz(d.dif), dea: nz(d.dea), h: nz(d.hist) }))
});
function kcBars(p) {
  return p.dates.map((d, i) => ({ timestamp: Date.parse(d + 'T15:00:00+08:00'), open: p.open[i], high: p.high[i],
    low: p.low[i], close: p.close[i], volume: p.volume[i], j: p.j[i], dif: p.dif[i], dea: p.dea[i], hist: p.hist[i],
    rsi: p.rsi ? p.rsi[i] : null }));
}
function kcReveal(id) {
  const el = document.querySelector("div.s[data-id='" + id + "']"), p = KC[id];
  if (!el || !p || !p.future || el._kcFutureShown) return;
  el._kcFutureShown = true;
  if (el._kc) {
    el._kcBars = el._kcBars.concat(kcBars(p.future));
    el._kc.setDataLoader({ getBars: ({ callback }) => callback(el._kcBars, false) });
    el._kc.resetData();
  }
  const title = el.querySelector('.t');
  if (title && !title.querySelector('.kc-future-note')) {
    const note = document.createElement('span');
    note.className = 'kc-future-note'; note.textContent = '　（已展示后 20 根）'; note.style.color = '#c56a00';
    title.appendChild(note);
  }
}
function kcDraw(el) {
  const p = KC[el.dataset.id], box = el.querySelector('.kc');
  if (!p || !box || box.dataset.done) return;
  box.dataset.done = '1';
  const ch = klinecharts.init(box, { styles: {
    candle: { bar: { upColor: UP, downColor: DN, upBorderColor: UP, downBorderColor: DN, upWickColor: UP, downWickColor: DN },
              priceMark: { last: { show: false } } },
    indicator: { lines: [{ color: '#E5A50A' }, { color: '#888888', style: 'dashed', dashedValue: [4, 4] }, { color: '#4C8BF5' }] } } });
  ch.setSymbol({ ticker: p.code || 'x', pricePrecision: 2, volumePrecision: 0 });
  ch.setPeriod({ span: 1, type: 'day' });
  let bars = kcBars(p);
  if (el._kcFutureShown) bars = bars.concat(kcBars(p.future));
  el._kcBars = bars;
  ch.setDataLoader({ getBars: ({ callback }) => callback(bars, false) });
  ch.createIndicator({ name: 'CVOL', paneId: 'p_vol' });
  ch.createIndicator({ name: 'CJ', paneId: 'p_j' });
  ch.createIndicator({ name: 'CMACD', paneId: 'p_macd' });
  if (p.rsi) ch.createIndicator({ name: 'CRSI', paneId: 'p_rsi' });  // v0.531：RD 校准用
  const ts = d => Date.parse(d + 'T15:00:00+08:00');
  const first = bars[0].timestamp;
  (p.marks || []).forEach(m => {
    const col = m.kind === 'H' ? '#E5A50A' : '#7E57C2';
    const s = Math.max(ts(m.start), first);
    ch.createOverlay({ name: 'segment', lock: true, points: [{ timestamp: s, value: m.price }, { timestamp: ts(m.end), value: m.price }],
      styles: { line: { color: col, size: m.label ? 2 : 1, style: m.ongoing ? 'dashed' : 'solid' } } });
    if (m.label) ch.createOverlay({ name: 'simpleTag', lock: true, points: [{ timestamp: ts(m.date), value: m.price }], extendData: m.label + ' ' + m.price,
      styles: { line: { color: col }, text: { backgroundColor: col, borderColor: col } } });
  });
  const asOf = bars[p.dates.length - 1];
  if (p.future) {
    const low = Math.min(...p.low, ...p.future.low), high = Math.max(...p.high, ...p.future.high);
    ch.createOverlay({ name: 'segment', lock: true,
      points: [{ timestamp: asOf.timestamp, value: low }, { timestamp: asOf.timestamp, value: high }],
      styles: { line: { color: '#4C8BF5', size: 1.5, style: 'dashed' } } });
  }
  ch.createOverlay({ name: 'simpleAnnotation', points: [{ timestamp: asOf.timestamp, value: asOf.high }],
    extendData: p.future ? '买点' : '当根',
    styles: { line: { color: '#4C8BF5' }, text: { backgroundColor: '#4C8BF5', borderColor: '#4C8BF5' } } });
  el._kc = ch;
}
const kcObs = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { kcDraw(e.target); kcObs.unobserve(e.target); } }),
  { rootMargin: '600px 0px' });
document.querySelectorAll('div.s').forEach(el => kcObs.observe(el));
</script>
"""


#: 图集上画 MACD 平台线（L1 / L2 / H1 / H2）的模块（v0.507）
_MARK_MODULES = frozenset({"n_structure", "trend_box"})


def platform_marks(df: Any) -> list[dict[str, Any]]:
    """图窗（末 CHART_BARS 根）内的 MACD 平台点；往回匹配的三组标 L1~L3 / H1~H3（v0.512）。"""
    from custos.pipeline.market_timing import technical_monitor as tm  # noqa: PLC0415

    segs = tm.macd_platform_segments(df)
    first = str(df["date"].iloc[max(0, len(df) - CHART_BARS)])[:10]
    # v0.512：标号与候选结构同源（往回匹配三组，同类合并取极值；L1/H1 最早、L3/H3 最新）
    pts = tm.macd_platform_points(segs, 3)
    for kind in ("L", "H"):
        ks = [x for x in pts if x["kind"] == kind][:3]
        for n, x in enumerate(ks):
            x["label"] = f"{kind}{len(ks) - n}"
    return [
        {
            k: x.get(k)
            for k in ("kind", "price", "date", "start", "end", "label", "ongoing")
        }
        for x in segs
        if x["end"] >= first
    ]


def hourglass_marks(df: Any) -> list[dict[str, Any]]:
    """图窗内的沙漏事件：枢轴线（突破前 20 根到突破日）+「沙漏突破」标签（v0.535）。"""
    from custos.core.factors import s_shape as ssm  # noqa: PLC0415

    first_i = max(0, len(df) - CHART_BARS)
    ds = [str(x)[:10] for x in df["date"]]
    out = []
    for e in ssm.hourglass_events(df):
        t = e["t"]
        if t < first_i or t >= len(df) - 1:
            continue
        out.append(
            {
                "kind": "H",
                "price": e["pivot"],
                "date": ds[t],
                "start": ds[max(0, t - 20)],
                "end": ds[t],
                "label": "沙漏突破",
                "ongoing": False,
            }
        )
    return out


def kc_assets() -> Optional[str]:
    """KLineChart 内联脚本（校验 sha256；缺失 / 不符 ⇒ None，图集退回静态 SVG）。"""
    import hashlib  # noqa: PLC0415

    from custos.core import code_freeze as _cf  # noqa: PLC0415

    try:
        b = _cf.read_bytes(KC_JS)
    except OSError:
        return None
    # 换行规范化后再比（Windows 旧检出可能是 CRLF；.gitattributes 已禁转换，此处兜底）
    b = b.replace(b"\r\n", b"\n")
    if hashlib.sha256(b).hexdigest() != KC_SHA256:
        print("[WARN] klinecharts.min.js 摘要不符，图集退回静态 SVG", file=sys.stderr)
        return None
    return b.decode("utf-8")


def kc_section(chart_data: dict[str, Any]) -> str:
    """图集交互图层：内联 KLineChart + 自定义指标（数值全来自 indicators.py，库只画图）+ 懒加载。"""
    lib = kc_assets()
    if lib is None:
        return ""
    data = json.dumps(chart_data, ensure_ascii=False).replace("</", "<\\/")
    css = (
        "<style>div.s .kc{height:540px;width:100%;max-width:1100px;border:1px solid #ddd}"
        "div.s .t{font:14px sans-serif;margin:14px 0 4px}</style>"
    )
    return (
        css
        + "<script>"
        + lib.replace("</script", "<\\/script")
        + "</script>"
        + _KC_RENDER.replace("__DATA__", data)
    )


def write_outputs(rep: dict[str, Any], out_dir: Path) -> Path:
    d = out_dir / f"{rep['module']}__{rep['tag']}"
    d.mkdir(parents=True, exist_ok=True)
    frames = rep.pop("_frames", {})
    lmode = MODULES.get(rep["module"], {}).get("label_mode") or rep["mode"]
    rep["label_mode"] = lmode
    if rep["mode"] in ("sample", "review"):
        parts = [
            f"<html><meta charset='utf-8'><title>{rep['module']} 抽检</title><body>",
            f"<h1>{html.escape(rep['module'])} 定义校准抽检（{rep['n_bars']} bar 池，{html.escape(rep['pool'])}）</h1>",
            "<p>只画到 as-of 当根（无未来数据）。每图下方点选判定、写形态理由，标完点顶部「导出 labels.csv」。</p>",
        ]
        csv_rows = _csv_rows(rep, frames)
        with (d / "labels.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(CSV_HEAD + CSV_TAIL[lmode])
            for row in csv_rows:
                w.writerow([*row, *([""] * len(CSV_TAIL[lmode]))])
        it = iter(csv_rows)
        chart_data: dict[str, Any] = {}
        use_kc = kc_assets() is not None
        for f, rows in rep["samples"].items():
            parts.append(f"<h2>{html.escape(f)}</h2>")
            for r in rows:
                row = next(it)
                fr = frames.get(tuple(r["key"]))
                title = f"#{row[0]} {row[2]} {row[3]} [{r['class']}] {f}={value_zh(r['value'], f)}"
                if row[6]:
                    title += f"（边界：{row[6]}）"
                if lmode == "shape":
                    title = f"#{row[0]} {row[2]} {row[3]}"
                if use_kc:
                    parts.append(
                        f"<div class='s' data-id='{row[0]}'>"
                        + (
                            shape_ask_html(
                                r.get("readings") or {}, blind=bool(rep.get("blind"))
                            )
                            if lmode == "shape"
                            else ask_html(f, r["class"], r["value"], row[6])
                        )
                        + f"<div class='t'>{html.escape(title)}</div>"
                        f"<div class='kc'></div></div>"
                    )
                else:
                    parts.append(
                        f"<div class='s' data-id='{row[0]}'>{svg_chart(fr[1], title) if fr else ''}</div>"
                    )
                if fr:
                    chart_data[str(row[0])] = {
                        **chart_payload(fr[1], title),
                        "code": row[2],
                        "marks": platform_marks(fr[1])
                        if rep["module"] in _MARK_MODULES
                        else (
                            hourglass_marks(fr[1])
                            if rep["module"] == "hourglass_b1"
                            else []
                        ),
                    }
        (d / "chart_data.json").write_text(
            json.dumps(chart_data, ensure_ascii=False), encoding="utf-8"
        )
        if use_kc:
            parts.append(kc_section(chart_data))
        parts.append(
            label_ui(
                csv_rows,
                lmode,
                f"calib:{d.name}",
                hint=SHAPE_HINT if lmode == "shape" else None,
            )
        )
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
        "--mode",
        choices=("sample", "sensitivity", "review", "ui", "ingest", "label"),
        default="sample",
        help="ui=给既有产物目录的 gallery 加标注界面（不重算）；label=终端逐条标注（写回 labels.csv）；"
        "ingest=读回标注出汇总（ingest.md）",
    )
    ap.add_argument("--dir", default="", help="ui / ingest：产物目录（<模块>__<tag>）")
    ap.add_argument(
        "--labels", default="", help="ingest：标注 CSV（缺省 = 目录内 labels.csv）"
    )
    ap.add_argument(
        "--all", action="store_true", help="label：已标的也逐条过一遍（缺省只看未标）"
    )
    ap.add_argument("--no-color", action="store_true", help="label：终端不用颜色")
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
    ap.add_argument(
        "--blind",
        action="store_true",
        help="形态图集盲标：图上不显示检测器读数（复验用，防锚定）",
    )
    ap.add_argument("--seed", type=int, default=20261103)
    ap.add_argument("--tag", default="v1")
    ap.add_argument("--out-dir", default="")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    if args.mode in ("ui", "ingest", "label"):
        d = Path(args.dir)
        if not (d / "summary.json").exists():
            print(
                f"[FAIL] {d} 不是 calib_probe 产物目录（缺 summary.json）",
                file=sys.stderr,
            )
            return 1
        try:
            if args.mode == "label":
                import os  # noqa: PLC0415

                label_cli(
                    d,
                    only_todo=not args.all,
                    color=not args.no_color and not os.environ.get("NO_COLOR"),
                )
            elif args.mode == "ui":
                n = upgrade_gallery(d)
                print(f"[calib_probe] 标注界面已加 {n} 图 ⇒ {d / 'gallery.html'}")
            else:
                o = ingest(d, Path(args.labels) if args.labels else None)
                if not o["n_labeled"]:
                    print("[FAIL] 标注表里没有任何已填判定", file=sys.stderr)
                    return 1
                print(
                    f"[calib_probe] 已标 {o['n_labeled']}/{o['n_rows']}，错 {len(o['wrong'])} ⇒ {d / 'ingest.md'}"
                )
        except RuntimeError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 1
        return 0
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
