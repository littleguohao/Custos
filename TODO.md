# 待办清单

> **范围**：跨目录的待跑/待重跑/待收敛项。与同目录 `CHANGELOG.md` 的分工——
> **本文件记「还没做的事」，版本日志记「已经改了的策略规则」**，互不混放。
>
> 优先级按**「它阻塞了什么」**排，不按工作量：
> P0 = 阻塞其他事或 live 正在依赖 ｜ P1 = 已有结论悬空 ｜ P2 = 新验证 ｜ P3 = 技术债
>
> 最后更新：2026-08-12（#20 tick_buffer 重设计已实现待对照（--stop-buffer pct/atr + m2 方案 low_pct_03/atr_02），见 CHANGELOG v0.46；#17 宇宙/窗口钉死开关默认改开（--no-* 显式关），完成删除，见 CHANGELOG v0.45；#48 RSS 代码命中加紧邻量词否定（方案 A）、#18 收口（load_bars_csv 加法口径告警 + R15 stale 行修正），两者完成删除，事实见 CHANGELOG v0.44；#56 保留项①–⑤ owner 拍板收口，判定精度全仓统一 round-2，口径变更见 CHANGELOG v0.43，#56 完成删除；#44 定案：三个 stale 研究脚本全部删除；#57 类型化五批收口；#53 例行核对落地）

## P0 · 阻塞项

（当前无 P0。）

✅ **#1~#4 已完成（2026-08-08/09）**：vipdoc 已重下完整历史（600000 自 2000-01-04 共 6327 根；
`--gap-report` 抽样最早 2000-01-04、vipdoc 总数 5538）。#3 去偏价值栏：`2021_2026`
有 20 只（0.4%）、`2006_2020` 有 230 只（5.9%）不在 vipdoc——退市票仍只在老 bundle。
#4 R4 重跑 16 格完成：0AMV 主过滤器地位三熊窗重取（~15pp，含退市老窗方向一致）；
板块相位「+4~6pp 辅助」**未通过**重跑（四窗两正两负）已降级。详见
[R4](governance/research/R4_timing_amv_sector.md) 与 `artifacts/logs/r4_rerun/`。
**后续**：R1 量级重写（#9）的前置已就绪一半（#4 done，还差 #5 #6）。

## P1 · 重跑（结论悬空）

逐条对应研究单元的 `**重跑口径**` 字段，详见
[`research/README.md`「⚠️ 重跑清单」](governance/research/README.md)。

| # | 单元 | 重跑什么 | 前置 |
|---|---|---|---|
| 8 | [R3](governance/research/R3_selection_discriminability_recall.md) | 只重算受影响窗的 `recall_by_band` 与净增益（「≥100% 带 −37.5%」这个被反复引用的数字）| — |

✅ **#5/#6 已完成（2026-08-09）**：m2 全扫描 27 方案 × s3000（钉死宇宙+窗口、已实现口径）。
R10：可用 margin 只在含 0AMV 的方案（pct_05_amv +7.8pp / pct_12_amv_cz3 +11.1pp）；
纯出场最强 trail_08（已实现 +0.116R）不过 3pp。R9：scale_out 四臂单调递增，∂E/∂b>0 成立。
✅ **#7 已完成（2026-08-11）**：R2 归因分离——换宇宙主因（5.6~12pp）、换源次因（2.7~2.8pp）、
加法放大另计 13~21pp（R14）；8 格 baseline 全赢 rqi 复证选股无 alpha。
✅ **#9 已完成（2026-08-10）**：R1 量级重写——per-trade edge 只在 0AMV 做多期成立
（+0.454R vs 基准 −0.009R）；组合量级 = 做多期年化 ~10%、回撤个位数、熊市空仓。
**后续**：通过方案 `--cross-window` 复核（2026-08-10 夜跑批中）；#15（组合层 --top-n 宽度）前置已解。

## P2 · 待跑（新验证）

| # | 事项 | 出处 | 备注 |
|---|---|---|---|
| 13 | 宽口径 `bottom_surge` 的 gate 语义修正后再议 | [R7](governance/research/R7_hypothesis_H2_b1b2b3.md) | 语义未修不必跑 |

