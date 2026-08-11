# -*- coding: utf-8 -*-
"""`holding_sector_mapper` —— 把持仓映射到通达信行业（`daily_pipeline` stage）。

数据源是**两个本地通达信文件**，格式很硬：

    TDX_ROOT/T0002/hq_cache/tdxhy.cfg   `1|688114|T0403|||X270302`（ASCII，管道分隔）
    TDX_ROOT/incon.dat                  `#TDXNHY` / `#TDXRSHY` 两个 **GBK** 段

⚠️ 经典的 `block_gn.dat`/`block_hy.dat`（mootdx `reader.block` 读的那两个）
**在本安装里不存在** ⇒ 概念/风格/指数/地区四个维度本地拿不到，
只能如实报空列表 + 每股 `quality.not_covered`。**这个「拿不到」必须显式留痕**，
否则下游会把空概念列表当成「这只票没有概念标签」。

2026-08-10 的教训：这个文件曾是 0% 覆盖，而正是查它在链上的地位时
发现 `daily_pipeline` 四个持仓 stage 的路径全断（commit f6c6568）。
"""

from __future__ import annotations

import json
import pathlib

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.pipeline.holdings import holding_sector_mapper as hsm  # noqa: E402
import sys


class TestNormCode:
    """⚠️ 本地语义：**只补零到 6 位，不加交易所后缀** ——
    与 `code_utils.norm_code`（会加 .SH/.SZ/.BJ）**刻意不同**，源码注释明写「Do not merge」。
    """

    @pytest.mark.parametrize(
        "raw,want",
        [
            ("600000", "600000"),
            (600000, "600000"),
            ("600000.0", "600000"),
            ("1", "000001"),
            ("920808", "920808"),
            ("", ""),
        ],
    )
    def test_pads_to_six_without_suffix(self, raw, want):
        assert hsm.norm_code(raw) == want

    def test_nan_is_empty(self):
        assert hsm.norm_code(float("nan")) == ""

    def test_non_numeric_passes_through(self):
        """「汇总」这类行要能原样过（main 里靠它筛掉）。"""
        assert hsm.norm_code("汇总") == "汇总"

    def test_does_not_append_exchange_suffix(self):
        from custos.core.code_utils import norm_code as shared

        assert hsm.norm_code("600000") == "600000"
        assert shared("600000") != hsm.norm_code("600000"), (
            "两者刻意不同；若哪天一致了，请回去读两边的 docstring 再决定是否合并"
        )


class TestLoadTdxhy:
    def test_parses_pipe_layout(self, tmp_path):
        p = tmp_path / "tdxhy.cfg"
        p.write_text(
            "1|688114|T0403|||X270302\n0|000001|T0301|||X480101\n", encoding="ascii"
        )
        m = hsm.load_tdxhy(p)
        assert m["688114"] == {"tdx": "T0403", "sw": "X270302"}
        assert m["000001"]["sw"] == "X480101"

    def test_skips_malformed_and_header_lines(self, tmp_path):
        p = tmp_path / "tdxhy.cfg"
        p.write_text(
            "# comment\n\nbad|line\n1|600000|T0101|||X110101\n", encoding="ascii"
        )
        m = hsm.load_tdxhy(p)
        assert list(m) == ["600000"], f"畸形行应被跳过：{m}"

    def test_missing_sw_column_is_empty_not_crash(self, tmp_path):
        """⚠️ 只有 3 段的行（没有 X 码）不得崩 —— 真实文件里存在这种行。"""
        p = tmp_path / "tdxhy.cfg"
        p.write_text("1|600001|T0102\n", encoding="ascii")
        assert hsm.load_tdxhy(p)["600001"] == {"tdx": "T0102", "sw": ""}


class TestLoadInconSections:
    def test_parses_gbk_sections(self, tmp_path):
        p = tmp_path / "incon.dat"
        p.write_bytes(
            (
                "#TDXNHY\nT0403|半导体\nT01|金融\n######\n#TDXRSHY\nX270302|集成电路\n"
            ).encode("gbk")
        )
        sec = hsm.load_incon_sections(p)
        assert sec["TDXNHY"]["T0403"] == "半导体"
        assert sec["TDXRSHY"]["X270302"] == "集成电路"

    def test_separator_lines_do_not_become_sections(self, tmp_path):
        p = tmp_path / "incon.dat"
        p.write_bytes("#TDXNHY\nT01|金融\n######\n".encode("gbk"))
        assert "#####" not in hsm.load_incon_sections(p)

    def test_entry_without_name_is_dropped(self, tmp_path):
        p = tmp_path / "incon.dat"
        p.write_bytes("#TDXNHY\nT01|\nT02|银行\n".encode("gbk"))
        sec = hsm.load_incon_sections(p)
        assert "T01" not in sec["TDXNHY"] and sec["TDXNHY"]["T02"] == "银行"


