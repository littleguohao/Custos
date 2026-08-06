# 待办清单

> **范围**：跨目录的待跑/待重跑/待收敛项。与同目录 `strategy_version_log.md` 的分工——
> **本文件记「还没做的事」，版本日志记「已经改了的策略规则」**，互不混放。
>
> 优先级按**「它阻塞了什么」**排，不按工作量：
> P0 = 阻塞其他事或 live 正在依赖 ｜ P1 = 已有结论悬空 ｜ P2 = 新验证 ｜ P3 = 技术债
>
> 最后更新：2026-08-06（含 strategy 梳理）

## P0 · 阻塞项

| # | 事项 | 解锁什么 | 状态 |
|---|---|---|---|
| 1 | **重下通达信完整历史日线** | 现在 vipdoc 只有 1214 根（2021-08 起）⇒ **跨年 walk-forward 只有 5 年**。重下后缺口对在市票不再是问题、跨牛熊验证深度打开 | ⏳ owner 2026-08-06 晚做，次日验证 |
| 2 | 验证 #1：`reconcile_qfq.py --gap-report` 看深度分布是否变长 | 确认 #1 生效 | ⏳ 等 #1 |
| 3 | `--gap-report` 的「**去偏价值**」栏：`2021_2026` 有多少票不在 vipdoc 里 | 若≈0 ⇒ 它是**从 vipdoc 生成的** ⇒ 加法调整是**我们自己转换脚本的 bug**，将来重新生成前先修那个脚本 | ⏳ 一条命令即可 |
| 4 | **R4 重跑**（[清单 P0](../00_governance/research/README.md)）：两窗改 tdx，或另找落在老 bundle 里的熊市窗（2008/2011/2015）做同样 2×2 | **live 择时正建立在 R4 上**，而它的「已 OOS 去偏」资格现在不成立 | 🔴 未开始 |

## P1 · 重跑（结论悬空）

逐条对应研究单元的 `**重跑口径**` 字段，详见
[`research/README.md`「⚠️ 重跑清单」](../00_governance/research/README.md)。

| # | 单元 | 重跑什么 | 前置 |
|---|---|---|---|
| 5 | [R10](../00_governance/research/R10_mechanism_M2_stops.md)/[R11](../00_governance/research/R11_baseline_margin_collapse.md) | 3000 样本 + **已实现口径**（剔 `open_end` 的分子分母）+ `margin > 3pp` 判据。提法换成「有没有方案能在 3000 只宇宙里做出可用 margin」 | — |
| 6 | [R9](../00_governance/research/R9_method_M1_payoff_ratio.md) | 三个对照臂 `scale_out_0/03/08` 于**收盘口径** + 3000 样本 | 与 #5 同批 |
| 7 | [R2](../00_governance/research/R2_selection_price_volume.md) | 把「换宇宙」与「换数据源」**拆成两次单变量对照** | — |
| 8 | [R3](../00_governance/research/R3_selection_discriminability_recall.md) | 只重算受影响窗的 `recall_by_band` 与净增益（「≥100% 带 −37.5%」这个被反复引用的数字）| — |
| 9 | [R1](../00_governance/research/R1_core_framework.md) | 重写**收益量级**。在此之前**任何 CAGR/期望数字都不应被引用** | #4 #5 #6 |

## P2 · 待跑（新验证）

| # | 事项 | 出处 | 备注 |
|---|---|---|---|
| 10 | **H3 RSI 状态因子** | [R8](../00_governance/research/R8_hypothesis_H3_H4_pending.md) | 已实现未跑 |
| 11 | **H4 主升始发点**（两种 CROSS 口径**都要跑**，原文有矛盾） | R8 | 已实现未跑 |
| 12 | 突破回踩型 B1 的三重门槛 + 净值终审 | [R6](../00_governance/research/R6_hypothesis_H1_dual_axis.md) | — |
| 13 | 宽口径 `bottom_surge` 的 gate 语义修正后再议 | [R7](../00_governance/research/R7_hypothesis_H2_b1b2b3.md) | 语义未修不必跑 |
| 14 | 仓位管理「**每天满仓 2 只**」 | [R16 ⑥](../00_governance/research/R16_input_material_corrections.md) | 材料高亮项 |
| 15 | R10 待跑 #5：组合层改扫 `--top-n` 宽度 | R10 | 前置 #5（要先把「敞口失效」与「样本太小」拆开）|

⚠️ **10–15 一律先过跨区间**。R6/R7 的教训：edge 集中在 2025-2026 单一 regime 的方案，
首轮看起来都很好。

## P3 · 待收敛 / 技术债

