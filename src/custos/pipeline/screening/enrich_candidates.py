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

    uv run python src/custos/pipeline/screening/enrich_candidates.py --date YYYY-MM-DD

输出 ``data/screening/{date}_candidates_enriched.json``。
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 因子实现已抽到 factors/ 各自成模块（2026-08-06）——**全项目唯一一份**，
# 本模块通过调用访问。常量随因子走（`WAVE_*` 在 wave_type、`DIST_*` 在 distribution…），
# 需要它们的地方从对应因子模块导入，不要在这里再抄一份。
from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays  # noqa: E402
from custos.core.factors.wave_type import detect_wave_type  # noqa: E402

#   ↑ v0.86（因子化批 B）前这里还导 `WAVE_MIN_BARS` / `_find_rally_segment`
#     （check_non_one_wave 用）——它们已随结构族迁入 factors/b1_structure.py。
from custos.core.factors.perfect_b1_fit import compute_perfect_b1_fit  # noqa: E402
from custos.core.factors.distribution import (  # noqa: E402  # pylint: disable=unused-import  detect_top_windmill: tests 的 ec.* 转出通道
    confirm_distribution,
    detect_distribution,
    detect_top_windmill,
)
from custos.core.factors.bottom_patterns import (  # noqa: E402  v0.56 底部侧（25chuhuo）
    bull_bear_volume,
    detect_red_fat_green_thin,
    detect_w_bottom,
)
from custos.core.factors.macd_technics import check_macd_technics  # noqa: E402

#   ↑ v0.86（因子化批 A）：check_macd_technics 及全部 _macd_* helper 与 MACD_* 常量
#     迁入 factors/macd_technics.py（零行为变化）。此处保名 re-export——
#     `enrich_candidates.check_macd_technics` 是 tests 的既有调用/monkeypatch 通道，
#     内部调用点（macd_technics = check_macd_technics(df, df_long=df_long)）不用改。
from custos.core.factors.weekly_j import (  # noqa: E402  # pylint: disable=unused-import  j_below_threshold: tests 的 ec.* 转出通道
    j_below_threshold,
    weekly_j_state,
)
from custos.core.factors.volume_detectors import (  # noqa: E402
    CZ_MIN_BARS,
    THREE_LOWS_DRAWDOWN_PCT,
    _drawdown_250d,
    check_bottom_volume,
    check_leader_volume,
    check_volume_sustain,
)
from custos.core.factors.b1_structure import (  # noqa: E402
    STOP_LOOKBACK,
    _stop_ref,
    check_five_day_entry,
    check_liquidity,
    check_non_one_wave,
    check_repair_signals,
)

#   ↑ v0.86（因子化批 B）：weekly_j_state/j_below_threshold、量能三检测器
#     （check_volume_sustain/check_leader_volume/check_bottom_volume 及 _vs_* 族、
#     _round3_or_none、VOLUME_SUSTAIN_*/LEADER_VOL_*/BOTTOM_* 常量）、结构族
#     （check_non_one_wave/_now_*、check_repair_signals/_repair_*、
#     check_five_day_entry、check_liquidity、_stop_ref 及 NOW_*/REPAIR_*/
#     FIVE_DAY_*/LIQUIDITY_WIN/STOP_LOOKBACK 常量）迁入 factors/ 三个新模块
#     （零行为变化）。此处保名 re-export——`enrich_candidates.check_*` 是 tests 的
#     既有调用/monkeypatch 通道，内部调用点不用改。
#     `_drawdown_250d`/`CZ_MIN_BARS`/`THREE_LOWS_DRAWDOWN_PCT` 被留在本模块的
#     check_three_lows 共用（L2 不得 import L3 ⇒ 共享件随因子下移、这里回导）。
from custos.core.factors.ignition import (  # noqa: E402
    b1_ignition_hit,
    check_ignition,
    check_pullback_shrink,
    zx_recent_golden,
)
from custos.core.factors.entry_patterns import (  # noqa: E402
    bbi_above,
    relative_strength_strong,
    reversal_flags,
)
from custos.core.factors.j_low_gate import j_low_gate_hit  # noqa: E402
#   ↑ v0.86（因子化批 C）：点火族（check_ignition/check_pullback_shrink +
#     b1_ignition_hit/zx_recent_golden 复合判定 + ZX_CROSS_RECENT/IGNITION_*/
#     PULLBACK_* 常量）迁入 factors/ignition.py；patterns 五单项判定
#     （reversal_flags=原 _reversal_flags、bbi_above、relative_strength_strong +
#     RS_STRONG_PP 常量）迁入 factors/entry_patterns.py；J<13 进池硬门槛登记为
#     factors/j_low_gate.py（判定本体复用 weekly_j.j_below_threshold，上方
#     re-export 保留）。全部零行为变化，此处保名 re-export——
#     `enrich_candidates.check_ignition` 是 tests 的既有 monkeypatch 通道。


from custos.core.paths import (
    DATA,
    RISK_DIR,
    SCREEN_FORMULA_REGISTRY_FILE,
    SECTORS_DIR,
    TRADES_DIR,
)  # noqa: E402
from custos.pipeline.screening import signal_labels  # noqa: E402
from custos.datasource.local_tdx import local_tdx_data  # noqa: E402
from custos.core.factors import s_shape as s_shape_mod  # noqa: E402
from custos.pipeline.screening import financials as financials_mod  # noqa: E402
from custos.core.factors import sector_phase as sector_phase_mod  # noqa: E402

# 死代码清理（2026-08-08）：本地 `_j_series` 包装已删 —— 唯一调用方早已搬走
# （全项目 grep 确认无引用），本模块的 J 走下方 `kdj`（indicators 共享实现，
# 内部 fill_na=50，行为不变）；`macd` 导入同步删除（check_macd_technics 自己
# 用 ema 算 DIF/DEA，从未调用它；v0.86 起 check_macd_technics 迁入 factors/macd_technics.py）。
from custos.core.indicators import bbi_state, kdj, zhixing_state  # noqa: E402
from custos.core.indicators import pct_change  # noqa: E402
from custos.core.indicators import amplitude_pct as amplitude_pct_of  # noqa: E402
from custos.core.indicators import dmi_arrays  # noqa: E402  DMI/ADX 唯一实现（v0.51 adx25 证据列）
from custos.core.contracts import require  # noqa: E402

