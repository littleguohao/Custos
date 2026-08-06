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
