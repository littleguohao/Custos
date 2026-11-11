# -*- coding: utf-8 -*-
"""0AMV 背景信息（1800 报告头部，v0.513，owner 2026-11-11）：当前 0AMV 段 × 上证位置 × R51 挖掘 B 段参考数。

**只描述，不进任何判定**：不改 regime、不改仓位、不改买卖。数字口径与 R51（``research/amv_regime_study``）
一致——生产代码不得 import 研究代码，故本模块独立实现同一口径，参考数写成常量并由钉测对账
``governance/research/results/R51/r51_v1.json``（研究结论变了 ⇒ 钉测红 ⇒ 同步更新）。

口径（同 R51）：
- 段 = 做多 ⇄ 空头 翻转日起到下次翻转前；「第 k 天」含翻转日。状态从台账 confirmed 记录首日起重放
  （>+4% 做多、<−2.3% 空头、其间粘滞，阈值取 ``amv_state`` 单源）。
- 0AMV 水平 = 逐日增量连乘；段内幅度以翻转日为基准（翻转日自身的增量已计入基准）。
- 上证（999999）收盘；翻转日位置 = 收盘距前 60 日（含当日）最低（做多）/ 最高（空头）收盘的幅度。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from custos.pipeline.market_timing.amv_state import AMV_LONG_PCT, AMV_SHORT_PCT

LONG, SHORT = "做多", "空头"
POS_LOOKBACK = 60

#: R51 挖掘 B 段（2022-01-01 ~ 2026-09-04）参考数。来源 results/R51/r51_v1.json（钉测对账）。
#: 段长单位交易日，幅度单位 %，比率为 0~1。位置档 n<5 只报 n，不报比例。
R51_B: dict[str, dict[str, Any]] = {
    "up": {
        "n_flips": 23,
        "duration": {"p50": 16, "p75": 28},
        "amv_fav": {"p50": 10.3717, "p75": 27.9585},
        "idx_fav": {"p50": 3.5261, "p75": 4.5749},
        "idx_adv": {"p50": -0.2568, "p25": -1.4144},
        "rev": {5: 0.1304, 10: 0.3913, 20: 0.6957},
        "pos_tiers": (
            (None, 5.0, "<5%", 15, 0.4667),
            (5.0, 10.0, "5~10%", 3, 0.6667),
            (10.0, 20.0, "10~20%", 4, 0.0),
            (20.0, None, "≥20%", 1, 0.0),
        ),
    },
    "down": {
        "n_flips": 23,
        "duration": {"p50": 21, "p75": 42},
        "amv_fav": {"p50": -12.6828, "p25": -22.1693},
        "idx_fav": {"p50": -2.7273, "p25": -5.4456},
        "idx_adv": {"p50": 1.8228, "p75": 4.1546},
        "rev": {5: 0.1739, 10: 0.2609, 20: 0.4783},
        "pos_tiers": (
            (-5.0, None, ">−5%", 11, 0.0),
            (-10.0, -5.0, "−5~−10%", 9, 0.6667),
            (-20.0, -10.0, "−10~−20%", 3, 0.0),
            (None, -20.0, "≤−20%", 0, None),
        ),
    },
}
MIN_TIER_N = 5


def ledger_confirmed(lines: list[str]) -> list[dict[str, Any]]:
    """台账行 → 逐日 confirmed 增量（同研究 ``_amv_ledger_records``：非 superseded，同日取 recorded_at 最晚）。"""
    latest: dict[str, tuple[str, float]] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            d = str(r.get("date") or "")
            pct = r.get("amv_change_pct")
            if (
                r.get("quality") != "confirmed"
                or r.get("superseded")
                or pct is None
                or len(d) != 10
            ):
                continue
            ts = str(r.get("recorded_at", ""))
            if d not in latest or ts >= latest[d][0]:
                latest[d] = (ts, float(pct))
        except (ValueError, TypeError, AttributeError):
            continue
    return [{"date": d, "chg": v} for d, (_, v) in sorted(latest.items())]


def replay(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐日 {date, chg, state}（起始中性，粘滞）。"""
    state = "中性"
    out = []
    for r in records:
        v = r["chg"]
        if v > AMV_LONG_PCT:
            state = LONG
        elif v < AMV_SHORT_PCT:
            state = SHORT
        out.append({"date": r["date"], "chg": v, "state": state})
    return out


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0


def _tier(x: float, tiers: tuple) -> tuple:
    for t in tiers:
        lo, hi = t[0], t[1]
        if (lo is None or x >= lo) and (hi is None or x < hi):
            return t
    return tiers[-1]


