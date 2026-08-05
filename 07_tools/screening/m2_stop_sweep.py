#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""M2 机制类改进扫描：分组跑对照并自动判定。

## 为什么要分组（2026-08-04 修正）

第一轮判定犯了一个口径错误：**拿累计 R 跨 `stop_mode` 比较**。

    R = ret / risk_frac

基准用 `stop_mode="low"`（买入当日最低价），实测 `risk_frac` **中位仅 0.65%**；
换成 `--stop-pct 12` 后固定 12%，**分母大了 18 倍**，R 自然崩。于是
「胜率 18%→51.2%、期望 +0.43%→+1.42%」这组明显更好的结果，被「累计 R 从 332 掉到 135」
判成了否决。

更要紧的是：基准那 332R **本来就不可实现**——按风险定额需要 `1%/0.65% = 154%` 仓位，
实际被 `max_pos` 削到 20%，兑现不了 87%。`_R_RISK_FLOOR`（2% 地板）就是这个问题的补丁。
而 0.65% 的止损空间在真实盘面根本执行不了，A 股日内波动轻易打掉。

所以本脚本按 **stop_mode 分组**：

    组内（同一 R 口径）  比 expectancy_R / total_R / 大赢家笔数
    跨组（不同 R 口径）  只比 expectancy / win_rate / payoff_ratio / 盈亏平衡 margin
    组合级              只比 total_return / CAGR / max_drawdown（R 完全不适用）

## 组合级为什么会「逐笔正期望、组合亏损」

受控实验定位到根因是**相关亏损**：B1 是超卖买入，市场普跌时全市场 `J<13` 同时触发，
随后市场继续跌 ⇒ 同批持仓一起亏。同一份幂律收益序列：

    信号聚集、**无**相关亏损 c5×20%(敞口100%)   +55.9%   回撤 6.7%
    信号聚集、**有**相关亏损 c5×20%(敞口100%)   −37.8%   回撤 47.0%
    同上 c2×20%(敞口40%)                        −16.4%   回撤 21.9%
    同上 c5×5% (敞口25%)                        −11.9%   回撤 15.2%
    同上 c20×5%(敞口100%)                       −54.3%   回撤 56.3%

**决定因素是总敞口，不是持仓数量**（c20×5% 与 c5×20% 同为 100% 敞口，同样惨）。
分散持仓数对高相关信号无效。

两个应对手段本仓库早就有、但第一轮没用：
  · `--amv-long-only`  避开普跌期（相关亏损的来源）
  · `--top-n`          横截面按 score 择优，替代「先到先得」
    （执行率仅 22% 时，先到先得等于抽签命中大赢家）

用法：
    uv run python 07_tools/screening/m2_stop_sweep.py --sample 300      # 小样本试跑
    uv run python 07_tools/screening/m2_stop_sweep.py                   # 全部
    uv run python 07_tools/screening/m2_stop_sweep.py --only B_stop_pct # 只跑一组
    uv run python 07_tools/screening/m2_stop_sweep.py --report-only     # 只重出报表
    uv run python 07_tools/screening/m2_stop_sweep.py --cross-window    # 2022-2024 复核
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import subprocess
import sys
import time
from typing import Any, Optional

BASE = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = BASE / "07_tools" / "screening" / "backtest_factors.py"
OUTDIR = BASE / "06_logs" / "m2_sweep"

MIN_EXPECTANCY_GAIN = 0.02      # 组内：expectancy_R 至少提升 2%
MAX_AVG_WIN_DROP = 0.05         # 均盈跌幅超 5% 即判「削大赢家」
BIG_WIN_THRESHOLD = 0.20        # ret > +20% 记为大赢家

