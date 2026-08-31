# -*- coding: utf-8 -*-
"""Screening 链第 4 段：渲染备选表格（candidate_table）。

读 ``data/stock_pool/{date}_stock_pool.json``，渲染
``artifacts/reports/daily/{date}/{date}_1800_candidate_table.md``
（2026-08-12 起按日期目录归档，废除 _supporting；2026-08-29 起文件名带 18:00 时点标记），
按 bucket 分组，供日报证据层引用。stock_pool 缺失时输出降级说明，
绝不报错、绝不阻塞主链。

CLI::

    uv run python src/custos/pipeline/screening/candidate_table.py --date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import PLANS, QUALITY_DIR, STOCK_POOL_DIR, daily_report_dir  # noqa: E402
from custos.core.runtime_guards import normalize_regime  # noqa: E402
from custos.core import report_audit  # noqa: E402

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


def _load_json(path, default):
    """读 JSON，缺失/损坏返回 default（门控提示是补强信息，不得让渲染失败）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _sig_nm(c: dict) -> str:
    return f"{c.get('code')} {c.get('name') or ''}".strip()


def _signal_label_row(key: str, meta: tuple, with_sig: list[dict]) -> Optional[str]:
    """单因子行：命中/可评 + 命中名单（按技术分降序前12）。无可评估且无命中返回 None。"""
    label, abbr, direction = meta
    hits, evaluable = [], 0
    for c in with_sig:
        st = (c["signals"].get(key) or {}).get("state")
        if st in ("hit", "miss"):
            evaluable += 1
        if st == "hit":
            hits.append(c)
    if not evaluable and not hits:
        return None
    mark = "⚠️ " if direction < 0 else ""
    # 2026-08-16（owner）：改表格 + 标技术分--命中名单按技术分降序取前 12，
    # 括号内是该票当日技术分（总分=技术分），一眼看出「命中的是强票还是弱票」。
    top_hits = sorted(hits, key=lambda c: (-(c.get("score") or 0), str(c.get("code"))))
    names = "、".join(
        f"{_sig_nm(c)}({int(c.get('score') or 0)})" for c in top_hits[:12]
    )
    if len(hits) > 12:
        names += f" 等 {len(hits)} 只"
    return f"| {mark}**{label}** `{abbr}` | {len(hits)}/{evaluable} | {names or '无'} |"


def _signal_labels_unavailable_note(sl, with_sig: list[dict]) -> list[str]:
    """「数据不足」补注行：unavailable 计数 top4；无则返回空。"""
    na_counts: dict[str, int] = {}
    for c in with_sig:
        for key in sl.SIGNAL_META:
            if (c["signals"].get(key) or {}).get("state") == "unavailable":
                na_counts[key] = na_counts.get(key, 0) + 1
    if not na_counts:
        return []
    top = sorted(na_counts.items(), key=lambda x: -x[1])[:4]
    return [
        "",
        "> 数据不足（算不出来，**不等于不符合条件**）："
        + "、".join(f"{sl.SIGNAL_META[k][0]} {v} 只" for k, v in top),
    ]


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
        # 包式导入失败（如模块损坏）此前被外层 except 吞掉，
        # 整个区块静默消失。降级必须留一行说明，不能无声。
        from custos.pipeline.screening import signal_labels as sl  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WARN] 信号标注区块不可用（signal_labels 导入失败: "
            f"{type(exc).__name__}: {exc}），跳过该区块",
            file=sys.stderr,
        )
        return []
    with_sig = [c for c in candidates if isinstance(c.get("signals"), dict)]
    if not with_sig:
        return []

    lines = [
        "## 🏷️ 信号标注一览（研究因子·只标注，不影响上方分层）",
        "",
        "| 因子 | 命中/可评 | 命中候选（按技术分降序，前12；括号内为技术分） |",
        "|---|---:|---|",
    ]
    for key, meta in sl.SIGNAL_META.items():
        # SG（底部异动）不单列（2026-08-14 owner 反馈两行名单每次完全相同）：
        # SB = SG ∧ 当日 J<13，而本池已过 J<13 硬门槛 ⇒ 池内 SG 与 SB 恒重合，
        # 单列只是噪声。两者算法**不同**（见 b2_surge_factor），重合是本池结构使然。
        if key == "bottom_surge":
            continue
        row = _signal_label_row(key, meta, with_sig)
        if row is not None:
            lines.append(row)
    lines += _signal_labels_unavailable_note(sl, with_sig)
    lines.append(
        "> 分母为**可评估数**；缩写见各行反引号。这些标注不改写分层/next_step。"
        "`SG`（底部异动）不单列：`SB`＝SG ∧ 当日 J<13，本池已过 J<13 硬门槛，两名单恒重合。"
    )
    lines.append(
        "> ⚠️ **这些因子已在跨窗终审中被否决**（edge 仅存在于 2025-2026 单一 regime，"
        "详见 governance/research/README.md「跨窗终审总账」）："
        "本区块是**观察记录，不是交易依据**，不得据命中数决定仓位。"
    )
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