✅ **P2 首轮已完成（2026-08-11）**：#10 H3 否决（rsi_strong 系跨窗翻负、div 系两窗方向不一致）；
#11 H4 否决——两种 CROSS 口径 0 触发不是稀缺是**公式自相矛盾**（主升占比≥80% 与 RSI<20+CCI<−100
互斥，flow∩超卖共现 0/4280 日，要救需改写条件组合=新假设）；
#12 突破回踩否决（主窗 +2.1% 赢基准、跨窗 +0.08% 归零）；#14 满仓 2 只否决（满仓 2/3/5 只
全部深亏 −35%/−37%/−22%）；#15 top-n 宽度无 edge（N=0/2/5 全小幅负、非单调）。
✅ **#39 反转K 区间验证完成**：±1.5/±2/±2.5/不对称四臂均未独立转正，更宽略优
（±2.5 已实现 −0.015R vs ±2 的 −0.017R），差异小 ⇒ 维持现行 ±2，不建议改。
✅ **cross-window 复核完成**：含 0AMV 方案全部跨窗稳（amv +0.468→+0.297R、pct_05_amv
+0.209→+0.200R）；纯出场改善全部跨窗翻负（trail_08 +0.122→−0.214R）⇒ R10 结论加固。
⚠️ 教训保留：edge 集中在单一 regime 的方案首轮看起来都很好。

## P3 · 待收敛 / 技术债

| # | 事项 | 出处 |
|---|---|---|
| 20 | `tick_buffer` 参数重设计：**已实现待对照**（2026-08-12，owner 拍板「两个都实现成可选 stop 模式，同批样本对照跑一轮再定取舍」）。`backtest_factors` 新增 `--stop-buffer {tick,pct,atr}`（默认 tick=旧行为逐位不变）+ `--stop-pct-buffer`（默认 0.3 ≈ 10 元股 tick_3）+ `--stop-atr-buffer`（默认 0.2×ATR(14)，Wilder `indicators.atr_series`）；m2 方案表 A 组加 `low_pct_03`/`atr_02`（已进 R_DENOM/EXIT_SIDE 分类，按期望%/margin 判）。**待跑（需目标机，本机无 vipdoc/S_DATA）**：① 小样冒烟 `uv run python src/custos/research/m2_stop_sweep.py --sample 300 --only A_stop_low -j 4`；② 正式对照按 run_m2_sweep.cmd 口径 `uv run python src/custos/research/m2_stop_sweep.py --sample 3000 --only A_stop_low -j 6`（#17 起窗口/宇宙默认已钉死 DEFAULT_WINDOW 口径，两轮的 tick_3/pct_03/atr_02 同批可比）。跑完按期望%/margin 定余量口径取舍，再决定是否 deprecate tick 模式 | [R10](governance/research/R10_mechanism_M2_stops.md) |
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
| 45 | ✅ **已按窄修法实现（2026-08-10 owner 拍板）**：`is_stale` 的判据由 `quality == "stale"` 改为 `quality in SECTION_NOT_FRESH`（见 v0.40）。查因过程中发现比原描述更根本的问题：**两个生产者用不同词表**（merge 出 `raw_only`、collector 出 `degraded`），而消费者只认 `"stale"` ⇒ 两个词都被当成新鲜。已在 `contracts.py` 立 `SECTION_QUALITY`/`SECTION_NOT_FRESH` 统一词表，并加一条**从生产者源码取字面量**的守卫：生产者新增未登记的 quality 值时当场报警（植入验证通过）。<br><br>**剩余未做（第二步，需 owner 定）**：把 `as_of` 补进 `market_breadth`/`sentiment`/`turnover` 的契约。⚠️ 本次**刻意没做**，因为那是更宽的 fail-closed —— 会把「合法但没写 as_of」的段也打成陈旧；且补契约前必须先确认所有走**全量** `require()` 的生产者都写了该键，否则当场硬失败（#52 就是先补骨架再补契约才没炸）。<br>⚠️ 另一个已知但未修的口子：`is_stale(sec, day=None)` 恒返回 False。三个调用点都传 `d.get("date")`，而 `date` 是契约必填字段 ⇒ 只有契约被违反时才可达，留作已知边界。 | ✅ 主体已完成；第二步待 owner |
| 49 | `rss_filter.entities(date)` 的 `date` **未被使用** —— `current_positions.json` 无历史版本，回填历史日期会用今天的持仓筛那天的新闻。等持仓快照有历史版本后接上 | ⏸ 依赖持仓历史 |
| 53 | **端到端真跑 `daily_pipeline`** —— ✅ **首次真跑已完成（2026-08-08）**：目标机按 `--date 2026-08-07` 重跑全部五个 runner（证据：`artifacts/logs/2026-08-07_*_run_log.json`，started_at 为 08-08 深夜）。1800 选股链 11 stage 全 OK；0850 7/7、0905 3/3、1700 completed。且立刻抓到一个下周一就会发作的真 bug：`holding_quotes` 契约 spec 把 `indices` 误写为 dict（生产者是 list），被 1700 链契约校验报出 → 已修（commit `1f118b1`）。⚠️ **残余缺口**：① 1445 的盘中快照类 stage 无法用历史日期复现 fresh 校验（本次 `close_review` 因 `captured_at` 非目标日失败，属预期）；② `collect_fund_flow` 网络失败为 best-effort 不阻断。⇒ 例行核对已实现（2026-08-12，`pipeline/run_log_check.py`：缺日志/意外失败 exit 1，#53 记录的两类预期内失败不告警）；cron 条目待 owner 加（建议每交易日 18:30） | ✅ 已完成（cron 待加） |
| 54 | **生产代码覆盖缺口（2026-08-11 大幅收口）**：总覆盖率 78.5% → **81.4%**；原清单十项里 **4 项已 ≥91%**（`market_timing_scorer` 36→95、`refresh_market_indices` 17→91、`holding_sector_mapper` 20→93、`daily_report` 65→84），`adjust_factors` 60→72、`collect_holding_quotes` 65→73、`financials` 70→78。<br><br>**剩三个 <70%，缺口全在网络/子进程边界**：`collect_incremental_market` 56%（缺 55，主要是 `main` 的 30 行编排 + yahoo 抓取）、`market_timing_collector` 69%（缺 48，`read_day`/`_vipdoc_rows`/`main` 需真 vipdoc）、`overseas_market_collector` 68%（缺 47，`fetch_chart` 的 28 行是 Yahoo HTTP 解析）。⚠️ 这三处的剩余部分**打桩收益递减**：桩越像真实响应，测的就越是桩本身；真正能验它们的是 #53 的端到端真跑。⇒ **建议就此停在 81%**，把余下的验证交给例行真跑而不是继续加桩。 | 主体已收口；剩余三项建议交端到端 |
| 55 | **`audit_*` 测试家族按模块重组**：11 文件 4821 行（占测试 15%），按**审计轮次**（P0/P1/P2/P3/opt）组织而非按模块 —— 找「`technical_monitor` 的测试」要翻 9 个文件（含 4 个 audit）。⚠️ 2026-08-07 评估后**决定先不动**：搬 4800 行测试的风险大于可读性收益（同日搬迁脚本已两次出错：按行替换撞上分号连写、正则把注释当导入名）。若哪天要做，前置条件是先有一次真跑验收（#53） | 已评估，暂不动 |

