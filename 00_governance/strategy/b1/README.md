# B1 波段策略（主）

> **角色**：primary　｜　**状态**：✅ 现行　｜　注册表：[`../STRATEGY_REGISTRY.json`](../STRATEGY_REGISTRY.json)

## 定位

应用于**上升趋势（右侧）**的波段策略。完整模型六层：宏观与市场择时 → 主线与个股筛选
→ KDJ 的 J 值触发 → 结构止损 → BBI 上方分批止盈 → BBI预警/N型清仓。

**任何单一条件都不等于完整 B1 买点**，尤其 `J < 13` 只代表进入观察区。

核心认知（来自研究链）：**收益来自「择时 + 交易管理 + 分散」，不来自「选哪只股」。**
价值排序 **0AMV ≫ 板块相位 > 选股（0/负）**。
⚠️ 量级数字待重跑，见 [`../../research/README.md`](../../research/README.md)「重跑清单」。

## 规则文件（按阅读顺序）

| 文件 | 执行者 | 状态 | 内容 |
|---|---|---|---|
| [01_swing_rules.md](01_swing_rules.md) | **代码** + 人 | ✅ | 主规则：J 触发 / 反转K / N 结构 / 止损 / BBI 止盈 |
| [02_holding_check.md](02_holding_check.md) | 人 | ⚠️ §七 空条款 | 每日持股检查：噪声过滤 / 收盘结构 / 持有条件 |
| [03_execution_discipline.md](03_execution_discipline.md) | 人 | ✅ | 执行纪律：卖出时间窗 / 等信号 / 浮盈转亏保护 |
| [04_pullback_rotation.md](04_pullback_rotation.md) | 人（设计上）| 🔴 **零实现** | 大盘回调后的资金切换与四类分层 |
| [05_pit_recovery.md](05_pit_recovery.md) | 人 | ✅ | 补坑：大坑 / 小坑 / 小洞 |
| [90_research_summary.md](90_research_summary.md) | —（摘要）| ⚠️ 量级待重跑 | 一轮回测的执行摘要 |

## 代码依赖

| 代码 | 用了什么 |
|---|---|
| `07_tools/screening/enrich_candidates.py` | 反转K 六项阈值、B1 模式识别 |
| `07_tools/market_timing/b1_holding_state.py` | 持仓状态机（`B1-holding-v1` 契约）|
| `07_tools/research/backtest_factors.py` | 研究用回测器 |

⚠️ **只有 `01_swing_rules.md` 是「代码执行」的** —— 改它的阈值必须同步改代码常量并跑测试。
反转K 六项已于 2026-08-06 逐项核查一致（见 [`../README.md`](../README.md)）。

## 与其他策略的关系

- **CZ（辅）**：为 B1 提供**标的池与阶段判断输入**（板块偏好、真假科技分化）。
  ⚠️ CZ 的止损数字（15%/20%）是长期持有语境的绝对上限，**不是 B1 的执行止损**，
  层级关系待拍板 —— 见 [`../README.md`](../README.md) 问题②。
- **通用因子**：[`../_factors/`](../_factors/README.md) 目前未接线。

## 已知问题

1. 🔴 `04_pullback_rotation.md` 零实现却被 `02_holding_check.md` §七 依赖（待办 #26）
2. ⚠️ `90_research_summary.md` 的收益量级数字待重跑
3. ⚠️ 止损层级未写定（与 CZ）
