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
  ErrorId=9）。当前启用：KDJ_J_LOW（用户自建 `J小于13`，B1 回调型初筛，
  2026-07-22 接入；同日用户决策停用 UPN_3/MA_BUY 等非 B1 动量/趋势公式）。
- **自选池通道**（`manual_pools`）：用户在通达信客户端手工维护的备选池
  （如"震荡"），经 `07_tools/screening/manual_pools.py` 读取
  `T0002/blocknew/*.blk`（本地文件，TdxW 离线也可用），以
  `category=manual_pool` 伪公式条目与公式命中并集进入 [2]。池子增删股票
  只需在客户端操作，次日自动生效；新增池子在注册表 `manual_pools` 加一条
  （block_name 为客户端板块中文名）。
- 股票池：全 A（exclude_bj 在此过滤；ST、上市天数在 [2] 过滤）。
- 单公式超时 15s；连续 2 个公式失败熔断，剩余标记 `circuit_open_skipped`。
- 命中判定：返回的按股按日 0/1 序列，最后一个元素（最新交易日）为 '1'。
- **注意**：`formula_get_all` 已两次打挂 TQ 服务（2026-07-20 / 07-22），
  禁止使用；核对公式名用 `formula_process_mul_xg` 小参数试跑（ErrorId=9
  为安全报错）。

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

**J<13 硬门槛**（2026-07-22 用户决策，`universe.j_low_required` 默认 true）：
全通道候选（公式与自选池一视同仁）必须日 J<13，J 不可计算视同不满足；
在 J<13 基础上用 `perfect_b1_fit`（0-8 梯度：J 深度/回踩贴线/缩量程度/
MACD 零轴上/DKS 上行，阈值见 enrich 顶部待回测常量）给贴合度分，
越符合 good_b1 完美图形分数越高。

板块映射：**优先 miscinfo 概念标签**（`07_tools/local_tdx/concept_tags.py`，
TQ `download_file down_type=4`，run_1800 第 2 步每日刷新，落盘
`01_data/sectors/stock_concept_tags.json`）——个股官方概念标签与各主题
`semantic_tags` 双向子串匹配，命中数最多的主题中标，无命中则 sector=未知
（宁缺毋滥）。标签缺失时回退 v1 的 880 成分股反查（`*_tq_sector_map.json`
→ `sector_code_map.json`，primary 优先于 candidate，已知存在错配，仅作
兜底）。候选落盘 `sector_source`（concept_tags / tq_880_fallback）标明来源。

### [3] 个股量价分层 + 板块提示

**2026-07-23 重构（用户决策）**：分层 A/B/C/D 由**个股自身**定夺，**板块不再封顶**
——很多强势个股不跟原板块走，仅因板块弱把走势好的个股打到 D 得不偿失。

base bucket ＝ 技术结构 × 资金意图（均为个股维度）：

| 技术结构\资金意图 | 强 | 中 | 弱 |
|---|---|---|---|
| 强 | A | B | C |
| 中 | B | C | D |
| 弱 | C | D | D |

- 技术结构 = `technical_score` 分级（强>=65 / 中40-64 / 弱<40，阈值待回测）。
  **`technical_score` 优先用 S_shape v3.0 有界加权评分**（借鉴 workflow.pptx「常规量化选股工作流」
  v3.0 沙漏模型；无 s_shape 数据时回退旧 patterns 加权）：

  > `S_shape(0-100)` = 压缩/收敛(0-20) + 枢轴邻近/突破(0-15) + 量(20/60日&斜率)(0-20)
  > + 口袋妖怪(0-15) + 上方套牢供给(0-10) + 均线结构(0-10) + 事件风险(0-10，个股新闻未接入→中性占位)；
  > `S**` = clamp(S_shape + Δ催化(0-10) − P惩罚(放量阴线 −15/−10/−5，前高近则减半), 0, 100)；
  > 建议：S**≥70 可买 / 60-69 观望 / <60 不买。

  分项封顶天然有界（解决旧 technical_score 无界累加饱和问题），实现见
  `07_tools/screening/s_shape.py`；**幻灯片部分阈值被遮挡，相关常量取合理猜测并标注"待回测"**，
  校准前不视为定型，各分项实际值随候选落盘可复盘。
  **校准工具**：`07_tools/screening/backtest_factors.py`（纯分析、只读本地日线、as-of 切片无未来函数）
  走查历史 S** → 前向 MFE/MAE/胜率，按 S** 档/建议/分项分组，验证"可买(≥70)"是否显著优于"不买(<60)"、
  各分项 hit 是否有正向 lift，据此重估 s_shape.py 顶部的待回测阈值与权重。
