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


def perm_threshold(pool: list[float], alpha: float) -> Optional[float]:
    """置换检验判定线（单一来源，v0.385 审计 CK-2）：返回池内**第 k 大**值，
    k = max(1, ⌊α·(n+1)⌋)；候选 **> 判定线** ⇔ 经验 p = (1+#{臂≥候选})/(n+1) ≤ α
    （k≥1 时精确成立，假过线率 ≤ α）。n+1 < 1/α 时取不到 α ⇒ k=1 = 池最大值
    （最严，假过线率 1/(n+1)，由各判据 min_pool 护栏兜底）。
    替代旧的 inclusive 线性插值 q95：阈值位置 (n−1)·0.95 的期望秩偏低，零假设
    假过线 n=50 实测 6.74%、n=20 为 9.26%（非名义 5%）。空池 ⇒ None。"""
    vals = sorted((float(x) for x in pool if x == x), reverse=True)  # 剔 NaN
    if not vals:
        return None
    k = max(1, int(alpha * (len(vals) + 1) + 1e-9))
    return vals[min(k, len(vals)) - 1]


def q95(pool: list[float]) -> Optional[float]:
    """C4 标尺 = ``perm_threshold(pool, 0.05)``（v0.385 起；名字保留兼容各终端）。

    n=50 ⇒ 第 2 大值；n=200 ⇒ 第 10 大值。历史：v0.266 起是 inclusive 线性插值
    分位，小池过松（见 perm_threshold）；更早的「累积最大值」随样本数发散。
    """
    return perm_threshold(pool, 0.05)


def batch_threshold(pool: list[float], m: int) -> Optional[float]:
    """判定段攒批判定线 q(1−0.05/m)（攒批协议，m = 本批名册候选数）：
    ``perm_threshold(pool, 0.05/m)``。m<1 ⇒ ValueError（名册不可为空）。"""
    if m < 1:
        raise ValueError(f"批次候选数 m 须 ≥1，得 {m}")
    return perm_threshold(pool, 0.05 / m)


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
    # 有效池 = 剔 NaN/None 后（v0.406 审计 P1-1：perm_threshold 剔 NaN，护栏也须按有效长度）
    pool = [x for x in pool if x is not None and x == x]
    if not pool:
        return "indeterminate"
    if len(pool) < min_pool:
        return "provisional"
    q = q95(pool)
    if plan_delta is not None and q is not None and plan_delta > q:
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


def verdict_without_c4(
    *, c1_ok: bool, c2_ok: Optional[bool], c3_ok: Optional[bool]
) -> Optional[str]:
    """v0.466：C1~C3 已定时，若四态结局对 C4 的**每一种**状态都相同 ⇒ 返回该结局（随机臂池改变不了它）；
    否则 None（结局取决于池，须跑臂）。按 ``verdict_four_state`` 穷举，不另写一份映射。"""
    vs = {
        verdict_four_state(c1_ok=c1_ok, c2_ok=c2_ok, c3_ok=c3_ok, c4_state=s)
        for s in C4_STATES
    }
    return vs.pop() if len(vs) == 1 else None


def assemble_c4_pool_or_skip(
    n_random: int,
    evaluate_arm: Callable[[int], Optional[float]],
    *,
    skip_reason: Optional[str],
    enabled: bool = True,
    cap_factor: int = 10,
) -> dict[str, Any]:
    """v0.466 随机臂提前终止（单一来源）：``skip_reason`` 非 None（调用方已证明结局与池无关）且
    ``enabled`` ⇒ 不评估任何臂，返回空池（形态同 ``assemble_c4_pool``，C4 状态机落 indeterminate）；
    否则照常凑池。返回值多一个 ``early_stop`` 块供报告留痕。"""
    if skip_reason is not None and enabled:
        import sys  # noqa: PLC0415

        print(
            f"[early-stop] {skip_reason}——跳过随机臂（--no-early-stop 可强制跑满）",
            file=sys.stderr,
        )
        return {
            "pool": [],
            "evaluated": 0,
            "gate_pass": 0,
            "max_arms": cap_factor * n_random,
            "target_pool": n_random,
            "gate_pass_rate": None,
            "early_stop": {"skipped": True, "reason": skip_reason},
        }
    out = assemble_c4_pool(n_random, evaluate_arm, cap_factor=cap_factor)
    out["early_stop"] = {"skipped": False}
    return out


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
        # 簇键 = (code, 段)：v2 挖掘 A∪B 合并池里同码两段的 bar 序号 i 各自从 0 起，
        # 只按 code 分组会把相隔二十年的信号判成相邻（v0.385 审计 CK-5）
        by_code.setdefault(f"{r.get('code')}|{r.get('_seg', '')}", []).append(idx)
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
# 质量门（v0.501，owner 2026-11-09「看质量不看数量」+「更关注近年有效性」）
# ---------------------------------------------------------------------------

