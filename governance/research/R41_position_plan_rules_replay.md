# R41 · 持仓计划规则离线回放（计划止损 stop_loss_ref + 计划止盈 vs 现行 EXIT_RULES）（预注册）

> **家族**：交易管理（出场轴）× live 机制研究　|
> **证据等级**：L2 / L3−（双窗纪律 + 幸存者宇宙 + 多重比较显式标注——
> 与 R10/R36/R37/R40 同纪律）　|
> **状态**：📋 **预注册落档（2026-10-09，v0.312）**，判据跑数前写死；
> **v0.315 口径定稿在案**（owner 拍板四项：①现行版主读数=pct10 忠实
> hard_loss（loss_reduction 引擎表达不了，注明）/副读数 pct7 只报告不
> 作判据；②scale_out_frac=0.5 两版共用写死（bbi_exit_consec=2 /
> stop_trigger="close" / cost_bps=25 同写死）；③rdd 相对门任一窗不过
> ⇒ C2 不过标 rdd_gate_fail；④C4 预算对等=两边都不挑选，池元素=臂
> 挖掘窗 Δmargin）；**v0.317 修订在案**（owner review：C4「N=50 指
> 过门臂数」——重抽至池满或评估达上限 max_arms=10×N，原「抽 N 次」
> 实现过门率 <100% 永远 provisional；统计含义不变，两边条件对称）；
> **Phase 1 工具 ✅（v0.316 `plan_rules_replay`，钉测 +17）**——Phase
> 2 生产机跑数待 owner 发令　|
> **依赖**：上游：R10（「5% 是崖」/双窗纪律）R11（绝对读数不可引用）
> R14（幸存者宇宙）R37（判据族/出场轴收口换方向）R39（C1 不过=untested
> 修订族）；live 侧对象：`core/trades/position_plans.py`（计划生成）、
> `pipeline/screening/enrich_candidates.py`（stop_loss_ref 产出）、
> `governance/contracts/EXIT_RULES.json`（现行出场）｜判据纪律：R12（预注册）
> R13（可复现）｜工程：`backtest_factors`（引擎执行语义单源）
> 索引与主图见 [`README.md`](README.md)。

## 主题

#60 拆解的**研究那一半**（owner 2026-10-09 方案）：「plan 规则好不好」是
研究问题，用历史离线回放回答，样本量是几百笔——与 live 影子（「管线算得
对不对」，实现问题，台账 `plan_shadow_ledger` + 事后打分
`plan_shadow_review` 回答）彻底分开。现状：持仓计划（`position_plans`）
自 v0.82 落地后只做影子判定（v0.83 起只展示不生效），**规则本身从未被
历史数据验证过**——计划止损价（stop_loss_ref）是选股口径的产物，它对
「持仓期的出场」是否优于现行 EXIT_RULES 是未回答的问题。

## 目标

在历史信号集上**配对**比较两套出场规则（同信号、同执行语义，唯一变量
是出场规则）：**计划版**（止损=stop_loss_ref 按信号日 as-of 重算 +
止盈=计划分批止盈 scale_out_two_bull）vs **现行版**（EXIT_RULES 现行
规则族）。双窗硬隔离；pre2019 untouched 段按纪律保留终审。非目标
（写死）：不改 live 任何规则（并轨走 #60 双条件判据，本单元只提供
「规则有效性」那一半的证据）、不挖新信号、LLM 不碰数值。

## 结论

**待跑数**（预注册落档 2026-10-09）。结局四态读法（写死）：**candidate**
（C1~C4 全过 ⇒ C5 pre2019 单独终步，owner 拍板发令）；**falsified**
（C2 不过或 C4 confirmed_fail ⇒ 计划规则按证伪归档——position_plans
维持影子层不并轨）；**untested**（C1 样本不足=不可判，不判 falsified
——样本不足≠否定证据，v0.301 哲学同族）；**provisional**（C4 池未满）。
⚠️ 即使 candidate 成立，**绝对 margin 读数也不进 live 决策**（幸存者
宇宙抬高绝对读数，相对结论才有效——R11/R14 同族声明）；并轨是独立
提交、只动 SIGNAL_ORDER，且 default 来源永远不出 plan 信号（v0.310
已是代码现实）。

