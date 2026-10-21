# R39 各档交易分布画像（r39_dist）

由 `result_digest --backfill` 写入，数字只来自产物；**描述性副读数，不是判据**，判读写在块外。

### 回填区

<!-- digest:results/R39/r39_v2_pre_dist.json -->
**跑数摘要** `r39_v2_pre_dist`（kind=r39_dist，产物 `_factor_exit__r39_v2_pre_dist.json` sha256 `3b8ed28de9efa187`，digest v1；数字只来自产物，勿手改）

| 字段 | 值 |
|---|---|
| `provenance.cmdline` | "--tag r39_v2_pre_dist --trade-dist --mining-start 2000-01-01 --mining-end 2016-12-31 --judgment-start 2022-01-01 --judgment-end 2026-09-04" |
| `provenance.data_last_date` | "2026-09-04" |
| `provenance.git_dirty` | false |
| `provenance.git_sha` | "ce48de678f" |
| `provenance.n_codes` | 5576 |
| `provenance.universe_rule` | "ashare_full_asof" |
| `provenance.universe_sha256` | "f0692547ad66f1b2" |
| `trade_dist.top_judgment.avg_loss` | -0.056582 |
| `trade_dist.top_judgment.avg_win` | 0.091974 |
| `trade_dist.top_judgment.big_win_share` | {"ge_2x_avg_loss": 0.1039, "ge_3x_avg_loss": 0.068, "ge_5x_avg_loss": 0.0306} |
| `trade_dist.top_judgment.buckets` | "见子表 trade_dist.top_judgment.buckets（9 行）" |
| `trade_dist.top_judgment.by_bucket` | {"None": {"avg_loss": -0.056582, "avg_win": 0.091974, "big_win_share": {"ge_2x_avg_loss": 0.1039, "ge_3x_avg_loss": 0.068, "ge_5x_avg_loss": 0.0306}, "by_reason": "见子表 trade_dist.top_judgment.by_bucket.None.by_reason（6 行）", "expectancy": 0.005483, "holding": {"avg_loss": 10.416093, "avg_win": 15.402189}, "margin": 0.036909, "median_loss": -0.0503, "median_win": 0.0418, "n": 413750, "payoff_ratio": 1.62552, "profit_concentration": {"expectancy_ex_top1pct_trades": -0.001571, "top10pct_winners_profit_share": 0.4474, "top1pct_winners_profit_share": 0.1029, "top5pct_winners_profit_share": 0.2939}, "quantiles": {"p1": -0.1337, "p10": -0.0957, "p25": -0.0741, "p5": -0.1059, "p50": -0.0101, "p75": 0.0288, "p90": 0.1178, "p95": 0.213055, "p99": 0.4616}, "win_rate": 0.417786}} |
| `trade_dist.top_judgment.by_reason` | "见子表 trade_dist.top_judgment.by_reason（6 行）" |
| `trade_dist.top_judgment.by_year` | "见子表 trade_dist.top_judgment.by_year（5 行）" |
| `trade_dist.top_judgment.expectancy` | 0.005483 |
| `trade_dist.top_judgment.margin` | 0.036909 |
| `trade_dist.top_judgment.n` | 413750 |
| `trade_dist.top_judgment.paired_vs_ref` | {"delta_ret_mean": 0.0, "delta_ret_quantiles": {"p25": 0.0, "p5": 0.0, "p50": 0.0, "p75": 0.0, "p95": 0.0}, "flip_ref_loss_to_win": 0, "flip_ref_win_to_loss": 0, "n_matched": 413750, "share_better": 0.0, "share_identical": 1.0, "share_worse": 0.0, "stopped_here_not_ref": {"n": 0}, "stopped_ref_not_here": {"n": 0}} |
| `trade_dist.top_judgment.payoff_ratio` | 1.62552 |
| `trade_dist.top_judgment.profit_concentration` | {"expectancy_ex_top1pct_trades": -0.001571, "top10pct_winners_profit_share": 0.4474, "top1pct_winners_profit_share": 0.1029, "top5pct_winners_profit_share": 0.2939} |
| `trade_dist.top_judgment.quantiles` | {"p1": -0.1337, "p10": -0.0957, "p25": -0.0741, "p5": -0.1059, "p50": -0.0101, "p75": 0.0288, "p90": 0.1178, "p95": 0.213055, "p99": 0.4616} |
| `trade_dist.top_judgment.win_rate` | 0.417786 |
| `trade_dist.top_mining.avg_loss` | -0.05713 |
| `trade_dist.top_mining.avg_win` | 0.095957 |
| `trade_dist.top_mining.big_win_share` | {"ge_2x_avg_loss": 0.0924, "ge_3x_avg_loss": 0.0602, "ge_5x_avg_loss": 0.0293} |
| `trade_dist.top_mining.buckets` | "见子表 trade_dist.top_mining.buckets（9 行）" |
| `trade_dist.top_mining.by_bucket` | {"None": {"avg_loss": -0.05713, "avg_win": 0.095957, "big_win_share": {"ge_2x_avg_loss": 0.0924, "ge_3x_avg_loss": 0.0602, "ge_5x_avg_loss": 0.0293}, "by_reason": "见子表 trade_dist.top_mining.by_bucket.None.by_reason（6 行）", "expectancy": 0.008601, "holding": {"avg_loss": 9.114047, "avg_win": 17.059263}, "margin": 0.056183, "median_loss": -0.0458, "median_win": 0.0357, "n": 216737, "payoff_ratio": 1.679637, "profit_concentration": {"expectancy_ex_top1pct_trades": -0.002837, "top10pct_winners_profit_share": 0.5362, "top1pct_winners_profit_share": 0.1859, "top5pct_winners_profit_share": 0.3934}, "quantiles": {"p1": -0.154064, "p10": -0.0987, "p25": -0.0607, "p5": -0.1127, "p50": -0.0079, "p75": 0.0265, "p90": 0.1049, "p95": 0.1998, "p99": 0.5353}, "win_rate": 0.429368}} |
| `trade_dist.top_mining.by_reason` | "见子表 trade_dist.top_mining.by_reason（6 行）" |
| `trade_dist.top_mining.by_year` | "见子表 trade_dist.top_mining.by_year（17 行）" |
| `trade_dist.top_mining.expectancy` | 0.008601 |
| `trade_dist.top_mining.margin` | 0.056183 |
| `trade_dist.top_mining.n` | 216737 |
| `trade_dist.top_mining.paired_vs_ref` | {"delta_ret_mean": 0.0, "delta_ret_quantiles": {"p25": 0.0, "p5": 0.0, "p50": 0.0, "p75": 0.0, "p95": 0.0}, "flip_ref_loss_to_win": 0, "flip_ref_win_to_loss": 0, "n_matched": 216737, "share_better": 0.0, "share_identical": 1.0, "share_worse": 0.0, "stopped_here_not_ref": {"n": 0}, "stopped_ref_not_here": {"n": 0}} |
| `trade_dist.top_mining.payoff_ratio` | 1.679637 |
| `trade_dist.top_mining.profit_concentration` | {"expectancy_ex_top1pct_trades": -0.002837, "top10pct_winners_profit_share": 0.5362, "top1pct_winners_profit_share": 0.1859, "top5pct_winners_profit_share": 0.3934} |
| `trade_dist.top_mining.quantiles` | {"p1": -0.154064, "p10": -0.0987, "p25": -0.0607, "p5": -0.1127, "p50": -0.0079, "p75": 0.0265, "p90": 0.1049, "p95": 0.1998, "p99": 0.5353} |
| `trade_dist.top_mining.win_rate` | 0.429368 |
| `trade_dist.uniform_judgment.avg_loss` | -0.056582 |
| `trade_dist.uniform_judgment.avg_win` | 0.091974 |
| `trade_dist.uniform_judgment.big_win_share` | {"ge_2x_avg_loss": 0.1039, "ge_3x_avg_loss": 0.068, "ge_5x_avg_loss": 0.0306} |
| `trade_dist.uniform_judgment.buckets` | "见子表 trade_dist.uniform_judgment.buckets（9 行）" |
| `trade_dist.uniform_judgment.by_reason` | "见子表 trade_dist.uniform_judgment.by_reason（6 行）" |
| `trade_dist.uniform_judgment.by_year` | "见子表 trade_dist.uniform_judgment.by_year（5 行）" |
| `trade_dist.uniform_judgment.expectancy` | 0.005483 |
| `trade_dist.uniform_judgment.margin` | 0.036909 |
| `trade_dist.uniform_judgment.n` | 413750 |
| `trade_dist.uniform_judgment.payoff_ratio` | 1.62552 |
| `trade_dist.uniform_judgment.profit_concentration` | {"expectancy_ex_top1pct_trades": -0.001571, "top10pct_winners_profit_share": 0.4474, "top1pct_winners_profit_share": 0.1029, "top5pct_winners_profit_share": 0.2939} |
| `trade_dist.uniform_judgment.quantiles` | {"p1": -0.1337, "p10": -0.0957, "p25": -0.0741, "p5": -0.1059, "p50": -0.0101, "p75": 0.0288, "p90": 0.1178, "p95": 0.213055, "p99": 0.4616} |
| `trade_dist.uniform_judgment.win_rate` | 0.417786 |
| `trade_dist.uniform_mining.avg_loss` | -0.05713 |
| `trade_dist.uniform_mining.avg_win` | 0.095957 |
| `trade_dist.uniform_mining.big_win_share` | {"ge_2x_avg_loss": 0.0924, "ge_3x_avg_loss": 0.0602, "ge_5x_avg_loss": 0.0293} |
| `trade_dist.uniform_mining.buckets` | "见子表 trade_dist.uniform_mining.buckets（9 行）" |
| `trade_dist.uniform_mining.by_reason` | "见子表 trade_dist.uniform_mining.by_reason（6 行）" |
| `trade_dist.uniform_mining.by_year` | "见子表 trade_dist.uniform_mining.by_year（17 行）" |
| `trade_dist.uniform_mining.expectancy` | 0.008601 |
| `trade_dist.uniform_mining.margin` | 0.056183 |
| `trade_dist.uniform_mining.n` | 216737 |
| `trade_dist.uniform_mining.payoff_ratio` | 1.679637 |
| `trade_dist.uniform_mining.profit_concentration` | {"expectancy_ex_top1pct_trades": -0.002837, "top10pct_winners_profit_share": 0.5362, "top1pct_winners_profit_share": 0.1859, "top5pct_winners_profit_share": 0.3934} |
| `trade_dist.uniform_mining.quantiles` | {"p1": -0.154064, "p10": -0.0987, "p25": -0.0607, "p5": -0.1127, "p50": -0.0079, "p75": 0.0265, "p90": 0.1049, "p95": 0.1998, "p99": 0.5353} |
| `trade_dist.uniform_mining.win_rate` | 0.429368 |
| `verdict` | "untested" |
| `window_usage` | {"k": null, "note": "判定窗在挖掘段内（非受保护段 2017~2021）——读挖掘段不入台账（v2 口径）", "window": "mining_segment"} |

