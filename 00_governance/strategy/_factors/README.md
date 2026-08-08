# 跨策略可复用因子

> **非策略上下文**（故目录带 `_` 前缀）。因子是**判别维度**，不含完整进出场规则，
> 可被多个策略引用。注册表：[`../STRATEGY_REGISTRY.json`](../STRATEGY_REGISTRY.json) 的 `factors` 段。

| 因子 | 文件 | 状态 | 被谁用 |
|---|---|---|---|
| 通用技术走势框架 | [technical_trend.md](technical_trend.md) | ⚠️ **未接线** | 无 |

## 新增因子

放一份 `lower_snake.md`（因子无阅读顺序，故**不编号**），在注册表 `factors` 段登记
`used_by`。若某因子被验证后要进 live，应由主策略的规则文档**转译**并回测，
不要让代码直接读因子文档。
