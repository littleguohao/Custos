"""`market_timing_scorer` —— **0AMV 择时的评分器**，`daily_pipeline` 硬失败 stage。

覆盖率清点（2026-08-07）：48%、128 语句未覆盖。

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
for _p in ("07_tools", "07_tools/market_timing"):
    sys.path.insert(0, str(ROOT / _p))

# ⚠️ **包限定导入**，与 `tests/test_audit_opt_tools.py:608` 保持一致。
# 用扁平 `import market_timing_scorer` 会与它形成**同一文件的两个模块对象**
# （conftest 把 07_tools 与 07_tools/market_timing 都铺进了 sys.path），
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
