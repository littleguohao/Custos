"""因子实现层：**live 选股链与研究回测器共同依赖的下层**。

因子 = 判别维度，不含完整进出场规则，可被多个策略/消费方引用。
文档侧对应 `00_governance/strategy/_factors/`（跨策略因子）与各策略目录下的规则文档。

## ⚠️ 现存三套接口（2026-08-06 清点，尚未统一）

    compute_xxx(df) -> dict        s_shape / sector_phase / b1_dual 的一部分
    detect_xxx(df)  -> dict|None   b2_surge / main_rally / platform_pullback / b1_dual 的一部分
    _sc_xxx(df,code) -> dict|None  `screening/backtest_factors.SCORERS` 里的适配层
                                   （统一返回 {score, suggestion, aux, components}）

## ⚠️ 两种消费方式

    live 标注   `screening/signal_labels.py` → 把因子结果转成候选表上的标签
    研究打分    `screening/backtest_factors.SCORERS` → 横截面排序与回测

其中 `b1_dual_factor` / `b2_surge_factor` / `main_rally_factor` / `rsi_state`
**同时被两处消费、各自包装一遍** ⇒ 新增因子要在两处各写一个适配，
而两处的判据可能不一致。

**统一接口是语义改动**（会改 live 选股行为），必须单独立项 + 回测，
不能搭在目录重构里 —— 所以本次只做位置统一。

## 因子之间的依赖

    b1_dual_factor  → s_shape, platform_pullback
    main_rally_factor → rsi_state
    sector_phase    → sector_mainstream

⇒ 同目录内保持扁平 import；被外部消费时由消费方把本目录加进 `sys.path`
（与 `screening/`、`market_timing/`、`local_tdx/` 同一惯例）。
"""