# 每组共享的 stop 口径 → 组内 R 可比；跨组只比收益率
GROUPS: dict[str, dict[str, Any]] = {
    "A_stop_low": {
        "desc": "stop_mode=low（买入当日最低价，risk_frac 中位 0.65%）",
        "common": [],
        "baseline": "00_baseline",
        "runs": {
            "00_baseline": [],
            "be_03": ["--breakeven", "0.03"],
            "be_05": ["--breakeven", "0.05"],
            "be_08": ["--breakeven", "0.08"],
            "trail_08": ["--trail", "0.08"],
            "trail_12": ["--trail", "0.12"],
            "trail_18": ["--trail", "0.18"],
            "trigger_intraday": ["--stop-trigger", "intraday"],
            "tick_buffer_3": ["--stop-tick-buffer", "3"],
            "cost_zone_3": ["--cost-zone-bars", "3"],
            "amv_long_only": ["--amv-long-only"],
        },
    },
    "B_stop_pct": {
        "desc": "stop_mode=pct（固定百分比，止损可执行；R 口径与 A 组不可比）",
        "common": ["--stop-mode", "pct"],
        "baseline": "pct_12",
        "runs": {
            "pct_05": ["--stop-pct", "5"],
            "pct_08": ["--stop-pct", "8"],
            "pct_12": ["--stop-pct", "12"],
            "pct_12_amv": ["--stop-pct", "12", "--amv-long-only"],
            "pct_12_amv_cz3": ["--stop-pct", "12", "--amv-long-only",
                               "--cost-zone-bars", "3"],
            "pct_08_amv": ["--stop-pct", "8", "--amv-long-only"],
        },
    },
    "C_portfolio": {
        "desc": "组合级资金曲线（只看 total_return / CAGR / max_drawdown）",
        "common": ["--stop-mode", "pct", "--stop-pct", "12", "--portfolio"],
        "baseline": None,
        "runs": {
            # 第一轮的两组（敞口 100% / 60%），留作对照
            "pf_c5_p20": ["--max-concurrent", "5", "--max-pos", "20", "--risk-pct", "1.0"],
            "pf_c3_p20": ["--max-concurrent", "3", "--max-pos", "20", "--risk-pct", "2.0"],
            # 低敞口
            "pf_c2_p20": ["--max-concurrent", "2", "--max-pos", "20", "--risk-pct", "1.0"],
            "pf_c5_p05": ["--max-concurrent", "5", "--max-pos", "5", "--risk-pct", "1.0"],
            # 加择时（避开相关亏损来源）
            "pf_c2_p20_amv": ["--max-concurrent", "2", "--max-pos", "20",
                              "--risk-pct", "1.0", "--amv-long-only"],
            "pf_c5_p05_amv": ["--max-concurrent", "5", "--max-pos", "5",
                              "--risk-pct", "1.0", "--amv-long-only"],
            # 加横截面择优（替代先到先得）
            "pf_top2_c2_amv": ["--top-n", "2", "--max-concurrent", "2", "--max-pos", "20",
                               "--risk-pct", "1.0", "--amv-long-only"],
            "pf_top3_c5_p05_amv": ["--top-n", "3", "--max-concurrent", "5", "--max-pos", "5",
                                   "--risk-pct", "1.0", "--amv-long-only"],
        },
    },
}


def _base_args(sample: int, cross: bool) -> list[str]:
    a = ["--trade-sim", "--entry-filter", "j_low", "--scorer", "b1_dual",
         "--cost-bps", "25", "--scale-out", "0.5",
         "--universe-local", "--universe-sample", str(sample)]
    if cross:
        # ⚠️ --count 必须加大：默认 500 根从今天往前数，加 --start/--end 只覆盖窗口尾部
        a += ["--start", "2022-01-01", "--end", "2024-12-31", "--count", "1500"]
    return a


def _run(group: str, name: str, extra: list[str], sample: int, cross: bool,
         force: bool) -> Optional[pathlib.Path]:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"{'cw_' if cross else ''}{group}__{name}.json"
    if out.exists() and not force:
        print(f"[SKIP] {out.name}")
        return out
    cmd = ([sys.executable, str(SCRIPT)] + _base_args(sample, cross)
           + GROUPS[group]["common"] + extra + ["--out", str(out)])
    print(f"\n[RUN ] {group}/{name}: {' '.join(extra) or '(组基准)'}")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(BASE))
    if r.returncode != 0:
        print(f"[FAIL] {group}/{name} exit={r.returncode}")
        return None
    print(f"[DONE] {group}/{name} {time.time() - t0:.0f}s")
    return out if out.exists() else None


def _load(p: pathlib.Path) -> dict:
    """读一个结果 JSON。

    ⚠️ 键名是 **`trade_summary`**（`backtest_factors.py:2118`），我第一版写成
    `trade_sim`/`summary`/`trade_simulation` 全都对不上，导致 owner 跑完 25 个方案后
    报表生成不出来、只能手工汇总。这里保留多个候选键并做兜底扫描，避免再因键名改动
    静默失效——**读不到就明确报错，不要静默返回空**。
    """
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                        # noqa: BLE001
        print(f"[WARN] 读不了 {p.name}: {e}")
        return {}
    pf = d.get("portfolio")
    for k in ("trade_summary", "trade_sim", "summary", "trade_simulation"):
        blk = d.get(k)
        if isinstance(blk, dict) and ("expectancy" in blk or "n" in blk):
            s = dict(blk)
            s["_trades"] = d.get("trades") or blk.get("trades") or []
            s["_portfolio"] = pf or blk.get("portfolio")
            return s
    if "expectancy" in d:                                          # 摘要直接在顶层
        s = dict(d)
        s["_trades"] = d.get("trades") or []
        s["_portfolio"] = pf
        return s
    # 兜底：扫一层子字典找带 expectancy 的块（键名再改也能活）
    for k, v in d.items():
        if isinstance(v, dict) and "expectancy" in v:
            s = dict(v)
            s["_trades"] = d.get("trades") or []
            s["_portfolio"] = pf
            print(f"[INFO] {p.name}: 摘要在非预期键 '{k}' 下，已兜底读取")
            return s
    if pf:                                                         # 纯组合级结果
        return {"_trades": [], "_portfolio": pf}
    print(f"[WARN] {p.name}: 找不到交易摘要（顶层键: {sorted(d)[:8]}）")
    return {}


