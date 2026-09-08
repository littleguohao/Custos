# R29 · 胜率稳定 ≥40% + 盈亏比 ≥2.4 的打分重建（score_stability_study，预注册）

> **家族**：选股方向（打分重构第三轮——R22/R24 两次死于 pre2019 后，以「跨 regime
> 稳定」为第一目标的重来）　|
> **证据等级**：L3（三窗预注册终审；vipdoc 幸存者宇宙）　|
> **状态**：❌ **证伪（2026-09-07，Phase 2）**——W1/W2/W4 窗内两窗全过但 ±50%
> 灵敏度翻转（参数敏感，不可信），W3 参数稳但主窗盈亏比 2.14 失 C2 线；
> 推荐名单为空，Phase 3 未启动（pre2019 保持 untouched），Phase 4 不启动
> **依赖**：上游：R19（技术分不预测涨幅+胜率/盈亏比互换结构）R20（赢家画像）
> R22（V2 窗内达标、pre2019 翻车）R24（逐腿三窗 ablation + 三候选同死因证伪——
> 本研究的直接证据地基）｜判据：R12（预注册纪律）｜口径：R11（量级不作数）
> R14（幸存者宇宙）R3（半窗纪律）
> 索引与主图见 [`README.md`](README.md)。脚本
> `research/score_stability_study.py`（Phase 2/3，判据机械全部复用
> `score_calibration_study`/`score_variants_study`，零口径重写）。
> 产物：`artifacts/logs/score_stability_study/r29_phase2.json`（已落）；
> `r29_phase3_pre2019.json` 未生成（推荐名单为空，终审未启动）。

## 主题

owner 目标（2026-09-07）：**top-20% 篮子胜率稳定在 40% 以上，同时通过打分把
高盈亏比（≥2.4）的股票识别出来**；手段 = 调整各子因子权重 / 增删因子（权重设 0）。
本页是该目标的预注册：判据、候选、纪律在跑数前写死。

## 目标

按预注册的 R29-C1~C4 判据重建打分体系：top-frac（0.20）篮子胜率 ≥40% 且盈亏比
≥2.4（主窗+跨窗两窗各自全过），Spearman 半窗不翻，强档占比 ≤15%，±50% 灵敏度
零翻转；**pre2019 untouched 窗终审一票否决**（终审线 = 胜率/盈亏比/判别三线的
跨 regime 保持）。口径与 R22/R24 完全对齐（v0.118 出场臂 + 25bps + J<13∧0AMV
做多基底，同一批 trades 离线求值，零新回测）。

## 结论

**❌ 证伪（Phase 2，2026-09-07）——本轮死在窗内，比 R22/R24 死得更早。**
Phase 2 四候选读数：W1/W2/W4 在主窗+跨窗 **R29-C1~C4 全部过线**（篮子胜率
43.6%~53.7%，盈亏比 2.42~2.91，Spearman 半窗同正，强档占比 ≤2.3%）——
窗内达标第三次复现「窗内指标从来不是瓶颈」；**但三者全部死于 ±50% 灵敏度**
（主窗各翻转 1~3 次：W1 的 weekly_j_low/rsi_bull_div/macd_bottom_divergence、
W2/W4 的 macd_bottom_divergence——判过本身贴着边线，权重稍动即翻，参数敏感
不可信）；W3_no_deep 灵敏度零翻转（参数稳），但主窗盈亏比 2.142 < 2.4 失
C2 线——「彻底去 rsi_deep」假说得到的答案是：去掉 regime 锚后窗内盈亏比
就不够线。推荐名单机械生成为**空**，Phase 3 按纪律未启动（CLI 硬停 exit 2，
**pre2019 窗保持 untouched**，与 R22/R24「终审窗翻车」不同，本轮连终审资格
都没拿到）。Phase 4（live 落地）不启动，任何方案不进 live。
结构含义：四腿证据核（rsi_deep/weekly_j_low/rsi_bull_div/macd_bottom_divergence）
在 40%/2.4 双线下没有参数稳健的窗内解——「向 pre2019 稳定腿倾斜」的方向
在窗内即不可行，第三轮打分重建判负。

