# -*- coding: utf-8 -*-
"""白名单因子表达式 DSL 测试：数值正确性 / 拒绝面 / 复杂度度量 / 违规阈值。"""

import ast
import math

import pandas as pd
import pytest

from custos.research.evolution import expr_dsl
from custos.research.evolution.expr_dsl import Complexity, ExprError


def make_df(closes, vols=None):
    n = len(closes)
    closes = [float(c) for c in closes]
    vols = [float(v) for v in (vols or [100.0 * (i + 1) for i in range(n)])]
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": vols,
            "amount": [0.0] * n,
        }
    )


DF = make_df([10, 11, 12, 13, 14, 15, 16, 17])


def _eq(result, expected):
    pd.testing.assert_series_equal(result, expected, check_names=False)


# ---------- 合法表达式：与手写 pandas 结果逐点相等 ----------


def test_ma_matches_pandas():
    _eq(
        expr_dsl.evaluate("MA(CLOSE,3)", DF),
        DF["close"].rolling(3, min_periods=3).mean(),
    )


def test_variable_name_case_insensitive():
    # bars 真实列名是小写；DSL 变量名大小写不敏感（CLOSE/close 均可）
    _eq(expr_dsl.evaluate("MA(close,3)", DF), expr_dsl.evaluate("MA(CLOSE,3)", DF))


def test_ratio_expression():
    _eq(
        expr_dsl.evaluate("MA(CLOSE,5)/MA(CLOSE,3)", DF),
        DF["close"].rolling(5, min_periods=5).mean()
        / DF["close"].rolling(3, min_periods=3).mean(),
    )


def test_parse_returns_validated_ast():
    tree = expr_dsl.parse("MA(CLOSE,5)")
    assert isinstance(tree, ast.Expression)
    _eq(
        expr_dsl.evaluate(tree, DF),
        DF["close"].rolling(5, min_periods=5).mean(),
    )


# ---------- 每个算子的数值正确性 ----------


def test_ref():
    _eq(expr_dsl.evaluate("REF(CLOSE,2)", DF), DF["close"].shift(2))


def test_sum():
    _eq(
        expr_dsl.evaluate("SUM(VOLUME,4)", DF),
        DF["volume"].rolling(4, min_periods=4).sum(),
    )


def test_std():
    _eq(
        expr_dsl.evaluate("STD(CLOSE,3)", DF),
        DF["close"].rolling(3, min_periods=3).std(),
    )


def test_max_min():
    _eq(
        expr_dsl.evaluate("MAX(HIGH,3)", DF),
        DF["high"].rolling(3, min_periods=3).max(),
    )
    _eq(
        expr_dsl.evaluate("MIN(LOW,3)", DF),
        DF["low"].rolling(3, min_periods=3).min(),
    )


def test_delta():
    _eq(
        expr_dsl.evaluate("DELTA(CLOSE,2)", DF),
        DF["close"] - DF["close"].shift(2),
    )


def test_roc():
    _eq(
        expr_dsl.evaluate("ROC(CLOSE,2)", DF),
        DF["close"] / DF["close"].shift(2) - 1,
    )


def test_ts_rank():
    # 非单调序列，逐点手算 (strict 与 weak 分位的均值)
    df = make_df([3.0, 1.0, 2.0, 4.0, 3.0])
    s = expr_dsl.evaluate("TS_RANK(CLOSE,3)", df)
    assert math.isnan(s.iloc[0]) and math.isnan(s.iloc[1])
    assert s.iloc[2] == pytest.approx(0.5)  # 窗 [3,1,2]，cur=2 → (1+2)/6
    assert s.iloc[3] == pytest.approx(5 / 6)  # 窗 [1,2,4]，cur=4 → (2+3)/6
    assert s.iloc[4] == pytest.approx(0.5)  # 窗 [2,4,3]，cur=3 → (1+2)/6


def test_abs():
    _eq(
        expr_dsl.evaluate("ABS(REF(CLOSE,1)-CLOSE)", DF),
        (DF["close"].shift(1) - DF["close"]).abs(),
    )


def test_log_positive():
    _eq(expr_dsl.evaluate("LOG(CLOSE)", DF), DF["close"].map(math.log))


def test_log_nonpositive_becomes_nan_not_raise():
    # 一阶差分有负有零 → 全部 NaN，不 raise
    s = expr_dsl.evaluate("LOG(CLOSE-REF(CLOSE,1)-5)", DF)
    assert s.isna().all()


def test_scalar_arithmetic():
    _eq(expr_dsl.evaluate("CLOSE*2+1", DF), DF["close"] * 2 + 1)
    _eq(expr_dsl.evaluate("100/CLOSE", DF), 100 / DF["close"])
    _eq(expr_dsl.evaluate("-CLOSE", DF), -DF["close"])
    _eq(expr_dsl.evaluate("2-CLOSE", DF), 2 - DF["close"])


