# -*- coding: utf-8 -*-
"""台账 ↔ 持仓快照对账：把 master_trade_ledger.csv 全量回放，与 current_positions.json 比对。

## 为什么需要这个

`incremental_ledger.py` 的 `_commit` 刻意选择了「**ledger 先落、positions 后落**」
这个失败顺序，理由写在它的 docstring 里：崩在两次 `os.replace` 之间会留下
「已记录成交但持仓未更新」——**可检测、可修复**，而反过来（持仓已加、台账没记）
会让下一次导入把同一批成交**再算一遍**（持仓静默翻倍，那是真实发生过的缺陷）。

**但 2026-08-06 review 发现：没有任何常规检查在「检测」它。**

    · 唯一的对账逻辑 `check_positions` 在 `backtest_0amv_bear_regime.py` 里，
      而那是个自称「不触碰任何管线」的研究脚本，只在有人手动跑回测时才执行
    · `runtime_guards` 读台账只判**新鲜度**（当日有无成交 → 持仓是否已按增量更新），
      **不校验「持仓 == 台账回放」**

⇒ 「detectable」在设计上成立、在运行上不成立。本模块补上它。

## 口径

回放**复用 `incremental_ledger.compute_positions`**，不另写一份买卖应用逻辑 ——
持仓推导只有一个来源，否则「对账」只是在比两个都可能错的实现。

## 两类正常的不一致（不是缺陷）

1. **台账不是从零开始**：若开始记台账时已有持仓，全量回放必然少算，
   甚至在卖出时报「超卖」。此时 `replay_ok=False`，须以 `--baseline` 传入期初持仓。
2. **单位成本的浮点尾差**：回放与增量累加的运算顺序不同，末位可能差 1e-12。
   故成本比对带容差，只有相对偏差超 `--cost-tol` 才报。

数量是整数股，float 对 2^53 内整数精确，**数量不设容差** —— 有差就是真有差。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

_TOOLS = Path(__file__).resolve().parents[1]
for _bp in (_TOOLS, _TOOLS.parent / "core"):  # core/: paths 等 L0 模块
    if str(_bp) not in sys.path:
        sys.path.insert(0, str(_bp))

from custos.core.paths import BASE, cn_now, QUALITY_DIR  # noqa: E402
from custos.core.code_utils import clean_code, finite                          # noqa: E402

_TRADES = Path(__file__).resolve().parent
if str(_TRADES) not in sys.path:
    sys.path.insert(0, str(_TRADES))

from custos.core.trades.incremental_ledger import (LEDGER, POS, TRADE_CATEGORIES,     # noqa: E402
                                compute_positions, norm)



def replay_ledger(ledger_path: Path | None = None,
                  baseline: list[dict] | None = None) -> dict:
    """全量回放台账得到应有持仓。

    返回 ``{"ok": bool, "positions": [...], "error": str|None, "trade_rows": int}``。
    **不抛异常**：超卖在这里是「诊断结论」而不是「运行故障」——
    它恰恰说明台账不完整或有缺陷，调用方需要拿到这个结论而不是一个 traceback。
    """
    # ⚠️ 默认值在**调用时**解析，不写成 `ledger_path=LEDGER` —— 那样默认值在 def 执行时
    #    就绑定成具体 Path，测试 monkeypatch 模块常量对它无效。
    #    见 governance/data/DATA_SOURCE_PRINCIPLE.md「模块级常量 + 运行时替换 = 陷阱」变体②。
    ledger_path = ledger_path or LEDGER
    if not ledger_path.exists():
        return {"ok": False, "positions": [], "error": "ledger_missing", "trade_rows": 0}
    df = pd.read_csv(ledger_path, dtype={"代码": str})
    trades = df[df["交易类别"].isin(TRADE_CATEGORIES)].copy() if len(df) else df
    if not len(trades):
        return {"ok": True, "positions": list(baseline or []), "error": None, "trade_rows": 0}
    trades = norm(trades)
    # 回放必须按成交顺序：先卖后买的顺序会把合法卖出误判成超卖
    trades = trades.sort_values(["成交日期", "成交时间"], kind="stable")
    try:
        pos = compute_positions(trades, list(baseline or []))
    except ValueError as e:                       # 超卖 = 诊断结论，不是故障
        return {"ok": False, "positions": [], "error": f"replay_oversell: {e}",
                "trade_rows": len(trades)}
    return {"ok": True, "positions": pos, "error": None, "trade_rows": len(trades)}


def diff_positions(replayed: list[dict], actual: list[dict], *,
                   cost_tol: float = 1e-6) -> list[dict]:
    """逐代码比对数量与单位成本。数量**不设容差**（整数股，float 精确）。"""
    def index(rows):
        return {clean_code(r.get("代码")): r for r in rows}

    a, b = index(replayed), index(actual)
    out = []
    for code in sorted(set(a) | set(b)):
        rq, aq = finite(a.get(code, {}).get("持有数量")), finite(b.get(code, {}).get("持有数量"))
        rc, ac = finite(a.get(code, {}).get("单位成本")), finite(b.get(code, {}).get("单位成本"))
        qty_diff = rq - aq
        base = max(abs(rc), abs(ac), 1e-9)
        cost_rel = abs(rc - ac) / base
        if qty_diff or cost_rel > cost_tol:
            out.append({"code": code,
                        "replay_qty": rq, "actual_qty": aq, "qty_diff": qty_diff,
                        "replay_cost": round(rc, 6), "actual_cost": round(ac, 6),
                        "cost_rel_diff": round(cost_rel, 9),
                        "kind": ("only_in_replay" if code not in b else
                                 "only_in_actual" if code not in a else
                                 "qty_mismatch" if qty_diff else "cost_mismatch")})
    return out


def reconcile(ledger_path: Path | None = None, positions_path: Path | None = None,
              baseline: list[dict] | None = None, cost_tol: float = 1e-6) -> dict:
    ledger_path = ledger_path or LEDGER          # 同上：调用时解析
    positions_path = positions_path or POS
    rep = replay_ledger(ledger_path, baseline)
    actual = json.loads(positions_path.read_text(encoding="utf-8")) \
        if positions_path.exists() else []
    diffs = diff_positions(rep["positions"], actual, cost_tol=cost_tol) if rep["ok"] else []
    qty_mismatch = [d for d in diffs if d["qty_diff"]]
    return {
        "checked_at": cn_now().isoformat(timespec="seconds"),
        "ledger": str(ledger_path),
        "replay_ok": rep["ok"],
        "replay_error": rep["error"],
        "trade_rows": rep["trade_rows"],
        "replay_positions": len(rep["positions"]),
        "actual_positions": len(actual),
        "diff_count": len(diffs),
        "qty_mismatch_count": len(qty_mismatch),
        "diffs": diffs[:50],
        # ⚠️ 数量不一致才是**硬信号**：它意味着台账与持仓已经脱节
        # （典型成因：commit 崩在两次 rename 之间，或有人手改了 positions）。
        # 成本不一致多为浮点尾差或期初基线缺失，单独看。
        "status": ("replay_failed" if not rep["ok"]
                   else "mismatch" if qty_mismatch
                   else "cost_only_diff" if diffs
                   else "ok"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="台账↔持仓对账（默认只报告，不阻断）")
    ap.add_argument("--date", help="仅用于落盘文件名，不影响比对")
    ap.add_argument("--baseline", help="期初持仓 JSON（台账非从零开始时必须提供）")
    ap.add_argument("--cost-tol", type=float, default=1e-6, help="单位成本相对容差")
    ap.add_argument("--strict", action="store_true",
                    help="数量不一致时 exit 1（默认只报告 —— 新校验先观察若干交易日再开硬闸，"
                         "见 2026-07-30 事故：门控与口径同时收紧导致整条链失败）")
    ap.add_argument("--out", help="结果落盘路径（默认 data/quality/{date}_ledger_reconcile.json）")
    a = ap.parse_args(argv)

    baseline = json.loads(Path(a.baseline).read_text(encoding="utf-8")) if a.baseline else None
    r = reconcile(baseline=baseline, cost_tol=a.cost_tol)

    day = a.date or cn_now().strftime("%Y-%m-%d")
    out = Path(a.out) if a.out else QUALITY_DIR / f"{day}_ledger_reconcile.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r["status"] != "ok":
        print(f"[RECONCILE] {r['status']}：数量不一致 {r['qty_mismatch_count']} 只，"
              f"其他差异 {r['diff_count'] - r['qty_mismatch_count']} 只", file=sys.stderr)
    return 1 if (a.strict and r["qty_mismatch_count"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
