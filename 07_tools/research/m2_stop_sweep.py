#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""M2 机制类改进扫描：分组跑对照并自动判定。

## 为什么要分组（2026-08-04 修正）

第一轮判定犯了一个口径错误：**拿累计 R 跨 `stop_mode` 比较**。

    R = ret / risk_frac

基准用 `stop_mode="low"`（买入当日最低价），实测 `risk_frac` **中位仅 0.65%**；
换成 `--stop-pct 12` 后固定 12%，**分母大了 18 倍**，R 自然崩。于是
「胜率 18%→51.2%、期望 +0.43%→+1.42%」这组明显更好的结果，被「累计 R 从 332 掉到 135」
判成了否决。

更要紧的是：基准那 332R **本来就不可实现**——按风险定额需要 `1%/0.65% = 154%` 仓位，
实际被 `max_pos` 削到 20%，兑现不了 87%。`_R_RISK_FLOOR`（2% 地板）就是这个问题的补丁。
而 0.65% 的止损空间在真实盘面根本执行不了，A 股日内波动轻易打掉。

所以本脚本按 **stop_mode 分组**：

    组内（同一 R 口径）  比 expectancy_R / total_R / 大赢家笔数
    跨组（不同 R 口径）  只比 expectancy / win_rate / payoff_ratio / 盈亏平衡 margin
    组合级              只比 total_return / CAGR / max_drawdown（R 完全不适用）

## 组合级为什么会「逐笔正期望、组合亏损」

受控实验定位到根因是**相关亏损**：B1 是超卖买入，市场普跌时全市场 `J<13` 同时触发，
随后市场继续跌 ⇒ 同批持仓一起亏。同一份幂律收益序列：

    信号聚集、**无**相关亏损 c5×20%(敞口100%)   +55.9%   回撤 6.7%
    信号聚集、**有**相关亏损 c5×20%(敞口100%)   −37.8%   回撤 47.0%
    同上 c2×20%(敞口40%)                        −16.4%   回撤 21.9%
    同上 c5×5% (敞口25%)                        −11.9%   回撤 15.2%
    同上 c20×5%(敞口100%)                       −54.3%   回撤 56.3%

**决定因素是总敞口，不是持仓数量**（c20×5% 与 c5×20% 同为 100% 敞口，同样惨）。
分散持仓数对高相关信号无效。

两个应对手段本仓库早就有、但第一轮没用：
  · `--amv-long-only`  避开普跌期（相关亏损的来源）
  · `--top-n`          横截面按 score 择优，替代「先到先得」
    （执行率仅 22% 时，先到先得等于抽签命中大赢家）

用法：
    uv run python 07_tools/research/m2_stop_sweep.py --sample 300      # 小样本试跑
    uv run python 07_tools/research/m2_stop_sweep.py                   # 全部
    uv run python 07_tools/research/m2_stop_sweep.py -j 6              # 6 进程并行
    uv run python 07_tools/research/m2_stop_sweep.py --only B_stop_pct # 只跑一组
    uv run python 07_tools/research/m2_stop_sweep.py --report-only     # 只重出报表
    uv run python 07_tools/research/m2_stop_sweep.py --cross-window    # 2022-2024 复核

## 为什么慢，以及怎么快（2026-08-05）

串行跑 25 个方案 = **25 次**「读 1000 只票的 vipdoc → 逐票算前复权 → 逐 bar 评估」。
数据加载与前复权的结果对所有方案**完全相同**，却被重复做了 25 遍；同时 CPU 只用一核。

已落地的三条（互相叠加）：

  ① `--jobs N` 并行。方案之间无共享状态（各写自己的结果文件）⇒ 天然可并行。
     ⚠️ 先用 `-j 1` 跑一个方案把 `data/market/xdxr/` 权息缓存焐热，再开并行：
     缓存冷时前复权要经通达信协议逐票取权息，N 个进程各开一条连接可能被限流。
     ⚠️ **并行会把内存乘 N**，见下方「OOM」。
  ② C 组 8 个方案只做 **1 次**真回测。它们的回测参数与 B 组 `pct_12`/`pct_12_amv`
     完全相同，差异全在资金曲线层（max_concurrent / max_pos / risk_pct / top_n），
     而资金曲线模拟是毫秒级。靠 `backtest_factors --from-trades` 跨组复用逐笔，
     复用前核对 `trades_signature`，口径不一致直接非零退出，失败自动退回全量回测。
     只剩 `--top-n` 那条必须自己跑（collect_all，逐笔是未去重全候选）。
     **整轮 25 次回测 → 18 次**。
  ③ `--data-source qlib` 可跳过前复权。tdx 路径要逐票读 xdxr 权息、自算因子；
     S_DATA 的 qlib bundle **已是前复权**，纯文件读取。它同时**含退市股**
     （point-in-time 宇宙）⇒ 顺带去掉幸存者偏差。代价见下方「数据源」。
~~还没做、需要改 backtest_factors 主循环的一条（收益最大）：④ 把「方案外循环 /
股票内循环」翻过来~~ —— **已被实测否掉**：

## 实测：瓶颈是评估，不是加载（2026-08-05 owner 实测）

    [TIME] 加载(含前复权) 8s / 评估 1238s（加载占 1%，1000 只票）

**加载只占 1%**（8 秒），99% 在 `evaluate_trades` 的逐 bar 评估。于是：

- 第 ④ 条「股票外循环」**彻底作废**——把加载从 17 次降到 1 次，总共只省 8 秒。
  我原先以为「重复加载 25 遍」是主因，实测证明那是错的。
- 第 ③ 条切 qlib 的**加速**价值也随之归零（它省的是前复权，也在那 8 秒里）；
  切 qlib 的理由回到**去幸存者偏差**这一个。
- 真正有效的只剩两条：
  · **`--jobs N` 并行** —— 99% CPU-bound ⇒ 近线性加速。单方案 ~1247s，
    `-j 6` 可把 35 个方案从约 12 小时压到 2 小时上下。**这是目前唯一的实用加速手段。**
  · **向量化 `evaluate_trades`** —— 1000 只 × 500 根 ≈ 50 万次 as-of 评估，
    每次约 2.5ms。要动核心逻辑，风险高（会改结果），先不碰。

## OOM Kill（2026-08-05）

`research/R17_infra_tooling.md`「全市场 OOM」早有记录，`--top-n`(collect_all) 大样本尤重：
非 collect_all 时 `i = tr["exit_idx"] + 1`（跳到出场后），collect_all 是 `i += step`
（step=1 ⇒ **每根 K 线**都可能出一条候选）⇒ 逐笔条数高一个量级。
**被 kill 掉的方案在报表里只是少一行**，比跑得慢糟得多。三道措施：

  · `--jobs` 按可用内存**自动收敛**（`_cap_jobs`，预算见 `MEM_PER_JOB_MB`）
  · collect_all 方案**单独串行**，不与别人抢内存（`_is_heavy`）
  · `backtest_factors` 每轮打 `[MEM] 峰值 XXXMb / N 笔`，据此校准 `MEM_PER_JOB_MB`；
    落盘改流式（`write_json_stream`，不再先拼出整个 JSON 字符串），复用路径不重写逐笔

## 数据源：tdx vs qlib（2026-08-05）

默认 `--data-source tdx` = 读**本地**通达信 vipdoc 的 `.day` 二进制（不是联网取行情）。
但有两处仍会联网：① 某只票本地读不到时回退在线 bars；② 前复权要 xdxr 权息，
`data/market/xdxr/` 缓存没有就经通达信协议取。

| | tdx（默认） | qlib / csv（S_DATA） |
|---|---|---|
| 来源 | 本地 vipdoc `.day` | Q_DATA/CSV_DATA bundle |
| 宇宙 | **只含当前挂牌股** ⇒ 有幸存者偏差 | **含退市股**（point-in-time）⇒ 可去偏 |
| 复权 | 逐票算前复权（最慢且唯一要联网的环节） | **已是前复权** ⇒ 完全跳过 |
| 覆盖 | 到最新交易日 | 1999-11→**2026-02**，且 2020-09-28→2021-07-30 缺约 10 个月 |

⚠️ 换数据源=换宇宙，结果与之前几轮**不可比**，所以数据源已进文件名指纹
（`_fingerprint`），两批结果不会被混着汇总。要换就整轮换。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import time
from typing import Any, Optional

BASE = pathlib.Path(__file__).resolve().parents[2]
if str(BASE / "07_tools") not in sys.path:
    sys.path.insert(0, str(BASE / "07_tools"))

from paths import LOGS  # noqa: E402

SCRIPT = BASE / "07_tools" / "research" / "backtest_factors.py"
OUTDIR = LOGS / "m2_sweep"

DEFAULT_SAMPLE = 1000           # 样本股票数默认值（300 样本实测不可靠，见模块文档）
# 单个方案子进程的内存预算（MB）。保守值：1000 只票流式加载 + 逐笔 list + 落盘。
# 实测数字看 backtest_factors 每轮打的 `[MEM] 峰值 XXXMb`，据此调这个常量。
# 这个数只用于**按可用内存收敛 --jobs**——OOM Kill 是这套回测的老问题。
# 2026-08-09 校准（TODO #19）：R4 批跑 16 格全量实测峰值最大 282MB
# （sector 腿 229–282MB 最高；m2 s3000 实测 93MB），归一 22–60MB/千只，
# 峰值不随只数线性增长（加载流式）。1200 → 400（对最大观测留 ~1.4×）。
# 未测盲区：--cross-window（count 1500，3 倍 K 线）与 collect_all/--top-n 重活
# 方案无 [MEM] 记录 —— 不再往下压的原因。
MEM_PER_JOB_MB = 400

MIN_EXPECTANCY_GAIN = 0.02      # 组内：expectancy_R 至少提升 2%
MAX_AVG_WIN_DROP = 0.05         # 均盈跌幅超 5% 即判「削大赢家」
BIG_WIN_THRESHOLD = 0.20        # ret > +20% 记为大赢家

