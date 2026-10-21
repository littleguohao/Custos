# R44 各档交易分布画像（r44_dist）

由 `result_digest --backfill` 写入，数字只来自产物；**描述性副读数，不是判据**，判读写在块外。

### 回填区

<!-- digest:results/R44/r44_scan_dist.json -->
**跑数摘要** `r44_scan_dist`（kind=r44_dist，产物 `_plan_width_scan__r44_scan_dist.json` sha256 `6aa2d950a2064a1f`，digest v1；数字只来自产物，勿手改）

| 字段 | 值 |
|---|---|
| `merged.ref.dist.avg_loss` | -0.059144 |
| `merged.ref.dist.avg_win` | 0.098036 |
| `merged.ref.dist.big_win_share` | {"ge_2x_avg_loss": 0.1048, "ge_3x_avg_loss": 0.0688, "ge_5x_avg_loss": 0.0324} |
| `merged.ref.dist.buckets` | "见子表 merged.ref.dist.buckets（9 行）" |
| `merged.ref.dist.by_reason` | "见子表 merged.ref.dist.by_reason（8 行）" |
| `merged.ref.dist.by_year` | "见子表 merged.ref.dist.by_year（22 行）" |
| `merged.ref.dist.expectancy` | 0.009421 |
| `merged.ref.dist.holding` | {"avg_loss": 11.378992, "avg_win": 17.435175} |
| `merged.ref.dist.margin` | 0.059938 |
| `merged.ref.dist.median_loss` | -0.0431 |
| `merged.ref.dist.median_win` | 0.041 |
| `merged.ref.dist.n` | 569861 |
| `merged.ref.dist.payoff_ratio` | 1.657575 |
| `merged.ref.dist.profit_concentration` | {"expectancy_ex_top1pct_trades": 0.000535, "top10pct_winners_profit_share": 0.4791, "top1pct_winners_profit_share": 0.1274, "top5pct_winners_profit_share": 0.3272} |
| `merged.ref.dist.quantiles` | {"p1": -0.1536, "p10": -0.1121, "p25": -0.0522, "p5": -0.1234, "p50": -0.0074, "p75": 0.0312, "p90": 0.1245, "p95": 0.226, "p99": 0.52384} |
| `merged.ref.dist.win_rate` | 0.43622 |
| `merged.x10.dist.avg_loss` | -0.060399 |
| `merged.x10.dist.avg_win` | 0.098565 |
| `merged.x10.dist.big_win_share` | {"ge_2x_avg_loss": 0.1047, "ge_3x_avg_loss": 0.0684, "ge_5x_avg_loss": 0.0318} |
| `merged.x10.dist.buckets` | "见子表 merged.x10.dist.buckets（9 行）" |
| `merged.x10.dist.by_reason` | "见子表 merged.x10.dist.by_reason（8 行）" |
| `merged.x10.dist.by_year` | "见子表 merged.x10.dist.by_year（22 行）" |
| `merged.x10.dist.expectancy` | 0.009645 |
| `merged.x10.dist.holding` | {"avg_loss": 12.036505, "avg_win": 17.591051} |
| `merged.x10.dist.margin` | 0.060673 |
| `merged.x10.dist.median_loss` | -0.0417 |
| `merged.x10.dist.median_win` | 0.0413 |
| `merged.x10.dist.n` | 569944 |
| `merged.x10.dist.paired_vs_ref` | {"delta_ret_mean": 0.000287, "delta_ret_quantiles": {"p25": 0.0, "p5": -0.0084, "p50": 0.0, "p75": 0.0, "p95": 0.0}, "flip_ref_loss_to_win": 2549, "flip_ref_win_to_loss": 0, "n_matched": 569859, "share_better": 0.0208, "share_identical": 0.9215, "share_worse": 0.0576, "stopped_here_not_ref": {"avg_ret_here": -0.171171, "avg_ret_ref": -0.151218, "n": 951, "share_here_better": 0.0}, "stopped_ref_not_here": {"avg_ret_here": -0.04385, "avg_ret_ref": -0.110798, "n": 13999, "share_here_better": 0.8121}} |
| `merged.x10.dist.payoff_ratio` | 1.631883 |
| `merged.x10.dist.profit_concentration` | {"expectancy_ex_top1pct_trades": 0.000715, "top10pct_winners_profit_share": 0.4781, "top1pct_winners_profit_share": 0.1267, "top5pct_winners_profit_share": 0.3263} |
| `merged.x10.dist.quantiles` | {"p1": -0.1709, "p10": -0.1196, "p25": -0.0496, "p5": -0.134, "p50": -0.0068, "p75": 0.0321, "p90": 0.1268, "p95": 0.2287, "p99": 0.527857} |
| `merged.x10.dist.win_rate` | 0.440629 |
| `merged.x12.dist.avg_loss` | -0.062009 |
| `merged.x12.dist.avg_win` | 0.0988 |
| `merged.x12.dist.big_win_share` | {"ge_2x_avg_loss": 0.1033, "ge_3x_avg_loss": 0.067, "ge_5x_avg_loss": 0.0308} |
| `merged.x12.dist.buckets` | "见子表 merged.x12.dist.buckets（9 行）" |
| `merged.x12.dist.by_reason` | "见子表 merged.x12.dist.by_reason（8 行）" |
| `merged.x12.dist.by_year` | "见子表 merged.x12.dist.by_year（22 行）" |
| `merged.x12.dist.expectancy` | 0.009441 |
| `merged.x12.dist.holding` | {"avg_loss": 12.993025, "avg_win": 17.765882} |
| `merged.x12.dist.margin` | 0.05871 |
| `merged.x12.dist.median_loss` | -0.0406 |
| `merged.x12.dist.median_win` | 0.0415 |
| `merged.x12.dist.n` | 569946 |
| `merged.x12.dist.paired_vs_ref` | {"delta_ret_mean": 8.8e-05, "delta_ret_quantiles": {"p25": 0.0, "p5": -0.0312, "p50": 0.0, "p75": 0.0, "p95": 0.0}, "flip_ref_loss_to_win": 4651, "flip_ref_win_to_loss": 0, "n_matched": 569855, "share_better": 0.0463, "share_identical": 0.8541, "share_worse": 0.0996, "stopped_here_not_ref": {"avg_ret_here": -0.180009, "avg_ret_ref": -0.157742, "n": 1338, "share_here_better": 0.0}, "stopped_ref_not_here": {"avg_ret_here": -0.055838, "avg_ret_ref": -0.112846, "n": 32340, "share_here_better": 0.7936}} |
| `merged.x12.dist.payoff_ratio` | 1.593324 |
| `merged.x12.dist.profit_concentration` | {"expectancy_ex_top1pct_trades": 0.000467, "top10pct_winners_profit_share": 0.4777, "top1pct_winners_profit_share": 0.1265, "top5pct_winners_profit_share": 0.3259} |
| `merged.x12.dist.quantiles` | {"p1": -0.1857, "p10": -0.1337, "p25": -0.0476, "p5": -0.1491, "p50": -0.0064, "p75": 0.0328, "p90": 0.1284, "p95": 0.2304, "p99": 0.5302} |
| `merged.x12.dist.win_rate` | 0.444316 |
| `merged.x15.dist.avg_loss` | -0.063882 |
| `merged.x15.dist.avg_win` | 0.099161 |
| `merged.x15.dist.big_win_share` | {"ge_2x_avg_loss": 0.1015, "ge_3x_avg_loss": 0.0652, "ge_5x_avg_loss": 0.0295} |
| `merged.x15.dist.buckets` | "见子表 merged.x15.dist.buckets（9 行）" |
| `merged.x15.dist.by_reason` | "见子表 merged.x15.dist.by_reason（8 行）" |
| `merged.x15.dist.by_year` | "见子表 merged.x15.dist.by_year（22 行）" |
| `merged.x15.dist.expectancy` | 0.009061 |
| `merged.x15.dist.holding` | {"avg_loss": 14.070732, "avg_win": 17.934307} |
| `merged.x15.dist.margin` | 0.055577 |
| `merged.x15.dist.median_loss` | -0.0398 |
| `merged.x15.dist.median_win` | 0.0417 |
| `merged.x15.dist.n` | 569951 |
| `merged.x15.dist.paired_vs_ref` | {"delta_ret_mean": -0.000284, "delta_ret_quantiles": {"p25": 0.0, "p5": -0.0543, "p50": 0.0, "p75": 0.0, "p95": 0.0209}, "flip_ref_loss_to_win": 6404, "flip_ref_win_to_loss": 0, "n_matched": 569850, "share_better": 0.0664, "share_identical": 0.833, "share_worse": 0.1006, "stopped_here_not_ref": {"avg_ret_here": -0.204249, "avg_ret_ref": -0.17218, "n": 1601, "share_here_better": 0.0}, "stopped_ref_not_here": {"avg_ret_here": -0.067575, "avg_ret_ref": -0.115003, "n": 51626, "share_here_better": 0.7111}} |
| `merged.x15.dist.payoff_ratio` | 1.552251 |
| `merged.x15.dist.profit_concentration` | {"expectancy_ex_top1pct_trades": 1.3e-05, "top10pct_winners_profit_share": 0.4778, "top1pct_winners_profit_share": 0.1267, "top5pct_winners_profit_share": 0.3261} |
| `merged.x15.dist.quantiles` | {"p1": -0.2099, "p10": -0.128, "p25": -0.0461, "p5": -0.173, "p50": -0.006, "p75": 0.0334, "p90": 0.1297, "p95": 0.2321, "p99": 0.5336} |
| `merged.x15.dist.win_rate` | 0.447388 |
| `merged.x20.dist.avg_loss` | -0.065777 |
| `merged.x20.dist.avg_win` | 0.099567 |
| `merged.x20.dist.big_win_share` | {"ge_2x_avg_loss": 0.0996, "ge_3x_avg_loss": 0.0635, "ge_5x_avg_loss": 0.0281} |
| `merged.x20.dist.buckets` | "见子表 merged.x20.dist.buckets（9 行）" |
| `merged.x20.dist.by_reason` | "见子表 merged.x20.dist.by_reason（8 行）" |
| `merged.x20.dist.by_year` | "见子表 merged.x20.dist.by_year（22 行）" |
| `merged.x20.dist.expectancy` | 0.008633 |
| `merged.x20.dist.holding` | {"avg_loss": 15.174388, "avg_win": 18.112375} |
| `merged.x20.dist.margin` | 0.052212 |
| `merged.x20.dist.median_loss` | -0.0392 |
| `merged.x20.dist.median_win` | 0.0418 |
| `merged.x20.dist.n` | 569940 |
| `merged.x20.dist.paired_vs_ref` | {"delta_ret_mean": -0.000714, "delta_ret_quantiles": {"p25": 0.0, "p5": -0.0766, "p50": 0.0, "p75": 0.0, "p95": 0.0302}, "flip_ref_loss_to_win": 7905, "flip_ref_win_to_loss": 0, "n_matched": 569837, "share_better": 0.078, "share_identical": 0.8293, "share_worse": 0.0927, "stopped_here_not_ref": {"avg_ret_here": -0.246177, "avg_ret_ref": -0.189067, "n": 1340, "share_here_better": 0.0}, "stopped_ref_not_here": {"avg_ret_here": -0.081539, "avg_ret_ref": -0.116095, "n": 70287, "share_here_better": 0.6069}} |
| `merged.x20.dist.payoff_ratio` | 1.51371 |
| `merged.x20.dist.profit_concentration` | {"expectancy_ex_top1pct_trades": -0.000508, "top10pct_winners_profit_share": 0.4781, "top1pct_winners_profit_share": 0.1274, "top5pct_winners_profit_share": 0.3266} |
| `merged.x20.dist.quantiles` | {"p1": -0.2508, "p10": -0.1144, "p25": -0.045, "p5": -0.2099, "p50": -0.0057, "p75": 0.034, "p90": 0.131, "p95": 0.2336, "p99": 0.537} |
| `merged.x20.dist.win_rate` | 0.45003 |
| `merged.x5.dist.avg_loss` | -0.052507 |
| `merged.x5.dist.avg_win` | 0.096626 |
| `merged.x5.dist.big_win_share` | {"ge_2x_avg_loss": 0.1078, "ge_3x_avg_loss": 0.0732, "ge_5x_avg_loss": 0.0369} |
| `merged.x5.dist.buckets` | "见子表 merged.x5.dist.buckets（9 行）" |
| `merged.x5.dist.by_reason` | "见子表 merged.x5.dist.by_reason（8 行）" |
| `merged.x5.dist.by_year` | "见子表 merged.x5.dist.by_year（22 行）" |
| `merged.x5.dist.expectancy` | 0.009036 |
| `merged.x5.dist.holding` | {"avg_loss": 8.452642, "avg_win": 16.586575} |
| `merged.x5.dist.margin` | 0.060591 |
| `merged.x5.dist.median_loss` | -0.0579 |
| `merged.x5.dist.median_win` | 0.0399 |
| `merged.x5.dist.n` | 569972 |
| `merged.x5.dist.paired_vs_ref` | {"delta_ret_mean": -0.000321, "delta_ret_quantiles": {"p25": 0.0, "p5": -0.034, "p50": 0.0, "p75": 0.0, "p95": 0.0472}, "flip_ref_loss_to_win": 389, "flip_ref_win_to_loss": 13763, "n_matched": 569859, "share_better": 0.1602, "share_identical": 0.7469, "share_worse": 0.0929, "stopped_here_not_ref": {"avg_ret_here": -0.071223, "avg_ret_ref": -0.020962, "n": 70476, "share_here_better": 0.1653}, "stopped_ref_not_here": {"avg_ret_here": -0.042466, "avg_ret_ref": -0.123563, "n": 1616, "share_here_better": 0.5427}} |
| `merged.x5.dist.payoff_ratio` | 1.840262 |
| `merged.x5.dist.profit_concentration` | {"expectancy_ex_top1pct_trades": 0.000431, "top10pct_winners_profit_share": 0.4821, "top1pct_winners_profit_share": 0.128, "top5pct_winners_profit_share": 0.3292} |
| `merged.x5.dist.quantiles` | {"p1": -0.1374, "p10": -0.0821, "p25": -0.0629, "p5": -0.0965, "p50": -0.0108, "p75": 0.0269, "p90": 0.1146, "p95": 0.2153, "p99": 0.507829} |
| `merged.x5.dist.win_rate` | 0.412671 |
| `merged.x6.dist.avg_loss` | -0.054933 |
| `merged.x6.dist.avg_win` | 0.097241 |
| `merged.x6.dist.big_win_share` | {"ge_2x_avg_loss": 0.1071, "ge_3x_avg_loss": 0.0717, "ge_5x_avg_loss": 0.0353} |
| `merged.x6.dist.buckets` | "见子表 merged.x6.dist.buckets（9 行）" |
| `merged.x6.dist.by_reason` | "见子表 merged.x6.dist.by_reason（8 行）" |
| `merged.x6.dist.by_year` | "见子表 merged.x6.dist.by_year（22 行）" |
| `merged.x6.dist.expectancy` | 0.009236 |
| `merged.x6.dist.holding` | {"avg_loss": 9.336663, "avg_win": 16.863366} |
| `merged.x6.dist.margin` | 0.060694 |
| `merged.x6.dist.median_loss` | -0.0531 |
| `merged.x6.dist.median_win` | 0.0403 |
| `merged.x6.dist.n` | 569962 |
| `merged.x6.dist.paired_vs_ref` | {"delta_ret_mean": -0.000121, "delta_ret_quantiles": {"p25": 0.0, "p5": -0.0206, "p50": 0.0, "p75": 0.0, "p95": 0.0366}, "flip_ref_loss_to_win": 510, "flip_ref_win_to_loss": 8753, "n_matched": 569859, "share_better": 0.1346, "share_identical": 0.7968, "share_worse": 0.0686, "stopped_here_not_ref": {"avg_ret_here": -0.079673, "avg_ret_ref": -0.026955, "n": 48342, "share_here_better": 0.125}, "stopped_ref_not_here": {"avg_ret_here": -0.032918, "avg_ret_ref": -0.121537, "n": 1874, "share_here_better": 0.5934}} |
| `merged.x6.dist.payoff_ratio` | 1.770182 |
| `merged.x6.dist.profit_concentration` | {"expectancy_ex_top1pct_trades": 0.000522, "top10pct_winners_profit_share": 0.4809, "top1pct_winners_profit_share": 0.1277, "top5pct_winners_profit_share": 0.3284} |
| `merged.x6.dist.quantiles` | {"p1": -0.1441, "p10": -0.0898, "p25": -0.0687, "p5": -0.1042, "p50": -0.0094, "p75": 0.0285, "p90": 0.1186, "p95": 0.2199, "p99": 0.514539} |
| `merged.x6.dist.win_rate` | 0.421681 |
| `merged.x7.dist.avg_loss` | -0.0568 |
| `merged.x7.dist.avg_win` | 0.097724 |
| `merged.x7.dist.big_win_share` | {"ge_2x_avg_loss": 0.1063, "ge_3x_avg_loss": 0.0707, "ge_5x_avg_loss": 0.0341} |
| `merged.x7.dist.buckets` | "见子表 merged.x7.dist.buckets（9 行）" |
| `merged.x7.dist.by_reason` | "见子表 merged.x7.dist.by_reason（8 行）" |
| `merged.x7.dist.by_year` | "见子表 merged.x7.dist.by_year（22 行）" |
| `merged.x7.dist.expectancy` | 0.009434 |
| `merged.x7.dist.holding` | {"avg_loss": 10.136592, "avg_win": 17.108874} |
| `merged.x7.dist.margin` | 0.061051 |
| `merged.x7.dist.median_loss` | -0.047 |
| `merged.x7.dist.median_win` | 0.0407 |
| `merged.x7.dist.n` | 569957 |
| `merged.x7.dist.paired_vs_ref` | {"delta_ret_mean": 7.6e-05, "delta_ret_quantiles": {"p25": 0.0, "p5": 0.0, "p50": 0.0, "p75": 0.0, "p95": 0.0258}, "flip_ref_loss_to_win": 677, "flip_ref_win_to_loss": 4962, "n_matched": 569859, "share_better": 0.1051, "share_identical": 0.848, "share_worse": 0.0469, "stopped_here_not_ref": {"avg_ret_here": -0.087915, "avg_ret_ref": -0.033282, "n": 29532, "share_here_better": 0.0871}, "stopped_ref_not_here": {"avg_ret_here": -0.029644, "avg_ret_ref": -0.119422, "n": 2385, "share_here_better": 0.6478}} |
| `merged.x7.dist.payoff_ratio` | 1.72051 |
| `merged.x7.dist.profit_concentration` | {"expectancy_ex_top1pct_trades": 0.000642, "top10pct_winners_profit_share": 0.4798, "top1pct_winners_profit_share": 0.1273, "top5pct_winners_profit_share": 0.3275} |
| `merged.x7.dist.quantiles` | {"p1": -0.1506, "p10": -0.0974, "p25": -0.0626, "p5": -0.1119, "p50": -0.0084, "p75": 0.0298, "p90": 0.1217, "p95": 0.2234, "p99": 0.519244} |
| `merged.x7.dist.win_rate` | 0.428629 |
| `merged.x8.dist.avg_loss` | -0.058261 |
| `merged.x8.dist.avg_win` | 0.098095 |
| `merged.x8.dist.big_win_share` | {"ge_2x_avg_loss": 0.1056, "ge_3x_avg_loss": 0.0698, "ge_5x_avg_loss": 0.0331} |
| `merged.x8.dist.buckets` | "见子表 merged.x8.dist.buckets（9 行）" |
| `merged.x8.dist.by_reason` | "见子表 merged.x8.dist.by_reason（8 行）" |
| `merged.x8.dist.by_year` | "见子表 merged.x8.dist.by_year（22 行）" |
| `merged.x8.dist.expectancy` | 0.009578 |
| `merged.x8.dist.holding` | {"avg_loss": 10.848503, "avg_win": 17.309119} |
| `merged.x8.dist.margin` | 0.06126 |
| `merged.x8.dist.median_loss` | -0.0443 |
| `merged.x8.dist.median_win` | 0.041 |
| `merged.x8.dist.n` | 569947 |
| `merged.x8.dist.paired_vs_ref` | {"delta_ret_mean": 0.000219, "delta_ret_quantiles": {"p25": 0.0, "p5": 0.0, "p50": 0.0, "p75": 0.0, "p95": 0.0134}, "flip_ref_loss_to_win": 959, "flip_ref_win_to_loss": 2257, "n_matched": 569858, "share_better": 0.0686, "share_identical": 0.8999, "share_worse": 0.0315, "stopped_here_not_ref": {"avg_ret_here": -0.096059, "avg_ret_ref": -0.040864, "n": 14701, "share_here_better": 0.0553}, "stopped_ref_not_here": {"avg_ret_here": -0.029303, "avg_ret_ref": -0.116091, "n": 3614, "share_here_better": 0.7205}} |
| `merged.x8.dist.payoff_ratio` | 1.68372 |
| `merged.x8.dist.profit_concentration` | {"expectancy_ex_top1pct_trades": 0.000724, "top10pct_winners_profit_share": 0.4791, "top1pct_winners_profit_share": 0.1271, "top5pct_winners_profit_share": 0.327} |
| `merged.x8.dist.quantiles` | {"p1": -0.1569, "p10": -0.1049, "p25": -0.0548, "p5": -0.1194, "p50": -0.0077, "p75": 0.0308, "p90": 0.1239, "p95": 0.2255, "p99": 0.5231} |
| `merged.x8.dist.win_rate` | 0.433877 |
| `provenance.cmdline` | "--scan-widths --trade-dist --tag r44_scan_dist" |
| `provenance.data_last_date` | "2026-09-04" |
| `provenance.git_dirty` | false |
| `provenance.git_sha` | "ce48de678f" |
| `provenance.n_codes` | 5576 |
| `provenance.universe_rule` | "ashare_full_asof" |
| `provenance.universe_sha256` | "f0692547ad66f1b2" |
| `readings.mining_a.ref.dist.big_win_share` | {"ge_2x_avg_loss": 0.1026, "ge_3x_avg_loss": 0.0668, "ge_5x_avg_loss": 0.0303} |
| `readings.mining_a.ref.dist.margin` | 0.070368 |
| `readings.mining_a.ref.dist.payoff_ratio` | 1.612608 |
| `readings.mining_a.ref.dist.win_rate` | 0.453128 |
| `readings.mining_a.x10.dist.big_win_share` | {"ge_2x_avg_loss": 0.1028, "ge_3x_avg_loss": 0.0665, "ge_5x_avg_loss": 0.0299} |
| `readings.mining_a.x10.dist.margin` | 0.07223 |
| `readings.mining_a.x10.dist.payoff_ratio` | 1.587944 |
| `readings.mining_a.x10.dist.win_rate` | 0.458637 |
| `readings.mining_a.x12.dist.big_win_share` | {"ge_2x_avg_loss": 0.1024, "ge_3x_avg_loss": 0.0661, "ge_5x_avg_loss": 0.0294} |
| `readings.mining_a.x12.dist.margin` | 0.073111 |
| `readings.mining_a.x12.dist.payoff_ratio` | 1.568456 |
| `readings.mining_a.x12.dist.win_rate` | 0.46245 |
| `readings.mining_a.x15.dist.big_win_share` | {"ge_2x_avg_loss": 0.1023, "ge_3x_avg_loss": 0.0658, "ge_5x_avg_loss": 0.0292} |
| `readings.mining_a.x15.dist.margin` | 0.074666 |
| `readings.mining_a.x15.dist.payoff_ratio` | 1.558519 |
| `readings.mining_a.x15.dist.win_rate` | 0.465517 |
| `readings.mining_a.x20.dist.big_win_share` | {"ge_2x_avg_loss": 0.1038, "ge_3x_avg_loss": 0.0668, "ge_5x_avg_loss": 0.0297} |
| `readings.mining_a.x20.dist.margin` | 0.080406 |
| `readings.mining_a.x20.dist.payoff_ratio` | 1.573945 |
| `readings.mining_a.x20.dist.win_rate` | 0.468914 |
| `readings.mining_a.x5.dist.big_win_share` | {"ge_2x_avg_loss": 0.1044, "ge_3x_avg_loss": 0.0694, "ge_5x_avg_loss": 0.0334} |
| `readings.mining_a.x5.dist.margin` | 0.067587 |
| `readings.mining_a.x5.dist.payoff_ratio` | 1.730637 |
| `readings.mining_a.x5.dist.win_rate` | 0.433802 |
| `readings.mining_a.x6.dist.big_win_share` | {"ge_2x_avg_loss": 0.1039, "ge_3x_avg_loss": 0.0685, "ge_5x_avg_loss": 0.0323} |
| `readings.mining_a.x6.dist.margin` | 0.06929 |
| `readings.mining_a.x6.dist.payoff_ratio` | 1.685537 |
| `readings.mining_a.x6.dist.win_rate` | 0.441655 |
| `readings.mining_a.x7.dist.big_win_share` | {"ge_2x_avg_loss": 0.1036, "ge_3x_avg_loss": 0.0678, "ge_5x_avg_loss": 0.0314} |
| `readings.mining_a.x7.dist.margin` | 0.070449 |
| `readings.mining_a.x7.dist.payoff_ratio` | 1.65239 |
| `readings.mining_a.x7.dist.win_rate` | 0.447467 |
| `readings.mining_a.x8.dist.big_win_share` | {"ge_2x_avg_loss": 0.1034, "ge_3x_avg_loss": 0.0675, "ge_5x_avg_loss": 0.0308} |
| `readings.mining_a.x8.dist.margin` | 0.071766 |
| `readings.mining_a.x8.dist.payoff_ratio` | 1.627429 |
| `readings.mining_a.x8.dist.win_rate` | 0.452366 |
| `readings.mining_b.ref.dist.big_win_share` | {"ge_2x_avg_loss": 0.1058, "ge_3x_avg_loss": 0.0697, "ge_5x_avg_loss": 0.0334} |
| `readings.mining_b.ref.dist.margin` | 0.05483 |
| `readings.mining_b.ref.dist.payoff_ratio` | 1.680984 |
| `readings.mining_b.ref.dist.win_rate` | 0.427828 |
| `readings.mining_b.x10.dist.big_win_share` | {"ge_2x_avg_loss": 0.1056, "ge_3x_avg_loss": 0.0692, "ge_5x_avg_loss": 0.0328} |
| `readings.mining_b.x10.dist.margin` | 0.055012 |
| `readings.mining_b.x10.dist.payoff_ratio` | 1.654809 |
| `readings.mining_b.x10.dist.win_rate` | 0.431687 |
| `readings.mining_b.x12.dist.big_win_share` | {"ge_2x_avg_loss": 0.1038, "ge_3x_avg_loss": 0.0674, "ge_5x_avg_loss": 0.0314} |
| `readings.mining_b.x12.dist.margin` | 0.051702 |
| `readings.mining_b.x12.dist.payoff_ratio` | 1.606826 |
| `readings.mining_b.x12.dist.win_rate` | 0.435311 |
| `readings.mining_b.x15.dist.big_win_share` | {"ge_2x_avg_loss": 0.1012, "ge_3x_avg_loss": 0.0651, "ge_5x_avg_loss": 0.0297} |
| `readings.mining_b.x15.dist.margin` | 0.04638 |
| `readings.mining_b.x15.dist.payoff_ratio` | 1.550995 |
| `readings.mining_b.x15.dist.win_rate` | 0.438384 |
| `readings.mining_b.x20.dist.big_win_share` | {"ge_2x_avg_loss": 0.0979, "ge_3x_avg_loss": 0.062, "ge_5x_avg_loss": 0.0276} |
| `readings.mining_b.x20.dist.margin` | 0.038839 |
| `readings.mining_b.x20.dist.payoff_ratio` | 1.488722 |
| `readings.mining_b.x20.dist.win_rate` | 0.440651 |
| `readings.mining_b.x5.dist.big_win_share` | {"ge_2x_avg_loss": 0.1095, "ge_3x_avg_loss": 0.0751, "ge_5x_avg_loss": 0.0386} |
| `readings.mining_b.x5.dist.margin` | 0.057059 |
| `readings.mining_b.x5.dist.payoff_ratio` | 1.897568 |
| `readings.mining_b.x5.dist.win_rate` | 0.402176 |
| `readings.mining_b.x6.dist.big_win_share` | {"ge_2x_avg_loss": 0.1085, "ge_3x_avg_loss": 0.0734, "ge_5x_avg_loss": 0.0367} |
| `readings.mining_b.x6.dist.margin` | 0.056409 |
| `readings.mining_b.x6.dist.payoff_ratio` | 1.814116 |
| `readings.mining_b.x6.dist.win_rate` | 0.411761 |
| `readings.mining_b.x7.dist.big_win_share` | {"ge_2x_avg_loss": 0.1076, "ge_3x_avg_loss": 0.0721, "ge_5x_avg_loss": 0.0354} |
| `readings.mining_b.x7.dist.margin` | 0.056401 |
| `readings.mining_b.x7.dist.payoff_ratio` | 1.755788 |
| `readings.mining_b.x7.dist.win_rate` | 0.419273 |
| `readings.mining_b.x8.dist.big_win_share` | {"ge_2x_avg_loss": 0.1068, "ge_3x_avg_loss": 0.071, "ge_5x_avg_loss": 0.0343} |
| `readings.mining_b.x8.dist.margin` | 0.056089 |
| `readings.mining_b.x8.dist.payoff_ratio` | 1.71292 |
| `readings.mining_b.x8.dist.win_rate` | 0.424695 |
| `verdict` | "falsified" |
| `window_usage` | {"k": null, "note": "扫描模式只读挖掘 A∪B——判定段不读、批次门不触发（v2 纪律）", "window": "not_read"} |

