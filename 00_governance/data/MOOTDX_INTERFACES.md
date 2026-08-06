# mootdx 接口使用方式

> mootdx 是本项目访问通达信数据的 Python 库。本文写**我们实际用到的 API 面**、
> 每个入口的用途/实测性能/已知坑，以及一条硬要求（连接管理）。
>
> 2026-08-06 新建。性能数据来自 `probe_data_sources.py` 实测（目标机，`--repeat 3`）。
> 标记：✅ 可用 / ⚠️ 有已知问题 / 🚫 不可用。
>
> 通达信生态的全景（含 TQ-Local、本地文件布局）见 `TDX_LOCAL_INTERFACES.md`；
> 数据源选择原则见 `DATA_SOURCE_PRINCIPLE.md`。

## 三个入口，用途完全不同

| 入口 | 数据来源 | 需要什么 | 我们的用法 |
|---|---|---|---|
| `mootdx.reader.Reader` | **本地** vipdoc 文件 | 只需 `TDX_ROOT` | ✅ 主力：日线、板块 |
| `mootdx.quotes.Quotes` | **在线** TDX 服务器 | 网络 + 服务器可达 | ⚠️ 部分可用（见下） |
| `mootdx.affair.Affair` | 财务专项文件下载 | 网络 | ✅ 585 字段财务 |

全仓调用点分布（`grep` 实测）：

```
Reader.factory()      ×6      reader.daily()  ×6      reader.block()  ×2
Quotes.factory()      ×11     client.bars() ×3  client.quotes() ×2
                              client.get_security_list() ×2  q.xdxr() ×4
Affair.files()        ×3      Affair.fetch/parse
```

⚠️ **`Quotes.factory()` 有 11 处** —— 这是「连接永不重连」反模式的高发区，
已修三处。任何新增调用都必须满足文末的连接管理硬要求。

---

## 一、Reader（本地 vipdoc）✅

### `reader.daily(symbol)` —— 日线

| 项 | 值 |
|---|---|
| 状态 | ✅ |
| 我们的封装 | `local_tdx_data.read_vipdoc_daily(code, strict=False)` |
| 实测 | **4.7ms**/股（1214 行 × 9 列） |

**两个必须知道的坑：**

**① 返回 DatetimeIndex 而非 `date` 列。** 传入分析脚本前必须 `reset_index()`，
否则下游按 `df["date"]` 取值会 KeyError。`read_vipdoc_daily` 已处理。

**② 读不到时返回带 `attrs["missing_reason"]` 的空 DataFrame**，
而不是抛错——调用方可据此区分 `file_not_found` / `empty_file` / `reader_empty`。
但 **`TDX_ROOT` 本身配错一律 raise**（`_assert_tdx_root`）：
「一只票没数据」与「通达信路径没配」必须区分开，否则全市场初筛会静默缩到只剩自选池。

`strict=True` 时读不到直接 raise ——给「拿不到本地数据就必须停下来」的调用方
（回测 universe、EOD 校验）用。默认 False 是为了保留在线回退路径。

### `reader.block()` —— 板块成分

| 项 | 值 |
|---|---|
| 状态 | ✅ |
| 用途 | 本地 `block.dat` 板块成分股 |
| 备注 | 01583e8 起改本地文件，去掉 tqcenter 依赖 |

---

## 二、Quotes（在线 TDX 协议）⚠️ 部分可用

### 🚫 已标记不可用：`bars` / `quotes` / 指数

| 方法 | 我们的封装 | p50 | 结果 |
|---|---|---|---|
| `client.bars()` | `get_online_bars()` | **12949ms** | 0 行 × 0 列 |
| `client.quotes()` | `get_snapshot()` / `get_snapshots()` | 70ms | 0 键 |
| 指数 bars | `get_online_index()` | **9992ms** | 0 行 × 0 列 |

三个都**不抛异常**，返回空值。owner 2026-08-06 拍板标记为不可用：
`_online_quotes_enabled()` 默认 False，在 `_get_client()` **之前**短路
（建连本身就要花时间）。`TDX_ONLINE_QUOTES=1` 可重新启用。

⚠️ 尚未查明是环境问题（防火墙/服务器不可达）还是 mootdx 的问题。

### ✅ 仍可用：`stocks` / `get_security_list` / `xdxr`