# 出场/入场分类：**按参数语义判，不按笔数判**（2026-08-05 修）
#
# 第一版用「笔数与基准相差 <5%」当判据。它对 `--trail`（1294→1298）成立，但对
# **止损距离**类参数不成立：非重叠去重下（backtest_factors.py:1562，
# `i = tr["exit_idx"] + 1`）止损越紧 → 离场越早 → 后续还能再进场 ⇒ **笔数系统性增加**。
# 实测 300 样本 pct_05=364 笔 vs pct_12=342 笔，差 6.4% 已越过 5% 线 ⇒ pct_05 会被
# 判成「入场类」、去过「大赢家占比不降」——正是 6785724 刚修掉的那类误否。
#
# 而方案改的是入场还是出场，`GROUPS` 里的参数**本来就写着**，不需要从数据反推。
ENTRY_SIDE_FLAGS = {                 # 改变**信号集**：被筛掉的收益永久消失
    "--amv-long-only", "--entry-filter", "--sector-gate", "--top-n",
    "--max-signals-per-code", "--bbi-consec-entry",
}
EXIT_SIDE_FLAGS = {                  # 只改**离场时点/仓位**：信号集不变
    "--breakeven", "--trail", "--stop-mode", "--stop-pct", "--stop-trigger",
    "--stop-tick-buffer", "--cost-zone-bars", "--cost-zone-pct",
    "--scale-out", "--time-stop", "--bbi-consec",
}

# 改动**止损距离**的参数 ⇒ 改动 R 的分母 ⇒ **R 不再可比**（2026-08-05 修）
#
# `backtest_factors.py:1294` 是 `stop = entry * (1 - stop_pct/100)`，
# 所以 pct 模式下 `risk_frac` **恒等于 stop_pct**，而 `R = ret / risk_frac`：
#
#     pct_05  期望 +0.67%  risk 5%   期望R 0.134   累计R 157.5
#     pct_08  期望 +0.64%  risk 8%   期望R 0.080   累计R  90.6
#     pct_12  期望 +0.63%  risk 12%  期望R 0.052   累计R  57.7
#
# 期望R 就是 `期望% ÷ stop_pct`。「pct_05 累计R +73.8%」里 **1.6 倍纯粹是分母 8%/5%**，
# 真实期望率 0.67% vs 0.64% 几乎持平。上一轮「pct_05 累计R 是 pct_12 的 2.76 倍」
# 同理——2.4 倍来自分母。这就是本模块开头声明要防的「跨 R 口径比 R」，
# 只是它发生在 **B 组内部**，而分组只按 stop_mode 分，没按 stop_pct 分。
#
# `--stop-tick-buffer` 同样改分母：stop 从当日最低再往下 N 个价位，
# 而 A 组 risk_frac 中位仅 0.65%，3 个价位（0.03 元）在 10 元股上就是 0.3%
# ⇒ 分母可能多出近 50%，R 被系统性压低。实测 tick_buffer_3 期望 +0.52% 明显优于
# 基准 +0.39%，却因累计R -3.0% 被否——**那个否决是分母造成的**。
#
# 相比之下 `--breakeven` / `--trail` / `--cost-zone-*` / `--stop-trigger` 只改**离场**，
# risk_frac 在 1297 行就按**初始**止损算完了 ⇒ 分母不变，R 可比。
R_DENOM_FLAGS = {"--stop-mode", "--stop-pct", "--stop-tick-buffer"}

# 每组共享的 stop 口径 → 组内 R 可比；跨组只比收益率
GROUPS: dict[str, dict[str, Any]] = {
    "A_stop_low": {
        "desc": "stop_mode=low（买入当日最低价，risk_frac 中位 0.65%）",
        "common": [],
        "baseline": "00_baseline",
        "runs": {
            "00_baseline": [],
            "be_03": ["--breakeven", "0.03"],
            "be_05": ["--breakeven", "0.05"],
            "be_08": ["--breakeven", "0.08"],
            "trail_08": ["--trail", "0.08"],
            "trail_12": ["--trail", "0.12"],
            "trail_18": ["--trail", "0.18"],
            "trigger_intraday": ["--stop-trigger", "intraday"],
            # 止损余量上探（2026-08-05）：tick_buffer_3 期望% **+33.3%**、margin
            # +2.6→+3.4pp，是 A 组最有效的单项之一 ⇒ 沿这个方向再探。
            # B1_w.pdf 说的是「或向下 3-5 个价位」，5 是它给的上界，8 用来看斜率是否续。
            # ⚠️ 它改 risk_frac ⇒ 报表标 [出场·R口径变]，按期望%/margin 判。
            "tick_buffer_3": ["--stop-tick-buffer", "3"],
            "tick_buffer_5": ["--stop-tick-buffer", "5"],
            "tick_buffer_8": ["--stop-tick-buffer", "8"],
            # 叠加：目前全是单变量扫描，而 trail_08(累计R +43.1%，最强出场) 与
            # tick_buffer_3(期望% +33.3%，最强初始止损余量) **机制正交**——一个改移动
            # 止盈、一个改初始止损位。正交不等于可叠加（可能互相抵消），必须实测。
            "trail_08_tick3": ["--trail", "0.08", "--stop-tick-buffer", "3"],
            "cost_zone_3": ["--cost-zone-bars", "3"],
            # 分批止盈**对照臂**（2026-08-05）：`--scale-out 0.5` 写在 `_base_args` 里，
            # 每个方案都开着 ⇒ 这轮扫描对「分批止盈有没有用」**零信息**（没有对照）。
            # M1（2026-08-04）验过 0.5 vs 0：期望 +16%、累计R +10%、胜率不变——
            # 但那是**旧止损口径**（盘中止损，胜率 18%、盈亏比 5.525、基准 302R），
            # 之后 f156a0a 改成收盘止损，现在胜率 29.8%、盈亏比 2.678、基准 250.5R。
            # 而 ∂E/∂b = p ⇒ **p 从 18% 升到 29.8%，盈亏比杠杆变得更值钱**；
            # 但能触发 `+scaled` 的交易占比同时也变了。两个方向不确定 ⇒ 必须重验。
            # argparse 后出现的值覆盖前面的，所以这里给 0 能盖掉 _base_args 的 0.5。
            "scale_out_0": ["--scale-out", "0"],
            "scale_out_03": ["--scale-out", "0.3"],
            "scale_out_08": ["--scale-out", "0.8"],
            "amv_long_only": ["--amv-long-only"],
        },
    },
    "B_stop_pct": {
        "desc": ("stop_mode=pct（固定百分比，止损可执行）⚠️ R 与 A 组不可比，"
                 "**组内不同 stop_pct 之间也不可比**（risk_frac 恒等于 stop_pct）"),
        "common": ["--stop-mode", "pct"],
        # 基准取**中间档** pct_08：本组是参数扫描，没有「未改动的原始方案」可当基准。
        # 原先取 pct_12，而 3932190/6785724 已认定它是三档里最差的（组内累计R：
        # pct_05 157.7 / pct_08 83.0 / pct_12 57.2，pct_05 是 pct_12 的 2.76 倍）。
        # 拿最差档当基准，✅/❌ 会退化成「比最差的好」⇒ 几乎全部通过，判定失去区分力。
        "baseline": "pct_08",
        "runs": {
            # 补探下界（2026-08-05）：实测 5%→8%→12% 的期望% 是 0.67/0.64/0.63，
            # **单调但极平**（相差 6%），而 margin 是 +3.8/+3.3/+3.1pp ⇒ 越紧略好。
            # 下界没探到，加 3%/4% 两档。⚠️ 别拿累计R 读它们：risk_frac 恒等于 stop_pct，
            # 3% 档的期望R 天然是 8% 档的 2.7 倍（见 R_DENOM_FLAGS 注释），
            # 报表会标 [出场·R口径变] 并改用 期望%/margin 判。
            # ⚠️ 另一处要留意：成本 25bps 固定，止损越紧则**成本占风险的比例越高**
            # （3% 档：0.25/3 = 8.3%），到某一档收益会被成本吃掉——这正是要找的下界。
            "pct_03": ["--stop-pct", "3"],
            "pct_04": ["--stop-pct", "4"],
            "pct_05": ["--stop-pct", "5"],
            # ⚠️ **最优档一直缺择时变体**：跨组表前三名全是 amv 方案（期望% +2.72~+3.27、
            # margin +11.5~+16.0pp），但它们配的是 8% / 12% 止损；而 5% 才是期望% 最高的
            # 档位（+0.67 vs +0.64 / +0.63，且 4% 以下崖式下滑 -42%）。
            # 「最优止损档 × 最强择时」这个组合从来没跑过 —— 很可能是全场最优。
            "pct_05_amv": ["--stop-pct", "5", "--amv-long-only"],
            # 「可执行的止损(5%) × 最强移动止盈(trail 8%)」：trail_08 在 A 组累计R +43.1%，
            # 但 A 组止损(risk_frac 中位 0.65%)实盘执行不了；换到 5% 固定止损上才有意义。
            "pct_05_trail_08": ["--stop-pct", "5", "--trail", "0.08"],
            "pct_08": ["--stop-pct", "8"],
            "pct_12": ["--stop-pct", "12"],
            "pct_12_amv": ["--stop-pct", "12", "--amv-long-only"],
            "pct_12_amv_cz3": ["--stop-pct", "12", "--amv-long-only",
                               "--cost-zone-bars", "3"],
            "pct_08_amv": ["--stop-pct", "8", "--amv-long-only"],
        },
    },
    "C_portfolio": {
        "desc": "组合级资金曲线（只看 total_return / CAGR / max_drawdown）",
        "common": ["--stop-mode", "pct", "--stop-pct", "12", "--portfolio"],
        "baseline": None,
        # trades 复用（值写作 "组/方案"，可跨组）：本组 8 个方案的**回测参数**与
        # B 组的 pct_12 / pct_12_amv **完全相同**——C 组 common 里的 `--portfolio`
        # 和 extra 里的 max_concurrent/max_pos/risk_pct 都只改资金曲线，不改 trades。
        # 所以 C 组只剩 **1 次真回测**：`--top-n` 那条（走 collect_all，逐笔是未去重
        # 全候选，与去重后完全不同口径，必须自己跑）。
        # 8 次回测 → 1 次，整轮 25 次 → 18 次。复用时 backtest_factors 会核对
        # trades_signature，口径不一致直接失败（不静默算），失败自动退回全量。
        "reuse": {
            "pf_c5_p20": "B_stop_pct/pct_12",
            "pf_c3_p20": "B_stop_pct/pct_12",
            "pf_c2_p20": "B_stop_pct/pct_12",
            "pf_c5_p05": "B_stop_pct/pct_12",
            "pf_c2_p20_amv": "B_stop_pct/pct_12_amv",
            "pf_c5_p05_amv": "B_stop_pct/pct_12_amv",
            "pf_top3_c5_p05_amv": "C_portfolio/pf_top2_c2_amv",
        },
        "runs": {
            # 第一轮的两组（敞口 100% / 60%），留作对照
            "pf_c5_p20": ["--max-concurrent", "5", "--max-pos", "20", "--risk-pct", "1.0"],
            "pf_c3_p20": ["--max-concurrent", "3", "--max-pos", "20", "--risk-pct", "2.0"],
            # 低敞口
            "pf_c2_p20": ["--max-concurrent", "2", "--max-pos", "20", "--risk-pct", "1.0"],
            "pf_c5_p05": ["--max-concurrent", "5", "--max-pos", "5", "--risk-pct", "1.0"],
            # 加择时（避开相关亏损来源）
            "pf_c2_p20_amv": ["--max-concurrent", "2", "--max-pos", "20",
                              "--risk-pct", "1.0", "--amv-long-only"],
            "pf_c5_p05_amv": ["--max-concurrent", "5", "--max-pos", "5",
                              "--risk-pct", "1.0", "--amv-long-only"],
            # 加横截面择优（替代先到先得）
            "pf_top2_c2_amv": ["--top-n", "2", "--max-concurrent", "2", "--max-pos", "20",
                               "--risk-pct", "1.0", "--amv-long-only"],
            "pf_top3_c5_p05_amv": ["--top-n", "3", "--max-concurrent", "5", "--max-pos", "5",
                                   "--risk-pct", "1.0", "--amv-long-only"],
        },
    },
}


