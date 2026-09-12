# R32 · LLM 进化引擎真实数据冒烟（mock 干跑 / 真实双窗 / joint+grid-judge 全链路）

> **家族**：元层 · 工具链验证（研究侧 LLM 进化引擎首次真实数据全流程）+ 候选留证　|
> **证据等级**：L4（链路验证、#69/#70/#75 根因与修复复测、退化实锤、随机 baseline
> 裁决——可复现工程事实/确定性跑数）；候选因子 L3−（双窗 pass + 三轴读数在案，
> 但 R11 caveat 下绝对量级不可引用）　|
> **状态**：✅ 全部闭环（2026-09-12）——链路三通（v0.204）；#69 `--count` 透传
> （v0.207 修复 + r3 重跑验证）；#70 因子轴退化（v0.210 `--cell-top-n` 默认 20 +
> r4 验证 scorer 恢复区分度）；#71 随机 baseline 裁决（随机 17% ≪ LLM 67%/100%，
> LLM 假设生成有增量，初步）；#75 慢格（v0.212 TS_RANK 向量化 + scorer 预计算
> 旁路 + 生产机复测 18s/格，修复前后读数逐位一致）　|
> **依赖**：引擎口径见 [`README.md`](README.md) 写入规范「LLM 进化引擎口径」段；
> R11（量级不作数）、R12（判据纪律）、R13（宇宙/窗口钉死——本轮复现一次漂移实例）。
> 索引与主图见 [`README.md`](README.md)。

## 主题

v0.202 落地 LLM 因子进化引擎、v0.204 落地联合演化第一档之后，在生产机用真实
通达信本地数据 + 真实 LLM（glm-5.3，ark coding 端点）跑冒烟与三轮修复验证，覆盖
「数据 → DSL 白名单 → IC 门 → 轨迹池落盘 → 双窗终审 → joint 三轴适应度 →
grid 终审」全链，以及三个结构性问题（#69 count 透传、#70 因子轴退化、#75 慢格）
的修复验证和一次 LLM 增量裁决实验（#71）。

## 目标

① 全链机械在真实数据 + 真实 LLM 下是否通畅；② 两门健康度读数（DSL 门、IC 门
通过率）落在哪；③ joint 循环内三轴适应度与 cell 签名复用是否工作；④ 修复验证
（#69/#70/#75）与 LLM 增量裁决（#71）。**不产出候选结论**——过门者一律只记
线索，晋级仍走三轴终审 + 因子注册表 status 流程 + owner 拍板。

## 运行口径

- 挖掘窗 2018-01-01~2020-12-31（706 交易日），判定窗 2022-01-01~2025-12-31
  （945 交易日），2021 年留缓冲带；`--count 2500` 两头覆盖；宇宙 `--universe-sample
  200 --seed 42`（实际 ~195 只：000005/000057/000106/000814/000914 本地 vipdoc
  无数据且在线源禁用，跳过）；horizon 5；`--timeout 300`；token 预算 150k~200k。
- 运行序列：`smoke_mock`（v0.204，--mock-llm）→ `smoke_r1`（真实 LLM 双窗）→
  `smoke_r2_joint`（joint+grid-judge，暴露 #69）→〔v0.207 修复〕→ `smoke_r3_joint`
  （同口径重跑，验证 #69 + 暴露 #70）→ `vwap_cell_judge`（r2 候选直接 cell 终审）
  →〔v0.210 修复〕→ `smoke_r4_topn20`（验证 #70）+ `topn20_probe`（4 表达式
  定点格对照，暴露 #75）+ `baseline_r1`（#71 随机 baseline）→〔v0.212 修复〕→
  `topn20_probe_v212`（#75 复测）。
- 各轮方向措辞略有差异（r2 多「截面」二字等），**轮间不构成严格对照**。
- 产物：`artifacts/logs/evolution/{smoke_*,topn20_probe,topn20_probe_v212}/` 与
  `artifacts/logs/random_baseline/baseline_r1/`。

## 结论

### 1. 链路验证（L4）：全链三通，三轮修复均验证成立

数据加载、DSL 白名单、截面 RankIC/ICIR、轨迹池落盘、双窗终审、joint 循环内三轴
cell、cell 签名复用全部按设计工作。#69 修复（v0.207）后 r3 重跑：grid-judge
75 格实跑 18、复用 0、截断 3（超预算）、**0 格因尾部截断护栏失败**。#70/#75
验证见结论 5/7。

### 2. 两门指标（owner 关注的健康度读数）

| 运行 | DSL 门 | IC 门 | 后续门 | tokens |
|---|---|---|---|---|
| smoke_r1 | 6/6 | 4/6 | 双窗 4/4 | 43,199 |
| smoke_r2_joint | 6/6 | 1/6 | 双窗 1/1 | 72,184 |
| smoke_r3_joint | 6/6 | 6/6 | 双窗 5/5 | 53,629 |
| smoke_r4_topn20 | 6/6 | 3/6 | joint obj 拦 2 / cell 超时拦 1 ⇒ 0 进双窗 | 44,143 |

