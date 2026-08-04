#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""M2 机制类改进扫描驱动：一条命令跑完全部对照组并输出对照表。

为什么要这个脚本：M2 有 6 组十几条命令，手动跑容易漏、跑完还要对比十几个 JSON。
更重要的是**判定标准不止看期望**——移动止损可能砍掉大赢家（终审揭示收益极端幂律：
若总 1000 笔，24 笔占 2.4% 贡献全部收益），所以必须同时盯 avg_win 和大赢家笔数。
人工对比很容易只看 expectancy 就下结论。

用法：
    uv run python 07_tools/screening/m2_stop_sweep.py                 # 全部
    uv run python 07_tools/screening/m2_stop_sweep.py --only breakeven # 只跑一组
    uv run python 07_tools/screening/m2_stop_sweep.py --sample 300     # 先小样本试跑
    uv run python 07_tools/screening/m2_stop_sweep.py --cross-window   # 2022-2024 复核
    uv run python 07_tools/screening/m2_stop_sweep.py --report-only    # 只重出报表

结果落 06_logs/m2_sweep/<name>.json，中断后重跑会跳过已完成的组（--force 强制重跑）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

BASE = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = BASE / "07_tools" / "screening" / "backtest_factors.py"
OUTDIR = BASE / "06_logs" / "m2_sweep"

# 判定阈值（比前几轮严：不只看期望）
MIN_EXPECTANCY_GAIN = 0.02      # expectancy_R 至少提升 2%（相对基准）
MAX_AVG_WIN_DROP = 0.05         # avg_win 下降超过 5% 即判「削大赢家」
BIG_WIN_THRESHOLD = 0.20        # ret > +20% 记为大赢家


def _groups(sample: int, cross: bool) -> list[tuple[str, list[str]]]:
    """(名称, 额外参数) —— 基准参数由 _base_args 统一给。"""
    g: list[tuple[str, list[str]]] = [("00_baseline", [])]
    g += [(f"be_{int(v*100):02d}", ["--breakeven", str(v)]) for v in (0.03, 0.05, 0.08)]
    g += [(f"trail_{int(v*100):02d}", ["--trail", str(v)]) for v in (0.08, 0.12, 0.18)]
    g += [("stop_pct_05", ["--stop-mode", "pct", "--stop-pct", "5"]),
          ("stop_pct_08", ["--stop-mode", "pct", "--stop-pct", "8"]),
          ("stop_pct_12", ["--stop-mode", "pct", "--stop-pct", "12"])]
    g += [("pf_r1_c5", ["--portfolio", "--risk-pct", "1.0", "--max-concurrent", "5"]),
          ("pf_r2_c3", ["--portfolio", "--risk-pct", "2.0", "--max-concurrent", "3"])]
    return g


def _base_args(sample: int, cross: bool) -> list[str]:
    a = ["--trade-sim", "--entry-filter", "j_low", "--scorer", "b1_dual",
         "--cost-bps", "25", "--scale-out", "0.5",
         "--universe-local", "--universe-sample", str(sample)]
    if cross:
        # ⚠️ --count 必须加大：默认 500 根从今天往前数，加 --start/--end 只覆盖窗口尾部
        a += ["--start", "2022-01-01", "--end", "2024-12-31", "--count", "1500"]
    return a


def _run(name: str, extra: list[str], sample: int, cross: bool,
         force: bool) -> pathlib.Path | None:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"{'cw_' if cross else ''}{name}.json"
    if out.exists() and not force:
        print(f"[SKIP] {out.name} 已存在（--force 强制重跑）")
        return out
    cmd = [sys.executable, str(SCRIPT)] + _base_args(sample, cross) + extra \
        + ["--out", str(out)]
    print(f"\n[RUN ] {name}: {' '.join(extra) or '(基准)'}")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(BASE))
    if r.returncode != 0:
        print(f"[FAIL] {name} exit={r.returncode}")
        return None
    print(f"[DONE] {name} {time.time() - t0:.0f}s")
    return out if out.exists() else None


