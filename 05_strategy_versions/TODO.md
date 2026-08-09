# 待办清单

> **范围**：跨目录的待跑/待重跑/待收敛项。与同目录 `strategy_version_log.md` 的分工——
> **本文件记「还没做的事」，版本日志记「已经改了的策略规则」**，互不混放。
>
> 优先级按**「它阻塞了什么」**排，不按工作量：
> P0 = 阻塞其他事或 live 正在依赖 ｜ P1 = 已有结论悬空 ｜ P2 = 新验证 ｜ P3 = 技术债
>
> 最后更新：2026-08-09（#21/#27/#36 核实收口已删，#53 首次真跑已记录；#4 批跑中）

## P0 · 阻塞项

| # | 事项 | 解锁什么 | 状态 |
|---|---|---|---|
| 4 | **R4 重跑**（[研究侧清单 P0](../00_governance/research/README.md)）：<br>① 两窗（2021-08~2023-12 / 2024-01~2026-02）改用 tdx vipdoc 重跑 → 修掉 qlib 加法调整带来的**收益放大 13~21%**<br>② 另找落在老 bundle 里的熊市窗做同样 2×2 → 才是**真去偏**（老 bundle 有 214 只退市票）| ①② 数据均已就位 | 🔄 **批跑中**（2026-08-08 启动，16 格 2×2×4窗，命令与口径见 `06_logs/r4_rerun/README.md`；2008 窗不可跑——880 板块指数自 2011-10 才有，改为 2015 股灾窗 + 2011-10~2012 慢熊窗） |

✅ **#1~#3 已完成（2026-08-08）**：vipdoc 已重下完整历史（600000 自 2000-01-04 共 6327 根；
`--gap-report` 抽样最早 2000-01-04、vipdoc 总数 5538）。#3 去偏价值栏：`2021_2026`
有 20 只（0.4%）、`2006_2020` 有 230 只（5.9%）不在 vipdoc——退市票仍只在老 bundle。

⚠️ #4 是唯一**live 正在依赖**的（0AMV + 板块相位择时），
它的「已 OOS 去偏」资格要等批跑结果回填才恢复。
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
| 17 | 宇宙/窗口钉死开关**默认关** ⇒ 要不要改默认（改了会让历史命令行为变化） | [R13](../00_governance/research/R13_meta_reproducibility.md) |
| 18 | 跨 bundle 拼接**口径混合**（已加告警，未解决）| [R14](../00_governance/research/R14_meta_data_foundation.md) |
| 19 | `m2_stop_sweep` 的 `MEM_PER_JOB_MB=1200` 用实测 `[MEM]` 校准 | R10 待跑 #1 |
| 20 | `tick_buffer` **参数本身设计有问题**：余量应按**风险单位**而非价位数，才能让不同价位的股票是同一个风险 | [R10](../00_governance/research/R10_mechanism_M2_stops.md) |
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
| 37 | ⚠️⚠️ **研究说没用、live 却在用**：R2 结论「S_shape 无 alpha，全市场阈值扫描无 lift」，而 `score_candidates.technical_score` 的**主路径**就是它——`sstar_level(s_star)` 直接出技术层级、参与候选表 A/B/C/D 分层。已在 `factors/s_shape.py` 元数据与 `factors.KNOWN_STATUS_USE_CONFLICTS` 显式登记（不静默放过、也不擅自改分层）。**需 owner 定**：分层要不要换掉 s_shape，还是维持（README 说 StockPool 只是证据层、买入由 chief_decision 裁决，所以维持也讲得通）| **需 owner 拍板** |

| 38 | **1800 评分系统待完善**（owner 2026-08-06 记）：`score_candidates` 的技术分/共振分/分层阈值整体还要打磨。与 #37（s_shape 是主路径但 R2 说无 alpha）同一片区域，宜一起做 | 后续迭代 |
| 39 | **反转K 涨跌幅区间做验证**：已做成可配置（`B1_REVK_CHG_PCT` / `B1_REVK_CHG_MIN` / `B1_REVK_CHG_MAX`，默认对称 ±2%）。待跑不同区间的效果对比（±1.5 / ±2 / ±2.5 / 不对称 −2~+1.8），用同一批样本 + 已实现口径。⚠️ 覆盖值同时影响 live 与回测（两边读同一处，有意如此）| 待跑 |

## P8 · 测试覆盖率（2026-08-07 首次量化）

总覆盖率 **78.2%**（19719 语句，4303 未覆盖）—— 2026-08-07 从 70.3% 提上来。