子表 `trade_dist.top_judgment.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 1606 | 0.0039 |
| "-15~-10%" | 29097 | 0.0703 |
| "-10~-5%" | 90167 | 0.2179 |
| "-5~0%" | 120021 | 0.2901 |
| "0~5%" | 94704 | 0.2289 |
| "5~10%" | 30237 | 0.0731 |
| "10~20%" | 25279 | 0.0611 |
| "20~30%" | 11441 | 0.0277 |
| ">30%" | 11198 | 0.0271 |

子表 `trade_dist.top_judgment.by_bucket.None.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 13.28 | 0.017543 | 277861 | "bbi_exit" | 0.6716 | 0.5133 |
| 9.08 | -0.096955 | 97544 | "stop" | 0.2358 | 0.0 |
| 15.8 | 0.193469 | 32574 | "trail_stop" | 0.0787 | 0.8373 |
| 18.41 | 0.221693 | 3744 | "trail_stop_delayed" | 0.009 | 0.7863 |
| 5.35 | -0.140332 | 1990 | "stop_delayed" | 0.0048 | 0.001 |
| 21.89 | -0.039181 | 37 | "bbi_exit_delayed" | 0.0001 | 0.2703 |

子表 `trade_dist.top_judgment.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 13.28 | 0.017543 | 277861 | "bbi_exit" | 0.6716 | 0.5133 |
| 9.08 | -0.096955 | 97544 | "stop" | 0.2358 | 0.0 |
| 15.8 | 0.193469 | 32574 | "trail_stop" | 0.0787 | 0.8373 |
| 18.41 | 0.221693 | 3744 | "trail_stop_delayed" | 0.009 | 0.7863 |
| 5.35 | -0.140332 | 1990 | "stop_delayed" | 0.0048 | 0.001 |
| 21.89 | -0.039181 | 37 | "bbi_exit_delayed" | 0.0001 | 0.2703 |

子表 `trade_dist.top_judgment.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2022" | 0.018019 | 0.139165 | 75157 | 1.670463 | 0.513631 |
| "2023" | -0.012536 | -0.119696 | 151114 | 1.101789 | 0.356089 |
| "2024" | 0.057402 | 0.246517 | 74050 | 2.287402 | 0.550709 |
| "2025" | 0.028626 | 0.224339 | 27534 | 2.251517 | 0.531888 |
| "2026" | -0.025963 | -0.179411 | 85895 | 1.124457 | 0.291298 |

