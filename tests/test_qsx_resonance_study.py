# -*- coding: utf-8 -*-
"""qsx_resonance_study 钉测：共振检测器（合成 K 线：有共振/无共振/贴线假摔/
站回时序因果/双线去重）+ QSX 跌破清仓出场（引擎新通道）+ 三臂 gate + 统计函数。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from custos.core import indicators as ind
from custos.research import backtest_factors as bf
from custos.research import qsx_resonance_study as qrs


# ---------------------------------------------------------------------------
# 合成数据工具
# ---------------------------------------------------------------------------


def _mk_df(closes, opens=None, lows=None, highs=None) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n),
            "open": opens if opens is not None else list(closes),
            "high": highs if highs is not None else [c * 1.01 for c in closes],
            "low": lows if lows is not None else [c * 0.99 for c in closes],
            "close": list(closes),
            "volume": [1e6] * n,
            "amount": [1e9] * n,
        }
    )


def _flat(n: int, v: float = 10.0) -> np.ndarray:
    return np.full(n, v, dtype=float)


# ---------------------------------------------------------------------------
# 共振检测器
# ---------------------------------------------------------------------------


class TestResonance:
    """线恒 10；tol=1% ⇒ 贴线带 [9.9, 10.1]；reclaim_bars=3；min_events=2。"""

    KW = {"lookback": 30, "tol": 0.01, "reclaim_bars": 3, "min_events": 2}

    def _series(self, close, low, line=None):
        n = len(close)
        line = _flat(n) if line is None else np.asarray(line, dtype=float)
        s = lambda a: pd.Series(np.asarray(a, dtype=float))  # noqa: E731
        return qrs.qsx_dks_resonance(s(close), s(low), s(line), s(line), **self.KW)

    def test_no_touch_no_resonance(self):
        """无共振：股价全程远高于线，从不贴线 ⇒ 全 False。"""
        close = [11.0] * 40
        low = [10.8] * 40
        res = self._series(close, low)
        assert not res.any()

    def test_two_bounces_resonance(self):
        """有共振：两次「贴线→当日收回线上」⇒ 第二次 confirm 起为 True。"""
        n = 40
        close = [11.0] * n
        low = [10.8] * n
        # 第一次回踩：bar 5 贴线、当日收 10.5（>线，confirm=5）
        low[5] = 10.02
        close[5] = 10.5
        # 第二次回踩：bar 12 贴线、当日收 10.4（confirm=12）
        low[12] = 10.05
        close[12] = 10.4
        res = self._series(close, low)
        assert not res[:12].any()  # 第二次 confirm 之前最多 1 次事件 ⇒ False
        assert res[12]  # 第二次 confirm 当根起 True
        assert res[12 + 5]  # 窗口内持续 True

    def test_touch_fakeout_not_counted(self):
        """贴线假摔：贴线后收盘破线且窗口内站不回 ⇒ 不算事件（仅 1 次有效 ⇒ False）。"""
        n = 40
        close = [11.0] * n
        low = [10.8] * n
        # 有效事件：bar 5 贴线收回
        low[5] = 10.02
        close[5] = 10.5
        # 假摔：bar 12 贴线但收 9.5 破线，之后 9.4/9.3 一路走低，3 根内站不回
        low[12] = 10.0
        close[12] = 9.5
        close[13] = 9.4
        close[14] = 9.3
        close[15] = 9.2
        low[13] = 9.3
        low[14] = 9.2
        low[15] = 9.1
        res = self._series(close, low)
        assert not res.any()  # 只有 1 次有效事件 < min_events=2

    def test_delayed_reclaim_causal(self):
        """站回滞后：贴线当日收破线、第 2 根才站回 ⇒ confirm 之前不可见（as-of 因果）。"""
        n = 40
        close = [11.0] * n
        low = [10.8] * n
        # 事件 1：bar 5 confirm
        low[5] = 10.02
        close[5] = 10.5
        # 事件 2：bar 12 贴线收破（9.8），bar 13 仍线下（9.85），bar 14 站回（10.3）
        low[12] = 10.0
        close[12] = 9.8
        low[13] = 9.7
        close[13] = 9.85
        close[14] = 10.3
        low[14] = 10.1
        res = self._series(close, low)
        assert not res[13]  # 事件 2 尚未确认 ⇒ 仍只有 1 次
        assert res[14]  # confirm=14 起第二次可见 ⇒ True

    def test_same_dip_two_lines_dedup(self):
        """双线去重：QSX 与 DKS 同值时同一次下跌只在一条线上各计一次——
        dedup 后 2 次（不是 4 次）⇒ min_events=3 不成立、=2 成立。"""
        n = 40
        close = [11.0] * n
        low = [10.8] * n
        low[5] = 10.02
        close[5] = 10.5
        low[12] = 10.05
        close[12] = 10.4
        s = lambda a: pd.Series(np.asarray(a, dtype=float))  # noqa: E731
        line = s(_flat(n))
        res3 = qrs.qsx_dks_resonance(
            s(close),
            s(low),
            line,
            line,
            lookback=30,
            tol=0.01,
            reclaim_bars=3,
            min_events=3,
        )
        assert not res3.any()  # 若不去重会有 4 次 ⇒ ≥3 成立；去重后只有 2 次
        res2 = qrs.qsx_dks_resonance(
            s(close),
            s(low),
            line,
            line,
            lookback=30,
            tol=0.01,
            reclaim_bars=3,
            min_events=2,
        )
        assert res2[12]

    def test_below_line_hug_not_counted(self):
        """线下贴线不算回踩：段前收盘已在线下（不是从上往下）⇒ 不计事件。"""
        n = 30
        # 前 5 根在线上，第 5 根破线，之后贴着线下方走（low 贴线但 close 一直在线下）
        close = [11.0] * 5 + [9.6] * (n - 5)
        low = [10.8] * 5 + [9.95] * (n - 5)  # 9.95 在 ±1% 带内
        res = self._series(close, low)
        assert not res.any()

    def test_window_expiry(self):
        """事件滑出 lookback 窗后不再计入。"""
        n = 80
        close = [11.0] * n
        low = [10.8] * n
        low[5] = 10.02
        close[5] = 10.5
        low[12] = 10.05
        close[12] = 10.4
        res = self._series(close, low)
        assert res[12]
        # 事件 1 可见窗 [5, 5+30-1=34]，事件 2 [12, 41]；两事件同见 ⇒ [12,34]。
        # 35 起事件 1 出窗只剩事件 2（<min_events=2）⇒ False。
        assert res[34]
        assert not res[35]


# ---------------------------------------------------------------------------
# 共振检测器 v2（owner 2026-08-26 六要素定稿）
# ---------------------------------------------------------------------------


class TestResonanceV2:
    """线恒 10；默认 lookback=60/bounce_bars=5/bounce_pct=3%/vol_ma=5/min_events=2。
    基准「干净事件」模具：low 9.95 真碰线、当日收 10.5 收回、high 10.6
    （9.95×1.03=10.2485 ⇒ 达标）、量 800 < 前5均 1000。"""

    KW = {
        "lookback": 60,
        "bounce_bars": 5,
        "bounce_pct": 0.03,
        "vol_ma": 5,
        "min_events": 2,
    }
    N = 80

    def _base(self):
        close = [11.0] * self.N
        low = [10.8] * self.N
        high = [11.1] * self.N
        vol = [1000.0] * self.N
        return close, low, high, vol

    def _dip(self, close, low, high, vol, t, **kw):
        """在 t 处放一个干净跌线反弹（可用 kw 逐要素破坏）。"""
        low[t] = kw.get("low_t", 9.95)
        close[t] = kw.get("close_t", 10.5)
        high[t] = kw.get("high_t", 10.6)
        vol[t] = kw.get("vol_t", 800.0)
        if "close_t1" in kw:
            close[t + 1] = kw["close_t1"]
        if "highs" in kw:  # 后续根的 high 序列（从 t 起）
            for k, h in enumerate(kw["highs"]):
                high[t + k] = h

    def _run(self, close, low, high, vol, line=10.0, **kw):
        s = lambda a: pd.Series(np.asarray(a, dtype=float))  # noqa: E731
        ln = s(np.full(len(close), line))
        return qrs.qsx_dks_resonance_v2(
            s(close), s(low), s(high), s(vol), ln, ln, **{**self.KW, **kw}
        )

    def test_clean_two_events_hit(self):
        """两次干净反弹 ⇒ confirm 后起 hit=True。"""
        close, low, high, vol = self._base()
        self._dip(close, low, high, vol, 8)
        self._dip(close, low, high, vol, 16)
        hit, excluded = self._run(close, low, high, vol)
        assert not hit[:16].any()
        assert hit[16]
        assert not excluded.any()

    def test_near_miss_without_touch_not_counted(self):
        """① 碰线须真碰：low=10.08（贴近但没碰）不算事件。"""
        close, low, high, vol = self._base()
        self._dip(close, low, high, vol, 8, low_t=10.08)
        self._dip(close, low, high, vol, 16, low_t=10.08)
        hit, _ = self._run(close, low, high, vol)
        assert not hit.any()

    def test_two_closes_below_invalid(self):
        """② 线下收盘 ≤1 根：连破 2 根 ⇒ 事件无效。"""
        close, low, high, vol = self._base()
        # 有效事件 ×1
        self._dip(close, low, high, vol, 8)
        # 无效事件：t=16 收破（9.7），t+1 仍破（9.8）⇒ 连破 2 根
        low[16] = 9.95
        close[16] = 9.7
        high[16] = 10.6
        vol[16] = 800.0
        close[17] = 9.8
        low[17] = 9.6
        hit, _ = self._run(close, low, high, vol)
        assert not hit.any()  # 只剩 1 次有效 < min_events

    def test_one_close_below_then_reclaim_valid(self):
        """②③ 线下收盘恰好 1 根后收回 ⇒ 有效；confirm=收回根。"""
        close, low, high, vol = self._base()
        self._dip(close, low, high, vol, 8)
        low[16] = 9.95
        close[16] = 9.7  # 收破（第 1 根线下）
        high[16] = 10.6  # 反弹达标（min_low=9.95）
        vol[16] = 800.0
        close[17] = 10.3  # 次日收回
        hit, _ = self._run(close, low, high, vol)
        assert not hit[16]  # 尚未收回 ⇒ 第二次事件不可见
        assert hit[17]  # confirm=17（reclaim=17、bounce=16）

    def test_close_exactly_on_line_not_reclaim(self):
        """③ 收回是严格 >：收盘压线（==）不算收回，连压 2 根 ⇒ 无效。"""
        close, low, high, vol = self._base()
        self._dip(close, low, high, vol, 8)
        low[16] = 9.95
        close[16] = 10.0  # == 线，不算收回（线下收盘第 1 根）
        high[16] = 10.6
        vol[16] = 800.0
        close[17] = 10.0  # 仍 == 线 ⇒ 线下收盘第 2 根 ⇒ 无效
        low[17] = 9.9
        hit, _ = self._run(close, low, high, vol)
        assert not hit.any()

    def test_bounce_threshold(self):
        """④ 反弹 ≥3%：只弹 2% ⇒ 无效；弹过线 ⇒ 有效。"""
        close, low, high, vol = self._base()
        self._dip(close, low, high, vol, 8)
        # 触线最低 9.9（收破），随后 high 最高 10.15 < 9.9×1.03=10.197 ⇒ 无效
        low[16] = 9.9
        close[16] = 9.75
        vol[16] = 800.0
        close[17] = 10.1  # 收回
        for u in range(16, 22):  # 覆盖反弹窗 [16, 21]，防基准 high=11.1 污染
            high[u] = 10.15
        hit, _ = self._run(close, low, high, vol)
        assert not hit.any()
        # 同款但 high 到 10.2 ≥ 10.197 ⇒ 有效
        close, low, high, vol = self._base()
        self._dip(close, low, high, vol, 8)
        low[16] = 9.9
        close[16] = 9.75
        vol[16] = 800.0
        close[17] = 10.1
        high[16] = 10.05
        high[17] = 10.1
        high[18] = 10.2
        hit, _ = self._run(close, low, high, vol)
        assert not hit[17]  # 收回但反弹未达标 ⇒ 尚不可见
        assert hit[18]  # confirm=max(reclaim=17, bounce=18)=18

    def test_volume_not_dry_invalid(self):
        """⑤ 缩量：触线日量 ≥ 前 5 日均量 ⇒ 无效。"""
        close, low, high, vol = self._base()
        self._dip(close, low, high, vol, 8)
        self._dip(close, low, high, vol, 16, vol_t=1200.0)  # 放量碰线
        hit, _ = self._run(close, low, high, vol)
        assert not hit.any()

    def test_exclusion_priority(self):
        """⑥ 排除态优先：≥2 次干净反弹在手，但当前跌破未收复 ⇒ pass=False；
        收复后排除解除 ⇒ pass 恢复。"""
        close, low, high, vol = self._base()
        self._dip(close, low, high, vol, 8)
        self._dip(close, low, high, vol, 16)
        # bar 20 起跌破不收复
        low[20] = 9.9
        close[20] = 9.7
        close[21] = 9.6
        low[21] = 9.5
        close[22] = 10.3  # 收复
        hit, excluded = self._run(close, low, high, vol)
        assert hit[20]  # 成立条件仍满足（两事件在 60 根窗内）
        assert excluded[20] and excluded[21]  # 跌破未收复
        assert not (hit & ~excluded)[20]  # 排除优先 ⇒ 不放行
        assert not excluded[22]  # 收复解除
        assert (hit & ~excluded)[22]

    def test_below_line_run_from_above_required(self):
        """① 线下运行不算「跌到线」：段前收盘已在线下的碰线不计事件。"""
        close, low, high, vol = self._base()
        self._dip(close, low, high, vol, 8)
        # bar 16 破线后一路线下：low 天天碰线但都不是「从上往下」
        close[16] = 9.7
        low[16] = 9.9
        for t in range(17, 25):
            close[t] = 9.6
            low[t] = 9.5
            high[t] = 10.6
            vol[t] = 800.0
        hit, _ = self._run(close, low, high, vol)
        assert not hit[17:25].any()  # 只有 1 次有效事件

    def test_dedup_same_dip_two_lines(self):
        """双线去重：QSX/DKS 同值时同一次碰线只算一次（min_events=3 不成立）。"""
        close, low, high, vol = self._base()
        self._dip(close, low, high, vol, 8)
        self._dip(close, low, high, vol, 16)
        hit3, _ = self._run(close, low, high, vol, min_events=3)
        assert not hit3.any()  # 若不去重会有 4 次
        hit2, _ = self._run(close, low, high, vol, min_events=2)
        assert hit2[16]


# ---------------------------------------------------------------------------
# QSX 跌破清仓（simulate_b1_trade 新通道）
# ---------------------------------------------------------------------------


class TestQsxExit:
    def test_qsx_exit_next_open(self):
        """收盘 < QSX ⇒ 次日**开盘**清仓（reason=qsx_exit，exit_idx=j+1）。"""
        closes = [10.0] * 6 + [11.0, 10.5, 9.0, 9.1, 9.2]
        opens = list(closes)
        opens[9] = 8.5  # 次日低开：证明按 open[j+1] 而非 close[j] 成交
        df = _mk_df(closes, opens=opens)
        bbi = pd.Series(_flat(len(closes), 5.0))  # BBI 远低于价，不干扰
        qsx = pd.Series(_flat(len(closes), 9.5))
        tr = bf.simulate_b1_trade(
            df,
            5,
            bbi,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=50,
            qsx=qsx,
            qsx_exit_consec=1,
        )
        assert tr["reason"] == "qsx_exit"
        assert tr["exit_idx"] == 9  # 触发根 j=8（close 9.0<9.5）的次日
        assert tr["ret"] == pytest.approx(8.5 / 10.0 - 1, abs=1e-9)

    def test_qsx_exit_last_bar_settles_close(self):
        """触发根已是最后一根 ⇒ 按最后收盘结算（无次日）。"""
        closes = [10.0] * 6 + [11.0, 10.5, 9.0]
        df = _mk_df(closes)
        bbi = pd.Series(_flat(len(closes), 5.0))
        qsx = pd.Series(_flat(len(closes), 9.5))
        tr = bf.simulate_b1_trade(
            df,
            5,
            bbi,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=50,
            qsx=qsx,
            qsx_exit_consec=1,
        )
        assert tr["reason"] == "qsx_exit"
        assert tr["exit_idx"] == len(closes) - 1
        assert tr["ret"] == pytest.approx(9.0 / 10.0 - 1, abs=1e-9)

    def test_qsx_exit_consec2(self):
        """consec=2：单日破线不触发，连破 2 根才在第二根的次日开盘清。"""
        closes = [10.0] * 6 + [11.0, 9.0, 10.0, 9.2, 9.1, 9.3]
        df = _mk_df(closes)
        bbi = pd.Series(_flat(len(closes), 5.0))
        qsx = pd.Series(_flat(len(closes), 9.5))
        tr = bf.simulate_b1_trade(
            df,
            5,
            bbi,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=50,
            qsx=qsx,
            qsx_exit_consec=2,
        )
        # j=7(9.0<9.5) 单日破、j=8(10.0) 收复 ⇒ 不触发；j=9(9.2)、j=10(9.1) 连破 ⇒ 次日 11 开盘清
        assert tr["reason"] == "qsx_exit"
        assert tr["exit_idx"] == 11

    def test_qsx_nan_guard(self):
        """QSX NaN 根不触发（NaN 守卫），恢复有效后正常判定。"""
        closes = [10.0] * 6 + [11.0, 9.0, 9.0, 9.2]
        df = _mk_df(closes)
        bbi = pd.Series(_flat(len(closes), 5.0))
        qsx_v = _flat(len(closes), 9.5)
        qsx_v[7] = np.nan  # 第 7 根线值缺失 ⇒ 该根不判
        tr = bf.simulate_b1_trade(
            df,
            5,
            bbi,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=50,
            qsx=pd.Series(qsx_v),
            qsx_exit_consec=1,
        )
        assert tr["reason"] == "qsx_exit"
        assert tr["exit_idx"] == 9  # 第 8 根才触发（7 被 NaN 跳过）

    def test_bbi_exit_disabled_by_zero_consec(self):
        """bbi_exit_consec=0 ⇒ BBI 连破清仓整体关闭（本轮 owner 口径）。"""
        closes = [10.0, 11.0, 11.0, 9.8, 9.7, 9.6, 9.5]
        df = _mk_df(closes)
        bbi = pd.Series(_flat(len(closes), 10.0))
        on = bf.simulate_b1_trade(
            df, 0, bbi, bbi_exit_consec=2, stop_mode="pct", stop_pct=50
        )
        assert on["reason"] == "bbi_exit"  # 默认通道仍在（回归钉）
        off = bf.simulate_b1_trade(
            df, 0, bbi, bbi_exit_consec=0, stop_mode="pct", stop_pct=50
        )
        assert off["reason"] == "open_end"  # 0 ⇒ 关闭，不再 bbi_exit

    def test_stop_priority_over_qsx(self):
        """同根既破止损又破 QSX ⇒ 止损先判（优先级 ①>②c）。"""
        closes = [10.0, 8.0, 8.1, 8.2]
        df = _mk_df(closes)
        bbi = pd.Series(_flat(len(closes), 5.0))
        qsx = pd.Series(_flat(len(closes), 9.5))
        tr = bf.simulate_b1_trade(
            df,
            0,
            bbi,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=12,
            qsx=qsx,
            qsx_exit_consec=1,
        )
        assert tr["reason"] != "qsx_exit"
        assert "stop" in tr["reason"]

    def test_evaluate_trades_pass_through(self):
        """evaluate_trades 透传：qsx_exit_consec=1 + bbi_exit_consec=0 ⇒ 出 qsx_exit 记录。"""
        n = 40
        closes = [10.0 + 0.1 * i for i in range(30)] + [12.0, 11.0, 10.0] + [9.5] * 7
        closes = closes[:n]
        df = _mk_df(closes)
        qsx = ind.qsx_series(df["close"])
        entry_i = 25
        gate = lambda s, p=None: len(s) - 1 == entry_i  # noqa: E731
        trades = bf.evaluate_trades(
            {"600000": df},
            scorer=bf.SCORERS["baseline"],
            entry_gate=gate,
            tradability=False,
            min_bars=5,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=50,
            qsx_exit_consec=1,
        )
        assert len(trades) == 1
        rec = trades[0]
        assert rec["reason"] == "qsx_exit"
        # 与手工口径对拍：entry 后首个 close<qsx 根 j ⇒ exit 于 j+1（其日期=exit_date）
        j = next(
            k
            for k in range(entry_i + 1, n)
            if float(df["close"].iloc[k]) < float(qsx.iloc[k])
        )
        assert rec["exit_date"] == str(df["date"].iloc[j + 1])[:10]


# ---------------------------------------------------------------------------
# 三臂 gate
# ---------------------------------------------------------------------------


class TestGates:
    """j_low 用预计算通道钉住（kdj_j 恒 5<13）；切片是前缀 ⇒ i=末根下标。"""

    N = 20
    PRE = {"kdj_j": np.full(N, 5.0)}

    def _zx(self, gt, res):
        return {
            "gt": np.asarray(gt, dtype=bool),
            "res": np.asarray(res, dtype=bool),
            "hit": np.asarray(res, dtype=bool),
            "excluded": np.zeros(self.N, dtype=bool),
        }

    def _slice(self, i: int) -> pd.DataFrame:
        return _mk_df([10.0] * (i + 1))  # 长度 i+1 ⇒ 末根下标 i

    def test_arm_a_only_j_low(self):
        gate = qrs._make_gate(
            "A", self._zx(np.zeros(self.N, bool), np.zeros(self.N, bool))
        )
        assert gate(self._slice(15), self.PRE)  # J<13 即放行
        bad_pre = {"kdj_j": np.full(self.N, 50.0)}
        assert not gate(self._slice(15), bad_pre)  # J 不低 ⇒ 拒

    def test_arm_b_requires_qsx_gt_dks(self):
        gt = np.zeros(self.N, bool)
        gt[15] = True
        gate = qrs._make_gate("B", self._zx(gt, np.zeros(self.N, bool)))
        assert gate(self._slice(15), self.PRE)
        assert not gate(self._slice(14), self.PRE)  # gt[14]=False ⇒ 拒

    def test_arm_c_requires_resonance(self):
        gt = np.ones(self.N, bool)
        res = np.zeros(self.N, bool)
        res[15] = True
        gate = qrs._make_gate("C", self._zx(gt, res))
        assert gate(self._slice(15), self.PRE)
        assert not gate(self._slice(14), self.PRE)  # 共振不满足 ⇒ 拒

    def test_arm_cp_resonance_without_structure(self):
        """C' 臂：共振即可，不要求 qsx_gt_dks（gt 全 False 也放行）。"""
        res = np.zeros(self.N, bool)
        res[15] = True
        gate = qrs._make_gate("Cp", self._zx(np.zeros(self.N, bool), res))
        assert gate(self._slice(15), self.PRE)
        assert not gate(self._slice(14), self.PRE)

    def test_no_exclusion_switch(self):
        """no_exclusion=True ⇒ res == hit（不减排除态）；False ⇒ res == hit & ~excluded。"""
        n = 60
        df = _mk_df([10.0 + 0.05 * i for i in range(n)])
        on = qrs._zhixing_arrays(df, 60, 0.01, 5, 2, "v2", 5, 0.03, 5, False)
        off = qrs._zhixing_arrays(df, 60, 0.01, 5, 2, "v2", 5, 0.03, 5, True)
        assert (on["res"] == (on["hit"] & ~on["excluded"])).all()
        assert (off["res"] == off["hit"]).all()
        assert (on["hit"] == off["hit"]).all()  # 开关只改 res，不改检测