编排层已收口（此前是最大的空洞，**两个 live bug 都藏在这里**）：

    run_0850 96.7%   run_0905 90.4%   run_1445 82.7%
    run_1700 90.3%   run_1800 87.8%   daily_pipeline 85.5%

⚠️ 但**覆盖率高 ≠ 链是通的**：编排测试里 stage 一律被打桩，
从不真的去看被调用的脚本文件在不在 —— 2026-08-07 `daily_pipeline` 四个持仓
stage 全部指向不存在的文件、整条链硬失败，而 3481 条测试全绿。见 #53。

⚠️ **补测试前先问「这段代码该不该存在」** —— 首轮清点就删掉一个 0% 的死文件
（`sync_trades.py`：零调用 + 依赖的 config 不存在 + 3 个真 bug），
给该删的代码写测试是浪费。

| # | 事项 | 当前 |
|---|---|---|
| 44 | **研究脚本存废**（部分推进）：2026-08-07 建了统一入口 `07_tools/research/__main__.py` 与注册表，**3 个覆盖率 0% 的已标 `stale`** 并在运行时打警告：`compare_signal_sets` / `scan_signal_backtest` / `m2_migrate_fingerprint`（后者是一次性迁移脚本，大概率可删）。留在表里而不是删掉，是因为「不确定」本身要可见。⇒ **需 owner 逐个定：删 / 转正 / 继续留**。`tests/test_research_entry.py` 钉住了这三个的 stale 状态，定案后要同步 | 待 owner |

| 45 | ⚠️ **`market_timing_scorer.is_stale` 是 fail-open**：`return bool(day and as_of) and as_of != day` —— **缺 `as_of` 时返回 False**（当成新鲜），于是没写 `as_of` 的 section 拿当日满分。与仓库别处的 fail-closed 原则相反（`runtime_gate._QUALITY_PASS` 注释：「风控组件的未知状态必须等于阻断」）。上游已缓解（`merge_incremental_market` 必须写 as_of），但判据本身仍是 fail-open。改成 fail-closed 会降低评分、改变 live 择时行为 ⇒ **需 owner 定**。测试已锁住现状 | **需 owner 拍板** |

| 46 | ⚠️ **覆盖率读数不稳定**：`market_timing_scorer.py` 在三次全量运行里读出 15% / 36% / 48%（语句总数不变）。已排除「导入形式不一致」（统一为包限定后仍 36%），未能在合理成本内定位。可验证的是「排除新测试 15% → 包含 36%」，即新测试确实 +21pp。**影响**：单文件覆盖率不能当精确指标用，只能看趋势。怀疑与 conftest 把 `07_tools` 与各子目录**都**铺进 sys.path、同一文件可两路导入有关 | 待查 |

| 47 | **硬失败 stage 覆盖收尾**：11 个里 **7 个已 ≥90%**（portfolio_review 96 / theme_tracker 94 / execution_review 97 / chief_decision 99 / review_enrichment 97 / generate_risk_and_sectors 99 / amv_state 92）。剩 4 个：`market_timing_scorer` 36%（159，读数不稳见 #46）、`b1_holding_state` 84%（24，剩 main 与 pre_checks 的 TQ 分支）、`batch_holding_technical` 61%（30）、`daily_report` 66%（66）| 待补 |

| 48 | **RSS 代码命中的残余误配**：`rss_filter` 已加数字边界（修掉「嵌在更长数字里」），但「`净利润600000元`」这种**代码恰好等于一个独立金额**仍会误配 +45 分并顶到候选首位。要分辨得看上下文（前后是否有「元/万元/亿」等量词，或要求邻近出现持仓名称）。收益 vs 复杂度待评估 | 待定 |
| 49 | `rss_filter.entities(date)` 的 `date` **未被使用** —— `current_positions.json` 无历史版本，回填历史日期会用今天的持仓筛那天的新闻。等持仓快照有历史版本后接上 | ⏸ 依赖持仓历史 |

