# AGENTS.md — Custos 协作共享认知

> 给所有在本仓库工作的 agent（含多机并行）的**最小必读**。只记两类东西：
> ① 不读代码就不知道、且**靠测试强制**的隐形纪律；② 快速定位的地图。
> 细节一律指向各目录 README/治理文档，本文件不复述（防漂移）。
> **改了本文件描述的行为时，必须同步更新本文件。**

## 0. 项目定位与核心原则

确定性脚本驱动的 A 股交易策略系统（择时/选股/持仓研判/卖出风控/总控决策/复盘）。

**核心原则：数据采集和因子计算全部由 Python 脚本完成，LLM 参与数据和消息分析、
市场信息判断、因子研究。** LLM 不进 live 交易决策与执行链路（live 零 LLM）。

核心思想：不追求胜率，追求盈亏比；杠杆加在出场与仓位管理（`README.md`、
`governance/strategy/_shared/system_principles.md`）。

## 1. 目录地图

| 路径 | 是什么 |
|---|---|
| `src/custos/core/` | L0 基建（paths/contracts/indicators/exit_rules）+ L2 因子/交易台账 |
| `src/custos/datasource/` | L1 数据采集（含 `local_tdx/` 本地通达信封装，文件解析器已下沉于此） |
| `src/custos/pipeline/` | L3 四个 stage 包 + L4 五时点 runner（run_0850/0905/1445/1700/1800） |
| `src/custos/research/` | L4 研究回测（只读管线数据，**生产永不 import 研究**，测试强制） |
| `src/custos/research/evolution/` | LLM 因子进化引擎（研究侧） |
| `governance/contracts` | 代码真读的 JSON 配置（EXIT_RULES 等）+ 工作流文档 |
| `governance/strategy` | 策略规则文档（一策略一目录 + STRATEGY_REGISTRY.json 强制登记） |
| `governance/research` | 研究单元 R1-R37（判据预注册，编号只增不复用） |
| `TODO.md` / `CHANGELOG.md` | 待办 vs 已改策略规则，两者分工严格（见 §4） |
| `tests/` | ~6100 测试，架构/契约/注册表全靠它强制 |

## 2. 架构分层（AST 测试强制：`tests/test_architecture_layers.py`）

```mermaid
flowchart TB
    subgraph L4["L4 · 编排与研究（叶子层）"]
        RUN["run_0850/0905/1445/1700/1800 五时点入口<br/>+ daily_pipeline / daily_report"]
        RES["research/ 研究回测<br/>backtest_factors · strategy_grid · evolution/ · 各 study"]
    end

    subgraph L3["L3 · pipeline/ 四 stage 包"]
        SCR["screening/ 选股链<br/>公式初筛→充实→打分→候选表"]
        MKT["market_timing/ 择时评分 · AMV 状态机"]
        HLD["holdings/ b1 持仓状态机 · 技术批处理"]
        REV["close_review/ 14:45 · 周/月复盘 · MFE/MAE"]
    end

    subgraph L2["L2 · 领域实现"]
        FAC["core/factors/ 因子注册表<br/>（status×live_use×stage + research_ref/trajectory_ref）"]
        TRD["core/trades/ 台账标准化 · 对账 · 持仓计划"]
    end

    subgraph L1["L1 · datasource/ 采集（厂商库只许此层）"]
        TDX["local_tdx/ 通达信封装<br/>vipdoc 日线 · 权息 · 板块文件"]
        COL["collect/ 持仓/指数报价 · 资金流 · 增量行情"]
        NEWS["news/ RSS 采集过滤"]
        ADP["顶层适配：trading_calendar · breadth_basis<br/>refresh_eod_klines · sync_compass_amv 等"]
    end

    subgraph L0["L0 · core/ 基建（contracts 零内部依赖）"]
        INF["paths · contracts · indicators · exit_rules · b1_thresholds"]
        KIT["pipeline_kit · fmt · net_retry · code_utils · report_audit"]
        GRD["runtime_guards / runtime_gate<br/>⚠️ 读 L3 产物形状（刻意折中，不 import）"]
    end

    RUN --> SCR
    RUN --> MKT
    RUN --> HLD
    RUN --> REV
    RES -.->|"只读复用其产物/引擎；反向 import 被测试禁止"| SCR
    SCR --> FAC
    SCR --> TDX
    MKT --> FAC
    HLD --> FAC
    HLD --> TRD
    REV -.->|"同层交叉（合规）"| HLD
    REV -.->|"同层交叉（合规）"| MKT
    FAC --> INF
    FAC --> TDX
    TRD --> INF
    TDX --> INF
    COL --> INF
    NEWS --> INF
    FAC --> KIT
    SCR --> KIT
    HLD --> KIT
    MKT --> KIT
    REV --> KIT
    RUN --> GRD
```

因子包内部互赖（同层合法，注册表测试核对）：`b1_dual_factor → s_shape /
platform_pullback`、`main_rally_factor → rsi_state`、`sector_phase →
sector_mainstream`。

