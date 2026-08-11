# src 工具层

策略团队确定性脚本目录：数据采集、指标计算、报告渲染全部由本目录脚本完成,LLM 只负责格式化和摘要。

原则:

- 所有路径统一从 `core/paths.py` 导入,不硬编码。
- 所有输出统一写入 `strategy_team/data/`(日志进 `artifacts/logs/`)。
- 五个时点 runner(`pipeline/run_0850/0905/1445/1700/1800`)是唯一调度入口,共享行为收敛到 `core/pipeline_kit.py`。

## 目录结构（2026-08-11 归堆）

```
src/
├── core/                  # L0 基建 + L2 因子/台账
│   ├── paths.py           # 全仓路径常量（唯一来源）
│   ├── pipeline_kit.py    # runner 共享件:run_stage、交易日历门、md 摘要
│   ├── indicators.py      # 有公认定义的技术指标的唯一实现（J 值/BBI/MACD/振幅…）
│   ├── code_utils.py / fmt.py / net_retry.py
│   ├── contracts.py       # 产物 schema 唯一来源（零内部依赖）
│   ├── runtime_guards.py / runtime_gate.py   # P0 运行时守卫 + 门控 CLI
│   ├── b1_thresholds.py / report_audit.py
│   ├── factors/           # L2 可复用因子（选股/研究共用,见 governance/strategy/_factors/）
│   └── trades/            # L2 交易台账同步与标准化、增量台账、持仓对账
├── datasource/            # L1 数据采集与刷新（只许依赖 L0/L1）
│   ├── s_data.py          # qlib/CSV 只读 loader
│   ├── trading_calendar.py  # A 股交易日历刷新与查询（TDX JSON-RPC）
│   ├── collect/           # 采集:持仓/指数报价、增量行情、资金流、在线行情
│   ├── local_tdx/         # 本地通达信数据封装（见 local_tdx/README.md）
│   ├── news/              # RSS 新闻采集与过滤、盘前情报 schema、盘后摘要
│   ├── breadth_basis.py   # 涨跌家数口径真值来源
│   ├── refresh_eod_klines.py / refresh_market_indices.py  # EOD/指数刷新
│   ├── sync_compass_amv.py / tdx_ext_quotes.py / overseas_market_collector.py
├── pipeline/              # L3 生产 + L4 编排
│   ├── run_0850.py ~ run_1800.py   # 时点入口 runner（调度器调用,共五个）
│   ├── daily_pipeline.py  # 日终完整管线（0905/1700 的部分阶段复用）
│   ├── daily_report.py    # 从 ChiefDecision 渲染统一日报
│   ├── generate_risk_and_sectors.py / feishu_report_publisher.py
│   ├── screening/         # 每日选股链（公式初筛→充实→打分→表格,
│   │                      #  见 governance/contracts/SCREENING_WORKFLOW.md）
│   ├── market_timing/     # 市场择时评分、AMV/0AMV 状态、板块映射、微信摘要
│   ├── holdings/          # 持仓侧:持仓状态机、持仓技术分析、组合复盘
│   └── close_review/      # 收盘复盘（14:45 链核心）、周复盘、MFE/MAE
└── research/              # L4 研究回测与分析（只读管线数据,不触碰 live 链）
```

## 分层与依赖方向

数字越小越底层；同层互相依赖允许，**下层不得依赖上层**（`tests/test_architecture_layers.py`
强制，AST 依赖图 + 无环 + 同名唯一性）。

- **L0** `core/` 顶层：`paths` `code_utils` `indicators` `contracts` `pipeline_kit`
  `runtime_guards` `runtime_gate` `b1_thresholds` `report_audit` `fmt` `net_retry`
