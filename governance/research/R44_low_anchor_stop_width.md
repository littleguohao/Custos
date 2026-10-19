# R44 · J 日最低价锚定的止损宽度寻优（0AMV 做多区间）（预注册）

> **家族**：交易管理（止损）　|
> **证据等级**：L2（预注册；参数网格寻优 + 双挖掘段选档 + 判定段攒批只读一次）　|
> **状态**：📋 **预注册落档（2026-10-15）**，判据跑数前写死；功效节数值待 #92 基线
> 读出后回填；工具 ✅（v0.379，TODO #100）；先验注记跑数前修订（2026-10-15）　|
> **依赖**：上游：R10（5% 是崖、买入日最低价锚定 tick_3 最优、low × 0.97 与 ATR 余量
> 否决、A 组 risk_frac 中位 0.65% 实盘不可执行）R12（B 组 R 比较口径教训）R41（近 10 日
> 最低价当止损证伪——太近）R9（盈亏比方法论）｜取代：TODO #97（计划止损 + 5% 下限 =
> 本网格 x=5 一档）｜约束：R11（绝对读数不作数）R14（幸存者宇宙）｜判据纪律：R12、
> research README「研究纪律」（v2 窗口、判定段攒批协议）
> 索引与主图见 [`README.md`](README.md)。

## 主题

J<13 信号日已是超跌极限位，不再另找结构低点（owner 2026-10-15）：止损价直接锚在 **J 信号日
最低价**，向下留 x 的空间——**止损价 = 信号日最低价 × (1 − x)**。在 5%~20% 内找让交易结果
更优（更高的胜率 × 盈亏比组合）的 x。

## 目标

在 0AMV 做多 + J<13 基底、止盈 / 清仓规则固定为现行口径的条件下，回答：

1. 哪个 x 让 margin（胜率与盈亏比的合成）最高，且回撤收益比不劣于现行止损；
2. 这个最优档在两段挖掘窗上是否一致、邻档是否平滑（不是孤立尖峰）；
3. 冻结的单档在判定段 2017~2021 上是否仍胜过现行止损。

## 结论

**📋 预注册，尚无读数。**

- **机械判决**：待读数（`criteria_kit.verdict_four_state` 四态）。
- **实质读数**：暂无。
- **不能读成什么**：①最优 x 只在**现行止盈 / 清仓规则下**成立（三维纪律：止损单维寻优，
  换止盈要重测）；②R 倍数口径的读数（expectancy_R）**不能跨档比较**——R 的定义就是止损
  距离，宽止损的 R 被系统性压低；③x 越宽仓位越小（固定单笔风险时），单笔收益高不等于
  组合更好，以组合层读数为准；④绝对收益不代表实盘（R11 / R14）。

---

## 证据与过程

### 实验单元（写死）

- **基底**：0AMV 做多 + J<13 信号（`_amv_checker` + j_low，同 R36~R42 基底）；入场 = 信号日
  收盘；宇宙 = 研究宇宙单源（全量个股，信号日 as-of 满 60 根）。
- **止损**：stop_override = 信号日最低价 × (1 − x)，**x ∈ {5, 6, 7, 8, 10, 12, 15, 20}%**
  共 8 档（R41 引擎信号级 `stop_override` 钩子，逐信号显式携带）。
- **对照（参照档）**：现行止损 = stop_mode="pct", stop_pct=10（忠实 `hard_loss` −10%，与
  R41 现行版同口径；`loss_reduction` −7% 引擎表达不了，只作副读数 pct7）。
- **其余出场固定**：两阳分批止盈（scale_out_frac=0.5）、BBI 两连破清仓（bbi_exit_consec=2）、
  stop_trigger="close"、cost_bps=25——各档与参照档完全相同，Δ 只来自止损。
- **配对**：各档与参照档在同一信号集上 collect_all 重放（精确配对）；信号日最低价 × (1−x)
  ≥ 入场收盘的信号（理论上不可能，防御性检查）两边同剔并记数。
- **窗口**（v2）：选档只用挖掘 A ∪ 挖掘 B（各段末隔离带 `clip_entries`，段前预热 300 根）；
  选出单档冻结后入判定段第一批（攒批协议，判定线 q(1−0.05/m)）。