子表 `merged.ref.dist.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 6805 | 0.0119 |
| "-15~-10%" | 95631 | 0.1678 |
| "-10~-5%" | 44220 | 0.0776 |
| "-5~0%" | 174620 | 0.3064 |
| "0~5%" | 137270 | 0.2409 |
| "5~10%" | 42330 | 0.0743 |
| "10~20%" | 35198 | 0.0618 |
| "20~30%" | 15796 | 0.0277 |
| ">30%" | 17991 | 0.0316 |

子表 `merged.ref.dist.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 14.12 | 0.021771 | 441862 | "bbi_exit" | 0.7754 | 0.5066 |
| 10.54 | -0.117687 | 97326 | "stop" | 0.1708 | 0.0 |
| 26.99 | 0.327022 | 23679 | "bbi_exit+scaled" | 0.0416 | 0.9964 |
| 6.51 | -0.177195 | 5085 | "stop_delayed" | 0.0089 | 0.0 |
| 19.08 | -0.009055 | 1047 | "bbi_exit_delayed" | 0.0018 | 0.3381 |
| 37.78 | 0.454604 | 815 | "bbi_exit_delayed+scaled" | 0.0014 | 0.9767 |
| 12.94 | -0.013072 | 32 | "stop+scaled" | 0.0001 | 0.1562 |
| 13.0 | -0.023167 | 15 | "stop_delayed+scaled" | 0.0 | 0.2667 |

