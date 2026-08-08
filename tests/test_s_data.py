# -*- coding: utf-8 -*-
"""s_data(E:\\S_DATA qlib/csv 接入)测试——全部用 tmp_path 迷你 fixture,不碰真实数据。"""
import json

import numpy as np
import pandas as pd
import pytest

# ⚠️ 扁平 `import s_data`：2026-08-07 它从 `screening/` 移到 `07_tools/` 根层
# （它是零内部依赖的**只读数据 loader**，放在选股目录里让 `local_tdx/` 的
# 探针与对账工具反向依赖了 L3）。移动后**只有一条导入路径** ——
# 此前全部调用点用扁平 `import s_data`、只有本测试用 `from screening import`，
# 那是「同一文件两个模块对象」的隐患（`s_data.list_universe` 是被打桩的目标）。
import s_data
from research import backtest_factors as bt


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
        # ⚠️ 补 factor：`load_bars_qlib` 默认跳过**缺 factor**的 bundle
        #（口径无法从内部验证，而实测缺 factor 的 2021_2026 是加法调整、
        # 百分比收益放大 13~21%）。本组测试测的是**加载机制**（跨 bundle 拼接 /
        # start-end / count / NaN 丢弃），不是口径 ⇒ fixture 造成"可信 bundle"。
        # 口径判定本身由 TestBundleConvention 单独覆盖。
        np.array([0.0] + [1.0] * len(dates), dtype="<f4").tofile(fdir / "factor.day.bin")
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


class TestMixedConventionWarning:
    """跨 bundle 拼接时，**各 bundle 字段集不同**必须出声。

    ⚠️ 2026-08-06 实测 `E:\\S_DATA\\Q_DATA` 两个 bundle 字段集不同：

        2006_2020   OHLCV + **factor** + **change**
        2021_2026   只有 OHLCV

    而对 2021_2026 的对账证明它是「减去累计现金分红」的**加法调整**（raw−adj 分段常数、
    相邻段之差恰好等于每股分红），不是乘法前复权。有 factor 的老 bundle 很可能是标准
    qlib dump（乘法）⇒ **两个 bundle 可能是两种价格口径**，而 `load_bars_qlib` 直接 concat。
    那 10 个月缺口正好在两者之间，**任何长窗口都会跨过去**，接缝处收益率失真——
    而且是静默的：拼接不报错，结果看起来就是一条完整曲线。
    """

    def _bundle(self, root, name, days, fields):
        d = root / name
        (d / "instruments").mkdir(parents=True)
        (d / "calendars").mkdir(parents=True)
        (d / "calendars" / "day.txt").write_text("\n".join(days) + "\n", encoding="utf-8")
        (d / "instruments" / "all.txt").write_text("SH600000\n", encoding="utf-8")
        f = d / "features" / "sh600000"
        f.mkdir(parents=True)
        for name_ in fields:
            (f / f"{name_}.day.bin").write_bytes(b"\x00" * 8)
        return d

    def test_warns_when_field_sets_differ(self, tmp_path, capsys):
        s_data._MIXED_WARNED.clear()
        root = tmp_path / "Q_DATA"
        self._bundle(root, "2006_2020", ["2019-01-02", "2020-09-25"],
                     ["open", "high", "low", "close", "volume", "factor", "change"])
        self._bundle(root, "2021_2026", ["2021-08-02", "2026-01-30"],
                     ["open", "high", "low", "close", "volume"])
        bundles = s_data.list_bundles(root)
        hits = s_data.code_to_qlib_dir("600000", bundles)
        s_data._warn_if_mixed_convention("600000", hits)
        err = capsys.readouterr().err
        assert "字段集不同" in err and "价格口径" in err
        assert "factor" in err, "告警要列出差异字段，否则看不出差在哪"

    def test_silent_when_field_sets_match(self, tmp_path, capsys):
        s_data._MIXED_WARNED.clear()
        root = tmp_path / "Q_DATA"
        fields = ["open", "high", "low", "close", "volume"]
        self._bundle(root, "2006_2020", ["2019-01-02"], fields)
        self._bundle(root, "2021_2026", ["2021-08-02"], fields)
        bundles = s_data.list_bundles(root)
        s_data._warn_if_mixed_convention("600000", s_data.code_to_qlib_dir("600000", bundles))
        assert "字段集不同" not in capsys.readouterr().err

    def test_warns_once_per_code(self, tmp_path, capsys):
        """全市场跑批时不能刷屏——每个代码只警告一次。"""
        s_data._MIXED_WARNED.clear()
        root = tmp_path / "Q_DATA"
        self._bundle(root, "2006_2020", ["2019-01-02"],
                     ["open", "high", "low", "close", "volume", "factor"])
        self._bundle(root, "2021_2026", ["2021-08-02"],
                     ["open", "high", "low", "close", "volume"])
        hits = s_data.code_to_qlib_dir("600000", s_data.list_bundles(root))
        for _ in range(3):
            s_data._warn_if_mixed_convention("600000", hits)
        assert capsys.readouterr().err.count("字段集不同") == 1


