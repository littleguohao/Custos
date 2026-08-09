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


class TestRerunMarks:
    """结论不稳的单元必须**标出来**，且标记与索引不许脱节。

    2026-08-06 数据层 review 查出三件事波及已有结论：
    ① qlib `2021_2026` 是加法调整（收益放大 13~21%）已弃用；
    ② 2021 年后退市的票一只都没有 ⇒ 近期窗口无法去幸存者偏差；
    ③ 基准边际随样本量崩塌（R11）。
    受影响的单元头部带 `**重跑**：Pn`，README 有对应清单。

    为什么要用测试钉住：**结论不稳这件事最容易随时间被遗忘** ——
    半年后有人读 R4 会看到「✅ 成立 L4」就直接用，而不知道它的 OOS 资格还没重新取得。
    """

    PRI = ("P0", "P1", "P2")

    def _marked(self):
        out = {}
        for p in UNITS:
            s = p.read_text(encoding="utf-8")
            m = re.search(r"\*\*重跑\*\*：\*\*(P[0-2])\*\*", s)
            if m:
                out[p.name] = m.group(1)
        return out

    def test_expected_units_are_marked(self):
        """这些单元的结论依赖已失效的口径，必须带重跑标记。

        R4 于 2026-08-09 完成重跑（16 格 2×2 × 4 窗）后移出本集合——
        重取完成的单元应摘标记，而不是永久挂着。
        R9/R10 同日完成（s3000 已实现口径全扫描）后移出。
        """
        want = {
            "R1_core_framework.md", "R2_selection_price_volume.md",
            "R3_selection_discriminability_recall.md",
        }
        got = set(self._marked())
        assert want <= got, f"缺重跑标记：{sorted(want - got)}"

    def test_mark_has_reason_and_method(self):
        """只标「要重跑」没用——必须写清**为什么不稳**与**用什么口径重跑**。"""
        for name in self._marked():
            s = (RESEARCH / name).read_text(encoding="utf-8")
            assert "**重跑口径**：" in s, f"{name} 有重跑标记但没写重跑口径"

    def test_readme_lists_every_marked_unit(self):
        """标了却不在清单里 = 没人会去跑。"""
        readme = (RESEARCH / "README.md").read_text(encoding="utf-8")
        assert "重跑清单" in readme
        seg = readme[readme.index("重跑清单"):]
        for name in self._marked():
            assert name in seg, f"{name} 未列入 README 重跑清单"

    def test_p0_is_the_live_dependency(self):
        """P0 的定义是「live 正在依赖它」。

        R4（择时腿）曾是唯一 P0，2026-08-09 重跑完成后摘标记 ⇒ 当前 P0 为空。
        若哪天 P0 变多，说明有更多 live 决策建立在待重跑的结论上 —— 那是要立即处理的信号。
        """
        p0 = [k for k, v in self._marked().items() if v == "P0"]
        assert p0 == [], f"P0 集合变了：{p0}"

    def test_level_and_state_not_contradicting_mark(self):
        """带重跑标记的单元，**不许**同时宣称干净的 L4 + ✅成立 —— 那会误导读者。"""
        bad = []
        for name in self._marked():
            s = (RESEARCH / name).read_text(encoding="utf-8")
            head = s[:s.index("## 主题")]
            if re.search(r"\*\*证据等级\*\*：L4\s*　", head) and "**状态**：✅" in head:
                bad.append(name)
        assert not bad, f"这些单元的等级/状态与重跑标记自相矛盾：{bad}"


class TestStrategyDocsFlagUnstableNumbers:
    """策略层文档引用了研究量级数字，必须指向重跑清单。

    ⚠️ 这是**跨目录**的约束：research/ 的结论被推翻时，strategy/ 里抄过去的数字
    不会自动更新。2026-08-06 实查发现两处：
    `90_research_summary.md` 写「幸存者已部分去除(含退市 qlib)」（近期窗口其实没去除）、
    `01_swing_rules.md` 写「含退市跨年 OOS」（对 2021-08 后的窗口不成立）。
    """

    DOCS = ("00_governance/strategy/b1/90_research_summary.md",
            "00_governance/strategy/b1/01_swing_rules.md")

    @pytest.mark.parametrize("rel", DOCS)
    def test_points_to_rerun_list(self, rel):
        root = RESEARCH.parents[1]
        s = (root / rel).read_text(encoding="utf-8")
        assert "重跑清单" in s, f"{rel} 引用了研究量级但没指向重跑清单"

    @pytest.mark.parametrize("rel", DOCS)
    def test_no_stale_debias_claim(self, rel):
        """不得再声称近期窗口「含退市/已去偏」而不加限定。"""
        root = RESEARCH.parents[1]
        for i, ln in enumerate((root / rel).read_text(encoding="utf-8").splitlines(), 1):
            if "含退市" in ln and "⚠️" not in ln and "不成立" not in ln:
                assert "1999" in ln or "老 bundle" in ln or "qlib" not in ln, \
                    f"{rel}:{i} 无限定地声称含退市：{ln.strip()[:80]!r}"
