# 通达信本地接口：已接入的用法 + 探过但没接的能力

> 本文合并 `LOCAL_TDX_DATA_SOURCE_STATUS.md`（2026-07-09，353 行）与
> `TQ_INTERFACE_PROBE_2026-07-20.md`（122 行）。合并理由：前者是"状态"、后者是"探测清单"，
> 混在一起读的人分不清**哪个能用、哪个只是探过**。
>
> 2026-08-06 重写，性能数据来自 `probe_data_sources.py` 实测（目标机，`--repeat 3`）。
> 标记：✅ 已接入实测可用 / ⚠️ 有已知问题 / 🚫 不可用 / ❌ 未接入。
>
> ⚠️ 本文只写**通达信生态**（本地文件 + TQ-Local + TDX 协议）。
> mootdx 库的 API 面见 `MOOTDX_INTERFACES.md`（qlib 接口已于 2026-08-24 v0.109
> 整体删除，历史档案见 `QLIB_LOCAL_DATA.md`）。

## 通达信生态的四条访问路径

| 路径 | 依赖 | 特点 |
|---|---|---|
| **本地文件** | 只需 `TDX_ROOT` 指向安装目录 | 最快（4~8ms），无网络、无需客户端运行 |
| **TQ-Local (HTTP JSON-RPC)** | **需 TdxW.exe 运行** | 本机 `http://127.0.0.1:17709/`，约 80ms |
| **TDX 在线协议** | mootdx Quotes（走公网服务器） | 🚫 `bars`/`quotes` 已标记不可用 |
| **专项文件下载** | mootdx Affair | 财务数据，640ms 一次性下载 |

⚠️ **TQ 的访问点分散在四处**，不是一个统一入口：

```
tq_http.py                  4 个薄封装（snapshot / more_info / stock_info / ping）
concept_tags.py             download_file(down_type=4)
formula_screen.py           formula_process_mul_xg
trading_calendar.py         get_trading_dates —— 走 tq_http.call（2026-08-06 已收敛）
```

**四条路径现已全部走 `tq_http.call`**（2026-08-06 收敛完 `trading_calendar`）。
收敛前它自己拼 JSON-RPC + `urlopen`，与 `tq_http` 是**同一个服务**
（两处都硬编码 `http://127.0.0.1:17709/`）却各写一套，于是拿不到：

· **TdxW 未运行的预检** —— 否则只有一个 `connection refused`，
  分不清「服务没起」还是「端口写错」。收敛后 `source.last_error` 直接写
  `TQ get_trading_dates 失败[tdxw_not_running]: TdxW.exe 未运行`
· **统一错误分类**（`tdxw_not_running`/`timeout`/`connection_failed`/`request_failed`）
· 将来加在 `call` 里的**安全拦截**自动覆盖这条路径，不会漏

`rpc_trading_dates` 仍**保持 raise 契约**：`refresh` 依赖 `except Exception` 记
`last_error` 并保住旧缓存（`status="cache_preserved"`），改成返回 dict 会让失败被当成成功。
端点一致性由 `tests/test_trading_calendar.py::TransportConvergenceTests` 守住。

---

# 第一部分：已接入的用法

## 一、本地文件（不需要 TdxW 运行）

### 1. 日线 K 线 —— `TDX_ROOT/vipdoc/{sh,sz,bj}/lday/{mkt}######.day`

| 项 | 值 |
|---|---|
| 状态 | ✅ |
| 入口 | `local_tdx_data.read_vipdoc_daily(code)` |
| 实测 | **4.7ms**/股（浦发 1214 行 × 9 列） |
| 返回 | `DataFrame[date, code, open, high, low, close, amount, volume, …]` |

⚠️ **上证指数是 `999999`，不是 `000001`** —— vipdoc 里 `sh000001` 不是上证指数（ebd9982 修）。

⚠️ **mootdx Reader 的 `daily()` 返回 DatetimeIndex 而非 date 列**，
传入分析脚本前需 `reset_index()`。`read_vipdoc_daily` 已经处理。

