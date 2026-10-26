# R45 · 0AMV 空头区间 J<13 单门 × 30% 仓位上限 × 出场网格（预注册）

> **家族**：择时 × 交易管理（0AMV 空头区间）　|
> **证据等级**：L2（预注册；单门假设验证 + 16 格出场网格 + 双挖掘段选格 + 判定段攒批只读一次）　|
> **状态**：❌ **挖掘段预演判毕 falsified（2026-10-26 复核，r45_v2_pre，A 选 B 验）**——C2 打不过「不交易」（两窗组合总收益均为负）、C3 邻档与最优止盈不稳；
> 不入第一批判定段；预注册落档 2026-10-15，工具 v0.397 `bear_regime_study --r45`，跑数前修订 v0.397 在案　|
> **依赖**：上游：R40（同面探索：46 门 × 5 档；本单元是其中 j_low 单门的具体假设）R44（信号日
> 最低价锚定止损、随机宽度臂构造）R10（5% 是崖、成本区截大亏在择时上下文有效、5%~12% 平台）
> R4（0AMV 区间定义）｜live 侧防守教义：`governance/strategy/b1/03_execution_discipline.md`
> （空头区间只卖不买 + 反弹减仓）｜约束：R11（绝对读数不作数）R14（幸存者宇宙）｜判据纪律：
> R12、research README「研究纪律」（v2 窗口、判定段攒批协议）
> 索引与主图见 [`README.md`](README.md)。

## 主题

owner 2026-10-15 提出的假设方案：0AMV 空头区间里**仍按 J<13 入场**，但**总仓位不超过 30%**，
止损与止盈多方案比较。问的是「现行『空头区间不买』能不能被一个受控的小仓位做多方案打败」。
与 R40 的区别：R40 在 46 个门里挑最好的（选择效应大，靠 max-of-230 随机臂对冲）；本单元
入场门只有一个、事先指定，选择只发生在 16 格出场上。

## 目标

机械验证：空头区间 j_low 入场 + 30% 总敞口上限下，是否存在一格出场配置在两段挖掘窗都
打过「不交易」（现行防守规则）与「同约束随机入场」两个基准。非目标（写死）：不动 live
（空头区间不买仍是现行规则，任何回流走立项 + 影子观察）；不搜入场门；不调 30% 上限。

## 结论

**❌ 挖掘段预演判毕 falsified（2026-10-26，r45_v2_pre，全量 as-of 宇宙 5,579 只）。** 不入第一批，判定段不读。

- **机械判决**：C1 ✓（组合成交 A 1,070 / B 315 笔）；**C2 ✗**——margin 两窗为正（A +5.9pp / B +0.14pp）且都高于随机臂 q95，
  但 `beats_no_trade=false`：top 格 x15_T3 在 30% 敞口下两窗组合总收益 A −33.7%（最大回撤 52.7%）/ B −19.6%（回撤 26.4%）；
  **C3 ✗**——止损邻档 x12_T3 两窗不同为正，两窗最优止盈不一致（A=T3 / B=T1）；C4 confirmed_pass（只说明 A 窗打过随机入场，救不回 C2）。
- **实质读数**：①全候选层面 B 窗期望 +0.02%/笔，几乎为零，A 窗 +0.9%/笔；②**全候选 margin 为正、V0 选中的 3 仓组合却亏钱**——
  每日 top-N 排序选到的是更差的那部分，与 R42（最高档不是最好）/ R46（最优格 = 不打分）同向；③出场结构：约 45% 交易被成本区止损
  截在 −4% 附近（胜率 8%），约 50% 由 BBI 出场赚 +5~7%（胜率 70%），硬止损只占 2.5% 却平均 −19%；④最优参数随窗漂移，没有稳定配置。
  ⇒ 空头区间按 J<13 做多（即便限仓 30%），现有出场下打不过现行「空仓防守」。
