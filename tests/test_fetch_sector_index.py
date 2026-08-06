# -*- coding: utf-8 -*-
"""板块指数缓存刷新测试。

回归背景(2026-07-30 18:00 选股链 degraded):
1. `--period day` 是错的周期串 —— TQ 要 "1d"(见 00_governance/data/TQ_INTERFACE_PROBE_2026-07-20.md:67,
   缺省/写错报 ErrorId=5 periodstr error),结果 400+ 板块逐个失败;
2. 每天**全量**重拉 20180101 起的历史 → run_stage 600s 超时。
现在:周期串自动探测 + 探测失败快速退出 + 增量合并(不截断回测深度)。
"""
from __future__ import annotations

import pandas as pd
import pytest

from local_tdx import fetch_sector_index_history as fs


class _TQ:
    """最小 TQ 替身:只接受指定周期串,其余抛错(模拟 periodstr error)。"""

    def __init__(self, good_period="1d", rows=None, fail_all=False):
        self.good = good_period
        self.fail_all = fail_all
        self.rows = rows or [("2026-07-29", 100.0), ("2026-07-30", 101.0)]
        self.refresh_calls: list[tuple] = []
        self.data_calls: list[dict] = []

    def refresh_kline(self, codes, period=""):
        self.refresh_calls.append((tuple(codes), period))
        if self.fail_all or period != self.good:
            raise RuntimeError("ErrorId=5 periodstr error")

    def get_market_data(self, field_list=None, stock_list=None, period="", start_time="", count=0):
        self.data_calls.append({"period": period, "start": start_time, "codes": tuple(stock_list or ())})
        if self.fail_all or period != self.good:
            raise RuntimeError("ErrorId=5 periodstr error")
        code = (stock_list or ["880001"])[0]
        df = pd.DataFrame({"Close": [c for _, c in self.rows]},
                          index=pd.to_datetime([d for d, _ in self.rows]))
        return {code: df}


class TestResolvePeriod:
    def test_finds_1d_when_day_rejected(self):
        tq = _TQ(good_period="1d")
        period, note = fs.resolve_period(tq, "880001", "20180101", wanted="day")
        assert period == "1d" and "1d" in note
        assert [p for _, p in tq.refresh_calls][:2] == ["day", "1d"]   # 先试指定值再走候选

    def test_wanted_period_tried_first_and_kept(self):
        tq = _TQ(good_period="day")
        period, _ = fs.resolve_period(tq, "880001", "20180101", wanted="day")
        assert period == "day" and len(tq.refresh_calls) == 1

    def test_all_candidates_failing_returns_empty_with_reasons(self):
        tq = _TQ(fail_all=True)
        period, note = fs.resolve_period(tq, "880001", "20180101")
        assert period == ""
        assert "periodstr" in note
        assert len(tq.refresh_calls) == len(fs.PERIOD_CANDIDATES)      # 只在一个板块上试


class TestMergeCloseFrame:
    def _write(self, p, rows):
        pd.DataFrame(rows, columns=["date", "close"]).to_csv(p, index=False)

    def test_merge_preserves_history_depth(self, tmp_path):
        """核心回归:增量刷新必须合并,否则回测所需的 2018 年以来深度会被截断成几十根。"""
        dest = tmp_path / "880001.csv"
        self._write(dest, [("2018-01-02", 90.0), ("2026-07-29", 100.0)])
        new = pd.DataFrame([("2026-07-30", 101.0)], columns=["date", "close"])
        out = fs.merge_close_frame(dest, new)
        assert list(out["date"]) == ["2018-01-02", "2026-07-29", "2026-07-30"]

    def test_new_value_wins_on_same_date(self, tmp_path):
        dest = tmp_path / "880001.csv"
        self._write(dest, [("2026-07-30", 100.0)])
        out = fs.merge_close_frame(dest, pd.DataFrame([("2026-07-30", 111.0)],
                                                     columns=["date", "close"]))
        assert len(out) == 1 and out["close"].iloc[0] == 111.0

    def test_no_existing_file_returns_new(self, tmp_path):
        out = fs.merge_close_frame(tmp_path / "none.csv",
                                   pd.DataFrame([("2026-07-30", 1.0)], columns=["date", "close"]))
        assert list(out["date"]) == ["2026-07-30"]

    def test_empty_new_frame_returns_none(self, tmp_path):
        assert fs.merge_close_frame(tmp_path / "none.csv", None) is None
        assert fs.merge_close_frame(tmp_path / "none.csv",
                                    pd.DataFrame(columns=["date", "close"])) is None

    def test_corrupt_existing_file_warns_and_quarantines(self, tmp_path, capsys):
        """损坏缓存不得静默丢弃:必须 WARN + 改名隔离(留现场),再只用新数据落盘并提示全量重拉。"""
        dest = tmp_path / "880001.csv"
        dest.write_text("not,a,valid\ncsv", encoding="utf-8")
        out = fs.merge_close_frame(dest, pd.DataFrame([("2026-07-30", 1.0)],
                                                      columns=["date", "close"]))
        assert list(out["date"]) == ["2026-07-30"]                  # 不 raise,用新数据继续
        err = capsys.readouterr().err
        assert "[WARN]" in err and "损坏" in err and "全量重拉" in err
        quarantined = list(tmp_path.glob("880001.csv.corrupt-*"))   # 隔离而非覆写,保留现场
        assert len(quarantined) == 1
        assert quarantined[0].read_text(encoding="utf-8") == "not,a,valid\ncsv"
        assert not dest.exists()                                    # 原路径让位给新数据原子落盘


