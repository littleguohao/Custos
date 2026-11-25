# -*- coding: utf-8 -*-
"""B1 结构检测器族（b1_structure）—— 非一波流 / 修复信号 / 五日战法 / 流动性 / 止损参考位。

2026-08-20（v0.86，因子化批 B）从
`pipeline/screening/enrich_candidates.py` 迁入（check_non_one_wave 及 _now_* 族、
check_repair_signals 及 _repair_* 族、check_five_day_entry、check_liquidity、
_stop_ref，及 NOW_* / REPAIR_* / FIVE_DAY_* / LIQUIDITY_WIN / STOP_LOOKBACK 常量）。
**行为零变化**：函数体逐字未动；enrich_candidates 改为 import 调用
（`enrich_candidates.check_*` / `_stop_ref` / `STOP_LOOKBACK` 仍是同一对象，
tests 的 monkeypatch 通道不变）。

`_stop_ref`（建议止损位=近 STOP_LOOKBACK 日最低价）逻辑自包含（纯 df→float），
随批 B 一并迁入本模块；enrich 的 _base_scalars / _assemble_metrics 改从这里导入
（STOP_LOOKBACK 常量在组装 stop_loss_ref.basis 文案时也要用）。

## status 定档理由（candidate）

- `live_use=scorer` / `stage=release` 是**事实**：five_day_entry 喂技术分
  「five_day_entry +8」腿、repair_signals 每项 +4（上限 +8）、non_one_wave
  confirmed +5；non_one_wave revoked 另喂封顶 C cap 判定；check_liquidity
  默认仅打 low_liquidity flag（是否降档由 cap_rules.liquidity_floor 控制）。
- `NOT_FOR_LIVE={needs_work, untested}` 由
  `tests/test_factor_registry.py::test_needs_work_cannot_be_gate_or_scorer`
  机械禁止与 scorer 共存 ⇒ 合规候选只剩 active / candidate。
- `active` 语义是「已验证可用」——本轮只是**搬迁**（零行为变化），没有新增
  任何回测证据，NOW_*/REPAIR_*/FIVE_DAY_* 参数仍是「待回测」启发式
  ⇒ 不能标 active。
- ⇒ 取 **candidate**（有依据未终审），阈值/分值校准挂回测 TODO。
"""

from __future__ import annotations

from typing import Any, Optional

from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays  # noqa: E402
from custos.core.indicators import kdj, macd_series, pct_change  # noqa: E402

FACTOR: dict[str, Any] = {
    "id": "b1_structure",
    "name": "B1 结构检测器族（check_non_one_wave / check_repair_signals / "
    "check_five_day_entry / check_liquidity / _stop_ref）",
    "kind": "pattern",
    # 见模块 docstring「status 定档理由」：scorer ⇒ 不能是 needs_work/untested；
    # 无回测证据 ⇒ 不能标 active；取 candidate 并挂回测 TODO。
    "status": "candidate",
    "evidence": "",
    "note": "v0.86 自 enrich_candidates 迁入（零行为变化）；喂技术分 five_day_entry +8 / "
    "repair_signals 每项 +4(上限+8) / non_one_wave confirmed +5 打分腿；non_one_wave "
    "revoked 另喂封顶 C cap；check_liquidity 默认仅 low_liquidity flag，参数待回测校准",
    "min_bars": 21,
    "live_use": "scorer",
    "stage": "release",
}

# --- B1/CZ 策略对齐参数 -------------------------------------------------
# 以下阈值全部标注"待回测参数"：策略原文（B1 §四、CZ §十六）
# 要求阈值可配置、实际值随候选落盘，不得静默使用；完成样本回测前不得
# 视为已校准。口径出处见 governance/contracts/SCREENING_WORKFLOW.md "策略对齐"章。

NOW_MILD_VOL_BURST = 2.0  # 待回测参数：上涨段单日量/段均量上限（温和放量）
NOW_PULLBACK_VOL_RATIO = 0.7  # 待回测参数：回调段均量/上涨段均量上限

REPAIR_J_PREV_MAX = 20.0  # 待回测参数：J拐头向上（昨日J上限）
REPAIR_VOL_SHRINK = 0.7  # 待回测参数：缩量止跌量比上限
REPAIR_CHANGE_PCT = 2.0  # 待回测参数：止跌涨跌幅区间±%

FIVE_DAY_SPIKE_RATIO = 1.45  # 五日战法：近7日巨量倍数（CZ §十六）。原文"前一交易日均量"存歧义，按前一交易日单日量实现（vol[t]/vol[t-1]），待策略 owner 确认
FIVE_DAY_SPIKE_WINDOW = 7  # 五日战法：巨量观察窗口（CZ §十六）

# --- 正交因子（非量价形态）待回测参数 ---
# 方向A(2026-07-23)：全市场回测证实突破式打分非短周期 alpha，转接正交维度。
LIQUIDITY_WIN = 20  # 待回测：近N日均成交额窗口

STOP_LOOKBACK = 10  # 近10日最低价。🔄 v0.528 起 live 不再使用（owner 2026-11-16），只留研究侧 R41 as-of 重算


def _top_bears_since(df, i_low: int) -> list[dict[str, Any]]:
    """上涨段起点之后的顶部 / 次高点放量阴线（唯一判定在 distribution.top_bear_bars；同层惰性导入）。"""
    from custos.core.factors.distribution import top_bear_bars  # noqa: PLC0415

    return top_bear_bars(df.reset_index(drop=True), start=i_low + 1)


def _now_break_with_volume(
    close, vol, i_low: int, i_high: int, n: int, up_vol_mean: float
) -> bool:
    """撤销：回调放量破位（跌回启动位且量>=上涨段均量）。"""
    return bool(
        len(vol[i_high + 1 :])
        and up_vol_mean
        and any(
            close[t] < close[i_low] and vol[t] >= up_vol_mean
            for t in range(i_high + 1, n)
        )
    )


def _now_status(big_bear: bool, break_with_vol: bool, mild, no_big_bear, shrink) -> str:
    if big_bear or break_with_vol:
        return "revoked"
    if mild and no_big_bear and shrink:
        return "confirmed"
    return "insufficient"


