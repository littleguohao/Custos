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
| 1 | **重下通达信完整历史日线** | 现在 vipdoc 只有 1214 根（2021-08 起）⇒ **跨年 walk-forward 只有 5 年**，跨牛熊验证深度打不开；bundle 缺口（2020-09~2021-08）对仍在市的票也补不上 | ⏸ **未准备好**（owner 2026-08-07 告知），无日期承诺 |
| 2 | 验证 #1：`reconcile_qfq.py --gap-report` 看深度分布是否变长（抽样各票根数的 min/中位/max 与最早日期）| 确认 #1 生效 | ⏸ 等 #1 |

### ⚠️ 被 #1 阻塞的只有「更长的历史」，别把别的也当成阻塞

`--gap-report` 实测：vipdoc 覆盖 **2021-08-02 起、1214 根（≈4.9 年）到 2026-08**。
所以凡是**窗口落在 2021-08 之后**的重跑，用现有 vipdoc 就能做，**不等 #1**：

| # | 事项 | 为什么不被阻塞 | 状态 |
|---|---|---|---|
| 3 | `--gap-report` 的「**去偏价值**」栏：`2021_2026` 有多少票不在 vipdoc 里 | 纯读现有数据，一条命令 | 🟢 **随时可跑** |
| 4 | **R4 重跑**（[研究侧清单 P0](../00_governance/research/README.md)）：<br>① 两窗（2021-08~2023-12 / 2024-01~2026-02）改用 tdx vipdoc 重跑 → 修掉 qlib 加法调整带来的**收益放大 13~21%**<br>② 另找落在老 bundle（1999-2020）里的熊市窗做同样 2×2 → 才是**真去偏**（老 bundle 有 214 只退市票）| ①的两个窗都在 **2021-08 之后**，现有 vipdoc 全覆盖；②用老 bundle，本来就在 | 🟢 **可开始，且最要紧** |

⚠️ #4 是这一批里唯一**live 正在依赖**的（0AMV + 板块相位择时），
而它的「已 OOS 去偏」资格现在不成立。**不该跟着 #1 一起等。**
注意 ① 只修口径放大、**不解决去偏**（tdx 同样只有在市股）——去偏必须靠 ②。

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
（原「两份文档入口不可达」已由 2026-08-06 重组解决：`STRATEGY_REGISTRY.json` + 各级 README + 测试强制登记。）

| # | 事项 | 性质 |
|---|---|---|
| 26 | **持仓手册 §七 依赖一份零实现的文档**：`04_pullback_rotation.md` 要求的主题切换/主题内分化/大小票切换/高低位切换四项检查**全仓零实现** ⇒ 要么手册第七节是空条款，要么在靠 LLM 判断（**违反项目核心原则**）| 需定性后处理 |
| 27 | 代码里 `REVERSAL_CHANGE_PCT = 2.0`（旧对称阈值残留）**无人对照** ⇒ 留着有被误用的风险 | 技术债 |

## P5 · contracts 梳理查出的问题（2026-08-06）

索引与核查结论见 [`contracts/README.md`](../00_governance/contracts/README.md)。

| # | 事项 | 性质 |
|---|---|---|
| 29 | **报告缺可审计字段**：`report_id` / 规则版本 / 数据截止时间 / 输入清单**全仓零命中**（原 `MASTER_WORKFLOW §十二` 第 8 条）。研究侧刚查出「历史批次不可复现」（[R13](../00_governance/research/R13_meta_reproducibility.md)），**报告侧是同一类问题**：出了问题无法定位当时用的哪版规则、哪天的数据 | 可审计性 |
| 30 | **月度复盘未实现**：`MASTER_WORKFLOW §七` 有完整一节（时间/目标/结构/指标/产物），但全仓 `月度`/`month_review` 零命中；周度有 `weekly_review.py` | 要么实现要么降级为目标 |
| 31 | **冷却机制未实现**：`RiskDecision.cooldown_list` 与 `risk_type` 的「冷却」枚举已从契约**删除**（不是标注——契约里没有它就不会有人依赖）。若确实需要「触发止损的票进冷却、不重复买入」，须单独立项 | 需 owner 判定是否要做 |

