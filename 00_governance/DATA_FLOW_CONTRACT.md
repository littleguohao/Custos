# 数据流契约

> ⚠️ **设计参考 / 待重建**：本文档描述 Agent+skill 时代的数据流设计（skill_adapters、StockCandidate、BuyPlan 等），相关代码已移除。选股流程重建时以此为设计蓝图对齐。

日期：2026-07-09

## 目标

统一各 Agent 的输入输出，避免后续扩展时数据混乱。

## 核心实体

### SkillEvidence

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
  "zero_amv_state": "做多区间|中性|空头区间",
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
  "technical_sources": [
    {
      "source_id": "B1_low_j_factor_similarity",
      "signal": "KDJ低位+因子相似度",
      "technical_score": 0,
      "raw_rank": 0
    }
  ],
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
  "cooldown_list": [],
  "stock_risks": [
    {
      "code": "600000",
      "name": "示例股票",
      "risk_type": "破位|亏损扩大|板块退潮|冷却|无止损计划",
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
7. 当前候选发现继续使用 `theme_tracker + stock_pool + formula_screen`；`tdx-wxd-a`、`tdx-wxd-bk` 暂不接入。
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