SCREENING_DIR = DATA / "screening"
# v0.156（owner 拍板 2026-08-28）：人工主题映射表 sector_code_map.json 已废弃删除，
# 概念标签 semantic_tags 匹配链（build_stock_theme_map）随之整段移除——
# 板块归属唯一逻辑=走势贴合（theme_tracker_report），候选侧不再有主题族归属。
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
# v0.86（因子化批 C）：判定（原 _reversal_flags）迁入 factors/entry_patterns.py；
# 本模块保留 import 作阈值转出通道（tests 钉 `ec.J_LOW_THRESHOLD` 等与 L0 同源）。
from custos.core.b1_thresholds import (  # pylint: disable=unused-import  阈值转出通道（tests 钉 ec.J_LOW_THRESHOLD 等与 L0 同源）
    J_LOW_THRESHOLD,
    VOL_PCTILE_MAX,  # noqa: E402
    VOL_RATIO_MAX,
)

# 默认日线加载根数（get_ohlcv_table(count=...)）。它同时是 list_days 的**上界**：
# 加载器内部 `df.tail(count)`，所以 len(df)==OHLCV_LOAD_BARS 只说明"至少这么多根"，
# 不是真实上市日数（审计：CZ 的 250 日窗口在 260 根里只剩 10 根余量）。
OHLCV_LOAD_BARS = 260

# 周/月 MACD 红柱腿的长历史加载根数（v0.60 修复，2026-08-16 review 发现）：
# 月线 EMA26 需 ≥40 根月线 ⇒ ~800 根日线起，1200 ≈ 5 年（~58 根月线）有余量。
# 仅 live 链逐票多加载一次（研究/注入路径不传，wm_available 如实标 False）。
OHLCV_LOAD_BARS_LONG = 1200

# v0.59（2026-08-14，owner ⑧）：公司「地位」证据——东财 F10 公司概况关键词台账
# （fetch_company_profile.py 产 data/fundamentals/company_profile.jsonl）。
# 严格证据层：只透传落盘，不进技术分/分层/gate。⚠️ 非 PIT（简介可被公司随时改），
# 只可用于 live/近端，禁止用作历史回测特征。
_COMPANY_POSITION_CACHE: "dict | None" = None


def _load_company_positions() -> dict:
    """读公司地位台账（进程内一次）。台账缺失/损坏 → {}（证据缺席，不炸链）。"""
    global _COMPANY_POSITION_CACHE
    if _COMPANY_POSITION_CACHE is None:
        try:
            from custos.datasource.local_tdx import (  # noqa: PLC0415
                fetch_company_profile as fcp,
            )

            _COMPANY_POSITION_CACHE = fcp.load_ledger()
        except Exception as exc:  # noqa: BLE001
            # 2026-08-16 review 修复：瞬时读错不再静默缓存成 {}（本进程证据全灭
            # 而无任何痕迹）——降级可以，必须留一行 WARN。
            print(
                f"[WARN] company_position 台账加载失败（{type(exc).__name__}: {exc}），"
                "本进程该证据字段全部缺席",
                file=sys.stderr,
            )
            _COMPANY_POSITION_CACHE = {}
    return _COMPANY_POSITION_CACHE


def company_position_of(code: str) -> dict:
    """候选的公司地位证据：{available, keywords, snippet, industry_em}。"""
    rec = _load_company_positions().get(str(code or "")[:6])
    if not rec or not rec.get("available"):
        return {"available": False}
    return {
        "available": True,
        "keywords": rec.get("keywords") or [],
        "snippet": rec.get("snippet") or "",
        "industry_em": rec.get("industry_em") or "",
    }


# v0.86（因子化批 C）：RS_STRONG_PP 随 patterns 五单项判定迁入
# factors/entry_patterns.py（常量跟因子走）。
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
from custos.core.b1_thresholds import (  # pylint: disable=unused-import  阈值转出通道（tests 钉 ec.change_in_range 等与 L0 同源）
    REVERSAL_AMPLITUDE_PCT,  # noqa: E402
    change_in_range,
)
from custos.core.b1_thresholds import (
    REVERSAL_CHANGE_MAX_PCT,  # noqa: E402
    REVERSAL_CHANGE_MIN_PCT,
)

__all__ = [
    "REVERSAL_CHANGE_MAX_PCT",
    "REVERSAL_CHANGE_MIN_PCT",
]  # 阈值转出声明（测试钉这两个值）

# --- B1/CZ 策略对齐参数 -------------------------------------------------
# 以下阈值全部标注"待回测参数"：策略原文（B1 §四、CZ §九/§14.6/§十六）
# 要求阈值可配置、实际值随候选落盘，不得静默使用；完成样本回测前不得
# 视为已校准。口径出处见 governance/contracts/SCREENING_WORKFLOW.md "策略对齐"章。
# v0.86（因子化批 B）：NOW_*/REPAIR_*/FIVE_DAY_* 随结构族迁入 factors/b1_structure.py，
# VOLUME_SUSTAIN_*/LEADER_VOL_*/BOTTOM_*/CZ_MIN_BARS/THREE_LOWS_DRAWDOWN_PCT 随量能族
# 迁入 factors/volume_detectors.py（常量跟因子走）。

THREE_LOWS_VOL_RATIO = 0.3  # 待回测参数：三低之低量（<250日均量×30%）

# --- 知行量价（good_b1 图集）与出货五方式 待回测参数 ---
# v0.86（因子化批 C）：ZX_CROSS_RECENT/IGNITION_*/PULLBACK_* 随点火族迁入
# factors/ignition.py（常量跟因子走）。


# --- 完美 B1 图形贴合度（good_b1 图集共性特征的梯度评分）待回测参数 ---
# 2026-07-22 用户决策：J<13 为全通道硬门槛（公式与自选池一视同仁），
# 在 J<13 基础上按贴合度梯度给分，越符合完美图形分数越高。
J_GATE_REQUIRED_DEFAULT = (
    True  # J<13 硬门槛默认开（registry universe.j_low_required 可覆盖）
)

# --- 完美B1「缩量回踩超卖企稳」买弱指纹（10只确认赢家反标，见 worklog）---
# recall 达标(10/10)，但全市场回测证伪：周线交易模拟(止损+BBI出场)加0AMV做多+25bps成本后
# 期望 -0.42%/笔，劣于 baseline(无差别进场) 的 +0.96%/笔 —— 作进场过滤反而有害(专挑弱势、
# 排除了做多区间的突破赢家)。故仅作**描述性证据**落盘、绝不作买入依据、不驱动分层。参数下方保留。

# --- MACD 十大技术（macd十大技术精讲）待回测参数 ---
# v0.86（因子化批 A）：MACD_SWING_FRACTAL / MACD_DIV_LOOKBACK / MACD_OVEREXT_PCTL /
# MACD_OVEREXT_WIN 随 check_macd_technics 迁入 factors/macd_technics.py（常量跟因子走）。

