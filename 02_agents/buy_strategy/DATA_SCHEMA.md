# buy_strategy 数据结构

## BuyPlan

```json
{
  "date": "YYYY-MM-DD",
  "market_permission": {
    "market_state": "进攻|震荡偏强|震荡偏弱|防守|冰点",
    "zero_amv_state": "做多区间|中性|空头区间",
    "new_position_permission": "允许|小仓试探|原则不允许|禁止",
    "risk_level": "普通|提高|强风控",
    "max_total_position_pct": 0.4
  },
  "stock_pool_input": {
    "a_pool": [],
    "b_pool": [],
    "c_pool": [],
    "d_pool": []
  },
  "plans": [
    {
      "code": "600000",
      "name": "示例股票",
      "sector": "示例板块",
      "sector_state": "主升|修复|分歧|震荡|退潮",
      "stock_role": "龙头|核心|中军|跟风|后排|未定",
      "relative_strength": "强于板块|同步板块|弱于板块|未定",
      "stock_pool_bucket": "A|B|C|D",
      "allowed": false,
      "conclusion": "允许|小仓试探|仅观察|禁止",
      "buy_mode": "趋势回踩|箱体低吸|放量突破|事件催化|无",
      "buy_price_range": {
        "lower": null,
        "upper": null,
        "basis": "箱体下沿/MA25/突破位/事件催化"
      },
      "first_position_pct": {
        "lower": 0.0,
        "upper": 0.0,
        "unit": "total_assets"
      },
      "entry_conditions": [],
      "add_conditions": [],
      "invalid_conditions": [],
      "stop_loss": {
        "price": null,
        "basis": "箱体下沿/MA25/MA60/MA144/MA240/前低/固定亏损阈值",
        "max_loss_pct": null
      },
      "time_stop": "3-5日未修复复盘，5-10日弱于板块降仓或退出",
      "risk_level": "低|中|高",
      "notes": ""
    }
  ]
}
```

## 字段约束

- `allowed=true` 只能在市场、板块、个股、风控全部通过时出现。
- `conclusion=允许` 必须有明确买入价区间和止损位。
- `buy_price_range` 不明确时，结论只能是 `仅观察` 或 `禁止`。
- `stop_loss` 不明确时，禁止输出可买。
- `zero_amv_state=空头区间` 时，默认 `allowed=false`，除非 chief_decision 特批极小仓位验证。
- `sector_state=退潮` 时，默认 `allowed=false`。
- `relative_strength=弱于板块` 时，不允许加仓。
