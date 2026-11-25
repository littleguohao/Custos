# -*- coding: utf-8 -*-
"""形态概念唯一实现（owner 2026-11-25「同一件事不要搞好几套实现」）。

登记表 ``REGISTRY`` = 概念 → 唯一实现（文件, 函数）；文档侧同表见 ``governance/strategy/_factors/README.md``
「概念唯一实现」节。本文件钉三件事：

1. 登记的唯一实现都存在；
2. 生产代码（core / pipeline / datasource）里，名字像这些概念的函数只能是登记的唯一实现或 ``ALLOWED`` 里
   写明理由的委托 / 消费方——新写一份同类实现即红（确属新概念：先登记再写）；
3. 不能写死涨停阈值、不能在 indicators.py 之外算 EMA / MACD；原始阴阳比较、原始量比分母**只减不增**。
"""

from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "src" / "custos"
PROD = [ROOT / "core", ROOT / "pipeline", ROOT / "datasource"]

REGISTRY = {
    "走势三态（N 结构推出）": ("core/factors/b1_structure.py", "check_swing_state"),
    "MACD 平台点（平台顶 H / 平台底 L）": (
        "core/factors/b1_structure.py",
        "macd_platform_segments",
    ),
    "上升 N 结构位": ("core/factors/b1_structure.py", "check_n_structure"),
    "下降 N 结构位": ("core/factors/b1_structure.py", "check_descending_n_structure"),
    "上涨段 / 回调段": ("core/factors/b1_structure.py", "rally_segment"),
    "价格区间（顶部 / 底部）": ("core/factors/b1_structure.py", "price_zone"),
    "顶部 / 次高点放量阴线": ("core/factors/distribution.py", "top_bear_bars"),
    "MACD 背离 / 三打白骨精": (
        "core/factors/macd_technics.py",
        "macd_divergence_v2_arrays",
    ),
    "涨停": ("core/code_utils.py", "is_limit_up"),
    "涨跌停幅度": ("core/code_utils.py", "price_limit_pct"),
    "K 线阴阳（假阴真阳按区间）": ("core/indicators.py", "candle_side"),
    "量比（量 / 前 N 日均量）": ("core/indicators.py", "vol_ratio"),
    "MACD 序列": ("core/indicators.py", "macd_series"),
    "EMA": ("core/indicators.py", "ema"),
}

NAME_PAT = re.compile(
    r"swing|pivot|fractal|rally_seg|limit_up|platform_seg|divergence|n_structure|price_zone"
    r"|candle_side|vol_ratio|vol_ma_prev|top_bear|price_limit|trend_state",
    re.I,
)

# 名字像、但不是另一份实现的函数（委托 / 包装 / 消费方 / 不同概念），逐个写理由
ALLOWED = {
    ("core/indicators.py", "vol_ma_prev"): "量比分母（vol_ratio 的一半）",
    ("core/indicators.py", "candle_sides"): "candle_side 的序列版",
    (
        "core/indicators.py",
        "price_limit_from",
    ): "涨跌停幅度：有代码委托 price_limit_pct，无代码才看行情",
    ("core/indicators.py", "_infer_price_limit"): "price_limit_from 的 df 包装",
    (
        "core/factors/s_shape.py",
        "_infer_price_limit",
    ): "indicators 不可用时的兜底，委托 price_limit_pct",
    (
        "core/factors/s_shape.py",
        "_price_limit_series_at",
    ): "预计算旁路，委托 price_limit_from",
    (
        "core/factors/wave_type.py",
        "_find_rally_segment",
    ): "委托 rally_segment（保留旧 tuple 返回形状）",
    ("core/factors/wave_type.py", "_limit_up_days"): "逐根调 is_limit_up 计数",
    (
        "core/factors/wave_type.py",
        "_top_vol_ratio",
    ): "冲刺波：高点日量比（不分阴阳），不是顶部放量阴线",
    (
        "core/factors/wave_type.py",
        "_second_start_swing",
    ): "二次启动：段前摆动幅度（量比 / 涨幅读数），不找高低点",
    ("core/factors/distribution.py", "_vol_ratio5"): "委托 vol_ratio",
    (
        "core/factors/distribution.py",
        "check_top_bear_volume",
    ): "top_bear_bars 的「只报最近一根」包装",
    (
        "core/factors/b1_structure.py",
        "_top_bears_since",
    ): "委托 top_bear_bars（同层惰性导入）",
    (
        "core/factors/bottom_patterns.py",
        "_wbot_leg_macd_divergence",
    ): "委托 macd_divergence_v2_arrays",
    ("core/factors/qn_adx_extreme.py", "_divergence"): "委托 macd_divergence_v2_arrays",
    (
        "core/factors/macd_technics.py",
        "check_macd_divergence_v2",
    ): "macd_divergence_v2_arrays 的 df 包装",
    (
        "core/factors/rsi_state.py",
        "rsi_divergence",
    ): "RSI 背离（另一指标，价格新低对 RSI），不是 MACD 背离",
    (
        "core/factors/s_shape.py",
        "compute_pivot",
    ): "S 打分的枢轴 = 窗口最高价阻力位（evidence_only），不是平台点",
    ("core/factors/s_shape.py", "_pivot_at"): "compute_pivot 的预计算旁路",
    (
        "core/factors/s_shape.py",
        "check_pocket_pivot",
    ): "口袋支点（放量阳线量 > 近 10 日最大阴量），不是高低点",
    (
        "pipeline/market_timing/technical_monitor.py",
        "trend_state",
    ): "持仓趋势：取 check_swing_state 的当前态 + 均线展示",
    (
        "pipeline/holdings/batch_holding_technical.py",
        "_n_structure_fields",
    ): "消费方：落盘字段",
    (
        "pipeline/holdings/b1_holding_state.py",
        "_n_structure_signals",
    ): "消费方：P0 / P1 信号",
    (
        "pipeline/close_review/holding_structure.py",
        "n_structure_basis",
    ): "消费方：复盘展示",
}

