# 因子 · 止损 · 止盈总览

> **上下文**：跨策略规则（不属于任何单一策略）　｜　**执行者**：代码（live 链每日消费）　｜　**状态**：live
> **版本**：2026-08-20 首版（v0.88）　｜　**代码依赖**：`core/factors/`（注册表）、`core/exit_rules.py`、`pipeline/screening/score_candidates.py`（契约 JSON：governance/contracts/EXIT_RULES.json 与 SCREEN_FORMULA_REGISTRY.json）
> **索引**：[`../README.md`](../README.md)　·　改动须记 [`CHANGELOG.md`](../../../CHANGELOG.md)
>
> ⚠️ **本文是人读索引，字段级真相以代码为准**：
> 因子 = `src/custos/core/factors/`（注册表强制登记，`tests/test_factor_registry.py` 钉住）；
> 止损/止盈 = `src/custos/core/exit_rules.py` + 覆盖层 [`contracts/EXIT_RULES.json`](../../contracts/EXIT_RULES.json)；
> 权重数值 = [`contracts/SCREEN_FORMULA_REGISTRY.json`](../../contracts/SCREEN_FORMULA_REGISTRY.json) 的 `scoring.weights`。
> **调任何分值/阈值/开关之前先回测**（同 cap_rules 纪律）。

## 核心思想（评价这一切的标尺）

**不追求胜率，追求「一定胜率基础上的更高盈亏比」，盈利通过优化止盈止损与仓位管理达成**
（owner 2026-08-19 定案，详见 [`system_principles.md`](system_principles.md) 核心原则第 0 条）。
推论：因子/选股只负责把票送进池子（研究已证选股无 alpha），**出场与仓位才是盈利杠杆**——
所以本文档里止损/止盈与因子并列，且出场侧规则的工程待遇（唯一来源、落盘计划、影子验证、
回流通道）不低于因子。

## 一、因子清单（注册表全量 31 个，按角色分组）

状态词表：`status` = active（已验证可用）/ candidate（在用但未经独立回测）/ untested / needs_work（按现有证据不可用）；
`live_use` = gate（门槛/否决）/ scorer（进打分/分层）/ evidence_only（只落盘展示）/ none（不进 live）；
`stage` = release（live 链引用）/ debug。机械约束：needs_work/untested 不得与 gate/scorer 共存（注册表测试强制）。

### A. live 打分/门槛因子（驱动 1800 选股结果）

| id | 名称 | live_use | 喂哪条腿 / 用途 |
|---|---|---|---|
| `j_low_gate` | J<13 进池硬门槛 | gate | 1800 唯一进池硬门槛（执行点 enrich `_apply_j_gate`） |
| `entry_patterns` | patterns 五单项判定 | scorer | 技术分：j_low **+24**（最大单项）、volume_contraction +15、relative_strength_strong +15、bbi_above +5、reversal_k_candidate +4 |
| `macd_technics` | MACD 十大技术 | scorer | 技术分：底背离 +8、水上 +7、zone1_restart +5、红柱增长 +5、周月红柱 +5、zone1 +3、**顶背离 −8**；cap：三打白骨精封顶 C |
| `volume_detectors` | 量能三件套 | scorer | 技术分：bottom_volume +10、leader_volume +6；资金意图证据；volume_sustain 主线确认（retreat 留痕，v0.60 起不封顶） |
| `b1_structure` | B1 结构检测器族 | scorer | 技术分：five_day_entry +8、repair 每项 +4（上限 +8）、non_one_wave confirmed +5；cap：non_one_wave revoked 封顶 C；liquidity 仅 flag；止损参考位 `_stop_ref` |
| `ignition` | 点火族 | scorer | 技术分：b1_ignition +8、pullback_shrink +5、ignition +4；资金意图证据；门内提醒判据（v0.89 起） |
| `weekly_j` | 周线 J 状态 | scorer | 技术分：weekly_j_low +5（周日共振） |
| `capital_intent` | 资金意图强度 | scorer | **分层第二轴**：9 条正向证据（ci_* 分值见 registry），≥5 强 / ≥2 中 / 否则弱 |
| `distribution` | 主力出货五方式 | gate | 技术分：watch **−10** / high **−20**；hits≥2 封 high；与 wave_type 同为 active 唯二 |
| `wave_type` | 前置拉升波分类 | gate | 冲刺波首个 B1 禁止买入（cap 层） |

配套（非因子注册但影响结果）：技术分组合器与分层矩阵在 `pipeline/screening/score_candidates.py`
（scorer 的家）；组合奖 `b1_healthy_pullback_pack` +9；趋势腿 adx>60 +5；阴阳量 volume_yy +7/−5
（检测器在 `bottom_patterns.bull_bear_volume`）；cap 编排 `apply_risk_downgrades`（6 条 +
无止损位→B、0AMV 空头→B 两条硬 cap）。

### B. live 证据层因子（落盘展示，不进分不进分层）

