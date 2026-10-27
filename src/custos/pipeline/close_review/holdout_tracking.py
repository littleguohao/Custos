# -*- coding: utf-8 -*-
"""holdout 跟踪月报节（TODO #120，owner 2026-10-26）：汇总三本 live 台账，按月报告
现行规则在前向 holdout 起点（2026-09-05）之后的实际表现。

读数纪律（写死，渲染与产物都带这句）：

- **纯描述、不作判据、不改规则、不冻结候选、不读判定段（2017~2021）、不作研究用途**；
  要把这里的读数变成规则改动，必须另行在挖掘段预注册研究单元验证（同
  trade_counterfactual 的「主动优于规则」纪律）。
- 不碰研究侧窗口代码（load_window / window_usage / judgment_batch）：需要 K 线的
  两块（选股练习模拟、影子事后打分）以**子进程**调 `custos.research` 的 live 跟踪
  CLI（两者均已显式 allow_holdout，豁免先例 v0.361），本模块不 import 研究侧
  （trade_cf_section 先例）；进程内只做纯台账聚合。

数据源与口径：

- **实盘平仓**（``data/trades/master_trade_ledger.csv``）：复用 weekly_review 的
  ``parse_ledger`` / ``fifo_pair``（月报同口径，单一来源），只信 ``match_status ==
  "full"`` 的平仓单；收益 = ``pnl_pct``（毛利口径，费用不进分子分母）；按平仓日切月。
  B1 拆分按**回合级归属**（v0.459）：同一 code 的连续持仓回合（FIFO 队列从空到空）
  内所有平仓单继承该回合首笔买入日的 B1 标记——桶级 ``first_buy_date`` 在加仓后
  分批卖出时是加仓日（第二笔卖单配平到加仓 lot），直接对键会错位落 unknown。
- **选股练习台账**（``data/trades/pick_practice.jsonl``）：子进程
  ``pick_practice report --out``（入场 T+1 开盘、B1 现行出场、成本 25bps 净口径——
  与主台账毛利口径不同，报告里注明）；台账 2026-10-15 才建，整体落在 holdout 期内。
- **plan 影子台账**（``data/trades/plan_shadow_observations.jsonl``）：行计数为
  进程内纯聚合（1700 收盘口径进一致/不一致统计，1445 盘中行只报行数——同
  plan_shadow_review 口径分工）；独立事件与 Δret₅ 读数来自子进程
  ``plan_shadow_review``（0 事件时它按空结果护栏非零退出，如实标 unavailable）。
- **操作反事实**：读 trade_cf_section 同一次月报跑出的
  ``artifacts/logs/trade_counterfactual/<stem>.json``（其 stats 已被 ``--period``
  界到本月；首笔那手 FIFO 净口径）。文件不在（如 CUSTOS_SKIP_TRADE_CF=1）⇒
  该块 unavailable，不重跑。

大单/大亏口径（**不新造第二份定义**）：大单 = 单笔收益 ≥ +20%，大亏 = 单笔收益 ≤
−10%——沿用 R48/R49 预注册写死定义（governance/research/R48_tail_entry_features.md
§口径、R49 §结局），常量只在此处声明一次。

回测分布对照基线（**冻结快照**，v0.455 抄自已入库 digest
``governance/research/results/R44_trade_dist.md`` 的 ``merged.ref.dist``，产物
``_plan_width_scan__r44_scan_dist.json`` sha256 6aa2d950a2064a1f）：R44 参照档 =
0AMV 做多窗 j_low 信号集 × 现行出场（pct10 忠实口径）在挖掘 A∪B 合并上的
collect_all 分布，569,861 笔。R44 判毕 falsified 的是「宽度档胜过现行」的假设，
参照档分布本身是对现行规则的有效描述读数。基线是手工同步的冻结读数——若 R44
回填更新，需显式复核后再改这里的常量（描述对照，不在任何判定链上）。

样本标注：holdout 初期样本薄。本月平仓 < ``MIN_N_MONTH``（10）笔或 holdout 累计 <
``MIN_N_CUMULATIVE``（30）笔 ⇒ 显式标「样本不足，读数不可外推」。阈值只是描述性
提醒，不进任何判据。

产物：markdown 附录拼进 ``<month>_monthly_review.md`` 末尾（trade_cf 节之后）；
伴生 JSON 落 ``artifacts/reports/monthly/<month>_holdout_tracking.json``（月报类
产物不入 git，.gitignore 已覆盖 monthly/），落盘前 ``contracts.require``。
环境变量 ``CUSTOS_SKIP_HOLDOUT_TRACKING=1`` 整节跳过（测试默认设，见 conftest）。
fail-soft：任何子块失败只在报告留原因，绝不打断月报主体。
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.contracts import require
from custos.core.paths import cn_now, write_json
from custos.pipeline.close_review.weekly_review import fifo_pair, parse_ledger

#: 前向 holdout 起点（研究纪律：≥2026-09-05 的数据不用于选型/判定，live 跟踪正当使用）
HOLDOUT_START = "2026-09-05"

#: 大单/大亏阈值——单一来源 = R48/R49 预注册写死口径（见模块 docstring），勿再定义第二份
BIG_WIN_RET = 0.20
BIG_LOSS_RET = -0.10

#: 样本不足标注阈值（描述性提醒，不进判据）
MIN_N_MONTH = 10
MIN_N_CUMULATIVE = 30

#: 回测分布对照基线（R44 参照档冻结快照，出处与同步纪律见模块 docstring）
BASELINE_R44_REF: dict[str, Any] = {
    "source": "governance/research/results/R44_trade_dist.md · merged.ref.dist"
    "（挖掘 A∪B 合并，0AMV 做多窗 j_low 信号集 × 现行出场 pct10 参照档，"
    "collect_all；digest 回填，v0.455 冻结快照）",
    "n": 569861,
    "win_rate": 0.43622,
    "payoff_ratio": 1.657575,
    "avg_win": 0.098036,
    "avg_loss": -0.059144,
    "expectancy": 0.009421,
    "quantile_p10": -0.1121,
    "quantile_p90": 0.1245,
    "quantile_p95": 0.226,
    # 大单/大亏基线 = buckets 子表两桶之和（桶粒度点估计；trade_dist.BINS 左开右闭：
    # 「20~30%」=(20%,30%] 不含恰 +20%，「-15~-10%」=(−15%,−10%] 含恰 −10%）
    "big_win_note": "≈5.93%（buckets「20~30%」2.77% +「>30%」3.16%；桶粒度：左开，"
    "恰 +20% 落入「10~20%」桶）",
    "big_loss_note": "≈17.97%（buckets「≤−15%」1.19% +「−15~−10%」16.78%；"
    "桶粒度：右闭，含恰 −10%）",
}

#: 纪律声明进产物（先例：复盘层契约 permission_rule——「是解释不是裁决」必须在产物里）
DISCIPLINE = (
    "纯描述读数：不作判据、不改规则、不冻结候选、不读判定段（2017~2021）、"
    "不作研究用途；改规则须另行在挖掘段预注册研究单元验证"
)

SCHEMA = "holdout_tracking_monthly/v1"
ENV_SKIP = "CUSTOS_SKIP_HOLDOUT_TRACKING"
TIMEOUT_S = 900
HEADER = "## 🛰 holdout 跟踪：2026-09-05 以来现行规则实际表现（纯描述）"

#: 子进程 runner 类型：成功返回 (报告 dict, None)，失败返回 (None, 原因)
Runner = Callable[[Path, Path], tuple[Optional[dict], Optional[str]]]


# ---------------------------------------------------------------------------
# 分布统计（大单/大亏/胜率/盈亏比，全模块统一这一个函数）
# ---------------------------------------------------------------------------


def _dist_stats(rets: list[float]) -> dict[str, Any]:
    """收益列（小数口径，0.25 = +25%）→ 分布读数。

    赢 = ret > 0，win_rate 分母 = 全部笔数（与 pick_practice / trade_counterfactual
    的 _stats 口径一致；打平单计非赢）。非有限值（NaN/Inf）在入口处剔除——产物
    走 write_json（allow_nan=False），不给它留炸点。0 笔时各比率 None
    （「没算出来」不冒充 0）。
    """
    rets = [r for r in rets if math.isfinite(r)]
    n = len(rets)
    if not n:
        return {
            "n": 0,
            "win_rate": None,
            "payoff_ratio": None,
            "avg_ret": None,
            "avg_win": None,
            "avg_loss": None,
            "big_win_rate": None,
            "big_loss_rate": None,
        }
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    payoff = (
        round(avg_win / abs(avg_loss), 4)
        if (avg_win is not None and avg_loss)
        else None
    )
    return {
        "n": n,
        "win_rate": round(len(wins) / n, 4),
        "payoff_ratio": payoff,
        "avg_ret": round(sum(rets) / n, 4),
        "avg_win": round(avg_win, 4) if avg_win is not None else None,
        "avg_loss": round(avg_loss, 4) if avg_loss is not None else None,
        "big_win_rate": round(sum(1 for r in rets if r >= BIG_WIN_RET) / n, 4),
        "big_loss_rate": round(sum(1 for r in rets if r <= BIG_LOSS_RET) / n, 4),
    }


def _finite(v: Any) -> Any:
    """透传标量防护：上游研究产物落盘 allow_nan=True，可能带 NaN/Inf——本模块产物
    走 ``write_json``（allow_nan=False），一个非有限值会把整节打成 unavailable。
    非有限浮点 ⇒ None（「没算出来」不冒充数）；int/str/None 等其余原样。"""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _month_index(month: str) -> int:
    """month（YYYY-MM）是 holdout 期第几个自然月；早于起点返回 0。"""
    y0, m0 = int(HOLDOUT_START[:4]), int(HOLDOUT_START[5:7])
    y, m = int(month[:4]), int(month[5:7])
    return max(0, (y - y0) * 12 + (m - m0) + 1)


def _month_window(month: str) -> dict[str, str]:
    """``YYYY-MM`` → 完整自然月 {label, start, end}。

    与 monthly_review.month_range 同口径的纯日历换算（无策略口径）；不 import
    monthly_review 是刻意的：月报主体惰性 import 本模块挂附录，反向顶格 import
    会成环（test_no_unexpected_cycles 打红过一次，故在此留注）。
    """
    y, m = int(month[:4]), int(month[5:7])
    if not (1 <= m <= 12) or len(month) != 7:
        raise SystemExit(f"month 格式应为 YYYY-MM，收到 {month!r}")
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return {
        "label": f"{y:04d}-{m:02d}",
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


# ---------------------------------------------------------------------------
# 源 1：实盘平仓（主台账 FIFO，月报同口径）
# ---------------------------------------------------------------------------


def _closing_rows(
    base: Path, start: str, end: str
) -> tuple[list[tuple[float, str, str]], Optional[str]]:
    """[start, end] 内 full 配平平仓单 (收益小数, code, 回合首笔买入日)；台账缺 → ([], 原因)。

    回合级归属（v0.459）：第三元素 = 该笔平仓所属**持仓回合**的首笔买入日，不是本桶
    自己 FIFO 配到的最早买入日——加仓后分批卖出时，后面的卖桶配到加仓 lot，桶级
    first_buy_date 是加仓日，直接拿它对 ``_b1_flags`` 的键（回合首笔买入日）会错位
    落 unknown。回合划分（fifo_pair 平仓单列，同 code 按卖出日序）：本桶最早配平
    买入日 ≤ 上一桶卖出日 ⇒ 上一桶卖出时仍有 open lot（仓位未归零）⇒ 同回合延续；
    反之 ⇒ 新回合开启，其首桶最早配平买入日即回合首笔买入日（FIFO 必配到回合最早
    open lot）。first_buy_date 缺失的桶（match_status none）不切断回合。
    """
    ledger_path = base / "data" / "trades" / "master_trade_ledger.csv"
    trades = parse_ledger(ledger_path)
    if trades is None:
        return [], f"成交台账缺失：{ledger_path}"
    by_code: dict[str, list[dict]] = {}
    for c in fifo_pair(trades):
        by_code.setdefault(str(c.get("code") or "").zfill(6), []).append(c)
    rows: list[tuple[float, str, str]] = []
    for code, crows in by_code.items():
        crows.sort(key=lambda c: c["sell_date"])
        round_entry = ""
        prev_sell: Optional[str] = None
        for c in crows:
            fb = str(c.get("first_buy_date") or "")[:10]
            if fb and (prev_sell is None or fb > prev_sell):
                round_entry = fb  # 新回合开启
            prev_sell = c["sell_date"]
            if (
                c["match_status"] == "full"
                and c["pnl_pct"] is not None
                and start <= c["sell_date"] <= end
            ):
                rows.append((c["pnl_pct"] / 100.0, code, round_entry))
    return rows, None


def _closing_rets(
    base: Path, start: str, end: str
) -> tuple[list[float], Optional[str]]:
    """[start, end] 内 full 配平平仓单的收益列（pnl_pct % → 小数）；台账缺 → ([], 原因)。"""
    rows, err = _closing_rows(base, start, end)
    return [r for r, _c, _d in rows], err


def _b1_flags(base: Path) -> dict[tuple[str, str], Optional[bool]]:
    """逐笔 B1 合规入场标记（v0.458）：读 trade_counterfactual 历次产物的 episodes.b1_entry
    （0AMV 做多 ∧ J<13，研究侧判定；本模块不 import 研究——只读其落盘 JSON）。
    键 = (code, 回合首笔买入日)；同键多文件以修改时间最新者为准。"""
    out: dict[tuple[str, str], Optional[bool]] = {}
    d = base / "artifacts" / "logs" / "trade_counterfactual"
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json"), key=lambda q: q.stat().st_mtime):
        try:
            rep = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for e in rep.get("episodes") or []:
            if e.get("instrument", "stock") != "stock":
                continue
            k = (str(e.get("code") or "").zfill(6), str(e.get("entry_date") or "")[:10])
            b = e.get("b1_entry")
            out[k] = b if isinstance(b, bool) else None
    return out


def _b1_split(
    rows: list[tuple[float, str, str]], flags: dict[tuple[str, str], Optional[bool]]
) -> dict[str, Any]:
    g: dict[str, list[float]] = {"b1": [], "non_b1": [], "unknown": []}
    for r, c, d in rows:
        f = flags.get((c, d))
        g["b1" if f is True else ("non_b1" if f is False else "unknown")].append(r)
    return {
        "b1": _dist_stats(g["b1"]),
        "non_b1": _dist_stats(g["non_b1"]),
        "n_unknown": len(g["unknown"]),
    }


def _actual_trades_block(
    base: Path, rng: dict, unavailable: list[str]
) -> dict[str, Any]:
    month_rows, err = _closing_rows(base, rng["start"], rng["end"])
    if err:
        unavailable.append(err)
        return {"available": False, "reason": err}
    cum_rows, _ = _closing_rows(base, HOLDOUT_START, rng["end"])
    flags = _b1_flags(base)
    return {
        "available": True,
        "basis": "主台账 FIFO 配平（只信 full 单），pnl_pct 毛利口径（费用不进分子分母）；"
        "R44 基线是扣 25bps 的净口径",
        "month": {
            "window": [rng["start"], rng["end"]],
            **_dist_stats([r for r, _c, _d in month_rows]),
        },
        "since_holdout": {
            "window": [HOLDOUT_START, rng["end"]],
            **_dist_stats([r for r, _c, _d in cum_rows]),
        },
        # v0.458：R44 基线是 B1 交易集——只有 B1 合规入场组可与之对照（标记来自 trade_cf 产物）
        "b1_split": {
            "flag_source": "artifacts/logs/trade_counterfactual/*.json · episodes.b1_entry",
            "month": _b1_split(month_rows, flags),
            "since_holdout": _b1_split(cum_rows, flags),
        },
    }


# ---------------------------------------------------------------------------
# 源 2：选股练习台账（子进程 pick_practice report）
# ---------------------------------------------------------------------------


def _run_pick_practice(base: Path, out: Path) -> tuple[Optional[dict], Optional[str]]:
    """子进程跑 ``pick_practice report --out``（不 import 研究，trade_cf_section 先例）。

    ⚠️ 台账路径走研究 CLI 的缺省（paths.PICK_PRACTICE_LEDGER）：pick_practice 没有
    --ledger 参数；生产机 base == 仓库根，缺省即正确。测试不 Spawn（注入假 runner）。
    """
    cmd = [
        sys.executable,
        "-m",
        "custos.research",
        "pick_practice",
        "report",
        "--out",
        str(out),
    ]
    try:
        cp = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"选股练习报告子进程失败（{type(exc).__name__}）"
    if cp.returncode != 0 or not out.exists():
        tail = (cp.stderr or "").strip().splitlines()[-1:] or ["无输出"]
        return None, f"选股练习报告不可得：{tail[0][:200]}"
    try:
        return json.loads(out.read_text(encoding="utf-8")), None
    except ValueError as exc:
        return None, f"选股练习报告 JSON 解析失败（{exc}）"


def _pick_block_from_report(rep: dict[str, Any], month: str) -> dict[str, Any]:
    """pick_practice 报告 JSON → 本月 / holdout 累计分布（从 closed 行重算，口径统一）。"""
    closed = [r for r in rep.get("closed") or [] if r.get("status") == "closed"]
    month_rets = [
        float(r["ret"])
        for r in closed
        if str(r.get("date", "")).startswith(month) and r.get("ret") is not None
    ]
    cum_rets = [
        float(r["ret"])
        for r in closed
        if str(r.get("date", "")) >= HOLDOUT_START and r.get("ret") is not None
    ]
    return {
        "available": True,
        "basis": "T+1 开盘入场 × B1 现行出场模拟，净口径（25bps 已扣）；"
        "open/pending 不进统计；台账 2026-10-15 起，整体在 holdout 期内；"
        "按**选股日**切月（实盘块按**平仓日**切月——两块切月口径不同，勿直接对读）",
        "month": _dist_stats(month_rets),
        "since_holdout": _dist_stats(cum_rets),
        "n_open": len(rep.get("open") or []),
        "n_pending": len(rep.get("pending") or []),
    }


# ---------------------------------------------------------------------------
# 源 3：plan 影子台账（进程内行计数 + 子进程 plan_shadow_review 打分）
# ---------------------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict]:
    """台账读松惯例：坏行跳过不炸链（同 pick_practice.load_picks）。"""
    if not path.exists():
        return []
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _shadow_counts(rows: list[dict]) -> dict[str, Any]:
    """影子台账行计数：1700 收盘口径进一致/不一致（plan_shadow_review 同分工）。"""
    final = [r for r in rows if r.get("stage") == "1700"]
    disagree = [
        r
        for r in final
        if r.get("agree") is False and r.get("plan_source") not in (None, "", "default")
    ]
    return {
        "rows": len(rows),
        "rows_1445": sum(1 for r in rows if r.get("stage") == "1445"),
        "rows_1700": len(final),
        "agree_true": sum(1 for r in final if r.get("agree") is True),
        "agree_false": sum(1 for r in final if r.get("agree") is False),
        "agree_none": sum(1 for r in final if r.get("agree") is None),
        # 原始不一致行数（**未按分歧段去重**——段头独立事件口径以 plan_shadow_review 为准）
        "disagree_nondefault_rows": len(disagree),
        "disagree_distinct_codes": len({str(r.get("code")) for r in disagree}),
    }


def _run_plan_shadow_review(
    base: Path, workdir: Path
) -> tuple[Optional[dict], Optional[str]]:
    """子进程跑 ``plan_shadow_review``（holdout 豁免在案 v0.361；0 事件 ⇒ 它按
    空结果护栏退出码 2 不写产物，本函数如实把护栏消息带回去）。"""
    ledger = base / "data" / "trades" / "plan_shadow_observations.jsonl"
    cmd = [
        sys.executable,
        "-m",
        "custos.research",
        "plan_shadow_review",
        "--ledger",
        str(ledger),
        "--out-dir",
        str(workdir),
    ]
    try:
        cp = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"影子打分子进程失败（{type(exc).__name__}）"
    out = (
        workdir / "plan_shadow_review" / "_plan_shadow_review__plan_shadow_review.json"
    )
    if cp.returncode != 0 or not out.exists():
        tail = (cp.stderr or "").strip().splitlines()[-1:] or ["无输出"]
        return None, tail[0][:200]
    try:
        return json.loads(out.read_text(encoding="utf-8")), None
    except ValueError as exc:
        return None, f"影子打分 JSON 解析失败（{exc}）"


def _plan_shadow_block(
    base: Path,
    month: str,
    workdir: Path,
    review_runner: Runner,
    unavailable: list[str],
) -> dict[str, Any]:
    ledger = base / "data" / "trades" / "plan_shadow_observations.jsonl"
    rows = _load_jsonl(ledger)
    if not rows:
        unavailable.append(f"plan 影子台账缺失或为空：{ledger}")
    month_rows = [r for r in rows if str(r.get("date", "")).startswith(month)]
    cum_rows = [r for r in rows if str(r.get("date", "")) >= HOLDOUT_START]
    review: dict[str, Any]
    rep, err = review_runner(base, workdir)
    if rep is None:
        review = {"available": False, "reason": err}
    else:
        s5 = (rep.get("summary") or {}).get("5") or {}
        review = {
            "available": True,
            "n_events": _finite(rep.get("n_events")),
            "n_distinct_codes": _finite(rep.get("n_distinct_codes")),
            "n_continuation_rows": _finite(rep.get("n_continuation_rows")),
            "delta5": {
                "n": _finite(s5.get("n")),
                "mean": _finite(s5.get("mean")),
                "median": _finite(s5.get("median")),
                "win_rate": _finite(s5.get("win_rate")),
                "n_pending": _finite(s5.get("n_pending")),
            },
            "basis": "plan_shadow_review（stage=1700，独立事件=分歧段段头，"
            "Δret₅=plan−live，>0 = plan 更好）；#60 E 判据 K=20 + ≥5 只持仓",
        }
    return {
        "month_rows": _shadow_counts(month_rows),
        "since_holdout_rows": _shadow_counts(cum_rows),
        "review": review,
    }


# ---------------------------------------------------------------------------
# 源 4：操作反事实（读 trade_cf_section 同次月报跑出的 JSON，不重跑）
# ---------------------------------------------------------------------------


def _trade_cf_block(base: Path, stem: str, unavailable: list[str]) -> dict[str, Any]:
    path = base / "artifacts" / "logs" / "trade_counterfactual" / f"{stem}.json"
    if not path.exists():
        reason = f"trade_cf 产物不存在（{path.name}）——月报主体应先跑 trade_cf_section"
        unavailable.append(reason)
        return {"available": False, "reason": reason}
    try:
        rep = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        reason = f"trade_cf 产物 JSON 解析失败（{exc}）"
        unavailable.append(reason)
        return {"available": False, "reason": reason}
    s = (rep.get("summary") or {}).get("rule") or {}
    episodes = [
        e
        for e in rep.get("episodes") or []
        if e.get("closed") and e.get("instrument", "stock") == "stock"
    ]
    first_lot = [
        float(e["first_lot_ret"])
        for e in episodes
        if e.get("first_lot_ret") is not None
    ]
    rule_rets = [
        float(e["rule"]["ret"])
        for e in episodes
        if isinstance(e.get("rule"), dict) and e["rule"].get("ret") is not None
    ]
    return {
        "available": True,
        "basis": "本月口径（--period 本月）：出场对照只比首笔那手（FIFO 净收益，"
        "含 25bps 成本模型）；Δ = 首笔实际 − 首笔按规则",
        "n_episodes": _finite(s.get("n_episodes")),
        "n_closed": _finite(s.get("n_closed")),
        "total_delta_amount": _finite(s.get("total_delta_amount")),
        "actual_first_lot": _dist_stats(first_lot),
        "rule_first_lot": _dist_stats(rule_rets),
        "categories": {
            c: _finite((s.get("categories") or {}).get(c, {}).get("n"))
            for c in (
                "止损拖延",
                "该清未清",
                "过早卖出",
                "执行价差",
                "主动优于规则",
                "一致",
            )
            if c in (s.get("categories") or {})
        },
    }


# ---------------------------------------------------------------------------
# 组装与渲染
# ---------------------------------------------------------------------------


def build_report(
    base: Path,
    month: str,
    *,
    pick_runner: Optional[Runner] = None,
    shadow_runner: Optional[Runner] = None,
) -> dict[str, Any]:
    """汇总三本台账 + 回测基线对照。runner 可注入（测试全合成，不 Spawn 子进程）。"""
    pick_runner = pick_runner or _run_pick_practice
    shadow_runner = shadow_runner or _run_plan_shadow_review
    rng = _month_window(month)
    base = Path(base)
    unavailable: list[str] = []
    workdir = (
        base / "artifacts" / "cache" / "holdout_tracking"
    )  # 中间件落 cache（v0.451）
    workdir.mkdir(parents=True, exist_ok=True)

    actual = _actual_trades_block(base, rng, unavailable)

    rep, err = pick_runner(base, workdir / "pick_practice_report.json")
    if rep is None:
        unavailable.append(f"选股练习：{err}")
        pick = {"available": False, "reason": err}
    else:
        pick = _pick_block_from_report(rep, rng["label"])

    shadow = _plan_shadow_block(base, rng["label"], workdir, shadow_runner, unavailable)
    trade_cf = _trade_cf_block(base, f"{rng['label']}_monthly_review", unavailable)

    sample_notes = _sample_notes(actual, pick, trade_cf)

    return {
        "schema": SCHEMA,
        "month": rng["label"],
        "generated_at": cn_now().isoformat(timespec="seconds"),
        "holdout_start": HOLDOUT_START,
        "holdout_month_index": _month_index(rng["label"]),
        "period": {"start": rng["start"], "end": rng["end"]},
        "thresholds": {
            "big_win_ret": BIG_WIN_RET,
            "big_loss_ret": BIG_LOSS_RET,
            "threshold_source": "R48/R49 预注册写死口径（大单 ≥ +20% / 大亏 ≤ −10%）",
            "min_n_month": MIN_N_MONTH,
            "min_n_cumulative": MIN_N_CUMULATIVE,
        },
        "actual_trades": actual,
        "pick_practice": pick,
        "plan_shadow": shadow,
        "trade_cf": trade_cf,
        "baseline": {"r44_ref": BASELINE_R44_REF},
        "sample_notes": sample_notes,
        "unavailable": unavailable,
        "permission_rule": DISCIPLINE,
    }


def _sample_notes(
    actual: dict[str, Any], pick: dict[str, Any], trade_cf: dict[str, Any]
) -> list[str]:
    """样本不足显式标注（描述性提醒，不进判据）。"""
    notes: list[str] = []
    if not any(b.get("available") for b in (actual, pick, trade_cf)):
        return ["全部数据源不可用——本节只有格式没有读数"]
    if actual.get("available"):
        n_m = actual["month"]["n"]
        n_c = actual["since_holdout"]["n"]
        if n_m < MIN_N_MONTH:
            notes.append(
                f"实盘平仓本月 {n_m} 笔 < {MIN_N_MONTH}：样本不足，读数不可外推"
            )
        if n_c < MIN_N_CUMULATIVE:
            notes.append(
                f"实盘平仓 holdout 累计 {n_c} 笔 < {MIN_N_CUMULATIVE}："
                "样本不足，与回测分布的对照只作格式跑通"
            )
    if pick.get("available") and pick["since_holdout"]["n"] < MIN_N_MONTH:
        notes.append(
            f"选股练习已平仓累计 {pick['since_holdout']['n']} 笔 < {MIN_N_MONTH}：样本不足"
        )
    if trade_cf.get("available") and (trade_cf.get("n_closed") or 0) < MIN_N_MONTH:
        notes.append(
            f"trade_cf 本月已清仓回合 {trade_cf.get('n_closed') or 0} 个 < "
            f"{MIN_N_MONTH}：样本不足"
        )
    if not notes:
        notes.append("样本量过描述性门槛；仍为 live 跟踪读数，不作判据")
    return notes


def _pct(v: Any) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _num(v: Any) -> str:
    return "—" if v is None else f"{v:.2f}"


def _cmp_row(label: str, m: dict, c: dict, key: str, fmt: Callable, base: str) -> str:
    return f"| {label} | {fmt(m.get(key))} | {fmt(c.get(key))} | {base} |"


def render_markdown(rep: dict[str, Any]) -> str:
    bl = rep["baseline"]["r44_ref"]
    L: list[str] = [
        HEADER,
        "",
        f"> 本月 = {rep['month']}（holdout 第 {rep['holdout_month_index']} 个自然月，"
        f"起点 {rep['holdout_start']}）。{rep['permission_rule']}。",
        f"> 大单 = 单笔 ≥ +{BIG_WIN_RET * 100:g}%，大亏 = 单笔 ≤ {BIG_LOSS_RET * 100:g}%"
        "（口径沿用 R48/R49 预注册写死定义）。",
        "",
        "### 实盘平仓 vs 回测分布（基线 = R44 参照档，挖掘 A∪B 现行出场 "
        f"{bl['n']:,} 笔冻结快照）",
        "",
    ]
    at = rep["actual_trades"]
    if at["available"]:
        sp = at.get("b1_split") or {}
        m = (sp.get("month") or {}).get("b1") or at["month"]
        c = (sp.get("since_holdout") or {}).get("b1") or at["since_holdout"]
        mn = (sp.get("month") or {}).get("non_b1") or {}
        cn = (sp.get("since_holdout") or {}).get("non_b1") or {}
        L += [
            f"> 口径：{at['basis']}",
            "> R44 基线是 B1 交易集（0AMV 做多 + J<13）——下表只放 **B1 合规入场** 的平仓；"
            "非 B1 与全部平仓另列，不与基线对照。",
            "",
            "| 指标 | 本月 B1 | holdout 累计 B1 | R44 回测基线 |",
            "|---|---:|---:|---|",
            f"| 笔数 | {m['n']} | {c['n']} | {bl['n']:,} |",
            _cmp_row("胜率", m, c, "win_rate", _pct, _pct(bl["win_rate"])),
            _cmp_row("盈亏比", m, c, "payoff_ratio", _num, _num(bl["payoff_ratio"])),
            _cmp_row(
                "平均每笔收益", m, c, "avg_ret", _pct, f"{bl['expectancy'] * 100:+.2f}%"
            ),
            _cmp_row("大单率", m, c, "big_win_rate", _pct, bl["big_win_note"]),
            _cmp_row("大亏率", m, c, "big_loss_rate", _pct, bl["big_loss_note"]),
            "",
            "| 其余平仓（不对照基线） | 本月 | holdout 累计 |",
            "|---|---:|---:|",
            f"| 非 B1 入场笔数 / 胜率 | {mn.get('n', 0)} / {_pct(mn.get('win_rate'))} | "
            f"{cn.get('n', 0)} / {_pct(cn.get('win_rate'))} |",
            f"| 非 B1 平均每笔收益 | {_pct(mn.get('avg_ret'))} | {_pct(cn.get('avg_ret'))} |",
            f"| 无法判定 B1（缺 trade_cf 标记） | {(sp.get('month') or {}).get('n_unknown', 0)} | "
            f"{(sp.get('since_holdout') or {}).get('n_unknown', 0)} |",
            f"| 全部平仓笔数 / 胜率 | {at['month']['n']} / {_pct(at['month']['win_rate'])} | "
            f"{at['since_holdout']['n']} / {_pct(at['since_holdout']['win_rate'])} |",
            "",
        ]
    else:
        L += [f"- unavailable：{at['reason']}", ""]
    L += [
        "> ⚠️ live 是单个账户的已实现平仓（样本数十笔量级），R44 基线是挖掘段全信号 "
        "57 万笔的理论分布——口径不同（单账户择时执行 vs 全信号 collect_all），"
        "对照只作分布位置参考，不作判据。",
        "",
    ]

    pp = rep["pick_practice"]
    L += ["### 选股练习台账（owner 亲选 × B1 现行规则模拟）", ""]
    if pp["available"]:
        m, c = pp["month"], pp["since_holdout"]
        L += [
            f"> 口径：{pp['basis']}",
            "",
            "| 指标 | 本月 | 累计（台账全在 holdout 内） |",
            "|---|---:|---:|",
            f"| 已平仓笔数 | {m['n']} | {c['n']} |",
            f"| 胜率 | {_pct(m['win_rate'])} | {_pct(c['win_rate'])} |",
            f"| 盈亏比 | {_num(m['payoff_ratio'])} | {_num(c['payoff_ratio'])} |",
            f"| 大单率 | {_pct(m['big_win_rate'])} | {_pct(c['big_win_rate'])} |",
            f"| 大亏率 | {_pct(m['big_loss_rate'])} | {_pct(c['big_loss_rate'])} |",
            f"| 持有中 / 待入场 | — | {pp['n_open']} / {pp['n_pending']} 只 |",
            "",
        ]
    else:
        L += [f"- unavailable：{pp['reason']}", ""]

    sh = rep["plan_shadow"]
    mc, cc = sh["month_rows"], sh["since_holdout_rows"]
    L += [
        "### plan 影子台账（#60 并轨证据累计；一致性只用 1700 收盘口径）",
        "",
        "| 指标 | 本月 | holdout 累计 |",
        "|---|---:|---:|",
        f"| 台账行数（1445 / 1700） | {mc['rows_1445']} / {mc['rows_1700']} | "
        f"{cc['rows_1445']} / {cc['rows_1700']} |",
        f"| 一致 / 不一致 / 无计划（1700） | {mc['agree_true']} / {mc['agree_false']} / "
        f"{mc['agree_none']} | {cc['agree_true']} / {cc['agree_false']} / {cc['agree_none']} |",
        f"| 非 default 不一致行（未按分歧段去重） | {mc['disagree_nondefault_rows']} | "
        f"{cc['disagree_nondefault_rows']} |",
        "",
    ]
    rv = sh["review"]
    if rv["available"]:
        d5 = rv["delta5"]
        L += [
            f"独立事件（分歧段段头）累计 **{rv['n_events']}** 个 / {rv['n_distinct_codes']} 只持仓"
            f"（段内续行 {rv['n_continuation_rows']} 行留痕）；"
            f"Δret₅：已判 {d5.get('n')} 个，均值 "
            f"{_pct(d5.get('mean'))}、中位 {_pct(d5.get('median'))}、plan 胜率 "
            f"{_pct(d5.get('win_rate'))}，未到期 {d5.get('n_pending')} 个。",
            "",
        ]
    else:
        L += [f"- 影子打分 unavailable：{rv['reason']}", ""]

    cf = rep["trade_cf"]
    L += ["### 操作反事实摘要（trade_cf，本月口径；明细见本月报附录反事实节）", ""]
    if cf["available"]:
        a, r = cf["actual_first_lot"], cf["rule_first_lot"]
        L += [
            f"> 口径：{cf['basis']}",
            "",
            "| 口径 | 笔数 | 胜率 | 盈亏比 | 大单率 | 大亏率 |",
            "|---|---:|---:|---:|---:|---:|",
            f"| 实际（首笔那手） | {a['n']} | {_pct(a['win_rate'])} | "
            f"{_num(a['payoff_ratio'])} | {_pct(a['big_win_rate'])} | {_pct(a['big_loss_rate'])} |",
            f"| 首笔严格按规则 | {r['n']} | {_pct(r['win_rate'])} | "
            f"{_num(r['payoff_ratio'])} | {_pct(r['big_win_rate'])} | {_pct(r['big_loss_rate'])} |",
            "",
            f"出场差 Δ 合计 {cf['total_delta_amount'] if cf['total_delta_amount'] is not None else '—'} 元"
            + (
                "；分类："
                + "；".join(f"{k} {v} 笔" for k, v in cf["categories"].items())
                if cf["categories"]
                else ""
            ),
            "",
        ]
    else:
        L += [f"- unavailable：{cf['reason']}", ""]

    L += ["### 样本标注", ""]
    for note in rep["sample_notes"]:
        L.append(f"- ⚠️ {note}" if "不足" in note else f"- {note}")
    L.append("")
    if rep["unavailable"]:
        L += ["数据缺口：", ""]
        for u in rep["unavailable"]:
            L.append(f"- {u}")
        L.append("")
    L.append(f"> 基线出处：{bl['source']}")
    return "\n".join(L) + "\n"


def section(base: Path, month: str) -> str:
    """月报附录入口（fail-soft）：组报告 → require → 落伴生 JSON → 返回 markdown。

    任何失败（含 require 的 SystemExit）只回一行 unavailable，绝不打断月报主体
    （旁路证据定位同 append_shadow_safe）。
    """
    if os.environ.get(ENV_SKIP) == "1":
        return ""
    base = Path(base)
    try:
        rep = build_report(base, month)
        out = (
            base
            / "artifacts"
            / "reports"
            / "monthly"
            / f"{month}_holdout_tracking.json"
        )
        require("holdout_tracking_monthly", rep)
        write_json(out, rep)
        return (
            "\n"
            + render_markdown(rep)
            + f"\n> 明细 JSON：`{out.relative_to(base).as_posix()}`\n"
        )
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — 旁路必须兜住一切
        print(
            f"[WARN] holdout 跟踪节生成失败（不影响月报主体）: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return (
            f"\n{HEADER}\n\n> unavailable：holdout 跟踪节生成失败"
            f"（{type(exc).__name__}: {str(exc)[:200]}）\n"
        )
