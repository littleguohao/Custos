# R41 · 持仓计划规则离线回放（计划止损 stop_loss_ref + 计划止盈 vs 现行 EXIT_RULES）（预注册）

> **家族**：交易管理（出场轴）× live 机制研究　|
> **证据等级**：L2 / L3−（双窗纪律 + 幸存者宇宙 + 多重比较显式标注——
> 与 R10/R36/R37/R40 同纪律）　|
> **状态**：📋 **预注册落档（2026-10-09，v0.312）**，判据跑数前写死；
> Phase 1 工具与生产机跑数均待 owner 发令　|
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
  （近 STOP_LOOKBACK 日最低价，触及/跌破即出）；止盈 =
  scale_out_two_bull 快照口径（BBI 上方连续两根中大阳分批止盈——
  与 `_plan_shadow` 的判定语义一致）；候选 stop_loss_ref 缺失的信号
  **剔除**（缺失≠用 default 兜底——default 止损=live −7% 线，不带
  新信息，v0.310 拍板）；
- **出场·现行版**：EXIT_RULES 现行规则族（live 正在用的那套，单一
  真源 `governance/contracts/EXIT_RULES.json`）；
- **执行语义**：T+1/跌停停牌顺延沿用引擎既有口径（`backtest_factors`
  单源），两版同待遇；双窗 s3000 钉死宇宙（挖掘窗 2022-01-01~
  2024-07-31 / 判定窗 2024-08-01~2026-09-04，同 R37/R39/R40）；
- **目标函数 v2.1 口径**：margin 单量 + rdd **相对门**（参照=同窗同
  信号**现行版**读数——事先固定不经过挑选；只相对排序，引用连带
  R11 声明）。

### 判据（跑数前写死；R39 族沿用）

- **R41-C1（样本量）**：双窗 n_taken 各 ≥ 100（同族口径；**不过 ⇒
  untested 不判 falsified**——样本不足误判成否定证据是 v0.299/v0.301
  刚修过两次的错）；
- **R41-C2（晋级线）**：Δmargin（计划版 − 现行版）**双窗同向为正**；
- **R41-C3（灵敏度）**：stop_loss_ref 的 lookback（STOP_LOOKBACK）
  ±50% ×4 扰动臂（吸附整数档），C2 结论**零翻转**（R29/R34/R37 零翻转
  同族）；
- **R41-C4（随机对照/多重比较记账）**：**随机止损价臂**——同信号集、
  止损价从「入场价 × U[0.85, 0.99]」独立重抽（均匀覆盖计划止损的
  实测分布区间，种子写死），其余同计划版；N=50 臂取 max 建池，计划版
  挖掘窗 Δmargin > 池 q95；池 ≥50 才 confirmed，未满 provisional 不停
  （v0.266/v0.297/v0.299 判据族沿用）；随机臂同样过 rdd 相对门（同
  参照，口径对称）；
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

- **Phase 1（工具）**：`research/plan_rules_replay.py`（新终端）——
  信号集复用 V0 预热族（`exit_campaign.warm_v0_signals` 单源），
  stop_loss_ref as-of 重算（rolling min of lows，STOP_LOOKBACK 单源
  引用不复制常量），双版出场重放（引擎执行语义单源），随机止损价
  臂（N=50），判据 C1~C4 机械读数；空结果护栏 + pre2019 硬拒绝镜像
  + TOOLS 登记。判据结构同族测试复用 R39/R40 套件惯例。
- **Phase 2（跑数）**：生产机双窗（命令随 Phase 1 落，owner 发令）。
- **Phase 3（C5）**：C2~C4 全过才启动，owner 拍板发令。

### 回填区（逐 Phase 填）

- **预注册落档（2026-10-09，v0.312）**：判据 C1~C5 写死；前置核实
  结论在案（stop_loss_ref 可 as-of 重算无未来数据）；R38 号被
  live-only 出场回测化预留，本单元取 R41。
