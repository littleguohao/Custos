# -*- coding: utf-8 -*-
"""主力出货五方式（顶部派发形态），用于清仓与选股规避

2026-08-06 从 `screening/enrich_candidates.py` 抽出（**零行为变化**，逐字搬）。
抽出的动因：因子实现必须**全项目唯一一份**，其他模块通过调用访问 ——
内联在 1723 行的选股链主流程里，既无法单独回测，也无法防止别处再写一份。

2026-08-13（25chuhuo 讲义覆盖度缺口，owner 批准）补全三项：
①/② 次日确认豁免层（`confirm_distribution`：pending/confirmed/revoked，
不换庄假出货不计真派发；不改既有命中语义）、③ 阶梯跌破补全（平量阴计数 +
DKS 黄线跌破）、顶部大风车（`detect_top_windmill`，高位+长上影/宽幅+次日
不反包确认，自带 T+1 状态机）。
"""

from __future__ import annotations

from statistics import median as _median
from typing import Any, Optional
from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays


from custos.core.indicators import _infer_price_limit, qsx_series, dks_series  # noqa: E402
from custos.core.indicators import amplitude_pct as amplitude_pct_of  # noqa: E402  振幅唯一实现

FACTOR: dict[str, Any] = {
    "id": "distribution",
    "name": "主力出货五方式（顶部派发形态），用于清仓与选股规避",
    "kind": "pattern",
    "status": "active",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "主力出货五方式（顶部派发形态），用于清仓与选股规避；2026-08-13 补全：次日确认豁免层（confirm_distribution）+ ③平量阴/DKS 跌破 + 顶部大风车（25chuhuo 覆盖度缺口）",
    "min_bars": 1,
    "live_use": "gate",
    "stage": "release",
}

DIST_RECENT = 5  # 待回测：出货形态观察最近N根
DIST_ACCEL_WIN = 10  # 待回测：加速涨幅窗口（日）
DIST_ACCEL_GAIN = 25.0  # 待回测：加速涨幅%下限（阴线前）
DIST_BIG_BEAR_FRAC = 0.5  # 待回测：大阴=跌幅≥涨跌幅制度×0.5
DIST_LONG_BEAR_FRAC = 0.8  # 待回测：长阴/近跌停=跌幅≥涨跌幅制度×0.8
DIST_HUGE_VOL_RATIO = 2.0  # 待回测：天量/巨量=≥20日均量×2
DIST_HUGE_VOL_WIN = 20  # 待回测：天量对比窗口（日）
DIST_STAIR_MIN_BARS = 3  # 待回测：阶梯放量阴线最少连续根数
DIST_STAIR_BREAK_VR = 1.2  # 待回测：放量跌破QSX的量比下限
DIST_TOP_WINDOW = 10  # 待回测：顶部区间（绿肥红瘦/双头）窗口
DIST_DOUBLE_TOP_TOL = 3.0  # 待回测：双头两顶相近容差%
DIST_SUBHIGH_SHRINK = 0.9  # 待回测：次高前一日缩量量比上限
DIST_MIN_VOL_MA20_FRAC = 0.05  # 待回测：vol_ma20 低于全序列均量×此比例时视为近零（派发检测器 available=False）

# ---- 25chuhuo 讲义覆盖度缺口补全（2026-08-13，owner 批准）----
# 依据：中国中铁 14.12.22（无加速段的放量=试盘非顶）、15.6.8（天量长阴后次日
# 反包涨停=换庄）；01_swing_rules §七.2 的 T+1 收盘后判定条款（文档早有、
# 代码未实现，顺势落进代码）。①/② 的既有命中语义**不动**——豁免在
# confirm_distribution 一层表达（状态机：pending/confirmed/revoked），
# ③ 的口径补全（平量阴计数 + DKS 跌破）是直接放宽命中条件。
DIST_WINDMILL_SHADOW_BODY = 1.0  # 待回测：大风车长上影=上影 ≥ 实体×此值
DIST_WINDMILL_RANGE_PCT = 7.0  # 待回测：宽幅震荡 K=振幅（高−低）/前收 ≥ 此值%
DIST_WINDMILL_TOP_FRAC = (
    0.93  # v0.62（owner 定向，2026-08-15）：0.98 -> 0.93。高位=近60根最高×此值
)
# --北方铜业 000737@2026-08-10 的天量双长影大风车出现在**次高位**（信号 K 最高
#   15.54 顶后反弹到 94.07%），0.98 只认「贴着最高点」，把次高位大风车全部漏掉。
# v0.62（owner 定向，2026-08-15）新增天量腿：大风车=高位+长影/宽幅+**天量**
# （owner：「长上影和长下影的历史天量大风车」--讲义口径里量能是形态组成部分，
# 主力对倒出货的量能证据）。北方铜业 08-10 实测 1.565×MA20。
DIST_WINDMILL_VOL_RATIO = 1.5  # 待回测：天量=信号 K 量 ≥ 前20日量**中位数**×此值（v0.62 起中位数，见 :351 附近注释——偶发天量不把基准顶飞）


