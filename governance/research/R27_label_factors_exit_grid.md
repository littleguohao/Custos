# R27 · 标注因子家族 × 统一出场轴双窗对照（RSI 家族 + 形态家族）

> **家族**：交易管理 × 选股边界（R21 的扩展批）　|　**证据等级**：L3
> （双窗对照：跨窗 2022-2024 + 主窗 2024-08~2026-09，s3000 钉死宇宙 digest
> `42fba32a9123`；vipdoc 宇宙带幸存者偏差，objective 是排序启发式）｜
> **状态**：RS 跨窗维持否决；RD 复现 R21；R★ 两窗同向但低频（线索非结论）；
> B2 交易层两窗为正但与 R7 信号层否决存在口径张力（不算平反）；异动后B1/
> 突破回踩B1 无加值；主升始发点双口径 0 触发　|　**依赖**：R20（画像）
> R21（深水 gate 验证）R8（H3/H4 否决）R7（H2 系信号层终审）
> ｜判据：R12｜口径：R11（量级不作数）R14（幸存者偏差）
> 产物：`artifacts/logs/strategy_grid/_ranked__rsi_family_{cw,main}.json` +
> `_ranked__form_family_{cw,main}.json`（+ 同名报告 md）；
> 出场轴 `governance/research/exit_grid_rsi_family.json`。

## 主题

owner 问句驱动：「RSI强势区间 RS 配 RD 的优胜出场（pct12 止损 + 分批止盈 +
BBI 跌破两根清仓）是否翻盘？」扩展为两批统一对照——RSI 家族（j_low 对照 /
RS / RD / R★ / RV）与形态家族（B2 确认 / 异动后B1 / 突破回踩B1 / 主升始发点
两口径），同出场轴 × 同宇宙 × 同双窗。

**出场轴**（params 键名同 `simulate_b1_trade` 形参）：

- `base_low` = {}（默认出场：买入K低点止损 + BBI 跌破 2 根清仓）
- `pct12` = 12% 固定止损（R21 的 RD 优胜档）
- `pct12_so5_bbi2` = pct12 + 分批止盈 0.5 + BBI 跌破 2 根（owner 指定组合）

**⚠️ 口径坑（已修，v0.183）**：strategy_grid 默认 `--count 500` 是滚动尾部
窗口——显式 `--start 2022-01-01` 时跨窗只剩 2024-08 之后的 4.5 个月，首批
跨窗 6 格因此空结果被护栏拒写。本研究全部数字为 `--count 2000`（完整覆盖）
重跑后的。R21 当时必传了大 count 才成立。

## 结论

**① RS 配优胜出场不翻盘，维持否决。** 主窗 pct12_so5_bbi2 下 RS 胜率 53.1%
/ 盈亏比 1.92 / margin +18.8pp（赢基底同档 +10.4pp）；但跨窗同一格只剩
+0.9pp，被基底（+15.0pp）甩开——与 R8 的 as-of 否决（跨窗翻负）、R20 的
输家侧富集（lift 0.79）同形态。出场档救不了画像层的输家富集。

**② RD 复现 R21，宇宙漂移下读数稳定。** 本批跨窗宇宙与 R21 不同（digest
24532320184b → 42fba32a9123，vipdoc 每日下载致宇宙漂移），RD 跨窗 pct12 档
3255 笔 / 73.1% / +42.6pp vs R21 的 3243 笔 / 73.5% / +42.2pp——逐位级一致，
R21 结论对新宇宙稳健。新出场档 pct12_so5_bbi2 ≈ pct12 单用（两窗差异
<0.2pp）：**分批止盈 0.5 这层在本网格无增量**。

**③ R★（RSI理想B1 = 强势∧深水，本批新增 gate `j_low_rsi_ideal_b1`）两窗
同向为正但低频。** 主窗 36 笔 69.4% / 2.28 / +38.9pp；跨窗 114 笔 70.2% /
1.74 / +33.6pp。方向两窗一致、margin 量级接近 RD；但笔数太少，Wilson 区间
大概率与基底重叠，按 R12 纪律记为**线索**：若要推进需预注册正式一轮
（扩宇宙/合并窗口补样本）。

**④ RV（底背离）主窗负、跨窗小正，方向不一致，维持 R21 的主窗证伪。**

**⑤ 形态家族：B2 两窗为正但与 R7 信号层否决存在口径张力；异动后B1 /
突破回踩B1 / 主升始发点均无加值或零触发。**