子表 `merged.ref.dist.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2000" | 0.00426 | 0.061262 | 5470 | 1.43555 | 0.471846 |
| "2001" | -0.00176 | -0.019132 | 5319 | 0.9407 | 0.496146 |
| "2002" | -0.038544 | -0.462717 | 6692 | 0.551048 | 0.182008 |
| "2003" | 0.005238 | 0.037424 | 15268 | 1.273846 | 0.477207 |
| "2004" | -0.020292 | -0.141233 | 5874 | 1.04626 | 0.347463 |
| "2005" | -0.028399 | -0.159447 | 6196 | 1.137336 | 0.308425 |
| "2006" | 0.075157 | 0.326721 | 15580 | 3.727383 | 0.538254 |
| "2007" | 0.151764 | 0.612036 | 7125 | 5.487615 | 0.766175 |
| "2008" | -0.025349 | -0.112985 | 6897 | 1.100017 | 0.363201 |
| "2009" | 0.04092 | 0.312579 | 3442 | 2.318856 | 0.613887 |
| "2010" | 0.005096 | 0.038452 | 10967 | 1.378805 | 0.458831 |
| "2011" | -0.024279 | -0.253008 | 12693 | 0.720518 | 0.328212 |
| "2012" | -0.015453 | -0.125933 | 17281 | 0.940323 | 0.389445 |
| "2013" | -0.009686 | -0.068539 | 12776 | 1.138368 | 0.399108 |
| "2014" | 0.023486 | 0.176207 | 23602 | 1.787806 | 0.534912 |
| "2015" | 0.005478 | 0.015978 | 6488 | 1.459129 | 0.422626 |
| "2016" | 0.013121 | 0.120298 | 27365 | 1.783249 | 0.479591 |
| "2022" | 0.020573 | 0.158386 | 71184 | 1.712652 | 0.527029 |
| "2023" | -0.01257 | -0.116136 | 142376 | 1.090109 | 0.362308 |
| "2024" | 0.073692 | 0.276908 | 68419 | 2.494826 | 0.563045 |
| "2025" | 0.028506 | 0.225783 | 26334 | 2.248213 | 0.533645 |
| "2026" | -0.029839 | -0.198189 | 72513 | 1.035552 | 0.293078 |

子表 `merged.x10.dist.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 13164 | 0.0231 |
| "-15~-10%" | 78720 | 0.1381 |
| "-10~-5%" | 49981 | 0.0877 |
| "-5~0%" | 176945 | 0.3105 |
| "0~5%" | 138199 | 0.2425 |
| "5~10%" | 42758 | 0.075 |
| "10~20%" | 35754 | 0.0627 |
| "20~30%" | 16072 | 0.0282 |
| ">30%" | 18351 | 0.0322 |

