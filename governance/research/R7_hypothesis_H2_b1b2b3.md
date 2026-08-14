# R7 · 假设 H2：B1-B2-B3 体系与底部异动

> **家族**：假设　|　**证据等级**：L3　|　**状态**：❌ 全否决
> **依赖**：同批终审：R6（基准数据在 R6）｜上游材料：other/B1.pdf
> 索引与主图见 [`README.md`](README.md)。证据等级定义同上。原始日志见 git 历史 `B1_BACKTEST_FINDINGS.md`。

## 主题

B2 / 底部异动能否作验证信号或入场门槛

## 目标

把 B1 分成「启动了」和「没启动」两组

## 结论

**全部否决。**

| 口径 | 裁决 | 理由 |
|---|---|---|
| B2 作**验证信号** | ❌ | **B2 全中 ≡ 追高** |
| B2 作**入场门槛** | ❌ | 5,773 条无增益 |
| 底部异动四 gate | ❌×3 | 三个直接否决 |
| `surge_strict_then_b1` | ❌ | 三项终审全挂 ⇒ **regime 过拟合** |

`surge_strict_then_b1` 的死法值得记：跨 seed **方向翻转**（38.8% vs 60.2%）、跨区间 **零信号**（`--allow-empty` 确认非故障）、133 条信号**全部集中 2025-2026**（最密 2025-11 单月 37 条）。首轮的 H20 60.2% / H60 63.8% 是**单一行情区间的产物**。

⇒ **B2/异动系全部不接入选股链。**

---

## 证据与过程

## 待验证假设 H2：B1-B2-B3 体系（来源 `other/B1.pdf`，2026-08-03）

**这是假设，不是结论。** 首轮判读（2026-08-03）见「H2 第一轮回测判读」：
B2 两个口径与底部异动三 gate 均**否决**；`surge_strict_then_b1` 首轮疑似幸存，
终审跨 seed 方向翻转 + 跨区间零信号，**正式否决**（见文末「H1/H2 终审」）。
H2 全部方向至此均有否决结论，不得据此改选股链。

### 原文体系（B1.pdf p16-17）

| | 定义 | 确认条件 |
|---|---|---|
| B1 | 不同**时间**周期下的多个相对低点 | 3 个交易日内有效上涨 |
| B2 | 不同**空间**维度下的多个相对低点 | 2 个交易日内必须有效放量 |
| B3 | 不同时间维度下持续上涨趋势的**中部**位置 | 不破坏 + 突破前高确认 |

三种排序（原文）：位置 `B1>B2>B3`；上涨确定性 `B3>B2>B1`；赔率 `B2>B3>B1`。

**B2 核心指标（原文原话）**：B1 之后 / 涨幅大于 4% / 比前一交易日放量 / J<55 / 无上影线最好。

### B2 的主要用途是**验证信号**，不只是多一个入场点

把 B1 样本按"N 日内是否出现 B2"分成两组对比，直接回答"什么样的 B1 会启动"——这比继续找
排序因子更接近"提升 B1 成功率"这个目标（也回应结论#15 实证第 1 条：瓶颈在召回不在排序）。

### 底部异动（B1.pdf p12「异动选股」+ p19「底部暴力K/击穿对手盘」）

原文条件：① 突然放量、量随价升 ② 异动后上涨趋势波段内「**地量才是地价**」
③ **找异动之后的 B1** ④ 穿越 60 日线的异动，幅度越大后续空间越大；
底部暴力K 另加：巨量点火 / 后 4 天量不能低于巨量的一半 / 9 个月新高 /
新闻媒介煽风点火（无法编码，跳过）。均线体系：30 日观察 / 60 日建仓 / 120 日必守。

第③条正是本项目 B1 选股的天然前置——**异动确认"主力进过场"，B1 给出回调买点**。这比
`b1_dual_factor` 里的简版"放量启动段"更完整（多了穿越 60 日线、9 个月新高、点火后量能
维持三个维度）。四条**分开报告**，不先合成分数——原文没给相对重要性。

### 实现与验证状态

> 注：下文因子路径 `screening/b2_surge_factor.py` 已迁至 `src/custos/core/factors/`。

- 因子：`screening/b2_surge_factor.py`（`detect_b2` / `detect_bottom_surge` /
  `detect_surge_then_b1`；`_j_series` 与 `technical_monitor.kdj` 同口径，测试逐值钉住）
