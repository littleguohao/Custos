# 第一批判定段 · 生产机跑数手册（v0.410，2026-10-16）

> 一份文件从头跑到尾：#91 探针 → #92 基线 → 各单元挖掘段（可并行）→ 冻结 → 开批 → 判定段统一读一次。
> 命令逐字可复制；`<SEG_A_START>` 在步骤 0 后代入。每步写明「产物 / 贴回什么」。纪律与判据见
> [`README.md`](README.md)「研究纪律 · 判定段攒批协议」与各单元文档；本手册只管执行顺序。

## 公共约定

- **开工前 `git pull`**，记下 `git log -1 --oneline`（贴回时附上）；跑数期间不要在同一工作目录 pull（懒加载模块会混跑新旧代码）。
- 长任务一律脱钩后台（Windows：`Start-Process`；见 R36 附录 2026-09-25 教训），stdout/stderr 落文件。
- 每条命令的 stderr 里 `[TIME]` / `[MEM]` 行一起贴回（#112 加载 / 评估耗时占比）。
- **分布画像（v0.422，描述性副读数，不进判据）**：R39 / R40 / R44 / R45 命令加 `--trade-dist`，跑完多出一份摘要：
  `result_digest r39_dist|r40_dist|r44_dist|r45_dist <产物> --tag <tag>_dist --write --backfill`（回填 `results/R*_trade_dist.md`）。
  R45 的必报扩展（组合层、出场原因、不可卖笔数、空头日数）不加开关也恒报。
- **回填协议（v0.413）**：每条命令跑完立刻
  `uv run python -m custos.research result_digest <kind> <产物.json> --tag <tag> --write --backfill`，
  然后 `git add governance/research/results/ governance/research/*.md && git commit && git push`。
  - 摘要只抄产物里预注册的字段（`result_digest.SPECS`），回填块里的数字**不许手改**（守卫测试逐字比对）；
  - 生产机可在块**外**写一段判读，单元头部状态（判毕）与 TODO 闭环**不要改**，留给复核；
  - 必需字段缺失 ⇒ 工具拒写（exit 2）：先查产物是否完整，别用 `--allow-missing` 硬过。

## 步骤 0 · #91 数据覆盖探针（关键路径起点）

```bash
uv run python -m custos.research data_coverage_probe --tdx-root <通达信根> \
  --out artifacts/logs/data_coverage_probe.json
ls <通达信根>/vipdoc/bj/lday/bj88* | head    # 北交所 88 段个股 vs 880/881 指数是否撞码
```

回填：`result_digest probe artifacts/logs/data_coverage_probe.json --tag probe_v1 --write --backfill`（bj88 那行 ls 输出写在块外）。我据此回填 `SEG_A_START = max(2000-01-01, amv_ledger.date_min)` 与 R43 功效节。

## 步骤 1 · #92 基线重测

命令写死在 README「基线重测 runbook」（R11 ×2、R4 2×2×2、2012 子窗 ×4），起点按步骤 0 代入。
回填：每份产物 `result_digest baseline <产物> --tag <文件名去 .json> --write --backfill`（`timing` 块自动带上）。**第一批截止日 = 本步完成日 + 4 周**（owner 定日）。
我据此回填 R39 / R44 / R45 / R46 功效节；功效节写「untested 不跑」的单元，步骤 2 跳过。

## 步骤 2 · 挖掘段跑数（只读挖掘段，不过批次门、不入台账；彼此独立可并行，内存允许时 2~3 路）

| # | 单元 | 命令 | 摘要 kind / 复核重点 |
|---|---|---|---|
| 2a | R36 adx_gt_60 前置核对（#105；须 ≥ v0.417，产物 version 2） | 见下 ① | `r36_frozen`：`verdict`、`candidate.delta_median`、`candidate.gate_rate`、`candidate.n_taken_median`、`pool` 长度 |
| 2b | R39 ADX 预演（A 选 B 验） | 见下 ② | `r39`：`verdict`、`criteria.C1~C4` |
| 2c | R40 预演（#109） | 见下 ③ | `r40`：`verdict`、`criteria`、`pools.entry_accounting`（requested/returned 比 < 0.8 须说明） |
| 2d | R43 挖掘段 | 见下 ④ | `r43`：`style.*.dims.*.verdict` 为 candidate 的（方向, 维度, 符号）清单 + `criteria.C4` |
| 2e | R44 8 档扫描 | 见下 ⑤ | `r44_scan`：`criteria.C1~C4`、`x_star` |
| 2f | R45 预演 | 见下 ⑥ | `r45`：`verdict`、`top.exit`、`criteria.C2.beats_no_trade`、`pools.entry_accounting` |
| 2g | R46 家族配比（v0.416 主读数改选中集后恢复；须用 ≥ v0.416 代码，产物 schema v2；**加 `-j <核数>`**，v0.422 起逐位一致，串行估 >1 天） | 见下 ⑦ | `r46`：`verdict`、`criteria.C2.g_star/merged_delta/tie_seed_median_delta`、`base_equals_live` |

