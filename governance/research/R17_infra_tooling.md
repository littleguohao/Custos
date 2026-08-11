# R17 · 工程：工具、性能与可执行规范

> **家族**：工程　|　**证据等级**：L4（实测）　|　**状态**：✅
> **依赖**：服务于：所有单元
> 索引与主图见 [`README.md`](README.md)。证据等级定义同上。原始日志见 git 历史 `B1_BACKTEST_FINDINGS.md`。

## 主题

让研究跑得动、跑得快、跑得可信

## 目标

工具能力清单 + 性能瓶颈定位 + 防重犯机制

## 结论

**① 回测器开关全集**（`backtest_factors.py`）与本轮新增入口清单。

**② 信号标注层已上线**（A 类改动）：必须**三态**且分母用**可评估数**。

**③ 性能实测：瓶颈是评估不是加载 —— 我之前的判断错了。** 扫描太慢的三条对策 + OOM 并行必须先设闸。

**④ 同一个反模式在同一天犯第二次，次日又发现第三处**（TDX 客户端「永不重连」）。第三处在 `collect_holding_quotes.py` —— 14:45/17:00 采集持仓行情的必经之路。
**⇒ 结论：写进文档不等于内化。** 文档只记录历史问题，没变成写新代码时的检查项。
补救 = `tests/test_tdx_connection_hygiene.py`（**可执行的规范**，AST 扫描）。
写这个检查**本身又踩三个坑**（字符串匹配被残留常量骗过 / `except SyntaxError` 把改坏的文件静默跳过 ⇒ 再次全绿 / 把 invalidate 函数误判为反模式）。
⇒ **两条通用教训：检查函数要像普通函数一样有单元测试；反向验证要用可控样本、不要动生产代码。**

---

## 证据与过程

## 1. 工具：`src/custos/research/backtest_factors.py`
研究用回测器（只读本地日线，不改线上）。可组合的开关：
- **入场门槛 `--entry-filter`**：`none` / `j_low`(J<13) / `reversal_k`(J<13+缩量+量底+小实体+小振幅) / `j_macd_turn`(J<13+MACD柱上行)。
- **打分/选择器 `--scorer`**：`baseline`(不选) / `reversal_quality`(反转成色0-4) / `s_shape` / `b1_pullback` / `alpha101` / `alpha_pvcorr` / `low_vol` / `momentum`。
- **交易模拟 `--trade-sim`**：进场=可买日收盘；`--stop-mode low|pct`(+`--stop-pct`)；BBI 移动止盈(站上BBI后连破`--bbi-consec`日卖)；`--time-stop`。
- **择时 `--amv-long-only`**：仅 0AMV『做多』区间进场(读指南针 compass_amv 历史)。
- **组合 `--portfolio`**：固定风险仓位 `--risk-pct`，`--max-concurrent`/`--max-pos`；`--top-n` 横截面择优；`--cost-bps` 成本。
- **周线 `--weekly`**；**内存**：流式逐股加载 + `--max-signals-per-code` + `--summary-only`。
- **数据源 `--data-source tdx|qlib|csv`**(默认 tdx):qlib/csv=`E:\S_DATA`(**含退市股、前复权**,1999-11→2026-02);
  配 `--start/--end` 指定回测区间(walk-forward 用)、`--universe-sdata` 用其全市场宇宙(含退市,可去幸存者偏差)。

## 本轮新增的回测入口（全部未接入选股链）

```
SCORERS      b1_dual / b1_dual_no_res / long_structure / b2 / rsi_state / main_rally
ENTRY_GATES  qsx_gt_dks / j_low_qsx_gt_dks / breakout_pullback_b1 / weekly_j_low /
             j_low_weekly_resonance / j_low_qsx_weekly / b2 / bottom_surge /
             bottom_surge_strict / surge_then_b1 / surge_strict_then_b1 /
             rsi_strong / rsi_bull_div / j_low_rsi_strong / j_low_rsi_div /
             main_rally / main_rally_above
CLI          --scale-out（分批止盈比例，原文"放飞一半"→0.5）
```

### 待跑回测（**判定看 expectancy_R / payoff_ratio / total_R，不看胜率**）

