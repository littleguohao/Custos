# -*- coding: utf-8 -*-
"""研究：8 个研究信号的「三面共振 / 空头前哨 / 技术高分」上下文维度加值验证（owner 2026-09-06）。

研究问题：1800 候选表 8 个研究信号（RS/RD/R★/RV/B2/SB/PB/QG）在统一出场档
pct12_so5_bbi2（pct12 止损 + 分批止盈 0.5 + BBI 连破 2 根清仓，cost 25bps）下的
逐笔成交，按**信号触发日（entry_date）**的三个上下文维度分「组内/组外」，回答
维度对胜率和盈亏比有没有正向影响，找出有正向影响的（信号 × 维度）组合。

三个维度（逐项与 live/既有研究同源，不重造口径）：

- **三面共振 resonance3** = 基本面优（PIT as-of tier=优）∧ 技术强（as-of 技术分
  ≥60）∧ 市场腿（0AMV 做多）——腿定义逐一对齐 resonance3_study.make_resonance_gate
  （PIT 台账 notice_date<信号日次日可见 + TECH_STRONG=60 + regime as-of 最近≤entry）。
  ⚠️ pinned 批（amv_long_only=True）只在做多期进场，市场腿恒为做多，该批内此维度
  实际退化为「基本面优 ∧ 技术强」（报告 meta 里给出实际做多占比作证）。
- **空头前哨 bear_outpost** = 0AMV 空头 ∧ 基本面优 ∧ 技术强——live 定义见
  candidate_table._bear_outposts（is_bear ∧ tier=优 ∧ resonance_4leg.technical，
  其中 technical = tech_level==强 = 技术分≥60）。只可能存在于 nopin 批
  （pinned 批没有空头期成交），是其专属数据源。
- **技术高分 tech_high** = as-of 技术分 ≥60 单独看。

数据源（已落盘的 strategy_grid 格子，不重跑回测）：
``artifacts/logs/strategy_grid/baseline__{gate}__pct12_so5_bbi2__{hash}.json``，
顶层元数据（entry_filter/start/end/amv_long_only/stop_mode/stop_pct/bbi_consec/
time_stop + trades_signature 里的 scale_out/breakeven/trail/cost_zone_bars）+
trades 逐笔 {code, entry_date, exit_date, ret, risk_frac, r_multiple, holding, reason}。

⚠️ **ret 已是净收益**：backtest_factors._trade_record 收的是
``ret_net = 毛ret − cost_bps/1e4``（这批格子 --cost-bps 25 ⇒ 每笔已扣 0.0025），
本研究**不再二次扣成本**。

格子发现（按元数据，不认文件名 hash）：

- 文件名只作预筛（exit slug + baseline 前缀），权威口径 = 文件内元数据；
  只收 gate ∈ 8 信号 ∧ 出场档元数据 == pct12_so5_bbi2 ∧ (start,end) ∈ 双窗的格子。
- 同 (gate, 窗口, pin) 多 hash（别的批/宇宙漂移重跑）⇒ 取 **mtime 最新**者，
  被丢的进 warnings + 报告 ``skipped_at_discovery`` 计数留痕。
- 批次族（--tag 的选择单位）：``_ranked__{tag}.json`` 的 results 行
  （exit==pct12_so5_bbi2）给出 tag→格子文件映射；tag 剥掉窗口标记 _main/_cw
  （后缀或中缀，只剥第一个）后归族：rsi_family_main+cw ⇒ rsi_family，
  ctx_cw_nopin+ctx_main_nopin ⇒ ctx_nopin。无任何 ranked 引用的格子按 pin 落
  兜底族 ``nopin``/``adhoc_pin``（批在跑、ranked 未落盘时就是这么认出来的）。
- fail-closed：缺格子/缺窗/文件读不了 ⇒ WARN 不崩，报告 ``missing_cells`` 留痕。

**预注册判读线**（写死，跑前不改）：

- 维度加值 = 组内 margin − 组外 margin > 0 且**两窗同向**（跨窗、主窗同正）。
- ✅ 加值：两窗组内 n 都 ≥ --min-n（默认 30）且两窗 Δmargin 都 > 0。
- ❌ 不加值：两窗样本都够但至少一窗 Δmargin ≤ 0（含方向不一致）。
- ⚠️ 样本不足/证据不足：任一窗组内 n < min-n、缺窗缺格、或 margin 不可算
  （盈亏比除零 ⇒ None）。
- margin = 胜率 − 盈亏平衡胜率 1/(1+盈亏比)，复用 m2_stop_sweep._margin 同口径；
  盈亏比 = avg_win/avg_loss，avg_loss=0 ⇒ None（除零如实缺，不冒充）。
- 空头前哨维度额外输出「同信号 空头期 vs 做多期」对照表（按 entry 日 regime 分组）。

⚠️ R11/R14：基准已实现口径为负期望、vipdoc 宇宙带幸存者偏差——本研究读数
只做组内/组外相对比较，量级不得引用为策略预期。

CLI::

    uv run python src/custos/research/signal_context_study.py --tag rsi_family
    uv run python src/custos/research/signal_context_study.py  # 默认处理全部发现的批次
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import statistics
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from custos.core.paths import LOGS  # noqa: E402
from custos.research import backtest_factors as bf  # noqa: E402
from custos.research import resonance3_study as r3  # noqa: E402
from custos.research import score_return_study as srs  # noqa: E402
from custos.research.m2_stop_sweep import _margin  # noqa: E402

# 8 个研究信号：1800 标注 RS/RD/R★/RV/B2/SB/PB/QG → strategy_grid gate 名（R27 口径）。
SIGNAL_GATES: tuple[tuple[str, str], ...] = (
    ("j_low_rsi_strong", "RS"),
    ("j_low_rsi_deep", "RD"),
    ("j_low_rsi_ideal_b1", "R★"),
    ("j_low_rsi_div", "RV"),
    ("b2", "B2"),
    ("surge_then_b1", "SB"),
    ("breakout_pullback_b1", "PB"),
    ("qg", "QG"),
)
GATE_LABEL = dict(SIGNAL_GATES)

# 统一出场档 pct12_so5_bbi2 = pct12 止损 + 分批止盈 0.5 + BBI 连破 2 根清仓，
# 其余出场旋钮全关（R27 出场轴 params 定义）。键名分工：stop_mode/stop_pct/
# bbi_consec/time_stop 在结果 JSON 顶层，scale_out/breakeven/trail/cost_zone_bars
# 只在 trades_signature 指纹里（顶层不自述，见 trades_signature docstring）。
EXIT_TIER = "pct12_so5_bbi2"
EXIT_TIER_PARAMS: dict[str, Any] = {
    "stop_mode": "pct",
    "stop_pct": 12.0,
    "bbi_consec": 2,
    "time_stop": 0,
    "scale_out": 0.5,
    "breakeven": 0.0,
    "trail": 0.0,
    "cost_zone_bars": 0,
}
_EXIT_TOP_KEYS = ("stop_mode", "stop_pct", "bbi_consec", "time_stop")
_EXIT_SIG_KEYS = ("scale_out", "breakeven", "trail", "cost_zone_bars")
# 出场档的净收益成本口径：这批格子 --cost-bps 25（v0.187 起发现阶段硬校验，
# 成本不同的格子不混进同一对照）
EXIT_COST_BPS = 25.0

# 双窗（R27 钉死）：跨窗 2022-2024 + 主窗 2024-08~2026-09。
TARGET_WINDOWS: tuple[tuple[str, str, str], ...] = (
    ("2022-01-01", "2024-12-31", "跨窗"),
    ("2024-08-01", "2026-09-04", "主窗"),
)
WINDOW_LABELS = [w[2] for w in TARGET_WINDOWS]

TECH_STRONG = r3.TECH_STRONG  # 技术强 = live 技术分 ≥60（同源复用，不另起口径）
DEFAULT_MIN_N = 30
DEFAULT_CELLS_DIR = LOGS / "strategy_grid"
DEFAULT_OUT_DIR = LOGS / "signal_context_study"

# (维度键, 中文标签)。pinned 批市场腿恒为做多 ⇒ resonance3 退化为「优∧强」，见模块头注。
DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("resonance3", "三面共振（基本面优∧技术强∧0AMV做多）"),
    ("bear_outpost", "空头前哨（0AMV空头∧基本面优∧技术强）"),
    ("tech_high", f"技术高分（技术分≥{TECH_STRONG}）"),
)

PREREG_CRITERION = (
    "预注册判读线：维度加值 = 组内 margin − 组外 margin > 0 且两窗（跨窗/主窗）同向。"
    "✅=两窗组内 n≥min-n 且两窗 Δmargin 同正；❌=样本够但至少一窗 Δmargin≤0"
    "（方向不一致同此）；⚠️=任一窗组内 n<min-n / 缺窗缺格 / margin 不可算（除零）。"
    "margin = 胜率 − 盈亏平衡胜率 1/(1+盈亏比)（m2_stop_sweep._margin 口径）。"
)

R11_WARNING = (
    "⚠️ R11/R14：基准已实现口径为负期望、vipdoc 宇宙带幸存者偏差——本研究读数"
    "只做组内/组外相对比较，量级不得引用为策略预期。"
)


# ---------------------------------------------------------------------------
# 格子发现（元数据权威；fail-closed：坏格子跳过 + WARN，不崩）
# ---------------------------------------------------------------------------


def _warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)


def _exit_params_of(meta: dict[str, Any]) -> dict[str, Any]:
    """格子的出场参数全集：顶层键 + trades_signature 键合并（两处分工见 EXIT_TIER 注释）。"""
    sig = meta.get("trades_signature") or {}
    out = {k: meta.get(k) for k in _EXIT_TOP_KEYS}
    out.update({k: sig.get(k) for k in _EXIT_SIG_KEYS})
    return out


def _exit_tier_ok(meta: dict[str, Any]) -> bool:
    """出场档 == pct12_so5_bbi2（按文件内元数据，不认文件名 slug）。"""
    params = _exit_params_of(meta)
    for k, v in EXIT_TIER_PARAMS.items():
        pv = params.get(k)
        if isinstance(v, (int, float)) and isinstance(pv, (int, float)):
            if float(pv) != float(v):
                return False
        elif pv != v:
            return False
    return True


def _window_of(start: str, end: str) -> Optional[str]:
    """(start, end) → 双窗标签；不在双窗内 ⇒ None（不在本研究范围，不是错误）。"""
    for s, e, label in TARGET_WINDOWS:
        if start == s and end == e:
            return label
    return None


def parse_cell(path: Path) -> tuple[Optional[dict[str, Any]], str]:
    """读格子 JSON 的元数据并校验研究范围；不合格 ⇒ (None, 原因)。trades 当场丢弃。

    （发现阶段不留逐笔：25 个格子 ~200MB，逐笔只在处理批次时按需重读。）
    """
    try:
        d = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        return None, f"读不了({exc})"
    if not isinstance(d, dict) or not isinstance(d.get("trades"), list):
        return None, "缺 trades 数组"
    gate = str(d.get("entry_filter") or "")
    if gate not in GATE_LABEL:
        return None, f"gate {gate!r} 不在 8 信号"
    if not _exit_tier_ok(d):
        return None, f"出场档元数据≠{EXIT_TIER}"
    cb = d.get("cost_bps")
    if not isinstance(cb, (int, float)) or float(cb) != EXIT_COST_BPS:
        # 成本不同 ⇒ ret 净额口径不同，混进同一对照 = 静默错口径（v0.187 起硬校验）
        return None, f"cost_bps≠{EXIT_COST_BPS:g}"
    window = _window_of(str(d.get("start") or ""), str(d.get("end") or ""))
    if window is None:
        return None, "窗口不在双窗"
    pin = d.get("amv_long_only")
    if not isinstance(pin, bool):
        return None, "amv_long_only 缺失/非布尔"
    return (
        {
            "path": path,
            "file": path.name,
            "gate": gate,
            "window": window,
            "pin": pin,
            "start": str(d.get("start")),
            "end": str(d.get("end")),
            "n_trades": len(d["trades"]),
            "cost_bps": d.get("cost_bps"),
            "mtime": path.stat().st_mtime,
        },
        "",
    )


def discover_cells(
    cells_dir: Path,
) -> tuple[dict[tuple[str, str, bool], dict[str, Any]], list[str], dict[str, int]]:
    """扫描 cells_dir → {(gate, 窗口, pin): 格子}；同键多 hash 取 mtime 最新。

    返回 (cells, warnings, skipped)：skipped = {跳过原因: 文件数}（含范围外格子，
    只进报告 meta 不打 WARN——别的出场档/别的窗口是正常存在，不是缺失）。
    """
    cells: dict[tuple[str, str, bool], dict[str, Any]] = {}
    warnings: list[str] = []
    skipped: dict[str, int] = {}
    dropped: list[dict[str, Any]] = []
    for p in sorted(cells_dir.glob(f"baseline__*__{EXIT_TIER}__*.json")):
        cell, reason = parse_cell(p)
        if cell is None:
            skipped[reason] = skipped.get(reason, 0) + 1
            if reason.startswith("读不了") or reason == "缺 trades 数组":
                warnings.append(f"格子 {p.name} {reason}，跳过")
            continue
        key = (cell["gate"], cell["window"], cell["pin"])
        old = cells.get(key)
        if old is None or cell["mtime"] > old["mtime"]:
            if old is not None:
                dropped.append(old)
                warnings.append(
                    f"{key} 多 hash：弃 {old['file']}，取 {cell['file']}（mtime 最新）"
                )
            cells[key] = cell
        else:
            dropped.append(cell)
            warnings.append(
                f"{key} 多 hash：弃 {cell['file']}，取 {old['file']}（mtime 最新）"
            )
    if dropped:
        # 报告留痕用：挂到 warnings 之外的稳定结构由调用方从 cells 反推即可，
        # 这里把被丢文件名并进 skipped 统计。
        skipped["同键多hash被弃"] = skipped.get("同键多hash被弃", 0) + len(dropped)
    return cells, warnings, skipped


# ---------------------------------------------------------------------------
# 批次族（tag）：_ranked__{tag}.json 的 results 映射 + 窗口标记剥离归族
# ---------------------------------------------------------------------------


def _family_of(tag: str) -> str:
    """批次 tag → 族名：剥第一个窗口标记 ``_main``/``_cw``（后缀或中缀）。

    rsi_family_main/rsi_family_cw ⇒ rsi_family；ctx_cw_nopin/ctx_main_nopin ⇒
    ctx_nopin；cw_rsi_deep（cw 是前缀，前面没有下划线）⇒ 原样保留。
    """
    return re.sub(r"_(main|cw)(?=_|$)", "", tag, count=1)


def load_ranked_batches(cells_dir: Path) -> tuple[dict[str, set[str]], list[str]]:
    """``_ranked__*.json`` → {族: {格子结果文件名}}（只收 exit==pct12_so5_bbi2 的行）。"""
    fam: dict[str, set[str]] = {}
    warnings: list[str] = []
    for rf in sorted(cells_dir.glob("_ranked__*.json")):
        try:
            d = json.loads(rf.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"ranked {rf.name} 读不了({exc})，跳过")
            continue
        tag = rf.name[len("_ranked__") : -len(".json")]
        for row in d.get("results") or []:
            if row.get("exit") != EXIT_TIER:
                continue
            fn = row.get("result_file")
            if fn:
                fam.setdefault(_family_of(tag), set()).add(str(fn))
    return fam, warnings


def assign_families(
    cells: dict[tuple[str, str, bool], dict[str, Any]],
    ranked: dict[str, set[str]],
) -> dict[str, list[tuple[str, str, bool]]]:
    """格子 → 批次族：ranked 引用优先；无引用按 pin 落兜底族（批在跑 ranked 未落盘）。

    一个格子可被多个族引用（如 rsi_deep 跨 rsi_family 与旧批 cw_rsi_deep）——
    各族报告各自包含它，逐笔标注按文件路径缓存只算一次。
    """
    out: dict[str, list[tuple[str, str, bool]]] = {}
    for key, cell in cells.items():
        fams = sorted(f for f, files in ranked.items() if cell["file"] in files)
        if not fams:
            fams = ["nopin" if not cell["pin"] else "adhoc_pin"]
        for f in fams:
            out.setdefault(f, []).append(key)
    return out


# ---------------------------------------------------------------------------
# 三维度判定（逐笔，as-of；全部同源复用，无未来函数）
# ---------------------------------------------------------------------------


def regime_at(regime: dict[str, str], reg_dates: list[str], day: str) -> Optional[str]:
    """as-of：最近 ≤ day 的 regime 读数（backtest_factors._amv_checker 同语义）。

    值为 做多/空头/中性（状态机起始中性、阈值间粘滞维持）；无读数 ⇒ None。
    """
    if not regime or not day:
        return None
    i = bisect.bisect_right(reg_dates, day) - 1
    return regime[reg_dates[i]] if i >= 0 else None


class TechScorer:
    """as-of live 技术分：口径 = resonance3 第④腿 = srs.asof_technical_score。

    每股全历史 df 只加载一次（LRU 缓存，随 df 存 entry_date→行号映射）；
    逐笔打分走 srs.asof_candidate 内容键缓存（v0.175，截断帧逐字节相同即复用，
    与逐笔重算逐位一致）。绝不 raise：加载/打分失败 ⇒ None（该笔计入
    n_unscored，不静默当 False 进组外）。
    """

    def __init__(self, index_df: Any, max_cache: int = 4):
        self.index_df = index_df
        self.max_cache = max_cache
        self._cache: OrderedDict[str, tuple[Any, dict[str, int]]] = OrderedDict()
        self._warned: set[str] = set()
        # (code, day) → 分数 memo：跨格子/跨族同一进场日只算一次（24 万笔实测
        # ~70ms/笔，无 memo 跑不完；asof 缓存按内容键对逐笔唯一截断帧不命中）
        self._memo: dict[tuple[str, str], Optional[float]] = {}

    def score_at(self, code: str, day: str) -> Optional[float]:
        key = (code, day)
        if key in self._memo:
            return self._memo[key]
        score = self._score_at(code, day)
        self._memo[key] = score
        return score

    def _df(self, code: str) -> tuple[Any, dict[str, int]]:
        hit = self._cache.get(code)
        if hit is not None:
            self._cache.move_to_end(code)
            return hit
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

        raw = local_tdx_data.get_ohlcv_table(code, count=100000)
        # ⚠️ date 列不得转字符串（enrich 若干检测器依赖 datetime64），同 srs.run_study。
        df = raw.sort_values("date").reset_index(drop=True)
        date2i = {d: i for i, d in enumerate(df["date"].astype(str).str[:10].tolist())}
        self._cache[code] = (df, date2i)
        while len(self._cache) > self.max_cache:
            self._cache.popitem(last=False)
        return df, date2i

    def _score_at(self, code: str, day: str) -> Optional[float]:
        try:
            df, date2i = self._df(code)
        except Exception as exc:  # noqa: BLE001
            if code not in self._warned:
                _warn(f"{code} K线加载失败({exc})，该票各笔技术分记缺失")
                self._warned.add(code)
            return None
        i = date2i.get(day)
        if i is None:
            return None
        try:
            score, _level, _contrib = srs.asof_technical_score(
                df, self.index_df, i, code
            )
            return float(score)
        except Exception as exc:  # noqa: BLE001
            if code not in self._warned:
                _warn(f"{code} {day} 技术分计算失败({exc})，记缺失")
                self._warned.add(code)
            return None


TechFn = Callable[[str, str], Optional[float]]


def annotate_trades(
    trades: list[dict[str, Any]],
    *,
    regime: dict[str, str],
    pit_map: dict[str, list],
    tech_fn: TechFn,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """逐笔在 entry_date 打三维度布尔值。返回 (标注行, 计数信息)。

    - regime：as-of 最近 ≤entry 的 0AMV regime（regime_at）。
    - tier 优：r3.pit_tier_at（公告次日可见口径；无记录 ⇒ 未知 ⇒ 非优，live 同语义）。
    - 技术分：tech_fn(code, entry_date)（生产 = TechScorer；None ⇒ 该笔不计入
      任何分组（三个维度都要技术分），计 n_unscored——不冒充组外）。
    """
    reg_dates = sorted(regime) if regime else []
    rows: list[dict[str, Any]] = []
    n_bad = n_unscored = 0
    regime_dist: dict[str, int] = {}
    for t in trades:
        try:
            code = str(t["code"])
            day = str(t["entry_date"])[:10]
            ret = float(t["ret"])
        except Exception:  # noqa: BLE001
            n_bad += 1
            continue
        state = regime_at(regime, reg_dates, day)
        regime_dist[state or "无读数"] = regime_dist.get(state or "无读数", 0) + 1
        tier = r3.pit_tier_at(pit_map, code, day)
        score = tech_fn(code, day)
        if score is None:
            n_unscored += 1
            continue
        tech_high = bool(score >= TECH_STRONG)
        tier_ok = tier == "优"
        rows.append(
            {
                "code": code,
                "entry_date": day,
                "ret": ret,
                "regime": state,
                "tier": tier,
                "tech_score": score,
                "tech_high": tech_high,
                "resonance3": bool(tier_ok and tech_high and state == "做多"),
                "bear_outpost": bool(state == "空头" and tier_ok and tech_high),
            }
        )
    info = {
        "n_total": len(trades),
        "n_scored": len(rows),
        "n_unscored": n_unscored,
        "n_bad_rows": n_bad,
        "regime_dist": regime_dist,
    }
    return rows, info


# ---------------------------------------------------------------------------
# 分组统计与预注册判读
# ---------------------------------------------------------------------------


def group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """一组逐笔 → n/胜率/盈亏比/margin（margin 用 m2 _margin 同口径）。

    盈亏比 = avg_win/avg_loss；avg_loss=0 ⇒ payoff None ⇒ margin None（除零如实缺）。
    margin 的输入与输出值同精度（胜率 4 位、盈亏比 3 位），与 resonance3 的
    svs_margin 调用方式一致（st.get("win_rate")/st.get("payoff_ratio") 是已舍入值）。
    """
    if not rows:
        return {
            "n": 0,
            "win_rate": None,
            "payoff_ratio": None,
            "avg_ret": None,
            "avg_win": None,
            "avg_loss": None,
            "margin": None,
        }
    # 胜率/盈亏比/均收复用 score_return_study.ret_stats（同舍入口径，v0.187
    # 起不再各自手抄）；avg_win/avg_loss 是 ret_stats 没有的键，本地补算
    st = srs.ret_stats(rows)
    wr, payoff = st["win_rate"], st["payoff_ratio"]
    rets = [r["ret"] for r in rows]
    wins = [r for r in rets if r > 0]
    losses = [-r for r in rets if r < 0]
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    margin = _margin({"win": wr, "payoff": payoff})
    return {
        "n": st["n"],
        "win_rate": wr,
        "payoff_ratio": payoff,
        "avg_ret": st["avg_ret"],
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "margin": round(margin, 4) if margin is not None else None,
    }


def judge_dimension(
    win_stats: dict[str, dict[str, Any]], min_n: int = DEFAULT_MIN_N
) -> dict[str, Any]:
    """单 (信号 × 维度) 的两窗判读（预注册线，见模块 docstring）。

    ``win_stats`` = {窗口: {"in": stats, "out": stats}}，缺窗不给键即可。
    """
    per_win: dict[str, Any] = {}
    flags: list[str] = []
    for label in WINDOW_LABELS:
        wd = win_stats.get(label)
        if wd is None:
            per_win[label] = {"status": "missing"}
            flags.append("missing")
            continue
        si, so = wd["in"], wd["out"]
        entry: dict[str, Any] = {"in": si, "out": so}
        if si.get("n", 0) < min_n:
            entry["status"] = "insufficient"  # 组内样本不足
        elif si.get("margin") is None or so.get("margin") is None:
            entry["status"] = "no_margin"  # 盈亏比除零，margin 不可算
        else:
            entry["delta_margin"] = round(si["margin"] - so["margin"], 4)
            entry["status"] = "pos" if entry["delta_margin"] > 0 else "nonpos"
        flags.append(entry["status"])
        per_win[label] = entry
    if any(f in ("missing", "insufficient", "no_margin") for f in flags):
        verdict = "⚠️"
        note = "样本不足/证据不足（缺窗、组内 n<min-n 或 margin 不可算）"
    elif all(f == "pos" for f in flags):
        verdict = "✅"
        note = "两窗 Δmargin 同正 ⇒ 维度加值"
    else:
        verdict = "❌"
        note = "样本够但 Δmargin 非两窗同正 ⇒ 不加值/方向不一致"
    return {"windows": per_win, "verdict": verdict, "verdict_note": note}


def bear_regime_compare(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """「同信号 空头期 vs 做多期」对照：按 entry 日 regime 分组统计（空头前哨配套）。"""
    out: dict[str, Any] = {}
    for state in ("空头", "做多", "中性"):
        sub = [r for r in rows if r["regime"] == state]
        if sub:
            out[state] = group_stats(sub)
    n_other = sum(1 for r in rows if r["regime"] not in ("空头", "做多", "中性"))
    if n_other:
        out["无读数"] = {"n": n_other, "count_only": True}
    return out


# ---------------------------------------------------------------------------
# 批次报告
# ---------------------------------------------------------------------------


def _family_dominant_pin(fam_cells: list[dict[str, Any]]) -> bool:
    """族的多数 pin（正常情况族内 pin 同质；混了按多数派，报告 meta 留痕）。"""
    n_pin = sum(1 for c in fam_cells if c["pin"])
    return n_pin >= len(fam_cells) - n_pin


def build_report(
    family: str,
    fam_cells: list[dict[str, Any]],
    annotated: dict[tuple[str, str, bool], tuple[list[dict[str, Any]], dict[str, Any]]],
    *,
    min_n: int = DEFAULT_MIN_N,
    warnings: Optional[list[str]] = None,
    skipped: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    """单批次族 → 报告 dict：格子清单 + 三维度判定 + 空头前哨对照 + 缺格留痕。"""
    dom_pin = _family_dominant_pin(fam_cells)
    cell_by_gw: dict[tuple[str, str], dict[str, Any]] = {}
    for c in sorted(
        fam_cells, key=lambda c: (c["pin"] != dom_pin, -c["mtime"])
    ):  # 多数派 pin 优先，同 pin 取新
        cell_by_gw.setdefault((c["gate"], c["window"]), c)

    warnings = list(warnings or [])
    missing: list[str] = []
    cells_listing: list[dict[str, Any]] = []
    gates_report: dict[str, Any] = {}
    gates_present = {c["gate"] for c in fam_cells}
    bear_tables: dict[str, Any] = {}

    for gate, label in SIGNAL_GATES:
        if gate not in gates_present:
            continue
        win_packs: dict[str, Any] = {}
        for wlabel in WINDOW_LABELS:
            cell = cell_by_gw.get((gate, wlabel))
            if cell is None:
                missing.append(f"{gate}({label}) {wlabel}")
                continue
            key = (cell["gate"], cell["window"], cell["pin"])
            rows, info = annotated[key]
            win_packs[wlabel] = {"cell": cell, "rows": rows, "info": info}
            cells_listing.append(
                {
                    "gate": gate,
                    "window": wlabel,
                    "pin": cell["pin"],
                    "file": cell["file"],
                    "cost_bps": cell.get("cost_bps"),
                    **info,
                }
            )
            if info["n_unscored"]:
                warnings.append(
                    f"{gate} {wlabel}：{info['n_unscored']}/{info['n_total']} 笔"
                    "技术分缺失，未计入分组（见 n_unscored）"
                )
            if not cell["pin"] or info["regime_dist"].get("空头"):
                bear_tables[f"{gate}|{wlabel}"] = {
                    "gate": gate,
                    "window": wlabel,
                    "pin": cell["pin"],
                    "by_regime": bear_regime_compare(rows),
                }
        dims: dict[str, Any] = {}
        for dim_key, dim_label in DIMENSIONS:
            win_stats = {
                wlabel: {
                    "in": group_stats([r for r in pack["rows"] if r[dim_key]]),
                    "out": group_stats([r for r in pack["rows"] if not r[dim_key]]),
                }
                for wlabel, pack in win_packs.items()
            }
            dims[dim_key] = {"label": dim_label, **judge_dimension(win_stats, min_n)}
        gates_report[gate] = {
            "label": label,
            "windows": {
                wlabel: {
                    "cell_file": pack["cell"]["file"],
                    "pin": pack["cell"]["pin"],
                    **pack["info"],
                }
                for wlabel, pack in win_packs.items()
            },
            "dims": dims,
        }

    for m in missing:
        warnings.append(f"缺格子：{m}（fail-closed，不崩，判读按缺窗 ⚠️）")

    return {
        "study": "signal_context_study",
        "family": family,
        "preregistered_criterion": PREREG_CRITERION,
        "r11_warning": R11_WARNING,
        "min_n": min_n,
        "exit_tier": EXIT_TIER,
        "exit_tier_params": EXIT_TIER_PARAMS,
        "windows": {w[2]: [w[0], w[1]] for w in TARGET_WINDOWS},
        "cost_note": "trades.ret 已是净收益（_trade_record 收 ret_net = 毛ret − "
        "cost_bps/1e4，这批 --cost-bps 25），本研究不再二次扣成本",
        "tech_note": f"as-of 技术分 = srs.asof_technical_score（resonance3 第④腿同口径，"
        f"技术强=≥{TECH_STRONG}）；tier 优 = r3.pit_tier_at（公告次日可见）；"
        "regime = as-of 最近≤entry 的 0AMV 读数",
        "pinned_market_leg_note": "pinned 批（amv_long_only=True）只在 0AMV 做多期进场"
        " ⇒ 三面共振的市场腿恒为做多，该批内维度退化为「基本面优∧技术强」；"
        "各格 regime_dist 给出实际占比作证",
        "dominant_pin": dom_pin,
        "cells": cells_listing,
        "missing_cells": missing,
        "skipped_at_discovery": skipped or {},
        "gates": gates_report,
        "bear_regime_compare": bear_tables,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Markdown / 控制台输出
# ---------------------------------------------------------------------------


def _fmt_pct(x: Optional[float], digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def _fmt_payoff(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:.2f}"


def _fmt_margin(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:+.1f}pp"


def _fmt_group(st: Optional[dict[str, Any]]) -> str:
    """一组统计的紧凑串：n/胜率/盈亏比/margin。"""
    if not st or not st.get("n"):
        return "—"
    return (
        f"{st['n']}笔 {_fmt_pct(st['win_rate'])}/{_fmt_payoff(st['payoff_ratio'])}"
        f"/{_fmt_margin(st['margin'])}"
    )


def _fmt_delta(entry: Optional[dict[str, Any]]) -> str:
    if not entry:
        return "缺窗"
    st = entry.get("status")
    if st == "missing":
        return "缺窗"
    if st == "insufficient":
        return f"n={entry['in'].get('n', 0)}<min"
    if st == "no_margin":
        return "margin不可算"
    return _fmt_margin(entry.get("delta_margin"))


def render_markdown(rep: dict[str, Any]) -> str:
    """报告 → Markdown（信号 × 维度，两窗并排；控制台打印同一文本）。"""
    lines = [
        f"# 信号上下文维度加值研究 · 批次族 {rep['family']}",
        "",
        f"> {rep['r11_warning']}",
        f"> {rep['preregistered_criterion']}",
        "",
        f"- 出场档：{rep['exit_tier']}（params={json.dumps(rep['exit_tier_params'], ensure_ascii=False)}）",
        f"- 窗口：{json.dumps(rep['windows'], ensure_ascii=False)}；min-n={rep['min_n']}",
        f"- {rep['cost_note']}",
        f"- {rep['tech_note']}",
        f"- {rep['pinned_market_leg_note']}",
        "",
        "## 格子清单",
        "",
        "| 信号 | gate | 窗口 | pin | 文件 | 笔数 | 已标注 | regime 分布 |",
        "|---|---|---|---|---|---:|---:|---|",
    ]
    for c in rep["cells"]:
        dist = "，".join(f"{k}{v}" for k, v in sorted(c["regime_dist"].items()))
        lines.append(
            f"| {GATE_LABEL.get(c['gate'], c['gate'])} | {c['gate']} | {c['window']} "
            f"| {c['pin']} | {c['file']} | {c['n_total']} | {c['n_scored']} | {dist} |"
        )
    lines += [
        "",
        "## 维度判定总表",
        "",
        "| 信号 | 三面共振 | 空头前哨 | 技术高分 |",
        "|---|---|---|---|",
    ]
    for gate, g in rep["gates"].items():
        cells3 = []
        for dim_key, _ in DIMENSIONS:
            d = g["dims"][dim_key]
            wins = " ".join(
                f"{w}{_fmt_delta(d['windows'].get(w))}" for w in WINDOW_LABELS
            )
            cells3.append(f"{d['verdict']} {wins}")
        lines.append(f"| {g['label']}（{gate}） | " + " | ".join(cells3) + " |")

    for dim_key, dim_label in DIMENSIONS:
        lines += [
            "",
            f"## 维度明细：{dim_label}",
            "",
            f"| 信号 | 跨窗组内 | 跨窗组外 | 跨窗Δ | 主窗组内 | 主窗组外 | 主窗Δ | 判定 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for gate, g in rep["gates"].items():
            d = g["dims"][dim_key]
            cw, mw = d["windows"].get("跨窗"), d["windows"].get("主窗")
            lines.append(
                f"| {g['label']} | {_fmt_group((cw or {}).get('in'))} "
                f"| {_fmt_group((cw or {}).get('out'))} | {_fmt_delta(cw)} "
                f"| {_fmt_group((mw or {}).get('in'))} "
                f"| {_fmt_group((mw or {}).get('out'))} | {_fmt_delta(mw)} "
                f"| {d['verdict']} |"
            )

    if rep["bear_regime_compare"]:
        lines += [
            "",
            "## 空头前哨对照：同信号 空头期 vs 做多期（nopin 批）",
            "",
            "| 信号 | 窗口 | regime | n/胜率/盈亏比/margin |",
            "|---|---|---|---|",
        ]
        for b in rep["bear_regime_compare"].values():
            for state, st in b["by_regime"].items():
                txt = f"{st['n']}笔" if st.get("count_only") else _fmt_group(st)
                lines.append(
                    f"| {GATE_LABEL.get(b['gate'], b['gate'])} | {b['window']} "
                    f"| {state} | {txt} |"
                )

    if rep["missing_cells"] or rep["warnings"]:
        lines += ["", "## ⚠️ 警告与缺失", ""]
        for w in rep["warnings"]:
            lines.append(f"- {w}")
    if rep["skipped_at_discovery"]:
        lines += [
            "",
            "## 发现阶段跳过统计（范围外/重复，非错误）",
            "",
        ]
        for k, v in sorted(rep["skipped_at_discovery"].items()):
            lines.append(f"- {k}: {v}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 上下文加载与 CLI
# ---------------------------------------------------------------------------


def load_context(args: argparse.Namespace) -> Optional[dict[str, Any]]:
    """真实三维度上下文：0AMV regime + PIT 台账 + 技术分打分器。

    跑前自检硬失败（regime/PIT/指数缺 ⇒ 维度无从算起，开跑无意义，对齐
    resonance3 的 ap.error 风格）；缺格子是另一层（WARN 容忍）。
    """
    regime = bf.load_amv_regime()
    if not regime:
        print("⛔ 读不到 0AMV regime 台账（0amv_observations.jsonl）", file=sys.stderr)
        return None
    from custos.datasource.local_tdx import fetch_pit_financials as pit  # noqa: PLC0415
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    records = pit.load_ledger()
    if not records:
        print(
            "⛔ PIT 财务台账为空（data/fundamentals/pit_financials.jsonl）——"
            "基本面腿不可用，开跑无意义",
            file=sys.stderr,
        )
        return None
    pit_map = r3.build_pit_map(records)
    try:
        index_df = (
            local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
            .sort_values("date")
            .reset_index(drop=True)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"⛔ 上证指数（{srs.INDEX_CODE}）加载失败: {exc}", file=sys.stderr)
        return None
    scorer = TechScorer(index_df)
    print(
        f"[INFO] 上下文就绪：regime {len(regime)} 天，PIT {len(records)} 条"
        f"（{len(pit_map)} 只）",
        file=sys.stderr,
    )
    return {
        "regime": regime,
        "pit_map": pit_map,
        "tech_fn": scorer.score_at,
        "n_pit_records": len(records),
    }


def _load_cell_trades(path: Path) -> list[dict[str, Any]]:
    """处理阶段按需重读格子逐笔（发现阶段只留了元数据）。"""
    d = json.loads(path.read_text(encoding="utf-8-sig"))
    return d.get("trades") or []


def run_families(
    families: dict[str, list[tuple[str, str, bool]]],
    cells: dict[tuple[str, str, bool], dict[str, Any]],
    *,
    ctx: dict[str, Any],
    out_dir: Path,
    min_n: int,
    discovery_warnings: list[str],
    skipped: dict[str, int],
) -> dict[str, dict[str, Any]]:
    """逐批次族：标注 → 报告 → 落盘 + 控制台。逐笔标注按格子键缓存（族间共享文件）。"""
    reports: dict[str, dict[str, Any]] = {}
    ann_cache: dict[
        tuple[str, str, bool], tuple[list[dict[str, Any]], dict[str, Any]]
    ] = {}
    for fam in sorted(families):
        keys = [k for k in families[fam] if k in cells]
        if not keys:
            _warn(f"批次族 {fam} 没有合格格子，跳过")
            continue
        t0 = time.time()
        for k in keys:
            if k in ann_cache:
                continue
            cell = cells[k]
            # 断点缓存（2026-09-06：全量标注 24 万笔 ~70ms/笔，长跑曾被静默
            # 中断两次）：逐格落盘，重跑跳过已完成的格子。缓存绑定源格子的
            # mtime+大小（v0.187）：同名格子被覆盖重跑/底层数据修正后旧标注
            # 不得静默复用——签名变了就重算。
            try:
                _st = Path(cell["path"]).stat()
                src_sig = [_st.st_mtime_ns, _st.st_size]
            except OSError:
                src_sig = None
            ck = out_dir / "_ann" / f"{cell['file']}.ann.json"
            if ck.is_file():
                try:
                    blob = json.loads(ck.read_text(encoding="utf-8"))
                    if blob.get("src_sig") != src_sig:
                        _warn(f"标注缓存 {ck.name} 的源格子已变更，重算")
                    else:
                        ann_cache[k] = (blob["rows"], blob["info"])
                        continue
                except (OSError, ValueError, KeyError) as exc:
                    _warn(f"标注缓存 {ck.name} 读不了（{exc}），重算")
            print(
                f"[INFO] 标注 {cell['file']}（{cell['n_trades']} 笔）…",
                file=sys.stderr,
            )
            ann_cache[k] = annotate_trades(
                _load_cell_trades(cell["path"]),
                regime=ctx["regime"],
                pit_map=ctx["pit_map"],
                tech_fn=ctx["tech_fn"],
            )
            ck.parent.mkdir(parents=True, exist_ok=True)
            rows, info = ann_cache[k]
            ck.write_text(
                json.dumps(
                    {"src_sig": src_sig, "rows": rows, "info": info},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        rep = build_report(
            fam,
            [cells[k] for k in keys],
            ann_cache,
            min_n=min_n,
            warnings=discovery_warnings,
            skipped=skipped,
        )
        rep["meta"] = {
            "n_pit_records": ctx.get("n_pit_records"),
            "elapsed_sec": round(time.time() - t0, 1),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        jpath = out_dir / f"{fam}_context.json"
        mpath = out_dir / f"{fam}_context.md"
        n_rows = sum(len(ann_cache[k][0]) for k in keys)
        bf.write_json_stream(jpath, rep, big=n_rows > 20000)
        mpath.write_text(render_markdown(rep), encoding="utf-8")
        print(f"[OK] 写出 {jpath} / {mpath}")
        print(render_markdown(rep))
        reports[fam] = rep
    return reports


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--tag",
        default="",
        help="批次族选择（精确或前缀，如 rsi_family / ctx_nopin / rsi）；"
        "默认处理全部发现的批次",
    )
    ap.add_argument(
        "--cells-dir",
        default=str(DEFAULT_CELLS_DIR),
        help="strategy_grid 格子目录（默认 artifacts/logs/strategy_grid）",
    )
    ap.add_argument("--min-n", type=int, default=DEFAULT_MIN_N, help="组内样本下限")
    ap.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="报告输出目录（默认 artifacts/logs/signal_context_study）",
    )
    return ap


def main(argv: Optional[list] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    cells_dir = Path(args.cells_dir)
    if not cells_dir.is_dir():
        print(f"⛔ 格子目录不存在: {cells_dir}", file=sys.stderr)
        return 2

    cells, d_warnings, skipped = discover_cells(cells_dir)
    ranked, r_warnings = load_ranked_batches(cells_dir)
    warnings = d_warnings + r_warnings
    for w in warnings:
        _warn(w)
    print(
        f"[INFO] 合格格子 {len(cells)} 个（跳过 {sum(skipped.values())} 个范围外/重复），"
        f"ranked 批次族 {sorted(ranked)}",
        file=sys.stderr,
    )
    families = assign_families(cells, ranked)
    if args.tag:
        families = {
            f: ks
            for f, ks in families.items()
            if f == args.tag or f.startswith(args.tag)
        }
        if not families:
            _warn(
                f"--tag {args.tag!r} 没有匹配到任何批次族；现有: {sorted(assign_families(cells, ranked))}"
            )
            return 1
    if not families:
        print("⛔ 没有发现任何合格格子", file=sys.stderr)
        return 1

    ctx = load_context(args)
    if ctx is None:
        return 2
    reports = run_families(
        families,
        cells,
        ctx=ctx,
        out_dir=Path(args.out_dir),
        min_n=args.min_n,
        discovery_warnings=warnings,
        skipped=skipped,
    )
    if not reports:
        print("⛔ 所有批次族都没有合格格子", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
