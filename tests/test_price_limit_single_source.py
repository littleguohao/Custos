"""`code_utils.price_limit_pct` —— 日涨跌幅限制的**唯一来源** + 禁止再分叉的守卫。

2026-08-07 收敛 4 份实现时发现它们**对同一输入给出不一致的答案**：

    backtest_factors._limit_pct           北交所 → 30 ✅
    reconcile_qfq._limit_pct              北交所 → 30 ✅
    technical_monitor._infer_price_limit  北交所 → **20** ⛔（且只认 920，漏 83/87/43）
    s_shape 的 fallback                   北交所 → **20** ⛔

写错的那两份是 **live 路径**，结果经 `price_limit / 2` 变成「中大阳/中大阴门槛」：

    BJ 股按 20% 算出 10% 门槛（应为 15%）
      ⇒ 10~15% 的涨/跌被误判成中大阳/中大阴
        ⇒ b1_holding_state 的 two_bull_profit_take（P2 分批止盈）
           与 heavy_large_bear（P1 减仓）在不该触发时触发

而 `technical_monitor` 的数据自纠只能把 10 升到 20、**永远到不了 30**，
所以这个偏差不会被历史波动纠正回来。
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("07_tools", "07_tools/market_timing", "07_tools/screening", "07_tools/local_tdx"):
    sys.path.insert(0, str(ROOT / _p))

from code_utils import price_limit_pct  # noqa: E402


class TestCanonicalTable:
    @pytest.mark.parametrize("code,want", [
        ("600000", 10.0), ("601398", 10.0), ("000001", 10.0), ("002415", 10.0),
        ("300750", 20.0), ("301001", 20.0),
        ("688111", 20.0),
        ("689009", 20.0),   # 科创板 CDR —— 收敛前没有一份实现认它
        ("920808", 30.0), ("830799", 30.0), ("870508", 30.0), ("430047", 30.0),
    ])
    def test_table(self, code, want):
        assert price_limit_pct(code) == want

    def test_suffix_and_case_tolerated(self):
        assert price_limit_pct("920808.BJ") == 30.0
        assert price_limit_pct(" 300750.sz ") == 20.0

    def test_garbage_falls_back_to_ten(self):
        assert price_limit_pct(None) == 10.0 and price_limit_pct("") == 10.0

    def test_88_prefix_is_not_beijing(self):
        """⚠️ `88` 前缀**不算北交所**：通达信的板块指数是 880xxx 系列
        （本项目用它算市场宽度）。把 88 算进去会让 880863 被判 30% 限制。

        `launch_point_study.BOARDS` 含 `88` 是因为它只做板块归类、不参与涨跌幅判定。
        """
        assert price_limit_pct("880863") == 10.0


class TestDelegation:
    """四个调用方必须**委托**唯一实现，而不是各自内联前缀表。"""

    def test_backtest_factors(self):
        import backtest_factors as bt
        assert bt._limit_pct("920808") == 30.0 and bt._limit_pct("689009") == 20.0

    def test_reconcile_qfq(self):
        import reconcile_qfq as rq
        assert rq._limit_pct("920808") == 30.0 and rq._limit_pct("689009") == 20.0

    def test_technical_monitor_base(self):
        import pandas as pd
        from market_timing import technical_monitor as tm
        quiet = pd.DataFrame({"close": [10.0 + (i % 2) * 0.01 for i in range(25)]})
        assert tm._infer_price_limit("920808", quiet) == 30
        assert tm._infer_price_limit("830799", quiet) == 30
        assert tm._infer_price_limit("689009", quiet) == 20

    def test_st_downgrade_still_only_for_ten_percent(self):
        """回归保护：安静窗口把 10% 品种降级 5%，但**不得**降级宽幅品种。"""
        import pandas as pd
        from market_timing import technical_monitor as tm
        quiet = pd.DataFrame({"close": [10.0 + (i % 2) * 0.01 for i in range(25)]})
        assert tm._infer_price_limit("600000", quiet) == 5
        for code in ("300750", "688111", "920808"):
            assert tm._infer_price_limit(code, quiet) > 5


class TestNoRefork:
    def test_no_inline_price_limit_prefix_tables(self):
        """守卫：不得再在别处内联涨跌幅前缀表。

        识别特征：同一表达式里同时出现 `688` 与 `20`（或 `920` 与 `30`）。
        只查涨跌幅相关的表达，板块归类（`BOARDS`）与市场判定不在此列。
        """
        allowed = {"code_utils.py"}
        offenders = []
        for p in sorted((ROOT / "07_tools").rglob("*.py")):
            rel = str(p.relative_to(ROOT / "07_tools"))
            if rel in allowed:
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
                if line.lstrip().startswith("#"):
                    continue
                if re.search(r'"688".*\b20\b|\b20\b.*"688"', line) or \
                   re.search(r'"920".*\b(?:20|30)\b|\b(?:20|30)\b.*"920"', line):
                    offenders.append(f"{rel}:{i} {line.strip()[:70]}")
        assert not offenders, ("涨跌幅上限只许在 code_utils.price_limit_pct 定义：\n  "
                              + "\n  ".join(offenders))


class TestMaFlag:
    """⚠️ `ma_flag(None)` 不得渲染成「下」。"""

    def test_none_renders_question_mark(self):
        """上游是**刻意**给 None 的：历史不足 240 日（新股/次新）算不出 MA240。

            refresh_market_indices:124  `bool(close > ma240) if ma240 else None`
            technical_monitor:566       `c > ma240v if ma240v is not None else None`

        显示成「下MA240」是一个未被支持的事实断言，且方向偏空 ——
        同 `fmt.pct_text` 那条教训：不能把「不知道」渲染成一个具体读数。
        """
        from close_review import final_close_review as fcr
        assert fcr.ma_flag(None) == "?"
        assert fcr.ma_flag(True) == "上" and fcr.ma_flag(False) == "下"

    def test_index_row_shows_question_for_missing_ma(self):
        from close_review import final_close_review as fcr
        row = {"name": "上证指数", "close": 3500.0, "change_pct": 0.5,
               "above_ma25": True, "above_ma60": False,
               "above_ma144": None, "above_ma240": None}
        line = fcr.render_index_row(row)
        assert "?MA144" in line and "?MA240" in line
        assert "上MA25" in line and "下MA60" in line

    def test_index_row_missing_close_is_unavailable(self):
        from close_review import final_close_review as fcr
        line = fcr.render_index_row({"name": "x", "close": None, "change_pct": None})
        assert "unavailable" in line
