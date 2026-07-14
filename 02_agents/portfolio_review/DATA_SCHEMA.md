# 持仓研判数据输入规范

## 输入文件

- `01_data/holdings/YYYY-MM-DD_holding_sector_mapping_enriched.json`
- `01_data/market/YYYY-MM-DD_market_timing_input.json`
- `03_daily_plans/YYYY-MM-DD_market_timing_score.md`

## 后续需要补充

- 个股日/周/月 KDJ、MACD
- 个股趋势：上涨/下跌/横盘
- 箱体上沿/下沿/中轴
- 个股相对板块强弱
- 个股关键价位
- 风险事件

## 第一阶段可先人工/半自动输出

先基于：

- 持仓盈亏
- 仓位占比
- 持仓天数
- 所属板块
- market_timing 状态
- 板块主线风险

生成初步动作建议。
