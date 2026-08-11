# R6 · 假设 H1：B1 双轴（长期结构 × 短期回调）

> **家族**：假设　|　**证据等级**：L3　|　**状态**：❌ 否决（weekly_j_low 弱 edge 维持现状）
> **依赖**：上游：R3(瓶颈在召回)｜同批终审：R7｜判据：R12
> 索引与主图见 [`README.md`](README.md)。证据等级定义同上。原始日志见 git 历史 `B1_BACKTEST_FINDINGS.md`。

## 主题

长期结构 × 短期回调的双轴能否提升 B1 选股

## 目标

过三重门槛 + 净值终审

## 结论

**未通过跨窗终审。**

| 口径 | 跨 seed | 跨区间 2022-2024 | 裁决 |
|---|---|---|---|
| `j_low_qsx_weekly` | ✅ 方向一致 | ❌ H20 43.6%，**2024 H20 31.1% 灾难** | ❌ 否决 |
| 共振加分（b1_dual vs no_res） | 无增益 | — | ❌ 否决 |
| `weekly_j_low` | — | ✅ 三 horizon 均赢基准 | ⚠️ **弱 edge 成立** |

`j_low_qsx_weekly` 净值上只多 **0.019R/笔**，代价是 **90% 召回** ⇒ 负交换。

唯一留下的 `weekly_j_low`：pooled 一致为正但**幅度小、分年有晃动**（2023 H60 43.8% 不达标）⇒ **作为既有基础门槛维持现状，不支持再向上加码任何「精选层」。**

首轮（100 只）两组都**样本量不足无统计效力**，且我**看错了档位**——那一轮唯一可信的结论是「召回代价」。

---

## 证据与过程

## 待验证假设 H1：B1 双轴（长期结构 × 短期回调）—— 2026-08-03 提出

**这是假设，不是结论。** 已有两轮判读：首轮（100 只，样本不足，见下）；
第二轮（1000 只随机抽样 + 无条件基准，见文末「H1 第二轮回测判读」）——
`j_low_qsx_weekly` 首轮通过待跨窗验证，日周共振方向不一致暂否。
未通过终审前不得据此改选股链。

### 依据：`other/good_b1.pptx` 九例形态统计（末根读数）

| 特征 | 命中 | 说明 |
|---|---|---|
| `QSX > DKS`（知行短期趋势线 > 多空线） | 8/9 | 图上参数 (14,28,57,114)，与 `zhixing_state` 同口径 |
| `J ≤ 13` | 8/9 | 多个为负（-4.78 / -10.85 / -13.42），J 越负越典型 |
| `DIF > 0` 且 `MACD 柱 < 0` | 7/9 | 中期趋势未破零轴 + 正在回调 |
| 曾有放量启动段 | 9/9（图形） | 单日 ≥5% + 量 ≥1.5×MA20 |
| 回调段缩量 / 回踩贴 QSX·DKS | 多数 | MA5<MA10；回调终点在趋势线附近 |

唯一异类 image6（野马电池，J=100.97、QSX<DKS）经 owner 确认：**买点在图上竖线处（J<13）**，
右上角数值是末根 K 线的，不代表买点状态。

**副产品：已上线的 `j_low_dif_pos` gate 得到独立验证。** 它当初依据"半程一致性"采纳，
而该指标被审计 E1 证明失效（按股票顺序切分而非日期）；现在 good_b1 的 7/9 给了它形态层面
的支持，不必再标为待重验。

### 假设内容

技术轴从"单轴 s_shape（买强/突破）"改为**双轴软加权**：

```
轴1 长期结构 0-100（底子好）= QSX>DKS 30 + 均线多头 20 + 上方套牢少 15
                              + 量能中枢上移 15 + 曾放量启动 20
轴2 短期回调 0-100（买点到）= compute_s_reversal（超跌 40 + 缩量企稳 30 + 反转确认 30）
双轴 = 0.40×轴1 + 0.60×轴2      # B1 是回调买入，买点轴权重更高
```

owner 裁定三点：① B1 是**单纯回调买入**，不吃突破 ⇒ s_shape 的 `pivot`/`pocket_pivot`/
`compression` 三项（占 50 分）**移出**技术轴；② 轴1 **软加权**不做硬门槛（熊市里
`QSX>DKS` 会大面积不满足，硬门槛使候选枯竭）；③ 出货五式作否决层，不混进打分。

### 移出突破式分项的量化依据（合成四形态实测）

