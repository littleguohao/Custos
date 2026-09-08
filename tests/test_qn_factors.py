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


# ───────────────────── 第二批：4 个因子 ─────────────────────

from custos.core.factors import (  # noqa: E402
    qn_bullish_engulf,
    qn_ma_converge,
    qn_shrink_limit_up,
    qn_weekly180_setup,
)

QN_IDS_BATCH2 = [
    "qn_ma_converge",
    "qn_bullish_engulf",
    "qn_weekly180_setup",
    "qn_shrink_limit_up",
]


class TestRegistryBatch2:
    def test_all_registered(self):
        reg = factors.registry()
        missing = [f for f in QN_IDS_BATCH2 if f not in reg]
        assert not missing, f"未注册：{missing}"

    @pytest.mark.parametrize("fid", QN_IDS_BATCH2)
    def test_meta_debug_untested_none(self, fid):
        m = factors.registry()[fid]["meta"]
        assert m["status"] == "untested"
        assert m["live_use"] == "none"
        assert m["stage"] == "debug"


# ───────────────────── qn_ma_converge ────────────────────────


def _converge_df(breakout=True, n=170):
    """四线长期粘合（窄幅震荡）→ 末根放量阳线上穿。"""
    close = [10.0 + (0.02 if i % 2 else -0.02) for i in range(n - 1)]
    open_ = list(close)
    vol = np.full(n, 1e6)
    if breakout:
        close.append(10.6)
        open_.append(9.99)
        vol[-1] = 2.0e6
    else:
        close.append(10.0 + (0.02 if (n - 1) % 2 else -0.02))
        open_.append(close[-2])
    return _df(close, open_=open_, vol=vol, spread=0.002)


class TestMaConverge:
    def test_hit_first_divergence(self):
        r = qn_ma_converge.detect(_converge_df())
        assert r["available"], r
        assert r["legs"]["bandwidth"]["hit"]  # 此前持续粘合
        assert r["legs"]["first_divergence"]["hit"], r["legs"]
        assert r["hit"]

    def test_no_hit_without_breakout(self):
        r = qn_ma_converge.detect(_converge_df(breakout=False))
        assert r["available"] and not r["hit"]
        assert r["converged"] is True  # 仍在粘合中

    def test_short_data(self):
        r = qn_ma_converge.detect(_df(_trend(100, 10.0, 0.05)))
        assert r["available"] is False and r["hit"] is False


# ───────────────────── qn_bullish_engulf ─────────────────────


def _engulf_df(vol_ratio=1.2, cross=True):
    """阴跌（收在 MA5/MA10 之下）→ 前根阴线延续跌势 → 末根阳线实体包覆前阴。"""
    n = 32
    close = [10.5 - 0.04 * i for i in range(n - 2)]
    open_ = list(np.roll(close, 1))
    open_[0] = close[0]
    close.append(9.30)  # 前根阴线 9.38 → 9.30（延续跌势，收在 MA 之下）
    open_.append(9.38)
    close.append(9.55 if cross else 9.35)  # 末根：开 ≤ 前收、收 > 前开 = 包覆
    open_.append(9.30)
    vol = np.full(n, 1e6)
    vol[-1] = 1e6 * vol_ratio
    return _df(close, open_=open_, vol=vol, spread=0.001)


class TestBullishEngulf:
    def test_hit(self):
        r = qn_bullish_engulf.detect(_engulf_df())
        assert r["available"] and r["hit"], r["legs"]

    def test_no_hit_without_engulf(self):
        # 末根收 9.58 未过前阴开盘 9.60 → 包覆不成立
        r = qn_bullish_engulf.detect(_engulf_df(cross=False))
        assert r["available"] and not r["hit"]
        assert r["legs"]["engulf"]["hit"] is False

    def test_no_hit_without_vol_edge(self):
        r = qn_bullish_engulf.detect(_engulf_df(vol_ratio=1.0))
        assert r["available"] and not r["hit"]
        assert r["legs"]["vol_edge"]["hit"] is False

    def test_short_data(self):
        r = qn_bullish_engulf.detect(_df(_trend(10, 10.0, 0.1)))
        assert r["available"] is False and r["hit"] is False


# ───────────────────── qn_weekly180_setup ────────────────────