def _gate_missing_notice() -> list[str]:
    """门控结论缺失时的降级提示行。"""
    return [
        "> ⚠️ 运行门控结论缺失（未跑 runtime_gate）：无法评估当日数据可信度，"
        "本表候选仍为策略选股结果，请自行核实行情与 0AMV 新鲜度。",
        "",
    ]


def _position_gate_note(pg: dict) -> list[str]:
    """加仓授权提示：未授权时一行；已授权/无 position_gate 时为空。"""
    if pg and not pg.get("allow_position_increase"):
        reason = (
            "；".join(str(x) for x in (pg.get("limitations") or [])) or "门控未授权"
        )
        return [f"> 加仓授权：**未授予**（{reason}）"]
    return []


def _gate_advisory_lines(mq: dict, pg: dict) -> list[str]:
    """门控有受限项时的提示区块主体；数据齐全（pass 且无受限项）返回空。"""
    status = mq.get("status")
    limitations = list(mq.get("limitations") or [])
    if status in {"pass"} and not limitations:
        return []  # 数据齐全，不占版面

    lines = ["## 🚦 数据可信度提示（门控建议·不影响上方选股结果）", ""]
    lines.append(
        f"> 市场数据质量：**{status or '未知'}**"
        f"（score={mq.get('quality_score', 'NA')}）"
        f"；0AMV 新鲜：**{'是' if mq.get('amv_ok') else '否'}**"
    )
    if not mq.get("amv_ok"):
        lines.append(
            "> ⚠️ **0AMV 不新鲜 ⇒ 上方 0AMV/市场许可一栏的 regime 值可能来自过期数据**，"
            "据它判断的「空头不买」「待0AMV做多」分档相应不可全信。"
        )
    if limitations:
        lines.append("> 受限项：" + "；".join(str(x) for x in limitations))
    lines += _position_gate_note(pg)
    lines.append(
        "> 本区块只作提示：候选池的分层、next_step 与信号一览均为选股链原始输出，"
        "未被门控改写；执行力度请结合本提示自行裁量。"
    )
    lines.append("")
    return lines


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
        return _gate_missing_notice()
    return _gate_advisory_lines(
        gate.get("market_quality") or {}, gate.get("position_gate") or {}
    )


def _bull_legs(watch: list[dict], bucket: str) -> list[dict]:
    """watch 中三面共振（bull_candidate）且分层为 bucket 的候选。"""
    return [
        c
        for c in watch
        if (c.get("resonance_4leg") or {}).get("bull_candidate")
        and c.get("bucket") == bucket
    ]


def _awaiting_bull(watch: list[dict]) -> list[dict]:
    """watch 中基本面+技术已共振、但尚未 bull_candidate 的候选。"""
    return [
        c for c in watch if not (c.get("resonance_4leg") or {}).get("bull_candidate")
    ]


def _names_or_none(cands: list[dict]) -> str:
    """「代码 名称」顿号串联，空列表显示「无」。"""
    return "、".join(_sig_nm(c) for c in cands) or "无"