### 读数口径（写死）

- **主判据量**：`margin`（收益率口径的胜率 × 盈亏比合成，`strategy_grid._margin`——不是 R
  口径）；**rdd 相对门**：ret_over_dd(候选档) ≥ ret_over_dd(参照档 pct10)（objective v2.1）。
- **必报读数**：胜率、盈亏比（均盈 / 均亏，收益率口径）、平均每笔收益、止损触发占比、
  平均持有天数、组合层 CAGR / 最大回撤 / 收益回撤比（固定 `--risk-pct 1` 单笔风险：止损越宽
  仓位越小，组合层读数才是同风险预算下的比较）。
- **禁止作判据**：expectancy_R（跨档不可比，见「不能读成什么」②）。

### 功效（跑数前回填）

- 输入：#92 基线重测给出的两段挖掘窗信号数 n、基线胜率 wr、档间配对相关 ρ_pair（同信号集
  不同止损，ρ 高）、日簇 deff；
- `power_mde` 估 Δmargin 的 MDE；**跑数前规则（写死）**：MDE > 2pp ⇒ 网格合并为
  {5, 8, 12, 20} 四档再估，仍 > 2pp ⇒ 本单元 untested 不跑。
- **回填（2026-10-17，#92）**：8 档共用同一信号集（0AMV 做多窗 j_low），n：A=78,470 /
  B=119,380（r4_seg{A,B}_amv 的 trade_summary.n；参照档 n_taken 同样 ≫100，C1 无虞）；
  基线胜率 A 0.4415 / B 0.4530；档间配对=同信号换止损宽度，ρ_pair 取 0.75（power_mde
  同信号配对推荐值）；`power_mde --wr 0.44 --n 78470 --rho 0.75`：MDE ≈ **0.35pp**
  （deff=1 下界）/ ≈ 0.79pp（deff=5 保守）；B 窗 n 更大、MDE 更小。均 ≪ 2pp ⇒
  **维持 8 档网格，不合并、不触 untested**。

### 判据（跑数前写死）

- **C1 样本**：两段挖掘窗各自参照档 n_taken ≥ 100。
- **C2 存在更优档**：选档 x* = 两窗合并 margin 最高且过 rdd 门的档；要求 x* 在**两窗各自**
  Δmargin（x* − 参照）> 0 且过 rdd 门。无档过门 ⇒ falsified（现行 pct10 不劣于全部候选）。
- **C3 稳健**：①平滑——x* 的相邻两档（端点档取单侧）两窗 Δmargin 均 > 0；②选档一致——
  两窗各自的最优档与 x* 相差不超过一档。任一不满足 ⇒ C3 不过（结论最高 provisional）。
- **C4 选择效应（criteria_kit 单源）**：随机止损臂 N=200（N 指过门臂数，重抽上限 10×N）：
  每臂抽 8 组、**每组一个固定宽度** x ~ U[5%, 20%] 作用于全部信号（与候选同锚点、同宽度范围；
  v0.408 跑数前修订——原「每个信号独立抽 x」把宽度系统效应在臂内平均掉，只要宽度对收益单调候选就恒过线，
  审计 2 P0-3），**取 max**（与 8 档网格同预算），池元素 = 臂的合并 Δmargin；x* 的合并 Δmargin > q95 ⇒
  confirmed_pass。——检验「某个固定宽度」是否比「同范围内随机宽度挑最好的」更好。
- **判定段池（v0.391 跑数前修订，owner 选 B）**：入批读数时同一次运行在判定段重抽随机宽度臂（每臂一个固定 x~U[5%,20%]、不取 max——候选冻结单档、臂冻结「策略」，两边都不在判定段挑选；v0.408 同步修订），N=200 过门臂，池元素 = 臂判定段 Δmargin；判定线 = `judgment_batch.judgment_line`（m 来自名册）。命令：`plan_rules_replay --stop-anchor signal_low --stop-buffer-pct <x*>`（v2 缺省布局）。
- **结局**：C1~C4 全过 = candidate ⇒ x* 冻结配置入判定段第一批；判定段读数过收紧后的判定线
  才可进 live 拍板（改 `EXIT_RULES.json` 是独立提交）。

