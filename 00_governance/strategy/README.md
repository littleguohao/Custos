# 策略层索引

> `00_governance/strategy/` 存放**规则**：改动要进 `05_strategy_versions/strategy_version_log.md`。
> 与邻居的分工——`research/` 是「测出来的结论」（会被推翻），本目录是「我们决定怎么做」
> （改了要记版本），`contracts/` 是「代码直接依赖的契约」。

## 结构：一个策略 = 一个上下文目录

```
strategy/
├── STRATEGY_REGISTRY.json   # 机器可读注册表（新增策略必须登记）
├── b1/                      # B1 波段策略  —— 主
├── cz/                      # CZ 认知框架  —— 辅
├── _factors/                # 跨策略可复用因子（非策略，故加 _ 前缀）
└── _shared/                 # 跨策略规则（非策略）
```

**`_` 前缀 = 非策略上下文。** 这样 `ls` 一眼能分出「哪些是策略」和「哪些是给策略用的」。

| 上下文 | 角色 | 状态 | 入口 |
|---|---|---|---|
| **B1 波段策略** | 主 | ✅ 现行 | [`b1/README.md`](b1/README.md) |
| **CZ 认知框架** | 辅 | ⚠️ 仅作输入 | [`cz/README.md`](cz/README.md) |
| 通用因子 | — | ⚠️ 未接线 | [`_factors/README.md`](_factors/README.md) |
| 跨策略规则 | — | ⚠️ 部分过时 | [`_shared/README.md`](_shared/README.md) |

## 命名规则

| 对象 | 规则 | 例 |
|---|---|---|
| 策略上下文目录 | 无前缀、小写短名 | `b1/` `cz/` |
| 非策略目录 | `_` 前缀 | `_factors/` `_shared/` |
| 规则文档 | `NN_lower_snake.md`，NN 给**阅读顺序** | `01_swing_rules.md` |
| 附录 / 摘要 | `90+` | `90_research_summary.md` |
| 已废 | `99_deprecated_*` —— **废弃状态写在文件名里** | `99_deprecated_buy_integration.md` |
| 代码消费的配置 | `UPPER_SNAKE.json`（与 `contracts/` 一致）| `CZ_SECTOR_PREFERENCE.json` |

⇒ 策略名不再进文件名（目录已经是上下文），所以 `b1_swing_strategy.md` → `b1/01_swing_rules.md`。

## 每份文档的头部块

三行固定顺序，可机器解析（由测试校验）：

```markdown
> **上下文**：… ｜ **执行者**：… ｜ **状态**：…
> **版本**：… ｜ **代码依赖**：…
> **索引**：… · 改动须记 strategy_version_log.md
```

**「执行者」是最重要的一栏**，它决定改动代价：

| 执行者 | 改动代价 |
|---|---|
| **代码** | 改文档必须**同步改代码常量 + 跑测试**，否则文档变成谎言 |
| **人 / LLM** | 只需人知道；但**无从验证是否真在执行** |
| — | 摘要 / 已废，不是规则 |

## 如何新增一个策略或因子

1. **判断它是策略还是因子**：有完整进出场规则 ⇒ 策略（建 `<id>/` 目录）；
   只是一个判别维度 ⇒ 因子（放 `_factors/`，可被多个策略引用）。
2. 建目录 + `README.md`（照 `b1/README.md` 的固定小节：定位 / 规则文件 / 代码依赖 /
   与其他策略的关系 / 已知问题）。
3. **在 `STRATEGY_REGISTRY.json` 登记** —— 不登记会被测试拦住。
   `role` 填 `experimental` 的策略**不得进 live**。
4. 文档按 `NN_lower_snake.md` 命名；代码消费的配置进 `paths.py` 加常量。
5. 记一条版本日志。

## ⚠️ 三处待处理的问题

### ① 持仓手册 §七 依赖一份零实现的文档