def _weekly180_df(n=1685, breakout=True, huge=True):
    """四要素正例（1685 根 ≈ 241 周）：基底 → 急拉见顶 40（峰在近 180 周内，
    MA180 仍在高位）→ 长跌至 12（大悬空，回撤 ~70%）→ 低位横盘 →
    最后一个完整周放量收上 180 周线。⚠️ 末 5 天须为完整 Mon-Fri 周
    （n ≡ 5 mod 7），否则突破周是残周、量能比失真。"""
    close = np.empty(n)
    close[:400] = 10.0  # 基底
    close[400:500] = np.linspace(10.0, 40.0, 100)  # 急拉见顶
    close[500:1400] = np.linspace(40.0, 12.0, 900)  # 长跌（大悬空）
    close[1400:1680] = np.linspace(12.0, 13.5, 280)  # 低位横盘
    vol = np.full(n, 1e6)
    if huge:
        vol[1300] = 30e6  # 脚踩巨量（悬空期内）
    if breakout:
        close[1680:] = [16.0, 20.0, 23.0, 25.0, 26.0]  # 突破周
        vol[1680:] = 3e6  # 整周放量（≥前周 ×1.5）
    else:
        close[1680:] = [13.6, 13.7, 13.8, 13.9, 14.0]  # 不突破
    return _df(close, vol=vol)


class TestWeekly180Setup:
    def test_hit(self):
        r = qn_weekly180_setup.detect(_weekly180_df())
        assert r["available"], r
        for name in ("suspension", "huge_vol", "breakout", "macd_mark"):
            assert r["legs"][name]["hit"], f"{name}: {r['legs'][name]}"
        assert r["hit"]

    def test_no_hit_without_breakout(self):
        r = qn_weekly180_setup.detect(_weekly180_df(breakout=False))
        assert r["available"] and not r["hit"]
        assert r["legs"]["breakout"]["hit"] is False

    def test_no_hit_shallow_history(self):
        # 无大悬空（始终在高位附近）→ 腿①不成立
        close = [40.0 + (0.5 if i % 2 else -0.5) for i in range(1400)]
        r = qn_weekly180_setup.detect(_df(close))
        assert r["available"] and not r["hit"]
        assert r["legs"]["suspension"]["hit"] is False

    def test_short_data(self):
        r = qn_weekly180_setup.detect(_df(_trend(500, 10.0, 0.02)))
        assert r["available"] is False and r["hit"] is False


# ───────────────────── qn_shrink_limit_up ────────────────────


def _shrink_limit_df(shrink_ratio=0.6, with_prev_surge_yin=True, limit_chg=10.0):
    """横盘基底 → 3 天前放量阴线 → 前日常态量 → 当日涨停且缩量适中。"""
    n = 152
    close = [10.0] * (n - 3)
    open_ = [10.0] * (n - 3)
    vol = np.full(n, 1e6)
    # 放量阴线（开 10.2 → 收 9.9，量 3× 均量）
    close.append(9.9)
    open_.append(10.2 if with_prev_surge_yin else 9.8)
    if with_prev_surge_yin:
        vol[n - 3] = 3e6
    # 前日：常态
    close.append(9.95)
    open_.append(9.9)
    # 当日：涨停 + 缩量
    close.append(round(close[-1] * (1 + limit_chg / 100), 2))
    open_.append(close[-2])
    vol[-1] = vol[-2] * shrink_ratio
    return _df(close, open_=open_, vol=vol, spread=0.001)


class TestShrinkLimitUp:
    def test_hit(self):
        r = qn_shrink_limit_up.detect(_shrink_limit_df(), code="600000")
        assert r["available"], r
        assert r["hit"], r["legs"]

    def test_no_hit_too_deep_shrink(self):
        # 缩量过深（≤前日 1/2）= 弱势，源规则明文不参与
        r = qn_shrink_limit_up.detect(_shrink_limit_df(shrink_ratio=0.4), code="600000")
        assert r["available"] and not r["hit"]
        assert r["legs"]["shrink"]["hit"] is False

    def test_no_hit_without_prev_surge_yin(self):
        r = qn_shrink_limit_up.detect(
            _shrink_limit_df(with_prev_surge_yin=False), code="600000"
        )
        assert r["available"] and not r["hit"]
        assert r["legs"]["prev_surge_yin"]["hit"] is False

    def test_no_hit_not_limit(self):
        r = qn_shrink_limit_up.detect(_shrink_limit_df(limit_chg=6.0), code="600000")
        assert r["available"] and not r["hit"]
        assert r["legs"]["limit_up"]["hit"] is False

    def test_short_data(self):
        r = qn_shrink_limit_up.detect(_df(_trend(60, 10.0, 0.05)), code="600000")
        assert r["available"] is False and r["hit"] is False