## 需要 owner 拍板

| # | 事项 | 出处 |
|---|---|---|
| 51 | **两条「卖出风控硬规则」原为零实现**。<br><br>① **连亏冷却 —— ✅ 已按 owner 2026-08-10 的落点实现**：落在**复盘环节**（不是闸门）—— 新增 `close_review/loss_streak.py`，每日 `final_close_review` 与每周 `weekly_review` 各出一节「连亏检查（全台账）」。⚠️ **刻意不做成 gate**：`chief_decision_report` 的 `buy_actions` 是字面量空表（源码注释 `buy_actions always empty`）⇒ 自动链里没有买入决策可拦，闸门会挂在空处。口径：只用 `match_status="full"` 的平仓单（partial 系统性少算）、按 **`net_pnl`**（扣费后）判亏、**被任何一次盈利打断即归零**（「连续」的原意，不是历史累计亏损次数）；配平复用 `weekly_review.fifo_pair`，不另写 FIFO。被排除的单子数如实上报，台账缺失时报 `unavailable` 而非「无连亏」。<br><br>② **胜率降仓 —— 仍未实现，待 owner 定**：「当月短线胜率 < 35% → 降低短线仓位」。只有研究脚本算胜率，无任何 live 组件据此降仓。月度胜率样本量小、规则易被噪声触发，且同样面临「没有仓位决策可拦」的问题（仓位建议目前由 `chief_decision` 的 `total_position_range` 给，是文本不是执行）。<br>⚠️ `README.md:160`「买入计划由 `chief_decision` 统一裁决」与代码不符，待②定了一起改。 | ①已完成（2026-08-10）／②待 owner |

## 维护约定

- **完成的项直接删**，不留 `~~删除线~~` —— 已完成的事实归入对应研究单元或版本日志。
- 发现旧文档里的行动项已被推翻时，**移到「已失效」表并写清原因**，不要静默删除：
  别人可能正照着做。
- 新增项必须写**出处链接**，否则无法判断它还成不成立。
