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

from custos.research import reconcile_qfq as R


def _series(dates, closes):
    return pd.DataFrame({"date": dates, "close": closes})


DATES = [f"2021-{m:02d}-{d:02d}" for m in (9, 10, 11, 12) for d in (1, 8, 15, 22)] + [
    f"2022-{m:02d}-{d:02d}" for m in range(1, 13) for d in (1, 8, 15, 22)
]


def _install(monkeypatch, tdx, qlib):
    monkeypatch.setattr(R, "_load_tdx", lambda c: (tdx, "adjust_events=3"))
    monkeypatch.setattr(R, "_load_qlib", lambda c: (qlib, ""))


class TestRatioConstancy:
    def test_same_series_different_base_is_ok(self, monkeypatch):
        """两边只差一个全局常数（基准日不同）⇒ 必须判一致，不能误报。"""
        base = [10.0 + i * 0.1 for i in range(len(DATES))]
        _install(
            monkeypatch, _series(DATES, base), _series(DATES, [x * 3.7 for x in base])
        )  # 整体缩放 3.7 倍
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
        assert r["mismatch_days"][0] == DATES[20], (
            f"应定位到跳变那一天，实际 {r['mismatch_days'][:3]}"
        )

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
        monkeypatch.setattr(
            R, "_load_tdx", lambda c: (None, "tdx 未复权（adjust='none'）")
        )
        monkeypatch.setattr(
            R, "_load_qlib", lambda c: (_series(DATES, [1.0] * len(DATES)), "")
        )
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
                encoding="utf-8",
            )

        IN = "2023-06-15"  # 窗口内
        OUT = "2019-06-15"  # 窗口外（qlib 有 2020-09~2021-07 缺口，
        # 且我们的对账窗口从 2021-08 起）
        _w("600001", [])  # 从未除权
        _w("600002", [{"date": IN, "fenhong": 1.0, "songzhuangu": 0.0}])  # 窗口内只分红
        _w(
            "600003", [{"date": IN, "fenhong": 2.4, "songzhuangu": 30.75}]
        )  # 窗口内大送股
        _w(
            "600004", [{"date": OUT, "fenhong": 9.9, "songzhuangu": 99.0}]
        )  # 事件在窗口外
        got = R.pick_auto(4, cache_dir=tmp_path)
        assert got[0] == "600003", got
        assert "600001" not in got, "从未除权的票不该入选"
        assert "600004" not in got, (
            "事件全在窗口外的票不该入选——它在窗口里因子恒为 1，"
            "判'一致'对复权公式零信息量（实测 20 只里有 7 只是这种）"
        )

    def test_empty_cache_warns(self, tmp_path, capsys):
        assert R.pick_auto(5, cache_dir=tmp_path) == []
        assert "缓存为空" in capsys.readouterr().err


