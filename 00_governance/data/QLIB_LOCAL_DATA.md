# Qlib 本地数据（S_DATA）接口使用方式

> `E:\S_DATA` 下的 qlib bundle / 单票 CSV，通过 `07_tools/screening/s_data.py` 只读访问。
> **只服务研究与回测链，live 链不用。**
>
> 2026-08-06 新建。此前它的关键事实只写在 `s_data.py` 的 docstring 里，
> 治理文档一字未提 —— 而这三周所有「跨年 walk-forward / 去幸存者偏差」的结论都依赖它。
>
> 标记：✅ 可用 / ⚠️ 有已知问题。性能数据来自 `probe_data_sources.py` 实测。

## 为什么需要它：它是 tdx 数据拿不到的一件事

| 能力 | tdx 本地 vipdoc | S_DATA qlib bundle |
|---|---|---|
| **退市股** | ❌ 只有当前挂牌股 | ✅ **含退市股**（point-in-time 宇宙） |
| 复权 | 乘法前复权（自算 xdxr，**已对账通过**） | ⚠️ **加法调整（减去累计现金分红）** —— 见下 |
| 覆盖区间 | 到最新交易日 | ⚠️ 1999-11 → **2026-02 截止** |
| 连续性 | 连续 | ⚠️ **2020-09-28 → 2021-07-30 约 10 个月缺口** |

⇒ 用途只剩**一个**：**去幸存者偏差**。vipdoc 只含活到今天的公司，用它回测会系统性
**抬高**表现；`--universe-sdata` 是唯一能消这个偏差的路径。

⚠️ **原先写的「给自算前复权做独立参照」这条已经完成，而且结论反过来了** ——
对账证明**我们的自算前复权是对的，qlib 侧的价格口径有问题**。见下。

---

## ⚠️⚠️ 最要紧：qlib 的价格是**加法调整**，不是乘法前复权（2026-08-06 对账实测）

此前本文写的「数据本身已前复权」**是错的**。`reconcile_qfq.py` 的对账证明：

### 判据

同一个「无事件段」内：

| 约定 | 不变量 |
|---|---|
| 乘法前复权 `adj = raw × f` | `adj/raw` 恒定，`raw−adj` 随价格变动 |
| **加法调整** `adj = raw − c` | **`raw−adj` 恒定**，`adj/raw` 随价格变动 |

### 实测（600519 / 600612 / 600622）

```
raw − qlib 是分段常数：
  600519   194.99 → 173.31      差 21.68 元 = 2021 年报分红 21.675 元/股
  600612   6.91 → 5.46 → 4.00   逐段差 1.45 / 1.46 = 每股分红
  600622   0.07 → 0（末段比值恰好 1.0）

qlib/raw 完全不恒定：茅台同一段内从 0.87836 游走到 0.89614
```

段边界恰好落在除权日，相邻段的差**恰好等于该次每股现金分红** ⇒ 结论确定。

### 后果：百分比收益被系统性放大

加法调整保留**绝对价差**，但把分母减小了 ⇒ 百分比收益一律偏大：

```
放大幅度 ≈ c / (raw − c)

  600519  c=173, raw≈1500  ⇒ 放大约 13%
  600612  c=6.91, raw≈39   ⇒ 放大约 21%
  600622  c=0.07, raw≈3.17 ⇒ 放大约 2.3%
```

**高分红股受影响最大。** 直接证据是 qlib 的涨跌幅**超过涨跌停限制**（物理不可能）：

| 日期 | 股票 | tdx | qlib | 限制 |
|---|---|---|---|---|
| 2023-04-07 | 600612 | +9.9928% | **+11.0737%** | 10% |
| 2023-04-28 | 600612 | +10.0000% | **+10.9795%** | 10% |
| 2023-10-27 | 600612 | +9.9929% | **+10.7523%** | 10% |
| 2021-09-27 | 600519 | +9.5041% | **+10.7404%** | 10% |

对照组：**tdx 的复权收益与未复权收益 0 天偏离**（`|t-raw| = 0.0000%` 每一行都是）——
非事件日复权只是乘同一个当日因子，收益必须与未复权一致，这是数学上必须成立的。
qlib 偏离 134/186/3 天。

### ⚠️ 两个 bundle 的字段集不同 —— 可能是两种价格口径

`--qlib-fields 600519` 实测：

```
2006_2020   open/high/low/close/volume + **factor** + **change**   ← 有 factor
2021_2026   只有 open/high/low/close/volume                        ← 没有 factor
```

**上面「加法调整」的结论只对 2021_2026 成立**（对账窗口 2021-08~2026-01 整个落在它里面）。
有 `factor` 的老 bundle 很可能是**标准 qlib dump（乘法复权）** —— 尚未验证。

