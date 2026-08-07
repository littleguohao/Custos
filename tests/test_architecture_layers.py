"""架构分层守卫 —— 用 AST 依赖图强制分层方向与无环。

2026-08-07 架构审查实测出的问题（本文件把结论钉住，防回归）：

  ① `factors/`（因子层，本该是最底层）依赖 `market_timing/technical_monitor`
     与 `research/backtest_factors` —— **底层依赖决策层**。
     后果：import 任一因子会拖进整个持仓状态机 + 1959 行回测器及其 40+ 依赖。
     成因：`technical_monitor` 552 行里**只有 7 个函数被模块外使用**，
     而这 7 个全是纯指标（`kdj`/`macd`/`ema`/`resample`/`bbi_state`/
     `zhixing_state`/`_infer_price_limit`），却定义在决策层模块里。
     已下移到 `indicators.py`（底层）。

  ② `factors/platform_pullback` 惰性 `import backtest_factors` 只为拿 `bt._kdj`，
     而 `bt._kdj` 就是 `technical_monitor.kdj`。构成
     `factors → screening → factors` 环。已改为直接用 `indicators.j_series`。

  ③ `news/postclose_news_digest` 从根层 `daily_report` 导入盘前情报访问器。
     已移到 `news/premarket_intel_schema`。
"""
from __future__ import annotations

import ast
import collections
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "07_tools"

# ── 分层：数字越小越底层。同层互相依赖允许，下层依赖上层不允许。
BASE_MODULES = {"paths.py", "code_utils.py", "indicators.py", "fmt.py",
                "net_retry.py", "pipeline_kit.py", "runtime_guards.py",
                # `contracts.py` 是产物 schema 的唯一来源，被 L1~L3 的生产者调用。
                # 它**只依赖 stdlib**（math + typing）—— 一条测试强制这一点，
                # 因为契约层若依赖别的模块，就可能被它校验的对象反向依赖。
                "contracts.py"}
LAYER_OF_DIR = {
    "local_tdx": 1, "collect": 1, "news": 1,
    "factors": 2, "trades": 2,
    "screening": 3, "market_timing": 3, "close_review": 3, "analysis": 3,
    # research/ 在生产链**之上**：回测要跑生产的因子与打分逻辑。
    # 2026-08-07 从 screening/ 拆出（研究代码占了那个目录的 70%）。
    "research": 4,
}
# 根目录下的**数据适配器**：性质是 L1，不是编排层。
# `s_data.py` 是 qlib/CSV 只读 loader（零内部依赖），2026-08-07 从 `screening/`
# 移来 —— 放在选股目录里会让 `local_tdx/` 的探针与对账工具反向依赖 L3。
DATA_ADAPTERS = {"s_data.py"}
ROOT_LAYER = 4   # 07_tools 根目录：runner 与编排


def _layer(rel: str) -> int:
    if rel in BASE_MODULES:
        return 0
    if rel in DATA_ADAPTERS:
        return 1
    return LAYER_OF_DIR.get(rel.split("/")[0], ROOT_LAYER) if "/" in rel else ROOT_LAYER


def _build_graph():
    """只统计**顶层** import —— 函数内的惰性导入与 `__main__` 块不算真实依赖。

    ⚠️ 这个区分是必要的：`factors/platform_pullback` 的 `__main__` 演示块
    import `local_tdx_data` 拉数据，那不是模块依赖；而它此前在**函数体内**
    import `backtest_factors` 却是真依赖（每次调用都会执行）。
    所以判据是「是否在模块顶层或函数体内」，而不是「是否在文件里出现」。
    """
    files = sorted(p for p in TOOLS.rglob("*.py") if "__pycache__" not in str(p))
    mods = {}
    for p in files:
        rel = p.relative_to(TOOLS)
        mods.setdefault(p.stem, str(rel))
        mods[str(rel.with_suffix("")).replace("/", ".")] = str(rel)

    graph = collections.defaultdict(set)
    for p in files:
        rel = str(p.relative_to(TOOLS))
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # 排除 `if __name__ == "__main__":` 块
        main_spans = [(n.lineno, n.end_lineno) for n in ast.walk(tree)
                      if isinstance(n, ast.If) and ast.unparse(n.test).replace(" ", "")
                      in ('__name__=="__main__"', "__name__=='__main__'")]

        def in_main(node):
            return any(a <= node.lineno <= b for a, b in main_spans)

        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module:
                names = [n.module]
            else:
                continue
            if in_main(n):
                continue
            for nm in names:
                for cand in (nm, nm.split(".")[0]):
                    if cand in mods and mods[cand] != rel:
                        graph[rel].add(mods[cand])
                        break
    return graph


