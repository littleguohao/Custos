# B1 回测调查结论与框架（2026-07）

> 目的：沉淀 B1（J<13 反转买点）系统性回测的**结论、框架形态、偏差警示、工具用法**，
> 防止后续重复踩坑或误把有偏结果当实盘预期。**所有内容为研究结论，未改动任何线上买入逻辑。**

## 0. 一句话结论
**B1 的可辩护框架 = 0AMV择时 + 反转K入场(J<13+缩量企稳) + 带空间的止损 + BBI移动止盈 + 分散多槽。**
per-trade 有正 edge（跨止损参数稳健），但**收益数字被幸存者/牛市窗/肥尾严重高估**，
真实预期应为**个位数 CAGR、中等回撤的稳健策略，而非暴利机器**。
日线价量**形态/选股**（S_shape/买弱/reversal_quality）**均无 alpha——随机择优 ≥ 花哨选择器**；
edge 来自**择时 + 分散 + 交易管理**，不是选股。

## 1. 工具：`07_tools/screening/backtest_factors.py`
研究用回测器（只读本地日线，不改线上）。可组合的开关：
- **入场门槛 `--entry-filter`**：`none` / `j_low`(J<13) / `reversal_k`(J<13+缩量+量底+小实体+小振幅) / `j_macd_turn`(J<13+MACD柱上行)。
- **打分/选择器 `--scorer`**：`baseline`(不选) / `reversal_quality`(反转成色0-4) / `s_shape` / `b1_pullback` / `alpha101` / `alpha_pvcorr` / `low_vol` / `momentum`。
- **交易模拟 `--trade-sim`**：进场=可买日收盘；`--stop-mode low|pct`(+`--stop-pct`)；BBI 移动止盈(站上BBI后连破`--bbi-consec`日卖)；`--time-stop`。
- **择时 `--amv-long-only`**：仅 0AMV『做多』区间进场(读指南针 compass_amv 历史)。
- **组合 `--portfolio`**：固定风险仓位 `--risk-pct`，`--max-concurrent`/`--max-pos`；`--top-n` 横截面择优；`--cost-bps` 成本。
- **周线 `--weekly`**；**内存**：流式逐股加载 + `--max-signals-per-code` + `--summary-only`。

## 2. 关键结论（按逻辑链）
1. **日线价量形态无短周期 alpha**：S_shape、"买弱"评分在全市场阈值扫描下无 lift（方向A）。
2. **"完美B1"指纹(10只赢家反标)作进场过滤有害**：`compute_b1_pullback_fit` recall 100%，但全市场周线交易模拟
   期望 -0.42%/笔，劣于无差别进场 baseline(+0.96%)——买弱指纹排除了做多区间的突破赢家。**仅描述性，不作买入依据。**
3. **固定 horizon 回测为负；edge 出现在交易管理**(止损截断 + BBI 移动止盈让利润奔跑)。
4. **0AMV做多择时有价值**：baseline+AMV 期望 +0.96%/笔（引入 J<13 前）。个股服从大盘，坐实。
5. **J<13 单用**：**周线为负**(接飞刀，均亏深)、**日线为正**(+2.06%)。"买入K最低"止损对超卖贴低K**过紧→秒止损**(83.7%被扫)。
6. **反转K(J<13+缩量企稳)是关键过滤**：排除飞刀。配**带空间止损**(pct 6~12% 全为正期望，8% 甜蜜点)，
   per-trade edge **跨止损参数稳健**(非过拟合单点)。但信号稀少→5槽组合 CAGR 仅约 3%(容量受限)。
7. **杠杆在容量/分散，不在门槛**：5槽组合下 j_low/reversal_k/j_macd_turn 的 CAGR 近似(2-3%)，
   因组合被并发上限卡住；`total_R` 具误导性(全候选池≠组合选中子集)。**更多小槽分散**降回撤。
