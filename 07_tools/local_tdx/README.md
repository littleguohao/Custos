# local_tdx 工具层

本目录用于封装本地通达信数据能力。

原则：

- 不直接修改 `C:\new_tdx64\PYPlugins\user` 下原始脚本。
- 将原始 TQ 能力封装成 strategy_team 可复用的数据层。
- 所有输出统一写入 `strategy_team/01_data/`。

当前可复用来源：

- `C:\new_tdx64\PYPlugins\user\tqcenter.py`
- `C:\new_tdx64\PYPlugins\user\tdxdata_download.py`
- `C:\new_tdx64\PYPlugins\user\workflow_B1.py`
- `C:\new_tdx64\vipdoc`

后续目标：

1. 封装 K 线下载。
2. 封装快照获取。
3. 封装股票列表和板块列表。
4. 封装 B1 选股候选来源。
5. 输出给 market_timing、theme_tracker、portfolio_review、stock_pool、buy_strategy。
