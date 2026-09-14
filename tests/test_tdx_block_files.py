# -*- coding: utf-8 -*-
"""TODO #63 等价性钉测：tdx_block_files（TDX 本地文件解析器，datasource 层）。

小样例文件（tmp_path 造 tdxhy.cfg / incon.dat / blocknew.cfg / .blk 字节样例）
直接对拍解析输出；异常路径（文件缺 / 截断 / 编码怪）行为一致；门面 re-export
与数据源函数同一实现（monkeypatch 通道不因下沉失效）。
"""

from __future__ import annotations

from custos.datasource.local_tdx import tdx_block_files as tbf

# ---------- tdxhy.cfg ----------


def test_tdxhy_parses_pipe_layout(tmp_path):
    p = tmp_path / "tdxhy.cfg"
    p.write_text(
        "1|688114|T0403|||X270302\n0|000001|T0301|||X480101\n", encoding="ascii"
    )
    m = tbf.load_tdxhy(p)
    assert m["688114"] == {"tdx": "T0403", "sw": "X270302"}
    assert m["000001"]["sw"] == "X480101"


def test_tdxhy_skips_malformed_and_header(tmp_path):
    p = tmp_path / "tdxhy.cfg"
    p.write_text("# comment\n\nbad|line\n1|600000|T0101|||X110101\n", encoding="ascii")
    assert list(tbf.load_tdxhy(p)) == ["600000"]


def test_tdxhy_missing_sw_column_not_crash(tmp_path):
    """只有 3 段的行（没有 X 码）不得崩——真实文件里存在这种行。"""
    p = tmp_path / "tdxhy.cfg"
    p.write_text("1|600001|T0102\n", encoding="ascii")
    assert tbf.load_tdxhy(p)["600001"] == {"tdx": "T0102", "sw": ""}


def test_tdxhy_bad_bytes_replaced_not_crash(tmp_path):
    """ASCII 解码遇非 ASCII 字节 → errors=replace（不 raise、不静默丢行）。"""
    p = tmp_path / "tdxhy.cfg"
    p.write_bytes(b"1|600000|T0101|\xff\xfe||X110101\n")
    assert tbf.load_tdxhy(p)["600000"]["tdx"] == "T0101"


def test_tdxhy_missing_file_raises(tmp_path):
    """文件缺失 → 抛 OSError（门面层依赖这一行为，不许改成静默返回空）。"""
    import pytest

    with pytest.raises(OSError):
        tbf.load_tdxhy(tmp_path / "nope.cfg")


# ---------- incon.dat ----------


def test_incon_parses_gbk_sections(tmp_path):
    p = tmp_path / "incon.dat"
    p.write_bytes(
        (
            "#TDXNHY\nT0403|半导体\nT01|金融\n######\n#TDXRSHY\nX270302|集成电路\n"
        ).encode("gbk")
    )
    sec = tbf.load_incon_sections(p)
    assert sec["TDXNHY"]["T0403"] == "半导体"
    assert sec["TDXRSHY"]["X270302"] == "集成电路"


def test_incon_separator_not_section(tmp_path):
    p = tmp_path / "incon.dat"
    p.write_bytes("#TDXNHY\nT01|金融\n######\n".encode("gbk"))
    assert "#####" not in tbf.load_incon_sections(p)


def test_incon_entry_without_name_dropped(tmp_path):
    p = tmp_path / "incon.dat"
    p.write_bytes("#TDXNHY\nT01|\nT02|银行\n".encode("gbk"))
    sec = tbf.load_incon_sections(p)
    assert "T01" not in sec["TDXNHY"] and sec["TDXNHY"]["T02"] == "银行"


def test_incon_bad_gbk_bytes_replaced(tmp_path):
    """GBK 解码遇非法字节 → errors=replace（不 raise）。"""
    p = tmp_path / "incon.dat"
    p.write_bytes(b"#TDXNHY\nT01|\xff\x01\xff\nT02|\xd2\xf8\xd0\xd0\n")
    sec = tbf.load_incon_sections(p)
    assert sec["TDXNHY"]["T02"] == "银行"


# ---------- lookup_name ----------


