# R31 · QN 因子批（骑牛登山体系 12 gate）入场加值验证（预注册）

> **家族**：选股方向（外部体系因子第一批——`governance/strategy/qn/` 融合后的
> 工程化验证）　|
> **证据等级**：L3−（一批 12 个 gate 同评（v0.195 扩批 +4），多重比较显式标注：单个 gate 过线只记
> 「线索」，须第二轮扩窗复核才可升级，同 R28 对 RV 的处理）　|
> **状态**：📋 **预注册（2026-09-08，跑数前）**——gate 清单/判据/样本条款本页
> 写死，跑数后如实回填　|
> **依赖**：因子实现 `core/factors/qn_*.py`（untested/none/debug，合成钉测
> `tests/test_qn_factors.py`）；判据纪律：R12（预注册）；口径：R11（量级不作数）
> R14（幸存者宇宙）；窗口/宇宙/出场轴：同 R27（双窗 s3000、统一出场轴、
> `--count 1500` 防尾部截断）
> 索引与主图见 [`README.md`](README.md)。

## 主题

骑牛登山体系（199 视频萃取的经验规则库，见 `governance/strategy/qn/README.md`）
第一批 8 个 + 第二批 4 个可确定性计算的规则已因子化。本轮回答一个问题：**这 12 个入场信号
在统一出场轴下，相对无条件基准有没有入场加值**。这是外部体系规则第一次
进入我们的证据流程——它们目前是「转译假设」，不是「可用信号」。

## 目标

对 12 个 qn ENTRY gate 逐一判定：过线线索 / 否决 / 样本不足。
**不追求胜率，追求盈亏比**（核心原则第 0 条）——判据里盈亏比与胜率并列。

## 结论

📋 **预注册（2026-09-08，跑数前）：结论未定且不许预设。**
每个 gate 三种合法结局：①双窗加值过线 ⇒ 记「线索」，开第二轮扩窗复核；
②任一项判据失线 ⇒ 否决（因子状态转 needs_work 并引本页为 evidence）；
③触发笔数不足 ⇒ 记「样本不足」（不判死刑，保留留证）。
无论何种结局如实回填本段并更新头部状态。

---

## 证据与过程

### 验证对象（12 个 gate，转译口径见各自 docstring）

| gate | 因子 | 源规则（qn 文档） | 入场转译 |
|---|---|---|---|
| `qn_ma25_state` | MA25 多空分界 | qn/01 §四、qn/08 | 线上缩量阴线 |
| `qn_volume_surge_cut` | 倍量切起爆K线 | qn/08 | 阳线倍量×2 上穿 MA5/MA10 |
| `qn_three_red` | 三线红 | qn/01 §二、qn/08 | 日/周/月 MACD 柱全红当日 |
| `qn_macd_bar_shift` | 买小绿 | qn/01 §八 | 绿柱连缩 + 收盘不破前低 |
| `qn_kdj_neg_day` | J 负值计数 | qn/05 | 负值第 3/5 天或 KD20 金叉 |
| `qn_adx_extreme` | ADX≥60 极端位 | qn/04 §三 | 极端位 + MACD 底背离 |
| `qn_box_target` | 1.3 系数箱体 | qn/02 §一 | ⚠️ 研究约定：站上半格×1.15 且未进目标区（非源规则直接买点） |
| `qn_ma144_launch` | 日线翻倍四要素 | qn/07 §二 | 四要素全中当日 |

**扩批（2026-09-08 v0.195，跑数前同批写死）**：第二批 4 个 gate 加入本页验证，
判据 C1~C4 与窗口/宇宙/出场轴完全相同，不另开研究单元：

| gate | 因子 | 源规则（qn 文档） | 入场转译 |
|---|---|---|---|
| `qn_ma_converge` | 均线收拢发散 | qn/01 §五 | 四线粘合后首次放量向上发散当日 |
| `qn_bullish_engulf` | 阳包阴/单阳包 | qn/01 §一 | 实体包覆+上穿 MA5/MA10+量略大 |
| `qn_weekly180_setup` | 180 周线大悬空 | qn/07 §一 | 四要素全中（低频，预期大面积 0 命中，C1 样本条款重点适用） |
| `qn_shrink_limit_up` | 缩量涨停板 | qn/03 §三 | 涨停+缩量 0.5~0.7×+前序放量阴+贴均线 |

### 窗口 / 宇宙 / 出场轴

- 双窗 s3000（主窗 + 跨窗，窗口日期与 R27 同口径，跑数命令按 R27 附录复刻）
- 统一出场轴三档：`base_low` / `pct12` / `pct12+分批止盈+BBI2`（同 R27）
- 基准：同宇宙同窗同出场轴的无条件基准（`ENTRY_GATES["none"]` 口径）

