# -*- coding: utf-8 -*-
"""S_shape 因子走查回测校准（walk-forward，纯分析、只读本地日线、绝不触碰管线）。

回答的问题：S_shape v3.0 的 S**（及各分项、建议档）能否区分"后市涨/跌"？
用于把 s_shape.py 里那些**待回测/猜测阈值**校准到有胜率与 MFE/MAE 支撑的值。

无未来函数：对每个 (股票, as-of 交易日 i)，只用 df[:i+1]（含当日）算 compute_s_shape，
前向指标只看 df[i+1 : i+H]（严格未来），两者绝不重叠。

CLI（在有本地通达信日线的机器上跑）::

    uv run python 07_tools/research/backtest_factors.py --codes 600000,000001 --count 500 \
        --horizons 5,10,20 --out 01_data/screening/backtest_s_shape.json

评估逻辑与数据加载解耦：evaluate() 接收 {code: DataFrame}，便于单测注入合成 bars。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

_RESEARCH_DIR = Path(__file__).resolve().parent
_TOOLS = _RESEARCH_DIR.parent
for _p in (str(_TOOLS), str(_RESEARCH_DIR), str(_TOOLS / "local_tdx")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_FACTORS_DIR = str(Path(__file__).resolve().parents[1] / "factors")
if _FACTORS_DIR not in sys.path:
    sys.path.insert(0, _FACTORS_DIR)   # 因子层：见 factors/__init__.py

# GBK（cp936）终端/管道下 --help 与报告里的 ⇒/⚠️ 等符号会 UnicodeEncodeError
# 直接崩掉。惯例同项目其他入口（hasattr 守卫：pytest 捕获替换过 stdout）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── research/ 与 screening/ 分家（2026-08-07）后的路径引导。
# 研究脚本要能同时导**自己的兄弟**（research/）与**生产链模块**（screening/）：
# 方向是研究依赖生产（回测要跑生产的因子与打分），反向为 0 ——
# 见 tests/test_architecture_layers.py。
for _p in (str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / "screening")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# 9 个自包含 scorer 已抽到 factors/ 各自成模块（2026-08-06），此处仅保留别名。
from alpha101 import score as _sc_alpha101  # noqa: E402
from alpha_pvcorr import score as _sc_alpha_pvcorr  # noqa: E402
from baseline import score as _sc_baseline  # noqa: E402
from kdj_j import score as _sc_kdj_j  # noqa: E402
from low_vol import score as _sc_low_vol  # noqa: E402
from mcap import score as _sc_mcap  # noqa: E402
from momentum import score as _sc_momentum  # noqa: E402
from reversal_quality import score as _sc_reversal_quality  # noqa: E402
from reversal_quality_inv import score as _sc_reversal_quality_inv  # noqa: E402


from indicators import bbi_series as _bbi_series, macd_series as _macd_series  # noqa: E402
from code_utils import price_limit_pct  # noqa: E402

def _bbi_series_from(df: pd.DataFrame) -> np.ndarray:
    """DataFrame 入口的 BBI（返回 ndarray）—— 逐 bar 评估里用 ndarray 更快。"""
    return _bbi_series(df["close"]).to_numpy()


from s_shape import compute_s_shape, compute_s_reversal, SSHAPE_MIN_BARS, SSTAR_STRONG, SSTAR_MID  # noqa: E402

try:
    from indicators import kdj as _kdj # noqa: E402
except Exception:  # noqa: BLE001
    _kdj = None

J_LOW_THRESHOLD = 13.0

# 尾窗口（evaluate/gate_window）的保守预热长度：覆盖本模块所有门槛与打分器的
# 最长回看 —— s_shape 的 OVERHEAD_WIN=60 / MA50、CZ 的 250 日窗口、
# _sc_momentum 的 100+20 自适应回看、以及 KDJ/MACD 递归指标的衰减预热。
# 取 260（＝生产链默认加载根数）后与全前缀逐字段一致，见
# tests/test_audit_opt_screening.py::test_evaluate_gate_window_matches_full_prefix。
GATE_WINDOW_SAFE = 260


def j_low_gate(df_slice: pd.DataFrame) -> bool:
    """as-of 切片当日 KDJ 的 J<13（B1 买点区）。kdj 不可用时视为不通过。"""
    if _kdj is None:
        return False
    r = _kdj(df_slice)
    return bool(r.get("available") and r.get("j") is not None and r["j"] < J_LOW_THRESHOLD)


# 完整 B1 反转K：J<13 + 缩量(量比≤50%) + 20日量底10% + 收盘变动±2% + 振幅≤7%（企稳，非落刀）
# ⚠️ 常量值**刻意钉死 = live 默认值**（`b1_thresholds` 的默认，不跟随 B1_REVK_* env）——
# 研究钉死才能复现既有回测数字；判定逻辑（round-2 涨跌幅、prev_close 振幅分母、`<` 量分位）
# 2026-08-09 已与 live 对齐，两边常量相等由
# tests/test_enrich_b1cz.py::TestReversalKThresholdSingleSource 钉住。
REVK_VOL_RATIO = 0.5
REVK_VOL_PCTILE = 0.10
REVK_CHG_PCT = 2.0
REVK_AMP_PCT = 7.0


def reversal_k_gate(df_slice: pd.DataFrame) -> bool:
    """B1 反转K 完整买点：J<13 且缩量企稳(小实体/小振幅)——排除收盘贴低的落刀。绝不 raise。

    2026-08-09 对齐 live（`enrich_candidates.compute_metrics` 经 `b1_thresholds`）：
    涨跌幅按 **round-2 显示精度**判定（2026-08-07 owner 拍板的刻意行为）、
    振幅分母 prev_close、量分位用 `<`。常量仍钉死默认值，不读 env（见上方注释）。
    """
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
        if (v20 < vol[-1]).mean() > REVK_VOL_PCTILE:                   # 当日量在20日底部10%（`<`，与 live 同向）
            return False
        chg = (close[-1] / close[-2] - 1) * 100 if close[-2] else 99   # 收盘变动 ±2%
        # 判定精度 = 显示精度（round-2），同 live 的 b1_thresholds.change_in_range。
        # 不直接调 change_in_range：它读 env 覆盖值，而研究侧要钉死默认 ±2%。
        if not (-REVK_CHG_PCT <= round(chg, 2) <= REVK_CHG_PCT):
            return False
        amp = (high[-1] - low[-1]) / close[-2] * 100 if close[-2] else 99  # 振幅≤7%（分母 prev_close，同 live）
        return bool(amp <= REVK_AMP_PCT)
    except Exception:  # noqa: BLE001
        return False


ENTRY_GATES: dict[str, Optional[Callable[[pd.DataFrame], bool]]] = {
    "none": None,        # 每根 K 线都当信号（全市场基线）
    "j_low": j_low_gate,  # 只在 J<13 入场区评估（仅J,含落刀）
    "reversal_k": reversal_k_gate,  # 完整 B1 反转K：J<13+缩量企稳(排除贴低落刀)
    "j_macd_turn": None,  # 占位，下方 j_low_macd_turn_gate 定义后回填
}


def _macd_hist(close: pd.Series) -> pd.Series:
    """MACD 柱(DIF-DEA, 12/26/9)。柱>0=红、<0=绿；柱上行=绿柱缩短或红柱变长。

    ⚠️ 口径是 **×1**（柱 = DIF − DEA），刻意**不是** `indicators.macd` 的中式 ×2 ——
    本模块语义就是「柱=dif-dea」。EMA 走唯一实现 `macd_series`（2026-08-09 收敛），
    这里只做相减，不得再自己写 EMA。
    """
    dif, dea, _hist_x2 = _macd_series(close)
    return dif - dea


def j_low_macd_turn_gate(df_slice: pd.DataFrame) -> bool:
    """B1×MACD：J<13 且 MACD 柱上行(绿柱缩短/红柱变长=动量拐头向上)。等待动量拐头,避开落刀。绝不 raise。"""
    if _kdj is None or len(df_slice) < 35:
        return False
    try:
        r = _kdj(df_slice)
        if not (r.get("available") and r.get("j") is not None and r["j"] < J_LOW_THRESHOLD):
            return False
        h = _macd_hist(df_slice["close"])
        return bool(len(h) >= 2 and h.iloc[-1] > h.iloc[-2])   # 柱上行(拐头)
    except Exception:  # noqa: BLE001
        return False


ENTRY_GATES["j_macd_turn"] = j_low_macd_turn_gate


def _macd_dif_series(close: pd.Series) -> pd.Series:
    """DIF 序列（12/26）—— 委托 `indicators.macd_series`（2026-08-09 收敛，唯一实现）。"""
    return _macd_series(close)[0]


def j_low_dif_pos_gate(df_slice: pd.DataFrame) -> bool:
    """J<13 且 DIF>0——『强势里的回踩』(赢家特征研究:mac_dif_pos 日内AUC 0.548/+3.3pp/半程一致)。
    超卖但中期趋势未破零轴,区别于下跌途中的新低。绝不 raise。"""
    if _kdj is None or len(df_slice) < 35:
        return False
    try:
        r = _kdj(df_slice)
        if not (r.get("available") and r.get("j") is not None and r["j"] < J_LOW_THRESHOLD):
            return False
        return bool(_macd_dif_series(df_slice["close"]).iloc[-1] > 0)
    except Exception:  # noqa: BLE001
        return False


def _adx_last(df_slice: pd.DataFrame, n: int = 14) -> float:
    """Wilder ADX 最后一点(len 不足返回 NaN)。"""
    h = df_slice["high"].astype(float).values
    l = df_slice["low"].astype(float).values
    c = df_slice["close"].astype(float).values
    if len(c) < 2 * n + 2:
        return float("nan")
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    pdm = np.where((h[1:] - h[:-1]) > (l[:-1] - l[1:]), np.maximum(h[1:] - h[:-1], 0), 0.0)
    mdm = np.where((l[:-1] - l[1:]) > (h[1:] - h[:-1]), np.maximum(l[:-1] - l[1:], 0), 0.0)

    def wilder(x):
        out = np.zeros(len(x))
        out[n - 1] = x[:n].sum()
        for i in range(n, len(x)):
            out[i] = out[i - 1] - out[i - 1] / n + x[i]
        return out / n

    atr, sp, sm = wilder(tr), wilder(pdm), wilder(mdm)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = np.where(atr > 0, 100 * sp / atr, 0.0)
        mdi = np.where(atr > 0, 100 * sm / atr, 0.0)
        dx = np.where(pdi + mdi > 0, 100 * abs(pdi - mdi) / (pdi + mdi), 0.0)
    adx = np.zeros(len(dx))
    adx[2 * n - 2] = dx[n - 1:2 * n - 1].mean()
    for i in range(2 * n - 1, len(dx)):
        adx[i] = (adx[i - 1] * (n - 1) + dx[i]) / n
    return float(adx[-1])


def _j_low_adx_gate(df_slice: pd.DataFrame, thr: float) -> bool:
    if _kdj is None or len(df_slice) < 35:
        return False
    try:
        r = _kdj(df_slice)
        if not (r.get("available") and r.get("j") is not None and r["j"] < J_LOW_THRESHOLD):
            return False
        a = _adx_last(df_slice)
        return bool(a == a and a >= thr)
    except Exception:  # noqa: BLE001
        return False


def j_low_adx25_gate(df_slice: pd.DataFrame) -> bool:
    """J<13 且 ADX≥25——前段趋势决绝(赢家特征研究:dmi_adx 日内AUC 0.544/+13.7pp/半程一致)。
    超卖池里 ADX 高=跌透/趋势明确,反弹更有力。绝不 raise。"""
    return _j_low_adx_gate(df_slice, 25)


def j_low_adx60_gate(df_slice: pd.DataFrame) -> bool:
    """J<13 且 ADX≥60——极端趋势强度(分档:赢占比 18.1%→21.7%→25.4%→33.5% 单调上行,
    但样本仅 0.2%,须净值终审)。绝不 raise。"""
    return _j_low_adx_gate(df_slice, 60)


ENTRY_GATES["j_low_dif_pos"] = j_low_dif_pos_gate
ENTRY_GATES["j_low_adx25"] = j_low_adx25_gate
ENTRY_GATES["j_low_adx60"] = j_low_adx60_gate


# --- 门槛统计:"没跑成"必须与"真的不命中"分开 -----------------------------------
# 一个 gate 里 `except Exception: return False` 会把**依赖缺失/检测器异常**伪装成
# "这根 K 线不符合形态"——于是整轮回测 0 命中,结论写成"该因子无判别力",而真相是它
# 一次都没被真正评估过(审计 E8)。库内 --sector-filter 走的是 ap.error 硬校验,两套标准。
GATE_STATS: dict[str, dict[str, int]] = {}
_WARNED_ONCE: set[str] = set()
BROKEN_GATE_KINDS = ("dep_missing", "error")


def reset_gate_stats() -> None:
    """清空门槛统计(单测/多轮扫描之间隔离)。"""
    GATE_STATS.clear()
    _WARNED_ONCE.clear()


def gate_stats() -> dict[str, dict[str, int]]:
    """{gate 名: {hit/miss/dep_missing/error/short_history: 次数}}。"""
    return GATE_STATS


def _note_gate(name: str, kind: str) -> None:
    d = GATE_STATS.setdefault(name, {})
    d[kind] = d.get(kind, 0) + 1


def _warn_once(key: str, msg: str) -> None:
    if key not in _WARNED_ONCE:
        _WARNED_ONCE.add(key)
        print(f"[WARN] {msg}", file=sys.stderr)


def gate_stats_report() -> dict[str, Any]:
    """整理门槛统计;`broken` 非空 = 有门槛因依赖缺失/异常而**从未真正评估**,
    此时"该因子无判别力"的结论不成立,必须先修依赖再重跑。"""
    broken: list[dict[str, Any]] = []
    lines: list[str] = []
    for name, st in sorted(GATE_STATS.items()):
        n_bad = sum(st.get(k, 0) for k in BROKEN_GATE_KINDS)
        lines.append(f"  {name}: 命中 {st.get('hit', 0)} / 不命中 {st.get('miss', 0)}"
                     f" / 依赖缺失 {st.get('dep_missing', 0)} / 异常 {st.get('error', 0)}"
                     f" / 历史不足 {st.get('short_history', 0)}")
        if n_bad:
            broken.append({"gate": name, "n_failed": n_bad, **st})
    if broken:
        lines.append("  ⚠️ 上列门槛存在**依赖缺失/异常**:这些 K 线根本没被评估过,"
                     "不得据此下'该因子无判别力'的结论——先修依赖再重跑")
    return {"broken": broken, "stats": {k: dict(v) for k, v in GATE_STATS.items()},
            "text": "\n".join(lines)}


def platform_pullback_gate(df_slice: pd.DataFrame) -> bool:
    """平台突破回踩(回踩不破前期平台高点):三段式——平台(≥2触上沿)→有效突破→
    回落至平台高附近但未破。止损天然=平台高×0.98(--stop-mode platform)。绝不 raise。
    依赖缺失/检测器异常与"真的不命中"分开计数(见 gate_stats_report)。"""
    if len(df_slice) < 65:
        _note_gate("platform_pullback", "short_history")
        return False
    try:
        from platform_pullback import detect_platform_pullback  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        _note_gate("platform_pullback", "dep_missing")
        _warn_once("platform_pullback:dep",
                   f"platform_pullback 检测器不可用({exc}):该入场门槛将全程 0 命中,"
                   "结果只能读成'没跑成'而非'无判别力'")
        return False
    try:
        hit = detect_platform_pullback(df_slice) is not None
    except Exception as exc:  # noqa: BLE001
        _note_gate("platform_pullback", "error")
        _warn_once("platform_pullback:err",
                   f"platform_pullback 检测器异常({exc.__class__.__name__}: {exc}):"
                   "该 K 线未被评估,已计入 GATE_STATS.error")
        return False
    _note_gate("platform_pullback", "hit" if hit else "miss")
    return hit


ENTRY_GATES["platform_pullback"] = platform_pullback_gate

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
    # 函数本体在因子层 `factors/b1_pullback_fit.py`；此前从 `enrich_candidates`
    # 导入只是蹭它顶层的偶然再导出（2026-08-08 订正）。保持 lazy：避免重导入开销。
    from b1_pullback_fit import compute_b1_pullback_fit  # noqa: PLC0415
    r = compute_b1_pullback_fit(df)
    if not r.get("available"):
        return None
    return {"score": round(r["score"] / 7 * 100, 1),
            "suggestion": "可买" if r.get("hit") else "不买",
            "aux": {"fit_raw": r["score"], "hit": r["hit"]},
            "components": {k: (1.0 if v else 0.0) for k, v in (r.get("components") or {}).items()}}




SCORERS = {"s_shape": _sc_s_shape, "s_reversal": _sc_s_reversal,
           "invert_s_shape": _sc_invert_s_shape, "b1_pullback": _sc_b1_pullback,
           "baseline": _sc_baseline}


# --- 借鉴「101 Formulaic Alphas」(Kakushadze 2016) 的思想：纯**选择器**,配 --entry-filter 定义 B1 池,
#     --top-n 做横截面择优。⚠️ 原论文 alpha 为 0.6~6.4 日超短持有的市场中性反转,与 B1(周级/单边/择时)
#     不同源,是否加值必须回测验证；此处仅作可排序因子,suggestion 恒「可买」,靠 entry_gate 约束进场池。 ---
# （2026-08-09：本处原有的死 `_ts_corr` 已删 —— 无调用方；唯一实现在 `factors/_util.ts_corr`。）






SCORERS["alpha101"] = _sc_alpha101
SCORERS["alpha_pvcorr"] = _sc_alpha_pvcorr


# --- 借鉴 Fama-French 因子「特征排序」思想：把已被文献验证有溢价的特征做成横截面选择器,
#     在 B1 池(entry_gate)里 top-N 择优。注意:FF 是风险/归因模型(月频/基本面),非交易信号;
#     A股应以 CH-3/CH-4(Liu-Stambaugh-Yuan) 为准(壳调整size+EP价值+换手)。是否加值须回测。
#     size/value/profitability 需股本/财务(见 financials.py),此处仅实现价格可算的 low-vol / momentum。---




SCORERS["low_vol"] = _sc_low_vol
SCORERS["momentum"] = _sc_momentum




SCORERS["reversal_quality"] = _sc_reversal_quality




SCORERS["reversal_quality_inv"] = _sc_reversal_quality_inv


# 股本索引已移到 factors/_shares.py（唯一所有者）；此处仅委托。
from factors._shares import shares_idx as _shares_idx  # noqa: E402  ⚠️ 必须包限定






SCORERS["mcap"] = _sc_mcap




SCORERS["kdj_j"] = _sc_kdj_j

# ---- B1 双轴组合（长期结构 × 短期回调）+ 突破回踩型 B1 ----
# owner 2026-08-03 裁定:B1 是单纯回调买入,故 s_shape 的突破式分项(pivot/pocket_pivot/
# compression)不进技术轴;轴1 软加权。依据 other/good_b1.pptx 九例形态统计,详见
# b1_dual_factor 模块 docstring。**先回测验证再谈接入选股链。**
try:
    from b1_dual_factor import (compute_b1_dual, compute_long_structure,
                                detect_breakout_pullback_b1,
                                detect_weekly_b1_resonance)
except Exception:  # noqa: BLE001 —— 缺依赖时不阻断其它 scorer
    compute_b1_dual = None


def _sc_b1_dual(df: pd.DataFrame, code: str):
    """双轴组合分:W_STRUCT×长期结构 + W_REVERSAL×短期回调。"""
    if compute_b1_dual is None:
        return None
    r = compute_b1_dual(df, code)
    if not r.get("available"):
        return None
    return {"score": r["score"], "suggestion": r["suggestion"],
            "aux": {"long_structure": r["long_structure"],
                    "short_reversal": r["short_reversal"],
                    "qsx_gt_dks": r["qsx_gt_dks"],
                    "weekly_resonance": r["weekly_resonance"],
                    "score_without_resonance": r["score_without_resonance"]},
            "components": {"struct": r["long_structure"], "reversal": r["short_reversal"]}}


def _sc_long_structure(df: pd.DataFrame, code: str):
    """消融用:只有轴1(长期结构)。"""
    if compute_b1_dual is None:
        return None
    r = compute_long_structure(df)
    if not r.get("available"):
        return None
    return {"score": r["score"], "suggestion": "可买" if r["score"] >= 70 else "不买",
            "aux": {"qsx_gt_dks": r["qsx_gt_dks"]}, "components": r["components"]}


if compute_b1_dual is not None:
    SCORERS["b1_dual"] = _sc_b1_dual
    SCORERS["long_structure"] = _sc_long_structure


def _sc_b1_dual_no_resonance(df: pd.DataFrame, code: str):
    """消融用:双轴分**不含**周线共振加分(对比共振项是否真有增益)。"""
    if compute_b1_dual is None:
        return None
    r = compute_b1_dual(df, code)
    if not r.get("available"):
        return None
    base = r["score_without_resonance"]
    return {"score": base, "suggestion": "可买" if base >= 70 else "不买",
            "aux": {"weekly_resonance": r["weekly_resonance"]},
            "components": {"struct": r["long_structure"], "reversal": r["short_reversal"]}}


if compute_b1_dual is not None:
    SCORERS["b1_dual_no_res"] = _sc_b1_dual_no_resonance


def weekly_j_low_gate(df_slice: pd.DataFrame) -> bool:
    """周线 J<13(更大周期回调也到位)。绝不 raise。"""
    if compute_b1_dual is None:
        return False
    try:
        r = detect_weekly_b1_resonance(df_slice)
        return bool(r.get("available") and r.get("weekly_j_low"))
    except Exception:  # noqa: BLE001
        return False


def j_low_weekly_resonance_gate(df_slice: pd.DataFrame) -> bool:
    """日周线 B1 共振:日线 J<13 **且** 周线 J<13(owner 2026-08-03 提出的加分项)。"""
    if compute_b1_dual is None:
        return False
    try:
        return bool(detect_weekly_b1_resonance(df_slice).get("hit"))
    except Exception:  # noqa: BLE001
        return False


def j_low_qsx_weekly_gate(df_slice: pd.DataFrame) -> bool:
    """good_b1 全条件组合:J<13 + QSX>DKS + 周线 J<13。最严一档,用于看召回代价。"""
    return bool(j_low_weekly_resonance_gate(df_slice) and qsx_gt_dks_gate(df_slice))


def qsx_gt_dks_gate(df_slice: pd.DataFrame) -> bool:
    """长期多头结构:QSX>DKS(good_b1 8/9)。绝不 raise。"""
    if compute_b1_dual is None:
        return False
    try:
        r = compute_long_structure(df_slice)
        return bool(r.get("available") and r.get("qsx_gt_dks"))
    except Exception:  # noqa: BLE001
        return False


def j_low_qsx_gt_dks_gate(df_slice: pd.DataFrame) -> bool:
    """B1 核心组合:J<13 且 QSX>DKS —— "长期向上的票上买短期回调点"。"""
    return bool(j_low_gate(df_slice) and qsx_gt_dks_gate(df_slice))


def breakout_pullback_b1_gate(df_slice: pd.DataFrame) -> bool:
    """突破回踩型 B1:平台突破回踩不破 + J<13 + 收盘不低于平台高。"""
    if compute_b1_dual is None:
        return False
    try:
        return bool(detect_breakout_pullback_b1(df_slice).get("hit"))
    except Exception:  # noqa: BLE001
        return False


if compute_b1_dual is not None:
    ENTRY_GATES["qsx_gt_dks"] = qsx_gt_dks_gate
    ENTRY_GATES["j_low_qsx_gt_dks"] = j_low_qsx_gt_dks_gate
    ENTRY_GATES["breakout_pullback_b1"] = breakout_pullback_b1_gate
    ENTRY_GATES["weekly_j_low"] = weekly_j_low_gate
    ENTRY_GATES["j_low_weekly_resonance"] = j_low_weekly_resonance_gate
    ENTRY_GATES["j_low_qsx_weekly"] = j_low_qsx_weekly_gate


# ---- B2 战法 + 底部异动（来源 other/B1.pdf；见 b2_surge_factor 模块 docstring）----
try:
    from b2_surge_factor import detect_b2, detect_bottom_surge, detect_surge_then_b1
except Exception:  # noqa: BLE001
    detect_b2 = None


def b2_gate(df_slice: pd.DataFrame) -> bool:
    """B2:B1 之后 + 涨幅>4% + 比前一交易日放量 + J<55(原文 B1.pdf p16)。"""
    if detect_b2 is None:
        return False
    try:
        return bool(detect_b2(df_slice).get("hit"))
    except Exception:  # noqa: BLE001
        return False


def bottom_surge_gate(df_slice: pd.DataFrame) -> bool:
    """底部异动(巨量点火 + 量随价升)——宽口径。"""
    if detect_b2 is None:
        return False
    try:
        return bool(detect_bottom_surge(df_slice).get("hit"))
    except Exception:  # noqa: BLE001
        return False


def bottom_surge_strict_gate(df_slice: pd.DataFrame) -> bool:
    """底部异动严口径:再加 量能维持4天 + 穿越60日线 + 9个月新高 三条。"""
    if detect_b2 is None:
        return False
    try:
        return bool(detect_bottom_surge(df_slice).get("strict_hit"))
    except Exception:  # noqa: BLE001
        return False


def surge_then_b1_gate(df_slice: pd.DataFrame) -> bool:
    """原文第③条「找异动之后的 B1」:回看窗内有异动 且 当日 J<13。"""
    if detect_b2 is None:
        return False
    try:
        return bool(detect_surge_then_b1(df_slice).get("hit"))
    except Exception:  # noqa: BLE001
        return False


def surge_strict_then_b1_gate(df_slice: pd.DataFrame) -> bool:
    """异动严口径 + B1。"""
    if detect_b2 is None:
        return False
    try:
        return bool(detect_surge_then_b1(df_slice, strict_surge=True).get("hit"))
    except Exception:  # noqa: BLE001
        return False


def _sc_b2(df: pd.DataFrame, code: str):
    """B2 打分:四条硬条件命中数 ×20 + 无上影线 +20（0-100，待回测）。

    分数只用于回测内排序;原文没给权重,故按"命中条件数"这个最少假设的方式合成。
    """
    if detect_b2 is None:
        return None
    r = detect_b2(df, code)
    if not r.get("available"):
        return None
    hard = (int(bool(r["b1_before"])) + int(bool(r["gain_ok"]))
            + int(bool(r["vol_up"])) + int(bool(r["j_ok"])))
    score = hard * 20.0 + (20.0 if r.get("no_upper_shadow") else 0.0)
    return {"score": round(score, 1), "suggestion": "可买" if r["hit"] else "不买",
            "aux": {"b2_hit": r["hit"], "b1_bars_ago": r.get("b1_bars_ago"),
                    "gain_pct": r.get("gain_pct"), "no_upper_shadow": r.get("no_upper_shadow")},
            "components": {"hard_conditions": hard,
                           "no_upper_shadow": int(bool(r.get("no_upper_shadow")))}}


if detect_b2 is not None:
    SCORERS["b2"] = _sc_b2
    ENTRY_GATES["b2"] = b2_gate
    ENTRY_GATES["bottom_surge"] = bottom_surge_gate
    ENTRY_GATES["bottom_surge_strict"] = bottom_surge_strict_gate
    ENTRY_GATES["surge_then_b1"] = surge_then_b1_gate
    ENTRY_GATES["surge_strict_then_b1"] = surge_strict_then_b1_gate


# ---- RSI 状态 + 主升始发点(来源:微信文章公式) ----
try:
    from rsi_state import rsi_divergence, rsi_multi, rsi_regime, rsi_state_score
    from main_rally_factor import detect_main_rally_start, main_rally_score
except Exception:  # noqa: BLE001
    rsi_state_score = None


def _sc_rsi_state(df: pd.DataFrame, code: str):
    """RSI 状态分:区间四态 50 + 底背离 30 + 多周期 20(权重待回测)。"""
    if rsi_state_score is None:
        return None
    r = rsi_state_score(df, code)
    if not r.get("available"):
        return None
    return {"score": r["score"], "suggestion": "可买" if r["score"] >= 60 else "不买",
            "aux": {"rsi_regime": r["regime"], "rsi": r["rsi"],
                    "bullish_divergence": r["bullish_divergence"]},
            "components": {"regime": r["regime"]}}


def _sc_main_rally(df: pd.DataFrame, code: str):
    """主升始发点(源码口径 cross_mode=below)。"""
    if rsi_state_score is None:
        return None
    r = main_rally_score(df, code, cross_mode="below")
    if not r.get("available"):
        return None
    return {"score": r["score"], "suggestion": "可买" if r["hit"] else "不买",
            "aux": {"hit": r["hit"]},
            "components": {"conditions_met": r["detail"]["conditions_met"]}}


def rsi_strong_regime_gate(df_slice: pd.DataFrame) -> bool:
    """RSI 处于牛市区间(回调低点≥40 且曾>70)——健康回调而非下跌中继。"""
    if rsi_state_score is None:
        return False
    try:
        return bool(rsi_regime(df_slice).get("state") == "strong")
    except Exception:  # noqa: BLE001
        return False


def rsi_bullish_divergence_gate(df_slice: pd.DataFrame) -> bool:
    """RSI 底背离:价格创新低而 RSI 不创新低(卖压衰竭)。"""
    if rsi_state_score is None:
        return False
    try:
        return bool(rsi_divergence(df_slice).get("bullish"))
    except Exception:  # noqa: BLE001
        return False


def j_low_rsi_strong_gate(df_slice: pd.DataFrame) -> bool:
    """B1 核心组合的 RSI 版:J<13 且 RSI 处于牛市区间。"""
    return bool(j_low_gate(df_slice) and rsi_strong_regime_gate(df_slice))


def j_low_rsi_div_gate(df_slice: pd.DataFrame) -> bool:
    """J<13 且 RSI 底背离——超卖**且**动能衰竭,比单纯 J<13 强。"""
    return bool(j_low_gate(df_slice) and rsi_bullish_divergence_gate(df_slice))


def _main_rally_gate(df_slice: pd.DataFrame, mode: str) -> bool:
    if rsi_state_score is None:
        return False
    try:
        return bool(detect_main_rally_start(df_slice, cross_mode=mode).get("hit"))
    except Exception:  # noqa: BLE001
        return False


def main_rally_below_gate(df_slice: pd.DataFrame) -> bool:
    """主升始发点(**源码口径**:主升占比跌破 0.8)。"""
    return _main_rally_gate(df_slice, "below")


def main_rally_above_gate(df_slice: pd.DataFrame) -> bool:
    """主升始发点(**文字口径**:主升占比突破 0.8)——与源码相反,两者都测。"""
    return _main_rally_gate(df_slice, "above")


if rsi_state_score is not None:
    SCORERS["rsi_state"] = _sc_rsi_state
    SCORERS["main_rally"] = _sc_main_rally
    ENTRY_GATES["rsi_strong"] = rsi_strong_regime_gate
    ENTRY_GATES["rsi_bull_div"] = rsi_bullish_divergence_gate
    ENTRY_GATES["j_low_rsi_strong"] = j_low_rsi_strong_gate
    ENTRY_GATES["j_low_rsi_div"] = j_low_rsi_div_gate
    ENTRY_GATES["main_rally"] = main_rally_below_gate
    ENTRY_GATES["main_rally_above"] = main_rally_above_gate


def sample_codes(all_codes: list[str], n: int, seed: int = 0) -> list[str]:
    """从全 A 代码列表随机抽 N 只（带 seed 可复现），用于代表性样本校准。

    n<=0 或 n>=总数 → 返回全部（去空、去重、排序）。
    """
    codes = sorted({str(c).strip() for c in all_codes if str(c).strip()})
    if n <= 0 or n >= len(codes):
        return codes
    return sorted(random.Random(seed).sample(codes, n))


def forward_metrics(df: pd.DataFrame, i: int, horizon: int,
                    require_full: bool = True) -> dict[str, Any]:
    """as-of 第 i 根后、未来 horizon 根内的前向收益/MFE/MAE（严格只看 i+1..i+H）。

    入场基准＝第 i 根收盘价；前向窗口＝df[i+1 : i+horizon]（不含 i，杜绝未来泄漏）。

    ``require_full=True``（默认）时窗口不足 horizon 根即**删失**返回
    ``available=False``。此前是静默截断:样本末端只剩 3 根也照样把 3 日收益写进
    ``ret20`` 字段,于是「20 日收益」这一列混着 3~19 日的短窗收益参与统计,横截面
    比较和分位数全部失真。launch_point_study 对同一问题做的是删失,同库两套标准。
    传 ``require_full=False`` 可取回旧行为,此时结果带 ``truncated=True``。
    """
    n = len(df)
    if i < 0 or i >= n - 1:
        return {"available": False, "reason": "无未来K线"}
    entry = float(df["close"].iloc[i])
    if not entry:
        return {"available": False, "reason": "入场价为0"}
    available_bars = n - 1 - i
    if available_bars < horizon:
        if require_full:
            return {"available": False, "reason": "前向窗口不足",
                    "bars": available_bars, "need": horizon, "censored": True}
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
        "truncated": len(fut) < horizon,
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
    gate_window: int = 0,
) -> list[dict[str, Any]]:
    """逐股逐日走查：as-of 切片算打分，配前向指标。返回逐条记录（可复盘）。

    entry_gate(df_slice)->bool 若提供，只在返回 True 的 as-of 日评估（如 J<13 买点区）。
    scorer(df_slice, code)->{"score","suggestion","aux","components"} 或 None（默认 s_shape）。
    记录字段 s_star 存所选打分器的分数（沿用旧字段名，summarize/矩阵零改动）。

    gate_window>0：只把**最近 gate_window 根**传给 gate/scorer（而非整段前缀），
    与 launch_point_study 同一做法。原实现每根 K 线都切 df[:i+1] 再让打分器
    `.astype(float).to_numpy()` 整段——结构上是 O(n²) 时间＋线性增长的临时对象。
    ⚠️ 预热必须足够，否则**会改变因子值**（不是"略有出入"）：KDJ/MACD 是递归指标，
    `_sc_momentum` 的回看长度还随 len(df) 自适应（需 ≥121 根才等于全历史口径）。
    ``GATE_WINDOW_SAFE`` 是覆盖本模块全部打分器/门槛的保守值，与整段前缀逐字段等价
    （tests/test_audit_opt_screening.py::test_evaluate_gate_window_matches_full_prefix）。

    默认 0＝整段前缀。实测（2026-08-03 审计）：compute_s_shape 的单次开销
    100/260/1000/4000 根分别 2.58/2.60/2.61/2.65 ms —— 几乎与切片长度无关，
    被 pandas 的固定开销主导；3000 根、每根都出信号时 peak 内存 1.8MB→1.6MB。
    也就是说这里的 O(n²) 是**结构性的、当前不是瓶颈**，故只提供开关、不改默认，
    保住已跑结果的可复现性；将来若加了真正随长度线性的打分器，直接开 gate_window。
    """
    scorer = scorer or _sc_s_shape
    gate_window = max(0, int(gate_window or 0))
    records: list[dict[str, Any]] = []
    for code, raw in bars_by_code.items():
        if raw is None or len(raw) == 0:
            continue
        df = raw.sort_values("date").reset_index(drop=True)
        n = len(df)
        emitted = 0
        for i in range(min_bars, n - 1, max(1, step)):
            lo = max(0, i + 1 - gate_window) if gate_window else 0
            slice_df = df.iloc[lo:i + 1]  # 只含 lo..i（含当日），无未来
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
                fm = forward_metrics(df, i, h)  # 只用到 i+1..i+H；窗口不足即删失
                rec[f"ret{h}"] = fm.get("fwd_return")
                rec[f"mfe{h}"] = fm.get("mfe")
                rec[f"mae{h}"] = fm.get("mae")
                rec[f"ret{h}_bars"] = fm.get("bars")   # 删失/截断可诊断
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

    # 分项命中 lift：分项得分 > 0 视为命中，比较命中/未命中两组。
    # 审计：键集原来只取 records[0]——第一条记录恰好缺某个 c_* 分项（打分器降级、
    # 分项 available=False、或混跑了两个打分器）时，后面所有记录的该分项被静默丢掉，
    # 报告里"没有这一项"和"这一项没 lift"长得一模一样。改成全量取并集。
    comp_keys: list[str] = []
    seen: set[str] = set()
    for r in records:
        for k in r:
            if k.startswith("c_") and k not in seen:
                seen.add(k)
                comp_keys.append(k)     # 保持首次出现顺序，输出稳定可 diff
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



def _limit_pct(code: str) -> float:
    """委托 `code_utils.price_limit_pct`（唯一来源）。这份原本是**对的**那两份之一。"""
    return price_limit_pct(code)


def tradable_flags(df: pd.DataFrame, code: str) -> tuple[np.ndarray, np.ndarray]:
    """Per-bar (can_buy, can_sell) flags.

    A backtest that fills every order at the closing price silently assumes an
    always-liquid market. In A-shares that is wrong in three ways that all
    inflate results:

      * a limit-up close cannot be bought (especially a sealed 一字板 where
        high == low), yet the old code entered at exactly that price;
      * a limit-down close cannot be sold, yet stop losses filled there;
      * a halted day (volume == 0) has no trading at all, yet stops filled.

    Flags are conservative: any limit-up bar is treated as unbuyable rather
    than only sealed ones, because a backtest should not assume it caught the
    intraday dip.
    """
    close = df["close"].astype(float).to_numpy()
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    vol = (df["volume"].astype(float).to_numpy() if "volume" in df.columns
           else np.ones(len(close)))
    prev = np.concatenate(([np.nan], close[:-1]))
    limit = _limit_pct(code) / 100.0
    with np.errstate(invalid="ignore"):
        chg = close / prev - 1.0
    # 容差 0.3%:交易所涨停价按四舍五入取整,收盘正好封板时算得的比例会略有出入
    tol = 0.003
    halted = vol <= 0
    limit_up = chg >= (limit - tol)
    limit_down = chg <= -(limit - tol)
    sealed = high == low                      # 一字板:全天单一价位,完全无法成交
    can_buy = ~halted & ~limit_up & ~(sealed & limit_up)
    can_sell = ~halted & ~limit_down & ~(sealed & limit_down)
    can_buy[0] = False                        # 首根无前收,无法判定涨跌停
    can_sell[0] = False
    return can_buy, can_sell


def _next_tradable(flags: np.ndarray, start: int, max_delay: int) -> Optional[int]:
    """First index >= start where flags is True, within max_delay bars."""
    end = min(len(flags) - 1, start + max_delay)
    for k in range(start, end + 1):
        if flags[k]:
            return k
    return None


def _medium_large_bull_flags(df: pd.DataFrame, code: str = "") -> np.ndarray:
    """逐根标记"中大阳线"（B1 §六 第五层止盈的量化口径）。

    口径与 technical_monitor / 01_swing_rules.md 一致：必须是阳线（close>open），
    且**单日涨幅或阳线实体幅度** ≥ 半个涨停幅度（10%品种→5%、20%→10%、30%→15%）。
    """
    close = df["close"].astype(float).to_numpy()
    open_ = df["open"].astype(float).to_numpy()
    n = len(close)
    thr = _limit_pct(code) / 2.0
    prev = np.concatenate(([np.nan], close[:-1]))
    with np.errstate(divide="ignore", invalid="ignore"):
        chg = (close / prev - 1) * 100
        body = np.where(open_ > 0, (close - open_) / open_ * 100, 0.0)
    is_bull = close > open_
    return is_bull & ((chg >= thr) | (body >= thr))



_TICK = 0.01          # A股最小价格变动单位(元);材料的「向下 3-5 个价位」以此为单位


def _center_rising(closes: np.ndarray) -> bool:
    """收盘价**重心**是否上升（材料持股手册「一等马：收盘价重心上升为主」）。

    用前后两段均值比，而不是「末值 > 首值」：材料说的是「重心」——那是中枢概念，
    单点比较会被最后一根的噪声左右（一根小阴线就把「重心上升」判成否）。
    段内均值对单根波动不敏感，更贴近「收盘价重心」的原意。

    少于 4 根无法谈重心，返回 False（交给其它维度判定）。
    """
    n = len(closes)
    if n < 4:
        return False
    h = n // 2
    front = float(np.mean(closes[:h]))
    back = float(np.mean(closes[h:]))
    return back > front


def simulate_b1_trade(df: pd.DataFrame, entry_idx: int, bbi: pd.Series,
                      bbi_exit_consec: int = 2, time_stop_bars: int = 0,
                      stop_mode: str = "low", stop_pct: float = 8.0,
                      stop_override: Optional[float] = None,
                      can_sell: Optional[np.ndarray] = None,
                      max_exit_delay: int = 5,
                      scale_out_frac: float = 0.0,
                      code: str = "",
                      bull_flags: Optional[np.ndarray] = None,
                      breakeven_trigger: float = 0.0,
                      trail_pct: float = 0.0,
                      stop_trigger: str = "close",
                      stop_tick_buffer: int = 0,
                      cost_zone_bars: int = 0,
                      cost_zone_pct: float = 3.0,
                      cost_zone_grace: int = 1) -> dict[str, Any]:
    """B1 交易规则模拟：买入当日收盘进场。

    止损位：stop_override 显式指定(如平台高×0.98)优先;否则 stop_mode='low'=买入当日最低价
    (B1.pdf「B1- 买入K线最低点或向下3-5个价位」,余量见 ``stop_tick_buffer``)；
    'pct'=entry×(1-stop_pct%)(固定空间)。
    站上 BBI 后若连续 bbi_exit_consec 日收盘跌破 BBI 则止盈卖出。
    跳空低开按开盘价成交。返回 {exit_idx, reason, ret, holding, risk_frac}。

    ``stop_trigger``（**2026-08-04 按 B1_w.pdf 修正**）：

        "close"（默认）  收盘价跌破才算破位 —— 材料的明确口径：
                         「设止损…**看上下区间，看收盘价**」
                         「破掉止损价格，拍掉！（**收盘时**）」
                         「**忽略盘中的冲高回落**」「**不要在下杀中卖出**」
                         「不要在意盘中上蹿下跳，给老子他妈的拿住！」
        "intraday"       盘中最低价触及即出（**旧行为**，保留用于口径对照）

    原实现用盘中最低价判定，会把大量「盘中假破、收盘收回」记成止损，
    系统性高估止损次数、低估策略表现。

    ⚠️ **保本止损例外，始终按盘中判定**：材料对它的表述是
    「赚钱的票**有过上涨行为后**，马上回到成本价，拍掉！（**盘中关注**）」。
    这个区分是有道理的——常规止损位在下方较远，盘中假破常见，等收盘确认；
    保本位就是成本价，属心理防线，立即执行。

    ``stop_tick_buffer``：止损位再向下留几个**价位**（tick=0.01 元）。
    材料写「买入K线最低点**或向下 3-5 个价位**」——贴着最低点挂止损容易被一笔扫掉。
    默认 0 保持旧行为，建议 3。

    ``cost_zone_bars``>0 启用**「不涨就拍」**（材料持股手册四种马 + 仓位实例）。
    进场后 ``cost_zone_bars + cost_zone_grace`` 根时检查，**三个维度都平淡才平仓**：

        · 未站上 BBI        「不温不火，**没上BBI**，又没到止损。收盘前全拍！」
        · 收盘价重心未上升   「一等马（**收盘价重心上升**为主）⇒ 拿住不动」
                            「看每天的收盘价是否还在提高！只要还在提高就不要怕！」
        · 未脱离成本区       「三个交易日还没**脱离成本区**，又没打止损，多等一天」
                            阈值 ``cost_zone_pct``%（默认 3，取自深V玩法「脱离成本线3%以上」）

    任一维度显示还在涨就留着。与无条件的 ``time_stop_bars`` 不同，它只砍真正「不涨」的单子。

    ⚠️ 第一版只看「未脱离成本区 3%」一条，实测胜率 38.5%（全场最高）但均盈 9.76%
    （全场最低）——典型「砍掉慢热单」：一个已站上 BBI、重心上行、只是涨幅还没到 3%
    的票会被误杀，而这类票里有后来的大赢家。材料的「低等马不涨就拍」本来就是
    **人工综合判断**，不是单纯计时或单看涨幅。

    scale_out_frac>0 启用**分批止盈**（B1 §六 第五层 / B1.pdf「止盈 BBI 之上两根中阳线，
    放飞一半」）：持仓期内首次出现"BBI 上方连续两根中大阳线"时按该比例减仓。

    ``trail_pct``>0 启用**移动止损**：止损位跟随持仓期最高价，回撤该比例即出场。
    ⚠️ 移动止损与保本止损都只用**截至 j-1** 的最高价更新止损位——日线数据无法知道
    盘中顺序，若用当日 high 更新再用当日价判触发，等于假设"先冲高后回落"。
    """
    close = df["close"].astype(float).values
    low = df["low"].astype(float).values
    high = df["high"].astype(float).values
    open_ = df["open"].astype(float).values
    n = len(close)
    entry = float(close[entry_idx])
    if stop_override is not None and stop_override < entry:   # 显式止损位(如平台高×0.98)
        stop = float(stop_override)
    else:
        stop = entry * (1 - stop_pct / 100.0) if stop_mode == "pct" else float(low[entry_idx])
        if stop_mode != "pct" and stop_tick_buffer > 0:
            stop -= stop_tick_buffer * _TICK          # 「或向下 3-5 个价位」
    risk_frac = (entry - stop) / entry if entry else 0.0
    bbi_v = bbi.values
    has_above = False
    consec_below = 0
    by_close = str(stop_trigger).lower() != "intraday"

    # 动态止损状态
    stop_cur = stop
    peak = entry                          # 截至 j-1 的最高价（不含当日，防未来函数）
    be_armed = False                      # 保本止损是否已触发
    trail_armed = False
    be_level = 0.0                        # 各机制给出的止损位（用于归因 reason）
    trail_level = 0.0
    peak_close = entry                    # 成本区判定用收盘价，不用盘中高点

    # 分批止盈准备
    scale = max(0.0, min(1.0, float(scale_out_frac)))
    bulls = bull_flags if bull_flags is not None else (
        _medium_large_bull_flags(df, code) if scale > 0 else None)
    scaled_at: Optional[int] = None
    scaled_ret = 0.0

    def _settle(exit_idx: int, reason: str, price: float) -> dict[str, Any]:
        """按（已减仓部分 + 剩余部分）加权结算。"""
        rest_ret = price / entry - 1
        if scaled_at is None:
            total = rest_ret
        else:
            total = scale * scaled_ret + (1 - scale) * rest_ret
            reason = f"{reason}+scaled"
        out = {"exit_idx": exit_idx, "reason": reason, "ret": total,
               "holding": exit_idx - entry_idx, "risk_frac": risk_frac}
        if scaled_at is not None:
            out.update(scale_out_frac=scale, scale_out_idx=scaled_at,
                       scale_out_ret=round(scaled_ret, 4),
                       rest_ret=round(rest_ret, 4))
        if be_armed:
            out["breakeven_armed"] = True
        if trail_armed:
            out["trail_armed"] = True
        return out

    def _exit(j: int, reason: str, price: float) -> dict[str, Any]:
        """Settle an exit at bar j, deferring to the next sellable bar."""
        if can_sell is not None and not can_sell[j]:
            k = _next_tradable(can_sell, j, max_exit_delay)
            if k is None:                     # 一直卖不掉:持有到数据末
                return _settle(n - 1, reason + "_unfillable", float(close[-1]))
            return _settle(k, reason + "_delayed", float(close[k]))
        return _settle(j, reason, price)

    for j in range(entry_idx + 1, n):
        # ⓪ 用截至 j-1 的 peak 更新止损位（只上移，不下移）
        if breakeven_trigger > 0 and entry and peak / entry - 1 >= breakeven_trigger:
            be_level = entry
            if entry > stop_cur:
                stop_cur = entry
                be_armed = True
        if trail_pct > 0:
            trail_level = peak * (1 - trail_pct)
            if trail_level > stop_cur:
                stop_cur = trail_level
                trail_armed = True

        # ① 破止损。**两套触发口径**：
        #    · 保本止损盘中判定（材料：「马上回到成本价，拍掉！(盘中关注)」）
        #    · 其余按 stop_trigger，默认收盘（材料：「看收盘价」「收盘时」「忽略盘中冲高回落」）
        # 保本必须**先判且独立判**：它盘中就成交了，之后当日跌到哪里都与它无关。
        # 若等到收盘再一起判，会把「盘中触及成本价出场」错记成「收盘破位出场」，
        # 成交价从成本价滑到收盘价（实测差 1.2pp）。
        if be_armed and be_level >= stop_cur and low[j] <= be_level:
            fill = float(open_[j]) if open_[j] < be_level else float(be_level)
            return _exit(j, "breakeven_stop", fill)
        # 移动止损位若已高于保本位，则由它接管（按 stop_trigger 口径；材料没有这条机制，
        # 沿用常规止损的保守口径）
        ref = close[j] if by_close else low[j]
        if ref <= stop_cur:
            if by_close:
                # 收盘破位 ⇒ 收盘价成交（材料的执行方式就是「收盘时拍掉」）
                fill = float(close[j])
            else:
                fill = float(open_[j]) if open_[j] < stop_cur else stop_cur
            # 按**实际决定当前止损位**的机制归因，而非按启用顺序
            if trail_armed and trail_level >= max(be_level, stop) and trail_level >= stop_cur:
                reason = "trail_stop"
            elif be_armed and be_level >= stop_cur:
                reason = "breakeven_stop"
            else:
                reason = "stop"
            return _exit(j, reason, fill)
        b = bbi_v[j]
        if b == b:                                            # ② 收盘 BBI 退出（b==b 排除 NaN）
            # ②a 分批止盈:BBI 上方连续两根中大阳线（首次触发，且此前未减仓）
            if (scale > 0 and scaled_at is None and bulls is not None and j >= 1
                    and bulls[j] and bulls[j - 1]
                    and close[j] >= b and bbi_v[j - 1] == bbi_v[j - 1]
                    and close[j - 1] >= bbi_v[j - 1]
                    and (can_sell is None or can_sell[j])):
                scaled_at = j
                scaled_ret = float(close[j]) / entry - 1
            if close[j] > b:
                has_above = True; consec_below = 0
            elif close[j] < b:
                if has_above:
                    consec_below += 1
                    if consec_below >= bbi_exit_consec:
                        return _exit(j, "bbi_exit", float(close[j]))
            else:
                consec_below = 0
        # ③ 「不涨就拍」（材料持股手册的四种马 + 仓位实例）。**三个维度都平淡才砍**：
        #      · 未站上 BBI      —— 「不温不火，**没上BBI**，又没到止损。收盘前全拍！」
        #      · 收盘价重心未上升 —— 「一等马（**收盘价重心上升**为主）⇒ 拿住不动」
        #                            「看每天的收盘价是否还在提高！只要还在提高就不要怕！」
        #      · 未脱离成本区     —— 「三个交易日还没**脱离成本区**，又没打止损，多等一天」
        #    任一维度显示还在涨就留着。第一版只看「未脱离成本区 3%」这一条，
        #    实测胜率 38.5%（全场最高）但均盈 9.76%（全场最低）——典型「砍掉慢热单」，
        #    因为一个已站上 BBI、重心上行、只是涨幅还没到 3% 的票会被误杀。
        if (cost_zone_bars and entry
                and (j - entry_idx) >= cost_zone_bars + max(0, cost_zone_grace)):
            bj = bbi_v[j]
            # 严格 `>`：贴着 BBI 横盘正是材料说的「不温不火，没上BBI」，
            # 用 `>=` 会把横盘误判成「站上」而永不触发（实测场景①因此没被拍掉）。
            # 也与下方 BBI 退出逻辑一致——那里对「相等」同样按中性处理
            # （既不算站上也不算跌破）。
            above_bbi = bool(bj == bj and close[j] > bj)
            rising = _center_rising(close[entry_idx + 1:j + 1])
            escaped = (peak_close / entry - 1) >= cost_zone_pct / 100.0
            if not (above_bbi or rising or escaped):
                return _exit(j, "cost_zone_stop", float(close[j]))
        if time_stop_bars and (j - entry_idx) >= time_stop_bars:   # ④ 无条件时间止损
            return _exit(j, "time_stop", float(close[j]))
        peak = max(peak, float(high[j]))          # 当日收盘后才把当日高点纳入 peak
        peak_close = max(peak_close, float(close[j]))
    return _settle(n - 1, "open_end", float(close[-1]))


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
                    stop_mode: str = "low", stop_pct: float = 8.0,
                    feature_panel: bool = False,
                    sector_gate: Optional[Callable[[str, str], bool]] = None,
                    tradability: bool = True,
                    max_exit_delay: int = 5,
                    scale_out_frac: float = 0.0,
                    breakeven_trigger: float = 0.0,
                    trail_pct: float = 0.0,
                    stop_trigger: str = "close",
                    stop_tick_buffer: int = 0,
                    cost_zone_bars: int = 0,
                    cost_zone_pct: float = 3.0) -> list[dict[str, Any]]:
    """在 scorer 判「可买」的 as-of 日进场，按 B1 规则(止损+BBI)模拟到出场；非重叠(平仓后再找)。

    cost_bps：单边成本合计的往返基点(A股约20~30bps含佣金/印花税/滑点)，从每笔收益中扣除，看净期望。
    amv_regime：date→regime 映射(如 load_amv_regime)。提供时只在「做多」区间进场(as-of最近≤进场日的regime)。
    bbi_exit_consec/time_stop_bars：出场规则参数(可扫描)。每笔记录 r_multiple=净收益/风险敞口，供风险定额仓位。
    collect_all=True：不做单股非重叠去重，返回**每个**可买as-of日的候选(含 score)，供组合级 top-N 横截面择优。
    entry_gate(df_slice)->bool：进场硬门槛(如 j_low_gate=当日 J<13，B1 核心买点)；不满足则不进场。
    scale_out_frac>0：启用**分批止盈**（B1 §六 第五层「BBI 上方两根中大阳线分批减仓」/
      B1.pdf「止盈 BBI 之上两根中阳线，放飞一半」→ 取 0.5）。此前回测完全没有这一层，
      盈利单必须等 BBI 跌破才离场（已回撤过），系统性低估 avg_win 与盈亏比。
    tradability=True(默认)：施加**可成交性护栏**——涨停/停牌日不得按收盘价买入，跌停/停牌日
      不得卖出(顺延至 max_exit_delay 根内的下一个可卖日)。关掉可复现旧口径,但那会系统性
      高估收益:一字板照买、跌停照卖、停牌日照止损(审计 E5)。
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
        # 可成交性:涨停/停牌不可买,跌停/停牌不可卖(逐股算一次,循环内复用)
        buy_ok, sell_ok = tradable_flags(df, code) if tradability else (None, None)
        # 中大阳线标记(分批止盈用):逐股算一次,避免每个信号重算
        bull_flags = _medium_large_bull_flags(df, code) if scale_out_frac > 0 else None
        emitted = 0
        i = min_bars
        while i < n - 1:
            entry_date = str(df["date"].iloc[i])[:10]
            slice_df = df.iloc[:i + 1]
            if entry_gate is not None and not entry_gate(slice_df):
                i += max(1, step)
                continue
            if sector_gate is not None and not sector_gate(code, entry_date):   # 板块相位择时
                i += max(1, step)
                continue
            res = scorer(slice_df, code)
            if (res is not None and res.get("suggestion") == "可买" and _amv_ok(entry_date)
                    and (buy_ok is None or buy_ok[i])):     # 涨停/停牌当日买不到
                stop_ov = None
                if stop_mode == "platform":                  # 平台高止损:形态自带止损位
                    try:
                        from platform_pullback import detect_platform_pullback  # noqa: PLC0415
                        det = detect_platform_pullback(slice_df)
                        if det:
                            stop_ov = det["platform_high"] * 0.98
                    except Exception:  # noqa: BLE001
                        stop_ov = None
                tr = simulate_b1_trade(df, i, bbi, bbi_exit_consec=bbi_exit_consec,
                                       time_stop_bars=time_stop_bars,
                                       stop_mode=stop_mode, stop_pct=stop_pct,
                                       stop_override=stop_ov,
                                       can_sell=sell_ok, max_exit_delay=max_exit_delay,
                                       scale_out_frac=scale_out_frac, code=code,
                                       bull_flags=bull_flags,
                                       breakeven_trigger=breakeven_trigger,
                                       trail_pct=trail_pct,
                                       stop_trigger=stop_trigger,
                                       stop_tick_buffer=stop_tick_buffer,
                                       cost_zone_bars=cost_zone_bars,
                                       cost_zone_pct=cost_zone_pct)
                ret_net = tr["ret"] - cost
                rf = tr.get("risk_frac") or 0.0
                rf_eff = max(rf, _R_RISK_FLOOR)   # 地板：周线收盘贴低时 risk_frac≈0 会把 R 炸成极端值
                rec = {"code": code, "entry_date": entry_date,
                       "exit_date": str(df["date"].iloc[tr["exit_idx"]])[:10],
                       "score": res.get("score"), "ret": round(ret_net, 4),
                       "risk_frac": round(rf, 4),
                       "r_multiple": round(ret_net / rf_eff, 3) if rf > 0 else None,
                       "holding": tr["holding"], "reason": tr["reason"]}
                if tr.get("scale_out_idx") is not None:
                    rec["scale_out_ret"] = tr.get("scale_out_ret")
                    rec["rest_ret"] = tr.get("rest_ret")
                    rec["scale_out_bars"] = tr["scale_out_idx"] - i
                if feature_panel:
                    rec["features"] = _feature_panel(slice_df)
                trades.append(rec)
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
         "median_return": round(statistics.median(rets), 4),
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
    lines = [f"交易 {d['n']} 笔  胜率 {d['win_rate']*100:.1f}%  期望(均) {d['expectancy']*100:+.2f}%/笔  "
             f"中位 {d['median_return']*100:+.2f}%  盈亏比 {d['payoff_ratio']}  均持 {d['avg_holding']} 根",
             f"  均盈 {d['avg_win']*100:+.2f}%  均亏 -{d['avg_loss']*100:.2f}%  "
             f"(均≫中位 → 少数肥尾大赢主导,警惕幸存者偏差)"]
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