子表 `merged.x10.dist.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 14.34 | 0.019712 | 455139 | "bbi_exit" | 0.7986 | 0.4958 |
| 11.62 | -0.129106 | 84276 | "stop" | 0.1479 | 0.0 |
| 27.11 | 0.3223 | 24484 | "bbi_exit+scaled" | 0.043 | 0.9927 |
| 7.87 | -0.212545 | 3881 | "stop_delayed" | 0.0068 | 0.0 |
| 17.91 | -0.029623 | 1279 | "bbi_exit_delayed" | 0.0022 | 0.2823 |
| 37.32 | 0.441122 | 857 | "bbi_exit_delayed+scaled" | 0.0015 | 0.965 |
| 18.33 | -0.0218 | 18 | "stop+scaled" | 0.0 | 0.0556 |
| 19.5 | -0.03635 | 10 | "stop_delayed+scaled" | 0.0 | 0.3 |

子表 `merged.x10.dist.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2000" | 0.004293 | 0.06172 | 5470 | 1.436102 | 0.472212 |
| "2001" | -0.001861 | -0.020029 | 5319 | 0.929597 | 0.498214 |
| "2002" | -0.039155 | -0.462439 | 6692 | 0.549561 | 0.182905 |
| "2003" | 0.008048 | 0.0581 | 15268 | 1.319683 | 0.489193 |
| "2004" | -0.022074 | -0.150626 | 5874 | 1.001513 | 0.348996 |
| "2005" | -0.023359 | -0.124908 | 6196 | 1.204255 | 0.32876 |
| "2006" | 0.078099 | 0.340406 | 15580 | 3.874122 | 0.545571 |
| "2007" | 0.152029 | 0.613491 | 7125 | 5.519506 | 0.766877 |
| "2008" | -0.023454 | -0.102159 | 6897 | 1.0122 | 0.394809 |
| "2009" | 0.041331 | 0.317053 | 3442 | 2.349216 | 0.61563 |
| "2010" | 0.004731 | 0.035408 | 10967 | 1.352584 | 0.460472 |
| "2011" | -0.024728 | -0.255449 | 12693 | 0.708249 | 0.329946 |
| "2012" | -0.015402 | -0.124064 | 17281 | 0.925469 | 0.39529 |
| "2013" | -0.010274 | -0.071275 | 12776 | 1.113617 | 0.401847 |
| "2014" | 0.024107 | 0.179853 | 23602 | 1.801058 | 0.536861 |
| "2015" | -0.004306 | -0.011901 | 6572 | 1.265258 | 0.42955 |
| "2016" | 0.014009 | 0.128756 | 27363 | 1.821842 | 0.483134 |
| "2022" | 0.021389 | 0.164436 | 71184 | 1.721229 | 0.531917 |
| "2023" | -0.012544 | -0.11502 | 142377 | 1.085979 | 0.364371 |
| "2024" | 0.075095 | 0.278934 | 68419 | 2.435786 | 0.569988 |
| "2025" | 0.028899 | 0.229417 | 26334 | 2.267029 | 0.535505 |
| "2026" | -0.031491 | -0.204075 | 72513 | 0.994632 | 0.297271 |

