# Agent 角色矩阵

> ⚠️ **设计参考 / 待重建**：角色职责描述仍有效，但 stock_pool_builder.py 已移除。选股流程重建时以此为蓝图。
>
> 2026-07-16更新：所有角色由确定性脚本执行，不创建、不调用、不等待独立 Agent。

## 总览

| 角色 | 执行模块 | 核心问题 | 主要输出 | 权限 |
|---|---|---|---|---|
| market_timing | `market_timing_collector.py` + `market_timing_scorer.py` | 今天能不能做？做多少？ | 市场状态、仓位、新开仓权限 | 强否决 |
| theme_tracker | `theme_tracker_report.py` | 哪些板块支持交易？ | 板块状态、强弱、关键节点 | 半否决 |
| stock_pool | `stock_pool_builder.py` | 哪些股票值得进候选？ | A/B/C/D 候选池 | 半否决 |
| buy_strategy | 由 B1 策略和买入规则覆盖 | 候选股怎么买？ | 买入价、首仓、加仓、止损 | 无 |
| portfolio_review | `portfolio_review_report.py` | 当前持仓怎么处理？ | 持有/观察/减仓/止损建议 | 半否决 |
| risk_control | `b1_holding_state.py` + `runtime_guards.py` | 哪些动作禁止？ | 禁止动作、风控清单 | 强否决 |
| chief_decision | `chief_decision_report.py` | 今天最终做什么？ | 每日交易计划 | 强否决 |

## 权限说明

### 强否决权

1. market_timing：市场防守/冰点、0AMV 空头时限制新开仓。
2. risk_control：触发止损、冷却、禁止加仓时否决交易。
3. chief_decision：根据总仓位、优先级和冲突处理做最终否决。

### 半否决权

1. theme_tracker：板块退潮时限制相关个股。
2. stock_pool：D池股票禁止买入。
3. portfolio_review：弱势持仓触发风控审核。

## 冲突处理规则

| 冲突 | 处理 |
|---|---|
| 买入信号强，但 risk_control 否决 | 以 risk_control 为准 |
| 个股技术好，但板块退潮 | 降级为观察或禁止 |
| 板块强，但 market_timing 防守 | 降低仓位或只观察 |
| 持仓浮亏扩大，但逻辑仍在 | 先风控，再复核逻辑 |

## 用户个性化权重

1. 风控纪律
2. 市场环境
3. 板块主线
4. 个股质量
5. 买点性价比
6. 短线弹性

短线弹性不能压过风控纪律。
