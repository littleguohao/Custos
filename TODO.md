# 待办清单

> **范围**：跨目录的待跑/待重跑/待收敛项。与同目录 `CHANGELOG.md` 的分工——
> **本文件记「还没做的事」，版本日志记「已经改了的策略规则」**，互不混放。
>
> 优先级按**「它阻塞了什么」**排，不按工作量：
> P0 = 阻塞其他事或 live 正在依赖 ｜ P1 = 已有结论悬空 ｜ P2 = 新验证 ｜ P3 = 技术债
>
> 最后更新：2026-08-19（#58 尾巴收口——radon E 级 19 个全部拆分清零、同批 D 级 53→26，过程中修复前轮拆分遗留的 `main_rally` 静默零产出回归（CHANGELOG v0.71）。此前：#49 持仓快照历史归档落地、`entities(date)` 生效（CHANGELOG v0.67）；#58 高复杂度函数拆分——研究侧 + live 链 F 级全拆完（CHANGELOG v0.68/v0.69）；#35 三因子补跑完成 ⇒ 均无跨窗口稳健性，owner 拍板降级「不再研究」（R2 第 16 条、CHANGELOG v0.70）。其余：#13 复核两案跑完 ⇒ 严变体终审否决（R7「#13 复核结果」节）；#25 跨窗复核跑完 ⇒ 两案过线但「cz3 首选换位」跨窗不成立（R10「#25 跨窗复核结果」节）；P2 待跑表已空）

## P0 · 阻塞项

（当前无 P0。）

## P1 · 重跑（结论悬空）

逐条对应研究单元的 `**重跑口径**` 字段，详见
[`research/README.md`「⚠️ 重跑清单」](governance/research/README.md)。

| # | 单元 | 重跑什么 | 前置 |
|---|---|---|---|

（当前无。原 #8 已于 2026-08-13 重算完成，见 R3「召回重算」节与 CHANGELOG v0.53。）

## P2 · 待跑（新验证）

| # | 事项 | 出处 | 备注 |
|---|---|---|---|

（当前无。原 #13 / #25 已于 2026-08-17 跑完并关闭：#13 严变体复核否决见 R7「#13 复核结果」节，#25 跨窗复核两案过线见 R10「#25 跨窗复核结果」节。）

⚠️ 教训（cross-window 复核，已归入 R10）：edge 集中在单一 regime 的方案首轮看起来都很好。

## P3 · 待收敛 / 技术债

| # | 事项 | 出处 |
|---|---|---|
| 58 | **高复杂度函数拆分**：✅ **E 级及以上全部拆完（2026-08-19，owner 拍板范围=只拆 E 级）**。F 级 2026-08-18 清零（v0.68/v0.69）；E 级 19 个 2026-08-19 清零（v0.71，明细见该条）——选股链 `apply_risk_downgrades`/`score_all`/`build_entry_reasons`/`check_macd_technics`/`check_non_one_wave`/`_bucket_pools`、因子/基建 `detect_wave_type`/`sector_mainstream.aggregate`/`market_quality_gate`/`load_bars_qlib`、复盘/持仓 `review_core.classify`/`review_enrichment.main`/`calc_mfe_mae.main`/`holding_sector_mapper.main`/`market_timing_scorer.score_indices`、研究侧 `run_bear_to_long_study` 两函数；同批 D 级 53→26。⚠️ 本轮修一个真回归：前轮拆分在 `main_rally_factor` 留下 4 个未定义 helper 被 except 吞掉 ⇒ `main_rally` scorer 静默零产出（v0.71）。等价验证：全量 4147 passed、周报/月报逐字节比对 MATCH、audit 套件通过。**剩余原则恢复**：D 级 26 / C 级 276（如 `n_structure_state` D28、`sector_concentration` D28）**下次因业务动这些文件时先拆** | 2026-08-19 收口（`reports/radon_cc.txt` 当日重生成，E/F 清零） |

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
| 26 | **板块信息利用整体优化**（owner 2026-08-12 扩展：「当前板块信息的处理还是很弱，没有利用好 TDX 的板块信息，后续需要整体优化」——TQ 板块数据利用不足）**+ 回调四项切换检查脚本化**（原定性保留）：主题切换/主题内分化/大小票/高低位四项检查做成确定性脚本并进**每日复盘报告**（每日几份报告内容随之调整）。原料大部分已有：`sector_phase`/`theme_tracker_report`（主题相位）、`fetch_market_cap`（市值层）、`holding_sector_mapper`（板块映射）、250 日高低点；输出字段以 `04_pullback_rotation.md` §六 的字段表为准。**+ 主线题材判定重设计**（owner 2026-08-13：「目前不准需要重新调整」「主线题材是交易预案的一部分，需要重新设计」）：盘后 §4 与盘前 §5 的主线节已压缩并标注待重设计（v0.57），重设计归入本条 | **大 feature，待排期** |

## P5 · contracts 梳理查出的问题（2026-08-06）

索引与核查结论见 [`contracts/README.md`](governance/contracts/README.md)。

（原 #31「冷却机制未实现」2026-08-12 并入 #51，同日随 #51 落地——见 CHANGELOG v0.48 与 `close_review/cooldowns.py`。）

## P6 · trades 梳理（2026-08-06）

| # | 事项 | 性质 |
|---|---|---|
| 32 | **reconcile_positions 已梳理（2026-08-12，owner「还需要优化和梳理」）**：① 超卖报错带分叉点（哪只票/哪笔/卖多少/当时持仓）；② 未给 baseline 时超卖报错直接附 `--baseline` 格式示例，baseline 文件缺失/非法/缺字段均 SystemExit + 格式引导（不再抛裸 traceback）；③ 17:00 链 mismatch/replay_failed 时 stderr 打 `[WARN]`（此前只躺 run log note，等于静默）。**剩余**：观察若干交易日、确认 `status=ok` 稳定后再考虑 `--strict` 转硬闸 | 已梳理，观察后转 strict |

## P7 · 因子层抽取查出的问题（2026-08-06）

| # | 事项 | 性质 |
|---|---|---|

（当前无。原 #35 三因子补跑 2026-08-18 完成：均无跨窗口稳健性，owner 拍板降级「不再研究」——`status` untested→needs_work，证据见 R2 第 16 条与 CHANGELOG v0.70。）

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

（原 #49「`rss_filter.entities(date)` 依赖持仓历史快照」已于 2026-08-18 落地：
`core/positions_history.py` 归档机制 + `entities(date)` 生效，见 CHANGELOG v0.67。）

## 需要 owner 拍板

（当前无。）

## 维护约定

- **完成的项直接删**，不留 `~~删除线~~` —— 已完成的事实归入对应研究单元或版本日志。
- 发现旧文档里的行动项已被推翻时，**移到「已失效」表并写清原因**，不要静默删除：
  别人可能正照着做。
- 新增项必须写**出处链接**，否则无法判断它还成不成立。