| 形态 | J | 轴1 | 轴2 | 双轴 | 旧 S** |
|---|---|---|---|---|---|
| D 突破回踩型（买点） | -14.4 | 80.5 | 52.0 | **63.4** ① | 24.5 ③ |
| A good_b1 型（买点） | -8.2 | 58.1 | 42.0 | **48.4** ② | 14.7 ④ |
| C 突破未回调（好票非买点） | 93.0 | 100.0 | 5.0 | **43.0** ③ | **56.4** ① |
| B 长期无量阴跌（差票） | 14.9 | 15.0 | 24.0 | **20.4** ④ | 12.0 ② |

双轴排序 D>A>C>B 正确；**旧 S\*\* 排序 C>B>D>A 方向相反**——它把"突破未回调"排第一、
good_b1 型排倒数第二，因为那三个突破式分项奖励的正是突破而非回调。

### 突破回踩型 B1（owner 提出，与结论#15 的边界）

> **2026-08-11 首轮（TODO #12）**：`breakout_pullback_b1` gate（s3000 钉死宇宙，20 日持有）
> 主窗 +2.105%（赢无条件基准 +1.313%）但跨窗 +0.079%（≈归零，基准 +1.316%）⇒ **不过跨窗，
> 维持否决**，与结论#15 的边界判定一致。召回代价提示依旧有效（n=11.5k/16.3k，仅 j_low 的 4%）。

「突破前高后回调到 B1 区间且股价不低于前高」= `platform_pullback ∩ J<13`。

结论#15 否决的是平台突破回踩**作独立入场**（净值 3 窗方向随环境摆动），并明确"证据层保留"；
它测过叠加板块/基本面优/0AMV 各腿，**没测过叠加 J<13**。故这是 #15 留下的未测组合，
作为**标记/子集对比**而非独立入场，与该结论不冲突。

⚠️ **待确认口径**：`platform_high` 由 `platform_pullback` 按**最高价**摆动高点算出，严格
口径 `close >= platform_high` 要求收盘超过历史最高价，回踩场景下几乎不可达（合成用例：
平台高 10.465 vs 回踩收盘 10.393）。现默认 `ph_tol=0.98`，复用 platform_pullback 自身的
"收盘守在平台高 ≥×0.98"判定；返回里同时给出 `close_ge_ph_strict` 供回测对比两种取法。

### 加分项 H1c：日周线 B1 共振（owner 2026-08-03 提出）

「日线 B1 的同时周线也 B1（J<13）」。周线 J<13 意味着**更大周期的回调也到位**——日线可能
只是短暂杀跌，周线同时超卖才说明整段回调走完。

现状：`weekly_j_state` 早已算出 `weekly_j` / `weekly_j_low` 并落盘到候选顶层，但
`score_candidates:620-621` **只落盘、未参与打分**。本次把它做成加分项（`RESONANCE_BONUS_PTS=12`，
待回测），并在返回里给 `score_without_resonance` 便于消融。

⚠️ **发现一个内在张力，回测必须专门验证**：周线 J<13 需要约 **9 周**连续下跌（周线 KDJ 的
RSV 窗口），而那么长的回调会**破坏 QSX>DKS**。合成用例实测：

| 用例 | 日线 J | 周线 J | 共振 | QSX>DKS | 双轴（含加分） | 无加分 |
|---|---|---|---|---|---|---|
| 仅日线 B1（急跌 4 根） | -14.6 | 86.0 | 否 | **是** | 60.3 | 60.3 |
| 日+周共振（9 周缓跌+末段加速） | 5.5 | -16.1 | **是** | **否** | 38.4 | 26.4 |

共振加分 +12 **补不回轴1 的下降**（38.4 < 60.3）。所以"共振一定更好"不能假定——
可能的解释是两者刻画的是不同阶段：日线急跌+结构完好 = 强势股的短暂回踩；日周共振 =
深度调整末期。哪个胜率高须由真实数据回答。已由
`tests/test_b1_dual_factor.py::TestWeeklyResonance::test_long_pullback_tension_is_real` 钉住该现象。

### 实现与验证状态

> 注：下文因子路径 `screening/b1_dual_factor.py` 已迁至 `07_tools/factors/`。

- 因子：`screening/b1_dual_factor.py`（`compute_b1_dual` / `compute_long_structure` /
  `detect_launch_segment` / `detect_breakout_pullback_b1` / `detect_weekly_b1_resonance`）
- 回测入口：`SCORERS["b1_dual"]`、`["b1_dual_no_res"]`、`["long_structure"]`（后两个消融用）；
  `ENTRY_GATES["qsx_gt_dks"]`、`["j_low_qsx_gt_dks"]`、`["breakout_pullback_b1"]`、
  `["weekly_j_low"]`、`["j_low_weekly_resonance"]`、`["j_low_qsx_weekly"]`