def _amv_ledger_records(since: str, after_date: Optional[str],
                        ledger_path: Optional[Path] = None) -> list[dict[str, Any]]:
    """从人工上报台账(0amv_observations.jsonl)取 after_date 之后的 confirmed 记录，
    补齐指南针 day.vdat 主序列的尾部缺口(用户每日收盘上报的 0AMV 只进台账,vdat 由客户端维护)。
    同一日期多条记录时后写入(recorded_at 晚)的覆盖先写的。best-effort：缺失/异常返回 []。"""
    try:
        if ledger_path is None:
            from paths import BASE  # noqa: PLC0415
            ledger_path = BASE / "01_data" / "market" / "0amv_observations.jsonl"
        if not ledger_path.is_file():
            return []
        latest: dict[str, tuple[str, float]] = {}
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            d = str(r.get("date") or "")
            pct = r.get("amv_change_pct")
            if (r.get("quality") == "confirmed" and pct is not None and len(d) == 10
                    and d >= since and (after_date is None or d > after_date)):
                if d not in latest or str(r.get("recorded_at", "")) >= latest[d][0]:
                    latest[d] = (str(r.get("recorded_at", "")), float(pct))
        return [{"date": d, "change_pct": pct} for d, (_, pct) in sorted(latest.items())]
    except Exception:  # noqa: BLE001
        return []


