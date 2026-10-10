# -*- coding: utf-8 -*-
"""判据件单一来源（owner 方法论 review #7，v0.322）。

判据逻辑曾在 factor_exit_study / bear_regime_study / plan_rules_replay /
exit_campaign / score_filter_study **各抄一份**——「这里修了、那里没修」
是本周全部缺陷的来源（空池放行 / max-of-5 / 池填不满 / 重抽只改了
R41 没改 R39）。本模块收口四件，各终端只负责组装：

- **q95**（campaign 语义，owner 拍板主源）：空池 ⇒ None；len==1 ⇒ 该点；
  否则 inclusive 线性插值 [94]。分位随样本数**收敛**（它替代的「累积
  最大值」随样本数发散——棘轮，owner review 实测）。⚠️「小池是否可用」
  **不是本函数的责任**——调用点必须显式 min_pool 门（score_filter 旧
  `_q95` 的 n<20⇒None 已改为调用点 min_pool=20 门，行为逐位一致）。
- **verdict 四态**（C1 不过 ⇒ untested 优先——样本不足≠否定证据，
  v0.299/v0.301 族）：C2/C3 False 或 C4 confirmed_fail ⇒ falsified；
  C2∧C3∧C4 confirmed_pass ⇒ candidate；其余 ⇒ provisional（C4 池未满/
  indeterminate、C3 not_applicable——不放行不判死）。``c3_ok=None``
  （not_applicable，R40 v0.307 族）按「无证据≠否定也不放行」处理：
  falsified 分支只看 ``c3_ok is False``，candidate 分支要求
  ``c3_ok is True`` ⇒ 落 provisional。
- **assemble_c4_pool**（v0.317 族重抽）：重抽直至过门臂满 N 或评估达
  上限 ``cap_factor×N``。统计含义：候选本身也须过门（C2），零假设 =
  「同样过了门的随机臂」，两边条件对称——只改凑齐 N 的方式不改判据。
- **c4_state_of**：空池 ⇒ indeterminate（不放行，v0.297 族）；
  池 < min_pool ⇒ provisional；候选 Δ > q95 ⇒ confirmed_pass；
  否则 confirmed_fail。

rdd 相对门与 C5 判决已有单源（``strategy_grid.rdd_gate_ok`` /
``exit_c5_terminal.apply_c5``），本模块不重复造。

**簇构造单源（v0.335，owner review——C4 打乱粒度按簇不按笔）**：
collect_all 口径下同一只票连续几天出信号、分数相近、落同档、出场结果
几乎一样——交易**成簇**；逐笔打乱把簇拆散，零假设方差被低估、q95 偏低
（owner 零假设模拟：簇大小 20 时 confirmed_pass 15/40≈38%，名义 ≈5%）。
``cluster_ids`` 划簇、``cluster_draw`` 整簇抽签，score_tier_position（R42
C4 换档）与 factor_exit（R39 C4 分桶）共用——禁止各终端再抄一份。
"""

from __future__ import annotations

import random
import statistics
from datetime import date
from typing import Any, Callable, Optional

#: 合法结局四态（判定文本以各研究单元预注册为准）
VERDICTS = ("candidate", "falsified", "untested", "provisional")

#: 合法 C4 状态（provisional 池未满/indeterminate 池空=不放行不判死）
C4_STATES = ("confirmed_pass", "confirmed_fail", "provisional", "indeterminate")


def q95(pool: list[float]) -> Optional[float]:
    """合并随机分布的 95% 分位（inclusive 线性插值）——C4 的标尺（单一来源）。

    **分位随样本数收敛**；它替代的「累积最大值」随样本数发散（棘轮——
    同一个好基因组的 C4 通过率取决于第几批被发现：8 抽样 21.3% → 264
    抽样 1.6%，owner review 实测）。**但小池同样失真（反方向）**：池=8 时
    q95≈最大值，零假设过线率实测 13.76%（~5% 的 2.7 倍）——故各判据都有
    最小池护栏（min_pool，v0.269 族）：池满后零假设各批 ≈5% 假过线
    （实测 pool=160 → 5.35%），未满只记 provisional 不停手。
    """
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]
    return statistics.quantiles(pool, n=100, method="inclusive")[94]


def verdict_four_state(
    *,
    c1_ok: bool,
    c2_ok: Optional[bool],
    c3_ok: Optional[bool],
    c4_state: str,
) -> str:
    """四态结局映射（C1 不过 ⇒ untested **优先**——样本不足≠否定证据）。

    ``c2_ok``/``c3_ok`` 取 None = 不可评/不适用（C2 池未建、C3
    not_applicable）——既不 falsified 也不 candidate ⇒ provisional。
    ``c4_state`` 必须是 ``C4_STATES`` 之一。
    """
    if c4_state not in C4_STATES:
        raise ValueError(f"非法 C4 状态: {c4_state!r}（期望 {C4_STATES}）")
    if not c1_ok:
        return "untested"
    if c2_ok is False or c3_ok is False or c4_state == "confirmed_fail":
        return "falsified"
    if c2_ok is True and c3_ok is True and c4_state == "confirmed_pass":
        return "candidate"
    return "provisional"


