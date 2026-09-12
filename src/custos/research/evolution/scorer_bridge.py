# -*- coding: utf-8 -*-
"""DSL 表达式 → backtest_factors SCORERS 的桥（进化闭环的最后一棒）。

进化引擎产出的候选因子是 DSL 表达式字符串；strategy_grid 三轴终审的因子轴
吃的是 backtest_factors 的 scorer 注册表。本模块是唯一翻译层：

- ``make_expr_scorer``：**构造期** ``expr_dsl.parse`` 白名单校验，ExprError
  直接上抛（fail-closed 在启动期，坏表达式不进回测循环），且 parse 只在
  构造期做一次 —— 返回的 scorer 闭包持有已校验 AST，逐调用直接
  ``evaluate(tree)``，不在回测热循环里重 parse 字符串；**计算期**永不
  raise —— 空切片 / 末行 NaN·inf / 任何求值异常一律返回 None（不参与排序），
  对齐 SCORERS/ENTRY_GATES 的不 raise 惯例（evaluate_trades 热循环里一个
  异常会炸掉整轮回测）。
- ``expr_scorer_key``：注册键 ``expr_<sha1(expr)[:8]>`` —— 进 --scorer 的
  choices、signals_signature 的 scorer 字段与输出标签，表达式差异由哈希覆盖。

suggestion 恒「可买」：表达式 scorer 是纯**选择器**（口径同 SCORERS 里
alpha101 的注释「仅作可排序因子,suggestion 恒『可买』,靠 entry_gate 约束
进场池」）——trade-sim 的进场判定要求 ``suggestion == "可买"``
（backtest_factors._check_entry），进场池由 --entry-filter 的 gate 约束。
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Callable

import pandas as pd

from custos.research.evolution.expr_dsl import evaluate, parse


def expr_scorer_key(expr: str) -> str:
    """注册键：``expr_<sha1(expr)[:8]>``（表达式差异由哈希覆盖，签名/标签同源）。"""
    return "expr_" + hashlib.sha1(expr.encode("utf-8")).hexdigest()[:8]


def make_expr_scorer(expr: str) -> Callable[..., dict | None]:
    """把 DSL 表达式包成 SCORERS 签名 ``scorer(df_slice, code) -> dict | None``。

    构造期 fail-closed（ExprError 上抛）且只 parse 一次；计算期闭包持有已
    校验 AST 直接求值（``evaluate`` 对 AST 输入仍过白名单但不重 parse），
    吞掉一切异常返回 None。

    三参形态 ``(df_slice, code, pre)``（v0.212，TODO #75）：pre 非 None 时
    查预计算全序列的末值点（每股只算一次，O(1) 点查询；只对「从第 0 根
    开始的前缀切片」有效——evaluate_trades 热循环恒如此，等价性钉测见
    tests/test_scorer_bridge.py::TestExprPrecompute）；pre 为 None 时回退
    逐切片全量重算（``evaluate()`` 信号扫描路径 gate_window>0 的非前缀切片
    走这里，行为与旧版逐位一致）。
    """
    tree = parse(expr)  # 白名单校验在构造期完成；ExprError 直接上抛

    def scorer(
        df_slice: pd.DataFrame, code: str = "", pre: dict | None = None
    ) -> dict[str, Any] | None:
        if df_slice is None or not len(df_slice):
            return None
        try:
            if pre is not None:
                vals = pre["expr_score"]
                last = float(vals[len(df_slice) - 1])
            else:
                last = float(evaluate(tree, df_slice).iloc[-1])
        except Exception:  # noqa: BLE001 —— 计算期异常一律吞掉返回 None
            return None
        if math.isnan(last) or math.isinf(last):
            return None
        return {
            "score": last,
            "suggestion": "可买",  # 纯选择器口径：进场池由 entry_gate 约束
            "aux": {"expr": expr},
            "components": {},
        }

    return scorer


def expr_scorer_precompute(expr: str) -> Callable[[pd.DataFrame], dict | None]:
    """逐股全序列预计算函数（backtest_factors ``_SCORER_PRECOMPUTE`` 口径）。

    等价性依据同 ``_precompute_kdj_j_series``：DSL 全部算子只看 ≤t 历史
    （rolling/shift 且 min_periods=n），前缀 ``df.iloc[:i+1]`` 上算出的末点
    与全序列第 i 点是**同一串浮点运算**，逐位相同。返回 None（异常）时
    scorer 走旧路径（pre=None 逐切片重算），行为与旧版逐位一致。
    """
    tree = parse(expr)  # 同一份 DSL 口径再 parse 一次（注册期一次，开销可忽略）

    def precompute(df: pd.DataFrame) -> dict | None:
        try:
            return {"expr_score": evaluate(tree, df).to_numpy(dtype=float)}
        except Exception:  # noqa: BLE001 —— 回退旧路径
            return None

    return precompute