class TestLookupName:
    """行业码逐级向上截断查找（子行业查不到就回退到父行业）。"""

    TREE = {"T04": "电子", "T0403": "半导体"}

    def test_exact_hit(self):
        assert hsm.lookup_name(self.TREE, "T0403") == "半导体"

    def test_trims_two_chars_to_parent(self):
        assert hsm.lookup_name(self.TREE, "T040399") == "半导体"

    def test_falls_back_to_top_level(self):
        assert hsm.lookup_name(self.TREE, "T0499") == "电子"

    def test_unknown_returns_empty_not_none(self):
        """⚠️ 返回**空串**而非 None —— 下游 `if tdx_ind:` 靠它判断，
        两种都为假但类型混用会让 JSON 里出现 null 与 "" 两种形态。"""
        assert hsm.lookup_name(self.TREE, "Z9999") == ""

    def test_empty_input_is_empty(self):
        assert hsm.lookup_name(self.TREE, "") == ""
        assert hsm.lookup_name(self.TREE, None) == ""


class TestMainMapping:
    @pytest.fixture
    def env(self, monkeypatch, tmp_path):
        """隔离产出目录 + 打桩两个**加载函数**。

        ⚠️ **不能 patch `TDXHY_CFG` / `INCON_DAT` 这两个路径常量** ——
        它们被用作**默认参数**（`def load_tdxhy(path: Path = TDXHY_CFG)`），
        默认值在 `def` 执行时就绑定了，运行时替换模块常量对它无效。
        我第一版就这么写的，`main()` 仍去读真实的 `E:\new_tdx64/...` 而 FileNotFoundError；
        而且 `tests/test_base_path_depth.py::TestNoPatchingDefaultArgConstants`
        **当场把这个错法拦下来了**（它专门查「测试 patch 了被用作默认参数的常量」）。
        见 `governance/data/DATA_SOURCE_PRINCIPLE.md`「模块级常量 + 运行时替换 = 陷阱」。

        ⇒ 正确做法：patch `load_tdxhy` / `load_incon_sections` 本身。
        两个函数的**解析逻辑**由上面 `TestLoadTdxhy` / `TestLoadInconSections`
        用显式 path 参数单独覆盖，本类只测 `main` 的映射逻辑。
        """
        out = tmp_path / "out_dir"
        monkeypatch.setattr(hsm, "OUT_DIR", out)
        monkeypatch.setattr(
            hsm, "load_tdxhy", lambda *a, **k: {"600000": {"tdx": "T01", "sw": "X48"}}
        )
        monkeypatch.setattr(
            hsm,
            "load_incon_sections",
            lambda *a, **k: {"TDXNHY": {"T01": "银行"}, "TDXRSHY": {"X48": "银行II"}},
        )
        return tmp_path

    def _run(self, env, monkeypatch, positions, extra=()):
        src = env / "pos.json"
        src.write_text(json.dumps(positions, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(
            sys, "argv", ["x", "--input", str(src), "--date", "2026-08-11", *extra]
        )
        hsm.main()
        out = hsm.OUT_DIR / "2026-08-11_holding_sector_mapping.json"
        assert out.exists(), "映射未落盘"
        return json.loads(out.read_text(encoding="utf-8"))

    def test_maps_industry_and_shenwan(self, env, monkeypatch):
        rows = self._run(
            env,
            monkeypatch,
            [{"代码": "600000", "名称": "浦发银行", "持有金额": 10000}],
        )
        assert len(rows) == 1
        r = rows[0]
        assert r["industry"] == "银行" and r["industry_code"] == "T01"
        assert r["source"] == hsm.LOCAL_SOURCE
        assert any(x["BlockType"] == "申万行业" for x in r["raw_relation"])

    def test_not_covered_dims_are_declared(self, env, monkeypatch):
        """⚠️⚠️ 概念/风格/指数/地区本地拿不到，必须**显式声明 not_covered** ——
        否则下游会把空 `concepts` 当成「这只票没有概念标签」，
        而真相是「我们这里查不到」。这是本仓库反复出现的
        「把『不知道』显示成『没有』」那类失真。
        """
        rows = self._run(env, monkeypatch, [{"代码": "600000", "名称": "浦发银行"}])
        q = rows[0]["quality"]
        assert q["covered"] == ["行业"]
        assert q["not_covered"] == hsm.NOT_COVERED_DIMS
        assert "概念/风格/指数/地区需TQ或在线数据源" in q["note"]
        assert rows[0]["concepts"] == [] and rows[0]["indices"] == []

    def test_miss_records_why_not_just_empty(self, env, monkeypatch):
        """⚠️ 本地查不到行业时必须写 `relation_error` 说明原因，
        并区分**北交所**与 A 股（北交所本地行业记录常缺，是已知现象而非异常）。"""
        rows = self._run(env, monkeypatch, [{"代码": "920808", "名称": "某北交所股"}])
        r = rows[0]
        assert r["industry"] == ""
        assert "北交所" in (r["relation_error"] or ""), r["relation_error"]

    def test_a_share_miss_says_a_share(self, env, monkeypatch):
        rows = self._run(env, monkeypatch, [{"代码": "600999", "名称": "未知票"}])
        assert "A股" in (rows[0]["relation_error"] or "")

    def test_summary_row_is_dropped(self, env, monkeypatch):
        """台账导出常带「汇总」行，不能被当成一只股票。"""
        rows = self._run(
            env,
            monkeypatch,
            [{"代码": "600000", "名称": "浦发银行"}, {"代码": "汇总", "名称": "合计"}],
        )
        assert [r["code"] for r in rows] == ["600000"]

    def test_rows_without_name_are_dropped(self, env, monkeypatch):
        rows = self._run(
            env,
            monkeypatch,
            [{"代码": "600000", "名称": "浦发银行"}, {"代码": "600001", "名称": None}],
        )
        assert len(rows) == 1

    def test_csv_drops_raw_relation(self, env, monkeypatch):
        """CSV 不带 `raw_relation`（嵌套结构塞进 CSV 会变成不可读的字符串）。"""
        self._run(env, monkeypatch, [{"代码": "600000", "名称": "浦发银行"}])
        csv = hsm.OUT_DIR / "2026-08-11_holding_sector_mapping.csv"
        assert csv.exists()
        assert "raw_relation" not in csv.read_text(encoding="utf-8-sig").splitlines()[0]

    def test_tq_fallback_is_opt_in(self, env, monkeypatch):
        """⚠️ TQ 兜底默认**关闭** —— 它要加载通达信插件、有副作用。
        不传 `--use-tq-fallback` 时不得触碰 `init_tq`。
        """

        def boom():
            raise AssertionError("默认不该初始化 TQ")

        monkeypatch.setattr(hsm, "init_tq", boom)
        rows = self._run(env, monkeypatch, [{"代码": "600999", "名称": "未知票"}])
        assert rows[0]["source"] == hsm.LOCAL_SOURCE

    def test_tq_fallback_used_when_flag_set(self, env, monkeypatch):
        called = {"init": 0, "closed": 0}

        class FakeTq:
            def get_relation(self, tcode):
                return [
                    {"BlockCode": "C1", "BlockName": "人工智能", "BlockType": "概念"}
                ]

            def close(self):
                called["closed"] += 1

        def fake_init():
            called["init"] += 1
            return FakeTq()

        monkeypatch.setattr(hsm, "init_tq", fake_init)
        rows = self._run(
            env,
            monkeypatch,
            [{"代码": "600999", "名称": "未知票"}],
            extra=("--use-tq-fallback",),
        )
        assert called["init"] == 1 and called["closed"] == 1, (
            f"TQ 必须初始化一次并**关闭**（插件句柄不关会泄漏）：{called}"
        )
        r = rows[0]
        assert r["source"] == "tq"
        assert r["concepts"] == ["人工智能"]
        assert r["quality"]["not_covered"] == [], (
            "走 TQ 时是全量维度，不该再报 not_covered"
        )

    def test_tq_returning_nothing_records_the_miss(self, env, monkeypatch):
        class Empty:
            def get_relation(self, tcode):
                return []

            def close(self):
                pass

        monkeypatch.setattr(hsm, "init_tq", lambda: Empty())
        rows = self._run(
            env,
            monkeypatch,
            [{"代码": "600999", "名称": "未知票"}],
            extra=("--use-tq-fallback",),
        )
        assert "tq fallback returned nothing" in (rows[0]["relation_error"] or "")

    def test_tq_exception_does_not_break_the_run(self, env, monkeypatch):
        """⚠️ `tq_relation` 吞掉异常返回 [] —— TQ 挂了不该让整个 stage 失败。"""

        class Boom:
            def get_relation(self, tcode):
                raise RuntimeError("TQ 挂了")

            def close(self):
                pass

        monkeypatch.setattr(hsm, "init_tq", lambda: Boom())
        rows = self._run(
            env,
            monkeypatch,
            [{"代码": "600999", "名称": "未知票"}],
            extra=("--use-tq-fallback",),
        )
        assert rows[0]["industry"] == ""