DSL 门全程 100%（零契约重试），无需补 prompt 算子示例。IC 门 17%~100% 波动大；
r4 首次出现 **joint objective 门拦**（两条 IC 过门者 obj=-0.08/-0.19 < 0）——
v0.210 容量选择压力生效后，「IC 好但交易层面不行」的候选会在循环内被拦下，
这是设计意图的首次实证。

### 3. 候选留证（L3−，均为线索）

**VWAP 偏离 × 量比**（r2 产出）：`(MA(close*volume,20)/MA(volume,20)-close)/close*MA(volume,5)/MA(volume,10)`，
挖掘 RankIC +0.0461/ICIR +0.293 → 判定 +0.0390/+0.235 双窗 pass。三轴终审
（vwap_cell_judge 直跑，11 格全成）：**best cell = j_low_adx25 × base_low，
objective 0.7395 / margin +13.3pp / 期望R 0.535 / 2442 笔 / 盈亏比 4.28**；
其自带基因组档位（j_low + stop5%/trail8%）只排第 8（0.4464/+11.3pp/0.309），
该格读数与 r2 循环内 judgment_cell **逐位一致**——交叉证明循环内 cell_runner
与 strategy_grid 子进程同口径；j_low_rsi_strong gate 为负。注意：r3 未重新产出
该候选（进化随机性），三轴读数靠直接 cell 跑取回。

**r3 候选**（6/6 pass、双窗 5/5）：最优 `-DELTA(close,5)/close*DELTA(SUM(volume,3),5)/SUM(volume,20)`
（判定 RankIC +0.0463/+0.406）。经济学上各轮产出全是经典原语重组合（短期反转/
Amihud 2002/乖离/量比），库内无 Amihud、VWAP 偏离实现（学术老、库内新）。
r3 的三轴排名读数受当时 top_n=0 退化污染（结论 5），不作晋级依据；
修复后的有效分化读数见结论 7。

### 4. #69 根因与闭环（L4）

r2 的 grid-judge 3 格全灭（exit=1，3~4s/格）：`_grid_command` 拼 strategy_grid
子进程 CLI 未转发 `--count`，子进程默认 `--count 500` 滚动尾部≈2024-08-06 晚于
`--start 2022-01-01`，触发尾部截断护栏 fail-closed。修复 = v0.207 透传 +
强制显式 `--count`（缺省 exit 2 零 spawn）；r3 重跑验证（结论 1）。闭环。

### 5. #70：`top_n=0` 退化与修复验证（L4）

**退化实锤（v0.208 登记）**：r3 排名前四表达式 objective 到小数点后 16 位全等
（0.9022056575774439），去 `score` 列后交易集逐字节相同（md5 `bbe0e998d3ea`），
跨出场档复现——`top_n=0` 下 scorer 只写分数不影响交易，「因子×止损×止盈」
退化为「gate×出场」。

**修复验证（v0.210 `--cell-top-n` 默认 20，r4 + topn20_probe）**：r4 循环内两格
同 gate（j_low）同出场（pct5/trail08）objective 分化为 **−0.0807 vs −0.1862**，
scorer 轴恢复区分度。机制澄清：top_n=0 时 objective 出自**全候选池**（gate 决定，
故全等）；top_n>0 时出自 **top-N 选中子集**（scorer 排序决定，故分化）。
候选池数组本身仍由 gate 决定（probe 中 expr1/expr3 候选池哈希一致、n=5202
是预期：expr3 = expr1 × 非负因子，排序数学等价）。选中子集口径示例：候选
5202 笔 → 成交 300 笔（限跳 4436），top-N 模式的读数只看选中子集。闭环。

### 6. #71：随机 baseline 裁决（L4 确定性跑数，小样本）

`baseline_r1`（12 条随机 DSL 表达式，seed 20260911，同宇宙同窗同 count）：
**随机过门率 17%（2/12）vs LLM 实测 67%（r1）/100%（r3）**，Fisher 精确检验
单侧 p=0.057（边缘显著）。按预登记读法落在「随机明显更低 ⇒ **LLM 假设生成有
增量**，值得加大跑批」档。两条随机过门者 `(DELTA(close,60)-volume)`（+0.065）/
`(LOG(close)-ROC(volume,40))`（+0.042）量纲无意义，疑为 `-volume` 代理低成交量
溢价——**即使纯随机也有 17% 过门，IC 门不是最后防线**，三轴终审/注册表/拍板
纪律仍必需。小样本 caveat：可再加 1~2 个 seed 把 baseline 钉窄（每 seed ~7 分钟、
零 token，工具 `random_baseline_study` 已落地 v0.210）。

