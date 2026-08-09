"""共享助手的唯一实现 + **禁止重新分叉**的守卫。

2026-08-07 收敛：close_review / news / market_timing 里有 30 个重复的助手定义
（`load`×11、`bare`×6、`finite`×4、`dump`×3、`optional_finite`/`pct_text`/`pct`×2…）。
收敛过程本身抓出四个问题，每个都只在**某几份**里存在：

  ① `load` 10/11 份用裸 `utf-8` ⇒ 遇 BOM 文件 `JSONDecodeError`（只有 rss_filter 用 utf-8-sig）
  ② `dump` 1/3 份缺 `allow_nan=False`（正是写 RiskDecision/SectorState 的那份）
  ③ `pct_text` 两份同名不同行为，只有一份修过「缺数渲染成 +0.00% 把不知道伪装成平盘」
  ④ `b1_holding_state.finite` 与 `code_utils.finite` **同名反语义**
     （前者缺数返回 None，后者返回 0.0）—— 按名字合并会让缺价格的持仓每天被判 P0 清仓

⇒ 这就是为什么重复实现不能靠「看起来一样」就合并，也不能放着不管：
   **分叉的那一份迟早会漏掉别人修过的东西。**
"""
from __future__ import annotations

import ast
import json
import math
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "07_tools"))

import code_utils  # noqa: E402
import fmt  # noqa: E402
import indicators  # noqa: E402
import paths  # noqa: E402


class TestReadJson:
    def test_bom_file_parses(self, tmp_path):
        """⚠️ 收敛前 10/11 份 load 会在这里 JSONDecodeError。

        Windows 上任何用记事本另存过的 JSON 都带 BOM，而目标机就是 Windows。
        """
        p = tmp_path / "b.json"
        p.write_bytes(b'\xef\xbb\xbf{"k": 2}')
        assert paths.read_json(p, {}) == {"k": 2}

    def test_missing_returns_default(self, tmp_path):
        assert paths.read_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}


class TestWriteJson:
    def test_nan_raises_instead_of_writing_invalid_json(self, tmp_path):
        """⚠️ `allow_nan=False` 是这个函数的**主要理由**。

        不带它时 `json.dumps` 写出 `NaN` / `Infinity` —— RFC 7159 不允许，
        而且下游数值比较里 `nan >= 60` 恒为 False，会造成**静默降级**。
        """
        with pytest.raises(ValueError):
            paths.write_json(tmp_path / "x.json", {"v": float("nan")})
        with pytest.raises(ValueError):
            paths.write_json(tmp_path / "y.json", {"v": float("inf")})

    def test_creates_parent_dirs_and_keeps_unicode(self, tmp_path):
        p = tmp_path / "a" / "b" / "c.json"
        paths.write_json(p, {"名": "值"})
        assert json.loads(p.read_text(encoding="utf-8")) == {"名": "值"}
        assert "\\u" not in p.read_text(encoding="utf-8"), "不得转义成 \\uXXXX"


class TestNumberCoercion:
    """`finite` 与 `fnum` **都要留**，语义相反且都被需要。"""

    def test_finite_returns_default(self):
        assert code_utils.finite(None) == 0.0
        assert code_utils.finite("x", -1) == -1

    def test_fnum_returns_none(self):
        """区分「缺数」与「读数是 0」—— `0.0` 是合法读数。"""
        assert code_utils.fnum(None) is None
        assert code_utils.fnum(0) == 0.0 and code_utils.fnum("0") == 0.0

    @pytest.mark.parametrize("v", [float("nan"), float("inf"), float("-inf")])
    def test_both_reject_non_finite(self, v):
        """⚠️ 2026-08-07 补的：此前 `finite` 放过 inf、`fnum` 放过 NaN 和 inf。

        两个常见守卫都拦不住 NaN，所以必须在转换处就拦：

            fnum(x) or 0.0          # bool(nan) 是 True ⇒ 得到 nan
            if v is None or v <= 0  # nan <= 0 是 False ⇒ NaN 当合法价格穿过
        """
        assert code_utils.finite(v) == 0.0
        assert code_utils.fnum(v) is None

    def test_nan_truthiness_trap_is_real(self):
        """把踩过的陷阱本身钉住，防止有人写回 `or 0.0` 的形式。"""
        assert bool(float("nan")) is True
        assert (float("nan") <= 0) is False


