# -*- coding: utf-8 -*-
"""共享零假设 / 真 edge 夹具（owner 方法论 review #4，v0.323）。

零假设校准没有固定下来曾是两个线上缺陷的发现路径（R40 C4 的 7/8 假阳性、
R41 池子永远填不满——都是临时写纯噪声脚本才发现）。本夹具把它变成**每个
研究终端必须带的测试**：纯噪声下 ``confirmed_pass`` 比例 ≤ ~10%；植入真
edge 时能被识别出来。**新终端不过这两项不许合入。**

用法：各终端用自己的 warm/replay 注入件（套件夹具）+ 本模块的
``null_replay``/``edge_replay``/两个 assert。判定只看报告的
``criteria.C4.state``——与具体判据结构解耦。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

#: 纯噪声下 confirmed_pass 的允许上限（5 种子实测 ⇒ 须 0/5）
NOISE_CAP = 0.10
#: 零假设校准的默认种子集（确定性——挂了就复现）
NULL_SEEDS = (0, 1, 2, 3, 4)


def mk_trade(code: str, date: str, ret: float, score: float = 50.0) -> dict:
    """合成交易行（各套件 _trade_of 的统一版——键名与引擎读数对齐）。"""
    return {
        "code": code,
        "entry_date": date,
        "exit_date": date,
        "ret": ret,
        "reason": "stop",
        "holding": 5,
        "risk_frac": 0.05,
        "r_multiple": ret / 0.05,
        "score": score,
    }


def null_replay(subset: list[dict], params: dict) -> list[dict]:
    """纯噪声重放：ret 与 params/止损价/版本无关（按子集序号交替 ±）——
    任何「版本间有 Δ」都是选择效应假阳性，confirmed_pass 必须 ≤ 帽。"""
    return [
        mk_trade(
            r["code"], r["date"], 0.02 if j % 2 == 0 else -0.01, r.get("score", 50.0)
        )
        for j, r in enumerate(subset)
    ]


def edge_replay(
    subset: list[dict],
    params: dict,
    *,
    is_edge: Callable[[dict, dict], bool],
    edge_ret: tuple[float, float] = (0.08, -0.02),
    base_ret: tuple[float, float] = (0.01, -0.005),
) -> list[dict]:
    """植入真 edge：``is_edge(rec, params)`` 为真的交易用 edge_ret（大胜）、
    其余用 base_ret（贴零）——识别器必须报 confirmed_pass。"""
    out = []
    for j, r in enumerate(subset):
        hi, lo = edge_ret if is_edge(r, params) else base_ret
        out.append(
            mk_trade(
                r["code"], r["date"], hi if j % 2 == 0 else lo, r.get("score", 50.0)
            )
        )
    return out


def assert_noise_calibration(
    tag: str,
    run: Callable[[int], dict],
    *,
    seeds: tuple[int, ...] = NULL_SEEDS,
    cap: float = NOISE_CAP,
) -> None:
    """零假设校准（强制）：``run(seed)`` 逐种子跑研究，confirmed_pass 比例
    必须 ≤ cap。挂了 = 该终端的对照臂不是干净零假设（或门太松）。"""
    hits: list[bool] = []
    for s in seeds:
        rep = run(s)
        hits.append(rep["criteria"]["C4"]["state"] == "confirmed_pass")
    rate = sum(hits) / len(hits)
    assert rate <= cap, (
        f"{tag} 纯噪声下 confirmed_pass 率 {rate:.0%} > {cap:.0%}"
        f"（逐种子 {hits}）——零假设校准失败（R40 7/8 假阳性/R41 池填不满"
        "都是这类问题，owner 方法论 review #4）"
    )


def assert_edge_detected(
    tag: str, run: Callable[[int], dict], *, seed: int = 0
) -> None:
    """真 edge 可识别（强制）：植入 edge 时 C4 必须 confirmed_pass——
    挂了 = 判据太松到连真 edge 也捞不出（统计功效不足）。"""
    rep = run(seed)
    state = rep["criteria"]["C4"]["state"]
    assert state == "confirmed_pass", (
        f"{tag} 植入 edge 未被识别（C4={state}）——判据对照臂/门把真效应也"
        "吞了（owner 方法论 review #4）"
    )