## P6 · trades 梳理（2026-08-06）

| # | 事项 | 性质 |
|---|---|---|
| 32 | **对账观察期**：`reconcile_positions` 已接入 17:00 链但**默认不阻断**。跑若干交易日、确认 `status=ok` 稳定后，再考虑 `--strict`（数量不一致 exit 1）。⚠️ 若台账非从零开始，须先准备 `--baseline` 期初持仓，否则会一直 `replay_failed` | 观察后决定 |
| 33 | `backtest_0amv_bear_regime.py` 里的 `check_positions` 与新的 `reconcile_positions` 功能重叠，可让研究脚本改调用后者（当前未动，避免影响已跑过的回测口径）| 技术债 |

## P7 · 因子层抽取查出的问题（2026-08-06）

| # | 事项 | 性质 |
|---|---|---|
| 34 | 🔴 **反转K 涨跌幅口径 live 与研究不一致**：live（`enrich_candidates`）用 **−2.0% ~ +1.8%**（不对称，B1_w.pdf 纠偏后），而研究侧（`factors/reversal_quality`，原 `backtest_factors.REVK_CHG_PCT`）用 **对称 ±2%**。⇒ **`reversal_quality` 与 live 的反转K不是同一个东西**，而 [R2](../00_governance/research/R2_selection_price_volume.md) 的「稳健负预测」结论建立在它上面。抽取时保持原口径不动（改了会作废已有回测数字，而那些数字已在重跑清单里）| **需 owner 定**：研究口径该不该跟 live 对齐 |
| 35 | `alpha_pvcorr` / `low_vol` / `momentum` 标 `untested` —— 实现了但没有独立的净值终审记录。按 R2 整体结论推定不可用，但**缺它们自己的证据**。要么补跑，要么明确降级为「不再研究」| 补证据或明确废弃 |
| 36 | `enrich_candidates.py` 里还有 4 个内联因子未抽（`detect_wave_type` 72 行 / `compute_perfect_b1_fit` 68 / `compute_b1_pullback_fit` 51 / `detect_distribution` 132）。其中 `compute_b1_pullback_fit` 已被 live 与研究双方共用，最该先抽 | 下一步 |

| 37 | ⚠️⚠️ **研究说没用、live 却在用**：R2 结论「S_shape 无 alpha，全市场阈值扫描无 lift」，而 `score_candidates.technical_score` 的**主路径**就是它——`sstar_level(s_star)` 直接出技术层级、参与候选表 A/B/C/D 分层。已在 `factors/s_shape.py` 元数据与 `factors.KNOWN_STATUS_USE_CONFLICTS` 显式登记（不静默放过、也不擅自改分层）。**需 owner 定**：分层要不要换掉 s_shape，还是维持（README 说 StockPool 只是证据层、买入由 chief_decision 裁决，所以维持也讲得通）| **需 owner 拍板** |

| 38 | **1800 评分系统待完善**（owner 2026-08-06 记）：`score_candidates` 的技术分/共振分/分层阈值整体还要打磨。与 #37（s_shape 是主路径但 R2 说无 alpha）同一片区域，宜一起做 | 后续迭代 |
| 39 | **反转K 涨跌幅区间做验证**：已做成可配置（`B1_REVK_CHG_PCT` / `B1_REVK_CHG_MIN` / `B1_REVK_CHG_MAX`，默认对称 ±2%）。待跑不同区间的效果对比（±1.5 / ±2 / ±2.5 / 不对称 −2~+1.8），用同一批样本 + 已实现口径。⚠️ 覆盖值同时影响 live 与回测（两边读同一处，有意如此）| 待跑 |

## P8 · 测试覆盖率（2026-08-07 首次量化）