WINDOW_COUNT = 1500             # 钉窗口时的 --count：必须**大于窗口内的 K 线根数**，见 _base_args


def _base_args(sample: int, cross: bool, data_source: str = "tdx",
               window: Optional[tuple[str, str]] = None,
               codes_file: Optional[str] = None) -> list[str]:
    a = ["--trade-sim", "--entry-filter", "j_low", "--scorer", "b1_dual",
         "--cost-bps", "25", "--scale-out", "0.5"]
    if codes_file:
        # 钉死宇宙。⚠️ 为什么必需：universe 来自 vipdoc 目录列举，会随通达信下载变动
        # （实测一轮扫描中 5535→5536）；seed 固定没用，**被抽的池子变了**
        # ⇒ sample_codes 抽到另一组 1000 只，各方案宇宙不同。
        a += ["--codes-file", codes_file]
    elif data_source == "tdx":
        # 本地通达信 vipdoc（.day 二进制）。⚠️ 只含**当前挂牌**的票 ⇒ 有幸存者偏差；
        # 且要逐票算前复权（xdxr 权息，缓存冷时还要联网）——全链最慢的一环。
        a += ["--universe-local", "--universe-sample", str(sample)]
    else:
        # S_DATA 的 qlib/csv bundle：**含退市股**(point-in-time)、**已是前复权**
        # ⇒ 去幸存者偏差 + 完全跳过 xdxr。代价：2020-09-28→2021-07-30 有约 10 个月
        # 缺口，且数据到 2026-02 截止。换数据源会改 trades_signature ⇒
        # **与之前几轮结果不可比**，要换就整轮换。
        a += ["--data-source", data_source, "--universe-sdata",
              "--universe-sample", str(sample)]
    if cross:
        # ⚠️ --count 必须加大：默认 500 根从今天往前数，加 --start/--end 只覆盖窗口尾部
        a += ["--start", "2022-01-01", "--end", "2024-12-31", "--count", str(WINDOW_COUNT)]
    elif window:
        # 钉死 K 线窗口。⚠️ **必须两端都给 + 放大 --count**，只给 --end 钉不住：
        # `get_ohlcv_table`(local_tdx_data:674) 先做 `df.tail(count)`，
        # `_load_bars_local` 才在之后按 start/end 过滤 ⇒ 文件从 N 根变 N+1 根时，
        # tail(500) 取的是 [N-499, N+1]，end 过滤掉最新那根 ⇒ 只剩 499 根，
        # **且最早那根往前挪了一天**：窗口既缩水又滑动。
        # 两端都给且 count 足够大时，tail 覆盖到 start 之前 ⇒ 窗口完全由日期决定。
        a += ["--start", window[0], "--end", window[1], "--count", str(WINDOW_COUNT)]
    if not (cross or window):
        a += ["--count", "500"]
    return a


def _fp_suffix(cross: bool, data_source: str = "tdx",
               window: Optional[tuple[str, str]] = None,
               pin_universe: bool = False) -> str:
    """指纹里样本量之后的部分。**每个会改变「比的是什么」的开关都要进来。**

    拼装式而不是一串正则分组，是为了以后加开关时 `_collect` 不用同步改正则
    （改漏了就会重演混批事故）。
    """
    s = "" if data_source == "tdx" else f"_{data_source}"
    if window:
        s += "_w" + window[0].replace("-", "") + "-" + window[1].replace("-", "")
    if pin_universe:
        s += "_u"
    if cross:
        s += "_cw"
    return s


def _fingerprint(sample: int, cross: bool, data_source: str = "tdx",
                 window: Optional[tuple[str, str]] = None,
                 pin_universe: bool = False) -> str:
    """结果文件的参数指纹。

    ⚠️ **必须含样本量**：第一版文件名只有 `{组}__{方案}.json`，owner 先跑 300 样本、
    再跑 1000 样本时，300 的旧文件被 `[SKIP]` 直接复用 ⇒ 汇总表里 ~400 笔的方案与
    ~1300 笔的基准混在一起比，A 组一半方案的判定全部无效。

    ⚠️ **也必须含数据源**：tdx（本地 vipdoc，只有当前挂牌股）与 qlib（S_DATA，含退市股、
    已前复权）是**两个不同的宇宙**，笔数与收益都不可比。

    ⚠️ **窗口与宇宙钉死也要进来**：钉了窗口/宇宙的批次与没钉的不是同一件事
    （前者可复现、后者随通达信下载漂移），混着汇总同样无效。
    `tdx` / 未钉 不加后缀，保持已有文件名不失效。
    """
    return f"s{sample}" + _fp_suffix(cross, data_source, window, pin_universe)


def _run(group: str, name: str, extra: list[str], sample: int, cross: bool,
         force: bool, capture: bool = False,
         data_source: str = "tdx", window: Optional[tuple[str, str]] = None,
         codes_file: Optional[str] = None) -> tuple[str, Optional[pathlib.Path], str]:
    """跑一个方案。返回 ``(方案标识, 结果文件或 None, 待打印的日志)``。

    ``capture=True``（并行时）把子进程输出收进字符串，等该方案跑完整块打印——
    否则 8 个进程的进度会逐行交错，完全没法读。串行时不捕获，保持实时进度。
    """
    tag = f"{group}/{name}"
    OUTDIR.mkdir(parents=True, exist_ok=True)
    fp = _fingerprint(sample, cross, data_source, window, bool(codes_file))
    out = OUTDIR / f"{group}__{name}__{fp}.json"
    if out.exists() and not force:
        return tag, out, f"[SKIP] {out.name}"
    extra = list(extra)
    note = ""
    src_ref = (GROUPS[group].get("reuse") or {}).get(name)
    if src_ref:
        src_group, src_name = (src_ref.split("/", 1) if "/" in src_ref
                               else (group, src_ref))
        src = OUTDIR / f"{src_group}__{src_name}__{fp}.json"
        if src.is_file():
            extra += ["--from-trades", str(src)]
            note = f"（复用 {src_ref} 的 trades，只跑资金曲线）"
        else:
            # 源还没跑（比如 --only 单独跑了这个方案）⇒ 老老实实全量回测。
            # 全量回测永远是正确的，只是慢；不要为了省时间去猜一份 trades。
            note = f"（源 {src_ref} 结果不存在，改为全量回测）"
    log: list[str] = []

    def _say(line: str) -> None:
        """串行时实时打印，并行时收进 log 等整块打印。两边都只出现一次。"""
        if capture:
            log.append(line)
        else:
            print(line)

    if capture:
        # ⚠️ 并行波把子进程输出全收进字符串、等方案跑完才整块打印（否则 6 路进度逐行
        # 交错没法读）。代价是**「在跑」与「已死」在日志上长得一模一样，且要等一整个
        # 方案（3000 只约 60 分钟）才能区分** —— owner 实测因此以为卡住。
        # 所以启动时先打一行短心跳，直接进 stdout（单行交错无妨）。
        print(f"[START] {tag}  {time.strftime('%H:%M:%S')}", flush=True)

    def _exec(args: list[str]) -> tuple[int, float]:
        cmd = ([sys.executable, str(SCRIPT)]
               + _base_args(sample, cross, data_source, window, codes_file)
               + GROUPS[group]["common"] + args + ["--out", str(out)])
        t0 = time.time()
        if capture:
            r = subprocess.run(cmd, cwd=str(BASE), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors="replace")
            if r.stdout:
                log.append(r.stdout.rstrip())
        else:
            r = subprocess.run(cmd, cwd=str(BASE))
        return r.returncode, time.time() - t0

    _say(f"[RUN ] {tag}: {' '.join(extra) or '(组基准)'}{note}")
    rc, dt = _exec(extra)
    if rc != 0 and "--from-trades" in extra:
        # 复用失败（多半是 trades 口径核对不过：扫描期间通达信又下了新数据、
        # universe 变了 ⇒ codes_digest 不同）。全量回测永远正确，只是慢 ⇒ 自动退回，
        # 不让「省时间」变成「少一个方案」——少一行的报表正是本脚本一直在防的失效。
        plain = [x for i, x in enumerate(extra)
                 if x != "--from-trades" and extra[i - 1] != "--from-trades"]
        _say(f"[RETRY] {tag}: 复用失败，退回全量回测")
        rc, dt = _exec(plain)
    if rc != 0:
        _say(f"[FAIL] {tag} exit={rc} ({dt:.0f}s)")
        return tag, None, "\n".join(log)
    _say(f"[DONE] {tag} {dt:.0f}s")
    return tag, (out if out.exists() else None), "\n".join(log)


def _avail_mem_mb() -> Optional[float]:
    """可用内存（MB）。取不到返回 None（那就只警告、不自动降并行度）。"""
    try:                                                          # Linux
        for ln in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                return float(ln.split()[1]) / 1024.0
    except Exception:                                             # noqa: BLE001
        pass
    try:                                                          # Windows
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        ms = _MS()
        ms.dwLength = ctypes.sizeof(_MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):  # type: ignore[attr-defined]
            return ms.ullAvailPhys / (1024.0 ** 2)
    except Exception:                                             # noqa: BLE001
        pass
    return None


def _is_heavy(group: str, name: str) -> bool:
    """内存重活：`--top-n` 走 `collect_all`，逐笔是**未去重的全候选**。

    非 collect_all 时 `i = tr["exit_idx"] + 1`（跳到出场后），collect_all 是
    `i += step`（step=1 ⇒ **每根 K 线**都可能产生一条候选）⇒ 逐笔条数高一个量级。
    `research/R17_infra_tooling.md` 早就记着「全市场 OOM，`--top-n`(collect_all) 大样本仍重」。
    这类方案不与别人并行，单独串行跑。
    """
    return "--top-n" in _flag_set((GROUPS.get(group, {}).get("runs") or {}).get(name, []))


