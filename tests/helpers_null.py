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

**v0.335/v0.336 成簇校准**：collect_all 口径交易成簇 ⇒ C4 打乱粒度按簇
（`criteria_kit.cluster_ids`/`cluster_draw` 单源）；校准夹具必须含
**成簇用例**（簇结局相关——构造件单源 = ``clustered_layout``（信号
骨架：簇内 i 连续/簇间跳 1000/档按码旋转）+ ``cluster_noise_replay``
（同簇同 ret 的零假设重放））+ **逐笔打乱哨兵**（monkeypatch 换回逐笔
⇒ 校准必须失败）。实测：逐笔 15/40=37.5%（≈owner 模拟 38%）、整簇
2/40≈名义 5%。
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


# ---------------------------------------------------------------------------
# 成簇夹具构造件（v0.336，owner review——C4 打乱粒度按簇不按笔的校准原料，
# R42/R39 校准共用单源）
# ---------------------------------------------------------------------------


def clustered_layout(
    n_codes: int = 10,
    clusters_per_code: int = 6,
    cluster_size: int = 20,
    rot: int = 1,
) -> list[tuple[str, str, int, int]]:
    """成簇信号骨架：产出 ``(code, day, i, band)`` 四元组清单。

    - 簇内 i 连续（间隔 1 ≤ 5 ⇒ 同簇），簇间 i 跳 1000（强制分簇）——
      每码 ``clusters_per_code`` 簇 × ``cluster_size`` 信号；
    - ``band = (cl + rot·码序) mod 3`` 按码旋转——簇内同档 ⇒ 任何按档/
      因子水平的真实划分都 = 整簇归属，与整簇抽签的臂**可交换**（同源
      零假设）；各档簇数均衡（每码每档 2 簇）；
    - day 按全局序号 k 生成（周期 336 > 单码 120 ⇒ 码内唯一——(code,
      date) 键可用）。

    定稿记录（v0.335 返修）：9 变体（band 旋转 × 噪声 salt）扫描取
    rot=1 + ``cluster_noise_replay`` 默认 salt="v2"——R42 主测 5 种子
    0/5、40 种子实测 confirmed_pass 2/40≈名义 5%、逐笔哨兵 3/5
    decisive；同结构 rot=0/salt="" 版 40 种子 4/40（10%）且主测
    seed4 冲线——变体差异只是噪声实现，机制（整簇 vs 逐笔）不变。
    """
    out: list[tuple[str, str, int, int]] = []
    k = 0
    for c in range(n_codes):
        code = f"{c:06d}"
        for cl in range(clusters_per_code):
            band = (cl + rot * c) % 3
            for j in range(cluster_size):
                day = (
                    f"{2023 + (k // 336)}-{((k // 28) % 12) + 1:02d}-{(k % 28) + 1:02d}"
                )
                out.append((code, day, 1000 * cl + j, band))
                k += 1
    return out


def cluster_noise_replay(
    seed: int, salt: str = "v2"
) -> Callable[[list[dict], dict], list[dict]]:
    """成簇零假设重放：**簇结局相关**——结局按 (code, 簇id, params, seed,
    salt) 哈希抽 40% +0.06 / 60% −0.035，同簇全部交易同 ret，与分数/
    因子无关。逐笔打乱的臂把簇拆散 ⇒ q95 系统性偏低（本夹具实测：
    逐笔 15/40=37.5% ≈ owner 零假设模拟的 38%；整簇 2/40≈名义 5%——
    与 v0.324 bootstrap 改日簇是同一个问题）。salt 是夹具实现细节
    （定稿记录见 ``clustered_layout`` docstring）。"""
    from custos.research import criteria_kit as kit  # noqa: PLC0415

    def _replay(subset: list[dict], params: dict) -> list[dict]:
        clusters = kit.cluster_ids(subset)
        out = []
        for r, cid in zip(subset, clusters):
            key = f"{r['code']}|{cid}|{sorted(params.items())}|{seed}|{salt}"
            ret = NULL_WIN if _draw(key, NULL_P) else NULL_LOSE
            out.append(mk_trade(r["code"], r["date"], ret, r.get("score", 50.0)))
        return out

    return _replay
