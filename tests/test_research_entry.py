"""研究/回测的**统一入口** —— `07_tools/research/__main__.py`。

owner 2026-08-07 问「总的回测和研究是否可以统一到一个入口」。
实测 14 个脚本、9002 行，其中两个引擎各约 2000 行。结论是
**一个入口 + 分发**，而不是合并成一个脚本 —— 三个理由写在模块 docstring 里，
最硬的一条是：`m2_stop_sweep` / `adjust_diagnostic` 是**故意用 subprocess**
调 `backtest_factors` 做**内存隔离**（那个回测常被 OOM Kill），
合进一个进程会毁掉它。

这里测的是入口本身的两条契约：
① 注册表与磁盘**不得漂移**（登记了却没文件 / 有文件却没登记）
② `stale` 状态必须**代码级可见**（项目原则：不可用的东西要标记出来）
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "07_tools" / "research"
sys.path.insert(0, str(ROOT / "07_tools"))

from research import __main__ as entry  # noqa: E402

# 入口与 backtest_factors 2026-08-08 起按项目惯例把 stdout/stderr reconfigure 成
# **UTF-8**（GBK 控制台打不了 ⚠️/⇒ 会 UnicodeEncodeError 直接崩）。subprocess 侧
# 必须显式 `encoding="utf-8"` —— `text=True` 默认按 locale（本机 GBK）解码，
# 会在 reader 线程里 UnicodeDecodeError。
_RUN = dict(capture_output=True, text=True, encoding="utf-8")


class TestRegistryMatchesDisk:
    """⚠️ 注册表与磁盘不得漂移 —— 这是「索引会过期」那类问题的可执行防线。"""

    def test_every_registered_tool_exists(self):
        missing = [n for n in entry.TOOLS if not (RESEARCH / f"{n}.py").exists()]
        assert not missing, f"注册表登记了不存在的工具：{missing}"

    def test_every_script_is_registered(self):
        on_disk = {p.stem for p in RESEARCH.glob("*.py")
                   if p.name not in {"__init__.py", "__main__.py"}}
        unregistered = sorted(on_disk - set(entry.TOOLS))
        assert not unregistered, (
            f"这些研究脚本没登记进 `TOOLS`：{unregistered}\n"
            "新增研究脚本必须登记 —— 否则它对使用者不可见（没有索引就得先知道该跑哪个）")

    def test_all_statuses_are_known(self):
        bad = {n: k for n, (k, _) in entry.TOOLS.items() if k not in entry.ORDER}
        assert not bad, f"未知 status：{bad}（允许 {entry.ORDER}）"

    def test_every_tool_has_description(self):
        empty = [n for n, (_, d) in entry.TOOLS.items() if not d.strip()]
        assert not empty, f"这些工具没有说明：{empty}"


class TestStaleIsVisible:
    """⚠️ 项目原则：**不可用的东西要标记出来，且标记要代码级生效**。

    3 个覆盖率 0% 的脚本标 `stale`。它们**留在表里而不是删掉** ——
    「不确定」本身要可见：删了就没人记得曾有这些工具，
    而标 stale 会在每次列表时提醒，并在运行时打警告。
    """

    STALE = {"compare_signal_sets", "scan_signal_backtest", "m2_migrate_fingerprint"}

    def test_the_three_zero_coverage_tools_are_stale(self):
        got = {n for n, (k, _) in entry.TOOLS.items() if k == "stale"}
        assert got == self.STALE, (
            f"stale 集合变了：{got}\n"
            "若某个已判定存废（删除或转正），请同步 TODO.md #44")

    def test_running_stale_tool_warns(self):
        """跑 stale 工具必须先打警告 —— 结论不该被直接采信。

        带 --help 走快速路径：compare_signal_sets 裸跑会做全量分析
        （逐票 TDX 取数，分钟级），而断言目标只是入口的 stale 警告。
        """
        r = subprocess.run([sys.executable, str(RESEARCH / "__main__.py"),
                            "compare_signal_sets", "--help"],
                           cwd=str(ROOT), **_RUN, timeout=120)
        assert r.returncode == 0
        assert "存废待定" in r.stderr

    def test_listing_marks_stale_section(self):
        r = subprocess.run([sys.executable, str(RESEARCH / "__main__.py")],
                           cwd=str(ROOT), **_RUN, timeout=60)
        assert "存废待定" in r.stdout


class TestListingAndDispatch:
    def test_listing_shows_all_tools(self):
        r = subprocess.run([sys.executable, str(RESEARCH / "__main__.py")],
                           cwd=str(ROOT), **_RUN, timeout=60)
        assert r.returncode == 0
        for n in entry.TOOLS:
            assert n in r.stdout, f"列表里缺 {n}"

    def test_listing_surfaces_mode_flags(self):
        """⚠️ 模式开关是**互斥的运行模式**却被塞进 `store_true` flag
        （`launch_point_study` 有 17 个）。看 `--help` 分不清模式与选项，
        所以入口要把模式单独列出来。"""
        r = subprocess.run([sys.executable, str(RESEARCH / "__main__.py")],
                           cwd=str(ROOT), **_RUN, timeout=60)
        assert "模式（17）" in r.stdout or "模式（1" in r.stdout
        assert "discriminate" in r.stdout, "launch_point_study 的模式要列出来"

    def test_unknown_tool_exits_nonzero_and_lists(self):
        r = subprocess.run([sys.executable, str(RESEARCH / "__main__.py"), "nope"],
                           cwd=str(ROOT), **_RUN, timeout=60)
        assert r.returncode == 2 and "未登记的工具" in r.stderr
        assert "backtest_factors" in r.stdout, "报错时也要给出可用清单"

    def test_dispatch_passes_args_through(self):
        """分发必须原样透传参数 —— 否则每个工具都要在入口再声明一遍。"""
        r = subprocess.run([sys.executable, str(RESEARCH / "__main__.py"),
                            "backtest_factors", "--help"],
                           cwd=str(ROOT), **_RUN, timeout=180)
        assert r.returncode == 0 and "backtest_factors.py" in r.stdout

    def test_dispatch_uses_subprocess_not_import(self):
        """⚠️ 必须 subprocess 而非 import：

        `m2_stop_sweep` / `adjust_diagnostic` 依赖 `backtest_factors` 的
        **内存隔离**（那个回测常被 OOM Kill，见 `MEM_PER_JOB_MB` 注释）。
        改成 import 会让它们在同一进程里累积内存；
        也会让一个工具的 import 错误波及**所有**工具。
        """
        src = (RESEARCH / "__main__.py").read_text(encoding="utf-8")
        assert "subprocess.run" in src
        assert "importlib" not in src and "__import__" not in src
