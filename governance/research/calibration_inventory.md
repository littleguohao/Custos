# 信号参数定义校准清单（owner 2026-11-03）

> **口径**：本轮只做 **A 类「定义校准」**——核对每个检测器是否检测出规则原文说的那个形态（对照 owner 标注样本 + 抽检图），
> **不拿收益寻优**；B 类「效果校准」（哪个参数值赚钱）等 A 全部做完再议。**live 用到的先做**（P0 → P1 → P2）。
> **在生产机运行**（真实全量数据 + owner 标注）；开发机只做工具与合成钉测。
> 清单由 `scripts/dev/calibration_inventory.py` 生成；「定义校准」列人工维护（未校准 / 进行中 / ✅ commit xxx），
> `--check` 钉测要求 P0/P1 参数全部在册（新增参数不登记即红）。

## 现状

- **P0H 持仓大形态 18 个**（17 个判定参数 + `QFQ_LOAD_BARS` 加载深度，见下节）；另 233 个模块级数值参数：**P0 107 个**（live 判定模块优先，含 4 个只在 calib_probe 对照的 v2 候选，9 个模块）、
  **P1 live 展示 99 个**（1800 证据标注，不进判定）、P2 研究 67 个。
- **定义校准：0 / 233**。现有研究（R2/R6/R19/R31/R42 等）测的都是**效果**，没有一个检测器做过「是否测对了形态」的核对。
- **清单未覆盖**（需在逐因子校准时一并核）：①函数体内联常量（KDJ 9/3/3、BBI 周期、20 日相对强度窗口等）；
  ②打分权重与分档（`DEFAULT_TECH_WEIGHTS`、`tech_strong/mid` 60/30、资金意图 `ci_*` 与 `CAP_STRONG/MID`——属打分，R35 终局 A 已收口，
  不在 A 类范围，只在因子定义改动时同步核对）；③出场规则（`EXIT_RULES.json`，R10/R44/R47 测过效果）与 0AMV 状态机（R4），另议。

## 校准循环与版本（owner 2026-11-03 定）

1. 选定一个因子；owner 看它在**最近几个月 1800 选股结果**里的命中名单，指出「不该命中」与「漏了」，**每条写成形态理由**
   （如「DIF 离零轴还远，不算小绿」），不写「后来涨了 / 跌了」——防止把 holdout 结果带进定义。
2. coder 按理由改定义，出 v(n+1) 候选：新旧命中差异清单 + 挖掘段 `calib_probe` 抽检图，owner 确认；不满意再迭代，确认后才升版。
3. 升版：`scripts/dev/factor_versions.py --bump <因子> --reason ...`（版本表 `governance/contracts/FACTOR_VERSIONS.json`，
   整数版本，每升一次 = 判定语义变一次）；同时 `--holdout-use <因子> --window <用到的 1800 日期区间>` 登记——该因子在该窗口内的
   holdout 读数不再是独立证据（holdout 月报自动带上此表）。CHANGELOG 列受影响 R。
4. 追踪：stock_pool / 研究 provenance / holdout 月报都落当时的 `factor_versions`；R 可按版本各跑一遍对比效果。
5. **持续进化，不设「改不改」门槛**（owner 2026-11-09）：不预设误中率 / 漏中数阈值——每条标「错」的样本都进 v(n+1) 候选讨论，改不改、怎么改由 owner 看 `ingest.md` 逐条定；同一因子可以多轮迭代、版本号持续上升。约束只有两条不变：改动写成形态理由、每次语义变化都升版并登记（holdout 使用照常登记）。
6. **先评 owner 判断正确率，再决定调整**（owner 2026-11-13）：owner 在每条样本写判断（对 / 错 / 不确定）+ 形态分析；ingest 之后、改任何定义之前，先对 owner 的判断本身做正确率分析，结果出来再定改不改。评估口径**在读到标注之前写死**（待 owner 定，写入本条后才读 labels.csv）；
   **口径定稿（2026-11-13，读 b1_structure__mining_v1 标注前写入；该文件 sha256 前 16 位 `02cddf9faa3fb8a2`；coder 建议 A+B+D，owner 未另指定即按此执行，B 步实施前 owner 可修订）**：
   - **A 对照原文**：逐条把 owner 判定与规则原文（`governance/strategy/**`、检测器 docstring 所引出处）对照，分「与原文一致 / 与原文不一致 / 原文有歧义判不了」三类报比例；不一致的逐条列出，由 owner 复核是判断错还是原文 / 转译错。
   - **B 前后一致性**：每模块随机抽 20%（seed 0，至少 10 条）打乱顺序、隐去原判定与备注，owner 隔 ≥2 天盲标，报一致率与 Cohen κ；不一致样本列出。
   - **D 自洽**：①判定与备注语义是否矛盾（判「对」但备注写不该算 / 反之）；②同字段同类别里备注描述同一形态而判定相反的样本对。逐条列出，不打分。
   - **C 后续走势**：只作描述性副读数，不进正确率；只读挖掘段，holdout 样本不报。
   - 正确率只描述、不设门槛；分析出来后由 owner 决定改不改定义。
   **2026-11-13 补记（owner 澄清）**：b1_structure__mining_v1 这批的「对 / 错」是 owner 对**买点**的判断，不是对检测器定义的判断 ⇒ 不进 A 类正确率；原件另存 `labels_owner_buypoint_20261113.csv`，作选股练习素材。
   **买点判断复盘口径（描述性，算之前写入）**：入场价 = 当根收盘；看之后 5 / 10 / 20 个交易日收盘涨跌、20 日内最大涨幅（最高价）与最大回撤（最低价）；前视窗越过所在挖掘段末（A 段 2016-12-31、B 段 2026-09-04）的样本不计。
   分「对 / 错 / 不确定」三组报均值、中位与 20 日上涨比例；「准确率」= 判「对」中 20 日收盘上涨的比例 与 判「错」中 20 日收盘不涨的比例，二者各报、再报合并；附同批全部样本的基准上涨比例。只描述，不作判据，不用于改检测器。
   **🔄 补充（2026-11-13，owner 指正）**：上面「只看对 / 错」的复盘不够——owner 的「错」多是「需满足条件 / 等企稳再买」，须**结合批语**评。批语复盘口径（算之前写入，描述性）：
   - **归类**（按批语关键词，一条可多类）：①状态陈述（绿柱增大 / 刚死叉 / 离 0 轴远 / 放量下跌·阴量大于阳量 / 底背离）②等待条件（等绿柱缩短 / 等双线回 0 轴附近 / 等底分型 / 等企稳）③可以试（可以试试 / 可以买）。
   - **状态陈述 ⇒ 当根核实**：绿柱增大 = MACD 柱 <0 且比前一根更负；刚死叉 = 近 3 根内 DIF 下穿 DEA；离 0 轴远 = |DIF| > 收盘价 1%；放量下跌 = 近 5 根有阴线跌 >3% 且量 > 前 5 日均量 1.5 倍，阴量大于阳量 = 近 20 根阴线量和 > 阳线量和 1.5 倍；底背离 = macd_technics 底背离检测器当根命中。报每类「批语说法与指标一致」的比例。
   - **等待条件 ⇒ 前向触发**：绿柱缩短 = MACD 柱 <0 且比前一根缩短；回 0 轴附近 = |DIF| 与 |DEA| 都 ≤ 收盘价 1%；底分型 = 前一根最低价低于其前后两根（当根确认）；企稳 = 连续 3 根不创 10 日新低。从次日起 20 根内找首个触发日，报触发率；触发日收盘买入 vs 当根立即买入，各看之后 20 日收盘涨跌、20 日最大涨幅 / 回撤（同一批样本配对比较）。
   - **可以试 ⇒** 当根买入 20 日表现，对照同批全部样本。前视窗越过挖掘段末不计；只描述。
   **修订（2026-11-13，owner 指正，重算前写入）**：「离 0 轴远 / 回 0 轴附近」原口径按收盘价 1% 是**错的**（owner 指的是 MACD 副图里双线相对 0 轴的远近，与价格无关）。改为：以当根（触发判定时以该日）往前 120 根（含）内 max(|DIF|, |DEA|) 为摆幅 A；离 0 轴远 = |DIF| > 0.2A；回 0 轴附近 = |DIF| ≤ 0.2A 且 |DEA| ≤ 0.2A。N=120、k=20% 由 coder 试用，owner 看结果再定。
   **语义澄清（owner 2026-11-13）**：等待条件 = 「现在不买；窗口内条件出现再考虑，不出现就不买」⇒ 未触发样本按「放弃」计，评价看放弃是否躲开了亏损（当根买 20 日 ≤0 记为放弃正确）。
   **修订（2026-11-17，owner 指正：只看 20 日后涨跌不科学，会漏掉「先涨后跌但按规则早已止盈」的单）**：买点练习复盘的主读数改为**按现行出场规则模拟交易**——
   `backtest_factors.simulate_b1_trade` + `plan_rules_replay.LIVE_PARAMS`（与 R47 参照格同：pct 止损、BBI 连破 2 根止盈、分批 0.5），收盘进场、成本 25bp，
   K 线截到所在挖掘段末（不读段外）；报每组已实现收益均值 / 中位、胜率、盈亏比、margin，段末仍持有的单单列不计。20 日涨跌与最大涨跌保留为副读数。
   准确率改为：买 → 交易盈利；不买 → 若买入会亏损。只描述，不作判据。
   **工具化（v0.517）**：以上口径由 `research/remark_rules.py`（单一来源）+ `buypoint_practice --mode review` 执行；买点练习出题用 `--mode make`（只抽挖掘段、前向 20 日不出段）。备注仍只写形态、不写后来涨跌（第 1 条）。
   **交互修订（v0.523，owner 2026-11-14）**：新练习每题选择「买 / 不买 / 等」后立即在该图展示同挖掘段内后 20 根，其他题仍盲态；刷新后已选择题保持揭示。导出 labels.csv 留存，**不再要求跑 review**；`--mode review` 仅为既有练习保留兼容。

**命令（生产机）**：

```bash
# ① 近月 1800 选股池的命中 / 近似命中名单（全部列出，不抽样）+ 抽检图 + 标注表
uv run python -m custos.research calib_probe --module b1_structure --mode review --since 2026-09-05 --until 2026-11-03 --workers 8 --tag v1
#    → artifacts/reports/calibration/b1_structure__v1/{gallery.html, labels.csv, summary.json}
#    labels.csv：「应命中(Y/N)」+「形态理由」由 owner 填；近似命中 = 未命中但任一参数 ±20% 即翻成命中
# ② coder 改定义后，同批日期出新旧差异（新增 / 消失 / 取值变）
uv run python -m custos.research calib_probe --module b1_structure --mode review --since 2026-09-05 --until 2026-11-03 --workers 8 --tag v2cand \
  --diff artifacts/reports/calibration/b1_structure__v1/summary.json
# ①′ 标注：直接在 gallery.html 每图下方点选 + 写形态理由（浏览器本地存档，可分次标；顶部「导出 labels.csv」）
#     旧产物加界面（不重算）：calib_probe --module <模块> --mode ui --dir artifacts/reports/calibration/<模块>__<tag>
# ①‴ 终端标注（v0.503，命令行逐条画图 + 按键 1/2/3 或 y/n/?，每条即写回 labels.csv，q 退出、再跑续标）：
#     uv run python -m custos.research calib_probe --module <模块> --mode label --dir artifacts/reports/calibration/<模块>__<tag>
# ①″ 汇总：把导出的 labels.csv 放回产物目录（或 --labels 指定），出 ingest.md（逐字段对/错、误中/漏中清单、参数改向票）
uv run python -m custos.research calib_probe --module b1_structure --mode ingest --dir artifacts/reports/calibration/b1_structure__v1
# ③ 挖掘段确认新定义（sample 模式），owner 确认后升版 + 登记 holdout 使用
uv run python scripts/dev/factor_versions.py --bump b1_structure --reason "..."
uv run python scripts/dev/factor_versions.py --holdout-use b1_structure --window 2026-09-05~2026-11-03 --reason "1800 命中名单标注校准"
```

## 校准方法（每个因子一轮，生产机执行）

1. **对照原文**：列出规则出处（`governance/strategy/**`）与代码转译，标出转译时做过的取舍（如 `FIVE_DAY_SPIKE_RATIO` 注释里的「前一交易日均量」歧义）。
2. **抽检图**：全量宇宙随机抽命中 / 未命中各 N 例（含边界附近：参数 ±20% 处翻转的样本），出 K 线 + 指标图，owner 判对错。
3. **读数**：检测器 vs owner 标注的精度 / 召回；敏感性扫描（参数逐个 ±10%/±25%/±50%，看命中数与命中集合变化——**只描述，不挑最优**）。
4. **结论**：定义没问题 ⇒ 标 ✅；定义有偏差 ⇒ 改代码（改判定语义 = 语义变更，CHANGELOG 记版本、按下文登记受影响 R）。
5. owner 待确认的定义问题在该轮一起定（例：**MACD「小绿」分三类：大绿后小绿 / 大红后小绿 / 小红后小绿**，随 `macd_technics` 一轮确认）。

## 工具（v0.479，`research/calib_probe.py`，生产机运行）

```bash
# 抽检图 + 标注表（命中 / 未命中 / 边界各 N 例；边界 = 任一参数 ×0.8 或 ×1.2 后判定翻转）
uv run python -m custos.research calib_probe --module <模块> --mode sample --universe-sample 1000 --workers 8 --tag v1
# 敏感性（逐参数 ×0.5/0.75/0.9/1.1/1.25/1.5，报命中数与 Jaccard；只描述不挑最优）
uv run python -m custos.research calib_probe --module <模块> --mode sensitivity --universe-sample 1000 --workers 8 --tag v1
```

