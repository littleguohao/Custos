# Phase 1 工作流：每日交易计划系统

## Phase 1 目标

先不追求全自动交易，而是建立稳定、可复盘、可迭代的每日决策流程。

## 当前启用角色

### 核心稳定运行角色

1. 市场择时 Agent：market_timing
2. 主线题材 Agent：theme_tracker
3. 每日持仓研判 Agent：portfolio_review
4. 卖出风控 Agent：risk_control
5. 总控决策 Agent：chief_decision

### 已扩展接入角色

6. 产业研究 Agent：industry_research
7. 选股池 Agent：stock_pool
8. 买入策略 Agent：buy_strategy
9. 交易复盘与进化 Agent：strategy_evolution 框架已建立，先以日/周/月复盘模板运行

当前阶段定义为 Phase 1.5：框架完整成型，继续补数据源、自动化和评分细节。

## 每日流程

### 盘前 / 开盘前

1. 市场择时：判断今日市场环境、仓位上限、是否允许开新仓
2. 产业研究：更新长期产业方向和催化验证点
3. 主线题材：识别主线、退潮方向、强弱排序
4. 持仓研判：逐只检查持仓状态
5. 选股池：生成 A/B/C/D 候选池
6. 买入策略：对 A池和部分 B池生成条件化买入/观察计划
7. 卖出风控：检查止损、止盈、冷却、仓位风险，并审核买入计划
8. 总控决策：输出今日交易计划

### 盘中

- 只执行计划内动作
- 新情况必须记录触发原因
- 禁止无计划追涨

### 收盘后

- 记录实际执行
- 对比计划与结果
- 更新次日观察点

## 每日输出模板

- 今日市场状态
- 建议总仓位
- 是否允许开新仓
- 主线方向
- 禁止方向
- 当前持仓动作
- 风控触发项
- 今日候选观察
- 明日验证点

## Phase 1 / 1.5 成功标准

连续运行 10 个交易日后，应能回答：

1. 每天是否有清晰交易计划？
2. 是否减少了计划外交易？
3. 是否减少了亏损单拖延？
4. 是否识别出短线无效环境？
5. 是否形成可复盘记录？
6. stock_pool 候选分层是否稳定？
7. buy_strategy 是否都能给出明确止损和无效条件？
8. risk_control 是否有效否决高风险计划？

## 相关治理文档

- `00_governance/MASTER_WORKFLOW.md`
- `00_governance/AGENT_ROLE_MATRIX.md`
- `00_governance/DAILY_RUNBOOK.md`
- `00_governance/DATA_FLOW_CONTRACT.md`
- `00_governance/DECISION_PRIORITY_RULES.md`
- `00_governance/FRAMEWORK_COMPLETION_CHECKLIST.md`
