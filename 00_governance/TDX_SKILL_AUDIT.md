# TDX 技能审计与 strategy_team 工作流映射

- 审计时间：2026-07-10
- 审计范围：`$HOME/.openclaw-tdxclaw/skills/tdx-*/SKILL.md`
- 实际盘点：**45 个目录，45 个 SKILL.md，缺失 0 个**
- 目标：将现有 TDX 技能作为“数据适配器 / 分析增强器 / 决策模板”接入 `strategy_team`，而不破坏既有 Agent 权限、数据契约和否决链。
- 映射阶段：数据采集、`market_timing`、`theme_tracker`、`portfolio_review`、`stock_pool`、`buy_strategy`、`position_decision`、`risk_control`、`chief_decision`、`daily_report`、`close_review`。

> 核心结论：不应把 45 个技能全部直接串入每日主链。第一阶段只接入 10 个左右的高频、结构化、可验证技能；研究型技能按需旁路；总控型和交易型技能必须降权或隔离，避免形成第二套决策系统。

---

## 1. 总体分类与接入原则

### 1.1 三类技能

1. **原子数据技能**：主要封装 `tdx_api_data` 等结构化接口，适合进入数据采集层。如财务、公司、股东、交易、题材、板块数据。
2. **分析框架技能**：组合多个数据源形成判断，如市场主线、题材周期、持仓诊断、交易计划。适合给对应 Agent 提供“证据与候选结论”，但不可越权。
3. **总控 / 执行技能**：仓位决策、个股问答总控、TQ 量化交易等。与 strategy_team 自身的总控和执行层重叠，必须隔离权限。

### 1.2 接入硬规则

- 所有技能输出先转换为 `DATA_FLOW_CONTRACT.md` 的实体，不能将自然语言报告直接作为下游唯一输入。
- `market_timing` 仍决定市场许可和总仓位上限；技能不得单独放宽许可。
- `theme_tracker` 只给板块许可；不得输出个股具体买价。
- `stock_pool` 只做候选分层；`D` 池不得进入买入策略。
- `buy_strategy` 只能处理 A/B 池，不得独立选股；没有止损、失效条件、时间止损时只能“观察”。
- `risk_control` 的否决权不被任何技能覆盖。
- `chief_decision` 是唯一最终动作输出层；`tdx-position-decision`、`tdx-ggwdzk` 等只能提供旁证。
- `tdx-quant` 的下单、撤单、账户、预警、自定义板块写入等能力默认禁用；首期只允许只读数据。
- 所有时效数据必须携带 `as_of` / `trade_date` / `source` / `freshness`；报告期数据还需携带 `report_date`。

---

## 2. 全量技能清单：输入、工具、输出及阶段映射

说明：

- “主要工具”按 SKILL.md 中明确出现的接口记录；其中部分名称在当前运行时工具集中不存在，详见第 4 节。
- “阶段”中首项为建议主归属，其余为可消费该结果的下游阶段。
- 优先级：**P0 = 每日主链首期必接；P1 = 按需增强；P2 = 延后、隔离或仅人工调用。**