- 产物：`artifacts/reports/calibration/<模块>__<tag>/`——`gallery.html`（纯 SVG：K 线 + 量 + J + MACD，**只画到 as-of 当根**）、
  `labels.csv`（owner 填「对 / 错 / 不确定」，回传后按 id 汇总精度 / 召回）、`sensitivity.md`、`summary.json`。
- bar 池：挖掘 A ∪ B 段内随机 bar（判定段 / holdout 不抽）；`--pool jlow`（默认，只取日 J<13，即 live 进池门之后）/
  `--pool all`（**校准 J 门槛本身必须用 all**——jlow 池里放宽 J 不会多出命中）。
- 参数改动覆盖：定义模块常量 + 所有 `from x import NAME` 绑定 + 源码默认值写该常量名的函数默认参数 + 抄进默认权重表的别名
  （如 `capital_intent.CAP_STRONG` → `DEFAULT_EVIDENCE_WEIGHTS`）。**函数体内联常量、import 时派生的常量改不到**——
  报告 `no_effect`（全部步长零变化）须人工确认是不敏感还是没改到。开发机快照复核（2026-11-06，×0.1/×10 极端步长）：
  `REVERSAL_CHANGE_PCT` 改不到（import 时派生 MIN/MAX，应改 `REVERSAL_CHANGE_MIN/MAX_PCT`）；`REVERSAL_AMPLITUDE_PCT` 等 11 个
  极端步长下有反应（只是 ±20% 不敏感）；`DIST_LONG_BEAR_FRAC` 在 jlow 池仍零变化（出货②腿另需巨量，distribution 轮用 `--pool all` 单看）。
- 边界样本的「边界翻转参数」带方向（v0.502，如 `J_LOW_THRESHOLD×1.2`）：ingest 据此出「参数改向票」——错样本能被它修正 / 对样本会被它误伤。
- live 的资金意图阈值可被 `SCREEN_FORMULA_REGISTRY.json` `scoring.weights` 覆盖，校准时以 registry 实际生效值为准。

## 轮次记录

### b1_thresholds（2026-11-14）

- **周 J 退出 A 类定义校准（owner 拍板）**：定义固定为周线 KDJ（9,3,3）的 `J < 13`，纯数值比较，不需人工判断；`calib_probe` 的 b1_thresholds 只再抽日 J、反转 K、极致缩量三项。live 判定不变。
- `b1_thresholds__mining_v1` 里 36 条周 J 标注只留作历史，不进 owner 判断正确率、参数改向票或 v2 讨论，不重标、不盲标；无需为周 J 增补周线图。

### entry_patterns / distribution（2026-11-14）

- **BBI 与相对强度退出 A 类人工校准（owner 拍板）**：BBI=(MA3+MA6+MA12+MA24)/4，当根收盘 >= BBI 即命中；相对强度 = 个股近 20 日涨幅 − 上证近 20 日涨幅，>=+3pp 命中。两者均纯计算，live 不变；`calib_probe` 删除 entry_patterns 抽检入口，旧图集只留档。
- 相对上证强度是 2026-07-23 选股打分链的工程规则；B1 有「明显强于大盘 / 所属主题」的定性思想，但没有「20 日 / 上证 / +3pp」这组明文参数。阈值有效性若需验证，另走效果研究，不做看图定义确认。
- **出货撤销名单退出普通 hit/miss 抽检**：`distribution_confirm.revoked` 是①/②的撤销名单，空名单 = 无撤销或不适用，不是漏判；mining_v1 的 12 条「-」不再要求标注。distribution 只抽 `risk_level`，提示补齐 none/watch/high 含义。
- 出货显示复算（描述性）：48 条显示值与重算 0 错位；36 条 risk_level 样本中 18 个 watch，其中 17 条来自③阶梯放量下跌、1 条来自⑤绿肥红瘦。该构成供后续逐腿定义讨论，不在本轮直接改 live。

### 纯数值字段统一退出（2026-11-15，owner 拍板）

- 退出 9 项：日 J、极致缩量、止损参考价、流动性均值、龙头量能、MACD 区间、MACD 过度伸展、20 日箱体位置、60 日箱体位置。它们只需数值比较或滚动统计，不需人工判断形态。
- 敏感性同步收口：BOX_*、STOP_LOOKBACK、LIQUIDITY_WIN、LEADER_VOL_*、MACD_OVEREXT_* 不再进入 calib_probe 参数扫描；日 J / 极致缩量的阈值仍随反转 K 复合形态保留。
- 保留：反转 K、修复信号、非一波流、量能持续、三低、底部巨量、点火、MACD 背离、三打白骨精、趋势状态、N 结构、出货、波段及资金意图复合评分。live 判定与打分均不变。
- 受影响旧图集只留档并标注退出项，不重标、不盲标、不进 owner 正确率或参数改向票。

### MACD 两项恢复（2026-11-15，owner 复核后拍板）

- 恢复 `macd_technics.zone`、`macd_technics.overextended.hit` 及 `MACD_OVEREXT_PCTL/WIN` 敏感性扫描，供 owner 逐图核对；其余 7 个纯数值退出项不变，live 不变。
- `macd_technics__mining_v1` 图集恢复可标注，提示改为具体口径：zone 0/1/2/3 的水上与柱体状态、|DIF| 近 120 根分位 ≥90% 且柱体与 DIF 同号。

### b1_structure（2026-11-13，mining_v1 重标 105 张 → v2 候选 `708e6ed`）

- owner 判断评估（口径 A / D）：五日战法判错 15 条全部站得住（检测器过松）；repair_signals 6 条判定点反了，owner 确认后更正（`labels-3.csv`）；
  非一波流错因分「段识别漏判 / 放量门槛过低 / 原文无阈值 / owner 新增条件」四类。口径 B（盲标复测）待做。
- **五日战法退出 B1 打分**（owner 2026-11-13）：v2 定义对照 owner 33/33 一致，但挖掘段 J<13 抽样 1,689 根 0 命中（v1 命中 14 根且均为误判）——
  五日战法是看**前方低位是否有爆量**的追涨买法，不是 B1 当根形态。处理：`SCREEN_FORMULA_REGISTRY.json` 的 `scoring.weights.five_day_entry` 8 → 0，
  检测器保留（CZ 与证据列仍可用）。**随本模块 v2 升版一起生效**（一次 `--bump`，避免 V0 分缓存在第二批前多次失效）。
- 新特征想法（owner，未立项）：「B1 之前的低位是否出现过爆量」——与 `volume_detectors.bottom_volume` / `ignition` 相近，作右尾特征候选时再预注册。
- owner 定稿（2026-11-13，v0.520）：一波流 ⇒ 证据不足；顶部放量大阴须**同时**满足「≥前 5 日均量 1.5 倍」与「大于区内最大阳线量」；
  止损参考位不校准——live 止损以**买入价固定 7%** 为准（`stop_loss_ref` 近 10 日最低价只作展示）；流动性口径（近 20 日日均成交额）认可，维持只打标记。
- v2 确认图集：`b1_structure_v2__mining_v2cand`（36 张，同 mining_v1 的 2000 bar 池）；owner 确认后与五日战法退出打分一起 `--bump`。

### 研究标注 RD / SB（2026-11-16，v0.531，TODO #123③）

- 图集：`label_rd__mining_v1`、`label_sb__mining_v1`（各 36 张，同 mining_v1 的 2000 bar J<13 池；RD 图集带 RSI14 窗格与 25 线）。
- 池内命中率：RD 5.2%；**SB 40.6%**——J<13 池里四成的票近 60 根内有过「量 ≥ 前 20 日均量 3 倍且涨 ≥5%」，宽口径偏松，是本轮重点看的地方。
- RD 是纯数值比较（RSI14 < 25），同日 J 一样可考虑退出人工校准，owner 看图后定。

### 阴阳量 + 红肥绿瘦合并定义（草案，2026-11-16，待 owner 定）

现状：阴阳量 = 近 10 根阳线总量 vs 阴线总量（不看位置，技术分 +7 / −5）；红肥绿瘦 = 近 10 根在底部区（最低价 ≤ 近 60 根最低 × 1.02）∧ 阳线根数 > 阴线 ∧ 阳线平均实体 > 阴线平均实体 ∧ 阳线平均量 > 阴线平均量（只展示）。出货侧镜像为「顶部绿肥红瘦」（`distribution` ⑤）。

草案「红肥绿瘦（合并）」，一个检测器、三档结论，替代上面两项：
1. 窗口：近 N 根（草案 N=10）；
2. 量：阳线**总量** > 阴线总量 × k（草案 k=1.0；阴阳量现口径）；
3. 实体：阳线**平均**实体 > 阴线平均实体（红肥绿瘦现口径）；
4. 位置：底部区（窗口最低 ≤ 近 60 根最低 × 1.02）只作**标注**，不作命中条件（阴阳量原本不看位置）；
5. 结论：量 ∧ 实体 = 「红肥绿瘦」（加分）；反之阴线总量 > 阳线 ∧ 阴线实体更大 = 「绿肥红瘦」（减分）；其余中性。

**owner 定稿（2026-11-16，v0.532，候选 `bottom_patterns.detect_red_green_v2`，未接 live）**：N=30、k=1、加「阳线根数 > 阴线」；
位置 = 阶段（收盘距近 120 根最高价回撤 ≥30% = 阶段底部、≤5% = 阶段顶部，30% 待校准）；阶段底部 + 阳占优 = 红肥绿瘦（+7），阶段顶部 + 阴占优 = 绿肥红瘦（−5）；
爆量根（量 ≥ 窗口均量 3 倍）不参与三项比较、单独标注：底部爆量阴线 = 好，顶部爆量阴线 = 坏，其余（底部爆量阳线 / 单根爆量阳线）只标注；标注暂不计分。
抽检图集 `red_green_v2__mining_v1`（36 张；J<13 池命中 3.5%，池内无绿肥红瘦——J<13 很少出现在阶段顶部）。
### perfect_b1_fit / s_shape 重新校准（2026-11-17，v0.533，TODO #123④）

- `perfect_b1_fit__mining_v1`（36 张）：按贴合分（0-8）分档抽样——≥6 高 / ≥3 中 / 其余低；J<13 池「中 + 高」占 68.7%。问「分档与图形是否相符」。
- `s_shape`：J<13 池 2000 根里 **S** 全部 < 60（建议档全为「不买」）**，抽样只得 13 张。开发机快照挖掘 B 抽 150 票：J<13 时 S** 中位 14、p99 37，
  无一 ≥60；J≥13 时中位 27、p90 59、≥60 占 9%。S_shape 是突破 / 压缩型评分，与 B1 超跌形态天然相反——**在 B1 候选上不具备可校准的信息**，
  coder 建议从 1800 候选表撤出（检测器保留给研究），待 owner 定。
- **owner 2026-11-17 定：改为「沙漏后的 B1」，只看沙漏形态本身**（v0.535 候选 `s_shape.detect_hourglass_then_b1`，未接 live）：近 60 根内出现「先收敛、后放量突破」
  （突破日前 10 根相对再前 10 根日均振幅 ≤0.75 倍、均量 ≤0.8 倍；突破日收盘站上前 20 根最高价且量 ≥ 前 20 根均量 1.2 倍——阈值沿用 S_shape 的 VCP / 枢轴 / 放量常量），
  当根日 J<13。J<13 池命中 11.5%（S** 总分口径近 60 根 ≥70 为 61%，太宽）。图集 `hourglass_b1__mining_v1`（36 张，图上标突破枢轴线）。校准通过后替换候选表的 S_shape 展示。
### b1_thresholds（owner 上周末标注，2026-11-17 读回；`labels_owner_20261117.csv`）

- 周 J（36 条，错 15）：图集只有日 J 副图、无周线 J，owner 按日 J 判（「这个 J 都 95 了怎么处于低位」）——**这批不作证据**，与 v0.523「周 J 退出人工校准」一致，只留档。
- 日 J（36 条全对）、极致缩量（36 条，错 10）：v0.526 已退出人工校准，只留档；但缩量的错集中在「检测器判不成立」（miss / boundary-False），
  参数改向票 `VOL_RATIO_MAX ×1.2` 修正 5 误伤 2——owner 看图认为缩量门槛偏严，作为后续 B 类线索记录。
- **反转 K（30 条，错 10）**：错全部在「检测器判不成立」（boundary-False 9、miss 2 … 合计 10），owner 认为是反转 K；
  改向票 `VOL_RATIO_MAX ×1.2`（反转 K 里的缩量门）修正 7、误伤 1，`REVERSAL_CHANGE_MAX_PCT ×1.2` 修正 1。备注全空，判断正确率口径 A / D 无从评；待 owner 补理由或盲标复测（口径 B）后定 v2。

### 买点练习 BP1（100 题，owner 上周末完成，2026-11-17 复盘；描述性）

- 20 日：买 −3.0%（n=24，38% 上涨）、不买 +0.4%（n=43，53%）、等 −1.2%（n=33）；同批基准 −0.9%。买 − 不买 −3.35pp，置换单侧 p=0.15——
  与 2026-11-13 的 141 条同向（判「买」的组反而较差），两批都与随机分不开。
