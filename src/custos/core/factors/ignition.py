# -*- coding: utf-8 -*-
"""点火族因子（ignition）—— 放量点火 / 回调缩量企稳 / B1 点火复合判定。

2026-08-20（v0.86，因子化批 C）从
`pipeline/screening/enrich_candidates.py` 迁入（check_ignition /
check_pullback_shrink + ZX_CROSS_RECENT/IGNITION_*/PULLBACK_* 常量段；
_b1 点火复合与知行近金叉判定原内联在 `_evidence_states`，抽成
`b1_ignition_hit` / `zx_recent_golden` 两个纯判定函数随本模块走）。
**行为零变化**：检测器函数体逐字未动；enrich_candidates 改为 import 调用
（`enrich_candidates.check_ignition` / `check_pullback_shrink` 仍是同一函数
对象，tests 的 `ec.check_ignition` monkeypatch 通道不变——见
test_j_gate_and_fit.TestOutsideGateWatchlist）。

## status 定档理由（candidate）

- `live_use=scorer` / `stage=release` 是**事实**：ignition / pullback_shrink /
  b1_ignition 是技术分 3 条打分腿（+4/+5/+8，score_candidates._ignition_score）
  的唯一生产者，并喂资金意图证据（capital_intent 的 ci_b1_ignition +3 /
  ci_ignition +1）；ignition.hit 还是候选表「门内提醒」（J≤13 且异动强，
  v0.89 起，取代门槛外观察区 watchlist_outside_gate）的两条判据之一。
- `NOT_FOR_LIVE={needs_work, untested}` 由
  `tests/test_factor_registry.py::test_needs_work_cannot_be_gate_or_scorer`
  机械禁止与 scorer 共存 ⇒ 合规候选只剩 active / candidate。
- `active` 语义是「已验证可用」——本轮只是**搬迁**（零行为变化），没有新增
  任何回测证据，IGNITION_*/PULLBACK_* 参数仍是「待回测」启发式 ⇒ 不能标 active。
- ⇒ 取 **candidate**（有依据未终审），阈值校准挂回测 TODO（#62）。
"""

from __future__ import annotations

from typing import Any, Optional

from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays  # noqa: E402

FACTOR: dict[str, Any] = {
    "id": "ignition",
    "name": "点火族（放量点火 / 回调缩量企稳 / B1 点火复合）",
    "kind": "pattern",
    # 见模块 docstring「status 定档理由」：scorer ⇒ 不能是 needs_work/untested；
    # 无回测证据 ⇒ 不能标 active；取 candidate 并挂回测 TODO。
    "status": "candidate",
    "evidence": "",
    "note": "v0.86 自 enrich_candidates 迁入（零行为变化）；ignition/pullback_shrink/"
    "b1_ignition 喂技术分 3 条打分腿（+4/+5/+8）+ 资金意图证据（ci_b1_ignition +3/"
    "ci_ignition +1）+ 门内提醒判据（v0.89 起，原门槛外观察区判据），参数待回测校准",
    "min_bars": 25,
    "live_use": "scorer",
    "stage": "release",
}

# --- 知行量价（good_b1 图集）与出货五方式 待回测参数 ---
ZX_CROSS_RECENT = 10  # 待回测：知行金叉"近N日"窗口
IGNITION_WINDOW = 10  # 待回测：放量点火扫描窗口（日）
IGNITION_VOL_RATIO = 1.5  # 待回测：点火量比（当日量/前5日均量）
IGNITION_MIN_GAIN = 3.0  # 待回测：点火单日涨幅%下限
PULLBACK_MIN_DROP = 3.0  # 待回测：距窗口高点回撤%下限
PULLBACK_VOL_RATIO = 0.85  # 待回测：回调段/上涨段均量上限
# v0.61（owner 定向，2026-08-15）：0.8 -> 0.85。正例 002074@2025-08-01 的
# 回调量比 0.849 被原阈值挡在门外（drop 7.73%、持 DKS 均满足）--B1 健康回调
# 的「缩量」不必苛求 <0.8。⚠️ 放宽会扩大命中面（全市场影响待复盘观察）。