子表 `merged.x12.dist.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 27385 | 0.048 |
| "-15~-10%" | 52244 | 0.0917 |
| "-10~-5%" | 57891 | 0.1016 |
| "-5~0%" | 179190 | 0.3144 |
| "0~5%" | 139073 | 0.244 |
| "5~10%" | 43191 | 0.0758 |
| "10~20%" | 36161 | 0.0634 |
| "20~30%" | 16242 | 0.0285 |
| ">30%" | 18569 | 0.0326 |

子表 `merged.x12.dist.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 14.72 | 0.016364 | 472684 | "bbi_exit" | 0.8293 | 0.4805 |
| 13.26 | -0.14913 | 66318 | "stop" | 0.1164 | 0.0 |
| 27.31 | 0.317926 | 25184 | "bbi_exit+scaled" | 0.0442 | 0.9883 |
| 8.61 | -0.232111 | 3394 | "stop_delayed" | 0.006 | 0.0 |
| 17.48 | -0.042361 | 1450 | "bbi_exit_delayed" | 0.0025 | 0.251 |
| 37.09 | 0.429299 | 886 | "bbi_exit_delayed+scaled" | 0.0016 | 0.9549 |
| 19.7 | -0.04447 | 20 | "stop+scaled" | 0.0 | 0.0 |
| 22.8 | -0.07364 | 10 | "stop_delayed+scaled" | 0.0 | 0.1 |