- **未接入选股链**（`score_candidates` 一行未动），由 `tests/test_b1_dual_factor.py::
  TestNotYetWiredIntoScreening` 钉住
- 合成数据只验证了判别方向；真实回测于 2026-08-03 在 vipdoc 环境完成
  （见文末「H1 第二轮回测判读」）

### 待跑的验证（三重门槛 + 净值终审，同结论#15 的标准）

```bash
# ① 分档 × horizon 网格：双轴 vs 现状 s_shape vs 消融
uv run python 07_tools/research/backtest_factors.py --scorer b1_dual   --entry-filter j_low --horizons 5,20,60
uv run python 07_tools/research/backtest_factors.py --scorer s_shape   --entry-filter j_low --horizons 5,20,60
uv run python 07_tools/research/backtest_factors.py --scorer long_structure --entry-filter j_low --horizons 5,20,60
uv run python 07_tools/research/backtest_factors.py --scorer s_reversal --entry-filter j_low --horizons 5,20,60

# ② 门槛对比：J<13 单独 vs 叠加 QSX>DKS vs 突破回踩型
uv run python 07_tools/research/backtest_factors.py --entry-filter j_low
uv run python 07_tools/research/backtest_factors.py --entry-filter j_low_qsx_gt_dks
uv run python 07_tools/research/backtest_factors.py --entry-filter breakout_pullback_b1

# ③ 日周共振（H1c）：共振加分是否有增益 / 共振与结构完好哪个胜率高
uv run python 07_tools/research/backtest_factors.py --scorer b1_dual        --entry-filter j_low --horizons 5,20,60
uv run python 07_tools/research/backtest_factors.py --scorer b1_dual_no_res --entry-filter j_low --horizons 5,20,60
uv run python 07_tools/research/backtest_factors.py --entry-filter weekly_j_low
uv run python 07_tools/research/backtest_factors.py --entry-filter j_low_weekly_resonance
uv run python 07_tools/research/backtest_factors.py --entry-filter j_low_qsx_weekly   # 最严一档，看召回代价

# ④ 净值终审（跨窗，必须赢过无条件基准）
uv run python 07_tools/research/backtest_factors.py --trade-sim --scorer b1_dual --entry-filter j_low_qsx_gt_dks --cost-bps 25
```

**判定标准（沿用结论#15 的教训）**：先问"是否赢过无条件基准"，且必须跨窗方向一致；
富集类比较必须同窗口同口径。任一不过 → 记否决结论、不接线。


---

## H1 第一轮回测判读（100 只样本，2026-08-03）—— **两组都不足以下结论**

### ① 共振消融：样本量不足，无统计效力

| 口径 | 分档 | H5 胜率 | H20 胜率 | H60 胜率 | H5 均收 | H20 均收 | H60 均收 |
|---|---|---|---|---|---|---|---|
| b1_dual（有共振） | A(≥70) | 47.6% | 51.6% | 50.9% | +0.86% | +4.95% | +6.00% |
| | B(60-70) | 53.4% | 54.1% | 53.4% | +0.70% | +4.18% | +10.55% |
| b1_dual_no_res | A(≥70) | 42.9% | 41.2% | 33.3% | +2.33% | +2.53% | +2.29% |
| | B(60-70) | 48.9% | 52.0% | 48.1% | +0.06% | +3.47% | +8.51% |

**A 档仅 7 条 ⇒ 最小可分辨差异 = 1/7 = 14.3pp，而实测 H20 胜率差只有 10.4pp
——连一条样本的翻转都不到，完全在噪声内。** 不能据此说共振有增益。

数据一致性存疑：47.6%/51.6%/50.9% 无法由 7 条样本得出（7 条只能给 0%、14.3%、28.6%、
42.9%…），只有无共振组 42.9%=3/7 对得上 ⇒ 各 horizon 实际分母不同（H60 因前向窗口删失
更少，见审计 E2 的修复）。**重跑需输出每档每 horizon 的真实 n。**

留意一个反向信号：有共振组 H5 均收 +0.86% < 无共振组 +2.33%，而 H20/H60 相反。小样本里
通常是噪声；若扩样本后仍存在，说明共振买点需要更长持有期。

### ② 门槛对比：**看错了档位**，但意外印证了审计 B4

| 门槛 | n | D_弱档 H10 胜率 | SE | 召回 |
|---|---|---|---|---|
| weekly_j_low | 9,167 | 51.8% | 0.52pp | 100% |
| j_low_weekly_resonance | 3,448 | 49.8% | 0.85pp | 38% |
| j_low_qsx_weekly | 877 | 54.5% | 1.68pp | **10%** |