| # | 技能（目录） | 主要输入 | 主要工具 / 依赖 | 主要输出 | 建议阶段 | 优先级 |
|---:|---|---|---|---|---|---|
| 1 | A股市场主线识别（`tdx-agzxsb`） | 交易日、市场范围、涨停/资金/指数语境 | `tdx_screener`、`tdx_quotes` | 市场结构、主线/伪主线、情绪周期、龙头与次日观察 | **market_timing**、theme_tracker、daily_report、close_review | **P0** |
| 2 | 板块比较（`tdx-bkbj`） | 2 个以上板块、比较维度、时间窗口 | `tdx_kline`、板块操盘必读、资讯/公告 | 核心逻辑、交易位置、催化、资金、估值拥挤度和排序 | **theme_tracker**、stock_pool、chief_decision | P1 |
| 3 | 板块操盘必读（`tdx-board-cpbd`） | 板块代码、`branch`、周期 | `tdx_api_data` | 板块基础资料、详解、阶段涨幅、市场统计 | **数据采集**、theme_tracker | **P0** |
| 4 | 板块估值（`tdx-board-valuation`） | 板块/指数代码、个股代码、`queryType` | `tdx_api_data` | 个股板块估值对比、板块/指数历史估值 | **数据采集**、theme_tracker、stock_pool、risk_control | P1 |
| 5 | 北向资金行为（`tdx-bxzjxw`） | 股票代码、日期/区间、北向资金口径 | `tdx_api_data` | 净流入/流出、持仓变化、偏好、趋势性判断 | **数据采集**、market_timing、portfolio_review、close_review | P1 |
| 6 | 出海链投资（`tdx-chltz`） | 公司/行业、主营构成、海外业务问题 | `tdx_api_data` | 出海真实性、成长兑现、产业环节、汇率/地缘/认证风险 | **stock_pool**、theme_tracker、risk_control | P2 |
| 7 | 查询公司信息（`tdx-company-info`） | 股票代码、公司信息类型 | `tdx_api_data` | 公司概要、基本情况、发行交易、董监高、参控股公司 | **数据采集**、stock_pool、portfolio_review | **P0** |
| 8 | 持仓诊断与风险检视（`tdx-czzdxfxjs`） | 当前持仓、成本/仓位/盈亏、风险偏好 | `tdx_indicator_select`、`tdx_quotes`、`tdx_kline`、资讯 | 组合概况、风险暴露、核心/问题仓、调整顺序 | **portfolio_review**、risk_control、chief_decision、close_review | **P0** |
| 9 | 查询分红融资（`tdx-dividend-financing`） | 股票代码、`fixedTag`、报告期/分页 | `tdx_api_data` | 分红、股息率、派现融资比、配股/增发、股东进出详情 | **数据采集**、stock_pool、portfolio_review | P1 |
| 10 | 查询个股龙虎榜（`tdx-dragon-tiger`） | 股票代码、可用日期/指定日期 | `tdx_api_data` | 龙虎榜日期及买卖明细 | **数据采集**、theme_tracker、portfolio_review、close_review | P1 |
| 11 | 查询个股业绩预警（`tdx-earnings-warning`） | 股票代码、证券 id /查询附加参数 | `tdx_api_data` | 预警类型、预告净利润、同比变动区间 | **数据采集**、stock_pool、portfolio_review、risk_control | **P0** |
| 12 | 事件驱动与短线催化分析（`tdx-event-driven-short-term-catalyst`） | 股票、3–10 日窗口、事件/催化 | `tdx_quotes`、`tdx_kline`、`tdx_api_data`、`tdx_screener`、资讯 | 催化分类/强度、预期差、路径推演、跟踪点和条件化建议 | **buy_strategy**、stock_pool、risk_control、close_review | P1 |
| 13 | 分红与股东回报（`tdx-fhgdhb`） | 股票、历史分红/融资/回购 | `tdx_api_data` | 分红稳定性、“真红利”判断、资本配置质量 | **stock_pool**、portfolio_review | P2 |
| 14 | 查询财务分析数据（`tdx-financials`） | 股票代码、报表类型、报告期/单季参数 | `tdx_api_data` | 利润表、现金流量表、资产负债表、财务指标、主营构成等 | **数据采集**、stock_pool、portfolio_review、risk_control | **P0** |
| 15 | 反身性与泡沫识别（`tdx-fsxypmsb`） | 热门板块/个股、价格、资金、估值、叙事 | 多个 TDX 子技能、`tdx_kline`、`tdx_quotes`、资讯/研报/公告 | 基本面→预期→泡沫阶段、拥挤度、反噬触发器 | **risk_control**、theme_tracker、chief_decision、close_review | P1 |
| 16 | 个股投资逻辑研究（`tdx-ggtzljyj`） | 股票名称/代码、研究期限 | `tdx_quotes`、`tdx_api_data`、`tdx_kline`、研报 | 公司画像、核心逻辑、竞争、财务、估值、风险、结论 | **stock_pool**、portfolio_review | P1 |
| 17 | 个股问答总控（`tdx-ggwdzk`） | 一句个股问题 | `tdx_quotes`、`tdx_api_data`、`tdx_screener`，并路由其他技能 | 问题分类、技能路由、综合个股答复 | **chief_decision（仅旁路）** | P2 |
| 18 | 公告与财报分析（`tdx-ggycbfx`） | 公告/财报文本或公司与日期范围 | `wenda_notice_query`、`tdx_api_data` | 公告增量、财报质量、影响方向、持续性和风险 | **portfolio_review**、stock_pool、risk_control、close_review | P1 |
| 19 | 公司质地打分（`tdx-gszddf`） | 股票代码、公司/财务/治理数据 | `tdx_api_data` | 壁垒、成长质量、治理、长期跟踪价值评分 | **stock_pool**、portfolio_review | P1 |
| 20 | 查询个股热点题材（`tdx-hot-topic`） | 股票代码、`fixedTag` | `tdx_api_data` | 题材板块族谱、主题库、事件驱动、信息面概览 | **数据采集**、theme_tracker、stock_pool | **P0** |
| 21 | 查询行业产业链（`tdx-industry-chain`） | 行业代码、标题/事件查询参数 | `tdx_api_data` | 产业链结构、行业重要事件 | **数据采集**、theme_tracker、stock_pool | P1 |
| 22 | 查询行业产业链映射（`tdx-industry-chain-mapping`） | 行业趋势/技术/政策/事件主题 | `tdx_api_data`、`tdx_indicator_select`、`tdx_quotes` | 上中下游、价值环节、公司分层、映射逻辑、伪受益 | **theme_tracker**、stock_pool | P1 |
| 23 | 机构持仓股东分析（`tdx-jgccgdfx`） | 股票、可用报告期、股东/机构持仓 | 主力资金技能、`tdx_api_data`、行情、资讯/公告 | 股东结构、机构变化、集中度、资金属性和影响 | **portfolio_review**、stock_pool、risk_control | P1 |
| 24 | 基金重仓拥挤度（`tdx-jjzcyjd`） | 股票、机构持仓、估值、价格、预期 | 主力/股东技能、`tdx_api_data`、`tdx_quotes`、`tdx_kline`、研报资讯 | 拥挤评分、来源、去拥挤触发器、策略建议 | **risk_control**、portfolio_review、stock_pool | P1 |
| 25 | 查询龙虎榜席位风格（`tdx-lhbxwfg`） | 股票、龙虎榜日期、席位明细 | `tdx_api_data`、`tdx_lookup_stock`、`tdx_quotes`、`tdx_kline`、资讯/公告 | 主导席位、接力/兑现、资金风格、短线博弈结构 | **close_review**、theme_tracker、buy_strategy | P1 |
| 26 | 主力资金（`tdx-main-position`） | 股票、报告期、机构/北向/持仓对比类型 | `tdx_api_data` | 机构持股、持仓明细/分布、北向、股价对比 | **数据采集**、portfolio_review、stock_pool、risk_control | **P0** |
| 27 | 每日投研简报（`tdx-mrtyjb`） | 日期、市场主题、宏观/事件/研报范围 | `tdx_quotes`、`web_search`、研报/资讯 | 当日结论、关键事件、机会、风险、行动建议 | **daily_report** | **P0** |
| 28 | 仓位决策（`tdx-position-decision`） | 风险类型、指数、热点、当前仓位 | `tdx_quotes`、`tdx_kline`、`tdx_screener`、`tdx_api_data` | 市场环境、建议仓位区间、加减仓条件、今日动作 | **position_decision**、chief_decision（只作旁证） | P1 |
| 29 | 通达信TQ（`tdx-quant`） | 本地 TDX 安装路径、代码/周期/公式/账户等 | 本地 Python `tq` API；行情、财务、板块、公式、账户/交易接口 | 原始数据、批量选股、回测、订阅、预警、账户查询及交易动作 | **数据采集（只读）**、market_timing、stock_pool；写操作隔离 | P2 |
| 30 | 查询个股研报评级一致预期（`tdx-report-rating`） | 股票代码、`fixedTag=yzyq` | `tdx_api_data` | 评级分布、目标价/盈利一致预期时间序列 | **数据采集**、stock_pool、portfolio_review | P1 |
| 31 | 查询股本信息（`tdx-share-capital`） | 股票代码、股本类型 `fixedTag` | `tdx_api_data` | 股本结构/变动、限售解禁、回购 | **数据采集**、portfolio_review、risk_control | P1 |
| 32 | 查询股东信息（`tdx-shareholder-research`） | 股票代码、`fixedTag`、报告期/分页 | `tdx_api_data` | 控股股东、股东人数、排名、十大股东/流通股东 | **数据采集**、portfolio_review、stock_pool | P1 |
| 33 | 查询股票事件信息（`tdx-stock-events`） | 股票代码、事件类型与附加参数 | `tdx_api_data` | 事件日历/股票事件类结构化结果 | **数据采集**、portfolio_review、risk_control、buy_strategy | P1 |
| 34 | 查询题材生命周期与持续性（`tdx-tczqcxx`） | 市场/题材/代理个股、交易日 | `tdx_screener`、`tdx_quotes`、`tdx_kline`、`tdx_api_data`、资讯/公告/研报 | 题材阶段、内部层次、持续性、核心锚点、次日观察 | **theme_tracker**、market_timing、stock_pool、close_review | **P0** |
| 35 | 生成交易计划（`tdx-trade-plan`） | 已入池股票、行情/K线、市场和板块许可 | `tdx_quotes`、`tdx_kline`、`tdx_api_data`、指标、资讯/公告/研报 | 位置、入场条件、价格区间、仓位路径、止盈止损、失效条件 | **buy_strategy**、risk_control | **P0**（需适配） |
| 36 | 查询个股交易相关数据（`tdx-trading-info`） | 股票代码、交易数据类型 `fixedTag` | `tdx_api_data` | 大宗交易、融资融券、转融券、资金流、涨跌停分析等 | **数据采集**、portfolio_review、buy_strategy、close_review | **P0** |
| 37 | 估值与定价框架分析（`tdx-valuation-pricing-framework`） | 股票、财务/估值、价格、预期 | `tdx_quotes`、`tdx_api_data`、`tdx_indicator_select`、研报 | 公司类型、估值方法、位置、重估/杀估值触发器 | **stock_pool**、portfolio_review、risk_control | P1 |
| 38 | 问小达选A股（`tdx-wxd-a`） | 自然语言 A 股筛选条件、分页 | `tdx_screener`，可补 `tdx_quotes`/`tdx_kline`/资讯研报 | 条件股列表、基础字段、筛选解释 | **stock_pool**、数据采集 | **P0** |
| 39 | 问小达选板块（`tdx-wxd-bk`） | 板块类型、涨跌/估值/资金等条件 | `tdx_screener`，可补行情资讯研报 | 候选板块列表与排序 | **theme_tracker**、数据采集 | **P0** |
| 40 | 问小达选ETF（`tdx-wxd-etf`） | ETF 主题/指数/规模/费率/行情条件 | `tdx_screener`，可补行情/K线/资讯 | ETF 候选列表与要点 | **stock_pool**（ETF 分支） | P2 |
| 41 | 问小达选基金（`tdx-wxd-jj`） | 基金类型、业绩、经理、风险、持仓等条件 | `tdx_screener`，可补行情资讯研报 | 公募基金候选列表 | **stock_pool**（基金分支） | P2 |
| 42 | 业绩预告博弈（`tdx-yjygby`） | 股票、预告/快报/财报节点、预期基准 | `wenda_notice_query` | 数据对比、事件时间轴、情景推演、交易策略矩阵 | **buy_strategy**、portfolio_review、risk_control、close_review | P1 |
| 43 | 专家访谈纪要提炼（`tdx-zjftjytl`） | 访谈/渠道/电话会原文 | 输入文本，必要时资讯/研报校验 | 增量信息、事实/观点/推演、公司映射、待验证项 | **theme_tracker**、stock_pool | P2 |
| 44 | 查询龙头博弈分析（`tdx-ztltby`） | 交易日、市场或股票、涨停/连板结构 | `tdx_screener`、`tdx_quotes`、`tdx_kline`、`tdx_api_data`、资讯/公告/研报 | 情绪周期、涨停梯队、龙头角色、弱转强/分歧一致观察点 | **market_timing**、theme_tracker、stock_pool、close_review | P1 |
| 45 | 政策解读与受益分析（`tdx-zzjdysyfx`） | 政策文件/会议/监管事件、时间范围 | `web_search`、资讯/公告、`tdx_lookup_stock`、`tdx_quotes` | 政策增量、执行力度、受益链、标的分层、风险 | **theme_tracker**、stock_pool、risk_control | P1 |

