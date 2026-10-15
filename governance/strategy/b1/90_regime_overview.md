# B1 现行规则与研究状态总览（按 0AMV 区间）

> **上下文**：B1 波段策略（主）　｜　**执行者**：—（**总览**，不是规则；规则正文见 `01`~`05`）　｜　**状态**：✅ live 对照（2026-10-15 核对）
> **版本**：2026-10-15 由 `_shared/factor_exit_catalog.md`（止损 / 止盈 / 回流节）、`b1/90_research_summary.md`、`research/regime_playbook.md` 三份合并（原文在 git 历史）　｜　**代码依赖**：`core/exit_rules.py`、`pipeline/screening/score_candidates.py`、`pipeline/holdings/b1_holding_state.py`
> **索引**：[`README.md`](README.md)　·　因子注册表全量清单见 [`../_factors/README.md`](../_factors/README.md)　·　改动须记 [`CHANGELOG.md`](../../../CHANGELOG.md)
>
> 一张表看清：按 0AMV 区间（做多 / 空头）分，**入场因子、止损、止盈清仓、仓位**各自 live 现在
> 怎么做、研究证据到哪一步、还在研什么。**研究单元判毕或 live 规则变动时同步更新本页**；字段级
> 真相以代码为准（`contracts/EXIT_RULES.json`、`contracts/SCREEN_FORMULA_REGISTRY.json` 的
> `scoring.weights`）。**调任何分值 / 阈值 / 开关之前先回测**。

## 研究结论一句话

**可辩护的框架 = 0AMV 做多（主择时）+ J<13 / 反转 K 入场 + 带空间止损（≥5%）+ BBI 移动止盈 +
分散多槽。** 收益来自「择时 + 交易管理 + 分散」，不来自「选哪只股」——这也是核心思想「不追求
胜率，追求一定胜率基础上的更高盈亏比，杠杆加在出场与仓位」的研究依据
（[`../_shared/system_principles.md`](../_shared/system_principles.md) 核心原则第 0 条）。

- **量级**只在 0AMV 做多期成立（已实现 +0.454R/笔、做多期年化约 10%、回撤个位数，见
  [`R1`](../../research/R1_core_framework.md)「量级」节；无择时的全宇宙基准已实现为负，R11）。
  引用任何 CAGR 必须带 R1 规定的三条修正项（幸存者偏差 / 单区间 / 组合层限跳敏感）。
- ⚠️ 2021 后无退市股（R14），近期窗口**无法去幸存者偏差**——绝对读数只作相对排序。
- 各单元的重跑状态见 [`research/README.md`](../../research/README.md)「⚠️ 重跑清单」（当前已全部闭环）
  与「当前状态」。

## 一、区间口径

- 状态机（live `amv_state._resolve_state` 与研究 `load_amv_regime` 同口径）：confirmed 单日
  >+4% 进**做多**、<−2.3% 进**空头**，其间**粘滞**（延续前态）。
- live 的「**中性**」只在**没有已知前态**时出现（冷启动、缺值），不是第三种行情状态；
  选股侧许可文案「仅低吸」。研究侧是两态，没有中性（首段状态未知直接丢弃）。
- 研究默认基底（v0.93 起）= **0AMV 做多 + J<13 钉死**；除下文注明的单元外，研究结论只在
  做多区间成立。

## 二、0AMV 做多区间