⇒ **`load_bars_qlib` 会把两个 bundle 直接 `concat`**，而那 10 个月缺口正好在两者之间
⇒ **任何长窗口都会跨过去**，接出来的序列在缺口两侧口径不同、收益率在接缝处失真。
而且是**静默的**：拼接不报错，结果看起来就是一条完整曲线。

已在 `s_data._warn_if_mixed_convention` 加告警（跨 bundle 且字段集不同时出声，
每个代码只警告一次以免跑批刷屏）。**但告警只是让它可见，口径问题本身没解决。**

验证老 bundle 口径的方法（tdx vipdoc 约覆盖近 8 年，2018-2020 与老 bundle 有重叠）：

```bash
uv run python 07_tools/local_tdx/reconcile_qfq.py --convention 600519 \
    --win 2018-01-02 2020-09-25
```

⚠️ **必须分 bundle 分别对账，不要跨缝比** —— 跨缝的分歧会混进「两种口径」这个因素。

### 这对「去幸存者偏差」意味着什么

**不能直接用 `--data-source qlib` 做百分比收益的回测**（B1 的止损、J 值、涨跌幅全是百分比）。
可能的出路，按可行性排：

1. **还原真实价格**：`raw = qlib + c`。c 分段常数、段边界=除权日 ⇒
   对**仍在市**的票可用 tdx 的 `raw_close` 直接求 c。
   但**退市股求不出 c**，而「含退市股」正是用 qlib 的唯一理由 ⇒ 这条只能解一半。
2. **看 bundle 里有没有 `factor` 字段**：`s_data._FIELDS` 只读
   `open/high/low/close/volume`，**没读 factor**。标准 qlib bundle 通常有它。
   有的话就能在 bundle 内部还原。⇒ `reconcile_qfq.py --qlib-fields 600519` 可查。
3. **换去偏路径**：另找 point-in-time 的退市股名单 + 价格源。

⇒ **在解决之前，所有基于 `--data-source qlib` 的结论都要打折**，包括那些
「跨年 walk-forward 证伪」的结论（`research/B1_BACKTEST_FINDINGS.md` 结论 #8 等）——
它们用的收益率被放大过。

---

## 复核工具

```bash
# 全量对账（自动挑窗口内除权影响最大的票）
uv run python 07_tools/local_tdx/reconcile_qfq.py --auto 20

# 单只票明细：逐日数字 + 谁错（未复权收益作第三方基准）+ 涨跌停越界
uv run python 07_tools/local_tdx/reconcile_qfq.py --detail 600519

# 复权约定探测：乘法 vs 加法，并列出每段的调整量 c
uv run python 07_tools/local_tdx/reconcile_qfq.py --convention 600519

# bundle 实有字段（看有没有 factor 能还原）
uv run python 07_tools/local_tdx/reconcile_qfq.py --qlib-fields 600519
```

---

## 目录结构

```
S_DATA_ROOT/                        # 环境变量，默认 E:\S_DATA
├── Q_DATA/                         # qlib bundle（多个，按区间分）
│   ├── 2006_2020/                  # 1999-11 → 2020-09
│   │   ├── calendars/day.txt       # 交易日历（bin 数据按此逐日对齐）
│   │   ├── instruments/all.txt     # 宇宙清单
│   │   └── features/{code}/*.bin
│   └── 2021_2026/                  # 2021-08 → 2026-02
└── CSV_DATA/                       # 2021_2026 bundle 的单票 CSV 冗余副本
    └── {code}.{MKT}-all-latest.csv # 列：Date,Code,Open,High,Low,Close,Volume,Amount
```

**qlib bin 格式**：`np.fromfile(dtype='<f4')`，**首元素 = start_index**，
其后与 `calendars/day.txt` 逐日对齐；停牌/未上市段为 `NaN`。

---

## 五个公开函数

| 函数 | 状态 | 实测 | 说明 |
|---|---|---|---|
| `list_bundles(root)` | ✅ | **1.1ms** / 2 个 | 发现 bundle 并读出各自区间 |
| `list_universe(root, source)` | ⚠️ | **5.0ms** / 5486 只 | 宇宙清单，见下方「解析陷阱」 |
| `code_to_qlib_dir(code6, bundles)` | ✅ | — | 6 位代码 → 各 bundle 里的目录与 instrument 名 |
| `load_bars_qlib(codes, count, start, end, root)` | ✅ | **8.6ms**/股 | 跨 bundle 段拼接去重 |
| `load_bars_csv(codes, count, start, end, root)` | ✅ | **4.3ms**/股 | CSV 副本，只覆盖 2021_2026 |