def test_bare_constant_broadcasts():
    s = expr_dsl.evaluate("5", DF)
    assert (s == 5.0).all() and len(s) == len(DF)


def test_division_by_zero_is_inf_not_raise():
    assert math.isinf(expr_dsl.evaluate("CLOSE/0", DF).iloc[0])
    assert math.isinf(expr_dsl.evaluate("1/0", DF).iloc[0])  # 纯标量除零也不 raise
    assert math.isnan(expr_dsl.evaluate("0/0", DF).iloc[0])


def test_nested_expression():
    _eq(
        expr_dsl.evaluate("MA(REF(CLOSE,1),2)", DF),
        DF["close"].shift(1).rolling(2, min_periods=2).mean(),
    )


def test_missing_column_raises():
    with pytest.raises(ExprError, match="缺少基础变量列"):
        expr_dsl.evaluate("MA(VOLUME,3)", DF.drop(columns=["volume"]))


def test_raw_ast_still_validated():
    # 手工构造 / 绕过 parse 的 AST 不得绕过白名单（fail-closed）
    evil = ast.parse("FOO(CLOSE)", mode="eval")
    with pytest.raises(ExprError):
        expr_dsl.evaluate(evil, DF)


# ---------- 拒绝面（逐个形态钉死） ----------


@pytest.mark.parametrize(
    "bad",
    [
        '__import__("os")',  # 未注册函数 + 字符串参数
        "CLOSE.iloc[0]",  # Attribute
        "CLOSE[0]",  # Subscript
        "x=CLOSE",  # 赋值：eval 模式语法错误
        "FOO(CLOSE)",  # 未注册函数
        "FOO",  # 未声明变量
        "_close",  # 下划线开头的名字
        "CLOSE > 5",  # 比较运算
        "CLOSE > 5 and OPEN > 5",  # BoolOp
        "CLOSE if CLOSE else OPEN",  # IfExp
        "[CLOSE]",  # List 容器
        "lambda: CLOSE",  # Lambda
        "MA(CLOSE,0)",  # n=0
        "MA(CLOSE,-1)",  # 负数是一元运算节点，非字面量
        "MA(CLOSE,251)",  # 超上限
        "MA(CLOSE,2.5)",  # 浮点窗口
        "MA(CLOSE,True)",  # bool 不算 int 字面量
        'MA("CLOSE",5)',  # 字符串常量参数
        '"CLOSE"',  # 字符串常量表达式
        "ABS(CLOSE,2)",  # 单参算子多参
        "MA(CLOSE)",  # 窗口算子缺参
        "MA(x=CLOSE,n=5)",  # 关键字参数
        "ma(CLOSE,5)",  # 算子名大小写敏感（规范形大写）
    ],
)
def test_rejected(bad):
    with pytest.raises(ExprError):
        expr_dsl.parse(bad)


def test_empty_expression_rejected():
    with pytest.raises(ExprError):
        expr_dsl.parse("   ")


# ---------- complexity 计数正确性 ----------


def test_complexity_counts():
    comp = expr_dsl.complexity("REF(CLOSE,1)+REF(CLOSE,1)")
    assert comp.symbol_len == 25
    assert comp.free_params == 1  # distinct 常量值 {1}
    assert comp.base_features == 1  # {close}
    # 重复子树：REF(CLOSE,1) 出现 2 次 (+1)、CLOSE 2 次 (+1)、常量 1 两次 (+1)
    assert comp.repeat_subtrees == 3
    assert comp.depth == 3  # BinOp → Call → 叶子


def test_complexity_distinct_params_and_features():
    comp = expr_dsl.complexity("MA(CLOSE,5)/MA(VOLUME,10)")
    assert comp.free_params == 2  # {5, 10}
    assert comp.base_features == 2  # {close, volume}
    assert comp.repeat_subtrees == 0
    assert comp.depth == 3


def test_complexity_strips_whitespace():
    assert expr_dsl.complexity("  MA( CLOSE , 5 ) ").symbol_len == len("MA(CLOSE,5)")


def test_complexity_accepts_ast_input():
    # AST 输入的 symbol_len 按 ast.unparse 规范形计，与同形字符串一致
    tree = expr_dsl.parse("REF(CLOSE,1)+REF(CLOSE,1)")
    assert expr_dsl.complexity(tree) == expr_dsl.complexity("REF(CLOSE,1)+REF(CLOSE,1)")


