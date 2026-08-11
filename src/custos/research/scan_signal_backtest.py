# -*- coding: utf-8 -*-
"""扫描信号的实战回测:⭐ 信号(可买候选/待0AMV做多/前哨)按 B1 买卖规则逐笔模拟。

规则:信号日收盘进场;pct 8% 止损;站上 BBI 后连破 2 日止盈;往返成本 30bps。
数据源:tdx(覆盖到最新)。按信号档分组报胜率/期望/盈亏比/出场原因。
⚠️ 「待0AMV做多/前哨」在纪律上不是买点,列出仅作对照(若提前买会怎样)。
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict


from custos.research import backtest_factors as bt  # noqa: E402
from custos.core.paths import DATA, LOGS  # noqa: E402

# ⚠️ 2026-08-07：原本用 `spec_from_file_location` 按**文件路径**加载
# `scan_signals_ytd`，硬编码了 `src/custos/pipeline/screening/...` —— 研究脚本拆到
# `research/` 时这行直接 FileNotFoundError（被 `--help` 子进程冒烟抓到，
# 普通 import 即可；顺带去掉了「按路径加载」这第三种导入机制
# —— 它会为同一文件再造一个模块对象。
from custos.research import scan_signals_ytd as scan  # noqa: E402

from custos.datasource.local_tdx import local_tdx_data  # noqa: E402

FIRINGS = [
    LOGS / "walkforward" / "firings_rk_2026YTD.json",
    LOGS / "walkforward" / "firings_rk_2026H1_tdx.json",
]
COST = 0.003


def collect_signals() -> list[dict]:
    pit = scan._pit_index(DATA / "fundamentals" / "pit_financials.jsonl")
    regime = bt.load_amv_regime(since="2024-01-01")
    out = []
    for fp in FIRINGS:
        payload = json.loads(fp.read_text(encoding="utf-8"))
        for r in payload.get("records") or []:
            for d in r.get("days") or []:
                day = d[0]
                extra = d[2] if len(d) > 2 and isinstance(d[2], dict) else {}
                sec_fav = bool(extra.get("f_sector_favorable"))
                fq = scan._tier_you(pit, r["code"], day)
                mkt = regime.get(day) == "做多"
                bear = regime.get(day) == "空头"
                if fq and sec_fav and mkt:
                    kind = "可买候选"
                elif fq and sec_fav and not mkt:
                    kind = "待0AMV做多"
                elif fq and bear and not sec_fav:
                    kind = "前哨"
                else:
                    continue
                out.append({"code": r["code"], "date": day, "kind": kind})
    return out


def main() -> None:
    signals = collect_signals()
    print(
        f"信号 {len(signals)} 个,逐笔按 B1 规则模拟(止损8%/BBI连破2日/成本30bps)…",
        file=sys.stderr,
    )
    bars_cache: dict = {}
    trades = []
    for k, s in enumerate(signals):
        if s["code"] not in bars_cache:
            try:
                df = local_tdx_data.get_ohlcv_table(s["code"], count=2000)
                if df is not None and len(df):
                    df = df.copy()
                    df["date"] = df["date"].astype(str).str[:10]
                    bars_cache[s["code"]] = df.sort_values("date").reset_index(
                        drop=True
                    )
                else:
                    bars_cache[s["code"]] = None
            except Exception:  # noqa: BLE001
                bars_cache[s["code"]] = None
        df = bars_cache[s["code"]]
        if df is None:
            continue
        idx = df.index[df["date"] == s["date"]]
        if len(idx) == 0:
            continue
        i = int(idx[0])
        bbi = bt._bbi_series(df["close"].astype(float))
        tr = bt.simulate_b1_trade(df, i, bbi, stop_mode="pct", stop_pct=8.0)
        trades.append(
            {
                **s,
                "ret": tr["ret"] - COST,
                "reason": tr["reason"],
                "holding": tr["holding"],
            }
        )
        if (k + 1) % 100 == 0:
            print(f"  {k + 1}/{len(signals)}", file=sys.stderr, flush=True)

    for kind in ("可买候选", "待0AMV做多", "前哨"):
        grp = [t for t in trades if t["kind"] == kind]
        if not grp:
            continue
        rets = [t["ret"] for t in grp]
        wins = [r for r in rets if r > 0]
        losses = [-r for r in rets if r < 0]
        closed = [t for t in grp if t["reason"] != "open_end"]
        by_reason = defaultdict(list)
        for t in grp:
            by_reason[t["reason"]].append(t["ret"])
        print(f"\n=== {kind} (n={len(grp)}, 已平仓 {len(closed)}) ===")
        print(
            f"  胜率 {len(wins) / len(rets) * 100:.1f}%  期望(均) {statistics.mean(rets) * 100:+.2f}%/笔  "
            f"中位 {statistics.median(rets) * 100:+.2f}%  累计 {sum(rets) * 100:+.1f}%"
        )
        print(
            f"  均盈 {(statistics.mean(wins) * 100 if wins else 0):+.2f}%  均亏 "
            f"{(-statistics.mean(losses) * 100 if losses else 0):+.2f}%  "
            f"盈亏比 {(statistics.mean(wins) / statistics.mean(losses) if wins and losses else 0):.2f}  "
            f"均持 {statistics.mean([t['holding'] for t in grp]):.1f} 根"
        )
        for rs, rr in sorted(by_reason.items()):
            print(
                f"    出场[{rs}] {len(rr)} 笔  均收 {statistics.mean(rr) * 100:+.2f}%"
            )
        worst = sorted(grp, key=lambda t: t["ret"])[:3]
        best = sorted(grp, key=lambda t: t["ret"], reverse=True)[:3]
        print(
            "  最好: "
            + "、".join(
                f"{t['code']}({t['date']},{t['ret'] * 100:+.1f}%)" for t in best
            )
        )
        print(
            "  最差: "
            + "、".join(
                f"{t['code']}({t['date']},{t['ret'] * 100:+.1f}%)" for t in worst
            )
        )


if __name__ == "__main__":
    main()