返回形态与 `backtest_factors` 的 loader 约定一致：
`{6位代码: DataFrame[date, open, high, low, close, volume]}`。

**纯只读，绝不 raise** —— 失败返回空 dict 并往 stderr 打 WARN。

### CLI 接入点

```
backtest_factors.py --data-source qlib|csv --universe-sdata [--s-data-root PATH]
m2_stop_sweep.py    --data-source qlib|csv
launch_point_study.py --s-data-root PATH
```

⚠️ `--data-source` 换了就是**换宇宙**，结果与 tdx 批次**不可比**。
`m2_stop_sweep` 已把数据源写进结果文件名指纹，防止两批混着汇总。

---

## ⚠️ 解析陷阱：`instruments/all.txt` 是制表符分隔的

```
SH600000	1999-11-10	2026-02-27
```

**旧实现 `codes.add(ln[-6:])` 取整行末 6 字符** ⇒ 取到的是结束日期尾巴。
2026-08-06 实测宇宙里混进了 `'-06-09'`、`'-09-25'` 两条垃圾，
**而函数照样"成功"返回 5486 项、零告警** —— 又一次静默失效。

现改为：按空白切分取第 0 段 → 抽数字 → **校验必须 6 位**；
并在剔除率 >5% 时**大声告警**（若哪天 bundle 换格式，`ln[-6:]` 那种写法会让整个宇宙
静默变成日期碎片而函数照样返回）。由 `tests/test_s_data.py::TestListUniverseParsing` 锁住。

⇒ 影响面：所有 `--universe-sdata` 的研究结论（跨年 walk-forward、去幸存者偏差）
此前的宇宙里都有这两条垃圾。占比极小（2/5486），但**这类 bug 的问题不在占比，
在于它不报警**。

---

## ⚠️ 两个数据坑（用它出结论前必须知道）

### ① 2020-09-28 → 2021-07-30 约 10 个月缺口

两个 bundle 之间不连续。跨越这段的回测窗口会**静默丢掉 10 个月**——
`load_bars_qlib` 做的是「跨 bundle 段拼接去重」，缺口段没有数据、不会报错。

⇒ 设计跨年窗口时要么避开这段，要么明确接受它。
`--cross-window` 的 2022-01→2024-12 恰好在缺口之后，不受影响。

### ② 数据到 2026-02 截止

比 tdx（到最新交易日）少半年。所以：

- **不能用它验证近半年的策略表现**
- 与 tdx 批次对读时要注意窗口右端不同

---

## 与 tdx 口径的差异（对读时必须记住）

| 维度 | tdx vipdoc | qlib bundle |
|---|---|---|
| 宇宙 | 5536 只，仅当前挂牌，**含 BJ** | 5486 只，**含退市股**，BJ 覆盖未核实 |
| 复权 | 自算 xdxr（未对账；BJ 拿不到权息） | 数据本身已前复权 |
| 右端 | 最新交易日 | 2026-02 |
| 连续性 | 连续 | 2020-09~2021-07 缺口 |
| 读取速度 | 4.7ms（read_vipdoc_daily） | 8.6ms（load_bars_qlib） |

⚠️ **两者宇宙数量接近（5536 vs 5486）纯属巧合**，构成完全不同：
前者是「今天还在的票」，后者是「历史上存在过的票（截至 2026-02）」。
数量接近容易让人误以为可以直接对读 —— 不可以。

⚠️ **切 qlib 的加速价值为零。** 曾以为「qlib 已前复权 ⇒ 省掉 xdxr 计算 ⇒ 回测更快」，
实测 `[TIME] 加载(含前复权) 8s / 评估 1238s（加载占 1%）` —— 加载根本不是瓶颈。
切 qlib 的理由只剩**去幸存者偏差**这一个。

---

## 待做

1. **查 bundle 有没有 `factor` 字段** —— 决定 qlib 数据能否救回来：
   `reconcile_qfq.py --qlib-fields 600519`。`s_data._FIELDS` 目前只读 5 个字段。
2. **若有 factor**：在 `s_data` 里还原真实价格再对外提供，并把加法调整这件事
   封在模块内部（调用方不该知道 bundle 用什么约定）。
3. **若没有 factor**：对仍在市的票用 tdx 的 `raw_close` 求 c 还原；退市股需另找路径
   —— 而退市股正是用 qlib 的唯一理由，所以这种情况下「去幸存者偏差」这条路要重新设计。
4. **复核依赖 qlib 的历史结论**：那些「跨年 walk-forward 证伪」的结论用的收益率被放大过
   （高分红股放大 13~21%），需要在还原后重跑。
5. 核实 qlib 是否覆盖 BJ。
