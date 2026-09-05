# -*- coding: utf-8 -*-
"""板块指数缓存刷新测试。

回归背景(2026-07-30 18:00 选股链 degraded):
1. `--period day` 是错的周期串 —— TQ 要 "1d"(见 governance/data/TDX_LOCAL_INTERFACES.md「周期串是 1d」,
   缺省/写错报 ErrorId=5 periodstr error),结果 400+ 板块逐个失败;
2. 每天**全量**重拉 20180101 起的历史 → run_stage 600s 超时。
现在:周期串自动探测 + 探测失败快速退出 + 增量合并(不截断回测深度)。
"""

from __future__ import annotations

import pandas as pd
import pytest

from custos.datasource.local_tdx import fetch_sector_index_history as fs


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

    def get_market_data(
        self, field_list=None, stock_list=None, period="", start_time="", count=0
    ):
        self.data_calls.append(
            {"period": period, "start": start_time, "codes": tuple(stock_list or ())}
        )
        if self.fail_all or period != self.good:
            raise RuntimeError("ErrorId=5 periodstr error")
        code = (stock_list or ["880001"])[0]
        df = pd.DataFrame(
            {"Close": [c for _, c in self.rows]},
            index=pd.to_datetime([d for d, _ in self.rows]),
        )
        return {code: df}


class TestResolvePeriod:
    def test_finds_1d_when_day_rejected(self):
        tq = _TQ(good_period="1d")
        period, note = fs.resolve_period(tq, "880001", "20180101", wanted="day")
        assert period == "1d" and "1d" in note
        assert [p for _, p in tq.refresh_calls][:2] == [
            "day",
            "1d",
        ]  # 先试指定值再走候选

    def test_wanted_period_tried_first_and_kept(self):
        tq = _TQ(good_period="day")
        period, _ = fs.resolve_period(tq, "880001", "20180101", wanted="day")
        assert period == "day" and len(tq.refresh_calls) == 1

    def test_all_candidates_failing_returns_empty_with_reasons(self):
        tq = _TQ(fail_all=True)
        period, note = fs.resolve_period(tq, "880001", "20180101")
        assert period == ""
        assert "periodstr" in note
        assert len(tq.refresh_calls) == len(fs.PERIOD_CANDIDATES)  # 只在一个板块上试


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
        out = fs.merge_close_frame(
            dest, pd.DataFrame([("2026-07-30", 111.0)], columns=["date", "close"])
        )
        assert len(out) == 1 and out["close"].iloc[0] == 111.0

    def test_no_existing_file_returns_new(self, tmp_path):
        out = fs.merge_close_frame(
            tmp_path / "none.csv",
            pd.DataFrame([("2026-07-30", 1.0)], columns=["date", "close"]),
        )
        assert list(out["date"]) == ["2026-07-30"]

    def test_empty_new_frame_returns_none(self, tmp_path):
        assert fs.merge_close_frame(tmp_path / "none.csv", None) is None
        assert (
            fs.merge_close_frame(
                tmp_path / "none.csv", pd.DataFrame(columns=["date", "close"])
            )
            is None
        )

    def test_corrupt_existing_file_warns_and_quarantines(self, tmp_path, capsys):
        """损坏缓存不得静默丢弃:必须 WARN + 改名隔离(留现场),再只用新数据落盘并提示全量重拉。"""
        dest = tmp_path / "880001.csv"
        dest.write_text("not,a,valid\ncsv", encoding="utf-8")
        out = fs.merge_close_frame(
            dest, pd.DataFrame([("2026-07-30", 1.0)], columns=["date", "close"])
        )
        assert list(out["date"]) == ["2026-07-30"]  # 不 raise,用新数据继续
        err = capsys.readouterr().err
        assert "[WARN]" in err and "损坏" in err and "全量重拉" in err
        quarantined = list(
            tmp_path.glob("880001.csv.corrupt-*")
        )  # 隔离而非覆写,保留现场
        assert len(quarantined) == 1
        assert quarantined[0].read_text(encoding="utf-8") == "not,a,valid\ncsv"
        assert not dest.exists()  # 原路径让位给新数据原子落盘


class TestIncrementalStart:
    def test_uses_last_cached_date_minus_overlap(self, tmp_path):
        dest = tmp_path / "880001.csv"
        pd.DataFrame([("2026-07-30", 1.0)], columns=["date", "close"]).to_csv(
            dest, index=False
        )
        assert fs.incremental_start(dest, "20180101") == "20260630"  # 30 天重叠

    def test_floor_respected(self, tmp_path):
        dest = tmp_path / "880001.csv"
        pd.DataFrame([("2018-01-10", 1.0)], columns=["date", "close"]).to_csv(
            dest, index=False
        )
        assert fs.incremental_start(dest, "20180101") == "20180101"  # 不早于 floor

    def test_missing_or_bad_cache_falls_back_to_floor(self, tmp_path):
        assert fs.incremental_start(tmp_path / "none.csv", "20180101") == "20180101"
        bad = tmp_path / "bad.csv"
        bad.write_text("x", encoding="utf-8")
        assert fs.incremental_start(bad, "20180101") == "20180101"


