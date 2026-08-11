# src 工具层

策略团队确定性脚本目录：数据采集、指标计算、报告渲染全部由本目录脚本完成,LLM 只负责格式化和摘要。

原则:

- 所有路径统一从 `paths.py` 导入,不硬编码。
- 所有输出统一写入 `strategy_team/data/`(日志进 `artifacts/logs/`)。
- 五个时点 runner(`run_0850/0905/1445/1700/1800`)是唯一调度入口,共享行为收敛到 `pipeline_kit.py`。

## 目录结构

```
src/
├── run_0850.py ~ run_1800.py   # 时点入口 runner(由调度器调用,共五个:0850/0905/1445/1700/1800)
├── daily_pipeline.py           # 日终完整管线(0905/1700 的部分阶段复用)
├── daily_report.py             # 从 ChiefDecision 渲染统一日报
├── runtime_gate.py             # 运行门禁入口(写 quality/{date}_runtime_gate.json)
├── indicators.py               # 共享技术指标(J 值/BBI 唯一实现)
├── paths.py / pipeline_kit.py / code_utils.py / net_retry.py
│                               # 共享基建(见下)
├── runtime_guards.py           # P0 运行时守卫:交易日历、新鲜度、数据质量
├── trading_calendar.py         # A 股交易日历刷新与查询
├── collect/                    # 数据采集脚本(报价/增量行情/资金流/在线行情)
├── market_timing/              # 市场择时与大盘技术分析
├── screening/                  # 每日选股链(公式初筛→充实→打分→表格,见 governance/contracts/SCREENING_WORKFLOW.md)
├── factors/                    # 可复用因子(选股/研究共用,见 governance/strategy/_factors/)
├── holdings/                   # 持仓侧:持仓状态机、持仓技术分析、组合复盘
├── close_review/               # 收盘复盘(14:45 链核心)
├── research/                   # 研究回测与分析(backtest_factors、复盘分析、对账探针,只读管线数据)
├── news/                       # RSS 新闻采集与过滤
├── trades/                     # 交易台账同步与标准化
└── local_tdx/                  # 本地通达信数据封装(见 local_tdx/README.md)
```

## 顶层脚本分类

| 脚本 | 分类 | 一句话职责 |
| --- | --- | --- |
| `run_0850.py` | 入口 runner | 08:50 盘前数据采集 |
| `run_0905.py` | 入口 runner | 09:05 盘前报告管线 |
| `run_1445.py` | 入口 runner | 14:45 收盘复盘链 |
| `run_1700.py` | 入口 runner | 17:00 盘后复盘链 |
| `run_1800.py` | 入口 runner | 18:00 每日选股独立链 |
| `daily_pipeline.py` | 入口 runner | 日终完整管线(被 0905/1700 阶段复用) |
| `daily_report.py` | 报告 | 从 ChiefDecision 渲染统一日报 |
| `runtime_gate.py` | 门禁 | 写 runtime_gate.json(daily_pipeline / run_1445 调用) |
| `indicators.py` | 共享基建 | J 值与 BBI 的唯一实现(live/回测/持仓三侧共用) |
| `paths.py` | 共享基建 | 全仓路径常量 |
| `pipeline_kit.py` | 共享基建 | runner 共享件:run_stage、交易日历、md 摘要 |
| `code_utils.py` | 共享基建 | 股票代码归一化 |
| `net_retry.py` | 共享基建 | 网络请求指数退避重试 |
| `report_audit.py` | 共享基建 | 报告可审计块（report_id/策略版本/数据截止/输入清单）唯一实现 |
| `runtime_guards.py` | 共享基建 | 交易日历/新鲜度/数据质量守卫 |
| `trading_calendar.py` | 共享基建 | 交易日历维护(经 TDX JSON-RPC 刷新) |

## 子包职责

- `collect/` — 数据采集:`collect_holding_quotes`(持仓+指数报价)、`collect_incremental_market`(A50/CNH/涨跌停梯队等)、`collect_fund_flow`(东财资金流)、`online_quotes`(域B独立在线行情,不依赖TDX)。
- `market_timing/` — 市场择时评分、AMV/0AMV 状态、EOD K 线刷新、板块映射、微信摘要等。
- `screening/` — 每日选股链:`formula_screen`(TQ 公式初筛)、`enrich_candidates`(模式识别)、`score_candidates`(共振打分分层)、`candidate_table`(备选表格);18:00 独立链(run_1800.py)运行,与三份报告分离,TdxW 未运行时干净降级。
- `factors/` — 可复用因子(S 形/反转K质量/板块相位/RSI 状态/主升浪等),选股链与研究回测共用。
- `holdings/` — 持仓侧:`b1_holding_state`(持仓状态机)、持仓技术分析、组合复盘报告。
- `close_review/` — 14:45 收盘复盘:执行复盘、终审、周复盘、持仓 BBI/结构分析、`calc_mfe_mae`(持仓 MFE/MAE)。
- `research/` — 研究回测与分析:`backtest_factors`(研究回测器)、`analyze_trades`(交易复盘 Excel,手动运行)、`reconcile_qfq`(复权对账)等;只读管线数据,不触碰 live 链。
- `news/` — RSS 新闻采集(`rss_collector`)、过滤(`rss_filter`)、盘前情报 schema、盘后新闻摘要。
- `trades/` — 交易台账同步与标准化、增量台账、持仓对账。
- `local_tdx/` — 本地通达信数据封装(tq_http 快照、指南针 AMV、板块、miscinfo 概念标签),详见 `local_tdx/README.md`。

## 新脚本放哪

- 定时链路的新阶段:写进对应子包,由 runner 调用;不要新增顶层 `run_*` 入口。
- 数据采集类脚本:放 `collect/`;因子放 `factors/`;研究/回测类放 `research/`。
- 被 2 处以上复用的辅助函数:收敛到顶层共享基建(`paths` / `pipeline_kit` / `code_utils` / `net_retry`)。
- 一次性探针/草稿脚本:用完即删,不要留在本目录(历史教训:2026-07-20 清理 `test_tq_idx.py` / `test_tq_http_idx.py`)。真正的测试写进仓库根目录 `tests/`。
