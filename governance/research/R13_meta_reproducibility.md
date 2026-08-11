# R13 · 元：可复现性（宇宙与数据窗口漂移）

> **家族**：元层　|　**证据等级**：L4　|　**状态**：⚠️ 已加开关，默认关 ⇒ 历史批次不可复现
> **依赖**：约束：R10 R11 的跨批次比较
> 索引与主图见 [`README.md`](README.md)。证据等级定义同上。原始日志见 git 历史 `B1_BACKTEST_FINDINGS.md`。

## 主题

扫描结果能不能复现

## 目标

钉死宇宙与数据窗口

## 结论

扫描期间**宇宙与数据窗口一直在漂移**（从 run log 才发现）。两个陷阱：

**① 只加 `--end` 钉不住** —— 加载顺序有坑。
**② `--start/--end` 管不到宇宙那一半** —— 随机抽样每次抽到另一组票。

已加两个开关（`--pin-universe` 等，**默认关，开了才可复现**）+ 可复现批次入口。

⚠️ **推论：开关之前跑的所有批次都不严格可复现。** 这不影响同一批次内部的相对比较，但跨批次比较要当心。

---

## 证据与过程

## ⚠️ 未解决：扫描期间**宇宙与数据窗口在漂移**（2026-08-05 晚，从 run log 发现）

owner 那轮 log 里，本该完全相同的回测出现了不同笔数：

| 方案 | universe | 笔数 | 回测参数 |
|---|---|---|---|
| `B_stop_pct/pct_12` | — | **1106** | `--stop-pct 12` |
| `C_portfolio/pf_c5_p20` | 5535 只 | **1106** | 同上（+组合层参数） |
| `C_portfolio/pf_c3_p20` | 5535 只 | **1092** | 同上 |
| `C_portfolio/pf_c2_p20` | **5536** 只 | **1087** | 同上 |
| `C_portfolio/pf_c5_p05` | **5536** 只 | **1087** | 同上 |

组合层参数（`max_concurrent` / `max_pos` / `risk_pct`）**不影响 trades**，所以这四个的
逐笔本该逐字相同。实际有两处漂移：

1. **新增了一只票**：`list_local_vipdoc_codes()` 从 5535 变成 5536 ⇒ 排序后的列表变了
   ⇒ `sample_codes(base, 1000, seed=0)` 抽到的**是另外一组 1000 只**。
2. **K 线窗口滑动**：`pf_c5_p20`(1106) 与 `pf_c3_p20`(1092) 的 universe 都是 5535，
   笔数却差 14 ⇒ 是 `.day` 文件被追加了新 bar，而 `--count 500` 是「从今天往前数 500 根」
   ⇒ 窗口右端滑动，进出场都跟着变。

每轮耗时 **~1230~1285s ≈ 20.5 分钟**，25 个方案跨 8.5 小时，横跨了通达信的盘后下载。

**后果**：跨方案比较里混着 ~1~2% 的笔数噪声，而这与某些被测效应同阶
（如 `pct_12` vs `pct_08` 期望 -1.6%）。也部分解释了上一节那个「`pf_c2_p20`(敞口 40%)
比 `pf_c5_p20`(敞口 100%) 亏更多」的矛盾——两者根本不是同一个宇宙、同一个窗口。

**顺带验证了 `trades_signature` 的价值**：`codes_digest`/`n_codes` 会捕捉到这种漂移，
`--from-trades` 会拒绝复用并自动退回全量回测，不会静默拿错口径的 trades 去算组合。

**待定的修法**（口径改动，需 owner 拍板）→ **已实现（2026-08-05）**：

### ⚠️ 只加 `--end` 钉不住——加载顺序有坑

`--start/--end` 一直是可用的（`--cross-window` 路径就在用），但**单给 `--end` 不够**：

```
get_ohlcv_table(count=500)        local_tdx_data.py:674   先 df.tail(500)
  ↓
_load_bars_local                 backtest_factors:1915   才按 start/end 过滤，再 tail(count)
```

文件从 N 根变 N+1 根时：`tail(500)` 取的是 `[N-499, N+1]` → `end` 过滤掉最新那根 →
**只剩 499 根，且最早那根往前挪了一天**。窗口既缩水又滑动。

⇒ 必须**两端都给 + 放大 `--count`**（`WINDOW_COUNT=1500`），让日期过滤而不是 `tail`
决定窗口——这正是脚本里 `--cross-window` 那条注释说的。

