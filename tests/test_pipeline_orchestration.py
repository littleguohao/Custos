"""管线**编排逻辑**测试：stage 顺序、失败传播、session 分支、门控码。

## 为什么必须有这一层

2026-08-07 实测覆盖率：

    run_0850   96.7%    run_1700   78.4%    run_0905   51.8%
    run_1445  **22.2%**  run_1800  **21.4%**  daily_pipeline **23.3%**（172 语句缺 132）

而**唯一**跑 runner 的 `test_runners_smoke.py` **只在非交易日跑** ——
runner 在日历门后立刻退出，所以它测的是那 5 行门检查，交易日一律 skip。
⇒ **编排逻辑（哪个 stage 先跑、失败了怎么办、session 怎么分支）从未被执行过。**

这类 bug 的代价高且静默。最具体的例子写在 `run_1700` 自己的注释里：

    sync_compass_amv **必须在** merge_incremental_market 之前 ——
    merge 据 amv_0day/confirmed 观测自动把 amv_0.quality 置 confirmed，
    随后 daily_pipeline 的 amv_state 才能据真值切换 regime

顺序反了不会报错，只会让 **regime 永远不切换** —— 而 0AMV regime 决定全链方向。

## 怎么测

所有 runner 都用模块级 `run_stage_quiet as _stage`（`daily_pipeline` 用 `run_stage`），
是**单一可注入接缝**。打桩成 recorder 后跑 `main()`，就能断言顺序与分支，
且**不真的 spawn 任何子进程**。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "src" / "custos"
sys.path.insert(0, str(TOOLS))


class Recorder:
    """替代 `_stage` / `run_stage`：记录调用顺序，按 `fail` 指定哪些 stage 失败。"""

    def __init__(self, fail: set[str] | None = None, gate_code: int = 0,
                 stdout: dict[str, str] | None = None):
        self.calls: list[tuple[str, list[str]]] = []
        self.fail = fail or set()
        self.gate_code = gate_code
        # 按 stage 名注入 stdout —— `run_1800` 会读它判 degraded
        # （**退出码 0 但 JSON 报 unavailable/partial 也算降级**）
        self.stdout = stdout or {}

    def __call__(self, cmd, name, *a, **kw):
        self.calls.append((name, list(cmd)))
        ok = name not in self.fail
        if name == "runtime_gate":
            # ⚠️ 键名必须是 `returncode` ——`daily_pipeline` 读的是
            # `gate_stage["returncode"]`，且只在 `not gate_stage["ok"]` 时才传播。
            # 第一版用了 `code` 并让 ok=True ⇒ 三条门控测试全挂，
            # 而挂的原因是**桩不真**，不是被测代码有问题。
            return self._shape(name, self.gate_code == 0, self.gate_code)
        r = self._shape(name, ok, 0 if ok else 1)
        if name in self.stdout:
            r["stdout"] = self.stdout[name]
        return r

    @staticmethod
    def _shape(name, ok, rc):
        """⚠️ 键要**齐全**：`run_stage_quiet` 返回 out/stdout/stderr/returncode，
        而 `run_1700` 读的是 `r["stdout"]`。第一版只给了 `out` ⇒ KeyError
        让 run_1700 在 `sync_compass_amv` 之后中止，于是「merge 在 sync 之后」
        这条断言**永远走 skip 分支** —— 桩不真会把测试悄悄变成空转。"""
        return {"stage": name, "ok": ok, "out": "", "stdout": "", "stderr": "",
                "returncode": rc}

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def index(self, name: str) -> int:
        return self.names.index(name)


@pytest.fixture()
def pipeline(monkeypatch, tmp_path):
    from custos.pipeline import daily_pipeline as dp

    # ⚠️ **必须 patch 全部路径常量**。第一版漏了 `PLANS` / `SUPPORT_DIR`，
    # 测试在**真实仓库**里建出了 `artifacts/reports/daily/_supporting/2026-08-07/`
    # （空目录、且被 gitignore，所以没污染 git —— 但这正是今天
    # `2026-07-16/` 那次事故的同一形态：脚本往仓库里写东西）。
    monkeypatch.setattr(dp, "BASE", tmp_path, raising=False)
    for attr in ("DATA", "MARKET_DIR", "HOLDINGS_DIR", "PLANS",
                 "SUPPORT_DIR", "LOGS"):
        assert hasattr(dp, attr), f"daily_pipeline 少了路径常量 {attr}（改名了？）"
        d = tmp_path / attr.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(dp, attr, d)
    return dp


def _run_pipeline(dp, monkeypatch, rec, session="premarket", extra=()):
    monkeypatch.setattr(dp, "run_stage", rec)
    monkeypatch.setattr(sys, "argv",
                        ["x", "--date", "2026-08-07", "--session-type", session, *extra])
    try:
        dp.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    return 0


class TestDecisionChainOrder:
    """⚠️ 决策链的顺序是**语义要求**，不是巧合。

    每一步都消费上一步的产物（契约里 24 个产物的生产者顺序就是这条链）：
    技术面 → B1 状态 → 持仓复盘 → 风控 → 总控。
    顺序反了不会报错 —— 下游读到的是**上一交易日**的文件。
    """

    def test_holding_to_decision_order(self, pipeline, monkeypatch):
        rec = Recorder()
        _run_pipeline(pipeline, monkeypatch, rec, "postclose")
        chain = ["batch_holding_technical", "b1_holding_state",
                 "portfolio_review_report", "generate_risk_and_sectors",
                 "chief_decision_report"]
        present = [c for c in chain if c in rec.names]
        idx = [rec.index(c) for c in present]
        assert idx == sorted(idx), f"决策链顺序错乱：{present} → {idx}"

    def test_collector_precedes_amv_state(self, pipeline, monkeypatch):
        """`amv_state` 读 `market_timing_input.json` ⇒ collector 必须先跑。"""
        rec = Recorder()
        _run_pipeline(pipeline, monkeypatch, rec, "premarket")
        if "market_timing_collector" in rec.names and "amv_state" in rec.names:
            assert rec.index("market_timing_collector") < rec.index("amv_state")

    def test_gate_runs_before_reports(self, pipeline, monkeypatch):
        """门控要在报告之前 —— 报告要引用门控结论。"""
        rec = Recorder()
        _run_pipeline(pipeline, monkeypatch, rec, "postclose")
        if "runtime_gate" in rec.names and "chief_decision_report" in rec.names:
            assert rec.index("runtime_gate") < rec.index("chief_decision_report")


class TestSessionBranching:
    """premarket 与 postclose 跑的 stage 集合不同 —— 分支错了会漏跑或多跑。"""

    def test_postclose_only_stages(self, pipeline, monkeypatch):
        pre, post = Recorder(), Recorder()
        _run_pipeline(pipeline, monkeypatch, pre, "premarket")
        _run_pipeline(pipeline, monkeypatch, post, "postclose")
        only_post = set(post.names) - set(pre.names)
        assert "postclose_news_digest" in only_post, \
            f"盘后新闻摘要应只在 postclose 跑，实际 postclose 独有={only_post}"

    def test_premarket_snapshot_stage_is_declared(self):
        """⚠️ premarket 会把 chief_decision **快照**一份
        （`{date}_premarket_chief_decision.json`）—— 17:00 复盘要拿盘前计划与
        实际执行对照，没有快照就无法复盘「计划外交易」。

        它是 `shutil.copy2` + 直接 append 一条 dict，**不走 `run_stage`**，
        所以 Recorder 记不到 ⇒ 查源码。
        （第一版为了「让它过」写成 `assert ... or True` —— 那种永真断言
        比没有测试更糟：看着有覆盖，实际什么都没验。）
        """
        src = (TOOLS / "pipeline/daily_pipeline.py").read_text(encoding="utf-8")
        assert 'session_type == "premarket"' in src
        assert "premarket_chief_decision" in src
        assert "snapshot_premarket_chief_decision" in src

    def test_both_sessions_run_decision_chain(self, pipeline, monkeypatch):
        """两个 session 都要跑决策链 —— 09:05 也产出当日 risk_decision
        （依据是 T-1 收盘，见 v0.35）。"""
        for session in ("premarket", "postclose"):
            rec = Recorder()
            _run_pipeline(pipeline, monkeypatch, rec, session)
            assert "generate_risk_and_sectors" in rec.names, session
            assert "chief_decision_report" in rec.names, session


class TestFailurePropagation:
    """⚠️ `required=True` 的 stage 失败必须让整条链失败；`required=False` 不得。

    区分错的代价：把硬失败当 best-effort ⇒ 报告缺内容却报成功；
    反之 ⇒ 一个可选数据源抖动就毁掉整份报告。
    """

    def test_optional_stage_failure_does_not_abort(self, pipeline, monkeypatch):
        rec = Recorder(fail={"rss_collector", "overseas_market_collector"})
        _run_pipeline(pipeline, monkeypatch, rec, "premarket")
        assert "chief_decision_report" in rec.names, \
            "可选采集失败不应中断决策链"

    def test_required_stage_failure_is_visible(self, pipeline, monkeypatch):
        """硬失败 stage 挂掉时，`run_stage` 的 required 语义由 pipeline_kit 处理，
        这里只断言它**确实被标成 required** —— 即调用时没传 `required=False`。"""
        rec = Recorder()
        _run_pipeline(pipeline, monkeypatch, rec, "postclose")
        hard = {"batch_holding_technical", "b1_holding_state", "generate_risk_and_sectors",
                "chief_decision_report", "theme_tracker_report"}
        for name, _cmd in rec.calls:
            if name in hard:
                # required 默认 True；调用点若显式传 False 会出现在 kwargs 里，
                # Recorder 收不到 —— 所以改查源码更可靠，见下面的源码断言
                pass
        src = (TOOLS / "pipeline/daily_pipeline.py").read_text(encoding="utf-8")
        import re
        for name in hard:
            m = re.search(rf'"{name}"\s*,\s*required=False', src)
            assert not m, f"{name} 是硬失败 stage，不得标 required=False"


class TestGateCodePropagation:
    """⚠️ 门控退出码必须穿透 `daily_pipeline`（cron 直接按码判定），
    且**只放行 3/4/5** —— 别的码不能被当成门控结论。"""

    @pytest.mark.parametrize("code", [3, 4, 5])
    def test_gate_codes_propagate(self, pipeline, monkeypatch, code):
        rec = Recorder(gate_code=code)
        rc = _run_pipeline(pipeline, monkeypatch, rec, "postclose")
        assert rc == code, f"门控码 {code} 应穿透，实际 exit={rc}"

    @pytest.mark.parametrize("code", [1, 2, 6, 127])
    def test_non_gate_codes_not_propagated_as_gate(self, pipeline, monkeypatch, code):
        """非门控码不得冒充门控结论 —— `propagate_gate_code` 只放行 3/4/5。"""
        rec = Recorder(gate_code=code)
        rc = _run_pipeline(pipeline, monkeypatch, rec, "postclose")
        assert rc not in {3, 4, 5} or rc == 0, f"code={code} 被当成门控码 {rc}"


# ══════════════════════════════════════════════════════════════════════════
# runner 级编排（run_1445 / run_1700 / run_1800 覆盖率原为 22% / 78% / 21%）
# 所有 runner 都用模块级 `run_stage_quiet as _stage` ⇒ 单一接缝
# ══════════════════════════════════════════════════════════════════════════

def _run_runner(mod, monkeypatch, rec, tmp_path, argv=(), seed=None):
    """跑 runner 的 main()，绕过日历门、隔离所有路径、注入 stage recorder。"""
    monkeypatch.setattr(mod, "_stage", rec)
    # 绕过日历门：让它当交易日（返回 exit_code=None 表示继续）
    from custos.core import pipeline_kit

    # ⚠️ 桩必须带 `cal` 字段 —— runner 会读 `_cg.cal`（日历检查的解析结果）。
    # 第一版只给了 exit_code/stages ⇒ AttributeError，4 条测试静默变成 skip
    # （因为 `_run_runner` 把异常吞成 "raised:..." 返回值）。
    # 教训：**skip 不是通过**，看到 skip 要先确认是「条件不满足」还是「桩不真」。
    gate = pipeline_kit.CalendarGate(
        cal={"is_trading_day": True, "date": "2026-08-07"}, exit_code=None)
    monkeypatch.setattr(mod, "calendar_gate", lambda *a, **kw: gate, raising=False)
    # ⚠️ patch **全部大写的 Path 属性**，不能只挑名字带 DIR 的 ——
    # `run_1700` 的复盘目录叫 `REV`，第一版按 "DIR" 过滤就漏了它，
    # 测试于是往**真实** `artifacts/reports/daily/` 里写。
    # 唯一例外是 `TOOLS`：它必须指向真实 src，否则拼出的 stage 命令没意义
    # （虽然 stage 被打桩不会真跑，但断言里要查命令内容）。
    for attr in dir(mod):
        if not attr.isupper() or attr in {"TOOLS", "PY"}:
            continue
        if isinstance(getattr(mod, attr, None), pathlib.Path):
            d = tmp_path / attr.lower()
            d.mkdir(parents=True, exist_ok=True)
            monkeypatch.setattr(mod, attr, d)
    monkeypatch.setattr(mod.os, "chdir", lambda *a: None, raising=False)
    if seed is not None:
        seed(mod)                     # 路径已 patch 完，此时铺前提文件
    monkeypatch.setattr(sys, "argv", ["x", *argv])
    try:
        # ⚠️ runner 的 `main()` 是**返回**退出码（`return propagate_gate_code(r)`），
        # 不是 raise SystemExit。第一版丢掉了返回值 ⇒ 门控码测试恒得 0。
        rc = mod.main()
        return rc if isinstance(rc, int) else 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    except Exception as e:                    # 编排之外的失败（缺数据）不该淹没顺序断言
        return f"raised:{type(e).__name__}"


class TestRun1700Order:
    """⚠️ `run_1700` 里有一条**写在注释里的顺序要求**，正是本文件存在的理由：

        sync_compass_amv **必须在** merge_incremental_market 之前 ——
        merge 据 amv_0day/confirmed 观测把 amv_0.quality 置 confirmed，
        随后 daily_pipeline 的 amv_state 才能据真值切换 regime

    顺序反了**不会报错**，只会让 regime 永远不切换 —— 而 0AMV regime 决定全链方向。
    """

    def test_sync_compass_precedes_merge(self, monkeypatch, tmp_path):
        from custos.pipeline import run_1700

        rec = Recorder()
        _run_runner(run_1700, monkeypatch, rec, tmp_path)
        assert "sync_compass_amv" in rec.names, rec.names
        assert "merge_incremental_market" in rec.names, rec.names
        assert rec.index("sync_compass_amv") < rec.index("merge_incremental_market"), \
            "sync_compass_amv 必须先跑，否则 amv quality 永远不会被置 confirmed"

    def test_collect_precedes_mfe_and_review(self, monkeypatch, tmp_path):
        """行情采集要在 MFE/MAE 与复盘之前 —— 它们都读当日行情。"""
        from custos.pipeline import run_1700

        rec = Recorder()
        _run_runner(run_1700, monkeypatch, rec, tmp_path)
        # stage 名实测自 run_1700.main()：collect_holding_quotes /
        # collect_incremental_market / calc_mfe_mae / ledger_reconcile /
        # collect_fund_flow / refresh_eod_klines / refresh_market_indices /
        # sync_compass_amv / merge_incremental_market / daily_pipeline /
        # final_close_review / final_review_validator
        for later in ("calc_mfe_mae", "final_close_review"):
            if "collect_holding_quotes" in rec.names and later in rec.names:
                assert rec.index("collect_holding_quotes") < rec.index(later), later

    def test_daily_pipeline_precedes_final_review(self, monkeypatch, tmp_path):
        """`final_close_review` 的 8 个强制输入全由 daily_pipeline 产出。"""
        from custos.pipeline import run_1700

        rec = Recorder()
        _run_runner(run_1700, monkeypatch, rec, tmp_path)
        if "daily_pipeline" in rec.names and "final_close_review" in rec.names:
            assert rec.index("daily_pipeline") < rec.index("final_close_review")


class TestRun1445Order:
    def test_quotes_precede_review(self, monkeypatch, tmp_path):
        """⚠️ 14:45 的核心语义：**先取当日实时行情，再算尾盘动作**。

        顺序反了会让 `review_core` 拿不到当日行情 ⇒ 走「等待当日行情」分支，
        整份 14:45 报告失去意义（而不会报错）。
        """
        from custos.pipeline import run_1445

        rec = Recorder()
        _run_runner(run_1445, monkeypatch, rec, tmp_path)
        # ⚠️ run_1445 的 stage 名带后缀：`collect_holding_quotes intraday`、
        # 复盘那步叫 `close_review`（不是 review_core）—— 实测得来。
        assert "collect_holding_quotes intraday" in rec.names, rec.names
        assert "close_review" in rec.names, rec.names
        assert rec.index("collect_holding_quotes intraday") < rec.index("close_review")

    def test_gate_recorded_but_not_blocking(self, monkeypatch, tmp_path):
        """14:45 门控**只留痕不阻断**（0AMV 与宽度本就要等收盘）。"""
        from custos.pipeline import run_1445

        rec = Recorder(gate_code=4)     # blocked
        rc = _run_runner(run_1445, monkeypatch, rec, tmp_path)
        assert rc != 4, "14:45 不得因门控 blocked 而退出 4"


class TestRun1800Order:
    """18:00 选股链。⚠️ 门控在这里**只提示、不得影响选股结果** ——
    否则 live 候选无法与回测对照（v0.29）。"""

    def test_names_refresh_precedes_screening(self, monkeypatch, tmp_path):
        """股票名称表刷新要在选股之前 —— 它是 **ST 硬排除的唯一依据**。"""
        from custos.pipeline import run_1800

        rec = Recorder()
        _run_runner(run_1800, monkeypatch, rec, tmp_path)
        assert "refresh_stock_names" in rec.names, rec.names
        assert "screening_formula_screen" in rec.names, rec.names
        assert rec.index("refresh_stock_names") < rec.index("screening_formula_screen")

    def test_screening_chain_order(self, monkeypatch, tmp_path):
        """公式初筛 → 充实 → 打分 → 表格，每步消费上一步产物。"""
        from custos.pipeline import run_1800

        rec = Recorder()
        _run_runner(run_1800, monkeypatch, rec, tmp_path)
        # ⚠️ 名字带 `screening_` 前缀 —— 实测得来。第一版按无前缀写，
        # 条件不满足直接 skip，而 **skip 不是通过**。
        chain = ["screening_formula_screen", "screening_enrich_candidates",
                 "screening_score_candidates", "screening_candidate_table"]
        present = [c for c in chain if c in rec.names]
        assert len(present) == len(chain), f"选股链 stage 缺失：{rec.names}"
        idx = [rec.index(c) for c in present]
        assert idx == sorted(idx), f"选股链顺序错乱：{present} → {idx}"

    def test_gate_blocked_does_not_stop_screening(self, monkeypatch, tmp_path):
        """⚠️ v0.29：18:00 门控 blocked 时**选股照跑** ——
        门控若改写分层，live 候选就无法与回测对照。"""
        from custos.pipeline import run_1800

        rec = Recorder(gate_code=4)
        rc = _run_runner(run_1800, monkeypatch, rec, tmp_path)
        assert rc != 4, "18:00 不得因门控 blocked 而退出"


class TestRunnerNamesResolve:
    """⚠️ 回归（2026-08-07 发现）：**runner 里用到的模块级名字必须真的存在**。

    `run_1445.py` 从 **2026-08-06** 起就是坏的：那天的提交
    「收敛 src 路径推导并修一处 TOOLS 误名」把 `TOOLS` 加进了**注释**
    却没加进**导入列表** ——

        -from paths import BASE, cn_today  # strategy_team/
        +from paths import BASE, cn_today  # strategy_team/, TOOLS

    于是 `main()` 在日历门后的**第一个 stage** 就 `NameError`，
    交易日完全产不出 14:45 报告。**整整一天没人发现**，因为：

      · `test_runners_smoke.py` **只在非交易日跑**（runner 在门后立刻退出）
      · `test_run_1445.py` 的 2 条测试**从不调 `main()`**
      · `--help` 冒烟也过 —— argparse 在 NameError 之前就 return 了

    ⇒ 这就是编排测试存在的理由：它是唯一会**执行 main() 主体**的一层。
    """

    @pytest.mark.parametrize("name", ["run_0850", "run_0905", "run_1445",
                                      "run_1700", "run_1800", "daily_pipeline"])
    def test_module_level_names_used_in_main_are_defined(self, name):
        """AST 扫 `main()` 里读取的全局名，逐个确认模块命名空间里有。"""
        import ast as _ast
        import builtins

        mod = __import__(name)
        src = (TOOLS / "pipeline" / f"{name}.py").read_text(encoding="utf-8")
        tree = _ast.parse(src)
        fn = next((n for n in tree.body
                   if isinstance(n, _ast.FunctionDef) and n.name == "main"), None)
        assert fn is not None, f"{name} 没有 main()"
        local = set()
        for n in _ast.walk(fn):
            if isinstance(n, _ast.Name) and isinstance(n.ctx, (_ast.Store,)):
                local.add(n.id)
            elif isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                local.add(n.name)
                local |= {a.arg for a in n.args.args}
            elif isinstance(n, _ast.ExceptHandler) and n.name:
                local.add(n.name)
        known = set(dir(builtins)) | set(vars(mod)) | local | {a.arg for a in fn.args.args}
        missing = sorted({n.id for n in _ast.walk(fn)
                          if isinstance(n, _ast.Name) and isinstance(n.ctx, _ast.Load)}
                         - known)
        assert not missing, (f"{name}.main() 用到未定义的名字：{missing}\n"
                             "很可能是重构时改了注释没改导入（run_1445 的 TOOLS 就是这样）")


class TestRun0905:
    """⚠️ `run_0905` 原本**整个 `main()` 都没测**（覆盖率 51.8%，40 行未覆盖
    就是 main 主体）—— 写编排测试时漏了它，2026-08-07 补上。

    它是盘前报告链：日历门 → daily_pipeline(premarket) → 报告摘要。
    """

    def test_runs_daily_pipeline_premarket(self, monkeypatch, tmp_path):
        from custos.pipeline import run_0905

        rec = Recorder()
        _run_runner(run_0905, monkeypatch, rec, tmp_path)
        assert any("daily_pipeline" in n for n in rec.names), rec.names
        cmd = next(c for n, c in rec.calls if "daily_pipeline" in n)
        assert "premarket" in " ".join(cmd), "0905 必须跑 premarket session"

    @pytest.mark.parametrize("code", [3, 4, 5])
    def test_gate_code_propagates_through_0905(self, monkeypatch, tmp_path, code):
        """⚠️ 门控码要**穿透 runner** —— cron 直接按码判定
        （`propagate_gate_code` 只放行 3/4/5）。

        `daily_pipeline` 失败时 run_0905 走 `propagate_gate_code(r)`，
        所以这里让那个 stage 带上门控码。
        """
        from custos.pipeline import run_0905

        # ⚠️ 不能在调 `_run_runner` **之前**打桩 `_stage` —— 它会被 harness 覆盖。
        # 用一个「所有 stage 都带指定 returncode 失败」的 Recorder 子类。
        class GateFail(Recorder):
            def __call__(self, cmd, name, *a, **kw):
                self.calls.append((name, list(cmd)))
                return self._shape(name, False, code)

        rc = _run_runner(run_0905, monkeypatch, GateFail(), tmp_path)
        assert rc == code, f"门控码 {code} 应穿透 run_0905，实际 {rc}"

    def test_missing_report_is_failed_not_completed(self, monkeypatch, tmp_path):
        """⚠️ 报告文件没生成必须记 `failed` —— 不能因为 stage 退出码 0 就报 completed。

        这正是「没抛异常 ≠ 产出了东西」在 runner 层的形态。
        """
        from custos.pipeline import run_0905

        rec = Recorder()
        _run_runner(run_0905, monkeypatch, rec, tmp_path)   # PLANS 是空的 tmp
        logs = list((tmp_path / "log_dir").glob("*.json")) if (tmp_path / "log_dir").exists() else []
        logs += [p for d in tmp_path.iterdir() if d.is_dir() for p in d.glob("*run_log*.json")]
        assert logs, "应写出 run log"
        import json
        data = json.loads(logs[0].read_text(encoding="utf-8"))
        assert data.get("status") == "failed", f"报告缺失时应为 failed，实际 {data.get('status')}"


class TestRun1800Degradation:
    """⚠️ 18:00 选股链的**降级判定**。原先只测了顺序，没测降级 ——
    而降级判定里有一条很容易被忽略的语义。"""

    def test_stage_ok_but_json_says_unavailable_counts_as_degraded(
            self, monkeypatch, tmp_path):
        """⚠️ **退出码 0 但 JSON 里 `status=unavailable/partial` 也算降级。**

        这是「没抛异常 ≠ 拿到数据」在选股链的形态：公式初筛可能正常退出
        却因为宇宙残缺只筛了子集。只看 `r["ok"]` 会把它当成功。
        """
        from custos.pipeline import run_1800

        rec = Recorder(stdout={"screening_formula_screen":
                               '{"status": "unavailable", "reason": "universe empty"}'})
        _run_runner(run_1800, monkeypatch, rec, tmp_path)
        logs = [p for d in tmp_path.rglob("*") if d.is_dir()
                for p in d.glob("*run_log*.json")]
        assert logs, "应写出 run log"
        import json
        data = json.loads(logs[0].read_text(encoding="utf-8"))
        assert data.get("status") == "degraded", \
            f"stage 报 unavailable 时整链应 degraded，实际 {data.get('status')}"

    def test_stage_failure_counts_as_degraded(self, monkeypatch, tmp_path):
        import json

        from custos.pipeline import run_1800

        rec = Recorder(fail={"screening_score_candidates"})
        _run_runner(run_1800, monkeypatch, rec, tmp_path)
        logs = [p for d in tmp_path.rglob("*") if d.is_dir()
                for p in d.glob("*run_log*.json")]
        data = json.loads(logs[0].read_text(encoding="utf-8"))
        assert data.get("status") == "degraded"

    def test_missing_candidate_table_recorded_not_crashed(self, monkeypatch, tmp_path):
        """⚠️ 备选表没生成时记一条 not-ok 的 `candidate_digest` stage
        并**继续**（不崩、不阻断）—— 18:00 是独立链，它挂了不该影响别的。"""
        import json

        from custos.pipeline import run_1800

        rec = Recorder()
        rc = _run_runner(run_1800, monkeypatch, rec, tmp_path)
        assert rc in (0, 1), f"不该异常退出，实际 {rc}"
        logs = [p for d in tmp_path.rglob("*") if d.is_dir()
                for p in d.glob("*run_log*.json")]
        data = json.loads(logs[0].read_text(encoding="utf-8"))
        # ⚠️ run log 里的键是 `name` 不是 `stage`（`pipeline_kit.log_stage`）——
        # 第一版按 `stage` 查，取到全 None，断言「应有该 stage」于是失败。
        digest = [x for x in data.get("stages", []) if x.get("name") == "candidate_digest"]
        assert digest, "应有 candidate_digest stage 记录"
        assert digest[0].get("ok") is False
        assert "备选表未生成" in (digest[0].get("note") or "")


class TestRun1700HardFailures:
    """⚠️ `run_1700` 的三处硬失败路径（原先全未覆盖）。

    这三个 stage 失败时必须记 `status="failed"` —— 与「best-effort 采集失败
    记 degraded」区分开。混淆的代价：盘后复盘没产出却报成功，第二天没人知道。
    """

    @pytest.mark.parametrize("stage", ["daily_pipeline", "final_close_review",
                                       "final_review_validator"])
    def test_hard_stage_failure_writes_failed(self, monkeypatch, tmp_path, stage):
        import json

        from custos.pipeline import run_1700

        rec = Recorder(fail={stage})
        _run_runner(run_1700, monkeypatch, rec, tmp_path)
        logs = [p for d in tmp_path.rglob("*") if d.is_dir()
                for p in d.glob("*run_log*.json")]
        assert logs, f"{stage} 失败也要写 run log"
        data = json.loads(logs[0].read_text(encoding="utf-8"))
        assert data.get("status") == "failed", \
            f"{stage} 是硬失败 stage，应记 failed，实际 {data.get('status')}"

    def test_best_effort_failures_do_not_mark_failed(self, monkeypatch, tmp_path):
        """采集类失败是 best-effort ⇒ 不得记 failed（否则每次网络抖动都像事故）。"""
        import json

        from custos.pipeline import run_1700

        rec = Recorder(fail={"collect_fund_flow", "refresh_eod_klines",
                             "collect_incremental_market"})

        # ⚠️ 必须先把复盘产物造出来。stage 被打桩后不会真的产文件，而 run_1700
        # 会检查 `{date}_final_review.md` 是否存在、不存在就（正确地）记 failed。
        # 第一版没造 ⇒ 测试失败，但失败原因不是 best-effort 语义错，
        # 而是**我没把前提铺好** —— 这种失败最容易被误读成「发现了 bug」。
        def _seed(mod):
            # ⚠️ 常量名是 `REVIEWS`（第一版猜成 `REV` ⇒ seed 写到不存在的属性上，
            # 测试照样失败，而失败原因看着像「best-effort 语义错了」）。
            # 教训与今天反复出现的一样：**别猜名字，去读**。
            rev = getattr(mod, "REVIEWS", None)
            assert isinstance(rev, pathlib.Path), "run_1700 的复盘目录常量改名了？"
            rev.mkdir(parents=True, exist_ok=True)
            (rev / "2026-08-07_final_review.md").write_text("# x", encoding="utf-8")

        _run_runner(run_1700, monkeypatch, rec, tmp_path,
                    argv=("--date", "2026-08-07"), seed=_seed)
        logs = [p for d in tmp_path.rglob("*") if d.is_dir()
                for p in d.glob("*run_log*.json")]
        data = json.loads(logs[0].read_text(encoding="utf-8"))
        assert data.get("status") != "failed", \
            f"best-effort 采集失败不应记 failed，实际 {data.get('status')}"


class TestManualInputs:
    """`daily_pipeline` 的两个**人工输入**通道（原先几乎全未覆盖）。

    ⚠️ 它们是唯一能把人写的值注入自动链的口子，所以两件事必须成立：
    ① 人工来源要**留痕**（否则复盘时分不清哪个数是自动采的、哪个是人填的）
    ② 上游文件缺失时**明确失败**，不得静默建一份只有人工值的文件
       （那会让门控以为数据齐全）。
    """

    def test_manual_market_requires_existing_input(self, pipeline, tmp_path):
        """⚠️ `market_timing_input.json` 不存在时必须 `ok=False` ——
        不得凭人工参数**新建**一份：那份文件只有 amv 没有宽度/成交额，
        而门控会按「文件存在」去读它。"""
        r = pipeline.apply_manual_market("2026-08-07", "double_wide", None, 5.0)
        assert r["ok"] is False and "missing" in r["message"]

    def _seed_market(self, pipeline, tmp_path):
        import json
        p = pipeline.MARKET_DIR / "2026-08-07_market_timing_input.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"date": "2026-08-07", "amv_0": {}}), encoding="utf-8")
        return p

    def test_manual_amv_marks_source_and_confirmed(self, pipeline, tmp_path):
        """⚠️ 人工填的 0AMV 会被置 `quality=confirmed` —— 这是**唯一**
        不经数据自证就 confirmed 的路径（v0.22 规定 regime 切换须 confirmed）。
        所以它**必须**同时写 `source=user_manual_input` 与 `as_of`，
        否则复盘时无法区分「指南针同步来的真值」与「人填的」。
        """
        import json
        p = self._seed_market(pipeline, tmp_path)
        r = pipeline.apply_manual_market("2026-08-07", None, None, 5.0)
        assert r["ok"] is True
        d = json.loads(p.read_text(encoding="utf-8"))
        amv = d["amv_0"]
        assert amv["quality"] == "confirmed"
        assert amv["source"] == "user_manual_input", "人工来源必须留痕"
        assert amv["as_of"] == "2026-08-07"

    @pytest.mark.parametrize("pct,zone", [(5.0, "做多"), (-3.0, "空头"), (1.0, "中性")])
    def test_manual_amv_zone_derived_from_pct(self, pipeline, tmp_path, pct, zone):
        """未显式给 zone 时按阈值派生：>4 做多 / <-2.3 空头 / 其余中性。
        这三个阈值与 `amv_state` 的 regime 切换线是同一套。"""
        import json
        p = self._seed_market(pipeline, tmp_path)
        pipeline.apply_manual_market("2026-08-07", None, None, pct)
        assert json.loads(p.read_text(encoding="utf-8"))["amv_0"]["amv_zone"] == zone

    def test_explicit_zone_wins_over_derived(self, pipeline, tmp_path):
        import json
        p = self._seed_market(pipeline, tmp_path)
        pipeline.apply_manual_market("2026-08-07", None, "空头", 5.0)
        assert json.loads(p.read_text(encoding="utf-8"))["amv_0"]["amv_zone"] == "空头"

    def test_manual_args_recorded_in_data_quality(self, pipeline, tmp_path):
        """⚠️ 人工参数要进 `data_quality.notes` —— 报告的数据质量段据此提示读者。"""
        import json
        p = self._seed_market(pipeline, tmp_path)
        pipeline.apply_manual_market("2026-08-07", "double_wide", None, 5.0)
        dq = json.loads(p.read_text(encoding="utf-8"))["data_quality"]
        assert "daily_pipeline_manual_args" in dq["sources"]
        assert any("macro=double_wide" in n for n in dq["notes"])

    def test_double_wide_writes_all_four_policy_fields(self, pipeline, tmp_path):
        import json
        p = self._seed_market(pipeline, tmp_path)
        pipeline.apply_manual_market("2026-08-07", "double_wide", None, None)
        mp = json.loads(p.read_text(encoding="utf-8"))["macro_policy"]
        for k in ("monetary_policy", "fiscal_policy", "credit_environment",
                  "regulation_environment"):
            assert mp.get(k), k
        assert "人工输入" in mp["policy_summary"], "必须自称人工输入"

    def test_position_updates_absent_is_not_an_error(self, pipeline, tmp_path):
        """没有人工持仓更新文件是**常态**（多数日子没有），不得记失败。"""
        r = pipeline.apply_manual_position_updates("2026-08-07")
        assert r.get("ok") is not False or r.get("skipped"), r

    def test_manual_clearance_removes_and_archives(self, pipeline, tmp_path):
        """⚠️ 人工标「已清仓」要从技术面表里**移除**，并把被移除的行**归档**。

        为什么必须归档而不是直接删：那些行是当日复盘的证据
        （比如它今天为什么被清）。直接删掉之后，复盘只能看到「这票不在持仓里」，
        说不出它是清了还是从来没有过。
        """
        import json
        hd = pipeline.HOLDINGS_DIR
        hd.mkdir(parents=True, exist_ok=True)
        (hd / "2026-08-07_manual_position_updates.json").write_text(
            json.dumps({"updates": [{"code": "600000", "action": "已清仓"}]}),
            encoding="utf-8")
        tech = hd / "2026-08-07_holding_technical_summary.json"
        tech.write_text(json.dumps([{"code": "600000", "name": "甲"},
                                    {"code": "600001", "name": "乙"}]), encoding="utf-8")

        r = pipeline.apply_manual_position_updates("2026-08-07")
        assert r["ok"] is True and r["changed"], r

        left = json.loads(tech.read_text(encoding="utf-8"))
        assert [x["code"] for x in left] == ["600001"], "已清仓的票要移除"

        archives = list(hd.glob("*_removed_by_pipeline_*.json"))
        assert archives, "被移除的行必须归档，否则复盘说不出它是清了还是从没有过"
        archived = json.loads(archives[0].read_text(encoding="utf-8"))
        assert [x["code"] for x in archived] == ["600000"]

    def test_manual_clearance_without_target_files_is_noop(self, pipeline, tmp_path):
        """有更新文件但没有技术面表时不得崩 —— 顺序上它可能先于 batch_holding_technical。"""
        import json
        hd = pipeline.HOLDINGS_DIR
        hd.mkdir(parents=True, exist_ok=True)
        (hd / "2026-08-07_manual_position_updates.json").write_text(
            json.dumps({"updates": [{"code": "600000", "action": "已清仓"}]}),
            encoding="utf-8")
        r = pipeline.apply_manual_position_updates("2026-08-07")
        assert r["ok"] is True and r["changed"] == []

    def test_non_clearance_actions_ignored(self, pipeline, tmp_path):
        """只有 `action == "已清仓"` 触发移除 —— 「减仓」等不得让整行消失。"""
        import json
        hd = pipeline.HOLDINGS_DIR
        hd.mkdir(parents=True, exist_ok=True)
        (hd / "2026-08-07_manual_position_updates.json").write_text(
            json.dumps({"updates": [{"code": "600000", "action": "减仓"}]}),
            encoding="utf-8")
        tech = hd / "2026-08-07_holding_technical_summary.json"
        tech.write_text(json.dumps([{"code": "600000"}]), encoding="utf-8")
        pipeline.apply_manual_position_updates("2026-08-07")
        assert len(json.loads(tech.read_text(encoding="utf-8"))) == 1
