"""`probe_data_sources` —— 数据源探针（0% 覆盖，235 语句）。

它自己的 docstring 记着为什么需要它：`00_governance/data/` 下的文档要写「现状」，
但现状里的性能与稳定性**没有任何实测数据**。

⚠️ 这里测的**不是网络**，是它的**判据**。探针最容易犯的错就是它自己要暴露的那类问题
——「没抛异常」被当成「拿到数据」。源码里记着它差点犯：
`get_online_bars` 返回 0行×0列却被记成 3/3 成功。本文件把这条钉住。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("07_tools", "07_tools/local_tdx"):
    sys.path.insert(0, str(ROOT / _p))

from research import probe_data_sources as P  # noqa: E402


class TestEmptyIsNotSuccess:
    """⚠️ 核心不变量：**「没抛异常」≠「拿到数据」**，空结果单独计不算成功。"""

    def test_empty_dataframe_counted_as_empty(self):
        import pandas as pd
        pr = P.Probe("g", "n").run(lambda: pd.DataFrame(), repeat=3)
        d = pr.as_dict()
        assert d["ok"] == 0 and d["empty"] == 3
        assert d["success_rate"] == 0.0, "空结果不得算成功率"

    @pytest.mark.parametrize("val", [None, [], {}, ""])
    def test_empty_containers_counted_as_empty(self, val):
        pr = P.Probe("g", "n").run(lambda: val, repeat=1)
        assert pr.as_dict()["empty"] == 1 and pr.as_dict()["ok"] == 0

    def test_nonempty_counted_ok(self):
        pr = P.Probe("g", "n").run(lambda: [1, 2, 3], repeat=2)
        assert pr.as_dict()["ok"] == 2 and pr.as_dict()["empty"] == 0
        assert pr.as_dict()["success_rate"] == 1.0

    def test_mixed_rate(self):
        seq = iter([[1], None, [2]])
        pr = P.Probe("g", "n").run(lambda: next(seq), repeat=3)
        d = pr.as_dict()
        assert (d["ok"], d["empty"], d["success_rate"]) == (2, 1, round(2 / 3, 3))


class TestExceptionsAreCaptured:
    """⚠️ 「任何异常都收进 error 字段，不向上抛」—— 探针崩掉就拿不到其余源的现状。"""

    def test_exception_recorded_not_raised(self):
        def boom():
            raise ConnectionResetError("peer reset")

        pr = P.Probe("g", "n").run(boom, repeat=2)
        d = pr.as_dict()
        assert "ConnectionResetError" in d["error"] and "peer reset" in d["error"]
        assert d["ok"] == 0

    def test_timing_recorded_even_on_failure(self):
        """失败也要记耗时 —— 「超时 25 秒才失败」与「立刻拒连」是不同的现状。"""
        pr = P.Probe("g", "n").run(lambda: 1 / 0, repeat=1)
        d = pr.as_dict()
        assert d["attempts"] == 1 and d["ms_p50"] is not None

    def test_no_samples_gives_none_not_zero(self):
        """一次都没跑时耗时是 None 而非 0 —— 0ms 是个读数，None 才是「没测」。"""
        d = P.Probe("g", "n").as_dict()
        assert d["ms_p50"] is None and d["ms_max"] is None


class TestWiredFlag:
    def test_wired_distinguishes_probed_from_integrated(self):
        """⚠️ `wired` 区分「探过但没接入生产链」与「已接入」——
        治理文档要据此标风险等级，混在一起会让「探过」看着像「在用」。"""
        assert P.Probe("g", "n").as_dict()["wired"] is True
        assert P.Probe("g", "n", wired=False).as_dict()["wired"] is False


class TestDescribe:
    """`_describe` 把返回值形状写成一行文本，供治理文档填数。"""

    def test_dataframe_shape(self):
        import pandas as pd
        s = P._describe(pd.DataFrame({"a": [1, 2], "b": [3, 4]}))
        assert "2" in s and ("a" in s or "col" in s.lower())

    def test_list_and_dict(self):
        assert P._describe([1, 2, 3])
        assert P._describe({"k": 1})

    def test_none(self):
        assert P._describe(None) is not None, "None 也要给出可读描述而不是崩"

    def test_tolerates_unserializable(self):
        """⚠️ 回归（2026-08-07 发现）：`_describe` 的兜底分支执行 `str(out)`，
        那会调对象的 `__str__`/`__repr__` —— 可能抛。

        而 `_describe` 在 `Probe.run` 里是**在 try 之外**调用的，抛上去就打破
        本模块最明确的契约「任何异常都收进 error 字段，不向上抛」，
        并中断整轮探测（拿不到其余数据源的现状）。
        """
        class Weird:
            def __repr__(self):
                raise RuntimeError("no repr")

            __str__ = __repr__

        out = P._describe(Weird())
        assert "无法描述" in out and "RuntimeError" in out

    def test_probe_run_survives_undescribable_result(self):
        """端到端：返回值无法描述时 `run` 仍要正常收尾。"""
        class Weird:
            def __repr__(self):
                raise RuntimeError("x")
            __str__ = __repr__
            def __len__(self):
                return 1

        pr = P.Probe("g", "n").run(lambda: Weird(), repeat=1)
        assert pr.as_dict()["ok"] == 1


class TestReport:
    def test_report_prints_all_groups(self, capsys):
        """`report` 是**打印器**（返回 None）。它必须把每个探针都打出来 ——
        少打一行就等于治理文档里那一格没数据却没人知道。"""
        probes = [P.Probe("mootdx", "daily").run(lambda: [1], 1),
                  P.Probe("mootdx", "quotes").run(lambda: None, 1),
                  P.Probe("tq", "sector", wired=False).run(lambda: 1 / 0, 1)]
        assert P.report(probes) is None
        text = capsys.readouterr().out
        for g in ("mootdx", "tq"):
            assert g in text
        assert "daily" in text and "quotes" in text and "sector" in text
        assert "ZeroDivisionError" in text, "错误必须出现在报告里"

    def test_report_marks_not_wired(self):
        """⚠️ 报告要能看出「探过但没接入」—— 否则会被当成在用。"""
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            P.report([P.Probe("tq", "x", wired=False).run(lambda: [1], 1)])
        assert buf.getvalue().strip(), "未接入的探针也要出现在报告里"

    @staticmethod
    def _out(probes):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            P.report(probes)
        return buf.getvalue()

    def test_environment_hint_only_when_something_failed(self):
        """⚠️ 「区分环境没装 vs 接口坏了」的提示**只在有失败项时**打 ——
        Linux/CI 上 mootdx 不可用是**预期**，不能被读成故障。
        全成功时不打这句（避免每次都提示一个不存在的问题）。
        """
        failed = self._out([P.Probe("mootdx", "x").run(lambda: 1 / 0, 1)])
        assert "环境没装" in failed
        ok = self._out([P.Probe("mootdx", "x").run(lambda: [1], 1)])
        assert "环境没装" not in ok

    def test_empty_returns_flagged_as_more_dangerous(self):
        """⚠️ 空返回要单独成节并写明「**比报错更危险：调用方看不出**」——
        这正是本探针存在的理由，报告里不能把它和报错混在一起。"""
        text = self._out([P.Probe("g", "x").run(lambda: [], 1)])
        assert "没抛异常但返回空" in text and "更危险" in text

    def test_footer_disclaims_being_a_test(self):
        """报告尾部必须写明「**不是**单元测试：结果依赖宿主环境，不能作为断言」——
        否则有人会把某次探测的数字当成契约。"""
        text = self._out([P.Probe("g", "x").run(lambda: [1], 1)])
        assert "不是" in text and "不能作为断言" in text
