# -*- coding: utf-8 -*-
"""s_data(E:\\S_DATA qlib/csv 接入)测试——全部用 tmp_path 迷你 fixture,不碰真实数据。"""
import json

import numpy as np
import pandas as pd
import pytest

from screening import s_data
from screening import backtest_factors as bt


def _mk_bundle(root, name, dates, stocks):
    """造一个迷你 qlib bundle:stocks={inst: {field: (start_index, values)}}。"""
    b = root / name
    (b / "calendars").mkdir(parents=True)
    (b / "instruments").mkdir()
    (b / "calendars" / "day.txt").write_text("\n".join(dates), encoding="utf-8")
    (b / "instruments" / "all.txt").write_text("\n".join(stocks), encoding="utf-8")
    for inst, fields in stocks.items():
        fdir = b / "features" / inst
        fdir.mkdir(parents=True)
        for field in s_data._FIELDS:
            si, vals = fields.get(field, (0, [1.0] * len(dates)))
            np.array([float(si)] + [float(v) for v in vals], dtype="<f4").tofile(fdir / f"{field}.day.bin")
    return b


DATES_A = ["2020-01-0%d" % d for d in (2, 3, 6, 7)]          # bundle A: 4 天
DATES_B = ["2021-01-0%d" % d for d in (4, 5, 6, 7, 8)]       # bundle B: 5 天


@pytest.fixture
def qroot(tmp_path):
    root = tmp_path / "Q_DATA"
    close_a = [10.0, 10.1, np.nan, 10.3]                     # 含 NaN(停牌) → 该行应被丢
    _mk_bundle(root, "2006_2020", DATES_A, {
        "SZ000001": {f: (0, close_a) for f in s_data._FIELDS},
    })
    close_b = [11.0, 11.1, 11.2, 11.3, 11.4]
    _mk_bundle(root, "2021_2026", DATES_B, {
        "SZ000001": {f: (2, close_b[:3]) for f in s_data._FIELDS},   # start_index=2 → 对齐 DATES_B[2:]
        "SH600000": {f: (0, close_b) for f in s_data._FIELDS},
        "BJ920000": {f: (0, close_b) for f in s_data._FIELDS},
    })
    return root


def test_list_bundles_sorted(qroot):
    bs = s_data.list_bundles(qroot)
    assert [b["dir"].name for b in bs] == ["2006_2020", "2021_2026"]
    assert bs[0]["start"] == "2020-01-02" and bs[1]["end"] == "2021-01-08"


def test_qlib_cross_bundle_concat_and_nan_drop(qroot):
    d = s_data.load_bars_qlib(["000001"], count=0, root=qroot)
    df = d["000001"]
    # bundle A 丢 NaN 行后 3 条 + bundle B start_index=2 → 3 条(DATES_B[2:]),跨段拼接
    assert list(df["date"]) == ["2020-01-02", "2020-01-03", "2020-01-07",
                                "2021-01-06", "2021-01-07", "2021-01-08"]
    assert float(df["close"].iloc[-1]) == pytest.approx(11.2, abs=1e-4)  # close_b[2] 对齐末日


def test_qlib_start_end_and_count(qroot):
    d = s_data.load_bars_qlib(["000001"], count=0, start="2021-01-01", end="2021-01-07", root=qroot)
    assert list(d["000001"]["date"]) == ["2021-01-06", "2021-01-07"]
    d2 = s_data.load_bars_qlib(["000001"], count=2, root=qroot)
    assert list(d2["000001"]["date"]) == ["2021-01-07", "2021-01-08"]   # tail(count)


def test_qlib_code_mapping_and_universe(qroot):
    assert "600000" in s_data.load_bars_qlib(["600000"], count=0, root=qroot)
    assert "920000" in s_data.load_bars_qlib(["920000"], count=0, root=qroot)
    assert s_data.load_bars_qlib(["999999"], count=0, root=qroot) == {}
    assert s_data.list_universe(qroot, source="qlib") == ["000001", "600000", "920000"]