---

## 3. 按工作流阶段的推荐映射

### 3.1 数据采集

**核心原子适配器**：

- 公司/基本面：`tdx-company-info`、`tdx-financials`、`tdx-earnings-warning`
- 题材/板块：`tdx-hot-topic`、`tdx-board-cpbd`、`tdx-industry-chain`、`tdx-board-valuation`
- 资金/持仓：`tdx-main-position`、`tdx-shareholder-research`、`tdx-share-capital`
- 交易/事件：`tdx-trading-info`、`tdx-stock-events`、`tdx-dragon-tiger`
- 预期/回报：`tdx-report-rating`、`tdx-dividend-financing`
- 本地只读数据：`tdx-quant`（仅作为补充，不首批替代现有 collector）

**建议产物**：统一写入带来源与时点的 evidence 对象，不直接形成买卖结论。

### 3.2 market_timing

- 主技能：`tdx-agzxsb`
- 增强：`tdx-ztltby`（涨停梯队/情绪）、`tdx-tczqcxx`（题材环境）、`tdx-bxzjxw`（北向）、`tdx-position-decision`（仅仓位旁证）
- 数据底座：`tdx_screener`、`tdx_quotes`、`tdx_kline`

**边界**：`tdx-position-decision` 不能覆盖 `market_timing_scorer.py` 的仓位上限，只能给出 `position_evidence`。