def detect_distribution(df, code: str = "") -> dict[str, Any]:
    """主力出货五方式（顶部派发，B1 §七.3）：负向因子，用于选股规避/降档。

    ① 顶部天量大阴、② 次高点巨量长阴、③ 阶梯放量跌破QSX、④ 双头双巨阴、
    ⑤ 顶部绿肥红瘦。命中≥1→watch；命中①/②或≥2→high。阈值均为待回测参数。
    """
    close, high, _low, vol = _ohlcv_arrays(df)
    open_ = df["open"].astype(float).to_numpy()
    n = len(df)
    if n < 30:
        return {
            "available": False,
            "signals": {},
            "hits": [],
            "hit_count": 0,
            "severe": False,
            "risk_level": "none",
        }
    limit = _infer_price_limit(code, df)
    big_bear = limit * DIST_BIG_BEAR_FRAC
    long_bear = limit * DIST_LONG_BEAR_FRAC
    vol_ma20 = float(vol[max(0, n - DIST_HUGE_VOL_WIN - 1) : n - 1].mean())
    # vol_ma20 近零（长期停牌/零成交脏数据）时量比类判定全部失真 → 检测器不可用
    series_vol_mean = float(vol.mean()) if n else 0.0
    if not series_vol_mean or vol_ma20 < series_vol_mean * DIST_MIN_VOL_MA20_FRAC:
        return {
            "available": False,
            "signals": {},
            "hits": [],
            "hit_count": 0,
            "severe": False,
            "risk_level": "none",
            "reason": f"vol_ma20 近零（{vol_ma20:.1f} < 全序列均量 {series_vol_mean:.1f}×{DIST_MIN_VOL_MA20_FRAC}）",
        }
    qsx = qsx_series(
        df["close"]
    ).to_numpy()  # 2026-08-09 起走 indicators 唯一实现（原内联 EMA×2 同式）
    dks = dks_series(df["close"]).to_numpy()  # 黄线（③ 的 DKS 跌破补判，2026-08-13）

    def chg(t: int) -> float:
        return (close[t] / close[t - 1] - 1) * 100 if t >= 1 and close[t - 1] else 0.0

    def vr5(t: int) -> float | None:
        base = vol[max(0, t - 5) : t].mean()
        return float(vol[t] / base) if base else None

    sig: dict[str, Any] = {}

    # ① 顶部天量大阴：近DIST_RECENT根内 大阴 + 天量 + 阴线前加速
    hit1 = None
    for t in range(n - DIST_RECENT, n):
        if t < DIST_ACCEL_WIN + 1:
            continue
        c = chg(t)
        # 「天量」＝ 量 ≥ 20日均量×DIST_HUGE_VOL_RATIO（与②同一口径，见顶部常量）。
        # 审计：原先还 or 了 `vol[t] >= vol[t-20:t+1].max()`——该切片**含 t 自身**，
        # 于是它恒等于"当日是窗口最大量"，即 20 日量新高，完全旁路了 2×MA20 阈值：
        # 一只均量平稳的票只要今天量比昨天高一点点就算"天量"，配上大阴+加速就被判出货。
        huge = bool(vol_ma20) and vol[t] >= vol_ma20 * DIST_HUGE_VOL_RATIO
        accel = (
            (close[t - 1] / close[t - DIST_ACCEL_WIN] - 1) * 100
            if close[t - DIST_ACCEL_WIN]
            else 0.0
        )
        if close[t] < open_[t] and c <= -big_bear and huge and accel >= DIST_ACCEL_GAIN:
            hit1 = {
                "bars_ago": n - 1 - t,
                "change_pct": round(c, 2),
                "vol_ratio_ma20": round(float(vol[t] / vol_ma20), 3)
                if vol_ma20
                else None,
                "accel_pct": round(accel, 2),
            }
            break
    sig["top_huge_vol_bear"] = {"hit": hit1 is not None, "detail": hit1}

    # ② 次高点巨量长阴：前一日缩量创新高/次高 + 当日巨量长阴
    hit2 = None
    for t in range(n - DIST_RECENT, n):
        if t < 25:
            continue
        c = chg(t)
        prev_new_high = high[t - 1] >= high[max(0, t - 21) : t - 1].max()
        v5 = vr5(t - 1)
        prev_shrink = v5 is not None and v5 <= DIST_SUBHIGH_SHRINK
        huge = vol_ma20 and vol[t] >= vol_ma20 * DIST_HUGE_VOL_RATIO
        if (
            close[t] < open_[t]
            and c <= -long_bear
            and huge
            and prev_new_high
            and prev_shrink
            and v5 is not None  # prev_shrink 已蕴含；显式写出是为了类型收窄
        ):
            hit2 = {
                "bars_ago": n - 1 - t,
                "change_pct": round(c, 2),
                "prev_vol_ratio5": round(float(v5), 3),
                "vol_ratio_ma20": round(float(vol[t] / vol_ma20), 3),
            }
            break
    sig["subhigh_vol_bear"] = {"hit": hit2 is not None, "detail": hit2}

    # ③ 阶梯放量跌破趋势线：近DIST_RECENT根内收盘放量跌破 QSX（白线）**或跌破
    #    DKS（黄线，平量也判）**，且此前连续≥3根阴线。
    #    2026-08-13 补全（25chuhuo 讲师口径）：「平量阴线也算」⇒ 连续阴计数不再
    #    要求量能递增（放量/平量分拆计数供回测消融）；「跌破黄线（DKS）也要判」
    #    ⇒ 新增 DKS 跌破路径（DKS 是更慢的中期趋势线，跌破它量未必放大）。
    hit3 = None
    for t in range(n - DIST_RECENT, n):
        if t < DIST_STAIR_MIN_BARS + 6:
            continue
        vrt = vr5(t)
        broke_qsx = close[t] < qsx[t] and vrt is not None and vrt >= DIST_STAIR_BREAK_VR
        broke_dks = bool(dks[t] == dks[t] and close[t] < dks[t])  # 黄线跌破（平量也判）
        # 连续阴线计数（平量阴也算），并拆放量/平量供回测消融
        cnt = 0
        cnt_vol_up = 0
        for k in range(t, max(0, t - 8), -1):
            if close[k] < open_[k]:
                cnt += 1
                vrk = vr5(k)
                if vol[k] >= vol[k - 1] or (vrk is not None and vrk >= 1.0):
                    cnt_vol_up += 1
            else:
                break
        if (broke_qsx or broke_dks) and cnt >= DIST_STAIR_MIN_BARS:
            hit3 = {
                "bars_ago": n - 1 - t,
                "consecutive_bears": cnt,
                "of_which_vol_up": cnt_vol_up,
                "vol_ratio5": round(vrt, 3) if vrt is not None else None,
                "below": "qsx" if broke_qsx else "dks",
            }
            break
    sig["stairstep_vol_decline"] = {"hit": hit3 is not None, "detail": hit3}

    # ④ 双头双巨阴：近窗口内两个相近高点，各自其后≤2根内出现放量阴
    hit4 = None
    w0 = max(0, n - DIST_TOP_WINDOW * 2)
    peaks = [
        i
        for i in range(w0 + 2, n - 2)
        if high[i] == high[i - 2 : i + 3].max()
        and float((high[i - 2 : i + 3] == high[i]).sum()) == 1
    ]
    if len(peaks) >= 2:
        p2 = peaks[-1]
        p1 = max((p for p in peaks[:-1]), key=lambda i: high[i], default=None)
        if p1 is not None and p2 - p1 >= 3:
            close_tops = abs(high[p1] / high[p2] - 1) * 100 <= DIST_DOUBLE_TOP_TOL

            def bear_vol_after(p: int) -> bool:
                for t in range(p + 1, min(n, p + 3)):
                    vrt = vr5(t)
                    if close[t] < open_[t] and vrt is not None and vrt >= 1.5:
                        return True
                return False

            if close_tops and bear_vol_after(p1) and bear_vol_after(p2):
                hit4 = {
                    "peak1_bars_ago": n - 1 - p1,
                    "peak2_bars_ago": n - 1 - p2,
                    "tops_gap_pct": round(abs(high[p1] / high[p2] - 1) * 100, 2),
                }
    sig["double_top_vol_bear"] = {"hit": hit4 is not None, "detail": hit4}

    # ⑤ 顶部绿肥红瘦：顶部区间阴线实体均值 > 阳线实体均值 且 阴量 > 阳量
    seg = range(n - DIST_TOP_WINDOW, n)
    near_top = (
        True if n < 60 else high[-DIST_TOP_WINDOW:].max() >= high[-60:].max() * 0.98
    )
    bear_bodies = [
        abs(close[t] / open_[t] - 1) * 100
        for t in seg
        if close[t] < open_[t] and open_[t]
    ]
    bull_bodies = [
        abs(close[t] / open_[t] - 1) * 100
        for t in seg
        if close[t] > open_[t] and open_[t]
    ]
    bear_vols = [vol[t] for t in seg if close[t] < open_[t]]
    bull_vols = [vol[t] for t in seg if close[t] > open_[t]]
    hit5 = bool(
        near_top
        and bear_bodies
        and bull_bodies
        and (sum(bear_bodies) / len(bear_bodies) > sum(bull_bodies) / len(bull_bodies))
        and bear_vols
        and bull_vols
        and (sum(bear_vols) / len(bear_vols) > sum(bull_vols) / len(bull_vols))
    )
    sig["top_green_heavy_red_light"] = {
        "hit": hit5,
        "detail": {
            "bear_body_mean_pct": round(sum(bear_bodies) / len(bear_bodies), 3)
            if bear_bodies
            else None,
            "bull_body_mean_pct": round(sum(bull_bodies) / len(bull_bodies), 3)
            if bull_bodies
            else None,
        }
        if near_top
        else None,
    }

    # ⑥ 顶部大风车（v0.62 接线，owner 定向 2026-08-15）：高位+长影/宽幅+天量+T+1
    #    不反包确认。此前仅落 confirm_distribution 证据层（v0.54），不进出货信号--
    #    北方铜业 000737@2026-08-10 的天量双长影大风车（次高位 94.07%、量 1.565×MA20）
    #    在 QD 多头名单里无任何出货标志，owner 拍板接线。
    #    仅 **confirmed** 计 hit（pending 待确认/revoked 反包豁免不计--T+1 纪律）；
    #    单独命中 -> watch（与③④⑤同级），hits≥2 -> high 的既有规则自然覆盖。
    wm = detect_top_windmill(df, code)
    sig["top_windmill"] = {
        "hit": bool(
            wm.get("available") and wm.get("hit") and wm.get("status") == "confirmed"
        ),
        "detail": wm.get("detail"),
    }

    hits = [k for k, v in sig.items() if v["hit"]]
    severe = sig["top_huge_vol_bear"]["hit"] or sig["subhigh_vol_bear"]["hit"]
    risk = "high" if (severe or len(hits) >= 2) else ("watch" if hits else "none")
    return {
        "available": True,
        "signals": sig,
        "hits": hits,
        "hit_count": len(hits),
        "severe": bool(severe),
        "risk_level": risk,
        "price_limit": limit,
    }