- **B2确认（b2）**：跨窗 base_low 49.8% / 2.87 / **+23.9pp**、pct12_so5_bbi2
  52.3% / 2.40 / +22.9pp（赢基底同档 8~14pp）；主窗 +19.1~+19.7pp
  （与基底同档 +20.2pp 持平）。两窗同为正——但注意 R7 终审已在信号层否决
  B2 系（全中≈追高、跨区间不成立），本批是交易层（trade-sim + AMV 做多钉 +
  组合层）口径，两者衡量的不是同一物。**记为口径张力而非平反**：若要主张
  升级，须预注册一轮把两个口径对齐再审。
- **异动后的B1（surge_then_b1）**：跨窗 pct12 +15.2pp ≈ 基底 +15.0pp（无
  加值），主窗全档 −8.0~−8.7pp——与 R7 否决一致。
- **突破回踩型B1（breakout_pullback_b1）**：两窗全档 −2.2~+3.2pp，无加值。
- **主升始发点（main_rally / main_rally_above）**：s300 定向探测（双窗 ×
  双口径 × base_low，`--allow-empty`）**全部 0 笔成交**——与 R8 H4（s3000×
  2年 0 触发）一致。⚠️ 该 gate 无预计算路径，s3000 全窗单格扫描超 2.4h
  （超 timeout 被杀），本批未进网格；0 触发 ⇒ 出场轴无交互可测。

## 证据与过程

口径：strategy_grid 驱动 backtest_factors `--trade-sim --portfolio`，每格
自动 `--amv-long-only`（v0.93 基底钉），scorer=baseline（top-n 0 无区分度），
`--count 2000 --timeout 10800`，-j 4。非白名单 gate（b2/surge/main_rally）
走慢速切片路径，单格 18~35 分钟（首抡 30 分钟超时杀过一波，本批为
`--timeout 10800` 重跑）。

### RSI 家族 · 跨窗 2022-2024（s3000）

| gate | 出场 | 笔数 | 胜率 | 盈亏比 | margin |
|---|---|---:|---:|---:|---:|
| j_low_rsi_deep | base_low | 4204 | 44.8% | 6.22 | +31.0pp |
| j_low_rsi_deep | pct12_so5_bbi2 | 3255 | 73.2% | 2.28 | +42.7pp |
| j_low_rsi_deep | pct12 | 3255 | 73.1% | 2.28 | +42.6pp |
| j_low_rsi_ideal_b1 | base_low | 129 | 37.2% | 2.68 | +10.0pp |
| j_low_rsi_ideal_b1 | pct12 | 114 | 70.2% | 1.78 | +34.2pp |
| j_low_rsi_ideal_b1 | pct12_so5_bbi2 | 114 | 70.2% | 1.74 | +33.6pp |
| j_low_rsi_div | base_low | 9934 | 31.2% | 4.36 | +12.5pp |
| j_low_rsi_div | pct12_so5_bbi2 | 8088 | 51.9% | 1.74 | +15.5pp |
| j_low_rsi_div | pct12 | 8088 | 51.8% | 1.76 | +15.6pp |
| j_low | base_low | 70985 | 28.7% | 4.22 | +9.5pp |
| j_low | pct12 | 42995 | 48.7% | 1.96 | +15.0pp |
| j_low | pct12_so5_bbi2 | 42995 | 48.9% | 1.95 | +15.0pp |
| j_low_rsi_strong | pct12_so5_bbi2 | 4235 | 43.8% | 1.33 | +0.9pp |
| j_low_rsi_strong | pct12 | 4235 | 43.5% | 1.33 | +0.6pp |
| j_low_rsi_strong | base_low | 6900 | 25.8% | 2.80 | −0.5pp |

### RSI 家族 · 主窗 2024-08~2026-09（s3000）

| gate | 出场 | 笔数 | 胜率 | 盈亏比 | margin |
|---|---|---:|---:|---:|---:|
| j_low_rsi_ideal_b1 | base_low | 42 | 45.2% | 6.26 | +31.5pp |
| j_low_rsi_ideal_b1 | pct12_so5_bbi2 | 36 | 69.4% | 2.28 | +38.9pp |
| j_low_rsi_ideal_b1 | pct12 | 36 | 69.4% | 2.20 | +38.2pp |
| j_low_rsi_deep | base_low | 1662 | 32.4% | 5.04 | +15.9pp |
| j_low_rsi_strong | base_low | 5295 | 36.4% | 3.11 | +12.0pp |
| j_low_rsi_strong | pct12 | 3953 | 52.7% | 1.94 | +18.6pp |
| j_low_rsi_strong | pct12_so5_bbi2 | 3953 | 53.1% | 1.92 | +18.8pp |
| j_low_rsi_deep | pct12 | 1189 | 56.5% | 1.51 | +16.7pp |
| j_low_rsi_deep | pct12_so5_bbi2 | 1189 | 56.7% | 1.49 | +16.5pp |
| j_low | pct12_so5_bbi2 | 20720 | 48.8% | 1.61 | +10.4pp |
| j_low | pct12 | 20720 | 48.5% | 1.60 | +10.0pp |
| j_low | base_low | 31745 | 28.9% | 3.33 | +5.8pp |
| j_low_rsi_div | pct12_so5_bbi2 | 3092 | 37.6% | 0.99 | −12.6pp |
| j_low_rsi_div | pct12 | 3092 | 37.3% | 0.97 | −13.4pp |
| j_low_rsi_div | base_low | 3951 | 21.2% | 2.69 | −5.9pp |