### 2. 股票清单 —— 目录枚举

| 项 | 值 |
|---|---|
| 状态 | ✅ |
| 入口 | `list_local_vipdoc_codes(ashare_only=True)` |
| 实测 | 40ms / **5536 只** |

按 vipdoc 的市场目录 + 代码前缀判定 A 股个股（`_is_ashare_stock_file`）：

```
sh: 600/601/603/605/688          排除 000/880 指数、5xx ETF
sz: 000/001/002/003/300/301      排除 15/16/18 ETF、399 指数
bj: 43/83/87/88/920              ⚠️ 含北交所
```

⚠️ **它是回测宇宙的推荐来源**（`--universe-local`）。与在线 `get_stock_list()`
是**两个口径**：后者原始返回 51567 项（含指数/ETF/债券），2026-08-06 起默认过滤为
A 股个股约 5300 只，且不含 BJ。

⚠️ **宇宙会随通达信下载变动** —— 实测一轮扫描中 5535 → 5536，导致 `sample_codes(seed=0)`
抽到另一组票。长时间回测要钉死宇宙：`m2_stop_sweep` 的 `--pin-universe` 2026-08-12 起
**默认开**（#17；显式关闭用 `--no-pin-universe`，见 `research/R13_meta_reproducibility.md`）。

### 3. 板块分类 —— `tdxzs3.cfg` / `tdxzs.cfg` / `block.dat` / `incon.dat`

| 项 | 值 |
|---|---|
| 状态 | ✅ |
| 入口 | `tq_sector.classify_sector(code)` / `load_sector_names()` |
| 数据 | `tdxzs3.cfg` **含 881xxx 细分行业**，优先于 `tdxzs.cfg` |

**881xxx 官方细分行业 = 每股恰好一个权威归属**，2026-08-04 实测 5546 只零冲突（f309ac6）。
这是「板块」列的现役依据；概念标签（多对多）走 TQ miscinfo，是另一回事。

⚠️ 审计 B11 曾把 `881xxx` 细分行业指数判成北交所（前缀 `88` 与 BJ 段重叠）。
判市场必须用 `code_utils.market_of`，不要自己写前缀规则。

### 4. 财务专项文件

| 项 | 值 |
|---|---|
| 状态 | ✅ |
| 入口 | `get_financial_data(report_period="")` → mootdx `Affair.files/fetch/parse` |
| 实测 | **640ms** / 114 行 × 585 列 |

⚠️ **只有最新一期快照，没有公告日** ⇒ 不能用于 point-in-time 回测。
需要「什么时候可见」时走东财 PIT（见 `DATA_SOURCE_PRINCIPLE.md` 查证结论①）。
⚠️ **列含义随 TDX 版本变化**，`financials.py` 有列名关键词自动映射 + `--inspect` 确认。

---

## 二、TQ-Local（需 TdxW.exe 运行）

统一入口 `tq_http.call(method, params)`，返回 `{"ok", "value", "error"}`，**绝不 raise**。
进程级预检 `tq_sector.is_tdxw_running()`（实测 76ms）。

### 已接入的 6 个方法

| 方法 | 状态 | 入口 | 实测 | 用途 |
|---|---|---|---|---|
| `get_match_stkinfo` | ✅ | `tq_http.ping()` | **149ms** / 48 条 | 连通性探测 |
| `get_market_snapshot` | ✅ | `tq_http.snapshot(code)` | **80ms** | 持仓/盘中快照。字段直挂 result |
| `get_stock_info` | ✅ | `tq_http.stock_info(code)` | **80ms** | 股票名称（ST 判定） |
| `get_more_info` | ✅ | `tq_http.more_info(code)` | **83ms** | 扩展字段。传 `field_list` 实际仍返回全字段 |
| `download_file(down_type=4)` | ✅ | `concept_tags.py` | **1132ms** | miscinfo 8.1MB / 68161 条，概念主题标签 |
| `formula_process_mul_xg` | ✅ | `formula_screen.py` | — | 公式批量选股 |
| `get_trading_dates` | ✅ | `trading_calendar.py` | 交易日历刷新（周五 14:35 cron） | 走 tq_http.call；`--endpoint` 可覆盖 |

