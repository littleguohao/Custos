# -*- coding: utf-8 -*-
"""S_shape 因子走查回测校准（walk-forward，纯分析、只读本地日线、绝不触碰管线）。

回答的问题：S_shape v3.0 的 S**（及各分项、建议档）能否区分"后市涨/跌"？
用于把 s_shape.py 里那些**待回测/猜测阈值**校准到有胜率与 MFE/MAE 支撑的值。

无未来函数：对每个 (股票, as-of 交易日 i)，只用 df[:i+1]（含当日）算 compute_s_shape，
前向指标只看 df[i+1 : i+H]（严格未来），两者绝不重叠。

CLI（在有本地通达信日线的机器上跑）::

    uv run python 07_tools/screening/backtest_factors.py --codes 600000,000001 --count 500 \
        --horizons 5,10,20 --out 01_data/screening/backtest_s_shape.json

评估逻辑与数据加载解耦：evaluate() 接收 {code: DataFrame}，便于单测注入合成 bars。
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

_SCREEN_DIR = Path(__file__).resolve().parent
_TOOLS = _SCREEN_DIR.parent
for _p in (str(_TOOLS), str(_SCREEN_DIR), str(_TOOLS / "local_tdx")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from s_shape import compute_s_shape, compute_s_reversal, SSHAPE_MIN_BARS, SSTAR_STRONG, SSTAR_MID  # noqa: E402

try:
    from technical_monitor import kdj as _kdj  # noqa: E402
except Exception:  # noqa: BLE001
    _kdj = None

J_LOW_THRESHOLD = 13.0


def j_low_gate(df_slice: pd.DataFrame) -> bool:
    """as-of 切片当日 KDJ 的 J<13（B1 买点区）。kdj 不可用时视为不通过。"""
    if _kdj is None:
        return False
    r = _kdj(df_slice)
    return bool(r.get("available") and r.get("j") is not None and r["j"] < J_LOW_THRESHOLD)


ENTRY_GATES: dict[str, Optional[Callable[[pd.DataFrame], bool]]] = {
    "none": None,        # 每根 K 线都当信号（全市场基线）
    "j_low": j_low_gate,  # 只在 J<13 入场区评估（B1 真买点）
}

HORIZONS_DEFAULT = (5, 10, 20)


def _components(r: dict) -> dict:
    return {k: (v or {}).get("points") for k, v in (r.get("components") or {}).items()}


def _sc_s_shape(df: pd.DataFrame, code: str):
    r = compute_s_shape(df, code)
    if not r.get("available"):
        return None
    return {"score": r["s_star"], "suggestion": r["suggestion"],
            "aux": {"s_shape": r["s_shape"], "delta": r["delta"], "penalty": r["penalty"]},
            "components": _components(r)}


def _sc_s_reversal(df: pd.DataFrame, code: str):
    r = compute_s_reversal(df, code)
    if not r.get("available"):
        return None
    return {"score": r["s_reversal"], "suggestion": r["suggestion"], "aux": {},
            "components": _components(r)}


def _sc_invert_s_shape(df: pd.DataFrame, code: str):
    r = compute_s_shape(df, code)
    if not r.get("available"):
        return None
    inv = round(100.0 - float(r["s_star"]), 1)
    sug = "可买" if inv >= 70 else ("观望" if inv >= 60 else "不买")
    return {"score": inv, "suggestion": sug, "aux": {"s_shape_star": r["s_star"]},
            "components": _components(r)}


# 可选打分器：同一批信号可跑三方对比（突破式 vs 买弱式 vs 反转突破分）
SCORERS = {"s_shape": _sc_s_shape, "s_reversal": _sc_s_reversal, "invert_s_shape": _sc_invert_s_shape}


def sample_codes(all_codes: list[str], n: int, seed: int = 0) -> list[str]:
    """从全 A 代码列表随机抽 N 只（带 seed 可复现），用于代表性样本校准。

    n<=0 或 n>=总数 → 返回全部（去空、去重、排序）。
    """
    codes = sorted({str(c).strip() for c in all_codes if str(c).strip()})
    if n <= 0 or n >= len(codes):
        return codes
    return sorted(random.Random(seed).sample(codes, n))


def forward_metrics(df: pd.DataFrame, i: int, horizon: int) -> dict[str, Any]:
    """as-of 第 i 根后、未来 horizon 根内的前向收益/MFE/MAE（严格只看 i+1..i+H）。

    入场基准＝第 i 根收盘价；前向窗口＝df[i+1 : i+horizon]（不含 i，杜绝未来泄漏）。
    """
    n = len(df)
    if i < 0 or i >= n - 1:
        return {"available": False, "reason": "无未来K线"}
    entry = float(df["close"].iloc[i])
    if not entry:
        return {"available": False, "reason": "入场价为0"}
    j = min(i + horizon, n - 1)
    fut = df.iloc[i + 1:j + 1]
    if fut.empty:
        return {"available": False, "reason": "前向窗口为空"}
    last = float(fut["close"].iloc[-1])
    hi = float(fut["high"].max())
    lo = float(fut["low"].min())
    return {
        "available": True,
        "bars": len(fut),
        "fwd_return": last / entry - 1,
        "mfe": hi / entry - 1,   # 最大有利偏移
        "mae": lo / entry - 1,   # 最大不利偏移
    }


def _liquidity_yi(df: pd.DataFrame, win: int = 20) -> Optional[float]:
    """近 win 日均成交额(亿元)；无 amount 列返回 None。用于回测里评估流动性因子 lift。"""
    if "amount" not in df.columns or len(df) == 0:
        return None
    amt = df["amount"].astype(float).to_numpy()
    return round(float(amt[-win:].mean()) / 1e8, 4)


def evaluate(
    bars_by_code: dict[str, pd.DataFrame],
    horizons: tuple[int, ...] = HORIZONS_DEFAULT,
    min_bars: int = SSHAPE_MIN_BARS,
    step: int = 1,
    max_signals_per_code: Optional[int] = None,
    entry_gate: Optional[Callable[[pd.DataFrame], bool]] = None,
    scorer: Optional[Callable[[pd.DataFrame, str], Optional[dict]]] = None,
) -> list[dict[str, Any]]:
    """逐股逐日走查：as-of 切片算打分，配前向指标。返回逐条记录（可复盘）。

    entry_gate(df_slice)->bool 若提供，只在返回 True 的 as-of 日评估（如 J<13 买点区）。
    scorer(df_slice, code)->{"score","suggestion","aux","components"} 或 None（默认 s_shape）。
    记录字段 s_star 存所选打分器的分数（沿用旧字段名，summarize/矩阵零改动）。
    """
    scorer = scorer or _sc_s_shape
    records: list[dict[str, Any]] = []
    for code, raw in bars_by_code.items():
        if raw is None or len(raw) == 0:
            continue
        df = raw.sort_values("date").reset_index(drop=True)
        n = len(df)
        emitted = 0
        for i in range(min_bars, n - 1, max(1, step)):
            slice_df = df.iloc[:i + 1]  # 只含 0..i（含当日），无未来
            if entry_gate is not None and not entry_gate(slice_df):
                continue
            res = scorer(slice_df, code)
            if res is None:
                continue
            rec: dict[str, Any] = {
                "code": code,
                "date": str(df["date"].iloc[i])[:10],
                "s_star": res["score"],
                "suggestion": res.get("suggestion"),
            }
            rec.update(res.get("aux") or {})
            for k, v in (res.get("components") or {}).items():
                rec[f"c_{k}"] = v
            rec["c_liquidity"] = _liquidity_yi(slice_df)  # 流动性(亿元)：可历史回测的正交因子
            for h in horizons:
                fm = forward_metrics(df, i, h)  # 只用到 i+1..i+H
                rec[f"ret{h}"] = fm.get("fwd_return")
                rec[f"mfe{h}"] = fm.get("mfe")
                rec[f"mae{h}"] = fm.get("mae")
            records.append(rec)
            emitted += 1
            if max_signals_per_code and emitted >= max_signals_per_code:
                break
    return records


def _stats(rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    """一组记录在给定 horizon 上的胜率/均值收益/中位 MFE-MAE。"""
    rk, mk, ak = f"ret{horizon}", f"mfe{horizon}", f"mae{horizon}"
    rets = [r[rk] for r in rows if r.get(rk) is not None]
    mfes = [r[mk] for r in rows if r.get(mk) is not None]
    maes = [r[ak] for r in rows if r.get(ak) is not None]
    if not rets:
        return {"n": 0}
    wins = sum(1 for x in rets if x > 0)
    return {
        "n": len(rets),
        "win_rate": round(wins / len(rets), 4),
        "avg_return": round(statistics.mean(rets), 4),
        "median_return": round(statistics.median(rets), 4),
        "median_mfe": round(statistics.median(mfes), 4) if mfes else None,
        "median_mae": round(statistics.median(maes), 4) if maes else None,
    }


def summarize(records: list[dict[str, Any]], horizon: int = 10) -> dict[str, Any]:
    """按 S** 档 / 建议 / 分项命中分组统计，输出校准视图。

    关键校准问题：'可买(S**≥70)' 的前向胜率/收益是否显著高于 '不买(<60)'？
    某分项(如 pocket_pivot/pivot)命中 vs 未命中是否有正向 lift？
    """
    bands = [
        ("A_可买(>=70)", 70.0, 1e9),
        ("B_观望(60-70)", 60.0, 70.0),
        ("C_中(40-60)", 40.0, 60.0),
        ("D_弱(<40)", -1e9, 40.0),
    ]
    by_band = []
    for label, lo, hi in bands:
        rows = [r for r in records if r.get("s_star") is not None and lo <= r["s_star"] < hi]
        by_band.append({"band": label, **_stats(rows, horizon)})

    by_suggestion = {}
    for sug in ("可买", "观望", "不买"):
        rows = [r for r in records if r.get("suggestion") == sug]
        by_suggestion[sug] = _stats(rows, horizon)

    # 分项命中 lift：分项得分 > 0 视为命中，比较命中/未命中两组
    comp_keys = [k for k in (records[0].keys() if records else []) if k.startswith("c_")]
    by_component = {}
    for ck in comp_keys:
        hit = [r for r in records if (r.get(ck) or 0) > 0]
        miss = [r for r in records if not (r.get(ck) or 0) > 0]
        by_component[ck] = {"hit": _stats(hit, horizon), "miss": _stats(miss, horizon)}

    return {
        "horizon": horizon,
        "total_signals": len(records),
        "sstar_level_thresholds": {"strong": SSTAR_STRONG, "mid": SSTAR_MID},
        "by_sstar_band": by_band,
        "by_suggestion": by_suggestion,
        "by_component_hit": by_component,
        "note": "阈值/权重待回测：若 可买 组胜率与均值收益未显著高于 不买 组，"
                "或某分项 hit 不优于 miss，则该阈值/权重需重估（见 s_shape.py 顶部常量）。",
    }


def summarize_multi(records: list[dict[str, Any]], horizons: tuple[int, ...]) -> dict[int, dict]:
    """多 horizon 汇总：{h: summarize(records, h)}，用于看反转是否随周期翻转。"""
    return {h: summarize(records, h) for h in horizons}


def horizon_band_matrix(records: list[dict[str, Any]], horizons: tuple[int, ...]) -> dict[str, Any]:
    """S** 档 × horizon 的胜率/均收益矩阵（诊断：高分档是否在长周期翻正）。"""
    bands = ["A_可买(>=70)", "B_观望(60-70)", "C_中(40-60)", "D_弱(<40)"]
    multi = summarize_multi(records, horizons)
    win: dict[str, dict] = {b: {} for b in bands}
    avg: dict[str, dict] = {b: {} for b in bands}
    for h in horizons:
        by = {x["band"]: x for x in multi[h]["by_sstar_band"]}
        for b in bands:
            cell = by.get(b, {})
            win[b][h] = cell.get("win_rate")
            avg[b][h] = cell.get("avg_return")
    lines = ["S**档 \\ horizon(日): " + "  ".join(f"H{h}" for h in horizons)]
    for b in bands:
        wr = "  ".join(f"{win[b][h] * 100:.1f}%" if win[b][h] is not None else "  -  " for h in horizons)
        ar = "  ".join(f"{avg[b][h] * 100:+.2f}%" if avg[b][h] is not None else "  -  " for h in horizons)
        lines.append(f"  {b:<14} 胜率 {wr}")
        lines.append(f"  {'':<14} 均收 {ar}")
    return {"win_rate": win, "avg_return": avg, "text": "\n".join(lines)}


def sweep_threshold(records: list[dict[str, Any]], horizon: int = 10,
                    cutoffs: tuple[int, ...] = (50, 55, 60, 65, 70, 75, 80)) -> dict[str, Any]:
    """扫描"分数 >= cutoff"分组的胜率/均收益，用于校准"可买"门槛（务必在全量数据上做，
    小样本上调门槛=过拟合）。返回每个 cutoff 的 n/胜率/均收益/中位MFE-MAE。"""
    rows = []
    for cut in cutoffs:
        sub = [r for r in records if r.get("s_star") is not None and r["s_star"] >= cut]
        rows.append({"cutoff": cut, **_stats(sub, horizon)})
    lines = [f"score>=cutoff \\ horizon={horizon}:"]
    for r in rows:
        if r.get("n"):
            lines.append(f"  >= {r['cutoff']:<3} n={r['n']:<5} 胜率 {r['win_rate'] * 100:5.1f}%  均收 {r['avg_return'] * 100:+.2f}%")
        else:
            lines.append(f"  >= {r['cutoff']:<3} n=0")
    return {"horizon": horizon, "cutoffs": rows, "text": "\n".join(lines)}


def factor_lift(records: list[dict[str, Any]], field: str, horizon: int = 10,
                quantiles: int = 4) -> dict[str, Any]:
    """把任意数值字段按分位分组，报前向胜率/均收益，验证该因子是否有 lift。

    用于流动性(c_liquidity)、S_shape 分项(c_*) 等**历史可计算**因子。
    注：资金流(fund_flow)无历史存档(只有每日快照)，无法走 as-of 回测，只能前向验证。
    """
    vals = [(r[field], r) for r in records
            if isinstance(r.get(field), (int, float)) and r.get(f"ret{horizon}") is not None]
    if len(vals) < quantiles * 5:
        return {"field": field, "horizon": horizon, "n": len(vals), "note": "样本不足",
                "text": f"{field}: 样本不足({len(vals)})"}
    vals.sort(key=lambda x: x[0])
    n = len(vals)
    buckets = []
    for q in range(quantiles):
        lo, hi = q * n // quantiles, (q + 1) * n // quantiles
        chunk = [r for _, r in vals[lo:hi]]
        buckets.append({"quantile": q + 1,
                        "value_range": [round(vals[lo][0], 4), round(vals[hi - 1][0], 4)],
                        **_stats(chunk, horizon)})
    lines = [f"{field} 分位(升序) \\ horizon={horizon}:"]
    for b in buckets:
        lines.append(f"  Q{b['quantile']} [{b['value_range'][0]}~{b['value_range'][1]}] "
                     f"n={b.get('n', 0)} 胜率 {(b.get('win_rate') or 0) * 100:.1f}% "
                     f"均收 {(b.get('avg_return') or 0) * 100:+.2f}%")
    return {"field": field, "horizon": horizon, "quantiles": buckets, "text": "\n".join(lines)}


def _load_bars_local(codes: list[str], count: int) -> dict[str, pd.DataFrame]:
    """CLI 用：经 local_tdx 读取本地日线（需通达信数据；单测走注入不经此）。"""
    import local_tdx_data  # noqa: PLC0415
    out: dict[str, pd.DataFrame] = {}
    for c in codes:
        try:
            df = local_tdx_data.get_ohlcv_table(c, count=count)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 加载 {c} 失败: {exc}", file=sys.stderr)
            df = None
        if df is not None and len(df):
            out[c] = df
    return out


def main(argv: Optional[list] = None, loader: Optional[Callable[[list[str], int], dict]] = None) -> int:
    ap = argparse.ArgumentParser(description="S_shape 因子走查回测校准（纯分析，只读本地日线）")
    ap.add_argument("--codes", default="", help="逗号分隔的 6 位代码（与 --universe-sample 二选一）")
    ap.add_argument("--universe-sample", type=int, default=0,
                    help="从 universe 随机抽 N 只（代表性样本；0=不抽，用 --codes 或全量 universe）")
    ap.add_argument("--universe-local", action="store_true",
                    help="universe 用本地 vipdoc 实有文件（推荐：覆盖率~100%%、不依赖在线代码表；否则用在线 get_stock_list）")
    ap.add_argument("--seed", type=int, default=0, help="随机抽样种子（可复现）")
    ap.add_argument("--count", type=int, default=500, help="每股回溯 K 线根数")
    ap.add_argument("--horizons", default="5,10,20", help="前向窗口(日)，逗号分隔")
    ap.add_argument("--step", type=int, default=1, help="as-of 采样步长")
    ap.add_argument("--entry-filter", choices=list(ENTRY_GATES.keys()), default="none",
                    help="只在满足入场条件的 as-of 日评估：none=每根K线；j_low=仅 J<13 买点区")
    ap.add_argument("--scorer", choices=list(SCORERS.keys()), default="s_shape",
                    help="打分器：s_shape(突破式)/s_reversal(买弱式)/invert_s_shape(反转突破分)")
    ap.add_argument("--summary-horizon", type=int, default=10)
    ap.add_argument("--threshold-sweep", action="store_true",
                    help="扫描 score>=cutoff 的胜率/均收益(校准可买门槛；仅在全量数据上有意义)")
    ap.add_argument("--factor-field", default="",
                    help="按该数值字段分位评估前向 lift(如 c_liquidity / c_compression / s_star)")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    if args.universe_local or args.universe_sample > 0:
        import local_tdx_data  # noqa: PLC0415
        if args.universe_local:
            base = local_tdx_data.list_local_vipdoc_codes()
            src = "local_vipdoc"
        else:
            base = local_tdx_data.get_stock_list()
            src = "online_get_stock_list"
        codes = sample_codes(base, args.universe_sample, args.seed) if args.universe_sample > 0 else list(base)
        print(f"[INFO] universe={src} 共 {len(base)} 只，取 {len(codes)} 只（seed={args.seed}）", file=sys.stderr)
    else:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        ap.error("需提供 --codes / --universe-sample N / --universe-local")
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    load = loader or _load_bars_local
    bars = load(codes, args.count)
    records = evaluate(bars, horizons=horizons, step=args.step,
                       entry_gate=ENTRY_GATES[args.entry_filter],
                       scorer=SCORERS[args.scorer])
    summary = summarize(records, horizon=args.summary_horizon)
    matrix = horizon_band_matrix(records, horizons)

    payload = {"codes": codes, "count": args.count, "horizons": list(horizons),
               "entry_filter": args.entry_filter, "scorer": args.scorer,
               "summary": summary, "horizon_band_matrix": matrix, "records": records}
    if args.threshold_sweep:
        payload["threshold_sweep"] = sweep_threshold(records, horizon=args.summary_horizon)
    if args.factor_field:
        payload["factor_lift"] = factor_lift(records, args.factor_field, horizon=args.summary_horizon)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 写出 {out}（{len(records)} 条信号，scorer={args.scorer}, entry_filter={args.entry_filter}）")
    print(f"\n=== 分档 × horizon 网格（scorer={args.scorer}, entry_filter={args.entry_filter}, 信号 {len(records)} 条）===")
    print(matrix["text"])
    if args.threshold_sweep:
        print(f"\n=== 门槛扫描（scorer={args.scorer}, horizon={args.summary_horizon}）===")
        print(payload["threshold_sweep"]["text"])
    if args.factor_field:
        print(f"\n=== 因子 lift（field={args.factor_field}, horizon={args.summary_horizon}）===")
        print(payload["factor_lift"]["text"])
    print("\n=== summary(horizon=%d) ===" % args.summary_horizon)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