子表 `merged.x12.dist.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2000" | 0.004285 | 0.061458 | 5470 | 1.432381 | 0.472578 |
| "2001" | -0.002427 | -0.025742 | 5319 | 0.903774 | 0.49953 |
| "2002" | -0.040588 | -0.465469 | 6692 | 0.539835 | 0.183951 |
| "2003" | 0.010661 | 0.077737 | 15270 | 1.360872 | 0.50131 |
| "2004" | -0.024623 | -0.163546 | 5876 | 0.946341 | 0.350238 |
| "2005" | -0.023263 | -0.120694 | 6196 | 1.188766 | 0.336185 |
| "2006" | 0.081664 | 0.35857 | 15580 | 4.134319 | 0.553338 |
| "2007" | 0.152332 | 0.615804 | 7125 | 5.570457 | 0.768 |
| "2008" | -0.023158 | -0.099688 | 6897 | 0.997078 | 0.401044 |
| "2009" | 0.041292 | 0.316505 | 3442 | 2.339838 | 0.615921 |
| "2010" | 0.004074 | 0.029967 | 10967 | 1.312079 | 0.462478 |
| "2011" | -0.026463 | -0.265433 | 12693 | 0.673843 | 0.331994 |
| "2012" | -0.015684 | -0.124118 | 17281 | 0.904267 | 0.401018 |
| "2013" | -0.012587 | -0.084549 | 12776 | 1.045402 | 0.404352 |
| "2014" | 0.024866 | 0.185788 | 23602 | 1.835067 | 0.538514 |
| "2015" | -0.005114 | -0.014026 | 6572 | 1.241336 | 0.432136 |
| "2016" | 0.014857 | 0.137741 | 27361 | 1.877298 | 0.485289 |
| "2022" | 0.022732 | 0.175877 | 71184 | 1.765894 | 0.537424 |
| "2023" | -0.012502 | -0.113602 | 142378 | 1.079103 | 0.367374 |
| "2024" | 0.073977 | 0.269615 | 68417 | 2.291696 | 0.57341 |
| "2025" | 0.02934 | 0.233658 | 26334 | 2.295935 | 0.537062 |
| "2026" | -0.034211 | -0.214599 | 72514 | 0.938699 | 0.301211 |

子表 `merged.x15.dist.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 51833 | 0.0909 |
| "-15~-10%" | 19593 | 0.0344 |
| "-10~-5%" | 62804 | 0.1102 |
| "-5~0%" | 180732 | 0.3171 |
| "0~5%" | 139764 | 0.2452 |
| "5~10%" | 43524 | 0.0764 |
| "10~20%" | 36503 | 0.064 |
| "20~30%" | 16404 | 0.0288 |
| ">30%" | 18794 | 0.033 |

子表 `merged.x15.dist.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 15.17 | 0.0122 | 491162 | "bbi_exit" | 0.8618 | 0.4647 |
| 15.7 | -0.180017 | 47290 | "stop" | 0.083 | 0.0 |
| 27.63 | 0.311787 | 26091 | "bbi_exit+scaled" | 0.0458 | 0.9771 |
| 9.84 | -0.261199 | 2849 | "stop_delayed" | 0.005 | 0.0 |
| 17.54 | -0.054617 | 1629 | "bbi_exit_delayed" | 0.0029 | 0.2253 |
| 36.76 | 0.419323 | 918 | "bbi_exit_delayed+scaled" | 0.0016 | 0.9412 |
| 28.38 | -0.1228 | 8 | "stop_delayed+scaled" | 0.0 | 0.0 |
| 26.5 | -0.082275 | 4 | "stop+scaled" | 0.0 | 0.0 |

子表 `merged.x15.dist.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2000" | 0.004493 | 0.064804 | 5470 | 1.450144 | 0.472943 |
| "2001" | -0.002787 | -0.028944 | 5319 | 0.883531 | 0.501974 |
| "2002" | -0.041533 | -0.469163 | 6692 | 0.530776 | 0.1841 |
| "2003" | 0.012516 | 0.092556 | 15275 | 1.405811 | 0.508216 |
| "2004" | -0.026126 | -0.170274 | 5875 | 0.909723 | 0.353362 |
| "2005" | -0.023501 | -0.120081 | 6196 | 1.168692 | 0.341026 |
| "2006" | 0.085406 | 0.375031 | 15580 | 4.447526 | 0.558601 |
| "2007" | 0.152473 | 0.617368 | 7125 | 5.62635 | 0.768281 |
| "2008" | -0.022459 | -0.096311 | 6897 | 0.981751 | 0.408293 |
| "2009" | 0.041364 | 0.317518 | 3442 | 2.351175 | 0.615921 |
| "2010" | 0.003163 | 0.022705 | 10967 | 1.264039 | 0.464393 |
| "2011" | -0.028536 | -0.277431 | 12693 | 0.639619 | 0.332467 |
| "2012" | -0.01587 | -0.123389 | 17281 | 0.887751 | 0.406342 |
| "2013" | -0.014839 | -0.096594 | 12776 | 0.987219 | 0.406622 |
| "2014" | 0.026136 | 0.195271 | 23602 | 1.900146 | 0.540081 |
| "2015" | -0.00412 | -0.011196 | 6572 | 1.219836 | 0.439288 |
| "2016" | 0.015642 | 0.146695 | 27361 | 1.93943 | 0.486897 |
| "2022" | 0.02385 | 0.18563 | 71184 | 1.813032 | 0.541119 |
| "2023" | -0.012364 | -0.111552 | 142377 | 1.077726 | 0.369744 |
| "2024" | 0.071397 | 0.252122 | 68417 | 2.071394 | 0.577707 |
| "2025" | 0.02959 | 0.236124 | 26334 | 2.316239 | 0.53767 |
| "2026" | -0.037118 | -0.22472 | 72516 | 0.888496 | 0.304802 |

子表 `merged.x20.dist.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 41155 | 0.0722 |
| "-15~-10%" | 25686 | 0.0451 |
| "-10~-5%" | 64830 | 0.1137 |
| "-5~0%" | 181779 | 0.3189 |
| "0~5%" | 140307 | 0.2462 |
| "5~10%" | 43826 | 0.0769 |
| "10~20%" | 36821 | 0.0646 |
| "20~30%" | 16552 | 0.029 |
| ">30%" | 18984 | 0.0333 |

子表 `merged.x20.dist.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 15.71 | 0.007185 | 509358 | "bbi_exit" | 0.8937 | 0.45 |
| 19.04 | -0.230643 | 28355 | "stop" | 0.0498 | 0.0 |
| 28.13 | 0.302966 | 27192 | "bbi_exit+scaled" | 0.0477 | 0.9577 |
| 11.39 | -0.307581 | 2265 | "stop_delayed" | 0.004 | 0.0 |
| 17.86 | -0.071007 | 1810 | "bbi_exit_delayed" | 0.0032 | 0.2028 |
| 36.62 | 0.408451 | 951 | "bbi_exit_delayed+scaled" | 0.0017 | 0.9222 |
| 31.0 | -0.1394 | 8 | "stop_delayed+scaled" | 0.0 | 0.0 |
| 31.0 | -0.1373 | 1 | "stop+scaled" | 0.0 | 0.0 |

子表 `merged.x20.dist.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2000" | 0.004332 | 0.062218 | 5470 | 1.433633 | 0.473126 |
| "2001" | -0.003139 | -0.032106 | 5319 | 0.861239 | 0.50517 |
| "2002" | -0.041611 | -0.466398 | 6692 | 0.535519 | 0.184848 |
| "2003" | 0.014938 | 0.113802 | 15276 | 1.504444 | 0.513092 |
| "2004" | -0.0269 | -0.173174 | 5875 | 0.890649 | 0.355745 |
| "2005" | -0.022154 | -0.113011 | 6196 | 1.175394 | 0.346675 |
| "2006" | 0.088963 | 0.387678 | 15580 | 4.75549 | 0.561425 |
| "2007" | 0.152438 | 0.616751 | 7125 | 5.593247 | 0.768421 |
| "2008" | -0.022065 | -0.094475 | 6897 | 0.97475 | 0.411918 |
| "2009" | 0.041258 | 0.316034 | 3442 | 2.334589 | 0.615921 |
| "2010" | 0.001501 | 0.010455 | 10967 | 1.195007 | 0.466034 |
| "2011" | -0.031312 | -0.292449 | 12693 | 0.598605 | 0.333097 |
| "2012" | -0.012952 | -0.101383 | 17281 | 0.924815 | 0.418147 |
| "2013" | -0.014126 | -0.090842 | 12776 | 0.99233 | 0.411083 |
| "2014" | 0.027604 | 0.206479 | 23602 | 1.985071 | 0.54148 |
| "2015" | 0.001102 | 0.002956 | 6572 | 1.21834 | 0.453743 |
| "2016" | 0.016153 | 0.152664 | 27359 | 1.988331 | 0.487299 |
| "2022" | 0.024575 | 0.192552 | 71184 | 1.850736 | 0.543338 |
| "2023" | -0.012593 | -0.112613 | 142377 | 1.068789 | 0.370762 |
| "2024" | 0.067631 | 0.229238 | 68419 | 1.82574 | 0.583127 |
| "2025" | 0.029663 | 0.236551 | 26334 | 2.317595 | 0.537974 |
| "2026" | -0.039768 | -0.233681 | 72504 | 0.848565 | 0.30728 |

子表 `merged.x5.dist.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 4056 | 0.0071 |
| "-15~-10%" | 20259 | 0.0355 |
| "-10~-5%" | 159245 | 0.2794 |
| "-5~0%" | 151201 | 0.2653 |
| "0~5%" | 131494 | 0.2307 |
| "5~10%" | 39702 | 0.0697 |
| "10~20%" | 32581 | 0.0572 |
| "20~30%" | 14672 | 0.0257 |
| ">30%" | 16762 | 0.0294 |

