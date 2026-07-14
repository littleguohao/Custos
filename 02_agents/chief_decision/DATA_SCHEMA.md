# chief_decision 数据输入规范

## 强制输入文件

- `03_daily_plans/YYYY-MM-DD_market_timing_score.md`
- `01_data/quality/YYYY-MM-DD_runtime_gate.json`
- `01_data/sectors/YYYY-MM-DD_sector_state.json`
- `01_data/holdings/YYYY-MM-DD_holding_review.json`
- `01_data/buy_strategy/YYYY-MM-DD_buy_plan_normalized.json`
- `01_data/risk/YYYY-MM-DD_risk_decision.json`

缺少 `RiskDecision` 时总控构建必须失败，不能生成一个看似完整的决策报告。

## 输出

先生成结构化结果，再渲染 Markdown：

- `01_data/decisions/YYYY-MM-DD_chief_decision.json`
- `03_daily_plans/YYYY-MM-DD_chief_decision.md`

## 冲突硬约束

- RiskDecision 否决的代码，ChiefDecision 不得允许买入或加仓。
- 市场质量门 blocked 时，新开仓权限必须为禁止。
- B池最高仅观察；C/D池不得进入可执行买入清单。
- 持仓时效 uncertain/stale 时，不得输出精确交易数量。

## 第一阶段限制

- 暂不自动下单
- 暂不自动修改真实持仓
- 所有动作均为策略辅助建议
- 涉及买卖仍由用户人工确认