def _load(p: pathlib.Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                        # noqa: BLE001
        print(f"[WARN] 读不了 {p.name}: {e}")
        return {}
    # trade_sim 摘要可能在顶层或嵌在 trade_sim/summary 下，兼容取值
    for k in ("trade_sim", "summary", "trade_simulation"):
        if isinstance(d.get(k), dict) and "expectancy" in d[k]:
            s = dict(d[k])
            s["_trades"] = d.get("trades") or d[k].get("trades") or []
            s["_portfolio"] = d.get("portfolio") or d[k].get("portfolio")
            return s
    if "expectancy" in d:
        s = dict(d)
        s["_trades"] = d.get("trades") or []
        s["_portfolio"] = d.get("portfolio")
        return s
    return {}


def _big_wins(trades: list, thr: float = BIG_WIN_THRESHOLD) -> int:
    return sum(1 for t in trades
               if isinstance(t, dict) and (t.get("ret") or 0) > thr)


def report(cross: bool) -> None:
    rows = []
    for p in sorted(OUTDIR.glob("cw_*.json" if cross else "*.json")):
        if not cross and p.name.startswith("cw_"):
            continue
        s = _load(p)
        if not s:
            continue
        name = p.stem[3:] if cross else p.stem
        rows.append({
            "name": name, "n": s.get("n"),
            "win": s.get("win_rate"), "exp": s.get("expectancy"),
            "expR": s.get("expectancy_R"), "totR": s.get("total_R"),
            "payoff": s.get("payoff_ratio"), "avg_win": s.get("avg_win"),
            "avg_loss": s.get("avg_loss"), "hold": s.get("avg_holding"),
            "big": _big_wins(s.get("_trades") or []),
            "reasons": s.get("exit_reasons") or {},
            "pf": s.get("_portfolio"),
        })
    if not rows:
        print("没有结果文件，先跑扫描")
        return

    base = next((r for r in rows if r["name"] == "00_baseline"), None)
    hdr = (f"{'组':<14}{'笔数':>7}{'胜率':>8}{'期望%':>8}{'期望R':>8}"
           f"{'累计R':>9}{'盈亏比':>8}{'均盈%':>8}{'均持':>7}{'大赢家':>7}")
    print("\n" + "=" * len(hdr))
    print(f"M2 机制扫描对照表{'（2022-2024 跨窗复核）' if cross else ''}")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['name']:<14}{r['n'] or 0:>7}"
              f"{(r['win'] or 0) * 100:>7.1f}%{(r['exp'] or 0) * 100:>+8.2f}"
              f"{r['expR'] or 0:>8.3f}{r['totR'] or 0:>9.1f}"
              f"{r['payoff'] or 0:>8.3f}{(r['avg_win'] or 0) * 100:>+8.2f}"
              f"{r['hold'] or 0:>7.1f}{r['big']:>7}")

    if not base:
        print("\n（缺基准组 00_baseline，无法判定）")
        return

    print("\n" + "=" * len(hdr))
    print("判定（阈值：期望R 提升 >2% 且 均盈跌幅 <5% 且 大赢家笔数不减少）")
    print("=" * len(hdr))
    print("⚠️ 大赢家保护是硬条件：终审显示收益极端幂律，极少数交易贡献全部收益。")
    print("   即使期望提升，只要大赢家被削掉也**否决**——那是把收益来源换成更脆弱的东西。\n")
    b_expR, b_aw, b_big = base["expR"] or 0, base["avg_win"] or 0, base["big"]
    for r in rows:
        if r["name"] == "00_baseline" or r["pf"]:
            continue
        expR, aw = r["expR"] or 0, r["avg_win"] or 0
        d_exp = (expR - b_expR) / abs(b_expR) if b_expR else 0.0
        d_aw = (aw - b_aw) / b_aw if b_aw else 0.0
        ok_exp = d_exp > MIN_EXPECTANCY_GAIN
        ok_aw = d_aw > -MAX_AVG_WIN_DROP
        ok_big = r["big"] >= b_big
        verdict = "✅ 通过" if (ok_exp and ok_aw and ok_big) else "❌ 否决"
        why = []
        if not ok_exp:
            why.append(f"期望R {d_exp:+.1%} 未达 +2%")
        if not ok_aw:
            why.append(f"均盈 {d_aw:+.1%} 削大赢家")
        if not ok_big:
            why.append(f"大赢家 {b_big}→{r['big']}")
        print(f"  {r['name']:<14}{verdict}  期望R {d_exp:+6.1%}  均盈 {d_aw:+6.1%}  "
              f"大赢家 {b_big}→{r['big']:<4}{('｜' + '；'.join(why)) if why else ''}")

    pf = [r for r in rows if r["pf"]]
    if pf:
        print("\n组合级资金曲线（逐笔期望为正 ≠ 组合能赚：并发上限漏信号、固定风险放大回撤）")
        for r in pf:
            d = r["pf"] or {}
            print(f"  {r['name']:<14}总收益 {d.get('total_return')}  "
                  f"CAGR {d.get('cagr')}  最大回撤 {d.get('max_drawdown')}  "
                  f"成交 {d.get('filled')}  被限 {d.get('skipped')}")

    print("\n出场原因分布（看新机制实际接管了多少单）")
    for r in rows:
        if r["pf"]:
            continue
        tot = sum(v.get("n", 0) for v in r["reasons"].values()) or 1
        parts = [f"{k} {v['n']}({v['n'] / tot:.0%},均{v['avg_return'] * 100:+.1f}%)"
                 for k, v in sorted(r["reasons"].items(),
                                    key=lambda kv: -kv[1].get("n", 0))[:4]]
        print(f"  {r['name']:<14}{'  '.join(parts)}")

    if not cross:
        print("\n⚠️ 通过的组仍须跨窗复核：--cross-window（2022-2024）。")
        print("   机制类无 regime 依赖是**假设**，上一轮就是被三个「看起来很合理」的因子骗了。")


def main() -> int:
    ap = argparse.ArgumentParser(description="M2 机制类改进扫描")
    ap.add_argument("--sample", type=int, default=1000, help="抽样只数（默认 1000）")
    ap.add_argument("--only", default="", help="只跑含该子串的组，如 breakeven/trail/stop_pct/pf")
    ap.add_argument("--cross-window", action="store_true", help="跑 2022-2024 跨窗复核")
    ap.add_argument("--report-only", action="store_true", help="只重出报表")
    ap.add_argument("--force", action="store_true", help="已有结果也重跑")
    a = ap.parse_args()

    if not a.report_only:
        sel = [(n, e) for n, e in _groups(a.sample, a.cross_window)
               if not a.only or a.only in n or (a.only == "breakeven" and n.startswith("be_"))]
        if not sel:
            print(f"--only {a.only} 没匹配到任何组")
            return 2
        print(f"将跑 {len(sel)} 组，样本 {a.sample} 只"
              f"{'，区间 2022-2024' if a.cross_window else ''}")
        for n, e in sel:
            _run(n, e, a.sample, a.cross_window, a.force)
    report(a.cross_window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
