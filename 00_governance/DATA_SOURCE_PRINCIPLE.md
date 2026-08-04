# 数据源原则：本地 TDX 优先

**owner 裁定（2026-08-04）：尽量使用本地 TDX 提供的接口，HTTP 接口不是很稳定。**

## 为什么这条原则重要——一个真实教训

`stock_names.py` 曾把「东财 push2 ulist」作为**主路径**，理由写在注释里：
「mootdx client.stocks —— **2026-07 起持续失败**（`'>' NoneType`），仅最后尝试」。

查证后发现：**接口本身完好，是我们自己的连接管理有 bug。**

```python
_client = None
def _get_client():
    if _client is None:                       # ← 永不重连
        _client = Quotes.factory(market="std")
    return _client
```

连接一旦断开（TCP 空闲超时 / 服务器踢连接 / 换网络），进程级单例仍持有死连接。
mootdx 的 `stocks()` 内部是 `if counts > 0`，而 `stock_count()` 失败返回 `None`
⇒ `None > 0` ⇒ `TypeError: '>' not supported between 'NoneType' and 'int'`。

于是一个**连接层的 bug** 被读成「上游接口失效」，应对方式是引入 HTTP 数据源绕过——
系统因此被绑到了更不稳的源上，而真正的 bug 留了一个月。

修好重连后实测：TDX 协议取全市场名称 **50725 条 / 1.6 秒**，ST 识别正确（221 只），
比东财分批 HTTP 又快又稳。

> **教训**：把「我们的调用方式有问题」误判为「上游数据源不行」，代价是架构被
> 不必要地复杂化。换源之前先确认自己的调用是对的。

## 当前数据源清单

| 数据 | 现用 | TDX 能力 | 状态 |
|---|---|---|---|
| A 股日线 | vipdoc `.day` 本地文件 | ✅ | **本地** |
| 前复权因子 | TDX 协议 `xdxr` 权息 | ✅ | **已改（2026-08-04）** |
| 总股本/流通股本/市值 | TDX 协议 `xdxr` `category=5` | ✅ | **已改（2026-08-04）** |
| 股票名称（ST 判定） | TDX 协议 `stocks()` | ✅ 沪深 | **已改为主路径**；北交所仍需东财 |
| 市场宽度（880 系列） | vipdoc 本地 | ✅ | **本地** |
| 财务数据 | mootdx Affair | ✅ | **本地** |
| 概念/主题标签 | TQ download_file | ✅ | **本地** |
| TQ 选股公式 | TQ-Local | ✅ | **本地** |
| 实时行情 | mootdx Quotes | ✅ | **TDX 协议** |
| **北交所行情/名称** | 东财 push2 | ❌ | **无法改**（见下） |
| 当期财务（live 选股） | mootdx Affair（585 列） | ✅ | **本地**（早就是） |
| **PIT 财务（带公告日）** | 东财 datacenter | ❌ 无 notice_date | **保留**（结论见下） |
| **资金流向** | 东财 push2 | ❌ 口径无法对齐 | **保留**（结论见下） |
| **海外指数 / A50 / 汇率** | Yahoo Finance | ⚠️ 部分 | **Yahoo 主 + TDX ext 降级** |
| 在线行情兜底 | 新浪 / 腾讯 | — | 仅 fallback |
| 新闻 RSS | 各家 RSS | ❌ | 无替代 |

## 北交所为什么改不了

TDX 服务器只提供沪深：

- `Quotes.stocks()` 硬校验 `if market not in [0, 1]`
- 绕过校验直接调 `client.get_security_list(market=2)` —— 实测**空返回**
  （尽管 `stock_count(market=2)` 返回 367）
- 已取到的沪深列表里没有 `92`/`83`/`87` 段（`88` 段是 880xxx 板块指数）

所以北交所（`920xxx` / `83xxxx` / `87xxxx`）的名称与行情仍须东财补。
**这是唯一确认无法本地化的部分**，占全市场约 1%。

## 剩余三项的评估

**PIT 财务（带公告日）** —— mootdx `Affair` 提供本地财务文件，但 PIT 的价值在于
`notice_date`（公告日，用于避免未来函数）。需确认 TDX 财务文件是否含公告日；
若只有报告期，则无法替代东财 `RPT_LICO_FN_CPD`。**待查**。

**资金流向** —— 东财的「主力净流入」是它自己按逐笔成交分类算的。TDX 有
`Quotes.transaction()`（逐笔成交），理论上可自算，但**口径无法与东财对齐**
（大单阈值、主动买卖判定都是东财的私有规则）。改造成本高、结果不可比，
优先级低。

**A50 / 汇率** —— mootdx 有 `Quotes.factory(market='ext')`（扩展市场：期货/外盘），
可能覆盖 A50 与汇率。**待查**。

## 新增数据源时的检查顺序

1. vipdoc 本地文件有没有？
2. TDX 协议（`Quotes.*` / `client.*`）有没有？**注意 mootdx 的校验层可能比协议层更严**
   （`stocks()` 拒绝 `market=2` 但 `stock_count()` 不拒绝），必要时可直接调 `client`
