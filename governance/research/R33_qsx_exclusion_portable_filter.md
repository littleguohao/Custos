# R33 · 「跌破 QSX/DKX 未收复=排除」可移植回避条件的边际验证（预注册）

> **家族**：风控/选股回避方向（R23 v2 收口时指名的下一题：排除态不是选股
> 因子，是可移植的风控腿——叠加在既有基底与出场轴上单独测边际）　|
> **证据等级**：L3−（2 窗 × 3 出场档 = 6 格多重比较显式标注：单格过线只记
> 「线索」，须第二轮扩窗复核才可升级，同 R28 对 RV、R31 对 qn gate 的处理）　|
> **状态**：📋 **预注册待跑数（2026-09-14 落档）**——判据 C1~C4 与窗口/宇宙/
> 出场轴/工程载体口径已全部写死；本机无通达信数据，跑数走附录生产机手册　|
> **依赖**：上游：R23（v2 实测定论：排除态是共振 C 臂全部边际；本页是它的
> 独立成腿验证）｜判据纪律：R12（预注册）；口径：R11（量级不作数）
> R14（幸存者宇宙）｜窗口/宇宙/出场轴：同 R27/R31（双窗 s3000、统一出场轴、
> `--count 2000` 防尾部截断）
> 索引与主图见 [`README.md`](README.md)。

## 主题

R23 v2 已证明：「跌破 QSX/DKX 后未收复 ⇒ 排除」这个排除态是共振 C 臂的
**全部边际**（干净反弹计数本身零价值，hit-only ≈ 结构过滤臂 B）。但 R23 的
证据有一个结构性限制：排除态只在「共振命中票内」测过边际（C 完整 vs
C hit-only 的差值法），从未作为**独立的、可移植的回避条件**测过——即
「任何票只要处于跌破未收复状态就不买」，不要求共振命中，叠加在
0AMV 做多 ∧ J<13 钉死基底上、跨统一出场轴，它的边际是正还是负？

## 目标

判定「排除态作为独立入场回避条件」（gate 语义：基底信号 ∧ 不处于跌破未
收复状态）相对同窗同轴无条件基准的边际：过线（进 strategy_grid 因子轴
下一轮）/ 否决 / 样本不足。**不追求胜率，追求盈亏比**（核心原则第 0 条）
——排除态的预期形态正是「砍大量交易换质量」，胜率与盈亏比并列进判据。

## 结论

**📋 预注册，尚无结论（2026-09-14 落档，判据跑数前写死）。** 本页按 R12
预注册纪律先把判据、窗口、宇宙、出场轴、工程载体口径全部钉死，跑数在
生产机执行（附录手册四步）。先验的两面都记录在案：R23 v2 排除项在 QSX 族
负基线下把 C 臂掰正（支持边际为正），但在 BBI 族正基线下 C 完整回不到
基底 A（支持边际为负）——「排除项加值依赖出场族」已被实测，本页不预设
方向，只按 C1~C4 机械判定。

---

## 证据与过程

### 先验证据（R23 v2 实测，数值逐条对账 `R23_qsx_resonance_filter.md`）

QSX 族出场（stop 12% + 保本 0.05 + 分批止盈 0.5 + 跌破 QSX 清仓，R23 v2 主轮）：

| 臂 | 笔数 | 胜率 | 盈亏比 | 均收 | 期望R |
|---|---:|---:|---:|---:|---:|
| C hit-only（无排除项） | 10627 | 38.6% | 1.21 | −0.37% | −0.031 |
| C 共振v2 完整（含排除项） | 1834 | 19.3% | 4.78 | +0.28% | +0.023 |

排除项砍掉 83% 交易（10627→1834）后均收/期望R 双双转正——「干净反弹计数」
本身零价值（hit-only ≈ B 臂），排除态是全部边际（R23 v2 判读原文）。

BBI 族出场对照（2026-09-01，`--bbi-exit-consec 2 --qsx-exit-consec 0`）：

| 臂（BBI 族） | 笔数 | 胜率 | 盈亏比 | 均收 | 期望R |
|---|---:|---:|---:|---:|---:|
| A 基底 | 15740 | 37.4% | 2.29 | +1.12% | +0.093 |
| C hit-only（无排除项） | 7407 | 33.6% | 2.17 | +0.31% | +0.026 |
| C 共振v2 完整（含排除项） | 1793 | 24.9% | 3.77 | +0.64% | +0.053 |

