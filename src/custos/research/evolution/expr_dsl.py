# -*- coding: utf-8 -*-
"""白名单因子表达式 DSL：解析 + 步行解释 + 复杂度度量（LLM 因子进化引擎的候选因子表示层）。

设计借鉴 QuantaAlpha 的因子 DSL（OHLCV 基础变量 + 时序算子组合的表达式树），但
**用白名单 AST 步行解释替代其 eval()**：表达式经 ``ast.parse(mode="eval")`` 解析后
逐节点核对白名单 —— 未登记的节点类型 / 函数 / 变量一律 ``ExprError`` 拒绝，全程
不触 eval/exec，LLM 生成的任意字符串都逃不出白名单。

口径约定：

- 基础变量（``BASE_VARIABLES``）即 bars DataFrame 的真实列名，与
  ``backtest_factors._load_bars_local`` 的产出一致（open/high/low/close/volume，
  小写；loader 不算 RET，故不提供）。变量名**大小写不敏感**（``CLOSE``/``close``
  均可，规范形为小写列名）；算子名大小写**敏感**（规范形大写，如 ``MA``）。
- 算子（``OPERATORS`` 注册表）全部作用于 pandas Series、按个股时序计算
  （rolling/shift 天然只看 ≤t 历史，无未来函数）。窗口参数 n 必须是正整数
  字面量且 ≤ ``MAX_WINDOW``（布尔字面量不算 int；``-1`` 是一元运算而非字面量，
  同样拒绝）。
- 数值安全：除零 → inf/NaN、``LOG`` 遇非正值 → NaN，**不 raise**（研究侧允许
  NaN，由评估层按截面日剔除）。
- 复杂度（``complexity``/``violations``）：``repeat_subtrees`` 统计所有同构子树
  （含 Name/Constant 叶子）的重复次数 —— 签名出现 k>1 次的各计 k-1 后求和；
  ``depth`` 为 AST 最大嵌套深度（叶子计 1，每嵌套一层 +1，不含 Expression 根）；
  ``symbol_len`` 为去掉所有空白字符后的表达式长度（AST 输入时按 ``ast.unparse``
  的规范形计）。
"""

from __future__ import annotations

import ast
import collections
import math
import operator
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

# bars DataFrame 的真实列名（小写，见 backtest_factors._load_bars_local）。
BASE_VARIABLES: tuple[str, ...] = ("open", "high", "low", "close", "volume")

# 时序窗口上限：约一年交易日，超出视为 LLM 胡言直接拒。
MAX_WINDOW = 250

_BASE_SET = frozenset(BASE_VARIABLES)


class ExprError(ValueError):
    """表达式违反白名单 / 语法错误 / 数据缺列（fail-closed，一律拒绝执行）。"""


# ---------------------------------------------------------------------------
# 算子实现（全部 Series → Series，按个股时序；rolling 一律 min_periods=n）
# ---------------------------------------------------------------------------


def _op_ref(x: pd.Series, n: int) -> pd.Series:
    """REF(x, n)：n 周期前的值。"""
    return x.shift(n)


def _op_ma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def _op_sum(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).sum()


def _op_std(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).std()


def _op_max(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).max()


def _op_min(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).min()


def _op_delta(x: pd.Series, n: int) -> pd.Series:
    """DELTA(x, n) = x - REF(x, n)。"""
    return x - x.shift(n)


def _op_roc(x: pd.Series, n: int) -> pd.Series:
    """ROC(x, n) = x / REF(x, n) - 1（REF 为 0 → inf/NaN，不 raise）。"""
    return x / x.shift(n) - 1


def _pct_rank_of_last(w: pd.Series) -> float:
    """窗口末值在窗内的分位（strict 与 weak 口径的均值，同分平局各让一半）。"""
    cur = w.iloc[-1]
    less = int((w < cur).sum())
    leq = int((w <= cur).sum())
    return (less + leq) / (2.0 * len(w))


