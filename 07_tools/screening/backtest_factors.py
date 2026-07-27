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
import datetime as _dt
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


# 完整 B1 反转K：J<13 + 缩量(量比≤50%) + 20日量底10% + 收盘变动±2% + 振幅≤7%（企稳，非落刀）
REVK_VOL_RATIO = 0.5
REVK_VOL_PCTILE = 0.10
REVK_CHG_PCT = 2.0
REVK_AMP_PCT = 7.0


def reversal_k_gate(df_slice: pd.DataFrame) -> bool:
    """B1 反转K 完整买点：J<13 且缩量企稳(小实体/小振幅)——排除收盘贴低的落刀。绝不 raise。"""
    if _kdj is None or len(df_slice) < 21:
        return False
    try:
        r = _kdj(df_slice)
        if not (r.get("available") and r.get("j") is not None and r["j"] < J_LOW_THRESHOLD):
            return False
        close = df_slice["close"].astype(float).values
        high = df_slice["high"].astype(float).values
        low = df_slice["low"].astype(float).values
        vol = df_slice["volume"].astype(float).values
        vma5 = vol[-6:-1].mean() if len(vol) >= 6 else vol[:-1].mean()
        if not (vma5 > 0 and vol[-1] / vma5 <= REVK_VOL_RATIO):        # 量比≤50%
            return False
        v20 = vol[-20:]
        if (v20 <= vol[-1]).mean() > REVK_VOL_PCTILE:                  # 当日量在20日底部10%
            return False
        chg = (close[-1] / close[-2] - 1) * 100 if close[-2] else 99   # 收盘变动 ±2%
        if abs(chg) > REVK_CHG_PCT:
            return False
        amp = (high[-1] - low[-1]) / close[-2] * 100 if close[-2] else 99  # 振幅≤7%
        return bool(amp <= REVK_AMP_PCT)
    except Exception:  # noqa: BLE001
        return False