```bash
S=src/custos/research/backtest_factors.py

# ① 分批止盈的价值（M1 的核心验证：胜率不变、盈亏比该提升）
uv run python $S --trade-sim --entry-filter j_low --scorer b1_dual --cost-bps 25 --universe-local --universe-sample 1000
uv run python $S --trade-sim --entry-filter j_low --scorer b1_dual --cost-bps 25 --universe-local --universe-sample 1000 --scale-out 0.5
# 再扫比例：0.3 / 0.5 / 0.7，看 payoff_ratio 与 total_R 的曲线

# ② 止损口径扫描（另一个盈亏比杠杆）
uv run python $S --trade-sim --entry-filter j_low --scorer b1_dual --scale-out 0.5 --stop-mode low
uv run python $S --trade-sim --entry-filter j_low --scorer b1_dual --scale-out 0.5 --stop-mode pct --stop-pct 5
uv run python $S --trade-sim --entry-filter j_low --scorer b1_dual --scale-out 0.5 --stop-mode pct --stop-pct 8

# ③ RSI 因子（H3）
uv run python $S --trade-sim --entry-filter j_low --scorer rsi_state --scale-out 0.5 --universe-local --universe-sample 1000
uv run python $S --trade-sim --entry-filter j_low_rsi_strong --scale-out 0.5 --universe-local --universe-sample 1000
uv run python $S --trade-sim --entry-filter j_low_rsi_div    --scale-out 0.5 --universe-local --universe-sample 1000

# ④ 主升始发点（H4）——两种 CROSS 口径必须都跑
uv run python $S --trade-sim --entry-filter main_rally       --scale-out 0.5 --universe-local --universe-sample 1000
uv run python $S --trade-sim --entry-filter main_rally_above --scale-out 0.5 --universe-local --universe-sample 1000

# ⑤ B2 与底部异动（H2）——⚠️ 已在信号级终审否决（见文末终审节）：B2 全中≡追高、
#   surge_then_b1 跨区间不成立。若仍要跑净值口径，仅限 surge_strict_then_b1 留证用
uv run python $S --trade-sim --entry-filter j_low --scorer b2 --scale-out 0.5 --universe-local --universe-sample 1000
uv run python $S --trade-sim --entry-filter surge_then_b1     --scale-out 0.5 --universe-local --universe-sample 1000

# ⑥ 标记数 → 仓位的可行性（M1 第③点）：按命中标记数分组，看 expectancy_R 是否单调递增
```

**样本要求**：`--universe-local --universe-sample 1000`（带 seed 随机抽样）。此前 100 只且疑似取前 100 个代码，
会全是深市主板、有选择偏差；且 A 档只剩 7 条、无统计效力。

---

## 已上线：信号标注层（A 类改动，2026-08-04）

owner 裁定的三类改动风险分级：

| 类型 | 是否改分层/next_step | 风险 | 需要回测 |
|---|---|---|---|
| **A. 纯标注** | 否 | ≈0 | 不需要 |
| B. 加分/减分 | **是**（改 total→改 A/B/C/D→改"可买"清单） | 高 | **必须** |
| C. 封顶/否决 | **是** | 高 | **必须** |

本次只做 A 类。

⚠️ **重要修正（终审之后）**：我在实现时写过「标注数就是确信度的代理，人可据此定仓位」——
**这个论断已被上方 H1/H2 终审证伪**。终审显示这些因子（b1_dual 系、B2/异动系、
`j_low_qsx_weekly`）的 edge 只存在于 2025-2026 单一 regime，跨区间不成立；
`surge_strict_then_b1` 甚至跨 seed 方向翻转。既然它们没有稳定的预测力，"标注多⇒确信度高"
就没有依据，**不得据标注数决定仓位**。

标注层保留的理由降级为：① 它是 A 类改动，不改分层/next_step，**不会造成损失**；
② 作为**观察记录**——每天看表能积累"这些形态在什么行情下出现"的直觉，也便于日后复盘
"被终审否决的因子在实盘里长什么样"；③ 负向标注（出货形态）本就是既有的证据层信息。
**它现在明确不是交易依据。** 若日后某因子通过跨窗终审，再单独把它升级为加分。

### 实现

- `screening/signal_labels.py`：12 个标注（11 正向 + 1 负向），**三态** hit/miss/unavailable
- `enrich_candidates.compute_metrics` 落盘 `signals` 字段，**复用已算的** `zx` /
  `distribution` / `daily_j` / `weekly_j_state` / `platform_pullback`
- `candidate_table`：新增「🏷️ 信号标注一览」区块（**逐个标注列出命中的票**，不只报数量）
  + 主表新增「标注」列（`4/11 QD·RS·B2·SB`，负向前缀 ⚠️）

### 为什么必须三态、且分母用可评估数

`min_list_days=60`，而 `qsx_gt_dks` 需 ≥120 根（DKS=MA114）、`surge_then_b1` 需 ≥200 根
（9 个月新高）。实测 70 根的新票有 **4 项 unavailable**。若把"算不出来"显示成"未命中"，
读者会误以为"这票不符合条件"，而实际是"不知道"——这正是本次审计反复出现的失效模式。
故命中率分母是**可评估数**：新票显示 `1/7` 而不是 `1/12`。

