# -*- coding: utf-8 -*-
"""共享零假设 / 真 edge 夹具（owner 方法论 review #4，v0.323；#1 返修 v0.330）。

零假设校准没有固定下来曾是两个线上缺陷的发现路径（R40 C4 的 7/8 假阳性、
R41 池子永远填不满——都是临时写纯噪声脚本才发现）。本夹具把它变成**每个
研究终端必须带的测试**：纯噪声下 ``confirmed_pass`` 比例 ≤ ~10%；植入真
edge 时能被识别出来。**新终端不过这两项不许合入。**

v0.330 返修（owner review #1）：原 ``null_replay`` 按子集序号交替 ± 是
**退化零假设**——ret 与 params/配置/抽样全无关，每个配置、每条随机臂读出
同一个 margin，max-of-230 与 max-of-5 是同一个数，**选择效应根本不出现**
（v0.306 的 7/8 假阳性 bug 放进旧夹具也能通过）。现改为**逐笔 iid 方差
噪声**：按 (code, i/date, params, stop_override) 哈希抽 40% +0.06 /
60% −0.035（均值 +0.003≈0；params/止损价进 key ⇒ 不同配置/臂抽不同噪声，
max-of-N 的选择效应才会真实发生）。哈希用 md5 而非内建 hash——后者
PYTHONHASHSEED 逐进程加盐，测试结果不可复现。

用法：各终端用自己的 warm/replay 注入件（套件夹具）+ 本模块的
``null_replay``/``edge_replay``/两个 assert。判定只看报告的
``criteria.C4.state``——与具体判据结构解耦。
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Optional

#: 纯噪声下 confirmed_pass 的允许上限（5 种子实测 ⇒ 须 0/5）
NOISE_CAP = 0.10
#: 零假设校准的默认种子集（确定性——挂了就复现）
NULL_SEEDS = (0, 1, 2, 3, 4)
#: 零假设噪声二值分布（40% +0.06 / 60% −0.035，均值 +0.003≈0）
NULL_WIN, NULL_LOSE, NULL_P = 0.06, -0.035, 0.4
#: 真 edge 二值分布（70% +0.09 / 30% −0.01，均值 +0.06——远大于噪声但
#: 保留胜负两侧，全胜组 payoff 无定义会把 margin 算成 None）
EDGE_WIN, EDGE_LOSE, EDGE_P = 0.09, -0.01, 0.7


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


def _draw(key: str, p: float) -> bool:
    """确定性哈希抽签（md5——内建 hash 对 str 逐进程加盐，不可复现）。"""
    h = hashlib.md5(key.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "little") / 2**32 < p


def _noise_key(rec: dict, params: dict, salt: str = "") -> str:
    """噪声 key：(code, i/date, params, stop_override, salt)——params 与
    止损价进 key 是选择效应出现的前提（不同配置/臂必须抽不同噪声）。"""
    sig = rec.get("sig") if isinstance(rec.get("sig"), dict) else {}
    return (
        f"{rec.get('code')}|{rec.get('i', rec.get('date'))}"
        f"|{sorted(params.items())}|{sig.get('stop_override')}|{salt}"
    )


def null_replay(subset: list[dict], params: dict) -> list[dict]:
    """纯噪声重放（v0.330 有方差化）：ret 与信号内容无关、但逐笔 iid
    方差——任何「版本间系统性 Δ」都来自选择效应/口径缺陷，confirmed_pass
    必须 ≤ 帽（退化交替 ± 版已废，见模块 docstring）。"""
    return [
        mk_trade(
            r["code"],
            r["date"],
            (NULL_WIN if _draw(_noise_key(r, params), NULL_P) else NULL_LOSE),
            r.get("score", 50.0),
        )
        for r in subset
    ]


def edge_replay(
    subset: list[dict],
    params: dict,
    *,
    is_edge: Callable[[dict, dict], bool],
) -> list[dict]:
    """植入真 edge：``is_edge(rec, params)`` 为真的交易用 edge 分布
    （70% +0.09 / 30% −0.01，均值 +0.06——保留胜负两侧防 payoff 无定义）、
    其余用零假设噪声——识别器必须报 confirmed_pass。"""
    out = []
    for r in subset:
        if is_edge(r, params):
            ret = (
                EDGE_WIN
                if _draw(_noise_key(r, params, salt="edge"), EDGE_P)
                else EDGE_LOSE
            )
        else:
            ret = NULL_WIN if _draw(_noise_key(r, params), NULL_P) else NULL_LOSE
        out.append(mk_trade(r["code"], r["date"], ret, r.get("score", 50.0)))
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
