"""`07_tools/` 顶层的公用抽取与防重复。

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

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
T = ROOT / "07_tools"
RUNNERS = ["run_0850.py", "run_0905.py", "run_1445.py", "run_1700.py", "run_1800.py"]


class TestNoLocalRedefinition:
    """公用实现抽出后，不许再有本地同义定义。"""

    @pytest.mark.parametrize("runner", RUNNERS)
    def test_runner_has_no_local_stage(self, runner):
        s = (T / runner).read_text(encoding="utf-8")
        assert not re.search(r"^def _stage\(", s, re.M), \
            f"{runner} 又定义了本地 _stage —— 应用 pipeline_kit.run_stage_quiet"
        assert "run_stage_quiet" in s, f"{runner} 未使用 pipeline_kit.run_stage_quiet"

    @pytest.mark.parametrize("mod,fn", [
        ("runtime_guards.py", "load_json"),
        ("trading_calendar.py", "load_json"),
        ("daily_report.py", "load"),
        ("generate_risk_and_sectors.py", "load"),
    ])
    def test_no_local_json_reader(self, mod, fn):
        s = (T / mod).read_text(encoding="utf-8")
        assert not re.search(rf"^def {fn}\(", s, re.M), \
            f"{mod} 又定义了本地 {fn} —— 应用 paths.read_json（它才是 utf-8-sig 的那一份）"
        assert "read_json" in s

    @pytest.mark.parametrize("mod", ["collect_holding_quotes.py", "collect_incremental_market.py"])
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
        s = (T / "paths.py").read_text(encoding="utf-8")
        m = re.search(r"def read_json\(.*?return", s, re.S)
        assert m and "utf-8-sig" in m.group(0), "read_json 必须用 utf-8-sig"

    def test_read_json_handles_bom(self, tmp_path):
        """反向验证：真给一个带 BOM 的文件，必须读得出来。"""
        import sys
        sys.path.insert(0, str(T))
        import paths
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
        import code_utils
        assert code_utils.fnum(None) is None
        assert code_utils.fnum("-") is None
        assert code_utils.fnum("0") == 0.0        # 0 是合法读数，不能变 None
        assert code_utils.finite(None) == 0.0     # finite 给默认值
        assert code_utils.finite("x", -1) == -1

    def test_run_stage_quiet_suppresses_stdout(self, capsys, monkeypatch):
        """静默是**协议要求**：runner 的 stdout 给机器消费，stage 回显会污染它。"""
        import sys
        sys.path.insert(0, str(T))
        import pipeline_kit

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
        import pipeline_kit
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
