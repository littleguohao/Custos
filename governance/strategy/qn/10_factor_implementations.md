# 10 QN 因子实现对照表

> **上下文**：qn（骑牛登山体系，辅）｜ **执行者**：代码（研究侧回测消费）｜ **状态**：⚠️ debug（未回测，不得进 live）
> **版本**：v0.194 ｜ **代码依赖**：`core/factors/qn_ma25_state.py`、`core/factors/qn_volume_surge_cut.py`、`core/factors/qn_three_red.py`、`core/factors/qn_macd_bar_shift.py`、`core/factors/qn_kdj_neg_day.py`、`core/factors/qn_adx_extreme.py`、`core/factors/qn_box_target.py`、`core/factors/qn_ma144_launch.py`、`research/backtest_factors.py`（QN gate 区段）
> **索引**：[README.md](README.md) · 改动须记 CHANGELOG.md

第一批因子化成果：9 维度文档中**日线 OHLCV 可确定性计算**的 8 条规则。
全部 `status=untested / live_use=none / stage=debug`——**不进 live 链、不驱动任何决策**；
盈利验证走 [`../../research/R31_qn_factor_validation.md`](../../research/R31_qn_factor_validation.md)
预注册流程，跑数前不得宣称有效。

## 因子 ↔ 规则 ↔ 实现对照

| factor id | 名称 | 规则出处 | kind | 研究 gate | 核心判定 |
|---|---|---|---|---|---|
| `qn_ma25_state` | MA25 多空分界 | [01 §四](01_general.md)、[08](08_main_wave_launch.md) | state | 线上缩量阴线 | 收盘 vs MA25 × 阴阳 × 缩量；MACD 柱红绿辅助 |
| `qn_volume_surge_cut` | 倍量切起爆K线 | [08](08_main_wave_launch.md) | pattern | 同 hit | 阳线量≥昨日×2，收盘自 MA5/MA10 下穿上；鬼招手发散排除腿 |
| `qn_three_red` | 三线红 | [01 §二](01_general.md)、[08](08_main_wave_launch.md) | state | 三线全红当日 | 日/周（W-FRI）/月（ME）MACD 柱全红；月绿柱标记 |
| `qn_macd_bar_shift` | 买小绿/卖小红 | [01 §八](01_general.md) | pattern | 买小绿 | 绿柱连缩 3 根 + 收盘不破前低；卖小红=出场侧记录 |
| `qn_kdj_neg_day` | J 负值计数 | [05](05_top_bottom_kdj.md) | pattern | 第 3/5 天或 KD20 金叉 | 死叉 J>50 + 下跌顺滑 + J<0 计数；⚠️ 与 B1 反转K J<13 不同口径 |
| `qn_adx_extreme` | ADX≥60 极端位 | [04 §三](04_tape_volume_auction.md) | state | 极端位+底背离 | Wilder DMI（L0 唯一实现）；方向腿=MACD 顶/底背离（bottom_patterns 同构分型） |
| `qn_box_target` | 1.3 系数箱体 | [02 §一](02_space_targets.md) | state | ⚠️ 研究约定转译（站上半格×1.15 且未进目标区） | 波段低点×1.3/×1.26/×1.15 三档空间度量，不判买卖 |
| `qn_ma144_launch` | 日线翻倍四要素 | [07 §二](07_doubling_swing.md) | pattern | 四要素全中 | MA144 走平上翘 + 回踩 ±10% + MACD 双线上零轴 + 过左风四形式之一 |

## 实现约定（整批统一）

- 检测器**绝不 raise**：短数据/异常 → `available=False` + 原因；腿级明细全落盘供回测消融
- 常量集中各模块顶部 `QN_*` 前缀，逐个标「待回测」；指标一律用 L0 唯一实现
  （`indicators.macd_series / kdj_series / dmi_arrays / resample`），禁止自写 EMA
- 钉测 `tests/test_qn_factors.py`（合成用例：正例/消融/短数据/垃圾输入 + gate 注册与转译口径）
- gate 全部接受并忽略 `precomputed`（黑盒 detector，不进 `_SLICE_FREE_GATES`）

## 明确未因子化（原因记录，第二批候选）

- **盘中/竞价类**（两点半、9:33、集合竞价、盘口大单）：需分时/Level-2 数据
- **截面类**（妖股龙一、涨停家数排序）：非单票 OHLCV，属选股链层
- **无数据源类**（大宗交易、解禁、F10 股东）：不得以代理冒充（同情绪维度缺口原则）
- **主观画线类**（斜横线、上下影线箱体、资金箱体）：画线规则主观成分大
- **180 周线大悬空**（[07 §一](07_doubling_swing.md)）：需 ~4 年数据，覆盖不足
- **打板族**（缩量板/一字板次日行为）：依赖次日开盘行为，属入场时机
