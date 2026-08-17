# -*- coding: utf-8 -*-
"""优秀 B1 指纹证据层回测（R18）——B1_DATA 正例样本的召回与后续收益。

⚠️ **样本边界（写进每一份输出）**：`/ZGNB/B1_DATA/` 是 owner 精选的**正例**
（10 只优秀 B1 配套样本），**没有负例对照** —— 本脚本的数字只回答
「指纹对正例的召回率」与「命中后的后续走势」，**不回答胜率/判别力**
（那需要全市场或随机对照，是 launch_point_study/backtest_factors 的活）。

口径与复用：
- 滑窗 as-of 切片（`df.iloc[:t+1]`），**无未来函数**；
- 检测逻辑**零重写**：逐窗调 `enrich_candidates.compute_metrics`（b1_ignition
  合成标签、check_ignition、check_pullback_shrink、J 值、知行线全在它里面），
  与 live 选股链逐字同口径。
- 后续收益：命中日收盘起 5/10/20 个交易日（不足则记 None，不外推）。

CLI::

    uv run python src/custos/research/b1_fingerprint_study.py \
        --b1-data-dir /home/gh/agent/ZGNB/B1_DATA \
        [--out artifacts/logs/b1_fingerprint/summary.json]
"""

from __future__ import annotations

import argparse
import statistics
import json
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd  # noqa: E402

from custos.pipeline.screening import enrich_candidates as ec  # noqa: E402
from custos.core.paths import write_json  # noqa: E402

FORWARD_HORIZONS = (5, 10, 20)  # 后续收益窗口（交易日）
MIN_SLICE_BARS = 20  # 切片少于 20 根时各检测器本就 available=False，跳过以省时间


def load_b1_csv(path: Path) -> pd.DataFrame:
    """读 B1_DATA 配套 CSV（首行带 BOM ⇒ utf-8-sig；列名归一小写）。

    `date` 必须转 DatetimeIndex 可用的类型——`indicators.resample`（周线聚合）
    在字符串日期上直接 TypeError。
    """
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def scan_code(df: pd.DataFrame, code: str) -> dict[str, Any]:
    """逐日滑窗：每天的指纹要素与 b1_ignition 命中、命中日的后续收益。"""
    days: list[dict[str, Any]] = []
    n = len(df)
    for t in range(MIN_SLICE_BARS, n):
        sl = df.iloc[: t + 1]
        m = ec.compute_metrics(sl, None, code=code)
        b1i = m.get("b1_ignition") or {}
        hit = bool(b1i.get("hit"))
        rec: dict[str, Any] = {
            "date": str(df["date"].iloc[t])[:10],
            "daily_j": m.get("daily_j"),
            "j_low": bool((m.get("patterns") or {}).get("j_low")),
            "reversal_k": bool((m.get("patterns") or {}).get("reversal_k_candidate")),
            "pullback_shrink": bool((m.get("pullback_shrink") or {}).get("hit")),
            "ignition": bool((m.get("ignition") or {}).get("hit")),
            "zx_recent_gold": bool(b1i.get("zhixing_recent_golden")),
            "b1_ignition": hit,
        }
        if hit:
            c0 = float(df["close"].iloc[t])
            rec["fwd"] = {
                h: round((float(df["close"].iloc[t + h]) / c0 - 1) * 100, 2)
                if t + h < n and c0
                else None
                for h in FORWARD_HORIZONS
            }
        days.append(rec)
    hits = [d for d in days if d["b1_ignition"]]
    first = hits[0] if hits else None
    fwd_stats: dict[str, Any] = {}
    for h in FORWARD_HORIZONS:
        vals = [d["fwd"][h] for d in hits if d.get("fwd", {}).get(h) is not None]
        fwd_stats[f"fwd{h}"] = {
            "n": len(vals),
            "mean": round(sum(vals) / len(vals), 2) if vals else None,
            "median": round(statistics.median(vals), 2) if vals else None,
            "win_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1)
            if vals
            else None,
        }
    leg_hits = {
        leg: sum(1 for d in days if d[leg])
        for leg in (
            "j_low",
            "reversal_k",
            "pullback_shrink",
            "ignition",
            "zx_recent_gold",
        )
    }
    return {
        "code": code,
        "bars": n,
        "scanned_days": len(days),
        "n_hits": len(hits),
        "first_hit": first,
        "hit_days": hits,
        "leg_hit_days": leg_hits,
        "fwd": fwd_stats,
    }


