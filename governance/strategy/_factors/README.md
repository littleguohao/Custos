# 跨策略可复用因子

> **非策略上下文**（故目录带 `_` 前缀）。因子是**判别维度**，不含完整进出场规则，
> 可被多个策略引用。注册表：[`../STRATEGY_REGISTRY.json`](../STRATEGY_REGISTRY.json) 的 `factors` 段。

| 因子 | 文件 | 状态 | 被谁用 |
|---|---|---|---|
| 通用技术走势框架 | [technical_trend.md](technical_trend.md) | ⚠️ **未接线** | 无 |

## 因子注册表全量清单（43 个，按角色分组）

> 下表是 `src/custos/core/factors/` 注册表的**人读索引**（2026-10-15 从
> `_shared/factor_exit_catalog.md` 迁入）：字段级真相以代码为准（注册表强制登记，
> `tests/test_factor_registry.py` 钉住），权重数值在
> [`../../contracts/SCREEN_FORMULA_REGISTRY.json`](../../contracts/SCREEN_FORMULA_REGISTRY.json)
> 的 `scoring.weights`。各因子在 B1 中的用法与研究状态见
> [`../b1/90_regime_overview.md`](../b1/90_regime_overview.md)。

状态词表：`status` = active（已验证可用）/ candidate（在用但未经独立回测）/ untested / needs_work（按现有证据不可用）；
`live_use` = gate（门槛/否决）/ scorer（进打分/分层）/ evidence_only（只落盘展示）/ none（不进 live）；
`stage` = release（live 链引用）/ debug。机械约束：needs_work/untested 不得与 gate/scorer 共存（注册表测试强制）。

### A. live 打分/门槛因子（驱动 1800 选股结果）

| id | 名称 | live_use | 喂哪条腿 / 用途 |
|---|---|---|---|
| `j_low_gate` | J<13 进池硬门槛 | gate | 1800 唯一进池硬门槛（执行点 enrich `_apply_j_gate`） |
| `entry_patterns` | patterns 五单项判定 | scorer | 技术分：j_low **+24**（最大单项）、volume_contraction +15、relative_strength_strong +15、bbi_above +5、reversal_k_candidate +4 |
| `macd_technics` | MACD 十大技术 | scorer | 技术分：底背离 +8、水上 +7、zone1_restart +5、红柱增长 +5、周月红柱 +5、zone1 +3、**顶背离 −8**；cap：三打白骨精封顶 C |
| `volume_detectors` | 量能三件套 | scorer | 技术分：bottom_volume +10、leader_volume +6；资金意图证据；volume_sustain 主线确认（retreat 留痕，v0.60 起不封顶） |
| `b1_structure` | B1 结构检测器族 | scorer | 技术分：five_day_entry +8、repair 每项 +4（上限 +8）、non_one_wave confirmed +5；cap：non_one_wave revoked 封顶 C；liquidity 仅 flag；止损参考位 `_stop_ref` |
| `ignition` | 点火族 | scorer | 技术分：b1_ignition +8、pullback_shrink +5、ignition +4；资金意图证据；门内提醒判据（v0.89 起） |
| `weekly_j` | 周线 J 状态 | scorer | 技术分：weekly_j_low +5（周日共振） |
| `capital_intent` | 资金意图强度 | scorer | **分层第二轴**：9 条正向证据（ci_* 分值见 registry），≥5 强 / ≥2 中 / 否则弱 |
| `distribution` | 主力出货五方式 | gate | 技术分：watch **−10** / high **−20**；hits≥2 封 high；与 wave_type 同为 active 唯二 |
| `wave_type` | 前置拉升波分类 | gate | 冲刺波首个 B1 禁止买入（cap 层） |

配套（非因子注册但影响结果）：技术分组合器与分层矩阵在 `pipeline/screening/score_candidates.py`
（scorer 的家）；组合奖 `b1_healthy_pullback_pack` +9；趋势腿 adx>60 +5；阴阳量 volume_yy +7/−5
（检测器在 `bottom_patterns.bull_bear_volume`）；cap 编排 `apply_risk_downgrades`（6 条 +
无止损位→B、0AMV 空头→B 两条硬 cap）。

### B. live 证据层因子（落盘展示，不进分不进分层）

| id | 名称 | 备注 |
|---|---|---|
| `fundamentals` | 基本面（CZ 三条件代理 + 品质档） | 四面共振基本面腿、🐂 展示 |
| `sector_phase` | 板块相位（880 MACD） | R4 未复现，降证据层（v0.50 移出可买定义） |
| `bottom_patterns` | W 底 / 红肥绿瘦 | ⚠️ 同模块 `bull_bear_volume` 实为打分腿（见 A 配套） |
| `rsi_state` | RSI 状态（H3） | 研究打分参考 |
| `main_rally_factor` | 主升始发点（H4） | 原文两处矛盾，双口径由回测判定 |
| `b1_dual_factor` | B1 双轴（长期结构 × 短期回调） | 需 ≥120 根（DKS=MA114） |
| `b2_surge_factor` | B2 异动 / 底部异动 | — |
| `platform_pullback` | 平台突破回踩 | — |
| `perfect_b1_fit` | 「完美 B1」指纹拟合度 | v0.50 停加分，贴合列展示 |
| `s_shape` | S 形态综合分（S**） | R2 证伪无 alpha，展示列（v0.50 移出分层） |

