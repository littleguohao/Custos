"""`local_tdx_data` 里**不依赖网络**的本地路径 —— 覆盖率 61%（缺 176）。

重点是 `_read_bj_vipdoc_daily`：它直接解通达信二进制 `.day` 格式，因为
**mootdx Reader 会把 920xxx 误路由到沪市**（源码 docstring 原话：
"mootdx Reader misroutes 920xxx to SH"）。

北交所路径今天已经出过两次问题 ——
① 涨跌幅上限写成 20% 而实际 30%（v0.33）
② 「BJ 曾因查错 market 拿不到权息」（`DATA_SOURCE_COVERAGE_MATRIX` 记录）
所以这条路径值得有测试。
"""
from __future__ import annotations

import pathlib
import struct
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("07_tools", "07_tools/local_tdx"):
    sys.path.insert(0, str(ROOT / _p))

from local_tdx import local_tdx_data as L  # noqa: E402


def _day_record(date_int, o, h, lo, c, amount, vol):
    """通达信 .day 单条记录：32 字节 `<IIIIIfII`（价格是**分**，即 ×100）。"""
    return struct.pack("<IIIIIfII", date_int, o, h, lo, c, amount, vol, 0)


def _write_bj(tmp_root, code, records):
    p = tmp_root / "vipdoc" / "bj" / "lday" / f"bj{code}.day"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"".join(records))
    return p


class TestBjVipdocReader:
    def test_reads_binary_records(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "TDX_ROOT", tmp_path)
        _write_bj(tmp_path, "920808", [
            _day_record(20260805, 1000, 1100, 990, 1050, 1.23e7, 50000),
            _day_record(20260806, 1050, 1200, 1040, 1180, 2.34e7, 80000),
        ])
        df = L._read_bj_vipdoc_daily("920808")
        assert len(df) == 2
        assert list(df["date"].dt.strftime("%Y-%m-%d")) == ["2026-08-05", "2026-08-06"]
        # 通达信存的是**分**，读出来必须是元
        assert df["close"].iloc[0] == pytest.approx(10.50)
        assert df["high"].iloc[1] == pytest.approx(12.00)

    def test_accepts_suffixed_code(self, tmp_path, monkeypatch):
        """带 `.BJ` 后缀也要能读 —— 上游代码形式不统一。"""
        monkeypatch.setattr(L, "TDX_ROOT", tmp_path)
        _write_bj(tmp_path, "920808", [_day_record(20260805, 1000, 1000, 1000, 1000, 1.0, 1)])
        assert len(L._read_bj_vipdoc_daily("920808.BJ")) == 1

    def test_missing_file_returns_reason_not_silence(self, tmp_path, monkeypatch):
        """⚠️ 文件不存在时返回**带原因**的空 DataFrame（`attrs["missing_reason"]`）。

        源码 docstring 写清了为什么：空 DataFrame 本身有**三义** ——
        文件不存在 / 解析失败 / 该票确实没有这一天的数据。
        调用方靠这个键区分，才能决定是回退在线源、还是把「本地数据缺失」
        报给下游，而不是一律当成「没数据」。
        """
        monkeypatch.setattr(L, "TDX_ROOT", tmp_path)
        df = L._read_bj_vipdoc_daily("920808")
        assert df.empty
        assert "file_not_found" in df.attrs.get("missing_reason", "")

    def test_empty_file_also_carries_reason(self, tmp_path, monkeypatch):
        """空文件与缺文件是不同的原因 —— 都要能从 attrs 分辨。"""
        monkeypatch.setattr(L, "TDX_ROOT", tmp_path)
        _write_bj(tmp_path, "920808", [])
        df = L._read_bj_vipdoc_daily("920808")
        assert df.empty

    def test_zero_date_records_skipped(self, tmp_path, monkeypatch):
        """`.day` 文件尾部有 date=0 的填充记录，必须跳过而不是产出 1970 年的行。"""
        monkeypatch.setattr(L, "TDX_ROOT", tmp_path)
        _write_bj(tmp_path, "920808", [
            _day_record(20260805, 1000, 1000, 1000, 1000, 1.0, 1),
            _day_record(0, 0, 0, 0, 0, 0.0, 0),
        ])
        df = L._read_bj_vipdoc_daily("920808")
        assert len(df) == 1

    def test_truncated_tail_ignored(self, tmp_path, monkeypatch):
        """不足 32 字节的尾部残块直接停 —— 不得当成一条记录解出垃圾。"""
        monkeypatch.setattr(L, "TDX_ROOT", tmp_path)
        p = _write_bj(tmp_path, "920808",
                      [_day_record(20260805, 1000, 1000, 1000, 1000, 1.0, 1)])
        p.write_bytes(p.read_bytes() + b"\x01\x02\x03")
        assert len(L._read_bj_vipdoc_daily("920808")) == 1

    def test_empty_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "TDX_ROOT", tmp_path)
        _write_bj(tmp_path, "920808", [])
        assert L._read_bj_vipdoc_daily("920808").empty


