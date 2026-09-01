# 数据流契约

> ⚠️ **设计参考 / 部分重建**：本文档描述 Agent+skill 时代的数据流设计（skill_adapters、StockCandidate、BuyPlan 等），相关代码已移除。StockPool（StockCandidate）已由每日选股 screening 链重建（2026-07-21，见 `governance/contracts/SCREENING_WORKFLOW.md`）；其余实体重建时以此为设计蓝图对齐。

日期：2026-07-09

## 目标

统一各 Agent 的输入输出，避免后续扩展时数据混乱。

## 实现状态（2026-08-06 逐实体核查）

**契约的第一件事是说清哪些真有产出。** 现存 6 个实体全部有生产者：

| 实体 | 生产者 |
|---|---|
| MarketState | `generate_risk_and_sectors.py`、`market_timing/` |
| SectorState | `generate_risk_and_sectors.py` |
| StockCandidate | `screening/` 链（enrich → score → table）|
| HoldingReview | `holdings/b1_holding_state.py` |
| RiskDecision | `generate_risk_and_sectors.py` |
| ChiefDecision | `chief_decision_report.py`、`daily_report.py` |

#### 已删除的两个实体（2026-08-06）

| 实体 | 为什么删 | 独有内容去哪了 |
|---|---|---|
| **SkillEvidence** | Skill 架构遗留（`build_skill_contracts.py` + `skill_adapters.py` 已被 `generate_risk_and_sectors.py` 取代）。而且它描述的「**统一证据信封**」实际并不存在 —— `as_of`/`facts`/`signals`/`status`/`risk_flags` 这些字段确实散落在各产出里，但**没有任何一份产出同时具备它们** | 无独有内容 |
| **BuyPlan** | `buy_strategy` 代码已移除；残留的 `next_step="generate_buy_plan"` 标签也于 v0.50 改名 `buy_review` | **结论四档 / 买入方式五类 / 最大亏损比例**已抢救到 [`../strategy/b1/03_execution_discipline.md`](../strategy/b1/03_execution_discipline.md) |

⚠️ 另删掉 `RiskDecision.cooldown_list`：**声明过但从未实现**的风控机制
（全仓 `cooldown_list`/`blacklist` 零命中）。冷却 2026-08-12 已实现为复盘提示节（只提示不拦截），见 `close_review/cooldowns.py` 与 CHANGELOG v0.48。
删掉字段本身就是最好的标记 —— 契约里没有它，就不会有人以为它存在。

## 核心实体

### MarketState

```json
{
  "date": "YYYY-MM-DD",
  "market_state": "进攻|震荡偏强|震荡偏弱|防守|冰点",
  "score": 0,
  "position_range": "20%-40%",
  "new_position_permission": "允许|小仓试探|原则不允许|禁止",
  "risk_level": "普通|提高|强风控",
  "amv_state": "做多|中性|空头",            // ⚠️ 契约原写 `zero_amv_state`，代码实际用 `amv_state`/`amv_zone`/`effective_state`
  "evidence": []
}
```

### SectorState

```json
{
  "date": "YYYY-MM-DD",
  "sector": "AI算力",
  "state": "主升|修复|分歧|震荡|退潮",
  "trend": "上涨|横盘震荡|下跌",
  "relative_strength": "强于大盘|同步大盘|弱于大盘",
  "support": null,
  "resistance": null,
  "trade_permission": "支持|观察|回避",
  "risk_flags": []
}
```

### StockCandidate

```json
{
  "code": "600000",
  "name": "示例股票",
  "sector": "示例板块",
  "theme_id": "semiconductor_chip_memory_packaging",
  "source": ["theme_tracker", "tdx_screener", "industry_research", "formula_screen"],
  // ⚠️ 契约原写嵌套的 `technical_sources[{source_id, signal, technical_score, raw_rank}]`，
  // 实际产出是**平铺**的（`technical` / `technical_level` / `technical_score` / `signals`），
  // 且从无 `technical_sources` 与 `raw_rank` 这两个名字。已按实际形状改写：
  "technical": {},
  "technical_level": "强|中|弱|未知",
  "technical_score": 0,
  "signals": [],
  "sector_heat_filter": {
    "sector_state": "主升|修复|分歧|震荡|退潮|未知",
    "sector_score": 0,
    "heat_level": "强|中|弱|未知",
    "pass_level": "allow_A|allow_B|observe_only|reject_A|reject_all",
    "reason": ""
  },
  "resonance": {
    "technical_level": "强|中|弱",
    "sector_heat_level": "强|中|弱|未知",
    "market_permission": "允许|仅低吸|观察|禁止",
    "resonance_level": "强共振|弱共振|无共振|反向"
  },
  "stock_role": "龙头|核心|中军|弹性|后排|未定",
  "relative_strength": "强于板块|同步板块|弱于板块|未定",
  "score": 0,
  "bucket": "A|B|C|D",
  "entry_reason": [],
  "risk_flags": [],
  "next_step": "buy_review|observe_price|long_term_track|avoid"
}
```