def c4_state_of(
    pool: list[float], *, min_pool: int, plan_delta: Optional[float]
) -> str:
    """C4 状态机（单一来源）：空池 ⇒ indeterminate（不放行，v0.297 族）；
    池 < min_pool ⇒ provisional；候选 Δ > q95 ⇒ confirmed_pass；
    否则 confirmed_fail（plan_delta=None 按不过处理）。"""
    if not pool:
        return "indeterminate"
    if len(pool) < min_pool:
        return "provisional"
    if plan_delta is not None and plan_delta > q95(pool):
        return "confirmed_pass"
    return "confirmed_fail"


def assemble_c4_pool(
    n_random: int,
    evaluate_arm: Callable[[int], Optional[float]],
    *,
    cap_factor: int = 10,
) -> dict[str, Any]:
    """C4 随机臂池构造（v0.317 族重抽，单一来源）：逐臂评估直至**过门臂
    满 N** 或评估数达上限 ``cap_factor × N``。

    ``evaluate_arm(i)`` 评估第 i 条臂，返回池元素值（None = 不过门/不入池
    ——臂的构造与门判定归各终端，本函数只管「怎么凑齐 N」）。返回
    {pool, evaluated, gate_pass, max_arms, target_pool, gate_pass_rate}。
    """
    max_arms = cap_factor * n_random
    pool: list[float] = []
    evaluated = 0
    while len(pool) < n_random and evaluated < max_arms:
        v = evaluate_arm(evaluated)
        evaluated += 1
        if v is not None:
            pool.append(v)
    return {
        "pool": pool,
        "evaluated": evaluated,
        "gate_pass": len(pool),
        "max_arms": max_arms,
        "target_pool": n_random,
        "gate_pass_rate": (len(pool) / evaluated if evaluated else None),
    }


# ---------------------------------------------------------------------------
# 簇构造（v0.335，owner review——C4 打乱粒度按簇不按笔，单一来源）
# ---------------------------------------------------------------------------


def _gap_bars(a: dict, b: dict) -> Optional[float]:
    """两条记录的信号点间隔：都有 bar 序号 i ⇒ |i 差|（同 code 内 i 单调——
    优先用它，owner 指定）；任一缺 i ⇒ 日期差（日历日，退化兜底）；
    日期不可解析 ⇒ None（无法证明相邻）。"""
    ia, ib = a.get("i"), b.get("i")
    if ia is not None and ib is not None:
        return abs(float(ib) - float(ia))
    try:
        da = date.fromisoformat(str(a.get("date"))[:10])
        db = date.fromisoformat(str(b.get("date"))[:10])
    except ValueError:
        return None
    return abs((db - da).days)


def cluster_ids(recs: list[dict], *, max_gap: int = 5) -> list[int]:
    """簇划分（单一来源）：簇 = **同 code、信号点相邻（间隔 ≤ max_gap 根
    bar）的连续段**（max_gap=5 ≈ pct5_trail08 档持有期量级——「间隔 ≤
    持有期或 ≤5 根 bar」的具体落点，owner review 写死）。

    相邻判定用 bar 序号 i（同 code 内单调）；缺 i 退回日期差；间隔无法
    计算 ⇒ 不判邻（各自成簇）。返回与输入**等长同序**的簇 id 清单
    （0 起；同 code 内按 i/日期排序后顺序编号，跨 code 必不同簇）。
    """
    ids = [-1] * len(recs)
    by_code: dict[str, list[int]] = {}
    for idx, r in enumerate(recs):
        by_code.setdefault(str(r.get("code")), []).append(idx)
    cid = -1
    for code in by_code:
        idxs = by_code[code]
        idxs.sort(
            key=lambda j: (
                (
                    0,
                    float(recs[j]["i"]),
                )
                if recs[j].get("i") is not None
                else (1, str(recs[j].get("date")))
            )
        )
        prev: Optional[int] = None
        for j in idxs:
            if prev is None:
                cid += 1
            else:
                gap = _gap_bars(recs[prev], recs[j])
                if gap is None or gap > max_gap:
                    cid += 1
            ids[j] = cid
            prev = j
    return ids


def cluster_draw(clusters: list[int], choices: tuple, rng: random.Random) -> list:
    """整簇抽签（单一来源）：每簇**独立均匀**抽一个归属，同簇同签——
    各归属的笔数组成在臂间**自然波动**（这正是零假设该含的方差，v0.335
    owner：不要再强行对齐笔数）。rng 消耗 = 不同簇数（按出现顺序），
    确定性可复现。"""
    draw: dict[int, Any] = {}
    out: list = []
    for cid in clusters:
        if cid not in draw:
            draw[cid] = rng.choice(choices)
        out.append(draw[cid])
    return out


# ---------------------------------------------------------------------------
# CLI（诊断：本模块是库——打印单一来源清单）
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """criteria_kit 是判据件**库**（非研究工具）——打印单一来源清单
    （防各终端再抄一份；owner 方法论 review #7）。"""
    print(
        "criteria_kit 判据件单一来源（v0.322）：\n"
        "  q95（campaign 语义）/ verdict_four_state（C1 untested 优先）/\n"
        "  c4_state_of（空池 indeterminate/池未满 provisional）/\n"
        "  assemble_c4_pool（v0.317 族重抽至过门臂满 N 或上限 10×N）/\n"
        "  cluster_ids + cluster_draw（v0.335 簇构造——C4 打乱按簇不按笔）\n"
        "rdd 相对门=strategy_grid.rdd_gate_ok；C5 判决=exit_c5_terminal.apply_c5"
        "（均已有单源，勿再抄）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
