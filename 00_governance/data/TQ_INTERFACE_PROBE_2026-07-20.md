# TQ-Local (tqcenter/HTTP) 待挖掘接口摸底评估报告

- 日期：2026-07-20
- 环境：TdxW 运行中，TQ-Local HTTP JSON-RPC `http://127.0.0.1:17709/`
- 方法：逐接口实测（非文档推断），探测脚本为临时文件，未改管线代码
- 已知不可用（此前确认）：`get_scjy_value` / `get_gpjy_value` / `get_bkjy_value`（权限，返回 null）

---

## 一、结论速览（TOP 推荐）

| 优先级 | 接口 | 能补的缺口 | 评级 |
|---|---|---|---|
| ★1 | `formula_process_mul_xg`（批量选股公式，107 个系统公式+用户公式） | 涨停板池/连板梯队/技术形态筛选的**可编程筛选器**；数据当日（含 2026-07-20） | 高 |
| ★2 | `download_file` down_type=3（sentiment.json 舆情） | 题材舆情/公告流，**当日实时**（22:16 更新的 1196 条） | 高 |
| ★3 | `download_file` down_type=4（miscinfo.json 综合信息） | 全市场 5529 股的**概念主题标签/主营业务/亮点**——题材归属映射的直接数据源 | 高 |
| ★4 | `get_ipo_info` | 新股/新债申购日历，含当天和未来日期 | 中高 |
| ★5 | `get_trackzs_etf_info`（指数代码须用 `000300.SH` 格式，`.CSI` 返回空） | ETF 规模/IOPV/份额——ETF 资金面观察 | 中 |
| 6 | `get_gb_info` / `get_kzz_info` | 股本、可转债条款（转股价/强赎/回售），B1 前置排除辅助 | 中 |
| 7 | `get_pricevol` | 仅 LastClose/Now/Volume 三字段，非真正"价量分布"，无增量价值 | 低（不用） |
| ✗ | `download_file` down_type=1/5/6（十大股东/经营分析/龙虎榜） | 参数格式苛刻且本次实测**把 TQ 服务打挂**，详见§四 | 低（暂不用） |
| ✗ | `get_scjy_value_by_date`（连板家数 SC23/市场高度 SC30 等） | 与 `get_scjy_value` 同样不可用（ErrorId=10） | 不可用 |

**对"真实反映市场情况"三个最相关问题的回答：**

1. **连板高度/涨停板池**：TQ 侧最佳路径是 `formula_process_mul_xg` 批量跑系统/自编选股公式（当日数据已验证），配合已可用的 `get_more_info` 的 `LastZTHzNum`(几板)/`EverZTCount`(连板天)/`FCAmo`(封单额) 字段。`get_scjy_value_by_date` 的市场级连板统计（SC23/SC30/SC33/SC35）**确认不可用**。
2. **龙虎榜**：down_type=6 参数格式未探明即触发服务挂死，本轮未能拿到产物；文档级存在但**可靠性存疑**，不建议近期接入。
3. **资金流向**：`get_more_info` 的 `Zjl`（主买净额）/`Zjl_HB`（主力净流入）已在既有能力内；ETF 资金方向可用 `get_trackzs_etf_info` 的份额/规模变化近似。无新增更优接口。

---

## 二、逐接口实测结果

### 1. `download_file`（6 种 down_type）

