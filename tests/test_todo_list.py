"""`TODO.md` 的结构与链接校验。

为什么值得一条测试：这份清单指向 20+ 个文件，而**目录重构正是链接断裂的时刻**
（research/ 刚拆过一次，strategy/ 还要拆）。断了的链接让待办无法判断是否还成立，
清单就退化成一堆看不懂的字。

另一条更重要：**「已失效的行动项」区块必须存在**。旧文档里的「待跑」被后续发现推翻后，
如果静默删除，别人可能正照着做 —— 实例：R10 待跑 #3「去幸存者偏差用 --data-source qlib」
在 2026-08-06 之后照做只会引入放大 13~21% 的收益，去偏一点没做到。
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TODO = ROOT / "TODO.md"


def test_todo_exists():
    assert TODO.exists(), "待办清单不存在"


def test_all_relative_links_resolve():
    """失效链接 = 无法判断该项是否还成立。"""
    bad = []
    for m in re.finditer(r"\]\((\.\.?/[^)#]+)", TODO.read_text(encoding="utf-8")):
        if not (TODO.parent / m.group(1)).resolve().exists():
            bad.append(m.group(1))
    assert not bad, f"待办清单里这些链接已失效（目录重构后要同步）：{bad}"


@pytest.mark.parametrize(
    "section",
    [
        "## 待办",
        "## 已闭环索引",
        "## ⚠️ 已失效的行动项",
    ],
)
def test_has_section(section):
    assert section in TODO.read_text(encoding="utf-8"), f"缺区块：{section}"


def test_stale_actions_explain_why():
    """已失效项必须写清**为什么失效**，否则读者无法判断能不能改回去。"""
    s = TODO.read_text(encoding="utf-8")
    seg = s[s.index("## ⚠️ 已失效的行动项") :]
    seg = seg[: seg.index("\n## ")] if "\n## " in seg else seg
    rows = [ln for ln in seg.splitlines() if ln.startswith("| ") and "---" not in ln]
    assert len(rows) >= 4, "已失效表至少要有表头 + 3 项（当前已知 3 项）"
    for ln in rows[1:]:
        assert len(ln.split("|")) >= 4, (
            f"已失效项缺列（原项/出处/为什么失效）：{ln[:60]}"
        )


def test_separated_from_version_log():
    """**TODO 记没做的事，版本日志记改过的规则**，不许混。

    owner 明确过：`CHANGELOG.md` 只记策略规则变化，
    不放研究结论/基建重构/待办。
    """
    log = ROOT / "CHANGELOG.md"
    # ⚠️ 不 skip：`CHANGELOG.md` 是**入库文件**，缺失说明仓库出了问题，
    #    而 skip 会让这条测试静默通过。2026-08-07 清理 tests/ 时改为硬失败。
    assert log.exists(), f"版本日志缺失（入库文件）：{log}"
    s = log.read_text(encoding="utf-8")
    for kw in ("## 已闭环索引", "已失效的行动项"):
        assert kw not in s, f"版本日志里出现了待办内容：{kw}"


def test_priority_is_by_blocking_not_effort():
    """优先级定义要写在文件里——否则下次有人按工作量排。"""
    s = TODO.read_text(encoding="utf-8")
    assert "它阻塞了什么" in s
    assert "不按工作量" in s


def test_no_strikethrough_entries():
    """维护约定：**完成的项直接删**，不留 `~~删除线~~`。

    2026-08-06 我自己先违反了一次（把已解决的 #27 标成删除线而不是删掉）。
    留删除线的代价：清单越读越长，而「已完成」的信息本该归到对应单元或版本日志里，
    留在待办里等于两处记同一件事、且会不同步。
    """
    s = TODO.read_text(encoding="utf-8")
    # 只查表格行——「维护约定」正文里引用 `~~删除线~~` 这个写法本身是合法的
    bad = [
        ln.strip()[:70]
        for ln in s.splitlines()
        if "~~" in ln and ln.lstrip().startswith("|")
    ]
    assert not bad, f"待办里有删除线条目，应直接删除：{bad}"


def test_item_numbers_are_unique():
    """编号重复会让「待办 #26」这类跨文档引用指向两处。"""
    import collections

    s = TODO.read_text(encoding="utf-8")
    nums = re.findall(r"^\| (\d+) \|", s, re.M)
    dup = [n for n, c in collections.Counter(nums).items() if c > 1]
    assert not dup, f"重复编号：{dup}"


