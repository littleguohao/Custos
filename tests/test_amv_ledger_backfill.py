# -*- coding: utf-8 -*-
"""0AMV 台账单源化（v0.150）钉测：回填幂等 / 台账优先 / vdat 兜底 / regime 一致性。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from custos.research import backtest_factors as bf


def _load_backfill_mod():
    """scripts/dev 不是包——按路径加载一次性脚本（测试其纯函数）。"""
    spec = importlib.util.spec_from_file_location(
        "amv_backfill_vdat",
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "dev"
        / "amv_backfill_vdat.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bfill = _load_backfill_mod()

_VDAT_FAKE = {
    "records": [
        {"date": "2024-01-02", "change_pct": 5.0},  # 台账已有 ⇒ 不覆盖
        {"date": "2024-01-03", "change_pct": -3.0},  # 台账没有 ⇒ 回填
        {"date": "2024-01-04", "change_pct": 1.0},  # 台账没有 ⇒ 回填
    ]
}
_LEDGER_ROWS = [
    # 台账已有 01-02（人工修正值 4.2 ≠ vdat 5.0 ⇒ 台账优先）+ 一行 candidate
    {
        "date": "2024-01-02",
        "amv_change_pct": 4.2,
        "as_of": "2024-01-02",
        "quality": "confirmed",
        "source": "user_manual_input",
        "recorded_at": "2024-01-02T17:00:00+08:00",
    },
    {
        "date": "2024-01-05",
        "amv_change_pct": 9.9,
        "as_of": "2024-01-05",
        "quality": "candidate",  # 非 confirmed：占位但也算「已有日期不动」
        "source": "market_timing_input",
        "recorded_at": "2024-01-05T17:00:00+08:00",
    },
]


def _write_ledger(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


@pytest.fixture()
def patched_vdat(monkeypatch):
    from custos.datasource.local_tdx import compass_amv

    monkeypatch.setattr(
        compass_amv, "parse_amv_daily", lambda since="2024-01-01", root=None: _VDAT_FAKE
    )
    return compass_amv


class TestBackfill:
    def test_fill_missing_keep_existing(self, tmp_path, patched_vdat):
        ledger = tmp_path / "0amv_observations.jsonl"
        _write_ledger(ledger, _LEDGER_ROWS)
        stats = bfill.backfill(ledger, now_iso="2026-08-30T10:00:00+08:00")
        # vdat 3 条：01-02 台账已有（不动）、01-05 台账已有 candidate 行（不动）、
        # 01-03/01-04 回填 2 条
        assert stats["added"] == 2
        assert stats["total"] == 4
        rows = bfill.load_ledger(ledger)
        by_date = {}
        for r in rows:
            by_date.setdefault(r["date"], []).append(r)
        # 台账优先：01-02 仍是人工值 4.2（vdat 的 5.0 没进来）
        assert by_date["2024-01-02"][0]["amv_change_pct"] == 4.2
        assert by_date["2024-01-02"][0]["source"] == "user_manual_input"
        # 回填条目形态钉死
        r3 = by_date["2024-01-03"][0]
        assert r3["amv_change_pct"] == -3.0
        assert r3["quality"] == "confirmed" and r3["source"] == "compass_day_vdat"
        assert r3["as_of"] == "2024-01-03"
        # 按日期排序落盘
        assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
        # 备份已落
        assert stats["backup"] and Path(stats["backup"]).is_file()

    def test_idempotent(self, tmp_path, patched_vdat):
        """幂等：第二次跑新增 0 条、文件不变。"""
        ledger = tmp_path / "0amv_observations.jsonl"
        _write_ledger(ledger, _LEDGER_ROWS)
        bfill.backfill(ledger, now_iso="2026-08-30T10:00:00+08:00")
        first = ledger.read_text(encoding="utf-8")
        stats2 = bfill.backfill(ledger, now_iso="2026-08-30T11:00:00+08:00")
        assert stats2["added"] == 0
        assert ledger.read_text(encoding="utf-8") == first

    def test_dry_run_writes_nothing(self, tmp_path, patched_vdat):
        ledger = tmp_path / "0amv_observations.jsonl"
        _write_ledger(ledger, _LEDGER_ROWS)
        before = ledger.read_text(encoding="utf-8")
        stats = bfill.backfill(
            ledger, now_iso="2026-08-30T10:00:00+08:00", dry_run=True
        )
        assert stats["added"] == 2 and stats["dry_run"] is True
        assert ledger.read_text(encoding="utf-8") == before  # 未写
        assert not list(tmp_path.glob("*.bak_*"))  # 无备份


class TestLedgerPerLineTolerance:
    """v0.156：单行损坏只跳过该行 + stderr WARN（带行号），其余行照常——
    此前整循环一个 try，一行坏 ⇒ 返回 [] ⇒ 静默回落停更的 vdat 兜底。"""

    def _write_with_bad_line(self, path: Path) -> None:
        good1 = {
            "date": "2024-01-02",
            "amv_change_pct": 5.0,
            "quality": "confirmed",
            "recorded_at": "t1",
        }
        good2 = {
            "date": "2024-01-04",
            "amv_change_pct": -3.0,
            "quality": "confirmed",
            "recorded_at": "t2",
        }
        path.write_text(
            json.dumps(good1)
            + "\n"
            + '{"date": "2024-01-03", "amv_change_pct":'  # 第 2 行：坏 JSON
            + "\n"
            + json.dumps(good2)
            + "\n",
            encoding="utf-8",
        )

    def test_bad_line_skipped_others_kept(self, tmp_path, capsys):
        ledger = tmp_path / "0amv_observations.jsonl"
        self._write_with_bad_line(ledger)
        recs = bf._amv_ledger_records("2024-01-01", None, ledger_path=ledger)
        assert recs == [
            {"date": "2024-01-02", "change_pct": 5.0},
            {"date": "2024-01-04", "change_pct": -3.0},
        ], "坏行只跳过本行，其余行照常读出"
        err = capsys.readouterr().err
        assert "[WARN]" in err and "第 2 行" in err, "WARN 必须带行号可见"

    def test_bad_line_does_not_fall_back_to_vdat(self, tmp_path, monkeypatch, capsys):
        """台账夹坏行 ⇒ load_amv_regime 仍走台账主源，不回落 vdat 兜底。"""
        import custos.core.paths as paths

        monkeypatch.setattr(paths, "MARKET_DIR", tmp_path)
        self._write_with_bad_line(tmp_path / "0amv_observations.jsonl")
        from custos.datasource.local_tdx import compass_amv

        def _boom(since="2015-01-01", root=None):
            raise AssertionError("台账有好行就不得回落 vdat 兜底")

        monkeypatch.setattr(compass_amv, "parse_amv_daily", _boom)
        reg = bf.load_amv_regime(since="2024-01-01")
        assert reg == {"2024-01-02": "做多", "2024-01-04": "空头"}

    def test_unreadable_file_still_falls_back(self, tmp_path):
        """文件整体不存在仍返回 []（走既有兜底）——逐行容错不改变这一路径。"""
        assert bf._amv_ledger_records("2024-01-01", None, tmp_path / "nope.jsonl") == []


class TestLoadAmvRegimeSingleSource:
    def test_ledger_full_read(self, monkeypatch):
        """台账全量 confirmed → 状态机重放（不再依赖 vdat）。"""
        monkeypatch.setattr(
            bf,
            "_amv_ledger_records",
            lambda since, after_date, ledger_path=None: [
                {"date": "2024-01-02", "change_pct": 5.0},  # >4 ⇒ 做多
                {"date": "2024-01-03", "change_pct": 1.0},  # 粘滞做多
                {"date": "2024-01-04", "change_pct": -3.0},  # <-2.3 ⇒ 空头
            ],
        )
        reg = bf.load_amv_regime(since="2024-01-01")
        assert reg == {
            "2024-01-02": "做多",
            "2024-01-03": "做多",
            "2024-01-04": "空头",
        }

    def test_vdat_fallback_when_ledger_empty(self, monkeypatch):
        """台账空 ⇒ 回落 vdat 主序列 + 台账尾部（旧路径兜底保留）。"""
        calls = []

        def fake_ledger(since, after_date, ledger_path=None):
            calls.append(after_date)
            return (
                []
                if after_date is None
                else [{"date": "2024-01-05", "change_pct": 5.0}]
            )

        monkeypatch.setattr(bf, "_amv_ledger_records", fake_ledger)
        from custos.datasource.local_tdx import compass_amv

        monkeypatch.setattr(
            compass_amv,
            "parse_amv_daily",
            lambda since="2015-01-01", root=None: {
                "records": [{"date": "2024-01-04", "change_pct": -3.0}]
            },
        )
        reg = bf.load_amv_regime(since="2024-01-01")
        assert calls[0] is None  # 先试台账全量
        assert calls[1] == "2024-01-04"  # 兜底：vdat 尾部拼接
        assert reg == {"2024-01-04": "空头", "2024-01-05": "做多"}

    def test_both_empty_returns_empty(self, monkeypatch):
        monkeypatch.setattr(bf, "_amv_ledger_records", lambda *a, **kw: [])
        from custos.datasource.local_tdx import compass_amv

        monkeypatch.setattr(
            compass_amv,
            "parse_amv_daily",
            lambda since="2015-01-01", root=None: {"error": "file_not_found"},
        )
        assert bf.load_amv_regime(since="2024-01-01") == {}

    def test_regime_consistency_same_records(self):
        """同一份记录两条路径（vdat 序 / 台账序）regime 逐日一致。"""
        records = [
            {"date": "2024-01-02", "change_pct": 5.0},
            {"date": "2024-01-03", "change_pct": -1.0},
            {"date": "2024-01-04", "change_pct": -3.0},
            {"date": "2024-01-05", "change_pct": 2.0},  # 空头锁定（未到 +4）
            {"date": "2024-01-08", "change_pct": 4.5},  # >4 ⇒ 做多
        ]
        a = bf._amv_regime_from_records(records)
        b = bf._amv_regime_from_records(list(reversed(records)))  # 内部排序无关输入序
        assert a == b
        assert a["2024-01-05"] == "空头" and a["2024-01-08"] == "做多"
