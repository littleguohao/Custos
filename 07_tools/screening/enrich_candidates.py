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
  （以上阈值为默认值，均可经 B1_* 环境变量覆盖 —— 唯一来源 `b1_thresholds`。）

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
import inspect
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
for p in (TOOLS_DIR, TOOLS_DIR / "local_tdx", TOOLS_DIR / "market_timing", TOOLS_DIR / "screening"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
_FACTORS_DIR = str(Path(__file__).resolve().parents[1] / "factors")
if _FACTORS_DIR not in sys.path:
    sys.path.insert(0, _FACTORS_DIR)   # 因子层：见 factors/__init__.py
# 因子实现已抽到 factors/ 各自成模块（2026-08-06）——**全项目唯一一份**，
# 本模块通过调用访问。常量随因子走（`WAVE_*` 在 wave_type、`DIST_*` 在 distribution…），
# 需要它们的地方从对应因子模块导入，不要在这里再抄一份。
from _util import ohlcv_arrays as _ohlcv_arrays  # noqa: E402
from wave_type import (WAVE_MIN_BARS, _find_rally_segment,  # noqa: E402
                       detect_wave_type)
#   ↑ `WAVE_MIN_BARS` / `_find_rally_segment` 在本模块**别处也被用到**（不只 detect_wave_type），
#     所以一并导入。常量与助手跟着因子走、由因子模块拥有，这里只引用。
from perfect_b1_fit import compute_perfect_b1_fit  # noqa: E402
from b1_pullback_fit import compute_b1_pullback_fit  # noqa: E402
from distribution import detect_distribution  # noqa: E402




from paths import (DATA, RISK_DIR, SCREEN_FORMULA_REGISTRY_FILE, SECTORS_DIR,
                   TRADES_DIR)  # noqa: E402
import concept_tags  # noqa: E402
import signal_labels  # noqa: E402
import local_tdx_data  # noqa: E402
import s_shape as s_shape_mod  # noqa: E402
import financials as financials_mod  # noqa: E402
import sector_phase as sector_phase_mod  # noqa: E402
# 死代码清理（2026-08-08）：本地 `_j_series` 包装已删 —— 唯一调用方早已搬走
# （全项目 grep 确认无引用），本模块的 J 走下方 `kdj`（indicators 共享实现，
# 内部 fill_na=50，行为不变）；`macd` 导入同步删除（check_macd_technics 自己
# 用 ema 算 DIF/DEA，从未调用它）。
from indicators import bbi_state, ema, kdj, resample, zhixing_state, _infer_price_limit # noqa: E402
from contracts import require  # noqa: E402

SCREENING_DIR = DATA / "screening"
SECTOR_CODE_MAP = SECTORS_DIR / "sector_code_map.json"
INDEX_CODE = "999999"  # 上证指数 vipdoc 代码（reader.daily 里 000001 是平安银行）

# 沪深 A 股代码前缀白名单（与 formula_screen/manual_pools 同一份规则）。
# 本段是候选进 StockPool 契约前的最后一道过滤：上游只有 build_universe 走了白名单，
# 自选池与外部注入的命中都没走，而这里此前只排 BJ 前缀 → ETF(51/15/16)、可转债(11/12/13)、
# B股(900/2xx)、指数能进 A-D 分层并被当成"可买个股"（审计 B10）。
_A_SHARE_RE = re.compile(r"^(60[0-5]|688|00[0-3]|30[0-3])\d{3}$")
_BJ_PREFIX = ("4", "8", "920")

# J 低位/极致缩量三阈值与反转 K 区间同源：`b1_thresholds`（L0，env 可配）。
# ⚠️ 这里原先本地硬编码同名常量（J_LOW_THRESHOLD=13.0 / VOL_RATIO_MAX=0.5 /
# VOL_PCTILE_MAX=10.0）：REVERSAL_* 收敛后这三个仍留在本地，设 B1_J_LOW 只改到
# 持仓链、选股链不动 —— 2026-08-07 补收敛。默认值见 b1_thresholds。
from b1_thresholds import (J_LOW_THRESHOLD, VOL_PCTILE_MAX,  # noqa: E402
                           VOL_RATIO_MAX)

# 默认日线加载根数（get_ohlcv_table(count=...)）。它同时是 list_days 的**上界**：
# 加载器内部 `df.tail(count)`，所以 len(df)==OHLCV_LOAD_BARS 只说明"至少这么多根"，
# 不是真实上市日数（审计：CZ 的 250 日窗口在 260 根里只剩 10 根余量）。
OHLCV_LOAD_BARS = 260


def j_below_threshold(j: Any, threshold: float = J_LOW_THRESHOLD) -> bool:
    """J 是否满足 `J < threshold` 的硬门槛。**NaN/None/非数值一律不满足**。

    审计：原判否写作 `dj is None or dj >= J_LOW_THRESHOLD`。IEEE 754 下
    `float("nan") >= 13` 为 False，`nan is None` 也为 False —— 于是"J 算不出来"
    被当成"J<13 满足买点"直接放行，坏数据成了最好的数据。KDJ 目前走
    `rsv.fillna(50)` 不易产出 NaN，但 daily_j 也可能来自落盘 JSON / 别的口径，
    这道门槛是全通道硬门槛，不能依赖上游恰好不脏。
    """
    if j is None or isinstance(j, bool):
        return False
    try:
        v = float(j)
    except (TypeError, ValueError):
        return False
    if not np.isfinite(v):          # NaN / ±inf 均视为不可用
        return False
    return v < threshold
RS_STRONG_PP = 3.0           # 20日相对强度 >= +3pp
# 反转K的收盘涨幅区间：**不对称**（B1_w.pdf「分歧转一致的反转K」与「如何筛选最强壮的
# B1宝宝」两处都明确写「涨幅为 -2% 到 1.8%」）。此前实现与治理文档都写成对称 ±2%，
# 上界宽了 0.2pp。不是刻意收紧门槛，是按材料原文纠偏。
# ⚠️ **对称 ±2%（owner 2026-08-06 拍板）。**
# 此前是不对称 -2.0 ~ +1.8（2026-08-04 按 B1_w.pdf 纠偏引入，材料两处独立写明「-2% 到 1.8%」）。
# 改回对称的直接动因：研究侧（factors/reversal_quality，原 backtest_factors.REVK_CHG_PCT）
# 一直用对称 ±2%，两边口径不一致 ⇒ **reversal_quality 与 live 的反转K不是同一个东西**，
# 而 R2 的结论建立在前者上。统一到对称后两边可比。
# 🔁 **这一改反转了 R16（材料纠偏）第 ④ 条**，若要回退：把下面三个常量恢复为
#    MIN=-2.0 / MAX=1.8，并同步 01_swing_rules.md §三.3 注与本文件的判定式。
# 📐 **可配置**（owner 2026-08-06）：默认对称 ±2%，可用环境变量覆盖以验证不同区间的效果。
#    `B1_REVK_CHG_PCT=2.5` → ±2.5%；`B1_REVK_CHG_MIN=-2 B1_REVK_CHG_MAX=1.8` → 回到不对称。
#    ⚠️ 覆盖值影响 **live 两条链**（选股链 + 持仓链，2026-08-07 收敛到 b1_thresholds），
#    但**不影响研究侧** `factors/reversal_quality` —— 那份刻意钉死以复现既有回测数字
#    （R2 P1 重跑清单依赖它们）。两边默认值相同，覆盖时只有 live 会变。
#    ⚠️ 原写「两边读同一处」，实测不成立（只是默认值恰好相同），2026-08-07 订正。
# ⚠️ 阈值已收敛到 `b1_thresholds`（L0）—— 这里只做转出，不再自己读环境变量。
#    2026-08-07 实测：原先「可配置」只覆盖本文件（选股链），持仓链
#    （technical_monitor + b1_holding_state）硬编码 ±2 与 j<13，改 env 后两链分歧。
from b1_thresholds import (REVERSAL_AMPLITUDE_PCT, REVERSAL_CHANGE_MAX_PCT,  # noqa: E402
                           REVERSAL_CHANGE_MIN_PCT, REVERSAL_CHANGE_PCT,
                           change_in_range)
STOP_LOOKBACK = 10           # 建议止损位：近10日最低价

# 概念标签命中主题所需的最小标签数。默认 1＝历史行为（命中1个语义标签即归入该主题）；
# 提高到 2+ 可要求更强证据、降低子串过度匹配，可经 registry.theme_mapping.min_match 覆盖。
THEME_MIN_MATCH = 1

# --- B1/CZ 策略对齐参数 -------------------------------------------------
# 以下阈值全部标注"待回测参数"：策略原文（B1 §四、CZ §九/§14.6/§十六）
# 要求阈值可配置、实际值随候选落盘，不得静默使用；完成样本回测前不得
# 视为已校准。口径出处见 00_governance/contracts/SCREENING_WORKFLOW.md "策略对齐"章。

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


# --- 完美 B1 图形贴合度（good_b1 图集共性特征的梯度评分）待回测参数 ---
# 2026-07-22 用户决策：J<13 为全通道硬门槛（公式与自选池一视同仁），
# 在 J<13 基础上按贴合度梯度给分，越符合完美图形分数越高。
J_GATE_REQUIRED_DEFAULT = True    # J<13 硬门槛默认开（registry universe.j_low_required 可覆盖）
DKS_MA_WINDOWS = (14, 28, 57, 114)  # DKS=(MA14+MA28+MA57+MA114)/4，与 technical_monitor.zhixing_state 同参

# --- 完美B1「缩量回踩超卖企稳」买弱指纹（10只确认赢家反标，见 worklog）---
# recall 达标(10/10)，但全市场回测证伪：周线交易模拟(止损+BBI出场)加0AMV做多+25bps成本后
# 期望 -0.42%/笔，劣于 baseline(无差别进场) 的 +0.96%/笔 —— 作进场过滤反而有害(专挑弱势、
# 排除了做多区间的突破赢家)。故仅作**描述性证据**落盘、绝不作买入依据、不驱动分层。参数下方保留。

# --- MACD 十大技术（macd十大技术精讲）待回测参数 ---
MACD_SWING_FRACTAL = 2           # 摆动高/低点分型：左右各 N 根确认
MACD_DIV_LOOKBACK = 60           # 背离观察窗口（日）
MACD_OVEREXT_PCTL = 0.9          # 开口/空间拐离：|DIF| 近 120 日分位上限
MACD_OVEREXT_WIN = 120           # 拐离分位窗口（日）

# --- 正交因子（非量价形态）待回测参数 ---
# 方向A(2026-07-23)：全市场回测证实突破式打分非短周期 alpha，转接正交维度。
LIQUIDITY_WIN = 20               # 待回测：近N日均成交额窗口
LIQUIDITY_FLOOR_YI = 0.5         # 待回测：均成交额底线(亿元)，低于→low_liquidity(默认仅flag)
FUND_FLOW_SECTOR_MIN_NAME = 2    # 板块名整名匹配所需最小长度（短于此视为不可判，不给分）


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


def build_stock_industry_map() -> dict[str, str]:
    """每股 → TDX 官方细分行业名（881xxx，每股恰好一个；2026-08-04 实测 5546 只零冲突）。

    这是**权威的每股行业归属**（建设银行→全国性银行、牧原股份→养殖业、
    共进股份→通信设备），与 9 大主题族（``sector``/``theme_id``，聚合层）是两个口径：
    行业是展示层（候选表「板块」列），主题族是聚合层（主线指纹/相位/资金流）。
    数据来自最新 ``*_tq_sector_map.json``；取不到返回 {}（调用方按"未知"降级，不 raise）。
    """
    out: dict[str, str] = {}
    try:
        for s in (latest_tq_sector_map().get("sectors") or []):
            if s.get("category") != "sub_industry":
                continue
            name = str(s.get("name") or "").strip()
            if not name:
                continue
            for raw in s.get("stocks") or []:
                out.setdefault(str(raw).split(".")[0].zfill(6), name)
    except Exception:  # noqa: BLE001
        return {}
    return out


def _match_theme_tags(stock_tags: list[str], semantic_tags: list[str]) -> list[str]:
    """个股概念标签与主题语义标签的命中列表（双向子串，长的单向≥3字）。"""
    matched = []
    for st in semantic_tags:
        for c in stock_tags:
            if st == c or st in c or (len(c) >= 3 and c in st):
                matched.append(st)
                break
    return matched


def build_stock_theme_map(min_match: int = THEME_MIN_MATCH,
                          codes: Optional[set] = None) -> tuple[dict[str, dict], bool]:
    """股 → 主题方向（theme_id/sector 名）。

    优先用 miscinfo 概念标签（concept_tags，每股官方概念）匹配
    sector_code_map.json 各主题的 semantic_tags——准确度远高于 880 反查；
    标签文件缺失时回退 tq_sector_map 成分股反查（v1，已知存在错配）。
    min_match：概念路径下命中主题所需的最小语义标签数（默认 1；提高可降低过度匹配）。
    codes：只为这批代码建图（通常几十只候选）。默认 None＝全市场（向后兼容）。
      审计：调用方只用 `stock_theme.get(code6)` 查几十只票，却让「全市场 5000 股 ×
      主题数 × 语义标签 × 个股标签」四层子串匹配跑满——把候选集传进来即可省掉两个量级。
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

    # 标签仍走 load_tags():它是既有的注入点,改成只调 load_tags_meta 会让所有
    # monkeypatch(load_tags) 的测试静默走到 fallback 分支。元数据另取一次,
    # 拿不到就当"无元数据"处理(不告警),行为与改动前一致。
    tags_map = concept_tags.load_tags()
    tags_meta = concept_tags.load_tags_meta()[1] if hasattr(concept_tags, "load_tags_meta") else {}
    if tags_meta.get("stale"):
        # 概念标签退化慢,陈旧仍可用,但必须留痕:不能让"上周的标签"以当日身份
        # 进入主线指纹,否则板块族密度榜会指向一条已经冷掉的主线(审计 C6)。
        print(f"[WARN] 概念标签陈旧(date={tags_meta.get('date')}, "
              f"requested={tags_meta.get('requested_date')})：主线指纹据此生成,"
              f"仅作情境参考", file=sys.stderr)

    def _scan(items) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for code6, stock_tags in items:
            best_matched: list[str] = []
            best_theme: dict | None = None
            for t in themes:
                matched = _match_theme_tags(stock_tags, t.get("semantic_tags") or [])
                if len(matched) > len(best_matched):
                    best_matched, best_theme = matched, t
            if best_theme and len(best_matched) >= min_match:
                out[code6] = {
                    "theme_id": best_theme.get("theme_id", ""),
                    "sector": best_theme.get("theme_name", ""),
                    "matched_tags": best_matched,
                    "match_count": len(best_matched),
                    "sector_source": "concept_tags",
                }
        return out

    if tags_map:
        if codes is None:
            stock_theme = _scan(tags_map.items())
        else:
            stock_theme = _scan((c, tags_map[c]) for c in codes if c in tags_map)
        if stock_theme:
            return stock_theme, True
        if codes is not None and _scan(tags_map.items()):
            # 这批候选一个都没匹配上，但概念路径本身是可用的（全市场能建出图）→
            # 不得偷偷回退 880 反查（那条路只在"概念标签不可用"时才走，错配已知）。
            # 全市场重扫只在这个退化分支发生，正常路径不付这个代价。
            return {}, True

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







def weekly_j_state(df) -> dict[str, Any]:
    """周线 J（B1 §四.1 主线口径：周线 J<13 为周线 B1 候选）。

    ``weekly_j_available`` 与 ``available`` 同值：本 dict 会被 `**weekly_j_state(df)`
    摊进 compute_metrics 的返回值，一个裸 ``available`` 键直接落到**候选顶层**、
    读起来像"这个候选可用"（审计）。compute_metrics 只摊 weekly_ 前缀的键；
    ``available`` 保留给直接调用方（既有测试/脚本）。
    weekly_j / weekly_j_low 本来就在候选顶层，下游 score_candidates 读得到。
    """
    weekly = resample(df, "W-FRI")
    w = kdj(weekly)
    if not w.get("available"):
        return {"available": False, "weekly_j_available": False,
                "weekly_j": None, "weekly_j_low": False}
    return {"available": True, "weekly_j_available": True, "weekly_j": w["j"],
            "weekly_j_low": j_below_threshold(w["j"])}


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


def check_repair_signals(df, index_df, kdj_state: Optional[dict] = None) -> dict[str, Any]:
    """B1 修复信号（B1 §四.2）：输出命中数组+各信号实际值。

    kdj_state 可传调用方已算好的 kdj(df)（compute_metrics 就有一份），避免同一只票
    把日线 KDJ 算两遍；不传则自己算，结果一致。
    """
    close, _, _, vol = _ohlcv_arrays(df)
    n = len(df)
    j = kdj_state if kdj_state is not None else kdj(df)
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
    # 与 01_cognition_framework.md §14.6 一致：峰值日后窗口内"逐日"量都必须 ≥ 峰值×55%
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

    # 摆动高/低点（左右各 MACD_SWING_FRACTAL 根分型，右确认避免未来函数）。
    # 唯一或近唯一峰/谷：窗口内其余 2f 根中至少 2f-1 根严格更低/更高
    #（允许至多 1 根等值，兼容双顶平台；>=2f-1 而非 <=，写反会导致唯一峰永不被检出）。
    f = MACD_SWING_FRACTAL
    w0 = max(f, n - MACD_DIV_LOOKBACK)
    swing_hi = [i for i in range(w0, n - f)
                if close[i] == close[i - f:i + f + 1].max() and (close[i - f:i + f + 1] < close[i]).sum() >= 2 * f - 1]
    swing_lo = [i for i in range(w0, n - f)
                if close[i] == close[i - f:i + f + 1].min() and (close[i - f:i + f + 1] > close[i]).sum() >= 2 * f - 1]

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
    overextended = {"hit": bool(pctl is not None and pctl >= MACD_OVEREXT_PCTL
                                and h0 * dif_last > 0),  # “下面还有柱体”＝柱体与 DIF 同号
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











def check_liquidity(df, win: int = LIQUIDITY_WIN) -> dict[str, Any]:
    """流动性：近 win 日均成交额（亿元）。仅计算值，底线判定在 score 层（可配）。"""
    if "amount" not in df.columns or len(df) < 5:
        return {"available": False}
    amt = df["amount"].astype(float).to_numpy()
    avg = float(amt[-win:].mean())
    return {"available": bool(avg > 0), "avg_amount_yi": round(avg / 1e8, 4),
            "avg_amount": round(avg, 0), "window": win}


def load_fund_flow(date: str, cumulative_days: int = 1, market_dir=None) -> dict[str, Any]:
    """读 collect_fund_flow 落盘的每日资金流快照（东财）。

    cumulative_days<=1：仅读 {date}_fund_flow_rank.json（现状）。
    cumulative_days>1：累加 <=date 的最近 N 个每日快照的主力净流入（按 code/板块名聚合）——
    单日快照噪声大，多日累计更稳（资金流本身无历史存档，只能就已落盘的每日文件累积）。
    market_dir 可注入以便测试。缺失干净降级。
    """
    mdir = Path(market_dir) if market_dir else (DATA / "market")
    if cumulative_days <= 1:
        data = _load_json(mdir / f"{date}_fund_flow_rank.json", {})
        stock_ranks = [data.get("stock_rank") or []]
        sector_maps = [data.get("sector_rank") or {}]
        files_used = [date] if data else []
    else:
        allf = sorted(p for p in mdir.glob("*_fund_flow_rank.json") if p.name[:10] <= date)
        use = allf[-cumulative_days:]
        files_used = [p.name[:10] for p in use]
        stock_ranks, sector_maps = [], []
        for p in use:
            d = _load_json(p, {})
            stock_ranks.append(d.get("stock_rank") or [])
            sector_maps.append(d.get("sector_rank") or {})

    by_code: dict[str, dict] = {}
    for sr in stock_ranks:
        for s in sr:
            c = str(s.get("code", "")).split(".")[0].zfill(6)
            if not (c.isdigit() and len(c) == 6):
                continue
            e = by_code.setdefault(c, {"code": c, "name": s.get("name", ""),
                                       "main_net_inflow": 0.0, "days": 0,
                                       # 单日快照才有意义的日内占比；多日累计无法相加 → None
                                       "main_net_pct": (s.get("main_net_pct") if cumulative_days <= 1 else None)})
            v = s.get("main_net_inflow")
            if isinstance(v, (int, float)):
                e["main_net_inflow"] += v
            e["days"] += 1
    sec_agg: dict[str, dict] = {}
    for sm in sector_maps:
        for item in (sm.get("concept") or []) + (sm.get("industry") or []):
            nm = str(item.get("name") or "")
            if not nm:
                continue
            e = sec_agg.setdefault(nm, {"name": nm, "main_net_inflow": 0.0})
            v = item.get("main_net_inflow")
            if isinstance(v, (int, float)):
                e["main_net_inflow"] += v
    return {"available": bool(by_code or sec_agg), "by_code": by_code,
            "sectors": list(sec_agg.values()), "cumulative_days": cumulative_days,
            "files_used": files_used}


def sector_name_matches(flow_name: str, sector_name: str,
                        min_len: int = FUND_FLOW_SECTOR_MIN_NAME) -> bool:
    """资金流板块名 vs 候选主题名是否算同一板块（**整名双向包含**，不截前缀）。

    审计：原实现除了 `nm in sector_name` 还允许 `nm[:2] in sector_name`——2 个汉字
    的前缀在 A 股板块命名里几乎必然撞车：「工程建设」↔「工程机械」、「医疗器械」↔
    「医疗服务」、「通信设备」↔「通信服务」都会互相命中。而板块净流入一旦命中就是
    资金意图 **+2 分**，直接把候选从"弱"抬到"中/强"档并改变 A/B/C/D 分层，
    所以这里必须精确：匹配不上就不给分，宁缺勿滥。
    """
    nm, sn = str(flow_name or "").strip(), str(sector_name or "").strip()
    if len(nm) < min_len or len(sn) < min_len:
        return False
    return nm in sn or sn in nm


def fund_flow_of(code6: str, sector_name: str, ff: dict) -> dict[str, Any]:
    """个股 + 板块资金流（正交于量价）：个股是否在主力净流入榜且为净流入、
    所属主题板块是否净流入。榜/文件缺失时干净降级。"""
    if not ff or not ff.get("available"):
        return {"available": False}
    entry = (ff.get("by_code") or {}).get(code6)
    main_inflow = entry.get("main_net_inflow") if entry else None
    in_rank_positive = bool(entry is not None and isinstance(main_inflow, (int, float)) and main_inflow > 0)
    sec_match = None
    if sector_name and sector_name != "未知":
        for s in ff.get("sectors") or []:
            nm = str(s.get("name") or "")
            if not sector_name_matches(nm, sector_name):
                continue
            if sec_match is None or (s.get("main_net_inflow") or 0) > (sec_match.get("main_net_inflow") or 0):
                sec_match = s
    sector_inflow = (sec_match or {}).get("main_net_inflow")
    sector_inflow_positive = bool(isinstance(sector_inflow, (int, float)) and sector_inflow > 0)
    return {
        "available": True,
        "in_rank": entry is not None,
        "main_net_inflow": main_inflow,
        "main_net_pct": (entry or {}).get("main_net_pct") if entry else None,
        "in_rank_positive": in_rank_positive,
        "sector_matched": (sec_match or {}).get("name"),
        "sector_main_net_inflow": sector_inflow,
        "sector_inflow_positive": sector_inflow_positive,
    }


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
        and change_in_range(change_pct)
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
    # 指标去重：日线 KDJ 与 MACD 各只算一次，再喂给下游检测器（审计：kdj×4/macd×3）。
    macd_technics = check_macd_technics(df)

    # 研究因子的**信号标注**（三态 hit/miss/unavailable）。只标注、不参与打分分层——
    # 这些因子还没跑过真实回测，而结论#15 的教训是"识别有术、盈利无效"。
    # 复用上面已算的 zx / distribution / daily_j / weekly_j，避免重复 resample 与 kdj。
    _wk = weekly_j_state(df)
    try:                                    # 平台回踩:与下方证据层同一份检测,延迟导入
        from platform_pullback import detect_platform_pullback  # noqa: PLC0415
        _plat = detect_platform_pullback(df)
    except Exception:  # noqa: BLE001
        _plat = None
    signals = signal_labels.compute_signals(
        df, code, daily_j=daily_j,
        weekly_j_low=_wk.get("weekly_j_low"),
        weekly_j_available=_wk.get("weekly_j_available"),
        zx=zx, distribution=distribution, platform_pullback=_plat)

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
        # 只摊 weekly_ 前缀键：裸 available 会落到候选顶层被误读成"候选可用"（审计）
        **{k: v for k, v in _wk.items() if k.startswith("weekly_")},
        "signals": signals,
        "non_one_wave": check_non_one_wave(df),
        "repair_signals": check_repair_signals(df, index_df, kdj_state=j),
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
        "macd_technics": macd_technics,
        "perfect_b1_fit": compute_perfect_b1_fit(df, daily_j, zx, pullback_shrink,
                                                 macd_state=macd_technics),
        # TODO(策略口径,需 owner 拍板 —— 审计【建议优化】14):这两条是**对称的两笔浪费**,
        # 但都涉及选股行为,本批只留痕不改:
        #   ① b1_pullback_fit 已被全市场回测**证伪**(见 compute_b1_pullback_fit docstring:
        #      作进场过滤期望 -0.42%/笔 < baseline +0.96%/笔),仅作描述性证据落盘、不驱动
        #      分层,却仍逐票计算(7 个分项 + 一条自建 J 序列)。若确认不再需要这份证据,
        #      去掉即可省掉每票一次全序列 KDJ；一旦删除,落盘契约少一个字段,下游报告
        #      与历史候选 JSON 的可比性会断,故必须由 owner 决定。
        #   ② compute_s_reversal(买弱/反转分,s_shape.py)**根本没算**——它才是与 B1
        #      "回调买入"同向的打分器(compute_s_shape 是突破式买强),backtest_factors 里
        #      作为 --scorer s_reversal 已在跑,但候选落盘里没有它,分层拿不到这维证据。
        #      要不要加进 compute_metrics(以及是否进 technical_score)同样是策略口径决定。
        "b1_pullback_fit": compute_b1_pullback_fit(df),
        "s_shape": s_shape_mod.compute_s_shape(df, code),
        "liquidity": check_liquidity(df),
    }


def enrich(
    date: str,
    hits_data: Optional[dict] = None,
    ohlcv_loader=None,
    index_loader=None,
    universe_cfg: Optional[dict] = None,
    theme_min_match: Optional[int] = None,
    fund_flow_days: int = 1,
    financials_cfg: Optional[dict] = None,
    sector_phase_cfg: Optional[dict] = None,
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

    # ST 硬排除的唯一依据是名称（`"ST" in name.upper()`）。formula_screen 早就把名称表
    # 的可用性落盘成 st_filter，但**此前没人消费**：名称表在线+缓存双挂时 name 全空，
    # `"ST" in ""` 为假 → 全部 ST 股静默通过硬排除，status 还是 ok（审计 B5）。
    # 这里 fail-closed：名称表不可用 → 无名候选一律剔除（无法证明非 ST），并把降级
    # 原因传导给下游；字段缺失（旧文件/测试注入）按 ok 处理，保持向后兼容。
    exclude_st = bool(cfg.get("exclude_st", True))
    st_filter = str(hits_data.get("st_filter", "ok") or "ok")
    st_filter_broken = exclude_st and st_filter != "ok"
    result["st_filter"] = st_filter
    if st_filter_broken:
        if result["status"] == "ok":
            result["status"] = "partial"
        result["degraded_reason"] = _append_reason(
            result["degraded_reason"],
            f"st_filter_unavailable:{st_filter}(名称表不可用 → 无名候选按 st_unverified 剔除)")

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
    fund_flow = load_fund_flow(date, cumulative_days=fund_flow_days)
    fin_cfg = financials_cfg or {}
    fin_enabled = bool(fin_cfg.get("enabled"))
    fin_df = financials_mod.load_financials(fin_cfg.get("report_period", "")) if fin_enabled else None
    fin_colmap = dict(fin_cfg.get("columns") or {})
    if fin_enabled and fin_df is not None and fin_cfg.get("auto_map", True):
        _cm = financials_mod.auto_colmap(getattr(fin_df, "columns", []))
        _cm.update(fin_colmap)   # 显式 registry.columns 按字段覆盖自动识别
        fin_colmap = _cm
    # 板块相位(hint,不封顶)：best-effort 构建 resolver；数据缺失则跳过
    sp_cfg = sector_phase_cfg or {}
    sp_resolve = None
    if sp_cfg.get("enabled", True):
        try:
            mpath = Path(sp_cfg.get("members_path")
                         or (DATA / "market" / "sector_members.json"))
            idir = Path(sp_cfg.get("index_dir") or (DATA / "market" / "sector_index"))
            if mpath.is_file() and idir.is_dir():
                members = _load_json(mpath, {})
                if members:
                    sp_resolve = sector_phase_mod.build_phase_resolver(idir, members)
        except Exception:  # noqa: BLE001
            sp_resolve = None
    # 只为本批候选建主题图（全市场四层子串匹配纯浪费，见 build_stock_theme_map）。
    # codes 关键字用 signature 探测后再传：既有测试把 build_stock_theme_map
    # monkeypatch 成 `lambda min_match=None: ...`，硬传会 TypeError 打挂整段。
    _bstm_kwargs: dict[str, Any] = {
        "min_match": theme_min_match if theme_min_match is not None else THEME_MIN_MATCH}
    try:
        if "codes" in inspect.signature(build_stock_theme_map).parameters:
            _bstm_kwargs["codes"] = set(merged)
    except (TypeError, ValueError):      # 无法取签名（C 实现/内建）→ 退回全市场
        pass
    stock_theme, theme_map_available = build_stock_theme_map(**_bstm_kwargs)
    if not theme_map_available:
        result["status"] = "partial"
        result["degraded_reason"] = _append_reason(
            result["degraded_reason"], "sector_map_unavailable")
    # 每股官方细分行业（881xxx，展示层「板块」列；与主题族聚合层并存，取不到全"未知"）
    stock_industry = build_stock_industry_map()

    load_ohlcv = ohlcv_loader or (lambda c: local_tdx_data.get_ohlcv_table(c, count=OHLCV_LOAD_BARS))
    load_index = index_loader or (lambda: local_tdx_data.get_ohlcv_table(INDEX_CODE, count=OHLCV_LOAD_BARS))
    # 指数序列与个股同等对待：**必须排序 + 必须当日**（审计 B7）。
    # 此前指数只有一个裸 try/except：
    #   1) 加载失败 → index_df=None，rs_20d/rs_turn 整列静默变 None/False，
    #      relative_strength_strong 一律不命中，报告里看不出"相对强度这维今天是废的"；
    #   2) 无 last_date==date 校验 → 指数停在 T-1（vipdoc 未更新/节前抓的旧文件）时，
    #      拿 T-1 的指数 20 日涨幅去减当日个股 20 日涨幅＝**错窗口相减**，rs 偏差直接
    #      喂给 rs_strong 与 capital_intent，比没有更危险；
    #   3) 无 sort_values → mootdx Reader 不保证顺序，iloc[-1] 可能取到中间某天。
    # 现在：坏/旧的指数一律降级为"不可用"（rs 置 None，不做错窗口相减），并把原因写进
    # index_status + degraded_reason，让下游能归因。
    index_status = "ok"
    try:
        index_df = load_index()
    except Exception as exc:  # noqa: BLE001
        index_df, index_status = None, f"index_load_error:{type(exc).__name__}"
    if index_status == "ok":
        if index_df is None or getattr(index_df, "empty", True):
            index_df, index_status = None, "index_missing"
        else:
            try:
                index_df = index_df.sort_values("date").reset_index(drop=True)
                index_last = str(index_df["date"].iloc[-1])[:10]
            except Exception as exc:  # noqa: BLE001
                index_df, index_status = None, f"index_bad_frame:{type(exc).__name__}"
            else:
                if index_last != date:
                    index_df, index_status = None, f"index_stale:last={index_last}"
    result["index_status"] = index_status
    result["index_code"] = INDEX_CODE
    if index_status != "ok":
        if result["status"] == "ok":
            result["status"] = "partial"
        result["degraded_reason"] = _append_reason(
            result["degraded_reason"],
            f"{index_status}(上证指数不可用/非当日 → 20日相对强度整列置空，不做错窗口相减)")

    for code6 in sorted(merged):
        item = merged[code6]
        name = item["name"]

        def exclude(reason: str) -> None:
            result["excluded"].append({"code": code6, "name": name, "reason": reason})

        if code6.startswith(_BJ_PREFIX):
            # BJ 有独立开关（可配置放开），单列一类，不与 not_a_share 混同
            if cfg.get("exclude_bj", True):
                exclude("exclude_bj")
                continue
        elif not _A_SHARE_RE.match(code6):
            exclude("not_a_share")          # ETF/可转债/B股/指数：不是可买个股（审计 B10）
            continue
        if exclude_st:
            if "ST" in name.upper():
                exclude("st_stock")
                continue
            if st_filter_broken and not name.strip():
                # fail-closed：名称表挂了就无法证明这只票不是 ST，宁可漏也不能错放（审计 B5）
                exclude("st_unverified:name_map_unavailable")
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
            # list_days 是**加载到的 K 线根数**，不是真实上市日数：默认加载器
            # `get_ohlcv_table(count=OHLCV_LOAD_BARS)` 内部 tail(count) 截断，
            # 所以取到上界时它只代表"≥260 个交易日"。硬排除（<min_list_days，默认60）
            # 不受影响（60 远小于 260），但任何"上市多久/够不够 250 日窗口"的判断
            # 都必须先看 list_days_exact，否则会把老票误当刚上市（审计）。
            "list_days": len(df),
            "list_days_exact": len(df) < OHLCV_LOAD_BARS,
            "list_days_basis": ("loaded_bars" if len(df) < OHLCV_LOAD_BARS
                                else "loaded_bars_censored"),
            "signal_date": last_date,
            **metrics,
        }
        # J<13 硬门槛（2026-07-22 用户决策）：全通道候选（公式与自选池一视同仁）
        # 必须先满足日 J<13，再谈完美图形贴合度；J 不可计算视同不满足。
        if cfg.get("j_low_required", J_GATE_REQUIRED_DEFAULT):
            dj = cand.get("daily_j")
            if not j_below_threshold(dj):
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
        cand["industry"] = stock_industry.get(code6, "未知")
        cand["fund_flow"] = fund_flow_of(code6, cand["sector"], fund_flow)
        if sp_resolve is not None:
            cand["sector_phase"] = sp_resolve(code6)     # 板块相位 hint(不封顶,证据层)
        try:                                             # 平台突破回踩形态(证据层,不驱动分层)
            from platform_pullback import detect_platform_pullback  # noqa: PLC0415
            pp = detect_platform_pullback(df)
            if pp:
                cand["platform_pullback"] = pp           # {platform_high, breakout_date, pullback_low, ...}
        except Exception:  # noqa: BLE001
            pass
        if fin_enabled and fin_colmap:
            # 财务维度(CZ抄底代理)：最佳努力落盘证据层，不驱动分层
            cand["financials"] = financials_mod.financial_factor(
                code6, fin_df, fin_colmap, price=cand.get("close"))
        result["candidates"].append(cand)

    # 名称表挂掉导致"筛完 0 只"必须报 unavailable 而不是 partial：score_candidates 只对
    # unavailable 整池降级，partial + 空 candidates 会被读成"今天市场没有符合条件的票"。
    if st_filter_broken and not result["candidates"] and any(
        str(x.get("reason", "")).startswith("st_unverified") for x in result["excluded"]
    ):
        result["status"] = "unavailable"

    return result


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="screening 链第 2 段：命中股充实+模式识别（确定性）")
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    registry = _load_json(
        SCREEN_FORMULA_REGISTRY_FILE, {}
    )
    result = enrich(args.date, universe_cfg=registry.get("universe") or {},
                    theme_min_match=(registry.get("theme_mapping") or {}).get("min_match"),
                    fund_flow_days=int((registry.get("fund_flow") or {}).get("cumulative_days", 1)),
                    financials_cfg=registry.get("financials") or {},
                    sector_phase_cfg=registry.get("sector_phase") or {})

    SCREENING_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SCREENING_DIR / f"{args.date}_candidates_enriched.json"
    require("candidates_enriched", result)
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