---

## 证据与过程

### 前置核实（写死，2026-10-09 已核）

**stop_loss_ref 的 as-of 可重算性**（owner 方案 D 的前置条件）：`stop_loss_ref`
由 `pipeline/screening/enrich_candidates.py:784` 产出 =
**近 STOP_LOOKBACK 日最低价**（常量单源 `core/factors/b1_structure.py`），
是信号日的 rolling min——**只用 ≤ 信号日的数据，无未来数据**，可在任意
历史信号日 as-of 重算 ⇒ 本单元可行。若该前提日后被破坏（产出口径改成
含未来数据），本单元中止并向 owner 报告，不硬做。

### 实验单元（写死）

- **入场信号集**：V0 + j_low + 0AMV 多头区间（与 R36/R37/R39 同基底，
  跨研究可比）；
- **出场·计划版**：止损价 = 入场信号日 as-of 重算的 stop_loss_ref
  （`core/factors/b1_structure._stop_ref(df, lookback)` 单源——近
  lookback 日最低价，lookback 默认 = STOP_LOOKBACK=10；触发=**全额
  清仓**（与影子判定的 P0 一致））；止盈 = scale_out_two_bull 快照口径
  （BBI 上方连续两根中大阳分批止盈——与现行版**同一条规则、同一
  scale_out_frac**，Δ 只来自止损）；候选 stop_loss_ref 缺失或
  ≥ 入场价的信号**两版同剔**（缺失≠用 default 兜底——default 止损
  =live −7% 线，不带新信息，v0.310 拍板；⚠️ stop ≥ entry 时引擎
  `_initial_stop` 会静默回退 stop_mode，必须工具层先剔，记
  n_missing / n_stop_ge_entry）；
- **出场·现行版**（v0.315 ①，owner 拍板）：现行 EXIT_RULES 启用规则
  只有三条——hard_loss −10% 清仓（P0）、loss_reduction −7% 减仓
  10~25%（P1）、scale_out_two_bull 分批止盈（P2）。引擎能用
  stop_mode="pct" + stop_pct 表达「全额止损」但**表达不了「−7% 减一
  部分仓」** ⇒ **主读数 = stop_mode="pct", stop_pct=10**（只忠实对应
  hard_loss；loss_reduction 表达不了，本注记即注明）；**副读数 =
  stop_pct=7**（相当于把 loss_reduction 当作全额出场——**只报告，
  不作判据**）；
- **两版共用写死**（v0.315 ②，owner 拍板）：`scale_out_frac=0.5`
  （研究侧无现成惯例，owner 拍板值；两版必须相同——止盈不是本单元
  的研究变量）；`bbi_exit_consec=2`（引擎默认，对应 live 的
  bbi_two_close_breach）；`stop_trigger="close"`（与影子判定的
  「现价 ≤ 止损价」一致）；`cost_bps=25`；
- **执行语义**：T+1/跌停停牌顺延沿用引擎既有口径（`backtest_factors`
  单源），两版同待遇；双窗 s3000 钉死宇宙（挖掘窗 2022-01-01~
  2024-07-31 / 判定窗 2024-08-01~2026-09-04，同 R37/R39/R40）；
- **目标函数 v2.1 口径**：margin 单量 + rdd **相对门**（参照=同窗同
  信号**现行版主读数**读数——事先固定不经过挑选；只相对排序，引用
  连带 R11 声明）。margin 在全部候选交易（collect_all）上算，信号集
  相同 ⇒ 精确配对。

### 判据（跑数前写死；R39 族沿用）

- **R41-C1（样本量）**：双窗 n_taken 各 ≥ 100（同族口径；**不过 ⇒
  untested 不判 falsified**，且优先于其他判决——样本不足误判成否定
  证据是 v0.299/v0.301 刚修过两次的错）；
