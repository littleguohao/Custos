# -*- coding: utf-8 -*-
"""QN 因子批（骑牛登山体系，8 个）的合成钉测。

每个检测器至少钉四件事（同 bottom_patterns 合成用例思路）：
① 正例命中；② 缺腿不命中（消融）；③ 短数据 → available=False；④ 垃圾输入不 raise。
外加注册表元数据与「debug ⇒ live_use=none」的项目级约束（test_factor_registry
的全量循环已覆盖，此处只钉 qn 批的集合与 detect 可调用性）。

⚠️ 这些测试钉的是**检测器语义**（规则转译的确定性实现），不是规则的盈利能力
——盈利验证走 R31 预注册 + 生产机回测（governance/research/R31_*）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from custos.core import factors
from custos.core.factors import (
    qn_adx_extreme,
    qn_box_target,
    qn_kdj_neg_day,
    qn_ma25_state,
    qn_ma144_launch,
    qn_macd_bar_shift,
    qn_three_red,
    qn_volume_surge_cut,
)

QN_IDS = [
    "qn_ma25_state",
    "qn_volume_surge_cut",
    "qn_three_red",
    "qn_macd_bar_shift",
    "qn_kdj_neg_day",
    "qn_adx_extreme",
    "qn_box_target",
    "qn_ma144_launch",
]


def _df(close, open_=None, vol=None, spread=0.01):
    """由收盘序列造合成 OHLCV：open 默认前收，high/low 由 ±spread 撑开。"""
    c = np.asarray(close, dtype=float)
    n = len(c)
    o = np.asarray(open_, dtype=float) if open_ is not None else np.roll(c, 1)
    o[0] = c[0]
    v = np.asarray(vol, dtype=float) if vol is not None else np.full(n, 1e6)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n).astype(str),
            "open": o,
            "close": c,
            "high": np.maximum(o, c) * (1 + spread),
            "low": np.minimum(o, c) * (1 - spread),
            "volume": v,
        }
    )


def _trend(n, start, step):
    return [start + i * step for i in range(n)]


# ─────────────────────────── 注册表 ───────────────────────────


class TestRegistry:
    def test_all_qn_registered(self):
        reg = factors.registry()
        missing = [f for f in QN_IDS if f not in reg]
        assert not missing, f"未注册：{missing}"

    @pytest.mark.parametrize("fid", QN_IDS)
    def test_meta_debug_untested_none(self, fid):
        m = factors.registry()[fid]["meta"]
        assert m["status"] == "untested"
        assert m["live_use"] == "none"
        assert m["stage"] == "debug"
        assert callable(factors.registry()[fid]["detect"])


# ─────────────────────── qn_ma25_state ───────────────────────


class TestMa25State:
    def test_hit_online_shrink_yin(self):
        # 升势站上 MA25，末根缩量阴线 → 线上阴线买候选
        close = _trend(40, 10.0, 0.1)
        close[-1] = close[-2] - 0.02  # 末根阴线（收<开=前收）
        vol = np.full(40, 1e6)
        vol[-1] = 5e5  # 缩量
        r = qn_ma25_state.detect(_df(close, vol=vol))
        assert r["available"] and r["hit"]
        assert r["above_ma25"] and r["state"] == "线上阴线"
        assert r["sell_candidate"] is False

    def test_no_hit_online_yang(self):
        close = _trend(40, 10.0, 0.1)
        r = qn_ma25_state.detect(_df(close))
        assert r["available"] and not r["hit"]
        assert r["state"] == "线上阳线"

    def test_sell_candidate_offline_yang(self):
        # 持续阴跌跌破 MA25，末根阳线（收>前收）→ 线下阳线抛候选
        close = _trend(33, 20.0, -0.15) + [15.25]
        r = qn_ma25_state.detect(_df(close))
        assert r["available"] and not r["above_ma25"]
        assert r["sell_candidate"] is True
        assert r["hit"] is False

    def test_short_data(self):
        r = qn_ma25_state.detect(_df(_trend(10, 10.0, 0.1)))
        assert r["available"] is False and r["hit"] is False


# ───────────────────── qn_volume_surge_cut ───────────────────


def _surge_cut_df(vol_mult=2.5, was_below=True):
    """构造倍量切正例：长期横盘（MA 粘合在 10 附近）→ 一根下砸（收 9.8，
    收在 MA5/MA10 之下）→ 末根放量大阳切回两线之上。"""
    n = 40
    close = [10.0] * (n - 2) + [9.8, 10.6]
    open_ = [10.0] * (n - 2) + [9.9, 9.9]
    if not was_below:
        close[-2] = 10.6
        open_[-2] = 10.5
    vol = np.full(n, 1e6)
    vol[-1] = 1e6 * vol_mult
    return _df(close, open_=open_, vol=vol, spread=0.001)


class TestVolumeSurgeCut:
    def test_hit(self):
        r = qn_volume_surge_cut.detect(_surge_cut_df())
        assert r["available"] and r["hit"]
        assert r["legs"]["surge"]["hit"] and r["legs"]["cut"]["hit"]

    def test_no_hit_without_surge(self):
        r = qn_volume_surge_cut.detect(_surge_cut_df(vol_mult=1.2))
        assert r["available"] and not r["hit"]
        assert r["legs"]["surge"]["hit"] is False

    def test_no_hit_without_cut(self):
        # 前收已在 MA5/MA10 上方 → 不是「从线下切断」
        r = qn_volume_surge_cut.detect(_surge_cut_df(was_below=False))
        assert r["available"] and not r["hit"]
        assert r["legs"]["cut"]["hit"] is False

    def test_short_data(self):
        r = qn_volume_surge_cut.detect(_df(_trend(10, 10.0, 0.1)))
        assert r["available"] is False and r["hit"] is False


# ─────────────────────── qn_three_red ────────────────────────


class TestThreeRed:
    def test_hit_long_uptrend(self):
        # 900 根指数上升（绝对增量递增 → 三周期柱持续为正；月线 ≥20 根可算）
        close = [10.0 * 1.002**i for i in range(900)]
        r = qn_three_red.detect(_df(close))
        assert r["available"]
        assert r["legs"]["daily"]["red"] and r["legs"]["weekly"]["red"]
        assert r["legs"]["monthly"]["red"]
        assert r["hit"] and r["monthly_green"] is False

    def test_no_hit_long_downtrend(self):
        # 900 根加速下跌（动量不减 → 三周期柱持续为负）
        close = [200.0 - 0.0002 * i * i for i in range(900)]
        r = qn_three_red.detect(_df(close))
        assert r["available"] and not r["hit"]
        assert r["monthly_green"] is True

    def test_short_history_legs_unavailable(self):
        # 60 根：日线腿可算，周/月腿不足 → hit=False 且如实标注
        r = qn_three_red.detect(_df(_trend(60, 10.0, 0.1)))
        assert r["available"] and not r["hit"]
        assert r["legs"]["monthly"]["available"] is False

    def test_short_data(self):
        r = qn_three_red.detect(_df(_trend(30, 10.0, 0.1)))
        assert r["available"] is False and r["hit"] is False


# ───────────────────── qn_macd_bar_shift ─────────────────────


class TestMacdBarShift:
    def test_buy_small_green(self):
        # 加速下跌建立深绿柱，末尾 3 根温和回升 → 绿柱连缩且收盘不破前低
        base = [60.0 - 0.01 * i * i for i in range(60)]
        close = base + [base[-1] + t for t in (0.05, 0.10, 0.16)]
        r = qn_macd_bar_shift.detect(_df(close))
        assert r["available"]
        assert r["legs"]["shrink_green"]["hit"], r["legs"]
        assert r["hit"]

    def test_sell_small_red(self):
        # 加速上升建立大红柱，末尾 3 根温和回落 → 红柱连缩且滞涨
        base = [10.0 + 0.01 * i * i for i in range(60)]
        close = base + [base[-1] + t for t in (-0.05, -0.10, -0.16)]
        r = qn_macd_bar_shift.detect(_df(close))
        assert r["available"]
        assert r["legs"]["shrink_red"]["hit"], r["legs"]
        assert r["sell_small_red"]

    def test_no_hit_green_expanding(self):
        # 持续加速下跌 → 绿柱放大而非缩短
        close = [40.0] + [40.0 - 0.1 * i * i / 10 for i in range(1, 52)]
        r = qn_macd_bar_shift.detect(_df(close))
        assert r["available"] and not r["hit"]
        assert r["legs"]["shrink_green"]["hit"] is False

    def test_short_data(self):
        r = qn_macd_bar_shift.detect(_df(_trend(20, 10.0, 0.1)))
        assert r["available"] is False and r["hit"] is False


# ───────────────────── qn_kdj_neg_day ────────────────────────


class TestKdjNegDay:
    def test_kd20_golden_cross_hit(self):
        # 长期阴跌后两根小阳、末根完成 K 上穿 D（K/D/J 均 <20 的低位金叉）
        close = _trend(45, 30.0, -0.3) + [16.6, 16.9]
        r = qn_kdj_neg_day.detect(_df(close))
        assert r["available"]
        assert r["legs"]["kd20_golden"]["hit"], r["legs"]
        assert r["hit"]

    def test_neg_day_count_helper(self):
        # 计数器语义钉：J 序列 [峰→死叉→3 根负值]
        j = np.array([80.0, 60.0, 40.0, -5.0, -8.0, -3.0])
        k = np.array([70.0, 65.0, 45.0, 30.0, 25.0, 22.0])
        d = np.array([60.0, 62.0, 50.0, 35.0, 28.0, 24.0])
        cross = qn_kdj_neg_day._find_death_cross(k, d)
        assert cross == 3  # 死叉在倒数第 4 根（K 自上穿破 D）
        assert qn_kdj_neg_day._neg_day_count(j, cross) == 3

    def test_neg_day_count_breaks_on_positive(self):
        j = np.array([-5.0, 2.0, -4.0, -6.0])
        assert qn_kdj_neg_day._neg_day_count(j, 3) == 2  # 中间 J≥0 计数中断

    def test_no_hit_uptrend(self):
        r = qn_kdj_neg_day.detect(_df(_trend(40, 10.0, 0.2)))
        assert r["available"] and not r["hit"]

    def test_short_data(self):
        r = qn_kdj_neg_day.detect(_df(_trend(10, 10.0, 0.1)))
        assert r["available"] is False and r["hit"] is False


# ───────────────────── qn_adx_extreme ────────────────────────


class TestAdxExtreme:
    def test_hit_strong_trend(self):
        # 强趋势（每日 +1%）→ ADX 冲到 60 上方
        close = [10.0 * 1.01**i for i in range(90)]
        r = qn_adx_extreme.detect(_df(close))
        assert r["available"] and r["legs"]["adx"]["adx"] >= 60.0, r["legs"]
        assert r["hit"]

    def test_no_hit_choppy(self):
        # 完全走平（DM=0）→ ADX 归零；交替涨跌会被 DMI 读成强方向，不能当反例
        close = [10.0] * 90
        r = qn_adx_extreme.detect(_df(close))
        assert r["available"] and not r["hit"]
        assert r["legs"]["adx"]["adx"] < 60.0

    def test_short_data(self):
        r = qn_adx_extreme.detect(_df(_trend(20, 10.0, 0.1)))
        assert r["available"] is False and r["hit"] is False


# ───────────────────── qn_box_target ─────────────────────────


class TestBoxTarget:
    def test_hit_near_target(self):
        # 低点 10，目标 13；现价 12.8 → 距目标 1.56% ≤ 3% → 进压力区
        close = [10.0] * 50 + _trend(10, 10.5, 0.25)
        r = qn_box_target.detect(_df(close))
        assert r["available"] and r["hit"]
        assert r["base_low"] == pytest.approx(10.0, abs=0.15)
        assert r["above_half_grid"] is True

    def test_no_hit_far_from_target(self):
        close = [10.0] * 50 + _trend(10, 10.2, 0.05)
        r = qn_box_target.detect(_df(close))
        assert r["available"] and not r["hit"]
        assert r["dist_target_pct"] > 3.0

    def test_short_data(self):
        r = qn_box_target.detect(_df(_trend(30, 10.0, 0.1)))
        assert r["available"] is False and r["hit"] is False


# ───────────────────── qn_ma144_launch ───────────────────────


def _ma144_df(rise_bars=25, flat_n=155, surge=True):
    """四要素正例：长期走平（MA144 平）→ 末段温和上升（上翘 + MACD 水上），
    收盘贴近 MA144，末根给倍量（过左风形式之一）。"""
    n = flat_n + rise_bars
    close = [10.0] * flat_n + _trend(rise_bars, 10.02, 0.03)
    vol = np.full(n, 1e6)
    if surge:
        vol[-1] = 2.5e6
    return _df(close, vol=vol, spread=0.001)


class TestMa144Launch:
    def test_hit(self):
        r = qn_ma144_launch.detect(_ma144_df(), code="600000")
        assert r["available"], r
        for name in (
            "ma144_turn_up",
            "pullback_zone",
            "macd_above_zero",
            "cross_forms",
        ):
            assert r["legs"][name]["hit"], f"{name}: {r['legs'][name]}"
        assert r["hit"]

    def test_no_hit_ma144_falling(self):
        # 长期下跌 → MA144 向下，四要素①不成立
        close = _trend(200, 20.0, -0.05)
        r = qn_ma144_launch.detect(_df(close), code="600000")
        assert r["available"] and not r["hit"]
        assert r["legs"]["ma144_turn_up"]["hit"] is False

    def test_no_hit_without_cross_form(self):
        r = qn_ma144_launch.detect(_ma144_df(surge=False), code="600000")
        forms = r["legs"]["cross_forms"]["forms"]
        assert not any(forms.values()), forms
        assert r["hit"] is False

    def test_short_data(self):
        r = qn_ma144_launch.detect(_df(_trend(100, 10.0, 0.05)), code="600000")
        assert r["available"] is False and r["hit"] is False


# ───────────────────── 健壮性（全因子） ──────────────────────


class TestRobustness:
    @pytest.mark.parametrize("fid", QN_IDS)
    def test_never_raise_on_garbage(self, fid):
        mod = factors.registry()[fid]["module"]
        for bad in (
            _df([np.nan] * 50),
            _df([0.0] * 50),
            _df(_trend(5, 10.0, 0.1)),
        ):
            r = mod.detect(bad, code="600000")
            assert isinstance(r, dict) and r.get("hit") is False


# ───────────────────── 研究侧 ENTRY gate ─────────────────────


class TestGates:
    def test_all_qn_gates_registered(self):
        from custos.research import backtest_factors as BF

        for fid in QN_IDS:
            assert fid in BF.ENTRY_GATES, f"{fid} 未注册 ENTRY_GATES"
            assert callable(BF.ENTRY_GATES[fid])

    @pytest.mark.parametrize("fid", QN_IDS)
    def test_gate_returns_bool(self, fid):
        from custos.research import backtest_factors as BF

        for df in (_df(_trend(200, 10.0, 0.05)), _df(_trend(200, 30.0, -0.05))):
            assert isinstance(BF.ENTRY_GATES[fid](df), bool), fid

    def test_ma25_state_gate_hit(self):
        from custos.research import backtest_factors as BF

        close = _trend(40, 10.0, 0.1)
        close[-1] = close[-2] - 0.02
        vol = np.full(40, 1e6)
        vol[-1] = 5e5
        assert BF.ENTRY_GATES["qn_ma25_state"](_df(close, vol=vol)) is True
        assert BF.ENTRY_GATES["qn_ma25_state"](_df(_trend(40, 10.0, 0.1))) is False

    def test_three_red_gate_hit(self):
        from custos.research import backtest_factors as BF

        up = [10.0 * 1.002**i for i in range(900)]
        dn = [200.0 - 0.0002 * i * i for i in range(900)]
        assert BF.ENTRY_GATES["qn_three_red"](_df(up)) is True
        assert BF.ENTRY_GATES["qn_three_red"](_df(dn)) is False

    def test_macd_bar_shift_gate_hit(self):
        from custos.research import backtest_factors as BF

        base = [60.0 - 0.01 * i * i for i in range(60)]
        pos = base + [base[-1] + t for t in (0.05, 0.10, 0.16)]
        assert BF.ENTRY_GATES["qn_macd_bar_shift"](_df(pos)) is True
        assert BF.ENTRY_GATES["qn_macd_bar_shift"](_df(base)) is False

    def test_box_target_gate_translation(self):
        """转译口径钉：站上半格×1.15 且未进目标压力区（现价 11.5~12.7 区间）。"""
        from custos.research import backtest_factors as BF

        # 低点 10，现价 11.8：above_half（≥11.5）✓、未进目标区（13/11.8−1≈10.2%>3%）✓
        close = [10.0] * 50 + _trend(10, 10.3, 0.16)
        assert BF.ENTRY_GATES["qn_box_target"](_df(close)) is True
        # 现价 12.8 已进目标压力区（≤3%）→ gate False（因子 hit=True 是另一回事）
        close = [10.0] * 50 + _trend(10, 10.5, 0.25)
        assert BF.ENTRY_GATES["qn_box_target"](_df(close)) is False

    def test_ma144_launch_gate_hit_without_code(self):
        """gate 路径无 code：倍量腿不受影响（涨停腿按主板口径，见 gate docstring）。"""
        from custos.research import backtest_factors as BF

        assert BF.ENTRY_GATES["qn_ma144_launch"](_ma144_df()) is True
        assert BF.ENTRY_GATES["qn_ma144_launch"](_ma144_df(surge=False)) is False