# --- 正交因子（非量价形态）待回测参数 ---
# 方向A(2026-07-23)：全市场回测证实突破式打分非短周期 alpha，转接正交维度。
# v0.86（因子化批 B）：LIQUIDITY_WIN 随 check_liquidity 迁入 factors/b1_structure.py
# （LIQUIDITY_FLOOR_YI 正主在 score_candidates——low_liquidity flag 的打分层用）。
FUND_FLOW_SECTOR_MIN_NAME = 2  # 板块名整名匹配所需最小长度（短于此视为不可判，不给分）


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
    for x in data.get("stock_risks") or []:
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
    """加载最新的 data/sectors/*_tq_sector_map.json（880板块→成分股）。"""
    files = sorted(glob.glob(str(SECTORS_DIR / "*_tq_sector_map.json")))
    if not files:
        return {}
    return _load_json(Path(files[-1]), {})


def build_stock_industry_map() -> dict[str, str]:
    """每股 → TDX 官方细分行业名（881xxx，每股恰好一个；2026-08-04 实测 5546 只零冲突）。

    这是**权威的每股行业归属**（建设银行→全国性银行、牧原股份→养殖业、
    共进股份→通信设备），与 9 大主题族（``sector``/``theme_id``，聚合层）是两个口径：
    行业是展示层（候选表「板块」列），主题族是聚合层（相位/资金流）。
    数据来自最新 ``*_tq_sector_map.json``；取不到返回 {}（调用方按"未知"降级，不 raise）。
    """
    out: dict[str, str] = {}
    try:
        for s in latest_tq_sector_map().get("sectors") or []:
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


def _close_ret_pct(df, n: int) -> Optional[float]:
    """n 日收盘收益率 %（`close[-1] / close[-n-1] - 1`），round-2。

    2026-08-11 已按 owner 拍板统一 round-2（TODO #56 保留项②）：
    原先「改调 L0 会引入 round-4」的顾虑因 `pct_change` 的 `digits` 参数
    消除，本函数现内部直接调 L0。结果进 `relative_strength_20d_pp` 并与
    `RS_STRONG_PP` 比较 —— 判定路径 round2∘round2 幂等（减法是精确值再
    与阈值比），与 technical_monitor 的判定链口径一致。

    撞名避让历史（保留说明）：原名与 L0 的 `indicators.pct_change` 撞名，
    2026-08-10 改名 `_close_ret_pct` 避让（守卫 `TestNamedIndicatorsLiveInL0`
    会拦同名），故函数名不再改回。
    """
    if len(df) < n + 1:
        return None
    prev = float(df["close"].iloc[-n - 1])
    now = float(df["close"].iloc[-1])
    if prev == 0:
        return None
    return pct_change(now, prev, digits=2)


# ========== B1/CZ 策略对齐检测器（阈值均为待回测参数，实际值随候选落盘） ==========
# v0.86（因子化批 B）：weekly_j_state / 非一波流(_now_*) / 修复信号(_repair_*) /
# 五日战法 / 量能持续(_vs_*) / 龙头量能 / 底部巨量 / 流动性 / _stop_ref 已迁入
# factors/weekly_j.py、volume_detectors.py、b1_structure.py（零行为变化，
# 上方 import 保名 re-export）。
# v0.86（因子化批 C）：check_ignition / check_pullback_shrink（及 b1 点火复合
# 判定）已迁入 factors/ignition.py（零行为变化，上方 import 保名 re-export）。
# 留在本模块的：check_three_lows（CZ 三低，非点火族）。


def check_three_lows(df) -> dict[str, Any]:
    """三低（CZ §九/§18.6）：低价格（回撤>=40%）+ 低量（<250日均量×30%）。

    第三维"低关注度"非量价可计算，不输出；财务排雷因数据源未接入暂缓。
    """
    close, high, _, vol = _ohlcv_arrays(df)
    _high250, dd = _drawdown_250d(close, high)
    if dd is None:
        return {"hit": False, "available": False}
    vol_ma250 = float(vol[-CZ_MIN_BARS:].mean())
    low_price = dd >= THREE_LOWS_DRAWDOWN_PCT
    low_vol = bool(vol_ma250 and vol[-1] < vol_ma250 * THREE_LOWS_VOL_RATIO)
    return {
        "hit": bool(low_price and low_vol),
        "available": True,
        "conditions": {
            "low_price": {
                "hit": bool(low_price),
                "drawdown_from_250d_high_pct": round(dd, 2),
            },
            "low_volume": {
                "hit": low_vol,
                "vol_today": float(vol[-1]),
                "vol_ma250": round(vol_ma250, 2),
                "vol_ratio_vs_ma250": round(float(vol[-1] / vol_ma250), 3)
                if vol_ma250
                else None,
            },
        },
    }


def _fund_flow_snapshots(
    mdir: Path, date: str, cumulative_days: int
) -> tuple[list, list, list]:
    """读取单日或累计 N 日的资金流快照，返回 (stock_ranks, sector_maps, files_used)。"""
    if cumulative_days <= 1:
        data = _load_json(mdir / f"{date}_fund_flow_rank.json", {})
        stock_ranks = [data.get("stock_rank") or []]
        sector_maps = [data.get("sector_rank") or {}]
        files_used = [date] if data else []
        return stock_ranks, sector_maps, files_used
    allf = sorted(p for p in mdir.glob("*_fund_flow_rank.json") if p.name[:10] <= date)
    use = allf[-cumulative_days:]
    files_used = [p.name[:10] for p in use]
    stock_ranks, sector_maps = [], []
    for p in use:
        d = _load_json(p, {})
        stock_ranks.append(d.get("stock_rank") or [])
        sector_maps.append(d.get("sector_rank") or {})
    return stock_ranks, sector_maps, files_used


def _agg_stock_fund_flow(stock_ranks: list, cumulative_days: int) -> dict[str, dict]:
    """按 code 聚合主力净流入（多日累加；日内占比仅单日快照有意义）。"""
    by_code: dict[str, dict] = {}
    for sr in stock_ranks:
        for s in sr:
            c = str(s.get("code", "")).split(".")[0].zfill(6)
            if not (c.isdigit() and len(c) == 6):
                continue
            e = by_code.setdefault(
                c,
                {
                    "code": c,
                    "name": s.get("name", ""),
                    "main_net_inflow": 0.0,
                    "days": 0,
                    # 单日快照才有意义的日内占比；多日累计无法相加 → None
                    "main_net_pct": (
                        s.get("main_net_pct") if cumulative_days <= 1 else None
                    ),
                },
            )
            v = s.get("main_net_inflow")
            if isinstance(v, (int, float)):
                e["main_net_inflow"] += v
            e["days"] += 1
    return by_code


