# 实盘交易复盘 → 反思 → 进化 记录

> **这个文件回答的问题**：某条策略约束**为什么存在**、从哪些交易记录看出来的、
> 现在是否真的在执行。
>
> 与邻居的分工（三份都要看，但问题不同）：
>
> | 文件 | 回答什么 |
> |---|---|
> | `strategy_version_log.md` | 规则**改了什么**（改动本身 + 待验证指标） |
> | `TODO.md` | **待做什么** |
> | **本文件** | **为什么改** —— 交易记录里的观察 → 结论 → 是否已变成机制 |
> | `governance/research/` | 回测**假设检验**（历史数据上的因子/策略研究，与实盘账户行为无关） |
>
> ⚠️ **只增不删。** 结论被后续证据推翻时，把状态改成 `已推翻` 并写清推翻依据，
> 不要删行 —— 与 `governance/research/` 同一约定。删掉之后就没人记得
> 曾经因为它改过策略，也就无法解释代码里那条判定为什么存在。

## 怎么写一条

**证据必须可追溯。** 机器已经在产出结构化归因，直接引它：

    artifacts/reports/weekly/{iso_year}W{week}_weekly_review.json   ← weekly_review.py 产出
      · facts        win_rate_pct / profit_loss_ratio / avg_hold_days / gross_pnl …
      · execution_issues / strategy_issues / environment_issues
        rule 取值（受控词表）：slow_stop_loss / unplanned_trade / sell_fly /
                              short_hold_loss_profile / no_trade_confirmation_missing /
                              wrong_advice_direction / adverse_market_environment

**样本量必须写。** 今天（2026-08-07）核对时发现下面回填的 6 条用户画像
**没有一条记了样本量与统计区间** —— 那是它们最大的弱点：
「20 天以内交易贡献主要亏损」是 3 笔还是 30 笔得出的？前者不足以支撑一条硬约束。
研究侧已经因为样本量吃过教训（R11 基准崩塌、「5% 是崖不是坡」），实盘侧同理。
不知道就写 **⚠️ 未记录**，不要编。

**状态词表**（与 `factors/` 的三维标记同一思路：证据强度 ≠ 是否上线）：

| 状态 | 含义 |
|---|---|
| `已成机制` | 代码在执行，**必须给出代码位置**（有测试校验该路径存在） |
| `仅人工约束` | 没有代码，靠人记着。**不要写成「已执行」** —— 那是今天删掉 `TEAM_BLUEPRINT.md` 的原因 |
| `待验证` | 样本不足或口径不清，先记下来 |
| `已推翻` | 后续证据否掉了。保留并写清推翻依据 |

---

## 记录

### 2026-08-07 回填：用户画像 6 条的来源与现状

来源：`TEAM_BLUEPRINT.md`（当日删除，内容抢救进
`governance/strategy/_shared/system_principles.md`）。原文只写了结论，
**没写统计区间与样本量** —— 下表如实标 ⚠️，不补编。

| # | 观察（来自交易记录） | 结论 | 样本量 | 状态 | 机制位置 / 缺口 |
|---|---|---|---|---|---|
| 1 | 中周期主线交易表现优于高频短线 | 优势在**中周期主线** | ⚠️ 未记录 | `仅人工约束` | 无代码。B1 波段本身偏中周期，但没有任何组件限制短线频率 |
| 2 | 2026 年 6 月亏损集中 | **短线试错密度过高** | ⚠️ 未记录（仅"2026-06"） | `仅人工约束` | 无「单周开仓次数上限」类机制 |
| 3 | 20 天以内的交易贡献主要亏损 | 短持有期是亏损主因 | ⚠️ 未记录 | `已成机制`（仅**检测**） | `close_review/weekly_review.py::_loss_structure` 算 `short_loss_share`；`rule="short_hold_loss_profile"` 会记进 `strategy_issues`。**只报告、不阻断** |
| 4 | 九丰能源等案例：同一标的连续亏损 | 需要**连亏冷却**机制 | ⚠️ 未记录（"等案例"，具体几笔不明） | `仅人工约束` ⛔ | **零实现**。`DATA_FLOW_CONTRACT.md` 曾声明 `RiskDecision.cooldown_list`，从未实现、已删。见待办 #51 |
| 5 | 卖出后 20 日平均收益为负 | 主要问题**不是普遍卖飞，而是亏损单处理偏慢** | ⚠️ 未记录 | `已成机制`（两侧都检测） | 卖飞：`weekly_review::_sell_fly_review`（`rule="sell_fly"`，且**必报 coverage**）；慢止损：`_slow_stops`（`rule="slow_stop_loss"`，判据是**亏损幅度**超 `STOP_LOSS_PCT=-7%`，**不看持有时长**） |
| 6 | —（原则性） | **风控权重必须高于买入** | — | `已成机制` | `chief_decision_report`：高优先风险覆盖 B1 动作；`b1_holding_state.permissions.allow_signal_override_hard_risk = False` |

**这张表暴露的缺口**（不是新结论，是把已知的说清楚）：

- 第 1/2 条是**观察**，没有对应机制，也没有可量化的判据（「密度过高」高于多少？）
  ⇒ 要么补一个可测判据、要么承认它只是提醒。
- 第 4 条有明确动因（第 5 条说「亏损单处理偏慢」，连亏冷却正是针对它）却**零实现** ⇒ 待办 #51。
- 第 3/5 条已有检测但**只报告不阻断** —— 这是刻意的（复盘层是解释不是裁决），
  但要意识到：**检测到 ≠ 会被阻止**。

### 2026-08-07 慢止损的判据与命名不符

| 项 | 内容 |
|---|---|
| 观察 | 补 `weekly_review` 测试时读 `_slow_stops` 源码 |
| 事实 | 规则名 `slow_stop_loss`、文案「止损偏慢」，但判据**只有** `pnl_pct <= -7%`，**不看 `hold_days`**（后者只作为证据附在 evidence 里） |
| 口径是否自洽 | **是** —— 止损线设在 −7% 而实现了 −20%，本身就说明没在线上切出去（跳空缺口除外） |
| 风险 | 名字会让人以为它验证过时长。读报告的人可能把一笔「1 天内 −20%」当成「拖了很久才止损」 |
| 样本量 | 不适用（代码核对，非统计结论） |
| 状态 | `已成机制` |
| 机制位置 | `close_review/weekly_review.py::_slow_stops`；口径已写进 `tests/test_extracted_units.py::test_slow_stops_judges_loss_depth_only` |
