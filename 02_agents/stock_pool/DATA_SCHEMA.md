# stock_pool 数据结构

```json
{
  "date": "YYYY-MM-DD",
  "market_context": {
    "market_state": "进攻|震荡偏强|震荡偏弱|防守|冰点",
    "zero_amv_state": "做多区间|中性|空头区间",
    "new_position_permission": "允许|小仓试探|原则不允许|禁止"
  },
  "candidates": [
    {
      "code": "600000",
      "name": "示例股票",
      "sector": "示例板块",
      "source": ["theme_tracker", "tdx_screener", "industry_research"],
      "stock_role": "龙头|核心|中军|弹性|后排|未定",
      "relative_strength": "强于板块|同步板块|弱于板块|未定",
      "score": 0,
      "bucket": "A|B|C|D",
      "entry_reason": [],
      "risk_flags": [],
      "next_step": "generate_buy_plan|observe_price|long_term_track|avoid",
      "a_pool_trigger": []
    }
  ],
  "to_buy_strategy": {
    "generate_buy_plan": [],
    "observe_price_only": [],
    "forbidden": []
  }
}
```

## 分层约束

- A池：可交给 buy_strategy 生成完整买入计划。
- B池：只允许 buy_strategy 生成观察价位和触发条件。
- C池：不生成交易计划，只长期跟踪。
- D池：禁止生成买入计划。