| 方法 | 我们的封装 | 实测 | 备注 |
|---|---|---|---|
| `client.stocks(market)` | `get_stock_list()` | 16640ms / 51567 项 | ⚠️ 见下方「两个宇宙口径」 |
| `client.stocks(market)` | `get_stock_name_map()` | — | ST 判定用；深 23906 + 沪 27642 条 |
| `client.get_security_list()` | — | — | ⚠️ **不给北交所**（`market=2` 实测空返回） |
| `q.xdxr(symbol)` | `adjust_factors.fetch_xdxr()` | 缓存命中 **0.2~0.4ms** | 前复权的权息来源 |

**⚠️ 两个宇宙口径不能混用：**

| 来源 | 数量 | 内容 |
|---|---|---|
| `list_local_vipdoc_codes()` | **5536** | A 股个股，**含 BJ**，本地实有文件 |
| `get_stock_list()` 原始 | **51567** | 含指数/ETF/债券，仅沪深 |
| `get_stock_list()` 现默认 | 约 **5300** | 已过滤为 A 股个股，仅沪深 |

2026-08-06 起 `get_stock_list(ashare_only=True)` 为默认。
原因：`backtest_factors:2285`（不传 `--universe-local` 时的 universe 源）
**没有下游过滤**，51567 项直接进 `sample_codes()` ⇒ 抽样约 89% 概率抽到非个股。
案底见 `DATA_SOURCE_COVERAGE_MATRIX.md` 备注 C。

**`xdxr` 的已知问题：BJ 返回空列表而非报错**，导致未复权数据被标成「已前复权」。
见 `DATA_SOURCE_PRINCIPLE.md` 问题②。

---

## 三、Affair（财务专项文件）✅

| 项 | 值 |
|---|---|
| 状态 | ✅ |
| 方法 | `Affair.files()` ×3 / `Affair.fetch()` / `Affair.parse()` |
| 我们的封装 | `local_tdx_data.get_financial_data(report_period="")` |
| 实测 | **640ms** / 114 行 × 585 列 |

⚠️ **只有最新一期快照，没有公告日** ⇒ 不能用于 point-in-time 回测
（用最新财报回测等于用未来信息）。需要可见日走东财 PIT。

⚠️ **585 列的含义随 TDX 版本变化。** `financials.py` 有列名关键词自动映射 +
`--inspect` 确认最终映射；曾出过「`revenue` 自动映射到比率列」「重复列名导致现金流全场
None」这类问题（019a0cc / 6a76f57）。改动映射后必须跑 `--inspect` 核对。

### 已废弃：`mootdx.contrib.adjust.get_adjust_year`

原用于取复权因子（同花顺口径）。**已被自算 xdxr 前复权取代**（owner 2026-08-04 拍板），
仅 `local_tdx_data.py --mode adjust` 的 CLI 保留。不要在新代码里用。

---

## 连接管理硬要求（有可执行检查）

`Quotes.factory()` 有 11 处调用，而「global 缓存客户端、只判 `is None`、永不重建」
这个反模式**跨两天犯了三次**：

| 位置 | 何时 |
|---|---|
| `local_tdx_data._get_client` | 首次发现并修（503b77d） |
| `market_timing/tdx_ext_quotes.py` | **同一天**新写的代码里又写了一遍（3c7c833） |
| `collect_holding_quotes.py` | 次日全仓复查发现第三处（aeb3e25） |

第三处最危险——它是 14:45/17:00 采集持仓行情的必经路径。

**症状不像连接问题**：连接一断，`stock_count()` 返回 `None`，
mootdx 内部 `if counts > 0` 抛 `'>' NoneType`。当时被误判成「接口失效」，
于是把 ST 判定改走东财 HTTP —— **绑到了更不稳的源上**（详见
`DATA_SOURCE_PRINCIPLE.md`「一个真实教训」）。

### 要求

1. 缓存客户端必须有**时效**或**显式失效**路径，不能只判 `is None`
2. 协议调用必须经 `_with_client_retry` / `_client_call` 包装，失败后**重建再试一次**
3. 由 `tests/test_tdx_connection_hygiene.py` 强制：AST 扫描所有用 global 缓存 TDX
   客户端的函数，要求存在「除 `is None` 之外的重建路径」

⚠️ 写那份检查时踩了三个坑（都记在文件里）：字符串匹配被残留常量骗过 →
`except SyntaxError: return []` 让反向验证静默全绿 → 把只置 None 的 invalidate 函数
误判为反模式。**教训：检查函数本身也要有单元测试**，以及**反向验证要用可控样本，
不要动生产代码**。