class TestMainWiring:
    @pytest.fixture(autouse=True)
    def _tq(self, monkeypatch):
        monkeypatch.setattr(fs.tq_sector, "is_tdxw_running", lambda: True)
        # 名称表/tcode 表默认置空:测试不得碰真实 TdxW/E盘文件
        monkeypatch.setattr(fs.tq_sector, "load_sector_names", lambda *a, **k: {})
        monkeypatch.setattr(fs, "load_tdxhy_tcodes", lambda *a, **k: {})

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
        assert (tmp_path / "880001.SH.csv").is_file() and (
            tmp_path / "880002.SH.csv"
        ).is_file()

    def test_unusable_period_fails_fast_without_scanning_all_sectors(
        self, tmp_path, monkeypatch, capsys
    ):
        tq = self._install(
            monkeypatch, _TQ(fail_all=True), sectors=[f"88{i:04d}" for i in range(50)]
        )
        rc = fs.main(["--out", str(tmp_path), "--start", "20180101"])
        assert rc == 2
        assert "无可用周期串" in capsys.readouterr().err
        # 只在第一个板块上探测,不得对 50 个板块各刷一遍
        assert len(tq.refresh_calls) == len(fs.PERIOD_CANDIDATES)

    def test_incremental_merges_and_narrows_request_window(self, tmp_path, monkeypatch):
        dest = tmp_path / "880001.SH.csv"
        pd.DataFrame(
            [("2018-01-02", 90.0), ("2026-07-29", 100.0)], columns=["date", "close"]
        ).to_csv(dest, index=False)
        tq = self._install(monkeypatch, _TQ(good_period="1d"), sectors=("880001",))
        rc = fs.main(["--out", str(tmp_path), "--start", "20180101", "--incremental"])
        assert rc == 0
        got = pd.read_csv(dest, dtype={"date": str})
        assert list(got["date"]) == [
            "2018-01-02",
            "2026-07-29",
            "2026-07-30",
        ]  # 深度保留
        starts = [c["start"] for c in tq.data_calls if c["codes"] == ("880001.SH",)]
        assert starts[-1] == "20260629"  # 请求窗口收窄(不再每天重拉 8 年)

    def test_full_mode_still_overwrites(self, tmp_path, monkeypatch):
        dest = tmp_path / "880001.SH.csv"
        pd.DataFrame([("2018-01-02", 90.0)], columns=["date", "close"]).to_csv(
            dest, index=False
        )
        self._install(monkeypatch, _TQ(good_period="1d"), sectors=("880001",))
        fs.main(["--out", str(tmp_path), "--start", "20180101"])
        got = pd.read_csv(dest, dtype={"date": str})
        assert list(got["date"]) == [
            "2026-07-29",
            "2026-07-30",
        ]  # 全量模式=以 TQ 返回为准

    def test_limit_restricts_sectors(self, tmp_path, monkeypatch):
        self._install(
            monkeypatch, _TQ(good_period="1d"), sectors=("880001", "880002", "880003")
        )
        fs.main(["--out", str(tmp_path), "--limit", "1"])
        assert (tmp_path / "880001.SH.csv").is_file()
        assert not (tmp_path / "880002.SH.csv").exists()


class TestBuildUniverse:
    """宇宙并集:TQ get_sector_list 不含 type 2 行业(实测 587 板块、行业 0 个),
    必须并上名称表 type∈{2,4} 的代码,否则 880431 船舶等 145 个行业板块永远抓不到。"""

    def test_union_includes_tdxzs_industry(self):
        name_map = {
            "880431": {"name": "船舶", "tdx_type": "2", "t_code": "T0702"},
            "880904": {"name": "机器人概念", "tdx_type": "4", "t_code": ""},
            "880201": {"name": "黑龙江", "tdx_type": "3", "t_code": ""},  # 地区不进
            "881002": {"name": "煤炭开采", "tdx_type": "12", "t_code": ""},  # 细分不进
        }
        uni = fs.build_universe(["880001.SH", "880002"], name_map)
        assert "880431" in uni and "880904" in uni  # 行业 + 概念补齐
        assert "880201" not in uni and "881002" not in uni
        assert uni == sorted(set(uni))  # 排序去重(880002 裸码/后缀各一次)

    def test_empty_name_map_degrades_to_tq_list(self):
        assert fs.build_universe(["880001.SH"], {}) == ["880001"]


