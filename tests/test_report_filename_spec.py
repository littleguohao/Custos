# -*- coding: utf-8 -*-
"""报告文件名规范（v0.141，owner 拍板）钉测。

三份日报告与 `{date}_1445_review.md` 同构、文件名带时点标记：

- 盘前：`{date}_0905_daily_report.md`（09:05 链）
- 盘后：`{date}_1700_final_review.md` / `.json`（17:00 链）
- 选股：`{date}_1800_candidate_table.md`（18:00 链）

历史文件（改名前产物）不动 ⇒ **读方必须兼容新旧两种名字**，否则改名次日
prev-day 查找（盘前读前一交易日 final_review 预案）会断。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-08-28"


# ── 新名产出 ─────────────────────────────────────────────────────────


def test_candidate_table_writes_timed_name(tmp_path, monkeypatch):
    """candidate_table.main 的默认落盘必须是 `{date}_1800_candidate_table.md`。"""
    from custos.pipeline.screening import candidate_table as ct

    monkeypatch.setattr(ct, "STOCK_POOL_DIR", tmp_path / "data/stock_pool")
    monkeypatch.setattr(ct, "PLANS", tmp_path / "artifacts/reports/daily")
    ct.main(["--date", DAY])  # stock_pool 缺失走降级分支，仍会落盘说明文件
    out = tmp_path / "artifacts/reports/daily" / DAY / f"{DAY}_1800_candidate_table.md"
    assert out.exists()
    assert not (
        tmp_path / "artifacts/reports/daily" / DAY / f"{DAY}_candidate_table.md"
    ).exists()


# ── 旧名回退兼容（prev-day 查找） ────────────────────────────────────


def _review_payload(day: str) -> dict:
    return {"date": day, "next_day_plan": {"holding_plans": [{"code": "600000"}]}}


def test_weekly_review_loads_timed_name(tmp_path):
    """新名 `daily/{day}/{day}_1700_final_review.json` 是主路径。"""
    from custos.pipeline.close_review import weekly_review as wr

    d = tmp_path / "artifacts/reports/daily" / DAY
    d.mkdir(parents=True)
    (d / f"{DAY}_1700_final_review.json").write_text(
        json.dumps(_review_payload(DAY)), encoding="utf-8"
    )
    assert wr._load_daily_review_json(tmp_path, DAY) == _review_payload(DAY)


@pytest.mark.parametrize(
    "rel",
    [
        f"{DAY}/{DAY}_final_review.json",  # 日期目录内旧名（2026-08-12~08-29）
        f"{DAY}_final_review.json",  # 旧平铺（2026-08-12 前）
    ],
)
def test_weekly_review_falls_back_to_old_names(tmp_path, rel):
    """改名前/目录重构前的历史产物仍必须能读到——否则周报找计划断链。"""
    from custos.pipeline.close_review import weekly_review as wr

    p = tmp_path / "artifacts/reports/daily" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_review_payload(DAY)), encoding="utf-8")
    assert wr._load_daily_review_json(tmp_path, DAY) == _review_payload(DAY)


def test_daily_report_previous_review_accepts_old_and_new(tmp_path, monkeypatch):
    """盘前读前一交易日 final_review 走 glob `*_final_review.json`，新旧名都命中。"""
    from custos.pipeline import daily_report as dr

    monkeypatch.setattr(dr, "REVIEWS", tmp_path / "artifacts/reports")
    old = tmp_path / "artifacts/reports/daily/2026-08-26"
    old.mkdir(parents=True)
    (old / "2026-08-26_final_review.json").write_text(
        json.dumps(_review_payload("2026-08-26")), encoding="utf-8"
    )
    new = tmp_path / "artifacts/reports/daily/2026-08-27"
    new.mkdir(parents=True)
    (new / "2026-08-27_1700_final_review.json").write_text(
        json.dumps(_review_payload("2026-08-27")), encoding="utf-8"
    )
    # 新旧并存时取最近一日（新名）
    assert dr.previous_review("2026-08-28") == _review_payload("2026-08-27")
    # 只剩旧名（改名次日的真实形态：上一交易日产物是旧名）也必须读到
    assert dr.previous_review("2026-08-27") == _review_payload("2026-08-26")


# ── 全仓无旧名残留（grep 守卫） ──────────────────────────────────────

# 旧后缀（不带时点标记）出现在同一行且没有对应新名 ⇒ 违规
_OLD_SUFFIX = re.compile(
    r"_daily_report\.md|_final_review\.(md|json)|_candidate_table\.md"
)
_TIMED = re.compile(
    r"_0905_daily_report\.|_1700_final_review\.|_1800_candidate_table\."
)
# 读方回退兼容**必须**引用旧名，这几处是有意保留：
_FALLBACK_WHITELIST = {
    "src/custos/pipeline/daily_report.py",  # previous_review 的 glob 兼容注释与模式
    "src/custos/pipeline/close_review/weekly_review.py",  # _load_daily_review_json 回退链
    # 落盘前校验器（读方）：校验历史日期不该直接报缺，三路径回退与 weekly_review 同口径
    "src/custos/pipeline/close_review/final_review_validator.py",
}
_GUARDED = [
    ROOT / "src",
    ROOT / "governance" / "contracts",
]


def test_no_untimed_report_filenames_left():
    """src 与治理契约里不得再出现不带时点标记的三份日报告文件名。

    例外只有读方的旧名回退（白名单）。历史记录类文档（CHANGELOG、
    事故复盘 docstring）不在本守卫范围——它们描述的是过去。
    """
    bad = []
    for base in _GUARDED:
        for f in sorted(base.rglob("*")):
            if f.suffix not in (".py", ".md") or not f.is_file():
                continue
            rel = f.relative_to(ROOT).as_posix()
            if rel in _FALLBACK_WHITELIST:
                continue
            for i, line in enumerate(
                f.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if _OLD_SUFFIX.search(line) and not _TIMED.search(line):
                    bad.append(f"{rel}:{i}: {line.strip()[:80]}")
    assert not bad, (
        "发现不带时点标记的日报告文件名（应为 _0905_daily_report / "
        "_1700_final_review / _1800_candidate_table；读方旧名回退才允许例外）：\n  "
        + "\n  ".join(bad)
    )