def _signal_overview(lines, is_bear, watch):
    """⭐ 今日信号一览：按四面共振把候选分成可买 / 观察价位 / 待 0AMV 做多。

    2026-08-07 从 `render_table`（原 211 行）抽出。
    ⚠️ 空头 regime 下会先打一条禁买提示 —— 共振度**不能**在空头里放宽权限。
    """
    _buy = _bull_legs(watch, "A")
    _obs = _bull_legs(watch, "B")
    _wait = _awaiting_bull(watch)
    lines.append("## ⭐ 今日信号一览")
    lines.append("")
    if is_bear:
        lines.append(
            "> **0AMV 空头：今日无可买信号（纪律：空头不买）**。"
            "📡 前哨/🔍 受限区为研究观察对象，待 0AMV 转多后看升级。"
        )
        lines.append("")
    # v0.50（#37 阶段 A）：板块相位（sector_phase.favorable）移出「可买」定义——
    # 可买 = A + 市场/基本面/技术三面共振；「四面共振」降为情境标注列（4面共振列）。
    lines.append(f"- **可买（A + 市场/基本面/技术三面共振）**：{_names_or_none(_buy)}")
    lines.append(f"- **观察价位（B + 三面共振）**：{_names_or_none(_obs)}")
    lines.append(f"- **待0AMV做多（基本面+技术已共振）**：{_names_or_none(_wait)}")
    lines.append("")


def _fundamental_bulls(lines, watch):
    """🐂 基本面牛股候选（共振观察区）。三面已共振 + 0AMV 做多 = 可买。"""
    lines.append("## 🐂 基本面牛股候选（共振观察区）")
    lines.append("")
    lines.append(
        "> 基本面优 + 技术强（板块相位 v0.50 起仅作情境标注、不再计入）；再叠 0AMV做多即为可买牛股候选（🐂）。单独列出供持续观察（基本面为当前快照、非回测验证，仅辅助）。"
    )
    lines.append("")
    if not watch:
        lines.append("（今日无基本面牛股候选）")
        lines.append("")
    else:
        lines.append(
            "| 代码 | 名称 | 板块 | 基本面 | 4面共振 | 技术分 | 资金意图 | 分层 | 建议止损位 | 标记 |"
        )
        lines.append("|---|---|---|---|---|---:|---|---|---:|---|")
        for c in watch:
            r4 = c.get("resonance_4leg") or {}
            if not r4.get("bull_candidate"):
                mark = "待0AMV做多"
            elif c.get("bucket") == "A":
                mark = "🐂可买"
            else:
                mark = "🐂观察价位(B)"  # 四腿命中但分层 B:next_step=观察价位,非直接可买
            lines.append(
                f"| {c.get('code')} | {c.get('name')}"
                f" | {c.get('industry') or c.get('sector', '未知')}"
                f" | {(c.get('fundamental_quality') or {}).get('tier', '-')}"
                f" | {r4.get('label', '-')}"
                f" | {_fmt((c.get('score_detail') or {}).get('technical_score'))}"
                f" | {(c.get('capital_intent') or {}).get('level', '-')}"
                f" | {c.get('bucket', '-')}"
                f" | {_fmt((c.get('stop_loss_ref') or {}).get('price'))}"
                f" | {mark} |"
            )
        lines.append("")


def _capped_but_resonant(lines, watch_capped):
    """🔍 共振成立但分层受限 —— **重点研究观察，非可买**。

    分层受限（bucket 落到 C/D）意味着技术或资金面有硬伤，共振不覆盖它。
    """
    if watch_capped:
        lines.append("## 🔍 共振成立但分层受限（重点研究观察·非可买）")
        lines.append("")
        lines.append(
            "> 以下标的同样三面/四面共振成立，但被风控降档/硬封（分层 C=长期跟踪 / D=回避）——**不是可买信号，是重点研究观察对象**："
            "若研究确认受限因素解除或误判，是潜在的最强候选。持续跟踪，不进入买入计划。"
        )
        lines.append("")
        lines.append(
            "| 代码 | 名称 | 板块 | 基本面 | 4面共振 | 技术分 | 分层 | 受限因素 | 建议止损位 |"
        )
        lines.append("|---|---|---|---|---|---:|---|---|---:|")
        for c in watch_capped:
            r4 = c.get("resonance_4leg") or {}
            flags = "、".join(c.get("risk_flags") or []) or "-"
            lines.append(
                f"| {c.get('code')} | {c.get('name')}"
                f" | {c.get('industry') or c.get('sector', '未知')}"
                f" | {(c.get('fundamental_quality') or {}).get('tier', '-')}"
                f" | {r4.get('label', '-')}"
                f" | {_fmt((c.get('score_detail') or {}).get('technical_score'))}"
                f" | {c.get('bucket', '-')}"
                f" | {flags}"
                f" | {_fmt((c.get('stop_loss_ref') or {}).get('price'))} |"
            )
        lines.append("")