8. **横截面择优：正向无用，反向(contrarian)有戏但待验**：
   - 正向按"教科书反转成色"(reversal_quality)择优 **劣于随机**(baseline top-5 +43% > reversal_quality +33%)。
   - 特征归因(train/test)：8 因子中仅 reversal_quality 一致，且为**稳健负预测**(train −3.42%/test −2.75% Q4−Q1)——越教科书越差。
   - 据此**反向**择优 `reversal_quality_inv`(选最"丑"的 J<13 回踩)：**同偏样本内显著优于随机**
     (+69.4% vs +43%，CAGR 31% vs 20%，收益/回撤 **4.91 vs 3.16**，回撤相近)，与归因自洽。
   - ⚠️ **仍在同一幸存者 + 近两年牛市 + 0AMV多头样本内**，train/test 只是该窗前后半(弱OOS)。**未经跨年份/含退市真样本外验证**。
     **这是全轮最强候选假设，非结论**；contrarian 信号尤需经济逻辑 + 真OOS 坐实(见 §5)。可能的逻辑：极致缩量=无人接盘继续阴跌，
     "丑"回踩(有量/实体大)=有真买盘介入→跟进更好；亦可能是数据artifact。
9. **外部因子借鉴**：
   - 101 Formulaic Alphas(Kakushadze)：核心可借=**横截面ranking选股**(非绝对阈值)；alpha 多为超短反转/市场中性，与 B1 不同源，仅作选择器候选(alpha101/alpha_pvcorr)。
   - Fama-French 3/5：**风险/归因模型非信号**；A股应用 CH-3/CH-4(壳调整size+EP+换手)；可借=归因严谨性(alpha vs beta) + 特征溢价选择器(low_vol/momentum)。

## 3. ⚠️ 偏差警示（务必牢记，否则会把有偏结果当真）
- **幸存者偏差**：`--universe-local` 只含**今天仍存续**的票，退市/暴雷全剔除 → 做多+移动止盈系统性高估。**无退市数据无法消除**，结果只能视为**乐观上界**。
- **regime 偏差**：`--amv-long-only` 只在牛市段进场；牛市做多本就赚 → 部分是 **beta 非 alpha**。
- **肥尾/均值陷阱**：池均期望曾达 +15.16%/笔（不可信），系少数幸存者 moonshot 拉高 **mean**；**看 `median_return` 与被选中子集**，不看池均值。
- **窗口敏感**：`--count` 250→500 使池期望 -0.15%→+15.16%（25倍）——**稳健策略不会因历史长度翻天**。当前回测仅取"最后 count 根"，**无 walk-forward/样本外**，跨年份稳健性未验证。
- **周线止损失真**：周线止损按周级执行，跳空低开尾损被夸大。

## 4. 当前"最佳配置"（有偏上界，非实盘预期）
`--entry-filter j_low --amv-long-only --stop-mode pct --stop-pct 8 --bbi-consec 2`
`--portfolio --risk-pct 1 --max-concurrent 15 --max-pos 6 --top-n 5`（**择优器用 baseline 即可，reversal_quality 反而更差**）
- 同口径对照：`baseline`(随机top-5) CAGR ~20% / 回撤 ~14% / 收益回撤 ~3.16，**优于** `reversal_quality`(~16%/16%/2.12)。
- 绝对数被**幸存者+牛市窗+肥尾**高估，**不可作实盘预期**；相对对照(随机≥选择器)有效。
- 作为**证据层候选打分/排序**接入是合理的；**不需要形态选择器**——简单随机/等权即可。

## 5. 未决 / 后续
- **【最高优先·进行中】跨年份真样本外验证**：用户有多年 qlib 数据(仿 FZT `data/2006_2020`、`2021_2026`)。待接入：给 `backtest_factors` 加 **qlib 数据源 + 指定日期区间**加载(现仅 tail-count)，做 **walk-forward**(早期校准/后期验证)，**重点验 `reversal_quality_inv`(选丑反转)是否跨年份、牛熊都成立**。前置：确认 0AMV(指南针)历史回溯年限(早期年份可能需先跑全 regime)。
- **幸存者偏差**：若 qlib instruments 为 point-in-time(含退市)→ 可真正去偏，得可信真实预期(大概率远低于 +69%)。需退市股数据才能修正。
- **全市场 OOM**：流式+cap+summary-only 已缓解；`--top-n`(collect_all) 大样本仍重，彻底解需"只模拟被选中交易"或向量化(仿 FZT 单表 groupby)。
- **归因**：可加"交易收益对基准回归求 alpha/beta"，判断收益是 alpha 还是小盘/牛市 beta。

## 6. 非目标（边界）
- 以上全部为**研究/回测结论**，**未改动** `b1_swing_strategy.md`、`b1_holding_state.py`、screening 分层等**任何线上买入逻辑**。
- `b1_pullback` 等描述性因子**不驱动分层**、不作买入依据。
- 任何上线前需：样本外稳健 + 去偏（或明确接受偏差）+ 用户确认。