```bash
# ① R36（原 C4 复核报告做冻结源；v0.407 起臂读 top-N 选中子集，旧格子产物含逐笔可直接复用）
uv run python -m custos.research score_evolution_study --v0-lattice \
  --frozen-read artifacts/logs/score_evolution/r36_p3_c4review/_score_evolution__r36_p3_c4review.json \
  --mining-start 2022-01-01 --mining-end 2024-07-31 \
  --judgment-start 2022-01-01 --judgment-end 2026-09-04 --tag r36_v2_preB --jobs 4

# ② R39 预演（legacy 四参：A 选型、B 验证）
uv run python -m custos.research factor_exit_study --tag r39_v2_pre \
  --mining-start <SEG_A_START> --mining-end 2016-12-31 \
  --judgment-start 2022-01-01 --judgment-end 2026-09-04

# ⚠️ R40/R45 提速（v0.446）：耗时大头是随机臂入场的 as-of V0 打分（~30ms/bar × 母体百万级 bar）。
#   --score-workers N：母体缺分 bar 先多进程一次算完（读数与原惰性串行逐位一致，不进冻结哈希）；
#   stderr 现有 [prescore] 进度与逐臂 [arm] 行（每臂耗时 / 打分缓存量），可据此估剩余时间。
#   N 取物理核数（本机 8 路实测 4~5×）；内存 ≈ 每路一份上证指数 + 当前任务 df，量级很小。
#   v0.463：不依赖池的判据已定结局（C1 不足 / 双窗 margin 非同正 / R45 未打过不交易）⇒ 跳过随机臂，stderr 打 [early-stop]；
#   结局逐位不变，--no-early-stop 强制跑满。
#   v0.461：随机臂逐笔重放备忘 --replay-memo N（默认 600 万条≈2.2GB，0=关）——同一 (票, bar, 出场档) 只重放一次，读数逐位不变；
#   [arm] 行带命中率。命中率低且内存够时可调大；命中率 h 下臂阶段约提速 1/(1−0.89h)（全命中实测 ~9×）。
#   v0.447：warm 阶段（信号日 V0 分，profile 占 warm 99%）同样走 --score-workers；且 V0 分**跨研究落盘缓存**
#   （artifacts/cache/v0_score/<源码哈希>/，键 = as-of 帧内容摘要，代码一改自动换目录）——R40/R45/R44/R47~R50
#   同票同信号日只算一次，后跑的研究大部分命中。磁盘量级：每 bar 约 50 字节。CUSTOS_V0_SCORE_CACHE=off 可关。
# ③ R40 预演（46 门 × 5 档；随机臂成簇抽样 v0.399/0.409）
uv run python -m custos.research bear_regime_study --tag r40_v2_pre --gates all --load-workers 4 --score-workers 8 \
  --mining-start <SEG_A_START> --mining-end 2016-12-31 \
  --judgment-start 2022-01-01 --judgment-end 2026-09-04

# ④ R43 挖掘段（窗口内置 A/B；需联网或预热 xdxr 缓存）
uv run python -m custos.research regime_style_study --out artifacts/logs/regime_style/r43_v1.json

# ⑤ R44 扫描（只读挖掘 A∪B）
uv run python -m custos.research plan_rules_replay --scan-widths --tag r44_scan

# ⑥ R45 预演
uv run python -m custos.research bear_regime_study --r45 --tag r45_v2_pre --load-workers 4 --score-workers 8 \
  --mining-start <SEG_A_START> --mining-end 2016-12-31 \
  --judgment-start 2022-01-01 --judgment-end 2026-09-04

# ⑦ R46（729 格 + 100 条簇级置换臂；合成估算单次组合模拟 17~85ms ⇒ 约 2~4 小时 + 收集）
uv run python -m custos.research family_lattice_study --mining-a-start <SEG_A_START> \
  --out artifacts/logs/family_lattice/r46_v1.json
```

