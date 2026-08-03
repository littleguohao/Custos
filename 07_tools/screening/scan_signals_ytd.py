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

import backtest_factors as bt  # noqa: E402

FIRINGS = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    BASE / "06_logs" / "walkforward" / "firings_rk_2026YTD.json")
PIT = BASE / "01_data" / "fundamentals" / "pit_financials.jsonl"


def _pit_index(path):
    """code → [(notice_date, net_profit, ocf_ps, roe_waa)] 升序。同 code 多版本取最新公告。"""
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
                (r["notice_date"], r.get("net_profit"), r.get("ocf_ps"), r.get("roe_waa")))
    for c in idx:
        idx[c].sort()
    return idx


def _tier_you(idx, code, day) -> bool:
    evs = idx.get(code)
    if not evs:
        return False
    # bisect_right((day,)) 落在**同日公告之前**((day,) < (day, np, ...)),故 k 指向
    # 公告日 **严格早于** 信号日的最新一期 ⇒ 口径是「公告次日起可见」(偏严一档,无 look-ahead),
    # 与模块 docstring 的"信号日实际可见"和 launch_point_study 默认 --pit-visible-same-day=off 一致。
    # (2026-08-03 修:此处原注释写的是"公告当日即可见,偏松一档",与实现正好相反。)
    # TODO(策略确认):此处**无财报时效上限**——三年不出报表的壳公司只要最后一期数字好看仍判"优"。
    #   财务侧已有 screening/financials.REPORT_MAX_AGE_DAYS(400天),此处是否同口径需策略拍板。
    k = bisect.bisect_right(evs, (day,)) - 1
    if k < 0:
        return False
    _, np_, ocf, roe = evs[k]
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