- 回测入口：`SCORERS["b2"]`（按命中硬条件数×20 + 无上影线 20 合成，最少假设）；
  `ENTRY_GATES["b2"]`、`["bottom_surge"]`、`["bottom_surge_strict"]`、
  `["surge_then_b1"]`、`["surge_strict_then_b1"]`
- **未接入选股链**，由 `tests/test_b2_surge_factor.py::TestNotWiredIntoScreening` 钉住
- 已确认返回值全为 Python 原生类型（numpy.bool_ 会让 `json.dumps(allow_nan=False)` 报错）

### 待跑回测

```bash
# B2 作为验证信号:B1 后是否出现 B2 → 把 B1 分成"启动了"和"没启动"两组
uv run python src/custos/research/backtest_factors.py --entry-filter j_low --scorer b2 --horizons 5,20,60 --universe-local --universe-sample 1000
uv run python src/custos/research/backtest_factors.py --entry-filter b2                --horizons 5,20,60 --universe-local --universe-sample 1000

# 底部异动:宽/严口径 + 异动后的 B1
uv run python src/custos/research/backtest_factors.py --entry-filter bottom_surge         --universe-local --universe-sample 1000
uv run python src/custos/research/backtest_factors.py --entry-filter bottom_surge_strict  --universe-local --universe-sample 1000
uv run python src/custos/research/backtest_factors.py --entry-filter surge_then_b1        --universe-local --universe-sample 1000
uv run python src/custos/research/backtest_factors.py --entry-filter surge_strict_then_b1 --universe-local --universe-sample 1000
```

---

## H2 第一轮回测判读（1000 只随机抽样 seed=0，2026-08-03）

基准同 H1 第二轮（无条件 422,097 条：H5 49.43% / H20 50.08% / H60 50.14%）；
j_low 条件基准 94,105 条：H5 49.4% / H20 48.7% / H60 52.7%。
数据：`artifacts/logs/backtest_b2_scorer_jlow_1000.json`、`backtest_b2_gate_1000.json`、
`backtest_h2_bottom_surge[_strict]_1000.json`、`backtest_h2_surge_[strict_]then_b1_1000.json`。

### ① B2 作验证信号（j_low 94,105 条按"B2 是否全中"分组）—— **否决**

| 组 | n | H5 | H20 | H60 |
|---|---:|---|---|---|
| B2 全中 | 957 | 48.6% / -0.10% | 51.0% / +2.83% | 43.0% / +4.20% |
| 未全中 | 93,148 | 49.4% / +0.19% | 48.7% / +1.10% | 52.7% / +5.72% |

H20 +2.3pp 不显著（z≈1.3）；**H60 -9.7pp 显著更差（z≈5）**。硬条件命中数 1→4 不单调，
全中组 H5/H60 最差。解释：B2 全中 ≡ 当日涨 4%+放量 ≡ **追高入场**，短线动能尚在、
H60 均值回归吞掉收益。"B2 确认 = 更好的 B1"在当前口径（信号日收盘入场）不成立；
原文"B2 出现后再等回调买"的用法本实现未覆盖（那是"B2 后再等 B1"的两次入场模型）。

### ② B2 作入场门槛（5,773 条）—— **否决**

召回仅 6.1%（94,105→5,773），全体 H5 48.7% / H20 48.5% / H60 46.9%——
**任何 horizon 都不赢无条件基准**，H60 显著更差。不接线。

### ③ 底部异动四 gate —— 三个否决，一个疑似幸存待验证

| gate | n | H5 | H20 | H60 | 判读 |
|---|---:|---|---|---|---|
| bottom_surge 宽 | 107,831 | 46.4% | 45.6% | 45.7% | 异动后 60 天窗口内 gate 持续为真、几乎无选择性，全 horizon 显著低于基准，**否决** |
| bottom_surge_strict | 606 | 46.4% | 52.1% | 51.3% | H20 z≈1.0 不显著；H60 均收 -0.07% vs 基准 +4.82%，**否决** |
| surge_then_b1 宽 | 25,004 | 48.0% | 44.5% | 46.9% | J<13 子集却全 horizon 跑输 j_low 基准，**否决** |
| surge_strict_then_b1 | 133 | 48.9% | **60.2%** | **63.8%** | H20 z≈2.3、H60 z≈3.1（对无条件基准），首轮疑似幸存；**终审已否决**（跨 seed H20 翻转 38.8%、2022-2024 零信号、133 条全集中 2025-2026） |

