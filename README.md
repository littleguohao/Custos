# strategy_team

确定性脚本驱动的 A 股交易策略系统：择时、选股、持仓研判、卖出风控、总控决策、复盘。

**核心原则：数据采集与判断全部由 Python 脚本完成，LLM 只做格式化与摘要，不参与策略判断。**

## 快速开始

需要 Python 3.11+（用 [uv](https://github.com/astral-sh/uv) 管理）、通达信客户端（本地日线源）。

```bash
uv sync
uv run pytest -q                    # 全量测试（3600+ 用例）
```

两个环境变量：

- `TDX_ROOT` —— 通达信安装目录（默认 `E:\new_tdx64`）
- `S_DATA_ROOT` —— 回测数据根，含 `Q_DATA/CSV_DATA`（默认 `E:\S_DATA`）

持仓数据放 `data/trades/`：`master_trade_ledger.csv`（主台账）、
`current_positions.json`（持仓快照）、
`position_confirmations.json` — 交易日无交易确认标记。

## 目录结构

```
governance/   治理层，按生命周期分四类，各有 README 索引
                 strategy/ 规则   data/ 数据源现状
                 research/ 回测研究（只增，结论会被推翻）
                 contracts/ 契约与运行时配置 —— **代码直接依赖**
data/         运行时数据（gitignore）
artifacts/    产物三合一：reports/{daily,weekly,monthly}/ 日报·周报·复盘
              logs/ 运行日志与诊断输出（gitignore）
TODO.md  CHANGELOG.md  TRADE_LESSONS.md   版本记录（实盘复盘→进化）
src/          全部脚本（core/ datasource/ pipeline/ research/，见 src/README.md）
tests/        pytest
```

`src/` 按依赖分层，下层不得依赖上层（有测试强制）：
**L0** 基础（`core/` 顶层：`paths` `code_utils` `indicators` `contracts` …）→
**L1** 数据（`datasource/`：`collect/` `local_tdx/` `news/` + 数据刷新脚本）→
**L2** 因子（`core/factors/` `core/trades/`）→
**L3** 决策（`pipeline/` 的 `screening/` `market_timing/` `holdings/` `close_review/`）→
**L4** 编排（`pipeline/` 顶层 `run_*.py` `daily_pipeline.py`，及 `research/`）。

四条硬约束，都有测试守着：

- **路径只在 `paths.py` 定义一次** —— 不要自己拼 `BASE / "governance" / ...`
- **因子实现全项目唯一一份**（`factors/` + 注册表）
- **有公认定义的指标只在 `indicators.py`**（RSI / MACD / 振幅 / KDJ …）——
  因子自己的打分逻辑留在因子里，判据是「是否存在口径选择」
- **产物 schema 在 `contracts.py`**，生产者落盘前 `require(...)`；
  治理层 `.md` 不参与执行、会漂移，**字段级真相以代码为准**

## 日常运行

交易日五个时点，由 OpenClaw cron 触发（配置以 `state/openclaw.sqlite` 的 `cron_jobs` 表为准）：

| 时间 | 脚本 | 做什么 |
|---|---|---|
| 08:50 | `run_0850.py` | 公告 / 海外行情 / RSS 采集 + 增量市场数据 |
| 09:05 | `run_0905.py` | 盘前日报 |
| 14:45 | `run_1445.py` | 尾盘建议（以实时价重算 B1） |
| 17:00 | `run_1700.py` | 盘后复盘 |
| 18:00 | `run_1800.py` | 每日选股独立链（与三份报告分离） |

周六 10:07 跑 `weekly_review.py` 周度复盘。手动执行同名脚本即可：

```bash
uv run python src/custos/pipeline/run_1445.py
uv run python src/custos/datasource/trading_calendar.py --check-date 20260717
uv run python src/custos/research/__main__.py        # 研究/回测统一入口
```

## 数据源

**本地优先**（通达信 vipdoc / TQ-Local），HTTP 只做补齐；
全链默认**前复权**（基于通达信 xdxr 权息自算）。
东财补齐北交所行情、真市值、PIT 财务、资金流、股票名称。
实测性能、覆盖率与风险等级见 [`governance/data/`](governance/data/)。

三个反复踩的坑：

- **mootdx 不支持北交所** —— 920xxx 走东财 push2
- **mootdx Reader 返回 DatetimeIndex 而非列** —— 传入分析前要 `reset_index()`
- **股票名称表是 ST 硬排除的唯一依据** —— 残缺表落盘会让 ST 过滤静默失效

## 策略与门控

**B1 波段策略**：BBI（预警而非权威）、N 结构、反转 K（J<13 + 极致缩量 + 收盘 ±2% +
振幅 ≤7%，**是观察点不是买点**）、P0~P3 优先级。
规则见 [`governance/strategy/b1/`](governance/strategy/b1/)。

**决策优先级**：个股服从板块，板块服从大盘；风控优先于买入；候选池仅为证据层，
`chief_decision` 是最终输出层；所有计划必须可复盘。
⚠️ 同一事实的多个读数按**证据新鲜度**取（14:45 以实时价重算的 B1 为准）——
见 [`_shared/decision_priority.md`](governance/strategy/_shared/decision_priority.md)。

**运行门控** `runtime_gate.py` 检查交易日历、持仓与技术数据新鲜度、市场质量、加仓授权，
退出码（3 非交易日 / 4 质量 blocked / 5 持仓 blocked）会穿透 `daily_pipeline` 供 cron 判定。
各时点是否启用硬闸、评分权重、以及 2026-07-30「硬闸叠加导致 17:00 链失败」的事故记录 ——
见 [`contracts/RUNTIME_GATE.md`](governance/contracts/RUNTIME_GATE.md)。**改任何判定前先读它。**

## 注意事项

- **0AMV** 是全链方向的主过滤器：`run_1700` 从指南针自动写 confirmed 观测，
  `amv_state` 据真值切 regime（单日 >+4% 进多头 / <−2.3% 进空头）；
  指南针不可用时回退人工确认
- **无交易是默认**：B1 默认盘中不交易，除非用户确认或台账更新
- **运行时数据不入库**：`data/` 等由 .gitignore 排除，只留 `.md` 模板
- 报告投递由 `feishu_report_publisher.py` 完成，**收件人必须显式配置**
  （`FEISHU_TO_OPEN_ID`），缺配置直接报错而不兜底
