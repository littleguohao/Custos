# -*- coding: utf-8 -*-
"""驱动"空头段识别未来赢家"研究:枚举窗口对 → 逐对跑 Pass1 → 跨窗 Pass2 汇总。

为什么要驱动脚本:窗口对有十几个,手抄十几条 Pass1 命令易错;且 Pass1 耗时长,中途失败必须能
**断点续跑**(已有 firings 文件默认跳过)。所有实际计算仍在 launch_point_study.py 里,本脚本只编排。

数据可用性护栏(依据 B1_BACKTEST_FINDINGS §3 s_data 特性):
  - qlib 两个 bundle 间缺口 2020-09-28→2021-07-30:任何与之相交的窗口对**必须剔除**;
  - 数据止于 2026-02-06:赢家窗超出即剔除(否则赢家收益按残缺区间算)。

用法(先看计划,不跑):
  uv run python 07_tools/screening/run_bear_to_long_study.py --dry-run
真跑:
  uv run python 07_tools/screening/run_bear_to_long_study.py --out-dir 06_logs/bear2long
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(TOOLS), str(TOOLS / "screening")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from paths import BASE  # noqa: E402

import backtest_factors as bt  # noqa: E402
import launch_point_study as lp  # noqa: E402

STUDY = TOOLS / "screening" / "launch_point_study.py"
QLIB_GAP = ("2020-09-28", "2021-07-30")     # 两 bundle 之间无数据
QLIB_END = "2026-02-06"                     # qlib 数据末尾
DEFAULT_FEATURES = ("reversal_quality,momentum,low_vol,alpha101,alpha_pvcorr,"
                    "kdj_j,s_shape,b1_pullback,invert_s_shape,s_reversal")


def overlaps_gap(start: str, end: str, gap: tuple[str, str] = QLIB_GAP) -> bool:
    """窗口 [start,end] 是否与数据缺口相交(相交即整对剔除)。"""
    return not (end < gap[0] or start > gap[1])


def usable_pairs(pairs: list[dict], qlib_end: str = QLIB_END,
                 gap: tuple[str, str] = QLIB_GAP) -> tuple[list[dict], list[dict]]:
    """按数据可用性切分 (可用, 剔除并附 reason)。信号窗与赢家窗都要过护栏。"""
    keep, drop = [], []
    for p in pairs:
        if overlaps_gap(p["signal_start"], p["signal_end"], gap):
            drop.append({**p, "reason": f"信号窗跨 qlib 缺口 {gap[0]}~{gap[1]}"})
        elif overlaps_gap(p["label_start"], p["label_end"], gap):
            drop.append({**p, "reason": f"赢家窗跨 qlib 缺口 {gap[0]}~{gap[1]}"})
        elif p["label_end"] > qlib_end:
            drop.append({**p, "reason": f"赢家窗超出 qlib 数据末尾 {qlib_end}"})
        else:
            keep.append(p)
    return keep, drop


def pass1_cmd(p: dict, out_file: Path, args) -> list[str]:
    """单对窗口的 Pass1 命令:信号窗 [signal_*] / 赢家窗 [label_*] 解耦 + 退市股按大亏计入。"""
    cmd = [sys.executable, str(STUDY),
           "--data-source", args.data_source, "--universe-sdata",
           "--s-data-root", args.s_data_root,
           "--entry-filter", args.entry_filter,
           "--start", p["signal_start"], "--end", p["signal_end"],
           "--ret-start", p["label_start"], "--ret-end", p["label_end"],
           "--delisted-ret", str(args.delisted_ret),
           "--buffer-days", str(args.buffer_days),
           "--gate-window", str(args.gate_window),
           "--feature-scores", args.feature_scores,
           "--rank-score", "none",
           "--emit-firings", str(out_file),
           "--progress", str(args.progress)]
    if args.sector_features:
        cmd.append("--sector-features")
    if args.chunk_size:
        cmd += ["--chunk-size", str(args.chunk_size)]
    return cmd


def pass2_cmd(files: list[Path], out_file: Path, args) -> list[str]:
    """跨窗 Pass2:每个 firings 文件=一对窗口,分窗判别 + 汇总共同点(极廉价,可反复换口径重算)。"""
    return [sys.executable, str(STUDY),
            "--from-firings", ",".join(str(f) for f in files),
            "--discriminate", "--per-window", "--label-basis", "winner",
            "--winner-basis", args.winner_basis,
            "--capture-top-pct", str(args.winner_top_pct),
            "--picks-per-day", str(args.picks_per_day),
            "--out", str(out_file)]


def tag_of(p: dict) -> str:
    return f"{p['signal_start']}_{p['signal_end']}__L{p['label_start']}_{p['label_end']}"


def expected_firings_header(args) -> dict:
    """本次运行要求 firings 头部匹配的关键参数(与 pass1_cmd / launch_point_study 写盘字段一致)。"""
    return {"entry_filter": args.entry_filter, "rank_score": "none",
            "feature_scores": args.feature_scores, "delisted_ret": args.delisted_ret,
            "universe": "sdata"}


def firings_reusable(f: Path, args) -> bool:
    """断点续跑校验:已有 firings 能否复用。两道闸,缺一即视为**未完成**,WARN 后重跑:
      ① JSON 可完整解析且含 records 键(上次 Pass1 失败/中断留下的截断文件不得当完成);
      ② 头部关键参数与本次一致(只认文件名会把旧参数跑出的结果当新参数复用,结论静默失真)。"""
    try:
        head = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[WARN] firings 损坏/截断({exc.__class__.__name__}),视为未完成重跑: {f.name}",
              file=sys.stderr)
        return False
    if not isinstance(head, dict) or "records" not in head:
        print(f"[WARN] firings 缺 records 键,视为未完成重跑: {f.name}", file=sys.stderr)
        return False
    diff = {k: (head.get(k), v) for k, v in expected_firings_header(args).items()
            if head.get(k) != v}
    if diff:
        print(f"[WARN] firings 参数与本次不一致,重跑不复用: {f.name} "
              + ", ".join(f"{k}: 文件={a!r} vs 本次={b!r}" for k, (a, b) in diff.items()),
              file=sys.stderr)
        return False
    return True


def survivorship_report(firings_files: list[Path], s_data_root: str,
                        data_source: str = "qlib",
                        today_codes: Optional[set] = None) -> dict:
    """幸存者偏差体检:样本里有多少只"当时在、今天已经没了"的票。

    为什么不能用 n_delisted 判断:它只统计**赢家窗内彻底没价格**的票,而 A 股退市是慢流程
    (ST→*ST→退市整理期),一只票正好死在某个 20~70 交易日窗口内本就稀有 ⇒ n_delisted=0 是
    预期行为,**不能**推出"退市股被剔除"。
    真正的判据是:qlib 宇宙(instruments/all.txt 并集,含退市股)减去**今天本地 vipdoc 实有**的票
    = 已摘牌队列;再看各窗 firings 的样本里落了多少只。落 0 才说明去偏失效(§3 首条)。
    """
    import json as _j
    import s_data as _sd  # noqa: PLC0415
    sub = "CSV_DATA" if data_source == "csv" else "Q_DATA"
    uni = set(_sd.list_universe(str(Path(s_data_root) / sub), source=data_source))
    if today_codes is None:
        try:
            sys.path.insert(0, str(TOOLS / "local_tdx"))
            import local_tdx_data as _ltd  # noqa: PLC0415
            today_codes = set(_ltd.list_local_vipdoc_codes(ashare_only=True))
        except Exception as exc:  # noqa: BLE001
            return {"error": f"无法列出今日本地宇宙: {exc}"}
    gone = uni - set(today_codes)                 # 宇宙里有、今天没有 = 已摘牌/退市
    out: dict = {"universe": len(uni), "today": len(today_codes), "gone_pool": len(gone),
                 "windows": []}
    for fp in firings_files:
        try:
            raw = _j.loads(Path(fp).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out["windows"].append({"file": Path(fp).name, "error": str(exc)})
            continue
        recs = raw if isinstance(raw, list) else (raw.get("records") or [])
        codes = {r.get("code") for r in recs if r.get("code")}
        with_sig = {r.get("code") for r in recs if (r.get("days") or [])}
        hit = codes & gone
        out["windows"].append({
            "file": Path(fp).name,
            "label": (f"{raw.get('start')}~{raw.get('end')}" if isinstance(raw, dict) else ""),
            "n_codes": len(codes), "n_with_signal": len(with_sig),
            "n_gone_in_sample": len(hit),
            "gone_share": round(len(hit) / len(codes), 4) if codes else None,
            "n_gone_with_signal": len(with_sig & gone),
            "n_delisted_flag": sum(1 for r in recs if r.get("delisted")),
        })
    lines = [f"qlib 宇宙 {out['universe']} 只;今日本地实有 {out['today']} 只;"
             f"**已摘牌队列 {out['gone_pool']} 只**(宇宙有、今天没有)",
             f"    {'窗口':<28} {'样本股':>7} {'有信号':>7} {'已摘牌在样本':>12} {'占比':>7} {'摘牌且有信号':>12} {'窗内消失':>9}"]
    for w in out["windows"]:
        if w.get("error"):
            lines.append(f"    {w['file']:<28} 读取失败: {w['error']}")
            continue
        lines.append(f"    {(w['label'] or w['file'])[:28]:<28} {w['n_codes']:>7} "
                     f"{w['n_with_signal']:>7} {w['n_gone_in_sample']:>12} "
                     f"{(w['gone_share'] or 0):>6.1%} {w['n_gone_with_signal']:>12} "
                     f"{w['n_delisted_flag']:>9}")
    zero = [w for w in out["windows"] if not w.get("error") and w["n_gone_in_sample"] == 0]
    if out["gone_pool"] == 0:
        lines.append("  ⚠️ 已摘牌队列为 0 ⇒ qlib 宇宙实际只含幸存者,**去偏无效**,"
                     "所有结论只能当乐观上界(§3 首条)")
    elif zero:
        lines.append(f"  ⚠️ {len(zero)} 个窗的样本里一只已摘牌股都没有 ⇒ 该窗去偏无效,单独标注")
    else:
        lines.append("  ✅ 各窗样本均含已摘牌股 ⇒ 飞刀留在样本内,'分不出赢家与飞刀'的结论成立")
    lines.append("  注:'窗内消失'=赢家窗完全无价格而按 --delisted-ret 计入的只数,"
                 "短窗内本就稀有,0 不代表去偏失效")
    out["text"] = "\n".join(lines)
    return out


def main(argv=None, runner=None) -> int:
    ap = argparse.ArgumentParser(description="空头段识别未来赢家:窗口枚举 → Pass1 逐对 → Pass2 汇总")
    ap.add_argument("--out-dir", default="06_logs/bear2long")
    ap.add_argument("--pairs-file", default="", help="复用已有窗口对 JSON(缺省则现算)")
    ap.add_argument("--min-bear-days", type=int, default=10)
    ap.add_argument("--min-long-days", type=int, default=20)
    ap.add_argument("--include-long-head-days", type=int, default=0,
                    help="⚠️>0 会让做多段头部信号的 label 含信号前涨幅,主结论用 0")
    ap.add_argument("--signal-span", choices=["adjacent", "since-prev-long"], default="adjacent")
    ap.add_argument("--regime-since", default="2015-01-01")
    ap.add_argument("--entry-filter", default="reversal_k")
    ap.add_argument("--feature-scores", default=DEFAULT_FEATURES)
    ap.add_argument("--sector-features", action="store_true")
    ap.add_argument("--data-source", choices=["qlib", "csv"], default="qlib")
    ap.add_argument("--s-data-root", default=os.environ.get("S_DATA_ROOT") or r"E:\S_DATA")
    ap.add_argument("--delisted-ret", type=float, default=-1.0)
    ap.add_argument("--buffer-days", type=int, default=60)
    ap.add_argument("--gate-window", type=int, default=120)
    ap.add_argument("--chunk-size", type=int, default=0)
    ap.add_argument("--progress", type=int, default=200)
    ap.add_argument("--winner-top-pct", type=float, default=50.0)
    ap.add_argument("--winner-basis", choices=["universe", "profitable"], default="profitable")
    ap.add_argument("--picks-per-day", type=int, default=3)
    ap.add_argument("--qlib-end", default=QLIB_END)
    ap.add_argument("--dry-run", action="store_true", help="只打印计划与命令,不执行")
    ap.add_argument("--force", action="store_true", help="已有 firings 也重跑(默认跳过=断点续跑)")
    ap.add_argument("--pass2-only", action="store_true", help="只做 Pass2 汇总(firings 已就绪)")
    ap.add_argument("--survivorship-report", action="store_true",
                    help="幸存者偏差体检:统计各窗样本里'当时在、今天已摘牌'的票数(不跑 Pass1/Pass2)")
    args = ap.parse_args(argv)
    run = runner or (lambda cmd: subprocess.run(cmd, check=False).returncode)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = BASE / out_dir
    if args.pairs_file:
        pairs = json.loads(Path(args.pairs_file).read_text(encoding="utf-8"))["window_pairs"]
    else:
        regime = bt.load_amv_regime(since=args.regime_since)
        if not regime:
            print("[ERR] 0AMV regime 为空(指南针数据不可用),无法枚举窗口", file=sys.stderr)
            return 2
        pairs = lp.bear_to_long_pairs(regime, min_bear_days=args.min_bear_days,
                                      min_long_days=args.min_long_days,
                                      include_long_head_days=args.include_long_head_days,
                                      signal_span=args.signal_span)
    keep, drop = usable_pairs(pairs, qlib_end=args.qlib_end)

    print(f"=== 窗口对:{len(pairs)} 对枚举 → {len(keep)} 对可用 / {len(drop)} 对剔除 ===")
    for p in keep:
        print(f"  [跑] 信号 {p['signal_start']}~{p['signal_end']} ({p.get('signal_days','?')}日)"
              f" → 赢家 {p['label_start']}~{p['label_end']} ({p['long_days']}日)")
    for p in drop:
        print(f"  [剔] 信号 {p['signal_start']}~{p['signal_end']} → 赢家 "
              f"{p['label_start']}~{p['label_end']}  ({p['reason']})")
    if not keep:
        print("[ERR] 无可用窗口对", file=sys.stderr)
        return 2
    if len(keep) < 4:
        print(f"[WARN] 仅 {len(keep)} 个独立窗口,跨窗一致性判定统计力很弱(结论只能当探索)",
              file=sys.stderr)

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)   # dry-run 只看计划,不落任何目录/文件
    files: list[Path] = []
    if args.survivorship_report:                     # 只体检,不跑 Pass1/Pass2
        existing = [out_dir / f"firings_{tag_of(p)}.json" for p in keep]
        existing = [f for f in existing if f.exists()]
        if not existing:
            print("[ERR] 没有可体检的 firings(先跑 Pass1)", file=sys.stderr)
            return 2
        rep = survivorship_report(existing, args.s_data_root, args.data_source)
        if rep.get("error"):
            print(f"[ERR] {rep['error']}", file=sys.stderr)
            return 2
        print("\n=== 幸存者偏差体检(样本里有多少'当时在、今天已摘牌'的票) ===")
        print(rep["text"])
        return 0
    rc_all = 0
    for p in keep:
        f = out_dir / f"firings_{tag_of(p)}.json"
        files.append(f)
        if args.pass2_only:
            continue
        if f.exists() and not args.force and firings_reusable(f, args):
            print(f"[skip] 已存在且参数一致,跳过(--force 可重跑): {f.name}")
            continue
        cmd = pass1_cmd(p, f, args)
        print("\n[pass1] " + " ".join(cmd))
        if args.dry_run:
            continue
        rc = run(cmd)
        if rc != 0:
            print(f"[ERR] Pass1 失败 rc={rc}: {f.name}(其余窗口继续;修好后重跑本脚本即续)",
                  file=sys.stderr)
            rc_all = 1
            files.pop()

    ready = [f for f in files if f.exists() or args.dry_run]
    if not ready:
        print("[ERR] 无可用 firings,跳过 Pass2", file=sys.stderr)
        return rc_all or 2
    agg_out = out_dir / "discriminate_bear_to_long.json"
    cmd2 = pass2_cmd(ready, agg_out, args)
    print("\n[pass2] " + " ".join(cmd2))
    if args.dry_run:
        return 0
    rc2 = run(cmd2)
    print(f"\n汇总输出:{agg_out}")
    return rc_all or rc2


if __name__ == "__main__":
    raise SystemExit(main())
