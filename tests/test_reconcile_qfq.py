# -*- coding: utf-8 -*-
"""前复权对账逻辑测试（合成数据，不碰真实数据源）。

对账思路的关键：两条前复权序列**基准日不同**，绝对价一定不等，
但若用了同一套事件与同一个比例公式，`tdx_t / qlib_t` 应当**恒定**。
所以判据是「比值离散度」+「日收益逐日差」，而不是比绝对价。

这份测试锁住的是：**能不能把一个被错误处理的除权事件抓出来、并定位到那一天**。
"""
from __future__ import annotations

import pandas as pd
import pytest

from local_tdx import reconcile_qfq as R


def _series(dates, closes):
    return pd.DataFrame({"date": dates, "close": closes})


DATES = [f"2021-{m:02d}-{d:02d}" for m in (9, 10, 11, 12) for d in (1, 8, 15, 22)] + \
        [f"2022-{m:02d}-{d:02d}" for m in range(1, 13) for d in (1, 8, 15, 22)]


def _install(monkeypatch, tdx, qlib):
    monkeypatch.setattr(R, "_load_tdx", lambda c: (tdx, "adjust_events=3"))
    monkeypatch.setattr(R, "_load_qlib", lambda c: (qlib, ""))


class TestRatioConstancy:
    def test_same_series_different_base_is_ok(self, monkeypatch):
        """两边只差一个全局常数（基准日不同）⇒ 必须判一致，不能误报。"""
        base = [10.0 + i * 0.1 for i in range(len(DATES))]
        _install(monkeypatch,
                 _series(DATES, base),
                 _series(DATES, [x * 3.7 for x in base]))    # 整体缩放 3.7 倍
        r = R.reconcile("600000")
        assert r["status"] == "ok", r
        assert r["ratio_spread"] == pytest.approx(0.0, abs=1e-9)

    def test_mishandled_event_is_caught_and_located(self, monkeypatch):
        """某天两边对事件处理不同 ⇒ 比值在那天跳变，必须抓到并报出日期。"""
        base = [10.0 + i * 0.1 for i in range(len(DATES))]
        # tdx 在第 20 根之前多乘了一个 0.5 的因子（相当于漏/多算一次送股）
        tdx = [x * 0.5 if i < 20 else x for i, x in enumerate(base)]
        _install(monkeypatch, _series(DATES, tdx), _series(DATES, base))
        r = R.reconcile("600000")
        assert r["status"] == "mismatch", r
        assert r["ratio_spread"] > R.RATIO_TOL
        assert r["n_mismatch"] >= 1
        assert r["mismatch_days"][0] == DATES[20], \
            f"应定位到跳变那一天，实际 {r['mismatch_days'][:3]}"

    def test_small_noise_within_tolerance_passes(self, monkeypatch):
        """浮点/舍入级别的差异不该报警——否则告警会变噪音、没人看。"""
        base = [10.0 + i * 0.1 for i in range(len(DATES))]
        jitter = [x * (1 + (0.0002 if i % 3 else -0.0002)) for i, x in enumerate(base)]
        _install(monkeypatch, _series(DATES, jitter), _series(DATES, base))
        r = R.reconcile("600000")
        assert r["status"] == "ok", r


class TestGuards:
    def test_short_overlap_is_skipped_not_judged(self, monkeypatch):
        """重叠样本太少时**不给结论** —— 少量重叠的"一致"没有说服力。"""
        d = DATES[:10]
        _install(monkeypatch, _series(d, [10.0] * 10), _series(d, [20.0] * 10))
        r = R.reconcile("600000")
        assert r["status"] == "skip" and "样本太少" in r["note"]

    def test_unadjusted_tdx_is_skipped(self, monkeypatch):
        """tdx 侧未复权时不能拿去对账（会得出一个必然的"分歧"）。"""
        monkeypatch.setattr(R, "_load_tdx", lambda c: (None, "tdx 未复权（adjust='none'）"))
        monkeypatch.setattr(R, "_load_qlib", lambda c: (_series(DATES, [1.0] * len(DATES)), ""))
        r = R.reconcile("600000")
        assert r["status"] == "skip" and "未复权" in r["note"]

    def test_never_raises(self, monkeypatch):
        """一只票的问题不该中断整轮对账。"""
        def _boom(c):
            raise RuntimeError("模拟数据源炸了")
        monkeypatch.setattr(R, "_load_tdx", _boom)
        r = R.reconcile("600000")
        assert r["status"] == "error" and "RuntimeError" in r["note"]

    def test_zero_prices_dropped(self, monkeypatch):
        """0 价（停牌/缺数）参与比值会算出 inf —— 必须先剔除。"""
        base = [10.0 + i * 0.1 for i in range(len(DATES))]
        tdx = list(base)
        tdx[5] = 0.0
        _install(monkeypatch, _series(DATES, tdx), _series(DATES, base))
        r = R.reconcile("600000")
        assert r["status"] == "ok", r
        assert r["bars"] == len(DATES) - 1


class TestAutoPick:
    def test_picks_high_impact_codes_first(self, tmp_path, monkeypatch):
        """自动挑票要挑**除权影响最大**的——从未除权的票两边必然一致、毫无判别力。"""
        import json

        def _w(code, events):
            (tmp_path / f"{code}.json").write_text(
                json.dumps({"code": code, "events": events, "market": 1}),
                encoding="utf-8")

        IN = "2023-06-15"                    # 窗口内
        OUT = "2019-06-15"                   # 窗口外（qlib 有 2020-09~2021-07 缺口，
                                             # 且我们的对账窗口从 2021-08 起）
        _w("600001", [])                                                   # 从未除权
        _w("600002", [{"date": IN, "fenhong": 1.0, "songzhuangu": 0.0}])   # 窗口内只分红
        _w("600003", [{"date": IN, "fenhong": 2.4, "songzhuangu": 30.75}])  # 窗口内大送股
        _w("600004", [{"date": OUT, "fenhong": 9.9, "songzhuangu": 99.0}])  # 事件在窗口外
        got = R.pick_auto(4, cache_dir=tmp_path)
        assert got[0] == "600003", got
        assert "600001" not in got, "从未除权的票不该入选"
        assert "600004" not in got, (
            "事件全在窗口外的票不该入选——它在窗口里因子恒为 1，"
            "判'一致'对复权公式零信息量（实测 20 只里有 7 只是这种）")

    def test_empty_cache_warns(self, tmp_path, capsys):
        assert R.pick_auto(5, cache_dir=tmp_path) == []
        assert "缓存为空" in capsys.readouterr().err