L0→L4 单向依赖，下层不得依赖上层；contracts.py **零内部依赖**（只许 stdlib）。
厂商库（mootdx/akshare/qlib/tqcenter…）只许 `datasource/` import（白名单钉测，
豁免集保持为空）。新增按日期命名的 JSON 产物必须在 `core/contracts.py` 建
schema 并在生产者落盘前 `require()`（豁免要登记理由），否则测试红。

两个语义注记（测试管不到、图上已标）：① `runtime_guards.py`（L0）读 L3 产物
形状——"守卫知道被守卫对象的 schema"是刻意折中；② L3 四包不是互相隔离的，
同层互 import 合法且已发生（见图注）。


## 3. 命令与环境

```bash
uv run python scripts/dev/test_affected.py   # 本地迭代默认：受影响模块+基础守卫（秒级~分钟级；--dry-run 看选择）
uv run pytest -q          # 全量（~5.3 分钟/6283 例，2026-10-08 实测）——GitHub CI 双平台门禁在每个 push/PR 必跑
bash scripts/audit.sh     # 六件套；第 0 件 ruff format --check 是唯一硬门槛
uv run --with mypy mypy --config-file scripts/mypy.linux.ini src/
```

- **测试分工（2026-10-08 起）**：本地提交前跑 `test_affected.py`（变更模块的
  反向 import 闭包 ⇒ 受影响测试 + 基础守卫集）必须绿；**全量由 GitHub CI
  （ubuntu+windows 双平台）跑，CI 红必须当天修**。影响面无法局部化时
  （conftest/pyproject/uv.lock/.github/tests/helpers_*）选择器自动转全量；
  推送前想本地全量兜底可随时 `uv run pytest -q` 或 `test_affected.py --full`。

- Python ≥3.11，依赖只有 pandas/mootdx/openpyxl/requests——**不得新增第三方依赖**
  （向量化用 numpy 等 pandas 自带物；DSL 解析用 stdlib ast，**禁止 eval/exec**）。
- 本机（dev）**无网络、无通达信数据**：测试一律合成数据注入（loader=/monkeypatch），
  conftest 已 block 部分网络。真实数据跑数在生产机。

## 4. 治理纪律（全部有测试钉着，违反必红）

**TODO.md**：
- 完成的项**直接删**，禁止 `~~删除线~~`（`test_no_strikethrough_entries`）；
- 新增必须带出处；编号只增——**改前先 pull**（多机并行撞过号）。

**CHANGELOG.md**：
- 每行 ≤400 字符（`test_row_length_cap`，多人踩过）；版本号只增不复用；
- 记「已改的策略规则」，不记待办。

**研究单元**（`governance/research/`，`test_research_units.py`）：
- 判据跑数前写死（`R{n}-C{i}`）；结论被推翻标 🔄 不删除；每结论带证据等级；
- **双窗纪律**：寻优全程不得读判定窗；**pre2019（2010-2016）untouched 终审段
  任何挖掘/判定不得相交**（工具硬拒绝）；
- 单元头部「状态/结论」必须与正文同步（R22 头部滞后是反面教材）。

**因子注册表**（`core/factors/`，`test_factor_registry.py`）：
- 元数据三维：status（证据）× live_use（允许怎么用）× stage（是否在跑）；
- 谱系字段 `research_ref`（R 文档须存在）/`trajectory_ref`（`t_xxxxxxxxxx`）；
- 准入：`free_params≥4` 且 active ⇒ 必须有 research_ref；evidence_only 集合钉死；
- 接口已收敛（#67）：规范入口 `detect(df, code, ctx=)` / `score()`，live 标注与
  研究 SCORERS 同源（改判定语义=语义变更，须立项+回测）。

**0AMV 台账**（`data/market/0amv_observations.jsonl`）：**append-only**——写入只走
`amv_state.append_observation`（每日首写前自动快照 `.bak_YYYYMMDD`）或 `>>` 追加，
**严禁覆盖式写入**（2026-09-21 agent 误用 write 工具覆写，8215 行全历史灭失，
靠 08-30 去重备份 + 当日记录手工合并救回）。

## 5. 研究层与进化引擎约定

- 新工具：写 `research/xxx.py`（`add_argument` **全留本文件**，`_modes()` 用 AST
  抽取）+ `research/__main__.py` TOOLS 登记；入口 stdout/stderr reconfigure utf-8；
- **空结果护栏**：0 数据/0 结果 → 非零退出且不写产物（防误读为"无有效因子"）；
- 研究产物 JSON **允许 NaN**（区别于生产侧 `paths.write_json` 的 allow_nan=False），
  落 `artifacts/logs/`；研究产物不建 contracts schema；
- 回测窗参数 `--start/--end/--count`：`--count` 是"最新向前 N 根"，早窗口必须显式
  加大（尾部截断护栏 fail-closed）；⚠️ 护栏只在 `_load_bars_local` 批量路径——
  逐股直调 `_load_one_bars` 的终端（两个 C5）自带 `check_reach` 前置校验 +
  默认 count=100000 全历史（r36_c5 首跑 count=2000 剪空 pre2019 的教训）；
