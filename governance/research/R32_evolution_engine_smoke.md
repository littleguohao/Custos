# R32 · LLM 进化引擎真实数据冒烟（mock 干跑 / 真实双窗 / joint+grid-judge 全链路）

> **家族**：元层 · 工具链验证（研究侧 LLM 进化引擎首次真实数据全流程）+ 候选留证　|
> **证据等级**：L4（链路验证、#69 根因与修复验证、top_n=0 退化实锤——可复现工程事实）；
> 候选因子 L3−（双窗 pass + 三轴读数在案，但 R11 caveat 下绝对量级不可引用，
> 且 LLM 增量缺随机 baseline 对照）　|
> **状态**：✅ 链路三通（2026-09-10，v0.204）＋ ✅ #69 闭环（v0.207 修复 `--count` 透传，
> 2026-09-11 生产机 r3 重跑验证：grid-judge 实跑 18 格、expressions 块填满）；⚠️ 新实锤
> `top_n=0` 下 scorer 轴不写交易集（因子轴退化，TODO #70）；LLM 增量裁决实验登记 TODO #71　|
> **依赖**：引擎口径见 [`README.md`](README.md) 写入规范「LLM 进化引擎口径」段；
> R11（量级不作数）、R12（判据纪律）、R13（宇宙/窗口钉死——本轮复现一次漂移实例）。
> 索引与主图见 [`README.md`](README.md)。

## 主题

v0.202 落地 LLM 因子进化引擎、v0.204 落地联合演化第一档之后，在生产机用真实
通达信本地数据 + 真实 LLM（glm-5.3，ark coding 端点）跑三步冒烟，覆盖「数据 →
DSL 白名单 → IC 门 → 轨迹池落盘 → 双窗终审 → joint 三轴适应度 → grid 终审」全链；
v0.207 修复 #69 后以 smoke_r3_joint 同口径重跑验证，并对 r2 候选做直接 cell 终审。

## 目标

回答三个问题：① 全链机械在真实数据 + 真实 LLM 下是否通畅（加载/白名单/IC 评估/
落盘/终审）；② owner 关注的两门健康度读数（DSL 门、IC 门通过率）落在哪；
③ joint 模式循环内三轴适应度与 cell 签名复用是否工作。**不产出候选结论**——
过门者一律只记线索，晋级仍走三轴终审 + 因子注册表 status 流程 + owner 拍板。

## 运行口径

- 挖掘窗 2018-01-01~2020-12-31（706 交易日），判定窗 2022-01-01~2025-12-31
  （945 交易日），2021 年留缓冲带；`--count 2500` 两头覆盖；宇宙 `--universe-sample
  200 --seed 42`（实际 ~195 只：000005/000057/000106/000814/000914 本地 vipdoc
  无数据且在线源禁用，跳过）；horizon 5；`--timeout 300`；预算 r1 150k / r2、r3 200k。
- 四次运行 tag：`smoke_mock`（--mock-llm 2 轮×2 候选）、`smoke_r1`（3 轮×2 候选
  + --final-judge）、`smoke_r2_joint`（+ --joint + --grid-judge）、`smoke_r3_joint`
  （v0.207 后同口径重跑，方向措辞「…反转因子」，与 r2 的「…截面反转因子」不同，
  不构成对照）。另有 `vwap_cell_judge`：r2 候选表达式直接喂 strategy_grid
  （判定窗、r2 钉死宇宙 codes-file、`--count 2500`）。
- 产物：`artifacts/logs/evolution/{smoke_mock,smoke_r1,smoke_r2_joint,smoke_r3_joint}/`
  与 `smoke_r2_joint/_ranked__vwap_cell_judge.json`。

## 结论

### 1. 链路验证（L4）：三步全通，#69 修复后重跑验证成立

数据加载（vipdoc 本地日线）、DSL 白名单解释、截面 RankIC/ICIR 评估、轨迹池/汇总落盘、
双窗终审、joint 循环内三轴 cell、cell 签名复用全部按设计工作。v0.207（`_grid_command`
透传 `--count` + 强制显式化）后 r3 重跑：grid-judge 75 格实跑 18、复用 0、截断 3
（超 max-runs 20 预算）、**0 格因尾部截断护栏失败**，`grid_judge.expressions` 块
填满 5 条候选的三轴读数——#69 修复验证成立。尾部截断护栏本身行为正确
（fail-closed 报错而非静默截断，正是它暴露了 #69）。

### 2. 两门指标（owner 关注的健康度读数）

| 运行 | DSL 门 | IC 门 | tokens |
|---|---|---|---|
| smoke_r1 | 6/6 = 100% | 4/6 = 67% | 43,199 / 150k |
| smoke_r2_joint | 6/6 = 100%（零契约重试） | 1/6 = 17% | 72,184 / 200k |
| smoke_r3_joint | 6/6 = 100% | 6/6 = 100% | 53,629 / 200k |

DSL 门无需补 prompt 算子示例。三次 IC 门波动大（17%~100%），且各轮方向措辞与
prompt 均不同，**不构成对照**；更重要：无随机表达式 baseline，过门率不能证明
LLM 假设生成相对随机采样有增量（裁决实验见 TODO #71）。

