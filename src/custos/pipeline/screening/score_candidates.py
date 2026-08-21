# -*- coding: utf-8 -*-
"""Screening 链第 3 段：个股量价分层 + 板块提示（score_candidates）。

2026-07-23 重构（用户决策）：分层（A/B/C/D）由**个股自身**定夺，不再被板块弱势
封顶——很多强势个股不跟原板块走，仅因板块弱把走势好的个股打到 D 得不偿失。

个股共振矩阵（base bucket）＝ 技术结构 × 资金意图（均为个股维度）：

| 技术结构\\资金意图 | 强 | 中 | 弱 |
|---|---|---|---|
| 强 | A | B | C |
| 中 | B | C | D |
| 弱 | C | D | D |

- 技术结构 = technical_score 分级（强>=60 / 中30-59 / 弱<30，patterns 累加
  单一路径；**v0.50 起 s_shape 主路径已删**——R2 证无 alpha，owner 拍板
  #37 阶段 A。s_shape 仅作展示/证据列落盘，不驱动分层）。
- 资金意图 = capital_intent_strength（放量点火/龙头量/底部巨量/相对强度/知行多头/
  量能持续；命中派发或 MACD 顶背离则判资金流出=弱）。

板块信息**只作提示**（v0.24 起不封顶；v0.50 起不进总分），只体现在：
- score：总分 = 技术分（原 0.6×技术+0.4×板块+共振±5 已废；板块分与共振
  仍落盘展示）。
- trade_style：主升/修复→波段；震荡/分歧→波段(谨慎)；退潮/未知→短线(交易性机会)。

仍保留的**风控/回避硬否决**（与"板块弱"无关）：
- 0AMV 空头 → 封顶 B 且 next_step=observe_price。
- 无可定义止损位 → 封顶 B。
- 冲刺波首个B1 → 封顶 B；非一波流撤销 → 封顶 C；量能撤退 → 封顶 C。
- 主力出货五方式 high→D/watch→C；MACD 顶背离/三打白骨精 → 封顶 C。
  （v0.80：CZ 回避方向名单 → D 已随板块名单机制一并移除。）

CLI::

    uv run python src/custos/pipeline/screening/score_candidates.py --date YYYY-MM-DD

输出 ``data/stock_pool/{date}_stock_pool.json``（StockPool 契约，
见 governance/contracts/DATA_FLOW_CONTRACT.md）。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import (
    DATA,
    MARKET_DIR,
    SCREEN_FORMULA_REGISTRY_FILE,
    SECTORS_DIR,
    STOCK_POOL_DIR,
)  # noqa: E402


from custos.core.runtime_guards import normalize_regime  # noqa: E402
from custos.core.contracts import require  # noqa: E402
from custos.core import report_audit  # noqa: E402

# 2026-08-20（v0.84，Phase D 因子化）：资金意图强度迁入因子注册表
# （core/factors/capital_intent.py），本模块 import 调用——`sc.capital_intent_strength`
# 仍是同一函数对象，判定逻辑零变化。
from custos.core.factors.capital_intent import (  # noqa: E402,F401
    capital_intent_strength,
    resolve_capital_weights,
)

# 基本面因子化（v0.84）：fundamental_quality 实现在 core/factors/fundamentals.py，
# 此处 re-export（four_leg_resonance 与既有测试 `sc.fundamental_quality` 不变）。
from custos.core.factors.fundamentals import fundamental_quality  # noqa: E402

SCREENING_DIR = DATA / "screening"
REGISTRY_PATH = SCREEN_FORMULA_REGISTRY_FILE

BUCKET_ORDER = ["A", "B", "C", "D"]

# 个股共振矩阵：(technical_level, capital_intent_level) → base bucket。
# 两轴均为个股维度（技术结构 × 资金意图）；板块不参与分层，只进 score/共振/trade_style。
RESONANCE_MATRIX = {
    ("强", "强"): "A",
    ("强", "中"): "B",
    ("强", "弱"): "C",
    ("强", "未知"): "C",
    ("中", "强"): "B",
    ("中", "中"): "C",
    ("中", "弱"): "D",
    ("中", "未知"): "D",
    ("弱", "强"): "C",
    ("弱", "中"): "D",
    ("弱", "弱"): "D",
    ("弱", "未知"): "D",
}

# 板块状态 → (heat_level, pass_level)。
# ⚠️ 板块**只作提示/展示，不封顶、不进总分**（v0.24 起不封顶；v0.50 #37 阶段 A
# 起连 0.4 权重也移出总分）。v0.50 前曾有第三列「封顶 bucket」，早已无人消费
# （score_candidate 里以 `_sector_cap` 丢弃）——死列已删。
SECTOR_STATE_MAP = {
    "主升": ("强", "allow_A"),
    "修复": ("强", "allow_A"),
    "震荡": ("中", "allow_B"),
    "分歧": ("中", "allow_B"),
    "退潮": ("弱", "observe_only"),
}

NEXT_STEP = {
    # ⚠️ v0.50（#37 阶段 A）：原值 "generate_buy_plan" 是**虚假承诺**——
    # BuyPlan 契约已删、没有任何组件生成买入计划，该值零行动读者
    # （只有 daily_report / candidate_table 的**展示**列读 next_step）。
    # 改为如实的 "buy_review"（可买候选，人工复核）。
    "A": "buy_review",
    "B": "observe_price",
    "C": "long_term_track",
    "D": "avoid",
}

WAVE_TYPE_LABELS = {"buildup": "建仓波", "rally": "拉升波", "sprint": "冲刺波"}

# 技术分层阈值 —— 单一定义处。
# ⚠️ 2026-08-12（v0.50，#37 阶段 A，owner 拍板）：s_shape 主路径**已删除**
# （R2：S_shape 无 alpha、全市场阈值扫描无 lift ⇒ 不得驱动分层），
# 本路径（patterns 累加分）是唯一技术分口径；s_shape 降为展示/证据列
# （s_star/s_shape 字段仍落盘，不驱动分层/排序/可见性）。
# 历史上 s_shape 路径用 65/40（sstar_level）、回退路径用 60/30，同一个 62 分
# 两路分别落"中"/"强" —— 现在只剩 60/30 一套。
TECH_STRONG_FALLBACK = 60
TECH_MID_FALLBACK = 30

# 技术分八段分值 + 分层阈值默认表 == 现值（v0.84 权重外置）。
# 可经 SCREEN_FORMULA_REGISTRY.json 的 "scoring".weights 覆盖（仿 resolve_cap_rules：
# 未知键忽略、默认表兜底；默认值==现值 ⇒ 缺省行为逐字节不变）。
# 资金意图证据分值在同表（ci_* 键），由 factors/capital_intent.resolve_capital_weights 解析。
DEFAULT_TECH_WEIGHTS: dict[str, Any] = {
    "tech_strong_fallback": TECH_STRONG_FALLBACK,
    "tech_mid_fallback": TECH_MID_FALLBACK,
    # patterns 五单项
    "bbi_above": 5,
    "reversal_k_candidate": 4,
    "j_low": 24,  # v0.63 owner 定向回调：20→33→24
    "volume_contraction": 15,
    "relative_strength_strong": 15,
    # B1/CZ 对齐加分
    "five_day_entry": 8,
    "leader_volume": 6,
    "bottom_volume": 10,
    "repair_signals_each": 4,  # v0.61：每项 3→4
    "repair_signals_cap": 8,  # v0.61：上限 6→8
    "non_one_wave_confirmed": 5,
    # MACD 十大技术
    "macd_zone1": 3,
    "macd_zone1_restart": 5,
    "macd_bottom_divergence": 8,  # v0.60：5→8
    "macd_above_water": 7,  # v0.61：5→7
    "macd_bar_grow": 5,
    "macd_wm_bar_grow": 5,
    "macd_top_divergence": -8,  # v0.61：单独出现改分数层减分
    # 知行量价位置三态
    "zhixing_bull": 9,
    "zhixing_close_above_qsx": 5,
    "zhixing_in_qsx_dks_band": 5,
    # 点火/缩量企稳/复合确认 + B1 健康回调组合包
    "ignition": 4,
    "pullback_shrink": 5,  # v0.61：3→5
    "b1_ignition": 8,
    "b1_healthy_pullback_pack": 9,  # v0.64 组合奖
    # 趋势（周日共振 / ADX）
    "weekly_j_low": 5,
    "adx_gt_60": 5,
    # 近 10 日阴阳量
    "volume_yy_bull": 7,  # v0.61：5→7
    "volume_yy_bear": -5,
    # 出货形态分数层减分（封顶规则不动）
    "distribution_watch": -10,
    "distribution_high": -20,
}


def _is_numeric_weight(val: Any) -> bool:
    """权重覆盖值必须是 int/float；bool 是 int 子类，显式排除。"""
    return isinstance(val, (int, float)) and not isinstance(val, bool)


# 非数值覆盖值的 WARN 去重记录（每键每进程一次）：整池逐票打分都会 resolve，
# 不去重同一坏键会刷屏。
_WARNED_BAD_WEIGHT_KEYS: set = set()


def resolve_tech_weights(overrides: Optional[dict]) -> dict:
    """把外部（registry scoring.weights）传入的技术分权重并入默认表；未知键忽略。

    覆盖模式仿 `resolve_cap_rules`：默认表兜底，只认已知键。
    覆盖值必须是 int/float——字符串/None/bool 等忽略并用默认兜底 +
    stderr [WARN] 一次（绝不 raise：误写配置不能炸掉整池打分）。
    """
    w = dict(DEFAULT_TECH_WEIGHTS)
    if isinstance(overrides, dict):
        for key, val in overrides.items():
            if key not in w:
                continue
            if not _is_numeric_weight(val):
                if key not in _WARNED_BAD_WEIGHT_KEYS:
                    _WARNED_BAD_WEIGHT_KEYS.add(key)
                    print(
                        f"[WARN] scoring.weights[{key}]={val!r} 非数值，"
                        f"忽略并按默认 {w[key]} 计",
                        file=sys.stderr,
                    )
                continue
            w[key] = val
    return w


# 待回测启发式驱动的封顶规则开关。默认全开＝保持历史行为；关闭某项后不再据此
# 降档，改在 risk_flags 记录 "<rule>_detected_cap_disabled"（仍随候选落盘，便于
# 回测校准前后对比）。可经 SCREEN_FORMULA_REGISTRY.json 的 "scoring".cap_rules
# 覆盖，见 governance/contracts/SCREENING_WORKFLOW.md「可配置项」。
DEFAULT_CAP_RULES = {
    "sprint_wave": True,  # 冲刺波后首个 B1 禁买 → 封顶 B（检测阈值待回测）
    # v0.60（2026-08-14，owner）：量能撤退封顶去掉——与「缩量回调是 B1 健康形态」
    # 语义冲突（同一缩量事实一边加分一边封顶）。检出仍记录（cap_disabled 证据 flag）。
    "volume_retreat": False,  # 量能持续性=主力撤退 → 原封顶 C（CZ §14.6）
    "non_one_wave_revoked": True,  # 非一波流撤销 → 封顶 C（待回测）
    "distribution_cap": True,  # 主力出货五方式命中 → high 封 D / watch 封 C（B1 §七.3，待回测）
    "macd_divergence": True,  # MACD 顶背离/三打白骨精 → 封顶 C（macd十大技术，待回测）
    "liquidity_floor": False,  # 流动性(近20日均成交额)低于底线 → 封顶 C（默认关，仅flag；待回测后开）
}

# 流动性底线（亿元，待回测）：低于此值的候选打 low_liquidity；是否降档由 cap_rules.liquidity_floor 控制
LIQUIDITY_FLOOR_YI = 0.5

# sector_state.score 的量纲：generate_risk_and_sectors 用 float(score)>=60 门控
# 主升/修复，即 0-100。此常量供 normalize_sector_score 归一化/兜底，若未来 generator
# 改量纲，只需改 registry "scoring".sector_score_max 一处即可。
SECTOR_SCORE_MAX = 100.0


def resolve_cap_rules(cap_rules: Optional[dict]) -> dict:
    """把外部（registry/调用方）传入的 cap 开关并入默认表；未知键忽略。"""
    rules = dict(DEFAULT_CAP_RULES)
    if isinstance(cap_rules, dict):
        for key, val in cap_rules.items():
            if key in rules:
                rules[key] = bool(val)
    return rules


def normalize_sector_score(
    raw: Any, score_max: float = SECTOR_SCORE_MAX
) -> Optional[float]:
    """把 sector_state.score 归一化到 0-100 并 clamp，量纲异常/缺失时鲁棒兜底。

    - raw 为 None/非数值 → 0.0（板块无评分，等价最弱）。
    - raw 为 NaN/±inf → **None（板块分不可用）**，由调用方显式降级处理。
      ⚠️ 2026-08-03 审计 B8：NaN 不是"非数值"——`float("nan")` 不抛异常，
      于是它逃过 except 走到 clamp，而 `min(100.0, nan)` 按 IEEE 754（nan 与任何数
      比较都为 False）返回 **100.0**，`max(0.0, 100.0)` 再确认。结果是：
      sector_state.score 一旦脏成 NaN（generator 除零/空序列 mean），该板块的票
      就白拿 40% 权重的板块满分，被推进 A 池——**坏数据表现成了最好的数据**。
      inf 同理（inf/smax*100=inf → 100.0）。故此处必须区分"无评分"与"评分脏"。
    - score_max<=0 或非法 → 回退 SECTOR_SCORE_MAX（避免除零/放大）。
    - 结果一律 clamp 到 [0, 100]，确保 0.4*sector_score 的量纲不被脏数据放大。
    """
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(val):
        return None
    try:
        smax = float(score_max)
    except (TypeError, ValueError):
        smax = SECTOR_SCORE_MAX
    if not math.isfinite(smax) or smax <= 0:
        smax = SECTOR_SCORE_MAX
    return max(0.0, min(100.0, val / smax * 100.0))


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def cap_bucket(bucket: str, cap: str) -> str:
    """把 bucket 封顶到 cap（A 最优、D 最差；只降档、不升档）。"""
    return bucket if BUCKET_ORDER.index(bucket) >= BUCKET_ORDER.index(cap) else cap


def technical_score(
    cand: dict, weights: Optional[dict] = None
) -> tuple[int, str, dict]:
    """技术分（0-100）与技术面层级（强/中/弱）。确定性加减分。

    ⚠️ 2026-08-12（v0.50，#37 阶段 A，owner 拍板）：**s_shape 主路径已删除**。
    此前 s_shape 可用时分数=S**、层级=sstar_level（阈值 65/40），与回退路径
    （60/30）是两套口径、同一个 62 分两路落不同档。R2 已证 S_shape 无 alpha、
    全市场阈值扫描无 lift ⇒ 它不得驱动分层；现统一为 patterns 累加路径，
    阈值 TECH_STRONG_FALLBACK/TECH_MID_FALLBACK = 60/30。s_shape 的
    s_star/suggestion 仍随候选落盘（展示/证据列），不进本函数。

    ⚠️ 2026-08-14（v0.58，owner 逐条定权重）：bbi_above 25→**5**、反转K 25→**4**
    且**不再取代** j_low/缩量子项（R18：优秀 B1 里反转K 仅 4/10，非必要条件；
    J<13 常伴短暂破 BBI，+25 惩罚的正是 B1 形态）；底部巨量 6→10（缩量维持 15，
    owner 复核后不改）；删 j_mid 死分支（主池恒 J<13，13≤J<50 永远走不到）。新增：周线 J<13（周日
    共振）+5、ADX>60 +5、知行位置三态（多头 QSX>DKS +6 不变；骑线 C≥QSX +4；
    回踩区 QSX>C≥DKS +2）、近 10 日阳量>阴量 +5 / 阴量>阳量 **−5**（首个负分项）、
    出货形态分数层减分（watch −10 / high −20；**封顶规则不动**，见
    apply_risk_downgrades）。

    其余 B1/CZ 对齐加分（阈值见 enrich_candidates 顶部"待回测参数"）：
    five_day_entry +8、leader_volume +6、repair_signals 每项 +3（上限 +6）、
    non_one_wave=confirmed +5。返回 (score, level, factor_contrib)（factor_contrib
    落盘可复盘）。仍是未校准启发式——排序无 alpha（R2），分值只管分层与展示序。

    2026-08-18（#58 收尾）：函数体按逻辑块拆成 `_pattern_score` 等私有段，
    本函数只做「取数 → 逐段评分 → 汇总分级」；分值、contrib 键与键序均未变。
    2026-08-20（v0.84）：分值/分层阈值改走 `resolve_tech_weights(weights)`
    （registry scoring.weights 覆盖），`weights=None` 时全默认 == 现值。
    """
    w = resolve_tech_weights(weights)
    contrib: dict[str, Any] = {}
    score = 0
    # 段顺序＝历史上 factor_contrib 的键序，勿动（落盘明细供复盘对照）。
    for pts, part in (
        _pattern_score(cand, w),
        _b1_bonus_score(cand, w),
        _macd_score(cand, w),
        _zhixing_score(cand, w),
        _ignition_score(cand, w),
        _trend_score(cand, w),
        _volume_yy_score(cand, w),
        _distribution_score(cand, w),
    ):
        score += pts
        contrib.update(part)
    # 2026-08-16 review 修复：负分项引入后补下限——口径恢复为 0-100
    # （负分信息在 factor_contrib 里仍可见，展示分不跌破 0）。
    score = min(max(score, 0), 100)
    level = (
        "强"
        if score >= w["tech_strong_fallback"]
        else ("中" if score >= w["tech_mid_fallback"] else "弱")
    )
    return score, level, contrib


def _pattern_score(cand: dict, w: dict) -> tuple[int, dict]:
    """patterns 五单项：bbi/反转K/J低位/极致缩量/20日相对强度。"""
    patterns = cand.get("patterns") or {}
    contrib: dict[str, Any] = {}
    score = 0
    if patterns.get("bbi_above"):
        score += w["bbi_above"]
        contrib["bbi_above"] = w["bbi_above"]
    if patterns.get("reversal_k_candidate"):
        # v0.58：复合信号不再取代子项（j_low/volume_contraction 照常独算）——
        # R18 实测反转K 在优秀 B1 里仅 4/10，不是必要条件，不值取代价。
        score += w["reversal_k_candidate"]
        contrib["reversal_k_candidate"] = w["reversal_k_candidate"]
    if patterns.get("j_low"):
        # v0.61（owner 定向）：20 -> 33；v0.63（owner 定向回调，2026-08-16）：33 -> 24。
        # v0.61 的 33 使 08-14 池 70+ 达 67 只（915 池 7.3%，owner 判定膨胀）；
        # 单项回调到 24 后离线模拟 70+ 降到 ~26 只，且 8 只正例的技术档全部
        # 仍 ≥60（「强」）⇒ A/B 桶位一只不丢（002074=61/B、600184=66/A、
        # 600601=64/B）。「不被埋没」由分层承担，70+ 分数线回归稀缺。
        score += w["j_low"]
        contrib["j_low"] = w["j_low"]
    if patterns.get("volume_contraction"):
        score += w["volume_contraction"]
        contrib["volume_contraction"] = w["volume_contraction"]
    if patterns.get("relative_strength_strong"):
        score += w["relative_strength_strong"]
        contrib["relative_strength_strong"] = w["relative_strength_strong"]
    return score, contrib


def _b1_bonus_score(cand: dict, w: dict) -> tuple[int, dict]:
    """B1/CZ 对齐加分：五日战法/龙头量/底部巨量/修复信号/非一波流（+贴合度证据列）。"""
    contrib: dict[str, Any] = {}
    score = 0
    if (cand.get("five_day_entry") or {}).get("hit"):
        score += w["five_day_entry"]
        contrib["five_day_entry"] = w["five_day_entry"]
    leader = cand.get("leader_volume") or {}
    if leader.get("available") and leader.get("hit"):
        score += w["leader_volume"]
        contrib["leader_volume"] = w["leader_volume"]
    bottom = cand.get("bottom_volume") or {}
    if bottom.get("available") and bottom.get("hit"):
        score += w["bottom_volume"]
        contrib["bottom_volume"] = w["bottom_volume"]
    repair_hits = (cand.get("repair_signals") or {}).get("signals") or []
    if repair_hits:
        # v0.61（owner 定向）：每项 3 -> 4、上限 6 -> 8（正例里 J 拐头/缩量止跌
        # 常作为唯一修复证据，原分值过低）。
        pts = min(len(repair_hits) * w["repair_signals_each"], w["repair_signals_cap"])
        score += pts
        contrib["repair_signals"] = pts
    if (cand.get("non_one_wave") or {}).get("status") == "confirmed":
        score += w["non_one_wave_confirmed"]
        contrib["non_one_wave_confirmed"] = w["non_one_wave_confirmed"]
    # 完美 B1 图形贴合度（0-8 梯度）：**evidence_only，不加分**（v0.50 #37 阶段 A；
    # R2：仅描述性）。仍记录进 factor_contrib —— candidate_table 的「贴合」列
    # 从 score_detail.factor_contrib 读它；且「算过是 0」与「没算」要可分（审计）。
    fit = (cand.get("perfect_b1_fit") or {}).get("score")
    if fit is not None:
        contrib["perfect_b1_fit"] = fit
    return score, contrib


def _macd_score(cand: dict, w: dict) -> tuple[int, dict]:
    """MACD 十大技术（正向）：第一区间强势扩张 +3；第一区间再启动（3/5浪买点）+5；
    底背离 +8（v0.60 从 5 上调，owner）。负向：顶背离单独出现 −8（v0.61，
    见下方减分支），三打白骨精仍走 apply_risk_downgrades 封顶 C。
    v0.60（owner）：水上（DIF>0 且 DEA>0）+5、日线红柱增长 +5、周月红柱同增 +5。
    """
    mt = cand.get("macd_technics") or {}
    contrib: dict[str, Any] = {}
    score = 0
    if mt.get("available"):
        if mt.get("zone") == 1:
            score += w["macd_zone1"]
            contrib["macd_zone1"] = w["macd_zone1"]
        if mt.get("zone1_restart"):
            score += w["macd_zone1_restart"]
            contrib["macd_zone1_restart"] = w["macd_zone1_restart"]
        if (mt.get("bottom_divergence") or {}).get("hit"):
            score += w["macd_bottom_divergence"]
            contrib["macd_bottom_divergence"] = w["macd_bottom_divergence"]
        if mt.get("above_water"):
            # v0.61（owner 定向）：5 -> 7（正例 8/8 命中，零轴上是趋势票基础证据）。
            score += w["macd_above_water"]
            contrib["macd_above_water"] = w["macd_above_water"]
        if mt.get("bar_grow"):
            score += w["macd_bar_grow"]
            contrib["macd_bar_grow"] = w["macd_bar_grow"]
        if mt.get("wm_bar_grow"):
            score += w["macd_wm_bar_grow"]
            contrib["macd_wm_bar_grow"] = w["macd_wm_bar_grow"]
        # v0.61（owner 定向）：MACD 顶背离**单独出现**（无三打白骨精）由封顶 C 改为
        # 分数层减分 −8--正例 301076 顶背离命中但后续走强，封顶直接把它压出 A/B，
        # 与 v0.60「量能撤退去封顶留减分」同一口径：检出留痕、降序但不一票否决。
        # 三打白骨精（K线三高+MACD三低）仍走 apply_risk_downgrades 封顶 C。
        if (mt.get("top_divergence") or {}).get("hit"):
            score += w["macd_top_divergence"]
            contrib["macd_top_divergence"] = w["macd_top_divergence"]
    return score, contrib


def _zhixing_score(cand: dict, w: dict) -> tuple[int, dict]:
    """知行量价（good_b1）位置三态：多头 +9；骑线/回踩区 +5（互斥）。"""
    zx = cand.get("zhixing") or {}
    contrib: dict[str, Any] = {}
    score = 0
    if zx.get("available"):
        if zx.get("qsx_gt_dks"):
            score += w["zhixing_bull"]
            contrib["zhixing_bull"] = w["zhixing_bull"]
        # v0.58（owner ②）：位置三态——骑线（多头且 C≥QSX）+5；回踩区（QSX>C≥DKS）+5。
        # 2026-08-16 review 修复：骑线腿补 qsx_gt_dks 前提——空头排列（QSX<DKS）下
        # 价站 QSX 不算「骑趋势线上」，此前与回踩区腿条件不对称。
        if zx.get("qsx_gt_dks") and zx.get("close_above_qsx"):
            score += w["zhixing_close_above_qsx"]
            contrib["zhixing_close_above_qsx"] = w["zhixing_close_above_qsx"]
        elif zx.get("qsx_gt_dks") and zx.get("close_above_dks"):
            score += w["zhixing_in_qsx_dks_band"]
            contrib["zhixing_in_qsx_dks_band"] = w["zhixing_in_qsx_dks_band"]
    return score, contrib


def _ignition_score(cand: dict, w: dict) -> tuple[int, dict]:
    """点火/缩量企稳/复合确认 + B1 健康回调组合包。

    注意：b1_ignition 是复合信号（含 ignition/pullback_shrink 条件），此处
    子项与复合项有意叠加计分，待回测校准（与 reversal_k 的"复合取代子项"
    口径不同，属已知不一致，回测后统一）。
    """
    contrib: dict[str, Any] = {}
    score = 0
    if (cand.get("ignition") or {}).get("hit"):
        score += w["ignition"]
        contrib["ignition"] = w["ignition"]
    if (cand.get("pullback_shrink") or {}).get("hit"):
        # v0.61（owner 定向）：3 -> 5（回调缩量+持 DKS 是 B1 健康回调核心证据）。
        score += w["pullback_shrink"]
        contrib["pullback_shrink"] = w["pullback_shrink"]
    if (cand.get("b1_ignition") or {}).get("hit"):
        score += w["b1_ignition"]
        contrib["b1_ignition"] = w["b1_ignition"]
    # v0.64（owner 定向，2026-08-16）：**B1 健康回调组合包** +9--J 低位 ∧ 缩量
    # 回调持 DKS（pullback_shrink）∧ 知行多头（QSX>DKS）三腿齐备的组合奖。
    # 为什么用组合而不是单因子提权：v0.61 教训--单因子普涨使 08-14 池 70+ 达
    # 67 只（j_low 33 一项占 1/3）；组合只奖励**完整结构**：8 只正例 7/8 三腿齐
    # （002812 无缩量回调不吃包、73 分已够），08-14 池仅约 1/3 命中--离线模拟
    # 70+ 26->38（比 v0.61 的 67 少 43%），正例 8/8 回到 ≥70。
    if (
        (cand.get("patterns") or {}).get("j_low")
        and (cand.get("pullback_shrink") or {}).get("hit")
        and (cand.get("zhixing") or {}).get("available")
        and (cand.get("zhixing") or {}).get("qsx_gt_dks")
    ):
        score += w["b1_healthy_pullback_pack"]
        contrib["b1_healthy_pullback_pack"] = w["b1_healthy_pullback_pack"]
    return score, contrib


def _trend_score(cand: dict, w: dict) -> tuple[int, dict]:
    """v0.58（owner ③⑥）：周日共振（周线 J<13）+5；ADX>60（强趋势）+5。"""
    contrib: dict[str, Any] = {}
    score = 0
    if cand.get("weekly_j_low"):
        score += w["weekly_j_low"]
        contrib["weekly_j_low"] = w["weekly_j_low"]
    adx = cand.get("adx")
    if adx is not None and adx > 60:
        score += w["adx_gt_60"]
        contrib["adx_gt_60"] = w["adx_gt_60"]
    return score, contrib


def _volume_yy_score(cand: dict, w: dict) -> tuple[int, dict]:
    """v0.58（owner ④）：近 10 日阳量>阴量 +7（v0.61 从 5 上调）/ 阴量>阳量 −5。

    2026-08-16 review 修复：按总量比较，平局（阳量=阴量）不加不减——
    此前用 bull_gt_bear 单布尔，平局被当空方 −5。
    """
    vy = cand.get("volume_yy") or {}
    contrib: dict[str, Any] = {}
    score = 0
    if vy.get("available"):
        bull_v, bear_v = vy.get("bull_vol"), vy.get("bear_vol")
        if bull_v is not None and bear_v is not None:
            if bull_v > bear_v:
                # v0.61（owner 定向）：+5 -> +7（正例 5/8 命中；负向 −5 维持不变--
                # 正向证据强于负向惩罚是 owner 定向选择：不埋没正例优先于惩罚疑点）。
                score += w["volume_yy_bull"]
                contrib["volume_yy_bull"] = w["volume_yy_bull"]
            elif bear_v > bull_v:
                score += w["volume_yy_bear"]
                contrib["volume_yy_bear"] = w["volume_yy_bear"]
        elif vy.get("bull_gt_bear"):  # 旧形状（无总量键）兜底
            score += w["volume_yy_bull"]
            contrib["volume_yy_bull"] = w["volume_yy_bull"]
    return score, contrib


def _distribution_score(cand: dict, w: dict) -> tuple[int, dict]:
    """v0.58（owner ④）：出货形态在分数层也减分（封顶规则不动）——watch −10 / high −20。

    2026-08-16 review 修复：available 守卫——检测器未评估（旧落盘/手工构造
    残留 risk_level）时不得无证据减分。
    """
    dist = cand.get("distribution") or {}
    dist_level = dist.get("risk_level") if dist.get("available") else None
    if dist_level == "watch":
        return w["distribution_watch"], {"distribution_watch": w["distribution_watch"]}
    if dist_level == "high":
        return w["distribution_high"], {"distribution_high": w["distribution_high"]}
    return 0, {}


def sector_heat(sector_entry: Optional[dict]) -> tuple[str, str, str]:
    """板块状态 → (heat_level, pass_level, reason)。

    ⚠️ 板块只作**提示**（v0.24 起不封顶；v0.50 起不进总分）——pass_level 的
    allow/reject 字样是**展示层的历史词表**，不构成任何降档/封顶动作。
    """
    if not sector_entry:
        return "未知", "reject_A", "板块未映射或无 sector_state（仅提示，不影响分层）"
    state = str(sector_entry.get("state") or sector_entry.get("sector_state") or "")
    heat, pass_level = SECTOR_STATE_MAP.get(state, ("未知", "reject_A"))
    reason = {
        "allow_A": f"板块{state}（仅提示，不影响分层）",
        "allow_B": f"板块{state}（仅提示，不影响分层）",
        "observe_only": f"板块{state}，偏弱（仅提示，不影响分层）",
    }.get(pass_level, f"板块状态{state or '未知'}（仅提示，不影响分层）")
    return heat, pass_level, reason


def resonance_level(tech_level: str, heat_level: str) -> str:
    if (tech_level, heat_level) == ("强", "强"):
        return "强共振"
    if "强" in (tech_level, heat_level):
        return "弱共振"
    if (tech_level, heat_level) == ("弱", "弱"):
        return "反向"
    return "无共振"


# 资金意图强度（分层第二轴）已迁入因子注册表 core/factors/capital_intent.py
# （v0.84，Phase D 因子化，零行为变化）；阈值 CAP_STRONG/CAP_MID 与证据分值
# 的默认表也在那里，可经 registry "scoring".weights 覆盖（ci_* 键）。
# 本模块顶部 import 同一函数对象，`sc.capital_intent_strength` 调用方不变。


def trade_style_of(heat_level: str) -> str:
    """板块热度 → 交易属性提示（不影响分层，只提示持有周期）。"""
    if heat_level == "强":
        return "波段"  # 主升/修复：主线持续性强，适合波段
    if heat_level == "中":
        return "波段(谨慎)"  # 震荡/分歧
    return "短线(交易性)"  # 退潮/未知/缺失：板块不行，仅交易性机会


def market_permission(amv_state: str) -> str:
    """0AMV regime → 市场许可文案。入口归一,不假定调用方已经归一过。"""
    return {"做多": "允许", "空头": "观察"}.get(normalize_regime(amv_state), "仅低吸")


# fundamental_quality（公司品质档/三无标记）已迁入因子注册表
# core/factors/fundamentals.py（v0.84，Phase D 因子化，零行为变化），
# 本模块顶部 re-export 同一函数对象，`sc.fundamental_quality` 调用方不变。


def apply_risk_downgrades(amv_state, base_bucket, cand, rules, sector_score_available):
    """风险标记与 bucket 降级 —— **只降不升**的一串判据。

    2026-08-07 从 `score_candidate`（原 258 行）抽出。这是整个打分里最该单独看的一段：
    76 行、13 条独立降级判据，每条都能把候选往下压一级或直接踢出可买。
    抽出后可以对着单条判据写测试，而不必构造一整份充实候选。

    返回 `(risk_flags, bucket, wave_type, dist)` —— 后三个下游还要用
    （`wave_type` 决定 next_step、`dist` 进 entry_reason、`bucket` 是分层结论）。

    2026-08-19（#58 收尾）：判据按主题拆成 `_cap_*` 段落函数，本函数只做
    「取数 → 逐段封顶 → 汇总返回」；flag 追加顺序与封顶语义逐条未变。
    v0.80（owner 拍板）：`_cap_cz_sector`（CZ 回避方向板块 → D）整段删除——
    板块证据链 v0.79 证伪，全仓唯一真实板块否决随 CZ_SECTOR_PREFERENCE 名单一并移除。
    """
    risk_flags: list[str] = []
    if cand.get("is_holding"):
        risk_flags.append("is_holding")
    if not sector_score_available:
        # 脏板块分只降不升：按 0 计入总分并显式 flag（score_all 据此把整池标 partial）
        risk_flags.append("sector_score_unavailable")

    # 风控/回避硬否决（cap 只降不升；与"板块弱"无关，故板块不在此列）
    bucket = _cap_stop_loss_and_bear(risk_flags, base_bucket, cand, amv_state)
    bucket, wave_type = _cap_wave_rules(risk_flags, bucket, cand, rules)
    dist = cand.get("distribution") or {}
    bucket = _cap_distribution(risk_flags, bucket, dist, rules)
    bucket = _cap_macd_risks(risk_flags, bucket, cand, rules)
    bucket = _cap_liquidity(risk_flags, bucket, cand, rules)
    return risk_flags, bucket, wave_type, dist


def _cap_stop_loss_and_bear(
    risk_flags: list, bucket: str, cand: dict, amv_state
) -> str:
    """止损位缺失 → 封顶 B；0AMV 空头 → 封顶 B（均无 rules 开关的硬否决）。"""
    if not (cand.get("stop_loss_ref") or {}).get("price"):
        risk_flags.append("no_stop_loss_ref")
        bucket = cap_bucket(bucket, "B")
    if amv_state == "空头":
        bucket = cap_bucket(bucket, "B")
    return bucket


def _cap_wave_rules(risk_flags: list, bucket: str, cand: dict, rules: dict):
    """波浪/量能/一波流三条：冲刺波首个 B1 → 封顶 B；量能撤退、非一波流撤销 → 封顶 C。"""
    wave_type = (cand.get("wave") or {}).get("wave_type")
    if wave_type == "sprint":
        # B1 §四.0：冲刺波后首个 B1 禁止买入 → 最高 B
        if rules["sprint_wave"]:
            risk_flags.append("sprint_wave_first_b1_forbidden")
            bucket = cap_bucket(bucket, "B")
        else:
            risk_flags.append("sprint_wave_detected_cap_disabled")
    if (cand.get("volume_sustain") or {}).get("status") == "retreat":
        # CZ §14.6：连续3日量<峰值55%，主力撤退 → 最高 C
        if rules["volume_retreat"]:
            risk_flags.append("main_force_retreat")
            bucket = cap_bucket(bucket, "C")
        else:
            risk_flags.append("main_force_retreat_cap_disabled")
    if (cand.get("non_one_wave") or {}).get("status") == "revoked":
        # B1 §四：非一波流撤销（顶部放量大阴/回调放量破位）→ 最高 C
        if rules["non_one_wave_revoked"]:
            risk_flags.append("non_one_wave_revoked")
            bucket = cap_bucket(bucket, "C")
        else:
            risk_flags.append("non_one_wave_revoked_cap_disabled")
    return bucket, wave_type


def _cap_distribution(risk_flags: list, bucket: str, dist: dict, rules: dict) -> str:
    """B1 §七.3：主力出货五方式命中 → 顶部派发规避（high 封 D / 其余封顶 C）。"""
    if dist.get("available") and dist.get("hits"):
        if rules["distribution_cap"]:
            if dist.get("risk_level") == "high":
                risk_flags.append("distribution_high")
                bucket = "D"
            else:
                risk_flags.append("distribution_watch")
                bucket = cap_bucket(bucket, "C")
        else:
            risk_flags.append("distribution_detected_cap_disabled")
    return bucket


def _cap_macd_risks(risk_flags: list, bucket: str, cand: dict, rules: dict) -> str:
    """MACD 十大技术风险：三打白骨精 → 封顶 C；顶背离仅留痕；overextended 仅记录。"""
    mt_cap = cand.get("macd_technics") or {}
    top_div_hit = (mt_cap.get("top_divergence") or {}).get("hit")
    three_peaks_hit = (mt_cap.get("three_peaks") or {}).get("hit")
    if mt_cap.get("available") and (top_div_hit or three_peaks_hit):
        # MACD 十大技术：三打白骨精（K线三高+MACD三低）→ 封顶 C；
        # 顶背离单独出现 v0.61 起只减分（technical_score −8），不再封顶。
        if rules["macd_divergence"]:
            if top_div_hit:
                risk_flags.append("macd_top_divergence")
            if three_peaks_hit:
                risk_flags.append("macd_three_peaks")
                # v0.61（owner 定向）：仅三打白骨精封顶 C；顶背离单独出现不再封顶，
                # 改分数层减分 -8（见 technical_score），risk_flag 仍留痕。
                bucket = cap_bucket(bucket, "C")
        else:
            risk_flags.append("macd_divergence_detected_cap_disabled")
    if mt_cap.get("available") and (mt_cap.get("overextended") or {}).get("hit"):
        risk_flags.append("macd_overextended")  # 开口/空间拐离：仅记录，不降档
    return bucket


def _cap_liquidity(risk_flags: list, bucket: str, cand: dict, rules: dict) -> str:
    """流动性底线（近20日均成交额），默认仅 flag；registry cap_rules.liquidity_floor=true 才封顶 C。"""
    liq = cand.get("liquidity") or {}
    if (
        liq.get("available")
        and liq.get("avg_amount_yi") is not None
        and liq["avg_amount_yi"] < LIQUIDITY_FLOOR_YI
    ):
        risk_flags.append("low_liquidity")
        if rules.get("liquidity_floor"):
            bucket = cap_bucket(bucket, "C")
    return bucket


def build_entry_reasons(cand, dist, wave_type):
    """把命中的公式、形态、信号翻译成人读的入选理由清单。

    2026-08-07 从 `score_candidate` 抽出。它只做**措辞**，不参与任何判定 ——
    与上面的 `apply_risk_downgrades`（判定）分开，改文案时不必担心动到分层。

    2026-08-19（#58 收尾）：按信号来源拆成 `_reason_*` 段落函数，本函数只做
    调度；文案与追加顺序逐条未变。
    """
    entry_reason: list[str] = []
    _reason_formula_hits(cand, entry_reason)
    _reason_patterns(cand, entry_reason)
    _reason_b1cz_signals(cand, entry_reason)
    _reason_wave_and_zhixing(cand, wave_type, entry_reason)
    _reason_dist_and_macd(cand, dist, entry_reason)
    return entry_reason


def _reason_formula_hits(cand: dict, entry_reason: list) -> None:
    """公式命中清单。"""
    for fid in cand.get("formula_hits") or []:
        entry_reason.append(f"公式命中:{fid}")


def _reason_patterns(cand: dict, entry_reason: list) -> None:
    """patterns 五单项的中文标签。"""
    label = {
        "bbi_above": "收盘站上BBI",
        "j_low": "日J低位(<13)",
        "volume_contraction": "极致缩量",
        "reversal_k_candidate": "反转K候选",
        "relative_strength_strong": "20日相对强度>=+3pp",
    }
    for tag, hit in (cand.get("patterns") or {}).items():
        if hit:
            entry_reason.append(label.get(tag, tag))


def _reason_b1cz_signals(cand: dict, entry_reason: list) -> None:
    """B1/CZ 对齐信号：五日战法/龙头量/底部巨量/修复信号/非一波流。"""
    if (cand.get("five_day_entry") or {}).get("hit"):
        entry_reason.append("五日战法入场")
    if (cand.get("leader_volume") or {}).get("hit"):
        entry_reason.append("龙头量能")
    if (cand.get("bottom_volume") or {}).get("hit"):
        entry_reason.append("底部巨量")
    for sig in (cand.get("repair_signals") or {}).get("signals") or []:
        entry_reason.append(f"修复信号:{sig}")
    if (cand.get("non_one_wave") or {}).get("status") == "confirmed":
        entry_reason.append("非一波流确认")


def _reason_wave_and_zhixing(cand: dict, wave_type, entry_reason: list) -> None:
    """波浪类型 + 知行 B1 点火/多头（互斥二选一）。"""
    if wave_type and wave_type != "unknown":
        # 未登记的 wave_type 不得 KeyError 打挂整只票的打分：.get 兜底并留原值（审计）
        entry_reason.append(f"波浪:{WAVE_TYPE_LABELS.get(wave_type, wave_type)}")
    if (cand.get("b1_ignition") or {}).get("hit"):
        entry_reason.append("知行B1点火确认")
    elif (cand.get("zhixing") or {}).get("available") and (
        cand.get("zhixing") or {}
    ).get("qsx_gt_dks"):
        entry_reason.append("知行多头(QSX>DKS)")


def _reason_dist_and_macd(cand: dict, dist: dict, entry_reason: list) -> None:
    """出货信号清单 + MACD 正向信号（第一区间再启动/底背离）。"""
    for _dk in dist.get("hits") or []:
        entry_reason.append(f"出货信号:{_dk}")
    _mt = cand.get("macd_technics") or {}
    if _mt.get("available"):
        if _mt.get("zone1_restart"):
            entry_reason.append("MACD第一区间再启动")
        if (_mt.get("bottom_divergence") or {}).get("hit"):
            entry_reason.append("MACD底背离")


def four_leg_resonance(cand, permission, tech_level):
    """四面共振（市场/板块/基本面/技术）。

    ⚠️ 这是**证据层描述**，不是 gate —— `aligned` 不参与 bucket 或 next_step，
    只写进产物供复盘对账。R2 的结论是「跟随主流」机械规则不成立，
    所以共振度不得反过来放宽权限。

    ⚠️ v0.50（#37 阶段 A，owner 拍板）：`sector_phase.favorable`（板块相位）从
    `bull_candidate` 定义中**移出**——「可买」判定不再含板块腿（板块相位降级为
    情境标注列）；legs/aligned 仍保留四腿计数供展示。
    """
    fq = fundamental_quality(cand.get("financials"))
    sp_fav = bool((cand.get("sector_phase") or {}).get("favorable"))
    legs = {
        "market": permission == "允许",
        "sector": sp_fav,
        "fundamental": fq.get("tier") in ("优", "中"),
        "technical": tech_level == "强",
    }
    aligned = sum(1 for v in legs.values() if v)
    resonance_4leg = {
        **legs,
        "aligned": aligned,
        "label": {4: "四面共振", 3: "三面共振", 2: "两面", 1: "单面", 0: "无"}[aligned],
        "bull_candidate": bool(
            # v0.50：板块腿（sp_fav）移出——可买 = 市场允许 + 基本面优 + 技术强
            legs["market"] and fq.get("tier") == "优" and legs["technical"]
        ),
    }
    return fq, sp_fav, legs, aligned, resonance_4leg


def score_candidate(
    cand: dict,
    sector_entry: Optional[dict],
    amv_state: str,
    cap_rules: Optional[dict] = None,
    sector_score_max: float = SECTOR_SCORE_MAX,
    weights: Optional[dict] = None,
) -> dict:
    """对单只充实候选打分分层，输出 StockPool 契约条目（含打分明细）。

    cap_rules 传 None 时用 DEFAULT_CAP_RULES（全开＝历史行为）；显式传部分键可
    单独关闭某条待回测封顶规则（关闭后仅在 risk_flags 记录检出、不降档）。
    sector_score_max 指定 sector_state.score 的量纲上界，用于归一化到 0-100。
    weights（v0.84）为技术分/资金意图分值覆盖表（registry scoring.weights），
    None 时全默认 == 现值。
    """
    rules = resolve_cap_rules(cap_rules)
    tech_score, tech_level, factor_contrib = technical_score(cand, weights)
    capital_level, capital_score, capital_detail = capital_intent_strength(
        cand, weights
    )
    heat, pass_level, reason = sector_heat(sector_entry)
    trade_style = trade_style_of(heat)
    # 板块分不可用（NaN/inf）与"无评分"（None）在**打分上**都按最弱 0 处理，但前者是
    # 脏数据、必须在 risk_flags/degraded_reason 留痕，否则无从区分"板块真弱"和"数据坏"。
    sector_score_raw, sector_score, sector_score_available = _sector_score_parts(
        sector_entry, sector_score_max
    )

    # 分层由个股（技术结构 × 资金意图）定夺；板块不封顶（降为提示，只进 score/共振/trade_style）
    base_bucket = RESONANCE_MATRIX[(tech_level, capital_level)]
    res_level = resonance_level(tech_level, heat)
    permission = market_permission(amv_state)

    risk_flags, bucket, wave_type, dist = apply_risk_downgrades(
        amv_state, base_bucket, cand, rules, sector_score_available
    )

    # 总分 = 技术分（v0.50 #37 阶段 A，owner 拍板：板块分 0.4 权重与共振 ±5
    # **移出总分**——板块/共振继续落盘作展示列（sector_heat_filter / resonance /
    # score_detail.resonance_adj），不驱动分层、排序与可见性）。
    resonance_adj = {"强共振": 5, "弱共振": 0, "无共振": 0, "反向": -5}[res_level]
    total = float(min(100, max(0, tech_score)))

    entry_reason = build_entry_reasons(cand, dist, wave_type)

    next_step = _next_step(bucket, amv_state, wave_type, rules)

    # 四面共振(市场+板块+基本面+技术)——hint/优先级,不驱动分层。牛股=三/四面共振(cz理念)。
    fq, _sp_fav, _legs, _aligned, resonance_4leg = four_leg_resonance(
        cand, permission, tech_level
    )

    return {
        "code": cand.get("code", ""),
        "name": cand.get("name", ""),
        "sector": cand.get("sector", "未知"),
        "sector_source": cand.get("sector_source", ""),
        "theme_id": cand.get("theme_id", ""),
        # TDX 官方细分行业（881xxx，展示层「板块」列；与主题族 sector 并存，只透传不消费）
        "industry": cand.get("industry", "未知"),
        "formula_hits": cand.get("formula_hits") or [],
        "sector_heat_filter": {
            "sector_state": (sector_entry or {}).get("state")
            or (sector_entry or {}).get("sector_state")
            or "未知",
            "sector_score": sector_score,
            "sector_score_raw": sector_score_raw,
            "sector_score_available": sector_score_available,
            "heat_level": heat,
            "pass_level": pass_level,
            "reason": reason,
        },
        "resonance": {
            "technical_level": tech_level,
            "capital_intent_level": capital_level,
            "sector_heat_level": heat,
            "market_permission": permission,
            "resonance_level": res_level,
        },
        "trade_style": trade_style,
        "capital_intent": {
            "level": capital_level,
            "score": capital_score,
            "detail": capital_detail,
        },
        "stock_role": "未定",
        "relative_strength": "强"
        if (cand.get("patterns") or {}).get("relative_strength_strong")
        else "未定",
        "score": total,
        "score_detail": {
            "technical_score": tech_score,
            "capital_intent_level": capital_level,
            "capital_intent_score": capital_score,
            "sector_score": sector_score,
            "sector_score_raw": sector_score_raw,
            "base_bucket": base_bucket,
            "resonance_adj": resonance_adj,
            "cap_rules": rules,
            "factor_contrib": factor_contrib,
            "total": total,
        },
        "bucket": bucket,
        "entry_reason": entry_reason,
        "risk_flags": risk_flags,
        "next_step": next_step,
        "patterns": cand.get("patterns") or {},
        "daily_j": cand.get("daily_j"),
        # v0.94 修复：门内提醒（candidate_table，v0.89）读候选的涨跌幅列，
        # 但本字典是显式白名单、此前不透传 ⇒ 实盘该列恒为「-」（旧观察区
        # watchlist_outside_gate 由 enrich 侧显式带 change_pct，无此问题）。
        "change_pct": cand.get("change_pct"),
        "stop_loss_ref": cand.get("stop_loss_ref"),
        "is_holding": bool(cand.get("is_holding")),
        # 证据层透传段（顺序＝历史落盘字段顺序）：B1/CZ → 信号标注 → 指标/正交因子
        **_b1cz_passthrough(cand),
        **_signal_evidence_passthrough(cand),
        **_indicator_passthrough(cand),
        "fundamental_quality": fq,
        "resonance_4leg": resonance_4leg,
    }


def _sector_score_parts(
    sector_entry: Optional[dict], sector_score_max: float
) -> tuple[Any, float, bool]:
    """板块分三件：raw 原值 / 归一化分（不可用按 0）/ 是否可用（NaN/inf → False）。"""
    raw = (sector_entry or {}).get("score") if sector_entry else None
    norm = normalize_sector_score(raw, sector_score_max)
    # 同值（available 即 norm 非 None），显式判空便于收窄
    return raw, (norm if norm is not None else 0.0), norm is not None


def _next_step(bucket: str, amv_state: str, wave_type: Any, rules: dict) -> str:
    """bucket → next_step；空头与冲刺波首个 B1（双保险）一律 observe_price。"""
    step = NEXT_STEP[bucket]
    if amv_state == "空头":
        step = "observe_price"
    if wave_type == "sprint" and rules["sprint_wave"] and step == "buy_review":
        # 双保险：冲刺波后首个 B1 禁买，不得进可买候选
        step = "observe_price"
    return step


def _b1cz_passthrough(cand: dict) -> dict:
    """B1/CZ 策略对齐落盘字段（证据层透传，不参与打分）。"""
    return {
        "wave": cand.get("wave") or {},
        "weekly_j": cand.get("weekly_j"),
        "weekly_j_low": bool(cand.get("weekly_j_low")),
        "non_one_wave": cand.get("non_one_wave") or {},
        "repair_signals": cand.get("repair_signals") or {},
        "five_day_entry": cand.get("five_day_entry") or {},
        "volume_sustain": cand.get("volume_sustain") or {},
        "leader_volume": cand.get("leader_volume") or {},
        "three_lows": cand.get("three_lows") or {},
        "bottom_volume": cand.get("bottom_volume") or {},
    }


def _signal_evidence_passthrough(cand: dict) -> dict:
    """知行量价/出货识别/底部侧/公司地位 + 信号标注层（纯透传，不参与打分）。

    ⚠️ signals 只允许出现在这里（纯映射）。一旦被读进打分逻辑，就从 A 类（纯标注）
    变成 B 类（改分层），必须先过回测——见 tests/test_signal_labels.py。
    """
    return {
        # v0.58 阴阳量（近 10 日阳量/阴量对比）——技术分加减分输入，落盘供复盘
        "volume_yy": cand.get("volume_yy") or {},
        # 知行量价 + 出货识别（good_b1 / 出货五方式）
        "zhixing": cand.get("zhixing") or {},
        "ignition": cand.get("ignition") or {},
        "pullback_shrink": cand.get("pullback_shrink") or {},
        "ride_above_fast": bool(cand.get("ride_above_fast")),
        "b1_ignition": cand.get("b1_ignition") or {},
        "distribution": cand.get("distribution") or {},
        # 次日确认豁免层（2026-08-13，25chuhuo 缺口）——证据层透传，不进分层
        "distribution_confirm": cand.get("distribution_confirm") or {},
        # v0.56 底部侧（25chuhuo 底部镜像）——同为证据层透传
        "w_bottom": cand.get("w_bottom") or {},
        "red_fat_green_thin": cand.get("red_fat_green_thin") or {},
        # v0.59（owner ⑧）：公司地位证据（东财 F10 简介关键词）——证据层透传
        "company_position": cand.get("company_position") or {},
        # 信号标注层（A 类改动）：**只透传，不参与打分**。
        # score_candidate 是显式字段白名单，enrich 落盘的 signals 不加在这里就会被
        # 丢掉——2026-08-04 实盘即因此出现「157 只候选、信号标注区块全空」。
        "signals": cand.get("signals") or {},
    }


def _indicator_passthrough(cand: dict) -> dict:
    """S_shape/ADX 等严格证据列 + 正交因子（流动性/资金流/基本面/板块相位）透传。"""
    s_shape = cand.get("s_shape") or {}
    return {
        # S_shape v3.0 有界评分（借鉴 workflow 沙漏模型）
        # v0.50：s_shape 移出分层（仅展示）；v0.51：s_reversal/adx25 同为
        # 严格证据列（#37 阶段 B）——只透传，不进技术分/分层/gate。
        "s_shape": s_shape,
        "s_star": s_shape.get("s_star"),
        "suggestion": s_shape.get("suggestion"),
        "s_reversal": cand.get("s_reversal") or {},
        "adx": cand.get("adx"),
        "adx25": bool(cand.get("adx25")),
        # 正交因子（方向A）：流动性 + 资金流向
        "liquidity": cand.get("liquidity") or {},
        "fund_flow": cand.get("fund_flow") or {},
        "financials": cand.get("financials") or {},
        "sector_phase": cand.get("sector_phase") or {},
    }


def _load_scoring_config(path: Optional[Path] = None) -> dict:
    """读 SCREEN_FORMULA_REGISTRY.json 的 "scoring" 段（cap_rules / sector_score_max
    / weights）；缺失/损坏返回 {}（调用方回退默认，行为不变）。"""
    p = Path(path) if path else REGISTRY_PATH
    data = _load_json(p, {})
    scoring = data.get("scoring") if isinstance(data, dict) else None
    return scoring if isinstance(scoring, dict) else {}


def score_all(
    date: str,
    enriched: Optional[dict] = None,
    sector_states: Optional[list] = None,
    amv_state: Optional[str] = None,
    cap_rules: Optional[dict] = None,
    sector_score_max: Optional[float] = None,
    weights: Optional[dict] = None,
) -> dict:
    """整池打分。输入缺失时干净降级，绝不 raise。

    cap_rules / sector_score_max / weights 传 None 时从 registry "scoring" 段加载，
    缺失回退默认（全开 + 0-100 + 现值权重），行为与历史一致。

    2026-08-19（#58 收尾）：按阶段拆成 `_resolve_*` / `_score_all_shell` /
    `_score_pool` 段落函数，本函数只做调度；status/degraded_reason 语义与
    candidates 排序键均未变。
    v0.80（owner 拍板）：cz_preference 输入与 cz_sector_status/cz_sector_preference_missing
    降级分支随 CZ 板块名单机制一并移除。
    v0.84：weights（scoring.weights，技术分/资金意图分值覆盖）自 registry 加载
    并透传到 score_candidate；默认值 == 现值 ⇒ 缺省打分结果不变。生效权重随
    结果壳的 `weights` 键落盘（审计：registry 改权重后 stock_pool.json 有记录）。
    """
    enriched, sector_states, amv_state = _resolve_score_all_inputs(
        date, enriched, sector_states, amv_state
    )

    cap_rules, sector_score_max, effective_caps, weights = _resolve_scoring_settings(
        cap_rules, sector_score_max, weights
    )

    result = _score_all_shell(
        date, amv_state, effective_caps, sector_score_max, enriched, weights
    )

    if not enriched or enriched.get("status") == "unavailable":
        result["status"] = "unavailable"
        result["degraded_reason"] = (
            f"enriched_unavailable:{(enriched or {}).get('degraded_reason', 'missing')}"
        )
        return result
    if not sector_states:
        result["status"] = "partial"
        result["degraded_reason"] = "sector_state_missing"
    if not amv_state:
        # market_timing 缺失：按保守处理（不放宽任何 cap，视同仅低吸），
        # 但必须显式标注，不得静默。
        _mark_partial(result, "market_timing_missing")

    by_theme, by_name = _sector_state_index(sector_states)
    _score_pool(
        result,
        enriched,
        by_theme,
        by_name,
        amv_state,
        cap_rules,
        sector_score_max,
        weights,
    )

    # 板块分脏（NaN/inf）必须整池留痕：单票 risk_flags 只有细看明细才发现，
    # 而 status/degraded_reason 是报告与门控真正会读的字段（审计 B8）。
    dirty_sector = [
        c["code"]
        for c in result["candidates"]
        if "sector_score_unavailable" in (c.get("risk_flags") or [])
    ]
    if dirty_sector:
        _mark_partial(
            result,
            f"sector_score_unavailable:{len(dirty_sector)}只(板块分NaN/inf,按0计入)",
        )

    result["candidates"].sort(
        key=lambda x: (BUCKET_ORDER.index(x["bucket"]), -x["score"], x["code"])
    )
    return result


def _resolve_score_all_inputs(
    date: str,
    enriched: Optional[dict],
    sector_states: Optional[list],
    amv_state: Optional[str],
) -> tuple:
    """三路输入的落盘加载与归一化（显式传入优先，None 才读盘）。"""
    if enriched is None:
        enriched = _load_json(SCREENING_DIR / f"{date}_candidates_enriched.json", {})
    if sector_states is None:
        sector_states = _load_json(SECTORS_DIR / f"{date}_sector_state.json", [])
    if amv_state is None:
        market = _load_json(MARKET_DIR / f"{date}_market_timing_input.json", {})
        amv_0 = market.get("amv_0") or {}
        # 归一化后再判定:amv_zone 的"空头触发"若按 == "空头" 比较会漏判,
        # 导致空头封顶 B 与 next_step 降级双双失效(审计 B1)。
        amv_state = normalize_regime(
            amv_0.get("effective_state") or amv_0.get("amv_zone") or ""
        )
    else:
        amv_state = normalize_regime(amv_state)
    return enriched, sector_states, amv_state


def _resolve_scoring_settings(
    cap_rules: Optional[dict],
    sector_score_max: Optional[float],
    weights: Optional[dict] = None,
) -> tuple:
    """cap_rules / sector_score_max / weights 的 registry 兜底 + 有效开关合并。"""
    if cap_rules is None or sector_score_max is None or weights is None:
        scoring_cfg = _load_scoring_config()
        if cap_rules is None:
            cap_rules = scoring_cfg.get("cap_rules")
        if sector_score_max is None:
            sector_score_max = scoring_cfg.get("sector_score_max", SECTOR_SCORE_MAX)
        if weights is None:
            weights = scoring_cfg.get("weights")
    return cap_rules, sector_score_max, resolve_cap_rules(cap_rules), weights


def _score_all_shell(
    date: str,
    amv_state: str,
    effective_caps: dict,
    sector_score_max: float,
    enriched: Optional[dict],
    weights: Optional[dict] = None,
) -> dict:
    """整池结果的初始壳（键序＝历史落盘字段顺序，勿动）。"""
    return {
        "date": date,
        "status": "ok",
        "degraded_reason": "",
        "source": "screening_chain_v1",
        "amv_state": amv_state or "未知",
        "market_permission": market_permission(amv_state),
        "cap_rules": effective_caps,
        "sector_score_max": float(sector_score_max),
        "bucket_counts": {"A": 0, "B": 0, "C": 0, "D": 0},
        "candidates": [],
        # v0.84 修复（code review 审计缺口）：生效权重落盘——registry 改权重后
        # stock_pool.json 有记录可查。新键加在末尾（上方历史键序勿动）；
        # 两组默认表键不相交（ci_* 前缀），合并即为完整生效表。
        "weights": {
            **resolve_tech_weights(weights),
            **resolve_capital_weights(weights),
        },
    }


def _mark_partial(result: dict, note: str) -> None:
    """status ok→partial（只降不升），并把降级原因追加进 degraded_reason。"""
    if result["status"] == "ok":
        result["status"] = "partial"
    result["degraded_reason"] = (
        f"{result['degraded_reason']};{note}" if result["degraded_reason"] else note
    )


def _sector_state_index(sector_states) -> tuple[dict, dict]:
    """theme_id / sector 名 → sector_state 条目（双索引）。"""
    by_theme: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for s in sector_states if isinstance(sector_states, list) else []:
        if s.get("theme_id"):
            by_theme[str(s["theme_id"])] = s
        if s.get("sector"):
            by_name[str(s["sector"])] = s
    return by_theme, by_name


def _score_pool(
    result: dict,
    enriched: dict,
    by_theme: dict,
    by_name: dict,
    amv_state: str,
    cap_rules: Optional[dict],
    sector_score_max: float,
    weights: Optional[dict] = None,
) -> None:
    """逐票打分并累计 bucket 计数（结果就地写进 result）。"""
    for cand in enriched.get("candidates", []):
        entry = by_theme.get(cand.get("theme_id", "")) or by_name.get(
            cand.get("sector", "")
        )
        scored = score_candidate(
            cand,
            entry,
            amv_state,
            cap_rules=cap_rules,
            sector_score_max=sector_score_max,
            weights=weights,
        )
        result["candidates"].append(scored)
        result["bucket_counts"][scored["bucket"]] += 1


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="screening 链第 3 段：板块过滤+共振打分分层（确定性）"
    )
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    result = score_all(args.date)

    # 可审计块（原待办 #29，已实现）：登记选股链实际读过的输入，出问题时可定位规则版本与数据时点
    result["audit"] = report_audit.build(
        args.date,
        "screening",
        [
            SCREENING_DIR / f"{args.date}_candidates_enriched.json",
            SECTORS_DIR / f"{args.date}_sector_state.json",
            MARKET_DIR / f"{args.date}_market_timing_input.json",
        ],
    )
    STOCK_POOL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = STOCK_POOL_DIR / f"{args.date}_stock_pool.json"
    require("stock_pool", result)
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "date": args.date,
        "status": result["status"],
        "degraded_reason": result["degraded_reason"],
        "bucket_counts": result["bucket_counts"],
        "output": str(out_path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
