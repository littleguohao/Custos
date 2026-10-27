# -*- coding: utf-8 -*-
"""export_mining_snapshot 的合成数据钉测（迷你生产树，无网络无通达信）。

被测对象是 scripts/dev 下的运维脚本（非包内模块）——importlib 按路径加载
（模式同 tests/test_shadow_1800_compare.py）。vipdoc .day 用 struct.pack 造真
32 字节记录，断言导出字节切片与原记录**逐位一致**（不 unpack/repack）。

注：简报原拟用 2021-06 作丢弃样本——但 2021-06 落在预热段 [2020-10-01,
2021-12-31]（K 线保留规则内）必然保留；此处改用 2019-06（真判定段间隙日）
作第二条丢弃样本，保留集 {2015, 2021-11, 2023} 与简报断言一致。
"""

import hashlib
import importlib.util
import json
import struct
import tarfile
from pathlib import Path

import pytest

from custos.core.paths import BASE

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "dev"
    / "export_mining_snapshot.py"
)
spec = importlib.util.spec_from_file_location("export_mining_snapshot", SCRIPT)
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)

DAY_FMT = "<IIIIIfII"
# 7 条：2015（挖掘A）/2018（判定段）/2019（判定段）/2021-11（预热段）/2023（挖掘B）
# /2026-10（holdout）/date_int=0（坏记录，同读取方惯例跳过）。保留 3 条。
SH600000_DATES = [20150601, 20180601, 20190603, 20211101, 20230601, 20261009, 0]
SH600000_KEPT = [20150601, 20211101, 20230601]
SZ000001_DATES = [20180102, 20190102]  # 全落判定段间隙 ⇒ 文件不产出
SERIES = {"2022-01-04": "long", "2026-10-09": "long"}


def _day_record(date_int: int) -> bytes:
    return struct.pack(DAY_FMT, date_int, 1000, 1100, 900, 1050, 1.5, 100, 0)


def _make_day(path: Path, date_ints: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(_day_record(d) for d in date_ints))