class TestIncrementalStart:
    def test_uses_last_cached_date_minus_overlap(self, tmp_path):
        dest = tmp_path / "880001.csv"
        pd.DataFrame([("2026-07-30", 1.0)], columns=["date", "close"]).to_csv(dest, index=False)
        assert fs.incremental_start(dest, "20180101") == "20260630"      # 30 天重叠

    def test_floor_respected(self, tmp_path):
        dest = tmp_path / "880001.csv"
        pd.DataFrame([("2018-01-10", 1.0)], columns=["date", "close"]).to_csv(dest, index=False)
        assert fs.incremental_start(dest, "20180101") == "20180101"      # 不早于 floor

    def test_missing_or_bad_cache_falls_back_to_floor(self, tmp_path):
        assert fs.incremental_start(tmp_path / "none.csv", "20180101") == "20180101"
        bad = tmp_path / "bad.csv"
        bad.write_text("x", encoding="utf-8")
        assert fs.incremental_start(bad, "20180101") == "20180101"


class TestMainWiring:
    @pytest.fixture(autouse=True)
    def _tq(self, monkeypatch):
        monkeypatch.setattr(fs.tq_sector, "is_tdxw_running", lambda: True)

    def _install(self, monkeypatch, tq, sectors=("880001", "880002")):
        tq.initialize = lambda *a, **k: None
        tq.close = lambda: None
        tq.get_sector_list = lambda: list(sectors)
        monkeypatch.setattr(fs.tq_sector, "_import_tq", lambda: tq)
        return tq

    def test_wrong_period_default_is_recovered(self, tmp_path, monkeypatch, capsys):
        tq = self._install(monkeypatch, _TQ(good_period="1d"))
        rc = fs.main(["--out", str(tmp_path), "--start", "20180101"])
        out = capsys.readouterr().out
        assert rc == 0 and "使用周期 1d" in out
        assert (tmp_path / "880001.SH.csv").is_file() and (tmp_path / "880002.SH.csv").is_file()

    def test_unusable_period_fails_fast_without_scanning_all_sectors(self, tmp_path, monkeypatch, capsys):
        tq = self._install(monkeypatch, _TQ(fail_all=True), sectors=[f"88{i:04d}" for i in range(50)])
        rc = fs.main(["--out", str(tmp_path), "--start", "20180101"])
        assert rc == 2
        assert "无可用周期串" in capsys.readouterr().err
        # 只在第一个板块上探测,不得对 50 个板块各刷一遍
        assert len(tq.refresh_calls) == len(fs.PERIOD_CANDIDATES)

    def test_incremental_merges_and_narrows_request_window(self, tmp_path, monkeypatch):
        dest = tmp_path / "880001.SH.csv"
        pd.DataFrame([("2018-01-02", 90.0), ("2026-07-29", 100.0)],
                     columns=["date", "close"]).to_csv(dest, index=False)
        tq = self._install(monkeypatch, _TQ(good_period="1d"), sectors=("880001",))
        rc = fs.main(["--out", str(tmp_path), "--start", "20180101", "--incremental"])
        assert rc == 0
        got = pd.read_csv(dest, dtype={"date": str})
        assert list(got["date"]) == ["2018-01-02", "2026-07-29", "2026-07-30"]   # 深度保留
        starts = [c["start"] for c in tq.data_calls if c["codes"] == ("880001.SH",)]
        assert starts[-1] == "20260629"                    # 请求窗口收窄(不再每天重拉 8 年)

    def test_full_mode_still_overwrites(self, tmp_path, monkeypatch):
        dest = tmp_path / "880001.SH.csv"
        pd.DataFrame([("2018-01-02", 90.0)], columns=["date", "close"]).to_csv(dest, index=False)
        self._install(monkeypatch, _TQ(good_period="1d"), sectors=("880001",))
        fs.main(["--out", str(tmp_path), "--start", "20180101"])
        got = pd.read_csv(dest, dtype={"date": str})
        assert list(got["date"]) == ["2026-07-29", "2026-07-30"]      # 全量模式=以 TQ 返回为准

    def test_limit_restricts_sectors(self, tmp_path, monkeypatch):
        self._install(monkeypatch, _TQ(good_period="1d"), sectors=("880001", "880002", "880003"))
        fs.main(["--out", str(tmp_path), "--limit", "1"])
        assert (tmp_path / "880001.SH.csv").is_file()
        assert not (tmp_path / "880002.SH.csv").exists()
