"""`rss_filter` —— 把归一化 RSS 证据筛成**有界、相关、可审计**的候选集。

覆盖率清点（2026-08-07）：31%、64 语句未覆盖。它在 08:50 采集链上
（`rss_collect` → `rss_filter`），失败会让 `run_0850` 记 degraded 并触发 09:05 重采。

它的输出会进 premarket_intelligence 供 LLM 做候选风控研判，所以两件事最要紧：
**① 相关性打分不能误配**（误配会把无关新闻顶到候选首位）
**② 输出必须写明 RSS 不能直接放宽交易权限**。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("07_tools", "07_tools/news"):
    sys.path.insert(0, str(ROOT / _p))

from news import rss_filter as rf  # noqa: E402

CFG = {
    "session_windows_hours": {"premarket": 18, "postclose": 6, "intraday_1445": 6,
                              "weekly": 168, "monthly": 720, "ad_hoc": 24},
    "limits": {"premarket": 10, "postclose": 10, "intraday_1445": 10,
               "weekly": 10, "monthly": 10, "ad_hoc": 10},
    "per_source_limits": {"premarket": 2},
    "tier_weight": {"S": 40, "A": 30, "B": 20, "C": 10},
    "category_weight": {"policy_official": 20, "a_share_official": 10, "media": 0},
    "theme_keywords": {"半导体": ["芯片", "晶圆"], "宏观政策": ["国常会", "降准"]},
    "market_keywords": ["成交额", "北向"],
    "negative_spam_keywords": ["直播带货"],
    "policy_negative_keywords": ["人事任免", "会见"],
}
REG = {"sources": [
    {"id": "gov", "policy_stage": "effective"},
    {"id": "consult", "policy_stage": "consultation_not_effective"},
    {"id": "media1"}, {"id": "media2"},
]}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    log = tmp_path / "artifacts/logs" / "rss"
    (data / "trades").mkdir(parents=True)
    monkeypatch.setattr(rf, "DATA", data)
    monkeypatch.setattr(rf, "LOG", log)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps(CFG), encoding="utf-8")
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps(REG), encoding="utf-8")
    monkeypatch.setattr(rf, "CFG", cfg)
    monkeypatch.setattr(rf, "REG", reg)
    # 交易日历不确定时 premarket_window 会走 fallback，这里固定成确定值
    monkeypatch.setattr(rf, "previous_confirmed_trading_day", lambda d: "2026-08-06")
    return tmp_path


def _items(env, *items):
    p = env / "data" / "news" / "rss" / "normalized" / "2026-08-07_rss_evidence.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(list(items), ensure_ascii=False), encoding="utf-8")


def _positions(env, rows):
    (env / "data" / "trades" / "current_positions.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8")


_ITEM_SEQ = iter(range(1, 10000))


def _item(**kw):
    """一条归一化 RSS 条目。

    ⚠️ **必须带 `item_id`** —— `rss_collector` 一定会写它
    （`sha256(source_id|guid|link|norm_title)[:24]`，去重与可追溯的键），
    而 `rss_filter` 的产物契约要求它。2026-08-07 铺契约时这批 fixture
    因为没有它而全挂 —— 是 fixture 不真实，不是契约太严。
    """
    base = {"item_id": f"id{next(_ITEM_SEQ):04d}",
            "title": "t", "summary": "", "published_at": "2026-08-07T08:00:00+08:00",
            "source_id": "media1", "source_tier": "B", "category": "media",
            "source_url": "https://x.com/a"}
    base.update(kw)
    return base


def _run(env, monkeypatch, session="premarket", as_of="2026-08-07T08:45:00+08:00"):
    monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07",
                                      "--session-type", session, "--as-of", as_of])
    rf.main()
    out = env / "data" / "news" / "rss" / "filtered" / f"2026-08-07_{session}_rss_candidates.json"
    rep = env / "artifacts/logs" / "rss" / f"2026-08-07_{session}_filter_log.json"
    return (json.loads(out.read_text(encoding="utf-8")),
            json.loads(rep.read_text(encoding="utf-8")))


class TestHelpers:
    def test_norm_text_strips_punctuation_and_case(self):
        assert rf.norm_text("A-B, 芯片!") == "ab芯片"

    def test_canonical_url_drops_tracking_params(self):
        """去掉 utm_* / ref 等追踪参数 —— 否则同一条新闻带不同来源参数会去重失败。"""
        a = rf.canonical_url("https://X.com/a/?utm_source=w&id=1")
        b = rf.canonical_url("https://x.com/a?id=1")
        assert a == b

    def test_canonical_url_survives_garbage(self):
        assert rf.canonical_url(None) == ""

    def test_parse_dt_handles_z_suffix(self):
        assert rf.parse_dt("2026-08-07T00:00:00Z") is not None
        assert rf.parse_dt("不是时间") is None


class TestDedupe:
    def test_same_canonical_url_dropped(self):
        items = [_item(source_url="https://x.com/a?utm_source=w", title="甲"),
                 _item(source_url="https://x.com/a", title="乙")]
        assert len(rf.dedupe(items)) == 1

    def test_near_identical_titles_dropped(self):
        """标题近似（一方是另一方子串且长度比 ≥0.82）也算重复 —— 转载常改几个字。"""
        items = [_item(title="国常会部署稳增长若干措施要点", source_url="https://a.com/1"),
                 _item(title="国常会部署稳增长若干措施要点补充", source_url="https://b.com/2")]
        assert len(rf.dedupe(items)) == 1

    def test_short_titles_not_deduped_by_substring(self):
        """短标题（<12 字符）不做子串去重 —— 否则「降准」会吃掉「降准落地」这类不同新闻。"""
        items = [_item(title="降准", source_url="https://a.com/1"),
                 _item(title="降准落地", source_url="https://b.com/2")]
        assert len(rf.dedupe(items)) == 2

    def test_distinct_titles_kept(self):
        items = [_item(title="芯片行业需求回暖明显加速", source_url="https://a.com/1"),
                 _item(title="房地产政策进一步放松预期", source_url="https://b.com/2")]
        assert len(rf.dedupe(items)) == 2


class TestCodeMatching:
    """⚠️ 回归：代码命中必须要求**数字边界**。

    持仓命中 +45 分（单项最大）且是排序的**首要键**，误配会把无关新闻顶到候选第一条。
    """

    @pytest.mark.parametrize("text,hit", [
        ("浦发银行600000发布公告", True),
        ("（600000）浦发银行", True),
        ("成交额达6000001万元", False),   # 嵌在更长数字里
        ("上证指数报3600000点", False),
    ])
    def test_digit_boundary(self, env, monkeypatch, text, hit):
        _positions(env, [{"代码": "600000", "名称": "浦发银行"}])
        _items(env, _item(title=text))
        sel, _ = _run(env, monkeypatch)
        got = bool(sel and sel[0]["matched_holdings_or_pool"]["codes"])
        assert got is hit, f"{text!r} 应{'命中' if hit else '不命中'}"

    def test_name_match_also_counts(self, env, monkeypatch):
        _positions(env, [{"代码": "600000", "名称": "浦发银行"}])
        _items(env, _item(title="浦发银行获批新业务"))
        sel, _ = _run(env, monkeypatch)
        assert sel[0]["matched_holdings_or_pool"]["names"] == ["浦发银行"]


class TestWindowing:
    def test_premarket_window_starts_at_previous_close(self, env, monkeypatch):
        """盘前窗口从**上一交易日 15:00** 开始，而不是简单往前推 N 小时 ——
        否则周末/长假后会漏掉休市期间的全部消息。"""
        _positions(env, [])
        _items(env, _item(title="芯片需求回暖", published_at="2026-08-06T16:00:00+08:00"))
        sel, rep = _run(env, monkeypatch)
        assert rep["previous_close_date"] == "2026-08-06"
        assert len(sel) == 1, "上一交易日收盘后的消息必须在窗口内"

    def test_fallback_when_calendar_unavailable(self, env, monkeypatch):
        """交易日历取不到时退回「往前 N 小时」，并把 previous_close_date 记为 null
        —— 让报告能看出用的是退化窗口。"""
        monkeypatch.setattr(rf, "previous_confirmed_trading_day", lambda d: None)
        _positions(env, [])
        _items(env, _item(title="芯片需求回暖"))
        _sel, rep = _run(env, monkeypatch)
        assert rep["previous_close_date"] is None
        assert rep["window_hours_actual"] == pytest.approx(18, abs=0.02)

    def test_outside_window_counted_not_silently_dropped(self, env, monkeypatch):
        _positions(env, [])
        _items(env, _item(title="芯片需求回暖", published_at="2026-08-01T08:00:00+08:00"))
        sel, rep = _run(env, monkeypatch)
        assert sel == [] and rep["excluded"]["outside_window"] == 1

    def test_future_skew_tolerated_10min(self, env, monkeypatch):
        """允许 10 分钟未来时钟偏移 —— 源站时间戳常有小幅超前。"""
        _positions(env, [])
        _items(env, _item(title="芯片需求回暖", published_at="2026-08-07T08:50:00+08:00"),
               )
        sel, _ = _run(env, monkeypatch)
        assert len(sel) == 1

    def test_missing_published_at_counted(self, env, monkeypatch):
        _positions(env, [])
        _items(env, _item(title="芯片需求回暖", published_at=None))
        sel, rep = _run(env, monkeypatch)
        assert sel == [] and rep["excluded"]["published_at_missing"] == 1


class TestRelevanceAndExclusion:
    def test_c_tier_without_any_signal_excluded(self, env, monkeypatch):
        """C 级源且**无任何相关性信号**才剔除 —— 有主题/市场词/持仓命中就留。"""
        _positions(env, [])
        _items(env, _item(title="某地举办文旅推介会", source_tier="C"),
               _item(title="芯片需求回暖", source_tier="C", source_url="https://x.com/b"))
        sel, rep = _run(env, monkeypatch)
        assert rep["excluded"]["c_tier_irrelevant"] == 1
        assert [x["title"] for x in sel] == ["芯片需求回暖"]

    def test_themes_and_market_keywords_recorded(self, env, monkeypatch):
        _positions(env, [])
        _items(env, _item(title="国常会讨论芯片与成交额"))
        sel, _ = _run(env, monkeypatch)
        assert set(sel[0]["matched_themes"]) == {"半导体", "宏观政策"}
        assert sel[0]["matched_market_keywords"] == ["成交额"]

    def test_policy_negative_recorded_but_not_excluded(self, env, monkeypatch):
        """⚠️ 政策负向词**只落痕、不剔除** —— 是否剔除由消费方裁决。

        政策源（gov_cn / 中新社国内）也会发人事任免、会见这类非政策内容。
        """
        _positions(env, [])
        _items(env, _item(title="国常会人事任免会见", source_id="gov",
                          source_tier="S", category="policy_official"))
        sel, _ = _run(env, monkeypatch)
        assert sel[0]["matched_policy_negative"] == ["人事任免", "会见"]
        assert len(sel) == 1, "只落痕，不得剔除"

    def test_spam_penalized_not_excluded(self, env, monkeypatch):
        _positions(env, [])
        _items(env, _item(title="芯片直播带货"))
        sel, _ = _run(env, monkeypatch)
        assert len(sel) == 1 and sel[0]["relevance_score"] < 20 + 12


class TestConsultationStage:
    def test_not_effective_marked_candidate_not_confirmed(self, env, monkeypatch):
        """⚠️ 征求意见稿（未生效）必须标 `confirmed=False` + `quality=candidate`
        并附核验条件 —— 否则「征求意见」会被当成既成事实读。"""
        _positions(env, [])
        _items(env, _item(title="证监会就程序化交易征求意见", source_id="consult",
                          source_tier="S", category="policy_official"))
        sel, _ = _run(env, monkeypatch)
        assert sel[0]["confirmed"] is False
        assert sel[0]["quality"] == "candidate"
        assert "核验正式文件、实施日期和配套细则" in sel[0]["validation_condition"]

    def test_effective_stage_not_downgraded(self, env, monkeypatch):
        _positions(env, [])
        _items(env, _item(title="国常会部署", source_id="gov", source_tier="S",
                          category="policy_official"))
        sel, _ = _run(env, monkeypatch)
        assert sel[0].get("confirmed") is not False


class TestOrderingAndLimits:
    def test_holdings_hit_ranks_first(self, env, monkeypatch):
        """排序首要键是**持仓命中**，压过源级别与分数 ——
        持仓相关的消息必须最先被看到。"""
        _positions(env, [{"代码": "600000", "名称": "浦发银行"}])
        _items(env,
               _item(title="国常会降准芯片晶圆成交额北向", source_id="gov",
                     source_tier="S", category="policy_official", source_url="https://a/1"),
               _item(title="浦发银行小事一则", source_id="media1", source_tier="C",
                     category="media", source_url="https://a/2"))
        sel, _ = _run(env, monkeypatch)
        assert sel[0]["title"] == "浦发银行小事一则"

    def test_per_source_limit_prevents_flooding(self, env, monkeypatch):
        """单源上限先于全局上限生效 —— 防一个源刷满候选池。"""
        _positions(env, [])
        _items(env, *[_item(title=f"芯片需求回暖第{i}批解读", source_url=f"https://a/{i}")
                      for i in range(5)])
        sel, rep = _run(env, monkeypatch)
        assert rep["per_source_limit"] == 2
        assert sum(1 for x in sel if x["source_id"] == "media1") == 2

    def test_global_limit_respected(self, env, monkeypatch):
        _positions(env, [])
        items = []
        for i in range(30):
            items.append(_item(title=f"芯片行情第{i}号独立解读文章",
                               source_id=f"media{i % 2 + 1}", source_url=f"https://a/{i}"))
        sel, rep = _run(env, monkeypatch)
        assert len(sel) <= rep["limit"]


class TestReportAudit:
    def test_report_has_full_funnel_counts(self, env, monkeypatch):
        """报告必须给出完整漏斗：入口数 → 窗口内且相关 → 去重后 → 选中数。
        少一环就无法判断候选少是因为没消息还是被筛掉了。"""
        _positions(env, [])
        _items(env, _item(title="芯片需求回暖", source_url="https://a/1"),
               _item(title="芯片需求回暖", source_url="https://a/1"),
               _item(title="无关内容", source_tier="C", source_url="https://a/3"))
        _sel, rep = _run(env, monkeypatch)
        assert rep["input_count"] == 3
        assert rep["within_window_and_relevant"] == 2
        assert rep["after_dedupe"] == 1
        assert rep["selected_count"] == 1

    def test_permission_rule_present(self, env, monkeypatch):
        """⚠️ RSS 候选**不能直接放宽交易权限** —— 必须写在产物里。"""
        _positions(env, [])
        _items(env)
        _sel, rep = _run(env, monkeypatch)
        assert rep["permission_rule"] == \
            "RSS candidates cannot directly increase trading permissions"

    def test_empty_input_still_writes_artifacts(self, env, monkeypatch):
        """零输入也要落盘 —— 下游按文件存在与否判断这一步跑没跑。"""
        _positions(env, [])
        _items(env)
        sel, rep = _run(env, monkeypatch)
        assert sel == [] and rep["input_count"] == 0