---

## 证据与过程

### 与前两轮的本质区别（为什么这次不是简单重复）

R22 V2 与 R24 P1/P2/P3 在窗内都达到过「胜率 46~54% / 盈亏比 2.4~2.8」——
**窗内指标从来不是瓶颈**。两轮五个候选全部死于同一点：pre2019 终审窗
（2010-2016 段）Spearman 半窗翻转，死因 = 判别 edge 属近 regime 富集。
R24 Phase 1 逐腿证据给出了前两轮没用的信息（add-one margin，pp）：

| 腿 | 主窗 | 跨窗 | pre2019 | regime 特性 |
|---|---|---|---|---|
| rsi_deep_oversold | +33.0 | +37.2 | **+6.3** | 窗内最强、**pre2019 大幅萎缩**——前两轮拿它当 40% 锚，正是翻车根源 |
| weekly_j_low | +8.0 | +9.8 | +2.5 | 中等、pre2019 萎缩 |
| **macd_bottom_divergence** | +2.1 | +3.2 | **+3.4** | 弱但**三窗最稳（pre2019 不缩）** |
| **rsi_bull_div** | +3.0 | +3.1 | **+6.9** | 弱但**pre2019 反而最强** |

三窗一致为负（取负有证据）：rsi_strong（−8.8/−14.2/−5.3）、b1_ignition
（−7.7/−15.6/−3.4）、volume_contraction（−8.8/−7.6/−3.0）、
relative_strength_strong（−3.1/−3.6/−3.3）、macd_top_divergence
（−3.8/−6.7/−0.8）、ignition（−1.9/−2.3/−3.5）。

R29 的设计原则因此与前两轮**相反**：权重向「pre2019 不萎缩」的腿倾斜，
把 regime 依赖最重的 rsi_deep_oversold 降权直至归零——用窗内量级换跨
regime 生存率（R11：量级本来就不作数）。

### 校准判据（预注册，不许事后改）

**目标函数**：top-frac（0.20）篮子（口径同 R22/R24：v0.118 出场臂 +
25bps + J<13∧0AMV 做多基底，trades 一次采集离线求值，零新回测）。

**过线判据（主窗+跨窗两窗各自全过才算过）**：

- **R29-C1**：篮子胜率 ≥ **40%**（绝对线）且 > V0 篮子胜率
- **R29-C2**：篮子盈亏比 ≥ **2.4**（沿用 R22 owner 放宽线）
- **R29-C3**：变体分 vs 收益 Spearman > 0 且前后半窗同正（R22/R24 的翻车顶线）
- **R29-C4**：强档（≥60）占全样本 ≤15%（A 桶离线不可算，如实标注；⚠️ 沿用
  R24 注记：实现是全窗聚合占比，非「当日池占比」）
- 参考列（不进判定）：C3★ 篮子 margin vs 全样本 margin + Wilson 注记
  （与 R22/R24 产物可比）
- **灵敏度**：入选方案每腿权重 ±50% 扰动（负腿同），pass_all（C1∧C2∧C3∧C4）
  不得翻转——翻转即「参数敏感，不可信」
- **终审（一票否决）**：调参只用主窗+跨窗（CLI 对 pre2019 输入硬拒绝，代码化）；
  **pre2019 untouched 终审线 = R29-C1 ∧ C2 ∧ C3 同时保持**（比 R24 终审线
  「C1+C3★」更严——owner 要的是胜率**稳定**≥40%，终审窗必须同样过 40%/2.4；
  C4 候选数约束不进终审线）

**边界沿用**：R11（量级不作数，只作相对排序）R14（幸存者宇宙）R3（半窗纪律）。

### 候选方案（≤4 个，简单整数权重，跑数前写死）

全部为**证据重构形态**（现行腿全部归零，只用 panel 证据腿从零搭；
contrib_mult={} 语义同 R24 P1）：