class TestBundleConvention:
    """bundle 的价格口径判定与「加法口径默认跳过」。

    ⚠️ 实测（2026-08-06）两个 bundle 是两种口径：

        2006_2020  有 factor + change；change 与 close.pct_change() 一致率 100%；
                   除权日 close 平滑；factor 21 个取值对应 20 个事件、事件日阶梯
                   ⇒ **标准乘法复权** ✅
        2021_2026  只有 OHLCV，没有 factor；`raw − close` 分段常数、相邻段之差
                   恰好等于每股现金分红（600519: 194.99→173.31，差 21.68）
                   ⇒ **加法调整** ❌ 百分比收益被放大 13~21%

    而坏的那段（2021-08 起）恰好是本地 vipdoc 有数据的时段
    ⇒ **不需要重做数据，只需要不用那一份**。
    """

    def _mk(self, root, name, days, fields):
        d = root / name
        (d / "instruments").mkdir(parents=True)
        (d / "calendars").mkdir(parents=True)
        (d / "calendars" / "day.txt").write_text("\n".join(days) + "\n", encoding="utf-8")
        (d / "instruments" / "all.txt").write_text("SH600000\n", encoding="utf-8")
        f = d / "features" / "sh600000"
        f.mkdir(parents=True)
        import numpy as np
        for fn in fields:
            # 首元素=start_index，其后逐日对齐
            np.array([0.0] + [10.0] * len(days), dtype="<f4").tofile(f / f"{fn}.day.bin")
        return d

    def test_factor_means_multiplicative(self, tmp_path):
        root = tmp_path / "Q_DATA"
        d = self._mk(root, "2006_2020", ["2019-01-02", "2019-01-03"],
                     ["open", "high", "low", "close", "volume", "factor", "change"])
        assert s_data.bundle_convention(d) == "multiplicative"

    def test_no_factor_means_unverified(self, tmp_path):
        root = tmp_path / "Q_DATA"
        d = self._mk(root, "2021_2026", ["2021-08-02", "2021-08-03"],
                     ["open", "high", "low", "close", "volume"])
        assert s_data.bundle_convention(d) == "unverified"

    def test_missing_features_is_unknown(self, tmp_path):
        d = tmp_path / "empty"
        (d / "calendars").mkdir(parents=True)
        assert s_data.bundle_convention(d) == "unknown"

    def test_list_bundles_stamps_convention(self, tmp_path):
        root = tmp_path / "Q_DATA"
        self._mk(root, "2006_2020", ["2019-01-02"],
                 ["open", "high", "low", "close", "volume", "factor"])
        self._mk(root, "2021_2026", ["2021-08-02"],
                 ["open", "high", "low", "close", "volume"])
        got = {b["dir"].name: b["convention"] for b in s_data.list_bundles(root)}
        assert got == {"2006_2020": "multiplicative", "2021_2026": "unverified"}

    def test_unverified_bundle_skipped_by_default(self, tmp_path, capsys):
        """加法 bundle 默认跳过，且必须**出声**——静默少一段数据比报错更糟。"""
        s_data._UNVERIFIED_SKIP_WARNED.clear()
        root = tmp_path / "Q_DATA"
        self._mk(root, "2006_2020", ["2019-01-02", "2019-01-03"],
                 ["open", "high", "low", "close", "volume", "factor"])
        self._mk(root, "2021_2026", ["2021-08-02", "2021-08-03"],
                 ["open", "high", "low", "close", "volume"])
        got = s_data.load_bars_qlib(["600000"], 100, root=root)
        err = capsys.readouterr().err
        assert "跳过**口径无法验证**" in err and "2021_2026" in err
        # 只剩老 bundle 的日期
        assert got and got["600000"]["date"].max() < "2021-01-01"

    def test_allow_unverified_override(self, tmp_path):
        """确需放行时可用（比如只看绝对价差的研究）——不是删掉能力。"""
        s_data._UNVERIFIED_SKIP_WARNED.clear()
        root = tmp_path / "Q_DATA"
        self._mk(root, "2021_2026", ["2021-08-02", "2021-08-03"],
                 ["open", "high", "low", "close", "volume"])
        assert s_data.load_bars_qlib(["600000"], 100, root=root) == {}
        got = s_data.load_bars_qlib(["600000"], 100, root=root, allow_unverified=True)
        assert got and len(got["600000"]) == 2

    def test_skip_warning_once(self, tmp_path, capsys):
        s_data._UNVERIFIED_SKIP_WARNED.clear()
        root = tmp_path / "Q_DATA"
        self._mk(root, "2021_2026", ["2021-08-02"],
                 ["open", "high", "low", "close", "volume"])
        for _ in range(3):
            s_data.load_bars_qlib(["600000"], 100, root=root)
        assert capsys.readouterr().err.count("跳过**口径无法验证**") == 1


