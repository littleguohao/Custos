# -*- coding: utf-8 -*-
"""打分基因组编译层（TODO #61 v1：打分系统进化 = 进化「分轴集合 × 权重」的基因组）。

核心洞察：**复合打分可以编译成一条普通 DSL 表达式**——每条腿裹
``TS_RANK(leg, K)`` 做个股自身历史分位归一（免截面依赖、量纲无关），再加权
求和::

    TS_RANK((l1),K) + w2*TS_RANK((l2),K) + ...   （weight=1 的腿省略 1* 前缀）

于是复合 scorer 直接复用 ``backtest_factors --scorer-expr`` / strategy_grid
``expr:`` 轴 / 双窗 / 灵敏度全部既有机制——本模块只有编译与权重格，零评估逻辑。

为什么增量在腿集合：四轮打分证伪（R22/R24/R29/R30）全是**旧轴调权**；本基因组的
基因组 = 「腿集合（新轴，来自 LLM 进化池或手工表达式）× 权重」。权重只在有界格上
搜（``weight_lattice``，R30 的 gcd 比例等价去重思路）——有界枚举是多重比较税的
上限，防「权重连续空间瞎搜」再死一次。

尺度不变性（比例等价去重的正确性，同 R30 论证）：复合分 = Σ w_i·r_i（r_i∈(0,1]）
是**纯线性加权和**；正权重同乘正常数 ⇒ 每股得分同比例缩放 ⇒ 截面排序与并列结构
逐位不变 ⇒ top_n 选中子集不变 ⇒ 交易层读数逐位不变。故同比例权重属同一排序等价
类，格上只评一次（代表 = 非零项除以 gcd 的最简整数比）。
"""

from __future__ import annotations

import itertools
import math
import random
from functools import reduce
from typing import Any, Sequence

from custos.research.evolution import expr_dsl
from custos.research.evolution.expr_dsl import MAX_WINDOW

#: 权重格默认档位：0=该腿关闭；1/2/3 相对权重（比例语义，非绝对量纲）。
DEFAULT_LEVELS: tuple[int, ...] = (0, 1, 2, 3)

#: 复合专用违规口径（v0.234，R34 r34_v1 生产机实据驱动的勘误）：
#: violations() 的默认上限（symbol_len 300 / depth 12）是**单因子**防过拟合
#: 简约门。复合是已过单因子门的腿的加权和——简约性在**腿的生产端**执行
#: （进化引擎 DSL 门 / 轨迹池入池判据 / 手工腿评审），复合级约束应该是
#: **腿数上限**（``--max-legs``）而非总长/总深：把单因子门原样套到复合上，
#: 会把合法的等权多腿复合误杀（r34_v1 实据：6 条长腿等权复合
#: symbol_len 448>300、depth 13>12，等权基线编译越界读数缺失；且 4 腿以上
#: 组合大面积编译失败，寻优被静默收窄到 ≤2~3 腿组合——预注册判据的
#: 「等权基准」被口径缺口架空）。
#: 1200/16 因此**不是简约门，是 AST 病态形态护栏**（防解析器/评估层被失控
#: 拼接打爆）：6 腿实据 448/13，1200/16 覆盖 ~12 条常长腿又拦得住病态输入。
#: repeat_subtrees/free_params 的复合豁免保留（同一 K、同一权重值按腿数重复
#: 是设计使然）；单因子 DSL 的 violations() 默认阈值**一律不动**。
COMPOSITE_MAX_SYMBOL_LEN = 1200
COMPOSITE_MAX_DEPTH = 16

#: 复合表达式的复杂度门（``violations()`` 的参数化覆盖；依据见上）。
_COMPOSITE_VIOLATION_LIMITS: dict[str, Any] = {
    "max_symbol_len": COMPOSITE_MAX_SYMBOL_LEN,
    "max_depth": COMPOSITE_MAX_DEPTH,
    "max_free_params": 10**9,  # 结构性豁免（见上）
    "max_repeat_subtrees": 10**9,  # 结构性豁免（见上）
}


def _fmt_weight(w: float) -> str:
    """权重的确定性文本形：整数值写整数（``2`` 非 ``2.0``），否则 repr 最短往返。"""
    return str(int(w)) if float(w).is_integer() else repr(float(w))


def _validate_legs_weights(
    legs: Sequence[str], weights: Sequence[float]
) -> tuple[float, ...]:
    """公共入参校验（fail-closed ValueError）；返回规范化后的 float 权重元组。"""
    if not isinstance(legs, (list, tuple)) or not legs:
        raise ValueError("legs 必须是非空表达式清单")
    if not isinstance(weights, (list, tuple)) or len(weights) != len(legs):
        raise ValueError(
            f"weights 数量必须与 legs 一致（{len(legs)}），实际 "
            f"{0 if not isinstance(weights, (list, tuple)) else len(weights)}"
        )
    out: list[float] = []
    for i, w in enumerate(weights):
        if isinstance(w, bool) or not isinstance(w, (int, float)):
            raise ValueError(f"weights[{i}] 必须是非负数值，得到 {w!r}")
        fw = float(w)
        if math.isnan(fw) or math.isinf(fw) or fw < 0:
            raise ValueError(f"weights[{i}] 必须是非负有限数值，得到 {w!r}")
        out.append(fw)
    if not any(out):
        raise ValueError("weights 全零：复合没有腿，拒收")
    return tuple(out)