class TestBareCode:
    def test_strips_suffix(self):
        assert code_utils.bare_code("600000.SH") == "600000"
        assert code_utils.bare_code(None) == "" and code_utils.bare_code("") == ""

    def test_is_not_clean_code(self):
        """⚠️ `bare_code` 与 `clean_code` 不能互换：后者补足 6 位（台账口径）。

        这些调用点是**跨源对齐同一只票**，从没验证过补位对指数代码是否安全。
        """
        assert code_utils.bare_code("1") == "1"
        assert code_utils.clean_code("1") == "000001"


class TestFmt:
    @pytest.mark.parametrize("v,want", [
        (1.5, "+1.50%"), (-1.5, "-1.50%"), (0, "+0.00%")])
    def test_pct_text_signed(self, v, want):
        assert fmt.pct_text(v) == want

    @pytest.mark.parametrize("v", [None, "x", float("nan"), float("inf")])
    def test_pct_text_missing(self, v):
        """⚠️ 缺数**不得渲染成 `+0.00%`** —— 那把「不知道」伪装成「平盘」，
        读报告的人看不出区别。收敛前 `review_core` 那份还会把 NaN 渲染成 `+nan%`。"""
        assert fmt.pct_text(v) == "unavailable"

    def test_pct_text_placeholder_configurable(self):
        assert fmt.pct_text(None, missing="缺失") == "缺失"

    @pytest.mark.parametrize("v", [None, "x", float("nan"), float("inf")])
    def test_num_text_missing(self, v):
        assert fmt.num_text(v) == "待确认"

    def test_num_text_digits(self):
        assert fmt.num_text(1.234, 1) == "1.2" and fmt.num_text(1.234, 3) == "1.234"


class TestPctChange:
    def test_basic(self):
        assert indicators.pct_change(11, 10) == 10.0
        assert indicators.pct_change(9, 10) == -10.0

    @pytest.mark.parametrize("a,b", [(None, 10), (10, None), (10, 0)])
    def test_returns_none_not_zero(self, a, b):
        """返回 None 而非 0：**「涨跌幅是 0」与「算不出」必须可区分**。"""
        assert indicators.pct_change(a, b) is None

    def test_zero_change_is_zero_not_none(self):
        assert indicators.pct_change(10, 10) == 0.0