### 3. 候选留证（L3−，均为线索）

**VWAP 偏离 × 量比**（r2 产出）：`(MA(close*volume,20)/MA(volume,20)-close)/close*MA(volume,5)/MA(volume,10)`，
挖掘 RankIC +0.0461/ICIR +0.293 → 判定 +0.0390/+0.235 双窗 pass。三轴终审
（vwap_cell_judge 直跑，11 格全成）：**best cell = j_low_adx25 × base_low，
objective 0.7395 / margin +13.3pp / 期望R 0.535 / 2442 笔 / 盈亏比 4.28**；
其自带基因组档位（j_low + stop5%/trail8%）只排第 8（0.4464/+11.3pp/0.309），
且该格读数与 r2 循环内 judgment_cell **逐位一致**——交叉证明循环内 cell_runner
与 strategy_grid 子进程同口径；j_low_rsi_strong gate 为负（−0.2155）。
注意：r3 未重新产出该候选（进化随机性 + 方向措辞变化），三轴读数靠直接 cell 跑取回。

**r3 候选**（6/6 全 pass、双窗 5/5）：最优 `-DELTA(close,5)/close*DELTA(SUM(volume,3),5)/SUM(volume,20)`
（判定 RankIC +0.0463/+0.406），三轴 rank1 objective 0.9022/+14.3pp/0.631——
但见结论 5：该读数在同 gate/出场比赛下与另三条表达式完全相同，因子轴贡献为零，
晋级讨论前必须先解决退化问题。经济学上三轮产出全是经典原语重组合（短期反转/
Amihud 2002/乖离/量比），库内无 Amihud、VWAP 偏离实现（学术老、库内新）。

### 4. #69 根因与闭环（L4）

r2 的 grid-judge 3 格全灭（exit=1，3~4s/格）：`_grid_command` 拼 strategy_grid
子进程 CLI 未转发 `--count`，子进程默认 `--count 500` 滚动尾部≈2024-08-06 晚于
`--start 2022-01-01`，触发尾部截断护栏 fail-closed。修复 = v0.207 透传 +
`--joint`/`--grid-judge` 强制显式 `--count`；r3 重跑验证（结论 1）。TODO #69 闭环。

### 5. 新实锤：`top_n=0` 下 scorer 轴不写交易集（L4，TODO #70）

r3 排名前四的表达式 objective/margin/期望R 到小数点后 16 位全等
（0.9022056575774439/+14.32pp/0.631）。取 cell 文件对比：**去掉 `score` 列后
四条表达式的交易集逐字节相同**（md5 `bbe0e998d3ea`，n=2437），跨出场档
（pct5，n=1897）复现。机制：cell 配置 `top_n=0`（不设容量上限）+ 入场由
gate（j_low 系）钉死 ⇒ scorer 只往交易记录写分数、不影响选谁；唯一例外是
rank5 少 66 笔（n=2371），因其 `TS_RANK(...,60)` warmup 最长、早期 NaN 被淘汰——
scorer 仅在「有效性」层面影响交易集，排序本身不起作用。**含义：当前网格配置下
「因子×止损×止盈」退化为「gate×出场」**；要让因子轴产生选择压力须 `top_n>0`
（容量约束）或把表达式接进 gate 轴。附带 infra 注记：`j_low/base_low` 格信号量大，
单格超 1800s 超时（r3 的 2 格失败原因，`--timeout` 可调）。

---

## 证据与过程

- `_summary__*.json` 关键字段：smoke_mock `pool_size=4`（MockLLM 5 条脚本轮换，
  4 次提案用不到第 5 条必 fail 的 `CLOSE/CLOSE` 对照——「6 条落池」的旧预期与
  v0.204 轮换口径不符，记此防误读）；r1 `n_pass=4/n_fail=2`；r2 `1/5`；r3 `6/0`。
- 退化实锤文件：`smoke_r3_joint/expr_*__j_low_adx25__base_low__*.json` 四格
  （ef45b402266b/c4651194370e/2c6380e0c677/31ec8b613c34），去 `score` 后
  trades md5 一致；对照格 b94433f6b764（rank5，n=2371，md5 不同）。
- VWAP 直跑：`smoke_r2_joint/_ranked__vwap_cell_judge.json`（11 格排名）、
  `_report__vwap_cell_judge.md`。
- 宇宙漂移实例（R13）：r2 与 r3 同为 seed42/200 抽样，grid 宇宙 digest 不同
  （c64358bd5141 vs 8790ac43f16a）——隔日全市场股票清单变化导致抽样母体漂移；
  vwap_cell_judge 复用 r2 的 codes-file，与 r2 同宇宙。
- 时间账：r3 约 3h47m（grid-judge 实跑 18 格 + 2 格 1800s 超时占主体）；
  vwap 直跑 15 格约 9 分钟；LLM 单次提案 ~16s / ~1k tokens。
- 工程注记（生产机 Windows）：链路易被 60s 默认超时误杀，实跑用 `--timeout 300`；
  长时间运行以分离进程启动（终端退出会杀会话子进程；`uv run` 自身退出后
  python 子进程孤儿续跑，监控要看 python 不是 uv）。