def _op_ts_rank(x: pd.Series, n: int) -> pd.Series:
    """TS_RANK(x, n)：当前值在过去 n 周期中的分位，(0, 1]。"""
    return x.rolling(n, min_periods=n).apply(_pct_rank_of_last)


def _op_abs(x: pd.Series) -> pd.Series:
    return x.abs()


def _op_log(x: pd.Series) -> pd.Series:
    """LOG(x)：自然对数；非正值（含 NaN）→ NaN，不 raise。"""
    return x.where(x > 0).map(math.log)


OPERATORS: dict[str, Callable[..., pd.Series]] = {
    "REF": _op_ref,
    "MA": _op_ma,
    "SUM": _op_sum,
    "STD": _op_std,
    "MAX": _op_max,
    "MIN": _op_min,
    "DELTA": _op_delta,
    "ROC": _op_roc,
    "TS_RANK": _op_ts_rank,
    "ABS": _op_abs,
    "LOG": _op_log,
}

# 带窗口参数 (x, n) 的算子；其余白名单算子（ABS/LOG）为单参数。
_WINDOW_OPS = frozenset(
    {"REF", "MA", "SUM", "STD", "MAX", "MIN", "DELTA", "ROC", "TS_RANK"}
)


# ---------------------------------------------------------------------------
# 白名单校验
# ---------------------------------------------------------------------------


def _validate_window_arg(name: str, node: ast.AST) -> None:
    """窗口参数 n：正整数字面量且 ≤ MAX_WINDOW（bool 是 int 子类，显式排除）。"""
    if (
        not isinstance(node, ast.Constant)
        or not isinstance(node.value, int)
        or isinstance(node.value, bool)
    ):
        raise ExprError(f"{name} 的窗口参数 n 必须是正整数字面量")
    if not 1 <= node.value <= MAX_WINDOW:
        raise ExprError(f"{name} 的窗口 n={node.value} 超出允许范围 [1, {MAX_WINDOW}]")


def _validate_call(node: ast.Call) -> None:
    if not isinstance(node.func, ast.Name) or node.func.id not in OPERATORS:
        raise ExprError(f"函数不在白名单: {ast.unparse(node.func)}")
    name = node.func.id
    if node.keywords:
        raise ExprError(f"{name} 不接受关键字参数")
    want = 2 if name in _WINDOW_OPS else 1
    if len(node.args) != want:
        raise ExprError(f"{name} 需要恰好 {want} 个参数，实际 {len(node.args)} 个")
    if name in _WINDOW_OPS:
        _validate_window_arg(name, node.args[1])
    _validate(node.args[0])


def _validate_op(node: ast.BinOp | ast.UnaryOp) -> None:
    allowed = _BIN_OPS if isinstance(node, ast.BinOp) else _UNARY_OPS
    if type(node.op) not in allowed:
        raise ExprError(f"算子不在白名单: {type(node.op).__name__}")
    children = (
        (node.left, node.right) if isinstance(node, ast.BinOp) else (node.operand,)
    )
    for child in children:
        _validate(child)


def _validate_leaf(node: ast.Name | ast.Constant) -> None:
    if isinstance(node, ast.Name):
        if node.id.lower() not in _BASE_SET:
            raise ExprError(f"未声明的变量: {node.id}（基础变量: {sorted(_BASE_SET)}）")
        return
    if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
        raise ExprError(f"只允许 int/float 字面量，得到: {node.value!r}")


def _validate(node: ast.AST) -> None:
    """逐节点核对白名单；任何未登记形态一律 ExprError（fail-closed）。"""
    if isinstance(node, ast.Expression):
        _validate(node.body)
    elif isinstance(node, (ast.BinOp, ast.UnaryOp)):
        _validate_op(node)
    elif isinstance(node, ast.Call):
        _validate_call(node)
    elif isinstance(node, (ast.Name, ast.Constant)):
        _validate_leaf(node)
    else:
        raise ExprError(f"节点类型不在白名单: {type(node).__name__}")


