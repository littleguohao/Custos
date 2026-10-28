# 开发机挖掘段数据快照（TODO #116 设计稿 / coder 任务单）

> owner 2026-10-26 同意（含预热段 (a) 与 PIT 增量 (b)）。本文是实现规格；落地后以代码为准，本文同步。

## 目标

开发机（无通达信、无网络）上，研究工具**不改代码**即可跑挖掘 A（2000-01-01 ~ 2016-12-31）与挖掘 B（2022-01-01 ~ 2026-09-04）。
判定段（2017 ~ 2021）与前向 holdout（≥ 2026-09-05）数据**物理上不出生产机**（预热豁免见下）。

## 原则：镜像目录，加载器不动

快照按生产机原目录与原格式导出、只按日期截断；开发机用环境变量把 `TDX_ROOT` 与 data 根改道到快照目录，现有加载链
（`read_vipdoc_daily` / 权息缓存 / PIT 台账 / 板块指数 / 宇宙枚举）照常工作。

## 导出范围（写死）

| 数据 | 生产机来源 | 保留日期 | 做法 |
|---|---|---|---|
| 个股 / 指数日线 | `vipdoc/{sh,sz,bj}/lday/*.day` | ≤ 2016-12-31 ∪ [2020-10-01, 2026-09-04] | 32 字节定长记录按日期过滤，原格式写回 |
| 板块指数 | `data/market/sector_index/*.csv` | 同上 | 按行过滤 |
| 板块成分 | `data/market/sector_members.json` | 原样 | 本就是当前快照（前视偏差在各单元注记） |
| 权息 / 股本 | `data/market/xdxr/*.json` | 事件日 ≤ 2026-09-04（**含 2017~2021**） | 缺判定段事件会让前复权算错 |
| PIT 财务 | `pit_financials.jsonl` | notice_date ≤ 2016-12-31 ∪ [2020-01-01, 2026-09-04] | 挖掘 B 起点 TTM 需前两期 |
| 0AMV | **不导原始台账** | 只导 regime 序列：上述保留日期内的 date → regime | 状态机须从台账首日重放，截断台账会算错 |
| 宇宙 / 名称 / 板块类型 | `universe.resolve`、`tq_sector.load_sector_names` 等实际读到的文件 | 原样 | coder 先审计全部读路径（见下「验收 1」） |
| 交易日历 | 仓库内已有 | — | — |

- **(a) 预热段** [2020-10-01, 2021-12-31]：只为挖掘 B 起点前 ≥ 320 根（现行 `warmup_bars=300` + 余量），属现行「段前预热只读价格」定案豁免。
- **(b) PIT 增量** [2020-01-01, 2021-12-31] 公告：只为挖掘 B 起点 as-of TTM。
- 量级估：整份约 1 GB（压缩后几百 MB）；其中 (a)+(b) 约 55 MB。

## 工程拆分

1. **导出脚本**（生产机）：`scripts/dev/export_mining_snapshot.py --out <dir> [--tar]`
   - 产出镜像目录 + `MANIFEST.json`：段边界、预热 / PIT 增量区间、导出时间、git sha、每文件 sha256 与记录数、票数、已知局限；
   - 只读生产数据，不改任何台账；输出路径不得在仓库内（防误入库），默认 `artifacts/cache/mining_snapshot/`。
2. **快照模式**（开发机）：环境变量 `CUSTOS_DATA_SNAPSHOT=<dir>`
   - `paths.py` 读到它时把 `TDX_ROOT` 与 DATA 根改道到快照（**单点**改道，其它模块不感知）；
   - `backtest_factors.load_amv_regime` 快照模式下读导出的 regime 序列；
   - 启动打一行 `[SNAPSHOT] <dir> sha=<manifest sha> 段=…`，研究产物 provenance 记 `data_source=snapshot` + manifest sha。
3. **三道硬闸**（都要钉测）：
   - 快照模式下 `window_usage.record_use` 与 `judgment_batch` 批次门一律拒（判定段只在生产机读）；
   - 快照模式下 live 五时点入口（run_0850/0905/1445/1700/1800）拒跑（runtime_gate）；
   - 加载器读到 (2016-12-31, 2020-10-01) 或 ≥ 2026-09-05 的任何 bar / 记录 ⇒ 直接报错（快照被污染的兜底）；
     预热段 [2020-10-01, 2021-12-31] 读到可以，但 `load_window.mining_violation` 照常拒绝把它当信号窗。
4. **测试**：合成迷你快照（2 票 × 三段）走全链——导出 → 快照模式加载 → 段外日期拒读 → 批次门 / 台账拒写 → live 入口拒跑。

## 落地偏差同步（v0.453 实现后回填，以代码为准）

- **live 拒跑落点**：改在 `pipeline_kit.calendar_gate`（五时点入口共用的第一步，exit 2）——实现时发现
  run_1800 的 runtime_gate 本就「只落盘、不阻断」，拦不住；`runtime_gate` 另加退出码 6 作补充闸（先于 3/4/5）。
- **K 线闸门落点**：`read_vipdoc_daily` 三条非空返回路径（BJ 直读 / SH 指数直读 / mootdx reader）统一调
  `paths.snapshot_bar_violation`——该谓词在 L0 的 paths.py，禁带边界是 `load_window` 段常量的同值镜像
  （paths 不许 import research，两边相等由 `tests/test_snapshot_mode.py` 钉住）。
