# -*- coding: utf-8 -*-
"""TODO #96 0AMV 手工录入防护钉测（v0.378 引入 / **v0.380 语义反转**）。

compass vdat 自 2026-07-17 停更 ⇒ 每日 0AMV 全靠手工录入，录错直接污染 regime。
v0.380（owner 实测拍板）：**手工录入本身就算确认**——collector ``--amv`` 落盘即
confirmed+source=manual+as_of=录入日（v0.378 的「默认 candidate + --confirm 才确认」
被推翻：忘带旗标的静默不翻转比录错更危险）。保护来自：

- 超阈值二次确认：|值| > EXTREME_AMV_PCT 未给 --confirm-extreme ⇒ exit 2 拒落盘；
- 越线醒目提示（>+4 / <−2.3，与 ``amv_state._resolve_state`` 同口径）：collector
  stderr WARN + 17:00 报告头提示行（不阻断）；
- 漏录告警：09:05 报告顶部（不阻断）；
- merge 恢复「有值即升 confirmed」（manual 节同样适用，v0.378 特判已撤）；
- daily_pipeline --amv-pct 旁路同款超阈值校验（EXTREME_AMV_PCT 单源 collector）。
"""

from __future__ import annotations

import argparse
import json
import sys

import pytest

from custos.pipeline.market_timing import amv_state  # noqa: E402
from custos.pipeline.market_timing import market_timing_collector as mtc  # noqa: E402
from custos.pipeline.market_timing import merge_incremental_market as mim  # noqa: E402
from custos.pipeline.close_review import final_close_review as fcr  # noqa: E402
from custos.pipeline import daily_report as dr  # noqa: E402

DAY = "2026-10-15"
PREV = "2026-10-14"


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

    def test_extreme_with_flag_writes_confirmed_and_warns(
        self, monkeypatch, tmp_path, capsys
    ):
        out = _drive_collector(
            monkeypatch,
            tmp_path,
            ["x", "--date", DAY, "--amv", "16.0", "--confirm-extreme"],
        )
        amv = _read(out)["amv_0"]
        assert amv["amv_change_pct"] == 16.0
        assert amv["source"] == "manual"
        assert amv["quality"] == "confirmed", "manual 录入即确认（v0.380）"
        assert amv["as_of"] == DAY
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