def _big_wins(trades: list) -> int:
    return sum(1 for t in trades
               if isinstance(t, dict) and (t.get("ret") or 0) > BIG_WIN_THRESHOLD)


def _breakeven_wr(payoff: Optional[float]) -> Optional[float]:
    """盈亏平衡胜率 = 1/(1+b)。它与实际胜率的差就是安全边际。"""
    if not payoff or payoff <= 0:
        return None
    return 1.0 / (1.0 + payoff)


def _collect(cross: bool) -> dict[str, list[dict]]:
    pref = "cw_" if cross else ""
    out: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for p in sorted(OUTDIR.glob(f"{pref}*__*.json")):
        if not cross and p.name.startswith("cw_"):
            continue
        stem = p.stem[3:] if cross else p.stem
        if "__" not in stem:
            continue
        group, name = stem.split("__", 1)
        if group not in out:
            continue
        s = _load(p)
        if not s:
            continue
        out[group].append({
            "name": name, "n": s.get("n"), "win": s.get("win_rate"),
            "exp": s.get("expectancy"), "expR": s.get("expectancy_R"),
            "totR": s.get("total_R"), "payoff": s.get("payoff_ratio"),
            "avg_win": s.get("avg_win"), "avg_loss": s.get("avg_loss"),
            "hold": s.get("avg_holding"), "big": _big_wins(s.get("_trades") or []),
            "reasons": s.get("exit_reasons") or {}, "pf": s.get("_portfolio"),
        })
    return out


def _print_trade_group(group: str, rows: list[dict]) -> None:
    meta = GROUPS[group]
    hdr = (f"{'组':<20}{'笔数':>7}{'胜率':>8}{'期望%':>8}{'期望R':>8}"
           f"{'累计R':>9}{'盈亏比':>8}{'均盈%':>8}{'大赢家':>7}")
    print("\n" + "=" * len(hdr))
    print(f"【{group}】{meta['desc']}")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(rows, key=lambda x: x["name"]):
        print(f"{r['name']:<20}{r['n'] or 0:>7}{(r['win'] or 0) * 100:>7.1f}%"
              f"{(r['exp'] or 0) * 100:>+8.2f}{r['expR'] or 0:>8.3f}"
              f"{r['totR'] or 0:>9.1f}{r['payoff'] or 0:>8.3f}"
              f"{(r['avg_win'] or 0) * 100:>+8.2f}{r['big']:>7}")

    base_name = meta.get("baseline")
    base = next((r for r in rows if r["name"] == base_name), None)
    if not base:
        return
    print(f"\n组内判定（基准 = {base_name}；**R 只在本组内可比**）")
    print("  阈值：期望R 提升 >2% 且 均盈跌幅 <5% 且 大赢家**占比**不下降")
    print("  ⚠️ 大赢家用**占比**（big/n）而非绝对数：择时类方案（如 --amv-long-only）会")
    print("     过滤掉部分信号，样本量下降时绝对数必然下降，用绝对数比会把它们全部误杀。")
    b_expR, b_aw = base["expR"] or 0, base["avg_win"] or 0
    b_big, b_n = base["big"], base["n"] or 1
    b_rate = b_big / b_n
    for r in sorted(rows, key=lambda x: x["name"]):
        if r["name"] == base_name:
            continue
        expR, aw = r["expR"] or 0, r["avg_win"] or 0
        n = r["n"] or 1
        rate = r["big"] / n
        d_exp = (expR - b_expR) / abs(b_expR) if b_expR else 0.0
        d_aw = (aw - b_aw) / b_aw if b_aw else 0.0
        d_rate = (rate - b_rate) / b_rate if b_rate else 0.0
        ok = (d_exp > MIN_EXPECTANCY_GAIN and d_aw > -MAX_AVG_WIN_DROP
              and d_rate > -MAX_AVG_WIN_DROP)
        why = []
        if d_exp <= MIN_EXPECTANCY_GAIN:
            why.append(f"期望R {d_exp:+.1%}")
        if d_aw <= -MAX_AVG_WIN_DROP:
            why.append(f"均盈 {d_aw:+.1%} 削大赢家")
        if d_rate <= -MAX_AVG_WIN_DROP:
            why.append(f"大赢家占比 {b_rate:.2%}→{rate:.2%}")
        print(f"  {r['name']:<20}{'✅ 通过' if ok else '❌ 否决'}  "
              f"期望R {d_exp:+6.1%}  均盈 {d_aw:+6.1%}  "
              f"大赢家 {b_big}/{b_n}({b_rate:.2%}) → {r['big']}/{n}({rate:.2%})"
              f"{('  ｜' + '；'.join(why)) if why else ''}")