def load_amv_regime(since: str = "2015-01-01", root: Optional[str] = None) -> dict[str, str]:
    """从指南针 0AMV 日线(compass_amv)构建历史 date→regime。best-effort：数据缺失返回 {}。
    day.vdat 主序列之后,拼接人工上报台账(0amv_observations.jsonl)的 confirmed 记录补齐尾部。"""
    try:
        import compass_amv  # noqa: PLC0415
        parsed = compass_amv.parse_amv_daily(since=since, root=root)
        if parsed.get("error") or not parsed.get("records"):
            return {}
        records = list(parsed["records"])
        last_date = str(records[-1].get("date"))[:10]
        records += _amv_ledger_records(since, last_date)
        return _amv_regime_from_records(records)
    except Exception:  # noqa: BLE001
        return {}


def simulate_portfolio(trades: list[dict[str, Any]], risk_pct: float = 0.01,
                       max_concurrent: int = 5, max_pos_frac: float = 0.20,
                       max_gross: float = 1.0) -> dict[str, Any]:
    """组合级资金曲线：把逐笔交易按**固定风险仓位**(每笔风险 risk_pct 的本金)+ 并发持仓上限
    + 单仓/总敞口上限,事件驱动地放到一条资金曲线上,输出总收益/CAGR/最大回撤/成交与被限笔数。

    仓位：alloc_frac = min(risk_pct/max(risk_frac, _R_RISK_FLOOR), max_pos_frac)（止损打掉≈risk_pct 本金；
    risk_frac 与 R 倍数同口径设 2% 地板——周线收盘贴低时 risk_frac≈0，不设地板仓位会失真顶到上限）。
    约束：同时持仓数 ≤ max_concurrent；名义总敞口 ≤ max_gross×当前权益（满则跳过后续信号）。
    回撤：按平仓时点的已实现权益序列计（**不含持仓中浮亏 → 真实回撤更大，本值为乐观下界**）。绝不 raise。
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
        alloc_frac = min(risk_pct / max(t["risk_frac"], _R_RISK_FLOOR), max_pos_frac)
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
        f"收益/回撤 {ret_dd}  (期约 {out['years']} 年; 回撤=已实现权益口径,不含浮亏,真实更大)")
    return out


def simulate_portfolio_topn(candidates: list[dict[str, Any]], top_n: int = 5,
                            risk_pct: float = 0.01, max_concurrent: int = 5,
                            max_pos_frac: float = 0.20, max_gross: float = 1.0) -> dict[str, Any]:
    """组合级**横截面 top-N 择优**资金曲线：每个进场日在所有「可买」候选里按 score 降序取前 top_n
    (排除已持有该股、受并发/敞口上限约束)，固定风险仓位入场，事件驱动出资金曲线/CAGR/最大回撤。

    candidates：evaluate_trades(collect_all=True) 的全候选(含 entry_date/exit_date/ret/risk_frac/score)。
    top_n：每个进场日最多新开仓数(横截面择优的宽度)。
    仓位同 simulate_portfolio(risk_frac 设 2% 地板);回撤=已实现权益口径(不含浮亏,真实回撤更大)。绝不 raise。
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
    taken_rets: list[float] = []

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
            alloc_cap = min(risk_pct / max(t["risk_frac"], _R_RISK_FLOOR), max_pos_frac) * equity
            if (gross + alloc_cap) > max_gross * equity:
                skipped += 1
                continue
            heapq.heappush(open_heap, (t["exit_date"], seq, alloc_cap, t["ret"], t["code"]))
            seq += 1
            gross += alloc_cap
            held.add(t["code"])
            taken += 1
            taken_rets.append(t["ret"])
            opened += 1
    _close_until("9999-99-99")

    # 被选中(实际持有)子集的统计 —— top-N 模式下真正代表策略,而非全候选池
    sel_n = len(taken_rets)
    sel_win = round(sum(1 for r in taken_rets if r > 0) / sel_n, 4) if sel_n else None
    sel_exp = round(sum(taken_rets) / sel_n, 4) if sel_n else None

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
           "selected_win_rate": sel_win, "selected_expectancy": sel_exp,
           "risk_pct": risk_pct, "max_concurrent": max_concurrent}
    out["text"] = (
        f"组合 top-{top_n} 横截面择优(风险{risk_pct*100:.1f}%/笔, 并发≤{max_concurrent}): "
        f"成交 {taken}/限跳 {skipped}  总收益 {out['total_return']*100:+.1f}%  "
        f"CAGR {out['cagr']*100:.1f}%  最大回撤 {out['max_drawdown']*100:.1f}%  收益/回撤 {ret_dd}  "
        f"(期约 {out['years']} 年)\n  [被选中子集] 胜率 {(sel_win or 0)*100:.1f}%  "
        f"期望 {(sel_exp or 0)*100:+.2f}%/笔 (n={sel_n}) —— top-N 模式看这个,非全候选池")
    return out


