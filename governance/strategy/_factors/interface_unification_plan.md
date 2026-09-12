# 因子层接口统一立项设计稿（TODO #67）

> **上下文**：**通用因子**（跨策略可复用）的接口治理　｜　**执行者**：脚本（迁移期）+ owner（裁决点）　｜　**状态**：✅ 已实施（B0-B4 五批全落地，v0.217-v0.223；遗留验证见 §8）
> **版本**：v0.216（2026-09-12 初稿）→ v0.223 实施回填（2026-09-13）　｜　**代码依赖**：现状清点见 §2（只读，不动实现）
> **索引**：[`README.md`](README.md)　·　关联：TODO #67、`core/factors/__init__.py` 头部清点（2026-08-06）、
> R31（qn 批的 registry 驱动先例）、R32/TODO #73（谱系字段已落地，晋级通道的另一半）
> ⚠️ **统一是语义改动**（会改 live 选股行为）：本稿只做设计与批次规划，任何实现
> 必须等 owner 拍板立项 + 回测验证后分批切换，不能搭便车机械改名（TODO #67 原话）。

## 实施回填（2026-09-13，v0.217-v0.223）

五批全部落地，每批独立 commit、批后全量绿（ruff/mypy/audit 同步过）：

| 批 | commit | 版本 | 实际落地 |
|---|---|---|---|
| B0 | cdef99d | v0.217 | qsx_resonance 补 FACTOR（needs_work+evidence_only，release 19→20）；44 因子 characterization 快照钉测入库（TestCharacterizationSnapshot） |
| B1 | 7a6543c | v0.218 | 9 selector+baseline 的 SCORERS 注册表直通（同一函数对象）；`_sc_kdj_j` 别名保留（_SCORER_PRECOMPUTE 身份键）；直通钉测用「同模块同名+行为等价」而非 `is`（importlib.reload 顺序污染是本仓库已知坑） |
| B2 | 1f93569 | v0.219 | platform_pullback/qsx_resonance detect 别名、bottom_patterns/sector_phase detect 门面、s_shape 的 _sc_s_shape 上移为模块 score()；live 只改 import 点名；快照更新 2 |
| B3 | 59fe4a1 / 9ce4edd / f2cb82c | v0.220/221/222 | 三裁决点全部按「行为零变化」口径落地，见 §7 回填 |
| B4 | 8143871 | v0.223 | weekly_j/macd_technics/ignition detect 别名；volume_detectors/b1_structure detect 打包门面（enrich 热路径改打包，ec.* 转出通道保留）；快照更新 2 |

### 勘误（设计稿与代码现状的出入，以代码为准）

1. **B2 范围**：perfect_b1_fit（compute 要吃 daily_j/zx/pullback 上下文）与
   fundamentals（吃财务数据不吃 df）签名不适配 `detect(df)` 规范形，未收口 ——
   留待 ctx 规范专项。qsx_resonance（B0 新登记）补进 B2 收口范围。
2. **B4 范围**：entry_patterns/j_low_gate/capital_intent/sector_mainstream 是
   标量/记录/板块成员输入型判定器，不吃 df —— 规范 df 入口不适用，与
   perfect_b1_fit/fundamentals 合并为「ctx 输入域规范」遗留专项（见 §8 遗留）。
   ignition/macd_technics/weekly_j 按别名收口；repair_signals（吃 index_df+
   kdj_state）不进 b1_structure 打包门面。
3. **快照机制**：驱动优先 score/detect 规范槽（与消费方一致）；新增规范槽的
   因子哈希随批更新并带版本注记（bottom_patterns/s_shape/rsi_state/
   volume_detectors/b1_structure 共 5 次），其余 39 个哈希全程未动 ⇒
   逐位冻结成立。

### §7 裁决点回填（落地口径）

1. **b1_dual 内联判据**：选「先对齐因子逻辑再切」的行为零变化路径——谓词
   `breakout_pullback_hit()` 单源化（两边默认值逐字相同 ⇒ live 逐位不变，
   漂移通道关闭）；signal_labels 手写判据已删。
2. **b2 合成 score 上移**：`b2_score()` 进因子模块（具名常量），live **不加**
   b2_score 标签列（候选表 schema 不变 ⇒ live 逐位不变）。