def _now_conditions(
    mild, max_burst, no_big_bear, top_bears, shrink, pull_ratio
) -> dict[str, Any]:
    last = top_bears[-1] if top_bears else None
    return {
        "mild_volume": {
            "hit": bool(mild),
            "max_vol_burst": round(max_burst, 3) if max_burst is not None else None,
        },
        "no_top_big_bear": {
            "hit": bool(no_big_bear),
            "top_bear_bars_ago": last["bars_ago"] if last else None,
            "top_bear_kind": last["kind"] if last else None,
        },
        "pullback_shrink": {
            "hit": bool(shrink),
            "pullback_vol_ratio": round(pull_ratio, 3)
            if pull_ratio is not None
            else None,
        },
    }


def check_non_one_wave(df) -> dict[str, Any]:
    """非一波流确认（B1 §四）：三条件各自布尔+实际值。

    confirmed=三全；revoked=顶部放量大阴或回调放量破位；其余 insufficient。
    """
    close, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    seg = _rally_pair(df)  # v0.591 上涨段唯一定位（MACD 平台 N 结构）
    if seg is None:
        return {
            "status": "insufficient",
            "available": False,
            "conditions": {},
            "reason": "无完整上涨段+回调段",
        }
    i_low, i_high = seg

    up_vol = vol[i_low : i_high + 1]
    up_vol_mean = float(up_vol.mean()) if len(up_vol) else 0.0
    # (a) 上涨段温和放量：无单日爆量（单日量/段均量 < 2）
    max_burst = float(up_vol.max() / up_vol_mean) if up_vol_mean else None
    mild = max_burst is not None and max_burst < NOW_MILD_VOL_BURST
    # (b) 上涨段起点之后无顶部 / 次高点放量阴线（v0.592 唯一判定 distribution.top_bear_bars，owner 2026-11-24 校准口径）
    tb = _top_bears_since(df, i_low)
    big_bear = bool(tb)
    no_big_bear = not big_bear
    # (c) 回调段缩量：回调段均量/上涨段均量 < 0.7
    pull_vol = vol[i_high + 1 :]
    pull_ratio = (
        float(pull_vol.mean() / up_vol_mean) if len(pull_vol) and up_vol_mean else None
    )
    shrink = pull_ratio is not None and pull_ratio < NOW_PULLBACK_VOL_RATIO
    break_with_vol = _now_break_with_volume(close, vol, i_low, i_high, n, up_vol_mean)
    status = _now_status(big_bear, break_with_vol, mild, no_big_bear, shrink)
    return {
        "status": status,
        "available": True,
        "conditions": _now_conditions(
            mild, max_burst, no_big_bear, tb, shrink, pull_ratio
        ),
        "break_with_volume": break_with_vol,
    }


def _repair_j_turn_up(j: dict) -> tuple[bool, Any, Any]:
    """修复信号①：J 拐头向上（今日 J > 昨日 J 且昨日 J < REPAIR_J_PREV_MAX）。"""
    j_now = j.get("j") if j.get("available") else None
    j_prev = j.get("j_prev") if j.get("available") else None
    hit = bool(
        j_now is not None
        and j_prev is not None
        and j_now > j_prev
        and j_prev < REPAIR_J_PREV_MAX
    )
    return hit, j_now, j_prev


def _repair_shrink_stop(
    close, vol, n: int
) -> tuple[bool, Optional[float], Optional[float]]:
    """修复信号②：缩量止跌（量比 <= REPAIR_VOL_SHRINK 且涨跌幅在 ±REPAIR_CHANGE_PCT 内）。"""
    vol_ma5_prev = float(vol[-6:-1].mean()) if n >= 6 else None
    vol_ratio = float(vol[-1] / vol_ma5_prev) if vol_ma5_prev else None
    change = (close[-1] / close[-2] - 1) * 100 if n >= 2 and close[-2] else None
    hit = bool(
        vol_ratio is not None
        and vol_ratio <= REPAIR_VOL_SHRINK
        and change is not None
        and abs(change) <= REPAIR_CHANGE_PCT
    )
    return hit, vol_ratio, change


def _repair_rs_turn(
    close, n: int, index_df
) -> tuple[bool, Optional[float], Optional[float]]:
    """修复信号③：5日相对强度由负转正（对上证指数）。"""
    rs_turn = False
    rs5_now = rs5_prev = None
    if index_df is not None and not index_df.empty and n >= 7 and len(index_df) >= 7:
        ic = index_df["close"].astype(float).to_numpy()
        rs5_now = (close[-1] / close[-6] - 1) * 100 - (ic[-1] / ic[-6] - 1) * 100
        rs5_prev = (close[-2] / close[-7] - 1) * 100 - (ic[-2] / ic[-7] - 1) * 100
        rs_turn = bool(rs5_now >= 0 > rs5_prev)
    return rs_turn, rs5_now, rs5_prev


def check_repair_signals(
    df, index_df, kdj_state: Optional[dict] = None
) -> dict[str, Any]:
    """B1 修复信号（B1 §四.2）：输出命中数组+各信号实际值。

    kdj_state 可传调用方已算好的 kdj(df)（compute_metrics 就有一份），避免同一只票
    把日线 KDJ 算两遍；不传则自己算，结果一致。
    """
    close, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    j = kdj_state if kdj_state is not None else kdj(df)
    j_turn_up, j_now, j_prev = _repair_j_turn_up(j)
    shrink_stop, vol_ratio, change = _repair_shrink_stop(close, vol, n)
    rs_turn, rs5_now, rs5_prev = _repair_rs_turn(close, n, index_df)

    signals = []
    if j_turn_up:
        signals.append("j_turn_up")
    if shrink_stop:
        signals.append("volume_shrink_stop_fall")
    if rs_turn:
        signals.append("rs_turn_strong")
    return {
        "signals": signals,
        "detail": {
            "j_turn_up": {"hit": j_turn_up, "j": j_now, "j_prev": j_prev},
            "volume_shrink_stop_fall": {
                "hit": shrink_stop,
                "vol_ratio": round(vol_ratio, 3) if vol_ratio is not None else None,
                "change_pct": round(change, 2) if change is not None else None,
            },
            "rs_turn_strong": {
                "hit": rs_turn,
                "rs5_now_pp": round(rs5_now, 2) if rs5_now is not None else None,
                "rs5_prev_pp": round(rs5_prev, 2) if rs5_prev is not None else None,
            },
        },
    }