子表 `trade_dist.top_mining.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 2490 | 0.0115 |
| "-15~-10%" | 17767 | 0.082 |
| "-10~-5%" | 38991 | 0.1799 |
| "-5~0%" | 64429 | 0.2973 |
| "0~5%" | 55038 | 0.2539 |
| "5~10%" | 15355 | 0.0708 |
| "10~20%" | 11851 | 0.0547 |
| "20~30%" | 4978 | 0.023 |
| ">30%" | 5838 | 0.0269 |

子表 `trade_dist.top_mining.by_bucket.None.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 13.36 | 0.024021 | 156412 | "bbi_exit" | 0.7217 | 0.5375 |
| 7.8 | -0.099515 | 43732 | "stop" | 0.2018 | 0.0 |
| 21.4 | 0.265353 | 9780 | "trail_stop" | 0.0451 | 0.7693 |
| 4.92 | -0.147082 | 4528 | "stop_delayed" | 0.0209 | 0.0004 |
| 23.37 | 0.242023 | 2208 | "trail_stop_delayed" | 0.0102 | 0.6544 |
| 10.06 | -0.058674 | 77 | "bbi_exit_delayed" | 0.0004 | 0.1429 |

子表 `trade_dist.top_mining.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 13.36 | 0.024021 | 156412 | "bbi_exit" | 0.7217 | 0.5375 |
| 7.8 | -0.099515 | 43732 | "stop" | 0.2018 | 0.0 |
| 21.4 | 0.265353 | 9780 | "trail_stop" | 0.0451 | 0.7693 |
| 4.92 | -0.147082 | 4528 | "stop_delayed" | 0.0209 | 0.0004 |
| 23.37 | 0.242023 | 2208 | "trail_stop_delayed" | 0.0102 | 0.6544 |
| 10.06 | -0.058674 | 77 | "bbi_exit_delayed" | 0.0004 | 0.1429 |

子表 `trade_dist.top_mining.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2000" | 0.004533 | 0.063773 | 5595 | 1.456974 | 0.470777 |
| "2001" | 0.000395 | 0.00434 | 5642 | 1.037917 | 0.495037 |
| "2002" | -0.034846 | -0.408089 | 7033 | 0.701632 | 0.179582 |
| "2003" | 0.000367 | 0.002589 | 16362 | 1.275459 | 0.442061 |
| "2004" | -0.016979 | -0.122535 | 6085 | 1.141378 | 0.344454 |
| "2005" | -0.031107 | -0.18998 | 6675 | 1.106929 | 0.284644 |
| "2006" | 0.082925 | 0.326912 | 16602 | 4.049863 | 0.524937 |
| "2007" | 0.16615 | 0.607182 | 7260 | 5.594858 | 0.758815 |
| "2008" | -0.023836 | -0.110095 | 7219 | 1.286103 | 0.327331 |
| "2009" | 0.039687 | 0.305111 | 3559 | 2.25585 | 0.612251 |
| "2010" | 0.005272 | 0.040964 | 11224 | 1.429847 | 0.452512 |
| "2011" | -0.022874 | -0.25013 | 13128 | 0.743727 | 0.323355 |
| "2012" | -0.014554 | -0.122451 | 18133 | 1.00245 | 0.376937 |
| "2013" | -0.006976 | -0.051963 | 13488 | 1.229834 | 0.396501 |
| "2014" | 0.022819 | 0.169917 | 24537 | 1.771681 | 0.530709 |
| "2015" | 0.021481 | 0.064297 | 6782 | 1.865302 | 0.4133 |
| "2016" | -0.005738 | -0.052516 | 47413 | 1.20188 | 0.401641 |

