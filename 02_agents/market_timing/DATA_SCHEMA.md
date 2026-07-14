# market_timing 数据输入规范 v1

## 目标

把市场择时从主观描述变成结构化输入，供 market_timing Agent 每日评分。

## 输入分组

### 1. 宏观政策环境 manual

暂时人工判断，后续可接宏观数据与政策文本。

字段：

- monetary_policy：宽松 / 中性 / 收紧
- fiscal_policy：积极 / 中性 / 收缩
- credit_environment：扩张 / 稳定 / 收缩
- regulation_environment：呵护市场 / 中性 / 压制风险偏好
- policy_summary：文字说明

### 2. 0AMV 活跃市值 manual

暂时人工输入。

字段：

- amv_change_pct：当日涨跌幅
- amv_zone：做多 / 中性 / 空头

规则：

- > 4%：做多
- < -2.3%：空头
- 其他：中性

### 3. 外围市场 external/manual

字段：

- nasdaq_change_pct
- sp500_change_pct
- sox_change_pct
- nikkei_change_pct
- kospi_change_pct
- hstech_change_pct
- overseas_summary

### 4. A股指数趋势 auto

至少包括：

- 上证指数
- 创业板指
- 科创50/科创板指数
- 北证50

字段：

- latest_close
- change_5d
- change_20d
- change_60d
- above_ma25
- above_ma60
- above_ma144
- above_ma240

### 5. 市场宽度 auto/manual

字段：

- up_count
- down_count
- up_down_ratio

### 6. 情绪强度 auto/manual

字段：

- limit_up_count
- limit_down_count
- once_limit_up_count
- blowup_rate
- market_height
- above_2_board_count

### 7. 成交量能 auto/manual

字段：

- total_turnover
- turnover_change_pct
- volume_summary

### 8. 主线清晰度 from theme_tracker

字段：

- main_themes
- theme_clarity：强 / 中 / 弱
- theme_summary

## 输出路径

每日输入文件：

`strategy_team/01_data/market/YYYY-MM-DD_market_timing_input.json`

## 注意

Phase 1 允许 manual 字段存在，优先把流程跑通；后续逐步自动化。

## v2 自动采集补充

collector：`strategy_team/07_tools/market_timing/market_timing_collector.py`

新增字段：

- collector_version
- data_quality
- raw_tq
- a_share_indices.*.intraday
- market_breadth.source / quality
- sentiment.source / quality
- turnover.source / quality

质量标记：

- auto：字段口径稳定，可直接用于评分
- candidate：字段来自候选快照，需确认口径，暂不强依赖
- raw_only：仅保留原始数据，不自动用于评分
- missing：未获取到

当前 TQ SC 市场交易字段返回不完整，仅稳定返回 `SC36`，因此涨跌停/连板高度暂保留 raw_only，不直接用于自动评分。涨跌家数和成交额使用 TQ 快照候选字段，标记为 candidate。

## 外围市场数据来源补充

### 推荐原则

外围市场数据分两类：

1. 结构化行情数据：用于自动评分
2. 财经媒体信息：用于解释波动原因和风险事件

### 可用来源

#### 结构化行情优先

适合采集：指数涨跌幅、期货涨跌幅、科技龙头涨跌幅、半导体指数涨跌幅。

可选来源：

- Yahoo Finance
- MarketWatch
- Investing.com
- Nasdaq
- TradingView
- HKEX/港交所
- Nikkei 指数页面
- KRX/韩国交易所

#### 财经媒体辅助

适合判断：为什么涨跌、是否有重大事件、市场叙事是否变化。

可选来源：

- Wall Street Journal / 华尔街日报
- Bloomberg
- Reuters
- CNBC
- Financial Times
- Nikkei Asia

### 华尔街日报使用边界

WSJ 可以作为外围市场解读来源，但存在两个限制：

1. 可能有付费墙，自动抓取不一定稳定。
2. 不应复制长篇付费内容，只提炼事实、标题级信息和市场影响。

因此：

- 自动评分不依赖 WSJ 文章正文。
- 若 WSJ 可访问，则用于补充“外围市场波动原因”。
- 若不可访问，则使用 Reuters/CNBC/MarketWatch/Yahoo Finance 等替代来源。

### 对 market_timing 的使用方式

外围市场模块应输出：

- 美股科技是否强/弱
- 费城半导体是否强/弱
- 日韩半导体链是否强/弱
- 港股科技是否强/弱
- 是否存在重大风险事件
- 对 A 股 AI、半导体、光模块、PCB、消费电子、机器人等方向的映射影响

### 重要约束

外围媒体解释只能作为辅助，不替代 A 股自身信号：

- 0AMV 活跃市值
- A 股涨跌家数
- 涨跌停情绪
- 板块强弱
- 主线持续性