def check_five_day_entry(df) -> dict[str, Any]:
    """五日战法入场三条件（CZ §十六，缺一不可）。"""
    close, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    if n < 21:
        return {"hit": False, "available": False, "conditions": {}}
    ma5 = float(close[-5:].mean())
    cond1 = bool(close[-1] > ma5)
    vol_ma20 = float(vol[-20:].mean())
    cond2 = bool((vol[-1] > vol[-2] > vol[-3]) or all(v >= vol_ma20 for v in vol[-3:]))
    spike_ratios = [
        float(vol[t] / vol[t - 1])
        for t in range(max(1, n - FIVE_DAY_SPIKE_WINDOW), n)
        if vol[t - 1]
    ]
    max_spike = max(spike_ratios) if spike_ratios else None
    cond3 = bool(max_spike is not None and max_spike >= FIVE_DAY_SPIKE_RATIO)
    return {
        "hit": bool(cond1 and cond2 and cond3),
        "available": True,
        "conditions": {
            "close_above_ma5": {
                "hit": cond1,
                "close": round(float(close[-1]), 4),
                "ma5": round(ma5, 4),
            },
            "three_day_volume_up": {
                "hit": cond2,
                "vols_last3": [float(v) for v in vol[-3:]],
                "vol_ma20": round(vol_ma20, 2),
            },
            "spike_within_7d": {
                "hit": cond3,
                "max_spike_ratio": round(max_spike, 3)
                if max_spike is not None
                else None,
            },
        },
    }


# --- v2 候选定义（v0.519，owner 2026-11-13 定义校准确认；只在 calib_probe 对照，不进 detect / 打分）---
FIVE_DAY_V2_SPIKE_RATIO = 2.0  # 巨量 = 当日量 ≥ 前一天的 2 倍，且当日收阳
NOW_V2_VOL_RATIO = (
    1.5  # 「放量」统一口径：当日量 ≥ 前 5 日均量 1.5 倍（放量大阴 / 放量破位共用）
)
NOW_V2_MAX_GAIN_PCT = 50.0  # 上涨段累计涨幅 > 50% ⇒ 一波流
# 一波流结局（owner 2026-11-13 定：不能判确认，按证据不足）
NOW_V2_ONE_WAVE_STATUS = "insufficient"
NOW_V2_MIN_GAIN_PCT = 0.0  # 「明显上涨段」下限：段涨幅不足 ⇒ insufficient（无可评价的上涨段；待 owner 定）


def check_five_day_entry_v2(df) -> dict[str, Any]:
    """五日战法 v2 候选（owner 2026-11-13）：①收盘 > 5 日均价 ②最近 3 天每天「放量上涨」
    （收盘 > 前一天且量 > 前一天）③近 7 天内有一天量 ≥ 前一天 2 倍且当天收阳。"""
    close, opn, vol = (
        df[k].astype(float).to_numpy() for k in ("close", "open", "volume")
    )
    n = len(df)
    if n < 21:
        return {"hit": False, "available": False, "conditions": {}}
    ma5 = float(close[-5:].mean())
    c1 = bool(close[-1] > ma5)
    c2 = all(close[k] > close[k - 1] and vol[k] > vol[k - 1] for k in range(n - 3, n))
    spikes = [
        t
        for t in range(max(1, n - FIVE_DAY_SPIKE_WINDOW), n)
        if vol[t - 1] > 0
        and vol[t] >= FIVE_DAY_V2_SPIKE_RATIO * vol[t - 1]
        and close[t] > opn[t]
    ]
    c3 = bool(spikes)
    return {
        "hit": bool(c1 and c2 and c3),
        "available": True,
        "conditions": {
            "close_above_ma5": {
                "hit": c1,
                "close": round(float(close[-1]), 4),
                "ma5": round(ma5, 4),
            },
            "three_day_up_with_volume": {"hit": bool(c2)},
            "bullish_spike_within_7d": {
                "hit": c3,
                "spike_offsets": [t - (n - 1) for t in spikes],
            },
        },
    }


def _v2_vol_up(vol, t: int) -> bool:
    base = vol[max(0, t - 5) : t]
    return bool(
        len(base) and base.mean() > 0 and vol[t] >= NOW_V2_VOL_RATIO * base.mean()
    )


def check_non_one_wave_v2(df) -> dict[str, Any]:
    """非一波流 v2 候选（owner 2026-11-13）。相对 v1 的改动：

    - 上涨段沿用 v1 识别；v1 找不到段（价格已跌到窗口最低，「无完整上涨段」）时回退为窗口最高价日与其之前的
      最低价日——v1 在这种情形下放量破位反而判不出来；
    - 「放量」统一为当日量 ≥ 前 5 日均量 1.5 倍：放量大阴（阴线、跌 ≥3%）与放量破位（收盘跌破启动低点收盘）共用；
    - 次高点（上涨段内阶段高点之前最高的局部高点，±3 日窗口内的最高）前后 3 日的放量大阴也撤销；
    - 上涨段累计涨幅（阶段高点最高价 / 启动低点最低价 − 1）> 50% ⇒ 一波流：不能判确认，按证据不足（owner 定）；
    - 放量大阴另须量大于该高点区内最大阳线量（owner 定）。
    温和放量（单日量 < 段均量 2 倍）与回调缩量（< 0.7 倍）沿用 v1。"""
    close, opn, high, low, vol = (
        df[k].astype(float).to_numpy()
        for k in ("close", "open", "high", "low", "volume")
    )
    n = len(df)
    seg = _rally_pair(
        df
    )  # v0.591 上涨段唯一定位；🔄 原「找不到段时取窗口最高价日」兜底停用
    if seg is None:
        return {
            "status": "insufficient",
            "available": False,
            "reason": "无 MACD 平台上涨段 + 回调段",
        }
    i_low, i_high = seg
    up_vol = vol[i_low : i_high + 1]
    up_mean = float(up_vol.mean()) if len(up_vol) else 0.0
    gain = pct_change(
        float(high[i_high]), float(low[i_low]), digits=2
    )  # 段涨幅（判定用 2 位，同 TODO #56）
    if gain is None or gain < NOW_V2_MIN_GAIN_PCT:
        return {
            "status": "insufficient",
            "available": True,
            "reason": f"无明显上涨段（段涨幅 {gain}% < {NOW_V2_MIN_GAIN_PCT:g}%）",
            "rally": {
                "i_low": i_low - (n - 1),
                "i_high": i_high - (n - 1),
                "gain_pct": None if gain is None else round(gain, 2),
            },
            "revoke_reasons": [],
        }
    one_wave = gain is not None and gain > NOW_V2_MAX_GAIN_PCT
    max_burst = float(up_vol.max() / up_mean) if up_mean else None
    mild = max_burst is not None and max_burst < NOW_MILD_VOL_BURST
    tb = _top_bears_since(df, i_low)  # v0.592 顶部 / 次高点放量阴线唯一判定
    top_bear = any(b["kind"] == "顶部" for b in tb)
    second_bear = any(b["kind"] == "次高点" for b in tb)
    pull = vol[i_high + 1 :]
    pull_ratio = float(pull.mean() / up_mean) if len(pull) and up_mean else None
    shrink = pull_ratio is not None and pull_ratio < NOW_PULLBACK_VOL_RATIO
    brk = any(
        close[t] < close[i_low] and _v2_vol_up(vol, t) for t in range(i_high + 1, n)
    )
    if top_bear or second_bear or brk:
        status = "revoked"
    elif one_wave:
        status = NOW_V2_ONE_WAVE_STATUS
    elif mild and shrink:
        status = "confirmed"
    else:
        status = "insufficient"
    return {
        "status": status,
        "available": True,
        "rally": {
            "i_low": i_low - (n - 1),
            "i_high": i_high - (n - 1),
            "gain_pct": None if gain is None else round(gain, 2),
        },
        "revoke_reasons": [
            r
            for r, on in (
                ("one_wave_gain", one_wave),
                ("top_big_bear", top_bear),
                ("second_high_big_bear", second_bear),
                ("break_with_volume", brk),
            )
            if on
        ],
        "mild_volume": {
            "hit": bool(mild),
            "max_vol_burst": None if max_burst is None else round(max_burst, 3),
        },
        "pullback_shrink": {
            "hit": bool(shrink),
            "pullback_vol_ratio": None if pull_ratio is None else round(pull_ratio, 3),
        },
    }


