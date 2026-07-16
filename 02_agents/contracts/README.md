# Agent 输出契约存档

以下三份 Schema 是早期异步专业研究 Agent 的结构化输出契约，生产链已不再调用。

保留作为参考：如果未来需要升级 `premarket_intelligence.json`、`theme_tracker_report.py` 或 `portfolio_review_report.py` 的证据标准化格式，可据此设计。

| 文件 | 来源 Agent | 核心结构 |
|------|-----------|---------|
| `MARKET_INTELLIGENCE_OUTPUT.schema.json` | market-intelligence | news_evidence / notice_evidence / overseas_evidence，含 evidence_id、fact_summary、impact_direction、confidence、quality、confirmed |
| `THEME_SECTOR_OUTPUT.schema.json` | theme-sector | sector_states / theme_lifecycles / holding_sector_map，含板块阶段、相对强度、生命周期转换依据 |
| `PORTFOLIO_EXECUTION_OUTPUT.schema.json` | portfolio-execution | holding_reviews / entry_rules / exit_plans / position_advice，含 P0-P3 优先级、七类退出计划 |

**注意**：这些 Schema 不再被任何生产代码读取或写入。当前确定性脚本使用更简洁的内部格式。