def _cap_jobs(jobs: int, n_tasks: int) -> int:
    """按可用内存收敛并行度。

    ⚠️ **并行会把内存乘 N**，而这套回测本来就常被 OOM Kill
    （`research/R17_infra_tooling.md`「全市场 OOM」）。被 kill 掉的方案在报表里只是少一行，
    比跑得慢糟得多 ⇒ 宁可少开几路。
    `MEM_PER_JOB_MB` 是保守估计；跑完看 `backtest_factors` 打的 `[MEM] 峰值 XXXMb`
    再按实测调。留 20% 余量给系统与通达信客户端本身。
    """
    jobs = max(1, min(jobs, n_tasks))
    if jobs == 1:
        return 1
    # 先按 CPU 核数收敛：实测 99% 时间在 `evaluate_trades` 的逐 bar 评估
    # （`[TIME] 加载 8s / 评估 1238s`）⇒ 纯 CPU-bound，进程数超过核数只会互相抢时间片，
    # 还会挤掉 TdxW（它要服务 xdxr 权息请求）。
    ncpu = os.cpu_count() or 1
    if jobs > ncpu:
        print(f"[INFO] --jobs {jobs} 超过 CPU 核数 {ncpu}，降到 {ncpu}"
              f"（评估是纯 CPU-bound，超订不会更快）")
        jobs = ncpu
    avail = _avail_mem_mb()
    if avail is None:
        print(f"⚠️ 读不到可用内存，按 {jobs} 路并行跑。每路约需 {MEM_PER_JOB_MB}MB，"
              f"OOM 风险自行判断（跑完看 [MEM] 峰值）")
        return jobs
    safe = max(1, int(avail * 0.8 // MEM_PER_JOB_MB))
    if safe < jobs:
        print(f"⚠️ 可用内存 {avail:.0f}MB，按每路 {MEM_PER_JOB_MB}MB 估算最多 {safe} 路，"
              f"已把 --jobs {jobs} 降到 {safe}（被 OOM kill 掉的方案在报表里只是少一行，"
              f"比跑得慢糟得多）")
        return safe
    print(f"[INFO] 可用内存 {avail:.0f}MB，{jobs} 路并行约需 "
          f"{jobs * MEM_PER_JOB_MB}MB")
    return jobs


def _run_all(todo: list[tuple[str, str, list[str]]], sample: int, cross: bool,
             force: bool, jobs: int, data_source: str = "tdx",
             window: Optional[tuple[str, str]] = None,
             codes_file: Optional[str] = None) -> None:
    """跑完 todo 里的全部方案，最后汇总失败项。

    ## 为什么可以并行

    每个方案是**独立的子进程**，只读同一份本地 vipdoc/xdxr、各写自己的结果文件，
    彼此无共享状态 ⇒ 天然可并行。串行跑 25 个方案时 CPU 只用一核，而每个方案都要
    把 1000 只票重新读盘 + 重算前复权 + 逐 bar 评估。

    ⚠️ **先用 `--jobs 1` 跑一遍把 xdxr 权息缓存焐热**（`data/market/xdxr/`）。
    缓存冷时前复权要经通达信协议逐票取权息，8 个进程同时取会各开一条连接、
    可能被服务端限流甚至拒连——那时并行不会更快，只会一起失败。

    ## 失败必须汇总

    原先失败只打一行 `[FAIL]` 就继续，25 个方案跑几小时那行早滚没了，
    报表里只是**少一行**。这里最后统一列出。
    """
    fails: list[str] = []

    def _one_wave(items: list[tuple[str, str, list[str]]], wave: str,
                  wave_jobs: int) -> None:
        if not items:
            return
        if wave_jobs <= 1:
            for g, n, e in items:
                tag, path, log = _run(g, n, e, sample, cross, force, False,
                                      data_source, window, codes_file)
                if log:
                    print(log)
                if path is None:
                    fails.append(tag)
            return
        from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415
        # 线程只是在等子进程，真正的并行度由子进程数决定 ⇒ 用线程池最省事
        with ThreadPoolExecutor(max_workers=wave_jobs) as ex:
            futs = {ex.submit(_run, g, n, e, sample, cross, force, True,
                              data_source, window, codes_file): f"{g}/{n}"
                    for g, n, e in items}
            done = 0
            for f in as_completed(futs):
                tag, path, log = f.result()
                done += 1
                print(f"\n{'─' * 70}\n[{wave} {done}/{len(items)}] {tag}\n{'─' * 70}")
                print(log)
                if path is None:
                    fails.append(tag)

    derived = [(g, n, e) for g, n, e in todo
               if n in (GROUPS[g].get("reuse") or {})]
    rest = [(g, n, e) for g, n, e in todo
            if n not in (GROUPS[g].get("reuse") or {})]
    heavy = [t for t in rest if _is_heavy(t[0], t[1])]
    light = [t for t in rest if not _is_heavy(t[0], t[1])]
    # 三波，顺序有讲究：
    #   轻活并行 → 重活(collect_all)**单独串行**，不与别人抢内存
    #   → 复用波串行（要等源产出；且它要把源文件整份读进内存，本身也不轻）
    _one_wave(light, "主", _cap_jobs(jobs, len(light)))
    if heavy:
        print(f"\n[INFO] {len(heavy)} 个 collect_all 方案单独串行"
              f"（--top-n 逐笔是未去重全候选，内存高一个量级，见 _is_heavy）")
    _one_wave(heavy, "重", 1)
    _one_wave(derived, "复用", 1)
    if fails:
        print(f"\n⚠️ **{len(fails)} 个方案失败**：{'、'.join(fails)}")
        print("   报表只在成功的方案之间判定；补跑：--only <方案名>")
        print("   若是被 OOM kill（退出码 137/-9）：降 --jobs、或先单跑重活")


def _load(p: pathlib.Path) -> dict:
    """读一个结果 JSON。

    ⚠️ 键名是 **`trade_summary`**（`backtest_factors.py:2118`），我第一版写成
    `trade_sim`/`summary`/`trade_simulation` 全都对不上，导致 owner 跑完 25 个方案后
    报表生成不出来、只能手工汇总。这里保留多个候选键并做兜底扫描，避免再因键名改动
    静默失效——**读不到就明确报错，不要静默返回空**。
    """
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))  # BOM 容错，见 paths.read_json
    except Exception as e:                                        # noqa: BLE001
        print(f"[WARN] 读不了 {p.name}: {e}")
        return {}
    pf = d.get("portfolio")
    for k in ("trade_summary", "trade_sim", "summary", "trade_simulation"):
        blk = d.get(k)
        if isinstance(blk, dict) and ("expectancy" in blk or "n" in blk):
            s = dict(blk)
            s["_trades"] = d.get("trades") or blk.get("trades") or []
            s["_portfolio"] = pf or blk.get("portfolio")
            return s
    if "expectancy" in d:                                          # 摘要直接在顶层
        s = dict(d)
        s["_trades"] = d.get("trades") or []
        s["_portfolio"] = pf
        return s
    # 兜底：扫一层子字典找带 expectancy 的块（键名再改也能活）
    for k, v in d.items():
        if isinstance(v, dict) and "expectancy" in v:
            s = dict(v)
            s["_trades"] = d.get("trades") or []
            s["_portfolio"] = pf
            print(f"[INFO] {p.name}: 摘要在非预期键 '{k}' 下，已兜底读取")
            return s
    if pf:                                                         # 纯组合级结果
        return {"_trades": [], "_portfolio": pf}
    print(f"[WARN] {p.name}: 找不到交易摘要（顶层键: {sorted(d)[:8]}）")
    return {}


def _big_wins(trades: list) -> int:
    return sum(1 for t in trades
               if isinstance(t, dict) and (t.get("ret") or 0) > BIG_WIN_THRESHOLD)


def _tail_split(trades: list) -> Optional[tuple[float, float]]:
    """把总 R 拆成 ``(尾部R, 非尾部R)``——大赢家（`ret>20%`）贡献 vs 其余全部。

    ⚠️ **原先用比值 `尾部R/总R` 判，方向会反。** 实测基准（1000 样本）：
    总R 250.5，其中尾部 ≈924R、非尾部 ≈**-673R** ⇒ 比值 **369%**。
    `trail_08` 是尾部 924→846（-8%）、非尾部 -673→-488（**少亏 185R**），净 +108R；
    比值 369%→236% 被旧文案读成「收益更依赖中等赢家」，**恰好说反**——
    它其实是「中部失血减少」，正是这个出场机制该干的事。

    比值只在「非尾部为正」时才等于占比；一旦非尾部为负，比值 >1 且**分母越小比值越大**，
    单看它无法区分「尾部变小」和「中部变好」。所以改成两个绝对量分别判。

    返回 None 表示逐笔里没有 r_multiple（`--summary-only` 时会这样）。
    """
    rs = [(t.get("ret") or 0, t.get("r_multiple")) for t in trades
          if isinstance(t, dict) and t.get("r_multiple") is not None]
    if not rs:
        return None
    tail = sum(r for ret, r in rs if ret > BIG_WIN_THRESHOLD)
    nontail = sum(r for ret, r in rs if ret <= BIG_WIN_THRESHOLD)
    return tail, nontail


def _is_exit_side(row: dict, base: dict) -> bool:
    """**兜底**判据：笔数与基准相差 <5% ⇒ 出场类。

    ⚠️ 只在 `GROUPS` 里查不到该方案时使用（手工拷进来的结果文件、改过名的旧文件）。
    正式判据是 `_side_of`——按参数语义判。理由见 `ENTRY_SIDE_FLAGS` 上方注释：
    止损距离会系统性改变笔数，用笔数反推机制类型会把 `pct_05`/`cost_zone_3`
    这类纯出场改动误判成入场类。
    """
    n, bn = row.get("n") or 0, base.get("n") or 0
    if not n or not bn:
        return False
    return abs(n - bn) / bn < 0.05


def _flag_set(args: list[str]) -> set[str]:
    return {a for a in args if isinstance(a, str) and a.startswith("--")}