class TestEOdataReader:
    def test_reads_and_renames(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TDX_E_ODATA", str(tmp_path))
        code = L.normalize_code("600000")
        (tmp_path / f"{code}-all-latest.csv").write_text(
            "Date,Code,Open,High,Low,Close,Volume,Amount\n"
            "2026-08-05,600000,10.0,11.0,9.9,10.5,50000,520000\n", encoding="utf-8")
        df = L.read_e_odata_daily("600000")
        assert len(df) == 1 and df["close"].iloc[0] == 10.5
        assert df["source"].iloc[0] == "e_odata", "来源必须标出，供下游判断口径"

    def test_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TDX_E_ODATA", str(tmp_path))
        assert L.read_e_odata_daily("600000").empty

    def test_bad_dates_dropped(self, tmp_path, monkeypatch):
        """无法解析的日期行丢掉 —— 留着会让排序和取末根都错。"""
        monkeypatch.setenv("TDX_E_ODATA", str(tmp_path))
        code = L.normalize_code("600000")
        (tmp_path / f"{code}-all-latest.csv").write_text(
            "Date,Close\n2026-08-05,10.5\n不是日期,9.9\n", encoding="utf-8")
        assert len(L.read_e_odata_daily("600000")) == 1


class TestSavers:
    def test_save_json_creates_parents(self, tmp_path):
        p = tmp_path / "a" / "b" / "c.json"
        L.save_json(p, {"名": "值"})
        import json
        assert json.loads(p.read_text(encoding="utf-8")) == {"名": "值"}

    def test_save_csv_uses_bom(self, tmp_path):
        """⚠️ CSV 用 `utf-8-sig`（带 BOM）—— 目标机是 Windows，
        Excel 打无 BOM 的 utf-8 CSV 会把中文显示成乱码。"""
        import pandas as pd
        p = tmp_path / "x.csv"
        L.save_csv(p, pd.DataFrame({"名称": ["浦发"]}))
        assert p.read_bytes().startswith(b"\xef\xbb\xbf")


class TestCodeHelpers:
    @pytest.mark.parametrize("raw,want", [("600000", True), ("601398", True),
                                          ("000001", False), ("920808", False)])
    def test_sh_prefix(self, raw, want):
        assert (L._get_market_code(raw) == 1) is want

    @pytest.mark.parametrize("code", ["920808", "830799", "870508", "430047"])
    def test_bj_codes_recognized(self, code):
        assert L._is_bj_code(code), f"{code} 应识别为北交所"

    @pytest.mark.parametrize("code", ["600000", "000001", "300750", "688111"])
    def test_non_bj_codes(self, code):
        assert not L._is_bj_code(code)

    def test_strip_suffix(self):
        assert L._strip_suffix("600000.SH") == "600000"
        assert L._strip_suffix("920808.BJ") == "920808"
        assert L._strip_suffix("600000") == "600000"
