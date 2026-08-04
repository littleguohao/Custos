# -*- coding: utf-8 -*-
"""Screening 链第 4 段：渲染备选表格（candidate_table）。

读 ``01_data/stock_pool/{date}_stock_pool.json``，渲染
``03_daily_plans/_supporting/{date}/{date}_candidate_table.md``，
按 bucket 分组，供日报证据层引用。stock_pool 缺失时输出降级说明，
绝不报错、绝不阻塞主链。

CLI::

    uv run python 07_tools/screening/candidate_table.py --date YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import PLANS, QUALITY_DIR, STOCK_POOL_DIR  # noqa: E402
from runtime_guards import normalize_regime  # noqa: E402

PATTERN_LABELS = {
    "bbi_above": "BBI上",
    "j_low": "低J",
    "volume_contraction": "缩量",
    "reversal_k_candidate": "反转K",
    "relative_strength_strong": "强RS",
}

WAVE_LABELS = {"buildup": "建仓", "rally": "拉升", "sprint": "冲刺"}


def _cz_tags(c: dict) -> str:
    """CZ 标签紧凑拼接：五日/龙头量/底部巨量/撤退。"""
    tags = []
    if (c.get("five_day_entry") or {}).get("hit"):
        tags.append("五日")
    if (c.get("leader_volume") or {}).get("hit"):
        tags.append("龙头量")
    if (c.get("bottom_volume") or {}).get("hit"):
        tags.append("底部巨量")
    if (c.get("volume_sustain") or {}).get("status") == "retreat":
        tags.append("撤退")
    return "、".join(tags) or "-"


def _fmt(v: Any, suffix: str = "") -> str:
    if v is None:
        return "-"
    return f"{v}{suffix}"


def _mainline_fingerprint_section(candidates: list[dict]) -> list[str]:
    """🧭 当日主线指纹:候选池板块族密度榜(情境感知,**不做进场过滤**)。best-effort,数据缺失则整段跳过。"""
    codes = [str(c.get("code", "")) for c in candidates if c.get("code")]
    if not codes:
        return []
    try:
        import sector_mainstream as sm  # noqa: PLC0415,E402
        mpath = STOCK_POOL_DIR.parent / "market" / "sector_members.json"
        members = json.loads(mpath.read_text(encoding="utf-8"))
        code2secs = sm.invert_members(members, exclude_types=True)
        fp = sm.mainline_fingerprint(codes, code2secs, sizes=sm.sector_sizes(members), top_k=8)
    except Exception:  # noqa: BLE001
        return []
    top = fp.get("top") or []
    if not top:
        return []
    out = ["## 🧭 当日主线指纹（候选池板块族密度榜）", "",
           f"> 当日候选 {fp['n']} 只（有板块 {fp['n_classified']}）；前5板块占归属 "
           f"{(fp.get('top5_count_share') or 0) * 100:.0f}%。**密度榜=情境感知**（看清当前主线在哪、"
           f"共振候选是否踩在主线上），**非进场过滤**——回测已证「跟随主流」非机械 edge，仅辅助主观研判。", "",
           "| 板块 | 候选数 | 板块规模 | 密度(候选/规模) | 占归属 |",
           "|---|---:|---:|---:|---:|"]
    for r in top:
        out.append(f"| {r['name']} | {r['n']} | {r.get('size') or '-'} "
                   f"| {_fmt(r.get('density'))} | {_fmt(round((r.get('share') or 0) * 100, 1), '%')} |")
    out.append("")
    return out


def _load_json(path, default):
    """读 JSON，缺失/损坏返回 default（门控提示是补强信息，不得让渲染失败）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _signal_labels_section(candidates: list[dict]) -> list[str]:
    """信号标注一览：**逐个标注列出命中的票**（而不是只报几只）。

    设计边界：这些研究因子（QSX>DKS、RSI 区间、B2、底部异动、主升始发点…）**只标注，
    不参与打分分层**，上方候选池的分层与 next_step 完全未被改写。

    ⚠️ 它们**已在跨窗终审中被否决**（治理文档「H1/H2 终审」）：edge 只存在于 2025-2026
    单一 regime。所以这个区块是**观察记录，不是交易依据**，尤其不得据命中数定仓位。

    分母是**可评估数**（排除数据不足的票）：`min_list_days=60` 而 `qsx_gt_dks` 需 120 根、
    `surge_then_b1` 需 200 根，大量候选算不出来。把"算不出来"混进分母会让"数据不足"
    被误读成"不符合条件"。
    """
    try:
        import signal_labels as sl
    except Exception:  # noqa: BLE001
        return []
    with_sig = [c for c in candidates if isinstance(c.get("signals"), dict)]
    if not with_sig:
        return []

    def nm(c):
        return f"{c.get('code')} {c.get('name') or ''}".strip()

    lines = ["## 🏷️ 信号标注一览（研究因子·只标注，不影响上方分层）", ""]
    for key, (label, abbr, direction) in sl.SIGNAL_META.items():
        hits, evaluable = [], 0
        for c in with_sig:
            st = (c["signals"].get(key) or {}).get("state")
            if st in ("hit", "miss"):
                evaluable += 1
            if st == "hit":
                hits.append(c)
        if not evaluable and not hits:
            continue
        mark = "⚠️ " if direction < 0 else ""
        names = "、".join(nm(c) for c in hits[:12])
        if len(hits) > 12:
            names += f" 等 {len(hits)} 只"
        lines.append(f"- {mark}**{label}** `{abbr}`（{len(hits)}/{evaluable}）："
                     f"{names or '无'}")
    na_counts: dict[str, int] = {}
    for c in with_sig:
        for key in sl.SIGNAL_META:
            if (c["signals"].get(key) or {}).get("state") == "unavailable":
                na_counts[key] = na_counts.get(key, 0) + 1
    if na_counts:
        top = sorted(na_counts.items(), key=lambda x: -x[1])[:4]
        lines.append("")
        lines.append("> 数据不足（算不出来，**不等于不符合条件**）："
                     + "、".join(f"{sl.SIGNAL_META[k][0]} {v} 只" for k, v in top))
    lines.append("> 分母为**可评估数**；缩写见各行反引号。这些标注不改写分层/next_step。")
    lines.append("> ⚠️ **这些因子已在跨窗终审中被否决**（edge 仅存在于 2025-2026 单一 regime，"
                 "详见 00_governance/B1_BACKTEST_FINDINGS.md「H1/H2 终审」）："
                 "本区块是**观察记录，不是交易依据**，不得据命中数决定仓位。")
    lines.append("")
    return lines