| id | 名称 | 备注 |
|---|---|---|
| `fundamentals` | 基本面（CZ 三条件代理 + 品质档） | 四面共振基本面腿、🐂 展示 |
| `sector_phase` | 板块相位（880 MACD） | R4 未复现，降证据层（v0.50 移出可买定义） |
| `bottom_patterns` | W 底 / 红肥绿瘦 | ⚠️ 同模块 `bull_bear_volume` 实为打分腿（见 A 配套） |
| `rsi_state` | RSI 状态（H3） | 研究打分参考 |
| `main_rally_factor` | 主升始发点（H4） | 原文两处矛盾，双口径由回测判定 |
| `b1_dual_factor` | B1 双轴（长期结构 × 短期回调） | 需 ≥120 根（DKS=MA114） |
| `b2_surge_factor` | B2 异动 / 底部异动 | — |
| `platform_pullback` | 平台突破回踩 | — |
| `perfect_b1_fit` | 「完美 B1」指纹拟合度 | v0.50 停加分，贴合列展示 |
| `s_shape` | S 形态综合分（S**） | R2 证伪无 alpha，展示列（v0.50 移出分层） |

### C. 对照 / 研究调试（不进 live）

| id | 名称 | status | 为什么不在 live（证据出处） |
|---|---|---|---|
| `baseline` | 对照基线：任何 as-of 日都判「可买」 | active | **必须保留**——所有进场信号的对照臂（R1） |
| `kdj_j` | 当日 KDJ 的 J 值（纯特征） | needs_work | 同号率仅 50%，不稳定（R3） |
| `low_vol` | 低波动因子（low-vol anomaly） | needs_work | 三窗对照 rally22/ytd26 跑输、无跨窗稳健性 ⇒ 不再研究（R2 第 16 条，v0.70） |
| `momentum` | 动量因子（12-1 类） | needs_work | 三窗对照 rally22/bull2425 垫底、无跨窗稳健性 ⇒ 不再研究（R2 第 16 条，v0.70） |
| `alpha_pvcorr` | Alpha#6 类：价量负相关 | needs_work | 三窗对照仅 bull2425 跑赢、无跨窗稳健性 ⇒ 不再研究（R2 第 16 条，v0.70） |
| `alpha101` | Alpha#101：进场 K 日内实体强度 | needs_work | 判别层过线但净值终审未过；2025 窗明确输（R2） |
| `mcap` | 小市值选择器 | needs_work | 判别层过线、净值终审惨败；止损把下跌端对称兑现（R2） |
| `reversal_quality` | 反转K 成色分（0–4） | needs_work | 稳健负预测；口径已与 live 默认值一致（对称 ±2%），刻意不跟随 env（R2） |
| `reversal_quality_inv` | 反转成色**反向**选择器 | needs_work | 样本内大胜、含退市跨年翻转；归因未分离（R2） |
| `b1_pullback_fit` | 买弱指纹 | needs_work | recall 100% 但期望 −0.42%/笔，劣于无差别进场 +0.96%（R2） |
| `sector_mainstream` | 主线板块族密度 | candidate | 准确的「窗口主线指纹」（归因工具），但「跟随主流」机械规则不成立（R2）；主线指纹节 v0.80 删除后 live 无引用，研究侧 `aggregate` 保留 |

⚠️ 没有 "falsified" 这一档是刻意的（owner：不要随便证伪）——needs_work = 「按现有证据
不可用，但证据本身待重跑」。

## 二、止损方案（`exit_rules.stop_rules`）

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

## 三、止盈方案（`exit_rules.take_profit_rules`）

| rule_id | enabled | 参数 | 说明 |
|---|---|---|---|
| `scale_out_two_bull` | ✅ live | consecutive_bull_bars = 2、require_above_bbi = true | **BBI 上方连续两根中大阳 → 分批止盈**（P2，减 10–20%） |
| `cost_zone_flat` | ⛔ 研究侧 | cost_zone_bars / pct = 3.0 / grace = 1 | 成本区「不涨就拍」（回测中） |

live 持仓链的其余卖出判定在 `b1_holding_state`（P0~P3 状态机：N 型 L1 结构硬失效位、
BBI 破位、0AMV 空头区间反弹减仓最高优先等）——它们是**持仓规则**不是止盈方案目录成员，
口径见 [`../b1/01_swing_rules.md`](../b1/01_swing_rules.md)。

**持仓计划持久化**（v0.82）：买入导入时把止损/止盈计划落盘 `data/trades/position_plans.json`
（止损价取 ≤ 买入日最近 stock_pool 的 `stop_loss_ref`，兜底 entry×0.93），14:45/17:00 报告
影子双跑对照（v0.83，TODO #60 观察 5 日后拍板是否并入正式判定）。

## 四、研究 → live 回流通道

`research/strategy_grid.py`（v0.85）：因子轴（scorer × entry_gate）× 出场轴网格寻优，
`cell_signature` 钉窗口/宇宙防混口径复用（v0.86）；优胜配置附 `exit_rules` 块，
键名与 live `EXIT_RULES.json` 一致可直接拷入。**纪律**：报告头部固定标注 R11 未决
（基准口径为负 ⇒ 读数仅供相对排序）；任何分值/阈值/开关的变更先回测、再落 CHANGELOG。
