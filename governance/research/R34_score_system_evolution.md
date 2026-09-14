# R34 · 打分系统进化 v1：新轴基因组（腿集合×权重格）裁决（预注册）

> **家族**：选股方向（打分系统进化第五轮——R22/R24/R29/R30 四轮**旧轴调权**
> 全证伪后，增量改押**新轴**：腿集合来自 LLM 进化池/手工 DSL 表达式，权重只在
> 有界格上搜）　|
> **证据等级**：L3−（权重格有界枚举多重比较显式标注：搜索族证据等级封顶 L3−，
> 同 R30；过线只是「线索」，终审在 pre2019 untouched 段单独终步）　|
> **状态**：📋 **预注册待跑数（2026-09-14 落档，v0.230 工具已落地；v0.231
> 增强：两阶段省钱模式 --two-stage/--quick + V0 对照臂 --v0-arm 实跑）**——
> 判据 C1~C5 与窗口/宇宙/出场轴/对照臂已全部写死；裁决驱动
> `research/score_evolution_study.py` + 基因组编译层
> `research/evolution/score_genome.py` 已实现并钉测；本机无通达信数据，
> 跑数走附录生产机手册　|
> **依赖**：上游：R30（权重有界组合搜索证伪收口=调权路线死刑，本页是它的
> 「增量在新轴」接续）R32（进化引擎载体与 #71 随机对照纪律）｜判据纪律：
> R12（预注册）R29（灵敏度零翻转）｜口径：R11（量级不作数）R14（幸存者宇宙）｜
> 窗口/宇宙/基底层：同 R31/R33（双窗 s3000、0AMV∧J<13 钉死、`--count 2000`）
> 索引与主图见 [`README.md`](README.md)。

## 主题

四轮打分重建（R22 变体/R24 校准/R29 稳定/R30 组合搜索）全部死于同一处：
**旧轴调权**——腿还是那几条腿，权重怎么调都调不出跨 regime 的 edge。
本页验证另一个假设：打分系统的进化空间应该是「**分轴集合 × 权重**」的
基因组，增量在**新轴**（LLM 造的新 DSL 腿），权重只是有界格上的次要维度。
关键工程洞察：复合打分可以编译成**一条普通 DSL 表达式**（每腿裹
`TS_RANK(leg, K)` 做个股自身历史分位归一后加权求和），于是复合 scorer 直接
复用 strategy_grid 的 `expr:` 轴/双窗/灵敏度全部既有机制，新代码只有基因组
编译层与裁决驱动。

## 目标

判定「新轴基因组」相对对照臂有没有可辩护的增量：过线（进 pre2019 终审
终步）/ 否决 / 样本不足。对照臂回答三个拆解问题：等权复合（权重寻优有
没有加值）、各单腿（复合有没有加值）、随机腿复合同待遇（**新轴本身**
有没有加值——#71 纪律的核心）。

## 结论

**📋 预注册，尚无结论（2026-09-14 落档，判据跑数前写死）。** 本页按 R12
预注册纪律先把判据 C1~C5、窗口、宇宙、出场轴、对照臂、灵敏度与随机对照
口径全部钉死，跑数在生产机执行（附录手册）。先验是悲观的：同赛道四轮
十候选无一幸存（R30 收口语），本轮的唯一辩护是「搜索空间换了」——
判据因此比 R30 更严：多加 R34-C4 随机臂对照（同权重格同待遇），
新轴打不过随机腿就直接标「筛选假象嫌疑」，不许进终审。

---

## 证据与过程

### 工程载体（v0.230 已落地，钉测在案）

- **基因组编译层** `research/evolution/score_genome.py`：
  `compile_composite(legs, weights, rank_window=250)` →
  `w1*TS_RANK((l1),K)+w2*TS_RANK((l2),K)+...`（legs 逐条过白名单 parse；
  weights 非负不全零；产物再过 parse + violations，symbol_len 主门超 300 拒，
  repeat/free_params 结构性豁免——同一 K/同一权重按腿数重复是设计使然）；
  `weight_lattice(n, levels=(0,1,2,3), max_combos=64)`：gcd 比例等价去重
  （尺度不变性论证同 R30：线性加权和同比例缩放排序不变 ⇒ top_n 子集逐位
  不变），字典序，截断保含单腿/等权基线；`perturb_weights(±pct, rng)`
  灵敏度臂；`baseline_arms` 对照臂向量。
- **裁决驱动** `research/score_evolution_study.py`（TOOLS 已注册）：每个
  基因组 = strategy_grid 一个单元格（`expr:` 形态 scorer，cell_runner 可
  注入/monkeypatch，默认实现仿 evolution_loop._make_cell_runner：子进程
  backtest_factors --trade-sim --portfolio，cell_signature 复用免费）；
  双窗/灵敏度/随机对照/pre2019 拒跑全部确定性。
- 钉测：`tests/test_score_genome.py`（33 例）+
  `tests/test_score_evolution_study.py`（23 例，fake cell_runner/v0_runner
  端到端；含两阶段 4 例 + V0 臂 3 例）。

### 判据（跑数前写死）

