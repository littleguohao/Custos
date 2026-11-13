# 信号参数定义校准清单（owner 2026-11-03）

> **口径**：本轮只做 **A 类「定义校准」**——核对每个检测器是否检测出规则原文说的那个形态（对照 owner 标注样本 + 抽检图），
> **不拿收益寻优**；B 类「效果校准」（哪个参数值赚钱）等 A 全部做完再议。**live 用到的先做**（P0 → P1 → P2）。
> **在生产机运行**（真实全量数据 + owner 标注）；开发机只做工具与合成钉测。
> 清单由 `scripts/dev/calibration_inventory.py` 生成；「定义校准」列人工维护（未校准 / 进行中 / ✅ commit xxx），
> `--check` 钉测要求 P0/P1 参数全部在册（新增参数不登记即红）。

## 现状

- **P0H 持仓大形态 18 个**（17 个判定参数 + `QFQ_LOAD_BARS` 加载深度，见下节）；另 229 个模块级数值参数：**P0 live 判定 70 个**（进技术分 / 进池门 / 封顶，9 个模块）、
  **P1 live 展示 92 个**（1800 证据标注，不进判定）、P2 研究 67 个。
- **定义校准：0 / 229**。现有研究（R2/R6/R19/R31/R42 等）测的都是**效果**，没有一个检测器做过「是否测对了形态」的核对。
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
   **工具化（v0.517）**：以上口径由 `research/remark_rules.py`（单一来源）+ `buypoint_practice --mode review` 执行；买点练习出题用 `--mode make`（只抽挖掘段、前向 20 日不出段）。备注仍只写形态、不写后来涨跌（第 1 条）。

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

### b1_structure（2026-11-13，mining_v1 重标 105 张 → v2 候选 `708e6ed`）

- owner 判断评估（口径 A / D）：五日战法判错 15 条全部站得住（检测器过松）；repair_signals 6 条判定点反了，owner 确认后更正（`labels-3.csv`）；
  非一波流错因分「段识别漏判 / 放量门槛过低 / 原文无阈值 / owner 新增条件」四类。口径 B（盲标复测）待做。
- **五日战法退出 B1 打分**（owner 2026-11-13）：v2 定义对照 owner 33/33 一致，但挖掘段 J<13 抽样 1,689 根 0 命中（v1 命中 14 根且均为误判）——
  五日战法是看**前方低位是否有爆量**的追涨买法，不是 B1 当根形态。处理：`SCREEN_FORMULA_REGISTRY.json` 的 `scoring.weights.five_day_entry` 8 → 0，
  检测器保留（CZ 与证据列仍可用）。**随本模块 v2 升版一起生效**（一次 `--bump`，避免 V0 分缓存在第二批前多次失效）。
- 新特征想法（owner，未立项）：「B1 之前的低位是否出现过爆量」——与 `volume_detectors.bottom_volume` / `ignition` 相近，作右尾特征候选时再预注册。
- 待 owner 定：一波流结局（revoked / insufficient）、顶部放量大阴的比较基准、止损参考位与流动性两个口径。

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
| `technical_monitor` | `BOX_UPPER_Q` | 0.85 | 箱体上沿 = 窗口最高价分位 | - | 未校准 |
| `technical_monitor` | `BOX_LOWER_Q` | 0.15 | 箱体下沿 = 窗口最低价分位 | - | 未校准 |
| `technical_monitor` | `BOX_SHORT` | 20 | 短箱体窗口（持仓「跌破 20 日箱体」） | - | 未校准 |
| `technical_monitor` | `BOX_LONG` | 60 | 长箱体窗口 | - | 未校准 |
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

### P0（70 个参数）

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
| `b1_structure` | `LIQUIDITY_WIN` | 20 | 待回测：近N日均成交额窗口 | candidate | 未校准 |
| `b1_structure` | `STOP_LOOKBACK` | 10 | 建议止损位：近10日最低价 | candidate | 未校准 |
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
| `entry_patterns` | `RS_STRONG_PP` | 3.0 | 20日相对强度 >= +3pp | candidate | 未校准 |
| `ignition` | `ZX_CROSS_RECENT` | 10 | 待回测：知行金叉"近N日"窗口 | candidate | 未校准 |
| `ignition` | `IGNITION_WINDOW` | 10 | 待回测：放量点火扫描窗口（日） | candidate | 未校准 |
| `ignition` | `IGNITION_VOL_RATIO` | 1.5 | 待回测：点火量比（当日量/前5日均量） | candidate | 未校准 |
| `ignition` | `IGNITION_MIN_GAIN` | 3.0 | 待回测：点火单日涨幅%下限 | candidate | 未校准 |
| `ignition` | `PULLBACK_LOOKBACK` | 20 | 待回测：回调缩量企稳观察窗口（日） | candidate | 未校准 |
| `ignition` | `PULLBACK_MIN_DROP` | 3.0 | 待回测：距窗口高点回撤%下限 | candidate | 未校准 |
| `ignition` | `PULLBACK_VOL_RATIO` | 0.85 | 待回测：回调段/上涨段均量上限 | candidate | 未校准 |
| `macd_technics` | `MACD_SWING_FRACTAL` | 2 | 摆动高/低点分型：左右各 N 根确认 | candidate | 未校准 |
| `macd_technics` | `MACD_DIV_LOOKBACK` | 60 | 背离观察窗口（日） | candidate | 未校准 |
| `macd_technics` | `MACD_OVEREXT_PCTL` | 0.9 | 开口/空间拐离：/DIF/ 近 120 日分位上限 | candidate | 未校准 |
| `macd_technics` | `MACD_OVEREXT_WIN` | 120 | 拐离分位窗口（日） | candidate | 未校准 |
| `volume_detectors` | `VOLUME_SUSTAIN_WINDOW` | 13 | 量能持续性窗口（CZ §14.6：7-13日） | candidate | 未校准 |
| `volume_detectors` | `VOLUME_SUSTAIN_MIN_POST_DAYS` | 7 | 待回测参数：峰值日后确认主线最少观察日数 | candidate | 未校准 |
| `volume_detectors` | `VOLUME_SUSTAIN_RATIO` | 0.55 | 峰值55%（CZ §14.6） | candidate | 未校准 |
| `volume_detectors` | `VOLUME_SUSTAIN_RETREAT_DAYS` | 3 | 连续N日<峰值55%判撤退（CZ §14.6） | candidate | 未校准 |
| `volume_detectors` | `LEADER_VOL_BASE_DAYS` | 20 | 龙头量能基准窗口（CZ §九） | candidate | 未校准 |
| `volume_detectors` | `LEADER_VOL_RATIO` | 1.7 | 地量1.7倍（CZ §九） | candidate | 未校准 |
| `volume_detectors` | `THREE_LOWS_DRAWDOWN_PCT` | 40.0 | 待回测参数：三低之低价格（自250日高点回撤%） | candidate | 未校准 |
| `volume_detectors` | `BOTTOM_VOL_RATIO` | 2.0 | 待回测参数：底部巨量（≥250日均量×2，CZ §14.6） | candidate | 未校准 |
| `volume_detectors` | `BOTTOM_NO_NEW_LOW_DAYS` | 20 | 待回测参数：不再创新低观察窗口 | candidate | 未校准 |
| `volume_detectors` | `CZ_MIN_BARS` | 250 | CZ 三低/底部巨量最少K线数（不足→available=false） | candidate | 未校准 |
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

### P1（92 个参数）

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
