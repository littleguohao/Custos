# -*- coding: utf-8 -*-
"""判定段批次锁（TODO #95）钉测 —— 状态机全路径 / 配置哈希 / 门禁豁免 /
7+1 终端生产方向（无批拒、带批过 + mark_read 落 tmp 登记文件）。

conftest autouse 已把 ``judgment_batch.REGISTRY`` 改道 tmp（真实登记文件
绝不被测试写）。
"""

from __future__ import annotations

import argparse
import json

import pytest

from custos.research import judgment_batch as jb


def _open2(**kw):
    """标准两候选批次（b1: cand_a + cand_b）。"""
    kw.setdefault("path", None)
    jb.open_batch(
        "b1",
        [
            {"id": "cand_a", "config_hash": "ha", "unit": "R-x", "note": ""},
            {"id": "cand_b", "config_hash": "hb", "unit": "R-y", "note": ""},
        ],
        **kw,
    )


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_open_duplicate_batch_rejected(self):
        _open2()
        with pytest.raises(ValueError, match="只增不复用"):
            _open2()

    def test_open_empty_roster_rejected(self):
        with pytest.raises(ValueError, match="名册为空"):
            jb.open_batch("b0", [])

    def test_open_missing_hash_rejected(self):
        with pytest.raises(ValueError, match="缺 config_hash"):
            jb.open_batch(
                "b0", [{"id": "c", "config_hash": "", "unit": "", "note": ""}]
            )

    def test_five_way_rejections(self):
        _open2()
        # ① 批不存在
        with pytest.raises(ValueError, match="不存在"):
            jb.check_read("nope", "cand_a", "ha")
        # ② 候选不在批
        with pytest.raises(ValueError, match="不在批次"):
            jb.check_read("b1", "ghost", "ha")
        # ③ 哈希不匹配
        with pytest.raises(ValueError, match="哈希不匹配"):
            jb.check_read("b1", "cand_a", "tampered")
        # ④ 候选已读（一批只读一次）
        jb.mark_read("b1", "cand_a", tool="t", tag="x")
        with pytest.raises(ValueError, match="只读一次"):
            jb.check_read("b1", "cand_a", "ha")
        # ⑤ 批已关（手动关后再读）
        jb.close_batch("b1")
        with pytest.raises(ValueError, match="批外不得零散补读"):
            jb.check_read("b1", "cand_b", "hb")

    def test_read_all_auto_closes(self):
        _open2()
        jb.mark_read("b1", "cand_a", tool="t", tag="x")
        assert jb.status("b1")["b1"]["closed_at"] is None  # 未读完不关
        jb.mark_read("b1", "cand_b", tool="t", tag="x")
        st = jb.status("b1")["b1"]
        assert st["closed_at"] is not None  # 末位候选读完自动关批
        assert st["unread"] == []

    def test_close_twice_rejected(self):
        _open2()
        jb.close_batch("b1")
        with pytest.raises(ValueError, match="重复关批"):
            jb.close_batch("b1")

    def test_mark_read_unknown_candidate_rejected(self):
        _open2()
        with pytest.raises(ValueError, match="不在批次"):
            jb.mark_read("b1", "ghost", tool="t", tag="x")

    def test_events_persisted_append_only(self):
        _open2()
        jb.mark_read("b1", "cand_a", tool="t", tag="x")
        rows = [
            json.loads(ln)
            for ln in jb.REGISTRY.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert [r["ev"] for r in rows] == ["open", "read"]
        assert rows[0]["m"] == 2 and rows[1]["candidate_id"] == "cand_a"


class TestCli:
    def test_open_close_status_roundtrip(self, capsys):
        assert (
            jb.main(["open", "--batch-id", "b1", "--candidate", "c1:deadbeef:R36:n"])
            == 0
        )
        assert jb.main(["status"]) == 0
        assert "b1" in capsys.readouterr().out
        assert jb.main(["close", "--batch-id", "b1"]) == 0
        assert jb.main(["status", "--batch-id", "b1"]) == 0

    def test_open_without_candidates_exits_2(self):
        assert jb.main(["open", "--batch-id", "b1"]) == 2

    def test_close_unknown_batch_exits_2(self):
        assert jb.main(["close", "--batch-id", "nope"]) == 2


# ---------------------------------------------------------------------------
# 配置哈希
# ---------------------------------------------------------------------------


class TestConfigHash:
    def _ns(self, **kw):
        base = {"start": "2000-01-01", "count": 1000, "stop_pct": 8.0}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_stable_and_sensitive(self):
        h1 = jb.config_hash("t", self._ns())
        h2 = jb.config_hash("t", self._ns())
        assert h1 == h2 and len(h1) == 16
        h3 = jb.config_hash("t", self._ns(stop_pct=9.0))  # 改一个参数 ⇒ 哈希变
        assert h3 != h1

    def test_bookkeep_keys_and_none_excluded(self):
        a = self._ns(out="x.json", batch_id="b1", candidate_id="c", note_none=None)
        b = self._ns()
        assert jb.config_hash("t", a) == jb.config_hash("t", b)

    def test_tool_prefix_in_print(self, capsys):
        ns = self._ns()
        rc = jb.print_config_hash_and_exit(ns, "mytool")
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out == f"mytool:{jb.config_hash('mytool', ns)}"


# ---------------------------------------------------------------------------
# 门禁（gate_judgment_read）
# ---------------------------------------------------------------------------


class TestGate:
    def _ap(self):
        return argparse.ArgumentParser()

    def _args(self, **kw):
        base = {"batch_id": "", "candidate_id": "", "count": 1000}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_synthetic_never_gated(self):
        # 合成运行（注入件非 None）不查批——无批次也过
        assert (
            jb.gate_judgment_read(
                self._args(),
                self._ap(),
                synthetic=True,
                judgment_span=("2017-01-01", "2021-12-31"),
                tool="t",
            )
            is False
        )

    def test_legacy_window_not_gated(self):
        # legacy 窗（2022~2024 / 2024-08~2026-09）与判定段不相交 ⇒ 不触发门
        for span in (("2022-01-01", "2024-07-31"), ("2024-08-01", "2026-09-04")):
            assert (
                jb.gate_judgment_read(
                    self._args(),
                    self._ap(),
                    synthetic=False,
                    judgment_span=span,
                    tool="t",
                )
                is False
            )

    def test_production_without_batch_errors(self):
        with pytest.raises(SystemExit) as exc:
            jb.gate_judgment_read(
                self._args(),
                self._ap(),
                synthetic=False,
                judgment_span=("2017-01-01", "2021-12-31"),
                tool="t",
            )
        assert exc.value.code == 2

    def test_production_with_batch_passes_and_stashes(self):
        args = self._args(batch_id="b1", candidate_id="cand_a")
        h = jb.config_hash("t", args)
        jb.open_batch(
            "b1", [{"id": "cand_a", "config_hash": h, "unit": "R", "note": ""}]
        )
        assert (
            jb.gate_judgment_read(
                args,
                self._ap(),
                synthetic=False,
                judgment_span=("2017-01-01", "2021-12-31"),
                tool="t",
            )
            is True
        )
        assert args._judgment_gated is True
        # 收尾补 mark_read ⇒ 候选变已读
        jb.mark_read("b1", "cand_a", tool="t", tag="x")
        assert "cand_a" in jb.status("b1")["b1"]["read"]

    def test_wrong_hash_rejected(self):
        args = self._args(batch_id="b1", candidate_id="cand_a")
        jb.open_batch(
            "b1", [{"id": "cand_a", "config_hash": "deadbeef", "unit": "R", "note": ""}]
        )
        with pytest.raises(SystemExit) as exc:
            jb.gate_judgment_read(
                args,
                self._ap(),
                synthetic=False,
                judgment_span=("2018-01-01", "2020-12-31"),  # 判定子窗也触发（相交制）
                tool="t",
            )
        assert exc.value.code == 2