最严门槛 vs 周线单条件差 +2.7pp，**z=1.53、p≈0.13，95% 水平不显著**（要显著需每组约
2,635 条）。

更根本的问题：**三行看的都是 `D_弱(<40)` 档**，而 D 是"不买"档，用它比较入场门槛没有
意义。原因是回测用了默认 scorer（s_shape），而**s_shape 打分下 A/B 档结构性近空**
（实测"B 档仅 1 条"）——这正是审计 B4 从代码推断的结论被数据证实。

⇒ ② 必须加 `--scorer b1_dual` 重跑，否则 A/B 档永远没样本。

### 唯一可信的结论：召回代价

`100% → 38% → 10%` 不受统计效力影响。三条件全满足只剩 10% 召回，与结论#15 的实证呼应
（「瓶颈在召回，不在排序」，入场门槛对大牛股是负选择）。10% 召回需要胜率有很大提升才划算。

### 重跑清单

```bash
# 换 scorer（否则 A/B 档无样本）+ 扩样本 + 随机抽样（避免"前 100 个代码"的选择偏差）
uv run python 07_tools/research/backtest_factors.py --scorer b1_dual --entry-filter weekly_j_low            --universe-local --universe-sample 1000
uv run python 07_tools/research/backtest_factors.py --scorer b1_dual --entry-filter j_low_weekly_resonance  --universe-local --universe-sample 1000
uv run python 07_tools/research/backtest_factors.py --scorer b1_dual --entry-filter j_low_qsx_weekly        --universe-local --universe-sample 1000
```

---

## H1 第二轮回测判读（1000 只随机抽样 seed=0 + b1_dual + 无条件基准，2026-08-03）

**无条件基准**（`--entry-filter none`，同批 1000 只，422,097 条）：H5 49.43% / H10 50.27% /
H20 50.08% / H60 50.14%；均收 +0.41 / +0.74 / +1.43 / +4.82%。以下所有 z 值均为对基准的
两比例 z 检验；显著性门槛 |z|>1.96。

数据：`06_logs/backtest_baseline_none_1000.json`、`backtest_h1_weekly_j_low_b1dual_1000.json`、
`backtest_h1_resonance_b1dual_1000.json`、`backtest_h1_qsx_weekly_b1dual_1000.json`、
`backtest_h1_resonance_nores_1000.json`（均带 `--horizons 5,10,20,60`）。

### ① 门槛链条（同 scorer b1_dual，三层严格嵌套 ③⊂②⊂①，全体胜率）

| gate | n（召回 vs ①） | H5 | H20 | H60 |
|---|---:|---|---|---|
| ① weekly_j_low | 90,546 (100%) | 51.0% (z=+8.6) | 55.0% (z=+25.5) | 50.9% (z=+3.7) |
| ② j_low_weekly_resonance | 32,531 (36%) | 51.4% | 51.9% | 53.1% |
| ③ j_low_qsx_weekly | 9,001 (10%) | 54.0% | 58.3% | 57.1% |

两两比较（同口径嵌套子集）：
- ①→②（加日周共振）：H20 **-3.1pp（z=9.2，显著变差）**、H60 +2.2pp（z=5.6，显著变好）、
  H5 不变（z=-1.3）。**方向不一致——首轮 H1c 推测的"共振与长期结构的内在张力"被大样本
  证实：共振买点需要更长持有期。**
- ②→③（加 QSX 周线）：H5 +2.6pp（z=4.2）、H20 +6.4pp（z=10.4）、H60 +4.0pp（z=5.9），
  **三个 horizon 一致显著变好**，但召回只剩 10%。
- 意外发现：weekly_j_low 单条件（①）H20 即 55.0%（z=25.5）——周线 J 低位本身是强 edge，
  首轮 100 只时完全被样本量掩盖。

### ② 共振加分消融（同 gate ②，b1_dual vs b1_dual_no_res，各 32,531 条）

- A 档胜率两版几乎相同（H20 55.1% vs 58.3%，z=-0.4；无加分版 A 档仅 51 条）；
  **共振"加分"没有在 A 档之上产生额外区分度**（A 档原本就大多是共振信号）。
- 但因加分才进 A 档的 1,482 条信号：H5 52.3% / H20 55.0% / H60 55.6%——
  **显著赢无条件基准（H20 z≈3.5）**，即共振作为**条件**有效，作为**加分**只是把它
  显性化，没有提成假象。

### ③ b1_dual 的排序能力（③ 组内，n=9,001）

