# 系统原则与用户画像

> **上下文**：**跨策略规则**（不属于任何单一策略）　｜　**执行者**：**人**（原则/画像）+ 代码（下表已实现的 4 条）　｜　**状态**：live
> **版本**：—（2026-08-07 从 `TEAM_BLUEPRINT.md` 抢救）　｜　**代码依赖**：`holdings/b1_holding_state.py`、`generate_risk_and_sectors.py`（仅下表标 ✅ 的规则）
> **索引**：[`../README.md`](../README.md)　·　改动须记 [`strategy_version_log.md`](../../../strategy_version_log.md)
>
> 2026-08-07 从根目录 `TEAM_BLUEPRINT.md` 抢救而来（该文件已删）。
>
> ⚠️ **为什么删掉原文件**：它 58 行里有两节在**说谎** —— 「卖出风控硬规则」声称
> 6 条规则「由 `generate_risk_and_sectors.py` 和 `b1_holding_state.py` 执行，
> 不可被总控决策覆盖」，逐条核实后**其中 2 条零实现**；架构表里的脚本路径已过期，
> 还写着「选股池**待重建**」而 18:00 选股链早就每天在跑。
>
> 只有这两节是**别处没有的策略资产**，所以搬过来；其余部分要么过期、
> 要么与 `README.md` / `MASTER_WORKFLOW.md` / `decision_priority.md` 重复。

## 核心原则

1. 不追求单次判断正确，而追求系统长期正期望。
2. 所有建议必须可追踪、可复盘、可迭代。
3. 风控优先级高于收益预测。
4. 交易计划必须先定义无效条件，再定义盈利目标。
5. 系统输出的是决策辅助，不替代最终人工决策。

## 用户画像与个性化约束

来自历史交易复盘。这些是**对这个账户的经验判断**，不是通用规律 ——
改动它们要有新的复盘证据，不能凭感觉。

⚠️ **每条的来源、样本量与「是否已成机制」见
[`trade_lessons.md`](../../../trade_lessons.md)。**
那份表里如实标出：这 6 条**没有一条记了统计区间与样本量**，
且第 1/2/4 条**没有任何代码在执行**（第 4 条「连亏冷却」是零实现，见待办 #51）。
读到这一节时不要默认它们已经被系统保证。

1. 用户优势更偏**中周期主线交易**，而不是高频短线。
2. 2026 年 6 月亏损集中，**短线试错密度过高**。
3. **20 天以内**的交易贡献主要亏损。
4. 九丰能源等案例显示需要**连续亏损冷却**机制。
5. 卖出后 20 日平均收益为负 —— 主要问题**不是普遍卖飞，而是亏损单处理偏慢**。
6. 风控权重必须高于买入。

## 卖出风控规则：声称 vs 实测（2026-08-07 逐条核实）

⚠️ 原文档把这 6 条一律写成「硬规则、不可被总控决策覆盖」。实测后必须区分开 ——
**把未实现的规则写成「已执行」，比没写更危险**：读的人会以为有这道防线。

| 规则 | 实测 | 执行处 |
|---|---|---|
| 单票亏损超 **−10%** → 强制风控评估 | ✅ 已实现 | `holdings/b1_holding_state.py` 的 `hard_loss`（P0） |
| 短线止损 **−7%** → 复盘或减仓 | ✅ 已实现 | `b1_holding_state` 的 `loss_reduction`（P1）、`portfolio_review_report`、`weekly_review.STOP_LOSS_PCT` |
| 短线止损 **−5%** 档 | ⛔ **不存在** | 只有 −7% 一档 |
| **P0 优先级不可覆盖** | ✅ 已实现 | `b1_holding_state` 的 `permissions.allow_signal_override_hard_risk = False` |
| **0AMV 空头区间减仓最高优先**（任何反弹都是卖出机会、禁止加仓补仓） | ✅ 已实现 | `b1_holding_state` 的 `bear_regime_reduce_top_priority`（P1）+ `generate_risk_and_sectors` 的 `regime_directive` |
| **连亏冷却**：同股连续亏损 2 次 → 冷却 10 个交易日 | ⛔ **零实现** | 见待办 #51。同一件事今天还出现过一次：`contracts/DATA_FLOW_CONTRACT.md` 里的 `RiskDecision.cooldown_list` 也是**声明过但从未实现**，已删 |
| **胜率降仓**：当月短线胜率 < 35% → 降低短线仓位 | ⛔ **零 live 实现** | 见待办 #51。只有研究脚本算胜率（`research/` 与 `weekly_review`），没有任何 live 组件据此降仓 |

⇒ 前 4 条可以当防线依赖；**后 2 条不能** —— 它们是**意图**，不是机制。

## 相关文档

- 决策优先级（含 2026-08-07 新增的「按证据新鲜度取」）：[`decision_priority.md`](decision_priority.md)
- 全链工作流：[`../../contracts/MASTER_WORKFLOW.md`](../../contracts/MASTER_WORKFLOW.md)
- B1 策略规则：[`../b1/01_swing_rules.md`](../b1/01_swing_rules.md)
