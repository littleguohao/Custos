# -*- coding: utf-8 -*-
"""R37-C5 pre2019 终审终端（判据 v0.273 定稿 → v0.281 CI 三分 → v0.298
thr 三分 → v0.299 a_sample 可疑闸）。

只接受 pre2019 untouched 段（2010-01-01..2016-12-31）内的窗口——与
``score_calibration_study`` Phase 3「只接受 pre2019 输入」互为同族镜像
（挖掘/判定侧工具对 pre2019 硬拒绝，本工具对非 pre2019 硬拒绝）。

做的事（一次一单）：
  冻结候选基因组 vs 基准档 pct5_trail08 在 pre2019 段上 V0 重放（与
  exit_campaign 生产评估器同引擎同公式：信号缓存 + as-of V0 分 +
  summarize_trades/simulate_portfolio_topn + sg._margin），报
  Δmargin + **日簇**配对 bootstrap SE（v0.324：交易按 (code, entry_date)
  1:1 配对、**成日重抽样**——同日进场的交易受同一市场冲击彼此相关，
  按交易 iid 重抽的 CI 系统性偏窄，owner 方法论 review #3），按 v0.299
  判决规则
  （n_taken 低于预期下限 ⇒ 跑数可疑不出判决 + thr 三分；完整判据、
  v0.281 废因勘误与修订史见 apply_c5 docstring）：

  thr = γ×合并标尺（量级激活 n≥200 时）否则 0：
  - CI95 hi < thr ⇒ killed（整个置信区间够不到标尺，证据性否决）；
  - CI95 lo > 0 且点估计 ≥ thr ⇒ not_vetoed（显著性+量级双要）；
  - 其余 ⇒ untested（既不进 Phase 4 也不按证伪归档）。
  γ 分档 v0.276：候选窗间保留率 <0.5 标 degraded ⇒ γ=0.75，否则 γ=0.5；
  n<200 时 SE≈0.025 任何 γ 失去意义 ⇒ thr=0——owner v0.273/v0.276 拍板在案。

**全过也只记「C5 未否决」**——本工具只能杀不能确认；判决表达 Δ/SE
（k·SE），不压二值。两窗合并标尺从战役报告自含读取（--campaign-report），
禁止手工转录。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

from custos.core.paths import LOGS, RESEARCH_DIR
from custos.research import window_usage as wu

#: pre2019 untouched 终审段（写死；窗外交集即拒）
PRE2019_START, PRE2019_END = "2010-01-01", "2016-12-31"
#: 前向 holdout 冻结段起点（v0.321 owner 方法论 review #1② 拍板）：判定窗
#: 2024-08-01~2026-09-04 已被 13 个单元反复读取（分岔路径——多次使用后不再
#: 是样本外），2026-09 以后的新数据**任何研究不得使用**，攒够后作下一轮
#: 判定窗。与 pre2019 同族：研究工具对 ≥ 本日起点的窗口硬拒绝。
FORWARD_HOLDOUT_START = "2026-09-05"

#: v0.273 定稿 γ（非 degraded）+ v0.276 C2 量级条款采 B 的执行端：
#: 窗间保留率（判定 Δm ÷ 挖掘 Δm）< 0.5 ⇒ candidate_degraded ⇒ C5 γ=0.75
GAMMA = 0.5
GAMMA_DEGRADED = 0.75
RETENTION_FLOOR = 0.5

#: C1 同门槛样本量——**v0.299 起升级为可疑闸**（见 apply_c5 docstring：
#: v0.281 的废因「钉死信号集 ⇒ 恒触发必杀门」被 v0.288 全历史复跑推翻，
#: 真因是加载到达截断；n 远低于预期=数据完整性信号 ⇒ 不出判决）；
#: v0.273 前置：n<200 量级条款停用
MIN_N_TAKEN = 100
MIN_N_FOR_MAGNITUDE = 200

#: 判决三态（只能杀不能确认；untested = 样本无法解析，既不杀也不放行）
VERDICT_KILLED = "killed"
VERDICT_NOT_VETOED = "not_vetoed"
VERDICT_UNTESTED = "untested"


def parse_genome_key(key: str) -> dict[str, Any]:
    """基因组键 → 参数字典（fail-closed：形态/家族/档位任一非法即 ValueError）。

    键形态 = ``sp{N}|{family}=off|{family}=v1[xv2]``（exit_genome.genome_key
    的逆；家族序任意、缺省家族按关处理，normalize 补全键）。
    """
    from custos.research.evolution import exit_genome as eg  # noqa: PLC0415

    parts = [p.strip() for p in str(key).split("|") if p.strip()]
    if not parts or not parts[0].startswith("sp"):
        raise ValueError(f"基因组键须以 sp<N> 起手: {key!r}")
    g: dict[str, Any] = {"stop_pct": float(parts[0][2:])}
    for part in parts[1:]:
        if "=" not in part:
            raise ValueError(f"基因组键段形态非法: {part!r}")
        fam, _, vals = part.partition("=")
        fam = fam.strip()
        if fam not in eg.FAMILIES:
            raise ValueError(f"未知家族 {fam!r}（合法：{sorted(eg.FAMILIES)}）")
        params = eg.FAMILIES[fam]
        if vals.strip() == "off":
            continue  # 关闭家族：normalize 归零/占位
        vs = vals.split("x")
        if len(vs) != len(params):
            raise ValueError(f"家族 {fam} 参数数不符：{vals!r}（须 {len(params)} 个）")
        for p, v in zip(params, vs):
            g[p] = float(v)
    bad = eg.validate(g)
    if bad:
        raise ValueError(f"基因组非法（fail-closed）：{'；'.join(bad)}")
    return eg.normalize(g)


def combined_yardstick(report: dict[str, Any]) -> dict[str, float]:
    """两窗按 n 加权合并 Δmargin（v0.273 标尺）——从战役报告自含读取。

    挖掘窗是数百基因组搜索的最大值，结构性偏高（实测 1.51×）；合并后字面
    「γ=0.5 保留一半效应」与实际严格度一致。返回 combined/bar(=γ×combined)
    及组成件（回填审计用）。
    """
    bc = report.get("best_candidate") or {}
    dm_m = bc.get("d_margin_mining")
    dm_j = bc.get("d_margin_judgment")
    n_m = (bc.get("mining") or {}).get("n_taken")
    n_j = (bc.get("judgment") or {}).get("n_taken")
    if None in (dm_m, dm_j) or not n_m or not n_j:
        raise ValueError("战役报告缺 best_candidate 双窗 Δmargin/n_taken")
    if dm_m <= 0:
        raise ValueError(f"挖掘窗 Δmargin={dm_m} ≤0——不该是候选（对账失败）")
    combined = (dm_m * n_m + dm_j * n_j) / (n_m + n_j)
    # v0.276 γ 分档：窗间保留率 < 0.5 ⇒ degraded ⇒ γ=0.75，否则 γ=0.5
    retention = dm_j / dm_m
    degraded = retention < RETENTION_FLOOR
    gamma = GAMMA_DEGRADED if degraded else GAMMA
    return {
        "d_margin_mining": float(dm_m),
        "d_margin_judgment": float(dm_j),
        "n_mining": float(n_m),
        "n_judgment": float(n_j),
        "combined": combined,
        "retention": retention,
        "candidate_degraded": degraded,
        "gamma": gamma,
        "bar": gamma * combined,
    }


def _margin_of(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """读数块（与 exit_campaign 生产评估器同公式同形状）。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    tsum = bt.summarize_trades(trades)
    return {
        "margin": sg._margin(
            {"win": tsum.get("win_rate"), "payoff": tsum.get("payoff_ratio")}
        ),
        "win_rate": tsum.get("win_rate"),
        "payoff_ratio": tsum.get("payoff_ratio"),
        "expectancy_R": tsum.get("expectancy_R"),
        "n": tsum.get("n"),
    }


