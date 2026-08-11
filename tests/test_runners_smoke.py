# -*- coding: utf-8 -*-
"""五个 runner 在**非交易日**的冒烟：走到日历门就退出，无副作用。

⚠️ **这一层能测到的很有限，交易日一律 skip。** 2026-08-07 亲身验证了它的盲区：
`run_1445` 从 2026-08-06 起 `main()` 一进第一个 stage 就
`NameError: name 'TOOLS' is not defined`（那天的提交把 `TOOLS` 加进注释却没加进导入），
**14:45 报告整整一天产不出来**，而这个文件的两条测试当天全部 skip、
`test_run_1445.py` 又从不调 `main()`、`--help` 冒烟在 NameError 之前就 return 了。

⇒ 编排逻辑（stage 顺序 / 失败传播 / 门控码）由
   **`tests/test_pipeline_orchestration.py`** 负责 —— 它打桩 `_stage` 后真跑
   `main()` 主体，不受日期影响、不 spawn 子进程。

保留本文件的理由：它是唯一**真的以子进程启动 runner** 的测试，
能抓到「模块导入期就炸」（语法错、import 循环、引导顺序错）这类
打桩测试抓不到的问题。**但不要把它当编排测试用。**
"""
import subprocess
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "src"
BASE = TOOLS.parent

sys.path.insert(0, str(TOOLS))
from pipeline_kit import _extract_json
from paths import LOGS

RUNNERS = {
    "pipeline/run_0850.py": "休市",
    "pipeline/run_0905.py": "休市",
    "pipeline/run_1445.py": "休市",
    "pipeline/run_1700.py": "休市",
    "pipeline/run_1800.py": "休市",
}

LOG_DIR = LOGS


def _is_trading_day(target: str) -> bool:
    r = subprocess.run(
        ["uv", "run", "python", str(TOOLS / "datasource/trading_calendar.py"), "--check-date", target],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(BASE),
    )
    d = _extract_json(r.stdout)
    if "is_trading_day" in d:
        return bool(d["is_trading_day"])
    return True  # calendar broken → skip to stay safe


class _RunLogRestore:
    """runner 子进程走日历门时会把 run_log 落进**真实** artifacts/logs/（LOG_DIR 是模块级
    常量，子进程打不了桩）。不处理的话：① 干净环境下被 repo hygiene 测试抓到；
    ② 目标机上若当天已有同名师手动日志会被覆盖。所以：跑前备份、跑后恢复/删除。
    """

    def __init__(self, testcase: unittest.TestCase, script: str, target: str):
        session = script.removeprefix("run_").removesuffix(".py")
        self.path = LOG_DIR / f"{target}_{session}_run_log.json"
        self.prior = self.path.read_bytes() if self.path.is_file() else None
        testcase.addCleanup(self._restore)

    def _restore(self):
        if self.prior is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.write_bytes(self.prior)


@unittest.skipIf(_is_trading_day(date.today().strftime("%Y-%m-%d")), "today is a trading day")
class RunnerSmokeTests(unittest.TestCase):
    def test_runner_exits_cleanly_on_closed_day(self):
        target = date.today().strftime("%Y-%m-%d")
        for script, marker in RUNNERS.items():
            with self.subTest(script=script):
                _RunLogRestore(self, script, target)
                r = subprocess.run(
                    ["uv", "run", "python", str(TOOLS / script)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    cwd=str(BASE), timeout=120,
                )
                self.assertEqual(r.returncode, 0, f"{script} failed: {r.stderr[:300]}")
                self.assertIn(marker, r.stdout, f"{script} missing closed-day message")

    def test_runner_date_option_on_last_sunday(self):
        """--date <最近周日> must actually take effect: exit 0, closed-day
        message, and the message carries the requested date."""
        today = date.today()
        last_sunday = today - timedelta(days=(today.weekday() + 1) % 7)
        target = last_sunday.strftime("%Y-%m-%d")
        if _is_trading_day(target):  # rare make-up trading Sunday → stay safe
            self.skipTest(f"{target} is a make-up trading day")
        for script, marker in RUNNERS.items():
            with self.subTest(script=script):
                _RunLogRestore(self, script, target)
                r = subprocess.run(
                    ["uv", "run", "python", str(TOOLS / script), "--date", target],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    cwd=str(BASE), timeout=120,
                )
                self.assertEqual(r.returncode, 0, f"{script} failed: {r.stderr[:300]}")
                self.assertIn(marker, r.stdout, f"{script} missing closed-day message")
                self.assertIn(target, r.stdout, f"{script} output does not reflect --date {target}")


if __name__ == "__main__":
    unittest.main()