| 53 | **端到端真跑 `daily_pipeline`** —— ✅ **首次真跑已完成（2026-08-08）**：目标机按 `--date 2026-08-07` 重跑全部五个 runner（证据：`06_logs/2026-08-07_*_run_log.json`，started_at 为 08-08 深夜）。1800 选股链 11 stage 全 OK；0850 7/7、0905 3/3、1700 completed。且立刻抓到一个下周一就会发作的真 bug：`holding_quotes` 契约 spec 把 `indices` 误写为 dict（生产者是 list），被 1700 链契约校验报出 → 已修（commit `1f118b1`）。⚠️ **残余缺口**：① 1445 的盘中快照类 stage 无法用历史日期复现 fresh 校验（本次 `close_review` 因 `captured_at` 非目标日失败，属预期）；② `collect_fund_flow` 网络失败为 best-effort 不阻断。⇒ 例行化缺口仍在：把「每交易日五个 runner 的 run_log status 检查」做成例行核对 | 已首跑，例行核对待做 |
| 54 | **生产代码剩余覆盖缺口**（按缺失语句排）：`market_timing/market_timing_scorer` 35%（159，⚠️ 读数不稳见 #46）、`market_timing/refresh_market_indices` 17%（130）、`local_tdx/adjust_factors` 62%（154）、`local_tdx/local_tdx_data` 69%（143）、`holdings/holding_sector_mapper` 20%（106）、`collect/collect_holding_quotes` 65%（102）、`market_timing/market_timing_collector` 48%（81，产 19 个消费者的产物）、`daily_report` 65%（66）。⚠️ 编排层各 runner 只剩 8~24 行未覆盖，且全是单行 `[WARN]` 打印分支，**边际收益低，不必再追** | 待补 |
| 55 | **`audit_*` 测试家族按模块重组**：11 文件 4821 行（占测试 15%），按**审计轮次**（P0/P1/P2/P3/opt）组织而非按模块 —— 找「`technical_monitor` 的测试」要翻 9 个文件（含 4 个 audit）。⚠️ 2026-08-07 评估后**决定先不动**：搬 4800 行测试的风险大于可读性收益（同日搬迁脚本已两次出错：按行替换撞上分号连写、正则把注释当导入名）。若哪天要做，前置条件是先有一次真跑验收（#53） | 已评估，暂不动 |

## 需要 owner 拍板

| # | 事项 | 出处 |
|---|---|---|
| 52 | **`overseas_market.as_of` 在时间戳全缺时被写成采集时刻（now），门控据此判 `confirmed`** —— 一处 fail-open。生产者留了痕（`as_of_basis: "collection_time_fallback"`）但 `runtime_guards.market_quality_gate` **从不读**它，判据只有「有值 且 as_of 非空」。⚠️ 与契约层已拍板的相反决定不一致：`amv_0.as_of` 刻意允许 None，理由原话是**「编一个 as_of 等于给门控假的新鲜度」**。影响面有界（overseas 权重 10/100，且排除在 `core` 覆盖率判定外）。两条路子：① 生产者不再伪造（as_of 缺省 ⇒ 门控自然判 candidate）；② 门控改读 `as_of_basis`。⚠️ 未擅自改 —— 2026-07-30 有「门控与口径同时收紧致 17:00 链失败」的事故。现状已由 `tests/test_tdx_ext_fallback.py::TestOverseasAsOfDerivation` 钉住 | 2026-08-07 review tests/ 时查出（该推导此前零测试覆盖） |
| 51 | **两条「卖出风控硬规则」零实现**（2026-08-07 逐条核实 `TEAM_BLUEPRINT.md` 时查出）：<br>① **连亏冷却**「同股连续亏损 2 次 → 冷却 10 个交易日」—— 代码里零实现。同一件事今天还出现过一次：`DATA_FLOW_CONTRACT.md` 的 `RiskDecision.cooldown_list` 也是声明过但从未实现（已删）。<br>② **胜率降仓**「当月短线胜率 < 35% → 降低短线仓位」—— 只有研究脚本算胜率，无任何 live 组件据此降仓。<br>⚠️ 原文档把它们与另外 4 条真规则并列写成「不可被总控决策覆盖」，**把未实现写成已执行比没写更危险**（读的人以为有这道防线）。现已在 `_shared/system_principles.md` 如实登记为「意图，不是机制」，来源与样本量见 `trade_lessons.md`（那 6 条**没有一条记了统计区间**）。<br>⇒ **需 owner 定：实现它们，还是明确放弃这两条**（用户画像第 4 条恰恰说「九丰能源等案例显示需要连续亏损冷却机制」，所以①有真实动因）|
| 23 | 「白线大于黄线」的歧义口径 | [R16 ⑧](../00_governance/research/R16_input_material_corrections.md) |
| 24 | `S**` 阈值 70/60 是否采纳（当前未采纳）| R16 ⑦ |
| 25 | M2 扫描是否继续（上一轮被暂停）| R10 |

## 维护约定

- **完成的项直接删**，不留 `~~删除线~~` —— 已完成的事实归入对应研究单元或版本日志。
- 发现旧文档里的行动项已被推翻时，**移到「已失效」表并写清原因**，不要静默删除：
  别人可能正照着做。
- 新增项必须写**出处链接**，否则无法判断它还成不成立。
