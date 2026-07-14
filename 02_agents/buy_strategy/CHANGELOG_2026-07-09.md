# buy_strategy CHANGELOG - 2026-07-09

## 新增

- 创建 buy_strategy Agent。
- 明确其职责：买入条件、买入价区间、首仓比例、加仓条件、无效条件、止损位。
- 与 market_timing、theme_tracker、portfolio_review、risk_control、chief_decision 建立上下游约束。
- 增加四类买入模式：趋势回踩低吸、箱体下沿低吸、放量突破买入、事件催化试探。
- 明确弱市和 0AMV 空头区间下的限制：原则不新开，不因 J 值低位直接买入。
- 明确加仓比首仓更严格，禁止亏损摊低式加仓。

## 当前定位

buy_strategy 已从 Phase 1 暂缓项升级为扩展启用 Agent。

但当前仍受以下角色约束：

1. market_timing 决定是否允许交易。
2. theme_tracker 决定板块是否支持。
3. risk_control 拥有否决权。
4. chief_decision 负责最终执行确认。
