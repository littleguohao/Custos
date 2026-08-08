# 数据源覆盖矩阵

> **本文回答一个问题：策略团队要的每一项数据，现在到底能不能拿到、从哪拿。**
>
> 2026-08-06 重写。此前版本标注的是「理想数据层」——大量数据项挂在
> `tdx_api_data` / `tdx_screener` / `wenda_report_query` 这些 LLM 工具上，而纯脚本模式
> 下它们**一个都没接入**。旧版把「想要」写成了「有」，读的人无法区分。
>
> 现在每一项都标可用性，依据是**代码 grep + 探针实测**，不是设计意图。
> 接口的用法细节见同目录 `TDX_LOCAL_INTERFACES.md` / `MOOTDX_INTERFACES.md` /
> `QLIB_LOCAL_DATA.md`；数据源选择原则见 `DATA_SOURCE_PRINCIPLE.md`。

## 可用性标记（五份数据文档共用）

| 标记 | 含义 | 判定依据 |
|---|---|---|
| ✅ | **已接入 · 实测可用** | 有生产代码在用，且探针/实跑拿到过真数据 |
| ⚠️ | **已接入 · 有已知问题** | 在用，但有降级、口径或正确性问题（问题写在备注里） |
| 🚫 | **已接入 · 实测拿不到** | 代码路径存在，但实测返回空/失败 |
| ❌ | **未接入** | 代码里根本没有这条路径（旧文档把它当成"有"） |

⚠️ 「✅」只保证**能拿到数据**，不保证数据正确。正确性问题另行标注——
例如前复权对全链默认生效，但它**从未与独立序列对账过**（见 `DATA_SOURCE_PRINCIPLE.md`）。

---

## 一、市场宽度与情绪

| 数据项 | 状态 | 来源 | 采集入口 | 时效 | 备注 |
|---|---|---|---|---|---|
| 涨跌家数 | ✅ | 880005 | `collect_incremental_market.py`（mootdx Reader 本地） | 盘后 EOD | up_count / down_count |
| 停板家数 | ✅ | 880006 | 同上 | 盘后 EOD | 涨停/跌停/炸板 |
| 平均股价 | ✅ | 880001 | 同上 | 盘后 EOD | ⚠️ 成交额口径曾修（2b8936a） |
| 0AMV 活跃市值 | ⚠️ | 指南针 `day.vdat` | `compass_amv.py` + `sync_compass_amv.py` | 盘后 | **决定 regime 的单点**。自动源不可用时退回人工录入 `0amv_observations.jsonl`；`amv_state` 仅在 `quality=='confirmed'` 时切换 |
| 涨跌停对比 | ❌ | ~~tdx_screener~~ | — | — | LLM 工具，纯脚本模式未接入 |
| 连板梯队 | ❌ | ~~tdx_screener~~ | — | — | `collect_incremental_market.py:197` 只留了一句 `note: "collected via tdx_screener in post-close enrichment"`，**是占位不是采集** |
| 炸板率 | ❌ | ~~tdx_screener~~ | — | — | 同上 |

## 二、指数与行情

| 数据项 | 状态 | 来源 | 采集入口 | 实测耗时 | 备注 |
|---|---|---|---|---|---|
| 上证指数 | ✅ | **999999** | mootdx Reader 本地 vipdoc | 4.7ms | ⚠️ **不是 000001**——vipdoc 里 `sh000001` 不是上证指数（ebd9982 修） |
| 个股日线 | ✅ | 本地 vipdoc | `read_vipdoc_daily()` | **4.7ms**/股 | 回测与 live 的主数据源。⚠️ **深度只约 1214 根（5 年，2021-06 起）** ⇒ 跨年 walk-forward 的可用历史有限 |
| 个股日线（前复权） | ✅ | vipdoc + 自算 xdxr | `get_ohlcv_table(adjust="qfq")` | **8.3ms**/股 | 全链默认口径。**已对账**：与未复权收益 0 天偏离（`reconcile_qfq.py`）。BJ 曾因查错 market 拿不到权息，2026-08-06 已修 |
| 持仓实时行情 | ✅ | TQ 快照 → 在线 bars → 域B | `collect_holding_quotes.py` | TQ 约 80ms | 多源链式降级 |
| 在线日线（兜底） | 🚫 | mootdx Quotes | `get_online_bars()` | **12949ms → 空** | 实测 13 秒返回 0 行（见备注 B） |
| 在线指数 | 🚫 | mootdx Quotes | `get_online_index()` | **9992ms → 空** | 同上 |
| BJ 股票行情 | ✅ | 东财 push2 | `collect_holding_quotes.py` | — | mootdx 不支持北交所 |
| 前复权因子（旧路径） | ⚠️ | mootdx `get_adjust_year` | `local_tdx_data.py --mode adjust` | — | **已被自算 xdxr 取代**，仅 CLI 保留 |

