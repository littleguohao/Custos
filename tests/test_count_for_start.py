# -*- coding: utf-8 -*-
"""--count 按窗口起点自动推算（owner 方法论 review #9，v0.328）。

count 是「最新向前 N 根」滚动窗：缺省时按 start 用 busday 交易日估算
+300 预热根数（高估属 fail-closed 方向，check_reach 实测兜底），把
「每个工具单独补 --count」的踩坑位从 CLI 上拿掉；显式值永远是覆盖通道。
"""

from __future__ import annotations

from datetime import date

import pytest

from custos.research.load_window import count_for_start, resolve_count


def test_count_for_start_known_value():
    # 2022-01-03(周一) ~ 2022-01-11(周二)：busday=[3,4,5,6,7,10] 共 6 天
    assert count_for_start("2022-01-03", today=date(2022, 1, 11)) == 6 + 300


def test_count_for_start_monotonic_in_start():
    today = date(2026, 10, 10)
    assert count_for_start("2010-01-01", today=today) > count_for_start(
        "2022-01-01", today=today
    )


def test_count_for_start_covers_real_trading_days():
    # 2010-01-01~2026-10-10 真实交易日约 4070；busday 高估（不扣节假日）
    # ⇒ 推算值必须 ≥ 真实交易日+预热（fail-closed 方向），同时远小于
    # 旧惯用的 100000 全历史（加载提速）
    n = count_for_start("2010-01-01", today=date(2026, 10, 10))
    assert 4070 + 300 <= n < 6000


def test_count_for_start_warmup_param():
    base = count_for_start("2022-01-03", today=date(2022, 1, 11))
    assert (
        count_for_start("2022-01-03", today=date(2022, 1, 11), warmup_bars=60)
        == base - 240
    )


@pytest.mark.parametrize("start", ["2026-10-10", "2026-10-11"])
def test_count_for_start_rejects_start_not_before_today(start):
    with pytest.raises(ValueError, match="不在今天"):
        count_for_start(start, today=date(2026, 10, 10))


def test_resolve_count_explicit_wins():
    assert resolve_count(100000, "2010-01-01") == 100000


@pytest.mark.parametrize("bad", [0, -1])
def test_resolve_count_rejects_non_positive(bad):
    with pytest.raises(ValueError, match="正整数"):
        resolve_count(bad, "2022-01-01")


def test_resolve_count_none_derives():
    assert resolve_count(None, "2022-01-03") == count_for_start("2022-01-03")


# ---------------------------------------------------------------------------
# 各研究终端 --count 缺省必须 = None（自动推算的入口；回归钉）
# ---------------------------------------------------------------------------

PARSER_MINIMAL_ARGV = {
    "exit_c5_terminal": [
        "--genome",
        "sp8",
        "--codes-file",
        "x",
        "--campaign-report",
        "y",
    ],
    "score_c5_terminal": ["--from-report", "x", "--codes-file", "y"],
    "factor_exit_study": ["--tag", "t"],
    "bear_regime_study": ["--tag", "t"],
    "plan_rules_replay": [
        "--mining-start",
        "2022-01-01",
        "--mining-end",
        "2024-07-31",
        "--judgment-start",
        "2024-08-01",
        "--judgment-end",
        "2026-09-04",
        "--tag",
        "t",
    ],
    "exit_campaign": ["--tag", "t"],
    "score_evolution_study": [
        "--mining-start",
        "2022-01-01",
        "--mining-end",
        "2024-07-31",
    ],
}


@pytest.mark.parametrize("module", sorted(PARSER_MINIMAL_ARGV))
def test_terminal_count_default_is_none(module):
    import importlib

    mod = importlib.import_module(f"custos.research.{module}")
    args = mod._build_parser().parse_args(PARSER_MINIMAL_ARGV[module])
    assert args.count is None, f"{module} --count 缺省必须为 None（自动推算）"
