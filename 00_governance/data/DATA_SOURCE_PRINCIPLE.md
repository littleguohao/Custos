# 数据源原则与现状

## 原则（三条，就这么多）

1. **本地 TDX 优先。** 能从本地通达信拿的，不走 HTTP。理由见下方「一个真实教训」。
2. **降级必须留痕。** 拿不到数据时可以降级，但**不得让调用方看不出来**——
   要么抛错，要么在 `attrs` / `quality` / 返回结构里标明。静默返回空是本仓库
   反复出事的头号原因。
3. **新增数据源前先查本地能不能做。** 检查顺序见文末。

> 其余内容都是**现状**：各数据源当前状态、已知问题、实测数据。
> 数据需求与获取方式的全景见 `DATA_SOURCE_COVERAGE_MATRIX.md`；
> 接口用法见 `TDX_LOCAL_INTERFACES.md` / `MOOTDX_INTERFACES.md` / `QLIB_LOCAL_DATA.md`。
> 2026-08-06 重写：此前这份文档是「原则 + 教训 + 清单 + 查证结论」混编。

---

## 为什么原则一重要——一个真实教训

`get_stock_name_map()`（股票名称 = ST 判定的唯一依据）曾被判定
「2026-07 起持续失败（`'>' NoneType`）」，于是引入**东财 HTTP** 当主路径。

真实原因是 `local_tdx_data._get_client()` **永不重连**：连接一断，
`stock_count()` 返回 `None`，mootdx 内部 `if counts > 0` 就抛 `'>' NoneType`。
**接口本身完好。**

给客户端加上时效与自动重建后，本地协议全面可用（实测深市 23906 条、沪市 27642 条，
ST 识别正确）。而当时的应对——引入东财 HTTP——反而把系统绑到了**更不稳的源**上。

⇒ **误诊导致的"降级"比原来的故障更糟。** 判定某个源不可用之前，先确认不是我们自己的
连接管理问题。

### 这个反模式后来又犯了两次

同一个「global 缓存客户端、只判 `is None`、永不重建」的写法：

| 位置 | 何时 |
|---|---|
| `local_tdx_data._get_client` | 首次发现并修复（503b77d） |
| `market_timing/tdx_ext_quotes.py` | **同一天**新写的代码里又写了一遍（3c7c833） |
| `collect_holding_quotes.py` | 次日全仓复查时发现第三处（aeb3e25） |

第三处最危险——它是 14:45/17:00 采集持仓行情的必经路径，连接死了整条链就没行情。

⇒ **写进文档不等于内化。** 补救不是再写一遍规范，而是
`tests/test_tdx_connection_hygiene.py`：AST 扫描所有用 global 缓存 TDX 客户端的函数，
要求存在「除 `is None` 之外的重建路径」。

---

## 连接管理要求（硬要求，有可执行检查）

任何缓存 TDX 客户端的模块必须满足：

- 有**时效**（`CLIENT_MAX_AGE_SEC` 之类）或**显式失效**路径，不能只判 `is None`
- 协议调用必须经 `_with_client_retry` / `_client_call` 之类的包装，
  失败后**重建客户端再试一次**
- 检查由 `tests/test_tdx_connection_hygiene.py` 强制。写那份检查时自己踩了三个坑，
  都记在文件里：字符串匹配被残留常量骗过 → `except SyntaxError: return []` 让验证
  静默放行 → 把只置 None 的 invalidate 函数误判为反模式。
  **教训：检查函数本身也要有单元测试。**

---

## 当前数据源状态

标记与 `DATA_SOURCE_COVERAGE_MATRIX.md` 一致：✅ 可用 / ⚠️ 有已知问题 / 🚫 不可用 / ❌ 未接入。

| 数据 | 状态 | 现役来源 | 实测 |
|---|---|---|---|
| A 股日线 | ✅ | 本地 vipdoc `.day` | **4.7ms**/股 |
| 日线（前复权） | ⚠️ | vipdoc + 自算 xdxr | **8.3ms**/股，见「前复权」 |
| 本地股票清单 | ✅ | vipdoc 目录枚举 | 40ms / 5536 只 |
| 在线股票清单 | ⚠️ | mootdx `client.stocks()` | 16640ms / 过滤后约 5300 只 |
| 权息 xdxr | ⚠️ | TDX 协议 + 本地缓存 | 缓存命中 **0.2~0.4ms**；BJ 返回空 |
| 财务（585 字段） | ✅ | mootdx Affair | 640ms / 114 行 × 585 列 |
| 股票名称（ST） | ✅ | 东财 ulist → TQ → 缓存 | — |
| 板块/概念 | ✅ | 本地 `tdxzs3.cfg` + TQ | miscinfo 1132ms |
| 交易日历 | ✅ | SSE 官方 + TQ | — |
| 持仓实时行情 | ✅ | TQ 快照 → 域B(腾讯/新浪) | TQ 约 80ms |
| **在线 TDX 行情** | 🚫 | ~~mootdx `bars`/`quotes`~~ | **已标记不可用**，见下 |
| PIT 财务 | ✅ | 东财 datacenter | 单期约 24 页 |
| 真市值/总股本 | ✅ | 东财 datacenter | 单期约 11 页 |
| 资金流向 | ✅ | 东财 push2 | — |
| 海外行情 | ✅ | Yahoo → TDX ext 降级 | — |