def check_ignition(df) -> dict[str, Any]:
    """放量点火（good_b1 启动长阳）：前段缩量后出现放量收阳的启动K。

    命中条件（近 IGNITION_WINDOW 根内任一根 t）：量比(vol[t]/前5日均量) >= 1.5、
    收阳(close>open)、单日涨幅 >= IGNITION_MIN_GAIN，且启动前处于缩量
    （前5日均量 <= 更前5日均量）。
    """
    close, _, _, vol = _ohlcv_arrays(df)
    open_ = df["open"].astype(float).to_numpy()
    n = len(df)
    if n < 12:
        return {"hit": False, "available": False}
    hit_detail = None
    for t in range(max(11, n - IGNITION_WINDOW), n):
        base5 = vol[t - 5 : t].mean()
        if not base5:
            continue
        vr = float(vol[t] / base5)
        chg = (close[t] / close[t - 1] - 1) * 100 if close[t - 1] else 0.0
        is_bull = close[t] > open_[t]
        prev5 = vol[t - 10 : t - 5].mean()
        pre_contracted = (prev5 == 0) or (base5 <= prev5)
        if (
            vr >= IGNITION_VOL_RATIO
            and is_bull
            and chg >= IGNITION_MIN_GAIN
            and pre_contracted
        ):
            hit_detail = {
                "bars_ago": n - 1 - t,
                "vol_ratio5": round(vr, 3),
                "change_pct": round(chg, 2),
                "pre_contracted": bool(pre_contracted),
            }
            break
    return {"hit": hit_detail is not None, "available": True, "detail": hit_detail}


def check_pullback_shrink(df, dks_last: Optional[float] = None) -> dict[str, Any]:
    """回调缩量企稳（good_b1 回调段）：自窗口高点回撤 + 回调段缩量 + 收盘守多空线。

    上涨段由 b1_structure.rally_segment（MACD 平台 N 结构，v0.591）定位：距高点回撤 >= PULLBACK_MIN_DROP%，回调段均量 /
    上涨段均量 < PULLBACK_VOL_RATIO，且（无 DKS 时忽略）收盘 >= DKS。
    """
    close, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    if n < 30:  # v0.591 同 rally_segment 最少根数
        return {"hit": False, "available": False}
    from custos.core.factors.b1_structure import rally_segment  # noqa: PLC0415  v0.591 上涨段唯一定位

    seg = rally_segment(df)
    if seg is None:
        return {
            "hit": False,
            "available": True,
            "detail": {"reason": "无 MACD 平台上涨段"},
        }
    hi_pos, lo_pos = seg["i_high"], seg["i_low"]
    high = float(close[hi_pos])
    drop_pct = (1 - close[-1] / high) * 100 if high else 0.0
    run_vol = vol[lo_pos : hi_pos + 1]
    pull_vol = vol[hi_pos + 1 :]
    run_mean = float(run_vol.mean()) if len(run_vol) else 0.0
    pull_ratio = (
        (float(pull_vol.mean()) / run_mean)
        if (len(pull_vol) >= 2 and run_mean)
        else None
    )
    shrink = pull_ratio is not None and pull_ratio < PULLBACK_VOL_RATIO
    hold_dks = (dks_last is None) or (close[-1] >= dks_last)
    hit = bool(drop_pct >= PULLBACK_MIN_DROP and shrink and hold_dks)
    return {
        "hit": hit,
        "available": True,
        "detail": {
            "drop_from_high_pct": round(drop_pct, 2),
            "pullback_vol_ratio": round(pull_ratio, 3)
            if pull_ratio is not None
            else None,
            "hold_dks": bool(hold_dks),
        },
    }


# --- 回调缩量企稳 v2 候选（owner 2026-11-25：按原文「回调段成交量单调收缩，收盘守住 DKS 或前一有效低点」+ 当根不放量、K 线不大）---
PB2_MIN_DROP = 3.0  # 距高点回撤 ≥3%（同 v1）
PB2_DECAY = 0.75  # 量「一路缩」：回调段末 1/3 均量 ≤ 初 1/3 均量 × 0.75（完美 B1 10 例 0.40~0.71；逐日单调太严，10 例无一满足）
PB2_YIN_VOL = 0.9  # 当根是阴线时：量 < 前 5 日均量 × 0.9（owner #40 2.52、#41 0.96 判放量；完美 B1 阴线 0.61~0.89）
PB2_YANG_VOL = 1.5  # 当根是阳线时：量 < 前 5 日均量 × 1.5（完美 B1 阳线 0.47~1.17；缩量回调末端的小阳放一点量正常）
PB2_MAX_BODY = 4.0  # 当根实体 < 前收 × 4%（owner #42 5.2% 判 K 线大；完美 B1 0.3~3.7%）
PB2_PIVOT = 3  # 前一有效低点 = 上涨段内最后一个收盘分型低点（左右各 3 根）


