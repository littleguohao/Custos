"""`theme_tracker_report` —— `daily_pipeline` 里**最大的零覆盖硬失败 stage**。

覆盖率清点（2026-08-07）：225 语句、**0% 覆盖**、`required=True`（挂了整条 17:00 链失败）。

这个模块决定「**板块支不支持加仓**」：
`classify_stage` 判板块阶段（退潮/下跌 ⇒ 不支持）、`score_sector` 打 0–100 分、
`action_bias` 落到「回避/禁止加仓 … 可关注核心股」。
而 R4 的结论是**板块相位择时是 0AMV 之后第二个 OOS 站得住的增强**（熊市减亏 ~4–6pp）
⇒ 这套判定错了，那条 edge 就无从兑现。

测的重点是**判据边界**（阈值恰好命中/差一点），不是冒烟。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("src", "src/pipeline/market_timing"):
    sys.path.insert(0, str(ROOT / _p))

import theme_tracker_report as ttr  # noqa: E402


def _an(trend=None, pos20=None, j=None, j_prev=None, macd_dir=None, weekly_hist=None,
        available=True, **kw):
    """造一份 `technical_monitor.analyze` 形状的分析结果。"""
    return {
        "available": available,
        "trend": {"state": trend, "close": 10.0},
        "box_20d": {"position": pos20, "upper": 11.0, "lower": 9.0},
        "box_60d": {},
        "daily": {"kdj": {"j": j, "j_prev": j_prev, "state": ""},
                  "macd": {"hist": 0.1, "hist_direction": macd_dir}},
        "weekly": {"macd": {"hist": weekly_hist}},
        **kw,
    }


class TestClassifyStage:
    """板块阶段判定 —— 决定「板块支不支持加仓」。"""

    def test_unavailable_is_data_shortage_not_a_verdict(self):
        """⚠️ 数据不足**不等于**任何一种阶段判断，必须单独一档。

        若把它归到「震荡」，一个取不到 K 线的板块会被当成正常震荡放行。
        """
        stage, why = ttr.classify_stage({"available": False, "error": "无K线"})
        assert stage == "数据不足" and why == "无K线"

    def test_main_rally_needs_all_three(self):
        """主升/加速要求**三条同时**：上涨 + 箱体上沿/突破 + MACD 扩张。"""
        assert ttr.classify_stage(
            _an(trend="上涨", pos20="上沿/突破区", macd_dir="扩张"))[0] == "主升/加速"

    @pytest.mark.parametrize("pos,macd", [("箱体上半区", "扩张"), ("上沿/突破区", "收缩")])
    def test_uptrend_without_all_three_is_only_repair(self, pos, macd):
        """缺任一条就只能是「修复/上行」—— 不许升级成主升。"""
        assert ttr.classify_stage(_an(trend="上涨", pos20=pos, macd_dir=macd))[0] == "修复/上行"

    def test_downtrend_says_no_add(self):
        stage, why = ttr.classify_stage(_an(trend="下跌"))
        assert stage == "退潮/下跌" and "不支持加仓" in why

    def test_range_at_lower_bound_is_divergence(self):
        assert ttr.classify_stage(
            _an(trend="横盘震荡", pos20="下沿/破位区"))[0] == "分歧/弱震荡"

    def test_high_j_only_when_trend_undecided(self):
        """J>90 的「高位分歧」只在趋势未定时生效 —— 趋势判定优先于指标。"""
        assert ttr.classify_stage(_an(trend=None, j=95.0))[0] == "高位分歧观察"
        assert ttr.classify_stage(_an(trend="上涨", j=95.0))[0] == "修复/上行"

    def test_weak_weekly_falls_back_to_range(self):
        assert ttr.classify_stage(_an(trend=None, weekly_hist=-0.2))[0] == "震荡"

    def test_default_is_range(self):
        assert ttr.classify_stage(_an())[0] == "震荡"


class TestScoreSector:
    def test_unavailable_scores_zero(self):
        assert ttr.score_sector({"available": False}, "high") == 0.0

    def test_base_is_fifty(self):
        assert ttr.score_sector(_an(), "") == 50.0

    def test_uptrend_and_breakout_and_expansion_stack(self):
        """加分项可叠加：50 + 18(上涨) + 12(上沿) + 8(扩张) = 88。"""
        assert ttr.score_sector(_an(trend="上涨", pos20="上沿/突破区", macd_dir="扩张"), "") == 88.0

    def test_downtrend_and_breakdown_subtract(self):
        assert ttr.score_sector(_an(trend="下跌", pos20="下沿/破位区"), "") == 18.0

    def test_clamped_to_0_100(self):
        """必须夹在 [0,100] —— 越界分数会让排序与「强板块」阈值失去意义。"""
        s = ttr.score_sector(_an(trend="上涨", pos20="上沿/突破区", macd_dir="扩张",
                                 j=85.0, weekly_hist=0.5), "high")
        assert 0 <= s <= 100

    def test_low_j_turning_up_gets_bonus(self):
        """J<30 且在抬头 → +5（低位转强）；单纯 J<12 → −3（可能是接飞刀）。"""
        up = ttr.score_sector(_an(j=20.0, j_prev=15.0), "")
        deep = ttr.score_sector(_an(j=8.0, j_prev=5.0), "")
        assert up == 55.0 and deep == 47.0

    def test_high_priority_bonus(self):
        assert ttr.score_sector(_an(), "high") - ttr.score_sector(_an(), "low") == 3.0


class TestActionBias:
    """动作倾向 —— 这是**风控闸**，措辞与阈值都要准。"""

    def test_ebb_always_forbids_add(self):
        """退潮**无论分数多高**都禁止加仓 —— 阶段判定优先于分数。"""
        assert ttr.action_bias("退潮/下跌", 95.0, "进攻") == "回避/禁止加仓"

    def test_low_score_forbids_add(self):
        assert ttr.action_bias("震荡", 34.9) == "回避/禁止加仓"

    def test_core_stock_needs_stage_score_and_market(self):
        """「可关注核心股」要求**三条同时**：主升 + ≥70 分 + 大盘进攻/偏强。"""
        assert ttr.action_bias("主升/加速", 70.0, "进攻") == "可关注核心股"
        assert ttr.action_bias("主升/加速", 69.9, "进攻") == "观察核心低吸，不追高"
        assert ttr.action_bias("主升/加速", 70.0, "震荡偏弱") == "观察核心低吸，不追高"

    def test_tiers(self):
        assert ttr.action_bias("震荡", 65.0) == "观察核心低吸，不追高"
        assert ttr.action_bias("震荡", 50.0) == "观察"
        assert ttr.action_bias("震荡", 49.9) == "谨慎观察"


class TestHoldingThemeMatching:
    ROWS = [
        {"theme_id": "chip", "theme_name": "半导体", "available": True, "score": 80,
         "holding_related": ["600000"], "representative_stocks": ["688981.SH"],
         "semantic_tags": ["芯片", "存储"], "trend_state": "上涨", "box20_position": "上沿/突破区"},
        {"theme_id": "robot", "theme_name": "机器人", "available": True, "score": 60,
         "holding_related": [], "representative_stocks": [],
         "semantic_tags": ["人形机器人"], "trend_state": "横盘震荡", "box20_position": "箱体下半区"},
    ]

    def test_explicit_code_wins(self):
        """显式关联优先于语义标签 —— 人工指定的映射不该被模糊匹配盖掉。"""
        r = ttr.match_holding_theme({"code": "600000", "primary_themes": ["人形机器人"]}, self.ROWS)
        assert r["theme_id"] == "chip"

    def test_representative_stock_suffix_ignored(self):
        r = ttr.match_holding_theme({"code": "688981"}, self.ROWS)
        assert r["theme_id"] == "chip"

    def test_semantic_fallback(self):
        r = ttr.match_holding_theme({"code": "000001", "primary_themes": ["人形机器人"]}, self.ROWS)
        assert r["theme_id"] == "robot"

    def test_nan_industry_ignored(self):
        """`industry` 是 pandas 读出来的，可能是字符串 'nan' —— 不能当成主题词。"""
        r = ttr.match_holding_theme({"code": "999999", "industry": "nan"}, self.ROWS)
        assert r == {}

    def test_no_match_returns_empty(self):
        assert ttr.match_holding_theme({"code": "999999"}, self.ROWS) == {}


class TestCompareHoldingToTheme:
    def test_theme_unavailable_is_undecided(self):
        """板块数据不足时必须「未定」，不能猜强弱。"""
        s, why = ttr.compare_holding_to_theme({"trend_state": "上涨"}, {"available": False})
        assert s == "未定" and "数据不足" in why

    def test_stronger_trend(self):
        s, _ = ttr.compare_holding_to_theme(
            {"trend_state": "上涨"}, {"available": True, "trend_state": "横盘震荡"})
        assert s == "强于板块"

    def test_weaker_trend(self):
        s, _ = ttr.compare_holding_to_theme(
            {"trend_state": "下跌"}, {"available": True, "trend_state": "上涨"})
        assert s == "弱于板块"

    def test_same_trend_but_worse_position_is_weaker(self):
        s, _ = ttr.compare_holding_to_theme(
            {"trend_state": "横盘震荡", "box20_position": "下沿/破位区"},
            {"available": True, "trend_state": "横盘震荡", "box20_position": "箱体上半区"})
        assert s == "弱于板块"

    def test_same_trend_better_position_is_stronger(self):
        s, _ = ttr.compare_holding_to_theme(
            {"trend_state": "横盘震荡", "box20_position": "上沿/突破区"},
            {"available": True, "trend_state": "横盘震荡", "box20_position": "下沿/破位区"})
        assert s == "强于板块"

    def test_synced(self):
        s, _ = ttr.compare_holding_to_theme(
            {"trend_state": "上涨", "box20_position": "箱体上半区"},
            {"available": True, "trend_state": "上涨", "box20_position": "箱体上半区"})
        assert s == "同步"


class TestBuildAndReport:
    @pytest.fixture(autouse=True)
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ttr, "SECTOR_MAP", tmp_path / "sector_code_map.json")
        monkeypatch.setattr(ttr, "SECTOR_DIR", tmp_path / "sectors")
        monkeypatch.setattr(ttr, "HOLDINGS_DIR", tmp_path / "holdings")
        monkeypatch.setattr(ttr, "OUT_DIR", tmp_path / "plans")
        for d in ("sectors", "holdings", "plans"):
            (tmp_path / d).mkdir()
        self.tmp = tmp_path

    def test_theme_without_primary_code_is_marked_unavailable(self, monkeypatch):
        """没有主板块代码的主题必须标 unavailable，**不能悄悄跳过** ——
        跳过会让主线清单看起来完整而其实少了条目。"""
        ttr.SECTOR_MAP.write_text(json.dumps({"themes": [
            {"theme_id": "x", "theme_name": "无代码主题", "priority": "high"}]}), encoding="utf-8")
        rows = ttr.build_sector_summary("2026-08-07")
        assert len(rows) == 1
        assert rows[0]["available"] is False
        assert rows[0]["reason"] == "no primary sector code"

    def test_rows_sorted_unavailable_last_then_by_score(self, monkeypatch):
        """排序：可用的在前、分数降序 —— `make_report` 用 rows[0] 当「今日主线」。"""
        ttr.SECTOR_MAP.write_text(json.dumps({"themes": [
            {"theme_id": "a", "theme_name": "弱", "primary_sector_codes": ["880001"]},
            {"theme_id": "b", "theme_name": "强", "primary_sector_codes": ["880002"]},
            {"theme_id": "c", "theme_name": "无码"}]}), encoding="utf-8")
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: None)
        monkeypatch.setattr(ttr.tm, "analyze", lambda df, code: _an(
            trend="上涨", pos20="上沿/突破区", macd_dir="扩张") if code == "880002" else _an(trend="下跌"))
        rows = ttr.build_sector_summary("2026-08-07")
        assert [r["theme_id"] for r in rows] == ["b", "a", "c"]

    def test_report_writes_both_artifacts(self, monkeypatch):
        ttr.SECTOR_MAP.write_text(json.dumps({"themes": [
            {"theme_id": "b", "theme_name": "半导体", "primary_sector_codes": ["880002"],
             "priority": "high"}]}), encoding="utf-8")
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: None)
        monkeypatch.setattr(ttr.tm, "analyze", lambda df, code: _an(
            trend="上涨", pos20="上沿/突破区", macd_dir="扩张", latest_date="2026-08-07"))
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        ttr.main()
        md = ttr.OUT_DIR / "2026-08-07_theme_tracker.md"
        js = ttr.SECTOR_DIR / "2026-08-07_sector_technical_summary.json"
        assert md.exists() and js.exists()
        text = md.read_text(encoding="utf-8")
        assert "主线方向：**半导体**" in text
        assert "生命周期：**主升/加速**" in text

    def test_empty_theme_map_does_not_crash(self, monkeypatch):
        """主题表为空时**不能崩** —— 它是硬失败 stage，崩了整条盘后链就断。"""
        ttr.SECTOR_MAP.write_text(json.dumps({"themes": []}), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        ttr.main()
        text = (ttr.OUT_DIR / "2026-08-07_theme_tracker.md").read_text(encoding="utf-8")
        assert "主线方向：**未定**" in text
