# -*- coding: utf-8 -*-
"""审计【建议优化】批次回归测试。

主题是**降级信息不传导**:采集/计算失败时下游读不到失败标记,于是"缺数"被当成 0 或
被当成成功。每个 class 对应审计清单里的一条,注释写清"修前会怎样错"。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent


# ======================================================== 1. calc_mfe_mae 字段名

class TestCalcMfeMaeFieldNames:
    """异常/无数据路径必须写 `mfe_pct`/`mae_pct`(下游读的键),不能写 `mfe`/`mae`。

    修前:`final_close_review` 读 `mfe_map[code]["mfe_pct"]`、`weekly_review.load_mfe_after`
    判 `entry.get("mfe_pct") is None`,而异常路径落的是 `{"mfe": None, "mae": None}` ——
    键名不一致,下游既读不到值也读不到失败原因,只能当"无该代码"静默略过。
    """

    def _positions(self, tmp_path: Path) -> Path:
        path = tmp_path / "01_data" / "trades" / "current_positions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([{"代码": "600000", "名称": "测试股", "单位成本": 10.0,
                                     "持有数量": 1000}], ensure_ascii=False), encoding="utf-8")
        return path

    def _run(self, tmp_path, monkeypatch, reader_daily):
        import calc_mfe_mae as cm

        pos = self._positions(tmp_path)
        monkeypatch.setattr(cm, "BASE", tmp_path)
        monkeypatch.setattr(cm, "POSITIONS", pos)
        monkeypatch.setattr(cm, "load_entry_dates",
                            lambda *a, **k: {"600000": {"entry_date": "2026-06-10"}})

        class _Reader:
            @staticmethod
            def factory(**kwargs):
                return _Reader()

            def daily(self, symbol=""):
                return reader_daily()

        monkeypatch.setitem(sys.modules, "mootdx.reader",
                            type(sys)("mootdx.reader"))
        sys.modules["mootdx.reader"].Reader = _Reader

        class _Quotes:                       # 兜底路径也必须离线,测试不得发真实网络请求
            @staticmethod
            def factory(**kwargs):
                return _Quotes()

            def bars(self, **kwargs):
                return None

        monkeypatch.setitem(sys.modules, "mootdx.quotes", type(sys)("mootdx.quotes"))
        sys.modules["mootdx.quotes"].Quotes = _Quotes
        rc = cm.main(["--date", "2026-06-11"])
        out = json.loads((tmp_path / "01_data" / "holdings" /
                          "2026-06-11_mfe_mae.json").read_text(encoding="utf-8"))
        return rc, out["holdings"][0]

    def test_exception_path_uses_pct_keys(self, tmp_path, monkeypatch):
        def _boom():
            raise RuntimeError("reader boom")

        rc, row = self._run(tmp_path, monkeypatch, _boom)
        assert set(row) >= {"code", "mfe_pct", "mae_pct", "unable_reason"}
        assert "mfe" not in row and "mae" not in row      # 旧键名不得再出现
        assert row["mfe_pct"] is None and row["mae_pct"] is None
        assert "reader boom" in row["unable_reason"]
        assert rc != 0                                    # 0/1 出数不得报成功

    def test_no_data_path_uses_pct_keys(self, tmp_path, monkeypatch):
        import pandas as pd

        rc, row = self._run(tmp_path, monkeypatch, lambda: pd.DataFrame())
        assert "mfe" not in row and "mae" not in row
        assert row["mfe_pct"] is None and row["unable_reason"]
        assert rc != 0

    def test_success_path_returns_zero_and_pct_keys(self, tmp_path, monkeypatch):
        import pandas as pd

        bars = pd.DataFrame({"high": [12.0, 13.0], "low": [9.5, 9.8]},
                            index=pd.to_datetime(["2026-06-10", "2026-06-11"]))
        rc, row = self._run(tmp_path, monkeypatch, lambda: bars)
        assert rc == 0
        assert row["mfe_pct"] == 30.0 and row["mae_pct"] == -5.0


class TestCalcMfeMaeCoverage:
    """出数覆盖率必须落盘 + 决定退出码,run_1700 才可能不报 [OK]。"""

    def test_coverage_summary_keys(self):
        import calc_mfe_mae as cm

        rows = [{"code": "1", "mfe_pct": 1.0}, {"code": "2", "mfe_pct": None},
                {"code": "3", "mfe_pct": None}]
        cov = cm.coverage_summary(rows)
        assert cov["total"] == 3 and cov["valued"] == 1 and cov["unable"] == 2
        assert cov["coverage_pct"] == pytest.approx(33.33, abs=0.01)
        assert cov["status"] == "degraded"

    def test_zero_coverage_is_failed(self):
        import calc_mfe_mae as cm

        cov = cm.coverage_summary([{"code": "1", "mfe_pct": None}])
        assert cov["status"] == "failed"

    def test_empty_positions_is_complete(self):
        import calc_mfe_mae as cm

        assert cm.coverage_summary([])["status"] == "complete"


class TestRun1700MfeStageEcho:
    """run_1700 不得对降级的 MFE/MAE 报 [OK]。

    修前:`print("[OK] MFE/MAE calculated")` 只看子进程退出码,而脚本无论出数 0/5 还是
    5/5 都退 0 ⇒ 盘后链输出里"全员没出数"和"全部出数"完全一样。
    """

    def _run(self, tmp_path, monkeypatch, mfe_out, mfe_ok):
        import run_1700

        review_dir = tmp_path / "04_reviews" / "daily"
        review_dir.mkdir(parents=True)
        (review_dir / "2026-07-17_final_review.md").write_text("# 复盘\n正文\n", encoding="utf-8")
        monkeypatch.setattr(run_1700, "REVIEWS", review_dir)
        monkeypatch.setattr(run_1700, "LOG_DIR", tmp_path / "06_logs")
        monkeypatch.setattr(run_1700, "check_trading_day", lambda d: {"is_trading_day": True})
        monkeypatch.setattr(run_1700.os, "chdir", lambda p: None)

        def _fake_stage(cmd, name):
            if name == "calc_mfe_mae":
                return {"ok": mfe_ok, "returncode": 0 if mfe_ok else 2, "timeout": False,
                        "stdout": mfe_out, "stderr": "", "out": mfe_out}
            return {"ok": True, "returncode": 0, "timeout": False,
                    "stdout": "", "stderr": "", "out": ""}

        monkeypatch.setattr(run_1700, "_stage", _fake_stage)
        rc = run_1700.main(["--date", "2026-07-17"])
        return rc

    def test_zero_coverage_not_reported_as_ok(self, tmp_path, monkeypatch, capsys):
        out_text = ("[WARN] 600000 A: 台账无未平仓记录，跳过\n"
                    "[WARN] MFE/MAE 0/3 出数(failed，未出数 600000) -> 2026-07-17_mfe_mae.json")
        rc = self._run(tmp_path, monkeypatch, out_text, mfe_ok=False)
        printed = capsys.readouterr().out
        assert rc == 0                               # best-effort stage 不中断整条链
        assert "[OK] MFE/MAE calculated" not in printed
        assert "0/3 出数" in printed and "[WARN]" in printed

    def test_degraded_summary_echoed(self, tmp_path, monkeypatch, capsys):
        out_text = "[WARN] MFE/MAE 2/3 出数(degraded，未出数 600001) -> f.json"
        self._run(tmp_path, monkeypatch, out_text, mfe_ok=True)
        printed = capsys.readouterr().out
        assert "2/3 出数" in printed
        assert "[OK] MFE/MAE calculated" not in printed

    def test_full_coverage_reported_ok(self, tmp_path, monkeypatch, capsys):
        self._run(tmp_path, monkeypatch, "[OK] MFE/MAE 3/3 出数(complete) -> f.json", mfe_ok=True)
        printed = capsys.readouterr().out
        assert "[OK] MFE/MAE 3/3 出数" in printed


# =================================== 2. fetch_sector_index_history 落盘/退出码

class TestSectorIndexAtomicWrite:
    """临时文件名必须由 dest 派生。

    修前:`tmp = outdir / f"{code}.csv.tmp"`(未加市场后缀)而 `dest = outdir / f"{code}.SH.csv"`,
    中断时留下的 `880001.csv.tmp` 与目标文件不同名 —— 下次运行不会覆盖它,按 dest 名做的
    清理也找不到它,只能在缓存目录里越积越多。
    """

    def test_tmp_name_derived_from_dest(self, tmp_path):
        from local_tdx import fetch_sector_index_history as fs

        seen = {}

        class _Frame:
            def to_csv(self, path, index=False):
                seen["path"] = Path(path)
                Path(path).write_text("date,close\n", encoding="utf-8")

        dest = tmp_path / "880001.SH.csv"
        fs.atomic_write_csv(_Frame(), dest)
        assert seen["path"].name == "880001.SH.csv.tmp"
        assert dest.is_file() and not seen["path"].exists()

    def test_crash_leaves_tmp_next_to_dest(self, tmp_path):
        from local_tdx import fetch_sector_index_history as fs

        class _Frame:
            def to_csv(self, path, index=False):
                Path(path).write_text("partial", encoding="utf-8")
                raise OSError("disk full")

        dest = tmp_path / "880001.SH.csv"
        with pytest.raises(OSError):
            fs.atomic_write_csv(_Frame(), dest)
        leftovers = [p.name for p in tmp_path.glob("*.tmp")]
        assert leftovers == ["880001.SH.csv.tmp"]      # 与 dest 同名前缀，可被发现/覆盖
        assert not dest.exists()                       # 残片不得冒充成品


class TestSectorIndexSuccessRate:
    """3/430 成功不得 exit 0,且成功率必须落盘。

    修前:`return 0 if ok else 2` —— 只要有一个板块成功就报成功,430 个里失败 427 个
    在退出码上完全看不出来,run_1800 照打 [OK]。
    """

    def _install(self, monkeypatch, tq, sectors):
        from local_tdx import fetch_sector_index_history as fs

        monkeypatch.setattr(fs.tq_sector, "is_tdxw_running", lambda: True)
        tq.initialize = lambda *a, **k: None
        tq.close = lambda: None
        tq.get_sector_list = lambda: list(sectors)
        monkeypatch.setattr(fs.tq_sector, "_import_tq", lambda: tq)
        return fs

    def test_low_success_rate_exits_nonzero_and_writes_status(self, tmp_path, monkeypatch, capsys):
        import pandas as pd
        from helpers_sector_tq import PartialTQ

        fs = self._install(monkeypatch, PartialTQ(ok_codes={"880001.SH"}),
                           [f"88{i:04d}" for i in range(1, 11)])
        rc = fs.main(["--out", str(tmp_path), "--start", "20180101"])
        status = json.loads((tmp_path / "_fetch_status.json").read_text(encoding="utf-8"))
        assert rc != 0
        assert status["ok"] == 1 and status["total"] == 10
        assert status["success_rate"] == pytest.approx(0.1)
        assert status["status"] == "degraded"
        assert "880002.SH" in status["failed_codes"]
        tail = capsys.readouterr().out.strip().splitlines()[-1]
        assert tail.startswith("[WARN]") and "1/10" in tail
        assert isinstance(pd.read_csv(tmp_path / "880001.SH.csv"), pd.DataFrame)

    def test_full_success_exits_zero_with_ok_status(self, tmp_path, monkeypatch, capsys):
        from helpers_sector_tq import PartialTQ

        fs = self._install(monkeypatch, PartialTQ(ok_codes=None), ("880001", "880002"))
        rc = fs.main(["--out", str(tmp_path), "--start", "20180101"])
        status = json.loads((tmp_path / "_fetch_status.json").read_text(encoding="utf-8"))
        assert rc == 0 and status["status"] == "ok" and status["ok"] == 2
        assert capsys.readouterr().out.strip().splitlines()[-1].startswith("[OK]")

    def test_threshold_is_configurable(self, tmp_path, monkeypatch):
        from helpers_sector_tq import PartialTQ

        fs = self._install(monkeypatch, PartialTQ(ok_codes={"880001.SH"}), ("880001", "880002"))
        assert fs.main(["--out", str(tmp_path), "--min-success-rate", "0.5"]) == 0
        assert fs.main(["--out", str(tmp_path), "--min-success-rate", "0.9"]) != 0


# ============================================ 3. tq_sector 成功率门槛 / 限速 / 进度

class _FakeTQ:
    """板块列表可控、成分股按代码成功或失败的 TQ 替身。"""

    def __init__(self, sectors, ok_codes=None):
        self.sectors = list(sectors)
        self.ok = ok_codes
        self.calls: list[str] = []

    def initialize(self, *a, **k):
        return None

    def close(self):
        return None

    def get_sector_list(self):
        return list(self.sectors)

    def get_stock_list_in_sector(self, code):
        self.calls.append(code)
        if self.ok is not None and code not in self.ok:
            raise RuntimeError(f"sector failed: {code}")
        return ["600000"]


def _session(monkeypatch, tq, name_map=None):
    import tq_sector as ts

    monkeypatch.setattr(ts, "is_tdxw_running", lambda: True)
    monkeypatch.setattr(ts, "_import_tq", lambda: tq)
    sess = ts.TQSectorSession(name_map=name_map if name_map is not None else {})
    return ts, sess


class TestTqSectorSuccessRate:
    """400+ 板块全部取不到成分股时 `stock_total=0` 却 exit 0 —— 下游按空映射跑。

    修前:`main` 只在**顶层** error 时返回 1;单板块失败进 errors 列表，430 个全失败
    也照样 exit 0，board/sector 映射为空却被当成"采集成功"。
    """

    def test_quality_reports_sector_success_rate(self, monkeypatch):
        tq = _FakeTQ([f"88{i:04d}" for i in range(1, 11)], ok_codes={"880001"})
        _ts, sess = _session(monkeypatch, tq)
        result = sess.build_sector_map()
        q = result["quality"]
        assert q["sector_success"] == 1 and q["sector_failed"] == 9
        assert q["sector_success_rate"] == pytest.approx(0.1)
        assert result["stock_total"] == 1

    def test_main_exits_nonzero_on_low_success_rate(self, monkeypatch, tmp_path):
        import tq_sector as ts

        tq = _FakeTQ([f"88{i:04d}" for i in range(1, 11)], ok_codes={"880001"})
        monkeypatch.setattr(ts, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(ts, "_import_tq", lambda: tq)
        monkeypatch.setattr(ts, "SECTORS_DIR", tmp_path)
        monkeypatch.setattr(ts, "load_sector_names", lambda *a, **k: {})
        rc = ts.main(["--date", "2026-07-17"])
        assert rc != 0
        saved = json.loads((tmp_path / "2026-07-17_tq_sector_map.json").read_text(encoding="utf-8"))
        assert saved["quality"]["sector_success_rate"] == pytest.approx(0.1)

    def test_main_exits_zero_when_all_sectors_ok(self, monkeypatch, tmp_path):
        import tq_sector as ts

        tq = _FakeTQ(["880001", "880002"])
        monkeypatch.setattr(ts, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(ts, "_import_tq", lambda: tq)
        monkeypatch.setattr(ts, "SECTORS_DIR", tmp_path)
        monkeypatch.setattr(ts, "load_sector_names", lambda *a, **k: {})
        assert ts.main(["--date", "2026-07-17"]) == 0

    def test_zero_stock_total_is_not_success(self, monkeypatch, tmp_path):
        """成分股全空(每个板块返回 [] 而不报错)也不算成功:映射空却 exit 0 是原缺陷。"""
        import tq_sector as ts

        class _EmptyTQ(_FakeTQ):
            def get_stock_list_in_sector(self, code):
                return []

        tq = _EmptyTQ(["880001", "880002"])
        monkeypatch.setattr(ts, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(ts, "_import_tq", lambda: tq)
        monkeypatch.setattr(ts, "SECTORS_DIR", tmp_path)
        monkeypatch.setattr(ts, "load_sector_names", lambda *a, **k: {})
        assert ts.main(["--date", "2026-07-17"]) != 0


class TestTqSectorRateLimitAndProgress:
    """串行保留，但要限速 + 有进度(400+ 次请求不能一口气打满 TdxW 且静默 5 分钟)。"""

    def test_sleep_between_sectors(self, monkeypatch):
        import tq_sector as ts

        slept: list[float] = []
        tq = _FakeTQ(["880001", "880002", "880003"])
        _ts, sess = _session(monkeypatch, tq)
        monkeypatch.setattr(ts.time, "sleep", lambda s: slept.append(s))
        sess.build_sector_map(sleep_ms=20)
        assert len(slept) == 3 and all(s == pytest.approx(0.02) for s in slept)

    def test_no_sleep_by_default(self, monkeypatch):
        import tq_sector as ts

        slept: list[float] = []
        tq = _FakeTQ(["880001", "880002"])
        _ts, sess = _session(monkeypatch, tq)
        monkeypatch.setattr(ts.time, "sleep", lambda s: slept.append(s))
        sess.build_sector_map()
        assert slept == []

    def test_progress_reports_failures(self, monkeypatch, capsys):
        tq = _FakeTQ([f"88{i:04d}" for i in range(1, 4)], ok_codes={"880001"})
        _ts, sess = _session(monkeypatch, tq)
        sess.build_sector_map(progress=True, progress_every=1)
        printed = capsys.readouterr().out
        assert "3/3" in printed and "失败" in printed


# ================================================ 4. Affair 财务缓存目录与失败处理

class TestAffairCache:
    """缓存必须落在**项目内**,且 fetch 失败/期号取不到时不得抛裸异常。

    修前:`download_dir = BASE / ".." / "tdx_affair_cache"` —— 写到项目**外面**的兄弟目录,
    既不在 .gitignore 覆盖范围内也不随项目迁移;`report_period` 取不到时会去 fetch
    `gpcw.zip`,Affair.fetch/parse 的网络异常直接冒泡打断调用方。
    """

    def _stub_affair(self, monkeypatch, files=None, fetch_exc=None, parse_ret=None):
        import local_tdx_data as ltd

        calls = {}

        class _Affair:
            @staticmethod
            def files():
                if isinstance(files, Exception):
                    raise files
                return files or []

            @staticmethod
            def fetch(downdir=None, filename=None):
                calls["downdir"] = downdir
                calls["filename"] = filename
                if fetch_exc is not None:
                    raise fetch_exc

            @staticmethod
            def parse(downdir=None, filename=None):
                return parse_ret

        mod = type(sys)("mootdx.affair")
        mod.Affair = _Affair
        monkeypatch.setitem(sys.modules, "mootdx.affair", mod)
        ltd._financial_cache.clear()
        return ltd, calls

    def test_cache_dir_inside_project(self, monkeypatch):
        import pandas as pd

        ltd, calls = self._stub_affair(monkeypatch, parse_ret=pd.DataFrame({"a": [1]}))
        df = ltd.get_financial_data("20260331")
        assert not df.empty
        downdir = Path(calls["downdir"])
        # 不 resolve：01_data 在部分环境是指向项目外盘符的符号链接,resolve 后会"跑出"
        # 项目,但**配置路径**本身仍在项目内——本测试钉的是配置不写到 BASE/.. 项目外。
        assert downdir.is_relative_to(ltd.BASE)
        assert ".." not in downdir.parts

    def test_empty_report_period_returns_empty_not_fetch(self, monkeypatch, capsys):
        ltd, calls = self._stub_affair(monkeypatch, files=[])
        df = ltd.get_financial_data()
        assert df.empty
        assert "filename" not in calls                          # 不得去 fetch gpcw.zip
        assert "WARN" in capsys.readouterr().err

    def test_fetch_failure_degrades_to_empty_frame(self, monkeypatch, capsys):
        ltd, _calls = self._stub_affair(monkeypatch, fetch_exc=OSError("network down"))
        df = ltd.get_financial_data("20260331")
        assert df.empty                                         # 降级而不是抛裸异常
        assert "network down" in capsys.readouterr().err

    def test_files_listing_failure_degrades(self, monkeypatch):
        ltd, _calls = self._stub_affair(monkeypatch, files=RuntimeError("list failed"))
        assert ltd.get_financial_data().empty

    def test_successful_result_cached(self, monkeypatch):
        import pandas as pd

        ltd, calls = self._stub_affair(monkeypatch, parse_ret=pd.DataFrame({"a": [1]}))
        ltd.get_financial_data("20260331")
        calls.clear()
        ltd.get_financial_data("20260331")
        assert calls == {}                                      # 第二次命中缓存，不再 fetch


# ============================================================ 5. 继承报价标记

class TestInheritedSnapshotMarkers:
    """继承的持仓基线必须把 `inherited` / `inherited_from` 一路带到报告层。

    生产者(`runtime_guards.position_snapshot_freshness`)已写这两个标记,
    `review_core.snapshot_state` 此前只透 `inherited_from`,丢了布尔标记本身。
    """

    def test_snapshot_state_propagates_inheritance(self, tmp_path, monkeypatch):
        from close_review import review_core as rc

        gate = {"position_freshness": {"status": "confirmed", "inherited": True,
                                       "inherited_from": "2026-07-16",
                                       "reason": "沿用 2026-07-16 收盘持仓",
                                       "assumption": "盘中默认不交易"}}
        (tmp_path / "2026-07-17_runtime_gate.json").write_text(
            json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(rc, "QUALITY", tmp_path)
        snap = rc.snapshot_state("2026-07-17")
        assert snap["inherited"] is True
        assert snap["inherited_from"] == "2026-07-16"
        assert "2026-07-16" in snap["reason"]

    def test_missing_gate_reports_not_inherited(self, tmp_path, monkeypatch):
        from close_review import review_core as rc

        monkeypatch.setattr(rc, "QUALITY", tmp_path)
        snap = rc.snapshot_state("2026-07-17")
        assert snap["inherited"] is None and snap["status"] == "未知"


# ================================================== 6. 净盈亏口径:只扣已平仓的费用

WEEK_CAL = {"official_years": {"2026": {"closed_ranges": [
    {"name": "国庆", "start": "2026-10-01", "end": "2026-10-07"}]}}}


def _write_ledger(base: Path, rows: list[dict]) -> None:
    header = ("成交日期,成交时间,代码,名称,交易类别,成交数量,成交价格,成交金额,发生金额,费用,备注")
    lines = [header]
    for r in rows:
        lines.append(",".join([
            r["date"], r.get("time", "09:30:00"), r["code"], r.get("name", "测试股"),
            r["side"], str(r["qty"]), str(r["price"]), str(r["qty"] * r["price"]),
            str(r["qty"] * r["price"]), str(r["fee"]), ""]))
    path = base / "01_data" / "trades" / "master_trade_ledger.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\ufeff" + "\n".join(lines), encoding="utf-8")


def _week_base(tmp_path: Path) -> Path:
    p = tmp_path / "00_governance" / "CN_TRADING_CALENDAR.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(WEEK_CAL, ensure_ascii=False), encoding="utf-8")
    return tmp_path


class TestRealizedPnlFeeScope:
    """已实现净盈亏只能扣**已平仓对应**的费用。

    数值用例(2026-07-13 ~ 2026-07-17 这一周):
      买 600000 1000股@10.00 费 5.00 → 卖 600000 1000股@11.00 费 8.00 (本周平仓)
      买 000001 2000股@20.00 费 30.00 (本周新开仓，**没卖**)
    毛盈亏 = 11000 - 10000 = +1000.00
      修前 net = 1000 - (5 + 8 + 30) = 957.00   ← 把未平仓的 30 元买单费用扣进已实现
      修后 net = 1000 - (5 + 8)      = 987.00
    """

    def _facts(self, tmp_path):
        from close_review import weekly_review as wr

        base = _week_base(tmp_path)
        _write_ledger(base, [
            {"date": "2026-07-13", "code": "600000", "side": "买入", "qty": 1000,
             "price": 10.0, "fee": 5.0},
            {"date": "2026-07-14", "code": "600000", "side": "卖出", "qty": 1000,
             "price": 11.0, "fee": 8.0},
            {"date": "2026-07-15", "code": "000001", "side": "买入", "qty": 2000,
             "price": 20.0, "fee": 30.0},
        ])
        return wr.build_weekly_review(base, "2026-07-17")["facts"]

    def test_open_position_buy_fee_not_deducted(self, tmp_path):
        f = self._facts(tmp_path)
        assert f["gross_pnl"] == 1000.0
        assert f["closed_fee_total"] == 13.0        # 5(已配平买单) + 8(卖单)
        assert f["net_pnl"] == 987.0               # 修前是 957.0
        assert f["fee_total"] == 43.0              # 本周全部费用仍如实披露

    def test_fee_breakdown_keys_present(self, tmp_path):
        f = self._facts(tmp_path)
        assert set(f) >= {"fee_total", "buy_fee_total", "sell_fee_total",
                          "closed_fee_total", "matched_buy_fee_total", "net_pnl"}
        assert f["buy_fee_total"] == 35.0 and f["sell_fee_total"] == 8.0

    def test_partial_lot_buy_fee_prorated(self, tmp_path):
        """只卖掉一半时，只有那一半对应的买单费用能进已实现盈亏。"""
        from close_review import weekly_review as wr

        base = _week_base(tmp_path)
        _write_ledger(base, [
            {"date": "2026-07-13", "code": "600000", "side": "买入", "qty": 1000,
             "price": 10.0, "fee": 10.0},
            {"date": "2026-07-14", "code": "600000", "side": "卖出", "qty": 400,
             "price": 11.0, "fee": 4.0},
        ])
        f = wr.build_weekly_review(base, "2026-07-17")["facts"]
        assert f["gross_pnl"] == 400.0              # 400 股 × 1 元
        assert f["matched_buy_fee_total"] == 4.0    # 10 元 × 400/1000
        assert f["closed_fee_total"] == 8.0         # 4(买) + 4(卖)
        assert f["net_pnl"] == 392.0

    def test_carried_over_buy_fee_counts_when_closed_this_week(self, tmp_path):
        """上周买、本周卖:该买单费用属于这笔已平仓交易，必须扣(即便不在本周费用里)。"""
        from close_review import weekly_review as wr

        base = _week_base(tmp_path)
        _write_ledger(base, [
            {"date": "2026-07-06", "code": "600000", "side": "买入", "qty": 1000,
             "price": 10.0, "fee": 6.0},
            {"date": "2026-07-14", "code": "600000", "side": "卖出", "qty": 1000,
             "price": 11.0, "fee": 9.0},
        ])
        f = wr.build_weekly_review(base, "2026-07-17")["facts"]
        assert f["fee_total"] == 9.0                # 本周只有卖单费用
        assert f["closed_fee_total"] == 15.0        # 6(上周买单，已平仓) + 9(卖单)
        assert f["net_pnl"] == 985.0

    def test_closing_row_exposes_fee_fields(self, tmp_path):
        from close_review import weekly_review as wr

        rows = wr.fifo_pair([
            {"date": "2026-07-13", "time": "09:30", "code": "600000", "name": "A",
             "side": "买入", "qty": 1000, "price": 10.0, "amount": 10000.0, "fee": 10.0},
            {"date": "2026-07-14", "time": "09:30", "code": "600000", "name": "A",
             "side": "卖出", "qty": 1000, "price": 11.0, "amount": 11000.0, "fee": 8.0},
        ])
        c = rows[0]
        assert set(c) >= {"gross_pnl", "sell_fee", "matched_buy_fee", "net_pnl"}
        assert c["matched_buy_fee"] == 10.0 and c["sell_fee"] == 8.0
        assert c["net_pnl"] == 982.0


# ====================================== 7. 增量新建持仓行缺字段：不得把缺失当 0

class TestPendingPositionMissingFields:
    """`incremental_ledger.compute_positions` 新建的持仓行只有
    代码/名称/持有数量/单位成本(+snapshot_status/note),缺 5 个券商快照字段:
    最新价、持有盈亏、持有盈亏率、仓位占比、持仓天数。

    受影响的下游是 `calc_mfe_mae`:`float(pos.get("最新价", 0))`、
    `float(pos.get("持有盈亏率", 0))*100`、`int(pos.get("持仓天数", 0))` ——
    "尚未按收盘价重估"被写成"现价 0 元 / 浮盈 0% / 持仓 0 天",复盘里看不出是缺数。
    """

    def test_incremental_row_fields_are_none_not_zero(self, tmp_path, monkeypatch):
        import pandas as pd

        import calc_mfe_mae as cm

        # incremental_ledger.compute_positions 的真实输出形态
        row = {"代码": "600000", "名称": "测试股", "持有数量": 1000.0, "单位成本": 10.0,
               "snapshot_status": "pending_close_revaluation",
               "snapshot_note": "数量/成本已按增量成交更新；市值、盈亏、仓位须用最新收盘价重估"}
        pos_path = tmp_path / "01_data" / "trades" / "current_positions.json"
        pos_path.parent.mkdir(parents=True, exist_ok=True)
        pos_path.write_text(json.dumps([row], ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(cm, "BASE", tmp_path)
        monkeypatch.setattr(cm, "POSITIONS", pos_path)
        monkeypatch.setattr(cm, "load_entry_dates",
                            lambda *a, **k: {"600000": {"entry_date": "2026-06-10"}})
        bars = pd.DataFrame({"high": [12.0], "low": [9.5]},
                            index=pd.to_datetime(["2026-06-10"]))

        class _Reader:
            @staticmethod
            def factory(**kwargs):
                return _Reader()

            def daily(self, symbol=""):
                return bars

        monkeypatch.setitem(sys.modules, "mootdx.reader", type(sys)("mootdx.reader"))
        sys.modules["mootdx.reader"].Reader = _Reader
        cm.main(["--date", "2026-06-11"])
        out = json.loads((tmp_path / "01_data" / "holdings" /
                          "2026-06-11_mfe_mae.json").read_text(encoding="utf-8"))
        got = out["holdings"][0]
        assert got["mfe_pct"] == 20.0                     # 有成本，MFE/MAE 照算
        assert got["current_price"] is None               # 修前是 0.0
        assert got["current_pnl_pct"] is None             # 修前是 0.0
        assert got["hold_days"] is None                   # 修前是 0
        assert got["snapshot_status"] == "pending_close_revaluation"   # 待重估状态透传

    def test_optional_float_rejects_missing_and_garbage(self):
        import calc_mfe_mae as cm

        assert cm.optional_float({}, "最新价") is None
        assert cm.optional_float({"最新价": ""}, "最新价") is None
        assert cm.optional_float({"最新价": "n/a"}, "最新价") is None
        assert cm.optional_float({"最新价": "12.5"}, "最新价") == 12.5

    def test_missing_cost_does_not_produce_zero_percentages(self, tmp_path, monkeypatch):
        """单位成本缺失时不得落 0%/-100%,必须 unable_reason。"""
        import pandas as pd

        import calc_mfe_mae as cm

        pos_path = tmp_path / "01_data" / "trades" / "current_positions.json"
        pos_path.parent.mkdir(parents=True, exist_ok=True)
        pos_path.write_text(json.dumps([{"代码": "600000", "名称": "A", "持有数量": 100}],
                                       ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(cm, "BASE", tmp_path)
        monkeypatch.setattr(cm, "POSITIONS", pos_path)
        monkeypatch.setattr(cm, "load_entry_dates",
                            lambda *a, **k: {"600000": {"entry_date": "2026-06-10"}})
        bars = pd.DataFrame({"high": [12.0], "low": [9.5]},
                            index=pd.to_datetime(["2026-06-10"]))

        class _Reader:
            @staticmethod
            def factory(**kwargs):
                return _Reader()

            def daily(self, symbol=""):
                return bars

        monkeypatch.setitem(sys.modules, "mootdx.reader", type(sys)("mootdx.reader"))
        sys.modules["mootdx.reader"].Reader = _Reader
        cm.main(["--date", "2026-06-11"])
        got = json.loads((tmp_path / "01_data" / "holdings" /
                          "2026-06-11_mfe_mae.json").read_text(encoding="utf-8"))["holdings"][0]
        assert got["mfe_pct"] is None and "单位成本" in got["unable_reason"]


# ============================================ 8. 卖飞审计覆盖率必须显式报告

class TestSellFlyCoverage:
    """全清仓单在后续 mfe_mae.json 里不存在 ⇒ 永远"无法评估"。

    修前报告只写"卖飞候选 0 单；无法评估 N 单",facts 里没有覆盖率、`unavailable`
    里也没有一条 —— 读者会把"没查"当成"没卖飞"。
    """

    def _base_with_closings(self, tmp_path, mfe_holdings):
        from close_review import weekly_review as wr

        base = _week_base(tmp_path)
        _write_ledger(base, [
            {"date": "2026-07-06", "code": "600000", "side": "买入", "qty": 1000,
             "price": 10.0, "fee": 1.0},
            {"date": "2026-07-14", "code": "600000", "side": "卖出", "qty": 1000,
             "price": 11.0, "fee": 1.0},
            {"date": "2026-07-06", "code": "000001", "side": "买入", "qty": 1000,
             "price": 20.0, "fee": 1.0},
            {"date": "2026-07-15", "code": "000001", "side": "卖出", "qty": 1000,
             "price": 21.0, "fee": 1.0},
        ])
        p = base / "01_data" / "holdings" / "2026-07-16_mfe_mae.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"date": "2026-07-16", "holdings": mfe_holdings},
                                ensure_ascii=False), encoding="utf-8")
        return wr, base

    def test_all_closed_positions_report_zero_coverage(self, tmp_path):
        wr, base = self._base_with_closings(tmp_path, [])       # 两只都清仓 → mfe 文件里没有
        review = wr.build_weekly_review(base, "2026-07-17")
        f = review["facts"]
        assert f["closing_count"] == 2
        assert f["sell_fly_evaluated_count"] == 0
        assert f["sell_fly_unevaluated_count"] == 2
        assert f["sell_fly_coverage_pct"] == 0.0
        assert any("卖飞审计覆盖 0/2" in u for u in review["unavailable"])
        assert all("仍持仓" in u["reason"] for u in f["sell_fly_unevaluated"])

    def test_partial_coverage_reported(self, tmp_path):
        wr, base = self._base_with_closings(tmp_path, [
            {"code": "600000", "cost": 10.0, "mfe_pct": 30.0, "mfe_date": "2026-07-15"}])
        f = wr.build_weekly_review(base, "2026-07-17")["facts"]
        assert f["sell_fly_evaluated_count"] == 1
        assert f["sell_fly_coverage_pct"] == 50.0
        assert f["sell_fly_count"] == 1                         # 600000 隐含 MFE 13 > 11×1.03

    def test_full_coverage_has_no_unavailable_entry(self, tmp_path):
        wr, base = self._base_with_closings(tmp_path, [
            {"code": "600000", "cost": 10.0, "mfe_pct": 1.0, "mfe_date": "2026-07-15"},
            {"code": "000001", "cost": 20.0, "mfe_pct": 1.0, "mfe_date": "2026-07-16"}])
        review = wr.build_weekly_review(base, "2026-07-17")
        assert review["facts"]["sell_fly_coverage_pct"] == 100.0
        assert not any("卖飞审计覆盖" in u for u in review["unavailable"])

    def test_markdown_states_coverage(self, tmp_path):
        wr, base = self._base_with_closings(tmp_path, [])
        md = wr.render_markdown(wr.build_weekly_review(base, "2026-07-17"))
        assert "审计覆盖 0/2" in md

    def test_unable_reason_propagated_from_mfe_file(self, tmp_path):
        """calc_mfe_mae 的 unable_reason 要带进卖飞"无法评估"原因,而不是笼统一句。"""
        wr, base = self._base_with_closings(tmp_path, [
            {"code": "600000", "mfe_pct": None, "cost": 10.0,
             "unable_reason": "K线未覆盖入场日"}])
        f = wr.build_weekly_review(base, "2026-07-17")["facts"]
        reasons = " ".join(u["reason"] for u in f["sell_fly_unevaluated"])
        assert "K线未覆盖入场日" in reasons


# ==================================== 12. 循环内重复 glob + JSON 解析

class TestWeeklyReviewIoReuse:
    """`load_mfe_after` / `find_plan_for_day` 不得在循环里反复 glob + 重复解析同一文件。"""

    def test_mfe_json_parsed_once_across_closings(self, tmp_path, monkeypatch):
        from close_review import weekly_review as wr

        base = _week_base(tmp_path)
        rows = []
        for i, code in enumerate(("600000", "000001", "600519")):
            rows.append({"date": "2026-07-06", "code": code, "side": "买入", "qty": 1000,
                         "price": 10.0, "fee": 1.0})
            rows.append({"date": "2026-07-14", "code": code, "side": "卖出", "qty": 1000,
                         "price": 11.0, "fee": 1.0})
        _write_ledger(base, rows)
        p = base / "01_data" / "holdings" / "2026-07-16_mfe_mae.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"date": "2026-07-16", "holdings": []}), encoding="utf-8")

        reads: list[str] = []
        orig = wr.load_json
        monkeypatch.setattr(wr, "load_json",
                            lambda path, default: (reads.append(str(path)), orig(path, default))[1])
        wr.build_weekly_review(base, "2026-07-17")
        mfe_reads = [r for r in reads if r.endswith("2026-07-16_mfe_mae.json")]
        assert len(mfe_reads) == 1              # 3 张平仓单只解析一次

    def test_plan_review_read_once_per_date(self, tmp_path, monkeypatch):
        from close_review import weekly_review as wr

        base = _week_base(tmp_path)
        _write_ledger(base, [
            {"date": "2026-07-14", "code": "600000", "side": "买入", "qty": 100,
             "price": 10.0, "fee": 1.0},
            {"date": "2026-07-14", "code": "000001", "side": "买入", "qty": 100,
             "price": 10.0, "fee": 1.0},
            {"date": "2026-07-14", "code": "600519", "side": "买入", "qty": 100,
             "price": 10.0, "fee": 1.0},
        ])
        rv = base / "04_reviews" / "daily" / "2026-07-13_final_review.json"
        rv.parent.mkdir(parents=True, exist_ok=True)
        rv.write_text(json.dumps({"next_day_plan": {"holding_plans": [{"code": "600000"}]}}),
                      encoding="utf-8")

        reads: list[str] = []
        orig = wr.load_json
        monkeypatch.setattr(wr, "load_json",
                            lambda path, default: (reads.append(str(path)), orig(path, default))[1])
        wr.build_weekly_review(base, "2026-07-17")
        plan_reads = [r for r in reads if r.endswith("2026-07-13_final_review.json")]
        assert len(plan_reads) == 1              # 3 笔成交共用一份计划，只读一次

    def test_mfe_index_globs_once_and_is_reusable(self, tmp_path):
        from close_review import weekly_review as wr

        holdings = tmp_path / "01_data" / "holdings"
        holdings.mkdir(parents=True)
        for day in ("2026-07-15", "2026-07-16"):
            (holdings / f"{day}_mfe_mae.json").write_text(
                json.dumps({"holdings": [{"code": "600000", "cost": 10.0, "mfe_pct": 5.0}]}),
                encoding="utf-8")
        idx = wr.mfe_index(tmp_path)
        assert [d for d, _ in idx] == ["2026-07-15", "2026-07-16"]
        cache: dict = {}
        got = wr.load_mfe_after(tmp_path, "2026-07-14", index=idx, cache=cache)
        assert "600000" in got and len(cache) == 1
        wr.load_mfe_after(tmp_path, "2026-07-14", index=idx, cache=cache)
        assert len(cache) == 1                  # 第二次命中缓存

    def test_load_mfe_after_still_works_without_index(self, tmp_path):
        """向后兼容:老调用方(不传 index/cache)行为不变。"""
        from close_review import weekly_review as wr

        holdings = tmp_path / "01_data" / "holdings"
        holdings.mkdir(parents=True)
        (holdings / "2026-07-16_mfe_mae.json").write_text(
            json.dumps({"holdings": [{"code": "600000", "cost": 10.0, "mfe_pct": 5.0}]}),
            encoding="utf-8")
        assert wr.load_mfe_after(tmp_path, "2026-07-14")["600000"]["mfe_pct"] == 5.0
        assert wr.load_mfe_after(tmp_path, "2026-07-20") is None
        assert wr.load_mfe_after(tmp_path / "nope", "2026-07-14") is None


# ================================================= 9. classify 运算符优先级显式化

class TestNewsClassifyPrecedence:
    """`A or B and C` 实际是 `A or (B and C)`。

    分组必须写出来:两种读法的分类结果不同(见 `POLICY_RULE_NOTE`),靠 Python 优先级
    隐式表达"意图"迟早被人按字面读反。这里把当前口径钉住,任何口径变更都会红。
    """

    def test_policy_category_alone_is_policy(self):
        import postclose_news_digest as pnd

        assert pnd.classify({"category": "policy_official", "matched_themes": []}) == "政策"
        assert pnd.classify({"category": "policy_consultation", "matched_themes": []}) == "政策"

    def test_official_category_needs_macro_theme(self):
        import postclose_news_digest as pnd

        assert pnd.classify({"category": "macro_official",
                             "matched_themes": ["宏观政策"]}) == "政策"
        # 官方源但没命中宏观政策主题 → 不是政策(落到风向/信息/舆情)
        assert pnd.classify({"category": "macro_official", "matched_themes": []}) != "政策"
        assert pnd.classify({"category": "company_official",
                             "matched_themes": ["半导体"]}) == "信息"

    def test_rule_note_documented(self):
        import postclose_news_digest as pnd

        assert "or" in pnd.POLICY_RULE_NOTE and "宏观政策" in pnd.POLICY_RULE_NOTE

    def test_market_keyword_wins_when_not_policy(self):
        import postclose_news_digest as pnd

        assert pnd.classify({"category": "cn_financial_media",
                             "matched_market_keywords": ["放量"]}) == "风向"

    def test_default_is_public_opinion(self):
        import postclose_news_digest as pnd

        assert pnd.classify({}) == "舆情"


# ============================== 10. 周报局部降级：float(pct) / closed_ranges 不得炸整份

class TestWeeklyReviewLocalDegradation:
    """一条脏数据不能打断整份周报。

    修前:`pct = float(pct)` 遇到 "N/A" 抛 ValueError、`r["start"]` 遇到缺键抛 KeyError,
    两者都在 build_weekly_review 主路径上 ⇒ 整份周报生成失败(而不是这一项 unavailable)。
    """

    def test_garbage_amv_value_skipped_not_raised(self, tmp_path):
        from close_review import weekly_review as wr

        path = tmp_path / "0amv.jsonl"
        path.write_text("\n".join([
            json.dumps({"date": "2026-07-13", "amv_change_pct": "N/A"}),
            json.dumps({"date": "2026-07-14", "amv_change_pct": 5.0}),
            json.dumps({"date": "2026-07-15", "amv_change_pct": None}),
            json.dumps({"date": "2026-07-16", "amv_change_pct": "-3.1"}),
        ]), encoding="utf-8")
        got = wr.load_amv_regimes(path)
        assert set(got) == {"2026-07-14", "2026-07-16"}      # 脏值跳过，其余照用
        assert got["2026-07-14"]["regime"] == "多头"
        assert got["2026-07-16"]["regime"] == "空头"

    def test_malformed_closed_range_does_not_raise(self, tmp_path):
        from close_review import weekly_review as wr

        cfg = {"official_years": {"2026": {"closed_ranges": [
            {"name": "缺键"}, {"start": "2026-10-01"}, "字符串不是 dict",
            {"start": "2026-07-14", "end": "2026-07-14"}]}}}
        p = tmp_path / "00_governance" / "CN_TRADING_CALENDAR.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        got = wr.trading_days_of_week(tmp_path, ["2026-07-13", "2026-07-14", "2026-07-18"])
        assert got["2026-07-13"] is True
        assert got["2026-07-14"] is False        # 合法区间仍生效
        assert got["2026-07-18"] is False        # 周六

    def test_whole_review_survives_dirty_amv(self, tmp_path):
        from close_review import weekly_review as wr

        base = _week_base(tmp_path)
        _write_ledger(base, [
            {"date": "2026-07-13", "code": "600000", "side": "买入", "qty": 100,
             "price": 10.0, "fee": 1.0},
            {"date": "2026-07-14", "code": "600000", "side": "卖出", "qty": 100,
             "price": 9.0, "fee": 1.0},
        ])
        amv = base / "01_data" / "market" / "0amv_observations.jsonl"
        amv.parent.mkdir(parents=True, exist_ok=True)
        amv.write_text(json.dumps({"date": "2026-07-14", "amv_change_pct": "bad"}) + "\n",
                       encoding="utf-8")
        review = wr.build_weekly_review(base, "2026-07-17")     # 修前在这里抛 ValueError
        assert review["facts"]["closing_count"] == 1
        assert isinstance(wr.render_markdown(review), str)


# =========================================== 11. bear_ratio_pct 除零

class TestBearRatioZeroDivision:
    """0AMV 记录里没有 >=2020 的样本时 `bear_days / len(days_since_2020)` 直接 ZeroDivisionError。"""

    def test_ratio_none_when_no_days(self):
        from trades import backtest_0amv_bear_regime as bt

        assert bt.safe_ratio_pct(0, 0) is None
        assert bt.safe_ratio_pct(3, 0) is None

    def test_ratio_computed_when_days_exist(self):
        from trades import backtest_0amv_bear_regime as bt

        assert bt.safe_ratio_pct(1, 4) == 25.0


# ==================================== 13. 收件人 open_id 不得硬编码兜底

class TestFeishuRecipientResolution:
    """硬编码 open_id 兜底 = 环境没配就把报告发给写死的那个人。缺配置必须报错。"""

    def test_env_override_used(self):
        import feishu_report_publisher as frp

        assert frp.resolve_to_open_id({"FEISHU_TO_OPEN_ID": "ou_env"}) == "ou_env"

    def test_missing_config_raises(self, tmp_path):
        import feishu_report_publisher as frp

        with pytest.raises(frp.FeishuError) as exc:
            frp.resolve_to_open_id({"OPENCLAW_CONFIG": str(tmp_path / "nope.json")})
        assert "FEISHU_TO_OPEN_ID" in str(exc.value)

    def test_no_hardcoded_open_id_constant(self):
        import feishu_report_publisher as frp

        source = Path(frp.__file__).read_text(encoding="utf-8")
        assert "ou_54ca3ea3b343e4d868b66b7084ed3be1" not in source
        assert not hasattr(frp, "DEFAULT_TO_OPEN_ID")

    def test_openclaw_config_recipient_used(self, tmp_path):
        import feishu_report_publisher as frp

        cfg = tmp_path / "openclaw.json"
        cfg.write_text(json.dumps({"channels": {"feishu": {"accounts": {"default": {
            "appId": "cli_x", "appSecret": "s", "reportToOpenId": "ou_cfg"}}}}}),
            encoding="utf-8")
        assert frp.resolve_to_open_id({"OPENCLAW_CONFIG": str(cfg)}) == "ou_cfg"

    def test_config_without_recipient_raises(self, tmp_path):
        import feishu_report_publisher as frp

        cfg = tmp_path / "openclaw.json"
        cfg.write_text(json.dumps({"channels": {"feishu": {"accounts": {"default": {
            "appId": "cli_x", "appSecret": "s"}}}}}), encoding="utf-8")
        with pytest.raises(frp.FeishuError):
            frp.resolve_to_open_id({"OPENCLAW_CONFIG": str(cfg)})