`surge_strict_then_b1`（巨量点火+量维持+穿60日线+9月新高 → 等 J<13 回调买）是唯一
没被首轮否掉的方向，H60 z≈3.1 过了 6 组多重比较的 Bonferroni 边界（≈2.75）。但：
n=133、召回 0.14%（全市场日均 ~0.13 个信号），单 seed 单区间，**必须跨 seed/跨区间
交叉验证后才准采信**；即使为真，定位也是极低频精选信号，不是召回口径。

### 下一轮验证清单

```bash
# surge_strict_then_b1 稳健性:换 seed、换区间
uv run python src/custos/research/backtest_factors.py --entry-filter surge_strict_then_b1 --universe-local --universe-sample 1000 --seed 1 --horizons 5,10,20,60
uv run python src/custos/research/backtest_factors.py --entry-filter surge_strict_then_b1 --universe-local --universe-sample 1000 --start 2022-01-01 --end 2024-12-31 --horizons 5,10,20,60
# 宽口径 bottom_surge 的 gate 语义修正(异动后 60 天持续为真 → 只在异动当日/异动后首次 J<13 触发)后再议
# ⇒ 已实现（2026-08-12，#13，owner 裁决「异动后的 J<13 触发，不一定是首次，可以多关注几次」）：
#   新 gate bottom_surge_j13 / bottom_surge_strict_j13 = 异动后 60 天窗口内每次 J<13 都触发；
#   旧 gate 原样保留作对照。**待回测**（目标机）：
uv run python src/custos/research/backtest_factors.py --entry-filter bottom_surge_j13        --universe-local --universe-sample 1000
uv run python src/custos/research/backtest_factors.py --entry-filter bottom_surge_strict_j13 --universe-local --universe-sample 1000
```

### #13 修正口径首轮回测（2026-08-13，s1000 vipdoc 宇宙）

| gate | n | 判读 |
|---|---:|---|
| `bottom_surge_j13`（宽） | 25,123 | 选择性仍弱：无 A/B 档（全 C），胜率 51.8%/均收 +1.06%——修正后量级与旧宽口径同阶，**不采信** |
| `bottom_surge_strict_j13`（严） | **110** | **方向上有戏**：H10 胜率 61.9%、均收 +1.64%、H20 +2.43%、median +1.97%；样本小，**复核后才采信** |

复核待跑（目标机，同 surge_strict_then_b1 的稳健性套路）：

```bash
uv run python src/custos/research/backtest_factors.py --entry-filter bottom_surge_strict_j13 --universe-local --universe-sample 1000 --seed 1
uv run python src/custos/research/backtest_factors.py --entry-filter bottom_surge_strict_j13 --universe-local --universe-sample 1000 --start 2022-01-01 --end 2024-12-31
```

### 已明确不做

- **三线/四线归零买（含"白线下20买"）**：owner 裁定它是**跟随策略，不属于 B1**，本项目不实现。
  原文公式留档备查（B1.pdf p04）：`短期=100*(C-LLV(L,N1))/(HHV(C,N1)-LLV(L,N1))` 等四线，
  `白线下20买 = 短期<=20 AND 长期>=60`。
- 新闻媒介煽风点火（底部暴力K 第 4 条）：无数据源，无法编码。

### B1.pdf 其余内容（已读，暂不实现）

SF 战法六步（择时/选股/等B1不追/止损/止盈/收队）；「**主题等日线 B1，主线等周 B1**」与
「**周线选完日线买**」——这是日周共振的**分工**解读（周线定方向、日线定时点），比"两周期
同时 J<13"更贴近原意，可解释 H1c 观察到的张力；交易节奏（呼吸/N型/J到负）；季节性（2、9、
10、11 月最好）；持仓检查手册（特级马/一等马/低等马/草泥马）；大盘回调期换股四依据；
仓位管理（分仓 vs 重仓）；资金规模-盈利目标表；松紧手；关键K（趋势反转六型/走势衰竭两型）。
`补坑策略` 一页在原 PDF 中为空页（仅标题）。

---

### surge_strict_then_b1：三项终审全挂 ⇒ **正式否决（regime 过拟合）**

- 跨 seed：seed=1 仅 74 条，**H20 方向翻转**（38.8% vs seed=0 的 60.2%）；
- 跨区间：2022-2024 **零信号**（`--allow-empty` 确认非依赖故障）；
- 133 条信号全部集中 2025-2026（2025 年 90 条、2026 年 43 条，最密 2025-11 单月 37 条）。

首轮的 H20 60.2% / H60 63.8% 是单一行情区间的产物，不可采信。