### 性能

向量化优化后单票 25.3ms（候选 300 只 → 7.6s，stage 超时 600s）：

| 优化 | 前 | 后 |
|---|---|---|
| CCI 的 AVEDEV（`rolling.apply` → `sliding_window_view`） | 1.90ms | 0.75ms（**2.5×**，逐值一致） |
| 周线共振（注入已算的 daily_j/weekly_j，跳过 resample） | 3.96ms | 0.002ms（**2273×**） |
| B2（注入已算的 J 序列） | 1.01ms | 0.26ms（3.9×） |

`resample("W-FRI")` 单次 2.3ms 是周线共振的主要开销，而 enrich 的 `weekly_j_state` 已经算过
一次同口径的周线 J，重复算等于白付。

### 边界由测试钉住

`tests/test_signal_labels.py::TestLabelsNeverAlterSelection`：对 4 种标注组合（全 miss /
多项命中 / 全 unavailable / 含负向）断言剥离标注后**选股输出逐字节一致**、`bucket` 与
`next_step` 不变；另断言 `score_candidates` **不得消费 signals**（一旦消费就成了 B 类改动）。

---

### ② 同一个反模式在同一天犯了第二次，次日又发现第三处

`503b77d`：我修了 `local_tdx_data._get_client()` 的「永不重连」，写进
`DATA_SOURCE_PRINCIPLE.md` 作为反模式，还立了规范「所有走 TDX 协议的调用**必须**经
`_with_client_retry()`」。
`3c7c833`（**同一天**）：创建 `tdx_ext_quotes.py` 时写了字面上一模一样的单例，
也没用 `_with_client_retry`。
今日再查全仓，发现**第三处**：`collect_holding_quotes.py` —— 而它是 14:45/17:00
采集持仓行情的必经之路，连接死了整条链的行情就没了（已修，三处协议调用改走
`_client_call` 包装）。

**结论：写进文档不等于内化。** 文档只记录了历史问题，没变成写新代码时的检查项。
所以补救措施是 `tests/test_tdx_connection_hygiene.py`——**可执行的规范**：
AST 扫描所有用 `global` 缓存 TDX 客户端的函数，要求存在「除 `is None` 之外的重建路径」
（`force_new` 形参 / 连接时效判断 / 委托给已知带重连的实现）。

写这个检查本身也踩了三个坑，都记在文件里：
- 第一版用**字符串匹配**标志词，结果一个残留的模块级常量 `CLIENT_MAX_AGE_SEC`
  就骗过了它（反向验证时照样全绿）；
- 第二版改 AST 后 `except SyntaxError: return []`，反向验证脚本用正则粗暴删行
  把文件改坏 ⇒ 语法错误被静默跳过 ⇒ **再次全绿**；
- 第三版把 `_drop_ext_client`（只把客户端置 None 的 invalidate 函数）误判为反模式——
  它本身就是重连机制的一部分。

⇒ 两条通用教训：**检查函数要像普通函数一样有单元测试**（已补 8 个合成样本用例）；
**反向验证要用可控样本，不要动生产代码**。

## 扫描太慢的三条对策（2026-08-05）

一轮 25 个方案要几小时。根因：串行跑 25 次「读 1000 只票的 vipdoc → 逐票算前复权 →
逐 bar 评估」，其中**数据加载与前复权的结果对所有方案完全相同**，却被重复做了 25 遍；
同时 CPU 只用一核。

**① `--jobs N` 并行**（已落地）。方案之间无共享状态（各写自己的结果文件）⇒ 天然可并行。
⚠️ 先用 `-j 1` 跑一个方案把 `data/market/xdxr/` 权息缓存焐热再开并行：缓存冷时
前复权要经通达信协议逐票取权息，N 个进程各开一条连接可能被限流，那时并行只会一起失败。

**② C 组 8 个方案只做 1 次真回测**（已落地）。它们的回测参数与 B 组 `pct_12` /
`pct_12_amv` **完全相同**——C 组 `common` 里的 `--portfolio` 和 extra 里的
`max_concurrent` / `max_pos` / `risk_pct` 都只改资金曲线，不改 trades。
资金曲线模拟是**毫秒级**、回测是**分钟级**。新增 `backtest_factors --from-trades`
跨组复用逐笔，只剩 `--top-n` 那条必须自己跑（`collect_all`，逐笔是未去重全候选，
与去重后完全不同口径）。**整轮 25 次回测 → 18 次**。

复用**必须先核对口径**——拿另一套止损参数的 trades 去跑组合，出来的曲线看不出任何异常。
为此新增 `trades_signature`：影响逐笔的全部参数（含 `collect_all`、universe 摘要），
排除组合层参数（它们不改 trades）。不一致直接非零退出，不做猜测性兼容。
复用失败会自动退回全量回测——全量永远正确，只是慢，不能让「省时间」变成「少一个方案」。

