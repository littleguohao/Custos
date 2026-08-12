# -*- coding: utf-8 -*-
"""Filter normalized RSS evidence into a bounded, relevant, auditable candidate set."""

from __future__ import annotations
import argparse, json, re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo


from custos.core.paths import (
    DATA,
    LOGS,
    RSS_FILTER_CONFIG_FILE,
    RSS_SOURCE_REGISTRY_FILE,
)  # noqa: E402
from custos.core.paths import read_json as load  # noqa: E402
from custos.core.paths import write_json as dump  # noqa: E402
from custos.core.code_utils import bare_code as bare  # noqa: E402
from custos.core.runtime_guards import previous_confirmed_trading_day  # noqa: E402
from custos.core.contracts import require  # noqa: E402

LOG = LOGS / "rss"
CFG = RSS_FILTER_CONFIG_FILE
REG = RSS_SOURCE_REGISTRY_FILE
SH = ZoneInfo("Asia/Shanghai")


def norm_text(s):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(s or "").lower())


def canonical_url(u):
    try:
        z = urlsplit(u)
        q = [
            (k, v)
            for k, v in parse_qsl(z.query, keep_blank_values=True)
            if k.lower()
            not in {
                "utm_source",
                "utm_medium",
                "utm_campaign",
                "utm_term",
                "utm_content",
                "source",
                "ref",
            }
        ]
        return urlunsplit(
            (z.scheme.lower(), z.netloc.lower(), z.path.rstrip("/"), urlencode(q), "")
        ).lower()
    except Exception:
        return str(u or "")


def parse_dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except Exception:
        return None


def premarket_window(day, asof, fallback_hours):
    previous = previous_confirmed_trading_day(day)
    if not previous:
        return asof - timedelta(hours=fallback_hours), None
    start = (
        datetime.fromisoformat(previous + "T15:00:00")
        .replace(tzinfo=SH)
        .astimezone(timezone.utc)
    )
    return start, previous


def entities(date):
    """持仓的名称与代码集合，用于相关性加分。

    ⚠️ `date` **未被使用**：`current_positions.json` 是**当前快照**、没有历史版本，
    所以给任何日期都返回同一份。日常当日运行没问题；但**回填历史日期时
    会用今天的持仓去筛那天的新闻**，结论不可复现。保留参数是为了让调用点
    显式表达「这是按日期筛」的意图，等持仓有了历史版本可直接接上。
    """
    positions = load(DATA / "trades" / "current_positions.json", [])
    names = set()
    codes = set()
    for x in positions:
        code = x.get("代码") or x.get("code")
        name = x.get("名称") or x.get("name")
        if code:
            codes.add(bare(code))
        if name:
            names.add(str(name))
    return names, codes