- 33 条「等」里 26 条没写批语、其余多写「等一下 / 快死叉」，只有 2 条能识别出等待条件，③ 无法复盘；继续练习时「等」请写明等什么。
### 一波流 v3（2026-11-17，v0.538，owner 看 v2 图集后指正）

- owner 看 v2 图集的读法是「这是不是一波流」，而字段问的是「是不是非一波流」（confirmed = 健康拉升、revoked = 撤销非一波流）——双重否定导致判读反向；
  owner 认定这批「几乎都不是一波流」：v2 有 9 条「撤销」只因放量跌破启动位（段涨幅 1.7%~26%），4 条「健康拉升」实为横盘 / 单边下跌。
- owner 定义（2026-11-17）：一波流 = 短期快速大涨后下跌（A 上 A 下），**20 根左右涨 ≥50%**；明显上涨 **≥45%**；出货单独标注。
  候选 `b1_structure.check_one_wave_v3`（未接 live）：结论三态 一波流 / 健康拉升 / 判断不了 + 出货标注；放量跌破只标注、不改结论。
- 开发机快照 J<13 抽样 1,287 根：判断不了 82%、一波流 18%、**健康拉升 0.3%（4 根）**——段涨幅 ≥45% 且非一波流的 123 根里 106 根卡在「上涨温和放量（单日量 < 段均量 2 倍）」，待 owner 定。
- 🔄 owner 2026-11-17 定：健康拉升**去掉「温和放量」**，只留回调缩量 + 无出货（v0.539）。同一抽样：判断不了 80%、一波流 18%、健康拉升 2.0%（26 根）。
- 图集 `b1_structure_v3__mining_v3cand` 按新定义重出，命中类按结论分层抽样：一波流 14、健康拉升 6、判断不了 16。
- owner 标注 `labels-yb2.csv`（36 条，错 8）后定（v0.540）：**一波流须回调不缩量**（回调段均量 ≥ 上涨段 0.9 倍 = A 下）；**健康拉升须上升 N 型结构不破**（高点后收盘不跌破上涨段内最后一个回踩低点）。
  对照这 36 条：与 owner 一致 28 → 33；仍不一致 3 条（#11 / #12 / #30：owner 认可的一波流，回调缩量但跌得深 −20%~−49%）。同一抽样：判断不了 89%、一波流 8.4%、健康拉升 2.4%。
  阈值由这 36 条调出 ⇒ 用新抽样图集 `b1_structure_v3__mining_v3b` 复验。
- **live 用法（owner 2026-11-17 定，随 v3 升版生效）**：一波流**只标记提醒、不否决**（同出货 watch）——升版时现行 `non_one_wave_revoked` 封顶规则撤掉，
  候选表风险列标「一波流」「顶部出货（v3）」；健康拉升沿用现行「非一波流确认」的技术分 +5。因不再改可买名单，不另立 R 做过滤效果检验（原拟 R53 取消）。
- 复验图集 `b1_structure_v3__mining_v3b` 标注 `labels-yb3.csv`（owner 2026-11-18，36 条）：一波流 对 4 / 错 4 / 不确定 2；健康拉升 对 1 / 错 7 / 不确定 2；
  判断不了 对 1 / 错 15。健康拉升误判多为「拉升后转横盘震荡」与「顶部放量出货」；「判断不了」被判错是因为 owner 要的是明确形态。**v3 不升版**。
- **合并校准（v0.548，owner 2026-11-18）**：一波流 / 健康拉升、趋势三态、wave_type、出货、N 结构本质都在回答「B1 之前是什么走势」，
  改为一个图集 `trend_shape__mining_v1`（48 张，每票一张，按一波流 v3 × 趋势三态分层）：owner 从菜单单选形态（一波流 / 健康拉升 / 拉升后横盘震荡 /
  横盘震荡 / 下跌后横盘震荡 / 震荡下跌 / 下跌中 / 震荡上涨 / 不确定）+ 附加勾「顶部放量出货」；图上并排各检测器读数只作参考。
  ingest 按 `SHAPE_EXPECT` 把形态换算到一波流 v3 与趋势三态口径出一致率，出货勾选对两个出货读数，wave_type / N 结构只出交叉表。
- `labels2.csv`（owner 2026-11-19，48/48）读回：一波流 v3 判一波流 10 张、owner 认 5 张（owner 标的 5 个一波流全被检出）；健康拉升判 8 张、owner 一张没标；
  趋势三态 32/43；N 结构收盘破位与 owner「下跌」对应好（下跌类 15/15 破位、其余 25/30 未破，线索）。「顶部放量出货」owner 没看到勾选框 ⇒ 出货项不计，改成「有 / 无」按钮行（v0.551）。
- **形态菜单浓缩（v0.552，owner 2026-11-19）**：三态 震荡上涨 / 横盘震荡 / 震荡下跌，中间有转折记「X 转 Y」（6 种）；趋势三态期望取转折后段。
  旧标签换算 `SHAPE_LEGACY`（一波流 → 上涨转下跌，待 owner 确认）；换算后的 `labels3_converted.csv` 导入续标，只需补「顶部放量出货」有 / 无。
- `labels3.csv`（owner 2026-11-19，三态菜单，48/48，形态改动 29 条 ⇒ 以本份为准）：48 张里转折 35 张、纯三态 13 张。
  趋势三态当前状态 = owner 后段 30/48（62.5%）；用「B1 前 k 根的趋势三态」当前段，前段 + 后段都对最多 13/48（k=10）⇒ 现趋势三态识别不了转折。
  顶部放量出货：owner 有 21 / 无 27；出货五方式 有 ∩ owner 有 13、有 ∩ owner 无 13（准确率 50%、检出率 62%，区分力弱）；一波流 v3 顶部出货检出 2/21。
  owner 备注里「一波流」作附注出现（#11 / #17 / #22）⇒ 一波流更像附加属性，不是形态。只描述，改法待 owner 定。
- **owner 定（2026-11-19）**：①做走势三态 v2；②一波流 v3 先复验再升版。落地（v0.553）：
  `research/swing_state.py` 候选（研究侧，不进 live）：近 10 根涨跌 ±8% 记上涨 / 下跌、其余横盘，近 90 根内游程（<5 根并入前段），最后两段 =「X 转 Y」。
  参数在 labels3 的 48 张上扫 320 组取平坦区中心：整条标签一致 22/48（现趋势三态做转折最多 13/48）、后段 31/48 ⇒ **由这 48 张调出，须复验**。
  已知口径：涨后横住再跌记「横盘转下跌」，只有陡涨陡跌才记「上涨转下跌」。
  一波流改为附加项（有 / 无），与「顶部放量出货」并列。复验图集 `trend_shape__mining_v2`：48 张新票（与 v1 重叠 1 只），按一波流 v3 分层（一波流 8 / 健康拉升 8 / 无数据 8 / 判断不了 24），
  **盲标**（图上不显示检测器读数，防锚定）。读回看：走势三态 v2 整条与后段一致率、一波流 v3 与 owner「一波流 有」的检出 / 准确率、出货两项。
- 复验读回 `labels4.csv`（owner 2026-11-19，盲标 48/48）：
  - 🔄 **走势三态 v2 未过复验**：整条标签 6/48（调参样本上 22/48 ⇒ 过拟合）；后段 23/48、前段 17/48。主要错法：10 根尺度把 B1 前的短回调读成一段（常判「下跌转横盘」），owner 看的是更大尺度。
  - 趋势三态后段 19/48（上一轮 30/48）。owner 本轮纯三态 24 张、上一轮 13 张 ⇒ 需先测 owner 自洽（B 口径盲标复测）再设计下一版。
  - 一波流 v3：owner 有 4 张，检出 3（另 1 张无数据）；owner 无 44 张，误报 5。两轮合计检出 8/9、判一波流 18 次对 8 次（约 44%）。
  - 出货五方式：owner 有 27 张检出 18；标了出货 29 张对 18 张。两轮合计检出 31/48、准确约 56%。一波流 v3 顶部出货检出 3/27，不可用。只描述。
- **走势三态 v3（v0.554，owner 2026-11-19「显示多少根 K 线就看多少 K 线」）**：只看图上 120 根收盘（对数价），整段一条直线 vs 一个转折点切两段（每段 ≥40 根）各一条直线；
  两段误差和 ≤ 整段一半才算转折；段内涨跌 ±10% 分三态。v1 / v2 两份 48 张交叉验证（一份调参、另一份验）：验证份整条 15~23/48、后段 27~33/48；
  选定参数两份合计整条 39/96（41%）、后段 63/96（66%）。比 v2（复验 6/48）与趋势三态做转折（≤13/48）好，但仍须新抽样复验 + owner 自洽复测。
- **一波流 v3 升版（v0.555，owner 2026-11-19「一波流可以只做提醒」）**：`b1_structure.detect` 加 `one_wave_v3`，`score_candidates` 命中记 `one_wave_v3`
  风险标记（候选表「一波流（提醒）」），不封顶、不改状态、不计分；`non_one_wave_revoked` 封顶同步关闭（只标记）。b1_structure 版本 v1 → v2（bump）。
  健康拉升不采用（两轮 owner 没标过、v3 判 16 次全错）；「非一波流确认 +5」仍按 v1。
- **owner 自洽复测（v0.556，B 口径）**：`calib_probe.build_retest` 从 v1（labels3）/ v2（labels4）各抽 8 张，打乱、隐去代码 / 检测器读数，
  图集 `trend_shape__retest_v1`（16 张）；读回 = owner 两次标注的形态整条 / 后段 / 两个附加项一致率。决定走势三态 v3 还值不值得往下磨。
- 复测读回 `labels5.csv`（owner 2026-11-20，16/16）：**owner 自洽 形态整条 13/16、后段 14/16、顶部放量出货 15/16、一波流 8/8**（v1 那 8 张原先没有一波流项）。
  改判 3 张：两张「横盘震荡 → 上涨转下跌」、一张「横盘转下跌 → 震荡下跌」。同 16 张上走势三态 v3 对新标注整条 7/16、后段 11/16 ⇒ 离 owner 自洽还有明显差距。
  试过末段最短根数单独设、阈值另取：v1 / v2 交叉验证的验证份没有提升（14~21/48）⇒ 两段直线这一族调参已到头，下一步要换判法，先请 owner 写出自己的判断规则（A 口径）。
- ⚠️ 盲度修正（v0.558 review）：`retest_v1` 只隐去标题代码与检测器读数，图轴真实日期、CSV / HTML 元数据仍可识别；上述自洽读数保留但只按**弱盲标**证据使用。新版 `build_retest` 改合成图轴日期、匿名 CSV / 页面元数据，真实映射仅留 summary；后续复测须用新版。
- **owner 判法（2026-11-20，A 口径）**：整体扫 120 根看 N 结构高低点抬高 / 降低，都在最高最低点内震荡算横盘；转折找最高 / 最低点看前后；从整体到局部、看大的波峰波谷。
  原型对照（v1+v2 共 96 张，交叉验证）：转折只取最高 / 最低点 + 段内直线 ±15% 最好（整条 52/96、后段 72/96）；段内改用 N 结构分型（3 根）33~40、改用大波峰波谷（ZigZag 5%~20%）46；
  加「末段破前低 / 前高」无提升。剩余错误分散（每类 ≤4 张）。
- **走势三态 v4（v0.557，owner 定用于选股侧提示）**：`b1_structure.check_swing_state`（不进 detect、live 不变），参数 SW4_*（120 根 / ±15% / 左端 ≥20 / 当根 ≥5 / 0.9）。
  复测 16 张对新标注整条 6/16、后段 10/16。复验图集 `trend_shape__mining_v3`（48 张新票，盲标，与已标重叠 1 只）；过了再接候选表「走势」提示列。
- 复验读回 `labels6.csv`（owner 2026-11-23，48 张，图只画 K 线和量）：11 张标「不确定」，其中 10 张备注写了 3 段以上走势（如「上涨转下跌再转横盘震荡」）、1 张 W 底。
  - 走势三态 v4：可判 37 张整条 18（49%）、后段 25（68%）；连 3 段样本 46 张整条 18、末两段 22、后段 30（65%）。调参样本上是 52% / 74% ⇒ 新样本略降，后段约 2/3。
  - 试「整体到局部」递归切成 ≤3~4 段：三份留一交叉验证每次都选回 2 段，3 段不比 2 段好。
  - 一波流 v3（live 提醒）：owner 有 15 张、检出 4；v3 判 8 次对 4 次。三轮合计检出 12/24、准确 12/26 ⇒ 本轮 owner 标的一波流明显多于前两轮（15/48 vs 9/96），口径待确认。
  - 出货五方式：owner 有 21 检出 13；标出货 29 对 13。区分力仍弱。只描述。