#: 资格底线：有效簇数 ≥ 30——只为让簇稳健下界算得出来，不是数量要求（R50 v0.477 口径推广）
MIN_CLUSTERS = 30
#: 双侧 95% 正态分位
Z95 = 1.96
#: 近年逐年独立读数年份 = 挖掘 B 的完整年（2026 只有 1~9 月 ⇒ 只描述不计）；
#: 2020/2021 属判定段，挖掘时不可读
RECENT_YEARS = (2022, 2023, 2024, 2025)
#: 可判年份中同向比例下限
YEAR_AGREE_FRAC = 0.75


def cluster_ratio_bounds(
    n_c: Any, h_c: Any, base: Optional[float], z: float = Z95
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """簇稳健比率界（单一来源；R50 v0.477 ``cluster_lift_bounds`` 上移并补上界）。

    精度 p = Σh/Σn；方差按簇残差 Σ(h_c − p·n_c)²·k/(k−1) / (Σn)²（比率估计的
    簇稳健三明治式）。返回 ``(p/base, (p−z·se)/base, (p+z·se)/base)``；
    基线缺 / 总数 0 ⇒ 全 None；簇数 < 2 ⇒ 界为 None（点估计照给）。
    ``base`` 视为常数（全体 / 同年同层精确基线，方差远小于信号侧）。"""
    import numpy as np  # noqa: PLC0415

    n_arr = np.asarray(n_c, dtype=float)
    h_arr = np.asarray(h_c, dtype=float)
    tot = float(n_arr.sum())
    if not base or tot <= 0:
        return None, None, None
    p = float(h_arr.sum()) / tot
    k = len(n_arr)
    if k < 2:
        return p / base, None, None
    se = float(np.sqrt(((h_arr - p * n_arr) ** 2).sum() * k / (k - 1)) / tot)
    return p / base, (p - z * se) / base, (p + z * se) / base


def cluster_sums(cids: Any, *cols: Any) -> tuple[Any, ...]:
    """按簇求和：``cids``（每行簇号）× 若干等长列 ⇒ 各列的逐簇和（只含出现过的簇）。"""
    import numpy as np  # noqa: PLC0415

    c = np.asarray(cids)
    _u, inv = np.unique(c, return_inverse=True)
    return tuple(
        np.bincount(inv, weights=np.asarray(x, dtype=float), minlength=len(_u))
        for x in cols
    )


def _weighted_margin(w: Any, ret: Any) -> Any:
    """加权 margin（向量化，B×N 权重）：胜率 − 1/(1+盈亏比)，与
    ``backtest_factors.summarize_trades`` + ``m2._margin`` 同口径（亏损 = ret<0，
    平局计入胜率分母、不计入盈亏均值）。无盈 / 无亏 ⇒ NaN。"""
    import numpy as np  # noqa: PLC0415

    r = np.asarray(ret, dtype=float)
    win, loss = r > 0, r < 0
    n = w.sum(axis=1)
    nw = w @ win.astype(float)
    nl = w @ loss.astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        aw = (w @ np.where(win, r, 0.0)) / nw
        al = (w @ np.where(loss, -r, 0.0)) / nl
        payoff = aw / al
        return nw / n - 1.0 / (1.0 + payoff)


def margin_delta_bounds(
    ret_a: Any,
    cid_a: Any,
    ret_b: Any,
    cid_b: Any,
    *,
    seed: Any,
    n_boot: int = 400,
) -> dict[str, Any]:
    """配对 margin 差（a − b）的簇 bootstrap 95% 区间（单一来源，R47 用）。

    两组交易来自**同一批信号**、只是出场不同 ⇒ 按信号簇整簇重抽、两边用同一组
    簇权重（配对，抵消信号侧方差）。``cid_*`` = 每笔交易所属信号簇号（两边同一
    编号体系）。返回 ``{delta, lo, hi, n_clusters}``；某侧无盈或无亏 ⇒ 对应量 None。"""
    import numpy as np  # noqa: PLC0415

    ca, cb = np.asarray(cid_a), np.asarray(cid_b)
    uniq = np.unique(np.concatenate([ca, cb]))
    k = len(uniq)
    out: dict[str, Any] = {"delta": None, "lo": None, "hi": None, "n_clusters": int(k)}
    if k == 0:
        return out
    ia = np.searchsorted(uniq, ca)
    ib = np.searchsorted(uniq, cb)
    one = np.ones((1, k))
    pa = _weighted_margin(one[:, ia], ret_a)[0]
    pb = _weighted_margin(one[:, ib], ret_b)[0]
    if np.isfinite(pa) and np.isfinite(pb):
        out["delta"] = float(pa - pb)
    if k < 2:
        return out
    rng = np.random.default_rng(seed)
    wc = rng.multinomial(k, np.full(k, 1.0 / k), size=n_boot).astype(float)
    d = _weighted_margin(wc[:, ia], ret_a) - _weighted_margin(wc[:, ib], ret_b)
    d = d[np.isfinite(d)]
    if len(d) >= max(20, n_boot // 2):
        out["lo"] = float(np.quantile(d, 0.025))
        out["hi"] = float(np.quantile(d, 0.975))
    return out


def yearly_stability(
    per_year: dict[Any, dict[str, Any]],
    *,
    null: float,
    years: tuple[int, ...] = RECENT_YEARS,
    min_clusters: int = MIN_CLUSTERS,
    frac: float = YEAR_AGREE_FRAC,
) -> dict[str, Any]:
    """近年逐年稳定性（单一来源；owner 2026-11-09）。

    ``per_year[y] = {point, lo, hi, n_clusters}``（键可为 int 或 str 年份）。规则写死：

    - **可判年** = 有效簇数 ≥ ``min_clusters`` 且点估计非空：要求可判年中
      「点估计 > null」（同向）的比例 ≥ ``frac``；
    - **任何一年**（含稀疏年）不得**显著反向**：95% 上界 < null ⇒ 不过；
    - 稀疏年 / 缺年不单独判方向——稀疏但高质量的信号不因「某年样本太少」出局
      （质量 > 数量）；可判年为 0 ⇒ 只看「无显著反向」，``evidence="none"`` 留痕。
    """
    detail: dict[str, Any] = {}
    judged = agree = 0
    reversed_years: list[int] = []
    for y in years:
        rd = per_year.get(y) or per_year.get(str(y)) or {}
        pt, hi, nc = rd.get("point"), rd.get("hi"), int(rd.get("n_clusters") or 0)
        is_judged = bool(pt is not None and pt == pt and nc >= min_clusters)
        rev = bool(hi is not None and hi == hi and float(hi) < null)
        same = bool(is_judged and pt is not None and float(pt) > null)
        judged += int(is_judged)
        agree += int(same)
        if rev:
            reversed_years.append(int(y))
        detail[str(y)] = {
            "point": pt,
            "lo": rd.get("lo"),
            "hi": hi,
            "n_clusters": nc,
            "judged": bool(is_judged),
            "same_direction": same if is_judged else None,
            "significantly_reversed": bool(rev),
        }
    dir_ok = judged == 0 or agree / judged >= frac
    return {
        "ok": bool(dir_ok and not reversed_years),
        "judged": judged,
        "agree": agree,
        "reversed_years": reversed_years,
        "evidence": "none" if judged == 0 else "judged",
        "years": detail,
        "rule": (
            f"近年 {years[0]}~{years[-1]} 逐年：可判年（簇 ≥{min_clusters}）同向（>{null:g}）"
            f"占比 ≥{frac:g}；任何一年 95% 上界 <{null:g} 即不过"
        ),
    }


# ---------------------------------------------------------------------------
# CLI（诊断：本模块是库——打印单一来源清单）
# ---------------------------------------------------------------------------


def main(_argv: Optional[list[str]] = None) -> int:
    """criteria_kit 是判据件**库**（非研究工具）——打印单一来源清单
    （防各终端再抄一份；owner 方法论 review #7）。"""
    print(
        "criteria_kit 判据件单一来源（v0.322）：\n"
        "  q95（campaign 语义）/ verdict_four_state（C1 untested 优先）/\n"
        "  c4_state_of（空池 indeterminate/池未满 provisional）/\n"
        "  assemble_c4_pool（v0.317 族重抽至过门臂满 N 或上限 10×N）/\n"
        "  cluster_ids + cluster_draw（v0.335 簇构造——C4 打乱按簇不按笔）/\n"
        "  cluster_ratio_bounds / margin_delta_bounds / yearly_stability（v0.501 质量门 + 近年逐年稳定性）\n"
        "rdd 相对门=strategy_grid.rdd_gate_ok；C5 判决=exit_c5_terminal.apply_c5"
        "（均已有单源，勿再抄）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