### HoldingReview

```json
{
  "code": "600000",
  "name": "示例股票",
  "position_pct": 0.0,
  "pnl_pct": 0.0,
  "holding_days": 0,
  "sector": "",
  "trend_state": "上涨|横盘震荡|下跌",
  "box_position": "上沿/突破区|箱体上半区|箱体下半区|下沿/破位区",
  "daily_j": null,
  "macd_state": "扩张|收缩",
  "action": "持有|观察|减仓|止损|清仓",
  "reason": []
}
```

### RiskDecision

```json
{
  "date": "YYYY-MM-DD",
  "risk_level": "普通|提高|强风控",
  "forbidden_actions": [],
  "stock_risks": [
    {
      "code": "600000",
      "name": "示例股票",
      "risk_type": "破位|亏损扩大|板块退潮|无止损计划",   // 原枚举含「冷却」，与 cooldown_list 一起未实现，已移除
      "action": "禁止加仓|减仓|止损|清仓|观察",
      "priority": "高|中|低"
    }
  ]
}
```

### ChiefDecision

```json
{
  "date": "YYYY-MM-DD",
  "market_state": "",
  "total_position_range": "",
  "new_position_permission": "",
  "allowed_actions": [],
  "forbidden_actions": [],
  "holding_actions": [],
  "buy_actions": [],
  "watchlist": [],
  "tomorrow_validation": [],
  "risk_notice": ""
}
```

## 数据流规则

1. 下游不得绕过上游许可。
2. 缺少 market_timing 时，所有买入默认为仅观察。
3. 缺少 theme_tracker 时，个股不得直接进入 A池。
4. 缺少 stop_loss 时，buy_strategy 不得输出允许买入。
5. risk_control 的禁止动作必须进入 chief_decision。
6. chief_decision 输出后，才形成最终交易计划。
7. 候选发现由每日选股 screening 链产出（`src/custos/pipeline/screening/`：公式初筛 → 充实/模式识别 → 板块共振打分 → 备选表格，18:00 独立链 `run_1800.py` 运行，输出 `data/stock_pool/YYYY-MM-DD_stock_pool.json`）；`theme_tracker` 的 sector_state 是其板块输入；`tdx-wxd-a`、`tdx-wxd-bk` 暂不接入。
8. 技能风险只允许追加，不能删除现有 risk flags。
9. B 池交易计划最高只能输出“仅观察”；C/D 池不调用交易计划技能。
10. SkillEvidence 为 `partial/stale/failed` 时，不得据此上调仓位或放宽交易权限。

## 文件命名规则

建议统一：

- `data/market/YYYY-MM-DD_market_timing_input.json`
- `data/sectors/YYYY-MM-DD_sector_state.json`
- `data/stock_pool/YYYY-MM-DD_stock_pool.json`
- `data/buy_strategy/YYYY-MM-DD_buy_plan.json`
- `data/holdings/YYYY-MM-DD_holding_technical_summary.json`
- `artifacts/reports/daily/YYYY-MM-DD/YYYY-MM-DD_chief_decision.md`
- `artifacts/reports/daily/YYYY-MM-DD/YYYY-MM-DD_1445_review.md`
- 三份日报告文件名带时点标记（v0.141 起）：盘前 `YYYY-MM-DD_0905_daily_report.md`、
  盘后 `YYYY-MM-DD_1700_final_review.md/.json`、选股 `YYYY-MM-DD_1800_candidate_table.md`
- v0.162 起 `YYYY-MM-DD_portfolio_review.md` 与 `YYYY-MM-DD_theme_tracker.md` 停产
  （结构化产物 `data/holdings/YYYY-MM-DD_holding_review.json` 与
  `data/sectors/YYYY-MM-DD_sector_technical_summary.json` 路径不变、照常产出）；
  `chief_decision.md` 是唯一人读决策报告，市场评分明细与板块强弱已并入其 §2/§3。

## 质量检查

每日输出前检查：

- 日期是否一致
- 持仓是否最新
- 已清仓股票是否从当前持仓移除
- 是否存在无止损买入计划
- 是否存在 risk_control 否决但 chief_decision 仍允许的冲突
- 是否存在 market_timing 防守但 buy_strategy 正常买入的冲突