def _signal_cell(cand: dict) -> str:
    """主表「标注」单元：`4/11 QD·RS·SG` + 负向 ⚠️。"""
    sig = cand.get("signals")
    if not isinstance(sig, dict):
        return "-"
    sm = sig.get("summary") or {}
    parts = [str(sm.get("label") or "-")]
    if sm.get("abbrs"):
        parts.append("·".join(sm["abbrs"]))
    if sm.get("neg_abbrs"):
        parts.append("⚠️" + "·".join(sm["neg_abbrs"]))
    return " ".join(parts)


def _gate_advisory_section(date: str, gate: Optional[dict] = None) -> list[str]:
    """运行门控**建议**区块（独立于选股结果）。

    设计边界（2026-08-03 定）：18:00 是纯粹的选股流程，门控**不得影响选股结果**——
    不改 bucket、不改 next_step、不改分层、不筛掉任何候选，只在表里单独给出建议。

    这样定的三个理由：
      ① 选股结果保持与回测同口径。若门控改写分层，live 选出的候选就无法与回测结果
         对照，"策略本身选出了什么"变得不可回溯。
      ② 职责分离：选股逻辑不混入运行时数据质量判断。
      ③ 可复现：同一天重跑，候选表不因数据新鲜度而变。

    门控通过时不占版面（返回空），只在有受限项时提示。
    """
    if gate is None:
        gate = _load_json(QUALITY_DIR / f"{date}_runtime_gate.json", {})
    if not isinstance(gate, dict) or not gate:
        return ["> ⚠️ 运行门控结论缺失（未跑 runtime_gate）：无法评估当日数据可信度，"
                "本表候选仍为策略选股结果，请自行核实行情与 0AMV 新鲜度。", ""]
    mq = gate.get("market_quality") or {}
    pg = gate.get("position_gate") or {}
    status = mq.get("status")
    limitations = list(mq.get("limitations") or [])
    if status in {"pass"} and not limitations:
        return []                              # 数据齐全，不占版面

    lines = ["## 🚦 数据可信度提示（门控建议·不影响上方选股结果）", ""]
    lines.append(f"> 市场数据质量：**{status or '未知'}**"
                 f"（score={mq.get('quality_score', 'NA')}）"
                 f"；0AMV 新鲜：**{'是' if mq.get('amv_ok') else '否'}**")
    if not mq.get("amv_ok"):
        lines.append("> ⚠️ **0AMV 不新鲜 ⇒ 上方 0AMV/市场许可一栏的 regime 值可能来自过期数据**，"
                     "据它判断的「空头不买」「待0AMV做多」分档相应不可全信。")
    if limitations:
        lines.append("> 受限项：" + "；".join(str(x) for x in limitations))
    if pg and not pg.get("allow_position_increase"):
        reason = "；".join(str(x) for x in (pg.get("limitations") or [])) or "门控未授权"
        lines.append(f"> 加仓授权：**未授予**（{reason}）")
    lines.append("> 本区块只作提示：候选池的分层、next_step 与信号一览均为选股链原始输出，"
                 "未被门控改写；执行力度请结合本提示自行裁量。")
    lines.append("")
    return lines


