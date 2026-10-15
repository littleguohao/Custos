# -*- coding: utf-8 -*-
"""TODO #96 0AMV 手工录入防护钉测（v0.378）。

compass vdat 自 2026-07-17 停更 ⇒ 每日 0AMV 全靠手工录入，录错直接污染 regime：

- collector ``--amv``：|值| > EXTREME_AMV_PCT 必须 --confirm-extreme 二次确认
  （否则 exit 2 拒绝落盘）；跨过状态机阈值（>+4 / <−2.3，与
  ``amv_state._resolve_state`` 同口径）打醒目 WARN；显式写 source="manual"；
  只有 --confirm 才置 confirmed。
- merge_incremental_market：manual 来源**不自动升 confirmed**；
  自动源（amv_0day / 台账 confirmed 回退）维持原样。
- 报告侧（final_close_review / daily_report / review_core）：越线提示、漏录告警、
  manual 标签——全是**展示层**提示，不阻断（硬闸照旧：未确认读数不过盘后
  ``_check_amv_gate``，见 test_close_review_mains.TestZeroAmvGate）。
"""

from __future__ import annotations

import json
import sys

import pytest

from custos.pipeline.market_timing import market_timing_collector as mtc  # noqa: E402
from custos.pipeline.market_timing import merge_incremental_market as mim  # noqa: E402
from custos.pipeline.close_review import final_close_review as fcr  # noqa: E402
from custos.pipeline import daily_report as dr  # noqa: E402

DAY = "2026-10-15"


# ---------------------------------------------------------------- collector ----
# derive_market_fields / read_day 打桩：本机无 vipdoc，钉的是录入防护而非采集。
_STUB_SECTIONS = (
    {"as_of": None, "quality": "missing"},  # market_breadth
    {"as_of": None, "quality": "missing"},  # sentiment
    {"as_of": None, "quality": "missing"},  # turnover
    {"notes": [], "sources": []},  # data_quality
)


def _drive_collector(monkeypatch, tmp_path, argv):
    md = tmp_path / "market"
    monkeypatch.setattr(mtc, "OUT_DIR", md)
    monkeypatch.setattr(mtc, "MARKET_DIR", md)
    monkeypatch.setattr(mtc, "derive_market_fields", lambda d: _STUB_SECTIONS)
    monkeypatch.setattr(mtc, "read_day", lambda *a, **k: [])
    monkeypatch.setattr(sys, "argv", argv)
    mtc.main()
    return md / f"{DAY}_market_timing_input.json"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestExtremeEntryGuard:
    """绝对值超阈值（占位 15%）必须 --confirm-extreme 二次确认。"""

    def test_extreme_without_flag_refused_exit2_nothing_written(
        self, monkeypatch, tmp_path, capsys
    ):
        out = tmp_path / "market" / f"{DAY}_market_timing_input.json"
        monkeypatch.setattr(mtc, "OUT_DIR", tmp_path / "market")
        monkeypatch.setattr(mtc, "MARKET_DIR", tmp_path / "market")
        monkeypatch.setattr(sys, "argv", ["x", "--date", DAY, "--amv", "16.0"])
        with pytest.raises(SystemExit) as e:
            mtc.main()
        assert e.value.code == 2, "超阈值无 --confirm-extreme 必须 exit 2"
        assert not out.exists(), "拒绝时不得落盘"
        err = capsys.readouterr().err
        assert "+16.00%" in err and "15" in err, "stderr 必须打出数值与阈值"
        assert "--confirm-extreme" in err

    def test_extreme_with_flag_writes_and_warns(self, monkeypatch, tmp_path, capsys):
        out = _drive_collector(
            monkeypatch,
            tmp_path,
            ["x", "--date", DAY, "--amv", "16.0", "--confirm-extreme"],
        )
        d = _read(out)
        assert d["amv_0"]["amv_change_pct"] == 16.0
        assert d["amv_0"]["source"] == "manual"
        err = capsys.readouterr().err
        assert "二次确认" in err, "带旗标落盘时必须留「已二次确认」WARN"

    def test_extreme_boundary_not_blocked(self, monkeypatch, tmp_path, capsys):
        """阈值是严格大于：|15.0| > 15.0 为假 ⇒ 不拦。"""
        out = _drive_collector(
            monkeypatch, tmp_path, ["x", "--date", DAY, "--amv", "15.0"]
        )
        assert _read(out)["amv_0"]["amv_change_pct"] == 15.0
        assert "REFUSED" not in capsys.readouterr().err


