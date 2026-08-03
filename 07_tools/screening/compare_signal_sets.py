# -*- coding: utf-8 -*-
"""研究版(reversal_k) vs live版(j_low≈KDJ_J_LOW) 信号擂台:同窗同源同规则。

对比:① 各档(可买候选/待做多/前哨)胜率与期望;② 全年 Top10 牛股各被哪套抓住。
口径备注:live 另有 POOL_ZHENDANG(震荡池低J)为 TQ 私有公式无法历史回放,
j_low 是可复现的最接近代理。
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
for p in (BASE / "07_tools", BASE / "07_tools" / "screening", BASE / "07_tools" / "local_tdx"):
    sys.path.insert(0, str(p))

import backtest_factors as bt  # noqa: E402
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("scan", str(BASE / "07_tools" / "screening" / "scan_signals_ytd.py"))
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)
import local_tdx_data  # noqa: E402

COST = 0.003
SETS = {
    "reversal_k(研究)": [BASE / "06_logs" / "walkforward" / "firings_rk_2026Jan_tdx.json",
                       BASE / "06_logs" / "walkforward" / "firings_rk_2026H1_tdx.json"],
    "j_low(live代理)": [BASE / "06_logs" / "walkforward" / "firings_jlow_2026H1_tdx.json"],
}


def classify(code, day, extra, pit, regime):
    sec_fav = bool(extra.get("f_sector_favorable"))
    fq = scan._tier_you(pit, code, day)
    mkt = regime.get(day) == "做多"
    bear = regime.get(day) == "空头"
    if fq and sec_fav and mkt:
        return "可买候选"
    if fq and sec_fav and not mkt:
        return "待0AMV做多"
    if fq and bear and not sec_fav:
        return "前哨"
    return None


def main() -> None:
    pit = scan._pit_index(BASE / "01_data" / "fundamentals" / "pit_financials.jsonl")
    regime = bt.load_amv_regime(since="2024-01-01")
    bars_cache: dict = {}

    def sim(code, day):
        if code not in bars_cache:
            try:
                df = local_tdx_data.get_ohlcv_table(code, count=2000)
                if df is not None and len(df):
                    df = df.copy()
                    df["date"] = df["date"].astype(str).str[:10]
                    bars_cache[code] = df.sort_values("date").reset_index(drop=True)
                else:
                    bars_cache[code] = None
            except Exception:  # noqa: BLE001
                bars_cache[code] = None
        df = bars_cache[code]
        if df is None:
            return None
        idx = df.index[df["date"] == day]
        if not len(idx):
            return None
        bbi = bt._bbi_series(df["close"].astype(float))
        tr = bt.simulate_b1_trade(df, int(idx[0]), bbi, stop_mode="pct", stop_pct=8.0)
        return {"ret": tr["ret"] - COST, "reason": tr["reason"]}

    summary: dict = {}
    best: dict = {}
    for name, files in SETS.items():
        seen = set()
        cohorts: dict = defaultdict(list)
        for fp in files:
            payload = json.loads(fp.read_text(encoding="utf-8"))
            for r in payload.get("records") or []:
                for d in (r.get("days") or []):
                    key = (r["code"], d[0])
                    if key in seen:
                        continue
                    seen.add(key)
                    extra = d[2] if len(d) > 2 and isinstance(d[2], dict) else {}
                    kind = classify(r["code"], d[0], extra, pit, regime)
                    if not kind:
                        continue
                    tr = sim(r["code"], d[0])
                    if tr:
                        cohorts[kind].append({"code": r["code"], "date": d[0], **tr})
        summary[name] = cohorts
        allt = [t for grp in cohorts.values() for t in grp]
        best[name] = sorted(allt, key=lambda t: t["ret"], reverse=True)[:10]

    for name in SETS:
        cohorts = summary[name]
        allt = [t for grp in cohorts.values() for t in grp]
        if not allt:
            continue
        rets = [t["ret"] for t in allt]
        wins = [r for r in rets if r > 0]
        print(f"\n=== {name}: 总信号 {len(allt)} ===")
        print(f"  胜率 {len(wins)/len(rets)*100:.1f}%  期望 {statistics.mean(rets)*100:+.2f}%/笔  "
              f"中位 {statistics.median(rets)*100:+.2f}%")
        for kind in ("可买候选", "待0AMV做多", "前哨"):
            grp = cohorts.get(kind, [])
            if grp:
                g = [t["ret"] for t in grp]
                gw = [r for r in g if r > 0]
                print(f"    {kind:<12} n={len(grp):<5} 胜率 {len(gw)/len(g)*100:.1f}%  "
                      f"期望 {statistics.mean(g)*100:+.2f}%/笔")

    # 牛股命中对比
    print("\n=== 各自 Top10(收益) ===")
    # 走统一 loader:缓存已改为带 generated_at 的新格式,直接 json.loads 会拿到
    # {"names": {...}} 这层壳而不是名称表本身(新旧格式兼容见 stock_names.load_cache)。
    import stock_names
    names, _meta = stock_names.load_cache()
    for name in SETS:
        tops = [(f"{t['code']} {names.get(t['code'], '')[:6]}({t['date']},{t['ret']*100:+.0f}%)" )
                for t in best[name]]
        print(f"  {name}: " + "、".join(tops))
    top_codes = {t["code"] for name in SETS for t in best[name][:5]}
    for c in sorted(top_codes):
        who = [name for name in SETS if any(t["code"] == c for t in best[name])]
        print(f"  {c} {names.get(c, '')[:6]} ← 被 {' / '.join(who)} 抓住")


if __name__ == "__main__":
    main()