# --- 一波流 v3 候选（owner 2026-11-17：一波流 = 短期快速大涨后下跌；不用「撤销非一波流」这种双重否定；未接 live）---
OW3_RALLY_MIN_PCT = (
    45.0  # 明显上涨：上涨段涨幅 ≥45%，不足 ⇒ 判断不了（横盘 / 单边下跌 / 左侧）
)
OW3_FAST_PCT = 50.0  # 一波流：上涨段内任一 ≤OW3_FAST_BARS 根的窗口涨幅 ≥50%
OW3_FAST_BARS = 20  # 「20 根左右」
OW3_HEAVY_PULLBACK = (
    0.9  # 一波流的「A 下」：回调不缩量 = 回调段均量 ≥ 上涨段 0.9 倍（owner 2026-11-17）
)


def _ow3_n_floor(close, i_low: int, i_high: int, df=None) -> tuple[float, int]:
    """上涨段内最后一个已完成的 MACD 平台底（v0.589 起同 check_n_structure 的平台点，唯一实现）；没有则取启动低点收盘。
    健康拉升要求高点之后收盘不跌破它（上升 N 型结构不破）。"""
    floor_i = i_low
    if df is not None:
        for sg in macd_platform_segments(df):
            if sg["kind"] == "L" and not sg["ongoing"] and i_low < sg["idx"] < i_high:
                floor_i = sg["idx"]
    return float(close[floor_i]), int(floor_i)


def check_one_wave_v3(df) -> dict[str, Any]:
    """一波流判定 v3（候选）。三种结论，正面表述：

    - ``one_wave``（一波流）：上涨段里 ``OW3_FAST_BARS`` 根内涨幅 ≥ ``OW3_FAST_PCT``%（A 上），且回调**不缩量**
      （回调段均量 ≥ 上涨段 ``OW3_HEAVY_PULLBACK`` 倍，A 下；owner 2026-11-17）；
    - ``healthy``（健康拉升）：上涨段涨幅 ≥ ``OW3_RALLY_MIN_PCT``% 但不是一波流，回调缩量（回调段均量 < 上涨段 0.7 倍）且无出货
      （owner 2026-11-17 去掉「温和放量」——大涨段几乎都有单日放量，该条使健康拉升近乎为零；``mild_volume`` 仍只作读数），
      且**上升 N 型结构不破**（高点后收盘不跌破上涨段内最后一个回踩低点，owner 2026-11-17）；
    - ``unclear``（判断不了）：没有明显上涨段，或回调不缩量 / 有出货。

    另报 ``distribution``（出货）：阶段高点或次高点前后 3 日有放量大阴（阴线、跌 ≥3%、量 ≥ 前 5 日均量 1.5 倍且大于区内最大阳线量）；
    ``break_with_volume``（放量跌破启动低点）只作标注，不改结论。上涨段识别同 v2。"""
    close, opn, high, low, vol = (
        df[k].astype(float).to_numpy()
        for k in ("close", "open", "high", "low", "volume")
    )
    n = len(df)
    seg = _rally_pair(df)  # v0.591 上涨段唯一定位
    if seg is None:
        return {
            "status": "unclear",
            "available": False,
            "reason": "无 MACD 平台上涨段 + 回调段",
        }
    i_low, i_high = seg
    gain = pct_change(float(high[i_high]), float(low[i_low]), digits=2)
    fast = None
    for t in range(i_low, i_high + 1):
        lo = float(low[max(i_low, t - OW3_FAST_BARS + 1) : t + 1].min())
        g = pct_change(float(high[t]), lo, digits=2)
        if g is not None and (fast is None or g > fast):
            fast = g
    up_vol = vol[i_low : i_high + 1]
    up_mean = float(up_vol.mean()) if len(up_vol) else 0.0
    max_burst = float(up_vol.max() / up_mean) if up_mean else None
    mild = max_burst is not None and max_burst < NOW_MILD_VOL_BURST
    pull = vol[i_high + 1 :]
    pull_ratio = float(pull.mean() / up_mean) if len(pull) and up_mean else None
    shrink = pull_ratio is not None and pull_ratio < NOW_PULLBACK_VOL_RATIO
    tb = _top_bears_since(df, i_low)  # v0.592 顶部 / 次高点放量阴线唯一判定
    top_bear = any(b["kind"] == "顶部" for b in tb)
    second_bear = any(b["kind"] == "次高点" for b in tb)
    distribution = bool(top_bear or second_bear)
    brk = any(
        close[t] < close[i_low] and _v2_vol_up(vol, t) for t in range(i_high + 1, n)
    )
    n_floor, n_floor_i = _ow3_n_floor(close, i_low, i_high, df.reset_index(drop=True))
    n_intact = (
        bool(float(close[i_high + 1 :].min()) >= n_floor) if i_high + 1 < n else True
    )
    heavy = pull_ratio is not None and pull_ratio >= OW3_HEAVY_PULLBACK
    if fast is not None and fast >= OW3_FAST_PCT and heavy:
        status = "one_wave"
    elif (
        gain is not None
        and gain >= OW3_RALLY_MIN_PCT
        and shrink  # owner 2026-11-17：去掉「温和放量」
        and n_intact  # owner 2026-11-17：N 型结构不破
        and not distribution
    ):
        status = "healthy"
    else:
        status = "unclear"
    return {
        "status": status,
        "available": True,
        "distribution": distribution,
        "break_with_volume": bool(brk),
        "n_structure": {
            "intact": n_intact,
            "floor": round(n_floor, 4),
            "floor_bars_ago": int(n - 1 - n_floor_i),
        },
        "rally": {
            "gain_pct": gain,
            "fast_gain_pct": fast,
            "up_bars": int(i_high - i_low),
            "bars_since_high": int(n - 1 - i_high),
        },
        "mild_volume": {
            "hit": bool(mild),
            "max_vol_burst": None if max_burst is None else round(max_burst, 3),
        },
        "pullback_shrink": {
            "hit": bool(shrink),
            "pullback_vol_ratio": None if pull_ratio is None else round(pull_ratio, 3),
        },
    }