def summarize(per_code: list[dict[str, Any]]) -> dict[str, Any]:
    """跨票汇总。召回率 = 有 ≥1 次 b1_ignition 命中的票占比（正例口径）。"""
    n = len(per_code)
    recalled = [p for p in per_code if p["n_hits"] > 0]
    pooled_fwd: dict[str, Any] = {}
    for h in FORWARD_HORIZONS:
        vals = [
            d["fwd"][h]
            for p in per_code
            for d in p["hit_days"]
            if d.get("fwd", {}).get(h) is not None
        ]
        pooled_fwd[f"fwd{h}"] = {
            "n": len(vals),
            "mean": round(sum(vals) / len(vals), 2) if vals else None,
            "median": round(statistics.median(vals), 2) if vals else None,
            "win_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1)
            if vals
            else None,
        }
    return {
        "n_codes": n,
        "recall_codes": len(recalled),
        "recall_rate": round(len(recalled) / n * 100, 1) if n else None,
        "total_hits": sum(p["n_hits"] for p in per_code),
        "pooled_fwd": pooled_fwd,
        # ⚠️ 样本边界必须随数字走：正例精选、无负例，召回≠胜率
        "caveat": "精选正例样本、无负例对照：只回答召回率与命中后走势，不回答胜率/判别力",
    }


def render_md(summary: dict[str, Any], per_code: list[dict[str, Any]]) -> str:
    lines = [
        "# 优秀 B1 指纹证据层回测（R18）",
        "",
        f"> ⚠️ {summary['caveat']}",
        "",
        f"- 样本 {summary['n_codes']} 票；b1_ignition 命中过 "
        f"{summary['recall_codes']} 票（召回 {summary['recall_rate']}%）；"
        f"总命中 {summary['total_hits']} 天。",
        "",
        "| 代码 | K线 | 扫描日 | 命中日数 | 首次命中 | 首次命中时的驱动腿 | fwd5 | fwd10 | fwd20 |",
        "|---|---:|---:|---:|---|---|---:|---:|---:|",
    ]
    for p in per_code:
        fh = p["first_hit"]
        legs = (
            "、".join(
                k
                for k in (
                    "j_low",
                    "reversal_k",
                    "pullback_shrink",
                    "ignition",
                    "zx_recent_gold",
                )
                if fh.get(k)
            )
            if fh
            else "-"
        )
        fwd = p["fwd"]
        lines.append(
            f"| {p['code']} | {p['bars']} | {p['scanned_days']} | {p['n_hits']} "
            f"| {fh['date'] if fh else '-'} | {legs} "
            f"| {_fmt(fwd['fwd5']['mean'])} | {_fmt(fwd['fwd10']['mean'])} | {_fmt(fwd['fwd20']['mean'])} |"
        )
    lines += ["", "**合并后续收益（均值/中位/胜率，n=命中日数）**：", ""]
    for h in FORWARD_HORIZONS:
        s = summary["pooled_fwd"][f"fwd{h}"]
        lines.append(
            f"- fwd{h}：n={s['n']}，均 {s['mean']}% / 中位 {s['median']}% / 胜率 {s['win_rate']}%"
        )
    lines.append("")
    return "\n".join(lines)


def _fmt(v: Any) -> str:
    return "-" if v is None else f"{v}"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="优秀 B1 指纹证据层回测（R18）")
    ap.add_argument(
        "--b1-data-dir",
        required=True,
        help="B1_DATA 目录（配套 CSV：Date,Code,Amount,Close,...,首行带 BOM）",
    )
    ap.add_argument("--out", default="", help="汇总 JSON 落盘路径（可选）")
    args = ap.parse_args(argv)

    data_dir = Path(args.b1_data_dir)
    files = sorted(data_dir.glob("*.csv"))
    if not files:
        print(f"[FAIL] {data_dir} 下没有 CSV", file=sys.stderr)
        return 2
    per_code = []
    for f in files:
        df = load_b1_csv(f)
        code = (
            str(df["code"].iloc[-1]).split(".")[0].zfill(6)
            if "code" in df
            else f.stem.split("-")[0]
        )
        per_code.append(scan_code(df, code))
    summary = summarize(per_code)
    print(render_md(summary, per_code))
    if args.out:
        out = Path(args.out)
        write_json(out, {"summary": summary, "per_code": per_code})
        print(f"[OK] 落盘 {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