### ⚠️ 周期串是 `"1d"`，不是 `"day"`

板块指数历史（`stock_period` 类接口）的周期参数**缺省或写错都会报
`不支持day周期`**。正确值是 `"1d"`。

`fetch_sector_index_history.py` 现在**自动探测周期串**（day→1d，3577eeb 修），
不再依赖调用方记住。生产验证 587/587 板块（ba8a396）。

### ⚠️ 两条必须遵守的调用约定（都做成了代码拦截）
**① `stock_code` 必须带市场后缀。** 传裸 6 位得到 `ErrorId=2 stock_code error`
——2026-08-06 探针实测踩到（探针传 `"600000"`，三个 stock_code 类方法全挂，
而不吃 stock_code 的 `get_match_stkinfo`/`download_file` 正常）。

生产代码都先过 `local_tdx_data.normalize_code()`（`600000` → `600000.SH`），
但接口原先不设防。现在 `tq_http.call()` 校验格式并在报错里给出修法。
**刻意不自动补后缀**：补错市场比报错更糟（`600000.SZ` 是另一只票或不存在）。

**② `download_file` 的 `down_type` 只放行 4。** `SAFE_DOWN_TYPES = {4}`，
非白名单直接返回 `unsafe_down_type` 且**不发请求**。
理由见下方「TQ 服务被打挂」。确需探测传 `allow_unsafe_download=True` 签名。

由 `tests/test_tq_http.py::TestUnsafeDownTypeGuard` / `TestStockCodeFormatGuard` 锁住。

### ⚠️ TQ 服务可被打挂（真实事故）

2026-07-20 探测 `download_file down_type=1`（十大股东）时：
`601696.SH` + 整型年份 `2025` → **请求挂起 120s 超时，此后整个 TQ 服务对所有请求无响应**。
down_type 5（经营分析）/ 6（龙虎榜）的后续探测因此无法区分「参数错」与「级联挂死」。

⇒ TdxW 一挂，**选股链与持仓行情一起没了**。这是白名单拦截的由来。
另：`tq_sector` 对 `get_stock_list_in_sector` 做了 20~50ms 限速，
「一口气打过去会把 TdxW 打满」。

---

## 三、TDX 在线协议 🚫

| 接口 | 状态 | p50 | 结果 |
|---|---|---|---|
| `client.bars()` → `get_online_bars()` | 🚫 | **12949ms** | 0 行 × 0 列 |
| `client.quotes()` → `get_snapshot()` | 🚫 | 70ms | 0 键 |
| 指数 → `get_online_index()` | 🚫 | **9992ms** | 0 行 × 0 列 |
| `client.stocks()` → `get_stock_list()` | ✅ | 16640ms | 51567 项 |
| `client.get_security_list()` | ⚠️ | — | **不给北交所**（market=2 实测空返回） |
| `q.xdxr()` → 权息 | ✅ | 缓存命中 0.2~0.4ms | 见 `adjust_factors` |

`bars`/`quotes`/指数三族已由 `_online_quotes_enabled()` **默认关闭**
（owner 2026-08-06 拍板），在 `_get_client()` 之前短路。
`TDX_ONLINE_QUOTES=1` 可重新启用。详见 `DATA_SOURCE_PRINCIPLE.md`。

⚠️ **尚未查明是环境问题还是 mootdx 的问题。** 标记不可用是止损，不是结论。

---

# 第二部分：探过但没接的能力（附风险等级）

来源：2026-07-20 的 TQ-Local 接口摸底。**这些都没有生产代码在用**，
列在这里是为了不重复探测——尤其是已知会打挂服务的那几个。