| 环节 | live 现行（代码位置） | 研究结论（证据） | 在研 / 待研 |
|---|---|---|---|
| **入场：进池** | J<13 唯一硬门槛（`j_low_gate`，enrich `_apply_j_gate`） | 0AMV 择时是唯一站得住的 edge，约 15pp（R4 ✅ L4）；edge 来自择时 + 分散 + 交易管理（R1） | — |
| **入场：打分 / 分层** | V0 八段技术分 + 资金意图二轴 → A/B/C/D（`score_candidates`；权重在 `SCREEN_FORMULA_REGISTRY.json`） | 选股无 alpha：打分重构 / 校准 / 调权 / 组合搜索五轮全灭（R22~R30、R34）；正例监督式四路全灭（R36）；按分数分档调仓位证伪、中段最好（R42） | 打分系统终局待拍板（R35 / TODO #87，等判定段第一批）；R36 残留单腿 adx_gt_60 进第一批 |
| **入场：否决 / 封顶** | 冲刺波首个 B1 禁买；出货 watch/high 扣分；无止损位 → 封顶 B（`apply_risk_downgrades`） | RD 深水 RSI 门内提醒成立（R21 ✅）；QSX/DKX 过滤、QSX 排除态证伪（R23 / R33） | — |
| **入场：风格 / 板块** | 板块相位只作证据层（R4 未复现，v0.50 移出可买定义） | 板块腿方向不稳（R4 L2）；活跃板块 ∩ J<13 证伪（#26，v0.79） | **R43**：多头区间进攻最猛的板块与风格（预注册，工具已落地，待 #91 → 跑数） |
| **止损** | `hard_loss` −10% → P0 清仓；`loss_reduction` −7% → P1 减仓（`EXIT_RULES.json`）；N 型结构 **L1 前低跌破 → P0**、**L2 回踩失守 → P1**；趋势箱体破位 / 下降 N 确认 → P0（`b1_holding_state`） | **5% 是崖不是坡**（5%→4% 期望掉 42%，R10 / R12）；买入日最低价锚定时 tick_3 余量最优，最低价 × 0.97 与 ATR 余量均否决（R10 #20）；「近 10 日最低价」当止损证伪（太近，中位 1.17%，R41 ❌） | **R44**：J 日最低价 × (1−x)，x ∈ 5%~20% 寻最优宽度（预注册，工具 #100）；N 型结构位止损**研究引擎未建模**（见下「缺口」） |
| **止盈 / 清仓** | BBI 上方连续两根中大阳 → P2 分批（`scale_out_two_bull`）；BBI 两日收盘跌破 → P1 清仓评估，首破 → P2 观察；放量长阴 → P1；KDJ 死叉 → P2 观察 | 研究基准档 pct5_trail08；出场参数进化三战役收口（R37：1 untested 存档不接 live + 2 证伪）；纯出场组合无择时为负（R11） | 因子 × 出场交互：ADX 判 untested，进第一批重测（R39）；保本 / 移动 / 时间止损、成本区「不涨就拍」仅研究侧（`EXIT_RULES.json` 关闭） |
| **仓位 / 加减仓** | 减仓幅度表 P0 100% / P1 10~25% / P2 10~20%；B1 默认盘中不交易 | 盈亏比方法论成立（R9 ✅）；打分 → 仓位分层证伪（R42） | 持仓计划（`position_plans`）影子观察中，plan 信号并入与否待 K=20 事件（TODO #60） |

## 三、0AMV 空头区间

| 环节 | live 现行（代码位置） | 研究结论（证据） | 在研 / 待研 |
|---|---|---|---|
| **入场** | **不开仓**：候选一律封顶 B、next_step 强制 `observe_price`（`score_candidates` 664 / 1000 行）；B1 规则「0AMV 空头不执行 B1」（`b1/01_swing_rules.md`） | 长多 B1 非 bear-proof，空头期空仓是真正的熊市保护（`01_swing_rules`）；**空头区间主动开仓从未被研究过**（R40 立项依据） | **R40**：46 门 × 5 档全栈做多探索（r40_a1 跑数中）；**R43**：空头区间抗跌 / 逆势上涨的板块与风格（预注册） |
| **止损 / 清仓** | 持仓**最高优先级 P1「空头区间反弹减仓」**；当日上涨再触发 P1「空头反弹减仓」；其余止损同做多区间（参数不按 regime 区分） | 「只卖不买 + 反弹减仓」只有 `backtest_0amv_bear_regime` 的防守两场景读数，**没有研究单元判决**（见「缺口」） | R40 判毕后若有可做多配置，其出场档即空头区间的出场候选 |
| **止盈** | 无专门规则（同做多区间；空头期反弹以减仓为主） | — | 同上 |
| **仓位 / 加减仓** | **禁止加仓补仓**（`b1_holding_state` `allow_add=False`） | — | — |

## 四、研究与 live 的已知缺口

1. **N 型结构止损只在 live**：持仓状态机用 N 型 L1 / L2 前低做 P0 / P1 结构止损，但研究
   交易引擎 `simulate_b1_trade` 没有这条出场——回测读数里的止损只有百分比 / 买入日最低价两种
   口径，live 的结构止损效果**从未被回测验证**。要研究「结构位止损」需先把 L1 / L2 as-of
   搬进引擎（新单元，Phase 0 核实检测器能否 as-of 稳定给出支撑位）。
2. **`loss_reduction` −7% 减仓引擎表达不了**：研究侧以 pct10（忠实 `hard_loss`）作现行版
   主读数（R41 同口径），−7% 减仓的效果没有回测证据。
3. **空头区间防守规则没有研究单元**：「只卖不买 + 反弹减仓」的依据只是一个工具的两场景读数，
   证据等级未定级；R40 跑完后应把防守规则也作为对照臂写进结论。
4. **板块成分只有当前快照**（`sector_members.json`）：任何用成分股回溯的板块研究都有
   look-ahead，R43 因此只用板块指数本身。

## 五、出场规则目录

### 止损方案（`exit_rules.stop_rules`）


| rule_id | enabled | 参数 | 说明 |
|---|---|---|---|
| `hard_loss` | ✅ live | pnl_pct = **−10%** | 单票亏损超 −10% → P0 强制风控评估（清仓） |
| `loss_reduction` | ✅ live | pnl_pct = **−7%** | 短线止损 → P1 复盘/减仓 |
| `breakeven_stop` | ⛔ 研究侧 | breakeven_trigger = 0.0 | 保本止损（回测中，未进 live） |
| `trailing_stop` | ⛔ 研究侧 | trail_pct = 0.0 | 移动止损（回测中） |
| `time_stop` | ⛔ 研究侧 | time_stop_bars = 0 | 时间止损（回测中） |