def check_liquidity(df, win: int = LIQUIDITY_WIN) -> dict[str, Any]:
    """流动性：近 win 日均成交额（亿元）。仅计算值，底线判定在 score 层（可配）。"""
    if "amount" not in df.columns or len(df) < 5:
        return {"available": False}
    amt = df["amount"].astype(float).to_numpy()
    avg = float(amt[-win:].mean())
    return {
        "available": bool(avg > 0),
        "avg_amount_yi": round(avg / 1e8, 4),
        "avg_amount": round(avg, 0),
        "window": win,
    }


def _stop_ref(df, lookback: int = STOP_LOOKBACK) -> Optional[float]:
    """建议止损位：近 lookback 日最低价（根数不足 → None）。

    ``lookback`` 默认 STOP_LOOKBACK（行为逐位不变）——R41-C3 的 ±50%
    扰动通道（v0.315 口径定稿）；调用方按 as-of 切片（df.iloc[:i+1]）
    传入，本函数只读入参 df，天然无未来数据。
    """
    if len(df) < lookback:
        return None
    return round(float(df["low"].tail(lookback).min()), 4)


# ---------------------------------------------------------------------------
# 一波流 v4（owner 2026-11-23：连续、快速、大幅即一波流；之后 A 杀 / 震荡下跌 / 横盘都可能，不看回调）
# ---------------------------------------------------------------------------
OW4_BARS = 120  # 只看图上显示的 120 根
OW4_FAST_BARS = 10  # 快速：≤10 根（「几个交易日内」）
OW4_RISE_PCT = 50.0  # 大幅：最高价相对起点最低价涨 ≥50%
OW4_MAX_PULLBACK = 0.10  # 连续：拉升途中收盘较途中最高收盘回撤 <10%，否则视为断开


def check_one_wave_v4(df) -> dict[str, Any]:
    """一波流 v4：末 ``OW4_BARS`` 根里存在一段 ≤``OW4_FAST_BARS`` 根、途中收盘回撤 <``OW4_MAX_PULLBACK``、
    最高价较起点最低价涨 ≥``OW4_RISE_PCT``% 的拉升 ⇒ hit。只作提醒（live 风险列「一波流（提醒）」），尽力识别。
    参数来源：owner 两份「一波流 有 / 无」标注 96 张（有 19）：检出 12/19、准确 12/29（v3 为 7/19、7/16）。"""
    if df is None or len(df) < OW4_FAST_BARS + 1:
        return {"available": False, "hit": False}
    t = df.tail(OW4_BARS)
    c, hi, lo = (t[k].astype(float).to_numpy() for k in ("close", "high", "low"))
    n = len(c)
    best: tuple[float, Optional[int], Optional[int]] = (0.0, None, None)
    for i in range(n):
        run = c[i]
        for j in range(i + 1, min(n, i + OW4_FAST_BARS + 1)):
            run = max(run, c[j])
            if c[j] < run * (1 - OW4_MAX_PULLBACK):
                break
            if lo[i] > 0 and (g := (hi[j] / lo[i] - 1) * 100) > best[0]:
                best = (g, i, j)
    g, i0, j0 = best
    return {
        "available": True,
        "hit": bool(g >= OW4_RISE_PCT),
        "rise_pct": round(float(g), 2),
        "start_bars_ago": None if i0 is None else int(n - 1 - i0),
        "peak_bars_ago": None if j0 is None else int(n - 1 - j0),
    }


# ---------------------------------------------------------------------------
# MACD 平台 N 结构（owner 2026-11-10 通达信公式转译，v0.507 / v0.512；2026-11-25 定为**全项目走势判断的唯一底座**）
#   红柱段内阴线最高开盘 = 平台顶 H，绿柱段内阴线最低收盘 = 平台底 L；N 结构抬高 = 上涨、横盘 = 横盘震荡、降低 = 下跌。
#   走势三态（check_swing_state）、持仓上升 N（check_n_structure）、下降 N（check_descending_n_structure）、
#   technical_monitor.trend_state 全部由这里推出。🔄 v0.589 停用：走势三态 v4 两段直线拟合（SW4_*）、
#   technical_monitor 的均线斜率 + 20 日高低点三态、收盘 3 根分型 N 结构（N_PIVOT_*）。
# ---------------------------------------------------------------------------
N_LOOKBACK = 90  # 持仓结构位（上升 / 下降 N）只在近 90 根的已完成平台点里找
N_STALE_BREACH_BARS = 10  # 跌破关键位超过 10 根视为陈旧结构（不再触发新鲜 P0）
_N_FULL = {"上升": "震荡上涨", "下降": "震荡下跌", "横盘震荡": "横盘震荡"}
_N_SHORT = {"上升": "上涨", "下降": "下跌", "横盘震荡": "横盘"}


