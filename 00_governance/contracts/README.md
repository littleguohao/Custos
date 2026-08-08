# 契约层索引

> `00_governance/contracts/` 存放**契约与运行时配置**：代码直接依赖的东西。
> 与邻居的分工——`research/` 是「测出来的结论」，`strategy/` 是「我们决定怎么做」，
> 本目录是「**代码照着做的约定**」。
>
> ⚠️ **契约的第一属性是「真的被遵守」。** 契约说 X 而代码做 Y，契约就不是契约而是谎言，
> 且读它的人不会知道。所以本目录的每一项都要能回答：**谁在读它 / 谁在产它**。

## 分类：按「代码怎么用它」

### ① 代码直接读的配置（改了立刻影响运行）

| 文件 | 行 | 代码引用 | paths 常量 | 读它的地方 |
|---|---|---|---|---|
| [CN_TRADING_CALENDAR.json](CN_TRADING_CALENDAR.json) | 38 | **11** | `CALENDAR_FILE` | `trading_calendar.py` 及五个 runner 的交易日判定 |
| [SCREEN_FORMULA_REGISTRY.json](SCREEN_FORMULA_REGISTRY.json) | 103 | 6 | ✅ | `screening/formula_screen.py` 公式初筛 |
| [RSS_SOURCE_REGISTRY.json](RSS_SOURCE_REGISTRY.json) | 91 | 1 | ✅ | `news/rss_collector.py` |
| [RSS_FILTER_CONFIG.json](RSS_FILTER_CONFIG.json) | 84 | 1 | ✅ | `news/rss_filter.py` |
| [RSSHUB_PRIVATE_ROUTE_CANDIDATES.json](RSSHUB_PRIVATE_ROUTE_CANDIDATES.json) | 23 | 1 | ✅ | `news/` 私有路由候选 |

⚠️ **配置路径只在 `07_tools/paths.py` 定义一次**，模块不得自己拼 `"00_governance"`
（由 `tests/test_base_path_depth.py` 强制）。

### ② 数据契约（描述产物形状）

| 文件 | 行 | 状态 |
|---|---|---|
| [DATA_FLOW_CONTRACT.md](DATA_FLOW_CONTRACT.md) | 246 | ⚠️ 8 实体中 **2 个无生产者**（见下）|
| [INCREMENTAL_TRADE_LEDGER.md](INCREMENTAL_TRADE_LEDGER.md) | 40 | 增量台账口径 |

### ③ 工作流文档（人读，描述编排）

| 文件 | 行 | 状态 |
|---|---|---|
| [MASTER_WORKFLOW.md](MASTER_WORKFLOW.md) | ~370 | ⚠️ 含 1 个未实现的报告（月度复盘）|
| [RUNTIME_GATE.md](RUNTIME_GATE.md) | 44 | ⚠️ **改门控判定前必读**。退出码被 cron 直接消费（3/4/5 穿透 `daily_pipeline`）、`--require-*` 会真的中断链路 ⇒ 算契约不是说明。含评分权重、`blocked` 覆盖率规则、加仓授权五条件、各时点策略、2026-07-30 硬闸事故记录。2026-08-07 从根 README 抽出 |
| [SCREENING_WORKFLOW.md](SCREENING_WORKFLOW.md) | 291 | 18:00 选股链编排 |

## ⚠️ 2026-08-06 核查查出的契约失真

逐字段比对契约声明与代码实际产出，查出 **7 处**。已全部就地标注或改正。

### ✅ 已删除：两个无生产者的实体 + 一个未实现的机制

按「**核查覆盖 → 抢救独有内容 → 才可删**」的顺序处理（同 `../strategy/README.md` 的废弃流程）：

| 删除项 | 为什么可删 | 独有内容去哪了 |
|---|---|---|
| **SkillEvidence** 实体 | Skill 架构遗留；且它描述的「**统一证据信封**」实际不存在 —— `as_of`/`facts`/`signals`/`status`/`risk_flags` 确实散落在各产出里，但**没有任何一份产出同时具备它们**（实测 `generate_risk_and_sectors.py` 只有 signals+risk_flags，`b1_holding_state.py` 只有 as_of+facts+signals）| 无独有内容 |
| **BuyPlan** 实体 | `buy_strategy` 代码已移除，只剩 `next_step="generate_buy_plan"` 字符串标签 | **结论四档 / 买入方式五类 / 最大亏损比例**已抢救到 [`../strategy/b1/03_execution_discipline.md`](../strategy/b1/03_execution_discipline.md) |
| **`RiskDecision.cooldown_list`** | 声明过但**从未实现**的风控机制（全仓 `cooldown`/`冷却`/`blacklist` 零命中）| 是否要真做见 TODO #31 |

⚠️ **删掉比标注更彻底**：契约里没有它，就不会有人以为它存在。
删除记录留在 `DATA_FLOW_CONTRACT.md` 的实现状态表里 —— 否则下次有人会重新加回来。

