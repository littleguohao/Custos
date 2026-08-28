# R24 · 1800 打分体系严谨重建（score_calibration_study，预注册）

> **家族**：选股方向（打分重构第二轮——R22 翻车后的「最严谨路线」重来）　|
> **证据等级**：L0（预注册落档 + Phase 0 基建；跑数未开始）　|
> **状态**：🔄 预注册进行中（2026-08-28 owner 拍板「走最严谨的路线重新构建研究」；
> Phase 0 基建已完成，Phase 1-3 跑数待生产机执行）
> **依赖**：上游：R19（技术分不预测涨幅）R20（逐腿 lift 证据）R22（V2/V3 pre2019
> 翻车——本研究的直接起因）R23（共振 99.2%≈无过滤——命中率>90% 无区分度的判例）
> ｜判据：R12（预注册纪律）｜口径：R11（量级不作数）R14（幸存者宇宙）R3（半窗纪律）
> 索引与主图见 [`README.md`](README.md)。脚本
> `research/score_calibration_study.py`（逐腿 ablation，离线纯函数）、
> `research/score_variants_study.py`（判据层第四列 C3=全样本天然基准）。

## 主题

现行打分「分数整体偏高、分层多、真正优秀的股票筛不出来」三个毛病，用
预注册判据 + 反过拟合纪律从头校准——不再走 R22「事后构造变体再翻车」的老路。

## 目标

按预注册的 C1–C5 判据重建打分体系：top-frac（0.20）篮子 margin 跑赢
**无筛选全样本 margin**（天然基准），跨窗保持，且满足候选数约束；
pre2019 untouched 窗终审一票否决。

## 结论

**尚无结论——预注册进行中。** Phase 0（基建）已落地：判据层加全样本
margin 基准列（`C3_natural_vs_universe`，与既有三列 C3 并列互不覆盖）、
逐腿 ablation 工具（池内命中率 / add-one / LOO margin，离线纯函数零回测）、
本预注册文档落档。Phase 1-3（生产机跑数：逐腿证据表 → ≤4 个候选方案 →
pre2019 终审）未执行，任何结论在终审前都不得进 live。

---

## 证据与过程

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