3. **rsi_state 阈值单源化**：`RSI_STATE_SCORE_BUY_MIN=60` 进因子模块 +
   score() 规范入口上移；live 的 rsi_ideal_b1 保持 strong∧deep 布尔合取
   （阈值口径不改 ⇒ live 逐位不变）。

三条都是「单源化但语义不变」——裁决点里唯一被采纳的行为变化是**零个**；
若日后要动阈值/口径本身，按新裁决单独立项。

> **索引**：[`README.md`](README.md)　·　关联：TODO #67、`core/factors/__init__.py` 头部清点（2026-08-06）、
> R31（qn 批的 registry 驱动先例）、R32/TODO #73（谱系字段已落地，晋级通道的另一半）
> ⚠️ **统一是语义改动**（会改 live 选股行为）：本稿只做设计与批次规划，任何实现
> 必须等 owner 拍板立项 + 回测验证后分批切换，不能搭便车机械改名（TODO #67 原话）。

## 主题

因子层现存三套调用约定 + 两种消费方式，且三个因子被 live 与研究**各自包装一遍**。
本稿回答：①现状到底分歧在哪（逐因子、逐行实据）；②目标单一接口长什么样；
③怎么证明迁移零行为变化；④分几批、每批怎么回滚；⑤进化引擎晋级通道怎么落在
统一接口上；⑥哪些点必须 owner 裁决。

## §1 为什么现在立项

- **接入面阻塞**：进化引擎（v0.202-0.213）已能源源产出 DSL 候选因子；TODO #73
  已把晋级谱系字段（`trajectory_ref`）落到注册表。但候选晋级成因子模块时**该按哪套
  接口写**没有答案 —— 三套并存意味着晋级产物要猜测消费方形态。
- **判据漂移风险实存**：`b1_dual_factor`/`b2_surge_factor`/`rsi_state` 两处各包一遍，
  分歧点见 §2.3 —— 有实锤的重复阈值，也有「一边新增一边没有」的不对称。

## §2 现状清点（实据，2026-09-12 快照）

### 2.1 表①：43 个注册因子的接口形态

按「模块顶层暴露的调用约定」分四类（注册表 `registry()` 只拾取 `score`/`detect`
两个名字的函数，第三类模块的 FACTOR 元数据挂着但规范入口不存在）：

| 形态 | 因子 | 计数 |
|---|---|---|
| `compute_xxx(df) -> dict` | b1_dual_factor（**另有** `detect_breakout_pullback_b1`，一个模块两种形态）、b1_pullback_fit、perfect_b1_fit、s_shape、sector_phase | 5 |
| `detect_xxx(df) -> dict\|None` | b2_surge_factor（3 个 detect：b2/bottom_surge/surge_then_b1）、bottom_patterns、distribution、main_rally_factor、platform_pullback、wave_type、qn_* 全家 12 个 | 18 |
| 模板接口 `score(df, code)` | alpha101、alpha_pvcorr、baseline、kdj_j、low_vol、mcap、momentum、reversal_quality、reversal_quality_inv | 9 |
| **领域命名函数**（check_\*/\*_state/rsi_regime/…，无 `score`/`detect` 顶层入口） | b1_structure、capital_intent、entry_patterns、fundamentals、ignition、j_low_gate、macd_technics、rsi_state、sector_mainstream、volume_detectors、weekly_j | 11 |

注册表外的缺口：`qsx_resonance` **没有 FACTOR 元数据**却被 live `signal_labels`
引用（`signal_labels.py:216`）——注册表登记都还没覆盖到它（统一前先补登记）。

另有研究侧第三套适配层：`research/backtest_factors.py` 的 `_sc_xxx(df, code)`
（SCORERS 19 键）+ gate 适配（ENTRY_GATES 46 键），见表②。

### 2.2 表②：两种消费方式的调用点清单

**live 标注（`pipeline/screening/signal_labels.py`，逐因子 import + `_put` 标签）**：

| 因子模块 | 调用形态 | 位置 |
|---|---|---|
| rsi_state | `rsi_regime` + `rsi_divergence` → 4 个标签 | :97 |
| b2_surge_factor | **`_j_series`（私有函数跨模块 import）** + 3 个 detect | :139 |
| b1_dual_factor | `detect_breakout_pullback_b1` + **手写内联判据**（见 §2.3） | :182 |
| qsx_resonance | `resonance_v2_snapshot`（注册表外模块） | :216 |