- **MACD 背离（owner 2026-11-23，`macd_technics__mining_v1` labels7.csv）**：底背离判了 7 张（全是检测器判成立）：对 1、错 5、不确定 1。owner 理由：
  「绿柱和绿柱比、红柱和红柱比」——比的是一组绿柱里最长那根和上一组绿柱里最长那根；当根还在红柱时不算；两组差不多不算；股价没创新低不算。
  现行判法（收盘 2 根分型摆低 + DIF 比较）与此不符。候选 v2（v0.565）：`macd_technics.check_macd_divergence_v2`（同色柱组；夹在中间 <2 根的异色柱并入；更短须 ≥10%），
  顶背离 / 三打白骨精同理用红柱组。这 7 张上 v2 与 owner 全一致（不确定那张判不成立）。新图集 `macd_technics_v2__mining_v1`（53 张，图上紫线标比较的两组）。未接 live。
  `labels8.csv`（owner 11 张底背离）：对 6 / 错 4 / 不确定 1；错的理由都是「同组内有多个波峰，应在波之间比」。v2 改（v0.566）：同色柱组内按柱长波峰（左右各 2 根）再切成「波」，比较最近两个波；
  更短门槛 10% → 0（10% 会漏掉同组内略短的背离）。两份共 16 张（有对错）一致 15/16，剩 #1（owner 比 11-09 与 10-31，检测器判成立）待核。新图集 `macd_technics_v2__mining_v2`。
  复验 `labels9.csv`（owner 2026-11-23，36/36，新样本）：**对 35 / 错 0 / 不确定 1**——底背离 12/12、顶背离 12/12、三打白骨精 11/12（#34 不确定：owner 认为更早的两波已是顶背离，最近两波股价走低故检测器判否）。
  ⇒ 背离 v2 定义校准通过；**v0.567 替换 live**（owner 2026-11-23）：check_macd_technics 的底背离 / 顶背离 / 三打白骨精改由 v2 产出，分值与否决不变，v1 结果留 `*_v1`；macd_technics v2（bump）。
- **v0.568 review 边界修复**：原左右 2 根窗会吞掉靠近大峰的矮峰，峰距 ≤2 且峰间已有更低谷底时补回候选；只合并连续同值平台峰。补底 / 顶背离、三打白骨精受控正例及近距双峰 / 平台钉测；原 35/35 未覆盖此边界。macd_technics v3（bump）。
  🔄 v0.569 复核：v0.568 补回矮峰不看谷底深度，5% 小抖动也切波，owner 标注 51 张一致 50 → 47（#13 / #16 顶背离、#27 三打白骨精变错）。加 `MACD2_WAVE_VALLEY=0.7`（峰间谷底 ≤0.7×较矮峰才补回）：回到 50/51，近距深谷双峰仍切开（0.5 / 0.8 同为 50/51）。macd_technics v4（bump）。
- **底部放量合并（owner 2026-11-24）**：`ignition__mining_v1` labels10.csv 的点火 36 张作废——图集问的是「出现放量收阳的启动 K」，没说检测器在近 10 根里找、也没标放量 K，owner 按当根判（错 13 张都是点火 K 在 3~9 根前）。
  owner 定义：放量点火 / 龙头量能 / 底部巨量说的都是「底部放量」，评价放量之后回调的量能状态，与当根无关；N 取 10~15 根；底部 = N 型结构的阶段低点；按程度用 1~3 星。
  候选（v0.570）：`volume_detectors.check_base_volume`，近 15 根内、从阶段低点（前 20 根最低收盘）起 5 根内的放量：⭐ 单根放量阳线 / ⭐⭐ 连续 3 根 ≥ 地量 1.7 倍 / ⭐⭐⭐ 单根 ≥ 250 根均量 2 倍，取最高档。挖掘 B 样本点：⭐ 2.7%、⭐⭐ 17.4%、⭐⭐⭐ 9.2%。
  未接 live（live 仍是三项叠加 +4 / +6 / +10）。图集 `base_volume__mining_v1`；旧图集隐藏点火与 B1 点火（依赖点火，合并后重定），只留回调缩量。
- **乖离过大（owner 2026-11-24）**：原名「过度伸展」是 agent 对字段 `overextended` 的直译，原文（qn/01_general.md §二）是「乖离过大 = 失去支撑力量，追高易站岗」。原判法 |DIF| 近 120 根分位 ≥90% 且柱体与 DIF 同号，绿柱也命中；`macd_technics__mining_v1` 的 24 张里 23 张绿柱、0 张水上红柱，不对题，已隐藏。
  owner 定义：MACD 双线还在上涨，但红柱开始滞涨或下降，且双线已经超过红柱范围。v0.571 落地：DIF、DEA 都比昨天高 ∧ 0 < 当根红柱 ≤ 昨天 ∧ DEA > 当根红柱；纯数值判定，退出人工校准（钉测）。删 MACD_OVEREXT_PCTL / WIN。挖掘 B 样本点命中 2.9%。live 仍只作风险标记「MACD 乖离过大」。
- **出货 ①②（owner 2026-11-24，`distribution__mining_v1` labels11.csv 15 张）**：对 7 / 错 8；错的都是 owner 指出更早的顶部 / 次高点放量阴线应判 high，原 ①② 只看近 5 根、要求大阴（≥ 涨跌停 ×0.5 / 0.8）+ 加速 ≥25% / 前日缩量，一根都没认出。
  owner 判法：顶部和次高点放量阴线是最高优先级出货信号；阴线量须大于**前面**的阳线（一字板算阳线）；假阴真阳（收 < 开、收 > 前收）底部区间算阳量、顶部区间算阴量，区间用走势三态 + N 型结构位置配合判断；#7 000812 2002-05-20 改判为次高点放量出货。
  候选（v0.572）：`distribution.check_top_bear_volume`（近 60 根；阴量 > 前 5 根阳线最大量且 ≥ 20 日均量 1.5 倍；距最高价分型高点 ≤2 根、该高点 ≥ 近 60 根最高 0.7）。15 张一致 13/15（原 7/15），剩 #8 002568 2025-10-28 判出、#14 000700 2004-02-06 未判出。未接 live。图集 `top_bear_volume__mining_v1`。
- **复验（owner 2026-11-24）**：`top_bear_volume__mining_v1` labels13.csv 24 张：对 16 / 错 3 / 不确定 5。owner 认的放量 = 前 60 根最大量的 0.84~0.96，存疑的（温和放量）只有 0.20~0.40 ⇒ v0.573 加 `DIST2_MAX60=0.6`；回看仍 60 根（owner 定）。
  两份共 34 张有对错：一致 29 → 30；剩 distribution #14 / #15、top_bear #9（3-3 量 1.45 倍 20 日均量，差一点）/ #13（顶部放量在 79 根前，超出 60 根，owner 定不追）。
  `base_volume__mining_v1` labels12.csv 15 张：对 12 / 错 2 / 不确定 1。v0.573：窗口 15 → 20 根（#51 底部巨量在 16~18 根前）；⭐ 档假阴真阳算阳量（底部区间，owner）。14 张有对错一致 13（#12 现判 ⭐⭐，比 owner 要的 ⭐ 强一档）。两份旧图集归档，重出 mining_v2。
- **底部放量去掉 ⭐（owner 2026-11-24）**：单根放量阳线（原放量点火）没有实质意义，不再提醒、不加分；只识别 ⭐⭐ 连续放量 / ⭐⭐⭐ 巨量（v0.574）。合并上 live 时原点火 +4 一并退出。`base_volume__mining_v2` 的 ⭐ 18 张已隐藏。
- **底部放量 · 阶段底部（owner 2026-11-24，`base_volume__mining_v2` labels14.csv 35 张）**：owner「整个形态要判断，要在持续下跌走势之后」「下跌之后的底部区域，只要是阶段底部都算」；#21 / #23 / #24（放量前横盘震荡 / 上涨转横盘）改判不成立。
  v0.575：底部 = 放量起点前一根的走势三态有下跌段、且最后一段不是上涨；🔄 去掉「距阶段低点 ≤5 根」（底部横盘中段的放量认不出），删 BV_NEAR_LOW / BV_STAGE_LOOKBACK / BV_PIVOT。31 张有对错（含改判）一致 20 → 22。
  待核：#44 603658 2026-01-26（owner：标准底部放量）放量前是横盘震荡、前 250 根内只跌 17%，新口径仍不认。图集归档，重出 mining_v3。
- **顶部放量阴线复验二（`top_bear_volume__mining_v2` labels16.csv 24 张，owner 2026-11-24）**：对 19 / 错 5。owner：多根命中「找最近的就行」（#7 改判对）；#11 600505 06-07 owner 看量大于前面阳线（比紧挨的阳线大、比 4 根前的小）。
  v0.577：DIST2_PREV_YANG 5 → 3、DIST2_POS 0.7 → 0.65（#18 次高点 0.69）、只报最近一根。三份共 58 张一致 49 → 51（88%）；剩 #1 000568（owner：后面涨上去的新平台低点，不是次高点；加「之后创新高即失效」会让 #24 变错，未采用）、#11 / #19（量 < 20 日均量 1.5 倍；降到 1.3~1.4 净值变差）等。
- **顶部放量阴线上 live（v0.578，owner 2026-11-24）**：替换旧 ①②。J<13 样本点（挖掘 B 6,618 个）命中 47.7%（旧 ①② 0.1%）⇒ owner 定只提醒不受限、技术分 −15；风险等级只看 ③~⑥。distribution v2（bump）。
- **三低（owner 2026-11-25）**：出处 cz/03_entry_methods.md §三「三低买入法」（低价格 / 低量 / 低关注度 + 基本面排雷）。v0.583：低量改为近 5 根均量 < 250 日均量 0.4（原当根 < 0.3；原文「地量持续萎缩」）；与基本面「优」（非三无）合取，候选表基本面列显示「优·三低」，只提醒不计分；低关注度不可量价计算、不做。纯数值，退出人工校准，`volume_detectors__mining_v1` 全部隐藏。J<13 样本点量价两条命中约 4.4%。
- **回调缩量企稳 v2（owner 2026-11-25）**：`ignition__mining_v1` labels10.csv 回调缩量 6 张（对 1 / 错 3 / 不确定 2），错的都是当根放量（#40 2.52、#41 0.96 倍前 5 日均量）或 K 线大（#42 实体 5.2%）。原文（01_swing_rules §5）是「回调段成交量单调收缩，收盘守住 DKS 或前一有效低点」，v1 只比两段均量（< 0.85）。
  完美 B1 10 例（R36 买点）读数：回调 4~27 根、回撤 6~30%；回调初段常是顶部余量（2~6 倍涨段均量），逐日单调无一满足，末 1/3 / 初 1/3 均量 0.40~0.71；当根阴线量 0.61~0.89、阳线 0.47~1.17，实体 0.3~3.7%；300689 收盘在 DKS 下 0.8% 但守住前一低点。
  候选（v0.584）：`ignition.check_pullback_shrink_v2`（近 30 根高点；末 / 初 1/3 均量 ≤0.75；阴线 < 0.9、阳线 < 1.5 倍前 5 日均量；实体 < 4%；守 DKS 或前一低点）。完美 B1 10 + owner 4 共 14 张全对（v1 9/14；门槛贴着这 14 张定，须新图集复验）。J<13 样本点命中 22.6%（v1 10.4%）。未接 live。「守住 DKS 或前一低点」是数值判断，退出人工校准（owner 2026-11-25），由钉测管；图集只判量与当根 K 线。图集 `pullback_v2__mining_v1`；旧图集回调缩量 30 张不再标。
- **纯数值判定退出人工校准（v0.563，owner 2026-11-23）**：MACD 区间（DIF / DEA 是否在 0 轴上 + 柱体放长 / 缩短 / 转绿）只是数值比较，看图只能核实现，改由钉测管四态；`macd_technics__mining_v1` 的 24 张区间图隐藏，MODULES 去掉该字段。
- **owner 定（2026-11-23）**：一波流 = ①连续 ②快速（几个交易日内）③大幅（≥50%），之后 A 杀或震荡下跌 / 横盘都算，尽力识别；走势只识别买点前最近 1~2 段，尽力识别。
  落地（v0.561）：`b1_structure.check_one_wave_v4`（120 根内 ≤10 根、途中收盘回撤 <10%、最高价较起点最低价 ≥50%），live 提醒改用 v4（风险列「一波流（提醒）」，不否决不计分）；
  owner 一波流标注 96 张（有 19）：v4 检出 12/19、准确 12/29（v3 7/19、7/16）。走势三态 v4 进 detect，候选表证据列显示「走势：X」（只展示）。b1_structure v3（bump）。
- 全部图集取值改正面中文（字段专属对照）：非一波流字段显示「健康拉升 / 疑似一波流或出货 / 判断不了」，MACD 区间与出货失效名单措辞同步改，不再出现双重否定。
## P0H 持仓大形态（v0.485 纳入，**排在 P0 之前**）

注册表外、但直接驱动 live 持仓状态机（`b1_holding_state`）的检测器，`pipeline/market_timing/technical_monitor.py`，18 个参数
（17 个判定参数，v0.485 从函数体 / 默认值提为模块常量，行为逐位不变：真实数据 200 个 as-of 截断对拍 0 差异；另 `QFQ_LOAD_BARS` 加载深度，v0.486 引入）：

| 检测器 | live 作用 | calib_probe 模块 |
|---|---|---|
| 上升 N 结构（收盘分型 L1→H1→更高 L2，站上 H1 = confirmed） | 收盘跌破 L1 ⇒ **P0 N 型主结构清仓评估** | `n_structure` |
| 下降 N 结构（H1→L1→更低 H2，跌破 L1 确认） | 跌破结构低点 ⇒ **P0 清仓评估** | `n_structure` |
| 趋势三态（上涨 / 下跌四条件全满足，其余一律「横盘震荡」） | 下跌 + 跌破 20 日箱体 ⇒ **P0**；下跌 ⇒ P1 反弹减仓 | `trend_box` |
| 箱体（20 / 60 日，上沿 = 最高价 85% 分位、下沿 = 最低价 15% 分位） | 同上 | `trend_box` |