### 写死的风险注记（判读时逐条核对）

1. **宽止损与 BBI 清仓的交互**：x ≥ 12% 时多数亏损单会先被 BBI 两连破清掉，止损很少触发，
   各宽档读数趋同——报告止损触发占比，读到趋同时不得读成「宽止损无害」；
2. **跳空穿越**：止损按收盘触发（stop_trigger="close"），跳空低开时实际成交低于止损价，
   宽档受影响更小——读数偏向宽档的程度用平均穿越幅度标注；
3. **先验**：R10 旧窗 B 组 pct5 > pct8 > pct12（越紧越好），但 5% 以下是崖；新宇宙 /
   新窗口重测，先验不作判据；**跑数前修订（2026-10-15，判据未改）**：上句只取了 R10 主窗，
   全貌是 5%~12% 期望相差 ≤6%（平台），且跨窗 2022~2024 叠 0AMV 后 5% 档无优势（pct_08_amv、
   pct_12_amv_cz3 居前）⇒ 先验应为「平台 + 最优档对窗口敏感」，x* 落在 8~12% 或两窗选档
   不一致都不意外——C3 选档一致条就是为此设的；
4. **open_end**：段末隔离带已压，残余 open_end 交易数逐窗入报告（R11 偏正）。

### 工程落点（TODO #100，coder）

- ✅ 工具已落地（v0.379，`plan_rules_replay` 扩展：`--stop-anchor signal_low --stop-buffer-pct x`
  锚点 + `--scan-widths` 挖掘段 8 档扫描模式），跑数待生产机发令。

- `plan_rules_replay` 扩展止损锚点模式：`--stop-anchor signal_low --stop-buffer-pct x`
  ⇒ stop_override = 信号日最低价 × (1 − x)（R41 原「近 N 日最低价」模式保留）；
- 加**只读挖掘段**的扫描模式（8 档 + 参照一次跑完、不读判定段、不过批次门），产出选档表与
  C2~C4；判定段读数用冻结单档、经批次门；
- C4 随机宽度臂（逐信号 U[5%,20%]、max-of-8）用 `criteria_kit.assemble_c4_pool`；
- 必接：零假设校准两连测（纯噪声 confirmed_pass ≤ ~10% + 植入「某宽度真更优」可识别）+
  回归哨兵（换成 max-of-1 随机臂，校准必须失败——抓选择效应）；provenance / cost_sensitivity。

### 回填区（逐 Phase 填）

<!-- digest:results/R44/r44_scan.json -->
**跑数摘要** `r44_scan`（kind=r44_scan，产物 `_plan_width_scan__r44_scan.json` sha256 `59cf58c7b94de0d2`，digest v1；数字只来自产物，勿手改）