GRAPH = _build_graph()


class TestLayerDirection:
    def test_no_downward_layer_violations(self):
        """下层不得依赖上层。"""
        bad = []
        for a, deps in GRAPH.items():
            la = _layer(a)
            for b in deps:
                lb = _layer(b)
                if lb > la:
                    bad.append(f"L{la} {a} → L{lb} {b}")
        assert not bad, "分层反转（下层依赖上层）：\n  " + "\n  ".join(sorted(bad))

    def test_factors_depend_only_on_base_and_data(self):
        """⚠️ `factors/` 是**因子实现层**（README：live 选股链与研究回测器共同依赖的下层）。

        它只许依赖基础层与数据层 —— 依赖 `screening/` 或 `market_timing/`
        会让 import 任一因子拖进整个回测器/状态机，且无法在轻量 live 路径里单独用。
        """
        bad = []
        for a, deps in GRAPH.items():
            if not a.startswith("factors/"):
                continue
            for b in deps:
                if not (b in BASE_MODULES or b.startswith("factors/") or _layer(b) <= 1):
                    bad.append(f"{a} → {b}")
        assert not bad, "factors/ 越层依赖：\n  " + "\n  ".join(sorted(bad))

    def test_no_factor_imports_backtester(self):
        """单列一条：因子**绝不许** import 回测器（1959 行、连带 40+ 模块）。"""
        bad = [f"{a} → {b}" for a, deps in GRAPH.items() if a.startswith("factors/")
               for b in deps if "backtest_factors" in b]
        assert not bad, "\n  ".join(bad)


class TestNoCycles:
    """允许的环：**同层**且其中一方是惰性导入（用于打破 import 期死锁）。

    不允许的：跨层环 —— 它意味着分层本身没立住。
    """

    ALLOWED = {
        ("local_tdx/adjust_factors.py", "local_tdx/local_tdx_data.py"),
        ("screening/enrich_candidates.py", "screening/signal_labels.py"),
    }

    def test_no_unexpected_cycles(self):
        seen, stack, found = set(), [], set()

        def dfs(u):
            if u in stack:
                found.add(tuple(sorted(set(stack[stack.index(u):]))))
                return
            if u in seen:
                return
            seen.add(u)
            stack.append(u)
            for v in sorted(GRAPH.get(u, ())):
                dfs(v)
            stack.pop()

        for k in sorted(GRAPH):
            dfs(k)
        unexpected = {c for c in found if c not in self.ALLOWED}
        assert not unexpected, ("新增循环依赖：\n  "
                               + "\n  ".join(" ↔ ".join(c) for c in sorted(unexpected)))

    def test_allowed_cycles_are_same_layer(self):
        """白名单里的环必须是同层的 —— 跨层环不得进白名单。"""
        for cyc in self.ALLOWED:
            layers = {_layer(x) for x in cyc}
            assert len(layers) == 1, f"{cyc} 跨层，不得白名单化"