- **R41-C2（晋级线）**：Δmargin（计划版 − 现行版）**双窗同向为正**，
  **且双窗都过 rdd 相对门**（v0.315 ③，owner 拍板：参照=同窗同信号
  现行版主读数；任一窗不过 ⇒ C2 不过、报告标 `rdd_gate_fail`——margin
  的提升如果是拿更差的回撤收益比换来的，不算真提升）。副读数：报告
  逐笔 Δret 的中位数与符号计数（同一批信号精确配对）；
- **R41-C3（灵敏度）**：lookback = round(10 × U(0.5, 1.5)) ×4 次（种子
  写死；经 `_stop_ref(df, lookback=)` 参数实现，v0.315 落地）；**每次
  扰动重算 stop → 重新剔除 → 两版都在新子集上重跑**（剔除集合变了，
  配对必须重新对齐——现行版不得沿用旧子集读数），C2 结论**零翻转**
  （R29/R34/R37 零翻转同族）；
- **R41-C4（随机对照/多重比较记账）**：**随机止损价臂** N=50——同
  信号集，每条臂给**每个信号独立抽** `entry × U[0.85, 0.99]` 作为
  stop_override（均匀覆盖计划止损的实测分布区间——报告必给止损
  距离（entry/stop − 1）p5/p25/p50/p75/p95 分位数供核对覆盖；臂级
  种子写死可复现），其余同计划版。**预算对等 = 两边都不挑选**
  （v0.315 ④，owner 拍板）：计划版是单一配置未经网格挑选，随机臂
  也不做挑选——池元素 = 这条臂在挖掘窗的 Δmargin（vs 同窗现行版），
  勿照字面去「取 max」。臂同样套 rdd 相对门（同参照），过不了门的
  臂不进池、记过门率；**N=50 指过门臂数**（v0.317 修订，跑数前补记
  合规）：**重抽直至过门臂数满 N 或评估数达上限 max_arms = 10×N**——
  「抽 N 次」实现下过门率 <100% 就永远 provisional（owner 零假设实测
  过门 ~20%，N=50 与 min_pool=50 结构性撞死）；上限仍未满 ⇒ 按当时
  池大小判 provisional/indeterminate，如实记过门率与评估数。统计
  含义不变：候选本身也须过门（C2），零假设 =「同样过了门的随机臂」，
  两边条件对称——只改凑齐 N 的方式，不改判据。**池为空 ⇒
  indeterminate 不放行**（v0.297 族）；
  计划版挖掘窗 Δ > 池 q95（`exit_campaign._q95` 单源）且池 ≥50 ⇒
  confirmed_pass；池未满 ⇒ provisional；
- **R41-C5（终审）**：pre2019 untouched 段（2010-2016）单独终步，
  **只能杀不能确认**；C2~C4 全过才启动，owner 拍板发令，判据数值
  跑数前再锁死（原则写死在此）。

### 写死的风险注记（判读时逐条核对）

1. **stop_loss_ref 的语义迁移是研究对象本身**：它是「选股时该票值
   不值得买」的参照价（近 N 日最低），直接当「持仓期止损价」用是
   一次语义迁移——本单元就是验这个迁移成不成立，判负不意外；
2. **幸存者偏差（R14）**：s3000 按当前上市选，绝对读数被抬高——
   只有配对 Δ 与随机臂对照有效，绝对 margin 不进 live 决策；
3. **计划止盈的执行落差**：scale_out_two_bull 的「分批」在回测里按
   机械档执行，与人工分批的实盘手感不同——判读止盈侧结论时按
   「机械近似」对待；
4. **缺失剔除的生存者面**：stop_loss_ref 缺失的信号被剔除，子集
   与全集的口径差在报告里如实给（n_missing 必报）。

### 工程落点

