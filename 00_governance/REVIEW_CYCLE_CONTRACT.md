# 周度与月度复盘数据契约

本文件是 `REPORT_BLUEPRINT_V2.md` 的周期复盘执行补充。

## WeeklyReview

```json
{
  "period": "YYYY-Www",
  "trading_dates": [],
  "data_complete": true,
  "market_regime_path": [],
  "theme_lifecycle_path": [],
  "opening_positions": [],
  "closing_positions": [],
  "new_trades": [],
  "plan_execution": {
    "planned_trade_count": 0,
    "unplanned_trade_count": 0,
    "action_compliance_rate": null,
    "stop_compliance_rate": null
  },
  "performance": {
    "weekly_return": null,
    "realized_pnl": null,
    "unrealized_pnl": null,
    "max_drawdown": null,
    "fees": null
  },
  "pool_conversion": {},
  "effective_rules": [],
  "failed_rules": [],
  "next_week": {
    "risk_budget": null,
    "scenarios": [],
    "watchlist": []
  }
}
```

## MonthlyReview

```json
{
  "period": "YYYY-MM",
  "trading_dates": [],
  "data_complete": true,
  "benchmark": {},
  "performance": {
    "monthly_return": null,
    "max_drawdown": null,
    "return_drawdown_ratio": null,
    "win_rate": null,
    "payoff_ratio": null,
    "expectancy": null,
    "turnover": null,
    "fees": null
  },
  "pnl_attribution": {
    "stocks": [],
    "sectors": [],
    "realized": null,
    "unrealized": null
  },
  "risk_exposure": {
    "max_single_stock": null,
    "max_sector": null,
    "concentration": null,
    "correlation": null
  },
  "rule_versions": [],
  "rule_performance": [],
  "behavior_errors": [],
  "next_month": {
    "parameter_changes": [],
    "risk_budget": null,
    "watch_themes": []
  }
}
```

## 硬约束

- 数据不完整时 `data_complete=false`，并列出缺失项；不得编造指标。
- 周/月报告只使用对应周期截止后的数据，不得前视。
- 收益、回撤、胜率和盈亏比必须可追溯至主交易台账和每日收盘估值。
- 规则调整必须记录旧版本、新版本、原因、样本量和生效日期。
