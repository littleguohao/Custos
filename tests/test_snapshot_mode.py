# -*- coding: utf-8 -*-
"""开发机快照模式（TODO #116，设计稿 governance/data/MINING_SNAPSHOT_DESIGN.md）钉测。

锁的契约（与导出侧 scripts/dev/export_mining_snapshot.py 共享）：

- ``CUSTOS_DATA_SNAPSHOT=<dir>`` ⇒ paths 单点改道：DATA→<dir>/data、
  TDX_ROOT→<dir>；import 时 stderr 打一行 ``[SNAPSHOT]`` 横幅；
- 禁带 = (2016-12-31, 2020-10-01) ∪ ≥ 2026-09-05：加载器读到 ⇒ 报错
  （预热段 [2020-10-01, 2021-12-31] 可读，是挖掘 B 起点指标预热的定案豁免）；
- 三道硬闸：window_usage.record_use / judgment_batch（批次门 + _append 写入口）
  / live 五入口（calendar_gate exit 2、runtime_gate exit 6）在快照模式一律拒；
- load_amv_regime 快照分支读导出的 regime 序列（缺文件 fail-closed raise）；
- provenance.build 记 data_source="snapshot" + snapshot_sha（非快照两键皆 None）。

全部合成件：迷你 vipdoc 树与 MANIFEST 都建在 tmp_path，不碰真实 data/ 与
governance/ 下的台账（tests/test_repo_hygiene.py 钉着）。
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from custos.core import paths
from custos.core import pipeline_kit
from custos.core import runtime_gate
from custos.datasource.local_tdx import local_tdx_data as ltd
from custos.research import backtest_factors as bf
from custos.research import judgment_batch
from custos.research import load_window
from custos.research import provenance
from custos.research import window_usage

# ---------------------------------------------------------------------------
# 横幅 / 改道（子进程钉：import 时行为只有新进程才看得到）
# ---------------------------------------------------------------------------


def _mini_snapshot(root: Path) -> Path:
    """造迷你快照目录：MANIFEST.json + vipdoc 骨架（镜像契约的最小集）。"""
    (root / "vipdoc" / "sh" / "lday").mkdir(parents=True)
    (root / "vipdoc" / "bj" / "lday").mkdir(parents=True)
    (root / "data" / "market").mkdir(parents=True)
    (root / "MANIFEST.json").write_text(
        json.dumps({"manifest_sha": "abc123def456", "files": {}}), encoding="utf-8"
    )
    return root


def _probe_paths(env_extra: dict) -> subprocess.CompletedProcess:
    """子进程跑 `from custos.core import paths`，打印 TDX_ROOT 与 DATA。"""
    env = os.environ.copy()
    env.pop("CUSTOS_DATA_SNAPSHOT", None)
    env.update(env_extra)
    # 子进程 I/O 固定 UTF-8：探针要把含中文的路径打到 stdout 管道——Windows 下管道默认
    # 走系统代码页（cp1252/cp936），stdout 是 strict ⇒ UnicodeEncodeError 退出 1（生产机实红）
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [
            sys.executable,
            "-c",
            ("from custos.core import paths;print(paths.TDX_ROOT);print(paths.DATA)"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=True,
    )


class TestBannerAndRedirect:
    def test_snapshot_mode_redirects_and_banners(self, tmp_path):
        root = _mini_snapshot(tmp_path).resolve()
        r = _probe_paths({"CUSTOS_DATA_SNAPSHOT": str(root)})
        out = r.stdout.splitlines()
        assert out == [str(root), str(root / "data")], (
            f"TDX_ROOT/DATA 未改道到快照: {out}"
        )
        assert "[SNAPSHOT]" in r.stderr and "abc123def456" in r.stderr

    def test_missing_dir_banner_marks_but_not_crash(self, tmp_path):
        gone = tmp_path / "不存在"
        r = _probe_paths({"CUSTOS_DATA_SNAPSHOT": str(gone)})
        assert "[SNAPSHOT]" in r.stderr and "目录不存在" in r.stderr

    def test_no_env_no_redirect_no_banner(self):
        r = _probe_paths({})
        assert r.stdout.splitlines()[1] == str(paths.BASE / "data")
        assert "[SNAPSHOT]" not in r.stderr

    def test_banner_pure_function(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path.resolve())
        _mini_snapshot(tmp_path)
        line = paths.snapshot_banner()
        assert line.startswith(f"[SNAPSHOT] {tmp_path.resolve()} sha=abc123def456")
        assert "判定段/holdout 不在快照内" in line
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", None)
        assert paths.snapshot_banner() == ""


class TestMirrorConstants:
    """paths（L0）不能 import research——禁带常量是 load_window 段边界的**镜像**，
    两边相等必须在这里钉住（改了一边没改另一边 = 快照闸门口径漂移）。"""

    def test_gap_and_bar_max_mirror_load_window(self):
        assert paths.SNAPSHOT_GAP == (load_window.SEG_MINING_A[1], "2020-10-01")
        assert paths.SNAPSHOT_BAR_MAX == load_window.SEG_MINING_B[1]
        assert load_window.FORWARD_HOLDOUT_START == "2026-09-05"


class TestBarViolation:
    @pytest.mark.parametrize(
        "d",
        ["2016-12-31", "2020-10-01", "2021-12-31", "2026-09-04", "2000-01-04"],
    )
    def test_allowed_dates_pass(self, d):
        assert paths.snapshot_bar_violation([d]) is None

    def test_mixed_legal_dates_pass(self):
        assert (
            paths.snapshot_bar_violation(["2016-12-31", "2020-10-01", "2026-09-04"])
            is None
        )

    @pytest.mark.parametrize("d", ["2017-01-01", "2020-09-30", "2018-06-15"])
    def test_gap_dates_rejected(self, d):
        msg = paths.snapshot_bar_violation([d])
        assert msg and d in msg and "禁带" in msg

    @pytest.mark.parametrize("d", ["2026-09-05", "2027-01-01"])
    def test_holdout_dates_rejected(self, d):
        msg = paths.snapshot_bar_violation([d])
        assert msg and d in msg

    def test_first_violation_reported(self):
        msg = paths.snapshot_bar_violation(["2016-12-31", "2018-01-02", "2019-01-02"])
        assert msg is not None and "2018-01-02" in msg


# ---------------------------------------------------------------------------
# K 线闸门（read_vipdoc_daily 三条返回路径）
# ---------------------------------------------------------------------------


def _day_record(date_int: int) -> bytes:
    """通达信 .day 单条记录：32 字节 `<IIIIIfII`（同 test_local_tdx_data_local）。"""
    return struct.pack("<IIIIIfII", date_int, 1000, 1100, 990, 1050, 1.0e7, 50000, 0)


def _write_day(root: Path, market: str, fname: str, date_ints: list[int]) -> None:
    p = root / "vipdoc" / market / "lday" / f"{fname}.day"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"".join(_day_record(d) for d in date_ints))


class TestVipdocGate:
    def test_bj_direct_path_rejects_gap_bar(self, tmp_path, monkeypatch):
        """BJ 直读路径（不经 mootdx）：含 2018 年 bar ⇒ LocalTdxError。"""
        _write_day(tmp_path, "bj", "bj920001", [20161230, 20180102])
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path)
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        with pytest.raises(ltd.LocalTdxError, match="快照数据污染"):
            ltd.read_vipdoc_daily("920001.BJ")

    def test_bj_direct_path_legal_bars_pass(self, tmp_path, monkeypatch):
        """挖掘 A + 预热 + 挖掘 B 的合法日期 ⇒ 照常返回。"""
        _write_day(tmp_path, "bj", "bj920001", [20161230, 20211008, 20260904])
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path)
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        df = ltd.read_vipdoc_daily("920001.BJ")
        assert len(df) == 3
        assert df["source"].iloc[0] == "vipdoc_bj_direct"

    def test_bj_direct_path_no_snapshot_no_gate(self, tmp_path, monkeypatch):
        """非快照模式：同样的 2018 年 bar 照常返回（闸门零开销零变化）。"""
        _write_day(tmp_path, "bj", "bj920001", [20180102])
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path)
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", None)
        df = ltd.read_vipdoc_daily("920001.BJ")
        assert len(df) == 1

    def test_mootdx_reader_path_rejects_gap_bar(self, tmp_path, monkeypatch):
        """mootdx reader 路径（沪深个股）：含 2018 年 bar ⇒ LocalTdxError。"""
        pytest.importorskip("mootdx")
        _write_day(tmp_path, "sh", "sh600000", [20161230, 20180102])
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path)
        monkeypatch.setattr(ltd, "_reader", None)  # 强制按新 TDX_ROOT 重建 reader
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        with pytest.raises(ltd.LocalTdxError, match="快照数据污染"):
            ltd.read_vipdoc_daily("600000.SH")

    def test_mootdx_reader_path_legal_bars_pass(self, tmp_path, monkeypatch):
        pytest.importorskip("mootdx")
        _write_day(tmp_path, "sh", "sh600000", [20161230, 20211008, 20260904])
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path)
        monkeypatch.setattr(ltd, "_reader", None)
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        df = ltd.read_vipdoc_daily("600000.SH")
        assert len(df) == 3


# ---------------------------------------------------------------------------
# 三道硬闸
# ---------------------------------------------------------------------------


class TestHardGates:
    def test_record_use_rejected_before_synthetic_shortcut(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        with pytest.raises(ValueError, match="快照模式禁止写判定窗使用台账"):
            window_usage.record_use("U", "judgment_2017_2021", "t", "p")
        # 快照检查在 synthetic 捷径之前——合成运行也照样拒
        with pytest.raises(ValueError, match="快照模式"):
            window_usage.record_use("U", "judgment_2017_2021", "t", "p", synthetic=True)
        # 闸门在写之前——台账文件不得出现
        assert not (tmp_path / "window_usage.jsonl").exists()

    def test_judgment_batch_append_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        with pytest.raises(ValueError, match="快照模式"):
            judgment_batch._append({"ev": "open"}, tmp_path / "x.jsonl")
        assert not (tmp_path / "x.jsonl").exists()

    def test_gate_judgment_read_rejected_with_exit_2(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        ap = argparse.ArgumentParser()
        with pytest.raises(SystemExit) as exc:
            judgment_batch.gate_judgment_read(
                argparse.Namespace(batch_id="", candidate_id=""),
                ap,
                synthetic=False,
                judgment_span=("2017-01-01", "2021-12-31"),
                tool="t",
            )
        assert exc.value.code == 2  # ap.error 口径

    def test_calendar_gate_rejected_without_touching_calendar(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)

        def _boom(_d):
            raise AssertionError("快照模式不得查交易日历（不得起子进程）")

        monkeypatch.setattr(pipeline_kit, "check_trading_day", _boom)
        gate = pipeline_kit.calendar_gate(
            "2026-10-26",
            log_dir=tmp_path,
            session="1800",
            run_started="2026-10-26T18:00:00",
            t0=0.0,
            stages_log=[],
            fail_msg="F {target} {err}",
            closed_msg="C {target}",
        )
        assert gate.exit_code == 2 and gate.cal == {}
        assert "快照模式" in capsys.readouterr().err
        assert not list(tmp_path.glob("*_run_log.json")), "拒跑不得落 run_log"

    def test_runtime_gate_exit_6(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        assert runtime_gate.main(["--date", "2026-10-26"]) == 6
        assert "快照模式" in capsys.readouterr().err

    def test_gates_open_when_not_snapshot(self, tmp_path, monkeypatch, capsys):
        """对照：非快照模式下三道闸不触发（record_use 正常写、calendar_gate 照常走）。"""
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", None)
        ledger = tmp_path / "window_usage.jsonl"
        k = window_usage.record_use("U", "judgment_2017_2021", "t", "p", path=ledger)
        assert k == 1 and ledger.exists()
        monkeypatch.setattr(
            pipeline_kit, "check_trading_day", lambda d: {"is_trading_day": True}
        )
        gate = pipeline_kit.calendar_gate(
            "2026-10-26",
            log_dir=tmp_path,
            session="1800",
            run_started="x",
            t0=0.0,
            stages_log=[],
            fail_msg="F {target} {err}",
            closed_msg="C {target}",
        )
        assert gate.exit_code is None


# ---------------------------------------------------------------------------
# load_amv_regime 快照分支 / provenance 两键
# ---------------------------------------------------------------------------


class TestAmvRegimeSnapshotBranch:
    def _write_series(self, market_dir: Path) -> None:
        market_dir.mkdir(parents=True, exist_ok=True)
        (market_dir / "amv_regime_series.json").write_text(
            json.dumps(
                {
                    "schema": "amv_regime_series/v1",
                    "series": {
                        "2016-12-30": "中性",
                        "2022-01-04": "做多",
                        "2026-09-04": "空头",
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_snapshot_branch_reads_exported_series(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        market = tmp_path / "data" / "market"
        self._write_series(market)
        monkeypatch.setattr(bf, "MARKET_DIR", market)
        got = bf.load_amv_regime(since="2022-01-01")
        assert got == {"2022-01-04": "做多", "2026-09-04": "空头"}, (
            "since 过滤 + 全量返回"
        )
        assert len(bf.load_amv_regime(since="2000-01-01")) == 3

    def test_snapshot_missing_series_fail_closed(self, tmp_path, monkeypatch):
        """缺文件 ⇒ raise（0AMV 是方向主过滤器，不得静默退 {}）。"""
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        monkeypatch.setattr(bf, "MARKET_DIR", tmp_path / "data" / "market")
        with pytest.raises(ValueError, match="amv_regime_series"):
            bf.load_amv_regime()

    def test_snapshot_corrupt_series_fail_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        market = tmp_path / "data" / "market"
        market.mkdir(parents=True)
        (market / "amv_regime_series.json").write_text("{坏", encoding="utf-8")
        monkeypatch.setattr(bf, "MARKET_DIR", market)
        with pytest.raises(ValueError, match="amv_regime_series"):
            bf.load_amv_regime()


class TestProvenanceSnapshotKeys:
    def test_snapshot_mode_marks_data_source(self, tmp_path, monkeypatch):
        (tmp_path / "MANIFEST.json").write_text(
            json.dumps({"manifest_sha": "f" * 16}), encoding="utf-8"
        )
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        blk = provenance.build(
            argparse.Namespace(codes_file="", cmdline=None), unit="R99"
        )
        assert blk["data_source"] == "snapshot"
        assert blk["snapshot_sha"] == "f" * 16

    def test_non_snapshot_both_none(self, monkeypatch):
        """非快照运行两键皆 None——既有产物零漂移。"""
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", None)
        blk = provenance.build(
            argparse.Namespace(codes_file="", cmdline=None), unit="R99"
        )
        assert blk["data_source"] is None
        assert blk["snapshot_sha"] is None


# ---------------------------------------------------------------------------
# review #1：conftest 防呆；review #2：PIT / xdxr / share_changes 禁带闸
# ---------------------------------------------------------------------------


class TestConftestStripsSnapshotEnv:
    """conftest 顶部 pop 掉 CUSTOS_DATA_SNAPSHOT（review #1）——若有人在 shell
    里 export 了它，测试会话绝不能在快照模式下跑（否则台账类测试被硬闸拦、
    加载类测试读真实快照，报错难懂）。monkeypatch 用例结束后 SNAPSHOT_ROOT
    会被还原，本断言与用例执行顺序无关。"""

    def test_env_absent_and_root_none(self):
        assert "CUSTOS_DATA_SNAPSHOT" not in os.environ
        assert paths.SNAPSHOT_ROOT is None


class TestSnapshotDatePredicates:
    """三条禁带谓词的边界钉——横杠与纯数字两种形态都要精确（"20161231" 与
    "2016-12-31" 同值；``_d8n`` 数字归一前，混比会把挖掘 A 最末日误判进禁带）。"""

    @pytest.mark.parametrize(
        "d,expect",
        [
            ("2016-12-31", False),
            ("20161231", False),  # 挖掘 A 最末日（两形态）
            ("2017-01-01", True),
            ("20170101", True),  # 判定段首日
            ("2020-09-30", True),
            ("20200930", True),  # 断口最末日
            ("2020-10-01", False),
            ("20201001", False),  # 预热段首日
            ("2026-09-04", False),
            ("20260904", False),  # 快照截止日
            ("2026-09-05", True),
            ("20260905", True),  # holdout 首日
        ],
    )
    def test_bar_violation_boundaries(self, d, expect):
        assert (paths.snapshot_bar_violation([d]) is not None) is expect

    @pytest.mark.parametrize(
        "d,expect",
        [
            ("2016-12-31", False),  # 挖掘 A 最末日
            ("2017-01-01", True),  # 判定段首日（PIT 无 2017~2019 增量）
            ("2019-12-31", True),  # 增量前最后日
            ("2020-01-01", False),  # PIT 增量首日（比 K 线预热更早）
            ("2026-09-04", False),
            ("2026-09-05", True),
        ],
    )
    def test_pit_violation_boundaries(self, d, expect):
        assert (paths.snapshot_pit_violation([d]) is not None) is expect

    def test_event_violation(self):
        """事件日（xdxr/股本）：判定段年份合法，只禁 holdout；无日期字段跳过。"""
        assert paths.snapshot_event_violation(["2018-06-01", "2026-09-04"]) is None
        assert paths.snapshot_event_violation(["2026-10-09"]) is not None
        assert paths.snapshot_event_violation([None, ""]) is None

    def test_pit_gap_mirrors_export_rule(self):
        """SNAPSHOT_PIT_GAP 与导出侧 _keep_pit 的边界同值（镜像钉）。"""
        assert paths.SNAPSHOT_PIT_GAP == ("2016-12-31", "2020-01-01")


class TestSnapshotContaminationGates:
    """review #2：PIT / xdxr / share_changes 读入侧的禁带闸（污染 ⇒ 当场炸；
    判定段年份的 PIT/事件合法——前复权与 as-of 股本都需要它们）。"""

    def test_pit_load_ledger_gate(self, tmp_path, monkeypatch):
        from custos.datasource.local_tdx import fetch_pit_financials as fpf

        f = tmp_path / "pit.jsonl"
        f.write_text(
            json.dumps(
                {
                    "code": "600000",
                    "report_date": "2018-03-31",
                    "notice_date": "2018-04-20",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        with pytest.raises(ValueError, match="PIT"):
            fpf.load_ledger(f)
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", None)
        assert len(fpf.load_ledger(f)) == 1  # 非快照不拦

    def test_xdxr_cache_gate(self, tmp_path, monkeypatch):
        from custos.datasource.local_tdx import adjust_factors as af

        d = tmp_path / "xdxr"
        d.mkdir()
        payload = {
            "code": "600000",
            "market": "SH",
            "n": 2,
            "events": [{"date": "2018-06-01"}, {"date": "2026-10-09"}],
        }
        (d / "600000.json").write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(af, "CACHE_DIR", d)
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        with pytest.raises(ValueError, match="xdxr"):
            af.load_xdxr_cache("600000")
        # 判定段事件合法（前复权需要）：去掉 holdout 事件即放行
        (d / "600000.json").write_text(
            json.dumps({**payload, "n": 1, "events": [{"date": "2018-06-01"}]}),
            encoding="utf-8",
        )
        assert af.load_xdxr_cache("600000") == [{"date": "2018-06-01"}]

    def test_share_changes_gate(self, tmp_path, monkeypatch):
        from custos.core.factors import _shares

        fund = tmp_path / "fundamentals"
        fund.mkdir()
        f = fund / "share_changes.jsonl"
        f.write_text(
            json.dumps(
                {"code": "600000", "observed_on": "2026-10-09", "total_shares": 100}
            )
            + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(paths, "DATA", tmp_path)
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        monkeypatch.setattr(_shares, "_SHARE_IDX", None)
        with pytest.raises(ValueError, match="share_changes"):
            _shares.shares_idx()
        # 判定段年份合法：换合法事件后放行（as-of 股本需要全史）
        f.write_text(
            json.dumps(
                {"code": "600000", "observed_on": "2018-05-01", "total_shares": 100}
            )
            + "\n",
            encoding="utf-8",
        )
        assert _shares.shares_idx() == {"600000": [("2018-05-01", 100)]}


class TestSectorCsvGate:
    """v0.454：板块指数 CSV（sector_phase.load_sector_gate / R48 板块腿）快照禁带检查。"""

    def _csv(self, tmp_path, dates):
        import pandas as pd

        d = tmp_path / "idx"
        d.mkdir(exist_ok=True)
        pd.DataFrame(
            {"date": dates, "close": [1.0 + i * 0.01 for i in range(len(dates))]}
        ).to_csv(d / "880001.csv", index=False)
        return d

    def test_gate_rejects_gap_rows(self, tmp_path, monkeypatch):
        from custos.core import paths
        from custos.core.factors import sector_phase as sp

        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        d = self._csv(tmp_path, ["2016-12-30", "2018-05-02"])
        with pytest.raises(ValueError, match="快照数据污染"):
            sp.load_sector_gate(d, {"880001": ["600000"]})

    def test_gate_legal_rows_pass_and_off_mode_noop(self, tmp_path, monkeypatch):
        from custos.core import paths
        from custos.core.factors import sector_phase as sp

        d = self._csv(tmp_path, ["2016-12-30", "2020-10-09"])
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        sp.load_sector_gate(d, {"880001": ["600000"]})
        d2 = self._csv(tmp_path, ["2018-05-02"])
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", None)
        sp.load_sector_gate(d2, {"880001": ["600000"]})  # 非快照不检查

    def test_tail_feature_sector_leg_rejects(self, tmp_path, monkeypatch):
        import json

        from custos.core import paths
        from custos.research import tail_feature_study as tf

        d = self._csv(tmp_path, ["2016-12-30", "2018-05-02"])
        mf = tmp_path / "members.json"
        mf.write_text(json.dumps({"880001": ["600000"]}), encoding="utf-8")
        monkeypatch.setattr(paths, "SECTOR_INDEX_DIR", d)
        monkeypatch.setattr(paths, "SECTOR_MEMBERS_FILE", mf)
        monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp_path)
        fn = tf.default_sector_fn()
        with pytest.raises(ValueError, match="快照数据污染"):
            fn("600000", "2016-12-30")
