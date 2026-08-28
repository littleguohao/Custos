# R24 · 1800 打分体系严谨重建（score_calibration_study，预注册）

> **家族**：选股方向（打分重构第二轮——R22 翻车后的「最严谨路线」重来）　|
> **证据等级**：L3（三窗预注册终审；vipdoc 幸存者宇宙）　|
> **状态**：❌ **证伪（2026-08-28，Phase 3 终审）**——P1/P2/P3 三候选在 pre2019
> untouched 窗 C1 全部半窗翻转（判别 edge 属近 regime 富集，与 R22 同死因）；
> 按预注册退路退回「分位数分层」止血方案。Phase 4（live 落地）不启动
> **依赖**：上游：R19（技术分不预测涨幅）R20（逐腿 lift 证据）R22（V2/V3 pre2019
> 翻车——本研究的直接起因，终审同点翻车）R23（共振 99.2%≈无过滤——命中率>90%
> 无区分度的判例）｜判据：R12（预注册纪律）｜口径：R11（量级不作数）R14
> （幸存者宇宙）R3（半窗纪律）
> 索引与主图见 [`README.md`](README.md)。脚本
> `research/score_calibration_study.py`（ablation + Phase 2/3）、
> `research/score_variants_study.py`（判据层 C3★=全样本天然基准）。
> 产物：`artifacts/logs/score_variants_study/*.ablation.json`（Phase 1）、
> `phase2_主窗_跨窗.json`（Phase 2）、`phase3_pre2019.json`（Phase 3 终审）。

## 主题

现行打分「分数整体偏高、分层多、真正优秀的股票筛不出来」三个毛病，用
预注册判据 + 反过拟合纪律从头校准——不再走 R22「事后构造变体再翻车」的老路。

## 目标

按预注册的 C1–C5 判据重建打分体系：top-frac（0.20）篮子 margin 跑赢
**无筛选全样本 margin**（天然基准），跨窗保持，且满足候选数约束；
pre2019 untouched 窗终审一票否决。

## 结论

**❌ 证伪（Phase 3 终审，2026-08-28）。** Phase 1 逐腿证据查明结构（j_low
100% 命中 +24 保底 = 地板效应主因；rsi_deep_oversold/weekly_j_low/rsi_bull_div/
macd_bottom_divergence add-one margin 三窗一致正；11 条腿三窗一致负）；
Phase 2 构造的 4 个候选里 P1/P2/P3 在主窗+跨窗四判据（C1/C2/C3★/C5）全过、
±50% 灵敏度零翻转，P0「只去地板/去负腿」两窗全灭（证明修权重无效、必须换
骨架）；**但 Phase 3 终审三候选在 pre2019 全部 C1 半窗翻转**（P1 −0.018/+0.090、
P2 −0.015/+0.093、P3 −0.009/+0.082——2010-2016 段为负），终审线一票否决。
C3★（篮子 margin 领先全样本）在 pre2019 其实保持（+5.7~+8.6pp vs +2.1pp），
但量级缩到近窗的 1/3 且相关性方向不稳——判别 edge 属近 regime 富集，
**与 R22 同一死因、同一窗口段（2010-2016）**，预注册纪律判负。
按既定退路退回**分位数分层**止血方案（强=当日池 top15%，治标解决偏高/分层多）；
Phase 4（权重回流 live）不启动，任何方案不进 live。

---

## 证据与过程

### Phase 3 终审（2026-08-28，`phase3_pre2019.json`，14068 笔）

| 方案 | C1 | C2 | C3★ | C5 | 篮子胜率/盈亏比/margin vs 全样本 | 判定 |
|---|---|---|---|---|---|---|
| P1_rebuild | ✗ 翻（−0.018/+0.090） | ✓ | ✓ | ✓ 2.7% | 39.4%/1.97/+5.7pp vs +2.1pp | ❌ |
| P2_rebuild_neg | ✗ 翻（−0.015/+0.093） | ✓ | ✓ | ✓ 2.3% | 41.1%/1.99/+7.7pp vs +2.1pp | ❌ |
| P3_rebuild_leader | ✗ 翻（−0.009/+0.082） | ✓ | ✓ | ✓ 4.9% | 40.9%/2.09/+8.6pp vs +2.1pp | ❌ |