def _composite_term(leg: str, w: float, rank_window: int) -> str:
    """单腿编译项：``TS_RANK((leg),K)``；**weight=1 省略 ``1*`` 前缀**（v0.234）。

    数值语义逐位不变：IEEE 乘 1.0 是精确恒等（``1*x ≡ x``，无舍入），省略
    前缀的产物与带前缀产物逐位一致（表达式级对拍钉测钉住）；收益是每条
    weight=1 的腿省 2 字符 + 1 层 AST 深度——多腿等权复合的 symbol_len/depth
    主门压力直接下降（R34 r34_v1 等权基线越界的成因之一）。
    """
    body = f"TS_RANK(({leg}),{rank_window})"
    return body if w == 1.0 else f"{_fmt_weight(w)}*{body}"


def compile_composite(
    legs: list[str], weights: list[float], *, rank_window: int = MAX_WINDOW
) -> str:
    """把「腿集合 × 权重」编译成一条 DSL 复合打分表达式。

    校验（全部 fail-closed ValueError）：

    - legs 逐条过 ``expr_dsl.parse``（白名单合法性；第几条非法在报错里指明）；
    - weights 非负数值、非 bool、非 NaN/inf、不全零、数量与 legs 一致；
    - ``rank_window`` 正整数（非 bool）且 ≤ ``MAX_WINDOW``；
    - 产物再过一次 ``parse`` + ``violations``（``_COMPOSITE_VIOLATION_LIMITS``
      口径：symbol_len 1200 / depth 16 是 AST 病态形态护栏，repeat/free_params
      结构性豁免——政策依据见模块常量 ``COMPOSITE_MAX_SYMBOL_LEN`` 的注释）；
      单因子 DSL 的 violations 默认阈值（300/12）**不动**，只覆盖复合层。

    形态约定：``TS_RANK((leg),K)`` 按 ``+`` 连接，weight≠1 加 ``{w}*`` 前缀
    （weight=1 省略，见 ``_composite_term``）；**w=0 的腿不编进表达式**
    （该轴关闭——lattice 里的 0 档位语义），legs 与 weights 的位置对应关系由
    调用方在落盘里保留（本函数只对正权重腿负责）。
    """
    ws = _validate_legs_weights(legs, weights)
    if (
        isinstance(rank_window, bool)
        or not isinstance(rank_window, int)
        or not 1 <= rank_window <= MAX_WINDOW
    ):
        raise ValueError(
            f"rank_window 必须是 [1, {MAX_WINDOW}] 的正整数，得到 {rank_window!r}"
        )
    parsed: list[tuple[str, float]] = []
    for i, (leg, w) in enumerate(zip(legs, ws)):
        if w == 0:
            continue  # 0 档 = 该腿关闭，不编进表达式
        if not isinstance(leg, str) or not leg.strip():
            raise ValueError(f"legs[{i}] 必须是非空表达式字符串，得到 {leg!r}")
        try:
            expr_dsl.parse(leg)
        except expr_dsl.ExprError as exc:
            raise ValueError(
                f"legs[{i}] 不是合法 DSL 表达式: {leg!r}（{exc}）"
            ) from exc
        parsed.append((leg.strip(), w))
    expr = "+".join(_composite_term(leg, w, rank_window) for leg, w in parsed)
    # 产物复核（构造即合法是论证，这里落成硬校验——复合层 bug 不得漏到评估层）：
    tree = expr_dsl.parse(expr)
    comp = expr_dsl.complexity(tree)
    bad = expr_dsl.violations(comp, **_COMPOSITE_VIOLATION_LIMITS)
    if bad:
        raise ValueError(f"复合表达式越界（{expr[:80]}…）: {'；'.join(bad)}")
    return expr