def _flag_pairs(args: list[str]) -> dict[str, Optional[str]]:
    """``["--stop-pct", "5"]`` → ``{"--stop-pct": "5"}``；开关型 → ``{flag: None}``。

    ⚠️ 必须比**取值**，不能只比 flag 名字：`pct_05` 与基准 `pct_08` 的 flag 名完全相同
    （都是 `--stop-pct`），只比名字会得出「没有差异」⇒ 判不出机制类型 ⇒ 回落到笔数
    启发式 ⇒ 又被误判成入场类。
    """
    out: dict[str, Optional[str]] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if not (isinstance(a, str) and a.startswith("--")):
            i += 1
            continue
        nxt = args[i + 1] if i + 1 < len(args) else None
        if isinstance(nxt, str) and not nxt.startswith("--"):
            out[a] = nxt
            i += 2
        else:
            out[a] = None
            i += 1
    return out


def _side_from_flags(group: str, name: str, base_name: Optional[str]) -> Optional[str]:
    """按参数语义判出场/入场。返回 ``"exit"`` / ``"entry"`` / ``None``（判不出）。

    与基准**逐参数取值**做差：B 组基准自带 `--stop-pct 8`，`pct_05` 的差异是那个
    **取值**变了。先看入场类——`pct_12_amv` 同时改了 `--stop-pct` 又加了
    `--amv-long-only`，它筛掉了信号，必须按入场类的严格判据走。
    """
    meta = GROUPS.get(group)
    if not meta:
        return None
    runs = meta.get("runs") or {}
    if name not in runs:
        return None
    mine = _flag_pairs(runs[name])
    base = _flag_pairs(runs[base_name]) if (base_name and base_name in runs) else {}
    changed = {f for f, v in mine.items() if base.get(f, "\0") != v}
    if changed & ENTRY_SIDE_FLAGS:
        return "entry"
    if changed & EXIT_SIDE_FLAGS:
        return "exit"
    return None


def _side_of(group: str, row: dict, base: dict, base_name: Optional[str]) -> str:
    """该方案是改出场还是改入场——优先参数语义，查不到才回落到笔数。

    为什么要分：「削大赢家」这条判据的初衷是防止**为提高胜率而筛掉大赢家**——
    那些收益会**永久消失**。但出场机制不筛信号（`trail_08` 笔数 1294→1298），
    它只改变离场时点，用「少赚一点尾部」换「多一些赢家」。
    对出场类硬套「大赢家占比不降」，会把累计 R +43% 的方案否掉。
    """
    side = _side_from_flags(group, row.get("name") or "", base_name)
    if side:
        return side
    return "exit" if _is_exit_side(row, base) else "entry"


def _margin(row: dict) -> Optional[float]:
    """安全边际 = 实际胜率 − 盈亏平衡胜率。**跨 R 口径时的主要判据之一**。"""
    be = _breakeven_wr(row.get("payoff"))
    wr = row.get("win")
    if be is None or wr is None:
        return None
    return wr - be


def _same_r_denom(group: str, name: str, base_name: Optional[str]) -> bool:
    """该方案与基准是否**同一个 R 分母口径**。

    `R = ret / risk_frac`，而 risk_frac 由**初始止损位**决定。凡是改动止损距离的参数
    （见 `R_DENOM_FLAGS`）都会改分母 ⇒ 它的 R 与基准的 R 不是一个尺度，
    比较 `期望R` / `累计R` 得到的差异里混着纯算术的分母变化。

    这类方案必须改用**收益率口径**（期望% / margin）判——与跨组表同一个道理。

    `GROUPS` 里查不到时返回 True（保持既有行为）：手工拷进来的文件无法推断参数，
    而误判成「口径不同」会让判定变宽松，比保守判严更危险。
    """
    meta = GROUPS.get(group)
    if not meta:
        return True
    runs = meta.get("runs") or {}
    if name not in runs:
        return True
    mine = _flag_pairs(runs[name])
    base = _flag_pairs(runs[base_name]) if (base_name and base_name in runs) else {}
    for f in R_DENOM_FLAGS:
        if mine.get(f, "\0") != base.get(f, "\0"):
            return False
    return True


def _breakeven_wr(payoff: Optional[float]) -> Optional[float]:
    """盈亏平衡胜率 = 1/(1+b)。它与实际胜率的差就是安全边际。"""
    if not payoff or payoff <= 0:
        return None
    return 1.0 / (1.0 + payoff)


def _collect(cross: bool, sample: Optional[int] = None,
             data_source: str = "tdx", window: Optional[tuple[str, str]] = None,
             pin_universe: bool = False) -> dict[str, list[dict]]:
    """按指纹收集结果。``sample=None`` 时自动取**最大样本量**那一批。

    只汇总同一指纹的文件——混合样本量、混合数据源、混合窗口比较都是无意义的
    （见 `_fingerprint` 注释）。
    """
    suffix = _fp_suffix(cross, data_source, window, pin_universe)
    # 后缀精确匹配（而非逐个正则分组）：加新开关时这里不用改，改漏就会重演混批事故
    pat = re.compile(r"__s(\d+)" + re.escape(suffix) + r"\.json$")
    avail: dict[int, int] = {}
    for p in OUTDIR.glob("*__*__s*.json"):
        m = pat.search(p.name)
        if not m:
            continue
        avail[int(m.group(1))] = avail.get(int(m.group(1)), 0) + 1
    if not avail:
        # 兼容第一版无指纹的文件名，但明确告警
        legacy = list(OUTDIR.glob("*__*.json"))
        if legacy:
            print(f"[WARN] 发现 {len(legacy)} 个**无样本量指纹**的旧结果文件"
                  f"（第一版命名）。它们可能来自不同 --sample，混在一起比较无效。"
                  f"建议删除 artifacts/logs/m2_sweep 后重跑。")
        return {g: [] for g in GROUPS}
    if sample is None:
        sample = max(avail)
        if len(avail) > 1:
            print(f"[INFO] 检测到多个样本量 {sorted(avail)}，"
                  f"只汇总最大的 s{sample}（{avail[sample]} 个方案）。"
                  f"用 --sample 指定其它批次。")
    want = f"__s{sample}{suffix}.json"

    out: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for p in sorted(OUTDIR.glob(f"*{want}")):
        stem = p.name[:-len(want)]
        if "__" not in stem:
            continue
        group, name = stem.split("__", 1)
        if group not in out:
            continue
        s = _load(p)
        if not s:
            continue
        out[group].append({
            "name": name, "n": s.get("n"), "win": s.get("win_rate"),
            "exp": s.get("expectancy"), "expR": s.get("expectancy_R"),
            "totR": s.get("total_R"), "payoff": s.get("payoff_ratio"),
            "avg_win": s.get("avg_win"), "avg_loss": s.get("avg_loss"),
            "hold": s.get("avg_holding"), "big": _big_wins(s.get("_trades") or []),
            "reasons": s.get("exit_reasons") or {}, "pf": s.get("_portfolio"),
            # exit_reasons 只有 {n, avg_return}，没有 R ⇒ 从逐笔自算，
            # 因为可加的是 sum_r 而非均收（见 _reason_stats）
            "reasons_calc": _reason_stats(s.get("_trades") or []),
            # 出场结构矩阵要按族重算，留一份逐笔引用（不复制，同一个 list 对象）
            "_trades_ref": s.get("_trades") or [],
            "tail_split": _tail_split(s.get("_trades") or []),
            "sample": sample,
            # `--top-n` 走 evaluate_trades(collect_all=True)，逐笔是**重叠未去重的全候选**
            # （backtest_factors.py:2133）⇒ 它的 trade_summary 与其它方案不同口径，
            # 只有 portfolio 块可用。标记出来，逐笔类表格一律排除。
            "topn": "--top-n" in _flag_set(
                (GROUPS.get(group, {}).get("runs") or {}).get(name, [])),
        })
    return out


def _warn_if_mixed(group: str, rows: list[dict]) -> None:
    """笔数一致性检查——**最后一道防线**。

    指纹已能防止跨样本量复用，但万一还是混了（手工拷文件、改了 universe 等），
    笔数会明显分簇。择时方案（名字含 amv）本来就会大幅减少笔数，单独排除。
    """
    plain = [(r["name"], r["n"]) for r in rows if r["n"] and "amv" not in r["name"]]
    if len(plain) < 2:
        return
    ns = [n for _, n in plain]
    if max(ns) / max(min(ns), 1) > 1.5:
        lo = [f"{n}({c})" for n, c in plain if c < max(ns) / 1.5]
        print(f"\n⚠️ **【{group}】笔数不一致，判定可能无效**：最多 {max(ns)} / 最少 {min(ns)}。")
        print(f"   偏少的方案：{'、'.join(lo)}")
        print("   同一 entry_filter 下信号数只由样本股票数决定，不由止损参数决定 ⇒")
        print("   这些结果很可能来自不同 --sample。删除 artifacts/logs/m2_sweep 后重跑。")