class TestManualEntryIsConfirmed:
    """v0.380（owner 拍板）：**手工录入本身就算确认**——不需要任何确认旗标。"""

    def test_manual_entry_confirmed_by_default(self, monkeypatch, tmp_path):
        """--amv 不带任何旗标：落盘即 confirmed + source=manual + as_of=录入日。"""
        out = _drive_collector(
            monkeypatch, tmp_path, ["x", "--date", DAY, "--amv", "1.2"]
        )
        amv = _read(out)["amv_0"]
        assert amv["quality"] == "confirmed"
        assert amv["source"] == "manual"
        assert amv["as_of"] == DAY

    def test_confirm_flag_removed(self, monkeypatch, tmp_path):
        """--confirm 旗标已删（v0.380）：再用就是 argparse 用法错（exit 2）。"""
        monkeypatch.setattr(mtc, "OUT_DIR", tmp_path / "market")
        monkeypatch.setattr(mtc, "MARKET_DIR", tmp_path / "market")
        monkeypatch.setattr(
            sys, "argv", ["x", "--date", DAY, "--amv", "1.2", "--confirm"]
        )
        with pytest.raises(SystemExit) as e:
            mtc.main()
        assert e.value.code == 2
        assert not (tmp_path / "market" / f"{DAY}_market_timing_input.json").exists()

    def test_confirm_extreme_without_amv_warn_and_ignored(
        self, monkeypatch, tmp_path, capsys
    ):
        out = _drive_collector(
            monkeypatch, tmp_path, ["x", "--date", DAY, "--confirm-extreme"]
        )
        amv = _read(out)["amv_0"]
        assert amv["amv_change_pct"] is None
        assert "quality" not in amv and "source" not in amv
        assert "旗标忽略" in capsys.readouterr().err

    def test_crossing_manual_entry_drives_regime_flip(self, monkeypatch, tmp_path):
        """⚠️ owner 实测场景回归钉（v0.378 失败链）：前态空头 + 手工录 +4.6%
        不带任何旗标 ⇒ 落盘 confirmed ⇒ amv_state 照常翻转做多、台账记 confirmed
        （v0.378 时这天在 regime 重放里缺失）。"""
        md = tmp_path / "market"
        md.mkdir(parents=True)
        monkeypatch.setattr(amv_state, "MARKET", md)
        monkeypatch.setattr(amv_state, "STATE", md / "0amv_regime_history.json")
        monkeypatch.setattr(amv_state, "LEDGER", md / "0amv_observations.jsonl")
        # 前一日 confirmed 空头
        (md / f"{PREV}_market_timing_input.json").write_text(
            json.dumps({"amv_0": {"amv_change_pct": -5.84, "quality": "confirmed"}}),
            encoding="utf-8",
        )
        assert amv_state.compute(PREV)["effective_state"] == "空头"
        # 当日手工越线录入（无旗标）⇒ confirmed 落盘
        out = _drive_collector(
            monkeypatch, tmp_path, ["x", "--date", DAY, "--amv", "4.6"]
        )
        amv = _read(out)["amv_0"]
        assert amv["quality"] == "confirmed" and amv["source"] == "manual"
        # regime 照常转移 + 台账记 confirmed（研究侧只采 confirmed，这天不再缺）
        rec = amv_state.compute(DAY)
        assert rec["effective_state"] == "做多", "confirmed 越线读数必须驱动转移"
        assert rec["confirmed"] is True
        day_recs = [
            json.loads(x)
            for x in (md / "0amv_observations.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if x.strip() and json.loads(x)["date"] == DAY
        ]
        assert day_recs[-1]["quality"] == "confirmed"
        assert day_recs[-1]["source"] == "manual"


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


class TestMergeManualRestored:
    """v0.380：撤掉 v0.378 的 manual 特判——merge 恢复「有值即升 confirmed」，
    manual 节同样适用（v0.378 形态的历史 candidate 节由此被升级收口）。"""

    def test_manual_candidate_promoted_from_amv_0day(self, monkeypatch, tmp_path):
        """manual 候选节 + amv_0day 在场 ⇒ 恢复自动升 confirmed（v0.378 是不升）。"""
        md = _merge_env(monkeypatch, tmp_path)
        (md / f"{DAY}_market_timing_input.json").write_text(
            json.dumps(
                {"amv_0day": 5.1, "amv_0": {"amv_change_pct": 4.5, "source": "manual"}}
            ),
            encoding="utf-8",
        )
        rc, mkt, status = _run_merge(md)
        assert rc == 0
        amv = mkt["amv_0"]
        assert amv["quality"] == "confirmed"
        assert amv["amv_change_pct"] == 5.1
        assert amv["as_of"] == DAY
        assert amv["source"] == "manual", "source 标签保留"
        assert status["amv_confirmed"] is True
        assert "amv_manual_candidate" not in status, "v0.378 特判的 status 键已撤"

    def test_ledger_fallback_promotes_manual_candidate(self, monkeypatch, tmp_path):
        """台账 confirmed 观测回退对 manual 节同样升级（v0.378 是不升）。"""
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
        assert mkt["amv_0"]["quality"] == "confirmed"
        assert mkt["amv_0"]["source"] == "manual"
        assert status["amv_confirmed"] is True
        assert status["amv_source"] == "0amv_observations"

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

    def test_confirmed_manual_left_untouched(self, monkeypatch, tmp_path):
        """manual 已 confirmed（v0.380 落盘形态）⇒ merge 外条件不进、不覆盖手工值。"""
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


# ------------------------------------------------------- daily_pipeline 旁路 ----
class TestDailyPipelineBypassGuard:
    """daily_pipeline --amv-pct 旁路收口（v0.380）：与 collector 同款超阈值校验。"""

    def _env(self, monkeypatch, tmp_path):
        from custos.pipeline import daily_pipeline as dp

        md = tmp_path / "market"
        md.mkdir(parents=True)
        monkeypatch.setattr(dp, "MARKET_DIR", md)
        (md / f"{DAY}_market_timing_input.json").write_text(
            json.dumps({"date": DAY, "amv_0": {}}), encoding="utf-8"
        )
        return dp, md

    def test_extreme_amv_pct_refused_without_flag(self, monkeypatch, tmp_path, capsys):
        """超阈值且无 --confirm-extreme ⇒ 拒绝落盘（照该函数既有失败语义：ok=False）。"""
        dp, md = self._env(monkeypatch, tmp_path)
        r = dp.apply_manual_market(DAY, None, None, 16.5)
        assert r["ok"] is False
        assert "--confirm-extreme" in r["message"]
        assert _read(md / f"{DAY}_market_timing_input.json")["amv_0"] == {}, (
            "拒绝时不得写入"
        )
        assert "--confirm-extreme" in capsys.readouterr().err

    def test_extreme_amv_pct_applied_with_flag(self, monkeypatch, tmp_path):
        dp, md = self._env(monkeypatch, tmp_path)
        r = dp.apply_manual_market(DAY, None, None, 16.5, confirm_extreme=True)
        assert r["ok"] is True
        amv = _read(md / f"{DAY}_market_timing_input.json")["amv_0"]
        assert amv["amv_change_pct"] == 16.5
        assert amv["quality"] == "confirmed"
        assert amv["amv_zone"] == "做多"

    def test_thresholds_single_sourced(self):
        """旁路的范围阈值/regime 阈值必须与 collector/amv_state 同一来源。"""
        from custos.pipeline import daily_pipeline as dp

        assert dp.EXTREME_AMV_PCT is mtc.EXTREME_AMV_PCT
        assert dp.AMV_LONG_PCT is amv_state.AMV_LONG_PCT
        assert dp.AMV_SHORT_PCT is amv_state.AMV_SHORT_PCT

    def test_confirm_extreme_forwarded_to_collector(self, monkeypatch, tmp_path):
        """--confirm-extreme 必须透传给 collector 子 stage（否则子 stage 拦死）。"""
        from custos.pipeline import daily_pipeline as dp

        monkeypatch.setattr(dp, "MARKET_DIR", tmp_path / "market")
        cmds = []
        monkeypatch.setattr(
            dp, "run_stage", lambda cmd, name, **kw: cmds.append(cmd) or {"ok": True}
        )
        args = argparse.Namespace(
            date=DAY,
            refresh_market=True,
            amv_pct=16.5,
            confirm_extreme=True,
            macro=None,
            amv_zone=None,
            reuse_discovery=True,
            non_trading_day=False,
            session_type="postclose",
        )
        dp._collect_market_stages(args, [])
        cmd = next(c for c in cmds if any("market_timing_collector" in p for p in c))
        assert "--amv" in cmd and "--confirm-extreme" in cmd


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
        assert fcr.AMV_LONG_PCT is amv_state.AMV_LONG_PCT
        assert fcr.AMV_SHORT_PCT is amv_state.AMV_SHORT_PCT
        assert mtc.AMV_LONG_PCT is amv_state.AMV_LONG_PCT


class TestAmvSourceLabel:
    def test_manual_label(self):
        label = fcr._amv_source_label({"source": "manual"})
        assert "manual 手工录入" in label
        assert "--confirm" not in label, "v0.380：manual 即确认，标签不提确认通道"

    def test_auto_source_no_label(self):
        assert fcr._amv_source_label({}) == ""
        assert fcr._amv_source_label({"source": "market_timing_input"}) == ""


class TestDailyReportAmvGuardLines:
    """0905 盘前报告顶部提示（不阻断）：漏录告警 / v0.378 历史 candidate 提示。"""

    def test_stale_amv_alerts_missing_entry(self):
        lines = dr._amv_guard_lines(
            {"checks": [{"field": "0AMV", "quality": "stale", "as_of": "2026-10-13"}]}
        )
        assert lines and "漏录告警" in lines[0] and "2026-10-13" in lines[0]
        assert "不阻断" in lines[0]
        assert "--confirm" not in lines[0], "v0.380：补录不需要确认旗标"

    def test_missing_amv_alerts(self):
        lines = dr._amv_guard_lines(
            {"checks": [{"field": "0AMV", "quality": "missing"}]}
        )
        assert lines and "漏录告警" in lines[0]

    def test_candidate_amv_shows_manual_note(self):
        """candidate 只剩 v0.378 当天的历史形态——提示重跑录入即确认。"""
        lines = dr._amv_guard_lines(
            {"checks": [{"field": "0AMV", "quality": "candidate"}]}
        )
        assert lines and "manual" in lines[0] and "录入即确认" in lines[0]
        assert "漏录告警" not in lines[0]
        assert "--confirm" not in lines[0]

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


class TestNoArgAbbrev:
    """v0.380 实测坑：argparse 默认前缀缩写会把旧习惯 ``--confirm`` 解析成
    ``--confirm-extreme``，静默绕过超阈值二次确认——两个入口都必须拒。"""

    def test_daily_pipeline_rejects_confirm_prefix(self, monkeypatch):
        import sys

        import pytest

        from custos.pipeline import daily_pipeline as dp

        monkeypatch.setattr(
            sys,
            "argv",
            ["x", "--date", "2026-10-15", "--amv-pct", "16.5", "--confirm"],
        )
        with pytest.raises(SystemExit) as ei:
            dp._parse_args()
        assert ei.value.code == 2