class TestCrossingWarn:
    """跨过状态机阈值（>+4 / <−2.3）⇒ stderr 醒目 WARN；阈值内无。"""

    @pytest.mark.parametrize("v", [4.5, -2.4, 5.0, -5.0])
    def test_crossing_warns(self, monkeypatch, tmp_path, capsys, v):
        _drive_collector(monkeypatch, tmp_path, ["x", "--date", DAY, "--amv", str(v)])
        err = capsys.readouterr().err
        assert "跨过状态机阈值" in err and "核对" in err

    @pytest.mark.parametrize("v", [1.0, 4.0, -2.3, 0.0])
    def test_within_thresholds_silent(self, monkeypatch, tmp_path, capsys, v):
        """4.0 / −2.3 是严格不等号的边界——恰在阈值上不算越线（同 _resolve_state）。"""
        _drive_collector(monkeypatch, tmp_path, ["x", "--date", DAY, "--amv", str(v)])
        assert "跨过状态机阈值" not in capsys.readouterr().err


class TestManualSourceAndConfirm:
    def test_manual_entry_marks_source_not_confirmed(self, monkeypatch, tmp_path):
        """--amv 不带 --confirm：source=manual，不写 quality（=candidate），as_of 留 None。"""
        out = _drive_collector(
            monkeypatch, tmp_path, ["x", "--date", DAY, "--amv", "1.2"]
        )
        amv = _read(out)["amv_0"]
        assert amv["source"] == "manual"
        assert "quality" not in amv, (
            "未确认的手工读数不得带 quality 键（门控按 candidate）"
        )
        assert amv["as_of"] is None

    def test_confirm_marks_confirmed_with_as_of(self, monkeypatch, tmp_path):
        """--confirm 是 manual 唯一升 confirmed 的通道（vdat 停更后 regime 转移靠它）。"""
        out = _drive_collector(
            monkeypatch, tmp_path, ["x", "--date", DAY, "--amv", "1.2", "--confirm"]
        )
        amv = _read(out)["amv_0"]
        assert amv["quality"] == "confirmed"
        assert amv["source"] == "manual"
        assert amv["as_of"] == DAY

    def test_confirm_flags_without_amv_warn_and_ignored(
        self, monkeypatch, tmp_path, capsys
    ):
        out = _drive_collector(monkeypatch, tmp_path, ["x", "--date", DAY, "--confirm"])
        amv = _read(out)["amv_0"]
        assert amv["amv_change_pct"] is None
        assert "quality" not in amv and "source" not in amv
        assert "旗标忽略" in capsys.readouterr().err


# ---------------------------------------------------------------- merge ----
def _merge_env(monkeypatch, tmp_path):
    md = tmp_path / "data" / "market"
    md.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mim, "MARKET_DIR", md)
    monkeypatch.setattr(mim, "QUALITY_DIR", tmp_path / "data" / "quality")
    return md


def _run_merge(md, day=DAY):
    rc = mim.main(["--date", day])
    mkt = _read(md / f"{day}_market_timing_input.json")
    status = json.loads(
        (
            md.parent.parent
            / "data"
            / "quality"
            / f"{day}_merge_incremental_status.json"
        ).read_text(encoding="utf-8")
    )
    return rc, mkt, status