- **不能读成什么**：①打过随机入场只说明 j_low 在空头区间有相对信息，不说明该做多——还要
  打过「不交易」；②30% 上限下的组合读数不能外推到满仓；③最优格只在本出场网格内成立；
  ④绝对收益不代表实盘（R11 / R14），空头区间的幸存者偏差尤其偏乐观（跌没了的票不在宇宙里）。

---

## 证据与过程

### 实验单元（写死）

- **入场**：0AMV 有效状态 = 空头（`_amv_checker` 反转，同 R40）∧ J<13（j_low）；入场 = 信号日
  收盘；宇宙 = 研究宇宙单源（全量个股，信号日 as-of 满 60 根）。
- **仓位**：组合层总敞口 ≤ 30%：`--max-concurrent 3 --max-pos 10`（单仓名义 ≤10%，同时 ≤3 只），
  `--risk-pct 1`；满仓位时新信号按 V0 分排序取前，余下丢弃（同引擎既有口径）。
- **出场网格 4 × 4 = 16 格**：
  - 止损（信号日最低价锚定，同 R44）：x ∈ {5, 8, 12, 15}%；
  - 止盈 / 清仓：T1 现行（两阳分批 0.5 + BBI 两连破清仓）／T2 trail 8%（无分批）／
    T3 成本区不涨就拍（cz3，R10 口径）+ BBI ／T4 时间止损 10 日 + BBI；
  - stop_trigger="close"，cost_bps=25，各格共用同一信号集（精确配对）。
- **窗口**（v2）：只读挖掘 A（起点 = SEG_A_START，#91 定）与挖掘 B；段末隔离带 `clip_entries`，
  段前预热 300 根；选出单格冻结后入判定段批次。

### 读数口径（写死）

- **主判据量**：`margin`（收益率口径）+ 组合层 `total_return`；rdd 相对门：ret_over_dd(候选) ≥
  ret_over_dd(随机入场臂中位)——空头区间做多绝对 rdd 普遍为负，绝对门会全灭（R40 v2.1 教训）。
- **必报**：胜率、盈亏比、平均每笔收益、止损/止盈/时间/BBI 各出场占比、平均持有天数、
  组合层 CAGR / 最大回撤 / 收益回撤比、空头区间日数与信号数（逐段）、跌停次日不可卖次数。
- **禁止作判据**：expectancy_R（跨止损档不可比，同 R44）。

### 功效（跑数前回填）

- 输入：#91 给出 0AMV 台账起点与两段挖掘窗的空头区间日数；#92 给出 j_low 基线信号密度；
  由此估两段空头区间信号数 n、30% 上限下的实际成交数 n_taken、日簇 deff；
- `power_mde` 估 Δmargin 的 MDE；**跑数前规则（写死）**：任一挖掘段预估 n_taken < 100 ⇒
  本单元 untested 不跑（不放宽仓位上限去凑样本）。
- **回填（2026-10-17，#91/#92）**：0AMV 空头段 A=135 段/2625 日、B=24 段/793 日（台账状态机
  + `split_regime_segments`）；j_low 信号密度 A≈69.1/日、B≈241.3/日 ⇒ 空头日信号
  A≈18.1 万 / B≈19.1 万（候选极充足，瓶颈在仓位上限不在信号）；30% 上限（3 并发×10%）下
  n_taken 为容量约束，按平均持有 10~15 日估 A≈530~790 / B≈160~240——**下限估也 > 100 ⇒
  不触 untested 线**（实际 n_taken 以跑数为准）。

### 判据（跑数前写死）

- **C1 样本**：两段挖掘窗各自候选格 n_taken ≥ 100；不过 ⇒ untested（不判 falsified）。
- **C2 打过不交易**：选格 g* = 两窗合并 margin 最高、且过 rdd 相对门的格；要求 g* 在**两窗各自**
  margin > 0 **且**组合 total_return > 0（不交易 = 0）。无格满足 ⇒ falsified（现行防守规则不劣）。
