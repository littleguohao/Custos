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

板块映射：**优先 miscinfo 概念标签**（`07_tools/local_tdx/concept_tags.py`，
TQ `download_file down_type=4`，run_1800 第 2 步每日刷新，落盘
`01_data/sectors/stock_concept_tags.json`）——个股官方概念标签与各主题
`semantic_tags` 双向子串匹配，命中数最多的主题中标，无命中则 sector=未知
（宁缺毋滥）。标签缺失时回退 v1 的 880 成分股反查（`*_tq_sector_map.json`
→ `sector_code_map.json`，primary 优先于 candidate，已知存在错配，仅作
兜底）。候选落盘 `sector_source`（concept_tags / tq_880_fallback）标明来源。

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

## 策略对齐（B1/CZ）

2026-07-21 起，enrich 段新增对齐 `00_governance/b1_swing_strategy.md`（B1）与
`00_governance/cz_strategy.md`（CZ）的确定性检测器。全部只依赖本地 vipdoc
日线 OHLCV，不引入新数据源。**所有阈值集中在 `enrich_candidates.py` 顶部常量
并标注"待回测参数"**：策略原文要求阈值可配置、实际值随候选落盘，完成样本
回测前不得视为已校准。

### B1 类标签

| 标签 | 出处 | 口径 | 阈值（待回测参数） |
|---|---|---|---|
| wave_type | B1 §四.0 | 近60日"有效启动低点→阶段高点"拉升段三分类；优先级 sprint > rally > buildup（特征冲突取保守） | buildup 段涨幅25%-50%+启动段放量长阳（≥5%且量≥前5日均×1.5）；rally 二次启动（前段摆动≥15%）+段涨幅35%-50%；sprint 近20日≥2次涨停(≥9.8%)+近10日涨幅≥25%+顶部量≥前5日均×1.5 |
| weekly_j / weekly_j_low | B1 §四.1 | 日线 resample(W-FRI) 成周线后算 KDJ J；J<13 为周线 B1 候选（主线口径） | J<13 |
| non_one_wave | B1 §四 | (a)上涨段单日量/段均量<2；(b)高点±3日无放量大阴（跌幅>3%且量>前5日均×1.5）；(c)回调段均量/上涨段均量<0.7。三全=confirmed；(b)反向或回调放量破位=revoked；其余 insufficient | 2.0 / -3%+1.5 / 0.7 |
| repair_signals | B1 §四.2 | j_turn_up（今J>昨J且昨J<20）、volume_shrink_stop_fall（量比≤0.7且涨跌±2%内）、rs_turn_strong（5日相对强度由负转正） | 20 / 0.7 / ±2% |

### CZ 类标签

| 标签 | 出处 | 口径 | 阈值 |
|---|---|---|---|
| five_day_entry | CZ §十六 | 三条件缺一不可：收盘>MA5；连续3日放量（递增或均≥20日均量）；近7日存在单日量≥前一日×1.45 | 1.45 / 7日 |
| volume_sustain | CZ §14.6 | 近13日量峰值日：峰值日≥7日前且其后日均量≥峰值×55% → mainline_confirmed；连续3日量<峰值×55% → retreat；否则 neutral | 55% / 13日 / 3日 / 7日（待回测） |
| leader_volume | CZ §九 | 连续3日量 ≥ 前20日最低日量×1.7 | 1.7 |
| three_lows | CZ §九/§18.6 | 低价格（收盘自250日高点回撤≥40%）+ 低量（当日量<250日均量×30%）；第三维"低关注度"非量价可计算不输出 | 40% / 30%（待回测参数） |
| bottom_volume | CZ §14.6 | 回撤≥40% + 当日量≥250日均量×2 + 当日最低价≥前20日最低价（不再创新低） | 40% / 2倍 / 20日（待回测参数） |

数据不足 250 日 K 线时 three_lows/bottom_volume 输出 `available=false`，
不得硬算；其余检测器亦各自带 available 标记。

### 板块白/黑名单（CZ §七）

`00_governance/CZ_SECTOR_PREFERENCE.json`（cz-sector-v1）。作用于候选股经
`sector_code_map.json` 映射后的**主题名子串匹配**：命中 favored →
cz_sector=favored，命中 avoid → cz_sector=avoid（avoid 优先，保守），否则
neutral。**注意：现有 sector_code_map 覆盖粗糙，匹配不上即 neutral，宁缺
毋滥，不要猜。** 名单文件缺失时该机制整体不起作用（全 neutral），并在
stock_pool.json 的 `cz_sector_status`/`degraded_reason` 注明。

### 打分与分层整合（score_candidates.py）

- 加分（计入技术分，factor_contrib 逐项落盘）：five_day_entry +8、
  leader_volume +6、bottom_volume +6、repair_signals 每项 +3（上限 +6）、
  non_one_wave=confirmed +5。
- 降档/否决（cap 只降不升）：
  - wave_type=sprint → 最高 B（B1 §四.0：冲刺波后首个 B1 禁买），
    next_step 不得 generate_buy_plan；
  - volume_sustain=retreat → 最高 C（CZ §14.6：主力撤退）；
  - cz_sector=avoid → D（CZ §七）；
  - non_one_wave=revoked → 最高 C（B1 §四：撤销条件）。
- 0AMV 空头最高 B、无止损位禁 A、共振矩阵等既有规则不变。

### 边界声明

- **财务类规则暂缓**：CZ 的 PEG/FCF/营收增速/"真科技8条"等基本面口径因
  数据源未接入，本轮不实现，不得用量价代理冒充。
- **拉升波分类/非一波流是首个 B1 候选的辅助判断，不构成独立买点**；
  B1 买入仍需 J 低位、修复确认、止损位与市场许可同时成立（B1 §四.2）。

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
