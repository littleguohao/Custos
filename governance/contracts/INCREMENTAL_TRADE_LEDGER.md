# 增量交易主台账

## 唯一主文件

`data/trades/master_trade_ledger.csv`

该文件只允许通过工具追加，禁止手工删除或覆盖历史记录。现有 990 条历史流水已作为初始基线全部保留。

## 每日使用

把当天新增成交填写到：

`data/trades/daily_new_trades_template.csv`

字段：成交日期、成交时间、代码、名称、交易类别、成交数量、成交价格、成交金额、发生金额、费用、备注。

然后运行增量追加工具。工具会：

1. 标准化代码和日期；
2. 生成稳定指纹及唯一 `transaction_id`；
3. 默认跳过已提交过的相同记录；
4. 追加审计日志 `ledger_append_audit.jsonl`；
5. 对买入/卖出更新 `current_positions.json` 的数量和单位成本；
6. 将市值、盈亏、仓位标记为等待收盘重估。

若当天无成交，应记录“无交易确认”，不伪造一条成交。

## 飞书「有交易」→ 投资账本（v0.521）

盘后补数桥收到「有交易」（可与 0AMV 同条回复）后，由 `datasource/tzzb_trades.py`
以本地 OAuth 直接调用投资账本 MCP，不经过模型：

1. `lastSyncTime` 必须是目标日 15:00 或之后，否则不入账并提示先同步 PC；
2. 逐条验收日期、代码、买卖方向、数量、价格、金额及费用，费用按接口原值，不猜补；
3. 接口没有成交时间，标准 CSV 的该字段保持空值；文件落 `artifacts/cache/tzzb/`；
4. 仍通过 `incremental_ledger.py` 唯一入口入库，重复刷新只补多重集合新增部分；
5. 同日已有非投资账本记录时禁止叠加，已有投资账本记录时也禁止再叠加券商表格；
   已有「无交易确认」与新成交冲突时必须人工核对；
6. `data/trades/tzzb_oauth.local.json` 保存本地授权与账户 ID，gitignored，严禁提交。

## 完全相同的真实分笔成交

历史数据中存在少量字段完全相同的分笔记录，因此主台账保留“指纹 + 出现序号”。
去重是**多重集合计数差**：同一指纹在导入文件中的第 k 次出现，只有台账已有数量少于 k
时才新增。因此同价同量的真实分笔会保留，完整快照重复导入为零新增，后续新增同形分笔
只补差额。`--allow-identical` 是绕过全部去重的人工修数开关，会造成重复计账，日常禁用。

## 数据边界

- 数量与成本：由增量成交更新。
- 收盘价格、持仓市值、盈亏和仓位：必须由收盘行情重新计算。
- 0AMV：市场指标，禁止写入交易盈亏字段。
- 修改或撤销历史成交：当前不允许直接操作；后续应使用冲正记录保持审计链。

## 台账 ↔ 持仓一致性（2026-08-06 补）

`incremental_ledger._commit` 刻意选择「**ledger 先落、positions 后落**」：
崩在两次 `os.replace` 之间会留下「已记录成交但持仓未更新」——**可检测、可修复**；
反过来（持仓已加、台账没记）会让下次导入把同一批成交再算一遍（持仓静默翻倍，真实发生过）。

⚠️ **但此前没有任何常规检查在「检测」它** —— 唯一的对账逻辑埋在
`research/backtest_0amv_bear_regime.py`（自称「不触碰任何管线」的研究脚本）里，
而 `runtime_guards` 读台账只判**新鲜度**、不校验「持仓 == 台账回放」。

现由 `src/custos/core/trades/reconcile_positions.py` 补上，并接入 17:00 链（**非阻断**）：

```bash
uv run python src/custos/core/trades/reconcile_positions.py --date 2026-08-06
# 台账非从零开始时须给期初持仓：--baseline path/to/initial_positions.json
# 观察若干交易日后可加 --strict（数量不一致 exit 1）
```

判据分三档：`ok` / `cost_only_diff`（多为浮点尾差或期初基线缺失）/
**`mismatch`（数量不一致 = 台账与持仓已脱节，硬信号）**，另有 `replay_failed`。
数量是整数股、float 对 2^53 内整数精确 ⇒ **数量不设容差**，有差就是真有差。

回放**复用 `incremental_ledger.compute_positions`**，不另写买卖应用逻辑 ——
否则「对账」只是在比两个都可能错的实现。
