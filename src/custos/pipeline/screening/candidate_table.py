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
from typing import Any, Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import PLANS, QUALITY_DIR, STOCK_POOL_DIR, daily_report_dir  # noqa: E402
from custos.core.runtime_guards import normalize_regime  # noqa: E402
from custos.core import report_audit  # noqa: E402
from custos.pipeline.market_timing import amv_context  # noqa: E402  L3 同层，只描述

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
    """单因子行：命中/可评 + 盈亏比/胜率（R27 读数）+ 命中名单（按技术分降序，全列不截断）。无可评估且无命中返回 None。"""
    from custos.pipeline.screening import signal_labels as sl  # noqa: PLC0415

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
    # 2026-08-16（owner）：改表格 + 标技术分——命中名单按技术分降序，
    # 括号内是该票当日技术分（总分=技术分），一眼看出「命中的是强票还是弱票」。
    # v0.169（owner）：命中名单完整列出，不再截断前 12。
    top_hits = sorted(hits, key=lambda c: (-(c.get("score") or 0), str(c.get("code"))))
    names = "、".join(f"{_sig_nm(c)}({int(c.get('score') or 0)})" for c in top_hits)
    stats = sl.SIGNAL_STATS.get(key)
    # 格内空格换 &nbsp;：整格不换行（换行会让该列被压到最窄），顺带把列撑宽。
    stat_cell = (
        f"{stats[0][0]:.2f}&nbsp;/&nbsp;{stats[0][1] * 100:.0f}%&nbsp;｜&nbsp;"
        f"{stats[1][0]:.2f}&nbsp;/&nbsp;{stats[1][1] * 100:.0f}%"
        if stats
        else "—"
    )
    return (
        f"| {mark}**{label}** `{abbr}` | {len(hits)}/{evaluable} | {stat_cell} "
        f"| {names or '无'} |"
    )