def _as_tree(expr: str | ast.AST) -> ast.AST:
    """str → parse；AST → 仍要过一遍白名单（手工构造的 AST 不得绕过校验）。"""
    if isinstance(expr, str):
        return parse(expr)
    if not isinstance(expr, ast.AST):
        raise ExprError(f"表达式必须是 str 或 ast.AST，得到 {type(expr).__name__}")
    _validate(expr)
    return expr


def parse(expr: str) -> ast.AST:
    """解析并校验白名单；语法错误 / 任何违规节点 → ExprError。"""
    text = expr.strip()
    if not text:
        raise ExprError("表达式为空")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"语法错误: {expr!r}（{exc.msg}）") from exc
    _validate(tree)
    return tree


# ---------------------------------------------------------------------------
# 步行解释（无 eval/exec）
# ---------------------------------------------------------------------------


def _safe_div(a: Any, b: Any) -> Any:
    """除法：Series 参与时走 numpy 语义（除零 → inf/NaN）；纯标量除零手工映射。"""
    if not isinstance(a, pd.Series) and not isinstance(b, pd.Series):
        if b == 0:
            if a == 0:
                return float("nan")
            return math.copysign(math.inf, a)
    return a / b


_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: _safe_div,
}
_UNARY_OPS: dict[type, Callable[[Any], Any]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_call(node: ast.Call, df: pd.DataFrame, cols: dict[str, str]) -> pd.Series:
    assert isinstance(node.func, ast.Name)  # _validate_call 已保证
    fn = OPERATORS[node.func.id]
    arg0 = _eval(node.args[0], df, cols)
    if node.func.id in _WINDOW_OPS:
        n_node = node.args[1]
        assert isinstance(n_node, ast.Constant)
        n_val = n_node.value
        assert isinstance(n_val, int)  # _validate_window_arg 已保证字面量
        return fn(arg0, n_val)
    return fn(arg0)


def _eval_name(node: ast.Name, df: pd.DataFrame, cols: dict[str, str]) -> pd.Series:
    col = cols.get(node.id.lower())
    if col is None:
        raise ExprError(
            f"DataFrame 缺少基础变量列: {node.id}（实际列: {sorted(df.columns)}）"
        )
    return df[col]


def _eval(node: ast.AST, df: pd.DataFrame, cols: dict[str, str]) -> Any:
    """递归解释已校验的 AST；返回 pd.Series 或数值标量（标量在顶层再广播）。"""
    if isinstance(node, ast.BinOp):
        left = _eval(node.left, df, cols)
        right = _eval(node.right, df, cols)
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp):
        return _UNARY_OPS[type(node.op)](_eval(node.operand, df, cols))
    if isinstance(node, ast.Call):
        return _eval_call(node, df, cols)
    if isinstance(node, ast.Name):
        return _eval_name(node, df, cols)
    if isinstance(node, ast.Constant):
        return node.value
    raise ExprError(f"节点类型不在白名单: {type(node).__name__}")


def evaluate(expr: str | ast.AST, df: pd.DataFrame) -> pd.Series:
    """在单股 bars 上求值，返回与 df 同索引的 Series；df 缺引用列 → ExprError。"""
    tree = _as_tree(expr)
    cols = {str(c).lower(): str(c) for c in df.columns}
    body = tree.body if isinstance(tree, ast.Expression) else tree
    result = _eval(body, df, cols)
    if isinstance(result, pd.Series):
        return result
    return pd.Series(float(result), index=df.index)


# ---------------------------------------------------------------------------
# 复杂度度量
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Complexity:
    symbol_len: int  # 表达式字符串长度（去空白；AST 输入按 unparse 规范形）
    free_params: (
        int  # distinct 数值字面量个数（按 AST 常量值计：-5 记为 5，5 与 5.0 同值）
    )
    base_features: int  # distinct 基础变量个数（大小写归一）
    repeat_subtrees: int  # 同构子树重复次数（签名出现 k>1 次各计 k-1 求和，含叶子）
    depth: int  # AST 最大深度（叶子计 1，每嵌套一层 +1，不含 Expression 根）