三窗并排（篮子 margin pp vs 全样本 pp / Spearman）：

| 方案 | 主窗 | 跨窗 | pre2019 |
|---|---|---|---|
| P1 | +17.2/+6.9, 0.091 | +27.2/+14.0, 0.136 | +5.7/+2.1, 0.037（半窗翻） |
| P2 | +17.5/+6.9, 0.093 | +27.5/+14.0, 0.138 | +7.7/+2.1, 0.040（半窗翻） |
| P3 | +19.4/+6.9, 0.088 | +29.2/+14.0, 0.154 | +8.6/+2.1, 0.034（半窗翻） |

### Phase 1-2（2026-08-28，证据留痕）

- **Phase 1（三窗 ablation）**：j_low 池内命中率 100%（⚠️ 无区分度，+24 保底
  = 地板效应主因）；add-one margin 三窗一致正 = rsi_deep_oversold
  （+33/+37/+6.3）、weekly_j_low（+8/+9.8/+2.5）、macd_bottom_divergence
  （+2.1/+3.2/+3.4）、rsi_bull_div（+3.0/+3.1/+6.9）；三窗一致负 =
  rsi_strong/b1_ignition/volume_contraction/relative_strength_strong/
  macd_top_divergence/ignition；zhixing 系/pullback_shrink/platform_pullback_b1
  等 pre2019 变号（不用）。
- **Phase 2（调参只用主窗+跨窗，pre2019 硬拒绝代码化）**：P0（最小改动：
  j_low 归零 + 负腿归零）两窗 C1/C2/C3★ 全灭——**只去地板救不了现行分**；
  P1/P2/P3 四判据全过、±50% 灵敏度零翻转进终审；P3（+leader_volume 20）
  最强（margin +19.4/+29.2pp）但终审同样翻车。

### 要解决的真问题（owner 原话）

1. **分数整体偏高**——机械成因已查明：① 地板效应（主池 J<13 硬门槛 ⇒
   j_low +24 每票自带保底分）；② 正偏结构（正腿 26 条 clamp 到 100，负腿仅
   4 条）；③ 60/30 绝对阈值追不上权重膨胀（v0.58–0.64 连续加腿）。
2. **分层多、候选多**——强档多 ⇒ RESONANCE_MATRIX 把 (强,中)→B、(强,强)→A，
   A/B 桶膨胀。
3. **真正优秀的股票筛不出来**——R19 已证技术分不预测涨幅；R22 实测 V0 篮子
   margin 仅 +1~+3pp（≈ 无筛选）。

### 校准目标（预注册，不许事后改）

**目标函数**：top-frac（0.20）篮子 margin **vs 无筛选全样本 margin**（天然
基准，2026-08-28 owner 定义：无因子影响的胜率/盈亏比组合；margin = 胜率 −
盈亏平衡胜率 1/(1+盈亏比)，复用 m2 口径）。

**过线判据**（C 系列，全部满足才算过）：

- **C1**：变体分 vs 收益 Spearman > 0 且前后半窗同正（沿用 R22）
- **C2**：TOP20% 赢家组变体均分 > bottom-80% 均分（沿用 R22）
- **C3★**：变体篮子 margin > **全样本 margin**，且与全样本胜率的 Wilson 95%
  区间**不重叠**（显著）
- **C4**：跨窗（2022-2024，s1000）C1 符号保持 + C3★ 保持
- **C5（新增，候选数约束）**：强档占当日池 ≤15%、A 桶 ≤5%（上限可调，
  owner 批准时可改——只许在跑数前改）
- **终审**：调参只用主窗+跨窗；**pre2019 为 untouched 验证窗**，C1 不翻转
  且 C3★ 保持才判通过（R22 翻车点，一票否决）

**边界沿用**：R11（量级不作数，只作相对排序）R14（幸存者宇宙）R3（半窗纪律）。

### 反过拟合纪律（V2/V3 翻车教训的直接回应）