### 3.3 theme_tracker

- 主技能：`tdx-tczqcxx`、`tdx-agzxsb`
- 数据：`tdx-board-cpbd`、`tdx-hot-topic`、`tdx-wxd-bk`
- 增强：`tdx-bkbj`、`tdx-board-valuation`、`tdx-industry-chain(-mapping)`、`tdx-zzjdysyfx`、`tdx-ztltby`

**建议标准化**：最终只落为 `SectorState`；诸如“主升/发酵/扩散/分歧/退潮”需映射到现有枚举并保留原始阶段标签。

### 3.4 portfolio_review

- 主技能：`tdx-czzdxfxjs`
- 数据：`tdx-financials`、`tdx-main-position`、`tdx-trading-info`、`tdx-earnings-warning`
- 增强：公告财报、股东/股本、估值、机构拥挤、分红、事件、北向

**建议标准化**：每只持仓只允许输出 `HoldingReview.action` 枚举；组合层风险另存 `portfolio_risk_flags`。

### 3.5 stock_pool

- 发现入口：`tdx-wxd-a`；ETF/基金另开分支使用 `tdx-wxd-etf`、`tdx-wxd-jj`
- 资格过滤：`tdx-company-info`、`tdx-financials`、`tdx-earnings-warning`、`tdx-hot-topic`
- 质量增强：投资逻辑、公司质地、估值、产业链、机构持仓、政策受益

