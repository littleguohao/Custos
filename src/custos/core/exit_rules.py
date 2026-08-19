# -*- coding: utf-8 -*-
"""止盈/止损规则目录 —— **live 链的唯一来源**（L0，只依赖 `paths`（L0）与 stdlib）。

## 为什么单独一个模块

−10% 硬止损 / −7% 减仓阈值原先有三份硬编码拷贝：

    pipeline/holdings/b1_holding_state.py        _pnl_signals       pnl <= -0.10 / -0.07
    pipeline/close_review/review_core.py         _hard_risk_signal  pnl <= -0.07
    pipeline/holdings/portfolio_review_report.py classify           pnl <= -0.10 / -0.07

三处都是 **L3**（holdings / close_review，彼此不能互相 import），共享常量只能
上提到 L0 —— 与 `b1_thresholds`（反转 K 阈值，2026-08-07 收敛）同一个理由：
「可配置」只覆盖一半的链，另一半就在谎报自己的阈值。

## 目录内容

止损方案：`hard_loss`（−10% 硬止损）/ `loss_reduction`（−7% 减仓）/
`breakeven_stop`（保本止损）/ `trailing_stop`（移动止损）/ `time_stop`（时间止损）
止盈方案：`scale_out_two_bull`（BBI 上方连续两根中大阳分批止盈）/
`cost_zone_flat`（成本区「不涨就拍」平仓）

每个方案 ``{rule_id, enabled, params}``；**默认 enabled/params == 当前 live
实际行为**：hard_loss / loss_reduction / scale_out_two_bull 在跑（enabled=True，
live 三处判定点直接消费本模块常量）；breakeven_stop / trailing_stop /
cost_zone_flat / time_stop 只在研究侧 `research/backtest_factors.py`
（``_update_dynamic_stop`` / ``_cost_zone_flat`` 等），live 本就不跑 ⇒
enabled=False，params 记录研究侧默认值（键名与 `simulate_b1_trade` 形参一致，
供 Phase E 联合寻优扫出的配置直接拷回 —— 研究→live 回流通道）。

减仓幅度表（P0 全清 / P1 减 10-25% / P2 减 10-20%）同源收进
`REDUCTION_PCT_OF_HOLDING`（原 `b1_holding_state.evaluate` 内联字面量）。

## 刻意不并入的边界

- `weekly_review.STOP_LOSS_PCT` / `cooldowns.STOP_COOLDOWN_THRESHOLD_PCT` 的 −7%
  是**已实现亏损**的止损合规复盘口径（单位是百分数 −7.0，不是这里的 −0.07
  小数），语义不同，不动。
- 研究侧 `backtest_factors` 的出场参数**不读**这里（与 b1_thresholds 的豁免同理：
  钉死才能复现既有回测数字）；两边 schema 一致，默认值各自独立。

## 配置覆盖

`governance/contracts/EXIT_RULES.json` 可覆盖 enabled/params/减仓幅度表
（仿 `score_candidates.resolve_cap_rules`：未知方案、未知参数键忽略，默认表
兜底；文件缺失/损坏回落默认表，行为不变）。

⚠️ 与 `b1_thresholds` 同样在**模块导入时**求值。运行中改文件对已导入的模块
无效，必须 `importlib.reload` —— 阈值在一次运行内保持恒定，否则同一份报告里
不同股票可能按不同阈值判定。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from custos.core.paths import EXIT_RULES_FILE

# --- 止损方案目录（默认 == 当前 live 实际行为）---
DEFAULT_STOP_RULES: dict[str, dict[str, Any]] = {
    "hard_loss": {
        "rule_id": "hard_loss",
        "enabled": True,
        # 持有盈亏 <= -10% 硬风控清仓（P0），live：b1_holding_state / portfolio_review_report
        "params": {"pnl_pct": -0.10},
    },
    "loss_reduction": {
        "rule_id": "loss_reduction",
        "enabled": True,
        # 持有盈亏 <= -7% 减仓（P1），live：上述两处 + review_core._hard_risk_signal
        "params": {"pnl_pct": -0.07},
    },
    "breakeven_stop": {
        "rule_id": "breakeven_stop",
        "enabled": False,  # 仅研究侧（backtest_factors 保本止损，live 不跑）
        # 研究侧默认 breakeven_trigger=0.0（0 = 不启用，见 simulate_b1_trade 签名）
        "params": {"breakeven_trigger": 0.0},
    },
    "trailing_stop": {
        "rule_id": "trailing_stop",
        "enabled": False,  # 仅研究侧（移动止损，live 不跑）
        "params": {"trail_pct": 0.0},
    },
    "time_stop": {
        "rule_id": "time_stop",
        "enabled": False,  # 仅研究侧（time_stop_bars，live 不跑）
        "params": {"time_stop_bars": 0},
    },
}

# --- 止盈方案目录 ---
DEFAULT_TAKE_PROFIT_RULES: dict[str, dict[str, Any]] = {
    "scale_out_two_bull": {
        "rule_id": "scale_out_two_bull",
        "enabled": True,  # live：b1_holding_state 的 two_bull_profit_take（P2 分批止盈）
        "params": {"consecutive_bull_bars": 2, "require_above_bbi": True},
    },
    "cost_zone_flat": {
        "rule_id": "cost_zone_flat",
        "enabled": False,  # 仅研究侧（成本区「不涨就拍」，live 不跑）
        # 研究侧默认 cost_zone_bars=0（不启用）；cost_zone_pct/grace 为启用时默认值
        "params": {"cost_zone_bars": 0, "cost_zone_pct": 3.0, "cost_zone_grace": 1},
    },
}

# --- 减仓幅度表（占持仓 %；原 b1_holding_state.py 内联字面量）---
DEFAULT_REDUCTION_PCT_OF_HOLDING: dict[str, list[int]] = {
    "P0": [100, 100],  # 全清
    "P1": [10, 25],
    "P2": [10, 20],
}


def resolve_exit_rules(overrides: Any) -> dict[str, Any]:
    """把外部（EXIT_RULES.json/调用方）传入的覆盖并入默认表；未知键忽略。

    覆盖粒度：方案级 ``enabled`` 与 ``params`` 内的**已知**参数键；
    未知方案 id、未知参数键一律忽略，默认表兜底。
    """
    rules: dict[str, Any] = {
        "stop_rules": copy.deepcopy(DEFAULT_STOP_RULES),
        "take_profit_rules": copy.deepcopy(DEFAULT_TAKE_PROFIT_RULES),
        "reduction_pct_of_holding": copy.deepcopy(DEFAULT_REDUCTION_PCT_OF_HOLDING),
    }
    if not isinstance(overrides, dict):
        return rules
    for section in ("stop_rules", "take_profit_rules"):
        sec = overrides.get(section)
        if not isinstance(sec, dict):
            continue
        for rule_id, patch in sec.items():
            rule = rules[section].get(rule_id)
            if rule is None or not isinstance(patch, dict):
                continue  # 未知方案 id 忽略
            if "enabled" in patch:
                rule["enabled"] = bool(patch["enabled"])
            params = patch.get("params")
            if isinstance(params, dict):
                for key, val in params.items():
                    if key in rule["params"]:
                        rule["params"][key] = val
    red = overrides.get("reduction_pct_of_holding")
    if isinstance(red, dict):
        for key, val in red.items():
            if key in rules["reduction_pct_of_holding"]:
                rules["reduction_pct_of_holding"][key] = val
    return rules


def load_exit_rule_overrides(path: Path | None = None) -> dict[str, Any]:
    """读 EXIT_RULES.json；缺失/损坏/非 dict 返回 {}（调用方回落默认表，行为不变）。"""
    p = Path(path) if path else EXIT_RULES_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ⚠️ 全部在**模块导入时**求值（同 b1_thresholds）：运行中改 EXIT_RULES.json
# 对已导入的模块无效，必须 importlib.reload。
_EFFECTIVE = resolve_exit_rules(load_exit_rule_overrides())

# live 三处判定点直接消费的生效值（默认值 == 原硬编码字面量，输出逐字节不变）
HARD_LOSS_ENABLED = bool(_EFFECTIVE["stop_rules"]["hard_loss"]["enabled"])
HARD_LOSS_PCT = float(_EFFECTIVE["stop_rules"]["hard_loss"]["params"]["pnl_pct"])
LOSS_REDUCTION_ENABLED = bool(_EFFECTIVE["stop_rules"]["loss_reduction"]["enabled"])
LOSS_REDUCTION_PCT = float(
    _EFFECTIVE["stop_rules"]["loss_reduction"]["params"]["pnl_pct"]
)
SCALE_OUT_TWO_BULL_ENABLED = bool(
    _EFFECTIVE["take_profit_rules"]["scale_out_two_bull"]["enabled"]
)
REDUCTION_PCT_OF_HOLDING: dict[str, list[int]] = _EFFECTIVE["reduction_pct_of_holding"]
