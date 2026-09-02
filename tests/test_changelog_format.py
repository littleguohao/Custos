# -*- coding: utf-8 -*-
"""CHANGELOG.md 格式钉测（v0.144 写入规范的机器执行）。

背景：v0.144 定了「每条 ≤2 行 + 细节只指引去向」的排版规范，但没有任何
钉测守着——2026-08-31 规范小节被插到表格中间（表被劈成两半、v0.150 起全部渲染成断表，
长条目（最长达 1102 字符的单行单元格）也没人拦。这两类问题都是纯机器可判的。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "CHANGELOG.md"
RULES_HEADING = "## 写入规范"

# 单行字符上限：v0.144 规范是「每条 ≤2 行」，物理行长度是代理判据。
# 现存最长相合条目 ~220；历史 blob 曾达 1102（断表事故同款）。阈值取 400。
MAX_ROW_CHARS = 400


def _lines():
    return LOG.read_text(encoding="utf-8").splitlines()


def test_header_and_separator():
    lines = _lines()
    header_idx = next(i for i, ln in enumerate(lines) if ln.startswith("| 日期 |"))
    sep = lines[header_idx + 1]
    assert sep.startswith("|---"), "表头下一行必须是分隔行"
    # 五列：日期/版本/修改内容/修改原因/后续验证指标
    assert lines[header_idx].count("|") == 6, f"表头列数不对：{lines[header_idx]}"


def test_table_continuous_until_rules_section():
    """规范小节必须在文末——表头到规范小节之间不得有标题/空行断表。"""
    lines = _lines()
    header_idx = next(i for i, ln in enumerate(lines) if ln.startswith("| 日期 |"))
    rules_idx = next(i for i, ln in enumerate(lines) if ln.startswith(RULES_HEADING))
    assert rules_idx > header_idx, "写入规范必须在表格之后（文末）"
    body = lines[header_idx + 2 : rules_idx]
    # 表格与规范之间允许正好一个空行收尾（最后一个条目之后的段落分隔）
    if body and body[-1] == "":
        body = body[:-1]
    assert body and body[-1] != "", "表格与规范之间不得有连续空行"
    for i, ln in enumerate(body, start=header_idx + 2):
        assert ln.startswith("|"), f"表格在第 {i} 行被截断：{ln[:40]!r}"
    # 规范之后不得再出现表格行（防「规范后又接了一段表」的重复事故）
    for i, ln in enumerate(lines[rules_idx:], start=rules_idx):
        assert not ln.startswith("|"), f"规范小节之后又出现表格行（第 {i} 行）"


def test_rows_have_five_columns_and_date_version():
    import re

    for ln in _lines():
        if not ln.startswith("| 20"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        assert len(cells) == 5, f"条目列数不对（{len(cells)}）：{ln[:50]}"
        assert re.match(r"\d{4}-\d{2}-\d{2}$", cells[0]), f"日期格式：{cells[0]}"
        assert re.match(r"v0\.\d+$", cells[1]), f"版本格式：{cells[1]}"


def test_versions_monotonic():
    import re

    versions = []
    for ln in _lines():
        m = re.match(r"\| \d{4}-\d{2}-\d{2} \| v0\.(\d+) \|", ln)
        if m:
            versions.append(int(m.group(1)))
    assert versions == sorted(versions), (
        f"版本号非单调：{[v for a, b in zip(versions, versions[1:]) for v in (a, b) if a > b]}"
    )


def test_row_length_cap():
    """blob 守卫：超过 400 字符的单行就是「一坨」复发。"""
    for i, ln in enumerate(_lines(), start=1):
        if ln.startswith("| 20"):
            assert len(ln) <= MAX_ROW_CHARS, (
                f"第 {i} 行 {len(ln)} 字符（>{MAX_ROW_CHARS}）——细节移到 commit/R 文档，"
                "表里只留一句话+去向"
            )
