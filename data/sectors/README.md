# sectors 数据说明

## 文件

- `tdx_sector_list_raw.json`：本地 TQ `get_sector_list()` 导出的原始板块代码列表，共约 588 个。
- `sector_member_probe.json`：按代表股反查板块成分得到的完整命中结果。
- `sector_probe_summary.json`：按主题聚合后的共同命中/高频命中摘要。
- `sector_code_map.json`：策略 Team 使用的板块代码映射 v1。

## 使用规则

1. `primary_sector_codes`：优先用于 `theme_tracker` 技术趋势监控。
2. `candidate_sector_codes`：只作辅助验证，不可直接当成强信号。
3. `confidence=high/medium_high`：可进入日常监控。
4. `confidence=medium`：需要结合代表股、涨停结构和人工判断。
5. `confidence=pending_probe`：暂不进入自动交易过滤，仅保留观察。

## 重要限制

本地 TQ 板块列表主要返回代码，不返回官方名称。当前 v1 映射来自：

- 代表股成分命中
- 个股概念/申万行业语义
- 当日涨停主题线索

因此它是“策略可用映射”，不是官方板块名称表。后续需要补充 `sector_code -> official_name` 数据源。

## 当前重点方向

- AI算力/服务器/液冷：`880545.SH`
- 半导体/芯片/存储/封测：`881319.SH`
- 机器人/具身智能：`880552.SH`
- 证券：`880679.SH`
- 船舶军工：`881290.SH`
- 燃气能源：`880705.SH`
- 医疗设备/AI医疗：`881241.SH`
- 稀土：`881082.SH`

> 风险提示：板块支持只是过滤器，不能直接推出个股买入信号。真实买入仍需满足 stock_pool、buy_strategy、risk_control、chief_decision 的全链路确认。