def _agg_sector_fund_flow(sector_maps: list) -> dict[str, dict]:
    """按板块名聚合主力净流入（concept + industry 同名合并，多日累加）。"""
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
    return sec_agg


def load_fund_flow(
    date: str, cumulative_days: int = 1, market_dir=None
) -> dict[str, Any]:
    """读 collect_fund_flow 落盘的每日资金流快照（东财）。

    cumulative_days<=1：仅读 {date}_fund_flow_rank.json（现状）。
    cumulative_days>1：累加 <=date 的最近 N 个每日快照的主力净流入（按 code/板块名聚合）——
    单日快照噪声大，多日累计更稳（资金流本身无历史存档，只能就已落盘的每日文件累积）。
    market_dir 可注入以便测试。缺失干净降级。
    """
    mdir = Path(market_dir) if market_dir else (DATA / "market")
    stock_ranks, sector_maps, files_used = _fund_flow_snapshots(
        mdir, date, cumulative_days
    )
    by_code = _agg_stock_fund_flow(stock_ranks, cumulative_days)
    sec_agg = _agg_sector_fund_flow(sector_maps)
    return {
        "available": bool(by_code or sec_agg),
        "by_code": by_code,
        "sectors": list(sec_agg.values()),
        "cumulative_days": cumulative_days,
        "files_used": files_used,
    }


def sector_name_matches(
    flow_name: str, sector_name: str, min_len: int = FUND_FLOW_SECTOR_MIN_NAME
) -> bool:
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


def _ff_best_sector_match(sector_name: str, sectors: list) -> Optional[dict]:
    """资金流板块聚合里与候选主题整名匹配、净流入最高的板块（无则 None）。"""
    sec_match = None
    for s in sectors:
        nm = str(s.get("name") or "")
        if not sector_name_matches(nm, sector_name):
            continue
        if sec_match is None or (s.get("main_net_inflow") or 0) > (
            sec_match.get("main_net_inflow") or 0
        ):
            sec_match = s
    return sec_match


def _fund_flow_result(
    entry: Optional[dict], sec_match: Optional[dict]
) -> dict[str, Any]:
    """组装 fund_flow_of 的输出 dict（字段与键序即契约，勿动）。"""
    main_inflow = entry.get("main_net_inflow") if entry else None
    sector_inflow = (sec_match or {}).get("main_net_inflow")
    return {
        "available": True,
        "in_rank": entry is not None,
        "main_net_inflow": main_inflow,
        "main_net_pct": (entry or {}).get("main_net_pct") if entry else None,
        "in_rank_positive": bool(
            entry is not None
            and isinstance(main_inflow, (int, float))
            and main_inflow > 0
        ),
        "sector_matched": (sec_match or {}).get("name"),
        "sector_main_net_inflow": sector_inflow,
        "sector_inflow_positive": bool(
            isinstance(sector_inflow, (int, float)) and sector_inflow > 0
        ),
    }


def fund_flow_of(code6: str, sector_name: str, ff: dict) -> dict[str, Any]:
    """个股 + 板块资金流（正交于量价）：个股是否在主力净流入榜且为净流入、
    所属主题板块是否净流入。榜/文件缺失时干净降级。"""
    if not ff or not ff.get("available"):
        return {"available": False}
    entry = (ff.get("by_code") or {}).get(code6)
    sec_match = None
    if sector_name and sector_name != "未知":
        sec_match = _ff_best_sector_match(sector_name, ff.get("sectors") or [])
    return _fund_flow_result(entry, sec_match)


def _volume_stats(df) -> tuple[Optional[float], Optional[float]]:
    """量比（当日量/前5日均量）与 20 日量分位。"""
    vol = df["volume"].astype(float)
    vol_today = float(vol.iloc[-1])
    vol_ma5_prev = float(vol.iloc[-6:-1].mean()) if len(df) >= 6 else None
    vol_ratio = (vol_today / vol_ma5_prev) if vol_ma5_prev else None
    vol20 = vol.tail(20)
    vol_pctile = float((vol20 < vol_today).mean() * 100) if len(vol20) >= 20 else None
    return vol_ratio, vol_pctile