def test_complexity_negative_sign_is_unary_not_param():
    # -5 是 UnaryOp(Constant 5)：free_params 记为 5 一个，depth 多一层
    comp = expr_dsl.complexity("-5+CLOSE")
    assert comp.free_params == 1 and comp.depth == 3


# ---------- violations 阈值 ----------


def test_violations_clean_passes():
    ok = Complexity(
        symbol_len=10, free_params=2, base_features=2, repeat_subtrees=1, depth=3
    )
    assert expr_dsl.violations(ok) == []
    # 恰好等于阈值不违规
    edge = Complexity(
        symbol_len=300, free_params=6, base_features=6, repeat_subtrees=8, depth=12
    )
    assert expr_dsl.violations(edge) == []


def test_violations_all_thresholds_trigger():
    bad = Complexity(
        symbol_len=301, free_params=7, base_features=7, repeat_subtrees=9, depth=13
    )
    msgs = expr_dsl.violations(bad)
    assert len(msgs) == 5
    assert any("symbol_len" in m for m in msgs)
    assert any("free_params" in m for m in msgs)
    assert any("base_features" in m for m in msgs)
    assert any("repeat_subtrees" in m for m in msgs)
    assert any("depth" in m for m in msgs)


def test_violations_custom_threshold():
    comp = expr_dsl.complexity("MA(CLOSE,5)")
    assert expr_dsl.violations(comp, max_symbol_len=5)


# ---------- 规模硬上限（在任何递归遍历之前拒，防 RecursionError） ----------


def test_overlong_expression_rejected():
    # ~6000 字符平铺链：旧实现在递归校验里 RecursionError；现在长度门先拒
    giant = "+".join(["CLOSE"] * 1000)
    assert len(giant) > expr_dsl.MAX_EXPR_LEN
    with pytest.raises(ExprError, match="长度"):
        expr_dsl.parse(giant)


def test_too_many_nodes_rejected():
    # 长度没超但节点超：749 个 walk 节点（含 Add 算子节点）> MAX_EXPR_NODES(400)
    chain = "+".join(["1"] * 250)
    assert len(chain) <= expr_dsl.MAX_EXPR_LEN
    with pytest.raises(ExprError, match="节点数"):
        expr_dsl.parse(chain)


def test_deep_nesting_recursion_becomes_expr_error():
    # 几千层括号：解析器自身递归先被打爆 —— 同样归一为 ExprError（不泄 RecursionError）
    deep = "(" * 1500 + "CLOSE" + ")" * 1500
    with pytest.raises(ExprError):
        expr_dsl.parse(deep)


def test_size_caps_boundary():
    # 恰好顶到上限仍受理：4000 字符（单个长浮点字面量）；398 个 walk 节点
    # （133 项平铺：133 常量 + 132 二元 + 132 Add + 1 根）都 <= 上限
    ok = "0." + "1" * (expr_dsl.MAX_EXPR_LEN - 2)  # 恰好 4000 字符的合法表达式
    assert isinstance(expr_dsl.parse(ok), ast.Expression)
    expr_dsl.parse("+".join(["1"] * 133))  # 398 节点
    with pytest.raises(ExprError, match="长度"):
        expr_dsl.parse(ok + "1")  # 4001 字符 > 4000
    with pytest.raises(ExprError, match="节点数"):
        expr_dsl.parse("+".join(["1"] * 134))  # 401 节点 > 400


def test_size_caps_apply_to_raw_ast_too():
    # 手工构造的超限 AST 同样被节点门拦下（绕过 parse 不等于绕过上限）
    tree = ast.parse("+".join(["1"] * 250), mode="eval")
    with pytest.raises(ExprError, match="节点数"):
        expr_dsl.evaluate(tree, DF)


# ---------- _safe_div 标量除零的符号（IEEE：分子符号 × 分母符号） ----------


def test_safe_div_scalar_negative_zero_sign():
    # 1 / -0.0 = -inf（-0.0 == 0 为 True，但符号位不能丢；旧实现恒返回 +inf）
    assert expr_dsl._safe_div(1, -0.0) == float("-inf")
    assert expr_dsl._safe_div(-1, -0.0) == float("inf")
    assert expr_dsl._safe_div(1, 0.0) == float("inf")
    assert expr_dsl._safe_div(-1, 0.0) == float("-inf")
    assert math.isnan(expr_dsl._safe_div(0, -0.0))  # 0/0 仍是 NaN


def test_dsl_level_negative_zero_division():
    # DSL 层端到端：一元负号构造的 -0.0 字面量走标量路径
    assert expr_dsl.evaluate("1/-0.0", DF).iloc[0] == float("-inf")
    assert expr_dsl.evaluate("-1/-0.0", DF).iloc[0] == float("inf")
