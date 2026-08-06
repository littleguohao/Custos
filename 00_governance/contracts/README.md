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

### 🔴 两个实体完全没有生产者

| 实体 | 为什么没有 |
|---|---|
| **SkillEvidence** | Skill 架构遗留。`build_skill_contracts.py` + `skill_adapters.py` 已被 `generate_risk_and_sectors.py` 取代；`skill_id`/`entity_id`/`raw_ref`/`source_tools` 全仓零命中 |
| **BuyPlan** | `buy_strategy` 代码已移除。代码里只剩 `next_step="generate_buy_plan"` 这个**字符串标签**，不是被产出的对象；8 个字段全仓零命中 |

⇒ 两者已标为「**设计草案而非现行契约**」。买入计划的必备项清单现在在
[`../strategy/b1/03_execution_discipline.md`](../strategy/b1/03_execution_discipline.md)（人执行）。

### 🔴 一个被声明但从未实现的风控机制

**`RiskDecision.cooldown_list`** —— 全仓 `cooldown` / `冷却` / `blacklist` / `banned` **零命中**。
`risk_type` 枚举里的「冷却」同样没有实现，已一并移除。

⚠️ **这一条是安全相关的**：读契约的人会以为「触发止损的票会自动进冷却清单、不会被重复买入」，
而那个机制不存在。现有的近似能力只有 `forbidden_actions`（已实现）。

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