# ─────────────── 第二批 gate 与健壮性 ───────────────


class TestGatesBatch2:
    def test_all_batch2_gates_registered(self):
        from custos.research import backtest_factors as BF

        for fid in QN_IDS_BATCH2:
            assert fid in BF.ENTRY_GATES, f"{fid} 未注册 ENTRY_GATES"

    @pytest.mark.parametrize("fid", QN_IDS_BATCH2)
    def test_gate_returns_bool(self, fid):
        from custos.research import backtest_factors as BF

        df = _df(_trend(200, 10.0, 0.05))
        assert isinstance(BF.ENTRY_GATES[fid](df), bool), fid

    def test_ma_converge_gate_hit(self):
        from custos.research import backtest_factors as BF

        assert BF.ENTRY_GATES["qn_ma_converge"](_converge_df()) is True
        assert BF.ENTRY_GATES["qn_ma_converge"](_converge_df(breakout=False)) is False

    def test_bullish_engulf_gate_hit(self):
        from custos.research import backtest_factors as BF

        assert BF.ENTRY_GATES["qn_bullish_engulf"](_engulf_df()) is True
        assert BF.ENTRY_GATES["qn_bullish_engulf"](_engulf_df(cross=False)) is False


class TestRobustnessBatch2:
    @pytest.mark.parametrize("fid", QN_IDS_BATCH2)
    def test_never_raise_on_garbage(self, fid):
        mod = factors.registry()[fid]["module"]
        for bad in (
            _df([np.nan] * 60),
            _df([0.0] * 60),
            _df(_trend(5, 10.0, 0.1)),
        ):
            r = mod.detect(bad, code="600000")
            assert isinstance(r, dict) and r.get("hit") is False


# ───────────────────── 预计算快速路径（v0.196）─────────────────────


class TestFastPathEquivalence:
    """qn gate 的 _arr 快速路径 vs 逐切片慢路径逐 bar 抽样一致。

    通用等价性由 test_gate_precompute_equivalence ①②⑤⑦ 自动兜底（覆盖全部
    12 gate）；这里补它们覆盖不到的关键场景：**带 amount 列、周/月键齐备、
    根数足够让 three_red 的月腿与 weekly180 的 180 周线真正参与判定**
    （通用测试的 ~200 根合成数据只会让这两条的周/月腿恒 unavailable——
    两路一致地空转不算验证）。
    """

    def test_all_qn_gates_fast_matches_slow(self):
        from custos.research import backtest_factors as BF

        rng = np.random.default_rng(42)
        n = 1400
        c = 20 + np.cumsum(rng.normal(0, 0.3, n))
        o = c + rng.normal(0, 0.05, n)
        df = pd.DataFrame(
            {
                "date": pd.date_range("2019-01-01", periods=n).astype(str),
                "open": o,
                "close": c,
                "high": np.maximum(o, c) + abs(rng.normal(0, 0.2, n)),
                "low": np.minimum(o, c) - abs(rng.normal(0, 0.2, n)),
                "volume": abs(rng.normal(1e6, 2e5, n)),
                "amount": abs(rng.normal(1e7, 2e6, n)),
            }
        )
        pre = BF._precompute_gate_series(df)
        assert pre is not None
        assert "weekly_dif" in pre and "monthly_dif" in pre, "周/月 MACD 键必须就位"
        assert "weekly_ma180" in pre and "day_w" in pre, "180 周线轴必须就位"
        for fid in QN_IDS + QN_IDS_BATCH2:
            gate = BF.ENTRY_GATES[fid]
            for i in list(range(180, n, 97)) + [n - 1]:
                sl = df.iloc[: i + 1]
                assert gate(sl) == gate(sl, pre), f"{fid} 在 i={i} 两路不一致"

    def test_qn_gates_placeholder_no_raise(self):
        """白名单成员在 _PrefixLen 占位对象上必须正常出 bool（不许读列）。"""
        from custos.research import backtest_factors as BF

        df = _df(_trend(200, 10.0, 0.05))
        df["amount"] = df["close"] * df["volume"]
        pre = BF._precompute_gate_series(df)
        for fid in QN_IDS + QN_IDS_BATCH2:
            gate = BF.ENTRY_GATES[fid]
            assert gate in BF._SLICE_FREE_GATES, f"{fid} 未登记无切片白名单"
            assert isinstance(gate(BF._PrefixLen(200), pre), bool), fid