| # | 事项 | 出处 |
|---|---|---|
| 16 | **复权失败率不可见**：`qfq_table` 失败只打一条 stderr WARN、**没有汇总** ⇒ 3000 只票的日志里早被淹没，不知道实际多少只没复权成功 | [DATA_SOURCE_PRINCIPLE ③](../00_governance/data/DATA_SOURCE_PRINCIPLE.md) |
| 17 | 宇宙/窗口钉死开关**默认关** ⇒ 要不要改默认（改了会让历史命令行为变化） | [R13](../00_governance/research/R13_meta_reproducibility.md) |
| 18 | 跨 bundle 拼接**口径混合**（已加告警，未解决）| [R14](../00_governance/research/R14_meta_data_foundation.md) |
| 19 | `m2_stop_sweep` 的 `MEM_PER_JOB_MB=1200` 用实测 `[MEM]` 校准 | R10 待跑 #1 |
| 20 | `tick_buffer` **参数本身设计有问题**：余量应按**风险单位**而非价位数，才能让不同价位的股票是同一个风险 | [R10](../00_governance/research/R10_mechanism_M2_stops.md) |
| 21 | 核实老 bundle 是否覆盖 BJ | [QLIB_LOCAL_DATA 待做](../00_governance/data/QLIB_LOCAL_DATA.md) |
| 22 | `platform_high` 的口径待确认（按最高价 vs 收盘）| [R6](../00_governance/research/R6_hypothesis_H1_dual_axis.md) |

## ⚠️ 已失效的行动项（**别照着做**）

旧文档里这些「待跑」已被后续发现推翻或已完成。留着是为了**防止有人照单执行**：

| 原行动项 | 出处 | 为什么失效 |
|---|---|---|
| 「去幸存者偏差：`--data-source qlib`」 | R10 待跑 #3 | **2021 年后一只退市股都没有**，且 `2021_2026` bundle 已弃用（加法调整）⇒ 照做只会引入放大 13~21% 的收益，去偏一点没做到。近期窗口**目前无法去偏** |
| 「补探止损下界 `pct_03`/`pct_04`」 | R10 待跑 #4 | ✅ 已完成 —— **5% 是崖不是坡**（5%→4% 掉 42%）|
| 「`--exclude-zero-ret` 复跑」 | R3 | ❌ 已证是**错误的修正**（删的是合法样本），已标注勿用 |

## P4 · strategy 梳理查出的问题（2026-08-06）

索引与分类见 [`strategy/README.md`](../00_governance/strategy/README.md)。

| # | 事项 | 性质 |
|---|---|---|
| 26 | **持仓手册 §七 依赖一份零实现的文档**：`market_pullback_rotation_selection.md` 要求的主题切换/主题内分化/大小票切换/高低位切换四项检查**全仓零实现** ⇒ 要么手册第七节是空条款，要么在靠 LLM 判断（**违反项目核心原则**）| 需定性后处理 |
| 27 | **两份文档入口不可达**（288 行）：`UNIVERSAL_TECHNICAL_TREND_FRAMEWORK.md`(183) 与 `trading_execution_discipline.md`(105)。索引已成为入口；若不再需要走「已废」区块 | 需 owner 判定去留 |
| 28 | 代码里 `REVERSAL_CHANGE_PCT = 2.0`（旧对称阈值残留）**无人对照** ⇒ 留着有被误用的风险 | 技术债 |

## 需要 owner 拍板

| # | 事项 | 出处 |
|---|---|---|
| 23 | 「白线大于黄线」的歧义口径 | [R16 ⑧](../00_governance/research/R16_input_material_corrections.md) |
| 24 | `S**` 阈值 70/60 是否采纳（当前未采纳）| R16 ⑦ |
| 25 | M2 扫描是否继续（上一轮被暂停）| R10 |
| 26 | **止损口径的层级关系**：B1 执行止损 **6~12%(甜蜜点 8%)** vs CZ 强制止损体系 **第一道 15%/极限 20%**（措辞「无论谁推荐的个股都必须执行」）。数值差 2~4 倍，大概率是**层级**（B1 总是先触发）而非冲突，但**没有任何文档写出这个层级**。⚠️ 且 R10 说「B1 止损普遍太紧」，放宽到 12%+ 就逼近 CZ 的 15% ⇒ 关系会变紧 | [strategy/README.md 问题②](../00_governance/strategy/README.md) |

## 维护约定

- **完成的项直接删**，不留 `~~删除线~~` —— 已完成的事实归入对应研究单元或版本日志。
- 发现旧文档里的行动项已被推翻时，**移到「已失效」表并写清原因**，不要静默删除：
  别人可能正照着做。
- 新增项必须写**出处链接**，否则无法判断它还成不成立。
