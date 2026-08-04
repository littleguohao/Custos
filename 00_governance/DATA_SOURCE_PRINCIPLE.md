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
| **PIT 财务（带公告日）** | 东财 datacenter | ⚠️ 待查 | 未改 |
| **资金流向** | 东财 push2 | ⚠️ 口径难对齐 | 未改 |
| **A50 / 汇率** | Yahoo Finance | ⚠️ 待查 ext 市场 | 未改 |
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