**边界**：研究技能只产生评分因子和风险项；最终 A/B/C/D 分层必须由 stock_pool Agent 结合市场、板块许可完成。

### 3.6 buy_strategy

- 主技能：`tdx-trade-plan`
- 事件型增强：`tdx-event-driven-short-term-catalyst`、`tdx-yjygby`
- 资金型增强：`tdx-trading-info`、`tdx-lhbxwfg`

**必要适配**：必须从 `StockCandidate` 接收 `bucket`、市场许可、板块许可；输出严格转换成 `BuyPlan`，且 B 池不得输出“允许”，C/D 池不得调用。

### 3.7 position_decision

- 主技能：`tdx-position-decision`
- 上游：MarketState、持仓净值/仓位、SectorState、RiskDecision

**定位**：建议作为独立的“仓位建议器”，输出 `recommended_range` 与条件，不拥有最终权限；若与 market_timing 冲突，取更保守值。

### 3.8 risk_control

- 主技能：`tdx-czzdxfxjs`（持仓风险）、`tdx-fsxypmsb`（泡沫风险）
- 增强：`tdx-jjzcyjd`、`tdx-earnings-warning`、`tdx-share-capital`、`tdx-stock-events`、公告财报、估值
- 审核对象：所有 `BuyPlan`、`HoldingReview`、position_decision 建议

**规则**：技能可新增风险，不能删除已有风险；冲突时执行更保守动作。

### 3.9 chief_decision

- 只消费标准化结果：MarketState、SectorState、StockCandidate、BuyPlan、HoldingReview、RiskDecision、PositionAdvice。
- 可参考：`tdx-position-decision`。
- 不建议直接接入：`tdx-ggwdzk`、`tdx-mrtyjb` 的自由文本结论，防止第二总控。

### 3.10 daily_report

- 主技能：`tdx-mrtyjb`
- 输入：必须是上游 Agent 的已决结果，而不是重新独立分析并改写决策。
- 输出：市场结论、主线、持仓动作、候选/买入、风险、明日验证点；行动项必须逐条引用 chief_decision。

### 3.11 close_review

- 市场与题材：`tdx-agzxsb`、`tdx-tczqcxx`、`tdx-ztltby`
- 资金与行为：`tdx-trading-info`、`tdx-dragon-tiger`、`tdx-lhbxwfg`
- 事件复核：公告财报、业绩预告博弈、事件催化
- 持仓复核：`tdx-czzdxfxjs`

**建议产物**：计划命中、触发器是否发生、偏离原因、错误标签、次日验证点；不能仅输出行情复述。

---

## 4. 重复、冲突与接口风险

### 4.1 功能重复组

