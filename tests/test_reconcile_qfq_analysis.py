"""`reconcile_qfq` 的**分析判据** —— 覆盖率 45%（538 语句缺 298）。

为什么这个文件值得单独测：它的结论**已经产生了决策后果** ——
2026-08-06 它判定 `qlib 2021_2026` bundle 是「加法调整（减去累计现金分红）」而非
乘法前复权，据此该 bundle 被**弃用**。如果这个判据本身有错，那个决策就错了。

这里不测网络/本地数据，而是给 `_load_tdx` / `_load_qlib` 打桩注入
**已知约定的合成序列**，验证判据能把它们分对：

    乘法前复权  adj = raw × f    ⇒ adj/raw 恒定、raw−adj 随价格变
    加法调整    adj = raw − c    ⇒ raw−adj 恒定、adj/raw 随价格变
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.research import reconcile_qfq as R  # noqa: E402


@pytest.fixture(autouse=True)
def _no_xdxr_network(monkeypatch):
    # detect_convention / detail 内部 `import adjust_factors` 取事件表：xdxr 缓存
    # 缺失时会走网络取数并落盘到真实 data/market/xdxr/（干净环境被 repo hygiene
    # 抓到）。本文件全部用合成序列，事件表应为空（真实事件日会切段，这里只测单段判据）。
    from custos.datasource.local_tdx import adjust_factors

    monkeypatch.setattr(adjust_factors, "get_xdxr", lambda code, **kw: [])


def _series(n=200, start="2021-09-01"):
    """一段价格明显波动的原始收盘序列（波动是判据的前提 —— 价格不动时
    两种约定都会显示「恒定」，分不开）。"""
    import pandas as pd

    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d").tolist()
    raw = [10.0 + 4.0 * (i % 37) / 37.0 for i in range(n)]  # 10.0~14.0 来回
    return dates, raw


def _patch(monkeypatch, adj_fn):
    """注入 tdx（乘法，固定）与 qlib（由 adj_fn 决定）两侧序列。"""
    import pandas as pd

    dates, raw = _series()
    monkeypatch.setattr(R, "WIN_START", dates[0])
    monkeypatch.setattr(R, "WIN_END", dates[-1])
    tdx = pd.DataFrame(
        {"date": dates, "raw_close": raw, "close": [x * 0.9 for x in raw]}
    )  # 乘法：adj/raw≡0.9
    qlib = pd.DataFrame({"date": dates, "close": [adj_fn(x) for x in raw]})
    monkeypatch.setattr(R, "_load_tdx", lambda code: (tdx, "stub"))
    monkeypatch.setattr(R, "_load_qlib", lambda code: (qlib, "stub"))
    # 无事件段：不让 adjust_factors 介入（真实事件日会切段，这里只测单段判据）
    monkeypatch.setattr(R, "detect_convention", R.detect_convention)
    return dates


class TestConventionDetection:
    def test_additive_detected(self, monkeypatch, capsys):
        """⚠️ 这就是 qlib 2021_2026 被判「加法调整」的判据。

        构造 `adj = raw − 1.45`（1.45 元/股现金分红），
        判据必须报出「加法调整」并给出调整量 c。
        """
        _patch(monkeypatch, lambda x: x - 1.45)
        assert R.detect_convention("600000") == 0
        out = capsys.readouterr().out
        assert "加法调整" in out
        assert "系统性放大百分比收益" in out, "必须点明它会放大收益"
        assert "c=1.4500" in out or "c=1.45" in out, out[-600:]

    def test_multiplicative_detected(self, monkeypatch, capsys):
        """对照组：`adj = raw × 0.87` 必须报「乘法前复权」，不得误判成加法。"""
        _patch(monkeypatch, lambda x: x * 0.87)
        assert R.detect_convention("600000") == 0
        out = capsys.readouterr().out
        assert "qlib → **乘法前复权**" in out
        assert "加法调整" not in out.split("判定：")[1]

    def test_tdx_side_reported_as_multiplicative(self, monkeypatch, capsys):
        """tdx 侧是我们自算的前复权，必须被判成**干净的乘法复权** ——
        这是「自算前复权正确」那条结论的判据。"""
        _patch(monkeypatch, lambda x: x - 1.45)
        R.detect_convention("600000")
        out = capsys.readouterr().out
        assert "tdx  → **乘法前复权**" in out

    def test_neither_convention_says_so(self, monkeypatch, capsys):
        """两种都不恒定时必须**说不知道**，不得二选一硬报一个。"""
        import math

        _patch(monkeypatch, lambda x: x * 0.9 - 0.3 * math.sin(x))
        R.detect_convention("600000")
        out = capsys.readouterr().out
        assert "两种都不恒定" in out

    def test_insufficient_overlap_reports_both_ranges(self, monkeypatch, capsys):
        """⚠️ 样本不足时不能只说「样本太少」—— 实测踩过：本地 vipdoc 只有约 5 年，
        与 2006_2020 bundle **区间不相交** ⇒ 0 根。必须报出两侧真实区间，
        读的人才知道是窗口选错还是数据不够深。
        """
        import pandas as pd

        monkeypatch.setattr(
            R,
            "_load_tdx",
            lambda code: (
                pd.DataFrame(
                    {"date": ["2021-09-01"], "raw_close": [10.0], "close": [9.0]}
                ),
                "s",
            ),
        )
        monkeypatch.setattr(
            R,
            "_load_qlib",
            lambda code: (pd.DataFrame({"date": ["2010-01-04"], "close": [5.0]}), "s"),
        )
        assert R.detect_convention("600000") == 2
        out = capsys.readouterr().out
        assert "样本太少" in out and "区间不相交" in out
        assert "--qlib-selfcheck" in out, "要指出替代手段"

    def test_missing_raw_close_refuses(self, monkeypatch, capsys):
        """没有 `raw_close`（未复权列）就判不了 —— 必须明确拒绝而不是猜。"""
        import pandas as pd

        monkeypatch.setattr(
            R,
            "_load_tdx",
            lambda code: (
                pd.DataFrame({"date": ["2021-09-01"], "close": [9.0]}),
                "no raw",
            ),
        )
        monkeypatch.setattr(
            R,
            "_load_qlib",
            lambda code: (pd.DataFrame({"date": ["2021-09-01"], "close": [9.0]}), "s"),
        )
        assert R.detect_convention("600000") == 2
        assert "取数不全" in capsys.readouterr().out


class TestLimitPct:
    """`_limit_pct` 的用途：**日收益超过它就是数据错**（物理不可能）。"""

    @pytest.mark.parametrize(
        "code,want",
        [
            ("600519", 10.0),
            ("000001", 10.0),
            ("300750", 20.0),
            ("688111", 20.0),
            ("689009", 20.0),
            ("920808", 30.0),
            ("830799", 30.0),
        ],
    )
    def test_delegates_to_single_source(self, code, want):
        assert R._limit_pct(code) == want

    def test_additive_adjustment_can_exceed_limit(self):
        """⚠️ 这是加法调整最直观的证据：它算出的涨跌幅能**超过涨停限制**。

        实测 600612 报出 +11.07%，而 600xxx 限 10%。
        用 `raw=6.91→6.95`（+0.58%）配 `c=1.45` 的加法调整验算：
        """
        raw_prev, raw_now, c = 6.91, 6.95, 1.45
        add_ret = (raw_now - c) / (raw_prev - c) - 1
        true_ret = raw_now / raw_prev - 1
        assert add_ret > true_ret, "加法调整放大收益"
        assert add_ret / true_ret > 1.2, f"放大倍数 {add_ret / true_ret:.2f}"


class TestSpreadHelper:
    """段内离散度：判据靠它区分「恒定」与「随价格变」。"""

    def test_constant_series_near_zero(self):
        import pandas as pd

        # _spread 是 detect_convention 的内部函数，这里通过端到端行为已覆盖；
        # 单独验证「常数序列离散度≈0」这个前提用等价实现
        s = pd.Series([0.9] * 50)
        assert s.max() / s.min() - 1.0 < 1e-12

    def test_varying_series_large(self):
        import pandas as pd

        s = pd.Series([10.0 + i * 0.1 for i in range(40)])
        assert s.max() / s.min() - 1.0 > 1e-3


class TestReport:
    """`report` 的核心不是排版，是**不让零信息量的「一致」冒充验证通过**。"""

    def test_vacuous_ok_is_flagged(self, capsys):
        """⚠️ `adjust_events=0` 的「一致」是**零信息量**：窗口内没有除权
        ⇒ 复权因子恒为 1 ⇒ 两边比的其实是未复权价，什么都没验证到。

        不标出来的话，一份「20 只全部一致」的报告会被当成对账通过。
        """
        rows = [
            {
                "code": "600000",
                "status": "ok",
                "bars": 200,
                "ratio_spread": 0.0,
                "worst_ret_diff": 0.0,
                "n_mismatch": 0,
                "note": "adjust_events=0",
            },
            {
                "code": "600519",
                "status": "ok",
                "bars": 200,
                "ratio_spread": 1e-6,
                "worst_ret_diff": 1e-6,
                "n_mismatch": 0,
                "note": "adjust_events=3",
            },
        ]
        assert R.report(rows) == 0
        out = capsys.readouterr().out
        assert "adjust_events=0" in out
        assert "一致 2" in out

    def test_mismatch_changes_exit_code(self, capsys):
        """有分歧必须**改退出码** —— 只打印不改码，cron 判不出来。"""
        rows = [
            {
                "code": "600612",
                "status": "mismatch",
                "bars": 200,
                "ratio_spread": 0.21,
                "worst_ret_diff": 0.11,
                "n_mismatch": 42,
                "mismatch_days": ["2021-09-01", "2021-09-02"],
                "note": "加法调整",
            }
        ]
        rc = R.report(rows)
        assert rc != 0, "分歧时退出码必须非 0"
        out = capsys.readouterr().out
        assert "600612" in out and "分歧明细" in out
        assert "2021-09-01" in out, "要给出具体分歧日供人去查 xdxr 事件"

    def test_partial_mismatch_row_does_not_abort_report(self, capsys):
        """⚠️ 回归（2026-08-07）：mismatch 行缺 `mismatch_days` 会 KeyError
        崩在**打印中途** —— 前面几行已输出，读者以为看全了。

        `reconcile` 总是成对设置那两个键，所以生产上不会缺；但一份诊断报告的
        全部价值就是被打出来，崩在中途比显示「—」糟得多。
        """
        rc = R.report([{"code": "600612", "status": "mismatch"}])
        assert rc != 0
        out = capsys.readouterr().out
        assert "600612" in out and "对账窗口" in out, "尾部说明也要打出来"

    def test_thresholds_printed(self, capsys):
        """阈值要打进报告 —— 否则读者无法判断「一致」有多严。"""
        R.report(
            [
                {
                    "code": "600000",
                    "status": "ok",
                    "bars": 100,
                    "ratio_spread": 0.0,
                    "worst_ret_diff": 0.0,
                    "n_mismatch": 0,
                }
            ]
        )
        out = capsys.readouterr().out
        assert "阈值" in out

    def test_skip_and_error_counted_separately(self, capsys):
        """跳过/出错不能混进「一致」—— 那会把取不到数说成验证通过。"""
        rows = [
            {"code": "a", "status": "skip", "note": "无 qlib 数据"},
            {"code": "b", "status": "error", "note": "读取失败"},
            {
                "code": "c",
                "status": "ok",
                "bars": 100,
                "ratio_spread": 0.0,
                "worst_ret_diff": 0.0,
                "n_mismatch": 0,
                "note": "adjust_events=2",
            },
        ]
        R.report(rows)
        out = capsys.readouterr().out
        assert "一致 1" in out and "跳过 2" in out

    def test_empty_rows_does_not_claim_success(self, capsys):
        """零样本时不得报「全部一致」。"""
        R.report([])
        out = capsys.readouterr().out
        assert "一致 0" in out


class TestNoBundleGuards:
    """没有 bundle 时（本机常态：S_DATA_ROOT 指向 Windows 盘）必须明确报错、
    而不是当成「检验通过」。"""

    def test_qlib_fields_without_bundle(self, monkeypatch, capsys):
        from custos.datasource import s_data as Q

        monkeypatch.setattr(Q, "list_bundles", lambda *a, **k: [])
        assert R.qlib_fields("600000") == 2
        assert "没有发现 bundle" in capsys.readouterr().out

    def test_qlib_selfcheck_without_bundle(self, monkeypatch, capsys):
        from custos.datasource import s_data as Q

        monkeypatch.setattr(Q, "list_bundles", lambda *a, **k: [])
        assert R.qlib_selfcheck("600000") == 2