### 而 `--start/--end` 管不到宇宙那一半

universe 来自 `list_local_vipdoc_codes()` 的**目录列举**，跟日期无关。
5535→5536 会让 `sample_codes(base, 1000, seed=0)` 抽到**另一组** 1000 只——
seed 固定没用，**被抽的池子变了**。

### 两个开关（默认关，开了才可复现）

| 开关 | 作用 | 实现 |
|---|---|---|
| `--window START END` | 钉死 K 线窗口 | 传 `--start/--end` + `--count=WINDOW_COUNT` |
| `--pin-universe` | 钉死宇宙 | 先跑一次 `backtest_factors --dump-codes` 落代码表，全部方案改用 `--codes-file` |

`--dump-codes` 在 codes 解析完、**任何 K 线加载之前**就返回，所以只花一次目录列举的时间。
不在扫描脚本里直接抽样，是因为那要 import `local_tdx_data`（依赖 TDX_ROOT）或 `s_data`
（依赖 S_DATA_ROOT），在没这些数据的机器上会直接失败；交给 `backtest_factors` 复用它已有的
universe 逻辑。落盘失败时**退回各自抽样并明确告警**，不中断扫描。

两项都进了文件名指纹（`s1000_w20240801-20260805_u`），钉过的批次与没钉的不会混着汇总。
报表在**未同时钉死两者**时会打一条「本批不可复现」的警告并给出可复现跑法。

可复现跑法：

```bash
uv run python src/custos/research/m2_stop_sweep.py \
    --window 2024-08-01 2026-08-05 --pin-universe -j 4
```

---

## 可复现批次的跑批入口（2026-08-05 夜）

`src/custos/pipeline/screening/run_m2_sweep.cmd` —— Windows 后台跑批。

```powershell
Start-Process -WindowStyle Hidden -FilePath "src\pipeline\screening\run_m2_sweep.cmd"
Get-Content -Wait -Tail 40 artifacts/logs\m2_sweep\sweep_run.log     # 看进度
```

四个设计点（每条都对应踩过的坑）：

1. **Windows 没有 nohup**，而关掉 PowerShell 会给同控制台的子进程发 `CTRL_CLOSE_EVENT`
   ⇒ 扫描被一起带走。`Start-Process` 起独立进程才能活下来。
2. **stdout 与 stderr 必须合并**（`>> log 2>&1`）：`[TIME]`/`[MEM]`/`[INFO] universe=`/
   `[WARN]` 全走 stderr，只重定向 stdout 会把判断依据全丢掉。
3. **第一步 `-j 1` 单进程焐热 xdxr**：`--sample 3000` 里约 2000 只是上一轮（抽 1000 只）
   没碰过的，权息缓存全冷；一上来就并行会 N 条连接同时取权息，可能被限流甚至拒连。
4. **`&&` 串联 = 免费冒烟测试**：第一步（约 1 小时）已覆盖钉宇宙
   (`--dump-codes` → `--codes-file`)、钉窗口、落盘、报表全链路。配置写错 1 小时内暴露，
   而不是 6 小时后发现整夜白跑。

参数：`--sample 3000 --window 2024-08-01 2026-08-05 --pin-universe -j 6`。
窗口约 490 根 K 线，与此前 `--count 500` 的实际窗口基本等长，便于与 s1000 批次对读趋势
（但**指纹不同、不可混着汇总**）。`-j 6` 会被自动收敛：先按 CPU 核数（评估是纯 CPU-bound，
超订只会互相抢，还会挤掉要服务 xdxr 的 TdxW），再按可用内存 ÷ `MEM_PER_JOB_MB`。

**这一轮要解决的三件事**：

| 问题 | 这轮怎么解 |
|---|---|
| amv 方案只有 224~255 笔、组合最好的只成交 21 笔 | 样本 1000→3000，笔数约 ×3 |
| 宇宙/窗口漂移（5535→5536、同参数笔数 1106/1092/1087） | `--window` + `--pin-universe` 双钉 |
| 幸存者偏差 | ⚠️ **这轮解决不了**（仍是 tdx vipdoc）；要另跑 `--data-source qlib` |

预计 28 次真回测（35 个方案 − 7 个走 trades 复用），3000 只约 62 分钟/次，
`-j 6` 下总计约 6~7 小时。

---