def _signal_labels_section(candidates: list[dict]) -> list[str]:
    """信号标注一览：**逐个标注列出命中的票**（而不是只报几只）。

    设计边界：这些研究因子（QSX共振、RSI 区间、B2、底部异动…）**只标注，
    不参与打分分层**，上方候选池的分层与 next_step 完全未被改写。

    ⚠️ 它们**已在跨窗终审中被否决**（治理文档「H1/H2 终审」）：edge 只存在于 2025-2026
    单一 regime。所以这个区块是**观察记录，不是交易依据**，尤其不得据命中数定仓位。

    分母是**可评估数**（排除数据不足的票）：`min_list_days=60` 而 `qsx_resonance_v2` 需
    ≥114 根（DKS 成形）、`surge_then_b1` 需 200 根，大量候选算不出来。把"算不出来"
    混进分母会让"数据不足"被误读成"不符合条件"。
    （v0.169 owner：⚠出货行不列、解释文字不列、命中名单全列不截断；
    v0.185 owner：撤 MR（主升始发点）行；新增「盈亏比/胜率」列（R27 交易层双窗读数），
    行序改按跨窗盈亏比降序，无同口径数据的行垫底显示 "—"。）
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
        "## 🏷️ 信号标注一览（研究因子·只标注，不影响判定）",
        "",
        "> 盈亏比/胜率列是 R27 交易层双窗读数（trade-sim：0AMV做多+J<13 基底、s3000、"
        "pct12+分批止盈+BBI跌破2根出场；跨窗 2022-2024｜主窗 2024-08~2026-09），"
        "供相对参考，非 live 统计；无同口径数据的显示 —。",
        "",
        # 前三列表头用 &nbsp; 连成不可断行单元：Markdown 表格没有列宽语法，渲染器
        # 会把宽度让给末列长名单，把这三列挤到逐字换行；&nbsp; 是各渲染器（VS Code
        # 预览/Typora/GitHub）都生效的最小列宽手段。digest 侧 md_to_digest 已剥掉。
        "| &nbsp;&nbsp;&nbsp;&nbsp;因&nbsp;子&nbsp;&nbsp;&nbsp;&nbsp; "
        "| &nbsp;&nbsp;命&nbsp;中&nbsp;/&nbsp;可&nbsp;评&nbsp;&nbsp; "
        "| 盈&nbsp;亏&nbsp;比&nbsp;/&nbsp;胜&nbsp;率&nbsp;（&nbsp;跨&nbsp;窗&nbsp;｜&nbsp;主&nbsp;窗&nbsp;） "
        "| 命中候选（按技术分降序；括号内为技术分） |",
        "|---|---:|---:|---|",
    ]
    # v0.185（owner）：行序按跨窗盈亏比降序（无读数的垫底），不再是 SIGNAL_META 序。
    rows = []
    for key, meta in (
        (k, sl.SIGNAL_META[k]) for k in sl.LIVE_SIGNALS
    ):  # v0.529：只列 live 保留项
        # SG（底部异动）不单列（2026-08-14 owner 反馈两行名单每次完全相同）：
        # SB = SG ∧ 当日 J<13，而本池已过 J<13 硬门槛 ⇒ 池内 SG 与 SB 恒重合，
        # 单列只是噪声。两者算法**不同**（见 b2_surge_factor），重合是本池结构使然。
        if key == "bottom_surge":
            continue
        # ⚠出货（主力出货形态）一览行不列（v0.169 owner 撤）——主表单元格 ⚠️ 负向标记保留。
        if key == "distribution_risk":
            continue
        row = _signal_label_row(key, meta, with_sig)
        if row is not None:
            stats = sl.SIGNAL_STATS.get(key)
            sort_key = -stats[0][0] if stats else 0.0
            rows.append((sort_key, key, row))
    rows.sort(key=lambda r: (r[0], r[1]))
    lines.extend(r[2] for r in rows)
    lines.append("")
    return lines


def _signal_cell(cand: dict) -> str:
    """主表「标注」单元：`4/9 QG·RS·B2` + 负向 ⚠️（分母 = 正向标注总数）。"""
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


def _names_or_none(cands: list[dict]) -> str:
    """「代码 名称」顿号串联，空列表显示「无」。"""
    return "、".join(_sig_nm(c) for c in cands) or "无"


def _rd_hit(c: dict) -> bool:
    """RD（RSI 深水区）证据命中判定——三态 fail-closed：只有 ``hit`` 亮灯，
    ``miss`` / ``unavailable``（数据不足）都不亮，绝不把「不知道」显示成「命中」。"""
    return ((c.get("signals") or {}).get("rsi_deep_oversold") or {}).get(
        "state"
    ) == "hit"


def _action_names_or_none(cands: list[dict]) -> str:
    """行动区名单（可买/观察价位）：RD 证据命中的候选名后亮 ⚡RD 灯。

    v0.229（TODO #64，owner 拍板接线方式=门内提醒）：只在**已过线进入行动
    视野**的候选上亮灯——不动分层、不动技术分、不动 gate 过滤；RD 标注层
    （signal_labels）保持现状。
    """
    return (
        "、".join(f"{_sig_nm(c)} ⚡RD" if _rd_hit(c) else _sig_nm(c) for c in cands)
        or "无"
    )


def _by_status(candidates: list[dict], status: str) -> list[dict]:
    """b1_status == status 的候选，按技术分降序（同分按代码）。"""
    return sorted(
        (c for c in candidates if c.get("b1_status") == status),
        key=lambda c: (
            -((c.get("score_detail") or {}).get("technical_score") or 0),
            str(c.get("code")),
        ),
    )


def _signal_overview(lines, is_bear, candidates):
    """⭐ 今日信号一览（v0.529，owner 2026-11-16，TODO #124）：按 B1 规则状态 ``b1_status`` 分组。

    可买 = 0AMV 做多 ∧ J<13 ∧ QSX>DKX ∧ 无风控否决；观察价位 = 同上但 QSX≤DKX；
    待0AMV做多 = 0AMV 非做多、其余都满足。⚠️ 空头 regime 先打禁买提示。
    """
    _buy = _by_status(candidates, "可买")
    _obs = _by_status(candidates, "观察价位")
    _wait = _by_status(candidates, "待0AMV做多")
    lines.append("## ⭐ 今日信号一览")
    lines.append("")
    if is_bear:
        lines.append(
            "> **0AMV 空头：今日无可买信号（纪律：空头不买）**。待 0AMV 转多后看升级。"
        )
        lines.append("")
    lines.append(
        f"- **可买（0AMV 做多 + J<13 + QSX>DKX + 无风控否决）**：{_action_names_or_none(_buy)}"
    )
    lines.append(f"- **观察价位（同上但 QSX≤DKX）**：{_action_names_or_none(_obs)}")
    if (
        _wait
    ):  # v0.543（owner 2026-11-18）：名单常有大片，只给计数，明细看总表「状态」列
        lines.append(f"- **待0AMV做多**：{len(_wait)} 只（明细见候选总表「状态」列）")
    if any(_rd_hit(c) for c in [*_buy, *_obs]):
        lines.append(
            "> ⚡RD = RSI 深水区证据命中（R21 跨窗证据：深水 RSI 四档出场压基底 "
            "+26~+42pp）——**仅提示，不改判定**。"
            "advisory：R21 跨窗显示 deep 配 12% 宽止损最优，口径与 live 现行 "
            "−7%/−10% 不同，是否联动另行拍板（本轮不启动任何止损联动）。"
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


_STATUS_DISP = {
    "可买": "🐂可买",
    "观察价位": "🐂观察价位",
    "待0AMV做多": "待0AMV做多",
    "受限": "🔍受限·非可买",
}


#: 候选总表一级排序：可买 → 观察价位 → 待0AMV做多 → 受限 → 其余
STATUS_ORDER: dict[str, int] = {"可买": 0, "观察价位": 1, "待0AMV做多": 2, "受限": 3}


def _status_of(c: dict) -> str:
    """单表「状态」列：直接显示 score_candidates 的 B1 规则状态 ``b1_status``（v0.529）。"""
    return _STATUS_DISP.get(str(c.get("b1_status") or ""), "-")


def _trigger_cell(c: dict) -> str:
    """触发：公式命中 · 模式标签 · CZ 标签 · 波浪（合成一格，空项不显示）。"""
    parts = [
        "、".join(c.get("formula_hits") or []),
        _pool_tags(c) if _pool_tags(c) != "-" else "",
        _cz_tags(c) if _cz_tags(c) != "-" else "",
        WAVE_LABELS.get(str((c.get("wave") or {}).get("wave_type")), ""),
    ]
    return " · ".join(p for p in parts if p) or "-"


def _evidence_cell(c: dict) -> str:
    """证据标记（只标注，不进判定）：命中才显示，未命中不占位。"""
    detail = c.get("score_detail") or {}
    fit = detail.get("factor_contrib", {}).get("perfect_b1_fit")
    sr = (c.get("s_reversal") or {}).get("s_reversal")
    pp = (c.get("platform_pullback") or {}).get("platform_high")
    sw = (c.get("swing_state") or {}).get("state")
    tags = [
        sw or "",  # v0.561 走势三态 v4（只展示，后段约 2/3 准确）
        f"贴合{fit}" if fit else "",
        "ADX25" if c.get("adx25") else "",
        f"S反转{sr}" if sr is not None else "",
        "W底" if (c.get("w_bottom") or {}).get("hit") else "",
        "红肥绿瘦" if (c.get("red_fat_green_thin") or {}).get("hit") else "",
        f"平台回踩✓@{_fmt(pp)}" if pp else "",
    ]
    return "、".join(t for t in tags if t) or "-"


#: 单表列含义（v0.478 改版；口径以代码为准，改判定须同步本表）
COLUMN_LEGEND = [
    (
        "板块",
        "TDX 细分行业（缺则主题族）+ 板块状态（只提示不进分）；🔥N = 近 10 个交易日进细分行业当日涨幅前十 N 次",
    ),
    (
        "基本面",
        "公司品质档 优/中/差（当前快照，非 PIT；⚠三无=三无标记）",
    ),
    (
        "技术分",
        "八段 patterns 累加分（score_candidates.technical_score）；**未校准启发式**，R6/R19/R42 证排序无效，只作参考；总表按状态排序，同状态内才按技术分",
    ),
    (
        "状态",
        "B1 规则（v0.529）：🐂可买 = 0AMV 做多 + J<13 + QSX>DKX + 无风控否决；🐂观察价位 = 同上但 QSX≤DKX；待0AMV做多 = 0AMV 非做多、其余满足；🔍受限 = B1 点但被风控否决（冲刺波 / 出货 high / MACD 三打白骨精；疑似一波流、出货 watch 只标记）",
    ),
    (
        "触发",
        "公式命中 · 模式标签（BBI上/低J/缩量/反转K/强RS）· CZ 标签（五日/龙头量/底部巨量/撤退）· 波浪（建仓/拉升/冲刺）",
    ),
    (
        "证据标记",
        "只标注不进判定：开头的走势（如「上涨转下跌」）=B1 前 120 根最近 1~2 段（震荡上涨 / 横盘震荡 / 震荡下跌，有转折记 X转Y；未校准完，约 2/3 准确）；贴合=完美 B1 贴合分；ADX25；S反转=S 型反转分；W底；红肥绿瘦；平台回踩✓@平台高（平台高即自然止损位）",
    ),
    (
        "研究标注",
        "研究标注（v0.529 只留 RD = RSI 深水区、SB = 异动后的 B1）：命中数/可评 + 缩写；只作观察记录，不进判定",
    ),
    (
        "风险",
        "风控标记（中文显示）：带「否决」的会让状态变为受限；其余只是提醒（如出货观察、MACD 顶背离减分、量能退潮）",
    ),
]


#: 风险列中文（owner 2026-11-18；stock_pool 产物仍存英文键，只改展示）
RISK_ZH: dict[str, str] = {
    "distribution_high": "出货（高，否决）",
    "distribution_watch": "出货（观察）",
    "distribution_detected_cap_disabled": "出货（已检出，封顶关闭）",
    "macd_three_peaks": "MACD 三打白骨精（否决）",
    "macd_top_divergence": "MACD 顶背离（减分）",
    "macd_overextended": "MACD 乖离过大",
    "macd_divergence_detected_cap_disabled": "MACD 背离（已检出，封顶关闭）",
    "non_one_wave_revoked": "疑似一波流 / 出货（否决）",
    "non_one_wave_revoked_cap_disabled": "疑似一波流 / 出货（提醒）",
    "one_wave": "一波流（提醒）",
    "one_wave_v3": "一波流（提醒，v3 旧口径）",
    "sprint_wave_first_b1_forbidden": "冲刺波首个 B1（否决）",
    "sprint_wave_detected_cap_disabled": "冲刺波（已检出，封顶关闭）",
    "main_force_retreat": "量能退潮",
    "main_force_retreat_cap_disabled": "量能退潮（仅标记）",
    "low_liquidity": "流动性偏低",
    "is_holding": "已持仓",
    "sector_score_unavailable": "板块分缺失",
}


def _risk_cell(c: dict) -> str:
    return (
        "、".join(RISK_ZH.get(str(f), str(f)) for f in (c.get("risk_flags") or []))
        or "-"
    )


def _unified_table(lines, candidates, is_bear, hot_hits=None):
    """v0.478（owner 2026-11-03）：去掉 A/B/C/D 分池与 Top5，全部候选一张表（技术分降序）。
    v0.529：状态列 = B1 规则状态；资金意图列去掉（TODO #124）。"""
    rows = sorted(
        candidates,
        key=lambda c: (
            STATUS_ORDER.get(str(c.get("b1_status") or ""), len(STATUS_ORDER)),
            -((c.get("score_detail") or {}).get("technical_score") or 0),
            str(c.get("code")),
        ),
    )  # v0.544（owner 2026-11-18）：状态一级、技术分二级（技术分未校准，R19/R42）
    lines.append(f"## 📋 候选总表（{len(rows)} 只，按状态排序，同状态内按技术分降序）")
    lines.append("")
    if not rows:
        lines.append("（空）")
        lines.append("")
        return
    lines.append(
        "| 代码 | 名称 | 板块 | 基本面 | 技术分 | 状态 | 触发 | 证据标记 | 研究标注 | 风险 |"
    )  # v0.541 列序（owner 2026-11-18）：板块、基本面前移
    lines.append("|---|---|---|---|---:|---|---|---|---|---|")
    for c in rows:
        sector = c.get("industry") or c.get("sector", "未知")
        if hot_hits and sector in hot_hits:
            sector = f"🔥{hot_hits[sector]} {sector}"  # v0.529：近 10 日进涨幅前十次数
        state = (c.get("sector_heat_filter") or {}).get("sector_state")
        lines.append(
            f"| {c.get('code')} | {c.get('name')}"
            f" | {sector}{f'（{state}）' if state else ''}"
            f" | {_fq_disp(c)}"
            f" | {_fmt((c.get('score_detail') or {}).get('technical_score'))}"
            f" | {_status_of(c)}"
            f" | {_trigger_cell(c)}"
            f" | {_evidence_cell(c)}"
            f" | {_signal_cell(c)}"
            f" | {_risk_cell(c)} |"
        )
    lines.append("")


