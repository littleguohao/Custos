# Agent 角色清单 - Phase 1

## 逻辑角色（不要求创建独立Agent）

1. market_timing：市场择时
2. theme_tracker：主线题材
3. portfolio_review：每日持仓研判
4. risk_control：卖出风控
5. chief_decision：总控决策

## 扩展逻辑角色

- stock_pool：已独立创建，用于结合主线、产业、板块、技术和风险过滤生成 A/B/C/D 候选池。
- buy_strategy：已独立创建，用于基于 stock_pool 输出生成条件化买入计划；但必须受 market_timing、theme_tracker、risk_control、chief_decision 约束。

## 异步研究与Subagent边界

- 专业Agent和Subagent仅用于非分钟级的独立研究任务。
- 09:05、14:45、15:15、20:30任务不得创建、调用或等待子Agent。
- 异步结果有则追加，缺失则标记`unavailable`，不阻断正式报告。

## 原则

生产系统不追求Agent数量。能写成确定性脚本的职责不启动Agent；能由一个轻量Agent完成的任务不创建Subagent。
