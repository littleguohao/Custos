"""`00_governance/strategy/` 的索引与一致性约束。

2026-08-06 梳理时发现三类问题，这份测试防它们复发：
① 有文档**零实现却被别的文档依赖**（`market_pullback_rotation_selection.md`）；
② 同一参数在不同文档里差 2~4 倍且**层级关系没写**（B1 8% vs CZ 15/20%）；
③ 两份文档（288 行）**任何入口都到不了** —— 代码、治理文档、README、contracts 全无引用。

最关键的一条是「文档写的参数必须与代码常量一致」：文档一旦与代码不符，
它就不再是规则而是谎言，而**读文档的人不会知道**。
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "00_governance" / "strategy"
INDEX = STRATEGY / "README.md"
DOCS = sorted(p for p in STRATEGY.glob("*.md") if p.name != "README.md")


def test_index_exists():
    assert INDEX.exists(), "strategy/ 缺索引，文档会变成孤岛"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_doc_is_indexed(doc):
    """**防孤岛**：不在索引里的文档没人能找到它。

    实例：`UNIVERSAL_TECHNICAL_TREND_FRAMEWORK.md`(183 行) 与
    `trading_execution_discipline.md`(105 行) 在建索引前，
    代码/治理文档/README/contracts 全都没引用过 —— 288 行无人可达。
    """
    assert doc.name in INDEX.read_text(encoding="utf-8"), \
        f"{doc.name} 未列入 strategy/README.md"


def test_index_classifies_by_executor():
    """按「谁执行」分类是这份索引的组织原则——它决定改动代价。"""
    s = INDEX.read_text(encoding="utf-8")
    for sec in ("规则 · 代码执行", "规则 · 人执行", "认知框架 · 输入", "已废 / 待重建"):
        assert sec in s, f"索引缺分类：{sec}"


def test_deprecated_docs_say_what_supersedes_them():
    """已废文档必须写明**以什么为准**，否则会被当现行规则读。"""
    s = INDEX.read_text(encoding="utf-8")
    seg = s[s.index("### ⑤ 已废 / 待重建"):]
    seg = seg[:seg.index("\n## ")] if "\n## " in seg else seg
    for name in ("BUY_STRATEGY_INTEGRATION_RULES.md", "DECISION_PRIORITY_RULES.md"):
        assert name in seg, f"{name} 应在已废区块"
    assert "以什么为准" in s


class TestReversalKMatchesCode:
    """文档写的反转K阈值必须与代码常量**逐项一致**。

    ⚠️ 这是本目录唯一「代码执行」的规则，也是唯一可机械验证的。
    文档与代码不符时**以代码为准**（代码是实际在跑的），并记为一次口径修正。
    """

    CONSTS = {
        "J_LOW_THRESHOLD": 13.0,
        "VOL_RATIO_MAX": 0.5,
        "VOL_PCTILE_MAX": 10.0,
        "REVERSAL_CHANGE_MIN_PCT": -2.0,
        "REVERSAL_CHANGE_MAX_PCT": 1.8,
        "REVERSAL_AMPLITUDE_PCT": 7.0,
    }

    def _code(self):
        return (ROOT / "07_tools" / "screening"
                / "enrich_candidates.py").read_text(encoding="utf-8")

    @pytest.mark.parametrize("name,want", sorted(CONSTS.items()))
    def test_constant_value(self, name, want):
        m = re.search(rf"^{name} *= *(-?[\d.]+)", self._code(), re.M)
        assert m, f"代码里找不到常量 {name}"
        assert float(m.group(1)) == want, \
            f"{name} 代码是 {m.group(1)}、索引记的是 {want} —— 两者必须一起改"

    def test_doc_states_asymmetric_range(self):
        """涨跌幅区间是**不对称**的 −2%~+1.8%。

        这一条曾经写错成对称 ±2%（`REVERSAL_CHANGE_PCT = 2.0` 是那个旧值的残留），
        所以文档里必须明确「不对称」，避免被"顺手改回"对称。
        """
        doc = (STRATEGY / "b1_swing_strategy.md").read_text(encoding="utf-8")
        assert "-2% 至 +1.8%" in doc or "−2% ~ +1.8%" in doc
        assert "不对称" in doc

    def test_stale_symmetric_constant_not_used_in_logic(self):
        """旧对称阈值只许作口径对照，不许回到判定里。"""
        code = self._code()
        assert "REVERSAL_CHANGE_PCT = 2.0" in code, "残留常量被删了？同步更新索引说明"
        assert "abs(change_pct) <= REVERSAL_CHANGE_PCT" not in code


class TestKnownIssuesStayVisible:
    """三处待处理问题必须留在索引里，直到被真正解决。

    为什么用测试钉：**这类问题最容易随时间被当作「已经这样了」而接受**。
    尤其②止损层级——它需要 owner 拍板，没拍之前不该消失。
    """

    def test_zero_impl_dependency_flagged(self):
        s = INDEX.read_text(encoding="utf-8")
        assert "market_pullback_rotation_selection.md" in s
        assert "零实现" in s, "「被依赖但零实现」这个问题必须显式写着"

    def test_stop_hierarchy_flagged(self):
        s = INDEX.read_text(encoding="utf-8")
        assert "层级" in s and "20%" in s and "8%" in s, \
            "B1 8% 与 CZ 15/20% 的层级关系未写明，这是最要紧的风控歧义"
        assert "owner 拍板" in s, "层级关系属策略决策，须标明待 owner 拍板"

    def test_unreachable_docs_flagged(self):
        s = INDEX.read_text(encoding="utf-8")
        assert "入口不可达" in s