def _bear_outposts(lines, _watch_key, candidates, is_bear, watch_all):
    """📡 空头前哨（提前埋伏观察·非可买）。只在 0AMV 空头时出现。"""
    if is_bear:
        _watch_codes = {c.get("code") for c in watch_all}
        outposts = sorted(
            (
                c
                for c in candidates
                if (c.get("fundamental_quality") or {}).get("tier") == "优"
                and (c.get("resonance_4leg") or {}).get("technical")
                and c.get("code") not in _watch_codes
            ),
            key=_watch_key,
            reverse=True,
        )
        if outposts:
            lines.append("## 📡 空头前哨（提前埋伏观察·非可买）")
            lines.append("")
            lines.append(
                "> 0AMV 空头期：基本面优 + 技术强，但板块/市场腿未到位（空头里滞后属正常）——"
                "**重点研究观察对象，不是可买信号**。回测显示赢家起涨多在空头尾部；"
                "跟踪其板块相位何时转有利、0AMV 何时转多：两腿补齐即升级 🐂 共振区。"
                "参与仅限人工研究确认后的小仓试错，不进入买入计划。"
            )
            lines.append("")
            lines.append(
                "| 代码 | 名称 | 板块 | 基本面 | 技术分 | 板块腿 | 市场腿 | 分层 | 建议止损位 |"
            )
            lines.append("|---|---|---|---|---:|---|---|---|---:|")
            for c in outposts:
                r4 = c.get("resonance_4leg") or {}
                sec_leg = "有利" if r4.get("sector") else "未到位"
                mkt_leg = "做多" if r4.get("market") else "空头"
                lines.append(
                    f"| {c.get('code')} | {c.get('name')}"
                    f" | {c.get('industry') or c.get('sector', '未知')}"
                    f" | {(c.get('fundamental_quality') or {}).get('tier', '-')}"
                    f" | {_fmt((c.get('score_detail') or {}).get('technical_score'))}"
                    f" | {sec_leg}"
                    f" | {mkt_leg}"
                    f" | {c.get('bucket', '-')}"
                    f" | {_fmt((c.get('stop_loss_ref') or {}).get('price'))} |"
                )
            lines.append("")


def _top5(lines, candidates):
    """得分 Top 5（与分层无关的纯排序视图）。

    ⚠️ v0.52（#37 阶段 C）：标注排序口径——总分=技术分（v0.50 起板块分移出），
    **未校准启发式，不是 alpha 排序**；S** 是 s_shape 展示列（v0.50 起不进分层）。
    """
    top5 = sorted(
        candidates,
        key=lambda c: (c.get("score_detail") or {}).get("total") or 0,
        reverse=True,
    )[:5]
    if top5:
        lines.append("## 得分 Top 5")
        lines.append("")
        lines.append(
            "> 排序口径：总分=技术分（v0.50 起板块分不参与），**未校准启发式、非 alpha 排序**；"
            "S**/建议为 s_shape 展示列（不进分层）。"
        )
        lines.append("")
        lines.append(
            "| 排名 | 代码 | 名称 | 总分 | 技术分 | S** | 建议 | 分层 | 公式命中 | 风险标记 |"
        )
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


def _pool_tags(c):
    # 未知 patterns 键（上游新增标签/脏数据）不得 KeyError 打挂整张表：
    # 用 .get 兜底并把原始键名留在表里，好让"多了个没登记的标签"看得见（审计）。
    return (
        "、".join(
            PATTERN_LABELS.get(t, str(t))
            for t, hit in (c.get("patterns") or {}).items()
            if hit
        )
        or "-"
    )