class TestUniverseAndPriceSameFilter:
    """`list_universe` 必须与 `load_bars_qlib` 用**同一套 bundle 过滤**。

    ⚠️ 否则：宇宙里含被跳过 bundle 的 instrument，而价格加载时那个 bundle 不读
    ⇒ 2020-09 之后上市的票**静默无数据**、被当成"这只票没信号"。
    实测 `2021_2026` 有 5484 只 instrument，跳过它却仍把这些票放进宇宙，
    就会产出一个「一半票拿不到数据」的宇宙而毫无提示 —— 与本仓库反复踩的静默失效同类。
    """

    def _mk(self, root, name, days, insts, fields):
        import numpy as np
        d = root / name
        (d / "instruments").mkdir(parents=True)
        (d / "calendars").mkdir(parents=True)
        (d / "calendars" / "day.txt").write_text("\n".join(days) + "\n", encoding="utf-8")
        (d / "instruments" / "all.txt").write_text(
            "\n".join(f"{i}\t{days[0]}\t{days[-1]}" for i in insts) + "\n", encoding="utf-8")
        for i in insts:
            f = d / "features" / i.lower()
            f.mkdir(parents=True)
            for fn in fields:
                np.array([0.0] + [10.0] * len(days), dtype="<f4").tofile(f / f"{fn}.day.bin")
        return d

    def _roots(self, tmp_path):
        root = tmp_path / "Q_DATA"
        ohlcv = ["open", "high", "low", "close", "volume"]
        self._mk(root, "2006_2020", ["2019-01-02", "2020-09-25"],
                 ["SH600000"], ohlcv + ["factor"])
        self._mk(root, "2021_2026", ["2021-08-02", "2026-02-06"],
                 ["SH600000", "SH688888"], ohlcv)          # 688888 只在被弃用的 bundle
        return root

    def test_universe_excludes_skipped_bundle(self, tmp_path, capsys):
        s_data._UNVERIFIED_SKIP_WARNED.clear()
        root = self._roots(tmp_path)
        got = s_data.list_universe(root)
        assert got == ["600000"], got
        assert "688888" not in got, (
            "被跳过 bundle 的票不该进宇宙——它拿不到价格，会被当成'没信号'")
        assert "宇宙也跳过" in capsys.readouterr().err

    def test_universe_can_include_when_allowed(self, tmp_path):
        root = self._roots(tmp_path)
        got = s_data.list_universe(root, allow_unverified=True)
        assert got == ["600000", "688888"], got

    def test_window_outside_coverage_warns(self, tmp_path, capsys):
        """弃用 2021_2026 后 qlib 只覆盖 1999-2020，而 --cross-window 用 2022-2024
        ⇒ 必然返回空。只靠"0 行"会被读成"因子无判别力"（审计 E9 的失效模式）。"""
        s_data._UNVERIFIED_SKIP_WARNED.clear()
        root = self._roots(tmp_path)
        s_data.load_bars_qlib(["600000"], 0, start="2022-01-01", end="2024-12-31", root=root)
        err = capsys.readouterr().err
        assert "完全不相交" in err and "2019-01-02~2020-09-25" in err
        assert "--data-source tdx" in err, "要给出可行的替代路径，不能只报错"

    def test_window_inside_coverage_silent(self, tmp_path, capsys):
        s_data._UNVERIFIED_SKIP_WARNED.clear()
        root = self._roots(tmp_path)
        s_data.load_bars_qlib(["600000"], 0, start="2019-01-01", end="2020-01-01", root=root)
        assert "完全不相交" not in capsys.readouterr().err