@pytest.fixture
def prod(tmp_path):
    """合成迷你生产树：tdx(vipdoc+T0002) 与 data(market/xdxr/sector/fundamentals)。"""
    tdx = tmp_path / "tdx"
    data = tmp_path / "data"
    _make_day(tdx / "vipdoc" / "sh" / "lday" / "sh600000.day", SH600000_DATES)
    _make_day(tdx / "vipdoc" / "sz" / "lday" / "sz000001.day", SZ000001_DATES)
    cfg = tdx / "T0002" / "hq_cache"
    cfg.mkdir(parents=True)
    (cfg / "tdxzs3.cfg").write_bytes(b"tdxzs3-bytes")
    (cfg / "tdxzs.cfg").write_bytes(b"tdxzs-bytes")
    xdxr = data / "market" / "xdxr"
    xdxr.mkdir(parents=True)
    (xdxr / "600000.json").write_text(
        json.dumps(
            {
                "code": "600000",
                "market": "SH",
                "fetched_at": "2026-10-01",
                "n": 3,
                "events": [
                    {"date": "2015-06-01", "fenhong": 0.5},
                    {"date": "20180601", "fenhong": 0.3},
                    {"date": "2026-10-09", "fenhong": 1.0},
                ],
                "shares": [
                    {"date": "2018-01-01", "total_shares": 100, "float_shares": 80},
                    {"date": "2026-10-09", "total_shares": 200, "float_shares": 160},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sidx = data / "market" / "sector_index"
    sidx.mkdir(parents=True)
    (sidx / "880001.csv").write_text(
        "date,close\n2015-06-01,100.0\n2018-06-01,110.0\n"
        "2021-11-01,120.0\n2026-10-09,130.0\n",
        encoding="utf-8",
    )
    (data / "market" / "sector_members.json").write_text(
        json.dumps({"880001": ["600000"]}, ensure_ascii=False), encoding="utf-8"
    )
    fund = data / "fundamentals"
    fund.mkdir(parents=True)
    pit_rows = [
        {"code": "600000", "report_date": "2016-09-30", "notice_date": "2016-12-15"},
        {"code": "600000", "report_date": "2018-03-31", "notice_date": "2018-04-20"},
        {"code": "600000", "report_date": "2020-12-31", "notice_date": "2021-03-30"},
        {"code": "600000", "report_date": "2026-06-30", "notice_date": "2026-10-20"},
        {"code": "600000", "report_date": "2021-06-30"},  # 缺 notice_date ⇒ 丢弃计数
    ]
    (fund / "pit_financials.jsonl").write_text(
        "\n".join(json.dumps(r) for r in pit_rows) + "\n", encoding="utf-8"
    )
    sc_rows = [
        {"code": "600000", "observed_on": "2018-05-01", "total_shares": 100},
        {"code": "600000", "observed_on": "2026-10-09", "total_shares": 200},
    ]
    (fund / "share_changes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in sc_rows) + "\n", encoding="utf-8"
    )
    return {"tdx": tdx, "data": data}


def _run(prod, out: Path, *extra: str, series=SERIES) -> int:
    return snap.main(
        [
            "--out",
            str(out),
            "--tdx-root",
            str(prod["tdx"]),
            "--data-root",
            str(prod["data"]),
            *extra,
        ],
        series_provider=lambda: series,
    )


class TestKeepRuleBoundaries:
    """保留规则边界钉测（与消费侧共用契约）。"""

    def test_kline_rule(self):
        assert snap._keep_kline_d8("20161231")
        assert not snap._keep_kline_d8("20170101")
        assert not snap._keep_kline_d8("20200930")
        assert snap._keep_kline_d8("20201001")
        assert snap._keep_kline_d8("20260904")
        assert not snap._keep_kline_d8("20260905")

    def test_xdxr_rule_keeps_judgment_years(self):
        assert snap._le_end("20180601")  # 判定段事件保留（前复权正确性）
        assert not snap._le_end("20260905")

    def test_pit_rule(self):
        assert snap._keep_pit("20161215")
        assert not snap._keep_pit("20180420")
        assert snap._keep_pit("20210330")  # 2020 起增量（挖掘 B 起点 as-of TTM）
        assert not snap._keep_pit("20261020")


class TestFilterDayFile:
    def test_keep_set_and_byte_identity(self, tmp_path):
        src = tmp_path / "sh600000.day"
        _make_day(src, SH600000_DATES)
        dst = tmp_path / "out" / "sh600000.day"
        assert snap.filter_day_file(src, dst) == (3, 4)
        expect = b"".join(_day_record(d) for d in SH600000_KEPT)
        assert dst.read_bytes() == expect  # 逐位一致（原样字节切片）

    def test_all_gap_file_not_written(self, tmp_path):
        src = tmp_path / "sz000001.day"
        _make_day(src, SZ000001_DATES)
        dst = tmp_path / "out" / "sz000001.day"
        assert snap.filter_day_file(src, dst) == (0, 2)
        assert not dst.exists()


class TestRowFilters:
    def test_filter_xdxr(self, tmp_path, prod):
        src = prod["data"] / "market" / "xdxr" / "600000.json"
        dst = tmp_path / "o" / "600000.json"
        assert snap.filter_xdxr(src, dst) == (2, 1)
        obj = json.loads(dst.read_text(encoding="utf-8"))
        assert obj["n"] == 2  # 过滤后重写 n
        assert [e["date"] for e in obj["events"]] == ["2015-06-01", "20180601"]
        assert [s["date"] for s in obj["shares"]] == ["2018-01-01"]
        assert obj["market"] == "SH" and obj["fetched_at"] == "2026-10-01"  # 其余原样

    def test_filter_sector_csv_uses_kline_rule(self, tmp_path, prod):
        src = prod["data"] / "market" / "sector_index" / "880001.csv"
        dst = tmp_path / "o" / "880001.csv"
        assert snap.filter_sector_csv(src, dst) == (2, 2)
        text = dst.read_text(encoding="utf-8")
        assert text.splitlines()[0] == "date,close"
        assert "2015-06-01" in text and "2021-11-01" in text
        assert "2018-06-01" not in text and "2026-10-09" not in text

    def test_filter_pit(self, tmp_path, prod):
        src = prod["data"] / "fundamentals" / "pit_financials.jsonl"
        dst = tmp_path / "o" / "pit_financials.jsonl"
        assert snap.filter_pit(src, dst) == (
            2,
            2,
            1,
        )  # 留 2016-12 / 2021-03；缺 notice 计 1
        kept = dst.read_text(encoding="utf-8")
        assert "2016-12-15" in kept and "2021-03-30" in kept
        assert "2018-04-20" not in kept and "2026-10-20" not in kept

    def test_filter_share_changes(self, tmp_path, prod):
        src = prod["data"] / "fundamentals" / "share_changes.jsonl"
        dst = tmp_path / "o" / "share_changes.jsonl"
        assert snap.filter_share_changes(src, dst) == (1, 1)
        assert "2018-05-01" in dst.read_text(encoding="utf-8")


class TestAmvSeries:
    def test_filtered_by_kline_rule(self, tmp_path):
        assert snap.write_amv_series(tmp_path, SERIES) == 1
        obj = json.loads(
            (tmp_path / "data" / "market" / "amv_regime_series.json").read_text(
                encoding="utf-8"
            )
        )
        assert obj["schema"] == "amv_regime_series/v1"
        assert obj["series"] == {"2022-01-04": "long"}  # 2026-10-09 被截

    def test_empty_series_writes_nothing(self, tmp_path):
        assert snap.write_amv_series(tmp_path, {"2026-10-09": "long"}) == 0
        assert not (tmp_path / "data" / "market" / "amv_regime_series.json").exists()


class TestEndToEnd:
    def test_export_manifest_and_verify(self, prod, tmp_path):
        out = tmp_path / "snap"
        assert _run(prod, out) == 0
        sh = out / "vipdoc" / "sh" / "lday" / "sh600000.day"
        assert sh.read_bytes() == b"".join(_day_record(d) for d in SH600000_KEPT)
        assert not (out / "vipdoc" / "sz" / "lday" / "sz000001.day").exists()
        assert (
            out / "T0002" / "hq_cache" / "tdxzs3.cfg"
        ).read_bytes() == b"tdxzs3-bytes"
        assert (out / "T0002" / "hq_cache" / "tdxzs.cfg").read_bytes() == b"tdxzs-bytes"
        assert (out / "data" / "market" / "sector_members.json").read_bytes() == (
            prod["data"] / "market" / "sector_members.json"
        ).read_bytes()

        m = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
        assert m["schema"] == "mining_snapshot/v1"
        assert set(m["segments"]) == {
            "mining_a",
            "judgment_excluded",
            "mining_b",
            "holdout_excluded",
        }
        assert m["segments"]["holdout_excluded"]["start"] == "2026-09-05"
        c = m["counts"]
        assert c["vipdoc_files"] == 1 and c["vipdoc_records_kept"] == 3
        assert c["vipdoc_records_dropped"] == 6 and c["vipdoc_empty_files_dropped"] == 1
        assert c["xdxr_events_kept"] == 2 and c["pit_missing_notice_dropped"] == 1
        assert c["amv_regime_points"] == 1
        assert len(m["known_limitations"]) == 4
        # 逐文件 sha256/bytes 与实际一致
        for entry in m["files"]:
            fp = out / entry["path"]
            assert hashlib.sha256(fp.read_bytes()).hexdigest() == entry["sha256"]
            assert fp.stat().st_size == entry["bytes"]
        # manifest_sha 复核（sorted "<relpath>:<sha256>" 行 join 取 sha256 前 16）
        lines = sorted(f"{e['path']}:{e['sha256']}" for e in m["files"])
        assert (
            hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]
            == m["manifest_sha"]
        )
        assert snap.verify(out) == 0

    def test_verify_detects_tamper_and_missing(self, prod, tmp_path):
        out = tmp_path / "snap"
        assert _run(prod, out) == 0
        day = out / "vipdoc" / "sh" / "lday" / "sh600000.day"
        buf = bytearray(day.read_bytes())
        buf[10] ^= 0xFF
        day.write_bytes(bytes(buf))
        assert snap.verify(out) != 0  # 篡改一字节 ⇒ 非零
        (out / "data" / "market" / "sector_members.json").unlink()
        assert snap.verify(out) != 0  # 缺失 ⇒ 非零

    def test_tar_roundtrip_verify_ok(self, prod, tmp_path):
        out = tmp_path / "snap2"
        assert _run(prod, out, "--tar") == 0
        tar_path = tmp_path / "snap2.tar.gz"
        assert tar_path.is_file()
        extract = tmp_path / "extract"
        extract.mkdir()
        kw = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        with tarfile.open(tar_path) as tf:
            tf.extractall(extract, **kw)
        assert snap.verify(extract / "snap2") == 0

    def test_out_guard_rejects_repo_non_cache_path(self):
        bad = BASE / "data" / "_snap_guard_test"
        assert snap.main(["--out", str(bad)]) != 0
        assert not bad.exists()

    def test_empty_amv_series_no_manifest(self, prod, tmp_path):
        out = tmp_path / "snap_empty"
        rc = _run(prod, out, series={"2026-10-09": "long"})
        assert rc != 0
        assert not (out / "MANIFEST.json").exists()


class TestGapRatios:
    """v0.457 验收①回修：前收 bar 落断口的除权事件，导出侧预算 qfq_ratio；
    快照侧复权因子与生产机全量数据逐位一致。"""

    @staticmethod
    def _bars():
        import pandas as pd

        d = pd.bdate_range("2015-01-02", "2022-06-30").strftime("%Y-%m-%d").tolist()
        c = [10.0 + 0.01 * (i % 97) + 0.003 * i for i in range(len(d))]
        return d, c

    def test_snapshot_factors_match_full(self):
        import numpy as np

        from custos.datasource.local_tdx import adjust_factors as af

        dates, closes = self._bars()
        events = [
            {
                "date": "2016-06-15",
                "category": 1,
                "fenhong": 3.0,
                "songzhuangu": 0.0,
                "peigu": 0.0,
                "peigujia": 0.0,
            },
            {
                "date": "2018-07-10",
                "category": 1,
                "fenhong": 2.0,
                "songzhuangu": 5.0,
                "peigu": 0.0,
                "peigujia": 0.0,
            },
            {
                "date": "20190520",
                "category": 1,
                "fenhong": 1.5,
                "songzhuangu": 0.0,
                "peigu": 0.0,
                "peigujia": 0.0,
            },
            {
                "date": "2021-05-12",
                "category": 1,
                "fenhong": 4.0,
                "songzhuangu": 0.0,
                "peigu": 0.0,
                "peigujia": 0.0,
            },
        ]
        full = af.compute_qfq_factors(np.array(dates), np.array(closes), events)
        keep = [i for i, d in enumerate(dates) if snap._keep_kline_d8(snap._d8(d))]
        sd = [dates[i] for i in keep]
        sc = [closes[i] for i in keep]
        ev_snap = [dict(e) for e in events]
        n = snap.attach_gap_ratios(ev_snap, (dates, closes))
        assert n == 2  # 只有两个断口期事件被预算
        assert "qfq_ratio" not in ev_snap[0] and "qfq_ratio" not in ev_snap[3]
        got = af.compute_qfq_factors(np.array(sd), np.array(sc), ev_snap)
        assert np.array_equal(got, full[keep]), "快照因子须与全量逐位一致"
        # 回修前（无 qfq_ratio）确实不一致——即生产机验收①看到的微差
        bad = af.compute_qfq_factors(
            np.array(sd), np.array(sc), [dict(e) for e in events]
        )
        assert not np.array_equal(bad, full[keep])

    def test_none_ratio_means_skip(self):
        import numpy as np

        from custos.datasource.local_tdx import adjust_factors as af

        d = np.array(["2016-12-30", "2020-10-09", "2020-10-12"])
        c = np.array([10.0, 11.0, 12.0])
        f = af.compute_qfq_factors(d, c, [{"date": "2018-01-02", "qfq_ratio": None}])
        assert np.array_equal(f, np.ones(3))

    def test_filter_xdxr_writes_ratio_json_roundtrip(self, tmp_path):
        import json

        dates, closes = self._bars()
        src = tmp_path / "600000.json"
        src.write_text(
            json.dumps(
                {
                    "market": 1,
                    "events": [
                        {
                            "date": "2018-07-10",
                            "category": 1,
                            "fenhong": 2.0,
                            "songzhuangu": 0.0,
                            "peigu": 0.0,
                            "peigujia": 0.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        dst = tmp_path / "out.json"
        snap.filter_xdxr(src, dst, bars=(dates, closes))
        r = json.loads(dst.read_text(encoding="utf-8"))["events"][0]["qfq_ratio"]
        from custos.datasource.local_tdx import adjust_factors as af

        i = dates.index("2018-07-10")
        assert r == af.event_ratio(closes[i - 1], {"fenhong": 2.0})
