# stock_pool

选股池 Agent。

职责：

- 从主线、产业、板块、技术、风险过滤中形成候选股票池
- 支持两条选股路径：
  - 自上而下：强主线/强板块 → 核心股
  - 自下而上：选股公式先筛候选 → 板块过滤 → A/B/C/D 分层
- 将候选分为 A/B/C/D 四层
- 把 A池和部分 B池传递给 buy_strategy

当前重点规则：

> 技术面/公式负责“找苗子”，板块热度负责“验证市场是否共振”。

最优机会是：个股技术面转强 + 板块热度支持 + 大盘许可 + 风控可控。

公式候选不能绕过 `market_timing`、`theme_tracker`、`risk_control`。

详见：`../../00_governance/FORMULA_SCREEN_SECTOR_FILTER_WORKFLOW.md`

边界：

- stock_pool 不给买入价和仓位
- buy_strategy 不独立选股
- chief_decision 最终决定是否执行