class TestMergeManualGuard:
    def test_manual_not_auto_confirmed_from_amv_0day(
        self, monkeypatch, tmp_path, capsys
    ):
        """manual 候选：即便 amv_0day 在场也不升级、不覆盖手工值、留 source 标签。"""
        md = _merge_env(monkeypatch, tmp_path)
        (md / f"{DAY}_market_timing_input.json").write_text(
            json.dumps(
                {"amv_0day": 5.1, "amv_0": {"amv_change_pct": 4.5, "source": "manual"}}
            ),
            encoding="utf-8",
        )
        rc, mkt, status = _run_merge(md)
        assert rc == 0, "manual 不升级是正常分支，不得失败"
        amv = mkt["amv_0"]
        assert amv.get("quality") != "confirmed", "manual 不得自动升 confirmed"
        assert amv["source"] == "manual"
        assert amv["amv_change_pct"] == 4.5, "手工值不得被 amv_0day 覆盖"
        assert status["amv_manual_candidate"] is True
        assert status.get("amv_confirmed") is not True
        assert "--confirm" in capsys.readouterr().err, "WARN 须指引确认通道"

    def test_ledger_fallback_does_not_promote_manual(
        self, monkeypatch, tmp_path, capsys
    ):
        """台账 confirmed 观测回退同样不得升级 manual 节（确认通道只有 --confirm）。"""
        md = _merge_env(monkeypatch, tmp_path)
        (md / f"{DAY}_market_timing_input.json").write_text(
            json.dumps({"amv_0": {"amv_change_pct": 1.1, "source": "manual"}}),
            encoding="utf-8",
        )
        (md / "0amv_observations.jsonl").write_text(
            json.dumps(
                {
                    "date": DAY,
                    "amv_change_pct": 1.1,
                    "as_of": DAY,
                    "quality": "confirmed",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        rc, mkt, status = _run_merge(md)
        assert rc == 0
        assert mkt["amv_0"].get("quality") != "confirmed"
        assert status["amv_manual_candidate"] is True

    def test_auto_source_still_auto_confirms(self, monkeypatch, tmp_path):
        """回归钉测：非 manual 节（无 source）+ amv_0day ⇒ 自动升 confirmed 原样。"""
        md = _merge_env(monkeypatch, tmp_path)
        (md / f"{DAY}_market_timing_input.json").write_text(
            json.dumps({"amv_0day": 5.1, "amv_0": {}}), encoding="utf-8"
        )
        rc, mkt, status = _run_merge(md)
        assert rc == 0
        amv = mkt["amv_0"]
        assert amv["quality"] == "confirmed"
        assert amv["amv_change_pct"] == 5.1
        assert amv["as_of"] == DAY
        assert status["amv_confirmed"] is True
        assert "amv_manual_candidate" not in status

    def test_confirmed_manual_left_untouched(self, monkeypatch, tmp_path):
        """manual + --confirm（已是 confirmed）⇒ merge 不覆盖手工值（外条件就不进）。"""
        md = _merge_env(monkeypatch, tmp_path)
        (md / f"{DAY}_market_timing_input.json").write_text(
            json.dumps(
                {
                    "amv_0day": 5.1,
                    "amv_0": {
                        "amv_change_pct": 4.5,
                        "source": "manual",
                        "quality": "confirmed",
                        "as_of": DAY,
                    },
                }
            ),
            encoding="utf-8",
        )
        rc, mkt, _status = _run_merge(md)
        assert rc == 0
        assert mkt["amv_0"]["amv_change_pct"] == 4.5
        assert mkt["amv_0"]["quality"] == "confirmed"


# ---------------------------------------------------------------- 报告提示 ----
class TestCrossingAlertHelper:
    """final_close_review 报告头越线提示：跨阈值有线、阈值内无、缺值无。"""

    @pytest.mark.parametrize("v", [4.5, -2.4])
    def test_crossing_produces_line(self, v):
        line = fcr._amv_crossing_alert(v)
        assert line is not None and "越线提示" in line and f"{v:+.2f}%" in line
        assert "不阻断" in line

    @pytest.mark.parametrize("v", [1.0, 4.0, -2.3, None, "nan"])
    def test_no_line_within_thresholds(self, v):
        assert fcr._amv_crossing_alert(v) is None

    def test_thresholds_shared_with_state_machine(self):
        """提示阈值必须与状态机同一来源（amv_state 常量），防两套口径漂移。"""
        from custos.pipeline.market_timing import amv_state

        assert fcr.AMV_LONG_PCT is amv_state.AMV_LONG_PCT
        assert fcr.AMV_SHORT_PCT is amv_state.AMV_SHORT_PCT
        assert mtc.AMV_LONG_PCT is amv_state.AMV_LONG_PCT


class TestAmvSourceLabel:
    def test_manual_label(self):
        assert "manual" in fcr._amv_source_label({"source": "manual"})

    def test_auto_source_no_label(self):
        assert fcr._amv_source_label({}) == ""
        assert fcr._amv_source_label({"source": "market_timing_input"}) == ""


class TestDailyReportAmvGuardLines:
    """0905 盘前报告顶部提示（不阻断）：漏录告警 / manual candidate 提示。"""

    def test_stale_amv_alerts_missing_entry(self):
        lines = dr._amv_guard_lines(
            {"checks": [{"field": "0AMV", "quality": "stale", "as_of": "2026-10-13"}]}
        )
        assert lines and "漏录告警" in lines[0] and "2026-10-13" in lines[0]
        assert "不阻断" in lines[0] and "--confirm" in lines[0]

    def test_missing_amv_alerts(self):
        lines = dr._amv_guard_lines(
            {"checks": [{"field": "0AMV", "quality": "missing"}]}
        )
        assert lines and "漏录告警" in lines[0]

    def test_candidate_amv_shows_manual_note(self):
        """manual candidate 计入「0AMV 可用」展示——提示但不告警漏录。"""
        lines = dr._amv_guard_lines(
            {"checks": [{"field": "0AMV", "quality": "candidate"}]}
        )
        assert lines and "manual" in lines[0] and "不驱动 regime 转移" in lines[0]
        assert "漏录告警" not in lines[0]

    def test_confirmed_amv_silent(self):
        assert (
            dr._amv_guard_lines({"checks": [{"field": "0AMV", "quality": "confirmed"}]})
            == []
        )

    def test_no_checks_silent(self):
        assert dr._amv_guard_lines({}) == []
        assert (
            dr._amv_guard_lines({"checks": [{"field": "turnover", "quality": "stale"}]})
            == []
        )
