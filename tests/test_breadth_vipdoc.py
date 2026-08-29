# -*- coding: utf-8 -*-
"""`breadth_basis.compute_breadth_from_vipdoc` —— 涨跌平停四桶 vipdoc 本地自算（v0.137 方案③）。

钉住的口径（2026-08-28 生产机实测原型结论）：
- 逐只读 .day 尾部两根，末日收盘 vs 前日收盘 ⇒ 涨/跌/平；
- 末日 < 全宇宙最新交易日 ⇒ 停牌/陈旧桶（当日无新 K 线）；
- 空文件/不足两根 ⇒ 不报错，归停牌桶；**四桶合计恒等于宇宙数**；
- 自算涨家数与 880005 官方值差 >2% ⇒ crosscheck=warning，**不阻断**。
"""

from __future__ import annotations

import struct

import pytest

from custos.datasource import breadth_basis as bb

DAY0, DAY1 = 20260827, 20260828


def _bar(date, close, amount=1.0e8, volume=1000):
    """一根 vipdoc .day：date,o,h,l,c（×100 整数）, amount(float), volume, reserved。"""
    c = int(close * 100)
    return struct.pack("<IIIIIfII", date, c, c, c, c, amount, volume, 0)


def _mk_stock(root: "object", mkt: str, code6: str, bars: bytes) -> None:
    d = root / "vipdoc" / mkt / "lday"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{mkt}{code6}.day").write_bytes(bars)


@pytest.fixture
def universe(tmp_path, monkeypatch):
    """5 只 mini 宇宙：涨/跌/平/停牌（陈旧末日）/空文件 各一。"""
    _mk_stock(tmp_path, "sh", "600001", _bar(DAY0, 10.0) + _bar(DAY1, 10.5))  # 涨
    _mk_stock(tmp_path, "sz", "000002", _bar(DAY0, 10.0) + _bar(DAY1, 9.5))  # 跌
    _mk_stock(tmp_path, "sz", "300003", _bar(DAY0, 10.0) + _bar(DAY1, 10.0))  # 平
    _mk_stock(
        tmp_path, "bj", "920004", _bar(20260820, 5.0) + _bar(DAY0, 5.1)
    )  # 停（末日陈旧）
    _mk_stock(tmp_path, "sh", "688005", b"")  # 空文件 ⇒ 防护，不报错
    # 对照校验默认打桩掉（880005 走真实 vipdoc/网络，单测不许碰）
    monkeypatch.setattr(bb, "_official_up_880005", lambda ltd: (None, ""))
    return tmp_path


class TestComputeBreadthFromVipdoc:
    def test_four_buckets_sum_to_universe(self, universe):
        r = bb.compute_breadth_from_vipdoc(tdx_root=universe)
        assert r["available"] is True
        assert r["universe_size"] == 5
        assert (r["up_count"], r["down_count"], r["flat_count"]) == (1, 1, 1)
        total = r["up_count"] + r["down_count"] + r["flat_count"] + r["suspended_count"]
        assert total == r["universe_size"], "四桶合计必须等于宇宙数"

    def test_stale_last_date_and_empty_file_go_to_suspended(self, universe):
        """陈旧末日（停牌）与空文件（不足两根）都进停牌桶，且空文件不得炸 seek。"""
        r = bb.compute_breadth_from_vipdoc(tdx_root=universe)
        assert r["suspended_count"] == 2  # 920004 陈旧 + 688005 空文件
        assert r["as_of"] == str(DAY1)

    def test_up_down_ratio_and_stale_flag(self, universe):
        r = bb.compute_breadth_from_vipdoc(date="2026-08-28", tdx_root=universe)
        assert r["up_down_ratio"] == 1.0 and r["stale"] is False
        r2 = bb.compute_breadth_from_vipdoc(date="2026-08-29", tdx_root=universe)
        assert r2["stale"] is True, "数据日 ≠ 期望日必须如实标 stale"

    def test_crosscheck_ok_within_2pct(self, universe, monkeypatch):
        monkeypatch.setattr(bb, "_official_up_880005", lambda ltd: (1, str(DAY1)))
        r = bb.compute_breadth_from_vipdoc(tdx_root=universe)
        cc = r["crosscheck_880005"]
        assert cc["status"] == "ok" and cc["diff_pct"] == 0.0
        assert r["available"] is True

    def test_crosscheck_warning_over_2pct_does_not_block(self, universe, monkeypatch):
        """对照超阈只标 warning 写 note，**不阻断** —— 宇宙边界差属正常。"""
        monkeypatch.setattr(bb, "_official_up_880005", lambda ltd: (100, str(DAY1)))
        r = bb.compute_breadth_from_vipdoc(tdx_root=universe)
        assert r["available"] is True, "warning 不得把真值口径打成不可用"
        assert r["crosscheck_880005"]["status"] == "warning"
        assert "warning" in r["note"]

    def test_empty_universe_is_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bb, "_official_up_880005", lambda ltd: (None, ""))
        r = bb.compute_breadth_from_vipdoc(tdx_root=tmp_path)
        assert r["available"] is False and r["note"]

    def test_enumeration_failure_is_unavailable(self, monkeypatch):
        """TDX_ROOT 未配等枚举失败 ⇒ available=False 走回落，不抛。"""
        from custos.datasource.local_tdx import local_tdx_data as ltd

        def boom(**k):
            raise RuntimeError("TDX_ROOT not configured")

        monkeypatch.setattr(ltd, "list_local_vipdoc_codes", boom)
        r = bb.compute_breadth_from_vipdoc()
        assert r["available"] is False