class TestIndicatorLayer:
    def test_indicators_is_the_indicator_home(self):
        """指标函数集中在 `indicators.py`，`technical_monitor` 不得再定义它们。"""
        moved = {"kdj", "macd", "ema", "resample", "bbi_state", "zhixing_state",
                 "_infer_price_limit"}
        tm = ast.parse((TOOLS / "market_timing" / "technical_monitor.py")
                       .read_text(encoding="utf-8"))
        redefined = {n.name for n in tm.body
                     if isinstance(n, ast.FunctionDef) and n.name in moved}
        assert not redefined, f"这些已下移到 indicators，不得在 technical_monitor 重新定义：{redefined}"

        ind = ast.parse((TOOLS / "indicators.py").read_text(encoding="utf-8"))
        have = {n.name for n in ind.body if isinstance(n, ast.FunctionDef)}
        assert moved <= have, f"indicators 缺少：{moved - have}"

    def test_indicators_has_no_upward_dependency(self):
        """`indicators.py` 是底层，只许依赖 `code_utils`（取涨跌幅前缀基准）。"""
        assert GRAPH.get("indicators.py", set()) <= {"code_utils.py"}, \
            f"indicators 多出依赖：{GRAPH.get('indicators.py')}"


class TestContractsLayer:
    def test_contracts_depends_on_nothing_internal(self):
        """⚠️ `contracts.py` 必须**零内部依赖**。

        它是产物 schema 的唯一来源，被 L1~L3 的生产者在落盘前调用。
        若它依赖别的项目模块，就可能出现「校验层依赖被校验对象」的环。
        """
        assert GRAPH.get("contracts.py", set()) == set(), \
            f"contracts.py 不得依赖项目内模块，实际: {GRAPH.get('contracts.py')}"

    def test_money_path_producers_validate_before_write(self):
        """四个钱的路径产物的生产者必须在落盘前 `require(...)`。"""
        import re
        expect = {
            "runtime_guards.py": "runtime_gate",
            "generate_risk_and_sectors.py": "risk_decision",
            "market_timing/chief_decision_report.py": "chief_decision",
            "market_timing/b1_holding_state.py": "b1_holding_state",
            # 2026-08-07 第二批（按消费者数量排的优先级）
            "market_timing/market_timing_collector.py": "market_timing_input",
            "market_timing/merge_incremental_market.py": "market_timing_input",
            "market_timing/batch_holding_technical.py": "holding_technical_summary",
        }
        for rel, artifact in expect.items():
            src = (TOOLS / rel).read_text(encoding="utf-8")
            assert re.search(rf"require\(['\"]{artifact}['\"]", src), \
                f"{rel} 未在落盘前校验 {artifact}"

    def test_sector_state_validated(self):
        import re
        src = (TOOLS / "generate_risk_and_sectors.py").read_text(encoding="utf-8")
        assert re.search(r"require\(['\"]sector_state['\"]", src)


class TestResearchProductionSplit:
    """⚠️ `research/` 与 `screening/` 的依赖方向：**只许研究 → 生产**。

    2026-08-07 拆分前实测反向为 0，所以能干净拆开。这条测试保住它 ——
    一旦生产链 import 了研究脚本，18:00 的每日选股就会依赖一个
    「探索性、可以失败、可以废弃」的模块。
    """

    def test_production_never_imports_research(self):
        bad = [f"{a} → {b}" for a, deps in GRAPH.items() if a.startswith("screening/")
               for b in deps if b.startswith("research/")]
        assert not bad, ("生产选股链不得依赖研究脚本：\n  " + "\n  ".join(bad))

    def test_research_may_import_production(self):
        """反向是允许且实际存在的（`backtest_factors → enrich_candidates`）——
        这条测试确认拆分没把它切断（切断了说明研究脚本已经跑不起来）。"""
        edges = [f"{a} → {b}" for a, deps in GRAPH.items() if a.startswith("research/")
                 for b in deps if b.startswith("screening/")]
        assert edges, "研究脚本应仍能依赖生产模块（回测要跑生产逻辑）"

    def test_screening_has_only_production_chain(self):
        """`screening/` 里只剩 18:00 生产链会用到的模块。"""
        got = {p.stem for p in (TOOLS / "screening").glob("*.py")} - {"__init__"}
        assert got == {"formula_screen", "enrich_candidates", "score_candidates",
                       "candidate_table", "manual_pools", "signal_labels", "financials"}, got