def _rs_20d_stats(
    df, index_df
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """20 日相对强度：个股 20 日涨幅 − 上证指数 20 日涨幅（百分点）。"""
    stock_ret20 = _close_ret_pct(df, 20)
    index_ret20 = (
        _close_ret_pct(index_df, 20)
        if index_df is not None and not index_df.empty
        else None
    )
    rs_20d = (
        (stock_ret20 - index_ret20)
        if (stock_ret20 is not None and index_ret20 is not None)
        else None
    )
    return stock_ret20, index_ret20, rs_20d


def _base_scalars(df, index_df) -> dict[str, Any]:
    """基础标量块：价/涨跌/振幅/BBI/日J/量能/20日相对强度/止损位及派生布尔。

    v0.86（因子化批 C）：patterns 五单项的**判定**迁入 factors/entry_patterns.py
    （reversal_flags / relative_strength_strong）；中间量（bbi/j/量比/相对强度）
    仍在这里各算一次、喂落盘字段与证据块——单次计算语义不变。
    """
    close = df["close"]
    bbi = bbi_state(df)
    j = kdj(df)
    last = df.iloc[-1]
    prev_close = float(close.iloc[-2]) if len(df) >= 2 else None

    vol_ratio, vol_pctile = _volume_stats(df)
    change_pct = pct_change(float(last["close"]), prev_close, digits=2)
    amplitude_pct = amplitude_pct_of(last["high"], last["low"], prev_close)
    stock_ret20, index_ret20, rs_20d = _rs_20d_stats(df, index_df)
    stop_ref = _stop_ref(df)

    daily_j = j.get("j") if j.get("available") else None
    j_low, vol_contraction, reversal_k = reversal_flags(
        daily_j, vol_ratio, vol_pctile, change_pct, amplitude_pct
    )
    return {
        "last": last,
        "bbi": bbi,
        "j": j,
        "change_pct": change_pct,
        "amplitude_pct": amplitude_pct,
        "daily_j": daily_j,
        "j_low": j_low,
        "vol_ratio": vol_ratio,
        "vol_pctile": vol_pctile,
        "vol_contraction": vol_contraction,
        "reversal_k": reversal_k,
        "stock_ret20": stock_ret20,
        "index_ret20": index_ret20,
        "rs_20d": rs_20d,
        "rs_strong": relative_strength_strong(rs_20d),
        "stop_ref": stop_ref,
    }


def _evidence_states(df, code: str, df_long, base: dict[str, Any]) -> dict[str, Any]:
    """证据块：知行量价/出货与底部形态/MACD 十大技术/信号标注/ADX。

    只计算、不组装；返回的中间态由 _assemble_metrics 摊进候选字段。
    """
    daily_j = base["daily_j"]
    j_low = base["j_low"]
    reversal_k = base["reversal_k"]

    # --- 知行量价（good_b1）与出货识别（出货五方式）---
    zx = zhixing_state(df)
    dks_last = zx.get("dks") if zx.get("available") else None
    ignition = check_ignition(df)
    pullback_shrink = check_pullback_shrink(df, dks_last)
    ride_above_fast = bool(
        zx.get("available") and zx.get("close_above_qsx") and zx.get("qsx_gt_dks")
    )
    # v0.86（因子化批 C）：近金叉与 b1_ignition 复合判定迁入 factors/ignition.py
    # （纯判定函数，输入全是本块已算好的中间态，不重算）。
    zx_recent_gold = zx_recent_golden(zx)
    b1_ignition_flag = b1_ignition_hit(
        j_low, reversal_k, pullback_shrink, zx_recent_gold, ignition
    )
    distribution = detect_distribution(df, code)
    # 次日确认豁免层（2026-08-13，25chuhuo 覆盖度缺口）：①/② 的换庄/假出货豁免 +
    # 顶部大风车。证据层（复用已算的 det，不重算），不驱动分层。
    distribution_confirm = confirm_distribution(df, code, det=distribution)
    # 底部侧形态（2026-08-13，v0.56，25chuhuo 底部镜像）：W 底（双底+底部放量+
    # MACD 底背离合成）与红肥绿瘦（数量+面积两维）。证据层，不进技术分/分层/gate。
    w_bottom = detect_w_bottom(df, code)
    red_fat_green_thin = detect_red_fat_green_thin(df, code)
    # 指标去重：日线 KDJ 与 MACD 各只算一次，再喂给下游检测器（审计：kdj×4/macd×3）。
    macd_technics = check_macd_technics(df, df_long=df_long)

    # 研究因子的**信号标注**（三态 hit/miss/unavailable）。只标注、不参与打分分层——
    # 这些因子还没跑过真实回测，而结论#15 的教训是"识别有术、盈利无效"。
    # 复用上面已算的 distribution / daily_j，避免重复 kdj。
    # （v0.172 起 weekly_j 不再注入标注层——W 标注撤除；`_wk` 仍落候选顶层 weekly_* 键）
    _wk = weekly_j_state(df)
    try:  # 平台回踩:与下方证据层同一份检测,延迟导入
        from custos.core.factors.platform_pullback import detect_platform_pullback  # noqa: PLC0415

        _plat = detect_platform_pullback(df)
    except Exception:  # noqa: BLE001
        _plat = None
    signals = signal_labels.compute_signals(
        df,
        code,
        daily_j=daily_j,
        distribution=distribution,
        platform_pullback=_plat,
    )

    # --- 证据层新列（v0.51，#37 阶段 B，owner 批准；严格证据层：不进技术分/
    #     分层/gate，只落盘 + 展示）---
    # adx25：R2:67-72「J<13 且 ADX≥25」是研究链首个跨牛熊一致改善的入场过滤
    # （三窗全改善），R2 裁决「以证据层接入观察一季，勿直接进 live gate」。
    # ADX 用 indicators.dmi_arrays（全项目唯一实现，Wilder 口径）；J 阈与
    # j_low 同口径（b1_thresholds.J_LOW_THRESHOLD）。
    _pdi, _mdi, _adx = dmi_arrays(df["high"], df["low"], df["close"])
    adx_last = round(float(_adx[-1]), 2) if _adx is not None and len(_adx) else None
    adx25 = bool(j_low and adx_last is not None and adx_last >= 25)

    return {
        "zx": zx,
        "ignition": ignition,
        "pullback_shrink": pullback_shrink,
        "ride_above_fast": ride_above_fast,
        "zx_recent_gold": zx_recent_gold,
        "b1_ignition_hit": b1_ignition_flag,
        "distribution": distribution,
        "distribution_confirm": distribution_confirm,
        "w_bottom": w_bottom,
        "red_fat_green_thin": red_fat_green_thin,
        "macd_technics": macd_technics,
        "weekly": _wk,
        "signals": signals,
        "adx_last": adx_last,
        "adx25": adx25,
    }


def _assemble_metrics(
    df, index_df, code: str, base: dict[str, Any], ev: dict[str, Any]
) -> dict[str, Any]:
    """组装 compute_metrics 的返回 dict（键集合/顺序为落盘契约，勿动）。"""
    last = base["last"]
    bbi = base["bbi"]
    daily_j = base["daily_j"]
    change_pct = base["change_pct"]
    amplitude_pct = base["amplitude_pct"]
    vol_ratio = base["vol_ratio"]
    vol_pctile = base["vol_pctile"]
    stock_ret20 = base["stock_ret20"]
    index_ret20 = base["index_ret20"]
    rs_20d = base["rs_20d"]
    stop_ref = base["stop_ref"]
    zx = ev["zx"]
    pullback_shrink = ev["pullback_shrink"]
    macd_technics = ev["macd_technics"]
    _wk = ev["weekly"]
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
        "stop_loss_ref": {"price": stop_ref, "basis": f"近{STOP_LOOKBACK}日最低价"}
        if stop_ref
        else None,
        "patterns": {
            "bbi_above": bbi_above(bbi),
            "j_low": bool(base["j_low"]),
            "volume_contraction": bool(base["vol_contraction"]),
            "reversal_k_candidate": base["reversal_k"],
            "relative_strength_strong": bool(base["rs_strong"]),
        },
        # --- B1/CZ 策略对齐（阈值均为待回测参数，实际值随候选落盘） ---
        "wave": detect_wave_type(df),
        # 只摊 weekly_ 前缀键：裸 available 会落到候选顶层被误读成"候选可用"（审计）
        **{k: v for k, v in _wk.items() if k.startswith("weekly_")},
        "signals": ev["signals"],
        "non_one_wave": check_non_one_wave(df),
        "repair_signals": check_repair_signals(df, index_df, kdj_state=base["j"]),
        "five_day_entry": check_five_day_entry(df),
        "volume_sustain": check_volume_sustain(df),
        "leader_volume": check_leader_volume(df),
        "three_lows": check_three_lows(df),
        "bottom_volume": check_bottom_volume(df),
        # --- 知行量价 + 出货识别（good_b1 / 出货五方式，阈值待回测，实际值落盘） ---
        "zhixing": zx,
        "ignition": ev["ignition"],
        "pullback_shrink": pullback_shrink,
        "ride_above_fast": ev["ride_above_fast"],
        "b1_ignition": {
            "hit": ev["b1_ignition_hit"],
            "zhixing_recent_golden": ev["zx_recent_gold"],
        },
        "distribution": ev["distribution"],
        "distribution_confirm": ev["distribution_confirm"],
        # v0.56 底部侧证据列（25chuhuo）：W 底 / 红肥绿瘦——只落盘展示，不进分层
        "w_bottom": ev["w_bottom"],
        "red_fat_green_thin": ev["red_fat_green_thin"],
        "macd_technics": macd_technics,
        "perfect_b1_fit": compute_perfect_b1_fit(
            df, daily_j, zx, pullback_shrink, macd_state=macd_technics
        ),
        # v0.50（#37 阶段 A，owner 拍板）：b1_pullback_fit 已被全市场回测**证伪**
        # （作进场过滤期望 -0.42%/笔 < baseline +0.96%/笔）⇒ 停止逐票计算
        # （原每笔一条自建 J 序列 + 7 个分项），落盘字段保留为不可用标记
        # （历史候选 JSON 的该字段是旧口径；研究侧 backtest_factors 仍可直接
        # 调因子模块）。
        "b1_pullback_fit": {
            "available": False,
            "note": "已证伪，v0.50 起停止逐票计算（#37 阶段 A）",
        },
        "s_shape": s_shape_mod.compute_s_shape(df, code),
        # v0.51（#37 阶段 B，TODO ② 闭环）：s_reversal（买弱/反转分，与 B1
        # 「回调买入」同向）此前存在但 live 从不调用 ⇒ 接进证据列。
        # 成本：与 compute_s_shape 同阶（对已加载 df 的一次 O(n) 纯计算，
        # 无网络/IO），18:00 链每票 +1 次评分调用，可忽略 ⇒ 不设开关。
        # 严格证据层：不进技术分/分层/gate。
        "s_reversal": s_shape_mod.compute_s_reversal(df, code),
        # v0.51（#37 阶段 B）：adx25 证据列（R2 三窗全改善的入场过滤，
        # 证据层观察一季）。adx 数值一并落盘供展示。
        "adx": ev["adx_last"],
        "adx25": ev["adx25"],
        # v0.58（2026-08-14，owner）：近 10 日阳量/阴量总量对比——技术分的
        # 加/减分项（阳量>阴量 +5 / 阴量>阳量 −5）。中性窗口，不带底部位置语义。
        "volume_yy": bull_bear_volume(df),
        "liquidity": check_liquidity(df),
        # v0.59（owner ⑧）：公司地位证据（东财 F10 简介关键词，evidence_only 透传）
        "company_position": company_position_of(code),
    }


