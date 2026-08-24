# -*- coding: utf-8 -*-
"""驱动"空头段识别未来赢家"研究:枚举窗口对 → 逐对跑 Pass1 → 跨窗 Pass2 汇总。

为什么要驱动脚本:窗口对有十几个,手抄十几条 Pass1 命令易错;且 Pass1 耗时长,中途失败必须能
**断点续跑**(已有 firings 文件默认跳过)。所有实际计算仍在 launch_point_study.py 里,本脚本只编排。

数据口径:本地 vipdoc/tdx 单源连续数据,旧 bundle 数据源的缺口与数据末尾护栏随之失效。
赢家窗超出最新交易日的窗口对不再预先剔除 —— Pass1 侧 tdx loader 加载不到数据
自然产出空记录,由下游(firings 空产物拒复用闸)兜住。

用法(先看计划,不跑):
  uv run python src/custos/research/run_bear_to_long_study.py --dry-run
真跑:
  uv run python src/custos/research/run_bear_to_long_study.py --out-dir artifacts/logs/bear2long
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

TOOLS = Path(__file__).resolve().parents[1]

from custos.core.paths import BASE, LOGS  # noqa: E402

from custos.research import backtest_factors as bt  # noqa: E402
from custos.research import launch_point_study as lp  # noqa: E402


# ⚠️ 2026-08-07 修：`launch_point_study` 已随研究脚本从 `screening/` 移到 `research/`，
# 而这行是用 Path 除法逐段拼出来的（不是一整条字符串字面量路径），
# 当时的替换脚本只按字符串匹配 ⇒ 漏了它。`--help` 冒烟也抓不到
# （路径只在真正执行子进程时才用到）。
STUDY = TOOLS / "research" / "launch_point_study.py"
# 真市值/总股本的历史起点(fetch_market_cap 二分探明)。用市值类特征时须据此再剔窗口。
try:
    from custos.datasource.local_tdx.fetch_market_cap import MV_START  # noqa: E402
except Exception:  # noqa: BLE001  取数模块缺失不应拖垮研究链
    MV_START = "2018-01-02"
DEFAULT_FEATURES = (
    "reversal_quality,momentum,low_vol,alpha101,alpha_pvcorr,"
    "kdj_j,s_shape,b1_pullback,invert_s_shape,s_reversal"
)


def usable_pairs(
    pairs: list[dict],
    require_market_cap: bool = False,
    mv_start: str = MV_START,
) -> tuple[list[dict], list[dict]]:
    """按数据可用性切分 (可用, 剔除并附 reason)。

    require_market_cap=True 时剔除**信号窗起点早于市值数据起点**的窗口对:
    `RPT_VALUEANALYSIS_DET` 的历史只到 2018-01-02(二分探明),用真市值/总股本做特征时
    2015~2017 的窗口无数据。不设默认以免静默改变既有窗口池(否则 12 窗会缩水)。
    赢家窗超出 tdx 最新交易日的窗口对不在此预剔:tdx 是连续单源,无固定数据末尾;
    这类对由 Pass1 侧 loader 空数据自然兜住(见模块 docstring)。
    """
    keep, drop = [], []
    for p in pairs:
        if require_market_cap and p["signal_start"] < mv_start:
            drop.append(
                {
                    **p,
                    "reason": f"信号窗早于市值数据起点 {mv_start}(真市值/总股本无数据)",
                }
            )
        else:
            keep.append(p)
    return keep, drop


def pass1_cmd(p: dict, out_file: Path, args) -> list[str]:
    """单对窗口的 Pass1 命令:信号窗 [signal_*] / 赢家窗 [label_*] 解耦 + 退市股按大亏计入。"""
    cmd = [
        sys.executable,
        str(STUDY),
        "--universe-local",
        "--entry-filter",
        args.entry_filter,
        "--start",
        p["signal_start"],
        "--end",
        p["signal_end"],
        "--ret-start",
        p["label_start"],
        "--ret-end",
        p["label_end"],
        "--delisted-ret",
        str(args.delisted_ret),
        "--buffer-days",
        str(args.buffer_days),
        "--gate-window",
        str(args.gate_window),
        "--feature-scores",
        args.feature_scores,
        "--rank-score",
        "none",
        "--emit-firings",
        str(out_file),
        "--progress",
        str(args.progress),
    ]
    if args.sector_features:
        cmd.append("--sector-features")
    if args.style_features:
        cmd.append("--style-features")
    if args.trade_sim:
        cmd.append("--trade-sim")
        cmd += ["--stop-pct", str(args.stop_pct), "--bbi-consec", str(args.bbi_consec)]
    if args.pit_features:  # A 组基本面特征(纯财务比率,不需市值)
        cmd.append("--pit-features")
        if args.pit_ledger:
            cmd += ["--pit-ledger", args.pit_ledger]
        if args.pit_visible_same_day:
            cmd.append("--pit-visible-same-day")
    if args.chunk_size:
        cmd += ["--chunk-size", str(args.chunk_size)]
    return cmd


def pass2_cmd(files: list[Path], out_file: Path, args) -> list[str]:
    """跨窗 Pass2:每个 firings 文件=一对窗口,分窗判别 + 汇总共同点(极廉价,可反复换口径重算)。"""
    return [
        sys.executable,
        str(STUDY),
        "--from-firings",
        ",".join(str(f) for f in files),
        "--discriminate",
        "--per-window",
        "--label-basis",
        "winner",
        "--winner-basis",
        args.winner_basis,
        "--capture-top-pct",
        str(args.winner_top_pct),
        "--picks-per-day",
        str(args.picks_per_day),
        "--out",
        str(out_file),
    ] + (["--exclude-zero-ret"] if args.exclude_zero_ret else [])


def tag_of(p: dict) -> str:
    return (
        f"{p['signal_start']}_{p['signal_end']}__L{p['label_start']}_{p['label_end']}"
    )


# 布尔型特征开关:旧 firings 没有这些键,`head.get(k)` 为 None。若按严格相等比对,
# **不开任何开关也会判"参数不一致"而把 12 窗全部重跑**(代价极大)。故这几个键按 bool() 归一,
# 缺失等价 False —— 只有真正开启开关时才判不一致、才重跑。
_BOOL_FINGERPRINT_KEYS = (
    "sector_features",
    "style_features",
    "trade_sim",
    "pit_features",
    "pit_visible_same_day",
)

# 缺键按默认容忍(后加的参数,旧 firings 必然是用默认值/无台账跑的):
# pit_ledger 路径(无=空串)、stop_pct/bbi_consec(trade-sim 出场参数默认 8.0/2)
_DEFAULT_TOLERANT = {"pit_ledger": "", "stop_pct": 8.0, "bbi_consec": 2}


def expected_firings_header(args) -> dict:
    """本次运行要求 firings 头部匹配的关键参数(与 pass1_cmd / launch_point_study 写盘字段一致)。

    ⚠️ 不含 `pit_ledger_n`:PIT 台账每季都会增长,进指纹会导致每次补数后全窗强制重跑;
    需要按新台账重算时显式 `--force`。台账条数仍会写进 firings 头部供追溯。
    `pit_ledger` **路径**进指纹(换台账文件必须重跑);`stop_pct`/`bbi_consec` 缺键按默认容忍
    (这两个参数后加,旧 firings 必然是用默认值跑的,None→默认是正确推断而非静默)。
    """
    return {
        "entry_filter": args.entry_filter,
        "rank_score": "none",
        "feature_scores": args.feature_scores,
        "delisted_ret": args.delisted_ret,
        "universe": "local",
        "sector_features": bool(args.sector_features),
        "style_features": bool(args.style_features),
        "trade_sim": bool(args.trade_sim),
        "pit_features": bool(args.pit_features),
        "pit_visible_same_day": bool(args.pit_visible_same_day),
        "pit_ledger": getattr(args, "pit_ledger", "") or "",
        "stop_pct": getattr(args, "stop_pct", 8.0),
        "bbi_consec": getattr(args, "bbi_consec", 2),
    }


_FIRINGS_HEAD_BYTES = 1 << 16  # 头部元数据探测窗口（"records" 恒为最后一个键）


def _firings_head(f: Path) -> Optional[dict]:
    """只读文件头部拿元数据（不解析 records 正文）。

    落盘格式把 ``"records"`` 写在最后一个键，且是原子落盘（tmp→replace），所以
    "头部可解析 + 尾部收尾正确" 已足以判定文件完整。为判定"是否为空产物"用头部
    自带的 ``n_signal_days``（生产者写的就是 sum(len(days))，与逐条数完全同值）。
    拿不到（老文件无 n_signal_days / 结构不符 / 找不到 records 键）→ 返回 None，
    调用方回退全量解析，保持原有严格语义。
    """
    try:
        size = f.stat().st_size
        with f.open("rb") as fh:
            head = fh.read(_FIRINGS_HEAD_BYTES)
            tail_at = max(0, size - 64)
            fh.seek(tail_at)
            tail = fh.read(64)
    except OSError:
        return None
    try:
        head_s = head.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    marker = head_s.rfind('"records"')
    if marker <= 0:
        return None
    prefix = head_s[:marker].rstrip().rstrip(",")
    try:
        meta = json.loads(prefix + "}")
    except ValueError:
        return None
    if not isinstance(meta, dict) or meta.get("n_signal_days") is None:
        return None
    if not tail.strip().endswith(b"]}"):  # 截断/半截 JSON
        return None
    meta["_records_key_present"] = True
    return meta


def firings_reusable(f: Path, args) -> bool:
    """断点续跑校验:已有 firings 能否复用。三道闸,缺一即视为**未完成**,WARN 后重跑:
      ① JSON 可完整解析且含 records 键(上次 Pass1 失败/中断留下的截断文件不得当完成);
      ② **非空**:0 条记录或 0 个信号日的 firings 是"数据源没挂上"的产物,不是研究结论
         (审计 E9:原先只要 JSON 完整+参数一致就永久跳过,一整轮 12 窗空跑无人察觉);
      ③ 头部关键参数与本次一致(只认文件名会把旧参数跑出的结果当新参数复用,结论静默失真)。

    ⚠️ 性能(审计):firings 是全市场×多窗的大 JSON,仅为"能否复用"这一个布尔量把整份
    records 解析进内存纯属浪费。现在先走 ``_firings_head`` 只读头部 + 校验尾部收尾;
    头部探测不成立时才回退全量解析（老文件无 n_signal_days、结构变更等），三道闸的
    结论与全量解析一致。代价:文件**中段**被改坏而头尾完好的情形不再被发现——落盘是
    原子的(tmp→replace),这种状态不由本流程产生。
    """
    head = _firings_head(f)
    if head is None:
        return _firings_reusable_full(f, args)
    if not head.get("n_signal_days"):
        print(
            f"[WARN] firings 为空(头部 n_signal_days={head.get('n_signal_days')}),"
            f"视为未完成重跑: {f.name} —— 空产物多半是数据源没挂上,不得当研究结论复用",
            file=sys.stderr,
        )
        return False
    return _firings_header_matches(head, args, f)


def _firings_reusable_full(f: Path, args) -> bool:
    """全量解析版校验（头部探测不成立时的回退，语义与改动前完全一致）。"""
    try:
        head = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(
            f"[WARN] firings 损坏/截断({exc.__class__.__name__}),视为未完成重跑: {f.name}",
            file=sys.stderr,
        )
        return False
    if not isinstance(head, dict) or "records" not in head:
        print(f"[WARN] firings 缺 records 键,视为未完成重跑: {f.name}", file=sys.stderr)
        return False
    recs = head.get("records") or []
    n_days = sum(len(r.get("days") or []) for r in recs if isinstance(r, dict))
    if not recs or not n_days:
        print(
            f"[WARN] firings 为空({len(recs)} 股 / {n_days} 信号日),视为未完成重跑: {f.name}"
            " —— 空产物多半是数据源没挂上,不得当研究结论复用",
            file=sys.stderr,
        )
        return False
    return _firings_header_matches(head, args, f)


def _firings_header_matches(head: dict, args, f: Path) -> bool:
    """闸③:头部关键参数与本次一致。"""
    diff = {}
    for k, v in expected_firings_header(args).items():
        got = head.get(k)
        if k in _BOOL_FINGERPRINT_KEYS:
            if bool(got) != bool(v):  # 缺失键等价 False,不误伤旧 firings
                diff[k] = (got, v)
        elif k in _DEFAULT_TOLERANT:
            if (got if got is not None else _DEFAULT_TOLERANT[k]) != v:
                diff[k] = (got, v)
        elif got != v:
            diff[k] = (got, v)
    if diff:
        print(
            f"[WARN] firings 参数与本次不一致,重跑不复用: {f.name} "
            + ", ".join(f"{k}: 文件={a!r} vs 本次={b!r}" for k, (a, b) in diff.items()),
            file=sys.stderr,
        )
        return False
    return True


def zero_ret_codes(recs: list[dict]) -> set:
    """赢家窗收益**恰好为 0** 的代码集合(与 lp.drop_zero_ret 同口径)。

    这类记录窗内确有 >=2 根有效 K 线(window_return 的前置条件),只是首末收盘一分不差,
    故不是\"无数据被丢\",而是**长期停牌/退市整理期/极低流动性**的直线样本。
    """
    out = set()
    for r in recs:
        v = r.get("ret")
        if v is None or not r.get("code"):
            continue
        try:
            if float(v) == 0.0:
                out.add(r["code"])
        except (TypeError, ValueError):
            continue
    return out


def board_mix(codes) -> dict:
    """按上市板计数(降序)。用来判\"零收益样本到底是不是集中在北交所\"。"""
    mix: dict[str, int] = {}
    for c in codes:
        b = lp.board_of(c)
        mix[b] = mix.get(b, 0) + 1
    return dict(sorted(mix.items(), key=lambda kv: -kv[1]))


