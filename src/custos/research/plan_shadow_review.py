# -*- coding: utf-8 -*-
"""持仓计划影子事后打分（#60 并轨判据 C，v0.311 owner 拍板口径写死）。

问法：影子台账（``data/trades/plan_shadow_observations.jsonl``）里每次
「影子不一致」事件，**事后看照 plan 动作 vs 照现行动作哪个更好**——
一致性不是判据（一致=并进来没增量），不一致的地方才可能是 plan 信号
的价值或危害所在（owner 2026-10-09 方案；并轨判据 E 见 TODO #60）。

口径（写死，LLM 不碰数值）：
- **事件筛选**：``stage="1700"``（收盘口径为准——1445 盘中行只做盘中/
  收盘一致性参考，不进打分）且 ``agree=False`` 且 plan_source 非
  default/None（default 视同无计划，v0.310）；**按分歧段计独立事件**
  （v0.314 owner review：同 code 同 plan_source 的连续不一致天只取段头
  ——真按 plan 执行第一天就已离场后续天不存在，逐日计会窗口重叠
  高估样本量；段内续行只留痕计数。报告必报**独立事件数 / 涉及持仓
  数**，E 判据 K=20 按独立事件数 + ≥5 只不同持仓）；决策日无 bar
  （停牌）⇒ 标 error（close0 与 bar 序列对不上）；
- **动作映射**（owner 拍板）：P0 清仓 = 决策日次一交易日（T+1）起首个
  可卖日**开盘**全卖；P1 减仓 = 同点**卖半仓**（P2 减仓类按 P1 同档——
  owner 映射只给了 P0/P1/持有三档，P2（计划分批止盈/现行减仓信号）按
  减仓同档处理，如实注明）；P3/持有 = 不动；
- **执行语义沿用引擎**（单源复用 ``backtest_factors.tradable_flags`` +
  ``_next_tradable``）：跌停/停牌/一字板不可卖 ⇒ 顺延；窗口内始终不可卖
  ⇒ 骑到窗口末日收盘，如实记 ``sell_executed=False``；
- **路径**：决策日收盘（台账 close，1700 即收盘价）起算，N=5（主，B1
  短持有周期）/10（副，P2 time_stop 档）个交易日；持仓层收益——
  P0：``open_sell/close0 − 1``；P1/P2：``(0.5·open_sell + 0.5·close_N)
  /close0 − 1``；P3：``close_N/close0 − 1``。``Δret_N = plan − live``，
  **Δ>0 = plan 更好**；
- **汇总**：均值/中位数/符号计数/胜率；副读数 = plan 更防守事件
  （plan=P0 且 live≠P0）的 **live 路径窗口最大不利波动**（持仓价值相对
  决策日收盘的最低收益，MAE 口径）；
- **未到期**（决策日后不足 N 根 bar）⇒ pending 单列不进统计；
- **空结果护栏**：筛完 0 事件 ⇒ 非零退出且不写产物。
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.paths import LOGS, PLAN_SHADOW_LEDGER, cn_now  # noqa: E402
from custos.research import backtest_factors as bt  # noqa: E402

#: 口径常量（owner 2026-10-09 拍板写死）
DEFAULT_HORIZONS = (5, 10)  # 主/副（B1 短持有周期 / P2 time_stop 档）
STAGE_FINAL = "1700"  # 打分只用收盘口径
_EPS = 1e-12  # Δ 符号判定的零带


# ---------------------------------------------------------------------------
# 事件筛选与路径计算
# ---------------------------------------------------------------------------


def _is_event_row(row: dict) -> bool:
    """打分事件行：agree=False 且来源非 default/None（default 视同无计划）。"""
    if row.get("agree") is not False:
        return False
    src = row.get("plan_source")
    return bool(src) and src != "default"


def load_ledger_events(
    path: Path, stage: str = STAGE_FINAL
) -> tuple[int, list[dict], int]:
    """读台账 →（总行数, 独立事件清单, 段内续行数）。

    **按分歧段计事件**（v0.314 owner review）：同 code 的 stage 行按日排序，
    事件行（``_is_event_row``）若其**同 code 前一行**也是同 plan_source
    的事件行 ⇒ 段内续行；否则 = 新事件（段头）。一只持仓连续 10 天
    「plan P0 vs live P1」只算 **1 个事件**——真按 plan 执行第一天就已
    离场，后续天根本不存在；且逐日计会让 5 日窗口高度重叠、两三只票
    凑满 K=20（E 判据按独立事件数计 + ≥5 只不同持仓，见 TODO #60）。
    段断条件：中间出现 agree=True/None 行，或 plan_source 切换。
    """
    n_rows = 0
    stage_rows: list[dict] = []
    if not Path(path).exists():
        return 0, [], 0
    with Path(path).open("r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            n_rows += 1
            try:
                row = json.loads(ln)
            except ValueError:
                continue  # 损坏行跳过不炸链（台账读松惯例）
            if row.get("stage") != stage:
                continue
            stage_rows.append(row)
    by_code: dict[str, list[dict]] = {}
    for r in stage_rows:
        by_code.setdefault(str(r.get("code")), []).append(r)
    heads: list[dict] = []
    n_continuation = 0
    for _code, rs in by_code.items():
        rs.sort(key=lambda r: str(r.get("date")))
        prev: Optional[dict] = None
        for r in rs:
            if _is_event_row(r):
                if (
                    prev is not None
                    and prev.get("agree") is False
                    and prev.get("plan_source") == r.get("plan_source")
                ):
                    n_continuation += 1  # 段内续行：留痕计数不进事件
                else:
                    heads.append(r)
            prev = r
    heads.sort(key=lambda r: (str(r.get("date")), str(r.get("code"))))
    return n_rows, heads, n_continuation


def _path_return(
    priority: Optional[str],
    path_bars: list[dict],
    can_sell: Any,
    close0: float,
    n: int,
) -> Optional[dict[str, Any]]:
    """单个动作口径的 N 日持仓路径收益；未到期（不足 N+1 根含决策日）⇒ None。

    ``path_bars`` 从决策日 T 起（index 0=T）；卖出候选搜索区间 = **[1, n]
    （含第 N 天）**——``_next_tradable(start=1, max_delay=n−1)`` 的
    end=start+max_delay=n（钉测锁死，勿误读为 [1, n−1] 再「修」）。
    """
    if len(path_bars) < n + 1:
        return None
    end_close = float(path_bars[n]["close"])
    sell_executed = False
    if priority in ("P0", "P1", "P2"):
        k = bt._next_tradable(can_sell, 1, n - 1)  # T+1 起首个可卖日
        if k is not None:
            sell_executed = True
            open_sell = float(path_bars[k]["open"])
            if priority == "P0":
                ret = open_sell / close0 - 1.0
            else:  # P1/P2 减仓类：卖半仓，余下半仓骑到窗口末
                ret = (0.5 * open_sell + 0.5 * end_close) / close0 - 1.0
        else:  # 窗口内不可卖 ⇒ 全仓骑到末日（如实记 sell_executed=False）
            ret = end_close / close0 - 1.0
    else:  # P3/持有/缺失 ⇒ 不动
        ret = end_close / close0 - 1.0
    return {"ret": ret, "sell_executed": sell_executed}


def _path_mae(
    priority: Optional[str],
    path_bars: list[dict],
    can_sell: Any,
    close0: float,
    n: int,
) -> Optional[float]:
    """live 路径持仓价值的窗口最大不利波动（MAE，bar 收盘采样）。"""
    if len(path_bars) < n + 1:
        return None
    sell_i: Optional[int] = None
    if priority in ("P0", "P1", "P2"):
        k = bt._next_tradable(can_sell, 1, n - 1)
        if k is not None:
            sell_i = k
    worst = 0.0
    for j in range(1, n + 1):
        c = float(path_bars[j]["close"])
        if sell_i is None:
            value = c
        elif priority == "P0":
            value = float(path_bars[sell_i]["open"]) if j >= sell_i else c
        else:
            value = (
                0.5 * float(path_bars[sell_i]["open"]) + 0.5 * c if j >= sell_i else c
            )
        worst = min(worst, value / close0 - 1.0)
    return worst


def evaluate_event(
    row: dict, df: Any, horizons: tuple[int, ...] = DEFAULT_HORIZONS
) -> dict[str, Any]:
    """单事件：双动作 N 日路径 + Δ + live MAE（pending 的 horizon 记 None）。"""
    day = str(row["date"])
    close0 = row.get("close")
    out: dict[str, Any] = {
        "date": day,
        "code": row.get("code"),
        "plan_source": row.get("plan_source"),
        "plan_priority": row.get("shadow_priority"),
        "live_priority": row.get("live_final_priority"),
        "plan_stop_price": row.get("plan_stop_price"),
        "entry_price": row.get("entry_price"),
        "close0": close0,
        "deltas": {},
        "live_mae": {},
    }
    if df is None or not len(df) or not isinstance(close0, (int, float)) or close0 <= 0:
        out["error"] = "bars 缺失或 close0 非法"
        return out
    sub = df[df["date"].astype(str).str[:10] >= day].reset_index(drop=True)
    if not len(sub):
        out["error"] = "决策日及之后无 bar"
        return out
    sub = sub.sort_values("date").reset_index(drop=True)
    if str(sub["date"].astype(str).str[:10].iloc[0]) != day:
        # 决策日停牌/缺数据：bar 序列首日≠决策日，台账 close 与 bar 价格对不上
        out["error"] = "决策日无 bar（停牌/缺数据）——close0 与 bar 序列口径对不上"
        return out
    can_buy, can_sell = bt.tradable_flags(sub, str(row.get("code") or ""))
    path_bars = [
        {"date": d, "open": o, "close": c}
        for d, o, c in zip(
            sub["date"].astype(str).str[:10].tolist(),
            sub["open"].astype(float).tolist(),
            sub["close"].astype(float).tolist(),
        )
    ]
    for n in horizons:
        plan_p = _path_return(out["plan_priority"], path_bars, can_sell, close0, n)
        live_p = _path_return(out["live_priority"], path_bars, can_sell, close0, n)
        if plan_p is None or live_p is None:
            out["deltas"][str(n)] = None  # pending（未到期）
            out["live_mae"][str(n)] = None
            continue
        out["deltas"][str(n)] = {
            "plan_ret": plan_p["ret"],
            "live_ret": live_p["ret"],
            "delta": plan_p["ret"] - live_p["ret"],
            "plan_sell_executed": plan_p["sell_executed"],
            "live_sell_executed": live_p["sell_executed"],
        }
        out["live_mae"][str(n)] = _path_mae(
            out["live_priority"], path_bars, can_sell, close0, n
        )
    return out


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def _summary(deltas: list[float]) -> dict[str, Any]:
    n = len(deltas)
    pos = sum(1 for d in deltas if d > _EPS)
    neg = sum(1 for d in deltas if d < -_EPS)
    srt = sorted(deltas)
    mid = n // 2
    median = srt[mid] if n % 2 else (srt[mid - 1] + srt[mid]) / 2
    return {
        "n": n,
        "mean": sum(deltas) / n,
        "median": median,
        "n_pos": pos,
        "n_zero": n - pos - neg,
        "n_neg": neg,
        "win_rate": pos / n,
        "merge_check": "均值 Δret ≥ 0 ⇒ live 影子「不更差」过线（E 判据的"
        " live 半：K=20 个事件 + 60 交易日帽，见 TODO #60）",
    }


def build_report(
    events: list[dict], n_rows: int, horizons: tuple[int, ...]
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    mae_avoided: dict[str, Any] = {}
    for n in horizons:
        key = str(n)
        ds = [
            e["deltas"][key]["delta"]
            for e in events
            if e.get("deltas", {}).get(key) is not None
        ]
        pending = sum(
            1
            for e in events
            if e.get("deltas", {}).get(key) is None and "error" not in e
        )
        n_error = sum(1 for e in events if "error" in e)
        summary[key] = {
            **(_summary(ds) if ds else {"n": 0}),
            "n_pending": pending,
            "n_error": n_error,  # bars 缺失/close0 非法（数据问题≠未到期）
        }
        # 副读数：plan 更防守（P0 vs live 非 P0）事件的 live MAE
        maes = [
            e["live_mae"][key]
            for e in events
            if e.get("deltas", {}).get(key) is not None
            and e.get("plan_priority") == "P0"
            and e.get("live_priority") != "P0"
            and e.get("live_mae", {}).get(key) is not None
        ]
        mae_avoided[key] = (
            {
                "n": len(maes),
                "mean": sum(maes) / len(maes),
                "worst": min(maes),
                "note": "plan 更防守（P0 vs live 非 P0）事件的 live 路径窗口"
                "最大不利波动——越负=plan 避开的风浪越大",
            }
            if maes
            else {"n": 0}
        )
    return {
        "schema": "plan_shadow_review/v1",
        "generated_at": cn_now().isoformat(timespec="seconds"),
        "n_ledger_rows": n_rows,
        "n_events": len(events),
        "horizons": list(horizons),
        "events": events,
        "summary": summary,
        "mae_avoided": mae_avoided,
        "rule": "stage=1700 收盘口径；独立事件=分歧段段头（v0.314，E 判据 "
        "K=20 按独立事件数 + ≥5 只不同持仓）；Δret=plan−live（>0=plan 更好）；"
        "动作映射 P0=T+1 首可卖日开盘清仓 / P1（P2 同档）=开盘卖半仓 / P3=不动；"
        "可卖搜索区间 [1,N] 含第 N 天；跌停停牌顺延（引擎 tradable_flags 单源）",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="持仓计划影子事后打分（#60 判据 C：不一致事件 plan vs 现行 N 日 Δret）"
    )
    ap.add_argument("--ledger", default=str(PLAN_SHADOW_LEDGER), help="台账 jsonl 路径")
    ap.add_argument(
        "--stage", default=STAGE_FINAL, help="打分口径时点（默认 1700 收盘）"
    )
    ap.add_argument("--horizons", default="5,10", help="N 日窗口（主,副；默认 5,10）")
    ap.add_argument("--tag", default="plan_shadow_review", help="产物标签")
    ap.add_argument("--out-dir", default=str(LOGS), help="产物目录")
    return ap


def main(
    argv: Optional[list[str]] = None,
    *,
    bars_loader: Optional[Callable[[str], Any]] = None,
) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    horizons = tuple(int(x) for x in str(args.horizons).split(",") if x.strip())
    if not horizons:
        raise SystemExit("--horizons 为空")
    if bars_loader is None:
        bars_loader = functools.partial(
            bt._load_one_bars, count=2000, start=None, end=None
        )

    n_rows, rows, n_continuation = load_ledger_events(
        Path(args.ledger), stage=str(args.stage)
    )
    if not rows:  # 空结果护栏：0 事件 ⇒ 非零退出不写产物
        print(
            f"[空结果护栏] 台账 {args.ledger} 筛完 0 个独立打分事件"
            f"（stage={args.stage} 且 agree=False 且来源非 default）——"
            "非零退出不写产物",
            file=sys.stderr,
        )
        return 2
    loader_cache: dict[str, Any] = {}

    def _bars(code: str) -> Any:
        if code not in loader_cache:
            loader_cache[code] = bars_loader(code)
        return loader_cache[code]

    events = [
        evaluate_event(r, _bars(str(r.get("code") or "")), horizons) for r in rows
    ]
    rep = build_report(events, n_rows, horizons)
    rep["tag"] = args.tag
    rep["ledger"] = str(args.ledger)
    rep["stage"] = str(args.stage)
    rep["n_continuation_rows"] = n_continuation  # 段内续行（留痕不进事件，v0.314）
    rep["n_distinct_codes"] = len({str(e.get("code")) for e in events})
    out_dir = Path(args.out_dir) / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_plan_shadow_review__{args.tag}.json"
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    s5 = rep["summary"].get(str(horizons[0]), {})
    print(
        f"[plan_shadow_review] 事件 {rep['n_events']}（台账 {n_rows} 行）｜"
        f"N={horizons[0]}：均值Δ={s5.get('mean', 0):+.4f} 胜率={s5.get('win_rate', 0):.1%}"
        f"（{s5.get('n', 0)} 判 / {s5.get('n_pending', 0)} 未到期）⇒ {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