子表 `merged.x5.dist.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 13.02 | 0.030928 | 375410 | "bbi_exit" | 0.6586 | 0.5673 |
| 7.25 | -0.078341 | 166218 | "stop" | 0.2916 | 0.0 |
| 26.41 | 0.336533 | 21136 | "bbi_exit+scaled" | 0.0371 | 0.9985 |
| 5.99 | -0.162781 | 5647 | "stop_delayed" | 0.0099 | 0.0019 |
| 37.71 | 0.463865 | 746 | "bbi_exit_delayed+scaled" | 0.0013 | 0.9732 |
| 21.06 | 0.027638 | 740 | "bbi_exit_delayed" | 0.0013 | 0.4622 |
| 10.56 | 0.018858 | 59 | "stop+scaled" | 0.0001 | 0.7119 |
| 13.81 | 0.004656 | 16 | "stop_delayed+scaled" | 0.0 | 0.5 |

子表 `merged.x5.dist.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2000" | 0.003616 | 0.05192 | 5470 | 1.418218 | 0.465448 |
| "2001" | 0.001493 | 0.017739 | 5319 | 1.116893 | 0.49013 |
| "2002" | -0.029382 | -0.411055 | 6692 | 0.693842 | 0.179319 |
| "2003" | -0.00283 | -0.02067 | 15268 | 1.326166 | 0.409222 |
| "2004" | -0.013006 | -0.09964 | 5877 | 1.273866 | 0.34014 |
| "2005" | -0.023477 | -0.151856 | 6196 | 1.279703 | 0.286798 |
| "2006" | 0.062815 | 0.277772 | 15580 | 3.564483 | 0.496855 |
| "2007" | 0.149669 | 0.597602 | 7125 | 5.233112 | 0.758035 |
| "2008" | -0.022899 | -0.104404 | 6897 | 1.164056 | 0.357692 |
| "2009" | 0.040029 | 0.304862 | 3442 | 2.297996 | 0.608077 |
| "2010" | 0.005328 | 0.042266 | 10967 | 1.493937 | 0.443239 |
| "2011" | -0.018845 | -0.218538 | 12693 | 0.853832 | 0.320886 |
| "2012" | -0.012173 | -0.107498 | 17281 | 1.078125 | 0.373705 |
| "2013" | -0.005343 | -0.042092 | 12776 | 1.359911 | 0.381653 |
| "2014" | 0.020905 | 0.158632 | 23602 | 1.750508 | 0.522202 |
| "2015" | -0.000156 | -0.000446 | 6569 | 1.396633 | 0.416806 |
| "2016" | 0.010389 | 0.094785 | 27390 | 1.726775 | 0.461519 |
| "2022" | 0.013712 | 0.106993 | 71184 | 1.659117 | 0.483058 |
| "2023" | -0.011995 | -0.118988 | 142376 | 1.174174 | 0.340956 |
| "2024" | 0.072856 | 0.292715 | 68419 | 3.063186 | 0.538827 |
| "2025" | 0.025822 | 0.203469 | 26334 | 2.190898 | 0.51686 |
| "2026" | -0.023321 | -0.173739 | 72515 | 1.241462 | 0.272399 |

子表 `merged.x6.dist.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 4746 | 0.0083 |
| "-15~-10%" | 30010 | 0.0527 |
| "-10~-5%" | 133969 | 0.235 |
| "-5~0%" | 160895 | 0.2823 |
| "0~5%" | 133775 | 0.2347 |
| "5~10%" | 40601 | 0.0712 |
| "10~20%" | 33559 | 0.0589 |
| "20~30%" | 15143 | 0.0266 |
| ">30%" | 17264 | 0.0303 |

子表 `merged.x6.dist.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 13.31 | 0.028287 | 396877 | "bbi_exit" | 0.6963 | 0.5473 |
| 8.2 | -0.088668 | 143814 | "stop" | 0.2523 | 0.0 |
| 26.57 | 0.333426 | 22053 | "bbi_exit+scaled" | 0.0387 | 0.998 |
| 6.09 | -0.168093 | 5561 | "stop_delayed" | 0.0098 | 0.0005 |
| 20.58 | 0.016717 | 823 | "bbi_exit_delayed" | 0.0014 | 0.4204 |
| 37.73 | 0.459745 | 776 | "bbi_exit_delayed+scaled" | 0.0014 | 0.9729 |
| 11.58 | 0.012975 | 48 | "stop+scaled" | 0.0001 | 0.625 |
| 12.8 | 0.00673 | 10 | "stop_delayed+scaled" | 0.0 | 0.5 |

子表 `merged.x6.dist.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2000" | 0.004037 | 0.05797 | 5470 | 1.432298 | 0.469104 |
| "2001" | 0.00072 | 0.008337 | 5319 | 1.062703 | 0.493138 |
| "2002" | -0.032188 | -0.427208 | 6692 | 0.646299 | 0.180215 |
| "2003" | -0.000466 | -0.003366 | 15268 | 1.29684 | 0.432015 |
| "2004" | -0.015321 | -0.113843 | 5877 | 1.189597 | 0.342862 |
| "2005" | -0.024131 | -0.147889 | 6196 | 1.25283 | 0.295997 |
| "2006" | 0.067565 | 0.295777 | 15580 | 3.582621 | 0.513992 |
| "2007" | 0.150711 | 0.603463 | 7125 | 5.320278 | 0.761684 |
| "2008" | -0.022823 | -0.102404 | 6897 | 1.119362 | 0.369436 |
| "2009" | 0.040468 | 0.308975 | 3442 | 2.31437 | 0.610691 |
| "2010" | 0.005434 | 0.042577 | 10967 | 1.457285 | 0.44953 |
| "2011" | -0.020138 | -0.22624 | 12693 | 0.816228 | 0.324352 |
| "2012" | -0.013189 | -0.113706 | 17281 | 1.029725 | 0.378971 |
| "2013" | -0.005951 | -0.045644 | 12776 | 1.302749 | 0.388619 |
| "2014" | 0.021677 | 0.163572 | 23602 | 1.746536 | 0.527667 |
| "2015" | -0.000954 | -0.002703 | 6571 | 1.367277 | 0.419723 |
| "2016" | 0.011252 | 0.102557 | 27378 | 1.730649 | 0.468771 |
| "2022" | 0.015487 | 0.119351 | 71184 | 1.640933 | 0.498005 |
| "2023" | -0.012425 | -0.120048 | 142376 | 1.134959 | 0.348345 |
| "2024" | 0.074051 | 0.289852 | 68419 | 2.84299 | 0.550067 |
| "2025" | 0.02704 | 0.213272 | 26334 | 2.202206 | 0.525556 |
| "2026" | -0.025284 | -0.181693 | 72515 | 1.166925 | 0.27979 |

子表 `merged.x7.dist.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 5809 | 0.0102 |
| "-15~-10%" | 44518 | 0.0781 |
| "-10~-5%" | 107584 | 0.1888 |
| "-5~0%" | 167746 | 0.2943 |
| "0~5%" | 135395 | 0.2376 |
| "5~10%" | 41396 | 0.0726 |
| "10~20%" | 34366 | 0.0603 |
| "20~30%" | 15480 | 0.0272 |
| ">30%" | 17663 | 0.031 |