class TestCodesFilter:
    @pytest.fixture(autouse=True)
    def _tq(self, monkeypatch):
        monkeypatch.setattr(fs.tq_sector, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(fs.tq_sector, "load_sector_names", lambda *a, **k: {})
        monkeypatch.setattr(fs, "load_tdxhy_tcodes", lambda *a, **k: {})

    def test_codes_filters_after_union(self, tmp_path, monkeypatch):
        """--codes 定向回填:并集宇宙里只抓指定板块(裸码/带后缀混写都行)。"""
        monkeypatch.setattr(
            fs.tq_sector,
            "load_sector_names",
            lambda *a, **k: {
                "880431": {"name": "船舶", "tdx_type": "2", "t_code": "T0702"}
            },
        )
        tq = _TQ(good_period="1d")
        tq.initialize = lambda *a, **k: None
        tq.close = lambda: None
        tq.get_sector_list = lambda: ["880001", "880002"]
        monkeypatch.setattr(fs.tq_sector, "_import_tq", lambda: tq)
        rc = fs.main(
            [
                "--out",
                str(tmp_path),
                "--start",
                "20180101",
                "--codes",
                "880431.SH,880002",
            ]
        )
        assert rc == 0
        assert (tmp_path / "880431.SH.csv").is_file()  # 并集里的行业板块被选中
        assert (tmp_path / "880002.SH.csv").is_file()
        assert not (tmp_path / "880001.SH.csv").exists()


class TestMembersMergeWrite:
    @pytest.fixture(autouse=True)
    def _tq(self, monkeypatch):
        monkeypatch.setattr(fs.tq_sector, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(fs.tq_sector, "load_sector_names", lambda *a, **k: {})
        monkeypatch.setattr(fs, "load_tdxhy_tcodes", lambda *a, **k: {})

    def test_merge_write_preserves_existing_keys(self, tmp_path, monkeypatch):
        """子集运行(--codes/--limit)不得冲掉既有 587 个键:合并覆盖同名键后落盘。"""
        import json

        outdir = tmp_path / "sector_index"
        (tmp_path / "sector_members.json").write_text(
            json.dumps({"880001.SH": ["600000"], "880002.SH": ["600099"]}),
            encoding="utf-8",
        )
        tq = _TQ(good_period="1d")
        tq.initialize = lambda *a, **k: None
        tq.close = lambda: None
        tq.get_sector_list = lambda: ["880002"]
        tq.get_stock_list_in_sector = lambda code: ["600001.SH", "600002.SZ"]
        monkeypatch.setattr(fs.tq_sector, "_import_tq", lambda: tq)
        rc = fs.main(["--out", str(outdir), "--start", "20180101", "--members"])
        assert rc == 0
        merged = json.loads(
            (tmp_path / "sector_members.json").read_text(encoding="utf-8")
        )
        assert merged["880001.SH"] == ["600000"]  # 旧键保留
        assert merged["880002.SH"] == ["600001", "600002"]  # 同名键被本次覆盖
        assert len(merged) == 2


class TestDeriveLocalMembers:
    """T-code 前缀推导(纯函数):T0702 收 T0702 与 T070201(树形后代),不收 T0703。"""

    NAME_MAP = {
        "880431": {"name": "船舶", "tdx_type": "2", "t_code": "T0702"},
        "880904": {
            "name": "机器人概念",
            "tdx_type": "4",
            "t_code": "智能机器",
        },  # 非 T-code
        "880201": {"name": "黑龙江", "tdx_type": "3", "t_code": "1"},  # 非 T-code
    }
    TDXHY = {
        "600150": "T0702",  # 等于 → 收
        "600151": "T070201",  # 前缀后代 → 收
        "600152": "T0703",  # 兄弟 → 不收
        "600153": "",  # 无 T-code → 不收
    }

    def test_prefix_tree_semantics(self):
        got = fs.derive_local_members(self.NAME_MAP, self.TDXHY)
        assert got["880431"] == ["600150", "600151"]
        assert "880904" not in got and "880201" not in got

    def test_tq_empty_falls_back_to_local_and_tq_wins_when_present(self):
        """TQ 对行业返回空 → 本地推导兜底;TQ 有结果时一律 TQ 优先。键保持带后缀约定。"""
        local = fs.derive_local_members(self.NAME_MAP, self.TDXHY)

        class _T:
            def __init__(self, ret):
                self.ret = ret

            def get_stock_list_in_sector(self, code):
                assert code == "880431.SH"  # 调 TQ 用带后缀码
                return self.ret

        members: dict = {}
        fs._fetch_members(_T([]), "880431", members, local)
        assert members == {"880431.SH": ["600150", "600151"]}  # 空 → 本地兜底
        fs._fetch_members(_T(["600999.SH"]), "880431", members, local)
        assert members["880431.SH"] == ["600999"]  # 非空 → TQ 优先

    def test_tq_exception_keeps_existing_key(self):
        class _T:
            def get_stock_list_in_sector(self, code):
                raise RuntimeError("rpc timeout")

        members = {"880431.SH": ["600150"]}
        fs._fetch_members(_T(), "880431", members, {})
        assert members["880431.SH"] == ["600150"]  # 失败不覆写既有键