# 只减不增（v0.596 基线）。新代码请调 indicators.candle_side / vol_ratio；数字变小时把这里改小。
RAW_YIN_MAX = 26  # (close|c)[..] <|>|<=|>= (open|o|opn)[..]：K 线形态类（阳包阴、线上阴线、平台公式 C<O）按实体，暂留
RAW_VOL_MA5_MAX = 3  # vol[..-5..].mean() / [-6:-1].mean()：enrich / technical_monitor 的 pandas 口径与 s_shape 一处
RAW_YIN = re.compile(r"(close|c)\[[^\]]+\]\s*[<>]=?\s*(open_?|o|opn)\[")
RAW_VR = re.compile(
    r"vol\w*\[[^\]\n]*-\s*5\s*:[^\]\n]*\]\.mean\(\)|\[-6:-1\]\.mean\(\)"
)


def _files():
    return [p for r in PROD for p in r.rglob("*.py") if "__pycache__" not in p.parts]


def _rel(p):
    # 注册表 / 白名单统一用 POSIX 相对路径；Windows 的 str(Path) 会返回反斜杠。
    return p.relative_to(ROOT).as_posix()


def test_rel_uses_posix_separators_on_every_platform():
    """v0.597：Windows 也不得把反斜杠带进 REGISTRY / ALLOWED 比较。"""
    assert _rel(ROOT / "core" / "code_utils.py") == "core/code_utils.py"


def _defs():
    for p in _files():
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield _rel(p), n.name


def test_registry_targets_exist():
    have = set(_defs())
    missing = [
        f"{c}: {f}.{fn}" for c, (f, fn) in REGISTRY.items() if (f, fn) not in have
    ]
    assert not missing, "登记的唯一实现不存在：\n  " + "\n  ".join(missing)


def test_no_second_implementation():
    canon = set(REGISTRY.values())
    extra = sorted(
        f"{f}:{fn}"
        for f, fn in _defs()
        if NAME_PAT.search(fn) and (f, fn) not in canon and (f, fn) not in ALLOWED
    )
    assert not extra, (
        "同类概念已有唯一实现（见本文件 REGISTRY / _factors/README「概念唯一实现」），不要另写一份：\n  "
        + "\n  ".join(extra)
        + "\n确属新概念：先在 REGISTRY 登记；只是委托 / 消费方：加进 ALLOWED 并写理由。"
    )


def test_allowed_entries_still_exist():
    have = set(_defs())
    stale = sorted(f"{f}:{fn}" for f, fn in ALLOWED if (f, fn) not in have)
    assert not stale, "ALLOWED 里的函数已删除，请同步移除：\n  " + "\n  ".join(stale)


def test_no_hardcoded_limit_up_threshold():
    bad = []
    for p in _files():
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (
                isinstance(n, ast.Constant)
                and isinstance(n.value, float)
                and n.value in (9.8, 9.9, 9.95, 19.8, 19.9, 29.8, 29.9)
            ):
                bad.append(f"{_rel(p)}:{n.lineno} {n.value}")
    assert not bad, (
        "涨停判定走 code_utils.is_limit_up，不要写死阈值：\n  " + "\n  ".join(bad)
    )


def test_ema_and_macd_only_in_indicators():
    bad = []
    for p in _files():
        if _rel(p) == "core/indicators.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
            if line.strip().startswith("#"):
                continue
            if re.search(r"\.ewm\(|ema\([^)]*,\s*12\)\s*-\s*ema\(", line):
                bad.append(f"{_rel(p)}:{i} {line.strip()[:60]}")
    assert not bad, (
        "EMA / MACD 只在 indicators.py 算（ema / macd_series）：\n  " + "\n  ".join(bad)
    )


def _count(pat):
    n = 0
    for p in _files():
        for line in p.read_text(encoding="utf-8").split("\n"):
            if not line.strip().startswith("#") and pat.search(line):
                n += 1
    return n


def test_raw_candle_compare_only_decreases():
    n = _count(RAW_YIN)
    assert n <= RAW_YIN_MAX, (
        f"原始阴阳比较 {n} 处 > 基线 {RAW_YIN_MAX}：新代码请调 indicators.candle_side"
    )


def test_raw_vol_ma5_only_decreases():
    n = _count(RAW_VR)
    assert n <= RAW_VOL_MA5_MAX, (
        f"原始量比分母 {n} 处 > 基线 {RAW_VOL_MA5_MAX}：新代码请调 indicators.vol_ratio / vol_ma_prev"
    )