- 资金意图 = `capital_intent_strength`（放量点火 +3、知行多头且沿短线上行 +2、
  20日相对强度强 +2、龙头量能 +2、底部巨量 +2、量能持续=主线确认 +2、点火 +1、
  反转K +1；≥5 强 / ≥2 中 / 否则 弱）。仅正向计"资金在进"，派发/顶背离由风控 cap 否决。

**板块降为提示**（不封顶）：

- 进 score：总分 = 0.6×技术 + 0.4×板块分 + 共振调整（强共振+5/反向−5）。
- 定 trade_style：主升/修复→`波段`；震荡/分歧→`波段(谨慎)`；退潮/未知→`短线(交易性)`。

**仍保留的风控/回避硬否决**（与"板块弱"无关）：

- 0AMV 空头 → 封顶 B 且 next_step=observe_price；无止损位 → 封顶 B。
- 冲刺波首个B1 → 封顶 B；非一波流撤销/量能撤退 → 封顶 C。
- 主力出货五方式 high→D/watch→C；MACD 顶背离/三打白骨精 → 封顶 C；CZ 回避名单 → D。
- 以上封顶开关见 `scoring.cap_rules`（默认全开）。

next_step：A→generate_buy_plan，B→observe_price，C→long_term_track，D→avoid
（0AMV 空头一律 observe_price）。打分明细（score_detail，含 capital_intent_level）
与 trade_style 随 StockPool 落盘可复盘。

### [4] 备选表格 + 日报

- `candidate_table.py` 渲染分组表格进 `03_daily_plans/_supporting/{date}/`，
  表头下带**得分 Top 5** 榜单（按总分降序跨分层，含总分/技术分/分层/风险标记）。
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

### MACD 十大技术（macd十大技术精讲）

`check_macd_technics`（2026-07-22 接入，阈值见 enrich 顶部 `MACD_*` 待回测常量）：

| 因子 | 口径 | 作用 |
|---|---|---|
| zone 三区间 | DIF/DEA 零轴上+红柱扩张=第一区间（+3）；红柱脱离 DIF=第二；脱离 DEA（≤0）=第三 | 打分 |
| zone1_restart | 昨日 hist≤0、今日重新扩张且 DIF>0——"3/5 浪的第一区间"（+5） | 打分 |
| bottom_divergence | 窗口内收盘摆低 L2<L1 但 DIF 低点抬高（+5，B1 修复确认） | 打分 |
| top_divergence | 收盘摆高 B>A 但 DIF/hist 低于前峰 | 封顶 C（cap_rules.macd_divergence） |
| three_peaks 三打白骨精 | 连续 3 摆高递增 + DIF 连续 3 峰递减 | 封顶 C（同开关） |
| overextended 开口/空间拐离 | \|DIF\| 近 120 日 ≥90% 分位且柱体仍在 | 仅 risk_flag，不降档 |

背离用左右各 2 根收盘分型定位摆点（右确认，无未来函数）；浪形（3/5 浪精确计数）
与面积背离未确定性化，暂不实现。

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

### 知行量价 + 出货识别（B1 §四.5 / §七.3）

来源：优质 B1 图集 + 主力出货五方式图集，落地为确定性因子（阈值待回测、实际值随候选落盘）。知行趋势线严格对齐通达信 ZSDKX。