子表 `trade_dist.uniform_judgment.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 1606 | 0.0039 |
| "-15~-10%" | 29097 | 0.0703 |
| "-10~-5%" | 90167 | 0.2179 |
| "-5~0%" | 120021 | 0.2901 |
| "0~5%" | 94704 | 0.2289 |
| "5~10%" | 30237 | 0.0731 |
| "10~20%" | 25279 | 0.0611 |
| "20~30%" | 11441 | 0.0277 |
| ">30%" | 11198 | 0.0271 |

子表 `trade_dist.uniform_judgment.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 13.28 | 0.017543 | 277861 | "bbi_exit" | 0.6716 | 0.5133 |
| 9.08 | -0.096955 | 97544 | "stop" | 0.2358 | 0.0 |
| 15.8 | 0.193469 | 32574 | "trail_stop" | 0.0787 | 0.8373 |
| 18.41 | 0.221693 | 3744 | "trail_stop_delayed" | 0.009 | 0.7863 |
| 5.35 | -0.140332 | 1990 | "stop_delayed" | 0.0048 | 0.001 |
| 21.89 | -0.039181 | 37 | "bbi_exit_delayed" | 0.0001 | 0.2703 |

子表 `trade_dist.uniform_judgment.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2022" | 0.018019 | 0.139165 | 75157 | 1.670463 | 0.513631 |
| "2023" | -0.012536 | -0.119696 | 151114 | 1.101789 | 0.356089 |
| "2024" | 0.057402 | 0.246517 | 74050 | 2.287402 | 0.550709 |
| "2025" | 0.028626 | 0.224339 | 27534 | 2.251517 | 0.531888 |
| "2026" | -0.025963 | -0.179411 | 85895 | 1.124457 | 0.291298 |

子表 `trade_dist.uniform_mining.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 2490 | 0.0115 |
| "-15~-10%" | 17767 | 0.082 |
| "-10~-5%" | 38991 | 0.1799 |
| "-5~0%" | 64429 | 0.2973 |
| "0~5%" | 55038 | 0.2539 |
| "5~10%" | 15355 | 0.0708 |
| "10~20%" | 11851 | 0.0547 |
| "20~30%" | 4978 | 0.023 |
| ">30%" | 5838 | 0.0269 |

