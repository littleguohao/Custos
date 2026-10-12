"""`TRADE_LESSONS.md` 的可执行约束。

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
DOC = ROOT / "TRADE_LESSONS.md"
STATUSES = {"已成机制", "仅人工约束", "待验证", "已推翻"}


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_exists_and_declares_its_scope():
    s = _text()
    assert "为什么改" in s, "必须写清它与 CHANGELOG / TODO 的分工"
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
    # 取所有反引号里的 src 路径（允许带 ::函数名）
    refs = set(
        re.findall(
            r"`((?:close_review|holdings|market_timing|screening|factors|"
            r"collect|news|trades|local_tdx|research)/[a-z_0-9]+\.py)"
            r"(?:::[A-Za-z_0-9]+)?`",
            s,
        )
    )
    srcrels = [
        p.relative_to(ROOT / "src").as_posix() for p in (ROOT / "src").rglob("*.py")
    ]
    missing = sorted(
        r for r in refs if not any(rel == r or rel.endswith("/" + r) for rel in srcrels)
    )
    assert not missing, (
        f"TRADE_LESSONS.md 引用了不存在的代码：{missing}\n"
        "移动文件时要同步这份记录 —— 否则它会变成又一份说谎的文档"
    )


def _overview_rows() -> list[list[str]]:
    s = _text()
    seg = s[s.index("## 约束总览") : s.index("## 怎么写")]
    return [
        [c.strip() for c in ln.strip().strip("|").split("|")]
        for ln in seg.splitlines()
        if re.match(r"^\| L\d+ \|", ln)
    ]


def test_every_constraint_has_sample_size_or_explicit_unknown():
    """总览每条约束的「样本量」列要么给数、要么显式标 `⚠️ 未记录` / 不适用 / — ——不许留空。

    「不知道」是合法答案；**假装知道**不是。（2026-10-12 改版后只查样本量列本身，
    旧版扫整行，编号里的数字就能让空样本量蒙混过关。）
    """
    rows = _overview_rows()
    assert rows, "约束总览表为空"
    bad = [
        r[0]
        for r in rows
        if not (
            "未记录" in r[3]
            or re.search(r"\d", r[3])
            or r[3] == "—"
            or "不适用" in r[3]
        )
    ]
    assert not bad, f"这些约束的样本量列为空：{bad}"


def test_mechanism_rows_cite_code_in_same_row():
    """标 `已成机制` 的约束，同一行的机制列必须给出 src 路径（只在别处出现不算）。"""
    path = re.compile(
        r"`(?:close_review|holdings|market_timing|screening|factors|collect|news|"
        r"trades|local_tdx|research)/[a-z_0-9]+\.py"
    )
    bad = [
        r[0] for r in _overview_rows() if "已成机制" in r[4] and not path.search(r[5])
    ]
    assert not bad, f"这些「已成机制」约束没写代码位置：{bad}"


def test_constraints_and_records_cross_reference():
    """L 行的「依据」必须指向存在的 E 记录；记录里提到的 L 必须在总览里；E 编号 1..n 连续。"""
    s = _text()
    body = s[s.index("## 记录") :]
    e_nums = [int(n) for n in re.findall(r"^### E(\d+) · ", body, re.M)]
    assert e_nums == list(range(1, len(e_nums) + 1)), f"E 编号须从 1 连续递增：{e_nums}"
    l_ids = {r[0] for r in _overview_rows()}
    for r in _overview_rows():
        for e in re.findall(r"E(\d+)", r[6]):
            assert int(e) in e_nums, f"{r[0]} 依据 E{e} 不存在"
    for l in set(re.findall(r"\bL(\d+)\b", body)):
        assert f"L{l}" in l_ids, f"记录引用了不存在的约束 L{l}"


def test_monthly_reflection_template_present():
    """每月一次反思（owner 2026-10-12）：模板与节奏说明必须在文件里，且逐条对照约束。"""
    s = _text()
    assert "月度反思（每月一次）" in s
    for kw in ("monthly_review.json", "按 rule 计数", "逐条对照约束", "约束变化"):
        assert kw in s, f"月度反思模板缺要素：{kw}"


def test_referenced_todo_items_exist():
    """引用的待办编号必须真实存在 —— 否则读者追不到后续。"""
    s = _text()
    todo = (ROOT / "TODO.md").read_text(encoding="utf-8")
    for num in set(re.findall(r"待办 #(\d+)", s)):
        assert re.search(rf"^\|\s*{num}\s*\|", todo, re.M), f"待办 #{num} 不存在"


def test_system_principles_points_here():
    """⚠️ `用户画像` 那一节必须回指本文件。

    否则读者会把那 6 条当成「系统已保证」—— 而实测其中 3 条没有任何代码在执行。
    """
    sp = (
        ROOT / "governance" / "strategy" / "_shared" / "system_principles.md"
    ).read_text(encoding="utf-8")
    assert "TRADE_LESSONS.md" in sp
    assert "不要默认它们已经被系统保证" in sp