# ============================================================================
# 25chuhuo 覆盖度缺口补全（2026-08-13，owner 批准）
# ============================================================================


def detect_top_windmill(df, code: str = "") -> dict[str, Any]:
    """顶部大风车（25chuhuo 讲义）：**高位 + 长上影/宽幅震荡 K + 次日不反包确认**。

    讲义口径：「不确定是否见顶 → 卖一半；次日必须反包否则全卖」——
    代码只产信号与状态，仓位动作归文档/人。绝不 raise。

    - 高位：信号 K 的最高价 ≥ 近 60 根最高 × ``DIST_WINDMILL_TOP_FRAC``
      （v0.62：0.98 -> 0.93，次高位大风车计入--北方铜业 2026-08-10 案例）；
    - 大风车 K：长上影（上影 ≥ 实体 × ``DIST_WINDMILL_SHADOW_BODY``）或
      宽幅震荡（振幅（高−低）/前收 ≥ ``DIST_WINDMILL_RANGE_PCT``%）；
    - 天量（v0.62 新增）：信号 K 量 ≥ 前 20 根量**中位数** × ``DIST_WINDMILL_VOL_RATIO``
      --owner 口径「长上影和长下影的历史天量大风车」，量能是对倒出货的证据腿；
    - T+1 状态机（01_swing_rules §七.2 的 T+1 收盘后判定条款）：
      信号 K 是最后一根 → ``pending``（次日未收盘）；
      次日收盘 ≥ 信号 K 实体上沿（反包）→ ``revoked``（豁免，不计出货）；
      否则 → ``confirmed``。
    """
    try:
        close, high, low, vol = _ohlcv_arrays(df)
        open_ = df["open"].astype(float).to_numpy()
        n = len(df)
        if n < 30:
            return {
                "available": False,
                "hit": False,
                "status": None,
                "reason": f"少于30根K线（{n}）",
            }
        top60 = float(high[-60:].max()) if n >= 60 else float(high.max())
        hit = None
        for t in range(n - 1, max(0, n - DIST_RECENT) - 1, -1):  # 取最近一根
            body = abs(close[t] - open_[t])
            upper = high[t] - max(close[t], open_[t])
            # 振幅走 indicators.amplitude_pct（唯一实现，分母=前收）——不写内联
            # (high-low)/x（有单实现守卫）。
            amp = amplitude_pct_of(high[t], low[t], close[t - 1] if t >= 1 else None)
            rng_pct = amp if amp is not None else 0.0
            windmill = (body > 0 and upper >= body * DIST_WINDMILL_SHADOW_BODY) or (
                rng_pct >= DIST_WINDMILL_RANGE_PCT
            )
            near_top = high[t] >= top60 * DIST_WINDMILL_TOP_FRAC
            # v0.62 天量腿：前 20 根**中位量**（不含信号 K 自身）。用中位数而非
            # 均值：信号 K 之前的拉升段常有放量（北方铜业 08-05/08-07 式），均值
            # 基线被抬高会把真天量错判为平量（08-10 实测：均值比 1.477 vs 中位
            # 数比 1.691）。「天量=显著高于常态量能」，中位数才是常态。
            vol_base = float(_median(vol[max(0, t - DIST_HUGE_VOL_WIN) : t]))
            huge_vol = bool(vol_base) and vol[t] >= vol_base * DIST_WINDMILL_VOL_RATIO
            if windmill and near_top and huge_vol:
                if t + 1 >= n:
                    status = "pending"
                elif close[t + 1] >= max(open_[t], close[t]):
                    status = "revoked"  # 次日反包 ⇒ 豁免（讲义：次日必须反包）
                else:
                    status = "confirmed"  # 次日不反包 ⇒ 确认
                hit = {
                    "bars_ago": n - 1 - t,
                    "upper_shadow_frac": round(upper / body, 3) if body > 0 else None,
                    "range_pct": round(rng_pct, 2),
                    "vol_ratio_ma20": round(float(vol[t] / vol_base), 3)
                    if vol_base
                    else None,
                    "status": status,
                }
                break
        return {
            "available": True,
            "hit": hit is not None,
            "status": hit["status"] if hit else None,
            "detail": hit,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "status": None,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }


def confirm_distribution(
    df, code: str = "", det: Optional[dict] = None
) -> dict[str, Any]:
    """次日确认豁免层（换庄/假出货，25chuhuo 讲义；T+1 收盘后判定）。

    对 ``detect_distribution`` 命中的 ① 顶部天量大阴 / ② 次高点巨量长阴：

        信号 K 的**次日**未破信号 K 低点，**或**反包（收盘收复信号 K 实体上沿）
        ⇒ ``revoked``（豁免：试盘/换庄，非真派发 —— 中国中铁 14.12.22/15.6.8）；
        信号 K 是最后一根（T+1 未收盘）⇒ ``pending``（待确认，次日再判）；
        否则 ⇒ ``confirmed``。

    ⚠️ **不改 detect_distribution 的既有命中语义**：hits/risk_level 照旧；
    豁免只在本层表达。``revoked`` 的 ①/② 不应再当「已派发」用——下游展示
    「待确认/已撤销」状态（同项目 degraded/best-effort 惯例：状态如实可见）。

    顺带聚合顶部大风车（``detect_top_windmill``，自带 T+1 状态机）。
    ``det`` 可传已算好的 detect_distribution 结果避免重算（enrich 如此）。
    """
    out_na = {
        "available": False,
        "confirmations": {},
        "revoked": [],
        "top_windmill": {"available": False, "hit": False, "status": None},
    }
    try:
        det = detect_distribution(df, code) if det is None else det
        if not det.get("available"):
            return out_na
        close, _high, low, _vol = _ohlcv_arrays(df)
        open_ = df["open"].astype(float).to_numpy()
        n = len(df)

        conf: dict[str, Any] = {}
        revoked: list[str] = []
        for key in ("top_huge_vol_bear", "subhigh_vol_bear"):
            s = (det.get("signals") or {}).get(key) or {}
            if not s.get("hit"):
                conf[key] = None
                continue
            t = n - 1 - int(s["detail"]["bars_ago"])
            if t + 1 >= n:
                st = "pending"
            else:
                held = low[t + 1] >= low[t]  # 次日未破信号 K 低点
                recovered = close[t + 1] >= max(open_[t], close[t])  # 反包
                st = "revoked" if (held or recovered) else "confirmed"
            conf[key] = st
            if st == "revoked":
                revoked.append(key)
        return {
            "available": True,
            "confirmations": conf,
            "revoked": revoked,
            "top_windmill": detect_top_windmill(df, code),
        }
    except Exception as exc:  # noqa: BLE001
        return {**out_na, "error": f"{type(exc).__name__}:{str(exc)[:80]}"}
