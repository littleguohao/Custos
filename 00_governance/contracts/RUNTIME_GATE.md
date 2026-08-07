# 运行门控（RUNTIME_GATE）

> 2026-08-07 从根目录 `README.md` 抽出。README 里那 30 行大半是**事故档案与设计理由**
> （07-31 加权修复的推导、07-30 事故、256 组合实测），属于**参考资料**而不是入门材料 ——
> 放在 README 里既让入门变长，也让这些结论不容易被当作契约来查。
>
> **它为什么算契约**：门控的**退出码被 cron 直接消费**（3/4/5 穿透 `daily_pipeline`），
> 且 `--require-*` 开关真的会中断链路。改这里的任何判定都要先读下面的事故记录。

## 门控检查项

`runtime_gate.py` 在每次报告生成前检查：
- 交易日历
- 持仓新鲜度
- 技术数据新鲜度
- 市场质量（0AMV、宽度、成交额；**按 `as_of` 判定新鲜度**，当日文件里装 T-1 数据同样记 `stale`）
  - **评分按关键性加权**（2026-07-31）：`0AMV 35 / 宽度 20 / 成交额 20 / 情绪 15 / 海外 10`。此前是无权重算术平均（5 项各 0.2），导致「0AMV 全缺 + 其余齐全」= 4/5 = **0.8 恰好判 pass 并授予加仓权**。0AMV 决定 regime，缺它等于不知道方向，故 **0AMV 非 confirmed/auto 时一律不得 pass**（只降为 `degraded`，不新增阻断）。
  - **`blocked` 改为显式覆盖率规则**：四个核心块（0AMV/宽度/情绪/成交额）**全部** stale/missing 才算「大面积缺数」。原先是 `score < 0.4` 这个魔数阈值——加权后它会凭空多出 24 种阻断场景（256 组合实测），而 blocked 经 `--require-quality`/`--require-gate` 真正中断链路，重演 07-30 事故。现规则下：新增 blocked = 0，6 个原 pass 收紧为 degraded，0 个放松为 pass。
  - **`limitations` 字段**：degraded 时列出具体受限项（哪个块 stale、as_of 是哪天），报告可据此归因，不再只有一个分数。
- 持仓行情是否当日
- **加仓授权（`position_gate.allow_position_increase`）**：需 ①持仓基线 confirmed + 全持仓当日行情 ②当日技术指标 ③`market_quality=pass` ④0AMV 新鲜 ⑤regime 属白名单 `{做多, 中性}`。此前写作 `regime != "空头"`，0AMV 缺失时 `effective_state` 是 None → 空串 → `"" != "空头"` 为真 ⇒ **regime 未知却授予加仓权**（2026-07-31 修）。regime 文本经 `normalize_regime` 归一，覆盖三套并行词表：`effective_state`（做多/中性/空头）、`amv_zone`（做多触发/空头触发/阈值内，merge 会用它兜底填 effective_state）、README 曾用的「多头」。判定逻辑抽成纯函数 `position_increase_decision`，可单测。

门控必须能**真正阻断**，而非只写 JSON。退出码：

| 退出码 | 触发条件 | 启用开关 |
|---|---|---|
| 0 | 通过 | — |
| 3 | 非交易日 | `--require-trading-day` |
| 4 | `market_quality=blocked` | `--require-quality` |
| 5 | `position_gate=blocked` | `--require-position-gate` |

- **17:00 盘后链**：门控结论一律落盘并记进 stage note（含 stale 明细），但**默认不阻断**。需要硬失败时给 `daily_pipeline --strict-quality-gate`（仅对 postclose 生效，blocked → exit 4）。⚠️ 2026-07-30 曾让 postclose 默认带 `--require-quality`，同时又收紧了 `as_of` 陈旧判定，两者叠加导致 17:00 盘后复盘直接失败——硬闸须等新的 stale 校准跑过若干交易日、确认 blocked 只在真正大面积缺数时出现，再显式开启。
- **session 期望数据日**：陈旧判定按 session 比对——preclose(08:50/09:05/14:45)期望 T-1（当日 K 线尚不存在，as_of=T-1 不算陈旧），postclose(17:00/1800)期望当日。`daily_pipeline` 已按 `--session-type` 自动传 `--data-session`；评分器(market_timing_scorer)按 section `as_of` 与评分日比对，T-1 数据一律按中性处理不给满分。
- **退出码可消费**：门控退出码会穿透 `daily_pipeline` 进程本身（非交易日 3 / 质量 blocked 4 / 持仓 blocked 5）,cron 可直接按码判定。
- **18:00 选股链只落盘、不阻断**：18:00 是纯粹的选股流程，门控**不得影响选股结果**（不改 bucket / next_step / 分层、不筛候选），只由 `candidate_table` 在备选表里给出独立的「🚦 数据可信度提示」区块。这样选股结果与回测同口径且可复现——门控若改写分层，live 候选就无法与回测对照，「策略本身选出了什么」将不可回溯。
- **09:05 / 14:45 不启用**：0AMV 与市场宽度本就要等收盘，盘中 blocked 属正常；14:45 把门控结论写进 `06_logs/{date}_1445_run_log.json`（`_gate_note`）留痕并降低报告内的权限文案。
- **投递侧**：`feishu_report_publisher.py --require-gate` 在投递前复核门控，非交易日或 `market_quality=blocked` 时拒发并 `exit 4`。
- **08:50 采集**：任一 stage 失败 → run log 写 `status="degraded"` 并列出失败项；09:05 按 **discovery stage 逐项 ok** 决定是否复用（`overseas`/`rss_collect`/`rss_filter` 任一失败即重采），不再只看 `status=="completed"`。

## 相关

- 退出码的传播实现：`07_tools/pipeline_kit.propagate_gate_code`（只放行 3/4/5）
- 判定纯函数：`runtime_guards.position_increase_decision` / `market_quality_gate`（可单测）
- 产物契约：`07_tools/contracts.py` 的 `runtime_gate`（三个 `allow_*` 布尔是权限本身）