class TestWhoIsWrong:
    """定方向的判据：**非事件日的复权收益必须等于未复权收益**（只差同一个当日因子）
    ⇒ 谁偏离 `ret_raw` 谁错。

    没有这一条时只能说「两边不一致」，说不出方向 —— 而 owner 首轮对账的 13 只全部
    分歧、事件日跳变却都 <0.4%，正是必须定方向的场景。
    """

    @pytest.fixture(autouse=True)
    def _no_xdxr_network(self, monkeypatch):
        # detail() 内部 `import adjust_factors` 取事件表：xdxr 缓存缺失时会走网络
        # 取数并落盘到真实 data/market/xdxr/（干净环境下被 repo hygiene 测试抓到）。
        # 本类判据用的是合成数据，不需要真事件表。
        from custos.datasource.local_tdx import adjust_factors

        monkeypatch.setattr(adjust_factors, "get_xdxr", lambda code, **kw: [])

    def test_limit_pct_by_prefix(self):
        assert R._limit_pct("600519") == 10.0
        assert R._limit_pct("300750") == 20.0
        assert R._limit_pct("688001") == 20.0
        assert R._limit_pct("920808") == 30.0

    def _frames(self, qlib_bad_day: int | None):
        """构造：非事件日、tdx 复权收益 == 未复权收益；可选让 qlib 某天偏离。"""
        n = len(DATES)
        raw = [10.0 * (1.01**i) for i in range(n)]
        f = 0.9  # 全窗口无事件 ⇒ 因子恒定
        tdx = _series(DATES, [x * f for x in raw])
        tdx["raw_close"] = raw
        qraw = list(raw)
        if qlib_bad_day is not None:
            qraw[qlib_bad_day] *= 1.05  # qlib 在这天多涨了 5%
        return tdx, _series(DATES, qraw)

    def test_points_at_qlib_when_qlib_deviates(self, monkeypatch, capsys):
        tdx, qlib = self._frames(qlib_bad_day=30)
        monkeypatch.setattr(R, "_load_tdx", lambda c: (tdx, "adjust_events=0"))
        monkeypatch.setattr(R, "_load_qlib", lambda c: (qlib, ""))
        monkeypatch.setattr(R, "WIN_START", DATES[0])
        monkeypatch.setattr(R, "WIN_END", DATES[-1])
        R.detail("600519", top=5)
        out = capsys.readouterr().out
        assert "谁错" in out
        assert "qlib 侧有问题" in out, out[-800:]

    def test_points_at_us_when_we_deviate(self, monkeypatch, capsys):
        """反向：把偏离放在 tdx 侧，必须指向我们自己，不能只会怪别人。"""
        n = len(DATES)
        raw = [10.0 * (1.01**i) for i in range(n)]
        adj = [x * 0.9 for x in raw]
        for i in (30, 40, 50, 60):
            adj[i] *= 1.05  # tdx 复权价在这些天异常
        tdx = _series(DATES, adj)
        tdx["raw_close"] = raw
        monkeypatch.setattr(R, "_load_tdx", lambda c: (tdx, "adjust_events=0"))
        monkeypatch.setattr(R, "_load_qlib", lambda c: (_series(DATES, raw), ""))
        monkeypatch.setattr(R, "WIN_START", DATES[0])
        monkeypatch.setattr(R, "WIN_END", DATES[-1])
        R.detail("600519", top=5)
        assert "我们的自算前复权有问题" in capsys.readouterr().out

    def test_limit_breach_flags_only_offending_side(self, monkeypatch, capsys):
        """日收益超过涨跌幅限制是物理不可能 ⇒ 直接证伪那一侧。"""
        n = len(DATES)
        raw = [10.0] * n
        raw[30] = 11.5  # +15%，600xxx 不可能
        tdx = _series(DATES, [10.0] * n)
        tdx["raw_close"] = [10.0] * n
        monkeypatch.setattr(R, "_load_tdx", lambda c: (tdx, "adjust_events=0"))
        monkeypatch.setattr(R, "_load_qlib", lambda c: (_series(DATES, raw), ""))
        monkeypatch.setattr(R, "WIN_START", DATES[0])
        monkeypatch.setattr(R, "WIN_END", DATES[-1])
        R.detail("600519", top=3)
        out = capsys.readouterr().out
        assert "只有 qlib 越界" in out, out[-600:]


