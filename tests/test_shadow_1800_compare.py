# -*- coding: utf-8 -*-
"""shadow_1800_compare 的抽取/对比钉测（合成候选表，无数据无 worktree）。

被测对象是 scripts/dev 下的运维脚本（非包内模块）——importlib 按路径加载；
只钉纯函数 extract_label_sections/compare_tables（编排层在生产机跑）。
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "dev" / "shadow_1800_compare.py"
)
spec = importlib.util.spec_from_file_location("shadow_1800_compare", SCRIPT)
shadow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shadow)


def _table(label_cell_a="4/9 QG·RS·B2", label_cell_b="1/9 QG", overview="hit 名单甲"):
    """迷你候选表：🏷️ 段 + 两行 A 池（列序与真表同构：标注=倒数第 4 列）。"""
    return f"""# 公式选股备选池｜2026-09-16

## ⭐ 今日信号一览

- **可买（A + 市场/基本面/技术三面共振）**：600000 甲

## 🏷️ 信号标注一览（研究因子·只标注，不影响上方分层）

{overview}

## A 池（2 只）

| 代码 | 名称 | 公式命中 | 模式标签 | 技术分 | 标注 | 分层 | 建议止损位 | next_step |
|---|---|---|---|---:|---|---|---:|---|
| 600000 | 甲 | KDJ_J_LOW | 低J | 61 | {label_cell_a} | A | 10.0 | buy_review |
| 600001 | 乙 | POOL_ZHENDANG | - | 55 | {label_cell_b} | A | 9.5 | buy_review |

## B 池（0 只）