| 字段 | 值 |
|---|---|
| `accounting` | {"mining_a": {"anchor": "signal_low", "n_anchor_ge_entry": 8845, "n_clipped": 20756, "n_missing": 0, "n_score_missing": 0, "n_signals": 198147, "n_subset": 189302, "x_pct": 0.0}, "mining_b": {"anchor": "signal_low", "n_anchor_ge_entry": 22223, "n_clipped": 11217, "n_missing": 0, "n_score_missing": 0, "n_signals": 403056, "n_subset": 380833, "x_pct": 0.0}} |
| `criteria.C1` | {"n_taken": {"mining_a": 865, "mining_b": 266}, "ok": true, "rule": "两段挖掘窗各自参照档 n_taken ≥ 100（不过=untested）"} |
| `criteria.C2` | {"merged_delta_margin": 0.0011302478143200845, "merged_margin": 0.06068241353936715, "ok": false, "passing_rdd_merged": [5.0, 6.0, 7.0, 10.0, 15.0, 20.0], "rule": "x*=两窗合并 margin 最高且过 rdd 门的档；x* 两窗各自 Δmargin>0 且过门", "windows": {"mining_a": {"delta_margin": 0.00018327188606948042, "ok": true, "rdd_gate": true}, "mining_b": {"delta_margin": 0.0015335078485758658, "ok": false, "rdd_gate": false}}, "x_star": 7.0} |
| `criteria.C3` | {"ok": false, "rule": "①x* 相邻两档（端点取单侧）两窗 Δmargin 均>0；②两窗各自最优档与 x* 相差 ≤1 档；任一不过 ⇒ C3 False（结局最高 provisional）", "smooth": {"x6": {"mining_a": -0.0009753452631369175, "mining_b": 0.0016548328843961069}, "x8": {"mining_a": 0.0014893814997263277, "mining_b": 0.0013060330157822486}}, "window_best": {"mining_a": 20.0, "mining_b": 10.0}, "x_star": 7.0} |
| `criteria.C4` | {"arm_draws": 8, "evaluated": 200, "gate_pass": 200, "gate_pass_rate": 1.0, "max_arms": 2000, "min_pool": 20, "note": "随机宽度臂：逐信号 x~U[5%,20%]（同锚点），每臂抽 8 组取 max（与 8 档网格同预算）；池元素=臂两窗合并 Δmargin；N 指过门臂数（criteria_kit.assemble_c4_pool 单源）", "plan_delta_merged": 0.0011302478143200845, "pool_size": 200, "q95": 0.0014762561148931042, "state": "confirmed_fail", "target_pool": 200} |
| `merged.ref.margin` | 0.05955216572504707 |
| `merged.ref.n_taken` | 1131 |
| `merged.ref.rdd_gate` | true |
| `merged.ref.stop_share` | 0.17070693783051383 |
| `merged.x10.margin` | 0.06037186311787068 |
| `merged.x10.n_taken` | 1105 |
| `merged.x10.rdd_gate` | true |
| `merged.x10.stop_share` | 0.14781762214212424 |
| `merged.x12.margin` | 0.05834862987263606 |
| `merged.x12.n_taken` | 1089 |
| `merged.x12.rdd_gate` | false |
| `merged.x12.stop_share` | 0.11631981899023916 |
| `merged.x15.margin` | 0.05524313725490193 |
| `merged.x15.n_taken` | 1044 |
| `merged.x15.rdd_gate` | true |
| `merged.x15.stop_share` | 0.08294526734896121 |
| `merged.x20.margin` | 0.05175228992433295 |
| `merged.x20.n_taken` | 1018 |
| `merged.x20.rdd_gate` | true |
| `merged.x20.stop_share` | 0.04973383496891087 |
| `merged.x5.margin` | 0.06033918252290349 |
| `merged.x5.n_taken` | 1181 |
| `merged.x5.rdd_gate` | true |
| `merged.x5.stop_share` | 0.29154147701860084 |
| `merged.x6.margin` | 0.06042832369942197 |
| `merged.x6.n_taken` | 1178 |
| `merged.x6.rdd_gate` | true |
| `merged.x6.stop_share` | 0.252243766827155 |
| `merged.x7.margin` | 0.06068241353936715 |
| `merged.x7.n_taken` | 1151 |
| `merged.x7.rdd_gate` | true |
| `merged.x7.stop_share` | 0.21834653196172837 |
| `merged.x8.margin` | 0.06090484893696385 |
| `merged.x8.n_taken` | 1132 |
| `merged.x8.rdd_gate` | false |
| `merged.x8.stop_share` | 0.19016022520981873 |
| `provenance.cmdline` | "--scan-widths --tag r44_scan" |
| `provenance.data_last_date` | "2026-09-04" |
| `provenance.git_dirty` | true |
| `provenance.git_sha` | "af920b8cd1" |
| `provenance.n_codes` | 5576 |
| `provenance.universe_rule` | "ashare_full_asof" |
| `provenance.universe_sha256` | "f0692547ad66f1b2" |
| `verdict` | "falsified" |
| `window_usage` | {"k": null, "note": "扫描模式只读挖掘 A∪B——判定段不读、批次门不触发（v2 纪律）", "window": "not_read"} |
| `windows` | {"mining_a": ["2000-01-01", "2016-12-31"], "mining_b": ["2022-01-01", "2026-09-04"]} |
<!-- /digest -->


（空）