1. **判据先写死再跑数**：本文件即预注册文档；任何判据调整必须新发版本并注明。
2. **变体 ≤4 个**，权重简单整数；网格搜索维度 ≤3，每维 ≤4 档。
3. **灵敏度扫描**：入选方案的每条腿权重 ±50% 扰动，C3★ 不得翻转——翻转即
   标注「参数敏感，不可信」。
4. **单轮数据采集**：trades（含 factor_contrib + panel）一次算好落盘，所有
   变体/消融**离线求值**（v0.118 出场口径：stop12 + 保本 0.05 + 分批 0.5 +
   BBI 连破 2 + 25bps，owner 已钉）。
5. pre2019 窗在终审前**不许看**（防调参污染）。

### 研究步骤（预注册）

- **Phase 0：基建扩展（本机，已完成 2026-08-28）**——`judge()` 加全样本
  margin 基准列（`universe_margin` / `C3_natural_vs_universe` / 与全样本胜率
  的 Wilson 95% 重叠注记，与既有三列 C3 **并列第四列、互不覆盖**；
  criteria_provenance 补出处）；新增逐腿边际分析
  `research/score_calibration_study.py`（池内命中率 >90% 标无区分度 /
  add-one 边际 = 命中子集 margin − 全样本 margin / LOO 边际 = V0 去腿后
  top-20% 篮子 margin 变化），CLI `--ablation --from-trades` 逐窗口出证据表。
- **Phase 1：跑数（Windows 生产机，待执行）**——复用
  `artifacts/logs/score_variants_study/*.rejudged.json`（主窗/跨窗/pre2019
  三份已含 trades + contrib）`--from-trades` 离线跑 ablation，零新回测；
  产物缺的腿（panel 键）用 winner_factor_study 产物补齐。产出：逐腿证据表
  （三窗 × 每腿 × {池内命中率, add-one margin, LOO margin}）。

  **执行命令**（在生产机仓库根目录跑；输入文件名以
  `artifacts/logs/score_variants_study/` 下实际产物为准，先 `ls` 核对）：

  ```bash
  # ① 逐腿 ablation（三窗一把跑；每份输入旁落 <文件>.ablation.json，
  #    stdout 打印「腿 × {命中率, add-one, LOO}」证据表）
  uv run python src/custos/research/score_calibration_study.py --ablation \
      --from-trades artifacts/logs/score_variants_study/score_variants_study_s0_n400.rejudged.json \
      artifacts/logs/score_variants_study/score_variants_study_s0_n1000_cw.rejudged.json \
      artifacts/logs/score_variants_study/score_variants_study_s0_n1000_pre2019.rejudged.json

  # ② 若产物缺 panel 腿（rsi_strong/platform_pullback_b1 等 contrib 没有的），
  #    用 winner_factor_study 的产物（含 panel 键）补跑同命令补证据
  ls artifacts/logs/winner_factor_study/
  ```

  ⚠️ pre2019 那份的证据表**只读、不调参**（反过拟合纪律第 5 条）。
- **Phase 2：构造候选方案（≤4 个，基于 Phase 1 证据，不是拍脑袋）**——
  池内命中率 >90% 的腿**归零或移除**（j_low 预期首当其冲——地板效应消除）；
  add-one margin 三窗一致为正的腿保留/加权；LOO 显示负贡献的腿移除或取负；
  候选数约束（C5）内调阈值；输出 ≤4 个方案（权重简单整数），灵敏度 ±50% 扫描。
- **Phase 3：终审（pre2019 untouched）**——通过 ⇒ Phase 4；翻转 ⇒ 如实判负
  （R22 同款结局也是合格产出），落 R24 证伪 + 退回「分位数分层」止血方案
  （强=当日池 top15%，治标但解决偏高/分层多）。
- **Phase 4：落地（仅终审通过才做）**——权重走
  `SCREEN_FORMULA_REGISTRY.json` 的 `scoring.weights` 覆盖（v0.84 已外置，
  零代码改动）；阈值同理；落档 + TODO #61 收口 + CHANGELOG；live 侧影子观察
  N 个交易日再切主。