C 档 H20 59.3% / B 档 58.3% / A 档 55.9%（D 档仅 72 条垫底 H60 47.1%）——顶部 C/B/A
倒挂但全部远高于基准；**scorer 能把最差的 D 档分出来，但 A/B/C 之间的排序无效**。
与结论#15 一致：瓶颈在召回/门槛，不在排序。

### 判定（按"三重门槛"标准）

- **赢无条件基准**：③ 全 horizon 一致显著（最小 z=4.2）✅；② 方向不一致 ❌（H20 显著变差）；
  ① H20 强但 H60 仅 z=3.7、H5 z=8.6，方向一致但 H60 增益弱。
- **跨窗方向一致**：本轮只有 seed=0 单区间，**无法判定**。
- ⇒ **j_low_qsx_weekly（③）首轮通过，待跨窗/跨 seed 验证**；日周共振（②）暂否，
  其增益只在 H60 成立，若用须明确"共振买点持有期 ≥60 日"。
- 召回代价重申：③ 只剩 10% 召回，与结论#15 实证第 1 条一致——即使验证通过，
  定位也是"高胜率低召回的精选档"，不是主力召回口径。

> **终审更新（2026-08-03 深夜）**：③ 跨 seed 通过、**跨区间失败（2024 年 H20 31.1%）**，
> 正式否决。详见文末「H1/H2 终审（跨 seed/跨区间/净值）」。

### 下一轮验证清单（已于 2026-08-03 执行，结果见文末终审）

```bash
# 跨 seed + 跨区间（方向一致性终审）：③ 与 ① 对照
uv run python 07_tools/research/backtest_factors.py --scorer b1_dual --entry-filter j_low_qsx_weekly --universe-local --universe-sample 1000 --seed 1 --horizons 5,10,20,60
# ⚠️ 跨区间必须加大 --count:count 默认 500 根从今天往前数,加 --start/--end 只覆盖
#    窗口尾部(实测只剩 2024H2);--count 1500 才能覆盖 2021 年初的预热段
uv run python 07_tools/research/backtest_factors.py --scorer b1_dual --entry-filter j_low_qsx_weekly --universe-local --universe-sample 1000 --start 2022-01-01 --end 2024-12-31 --count 1500 --horizons 5,10,20,60
# 净值终审（跨窗必须赢无条件基准）
uv run python 07_tools/research/backtest_factors.py --trade-sim --scorer b1_dual --entry-filter j_low_qsx_weekly --cost-bps 25 --universe-local --universe-sample 1000
```

---

## H1/H2 终审（跨 seed / 跨区间 / 净值，2026-08-03 深夜）

配套基准：seed=1 无条件（428,718 条：H5 49.40 / H10 50.17 / H20 49.97 / H60 50.10，
与 seed=0 基准几乎一致，基准本身跨 seed 稳定）；2022-2024 无条件（592,882 条，
`--count 1500` 修正窗口后：H5 46.54 / H20 46.95 / H60 45.27——熊市区间基准偏弱）。
数据：`06_logs/backtest_*_{seed1,2022_2024,tradesim}*.json`。

### ③ j_low_qsx_weekly：跨 seed 过、跨区间挂 ⇒ **否决（区间不稳定）**

| 检验 | 结果 |
|---|---|
| 跨 seed（seed=1，9,366 条） | ✅ H5 54.4%(z=9.6) / H20 59.4%(z=17.6) / H60 56.8%(z=11.8)，与 seed=0 方向一致、召回稳定 ~10% |
| 跨区间（2022-2024，9,762 条） | ❌ 全体 H5 43.9%(z=-5.0) / H20 43.6%(z=-6.2) / H60 43.8%(z=-2.6)，**在偏弱基准之下还显著跑输** |
| 分年 | 2022 H20 50.2/H60 54.4（赢）；2023 47.0/42.8（混合）；**2024 H20 31.1/H60 34.8（灾难）** |
| 净值（trade-sim，含 25bps） | ③ +0.352R/笔 vs 母门槛① +0.333R/笔——收紧只多 0.019R，代价 90% 召回 |

edge 只存在于 2025-2026（与 2022），按「必须跨窗方向一致」正式否决。

### ① weekly_j_low：跨区间三 horizon 均赢基准 ⇒ **弱 edge 成立（维持现状，不加码）**

2022-2024 全体：H5 46.9%(z=+2.2) / H20 48.7%(z=+11.4) / H60 52.7%(z=+48.6)。
分年 H60：2022 56.9% / **2023 43.8%（不达标）** / 2024 60.2%——pooled 一致为正但幅度小、
分年有晃动。结论：作为既有基础门槛维持现状；它带来的增益主要在 H20/H60 且不大，
不支持再向上加码任何「精选层」。