- **导出清单补一项**：`data/fundamentals/share_changes.jsonl`（mcap 因子的 as-of 股本来源，
  `observed_on ≤ 2026-09-04` 全保留、**含判定段年份**——as-of 只可能 stale，缺了挖掘 B 的股本会错）。
- **快照模式 `load_amv_regime` 缺序列文件 ⇒ fail-closed raise**（不是 best-effort {}）——0AMV 是方向
  主过滤器，静默退 {} 会让回测在无方向过滤下跑出假读数。
- 导出侧两个实现细节：过滤后 0 条的 `.day` / 板块 CSV **不写出**（宇宙枚举自动跟着缩）；
  vipdoc 保留记录按**原样 32 字节切片**写回（不 unpack/repack，保验收 1 的逐位一致）；
  `--out` 守卫口径 = 位于仓库内且不在 `artifacts/cache/` 之下 ⇒ 拒；另有只读的 `--verify <dir>`
  （逐文件重算 sha256 比对 MANIFEST）。
- **review 修补（2026-10-26，合入前）**：① `tests/conftest.py` 顶部在任何 custos import 之前
  `pop("CUSTOS_DATA_SNAPSHOT")`——SNAPSHOT_ROOT 在 import paths 时定死，带着它跑 pytest 会让
  整个套件在快照模式下跑（台账测试被硬闸拦、加载测试读真实快照），报错难懂；专项测试走
  monkeypatch，不受影响。② 禁带检查从「只覆盖日线」扩到 PIT / xdxr / share_changes 三个单一
  读取点（`snapshot_pit_violation` 禁带多一段 2017~2019——PIT 增量从 2020-01-01 起；
  `snapshot_event_violation` 只禁 > 2026-09-04——判定段事件是前复权与 as-of 股本的合法输入）。
  板块指数 CSV 无单一读取点、且污染场景下 vipdoc 闸必先触发，不补（在案决策）。
  ③ 日期比较统一走 `_d8n` 数字归一——横杠/纯数字混比会把边界日误判进禁带。

## 验收（缺一不合）

1. **一致性（最重要）**：生产机上同一 commit，真实数据 vs 快照各跑一次 R44 `--scan-widths --universe-sample 300`（固定种子），
   挖掘段读数**逐位一致**。不一致 ⇒ 快照漏了输入（常见：宇宙枚举文件、权息事件、板块类型表）——先修导出，不放宽验收。
2. MANIFEST sha256 与文件逐一核对通过；`--tar` 包解开后验收 1 仍成立。
3. 全量测试绿；`test_architecture_layers` 不新增豁免。

## 已知局限（写进 MANIFEST，不修）

- 序列在 2017 ~ 2020-09 断开：R43 `age`（上市第几根）类特征在 B 窗失真，快照上该维不可用；
- 前复权价格水平与生产机差一个常数（holdout 之后的权息被截）——收益比例与 `raw_close` 口径不受影响；
- 板块成分为当前快照（与生产机同样的前视偏差）；
- 通达信数据仅限个人自用，快照不外传、不入库。

## 验收①回修（v0.457，2026-10-27）

- **现象**：挖掘 B 逐位一致，挖掘 A 同信号同笔数但少数交易价格微差（margin 差 ~2e-4）。
- **根因**：`compute_qfq_factors` 每个除权事件的比例用「事件日前一根 K 线的收盘」。断口期（2017 ~ 2020-09）的事件在快照里
  找到的「前一根」是 2016-12-30，而不是除权前一天的真实收盘 ⇒ 比例略偏，且乘到整个挖掘 A 上。导出并未漏权息，缺的是前收 bar。
- **修法**：导出侧对「真实前收 bar 不在快照保留集」的事件，在生产机用真实前收预算 `qfq_ratio` 写进事件（与复权侧完全同口径定位，
  `event_ratio` 同函数；None 写 null = 生产机也跳过）；`compute_qfq_factors` 遇 `qfq_ratio` 直接用。生产机自身权息缓存无此字段 ⇒ 行为不变。
- **信息泄露注记（owner 2026-10-27 同意）**：每个断口期分红事件的比例与每股分红合起来可反推除权前一天的收盘——每票每年 1~2 个数，
  不构成可交易信息，但判定段并非一个数都不出生产机。
- 钉测：合成全量 vs 快照的复权因子逐位一致（回修前确实不一致）。

## 验收①第二轮（生产机 2026-10-28 记账，v0.457 回修后）

- 结果：挖掘 A 8 档中 6 档逐位一致（qfq_ratio 见效，剩 ref / x15 两档非 margin 字段微差）；挖掘 B 由第一轮全一致转为 6/8 档 5 位小数级微差
  （笔数全同、win_rate ±0.0001）。两轮真实侧 n 也变了（10805→10959 / 20872→20564），是代码版本漂移，轮内同 commit 可比。
- 证据：`artifacts/logs/snap2_acc_{real,snap}/`；导出 `mining_snapshot_v2`（manifest 320bcba2e73f3879，verify 通过）。
- 排查方向（待定位，不放宽验收）：见 TODO #116 当前下一步。

