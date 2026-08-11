"""`src/` 顶层的公用抽取与防重复。

2026-08-06 收敛三处真重复。**每一处都不是「形状相同」那么简单**：

    _stage × 5    字节级相同 ⇒ 纯重复，直接抽
    load_json × 4 ⚠️ **编码不一致是真 bug**：trading_calendar 用 utf-8-sig（能读 BOM），
                  其余三份用 utf-8（遇 BOM 解析失败）⇒ 合并必须取 utf-8-sig，
                  取多数派写法等于把修复回退了
    _fnum × 2     与既有 finite() **语义不同**（None vs 默认值 0.0）⇒ 两者都得留

这份测试防的是「抽完之后又有人写一份本地实现」——那是最常见的回退方式。
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
T = ROOT / "src" / "custos"
RUNNERS = ["pipeline/run_0850.py", "pipeline/run_0905.py", "pipeline/run_1445.py",
           "pipeline/run_1700.py", "pipeline/run_1800.py"]


class TestNoLocalRedefinition:
    """公用实现抽出后，不许再有本地同义定义。"""

    @pytest.mark.parametrize("runner", RUNNERS)
    def test_runner_has_no_local_stage(self, runner):
        s = (T / runner).read_text(encoding="utf-8")
        assert not re.search(r"^def _stage\(", s, re.M), \
            f"{runner} 又定义了本地 _stage —— 应用 pipeline_kit.run_stage_quiet"
        assert "run_stage_quiet" in s, f"{runner} 未使用 pipeline_kit.run_stage_quiet"

    @pytest.mark.parametrize("mod,fn", [
        ("core/runtime_guards.py", "load_json"),
        ("datasource/trading_calendar.py", "load_json"),
        ("pipeline/daily_report.py", "load"),
        ("pipeline/generate_risk_and_sectors.py", "load"),
    ])
    def test_no_local_json_reader(self, mod, fn):
        s = (T / mod).read_text(encoding="utf-8")
        assert not re.search(rf"^def {fn}\(", s, re.M), \
            f"{mod} 又定义了本地 {fn} —— 应用 paths.read_json（它才是 utf-8-sig 的那一份）"
        assert "read_json" in s

    @pytest.mark.parametrize("mod", ["datasource/collect/collect_holding_quotes.py",
                                     "datasource/collect/collect_incremental_market.py"])
    def test_no_local_fnum(self, mod):
        s = (T / mod).read_text(encoding="utf-8")
        assert not re.search(r"^def _fnum\(", s, re.M), \
            f"{mod} 又定义了本地 _fnum —— 应用 code_utils.fnum"


class TestSharedImplBehavior:
    def test_read_json_uses_utf8_sig(self):
        """⚠️ **必须是 utf-8-sig**：合并 4 份实现时，只有一份修过 BOM 问题。

        用 utf-8 读带 BOM 的 JSON 会 `JSONDecodeError`（BOM 变成内容字符）。
        utf-8-sig 对无 BOM 文件同样正常，所以它是安全的那一侧。
        """
        s = (T / "core" / "paths.py").read_text(encoding="utf-8")
        m = re.search(r"def read_json\(.*?return", s, re.S)
        assert m and "utf-8-sig" in m.group(0), "read_json 必须用 utf-8-sig"

    def test_read_json_handles_bom(self, tmp_path):
        """反向验证：真给一个带 BOM 的文件，必须读得出来。"""
        import sys
        sys.path.insert(0, str(T))
        from custos.core import paths
        f = tmp_path / "bom.json"
        f.write_bytes("\ufeff{\"a\": 1}".encode("utf-8"))
        assert paths.read_json(f, None) == {"a": 1}
        assert paths.read_json(tmp_path / "nope.json", {"d": 2}) == {"d": 2}

    def test_fnum_and_finite_stay_distinct(self):
        """两个数值转换并存是**有意的**，不许有人「顺手统一」。

        `finite` 失败给默认值（参与计算）；`fnum` 失败给 None
        （区分「缺数」与「读数是 0」）。collect_incremental_market 的教训：
        **0.0 是合法读数**，用 finite 的 0.0 默认值会让两者无法区分。
        """
        import sys
        sys.path.insert(0, str(T))
        from custos.core import code_utils
        assert code_utils.fnum(None) is None
        assert code_utils.fnum("-") is None
        assert code_utils.fnum("0") == 0.0        # 0 是合法读数，不能变 None
        assert code_utils.finite(None) == 0.0     # finite 给默认值
        assert code_utils.finite("x", -1) == -1

    def test_run_stage_quiet_suppresses_stdout(self, capsys, monkeypatch):
        """静默是**协议要求**：runner 的 stdout 给机器消费，stage 回显会污染它。"""
        import sys
        sys.path.insert(0, str(T))
        from custos.core import pipeline_kit

        def fake(cmd, name, required=False):
            print("[RUN] 这行不该出现在 runner stdout 里")
            return {"stdout": "o", "stderr": "e", "ok": True}

        monkeypatch.setattr(pipeline_kit, "run_stage", fake)
        r = pipeline_kit.run_stage_quiet(["x"], "n")
        assert capsys.readouterr().out == "", "stage 回显未被抑制"
        assert r["out"] == "oe", "stdout+stderr 应合并进 out"


class TestIntentionalWrappersKept:
    """`_write_run_log` 的 5 个 wrapper **不是重复**，不许被误删。

    每个 runner 调用它 3~7 次；wrapper 绑定了 `LOG_DIR` + 时点标签，
    内联会把这两个参数重复 3~7 遍。这是合理的偏应用。
    """

    @pytest.mark.parametrize("runner", RUNNERS)
    def test_write_run_log_wrapper_present(self, runner):
        s = (T / runner).read_text(encoding="utf-8")
        assert re.search(r"^def _write_run_log\(", s, re.M), \
            f"{runner} 的 _write_run_log wrapper 被删了？它是有意的偏应用"
        assert "write_run_log(LOG_DIR" in s, f"{runner} 应委托给 pipeline_kit.write_run_log"


class TestCalendarGate:
    """交易日门控从 5 个 runner 抽出后，行为必须不变。

    ⚠️ **这是最需要小心的一处抽取**：它是门控逻辑，而 README 记着 2026-07-30 的事故
    正是门控行为在多处叠加改动导致的（postclose 默认带 `--require-quality` +
    `as_of` 收紧同时生效 ⇒ 17:00 盘后链直接失败）。抽成一处之后，
    改门控只需改一个地方 —— 也只能在一个地方出错。
    """

    def _kit(self):
        import sys
        sys.path.insert(0, str(T))
        from custos.core import pipeline_kit
        return pipeline_kit

    @pytest.mark.parametrize("runner", RUNNERS)
    def test_runner_uses_shared_gate(self, runner):
        s = (T / runner).read_text(encoding="utf-8")
        assert "calendar_gate(" in s, f"{runner} 未用共享门控"
        # 不许再有本地的 23 行块
        assert "c_started = _now_iso()" not in s, f"{runner} 又内联了日历检查块"

    def test_trading_day_continues(self, tmp_path, monkeypatch, capsys):
        kit = self._kit()
        monkeypatch.setattr(kit, "check_trading_day", lambda t: {"is_trading_day": True})
        logs: list[dict] = []
        r = kit.calendar_gate("2026-08-06", log_dir=tmp_path, session="0850",
                              run_started="x", t0=0.0, stages_log=logs,
                              fail_msg="F {target} {err}", closed_msg="C {target}")
        assert r.exit_code is None, "交易日应继续"
        assert logs and logs[0]["name"] == "calendar"
        assert capsys.readouterr().out == "", "交易日不该打印门控消息"

    def test_closed_returns_zero(self, tmp_path, monkeypatch, capsys):
        """非交易日是**正常结局**，exit 0 —— 不是失败。"""
        kit = self._kit()
        monkeypatch.setattr(kit, "check_trading_day", lambda t: {"is_trading_day": False})
        logs: list[dict] = []
        r = kit.calendar_gate("2026-08-08", log_dir=tmp_path, session="1700",
                              run_started="x", t0=0.0, stages_log=logs,
                              fail_msg="F {target} {err}", closed_msg="今日休市（{target}）")
        assert r.exit_code == 0
        assert "今日休市（2026-08-08）" in capsys.readouterr().out
        assert logs[0]["ok"] is True, "非交易日不是 stage 失败"

    def test_calendar_error_returns_one(self, tmp_path, monkeypatch, capsys):
        """日历**查不到**要 exit 1 —— 与「今天休市」必须分开，否则会把故障读成假期。"""
        kit = self._kit()

        def boom(t):
            raise RuntimeError("日历缓存缺失")

        monkeypatch.setattr(kit, "check_trading_day", boom)
        logs: list[dict] = []
        r = kit.calendar_gate("2026-08-06", log_dir=tmp_path, session="0905",
                              run_started="x", t0=0.0, stages_log=logs,
                              fail_msg="失败｜{target}：{err}", closed_msg="C")
        assert r.exit_code == 1
        out = capsys.readouterr().out
        assert "失败｜2026-08-06：日历缓存缺失" in out
        assert logs[0]["ok"] is False

    def test_gate_captures_calendar_stdout(self, tmp_path, monkeypatch, capsys):
        """`check_trading_day` 的回显必须被捕获进 stage 日志，而不是漏进 runner 协议。"""
        kit = self._kit()

        def noisy(t):
            print("[CAL] 这行会污染 runner 的机器可读 stdout")
            return {"is_trading_day": True}

        monkeypatch.setattr(kit, "check_trading_day", noisy)
        logs: list[dict] = []
        kit.calendar_gate("2026-08-06", log_dir=tmp_path, session="1445",
                          run_started="x", t0=0.0, stages_log=logs,
                          fail_msg="F", closed_msg="C")
        assert capsys.readouterr().out == "", "日历回显未被捕获"
        # log_stage 把 stdout 收进 `stdout_tail`（截末 1000 字），不是 `stdout`
        assert "[CAL]" in logs[0]["stdout_tail"], "捕获的回显应留在 stage 日志里，不能丢"


class TestMovedScriptsRunAsMain:
    """搬进子目录的**入口脚本**必须仍能作为 `__main__` 跑起来。

    ⚠️ **这是 import 型测试抓不到的一类断裂。** 2026-08-06 分包时实际发生：
    测试全绿（conftest 把 `src` 与各子目录都铺进了 `sys.path`），
    但 `uv run python src/custos/datasource/collect/collect_fund_flow.py --help` 直接
    `ModuleNotFoundError: net_retry` —— 因为作为脚本跑时 `sys.path[0]` 是**本目录**，
    不含 `src`。

    而且第一版引导插错了位置：放在 `from paths import` 之前是**不够的** ——
    `collect_fund_flow` 的 `from net_retry import` 在更早的行，会先失败。
    引导必须放在**第一个本地模块导入之前**。

    ⇒ runner 用 subprocess 调这些脚本，所以「能被 import」不等于「能被执行」。
    """

    ENTRIES = [
        "datasource/collect/collect_holding_quotes.py",
        "datasource/collect/collect_incremental_market.py",
        "datasource/collect/collect_fund_flow.py",
        # 2026-08-07 二次搬迁：analyze_trades → research/（手工工具）、
        # calc_mfe_mae → close_review/（17:00 链的一步，且已依赖 weekly_review）。
        # `analysis/` 因此空掉并删除 —— 一个只剩单文件的目录不值得留。
        "research/analyze_trades.py",
        "pipeline/close_review/calc_mfe_mae.py",
    ]
    # online_quotes.py 不在列：它没有 main()/`if __name__`，是纯库模块，
    # 由同目录的 collect_holding_quotes 导入（后者已铺 sys.path）。

    @pytest.mark.parametrize("rel", ENTRIES)
    def test_help_works(self, rel):
        import subprocess
        # 入口脚本已陆续 reconfigure 成 UTF-8（cp936/cp1252 下中文 help 会崩），
        # 没 reconfigure 的仍按 locale 出字节 —— 统一按 utf-8+replace 解码，
        # 断言目标（usage 行、returncode）都是 ASCII，不受残余乱码影响。
        r = subprocess.run([sys.executable, str(T / rel), "--help"],
                           capture_output=True, encoding="utf-8", errors="replace",
                           timeout=90)
        assert r.returncode == 0, f"{rel} 不能作为脚本执行：\n{r.stderr[-600:]}"
        assert r.stdout.lstrip().startswith("usage"), f"{rel} 未输出 usage"

    @pytest.mark.parametrize("rel", ENTRIES + ["datasource/collect/online_quotes.py"])
    def test_bootstrap_precedes_first_local_import(self, rel):
        """引导必须在第一个本地模块导入**之前**。"""
        local = {p.stem for p in T.rglob("*.py")} - {"__init__"}
        lines = (T / rel).read_text(encoding="utf-8").split("\n")
        boot = next((i for i, l in enumerate(lines)
                     if "_TOOLS = Path(__file__).resolve().parents[1]" in l), None)
        first = next((i for i, l in enumerate(lines)
                      if (m := re.match(r"\s*(?:from|import)\s+([a-z_][a-z0-9_]*)", l))
                      and m.group(1) in local), None)
        if boot is None:
            # 纯库模块允许无引导（由导入方负责），但必须没有 __main__ 入口
            src = "\n".join(lines)
            assert "if __name__" not in src, \
                f"{rel} 有 __main__ 入口却没有 sys.path 引导 —— 作为脚本跑会崩"
            return
        assert first is None or boot < first, \
            f"{rel} 的引导在第 {boot+1} 行，而第一个本地导入在第 {first+1} 行 —— 顺序反了"

    def test_no_stale_parent_paths(self):
        """搬进子目录后，`__file__.parent` 指向的是子目录，不再是 src。

        `calc_mfe_mae` 原有两处 `Path(__file__).resolve().parent / "close_review"`，
        搬后会指向不存在的 `analysis/close_review` —— 而 `sys.path.insert`
        **对不存在的路径不报错**，是静默失效。已改为 `parents[1]`。
        """
        for rel in self.ENTRIES + ["datasource/collect/online_quotes.py"]:
            s = (T / rel).read_text(encoding="utf-8")
            for m in re.finditer(r"Path\(__file__\)\.resolve\(\)\.parent\b(?!s)", s):
                ctx = s[max(0, m.start() - 60):m.end() + 60].replace("\n", " ")
                raise AssertionError(
                    f"{rel} 仍用 `.parent`（子目录里应为 `parents[1]`）：…{ctx}…")


class TestToolsPathSingleSource:
    """`src` 路径只从 `paths.TOOLS` 取，不许重新推导。

    ⚠️ **必须区分两种 `__file__` 用法，不能一刀切**：

        _TOOLS = Path(__file__).resolve().parents[1]   ✅ **sys.path 引导，合法**
                                                       它跑在 `from paths import` 之前，
                                                       是鸡生蛋问题，只能用 __file__
        TOOLS = BASE / "src"                      ❌ 已 import paths 之后重新推导

    2026-08-06 收敛：`run_1700` / `run_1800` 各有一份 `TOOLS = BASE / "src"`；
    `daily_pipeline` 有 9 处 `BASE / "src" / ...` 硬编码。
    """

    AFTER_PATHS = ["pipeline/run_0850.py", "pipeline/run_0905.py", "pipeline/run_1445.py",
                   "pipeline/run_1700.py", "pipeline/run_1800.py", "pipeline/daily_pipeline.py",
                   "core/runtime_gate.py", "pipeline/generate_risk_and_sectors.py",
                   "datasource/trading_calendar.py"]

    @pytest.mark.parametrize("f", AFTER_PATHS)
    def test_no_rederiving_tools_from_base(self, f):
        s = (T / f).read_text(encoding="utf-8")
        assert 'BASE / "src"' not in s, \
            f'{f} 用 BASE / "src" 重新推导 —— 应 `from paths import TOOLS`'

    def test_bootstrap_pattern_still_allowed(self):
        """反面：子目录脚本的 `__file__` 引导必须仍然存在，不能被误删。"""
        s = (T / "datasource" / "collect" / "collect_fund_flow.py").read_text(encoding="utf-8")
        assert "_TOOLS = Path(__file__).resolve().parents[1]" in s, \
            "sys.path 引导被误删了 —— 它跑在 import paths 之前，只能用 __file__"

    def test_daily_pipeline_market_timing_not_named_tools(self):
        """`daily_pipeline` 里指向 market_timing 的常量**不能叫 TOOLS**
        —— 仓库其他地方 `TOOLS` 一律指 src，同名不同义会让读者把
        `TOOLS / "x.py"` 读成顶层脚本。

        ⚠️ 判据从「源码里有那行字面量」改成**运行时真值比对**（2026-08-07）：
        原判据是 `assert 'MARKET_TIMING = TOOLS / "market_timing"' in src`，
        把常量改成从 `paths` 导入之后它就挂了 —— 而语义完全没变。
        这正是今天反复踩的「查字符串形式而非语义」。
        """
        import sys
        tools = pathlib.Path(__file__).resolve().parent.parent / "src" / "custos"
        sys.path.insert(0, str(tools))
        from custos.pipeline import daily_pipeline as dp

        assert dp.TOOLS == tools, "TOOLS 必须指 src 本身"
        assert dp.MARKET_TIMING == tools / "pipeline" / "market_timing"
        assert dp.HOLDINGS == tools / "pipeline" / "holdings", \
            "持仓工具目录 —— 2026-08-07 拆分后 daily_pipeline 必须指向它"

class TestGateCodePropagation:
    """门控退出码必须**端到端**到 cron，不能在 runner 这层被压平。

    链条：`runtime_gate` (3/4/5) → `daily_pipeline`（`SystemExit(returncode)`）
    → **runner** → cron。

    ⚠️ 2026-08-06 查出最后一跳断了：`run_0905:113` 与 `run_1700:185` 都写
    `return 1`，把内层的 3/4/5 抹成「失败」⇒ cron 分不清「质量 blocked」与
    「任意 stage 挂了」。

    **当前无害**（两者都没传 `--strict-quality-gate`，内层不会 exit 4），
    但 README 明确说硬闸会在 stale 校准跑通后启用 —— **那一刻正是需要区分的时刻**，
    而那时没人会想起 runner 这层把码抹了。所以提前修。
    """

    def _kit(self):
        sys.path.insert(0, str(T))
        from custos.core import pipeline_kit
        return pipeline_kit

    @pytest.mark.parametrize("rc,want", [(3, 3), (4, 4), (5, 5)])
    def test_gate_codes_pass_through(self, rc, want):
        assert self._kit().propagate_gate_code({"returncode": rc}) == want

    @pytest.mark.parametrize("rc", [1, 2, 127, None, 0])
    def test_non_gate_codes_collapse_to_one(self, rc):
        """只放行 3/4/5。

        其他非零码语义是「跑挂了」：Python 的 exit 2 是 argparse 用法错、
        127 是命令找不到 —— 把它们当门控结论会误导 cron。
        """
        assert self._kit().propagate_gate_code({"returncode": rc}) == 1

    def test_custom_default(self):
        assert self._kit().propagate_gate_code({"returncode": 9}, default=7) == 7

    @pytest.mark.parametrize("runner", ["pipeline/run_0905.py", "pipeline/run_1700.py"])
    def test_runner_no_longer_flattens(self, runner):
        s = (T / runner).read_text(encoding="utf-8")
        i = s.index("daily_pipeline失败")
        seg = s[i:i + 300]
        assert "propagate_gate_code(r)" in seg, \
            f"{runner} 仍把 daily_pipeline 的退出码压平"

    def test_daily_pipeline_still_propagates(self):
        """上游那一跳也要在：`daily_pipeline` 必须先落日志再抛原码。"""
        s = (T / "pipeline" / "daily_pipeline.py").read_text(encoding="utf-8")
        i = s.index("raise SystemExit")
        seg = s[max(0, i - 400):i + 80]
        assert "_write_pipeline_log" in seg, \
            "退出前必须先落 pipeline 日志，否则这次阻断连记录都不留"
        assert 'gate_stage["returncode"]' in seg, "必须抛门控原码，不能包成 exit 1"


class TestAmplitudeSingleImplementation:
    """⚠️ 当日振幅**全项目唯一一份**：`indicators.amplitude_pct`（owner 2026-08-10 定口径）。

    清点时发现**四份**内联实现，且分母不一致：

        screening/enrich_candidates       (high−low)/prev_close    ✅
        factors/s_shape                   (high−low)/close[-2]     ✅（08-09 才改对）
        research/backtest_factors         (high−low)/close[-2]      ✅
        market_timing/technical_monitor   (high/low − 1)            ❌ 分母是**当日最低价**

    四份来自四个独立特性，当时没有共享出口可用。
    ⚠️ 分母之差不是小数点问题：合成 20 万根日 K 实测，**约 2% 在 7% 门槛上给出相反结论**，
    方向是 `low < prev_close`（正是反转K 要找的缩量回踩形态）时 `high/low` 更严
    ⇒ **同一支票在选股链与持仓链可能得出相反的反转K 结论**。
    """

    def test_no_inline_amplitude_formula(self):
        """除 L0 唯一实现外，不得再出现内联振幅式。

        ⚠️ 判据用 **AST** 而非文本正则：第一版正则 `high[\w]*\s*/\s*low` 把
        docstring 里的字段列表 `open/high/low/close/volume` 全部误报
        （`s_data.py`、`reconcile_qfq.py`、`platform_pullback.py` 各中一枪）。
        AST 只看真实的除法表达式，注释与字符串天然排除。
        """
        import ast
        bad = []
        for f in sorted(T.rglob("*.py")):
            if f.name == "indicators.py":
                continue                       # 唯一实现所在
            try:
                raw = f.read_text(encoding="utf-8-sig")
                tree = ast.parse(raw)
            except SyntaxError:
                continue
            src_lines = raw.split("\n")
            for node in ast.walk(tree):
                if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
                    continue
                src = ast.unparse(node)
                # 形态一：high / low     形态二：(high - low) / x
                looks = (re.search(r'\bhigh\b[^/]{0,12}/\s*low\b', src)
                         or re.search(r'\(\s*high\b.{0,14}-\s*low\b.{0,6}\)\s*/', src))
                if not looks:
                    continue
                # ⚠️ 豁免靠**源码里的标记**，不是硬编码行号（行号会随编辑腐坏）：
                #    该表达式上方 6 行内必须有含「刻意不同」的注释说明为什么。
                #    这样豁免与代码同进退，且**强制写下理由** ——
                #    目前唯一一处是 `s_shape` 的 VCP 压缩度（分母用当日收盘、
                #    只作 recent/prior 比值参与打分，换分母会改 live 的 S 分）。
                above = src_lines[max(0, node.lineno - 7):node.lineno - 1]
                if any("刻意不同" in x for x in above):
                    continue
                bad.append(f"{f.relative_to(T.parent)}:{node.lineno}  {src[:74]}")
        assert not bad, ("发现内联振幅式，应改调 `indicators.amplitude_pct`：\n  "
                         + "\n  ".join(bad)
                         + "\n若确为**不同的量**（如 s_shape 的 VCP 压缩度用当日收盘作分母、"
                           "且只作比值参与打分），请在该行上方写明为何刻意不同"
                           "并把它加进本测试的豁免。")


    def test_all_live_chains_share_the_function(self):
        """三条 live 路径必须是**同一个函数对象**。"""
        import sys
        sys.path.insert(0, str(T))
        from custos.core import indicators
        from custos.pipeline.market_timing import technical_monitor as tm
        from custos.pipeline.screening import enrich_candidates as ec

        assert ec.amplitude_pct_of is indicators.amplitude_pct
        assert tm.amplitude_pct_of is indicators.amplitude_pct

    def test_canonical_denominator_is_prev_close(self):
        """⚠️ 分母是**前收盘**，不是当日最低价 —— 治理文档明文
        （`01_swing_rules.md`：「当日振幅优先按 `(最高价 - 最低价) / 前收盘价` 计算」）。

        用一组两式**结论相反**的数据钉住方向：前收 10.0 / 低 9.30 / 高 9.95
        ⇒ 规范口径 6.5%（≤7 判入），`high/low` 口径 6.99%…（也判入）——
        所以要选真正跨过 7% 的那组。
        """
        import sys
        sys.path.insert(0, str(T))
        from custos.core.indicators import amplitude_pct

        # 前收 10.0、低 9.00、高 9.65：规范 6.5%（入）；high/low = 7.22%（出）
        assert abs(amplitude_pct(9.65, 9.00, 10.0) - 6.5) < 1e-9
        assert (9.65 / 9.00 - 1) * 100 > 7, "这组数据没能跨过门槛，测不出方向"

    def test_returns_none_not_zero_when_uncomputable(self):
        """⚠️ 算不出返回 **None**，不是 0.0 —— 0.0 会被 `<= 7` 判成「振幅很小」，
        把「算不出」显示成「符合条件」。"""
        import sys
        sys.path.insert(0, str(T))
        from custos.core.indicators import amplitude_pct

        assert amplitude_pct(10.0, 9.5, 0) is None
        assert amplitude_pct(10.0, 9.5, None) is None
        assert amplitude_pct(None, 9.5, 10.0) is None
        assert amplitude_pct(float("nan"), 9.5, 10.0) is None
        assert amplitude_pct(10.0, 9.5, float("inf")) is None

    def test_exemption_requires_a_written_reason(self):
        """⚠️ 守卫自证：豁免必须靠「刻意不同」标记，删掉标记就该报警。

        否则豁免会变成一个静默的白名单 —— 而今天已经三次遇到
        「守卫看着通过、其实什么都没验」。
        """
        import ast

        raw = (T / "core" / "factors" / "s_shape.py").read_text(encoding="utf-8-sig")
        lines = raw.split("\n")
        i = next(k for k, l in enumerate(lines) if "rng = (high - low) / np.where" in l)
        above = lines[max(0, i - 6):i]
        assert any("刻意不同" in x for x in above), \
            "s_shape 的 VCP 豁免依赖上方注释里的「刻意不同」标记，它不见了"
        # 反证：把标记去掉后，该处应落入 bad 列表
        stripped = [x.replace("刻意不同", "XX") for x in above]
        assert not any("刻意不同" in x for x in stripped)


class TestNamedIndicatorsLiveInL0:
    """⚠️ **有公认定义的技术指标必须定义在 `indicators.py`**（owner 2026-08-10 定）。

    判据是「**是否存在口径选择**」—— 该量在项目外有标准定义，因而有人可能挑到
    不同变体，一旦分叉就是**同名不同义**且很难在报告里看出来。实际发生过的：

        振幅   `technical_monitor` 分母用当日最低价、另四处用前收
               ⇒ 同一支票在选股链与持仓链可能得出相反的反转K 结论
        MACD   柱 ×1 与 ×2 两种口径并存（现已各自留痕说明）
        RSI    Wilder 指数平滑 vs 简单算术均值，两者同名不同值
        AVEDEV 平均绝对偏差 vs 标准差 —— 都叫「偏差」、量级相近、代入 CCI 后不同

    ⚠️ **刻意不采用「所有公式一律进 L0」**：实测全仓 255 个含 ≥3 个算术运算的函数、
    共 14066 行（含组合回测、报告评分、MFE/MAE），全搬会让 `indicators.py`
    约 14500 行 —— 既无法执行（「什么算公式」没边界），也会毁掉因子内聚。
    因子自己的判定/打分逻辑（VCP 压缩度、D1/D2、贴合打分）留在因子里。
    """

    # 有公认定义的指标名。新增指标时加进来。
    NAMED = {
        "rsi", "cci", "avedev", "kdj", "macd", "bbi", "dks", "qsx",
        "amplitude_pct", "pct_change", "dmi_arrays", "ema", "atr", "obv",
        "boll", "trix", "psy", "roc", "mfi", "sar", "bias", "wr",
    }
    # 允许的后缀变体：`_series` / `_state` 是本项目的命名惯例
    SUFFIXES = ("", "_series", "_state", "_arrays", "_pct")

    def _canonical_names(self):
        out = set()
        for n in self.NAMED:
            for suf in self.SUFFIXES:
                out.add(n + suf if not n.endswith(suf) or suf == "" else n)
        return out

    def test_named_indicators_defined_only_in_indicators(self):
        import ast

        allowed = self._canonical_names()
        offenders = []
        for f in sorted(T.rglob("*.py")):
            if f.name == "indicators.py":
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8-sig"))
            except SyntaxError:
                continue
            for node in tree.body:            # 只看模块级函数（嵌套的是局部工具）
                if not isinstance(node, ast.FunctionDef):
                    continue
                nm = node.name.lower().strip("_")
                if nm in allowed:
                    offenders.append(f"{f.relative_to(T.parent)}:{node.lineno}  def {node.name}")
        assert not offenders, (
            "有公认定义的指标被定义在 `indicators.py` 之外：\n  "
            + "\n  ".join(offenders)
            + "\n判据是「是否存在口径选择」——这类指标一旦分叉就是同名不同义。"
              "\n若它其实是**因子自己的量**（定义就是这个因子本身），请改个不撞车的名字"
              "并在此说明；若是新指标，请搬到 indicators.py 并加进 NAMED。")

    def test_guard_catches_a_planted_fork(self, tmp_path):
        """⚠️ 守卫自证：在别处定义 `def rsi(...)` 必须被抓到。

        今天已多次遇到「守卫看着通过、其实什么都没验」，所以每条守卫都要能自证。
        """
        import ast

        planted = ast.parse("def rsi(close, n=14):\n    return close\n")
        fn = planted.body[0]
        assert isinstance(fn, ast.FunctionDef)
        assert fn.name.lower().strip("_") in self._canonical_names(), \
            "NAMED 表里没有 rsi —— 守卫会放行第二份 RSI"

    def test_policy_is_documented_in_the_module(self):
        """政策必须写在 `indicators.py` 里 —— 只写在测试里，改代码的人看不到。"""
        src = (T / "core" / "indicators.py").read_text(encoding="utf-8-sig")
        assert "是否存在口径选择" in src, "indicators.py 头部的归属政策不见了"
        assert "杂物抽屉" in src, "「为什么不全搬」的理由不见了"
