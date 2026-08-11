"""`governance/strategy/` 的结构、注册表与一致性约束。

2026-08-06 重组：**一个策略 = 一个上下文目录**（`b1/` `cz/`），
非策略用 `_` 前缀（`_factors/` 可复用因子、`_shared/` 跨策略规则）。
动机是 owner 明确的扩展需求：「后续可能会引入更多的策略，有的是策略，
有的可能只是单个因子」。

这份测试守三件事：
① **注册表与目录必须一致** —— 新增策略不登记就会被拦住（扩展机制的强制点）；
② **文档写的阈值必须与代码常量一致** —— 不一致时文档就不是规则而是谎言，
   而读文档的人不会知道；
③ **三处已知问题必须留在索引里**直到真正解决 —— 这类问题最容易随时间被
   当作「已经这样了」而接受。
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "governance" / "strategy"
INDEX = STRATEGY / "README.md"
REGISTRY = STRATEGY / "STRATEGY_REGISTRY.json"

ROLES = {"primary", "secondary", "experimental"}
STATES = {"live", "advisory", "unwired", "partially_stale", "deprecated"}


def reg():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


class TestLayout:
    def test_root_holds_only_index_and_registry(self):
        """根目录只放索引与注册表——规则文档一律进上下文目录。"""
        files = sorted(p.name for p in STRATEGY.glob("*") if p.is_file())
        assert files == ["README.md", "STRATEGY_REGISTRY.json"], files

    def test_underscore_prefix_means_non_strategy(self):
        """`_` 前缀 = 非策略上下文，这样 ls 一眼能分开。"""
        dirs = sorted(p.name for p in STRATEGY.iterdir() if p.is_dir())
        strat_ids = {s["id"] for s in reg()["strategies"]}
        for d in dirs:
            if d.startswith("_"):
                assert d not in strat_ids, f"{d} 带 _ 前缀却登记成策略"
            else:
                assert d in strat_ids, f"{d} 是无前缀目录但未登记为策略"

    def test_doc_naming_convention(self):
        """规则文档 `NN_lower_snake.md`；已废把状态写进文件名。"""
        bad = []
        for s in reg()["strategies"]:
            for d in (STRATEGY / s["dir"]).glob("*.md"):
                if d.name == "README.md":
                    continue
                if not re.fullmatch(r"\d{2}_[a-z0-9_]+\.md", d.name):
                    bad.append(str(d.relative_to(STRATEGY)))
        assert not bad, f"不符合 NN_lower_snake.md 的文档：{bad}"

    def test_deprecated_naming_convention_holds(self):
        """废弃状态若存在，必须写在文件名里（读目录就能看见），且必须在注册表登记。

        当前没有已废文档 —— `99_deprecated_buy_integration.md` 已于 2026-08-06 删除
        （核查确认其交易规则均已被现行文档覆盖，独有的「买入计划必备项 + 缺项不得放行」
        已抢救进 `b1/03_execution_discipline.md`）。这条测试保证**将来**再出现已废文档时
        命名仍统一。
        """
        declared = set()
        for s in reg()["strategies"]:
            declared |= set(s["docs"])
        for d in declared:
            name = pathlib.Path(d).name
            if "deprecated" in name:
                assert name.startswith("99_deprecated_"), (
                    f"{d} 含 deprecated 却不是 99_deprecated_* 命名"
                )

    def test_configs_keep_upper_snake(self):
        """代码消费的配置保持 UPPER_SNAKE（与 contracts/ 一致，一眼看出代码在读它）。"""
        for s in reg()["strategies"]:
            for c in s["configs"]:
                name = pathlib.Path(c).name
                assert re.fullmatch(r"[A-Z0-9_]+\.json", name), (
                    f"{c} 应为 UPPER_SNAKE.json"
                )


class TestRegistry:
    def test_exists_and_parses(self):
        assert REGISTRY.exists()
        r = reg()
        assert r["strategies"] and "conventions" in r

    def test_exactly_one_primary(self):
        """主策略当前只有 B1。变多说明架构变了，要有意识地改。"""
        prim = [s["id"] for s in reg()["strategies"] if s["role"] == "primary"]
        assert prim == ["b1"], f"primary 集合变了：{prim}"

    @pytest.mark.parametrize("kind", ["strategies", "factors", "shared_rules"])
    def test_declared_paths_exist(self, kind):
        """注册表里声明的每个路径都必须真实存在——否则注册表本身就是谎言。"""
        bad = []
        for item in reg()[kind]:
            for key in ("entry", "rule_doc", "doc"):
                v = item.get(key)
                if v and not (STRATEGY / v).exists():
                    bad.append(f"{item['id']}.{key}={v}")
            for key in ("docs", "configs"):
                for v in item.get(key, []):
                    if not (STRATEGY / v).exists():
                        bad.append(f"{item['id']}.{key}={v}")
            for v in item.get("code", []):
                if not (ROOT / v).exists():
                    bad.append(f"{item['id']}.code={v}")
        assert not bad, f"注册表里的路径不存在：{bad}"

    def test_every_doc_is_registered(self):
        """**防孤岛**：目录里的文档必须在注册表里登记。

        实例：重组前 `technical_trend.md`(183 行) 与 `03_execution_discipline.md`(105 行)
        代码/治理文档/README/contracts 全都没引用过 —— 288 行无人可达。
        """
        declared = set()
        r = reg()
        for s in r["strategies"]:
            declared |= set(s["docs"]) | set(s["configs"])
        for f in r["factors"]:
            declared.add(f["doc"])
        for x in r["shared_rules"]:
            declared.add(x["doc"])
        # as_posix()：Windows 下 relative_to 产出反斜杠，与注册表里的正斜杠永不匹配
        actual = {
            p.relative_to(STRATEGY).as_posix()
            for p in STRATEGY.rglob("*")
            if p.is_file() and p.name not in ("README.md", "STRATEGY_REGISTRY.json")
        }
        assert actual <= declared, f"未登记的文件：{sorted(actual - declared)}"

    def test_roles_and_statuses_valid(self):
        r = reg()
        for s in r["strategies"]:
            assert s["role"] in ROLES, f"{s['id']} role={s['role']}"
            assert s["status"] in STATES, f"{s['id']} status={s['status']}"

    def test_experimental_never_live(self):
        """`role=experimental` 不得 `status=live` —— 实验中的策略不许进 live。"""
        for s in reg()["strategies"]:
            if s["role"] == "experimental":
                assert s["status"] != "live", f"{s['id']} 是实验策略却标 live"


class TestContextReadmes:
    @pytest.mark.parametrize("d", ["b1", "cz", "_factors", "_shared"])
    def test_each_dir_has_readme(self, d):
        assert (STRATEGY / d / "README.md").exists(), f"{d}/ 缺入口 README"

    @pytest.mark.parametrize("d", ["b1", "cz"])
    def test_strategy_readme_has_fixed_sections(self, d):
        """固定小节让新增策略有模板可照抄。"""
        s = (STRATEGY / d / "README.md").read_text(encoding="utf-8")
        for sec in ("## 定位", "## 代码依赖"):
            assert sec in s, f"{d}/README.md 缺 {sec}"


class TestDocHeaders:
    """每份规则文档必须有统一头部块，「执行者」是最重要的一栏——它决定改动代价。"""

    DOCS = sorted(p for p in STRATEGY.rglob("*.md") if p.name != "README.md")

    @pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(STRATEGY)))
    def test_header_present(self, doc):
        s = doc.read_text(encoding="utf-8")
        head = s[:1200]
        for field in ("**上下文**：", "**执行者**：", "**状态**：", "**代码依赖**："):
            assert field in head, f"{doc.name} 头部缺 {field}"

    @pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(STRATEGY)))
    def test_no_bom(self, doc):
        assert not doc.read_text(encoding="utf-8").startswith("\ufeff"), (
            f"{doc.name} 带 BOM"
        )


class TestReferencedPathsExist:
    """文档里写的代码/治理路径必须真实存在。

    本周大重构搬移了大量文件（holdings/ 从 market_timing 拆出、research/ 从
    screening/analysis 搬入），引用漂移是最常见的失效形式——
    `b1_holding_state.py`、`s_shape.py`、`reconcile_qfq.py` 都踩过。
    """

    # 只收「形如路径」的 token：src/ 或 governance/ 开头、以扩展名
    # （文件）或 `/`（目录）结尾；容忍 `:行号` / `#锚点` 后缀。
    # `src/pipeline_kit.propagate_gate_code` 这类「模块.符号」不算路径。
    PATHISH = re.compile(r"^(?:src|governance)/\S+?(\.[a-z0-9]+|/)$")

    @staticmethod
    def _strip_suffix(token):
        """去掉 `:行号` / `#锚点` 后缀，只校验路径部分。"""
        return re.split(r"[:#]", token, maxsplit=1)[0]

    def test_header_code_deps_exist(self):
        """策略文档头部「代码依赖」字段里的每个路径（相对 src/）必须存在。"""
        bad = []
        for doc in STRATEGY.rglob("*.md"):
            for ln in doc.read_text(encoding="utf-8")[:1200].splitlines():
                if "**代码依赖**：" not in ln:
                    continue
                # 只取标记之后的 token——同一行前文可能引用别的文件（如抢救来源）
                deps = ln.split("**代码依赖**：", 1)[1]
                for tok in re.findall(r"`([^`]+)`", deps):
                    path = self._strip_suffix(tok)
                    if not re.search(r"\.[a-z0-9]+$", path):
                        continue
                    # 归堆（src/{core,pipeline,...}）后文档保持「stage/文件.py」简写，
                    # 按相对路径后缀匹配
                    if not (ROOT / "src" / "custos" / path).exists() and not any(
                        str(p.relative_to(ROOT / "src")).endswith("/" + path)
                        for p in (ROOT / "src").rglob("*.py")
                    ):
                        bad.append(f"{doc.relative_to(STRATEGY).as_posix()}: {tok}")
        assert not bad, f"「代码依赖」里的路径不存在：{bad}"

    def test_contracts_backtick_paths_exist(self):
        """contracts/*.md 里反引号包裹的 src/** 与 governance/** 路径必须存在。"""
        bad = []
        for doc in (ROOT / "governance" / "contracts").glob("*.md"):
            for tok in re.findall(r"`([^`]+)`", doc.read_text(encoding="utf-8")):
                path = self._strip_suffix(tok)
                if not self.PATHISH.match(path):
                    continue
                if not (ROOT / path).exists():
                    bad.append(f"{doc.name}: {tok}")
        assert not bad, f"contracts 里的路径不存在：{bad}"


class TestReversalKMatchesCode:
    """文档写的反转K阈值必须与代码常量**逐项一致**。

    这是本目录唯一「代码执行」的规则，也是唯一可机械验证的。
    文档与代码不符时**以代码为准**（代码是实际在跑的），并记为一次口径修正。
    """

    CONSTS = {
        "J_LOW_THRESHOLD": 13.0,
        "VOL_RATIO_MAX": 0.5,
        "VOL_PCTILE_MAX": 10.0,
        "REVERSAL_CHANGE_MIN_PCT": -2.0,
        "REVERSAL_CHANGE_MAX_PCT": 2.0,
        "REVERSAL_AMPLITUDE_PCT": 7.0,
    }

    def _code(self):
        return (
            ROOT / "src" / "custos" / "pipeline" / "screening" / "enrich_candidates.py"
        ).read_text(encoding="utf-8")

    @pytest.mark.parametrize("name,want", sorted(CONSTS.items()))
    def test_constant_value(self, name, want):
        """断言**运行时真值**，不是源码里的字面数字。

        ⚠️ 第一版用正则读源码里的字面量，在常量改成**派生**
        （`MIN = -REVERSAL_CHANGE_PCT`，owner 2026-08-06 改回对称 ±2% 时引入）
        之后立刻假失败 —— 值是对的、正则读不到。
        与今天反复踩的「查字符串形式而非语义」同形：**能读真值就别读源码。**
        """
        import sys as _s

        _s.path.insert(0, str(ROOT / "src"))
        _s.path.insert(0, str(ROOT / "src" / "custos" / "pipeline" / "screening"))
        from custos.pipeline.screening import enrich_candidates as ec

        got = getattr(ec, name, None)
        assert got is not None, f"代码里找不到常量 {name}"
        assert float(got) == want, f"{name} 实际 {got}、索引记 {want} —— 必须一起改"

    def test_doc_records_symmetric_decision(self):
        """涨跌幅区间是**对称 ±2%**（owner 2026-08-06 拍板）。

        ⚠️ 这一改**反转了 R16（材料纠偏）第 ④ 条** —— B1_w.pdf 两处独立写明
        「涨幅为 −2% 到 1.8%」，而我们有意不跟材料。
        动因：研究侧的 `reversal_quality` 一直用对称 ±2%，两边口径不一致 ⇒
        它与 live 的反转K不是同一个东西，而 R2 的结论建立在它上面。

        **与材料不一致是有意选择，必须在文档里留痕** —— 否则下一个读材料的人
        会以为是漏改，又把它改回不对称，而研究侧又会重新对不上。
        """
        doc = (STRATEGY / "b1" / "01_swing_rules.md").read_text(encoding="utf-8")
        assert "-2% 至 +2%" in doc or "−2% ~ +2%" in doc
        assert "反转了 R16" in doc, "反转材料纠偏必须留痕"
        assert "有意的选择" in doc

    def test_min_max_default_symmetric_and_configurable(self, reversal_thresholds):
        """默认对称 ±2%，且**可配置**（owner 2026-08-06）。

        ⚠️ 判据 2026-08-07 从「源码里有 `os.environ.get("B1_REVK_CHG_PCT", "2.0")`」
        改成**行为验证** —— 阈值当天收敛到 `b1_thresholds`（L0）之后，
        `enrich_candidates` 里已经没有那行字面量了，而可配置性完全没变。
        又一次「查字符串形式而非语义」。

        ⚠️ 原 docstring 写「覆盖值**同时**影响 live 与回测（两边读同一处）」——
        **实测不成立**：`factors/reversal_quality` 有自己的 `REVK_CHG_PCT = 2.0`
        且刻意不读环境变量（钉死才能复现既有回测数字，R2 P1 重跑清单依赖）。
        两边只是默认值相同。这条边界由
        `test_enrich_b1cz.py::TestReversalKThresholdSingleSource` 钉住。
        """
        mods = reversal_thresholds()
        bt = mods["b1_thresholds"]
        assert (bt.REVERSAL_CHANGE_MIN_PCT, bt.REVERSAL_CHANGE_MAX_PCT) == (-2.0, 2.0)

        mods = reversal_thresholds(B1_REVK_CHG_PCT="3.0")
        assert (
            mods["b1_thresholds"].REVERSAL_CHANGE_MIN_PCT,
            mods["b1_thresholds"].REVERSAL_CHANGE_MAX_PCT,
        ) == (-3.0, 3.0), "MIN/MAX 必须由 PCT 派生，不是各写一个字面量"


class TestKnownIssuesStayVisible:
    """三处待处理问题必须留在索引里，直到被真正解决。

    尤其②止损层级需要 owner 拍板 —— 没拍之前不该消失。
    """

    def test_zero_impl_dependency_flagged(self):
        s = INDEX.read_text(encoding="utf-8")
        assert "04_pullback_rotation.md" in s and "零实现" in s
        # 也必须标在文档自己头部，读文档的人才看得到
        doc = (STRATEGY / "b1" / "04_pullback_rotation.md").read_text(encoding="utf-8")
        assert "零实现" in doc[:1200]

    def test_stop_scope_decided_and_recorded(self):
        """止损口径已定案（owner 2026-08-06）：**按策略上下文各行其是，无统一止损线**。

        这条必须三处同时写着，因为三处都会被单独读到：
        索引（有人查规则）／CZ 文档头部（有人读 CZ）／CZ「强制止损体系」那一节
        （那里原文措辞是「无论谁推荐的个股都必须执行」—— 普适口气，最容易被误用）。
        """
        s = INDEX.read_text(encoding="utf-8")
        assert "不存在跨策略的统一止损线" in s
        assert "已定案" in s and "20%" in s

        cz = (STRATEGY / "cz" / "01_cognition_framework.md").read_text(encoding="utf-8")
        assert "只适用于 CZ 语境" in cz[:2000], "CZ 头部要限定 15%/20% 的适用范围"
        k = cz.index("### 强制止损体系")
        assert "仅 CZ 长期持有语境" in cz[k : k + 400], (
            "「强制止损体系」那一节必须就地限定——它的措辞是普适口气，最易被误读"
        )

        b1 = (STRATEGY / "b1" / "01_swing_rules.md").read_text(encoding="utf-8")
        assert "B1 按本文档的规则止损" in b1[:2500]
        assert "5%" in b1[:2500], "B1 侧的硬约束（5% 是崖）要一并写在头部"

    def test_unreachable_docs_now_reachable(self):
        """曾经入口不可达的两份，现在必须能从索引/注册表到达。"""
        s = INDEX.read_text(encoding="utf-8")
        for name in ("technical_trend.md", "03_execution_discipline.md"):
            assert name in s, f"{name} 未在索引里"


class TestPathsConstants:
    """新目录必须在 paths.py 有常量——模块不得自己拼 strategy 子目录。"""

    def test_constants_exist(self):
        s = (ROOT / "src" / "custos" / "core" / "paths.py").read_text(encoding="utf-8")
        for c in ("B1_DIR", "CZ_DIR", "FACTORS_DIR", "STRATEGY_REGISTRY_FILE"):
            assert c in s, f"paths.py 缺 {c}"

    def test_cz_config_points_into_cz_dir(self):
        s = (ROOT / "src" / "custos" / "core" / "paths.py").read_text(encoding="utf-8")
        assert 'CZ_SECTOR_PREFERENCE_FILE = CZ_DIR / "CZ_SECTOR_PREFERENCE.json"' in s


class TestSalvagedBuyPlanChecklist:
    """删 `99_deprecated_buy_integration.md` 时抢救的规则，不许再丢。

    删除前逐条核查：它的交易规则（拉升波分类/非一波流/反转K/开盘量比六场景/
    补坑接入/否决权链）**全部已被现行文档覆盖，且多数更细**。
    但有一条**别处一处都没有**：

        「买入计划必备项清单 + 缺任一项，不得输出『允许买入』」

    而且原清单里的 **加仓条件 / 时间止损 / 风险等级** 三项在现行 03 里也缺
    （当时只有 6/8）。已一并补齐。

    价值在于它把「买入计划」定义成一个**可检查的完整对象**：
    少了时间止损，亏损仓位会被无限期持有；少了风险等级，总控无法排优先级。
    """

    DOC = STRATEGY / "b1" / "03_execution_discipline.md"

    @pytest.mark.parametrize(
        "item",
        [
            "触发信号",
            "买入价格区间",
            "首仓比例",
            "加仓条件",
            "无效条件",
            "止损位",
            "时间止损",
            "风险等级",
        ],
    )
    def test_checklist_item_present(self, item):
        assert item in self.DOC.read_text(encoding="utf-8"), (
            f"买入计划必备项缺「{item}」—— 这是删已废文档时抢救来的清单，不许再丢"
        )

    def test_hard_rule_present(self):
        """**缺任一项不得放行**——没有这条，清单只是建议。"""
        s = self.DOC.read_text(encoding="utf-8")
        assert "缺任一项，不得输出" in s

    def test_records_where_it_came_from(self):
        """写清来历，否则将来有人会以为这是凭空加的规则而删掉。"""
        s = self.DOC.read_text(encoding="utf-8")
        assert "99_deprecated_buy_integration.md" in s and "git 历史" in s


class TestDeprecationProcess:
    """废弃流程必须写着「先核查再删」，且顺序不能反。

    ⚠️ 我一开始写的规范是「判定废弃时改名，**不要删除**」，
    而 owner 直接决定删 —— 规范与实际做法矛盾。改成三步流程后两者一致：
    核查覆盖 → 抢救独有内容 → 才可以删。
    **先删再想起来「那里好像有条规则」就晚了：git 里找得回文件，但没人会想起去找。**
    """

    def test_process_documented(self):
        s = INDEX.read_text(encoding="utf-8")
        assert "废弃流程" in s
        for step in ("逐条核查", "抢救独有内容", "可以删除"):
            assert step in s, f"废弃流程缺步骤：{step}"
        assert "顺序不能反" in s

    def test_warns_against_keyword_only_check(self):
        """核查必须比对具体数值 —— 只看关键词会得出错结论（有实例）。"""
        s = INDEX.read_text(encoding="utf-8")
        assert "只看关键词会得出错结论" in s
