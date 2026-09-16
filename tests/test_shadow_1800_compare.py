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
        assert got["overview"] == "hit 名单甲"
        assert got["pool"] == {"600000": "4/9 QG·RS·B2", "600001": "1/9 QG"}

    def test_nbsp_normalized(self):
        md = _table(label_cell_a="4/9&nbsp;QG·RS")
        got = shadow.extract_label_sections(md)
        assert got["pool"]["600000"] == "4/9 QG·RS"

    def test_missing_overview_and_empty_pool(self):
        got = shadow.extract_label_sections("# 空表\n\n（今日无）\n")
        assert got == {"overview": "", "pool": {}}

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