### 未删的两项及理由

| 项 | 为什么不删 |
|---|---|
| `MASTER_WORKFLOW §七` **月度复盘** | 它是**想要的功能**（TODO #30：要么实现要么降级），删掉会丢掉已设计好的时间范围/核心目标/固定结构/核心指标/正式产物。已标「目标设计，不是现行流程」 |
| `INCREMENTAL_TRADE_LEDGER.md` | 核查后**有效** —— 它描述的唯一主文件与 `transaction_id` 在代码中都存在，只是没有文件名级引用 |

### ⚠️ 四处字段名/形状与实际不符（已按代码改正）

| 契约原写 | 实际 |
|---|---|
| `MarketState.zero_amv_state`，枚举「做多区间\|中性\|空头区间」 | `amv_state` / `amv_zone` / `effective_state`，枚举「做多\|中性\|空头」 |
| `StockCandidate.technical_sources[{...}]` 嵌套 | **平铺** `technical` / `technical_level` / `technical_score` / `signals` |
| `StockCandidate.raw_rank` | 不存在（有 `rank_score` / `stock_rank` / `sector_rank`）|
| `MASTER_WORKFLOW` 盘前「08:30」 | **08:50 采集 + 09:05 出报告** |

**改正方向一律「以代码为准」** —— 代码是实际在跑的，文档不是。

### ⚠️ 一个未实现的报告 + 一份埋在契约里的待办

- `MASTER_WORKFLOW §七` **月度复盘**：全仓 `月度`/`month_review` 零命中（周度有 `weekly_review.py`）
  ⇒ 已标为「目标设计，不是现行流程」。
- `MASTER_WORKFLOW §十二` 原挂着 8 条「当前需要调整的旧设计」。**待办不该埋在契约文档里** ——
  找不到，也不会被跟踪。逐条核实后：**6 条已完成、1 条部分（月度）、1 条未做**。
  未做的第 8 条（`report_id` / 规则版本 / 数据截止 / 输入清单，关乎**可审计与可重跑**）
  已移入 `05_strategy_versions/TODO.md`。

## 写入规范

- **加字段前先确认谁产它。** 契约里写一个没人产的字段，比不写更糟 ——
  它让下游以为可以依赖。
- 字段名以**代码**为准。发现不符时改文档，并记为一次口径修正。
- 无生产者的实体/字段必须显式标 🔴，并写清「保留它的理由」（通常是重建时的目标形状）。
- 配置类 JSON 的路径只在 `paths.py` 定义，不要在模块里拼。
- 待办不要写进契约文档，写进 `05_strategy_versions/TODO.md`。

## 可执行契约（`07_tools/contracts.py`）

⚠️ **本目录的 `.md` 是文档，不参与执行，所以会漂移** —— 这份 README 自己就记着
7 处契约失真的核查结论，而其中 `SkillEvidence` 那个实体**项目里从来没有过**
任何产出同时具备它描述的 8 个字段。

2026-08-07 起，**钱的路径与硬失败链上的产物 schema 变成可执行代码**：

    07_tools/contracts.py     SPECS 是唯一来源；生产者落盘前 require(...)
    tests/test_contracts.py   每条校验规则标注它对应的**真实 bug**
    tests/test_architecture_layers.py::test_money_path_producers_validate_before_write
                              强制 11 个生产者都在落盘前校验

覆盖 **24 个产物** —— 全部按日期命名的 JSON 产物都已纳入，5 类刻意豁免（执行痕迹 / md 报告 / 人工输入 / 副本 / 可选产物）的理由写在 `contracts.py`「第五批」注释块，并由 `tests/test_architecture_layers.py::TestContractCoverageOfArtifacts` 强制：**新增产物时忘了建契约会让它挂**。执行策略是「写严、读松」：
生产者不合规当场 SystemExit，消费者只拿结构化结论、按既有降级策略裁决
（README 记着 2026-07-30 悄悄收紧硬闸导致 17:00 链失败的教训）。

三个由真实数据逼出来的概念，改 spec 前先读它们的说明：

| 概念 | 用在哪 | 为什么 |
|---|---|---|
| `nullable` | `market_timing_input.amv_0.as_of` | 渐进填充产物里**刻意留 None** 的字段；每个都必须说清理由 |
| `only=(...)` | `merge_incremental_market` | **责任边界**：部分写者只为自己写的字段背责，支持点号路径，一律去掉 `required` |
| 分支型 | `holding_quotes` / `sector_technical_summary` / `holding_technical_summary` | `available=False` 时后面的字段**全不存在**，契约只能要求普遍字段 |
| 能力边界 | —— | 只查**字段**，查不出**跨字段矛盾**（如 `transport_verified=False` 却 `confirmed=True`）；那类不变量靠单元测试 |

`DATA_FLOW_CONTRACT.md` 仍是**给人读**的数据流全景；字段级真相以 `contracts.py` 为准。