（空）
"""


class TestExtractLabelSections:
    def test_overview_and_pool_cells(self):
        got = shadow.extract_label_sections(_table())
        assert (
            got["overview"] == "hit名单甲"
        )  # overview 归一后不含空白（排版归一，见 extractor docstring）
        assert got["pool"] == {"600000": "4/9 QG·RS·B2", "600001": "1/9 QG"}

    def test_nbsp_normalized(self):
        md = _table(label_cell_a="4/9&nbsp;QG·RS")
        got = shadow.extract_label_sections(md)
        assert got["pool"]["600000"] == "4/9 QG·RS"

    def test_missing_overview_and_empty_pool(self):
        got = shadow.extract_label_sections("# 空表\n\n（今日无）\n")
        assert got == {"overview": "", "pool": {}, "bj_excluded": 0}

    def test_header_and_separator_rows_filtered(self):
        got = shadow.extract_label_sections(_table())
        assert "|" not in got["pool"].get("600000", "")
        assert "---" not in got["pool"]


class TestCompareTables:
    def test_identical(self):
        assert shadow.compare_tables(_table(), _table()) == []

    def test_label_cell_diff_reported(self):
        diffs = shadow.compare_tables(_table(), _table(label_cell_a="3/9 QG·RS"))
        assert any("600000" in d and "标注列不一致" in d for d in diffs)

    def test_overview_diff_reported(self):
        diffs = shadow.compare_tables(_table(), _table(overview="hit 名单乙"))
        assert any("信号标注一览" in d for d in diffs)

    def test_pool_membership_diff_reported(self):
        md = _table()
        diffs = shadow.compare_tables(md, md.replace("600001", "600002"))
        assert any("缺票" in d for d in diffs)

    @pytest.mark.parametrize("nb", ["4/9&nbsp;QG·RS·B2", "4/9 QG·RS·B2"])
    def test_nbsp_variant_counts_as_identical(self, nb):
        assert shadow.compare_tables(_table(), _table(label_cell_a=nb)) == []


class TestEnsureDataLink:
    """生产机 WinError 1314 驱动的链接机制钉测（编排层全部 monkeypatch，不建真链）。"""

    def test_junction_fallback_when_symlink_denied(self, tmp_path, monkeypatch):
        """symlink 无特权 ⇒ 退 junction（cmd mklink /J），目标取 resolve 后真路径。"""
        target = tmp_path / "real_data"
        target.mkdir()
        wt_data = tmp_path / "wt" / "data"

        def _deny(*a, **k):
            raise OSError(1314, "privilege not held")

        monkeypatch.setattr(shadow.os, "symlink", _deny)
        calls = []

        class _R:
            returncode = 0
            stderr = ""

        monkeypatch.setattr(
            shadow, "_run", lambda cmd, cwd, timeout=3600: (calls.append(cmd), _R())[1]
        )
        mech = shadow._ensure_data_link(target, wt_data)
        assert mech == "junction"
        assert calls[0][:4] == ["cmd", "/c", "mklink", "/J"]
        assert str(wt_data) in calls[0]
        assert calls[0][-1] == str(target.resolve())

    def test_junction_never_rmtree(self, tmp_path, monkeypatch):
        """红线钉测：重解析点（junction/软链）只 _remove_link 摘点，绝不 rmtree 穿链。"""
        wt_data = tmp_path / "wt" / "data"
        monkeypatch.setattr(shadow, "_link_kind", lambda p: "link")
        removed = []
        monkeypatch.setattr(shadow, "_remove_link", lambda p: removed.append(p))

        def _boom(p):
            raise AssertionError("rmtree 被调用——穿链删共享数据的红线被触发")

        monkeypatch.setattr(shadow.shutil, "rmtree", _boom)
        monkeypatch.setattr(shadow.os, "symlink", lambda *a, **k: None)
        # wt_data 不存在 ⇒ resolve≠target ⇒ 走「摘点重建」分支
        mech = shadow._ensure_data_link(tmp_path / "real_data", wt_data)
        assert mech == "symlink"
        assert removed == [wt_data]

    def test_reuse_existing_link(self, tmp_path):
        """已存在且指向正确的链接 ⇒ reused，不动文件系统。"""
        target = tmp_path / "real"
        target.mkdir()
        link = tmp_path / "wt_data"
        try:
            import os

            os.symlink(target, link, target_is_directory=True)
        except OSError:
            pytest.skip("无 symlink 特权（生产机外）")
        assert shadow._ensure_data_link(target, link) == "reused"

    def test_overview_cjk_padding_is_formatting_only(self):
        """🏷️ 段内表格的全角对齐 padding（v0.235 fmt 排版变更：CJK 字间空格）
        属排版差异不算不一致——剔除全部空白后语义相同。"""
        padded = _table(
            overview="|     因 子     |   命 中   |\n|---|---|\n| QG | 3/9 |"
        )
        plain = _table(overview="| 因子 | 命中 |\n|---|---|\n| QG | 3/9 |")
        assert shadow.compare_tables(padded, plain) == []

    def test_overview_content_diff_still_reported(self):
        """归一不能吃掉真差异：命中计数变了必须报。"""
        a = _table(overview="| 因子 | 命中 |\n|---|---|\n| QG | 3/9 |")
        b = _table(overview="| 因子 | 命中 |\n|---|---|\n| QG | 4/9 |")
        assert any("信号标注一览" in d for d in shadow.compare_tables(a, b))


_BJ_ROW = "| 830799 | 丙 | KDJ_J_LOW | 低J | 50 | {label} | A | 9.0 | buy_review |\n"


def _table_bj(label="0/9"):
    """在 A 池追加一行北交所票（920/8/4 前缀 ⇒ BJ，对照范围外）。"""
    return _table().replace(
        "## B 池（0 只）", _BJ_ROW.format(label=label) + "\n## B 池（0 只）"
    )


class TestBJExclusion:
    """v0.295：北交所票不进对照范围（v0.257 起现行宇宙含 BJ，迁移前基线无
    BJ 评分口径——不滤则每日恒差、闭环判据永不可达，09-24 实差 14 只全是 BJ）。"""

    def test_bj_pool_row_not_diffed(self):
        """现行表多出的 BJ 行不产生缺票/标注差异，且剔除数如实记。"""
        got = shadow.extract_label_sections(_table_bj())
        assert got["bj_excluded"] == 1
        assert "830799" not in got["pool"]
        assert shadow.compare_tables(_table_bj(), _table()) == []

    def test_bj_label_cell_diff_ignored(self):
        """两边都有同一 BJ 票但标注不同 ⇒ 仍不算差异（整个出对照范围）。"""
        assert shadow.compare_tables(_table_bj("1/9 QG"), _table_bj("9/9 全中")) == []

    def test_bj_overview_line_dropped(self):
        """🏷️ 段内含 BJ 码的行整行剔除；非 BJ 行差异仍报。"""
        a = _table(overview="hit 名单甲\n- 830799 丙：BJ 标注")
        assert shadow.compare_tables(a, _table()) == []
        b = _table(overview="hit 名单甲\n- 830799 丙：BJ 标注\nhit 名单丁")
        assert any("信号标注一览" in d for d in shadow.compare_tables(a, b))

    def test_non_bj_diff_unaffected(self):
        """滤 BJ 不吃掉沪深真差异（600/000 行照常比对）。"""
        diffs = shadow.compare_tables(_table_bj(), _table(label_cell_a="3/9 QG·RS"))
        assert any("600000" in d and "标注列不一致" in d for d in diffs)