- 进化引擎：LLM 只做提案（假设/变异/杂交/解读），**decision 由确定性函数给出**；
  挖掘/判定双窗硬隔离；`--joint`/`--grid-judge` 必须显式 `--count`；`--cell-top-n`
  默认 20（=0 是因子轴退化，scorer 不写交易集）。指标门 `--ic-gate`（rank 默认 /
  top_tail 头部价差 / off）+ 门次序 `--gate-order`（ic_first 默认 / marks_first
  成本控制）——非默认口径须已在研究单元预注册（R36 三轮是首个用例）。
  score_evolution_study 终审姿态：`--v0-lattice` 调权格（P3 族）/ `--addon-leg`
  骨架加腿（R36 思路二——新腿一律问「加进 V0 等权骨架的 Δmargin」，不问单独立）。
  exit_campaign（R37 战役壳）：出场基因组×批次进化 + CTL-1~5 确定性控制器
  （继续/转向/双向停止/预算帽）+ 台账每批原子落盘（--resume 续跑）；评估器
  协议注入，生产=V0 重放（信号缓存+as-of V0 分+topn，同 score_evolution V0 臂）。
  factor_exit_study（R39 终端）：因子×出场交互——因子连续值×分桶×映射→
  出场档 80 格全枚举（无进化无 LLM；切点只估挖掘窗），主基准=uniform-best，
  C4=随机分桶臂同预算；预热复用 exit_campaign.warm_v0_signals（模块级单源）。
  bear_regime_study（R40 终端）：0AMV 空头区间做多全栈——46 门×5 档 230 格
  （invert_regime_bearish 反转仅空头日放行），基准=随机入场臂（v0.302 对等
  纪律：挖掘窗选型冻结配置带判定窗）；判据四态含 untested（C1 不过≠证伪）。
  exit_c5_terminal（R37-C5 终审终端，判据 v0.299——**a_sample 可疑闸**：
  n 低于预期 ⇒ 跑数可疑不出判决（双向压）+ thr 三分：CI95 hi<thr 杀 /
  lo>0 且点估计≥thr 活 / 其余 untested）：冻结候选 vs 基准档
  pre2019 段配对 bootstrap（SE+n 前置）——**只接受 pre2019 段内窗口**
  （硬拒绝镜像），只能杀不能确认。score_c5_terminal（R36-C5 终审终端，
  score 侧同族镜像）：冻结 v0-lattice 基因组（产物自含读取）vs 等倍率基准，
  日簇配对 bootstrap（选中集不同 ⇒ 交易级配对不成立，同日同进同出）。
  **objective 版本（v0.296，#80④ 采 Ⓐ）**：搜索标量 = margin 单量 +
  rdd≥1.0 约束门（`strategy_grid.search_objective`）；v1 复合
  （margin+expectancy_R+0.05·rdd）作废——历史 objective/q95 读数**不跨版本
  引用**（各战役 C4 随机池标尺本就自备）。**v0.297 两修补**：①exit_campaign
  随机池空（随机臂全被 rdd 门拦）⇒ C4 **indeterminate 不放行**（原假设
  「n_random≥1 总有臂」被 v2 打破；报告记随机臂 rdd 过门率）；②CLI 格子排名
  `rank_rows` 在 v2 默认权重下同样套门（「优胜格可拷 live EXIT_RULES」的路径
  不能偏袒高敞口；显式自定义权重=复算口径不套门）。
- 因子 IC 画像（`factor_ic_profile`）：SCORERS/DSL 的截面 RankIC/ICIR + horizon
  衰减全因子可比表——**分诊镜不是晋级判据**（晋级永远走双窗+三轴交易语义；
  读数 L3− 带幸存者偏差，R19/R21/R14）。
- 指标盘缓存（`research/indicator_cache.py`，`backtest_factors --indicator-cache`
  开启）：研究专用逐股电池复用——**红线：不得服务 live、不得服务 as-of 重播种
  路径**（score_return_study 判例）；指标实现任何改动必须 bump
  `INDICATOR_PACK_VERSION`（不改=旧缓存被当新实现读）。

## 6. 多 agent / 多机协作纪律

1. **开工先 pull**；完成立即 commit + push（版本号顺增，批间可 revert）；
2. TODO/CHANGELOG 是单写者文件：改前 pull，编号冲突时后手避让顺增；
3. 改行为必同步：对应 README/治理文档/AGENTS.md 三处至少检视一遍；
4. 文档与代码不一致时**以代码为准**（文档会漂移；schema/注册表在代码里）；
5. 发现旧文档行动项已被推翻：移到 TODO「已失效」表并写原因，不静默删除。

## 7. 快速定位

- 核心原则与架构：`README.md`；src 层细则：`src/custos/README.md`
- 研究索引/双窗/LLM 口径：`governance/research/README.md`（写入规范段）
- 因子接口收敛设计：`governance/strategy/_factors/interface_unification_plan.md`
- 进化引擎入口：`python -m custos.research evolution --help`（README 有冒烟命令）
- 实盘教训台账：`TRADE_LESSONS.md`（只增不删）
