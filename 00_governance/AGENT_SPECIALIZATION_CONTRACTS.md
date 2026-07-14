# 专业 Agent 分工契约

## market_intelligence

只负责市场与消息证据：隔夜市场、公告、政策、宏观、0AMV、市场情绪和数据质量。输出 MarketState、NewsEvidence、NoticeEvidence、AMVState。不得直接输出买卖动作，不得覆盖 RiskDecision。

## theme_sector

只负责主线、板块生命周期、龙头/中军/后排、持仓所属板块和相对强弱。输出 SectorState、ThemeLifecycle、HoldingSectorMap。板块强不等于买入许可，不得绕过 ChiefDecision。

## portfolio_execution

只负责持仓实时/收盘复核、EntryRule、ExitPlan、PositionAdvice 和执行建议。卖出/风控优先；补仓必须满足市场、板块、个股、仓位和 RiskDecision 条件。不得写交易台账，不得直接下单。

## 共享硬约束

- 只读访问策略工作区；不得执行交易写操作。
- 不修改 OpenClaw 配置。
- 输出必须结构化、带 date/as_of/source/quality/confirmed。
- 发现缺失或冲突数据必须降级并标记，不得猜测。
- main 是唯一总控和最终动作来源。
