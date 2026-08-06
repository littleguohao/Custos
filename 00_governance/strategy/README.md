# 策略规则索引

> `00_governance/strategy/` 存放**规则**：改动要进 `05_strategy_versions/strategy_version_log.md`。
> 与邻居的分工——`research/` 是「测出来的结论」（会被推翻），
> 本目录是「我们决定怎么做」（改了要记版本），`contracts/` 是「代码直接依赖的契约」。
>
> **按「谁执行它」分类**，因为这决定了改动的代价：代码执行的改动要同步改代码并跑测试，
> 人执行的改动只需人知道。2026-08-06 首次建立。

## 分类

### ① 规则 · 代码执行

代码按它计算。**改文档必须同步改代码**，否则文档就成了谎言。

| 文档 | 行 | 代码依赖 | 一致性 |
|---|---|---|---|
| [b1_swing_strategy.md](b1_swing_strategy.md) | 463 | `enrich_candidates.py` / `b1_holding_state.py` / `backtest_factors.py` | ✅ 反转K 已逐项核查（见下） |

### ② 规则 · 人执行

人或 LLM 按它做判断，**无代码实现**。风险是「写了但没人做」，且无从验证。

| 文档 | 行 | 状态 |
|---|---|---|
| [daily_holding_check_manual.md](daily_holding_check_manual.md) | 146 | ⚠️ §七 依赖一份零实现的文档（见问题 ①）|
| [market_pullback_rotation_selection.md](market_pullback_rotation_selection.md) | 137 | 🔴 **零实现、零代码引用**，唯一引用者是上面那份手册 |
| [pit_recovery_strategy.md](pit_recovery_strategy.md) | 120 | 人判断（坑位分类）|
| [trading_execution_discipline.md](trading_execution_discipline.md) | 105 | ⚠️ **入口不可达**（见问题 ③）|

### ③ 认知框架 · 输入

提供**判断依据**，不是可直接执行的规则。数字不应被当作我们的操作参数。

| 文档 | 行 | 来源 | 代码用了多少 |
|---|---|---|---|
| [cz_strategy.md](cz_strategy.md) | 933 | 星球社区认知提炼 | 只有 §14.6 量能规则（`enrich_candidates.py:670`）|
| [UNIVERSAL_TECHNICAL_TREND_FRAMEWORK.md](UNIVERSAL_TECHNICAL_TREND_FRAMEWORK.md) | 183 | — | ⚠️ **入口不可达**，0 术语在代码中出现 |

### ④ 摘要

| 文档 | 行 | 状态 |
|---|---|---|
| [B1_STRATEGY_SUMMARY.md](B1_STRATEGY_SUMMARY.md) | 50 | ⚠️ **量级数字待重跑**，见 [`../research/README.md`「重跑清单」](../research/README.md) |

### ⑤ 已废 / 待重建

**保留而不删**：它们记录了「为什么当初这样设计」，删掉会让后来者重新踩坑。
但必须写明**以什么为准**，否则会被当作现行规则读。

| 文档 | 行 | 以什么为准 |
|---|---|---|
| [BUY_STRATEGY_INTEGRATION_RULES.md](BUY_STRATEGY_INTEGRATION_RULES.md) | 130 | 相关代码已移除。选股流程重建时作参考；现行流程见 `../contracts/SCREENING_WORKFLOW.md` |
| [DECISION_PRIORITY_RULES.md](DECISION_PRIORITY_RULES.md) | 141 | 写于 Agent 架构时代。优先级规则本身仍有效，以 `../contracts/MASTER_WORKFLOW.md` 与仓库 `README.md`「决策优先级」为准 |

## ⚠️ 三处待处理的问题

### ① 持仓手册 §七 依赖一份零实现的文档

`daily_holding_check_manual.md:139` 写「大盘回调后，按 `market_pullback_rotation_selection.md`
将持仓分为四类」。而那份文档要求的**主题切换 / 主题内分化 / 大小票切换 / 高低位切换**
四项检查，**全仓零实现**（grep `主题切换|大小切换|高低切换` 在 `07_tools/` 无命中）。

⇒ 两种可能，都需要处理：

- **没在做** ⇒ 手册第七节是空条款，该标出来；
- **靠 LLM 在做** ⇒ 违反项目核心原则（「数据采集与分析判断用确定性脚本，
  LLM 仅负责格式化和输出摘要」），该改成脚本或明确降级为「参考」。

### ② 止损口径的**层级关系没有写下来**

| 出处 | 止损 |
|---|---|
| `b1_swing_strategy.md` / `B1_STRATEGY_SUMMARY.md` | **6~12%，甜蜜点 ~8%** |
| `cz_strategy.md`「强制止损体系」 | 第一道防线 **15%**、最终防线 **20%**，措辞是「**无论谁推荐的个股都必须执行**」 |
| [R10 实测](../research/R10_mechanism_M2_stops.md) | **5% 是崖不是坡**；**B1 的止损普遍太紧** |

数值差 2~4 倍。**它们大概率不是冲突而是层级**——B1 的 8% 总是先于 CZ 的 15% 触发，
所以可以读成「B1 执行止损 8%，CZ 绝对上限 20%」。**但没有任何文档写出这个层级**，
而 CZ 那句措辞是普适的，人读到会以为 B1 仓位也按 20% 执行。

⚠️ 且这个关系正在变紧：R10 说 B1 止损太紧，若放宽到 12%+ 就逼近 CZ 的 15% 第一道防线。
**需要 owner 拍板层级关系，我不擅自定。**

### ③ 两份文档入口不可达（288 行）

`UNIVERSAL_TECHNICAL_TREND_FRAMEWORK.md`（183）与 `trading_execution_discipline.md`（105）
**代码没引用、其他治理文档没引用、仓库 README 与 contracts 也没点名**。

⇒ 本索引即是它们的入口。若判定不再需要，走「已废」区块而不是删除。

## ✅ 已核查一致（2026-08-06，下次不必重查）

`b1_swing_strategy.md` §三.3「分歧转一致反转K」的全部阈值与 `enrich_candidates.py` **逐项一致**：

| 文档 | 代码常量 | 值 |
|---|---|---|
| J 低位 | `J_LOW_THRESHOLD` | 13.0 |
| 量比 ≤50% | `VOL_RATIO_MAX` | 0.5 |
| 20 日量分位最低 10% | `VOL_PCTILE_MAX` | 10.0 |
| 涨跌幅 −2% ~ +1.8%（**不对称**）| `REVERSAL_CHANGE_MIN_PCT` / `MAX_PCT` | −2.0 / 1.8 |
| 振幅 ≤7% | `REVERSAL_AMPLITUDE_PCT` | 7.0 |
| 振幅算法 `(高−低)/前收` | `amplitude_pct` 计算式 | 一致 |

⚠️ 代码里还留着 `REVERSAL_CHANGE_PCT = 2.0`（旧对称阈值），**判定已不使用**，
仅供口径对照；由 `tests/test_enrich_b1cz.py` 断言旧对称表达式不得重现。
留着它有被误用的风险——若哪天确认无人对照，应删除。

## 写入规范

- 改①类文档 = 改代码 + 跑 `uv run pytest -q`；改②③类只需人知道。**两者都要记版本日志。**
- 参数值只在**一处**定义：文档写值、代码写常量，且**必须一致**；
  发现不一致时**以代码为准**改文档（代码是实际在跑的），并记为一次口径修正。
- 判定为废弃时移到⑤区块并写明「以什么为准」，**不要删除** —— 删掉会让后来者重踩。
- 认知框架（③类）里的数字**不得直接当操作参数用**，要经过①类文档转译并回测。
