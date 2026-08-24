# R21 · 从画像到交易：j_low_rsi_deep 进场 gate 的联合寻优验证（strategy_grid 两轮）

> **家族**：交易管理 × 选股边界（因子×出场联合寻优首轮）　|　**证据等级**：L3
> （双窗对照：主窗 2024-07~2026-08 + 跨窗 2022-2024，s3000 钉死宇宙，Wilson 区间
> 与基底不重叠；但 vipdoc 宇宙带幸存者偏差、objective 是排序启发式）　|
> **状态**：✅ 跨窗通过（j_low_rsi_deep）；⚠️ j_low_rsi_div 主窗证伪、跨窗部分平反；
> j_low_weekly_resonance 平庸
> **依赖**：上游：R20（画像发现 rsi_deep 富集——本单元验证它能否交易）R10（止损
> 机制与种子方案）R4（0AMV 基底）｜判据：R12｜口径：R11（量级不作数）R14（幸存者
> 偏差）｜可复现性：R13（cell_signature 钉窗口/宇宙）
> 索引与主图见 [`README.md`](README.md)。产物：`artifacts/logs/strategy_grid/`
>（`_ranked__r20_first.json` / `_ranked__cw_rsi_deep.json` + 报告 md）；出场网格
> `governance/research/exit_grid_first_round_r20.json` / `exit_grid_crosscheck_rsi_deep.json`；
> 驱动器 `research/strategy_grid.py`（v0.85/v0.86/v0.93）。

## 主题

R20 的画像发现（赢家信号日 RSI 深水富集 lift 4.8-6.2）作为**进场 gate**
（J<13 ∧ RSI<25）是否可交易——富集 ≠ 可交易，必须用「因子×出场」联合寻优验证。

## 目标

在固定研究基底（0AMV 做多 ∧ J<13，v0.93）上，验证 j_low_rsi_deep 相对基底
j_low 的 margin/期望优势是否跨窗稳定，并筛出与其搭配的出场档。

## 结论

**① j_low_rsi_deep 跨窗成立，且跨窗更强。** 主窗（s300）：六档出场全部压过基底
同档（objective 1.36 居首 vs 基底 0.80）。跨窗（s3000，2022-2024）：**四档出场
包揽前四**，margin +26.3~+42.2pp vs 基底 +9.6~+14.9pp；笔数 3243-4219（主窗仅
116-155 的低频疑虑解除），Wilson 区间与基底不重叠。低频高精度画像转成了
可交易 margin。

**② 出场搭配跨窗改序：12% 止损跳升最优。** 主窗 margin 最高档是 pct5_cz3
（+19.7pp）；跨窗 **pct12（12% 止损）跳到 +42.2pp**（胜率 73.5%、期望 11.84%
全场最高）——宽止损让深水泵的反弹充分展开。对 R10「5% 是崖」的补充：崖的位置
随信号类型移动，低频高弹信号的止损要更宽；**止损档必须按因子组合分别寻优**
（三维度搭配原则的又一实证）。

**③ 富集 ≠ 可交易的反例入库。** j_low_rsi_div（RSI 底背离）画像臂 lift
1.4-1.5（R20 正向），主窗当 gate 用 margin **−6.8pp 垫底**；跨窗部分平反
（+12~+14.7pp，正向但仍被 deep 全档压制）。画像层与交易层是两种证据，
升级路径必须以交易层为准。j_low_weekly_resonance 两轮皆平庸（+4.3pp），
周线 J 共振不作进场 gate。

**④ live 含义。** 「RSI深水区 RD」标注、R20 面板、j_low_rsi_deep gate 同一份
判定（`rsi_state.rsi_regime` 的 `RSI14<25`，钉测逐点一致）。rsi_state 因子
status 升级（untested→candidate/active）与 1800 接线（分层/提醒如何使用
RD）的证据已齐——**待 owner 拍板**；接线前它仍只是展示标注。

---

## 证据与过程

口径：strategy_grid 驱动 backtest_factors --trade-sim --portfolio；每格自动
`--amv-long-only`（v0.93 基底钉）；scorer=baseline（对照臂，--top-n 0 ⇒ scorer
轴无区分度）；cell_signature 钉显式窗口+宇宙 digest（v0.86）。主窗 tag
`r20_first`（24 格，两阶段 top-k=2，14 实跑 0 失败）；跨窗 tag `cw_rsi_deep`
（3 gate × 4 出场 = 12 格全跑，0 失败，s3000 宇宙 digest 落盘）。

关键数字（跨窗 s3000）：deep 四档 margin +31.0pp（纯BBI，胜率 45.0%/盈亏比
6.15）/ +26.5pp（pct5）/ +26.3pp（pct5_cz3）/ +42.2pp（pct12，胜率 73.5%
期望 11.84%）；基底 j_low 四档 +9.6~+14.9pp。主窗数字见 R20 与本单元产物。

### ⚠️ 边界

- **R11**：基准已实现口径为负 ⇒ 以上只用于相对排序，量级不得引用。
- **R14**：vipdoc 宇宙带幸存者偏差；L3 不得进 live。
- objective = margin + expectancy_R + 0.05×ret/dd 是**排序启发式**，跨档比较
  以 margin/期望分口径为准。
- 两个窗口之外（2019 前）未验证；样本外扩窗是下一轮可选项。