def _legend_section() -> list[str]:
    out = ["## 📖 列含义", ""]
    out += [f"- **{k}**：{v}" for k, v in COLUMN_LEGEND]
    out.append("")
    return out


def _header_lines(
    pool: dict,
    date: str,
    context_lines: Optional[Callable[[str, str], list[str]]] = None,
) -> list[str]:
    """表头 + 可审计块 + 选股链状态行；背景由生产入口显式注入。"""
    # 可审计块（原待办 #29，已实现）：本表实际读过的输入（stock_pool 本体 + 门控结论）
    audit = report_audit.build(
        date,
        "candidate_table",
        [
            STOCK_POOL_DIR / f"{date}_stock_pool.json",
            QUALITY_DIR / f"{date}_runtime_gate.json",
        ],
    )
    amv_background = (
        context_lines(date, str(pool.get("amv_state") or ""))
        if context_lines is not None
        else []
    )
    return [
        f"# 公式选股备选池｜{date}",
        "",
        *report_audit.render_md(audit),
        f"> 选股链状态：{pool.get('status', '未知')}"
        + (f"（{pool['degraded_reason']}）" if pool.get("degraded_reason") else "")
        + f"；0AMV：{pool.get('amv_state', '未知')}；市场许可：{pool.get('market_permission', '未知')}",
        *amv_background,
        "> 本表为证据层候选，不构成买入计划；可买 / 观察价位亦须经总控与风控审批。列含义见文末。",
        "",
    ]


