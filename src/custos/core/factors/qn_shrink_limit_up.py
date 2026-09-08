# -*- coding: utf-8 -*-
"""QN·缩量涨停板（骑牛登山体系，规则出处
`governance/strategy/qn/03_limit_up_daban.md` §一/§三）。

源规则（经验规律，未回测）：
- 缩量板出现在**放量阴线/巨量阴线之后**（先放量分歧、再缩量一致），
  且股价离 25/60/144 天线近、均线粘合或刚发散时质量最高。
- 缩量板当日成交量较前一日明显萎缩；**缩量过深（至前日 1/2）视为弱势**不参与。
- 买点在次日（高开才买）——次日行为是入场时机，不属于本因子
  （本因子只识别「缩量板出现」这个形态事实）。

确定性转译（待回测）：
- 涨停 = 当日涨幅（round-2 显示精度，项目惯例）≥ `code_utils.price_limit_pct(code)`
  − ``QN_LIMIT_TOL``
- 缩量 = 当日量 / 前日量 ∈ [``QN_SHRINK_FLOOR``, ``QN_SHRINK_PCT``)
  （0.5~0.7：萎缩但不过深；源规则两端都有明文）
- 前序放量阴线 = 近 ``QN_LOOKBACK`` 根内存在阴线且量 ≥ 同期 20 日均量
  ×``QN_PREV_SURGE``
- 均线近 = 收盘距 MA25/60/144 任一 ≤ ``QN_NEAR_MA_PCT``（%）

pattern 类；绝不 raise。
"""

from __future__ import annotations

from typing import Any

from custos.core.code_utils import price_limit_pct
from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays

FACTOR: dict[str, Any] = {
    "id": "qn_shrink_limit_up",
    "name": "QN·缩量涨停板（放量阴后缩量一致板）",
    "kind": "pattern",
    "status": "untested",  # 新实现未回测（骑牛体系口径 + 合成用例）
    "evidence": "",
    "note": "规则出处 governance/strategy/qn/03_limit_up_daban.md §三；涨停+缩量(0.5~0.7×前日)+近期放量阴线在前+贴近 MA25/60/144；次日买点属入场时机不在本因子",
    "min_bars": 150,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数 ----
QN_LIMIT_TOL = 0.5  # 待回测：涨停判定容差 pp（封板认定，同 qn_ma144_launch）
QN_SHRINK_PCT = 0.7  # 待回测：缩量上界（当日量 < 前日×0.7）
QN_SHRINK_FLOOR = 0.5  # 待回测：缩量下界（≤前日 1/2 = 缩过头，弱势）
QN_PREV_SURGE = 2.0  # 待回测：前序放量阴线的量能门槛（≥20 日均量×2）
QN_LOOKBACK = 10  # 待回测：前序放量阴线回溯窗
QN_NEAR_MA_PCT = 10.0  # 待回测：贴近均线的距离上限%（25/60/144 任一）


def detect(df, code: str = "", _arr: dict | None = None) -> dict[str, Any]:
    """缩量涨停板形态。绝不 raise。

    ``_arr``：研究侧预计算序列（close/open/volume/vol_ma20/ma25/ma60/ma144），
    给定时不读 df 任何列。两路逐位一致（rolling 从第 0 根递归同序）。

    返回键：
        hit        四腿全中（涨停 + 缩量适中 + 前序放量阴 + 贴均线）
        legs       limit_up / shrink / prev_surge_yin / near_ma 明细
    """
    try:
        n = len(df)
        if n < FACTOR["min_bars"]:
            return {
                "available": False,
                "hit": False,
                "reason": f"少于{FACTOR['min_bars']}根K线（{n}）",
            }
        if _arr is None:
            close, _high, _low, vol = _ohlcv_arrays(df)
            open_ = df["open"].astype(float).to_numpy()
            v20 = df["volume"].astype(float).rolling(20).mean().to_numpy()
            c = df["close"].astype(float)
            ma_lasts = {w: float(c.rolling(w).mean().iloc[-1]) for w in (25, 60, 144)}
        else:
            close = _arr["close"][:n]
            open_ = _arr["open"][:n]
            vol = _arr["volume"][:n]
            v20 = _arr["vol_ma20"][:n]
            ma_lasts = {
                25: float(_arr["ma25"][n - 1]),
                60: float(_arr["ma60"][n - 1]),
                144: float(_arr["ma144"][n - 1]),
            }
        # 腿① 涨停
        chg = (close[-1] / close[-2] - 1) * 100 if close[-2] else 0.0
        limit_up = bool(round(chg, 2) >= price_limit_pct(code) - QN_LIMIT_TOL)
        # 腿② 缩量适中（萎缩但不过深）
        vr = float(vol[-1] / vol[-2]) if vol[-2] else 0.0
        shrink = bool(QN_SHRINK_FLOOR <= vr < QN_SHRINK_PCT)
        # 腿③ 前序放量阴线（近 QN_LOOKBACK 根内）
        prev_hits = []
        for t in range(n - 1 - QN_LOOKBACK, n - 1):
            if (
                close[t] < open_[t]
                and v20[t] == v20[t]
                and v20[t]
                and vol[t] >= v20[t] * QN_PREV_SURGE
            ):
                prev_hits.append(n - 1 - t)
        # 腿④ 贴均线（25/60/144 任一 ±QN_NEAR_MA_PCT）
        near: dict[str, Any] = {}
        for w in (25, 60, 144):
            ma = ma_lasts[w]
            near[f"ma{w}"] = bool(
                ma == ma and abs(close[-1] / ma - 1) * 100 <= QN_NEAR_MA_PCT
            )
        near_ma = any(near.values())
        legs = {
            "limit_up": {"hit": limit_up, "chg_pct": round(chg, 2)},
            "shrink": {"hit": shrink, "vol_ratio": round(vr, 3)},
            "prev_surge_yin": {
                "hit": bool(prev_hits),
                "bars_ago_list": prev_hits,
            },
            "near_ma": {"hit": near_ma, **near},
        }
        return {
            "available": True,
            "hit": bool(limit_up and shrink and prev_hits and near_ma),
            "legs": legs,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