def _print_cross_group(groups: dict[str, list[dict]]) -> None:
    """跨组比较：**只用收益率口径**，R 一律不出现。"""
    rows = []
    for g, rs in groups.items():
        if g == "C_portfolio":
            continue
        for r in rs:
            rows.append((g, r))
    if not rows:
        return
    hdr = (f"{'组/方案':<32}{'笔数':>7}{'胜率':>8}{'期望%':>9}{'盈亏比':>8}"
           f"{'平衡胜率':>9}{'margin':>8}")
    print("\n" + "=" * len(hdr))
    print("跨组比较（**不同 stop_mode 之间 R 不可比，这里只看收益率**）")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for g, r in sorted(rows, key=lambda x: -(x[1]["exp"] or -9)):
        be = _breakeven_wr(r["payoff"])
        margin = (r["win"] - be) if (be is not None and r["win"] is not None) else None
        print(f"{g + '/' + r['name']:<32}{r['n'] or 0:>7}"
              f"{(r['win'] or 0) * 100:>7.1f}%{(r['exp'] or 0) * 100:>+9.2f}"
              f"{r['payoff'] or 0:>8.3f}"
              f"{be * 100 if be else 0:>8.1f}%"
              f"{margin * 100 if margin is not None else 0:>+7.1f}pp")
    print("\n  margin = 实际胜率 − 盈亏平衡胜率。越薄越脆弱：成本上升或波动率下降就可能翻负。")


def _print_portfolio(rows: list[dict]) -> None:
    if not rows:
        return
    hdr = (f"{'方案':<24}{'总收益':>9}{'CAGR':>8}{'最大回撤':>9}"
           f"{'成交':>7}{'被限':>7}{'执行率':>8}")
    print("\n" + "=" * len(hdr))
    print("【C_portfolio】组合级（R 完全不适用；逐笔正期望 ≠ 组合能赚）")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(rows, key=lambda x: -((x["pf"] or {}).get("total_return") or -9)):
        d = r["pf"] or {}
        taken = d.get("n_taken") or d.get("filled") or 0
        skip = d.get("n_skipped") or d.get("skipped") or 0
        er = taken / (taken + skip) if (taken + skip) else 0
        print(f"{r['name']:<24}{(d.get('total_return') or 0) * 100:>8.1f}%"
              f"{(d.get('cagr') or 0) * 100:>7.1f}%"
              f"{(d.get('max_drawdown') or 0) * 100:>8.1f}%"
              f"{taken:>7}{skip:>7}{er * 100:>7.1f}%")
    print("\n  ⚠️ 受控实验结论：决定亏损幅度的是**总敞口**（max_concurrent × max_pos），")
    print("     不是持仓数量——B1 信号高度相关（普跌时全市场同时触发），分散持仓数无效。")
    print("     执行率低时「先到先得」等于抽签命中大赢家，用 --top-n 做横截面择优。")


def report(cross: bool) -> None:
    groups = _collect(cross)
    if not any(groups.values()):
        print("没有结果文件，先跑扫描")
        return
    print("\n" + "#" * 74)
    print(f"# M2 机制扫描{'（2022-2024 跨窗复核）' if cross else ''}")
    print("#" * 74)
    for g in ("A_stop_low", "B_stop_pct"):
        if groups.get(g):
            _print_trade_group(g, groups[g])
    _print_cross_group(groups)
    _print_portfolio([r for r in groups.get("C_portfolio", []) if r.get("pf")])
    if not cross:
        print("\n⚠️ 通过的组仍须跨窗复核：--cross-window（2022-2024）。")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # GBK 终端打不了 ⚠️ 等符号
    ap = argparse.ArgumentParser(description="M2 机制类改进扫描（分组）")
    ap.add_argument("--sample", type=int, default=1000)
    ap.add_argument("--only", default="",
                    help="只跑匹配的组或方案（子串匹配组名/方案名）")
    ap.add_argument("--cross-window", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if not a.report_only:
        todo = [(g, n, e) for g, meta in GROUPS.items()
                for n, e in meta["runs"].items()
                if not a.only or a.only in g or a.only in n]
        if not todo:
            print(f"--only {a.only} 没匹配到任何组/方案")
            return 2
        print(f"将跑 {len(todo)} 个方案，样本 {a.sample} 只"
              f"{'，区间 2022-2024' if a.cross_window else ''}")
        for g, n, e in todo:
            _run(g, n, e, a.sample, a.cross_window, a.force)
    report(a.cross_window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