- **R34-C1（样本量）**：单窗单格笔数 ≥ **100 笔**；不足 ⇒ 结局③样本不足
  （留证不判死刑——同 R31-C1/R33-C1 口径）
- **R34-C2（边际方向）**：top 基因组（挖掘窗 objective 最高者）相对**等权
  复合基准**的 margin 差 **> 0 且双窗同向为正**（挖掘窗+判定窗同号）。
  口径披露：margin = strategy_grid 交易层读数（胜率 − 盈亏平衡胜率
  1/(1+盈亏比)）；「A 层 vs 其余」操作化为 `--top-n 20` 选中子集 vs 全池
  基线——scorer 只排序、top_n 把排序截成「A 层」，预注册按此口径披露
- **R34-C3（灵敏度）**：top 基因组权重 ±50% 扰动（默认 4 臂）重跑挖掘窗，
  **零翻转**（扰动臂 objective 跌破等权基准 = 翻转；读数缺失按翻转计，
  保守）——R29 零翻转纪律：参数敏感的「最优」是网格噪声不是 edge
- **R34-C4（随机对照）**：top 基因组 objective 必须**打过随机臂最高分**
  （随机臂 = random_expr 采样同腿数 × 同权重格同待遇，--n-random 默认 3 条，
  --random-seed 钉死）——#71/R32 纪律；打不过 ⇒ 标「筛选假象嫌疑」，
  新轴增量不成立，直接结局②
- **R34-C5（终审）**：C1~C4 全过才启动——pre2019 untouched 终审段
  （2010-2016）单独终步（生产机，strategy_grid 直接跑 top 基因组，
  不在本工具内；工具窗口与该段相交直接拒跑）。**一票否决**：
  终审不过 = 结局②，过线才谈影子上线（R22/R24/R30 同死因防线：
  edge 属近 regime 的候选必须死在这一步）

### 窗口 / 宇宙 / 出场轴

- 双窗 s3000：挖掘窗 2022-01-01~2024-07-31 / 判定窗 2024-08-01~2026-09-04
  （同 R31/R33 跨窗/主窗口径），两窗共用 codes-file 钉死宇宙（生产机先
  `--dump-codes` 落盘，digest 记入回填区）
- `--count 2000` 防尾部截断；pre2019 终审段（2010-2016）保持 untouched
- gate=j_low（0AMV 做多 ∧ J<13 钉死基底，v0.93 口径）；`--top-n 20`
  选择压力（A 层容量）；成本 25bps
- 出场 = strategy_grid DEFAULT_EXIT_GRID **中档**（pct5_trail08：
  stop 5% + 追踪止盈 8%）——单档出场是本轮的刻意收敛：出场轴寻优已在
  R21/R27 做过，本轮变量只有基因组
- 对照臂：**等权复合 / 各单腿**（恒在权重格保底集，零额外预算）/
  **随机腿复合**（同腿数同格同待遇）/ **s_shape**（注册表现成参照）/
  **V0 臂（v0.231 起实跑，`--v0-arm`）**：V0=live 现行技术分（依赖 enrich
  compute_metrics 的指数相对强度/周月 MACD 腿，非 DSL 可表达）——走
  run_cell 之外的独立载体：`evaluate_trades(collect_all)` 同引擎同出场参数
  出全候选 → 每笔 score 改写为 as-of V0 技术分 → `simulate_portfolio_topn`
  同函数做 A 层选择 → `summarize_trades`+`objective_of` 同公式出读数；
  报告 `arms.v0` + `top_genome.vs_v0` 对照块（**不进预注册判据 C1~C5**，
  判据定义不动）。warmup 注记：V0 评分指标 warmup 限于研究窗口（cell 同款
  `_load_one_bars`），live 链全历史口径残差如实标注——同窗对比成立，
  绝对值不与 live 互引

### 结局判定（跑数后按此机械填）

- 结局① **过线**：C1~C4 全过 ⇒ 进 C5 终审终步；终审过 ⇒ 记「线索」，
  谈影子上线（L3− 封顶，不进 live 打分）
- 结局② **否决**：C2 双窗不同向 / C3 有翻转 / C4 打不过随机臂 / C5 终审
  不过，任一 ⇒ 打分进化 v1 证伪（新轴路线同死，打分系收口转元层反思）
- 结局③ **样本不足**：C1 不满足 ⇒ 留证不判死刑

## 回填区（跑数后填）

待回填：①宇宙 digest 与 codes-file 路径；②top 基因组与逐臂读数
（`_score_evolution__{tag}.json` 落盘点）；③C1~C4 逐条读数与判定；
④C5 终审读数（若启动）；⑤腿集合来源（trajectory_pool 批次 / 手工）。

---

## 附录 · 生产机跑数手册（2026-09-14 随预注册写死）

工具内建：灵敏度与随机对照臂随主跑一次完成（不需单独步骤）；判定窗
加 `--judgment-*` 同跑即双窗。

**第 0 步 · 冒烟（2 腿示例 + s300 短窗，验证链路与格子耗时）**：

```bash
uv run python -m custos.research score_evolution_study \
  --legs "MA(close,20)/close,volume/MA(volume,20)" \
  --mining-start 2024-08-01 --mining-end 2024-12-31 \
  --universe-local --universe-sample 300 \
  --tag r34_smoke
```