class TestLookupName:
    TREE = {"T04": "电子", "T0403": "半导体"}

    def test_exact_hit(self):
        assert tbf.lookup_name(self.TREE, "T0403") == "半导体"

    def test_trims_to_parent(self):
        assert tbf.lookup_name(self.TREE, "T040399") == "半导体"

    def test_falls_back_to_top_level(self):
        assert tbf.lookup_name(self.TREE, "T0499") == "电子"

    def test_unknown_returns_empty_not_none(self):
        assert tbf.lookup_name(self.TREE, "Z9999") == ""

    def test_empty_input_is_empty(self):
        assert tbf.lookup_name(self.TREE, "") == ""
        assert tbf.lookup_name(self.TREE, None) == ""


# ---------- blocknew.cfg / .blk ----------


def _make_block_dir(tmp_path):
    d = tmp_path / "blocknew"
    d.mkdir()
    cfg = (
        "持仓".encode("gbk")
        + b"\x00" * 88
        + b"CC"
        + b"\x00" * 58
        + "震荡".encode("gbk")
        + b"\x00" * 88
        + b"ZD"
        + b"\x00" * 58
    )
    (d / "blocknew.cfg").write_bytes(cfg)
    (d / "ZD.blk").write_bytes(
        b"1600150\r\n0000977\r\n2920808\r\n\r\nX600150\r\n160015\r\n"
    )
    return d


def test_resolve_block_file_by_name(tmp_path):
    d = _make_block_dir(tmp_path)
    assert tbf.resolve_block_file("震荡", d) == d / "ZD.blk"
    assert tbf.resolve_block_file("不存在", d) is None


def test_resolve_fallback_direct_blk(tmp_path):
    """cfg 未登记的同名 .blk 直接存在 → 兜底命中（用户自建板块）。"""
    d = _make_block_dir(tmp_path)
    (d / "自建池.blk").write_bytes(b"1600000\r\n")
    assert tbf.resolve_block_file("自建池", d) == d / "自建池.blk"


def test_resolve_missing_cfg_returns_none(tmp_path):
    """blocknew.cfg 不存在（OSError）→ None（绝不 raise）。"""
    d = tmp_path / "blocknew"
    d.mkdir()
    assert tbf.resolve_block_file("震荡", d) is None


def test_resolve_truncated_cfg_no_match(tmp_path):
    """cfg 截断（名字与 blk 短名不成对）→ None（不成对不硬凑）。"""
    d = _make_block_dir(tmp_path)
    (d / "blocknew.cfg").write_bytes("震荡".encode("gbk") + b"\x00" * 4)
    assert tbf.resolve_block_file("震荡", d) is None


def test_read_blk_parses_and_skips_dirty(tmp_path):
    d = _make_block_dir(tmp_path)
    rows = tbf.read_blk(d / "ZD.blk")
    assert rows == [
        {"code": "600150", "market": "SH"},
        {"code": "000977", "market": "SZ"},
        {"code": "920808", "market": "BJ"},
    ]  # 空行、非法前缀、短行均被跳过


def test_read_blk_missing_returns_empty(tmp_path):
    assert tbf.read_blk(tmp_path / "nope.blk") == []


# ---------- 门面与数据源同一实现（下沉后 monkeypatch 通道不失效） ----------


def test_facades_are_same_implementation():
    from custos.pipeline.holdings import holding_sector_mapper as hsm
    from custos.pipeline.screening import manual_pools

    assert hsm.load_tdxhy is tbf.load_tdxhy
    assert hsm.load_incon_sections is tbf.load_incon_sections
    assert hsm.lookup_name is tbf.lookup_name
    assert hsm.TDXHY_CFG == tbf.TDXHY_CFG and hsm.INCON_DAT == tbf.INCON_DAT
    assert manual_pools.read_blk is tbf.read_blk
    assert manual_pools.TDX_BLOCK_DIR == tbf.TDX_BLOCK_DIR


def test_manual_pools_block_dir_patch_channel(tmp_path, monkeypatch):
    """门面语义钉：patch manual_pools.TDX_BLOCK_DIR 必须仍然生效（包装把门面
    常量传下去，patch 不会落到数据源模块常量上失效）。"""
    from custos.pipeline.screening import manual_pools

    d = _make_block_dir(tmp_path)
    monkeypatch.setattr(manual_pools, "TDX_BLOCK_DIR", d)
    assert manual_pools.resolve_block_file("震荡") == d / "ZD.blk"
    assert manual_pools.resolve_block_file("不存在") is None