**live enrich/打分/板块（enrich_candidates / score_candidates / financials / sector_daily_rank）**：
enrich_candidates 14 个（s_shape、sector_phase、wave_type、perfect_b1_fit、
distribution、bottom_patterns、macd_technics、b1_structure、volume_detectors、
weekly_j、ignition、entry_patterns、j_low_gate、platform_pullback，全部
`check_*/compute_*/detect_*` 直调）；score_candidates 2 个（capital_intent、
fundamentals）；financials 1 个（fundamentals）；sector_daily_rank 1 个
（sector_mainstream）。live 引用合计 19 个模块 = `test_release_set` 钉住的 19 个
release 因子（口径自洽 ✓）。

**研究打分（`backtest_factors.SCORERS`，19 键 → 14 个因子模块）**：
9 个纯 selector 直通 + baseline；s_shape ×3 键（s_shape/s_reversal/invert_s_shape）；
b1_dual_factor ×3 键（b1_dual/long_structure/b1_dual_no_res）；b2_surge_factor（b2）；
rsi_state（rsi_state）；main_rally_factor（main_rally）；platform_pullback（b1_pullback，
经 `_precompute_b1_pullback_series` 旁路）。

**研究 gate（`backtest_factors.ENTRY_GATES`，46 键）**：qn_* ×12（走
`_qn_detect` → `factors.registry()` 的 detect 入口——**registry 驱动消费的先例**
已存在，见 §3）；j_low 系 ×8（weekly_j/indicators）；b2 系 ×6；rsi 系 ×4；
qsx/weekly_qsx 系；platform_pullback；main_rally ×2；qg；reversal_k；none。

**交叉集（live + 研究两侧都消费同一模块）**：b1_dual_factor、b2_surge_factor、
rsi_state、platform_pullback、s_shape、sector_phase、qsx_resonance。其中**明确
被两处各写一遍包装逻辑**的是前三个（TODO #67 点名）。

### 2.3 表③：双重包装因子的分歧点（逐个，到行/阈值）

| 因子 | live 侧判据 | 研究侧判据 | 是否一致 | 分歧点 |
|---|---|---|---|---|
| b1_dual_factor | `signal_labels.py:184-187`：调用方给了 platform_pullback/daily_j 时**手写内联** `close >= platform_high*0.98 and daily_j < 13.0`（绕开因子函数） | `_sc_b1_dual`（:1465）走 `compute_b1_dual` 的双轴分 | **不一致** | 内联判据手抄了 `detect_breakout_pullback_b1` 的默认参数（`ph_tol=0.98`/`j_threshold=13.0`，`b1_dual_factor.py:299`）——因子改默认值，live 标签**静默漂移**；且内联路径跳过因子的 available/结构检查 |
| b2_surge_factor | `signal_labels.py:139-167`：3 个 detect 直出 hit 布尔标签 | `_sc_b2`（:1809）：命中数×20 + 无上影线×20 的合成 score（「原文没给权重」的自造合成） | 不一致（**不对称**，非矛盾） | score 合成权重只存在于研究侧；live 无分、研究无标签口径对照；`_j_series` 私有函数被跨模块 import（改签名即双崩） |
| rsi_state | `signal_labels.py:97-130`：`state=="strong"`、`deep_oversold`、`bullish` 三个布尔标签 | `_sc_rsi_state`（:1904）：`rsi_state_score` 的 50+30+20 权重合成 + `>=60` 可买阈值（模块内自注「权重待回测」） | 不一致（不对称） | 权重与阈值只在研究侧；live 的「strong+deep」复合标签（rsi_ideal_b1）在研究侧没有对应分量 |

结论：**三处分歧都不是「同阈值两边写错」，而是「一边有合成/门槛逻辑、另一边没有或手抄」**
——统一接口的核心收益是把「合成/门槛」收到因子模块内的唯一一处。

## §3 目标形态

### 3.1 单一规范接口（规范，非实现）

每个因子模块顶层暴露**恰好两个**规范入口（按需各一或兼有）：

```python
# 示意（非实现）——形态/状态类：
def detect(df: pd.DataFrame, *, precomputed: dict | None = None) -> dict | None:
    """None = 数据不足/不适用（不参与）；dict 必含 available: bool。
    绝不 raise（热循环纪律）。precomputed 见 §3.2 双形态惯例。"""

# 示意（非实现）——横截面排序类：
def score(df: pd.DataFrame, code: str = "", *, precomputed: dict | None = None) -> dict | None:
    """None = 不参与排序；否则 {"score": float, "suggestion": str, "aux": dict,
    "components": dict}（模板既有约定，不动）。绝不 raise。"""
```