| 候选 | 权重 | 设计依据 |
|---|---|---|
| **W1_balanced** | rsi_deep 25 / weekly_j_low 25 / rsi_bull_div 25 / macd_bottom_divergence 25 | R22 V2 形态的等权版：rsi_deep 占比 40%→25%，检验「降 regime 锚」是否够 |
| **W2_pre2019_tilt** | rsi_bull_div 30 / macd_bottom_divergence 30 / weekly_j_low 20 / rsi_deep 20 | 向 pre2019 最强的两条腿倾斜；rsi_deep 降到 20% |
| **W3_no_deep** | rsi_bull_div 40 / macd_bottom_divergence 30 / weekly_j_low 30 | **彻底去掉 regime 依赖腿**：直接检验「翻车全部来自 rsi_deep」假说 |
| **W4_tilt_neg** | W2 + 六条三窗负腿各 −5（rsi_strong/b1_ignition/volume_contraction/relative_strength_strong/macd_top_divergence/ignition） | V3 式负向证据加在稳态核上（R24：负腿三窗一致负，取负有证据） |

⚠️ 不再单设「V2 复跑」臂：P1_rebuild（R24）= V2 形态已终审证伪，V0 对照臂
判据层自带；leader_volume 证据互斥（add-one 正 / LOO 三窗皆负）且 P3 已随
R24 证伪，本轮不用。

### 反过拟合纪律（沿用 R24，条款不变）

1. **判据先写死再跑数**：本文件即预注册文档；任何判据调整必须新发版本并注明。
2. **变体 ≤4 个**，权重简单整数；不做网格搜索。
3. **灵敏度扫描**：±50% 扰动 pass_all 不得翻转。
4. **单轮数据采集**：复用 R24 同一批 trades（含 factor_contrib + panel）
   离线求值，零新回测。
5. pre2019 窗在终审前**不许看**（CLI 硬拒绝代码化）。
6. R12 教训常驻：窗内达标 ≠ 终审通过；终审翻车 = 如实判负也是合格产出。

### 研究步骤（预注册）

- **Phase 0：基建（本机，已完成 2026-09-07）**——新增
  `research/score_stability_study.py`：R29 四候选 + R29-C1~C4 判据 +
  ±50% 灵敏度 + Phase 2/3 CLI（pre2019 硬拒绝/只接受，镜像 R24；
  终审名单从 Phase 2 落盘的 recommended 机械读取，不许临时指定）；
  判据机械全部复用 `score_variants_study.judge` /
  `score_calibration_study` 的现成函数，零口径重写；合成数据钉测 29 个随附。
- **Phase 1：不重跑**——逐腿证据直接引用 R24 Phase 1 三窗 ablation
  （本页顶部表）；候选设计已完成（上表），跑数前不再看任何候选级结果。
- **Phase 2：跑数（Windows 生产机）**——主窗+跨窗离线评估 + 灵敏度：

  ```bash
  uv run python src/custos/research/score_stability_study.py --phase2 \
      --from-trades artifacts/logs/score_variants_study/score_variants_study_s0_n400.rejudged.json \
                    artifacts/logs/score_variants_study/score_variants_study_s0_n1000_cw.rejudged.json
  ```

  产出 `artifacts/logs/score_stability_study/r29_phase2.json`（含推荐名单——
  机械生成：两窗全过且参数不敏感者进终审）。
- **Phase 3：终审（pre2019 untouched，第一次也是唯一一次读取）**——

  ```bash
  uv run python src/custos/research/score_stability_study.py --phase3 \
      --from-trades artifacts/logs/score_variants_study/score_variants_study_s0_n1000_pre2019.rejudged.json
  ```

  终审线 R29-C1∧C2∧C3：通过 ⇒ Phase 4；翻车 ⇒ 如实判负回填本页
  （与 R22/R24 同款结局也是合格产出）。
- **Phase 4：落地（仅终审通过才做）**——⚠️ 候选腿 rsi_deep_oversold /
  rsi_bull_div 目前**不是 live 打分腿**（只是信号标注/研究 panel 腿），落地
  需在 `score_candidates` 增段 + `SCREEN_FORMULA_REGISTRY.json` 加键 +
  钉测三处同步（B 类改动），权重走 `scoring.weights` 覆盖；live 侧影子观察
  N 个交易日再切主。

