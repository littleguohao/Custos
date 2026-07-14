# Agent 角色清单 - Phase 1

## 必须创建/启用的 5 个角色

1. market_timing：市场择时
2. theme_tracker：主线题材
3. portfolio_review：每日持仓研判
4. risk_control：卖出风控
5. chief_decision：总控决策

## 扩展启用的角色

- stock_pool：已独立创建，用于结合主线、产业、板块、技术和风险过滤生成 A/B/C/D 候选池。
- buy_strategy：已独立创建，用于基于 stock_pool 输出生成条件化买入计划；但必须受 market_timing、theme_tracker、risk_control、chief_decision 约束。

## 暂不独立创建的角色

- strategy_evolution：先用周/月复盘模板覆盖

## 原则

Phase 1 不追求 agent 数量，而追求流程稳定。
先让 5 个角色每天稳定产出，再扩展到完整 8 角色 Team。