### 判据（每个 gate × 每个出场档 × 双窗，跑数前写死）

- **R31-C1（样本）**：单窗触发笔数 ≥ **100 笔**；不足 ⇒ 结局③「样本不足」
  （低频记线索留证，不判死刑——同 R27 对 R★ 的处理）
- **R31-C2（加值）**：H20 相对无条件基准 **> +3pp**（同 R25/R26 判据线），
  且**双窗同向为正**（一正一负 = 失线）
- **R31-C3（盈亏比）**：H20 盈亏比不劣于基准的 **80%**（胜率与盈亏比并列，
  防「高胜率低盈亏比」假过线——核心原则第 0 条）
- **R31-C4（级别）**：单 gate 过 C1~C3 也只记「线索」（12 个一批的多重比较税），
  升级须第二轮扩窗复核同向

### 工程与产物

- gate 实现：`research/backtest_factors.py` QN 区段（黑盒 detector 包装，
  接受并忽略 precomputed，不进 `_SLICE_FREE_GATES`——初版求正确不求快；
  跑批耗时长属预期，不为此改判定语义）
- 已知口径限制：`qn_ma144_launch` gate 路径无 code，「涨停」腿按主板 10%
  （其余三形式不受影响；gate docstring 已注）
- 产物：`artifacts/logs/`（跑数后登记路径）+ 本页结论回填

## 回填区（跑数后填）

（空——跑数后按「结论」段的三种结局如实回填，并同步因子注册表 status。）

---

## 附录 · 生产机跑数手册（2026-09-08 随预注册写死）

**口径与 R27 逐格一致**：strategy_grid 驱动 backtest_factors `--trade-sim
--portfolio`，每格自动 `--amv-long-only`（v0.93 基底钉），scorer=baseline
（`--top-n 0` 无区分度），`--count 2000`、`--timeout 10800`、`-j 4`、
出场轴 `governance/research/exit_grid_rsi_family.json`
（base_low / pct12 / pct12_so5_bbi2）。宇宙与 R27 同：s3000 钉死
（生产机上沿用 R27 同批 codes-file；若不可得退 `--sample 3000`，
并在回填时记录宇宙 digest 差异——R27 已实测 digest 漂移对读数的影响）。

**第 0 步 · 小规模冒烟（估算单格耗时，qn gate 全走慢速切片路径）**：

```bash
uv run python -m custos.research.strategy_grid \
  --scorers baseline \
  --gates qn_ma25_state,qn_volume_surge_cut,qn_three_red,qn_macd_bar_shift,qn_kdj_neg_day,qn_adx_extreme,qn_box_target,qn_ma144_launch,qn_ma_converge,qn_bullish_engulf,qn_weekly180_setup,qn_shrink_limit_up \
  --exit-grid governance/research/exit_grid_rsi_family.json \
  --start 2024-08-01 --end 2024-12-31 --sample 300 \
  --count 2000 --top-n 0 --timeout 10800 -j 4 --tag r31_smoke
```

⚠️ 性能预期（v0.196 起已提速）：12 个 qn gate 的因子 detect 带 `_arr`
预计算通道，必需键齐备时走无切片快速路径（`_SLICE_FREE_GATES` 已登记，
等价性钉测逐 bar 钉住两路一致）；**缺键（如无 amount 列）自动回退慢路径**。
冒烟若仍超时，优先检查是否发生回退（GATE_STATS 的 error/dep_missing），
不得为提速改判定语义。

**第 1 步 · 跨窗 2022-2024（s3000）**：

```bash
uv run python -m custos.research.strategy_grid \
  --scorers baseline \
  --gates qn_ma25_state,qn_volume_surge_cut,qn_three_red,qn_macd_bar_shift,qn_kdj_neg_day,qn_adx_extreme,qn_box_target,qn_ma144_launch,qn_ma_converge,qn_bullish_engulf,qn_weekly180_setup,qn_shrink_limit_up \
  --exit-grid governance/research/exit_grid_rsi_family.json \
  --start 2022-01-01 --end 2024-07-31 --sample 3000 \
  --count 2000 --top-n 0 --timeout 10800 -j 4 --tag r31_cw
```

**第 2 步 · 主窗 2024-08~2026-09（s3000）**：同上，窗口改
`--start 2024-08-01 --end 2026-09-04 --tag r31_main`。

**第 3 步 · 基准对照**：同两窗跑 `--gates none`（同出场轴同宇宙）；
若同参数基线格已落盘，cell_signature 复用会自动跳过，直接引用即可。

**产物落点**：`artifacts/logs/strategy_grid/_ranked__r31_{smoke,cw,main}.json`
+ 同名 .md；回填时按 C1~C4 逐 gate 判定并更新因子注册表 status。
