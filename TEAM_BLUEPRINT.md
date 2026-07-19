# 投资策略系统蓝图

> ⚠️ 本文档已从原 8-Agent 架构演进为确定性脚本主链。保留风控硬规则和用户画像作为策略资产。

## 核心原则

1. 不追求单次判断正确，而追求系统长期正期望。
2. 所有建议必须可追踪、可复盘、可迭代。
3. 风控优先级高于收益预测。
4. 交易计划必须先定义无效条件，再定义盈利目标。
5. 系统输出的是决策辅助，不替代最终人工决策。

## 当前架构：确定性脚本主链

| 职能 | 脚本 | 说明 |
|---|---|---|
| 市场择时 | `market_timing/` 系列 | 0AMV、市场宽度、指数趋势 → 进攻/震荡/防守/冰点 |
| 主线/板块 | `market_timing/holding_sector_mapper.py` | 持仓板块映射、板块状态 |
| 持仓研判 | `market_timing/technical_monitor.py`、`b1_holding_state.py` | B1 持仓状态、四均线/箱体/BBI/N结构 |
| 卖出风控 | `generate_risk_and_sectors.py` | risk_decision 生成，拥有否决权 |
| 总控决策 | `market_timing/chief_decision_report.py` | ChiefDecision：三份报告的内部决策对象 |
| 复盘进化 | `close_review/` 系列 | execution_review → review_enrichment → final_close_review |
| 选股池 | **待重建** | 原 stock_pool/buy_strategy 已移除（theme_tracker 仍每日运行作为证据层），TQ 公式初筛 + LLM 因子评分重建中 |

不创建、不调用、不等待专业 Agent 或 Subagent。所有数据采集和指标计算由 Python 脚本完成。

## 卖出风控硬规则

以下规则由 `generate_risk_and_sectors.py` 和 `b1_holding_state.py` 执行，不可被总控决策覆盖：

1. **连亏冷却**：同一股票连续亏损 2 次，冷却 10 个交易日
2. **短线止损**：短线仓亏损 -5%~-7%，必须复盘或减仓
3. **强制风控**：单票亏损超过 -10%，必须进入强制风控评估
4. **胜率降仓**：当月短线胜率低于 35%，降低短线仓位
5. **P0 优先级**：硬止损/熔断 → 立即执行，不可覆盖
6. **空头区间减仓最高优先级**：0AMV 活跃市值空头区间，降低仓位是最高优先级，任何反弹都是卖出机会，禁止加仓补仓（由 `b1_holding_state.py` 信号 `bear_regime_reduce_top_priority` 与 `generate_risk_and_sectors.py` 的 `regime_directive` 执行）

## 用户画像与个性化约束

根据历史交易复盘：

1. 用户优势更偏中周期主线交易，而不是高频短线。
2. 2026 年 6 月亏损集中，短线试错密度过高。
3. 20 天以内交易贡献主要亏损。
4. 九丰能源等案例显示需要连续亏损冷却机制。
5. 卖出后 20 日平均收益为负，主要问题不是普遍卖飞，而是亏损单处理偏慢。
6. 风控权重必须高于买入。

## 决策优先级

1. 个股服从板块，板块服从大盘
2. 风控优先于买入
3. 候选池待重建，买入计划由 chief_decision 统一裁决
4. risk_control 拥有否决权
5. chief_decision 是最终交易计划输出层
6. 所有计划必须可复盘

详细工作流见：`00_governance/MASTER_WORKFLOW.md`
