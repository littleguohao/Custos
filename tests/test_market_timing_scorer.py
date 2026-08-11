"""`market_timing_scorer` —— **0AMV 择时的评分器**，`daily_pipeline` 硬失败 stage。

覆盖率清点（2026-08-09 订正）：35% 基线（244 语句/165 未覆盖）。
⚠️ 单文件覆盖率读数不稳（曾读出 15%/36%/48%）——根因已定位（TODO #46）：
① `--cov` 单文件路径与点分模块两种写法在 pytest-cov 下行为异常（静默无数据/
抢先 import 漏记模块级行），只有目录形式 `--cov=src/pipeline/market_timing` 可靠；
② 读数由「哪些测试文件跑了 scorer」决定（本文件 + test_audit_opt_tools 两处）；
③ 全量跑在不同通过/跳过组合下读数本就不同。⇒ 单文件覆盖率不做门禁，只看趋势。

为什么要认真测：R4 的结论是「**0AMV 是最强单层过滤器，熊市减亏 ~15pp**」——
而这个模块把 0AMV 状态换算成择时总分，总分又决定 `market_state`（进攻/防守/冰点）
与建议总仓位。**换算错了，那 15pp 就无从兑现。**

而且它历史上出过一次真错（审计 B1，注释里记着）：
「空头触发」曾按 9/15 分（中性偏多）而非 0 分计入总分 ——
**0AMV 说空头，评分器却给中性偏多**。现已归一化，本测试钉住它。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("src", "src/pipeline/market_timing"):
    sys.path.insert(0, str(ROOT / _p))

# ⚠️ **包限定导入**，与 `tests/test_audit_opt_tools.py:608` 保持一致。
# 用扁平 `import market_timing_scorer` 会与它形成**同一文件的两个模块对象**
# （conftest 把 src 与 src/pipeline/market_timing 都铺进了 sys.path），
# 实测会让这个文件的覆盖率读数在 15%/36%/48% 之间跳 —— **测量本身变得不可信**。
# 这是 DATA_SOURCE_PRINCIPLE「模块级常量 + 运行时替换 = 陷阱」变体①的又一处后果。
from market_timing import market_timing_scorer as ms  # noqa: E402


class TestScoreAmv:
    """0AMV 计分 —— 权重最高（15 分），且是 regime 的直接来源。"""

    def test_bear_scores_zero_not_neutral(self):
        """⚠️ **回归**（审计 B1）：空头必须 0 分。

        曾按 9/15（中性偏多）计入 ⇒ 0AMV 说空头、评分器却偏多，
        择时总分被系统性抬高、`market_state` 可能仍判「震荡偏强」。
        """
        s, why = ms.score_amv({"amv_0": {"effective_state": "空头", "amv_change_pct": -3.0}})
        assert s == 0, f"空头给了 {s} 分"
        assert "空头" in why

    def test_bear_zone_wording_also_zero(self):
        """三套并行词表都要归一 —— `amv_zone` 写的是「空头触发」。"""
        assert ms.score_amv({"amv_0": {"amv_zone": "空头触发"}})[0] == 0

    def test_long_scores_full(self):
        assert ms.score_amv({"amv_0": {"effective_state": "做多", "amv_change_pct": 5.0}})[0] == 15

    def test_long_zone_wording(self):
        assert ms.score_amv({"amv_0": {"amv_zone": "做多触发"}})[0] == 15

    def test_missing_value_without_lock_is_half(self):
        """无锁定态且无读数 ⇒ 半分，并**说明是按中性处理** ——
        不能给 0（那等于把「不知道」当成「空头」）也不能给满分。"""
        s, why = ms.score_amv({"amv_0": {}})
        assert s == 7.5 and "中性半分" in why

    def test_no_lock_positive_is_slightly_bullish(self):
        assert ms.score_amv({"amv_0": {"amv_change_pct": 1.5}})[0] == 9

    def test_no_lock_negative_is_slightly_bearish(self):
        assert ms.score_amv({"amv_0": {"amv_change_pct": -1.0}})[0] == 5

    def test_lock_overrides_daily_value(self):
        """锁定态优先于当日读数 —— 状态机的锁定语义不能被单日读数推翻。"""
        assert ms.score_amv({"amv_0": {"effective_state": "空头", "amv_change_pct": 3.0}})[0] == 0


class TestScoreMacro:
    def test_all_blank_is_half_with_explicit_note(self):
        s, why = ms.score_macro({})
        assert s == 7.5 and "未填" in why and "人工补充" in why

    def test_best_case_capped_at_15(self):
        s, _ = ms.score_macro({"macro_policy": {
            "monetary_policy": "宽松", "fiscal_policy": "积极",
            "credit_environment": "扩张", "regulation_environment": "呵护市场"}})
        assert s == 15, "加起来 4+4+3+4=15，且必须被 min 夹住"

    def test_neutral_across_the_board(self):
        s, _ = ms.score_macro({"macro_policy": {
            "monetary_policy": "中性", "fiscal_policy": "中性",
            "credit_environment": "稳定", "regulation_environment": "中性"}})
        assert s == 7.5

    def test_adverse_items_are_listed(self):
        """不利项必须逐条列出 —— 只给分数无法复盘。"""
        _, why = ms.score_macro({"macro_policy": {"monetary_policy": "收紧"}})
        assert "货币政策非宽松" in why


class TestScoreOverseas:
    @pytest.mark.parametrize("avg,want", [(1.0, 10), (0.5, 7), (0.0, 5), (-0.5, 3), (-1.0, 1)])
    def test_tiers(self, avg, want):
        d = {"overseas_market": {"nasdaq_change_pct": avg}}
        assert ms.score_overseas(d)[0] == want

    def test_missing_is_half(self):
        s, why = ms.score_overseas({})
        assert s == 5 and "未填" in why

    def test_averages_only_available_markets(self):
        """缺的市场不参与平均 —— 用 0 填充会把缺数算成「持平」。"""
        d = {"overseas_market": {"nasdaq_change_pct": 2.0, "sp500_change_pct": None}}
        assert ms.score_overseas(d)[0] == 10


class TestStatusFromScore:
    def test_monotonic(self):
        """分数→状态必须单调 —— 非单调会让「分数更高但状态更弱」这种荒谬结果出现。"""
        seen = [ms.status_from_score(s) for s in range(0, 101, 5)]
        order = {}
        for i, st in enumerate(seen):
            order.setdefault(st, i)
        # 同一状态的出现区间不得交叉
        idx = [order[st] for st in dict.fromkeys(seen)]
        assert idx == sorted(idx), f"状态随分数非单调：{list(dict.fromkeys(seen))}"


class TestStaleDetection:
    def test_stale_when_as_of_is_older(self):
        """`as_of` 早于评分日 ⇒ stale。

        README 记着这条的由来：当日文件里装 T-1 数据同样记 stale，
        否则「昨天的数据」会拿到满分。
        """
        assert ms.is_stale({"as_of": "2026-08-06"}, "2026-08-07") is True

    def test_not_stale_when_same_day(self):
        assert ms.is_stale({"as_of": "2026-08-07"}, "2026-08-07") is False

    def test_quality_stale_flag_alone_is_enough(self):
        """判据①：`quality` 已被门控/合并标 stale ⇒ 直接 stale，不看 as_of。"""
        assert ms.is_stale({"quality": "stale"}) is True

    def test_missing_as_of_is_currently_fail_open(self):
        """⚠️ **如实记录现状：缺 `as_of` 被当成「新鲜」**（fail-open）。

        `return bool(day and as_of) and as_of != day` —— `as_of` 为空时整式为 False。
        于是一个没写 `as_of` 的 section 会**拿到当日满分**。

        这与仓库别处的 fail-closed 原则相反（比较 `runtime_gate._QUALITY_PASS`：
        「风控组件的未知状态必须等于阻断」）。上游已缓解：
        `merge_incremental_market` 被改成**必须写 as_of**（README 记着同一隐患：
        「一个『确认过但其实是上周的』0AMV 会拿满分并授予加仓权」）。

        **不在补测试的这一轮改它** —— 改成 fail-closed 会降低评分、改变 live 择时行为，
        属语义改动。已登记待办交 owner 定。本测试锁住现状，改动时会被提醒。
        """
        assert ms.is_stale({}, "2026-08-07") is False, "行为变了：请同步更新待办与本测试"
        assert ms.is_stale({"as_of": None}, "2026-08-07") is False


class TestFnum:
    def test_none_and_garbage(self):
        assert ms.fnum(None) is None
        assert ms.fnum("x") is None

    def test_zero_is_kept(self):
        """⚠️ `0` 是合法读数（涨跌幅为 0 真实存在），不得变 None。"""
        assert ms.fnum(0) == 0.0
        assert ms.fnum("0") == 0.0


class TestIsStaleHonorsProducerQuality:
    """⚠️ `is_stale` 必须听**生产者声明的 quality**，不能只认 `"stale"` 一个词。

    2026-08-10 查出：两个生产者用的是**不同词表**，而消费者只认一个词 ⇒

        merge_incremental_market.section_quality  → auto / stale / **raw_only**（无数据日）
        market_timing_collector._freshness        → auto / **degraded**（数据日与预期不符）
                                                     + missing 分支

    `raw_only` 与 `degraded` 被当成新鲜、照满分计入：实测 `score_breadth` 给
    **11/15 分**，而归因文案是「涨跌比 1.24。」—— 完全看不出数据日未知。

    触发路径是真的（不是理论）：`collect_incremental_market` 在索引非日期时写
    `date: ''`（`str(last.name if hasattr(last.name,'strftime') else '')`），
    而「mootdx Reader 返回 DatetimeIndex 而非列」是本项目记录在案的反复踩坑点；
    merge 据此写 `quality: raw_only`。**生产者已如实声明，只是没人听。**
    """

    DAY = "2026-08-10"

    def _sec(self, quality, as_of):
        return {"up_count": 2600, "down_count": 2100, "quality": quality, "as_of": as_of}

    @pytest.mark.parametrize("quality", ["stale", "raw_only", "degraded", "missing"])
    def test_producer_declared_not_fresh_is_stale(self, quality):
        assert ms.is_stale(self._sec(quality, ""), self.DAY) is True, \
            f"quality={quality} 是生产者声明的「不新鲜」，必须判 stale"

    def test_auto_with_matching_as_of_is_fresh(self):
        assert ms.is_stale(self._sec("auto", self.DAY), self.DAY) is False

    @pytest.mark.parametrize("quality", ["raw_only", "degraded", "missing"])
    def test_score_degrades_to_neutral(self, quality):
        """⚠️ 这才是真正要守的东西：分数从 11 降到中性 7.5。

        只断言 `is_stale` 为 True 不够 —— 判据改了但 `score_*` 没用它，
        断言照样通过而评分毫无变化。
        """
        s, note = ms.score_breadth({"date": self.DAY,
                                    "market_breadth": self._sec(quality, "")})
        assert s == 7.5, f"quality={quality} 应按中性 7.5 记，实际 {s}"
        assert "stale" in note or "非当日" in note, f"归因文案要说明原因：{note}"

    def test_vocabulary_covers_both_producers(self):
        """⚠️ 词表必须覆盖**两个生产者的全部输出** —— 少一个就是又一个静默满分。

        用 AST/源码取生产者实际会返回的字面量，而不是靠记忆维护清单。
        """
        import re

        import contracts as C
        from market_timing import market_timing_collector as mtc
        from market_timing import merge_incremental_market as mim

        emitted = set()
        for mod in (mim.section_quality, mtc._freshness):
            import inspect
            for m in re.finditer(r'return "(\w+)"', inspect.getsource(mod)):
                emitted.add(m.group(1))
        assert emitted, "取不到生产者返回的字面量 —— 本测试已失效"
        unknown = emitted - C.SECTION_QUALITY
        assert not unknown, (
            f"生产者会返回 {sorted(unknown)}，但不在 contracts.SECTION_QUALITY 里 "
            f"⇒ `is_stale` 会把它当新鲜。请把它归入 SECTION_QUALITY，"
            f"并判断是否属 SECTION_NOT_FRESH。")


class TestGateOverridesScore:
    """⚠️ 运行门控**覆盖评分结论** —— `make_report` 里这一段是全模块最要紧的未覆盖代码。

    评分器算出「进攻 / 允许开新仓」，但门控说数据 blocked 时必须翻成「禁止 / 强风控 /
    仓位 0%-20%」。这是「**数据不可信时不得放宽交易权限**」的落点 ——
    整个门控体系的价值就在这一步兑现，它错了则前面所有新鲜度判定都白做。
    """

    MODULES = [("宏观政策环境", 15, 15.0, "满分"), ("0AMV 活跃市值", 15, 15.0, "满分"),
               ("多指数结构", 15, 15.0, "满分"), ("市场宽度", 15, 15.0, "满分"),
               ("情绪", 15, 15.0, "满分"), ("成交额", 10, 10.0, "满分"),
               ("主线", 15, 15.0, "满分")]          # 合计 100 ⇒ 进攻档

    def _report(self, gate=None, modules=None):
        return ms.make_report({"date": "2026-08-11"}, modules or self.MODULES, gate)

    def test_no_gate_keeps_score_conclusion(self):
        r = self._report()
        assert "**进攻**" in r and "**允许**" in r and "**普通**" in r

    def test_blocked_forces_forbid_and_min_position(self):
        r = self._report({"market_quality": {"status": "blocked", "quality_score": 0.3}})
        assert "今日是否允许开新仓：**禁止**" in r
        assert "风控等级：**强风控**" in r
        assert "建议总仓位：**0%-20%**" in r

    def test_blocked_does_not_rewrite_status_line(self):
        """⚠️ `blocked` **不改 `status`** —— 报告会同时出现
        「状态：**进攻**」与「允许开新仓：**禁止**、仓位 0%-20%」。

        这是当前实现的行为，本条**钉住现状**并把它标出来：读报告的人可能
        据「状态：进攻」形成印象而忽略下面三行。是否要让 blocked 也把 status
        降档，属口径问题，留给 owner（TODO 已记）。
        """
        r = self._report({"market_quality": {"status": "blocked"}})
        assert "状态：**进攻**" in r, "现状是 status 不被 blocked 改写"
        assert "**禁止**" in r

    def test_degraded_downgrades_only_permissive_wordings(self):
        """⚠️ `degraded` 只在 `open_perm` 以「允许」开头时降级。

        `status_from_score` 的五种 open_perm 里只有 `允许` / `允许，但精选`
        以它开头；`仅低吸 / 小仓核心主线`、`原则上不新开`、`禁止追涨`
        本就受限，degraded 只把 risk 抬到「提高」。
        """
        r = self._report({"market_quality": {"status": "degraded"}})
        assert "仅观察 / 小仓待确认" in r
        assert "风控等级：**提高**" in r

    def test_degraded_leaves_already_restrictive_permission(self):
        weak = [("宏观政策环境", 15, 7.0, ""), ("0AMV 活跃市值", 15, 7.0, ""),
                ("多指数结构", 15, 7.0, ""), ("市场宽度", 15, 7.0, ""),
                ("情绪", 15, 7.0, ""), ("成交额", 10, 4.0, ""), ("主线", 15, 7.0, "")]
        total = sum(x[2] for x in weak)
        assert 40 <= total < 60, f"用例要落在震荡偏弱档，实际 {total}"
        r = ms.make_report({"date": "2026-08-11"}, weak,
                           {"market_quality": {"status": "degraded"}})
        assert "仅低吸 / 小仓核心主线" in r, "已受限的措辞不该被改写"
        assert "仅观察 / 小仓待确认" not in r

    def test_amv_caveat_always_present(self):
        """「若 0AMV 未填，最终仓位不得上调到进攻档」这句是**无条件**的提醒。

        0AMV 是全链方向的主过滤器（R4：熊市减亏 ~15pp），这句话不该因为
        当天恰好填了就消失 —— 它是给读者的规则说明，不是当日状态。
        """
        assert "0AMV 未填" in self._report()

    def test_quality_notes_absent_says_so(self):
        """⚠️ 无数据质量提示时出「无特殊数据质量提示」而**不是**整节空白 ——
        空白读者分不清「查了没有」与「没查」。"""
        assert "无特殊数据质量提示" in self._report()

    def test_quality_notes_are_rendered(self):
        r = ms.make_report({"date": "2026-08-11",
                            "data_quality": {"notes": ["宽度取自 T-1", "海外缺 KOSPI"]}},
                           self.MODULES)
        assert "宽度取自 T-1" in r and "海外缺 KOSPI" in r
        assert "无特殊数据质量提示" not in r

    def test_gate_line_reports_score(self):
        r = self._report({"market_quality": {"status": "degraded", "quality_score": 0.62}})
        assert "运行时质量门" in r and "0.62" in r

    def test_module_table_sums_to_total(self):
        r = self._report()
        assert "| 合计 | 100 | 100.00 | |" in r


class TestScoreIndices:
    """多指数结构评分（38 行未覆盖）。四指数分化时按结构性行情处理。"""

    def _one(self, **kw):
        base = {"available": True, "change_20d_pct": 0.0, "above_ma25": None,
                "above_ma60": None, "above_ma144": None, "above_ma240": None}
        base.update(kw)
        return {"a_share_indices": {"上证": base}, "date": "2026-08-11"}

    def test_no_available_index_is_neutral_and_says_why(self):
        s, note = ms.score_indices({"a_share_indices": {"上证": {"available": False}}})
        assert s == 7.5 and "缺失" in note

    def test_empty_section_is_neutral(self):
        assert ms.score_indices({})[0] == 7.5

    def test_all_ma_above_scores_higher_than_all_below(self):
        up = ms.score_indices(self._one(above_ma25=True, above_ma60=True,
                                        above_ma144=True, above_ma240=True))[0]
        dn = ms.score_indices(self._one(above_ma25=False, above_ma60=False,
                                        above_ma144=False, above_ma240=False))[0]
        assert up > 7.5 > dn, f"上穿四线应高于中性、下破应低于中性：{up} / {dn}"

    def test_score_is_clamped_to_0_15(self):
        """⚠️ 归一化除数 6 是个近似（源码注释 `approx -1~1`）——
        单指数极端值会溢出，必须被 clamp 住而不是给出 >15 的分。"""
        hot = self._one(change_20d_pct=99, above_ma25=True, above_ma60=True,
                        above_ma144=True, above_ma240=True)
        hot["a_share_indices"]["上证"]["intraday"] = {"intraday_change_pct": 9.9}
        s = ms.score_indices(hot)[0]
        assert 0 <= s <= 15, f"越界：{s}"

    def test_missing_intraday_is_not_called_strong(self):
        """⚠️ `intraday=None` 在 strong/weak 判定里被 `or 0` 兜成 0 ——
        方向安全（不会被误判成强），本条钉住它别哪天变成误判。"""
        _, note = ms.score_indices(self._one(change_20d_pct=0.5))
        assert "强=无明显" in note

    def test_strong_and_weak_are_named(self):
        d = {"date": "2026-08-11", "a_share_indices": {
            "强指": {"available": True, "change_20d_pct": 5.0},
            "弱指": {"available": True, "change_20d_pct": -5.0}}}
        _, note = ms.score_indices(d)
        assert "强指" in note and "弱指" in note


class TestSentimentAndTurnover:
    """⚠️ 补齐 v0.40 那次**声称过但只测了 breadth** 的两段。

    2026-08-10 的提交信息写「breadth 11 → 7.5、sentiment → 7.5、turnover 8 → 4」，
    但当时的测试只驱动了 `score_breadth` —— 另两段是**推断**，没有实测。
    这正是「声称 vs 实测」要分开记的那类：结论对了，证据没到位。
    """

    DAY = "2026-08-11"

    def test_sentiment_stale_is_neutral(self):
        d = {"date": self.DAY,
             "sentiment": {"limit_up_count": 90, "limit_down_count": 2,
                           "quality": "raw_only", "as_of": ""}}
        s, note = ms.score_sentiment(d)
        assert s == 7.5, f"stale 情绪应按中性，实际 {s}"
        assert "stale" in note

    def test_turnover_stale_is_half(self):
        d = {"date": self.DAY,
             "turnover": {"turnover_change_pct": 30.0,
                          "quality": "degraded", "as_of": ""}}
        s, note = ms.score_turnover(d)
        assert s == 4, f"stale 成交额应按半分，实际 {s}"
        assert "stale" in note

    def test_sentiment_fresh_hot_scores_high(self):
        """对照：同样的数字在**新鲜**时应显著高于中性 —— 否则上面两条测的是恒定值。"""
        d = {"date": self.DAY,
             "sentiment": {"limit_up_count": 90, "limit_down_count": 2,
                           "quality": "auto", "as_of": self.DAY}}
        assert ms.score_sentiment(d)[0] > 7.5

    def test_turnover_fresh_surge_scores_high(self):
        d = {"date": self.DAY,
             "turnover": {"turnover_change_pct": 30.0,
                          "quality": "auto", "as_of": self.DAY}}
        assert ms.score_turnover(d)[0] == 8

    def test_missing_counts_neutral_before_stale_check(self):
        """⚠️ 缺涨跌停数时先走「缺失」分支（归因不同）——
        两种降级都给 7.5，但**文案必须能区分**，否则复盘时分不清是没采到还是数据旧了。"""
        _, note = ms.score_sentiment({"date": self.DAY, "sentiment": {}})
        assert "缺失" in note and "stale" not in note

    def test_turnover_missing_says_unconfirmed_not_stale(self):
        _, note = ms.score_turnover({"date": self.DAY, "turnover": {}})
        assert "未确认" in note and "stale" not in note

    @pytest.mark.parametrize("chg,want", [(30, 8), (10, 6), (0, 4), (-10, 3), (-30, 1)])
    def test_turnover_ladder(self, chg, want):
        d = {"date": self.DAY, "turnover": {"turnover_change_pct": chg,
                                            "quality": "auto", "as_of": self.DAY}}
        assert ms.score_turnover(d)[0] == want

    def test_limit_down_wave_drags_score_below_neutral(self):
        """跌停潮必须把情绪压到中性以下 —— 这是它存在的意义。"""
        d = {"date": self.DAY,
             "sentiment": {"limit_up_count": 10, "limit_down_count": 50,
                           "quality": "auto", "as_of": self.DAY}}
        assert ms.score_sentiment(d)[0] < 7.5

    def test_sentiment_clamped_to_0_15(self):
        for lu, ld, blow, h in [(200, 0, 0.0, 9), (0, 200, 0.9, 0)]:
            d = {"date": self.DAY,
                 "sentiment": {"limit_up_count": lu, "limit_down_count": ld,
                               "blowup_rate": blow, "market_height": h,
                               "quality": "auto", "as_of": self.DAY}}
            s = ms.score_sentiment(d)[0]
            assert 0 <= s <= 15, f"越界：{s}"


class TestMainEndToEnd:
    """`main()` 的端到端：读 `market_timing_input` → 七模块打分 → 落盘报告。

    ⚠️ 权重之和必须是 100，否则「择时评分 X/100」这句是假的 ——
    而下游 `status_from_score` 的五个档位（80/60/40/20）全按百分制切。
    """

    def _run(self, monkeypatch, tmp_path, market, gate=None):
        import json
        import sys as _s
        # ⚠️ patch **全部** Path 常量（漏一个就会往真实目录写 ——
        #    2026-08-07 给 runner 写 harness 时漏了 REVIEWS，2026-08-10 漏了 AUDIT）
        import pathlib as _pl
        for attr in dir(ms):
            v = getattr(ms, attr, None)
            if attr.isupper() and isinstance(v, _pl.Path):
                d = tmp_path / attr.lower()
                d.mkdir(parents=True, exist_ok=True)
                monkeypatch.setattr(ms, attr, d)
        inp = tmp_path / "in.json"
        inp.write_text(json.dumps(market, ensure_ascii=False), encoding="utf-8")
        if gate is not None:
            gp = ms.QUALITY_DIR / f"{market['date']}_runtime_gate.json"
            gp.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(_s, "argv", ["x", "--date", market["date"], "--input", str(inp)])
        ms.main()
        out = ms.OUT_DIR / f"{market['date']}_market_timing_score.md"
        assert out.exists(), "报告未落盘"
        return out.read_text(encoding="utf-8")

    MARKET = {"date": "2026-08-11",
              "amv_0": {"amv_zone": "做多触发", "amv_change_pct": 5.0,
                        "quality": "confirmed", "as_of": "2026-08-11"},
              "market_breadth": {"up_count": 3200, "down_count": 1500,
                                 "quality": "auto", "as_of": "2026-08-11"},
              "sentiment": {"limit_up_count": 70, "limit_down_count": 3,
                            "quality": "auto", "as_of": "2026-08-11"},
              "turnover": {"turnover_change_pct": 12.0,
                           "quality": "auto", "as_of": "2026-08-11"},
              "a_share_indices": {"上证": {"available": True, "change_20d_pct": 4.0,
                                           "above_ma25": True, "above_ma60": True}},
              "data_quality": {"notes": []}}

    def test_weights_sum_to_100(self, monkeypatch, tmp_path):
        """⚠️ 权重之和必须真的是 100 —— 从 `main()` 源码里把每个模块的权重字面量
        抠出来求和，而不是只看报告里那行硬编码的「| 合计 | 100 |」
        （那是 make_report 里的字面量，改掉任一模块权重它照样打印 100）。
        """
        import inspect
        import re

        src = inspect.getsource(ms.main)
        weights = [int(w) for w in
                   re.findall(r'\("[^"]+",\s*(\d+),\s*\*score_', src)]
        assert len(weights) == 8, f"只抠到 {len(weights)} 个模块权重 —— 本测试已失效"
        assert sum(weights) == 100, \
            f"模块权重之和 = {sum(weights)}（{weights}）—— 「X/100」与档位切分都会失真"
        r = self._run(monkeypatch, tmp_path, dict(self.MARKET))
        assert "| 合计 | 100 |" in r

    def test_all_seven_modules_appear(self, monkeypatch, tmp_path):
        r = self._run(monkeypatch, tmp_path, dict(self.MARKET))
        for name in ("宏观政策环境", "0AMV 活跃市值", "外围市场影响", "指数趋势",
                     "市场宽度", "情绪强度", "成交量能", "主线清晰度"):
            assert f"| {name} |" in r, f"缺模块 {name}"

    def test_missing_gate_file_is_not_an_error(self, monkeypatch, tmp_path):
        """门控文件不存在时按空处理 —— 不得因此崩掉整个评分（它是硬失败 stage）。"""
        r = self._run(monkeypatch, tmp_path, dict(self.MARKET), gate=None)
        assert "运行时质量门" not in r, "无门控文件时不该编出一行门控结论"

    def test_gate_file_blocked_reaches_the_report(self, monkeypatch, tmp_path):
        """⚠️ 端到端确认门控**真的被读到并生效** —— 只测 `make_report` 不够：
        文件名拼错、目录常量指错，单测都发现不了。"""
        r = self._run(monkeypatch, tmp_path, dict(self.MARKET),
                      gate={"market_quality": {"status": "blocked", "quality_score": 0.2}})
        assert "今日是否允许开新仓：**禁止**" in r
        assert "运行时质量门：blocked" in r
