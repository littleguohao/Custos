# 待办清单

> **范围**：跨目录的待跑/待重跑/待收敛项。与同目录 `CHANGELOG.md` 的分工——
> **本文件记「还没做的事」，版本日志记「已经改了的策略规则」**，互不混放。
>
> 优先级按**「它阻塞了什么」**排，不按工作量：
> P0 = 阻塞其他事或 live 正在依赖 ｜ P1 = 已有结论悬空 ｜ P2 = 新验证 ｜ P3 = 技术债
>
> 最后更新：2026-08-12（去冗精简：有结论/已落地条目删除——#1~#7、#9~#12、#14、#15、#20、#39、#45 主体、#51①、#53~#55；事实归宿在 R 系列文档与 CHANGELOG（#20 对照结论见 R10 与 v0.46），P8 覆盖率收口结论并入该节引言）

## P0 · 阻塞项

（当前无 P0。）

## P1 · 重跑（结论悬空）

逐条对应研究单元的 `**重跑口径**` 字段，详见
[`research/README.md`「⚠️ 重跑清单」](governance/research/README.md)。

| # | 单元 | 重跑什么 | 前置 |
|---|---|---|---|
| 8 | [R3](governance/research/R3_selection_discriminability_recall.md) | 只重算受影响窗的 `recall_by_band` 与净增益（「≥100% 带 −37.5%」这个被反复引用的数字）| — |

## P2 · 待跑（新验证）

| # | 事项 | 出处 | 备注 |
|---|---|---|---|
| 13 | 宽口径 `bottom_surge` 的 gate 语义修正后再议 | [R7](governance/research/R7_hypothesis_H2_b1b2b3.md) | 语义未修不必跑 |

⚠️ 教训（cross-window 复核，已归入 R10）：edge 集中在单一 regime 的方案首轮看起来都很好。

## P3 · 待收敛 / 技术债

| # | 事项 | 出处 |
|---|---|---|
| 58 | **高复杂度函数拆分**：radon cc 检出 C 级以上函数 267 个，集中在研究侧引擎——`backtest_factors.main` F(75)、`m2_stop_sweep._print_trade_group` F(69)、`backtest_factors.simulate_b1_trade` F(54)、`reconcile_qfq.gap_report` E(35)、`compare_signal_sets.main` E(34)。原则：**下次因业务动这些文件时先拆**，不为拆分而拆分 | 2026-08-11 静态检查（`reports/radon_cc.txt`） |

## ⚠️ 已失效的行动项（**别照着做**）

旧文档里这些「待跑」已被后续发现推翻或已完成。留着是为了**防止有人照单执行**：

| 原行动项 | 出处 | 为什么失效 |
|---|---|---|
| 「去幸存者偏差：`--data-source qlib`」 | R10 待跑 #3 | **2021 年后一只退市股都没有**，且 `2021_2026` bundle 已弃用（加法调整）⇒ 照做只会引入放大 13~21% 的收益，去偏一点没做到。近期窗口**目前无法去偏** |
| 「补探止损下界 `pct_03`/`pct_04`」 | R10 待跑 #4 | ✅ 已完成 —— **5% 是崖不是坡**（5%→4% 掉 42%）|
| 「`--exclude-zero-ret` 复跑」 | R3 | ❌ 已证是**错误的修正**（删的是合法样本），已标注勿用 |

## P4 · strategy 梳理查出的问题（2026-08-06）

索引与分类见 [`strategy/README.md`](governance/strategy/README.md)。
（原「两份文档入口不可达」已由 2026-08-06 重组解决：`STRATEGY_REGISTRY.json` + 各级 README + 测试强制登记。）

