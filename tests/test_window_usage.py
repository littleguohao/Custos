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
        assert wu.record_use("R39", "judgment_2017_2021", "t1", "x", path=p) == 1
        assert wu.record_use("R40", "judgment_2017_2021", "t2", "x", path=p) == 2
        assert wu.count_uses("judgment_2017_2021", path=p) == 2
        assert wu.count_uses("pre2019", path=p) == 0
        rows = _read(p)
        assert [r["unit"] for r in rows] == ["R39", "R40"]
        assert all(r["schema"] == wu.SCHEMA for r in rows)

    def test_frozen_window_keys_rejected(self, tmp_path):
        """v2 布局（TODO #90）：旧键只判读历史行，新写入 fail-closed。"""
        p = tmp_path / "led.jsonl"
        for frozen in ("judgment", "pre2019"):
            with pytest.raises(ValueError, match="已冻结"):
                wu.record_use("R39", frozen, "t", "x", path=p)
        assert not p.exists()  # 拒绝在写入前，不落任何行
        # 历史行判读不受影响（count_uses/usage_note 只读）
        assert wu.count_uses("judgment", path=p) == 0
        assert "2024-08-01~2026-09-04" in wu.usage_note("R39", "judgment", 1)

    def test_daily_snapshot_created(self, tmp_path):
        p = tmp_path / "led.jsonl"
        wu.record_use("R39", "judgment_2017_2021", "t1", "x", path=p)
        wu.record_use("R40", "judgment_2017_2021", "t2", "x", path=p)
        baks = list(tmp_path.glob("led.jsonl.bak_*"))
        assert len(baks) == 1
        assert len(_read(baks[0])) == 1  # 快照=第二次写入前的内容
        assert len(_read(p)) == 2  # 台账只增不减

    def test_write_failure_does_not_crash(self, tmp_path, capsys):
        d = tmp_path / "as_dir"
        d.mkdir()  # 用目录当路径 ⇒ open 失败
        k = wu.record_use("R39", "judgment_2017_2021", "t1", "x", path=d)
        assert k == 0  # 写不进去 ⇒ 返回当时计数（0），不炸研究
        assert "判定窗台账写入失败" in capsys.readouterr().err

    def test_default_path_is_redirected(self):
        """不显式传 path ⇒ 走 conftest 改道的 tmp（不碰真实 governance 台账）。"""
        k = wu.record_use("RTEST", "judgment_2017_2021", "t", "x")
        assert k == 1
        assert "governance" not in str(wu.LEDGER)

    def test_usage_note_content(self):
        note = wu.usage_note("R39", "judgment", 7)
        assert "第 7 次被读" in note
        assert "2024-08-01~2026-09-04" in note
        assert "打折判读" in note
        # 下限声明（v0.341）：台账 2026-10 起计，此前约 13 单元已反复读过
        assert "k 只是下限" in note and "2026-10 起计" in note


class TestSyntheticGuard:
    """合成数据守卫（v0.334，owner 拍板台账**重新入库**）：台账回答「真实
    判定窗被读几次」⇒ 入库跨机 git 同步（机器本地各数各的、k 系统性低估
    ——owner 2026-10-10 复核纠正）；写入路径加守卫，任何注入件非 None ⇒
    不写——pytest 外直跑注入路径也混不进假记录（2026-10-10 事故根因）。"""

    def test_synthetic_true_skips_write(self, tmp_path):
        p = tmp_path / "led.jsonl"
        got = wu.record_use("R39", "judgment", "t", "x", synthetic=True, path=p)
        assert got is None
        assert not p.exists()  # 台账路径零改动

    def test_usage_note_synthetic(self):
        note = wu.usage_note("R39", "judgment", None)
        assert "合成" in note and "未入台账" in note

    def test_injected_run_study_leaves_ledger_untouched(self, tmp_path, monkeypatch):
        """钉死事故路径：注入件（warm_fn/replay_fn）跑 run_study ⇒ 真实台账
        路径零改动、k=None 注记「未入台账」。"""
        import test_plan_rules_replay as pxt

        ledger = tmp_path / "real_ledger.jsonl"
        monkeypatch.setattr(wu, "LEDGER", ledger)
        rep = pxt._run(
            pxt._mk_per_code(), pxt._replay_candidate, n_random=2, c4_min_pool=2
        )
        assert rep["window_usage"]["k"] is None
        assert "未入台账" in rep["window_usage"]["note"]
        assert not ledger.exists()


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


class TestTrackedLedgerKeys:
    """入库台账（v0.334 起 git 同步）的行格式钉——window 字段必须只放
    窗名（count_uses 按键匹配）：r39_a2/r40_a1 补登行曾把区间写进
    window（judgment(2024-08-01~…)）导致 k 数不到（v0.338 已修正）。"""

    def test_window_key_is_bare_name(self):
        ledger = wu.RESEARCH_DIR / "window_usage.jsonl"
        if not ledger.is_file():
            pytest.skip("台账未入库（该机无真实台账）")
        for ln in ledger.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            row = json.loads(ln)
            assert row.get("schema") == wu.SCHEMA, row
            assert row.get("window") in wu.WINDOW_SPAN, (
                f"window 键必须是窗名（{sorted(wu.WINDOW_SPAN)}），"
                f"区间写进 purpose：{row.get('window')!r}"
            )