### C. 对照 / 研究调试（不进 live）

| id | 名称 | status | 为什么不在 live（证据出处） |
|---|---|---|---|
| `baseline` | 对照基线：任何 as-of 日都判「可买」 | active | **必须保留**——所有进场信号的对照臂（R1） |
| `kdj_j` | 当日 KDJ 的 J 值（纯特征） | needs_work | 同号率仅 50%，不稳定（R3） |
| `low_vol` | 低波动因子（low-vol anomaly） | needs_work | 三窗对照 rally22/ytd26 跑输、无跨窗稳健性 ⇒ 不再研究（R2 第 16 条，v0.70） |
| `momentum` | 动量因子（12-1 类） | needs_work | 三窗对照 rally22/bull2425 垫底、无跨窗稳健性 ⇒ 不再研究（R2 第 16 条，v0.70） |
| `alpha_pvcorr` | Alpha#6 类：价量负相关 | needs_work | 三窗对照仅 bull2425 跑赢、无跨窗稳健性 ⇒ 不再研究（R2 第 16 条，v0.70） |
| `alpha101` | Alpha#101：进场 K 日内实体强度 | needs_work | 判别层过线但净值终审未过；2025 窗明确输（R2） |
| `mcap` | 小市值选择器 | needs_work | 判别层过线、净值终审惨败；止损把下跌端对称兑现（R2） |
| `reversal_quality` | 反转K 成色分（0–4） | needs_work | 稳健负预测；口径已与 live 默认值一致（对称 ±2%），刻意不跟随 env（R2） |
| `reversal_quality_inv` | 反转成色**反向**选择器 | needs_work | 样本内大胜、含退市跨年翻转；归因未分离（R2） |
| `b1_pullback_fit` | 买弱指纹 | needs_work | recall 100% 但期望 −0.42%/笔，劣于无差别进场 +0.96%（R2） |
| `sector_mainstream` | 主线板块族密度 | candidate | 准确的「窗口主线指纹」（归因工具），但「跟随主流」机械规则不成立（R2）；主线指纹节 v0.80 删除后 live 无引用，研究侧 `aggregate` 保留 |

**QN 批（骑牛登山体系，v0.194，8 个，全部 untested / none / debug）**：
规则出处与转译口径见 [`../qn/10_factor_implementations.md`](../qn/10_factor_implementations.md)，
盈利验证走 R31 预注册（跑数前）——过线前一律不得进 live。

| id | 名称 | 入场转译 |
|---|---|---|
| `qn_ma25_state` | MA25 多空分界 | 线上缩量阴线 |
| `qn_volume_surge_cut` | 倍量切起爆K线 | 阳线倍量×2 上穿 MA5/MA10 |
| `qn_three_red` | 三线红（日/周/月 MACD） | 三线全红当日 |
| `qn_macd_bar_shift` | 买小绿/卖小红 | 绿柱连缩+不破前低 |
| `qn_kdj_neg_day` | KDJ J 负值计数 | 第 3/5 天或 KD20 金叉（⚠️ 与 B1 J<13 不同口径） |
| `qn_adx_extreme` | DMI ADX≥60 极端位 | 极端位+MACD 底背离 |
| `qn_box_target` | 1.3 系数箱体目标位 | 研究约定转译（半格×1.15 上方、未进目标区） |
| `qn_ma144_launch` | 日线翻倍四要素 | 144 上翘+回踩±10%+MACD 水上+过左风 |
| `qn_ma_converge` | 均线收拢发散（v0.195 第二批） | 四线粘合后首次放量向上发散 |
| `qn_bullish_engulf` | 阳包阴/单阳包（v0.195 第二批） | 实体包覆前阴+上穿 MA5/MA10+量略大 |
| `qn_weekly180_setup` | 180 周线大悬空（v0.195 第二批） | 四要素全中（低频大波段，需 ~900 根日 K） |
| `qn_shrink_limit_up` | 缩量涨停板（v0.195 第二批） | 涨停+缩量 0.5~0.7×+前序放量阴+贴均线 |

⚠️ 没有 "falsified" 这一档是刻意的（owner：不要随便证伪）——needs_work = 「按现有证据
不可用，但证据本身待重跑」。

## 新增因子

放一份 `lower_snake.md`（因子无阅读顺序，故**不编号**），在注册表 `factors` 段登记
`used_by`。若某因子被验证后要进 live，应由主策略的规则文档**转译**并回测，
不要让代码直接读因子文档。