规范要点（全部沿用既有惯例，无新发明）：
- **None 语义**：`None` ≠ `score=0`（缺失不误标，_template.py 已有，不动）；
- **不 raise**：计算期异常吞掉返回 None（SCORERS/ENTRY_GATES 惯例）；
- **min_bars**：FACTOR 元数据已有，接口内部自检；
- **领域命名函数降级为模块内实现细节**（`check_*` 等保留为私有或委托），对外只认
  `detect`/`score` —— 让 `registry()` 拾取的入口就是**全部**消费入口。

### 3.2 双形态 precomputed 惯例（性能纪律，沿用 v0.173/v0.212 已有机制）

- 研究侧逐 bar 热循环的 O(n²)→O(n) 旁路：`backtest_factors._SCORER_PRECOMPUTE`
  按函数身份查表、`precomputed` 第三参点查，只对「从第 0 根开始的前缀切片」有效；
  前缀等价性的判据与钉测口径（`test_scorer_precompute_equivalence.py` +
  v0.212 的 `TestExprPrecompute`）原样沿用。
- 统一后**每个 release 因子的 detect/score 都必须能吃 precomputed**（查不到表 ⇒
  None ⇒ 旧路径，行为逐位一致）——这是性能纪律不是语义变化。

### 3.3 唯一适配层在哪

```
core/factors/xxx.py        ← 唯一实现（detect/score 规范入口 + 合成/门槛逻辑全在这里）
        │
        ├── live 标注：signal_labels 只读 detect/score 返回字典映射 _put 标签
        │   （不许再手写判据；需要新标签 ⇒ 改因子返回字段，不改 signal_labels 算式）
        └── 研究打分：backtest_factors 的 SCORERS/ENTRY_GATES 适配层
            退化为**纯注册**（`SCORERS["x"] = factors.registry()["x"]["score"]` 形态），
            不再有 _sc_xxx 手写包装体；合成分数（如 _sc_b2 的命中数×20）上移进因子模块
```

先例：`ENTRY_GATES` 的 qn_* 12 键已经这么消费（`_qn_detect` → `registry()`），
本设计是把这个形态推广到全部因子。

## §4 等价性策略（先冻结行为，再动结构）

1. **characterization 钉测先行**：对 43 个注册因子在固定合成数据集（`_bars()` 族 +
  边界形态）快照当前 detect/score 输出（逐字段逐位），测试先行入库、先行全绿 ——
  迁移期任何重构必须过这份快照。
2. **逐位等价验证法**：适配层搬迁（`_sc_xxx` 合成逻辑上移至因子模块）用「同一输入
  两路调用逐位比对」钉测（v0.212 `TestExprPrecompute` 同款模式）。
3. **影子对照**：live 侧切换后，1800 候选表标注列与旧口径**逐位对比 N 天**
  （建议 N≥10 个交易日），任一标签翻转即回滚该批。
4. **生产机回测验证清单**：strategy_grid 关键格子（j_low×base_low、
  j_low×pct5、b2 系 gate 各一、rsi 系各一）修复前后逐位一致 + 双窗
  （mining/judgment 两窗读数不变）。

## §5 迁移批次（风险从低到高，每批独立提交、可单独 git revert）

| 批 | 内容 | 风险 | 回滚 |
|---|---|---|---|
| B0 | 登记补齐：qsx_resonance 补 FACTOR 元数据进注册表；43 因子 characterization 快照钉测入库 | 零（纯增量） | revert 单个 commit |
| B1 | 纯研究侧适配层：9 个纯 selector + baseline 的 `_sc_xxx` 改为注册直通（live 无引用，无 live 行为面） | 低（研究侧，快照 + SCORERS 键集合钉测兜底） | revert B1 commit |
| B2 | evidence_only 因子（perfect_b1_fit/platform_pullback/s_shape/sector_phase/bottom_patterns 等）：detect/score 规范入口收口，live 只改 import 点名 | 中（碰 live import 面；影子对照兜底） | revert B2 commit |
| B3 | 双重包装三家（b1_dual_factor/b2_surge_factor/rsi_state）：合成/门槛逻辑上移到因子模块，signal_labels 删手写判据 —— **含 §7 裁决点，owner 逐条拍板后实施** | 高（改 live 行为的真候选） | revert B3 单因子 commit（一因子一个 commit） |
| B4 | gate/scorer 因子收口（volume_detectors/entry_patterns/ignition/macd_technics 等领域命名函数 → 规范入口） | 中 | revert B4 commit |