def weight_lattice(
    n_legs: int, levels: tuple[float, ...] = DEFAULT_LEVELS, *, max_combos: int = 64
) -> list[tuple[float, ...]]:
    """确定性权重格：枚举 → 比例等价去重 → 字典序 → 超 ``max_combos`` 截断。

    - 枚举 ``levels ** n_legs``，剔全零；
    - **比例等价去重**（gcd 最简整数比为代表，尺度不变性论证见模块 docstring，
      同 R30 score_combo_search 思路）：(2,2,0)≡(1,1,0)、(0,3,3)≡(0,1,1)；
    - 按字典序排序（确定性）；
    - 超 ``max_combos`` 截断，**保底策略**：单腿向量（(1,0,…)/(0,1,…)/…）与
      等权向量（(1,1,…,1)）**永远保留**（它们是对照臂：各单腿 = 新轴单独价值，
      等权 = 无权重寻优基准），其余按字典序补满——截断砍的是多腿混合的高档
      组合，对照基线不受 max_combos 影响。

    levels 必须是非负整数值（int 或整数值 float）——gcd 去重只在整数刻度上成立。
    返回 float 元组（类型统一；整数值）。
    """
    if isinstance(n_legs, bool) or not isinstance(n_legs, int) or n_legs < 1:
        raise ValueError(f"n_legs 必须是正整数，得到 {n_legs!r}")
    int_levels: list[int] = []
    for lv in levels:
        if isinstance(lv, bool) or not isinstance(lv, (int, float)):
            raise ValueError(f"levels 必须是非负整数刻度，得到 {lv!r}")
        flv = float(lv)
        if math.isnan(flv) or math.isinf(flv) or flv < 0 or not flv.is_integer():
            raise ValueError(f"levels 必须是非负整数刻度，得到 {lv!r}")
        int_levels.append(int(flv))
    if not any(int_levels):
        raise ValueError("levels 全零：格子上没有非零权重，拒收")
    if (
        isinstance(max_combos, bool)
        or not isinstance(max_combos, int)
        or max_combos < 1
    ):
        raise ValueError(f"max_combos 必须是正整数，得到 {max_combos!r}")

    seen: set[tuple[int, ...]] = set()
    for combo in itertools.product(int_levels, repeat=n_legs):
        if not any(combo):
            continue  # 全零 = 无腿，排除
        g = reduce(math.gcd, (x for x in combo if x))
        seen.add(tuple(x // g for x in combo))  # 最简整数比代表
    ordered = sorted(seen)  # 字典序（确定性）
    if len(ordered) <= max_combos:
        return [tuple(float(x) for x in c) for c in ordered]

    def _is_single(c: tuple[int, ...]) -> bool:
        return sum(1 for x in c if x) == 1  # canonical ⇒ 唯一的非零项必为 1

    def _is_equal(c: tuple[int, ...]) -> bool:
        return all(x == c[0] for x in c)  # canonical ⇒ 全 1

    must = [c for c in ordered if _is_single(c) or _is_equal(c)]
    must_set = set(must)
    rest = [c for c in ordered if c not in must_set]
    kept = must + rest[: max(0, max_combos - len(must))]
    return [tuple(float(x) for x in c) for c in kept]


def perturb_weights(
    weights: Sequence[float], *, pct: float = 0.5, rng: random.Random
) -> tuple[float, ...]:
    """灵敏度臂：每维独立乘性扰动 ``w × (1 + U(-pct, +pct))``，负值截 0。

    确定性由调用方注入 seeded ``rng`` 保证（同 random_expr 的注入惯例）。
    pct ≤ 1 时正权重保持为正（因子 ≥ 1−pct > 0），截 0 只在 pct > 1 时才会
    实际触发（防御）；扰动产物全零 ⇒ ValueError（pct>1 的退化防御，正常不可达）。
    """
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct:
        raise ValueError(f"pct 必须是非负数值，得到 {pct!r}")
    ws = tuple(float(w) for w in weights)
    if not all(w >= 0 for w in ws) or not any(ws):
        raise ValueError("weights 必须非负且不全零")
    out = tuple(max(0.0, w * (1.0 + rng.uniform(-pct, pct))) for w in ws)
    if not any(out):
        raise ValueError(f"扰动产物全零（pct={pct}）：灵敏度臂无腿可评")
    return out


def baseline_arms(n_legs: int) -> dict[str, tuple[float, ...]]:
    """对照臂权重元组：``equal`` = 等权复合；``single_<i>`` = 第 i 条单腿（其余 0）。

    这些向量恒在 ``weight_lattice`` 的保底集里（截断不砍）——对照臂不需要额外
    评估预算，从格子的评估结果里按名字取出即可（驱动层
    ``score_evolution_study`` 就是这么做的）。

    ⚠️ **V0 对照（live 现行技术分）不是权重元组**：V0 由 enrich 的 factor_contrib
    明细重建（score_variants_study 口径），依赖资金意图/板块相位等 enrich 输入，
    不是 OHLCV 时序算子的 DSL 表达式，编不进本编译层——驱动层以独立臂处理。
    """
    if isinstance(n_legs, bool) or not isinstance(n_legs, int) or n_legs < 1:
        raise ValueError(f"n_legs 必须是正整数，得到 {n_legs!r}")
    arms: dict[str, tuple[float, ...]] = {"equal": tuple(1.0 for _ in range(n_legs))}
    for i in range(n_legs):
        arms[f"single_{i}"] = tuple(1.0 if j == i else 0.0 for j in range(n_legs))
    return arms
