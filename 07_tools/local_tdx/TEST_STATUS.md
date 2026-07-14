# local_tdx 测试状态

日期：2026-07-09

## 已通过

### 1. K线

命令：

```bash
uv run python strategy_team/07_tools/local_tdx/local_tdx_data.py kline --code 600150.SH --count 5
```

结果：成功返回 600150.SH 最近 K 线。

### 2. 快照

命令：

```bash
uv run python strategy_team/07_tools/local_tdx/local_tdx_data.py snapshot --codes 000001.SH,399006.SZ,600150.SH
```

结果：成功返回指数和个股快照。

### 3. 股票列表

命令：

```bash
uv run python strategy_team/07_tools/local_tdx/local_tdx_data.py stock-list --pool-type 5
```

结果：返回 5534 个股票代码。

### 4. 板块列表

命令：

```bash
uv run python strategy_team/07_tools/local_tdx/local_tdx_data.py sector-list
```

结果：返回 588 个板块代码。

### 5. 板块成分股

命令：

```bash
uv run python strategy_team/07_tools/local_tdx/local_tdx_data.py sector-members --sector 880081.SH
```

结果：成功返回成分股。

## 重要发现

TQ 不适合并发初始化。并行测试时出现：

```text
TQ数据接口初始化失败或已有同名策略运行
```

处理：

- `local_tdx_data.py` 已将 TQ session path 改为带 PID 的唯一标识。
- 后续 daily_pipeline 仍应串行调用 TQ。
- 需要并发时，优先并发读取本地 vipdoc 文件，而不是并发 TQ。

## 已生成测试文件

- `01_data/local_tdx/test_600150_kline.csv`
- `01_data/local_tdx/test_snapshot.json`
- `01_data/local_tdx/test_stock_list.json`
- `01_data/local_tdx/test_sector_list.json`
- `01_data/local_tdx/test_sector_880081_members.json`