### 7. #75：慢格根因、修复与复测（L4，闭环）

**根因（v0.212 profile 定位）**：两乘性——表达式 scorer 未接 `_SCORER_PRECOMPUTE`，
热循环逐 bar 对前缀切片全量重算（O(n²)，单股逐 bar 698.7s）；TS_RANK 用
`rolling.apply` 逐点 Python 回调（单股全序列 552ms，约为 ROC 的 2000 倍）。
topn20_probe（v0.211）中 expr4/expr5 两格 >28 分钟未完被杀，r3 的
`j_low/base_low` 1800s 超时 ×2 同属此路径（scorer 逐 bar 判定与 top_n 无关，
不是 gate/组合层问题）。

**修复（v0.212）**：TS_RANK 改 `sliding_window_view` 整数比较计数（逐位等价：
同一笔 `/(2n)` 浮点除法，NaN 窗口掩码同旧 `min_periods=n` 口径）；表达式 scorer
三参形态接 `_SCORER_PRECOMPUTE`（每股全序列算一次、热循环 O(1) 点查，异常
回退旧路径）——等价性沿用仓内 kdj_j/rsi_state 同款「前缀切片 ≡ 全序列第 i 点」
口径，判定语义零变化。

**生产机复测（topn20_probe_v212，2026-09-12）**：4 格全部 **18s/格** 完成
（加载 3-4s + 评估 14s），对照修复前 2 格约 1 分钟 + 2 格 >28 分钟未完——
病态格消失，与 commit 折算量级一致。**等价性实锤（生产数据）**：expr3 cell
修复前后读数逐位相同（成交 300/限跳 4436、CAGR 19.1%、收益/回撤 2.52）。

**副产观察（R11 caveat：只看相对排序）**：4 scorer × 同 gate/出场，选中子集
组合读数真实分化——expr1（n=305，收益/回撤 1.71）、expr3（n=300，2.52）、
expr4（n=328，0.26）、**expr5 纯 Amihud（n=312，胜率 34.0%，收益/回撤 5.73，
最大回撤 19.3% 为四者最小）**——最朴素的 Amihud 单因子选中子集质量最好，
此差异在 top_n=0 退化时代完全不可见。expr1/expr3 微差（305 vs 300）系
NaN/并列边界效应，与「排序数学等价」不矛盾（top_n=0 时严格一致是因为
当时根本不发生选择）。

---

## 证据与过程

- `_summary__*.json` 关键字段：smoke_mock `pool_size=4`（MockLLM 5 条脚本轮换，
  4 次提案用不到第 5 条必 fail 对照——「6 条落池」的旧预期与 v0.204 轮换口径不符，
  记此防误读）；r1 `4/2`；r2 `1/5`；r3 `6/0`；r4 `0/6`（`cell_top_n=20`、
  `factor_axis_degenerate=false` 写入 config）。
- 退化实锤文件：`smoke_r3_joint/expr_*__j_low_adx25__base_low__*.json` 四格
  （ef45b402266b/c4651194370e/2c6380e0c677/31ec8b613c34）去 `score` 哈希一致；
  对照格 b94433f6b764（rank5，n=2371，不同）。#70 验证文件：
  `smoke_r4_topn20/grid_cells/`（156b91aaf54d/9d1328d3d072，objective 分化）、
  `topn20_probe/`（f52564662655/f0176dc5d4c1）。#75 复测：`topn20_probe_v212/`
  4 格全成（436de47f8405/23d6710dbbed 等，stdout `[DONE] ... 18s` ×4）。
- VWAP 直跑：`smoke_r2_joint/_ranked__vwap_cell_judge.json`（11 格排名）。
- baseline：`random_baseline/baseline_r1/_random_baseline__baseline_r1.json`。
- 宇宙漂移实例（R13）：r2 与 r3 同为 seed42/200 抽样，grid 宇宙 digest 不同
  （c64358bd5141 vs 8790ac43f16a）——隔日全市场股票清单变化致抽样母体漂移；
  vwap_cell_judge/topn20_probe(_v212) 均复用钉死 codes-file。
- 时间账：r3 约 3h47m（grid 实格 18 + 2 格 1800s 超时）；vwap 直跑 15 格约 9 分钟；
  baseline 约 7 分钟；r4 约 2.5 小时（含 1 格循环内超时 1800s）；
  topn20_probe_v212 全程 61 秒。
- 工程注记（生产机 Windows）：LLM 单次提案 ~16s / ~1k tokens，默认 60s 超时
  易误杀，实跑用 `--timeout 300`；长时间运行以分离进程启动（终端退出会杀会话
  子进程；`uv run` 自身退出后 python 子进程孤儿续跑，监控要看 python 不是 uv）。
