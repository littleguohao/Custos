# -*- coding: utf-8 -*-
"""B2 战法与底部异动因子回归测试（来源 other/B1.pdf，2026-08-03）。

原文条件（逐条钉住，防止日后被"顺手放宽"）::

    B2      B1 之后 / 涨幅大于 4% / 比前一交易日放量 / J<55 / 无上影线最好
    异动    ① 突然放量量随价升 ② 后 4 天量不低于巨量一半 ③ 穿越 60 日线
            ④ 9 个月新高 ⑤「找异动之后的 B1」

B2 对本项目的主要用途是**验证信号**：把 B1 样本按"N 日内是否出现 B2"分组对比，
直接回答"什么样的 B1 会启动"。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from custos.research import backtest_factors as bt
from custos.core.factors import b2_surge_factor as bs
from custos.pipeline.market_timing.technical_monitor import kdj


def _mk(rows):
    c = np.array([r[0] for r in rows], float)
    v = np.array([r[1] for r in rows], float)
    o = np.concatenate(([c[0]], c[:-1]))
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2023-01-02", periods=len(c)),
            "open": o,
            "high": np.maximum(c, o) * 1.012,
            "low": np.minimum(c, o) * 0.988,
            "close": c,
            "volume": v,
            "amount": v * c,
        }
    )


def _b1_then(final_bar, n_flat=30, n_drop=8, drop=0.25, v_flat=4.0e5, v_drop=3.0e5):
    """构造"先 B1(连跌使 J<13) 再来一根指定 K 线"的序列。"""
    rows = [(10.0, v_flat)] * n_flat
    p = 10.0
    for _ in range(n_drop):
        p -= drop
        rows.append((p, v_drop))
    rows.append(final_bar(p))
    return _mk(rows)


class TestJSeriesMatchesKdj:
    """J 序列必须与 technical_monitor.kdj 同口径，否则 B2 的 J<55 判定就不是同一个 J。"""

    def test_matches(self):
        df = _mk([(10 + 0.3 * np.sin(i / 3), 4e5) for i in range(120)])
        js = bs._j_series(df)
        assert js is not None
        assert float(js[-1]) == pytest.approx(float(kdj(df)["j"]), abs=1e-3)

    def test_none_on_short_history(self):
        assert bs._j_series(_mk([(10.0, 4e5)] * 8)) is None


class TestB2:
    """B2 的四条硬条件 + 无上影线加分项。"""

    def test_hits_all_conditions(self):
        df = _b1_then(lambda p: (p * 1.055, 9.0e5))  # 涨 5.5% + 量 ×3
        r = bs.detect_b2(df)
        assert r["hit"] is True
        assert r["b1_before"] is True and r["b1_bars_ago"] == 1
        assert r["gain_pct"] > bs.B2_GAIN_PCT
        assert r["vol_up"] is True
        assert r["j"] < bs.B2_J_MAX

    def test_requires_prior_b1(self):
        """没有前置 B1 就不是 B2——这是它与"普通放量大阳"的区别。"""
        rows = [(10 + 0.02 * i, 4e5) for i in range(40)]
        rows.append((rows[-1][0] * 1.055, 9.0e5))
        r = bs.detect_b2(_mk(rows))
        assert r["b1_before"] is False and r["hit"] is False

    def test_requires_gain_over_4pct(self):
        df = _b1_then(lambda p: (p * 1.02, 9.0e5))  # 只涨 2%
        r = bs.detect_b2(df)
        assert r["gain_ok"] is False and r["hit"] is False

    def test_requires_volume_up(self):
        df = _b1_then(lambda p: (p * 1.055, 1.0e5))  # 涨够但缩量
        r = bs.detect_b2(df)
        assert r["vol_up"] is False and r["hit"] is False

    def test_j_ceiling_excludes_overbought(self):
        """J<55:B2 是"B1 后的确认",不是追高。"""
        rows = [(10.0, 4e5)] * 30
        p = 10.0
        for _ in range(8):
            p -= 0.25
            rows.append((p, 3e5))
        for _ in range(6):  # 连续大涨把 J 拉高
            p *= 1.06
            rows.append((p, 9e5))
        r = bs.detect_b2(_mk(rows))
        assert r["j"] > bs.B2_J_MAX
        assert r["j_ok"] is False and r["hit"] is False

    def test_no_upper_shadow_is_bonus_not_gate(self):
        """无上影线是加分项,不影响 hit（原文"最好",非必须）。"""
        df = _b1_then(lambda p: (p * 1.055, 9.0e5))
        r = bs.detect_b2(df)
        assert r["hit"] is True  # 命中与上影无关
        assert "no_upper_shadow" in r

    def test_b1_window_is_parameterized(self):
        """ "B1 之后"的天数原文未给,必须可调（现默认 5,待回测）。"""
        df = _b1_then(lambda p: (p * 1.055, 9.0e5))
        assert bs.detect_b2(df, b1_within=1)["b1_before"] is True
        assert bs.detect_b2(df, b1_within=0)["b1_before"] is False

    def test_short_history_unavailable(self):
        r = bs.detect_b2(_mk([(10.0, 4e5)] * 10))
        assert r["available"] is False and r["hit"] is False


class TestBottomSurge:
    """底部异动的四个维度分开报告（原文四条的相对重要性未知，不先合成分数）。"""

    @pytest.fixture(scope="class")
    @staticmethod
    def surge_df():
        base = [(10.0 + 0.15 * np.sin(i / 4), 4.0e5) for i in range(200)]
        p = base[-1][0]
        rows = list(base) + [(p * 1.09, 1.4e6)]  # 巨量点火 +9%、3.5×量
        for _ in range(4):
            rows.append((rows[-1][0] * 1.02, 9.0e5))  # 后4天量 > 巨量一半
        for _ in range(6):
            rows.append((rows[-1][0] * 1.012, 7.0e5))
        q = rows[-1][0]
        for _ in range(9):  # 缩量回调 → J 落低位
            q *= 0.978
            rows.append((q, 2.6e5))
        return _mk(rows)

    def test_detects_all_four_conditions(self, surge_df):
        r = bs.detect_bottom_surge(surge_df)
        assert r["hit"] is True
        assert r["vol_ratio_ma20"] >= bs.SURGE_VOL_MULT
        assert r["gain_pct"] >= bs.SURGE_GAIN_PCT
        assert r["hold_4d_ok"] is True  # 后4天量不低于巨量一半
        assert r["cross_ma60"] is True  # 穿越60日线
        assert r["new_high_9m"] is True  # 9个月新高
        assert r["strict_hit"] is True

    def test_no_surge_in_quiet_decline(self):
        df = _mk([(22.0 * (0.997**i), 3.0e5) for i in range(210)])
        r = bs.detect_bottom_surge(df)
        assert r["hit"] is False and r["reason"] == "no_surge"

    def test_conditions_reported_separately(self, surge_df):
        """四条必须分开报告,便于回测消融——不能只给一个合成分。"""
        r = bs.detect_bottom_surge(surge_df)
        for k in ("hold_4d_ok", "cross_ma60", "new_high_9m", "conditions_met"):
            assert k in r

    def test_needs_long_history(self):
        r = bs.detect_bottom_surge(_mk([(10.0, 4e5)] * 100))
        assert r["available"] is False and "180" in r["reason"]


class TestSurgeThenB1:
    """原文第③条「找异动之后的 B1」——异动确认主力进过场，B1 给回调买点。"""

    def test_hits_when_both(self, request):
        df = request.getfixturevalue("surge_df") if False else None
        # 复用 TestBottomSurge 的构造（避免跨 class fixture 依赖）
        base = [(10.0 + 0.15 * np.sin(i / 4), 4.0e5) for i in range(200)]
        p = base[-1][0]
        rows = list(base) + [(p * 1.09, 1.4e6)]
        for _ in range(4):
            rows.append((rows[-1][0] * 1.02, 9.0e5))
        for _ in range(6):
            rows.append((rows[-1][0] * 1.012, 7.0e5))
        q = rows[-1][0]
        for _ in range(9):
            q *= 0.978
            rows.append((q, 2.6e5))
        df = _mk(rows)
        r = bs.detect_surge_then_b1(df)
        assert r["hit"] is True and r["surge_hit"] is True and r["in_b1_zone"] is True

    def test_no_hit_without_surge(self):
        df = _mk([(22.0 * (0.997**i), 3.0e5) for i in range(210)])
        r = bs.detect_surge_then_b1(df)
        assert r["hit"] is False

    def test_strict_flag_is_reported(self):
        base = [(10.0 + 0.15 * np.sin(i / 4), 4.0e5) for i in range(200)]
        p = base[-1][0]
        rows = list(base) + [(p * 1.09, 1.4e6)]
        for _ in range(4):
            rows.append((rows[-1][0] * 1.02, 9.0e5))
        q = rows[-1][0]
        for _ in range(9):
            q *= 0.978
            rows.append((q, 2.6e5))
        df = _mk(rows)
        assert bs.detect_surge_then_b1(df, strict_surge=True)["strict_surge"] is True


class TestRegistration:
    @pytest.mark.parametrize("name", ["b2"])
    def test_scorer_registered(self, name):
        assert name in bt.SCORERS

    @pytest.mark.parametrize(
        "name",
        [
            "b2",
            "bottom_surge",
            "bottom_surge_strict",
            "surge_then_b1",
            "surge_strict_then_b1",
            "bottom_surge_j13",
            "bottom_surge_strict_j13",
        ],
    )
    def test_gate_registered(self, name):
        assert name in bt.ENTRY_GATES

    @pytest.mark.parametrize(
        "name",
        [
            "b2",
            "bottom_surge",
            "bottom_surge_strict",
            "surge_then_b1",
            "surge_strict_then_b1",
            "bottom_surge_j13",
            "bottom_surge_strict_j13",
        ],
    )
    def test_gates_never_raise(self, name):
        for df in (
            _mk([(10.0, 4e5)] * 5),
            _mk([(10.0, 4e5)] * 250),
            _b1_then(lambda p: (p * 1.055, 9.0e5)),
        ):
            assert isinstance(bt.ENTRY_GATES[name](df), bool)

    def test_b2_scorer_returns_none_on_short_history(self):
        """缺数据返回 None → evaluate 跳过而非填 0 分参与排名（审计 E3）。"""
        assert bt.SCORERS["b2"](_mk([(10.0, 4e5)] * 10), "600000") is None

    def test_b2_score_counts_hard_conditions(self):
        df = _b1_then(lambda p: (p * 1.055, 9.0e5))
        r = bt.SCORERS["b2"](df, "600000")
        assert r["components"]["hard_conditions"] == 4  # 四条硬条件全中
        assert r["score"] >= 80.0


class TestNotWiredIntoScreening:
    """与上批一致的纪律：只做可回测因子，先验证再接线。"""

    def test_score_candidates_untouched(self):
        import inspect

        from custos.pipeline.screening import score_candidates as sc

        src = inspect.getsource(sc)
        for name in ("detect_b2", "bottom_surge", "b2_surge_factor"):
            assert name not in src, "接入选股链前必须先有回测证据"


def _surge_then_decline(extra_decline: int = 0) -> pd.DataFrame:
    """构造「200 根平缓基线 + 巨量点火（四条件全中）+ 量维持 4 天 + 缓涨 6 天
    + 缩量回调 9 天（J 落低位）」的序列；``extra_decline`` 再追加缩量阴跌
    （把异动挤出 60 天窗口用）。"""
    base = [(10.0 + 0.15 * np.sin(i / 4), 4.0e5) for i in range(200)]
    p = base[-1][0]
    rows = list(base) + [(p * 1.09, 1.4e6)]  # 巨量点火 +9%、3.5×量
    for _ in range(4):
        rows.append((rows[-1][0] * 1.02, 9.0e5))  # 后4天量 > 巨量一半
    for _ in range(6):
        rows.append((rows[-1][0] * 1.012, 7.0e5))
    q = rows[-1][0]
    for _ in range(9 + extra_decline):  # 缩量回调 → J 落低位
        q *= 0.978
        rows.append((q, 2.6e5))
    return _mk(rows)


def _j13_indices(df: pd.DataFrame) -> list[int]:
    """60 天窗口内 J<13 的索引（测试驱动断言用，与 gate 同口径）。"""
    j = bs._j_series(df)
    n = len(df)
    return [
        i
        for i in range(max(0, n - bs.SURGE_LOOKBACK), n)
        if j[i] == j[i] and j[i] < bs.B2_J_LOW
    ]


class TestBottomSurgeJ13:
    """#13 修正口径（2026-08-12 owner 裁决）：异动后 60 天窗口内**每次 J<13 都
    触发**（不限首次）。旧 `bottom_surge`（窗口内持续为真）原样保留作对照。"""

    def test_surge_day_itself_not_triggered(self):
        """异动当日 J 不低 ⇒ 新 gate 不触发（旧 gate 在当日就触发——对照差异）。"""
        df = _surge_then_decline().iloc[:201]  # 截到点火当日
        j = bs._j_series(df)
        assert j[-1] >= bs.B2_J_LOW, "夹具前提：点火日 J 应在高位"
        assert bt.ENTRY_GATES["bottom_surge"](df) is True  # 旧口径当日即真
        assert bt.ENTRY_GATES["bottom_surge_j13"](df) is False

    def test_every_j13_dip_in_window_triggers(self):
        """窗口内每次 J<13 都触发，不限首次（owner：可以多关注几次）。"""
        df = _surge_then_decline()
        dips = _j13_indices(df)
        assert len(dips) >= 2, f"夹具前提：窗口内应有多次 J<13，实有 {dips}"
        for i in dips:
            assert bt.ENTRY_GATES["bottom_surge_j13"](df.iloc[: i + 1]) is True, (
                f"第 {i} 根 J<13 却未触发"
            )

    def test_no_trigger_without_j13(self):
        """窗口内但当日 J 不低 ⇒ 不触发（这正是对旧口径的修正点）。"""
        df = _surge_then_decline()
        j = bs._j_series(df)
        n = len(df)
        high_j = [
            i
            for i in range(n - bs.SURGE_LOOKBACK, n)
            if j[i] == j[i] and j[i] >= bs.B2_J_LOW
        ]
        assert high_j, "夹具前提：窗口内应有 J 不低的日子"
        for i in high_j:
            assert bt.ENTRY_GATES["bottom_surge_j13"](df.iloc[: i + 1]) is False

    def test_outside_window_not_triggered(self):
        """异动被挤出 60 天窗口后，即使当日 J<13 也不触发。"""
        df = _surge_then_decline(extra_decline=61)
        assert bs.detect_bottom_surge(df)["hit"] is False, "夹具前提：异动已在窗口外"
        j = bs._j_series(df)
        assert j[-1] < bs.B2_J_LOW, "夹具前提：末日 J<13"
        assert bt.ENTRY_GATES["bottom_surge_j13"](df) is False

    def test_strict_variant_requires_strict_surge(self):
        """严变体：四条件全中的异动 ⇒ 触发；宽口径异动（缺 9 个月新高）⇒ 不触发。"""
        df = _surge_then_decline()
        dips = _j13_indices(df)
        assert dips
        assert bt.ENTRY_GATES["bottom_surge_strict_j13"](df.iloc[: dips[-1] + 1])

        # 一路阴跌后的放量反弹：量随价升（宽口径中）但绝非 9 个月新高（严口径缺）
        rows = [(22.0 * (0.997**i), 3.0e5) for i in range(210)]
        rows.append((rows[-1][0] * 1.09, 1.4e6))
        q = rows[-1][0]
        for _ in range(15):
            q *= 0.978
            rows.append((q, 2.6e5))
        df2 = _mk(rows)
        r = bs.detect_bottom_surge(df2)
        assert r["hit"] is True and r["strict_hit"] is False, "夹具前提：宽中严缺"
        dips2 = _j13_indices(df2)
        assert dips2, "夹具前提：回调后应有 J<13"
        sl = df2.iloc[: dips2[-1] + 1]
        assert bt.ENTRY_GATES["bottom_surge_j13"](sl) is True
        assert bt.ENTRY_GATES["bottom_surge_strict_j13"](sl) is False