def check_pullback_shrink_v2(df, dks_last: Optional[float] = None) -> dict[str, Any]:
    """回调缩量企稳 v2（候选，owner 2026-11-25；未接 live）。

    上涨段 = b1_structure.rally_segment（MACD 平台 N 结构，v0.591 唯一定位）；回调段 = 高点之后到当根（≥2 根）。
    成立 = 回撤 ≥ PB2_MIN_DROP% ∧ 回调段量一路缩（末 1/3 均量 ≤ 初 1/3 × PB2_DECAY）∧ 当根不放量（阴线 < 前 5 日均量 × PB2_YIN_VOL，
    阳线 < × PB2_YANG_VOL）∧ 当根实体 < PB2_MAX_BODY% ∧ 收盘守住 DKS 或前一有效低点。
    🔄 v1 的「回调段均量 / 上涨段均量 < 0.85」看不到当根放量与大 K（owner #40~#42），且平均量比较不等于原文的「单调收缩」。"""
    close, _, _, vol = _ohlcv_arrays(df)
    open_ = df["open"].astype(float).to_numpy()
    n = len(df)
    if n < 30:  # v0.591 同 rally_segment 最少根数
        return {"hit": False, "available": False}
    from custos.core.factors.b1_structure import rally_segment  # noqa: PLC0415  v0.591 上涨段唯一定位

    seg = rally_segment(df)
    if seg is None:
        return {
            "hit": False,
            "available": True,
            "detail": {"reason": "无 MACD 平台上涨段"},
        }
    hp, lo = seg["i_high"], seg["i_low"]
    pv = vol[hp + 1 :]
    if len(pv) < 2 or not close[hp]:
        return {"hit": False, "available": True, "detail": {"pullback_bars": len(pv)}}
    drop = (1 - close[-1] / close[hp]) * 100
    k = max(1, len(pv) // 3)
    head = float(pv[:k].mean())
    decay = float(pv[-k:].mean() / head) if head else None
    base5 = float(vol[-6:-1].mean())
    vr = float(vol[-1] / base5) if base5 else None
    yin = bool(close[-1] < open_[-1])
    body = abs(close[-1] - open_[-1]) / close[-2] * 100 if close[-2] else 0.0
    piv = [
        t
        for t in range(lo + PB2_PIVOT, hp - PB2_PIVOT + 1)
        if close[t] <= close[t - PB2_PIVOT : t + PB2_PIVOT + 1].min()
    ]
    prev_low = float(close[piv[-1]]) if piv else float(close[lo])
    hold = bool(
        (dks_last is not None and close[-1] >= dks_last) or close[-1] >= prev_low
    )
    conds = {
        "drop": bool(drop >= PB2_MIN_DROP),
        "decay": bool(decay is not None and decay <= PB2_DECAY),
        "bar_vol": bool(vr is not None and vr < (PB2_YIN_VOL if yin else PB2_YANG_VOL)),
        "bar_body": bool(body < PB2_MAX_BODY),
        "hold": hold,
    }
    return {
        "hit": all(conds.values()),
        "available": True,
        "conditions": conds,
        "detail": {
            "high_i": hp,
            "run_low_i": lo,
            "pullback_bars": len(pv),
            "drop_from_high_pct": round(drop, 2),
            "vol_decay": round(decay, 3) if decay is not None else None,
            "bar_vol_ratio5": round(vr, 3) if vr is not None else None,
            "bar_yin": yin,
            "bar_body_pct": round(body, 2),
            "prev_low": round(prev_low, 3),
        },
    }


def zx_recent_golden(zx: dict) -> bool:
    """知行金叉"近 N 日"（<= ZX_CROSS_RECENT）：B1 点火复合的知行腿。"""
    return bool(
        zx.get("available")
        and zx.get("qsx_gt_dks")
        and zx.get("days_since_golden_cross") is not None
        and zx["days_since_golden_cross"] <= ZX_CROSS_RECENT
    )


def b1_ignition_hit(
    j_low: bool,
    reversal_k: bool,
    pullback_shrink: dict,
    zx_recent_gold: bool,
    ignition: dict,
) -> bool:
    """B1 点火复合判定：(J低位 或 反转K) ∧ 回调缩量企稳 ∧ (知行近金叉 或 放量点火)。

    输入全部是上游已算好的中间态（base scalars / zhixing / 本模块两个检测器），
    本函数只做合取，不重算——单次计算语义与 enrich 内联时一致。
    """
    return bool(
        (j_low or reversal_k)
        and pullback_shrink.get("hit")
        and (zx_recent_gold or ignition.get("hit"))
    )


# v0.223（TODO #67 B4）：规范入口别名（同对象）。check_pullback_shrink 吃
# dks_last 上下文不进门面（见设计稿勘误）；live 调用面不变。
detect = check_ignition