def _feature_panel(df: pd.DataFrame) -> dict[str, Any]:
    """进场时的特征面板(用现有选择器 + KDJ/均线),供归因分析。绝不 raise。"""
    feats: dict[str, Any] = {}
    for name in ("reversal_quality", "alpha101", "alpha_pvcorr", "low_vol", "momentum"):
        try:
            r = SCORERS[name](df, "")
            feats[name] = r["score"] if r else None
        except Exception:  # noqa: BLE001
            feats[name] = None
    try:
        if _kdj is not None:
            k = _kdj(df)
            feats["j"] = k.get("j") if k.get("available") else None
        c = df["close"].astype(float).values
        if len(c) >= 60:
            feats["dist_ma10"] = (c[-1] / c[-10:].mean() - 1) * 100
            feats["prior_gain60"] = (c[-1] / c[-60:].min() - 1) * 100
    except Exception:  # noqa: BLE001
        pass
    return feats


def attribution_report(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """按日期切 train/test 两半，对每个特征按 4 分位算 (Q4均收 − Q1均收) 的前向 lift。
    只有 train、test **同号且够大** 的特征才算稳健判别子(否则是过拟合/幸存者假象)。"""
    ts = [t for t in trades if t.get("features") and t.get("ret") is not None]
    if len(ts) < 80:
        return {"n": len(ts), "text": f"归因样本不足({len(ts)}，需≥80)"}
    ts.sort(key=lambda t: t["entry_date"])
    mid = len(ts) // 2
    train, test = ts[:mid], ts[mid:]
    feats = sorted({k for t in ts for k in t["features"]})

    def _lift(grp, f):
        vals = sorted(((t["features"].get(f), t["ret"]) for t in grp
                       if isinstance(t["features"].get(f), (int, float))), key=lambda x: x[0])
        if len(vals) < 20:
            return None
        q = len(vals) // 4
        return (statistics.mean(r for _, r in vals[-q:])
                - statistics.mean(r for _, r in vals[:q]))

    rows = []
    lines = [f"特征归因（train {len(train)} / test {len(test)} 笔；每半按特征4分位，报 Q4均收−Q1均收）：",
             "  →只有 train/test **同号且|lift|>0.5%** 才算稳健判别子(否则过拟合/幸存者假象)"]
    for f in feats:
        lt, lv = _lift(train, f), _lift(test, f)
        robust = bool(lt is not None and lv is not None and (lt > 0) == (lv > 0)
                      and min(abs(lt), abs(lv)) > 0.005)
        rows.append({"feature": f, "train_lift": lt, "test_lift": lv, "robust": robust})
        fmt = lambda x: f"{x*100:+.2f}%" if x is not None else "NA"
        lines.append(f"  {f:<16} train {fmt(lt):>8}  test {fmt(lv):>8}  {'✓稳健判别' if robust else ''}")
    robust_feats = [r["feature"] for r in rows if r["robust"]]
    lines.append(f"结论：稳健判别子 = {robust_feats or '无(→无可泛化的赢家规律,选股不加值,与前述一致)'}")
    return {"n": len(ts), "features": rows, "robust_features": robust_feats, "text": "\n".join(lines)}


_RSS_FAIL: str = ""             # 峰值内存探测失败原因（供 [MEM] 行诊断，只记最后一次）


def peak_rss_mb() -> Optional[float]:
    """本进程峰值 RSS（MB）。取不到返回 None，失败原因写进 `_RSS_FAIL`。

    OOM Kill 是这套回测的老问题（见 00_governance/research/R17_infra_tooling.md「全市场 OOM」），
    但一直没有**每轮实测数字**，只能靠猜。有了它才能判断 `--jobs N` 并行安全到几路。

    ⚠️ 第一版在 Windows 上静默返回 None（owner 实测打出「峰值 未知」）。原因是只试了
    `ctypes.windll.psapi`，而且没设 restype/argtypes——句柄按默认 c_int 传给
    `GetProcessMemoryInfo` 时可能被截断。这里改成多路兜底并**记下失败原因**：
    静默返回 None 的诊断价值为零，这正是本仓库反复踩的「静默降级」坑。
    """
    global _RSS_FAIL
    errs = []
    try:
        import resource  # noqa: PLC0415  Unix
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux 是 KB，macOS 是字节
        return v / 1024.0 if sys.platform != "darwin" else v / (1024.0 ** 2)
    except Exception as e:                                        # noqa: BLE001
        errs.append(f"resource: {e}")
    try:                                                          # Windows
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)      # type: ignore[attr-defined]
        k32.GetCurrentProcess.restype = wintypes.HANDLE           # 不设会按 c_int 截断句柄
        h = k32.GetCurrentProcess()
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        # Win7+ 把 psapi 的实现搬进了 kernel32（K32 前缀），psapi.dll 仍是转发壳；
        # 两个都试，哪个在就用哪个。
        for dll_name, fn_name in (("kernel32", "K32GetProcessMemoryInfo"),
                                  ("psapi", "GetProcessMemoryInfo")):
            try:
                dll = k32 if dll_name == "kernel32" else \
                    ctypes.WinDLL(dll_name, use_last_error=True)  # type: ignore[attr-defined]
                fn = getattr(dll, fn_name)
                fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
                fn.restype = wintypes.BOOL
                if fn(h, ctypes.byref(pmc), pmc.cb):
                    return pmc.PeakWorkingSetSize / (1024.0 ** 2)
                errs.append(f"{dll_name}.{fn_name}: "
                            f"err={ctypes.get_last_error()}")
            except Exception as e:                                # noqa: BLE001
                errs.append(f"{dll_name}.{fn_name}: {e}")
    except Exception as e:                                        # noqa: BLE001
        errs.append(f"ctypes: {e}")
    _RSS_FAIL = "; ".join(errs)[:200]
    return None