def _print_trade_group(group: str, rows: list[dict]) -> None:
    meta = GROUPS[group]
    hdr = (f"{'组':<20}{'笔数':>7}{'胜率':>8}{'期望%':>8}{'期望R':>8}"
           f"{'累计R':>9}{'盈亏比':>8}{'均盈%':>8}{'大赢家':>7}")
    print("\n" + "=" * len(hdr))
    print(f"【{group}】{meta['desc']}")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(rows, key=lambda x: x["name"]):
        print(f"{r['name']:<20}{r['n'] or 0:>7}{(r['win'] or 0) * 100:>7.1f}%"
              f"{(r['exp'] or 0) * 100:>+8.2f}{r['expR'] or 0:>8.3f}"
              f"{r['totR'] or 0:>9.1f}{r['payoff'] or 0:>8.3f}"
              f"{(r['avg_win'] or 0) * 100:>+8.2f}{r['big']:>7}")

    base_name = meta.get("baseline")
    base = next((r for r in rows if r["name"] == base_name), None)
    if not base:
        return
    print(f"\n组内判定（基准 = {base_name}；**R 仅在 risk_frac 相同的方案之间可比**）")
    print("  出场类（改离场时点：trail/breakeven/stop-pct/cost-zone…）：")
    print("    **累计R** 提升 >2% **且 期望R 不下降** 即通过；尾部R 绝对量降 >30% 另行警示")
    print("  入场类（改信号集：amv 择时 / 入场过滤 / top-n）：期望R 提升 >2% 且 大赢家**占比**不降")
    print("  ⚠️ **改动止损距离的方案（stop-pct / stop-mode / tick-buffer）一律不用 R 判**：")
    print("     R = ret/risk_frac，而 pct 模式下 risk_frac **恒等于 stop_pct**")
    print("     ⇒ 期望R = 期望% ÷ stop_pct。「pct_05 累计R +73.8%」里 1.6 倍纯粹是分母")
    print("     8%/5%，真实期望率 0.67% vs 0.64% 几乎持平。这类方案改判 期望% 与 margin，")
    print("     判定行标 [出场·R口径变]。")
    print("  ⚠️ 分开判的理由：「削大赢家」是防**为提高胜率而筛掉大赢家**——那些收益会")
    print("     **永久消失**。出场机制不筛信号，只改离场时点，用「少赚一点尾部」换")
    print("     「多一些赢家」；对它硬套「大赢家占比不降」会否掉累计R +43% 的方案。")
    print("  ⚠️ 出场类为何还要看期望R：累计R = 期望R × 笔数，而**止损越紧笔数越多**")
    print("     （更早离场 ⇒ 后续还能再进场）。只看累计R，笔数涨 5% 就能凭摊薄「通过」。")
    if group == "B_stop_pct":
        print("  ⚠️ B 组是**参数扫描**，基准取中间档，✅/❌ 只表示「相对中间档的方向」，")
        print("     不代表绝对优劣。⚠️ **别拿本组的累计R 排序**——各档 risk_frac 不同，")
        print("     累计R 之间差着分母；绝对排序看 期望% 与跨组表的 margin。")
    b_expR, b_aw = base["expR"] or 0, base["avg_win"] or 0
    b_totR = base["totR"] or 0
    b_big, b_n = base["big"], base["n"] or 1
    b_rate = b_big / b_n
    b_split = base.get("tail_split")
    if b_split:
        tail, non = b_split
        print(f"  基准收益结构：尾部R {tail:+.0f}（大赢家 {b_big} 笔，ret>20%）"
              f" / 非尾部R {non:+.0f} / 合计 {tail + non:+.0f}")
        if non < 0:
            print(f"     ⚠️ **非尾部整体亏损**：全部收益来自 {b_big / b_n:.1%} 的大赢家，"
                  f"其余 {1 - b_big / b_n:.1%} 交易净亏 {abs(non):.0f}R。")
            print("        ⇒ 任何「提高胜率」的改动都要先看它有没有动到尾部；")
            print("          也意味着漏掉几只大赢家就足以让整个策略转负。")
    b_fam = _family_stats(base.get("_trades_ref") or [])
    if b_fam:
        n_open = b_fam.get("末持", {}).get("n", 0.0)
        r_open = b_fam.get("末持", {}).get("sum_r", 0.0)
        r_all = sum(d["sum_r"] for f, d in b_fam.items() if f != "scaled")
        if n_open and r_all:
            print(f"  基准已实现口径：累计R {r_all:+.0f} → 剔除末持(open_end) "
                  f"{n_open:.0f} 笔的 {r_open:+.0f}R ⇒ **{r_all - r_open:+.0f}R**")
            if r_all > 0 >= r_all - r_open:
                print("     ⚠️ **基准的正期望完全来自未平仓浮盈**（期末仍持仓、按最后一根")
                print("        收盘价标记）⇒ 已实现口径为负。在这个基准上比出来的「改进」")
                print("        要格外小心：相对提升再大，绝对水平仍可能是负的。")
    for r in sorted(rows, key=lambda x: x["name"]):
        if r["name"] == base_name:
            continue
        expR, aw, totR = r["expR"] or 0, r["avg_win"] or 0, r["totR"] or 0
        n = r["n"] or 1
        rate = r["big"] / n
        d_exp = (expR - b_expR) / abs(b_expR) if b_expR else 0.0
        d_tot = (totR - b_totR) / abs(b_totR) if b_totR else 0.0
        d_aw = (aw - b_aw) / b_aw if b_aw else 0.0
        d_rate = (rate - b_rate) / b_rate if b_rate else 0.0
        d_n = (n - b_n) / b_n if b_n else 0.0
        exp_pct, b_exp_pct = r["exp"] or 0.0, base["exp"] or 0.0
        d_exp_pct = (exp_pct - b_exp_pct) / abs(b_exp_pct) if b_exp_pct else 0.0
        mg = _margin(r)
        b_mg = _margin(base)
        exit_side = _side_of(group, r, base, base_name) == "exit"
        same_denom = _same_r_denom(group, r["name"], base_name)
        why, notes = [], []
        if not same_denom:
            # ⚠️ 止损距离变了 ⇒ risk_frac(=R 分母)变了 ⇒ **R 一律不能用**。
            # 改用收益率口径：期望% 提升 >2%；出场类附加 margin 不降、
            # 入场类附加 大赢家占比不降。
            ok = d_exp_pct > MIN_EXPECTANCY_GAIN
            if d_exp_pct <= MIN_EXPECTANCY_GAIN:
                why.append(f"期望% {d_exp_pct:+.1%} 未达 +2%")
            if exit_side:
                if mg is not None and b_mg is not None:
                    if mg < b_mg - 0.002:            # margin 掉超过 0.2pp
                        ok = False
                        why.append(f"margin {b_mg * 100:+.1f}→{mg * 100:+.1f}pp 变薄")
            elif d_rate <= -MAX_AVG_WIN_DROP:
                ok = False
                why.append(f"大赢家占比 {b_rate:.2%}→{rate:.2%}")
            main = (f"期望% {b_exp_pct * 100:+.2f}→{exp_pct * 100:+.2f}"
                    f"（{d_exp_pct:+6.1%}）"
                    f"  margin {(b_mg or 0) * 100:+.1f}→{(mg or 0) * 100:+.1f}pp")
            notes.append(f"**R 口径不同**（止损距离变了 ⇒ risk_frac 变了）："
                         f"期望R {b_expR:.3f}→{expR:.3f} 的差异含纯分母变化，故不用 R 判")
        elif exit_side:
            ok = d_tot > MIN_EXPECTANCY_GAIN and d_exp >= 0
            if d_tot <= MIN_EXPECTANCY_GAIN:
                why.append(f"累计R {d_tot:+.1%} 未达 +2%")
            if d_exp < 0:
                why.append(f"期望R {d_exp:+.1%} 下降（累计R 靠笔数 {d_n:+.1%} 摊薄，非质量提升）")
            split = r.get("tail_split")
            if split and b_split:
                d_tail = (split[0] - b_split[0]) / abs(b_split[0]) if b_split[0] else 0.0
                d_non = split[1] - b_split[1]
                if d_tail < -0.30:
                    # 只在**尾部R 绝对量**明显缩水时警示——那是真的「削大赢家」。
                    # 非尾部同时改善多少一并打出来，供人判断净效果。
                    notes.append(f"尾部R {b_split[0]:.0f}→{split[0]:.0f}"
                                 f"（{d_tail:+.0%}，大赢家贡献显著缩水；"
                                 f"非尾部R {b_split[1]:.0f}→{split[1]:.0f}，{d_non:+.0f}R）")
            if d_aw <= -MAX_AVG_WIN_DROP:
                notes.append(f"均盈 {d_aw:+.1%}")
            if d_rate <= -MAX_AVG_WIN_DROP:
                notes.append(f"大赢家占比 {b_rate:.2%}→{rate:.2%}")
            main = f"累计R {d_tot:+6.1%}  期望R {d_exp:+6.1%}  笔数 {d_n:+5.1%}"
        else:
            ok = (d_exp > MIN_EXPECTANCY_GAIN and d_aw > -MAX_AVG_WIN_DROP
                  and d_rate > -MAX_AVG_WIN_DROP)
            if d_exp <= MIN_EXPECTANCY_GAIN:
                why.append(f"期望R {d_exp:+.1%}")
            if d_aw <= -MAX_AVG_WIN_DROP:
                why.append(f"均盈 {d_aw:+.1%} 削大赢家")
            if d_rate <= -MAX_AVG_WIN_DROP:
                why.append(f"大赢家占比 {b_rate:.2%}→{rate:.2%}")
            main = f"期望R {d_exp:+6.1%}  均盈 {d_aw:+6.1%}"
        tag = ("出场" if exit_side else "入场") + ("" if same_denom else "·R口径变")
        line = (f"  {r['name']:<20}{'✅ 通过' if ok else '❌ 否决'} [{tag}]  {main}"
                f"  大赢家 {b_rate:.2%}→{rate:.2%}")
        if why:
            line += "  ｜" + "；".join(why)
        print(line)
        for nt in notes:
            print(f"      ⚠️ {nt}")


# 离场原因归并成「族」。原始词表有 10 种（stop / stop_delayed / trail_stop /
# breakeven_stop / cost_zone_stop / bbi_exit / bbi_exit_delayed / open_end / 各自的
# +scaled 变体），逐个列成矩阵太宽，而决策关心的是**族**。
#
# 两个后缀是**正交标记**，不是独立的族：
#   `_delayed`(backtest_factors:1345)  跳空等次日成交 ⇒ 并进基础族
#   `+scaled` (1326)                   触发过分批止盈 ⇒ **单独一列**，与 bbi/末持 **重叠**
#                                      （所以那一列不计入 100%）
_REASON_FAMILY = {
    "stop": "stop", "trail_stop": "trail", "breakeven_stop": "be",
    "cost_zone_stop": "cz", "bbi_exit": "bbi", "open_end": "末持",
}
_FAMILY_ORDER = ["bbi", "stop", "trail", "be", "cz", "末持", "其它"]


def _reason_family(reason: str) -> tuple[str, bool]:
    """``"bbi_exit+scaled"`` → ``("bbi", True)``；``"stop_delayed"`` → ``("stop", False)``。"""
    rs = str(reason)
    scaled = rs.endswith("+scaled")
    if scaled:
        rs = rs[: -len("+scaled")]
    if rs.endswith("_delayed"):
        rs = rs[: -len("_delayed")]
    return _REASON_FAMILY.get(rs, "其它"), scaled


def _family_stats(trades: list) -> dict[str, dict[str, float]]:
    """按族汇总 ``{family: {n, sum_r}}``，额外给一个 ``"scaled"`` 叠加项。"""
    out: dict[str, dict[str, float]] = {}

    def _bump(key: str, r: Optional[float]) -> None:
        d = out.setdefault(key, {"n": 0.0, "sum_r": 0.0})
        d["n"] += 1
        if r is not None:
            d["sum_r"] += r

    for t in trades:
        if not isinstance(t, dict) or not t.get("reason"):
            continue
        fam, scaled = _reason_family(t["reason"])
        _bump(fam, t.get("r_multiple"))
        if scaled:
            _bump("scaled", t.get("r_multiple"))
    return out


