# 个股三状态走势识别

将日线走势分为：

- `震荡向上`：右侧趋势策略
- `横盘震荡`：区间或突破确认策略
- `震荡向下`：左侧/弱势状态，默认回避或等待修复

## 数据源

优先使用通达信本地 TQ HTTP 服务：

```powershell
uv run python strategy_team\07_tools\trend_state\three_state_trend.py --code 600150 --source tq-http --count 520
```

如果 TQ HTTP 服务暂未启用，可以读取同一通达信客户端的本地 `vipdoc` 日线：

```powershell
uv run python strategy_team\07_tools\trend_state\three_state_trend.py --code 600150 --source vipdoc --count 520
```

`--source auto` 会先尝试 TQ HTTP，失败后自动读取 `vipdoc`：

```powershell
uv run python strategy_team\07_tools\trend_state\three_state_trend.py --code 600150 --source auto --count 520
```

## 初始分类规则

向上和向下各有 6 个透明评分项：

1. 收盘价、MA25、MA60 排列；同步输出MA144、MA240作为中长期结构过滤
2. MA25 的 5 日斜率
3. MA60 的 10 日斜率
4. 最近两个 20 日窗口的高低点结构
5. 20 日 Kaufman Efficiency Ratio
6. ADX 体系中的 `+DI/-DI` 方向

默认方向得分至少 4 分、反向得分不超过 1 分，才形成原始方向状态；否则归入横盘。新状态连续出现 3 个交易日后才成为确认状态。

核心均线统一为MA25/MA60/MA144/MA240。MA144和MA240暂作为结构字段输出，不额外增加原始六项评分权重，待跨股票回测后再决定是否纳入方向分。

## 输出

默认输出至 `strategy_team/01_data/trend_state/`：

- `*_trend_summary.json`：最新状态、评分和指标
- `*_trend_series.csv`：每日原始状态与确认状态
- `*_trend_backtest.json`：各状态后续 1/3/5/10/20 日收益统计

回测结果用于检验分类是否有区分度，不应直接解释为买卖收益。需要在多股票、跨行业和不同市场阶段上验证后，再调整阈值或接入交易策略。