def render_table(pool: dict, date: str, gate: Optional[dict] = None) -> str:
    lines: list[str] = [
        f"# 公式选股备选池｜{date}",
        "",
        f"> 选股链状态：{pool.get('status', '未知')}"
        + (f"（{pool['degraded_reason']}）" if pool.get("degraded_reason") else "")
        + f"；0AMV：{pool.get('amv_state', '未知')}；市场许可：{pool.get('market_permission', '未知')}",
        "> 本表为证据层候选，不构成买入计划；A/B 池亦须经总控与风控审批。",
        "> 「平台回踩」列：✓@平台高 = 平台突破回踩形态命中（回踩不破前期平台高点）；平台高即自然止损位（证据层，非进场条件）。",
        "",
    ]
    counts = pool.get("bucket_counts") or {}
    candidates = pool.get("candidates") or []
    # 先看全景分组(供置顶信号一览 + 后续各区复用)
    watch_all = [c for c in candidates
                 if (c.get("fundamental_quality") or {}).get("tier") == "优"
                 and (c.get("resonance_4leg") or {}).get("sector")
                 and (c.get("resonance_4leg") or {}).get("technical")]
    _watch_key = lambda c: ((c.get("resonance_4leg") or {}).get("aligned", 0),
                            (c.get("score_detail") or {}).get("total") or 0)
    watch = sorted((c for c in watch_all if c.get("bucket") in ("A", "B")), key=_watch_key, reverse=True)
    watch_capped = sorted((c for c in watch_all if c.get("bucket") not in ("A", "B")),
                          key=_watch_key, reverse=True)
    # 与 score_candidates 共用同一套归一,避免"报告说空头不买、A池却仍生成买入计划"的自相矛盾
    is_bear = normalize_regime(pool.get("amv_state")) == "空头"
    # 🚦 门控建议:独立区块,置于信号一览之前(先知道数据可不可信,再看信号)。
    # **不改任何分层/next_step** —— 18:00 是纯粹选股流程,详见 _gate_advisory_section。
    lines += _gate_advisory_section(date, gate)
    # ⭐ 置顶:今日信号一览——可买/观察价位/待0AMV做多 三档,一眼看清"今天哪些是真信号"
    _buy = [c for c in watch if (c.get("resonance_4leg") or {}).get("bull_candidate")
            and c.get("bucket") == "A"]
    _obs = [c for c in watch if (c.get("resonance_4leg") or {}).get("bull_candidate")
            and c.get("bucket") == "B"]
    _wait = [c for c in watch if not (c.get("resonance_4leg") or {}).get("bull_candidate")]
    _nm = lambda c: f"{c.get('code')} {c.get('name') or ''}".strip()
    lines.append("## ⭐ 今日信号一览")
    lines.append("")
    if is_bear:
        lines.append("> **0AMV 空头：今日无可买信号（纪律：空头不买）**。"
                     "📡 前哨/🔍 受限区为研究观察对象，待 0AMV 转多后看升级。")
        lines.append("")
    lines.append(f"- **可买（A+四面共振）**：{('、'.join(_nm(c) for c in _buy)) or '无'}")
    lines.append(f"- **观察价位（B+四面共振）**：{('、'.join(_nm(c) for c in _obs)) or '无'}")
    lines.append(f"- **待0AMV做多（三面已共振）**：{('、'.join(_nm(c) for c in _wait)) or '无'}")
    lines.append("")
    # 🧭 当日主线指纹:候选池板块族密度榜(情境感知,非进场 gate)——置于最前,先看当前主线全貌
    lines += _mainline_fingerprint_section(candidates)
    lines += _signal_labels_section(candidates)
    lines.append("## 🐂 基本面牛股候选（共振观察区）")
    lines.append("")
    lines.append("> 基本面优 + 板块相位有利 + 技术强 = 三面已共振；再叠 0AMV做多即为可买牛股候选（🐂）。单独列出供持续观察（基本面为当前快照、非回测验证，仅辅助）。")
    lines.append("")
    if not watch:
        lines.append("（今日无基本面牛股候选）")
        lines.append("")
    else:
        lines.append("| 代码 | 名称 | 板块 | 基本面 | 4面共振 | 技术分 | 资金意图 | 分层 | 建议止损位 | 标记 |")
        lines.append("|---|---|---|---|---|---:|---|---|---:|---|")
        for c in watch:
            r4 = c.get("resonance_4leg") or {}
            if not r4.get("bull_candidate"):
                mark = "待0AMV做多"
            elif c.get("bucket") == "A":
                mark = "🐂可买"
            else:
                mark = "🐂观察价位(B)"               # 四腿命中但分层 B:next_step=观察价位,非直接可买
            lines.append(
                f"| {c.get('code')} | {c.get('name')}"
                f" | {c.get('sector', '未知')}"
                f" | {(c.get('fundamental_quality') or {}).get('tier', '-')}"
                f" | {r4.get('label', '-')}"
                f" | {_fmt((c.get('score_detail') or {}).get('technical_score'))}"
                f" | {(c.get('capital_intent') or {}).get('level', '-')}"
                f" | {c.get('bucket', '-')}"
                f" | {_fmt((c.get('stop_loss_ref') or {}).get('price'))}"
                f" | {mark} |"
            )
        lines.append("")
    if watch_capped:
        lines.append("## 🔍 共振成立但分层受限（重点研究观察·非可买）")
        lines.append("")
        lines.append("> 以下标的同样三面/四面共振成立，但被风控降档/硬封（分层 C=长期跟踪 / D=回避）——**不是可买信号，是重点研究观察对象**："
                     "若研究确认受限因素解除或误判，是潜在的最强候选。持续跟踪，不进入买入计划。")
        lines.append("")
        lines.append("| 代码 | 名称 | 板块 | 基本面 | 4面共振 | 技术分 | 分层 | 受限因素 | 建议止损位 |")
        lines.append("|---|---|---|---|---|---:|---|---|---:|")
        for c in watch_capped:
            r4 = c.get("resonance_4leg") or {}
            flags = "、".join(c.get("risk_flags") or []) or "-"
            lines.append(
                f"| {c.get('code')} | {c.get('name')}"
                f" | {c.get('sector', '未知')}"
                f" | {(c.get('fundamental_quality') or {}).get('tier', '-')}"
                f" | {r4.get('label', '-')}"
                f" | {_fmt((c.get('score_detail') or {}).get('technical_score'))}"
                f" | {c.get('bucket', '-')}"
                f" | {flags}"
                f" | {_fmt((c.get('stop_loss_ref') or {}).get('price'))} |"
            )
        lines.append("")
    # 📡 空头前哨(0AMV 空头期启用)：回测显示大量优秀股票起涨点在空头(领先 0AMV 转多 ~12 交易日,
    # 治理文档结论#11)——空头里板块/市场腿天然未到位(滞后),严格四面共振永远不会在空头触发,
    # 故空头期单列"基本面优+技术强"的提前埋伏观察对象,跟踪其板块/市场腿何时补齐。
    if is_bear:
        _watch_codes = {c.get("code") for c in watch_all}
        outposts = sorted((c for c in candidates
                           if (c.get("fundamental_quality") or {}).get("tier") == "优"
                           and (c.get("resonance_4leg") or {}).get("technical")
                           and c.get("code") not in _watch_codes),
                          key=_watch_key, reverse=True)
        if outposts:
            lines.append("## 📡 空头前哨（提前埋伏观察·非可买）")
            lines.append("")
            lines.append("> 0AMV 空头期：基本面优 + 技术强，但板块/市场腿未到位（空头里滞后属正常）——"
                         "**重点研究观察对象，不是可买信号**。回测显示赢家起涨多在空头尾部；"
                         "跟踪其板块相位何时转有利、0AMV 何时转多：两腿补齐即升级 🐂 共振区。"
                         "参与仅限人工研究确认后的小仓试错，不进入买入计划。")
            lines.append("")
            lines.append("| 代码 | 名称 | 板块 | 基本面 | 技术分 | 板块腿 | 市场腿 | 分层 | 建议止损位 |")
            lines.append("|---|---|---|---|---:|---|---|---|---:|")
            for c in outposts:
                r4 = c.get("resonance_4leg") or {}
                sec_leg = "有利" if r4.get("sector") else "未到位"
                mkt_leg = "做多" if r4.get("market") else "空头"
                lines.append(
                    f"| {c.get('code')} | {c.get('name')}"
                    f" | {c.get('sector', '未知')}"
                    f" | {(c.get('fundamental_quality') or {}).get('tier', '-')}"
                    f" | {_fmt((c.get('score_detail') or {}).get('technical_score'))}"
                    f" | {sec_leg}"
                    f" | {mkt_leg}"
                    f" | {c.get('bucket', '-')}"
                    f" | {_fmt((c.get('stop_loss_ref') or {}).get('price'))} |"
                )
            lines.append("")
    # 得分 Top5：按总分降序（跨分层），供快速浏览当日最强候选
    top5 = sorted(candidates,
                  key=lambda c: ((c.get("score_detail") or {}).get("total") or 0),
                  reverse=True)[:5]
    if top5:
        lines.append("## 得分 Top 5")
        lines.append("")
        lines.append("| 排名 | 代码 | 名称 | 总分 | 技术分 | S** | 建议 | 分层 | 公式命中 | 风险标记 |")
        lines.append("|---:|---|---|---:|---:|---:|---|---|---|---|")
        for i, c in enumerate(top5, 1):
            detail = c.get("score_detail") or {}
            lines.append(
                f"| {i} | {c.get('code')} | {c.get('name')}"
                f" | {_fmt(detail.get('total'))}"
                f" | {_fmt(detail.get('technical_score'))}"
                f" | {_fmt(c.get('s_star'))}"
                f" | {c.get('suggestion') or '-'}"
                f" | {c.get('bucket', '-')}"
                f" | {'、'.join(c.get('formula_hits') or []) or '-'}"
                f" | {'、'.join(c.get('risk_flags') or []) or '-'} |"
            )
        lines.append("")
    for bucket in ("A", "B", "C", "D"):
        rows = [c for c in candidates if c.get("bucket") == bucket]
        lines.append(f"## {bucket} 池（{counts.get(bucket, 0)} 只）")
        lines.append("")
        if not rows:
            lines.append("（空）")
            lines.append("")
            continue
        lines.append(
            "| 代码 | 名称 | 公式命中 | 模式标签 | 波浪 | CZ标签 | 技术分 | 贴合 | 资金意图 | 板块 | 板块状态 | 交易属性 | 共振 | 基本面 | 4面共振 | 平台回踩 | 标注 | 分层 | 建议止损位 | next_step |"
        )
        lines.append("|---|---|---|---|---|---|---:|---:|---|---|---|---|---|---|---|---|---|---|---|---|")
        for c in rows:
            # 未知 patterns 键（上游新增标签/脏数据）不得 KeyError 打挂整张表：
            # 用 .get 兜底并把原始键名留在表里，好让"多了个没登记的标签"看得见（审计）。
            tags = "、".join(
                PATTERN_LABELS.get(t, str(t)) for t, hit in (c.get("patterns") or {}).items() if hit
            ) or "-"
            wave = WAVE_LABELS.get((c.get("wave") or {}).get("wave_type"), "-")
            shf = c.get("sector_heat_filter") or {}
            res = c.get("resonance") or {}
            detail = c.get("score_detail") or {}
            stop = (c.get("stop_loss_ref") or {}).get("price")
            fit = (c.get("score_detail") or {}).get("factor_contrib", {}).get("perfect_b1_fit")
            cap_intent = (c.get("capital_intent") or {}).get("level", "-")
            fq = c.get("fundamental_quality") or {}
            fq_disp = (fq.get("tier", "-") or "-") + ("⚠三无" if fq.get("sanwu") else "")
            r4 = c.get("resonance_4leg") or {}
            r4_disp = (r4.get("label", "-") or "-") + ("🐂" if r4.get("bull_candidate") else "")
            pp = c.get("platform_pullback") or {}
            pp_disp = (f"✓@{_fmt(pp.get('platform_high'))}" if pp.get("platform_high") else "-")
            lines.append(
                f"| {c.get('code')} | {c.get('name')}"
                f" | {'、'.join(c.get('formula_hits') or []) or '-'}"
                f" | {tags}"
                f" | {wave}"
                f" | {_cz_tags(c)}"
                f" | {_fmt(detail.get('technical_score'))}"
                f" | {_fmt(fit)}"
                f" | {cap_intent}"
                f" | {c.get('sector', '未知')}"
                f" | {shf.get('sector_state', '未知')}"
                f" | {c.get('trade_style', '-')}"
                f" | {res.get('resonance_level', '-')}"
                f" | {fq_disp}"
                f" | {r4_disp}"
                f" | {pp_disp}"
                f" | {_signal_cell(c)}"
                f" | {bucket}"
                f" | {_fmt(stop)}"
                f" | {c.get('next_step', '-')} |"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="screening 链第 4 段：渲染备选表格（证据层）")
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    pool_path = STOCK_POOL_DIR / f"{args.date}_stock_pool.json"
    try:
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pool = None

    if pool is None:
        text = (
            f"# 公式选股备选池｜{args.date}\n\n"
            "> 当日未运行选股链（stock_pool.json 缺失或不可解析）。\n"
        )
        status = "missing_pool"
    else:
        text = render_table(pool, args.date)
        status = pool.get("status", "ok")

    out_dir = PLANS / "_supporting" / args.date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.date}_candidate_table.md"
    out_path.write_text(text, encoding="utf-8")

    print(json.dumps({"date": args.date, "status": status, "output": str(out_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