[`b1/02_holding_check.md`](b1/02_holding_check.md) §七 写「按 `04_pullback_rotation.md`
将持仓分为四类」，而那份文档要求的**主题切换 / 主题内分化 / 大小票切换 / 高低位切换**
四项检查，在 `07_tools/` 里 **grep 零命中**。

⇒ 两种可能都要处理：**没在做**（空条款，该标出来）／**靠 LLM 在做**（违反核心原则：
分析判断须用确定性脚本）。→ 待办 #26。

### ② 止损口径的层级关系没有写定

| 出处 | 止损 |
|---|---|
| [`b1/01_swing_rules.md`](b1/01_swing_rules.md) / [`b1/90_research_summary.md`](b1/90_research_summary.md) | **6~12%，甜蜜点 ~8%** |
| [`cz/01_cognition_framework.md`](cz/01_cognition_framework.md)「强制止损体系」 | 第一道 **15%**、极限 **20%**，措辞「**无论谁推荐的个股都必须执行**」 |
| [R10 实测](../research/R10_mechanism_M2_stops.md) | **5% 是崖不是坡**；**B1 的止损普遍太紧** |

数值差 2~4 倍。**大概率是层级而非冲突** —— B1 的 8% 总是先于 CZ 的 15% 触发，
可读成「B1 执行止损 8%，CZ 绝对上限 20%」。但**没有任何文档写出这个层级**，
而 CZ 那句措辞是普适的，人读到会以为 B1 仓位也按 20% 执行。

⚠️ 且这个关系正在变紧：R10 说 B1 止损太紧，若放宽到 12%+ 就逼近 CZ 的 15% 第一道防线。
**属策略决策，待 owner 拍板，不擅自定。**

### ③ 两份文档此前入口不可达（288 行）

[`_factors/technical_trend.md`](_factors/technical_trend.md)（183）与
[`b1/03_execution_discipline.md`](b1/03_execution_discipline.md)（105）
在建索引前：代码没引用、其他治理文档没引用、仓库 README 与 `contracts/` 也没点名。
本索引与注册表现在是它们的入口。若判定不再需要，改名为 `99_deprecated_*` 而不是删除。

## ✅ 已核查一致（2026-08-06，下次不必重查）

[`b1/01_swing_rules.md`](b1/01_swing_rules.md) §三.3「分歧转一致反转K」的全部阈值
与 `enrich_candidates.py` **逐项一致**：

| 文档 | 代码常量 | 值 |
|---|---|---|
| J 低位 | `J_LOW_THRESHOLD` | 13.0 |
| 量比 ≤50% | `VOL_RATIO_MAX` | 0.5 |
| 20 日量分位最低 10% | `VOL_PCTILE_MAX` | 10.0 |
| 涨跌幅 −2% ~ +1.8%（**不对称**）| `REVERSAL_CHANGE_MIN_PCT` / `MAX_PCT` | −2.0 / 1.8 |
| 振幅 ≤7% | `REVERSAL_AMPLITUDE_PCT` | 7.0 |
| 振幅算法 `(高−低)/前收` | `amplitude_pct` 计算式 | 一致 |

⚠️ 代码里还留着 `REVERSAL_CHANGE_PCT = 2.0`（旧对称阈值），**判定已不使用**，仅供口径对照；
由 `tests/test_enrich_b1cz.py` 断言旧对称表达式不得重现。留着有被误用的风险（待办 #28）。

## 写入规范

- 改「执行者=代码」的文档 = 改代码 + 跑 `uv run pytest -q`；改「执行者=人」只需人知道。
  **两者都要记版本日志。**
- 参数值只在**一处**定义：文档写值、代码写常量，且**必须一致**；
  发现不一致时**以代码为准**改文档（代码是实际在跑的），并记为一次口径修正。
- 判定废弃时改名 `99_deprecated_*` 并在头部写明「以什么为准」，**不要删除** ——
  删掉会让后来者重踩。
- 认知框架（`role=secondary`、`status=advisory`）里的数字**不得直接当操作参数**，
  须经主策略的规则文档转译并回测。
