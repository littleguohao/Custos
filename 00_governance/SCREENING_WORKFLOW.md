# 每日选股 Screening 链工作流

日期：2026-07-21
状态：已重建（v1，确定性脚本，LLM 不参与判断）

## 链路

```
全A股(~5500)
  → [1] 公式初筛  07_tools/screening/formula_screen.py
        TQ formula_process_mul_xg 批量跑注册公式 → 01_data/screening/{date}_formula_hits.json
  → [2] 充实+模式识别  07_tools/screening/enrich_candidates.py
        本地日线算 BBI/日J/量比/20日相对强度；硬排除 → 01_data/screening/{date}_candidates_enriched.json
  → [3] 板块过滤+打分  07_tools/screening/score_candidates.py
        sector_state + 0AMV 共振矩阵打分分层 A/B/C/D → 01_data/stock_pool/{date}_stock_pool.json
  → [4] 渲染备选表格  07_tools/screening/candidate_table.py
        → 03_daily_plans/_supporting/{date}/{date}_candidate_table.md（日报证据层）
```

运行时点：18:00 独立链 `run_1800.py`，与三份报告（0905/1445/1700）完全分离。
它在 17:00 盘后复盘链之后运行，消费其产出的当日 `sector_state.json`、
`risk_decision.json` 和已刷新的 EOD K线。四个阶段全部 best-effort：
TdxW 未运行或任一阶段失败时整链干净降级（status=unavailable / partial），
不报错、run log 记为 `degraded`。

## 各段规则要点

### [1] 公式初筛

- 公式注册表：`00_governance/SCREEN_FORMULA_REGISTRY.json`。**只能引用客户端
  已存在的公式名**（系统公式或用户预建公式），不能跑任意表达式（实测
  ErrorId=9）。
- 股票池：全 A（exclude_bj 在此过滤；ST、上市天数在 [2] 过滤）。
- 单公式超时 15s；连续 2 个公式失败熔断，剩余标记 `circuit_open_skipped`。
- 命中判定：返回的按股按日 0/1 序列，最后一个元素（最新交易日）为 '1'。

### [2] 充实 + 模式识别

确定性指标（本地 vipdoc 日线）：BBI=(MA3+MA6+MA12+MA24)/4 与偏离、
日 J(KDJ 9,3,3)、量比（当日量/前5日均量）与 20 日量分位、20 日相对强度
（个股20日涨幅 − 上证指数999999 20日涨幅）、建议止损位（近10日最低价）。

模式标签（每个标签的实际数值随 candidates 落盘）：

| 标签 | 规则 |
|---|---|
| bbi_above | 收盘价 >= BBI |
| j_low | 日 J < 13 |
| volume_contraction | 量比 <= 50% 且 20 日量分位 <= 10% |
| reversal_k_candidate | j_low + volume_contraction + 涨跌幅∈[-2%,+2%] + 振幅<=7%（四项同时） |
| relative_strength_strong | 20 日相对强度 >= +3pp |

硬排除：名称含 ST、停牌（无当日 K 线）、上市不足 60 天、risk_decision
高优先级股、北交所。已持仓股打 `is_holding` 标记但不剔除。

板块映射：最新 `01_data/sectors/*_tq_sector_map.json` 成分股关系 →
`sector_code_map.json` 主题（primary 优先于 candidate）；无映射则 sector=未知。

### [3] 板块过滤 + 共振打分

共振矩阵（技术面 × 板块热度 → base bucket）：

| 技术面\板块 | 强 | 中 | 弱 | 未知 |
|---|---|---|---|---|
| 强 | A | B | C | C |
| 中 | B | C | D | D |
| 弱 | C | D | D | D |

板块过滤封顶：主升/修复→可 A；震荡/分歧→最多 B；退潮→最多 C；未知→不进 A。

附加调整：

- 0AMV 空头 → 全池最高 B 且 next_step=observe_price。
- 无可定义止损位（近10日最低价缺失）→ 不得入 A（封顶 B，打 no_stop_loss_ref）。

总分 = 0.6×技术分 + 0.4×板块分 + 共振调整（强共振+5/反向−5），
打分明细（score_detail）随 StockPool 落盘可复盘。

next_step：A→generate_buy_plan，B→observe_price，C→long_term_track，D→avoid
（0AMV 空头一律 observe_price）。

### [4] 备选表格 + 日报

- `candidate_table.py` 渲染分组表格进 `03_daily_plans/_supporting/{date}/`。
- `daily_report.py` 在"主线、机会与风险"节内追加"公式选股备选池"小节，
  只读 stock_pool.json；文件缺失显示"当日未运行选股链"，不影响报告生成。

## 客户端预建公式运维流程

1. 在通达信客户端"公式管理器"新建**条件选股公式**（名称即注册表 tq_name）。
2. 在 `SCREEN_FORMULA_REGISTRY.json` 添加条目：`enabled:false` 起步，
   注明 category 与 note。
3. **改公式或改参数前必须先回测**（历史信号质量验证），回测结论记录到
   `05_strategy_versions/strategy_version_log.md` 或对应复盘文件。
4. 回测通过后置 `enabled:true`；次日 18:00 链自动纳入。
5. 公式连续失败会触发熔断并落盘 error，排查时先看
   `01_data/screening/{date}_formula_hits.json` 的 per-formula error。

当前占位：`B1_REVERSAL_K`（tq_name=TODO_CLIENT_FORMULA，enabled:false），
需在客户端预建 B1 反转K 选股公式后启用。

## 明确不做

- 不接 chief_decision.buy_actions、不生成 BuyPlan、不改新开仓权限逻辑。
- 不做盘中实时选股；不接龙虎榜。
- StockPool 仅为证据层候选；A/B 池亦须经总控与风控审批。

## TQ 服务可靠性警示

`formula_process_mul_xg` 等 TQ 接口参数形态错误可能挂死 TdxW 服务端
（单线程阻塞，需重启客户端恢复，见 00_governance/TQ_INTERFACE_PROBE_2026-07-20.md §四）。
因此：固定参数形态写入代码（formula_name/formula_arg/stock_list/stock_period/
count/dividend_type），不得随意改动；所有调用带 15s 超时与熔断。