- **C3 稳健**：①g* 的止损邻档（同止盈、相邻 x）两窗 margin 均 > 0；②两窗各自最优格的止盈
  方案与 g* 相同。任一不满足 ⇒ 最高 provisional。
- **C4 打过随机入场（criteria_kit 单源）**：随机入场臂 N=200（过门臂数，重抽上限 10×N）——每臂
  在同一批空头日内按候选的逐日信号数随机抽 (code, bar) 入场（无放回、整簇抽，簇定义
  `criteria_kit.cluster_ids`），同 30% 上限，**同 16 格取 max**（与候选同预算）；池元素 = 臂两窗
  合并 margin；g* 合并 margin > q95 ⇒ confirmed_pass。
- **结局**：C1~C4 全过 = candidate ⇒ g* 冻结入判定段批次（判定线按攒批协议收紧到
  q(1−0.05/m)，随机臂冻结配置读判定段建池，对等纪律同 R40 v0.302）；判定段过线也只进
  owner 拍板，改 live 是独立立项。

### 写死的风险注记（判读时逐条核对）

1. **与现行教义对赌**：现行最高优先级纪律是空头区间反弹减仓；C2 要求打过「不交易」就是这条
   教义的形式化，判负是默认预期；
2. **区间翻转脆弱**：空头区间段数少（台账起点后约十余段，#91 定），读数被少数长熊段主导——
   逐段报贡献，单段贡献 > 50% 时结论降一级；
3. **跌停不可卖**：引擎执行语义覆盖，但必须报跌停次日不可卖次数与该部分平均损失；
4. **与 R40 的关系**：r40_a1 的 j_low 行是本假设在旧口径（s3000、旧双窗、无仓位上限）的读数，
   出读数后只作背景、不改本网格与判据（网格写死在其之前）。

### 工程落点（TODO #103）与跑数前修订（v0.397）

- **工具**：`bear_regime_study --r45`（j_low 单门、16 格 `x{5,8,12,15}_T{1..4}`、组合口径 `R45_PORTFOLIO` =
  并发 3 × 单仓 10% × 单笔风险 1%；止损 = 信号日最低价 ×(1−x%)，止损 ≥ 入场收盘的信号剔除）。
- **修订 ①（rdd 参照）**：参照格改为事先固定的 **x8_T1**（现行止盈 × 中间档止损），不再用「随机入场臂中位」——
  与 R39/R40/R44 的 v2.1 纪律一致（参照档固定、不经挑选），且符号感知 rdd（v0.382）已解决空头窗口全负问题。
- **修订 ②（执行路径，同 R39/R40 两步走）**：①挖掘内预演——legacy 四参 `--mining-start SEG_A_START
  --mining-end 2016-12-31 --judgment-start 2022-01-01 --judgment-end 2026-09-04`（A 选格、B 验证，不触判定段）；
  C1~C4 全过才进②；②入批——v2 缺省布局加 `--print-config-hash` 冻结、开批后加 `--batch-id/--candidate-id` 重跑，
  判定段一侧走攒批判定线（冻结臂判定窗重读池，m 来自名册）。
- **修订 ③（C2/C4 落到代码）**：C2 = 两窗 margin>0 **且** 两窗组合收益>0（打过不交易）**且** 两窗 > 各自随机臂池
  q95；C4 = 挖掘窗 margin > 随机臂池 q95。随机臂 = R40 构造（同空头日母体无放回抽 n 个入场、同 30% 上限、同 16 格取
  max，冻结出场后判定窗按实际信号数重抽），不再要求逐日信号数对齐与整簇抽。
