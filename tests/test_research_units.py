"""研究单元的结构约束。

`B1_BACKTEST_FINDINGS.md`（2456 行、按时间追加）2026-08-06 拆成 17 个单元。
拆分动机：同一主题的结论散落在十几处，而**后面的常常推翻前面的**
⇒ 按时间读会先读到已被作废的数字。

这份测试钉住三件事：①每个单元都有【主题/目标/结论】三段（这是拆分的组织原则）；
②每个单元都标了证据等级与状态（**没等级的结论就是猜测**）；
③README 主图覆盖全部单元，不许有孤儿文件。
"""
from __future__ import annotations

import pathlib
import re

import pytest

RESEARCH = pathlib.Path(__file__).resolve().parents[1] / "00_governance" / "research"
UNITS = sorted(p for p in RESEARCH.glob("R*.md") if p.name != "README.md")
LEVELS = {"L0", "L1", "L2", "L3", "L4"}


def test_units_exist():
    assert len(UNITS) == 17, f"预期 17 个研究单元，实际 {len(UNITS)}：{[p.name for p in UNITS]}"


@pytest.mark.parametrize("path", UNITS, ids=lambda p: p.name)
class TestUnitStructure:
    def test_has_topic_goal_conclusion(self, path):
        """【主题：目标：结论】是拆分的组织原则，缺一段就退化成又一份日志。"""
        s = path.read_text(encoding="utf-8")
        for sec in ("## 主题", "## 目标", "## 结论"):
            assert sec in s, f"{path.name} 缺 {sec}"
        # 三段必须按序出现，且都在「证据与过程」之前
        i_t, i_g, i_c = s.index("## 主题"), s.index("## 目标"), s.index("## 结论")
        assert i_t < i_g < i_c, f"{path.name} 三段顺序不对"
        assert "## 证据与过程" in s, f"{path.name} 缺证据段"
        assert i_c < s.index("## 证据与过程"), f"{path.name} 结论必须在证据之前（给人读的在前）"

    def test_has_evidence_level(self, path):
        """**没有证据等级的结论就是猜测。** L3 及以下不得进 live。"""
        s = path.read_text(encoding="utf-8")
        m = re.search(r"\*\*证据等级\*\*：(L[0-4])", s)
        assert m, f"{path.name} 头部缺 **证据等级**：L0~L4"
        assert m.group(1) in LEVELS

    def test_has_state_and_deps(self, path):
        s = path.read_text(encoding="utf-8")
        assert "**状态**：" in s, f"{path.name} 缺状态标记"
        assert "**依赖**：" in s, f"{path.name} 缺依赖说明（主图靠它对账）"

    def test_conclusion_is_not_empty(self, path):
        """结论段不得是占位符——空结论比没有更糟，它看起来像有结论。"""
        s = path.read_text(encoding="utf-8")
        body = s[s.index("## 结论") + len("## 结论"):s.index("---\n\n## 证据与过程")]
        assert len(body.strip()) >= 80, f"{path.name} 结论段过短（{len(body.strip())} 字）"

    def test_linked_from_readme(self, path):
        """孤儿单元＝写了没人读。"""
        readme = (RESEARCH / "README.md").read_text(encoding="utf-8")
        assert path.name in readme, f"{path.name} 未在 README 主图/总账里出现"


class TestReadmeIndex:
    def _readme(self):
        return (RESEARCH / "README.md").read_text(encoding="utf-8")

    def test_has_main_graph(self):
        s = self._readme()
        assert "```mermaid" in s, "主图是索引的核心，不能只有表格"
        assert "flowchart" in s

    def test_graph_marks_refutation(self):
        """**推翻关系必须在图上可见**——R11 推翻 R10 的可用性前提是当前最要紧的一条，
        若图上看不出来，读者会按顺序读完 R10 就以为有可用策略。"""
        s = self._readme()
        assert "==>" in s, "推翻关系要用粗箭头区别于普通依赖"
        assert "推翻" in s

    def test_defines_evidence_levels(self):
        s = self._readme()
        for lv in sorted(LEVELS):
            assert f"**{lv}**" in s, f"README 未定义 {lv}"

    def test_old_numbering_map_present(self):
        """旧编号（结论#N / §N）在正文里仍被引用，必须给对照表，否则读者查无此章。"""
        s = self._readme()
        assert "旧编号对照表" in s
        for old in ("结论#8", "结论#11", "§3"):
            assert old in s, f"对照表缺 {old}"

    def test_no_dead_link_to_split_source(self):
        """原文件已删（历史在 git），README 不得把它当活链接。"""
        s = self._readme()
        assert not (RESEARCH / "B1_BACKTEST_FINDINGS.md").exists()
        # 提到原文件是可以的（说明来历），但必须点明它在 git 历史里而非现存文件
        assert "B1_BACKTEST_FINDINGS" not in s or "git 历史" in s, \
            "提到原文件必须说明它已在 git 历史里，否则读者会去找一个不存在的文件"


class TestNoStaleReferences:
    """全仓不得再有指向已删单文件的引用（拆分时改了 18 处）。"""

    def test_repo_has_no_findings_reference_outside_research(self):
        root = RESEARCH.parents[1]
        bad = []
        for p in root.rglob("*"):
            if p.suffix not in {".py", ".md", ".json", ".cmd"}:
                continue
            # 排除 research/ 目录（单元头部指向 git 历史是有意的）与本测试自身
            if ".git" in p.parts or RESEARCH == p.parent or p.name == pathlib.Path(__file__).name:
                continue
            try:
                s = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, ln in enumerate(s.splitlines(), 1):
                if "B1_BACKTEST_FINDINGS" in ln:
                    bad.append(f"{p.relative_to(root)}:{i}")
        assert not bad, ("这些引用指向已拆分删除的文件，请改到具体单元：\n  "
                         + "\n  ".join(bad))