# ---------------------------------------------------------------------------
# 统计函数
# ---------------------------------------------------------------------------


def _trade(
    ret, code="600000", entry="2024-01-01", reason="qsx_exit", r=1.0, score=40.0
):
    return {
        "code": code,
        "entry_date": entry,
        "exit_date": "2024-02-01",
        "ret": ret,
        "r_multiple": r,
        "reason": reason,
        "tech_score": score,
    }


class TestStats:
    def test_wilson_ci_basics(self):
        assert qrs.wilson_ci(0, 0) == (None, None)
        lo, hi = qrs.wilson_ci(50, 100)
        assert lo == pytest.approx(0.4038, abs=1e-3)
        assert hi == pytest.approx(0.5962, abs=1e-3)
        lo2, hi2 = qrs.wilson_ci(1, 10)  # 小样本区间明显宽
        assert hi2 - lo2 > hi - lo

    def test_arm_stats(self):
        trades = [_trade(0.1, r=2.0), _trade(-0.05, r=-1.0), _trade(0.02, r=0.5)]
        st = qrs.arm_stats(trades)
        assert st["n"] == 3
        assert st["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
        assert st["expectancy_R"] == pytest.approx(0.5, abs=1e-3)
        assert st["win_rate_wilson95"][0] < st["win_rate"] < st["win_rate_wilson95"][1]
        assert st["n_codes"] == 1

    def test_half_window_ret(self):
        trades = [_trade(0.1, entry=f"2024-01-0{i}") for i in range(1, 5)] + [
            _trade(-0.1, entry=f"2024-02-0{i}") for i in range(1, 5)
        ]
        hw = qrs.half_window_ret(trades)
        assert hw["consistent"] is False  # 前半正后半负 ⇒ 翻转
        assert qrs.half_window_ret(trades[:4])["skipped"]  # 样本不足如实标

    def test_compare_arms_marginal(self):
        def rep(arm, win_mod, code):
            # 100 笔、日期递增、输赢交错（每 5 根前 win_mod 根赢）⇒ 两半窗同构
            trades = []
            for i in range(100):
                win = (i % 5) < win_mod
                trades.append(
                    _trade(
                        0.1 if win else -0.1,
                        code=code,
                        entry=f"2024-{i // 28 + 1:02d}-{i % 28 + 1:02d}",
                    )
                )
            return qrs.build_arm_report(trades, arm)

        reps = {
            "A": rep("A", 2, "a"),  # 胜率 0.4
            "B": rep("B", 2, "b"),  # 0.4
            "C": rep("C", 3, "c"),  # 0.6
        }
        cmp = qrs.compare_arms(reps)
        m1 = cmp["marginal_filter1_B_minus_A"]
        assert m1["win_rate"] == pytest.approx(0.0, abs=1e-4)
        m2 = cmp["marginal_filter2_C_minus_B"]
        assert m2["win_rate"] == pytest.approx(0.2, abs=1e-4)
        hit = cmp["resonance_hit_C_over_B"]
        assert hit["signal_rate"] == pytest.approx(1.0, abs=1e-4)
        assert hit["code_rate"] == pytest.approx(1.0, abs=1e-4)
        # 预注册判读：胜率 +20pp >3pp、盈亏比不降（同 1.0）、半窗一致 ⇒ 过线
        p = cmp["prereg_C_over_B"]
        assert p["win_rate_+3pp"] and p["payoff_not_worse"] and p["pass"]

    def test_prereg_fail_when_half_window_flips(self):
        """预注册：半窗翻转 ⇒ 不过线（即使胜率/盈亏比达标）。"""
        # 前半全亏后半全赢 ⇒ consistent=False（亏损笔数须 > 半数，否则首根二月赢单落进前半）
        trades_a = [_trade(0.05, entry=f"2024-01-{i + 1:02d}") for i in range(2)] + [
            _trade(-0.05, entry=f"2024-01-{i + 1:02d}") for i in range(2, 10)
        ]  # 胜率 0.2、盈亏比 1.0
        trades_b = [_trade(-0.05, entry=f"2024-01-{i + 1:02d}") for i in range(8)] + [
            _trade(0.5, entry=f"2024-02-{i + 1:02d}") for i in range(4)
        ]  # 胜率 1/3、盈亏比 10、半窗翻转
        reps = {
            "A": qrs.build_arm_report(trades_a, "A"),
            "Cp": qrs.build_arm_report(trades_b, "Cp"),
        }
        cmp = qrs.compare_arms(reps)
        p = cmp["prereg_Cp_over_A"]
        assert p["win_rate_+3pp"] and p["payoff_not_worse"]
        assert not p["half_window_consistent"]
        assert not p["pass"]

    def test_build_arm_report_top_frac(self):
        trades = [
            _trade(0.5 - i * 0.1, entry=f"2024-01-{i + 1:02d}", score=30 + i)
            for i in range(10)
        ]
        rep = qrs.build_arm_report(trades, "C", top_frac=0.2)
        assert rep["n_trades"] == 10
        assert rep["top20_score_dist"]["n"] == 2  # ceil(10×0.2)
        assert rep["rest_score_dist"]["n"] == 8
        assert "qsx_exit" in rep["exit_reasons"]