def _mark(value):
    """证据列命中标记：✅ 或 -。"""
    return "✅" if value else "-"


def _fq_disp(c):
    fq = c.get("fundamental_quality") or {}
    return (fq.get("tier", "-") or "-") + ("⚠三无" if fq.get("sanwu") else "")


def _r4_disp(c):
    r4 = c.get("resonance_4leg") or {}
    return (r4.get("label", "-") or "-") + ("🐂" if r4.get("bull_candidate") else "")


def _pp_disp(c):
    pp = c.get("platform_pullback") or {}
    return f"✓@{_fmt(pp.get('platform_high'))}" if pp.get("platform_high") else "-"


def _pool_row_evidence(c):
    return (
        # v0.51（#37 阶段 B）：adx25/S反转 证据列（严格证据层，不进分层）
        f" | {_mark(c.get('adx25'))}"
        f" | {_fmt((c.get('s_reversal') or {}).get('s_reversal'))}"
        # v0.56：W底/红肥绿瘦 底部侧证据列（25chuhuo 底部镜像，不进分层）
        f" | {_mark((c.get('w_bottom') or {}).get('hit'))}"
        f" | {_mark((c.get('red_fat_green_thin') or {}).get('hit'))}"
    )


def _pool_row_quality(c):
    return (
        f" | {c.get('trade_style', '-')}"
        f" | {(c.get('resonance') or {}).get('resonance_level', '-')}"
        f" | {_fq_disp(c)}"
        f" | {_r4_disp(c)}"
        f" | {_pp_disp(c)}"
    )


def _bucket_pool_row(c, bucket):
    """分层池明细表中单只候选的一行。"""
    detail = c.get("score_detail") or {}
    stop = (c.get("stop_loss_ref") or {}).get("price")
    fit = detail.get("factor_contrib", {}).get("perfect_b1_fit")
    return (
        (
            f"| {c.get('code')} | {c.get('name')}"
            f" | {'、'.join(c.get('formula_hits') or []) or '-'}"
            f" | {_pool_tags(c)}"
            f" | {WAVE_LABELS.get((c.get('wave') or {}).get('wave_type'), '-')}"
            f" | {_cz_tags(c)}"
            f" | {_fmt(detail.get('technical_score'))}"
            f" | {_fmt(fit)}"
        )
        + _pool_row_evidence(c)
        + (
            f" | {(c.get('capital_intent') or {}).get('level', '-')}"
            f" | {c.get('industry') or c.get('sector', '未知')}"
            f" | {(c.get('sector_heat_filter') or {}).get('sector_state', '未知')}"
        )
        + _pool_row_quality(c)
        + (
            f" | {_signal_cell(c)}"
            f" | {bucket}"
            f" | {_fmt(stop)}"
            f" | {c.get('next_step', '-')} |"
        )
    )