**入批条件**：机械判决 `candidate`（R36 ① 为 Δ 中位数 > 0 且过门率 ≥ 50%）。其余结局按各单元回填归档，不入批。
我逐份判读回填，并列出入批名册（候选 id + 单元 + 冻结命令）。

## 步骤 3 · 冻结（截止日前，逐候选）

把**读判定段的命令**逐字定下来，末尾加 `--print-config-hash`（不读判定段交易数据；R39/R40/R44/R45 会先做一次
指数日线的到达校验，需在生产机跑）：

| 单元 | 读判定段命令（v2 缺省布局 = 不给窗口四参） |
|---|---|
| R36（不入批） | ⏹ owner 2026-10-21 撤出——不跑判定段 |
| R39（不入批） | ❌ 挖掘段预演 untested（2026-10-20）——不跑判定段 |
| R40 | `bear_regime_study --tag r40_v2_j --gates all` |
| R43（不入批） | ❌ 挖掘段 falsified（2026-10-20）——不跑判定段 |
| R44（不入批） | ❌ 挖掘段 falsified（2026-10-19，r44_scan）——不入批，不跑判定段 |
| R45 | `bear_regime_study --r45 --tag r45_v2_j` |
| R46（不入批） | ⏹ owner 2026-10-21 撤回——不跑判定段 |

回填：每个候选的 `<tool>:<hash>` 写进对应单元回填区（块外，文本即可）。`--tag/--out-dir/--jobs/count` 不进哈希，可改；其余参数（含 `--timeout`）一个都不能动。

## 步骤 4 · 开批（截止日当天，owner 发令）

```bash
uv run python -m custos.research judgment_batch open --batch-id b1 \
  --candidate <id>:<hash>:<Rxx>:<备注> [--candidate ...]
git add governance/research/judgment_batches.jsonl && git commit -m "batch b1 open" && git push
```

名册一旦开出不增不减；m = 名册人数，各终端自动读取（不可手填）。

## 步骤 5 · 判定段读数（每候选一次，过闸即消耗）

步骤 3 的命令去掉 `--print-config-hash`，加 `--batch-id b1 --candidate-id <id>`。跑完：

```bash
uv run python -m custos.research judgment_batch status --batch-id b1
git add governance/research/judgment_batches.jsonl governance/research/window_usage.jsonl
git commit -m "batch b1 reads" && git push
```

回填：R36 `r36_frozen`、R39 `r39`、R40 `r40`、R43 `r43_judgment`、R44 `r44_judgment`、R45 `r45`、R46 `r46`（各自 tag 用 `*_j`）。
崩溃 / 中断 = 该候选已消耗（claim 已写），**不要重跑**，报给我；需重读只能由 owner 开新批。

## 第二批预告（不在第一批，第一批判定段读完后再跑）

- R47 浮盈保护扫描（只读挖掘 A∪B）：`uv run python -m custos.research plan_rules_replay --scan-protect --trade-dist --load-workers 4 --score-workers 8 --tag r47_scan`，
  然后 `result_digest r47_scan <产物>/_plan_protect_scan__r47_scan.json --tag r47_scan --write --backfill`（报告自动生成）。
- R48 + R49 一条命令（只读挖掘 A∪B；v0.444：R49 内部完整跑一遍 R48 含 200 臂池，同次落 R48 产物，读数与单独跑一致——**不要再单独跑 R48**，否则全宇宙加载 + 重放重复一次）：
  `uv run python -m custos.research tail_explore_study --load-workers 4 --score-workers 8 --tag r49_v1 --r48-tag r48_v1`，然后两条摘要：
  `result_digest r48 artifacts/logs/tail_feature/_tail_feature__r48_v1.json --tag r48_v1 --write --backfill`；
  `result_digest r49 artifacts/logs/tail_feature/_tail_explore__r49_v1.json --tag r49_v1 --write --backfill`。
  （只要 R48 时：`tail_feature_study --load-workers 4 --score-workers 8 --tag r48_v1`。）
