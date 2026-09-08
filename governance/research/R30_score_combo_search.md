# R30 · 打分权重有界组合搜索（score_combo_search_study，预注册）

> **家族**：选股方向（打分重构第四轮——R29 判负后 owner 拍板「有界组合搜索」）　|
> **证据等级**：L3−（三窗终审仍在，但候选**来自搜索族**——多重比较风险显式标注，
> 证据等级低于 R22/R24/R29 的「证据设计 ≤4 候选」路线；终审通过也带「经搜索」
> 注记，进 live 前必须影子观察）　|
> **状态**：📋 **预注册（2026-09-08，跑数前）**——搜索空间/筛选线/幸存者规则本页
> 写死，跑数后如实回填
> **依赖**：上游：R24 Phase 1（37 腿三窗 ablation——搜索空间的证据地基）
> R29（四候选窗内贴线过、灵敏度翻转判负——本轮的直接起因；owner 质疑「组合
> 没穷举」成立，遂有本轮）｜判据：R12（预注册纪律——搜索族也是先写死再跑数）
> ｜口径：R11（量级不作数）R14（幸存者宇宙）R3（半窗纪律）
> 索引与主图见 [`README.md`](README.md)。脚本
> `research/score_combo_search_study.py`（--search/--final，判据机械复用
> `score_variants_study`/`score_calibration_study`/`score_stability_study`）。
> 产物：`artifacts/logs/score_combo_search_study/r30_search.json`、
> `r30_final_pre2019.json`（跑数后落）。

## 主题

R29 证明了「证据设计的 4 个候选」在 40%/2.4 双线下没有参数稳健的窗内解；
owner（2026-09-08）合理质疑：组合空间并未穷举。本轮在**不碰 pre2019** 的
前提下把证据腿池的组合空间**有界枚举**一遍——用「加严筛选线」支付多重比较
的显著性税，幸存者再进 pre2019 一次终审。

## 目标

在预注册的搜索族内找出**调参双窗（主窗+跨窗）同时满足加严线**的权重组合：
篮子胜率 ≥45% 且盈亏比 ≥2.6、Spearman 半窗不翻、强档 ≤15%、±50% 零翻转；
幸存者 ≤3 个进 pre2019 untouched 终审（R29 基线终审线：胜率≥40% ∧
盈亏比≥2.4 ∧ Spearman 半窗同正，一票否决）。

## 结论

📋 **预注册（2026-09-08，跑数前）：结论未定且不许预设。** 判定是机械的：
搜索族、筛选线、幸存者规则均已写死（见下），脚本只负责执行。三种合法结局：
①无组合过加严线 ⇒ 判负，「组合空间无稳健解」收口；②有过线组合但 pre2019
终审失线 ⇒ 判负（与 R22/R24 同死因）；③终审保持 ⇒ 「经搜索」L3− 候选，
进 Phase 4 前影子观察 N 个交易日。无论何种结局如实回填本段并更新头部状态。
⚠️ 诚实条款：搜索族 ~4000 组合 ⇒ 即使终审通过，也存在「搜索后偶然过线」
的残余概率，本路线证据等级封顶 L3−，不得与 R21（gate 双窗）同级引用。

---

## 证据与过程

### 搜索空间（预注册写死）

**正腿池（5 条）**＝R24 Phase 1 三窗 add-one 为正的 4 条 + leader_volume
（add-one 三窗正/LOO 互斥，R24 P3 曾用，给搜索一次机会）：

| 腿 | 权重档位 |
|---|---|
| rsi_deep_oversold / weekly_j_low / rsi_bull_div / macd_bottom_divergence / leader_volume | 各 ∈ **{0, 10, 20, 30, 40}**（0 = 删腿），至少一条非零 |

**负腿块（6 条三窗一致负）**：rsi_strong / b1_ignition / volume_contraction /
relative_strength_strong / macd_top_divergence / ignition —— 整体二态：
**关 / 开（每条 −5）**，不做逐腿排列。

**规模与去重**：5 腿 × 5 档 − 全零 = 3124；×2（负腿块）= 6248。打分是纯
线性加权（clamp 100 前），**正权重同比例缩放不改变排序**（篮子=top-20% 按
分数排序、Spearman 秩相关均尺度不变）⇒ 按最大公约数归一去重（如
10/10/20/0/0 与 20/20/40/0/0 同类），每个排序等价类只评一次，代表权重 =
原始向量 ×10；C4（强档≥60 占比）在代表权重上度量。去重后 **2851 个排序
等价类 ×2（负腿块）= 5702 组合**（2026-09-08 脚本实测；本条是对预注册
估算数的精确化，搜索空间本身未变）。现行腿全部归零
（证据重构形态，同 R24 P1 / R29），打分机械复用
`score_calibration_study.make_candidate_score({}, panel_weights)`。