def dedupe(items):
    # First exact/canonical URL, then near-identical normalized titles.
    out = []
    url_seen = set()
    title_seen = []
    for x in items:
        cu = canonical_url(x.get("source_url"))
        nt = norm_text(x.get("title"))
        if cu and cu in url_seen:
            continue
        duplicate = False
        if nt:
            for old in title_seen:
                shorter = min(len(nt), len(old))
                longer = max(len(nt), len(old))
                if (
                    shorter >= 12
                    and (nt in old or old in nt)
                    and shorter / longer >= 0.82
                ):
                    duplicate = True
                    break
        if duplicate:
            continue
        if cu:
            url_seen.add(cu)
        if nt:
            title_seen.append(nt)
        out.append(x)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument(
        "--session-type",
        required=True,
        choices=[
            "premarket",
            "intraday_1445",
            "postclose",
            "weekly",
            "monthly",
            "ad_hoc",
        ],
    )
    ap.add_argument("--as-of")
    a = ap.parse_args()
    cfg = load(CFG, {})
    reg = {x["id"]: x for x in load(REG, {}).get("sources", [])}
    raw = load(DATA / "news" / "rss" / "normalized" / f"{a.date}_rss_evidence.json", [])
    asof = (
        datetime.fromisoformat(a.as_of).astimezone(timezone.utc)
        if a.as_of
        else datetime.now(timezone.utc)
    )
    hours = cfg["session_windows_hours"][a.session_type]
    cutoff = asof - timedelta(hours=hours)
    previous_close_date = None
    if a.session_type == "premarket":
        cutoff, previous_close_date = premarket_window(a.date, asof, hours)
    limit = cfg["limits"][a.session_type]
    per_source_limit = cfg.get("per_source_limits", {}).get(a.session_type, limit)
    names, codes = entities(a.date)
    scored = []
    excluded = {}
    # ⚠️ 代码命中必须要求**数字边界**，不能裸子串匹配。持仓命中值 +45 分（单项最大）
    # 且是排序的**首要键**，误配会把无关新闻顶到候选第一条。实测误配：
    #   "成交额达0024156万元"  → 裸匹配命中 002415（嵌在更长数字里）
    #   "上证指数报3600000点"  → 裸匹配命中 600000
    # 数字边界修掉这两类，且不伤真命中（"浦发银行600000发布公告" / "（600000）" 仍命中）。
    # 2026-08-12（#48，owner 拍板方案 A）：再加**紧邻量词否定** —— 命中数字后
    # 0~2 个空白内紧跟金额/计数量词（元/万元/亿元/港元/美元/辆/台/家/吨/列/人次…）
    # 时不计命中，修掉「净利润600000元」「第600000列」这类「代码恰好等于一个独立
    # 金额/计数」的误配；「600000浦发银行」「（600000）分红每10股派2元」因紧邻的
    # 是名称/括号而非量词，不受影响。量词表走 contracts 配置（同其他词表）。
    unit_suffixes = [str(u) for u in cfg.get("code_unit_suffixes", []) if u]
    unit_neg = ""
    if unit_suffixes:
        unit_neg = (
            r"(?!\s{0,2}(?:" + "|".join(re.escape(u) for u in unit_suffixes) + "))"
        )
    code_pats = {
        c: re.compile(r"(?<!\d)" + re.escape(c) + r"(?!\d)" + unit_neg)
        for c in codes
        if c
    }
    for x in raw:
        pub = parse_dt(x.get("published_at"))
        text = (str(x.get("title") or "") + " " + str(x.get("summary") or "")).lower()
        tier = x.get("source_tier", "C")
        cat = x.get("category", "")
        if pub is None:
            excluded["published_at_missing"] = (
                excluded.get("published_at_missing", 0) + 1
            )
            continue
        if pub and (pub > asof + timedelta(minutes=10) or pub < cutoff):
            excluded["outside_window"] = excluded.get("outside_window", 0) + 1
            continue
        hits_names = sorted(n for n in names if n and n.lower() in text)
        hits_codes = sorted(c for c, pat in code_pats.items() if pat.search(text))
        themes = []
        for theme, words in cfg.get("theme_keywords", {}).items():
            if any(w.lower() in text for w in words):
                themes.append(theme)
        market_hits = [w for w in cfg.get("market_keywords", []) if w.lower() in text]
        spam = [w for w in cfg.get("negative_spam_keywords", []) if w.lower() in text]
        # 政策负向词:政策源(gov_cn/中新社国内)会发人事任免、会见、文旅推介这类非政策内容。
        # 在这里匹配而不是在 postclose_news_digest.classify 里读配置——text(title+summary,
        # 已 lower)只在这里备好,且 classify 保持纯函数。仅落痕,是否剔除由消费方裁决。
        policy_neg = [
            w for w in cfg.get("policy_negative_keywords", []) if w.lower() in text
        ]
        score = (
            cfg["tier_weight"].get(tier, 0)
            + cfg["category_weight"].get(cat, 0)
            + (45 if hits_names or hits_codes else 0)
            + min(36, len(themes) * 12)
            + min(18, len(market_hits) * 6)
            - (18 if spam else 0)
        )
        if tier == "C" and not (hits_names or hits_codes or themes or market_hits):
            excluded["c_tier_irrelevant"] = excluded.get("c_tier_irrelevant", 0) + 1
            continue
        y = dict(x)
        y.update(
            {
                "relevance_score": score,
                "matched_holdings_or_pool": {"names": hits_names, "codes": hits_codes},
                "matched_themes": themes,
                "matched_market_keywords": market_hits,
                "matched_policy_negative": policy_neg,
                "filter_session": a.session_type,
                "filter_cutoff": cutoff.isoformat(),
            }
        )
        src = reg.get(x.get("source_id"), {})
        y["policy_stage"] = src.get("policy_stage")
        if y["policy_stage"] == "consultation_not_effective":
            y["confirmed"] = False
            y["quality"] = "candidate"
            y["validation_condition"] = list(
                dict.fromkeys(
                    (y.get("validation_condition") or [])
                    + ["核验正式文件、实施日期和配套细则"]
                )
            )
        scored.append(y)
    scored.sort(
        key=lambda x: (
            bool(
                x["matched_holdings_or_pool"]["names"]
                or x["matched_holdings_or_pool"]["codes"]
            ),
            x.get("source_tier") in {"S", "A"},
            x["relevance_score"],
            x.get("published_at") or "",
        ),
        reverse=True,
    )
    unique = dedupe(scored)
    selected = []
    source_selected = {}
    for x in unique:
        source = x.get("source_id", "unknown")
        if source_selected.get(source, 0) >= per_source_limit:
            continue
        selected.append(x)
        source_selected[source] = source_selected.get(source, 0) + 1
        if len(selected) >= limit:
            break
    require("rss_candidates", selected)
    out = (
        DATA
        / "news"
        / "rss"
        / "filtered"
        / f"{a.date}_{a.session_type}_rss_candidates.json"
    )
    dump(out, selected)
    report = {
        "date": a.date,
        "session_type": a.session_type,
        "as_of": asof.isoformat(),
        "window_start": cutoff.isoformat(),
        "previous_close_date": previous_close_date,
        "window_hours_actual": round((asof - cutoff).total_seconds() / 3600, 2),
        "input_count": len(raw),
        "within_window_and_relevant": len(scored),
        "after_dedupe": len(unique),
        "selected_count": len(selected),
        "limit": limit,
        "per_source_limit": per_source_limit,
        "excluded": excluded,
        "tier_counts": {},
        "theme_counts": {},
        "source_counts": {},
        "output": str(out),
        "permission_rule": "RSS candidates cannot directly increase trading permissions",
    }
    for x in selected:
        report["tier_counts"][x["source_tier"]] = (
            report["tier_counts"].get(x["source_tier"], 0) + 1
        )
        report["source_counts"][x["source_id"]] = (
            report["source_counts"].get(x["source_id"], 0) + 1
        )
        for t in x["matched_themes"]:
            report["theme_counts"][t] = report["theme_counts"].get(t, 0) + 1
    rp = LOG / f"{a.date}_{a.session_type}_filter_log.json"
    dump(rp, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