### 形态家族 · 跨窗 2022-2024（s3000）

| gate | 出场 | 笔数 | 胜率 | 盈亏比 | margin |
|---|---|---:|---:|---:|---:|
| b2 | base_low | 10981 | 49.8% | 2.87 | +23.9pp |
| b2 | pct12_so5_bbi2 | 10891 | 52.3% | 2.40 | +22.9pp |
| b2 | pct12 | 10891 | 51.8% | 2.43 | +22.6pp |
| surge_then_b1 | base_low | 22603 | 28.6% | 4.30 | +9.7pp |
| j_low | base_low | 70985 | 28.7% | 4.22 | +9.5pp |
| j_low | pct12 | 42995 | 48.7% | 1.96 | +15.0pp |
| j_low | pct12_so5_bbi2 | 42995 | 48.9% | 1.95 | +15.0pp |
| surge_then_b1 | pct12_so5_bbi2 | 14295 | 47.2% | 2.11 | +15.1pp |
| surge_then_b1 | pct12 | 14295 | 47.0% | 2.14 | +15.2pp |
| breakout_pullback_b1 | pct12 | 2728 | 40.4% | 1.41 | −1.2pp |
| breakout_pullback_b1 | pct12_so5_bbi2 | 2728 | 40.5% | 1.41 | −1.0pp |
| breakout_pullback_b1 | base_low | 3572 | 26.5% | 2.81 | +0.3pp |
| main_rally / main_rally_above | base_low | 0 | — | — | 0 触发（s300 探测） |

### 形态家族 · 主窗 2024-08~2026-09（s3000）

| gate | 出场 | 笔数 | 胜率 | 盈亏比 | margin |
|---|---|---:|---:|---:|---:|
| b2 | base_low | 5744 | 46.5% | 2.74 | +19.7pp |
| b2 | pct12_so5_bbi2 | 5684 | 49.4% | 2.36 | +19.6pp |
| b2 | pct12 | 5684 | 48.7% | 2.38 | +19.1pp |
| j_low | base_low | 33844 | 29.6% | 4.51 | +11.5pp |
| j_low | pct12_so5_bbi2 | 21711 | 51.7% | 2.17 | +20.2pp |
| j_low | pct12 | 21711 | 51.4% | 2.19 | +20.1pp |
| breakout_pullback_b1 | pct12_so5_bbi2 | 1161 | 38.2% | 1.83 | +2.8pp |
| breakout_pullback_b1 | pct12 | 1161 | 37.9% | 1.88 | +3.2pp |
| breakout_pullback_b1 | base_low | 1406 | 20.5% | 3.40 | −2.2pp |
| surge_then_b1 | pct12_so5_bbi2 | 5576 | 35.9% | 1.28 | −8.0pp |
| surge_then_b1 | pct12 | 5576 | 35.6% | 1.27 | −8.5pp |
| surge_then_b1 | base_low | 8940 | 19.5% | 2.55 | −8.7pp |
| main_rally / main_rally_above | base_low | 0 | — | — | 0 触发（s300 探测） |

（主窗 j_low 系为 `--count 500` 首抡产物，窗口起点 2024-08-01 被截 9 个
交易日，影响可忽略；其余窗格均为 count 2000 完整覆盖。）

### ⚠️ 边界

- **R11**：基准已实现口径为负 ⇒ 只用于相对排序，量级不得引用。
- **R14**：vipdoc 宇宙带幸存者偏差；L3 不得进 live。
- 宇宙漂移说明：本批 digest 与 R21 不同（vipdoc 日更），RD 读数逐位级复现
  R21 ⇒ 结论对宇宙扰动稳健，但跨批比较笔数时应以本批内对照为准。