- **知行指标**（`technical_monitor.zhixing_state`）：`QSX=EMA(EMA(C,10),10)`（白线/快）、`DKS=(MA14+MA28+MA57+MA114)/4`（黄线/慢）；多头 `QSX>DKS`，金叉=上穿当日；需 ≥114 根，否则 available=false。
- **正向因子**（`enrich`，进 `technical_score` 加分）：知行多头 `qsx_gt_dks`(+6)、放量点火 `ignition`(+4)、回调缩量企稳 `pullback_shrink`(+3)、复合 `b1_ignition`(+8，=（J<13 或 反转K）+ 缩量企稳 +（近N日金叉 或 点火））、沿短线上行 `ride_above_fast`。
- **负向因子**（`enrich.detect_distribution`，出货五方式）：① 顶部天量大阴、② 次高点巨量长阴、③ 阶梯放量跌破 QSX（白线用 QSX）、④ 双头双巨阴、⑤ 顶部绿肥红瘦；命中 ①/② 或 ≥2 项 → `risk_level=high`，否则 `watch`。
- **打分接入**：`score_candidates` 新增 `distribution_cap`（registry `scoring.cap_rules`，默认开）——high→封 D、watch→封 C；关闭则仅记 `distribution_detected_cap_disabled`、不降档。
- **落盘**：候选带 `zhixing/ignition/pullback_shrink/ride_above_fast/b1_ignition/distribution`，`entry_reason` 追加"知行B1点火确认/知行多头/出货信号:*"。

### 边界声明

- **财务类规则暂缓**：CZ 的 PEG/FCF/营收增速/"真科技8条"等基本面口径因
  数据源未接入，本轮不实现，不得用量价代理冒充。
- **拉升波分类/非一波流是首个 B1 候选的辅助判断，不构成独立买点**；
  B1 买入仍需 J 低位、修复确认、止损位与市场许可同时成立（B1 §四.2）。

## 可配置项与数据一致性

所有开关集中在 `00_governance/SCREEN_FORMULA_REGISTRY.json`，默认值＝历史行为，
改动前同样遵循「先回测」原则。

- **`scoring.cap_rules`（封顶规则开关，默认全开）**：`sprint_wave` / `volume_retreat`
  / `non_one_wave_revoked` / `cz_avoid_sector` 四条**待回测启发式**驱动的降档规则。
  样本回测校准前若只想「观察不降档」，可逐条置 `false`；关闭后不再降档，但仍在候选
  `risk_flags` 记录 `<rule>_detected_cap_disabled`，并把生效开关写入
  `score_detail.cap_rules`，便于前后对比。
- **`scoring.sector_score_max`（默认 100）**：`sector_state.score` 的量纲上界。
  打分用 `0.6*技术分 + 0.4*板块分` 混合，板块分经 `normalize_sector_score` 归一化到
  0–100 并 clamp（越界/缺失/负值都被兜底），`score_detail` 同时落盘归一化值与
  `sector_score_raw`。若上游 generator 改量纲，只需改此值一处。
- **`theme_mapping.min_match`（默认 1）**：概念标签命中主题所需的最小语义标签数。
  提高到 2+ 要求更强证据、降低子串过度匹配；候选落盘 `match_count` 可复盘。

**数据源当日一致性**：第 1 段公式初筛用 TQ 在线公式评估（`return_date=False`，命中
日期由盘后调用时点决定），第 2 段充实用本地 vipdoc 日线。二者为独立来源，故：
① 若 `formula_hits.json` 的 `date` 与目标日不符，enrich 标注 `partial` +
`formula_hits_date_mismatch`；② 每只候选一律用本地日线 `last_date==date` 二次校验，
不满足者计入 `excluded(no_today_bar)`，并落盘 `signal_date`，确保命中信号与所算指标
同为当日（契约见 enrich 输出的 `signal_date_contract`）。

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
