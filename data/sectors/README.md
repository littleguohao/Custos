# sectors 数据说明

## 文件

- `*_tq_sector_map.json`：板块（880xxx 概念/细分行业）→ 成分股的反向映射，
  走势贴合归属（theme_tracker_report）的候选池来源。

## 已废弃

- ~~`stock_concept_tags.json`~~：个股官方概念标签（TQ miscinfo）。**v0.157 起
  随数据源 `concept_tags.py` 整体删除**（owner 拍板：v0.156 起已无在链消费方，
  仅保温无读者）。
- ~~`sector_code_map.json`~~：人工语义主题映射表（semantic_tags /
  primary_sector_codes / candidate_sector_codes）。**v0.156 起废弃删除**
  （owner 拍板 2026-08-28：板块归属全部去掉人工判断，唯一逻辑=走势贴合
  60 日日收益相关）；同时废弃的还有持仓 owner 指定层
  ~~`holding_mainline_overrides.json`~~（v0.149 已撤）。
  历史内容可从 git 考古（删除发生在 v0.142-149 区间）。

## 现行归属规则

1. 板块归属唯一判据 = **走势贴合**：持仓/个股与候选板块指数的 60 日日收益
   Pearson 相关，贴合最高者胜（`theme_tracker_report.resolve_holding_sector`）。
2. 贴合无有效数据（候选板块无 K 线 / 重叠不足 20 根）⇒ 如实「未定」，不猜、
   无兜底、无人工指定层。
3. 候选股（screening 链）不再有主题族归属；「板块」展示列以 TDX 官方细分
   行业（881xxx，每股恰好一个）为准。

> 风险提示：板块支持只是过滤器，不能直接推出个股买入信号。真实买入仍需满足 stock_pool、buy_strategy、risk_control、chief_decision 的全链路确认。