---

## 🚫 在线 TDX 行情已标记为不可用（2026-08-06 owner 拍板）

探针实测（`--repeat 3`，目标机）：

| 接口 | p50 | 结果 |
|---|---|---|
| `get_online_bars()` | **12949ms** | DataFrame 0 行 × 0 列 |
| `get_online_index()` | **9992ms** | DataFrame 0 行 × 0 列 |
| `get_snapshot()` | 70ms (max 3114ms) | dict 0 键 |
| `get_stock_list()`（`client.stocks`） | 16640ms | 51567 项 ✅ **可用** |

三个失败的都**不抛异常**，返回空值 ⇒ 调用方看不出「这只票没数据」与「在线源坏了」的
区别（违反原则二）。而 `get_ohlcv_table` 在本地数据 stale 时会走这条兜底：
**13 秒换一个空 DataFrame**；14:45/17:00 采集 N 只持仓就是 N×13 秒纯等待。

**处理**：`_online_quotes_enabled()` 默认返回 False，`get_online_bars` /
`get_online_index` / `get_snapshot` 在 `_get_client()` **之前**短路（建连本身就要花时间）。
只关 `bars`/`quotes` 两族，**不关 `client.stocks()`**。
要重新启用（换网络环境、或验证服务端恢复）：设 `TDX_ONLINE_QUOTES=1`。
由 `tests/test_tdx_connection_hygiene.py::TestOnlineQuotesMarkedUnavailable` 锁住。

⚠️ **尚未查明是环境问题（防火墙/服务器不可达）还是 mootdx 的问题。**
标记为不可用是止损，不是结论。

---

## ⚠️ 前复权：全链默认口径，但从未对账

owner 2026-08-04 拍板全链统一前复权：`get_ohlcv_table(adjust="qfq")` 成为默认，
因子基于**通达信 xdxr 权息事件本地自算**（分红/送转/配股/缩股）。

理由是充分的：未复权数据把除权跳空当成真实暴跌 ⇒ 假止损、假 `J<13` 信号、假跌停。

### 但有三个未解决的问题

**① 已对账，且我们这边是对的（2026-08-06）。** `reconcile_qfq.py` 拿 qlib 作独立参照
逐日比对，结论：

    tdx  的复权收益与未复权收益 **0 天偏离**（|t-raw| = 0.0000%，每一行都是）
    事件日的比值跳变全部 ≤0.39%、大部分 <0.1%

非事件日复权只是乘同一个当日因子 ⇒ 收益必须与未复权一致，这是数学上必须成立的，
tdx 严格满足。**分歧来自 qlib 侧：它用的是「减去累计现金分红」的加法调整，
不是乘法前复权**（详见 `QLIB_LOCAL_DATA.md`）。

⚠️ 这不等于自算前复权「完全正确」——对账只能证明它与「未复权收益」自洽、与 qlib 的
事件处理一致。**绝对正确性（因子基准、送转比例公式）仍无第三方权威序列可比。**

**② BJ 股票的未复权数据被标成「已前复权」。** 实测：

```
get_xdxr("920808") → []            # 不抛错，返回空列表
apply_qfq(df, []) → attrs: {'adjust': 'qfq', 'adjust_events': 0}
价格是否被改动: False
```

`get_xdxr` 对 BJ 拿不到权息却返回空列表（而非报错），`qfq_table` 因此走**成功路径**。
⇒ **`adjust == "qfq"` 不能推断已正确复权**——它可能是「真的从未除权」，
也可能是「该市场取不到事件」。唯一线索 `adjust_events == 0` **无任何调用方检查**。

影响面：BJ 约占 universe 4.8%，每轮全市场回测约 5% 样本带着除权假跳空在跑。
**修法待定**（需先确认 BJ 无 xdxr 是接口限制还是调用方式问题）。

**③ 复权失败率不可见。** `qfq_table` 失败时只打一条 `[WARN]` 到 stderr，**没有汇总**。
3000 只票的回测日志里那些 WARN 早被淹没 —— 我们不知道实际有多少只票没复权成功。

---

## 三项东财依赖的查证结论（2026-08-04）

原则一说本地优先，这三项**查证后确认应该保留东财**，不是妥协：

### ① PIT 财务：这是正确的架构