def _print_exit_structure(group: str, rows: list[dict], base_name: Optional[str]) -> None:
    """**跨方案**的出场结构对比矩阵。

    为什么必须是矩阵而不是单方案的原因表：每个方案单看都是「`bbi_exit+scaled` 均收最高」
    ——那是恒等式（见 `_reason_stats`）。有判别力的是**方案之间的差异**：
    某个机制有没有把交易从 `stop` 桶**搬走**、R 的来源有没有换地方。

    打两张表而不是一张宽表：
      ① 笔数占比 —— 看**交易去哪了**（分布迁移）
      ② R 贡献占比 —— 看**钱从哪来**（可加；均收不可加，不能用来加总）
    """
    stats = {r["name"]: _family_stats(r.get("_trades_ref") or [])
             for r in rows if r.get("_trades_ref")}
    stats = {k: v for k, v in stats.items() if v}
    if len(stats) < 2:                       # 单方案没有可比性，矩阵没意义
        return
    fams = [f for f in _FAMILY_ORDER
            if any(f in v for v in stats.values())]
    cols = fams + ["scaled"]
    order = ([base_name] if base_name in stats else []) + \
            sorted(k for k in stats if k != base_name)

    def _row_head(name: str) -> str:
        return "    " + f"{name:<20}"

    print(f"\n  【出场结构对比】{group}"
          f"（scaled 是**叠加标记**，与 bbi/末持重叠，不计入合计）")
    print("\n  ① 笔数占比%（看**交易去哪了**）")
    print("    " + f"{'方案':<20}" + "".join(f"{c:>8}" for c in cols))
    for name in order:
        v = stats[name]
        tot = sum(d["n"] for f, d in v.items() if f != "scaled") or 1
        cells = [(f"{v[c]['n'] / tot * 100:>7.1f}" if c in v else f"{'—':>8}")
                 for c in cols]
        print(_row_head(name) + "".join(cells))

    # ② 用**每笔 R 贡献**而不是「占总R 的百分比」：后者的分母是 total_R，
    #    而 total_R 可以很小（pct_12 只有 58R，而 bbi 桶 +383R / stop 桶 -335R）
    #    ⇒ 占比会炸到 660% / -578%，跨方案还不可比。
    #    每笔 R 贡献的好处：**行合计恰好等于期望R**，一眼看出「期望从哪来、在哪漏掉」。
    #
    # ⚠️ 「已实现」列是**必须**的：`末持`(open_end) 是样本期末仍持仓、按最后一根收盘价
    #    标记的**未实现**盈亏（backtest_factors:1430）。实测 3000 样本基准：
    #    含未实现累计R +288，剔掉末持的 +320R 之后是 **-32R** ⇒ 正期望完全来自没兑现的
    #    浮盈。只看「合计」会把一个已实现负期望的策略读成正期望。
    #    分母也要剔掉末持笔数，否则不是「已平仓交易的期望」。
    print("\n  ② 每笔 R 贡献（行合计 = 期望R；看**钱从哪来、在哪漏掉**）")
    print("    " + f"{'方案':<20}" + "".join(f"{c:>8}" for c in cols)
          + f"{'合计':>8}" + f"{'已实现':>9}")
    for name in order:
        v = stats[name]
        n_tot = sum(d["n"] for f, d in v.items() if f != "scaled") or 1
        n_open = v.get("末持", {}).get("n", 0.0)
        cells, total, realized = [], 0.0, 0.0
        for c in cols:
            if c not in v:
                cells.append(f"{'—':>8}")
                continue
            x = v[c]["sum_r"] / n_tot
            cells.append(f"{x:>+8.3f}")
            if c != "scaled":
                total += x
                if c != "末持":
                    realized += v[c]["sum_r"]
        n_closed = max(n_tot - n_open, 1)
        r_closed = realized / n_closed
        flip = total > 0 >= r_closed          # 含未实现为正、已实现非正 ⇒ 必须点出来
        print("    " + f"{name:<20}" + "".join(cells) + f"{total:>+8.3f}"
              + f"{r_closed:>+9.3f}" + ("  ⚠️" if flip else ""))
        if flip:
            print(f"      ⚠️ **正期望全部来自未平仓浮盈**：末持 {n_open:.0f} 笔"
                  f"（占 {n_open / n_tot:.1%}）贡献 {v.get('末持', {}).get('sum_r', 0):+.0f}R，"
                  f"剔掉后已实现期望 {r_closed:+.3f}R/笔 ⇒ **没兑现的边际不是边际**")
    print("    ⚠️ 「已实现」= 剔除 `末持`(open_end，期末仍持仓、按最后收盘价标记的未实现"
          "盈亏)，")
    print("       分母也剔掉那些笔数 ⇒ 它才是「已平仓交易」的期望R。")
    print("    ⚠️ R 仅在 risk_frac 相同的方案之间可比（本组不同 stop_pct 差着分母）；")
    print("       跨档位只看表①的分布迁移。")
    print("    ⇒ 单看一个方案永远是「bbi_exit+scaled 均收最高」（那是它的定义）；")
    print("       有判别力的是**行与行的差**：哪个机制把交易从 stop 桶搬走了、期望从哪补回来。")


def _reason_stats(trades: list) -> dict[str, dict[str, Any]]:
    """按离场原因汇总 ``{reason: {n, avg_ret, sum_r}}``。

    ⚠️ **这是按「结果」分组，不是按参数分组。** `+scaled` 后缀只在价格站上 BBI 并打出
    两根中大阳线时才挂上（`backtest_factors.py:1326/1390`），`bbi_exit` 也要求曾站上 BBI；
    而 `stop` / `trail_stop` / `breakeven_stop` 按定义就是跌下来的交易。
    ⇒ **按均收给离场原因排序，`bbi_exit+scaled` 永远第一**——那是它的定义，不是发现。
    「选 bbi_exit+scaled」是不可能的，离场原因由价格路径决定。

    能选的是参数（`--scale-out` / `--trail` / …），要看的是**机制改动有没有把交易从
    `stop` 桶搬到 `bbi` 桶**（分布迁移），以及每个桶**贡献的总 R**（不是均收）。
    只有 `sum_r` 是可加的：均收高但只有 20 笔的桶，对结果的影响可能不如均收平平但有
    900 笔的桶。
    """
    out: dict[str, dict[str, Any]] = {}
    for t in trades:
        if not isinstance(t, dict):
            continue
        rs = t.get("reason")
        if not rs:
            continue
        d = out.setdefault(rs, {"n": 0, "ret_sum": 0.0, "sum_r": 0.0, "has_r": False})
        d["n"] += 1
        d["ret_sum"] += t.get("ret") or 0.0
        if t.get("r_multiple") is not None:
            d["sum_r"] += t["r_multiple"]
            d["has_r"] = True
    for d in out.values():
        d["avg_ret"] = d["ret_sum"] / d["n"] if d["n"] else 0.0
    return out


def _print_reasons(rows: list[dict], base_name: Optional[str]) -> None:
    """打印基准的离场原因分布。**按 R 贡献排序，不按均收排序。**"""
    base = next((r for r in rows if r["name"] == base_name), None)
    if not base:
        return
    st = base.get("reasons_calc") or {}
    if not st:
        return
    tot_n = sum(d["n"] for d in st.values()) or 1
    tot_r = sum(d["sum_r"] for d in st.values())
    print(f"\n  基准 {base_name} 的离场原因分布"
          f"（⚠️ **这是结果分组，不是可选参数**）")
    print(f"    {'原因':<20}{'笔数':>7}{'占比':>8}{'均收%':>9}{'R贡献':>10}{'占总R':>9}")
    for rs, d in sorted(st.items(), key=lambda kv: -kv[1]["sum_r"]):
        share_r = (d["sum_r"] / tot_r) if abs(tot_r) > 1e-9 else 0.0
        print(f"    {rs:<20}{d['n']:>7}{d['n'] / tot_n * 100:>7.1f}%"
              f"{d['avg_ret'] * 100:>+9.2f}{d['sum_r']:>+10.0f}{share_r * 100:>8.0f}%")
    print("    ⚠️ `+scaled` 只在站上 BBI 且出两根中大阳线时才挂上，`bbi_exit` 也要求曾站上")
    print("       BBI；而 stop/trail_stop 按定义就是跌下来的交易 ⇒ **按均收排序它必然第一，**")
    print("       **那是定义不是发现**。离场原因由价格路径决定，选不了。")
    print("    ⇒ 该看的是：机制改动有没有把交易从 stop 桶**搬到** bbi 桶（分布迁移），")
    print("       以及每桶**贡献的总 R**——均收高但只 20 笔的桶，影响可能不如均收平平的 900 笔。")


def _print_cross_group(groups: dict[str, list[dict]]) -> None:
    """跨组比较：**只用收益率口径**，R 一律不出现。"""
    rows = []
    for g, rs in groups.items():
        if g == "C_portfolio":
            continue
        for r in rs:
            if r.get("topn"):          # 全候选口径，逐笔指标不可与其它方案并列
                continue
            rows.append((g, r))
    if not rows:
        return
    hdr = (f"{'组/方案':<32}{'笔数':>7}{'胜率':>8}{'期望%':>9}{'盈亏比':>8}"
           f"{'平衡胜率':>9}{'margin':>8}")
    print("\n" + "=" * len(hdr))
    print("跨组比较（**不同 stop_mode 之间 R 不可比，这里只看收益率**）")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for g, r in sorted(rows, key=lambda x: -(x[1]["exp"] or -9)):
        be = _breakeven_wr(r["payoff"])
        margin = (r["win"] - be) if (be is not None and r["win"] is not None) else None
        print(f"{g + '/' + r['name']:<32}{r['n'] or 0:>7}"
              f"{(r['win'] or 0) * 100:>7.1f}%{(r['exp'] or 0) * 100:>+9.2f}"
              f"{r['payoff'] or 0:>8.3f}"
              f"{be * 100 if be else 0:>8.1f}%"
              f"{margin * 100 if margin is not None else 0:>+7.1f}pp")
    print("\n  margin = 实际胜率 − 盈亏平衡胜率。越薄越脆弱：成本上升或波动率下降就可能翻负。")


def _ret_over_dd(pf: dict) -> Optional[float]:
    """收益/最大回撤。``simulate_portfolio`` 已经算好（`return_over_maxdd`），
    旧结果文件里没有则现算。

    为什么必须看它：按 `total_return` 排序会**系统性偏袒高敞口方案**——同一份幂律
    收益序列，敞口翻倍则收益与回撤同向放大，总收益榜首往往只是杠杆最大的那个。
    这恰好与本脚本自己的结论（「决定亏损幅度的是总敞口」）相反着用。
    """
    if not isinstance(pf, dict):
        return None
    v = pf.get("return_over_maxdd")
    if isinstance(v, (int, float)):
        return float(v)
    tr, dd = pf.get("total_return"), pf.get("max_drawdown")
    if isinstance(tr, (int, float)) and isinstance(dd, (int, float)) and dd > 0:
        return tr / dd
    return None


