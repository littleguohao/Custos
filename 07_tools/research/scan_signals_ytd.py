# -*- coding: utf-8 -*-
"""2026 年内信号扫描:对 firings(reversal_k 事件 + 板块相位)按 ⭐ 信号定义逐日分类。

信号定义(与 candidate_table ⭐ 一致,技术腿=reversal_k 事件已触发):
- 可买候选:市场(0AMV做多)+板块有利+基本面优(PIT as-of)+技术 = 四面共振
- 待0AMV做多:基本面优+板块+技术(差市场腿)
- 📡前哨(空头):基本面优+技术(板块/市场未到位)
基本面优 = PIT 台账中信号日**实际可见**的最新财报(net_profit>0 且 ocf_ps>0 且 roe_waa>0),
非当前快照——历史扫描无 look-ahead。⚠️ 未含 live 的 A/B 分层与风控硬封(那是完整管线),
本扫描是"信号事件层"回答:哪天、哪只、达几面。
"""
from __future__ import annotations

import bisect
import json
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "07_tools"))
sys.path.insert(0, str(BASE / "07_tools" / "screening"))
_FACTORS_DIR = str(Path(__file__).resolve().parents[1] / "factors")
if _FACTORS_DIR not in sys.path:
    sys.path.insert(0, _FACTORS_DIR)   # 因子层：见 factors/__init__.py

# ── research/ 与 screening/ 分家（2026-08-07）后的路径引导。
# 研究脚本要能同时导**自己的兄弟**（research/）与**生产链模块**（screening/）：
# 方向是研究依赖生产（回测要跑生产的因子与打分），反向为 0 ——
# 见 tests/test_architecture_layers.py。
for _p in (str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / "screening")):
    if _p not in sys.path:
        sys.path.insert(0, _p)



import backtest_factors as bt  # noqa: E402
from paths import DATA, LOGS  # noqa: E402
# 财报时效阈值走 financials 的**单一定义**,不在此二次定义——两处口径漂移会让同一只票
# 在 live 与回测里得到相反的基本面判定。
from financials import REPORT_MAX_AGE_DAYS, _parse_day  # noqa: E402


def _report_age_days(report_date, as_of) -> int | None:
    """报告期距信号日的天数;任一侧无法解析 → None(交调用方按保守处理)。"""
    d = _parse_day(report_date)
    ref = _parse_day(as_of)
    if d is None or ref is None:
        return None
    return (ref - d).days

FIRINGS = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    LOGS / "walkforward" / "firings_rk_2026YTD.json")
PIT = DATA / "fundamentals" / "pit_financials.jsonl"


def _pit_index(path):
    """code → [(notice_date, report_date, net_profit, ocf_ps, roe_waa)] 升序。

    **notice_date 必须留在元组首位**：``idx[c].sort()`` 与 ``bisect_right(evs, (day,))``
    都依赖它作为排序/比较键，换位会静默破坏 as-of 语义。report_date 是后加的（供
    :func:`_tier_you` 做财报时效判定），旧台账缺该字段时置 ""。
    """
    idx: dict[str, list] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("notice_date") and r.get("net_profit") is not None:
            idx.setdefault(r["code"], []).append(
                (r["notice_date"], str(r.get("report_date") or ""),
                 r.get("net_profit"), r.get("ocf_ps"), r.get("roe_waa")))
    for c in idx:
        idx[c].sort()
    return idx


def _tier_you(idx, code, day, max_age_days: int = REPORT_MAX_AGE_DAYS) -> bool:
    """信号日 ``day`` 当时可见的最新一期财报是否达"基本面优"。

    PIT 语义：``bisect_right((day,))`` 落在**同日公告之前**（``(day,) < (day, rpt, ...)``），
    故 k 指向公告日 **严格早于** 信号日的最新一期 ⇒ 口径是「公告次日起可见」（偏严一档，
    无 look-ahead），与模块 docstring 的"信号日实际可见"和 launch_point_study 默认
    ``--pit-visible-same-day=off`` 一致。
    （2026-08-03 修：此处原注释写的是"公告当日即可见，偏松一档"，与实现正好相反。）

    财报时效（2026-08-03 加，审计 E11 的另一半）：报告期距信号日超过 ``max_age_days``
    即不算优。此前**没有任何时效上限**——一家 2023-04 出了最后一期财报、之后再没披露的
    公司，2026 年的任何信号日都会取到 2023 年那期，只要数字好看就判"基本面优"。这比
    live 侧（financials）更危险：那边用当前快照，这边服务跨年历史回测，陈旧数据命中机会
    成倍增加，且污染的是「哪天几面共振」这类会变成上线入场门槛的研究结论。

    口径与 ``financials.REPORT_MAX_AGE_DAYS`` **同一份定义**（按 report_date 而非
    notice_date）：报告期衡量"数据本身多老"，且不被补披露/更正公告掩盖——同一报告期若
    出现新的 notice_date，按公告日算会让 age 变小，反而掩盖数据陈旧。
    ``max_age_days=0`` 关闭该检查。报告期缺失/无法解析时返回 False（不给"优"，保守）。
    """
    evs = idx.get(code)
    if not evs:
        return False
    k = bisect.bisect_right(evs, (day,)) - 1
    if k < 0:
        return False
    _notice, rpt, np_, ocf, roe = evs[k]
    if max_age_days:
        age = _report_age_days(rpt, day)
        if age is None or age > max_age_days:
            return False
    return bool(np_ and np_ > 0 and ocf is not None and ocf > 0 and roe is not None and roe > 0)


def main() -> None:
    payload = json.loads(FIRINGS.read_text(encoding="utf-8"))
    recs = payload.get("records") or []
    pit = _pit_index(PIT)
    regime = bt.load_amv_regime(since="2024-01-01")   # 状态机粘滞,起点须远早于扫描窗

    per_day: dict[str, dict] = defaultdict(lambda: {"可买候选": [], "待0AMV做多": [], "前哨": [], "total": 0})
    for r in recs:
        for d in (r.get("days") or []):
            day = d[0]
            extra = d[2] if len(d) > 2 and isinstance(d[2], dict) else {}
            sec_fav = bool(extra.get("f_sector_favorable"))
            fq = _tier_you(pit, r["code"], day)
            mkt = regime.get(day) == "做多"
            bear = regime.get(day) == "空头"
            pd_ = per_day[day]
            pd_["total"] += 1
            if fq and sec_fav and mkt:
                pd_["可买候选"].append(r["code"])
            elif fq and sec_fav and not mkt:
                pd_["待0AMV做多"].append(r["code"])
            elif fq and bear and not sec_fav:
                pd_["前哨"].append(r["code"])

    print(f"扫描 {len(recs)} 股 | 信号日 {len(per_day)} 天")
    print(f"{'日期':<12}{'reversal_k':>10}{'可买候选':>10}{'待0AMV做多':>12}{'📡前哨':>10}  明细")
    for day in sorted(per_day):
        pd_ = per_day[day]
        detail = ""
        for k in ("可买候选", "待0AMV做多", "前哨"):
            if pd_[k]:
                detail += f" {k}={','.join(pd_[k][:6])}"
        print(f"{day:<12}{pd_['total']:<10}{len(pd_['可买候选']):<10}{len(pd_['待0AMV做多']):<12}{len(pd_['前哨']):<10}{detail}")


if __name__ == "__main__":
    main()