3. TQ-Local（需 TdxW.exe）有没有？
4. 以上都没有，才考虑 HTTP；且必须：多域名轮询 + 缓存 + 失败留痕（不静默降级）

## 连接管理要求

所有走 TDX 协议的调用**必须**经 `local_tdx_data._with_client_retry()`：

- 连接有时效上限（`CLIENT_MAX_AGE_SEC = 600`），过期主动重建
- 失败时**重建连接**再试（用同一个死连接重试没有意义——原实现的问题正在这里）
- 长跑进程（18:00 选股链跑几百只票）尤其需要，连接可能在中途失效


---

## 三项剩余依赖的查证结论（2026-08-04）

### ① PIT 财务：保留东财，这是正确的架构而非妥协

TDX 财务文件（`Affair`）实测：`gpcw20260331.zip` 解析出 **5532 只 × 585 个字段**，
比东财业绩报表丰富得多。但**唯一的日期字段是 `report_date`（报告期），没有公告日**。

不能用「文件发布时间」代替公告日：一个文件含全市场，而公告日从 4 月底跨到 8 月底。
用统一时间会让晚公告的公司获得**未来函数**（4 月就"看见"了 8 月才公告的财务），
这比信息损失严重得多。

而现有分工其实已经是最优的：

| 用途 | 数据源 | 是否需要 PIT |
|---|---|---|
| live 选股（`financials.py`） | **mootdx Affair 本地** | 不需要——当前时点用最新财务，本就是"当时可见的" |
| 回测（`scan_signals_ytd` 等） | 东财 PIT | **需要**——否则用上了未来才公告的财务 |

且东财只在**构建 `pit_financials.jsonl` 台账**时调用，回测运行时读本地文件。
**HTTP 不在关键路径上。** 结论：不改。

### ② 资金流向：不建议改

东财的「主力净流入」是它按逐笔成交自行分类算出来的（大单阈值、主动买卖判定都是
私有规则）。TDX 有 `Quotes.transaction()` 逐笔成交，理论上可自算，但**口径无法对齐**
——自算的结果与历史积累的东财数据不可比，等于换了个指标而不是换了个数据源。
成本高、破坏历史可比性。结论：不改。

### ③ 海外行情：Yahoo 主路径 + TDX ext 降级

`Quotes.factory(market='ext')` 实测可用，**82733 个品种**，能取到真实 K 线
（NVDA 2026-08-03 收 206.64）。mootdx 会打警告「目前扩展市场行情接口已经失效」，
**该警告是过时的**，数据正常返回。

覆盖情况：

| 品种 | ext 是否有 | 说明 |
|---|---|---|
| NVDA / AMD / TSM | ✅ | `market=74` 美股，17146 个品种，口径一致 |
| 港股（恒生科技 ETF） | ✅ | `market=71`；Yahoo 那边本来也是 ETF 代理 |
| 道指 / 纳指 / 标普 / 费半 | ⚠️ 只有 ETF | DIA / QQQ / SPY / SOXX **代理**，非指数本身 |
| 日经 / KOSPI / 三星 / SK海力士 | ❌ | 只有跟踪 ETF，无指数与个股 |
| **A50 期货** | ❌ | 新交所品种，通达信国内版不含 |
| **USDCNH 汇率** | ❌ | 只有美股的汇率 ETF（FXA/FXC/FXF/FXY） |

既然无法完整替代，**混用两个源会让口径不一致**，所以定位为「Yahoo 失败时的降级」：

- `market_timing/tdx_ext_quotes.py`，8 个品种可降级
- 用了代理必须留痕：`details[key]["degraded"]=True`、`proxy`/`proxy_note`、
  顶层 `overseas_market["fallback_source"]`，`source` 也改成
  `"Yahoo Finance chart API + TDX ext fallback"`
- ETF 代理与指数的口径差（跟踪误差、溢价折价、交易时段不同）已写进模块 docstring；
  代理的 `last_close` 是 **ETF 价格**（如 SPY 759.18）而非指数点位，只有
  `change_pct` 可用于方向性参考

海外数据在门控评分里权重最低（10%），所以降级后精度略降是可接受的；
但**不留痕是不可接受的**——那就成了「降级了没人知道」。

---

## 代码规范：同一文件不得被加载成两个模块

`07_tools/` 下的模块既可能被当脚本跑（`python 07_tools/xx/yy.py`），也可能被当包模块
导入（`from xx import yy`）。若两处用不同导入路径引用同一个文件，Python 会把它加载成
**两个独立模块**，后果是：

- 自定义异常成了两个不同的类 ⇒ `except` **静默失效**、异常穿透上抛
- `monkeypatch` 打在一个上，运行时用的是另一个 ⇒ 测试以为打了桩，实际在打真实网络

本次实际踩到两次（`fetch_market_cap` → `adjust_factors`、
`overseas_market_collector` → `tdx_ext_quotes`），第二次是靠测试日志里冒出
mootdx 的网络 WARNING 才发现的。

统一写法：

```python
try:
    from .sibling_module import thing          # 包内导入优先
except ImportError:                            # 脚本模式回退
    from sibling_module import thing
```