def _n_date(x, i: Optional[int]) -> Optional[str]:
    if i is None or "date" not in x.columns:
        return None
    return str(x["date"].iloc[int(i)])[:10]


def macd_platform_segments(df) -> list[dict[str, Any]]:
    """MACD 红 / 绿柱分段的平台点（**候选定义**，v0.507；owner 2026-11-10 通达信公式转译）。

    - 分段：连续 ``MACD>0``（红柱）/ ``MACD<0``（绿柱）为一段（MACD = indicators.macd_series 的中式 ×2 柱）；
      ``MACD==0`` 的根不属任何段。不设最短段长（owner：按公式）。
    - 红柱段平台顶 = 段内**阴线**（收 < 开）开盘价的最高值；绿柱段平台底 = 段内阴线收盘价的最低值；
      段内无阴线 ⇒ 该段跳过（owner：跳过）。
    - as-of：只用截至末根的数据；末段（进行中）的平台值随新 K 线更新，标 ``ongoing``。
    返回按时间顺序的 ``[{kind: "H"|"L", price, idx, date, start, end, ongoing}]``。"""
    if df is None or len(df) < 2:
        return []
    x = df.reset_index(drop=True)
    o = x["open"].astype(float).to_numpy()
    c = x["close"].astype(float).to_numpy()
    _dif, _dea, hist = macd_series(x["close"])
    h = hist.to_numpy()
    n = len(x)
    out: list[dict[str, Any]] = []
    i = 0
    while i < n:
        sgn = 1 if h[i] > 0 else (-1 if h[i] < 0 else 0)
        j = i
        while j + 1 < n and (1 if h[j + 1] > 0 else (-1 if h[j + 1] < 0 else 0)) == sgn:
            j += 1
        if sgn != 0:
            bears = [k for k in range(i, j + 1) if c[k] < o[k]]
            if bears:
                if sgn > 0:
                    k = max(bears, key=lambda t: (o[t], t))
                    price = float(o[k])
                else:
                    k = min(bears, key=lambda t: (c[t], -t))
                    price = float(c[k])
                out.append(
                    {
                        "kind": "H" if sgn > 0 else "L",
                        "price": round(price, 4),
                        "idx": int(k),
                        "date": _n_date(x, k),
                        "start": _n_date(x, i),
                        "end": _n_date(x, j),
                        "start_i": int(i),
                        "end_i": int(j),
                        "ongoing": bool(j == n - 1),
                    }
                )
        i = j + 1
    return out


def macd_platform_points(
    segs: list[dict[str, Any]], groups: int = 3
) -> list[dict[str, Any]]:
    """往回交替匹配平台点（v0.512，owner 2026-11-11 定：从当根往回、B2、同类合并取极值）。

    从最新的平台点（含进行中末段）往回交替收 L / H；连续同类（中间段无阴线被跳过）合并为一个——
    L 取更低、H 取更高（并列取较新者）；凑满 ``groups`` 个 L 与 ``groups`` 个 H 即停。
    返回按时间**从新到旧**的代表点列表（字段同 ``macd_platform_segments``）。"""
    seq: list[dict[str, Any]] = []
    for sg in reversed(segs):
        if seq and seq[-1]["kind"] == sg["kind"]:
            cur = seq[-1]
            if (sg["kind"] == "L" and sg["price"] < cur["price"]) or (
                sg["kind"] == "H" and sg["price"] > cur["price"]
            ):
                seq[-1] = sg
            continue
        n_l = sum(1 for x in seq if x["kind"] == "L")
        n_h = len(seq) - n_l
        if n_l >= groups and n_h >= groups:
            break
        seq.append(sg)
    return seq


def _platform_state(l_a: float, l_b: float, h_a: float, h_b: float) -> str:
    if l_b > l_a and h_b > h_a:
        return "上升"
    if l_b < l_a and h_b < h_a:
        return "下降"
    return "横盘震荡"


def macd_platform_structure(df) -> dict[str, Any]:
    """MACD 平台结构：往回匹配三组平台点判结构（v0.589 起为全项目走势判断的唯一底座）。

    v0.512（owner 2026-11-11 对图确认「可以对上」）：从当根往回交替匹配 L / H（``macd_platform_points``，
    同类相邻合并取极值），凑满三组，标号 L1/H1 最早、L3/H3 最新。**最近两组定状态**：L3>L2 且 H3>H2 ⇒ 上升，
    反之 ⇒ 下降，其余 ⇒ 横盘震荡；**第三组判延续 / 转折**：前一状态 = 同规则作用于 (L1→L2, H1→H2)，
    ``transition`` = 「延续」或「由<前一状态>转」。（v0.507 原定义 = L、H 各自取最近两个，已被本版取代。）
    上升结构里收盘 < L3 ⇒ ``breached``；下降结构里收盘 > H3 ⇒ ``reclaimed``；上升 N 的「确认」= L3 之后
    收盘站上 L3 之前最近的平台顶。平台点含进行中的末段（as-of 当时值）。不足三组 ⇒ ``available=False``。"""
    segs = macd_platform_segments(df)
    pts = macd_platform_points(segs, 3)
    lows = [x for x in pts if x["kind"] == "L"][:3][::-1]  # 旧 → 新
    highs = [x for x in pts if x["kind"] == "H"][:3][::-1]
    if len(lows) < 3 or len(highs) < 3:
        return {
            "available": False,
            "reason": "MACD 平台底 / 顶不足三组",
            "n_lows": len(lows),
            "n_highs": len(highs),
        }
    l1, l2, l3 = lows
    h1, h2, h3 = highs
    state = _platform_state(l2["price"], l3["price"], h2["price"], h3["price"])
    prior = _platform_state(l1["price"], l2["price"], h1["price"], h2["price"])
    x = df.reset_index(drop=True)
    close = x["close"].astype(float).to_numpy()
    cur = float(close[-1])
    prior_h = [sg for sg in segs if sg["kind"] == "H" and sg["idx"] < l3["idx"]]
    confirmed = bool(
        prior_h
        and any(
            close[k] > prior_h[-1]["price"] for k in range(l3["idx"] + 1, len(close))
        )
    )
    return {
        "available": True,
        "state": state,
        "prior_state": prior,
        "transition": "延续" if state == prior else f"由{prior}转",
        "L1": l1,
        "L2": l2,
        "L3": l3,
        "H1": h1,
        "H2": h2,
        "H3": h3,
        "current_close": round(cur, 4),
        "confirmed": confirmed if state == "上升" else None,
        "breached": bool(state == "上升" and cur < l3["price"]),
        "reclaimed": bool(state == "下降" and cur > h3["price"]),
        "definition": "macd_platform_v2（往回交替匹配三组，同类合并取极值；最近两组定状态，第三组判延续 / 转折）",
    }