def compute_metrics(df, index_df, code: str = "", df_long=None) -> dict[str, Any]:
    """对单股日线 DataFrame 计算全部指标与模式标签（确定性）。

    ``df_long``（可选）：更长历史的日线（live 链传 count=1200），仅供
    check_macd_technics 的周/月红柱腿（EMA26 需 ≥40 根月线，260 根日线不够）。
    不传则用 df 自身，月线根数不足时 wm_available=False 如实标注。

    实现分三段：``_base_scalars``（基础标量）→ ``_evidence_states``（证据块）
    → ``_assemble_metrics``（组装落盘 dict）。
    """
    base = _base_scalars(df, index_df)
    ev = _evidence_states(df, code, df_long, base)
    return _assemble_metrics(df, index_df, code, base, ev)


def _init_enrich_result(
    date: str, hits_data: dict, cfg: dict
) -> tuple[dict, bool, bool, bool]:
    """初始化 result 骨架 + hits 可用性/日期一致性/ST 过滤门控。

    返回 (result, ready, exclude_st, st_filter_broken)；ready=False 时
    enrich 直接返回 result（命中清单不可用）。
    """
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
        return result, False, False, False

    # 数据源当日一致性：formula_hits（TQ 在线公式评估）与本段（本地 vipdoc 日线）是两个
    # 独立来源。若命中清单不是当日产出（喂了旧文件/TQ 落后），标注 partial；无论如何，
    # 下游都用逐票 last_date==date 二次校验（见循环内 no_today_bar 剔除）兜底。
    hits_date = hits_data.get("date")
    if hits_date and hits_date != date:
        result["status"] = "partial"
        result["degraded_reason"] = _append_reason(
            result["degraded_reason"], f"formula_hits_date_mismatch:{hits_date}"
        )
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
            f"st_filter_unavailable:{st_filter}(名称表不可用 → 无名候选按 st_unverified 剔除)",
        )
    return result, True, exclude_st, st_filter_broken


def _merge_hits(hits_data: dict) -> dict[str, dict]:
    """去重合并：code → {name, formula_ids}。"""
    merged: dict[str, dict] = {}
    for f in hits_data.get("formulas", []):
        for h in f.get("hits", []):
            code6 = str(h.get("code", "")).split(".")[0].zfill(6)
            if not (code6.isdigit() and len(code6) == 6):
                continue
            entry = merged.setdefault(
                code6, {"code": code6, "name": h.get("name", ""), "formula_hits": []}
            )
            if not entry["name"] and h.get("name"):
                entry["name"] = h["name"]
            if f.get("id") and f["id"] not in entry["formula_hits"]:
                entry["formula_hits"].append(f["id"])
    return merged


def _load_financials_ctx(
    financials_cfg: Optional[dict],
) -> tuple[bool, Any, dict]:
    """财务台账（CZ 抄底代理证据层）：返回 (fin_enabled, fin_df, fin_colmap)。"""
    fin_cfg = financials_cfg or {}
    fin_enabled = bool(fin_cfg.get("enabled"))
    fin_df = (
        financials_mod.load_financials(fin_cfg.get("report_period", ""))
        if fin_enabled
        else None
    )
    fin_colmap = dict(fin_cfg.get("columns") or {})
    if fin_enabled and fin_df is not None and fin_cfg.get("auto_map", True):
        _cm = financials_mod.auto_colmap(getattr(fin_df, "columns", []))
        _cm.update(fin_colmap)  # 显式 registry.columns 按字段覆盖自动识别
        fin_colmap = _cm
    return fin_enabled, fin_df, fin_colmap