class TestBreadthCountsReal:
    def test_real_caliber_wins(self, universe, monkeypatch):
        """自算成功 ⇒ 真值口径：down/flat/suspended 有值，status=vipdoc_self_compute。"""
        counts = bb.breadth_counts_real(
            1, tdx_root=universe
        )  # up_count 仍传 880005 官方口径
        assert counts["up_down_ratio_status"] == bb.VIPDOC_STATUS
        assert counts["down_count"] == 1
        assert counts["flat_count"] == 1 and counts["suspended_count"] == 2
        assert counts["total_stocks"] == 5
        assert counts["total_stocks_source"] == "vipdoc_universe_self_compute"
        assert counts["up_down_ratio"] == 1.0  # 官方涨 1 ÷ 自算跌 1

    def test_fallback_when_self_compute_fails(self, monkeypatch):
        """自算失败 ⇒ 回落 breadth_counts 的 derived/unavailable，flat/suspended 为 None。"""
        monkeypatch.setattr(
            bb,
            "compute_breadth_from_vipdoc",
            lambda **k: {"available": False, "note": "stub_off"},
        )
        monkeypatch.setattr(bb, "resolve_total_stocks", lambda: (None, "no source"))
        counts = bb.breadth_counts_real(2000)
        assert counts["up_down_ratio_status"] == "unavailable"
        assert counts["down_count"] is None
        assert counts["flat_count"] is None and counts["suspended_count"] is None
        assert "stub_off" in counts["note"], "回落原因必须留痕"

    def test_fallback_derived_path_kept(self, monkeypatch):
        monkeypatch.setattr(
            bb,
            "compute_breadth_from_vipdoc",
            lambda **k: {"available": False, "note": "stub_off"},
        )
        monkeypatch.setattr(bb, "resolve_total_stocks", lambda: (5000, "test_source"))
        counts = bb.breadth_counts_real(2000)
        assert counts["up_down_ratio_status"] == "derived_from_total"
        assert counts["down_count"] == 3000


class TestWritersUseRealCaliber:
    """写方接线钉板：两个 market_breadth 生产点必须走 breadth_counts_real（真值口径），
    不得回退到直接 breadth_counts 推算。"""

    @pytest.mark.parametrize(
        "rel",
        [
            "datasource/refresh_market_indices.py",
            "pipeline/market_timing/market_timing_collector.py",
        ],
    )
    def test_writer_calls_breadth_counts_real(self, rel):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "src" / "custos" / rel).read_text(
            encoding="utf-8"
        )
        assert "breadth_counts_real(" in src, f"{rel} 未接 vipdoc 自算真值口径"
        assert "flat_count" in src and "suspended_count" in src
