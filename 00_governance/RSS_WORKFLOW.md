# RSS信息工作流

## 定位

RSS是信息发现和时间线补充渠道，不是交易信号，也不能直接扩大交易权限。

```text
原始Feed → rss_collector → normalized全量证据 → rss_filter → 有界候选集 → market-intelligence → main → RiskDecision → ChiefDecision
```

## 数据保留

- 原始XML：`01_data/news/rss/raw/YYYY-MM-DD/`
- 全量标准化：`01_data/news/rss/normalized/YYYY-MM-DD_rss_evidence.json`
- 盘前候选：`01_data/news/rss/filtered/YYYY-MM-DD_premarket_rss_candidates.json`
- 采集日志：`06_logs/rss/YYYY-MM-DD_collection_log.json`
- 筛选日志：`06_logs/rss/YYYY-MM-DD_<session>_filter_log.json`

## 筛选规则

- 按 session 使用时间窗：盘前36小时、14:45近10小时、盘后18小时、周度192小时、月度840小时。
- 规范化URL去重，再按标题近似去重。
- 按来源等级、来源类别、持仓/候选池实体、主线主题和市场关键词评分。
- C级内容若不命中持仓、候选池、主线主题或市场关键词，不进入候选集。
- 发布时间缺失降低优先级；未来时间和窗口外数据排除。
- 工信部“意见征集”保留为候选事实，但必须标记未实施并要求核验正式文件/实施日期。
- 候选集默认上限：盘前80、14:45 50、盘后80、周度150、月度250。
- 筛选只改变分析优先级，不改变市场许可、仓位许可或RiskDecision。

## 2026-07-12盘前测试

输入：630条标准化RSS证据。

结果：

- 窗口内且相关：49条；
- 标题/URL去重后：47条；
- 进入盘前候选：47条；
- 排除窗口外：554条；
- 排除无关C级内容：27条。

候选中：B级40条、A级1条、C级6条。C级条目只能作为待核验线索，不能单独确认或提高交易权限。