| # | 事项 | 性质 |
|---|---|---|
| 26 | **回调四项切换检查——已定性为「确实缺失的功能」（owner 2026-08-12）**：`04_pullback_rotation.md` 要求的主题切换/主题内分化/大小票切换/高低位切换四项检查全仓零实现，实际每日检查里也**没在做**（owner 确认）⇒ 不是空条款措辞问题，是缺失能力。**落地方向（owner 定）**：做成确定性脚本并在**每日复盘报告**里体现（每日的几份报告内容需随之调整）。原料大部分已有：`sector_phase`/`theme_tracker_report`（主题相位）、`fetch_market_cap`（市值层）、`holding_sector_mapper`（板块映射）、250 日高低点（高低位）。输出字段以 `04_pullback_rotation.md` §六 的字段表为准 | **待实现**（owner 已定性） |

## P5 · contracts 梳理查出的问题（2026-08-06）

索引与核查结论见 [`contracts/README.md`](governance/contracts/README.md)。

| # | 事项 | 性质 |
|---|---|---|
| 31 | **冷却机制未实现**：`RiskDecision.cooldown_list` 与 `risk_type` 的「冷却」枚举已从契约**删除**（不是标注——契约里没有它就不会有人依赖）。若确实需要「触发止损的票进冷却、不重复买入」，须单独立项 | 需 owner 判定是否要做 |

## P6 · trades 梳理（2026-08-06）

| # | 事项 | 性质 |
|---|---|---|
| 32 | **对账观察期**：`reconcile_positions` 已接入 17:00 链但**默认不阻断**。跑若干交易日、确认 `status=ok` 稳定后，再考虑 `--strict`（数量不一致 exit 1）。⚠️ 若台账非从零开始，须先准备 `--baseline` 期初持仓，否则会一直 `replay_failed` | 观察后决定 |

## P7 · 因子层抽取查出的问题（2026-08-06）

| # | 事项 | 性质 |
|---|---|---|
| 35 | `alpha_pvcorr` / `low_vol` / `momentum` 标 `untested` —— 实现了但没有独立的净值终审记录。按 R2 整体结论推定不可用，但**缺它们自己的证据**。要么补跑，要么明确降级为「不再研究」| 补证据或明确废弃 |
| 37 | ⚠️⚠️ **研究说没用、live 却在用**：R2 结论「S_shape 无 alpha，全市场阈值扫描无 lift」，而 `score_candidates.technical_score` 的**主路径**就是它——`sstar_level(s_star)` 直接出技术层级、参与候选表 A/B/C/D 分层。已在 `factors/s_shape.py` 元数据与 `factors.KNOWN_STATUS_USE_CONFLICTS` 显式登记（不静默放过、也不擅自改分层）。**需 owner 定**：分层要不要换掉 s_shape，还是维持（README 说 StockPool 只是证据层、买入由 chief_decision 裁决，所以维持也讲得通）| **需 owner 拍板** <br><br>⚠️ **2026-08-10 补**：分层**不只是标签** —— `daily_report.py:239` 是 `[x for x in pool.get('candidates',[]) if x.get('bucket') in ('A','B')][:10]`，**C/D 档根本不进盘前日报**。所以若排序无 alpha，这个 top-10 就是任取的 10 个，而人是照着日报看的。⇒ 「改名为形态分档」能减少「把分层当 alpha 信号」的误读，但**不解决「A/B 过滤在事实上决定了你看见谁」**。需要一并定：日报要不要继续只展示 A/B。<br>⚠️ 与 #38（1800 评分系统待完善）是同一件事的两面，建议合并为一个「1800 评分与日报展示」决策。 |
| 38 | **1800 评分系统待完善**（owner 2026-08-06 记）：`score_candidates` 的技术分/共振分/分层阈值整体还要打磨。与 #37（s_shape 是主路径但 R2 说无 alpha）同一片区域，宜一起做 | 后续迭代 |

## P8 · 测试覆盖率（2026-08-07 首次量化）

总覆盖率 **81.4%**（2026-08-11；首次量化时 78.2%）——**已收口停在这里**：
剩余缺口全在网络/子进程边界（`collect_incremental_market` 56%、
`market_timing_collector` 69%、`overseas_market_collector` 68%），
打桩收益递减（桩越像真实响应，测的就越是桩本身），验证交给端到端真跑
而不是继续加桩。