产物目录：`E:\new_tdx64\PYPlugins\data\`

| down_type | 参数实测 | 返回 | 产物 | 时效 |
|---|---|---|---|---|
| 2 ETF申赎 | `510300.SH` + `20260720` | ErrorId=0 成功 | `etfpcf510300_20260720.json` **仅 2 字节（空）** | 当日文件但无内容（PCF 清单可能需盘前/前一交易日；改用 20260717 重试时服务已挂，未验证） |
| 3 舆情 | 无参 | ErrorId=0 成功 | `sentiment.json` 556KB，1196 条 | **当日实时**（最新 2026-07-20 22:16，覆盖 07-19~07-20），字段 Issue_date/title/Summary，内容为公告+新闻流 |
| 4 综合信息 | 无参 | ErrorId=0 成功 | `miscinfo.json` 8.1MB，68161 条 | 当前快照：5529 只股票 × 多个类别（id=10001 概念主题标签、10004 主营业务、10010 亮点等），如 601696 → "不可减持(新规),无实控人,罗素中盘,期货概念" |
| 1 十大股东 | `601696.SH`+`"2025"`(字符串) → ErrorId=3 down_time error；`601696`(纯代码) → ErrorId=2 stock_code error；`601696.SH`+`2025`(整数) → **请求挂起 120s 超时，此后整个 TQ 服务对所有请求无响应** | 失败 | 无产物 | — |
| 5 经营分析 | 同 type1 的参数矩阵，均失败/超时（在 type1 挂死之后执行，无法区分参数错误与级联挂死） | 失败 | 无产物 | — |
| 6 龙虎榜 | `601696.SH` 无 down_time → ErrorId=3 "down_time error:"（文档称 down_time 无效，但实际校验存在）；带参重试发生在服务挂死后，全部超时 | 失败 | 无产物 | — |

**评级**：type3/type4 **高**（一次调用、当日、内容直接可用）；type2 **中**（成功但当日为空，需盘前时段复验）；type1/5/6 **低**（参数格式苛刻、整型年份触发服务端挂死）。

### 2. `get_ipo_info`

- `ipo_type=0, ipo_date=1`：返回 4+ 条未来新股（SGDate 20260720/0724/0727），含 Code/Name/SGDate/SGPrice/SGCode/MaxSG/PE_Issue
- `ipo_type=2, ipo_date=0`：当日 3 条（2 只新股 + 1 只新债，含发行价、申购上限、发行 PE）
- 时效：当日及未来排期，准确。**评级：中高**。用途：B1 前置排除"次新/申购日"核对、打新日历。

### 3. `get_trackzs_etf_info`

- `000300.CSI` → 返回 `{"raw": ""}` 空（文档示例格式实际不可用）
- `000300.SH` → 成功，返回跟踪沪深300的 ETF 列表（Code/Name/NowPrice/PreClose/IOPV/Zgb份额/Sz规模亿元），如 510350、159925 等十余只
- 时效：实时行情级。**评级：中**。用途：宽基/行业 ETF 规模与份额变动 ≈ 被动资金方向；板块 ETF 情绪。

### 4. 公式接口（`formula_get_all` / `formula_xg` / `formula_process_mul_xg`）

- `formula_get_all(type=1)`：返回 **107 个条件选股公式**（全部系统公式：UPN 连涨、MA买入、MACD金叉、BBI 等，另含基本面选股 A001~A013 系列）
- `formula_process_mul_xg`（UPN, arg=3, stock_period=1d, count=60）：**成功**，按股按日返回 0/1 序列，含 20260720 当日 → 数据当日
- `formula_set_data_info` + `formula_xg` 单次：成功，返回 60 日 0/1 序列
- `formula_xg` 传原生表达式 `"MA(C,5)>MA(C,10)"`：ErrorId=9 "获取公式失败或公式不存在" → **不能直接跑任意表达式**，只能用客户端已存在的（系统或用户预建）公式名
- 注意：`formula_process_mul_xg` 必须显式传 `stock_period`（缺省报 ErrorId=5 periodstr error）
- **评级：高**。结论：**"涨停板池/连板高度"可以用公式筛**——路径是在通达信客户端预建一个选股公式（如 `C>=ZTPRICE(REF(C,1),0.1)` 或连板条件），再经 `formula_process_mul_xg` 对全 A 批量执行。这是本次摸底中补齐"涨停池/连板梯队"缺口的最可行通道。

### 5. `get_gb_info` / `get_kzz_info`

- `get_gb_info`（601696.SH, 20260720）：成功，Date/Zgb/Ltgb，当日。**评级：中**（股本变动核对用）。
- `get_kzz_info` 空参 → ErrorId=2；传真实可转债代码（123064.SZ）→ 成功，返回转股价/强赎触发价/回售触发价/剩余规模/评级/溢价率/转股价值等 26 字段。**评级：中**。用途：持仓股含可转债时的强赎风险前置排除。

### 6. `get_pricevol`

- 持仓股 601696.SH / 920808.BJ：成功，但**仅返回 LastClose/Now/Volume 三个字段**——并不是真正的分价位成交量分布，信息量被 `get_market_snapshot` 完全覆盖。**评级：低，建议不用**。

### 7. 追加探测：`get_scjy_value_by_date`

- 目的：SC23（连板家数）、SC24（涨跌停个数）、SC30（市场高度/2板以上个数）、SC33（市场总封单金额）、SC35（换手板家数/回封率）正是"市场情绪/连板高度"最直接的指标
- 实测（year=0 最新 / year=2026+mmdd=717 历史 两种）：均 ErrorId=10 "json has no table_list" → 与 `get_scjy_value` 同源权限问题，**确认不可用**

---

## 三、可靠性分级汇总

| 接口 | 评级 | 接入建议 |
|---|---|---|
| formula_process_mul_xg / formula_xg | 高 | **立即接入**（先解决客户端预建公式的运维流程） |
| download_file type=3 舆情 | 高 | **立即接入**（每日 1-2 次，落盘解析 JSON） |
| download_file type=4 综合信息 | 高 | **立即接入**（每日 1 次，作为题材/概念标签源） |
| get_ipo_info | 中高 | 接入（低频，每日 1 次） |
| get_trackzs_etf_info | 中 | 观察（指数代码格式 `.SH/.SZ` 需固化进封装） |
| get_gb_info / get_kzz_info | 中 | 观察（按需，B1 前置排除辅助） |
| download_file type=2 ETF申赎 | 中 | 观察（盘前时段复验内容是否非空） |
| download_file type=1/5/6 | 低 | **暂不用**（参数格式未探明 + 可打挂服务） |
| get_pricevol | 低 | 不用 |
| get_scjy_value_by_date | 不可用 | 不用（同 get_scjy_value 权限问题） |

---

## 四、重要风险记录：TQ 服务被打挂

- 23:2x 左右，`download_file` down_type=1 传整数年份 `2025` 后请求挂起（120s 客户端超时），此后 **TQ HTTP 服务对所有接口（含此前正常的 get_match_stkinfo）持续无响应，截至 23:43 已超 20 分钟未恢复**，TdxW 进程 CPU 无增长（疑似服务端单线程死锁/阻塞，而非在下载）。
- 影响：本次摸底后续 type1/5/6 复测全部无法完成；**strategy_team 管线若共用该服务，此时段内所有 TQ 调用均会失败**。
- 结论：`download_file` type1/5/6 在服务恢复并探明正确参数格式前**禁止接入管线**；任何对 download_file 的调用必须带客户端超时和熔断。
- 恢复方式：需要重启 TdxW 客户端（本报告不含此操作）。

---

## 五、与 strategy_team 缺口的映射

| 缺口 | TQ 可补方案 | 状态 |
|---|---|---|
| 连板高度/涨停池 | 客户端预建选股公式 + `formula_process_mul_xg` 全 A 批跑；或 `get_more_info` 逐股 LastZTHzNum/EverZTCount | 可补（需预建公式） |
| 市场级情绪指标（连板家数/封单总额/回封率） | `get_scjy_value_by_date` SC23/30/33/35 | ✗ 权限不可用 |
| 题材舆情 | down_type=3 sentiment.json 当日公告/新闻流 | 可补，当日实时 |
| 题材/概念归属 | down_type=4 miscinfo.json 全市场概念标签 | 可补 |
| 龙虎榜 | down_type=6 | 未探明，服务挂死，暂不可补 |
| 资金流向 | get_more_info Zjl/Zjl_HB（已有）+ trackzs ETF 份额变动 | 部分可补 |
| B1 前置排除 | get_ipo_info（次新/申购）、get_kzz_info（强赎）、get_gb_info（股本变动） | 可补 |
