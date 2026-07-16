# 数据源覆盖矩阵

> 2026-07-16 创建。覆盖策略团队九大类数据需求的来源、采集方式、质量和时效。

## 一、市场宽度与情绪

| 数据项 | 来源 | 采集方式 | 时效 | 质量 | 备注 |
|---|---|---|---|---|---|
| 涨跌家数 | 880005 | mootdx Reader 本地 / mootdx online | 盘后EOD | confirmed | 含 up_count/down_count |
| 停板家数 | 880006 | mootdx Reader 本地 / mootdx online | 盘后EOD | confirmed | 含涨停/跌停/炸板 |
| 平均股价 | 880001 | mootdx Reader 本地 / mootdx online | 盘后EOD | confirmed | 判断小票整体强弱 |
| 涨跌停对比 | tdx_screener | LLM 调用 "涨停"/"跌停" | 盘中实时 | auto | 用于情绪极值判断 |
| 连板梯队 | tdx_screener | LLM 调用 "2连板"/"3连板" | 盘后 | auto | 高度坍塌=亏钱效应 |
| 炸板率 | tdx_screener | LLM 调用 "炸板" | 盘后 | auto | 需与涨停数交叉计算 |

## 二、指数与行情

| 数据项 | 来源 | 采集方式 | 时效 | 质量 | 备注 |
|---|---|---|---|---|---|
| 上证指数 | 000001 | mootdx Reader / tdx_quotes | EOD+实时 | confirmed | |
| 深证成指 | 399001 | mootdx Reader / tdx_quotes | EOD+实时 | confirmed | |
| 创业板指 | 399006 | mootdx Reader / tdx_quotes | EOD+实时 | confirmed | |
| 个股行情 | 持仓代码 | mootdx Reader / collect_holding_quotes.py | EOD+实时 | confirmed | BJ股用mootdx online |
| 指数K线 | 多代码 | mootdx Reader (0.006s/股) | EOD | confirmed | 本地vipdoc |
| 前复权数据 | 个股 | mootdx get_adjust_year | EOD | confirmed | 同花顺官方复权因子 |

## 三、外围市场

| 数据项 | 来源 | 采集方式 | 时效 | 质量 | 备注 |
|---|---|---|---|---|---|
| 道琼斯 | ^DJI | Yahoo Finance API | 收盘 | auto | overseas_market_collector.py |
| 纳斯达克 | ^IXIC | Yahoo Finance API | 收盘 | auto | |
| 标普500 | ^GSPC | Yahoo Finance API | 收盘 | auto | |
| 费城半导体 | ^SOX | Yahoo Finance API | 收盘 | auto | 半导体链风向标 |
| 日经225 | ^N225 | Yahoo Finance API | 收盘 | auto | |
| KOSPI | ^KS11 | Yahoo Finance API | 收盘 | auto | |
| 恒生科技 | ^HSTECH | Yahoo Finance API | 收盘 | auto | |
| A50期指 | CFF=A50 | Yahoo Finance / web_search | 收盘 | auto | 隔夜外围影响 |
| 离岸人民币 | USDCNH=X | Yahoo Finance | 实时 | auto | 资金流出/流入压力 |
| 英伟达/AMD/台积电 | NVDA/AMD/TSM | Yahoo Finance API | 收盘 | auto | AI链核心标的 |

## 四、资金流向

| 数据项 | 来源 | 采集方式 | 时效 | 质量 | 备注 |
|---|---|---|---|---|---|
| 北向资金 | 880863 | mootdx Reader 本地 | EOD | confirmed | 5日趋势判断 |
| 个股主力净流入 | tdx_api_data | zjlx fixedTag | EOD | auto | |
| 融资融券指数 | 880390 | mootdx Reader 本地 | EOD | confirmed | 杠杆情绪 |
| 两融余额变动 | tdx_api_data | rzrq fixedTag | EOD | auto | |
| 大单成交统计 | tdx_api_data | tdx_screener | 盘后 | auto | |

## 五、龙虎榜与异动

| 数据项 | 来源 | 采集方式 | 时效 | 质量 | 备注 |
|---|---|---|---|---|---|
| 龙虎榜明细 | tdx_api_data | jglhb fixedTag | 盘后 | confirmed | |
| 龙虎榜可用日期 | tdx_api_data | comreq jglhb | 盘后 | confirmed | |
| 大宗交易 | tdx_api_data | dzjy fixedTag | 盘后 | auto | |

## 六、公告与新闻

| 数据项 | 来源 | 采集方式 | 时效 | 质量 | 备注 |
|---|---|---|---|---|---|
| 持仓公告 | wenda_notice_query | LLM 工具调用 | 盘前 | confirmed | 5只持仓逐个查 |
| 宏观要闻 | RSS 26源 | rss_collector.py + rss_filter.py | 盘前 | auto | 30候选/15摘要 |
| 研报评级 | wenda_report_query | LLM 工具调用 | 按需 | auto | |
| 财经要闻 | .777 同类 | RSS 覆盖 | 盘前+盘后 | auto | |

## 七、财务数据

| 数据项 | 来源 | 采集方式 | 时效 | 质量 | 备注 |
|---|---|---|---|---|---|
| 全市场财务(585字段) | mootdx Affair | Affair.fetch+parse | 季报 | confirmed | 5525家公司一次性下载 |
| 利润表 | tdx_api_data | lyb fixedTag | 季报 | confirmed | 交叉验证 |
| 资产负债表 | tdx_api_data | zcfzb | 季报 | confirmed | |
| 现金流量表 | tdx_api_data | xjllb fixedTag | 季报 | confirmed | |
| 主营构成 | tdx_api_data | jyfx fixedTag | 季报 | auto | |
| 业绩预警 | tdx_api_data | yjyj | 按需 | auto | |

## 八、板块与行业

| 数据项 | 来源 | 采集方式 | 时效 | 质量 | 备注 |
|---|---|---|---|---|---|
| 行业板块 | 991010 | tdx_quotes / mootdx | EOD | confirmed | |
| 概念题材 | 991020 | tdx_quotes / mootdx | EOD | confirmed | |
| 板块涨幅排序 | tdx_screener | LLM 调用 | 盘后 | auto | |
| 行业产业链 | tdx_api_data | cfg_tk_gethy | 按需 | confirmed | |
| 行业重要事件 | tdx_api_data | hyzysj | 按需 | auto | |
| 板块操盘必读 | tdx_api_data | skef10_bk_cpbd | 按需 | confirmed | |

## 九、交易日历与基础数据

| 数据项 | 来源 | 采集方式 | 时效 | 质量 | 备注 |
|---|---|---|---|---|---|
| 交易日历 | SSE官方日程 | trading_calendar.py | 年度 | confirmed | 242天/2026年 |
| 交易日判断 | trading_calendar.py | --check-date | 实时 | confirmed | 手动>SSE>缓存>周末>unknown |
| 板块成分股 | mootdx Reader.block | 本地 block.dat | 静态 | auto | 需盘后生成 |
| 股票列表 | mootdx stocks | 在线API | 实时 | auto | |
| 休市判断 | mootdx holiday | 在线API | 实时 | auto | 辅助验证 |

## 质量等级说明

| 等级 | 含义 |
|---|---|
| confirmed | 数据已验证，可作为交易决策依据 |
| auto | 自动采集但未经人工确认，标记后可用 |
| missing | 数据不可用，报告标记为 unavailable |
| degraded | 部分可用，需要人工补充确认 |