def _print_portfolio(rows: list[dict]) -> None:
    if not rows:
        return
    hdr = (f"{'方案':<24}{'总收益':>9}{'CAGR':>8}{'最大回撤':>9}{'收益/回撤':>10}"
           f"{'成交':>7}{'被限':>7}{'执行率':>8}")
    print("\n" + "=" * len(hdr))
    print("【C_portfolio】组合级（R 完全不适用；逐笔正期望 ≠ 组合能赚）")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    # 按 收益/回撤 降序（None 垫底）——不用总收益，理由见 _ret_over_dd
    for r in sorted(rows, key=lambda x: (_ret_over_dd(x["pf"] or {}) is not None,
                                         _ret_over_dd(x["pf"] or {}) or 0.0),
                    reverse=True):
        d = r["pf"] or {}
        taken = d.get("n_taken") or d.get("filled") or 0
        skip = d.get("n_skipped") or d.get("skipped") or 0
        er = taken / (taken + skip) if (taken + skip) else 0
        rdd = _ret_over_dd(d)
        print(f"{r['name']:<24}{(d.get('total_return') or 0) * 100:>8.1f}%"
              f"{(d.get('cagr') or 0) * 100:>7.1f}%"
              f"{(d.get('max_drawdown') or 0) * 100:>8.1f}%"
              f"{(f'{rdd:+.2f}' if rdd is not None else '—'):>10}"
              f"{taken:>7}{skip:>7}{er * 100:>7.1f}%")
    print("\n  ⚠️ 排序用**收益/回撤**而非总收益：敞口翻倍会让收益与回撤同向放大，")
    print("     总收益榜首往往只是杠杆最大的那个。")
    print("  ⚠️ 受控实验结论：决定亏损幅度的是**总敞口**（max_concurrent × max_pos），")
    print("     不是持仓数量——B1 信号高度相关（普跌时全市场同时触发），分散持仓数无效。")
    print("     执行率低时「先到先得」等于抽签命中大赢家，用 --top-n 做横截面择优。")
    print("  ⚠️ 回撤是**已实现权益**口径（不含持仓浮亏），真实回撤更大 ⇒ 收益/回撤为乐观上界。")


def _warn_missing(groups: dict[str, list[dict]]) -> None:
    """列出**该有却没有**的方案。

    ⚠️ 没有这段提示时，某个方案 `[FAIL]` 之后报表只是**少一行**，没有任何标记。
    25 个方案要跑几小时，那行 `[FAIL]` 早滚出屏幕 ⇒ 判读时会把「没跑出来」
    当成「本来就没这一项」，得出的结论建立在残缺矩阵上。
    """
    miss: list[str] = []
    for g, meta in GROUPS.items():
        got = {r["name"] for r in groups.get(g, [])}
        for n in meta.get("runs") or {}:
            if n not in got:
                miss.append(f"{g}/{n}")
    if not miss:
        return
    print(f"\n⚠️ **缺 {len(miss)} 个方案的结果文件**（跑失败、被中断，或本轮 --only 没覆盖）：")
    for i in range(0, len(miss), 3):
        print("     " + "、".join(miss[i:i + 3]))
    print("     判定只在**已有**方案之间成立；补跑：--only <方案名>（已完成的会 [SKIP]）")


def report(cross: bool, sample: Optional[int] = None,
           data_source: str = "tdx", window: Optional[tuple[str, str]] = None,
           pin_universe: bool = False) -> None:
    groups = _collect(cross, sample, data_source, window, pin_universe)
    if not any(groups.values()):
        print("没有结果文件，先跑扫描")
        return
    n_sample = next((r["sample"] for rs in groups.values() for r in rs), None)
    print("\n" + "#" * 74)
    print(f"# M2 机制扫描  样本 {n_sample} 只  数据源 {data_source}"
          f"{'（含退市股/已前复权）' if data_source != 'tdx' else '（本地 vipdoc，仅当前挂牌）'}"
          f"{'  区间 2022-2024（跨窗复核）' if cross else ''}"
          f"{f'  窗口 {window[0]}~{window[1]}(已钉死)' if window else ''}"
          f"{'  宇宙已钉死' if pin_universe else ''}")
    if not (window and pin_universe) and not cross:
        print("# ⚠️ 未同时钉死窗口与宇宙 ⇒ **本批不可复现**：vipdoc 目录与 .day 都会随")
        print("#    通达信下载变动，长时间扫描里各方案的宇宙/K线窗口不同（实测 5535→5536、")
        print("#    同参数笔数 1106/1092/1087）。可复现跑法：--window S E --pin-universe")
    print("#" * 74)
    for g in ("A_stop_low", "B_stop_pct"):
        if groups.get(g):
            _warn_if_mixed(g, groups[g])
            _print_trade_group(g, groups[g])
            _print_reasons(groups[g], GROUPS[g].get("baseline"))
            _print_exit_structure(g, groups[g], GROUPS[g].get("baseline"))
    _print_cross_group(groups)
    _print_portfolio([r for r in groups.get("C_portfolio", []) if r.get("pf")])
    _warn_missing(groups)
    if not cross:
        print("\n⚠️ 通过的组仍须跨窗复核：--cross-window（2022-2024）。")


def _prepare_universe(sample: int, cross: bool, data_source: str,
                      window: Optional[tuple[str, str]]) -> Optional[str]:
    """先跑一次 `--dump-codes` 落一份代码表，供全部方案共用 ⇒ **钉死宇宙**。

    为什么不在本脚本里直接抽样：universe 解析要 import `local_tdx_data`（依赖 TDX_ROOT）
    或 `s_data`（依赖 S_DATA_ROOT），在没有这些数据的机器上会直接失败。
    交给 `backtest_factors --dump-codes` 复用它已有的 universe 逻辑，只做一次目录列举，
    很快（不加载任何 K 线）。
    """
    OUTDIR.mkdir(parents=True, exist_ok=True)
    fp = _fingerprint(sample, cross, data_source, window, True)
    path = OUTDIR / f"_universe__{fp}.txt"
    if path.is_file() and path.stat().st_size > 0:
        n = sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
        print(f"[SKIP] 宇宙已存在 {path.name}（{n} 只）")
        return str(path)
    # 只要 universe 选择相关的参数，不要窗口/回测参数
    probe = ["--trade-sim"]
    if data_source == "tdx":
        probe += ["--universe-local", "--universe-sample", str(sample)]
    else:
        probe += ["--data-source", data_source, "--universe-sdata",
                  "--universe-sample", str(sample)]
    probe += ["--dump-codes", str(path)]
    print(f"[PREP] 落一份宇宙到 {path.name} 供全部方案共用")
    r = subprocess.run([sys.executable, str(SCRIPT)] + probe, cwd=str(BASE))
    if r.returncode != 0 or not path.is_file():
        print(f"⚠️ 宇宙落盘失败（exit={r.returncode}）⇒ **本轮不钉宇宙**，"
              f"各方案仍会各自抽样（可能漂移）")
        return None
    return str(path)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # GBK 终端打不了 ⚠️ 等符号
    ap = argparse.ArgumentParser(description="M2 机制类改进扫描（分组）")
    # default=None 而非 1000：区分「用户显式指定了批次」与「没指定」。
    # 前者报表就看那一批；后者实跑看刚跑的那批、--report-only 自动取最大批。
    ap.add_argument("--sample", type=int, default=None,
                    help="样本股票数（默认 1000）。--report-only 时用于指定汇总哪一批")
    ap.add_argument("--only", default="",
                    help="只跑匹配的组或方案（子串匹配组名/方案名）")
    ap.add_argument("--cross-window", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--jobs", "-j", type=int, default=1,
                    help="并行进程数（各方案相互独立；默认 1=串行）。见模块开头「为什么慢」。"
                         "⚠️ 内存乘 N，会按可用内存自动收敛")
    ap.add_argument("--data-source", choices=["tdx", "qlib", "csv"], default="tdx",
                    help="tdx(默认)=本地通达信 vipdoc,只含当前挂牌股(有幸存者偏差)、"
                         "要逐票算前复权; qlib/csv=S_DATA bundle,含退市股且已前复权"
                         "(去偏且更快,但 2020-09~2021-07 有缺口、数据到 2026-02)。"
                         "⚠️ 换数据源=换宇宙,结果与之前几轮不可比,已进文件名指纹")
    ap.add_argument("--window", nargs=2, metavar=("START", "END"), default=None,
                    help="钉死 K 线窗口(YYYY-MM-DD YYYY-MM-DD)。⚠️ **必须两端都给**："
                         "只给 --end 钉不住(get_ohlcv_table 先 tail(count) 再过滤 ⇒ "
                         "新 bar 到来时窗口缩水且滑动)。本项会自动放大 --count")
    ap.add_argument("--pin-universe", action="store_true",
                    help="先落一份代码表供全部方案共用 ⇒ 钉死宇宙。"
                         "vipdoc 目录会随下载变动(实测扫描中 5535→5536)，"
                         "seed 固定也没用——**被抽的池子变了**")
    a = ap.parse_args()
    sample = a.sample if a.sample else DEFAULT_SAMPLE
    window = tuple(a.window) if a.window else None            # type: ignore[assignment]
    if window and a.cross_window:
        print("--window 与 --cross-window 冲突（后者已自带 2022-2024 窗口）")
        return 2
    codes_file = None

    if not a.report_only:
        todo = [(g, n, e) for g, meta in GROUPS.items()
                for n, e in meta["runs"].items()
                if not a.only or a.only in g or a.only in n]
        if not todo:
            print(f"--only {a.only} 没匹配到任何组/方案")
            return 2
        if a.pin_universe:
            codes_file = _prepare_universe(sample, a.cross_window, a.data_source, window)
        print(f"将跑 {len(todo)} 个方案，样本 {sample} 只，数据源 {a.data_source}"
              f"{'，区间 2022-2024' if a.cross_window else ''}"
              f"{f'，窗口 {window[0]}~{window[1]}' if window else ''}"
              f"{'，宇宙已钉死' if codes_file else ''}"
              f"{f'，并行 {a.jobs} 进程' if a.jobs > 1 else ''}")
        _run_all(todo, sample, a.cross_window, a.force, max(1, a.jobs), a.data_source,
                 window, codes_file)
    # ⚠️ 实跑时必须传**刚跑的那批**。原先这里传 None ⇒ 汇总「最大样本量」那批：
    #    跑 --sample 300 试跑，报表却显示 s1000 的旧结果，看起来像新结果。
    report(a.cross_window, sample if not a.report_only else a.sample, a.data_source,
           window, bool(codes_file) or (a.report_only and a.pin_universe))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