## 三、外围市场

| 数据项 | 状态 | 来源 | 采集入口 | 备注 |
|---|---|---|---|---|
| 美股三大指数 / 费半 | ✅ | Yahoo Finance | `overseas_market_collector.py` | ^DJI / ^IXIC / ^GSPC / ^SOX |
| 日经 / KOSPI / 恒生科技 | ✅ | Yahoo Finance | 同上 | |
| A50 期指 / 离岸人民币 | ✅ | Yahoo Finance | 同上 | |
| AI 链个股（NVDA/AMD/TSM） | ✅ | Yahoo Finance | 同上 | |
| 降级路径 | ✅ | TDX ext 行情 | `tdx_ext_quotes.py` | Yahoo 不可用时降级（3c7c833） |

## 四、资金流向

| 数据项 | 状态 | 来源 | 采集入口 | 备注 |
|---|---|---|---|---|
| 个股主力净流入 | ✅ | **东财 push2** | `collect_fund_flow.py` | ⚠️ 旧文档写的是 `tdx_api_data zjlx`，**实际走东财**；已查证无法本地化（见 `DATA_SOURCE_PRINCIPLE.md`） |
| 北向资金 | ✅ | 880863 | mootdx Reader 本地 | 5 日趋势 |
| 融资融券指数 | ✅ | 880390 | mootdx Reader 本地 | 杠杆情绪 |
| 两融余额变动 | ❌ | ~~tdx_api_data rzrq~~ | — | **全仓 0 处代码** |
| 大单成交统计 | ❌ | ~~tdx_screener~~ | — | 未接入 |

## 五、龙虎榜与异动

| 数据项 | 状态 | 来源 | 采集入口 | 备注 |
|---|---|---|---|---|
| 龙虎榜明细 | ❌ | ~~tdx_api_data jglhb~~ | — | **全仓 0 处代码**（旧文档标 confirmed，是误标） |
| 龙虎榜可用日期 | ❌ | ~~tdx_api_data~~ | — | 同上 |
| 大宗交易 | ❌ | ~~tdx_api_data dzjy~~ | — | 同上 |

⇒ **整个第五类目前完全空缺。** 若要补，TQ 探测记录里 `download_file down_type=6`
是龙虎榜，但它属**高风险接口**（见 `TDX_LOCAL_INTERFACES.md`「探过但没接」）。

## 六、公告与新闻

| 数据项 | 状态 | 来源 | 采集入口 | 备注 |
|---|---|---|---|---|
| 持仓公告 | ⚠️ | `wenda_notice_query` | **cron LLM 调用**（不在 py 里） | 08:50 job 的 toolsAllow 含它；脚本链无此能力 |
| 宏观 / 财经要闻 | ✅ | RSS | `rss_collector.py` + `rss_filter.py` | 源与过滤规则在 `contracts/RSS_*.json` |
| 华尔街见闻快讯 | ✅ | wscn 直连 | `rss_collector.py`（wscn_lives 适配器） | 3f01e3a |
| 研报评级 | ❌ | ~~wenda_report_query~~ | — | **0 处代码** |

## 七、财务数据

| 数据项 | 状态 | 来源 | 采集入口 | 实测 | 备注 |
|---|---|---|---|---|---|
| 全市场财务（585 字段） | ✅ | mootdx Affair | `get_financial_data()` | 640ms / 114 行 × 585 列 | 专项文件一次性下载 |
| **PIT 财务（带公告日）** | ✅ | 东财 datacenter | `fetch_pit_financials.py` | 单期约 24 页 | 以**公告日**为可见日；单期约 1.15 万行（5400 只 × 多报表类型）。残缺一律抛 `FetchIncomplete` 不落盘 |
| 真市值 / 总股本 | ✅ | 东财 datacenter | `fetch_market_cap.py` | 单期约 11 页 | 2018-01-02 起；早期靠东财 F10 股本史回填 |
| 利润表/资产负债表/现金流 | ❌ | ~~tdx_api_data~~ | — | — | 未接入（585 字段里已含大部分） |
| 主营构成 / 业绩预警 | ❌ | ~~tdx_api_data~~ | — | — | 未接入 |

