# -*- coding: utf-8 -*-
"""provenance（研究产物溯源块，v0.325，owner 方法论 review #8）钉测。

锁的契约：build 块九字段（git_sha/git_dirty/objective_version/unit/
criteria_version/pre_reg_blob_hash/universe_sha256/data_last_date/cmdline）；
git blob hash 与 `git hash-object` 一致；宇宙 sha256 内容寻址；last_date_of
取全宇宙尾行最大日期（占位/缺 df 不炸）；溯源失败字段 None 不炸研究。
"""

from __future__ import annotations

import argparse
import subprocess

import pandas as pd
import pytest

from custos.core.paths import BASE
from custos.research import provenance as pv
from custos.research import strategy_grid as sg


class TestGitBits:
    def test_sha_and_dirty(self):
        sha = pv.git_sha()
        assert sha and len(sha) == 10  # 本仓库内必有 sha
        assert pv.git_dirty() in (True, False)

    def test_blob_hash_matches_git(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("判据跑数前写死\n", encoding="utf-8")
        expect = subprocess.run(
            ["git", "hash-object", str(f)], capture_output=True, text=True, check=True
        ).stdout.strip()
        assert pv.git_blob_hash(f) == expect
        assert pv.git_blob_hash(tmp_path / "none.md") is None


class TestFileShaAndLastDate:
    def test_sha256_content_addressed(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("600000\n600001\n", encoding="utf-8")
        b.write_text("600000\n600001\n", encoding="utf-8")
        assert pv.file_sha256(a) == pv.file_sha256(b)
        b.write_text("600002\n", encoding="utf-8")
        assert pv.file_sha256(a) != pv.file_sha256(b)
        assert pv.file_sha256(tmp_path / "none.txt") is None

    def test_last_date_of(self):
        df = pd.DataFrame({"date": pd.to_datetime(["2024-01-02", "2024-06-03"])})
        per_code = {
            "A": {"df": df},
            "B": {"df": None},  # 占位跳过
            "C": {"no_df": 1},  # 缺键跳过
        }
        assert pv.last_date_of(per_code) == "2024-06-03"
        assert pv.last_date_of({}) is None


class TestBuild:
    def test_full_block(self, tmp_path):
        codes = tmp_path / "codes.txt"
        codes.write_text("600000\n", encoding="utf-8")
        doc = tmp_path / "R99_x.md"
        doc.write_text("# 预注册\n", encoding="utf-8")
        args = argparse.Namespace(
            codes_file=str(codes), cmdline="--tag t1 --codes-file x.txt"
        )
        blk = pv.build(
            args,
            unit="R99",
            criteria_version="v0.1",
            pre_reg_doc=doc,
            data_last_date="2026-09-04",
        )
        assert blk["unit"] == "R99"
        assert blk["criteria_version"] == "v0.1"
        assert blk["objective_version"] == sg.OBJECTIVE_VERSION
        assert blk["git_sha"] and blk["git_dirty"] in (True, False)
        assert blk["pre_reg_blob_hash"] == pv.git_blob_hash(doc)
        assert blk["universe_sha256"] == pv.file_sha256(codes)
        assert blk["data_last_date"] == "2026-09-04"
        assert blk["cmdline"] == "--tag t1 --codes-file x.txt"

    def test_missing_pieces_are_none_not_crash(self):
        blk = pv.build(argparse.Namespace(codes_file="", cmdline=None), unit="R99")
        assert blk["universe_sha256"] is None
        assert blk["pre_reg_blob_hash"] is None
        assert blk["data_last_date"] is None
        assert blk["cmdline"] is None
        assert blk["objective_version"] == sg.OBJECTIVE_VERSION