def _load_sector_phase_resolver(sector_phase_cfg: Optional[dict]) -> Any:
    """板块相位（hint，不封顶）：best-effort 构建 resolver；数据缺失/异常则跳过。"""
    sp_cfg = sector_phase_cfg or {}
    sp_resolve = None
    if sp_cfg.get("enabled", True):
        try:
            mpath = Path(
                sp_cfg.get("members_path") or (DATA / "market" / "sector_members.json")
            )
            idir = Path(sp_cfg.get("index_dir") or (DATA / "market" / "sector_index"))
            if mpath.is_file() and idir.is_dir():
                members = _load_json(mpath, {})
                if members:
                    sp_resolve = sector_phase_mod.build_phase_resolver(idir, members)
        except Exception:  # noqa: BLE001
            sp_resolve = None
    return sp_resolve


def _load_enrich_context(
    date: str,
    fund_flow_days: int,
    financials_cfg: Optional[dict],
    sector_phase_cfg: Optional[dict],
) -> dict[str, Any]:
    """批次级上下文：风险名单/持仓/资金流/财务/板块相位/行业图。"""
    ctx: dict[str, Any] = {
        "risk_high": load_risk_high_codes(date),
        "holding": load_holding_codes(),
        "fund_flow": load_fund_flow(date, cumulative_days=fund_flow_days),
    }
    fin_enabled, fin_df, fin_colmap = _load_financials_ctx(financials_cfg)
    ctx["fin_enabled"] = fin_enabled
    ctx["fin_df"] = fin_df
    ctx["fin_colmap"] = fin_colmap
    ctx["sp_resolve"] = _load_sector_phase_resolver(sector_phase_cfg)
    # 每股官方细分行业（881xxx，展示层「板块」列；取不到全"未知"）
    ctx["stock_industry"] = build_stock_industry_map()
    return ctx


def _load_index_frame(index_loader, date: str, result: dict):
    """加载并校验上证指数日线（就地写 result 的 index_status/index_code/降级痕迹）。

    指数序列与个股同等对待：**必须排序 + 必须当日**（审计 B7）。
    此前指数只有一个裸 try/except：
      1) 加载失败 → index_df=None，rs_20d/rs_turn 整列静默变 None/False，
         relative_strength_strong 一律不命中，报告里看不出"相对强度这维今天是废的"；
      2) 无 last_date==date 校验 → 指数停在 T-1（vipdoc 未更新/节前抓的旧文件）时，
         拿 T-1 的指数 20 日涨幅去减当日个股 20 日涨幅＝**错窗口相减**，rs 偏差直接
         喂给 rs_strong 与 capital_intent，比没有更危险；
      3) 无 sort_values → mootdx Reader 不保证顺序，iloc[-1] 可能取到中间某天。
    现在：坏/旧的指数一律降级为"不可用"（rs 置 None，不做错窗口相减），并把原因写进
    index_status + degraded_reason，让下游能归因。
    """
    load_index = index_loader or (
        lambda: local_tdx_data.get_ohlcv_table(INDEX_CODE, count=OHLCV_LOAD_BARS)
    )
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
            f"{index_status}(上证指数不可用/非当日 → 20日相对强度整列置空，不做错窗口相减)",
        )
    return index_df


def _precheck_exclusion(
    code6: str,
    name: str,
    cfg: dict,
    exclude_st: bool,
    st_filter_broken: bool,
    risk_high: set,
) -> Optional[str]:
    """加载 K 线前的硬排除（BJ/A股白名单/ST/风险名单）；返回剔除原因或 None。"""
    if code6.startswith(_BJ_PREFIX):
        # BJ 有独立开关（可配置放开），单列一类，不与 not_a_share 混同
        if cfg.get("exclude_bj", True):
            return "exclude_bj"
    elif not _A_SHARE_RE.match(code6):
        return "not_a_share"  # ETF/可转债/B股/指数：不是可买个股（审计 B10）
    if exclude_st:
        if "ST" in name.upper():
            return "st_stock"
        if st_filter_broken and not name.strip():
            # fail-closed：名称表挂了就无法证明这只票不是 ST，宁可漏也不能错放（审计 B5）
            return "st_unverified:name_map_unavailable"
    if code6 in risk_high:
        return "risk_high_priority"
    return None


def _load_stock_frames(
    code6: str, date: str, min_list_days: int, load_ohlcv, live_long: bool
) -> tuple[Any, Any, Optional[str], Optional[str]]:
    """加载并校验个股日线（+live 长历史）。返回 (df, df_long, last_date, 剔除原因)。

    需剔除时前三者为 None；否则剔除原因为 None。
    """
    try:
        df = load_ohlcv(code6)
    except Exception:  # noqa: BLE001
        df = None
    if df is None or df.empty:
        return None, None, None, "no_local_kline"
    df = df.sort_values("date").reset_index(drop=True)
    last_date = str(df["date"].iloc[-1])[:10]
    if last_date != date:
        return (
            None,
            None,
            None,
            f"no_today_bar:last={last_date}",
        )  # 停牌或本地数据未更新
    if len(df) < min_list_days:
        return None, None, None, f"list_days<{min_list_days}"

    # 周/月 MACD 腿的长历史（仅 live 默认加载路径；注入 loader 的测试/研究
    # 路径不加载，check_macd_technics 退回 df 自身并如实标 wm_available）。
    df_long = None
    if live_long:
        try:
            df_long = local_tdx_data.get_ohlcv_table(code6, count=OHLCV_LOAD_BARS_LONG)
            if df_long is not None and not df_long.empty:
                df_long = df_long.sort_values("date").reset_index(drop=True)
            else:
                df_long = None
        except Exception:  # noqa: BLE001 —— 长历史加载失败不阻塞（月线腿降级）
            df_long = None
    return df, df_long, last_date, None


def _compute_one_metrics(code6: str, df, index_df, df_long):
    """单股指标计算；单股坏数据不中断批次。返回 (metrics, 剔除原因)。"""
    try:
        # df_long 为 None 时不传参——旧签名调用（测试/研究对 compute_metrics
        # 的 monkeypatch 替身没有 df_long 形参，多传会 TypeError 误杀候选）
        if df_long is not None:
            return compute_metrics(df, index_df, code=code6, df_long=df_long), None
        return compute_metrics(df, index_df, code=code6), None
    except Exception as exc:  # noqa: BLE001
        return None, f"metrics_error:{type(exc).__name__}:{str(exc)[:80]}"