def _bucket_pool_section(lines, bucket, rows, counts):
    lines.append(f"## {bucket} 池（{counts.get(bucket, 0)} 只）")
    lines.append("")
    if not rows:
        lines.append("（空）")
        lines.append("")
        return
    lines.append(
        "| 代码 | 名称 | 公式命中 | 模式标签 | 波浪 | CZ标签 | 技术分 | 贴合 | ADX25 | S反转 | W底 | 红肥绿瘦 | 资金意图 | 板块 | 板块状态 | 交易属性 | 共振 | 基本面 | 4面共振 | 平台回踩 | 标注 | 分层 | 建议止损位 | next_step |"
    )
    lines.append(
        "|---|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    for c in rows:
        lines.append(_bucket_pool_row(c, bucket))
    lines.append("")


def _bucket_pools(lines, candidates, counts):
    """A/B/C/D 四个分层池的明细表。"""
    for bucket in ("A", "B", "C", "D"):
        rows = [c for c in candidates if c.get("bucket") == bucket]
        _bucket_pool_section(lines, bucket, rows, counts)


def _header_lines(pool: dict, date: str) -> list[str]:
    """表头 + 可审计块 + 选股链状态行。"""
    # 可审计块（原待办 #29，已实现）：本表实际读过的输入（stock_pool 本体 + 门控结论）
    audit = report_audit.build(
        date,
        "candidate_table",
        [
            STOCK_POOL_DIR / f"{date}_stock_pool.json",
            QUALITY_DIR / f"{date}_runtime_gate.json",
        ],
    )
    return [
        f"# 公式选股备选池｜{date}",
        "",
        *report_audit.render_md(audit),
        f"> 选股链状态：{pool.get('status', '未知')}"
        + (f"（{pool['degraded_reason']}）" if pool.get("degraded_reason") else "")
        + f"；0AMV：{pool.get('amv_state', '未知')}；市场许可：{pool.get('market_permission', '未知')}",
        "> 本表为证据层候选，不构成买入计划；A/B 池亦须经总控与风控审批。",
        "> 「平台回踩」列：✓@平台高 = 平台突破回踩形态命中（回踩不破前期平台高点）；平台高即自然止损位（证据层，非进场条件）。",
        "",
    ]


def _watch_key(c: dict) -> tuple:
    """共振观察区排序键：先按对齐腿数、再按总分，均降序。"""
    return (
        (c.get("resonance_4leg") or {}).get("aligned", 0),
        (c.get("score_detail") or {}).get("total") or 0,
    )


def _resonant_watch(candidates: list[dict]) -> list[dict]:
    """全景分组：基本面优 + 板块腿 + 技术腿到位的候选（供置顶信号一览 + 后续各区复用）。"""
    return [
        c
        for c in candidates
        if (c.get("fundamental_quality") or {}).get("tier") == "优"
        and (c.get("resonance_4leg") or {}).get("sector")
        and (c.get("resonance_4leg") or {}).get("technical")
    ]


def _split_watch(watch_all: list[dict]) -> tuple[list[dict], list[dict]]:
    """全景分组按分层切成（A/B 观察区, C/D 受限区），各按 _watch_key 降序。"""
    watch = sorted(
        (c for c in watch_all if c.get("bucket") in ("A", "B")),
        key=_watch_key,
        reverse=True,
    )
    watch_capped = sorted(
        (c for c in watch_all if c.get("bucket") not in ("A", "B")),
        key=_watch_key,
        reverse=True,
    )
    return watch, watch_capped


def render_table(pool: dict, date: str, gate: Optional[dict] = None) -> str:
    lines = _header_lines(pool, date)
    counts = pool.get("bucket_counts") or {}
    candidates = pool.get("candidates") or []
    # 先看全景分组(供置顶信号一览 + 后续各区复用)
    watch_all = _resonant_watch(candidates)
    watch, watch_capped = _split_watch(watch_all)
    # 与 score_candidates 共用同一套归一,避免"报告说空头不买、A池却仍生成买入计划"的自相矛盾
    is_bear = normalize_regime(pool.get("amv_state") or "") == "空头"
    # 🚦 门控建议:独立区块,置于信号一览之前(先知道数据可不可信,再看信号)。
    # **不改任何分层/next_step** —— 18:00 是纯粹选股流程,详见 _gate_advisory_section。
    lines += _gate_advisory_section(date, gate)
    # ⭐ 置顶:今日信号一览——可买/观察价位/待0AMV做多 三档,一眼看清"今天哪些是真信号"
    _signal_overview(lines, is_bear, watch)
    lines += _signal_labels_section(candidates)
    _fundamental_bulls(lines, watch)
    _capped_but_resonant(lines, watch_capped)
    # 📡 空头前哨(0AMV 空头期启用)：回测显示大量优秀股票起涨点在空头(领先 0AMV 转多 ~12 交易日,
    # 治理文档结论#11)——空头里板块/市场腿天然未到位(滞后),严格四面共振永远不会在空头触发,
    # 故空头期单列"基本面优+技术强"的提前埋伏观察对象,跟踪其板块/市场腿何时补齐。
    _bear_outposts(lines, _watch_key, candidates, is_bear, watch_all)
    # 📌 门内提醒（v0.89，owner）：池内（已过 J<13 硬门槛）且异动强的票——
    # 取代 v0.51 的门槛外观察区（J≥13 票不再单列，只进 excluded 留痕）。
    lines += _in_gate_reminder_section(candidates)
    # 得分 Top5：按总分降序（跨分层），供快速浏览当日最强候选
    _top5(lines, candidates)
    _bucket_pools(lines, candidates, counts)
    return "\n".join(lines)


# 门内提醒单行上限：异动强命中在池内可能有数百只，只列日 J 最低的前 N 只
_IN_GATE_REMINDER_TOP_N = 20


def _in_gate_reminder_section(candidates: list) -> list[str]:
    """门内提醒（v0.160，owner）：震荡池（POOL_ZHENDANG）内 J≤13 的票——
    **不再强制异动强**（v0.89-0.91 的底部巨量/放量点火条件撤除）。

    沿革：v0.89 替代「门槛外观察区」（门外票只进 excluded 留痕）；v0.90 改日 J
    升序；v0.91 作用域收回震荡池（POOL_ZHENDANG 显式过滤）。v0.160 owner：
    异动条件把大多数震荡池成员挡在提醒外，撤除——异动判据列保留作展示信息。
    """
    out = [
        "## 📌 门内提醒（震荡池 · J≤13）",
        "",
        "> 震荡池（POOL_ZHENDANG）内、J≤13 的票（v0.160 起不再要求异动强）。"
        "**仅提醒**，不改变分层与排序；按日 J 从小到大最多列前 "
        f"{_IN_GATE_REMINDER_TOP_N} 只。",
        "",
    ]
    hits = [
        c
        for c in candidates
        if "POOL_ZHENDANG" in (c.get("formula_hits") or [])
        and (c.get("daily_j") is not None and c.get("daily_j") <= 13)
    ]
    if not hits:
        out.append("（今日无）")
        out.append("")
        return out
    out.append("| 代码 | 名称 | 日J | 涨跌幅 | 异动判据 | 分层 | 技术分 |")
    out.append("|---|---|---:|---:|---|---|---:|")
    hits.sort(key=lambda c: c.get("daily_j"))
    for c in hits[:_IN_GATE_REMINDER_TOP_N]:
        triggers = "、".join(
            t
            for t, hit in (
                ("底部巨量", (c.get("bottom_volume") or {}).get("hit")),
                ("放量点火", (c.get("ignition") or {}).get("hit")),
            )
            if hit
        )
        out.append(
            f"| {c.get('code')} | {c.get('name')}"
            f" | {_fmt(c.get('daily_j'))}"
            f" | {_fmt(c.get('change_pct'))}"
            f" | {triggers or '-'}"
            f" | {c.get('bucket') or '-'}"
            f" | {_fmt((c.get('score_detail') or {}).get('technical_score'))} |"
        )
    if len(hits) > _IN_GATE_REMINDER_TOP_N:
        out.append(f"\n> 共 {len(hits)} 只命中，仅列前 {_IN_GATE_REMINDER_TOP_N} 只。")
    out.append("")
    return out


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="screening 链第 4 段：渲染备选表格（证据层）"
    )
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    pool_path = STOCK_POOL_DIR / f"{args.date}_stock_pool.json"
    try:
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pool = None

    if pool is None:
        audit_lines = report_audit.render_md(
            report_audit.build(args.date, "candidate_table", [pool_path])
        )
        text = (
            f"# 公式选股备选池｜{args.date}\n\n" + "\n".join(audit_lines) + "\n"
            "> 当日未运行选股链（stock_pool.json 缺失或不可解析）。\n"
        )
        status = "missing_pool"
    else:
        text = render_table(pool, args.date)
        status = pool.get("status", "ok")

    # 归到**目标交易日**的目录（candidate_table 的 --date 就是 target）：
    # 它是「为 target 日准备的候选表」，盘前查当日报告时自然在 target 目录下找。
    out_dir = daily_report_dir(args.date, PLANS)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.date}_1800_candidate_table.md"
    out_path.write_text(text, encoding="utf-8")

    print(
        json.dumps(
            {"date": args.date, "status": status, "output": str(out_path)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
