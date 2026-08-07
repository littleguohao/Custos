"""`05_strategy_versions/trade_lessons.md` 的可执行约束。

这份文件记「从交易记录看到什么 → 所以改了什么 → 现在是否真的在执行」。
它最容易退化成一堆看起来有道理的散文，所以有三条机械检查：

  ① `已成机制` 必须给出**真实存在**的代码位置 —— 这正是 `TEAM_BLUEPRINT.md`
     被删的原因：它把 2 条零实现的规则写成「由 XXX 执行，不可被总控决策覆盖」。
  ② 状态必须在**受控词表**内 —— 尤其不许出现「已执行」这种含糊说法
     （分不清是代码在做还是人记得做）。
  ③ 每条结论要么有样本量、要么显式标 `⚠️ 未记录` ——
     研究侧已因样本量吃过教训（R11 基准崩塌、「5% 是崖不是坡」），实盘侧同理。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "05_strategy_versions" / "trade_lessons.md"
STATUSES = {"已成机制", "仅人工约束", "待验证", "已推翻"}


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_exists_and_declares_its_scope():
    s = _text()
    assert "为什么改" in s, "必须写清它与 strategy_version_log / TODO 的分工"
    assert "只增不删" in s, "必须写明只增不删的约定（同 research/）"


def test_status_vocabulary_is_controlled():
    """⚠️ 出现在表格里的状态必须在词表内。

    特别不许「已执行」—— 它分不清「代码在做」与「人记得做」，
    而这正是 `TEAM_BLUEPRINT.md` 误导人的方式。
    """
    s = _text()
    # 表格里 `xxx` 形式的状态标记
    used = set(re.findall(r"`(已成机制|仅人工约束|待验证|已推翻|已执行)`", s))
    assert "已执行" not in used, "不许用「已执行」—— 用「已成机制」或「仅人工约束」"
    assert used <= STATUSES, f"未知状态：{used - STATUSES}"


def test_mechanism_entries_cite_existing_code():
    """⚠️ 标 `已成机制` 的条目必须指向**真实存在**的代码文件。

    这条是整份文件的价值所在：它把「声称有防线」变成可验证的。
    """
    s = _text()
    # 取所有反引号里的 07_tools 路径（允许带 ::函数名）
    refs = set(re.findall(r"`((?:close_review|holdings|market_timing|screening|factors|"
                          r"collect|news|trades|local_tdx|research)/[a-z_0-9]+\.py)"
                          r"(?:::[A-Za-z_0-9]+)?`", s))
    missing = sorted(r for r in refs if not (ROOT / "07_tools" / r).exists())
    assert not missing, (f"trade_lessons.md 引用了不存在的代码：{missing}\n"
                         "移动文件时要同步这份记录 —— 否则它会变成又一份说谎的文档")


def test_every_record_has_sample_size_or_explicit_unknown():
    """每条记录要么给样本量、要么显式标 `⚠️ 未记录` —— 不许留空。

    「不知道」是合法答案；**假装知道**不是。
    """
    s = _text()
    body = s[s.index("## 记录"):]
    rows = [ln for ln in body.splitlines()
            if ln.startswith("|") and "---" not in ln and "样本量" not in ln]
    bad = []
    for ln in rows:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) < 4:            # 竖排表（| 项 | 内容 |）跳过
            continue
        if not any(("未记录" in c) or re.search(r"\d", c) or c in {"—", "不适用"} for c in cells):
            bad.append(ln[:70])
    assert not bad, "这些行既没给样本量也没标「未记录」：\n  " + "\n  ".join(bad)


def test_referenced_todo_items_exist():
    """引用的待办编号必须真实存在 —— 否则读者追不到后续。"""
    s = _text()
    todo = (ROOT / "05_strategy_versions" / "TODO.md").read_text(encoding="utf-8")
    for num in set(re.findall(r"待办 #(\d+)", s)):
        assert re.search(rf"^\|\s*{num}\s*\|", todo, re.M), f"待办 #{num} 不存在"


def test_system_principles_points_here():
    """⚠️ `用户画像` 那一节必须回指本文件。

    否则读者会把那 6 条当成「系统已保证」—— 而实测其中 3 条没有任何代码在执行。
    """
    sp = (ROOT / "00_governance" / "strategy" / "_shared"
          / "system_principles.md").read_text(encoding="utf-8")
    assert "trade_lessons.md" in sp
    assert "不要默认它们已经被系统保证" in sp