- review 名单来源用持仓：`calib_probe --module n_structure --mode review --review-source holdings --since ... --until ...`。
- **已知定义问题**：
  1. ✅ **已修（v0.486，owner 拍板全部前复权，technical_monitor v2）**——原问题：**持仓链用未复权价**：`technical_monitor.read_vipdoc` 读 vipdoc 原始日线，不走前复权（与「全链前复权」原则不符）。快照实测 592 个
     as-of 截断里，收盘价未复权 ≠ 前复权 482 个；**N 结构判定因此不同 12 个（2.0%）、趋势 / 20 日箱体判定不同 17 个（2.9%）**——
     除权缺口会造出假的「跌破结构低点」。改为前复权是判定语义变更（持仓 P0 触发会变），须 owner 定。
  2. 「横盘震荡」是兜底类（上涨 / 下跌四条件任一不满足即落入），混有上升 / 下跌中继。
  3. N 结构分型粒度细（左右 3 根、近 90 根），无幅度门槛，日线小波动也成 N；且只取最近一组，不看所处更大级别。

## P0 校准顺序（按对 live 的影响面）

| 序 | 模块 | live 角色 | 参数数 | 改动后受影响的 R（需重跑或复核） |
|---|---|---|---|---|
| 1 | `b1_thresholds`（J<13、反转 K） | 进池硬门槛 / 打分 | 5 | **全部 B1 信号集研究**：R1~R10、R40~R50（信号集本身变） |
| 2 | `entry_patterns` | 技术分腿（BBI 上 / 低 J / 缩量 / 反转 K / 强 RS） | 1 | V0 分相关读数：R40/R45、R42、R44/R47（top-N 与组合层）、R50 L3/L4 |
| 3 | `b1_structure` | 技术分腿（五日、修复信号、非一波流等） | 12 | 同上 |
| 4 | `volume_detectors` | 技术分 + 资金意图（龙头量、底部巨量、量能持续） | 10 | 同上 |
| 5 | `ignition` | 技术分 + 资金意图（点火、回调缩量、知行） | 7 | 同上 |
| 6 | `macd_technics`（含小绿三类） | 技术分 MACD 腿 + 顶背离封顶 | 4 | 同上 |
| 7 | `distribution` | 出货封顶（gate）+ 技术分减分 | 17 | 同上（V0 含减分） |
| 8 | `wave_type` | 冲刺波 / 非一波流封顶（gate） | 12 | live 分层；研究侧 V0 若含波浪腿同上 |
| 9 | `capital_intent` | 资金意图分档（分层第二轴） | 2 | 仅 live 分层 / 状态列；研究侧无直接消费 |

- `j_low_gate`、`weekly_j` 无独立数值参数（阈值来自 `b1_thresholds.J_LOW_THRESHOLD`），随第 1 项。
- **重跑规则**：只有定义被改动的因子才触发；改动当轮在 CHANGELOG 列出受影响 R，判毕单元按纪律就地标 🔄 后重跑，不追溯翻案。
  `collect_all` 口径（全候选 margin）只受第 1 项影响；V0 分改动只影响依赖分数的读数（top-N / 组合层 / 分档）。

## 参数总表（生成）

### P0H（18 个参数）

| 因子 | 参数 | 当前值 | 代码注释 | 因子状态 / 效果证据 | 定义校准 |
|---|---|---|---|---|---|
| `technical_monitor` | `N_PIVOT_LEFT` | 3 | 收盘分型左侧确认根数（N 结构） | - | 未校准 |
| `technical_monitor` | `N_PIVOT_RIGHT` | 3 | 收盘分型右侧确认根数 | - | 未校准 |
| `technical_monitor` | `N_LOOKBACK` | 90 | N 结构分型搜索近端窗口（根） | - | 未校准 |
| `technical_monitor` | `N_STALE_BREACH_BARS` | 10 | 跌破关键位超过 N 根视为陈旧结构（不再触发新鲜 P0） | - | 未校准 |
| `technical_monitor` | `BOX_UPPER_Q` | 0.85 | 箱体上沿 = 窗口最高价分位；纯数值比较 | - | 无需 A 类（owner 2026-11-15） |
| `technical_monitor` | `BOX_LOWER_Q` | 0.15 | 箱体下沿 = 窗口最低价分位；纯数值比较 | - | 无需 A 类（owner 2026-11-15） |
| `technical_monitor` | `BOX_SHORT` | 20 | 短箱体窗口；纯数值比较 | - | 无需 A 类（owner 2026-11-15） |
| `technical_monitor` | `BOX_LONG` | 60 | 长箱体窗口；纯数值比较 | - | 无需 A 类（owner 2026-11-15） |
| `technical_monitor` | `TREND_MA_FAST` | 25 | 趋势快线 | - | 未校准 |
| `technical_monitor` | `TREND_MA_SLOW` | 60 | 趋势慢线 | - | 未校准 |
| `technical_monitor` | `TREND_MA_LONG1` | 144 | 展示：长期均线 1 | - | 未校准 |
| `technical_monitor` | `TREND_MA_LONG2` | 240 | 展示：长期均线 2 | - | 未校准 |
| `technical_monitor` | `TREND_FAST_SLOPE_BARS` | 5 | 快线斜率回看根数（三态判定用） | - | 未校准 |
| `technical_monitor` | `TREND_SLOW_SLOPE_BARS` | 10 | 慢线斜率回看根数（展示） | - | 未校准 |
| `technical_monitor` | `TREND_LONG_SLOPE_BARS` | 20 | 长线斜率回看根数（展示） | - | 未校准 |
| `technical_monitor` | `TREND_HL_WINDOW` | 20 | 高低点比较窗口：近 N 根 vs 前 N 根 | - | 未校准 |
| `technical_monitor` | `TREND_MIN_BARS` | 60 | 趋势判定最少 K 线数 | - | 未校准 |
| `technical_monitor` | `QFQ_LOAD_BARS` | 100000 | - | - | 未校准 |

### P0（107 个参数）

