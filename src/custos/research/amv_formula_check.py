# -*- coding: utf-8 -*-
"""研究：0AMV 论文公式验证——能否用公开行情自算 0AMV（摆脱指南针客户端依赖）。

> 这是**数据验证**研究（复现口径），不是交易信号研究；无未来函数约束照常钉死
> （MA(REF(CLOSE,1),5) 只用 ≤T-1 的数据）。

**论文公式**（owner 2026-08-30 提供）::

    0AMV = SMA(成交额, 10, 1) × CLOSE / MA(REF(CLOSE,1), 5) / 10⁷ × 0.835

- ``SMA(X,N,M)`` 是 TDX 递归语义：Y = (X×M + Y′×(N−M))/N（**不是**简单均线；
  递归起点敏感，前 15 根跳过不评）。
- ``成交额``：vdat 自带 amount 列 ≈ 全市场成交额（与 880001 amount 比值均值
  0.979，2026 段逐日贴合）——全历史（1993 起）用它；880001/880002（总市值/
  流通市值统计指数）的 amount 仅 2011-10 起，作交叉验证。
- ``CLOSE`` 是本研究的关键不确定点 ⇒ 逐个变体试：999999 上证收盘 /
  880002 流通市值指数收盘 / 0AMV 自身收盘（循环引用，只在动量项上有意义）/
  常数 1（纯量能项，检验动量项是否重要）。

比对：水平值序列相关 + 日涨跌幅序列相关 + 残差分布（f/真值−1）+ 分时段
（1993-2006 / 2007-2015 / 2016+）口径漂移检查。真值 = vdat 的 0AMV 收盘水平
（比台账涨跌幅更强的对照——涨跌幅由水平值推出）。

CLI::

    uv run python src/custos/research/amv_formula_check.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# 论文公式常量（owner 提供口径，钉死不拟合；拟合出的隐含系数单列报告）
SMA_N, SMA_M = 10, 1
DIVISOR = 1e7
SCALE = 0.835
WARMUP_BARS = 15  # SMA 递归起点敏感段（>N 余量），跳过不评
ERAS = (
    ("1993-2006", "1993-01-01", "2006-12-31"),
    ("2007-2015", "2007-01-01", "2015-12-31"),
    ("2016+", "2016-01-01", "2999-12-31"),
)


def sma_tdx(x: np.ndarray, n: int = SMA_N, m: int = SMA_M) -> np.ndarray:
    """TDX SMA(X,N,M)：Y = (X×M + Y′×(N−M))/N 递归（Y₀=X₀）。

    ⚠️ 递归起点敏感：同一序列的不同前缀起点 → 不同轨迹 ⇒ 只允许从第 0 根
    开始的全序列调用（无未来函数：Y[i] 只用 X[0..i]）。
    """
    y = np.full(len(x), np.nan)
    if len(x) == 0:
        return y
    y[0] = x[0]
    for i in range(1, len(x)):
        y[i] = (x[i] * m + y[i - 1] * (n - m)) / n
    return y


def formula_series(amount: np.ndarray, close: Optional[np.ndarray]) -> np.ndarray:
    """论文公式逐日值。close=None ⇒ 纯量能项（动量项=1）。

    MA(REF(CLOSE,1),5) = 昨日收盘的 5 日均线 ⇒ T 日只用 close[T-5..T-1]
    （shift(1) 后 rolling(5)——无未来函数，钉测钉住）。
    """
    sma = sma_tdx(np.asarray(amount, float))
    if close is None:
        momentum = np.ones(len(sma))
    else:
        c = np.asarray(close, float)
        momentum = c / pd.Series(c).shift(1).rolling(5).mean().to_numpy()
    return sma * momentum / DIVISOR * SCALE


def compare_series(
    dates: np.ndarray, formula: np.ndarray, truth: np.ndarray
) -> dict[str, Any]:
    """水平相关 + 日涨跌幅相关 + 残差分布 + 隐含系数 + 分时段（全历史 + ERAS）。"""
    mask = (~np.isnan(formula)) & (~np.isnan(truth)) & (formula > 0) & (truth > 0)
    mask[:WARMUP_BARS] = False

    def _block(m: np.ndarray) -> Optional[dict[str, Any]]:
        if m.sum() < 30:
            return None
        f, t = formula[m], truth[m]
        level_corr = float(np.corrcoef(f, t)[0, 1])
        f_chg = pd.Series(f).pct_change().to_numpy()[1:]
        t_chg = pd.Series(t).pct_change().to_numpy()[1:]
        ok = ~(np.isnan(f_chg) | np.isnan(t_chg))
        chg_corr = float(np.corrcoef(f_chg[ok], t_chg[ok])[0, 1]) if ok.any() else None
        resid = f / t - 1
        # 隐含系数：公式 = 系数 × 无量纲项 ⇒ 真值/无量纲项 的中位数 = 该常数应为多少
        implied = SCALE / (1 + float(np.median(resid)))
        return {
            "n": int(m.sum()),
            "level_corr": round(level_corr, 4),
            "change_corr": round(chg_corr, 4) if chg_corr is not None else None,
            "resid_median_pct": round(float(np.median(resid)) * 100, 2),
            "resid_mean_pct": round(float(np.mean(resid)) * 100, 2),
            "resid_std_pct": round(float(np.std(resid)) * 100, 2),
            "resid_p25_pct": round(float(np.percentile(resid, 25)) * 100, 2),
            "resid_p75_pct": round(float(np.percentile(resid, 75)) * 100, 2),
            "implied_scale": round(implied, 4),
        }

    out: dict[str, Any] = {"all": _block(mask)}
    for era, lo, hi in ERAS:
        era_mask = mask & (dates >= lo) & (dates <= hi)
        out[era] = _block(era_mask)
    return out


def load_inputs() -> dict[str, pd.DataFrame]:
    """加载真值与各变体输入（vdat 全历史 + 999999 + 880001/880002）。"""
    from custos.datasource.local_tdx import compass_amv, local_tdx_data  # noqa: PLC0415

    parsed = compass_amv.parse_amv_daily(since="1990-01-01")
    if parsed.get("error") or not parsed.get("records"):
        raise RuntimeError(f"vdat 读取失败: {parsed.get('error')}")
    amv = pd.DataFrame(parsed["records"])[["date", "close", "amount"]].dropna()
    out = {"amv": amv.reset_index(drop=True)}
    for code, col in (("999999", "idx_close"), ("880002", "liv_close")):
        df = local_tdx_data.get_ohlcv_table(code, count=100000)
        df = df[["date", "close"]].rename(columns={"close": col})
        df["date"] = df["date"].astype(str).str[:10]
        out[col] = df
    # 880001 成交额交叉验证（2011+）
    a1 = local_tdx_data.get_ohlcv_table("880001", count=100000)
    a1 = a1[["date", "amount"]].rename(columns={"amount": "a880001"})
    a1["date"] = a1["date"].astype(str).str[:10]
    out["a880001"] = a1
    return out


def run_check() -> dict[str, Any]:
    """主流程：对齐 → 四变体 × 全历史比对 + 880001 成交额交叉验证。"""
    inp = load_inputs()
    amv = inp["amv"]
    truth = amv["close"].to_numpy(float)
    dates = amv["date"].to_numpy()
    amt = amv["amount"].to_numpy(float)

    # 变体 CLOSE 序列按日期对齐到 vdat 交易日（缺失 ⇒ NaN，比对时剔除）
    idx_close = amv.merge(inp["idx_close"], on="date", how="left")[
        "idx_close"
    ].to_numpy(float)
    liv_close = amv.merge(inp["liv_close"], on="date", how="left")[
        "liv_close"
    ].to_numpy(float)

    variants = {
        "close_999999_上证": formula_series(amt, idx_close),
        "close_880002_流通市值": formula_series(amt, liv_close),
        "close_0amv_自身": formula_series(amt, truth),
        "close_常数1_纯量能": formula_series(amt, None),
    }
    report: dict[str, Any] = {
        "formula": f"SMA(成交额,{SMA_N},{SMA_M}) × CLOSE / MA(REF(CLOSE,1),5) "
        f"/ {DIVISOR:g} × {SCALE}",
        "truth": "vdat 0AMV 收盘水平（1993-01-04 起全历史）",
        "warmup_bars_skipped": WARMUP_BARS,
        "n_truth": int(len(truth)),
        "truth_range": [str(dates[0]), str(dates[-1])],
        "variants": {
            name: compare_series(dates, f, truth) for name, f in variants.items()
        },
    }
    # 880001 成交额交叉验证（2011+）：换成交额源重算主变体
    a1 = amv.merge(inp["a880001"], on="date", how="left")["a880001"].to_numpy(float)
    ok = ~np.isnan(a1)
    if ok.any():
        f_a1 = np.full(len(a1), np.nan)
        idx_v = np.where(ok)[0]
        sub = formula_series(a1[idx_v], idx_close[idx_v])
        f_a1[idx_v] = sub
        rep2 = compare_series(dates, f_a1, truth)
        report["amount_source_crosscheck"] = {
            "note": "成交额源换成 880001.amount（2011-10 起）重算 CLOSE=999999 变体；"
            "vdat.amount 与 880001.amount 比值均值 0.979（已探）",
            "first_880001_date": str(dates[idx_v[0]]),
            **rep2,
        }
    # 决策层检验（我们最关心的用途）：公式日涨跌幅 → 状态机重放 ⇒ regime 逐日一致率
    # 与做多区间数对比（load_amv_regime 的实际消费口径；水平残差不影响这一项）
    best = variants["close_999999_上证"]
    mask = ~np.isnan(best)
    rec_f = [
        {"date": str(d), "change_pct": float(c)}
        for d, c in zip(
            dates[mask], pd.Series(best[mask]).pct_change().to_numpy() * 100
        )
        if not np.isnan(c)
    ]
    rec_t = [
        {"date": str(d), "change_pct": float(c)}
        for d, c in zip(
            dates[mask], pd.Series(truth[mask]).pct_change().to_numpy() * 100
        )
        if not np.isnan(c)
    ]
    from custos.research import backtest_factors as bf  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415

    reg_f = bf._amv_regime_from_records(rec_f)
    reg_t = bf._amv_regime_from_records(rec_t)
    common = sorted(set(reg_f) & set(reg_t))
    agree = sum(1 for d in common if reg_f[d] == reg_t[d])
    report["regime_replay_check"] = {
        "note": "公式涨跌幅与真值涨跌幅各自重放状态机（>4% 做多 / <-2.3% 空头 / "
        "粘滞），比 date→regime；这是我们实际消费 0AMV 的口径",
        "variant": "close_999999_上证",
        "n_days": len(common),
        "state_agree_pct": round(agree / len(common), 4) if common else None,
        "long_intervals_formula": len(srs.long_intervals(reg_f)),
        "long_intervals_truth": len(srs.long_intervals(reg_t)),
    }
    return report


def print_summary(rep: dict[str, Any]) -> None:
    """stdout 中文摘要。"""
    print("\n" + "=" * 76)
    print(
        "0AMV 论文公式验证（真值 = vdat 收盘水平，%d 日 %s~%s；前 %d 根 SMA 预热跳过）"
        % (
            rep["n_truth"],
            rep["truth_range"][0],
            rep["truth_range"][1],
            rep["warmup_bars_skipped"],
        )
    )
    print("=" * 76)
    print("公式:", rep["formula"])
    print("\n变体 | n | 水平相关 | 涨跌幅相关 | 残差中位% | 残差std% | 隐含系数")
    for name, blk in rep["variants"].items():
        a = blk.get("all") or {}
        print(
            f"  {name:<20} {a.get('n'):>5} | {a.get('level_corr'):>7} | "
            f"{a.get('change_corr'):>7} | {a.get('resid_median_pct'):>8} | "
            f"{a.get('resid_std_pct'):>6} | {a.get('implied_scale')}"
        )
        for era, lo, hi in ERAS:
            e = blk.get(era)
            if e:
                print(
                    f"    {era:<10} n={e['n']:>5} 水平 {e['level_corr']:>7} "
                    f"涨跌 {e.get('change_corr')} 残差中位 {e['resid_median_pct']}% "
                    f"隐含系数 {e['implied_scale']}"
                )
    cc = rep.get("amount_source_crosscheck")
    if cc and cc.get("all"):
        a = cc["all"]
        print(
            f"\n成交额源交叉验证（880001.amount，{cc['first_880001_date']} 起）："
            f"水平相关 {a.get('level_corr')} / 涨跌幅相关 {a.get('change_corr')} / "
            f"残差中位 {a.get('resid_median_pct')}%"
        )
    rc = rep.get("regime_replay_check")
    if rc:
        print(
            f"\n决策层检验（{rc['variant']}，状态机重放）：regime 逐日一致率 "
            f"{rc['state_agree_pct'] * 100:.1f}%（{rc['n_days']} 日）；"
            f"做多区间数 公式 {rc['long_intervals_formula']} vs 真值 "
            f"{rc['long_intervals_truth']}"
        )


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out",
        default=str(Path("artifacts/logs/amv_formula_check/amv_formula_check.json")),
        help="结果 JSON 路径",
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    rep = run_check()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    from custos.research import backtest_factors as bf  # noqa: PLC0415

    bf.write_json_stream(out, rep, big=False)
    print(f"[OK] 写出 {out}")
    print_summary(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