## 八、板块与行业

| 数据项 | 状态 | 来源 | 采集入口 | 备注 |
|---|---|---|---|---|
| 官方细分行业（881xxx） | ✅ | `tdxzs3.cfg` 本地 | `tq_sector.classify_sector()` | **每股恰好一个**，2026-08-04 实测 5546 只零冲突（f309ac6） |
| 概念/主题标签 | ✅ | TQ `download_file(down_type=4)` | `concept_tags.py` | miscinfo 8.1MB / 68161 条；实测 1132ms |
| 板块指数日线（880xxx） | ✅ | TQ | `fetch_sector_index_history.py` | 供板块相位 MACD；生产验证 587/587（ba8a396） |
| 板块成分股 | ✅ | 本地 `block.dat` / TQ | `tq_sector` / `holding_sector_mapper` | 01583e8 起改本地文件，去 tqcenter 依赖 |
| 板块涨幅排序 | ❌ | ~~tdx_screener~~ | — | 未接入 |
| 行业产业链 / 重要事件 / 操盘必读 | ❌ | ~~tdx_api_data~~ | — | 未接入 |

## 九、交易日历与基础数据

| 数据项 | 状态 | 来源 | 采集入口 | 备注 |
|---|---|---|---|---|
| 交易日历 | ✅ | SSE 官方 + TQ `get_trading_dates` | `trading_calendar.py` | 配置落 `contracts/CN_TRADING_CALENDAR.json`（**7 处代码依赖**） |
| 交易日判断 | ✅ | 同上 | `--check-date` | 优先级：手动 > SSE > 缓存 > 周末 > unknown |
| 本地股票清单 | ✅ | vipdoc 目录枚举 | `list_local_vipdoc_codes()` | 40ms / **5536 只**（A 股个股，含 BJ） |
| 在线股票清单 | ⚠️ | mootdx Quotes | `get_stock_list()` | 16640ms / 原始 **51567 项** → 过滤后约 5300 只。2026-08-06 起默认只返 A 股个股（见备注 C） |
| 股票名称（ST 判定） | ✅ | 东财 ulist → TQ → 缓存 | `stock_names.py` | ST 硬排除的唯一依据；全量表有最低覆盖率门槛（b33e52a） |
| 权息数据（xdxr） | ⚠️ | TDX 协议 + 本地缓存 | `adjust_factors.get_xdxr()` | 缓存命中 **0.2~0.4ms**；**BJ 返回空**（备注 A） |

---

## 备注：三个已知的正确性问题

### A. BJ 股票的未复权数据被标成「已前复权」（2026-08-06 **已修**）

实测（2026-08-06 探针）：

```
get_xdxr("920808") → []            # 不抛错，返回空列表
apply_qfq(df, []) → attrs: {'adjust': 'qfq', 'adjust_events': 0}
价格是否被改动: False
```

`get_xdxr` 对 BJ 拿不到权息却返回空列表（而非报错），`qfq_table` 因此走**成功路径**，
`apply_qfq` 盖章 `adjust="qfq"`。⇒ **`adjust == "qfq"` 不能推断已正确复权**：
它可能是「真的从未除权」，也可能是「该市场取不到事件」。
唯一线索 `adjust_events == 0` **目前无任何调用方检查**。

**根因**：`mootdx.utils.get_stock_market` 的规则是「'5'/'6'/'9' 开头为 sh」，
北交所**新代码段 `920xxx` 被判成沪市**（老段 43/83/87 判对了），而 `q.xdxr()` 内部用它推断
market ⇒ 查 `SH:920808` 的权息，服务器返回空。实测：

    get_xdxr_info(1, "920808") →  0 条
    get_xdxr_info(2, "920808") → **24 条**（8 条影响价格 + 16 条股本变化）