def render_table(
    pool: dict,
    date: str,
    gate: Optional[dict] = None,
    context_lines: Optional[Callable[[str, str], list[str]]] = None,
    hot: Optional[dict] = None,
) -> str:
    lines = _header_lines(pool, date, context_lines=context_lines)
    candidates = pool.get("candidates") or []
    # 与 score_candidates 共用同一套归一,避免"报告说空头不买、A池却仍生成买入计划"的自相矛盾
    is_bear = normalize_regime(pool.get("amv_state") or "") == "空头"
    # 🚦 门控建议:独立区块,置于信号一览之前(先知道数据可不可信,再看信号)。
    # **不改任何分层/next_step** —— 18:00 是纯粹选股流程,详见 _gate_advisory_section。
    lines += _gate_advisory_section(date, gate)
    # ⭐ 置顶:今日信号一览——可买/观察价位/待0AMV做多 三档,一眼看清"今天哪些是真信号"
    _signal_overview(lines, is_bear, candidates)
    # v0.478（owner 2026-11-03）：去掉 A/B/C/D 分池、Top5 与 🐂/🔍/📡 三个子区，全部候选并成一张总表，
    # 判定结果收进「状态」列；信号标注一览与门内提醒保留；文末列含义。
    if hot is not None:  # v0.529（TODO #125）：热门细分行业，只作标注
        from custos.pipeline.screening import hot_sectors  # noqa: PLC0415

        lines += hot_sectors.render_lines(hot)
    hot_hits = {h["industry"]: h["hits"] for h in (hot or {}).get("hot") or []}
    _unified_table(lines, candidates, is_bear, hot_hits)
    lines += _signal_labels_section(candidates)
    lines += _in_gate_reminder_section(candidates)
    lines += _legend_section()
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
        "**仅提醒**，不改变判定与排序；按日 J 从小到大最多列前 "
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
    out.append("| 代码 | 名称 | 日J | 涨跌幅 | 异动判据 | 技术分 |")
    out.append("|---|---|---:|---:|---|---:|")
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
        from custos.pipeline.screening import hot_sectors  # noqa: PLC0415

        text = render_table(
            pool,
            args.date,
            context_lines=amv_context.context_lines,
            hot=hot_sectors.hot_for(args.date),
        )  # 真正的 18:00 候选表头部（v0.515）；热门细分行业（v0.529）
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