def _signature(node: ast.AST) -> tuple:
    """子树规范签名：结构 + 算子 + 变量（小写归一）+ 常量值。"""
    if isinstance(node, ast.Expression):
        return _signature(node.body)
    if isinstance(node, ast.BinOp):
        return (
            "bin",
            type(node.op).__name__,
            _signature(node.left),
            _signature(node.right),
        )
    if isinstance(node, ast.UnaryOp):
        return ("un", type(node.op).__name__, _signature(node.operand))
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name)
        return ("call", node.func.id, tuple(_signature(a) for a in node.args))
    if isinstance(node, ast.Name):
        return ("name", node.id.lower())
    if isinstance(node, ast.Constant):
        return ("const", node.value)
    raise ExprError(f"节点类型不在白名单: {type(node).__name__}")


def _depth(node: ast.AST) -> int:
    if isinstance(node, ast.BinOp):
        return 1 + max(_depth(node.left), _depth(node.right))
    if isinstance(node, ast.UnaryOp):
        return 1 + _depth(node.operand)
    if isinstance(node, ast.Call):
        return 1 + max(_depth(a) for a in node.args)
    return 1  # Name / Constant 叶子


def _walk_exprs(node: ast.AST):
    """只步行表达式位置的节点（ast.walk 会混入 Call.func / Add / Load 等非表达式节点）。"""
    yield node
    if isinstance(node, ast.BinOp):
        yield from _walk_exprs(node.left)
        yield from _walk_exprs(node.right)
    elif isinstance(node, ast.UnaryOp):
        yield from _walk_exprs(node.operand)
    elif isinstance(node, ast.Call):
        for a in node.args:
            yield from _walk_exprs(a)


def complexity(expr: str | ast.AST) -> Complexity:
    """计算表达式的复杂度度量（先过白名单校验，违规抛 ExprError）。"""
    tree = _as_tree(expr)
    text = expr if isinstance(expr, str) else ast.unparse(tree)
    body = tree.body if isinstance(tree, ast.Expression) else tree
    consts: set[Any] = set()
    names: set[str] = set()
    sigs: collections.Counter[tuple] = collections.Counter()
    for node in _walk_exprs(body):
        if isinstance(node, ast.Constant):
            consts.add(node.value)
        elif isinstance(node, ast.Name):
            names.add(node.id.lower())
        sigs[_signature(node)] += 1
    repeat = sum(k - 1 for k in sigs.values() if k > 1)
    return Complexity(
        symbol_len=len("".join(text.split())),
        free_params=len(consts),
        base_features=len(names),
        repeat_subtrees=repeat,
        depth=_depth(body),
    )


def violations(
    comp: Complexity,
    *,
    max_symbol_len: int = 300,
    max_free_params: int = 6,
    max_base_features: int = 6,
    max_repeat_subtrees: int = 8,
    max_depth: int = 12,
) -> list[str]:
    """阈值检查；返回违规描述列表，空列表 = 通过。"""
    out = []
    if comp.symbol_len > max_symbol_len:
        out.append(f"symbol_len={comp.symbol_len} > {max_symbol_len}（表达式过长）")
    if comp.free_params > max_free_params:
        out.append(
            f"free_params={comp.free_params} > {max_free_params}（数值字面量过多）"
        )
    if comp.base_features > max_base_features:
        out.append(
            f"base_features={comp.base_features} > {max_base_features}（基础变量过多）"
        )
    if comp.repeat_subtrees > max_repeat_subtrees:
        out.append(
            f"repeat_subtrees={comp.repeat_subtrees} > {max_repeat_subtrees}（重复子树过多）"
        )
    if comp.depth > max_depth:
        out.append(f"depth={comp.depth} > {max_depth}（嵌套过深）")
    return out