| 重复组 | 涉及技能 | 关系 | 建议主从关系 |
|---|---|---|---|
| 市场主线 / 题材周期 / 龙头情绪 | `tdx-agzxsb`、`tdx-tczqcxx`、`tdx-ztltby` | 都会判断市场情绪、主线和持续性 | `agzxsb` 负责市场主线；`tczqcxx` 负责题材生命周期；`ztltby` 只负责涨停梯队/龙头微观结构 |
| 板块资料 / 比较 / 估值 / 筛选 | `tdx-board-cpbd`、`tdx-bkbj`、`tdx-board-valuation`、`tdx-wxd-bk` | 同时参与板块判断 | `wxd-bk` 发现，`cpbd`/估值供数，`bkbj` 只做候选板块横向排序 |
| 产业链 | `tdx-industry-chain`、`tdx-industry-chain-mapping` | 前者是原子查询，后者是研究框架 | 前者数据层，后者分析层；禁止重复抓取后相互覆盖 |
| 龙虎榜 | `tdx-dragon-tiger`、`tdx-lhbxwfg` | 前者返回明细，后者解释席位行为 | `dragon-tiger` 供数，`lhbxwfg` 分析 |
| 分红 | `tdx-dividend-financing`、`tdx-fhgdhb` | 前者原子数据，后者长期股东回报框架 | 前者数据层，后者按需研究 |
| 股东 / 机构持仓 | `tdx-main-position`、`tdx-shareholder-research`、`tdx-jgccgdfx`、`tdx-jjzcyjd` | 数据字段与分析框架交叉 | `main-position`/`shareholder` 供数，`jgccgdfx` 分析结构，`jjzcyjd` 只评拥挤风险 |
| 财务 / 财报 / 业绩预警 | `tdx-financials`、`tdx-earnings-warning`、`tdx-ggycbfx`、`tdx-yjygby` | 报表事实、公告解读、事件博弈交叉 | `financials`/预警供数，公告技能解释基本面，业绩博弈只做事件路径 |
| 公司研究 / 质地 / 估值 | `tdx-company-info`、`tdx-ggtzljyj`、`tdx-gszddf`、`tdx-valuation-pricing-framework` | 都会输出公司结论 | 公司信息供数；质地打分、估值形成独立因子；投资逻辑只做聚合研究 |
| 持仓 / 仓位 / 交易计划 | `tdx-czzdxfxjs`、`tdx-position-decision`、`tdx-trade-plan` | 容易同时给出仓位与操作建议 | 持仓诊断只处理存量；仓位决策只处理总仓位；交易计划只处理已入池标的 |
| 总控与日报 | `tdx-ggwdzk`、`tdx-mrtyjb`、strategy_team `chief_decision` | 都可能综合多源并给最终建议 | chief_decision 唯一决策；mrtyjb 只负责发布；ggwdzk 不进自动主链 |
| 选股 | `tdx-wxd-a`、`tdx-wxd-bk`、`tdx-wxd-etf`、`tdx-wxd-jj`、`tdx-quant` 公式选股 | 资产范围和数据引擎不同 | A股日链先用 wxd-a；ETF/基金分支隔离；TQ 公式结果作为 `source=formula_screen` 合并去重 |

### 4.2 决策冲突

1. **仓位冲突**：`tdx-position-decision` 可能给出独立总仓位，与 `market_timing` 重复。解决：最终上限取两者更保守值，且 risk_control 可进一步下调。
2. **选股越权**：`tdx-trade-plan` 内含市场、板块、财务检索，可能绕过 stock_pool。解决：强制要求 `candidate_id` 与 A/B 桶；没有上游对象则拒绝生成交易计划。
3. **持仓与买入混淆**：`tdx-czzdxfxjs` 会给“仓位建议”，可能被误用为新开仓。解决：仅允许处理现有 position id。
4. **自由文本覆盖结构化结论**：研究技能常给“建议买入/配置”措辞。解决：解析为 evidence，不进入 allowed_actions；只有 chief_decision 可发布动作。
5. **题材阶段枚举不一致**：技能使用“发酵/扩散/主升/分歧/退潮/尾声”，现有 SectorState 使用“主升/修复/分歧/震荡/退潮”。解决：保留 `raw_stage`，并建立确定性映射；“发酵/扩散”默认映射“修复或震荡”，须结合趋势后确定。
6. **时间窗口冲突**：短线催化（3–10 日）、长期质地、季度机构数据可能得出相反结论。解决：每条 evidence 必须带 `horizon`，不同期限不相互覆盖。

### 4.3 工具名称与可执行性风险

审计发现多个 SKILL.md 引用了当前工具清单中不存在或名称不一致的接口：

- **`wenda_news_query`**：大量技能引用，但当前可用工具中没有该名称；现有的是 `web_search`、`wenda_notice_query`、`wenda_report_query`。接入前必须设统一替代路由，不能假装已取到新闻。
- **`tdx_api_date`**：`tdx-jgccgdfx` 文档出现，疑为 `tdx_api_data` 拼写错误。
- **`tdx_income_statement`**：`tdx-trade-plan` 文档出现，当前应通过 `tdx_api_data` 的利润表 preset 或 `tdx-financials` 获取。
- **`tdx-kline` / `tdx-api-data`**：少数描述使用连字符形式，是技能名/文档别名，不是可直接调用的工具；实际工具为 `tdx_kline` / `tdx_api_data`。
- **技能间调用语义不统一**：如“调用 `tdx-main-position` skill”并不是原生工具调用。工作流实现时应由 orchestrator 展开成技能规定的底层工具，或直接复用已缓存数据。
- **`tdx-quant` 本地依赖**：需要通达信客户端、本地路径、DLL/插件、初始化环境；同时含账户和交易写操作，不能视作普通无副作用数据工具。