def build(
    day: str, records: list[dict[str, Any]], index_close: list[tuple[str, float]]
) -> Optional[dict[str, Any]]:
    """截至 ``day`` 的当前段读数；无翻转 / 上证缺翻转日 ⇒ None。``index_close`` = [(date, close)] 升序。"""
    days = [r for r in replay(records) if r["date"] <= day]
    if not days or days[-1]["state"] not in (LONG, SHORT):
        return None
    p = len(days) - 1
    while p > 0 and days[p - 1]["state"] == days[-1]["state"]:
        p -= 1
    if p == 0:
        return None  # 台账首段：无翻转日
    up = days[-1]["state"] == LONG
    lv, cur = [], 100.0
    for r in days[p:]:
        cur *= 1.0 + r["chg"] / 100.0
        lv.append(cur)
    idx = [(d, c) for d, c in index_close if d <= day]
    pos = {d: i for i, (d, _c) in enumerate(idx)}
    flip_d = days[p]["date"]
    if flip_d not in pos or not idx:
        return None
    k = pos[flip_d]
    seg_close = [c for _d, c in idx[k:]]
    base = idx[k][1]
    window = [c for _d, c in idx[max(0, k - POS_LOOKBACK + 1) : k + 1]]
    flip_pos = _pct(base, min(window)) if up else _pct(base, max(window))
    return {
        "state": days[-1]["state"],
        "dir": "up" if up else "down",
        "flip_date": flip_d,
        "day_k": len(days) - p,
        "ledger_last": days[-1]["date"],
        "amv_since": _pct(lv[-1], lv[0]),
        "amv_fav": _pct(max(lv), lv[0]) if up else _pct(min(lv), lv[0]),
        "idx_since": _pct(seg_close[-1], base),
        "idx_fav": _pct(max(seg_close), base) if up else _pct(min(seg_close), base),
        "flip_pos": flip_pos,
    }


def render(ctx: dict[str, Any], regime: str) -> list[str]:
    """报告行（markdown 引用块）。所有比较只陈述位置，不给动作。"""
    ref = R51_B[ctx["dir"]]
    up = ctx["dir"] == "up"
    k = ctx["day_k"]
    dur = ref["duration"]
    if k <= dur["p50"]:
        dur_txt = f"未到 B 段中位段长 {dur['p50']} 天"
    elif k <= dur["p75"]:
        dur_txt = f"已过中位 {dur['p50']} 天、未到 p75 {dur['p75']} 天"
    else:
        dur_txt = f"已超 p75 {dur['p75']} 天（B 段 3/4 的{ctx['state']}段比这短）"
    af, ifav = ref["amv_fav"], ref["idx_fav"]
    q2 = "p75" if up else "p25"
    tier = _tier(ctx["flip_pos"], ref["pos_tiers"])
    pos_name = "距前 60 日最低" if up else "距前 60 日最高"
    tier_txt = (
        f"B 段同档 10 日内翻回 {tier[4]:.0%}（n={tier[3]}）"
        if tier[3] >= MIN_TIER_N and tier[4] is not None
        else f"B 段同档样本 n={tier[3]}，不足 {MIN_TIER_N} 不报比例"
    )
    back = "空头" if up else "做多"
    rev = ref["rev"]
    rev_txt = "、".join(f"{n} 日内 {rev[n]:.0%}" for n in (5, 10, 20))
    if k <= 20:
        rev_now = f"当前第 {k} 天，仍在 20 日观察期内"
    else:
        rev_now = "已过 20 日观察期"
    lines = [
        f"> 0AMV 背景（R51 挖掘 B 段参考，**只描述、不进判定**）：{ctx['state']}第 **{k}** 天（{ctx['flip_date']} 翻{'多' if up else '空'}），{dur_txt}。",
        f">   - 段内 0AMV 累计 {ctx['amv_since']:+.1f}%（段内最{'高' if up else '低'} {ctx['amv_fav']:+.1f}%）；B 段{ctx['state']}段 0AMV 最{'多涨' if up else '多跌'}中位 {af['p50']:+.1f}%、{q2} {af[q2]:+.1f}%。",
        f">   - 上证自翻转日 {ctx['idx_since']:+.1f}%（段内最{'高' if up else '低'} {ctx['idx_fav']:+.1f}%）；B 段中位 {ifav['p50']:+.1f}%、{q2} {ifav[q2]:+.1f}%。",
        f">   - 翻转日上证{pos_name} {ctx['flip_pos']:+.1f}%（{tier[2]} 档），{tier_txt}。",
        f">   - B 段翻{'多' if up else '空'}后翻回{back}：{rev_txt}；{rev_now}。",
    ]
    if regime and regime != ctx["state"]:
        lines.insert(
            1,
            f">   - ⚠️ 台账重放状态（{ctx['state']}，截至 {ctx['ledger_last']}）与报告有效状态（{regime}）不一致，背景信息仅供参考。",
        )
    return lines


def context_lines(
    day: str,
    regime: str,
    *,
    ledger_lines: Optional[Callable[[], list[str]]] = None,
    index_close: Optional[Callable[[], list[tuple[str, float]]]] = None,
) -> list[str]:
    """1800 报告入口：**旁路隔离**——任何异常只返回一行降级说明，绝不打死主报告。"""
    try:
        if ledger_lines is None:
            from custos.pipeline.market_timing import amv_state  # noqa: PLC0415

            def ledger_lines() -> list[str]:
                if not amv_state.LEDGER.exists():
                    return []
                return amv_state.LEDGER.read_text(encoding="utf-8").splitlines()

        if index_close is None:

            def index_close() -> list[tuple[str, float]]:
                from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

                df = local_tdx_data.get_ohlcv_table("999999", count=400)
                return sorted(
                    (str(d)[:10], float(c))
                    for d, c in zip(df["date"], df["close"])
                    if c == c
                )

        ctx = build(day, ledger_confirmed(ledger_lines()), index_close())
        if ctx is None:
            return ["> 0AMV 背景：当前段无翻转日或上证缺数据，未生成。"]
        return render(ctx, regime)
    except Exception as exc:  # noqa: BLE001 — 背景信息旁路，不影响主报告
        return [
            f"> 0AMV 背景：生成失败（{type(exc).__name__}），不影响本报告其余内容。"
        ]