## 风险分级

| 等级 | 含义 |
|---|---|
| 🟢 低 | 一次调用、参数简单、实测稳定 |
| 🟡 中 | 能调通但产物为空 / 需特定时段 / 需参数试错 |
| 🔴 高 | **实测可打挂 TQ 服务**，或参数格式苛刻到无法安全试错 |

## `download_file` 的 6 种 down_type

| type | 内容 | 风险 | 实测结论 |
|---|---|---|---|
| 4 | 综合信息 miscinfo | 🟢 | **已接入**。8.1MB / 68161 条，5529 只 × 多类别（10001 概念标签、10004 主营、10010 亮点） |
| 3 | 舆情 sentiment | 🟢 | 556KB / 1196 条，**当日实时**，字段 `Issue_date/title/Summary`。**未接入** |
| 2 | ETF 申赎 PCF | 🟡 | `510300.SH`+`20260720` ErrorId=0 成功但产物**仅 2 字节（空）**；可能需盘前时段 |
| 1 | 十大股东 | 🔴 | 参数格式苛刻：纯代码→ErrorId=2；`.SH`+字符串年份→ErrorId=3；**`.SH`+整型年份→服务挂死 120s** |
| 5 | 经营分析 | 🔴 | 同 type1 参数矩阵均失败；且在 type1 挂死之后执行，无法区分参数错与级联挂死 |
| 6 | 龙虎榜 | 🔴 | `601696.SH` 无 `down_time` → ErrorId=3；带参重试发生在服务挂死后，全部超时 |

⚠️ type 1/5/6 已由 `SAFE_DOWN_TYPES` 白名单拦截。
**type 3（舆情）是这批里最值得接的** —— 🟢 低风险、当日实时、内容直接可用，
而第六类「公告与新闻」目前只有 RSS + cron LLM 的 `wenda_notice_query`。

## 其他探过的接口

| 接口 | 风险 | 实测结论 | 是否值得接 |
|---|---|---|---|
| `get_ipo_info` | 🟢 | `ipo_type=0, ipo_date=1` 返回 4+ 条未来新股，含 `Code/Name/SGDate/SGPrice/MaxSG/PE_Issue` | 打新用，与 B1 策略无关 |
| `get_trackzs_etf_info` | 🟢 | 跟踪指数的 ETF 列表 | 无需求 |
| `formula_get_all` / `formula_xg` | 🟢 | 公式列表与单股选股 | `formula_process_mul_xg` 已够用 |
| `get_gb_info` / `get_kzz_info` | 🟢 | 股本 / 可转债信息 | 股本已走东财 PIT；可转债无需求 |
| `get_pricevol` | 🟡 | 价量分布 | 未评估用途 |
| `get_scjy_value_by_date` | 🟡 | 追加探测，结论未记完整 | 待重探 |

⚠️ **第五类（龙虎榜/大宗交易）在覆盖矩阵里整类空缺**，而唯一的 TQ 路径
（`down_type=6`）是 🔴 高风险。要补这类数据，得先解决「怎么安全试探 down_type」
——建议在**独立的 TdxW 实例**上试，不要在生产实例上。

---

## 已知运行约束

- **TdxW.exe 必须运行**才有 TQ-Local；`is_tdxw_running()` 做进程级预检（76ms）
- TQ 请求要**限速**：`get_stock_list_in_sector` 之类循环调用给 20~50ms 间隔
- 通达信**盘后会下载数据** ⇒ vipdoc 文件与目录清单都会在运行期间变动
  （长时间回测要钉死窗口与宇宙）
- 北交所：TDX 服务器 `stocks()` 硬校验 `market in [0,1]`，绕过校验调 `market=2`
  实测也是空返回 ⇒ **BJ 只能走本地 vipdoc 文件或东财**
- BJ 的 xdxr 权息取不到（返回空列表而非报错）⇒ 见 `DATA_SOURCE_PRINCIPLE.md` 问题②
