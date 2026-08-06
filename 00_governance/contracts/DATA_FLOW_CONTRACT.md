# 数据流契约

> ⚠️ **设计参考 / 部分重建**：本文档描述 Agent+skill 时代的数据流设计（skill_adapters、StockCandidate、BuyPlan 等），相关代码已移除。StockPool（StockCandidate）已由每日选股 screening 链重建（2026-07-21，见 `00_governance/contracts/SCREENING_WORKFLOW.md`）；其余实体重建时以此为设计蓝图对齐。

日期：2026-07-09

## 目标

统一各 Agent 的输入输出，避免后续扩展时数据混乱。

## ⚠️ 实现状态（2026-08-06 逐实体核查）

**契约的第一件事是说清哪些真有产出。** 8 个实体里 **2 个没有任何生产者**：

| 实体 | 生产者 | 状态 |
|---|---|---|
| MarketState | `generate_risk_and_sectors.py`、`market_timing/` | ✅ |
| SectorState | `generate_risk_and_sectors.py` | ✅ |
| StockCandidate | `screening/` 链（enrich → score → table）| ✅ |
| HoldingReview | `market_timing/b1_holding_state.py` | ✅ |
| RiskDecision | `generate_risk_and_sectors.py` | ⚠️ 除 `cooldown_list`（见下）|
| ChiefDecision | `chief_decision_report.py`、`daily_report.py` | ✅ |
| **SkillEvidence** | — | 🔴 **无生产者** |
| **BuyPlan** | — | 🔴 **无生产者** |

**🔴 SkillEvidence**：Skill 架构时代的遗留。`build_skill_contracts.py` + `skill_adapters.py`
已在纯脚本化时被 `generate_risk_and_sectors.py` 取代（见该文件 docstring），
`skill_id` / `entity_id` / `raw_ref` / `source_tools` 全仓零命中。

**🔴 BuyPlan**：`buy_strategy` 相关代码已移除。代码里只剩 `next_step = "generate_buy_plan"`
这个**字符串标签**（提示人去做买入计划），**不是一个被产出的 BuyPlan 对象** ——
`buy_price_range` / `entry_conditions` / `first_position_pct` / `add_conditions` /
`invalid_conditions` / `max_loss_pct` / `stock_pool_bucket` / `buy_mode` 全仓零命中。
买入计划的**必备项清单**现在在 `../strategy/b1/03_execution_discipline.md`（人执行）。

⇒ **读这两节时不要假设有对应产物。** 保留它们是因为重建这两层时是有用的目标形状，
但**当前不是契约，是设计草案**。

## 核心实体

### SkillEvidence

> 🔴 **无生产者（2026-08-06 核查）。** Skill 架构遗留，已被 `generate_risk_and_sectors.py` 取代。下面是**设计草案而非现行契约**。

所有本地 TDX 技能必须先转换为证据对象，不能把自由文本结论直接送入总控：

```json
{
  "skill_id": "tdx-hot-topic",
  "entity_type": "stock|sector|market",
  "entity_id": "600000.SH",
  "as_of": "YYYY-MM-DDTHH:mm:ss+08:00",
  "trade_date": "YYYY-MM-DD",
  "report_date": null,
  "horizon": "intraday|short|medium|long",
  "source_tools": ["tdx_api_data"],
  "status": "ok|partial|stale|failed",
  "facts": {},
  "signals": [],
  "risk_flags": [],
  "raw_ref": ""
}
```

适配器实现：`07_tools/skill_adapters.py`。

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
  "next_step": "generate_buy_plan|observe_price|long_term_track|avoid"
}
```

### BuyPlan

> 🔴 **无生产者（2026-08-06 核查）。** `buy_strategy` 代码已移除；代码里只有 `next_step="generate_buy_plan"` 这个标签。
> 买入计划的**必备项清单**（含「缺任一项不得放行」）在 [`../strategy/b1/03_execution_discipline.md`](../strategy/b1/03_execution_discipline.md)。
> 下面是**设计草案而非现行契约**。

```json
{
  "code": "600000",
  "name": "示例股票",
  "stock_pool_bucket": "A|B|C|D",
  "conclusion": "允许|小仓试探|仅观察|禁止",
  "buy_mode": "趋势回踩|箱体低吸|放量突破|事件催化|无",
  "buy_price_range": {
    "lower": null,
    "upper": null,
    "basis": ""
  },
  "first_position_pct": {
    "lower": 0.0,
    "upper": 0.0
  },
  "entry_conditions": [],
  "add_conditions": [],
  "invalid_conditions": [],
  "stop_loss": {
    "price": null,
    "basis": "",
    "max_loss_pct": null
  },
  "risk_level": "低|中|高"
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
  // 🔴 "cooldown_list": [] —— **契约声明过但从未实现**（全仓 cooldown/冷却/blacklist 零命中）。
  // 别把它当成「触发止损的票会自动进冷却、不会被重复买入」——那个机制不存在。
  // 现有的近似能力只有 forbidden_actions（已实现）。要真做冷却须先立项，见 TODO。
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
7. 候选发现由每日选股 screening 链产出（`07_tools/screening/`：公式初筛 → 充实/模式识别 → 板块共振打分 → 备选表格，18:00 独立链 `run_1800.py` 运行，输出 `01_data/stock_pool/YYYY-MM-DD_stock_pool.json`）；`theme_tracker` 的 sector_state 是其板块输入；`tdx-wxd-a`、`tdx-wxd-bk` 暂不接入。
8. 技能风险只允许追加，不能删除现有 risk flags。
9. B 池交易计划最高只能输出“仅观察”；C/D 池不调用交易计划技能。
10. SkillEvidence 为 `partial/stale/failed` 时，不得据此上调仓位或放宽交易权限。

## 文件命名规则

建议统一：

- `01_data/market/YYYY-MM-DD_market_timing_input.json`
- `01_data/sectors/YYYY-MM-DD_sector_state.json`
- `01_data/stock_pool/YYYY-MM-DD_stock_pool.json`
- `01_data/buy_strategy/YYYY-MM-DD_buy_plan.json`
- `01_data/holdings/YYYY-MM-DD_holding_technical_summary.json`
- `03_daily_plans/YYYY-MM-DD_chief_decision.md`
- `04_reviews/daily/YYYY-MM-DD_review.md`

## 质量检查

每日输出前检查：

- 日期是否一致
- 持仓是否最新
- 已清仓股票是否从当前持仓移除
- 是否存在无止损买入计划
- 是否存在 risk_control 否决但 chief_decision 仍允许的冲突
- 是否存在 market_timing 防守但 buy_strategy 正常买入的冲突