def test_csv_loader(tmp_path):
    croot = tmp_path / "CSV_DATA"
    croot.mkdir()
    pd.DataFrame({
        "Date": ["2021-01-04", "2021-01-05", "2021-01-06"], "Code": ["000001.SZ"] * 3,
        "Open": [1, 2, 3], "High": [1, 2, 3], "Low": [1, 2, 3], "Close": [10.0, 10.5, 11.0],
        "Volume": [100, 200, 300], "Amount": [1, 2, 3],
    }).to_csv(croot / "000001.SZ-all-latest.csv", index=False)
    d = s_data.load_bars_csv(["000001"], count=0, start="2021-01-05", root=croot)
    assert list(d["000001"]["date"]) == ["2021-01-05", "2021-01-06"]
    assert list(d["000001"].columns) == ["date", "open", "high", "low", "close", "volume"]
    assert s_data.list_universe(croot, source="csv") == ["000001"]


def test_main_with_qlib_data_source(tmp_path):
    # 45 个交易日单 bundle,直接验证 --data-source qlib 全链路(main 内部自己构造 loader)
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2021-01-04", periods=45)]
    qdir = tmp_path / "sroot" / "Q_DATA"
    n = len(dates)
    _mk_bundle(qdir, "2021_2026", dates, {
        "SZ000001": {f: (0, [10.0 + 0.1 * i for i in range(n)]) for f in s_data._FIELDS},
    })
    out = tmp_path / "sim.json"
    rc = bt.main(["--codes", "000001", "--data-source", "qlib", "--s-data-root", str(tmp_path / "sroot"),
                  "--start", "2021-01-04", "--end", dates[-1], "--trade-sim", "--scorer", "baseline",
                  "--entry-filter", "none", "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["data_source"] == "qlib" and payload["start"] == "2021-01-04"
    assert payload["trade_summary"]["n"] >= 1          # 真数据加载进 evaluate_trades 并产生了交易


class TestListUniverseParsing:
    """`instruments/all.txt` 是**制表符分隔**的 `SH600000\\t1999-11-10\\t2026-02-27`。

    ⚠️ 旧实现 `codes.add(ln[-6:])` 取整行末 6 字符 ⇒ 取到的是**结束日期尾巴**。
    2026-08-06 实测宇宙里混进了 `'-06-09'`、`'-09-25'` 两条垃圾，
    而函数照样"成功"返回 5486 项 —— 正是本仓库反复踩的静默失效。
    """

    def _bundle(self, tmp_path, lines):
        d = tmp_path / "Q_DATA" / "2021_2026"
        (d / "instruments").mkdir(parents=True)
        (d / "calendars").mkdir(parents=True)
        (d / "calendars" / "day.txt").write_text("2021-08-02\n2026-02-27\n",
                                                 encoding="utf-8")
        (d / "instruments" / "all.txt").write_text("\n".join(lines) + "\n",
                                                   encoding="utf-8")
        return tmp_path / "Q_DATA"

    def test_tab_separated_lines_yield_codes_not_dates(self, tmp_path):
        root = self._bundle(tmp_path, [
            "SH600000\t1999-11-10\t2026-02-27",
            "SZ000001\t1991-04-03\t2026-02-27",
            "SZ300750\t2018-06-11\t2024-06-09",
        ])
        got = s_data.list_universe(root)
        assert got == ["000001", "300750", "600000"], got
        assert not any(c.startswith("-") for c in got), "日期碎片混进了宇宙"

    def test_code_only_lines_still_work(self, tmp_path):
        """有的 bundle 是一行一个代码，不能因为改了解析就读不了。"""
        root = self._bundle(tmp_path, ["SH600000", "SZ000001", "600519.SH"])
        assert s_data.list_universe(root) == ["000001", "600000", "600519"]

    def test_high_reject_rate_warns(self, tmp_path, capsys):
        """若 bundle 换了格式导致大面积取不出代码，必须**大声告警**而不是静默返回空。"""
        root = self._bundle(tmp_path, ["# header", "garbage line", "another bad one",
                                       "SH600000\t1999-11-10\t2026-02-27"])
        s_data.list_universe(root)
        err = capsys.readouterr().err
        assert "取不出 6 位代码" in err and "宇宙不可信" in err

    def test_low_reject_rate_silent(self, tmp_path, capsys):
        """个别坏行不告警——否则告警会变成噪音、没人看。"""
        lines = [f"SH60{i:04d}\t1999-11-10\t2026-02-27" for i in range(30)]
        lines.append("# 一行注释")
        root = self._bundle(tmp_path, lines)
        s_data.list_universe(root)
        assert "宇宙不可信" not in capsys.readouterr().err
