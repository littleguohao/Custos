"""财联社每日重点栏目识别、跨报告去重与确定性内容解读。

只影响消息展示，不影响交易权限。内容总结采用正文要点抽取，解读采用栏目规则；
不调用 LLM，不把原文大段搬入报告。
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

_INTERPRET_RULE = {
    "盘前避雷针": "涉及持仓或候选时先核对公告、监管原文与生效日期；风险线索只用于核查和收紧，未证实内容不当成事实",
    "连板股分析": "高度、晋级率与炸板率须同看；只有晋级改善且炸板回落才支持情绪修复，单一高标不代表整体转强",
    "涨停与舆情分析": "涨停数量、封板率与焦点股需相互印证；舆情热度不等于持续性，一致加速后仍需次日承接确认",
    "盘中主线板块分析": "板块共振、成交放大与核心股承接同时成立才确认主线，否则按轮动或跟风观察",
    "盘后热门板块与逻辑": "热门板块须由成交额、核心股承接和次日持续性确认；单日异动不直接外推为持续主线",
}

_SUMMARY_KEYWORDS = {
    "盘前避雷针": ("减持", "解禁", "监管", "ST", "业绩", "风险", "处罚", "复牌"),
    "连板股分析": ("连板", "晋级率", "炸板", "高度", "龙头", "涨停"),
    "涨停与舆情分析": ("涨停", "封板率", "焦点股", "舆情", "热点", "连板"),
    "盘中主线板块分析": ("主线", "板块", "成交", "资金", "领涨", "扩散"),
    "盘后热门板块与逻辑": ("板块", "概念", "成交", "领涨", "驱动", "涨停"),
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


def _summary_parts(row: dict) -> list[str]:
    """去掉电头与重复标题，再按栏目相关度抽取至多三句/三个编号要点。"""
    raw = _cell(row.get("summary"))
    raw = re.sub(r"^【[^】]*】", "", raw).strip()
    raw = re.sub(r"^财联社[^，。；]{0,30}[，。；]", "", raw).strip()
    if not raw:
        return [_cell(row.get("title"))]
    parts = [
        p.strip()
        for p in re.split(r"(?<=[。！？；])|(?=\d{1,2}[）)])", raw)
        if p.strip(" ；;，,")
    ]
    if not parts:
        return [_cell(row.get("title"))]
    title = re.sub(r"\W+", "", _cell(row.get("title")))
    parts = [p for p in parts if re.sub(r"\W+", "", p) != title] or parts
    label = str(row.get("cls_priority") or "")
    words = _SUMMARY_KEYWORDS.get(label, ())

    def _part_relevance(part: str) -> int:
        return sum(word in part for word in words) * 3 + bool(re.search(r"\d", part))

    # 首句保留事件背景，再补相关度最高的两个要点；恢复原文顺序便于阅读。
    chosen = {0}
    chosen.update(
        sorted(
            range(len(parts)),
            key=lambda i: (_part_relevance(parts[i]), -i),
            reverse=True,
        )[:2]
    )
    return [parts[i] for i in sorted(chosen)]


def summarize_cls_article(row: dict, max_chars: int = 320) -> str:
    """提取内容要点，而不是直接展示原文摘要；过短内容保留完整事实。"""
    text = " ".join(_summary_parts(row))
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip(" ，,；;。") + "…"


def _association(row: dict) -> str:
    names: list[str] = []
    for item in row.get("stocks") or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("stock_name") or item.get("code")
        else:
            name = item
        if name:
            names.append(str(name))
    for item in row.get("subject_tags") or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("tag") or item.get("subject_name")
        else:
            name = item
        if name:
            names.append(str(name))
    names = list(dict.fromkeys(names))
    return "、".join(names[:8])


def _metric_reading(row: dict) -> str:
    label = str(row.get("cls_priority") or "")
    if label not in {"连板股分析", "涨停与舆情分析"}:
        return ""
    text = _cell(row.get("summary"))
    out = []
    seal = re.search(r"封板率(?:为|约为|约|达)?\s*(\d+(?:\.\d+)?)%", text)
    if seal:
        value = float(seal.group(1))
        state = (
            "承接较强" if value >= 70 else ("分歧偏大" if value < 50 else "仍有分歧")
        )
        out.append(f"封板率 {value:g}%，{state}")
    promotion = re.search(r"晋级率(?:为|约为|约|达|近)?\s*(\d+(?:\.\d+)?)%", text)
    if promotion:
        value = float(promotion.group(1))
        state = (
            "接力较强" if value >= 50 else ("接力偏弱" if value < 30 else "接力一般")
        )
        out.append(f"晋级率 {value:g}%，{state}")
    return "；".join(out)


def interpret_cls_article(row: dict) -> str:
    """按原第四列的栏目口径给出确定性的读数、关联对象与验证结论。"""
    label = str(row.get("cls_priority") or "")
    parts = [
        _metric_reading(row),
        _INTERPRET_RULE.get(label, analysis_focus(label)),
    ]
    association = _association(row)
    if association:
        parts.append(f"本文关联：{association}，优先核对其后续价格与成交反馈")
    return "；".join(p for p in parts if p)


def render_cls_article(row: dict) -> str:
    """0905/1700 共用的第三列：标题链接＋内容总结＋解读，不再单列原文和第四列。"""
    title = _cell(row.get("title"))
    if row.get("source_url"):
        title = f"[{title}]({row['source_url']})"
    summary = _cell(summarize_cls_article(row))
    interpretation = _cell(interpret_cls_article(row))
    return f"{title}<br>**内容总结**：{summary}<br>**解读**：{interpretation}"
