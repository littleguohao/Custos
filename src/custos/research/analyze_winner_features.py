# -*- coding: utf-8 -*-
"""赢家特征反向研究:MACD/KDJ/DMI(ADX) 在信号当时的判别力检验。

⚠️ 方法论(与全研究链一致):不做"赢家长什么样"的事后归纳(hindsight 陷阱,
完美B1指纹/alpha101/mcap 三次已证伪),而是把指标做成 as-of 特征,用
①日内分层 AUC(只在同日信号间比) ②每日 top-3 精确率 vs 公平随机基线
③前后半程同号(防过拟合) 三重门槛检验。标签=前向 20 日收益(不作特征)。

特征(as-of,信号日):
  macd_dif / macd_hist / macd_hist_rising(柱上行) / macd_dif_pos(DIF>0)
  kdj_k / kdj_d / kdj_j / kdj_j_rising(J上行)
  dmi_adx / dmi_pdi / dmi_mdi / dmi_pdi_gt(+DI>-DI) / dmi_adx_rising(ADX上行)
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from custos.core.indicators import dmi_arrays  # noqa: E402  DMI/ADX 唯一实现


from custos.research import backtest_factors as bt  # noqa: E402
from custos.core.paths import LOGS  # noqa: E402
from custos.datasource.local_tdx import local_tdx_data  # noqa: E402
from custos.research.launch_point_study import _auc  # noqa: E402  Mann-Whitney 正确实现(本文件曾用错公式)

FIRINGS = LOGS / "walkforward" / "firings_jlow_2026H1_tdx.json"
FWD = 20
WIN_Q = 0.2  # 全体前 20% 算"跑出来"
PICKS = 3


def _cache_path() -> Path:
    """Sample cache keyed by the parameters that determine its content.

    A single fixed filename silently reused the previous run's samples after
    FIRINGS or FWD changed, so a study would report on a different signal pool
    than the one requested — and that stale pool fed the split-consistency
    check that gates feature adoption.
    """
    import hashlib

    try:
        sig = f"{FIRINGS.name}:{FIRINGS.stat().st_mtime_ns}:{FWD}"
    except OSError:
        sig = f"{FIRINGS.name}:missing:{FWD}"
    digest = hashlib.sha256(sig.encode()).hexdigest()[:12]
    return LOGS / "walkforward" / f"winner_feature_rows_{digest}.json"


def _adx_features(high, low, close, n: int = 14) -> dict:
    """Wilder DMI/ADX（as-of 最后一点）。

    ⚠️ 计算体 2026-08-10 收敛到 `indicators.dmi_arrays` —— 它与
    `backtest_factors._adx_last` 曾是**逐行相同**的 19 行复制粘贴。
    这里取 pdi/mdi/adx 与两个派生标志。
    """
    pdi, mdi, adx = dmi_arrays(high, low, close, n)
    if adx is None or pdi is None or mdi is None:  # 三者同生同灭，写全便于收窄
        return {}
    return {
        "dmi_adx": round(float(adx[-1]), 2),
        "dmi_pdi": round(float(pdi[-1]), 2),
        "dmi_mdi": round(float(mdi[-1]), 2),
        "dmi_pdi_gt": float(pdi[-1] > mdi[-1]),
        "dmi_adx_rising": float(adx[-1] > adx[-2]),
    }


def _features(df, i: int) -> dict:
    sub = df.iloc[: i + 1]
    c = sub["close"].astype(float)
    feats: dict = {}
    h = bt._macd_hist(c)
    dif = bt._macd_dif_series(
        c
    )  # 2026-08-09 收敛：DIF 复用唯一实现（原内联 EMA12−EMA26 同式）
    feats["macd_dif"] = round(float(dif.iloc[-1]), 4)
    feats["macd_hist"] = round(float(h.iloc[-1]), 4)
    feats["macd_hist_rising"] = float(h.iloc[-1] > h.iloc[-2])
    feats["macd_dif_pos"] = float(dif.iloc[-1] > 0)
    k = bt._kdj(sub) if bt._kdj is not None else {}
    if k.get("available"):
        feats["kdj_k"] = round(float(k["k"]), 2)
        feats["kdj_d"] = round(float(k["d"]), 2)
        feats["kdj_j"] = round(float(k["j"]), 2)
        if len(c) >= 2 and bt._kdj is not None:
            k2 = bt._kdj(sub.iloc[:-1])
            if k2.get("available"):
                feats["kdj_j_rising"] = float(k["j"] > k2["j"])
    feats.update(
        _adx_features(
            sub["high"].astype(float).values, sub["low"].astype(float).values, c.values
        )
    )
    return feats


def _build_rows(cache: Path) -> list:
    """读 FIRINGS 信号池,逐股取 K 线、逐信号算 as-of 特征+前向收益标签,写缓存。"""
    rows = []
    payload = json.loads(FIRINGS.read_text(encoding="utf-8"))
    recs = payload.get("records") or []
    print(
        f"信号池 {sum(len(r.get('days') or []) for r in recs)} 个(j_low 全年),逐信号算特征+标签…",
        file=sys.stderr,
    )
    bars_cache: dict = {}
    for n, r in enumerate(recs):
        code = r["code"]
        if code not in bars_cache:
            try:
                df = local_tdx_data.get_ohlcv_table(code, count=2000)
                if df is not None and len(df):
                    df = df.copy()
                    df["date"] = df["date"].astype(str).str[:10]
                    bars_cache[code] = df.sort_values("date").reset_index(drop=True)
                else:
                    bars_cache[code] = None
            except Exception:  # noqa: BLE001
                bars_cache[code] = None
        df = bars_cache[code]
        if df is None:
            continue
        closes = df["close"].astype(float).values
        for d in r.get("days") or []:
            idx = df.index[df["date"] == d[0]]
            if not len(idx):
                continue
            i = int(idx[0])
            if i + FWD >= len(closes) or not closes[i]:
                continue
            fwd = closes[i + FWD] / closes[i] - 1
            try:
                feats = _features(df, i)
            except Exception:  # noqa: BLE001
                continue
            rows.append({"date": d[0], "y": fwd, "feats": feats})
        if (n + 1) % 500 == 0:
            print(
                f"  {n + 1}/{len(recs)} 股 | {len(rows)} 样本",
                file=sys.stderr,
                flush=True,
            )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def _label_and_group(rows: list) -> tuple:
    """全局 win 标签(全体前 WIN_Q 分位)+ 基准率 + 按交易日分组。"""
    ys = sorted((x["y"] for x in rows), reverse=True)
    thr = ys[int(len(ys) * WIN_Q) - 1]
    for x in rows:
        x["win"] = x["y"] >= thr
    base = sum(1 for x in rows if x["win"]) / len(rows)
    by_day: dict = defaultdict(list)
    for x in rows:
        by_day[x["date"]].append(x)
    return thr, base, by_day


def _split_halves(rows: list, by_day: dict) -> tuple:
    """按日期中点切前后半程(调用前 rows 必须已按 date 排序)。"""
    dates = sorted(by_day)
    split_date = dates[len(dates) // 2] if dates else ""
    first_half = [x for x in rows if x["date"] < split_date]
    second_half = [x for x in rows if x["date"] >= split_date]
    return split_date, first_half, second_half


def _half_win_threshold(half: list) -> float:
    """半程自己的 win 阈值(不能用全样本分位,否则标签泄漏)。"""
    hy = sorted((x["y"] for x in half), reverse=True)
    return hy[max(0, int(len(hy) * WIN_Q) - 1)]


def _intraday_stats(by_day: dict, vf) -> tuple:
    """日内分层 AUC(按 pos*neg 加权聚合)+ 每日 top-PICKS 精确率 vs 公平随机基线。"""
    num = den = 0.0
    hit = tot = 0
    fair = 0.0
    for _day, lst in by_day.items():
        cand = [x for x in lst if vf(x) is not None]
        if not cand:
            continue
        pos = [vf(x) for x in cand if x["win"]]
        neg = [vf(x) for x in cand if not x["win"]]
        a = _auc(pos, neg)
        if a is not None:
            num += a * len(pos) * len(neg)
            den += len(pos) * len(neg)
        k = min(PICKS, len(cand))
        wr = sum(1 for x in cand if x["win"]) / len(cand)
        fair += k * wr
        cand.sort(key=vf, reverse=True)
        for x in cand[:k]:
            tot += 1
            hit += int(x["win"])
    auc = num / den if den else None
    prec = hit / tot if tot else None
    fair_p = fair / tot if tot else None
    return auc, prec, fair_p


def _half_gap(samples: list, vf):
    """半程内 AUC-0.5(用该半程自己的 win_half 标签)。"""
    p = [vf(x) for x in samples if x.get("win_half") and vf(x) is not None]
    q = [vf(x) for x in samples if not x.get("win_half") and vf(x) is not None]
    a = _auc(p, q)
    return a - 0.5 if a is not None else None


def _eval_feature(f: str, by_day: dict, first_half: list, second_half: list) -> dict:
    """单特征三重指标:日内 AUC、top-PICKS 精确率/净增益、前后半程同号。"""
    vf = lambda x: x["feats"].get(f)
    auc, prec, fair_p = _intraday_stats(by_day, vf)
    # 前后半程同号(按日期切分,各半程用自己的 win_half 标签)
    h1, h2 = _half_gap(first_half, vf), _half_gap(second_half, vf)
    consistent = h1 is not None and h2 is not None and (h1 > 0) == (h2 > 0)
    return {
        "f": f,
        "auc": auc,
        "prec": prec,
        "fair": fair_p,
        "lift": (prec - fair_p) if prec and fair_p else None,
        "h1": h1,
        "h2": h2,
        "consistent": consistent,
    }


def _select_strong(results: list) -> list:
    """三重门槛筛选:日内AUC≥0.53 + 净增益≥2pp + 半程同号。"""
    return [
        r
        for r in results
        if r["auc"]
        and r["auc"] - 0.5 >= 0.03
        and (r["lift"] or 0) >= 0.02
        and r["consistent"]
    ]


def main() -> None:
    cache = _cache_path()
    if cache.is_file():
        rows = json.loads(cache.read_text(encoding="utf-8"))
        print(f"复用样本缓存 {cache.name}（{len(rows)} 样本）", file=sys.stderr)
    else:
        rows = _build_rows(cache)

    if not rows:
        # 空样本走下去会在 ys[...] 上 IndexError,或(有缓存时)把空结论写进研究产物
        raise SystemExit("no usable samples: check FIRINGS pool and local bar coverage")

    print(f"\n有效样本 {len(rows)} 个 | 标签:前向{FWD}日收益 前{int(WIN_Q * 100)}%为赢")
    thr, base, by_day = _label_and_group(rows)

    # 半程切分**必须按日期**。rows 是外层遍历股票、内层遍历该股信号日 append 出来的,
    # 所以 rows[:mid] / rows[mid:] 切出来的是「前一半股票的全部年份」vs「后一半股票的
    # 全部年份」——那是两个股票集合的对比,与"样本外时间段是否复现"毫无关系,防过拟合
    # 门槛因此恒通过。三个已上线/曾上线的 gate(j_low_dif_pos / j_low_adx25 /
    # j_low_adx60)都把"半程一致"写进了采纳依据。
    rows.sort(key=lambda x: x["date"])
    split_date, first_half, second_half = _split_halves(rows, by_day)
    # 每半程各自定 win 阈值:用全样本分位再切半程会把另一半的分布信息带进来(标签泄漏),
    # 而 consistent 直接决定特征是否被采纳。
    for half in (first_half, second_half):
        if not half:
            continue
        hthr = _half_win_threshold(half)
        for x in half:
            x["win_half"] = x["y"] >= hthr
    print(
        f"半程切分点 {split_date}（前段 {len(first_half)} 样本 / 后段 {len(second_half)} 样本，"
        f"各自独立定阈值）"
    )

    feat_names = sorted({k for x in rows for k in x["feats"]})
    print(
        f"基准率 {base:.1%} | {'特征':<18}{'日内AUC':>8}{'精确率':>8}{'公平随机':>8}{'净增益':>8}  半程一致"
    )
    results = []
    for f in feat_names:
        r = _eval_feature(f, by_day, first_half, second_half)
        results.append(r)
        auc, prec, fair_p, consistent = r["auc"], r["prec"], r["fair"], r["consistent"]
        print(
            f"{'':2}{f:<18}{(f'{auc:.4f}' if auc else '-'):>8}{(f'{prec:.1%}' if prec else '-'):>8}"
            f"{(f'{fair_p:.1%}' if fair_p else '-'):>8}"
            f"{(f'{(prec - fair_p) * 100:+.1f}pp' if prec and fair_p else '-'):>8}"
            f"  {'✓' if consistent else '✗'}"
        )
    strong = _select_strong(results)
    print(
        f"\n过三重门槛(日内AUC≥0.53 + 净增益≥2pp + 半程同号): "
        f"{[r['f'] for r in strong] or '无'}"
    )
    out = {
        "n": len(rows),
        "thr": thr,
        "base_rate": base,
        "features": results,
        "strong": strong,
    }
    (LOGS / "walkforward" / "winner_feature_study.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