本地 TDX 的财务专项文件**只有最新一期快照**，没有「这条数据什么时候可见」的信息。
而回测要的是 point-in-time：用最新财报回测等于**用未来信息**。
东财 `RPT_LICO_FN_CPD` 带 `NOTICE_DATE`（公告日），这是本地拿不到的能力。
⇒ 保留东财是架构选择。

### ② 资金流向：不建议改

主力净流入需要逐笔委托明细聚合，本地 TDX 不提供。东财 push2 的 `ut` 参数是网页端
硬编码的公开值（非凭据、非密钥，全网通用），抽成常量只为不散落三处魔法串。

### ③ 海外行情：Yahoo 主路径 + TDX ext 降级

TDX ext 有海外品种但覆盖不全、代码映射需维护。Yahoo 为主、ext 为降级（3c7c833）。

---

## 新增数据源时的检查顺序

1. **本地 vipdoc / block / 专项文件**能不能拿？（最快，4~8ms，无网络依赖）
2. **TQ-Local** 能不能拿？（需 TdxW 运行；注意 `download_file` 的危险 `down_type`，
   见 `TDX_LOCAL_INTERFACES.md`）
3. **TDX 在线协议**能不能拿？（⚠️ 目前 `bars`/`quotes` 已标记不可用）
4. 以上都不行，才考虑 HTTP 外部源。**并且要说明为什么本地做不到**——
   像上面三项那样留下查证结论，而不是直接接一个 HTTP。

⚠️ 每一步都要问：**拿不到时怎么让调用方知道？**（原则二）

---

## 代码规范：**模块级常量 + 运行时替换 = 陷阱**（同一天踩了三次）

这一类问题的共同形态是：**某个值在"早于你以为的时刻"被固定下来了**，
于是运行时替换它没有效果。三种变体都在 2026-08-06 实际踩到：

### ① 同一文件被加载成两个模块

`07_tools/` 下的模块既被 `import x` 直接引用、也被 `from pkg import x` 引用时，
会成为**两个独立的模块对象** —— 模块级缓存/常量各存一份，
monkeypatch 只影响其中一个 ⇒ **测试通过而生产失效**。

实际案例：`reconcile_qfq.pick_auto` 内部 `import adjust_factors as A` 读 `A.CACHE_DIR`，
而测试 patch 的是 `local_tdx.adjust_factors.CACHE_DIR` —— 两个对象，patch 无效。

### ② 默认参数在**函数定义时**求值

```python
def list_bundles(root=DEFAULT_Q_ROOT):   # ← DEFAULT_Q_ROOT 在 def 执行时就被绑定
    ...
```

之后 `monkeypatch.setattr(s_data, "DEFAULT_Q_ROOT", tmp)` **对这个默认值毫无影响**，
因为它早已是一个具体的 Path 对象。实际案例：`gap_report` 调 `Q.list_bundles()`
不传 root，测试怎么 patch 都读的是 `E:\S_DATA`。

### ③ 从常量派生的常量

`CALENDAR_FILE = GOVERNANCE / "CN_TRADING_CALENDAR.json"` 这类派生常量在导入时就算好了，
改 `GOVERNANCE` 不会让它跟着变。

### 规范

1. **路径只在 `paths.py` 定义一次**，模块不要自己拼 `BASE / "00_governance" / ...`
   —— 由 `tests/test_base_path_depth.py::test_modules_do_not_rebuild_governance_paths`
   强制（AST/文本扫描，一加上就抓到一处漏改）。
2. **需要在测试里替换的值，做成显式参数**，不要读模块全局：
   `pick_auto(n, cache_dir=None)` / `gap_report(sample, root=None)`。
   `None` 时才回落到模块默认 —— 这样默认值在**调用时**才解析。
3. **统一导入形式**：同一个模块在全仓只用一种引用方式。
4. 判断依据：**如果一个值需要在测试里被替换，它就不该是模块级常量的默认参数。**
5. 由 `tests/test_base_path_depth.py::TestNoPatchingDefaultArgConstants` 强制：
   AST 扫出「哪些常量在哪个模块里被用作默认参数」，再扫测试里有没有 patch 它们
   —— 这个组合正是我踩的坑，patch 了也不生效、症状只是「行为不符」而非报错。
   ⚠️ 检查**必须按模块分开算**：第一版把常量名跨模块收成一个集合，于是
   `amv_state.LEDGER`（在函数体内读、patch 完全有效）被
   `fetch_market_cap.LEDGER`（确实是默认参数）连坐误报 6 处。
   同名常量在不同模块里的用法可以完全不同。

⚠️ 为什么单列一节：这三种变体一天内出现三次，而它们的症状都是「测试绿、生产错」
或「patch 没生效但没人发现」—— 属于最难查的一类。
与「连接永不重连」（跨两天犯三次）同理：**写进文档不等于内化，要靠可执行检查兜住**。