def rally_segment(df) -> Optional[dict[str, Any]]:
    """上涨段（**全项目唯一定位**，v0.591，owner 2026-11-25「所有波段识别都建立在 N 型结构上」）：
    最近一个平台顶 H 与它前面的平台底 L 组成上涨段（含进行中的末段）。
    起点 ``i_low`` = 从 L 之前那个平台顶（没有则 L 前 60 根）到 H 之间的最低价；高点 ``i_high`` = 从 ``i_low`` 到 H 之后那个平台底
    （没有则当根）之间的最高价；``i_high`` 之后到当根 = 回调段。找不到「L → H」⇒ None。
    🔄 停用：wave_type 的「近 60 根最低价 → 其后最高价」、回调缩量 v1 的「近 20 根最高收盘」、v2 的「近 30 根最高 / 前 40 根最低」。"""
    if df is None or len(df) < 30:
        return None
    x = df.reset_index(drop=True)
    hi = x["high"].astype(float).to_numpy()
    lo = x["low"].astype(float).to_numpy()
    n = len(x)
    seq = macd_platform_points(macd_platform_segments(x), groups=10**9)[::-1]  # 旧 → 新
    jh = next(
        (
            j
            for j in range(len(seq) - 1, 0, -1)
            if seq[j]["kind"] == "H" and seq[j - 1]["kind"] == "L"
        ),
        None,
    )
    if jh is None:
        return None
    left = int(seq[jh - 2]["idx"]) if jh >= 2 else max(0, int(seq[jh - 1]["idx"]) - 60)
    h_idx = int(seq[jh]["idx"])
    right = int(seq[jh + 1]["idx"]) if jh + 1 < len(seq) else n - 1
    i_low = left + int(lo[left : h_idx + 1].argmin())
    i_high = i_low + int(hi[i_low : max(right, h_idx) + 1].argmax())
    if i_high <= i_low:
        return None
    return {
        "start": left,
        "i_low": i_low,
        "i_high": i_high,
        "n": n,
        "pullback_bars": n - 1 - i_high,
        "basis": "macd_platform",
    }


def _rally_pair(df) -> Optional[tuple[int, int]]:
    """rally_segment 的 (i_low, i_high)，并要求高点之后至少 2 根回调；否则 None。"""
    seg = rally_segment(df)
    if seg is None or seg["i_high"] >= seg["n"] - 2:
        return None
    return int(seg["i_low"]), int(seg["i_high"])


def check_swing_state(df) -> dict[str, Any]:
    """走势三态（v0.589 起由 MACD 平台 N 结构推出，owner 2026-11-25）：
    最近两组平台点定当前态（上升 ⇒ 震荡上涨、下降 ⇒ 震荡下跌、其余 ⇒ 横盘震荡）；第三组定前一态，
    前后不同 ⇒「X 转 Y」（上涨 / 横盘 / 下跌）。``now`` = 当前态全称；``turn_bars_ago`` = 转折（第二组平台点）距当根根数。
    🔄 v4（v0.557~v0.585，末 120 根两段直线拟合，SW4_*）停用。"""
    r = (
        macd_platform_structure(df)
        if df is not None and len(df) >= 2
        else {"available": False}
    )
    if not r.get("available"):
        return {
            "available": False,
            "state": None,
            "now": None,
            "turn_bars_ago": None,
            "basis": "macd_platform",
        }
    st, pr = str(r["state"]), str(r["prior_state"])
    l2: Any = r["L2"]
    h2: Any = r["H2"]
    n = len(df)
    if st == pr:
        return {
            "available": True,
            "state": _N_FULL[st],
            "now": _N_FULL[st],
            "turn_bars_ago": None,
            "basis": "macd_platform",
        }
    k = min(int(l2["idx"]), int(h2["idx"]))
    return {
        "available": True,
        "state": f"{_N_SHORT[pr]}转{_N_SHORT[st]}",
        "now": _N_FULL[st],
        "turn_bars_ago": int(n - 1 - k),
        "basis": "macd_platform",
    }


def price_zone(df) -> Optional[str]:
    """价格区间（**全项目唯一判定**，v0.593，owner 2026-11-24「走势三态与 N 型结构位置配合」）：
    走势三态最后一段是上涨 ⇒ ``"top"``（顶部区间）；有下跌段且最后一段不是上涨 ⇒ ``"bottom"``（下跌之后的阶段底部）；其余 ⇒ None。"""
    st = check_swing_state(df).get("state") or ""
    if st.endswith("上涨"):
        return "top"
    if "下跌" in st:
        return "bottom"
    return None


def _n_completed_seq(df) -> list[dict[str, Any]]:
    """近 N_LOOKBACK 根内**已完成**平台点（进行中末段不算：其平台值随价格移动，会把破位改写成新低点），
    同类相邻合并取极值（同 macd_platform_points），按时间旧 → 新。"""
    n = len(df)
    segs = [
        s
        for s in macd_platform_segments(df)
        if not s["ongoing"] and s["idx"] >= n - N_LOOKBACK
    ]
    return macd_platform_points(segs, groups=10**9)[::-1]


def _n_breach(
    close, anchor: int, level: float
) -> tuple[Optional[int], Optional[int], bool, bool]:
    """anchor 之后首次收盘跌破 level 的新鲜度 → (first_breach, breach_bars_ago, currently_breached, stale)。"""
    n = len(close)
    first = next((t for t in range(anchor + 1, n) if close[t] < level), None)
    ago = (n - 1 - first) if first is not None else None
    cur = bool(close[-1] < level)
    return first, ago, cur, bool(cur and ago is not None and ago > N_STALE_BREACH_BARS)