**不在搜索内**（预注册排除，防维数爆炸）：zhixing 系/pullback_shrink/
platform_pullback_b1/macd_above_water 等 pre2019 变号腿（R24 Phase 1 已标
「不用」）；出场口径（钉死 v0.118）；top-frac（钉死 0.20）；权重档 >40
或负腿逐腿排列。

### 筛选线（调参双窗 = 主窗+跨窗，**两窗各自全过**；括号内为 R29 基线）

- **R30-F1**：篮子胜率 ≥ **45%**（基线 40%；+5pp 显著性税）且 > V0 篮子胜率
- **R30-F2**：篮子盈亏比 ≥ **2.6**（基线 2.4；+0.2 显著性税）
- **R30-F3**：Spearman > 0 且前后半窗同正（同基线 R29-C3）
- **R30-F4**：强档占比 ≤15%（同基线 R29-C4；A 桶离线不可算）
- **R30-F5（灵敏度）**：每条非零腿 ±50% 扰动，**R29 基线 pass_all**
  （40%/2.4/C3/C4）零翻转——翻转即参数敏感，淘汰
- pre2019 输入 CLI **硬拒绝**（终审前不许碰，代码化，同 R24/R29）

### 幸存者规则（预注册写死）

过 F1~F5 的组合按 **两窗篮子 margin（vs 全样本）的较小值**降序，取
**top 3** 进终审（不足 3 个则全取；0 个 ⇒ 判负收口）。排名键写死，
不许跑数后换键挑好看的。

### 终审（pre2019 untouched，第一次也是唯一一次读取）

幸存者逐一过 **R29 基线终审线：篮子胜率 ≥40% ∧ 盈亏比 ≥2.4 ∧ Spearman>0
且半窗同正**（C4 只作参考列）。任一幸存者全保持 ⇒ 该组合判过（带「经搜索」
L3− 注记）；全灭 ⇒ 判负。终审名单只能从 `r30_search.json` 的 survivors
读，不许临时指定（代码化）。

### 反过拟合纪律（对搜索族的特化）

1. **搜索族先写死再跑数**：本页即预注册；搜完不许「再补一档权重试试」——
   要补必须新发研究单元。
2. **显著性税**：筛选线比终审线严（45%/2.6 vs 40%/2.4）+ 灵敏度零翻转，
   双重防「贴线碰巧过」（R29 的死因）。
3. **单轮数据采集**：复用 R24 同一批 trades 离线求值，零新回测。
4. pre2019 终审前不许看（CLI 硬拒绝）。
5. 多重比较残余风险不掩饰：证据等级封顶 L3−，头部与结论均带注记。

### 研究步骤（预注册）

- **Phase 0：基建（本机，2026-09-08）**——新增
  `research/score_combo_search_study.py`：网格枚举（gcd 去重）+ 双窗筛选 +
  幸存者灵敏度 + Phase final CLI；合成数据钉测随附（含去重正确性、
  筛选线边界、pre2019 硬拒绝、终审名单机械读取）。
- **Phase 1：搜索（Windows 生产机）**——

  ```bash
  uv run python src/custos/research/score_combo_search_study.py --search \
      --from-trades artifacts/logs/score_variants_study/score_variants_study_s0_n400.rejudged.json \
                    artifacts/logs/score_variants_study/score_variants_study_s0_n1000_cw.rejudged.json
  ```

  产出 `artifacts/logs/score_combo_search_study/r30_search.json`（含
  n_combos_evaluated、过线清单、survivors top 3）。
- **Phase 2：终审（pre2019 untouched）**——

  ```bash
  uv run python src/custos/research/score_combo_search_study.py --final \
      --from-trades artifacts/logs/score_variants_study/score_variants_study_s0_n1000_pre2019.rejudged.json
  ```

  产出 `r30_final_pre2019.json`；判过 ⇒ Phase 3 落地议（同 R29 Phase 4
  注记：rsi_deep/rsi_bull_div 需升级为 live 打分腿，B 类改动 + 影子观察）；
  判负 ⇒ 如实回填，打分权重路线关闭。
- **Phase 3：落地（仅终审通过才做）**——同 R29 Phase 4 的 B 类改动路径，
  且必须带「经搜索 L3−」注记 + live 影子观察 N 个交易日再切主。

### 结果回填（跑数后写）

（待搜索/终审跑数后如实回填：n_combos_evaluated、过线组合数、survivors
读数、终审判定、结论与状态。）