## §6 与进化引擎晋级通道的衔接

统一接口即 DSL 候选晋级的**落点**。晋级 checklist（候选 → 因子模块）：

1. 轨迹池里 decision=pass 且双窗终审 pass（R32 口径），`trajectory_ref` 记轨迹 id；
2. 因子模块按 §3.1 规范接口写（detect 或 score 其一），FACTOR 元数据填
   `research_ref`（对应研究单元）+ `trajectory_ref` + `free_params`（DSL 的
   `complexity().free_params` 直接可填 —— TODO #74 的准入门同口径）；
3. `free_params >= 4` ⇒ status 升级前必须有在案研究单元背书（注册表测试强制）；
4. 研究侧接入零适配：注册即进 SCORERS/GATES（B1 完成后成立）；live 接入走
   §5 批次纪律 + owner 拍板。

## §7 风险与边界（owner 裁决点逐条列出）

统一后**会改变 live 行为**的点（每条都要 owner 明示裁决，不许默认带过）：

1. **b1_dual 内联判据删除**：signal_labels 的手写 `ph*0.98 ∧ J<13` 删除后，
   标签改吃因子函数返回 —— 若因子内部结构检查比内联严，标签命中率会变（变少）。
   裁决点：接受命中率变化，还是先对齐因子逻辑再切？
2. **b2 合成 score 上移**：研究侧「命中数×20+影线上限」合成权重进入因子模块后，
   live 可选择是否消费该分数（现在 live 没有分数列）。裁决点：live 要不要加
   b2_score 标签列（加 = 候选表 schema 变化）？
3. **rsi_state 权重阈值单源化**：50/30/20 权重与 ≥60 阈值移进因子模块后，
   live 的 rsi_ideal_b1 复合标签是否改用合成阈值（现为 strong∧deep 布尔合取）？
   裁决点：改阈值口径 ⇒ 标签语义变化，须 owner 拍板。

**明确不许动的**：判定语义（detect/score 的数学口径逐位冻结，§4 快照为证）、
出场规则（EXIT_RULES 体系完全不碰）、数据口径（数据源/复权/as-of 一律不动）、
NOT_FOR_LIVE / status / live_use / stage 四维元数据语义。

## §8 验收标准（什么证据集齐了算收敛完成）

代码侧（✅ 已完成，v0.217-v0.223）：
- 规范槽落地 32 个（score 11：9 原生 + s_shape + rsi_state；detect 21：qn 12 +
  B2/B4 收口的 9 个）。无槽 12 个分两类：①标量/记录/板块成员输入型判定器 6 个
  （entry_patterns/j_low_gate/capital_intent/fundamentals/sector_mainstream/
  perfect_b1_fit，不吃 df——勘误 2 的 ctx 专项）；②compute_xxx/detect_xxx 命名
  形态 6 个（b1_dual_factor/b1_pullback_fit/b2_surge_factor/distribution/
  main_rally_factor/wave_type——**带后缀名本身就是规范名**，裸 detect/score 槽位
  的取舍是 API 表面决策，并入 ctx 专项一起定）；`signal_labels.py` 手写内联
  判据已删（grep 钉测在案）、`backtest_factors.py` 的 `_sc_` 包装体剩 4 个消融
  变体（s_reversal/invert_s_shape/long_structure/b1_dual_no_res——消融专用、
  无注册表身份，不属收口范围）；
- characterization 快照 + 逐位等价钉测全绿（44 因子，5 次哈希更新均带版本注记）；
  注册表/分层/架构测试全绿。

遗留验证事项（代码无法自证，须生产机/时间）：
- 影子对照：1800 候选表标注列 ≥10 个交易日与旧口径逐位一致（本次所有改动
  理论上逐位不变，影子是最后保险）；
- 生产机回测清单（§4.4）：关键格子修复前后逐位一致 + 双窗读数不变；
- ctx 输入域规范专项（勘误 2 的 6 个判定器 + B2 留下的 2 个上下文签名因子）；
- 全部齐后 TODO #67 才按维护约定删除，本稿转「✅ 已收敛」。