ENTRY_GATES: dict[str, Optional[Callable[[pd.DataFrame], bool]]] = {
    "none": None,        # 每根 K 线都当信号（全市场基线）
    "j_low": j_low_gate,  # 只在 J<13 入场区评估（仅J,含落刀）
    "reversal_k": reversal_k_gate,  # 完整 B1 反转K：J<13+缩量企稳(排除贴低落刀)
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
def _sc_b1_pullback(df: pd.DataFrame, code: str):
    """完美B1 缩量回踩买弱指纹（0-7 → 归一 0-100）。10只赢家反标，precision 待本回测确认。"""
    from enrich_candidates import compute_b1_pullback_fit  # noqa: PLC0415 —— 懒加载避免重导入开销
    r = compute_b1_pullback_fit(df)
    if not r.get("available"):
        return None
    return {"score": round(r["score"] / 7 * 100, 1),
            "suggestion": "可买" if r.get("hit") else "不买",
            "aux": {"fit_raw": r["score"], "hit": r["hit"]},
            "components": {k: (1.0 if v else 0.0) for k, v in (r.get("components") or {}).items()}}


def _sc_baseline(df: pd.DataFrame, code: str):
    """基线打分器：任何 as-of 日都判「可买」。用于对照——同样的止损+BBI出场规则下，
    无差别进场能拿到多少期望/盈亏比；b1_pullback 需**显著优于**它，才证明进场信号本身有价值
    (否则 edge 全来自出场规则而非进场指纹)。"""
    return {"score": 0.0, "suggestion": "可买", "aux": {}, "components": {}}


SCORERS = {"s_shape": _sc_s_shape, "s_reversal": _sc_s_reversal,
           "invert_s_shape": _sc_invert_s_shape, "b1_pullback": _sc_b1_pullback,
           "baseline": _sc_baseline}


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
    gains = [x for x in rets if x > 0]
    losses = [-x for x in rets if x < 0]
    avg_win = statistics.mean(gains) if gains else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    payoff = round(avg_win / avg_loss, 3) if avg_loss > 0 else None   # 盈亏比：均盈/均亏(核心目标)
    med_mfe = statistics.median(mfes) if mfes else None
    med_mae = statistics.median(maes) if maes else None
    mfe_mae = (round(med_mfe / abs(med_mae), 3) if (med_mfe is not None and med_mae) else None)
    return {
        "n": len(rets),
        "win_rate": round(wins / len(rets), 4),
        "avg_return": round(statistics.mean(rets), 4),
        "median_return": round(statistics.median(rets), 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "payoff_ratio": payoff,                 # 均盈/均亏；追求盈亏比时看这个而非胜率
        "median_mfe": round(med_mfe, 4) if med_mfe is not None else None,
        "median_mae": round(med_mae, 4) if med_mae is not None else None,
        "mfe_mae_ratio": mfe_mae,               # 中位 MFE/|MAE|：潜在盈亏比
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


_R_RISK_FLOOR = 0.02   # R 计算的 risk_frac 地板(2%)：周线收盘贴低时防 ret/≈0 炸成极端 R


def _bbi_series(close: pd.Series) -> pd.Series:
    """BBI = (MA3+MA6+MA12+MA24)/4。"""
    c = close.astype(float)
    return (c.rolling(3).mean() + c.rolling(6).mean() + c.rolling(12).mean() + c.rolling(24).mean()) / 4


def simulate_b1_trade(df: pd.DataFrame, entry_idx: int, bbi: pd.Series,
                      bbi_exit_consec: int = 2, time_stop_bars: int = 0,
                      stop_mode: str = "low", stop_pct: float = 8.0) -> dict[str, Any]:
    """B1 交易规则模拟：买入当日收盘进场；
    止损：stop_mode='low'=买入当日最低价(超卖贴低时几乎无空间)；'pct'=entry×(1-stop_pct%)(固定空间)。
    站上 BBI 后若连续 bbi_exit_consec 日收盘跌破 BBI 则止盈卖出；可选 time_stop_bars 根后到期平仓。
    优先级：先判当日最低是否破止损(盘中)，再判收盘 BBI 退出，再判时间止损；均未触发则持有到数据末。
    跳空低开(open<stop)按开盘价成交。返回 {exit_idx, reason, ret, holding, risk_frac}。"""
    close = df["close"].astype(float).values
    low = df["low"].astype(float).values
    open_ = df["open"].astype(float).values
    n = len(close)
    entry = float(close[entry_idx])
    stop = entry * (1 - stop_pct / 100.0) if stop_mode == "pct" else float(low[entry_idx])
    risk_frac = (entry - stop) / entry if entry else 0.0
    bbi_v = bbi.values
    has_above = False
    consec_below = 0
    for j in range(entry_idx + 1, n):
        if low[j] <= stop:                                    # ① 盘中破止损
            fill = float(open_[j]) if open_[j] < stop else stop
            return {"exit_idx": j, "reason": "stop", "ret": fill / entry - 1,
                    "holding": j - entry_idx, "risk_frac": risk_frac}
        b = bbi_v[j]
        if b == b:                                            # ② 收盘 BBI 退出（b==b 排除 NaN）
            if close[j] > b:
                has_above = True; consec_below = 0
            elif close[j] < b:
                if has_above:
                    consec_below += 1
                    if consec_below >= bbi_exit_consec:
                        return {"exit_idx": j, "reason": "bbi_exit", "ret": float(close[j]) / entry - 1,
                                "holding": j - entry_idx, "risk_frac": risk_frac}
            else:
                consec_below = 0
        if time_stop_bars and (j - entry_idx) >= time_stop_bars:   # ③ 时间止损
            return {"exit_idx": j, "reason": "time_stop", "ret": float(close[j]) / entry - 1,
                    "holding": j - entry_idx, "risk_frac": risk_frac}
    return {"exit_idx": n - 1, "reason": "open_end", "ret": float(close[-1]) / entry - 1,
            "holding": (n - 1) - entry_idx, "risk_frac": risk_frac}


def _to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """日线重采样为周线（W-FRI）：开=首、高=max、低=min、收=末、量/额=sum。"""
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.set_index("date")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in d.columns:
        agg["volume"] = "sum"
    if "amount" in d.columns:
        agg["amount"] = "sum"
    return d.resample("W-FRI").agg(agg).dropna(subset=["close"]).reset_index()


def evaluate_trades(bars_by_code: dict[str, pd.DataFrame],
                    scorer: Optional[Callable[[pd.DataFrame, str], Optional[dict]]] = None,
                    min_bars: int = 30, step: int = 1,
                    max_signals_per_code: Optional[int] = None,
                    weekly: bool = False, cost_bps: float = 0.0,
                    amv_regime: Optional[dict] = None,
                    bbi_exit_consec: int = 2, time_stop_bars: int = 0,
                    collect_all: bool = False,
                    entry_gate: Optional[Callable[[pd.DataFrame], bool]] = None,
                    stop_mode: str = "low", stop_pct: float = 8.0) -> list[dict[str, Any]]:
    """在 scorer 判「可买」的 as-of 日进场，按 B1 规则(止损+BBI)模拟到出场；非重叠(平仓后再找)。

    cost_bps：单边成本合计的往返基点(A股约20~30bps含佣金/印花税/滑点)，从每笔收益中扣除，看净期望。
    amv_regime：date→regime 映射(如 load_amv_regime)。提供时只在「做多」区间进场(as-of最近≤进场日的regime)。
    bbi_exit_consec/time_stop_bars：出场规则参数(可扫描)。每笔记录 r_multiple=净收益/风险敞口，供风险定额仓位。
    collect_all=True：不做单股非重叠去重，返回**每个**可买as-of日的候选(含 score)，供组合级 top-N 横截面择优。
    entry_gate(df_slice)->bool：进场硬门槛(如 j_low_gate=当日 J<13，B1 核心买点)；不满足则不进场。
    """
    scorer = scorer or _sc_b1_pullback
    cost = cost_bps / 1e4
    import bisect
    amv_dates = sorted(amv_regime) if amv_regime else None

    def _amv_ok(date: str) -> bool:
        if not amv_regime:
            return True
        idx = bisect.bisect_right(amv_dates, date) - 1     # as-of：最近 ≤ date 的regime
        return idx >= 0 and amv_regime[amv_dates[idx]] == "做多"

    trades: list[dict[str, Any]] = []
    for code, raw in bars_by_code.items():
        if raw is None or len(raw) == 0:
            continue
        df = raw.sort_values("date").reset_index(drop=True)
        if weekly:
            df = _to_weekly(df)
        n = len(df)
        if n < min_bars + 2:
            continue
        bbi = _bbi_series(df["close"])
        emitted = 0
        i = min_bars
        while i < n - 1:
            entry_date = str(df["date"].iloc[i])[:10]
            slice_df = df.iloc[:i + 1]
            if entry_gate is not None and not entry_gate(slice_df):
                i += max(1, step)
                continue
            res = scorer(slice_df, code)
            if res is not None and res.get("suggestion") == "可买" and _amv_ok(entry_date):
                tr = simulate_b1_trade(df, i, bbi, bbi_exit_consec=bbi_exit_consec,
                                       time_stop_bars=time_stop_bars,
                                       stop_mode=stop_mode, stop_pct=stop_pct)
                ret_net = tr["ret"] - cost
                rf = tr.get("risk_frac") or 0.0
                rf_eff = max(rf, _R_RISK_FLOOR)   # 地板：周线收盘贴低时 risk_frac≈0 会把 R 炸成极端值
                trades.append({"code": code, "entry_date": entry_date,
                               "exit_date": str(df["date"].iloc[tr["exit_idx"]])[:10],
                               "score": res.get("score"), "ret": round(ret_net, 4),
                               "risk_frac": round(rf, 4),
                               "r_multiple": round(ret_net / rf_eff, 3) if rf > 0 else None,
                               "holding": tr["holding"], "reason": tr["reason"]})
                emitted += 1
                if max_signals_per_code and emitted >= max_signals_per_code:
                    break
                i = (i + max(1, step)) if collect_all else (tr["exit_idx"] + 1)  # 收集全部候选 / 或非重叠
            else:
                i += max(1, step)
    return trades


def summarize_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """交易级汇总：胜率、每笔期望、盈亏比、均持仓、按出场原因分解。"""
    if not trades:
        return {"n": 0, "text": "无交易"}
    import collections
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [-r for r in rets if r < 0]
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    payoff = round(avg_win / avg_loss, 3) if avg_loss > 0 else None
    by_reason = {}
    for rs in collections.Counter(t["reason"] for t in trades):
        rr = [t["ret"] for t in trades if t["reason"] == rs]
        by_reason[rs] = {"n": len(rr), "avg_return": round(statistics.mean(rr), 4)}
    d = {"n": len(trades), "win_rate": round(len(wins) / len(rets), 4),
         "expectancy": round(statistics.mean(rets), 4),
         "avg_win": round(avg_win, 4), "avg_loss": round(avg_loss, 4),
         "payoff_ratio": payoff,
         "avg_holding": round(statistics.mean([t["holding"] for t in trades]), 1),
         "exit_reasons": by_reason}
    # R 倍数视角（风险定额仓位）：R=净收益/单笔风险敞口；期望R×每笔风险% ≈ 每笔账户增长
    rmults = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    if rmults:
        rwin = [r for r in rmults if r > 0]
        rloss = [-r for r in rmults if r < 0]
        d["expectancy_R"] = round(statistics.mean(rmults), 3)      # 每笔期望R(已对 risk_frac 设地板)
        d["median_R"] = round(statistics.median(rmults), 3)        # 中位R(抗极端值,更稳)
        d["avg_win_R"] = round(statistics.mean(rwin), 3) if rwin else 0.0
        d["avg_loss_R"] = round(statistics.mean(rloss), 3) if rloss else 0.0
        d["total_R"] = round(sum(rmults), 1)                       # 累计R(样本期总盈亏,以R计)
    lines = [f"交易 {d['n']} 笔  胜率 {d['win_rate']*100:.1f}%  期望 {d['expectancy']*100:+.2f}%/笔  "
             f"盈亏比 {d['payoff_ratio']}  均持 {d['avg_holding']} 根",
             f"  均盈 {d['avg_win']*100:+.2f}%  均亏 -{d['avg_loss']*100:.2f}%"]
    if rmults:
        lines.append(f"  期望 {d['expectancy_R']:+.3f}R/笔  (均盈 {d['avg_win_R']:.2f}R / 均亏 "
                     f"{d['avg_loss_R']:.2f}R)  累计 {d['total_R']:+.0f}R —— 按风险r%/笔计,每笔账户增长≈r%×{d['expectancy_R']:+.3f}")
    for rs, s in by_reason.items():
        lines.append(f"  出场[{rs}] {s['n']} 笔  均收 {s['avg_return']*100:+.2f}%")
    d["text"] = "\n".join(lines)
    return d


def _amv_regime_from_records(records: list[dict[str, Any]]) -> dict[str, str]:
    """把 0AMV 日线(含 change_pct)按状态机(>4%→做多; <-2.3%→空头; 之间粘滞维持)转成 date→regime。

    复刻 amv_state：空头/做多一旦进入则锁定，直到反向阈值触发；起始中性。
    """
    regime: dict[str, str] = {}
    state = "中性"
    for r in sorted(records, key=lambda x: x.get("date", "")):
        v = r.get("change_pct")
        if v is not None:
            if v > 4:
                state = "做多"
            elif v < -2.3:
                state = "空头"
        regime[str(r.get("date"))[:10]] = state
    return regime


def load_amv_regime(since: str = "2015-01-01", root: Optional[str] = None) -> dict[str, str]:
    """从指南针 0AMV 日线(compass_amv)构建历史 date→regime。best-effort：数据缺失返回 {}。"""
    try:
        import compass_amv  # noqa: PLC0415
        parsed = compass_amv.parse_amv_daily(since=since, root=root)
        if parsed.get("error") or not parsed.get("records"):
            return {}
        return _amv_regime_from_records(parsed["records"])
    except Exception:  # noqa: BLE001
        return {}


def simulate_portfolio(trades: list[dict[str, Any]], risk_pct: float = 0.01,
                       max_concurrent: int = 5, max_pos_frac: float = 0.20,
                       max_gross: float = 1.0) -> dict[str, Any]:
    """组合级资金曲线：把逐笔交易按**固定风险仓位**(每笔风险 risk_pct 的本金)+ 并发持仓上限
    + 单仓/总敞口上限,事件驱动地放到一条资金曲线上,输出总收益/CAGR/最大回撤/成交与被限笔数。

    仓位：alloc_frac = min(risk_pct/risk_frac, max_pos_frac)（止损打掉≈risk_pct 本金）。
    约束：同时持仓数 ≤ max_concurrent；名义总敞口 ≤ max_gross×当前权益（满则跳过后续信号）。
    回撤：按平仓时点的已实现权益序列计（不含持仓中浮亏，属保守低估，已注明）。绝不 raise。
    """
    import heapq
    entries = sorted([t for t in trades if t.get("entry_date") and t.get("exit_date")
                      and (t.get("risk_frac") or 0) > 0], key=lambda t: t["entry_date"])
    if not entries:
        return {"n_taken": 0, "n_skipped": 0, "text": "无可用交易(缺 risk_frac/日期)"}
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    gross = 0.0                       # 已开仓名义敞口合计(进场时资金)
    open_heap: list = []              # (exit_date, seq, alloc_cap, ret)
    curve: list[dict[str, Any]] = []
    taken = 0
    skipped = 0
    seq = 0

    def _close_until(date: str) -> None:
        nonlocal equity, peak, max_dd, gross
        while open_heap and open_heap[0][0] <= date:
            ed, _, alloc_cap, ret = heapq.heappop(open_heap)
            equity += alloc_cap * ret
            gross -= alloc_cap
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
            curve.append({"date": ed, "equity": round(equity, 5)})

    for t in entries:
        _close_until(t["entry_date"])
        alloc_frac = min(risk_pct / t["risk_frac"], max_pos_frac)
        alloc_cap = alloc_frac * equity
        if len(open_heap) >= max_concurrent or (gross + alloc_cap) > max_gross * equity:
            skipped += 1
            continue
        heapq.heappush(open_heap, (t["exit_date"], seq, alloc_cap, t["ret"]))
        seq += 1
        gross += alloc_cap
        taken += 1
    _close_until("9999-99-99")

    years = None
    cagr = None
    try:
        d0 = _dt.date.fromisoformat(entries[0]["entry_date"])
        d1 = _dt.date.fromisoformat(curve[-1]["date"]) if curve else d0
        years = max((d1 - d0).days / 365.25, 1e-9)
        cagr = equity ** (1 / years) - 1 if equity > 0 else None
    except Exception:  # noqa: BLE001
        pass
    out = {"n_taken": taken, "n_skipped": skipped, "final_equity": round(equity, 4),
           "total_return": round(equity - 1, 4), "max_drawdown": round(max_dd, 4),
           "cagr": round(cagr, 4) if cagr is not None else None,
           "years": round(years, 2) if years else None,
           "risk_pct": risk_pct, "max_concurrent": max_concurrent,
           "max_pos_frac": max_pos_frac, "max_gross": max_gross}
    ret_dd = (round(out["total_return"] / max_dd, 2) if max_dd > 0 else None)
    out["return_over_maxdd"] = ret_dd
    out["text"] = (
        f"组合资金曲线(风险{risk_pct*100:.1f}%/笔, 并发≤{max_concurrent}, 单仓≤{max_pos_frac*100:.0f}%): "
        f"成交 {taken} 笔/限跳 {skipped}  总收益 {out['total_return']*100:+.1f}%  "
        f"CAGR {out['cagr']*100:.1f}%  最大回撤 {out['max_drawdown']*100:.1f}%  "
        f"收益/回撤 {ret_dd}  (期约 {out['years']} 年)")
    return out


def simulate_portfolio_topn(candidates: list[dict[str, Any]], top_n: int = 5,
                            risk_pct: float = 0.01, max_concurrent: int = 5,
                            max_pos_frac: float = 0.20, max_gross: float = 1.0) -> dict[str, Any]:
    """组合级**横截面 top-N 择优**资金曲线：每个进场日在所有「可买」候选里按 score 降序取前 top_n
    (排除已持有该股、受并发/敞口上限约束)，固定风险仓位入场，事件驱动出资金曲线/CAGR/最大回撤。

    candidates：evaluate_trades(collect_all=True) 的全候选(含 entry_date/exit_date/ret/risk_frac/score)。
    top_n：每个进场日最多新开仓数(横截面择优的宽度)。绝不 raise。
    """
    import heapq
    import collections as _c
    cands = [t for t in candidates if t.get("entry_date") and t.get("exit_date")
             and (t.get("risk_frac") or 0) > 0]
    if not cands:
        return {"n_taken": 0, "n_skipped": 0, "text": "无可用候选"}
    by_date: dict[str, list] = _c.defaultdict(list)
    for t in cands:
        by_date[t["entry_date"]].append(t)
    dates = sorted(by_date)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    gross = 0.0
    open_heap: list = []                 # (exit_date, seq, alloc_cap, ret, code)
    held: set = set()
    curve: list[dict[str, Any]] = []
    taken = 0
    skipped = 0
    seq = 0

    def _close_until(date: str) -> None:
        nonlocal equity, peak, max_dd, gross
        while open_heap and open_heap[0][0] <= date:
            ed, _, alloc_cap, ret, code = heapq.heappop(open_heap)
            equity += alloc_cap * ret
            gross -= alloc_cap
            held.discard(code)
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)
            curve.append({"date": ed, "equity": round(equity, 5)})

    for date in dates:
        _close_until(date)
        slots = max_concurrent - len(open_heap)
        if slots <= 0:
            skipped += sum(1 for t in by_date[date] if t["code"] not in held)
            continue
        ranked = sorted((t for t in by_date[date] if t["code"] not in held),
                        key=lambda t: (t.get("score") or 0), reverse=True)
        opened = 0
        for t in ranked:
            if opened >= top_n or len(open_heap) >= max_concurrent:
                skipped += 1
                continue
            alloc_cap = min(risk_pct / t["risk_frac"], max_pos_frac) * equity
            if (gross + alloc_cap) > max_gross * equity:
                skipped += 1
                continue
            heapq.heappush(open_heap, (t["exit_date"], seq, alloc_cap, t["ret"], t["code"]))
            seq += 1
            gross += alloc_cap
            held.add(t["code"])
            taken += 1
            opened += 1
    _close_until("9999-99-99")

    years = cagr = None
    try:
        d0 = _dt.date.fromisoformat(dates[0])
        d1 = _dt.date.fromisoformat(curve[-1]["date"]) if curve else d0
        years = max((d1 - d0).days / 365.25, 1e-9)
        cagr = equity ** (1 / years) - 1 if equity > 0 else None
    except Exception:  # noqa: BLE001
        pass
    ret_dd = (round((equity - 1) / max_dd, 2) if max_dd > 0 else None)
    out = {"mode": "topn", "top_n": top_n, "n_taken": taken, "n_skipped": skipped,
           "final_equity": round(equity, 4), "total_return": round(equity - 1, 4),
           "max_drawdown": round(max_dd, 4), "cagr": round(cagr, 4) if cagr is not None else None,
           "years": round(years, 2) if years else None, "return_over_maxdd": ret_dd,
           "risk_pct": risk_pct, "max_concurrent": max_concurrent}
    out["text"] = (
        f"组合 top-{top_n} 横截面择优(风险{risk_pct*100:.1f}%/笔, 并发≤{max_concurrent}): "
        f"成交 {taken}/限跳 {skipped}  总收益 {out['total_return']*100:+.1f}%  "
        f"CAGR {out['cagr']*100:.1f}%  最大回撤 {out['max_drawdown']*100:.1f}%  收益/回撤 {ret_dd}  "
        f"(期约 {out['years']} 年)")
    return out


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
    ap.add_argument("--trade-sim", action="store_true",
                    help="按B1交易规则模拟(进场=可买日收盘;止损=买入当日最低;站上BBI后连破2日收盘卖出)测真实盈亏比")
    ap.add_argument("--weekly", action="store_true",
                    help="日线重采样为周线后再回测(周线B1;配合 --trade-sim)")
    ap.add_argument("--cost-bps", type=float, default=0.0,
                    help="往返交易成本(基点),从每笔收益扣除(A股约20~30bps含佣金/印花/滑点);默认0=毛收益")
    ap.add_argument("--amv-long-only", action="store_true",
                    help="仅在0AMV『做多』区间进场(读指南针compass_amv历史→状态机>4%做多/<-2.3%空头;配合 --trade-sim)")
    ap.add_argument("--bbi-consec", type=int, default=2,
                    help="出场:站上BBI后连续N日收盘跌破BBI才卖出(默认2;可扫描出场松紧)")
    ap.add_argument("--time-stop", type=int, default=0,
                    help="出场:持有N根仍未触发止损/BBI则到期平仓(默认0=不启用)")
    ap.add_argument("--portfolio", action="store_true",
                    help="在逐笔交易上叠加组合级资金曲线(固定风险仓位+并发上限),输出总收益/CAGR/最大回撤")
    ap.add_argument("--risk-pct", type=float, default=1.0, help="组合:每笔风险占本金%(默认1.0)")
    ap.add_argument("--max-concurrent", type=int, default=5, help="组合:同时持仓上限(默认5)")
    ap.add_argument("--max-pos", type=float, default=20.0, help="组合:单仓名义上限%%本金(默认20)")
    ap.add_argument("--top-n", type=int, default=0,
                    help="组合:每个进场日按score降序只取前N只(横截面择优;0=不启用,取全部可买)")
    ap.add_argument("--stop-mode", choices=["low", "pct"], default="low",
                    help="止损:low=买入K最低(超卖贴低几乎无空间);pct=entry×(1-stop_pct%%)(固定空间)")
    ap.add_argument("--stop-pct", type=float, default=8.0, help="--stop-mode pct 时的止损百分比(默认8)")
    ap.add_argument("--summary-only", action="store_true",
                    help="输出JSON不含逐笔trades(仅摘要;全市场日线省内存,防OOM)")
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

    if args.trade_sim:
        amv_regime = None
        if args.amv_long_only:
            amv_regime = load_amv_regime()
            if not amv_regime:
                ap.error("--amv-long-only 需要指南针 0AMV 数据(compass_amv)，未读到；请在有指南针的机器运行")
            print(f"[INFO] 0AMV regime 覆盖 {len(amv_regime)} 个交易日，仅在『做多』区间进场", file=sys.stderr)
        trades = evaluate_trades(bars, scorer=SCORERS[args.scorer], step=args.step,
                                 weekly=args.weekly, cost_bps=args.cost_bps, amv_regime=amv_regime,
                                 bbi_exit_consec=args.bbi_consec, time_stop_bars=args.time_stop,
                                 collect_all=bool(args.top_n > 0),
                                 entry_gate=ENTRY_GATES[args.entry_filter],
                                 stop_mode=args.stop_mode, stop_pct=args.stop_pct)
        tsum = summarize_trades(trades)
        payload = {"mode": "trade_sim", "scorer": args.scorer, "weekly": args.weekly,
                   "cost_bps": args.cost_bps, "amv_long_only": bool(args.amv_long_only),
                   "entry_filter": args.entry_filter, "top_n": args.top_n,
                   "codes": codes, "count": args.count, "trade_summary": tsum, "trades": trades}
        if args.top_n > 0:
            payload["portfolio"] = simulate_portfolio_topn(
                trades, top_n=args.top_n, risk_pct=args.risk_pct / 100.0,
                max_concurrent=args.max_concurrent, max_pos_frac=args.max_pos / 100.0)
        elif args.portfolio:
            payload["portfolio"] = simulate_portfolio(
                trades, risk_pct=args.risk_pct / 100.0, max_concurrent=args.max_concurrent,
                max_pos_frac=args.max_pos / 100.0)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            if args.summary_only:
                payload = {k: v for k, v in payload.items() if k != "trades"}
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] 写出 {out}（{len(trades)} 笔交易，scorer={args.scorer}, {'周线' if args.weekly else '日线'}, cost={args.cost_bps}bps, amv_long_only={bool(args.amv_long_only)}）")
        print(f"\n=== B1 交易模拟（scorer={args.scorer}, {'周线' if args.weekly else '日线'}, "
              f"入场门槛={args.entry_filter}, cost={args.cost_bps}bps, "
              f"{'仅0AMV做多' if args.amv_long_only else '全regime'}, "
              f"止损=买入K最低 / 站上BBI后连破2日卖出）===")
        print(tsum["text"])
        if payload.get("portfolio"):
            print("\n" + payload["portfolio"]["text"])
        return 0

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
