"""财联社每日重点栏目识别、跨报告去重与文章链接展示。

只影响消息展示，不影响交易权限。报告只放可点击标题，正文仍留在数据文件中供审计。
"""

from __future__ import annotations

import re

#: 顺序即判定优先级：连板股分析须先于泛“涨停分析”，避雷针独立于其他栏目。
RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("盘前避雷针", ("股市避雷针", "投资避雷针")),
    ("连板股分析", ("连板股分析", "连板涨停分析")),
    ("涨停与舆情分析", ("涨停分析", "投资舆情热点", "舆论分析", "舆情分析")),
    (
        "盘中主线板块分析",
        ("市场主线分析", "盘中主线板块分析", "主线板块分析"),
    ),
    (
        "盘后热门板块与逻辑",
        (
            "昨日行情回顾",
            "收评：",
            "科创板收评：",
            "盘后热门板块",
            "人气板块及个股点评",
        ),
    ),
)

ANALYSIS_FOCUS: dict[str, str] = {
    "盘前避雷针": "逐项提取减持、解禁、监管、ST、业绩与海外风险，映射当前持仓和候选池；区分已确认事实与待核验传闻。",
    "连板股分析": "拆解连板高度、晋级率、炸板率、梯队完整度与龙头位置，判断情绪处于加强、分歧还是退潮。",
    "涨停与舆情分析": "对照涨停数量、封板率、焦点股和舆情热点，核验上涨驱动是否一致，识别一致加速与分歧风险。",
    "盘中主线板块分析": "梳理主线形成、切换和资金确认，区分核心主线、扩散分支与短线跟风，并列出后续验证点。",
    "盘后热门板块与逻辑": "提炼热门板块、领涨个股及上涨驱动，与成交、板块持续性和持仓/三池映射对照，区分持续主线与一日脉冲。",
}


def classify_cls_priority(title: str, content: str = "") -> str | None:
    """按标题与正文识别栏目；兼容实际日电报标题，不写死单日标题。"""
    text = f"{title or ''} {content or ''}"
    for label, keywords in RULES:
        if any(keyword in text for keyword in keywords):
            return label
    return None


def analysis_focus(label: str) -> str:
    return ANALYSIS_FOCUS.get(label, "提炼核心事实、消息驱动与后续验证点。")


def presented_cls_priority(news: dict, limit: int = 6) -> list[dict]:
    """1700 实际展示的财联社栏目集合；0905 去重只排除这些，不误排除未展示库存。"""
    rows = list(news.get("priority_events") or [])
    if not rows:  # 兼容 v0.586 之前的 digest 形状
        sections = news.get("sections") or {}
        rows = [
            row
            for name in ("信息", "政策", "风向", "舆情")
            for row in sections.get(name) or []
            if row.get("cls_priority")
        ]
    return rows[:limit]


def cls_article_key(row: dict) -> str:
    """跨 0905/1700 的稳定文章键：详情页 ID 优先，URL 其次，最后才用时间＋标题。"""
    url = str(row.get("source_url") or "").strip()
    match = re.search(r"/detail/(\d+)", url, re.I)
    if match:
        return f"cls_detail:{match.group(1)}"
    if url:
        return "url:" + url.lower().rstrip("/")
    title = re.sub(r"\s+", "", str(row.get("title") or ""))
    published = str(row.get("published_at") or "").strip()
    return f"fallback:{published}|{title}" if title else ""


def exclude_presented_cls(articles: list[dict], presented: list[dict]) -> list[dict]:
    """从本次栏目文章中删除前次报告已实际展示的条目，保持原顺序。"""
    seen = {k for row in presented if (k := cls_article_key(row))}
    return [
        row for row in articles if not (key := cls_article_key(row)) or key not in seen
    ]


def _cell(value) -> str:
    return str(value or "").strip().replace("|", "／").replace("\n", " ")


def render_cls_article(row: dict) -> str:
    """0905/1700 共用的第三列：只展示可点击的文章标题链接（owner v0.600）。"""
    title = _cell(row.get("title"))
    if row.get("source_url"):
        return f"[{title}]({row['source_url']})"
    return title