class TestGapReport:
    """缺口代价诊断：**先量清代价，再决定要不要修**。

    两个 bundle 之间有约 10 个月缺口（2020-09-28 ~ 2021-07-30）。
    但「补缺口」未必是最优解——仍在市的票可由 vipdoc 补（若能扩历史深度），
    真正补不回来的只有**缺口期间退市的票**。这个数才是缺口的实际代价。
    """

    def _mk(self, root, name, days, insts, fields):
        import numpy as np

        d = root / name
        (d / "instruments").mkdir(parents=True)
        (d / "calendars").mkdir(parents=True)
        (d / "calendars" / "day.txt").write_text(
            "\n".join(days) + "\n", encoding="utf-8"
        )
        (d / "instruments" / "all.txt").write_text(
            "\n".join(f"{i}\t{days[0]}\t{days[-1]}" for i in insts) + "\n",
            encoding="utf-8",
        )
        for i in insts:
            f = d / "features" / i.lower()
            f.mkdir(parents=True)
            for fn in fields:
                np.array([0.0] + [10.0] * len(days), dtype="<f4").tofile(
                    f / f"{fn}.day.bin"
                )
        return d

    def test_detects_gap_and_delisted_cost(self, tmp_path, monkeypatch, capsys):
        root = tmp_path / "Q_DATA"
        ohlcv = ["open", "high", "low", "close", "volume"]
        # 老 bundle 有 3 只，新 bundle 只剩 1 只 ⇒ 2 只在缺口前后退市
        self._mk(
            root,
            "2006_2020",
            ["2019-01-02", "2020-09-25"],
            ["SH600000", "SH600001", "SH600002"],
            ohlcv + ["factor"],
        )
        self._mk(root, "2021_2026", ["2021-08-02", "2026-02-06"], ["SH600000"], ohlcv)
        monkeypatch.setattr(R, "WIN_START", "2021-08-02")
        monkeypatch.setattr(R, "WIN_END", "2026-01-31")
        R.gap_report(sample=1, root=root)
        out = capsys.readouterr().out
        assert "缺口：2020-09-25 → 2021-08-02" in out
        assert "只在老 bundle 里" in out and "2 只" in out
        assert "口径" in out and "multiplicative" in out and "unverified" in out

    def test_reports_window_overlap(self, tmp_path, monkeypatch, capsys):
        """现有窗口是否踩缺口必须直接给出，而不是让人自己比日期。"""
        root = tmp_path / "Q_DATA"
        ohlcv = ["open", "high", "low", "close", "volume"]
        self._mk(
            root, "a", ["2019-01-02", "2020-09-25"], ["SH600000"], ohlcv + ["factor"]
        )
        self._mk(root, "b", ["2021-08-02", "2026-02-06"], ["SH600000"], ohlcv)
        monkeypatch.setattr(R, "WIN_START", "2021-08-02")
        monkeypatch.setattr(R, "WIN_END", "2026-01-31")
        R.gap_report(sample=1, root=root)
        out = capsys.readouterr().out
        assert "m2 --cross-window" in out and "✅ 不跨" in out

    def test_no_gap_says_so(self, tmp_path, monkeypatch, capsys):
        root = tmp_path / "Q_DATA"
        ohlcv = ["open", "high", "low", "close", "volume", "factor"]
        self._mk(root, "a", ["2019-01-02", "2021-08-01"], ["SH600000"], ohlcv)
        self._mk(root, "b", ["2021-08-02", "2026-02-06"], ["SH600000"], ohlcv)
        R.gap_report(sample=1, root=root)
        assert "✅ 无缺口" in capsys.readouterr().out

    def test_reports_debias_value_per_bundle(self, tmp_path, monkeypatch, capsys):
        """每个 bundle 的**去偏价值** = 它有多少票是本地 vipdoc 没有的（≈ 已退市）。

        去偏价值为 0 意味着 universe≈在市股 ⇒ 该 bundle 对「去幸存者偏差」毫无贡献；
        若它同时还有价格口径问题，就是**纯负债**。
        实测 2021_2026 正是这种情况（universe 5484 ≈ vipdoc 5536、加法调整、
        覆盖期还短于 vipdoc）。
        """
        root = tmp_path / "Q_DATA"
        ohlcv = ["open", "high", "low", "close", "volume"]
        # 老 bundle 含 2 只已退市（600001/600002 不在 vipdoc）
        self._mk(
            root,
            "2006_2020",
            ["2019-01-02", "2020-09-25"],
            ["SH600000", "SH600001", "SH600002"],
            ohlcv + ["factor"],
        )
        # 新 bundle 全部在 vipdoc 里 ⇒ 去偏价值 0
        self._mk(root, "2021_2026", ["2021-08-02", "2026-02-06"], ["SH600000"], ohlcv)
        from custos.datasource.local_tdx import local_tdx_data

        monkeypatch.setattr(
            local_tdx_data, "list_local_vipdoc_codes", lambda *a, **k: ["600000"]
        )
        monkeypatch.setattr(R, "WIN_START", "2021-08-02")
        monkeypatch.setattr(R, "WIN_END", "2026-01-31")
        R.gap_report(sample=1, root=root)
        out = capsys.readouterr().out
        assert "去偏价值" in out
        assert "纯负债" in out, "去偏价值为 0 的 bundle 必须被点名"
        assert "2 只退市票可用于去偏" in out