| 因子 | 参数 | 当前值 | 代码注释 | 因子状态 / 效果证据 | 定义校准 |
|---|---|---|---|---|---|
| `b1_thresholds` | `J_LOW_THRESHOLD` | 13.0 | - | - | 未校准 |
| `b1_thresholds` | `VOL_RATIO_MAX` | 0.5 | 量比 vs MA5 <= 0.5 | - | 未校准 |
| `b1_thresholds` | `VOL_PCTILE_MAX` | 10.0 | 20 日量分位 <= 10（单位：%） | - | 未校准 |
| `b1_thresholds` | `REVERSAL_CHANGE_PCT` | 2.0 | - | - | 未校准 |
| `b1_thresholds` | `REVERSAL_AMPLITUDE_PCT` | 7.0 | - | - | 未校准 |
| `b1_structure` | `NOW_MILD_VOL_BURST` | 2.0 | 待回测参数：上涨段单日量/段均量上限（温和放量） | candidate | 未校准 |
| `b1_structure` | `NOW_BEAR_DROP_PCT` | -3.0 | 待回测参数：放量大阴跌幅% | candidate | 未校准 |
| `b1_structure` | `NOW_BEAR_VOL_RATIO` | 1.5 | 待回测参数：放量大阴量比（量/前5日均量） | candidate | 未校准 |
| `b1_structure` | `NOW_PULLBACK_VOL_RATIO` | 0.7 | 待回测参数：回调段均量/上涨段均量上限 | candidate | 未校准 |
| `b1_structure` | `NOW_TOP_ZONE` | 3 | 待回测参数：阶段高点观察区±N日 | candidate | 未校准 |
| `b1_structure` | `REPAIR_J_PREV_MAX` | 20.0 | 待回测参数：J拐头向上（昨日J上限） | candidate | 未校准 |
| `b1_structure` | `REPAIR_VOL_SHRINK` | 0.7 | 待回测参数：缩量止跌量比上限 | candidate | 未校准 |
| `b1_structure` | `REPAIR_CHANGE_PCT` | 2.0 | 待回测参数：止跌涨跌幅区间±% | candidate | 未校准 |
| `b1_structure` | `FIVE_DAY_SPIKE_RATIO` | 1.45 | 五日战法：近7日巨量倍数（CZ §十六）。原文"前一交易日均量"存歧义，按前一交易日单日量实现（vol[t]/vol[t-1]），待策略 owner 确认 | candidate | 未校准 |
| `b1_structure` | `FIVE_DAY_SPIKE_WINDOW` | 7 | 五日战法：巨量观察窗口（CZ §十六） | candidate | 未校准 |
| `b1_structure` | `FIVE_DAY_V2_SPIKE_RATIO` | 2.0 | v2 候选：当日量 ≥ 前一天 2 倍且收阳；只在 calib_probe 对照，不进 live | candidate | 未校准 |
| `b1_structure` | `NOW_V2_VOL_RATIO` | 1.5 | v2 候选：放量统一为当日量 ≥ 前 5 日均量 1.5 倍；只在 calib_probe 对照，不进 live | candidate | 未校准 |
| `b1_structure` | `NOW_V2_MAX_GAIN_PCT` | 50.0 | v2 候选：上涨段累计涨幅 >50% 判一波流；只在 calib_probe 对照，不进 live | candidate | 未校准 |
| `b1_structure` | `NOW_V2_MIN_GAIN_PCT` | 0.0 | v2 候选：明显上涨段下限，不足则 insufficient；只在 calib_probe 对照，不进 live | candidate | 未校准 |
| `b1_structure` | `LIQUIDITY_WIN` | 20 | 近 20 日日均成交额；滚动均值，只作展示 | candidate | 无需 A 类（owner 2026-11-15） |
| `b1_structure` | `STOP_LOOKBACK` | 10 | 止损参考位：近 10 日最低价；滚动极值，只作展示 | candidate | 无需 A 类（owner 2026-11-15） |
| `b1_structure` | `OW3_RALLY_MIN_PCT` | 45.0 | - | candidate | 候选（owner 2026-11-17 定稿，图集 `b1_structure_v3__mining_v3cand`） |
| `b1_structure` | `OW3_FAST_PCT` | 50.0 | 一波流：上涨段内任一 ≤OW3_FAST_BARS 根的窗口涨幅 ≥50% | candidate | 候选（owner 2026-11-17 定稿，图集 `b1_structure_v3__mining_v3cand`） |
| `b1_structure` | `OW3_FAST_BARS` | 20 | 「20 根左右」 | candidate | 候选（owner 2026-11-17 定稿，图集 `b1_structure_v3__mining_v3cand`） |
| `b1_structure` | `OW3_HEAVY_PULLBACK` | 0.9 | - | candidate | 候选（owner 2026-11-17） |
| `b1_structure` | `OW3_PIVOT` | 3 | N 型结构回踩低点：收盘分型左右各 3 根确认（同 technical_monitor.N_PIVOT_LEFT/RIGHT 口径） | candidate | 候选（owner 2026-11-17） |
| `b1_structure` | `SW4_BARS` | 120 | 只看图上显示的 120 根（owner：显示多少根就看多少根） | candidate | 已标 · live 提示（v0.561） |
| `b1_structure` | `SW4_THR` | 0.15 | 段内直线涨跌 ≥15% 上涨 / ≤−15% 下跌，其余横盘 | candidate | 已标 · live 提示（v0.561） |
| `b1_structure` | `SW4_MIN_HEAD` | 20 | 转折点（最高 / 最低点）距图左端至少 20 根 | candidate | 已标 · live 提示（v0.561） |
| `b1_structure` | `SW4_MIN_TAIL` | 5 | 转折点距当根至少 5 根（B1 前短回落也算一段） | candidate | 已标 · live 提示（v0.561） |
| `b1_structure` | `SW4_SPLIT_RATIO` | 0.9 | 两段拟合误差和须 ≤ 整段 0.9 倍才算有转折 | candidate | 已标 · live 提示（v0.561） |
| `b1_structure` | `OW4_BARS` | 120 | 只看图上显示的 120 根 | candidate | 已标 · live 提示（v0.561） |
| `b1_structure` | `OW4_FAST_BARS` | 10 | 快速：≤10 根（「几个交易日内」） | candidate | 已标 · live 提示（v0.561） |
| `b1_structure` | `OW4_RISE_PCT` | 50.0 | 大幅：最高价相对起点最低价涨 ≥50% | candidate | 已标 · live 提示（v0.561） |
| `b1_structure` | `OW4_MAX_PULLBACK` | 0.1 | 连续：拉升途中收盘较途中最高收盘回撤 <10%，否则视为断开 | candidate | 已标 · live 提示（v0.561） |
| `capital_intent` | `CAP_STRONG` | 5 | - | candidate | 未校准 |
| `capital_intent` | `CAP_MID` | 2 | - | candidate | 未校准 |
| `distribution` | `DIST_RECENT` | 5 | 待回测：出货形态观察最近N根 | active；R2 | 未校准 |
| `distribution` | `DIST_ACCEL_WIN` | 10 | 待回测：加速涨幅窗口（日） | active；R2 | 未校准 |
| `distribution` | `DIST_ACCEL_GAIN` | 25.0 | 待回测：加速涨幅%下限（阴线前） | active；R2 | 未校准 |
| `distribution` | `DIST_BIG_BEAR_FRAC` | 0.5 | 待回测：大阴=跌幅≥涨跌幅制度×0.5 | active；R2 | 未校准 |
| `distribution` | `DIST_LONG_BEAR_FRAC` | 0.8 | 待回测：长阴/近跌停=跌幅≥涨跌幅制度×0.8 | active；R2 | 未校准 |
| `distribution` | `DIST_HUGE_VOL_RATIO` | 2.0 | 待回测：天量/巨量=≥20日均量×2 | active；R2 | 未校准 |
| `distribution` | `DIST_HUGE_VOL_WIN` | 20 | 待回测：天量对比窗口（日） | active；R2 | 未校准 |
| `distribution` | `DIST_STAIR_MIN_BARS` | 3 | 待回测：阶梯放量阴线最少连续根数 | active；R2 | 未校准 |
| `distribution` | `DIST_STAIR_BREAK_VR` | 1.2 | 待回测：放量跌破QSX的量比下限 | active；R2 | 未校准 |
| `distribution` | `DIST_TOP_WINDOW` | 10 | 待回测：顶部区间（绿肥红瘦/双头）窗口 | active；R2 | 未校准 |
| `distribution` | `DIST_DOUBLE_TOP_TOL` | 3.0 | 待回测：双头两顶相近容差% | active；R2 | 未校准 |
| `distribution` | `DIST_SUBHIGH_SHRINK` | 0.9 | 待回测：次高前一日缩量量比上限 | active；R2 | 未校准 |
| `distribution` | `DIST_MIN_VOL_MA20_FRAC` | 0.05 | 待回测：vol_ma20 低于全序列均量×此比例时视为近零（派发检测器 available=False） | active；R2 | 未校准 |
| `distribution` | `DIST_WINDMILL_SHADOW_BODY` | 1.0 | 待回测：大风车长上影=上影 ≥ 实体×此值 | active；R2 | 未校准 |
| `distribution` | `DIST_WINDMILL_RANGE_PCT` | 7.0 | 待回测：宽幅震荡 K=振幅（高−低）/前收 ≥ 此值% | active；R2 | 未校准 |
| `distribution` | `DIST_WINDMILL_TOP_FRAC` | 0.93 | - | active；R2 | 未校准 |
| `distribution` | `DIST_WINDMILL_VOL_RATIO` | 1.5 | 待回测：天量=信号 K 量 ≥ 前20日量**中位数**×此值（v0.62 起中位数，见 :351 附近注释——偶发天量不把基准顶飞） | active；R2 | 未校准 |
| `distribution` | `DIST2_LOOKBACK` | 60 | - | active；R2 | 已标 · live 提示（v0.578） |
| `distribution` | `DIST2_PREV_YANG` | 3 | 阴线量须大于前 3 根里最大的阳线量（owner：只跟前面的阳线比；v0.577 由 5 改 3：#11 600505 06-07 比 4 根前的阳线小、比紧挨着的阳线大，owner 看是大；一字板收 = 开也算阳线） | active；R2 | 已标 · live 提示（v0.578） |
| `distribution` | `DIST2_VOL_MA` | 1.5 | 且 ≥ 前 20 日均量 1.5 倍（过滤缩量区的小阴线） | active；R2 | 已标 · live 提示（v0.578） |
| `distribution` | `DIST2_PIVOT` | 2 | 高点 = 最高价分型（左右各 2 根） | active；R2 | 已标 · live 提示（v0.578） |
| `distribution` | `DIST2_NEAR_HIGH` | 2 | 阴线距该高点 ≤2 根（顶部 / 次高点当天或前后） | active；R2 | 已标 · live 提示（v0.578） |
| `distribution` | `DIST2_POS` | 0.65 | 次高点 ≥ 近 60 根最高 × 0.65（v0.577 由 0.7 改：#18 300157 高点放量在 0.69；更低的反弹高点不算） | active；R2 | 已标 · live 提示（v0.578） |
| `distribution` | `DIST2_MAX60` | 0.6 | 且 ≥ 前 60 根最大量 × 0.6（owner 2026-11-24：温和放量不算；owner 认的放量都在 0.84~0.96，存疑的在 0.20~0.40） | active；R2 | 已标 · live 提示（v0.578） |
| `entry_patterns` | `RS_STRONG_PP` | 3.0 | 个股20日涨幅 − 上证20日涨幅 >= +3pp；纯数值比较 | candidate | 无需 A 类（owner 2026-11-14） |
| `ignition` | `ZX_CROSS_RECENT` | 10 | 待回测：知行金叉"近N日"窗口 | candidate | 未校准 |
| `ignition` | `IGNITION_WINDOW` | 10 | 待回测：放量点火扫描窗口（日） | candidate | 未校准 |
| `ignition` | `IGNITION_VOL_RATIO` | 1.5 | 待回测：点火量比（当日量/前5日均量） | candidate | 未校准 |
| `ignition` | `IGNITION_MIN_GAIN` | 3.0 | 待回测：点火单日涨幅%下限 | candidate | 未校准 |
| `ignition` | `PULLBACK_LOOKBACK` | 20 | 待回测：回调缩量企稳观察窗口（日） | candidate | 未校准 |
| `ignition` | `PULLBACK_MIN_DROP` | 3.0 | 待回测：距窗口高点回撤%下限 | candidate | 未校准 |
| `ignition` | `PULLBACK_VOL_RATIO` | 0.85 | 待回测：回调段/上涨段均量上限 | candidate | 未校准 |
| `ignition` | `PB2_LOOKBACK` | 30 | - | candidate | 候选（owner 2026-11-25） |
| `ignition` | `PB2_RUN_BACK` | 40 | 上涨起点 = 高点前 40 根内最低收盘 | candidate | 候选（owner 2026-11-25） |
| `ignition` | `PB2_MIN_DROP` | 3.0 | 距高点回撤 ≥3%（同 v1） | candidate | 候选（owner 2026-11-25） |
| `ignition` | `PB2_DECAY` | 0.75 | 量「一路缩」：回调段末 1/3 均量 ≤ 初 1/3 均量 × 0.75（完美 B1 10 例 0.40~0.71；逐日单调太严，10 例无一满足） | candidate | 候选（owner 2026-11-25） |
| `ignition` | `PB2_YIN_VOL` | 0.9 | 当根是阴线时：量 < 前 5 日均量 × 0.9（owner #40 2.52、#41 0.96 判放量；完美 B1 阴线 0.61~0.89） | candidate | 候选（owner 2026-11-25） |
| `ignition` | `PB2_YANG_VOL` | 1.5 | 当根是阳线时：量 < 前 5 日均量 × 1.5（完美 B1 阳线 0.47~1.17；缩量回调末端的小阳放一点量正常） | candidate | 候选（owner 2026-11-25） |
| `ignition` | `PB2_MAX_BODY` | 4.0 | 当根实体 < 前收 × 4%（owner #42 5.2% 判 K 线大；完美 B1 0.3~3.7%） | candidate | 候选（owner 2026-11-25） |
| `ignition` | `PB2_PIVOT` | 3 | 前一有效低点 = 上涨段内最后一个收盘分型低点（左右各 3 根） | candidate | 候选（owner 2026-11-25） |
| `macd_technics` | `MACD_SWING_FRACTAL` | 2 | 摆动高/低点分型：左右各 N 根确认 | candidate | 未校准 |
| `macd_technics` | `MACD_DIV_LOOKBACK` | 60 | 背离观察窗口（日） | candidate | 未校准 |
| `macd_technics` | `MACD2_MIN_GROUP` | 2 | - | candidate | 已标 · live（v0.567） |
| `macd_technics` | `MACD2_MARGIN` | 0.0 | 柱子「更短」的门槛：v0.566 起 0（owner 18 张标注上 10% 会漏掉同组内略短的背离） | candidate | 已标 · live（v0.567） |
| `macd_technics` | `MACD2_WAVE_FRACTAL` | 2 | 同一组柱子里按柱长再切「波」：柱长局部峰（左右各 2 根）各算一个波（owner：同组有多个波峰就在波之间比） | candidate | 已标 · live（v0.567） |
| `macd_technics` | `MACD2_WAVE_VALLEY` | 0.7 | v0.569：近距矮峰补回须峰间谷底 ≤0.7×较矮峰（v0.568 不限深度，5% 抖动也切波，owner 标注 50→47/51） | candidate | 已标 · live（v0.569） |
| `volume_detectors` | `VOLUME_SUSTAIN_WINDOW` | 13 | 量能持续性窗口（CZ §14.6：7-13日） | candidate | 未校准 |
| `volume_detectors` | `VOLUME_SUSTAIN_MIN_POST_DAYS` | 7 | 待回测参数：峰值日后确认主线最少观察日数 | candidate | 未校准 |
| `volume_detectors` | `VOLUME_SUSTAIN_RATIO` | 0.55 | 峰值55%（CZ §14.6） | candidate | 未校准 |
| `volume_detectors` | `VOLUME_SUSTAIN_RETREAT_DAYS` | 3 | 连续N日<峰值55%判撤退（CZ §14.6） | candidate | 未校准 |
| `volume_detectors` | `LEADER_VOL_BASE_DAYS` | 20 | 龙头量能：连续 3 根与滚动最低量比较 | candidate | 无需 A 类（owner 2026-11-15） |
| `volume_detectors` | `LEADER_VOL_RATIO` | 1.7 | 龙头量能倍数；纯数值比较 | candidate | 无需 A 类（owner 2026-11-15） |
| `volume_detectors` | `THREE_LOWS_DRAWDOWN_PCT` | 40.0 | 三低之低价格（自250日高点回撤%）；纯数值，钉测管（v0.583） | candidate | 退出人工校准（owner 2026-11-25） |
| `volume_detectors` | `BOTTOM_VOL_RATIO` | 2.0 | 待回测参数：底部巨量（≥250日均量×2，CZ §14.6） | candidate | 未校准 |
| `volume_detectors` | `BOTTOM_NO_NEW_LOW_DAYS` | 20 | 待回测参数：不再创新低观察窗口 | candidate | 未校准 |
| `volume_detectors` | `CZ_MIN_BARS` | 250 | CZ 三低/底部巨量最少K线数（不足→available=false） | candidate | 未校准 |
| `volume_detectors` | `BV_WINDOW` | 20 | 近 20 根内（owner 2026-11-24 由 15 放宽：#51 301398 的底部巨量在 16~18 根前）；评价量能状态，与当根形态无关 | candidate | 候选（owner 2026-11-24） |
| `volume_detectors` | `BV_BASE_LOW_WIN` | 60 | 底部低点（图上标注用）= 放量起点前 60 根最低收盘 | candidate | 候选（owner 2026-11-24） |
| `wave_type` | `WAVE_LOOKBACK` | 60 | 拉升波分析窗口（日） | active；R2 | 未校准 |
| `wave_type` | `WAVE_MIN_BARS` | 40 | 拉升波分类最少K线数 | active；R2 | 未校准 |
| `wave_type` | `WAVE_LIMIT_UP_PCT` | 9.8 | 待回测参数：涨停/接近涨停判定（单日涨幅%） | active；R2 | 未校准 |
| `wave_type` | `WAVE_SPRINT_WINDOW` | 20 | 待回测参数：冲刺波涨停统计窗口（日） | active；R2 | 未校准 |
| `wave_type` | `WAVE_SPRINT_MIN_LIMIT_UPS` | 2 | 待回测参数：冲刺波涨停次数下限 | active；R2 | 未校准 |
| `wave_type` | `WAVE_ACCEL_10D_GAIN` | 25.0 | - | active；R2 | 未校准 |
| `wave_type` | `WAVE_TOP_VOL_RATIO` | 1.5 | 待回测参数：顶部放量（高点日量/前5日均量） | active；R2 | 未校准 |
| `wave_type` | `WAVE_BUILDUP_GAIN` | (25.0, 50.0) | 建仓波段涨幅%（B1 §四.0 口径） | active；R2 | 未校准 |
| `wave_type` | `WAVE_RALLY_GAIN` | (35.0, 50.0) | 拉升波段涨幅%（B1 §四.0 口径） | active；R2 | 未校准 |
| `wave_type` | `WAVE_START_CANDLE_PCT` | 5.0 | 待回测参数：启动段长阳单日涨幅% | active；R2 | 未校准 |
| `wave_type` | `WAVE_START_CANDLE_VOL` | 1.5 | 待回测参数：启动段放量倍数 | active；R2 | 未校准 |
| `wave_type` | `WAVE_SECOND_START_GAIN` | 15.0 | 待回测参数：二次启动（前一段摆动幅度%下限） | active；R2 | 未校准 |

### P1（99 个参数）