### 4.4 数据与运行风险

- 多个研究技能输出篇幅很长，不适合每天全量串行，成本高且易制造结论漂移。
- 机构持仓、十大股东、财报等是低频报告期数据，若与实时行情混排而不标日期，会产生“旧数据解释今日价格”的错觉。
- 龙虎榜只对上榜日有效；无榜不能解释为“资金没有行为”。
- 北向资金口径和可用性会变化，应显式记录数据源和统计口径。
- `tdx_screener` 的自然语言解析可能受词序影响；自动流程要保留原始 query、返回量和降级状态。
- 估值、评级、目标价属于模型/机构预期，不应被当成确定性目标。

---

## 5. P0 / P1 / P2 接入优先级

### 5.1 P0：首期主链（建议 14 个）

1. `tdx-agzxsb`：直接补强市场主线和情绪证据，适配 market_timing/theme_tracker。
2. `tdx-board-cpbd`：结构化板块资料，接口稳定、边界清晰。
3. `tdx-company-info`：候选与持仓的基础身份核验。
4. `tdx-czzdxfxjs`：与 portfolio_review/risk_control 高度吻合。
5. `tdx-earnings-warning`：高影响风险事件，适合成为硬风险旗标。
6. `tdx-financials`：stock_pool/portfolio_review 的基础质量数据。
7. `tdx-hot-topic`：个股—题材映射，直接支撑板块过滤。
8. `tdx-main-position`：机构与北向资金底层数据。
9. `tdx-mrtyjb`：适配 daily_report 发布层，但不得重做决策。
10. `tdx-tczqcxx`：theme_tracker 的核心生命周期框架。
11. `tdx-trade-plan`：与 BuyPlan 高度匹配；前提是先做 schema 适配和上游校验。
12. `tdx-trading-info`：融资融券、资金流、涨跌停等高频交易证据。
13. `tdx-wxd-a`：stock_pool 的标准化 A 股候选入口。
14. `tdx-wxd-bk`：theme_tracker 的板块发现入口。

**P0 理由**：覆盖每日最小闭环“市场许可→板块→候选→买入计划→持仓/风险→日报”，且多数能产生结构化或可清晰适配的数据。

### 5.2 P1：按需增强（建议 24 个）

`tdx-bkbj`、`tdx-board-valuation`、`tdx-bxzjxw`、`tdx-dividend-financing`、`tdx-dragon-tiger`、`tdx-event-driven-short-term-catalyst`、`tdx-fsxypmsb`、`tdx-ggtzljyj`、`tdx-ggycbfx`、`tdx-gszddf`、`tdx-industry-chain`、`tdx-industry-chain-mapping`、`tdx-jgccgdfx`、`tdx-jjzcyjd`、`tdx-lhbxwfg`、`tdx-position-decision`、`tdx-report-rating`、`tdx-share-capital`、`tdx-shareholder-research`、`tdx-stock-events`、`tdx-valuation-pricing-framework`、`tdx-yjygby`、`tdx-ztltby`、`tdx-zzjdysyfx`。

> 这里共列 24 个。它们价值较高，但属于低频、专题或二级分析；应由风险旗标、事件或用户关注触发，而非每日全量运行。

**P1 触发示例**：

- 个股当日异常波动/上榜：龙虎榜与席位风格。
- 临近财报或已有预告：业绩预警、公告财报、业绩博弈。
- 热门板块估值快速抬升：板块估值、泡沫识别、拥挤度。
- 新政策/产业事件：政策受益、产业链映射。
- 持仓发生机构/股东/解禁变化：股东、股本、主力持仓专题。

### 5.3 P2：延后或隔离（建议 7 个）

1. `tdx-chltz`：垂直主题研究，非每日通用链路。
2. `tdx-fhgdhb`：长周期红利研究，可由原子分红数据先覆盖。
3. `tdx-ggwdzk`：与 chief_decision/路由器重叠，容易产生第二总控。
4. `tdx-quant`：能力强但本地依赖和写操作风险高；首期仅做单独只读 PoC。
5. `tdx-wxd-etf`：当前主工作流以 A 股个股为主，待资产范围扩展。
6. `tdx-wxd-jj`：同上，基金应建立独立 schema 与评价体系。
7. `tdx-zjftjytl`：依赖用户提供纪要，属于事件驱动的人工旁路。

**数量校验**：P0 14 + P1 24 + P2 7 = **45**。

---

## 6. 推荐的最小落地方案

### 6.1 MVP 目标（按 2026-07-10 用户确认调整）