def pair_trades(
    cand: list[dict[str, Any]], base: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(code, entry_date) 1:1 配对——两变体重放同一信号集，配对天然成立。

    仅交集入对；丢对数由调用方如实上报（重放确定性 ⇒ 正常应为 0）。
    """
    bmap = {(t["code"], t["entry_date"]): t for t in base}
    pairs = [
        (t, bmap[(t["code"], t["entry_date"])])
        for t in cand
        if (t["code"], t["entry_date"]) in bmap
    ]
    return pairs


def paired_bootstrap(
    pairs: list[tuple[dict[str, Any]]], *, seed: int, n_boot: int
) -> dict[str, Any]:
    """**日簇**配对 bootstrap（v0.324，owner 方法论 review #3）：成**日**
    重抽样 ⇒ Δmargin 分布的 SE/CI95。

    同一天进场的交易受同一个市场冲击、彼此相关——按交易 iid 重抽会把
    这份相关性当独立信息、**CI 系统性偏窄**（R37-C5 的 CI [−0.0002,
    +0.0063] 与该口径下「会判 killed」的推断都可能偏乐观，已注记并按
    owner 拍板日簇口径重跑）。配对结构：两变体重放同一信号集，同一对
    共享 entry_date——**抽中某日 ⇒ 该日所有配对同进同出**（与
    score_c5_terminal.day_cluster_bootstrap 同族；全部对各自一天的
    极端情形退化为按对 iid，语义连续）。
    """
    rng = random.Random(seed)
    by_day: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for pr in pairs:
        by_day.setdefault(pr[0]["entry_date"], []).append(pr)
    days = sorted(by_day)
    if not days:
        return {"se": None, "ci95": None, "n_boot_ok": 0, "n_days": 0}
    deltas: list[float] = []
    for _ in range(n_boot):
        pool = [
            pr
            for _ in range(len(days))
            for pr in by_day[days[rng.randrange(len(days))]]
        ]
        mc = _margin_of([c for c, _ in pool])["margin"]
        mb = _margin_of([b for _, b in pool])["margin"]
        if mc is None or mb is None:
            continue
        deltas.append(mc - mb)
    if len(deltas) < 2:
        return {"se": None, "ci95": None, "n_boot_ok": len(deltas), "n_days": len(days)}
    deltas.sort()
    lo = deltas[int(0.025 * (len(deltas) - 1))]
    hi = deltas[int(0.975 * (len(deltas) - 1))]
    return {
        "se": statistics.pstdev(deltas),
        "ci95": [lo, hi],
        "n_boot_ok": len(deltas),
        "n_days": len(days),
    }


def apply_c5(
    n_taken: Optional[int],
    d_margin: Optional[float],
    yardstick: dict[str, float],
    ci95: Optional[list[float]] = None,
) -> dict[str, Any]:
    """C5 判决（v0.299 owner review 修订；只能杀不能确认）。

    **判决规则（写死）**：
    - **a_sample 可疑闸前置（v0.299）**：``n_taken < 100`` ⇒ 本次跑数
      **判为可疑，不出判决**（verdict=untested + ``sample_suspicious``
      标记；报告顶层由终端打 warning）——**双向压**：杀与放行都不出。
      n 远低于预期是**数据完整性信号**，不是候选优劣的证据（废因勘误
      见下）。
    - 样本不可疑时按 **thr 三分**：``thr = γ×合并标尺``（量级激活：
      n_taken ≥ 200 时）**否则 0**——
      - CI95 **hi < thr** ⇒ ``killed``（证据性否决：hi<0 记 ``b_sign``；
        0≤hi<thr 记 ``c_magnitude``——整个置信区间都够不到标尺）；
      - CI95 **lo > 0 且点估计 ≥ thr** ⇒ ``not_vetoed``（显著性+量级双要）；
      - 其余 ⇒ ``untested``（CI 不可得 / 跨 thr / 显著但量级不定）——
        **既不进 Phase 4 也不按证伪归档**（区分「没测出来」与「确实不行」）。

    **v0.281→v0.298 迁移两个读数形**（owner 逐案核过）：
    ① CI 跨 0 但整体低于 bar（r37_c5_v2 形：CI_hi +0.0063 < thr 0.0167）
       untested → **killed**——v0.281 里量级条款只在 CI 全正时才被咨询，
       这种「跨零但整体够不到 bar」的形态漏网；
    ② CI 全正但点估计 < bar（v0.281 量级条款按点估计杀）→ **untested**
       ——杀要求整个 CI 低于标尺（更统计原则：连乐观端都够不到才杀）。
    r37_c5_v2 的实际判决**不翻**（owner 拍板：看过数据再改判=事后判据；
    R37 已收口不接 live，实际后果为零——R37 文档加注记留档）。
    R36-C5 不受影响（CI_hi +0.127 > bar 0.055 ⇒ 仍 untested）。

    **为什么硬 n 门槛不当判决用、却又不能只是诊断**（v0.299 owner review
    更正——v0.281 那段废因**归因错误**）：v0.281 写的「pre2019 交易数由
    **钉死信号集**决定（0AMV 做多区间所限），任何候选都在 ~64 笔 /
    n_taken ~21 ⇒ 恒触发必杀门」已被 **v0.288 全历史复跑推翻**：真因是
    **加载到达截断**（count=2000 滚动窗只回溯到 ~2018，start/end 过滤后
    pre2019 窗口近乎剪空）；全历史加载下同一候选 **70710 对配对、
    n_taken=536**，Δmargin 符号翻转（−0.2396 → +0.0033）。连带作废
    「r37_c5 自证 64 对样本足以给出 Δ/SE=−3.25 的决定性读数」——那个
    −3.25·SE 是在 **~1% 碎片宇宙**上算的。**教训方向相反**：配对
    bootstrap CI 只覆盖抽样误差，**不覆盖样本本身有偏**——它在被截断的
    数据上给出了自信且方向错误的结论；而当时真正把异常暴露出来的，
    恰恰是被废掉的那个 n 门槛（n=21 < 100）。⇒ 硬 n 门槛**不当判决用**
    （否则会把「数据坏了」误判成「候选坏了」——碎片首跑的 killed 正是
    如此），但 n 远低于预期本身是数据完整性信号：``check_reach`` 只守住
    截断这一种成因，其他成因（宇宙变化/信号集塌缩/未来数据缺陷）不在
    保护范围内 ⇒ a_sample 从「仅诊断」升级为「可疑即不出判决」。

    **全条款独立求值、不短路**（v0.281）：原实现 clause(a) 触发后 b/c 不再
    求值，``fired`` 只含 a_sample——那条更强的证据（Δ/SE=−3.25）因此
    不在 fired 里，只能靠报告顶层字段捞回。``diagnostics`` 逐条记录「若单独
    看会不会触发」，战役壳作为后续战役 generic 载体，档案精度值得。
    **would_fire 口径与判决一致（v0.299 对齐，owner review）**：b_sign /
    c_magnitude 改按 CI 判（hi<0 / hi<thr），不再按点估计——原口径下
    「CI 全正、点估计 < bar」诊断显示 would_fire=True 而判决 untested，
    两者对不上。
    """
    fired: list[dict[str, Any]] = []
    diag: list[dict[str, Any]] = []

    def _clause(name: str, value: Any, threshold: str, would: bool) -> None:
        diag.append(
            {
                "clause": name,
                "value": value,
                "threshold": threshold,
                "would_fire": would,
            }
        )

    # ── 前置量（CI/阈值先算，诊断与判决同口径）──
    lo, hi = (ci95[0], ci95[1]) if ci95 and len(ci95) == 2 else (None, None)
    mag_active = (n_taken or 0) >= MIN_N_FOR_MAGNITUDE
    thr = yardstick["bar"] if mag_active else 0.0
    below_floor = n_taken is None or n_taken < MIN_N_TAKEN

    # ── 全条款独立求值（不短路）；would_fire 与判决同 CI 口径（v0.299）──
    _clause(
        "a_sample",
        n_taken,
        f"n_taken≥{MIN_N_TAKEN}（低于 ⇒ 跑数可疑不出判决）",
        below_floor,
    )
    _clause("b_sign", d_margin, "CI95 全负 ⇒ 证据性否决", hi is not None and hi < 0)
    _clause(
        "c_magnitude",
        d_margin,
        f"CI95 整体 < {yardstick['gamma']}×合并标尺={yardstick['bar']:.6f}"
        + ("" if mag_active else f"（n<{MIN_N_FOR_MAGNITUDE} 停用）"),
        mag_active and hi is not None and hi < thr,
    )

    # ── 判决（v0.299）：a_sample 可疑闸前置（双向压），然后 thr 三分 ──
    ci_state = (
        "unavailable"
        if lo is None or hi is None
        else "all_negative"
        if hi < 0
        else "all_positive"
        if lo > 0
        else "spans_zero"
    )
    if below_floor:
        # 跑数可疑（数据完整性信号）——杀与放行都不出判决
        verdict = VERDICT_UNTESTED
    elif lo is None or hi is None:
        verdict = VERDICT_UNTESTED  # CI 不可得 = 无法解析
    elif hi < 0:
        verdict = VERDICT_KILLED
        fired.append(
            {
                "clause": "b_sign",
                "value": d_margin,
                "threshold": "CI95 全负 ⇒ 证据性否决",
                "ci95": ci95,
            }
        )
    elif hi < thr:
        verdict = VERDICT_KILLED
        fired.append(
            {
                "clause": "c_magnitude",
                "value": d_margin,
                "threshold": f"CI95 整体 < γ×合并标尺={thr:.6f} ⇒ 证据性否决",
                "ci95": ci95,
            }
        )
    elif lo > 0 and d_margin is not None and d_margin >= thr:
        verdict = VERDICT_NOT_VETOED
    else:
        verdict = VERDICT_UNTESTED

    return {
        "verdict": verdict,
        "fired": fired,
        "diagnostics": diag,
        "ci95": ci95,
        "ci_state": ci_state,
        "threshold": thr,
        "magnitude_clause_active": mag_active,
        "sample_suspicious": below_floor,
        "note": (
            (
                "n_taken 低于预期下限 ⇒ 跑数可疑（数据完整性信号），不出判决："
                "既不进 Phase 4 也不按证伪归档——排查数据到达/宇宙/信号集后重跑"
            )
            if below_floor
            else (
                "untested = pre2019 样本无法解析：既不进 Phase 4 也不按证伪归档"
                if verdict == VERDICT_UNTESTED
                else "全过也只记「未否决」，非确认"
            )
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R37-C5 pre2019 终审终端（判据 v0.299 可疑闸+thr 三分；只能杀不能确认）"
    )
    ap.add_argument("--genome", required=True, help="冻结候选基因组键（sp8|...）")
    ap.add_argument("--codes-file", required=True, help="钉死宇宙 codes 表")
    ap.add_argument(
        "--campaign-report",
        required=True,
        help="战役报告 JSON（两窗合并标尺自含读取，禁手工转录；候选键对账）",
    )
    ap.add_argument("--start", default=PRE2019_START)
    ap.add_argument("--end", default=PRE2019_END)
    ap.add_argument(
        "--count",
        type=int,
        default=100000,
        help="每股加载 K 线根数（默认 100000=全历史：count 是「最新向前 N 根」"
        "滚动窗，pre2019 终审窗口必须全历史加载，否则 start/end 过滤后窗口"
        "被静默剪空——r36_c5 首跑 count=2000 只跑到 19 笔碎片宇宙）",
    )
    ap.add_argument("--cost-bps", type=float, default=25.0)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--n-bootstrap", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260924)
    ap.add_argument("--tag", default="r37_c5")
    return ap


def _check_pre2019(args: Any, ap: argparse.ArgumentParser) -> None:
    """只接受 pre2019 段内窗口（硬拒绝镜像：非 pre2019 一律 exit 2）。"""
    if args.start < PRE2019_START or args.end > PRE2019_END or args.start > args.end:
        ap.error(
            f"C5 只接受 pre2019 段内窗口（{PRE2019_START}~{PRE2019_END}），"
            f"实际 {args.start}~{args.end}"
        )


def check_reach(count: int, start: str) -> None:
    """加载到达校验（fail-closed 前置）：count 是「最新向前 N 根」的滚动窗
    （``_load_one_bars`` 先取最新 N 根再做 start/end 过滤）——pre2019 终审
    窗口需要全历史加载，否则窗口被静默剪空（r36_c5 首跑 19 笔碎片宇宙的
    教训：count=2000 只回溯到 ~2018，过滤后近乎全空；``_load_bars_local``
    的尾部截断护栏管不到逐股直调路径）。用指数序列探测：加载到的最早日期
    晚于窗口起点 ⇒ 到达不足。"""
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415

    df = local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=count or 2000)
    if df is None or not len(df):
        raise RuntimeError("加载到达校验：指数数据读不到（本机无通达信数据？）")
    earliest = str(df["date"].astype(str).str[:10].iloc[0])
    if earliest > start:
        raise RuntimeError(
            f"加载到达不足：count={count} 仅回溯到 {earliest}，窗口起点 {start} "
            "在之前——start/end 过滤会把窗口剪空（r36_c5 首跑 19 笔碎片教训）。"
            "pre2019 终审须 --count 100000（全历史加载）"
        )


def _replay_variant(
    per_code: dict[str, dict], params: dict[str, Any], cost_bps: float
) -> list[dict[str, Any]]:
    """单变体信号重放（出场参数 = 基因组）——与 exit_campaign 评估器同调用序。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    trades: list[dict[str, Any]] = []
    for code, pack in per_code.items():
        trs = bt.evaluate_trades(
            {code: pack["df"]},
            signals_in={code: pack["signals"]},
            amv_regime=pack["regime"],
            cost_bps=cost_bps,
            collect_all=True,
            **params,
        )
        for tr in trs:
            score = pack["scores"].get(tr["entry_date"])
            if score is None:
                continue
            trades.append({**tr, "score": score, "code": code})
    return trades


def _warm_pre2019(args: Any) -> dict[str, dict]:
    """pre2019 窗信号缓存+V0 as-of 分（与 exit_campaign._warm 同调用序）。"""
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415

    codes = [
        l.strip() for l in Path(args.codes_file).read_text().splitlines() if l.strip()
    ]
    regime = bt.load_amv_regime(since=args.start)
    if not regime:
        raise RuntimeError("0AMV regime 读不到（compass_amv）——本机无数据？")
    index_df = (
        local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )
    per_code: dict[str, dict] = {}
    for i, code in enumerate(codes, 1):
        if i % 200 == 0:
            print(f"[warmup] {args.start}~{args.end} {i}/{len(codes)}", file=sys.stderr)
        df = bt._load_one_bars(code, args.count, args.start, args.end)
        if df is None or not len(df):
            continue
        sigs: list[dict] = []
        bt.evaluate_trades(
            {code: df},
            scorer=bt.SCORERS["baseline"],
            entry_gate=bt.j_low_gate,
            amv_regime=regime,
            cost_bps=args.cost_bps,
            collect_all=True,
            signals_out=sigs,
        )
        if not sigs:
            continue
        scores: dict[str, float] = {}
        for s in sigs:
            try:
                score, _level, _contrib = srs.asof_technical_score(
                    df, index_df, s["i"], code
                )
            except Exception:  # noqa: BLE001 — 单信号评分失败丢该信号
                continue
            scores[s["date"]] = score
        per_code[code] = {"df": df, "signals": sigs, "scores": scores, "regime": regime}
    return per_code


def run_c5(args: Any, per_code: Optional[dict[str, dict]] = None) -> dict[str, Any]:
    """C5 驱动：重放两变体 → 读数 → 配对 bootstrap → 判决 → 报告 dict。

    ``per_code`` 可注入（测试合成数据）；None = 生产预热。空结果护栏：
    任一变体 0 交易 → RuntimeError（不落盘——防误读为「候选被杀」）。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import exit_campaign as ec  # noqa: PLC0415
    from custos.research.evolution import exit_genome as eg  # noqa: PLC0415

    genome = parse_genome_key(args.genome)
    report = json.loads(Path(args.campaign_report).read_text(encoding="utf-8"))
    camp_key = (report.get("best_candidate") or {}).get("key")
    if camp_key != args.genome:
        raise RuntimeError(
            f"候选键对账不符：CLI {args.genome!r} vs 战役报告 {camp_key!r}（防跑错候选）"
        )
    yard = combined_yardstick(report)

    if per_code is None:
        check_reach(args.count, args.start)
        per_code = _warm_pre2019(args)
    params = {**eg.FIXED_PARAMS, **genome}
    base = {**eg.FIXED_PARAMS, **eg.baseline_genome()}
    cand_trades = _replay_variant(per_code, params, args.cost_bps)
    base_trades = _replay_variant(per_code, base, args.cost_bps)
    if not cand_trades or not base_trades:
        raise RuntimeError(
            f"C5 空结果护栏：候选 {len(cand_trades)} / 基准 {len(base_trades)} 笔"
            "——0 交易不许落盘（防误读为「候选被杀」）"
        )

    cands_c = [t for t in cand_trades]
    cands_b = [t for t in base_trades]
    rd_c = _margin_of(cands_c)
    rd_b = _margin_of(cands_b)
    pf_c = bt.simulate_portfolio_topn(cands_c, top_n=args.top_n, **ec.V0_PORTFOLIO)
    n_taken = pf_c.get("n_taken")
    d_margin = (
        rd_c["margin"] - rd_b["margin"]
        if rd_c["margin"] is not None and rd_b["margin"] is not None
        else None
    )

    pairs = pair_trades(cand_trades, base_trades)
    boot = paired_bootstrap(pairs, seed=args.seed, n_boot=args.n_bootstrap)
    se = boot.get("se")
    verdict = apply_c5(n_taken, d_margin, yard, boot.get("ci95"))
    _wu_k = wu.record_use("R37-C5", "pre2019", args.tag, "C5 pre2019 终审")
    from custos.research import provenance as pv  # noqa: PLC0415

    rep = {
        "version": 1,
        "tag": args.tag,
        "window_usage": {
            "window": "pre2019",
            "k": _wu_k,
            "note": wu.usage_note("R37-C5", "pre2019", _wu_k),
        },
        "provenance": pv.build(
            args,
            unit="R37-C5",
            criteria_version="v0.299/v0.324",
            pre_reg_doc=RESEARCH_DIR / "R37_exit_axis_evolution_campaign.md",
        ),
        "config": {
            "genome_key": args.genome,
            "window": {"start": args.start, "end": args.end},
            "codes_file": str(args.codes_file),
            "count": args.count,
            "cost_bps": args.cost_bps,
            "top_n": args.top_n,
            "n_bootstrap": args.n_bootstrap,
            "seed": args.seed,
            "criteria": "R37-C5 v0.299（a_sample 可疑闸：n<100 ⇒ 跑数可疑不出判决；thr 三分：CI95 hi<thr 杀/lo>0 且点估计≥thr 活/其余 untested；thr=bar（n≥200）否则 0；v0.276 γ 分档 degraded 0.75/否则 0.5；合并标尺自含读取）",
        },
        "yardstick": yard,
        "candidate": {**rd_c, "n_taken": n_taken},
        "baseline": rd_b,
        "d_margin": d_margin,
        "delta_over_se": (d_margin / se if d_margin is not None and se else None),
        "bootstrap": {**boot, "n_pairs": len(pairs)},
        "n_unpaired": len(cand_trades) - len(pairs),
        "kill": verdict,
        "note": "全过也只记「C5 未否决」——本工具只能杀不能确认；判决表达 Δ/SE，不压二值",
    }
    if verdict["sample_suspicious"]:
        rep["warning"] = (
            f"n_taken={n_taken} 低于预期下限 {MIN_N_TAKEN} ⇒ 本次跑数判为可疑"
            "（数据完整性信号；check_reach 只守加载截断一种成因，宇宙/信号集/"
            "未来数据缺陷不在保护范围），不出判决——排查数据后重跑"
        )
    return rep


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = _build_parser()
    args = ap.parse_args(argv)
    args.cmdline = " ".join(argv) if argv is not None else " ".join(sys.argv[1:])
    _check_pre2019(args, ap)
    try:
        rep = run_c5(args)
    except (RuntimeError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 2
    out_dir = LOGS / "exit_campaign" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_exit_c5__{args.tag}.json"
    # 研究产物允许 NaN（区别于生产侧 paths.write_json 的 allow_nan=False）
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    k = rep["kill"]
    ds = rep["delta_over_se"]
    dm = f"{rep['d_margin']:+.6f}" if rep["d_margin"] is not None else "None"
    ds_txt = f"（{ds:+.2f}·SE）" if ds is not None else ""
    print(
        f"[C5] verdict={k['verdict']} "
        f"fired={[f['clause'] for f in k['fired']]} Δmargin={dm}{ds_txt}"
    )
    if "warning" in rep:
        print(f"[C5] ⚠️ WARNING: {rep['warning']}", file=sys.stderr)
    print(f"[C5] 报告 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