顺带修了一个**结果文件不自述身份**的问题：payload 原先**没有记录**
`scale_out / breakeven / trail / stop_trigger / stop_tick_buffer / cost_zone_*`，
而这些恰好就是 M2 在扫的参数 ⇒ 事后无法确认一个文件是哪套参数跑出来的。现已随指纹落盘。

**③ 把「方案外循环 / 股票内循环」翻过来**（未做，收益最大）。每只票读一次盘、
算一次前复权，然后在这只票上跑完 17 个逐笔方案（A 组 11 + B 组 6），
加载与复权从 17 次降到 1 次。值不值得取决于加载在总耗时里的占比，
所以先给 `backtest_factors` 加了耗时拆分：结束时打印
`[TIME] 加载(含前复权) Xs / 评估 Ys（加载占 N%）`。**下一轮跑完先看这个数再决定**。

---

## OOM Kill：并行前必须先设闸（2026-08-05）

「全市场 OOM」这条早就记在第 5 节，`--top-n`(collect_all) 大样本尤重。**而我上面
刚加的 `--jobs N` 恰好是把内存乘 N 的方向**——不设闸就是在给 OOM 加油。

### collect_all 为什么重一个量级

非 collect_all 时下一个候选从 `i = tr["exit_idx"] + 1` 开始（跳到出场之后，非重叠）；
collect_all 是 `i += step`，`step=1` ⇒ **每根 K 线**都可能产生一条候选。
同样 1000 只票、500 根 K 线，逐笔条数差一个量级。

实测量级（合成 50k 笔）：单笔 dict ≈ 1.6KB ⇒ **50k 笔 ≈ 82MB** Python 对象；
`json.dumps(indent=2)` 再拼出 **17MB 字符串**（无缩进 12MB，`indent=2` 放大 1.36 倍）。

### 三道措施

| 措施 | 做法 |
|---|---|
| `--jobs` 自动收敛 | `_cap_jobs` 按可用内存（Linux `/proc/meminfo` / Windows `GlobalMemoryStatusEx`）÷ `MEM_PER_JOB_MB` 定上限，留 20% 余量 |
| 重活隔离 | `_is_heavy`（含 `--top-n`）的方案**单独串行**，不与别人抢内存 |
| 降低单进程峰值 | 落盘改 `write_json` 流式（不再先拼整串）；复用路径**不重写 trades**（与源文件逐字相同，`trades_reused_from` 指明来源）；组合曲线算完立刻 `del` 源 dict |

并且 `backtest_factors` 每轮打 `[MEM] 峰值 XXXMb / N 笔`，`MEM_PER_JOB_MB` 现在是
保守估计（1200MB），**跑一轮后按实测校准**。失败汇总里也加了「退出码 137/-9 ⇒ 被 OOM
kill，降 --jobs 或先单跑重活」的提示——否则一个被 kill 的方案在报表里只是少一行。

---

## 性能实测：瓶颈是评估不是加载，我之前的判断错了（2026-08-05）

```
[TIME] 加载(含前复权) 8s / 评估 1238s（加载占 1%，1000 只票）
```

**加载只占 1%。** 我此前认定「25 个方案重复加载 25 遍数据是主因」，据此提出「把方案外循环
翻成股票外循环」是收益最大的改造——**实测证明那是错的**，那条改造总共只能省 8 秒。

连带修正两条：

- 「切 qlib 能省掉前复权、所以更快」——前复权也在那 8 秒里，**加速价值归零**。
  切 qlib 的理由只剩**去幸存者偏差**这一个。
- 单方案 ~1247s（20.8 分钟），99% 是 `evaluate_trades` 的逐 bar 评估：
  1000 只 × 500 根 ≈ **50 万次 as-of 评估，每次约 2.5ms**。

⇒ 目前唯一的实用加速手段是 **`--jobs N` 并行**（纯 CPU-bound，近线性）。
`-j 6` 可把 35 个方案从约 12 小时压到 2 小时上下。向量化 `evaluate_trades` 收益更大但会
动核心逻辑（可能改结果），暂不碰。

另：`[MEM] 峰值 未知` —— Windows 上的探测第一版失败了（只试了 `ctypes.windll.psapi`
且没设 restype/argtypes，句柄可能被截断）。已改成 `kernel32.K32GetProcessMemoryInfo` 与
`psapi.GetProcessMemoryInfo` 双路兜底，并把失败原因打进 `[MEM] 未知(原因)`
——**静默返回 None 的诊断价值为零**，这正是本仓库反复踩的「静默降级」坑。

---