- **修订 ⑤（成簇随机入场臂（v0.399 跑数前修订，owner 2026-10-16）**：随机臂按候选门信号的簇形状抽（`kit.cluster_ids` 单源：同票 bar 间隔 ≤5 成簇；每簇独立抽一个空头日锚点，同票按偏移连续取，整体无放回），判定窗按冻结门在判定窗的簇形状重抽。原逐 bar 独立抽样在成簇零假设下实测假过线 26.7%（成簇臂 0.8%，`tests/test_bear_clustered_arms.py`）——判定线偏松。
- **修订 ④（C3）**：代码 `judge_c3_r45` = 止损邻档（同止盈）两窗 margin>0 + 两窗各自最优格止盈方案与 g* 相同，
  原文不变；T2（trail 8%）保留引擎缺省 BBI 两连破清仓（原文未写，按缺省落地）。

### 回填区（逐 Phase 填）

<!-- digest:results/R45/r45_v2_pre.json -->
**跑数摘要** `r45_v2_pre`（kind=r45，产物 `_bear_regime__r45_v2_pre.json` sha256 `bbe1af7dd86823f1`，digest v1；数字只来自产物，勿手改）

| 字段 | 值 |
|---|---|
| `criteria.C1` | {"judgment": {"n_taken": 315, "ok": true}, "min_n_taken": 100, "mining": {"n_taken": 1070, "ok": true}, "ok": true} |
| `criteria.C2` | {"beats_no_trade": false, "margin_judgment": 0.0014402025710947286, "margin_mining": 0.05904700036805299, "ok": false, "pool_ok_judgment": true, "pool_ok_mining": true, "pools_available": true, "pos_ok": true, "q95_judgment": -0.008156862745098081, "q95_mining": 0.019888514777403665, "rule": "margin 双窗同向为正 且 双窗>随机臂对应窗池 q95（基准=随机臂非空仓）"} |
| `criteria.C3` | {"best_tp_by_window": {"judgment": "T1", "mining": "T3"}, "neighbors": ["x12_T3"], "neighbors_positive": false, "ok": false, "rule": "止损邻档两窗 margin>0 且两窗各自最优格止盈方案与 top 相同（R45 预注册）", "tp_consistent": false} |
| `criteria.C4` | {"evaluated": 50, "gate_pass": 50, "gate_pass_rate": 1.0, "min_pool": 50, "note": "与 C2 挖掘半窗同池（重合是设计使然——R40 量级线与选择效应对的是同一个零假设）", "pool_size": 50, "q95": 0.019888514777403665, "state": "confirmed_pass"} |
| `criteria.r45` | {"exit_grid": ["x5_T1", "x5_T2", "x5_T3", "x5_T4", "x8_T1", "x8_T2", "x8_T3", "x8_T4", "x12_T1", "x12_T2", "x12_T3", "x12_T4", "x15_T1", "x15_T2", "x15_T3", "x15_T4"], "note": "R45 模式（v0.397）：j_low 单门 × 4 止损（信号日低点锚定）× 4 止盈；C2 另须两窗组合收益>0（打过不交易）；C3=止损邻档平滑+最优止盈一致", "portfolio": {"max_concurrent": 3, "max_pos_frac": 0.1, "risk_pct": 0.01}, "ref_cell": "x8_T1", "unit": "R45"} |
| `n_signals_by_gate` | {"judgment": {"j_low": 905702}, "mining": {"j_low": 962306}} |
| `pools.entry_accounting` | {"judgment": {"requested": 45285100, "returned": 45259222}, "mining": {"requested": 48115300, "returned": 48056125}} |
| `pools.q95_judgment` | -0.008156862745098081 |
| `pools.q95_mining` | 0.019888514777403665 |
| `provenance.cmdline` | "--r45 --tag r45_v2_pre --load-workers 4 --score-workers 8 --mining-start 2000-01-01 --mining-end 2016-12-31 --judgment-start 2022-01-01 --judgment-end 2026-09-04" |
| `provenance.data_last_date` | "2026-09-04" |
| `provenance.git_dirty` | false |
| `provenance.git_sha` | "5fc6d0b90e" |
| `provenance.n_codes` | 5579 |
| `provenance.universe_rule` | "ashare_full_asof" |
| `provenance.universe_sha256` | "6a24a9f13ee5ee1b" |
| `top.exit` | "x15_T3" |
| `top.extended.judgment.dist.by_reason` | "见子表 top.extended.judgment.dist.by_reason（6 行）" |
| `top.extended.judgment.dist.expectancy` | 0.00023 |
| `top.extended.judgment.dist.holding` | {"avg_loss": 5.811977, "avg_win": 14.378315} |
| `top.extended.judgment.n_unfillable` | 15 |
| `top.extended.judgment.portfolio` | {"cagr": -0.0469, "max_drawdown": 0.2641, "n_taken": 315, "return_over_maxdd": -0.74, "total_return": -0.1956} |
| `top.extended.mining.dist.by_reason` | "见子表 top.extended.mining.dist.by_reason（6 行）" |
| `top.extended.mining.dist.expectancy` | 0.009039 |
| `top.extended.mining.dist.holding` | {"avg_loss": 6.223375, "avg_win": 15.582984} |
| `top.extended.mining.n_unfillable` | 199 |
| `top.extended.mining.portfolio` | {"cagr": -0.0241, "max_drawdown": 0.5266, "n_taken": 1070, "return_over_maxdd": -0.64, "total_return": -0.337} |
| `top.judgment` | {"expectancy_R": 0.003, "margin": 0.0014402025710947286, "n": 896941, "n_taken": 315, "n_unrealized": 8761, "objective": 0.0014402025710947286, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 1.567, "rdd_gate": true, "ret_over_dd": -0.051657959999999996, "total_return": -0.1956, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.391} |
| `top.mining` | {"expectancy_R": 0.055, "margin": 0.05904700036805299, "n": 959125, "n_taken": 1070, "n_unrealized": 3181, "objective": 0.05904700036805299, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 1.717, "rdd_gate": true, "ret_over_dd": -0.1774642, "total_return": -0.337, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.4271} |
| `verdict` | "falsified" |
| `window_usage` | {"k": null, "note": "判定窗在挖掘段内（非受保护段 2017~2021）——读挖掘段不入台账（v2 口径）", "window": "mining_segment"} |
| `windows` | {"judgment": {"end": "2026-09-04", "start": "2022-01-01"}, "mining": {"end": "2016-12-31", "start": "2000-01-01"}} |