⚠️ 关键先验：**排除项「加值」依赖出场族**——QSX 族负基线下它把 C 掰正
（hit-only −0.031 → 完整 +0.023）；BBI 族正基线下 C 完整（0.053）回不到
基底 A（0.093），好出场里它反而拖住收益。且以上全是「共振命中票内」的
差值法证据；排除态作为独立回避条件（不要求共振命中）的边际**从未测过**
——这正是本页要回答的。

### 判据（2 窗 × 3 出场档 = 6 格，跑数前写死）

- **R33-C1（样本）**：单窗单格笔数 ≥ **100 笔**；不足 ⇒ 结局③「样本不足」
  （留证不判死刑——同 R31-C1 / R27 对 R★ 的口径）。预警：排除态在 R23 v2
  共振命中票内实测砍 76~83% 笔数（QSX 族 10627→1834、BBI 族 7407→1793）；
  独立回避口径的剔除率未知，笔数小的格按本条款机械处理
- **R33-C2（边际）**：交易模拟胜率相对同窗同档 none 基准 **> +3pp**
  （判据线同 R25/R26/R31），且**双窗同向为正**（一正一负 = 失线）。
  口径披露：判据线数值 +3pp 不变，度量载体 = 交易模拟胜率差（pp），
  与 R31 的口径披露一致
- **R33-C3（盈亏比）**：盈亏比不劣于同窗同档基准的 **80%**（同 R31-C3；
  胜率与盈亏比并列，防假过线——R23 v2 实测 19.3%↔4.78 的胜率/盈亏比
  极端互换正是 C3 专防的形态）
- **R33-C4（级别）**：6 格中任何单格过 C1~C3 也只记「线索」（多重比较税，
  同 R31-C4；格数虽少于 R31 的 12 gate 批，税额不减），升级须第二轮
  扩窗复核同向

### 窗口 / 宇宙 / 出场轴

- 双窗 s3000：跨窗 2022-01-01~2024-07-31 / 主窗 2024-08-01~2026-09-04，
  seed=0（同 R27/R31）；两窗共用同一份 codes-file 钉死宇宙（生产机先
  `--dump-codes` 落盘，digest 记入回填区——R31 吃过 codes-file 未留档、
  宇宙与 R27 不严格同批的亏，本页写死为先落盘后跑数）
- `--count 2000` 防尾部截断；pre2019 终审段（2010-2016）保持 untouched
  （两窗均不相交）
- scorer=baseline（`--top-n 0`，无区分度要求——问的是 gate 边际不是排序）；
  每格自动 `--amv-long-only`（v0.93 基底钉）
- 统一出场轴 `governance/research/exit_grid_rsi_family.json` 三档：
  base_low / pct12 / pct12_so5_bbi2——第三档（stop12 + 保本 0.05 引擎默认
  + 分批止盈 0.5 + BBI 跌破两根）正是 R23 v2 📊 BBI 族对照口径，先验读数
  可跨研究对账
- 基准：同宇宙同窗同出场轴的 none gate（`ENTRY_GATES["none"]`，每根 K 线
  都当信号的全市场基线）——`--gates none,qsx_exclusion_free` 同批跑，
  基准与处理臂天然同窗同轴同 codes-file

### 工程载体评估（跑数前的语义缺口结论）

- 现成 gate 不表达排除态：`ENTRY_GATES["qsx_gt_dks"]` /
  `["j_low_qsx_gt_dks"]`（`research/backtest_factors.py:1614/1632`，
  注册于 :1654-1655）是 R23 过滤①「QSX>DKS 多头结构」过滤——语义是
  「结构在位」而非「跌破未收复」，且该过滤已在 R23 双证伪，不能复用
- 排除态状态机唯一实现：`core/factors/qsx_resonance.py` 的
  `_unrecovered_line`（:109）+ `resonance_v2_snapshot(df)`（:178），返回
  `{"available","hit","excluded","events","reason"}`；`excluded` 即「末根
  处于跌破未收复」（QSX 或 DKS 任一，as-of 因果，已被 R23 v2 的 35 例
  钉测钉住）
- **缺口**：全仓没有「基底 ∧ 非排除态」的现成臂——qsx_resonance_study 的
  `--no-exclusion` 只能做「共振命中票内」的差值法对照（手册第 0 步用它），
  strategy_grid 的 ENTRY_GATES 里没有独立回避条件臂
- **临时适配口径（本页写死，owner 拍板后实施，不在本页落代码）**：前置
  最小 gate `qsx_exclusion_free(df_slice, precomputed=None) -> bool`——
  as-of 调 `resonance_v2_snapshot(df_slice)`；`available and not excluded`
  ⇒ True；`available=False` ⇒ **False**（fail-closed，与全仓 gate 同族，
  历史不足的票不放行）；约 10 行 + 合成钉测 3 例（贴线未破 / 跌破已收复 /
  跌破未收复）。实施工时 <1h；口径一经实施不因跑数结果修改