### 结果回填（2026-09-07 跑数后写）

**Phase 2（`r29_phase2.json`；主窗 15636 笔 / 跨窗 14687 笔）**——候选×窗口
判据表（篮子 = top-20%；margin 单位 pp，对照 = 全样本 margin 主窗 +6.9 /
跨窗 +14.0）：

| 候选 | 窗口 | C1 | C2 | C3 | C4 | 篮子胜率/盈亏比/margin | Spearman |
|---|---|---|---|---|---|---|---|
| W1_balanced | 主窗 | ✓ | ✓ | ✓ | ✓ 1.96% | 46.2% / 2.42 / +16.9 | 0.087（半窗同正） |
| W1_balanced | 跨窗 | ✓ | ✓ | ✓ | ✓ 1.93% | 53.7% / 2.77 / +27.2 | 0.130（半窗同正） |
| W2_pre2019_tilt | 主窗 | ✓ | ✓ | ✓ | ✓ 2.25% | 43.6% / 2.54 / +15.4 | 0.082（半窗同正） |
| W2_pre2019_tilt | 跨窗 | ✓ | ✓ | ✓ | ✓ 2.29% | 52.0% / 2.91 / +26.5 | 0.123（半窗同正） |
| W3_no_deep | 主窗 | ✓ | **✗** | ✓ | ✓ 9.15% | 44.4% / **2.14** / +12.6 | 0.073（半窗同正） |
| W3_no_deep | 跨窗 | ✓ | ✓ | ✓ | ✓ 10.23% | 50.3% / 2.42 / +21.1 | 0.110（半窗同正） |
| W4_tilt_neg | 主窗 | ✓ | ✓ | ✓ | ✓ 2.16% | 44.7% / 2.51 / +16.2 | 0.084（半窗同正） |
| W4_tilt_neg | 跨窗 | ✓ | ✓ | ✓ | ✓ 2.17% | 52.5% / 2.88 / +26.8 | 0.125（半窗同正） |

**±50% 灵敏度（判定对象 = pass_all = C1∧C2∧C3∧C4）**：

| 候选 | 扰动×窗 | 翻转次数 | 翻转明细（全部落在主窗） | 判定 |
|---|---|---|---|---|
| W1 | 8×2 | **3** | weekly_j_low→37.5、rsi_bull_div→37.5、macd_bottom_divergence→12.5 | ⚠️ 参数敏感 |
| W2 | 8×2 | **1** | macd_bottom_divergence→15.0 | ⚠️ 参数敏感 |
| W3 | 6×2 | 0 | — | 稳（但基线主窗 C2 本就不过） |
| W4 | 20×2 | **2** | macd_bottom_divergence→15.0、weekly_j_low→30.0 | ⚠️ 参数敏感 |

翻转共性：全部发生在**主窗**、全部由 macd_bottom_divergence 减权或
weekly_j_low/rsi_bull_div 加权触发——主窗判过本身就贴着边线（W2 主窗胜率
43.6% 仅高于 40% 线 3.6pp），权重 ±50% 即翻出线下。

**推荐名单（机械生成）= 空**（`recommended: []`）：W1/W2/W4 参数敏感、
W3 主窗 C2 失线，无一满足「两窗全过且参数不敏感」。

**Phase 3 终审：未启动。** 推荐名单为空 ⇒ CLI 硬停（exit 2，「无可终审方案，
如实上报」），`r29_phase3_pre2019.json` 未生成，**pre2019 窗保持 untouched**
（与前两轮「终审窗翻车」的结局不同：本轮四候选连终审资格都没拿到，死在
窗内的参数稳定性上——40%/2.4 双线下证据四腿没有稳健解）。

**最终判定：❌ 证伪（Phase 2）。** Phase 4（live 落地）不启动，任何权重
不进 live。第三轮打分重建与前两轮合计：六候选（V2/P1/P2/P3 死于 pre2019
终审，W1-W4 死于窗内）无一幸存——「调权重/换骨架把篮子胜率稳定推上 40%
且盈亏比 ≥2.4」这条路线在现有证据腿上判负。
