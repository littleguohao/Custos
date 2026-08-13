"""仓库卫生守卫 —— 防「运行时数据被误提交」这一类事故。

2026-08-07 实际出过一次：一个基线脚本把测试助手的 `tmp_path` 实参写成了
**字符串** `"2026-07-16"`，于是它在**仓库根**建出

    2026-07-16/data/trades/master_trade_ledger.csv
    2026-07-16/artifacts/reports/daily/2026-07-13_final_review.json
    ...

并被 `git add -A` 一并提交（提交 `e8fd4b2`）。

根因是 gitignore 的**锚定语义**：含 `/` 的模式锚定在仓库根，
所以 `data/**` 拦不住 `<任意目录>/data/**`。

那次写进去的是合成 fixture（「测试A」、600000、10.0/10.5），**没有真数据外泄**；
但同类事故若写的是真实台账，就是账户数据进公开仓库。所以这里加两道**可执行**检查。
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 运行时数据目录：只允许 .md（模板/文档）入库
# 2026-08-12：_supporting 废除、daily 改按日期目录归档 ⇒ 整个 daily/ 都按运行时算
# （唯二的入库例外是 DAILY_PLAN_TEMPLATE.md 等 .md，规则本来就是「只放行 .md」）。
RUNTIME_DIRS = ("data", "artifacts/logs", "artifacts/reports/daily")
# 敏感文件名片段：这些一旦出现在 git 里就是事故
SENSITIVE = (
    "master_trade_ledger",
    "current_positions",
    "position_confirmations",
    "trades_stock",
    "_import_meta",
    "0amv_observations",
)


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [x for x in out.splitlines() if x.strip()]


def test_no_runtime_data_tracked_anywhere():
    """⚠️ 运行时数据目录下**任何嵌套位置**都不得有非 .md 文件入库。

    「任何嵌套位置」是关键 —— 事故就是发生在 `2026-07-16/data/` 这种
    非根位置，而 gitignore 的 `data/**` 只锚定根。
    """
    bad = [
        f
        for f in _tracked()
        if any(f == d or f"/{d}/" in f"/{f}" for d in RUNTIME_DIRS)
        and not f.endswith(".md")
    ]
    assert not bad, (
        "运行时数据被提交（只允许 .md）：\n  "
        + "\n  ".join(bad)
        + "\n提示：gitignore 里含 `/` 的模式锚定仓库根，"
        "嵌套位置需要 `**/` 前缀"
    )


def test_no_sensitive_filenames_tracked():
    """⚠️ 台账/持仓/确认这几类文件名一旦入库就是账户数据外泄。

    与上一条互补：上一条按**目录**查，这条按**文件名**查 ——
    脚本把台账写到别的目录（如 `src/master_trade_ledger.csv`）时上一条查不出来。
    """
    bad = [
        f
        for f in _tracked()
        if any(s in pathlib.Path(f).name for s in SENSITIVE) and not f.endswith(".md")
    ]
    assert not bad, "敏感文件被提交：\n  " + "\n  ".join(bad)


def test_no_stray_date_named_dirs_at_root():
    """⚠️ 仓库根不得有形如 `YYYY-MM-DD` 的目录。

    项目里没有任何**按日期命名的顶层目录**这种设计（日期都是文件名前缀，
    如 `data/market/2026-08-07_xxx.json`）。根目录出现日期名目录 = 有脚本
    把日期当路径参数传了 —— 这正是那次事故的形态。
    """
    stray = sorted(
        p.name
        for p in ROOT.iterdir()
        if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)
    )
    assert not stray, (
        f"仓库根有日期命名的目录：{stray}\n"
        "很可能是某个脚本把日期字符串当成了 tmp_path / 输出目录实参"
    )


def test_gitignore_runtime_patterns_have_recursive_form():
    """gitignore 里运行时数据的模式必须有 `**/` 递归形式。

    这条查的是**规则本身**而不是当前文件状态 —— 上面两条只能发现已经犯了的错，
    这条防止有人把 `**/` 前缀删掉（那样下次误写就又拦不住了）。
    """
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pat in ("**/data/**", "**/artifacts/logs/"):
        assert pat in text, f".gitignore 缺递归模式 {pat}（含 / 的模式只锚定仓库根）"


# ⚠️ 在**模块导入时**（pytest 收集阶段，早于任何测试执行）记下时刻。
# 只有晚于它的文件才可能是**测试**写的 —— 这样才能与「owner 手动跑了管线」区分开。
# 第一版用「一小时内」判定，结果我自己手动跑 run_1445 验证修复后它就误报了；
# 在目标机上会**每天误报**（那里天天跑管线）。
_SESSION_START = time.time()


def test_test_suite_does_not_write_into_repo():
    """⚠️ 测试不得往**仓库内**写运行时目录。

    2026-08-07 两次踩到同一形态：
      ① 基线脚本把 `tmp_path` 实参写成字符串 `"2026-07-16"`
         ⇒ 仓库根建出该目录并被提交
      ② `test_pipeline_orchestration` 的 fixture 漏 patch `PLAN_DIR`/`SUPPORT_DIR`
         ⇒ 在真实 `artifacts/reports/daily/_supporting/` 下建出日期目录
         （空、且被 gitignore，所以没污染 git —— 但那是运气，不是设计）

    判据是「**本次 pytest 会话开始之后**新建」，不是「最近一小时」——
    后者分不清测试泄漏与 owner 手动跑管线（目标机天天跑）。

    与 `test_no_runtime_data_tracked_anywhere`（查已入库）互补：
    这条查「有没有漏网」，那条查「有没有已经进 git」。
    """
    watched = [
        ROOT / "data",
        ROOT / "artifacts/reports/daily",
        ROOT / "artifacts/logs",
        ROOT / "artifacts/reports",
    ]
    fresh = []
    for d in watched:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.stat().st_mtime > _SESSION_START and p.suffix != ".md":
                fresh.append(str(p.relative_to(ROOT)))
    assert not fresh, (
        "这些运行时文件是**本次测试会话期间**产生的 —— "
        f"某个测试的 fixture 漏 patch 了路径常量：\n  " + "\n  ".join(fresh[:20])
    )


def test_no_scratch_files_in_code_tree():
    """⚠️ 代码目录与仓库根不得有临时/草稿文件入库。

    2026-08-10 拉取时发现两类，都是目标机 cron 的自动提交（`git add -A`）扫进来的：

        src/custos/core/trades/_no_trades_2026080{5,6,7}.json   2 字节 `{}`
        _summ_m2.py                                      根目录一次性分析手稿

    前者的根因是 **CLI 设计逼出来的**：`incremental_ledger --confirm-no-trades`
    本就要求输入为空，而 `--input` 却是 `required=True` ⇒ 每次无交易确认都得
    `echo {} > x.json`，文件留在 CWD。已把 `--input` 改为该模式下可选。
    后者是 `m2_stop_sweep --report-only` 的冗余劣化副本，已删。

    ⚠️ 已有的两条守卫都拦不住：`test_no_runtime_data_tracked_anywhere` 只看
    **运行时目录**（`src/` 不是），`test_no_sensitive_filenames_tracked` 只看
    **敏感名**（`_no_trades_` / `_summ_` 都不在表里）。这是第三个角度：**形态**。
    """
    import re

    # 形态：下划线开头 + 含日期、或 _tmp/_scratch/_summ/_debug 前缀、或 .bak/.orig 后缀
    SCRATCH = re.compile(
        r"(^_\w*\d{6,8}\w*\.|^_(tmp|scratch|summ|debug|test|old)\w*\.|"
        r"\.(bak|orig|tmp|swp|rej)$|^~)",
        re.I,
    )
    bad = []
    for f in _tracked():
        parts = f.split("/")
        # 只查代码树与仓库根；运行时目录另有守卫，`tests/` 的夹具允许下划线命名
        if parts[0] not in ("src",) and len(parts) > 1:
            continue
        if parts[0] == "tests":
            continue
        if SCRATCH.search(pathlib.Path(f).name):
            bad.append(f)
    assert not bad, (
        "代码树/仓库根有临时文件入库：\n  "
        + "\n  ".join(bad)
        + "\n目标机 cron 用 `git add -A` 自动提交，任何留在树里的草稿都会被扫走。"
        "\n若是 CLI 逼你造的文件（如 --confirm-no-trades 需要空 --input），改 CLI 而不是靠自律。"
    )


def test_scratch_guard_catches_the_2026_08_10_shapes():
    """⚠️ 守卫自证：必须能抓到当天真实入库的那两种形态。

    上一版（只有目录守卫 + 敏感名守卫）对这两个文件全部放行。
    """
    import re

    SCRATCH = re.compile(
        r"(^_\w*\d{6,8}\w*\.|^_(tmp|scratch|summ|debug|test|old)\w*\.|"
        r"\.(bak|orig|tmp|swp|rej)$|^~)",
        re.I,
    )
    for name in (
        "_no_trades_20260805.json",
        "_summ_m2.py",
        "daily_report.py.bak",
        "_tmp_check.py",
    ):
        assert SCRATCH.search(name), f"守卫漏掉：{name}"
    # 正常文件不得误报 —— `_shares.py` / `_util.py` / `_template.py` 是 factors/ 的真实私有模块
    for name in (
        "_shares.py",
        "_util.py",
        "_template.py",
        "paths.py",
        "b1_thresholds.py",
        "__init__.py",
    ):
        assert not SCRATCH.search(name), f"守卫误报：{name}"