def check_n_structure(df) -> dict[str, Any]:
    """上升 N（持仓结构位，v0.589 起由 MACD 平台点推出）：最近一组「平台底 L1 → 平台顶 H1 → 更高的平台底 L2」。
    L1 = 主结构前低（跌破 ⇒ P0 清仓评估），L2 = 回踩低点（跌破 ⇒ P1）；L2 之后收盘站上 H1 ⇒ confirmed。
    字段同 v0.585 前的收盘分型版（下游持仓状态机不改）。"""
    if df is None or len(df) < 30:
        return {"available": False, "reason": "K线数量不足以确认N型结构"}
    x = df.reset_index(drop=True)
    close = x["close"].astype(float).to_numpy()
    low = x["low"].astype(float).to_numpy()
    seq = _n_completed_seq(x)
    hit = None
    for j in range(len(seq) - 1, 1, -1):
        if (
            seq[j]["kind"] == "L"
            and seq[j - 1]["kind"] == "H"
            and seq[j - 2]["kind"] == "L"
            and seq[j]["price"] > seq[j - 2]["price"]
        ):
            hit = (seq[j - 2], seq[j - 1], seq[j])
            break
    if hit is None:
        return {
            "available": False,
            "reason": "未发现已完成的上升N型结构（MACD 平台点）",
        }
    p1, ph, p2 = hit
    l1, h1, l2 = p1["idx"], ph["idx"], p2["idx"]
    origin_low, swing_high, pullback_low = p1["price"], ph["price"], p2["price"]
    breakout = next(
        (t for t in range(l2 + 1, len(close)) if close[t] > swing_high), None
    )
    cur = float(close[-1])
    first, ago, breached, stale = _n_breach(close, l2, origin_low)
    return {
        "available": True,
        "pattern": "L1-H1-higher_L2"
        + ("-breakout" if breakout is not None else "-candidate"),
        "status": "confirmed" if breakout is not None else "candidate",
        "prior_low": round(origin_low, 4),
        "prior_low_date": _n_date(x, l1),
        "origin_extreme_low": round(float(low[l1]), 4),
        "breakout_level": round(swing_high, 4),
        "breakout_level_date": _n_date(x, h1),
        "pullback_low": round(pullback_low, 4),
        "pullback_low_date": _n_date(x, l2),
        "confirmed_date": _n_date(x, breakout),
        "current_close": round(cur, 4),
        "distance_pct": round((cur / origin_low - 1) * 100, 4) if origin_low else None,
        "close_above": bool(cur >= origin_low),
        "breached_on_close": breached,
        "pullback_breached_on_close": bool(cur < pullback_low),
        "breach_bars_ago": ago,
        "first_breach_date": _n_date(x, first),
        "stale": stale,
        "fresh_breach": bool(breached and not stale),
        "basis": "macd_platform",
        "lookback": N_LOOKBACK,
    }


def check_descending_n_structure(df) -> dict[str, Any]:
    """下降 N（v0.589 起由 MACD 平台点推出）：最近一组「平台顶 H1 → 平台底 L1 → 更低的平台顶 H2」；
    收盘跌破 L1 ⇒ confirmed（P0）。字段同 v0.585 前的收盘分型版。"""
    if df is None or len(df) < 30:
        return {"available": False, "reason": "K线数量不足以确认下降N型结构"}
    x = df.reset_index(drop=True)
    close = x["close"].astype(float).to_numpy()
    high = x["high"].astype(float).to_numpy()
    seq = _n_completed_seq(x)
    hit = None
    for j in range(len(seq) - 1, 1, -1):
        if (
            seq[j]["kind"] == "H"
            and seq[j - 1]["kind"] == "L"
            and seq[j - 2]["kind"] == "H"
            and seq[j]["price"] < seq[j - 2]["price"]
        ):
            hit = (seq[j - 2], seq[j - 1], seq[j])
            break
    if hit is None:
        return {
            "available": False,
            "reason": "未发现已完成的下降N型结构（MACD 平台点）",
        }
    p1, pl, p2 = hit
    h1, l1, h2 = p1["idx"], pl["idx"], p2["idx"]
    cur = float(close[-1])
    structural_low = pl["price"]
    confirmed = bool(cur < structural_low)
    first, ago, _b, stale = _n_breach(close, h2, structural_low)
    return {
        "available": True,
        "pattern": "H1-L1-lower_H2" + ("-confirmed" if confirmed else "-candidate"),
        "status": "confirmed" if confirmed else "candidate",
        "prior_high": round(p1["price"], 4),
        "prior_high_date": _n_date(x, h1),
        "origin_extreme_high": round(float(high[h1]), 4),
        "structural_low": round(structural_low, 4),
        "structural_low_date": _n_date(x, l1),
        "lower_high": round(p2["price"], 4),
        "lower_high_date": _n_date(x, h2),
        "current_close": round(cur, 4),
        "distance_to_structural_low_pct": round((cur / structural_low - 1) * 100, 4)
        if structural_low
        else None,
        "below_structural_low": confirmed,
        "breach_bars_ago": ago,
        "first_breach_date": _n_date(x, first),
        "stale": stale,
        "fresh_breach": bool(confirmed and not stale),
        "basis": "macd_platform",
        "lookback": N_LOOKBACK,
    }


def detect(df) -> dict[str, Any]:
    """规范入口（v0.223，TODO #67 B4）：结构三检测器打包门面。

    返回 {non_one_wave, five_day_entry, liquidity, one_wave_v3, one_wave_v4, swing_state} —— 同函数同输入逐位一致。
    v0.561（owner 2026-11-23）：一波流提醒改用 v4；走势三态 v4 进候选表证据列（只展示）。
    v0.555（owner 2026-11-19）：one_wave_v3 进 live，**只作提醒**（候选表风险列标「一波流（提醒）」，不否决、不加减分）。
    repair_signals 吃 index_df/kdj_state 上下文，不进门面（设计稿勘误）；
    enrich_candidates 改为一次 detect 读字段。
    """
    return {
        "non_one_wave": check_non_one_wave(df),
        "five_day_entry": check_five_day_entry(df),
        "liquidity": check_liquidity(df),
        "one_wave_v3": check_one_wave_v3(df),
        "one_wave_v4": check_one_wave_v4(df),
        "swing_state": check_swing_state(df),
    }