总覆盖率 **70.3%**（19587 语句，5784 未覆盖）。按风险分层的未覆盖语句：

    🔴 live/资金   ~540   🟠 live 链   ~860   ⚪ 研究  ~1297   🟡 其他  ~3087

⚠️ **补测试前先问「这段代码该不该存在」** —— 首轮清点就删掉一个 0% 的死文件
（`sync_trades.py`：零调用 + 依赖的 config 不存在 + 3 个真 bug），
给该删的代码写测试是浪费。

| # | 事项 | 当前 |
|---|---|---|
| 40 | `daily_pipeline.py` 23%（132 未覆盖）—— 编排核心。主要是 subprocess 串联，测试成本高；关键分支（门控码穿透、stage 失败）已由 `pipeline_kit` 覆盖，剩下的是 stage 清单本身 | 待评估 ROI |
| 41 | `run_1800.py` 21% / `run_1445.py` 22% / `run_0905.py` 52% —— runner 主流程。同上，stage 编排为主 | 待评估 ROI |
| 42 | **报告生成层零覆盖（部分已补）**：✅ `portfolio_review_report` 0→96%（当场抓出 state 变量覆盖 bug）、✅ `execution_review` 0→97%。剩 `theme_tracker_report` **0%（225 语句，⛔硬失败 stage）**、`holding_sector_mapper` 0%（130，非硬失败）、`wechat_summary` 0%（23）、`chief_decision_report` 19%（58，⛔硬失败）| 待补（theme_tracker 最要紧）|
| 43 | `close_review/` 多个文件低覆盖：`final_close_review.py` 19%（164）、`execution_review.py` 0%（63）、`review_core.py` 51%（134）、`review_enrichment.py` 32% | 下一批 review 时一起 |
| 44 | 研究脚本低覆盖（`adjust_diagnostic` 21% / `analyze_winner_features` 17% / `compare_signal_sets` 0% / `scan_signal_backtest` 0% / `m2_migrate_fingerprint` 0%）—— 风险最低，但先判定哪些已被取代可删 | 先判存废 |

| 45 | ⚠️ **`market_timing_scorer.is_stale` 是 fail-open**：`return bool(day and as_of) and as_of != day` —— **缺 `as_of` 时返回 False**（当成新鲜），于是没写 `as_of` 的 section 拿当日满分。与仓库别处的 fail-closed 原则相反（`runtime_gate._QUALITY_PASS` 注释：「风控组件的未知状态必须等于阻断」）。上游已缓解（`merge_incremental_market` 必须写 as_of），但判据本身仍是 fail-open。改成 fail-closed 会降低评分、改变 live 择时行为 ⇒ **需 owner 定**。测试已锁住现状 | **需 owner 拍板** |

| 46 | ⚠️ **覆盖率读数不稳定**：`market_timing_scorer.py` 在三次全量运行里读出 15% / 36% / 48%（语句总数不变）。已排除「导入形式不一致」（统一为包限定后仍 36%），未能在合理成本内定位。可验证的是「排除新测试 15% → 包含 36%」，即新测试确实 +21pp。**影响**：单文件覆盖率不能当精确指标用，只能看趋势。怀疑与 conftest 把 `07_tools` 与各子目录**都**铺进 sys.path、同一文件可两路导入有关 | 待查 |

## 需要 owner 拍板

| # | 事项 | 出处 |
|---|---|---|
| 23 | 「白线大于黄线」的歧义口径 | [R16 ⑧](../00_governance/research/R16_input_material_corrections.md) |
| 24 | `S**` 阈值 70/60 是否采纳（当前未采纳）| R16 ⑦ |
| 25 | M2 扫描是否继续（上一轮被暂停）| R10 |

## 维护约定

- **完成的项直接删**，不留 `~~删除线~~` —— 已完成的事实归入对应研究单元或版本日志。
- 发现旧文档里的行动项已被推翻时，**移到「已失效」表并写清原因**，不要静默删除：
  别人可能正照着做。
- 新增项必须写**出处链接**，否则无法判断它还成不成立。