编排层已收口（此前是最大的空洞，**两个 live bug 都藏在这里**）：

    run_0850 96.7%   run_0905 90.4%   run_1445 82.7%
    run_1700 90.3%   run_1800 87.8%   daily_pipeline 85.5%

⚠️ 但**覆盖率高 ≠ 链是通的**：编排测试里 stage 一律被打桩，
从不真的去看被调用的脚本文件在不在 —— 2026-08-07 `daily_pipeline` 四个持仓
stage 全部指向不存在的文件、整条链硬失败，而 3481 条测试全绿
（该事故的例行核对已落地：`pipeline/run_log_check.py`）。

⚠️ **补测试前先问「这段代码该不该存在」** —— 首轮清点就删掉一个 0% 的死文件
（`sync_trades.py`：零调用 + 依赖的 config 不存在 + 3 个真 bug），
给该删的代码写测试是浪费。

| # | 事项 | 当前 |
|---|---|---|
| 45 | **鲜度判定第二步（需 owner 定）**：把 `as_of` 补进 `market_breadth`/`sentiment`/`turnover` 的契约。⚠️ 第一步刻意没做它：那是更宽的 fail-closed——会把「合法但没写 as_of」的段也打成陈旧；且补契约前必须先确认所有走**全量** `require()` 的生产者都写了该键，否则当场硬失败（#52 就是先补骨架再补契约才没炸）。<br>⚠️ 已知边界：`is_stale(sec, day=None)` 恒返回 False——三个调用点都传契约必填的 `date`，只有契约被违反时才可达。（第一步主体=统一 quality 词表 `SECTION_QUALITY`/`SECTION_NOT_FRESH`，已完成，见 CHANGELOG v0.40 与 `contracts.py`） | 第二步待 owner |
| 49 | `rss_filter.entities(date)` 的 `date` **未被使用** —— `current_positions.json` 无历史版本，回填历史日期会用今天的持仓筛那天的新闻。等持仓快照有历史版本后接上 | ⏸ 依赖持仓历史 |

## 需要 owner 拍板

| # | 事项 | 出处 |
|---|---|---|
| 25 | **M2 机制扫描是否继续**（R10 待跑 #25）。判定证据已齐（2026-08-11）：① s3000 全扫描里 margin>3pp 且已实现为正的方案**全部含 0AMV**（纯出场无一通过）；② cross-window 复核里纯出场改善**全部翻负**（trail_08 +0.122→−0.214R 等），只有 0AMV 系稳定；③ #20 余量口径对照 pct/atr 均否决。**我的建议**：纯出场维度的 M2 扫描**停止**（边际已空），保留现有 trail_08/scale_out_08 作为出场配置即可；若继续，只扫「0AMV × 出场」的组合维度 | [R10](governance/research/R10_mechanism_M2_stops.md) |
| 51 | ② **胜率降仓 —— 未实现，待 owner 定**：「当月短线胜率 < 35% → 降低短线仓位」。只有研究脚本算胜率，无任何 live 组件据此降仓。月度胜率样本量小、规则易被噪声触发，且同样面临「没有仓位决策可拦」的问题（仓位建议目前由 `chief_decision` 的 `total_position_range` 给，是文本不是执行）。<br>⚠️ `README.md:160`「买入计划由 `chief_decision` 统一裁决」与代码不符，待②定了一起改。<br>（① 连亏冷却已完成：`close_review/loss_streak.py`，每日 `final_close_review` 与每周 `weekly_review` 各出一节——刻意做成复盘节而非 gate：`buy_actions` 是字面量空表，闸门会挂在空处） | ②待 owner |

## 维护约定

- **完成的项直接删**，不留 `~~删除线~~` —— 已完成的事实归入对应研究单元或版本日志。
- 发现旧文档里的行动项已被推翻时，**移到「已失效」表并写清原因**，不要静默删除：
  别人可能正照着做。
- 新增项必须写**出处链接**，否则无法判断它还成不成立。