**修法**：`adjust_factors._tdx_market()` 走 `code_utils.market_of` 自己判 market，
三处调用改为 `q.client.get_xdxr_info(market, code)`；缓存加 `market` 标记且
**缺 market 的空事件缓存一律作废**（此前把空结果永久缓存了）。

**修复后实测 920808 首根因子 0.0403** —— 未复权价是复权价的约 25 倍，
任何除权日在样本里都长得像 -96% 暴跌。BJ 约占 universe 4.8%
⇒ **此前所有含 BJ 的回测约 5% 样本用了错价格。** 另：BJ 的股本事件此前也全空（16 条）。

### B. 在线 TDX 协议基本不可用

| 接口 | 实测耗时 | 结果 |
|---|---|---|
| `get_online_bars()` | 12949ms | 0 行 × 0 列 |
| `get_online_index()` | 9992ms | 0 行 × 0 列 |
| `get_snapshot()` | 70ms (max 3114ms) | 0 键 |
| `get_stock_list()` | 16640ms | 51567 项 ✅ |

只有 `get_security_list` 能用。**且这些失败都不抛异常**——返回空 DataFrame，
调用方看不出区别。`get_ohlcv_table` 在本地数据 stale 时会走这条兜底：
13 秒换一个空 DataFrame。**待评估是否该判定不可用或加短超时。**

### C. 在线股票清单曾是回测宇宙的隐形污染源（已修）

`get_stock_list()` 原样返回 **51567 项**——含 `999999` 等指数、ETF、可转债，
而沪深 A 股个股只有约 5300 只。两个生产调用方处境完全不同：

| 调用方 | 有无下游过滤 | 后果 |
|---|---|---|
| `formula_screen.py:130` | ✅ `_A_SHARE_RE` + `exclude_bj` | 不受影响 |
| `backtest_factors.py`（`sample_codes()`） | ❌ **无** | 直接 `sample_codes(base, N, seed)` ⇒ 抽样约 **89%** 概率抽到非个股 |

而 `backtest_factors` 的这条路径是**不传 `--universe-local` 时的默认**。
案底：`1d0d7de` 的提交信息就是「universe 改用本地 vipdoc 枚举，**修复回测 16.7% 覆盖率**」，
`eab500a` 又专门修文档里漏传 `--universe-local` 的命令 —— 同一个坑踩过两次。

2026-08-06 起 `get_stock_list(ashare_only=True)` 为默认，与 `list_local_vipdoc_codes()`
口径可比（都是 A 股个股），差异只剩「在线代码表 vs 本地实有文件」与「含不含 BJ」。
需要原始全表传 `ashare_only=False`。

### D. 东财两个接口不是坏的——是探针参数偏离了生产默认

第一版探针把两项都报成失败，实际都是我传错参数：

| 接口 | 探针传的 | 接口自报 | 真相 |
|---|---|---|---|
| PIT 财务 | `page_size=5` | `pages=2304` | 2304 × 5 = 11520 行/期。生产默认 `page_size=500` ⇒ **约 24 页**，`max_pages=40` 够用 |
| 真市值 | `max_pages=2` | `pages=110` | 110 × 50 = 5500 ≈ A 股数。生产默认 `page_size=500, max_pages=40` ⇒ 约 11 页 |

单期 11520 行 ≈ 5400 只 × 多种报表类型，与 `normalize()` 里有 `dropped_type` 吻合，
量级合理。**教训：探针的参数一偏离生产就会量到假故障** —— 已把两项改回生产默认。

`fetch_period` 的分页完整性校验本身是对的（残缺一律抛 `FetchIncomplete`，
而不是把限流当成"翻完了"）。

---

## 质量等级说明（数据落盘时的 `quality` 字段）

| 等级 | 含义 |
|---|---|
| `confirmed` | 数据已验证，可作为交易决策依据 |
| `auto` | 自动采集但未经人工确认，标记后可用 |
| `degraded` | 部分可用，需人工补充确认 |
| `missing` | 数据不可用，报告标记 unavailable |

⚠️ 这套等级描述的是**采集结果的可信度**，与本文的接入状态标记（✅/⚠️/🚫/❌）是两回事：
一个 ✅ 的接口在某天也可能产出 `missing`。运行门控按 `quality` + `as_of` 判定，
见 `contracts/MASTER_WORKFLOW.md`。
