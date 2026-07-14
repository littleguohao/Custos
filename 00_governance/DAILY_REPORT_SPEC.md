# 统一每日投研简报规范

## 目标

将 market_timing、theme_tracker、portfolio_review、stock_pool、buy_strategy、position_decision、risk_control、chief_decision 的结果收敛为一份面向决策的日报，避免各 Agent 重复陈述或相互冲突。

## 固定输出结构

1. 今日核心结论
2. 关键事件与影响
3. 主线与机会方向
4. 风险提示
5. 重点跟踪清单（指数/情绪、持仓、观察池）
6. 当日行动建议
7. 数据时效与声明

模板：`03_daily_plans/DAILY_REPORT_TEMPLATE.md`

## 数据优先级

1. 盘中可靠实时数据
2. 当日收盘结构化数据
3. 最近交易日结构化数据（必须标注数据基准）
4. Agent Markdown 报告
5. 人工输入

同一字段出现冲突时，按以上优先级选择；未确认口径不得用于提高仓位或放宽风控。

## 决策链路

```text
数据采集
  → market_timing（市场许可）
  → theme_tracker（板块过滤）
  → portfolio_review（持仓诊断）
  → stock_pool（候选分层）
  → buy_strategy（价格与失效条件）
  → position_decision（仓位路径）
  → risk_control（否决权）
  → chief_decision（唯一执行口径）
  → daily_report（面向用户的统一表达）
```

日报不得绕过中间层直接把“板块强”翻译成“可以买”。

## TDX 技能接入原则

- 一个阶段只能有一个主责技能；其他技能作为数据增强或专题插件。
- 专题技能只有在命中持仓、观察池、重大事件或用户明确关注时调用。
- 同类查询优先批量化，避免对每只股票重复调用相同接口。
- 风险相关技能可以否决机会结论，机会技能不能覆盖 risk_control 的否决。
- 所有技能输出先结构化，再由日报生成器统一表达，禁止直接拼接长文本。

## 生成命令

```powershell
uv run python strategy_team/07_tools/daily_report.py --date YYYY-MM-DD
```

若报告日和数据基准日不同：

```powershell
uv run python strategy_team/07_tools/daily_report.py --date YYYY-MM-DD --data-date YYYY-MM-DD --session 盘前
```

## 验证门槛

- UTF-8 可读，无乱码和替换字符。
- 恰好包含 7 个一级业务章节。
- 模板变量全部解析。
- 仓位百分比口径正确。
- D 池候选不进入重点观察清单。
- 缺失数据明确标注，不编造。
- 日报生成失败时，`daily_pipeline` 应视为必需阶段失败，不继续宣称日报已完成。
