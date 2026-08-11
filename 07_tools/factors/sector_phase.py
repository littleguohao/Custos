# -*- coding: utf-8 -*-
"""板块相位(MACD 波段)——对板块指数收盘序列判"建仓上水/拉升/冲刺(顶背离)/水下调整"。

用于板块择时 gate:只在板块**有利相位**(DIF>0 且未走完冲刺=无近期顶背离/三打)时,放行其成分股进场。
少参数(lookback/fractal),防过拟合;所有阈值待跨年 walk-forward 验证。纯序列运算,绝不 raise。

⚠️ 数据:通达信 880 板块指数历史约 2021-08 起(~5年,含熊含牛);概念板块更短。跨周期结论以 OOS 为准。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))   # indicators 在 07_tools 根

from indicators import macd_series as _macd_series  # noqa: E402  DIF/DEA 唯一实现

FACTOR: dict[str, Any] = {
    "id": "sector_phase",
    "name": "板块相位（MACD 相位择时）",
    "kind": "state",
    "status": "active",
    "evidence": "governance/research/R4_timing_amv_sector.md",
    "note": "R4：0AMV 之后第二个 OOS 站得住的增强，熊市减亏 ~4-6pp",
    "min_bars": 60,
    "live_use": "gate",
    "stage": "release",
}


MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
PHASE_LOOKBACK = 60          # 顶背离/三打回看窗口(交易日)
PHASE_FRACTAL = 2            # 摆动高点左右确认根数


def _norm6(code) -> str:
    """代码归一为 6 位数字:'600000'/'SH600000'/'600000.SH' → '600000'。"""
    s = str(code)
    return s[:6] if s[:6].isdigit() else s.split(".")[0][-6:].zfill(6)


def _clean_close(close) -> "tuple[pd.Series, list[int]]":
    """收盘序列清洗:转数值、丢非数值项,返回 (Series, 保留项的原索引)。对齐安全。"""
    s = pd.to_numeric(pd.Series(list(close)), errors="coerce")
    keep = [i for i, v in enumerate(s) if v == v]           # 非 NaN 的原索引
    return pd.Series([s[i] for i in keep], dtype=float).reset_index(drop=True), keep


def _swing_highs(x: np.ndarray, f: int, w0: int) -> list[int]:
    """收盘摆动高点:窗口[i-f,i+f]内 x[i]=max 且至少 2f-1 根严格更低(允许1平台)。"""
    out = []
    n = len(x)
    for i in range(max(w0, f), n - f):
        seg = x[i - f:i + f + 1]
        if x[i] == seg.max() and int((seg < x[i]).sum()) >= 2 * f - 1:
            out.append(i)
    return out


def compute_sector_phase(close, lookback: int = PHASE_LOOKBACK,
                         fractal: int = PHASE_FRACTAL) -> dict[str, Any]:
    """输入板块指数收盘(list/Series)→ 相位字典。favorable=可在该板块选股进场。"""
    c, _ = _clean_close(close)
    n = len(c)
    if n < MACD_SLOW + MACD_SIGNAL + fractal + 5:
        return {"available": False}
    dif, dea, _hist = _macd_series(c)   # 2026-08-09 起走 indicators.macd_series（唯一实现）
    dif_v = dif.values
    close_v = c.values
    dif_last = float(dif_v[-1])
    above_zero = bool(dif_last > 0)                          # 建仓已上水/趋势在
    w0 = max(fractal, n - lookback)
    hi = _swing_highs(close_v, fractal, w0)
    top_div = three_peaks = False
    if len(hi) >= 2:
        a, b = hi[-2], hi[-1]
        top_div = bool(close_v[b] > close_v[a] and dif_v[b] < dif_v[a])   # 价新高、DIF不创高=顶背离
    if len(hi) >= 3:
        p1, p2, p3 = hi[-3], hi[-2], hi[-1]
        three_peaks = bool(close_v[p1] < close_v[p2] < close_v[p3]
                           and dif_v[p1] > dif_v[p2] > dif_v[p3])          # 三打白骨精
    exhausted = bool(top_div or three_peaks)                # 冲刺(接近)走完
    favorable = bool(above_zero and not exhausted)
    if not above_zero:
        phase = "水下/调整"
    elif exhausted:
        phase = "冲刺/顶背离(过滤)"
    else:
        phase = "建仓上水/拉升(有利)"
    return {"available": True, "dif": round(dif_last, 4), "above_zero": above_zero,
            "top_divergence": top_div, "three_peaks": three_peaks,
            "exhausted": exhausted, "favorable": favorable, "phase": phase}


def favorable_series(dates, close, lookback: int = PHASE_LOOKBACK,
                     fractal: int = PHASE_FRACTAL) -> dict[str, bool]:
    """逐日**因果**有利标志(date→bool):每个 t 只用截至 t 的信息(摆动高点需 i+fractal 确认)。
    favorable[t] = DIF[t]>0 且 截至 t 无(顶背离/三打)。供板块相位 gate 按 as-of 查询。"""
    c_full, keep = _clean_close(close)
    c = c_full.values
    n = len(c)
    ds = [str(dates[i])[:10] for i in keep]               # 与被保留的收盘对齐(丢非数值项不错位)
    if n < MACD_SLOW + MACD_SIGNAL + fractal + 5:
        return {d: False for d in ds}
    dif = _macd_series(pd.Series(c))[0].values
    sh = _swing_highs(c, fractal, 0)                       # 全部摆动高点(每个在 i+fractal 才确认)
    out: dict[str, bool] = {}
    for t in range(n):
        if dif[t] <= 0:
            out[ds[t]] = False
            continue
        conf = [i for i in sh if i + fractal <= t and i >= t - lookback + 1]  # 截至 t 已确认且在回看内(与 compute_sector_phase 同宽)
        exhausted = False
        if len(conf) >= 2:
            a, b = conf[-2], conf[-1]
            exhausted = bool(c[b] > c[a] and dif[b] < dif[a])              # 顶背离
        if not exhausted and len(conf) >= 3:
            p1, p2, p3 = conf[-3], conf[-2], conf[-1]
            exhausted = bool(c[p1] < c[p2] < c[p3] and dif[p1] > dif[p2] > dif[p3])  # 三打
        out[ds[t]] = not exhausted
    return out


def load_sector_gate(index_dir, members: dict[str, list],
                     lookback: int = PHASE_LOOKBACK):
    """构建板块相位 gate: gate(code6, date)->bool。
    index_dir: 板块指数 CSV 目录({sector}.csv, date/close)；members: {sector_code:[stock codes]}。
    未分类个股 → True(不过滤);已分类 → 其任一所属板块 as-of 有利即 True。绝不 raise。
    """
    import bisect
    from pathlib import Path as _P
    idx = _P(index_dir)
    fav_by_sec: dict[str, tuple[list, dict]] = {}
    for sec in members:
        p = idx / f"{sec}.csv"
        if not p.is_file():
            continue
        try:
            df = pd.read_csv(p)
            fav = favorable_series(df["date"].tolist(), df["close"].tolist(), lookback=lookback)
            fav_by_sec[sec] = (sorted(fav), fav)
        except Exception:  # noqa: BLE001
            continue
    # code→板块:复用 sector_mainstream.invert_members(剔除地区/风格,统一 _norm6 口径),再限定到有相位的板块
    try:
        import sector_mainstream as _sm  # noqa: PLC0415
        _c2s = _sm.invert_members(members, exclude_types=True, norm=_norm6)
    except Exception:  # noqa: BLE001
        _c2s = {}
        for sec, codes in members.items():
            for cc in codes:
                _c2s.setdefault(_norm6(cc), []).append(sec)
    code2sec: dict[str, list] = {c6: [s for s in secs if s in fav_by_sec]
                                 for c6, secs in _c2s.items()}
    code2sec = {c6: secs for c6, secs in code2sec.items() if secs}

    def _asof(sorted_dates, fav, date):
        j = bisect.bisect_right(sorted_dates, date) - 1
        return fav.get(sorted_dates[j], False) if j >= 0 else False

    def gate(code6: str, date: str) -> bool:
        secs = code2sec.get(str(code6)[:6])
        if not secs:
            return True                                    # 未分类 → 不过滤
        for sec in secs:
            sd, fav = fav_by_sec[sec]
            if _asof(sd, fav, date):
                return True                                # 任一板块有利即放行
        return False

    # 元数据:调用方据此防止"目录缺失→gate 静默退化为全放行"的假象,并提示 gate 有效起始日
    gate.n_sectors = len(fav_by_sec)                       # type: ignore[attr-defined]
    gate.effective_start = min((sd[0] for sd, _ in fav_by_sec.values() if sd), default=None)  # type: ignore[attr-defined]
    return gate


def build_phase_resolver(index_dir, members: dict[str, list],
                         lookback: int = PHASE_LOOKBACK):
    """LIVE 用:预计算各板块**当前**相位 + code→板块映射,返回 resolve(code6)->相位字典(hint,不封顶)。
    个股所属任一板块有利 → favorable=True;附代表相位标签。数据缺失 → available=False。绝不 raise。"""
    from pathlib import Path as _P
    idx = _P(index_dir)
    phase_by_sec: dict[str, dict] = {}
    for sec in members:
        p = idx / f"{sec}.csv"
        if not p.is_file():
            continue
        try:
            df = pd.read_csv(p)
            ph = compute_sector_phase(df["close"].tolist(), lookback=lookback)
            if ph.get("available"):
                phase_by_sec[sec] = ph
        except Exception:  # noqa: BLE001
            continue
    try:
        import sector_mainstream as _sm  # noqa: PLC0415
        _c2s = _sm.invert_members(members, exclude_types=True, norm=_norm6)
    except Exception:  # noqa: BLE001
        _c2s = {}
        for sec, codes in members.items():
            for cc in codes:
                _c2s.setdefault(_norm6(cc), []).append(sec)
    code2sec: dict[str, list] = {c6: [s for s in secs if s in phase_by_sec]
                                 for c6, secs in _c2s.items()}
    code2sec = {c6: secs for c6, secs in code2sec.items() if secs}

    def resolve(code6: str) -> dict[str, Any]:
        secs = code2sec.get(str(code6)[:6], [])
        phases = [phase_by_sec[s] for s in secs if s in phase_by_sec]
        if not phases:
            return {"available": False}
        favorable = any(p.get("favorable") for p in phases)
        rep = next((p for p in phases if p.get("favorable")), phases[0])
        return {"available": True, "favorable": bool(favorable),
                "phase": rep.get("phase"), "any_exhausted": any(p.get("exhausted") for p in phases),
                "sectors": secs[:5], "n_sectors": len(phases)}

    return resolve
