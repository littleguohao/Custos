# -*- coding: utf-8 -*-
"""window_usage（判定窗/pre2019 使用台账，v0.321）+ 前向 holdout 冻结钉测。

锁的契约：record_use 返回「第 k 次」（分窗独立计数）、append-only + 每日
快照、写失败不炸研究（旁路治理数据）、usage_note 判读指引、conftest
改道不碰真实台账；`_check_windows` 对触及 2026-09-05 起前向 holdout
冻结段的窗口硬拒绝（owner 方法论 review #1②），边界日 2026-09-04 放行。
"""

from __future__ import annotations

import json

import pytest

from custos.research import factor_exit_study as fes
from custos.research import window_usage as wu


def _read(path):
    return [
        json.loads(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


class TestRecordUse:
    def test_k_increments_and_windows_separate(self, tmp_path):
        p = tmp_path / "led.jsonl"
        assert wu.record_use("R39", "judgment", "t1", "x", path=p) == 1
        assert wu.record_use("R40", "judgment", "t2", "x", path=p) == 2
        assert wu.record_use("R37-C5", "pre2019", "t3", "x", path=p) == 1
        assert wu.count_uses("judgment", path=p) == 2
        assert wu.count_uses("pre2019", path=p) == 1
        rows = _read(p)
        assert [r["unit"] for r in rows] == ["R39", "R40", "R37-C5"]
        assert all(r["schema"] == wu.SCHEMA for r in rows)

    def test_daily_snapshot_created(self, tmp_path):
        p = tmp_path / "led.jsonl"
        wu.record_use("R39", "judgment", "t1", "x", path=p)
        wu.record_use("R40", "judgment", "t2", "x", path=p)
        baks = list(tmp_path.glob("led.jsonl.bak_*"))
        assert len(baks) == 1
        assert len(_read(baks[0])) == 1  # 快照=第二次写入前的内容
        assert len(_read(p)) == 2  # 台账只增不减

    def test_write_failure_does_not_crash(self, tmp_path, capsys):
        d = tmp_path / "as_dir"
        d.mkdir()  # 用目录当路径 ⇒ open 失败
        k = wu.record_use("R39", "judgment", "t1", "x", path=d)
        assert k == 0  # 写不进去 ⇒ 返回当时计数（0），不炸研究
        assert "判定窗台账写入失败" in capsys.readouterr().err

    def test_default_path_is_redirected(self):
        """不显式传 path ⇒ 走 conftest 改道的 tmp（不碰真实 governance 台账）。"""
        k = wu.record_use("RTEST", "judgment", "t", "x")
        assert k == 1
        assert "governance" not in str(wu.LEDGER)

    def test_usage_note_content(self):
        note = wu.usage_note("R39", "judgment", 7)
        assert "第 7 次被读" in note
        assert "2024-08-01~2026-09-04" in note
        assert "打折判读" in note


class TestForwardHoldout:
    """前向 holdout 冻结（v0.321 #1②）：判定窗反复使用已接近第二个挖掘窗
    ——2026-09 以后的新数据保留为下一轮判定窗，任何研究不得使用。"""

    def _args(self, ap, j_end):
        return ap.parse_args(
            [
                "--tag",
                "t",
                "--mining-start",
                "2022-01-01",
                "--mining-end",
                "2024-07-31",
                "--judgment-start",
                "2024-08-01",
                "--judgment-end",
                j_end,
            ]
        )

    def test_holdout_intersection_rejected(self):
        ap = fes._build_parser()
        with pytest.raises(SystemExit):
            fes._check_windows(self._args(ap, "2026-09-05"), ap)

    def test_beyond_holdout_rejected(self):
        ap = fes._build_parser()
        with pytest.raises(SystemExit):
            fes._check_windows(self._args(ap, "2027-01-01"), ap)

    def test_judgment_end_at_boundary_passes(self):
        ap = fes._build_parser()
        fes._check_windows(self._args(ap, "2026-09-04"), ap)  # 不炸即过