def classify_zero_ret_bars(bars, start: str, end: str, few_bars: int = 5) -> dict:
    """按 **volume** 判定单只零收益样本的成因。这是"事实"判据,`ret==0` 只是症状。

    tdx loader 只按收盘价有效性过滤,**volume 加载了却不参与过滤** —— 长期停牌/无成交的
    日子只要记了前收价就会留在 frame 里,首末收盘一分不差 ⇒ ret 恰好 0。分四类:
      - suspended_all  : 窗内 K 线 volume 全为 0 ⇒ 全程停牌/无成交(剔除正确)
      - suspended_part : 有部分零量日且收盘恒定 ⇒ 断续停牌(剔除正确)
      - few_bars       : 窗内有效 K 线 < few_bars 根 ⇒ 窗内新上市/末期退市(不该按区间收益判赢家)
      - traded_flat    : 全程有成交但首末同价 ⇒ 真实收平,极罕见;**这类要单独查**
    返回 {kind, n_bars, n_zero_vol, first_close, last_close};窗内无 K 线返回 kind=no_bars。
    """
    if bars is None or len(bars) == 0:
        return {"kind": "no_bars", "n_bars": 0, "n_zero_vol": 0}
    d = bars.copy()
    col = next((c for c in ("date", "datetime") if c in d.columns), None)
    if col is None:
        return {"kind": "no_date_col", "n_bars": int(len(d)), "n_zero_vol": 0}
    d["_d"] = d[col].astype(str).str[:10]
    win = d[(d["_d"] >= start) & (d["_d"] <= end)]
    n = int(len(win))
    if n == 0:
        return {"kind": "no_bars", "n_bars": 0, "n_zero_vol": 0}
    vol = win["volume"].astype(float) if "volume" in win.columns else None
    n_zero = int((vol == 0).sum()) if vol is not None else 0
    closes = win["close"].astype(float)
    out: dict[str, Any] = {
        "n_bars": n,
        "n_zero_vol": n_zero,
        "first_close": round(float(closes.iloc[0]), 4),
        "last_close": round(float(closes.iloc[-1]), 4),
    }
    if vol is None:
        out["kind"] = "no_volume_col"
    elif n_zero == n:
        out["kind"] = "suspended_all"
    elif n < few_bars:
        out["kind"] = "few_bars"
    elif n_zero:
        out["kind"] = "suspended_part"
    else:
        out["kind"] = "traded_flat"
    return out