# ── 2026-10-12 改版（待办 + 已闭环索引 + 已失效）的结构钉 ─────────────────────

PRIORITIES = {"P0", "P1", "P2", "P3"}
BLOCKERS = {"owner", "生产机", "coder", "观察中", "挂起"}
ROW_CAP = 400


def _section_rows(title: str) -> list[list[str]]:
    s = TODO.read_text(encoding="utf-8")
    seg = s[s.index(title) :]
    seg = seg[len(title) :]
    seg = seg[: seg.index("\n## ")] if "\n## " in seg else seg
    return [
        [c.strip() for c in ln.strip().strip("|").split("|")]
        for ln in seg.splitlines()
        if re.match(r"^\| \d+ \|", ln)
    ]


def test_active_rows_shape():
    """待办每行：6 列、优先级与「卡在」取固定值、单行 ≤400 字、不在行内打 ✅ 记进度。

    改版前单行最长 2486 字——已完成子项打 ✅ 留在行里，待办退化成进度流水账。
    """
    s = TODO.read_text(encoding="utf-8")
    rows = _section_rows("## 待办")
    assert rows, "待办表为空却没删区块？"
    for c in rows:
        assert len(c) == 6, f"#{c[0]} 列数应为 6（#/事项/优先级/卡在/下一步/出处）"
        assert c[2] in PRIORITIES, f"#{c[0]} 优先级非法：{c[2]}"
        assert c[3] in BLOCKERS, (
            f"#{c[0]}「卡在」非法：{c[3]}（只许 {sorted(BLOCKERS)}）"
        )
        assert c[5], f"#{c[0]} 缺出处"
        assert "✅" not in "|".join(c), f"#{c[0]} 行内有 ✅：完成的子项应拆条进索引"
    for ln in s[s.index("## 待办") : s.index("## 已闭环索引")].splitlines():
        if re.match(r"^\| \d+ \|", ln):
            assert len(ln) <= ROW_CAP, (
                f"待办行超 {ROW_CAP} 字：{ln[:40]}…（细节放研究单元）"
            )


def test_closed_index_has_destination():
    """已闭环索引每行必须给去处（版本号或提交），否则追不到内容。"""
    for c in _section_rows("## 已闭环索引"):
        assert len(c) == 4, f"索引 #{c[0]} 列数应为 4"
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", c[2]), f"索引 #{c[0]} 日期格式"
        assert re.search(r"v0\.\d+|commit [0-9a-f]{7}", c[3]), f"索引 #{c[0]} 缺去处"


def test_numbers_contiguous():
    """编号 1..max 连续：删待办必须同时进索引（否则编号「消失」，引用悬空）。"""
    nums = {
        int(n)
        for n in re.findall(r"^\| (\d+) \|", TODO.read_text(encoding="utf-8"), re.M)
    }
    missing = sorted(set(range(1, max(nums) + 1)) - nums)
    assert not missing, f"这些编号既不在待办也不在已闭环索引：{missing}"


def test_referenced_todo_numbers_resolve():
    """仓库里任何「TODO #n / 待办 #n」都必须能在本文件追到（改版前悬空 37 个编号 / 165 处）。"""
    have = set(re.findall(r"^\| (\d+) \|", TODO.read_text(encoding="utf-8"), re.M))
    files = [
        ROOT / n for n in ("README.md", "AGENTS.md", "CHANGELOG.md", "TRADE_LESSONS.md")
    ]
    for d in ("governance", "src", "tests", "scripts"):
        files += [p for p in (ROOT / d).rglob("*") if p.suffix in (".md", ".py")]
    bad = {}
    for p in files:
        if not p.is_file():
            continue
        for n in re.findall(
            r"(?:TODO ?#|待办 ?#)(\d+)", p.read_text(encoding="utf-8", errors="ignore")
        ):
            if n not in have:
                bad.setdefault(n, str(p.relative_to(ROOT)))
    assert not bad, f"引用了不存在的待办编号：{bad}"
