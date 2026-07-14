# 工作框架完善清单

日期：2026-07-09

## 已完成骨架

- [x] Team 蓝图
- [x] market_timing 市场择时
- [x] industry_research 产业研究
- [x] theme_tracker 主线/板块跟踪
- [x] stock_pool 选股池
- [x] buy_strategy 买入策略
- [x] portfolio_review 持仓研判
- [x] risk_control 风控纪律
- [x] chief_decision 总控决策
- [x] strategy_evolution 复盘进化框架
- [x] 总工作流
- [x] 角色矩阵
- [x] 每日运行手册
- [x] 数据流契约
- [x] 决策优先级规则

## 仍需后续补细节

### 数据源

- [ ] 0AMV 自动采集稳定化
- [ ] 涨跌停/炸板/连板数据自动 overlay
- [ ] 板块代码映射完善
- [ ] 持仓/交易记录自动同步
- [ ] stock_pool 候选来源自动化

### 策略规则

- [ ] stock_pool 评分参数细化
- [ ] buy_strategy 各模式价格计算公式
- [ ] risk_control 冷却名单自动维护
- [ ] chief_decision 冲突评分机制
- [ ] strategy_evolution 周/月复盘指标

### 输出

- [ ] 每日微信简版摘要
- [ ] 完整 Markdown 交易计划
- [ ] JSON 结构化结果
- [ ] 每周复盘报告
- [ ] 月度策略版本更新

## 当前阶段定义

当前处于：Phase 1.5

含义：

- 核心 5 个 Agent 已有基础产出。
- stock_pool 和 buy_strategy 已接入框架。
- 自动化数据仍不完全，需要继续补稳定性和数据源。
- 暂不追求自动交易，只追求稳定生成决策辅助。

## 下一步建议

1. 先完善 daily_pipeline，让 market_timing、portfolio_review、risk_control、chief_decision 稳定每日运行。
2. 再接入 stock_pool 的候选来源。
3. 最后让 buy_strategy 自动读取 A/B 池生成买入计划。
4. 每日输出统一汇总到 chief_decision。