def _zero_ret_window(
    fp: Path, loader, few_bars: int, max_codes: int, out: dict
) -> None:
    """单窗诊断:结果追加进 out["windows"],并按 kind 累计 out["kind_total"]。"""
    import json as _j

    try:
        raw = _j.loads(Path(fp).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["windows"].append({"file": Path(fp).name, "error": str(exc)})
        return
    recs = raw if isinstance(raw, list) else (raw.get("records") or [])
    codes = sorted(zero_ret_codes(recs))
    if max_codes:
        codes = codes[:max_codes]
    rs = raw.get("ret_start") if isinstance(raw, dict) else None
    re_ = raw.get("ret_end") if isinstance(raw, dict) else None
    w: dict = {
        "file": Path(fp).name,
        "label": f"{rs}~{re_}",
        "n_zero_ret": len(codes),
        "kinds": {},
        "samples": {},
    }
    if not codes:
        w["error"] = "无零收益样本"
        out["windows"].append(w)
        return
    if not (rs and re_):
        # 缺 ret_start/ret_end 时**不得**回退信号窗(会把信号窗当赢家窗诊断,整窗错位且无提示)
        w["error"] = (
            "firings 缺 ret_start/ret_end(旧格式?),无法定位赢家窗,请重跑该窗 Pass1"
        )
        out["windows"].append(w)
        return
    try:
        bars = loader(codes, rs, re_) or {}
    except Exception as exc:  # noqa: BLE001
        w["error"] = f"重载 K 线失败: {exc}"
        out["windows"].append(w)
        return
    for c in codes:
        info = classify_zero_ret_bars(bars.get(c), rs, re_, few_bars=few_bars)
        kind = info["kind"]
        w["kinds"][kind] = w["kinds"].get(kind, 0) + 1
        out["kind_total"][kind] = out["kind_total"].get(kind, 0) + 1
        w["samples"].setdefault(kind, []).append({"code": c, **info})
    for k in w["samples"]:
        w["samples"][k] = w["samples"][k][:3]  # 每类留 3 个样例便于人工核
    w["board_mix"] = board_mix(codes)
    out["windows"].append(w)


def _zero_ret_render(out: dict) -> None:
    """按各窗结果渲染诊断文本,并写入 out["n_total"] / out["text"]。"""
    lines = [
        f"    {'窗口':<28} {'零收益':>7} {'全程停牌':>9} {'断续停牌':>9} "
        f"{'K线过少':>9} {'有量收平':>9}"
    ]
    for w in out["windows"]:
        if w.get("error"):
            lines.append(f"    {(w.get('label') or w['file'])[:28]:<28} {w['error']}")
            continue
        k = w["kinds"]
        lines.append(
            f"    {w['label'][:28]:<28} {w['n_zero_ret']:>7} "
            f"{k.get('suspended_all', 0):>9} {k.get('suspended_part', 0):>9} "
            f"{k.get('few_bars', 0):>9} {k.get('traded_flat', 0):>9}"
        )
    tot = out["kind_total"]
    n_all = sum(tot.values())
    out["n_total"] = n_all
    if n_all:
        susp = tot.get("suspended_all", 0) + tot.get("suspended_part", 0)
        flat = tot.get("traded_flat", 0)
        lines.append(
            "  成因合计:"
            + "、".join(
                f"{k} {v} 只({v / n_all:.0%})"
                for k, v in sorted(tot.items(), key=lambda x: -x[1])
            )
        )
        if susp / n_all >= 0.8:
            lines.append(
                f"  ✅ 停牌/无成交占 {susp / n_all:.0%} ⇒ 零收益样本确系僵尸,"
                "`--exclude-zero-ret` 剔除对象正确"
            )
        elif flat / n_all >= 0.5:
            lines.append(
                f"  ⚠️ **有量收平占 {flat / n_all:.0%}** ⇒ 这些票窗内正常成交、"
                "只是首末同价,不是停牌僵尸;`--exclude-zero-ret` 剔错了对象,须改判据"
            )
        else:
            lines.append(
                f"  ⚠️ 成因混杂(停牌 {susp / n_all:.0%} / 有量收平 {flat / n_all:.0%})"
                " ⇒ 不能一刀切剔除,须按 kind 分别处理"
            )
    else:
        lines.append("  无零收益样本可诊断")
    out["text"] = "\n".join(lines)


def zero_ret_diagnose(
    firings_files: list[Path], loader, few_bars: int = 5, max_codes: int = 0
) -> dict:
    """逐窗诊断零收益样本成因。loader(codes, start, end) → {code: DataFrame}。

    只重载 ret==0 的那些票(实跑最大一窗约 800 只),不重跑 Pass1。
    结论行按占比给判决:traded_flat 占多数说明不是停牌所致,`--exclude-zero-ret` 剔错了对象。
    """
    out: dict = {"windows": [], "kind_total": {}}
    for fp in firings_files:
        _zero_ret_window(fp, loader, few_bars, max_codes, out)
    _zero_ret_render(out)
    return out


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="空头段识别未来赢家:窗口枚举 → Pass1 逐对 → Pass2 汇总"
    )
    ap.add_argument("--out-dir", default=str(LOGS / "bear2long"))
    ap.add_argument("--pairs-file", default="", help="复用已有窗口对 JSON(缺省则现算)")
    ap.add_argument("--min-bear-days", type=int, default=10)
    ap.add_argument("--min-long-days", type=int, default=20)
    ap.add_argument(
        "--include-long-head-days",
        type=int,
        default=0,
        help="⚠️>0 会让做多段头部信号的 label 含信号前涨幅,主结论用 0",
    )
    ap.add_argument(
        "--signal-span", choices=["adjacent", "since-prev-long"], default="adjacent"
    )
    ap.add_argument("--regime-since", default="2015-01-01")
    ap.add_argument("--entry-filter", default="reversal_k")
    ap.add_argument("--feature-scores", default=DEFAULT_FEATURES)
    ap.add_argument("--sector-features", action="store_true")
    ap.add_argument(
        "--style-features",
        action="store_true",
        help="Pass1:追加风格特征 f_board_code(上市板)与 f_amount20(20日均成交额)",
    )
    ap.add_argument(
        "--trade-sim",
        action="store_true",
        help="Pass1:每个信号另算一笔**本策略买卖规则**下的收益(sim_ret/sim_reason),"
        "供 Pass2 --coverage 做双口径对比",
    )
    ap.add_argument(
        "--stop-pct",
        type=float,
        default=8.0,
        help="--trade-sim 的固定止损百分比(默认8;透传 Pass1 并进 firings 指纹)",
    )
    ap.add_argument(
        "--bbi-consec",
        type=int,
        default=2,
        help="--trade-sim 的 BBI 连破日数(默认2;透传 Pass1 并进 firings 指纹)",
    )
    ap.add_argument(
        "--pit-features",
        action="store_true",
        help="Pass1:追加 A 组基本面特征(纯财务比率,不需市值,2015 起 12 窗全可用):"
        "f_roe/f_gross_margin/f_ocf_ps/f_deduct_ratio/f_rev_yoy/f_np_yoy/"
        "f_pit_lag_days。需先建 PIT 台账并 --verify 确认无缺期",
    )
    ap.add_argument(
        "--pit-ledger",
        default="",
        help="PIT 财务台账路径(缺省用 data/fundamentals/pit_financials.jsonl)",
    )
    ap.add_argument(
        "--pit-visible-same-day",
        action="store_true",
        help="把公告当日算作可见(默认次日;公告多在盘后发布)",
    )
    ap.add_argument("--delisted-ret", type=float, default=-1.0)
    ap.add_argument("--buffer-days", type=int, default=60)
    ap.add_argument("--gate-window", type=int, default=120)
    ap.add_argument("--chunk-size", type=int, default=0)
    ap.add_argument("--progress", type=int, default=200)
    ap.add_argument("--winner-top-pct", type=float, default=50.0)
    ap.add_argument(
        "--winner-basis", choices=["universe", "profitable"], default="profitable"
    )
    ap.add_argument("--picks-per-day", type=int, default=3)
    ap.add_argument(
        "--exclude-zero-ret",
        action="store_true",
        help="Pass2:剔除赢家窗收益恰好为 0 的僵尸样本(停牌/退市整理期 forward-fill)",
    )
    ap.add_argument("--dry-run", action="store_true", help="只打印计划与命令,不执行")
    ap.add_argument(
        "--force", action="store_true", help="已有 firings 也重跑(默认跳过=断点续跑)"
    )
    ap.add_argument(
        "--pass2-only", action="store_true", help="只做 Pass2 汇总(firings 已就绪)"
    )
    ap.add_argument(
        "--require-market-cap",
        action="store_true",
        help=f"额外剔除信号窗起点早于市值数据起点({MV_START})的窗口对——用真市值/总股本做特征时 2015~2017 无数据(默认不开,以免静默缩小窗口池)",
    )
    ap.add_argument(
        "--zero-ret-report",
        action="store_true",
        help="零收益样本成因诊断:按 volume 分全程停牌/断续停牌/K线过少/有量收平"
        "(只重载 ret==0 的票,不跑 Pass1/Pass2)",
    )
    ap.add_argument(
        "--zero-ret-max-codes",
        type=int,
        default=0,
        help="每窗最多诊断多少只零收益样本(0=全量;大窗先抽样时用)",
    )
    return ap


