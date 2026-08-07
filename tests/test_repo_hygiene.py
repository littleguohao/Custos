"""仓库卫生守卫 —— 防「运行时数据被误提交」这一类事故。

2026-08-07 实际出过一次：一个基线脚本把测试助手的 `tmp_path` 实参写成了
**字符串** `"2026-07-16"`，于是它在**仓库根**建出

    2026-07-16/01_data/trades/master_trade_ledger.csv
    2026-07-16/04_reviews/daily/2026-07-13_final_review.json
    ...

并被 `git add -A` 一并提交（提交 `e8fd4b2`）。

根因是 gitignore 的**锚定语义**：含 `/` 的模式锚定在仓库根，
所以 `01_data/**` 拦不住 `<任意目录>/01_data/**`。

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
RUNTIME_DIRS = ("01_data", "06_logs", "03_daily_plans/_supporting")
# 敏感文件名片段：这些一旦出现在 git 里就是事故
SENSITIVE = ("master_trade_ledger", "current_positions", "position_confirmations",
             "trades_stock", "_import_meta", "0amv_observations")


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    return [x for x in out.splitlines() if x.strip()]


def test_no_runtime_data_tracked_anywhere():
    """⚠️ 运行时数据目录下**任何嵌套位置**都不得有非 .md 文件入库。

    「任何嵌套位置」是关键 —— 事故就是发生在 `2026-07-16/01_data/` 这种
    非根位置，而 gitignore 的 `01_data/**` 只锚定根。
    """
    bad = [f for f in _tracked()
           if any(f == d or f"/{d}/" in f"/{f}" for d in RUNTIME_DIRS)
           and not f.endswith(".md")]
    assert not bad, ("运行时数据被提交（只允许 .md）：\n  " + "\n  ".join(bad)
                     + "\n提示：gitignore 里含 `/` 的模式锚定仓库根，"
                       "嵌套位置需要 `**/` 前缀")


def test_no_sensitive_filenames_tracked():
    """⚠️ 台账/持仓/确认这几类文件名一旦入库就是账户数据外泄。

    与上一条互补：上一条按**目录**查，这条按**文件名**查 ——
    脚本把台账写到别的目录（如 `07_tools/master_trade_ledger.csv`）时上一条查不出来。
    """
    bad = [f for f in _tracked()
           if any(s in pathlib.Path(f).name for s in SENSITIVE)
           and not f.endswith(".md")]
    assert not bad, "敏感文件被提交：\n  " + "\n  ".join(bad)


def test_no_stray_date_named_dirs_at_root():
    """⚠️ 仓库根不得有形如 `YYYY-MM-DD` 的目录。

    项目里没有任何**按日期命名的顶层目录**这种设计（日期都是文件名前缀，
    如 `01_data/market/2026-08-07_xxx.json`）。根目录出现日期名目录 = 有脚本
    把日期当路径参数传了 —— 这正是那次事故的形态。
    """
    stray = sorted(p.name for p in ROOT.iterdir()
                   if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))
    assert not stray, (f"仓库根有日期命名的目录：{stray}\n"
                       "很可能是某个脚本把日期字符串当成了 tmp_path / 输出目录实参")


def test_gitignore_runtime_patterns_have_recursive_form():
    """gitignore 里运行时数据的模式必须有 `**/` 递归形式。

    这条查的是**规则本身**而不是当前文件状态 —— 上面两条只能发现已经犯了的错，
    这条防止有人把 `**/` 前缀删掉（那样下次误写就又拦不住了）。
    """
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pat in ("**/01_data/**", "**/06_logs/"):
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
         ⇒ 在真实 `03_daily_plans/_supporting/` 下建出日期目录
         （空、且被 gitignore，所以没污染 git —— 但那是运气，不是设计）

    判据是「**本次 pytest 会话开始之后**新建」，不是「最近一小时」——
    后者分不清测试泄漏与 owner 手动跑管线（目标机天天跑）。

    与 `test_no_runtime_data_tracked_anywhere`（查已入库）互补：
    这条查「有没有漏网」，那条查「有没有已经进 git」。
    """
    watched = [ROOT / "01_data", ROOT / "03_daily_plans", ROOT / "06_logs",
               ROOT / "04_reviews"]
    fresh = []
    for d in watched:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.stat().st_mtime > _SESSION_START and p.suffix != ".md":
                fresh.append(str(p.relative_to(ROOT)))
    assert not fresh, (
        "这些运行时文件是**本次测试会话期间**产生的 —— "
        f"某个测试的 fixture 漏 patch 了路径常量：\n  " + "\n  ".join(fresh[:20]))