### 结局判定（跑数后按此机械填）

- 结局① **过线**：某出场档双窗 C1~C3 全过 ⇒ 记线索（C4），开第二轮扩窗
  复核；复核同向 ⇒ 进 strategy_grid 因子轴下一轮候选
- 结局② **否决**：无任何出场档双窗同向过线 ⇒ 排除态作为独立回避条件
  证伪；R23 v2 的「可移植风控腿」方向性建议回收（它只在共振命中票内、
  负基线出场族下成立）
- 结局③ **样本不足**：C1 不满足 ⇒ 留证不判死刑，扩窗或降基底门槛后重估

## 回填区（跑数后填）

待回填：①两窗 codes-file digest（应一致）与各格笔数；②none 基准逐档
读数（与 R31 同窗基准可交叉对账）；③qsx_exclusion_free 逐格读数与
C1~C4 逐格判定；④第 0 步 kill-switch 差值读数；⑤产物路径
（`artifacts/logs/strategy_grid/_ranked__r33_*` 与逐格 cell JSON）。

---

## 附录 · 生产机跑数手册（2026-09-14 随预注册写死）

**第 0 步 · kill-switch 初筛（现有代码直接可跑，不做工程）**：
qsx_resonance_study 差值法——Cp（共振独立臂，含排除态）vs Cp
`--no-exclusion`（同臂去排除态），BBI 族出场口径：

```bash
uv run python -m custos.research.qsx_resonance_study --arm Cp \
  --max-stocks 400 --seed 0 --start 2010-01-01 \
  --stop-pct 12 --breakeven 0.05 --scale-out 0.5 \
  --bbi-exit-consec 2 --qsx-exit-consec 0
# 同参数加 --no-exclusion 再跑一臂；--compare 两个 JSON 读排除项边际
```

先 400 只冒烟，正常再 `--max-stocks 3000`。**排除项边际 ≤0 ⇒ 直接结局②
否决，省掉后续 gate 工程**。口径警示（写死）：差值法测的是「共振命中票内」
的排除边际，不是独立回避条件——只作 kill-switch 不作主证据；>0 也必须
走完 1~3 步才算数。

**第 1 步 · 前置工程 + 冒烟**：按上文「临时适配口径」落
`qsx_exclusion_free` gate（`research/backtest_factors.py` ENTRY_GATES 注册
+ 合成钉测 3 例）；strategy_grid 冒烟验证信号量非零：

```bash
uv run python -m custos.research.strategy_grid \
  --scorers baseline --gates none,qsx_exclusion_free \
  --exit-grid governance/research/exit_grid_rsi_family.json \
  --start 2024-08-01 --end 2024-12-31 --sample 300 \
  --count 2000 --top-n 0 --timeout 10800 -j 4 --tag r33_smoke
```

**第 1.5 步 · 钉死宇宙**：生产机一次性落 codes-file（两窗共用，seed 默认 0）：

```bash
uv run python -m custos.research.backtest_factors --trade-sim \
  --universe-local --universe-sample 3000 \
  --dump-codes artifacts/logs/r33_codes_s3000_seed0.txt
```

digest 由 strategy_grid 启动时自动打印（`[INFO] 宇宙 digest=...`），两窗
应一致，记入回填区。

**第 2 步 · 跨窗 2022-01-01~2024-07-31（s3000）**：

```bash
uv run python -m custos.research.strategy_grid \
  --scorers baseline --gates none,qsx_exclusion_free \
  --exit-grid governance/research/exit_grid_rsi_family.json \
  --codes-file artifacts/logs/r33_codes_s3000_seed0.txt \
  --start 2022-01-01 --end 2024-07-31 \
  --count 2000 --top-n 0 --timeout 10800 -j 4 --tag r33_cross
```

（只 2 个 gate、默认 `--top-k 2` ⇒ 两臂都跑全三档出场轴，无自适应截断；
none 即基准，与处理臂同窗同轴同 codes-file。）

**第 3 步 · 主窗 2024-08-01~2026-09-04（s3000）**：同上，窗口改
`--start 2024-08-01 --end 2026-09-04 --tag r33_main`。

**第 4 步 · 基准对照与 digest 核对**：两窗的 none 格即基准（同批同签名
上下文，不另跑）；核对两窗 `[INFO] 宇宙 digest` 一致并与 codes-file 实算
一致；按 C1~C4 逐格判定并回填「回填区」。⚠️ 若同签名旧格被自动复用而
digest 对不上，是事故不是省事——必须 `--force` 重跑。