def _load_pairs(args) -> Optional[list]:
    """取窗口对:--pairs-file 复用现成 JSON,否则从 0AMV regime 现算;regime 空返回 None。"""
    if args.pairs_file:
        return json.loads(Path(args.pairs_file).read_text(encoding="utf-8"))[
            "window_pairs"
        ]
    regime = bt.load_amv_regime(since=args.regime_since)
    if not regime:
        print("[ERR] 0AMV regime 为空(指南针数据不可用),无法枚举窗口", file=sys.stderr)
        return None
    return lp.bear_to_long_pairs(
        regime,
        min_bear_days=args.min_bear_days,
        min_long_days=args.min_long_days,
        include_long_head_days=args.include_long_head_days,
        signal_span=args.signal_span,
    )


def _print_plan(n_pairs: int, keep: list[dict], drop: list[dict]) -> None:
    """打印窗口计划:可用对逐条 [跑],剔除对逐条 [剔] 并附原因。"""
    print(f"=== 窗口对:{n_pairs} 对枚举 → {len(keep)} 对可用 / {len(drop)} 对剔除 ===")
    for p in keep:
        print(
            f"  [跑] 信号 {p['signal_start']}~{p['signal_end']} ({p.get('signal_days', '?')}日)"
            f" → 赢家 {p['label_start']}~{p['label_end']} ({p['long_days']}日)"
        )
    for p in drop:
        print(
            f"  [剔] 信号 {p['signal_start']}~{p['signal_end']} → 赢家 "
            f"{p['label_start']}~{p['label_end']}  ({p['reason']})"
        )