def write_json_stream(path: Path, payload: dict[str, Any], *, big: bool = False) -> None:
    """落盘 JSON（**流式**）。``big=True`` 时不缩进。

    ⚠️ 2026-08-07 从 `write_json` 改名：`paths.write_json` 是全项目的 JSON 产物写入口，
    两者**同名不同行为**，混用会出事 ——

        paths.write_json         allow_nan=False ⇒ NaN 当场崩（要的就是显式失败）
        write_json_stream(此处)  允许 NaN        ⇒ 研究指标里 NaN 是合法读数
                                                 （零方差的 Sharpe、无交易的胜率）

    所以这份**不能**换成 `paths.write_json`，也不该叫一样的名字。

    ⚠️ `path.write_text(json.dumps(...))` 会先在内存里拼出**整个字符串**再写，
    等于把 payload 复制一份到内存；`indent=2` 又让这份字符串再大 1.36 倍（实测）。
    逐笔上万条时这是白送的一次内存峰值——而这个回测本来就常被 OOM Kill。
    `json.dump(fh)` 分块写出，峰值与 payload 大小无关。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=None if big else 2)


def trades_signature(args: Any, codes: list[str]) -> dict[str, Any]:
    """影响**逐笔交易集**的全部参数指纹。

    组合层参数（`risk_pct` / `max_concurrent` / `max_pos` / `top_n` 的取值）**不在内**
    ——它们只改资金曲线，不改 trades。但 `collect_all` 在内：`--top-n > 0` 时
    `evaluate_trades` 返回**未去重的全候选**（backtest_factors 内 collect_all=True），
    与去重后的逐笔完全不同口径。

    落进结果 JSON 有两个用途：
      ① `--from-trades` 复用前核对口径（不一致就拒绝，不静默算）
      ② 事后能查清一个结果文件到底是什么参数跑出来的——原先 payload **没有记录**
         `scale_out/breakeven/trail/stop_trigger/stop_tick_buffer/cost_zone_*`，
         而这些恰好就是 M2 扫描在扫的参数，等于结果文件不自述身份。
    """
    import hashlib  # noqa: PLC0415
    digest = hashlib.sha1(",".join(codes).encode("utf-8")).hexdigest()[:12]
    return {
        "data_source": args.data_source, "scorer": args.scorer,
        "weekly": bool(args.weekly), "step": args.step, "cost_bps": args.cost_bps,
        "entry_filter": args.entry_filter, "amv_long_only": bool(args.amv_long_only),
        "bbi_consec": args.bbi_consec, "time_stop": args.time_stop,
        "stop_mode": args.stop_mode, "stop_pct": args.stop_pct,
        "stop_trigger": args.stop_trigger, "stop_tick_buffer": args.stop_tick_buffer,
        "scale_out": args.scale_out, "breakeven": args.breakeven, "trail": args.trail,
        "cost_zone_bars": args.cost_zone_bars, "cost_zone_pct": args.cost_zone_pct,
        "max_signals_per_code": args.max_signals_per_code,
        "sector_filter": bool(args.sector_filter),
        "start": args.start or None, "end": args.end or None, "count": args.count,
        "collect_all": bool(args.top_n > 0),
        "n_codes": len(codes), "codes_digest": digest,
    }


def _portfolio_from_trades(args: Any, codes: list[str]) -> int:
    """只跑资金曲线，逐笔交易从已有结果 JSON 读入（跳过回测）。

    为什么值得：M2 扫描 C 组 8 个方案的**回测参数完全相同**，只有资金曲线参数不同
    （max_concurrent / max_pos / risk_pct / top_n）。资金曲线模拟是**毫秒级**，
    回测是**分钟级** ⇒ 8 次回测里有 5 次纯属重算，约占整轮扫描 20% 的时间。

    ⚠️ 复用前**必须**核对 `trades_signature` 完全一致。拿另一套止损参数的 trades
    去跑组合，出来的曲线看不出任何异常——「静默用错口径」这类错误最难发现，
    所以这里**不一致就非零退出**，不做任何猜测性兼容。
    """
    src = Path(args.from_trades)
    if not src.is_file():
        print(f"[FAIL] --from-trades 文件不存在: {src}", file=sys.stderr)
        return 2
    try:
        d = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:                                        # noqa: BLE001
        print(f"[FAIL] --from-trades 读不了 {src.name}: {e}", file=sys.stderr)
        return 2
    trades = d.get("trades")
    if not trades:
        print(f"[FAIL] {src.name} 里没有 trades（--summary-only 落的盘不能复用）",
              file=sys.stderr)
        return 2
    want = trades_signature(args, codes)
    got = d.get("trades_signature")
    if not isinstance(got, dict):
        print(f"[FAIL] {src.name} 没有 trades_signature（旧版结果文件）⇒ "
              f"无法确认口径一致，拒绝复用", file=sys.stderr)
        return 2
    diff = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
    if diff:
        print(f"[FAIL] trades 口径与 {src.name} 不一致，拒绝复用：", file=sys.stderr)
        for k, (a, b) in sorted(diff.items()):
            print(f"       {k}: 源={a!r} 本次={b!r}", file=sys.stderr)
        return 2
    if args.top_n <= 0 and not args.portfolio:
        print("[FAIL] --from-trades 只用于组合模拟，需同时给 --portfolio 或 --top-n",
              file=sys.stderr)
        return 2

    tsum = summarize_trades(trades)
    payload = {"mode": "trade_sim", "scorer": args.scorer, "weekly": args.weekly,
               "data_source": args.data_source, "start": args.start or None,
               "end": args.end or None, "cost_bps": args.cost_bps,
               "amv_long_only": bool(args.amv_long_only),
               "entry_filter": args.entry_filter, "top_n": args.top_n,
               "bbi_consec": args.bbi_consec, "time_stop": args.time_stop,
               "stop_mode": args.stop_mode, "stop_pct": args.stop_pct,
               "codes": codes, "count": args.count,
               "trades_signature": want, "trades_reused_from": src.name,
               "n_trades": len(trades), "trade_summary": tsum}
    if args.top_n > 0:
        payload["portfolio"] = simulate_portfolio_topn(
            trades, top_n=args.top_n, risk_pct=args.risk_pct / 100.0,
            max_concurrent=args.max_concurrent, max_pos_frac=args.max_pos / 100.0)
    else:
        payload["portfolio"] = simulate_portfolio(
            trades, risk_pct=args.risk_pct / 100.0,
            max_concurrent=args.max_concurrent, max_pos_frac=args.max_pos / 100.0)
    _rss = peak_rss_mb()
    print(f"[OK] 复用 {src.name} 的 {len(trades)} 笔交易（口径已核对一致），只跑资金曲线"
          f"  [MEM] 峰值 {f'{_rss:.0f}MB' if _rss else '未知'}")
    del d, trades                       # 组合曲线已算完，尽早还内存
    if args.out:
        # ⚠️ **不重写 trades**：它与源文件逐字相同，`trades_reused_from` 已指明来源。
        # 复用路径本来就要把源文件整份读进内存，再写一遍等于同一份数据占三份
        # （源 dict + payload 引用 + 落盘缓冲）——OOM 就是这么攒出来的。
        write_json_stream(Path(args.out), payload)
        print(f"[OK] 写出 {args.out}（逐笔见 {src.name}，不重复落盘）")
    print("\n" + payload["portfolio"]["text"])
    return 0


def _load_bars_local(codes: list[str], count: int, start: Optional[str] = None,
                     end: Optional[str] = None) -> dict[str, pd.DataFrame]:
    """CLI 用：经 local_tdx 读取本地日线（需通达信数据；单测走注入不经此）。
    start/end(YYYY-MM-DD)在 count 之前应用(此前 tdx 路径静默忽略 --start/--end,
    导致指定窗口无效、实际跑全历史)。"""
    import local_tdx_data  # noqa: PLC0415
    local_tdx_data.reset_qfq_failure_stats()    # 计数限定本轮加载，见下方汇总
    out: dict[str, pd.DataFrame] = {}
    for c in codes:
        try:
            df = local_tdx_data.get_ohlcv_table(c, count=count or 2000)
            if df is not None and len(df) and (start or end):
                df = df.copy()
                df["date"] = df["date"].astype(str).str[:10]
                if start:
                    df = df[df["date"] >= start]
                if end:
                    df = df[df["date"] <= end]
                df = df.tail(count).reset_index(drop=True) if count else df.reset_index(drop=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 加载 {c} 失败: {exc}", file=sys.stderr)
            df = None
        if df is not None and len(df):
            out[c] = df
    # 前复权失败汇总（DATA_SOURCE_PRINCIPLE ③ / 原 TODO #16）：逐票 WARN 在全宇宙
    # 日志里会被淹没，加载完整轮后必须有一行总数。
    # ⚠️ 必须用与写入方相同的**扁平**导入路径（本函数顶部 `import local_tdx_data`）：
    # 包路径 `local_tdx.local_tdx_data` 是另一个模块对象，计数恒为 0。
    stats = local_tdx_data.qfq_failure_stats()
    if stats["count"]:
        shown = " ".join(stats["codes"][:10])
        more = f" 等{stats['count']}只" if stats["count"] > 10 else ""
        print(f"[WARN] 前复权失败 {stats['count']}/{len(codes)} 只"
              f"（{stats['count'] / max(len(codes), 1) * 100:.1f}%），按未复权使用: {shown}{more}",
              file=sys.stderr)
    return out


def _report_gates() -> None:
    """入场门槛统计打到 stderr;依赖缺失/异常时额外顶一条告警(否则 0 命中会被读成'无判别力')。"""
    gr = gate_stats_report()
    if not gr["text"]:
        return
    print("[INFO] 入场门槛统计:\n" + gr["text"], file=sys.stderr)
    if gr["broken"]:
        print("[WARN] 入场门槛存在依赖缺失/异常,本轮结果**不可用于判定因子有效性**",
              file=sys.stderr)


def _empty_result_guard(n_loaded: int, n_out: int, unit: str, allow_empty: bool) -> int:
    """空结果护栏:一根 K 线都没加载 / 一条结果都没产出 → **非零退出且不落盘**。

    回测里"全空"与"因子无效"长得一模一样:静默 exit 0 会让"没数据"被当成"没效果"写进
    结论,空产物还会被下游续跑校验当成"已完成"永久复用(审计 E9)。确需空结果时显式
    --allow-empty(降级为 WARN 并照常落盘)。"""
    msg = None
    if n_loaded <= 0:
        msg = "未加载到任何 K 线(数据源/代码列表/日期区间有问题?)"
    elif n_out <= 0:
        msg = f"加载了 {n_loaded} 只票却产出 0 {unit}(门槛过严/依赖失败?)"
    if msg is None:
        return 0
    if allow_empty:
        print(f"[WARN] {msg};--allow-empty 已开,按空结果继续", file=sys.stderr)
        return 0
    print(f"[ERR] {msg};拒绝落盘——空结果会被误读成'该因子无判别力'。"
          "确需空结果请显式加 --allow-empty", file=sys.stderr)
    return 2


def main(argv: Optional[list] = None, loader: Optional[Callable[[list[str], int], dict]] = None) -> int:
    ap = argparse.ArgumentParser(description="S_shape 因子走查回测校准（纯分析，只读本地日线）")
    ap.add_argument("--codes", default="", help="逗号分隔的 6 位代码（与 --universe-sample 二选一）")
    ap.add_argument("--codes-file", default="",
                    help="从文件读代码(每行一个,# 开头为注释)。用于**钉死宇宙**:"
                         "vipdoc 目录会随下载变动(实测 5535→5536),"
                         "sample_codes(seed=0) 就会抽到另一组票 ⇒ 长时间扫描里各方案宇宙不同")
    ap.add_argument("--dump-codes", default="",
                    help="只解析 universe 并把代码写到该文件后**立即退出**(不跑回测)。"
                         "配合 --codes-file 让一轮扫描的所有方案共用同一份宇宙")
    ap.add_argument("--universe-sample", type=int, default=0,
                    help="从 universe 随机抽 N 只（代表性样本；0=不抽，用 --codes 或全量 universe）")
    ap.add_argument("--universe-local", action="store_true",
                    help="universe 用本地 vipdoc 实有文件（推荐：覆盖率~100%%、不依赖在线代码表；否则用在线 get_stock_list）")
    ap.add_argument("--data-source", choices=["tdx", "qlib", "csv"], default="tdx",
                    help="行情数据源:tdx=本地通达信(默认,**已前复权** owner 2026-08-04 拍板;\n"
                         "首次须跑 local_tdx/adjust_factors.py --warmup 预热权息);"
                         "qlib/csv=E:\\S_DATA(含退市股,前复权,1999~2026-02)")
    ap.add_argument("--s-data-root", default=os.environ.get("S_DATA_ROOT") or r"E:\S_DATA",
                    help=r"s_data 根目录(含 Q_DATA/CSV_DATA);可用环境变量 S_DATA_ROOT 覆盖,默认 E:\S_DATA")
    ap.add_argument("--start", default="", help="回测起点 YYYY-MM-DD(在 --count 之前应用;配合 walk-forward)")
    ap.add_argument("--end", default="", help="回测终点 YYYY-MM-DD(默认不限)")
    ap.add_argument("--universe-sdata", action="store_true",
                    help="universe 用 s_data 全市场(含退市股;替代 --universe-local 的 tdx 文件列表)")
    ap.add_argument("--seed", type=int, default=0, help="随机抽样种子（可复现）")
    ap.add_argument("--count", type=int, default=500, help="每股回溯 K 线根数")
    ap.add_argument("--horizons", default="5,10,20", help="前向窗口(日)，逗号分隔")
    ap.add_argument("--step", type=int, default=1, help="as-of 采样步长")
    ap.add_argument("--gate-window", type=int, default=0,
                    help=f"传给 gate/scorer 的尾窗口根数（0=整段前缀，默认）。"
                         f"{GATE_WINDOW_SAFE} 是与整段前缀逐字段等价的保守预热值；"
                         "调得更小可能改变因子值（KDJ/MACD 递归、momentum 回看随长度自适应）。"
                         "实测本模块打分器的单次开销几乎与切片长度无关（见 evaluate docstring），"
                         "所以默认不开、保持已跑结果可复现")
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
                    help="仅在0AMV『做多』区间进场(读指南针compass_amv历史→状态机>4%%做多/<-2.3%%空头;配合 --trade-sim)")
    ap.add_argument("--bbi-consec", type=int, default=2,
                    help="出场:站上BBI后连续N日收盘跌破BBI才卖出(默认2;可扫描出场松紧)")
    ap.add_argument("--time-stop", type=int, default=0,
                    help="出场:持有N根仍未触发止损/BBI则到期平仓(默认0=不启用)")
    ap.add_argument("--portfolio", action="store_true",
                    help="在逐笔交易上叠加组合级资金曲线(固定风险仓位+并发上限),输出总收益/CAGR/最大回撤")
    ap.add_argument("--from-trades", default="",
                    help="从已有结果 JSON 读 trades、**跳过回测**只跑资金曲线(配合 --portfolio/--top-n)。"
                         "复用前核对 trades_signature,口径不一致直接非零退出")
    ap.add_argument("--risk-pct", type=float, default=1.0, help="组合:每笔风险占本金%%(默认1.0)")
    ap.add_argument("--max-concurrent", type=int, default=5, help="组合:同时持仓上限(默认5)")
    ap.add_argument("--max-pos", type=float, default=20.0, help="组合:单仓名义上限%%本金(默认20)")
    ap.add_argument("--top-n", type=int, default=0,
                    help="组合:每个进场日按score降序只取前N只(横截面择优;0=不启用,取全部可买)")
    ap.add_argument("--max-signals-per-code", type=int, default=0,
                    help="每只股最多保留N个候选(0=不限;--top-n 下限制单股候选爆炸、省内存/CPU)")
    ap.add_argument("--stop-mode", choices=["low", "pct", "platform"], default="low",
                    help="止损:low=买入K最低;pct=entry×(1-stop_pct%%);platform=平台高×0.98(配 platform_pullback 入场)")
    ap.add_argument("--stop-pct", type=float, default=8.0, help="--stop-mode pct 时的止损百分比(默认8)")
    ap.add_argument("--scale-out", type=float, default=0.0,
                    help="分批止盈比例:BBI 上方两根中大阳线时减仓比例(B1 原文「放飞一半」→ 0.5;"
                         "默认 0=不启用,便于与旧结果对照)")
    ap.add_argument("--breakeven", type=float, default=0.0,
                    help="盈亏平衡保护:浮盈达该比例后止损上移到成本价(如 0.05=浮盈5%%后保本;"
                         "01_swing_rules.md §六 已定义但回测此前未实现;默认 0=不启用)")
    ap.add_argument("--trail", type=float, default=0.0,
                    help="移动止损:止损跟随持仓期最高价,回撤该比例出场(如 0.08=回撤8%%;"
                         "默认 0=不启用)。只用截至前一根的最高价更新,避免未来函数")
    ap.add_argument("--stop-trigger", choices=["close", "intraday"], default="close",
                    help="止损触发口径(2026-08-04 按 B1_w.pdf 修正):close=收盘价跌破才算破位"
                         "(材料原文「看收盘价」「收盘时」「忽略盘中冲高回落」,**默认**);"
                         "intraday=盘中最低触及即出(旧行为,留作口径对照)。"
                         "保本止损不受此参数影响,始终盘中判定(材料:「盘中关注」)")
    ap.add_argument("--stop-tick-buffer", type=int, default=0,
                    help="止损位再向下留几个价位(1 价位=0.01 元)。材料「买入K线最低点"
                         "**或向下 3-5 个价位**」;贴着最低点容易被一笔扫掉。默认 0=旧行为")
    ap.add_argument("--cost-zone-bars", type=int, default=0,
                    help="成本区时间止损:进场后 N+1 根内收盘涨幅始终 <--cost-zone-pct 则平仓"
                         "(材料「三个交易日还没脱离成本区,又没打止损,多等一天」/"
                         "持股手册「低等马…不涨就拍」)。默认 0=不启用,建议 3")
    ap.add_argument("--cost-zone-pct", type=float, default=3.0,
                    help="脱离成本区的涨幅阈值%%(默认 3,取自材料深V玩法「脱离成本线3%%以上」)")
    ap.add_argument("--summary-only", action="store_true",
                    help="输出JSON不含逐笔trades(仅摘要;全市场日线省内存,防OOM)")
    ap.add_argument("--attribution", action="store_true",
                    help="记录每笔特征面板,按日期切train/test,报各特征前向lift——严谨检验'赢家共性'是否可泛化(防幸存者/过拟合)")
    ap.add_argument("--sector-filter", action="store_true",
                    help="板块相位择时:只在个股所属板块处于有利相位(DIF>0且无近期顶背离/三打)时进场")
    ap.add_argument("--sector-index-dir",
                    default=str(Path(__file__).resolve().parents[2] / "01_data" / "market" / "sector_index"),
                    help="板块指数CSV目录(fetch_sector_index_history.py 产出)")
    ap.add_argument("--sector-members",
                    default=str(Path(__file__).resolve().parents[2] / "01_data" / "market" / "sector_members.json"),
                    help="板块成员映射 JSON({sector:[codes]}; fetcher --members 产出)")
    ap.add_argument("--allow-empty", action="store_true",
                    help="允许空结果(0 K线/0 信号)仍 exit 0 并落盘;默认拒绝——空结果会被误读成'因子无效'")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    reset_gate_stats()

    if args.codes_file:
        # 钉死宇宙：优先级最高，跳过所有抽样逻辑。
        # ⚠️ 为什么需要它：universe 来自 vipdoc 目录列举，会随通达信下载变动
        # （实测一轮扫描中 5535→5536）；`sample_codes(seed=0)` 的 seed 固定没用，
        # **被抽的池子变了** ⇒ 抽到的是另一组 1000 只。长时间扫描里各方案宇宙不同，
        # 跨方案比较就混进了「换了一批票」这个噪声。
        p = Path(args.codes_file)
        if not p.is_file():
            ap.error(f"--codes-file 不存在: {p}")
        codes = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        print(f"[INFO] universe=codes_file({p.name}) {len(codes)} 只（**已钉死**）",
              file=sys.stderr)
    elif args.universe_sdata:
        import s_data  # noqa: PLC0415
        src_root = Path(args.s_data_root) / ("CSV_DATA" if args.data_source == "csv" else "Q_DATA")
        base = s_data.list_universe(src_root, source=args.data_source)
        codes = sample_codes(base, args.universe_sample, args.seed) if args.universe_sample > 0 else list(base)
        print(f"[INFO] universe=s_data({args.data_source}) 共 {len(base)} 只，取 {len(codes)} 只（seed={args.seed}）", file=sys.stderr)
    elif args.universe_local or args.universe_sample > 0:
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
        ap.error("需提供 --codes / --universe-sample N / --universe-local / --universe-sdata")
    if args.dump_codes:
        # 只解析宇宙就退出——让一轮扫描先落一份代码表，后续所有方案用 --codes-file 读它。
        # 放在这里(codes 已解析、任何数据加载之前)是为了快：只做一次目录列举。
        out = Path(args.dump_codes)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(codes) + "\n", encoding="utf-8")
        print(f"[OK] 宇宙已落盘 {out}（{len(codes)} 只）；"
              f"后续用 --codes-file 复用即可钉死宇宙")
        return 0
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    if loader is not None:
        load = loader
    elif args.data_source == "tdx":
        import functools
        load = functools.partial(_load_bars_local, start=args.start or None, end=args.end or None)
    else:
        import functools
        import s_data  # noqa: PLC0415
        fn = s_data.load_bars_csv if args.data_source == "csv" else s_data.load_bars_qlib
        sub = "CSV_DATA" if args.data_source == "csv" else "Q_DATA"
        load = functools.partial(fn, start=args.start or None, end=args.end or None,
                                 root=str(Path(args.s_data_root) / sub))

    if args.trade_sim:
        if args.from_trades:
            # 放在 amv_regime 加载**之前**：复用时不需要指南针数据在本机可读
            return _portfolio_from_trades(args, codes)
        amv_regime = None
        if args.amv_long_only:
            amv_regime = load_amv_regime(since=args.start or "2015-01-01")  # regime 起点跟随回测起点
            if not amv_regime:
                ap.error("--amv-long-only 需要指南针 0AMV 数据(compass_amv)，未读到；请在有指南针的机器运行")
            print(f"[INFO] 0AMV regime 覆盖 {len(amv_regime)} 个交易日，仅在『做多』区间进场", file=sys.stderr)
        sector_gate = None
        if args.sector_filter:
            import sector_phase  # noqa: PLC0415
            mpath = Path(args.sector_members)
            members = json.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else {}
            if not members:
                ap.error("--sector-filter 需 sector_members.json(先跑 fetch_sector_index_history.py --members)")
            sector_gate = sector_phase.load_sector_gate(args.sector_index_dir, members)
            n_loaded = getattr(sector_gate, "n_sectors", 0)
            if not n_loaded:
                ap.error(f"--sector-filter 无任何板块指数数据(dir={args.sector_index_dir});"
                         f"先跑 fetch_sector_index_history.py,否则 gate 会静默退化为全放行")
            eff = getattr(sector_gate, "effective_start", None)
            print(f"[INFO] 板块相位 gate: {n_loaded}/{len(members)} 板块有数据, 有效起始 {eff}"
                  f" (dir={args.sector_index_dir})", file=sys.stderr)
            if args.start and eff and args.start < eff:
                print(f"[WARN] --start {args.start} 早于板块数据起始 {eff}:该日前已分类个股一律被 gate 拦截,"
                      f"样本期与不带 filter 的 baseline 不可比", file=sys.stderr)
        trades: list[dict[str, Any]] = []
        import gc
        import time as _time
        n_loaded = 0
        t_load = 0.0        # 读盘 + 前复权累计秒数
        t_eval = 0.0        # 逐 bar 评估累计秒数
        for k, c in enumerate(codes):     # 流式：逐股加载→评估→释放，避免全量载入 OOM
            _t0 = _time.time()
            d = load([c], args.count)
            t_load += _time.time() - _t0
            if d:
                n_loaded += len(d)
                _t0 = _time.time()
                trades += evaluate_trades(
                    d, scorer=SCORERS[args.scorer], step=args.step, weekly=args.weekly,
                    cost_bps=args.cost_bps, amv_regime=amv_regime, bbi_exit_consec=args.bbi_consec,
                    time_stop_bars=args.time_stop, collect_all=bool(args.top_n > 0),
                    entry_gate=ENTRY_GATES[args.entry_filter],
                    stop_mode=args.stop_mode, stop_pct=args.stop_pct,
                    max_signals_per_code=(args.max_signals_per_code or None),
                    feature_panel=bool(args.attribution), sector_gate=sector_gate,
                    scale_out_frac=args.scale_out,
                    breakeven_trigger=args.breakeven, trail_pct=args.trail,
                    stop_trigger=args.stop_trigger,
                    stop_tick_buffer=args.stop_tick_buffer,
                    cost_zone_bars=args.cost_zone_bars,
                    cost_zone_pct=args.cost_zone_pct)
                t_eval += _time.time() - _t0
            del d
            if (k + 1) % 500 == 0:
                gc.collect()
                print(f"[INFO] 已处理 {k + 1}/{len(codes)} 只，累计 {len(trades)} 笔候选", file=sys.stderr)
        # 加载(含前复权)与评估的耗时拆分。加载结果对**所有扫描方案都相同**，
        # 若加载占比高，则「方案外循环」改「股票外循环」可省掉 (N-1)/N 的加载时间
        # ——m2_stop_sweep 模块文档第 ③ 条要的就是这个数。
        # 峰值 RSS 同时打出来：OOM Kill 是这套回测的老问题，而 `--jobs N` 并行会把
        # 内存乘 N ⇒ 必须有实测数字才能定安全路数。
        _rss = peak_rss_mb()
        _mem = f"{_rss:.0f}MB" if _rss else f"未知({_RSS_FAIL or '无原因'})"
        print(f"[TIME] 加载(含前复权) {t_load:.0f}s / 评估 {t_eval:.0f}s"
              f"（加载占 {t_load / max(t_load + t_eval, 1e-9):.0%}，"
              f"{len(codes)} 只票）"
              f"  [MEM] 峰值 {_mem} / {len(trades)} 笔"
              f"{'（collect_all 全候选）' if args.top_n > 0 else ''}", file=sys.stderr)
        _report_gates()
        rc_empty = _empty_result_guard(n_loaded, len(trades), "笔交易", args.allow_empty)
        if rc_empty:
            return rc_empty
        tsum = summarize_trades(trades)
        payload = {"mode": "trade_sim", "scorer": args.scorer, "weekly": args.weekly,
                   "data_source": args.data_source, "start": args.start or None, "end": args.end or None,
                   "cost_bps": args.cost_bps, "amv_long_only": bool(args.amv_long_only),
                   "entry_filter": args.entry_filter, "top_n": args.top_n,
                   "bbi_consec": args.bbi_consec, "time_stop": args.time_stop,
                   "stop_mode": args.stop_mode, "stop_pct": args.stop_pct,
                   # 被 M2 扫描的那几个参数原先**没有落盘** ⇒ 结果文件不自述身份，
                   # 事后无法确认某个文件是哪套参数跑的。指纹见 trades_signature。
                   "trades_signature": trades_signature(args, codes),
                   "codes": codes, "count": args.count, "trade_summary": tsum, "trades": trades}
        if args.top_n > 0:
            payload["portfolio"] = simulate_portfolio_topn(
                trades, top_n=args.top_n, risk_pct=args.risk_pct / 100.0,
                max_concurrent=args.max_concurrent, max_pos_frac=args.max_pos / 100.0)
        elif args.portfolio:
            payload["portfolio"] = simulate_portfolio(
                trades, risk_pct=args.risk_pct / 100.0, max_concurrent=args.max_concurrent,
                max_pos_frac=args.max_pos / 100.0)
        if args.attribution:      # 先算归因,保证 --out JSON 里也带 attribution
            payload["attribution"] = attribution_report(trades)
        if args.out:
            out = Path(args.out)
            if args.summary_only:
                payload = {k: v for k, v in payload.items() if k != "trades"}
            # 流式写：`write_text(json.dumps(...))` 会先拼出整个字符串（indent=2 再放大
            # 1.36 倍），逐笔上万条时白送一次内存峰值——这个回测本来就常被 OOM Kill。
            write_json_stream(out, payload, big=len(trades) > 20000)
            print(f"[OK] 写出 {out}（{len(trades)} 笔交易，scorer={args.scorer}, {'周线' if args.weekly else '日线'}, cost={args.cost_bps}bps, amv_long_only={bool(args.amv_long_only)}）")
        stop_desc = (f"买入K最低" if args.stop_mode == "low" else f"pct {args.stop_pct}%")
        tstop_desc = f" / 时间止损{args.time_stop}根" if args.time_stop else ""
        print(f"\n=== B1 交易模拟（scorer={args.scorer}, {'周线' if args.weekly else '日线'}, "
              f"数据源={args.data_source}"
              f"{(' ' + (args.start or '…') + '~' + (args.end or '…')) if (args.start or args.end) else ''}, "
              f"入场门槛={args.entry_filter}, cost={args.cost_bps}bps, "
              f"{'仅0AMV做多' if args.amv_long_only else '全regime'}, "
              f"止损={stop_desc} / 站上BBI后连破{args.bbi_consec}日卖出{tstop_desc}）===")
        print(tsum["text"])
        if payload.get("portfolio"):
            print("\n" + payload["portfolio"]["text"])
        if args.attribution:
            print("\n=== 特征归因(train/test 前向lift,检验赢家共性可否泛化) ===")
            print(payload["attribution"]["text"])
        return 0

    bars = load(codes, args.count)
    records = evaluate(bars, horizons=horizons, step=args.step,
                       entry_gate=ENTRY_GATES[args.entry_filter],
                       scorer=SCORERS[args.scorer],
                       gate_window=args.gate_window)
    _report_gates()
    rc_empty = _empty_result_guard(len(bars or {}), len(records), "条信号", args.allow_empty)
    if rc_empty:
        return rc_empty
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
        write_json_stream(out, payload, big=len(records) > 20000)
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
