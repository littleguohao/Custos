# 跨策略规则

> **非策略上下文**（故目录带 `_` 前缀）。这里放**不属于任何单一策略**的规则 ——
> 典型是策略/信号之间如何裁决。
> 注册表：[`../STRATEGY_REGISTRY.json`](../STRATEGY_REGISTRY.json) 的 `shared_rules` 段。

| 规则 | 文件 | 状态 | 以什么为准 |
|---|---|---|---|
| 决策优先级 | [decision_priority.md](decision_priority.md) | ⚠️ 部分过时 | [`../../contracts/MASTER_WORKFLOW.md`](../../contracts/MASTER_WORKFLOW.md) 与仓库 `README.md`「决策优先级」 |
| 系统原则与用户画像 | [system_principles.md](system_principles.md) | ✅ live | 核心原则第 0 条（核心思想）为 owner 定案 |
| 因子·止损·止盈总览 | [factor_exit_catalog.md](factor_exit_catalog.md) | ✅ live（2026-08-20 首版） | `core/factors/` 注册表 + `core/exit_rules.py` + `contracts/EXIT_RULES.json`（字段级真相以代码为准） |

⚠️ `decision_priority.md` 写于 Agent 架构时代（「不同 Agent 结论冲突」等口径已不适用），
**优先级原则本身仍有效**。多策略并行后这一层会更重要（哪个策略的信号优先、冲突如何裁决），
届时应重写而不是继续打补丁。
