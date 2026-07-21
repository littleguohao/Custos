# -*- coding: utf-8 -*-
"""Smoke tests for the five daily runners on non-trading days.

Runners exit right after the trading-calendar gate when the market is
closed, so these tests are side-effect free. On trading days they are
skipped to avoid triggering the real pipelines.
"""
import subprocess
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "07_tools"
BASE = TOOLS.parent

sys.path.insert(0, str(TOOLS))
from pipeline_kit import _extract_json

RUNNERS = {
    "run_0850.py": "休市",
    "run_0905.py": "休市",
    "run_1445.py": "休市",
    "run_1700.py": "休市",
    "run_1800.py": "休市",
}


def _is_trading_day(target: str) -> bool:
    r = subprocess.run(
        ["uv", "run", "python", str(TOOLS / "trading_calendar.py"), "--check-date", target],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(BASE),
    )
    d = _extract_json(r.stdout)
    if "is_trading_day" in d:
        return bool(d["is_trading_day"])
    return True  # calendar broken → skip to stay safe


@unittest.skipIf(_is_trading_day(date.today().strftime("%Y-%m-%d")), "today is a trading day")
class RunnerSmokeTests(unittest.TestCase):
    def test_runner_exits_cleanly_on_closed_day(self):
        for script, marker in RUNNERS.items():
            with self.subTest(script=script):
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
