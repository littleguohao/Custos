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

from s_shape import compute_s_shape, SSHAPE_MIN_BARS, SSTAR_STRONG, SSTAR_MID  # noqa: E402

HORIZONS_DEFAULT = (5, 10, 20)


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


def evaluate(
    bars_by_code: dict[str, pd.DataFrame],
    horizons: tuple[int, ...] = HORIZONS_DEFAULT,
    min_bars: int = SSHAPE_MIN_BARS,
    step: int = 1,
    max_signals_per_code: Optional[int] = None,
) -> list[dict[str, Any]]:
    """逐股逐日走查：as-of 切片算 S_shape，配前向指标。返回逐条记录（可复盘）。"""
    records: list[dict[str, Any]] = []
    for code, raw in bars_by_code.items():
        if raw is None or len(raw) == 0:
            continue
        df = raw.sort_values("date").reset_index(drop=True)
        n = len(df)
        emitted = 0
        for i in range(min_bars, n - 1, max(1, step)):
            ss = compute_s_shape(df.iloc[:i + 1], code)  # 只用到 i（含当日）
            if not ss.get("available"):
                continue
            rec: dict[str, Any] = {
                "code": code,
                "date": str(df["date"].iloc[i])[:10],
                "s_star": ss["s_star"],
                "s_shape": ss["s_shape"],
                "delta": ss["delta"],
                "penalty": ss["penalty"],
                "suggestion": ss["suggestion"],
            }
            for k, v in (ss.get("components") or {}).items():
                rec[f"c_{k}"] = (v or {}).get("points")
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
    ap.add_argument("--codes", required=True, help="逗号分隔的 6 位代码")
    ap.add_argument("--count", type=int, default=500, help="每股回溯 K 线根数")
    ap.add_argument("--horizons", default="5,10,20", help="前向窗口(日)，逗号分隔")
    ap.add_argument("--step", type=int, default=1, help="as-of 采样步长")
    ap.add_argument("--summary-horizon", type=int, default=10)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    load = loader or _load_bars_local
    bars = load(codes, args.count)
    records = evaluate(bars, horizons=horizons, step=args.step)
    summary = summarize(records, horizon=args.summary_horizon)

    payload = {"codes": codes, "count": args.count, "horizons": list(horizons),
               "summary": summary, "records": records}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 写出 {out}（{len(records)} 条信号）")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