先完成一个不改变既有决策权、可运行、可复盘的最小闭环。首期考虑以下 **9 个技能能力**：

- 市场/题材：`tdx-agzxsb`、`tdx-tczqcxx`
- 板块/映射：`tdx-board-cpbd`、`tdx-hot-topic`
- 个股风险底座：`tdx-company-info`、`tdx-financials`、`tdx-earnings-warning`
- 计划：`tdx-trade-plan`
- 持仓：`tdx-czzdxfxjs`

**暂不使用** `tdx-wxd-a`、`tdx-wxd-bk`。候选发现继续沿用现有 `theme_tracker + stock_pool + formula_screen`，技能层不得自行扩充候选。

### 6.2 最小调用链

```text
盘前数据采集
  ├─ tdx-agzxsb → market_evidence
  ├─ 现有 theme_tracker + tdx-board-cpbd → sector_evidence
  └─ tdx-tczqcxx → theme_stage_evidence

market_timing / theme_tracker
  └─ 写入 MarketState、SectorState（仍由现有 Agent 决策）

stock_pool
  ├─ 现有 stock_pool + formula_screen → raw_candidates
  ├─ tdx-hot-topic → candidate_sector/theme mapping
  └─ company-info + financials + earnings-warning → quality/risk flags

buy_strategy
  └─ 仅 A/B 池调用 tdx-trade-plan → 转换为 BuyPlan

risk_control
  └─ 校验市场许可、板块许可、预警、止损/失效条件

chief_decision
  └─ 只读取标准化实体，形成唯一最终动作
```

### 6.3 最小适配字段

每个技能调用统一包一层 `SkillEvidence`：

```json
{
  "skill_id": "tdx-hot-topic",
  "entity_type": "stock",
  "entity_id": "600000.SH",
  "as_of": "2026-07-10T13:00:00+08:00",
  "trade_date": "2026-07-10",
  "report_date": null,
  "horizon": "intraday|short|medium|long",
  "source_tools": ["tdx_api_data"],
  "status": "ok|partial|stale|failed",
  "facts": {},
  "signals": [],
  "risk_flags": [],
  "raw_ref": "可追溯缓存位置"
}
```

### 6.4 四个必须先做的适配器

1. **ThemeStageAdapter**：将题材技能阶段映射到 `SectorState.state`，保留 `raw_stage`。
2. **CandidateAdapter**：将 `tdx_screener` 返回转换成 StockCandidate 基础字段，并执行代码/市场规范化和去重。
3. **BuyPlanAdapter**：将交易计划转换为 BuyPlan；止损、失效条件、首仓任一缺失即降级为“仅观察”。
4. **RiskFlagAdapter**：将业绩预警、板块退潮、异常交易、数据过期统一转成 risk flags，且只允许追加风险。

### 6.5 运行与降级规则

- 任一技能失败：保留 `status=failed`，不得用模型猜测补值。
- 新闻接口缺失：标 `partial`，使用 `web_search` 补充时必须注明来源；不自动等同于原 SKILL 的 `wenda_news_query`。
- 缺 market_timing：所有候选最高 B 池，BuyPlan 只能观察。
- 缺 SectorState：候选不得进入 A 池。
- 财务/股东数据过期不代表失败，但必须展示报告期。
- 每日只对 A 池和现有持仓跑深度技能；B 池轻量更新，C/D 池不跑，控制成本和结论漂移。

### 6.6 MVP 验收标准

连续运行 5 个交易日，至少满足：

1. 所有技能结果可追溯到原始工具、日期和实体。
2. 无技能绕过 market_timing、stock_pool、risk_control。
3. A 池候选均有板块支持、基础资料和业绩预警检查。
4. 所有“允许/小仓试探”的 BuyPlan 均有止损和失效条件。
5. risk_control 否决项 100% 进入 chief_decision 禁止动作。
6. 技能失败不会中断全链，且报告明确标记 partial/failed。
7. close_review 能追踪“使用了哪些技能证据、哪些触发器命中、结论是否有效”。

---

## 7. 最终建议

- **立即做**：用 P0 中的原子数据和题材/选股技能补强现有 Agent，先做 schema 适配，不重写现有工作流。
- **第二步做**：按事件触发 P1 专题技能，避免每日全量长报告。
- **暂不做**：不要让 `tdx-ggwdzk` 成为第二总控；不要启用 `tdx-quant` 的账户与交易写能力；不要把 ETF/基金筛选硬塞进当前 A 股 StockCandidate schema。
- **推荐架构**：`技能 → SkillEvidence → 现有 Agent → 数据契约实体 → risk_control → chief_decision`。技能负责“提供事实和框架”，strategy_team 负责“权限、冲突、动作和复盘”。