| 因子 | 参数 | 当前值 | 代码注释 | 因子状态 / 效果证据 | 定义校准 |
|---|---|---|---|---|---|
| `b1_dual_factor` | `STRUCT_QSX_DKS_PTS` | 30.0 | QSX>DKS 多头结构（good_b1 8/9）——单项权重最高 | needs_work；R6 | 未校准 |
| `b1_dual_factor` | `STRUCT_MA_PTS` | 20.0 | - | needs_work；R6 | 未校准 |
| `b1_dual_factor` | `STRUCT_OVERHEAD_PTS` | 15.0 | 上方套牢少（复用 compute_overhead_supply 0-10 → ×1.5） | needs_work；R6 | 未校准 |
| `b1_dual_factor` | `STRUCT_VOL_CENTER_PTS` | 15.0 | 量能中枢上移（MA20 ≥ MA60） | needs_work；R6 | 未校准 |
| `b1_dual_factor` | `STRUCT_LAUNCH_PTS` | 20.0 | 曾有放量启动段（good_b1 9/9） | needs_work；R6 | 未校准 |
| `b1_dual_factor` | `LAUNCH_LOOKBACK` | 60 | 回看窗 | needs_work；R6 | 未校准 |
| `b1_dual_factor` | `LAUNCH_GAIN_PCT` | 5.0 | 启动阳线单日涨幅下限 % | needs_work；R6 | 未校准 |
| `b1_dual_factor` | `LAUNCH_VOL_MULT` | 1.5 | 启动阳线量 / 前 20 日均量下限 | needs_work；R6 | 未校准 |
| `b1_dual_factor` | `W_STRUCT` | 0.4 | 轴1 长期结构 | needs_work；R6 | 未校准 |
| `b1_dual_factor` | `W_REVERSAL` | 0.6 | 轴2 短期回调（B1 是回调买入，买点权重更高） | needs_work；R6 | 未校准 |
| `b1_dual_factor` | `RESONANCE_BONUS_PTS` | 12.0 | 待回测 | needs_work；R6 | 未校准 |
| `b1_dual_factor` | `DUAL_MIN_BARS` | 120 | DKS 需要 MA114 → 至少 120 根 | needs_work；R6 | 未校准 |
| `b2_surge_factor` | `B2_GAIN_PCT` | 4.0 | 原文:涨幅大于 4% | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `B2_J_MAX` | 55.0 | 原文:J < 55 | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `B2_B1_WITHIN` | 5 | "B1 之后"的回看窗:原文未给天数,B1 确认条件是"3 个交易日内 | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `B2_NO_UPPER_SHADOW_FRAC` | 0.15 | "无上影线最好":上影 ≤ 实体×此值算无上影（待回测） | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `B2_MIN_BARS` | 30 | - | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `SURGE_VOL_MULT` | 3.0 | "巨量点火":量 ≥ 此倍数 × 前 20 日均量（待回测） | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `SURGE_GAIN_PCT` | 5.0 | 点火日涨幅下限（量随价升）（待回测） | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `SURGE_HOLD_DAYS` | 4 | 原文:后续 4 天 | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `SURGE_HOLD_FRAC` | 0.5 | 原文:4 天量不能低于巨量的一半 | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `SURGE_MA_CROSS` | 60 | 原文:穿越 60 日线 | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `SURGE_NEW_HIGH_DAYS` | 180 | 原文:9 个月新高 ≈ 180 交易日 | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `SURGE_LOOKBACK` | 60 | 异动回看窗（"异动之后找 B1"的有效期）（待回测） | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `SURGE_MIN_BARS` | 200 | 需 180 日新高 + 余量 | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `B2_HARD_PTS` | 20.0 | 每条硬条件（B1 先行/涨幅达标/放量/J 达标）分值 | needs_work；R7 | 未校准 |
| `b2_surge_factor` | `B2_NO_UPPER_SHADOW_PTS` | 20.0 | 无上影线加分 | needs_work；R7 | 未校准 |
| `bottom_patterns` | `WBOT_TOL_PCT` | 3.0 | 待回测：双底两低点相近容差%（镜像双顶 DIST_DOUBLE_TOP_TOL） | untested；R2 | 未校准 |
| `bottom_patterns` | `WBOT_MIN_GAP` | 3 | 待回测：两底最少间隔根数（镜像双顶 p2-p1>=3） | untested；R2 | 未校准 |
| `bottom_patterns` | `WBOT_FRACTAL` | 2 | 待回测：分型左右确认根数（镜像双顶的 2 左 2 右窗口） | untested；R2 | 未校准 |
| `bottom_patterns` | `WBOT_WINDOW` | 20 | 待回测：双底观察窗口（近 N 根，镜像 DIST_TOP_WINDOW×2） | untested；R2 | 未校准 |
| `bottom_patterns` | `WBOT_DRAWDOWN_PCT` | 40.0 | - | untested；R2 | 未校准 |
| `bottom_patterns` | `WBOT_VOL_RATIO` | 2.0 | 待回测：底部放量=量 ≥ 250 日均量×此值（同 check_bottom_volume BOTTOM_VOL_RATIO） | untested；R2 | 未校准 |
| `bottom_patterns` | `WBOT_VOL_WIN` | 5 | 待回测：第二底之后几根内出现放量（含当日） | untested；R2 | 未校准 |
| `bottom_patterns` | `RF_WINDOW` | 10 | 待回测：底部区间窗口（镜像 DIST_TOP_WINDOW） | untested；R2 | 未校准 |
| `bottom_patterns` | `RF_BOTTOM_FRAC` | 1.02 | 待回测：底部=窗口最低 ≤ 近60根最低×此值（镜像 near_top 0.98） | untested；R2 | 未校准 |
| `bottom_patterns` | `RG2_WINDOW` | 30 | 观察窗 N=30 根 | untested；R2 | 候选（v0.532，owner 2026-11-16 定稿，抽检图集 `red_green_v2__mining_v1`） |
| `bottom_patterns` | `RG2_VOL_K` | 1.0 | 阳线总量 > 阴线总量 × k | untested；R2 | 候选（v0.532，owner 2026-11-16 定稿，抽检图集 `red_green_v2__mining_v1`） |
| `bottom_patterns` | `RG2_BURST` | 3.0 | 爆量 = 单根量 ≥ 窗口均量 × 此值；爆量根不参与三项比较，单独标注 | untested；R2 | 候选（v0.532，owner 2026-11-16 定稿，抽检图集 `red_green_v2__mining_v1`） |
| `bottom_patterns` | `RG2_STAGE_LOOKBACK` | 120 | 阶段高点回看根数 | untested；R2 | 候选（v0.532，owner 2026-11-16 定稿，抽检图集 `red_green_v2__mining_v1`） |
| `bottom_patterns` | `RG2_BOTTOM_DD_PCT` | 30.0 | 阶段底部 = 收盘距近 120 根最高价回撤 ≥ 此值（待校准） | untested；R2 | 候选（v0.532，owner 2026-11-16 定稿，抽检图集 `red_green_v2__mining_v1`） |
| `bottom_patterns` | `RG2_TOP_NEAR_PCT` | 5.0 | 阶段顶部 = 收盘距近 120 根最高价 ≤ 此值 | untested；R2 | 候选（v0.532，owner 2026-11-16 定稿，抽检图集 `red_green_v2__mining_v1`） |
| `fundamentals` | `DIXI_NET_PROFIT_YOY` | 100.0 | 待回测：业绩预增代理阈值（净利同比%） | untested | 未校准 |
| `fundamentals` | `REPORT_MAX_AGE_DAYS` | 270 | - | untested | 未校准 |
| `perfect_b1_fit` | `FIT_J_DEEP` | 0.0 | J<0 → 2 分 | candidate；R2 | 未校准 |
| `perfect_b1_fit` | `FIT_J_MID` | 7.0 | J<7 → 1.5 分；J<13 → 1 分 | candidate；R2 | 未校准 |
| `perfect_b1_fit` | `FIT_NEAR_LINE_PCT` | 3.0 | 收盘距 QSX 或 DKS ≤3% → 2 分（回踩贴线） | candidate；R2 | 未校准 |
| `perfect_b1_fit` | `FIT_NEAR_LINE_MAX_PCT` | 6.0 | ≤6% → 1 分 | candidate；R2 | 未校准 |
| `perfect_b1_fit` | `FIT_SHRINK_DEEP` | 0.5 | 回调段/上涨段均量 ≤0.5 → 2 分 | candidate；R2 | 未校准 |
| `perfect_b1_fit` | `FIT_SHRINK_MID` | 0.8 | ≤0.8 → 1 分 | candidate；R2 | 未校准 |
| `perfect_b1_fit` | `FIT_DKS_SLOPE_DAYS` | 5 | DKS 上行判断窗口（DKS[t] > DKS[t-N]） | candidate；R2 | 未校准 |
| `platform_pullback` | `PLATFORM_LOOKBACK` | 60 | 平台回看窗 | needs_work；R2 | 未校准 |
| `platform_pullback` | `BREAKOUT_WITHIN` | 40 | 突破须发生在近 40 日内 | needs_work；R2 | 未校准 |
| `platform_pullback` | `PULLBACK_WITHIN` | 15 | 回踩发生在近 15 日内 | needs_work；R2 | 未校准 |
| `platform_pullback` | `TOUCH_TOL` | 0.03 | 平台高点容差(相近高点) | needs_work；R2 | 未校准 |
| `platform_pullback` | `BREAKOUT_BUF` | 0.01 | 收盘站上平台高的缓冲 | needs_work；R2 | 未校准 |
| `platform_pullback` | `LEAVE_MIN` | 0.05 | 突破后最高收盘至少离开平台高的幅度 | needs_work；R2 | 未校准 |
| `platform_pullback` | `NEAR_TOL` | 0.06 | 回踩低点距平台高的"附近"上界 | needs_work；R2 | 未校准 |
| `platform_pullback` | `BREAK_TOL` | 0.02 | 允许刺破深度(不破=不低于 ×(1-2%)) | needs_work；R2 | 未校准 |
| `qsx_resonance` | `LOOKBACK` | 60 | - | needs_work；R23 | 未校准 |
| `qsx_resonance` | `MIN_EVENTS` | 2 | - | needs_work；R23 | 未校准 |
| `qsx_resonance` | `BOUNCE_BARS` | 5 | - | needs_work；R23 | 未校准 |
| `qsx_resonance` | `BOUNCE_PCT` | 0.03 | - | needs_work；R23 | 未校准 |
| `qsx_resonance` | `VOL_MA` | 5 | - | needs_work；R23 | 未校准 |
| `rsi_state` | `BULL_RANGE_LOW` | 40.0 | 牛市区间下沿：回调低点 ≥ 此值 | candidate；R8,R20,R21 | 未校准 |
| `rsi_state` | `BULL_RANGE_CONFIRM` | 70.0 | 牛市确认：区间内曾突破此值 | candidate；R8,R20,R21 | 未校准 |
| `rsi_state` | `BEAR_RANGE_HIGH` | 60.0 | 熊市区间上沿：反弹高点 ≤ 此值 | candidate；R8,R20,R21 | 未校准 |
| `rsi_state` | `BEAR_RANGE_LOW` | 30.0 | 熊市区间：低点曾 < 此值 | candidate；R8,R20,R21 | 未校准 |
| `rsi_state` | `DEEP_OVERSOLD` | 25.0 | 深水区 | candidate；R8,R20,R21 | 未校准 |
| `rsi_state` | `REGIME_LOOKBACK` | 60 | 区间行为的观察窗 | candidate；R8,R20,R21 | 未校准 |
| `rsi_state` | `REGIME_EXCLUDE_RECENT` | 12 | - | candidate；R8,R20,R21 | 未校准 |
| `rsi_state` | `DIV_LOOKBACK` | 30 | 背离观察窗 | candidate；R8,R20,R21 | 未校准 |
| `rsi_state` | `DIV_MIN_GAP` | 5 | 两个低点之间至少间隔的根数 | candidate；R8,R20,R21 | 未校准 |
| `rsi_state` | `RSI_MIN_BARS` | 40 | - | candidate；R8,R20,R21 | 未校准 |
| `rsi_state` | `RSI_STATE_SCORE_BUY_MIN` | 60.0 | - | candidate；R8,R20,R21 | 未校准 |
| `s_shape` | `SSHAPE_MIN_BARS` | 60 | 计算 S_shape 所需最少 K 线（含 60 日均量/均线） | needs_work；R2 | 未校准 |
| `s_shape` | `VCP_LEG` | 10 | 每段观察长度（日） | needs_work；R2 | 未校准 |
| `s_shape` | `VCP_RANGE_STRONG` | 0.5 | 近段日均振幅/前段 ≤0.5 强收敛 | needs_work；R2 | 未校准 |
| `s_shape` | `VCP_RANGE_MILD` | 0.75 | ≤0.75 温和收敛 | needs_work；R2 | 未校准 |
| `s_shape` | `VCP_VOL_STRONG` | 0.6 | 近段/前段均量 ≤0.6 强缩量 | needs_work；R2 | 未校准 |
| `s_shape` | `VCP_VOL_MILD` | 0.8 | ≤0.8 温和缩量 | needs_work；R2 | 未校准 |
| `s_shape` | `PIVOT_BASE_WIN` | 20 | 枢轴＝近 N 日（不含当日）最高价 | needs_work；R2 | 未校准 |
| `s_shape` | `PIVOT_BREAK_PCT` | 2.0 | 突破枢轴且超出 ≤2% ＝ 刚突破（最佳） | needs_work；R2 | 未校准 |
| `s_shape` | `PIVOT_NEAR_PCT` | 3.0 | 收盘距枢轴 ≤3% ＝ 邻近 | needs_work；R2 | 未校准 |
| `s_shape` | `PIVOT_MID_PCT` | 6.0 | ≤6% ＝ 尚可 | needs_work；R2 | 未校准 |
| `s_shape` | `VOL_SURGE_RATIO` | 1.2 | 当日量/20日均量 ≥1.2 放量 | needs_work；R2 | 未校准 |
| `s_shape` | `POCKET_LOOKBACK` | 10 | - | needs_work；R2 | 未校准 |
| `s_shape` | `POCKET_RECENT` | 3 | 近 N 日内出现即计分 | needs_work；R2 | 未校准 |
| `s_shape` | `OVERHEAD_WIN` | 60 | - | needs_work；R2 | 未校准 |
| `s_shape` | `DELTA_LOW_POS_PCT` | 15.0 | 低位反包：收盘处于近20日低点上方 ≤此% 视为低位 | needs_work；R2 | 未校准 |
| `s_shape` | `PEN_BIG_BEAR_FRAC` | 0.5 | 大阴＝跌幅 ≥ 涨跌幅制度 ×0.5 | needs_work；R2 | 未校准 |
| `s_shape` | `PEN_VOL_RATIO` | 1.2 | 放量＝量/前5日均量 ≥1.2 | needs_work；R2 | 未校准 |
| `s_shape` | `PEN_RECOVER_VOL` | 0.9 | 收复但量 <0.9× → −10；≥0.9× → −5 | needs_work；R2 | 未校准 |
| `s_shape` | `PEN_FRONTHIGH_PCT` | 5.0 | 前高距收盘 ≤此% → 惩罚减半 | needs_work；R2 | 未校准 |
| `s_shape` | `SSTAR_STRONG` | 65.0 | - | needs_work；R2 | 未校准 |
| `s_shape` | `SSTAR_MID` | 40.0 | - | needs_work；R2 | 未校准 |
| `s_shape` | `HOURGLASS_LOOKBACK` | 60 | 沙漏事件回看根数（同 SB 异动后的 B1） | needs_work；R2 | 候选（owner 2026-11-17，抽检图集 `hourglass_b1__mining_v1`） |
| `sector_phase` | `PHASE_LOOKBACK` | 60 | 顶背离/三打回看窗口(交易日) | active；R4 | 未校准 |
| `sector_phase` | `PHASE_FRACTAL` | 2 | 摆动高点左右确认根数 | active；R4 | 未校准 |

