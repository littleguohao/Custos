# portfolio_review 每日持仓研判

日期：2026-07-17

## 1. 总体持仓风险

- market_timing：**{'version': 'B1-holding-v1', 'code': '605090', 'as_of': '2026-07-16', 'price_date': '2026-07-17', 'price_volume_current': False, 'market_regime': '空头', 'final_priority': 'P0', 'final_action': 'N型主结构清仓评估', 'final_reason': '价格37.82跌破L1主结构前低38.13', 'action_plan': {'suggested_reduction_pct_of_holding': [100, 100], 'exact_quantity': None, 'exact_quantity_reason': '精确数量必须由目标日完整行情、确认持仓基线和运行门控另行授权'}, 'signals': [{'signal': 'n_l1_breach', 'priority': 'P0', 'action': 'N型主结构清仓评估', 'reason': '价格37.82跌破L1主结构前低38.13'}, {'signal': 'bbi_first_breach', 'priority': 'P2', 'action': '次日收复观察', 'reason': '首日收盘跌破BBI，等待次日收复确认'}], 'facts': {'trend_state': '横盘震荡', 'box20_position': '箱体上半区', 'above_bbi': False, 'consecutive_closes_below_bbi': 1, 'n_structure': {'available': True, 'pattern': 'L1-H1-higher_L2-candidate', 'status': 'candidate', 'prior_low': 38.13, 'prior_low_date': '2026-04-03', 'origin_extreme_low': 37.6, 'breakout_level': 48.71, 'breakout_level_date': '2026-04-28', 'pullback_low': 43.27, 'pullback_low_date': '2026-05-08', 'confirmed_date': None, 'current_close': 37.82, 'distance_pct': -0.813, 'close_above': False, 'breached_on_close': True, 'pullback_breached_on_close': True, 'pivot_window': {'left': 3, 'right': 3}}, 'price_volume': {'available': True, 'date': '2026-07-16', 'change_pct': -9.0865, 'amplitude_pct': 6.0363, 'body_pct': 4.7355, 'volume_ratio_5': 0.9371, 'volume_ratio_20': 1.7751, 'volume_rank20_pct': 85.0, 'close_raised': False, 'shrink_small_bear': False, 'large_bear': True, 'heavy_large_bear': False, 'last_two_bull_metrics': [{'bull': True, 'change_pct': 5.5838, 'body_pct': 7.9958}, {'bull': False, 'change_pct': -9.0865, 'body_pct': -4.7355}], 'two_medium_large_bull': False, 'two_medium_large_bull_reason': '涨跌幅限制=20%，中大阳门槛=10.0%；T-1阳=True/涨幅5.5838%/实体7.9958%，T阳=False/涨幅-9.0865%/实体-4.7355%；BBI上方T-1=True,T=False', 'price_limit': 20, 'medium_large_bull_threshold': 10.0, 'extreme_shrink': False, 'reversal_k_candidate_without_j': False, 'thresholds': {'medium_large_bull_rule': '单日涨幅或阳线实体幅度达到当日涨跌幅限制的一半', 'small_bear_change_pct': [-2.0, 0.0], 'shrink_volume_ratio_5_max': 0.8, 'heavy_volume_ratio_5_min': 1.5, 'reversal_volume_ratio_5_max': 0.5, 'reversal_volume_rank20_pct_max': 10.0, 'reversal_close_change_pct': [-2.0, 2.0], 'reversal_amplitude_pct_max': 7.0}}, 'daily_j': 78.1994, 'holding_pnl_pct': -0.0512}, 'permissions': {'allow_add': False, 'allow_reduce': True, 'allow_signal_override_hard_risk': False}, 'unavailable': ['current_price_volume', 'max_favorable_excursion', 'opening_volume_ratio', 'trade_execution_feedback', 'wave_stage']}**
- 建议总仓位：**20%-40%**
- 原则：低位指标不能覆盖趋势、板块与风险规则。

## 2. 持仓逐只研判

| 优先级 | 代码 | 名称 | 仓位 | 盈亏 | 趋势/位置 | 动作 | 理由 |
|---|---|---|---:|---:|---|---|---|
| P0 | 600150 | 中国船舶 | 0.1514 | -0.0001 | 下跌/下沿/破位区 | 趋势破位退出评估 | 下跌趋势且跌破20日箱体；价格33.00跌破下降N型结构低点37.49；价格33.00跌破L2更高回踩低点37.49，但L1尚未失守 |
| P0 | 601696 | 中银证券 | 0.1153 | 0.1542 | 横盘震荡/箱体下半区 | 下降N型结构清仓评估 | 价格11.57跌破下降N型结构低点12.38；价格11.57跌破L2更高回踩低点12.38，但L1尚未失守；连续9日收盘跌破BBI |
| P0 | 605090 | 九丰能源 | 0.1103 | -0.0512 | 横盘震荡/箱体上半区 | N型主结构清仓评估 | 价格37.82跌破L1主结构前低38.13；首日收盘跌破BBI，等待次日收复确认 |
| P1 | 688114 | 华大智造 | 0.2156 | 0.1459 | 上涨/箱体下半区 | BBI清仓评估 | 连续4日收盘跌破BBI |
| P3 | 920808 | 曙光数创 | 0.2384 | 0.0119 | None/None | 条件持有 | 未触发B1减仓、止损或止盈信号 |

## 3. 风控触发项

- **华大智造(688114)**：BBI清仓评估。连续4日收盘跌破BBI

## 4. 数据声明

- 结构化输出：`C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team\01_data\holdings\2026-07-17_holding_review.json`
- 本报告是策略辅助，不构成收益承诺。