子表 `merged.x7.dist.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 13.61 | 0.025826 | 415524 | "bbi_exit" | 0.729 | 0.5304 |
| 9.11 | -0.099001 | 124487 | "stop" | 0.2184 | 0.0 |
| 26.73 | 0.330354 | 22819 | "bbi_exit+scaled" | 0.04 | 0.997 |
| 6.28 | -0.175018 | 5365 | "stop_delayed" | 0.0094 | 0.0002 |
| 20.09 | 0.005664 | 918 | "bbi_exit_delayed" | 0.0016 | 0.3845 |
| 37.68 | 0.456347 | 800 | "bbi_exit_delayed+scaled" | 0.0014 | 0.9712 |
| 13.75 | 0.006558 | 36 | "stop+scaled" | 0.0001 | 0.5556 |
| 13.75 | -0.016275 | 8 | "stop_delayed+scaled" | 0.0 | 0.375 |

子表 `merged.x7.dist.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2000" | 0.004122 | 0.059162 | 5470 | 1.430699 | 0.470567 |
| "2001" | -0.000382 | -0.00431 | 5319 | 1.003447 | 0.49483 |
| "2002" | -0.034514 | -0.44134 | 6692 | 0.606935 | 0.180962 |
| "2003" | 0.001896 | 0.013633 | 15267 | 1.286894 | 0.450907 |
| "2004" | -0.017013 | -0.123083 | 5876 | 1.132669 | 0.345813 |
| "2005" | -0.024379 | -0.142607 | 6196 | 1.230711 | 0.305681 |
| "2006" | 0.070888 | 0.309282 | 15580 | 3.628108 | 0.525353 |
| "2007" | 0.151307 | 0.607114 | 7125 | 5.3769 | 0.76393 |
| "2008" | -0.022914 | -0.101657 | 6897 | 1.084866 | 0.37799 |
| "2009" | 0.040817 | 0.312063 | 3442 | 2.325989 | 0.612725 |
| "2010" | 0.005511 | 0.042594 | 10967 | 1.434474 | 0.45336 |
| "2011" | -0.021322 | -0.233666 | 12693 | 0.783747 | 0.326952 |
| "2012" | -0.014064 | -0.118624 | 17281 | 0.992061 | 0.383369 |
| "2013" | -0.006914 | -0.051678 | 12776 | 1.247616 | 0.393237 |
| "2014" | 0.022559 | 0.169267 | 23602 | 1.762741 | 0.531226 |
| "2015" | -0.001807 | -0.005081 | 6572 | 1.338464 | 0.42255 |
| "2016" | 0.012122 | 0.110507 | 27374 | 1.751993 | 0.47388 |
| "2022" | 0.017475 | 0.133759 | 71184 | 1.651223 | 0.510943 |
| "2023" | -0.012644 | -0.119949 | 142376 | 1.108621 | 0.354294 |
| "2024" | 0.074623 | 0.286428 | 68419 | 2.687181 | 0.557637 |
| "2025" | 0.027862 | 0.220389 | 26334 | 2.224333 | 0.530531 |
| "2026" | -0.026884 | -0.186982 | 72515 | 1.114631 | 0.285913 |

子表 `merged.x8.dist.buckets`：

| bin | n | share |
|---|---|---|
| "≤-15%" | 7345 | 0.0129 |
| "-15~-10%" | 64262 | 0.1128 |
| "-10~-5%" | 78865 | 0.1384 |
| "-5~0%" | 172188 | 0.3021 |
| "0~5%" | 136596 | 0.2397 |
| "5~10%" | 42007 | 0.0737 |
| "10~20%" | 35003 | 0.0614 |
| "20~30%" | 15728 | 0.0276 |
| ">30%" | 17953 | 0.0315 |

子表 `merged.x8.dist.by_reason`：

| avg_holding | avg_ret | n | reason | share | win_rate |
|---|---|---|---|---|---|
| 13.88 | 0.02358 | 431120 | "bbi_exit" | 0.7564 | 0.5167 |
| 9.98 | -0.109205 | 108417 | "stop" | 0.1902 | 0.0 |
| 26.88 | 0.327697 | 23471 | "bbi_exit+scaled" | 0.0412 | 0.9959 |
| 6.61 | -0.182947 | 5045 | "stop_delayed" | 0.0089 | 0.0002 |
| 19.22 | -0.00772 | 1035 | "bbi_exit_delayed" | 0.0018 | 0.343 |
| 37.54 | 0.452437 | 824 | "bbi_exit_delayed+scaled" | 0.0014 | 0.9709 |
| 17.43 | -0.002413 | 30 | "stop+scaled" | 0.0001 | 0.4 |
| 13.4 | -0.0356 | 5 | "stop_delayed+scaled" | 0.0 | 0.2 |

子表 `merged.x8.dist.by_year`：

| year | expectancy | margin | n | payoff_ratio | win_rate |
|---|---|---|---|---|---|
| "2000" | 0.004152 | 0.059624 | 5470 | 1.42803 | 0.471481 |
| "2001" | -0.000653 | -0.007251 | 5319 | 0.983543 | 0.496898 |
| "2002" | -0.036469 | -0.451726 | 6692 | 0.579438 | 0.181411 |
| "2003" | 0.004468 | 0.032089 | 15267 | 1.29334 | 0.468134 |
| "2004" | -0.018941 | -0.133884 | 5876 | 1.08022 | 0.346835 |
| "2005" | -0.023942 | -0.134718 | 6196 | 1.224207 | 0.314881 |
| "2006" | 0.073576 | 0.320299 | 15580 | 3.686082 | 0.533697 |
| "2007" | 0.151753 | 0.611265 | 7125 | 5.467063 | 0.765895 |
| "2008" | -0.02287 | -0.100869 | 6897 | 1.055924 | 0.38553 |
| "2009" | 0.041004 | 0.313902 | 3442 | 2.333495 | 0.613887 |
| "2010" | 0.005447 | 0.041547 | 10967 | 1.405383 | 0.457281 |
| "2011" | -0.022574 | -0.241861 | 12693 | 0.754403 | 0.328134 |
| "2012" | -0.014602 | -0.120854 | 17281 | 0.965208 | 0.387998 |
| "2013" | -0.007799 | -0.056826 | 12776 | 1.200476 | 0.397621 |
| "2014" | 0.023337 | 0.17452 | 23602 | 1.783592 | 0.533768 |
| "2015" | -0.002959 | -0.008274 | 6572 | 1.311338 | 0.424376 |
| "2016" | 0.012926 | 0.118136 | 27366 | 1.777969 | 0.478112 |
| "2022" | 0.019044 | 0.145727 | 71184 | 1.669513 | 0.520328 |
| "2023" | -0.012646 | -0.118161 | 142376 | 1.097026 | 0.358705 |
| "2024" | 0.074833 | 0.28318 | 68419 | 2.575016 | 0.562899 |
| "2025" | 0.02832 | 0.224163 | 26334 | 2.239159 | 0.532885 |
| "2026" | -0.028447 | -0.192324 | 72513 | 1.070323 | 0.290693 |
<!-- /digest -->

- **复核（2026-10-21，描述性，不改 R44 falsified）**：①**全部利润来自最右尾**——各档去掉收益最高的 1% 交易后期望 ≈ 0
  （pct10 +0.05%、x20 −0.05%），前 5% 赢单贡献约 33% 毛利；B1 基底是「大量小亏小赚 + 少数大单」结构，这是 R47 设右尾护栏的直接依据。
  ②收紧止损的代价：x5 相对 pct10，13,763 笔由盈转亏、只有 389 笔由亏转盈；「x5 止损而 pct10 没止损」的 70,476 笔在 pct10 下平均
  −2.1%、在 x5 下 −7.1%——紧止损把本来会回升或小亏的单子锁成了大亏（收盘触发 + 跳空）。③≥3×均亏占比 6.4%~7.3%，各档相近。