### P2（67 个参数）

| 因子 | 参数 | 当前值 | 代码注释 | 因子状态 / 效果证据 | 定义校准 |
|---|---|---|---|---|---|
| `b1_pullback_fit` | `B1PB_TREND_MA` | 60 | 趋势未破：收盘 > MA60（-1% 容差） | needs_work；R2 | 未校准 |
| `b1_pullback_fit` | `B1PB_MA10_BAND` | (-12.0, 1.0) | - | needs_work；R2 | 未校准 |
| `b1_pullback_fit` | `B1PB_PRIOR_GAIN` | 25.0 | 前有涨幅：波段起涨 ≥25% | needs_work；R2 | 未校准 |
| `b1_pullback_fit` | `B1PB_PULL_BAND` | (4.0, 35.0) | 回调温和：回调深度 4~35% | needs_work；R2 | 未校准 |
| `b1_pullback_fit` | `B1PB_VOL_MA5_MAX` | 1.3 | 缩量企稳：B1日量 ≤1.3×近5日均量 | needs_work；R2 | 未校准 |
| `b1_pullback_fit` | `B1PB_JMIN_MAX` | 13.0 | J超卖重置：回调段最低 J ≤13 | needs_work；R2 | 未校准 |
| `b1_pullback_fit` | `B1PB_BODY_MAX` | 4.5 | 小实体企稳：B1日实体 ≤4.5% | needs_work；R2 | 未校准 |
| `b1_pullback_fit` | `B1PB_HIT_MIN` | 6 | 命中门槛：7项中 ≥6 项 | needs_work；R2 | 未校准 |
| `main_rally_factor` | `FLOW_WIN` | 15 | D1/D2 的统计窗（原文 15；文章说想更灵敏可改 10） | untested；R8 | 未校准 |
| `main_rally_factor` | `FLOW_THRESHOLD` | 0.8 | 主升占比阈值（原文 0.8；想过滤弱势反弹可上调 0.85） | untested；R8 | 未校准 |
| `main_rally_factor` | `RSI_N` | 7 | 原文 D5 用 7 日 | untested；R8 | 未校准 |
| `main_rally_factor` | `RSI_OVERSOLD` | 20.0 | 原文 REF(D5,1)<20（震荡市可放宽到 30） | untested；R8 | 未校准 |
| `main_rally_factor` | `CCI_N` | 14 | 原文 AVEDEV/MA 都用 14 | untested；R8 | 未校准 |
| `main_rally_factor` | `CCI_EXTREME` | -100.0 | 原文 偏差<-100（想提高安全边际可下调到 -120） | untested；R8 | 未校准 |
| `main_rally_factor` | `MAIN_RALLY_MIN_BARS` | 60 | 原文 BARSCOUNT(C)>60 | untested；R8 | 未校准 |
| `qn_adx_extreme` | `QN_ADX_EXTREME` | 60.0 | 待回测：极端位阈值（源规则「ADX 大于等于 60」） | untested | 未校准 |
| `qn_adx_extreme` | `QN_DIV_WINDOW` | 20 | 待回测：背离分型观察窗（近 N 根，同 bottom_patterns WBOT_WINDOW） | untested | 未校准 |
| `qn_adx_extreme` | `QN_DIV_FRACTAL` | 2 | 待回测：分型左右确认根数（同 bottom_patterns WBOT_FRACTAL） | untested | 未校准 |
| `qn_box_target` | `QN_BASE_WIN` | 60 | 待回测：波段低点观察窗（转译定窗 60 日，spec 表未要求 20/60 取大） | needs_work；R31 | 未校准 |
| `qn_box_target` | `QN_COEF_BIG` | 1.3 | 待回测：大波段系数（源规则 1.3） | needs_work；R31 | 未校准 |
| `qn_box_target` | `QN_COEF_SMALL` | 1.26 | 待回测：小波段系数（源规则 1.26/1.2 取 1.26） | needs_work；R31 | 未校准 |
| `qn_box_target` | `QN_COEF_HALF` | 1.15 | 待回测：半格压力位（上沿 1.3 平移半格 = 低点 ×1.15） | needs_work；R31 | 未校准 |
| `qn_box_target` | `QN_NEAR_PCT` | 3.0 | 待回测：目标压力区容差%（距目标位 ≤ 此值视为进入压力区） | needs_work；R31 | 未校准 |
| `qn_bullish_engulf` | `QN_VOL_EDGE` | 1.1 | 待回测：量能「略大于左侧」的下界倍数（源规则非倍量） | needs_work；R31 | 未校准 |
| `qn_kdj_neg_day` | `QN_CROSS_LOOKBACK` | 15 | 待回测：向前找死叉的最大根数 | needs_work；R31 | 未校准 |
| `qn_kdj_neg_day` | `QN_J_CROSS_MIN` | 50.0 | 待回测：死叉时 J 的跌透阈值（源规则「死叉 J 值大于 50」） | needs_work；R31 | 未校准 |
| `qn_kdj_neg_day` | `QN_BUY_DAYS` | (3, 5) | 待回测：负值第 3 天=可选买点、第 5 天=极致买点 | needs_work；R31 | 未校准 |
| `qn_kdj_neg_day` | `QN_KD20_LEVEL` | 20.0 | 待回测：KD 金叉低位阈值（源规则「20 以下的 KD 金叉」） | needs_work；R31 | 未校准 |
| `qn_ma144_launch` | `QN_MA_WIN` | 144 | 待回测：趋势均线窗口（源规则 144 日线，与 v0.7 中长期口径一致） | needs_work；R31 | 未校准 |
| `qn_ma144_launch` | `QN_RISE_WIN` | 5 | 待回测：上翘确认根数（近 N 根均线上移） | needs_work；R31 | 未校准 |
| `qn_ma144_launch` | `QN_FLAT_WIN` | 20 | 待回测：上翘之前的走平观察窗 | needs_work；R31 | 未校准 |
| `qn_ma144_launch` | `QN_FLAT_PCT` | 0.02 | 待回测：走平容差（窗内相对变化） | needs_work；R31 | 未校准 |
| `qn_ma144_launch` | `QN_PULLBACK_PCT` | 10.0 | 待回测：回踩均线容差 ±%（源规则「正负10%以内」） | needs_work；R31 | 未校准 |
| `qn_ma144_launch` | `QN_LIMIT_TOL` | 0.5 | 待回测：涨停判定容差 pp（封板认定） | needs_work；R31 | 未校准 |
| `qn_ma144_launch` | `QN_SURGE_MULT` | 2.0 | 待回测：倍量阈值（源规则「倍量」） | needs_work；R31 | 未校准 |
| `qn_ma144_launch` | `QN_MA25_WIN` | 25 | 待回测：线上阴线的「线」（25 日线，源规则同一体系） | needs_work；R31 | 未校准 |
| `qn_ma25_state` | `QN_MA25_WIN` | 25 | 待回测：多空分界均线窗口（源规则=25 日线，与 v0.7 口径一致） | needs_work；R31 | 未校准 |
| `qn_ma_converge` | `QN_MA_WINDOWS` | (5, 10, 25) | 带宽只算三线（v0.200 校准：144 是托底前提非绳身） | needs_work；R31 | 未校准 |
| `qn_ma_converge` | `QN_CONVERGE_PCT` | 0.05 | 待回测：粘合带宽上限（三线 max−min / mid） | needs_work；R31 | 未校准 |
| `qn_ma_converge` | `QN_CONVERGE_BARS` | 10 | 待回测：收拢持续最少根数（「成一股绳」） | needs_work；R31 | 未校准 |
| `qn_ma_converge` | `QN_BREAK_SURGE` | 1.5 | 待回测：发散日放量倍数（第一根放量阳线） | needs_work；R31 | 未校准 |
| `qn_ma_converge` | `QN_MA144_RISE_WIN` | 5 | 待回测：MA144 上翘确认根数 | needs_work；R31 | 未校准 |
| `qn_macd_bar_shift` | `QN_SHRINK_BARS` | 3 | 待回测：连续缩短根数（末 N 根逐根收缩） | needs_work；R31 | 未校准 |
| `qn_macd_bar_shift` | `QN_LOW_WIN` | 5 | 待回测：买小绿的「前低」参照窗（此前 N 根最低收盘） | needs_work；R31 | 未校准 |
| `qn_macd_bar_shift` | `QN_HIGH_WIN` | 5 | 待回测：卖小红的「滞涨」参照窗（此前 N 根最高收盘） | needs_work；R31 | 未校准 |
| `qn_shrink_limit_up` | `QN_LIMIT_TOL` | 0.5 | 待回测：涨停判定容差 pp（封板认定，同 qn_ma144_launch） | untested | 未校准 |
| `qn_shrink_limit_up` | `QN_SHRINK_PCT` | 0.7 | 待回测：缩量上界（当日量 < 前日×0.7） | untested | 未校准 |
| `qn_shrink_limit_up` | `QN_SHRINK_FLOOR` | 0.5 | 待回测：缩量下界（≤前日 1/2 = 缩过头，弱势） | untested | 未校准 |
| `qn_shrink_limit_up` | `QN_PREV_SURGE` | 2.0 | 待回测：前序放量阴线的量能门槛（≥20 日均量×2） | untested | 未校准 |
| `qn_shrink_limit_up` | `QN_LOOKBACK` | 10 | 待回测：前序放量阴线回溯窗 | untested | 未校准 |
| `qn_shrink_limit_up` | `QN_NEAR_MA_PCT` | 10.0 | 待回测：贴近均线的距离上限%（25/60/144 任一） | untested | 未校准 |
| `qn_three_red` | `QN_MIN_DAY_BARS` | 35 | 待回测：日线 MACD 可信所需最少日 K 数 | needs_work；R31 | 未校准 |
| `qn_three_red` | `QN_MIN_WEEK_BARS` | 35 | 待回测：周线 MACD 可信所需最少周 K 数 | needs_work；R31 | 未校准 |
| `qn_three_red` | `QN_MIN_MONTH_BARS` | 20 | 待回测：月线 MACD 可信所需最少月 K 数（≈400+ 交易日） | needs_work；R31 | 未校准 |
| `qn_volume_surge_cut` | `QN_SURGE_MULT` | 2.0 | 待回测：倍量阈值（源规则「成交量是昨日二倍」） | needs_work；R31 | 未校准 |
| `qn_volume_surge_cut` | `QN_BASE_FLAT_WIN` | 20 | 待回测：大级别均线走平观察窗 | needs_work；R31 | 未校准 |
| `qn_volume_surge_cut` | `QN_BASE_FLAT_PCT` | 0.02 | 待回测：走平容差（窗内相对变化） | needs_work；R31 | 未校准 |
| `qn_volume_surge_cut` | `QN_DIVERGE_WARN_PCT` | 0.3 | 待回测：鬼招手近似（MA5 相对 MA144 的间距） | needs_work；R31 | 未校准 |
| `qn_weekly180_setup` | `QN_MA_WEEK` | 180 | 待回测：源规则 180 周均线 | untested | 未校准 |
| `qn_weekly180_setup` | `QN_BELOW_BARS` | 52 | 待回测：大悬空=近 N 根周 K 全在线下（≈1 年，「时间跨度足够长」） | untested | 未校准 |
| `qn_weekly180_setup` | `QN_DRAWDOWN_PCT` | 50.0 | 待回测：相对历史最高收盘的最小回撤%（源规则「跌幅超 50%」） | untested | 未校准 |
| `qn_weekly180_setup` | `QN_HUGE_VOL` | 3.0 | 待回测：脚踩巨量 = 悬空期内周量 ≥ 此前均量 ×此值 | untested | 未校准 |
| `qn_weekly180_setup` | `QN_BREAK_SURGE` | 1.5 | 待回测：突破周放量倍数（相对前周） | untested | 未校准 |
| `reversal_quality` | `REVK_VOL_RATIO` | 0.5 | 量比 <= 50% | needs_work；R2 | 未校准 |
| `reversal_quality` | `REVK_VOL_PCTILE` | 0.1 | 20 日量分位 <= 10% | needs_work；R2 | 未校准 |
| `reversal_quality` | `REVK_CHG_PCT` | 2.0 | 对称，与 live 默认值同；刻意不跟随 B1_REVK_CHG_PCT | needs_work；R2 | 未校准 |
| `reversal_quality` | `REVK_AMP_PCT` | 7.0 | 振幅 <= 7% | needs_work；R2 | 未校准 |