子表 `top.extended.judgment.dist.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 13.43 | 0.051 | 452432 | "bbi_exit" | 0.5044 | 0.7043 |
| 4.55 | -0.040595 | 408517 | "cost_zone_stop" | 0.4555 | 0.0762 |
| 7.57 | -0.194036 | 23678 | "stop" | 0.0264 | 0.0 |
| 5.92 | -0.202791 | 7005 | "stop_delayed" | 0.0078 | 0.0 |
| 16.66 | 0.026866 | 2687 | "bbi_exit_delayed" | 0.003 | 0.3238 |
| 5.71 | -0.129986 | 2622 | "cost_zone_stop_delayed" | 0.0029 | 0.0027 |

子表 `top.extended.mining.dist.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 14.59 | 0.065604 | 514473 | "bbi_exit" | 0.5364 | 0.7242 |
| 4.6 | -0.043875 | 388264 | "cost_zone_stop" | 0.4048 | 0.0836 |
| 7.75 | -0.184237 | 23729 | "stop" | 0.0247 | 0.0 |
| 5.72 | -0.212265 | 17456 | "stop_delayed" | 0.0182 | 0.0 |
| 19.45 | 0.079136 | 9822 | "bbi_exit_delayed" | 0.0102 | 0.4696 |
| 5.92 | -0.138691 | 5381 | "cost_zone_stop_delayed" | 0.0056 | 0.0013 |
<!-- /digest -->


- 无。