**减仓幅度表**（`reduction_pct_of_holding`）：P0 清 100%、P1 减 10–25%、P2 减 10–20%。

研究侧已验证的出场口径（m2/R10）：**5% 是崖不是坡**（止损从 5% 收紧到 4% 期望掉 42%）；
止损要**带空间**（不贴买入 K 最低价）。研究侧 `simulate_b1_trade` 的形参、
`EXIT_RULES.json` 的 params、`strategy_grid` 的出场轴**三者键名一致**——优胜配置可直接拷入 live。

### 止盈方案（`exit_rules.take_profit_rules`）

| rule_id | enabled | 参数 | 说明 |
|---|---|---|---|
| `scale_out_two_bull` | ✅ live | consecutive_bull_bars = 2、require_above_bbi = true | **BBI 上方连续两根中大阳 → 分批止盈**（P2，减 10–20%） |
| `cost_zone_flat` | ⛔ 研究侧 | cost_zone_bars / pct = 3.0 / grace = 1 | 成本区「不涨就拍」（回测中） |

live 持仓链的其余卖出判定在 `b1_holding_state`（P0~P3 状态机：N 型 L1 结构硬失效位、
BBI 破位、0AMV 空头区间反弹减仓最高优先等）——它们是**持仓规则**不是止盈方案目录成员，
口径见 [`../b1/01_swing_rules.md`](01_swing_rules.md)。

**持仓计划持久化**（v0.82）：买入导入时把止损/止盈计划落盘 `data/trades/position_plans.json`
（止损价取 ≤ 买入日最近 stock_pool 的 `stop_loss_ref`，兜底 entry×0.93），14:45/17:00 报告
影子双跑对照（v0.83）；是否并入正式判定的判据已改写（TODO #60：实现一致性 + K=20 独立事件），现状见上「二、做多区间 · 仓位」。

### 持仓状态机信号（`pipeline/holdings/b1_holding_state.py`，live 每日判定）

按 `SIGNAL_ORDER` 排序，越靠前越优先；P0 = 清仓评估、P1 = 减仓 / 清仓评估、P2 = 观察 / 分批、P3 = 条件持有。

| 序 | 信号 | 优先级 | 含义 |
|---|---|---|---|
| 0 | `hard_loss` | P0 | 持有盈亏 ≤ −10% 止损 / 清仓评估 |
| 1 | `n_l1_breach` | P0 | 跌破 N 型主结构前低 L1 |
| 2 | `trend_box_break` | P0 | 趋势箱体破位退出评估 |
| 3 | `desc_n_confirmed` | P0 | 下降 N 型结构确认 |
| 9 | `bear_regime_reduce_top_priority` | P1 | **0AMV 空头区间反弹减仓（最高优先级）** |
| 10 | `n_l2_breach` | P1 | N 型回踩 L2 失守 |
| 11 | `bbi_two_close_breach` | P1 | BBI 两日收盘跌破 → 清仓评估 |
| 12 | `heavy_large_bear` | P1 | 放量长阴减仓 / 清仓评估 |
| 13 | `downtrend` | P1 | 日线下跌趋势 → 反弹减仓 |
| 14 | `bear_rebound_reduce` | P1 | 0AMV 空头区间当日反弹 → 减仓 |
| 15 | `loss_reduction` | P1 | 持有盈亏 ≤ −7% 减仓评估 |
| 20 | `bbi_first_breach` | P2 | BBI 首破 → 次日收复观察 |
| 21 | `two_bull_profit_take` | P2 | BBI 上方两根中大阳 → 分批止盈 |
| 22 | `kdj_death_cross` | P2 | KDJ 死叉 → 动能转弱观察 |
| 30 | `shrink_small_bear` | P3 | 缩量小阴 → 条件持有一天 |
| 31 | `reversal_k_candidate` | P3 | 反转 K 候选观察 |

## 六、研究 → live 回流通道

`research/strategy_grid.py`（v0.85）：因子轴（scorer × entry_gate）× 出场轴网格寻优，
`cell_signature` 钉窗口/宇宙防混口径复用（v0.86）；优胜配置附 `exit_rules` 块，
键名与 live `EXIT_RULES.json` 一致可直接拷入。**纪律**：报告头部固定标注 R11 未决
（基准口径为负 ⇒ 读数仅供相对排序）；任何分值/阈值/开关的变更先回测、再落 CHANGELOG。

## 更新约定

- 研究单元判毕：更新对应格的「研究结论」并把「在研」项移走；
- live 规则改动（CHANGELOG 有行）：更新「live 现行」、出场规则目录与头部核对日期；
- 新单元立项：在对应格的「在研 / 待研」登记单元号。
