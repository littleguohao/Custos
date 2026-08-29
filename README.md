# strategy_team

确定性脚本驱动的 A 股交易策略系统：择时、选股、持仓研判、卖出风控、总控决策、复盘。

**核心原则：数据采集与判断全部由 Python 脚本完成，LLM 只做格式化与摘要，不参与策略判断。**

## 核心思想与架构原则

**核心思想（owner 2026-08-19 定案）**：不追求胜率，追求「一定胜率基础上的更高盈亏比」，
通过优化**止盈止损与仓位管理**达成持续盈利——评价任何信号看期望/盈亏比（胜率只是底线），
改进杠杆优先加在出场与仓位而非入场精度（研究口径与此一致：选股无 alpha，
出场侧是少数反复过线的地方）。全文见
[`strategy/_shared/system_principles.md`](governance/strategy/_shared/system_principles.md) 核心原则第 0 条。

**架构主线：因子 + 止损策略 + 止盈策略**（v0.81-v0.85，Phase A-E）：

- **因子**——实现全项目唯一一份（`core/factors/` + 注册表强制登记），权重外置
  `SCREEN_FORMULA_REGISTRY.json` 的 `scoring.weights`（调参不改代码，调前先回测）
- **止损/止盈**——规则唯一来源 `core/exit_rules.py` + `contracts/EXIT_RULES.json` 覆盖层；
  买入时计划点位持久化（选股 stop_loss_ref 不再断链）
- **研究→live 回流**——`research/strategy_grid.py` 因子×出场联合寻优，
  优胜配置的键名与 live `EXIT_RULES.json` 一致可直接拷入；**调任何分值/阈值先回测**
- 三件套全景：[`_shared/factor_exit_catalog.md`](governance/strategy/_shared/factor_exit_catalog.md)

**决策与风控哲学**：

- 个股服从板块，板块服从大盘；风控优先于买入；候选池仅为证据层，
  `chief_decision` 是最终输出层；所有计划必须可复盘——
  [`_shared/decision_priority.md`](governance/strategy/_shared/decision_priority.md)
- **无交易是默认**：B1 默认盘中不交易，除非用户确认或台账更新
- **0AMV** 是全链方向的主过滤器
- **fail-closed**：缺配置/缺数据直接报错而不兜底（如报告投递收件人必须显式配置）；
  运行时数据不入库（`data/` 等由 .gitignore 排除）
- **数据口径哲学**：本地优先（通达信 vipdoc / TQ-Local），HTTP 只做补齐；
  全链默认**前复权**——实测与坑见 [`governance/data/`](governance/data/)

**研究纪律**：

- 判据**预注册**，翻车如实记录——「推翻不删除，标推翻者」
- 策略类研究以**因子×止损×止盈三维搭配**为实验单元（机制不能单独评价）
- 研究单元只增不改，结论允许被推翻——[`governance/research/`](governance/research/)

**工程硬约束**（都有测试守着）：

- 路径只在 `paths.py` 定义一次；因子实现全项目唯一一份；
  有公认定义的指标只在 `indicators.py`；产物 schema 在 `contracts.py`，
  生产者落盘前 `require(...)`——**字段级真相以代码为准**（治理层 `.md` 不参与执行、会漂移）
- `src/` 按依赖分层（L0 基础→L1 数据→L2 因子→L3 决策→L4 编排），
  下层不得依赖上层——见 [`src/custos/README.md`](src/custos/README.md)

## 文档索引

本文件只保留思想框架；一切操作细节的去处：

- **运行与调度**（交易日五时点 cron、手动执行、报告体系）：
  [`governance/contracts/MASTER_WORKFLOW.md`](governance/contracts/MASTER_WORKFLOW.md)
- **运行门控**（改任何判定前先读）：[`contracts/RUNTIME_GATE.md`](governance/contracts/RUNTIME_GATE.md)
- **策略规则**：[`governance/strategy/`](governance/strategy/)
  （B1 见 [`strategy/b1/`](governance/strategy/b1/)、CZ 波段见 [`strategy/cz/`](governance/strategy/cz/)，各有 README 索引）
- **契约与运行时配置（代码直接依赖）**：[`governance/contracts/`](governance/contracts/)
- **版本记录 / 待办 / 实盘教训**：[CHANGELOG.md](CHANGELOG.md)（每条 ≤2 行，细节指向研究单元/commit）·
  [TODO.md](TODO.md) · [TRADE_LESSONS.md](TRADE_LESSONS.md)
- **持仓数据**：`data/trades/`（`master_trade_ledger.csv` 主台账、`current_positions.json` 持仓快照、
  `position_confirmations.json` — 交易日无交易确认标记）
- **开发操作**：环境用 uv（`uv sync`），测试 `uv run pytest -q`，
  静态检查 `scripts/audit.sh` / `scripts/audit.ps1`，代码风格 `ruff format`——
  细节见各脚本自身与 `pyproject.toml`