def _existing_firings(out_dir: Path, keep: list[dict]) -> list[Path]:
    """按窗口对拼 firings 路径并筛出已存在的(诊断分支用)。"""
    existing = [out_dir / f"firings_{tag_of(p)}.json" for p in keep]
    return [f for f in existing if f.exists()]


def _zero_ret_report_cli(args, out_dir: Path, keep: list[dict]) -> int:
    """--zero-ret-report 分支:只诊断零收益成因,不跑 Pass1/Pass2。"""
    existing = _existing_firings(out_dir, keep)
    if not existing:
        print("[ERR] 没有可诊断的 firings(先跑 Pass1)", file=sys.stderr)
        return 2

    # tdx loader 签名 (codes, count, start, end),适配成诊断约定的 (codes, start, end)。
    # ⚠️ count 必须给足:底层 get_ohlcv_table 会 .tail(count),默认量级(2000 根)只到 ~2018,
    # 而本工具 --regime-since 默认 2015-01-01,不放大 count 会把早期窗口静默截断。
    def _load(codes, start, end):
        return bt._load_bars_local(codes, 100000, start=start, end=end)

    rep = zero_ret_diagnose(existing, _load, max_codes=args.zero_ret_max_codes)
    print("\n=== 零收益样本成因诊断(按 volume 判停牌,ret==0 只是症状) ===")
    print(rep["text"])
    return 0