- **Phase 1（工具，`research/plan_rules_replay.py`，v0.315 口径定稿后
  落地）**：骨架照 `factor_exit_study` 结构——
  ① 引擎最小钩子（`backtest_factors.py:3601`）：信号重放循环
  `stop_ov = cand["stop_override"] if "stop_override" in cand else
  _platform_stop_override(...)`——键缺省时 R37/R39/R40 全部逐位不变
  （钉测锁死）；② `_stop_ref(df, lookback=STOP_LOOKBACK)` 参数化
  （L2，live 同函数——默认参数逐位不变钉测；as-of 只读
  `df.iloc[:i+1]` 钉测）；③ warm 复用 `exit_campaign.warm_v0_signals`
  （单源）→ `attach_plan_stops`（逐信号 as-of 算 stop_override + 剔除
  记账 n_missing/n_stop_ge_entry，两版同剔保配对）→ replay 复用
  `factor_exit_study.replay_signals`；④ 读数 `combine_readings`：现行
  版自身 ref="self"，计划版/随机臂/C3 臂 ref=同窗现行版主读数；
  ⑤ C1~C4 机械读数 + 四态结局（次序同 R39/R40）；⑥ CLI 护栏照搬
  `factor_exit_study._check_windows`（pre2019 硬拒绝）+ 
  `exit_c5_terminal.check_reach(count, mining_start)`（逐股加载不经
  批量截断护栏——R39/R40 工具同补）；空结果护栏（任一窗口子集 0
  信号 ⇒ 非零退出不写产物）；产物自含（口径常量/剔除记账/止损距离
  分位数/两版读数/C3 抽样/C4 池——C5 终端要从产物自读）；TOOLS
  登记 + AGENTS.md §5 同步。
- **Phase 2（跑数）**：生产机双窗（命令随 Phase 1 落，owner 发令）。
- **Phase 3（C5）**：C2~C4 全过才启动，owner 拍板发令。

### 回填区（逐 Phase 填）

- **预注册落档（2026-10-09，v0.312）**：判据 C1~C5 写死；前置核实
  结论在案（stop_loss_ref 可 as-of 重算无未来数据）；R38 号被
  live-only 出场回测化预留，本单元取 R41。
- **口径定稿（2026-10-09，v0.315，owner 拍板四项）**：①现行版主读数
  pct10/副读数 pct7（不作判据）；②scale_out_frac=0.5 两版共用写死
  （bbi_exit_consec=2 / stop_trigger="close" / cost_bps=25 同写死）；
  ③rdd 相对门任一窗不过 ⇒ C2 不过标 rdd_gate_fail；④C4 预算对等
  =两边都不挑选（池元素=臂挖掘窗 Δmargin；臂不过门不进池记过门率；
  池空=indeterminate）。
- **Phase 1 工程（✅ 2026-10-09，v0.316）**：引擎信号级 `stop_override`
  钩子（`backtest_factors.py:3601`，缺省逐位不变钉测）+ `_stop_ref(df,
  lookback=)` 参数化（L2，默认逐位不变钉测）+ `research/plan_rules_replay.py`
  全栈（attach 配对剔除记账/两版读数/C1~C4 机械读数/四态结局/CLI 护栏
  pre2019 硬拒绝 + check_reach 到达校验——factor_exit_study /
  bear_regime_study 同补 check_reach）。钉测 +17（owner 指导 §5 清单
  九项全覆盖）；TOOLS 登记 `plan_rules_replay`，AGENTS.md §5 同步。
  Phase 2 生产机跑数待发令：`uv run python -m custos.research
  plan_rules_replay --tag r41_a1 --codes-file <s3000 钉死宇宙>
  --mining-start 2022-01-01 --mining-end 2024-07-31 --judgment-start
  2024-08-01 --judgment-end 2026-09-04`。
- **C4 口径修订（2026-10-09，v0.317，owner review）**：「N=50 指过门
  臂数」——过不了 rdd 门的臂不进池，「抽 50 次」实现下过门率 <100%
  就永远 provisional（owner 零假设 6 种子实测过门 9~14/50；R39 臂含
  全桶 P1 映射=参照档本身、R40 臂 trail08 档自参照，均不受影响，R41
  单配置+外部参照独撞此坑）。改重抽至过门臂满 N 或评估达上限
  max_arms=10×N，上限未满按当时池大小判 provisional/indeterminate，
  如实记过门率与评估数；统计含义不变（候选也须过门 C2，零假设=同样
  过了门的随机臂，两边条件对称）。另注明：LIVE_PARAMS 未合并
  exit_genome.FIXED_PARAMS（两版同用 evaluate_trades 默认值，配对内部
  口径一致；与 R37/R39 基准档非逐位相同，横向对比前需统一）。