def _apply_j_gate(cand: dict, result: dict, cfg: dict) -> bool:
    """J<13 硬门槛（2026-07-22 用户决策）：全通道候选（公式与自选池一视同仁）
    必须先满足日 J<13，再谈完美图形贴合度；J 不可计算视同不满足。

    被挡时写 excluded 并返回 True（v0.89 起门外异动票不再单列观察区）。
    """
    if not cfg.get("j_low_required", J_GATE_REQUIRED_DEFAULT):
        return False
    dj = cand.get("daily_j")
    # v0.86（因子化批 C）：门槛判定改走 factors/j_low_gate.py 的因子化入口
    # （判定本体仍是 weekly_j.j_below_threshold，上方 re-export 通道不变）。
    if j_low_gate_hit(dj):
        return False
    # 被挡时写 excluded 并返回 True（v0.89 起门外异动票不再单列观察区，
    # 报告侧改为池内门内提醒，见 candidate_table._in_gate_reminder_section）。
    result["excluded"].append(
        {"code": cand["code"], "name": cand["name"], "reason": f"j_not_low:j={dj}"}
    )
    return True


def _apply_post_metrics(cand: dict, code6: str, df, ctx: dict) -> None:
    """行业/资金流/板块相位/平台回踩/财务 充实字段（就地写入 cand）。

    v0.156（owner 拍板）：主题族归属（theme_id/sector/sector_source）随人工主题映射表
    一并废弃——契约键保留但恒为空/「未知」，板块归属唯一逻辑=走势贴合
    （theme_tracker_report）；候选的板块展示以 881 官方细分行业（industry 列）为准。
    """
    cand["theme_id"] = ""
    cand["sector"] = "未知"
    cand["sector_source"] = ""
    cand["industry"] = ctx["stock_industry"].get(code6, "未知")
    cand["fund_flow"] = fund_flow_of(code6, cand["sector"], ctx["fund_flow"])
    sp_resolve = ctx["sp_resolve"]
    if sp_resolve is not None:
        cand["sector_phase"] = sp_resolve(code6)  # 板块相位 hint(不封顶,证据层)
    try:  # 平台突破回踩形态(证据层,不驱动分层)
        from custos.core.factors.platform_pullback import detect_platform_pullback  # noqa: PLC0415

        pp = detect_platform_pullback(df)
        if pp:
            cand["platform_pullback"] = (
                pp  # {platform_high, breakout_date, pullback_low, ...}
            )
    except Exception:  # noqa: BLE001
        pass
    if ctx["fin_enabled"] and ctx["fin_colmap"]:
        # 财务维度(CZ抄底代理)：最佳努力落盘证据层，不驱动分层
        cand["financials"] = financials_mod.financial_factor(
            code6, ctx["fin_df"], ctx["fin_colmap"], price=cand.get("close")
        )


def enrich(
    date: str,
    hits_data: Optional[dict] = None,
    ohlcv_loader=None,
    index_loader=None,
    universe_cfg: Optional[dict] = None,
    fund_flow_days: int = 1,
    financials_cfg: Optional[dict] = None,
    sector_phase_cfg: Optional[dict] = None,
) -> dict:
    """充实命中股。loader 可注入以便测试；所有失败结构化落盘，绝不 raise。

    流程：``_init_enrich_result``（门控）→ ``_merge_hits``（去重）→
    ``_load_enrich_context``（批次上下文）→ ``_load_index_frame``（指数）→
    逐票（``_precheck_exclusion`` → ``_load_stock_frames`` →
    ``_compute_one_metrics`` → ``_apply_j_gate`` → ``_apply_post_metrics``）。
    """
    hits_data = hits_data if hits_data is not None else load_hits(date)
    cfg = universe_cfg or {}
    min_list_days = int(cfg.get("min_list_days", 60))

    result, ready, exclude_st, st_filter_broken = _init_enrich_result(
        date, hits_data, cfg
    )
    if not ready:
        return result

    merged = _merge_hits(hits_data)
    ctx = _load_enrich_context(
        date,
        fund_flow_days,
        financials_cfg,
        sector_phase_cfg,
    )

    load_ohlcv = ohlcv_loader or (
        lambda c: local_tdx_data.get_ohlcv_table(c, count=OHLCV_LOAD_BARS)
    )
    index_df = _load_index_frame(index_loader, date, result)

    for code6 in sorted(merged):
        item = merged[code6]
        name = item["name"]

        reason = _precheck_exclusion(
            code6, name, cfg, exclude_st, st_filter_broken, ctx["risk_high"]
        )
        if reason is None:
            df, df_long, last_date, reason = _load_stock_frames(
                code6, date, min_list_days, load_ohlcv, live_long=ohlcv_loader is None
            )
        if reason is None:
            metrics, reason = _compute_one_metrics(code6, df, index_df, df_long)
        if reason is not None:
            result["excluded"].append({"code": code6, "name": name, "reason": reason})
            continue

        cand = {
            "code": code6,
            "name": name,
            "formula_hits": item["formula_hits"],
            "is_holding": code6 in ctx["holding"],
            # list_days 是**加载到的 K 线根数**，不是真实上市日数：默认加载器
            # `get_ohlcv_table(count=OHLCV_LOAD_BARS)` 内部 tail(count) 截断，
            # 所以取到上界时它只代表"≥260 个交易日"。硬排除（<min_list_days，默认60）
            # 不受影响（60 远小于 260），但任何"上市多久/够不够 250 日窗口"的判断
            # 都必须先看 list_days_exact，否则会把老票误当刚上市（审计）。
            "list_days": len(df),
            "list_days_exact": len(df) < OHLCV_LOAD_BARS,
            "list_days_basis": (
                "loaded_bars" if len(df) < OHLCV_LOAD_BARS else "loaded_bars_censored"
            ),
            "signal_date": last_date,
            **metrics,
        }
        if _apply_j_gate(cand, result, cfg):
            continue
        _apply_post_metrics(cand, code6, df, ctx)
        result["candidates"].append(cand)

    # 名称表挂掉导致"筛完 0 只"必须报 unavailable 而不是 partial：score_candidates 只对
    # unavailable 整池降级，partial + 空 candidates 会被读成"今天市场没有符合条件的票"。
    if (
        st_filter_broken
        and not result["candidates"]
        and any(
            str(x.get("reason", "")).startswith("st_unverified")
            for x in result["excluded"]
        )
    ):
        result["status"] = "unavailable"

    return result


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="screening 链第 2 段：命中股充实+模式识别（确定性）"
    )
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    registry = _load_json(SCREEN_FORMULA_REGISTRY_FILE, {})
    result = enrich(
        args.date,
        universe_cfg=registry.get("universe") or {},
        fund_flow_days=int((registry.get("fund_flow") or {}).get("cumulative_days", 1)),
        financials_cfg=registry.get("financials") or {},
        sector_phase_cfg=registry.get("sector_phase") or {},
    )

    SCREENING_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SCREENING_DIR / f"{args.date}_candidates_enriched.json"
    require("candidates_enriched", result)
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

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
