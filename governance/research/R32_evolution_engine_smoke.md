# R32 · LLM 进化引擎真实数据冒烟（mock 干跑 / 真实双窗 / joint+grid-judge 全链路）

> **家族**：元层 · 工具链验证（研究侧 LLM 进化引擎首次真实数据全流程）+ 首个 joint 候选留证　|
> **证据等级**：L4（链路验证与失败根因——可复现工程事实）；候选因子 L3−
> （双窗 pass 但三轴终审因工程缺口未完成，且 R11 caveat 下 objective/margin/期望R
> 绝对量级不可引用，只作相对排序）　|
> **状态**：✅ 链路三通（2026-09-10，v0.204，生产机真实 tdx 数据 + 真实 LLM）；⚠️
> `--grid-judge` 三轴终审 3 格全灭——根因已查明（`_grid_command` 未转发 `--count`），
> 修复与重跑登记 TODO #69　|
> **依赖**：引擎口径见 [`README.md`](README.md) 写入规范「LLM 进化引擎口径」段；
> R11（量级不作数）、R12（判据纪律）、R13（宇宙/窗口钉死）。
> 索引与主图见 [`README.md`](README.md)。

## 主题

v0.202 落地 LLM 因子进化引擎、v0.204 落地联合演化第一档之后，首次在生产机用真实
通达信本地数据 + 真实 LLM（glm-5.3，ark coding 端点）跑三步冒烟，覆盖「数据 →
DSL 白名单 → IC 门 → 轨迹池落盘 → 双窗终审 → joint 三轴适应度 → grid 终审」全链。

## 目标

回答三个问题：① 全链机械在真实数据 + 真实 LLM 下是否通畅（加载/白名单/IC 评估/
落盘/终审）；② owner 关注的两门健康度读数（DSL 门、IC 门通过率）落在哪；
③ joint 模式循环内三轴适应度与 cell 签名复用是否工作。**不产出候选结论**——
过门者一律只记线索，晋级仍走三轴终审 + 因子注册表 status 流程 + owner 拍板。

## 运行口径

- 挖掘窗 2018-01-01~2020-12-31（706 交易日），判定窗 2022-01-01~2025-12-31
  （945 交易日），2021 年留缓冲带；`--count 2500` 两头覆盖；宇宙 `--universe-sample
  200 --seed 42`（实际 ~195 只：000005/000057/000106/000814/000914 本地 vipdoc
  无数据且在线源禁用，跳过）；horizon 5；预算 smoke_r1 150k / smoke_r2 200k tokens。
- 三次运行 tag：`smoke_mock`（--mock-llm 2 轮×2 候选）、`smoke_r1`（3 轮×2 候选
  + --final-judge）、`smoke_r2_joint`（同上 + --joint + --grid-judge，
  方向措辞多「因子」二字，与 r1 不构成对照）。
- 产物：`artifacts/logs/evolution/{smoke_mock,smoke_r1,smoke_r2_joint}/`
  （`_summary__*.json`、`trajectory_pool.json`、`grid_cells/`、console 日志）。

## 结论

### 1. 链路验证（L4）：三步全通

数据加载（vipdoc 本地日线）、DSL 白名单解释、截面 RankIC/ICIR 评估、轨迹池/汇总落盘、
双窗终审、joint 循环内三轴 cell（46s/格）、cell 签名复用（`grid_cells/` 两签名文件在案，
判定阶段直接复用 `a72fcd15d51f`）全部按设计工作。尾部截断护栏也按设计工作——
fail-closed 报错而非静默截断（正是它抓住了下面的 #69 缺口）。

### 2. 两门指标（owner 关注的健康度读数）

| 运行 | DSL 门 | IC 门 | tokens |
|---|---|---|---|
| smoke_r1 | 6/6 = 100% | 4/6 = 67% | 43,199 / 150k |
| smoke_r2_joint | 6/6 = 100%（零契约重试） | 1/6 = 17% | 72,184 / 200k |

DSL 门无需补 prompt 算子示例。IC 门 r2 偏低但未全灭，且 r1/r2 方向措辞与提案
prompt（joint 基因组）均不同，**不构成对照**；是否需干预待更多批次观察。

### 3. 候选留证（L3−）：VWAP 偏离 × 量比

smoke_r2_joint 唯一过门者，基因组 = 表达式 × gate `j_low` × 出场（stop 5% /
trail 8%），循环内 objective +0.2697：

```
(MA(close*volume,20)/MA(volume,20)-close)/close * MA(volume,5)/MA(volume,10)
```

机制假设：收盘价深度跌破 20 日量加权均价（≈近月平均持仓成本、套牢密集）且 5 日
均量较 10 日持续放大（放量宣泄卖压）→ 未来数日截面反弹更强。读数：挖掘窗
RankIC +0.0461 / ICIR +0.293；判定窗 RankIC +0.0390 / ICIR +0.235，**双窗 pass**；
判定窗 joint cell objective 0.4464 / margin +0.113 / expectancy_R 0.309
（R11 caveat：仅相对排序参考）。smoke_r1 另有 4 条双窗 pass（最优
`-(DELTA(close,5)/STD(close,10))*(DELTA(volume,5)/MA(volume,20))`，挖掘
0.0441/0.419 → 判定 0.0440/0.418，几乎零衰减）。
**以上候选均为线索**：晋级须 #69 修复后补三轴终审 + 因子注册表 status 流程 +
owner 拍板（README「LLM 进化引擎口径」段纪律不变）。

### 4. `--grid-judge` 失败根因（L4，TODO #69）

三轴终审 3 格（j_low / j_low_adx25 / j_low_rsi_strong × base_low）全部 exit=1
（3~4s/格），0 格入榜。根因：`evolution_loop.py:_grid_command` 拼 strategy_grid
子进程 CLI 时**未转发 `--count`**，子进程用默认 `--count 500` 滚动尾部只回溯到约
2024-08-06，晚于 `--start 2022-01-01` ⇒ 30/30 票首根晚于起点，触发尾部截断护栏
fail-closed。护栏行为正确；缺口在进化侧参数没传下去（strategy_grid 本身有
`--count` 旗标）。修复 = `_grid_command` 加一行转发 + 钉测，随后按 smoke_r2_joint
同口径重跑取回三轴终审读数。

---

## 证据与过程

- 各步 `_summary__*.json` 关键字段：smoke_mock `pool_size=4`（2 轮×2 候选×1 方向；
  MockLLM 5 条脚本轮换，4 次提案用不到第 5 条必 fail 的 `CLOSE/CLOSE` 对照——
  「6 条落池」的旧预期与 v0.204 轮换口径不符，记此防误读）；smoke_r1
  `n_pass=4/n_fail=2`；smoke_r2_joint `n_pass=1/n_fail=5`。
- smoke_r2 的 [evo] 流里 6 提案全带 `j_low` gate 分量，其中 r1c0 RankIC 为空
  （评估无可读数，判 fail）；r2c1 过门。
- grid 报告头部 R11 标注常驻：「预算 max-runs 20，实际跑 0，复用跳过 0，失败 3」
  （`_report__smoke_r2_joint__grid.md`）。
- 工程注记（生产机 Windows）：LLM 单次提案 ~16s / ~1k tokens，链路易被 60s 默认
  超时误杀，实跑用 `--timeout 300`；长时间运行应以分离进程启动（终端退出会杀
  会话子进程）。
