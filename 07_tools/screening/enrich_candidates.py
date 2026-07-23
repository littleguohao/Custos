# -*- coding: utf-8 -*-
"""Screening 链第 2 段：命中股充实 + 模式识别（enrich_candidates）。

对公式初筛命中股（去重后通常几十只）用本地日线（vipdoc，mootdx Reader）
计算确定性指标并打模式标签；每个标签对应的实际数值一并落盘，可复盘。

指标与标签（全部为确定性规则）：
- BBI=(MA3+MA6+MA12+MA24)/4，bbi_above：收盘价 >= BBI。
- 日 J（KDJ 9,3,3），j_low：J < 13。
- 量比=当日量/前5日均量；20日量分位=当日量在近20日量中的百分位。
  volume_contraction：量比 <= 50% 且 20日量分位 <= 10%。
- 20日相对强度=个股20日涨幅 - 上证指数(999999)20日涨幅（百分点）。
  relative_strength_strong：相对强度 >= +3pp。
- reversal_k_candidate：j_low + volume_contraction + 涨跌幅∈[-2%,+2%]
  + 振幅<=7%，四项同时满足。

硬排除：名称含 ST、停牌（无当日K线）、上市不足 min_list_days 天、
risk_decision 高优先级股、北交所（exclude_bj）。已持仓股打 is_holding
标记但不剔除。

CLI::

    uv run python 07_tools/screening/enrich_candidates.py --date YYYY-MM-DD

输出 ``01_data/screening/{date}_candidates_enriched.json``。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
for p in (TOOLS_DIR, TOOLS_DIR / "local_tdx", TOOLS_DIR / "market_timing"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from paths import DATA, RISK_DIR, SECTORS_DIR, TRADES_DIR  # noqa: E402
import concept_tags  # noqa: E402
import local_tdx_data  # noqa: E402
from technical_monitor import bbi_state, ema, kdj, macd, resample, zhixing_state, _infer_price_limit  # noqa: E402

SCREENING_DIR = DATA / "screening"
SECTOR_CODE_MAP = SECTORS_DIR / "sector_code_map.json"
INDEX_CODE = "999999"  # 上证指数 vipdoc 代码（reader.daily 里 000001 是平安银行）

J_LOW_THRESHOLD = 13.0
VOL_RATIO_MAX = 0.5          # 量比 <= 50%
VOL_PCTILE_MAX = 10.0        # 20日量分位 <= 10%
RS_STRONG_PP = 3.0           # 20日相对强度 >= +3pp
REVERSAL_CHANGE_PCT = 2.0
REVERSAL_AMPLITUDE_PCT = 7.0
STOP_LOOKBACK = 10           # 建议止损位：近10日最低价

# 概念标签命中主题所需的最小标签数。默认 1＝历史行为（命中1个语义标签即归入该主题）；
# 提高到 2+ 可要求更强证据、降低子串过度匹配，可经 registry.theme_mapping.min_match 覆盖。
THEME_MIN_MATCH = 1

# --- B1/CZ 策略对齐参数 -------------------------------------------------
# 以下阈值全部标注"待回测参数"：策略原文（B1 §四、CZ §九/§14.6/§十六）
# 要求阈值可配置、实际值随候选落盘，不得静默使用；完成样本回测前不得
# 视为已校准。口径出处见 00_governance/SCREENING_WORKFLOW.md "策略对齐"章。
WAVE_LOOKBACK = 60                  # 拉升波分析窗口（日）
WAVE_MIN_BARS = 40                  # 拉升波分类最少K线数
WAVE_LIMIT_UP_PCT = 9.8             # 待回测参数：涨停/接近涨停判定（单日涨幅%）
WAVE_SPRINT_WINDOW = 20             # 待回测参数：冲刺波涨停统计窗口（日）
WAVE_SPRINT_MIN_LIMIT_UPS = 2       # 待回测参数：冲刺波涨停次数下限
WAVE_ACCEL_10D_GAIN = 25.0          # 待回测参数：高斜率加速（拉升段内最大10日涨幅%下限，i_low→i_high 段上计算）
WAVE_TOP_VOL_RATIO = 1.5            # 待回测参数：顶部放量（高点日量/前5日均量）
WAVE_BUILDUP_GAIN = (25.0, 50.0)    # 建仓波段涨幅%（B1 §四.0 口径）
WAVE_RALLY_GAIN = (35.0, 50.0)      # 拉升波段涨幅%（B1 §四.0 口径）
WAVE_START_CANDLE_PCT = 5.0         # 待回测参数：启动段长阳单日涨幅%
WAVE_START_CANDLE_VOL = 1.5         # 待回测参数：启动段放量倍数
WAVE_SECOND_START_GAIN = 15.0       # 待回测参数：二次启动（前一段摆动幅度%下限）

NOW_MILD_VOL_BURST = 2.0            # 待回测参数：上涨段单日量/段均量上限（温和放量）
NOW_BEAR_DROP_PCT = -3.0            # 待回测参数：放量大阴跌幅%
NOW_BEAR_VOL_RATIO = 1.5            # 待回测参数：放量大阴量比（量/前5日均量）
NOW_PULLBACK_VOL_RATIO = 0.7        # 待回测参数：回调段均量/上涨段均量上限
NOW_TOP_ZONE = 3                    # 待回测参数：阶段高点观察区±N日

REPAIR_J_PREV_MAX = 20.0            # 待回测参数：J拐头向上（昨日J上限）
REPAIR_VOL_SHRINK = 0.7             # 待回测参数：缩量止跌量比上限
REPAIR_CHANGE_PCT = 2.0             # 待回测参数：止跌涨跌幅区间±%

FIVE_DAY_SPIKE_RATIO = 1.45         # 五日战法：近7日巨量倍数（CZ §十六）。原文"前一交易日均量"存歧义，按前一交易日单日量实现（vol[t]/vol[t-1]），待策略 owner 确认
FIVE_DAY_SPIKE_WINDOW = 7           # 五日战法：巨量观察窗口（CZ §十六）
VOLUME_SUSTAIN_WINDOW = 13          # 量能持续性窗口（CZ §14.6：7-13日）
VOLUME_SUSTAIN_MIN_POST_DAYS = 7    # 待回测参数：峰值日后确认主线最少观察日数
VOLUME_SUSTAIN_RATIO = 0.55         # 峰值55%（CZ §14.6）
VOLUME_SUSTAIN_RETREAT_DAYS = 3     # 连续N日<峰值55%判撤退（CZ §14.6）
LEADER_VOL_BASE_DAYS = 20           # 龙头量能基准窗口（CZ §九）
LEADER_VOL_RATIO = 1.7              # 地量1.7倍（CZ §九）
THREE_LOWS_DRAWDOWN_PCT = 40.0      # 待回测参数：三低之低价格（自250日高点回撤%）
THREE_LOWS_VOL_RATIO = 0.3          # 待回测参数：三低之低量（<250日均量×30%）
BOTTOM_VOL_RATIO = 2.0              # 待回测参数：底部巨量（≥250日均量×2，CZ §14.6）
BOTTOM_NO_NEW_LOW_DAYS = 20         # 待回测参数：不再创新低观察窗口
CZ_MIN_BARS = 250                   # CZ 三低/底部巨量最少K线数（不足→available=false）

# --- 知行量价（good_b1 图集）与出货五方式 待回测参数 ---
ZX_CROSS_RECENT = 10                # 待回测：知行金叉"近N日"窗口
IGNITION_WINDOW = 10                # 待回测：放量点火扫描窗口（日）
IGNITION_VOL_RATIO = 1.5            # 待回测：点火量比（当日量/前5日均量）
IGNITION_MIN_GAIN = 3.0            # 待回测：点火单日涨幅%下限
PULLBACK_LOOKBACK = 20             # 待回测：回调缩量企稳观察窗口（日）
PULLBACK_MIN_DROP = 3.0           # 待回测：距窗口高点回撤%下限
PULLBACK_VOL_RATIO = 0.8         # 待回测：回调段/上涨段均量上限

DIST_RECENT = 5                    # 待回测：出货形态观察最近N根
DIST_ACCEL_WIN = 10               # 待回测：加速涨幅窗口（日）
DIST_ACCEL_GAIN = 25.0           # 待回测：加速涨幅%下限（阴线前）
DIST_BIG_BEAR_FRAC = 0.5        # 待回测：大阴=跌幅≥涨跌幅制度×0.5
DIST_LONG_BEAR_FRAC = 0.8      # 待回测：长阴/近跌停=跌幅≥涨跌幅制度×0.8
DIST_HUGE_VOL_RATIO = 2.0      # 待回测：天量/巨量=≥20日均量×2
DIST_HUGE_VOL_WIN = 20         # 待回测：天量对比窗口（日）
DIST_STAIR_MIN_BARS = 3        # 待回测：阶梯放量阴线最少连续根数
DIST_STAIR_BREAK_VR = 1.2      # 待回测：放量跌破QSX的量比下限
DIST_TOP_WINDOW = 10           # 待回测：顶部区间（绿肥红瘦/双头）窗口
DIST_DOUBLE_TOP_TOL = 3.0      # 待回测：双头两顶相近容差%
DIST_SUBHIGH_SHRINK = 0.9      # 待回测：次高前一日缩量量比上限
DIST_MIN_VOL_MA20_FRAC = 0.05  # 待回测：vol_ma20 低于全序列均量×此比例时视为近零（派发检测器 available=False）

# --- 完美 B1 图形贴合度（good_b1 图集共性特征的梯度评分）待回测参数 ---
# 2026-07-22 用户决策：J<13 为全通道硬门槛（公式与自选池一视同仁），
# 在 J<13 基础上按贴合度梯度给分，越符合完美图形分数越高。
J_GATE_REQUIRED_DEFAULT = True    # J<13 硬门槛默认开（registry universe.j_low_required 可覆盖）
FIT_J_DEEP = 0.0                  # J<0 → 2 分
FIT_J_MID = 7.0                   # J<7 → 1.5 分；J<13 → 1 分
FIT_NEAR_LINE_PCT = 3.0           # 收盘距 QSX 或 DKS ≤3% → 2 分（回踩贴线）
FIT_NEAR_LINE_MAX_PCT = 6.0       # ≤6% → 1 分
FIT_SHRINK_DEEP = 0.5             # 回调段/上涨段均量 ≤0.5 → 2 分
FIT_SHRINK_MID = 0.8              # ≤0.8 → 1 分
FIT_DKS_SLOPE_DAYS = 5            # DKS 上行判断窗口（DKS[t] > DKS[t-N]）

# --- MACD 十大技术（macd十大技术精讲）待回测参数 ---
MACD_SWING_FRACTAL = 2           # 摆动高/低点分型：左右各 N 根确认
MACD_DIV_LOOKBACK = 60           # 背离观察窗口（日）
MACD_OVEREXT_PCTL = 0.9          # 开口/空间拐离：|DIF| 近 120 日分位上限
MACD_OVEREXT_WIN = 120           # 拐离分位窗口（日）


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _append_reason(existing: str, new: str) -> str:
    """把多条降级原因用 ';' 累积拼接，避免后写覆盖先写。"""
    return f"{existing};{new}" if existing else new


def load_hits(date: str) -> dict:
    return _load_json(SCREENING_DIR / f"{date}_formula_hits.json", {})


def load_risk_high_codes(date: str) -> set[str]:
    data = _load_json(RISK_DIR / f"{date}_risk_decision.json", {})
    out = set()
    for x in (data.get("stock_risks") or []):
        if str(x.get("priority", "")) == "高" and x.get("code"):
            out.add(str(x["code"]).split(".")[0].zfill(6))
    return out


def load_holding_codes() -> set[str]:
    data = _load_json(TRADES_DIR / "current_positions.json", [])
    out = set()
    for x in data if isinstance(data, list) else []:
        code = str(x.get("代码", "") or "").split(".")[0]
        if code.isdigit():
            out.add(code.zfill(6))
    return out


def latest_tq_sector_map() -> dict:
    """加载最新的 01_data/sectors/*_tq_sector_map.json（880板块→成分股）。"""
    files = sorted(glob.glob(str(SECTORS_DIR / "*_tq_sector_map.json")))
    if not files:
        return {}
    return _load_json(Path(files[-1]), {})


def _match_theme_tags(stock_tags: list[str], semantic_tags: list[str]) -> list[str]:
    """个股概念标签与主题语义标签的命中列表（双向子串，长的单向≥3字）。"""
    matched = []
    for st in semantic_tags:
        for c in stock_tags:
            if st == c or st in c or (len(c) >= 3 and c in st):
                matched.append(st)
                break
    return matched


def build_stock_theme_map(min_match: int = THEME_MIN_MATCH) -> tuple[dict[str, dict], bool]:
    """股 → 主题方向（theme_id/sector 名）。

    优先用 miscinfo 概念标签（concept_tags，每股官方概念）匹配
    sector_code_map.json 各主题的 semantic_tags——准确度远高于 880 反查；
    标签文件缺失时回退 tq_sector_map 成分股反查（v1，已知存在错配）。
    min_match：概念路径下命中主题所需的最小语义标签数（默认 1；提高可降低过度匹配）。
    返回 ({code6: {"theme_id","sector",...}}, map_available)。
    """
    try:
        min_match = max(1, int(min_match))
    except (TypeError, ValueError):
        min_match = THEME_MIN_MATCH
    code_map = _load_json(SECTOR_CODE_MAP, {})
    themes = code_map.get("themes") or []
    if not themes:
        return {}, False

    tags_map = concept_tags.load_tags()
    if tags_map:
        stock_theme: dict[str, dict] = {}
        for code6, stock_tags in tags_map.items():
            best_matched: list[str] = []
            best_theme: dict | None = None
            for t in themes:
                matched = _match_theme_tags(stock_tags, t.get("semantic_tags") or [])
                if len(matched) > len(best_matched):
                    best_matched, best_theme = matched, t
            if best_theme and len(best_matched) >= min_match:
                stock_theme[code6] = {
                    "theme_id": best_theme.get("theme_id", ""),
                    "sector": best_theme.get("theme_name", ""),
                    "matched_tags": best_matched,
                    "match_count": len(best_matched),
                    "sector_source": "concept_tags",
                }
        if stock_theme:
            return stock_theme, True

    sector_map = latest_tq_sector_map()
    if not sector_map.get("sectors"):
        return {}, False

    # 880 板块代码 → 主题（primary 优先于 candidate，按注册顺序取先命中者）
    code_to_theme: dict[str, dict] = {}
    for t in themes:
        theme = {"theme_id": t.get("theme_id", ""), "sector": t.get("theme_name", "")}
        for c in t.get("candidate_sector_codes") or []:
            code_to_theme.setdefault(str(c).upper(), theme)
    for t in themes:
        theme = {"theme_id": t.get("theme_id", ""), "sector": t.get("theme_name", "")}
        for c in t.get("primary_sector_codes") or []:
            code_to_theme[str(c).upper()] = theme

    stock_theme = {}
    for s in sector_map["sectors"]:
        theme = code_to_theme.get(str(s.get("code", "")).upper())
        if not theme:
            continue
        for raw in s.get("stocks") or []:
            code6 = str(raw).split(".")[0].zfill(6)
            stock_theme.setdefault(code6, {**theme, "matched_code": s.get("code", ""),
                                           "sector_source": "tq_880_fallback"})
    return stock_theme, True


def _pct_change(df, n: int) -> Optional[float]:
    if len(df) < n + 1:
        return None
    prev = float(df["close"].iloc[-n - 1])
    now = float(df["close"].iloc[-1])
    if prev == 0:
        return None
    return (now / prev - 1) * 100


# ========== B1/CZ 策略对齐检测器（阈值均为待回测参数，实际值随候选落盘） ==========

def _ohlcv_arrays(df):
    close = df["close"].astype(float).to_numpy()
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    vol = df["volume"].astype(float).to_numpy()
    return close, high, low, vol


def _find_rally_segment(df, lookback: int = WAVE_LOOKBACK) -> Optional[tuple[int, int, int, int]]:
    """在近 lookback 日内定位"有效启动低点→阶段高点"拉升段。

    返回 (seg_start, i_low, i_high, n)（df 内绝对位置）；找不到返回 None。
    口径（待回测）：窗口内最低价日为启动低点，其后最高价为阶段高点。
    """
    n = len(df)
    if n < 10:
        return None
    start = max(0, n - lookback)
    _, high, low, _ = _ohlcv_arrays(df)
    i_low = start + int(low[start:].argmin())
    if i_low >= n - 2:
        return None
    i_high = i_low + int(high[i_low:].argmax())
    if i_high <= i_low:
        return None
    return start, i_low, i_high, n


def detect_wave_type(df) -> dict[str, Any]:
    """拉升波三分类（B1 §四.0）：sprint > rally > buildup，冲突取保守。

    detail.accel_10d_gain_pct 为拉升段（i_low→i_high）内最大 10 日涨幅，
    不以当日为终点（回调时点后置口径会失效）。
    """
    close, high, low, vol = _ohlcv_arrays(df)
    n = len(df)
    detail: dict[str, Any] = {}
    if n < WAVE_MIN_BARS:
        return {"wave_type": "unknown", "available": False, "detail": {"reason": f"K线不足{WAVE_MIN_BARS}根"}}
    seg = _find_rally_segment(df)
    if seg is None:
        return {"wave_type": "unknown", "available": True, "detail": {"reason": "无有效启动低点→阶段高点段"}}
    start, i_low, i_high, _ = seg

    seg_gain = (float(high[i_high]) / float(close[i_low]) - 1) * 100 if close[i_low] else 0.0
    # 近20日涨停/接近涨停计数（全 df 口径；prev close<=0 的脏数据 bar 不计涨停）
    with np.errstate(divide="ignore", invalid="ignore"):
        chg = close[1:] / close[:-1] * 100 - 100
    limit_ups = [i + 1 for i in range(max(0, n - WAVE_SPRINT_WINDOW - 1), n - 1)
                 if close[i] > 0 and chg[i] >= WAVE_LIMIT_UP_PCT]
    # 高斜率加速：拉升段（i_low→i_high）内最大 10 日涨幅。
    # 以今天为终点会在 B1 回调时点必然失效，必须在段上计算（code review 修复）。
    accel_10d = None
    if i_high - i_low >= 10:
        accel_10d = max(
            (float(close[t] / close[t - 10] - 1) * 100)
            for t in range(i_low + 10, i_high + 1)
            if close[t - 10] > 0
        )
    # 顶部放量：阶段高点日量 / 其前5日均量
    top_vol_ratio = None
    if i_high >= 1:
        base = vol[max(0, i_high - 5):i_high].mean() if i_high >= 1 else 0
        top_vol_ratio = float(vol[i_high] / base) if base else None
    # 启动段放量长阳：启动低点后5日内存在涨幅>=5%且量>=前5日均量1.5倍
    start_bull = False
    for t in range(i_low + 1, min(i_low + 6, n)):
        base = vol[max(0, t - 5):t].mean()
        if base and close[t] / close[t - 1] - 1 >= WAVE_START_CANDLE_PCT / 100 and vol[t] >= base * WAVE_START_CANDLE_VOL:
            start_bull = True
            break
    # 二次启动：启动低点之前的窗口段已存在 >=15% 摆动（前一段拉升）
    second_start = False
    if i_low - start >= 5:
        prior_swing = (float(high[start:i_low].max()) / float(low[start:i_low].min()) - 1) * 100
        second_start = prior_swing >= WAVE_SECOND_START_GAIN
    else:
        prior_swing = None

    accel_ok = accel_10d is not None and accel_10d >= WAVE_ACCEL_10D_GAIN
    top_vol_ok = top_vol_ratio is not None and top_vol_ratio >= WAVE_TOP_VOL_RATIO
    if len(limit_ups) >= WAVE_SPRINT_MIN_LIMIT_UPS and accel_ok and top_vol_ok:
        wave = "sprint"
    elif second_start and WAVE_RALLY_GAIN[0] <= seg_gain <= WAVE_RALLY_GAIN[1]:
        wave = "rally"
    elif WAVE_BUILDUP_GAIN[0] <= seg_gain <= WAVE_BUILDUP_GAIN[1] and start_bull:
        wave = "buildup"
    else:
        wave = "unknown"

    detail = {
        "seg_gain_pct": round(seg_gain, 2),
        "limit_up_count_20d": len(limit_ups),
        "accel_10d_gain_pct": round(accel_10d, 2) if accel_10d is not None else None,
        "top_vol_ratio": round(top_vol_ratio, 3) if top_vol_ratio is not None else None,
        "start_bull_candle": start_bull,
        "second_start": second_start,
        "prior_swing_pct": round(prior_swing, 2) if prior_swing is not None else None,
    }
    return {"wave_type": wave, "available": True, "detail": detail}


def weekly_j_state(df) -> dict[str, Any]:
    """周线 J（B1 §四.1 主线口径：周线 J<13 为周线 B1 候选）。"""
    weekly = resample(df, "W-FRI")
    w = kdj(weekly)
    if not w.get("available"):
        return {"available": False, "weekly_j": None, "weekly_j_low": False}
    return {"available": True, "weekly_j": w["j"], "weekly_j_low": bool(w["j"] < J_LOW_THRESHOLD)}


def check_non_one_wave(df) -> dict[str, Any]:
    """非一波流确认（B1 §四）：三条件各自布尔+实际值。

    confirmed=三全；revoked=顶部放量大阴或回调放量破位；其余 insufficient。
    """
    close, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    seg = _find_rally_segment(df)
    if seg is None or n < WAVE_MIN_BARS or seg[2] >= n - 2:
        return {"status": "insufficient", "available": False,
                "conditions": {}, "reason": "无完整上涨段+回调段"}
    _, i_low, i_high, _ = seg

    up_vol = vol[i_low:i_high + 1]
    up_vol_mean = float(up_vol.mean()) if len(up_vol) else 0.0
    # (a) 上涨段温和放量：无单日爆量（单日量/段均量 < 2）
    max_burst = float(up_vol.max() / up_vol_mean) if up_vol_mean else None
    mild = max_burst is not None and max_burst < NOW_MILD_VOL_BURST
    # (b) 阶段高点±3日内无放量大阴（跌幅>3% 且 量/前5日均量>1.5）
    worst_drop = None
    worst_vol_ratio = None
    big_bear = False
    for t in range(max(1, i_high - NOW_TOP_ZONE), min(n, i_high + NOW_TOP_ZONE + 1)):
        drop = (close[t] / close[t - 1] - 1) * 100
        base = vol[max(0, t - 5):t].mean()
        vr = float(vol[t] / base) if base else None
        if worst_drop is None or drop < worst_drop:
            worst_drop = drop
        if vr is not None and (worst_vol_ratio is None or vr > worst_vol_ratio):
            worst_vol_ratio = vr
        if drop <= NOW_BEAR_DROP_PCT and vr is not None and vr >= NOW_BEAR_VOL_RATIO:
            big_bear = True
    no_big_bear = not big_bear
    # (c) 回调段缩量：回调段均量/上涨段均量 < 0.7
    pull_vol = vol[i_high + 1:]
    pull_ratio = float(pull_vol.mean() / up_vol_mean) if len(pull_vol) and up_vol_mean else None
    shrink = pull_ratio is not None and pull_ratio < NOW_PULLBACK_VOL_RATIO
    # 撤销：回调放量破位（跌回启动位且量>=上涨段均量）
    break_with_vol = bool(
        len(pull_vol) and up_vol_mean
        and any(close[t] < close[i_low] and vol[t] >= up_vol_mean for t in range(i_high + 1, n))
    )
    if big_bear or break_with_vol:
        status = "revoked"
    elif mild and no_big_bear and shrink:
        status = "confirmed"
    else:
        status = "insufficient"
    return {
        "status": status,
        "available": True,
        "conditions": {
            "mild_volume": {"hit": bool(mild), "max_vol_burst": round(max_burst, 3) if max_burst is not None else None},
            "no_top_big_bear": {"hit": bool(no_big_bear),
                                "worst_drop_pct": round(worst_drop, 2) if worst_drop is not None else None,
                                "worst_vol_ratio": round(worst_vol_ratio, 3) if worst_vol_ratio is not None else None},
            "pullback_shrink": {"hit": bool(shrink), "pullback_vol_ratio": round(pull_ratio, 3) if pull_ratio is not None else None},
        },
        "break_with_volume": break_with_vol,
    }


def check_repair_signals(df, index_df) -> dict[str, Any]:
    """B1 修复信号（B1 §四.2）：输出命中数组+各信号实际值。"""
    close, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    j = kdj(df)
    j_now = j.get("j") if j.get("available") else None
    j_prev = j.get("j_prev") if j.get("available") else None

    j_turn_up = bool(j_now is not None and j_prev is not None
                     and j_now > j_prev and j_prev < REPAIR_J_PREV_MAX)

    vol_ma5_prev = float(vol[-6:-1].mean()) if n >= 6 else None
    vol_ratio = float(vol[-1] / vol_ma5_prev) if vol_ma5_prev else None
    change = (close[-1] / close[-2] - 1) * 100 if n >= 2 and close[-2] else None
    shrink_stop = bool(vol_ratio is not None and vol_ratio <= REPAIR_VOL_SHRINK
                       and change is not None and abs(change) <= REPAIR_CHANGE_PCT)

    rs_turn = False
    rs5_now = rs5_prev = None
    if index_df is not None and not index_df.empty and n >= 7 and len(index_df) >= 7:
        ic = index_df["close"].astype(float).to_numpy()
        rs5_now = (close[-1] / close[-6] - 1) * 100 - (ic[-1] / ic[-6] - 1) * 100
        rs5_prev = (close[-2] / close[-7] - 1) * 100 - (ic[-2] / ic[-7] - 1) * 100
        rs_turn = bool(rs5_now >= 0 > rs5_prev)

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
            "volume_shrink_stop_fall": {"hit": shrink_stop, "vol_ratio": round(vol_ratio, 3) if vol_ratio is not None else None,
                                        "change_pct": round(change, 2) if change is not None else None},
            "rs_turn_strong": {"hit": rs_turn, "rs5_now_pp": round(rs5_now, 2) if rs5_now is not None else None,
                               "rs5_prev_pp": round(rs5_prev, 2) if rs5_prev is not None else None},
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
    cond2 = bool((vol[-1] > vol[-2] > vol[-3])
                 or all(v >= vol_ma20 for v in vol[-3:]))
    spike_ratios = [float(vol[t] / vol[t - 1]) for t in range(max(1, n - FIVE_DAY_SPIKE_WINDOW), n) if vol[t - 1]]
    max_spike = max(spike_ratios) if spike_ratios else None
    cond3 = bool(max_spike is not None and max_spike >= FIVE_DAY_SPIKE_RATIO)
    return {
        "hit": bool(cond1 and cond2 and cond3),
        "available": True,
        "conditions": {
            "close_above_ma5": {"hit": cond1, "close": round(float(close[-1]), 4), "ma5": round(ma5, 4)},
            "three_day_volume_up": {"hit": cond2, "vols_last3": [float(v) for v in vol[-3:]],
                                    "vol_ma20": round(vol_ma20, 2)},
            "spike_within_7d": {"hit": cond3, "max_spike_ratio": round(max_spike, 3) if max_spike is not None else None},
        },
    }


def check_volume_sustain(df) -> dict[str, Any]:
    """量能持续性（CZ §14.6）：mainline_confirmed / retreat / neutral。"""
    _, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    if n < VOLUME_SUSTAIN_WINDOW + 1:
        return {"status": "neutral", "available": False}
    win = vol[-VOLUME_SUSTAIN_WINDOW:]
    peak_rel = int(win.argmax())
    peak = float(win[peak_rel])
    days_since = VOLUME_SUSTAIN_WINDOW - 1 - peak_rel
    peak_pos = n - VOLUME_SUSTAIN_WINDOW + peak_rel
    peak_date = str(df["date"].iloc[peak_pos])[:10]
    post = vol[peak_pos + 1:]
    post_mean_ratio = float(post.mean() / peak) if len(post) and peak else None
    post_min_ratio = float(post.min() / peak) if len(post) and peak else None
    ratios_last13 = [round(float(v / peak), 3) if peak else None for v in win]
    retreat = bool(days_since >= VOLUME_SUSTAIN_RETREAT_DAYS and peak
                   and all(v < peak * VOLUME_SUSTAIN_RATIO for v in vol[-VOLUME_SUSTAIN_RETREAT_DAYS:]))
    # 与 cz_strategy.md §14.6 一致：峰值日后窗口内"逐日"量都必须 ≥ 峰值×55%
    # （均值达标但有单日跌破不算主线确认）。
    confirmed = bool(not retreat and days_since >= VOLUME_SUSTAIN_MIN_POST_DAYS
                     and len(post) and peak
                     and all(v >= peak * VOLUME_SUSTAIN_RATIO for v in post))
    status = "retreat" if retreat else ("mainline_confirmed" if confirmed else "neutral")
    return {
        "status": status,
        "available": True,
        "peak_date": peak_date,
        "days_since_peak": days_since,
        "post_mean_ratio": round(post_mean_ratio, 3) if post_mean_ratio is not None else None,
        "post_min_ratio": round(post_min_ratio, 3) if post_min_ratio is not None else None,
        "vol_ratios_last13": ratios_last13,
    }


def check_leader_volume(df) -> dict[str, Any]:
    """龙头量能（CZ §九）：连续3日量 >= 前20日最低日量×1.7。"""
    _, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    if n < LEADER_VOL_BASE_DAYS + 3:
        return {"hit": False, "available": False}
    base = float(vol[-(LEADER_VOL_BASE_DAYS + 3):-3].min())
    ratios = [float(v / base) if base else None for v in vol[-3:]]
    hit = bool(base and all(v >= base * LEADER_VOL_RATIO for v in vol[-3:]))
    return {"hit": hit, "available": True, "base_vol": base,
            "vol_ratios_last3": [round(r, 3) if r is not None else None for r in ratios]}


def _drawdown_250d(close, high) -> tuple[Optional[float], Optional[float]]:
    if len(close) < CZ_MIN_BARS:
        return None, None
    high250 = float(high[-CZ_MIN_BARS:].max())
    dd = (1 - float(close[-1]) / high250) * 100 if high250 else None
    return high250, dd


def check_three_lows(df) -> dict[str, Any]:
    """三低（CZ §九/§18.6）：低价格（回撤>=40%）+ 低量（<250日均量×30%）。

    第三维"低关注度"非量价可计算，不输出；财务排雷因数据源未接入暂缓。
    """
    close, high, _, vol = _ohlcv_arrays(df)
    high250, dd = _drawdown_250d(close, high)
    if dd is None:
        return {"hit": False, "available": False}
    vol_ma250 = float(vol[-CZ_MIN_BARS:].mean())
    low_price = dd >= THREE_LOWS_DRAWDOWN_PCT
    low_vol = bool(vol_ma250 and vol[-1] < vol_ma250 * THREE_LOWS_VOL_RATIO)
    return {
        "hit": bool(low_price and low_vol),
        "available": True,
        "conditions": {
            "low_price": {"hit": bool(low_price), "drawdown_from_250d_high_pct": round(dd, 2)},
            "low_volume": {"hit": low_vol, "vol_today": float(vol[-1]),
                           "vol_ma250": round(vol_ma250, 2),
                           "vol_ratio_vs_ma250": round(float(vol[-1] / vol_ma250), 3) if vol_ma250 else None},
        },
    }


def check_bottom_volume(df) -> dict[str, Any]:
    """底部巨量（CZ §14.6）：回撤>=40% + 当日量>=250日均量×2 + 不再创新低。

    不再创新低 = 今日最低未跌破"此前"20 日最低（不含当日；含当日则恒真）。
    """
    close, high, low, vol = _ohlcv_arrays(df)
    _, dd = _drawdown_250d(close, high)
    if dd is None or len(close) < BOTTOM_NO_NEW_LOW_DAYS + 1:
        return {"hit": False, "available": False}
    vol_ma250 = float(vol[-CZ_MIN_BARS:].mean())
    huge_vol = bool(vol_ma250 and vol[-1] >= vol_ma250 * BOTTOM_VOL_RATIO)
    low20 = float(low[-(BOTTOM_NO_NEW_LOW_DAYS + 1):-1].min())
    no_new_low = bool(low[-1] >= low20)
    return {
        "hit": bool(dd >= THREE_LOWS_DRAWDOWN_PCT and huge_vol and no_new_low),
        "available": True,
        "conditions": {
            "deep_drawdown": {"hit": bool(dd >= THREE_LOWS_DRAWDOWN_PCT),
                              "drawdown_from_250d_high_pct": round(dd, 2)},
            "huge_volume": {"hit": huge_vol,
                            "vol_ratio_vs_ma250": round(float(vol[-1] / vol_ma250), 3) if vol_ma250 else None},
            "no_new_low": {"hit": no_new_low, "low_today": float(low[-1]), "low_20d": low20},
        },
    }


def check_ignition(df) -> dict[str, Any]:
    """放量点火（good_b1 启动长阳）：前段缩量后出现放量收阳的启动K。

    命中条件（近 IGNITION_WINDOW 根内任一根 t）：量比(vol[t]/前5日均量) >= 1.5、
    收阳(close>open)、单日涨幅 >= IGNITION_MIN_GAIN，且启动前处于缩量
    （前5日均量 <= 更前5日均量）。
    """
    close, _, _, vol = _ohlcv_arrays(df)
    open_ = df["open"].astype(float).to_numpy()
    n = len(df)
    if n < 12:
        return {"hit": False, "available": False}
    hit_detail = None
    for t in range(max(11, n - IGNITION_WINDOW), n):
        base5 = vol[t - 5:t].mean()
        if not base5:
            continue
        vr = float(vol[t] / base5)
        chg = (close[t] / close[t - 1] - 1) * 100 if close[t - 1] else 0.0
        is_bull = close[t] > open_[t]
        prev5 = vol[t - 10:t - 5].mean()
        pre_contracted = (prev5 == 0) or (base5 <= prev5)
        if vr >= IGNITION_VOL_RATIO and is_bull and chg >= IGNITION_MIN_GAIN and pre_contracted:
            hit_detail = {"bars_ago": n - 1 - t, "vol_ratio5": round(vr, 3),
                          "change_pct": round(chg, 2), "pre_contracted": bool(pre_contracted)}
            break
    return {"hit": hit_detail is not None, "available": True, "detail": hit_detail}


def check_pullback_shrink(df, dks_last: Optional[float] = None) -> dict[str, Any]:
    """回调缩量企稳（good_b1 回调段）：自窗口高点回撤 + 回调段缩量 + 收盘守多空线。

    窗口 PULLBACK_LOOKBACK 内：距最高收盘回撤 >= PULLBACK_MIN_DROP%，回调段均量 /
    上涨段均量 < PULLBACK_VOL_RATIO，且（无 DKS 时忽略）收盘 >= DKS。
    """
    close, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    if n < PULLBACK_LOOKBACK + 5:
        return {"hit": False, "available": False}
    seg_close = close[-PULLBACK_LOOKBACK:]
    hi_rel = int(seg_close.argmax())
    hi_pos = n - PULLBACK_LOOKBACK + hi_rel
    high = float(close[hi_pos])
    drop_pct = (1 - close[-1] / high) * 100 if high else 0.0
    run_vol = vol[n - PULLBACK_LOOKBACK:hi_pos + 1]
    pull_vol = vol[hi_pos + 1:]
    run_mean = float(run_vol.mean()) if len(run_vol) else 0.0
    pull_ratio = (float(pull_vol.mean()) / run_mean) if (len(pull_vol) >= 2 and run_mean) else None
    shrink = pull_ratio is not None and pull_ratio < PULLBACK_VOL_RATIO
    hold_dks = (dks_last is None) or (close[-1] >= dks_last)
    hit = bool(drop_pct >= PULLBACK_MIN_DROP and shrink and hold_dks)
    return {
        "hit": hit, "available": True,
        "detail": {"drop_from_high_pct": round(drop_pct, 2),
                   "pullback_vol_ratio": round(pull_ratio, 3) if pull_ratio is not None else None,
                   "hold_dks": bool(hold_dks)},
    }


def check_macd_technics(df) -> dict[str, Any]:
    """MACD 十大技术（macd十大技术精讲）→ 确定性因子。

    - zone：三区间动能状态机。做多口径：DIF/DEA 在零轴上且红柱扩张=第一区间
      （强势）；红柱脱离 DIF（收缩）=第二区间；红柱脱离 DEA（≤0）=第三区间。
      zone1_restart：昨日 hist≤0（或收缩后）今日重新扩张且 DIF>0——"3浪/5浪
      的第一区间"，回调后再启动的强信号。
    - bottom_divergence 底背离：窗口内两个收盘价摆低 L2<L1，但 DIF 低点抬高。
    - top_divergence 顶背离（高度/线型）：两个收盘摆高 B>A，但 DIF_B<DIF_A
      或 hist_B<hist_A。
    - three_peaks 三打白骨精：连续 3 个摆高递增 + DIF 连续 3 峰递减。
    - overextended 开口/空间拐离：|DIF| 处于近 120 日 90%+ 分位且柱体仍在。
    """
    close_s = df["close"].astype(float).reset_index(drop=True)
    n = len(df)
    if n < 40:
        return {"available": False}
    dif = ema(close_s, 12) - ema(close_s, 26)
    dea = ema(dif, 9)
    hist = (dif - dea) * 2
    d, h = dif.to_numpy(), hist.to_numpy()
    close = close_s.to_numpy()

    # 区间状态机
    dif_last, dea_last = float(dif.iloc[-1]), float(dea.iloc[-1])
    h0, h1 = float(h[-1]), float(h[-2])
    if dif_last > 0 and dea_last > 0:
        if h0 > 0:
            zone = 1 if h0 >= h1 else 2  # 扩张=第一区间；收缩（脱离DIF）=第二区间
        else:
            zone = 3                     # 柱体脱离 DEA（≤0）=第三区间
    else:
        zone = 0                         # 零轴下方，不做多区间分级
    zone1_restart = bool(dif_last > 0 and h0 > 0 and h0 > h1 and h1 <= 0)

    # 摆动高/低点（左右各 MACD_SWING_FRACTAL 根分型，右确认避免未来函数）
    f = MACD_SWING_FRACTAL
    w0 = max(f, n - MACD_DIV_LOOKBACK)
    swing_hi = [i for i in range(w0, n - f)
                if close[i] == close[i - f:i + f + 1].max() and (close[i - f:i + f + 1] < close[i]).sum() <= 2 * f - 1]
    swing_lo = [i for i in range(w0, n - f)
                if close[i] == close[i - f:i + f + 1].min() and (close[i - f:i + f + 1] > close[i]).sum() <= 2 * f - 1]

    top_div = {"hit": False}
    if len(swing_hi) >= 2:
        a, b = swing_hi[-2], swing_hi[-1]
        if close[b] > close[a] and (d[b] < d[a] or h[b] < h[a]):
            top_div = {"hit": True, "a_bars_ago": n - 1 - a, "b_bars_ago": n - 1 - b,
                       "close_a": round(float(close[a]), 4), "close_b": round(float(close[b]), 4),
                       "dif_a": round(float(d[a]), 4), "dif_b": round(float(d[b]), 4),
                       "hist_a": round(float(h[a]), 4), "hist_b": round(float(h[b]), 4)}
    three_peaks = {"hit": False}
    if len(swing_hi) >= 3:
        p1, p2, p3 = swing_hi[-3], swing_hi[-2], swing_hi[-1]
        if close[p1] < close[p2] < close[p3] and d[p1] > d[p2] > d[p3]:
            three_peaks = {"hit": True, "peaks_bars_ago": [n - 1 - p1, n - 1 - p2, n - 1 - p3],
                           "dif_peaks": [round(float(d[p1]), 4), round(float(d[p2]), 4), round(float(d[p3]), 4)]}
    bottom_div = {"hit": False}
    if len(swing_lo) >= 2:
        a, b = swing_lo[-2], swing_lo[-1]
        if close[b] < close[a] and d[b] > d[a]:
            bottom_div = {"hit": True, "a_bars_ago": n - 1 - a, "b_bars_ago": n - 1 - b,
                          "close_a": round(float(close[a]), 4), "close_b": round(float(close[b]), 4),
                          "dif_a": round(float(d[a]), 4), "dif_b": round(float(d[b]), 4)}

    # 开口/空间拐离：|DIF| 分位 + 柱体仍在
    win = min(MACD_OVEREXT_WIN, n)
    abs_dif = [abs(float(x)) for x in d[-win:]]
    pctl = float(sum(1 for x in abs_dif if x <= abs_dif[-1]) / len(abs_dif)) if win >= 20 else None
    overextended = {"hit": bool(pctl is not None and pctl >= MACD_OVEREXT_PCTL and h0 != 0),
                    "dif_abs_percentile": round(pctl, 3) if pctl is not None else None}

    return {
        "available": True,
        "zone": zone, "zone1_restart": zone1_restart,
        "dif": round(dif_last, 4), "dea": round(dea_last, 4), "hist": round(h0, 4),
        "bottom_divergence": bottom_div,
        "top_divergence": top_div,
        "three_peaks": three_peaks,
        "overextended": overextended,
    }


def compute_perfect_b1_fit(df, daily_j, zx: dict, pullback: dict) -> dict[str, Any]:
    """完美 B1 图形贴合度（0-8 梯度分）：J 深度 + 回踩贴线 + 缩量程度 +
    MACD 零轴上 + DKS 上行。每个分量输出实际值（待回测参数见顶部常量）。"""
    comp: dict[str, Any] = {}

    # J 深度：J<0 → 2；J<7 → 1.5；J<13 → 1（图集案例 J 全在 13 以下，多为负）
    if daily_j is None:
        j_pts = 0.0
    elif daily_j < FIT_J_DEEP:
        j_pts = 2.0
    elif daily_j < FIT_J_MID:
        j_pts = 1.5
    elif daily_j < J_LOW_THRESHOLD:
        j_pts = 1.0
    else:
        j_pts = 0.0
    comp["j_depth"] = {"points": j_pts, "daily_j": daily_j}

    # 回踩贴线：收盘距 QSX 或 DKS 的最近偏离 ≤3% → 2；≤6% → 1
    near_pts = 0.0
    line_dist = None
    close_last = float(df["close"].iloc[-1])
    if zx.get("available") and close_last:
        dists = []
        for key in ("qsx", "dks"):
            v = zx.get(key)
            if v:
                dists.append(abs(close_last / float(v) - 1) * 100)
        if dists:
            line_dist = round(min(dists), 2)
            near_pts = 2.0 if line_dist <= FIT_NEAR_LINE_PCT else (1.0 if line_dist <= FIT_NEAR_LINE_MAX_PCT else 0.0)
    comp["near_line"] = {"points": near_pts, "min_line_distance_pct": line_dist}

    # 缩量程度：回调段/上涨段均量 ≤0.5 → 2；≤0.8 → 1
    pull_ratio = ((pullback.get("detail") or {}).get("pullback_vol_ratio")
                  if pullback.get("available") else None)
    shrink_pts = 0.0
    if pull_ratio is not None:
        shrink_pts = 2.0 if pull_ratio <= FIT_SHRINK_DEEP else (1.0 if pull_ratio <= FIT_SHRINK_MID else 0.0)
    comp["shrink_degree"] = {"points": shrink_pts, "pullback_vol_ratio": pull_ratio}

    # MACD 零轴上：DIF>0 → 1（图集多数案例 DIF 在零轴上方）
    m = macd(df)
    macd_pts = 0.0
    dif_val = None
    if m.get("available"):
        dif_val = m.get("dif")
        macd_pts = 1.0 if (dif_val is not None and dif_val > 0) else 0.0
    comp["macd_above_zero"] = {"points": macd_pts, "dif": dif_val}

    # DKS 上行：DKS[t] > DKS[t-5] → 1（慢线本身走升）
    dks_pts = 0.0
    dks_now = dks_prev = None
    close_s = df["close"].astype(float).reset_index(drop=True)
    if len(close_s) >= 114 + FIT_DKS_SLOPE_DAYS:
        dks_series = sum(close_s.rolling(w).mean() for w in (14, 28, 57, 114)) / 4
        dks_now = float(dks_series.iloc[-1])
        dks_prev = float(dks_series.iloc[-1 - FIT_DKS_SLOPE_DAYS])
        dks_pts = 1.0 if dks_now > dks_prev else 0.0
    comp["dks_rising"] = {"points": dks_pts, "dks": dks_now, "dks_prev": dks_prev}

    total = round(sum(c["points"] for c in comp.values()), 2)
    return {"score": total, "max_score": 8, "components": comp}


def detect_distribution(df, code: str = "") -> dict[str, Any]:
    """主力出货五方式（顶部派发，B1 §七.3）：负向因子，用于选股规避/降档。

    ① 顶部天量大阴、② 次高点巨量长阴、③ 阶梯放量跌破QSX、④ 双头双巨阴、
    ⑤ 顶部绿肥红瘦。命中≥1→watch；命中①/②或≥2→high。阈值均为待回测参数。
    """
    close, high, low, vol = _ohlcv_arrays(df)
    open_ = df["open"].astype(float).to_numpy()
    n = len(df)
    if n < 30:
        return {"available": False, "signals": {}, "hits": [], "hit_count": 0,
                "severe": False, "risk_level": "none"}
    limit = _infer_price_limit(code, df)
    big_bear = limit * DIST_BIG_BEAR_FRAC
    long_bear = limit * DIST_LONG_BEAR_FRAC
    vol_ma20 = float(vol[max(0, n - DIST_HUGE_VOL_WIN - 1):n - 1].mean())
    # vol_ma20 近零（长期停牌/零成交脏数据）时量比类判定全部失真 → 检测器不可用
    series_vol_mean = float(vol.mean()) if n else 0.0
    if not series_vol_mean or vol_ma20 < series_vol_mean * DIST_MIN_VOL_MA20_FRAC:
        return {"available": False, "signals": {}, "hits": [], "hit_count": 0,
                "severe": False, "risk_level": "none",
                "reason": f"vol_ma20 近零（{vol_ma20:.1f} < 全序列均量 {series_vol_mean:.1f}×{DIST_MIN_VOL_MA20_FRAC}）"}
    qsx = df["close"].astype(float).ewm(span=10, adjust=False).mean().ewm(span=10, adjust=False).mean().to_numpy()

    def chg(t: int) -> float:
        return (close[t] / close[t - 1] - 1) * 100 if t >= 1 and close[t - 1] else 0.0

    def vr5(t: int):
        base = vol[max(0, t - 5):t].mean()
        return float(vol[t] / base) if base else None

    sig: dict[str, Any] = {}

    # ① 顶部天量大阴：近DIST_RECENT根内 大阴 + 天量 + 阴线前加速
    hit1 = None
    for t in range(n - DIST_RECENT, n):
        if t < DIST_ACCEL_WIN + 1:
            continue
        c = chg(t)
        huge = vol[t] >= vol_ma20 * DIST_HUGE_VOL_RATIO or vol[t] >= vol[max(0, t - DIST_HUGE_VOL_WIN):t + 1].max()
        accel = (close[t - 1] / close[t - DIST_ACCEL_WIN] - 1) * 100 if close[t - DIST_ACCEL_WIN] else 0.0
        if close[t] < open_[t] and c <= -big_bear and huge and accel >= DIST_ACCEL_GAIN:
            hit1 = {"bars_ago": n - 1 - t, "change_pct": round(c, 2),
                    "vol_ratio_ma20": round(float(vol[t] / vol_ma20), 3) if vol_ma20 else None,
                    "accel_pct": round(accel, 2)}
            break
    sig["top_huge_vol_bear"] = {"hit": hit1 is not None, "detail": hit1}

    # ② 次高点巨量长阴：前一日缩量创新高/次高 + 当日巨量长阴
    hit2 = None
    for t in range(n - DIST_RECENT, n):
        if t < 25:
            continue
        c = chg(t)
        prev_new_high = high[t - 1] >= high[max(0, t - 21):t - 1].max()
        prev_shrink = (vr5(t - 1) is not None and vr5(t - 1) <= DIST_SUBHIGH_SHRINK)
        huge = vol_ma20 and vol[t] >= vol_ma20 * DIST_HUGE_VOL_RATIO
        if close[t] < open_[t] and c <= -long_bear and huge and prev_new_high and prev_shrink:
            hit2 = {"bars_ago": n - 1 - t, "change_pct": round(c, 2),
                    "prev_vol_ratio5": round(float(vr5(t - 1)), 3),
                    "vol_ratio_ma20": round(float(vol[t] / vol_ma20), 3)}
            break
    sig["subhigh_vol_bear"] = {"hit": hit2 is not None, "detail": hit2}

    # ③ 阶梯放量跌破QSX：近DIST_RECENT根内收盘放量跌破QSX，且此前连续≥3根放量阴
    hit3 = None
    for t in range(n - DIST_RECENT, n):
        if t < DIST_STAIR_MIN_BARS + 6:
            continue
        vrt = vr5(t)
        broke = close[t] < qsx[t] and vrt is not None and vrt >= DIST_STAIR_BREAK_VR
        cnt = 0
        for k in range(t, max(0, t - 8), -1):
            vrk = vr5(k)
            if close[k] < open_[k] and vrk is not None and (vol[k] >= vol[k - 1] or vrk >= 1.0):
                cnt += 1
            else:
                break
        if broke and cnt >= DIST_STAIR_MIN_BARS:
            hit3 = {"bars_ago": n - 1 - t, "consecutive_vol_bears": cnt,
                    "vol_ratio5": round(vrt, 3), "below_qsx": True}
            break
    sig["stairstep_vol_decline"] = {"hit": hit3 is not None, "detail": hit3}

    # ④ 双头双巨阴：近窗口内两个相近高点，各自其后≤2根内出现放量阴
    hit4 = None
    w0 = max(0, n - DIST_TOP_WINDOW * 2)
    peaks = [i for i in range(w0 + 2, n - 2)
             if high[i] == high[i - 2:i + 3].max() and float((high[i - 2:i + 3] == high[i]).sum()) == 1]
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
                hit4 = {"peak1_bars_ago": n - 1 - p1, "peak2_bars_ago": n - 1 - p2,
                        "tops_gap_pct": round(abs(high[p1] / high[p2] - 1) * 100, 2)}
    sig["double_top_vol_bear"] = {"hit": hit4 is not None, "detail": hit4}

    # ⑤ 顶部绿肥红瘦：顶部区间阴线实体均值 > 阳线实体均值 且 阴量 > 阳量
    seg = range(n - DIST_TOP_WINDOW, n)
    near_top = True if n < 60 else high[-DIST_TOP_WINDOW:].max() >= high[-60:].max() * 0.98
    bear_bodies = [abs(close[t] / open_[t] - 1) * 100 for t in seg if close[t] < open_[t] and open_[t]]
    bull_bodies = [abs(close[t] / open_[t] - 1) * 100 for t in seg if close[t] > open_[t] and open_[t]]
    bear_vols = [vol[t] for t in seg if close[t] < open_[t]]
    bull_vols = [vol[t] for t in seg if close[t] > open_[t]]
    hit5 = bool(near_top and bear_bodies and bull_bodies
                and (sum(bear_bodies) / len(bear_bodies) > sum(bull_bodies) / len(bull_bodies))
                and bear_vols and bull_vols
                and (sum(bear_vols) / len(bear_vols) > sum(bull_vols) / len(bull_vols)))
    sig["top_green_heavy_red_light"] = {
        "hit": hit5,
        "detail": {"bear_body_mean_pct": round(sum(bear_bodies) / len(bear_bodies), 3) if bear_bodies else None,
                   "bull_body_mean_pct": round(sum(bull_bodies) / len(bull_bodies), 3) if bull_bodies else None} if near_top else None,
    }

    hits = [k for k, v in sig.items() if v["hit"]]
    severe = sig["top_huge_vol_bear"]["hit"] or sig["subhigh_vol_bear"]["hit"]
    risk = "high" if (severe or len(hits) >= 2) else ("watch" if hits else "none")
    return {"available": True, "signals": sig, "hits": hits, "hit_count": len(hits),
            "severe": bool(severe), "risk_level": risk, "price_limit": limit}


def compute_metrics(df, index_df, code: str = "") -> dict[str, Any]:
    """对单股日线 DataFrame 计算全部指标与模式标签（确定性）。"""
    close = df["close"]
    bbi = bbi_state(df)
    j = kdj(df)
    last = df.iloc[-1]
    prev_close = float(close.iloc[-2]) if len(df) >= 2 else None

    vol = df["volume"].astype(float)
    vol_today = float(vol.iloc[-1])
    vol_ma5_prev = float(vol.iloc[-6:-1].mean()) if len(df) >= 6 else None
    vol_ratio = (vol_today / vol_ma5_prev) if vol_ma5_prev else None
    vol20 = vol.tail(20)
    vol_pctile = float((vol20 < vol_today).mean() * 100) if len(vol20) >= 20 else None

    change_pct = ((float(last["close"]) / prev_close - 1) * 100) if prev_close else None
    amplitude_pct = (
        (float(last["high"]) / prev_close - float(last["low"]) / prev_close) * 100
        if prev_close else None
    )

    stock_ret20 = _pct_change(df, 20)
    index_ret20 = _pct_change(index_df, 20) if index_df is not None and not index_df.empty else None
    rs_20d = (stock_ret20 - index_ret20) if (stock_ret20 is not None and index_ret20 is not None) else None

    stop_ref = None
    if len(df) >= STOP_LOOKBACK:
        stop_ref = round(float(df["low"].tail(STOP_LOOKBACK).min()), 4)

    daily_j = j.get("j") if j.get("available") else None
    j_low = daily_j is not None and daily_j < J_LOW_THRESHOLD
    vol_contraction = (
        vol_ratio is not None and vol_ratio <= VOL_RATIO_MAX
        and vol_pctile is not None and vol_pctile <= VOL_PCTILE_MAX
    )
    reversal_k = bool(
        j_low and vol_contraction
        and change_pct is not None and abs(change_pct) <= REVERSAL_CHANGE_PCT
        and amplitude_pct is not None and amplitude_pct <= REVERSAL_AMPLITUDE_PCT
    )
    rs_strong = rs_20d is not None and rs_20d >= RS_STRONG_PP

    # --- 知行量价（good_b1）与出货识别（出货五方式）---
    zx = zhixing_state(df)
    dks_last = zx.get("dks") if zx.get("available") else None
    ignition = check_ignition(df)
    pullback_shrink = check_pullback_shrink(df, dks_last)
    ride_above_fast = bool(zx.get("available") and zx.get("close_above_qsx") and zx.get("qsx_gt_dks"))
    zx_recent_gold = bool(
        zx.get("available") and zx.get("qsx_gt_dks")
        and zx.get("days_since_golden_cross") is not None
        and zx["days_since_golden_cross"] <= ZX_CROSS_RECENT
    )
    b1_ignition_hit = bool(
        (j_low or reversal_k) and pullback_shrink.get("hit")
        and (zx_recent_gold or ignition.get("hit"))
    )
    distribution = detect_distribution(df, code)

    return {
        "close": round(float(last["close"]), 4),
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "amplitude_pct": round(amplitude_pct, 2) if amplitude_pct is not None else None,
        "bbi": bbi.get("value") if bbi.get("available") else None,
        "bbi_distance_pct": bbi.get("distance_pct") if bbi.get("available") else None,
        "daily_j": daily_j,
        "vol_ratio_vs_ma5": round(vol_ratio, 4) if vol_ratio is not None else None,
        "vol_pctile_20d": round(vol_pctile, 1) if vol_pctile is not None else None,
        "stock_ret_20d_pct": round(stock_ret20, 2) if stock_ret20 is not None else None,
        "index_ret_20d_pct": round(index_ret20, 2) if index_ret20 is not None else None,
        "relative_strength_20d_pp": round(rs_20d, 2) if rs_20d is not None else None,
        "stop_loss_ref": {"price": stop_ref, "basis": f"近{STOP_LOOKBACK}日最低价"} if stop_ref else None,
        "patterns": {
            "bbi_above": bool(bbi.get("available") and bbi.get("close_above")),
            "j_low": bool(j_low),
            "volume_contraction": bool(vol_contraction),
            "reversal_k_candidate": reversal_k,
            "relative_strength_strong": bool(rs_strong),
        },
        # --- B1/CZ 策略对齐（阈值均为待回测参数，实际值随候选落盘） ---
        "wave": detect_wave_type(df),
        **weekly_j_state(df),
        "non_one_wave": check_non_one_wave(df),
        "repair_signals": check_repair_signals(df, index_df),
        "five_day_entry": check_five_day_entry(df),
        "volume_sustain": check_volume_sustain(df),
        "leader_volume": check_leader_volume(df),
        "three_lows": check_three_lows(df),
        "bottom_volume": check_bottom_volume(df),
        # --- 知行量价 + 出货识别（good_b1 / 出货五方式，阈值待回测，实际值落盘） ---
        "zhixing": zx,
        "ignition": ignition,
        "pullback_shrink": pullback_shrink,
        "ride_above_fast": ride_above_fast,
        "b1_ignition": {"hit": b1_ignition_hit, "zhixing_recent_golden": zx_recent_gold},
        "distribution": distribution,
        "macd_technics": check_macd_technics(df),
        "perfect_b1_fit": compute_perfect_b1_fit(df, daily_j, zx, pullback_shrink),
    }


def enrich(
    date: str,
    hits_data: Optional[dict] = None,
    ohlcv_loader=None,
    index_loader=None,
    universe_cfg: Optional[dict] = None,
    theme_min_match: Optional[int] = None,
) -> dict:
    """充实命中股。loader 可注入以便测试；所有失败结构化落盘，绝不 raise。"""
    hits_data = hits_data if hits_data is not None else load_hits(date)
    cfg = universe_cfg or {}
    min_list_days = int(cfg.get("min_list_days", 60))

    result: dict[str, Any] = {
        "date": date,
        "status": "ok",
        "degraded_reason": "",
        "candidates": [],
        "excluded": [],
    }

    if not hits_data or hits_data.get("status") == "unavailable":
        result["status"] = "unavailable"
        result["degraded_reason"] = (
            f"formula_hits_unavailable:{(hits_data or {}).get('degraded_reason', 'missing')}"
        )
        return result

    # 数据源当日一致性：formula_hits（TQ 在线公式评估）与本段（本地 vipdoc 日线）是两个
    # 独立来源。若命中清单不是当日产出（喂了旧文件/TQ 落后），标注 partial；无论如何，
    # 下游都用逐票 last_date==date 二次校验（见循环内 no_today_bar 剔除）兜底。
    hits_date = hits_data.get("date")
    if hits_date and hits_date != date:
        result["status"] = "partial"
        result["degraded_reason"] = _append_reason(
            result["degraded_reason"], f"formula_hits_date_mismatch:{hits_date}")
    result["signal_date_contract"] = (
        "公式命中(TQ在线)按最新交易日报出；本段以本地日线 last_date==date 逐票二次校验，"
        "不满足者计入 excluded(no_today_bar)，确保命中信号与所算指标同为当日。"
    )

    # 去重合并：code → {name, formula_ids}
    merged: dict[str, dict] = {}
    for f in hits_data.get("formulas", []):
        for h in f.get("hits", []):
            code6 = str(h.get("code", "")).split(".")[0].zfill(6)
            if not (code6.isdigit() and len(code6) == 6):
                continue
            entry = merged.setdefault(code6, {"code": code6, "name": h.get("name", ""), "formula_hits": []})
            if not entry["name"] and h.get("name"):
                entry["name"] = h["name"]
            if f.get("id") and f["id"] not in entry["formula_hits"]:
                entry["formula_hits"].append(f["id"])

    risk_high = load_risk_high_codes(date)
    holding = load_holding_codes()
    stock_theme, theme_map_available = build_stock_theme_map(
        min_match=theme_min_match if theme_min_match is not None else THEME_MIN_MATCH)
    if not theme_map_available:
        result["status"] = "partial"
        result["degraded_reason"] = _append_reason(
            result["degraded_reason"], "sector_map_unavailable")

    load_ohlcv = ohlcv_loader or (lambda c: local_tdx_data.get_ohlcv_table(c, count=260))
    load_index = index_loader or (lambda: local_tdx_data.get_ohlcv_table(INDEX_CODE, count=260))
    try:
        index_df = load_index()
    except Exception:  # noqa: BLE001
        index_df = None

    for code6 in sorted(merged):
        item = merged[code6]
        name = item["name"]

        def exclude(reason: str) -> None:
            result["excluded"].append({"code": code6, "name": name, "reason": reason})

        if cfg.get("exclude_bj", True) and code6.startswith(("4", "8", "920")):
            exclude("exclude_bj")
            continue
        if cfg.get("exclude_st", True) and "ST" in name.upper():
            exclude("st_stock")
            continue
        if code6 in risk_high:
            exclude("risk_high_priority")
            continue

        try:
            df = load_ohlcv(code6)
        except Exception:  # noqa: BLE001
            df = None
        if df is None or df.empty:
            exclude("no_local_kline")
            continue
        df = df.sort_values("date").reset_index(drop=True)
        last_date = str(df["date"].iloc[-1])[:10]
        if last_date != date:
            exclude(f"no_today_bar:last={last_date}")  # 停牌或本地数据未更新
            continue
        if len(df) < min_list_days:
            exclude(f"list_days<{min_list_days}")
            continue

        try:
            metrics = compute_metrics(df, index_df, code=code6)
        except Exception as exc:  # noqa: BLE001 —— 单股坏数据不中断批次
            exclude(f"metrics_error:{type(exc).__name__}:{str(exc)[:80]}")
            continue
        cand = {
            "code": code6,
            "name": name,
            "formula_hits": item["formula_hits"],
            "is_holding": code6 in holding,
            "list_days": len(df),
            "signal_date": last_date,
            **metrics,
        }
        # J<13 硬门槛（2026-07-22 用户决策）：全通道候选（公式与自选池一视同仁）
        # 必须先满足日 J<13，再谈完美图形贴合度；J 不可计算视同不满足。
        if cfg.get("j_low_required", J_GATE_REQUIRED_DEFAULT):
            dj = cand.get("daily_j")
            if dj is None or dj >= J_LOW_THRESHOLD:
                exclude(f"j_not_low:j={dj}")
                continue
        theme = stock_theme.get(code6)
        if theme:
            cand["theme_id"] = theme["theme_id"]
            cand["sector"] = theme["sector"]
            cand["sector_source"] = theme.get("sector_source", "")
        else:
            cand["theme_id"] = ""
            cand["sector"] = "未知"
            cand["sector_source"] = ""
        result["candidates"].append(cand)

    return result


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="screening 链第 2 段：命中股充实+模式识别（确定性）")
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    registry = _load_json(
        Path(__file__).resolve().parents[2] / "00_governance" / "SCREEN_FORMULA_REGISTRY.json", {}
    )
    result = enrich(args.date, universe_cfg=registry.get("universe") or {},
                    theme_min_match=(registry.get("theme_mapping") or {}).get("min_match"))

    SCREENING_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SCREENING_DIR / f"{args.date}_candidates_enriched.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    summary = {
        "date": args.date,
        "status": result["status"],
        "degraded_reason": result["degraded_reason"],
        "candidates": len(result["candidates"]),
        "excluded": len(result["excluded"]),
        "output": str(out_path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