**第 1 步 · 钉死宇宙（两窗共用，seed 默认 0）**：

```bash
uv run python -m custos.research.backtest_factors --trade-sim \
  --universe-local --universe-sample 3000 \
  --dump-codes artifacts/logs/r34_codes_s3000_seed0.txt
```

**第 2 步 · 主跑（挖掘窗 + 判定窗双窗，灵敏度/随机臂内建）**：
腿集合按 owner 指定（--legs 内联 或 --legs-file 轨迹池
`artifacts/logs/evolution/{tag}/trajectory_pool.json` 自动取 pass∧gate非空者）：

```bash
uv run python -m custos.research score_evolution_study \
  --legs-file artifacts/logs/evolution/{tag}/trajectory_pool.json \
  --mining-start 2022-01-01 --mining-end 2024-07-31 \
  --judgment-start 2024-08-01 --judgment-end 2026-09-04 \
  --codes-file artifacts/logs/r34_codes_s3000_seed0.txt \
  --count 2000 --v0-arm --tag r34_main
```

产物：`artifacts/logs/score_evolution/r34_main/_score_evolution__r34_main.json`
（stdout 汇总表 = 逐臂 objective/margin/胜率/盈亏比/笔数 + 灵敏度翻转数 +
随机对照判定 + Δmargin 双窗 + V0 对照块）。

**第 2 步（降本变体，v0.231）· 两阶段省钱模式**：r34_v1 实跑 264 格 × 3.5
分钟 ≈ 15h 的降本——**建议下轮一律 `--two-stage`**（粗筛宇宙跑全权重格，
只让 top K 基因装进 s3000 终筛）；试跑档再叠 `--quick`：

```bash
uv run python -m custos.research score_evolution_study \
  --legs-file artifacts/logs/evolution/{tag}/trajectory_pool.json \
  --mining-start 2022-01-01 --mining-end 2024-07-31 \
  --judgment-start 2024-08-01 --judgment-end 2026-09-04 \
  --codes-file artifacts/logs/r34_codes_s3000_seed0.txt \
  --count 2000 --v0-arm --two-stage --coarse-sample 500 --stage1-top-k 8 \
  --tag r34_main_2s
```

口径钉死（与单阶段一致才可对照）：阶段 1 = 粗筛宇宙（同 `--universe-seed`
抽样）× 全权重格 × 全对照臂（含随机臂/V0），按挖掘窗 objective 取 top K
基因组（**对照臂不占 K 名额**）；阶段 2 = 仅晋级基因组 × 原宇宙终筛，
**对照臂在终筛宇宙重跑**；灵敏度/随机对照判定/双窗复测都在阶段 2。
报告 `two_stage` 块留 stage1_survivors/两阶段格数审计痕迹。

成本账（s3000 终筛、粗筛 500 ≈ 1/6 单格成本；灵敏度 4 + 双窗 3 +
s_shape 1 + V0 1 = 9 格固定终筛开销，两种模式相同；quick = n_random 1 +
max_combos 24，会同时把 6 腿的 L 从 64 砍到 24）：

| 配置 | 单阶段 | 两阶段（当量） | 两阶段+quick（当量） |
|---|---:|---:|---:|
| 2 腿 L=9，R=3 | 45 格 | ≈52（不划算，随机臂跑两遍） | ≈31 |
| 6 腿 L=64，R=3（r34_v1 口径） | 265 格 | ≈258 | ≈55 |

（当量 = 粗筛格 × 1/6 + 终筛格。）结论：**两阶段的节省只来自基因组格
（L→K），随机臂是主成本且两阶段各跑一遍**——小格子两阶段反而更贵、
6 腿档省得有限；真正的降本大头是 `--quick`（随机臂 3→1 + 格子帽
64→24）。建议迭代期 `--two-stage --quick` 粗筛（6 腿 ≈55 当量 vs 265，
−79%），**终审/判读前必须满配补跑**（C4 随机臂判据线以满配口径为准，
quick 只是试跑档）。

**第 3 步 · C1~C4 判定**：按预注册判据机械读 `criteria_readings` 块回填。
任一不过 ⇒ 结局②/③，**止步**。

**第 4 步 · C5 终审（C1~C4 全过才启动，单独终步）**：strategy_grid 直接跑
top 基因组复合式（`expr:` scorer 从 `_score_evolution__r34_main.json` 的
`top_genome.expr` 原样拷出），窗口 pre2019 untouched 段：

```bash
uv run python -m custos.research.strategy_grid \
  --scorers "expr:<top_genome.expr 原样>" --gates j_low \
  --start 2010-01-01 --end 2016-12-31 \
  --codes-file artifacts/logs/r34_codes_s3000_seed0.txt \
  --count 2000 --top-n 20 --timeout 10800 -j 4 --tag r34_final
```

⚠️ 本步是**唯一**允许碰 2010-2016 段的步骤（终审段存在的意义）；
终审不过 ⇒ 结局②，一票否决，不许回头调权重再试（那就是 R30 的老路）。
