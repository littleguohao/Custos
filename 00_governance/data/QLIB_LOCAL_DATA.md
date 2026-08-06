# Qlib 本地数据（S_DATA）接口使用方式

> `E:\S_DATA` 下的 qlib bundle / 单票 CSV，通过 `07_tools/screening/s_data.py` 只读访问。
> **只服务研究与回测链，live 链不用。**
>
> 2026-08-06 新建。此前它的关键事实只写在 `s_data.py` 的 docstring 里，
> 治理文档一字未提 —— 而这三周所有「跨年 walk-forward / 去幸存者偏差」的结论都依赖它。
>
> 标记：✅ 可用 / ⚠️ 有已知问题。性能数据来自 `probe_data_sources.py` 实测。

## 为什么需要它：它是 tdx 数据拿不到的两件事

| 能力 | tdx 本地 vipdoc | S_DATA qlib bundle |
|---|---|---|
| **退市股** | ❌ 只有当前挂牌股 | ✅ **含退市股**（point-in-time 宇宙） |
| **前复权** | 自算（xdxr 本地计算，从未对账） | ✅ **数据本身已前复权** |
| 覆盖区间 | 到最新交易日 | ⚠️ 1999-11 → **2026-02 截止** |
| 连续性 | 连续 | ⚠️ **2020-09-28 → 2021-07-30 约 10 个月缺口** |

⇒ 两个用途：

1. **去幸存者偏差。** vipdoc 只含活到今天的公司，用它回测会系统性**抬高**表现。
   `--universe-sdata` 是唯一能消这个偏差的路径。
2. **给自算前复权做独立参照。** qlib 数据本身是前复权，可与 `adjust_factors` 的自算结果
   **逐日比对** —— 这是零成本的交叉验证，`adjust_factors` 至今**从未对账过**
   （见 `DATA_SOURCE_PRINCIPLE.md` 问题①）。**尚未做。**

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

1. **前复权对账**：拿 qlib 的前复权序列作独立参照，逐日比对 `adjust_factors` 的自算结果。
   挑有过大比例送转/除权的票，两者应吻合；不吻合就能定位到具体哪一天、哪个事件。
   这是回答「自算前复权准不准」的最直接手段，**零额外数据成本**。
2. **核实 qlib 是否覆盖 BJ**：若覆盖，它可能是 BJ 前复权问题的解。
3. 用 `--universe-sdata` 复核那些在 tdx 宇宙上得到的结论（幸存者偏差方向是**抬高**表现，
   去偏后只会更低）。