def _run_pass1(args, run, out_dir: Path, keep: list[dict]) -> tuple[list[Path], int]:
    """Pass1 逐窗:断点续跑(可复用则跳过);返回 (候选 firings 列表, 累计失败码)。"""
    rc_all = 0
    files: list[Path] = []
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
            print(
                f"[ERR] Pass1 失败 rc={rc}: {f.name}(其余窗口继续;修好后重跑本脚本即续)",
                file=sys.stderr,
            )
            rc_all = 1
            files.pop()
    return files, rc_all


def _run_passes(args, run, out_dir: Path, keep: list[dict]) -> int:
    """Pass1 逐窗 → Pass2 跨窗汇总。"""
    files, rc_all = _run_pass1(args, run, out_dir, keep)
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


def main(argv=None, runner=None) -> int:
    args = _build_parser().parse_args(argv)
    run = runner or (lambda cmd: subprocess.run(cmd, check=False).returncode)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = BASE / out_dir
    pairs = _load_pairs(args)
    if pairs is None:
        return 2
    keep, drop = usable_pairs(pairs, require_market_cap=args.require_market_cap)
    _print_plan(len(pairs), keep, drop)
    if not keep:
        print("[ERR] 无可用窗口对", file=sys.stderr)
        return 2
    if len(keep) < 4:
        print(
            f"[WARN] 仅 {len(keep)} 个独立窗口,跨窗一致性判定统计力很弱(结论只能当探索)",
            file=sys.stderr,
        )

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)  # dry-run 只看计划,不落任何目录/文件
    if args.zero_ret_report:  # 只诊断零收益成因,不跑 Pass1/Pass2
        return _zero_ret_report_cli(args, out_dir, keep)
    return _run_passes(args, run, out_dir, keep)


if __name__ == "__main__":
    raise SystemExit(main())