- **L1** `datasource/`：采集与数据刷新。L2/L3 不得被它 import
- **L2** `core/factors/` `core/trades/`：因子实现全项目唯一一份（因子 + 注册表）
- **L3** `pipeline/` 四个 stage 包：`screening/` `market_timing/` `holdings/` `close_review/`
- **L4** `pipeline/` 顶层 runner 与 `research/`：研究依赖生产（回测跑生产的因子与打分），反向为 0

四条硬约束，都有测试守着：

- **路径只在 `core/paths.py` 定义一次** —— 不要自己拼 `BASE / "governance" / ...`
- **有公认定义的指标只在 `core/indicators.py`**（RSI / MACD / 振幅 / KDJ …）——
  因子自己的打分逻辑留在因子里，判据是「是否存在口径选择」
- **产物 schema 在 `core/contracts.py`**，生产者落盘前 `require(...)`；
  治理层 `.md` 不参与执行、会漂移，**字段级真相以代码为准**
- **同名模块全仓唯一** —— import 一律包式（`from custos.xxx import ...`），由可编辑安装解析；sys.path 注入机制已删除（阶段 4b），同名仍会让读者与工具歧义

## 子包职责

- `datasource/collect/` — `collect_holding_quotes`(持仓+指数报价)、`collect_incremental_market`(A50/CNH/涨跌停梯队等)、`collect_fund_flow`(东财资金流)、`online_quotes`(域B独立在线行情,不依赖TDX)。
- `datasource/`（顶层）— 数据刷新类：`refresh_eod_klines`/`refresh_market_indices`（EOD 与指数）、`sync_compass_amv`（指南针 0AMV 台账）、`tdx_ext_quotes`/`overseas_market_collector`（海外市场）、`breadth_basis`（涨跌家数真值）、`trading_calendar`（交易日历）。2026-08-11 从 market_timing 迁入：它们只依赖 L0/L1，是数据层不是择时。
- `datasource/local_tdx/` — 本地通达信数据封装(tq_http 快照、指南针 AMV、板块、miscinfo 概念标签),详见 `local_tdx/README.md`。
- `datasource/news/` — `rss_collector`/`rss_filter`/盘前情报 schema/盘后新闻摘要。
- `pipeline/market_timing/` — 择时评分、AMV/0AMV 状态机、技术监控、主题跟踪等。
- `pipeline/screening/` — 每日选股链:`formula_screen`(TQ 公式初筛)、`enrich_candidates`(模式识别)、`score_candidates`(共振打分分层)、`candidate_table`(备选表格);18:00 独立链(run_1800.py)运行,与三份报告分离,TdxW 未运行时干净降级。
- `pipeline/holdings/` — `b1_holding_state`(持仓状态机)、持仓技术分析、组合复盘报告。
- `pipeline/close_review/` — 14:45 收盘复盘:执行复盘、终审、周复盘、持仓 BBI/结构分析、`calc_mfe_mae`(持仓 MFE/MAE)。
- `research/` — `backtest_factors`(研究回测器)、`analyze_trades`(交易复盘,手动运行)、`reconcile_qfq`(复权对账)等;统一入口 `research/__main__.py`。
- `core/factors/` — 可复用因子(S 形/反转K质量/板块相位/RSI 状态/主升浪等),选股链与研究回测共用。
- `core/trades/` — 交易台账同步与标准化、增量台账、持仓对账。

## 新脚本放哪

- 定时链路的新阶段:写进 `pipeline/` 对应 stage 包,由 runner 调用;不要新增顶层 `run_*` 入口。
- 数据采集/刷新类脚本:放 `datasource/`;因子放 `core/factors/`;研究/回测类放 `research/`。
- 被 2 处以上复用的辅助函数:收敛到 `core/` 顶层共享基建(`paths` / `pipeline_kit` / `code_utils` / `net_retry`)。
- 一次性探针/草稿脚本:用完即删,不要留在本目录(历史教训:2026-07-20 清理 `test_tq_idx.py` / `test_tq_http_idx.py`)。真正的测试写进仓库根目录 `tests/`。