class TestNoRefork:
    """守卫：这些助手**不得再在别处重新定义**（标记要代码级生效）。

    只列真正收敛过的名字。刻意**不**管项目里那十几个一次性格式化助手
    （`_fmt_num` / `_pct` / `num` …）—— 它们的占位文本随所在报告的行文，
    强行统一是 churn 而非改进。
    """

    # 名字 → 允许定义它的文件（唯一实现所在处）
    CANON = {
        "read_json": {"paths.py"},
        "write_json": {"paths.py"},
        "write_json_atomic": {"paths.py"},
        "bare_code": {"code_utils.py"},
        "clean_code": {"code_utils.py"},
        "fnum": {"code_utils.py"},
        "pct_change": {"indicators.py"},
        "pct_text": {"fmt.py", "close_review/review_core.py"},  # 后者是 1 行措辞适配器
        "num_text": {"fmt.py"},
        "optional_finite": set(),  # 已全部改为 code_utils.fnum
        # ── 指标序列级入口（2026-08-09 收敛：QSX/MACD 曾各有 3~4 份逐位相同的实现）──
        "qsx_series": {"indicators.py"},
        "macd_series": {"indicators.py"},
        "dks_series": {"indicators.py"},
        "bbi_series": {"indicators.py"},
        "kdj_series": {"indicators.py"},
        "j_series": {"indicators.py"},
        # ── 21 个因子的入口函数（唯一实现 = 因子模块本身）──
        "detect_wave_type": {"factors/wave_type.py"},
        "compute_s_shape": {"factors/s_shape.py"},
        "compute_s_reversal": {"factors/s_shape.py"},
        "ts_corr": {"factors/_util.py"},
        "shares_idx": {"factors/_shares.py"},
        "events_to_idx": {"factors/_shares.py"},
        "detect_distribution": {"factors/distribution.py"},
        "compute_b1_dual": {"factors/b1_dual_factor.py"},
        "compute_long_structure": {"factors/b1_dual_factor.py"},
        "detect_weekly_b1_resonance": {"factors/b1_dual_factor.py"},
        "detect_breakout_pullback_b1": {"factors/b1_dual_factor.py"},
        "detect_launch_segment": {"factors/b1_dual_factor.py"},
        "compute_b1_pullback_fit": {"factors/b1_pullback_fit.py"},
        "compute_perfect_b1_fit": {"factors/perfect_b1_fit.py"},
        "detect_b2": {"factors/b2_surge_factor.py"},
        "detect_bottom_surge": {"factors/b2_surge_factor.py"},
        "detect_surge_then_b1": {"factors/b2_surge_factor.py"},
        "detect_main_rally_start": {"factors/main_rally_factor.py"},
        "main_rally_score": {"factors/main_rally_factor.py"},
        "rsi_regime": {"factors/rsi_state.py"},
        "rsi_divergence": {"factors/rsi_state.py"},
        "rsi_multi": {"factors/rsi_state.py"},
        "rsi_state_score": {"factors/rsi_state.py"},
        "detect_platform_pullback": {"factors/platform_pullback.py"},
        "compute_sector_phase": {"factors/sector_phase.py"},
        "favorable_series": {"factors/sector_phase.py"},
        "mainline_fingerprint": {"factors/sector_mainstream.py"},
        # 注册表接口名（约定：每个 selector 各一份），但不得再长出新文件
        "score": {"factors/_template.py", "factors/alpha101.py", "factors/alpha_pvcorr.py",
                  "factors/baseline.py", "factors/kdj_j.py", "factors/low_vol.py",
                  "factors/mcap.py", "factors/momentum.py", "factors/reversal_quality.py",
                  "factors/reversal_quality_inv.py"},
    }

    def test_no_duplicate_definitions(self):
        offenders = []
        for p in sorted((ROOT / "07_tools").rglob("*.py")):
            # ⚠️ 必须 as_posix()：Windows 上 str() 产反斜杠，
            # 与 CANON 白名单（'close_review/review_core.py' 正斜杠）永不匹配 ⇒ 误报。
            rel = p.relative_to(ROOT / "07_tools").as_posix()
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in self.CANON:
                    if rel not in self.CANON[node.name]:
                        offenders.append(f"{rel}:{node.lineno} def {node.name}")
        assert not offenders, (
            "这些助手已有唯一实现，请改为导入而不要重新定义：\n  "
            + "\n  ".join(offenders))

    def test_finite_is_only_defined_in_code_utils(self):
        """`finite` 单独一条：`b1_holding_state` 曾有个**同名反语义**的版本
        （缺数返回 None）。若有人再定义一个 `finite`，必须先确认它返回默认值
        而不是 None，否则合并时会静默改变 11 个调用点的行为。"""
        found = []
        for p in sorted((ROOT / "07_tools").rglob("*.py")):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name == "finite":
                    found.append(str(p.relative_to(ROOT / "07_tools")))
        assert found == ["code_utils.py"], f"finite 应只在 code_utils 定义，实际: {found}"

    def test_no_plain_utf8_json_read_helper(self):
        """守卫 BOM：不得再出现用裸 `utf-8` 读 JSON 的私有 load。"""
        offenders = []
        for p in sorted((ROOT / "07_tools").rglob("*.py")):
            t = p.read_text(encoding="utf-8")
            for m in re.finditer(r"^def (load|_load|read_json)\(.*?(?=\n(?:def |class |@|\Z))",
                                 t, re.S | re.M):
                body = m.group(0)
                if "read_text" in body and "utf-8-sig" not in body:
                    offenders.append(f"{p.relative_to(ROOT / '07_tools')}: def {m.group(1)}")
        assert not offenders, ("读 JSON 必须用 `utf-8-sig`（Windows 记事本会加 BOM），"
                              "或直接用 paths.read_json：\n  " + "\n  ".join(offenders))


class TestStreamWriterStaysSeparate:
    """研究链的 `backtest_factors.write_json_stream` **刻意不并入 `paths.write_json`**。

    2026-08-07 它原名就叫 `write_json`，与 `paths.write_json` 同名不同行为 ——
    正是本轮反复踩的那类陷阱，已改名消除歧义。两者都要留：

        paths.write_json          allow_nan=False ⇒ NaN 当场崩（产物要显式失败）
        write_json_stream         允许 NaN 且流式  ⇒ 研究指标里 NaN 是合法读数
                                                   （零方差的 Sharpe、无交易的胜率），
                                                   且逐笔上万条时不能先在内存拼整串
    """

    def test_stream_writer_tolerates_nan(self, tmp_path):
        sys.path.insert(0, str(ROOT / "07_tools" / "screening"))
        import backtest_factors as bt

        p = tmp_path / "r.json"
        bt.write_json_stream(p, {"sharpe": float("nan")})
        assert "NaN" in p.read_text(encoding="utf-8"), "研究产物允许 NaN"

    def test_old_name_is_gone(self):
        import backtest_factors as bt

        assert not hasattr(bt, "write_json"), "旧名应已移除，避免与 paths.write_json 混用"