子表 `trade_dist.uniform_mining.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 13.36 | 0.024021 | 156412 | "bbi_exit" | 0.7217 | 0.5375 |
| 7.8 | -0.099515 | 43732 | "stop" | 0.2018 | 0.0 |
| 21.4 | 0.265353 | 9780 | "trail_stop" | 0.0451 | 0.7693 |
| 4.92 | -0.147082 | 4528 | "stop_delayed" | 0.0209 | 0.0004 |
| 23.37 | 0.242023 | 2208 | "trail_stop_delayed" | 0.0102 | 0.6544 |
| 10.06 | -0.058674 | 77 | "bbi_exit_delayed" | 0.0004 | 0.1429 |

子表 `trade_dist.uniform_mining.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2000" | 0.004533 | 0.063773 | 5595 | 1.456974 | 0.470777 |
| "2001" | 0.000395 | 0.00434 | 5642 | 1.037917 | 0.495037 |
| "2002" | -0.034846 | -0.408089 | 7033 | 0.701632 | 0.179582 |
| "2003" | 0.000367 | 0.002589 | 16362 | 1.275459 | 0.442061 |
| "2004" | -0.016979 | -0.122535 | 6085 | 1.141378 | 0.344454 |
| "2005" | -0.031107 | -0.18998 | 6675 | 1.106929 | 0.284644 |
| "2006" | 0.082925 | 0.326912 | 16602 | 4.049863 | 0.524937 |
| "2007" | 0.16615 | 0.607182 | 7260 | 5.594858 | 0.758815 |
| "2008" | -0.023836 | -0.110095 | 7219 | 1.286103 | 0.327331 |
| "2009" | 0.039687 | 0.305111 | 3559 | 2.25585 | 0.612251 |
| "2010" | 0.005272 | 0.040964 | 11224 | 1.429847 | 0.452512 |
| "2011" | -0.022874 | -0.25013 | 13128 | 0.743727 | 0.323355 |
| "2012" | -0.014554 | -0.122451 | 18133 | 1.00245 | 0.376937 |
| "2013" | -0.006976 | -0.051963 | 13488 | 1.229834 | 0.396501 |
| "2014" | 0.022819 | 0.169917 | 24537 | 1.771681 | 0.530709 |
| "2015" | 0.021481 | 0.064297 | 6782 | 1.865302 | 0.4133 |
| "2016" | -0.005738 | -0.052516 | 47413 | 1.20188 | 0.401641 |
<!-- /digest -->

