# -*- coding: utf-8 -*-
"""#116 输入层对拍（v0.467）：真实数据 vs 快照的**前复权日线**逐票比对，定位产物层不一致的来源。

假设（待本脚本证实 / 证伪）：快照截掉了 holdout（≥2026-09-05）的权息事件 ⇒ 前复权锚点不同，
快照价格 = 真实价格 × 每票一个常数。收益比例不变，但出场模拟里**非比例**的环节会出差：
跌停 / 涨停判定按 0.01 取整、止损 tick 缓冲（绝对 0.01 元）等 ⇒ 少数交易进出场价不同 ⇒ margin 微差。

输出每票：比较的 bar 数、真实 / 快照收盘比的最小 / 最大值、是否「常数比」、真实侧 holdout 权息事件数。
结论判读：比 ≠ 1 的票若恰好都是有 holdout 权息事件的票 ⇒ 假设成立。

用法（生产机，真实数据侧直接读；快照侧由脚本自己起子进程设 ``CUSTOS_DATA_SNAPSHOT``）::

    uv run python scripts/dev/snapshot_input_diff.py --snapshot artifacts/cache/mining_snapshot \\
        --sample 300 --out artifacts/logs/batch1/snap_input_diff.json
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

SNAPSHOT_END = "2026-09-04"
KEEP_A_END = "2016-12-31"
KEEP_B_START = "2020-10-01"


def _kept(d: str) -> bool:
    return d <= KEEP_A_END or KEEP_B_START <= d <= SNAPSHOT_END


def _load(codes: list[str]) -> dict[str, Any]:
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    out: dict[str, Any] = {}
    for c in codes:
        try:
            df = local_tdx_data.get_ohlcv_table(c, count=100000)
            if df.empty:
                out[c] = {"error": "empty_ohlcv"}
                continue
            missing = sorted({"date", "close", "low"} - set(df.columns))
            if missing:
                out[c] = {"error": f"missing_columns={','.join(missing)}"}
                continue
            df = df[df["date"].astype(str).map(_kept)]
            out[c] = {
                "date": df["date"].astype(str).tolist(),
                "close": df["close"].astype(float).tolist(),
                "low": df["low"].astype(float).tolist(),
            }
        except Exception as exc:  # noqa: BLE001 — 逐票容错，记原因
            out[c] = {"error": repr(exc)}
    return out


def _holdout_events(code: str) -> int:
    """真实侧该票 holdout 期（> SNAPSHOT_END）的权息事件数；读不到 = -1。"""
    try:
        from custos.datasource.local_tdx import adjust_factors as af  # noqa: PLC0415

        evs = af.load_xdxr_cache(code) or []
    except Exception:  # noqa: BLE001
        return -1
    return sum(1 for e in evs if str(e.get("date", ""))[:10] > SNAPSHOT_END)


def compare(real: dict[str, Any], snap: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for c in sorted(real):
        r, s = real[c], snap.get(c, {})
        if "error" in r or "error" in s or not s:
            rows.append(
                {"code": c, "error": r.get("error") or s.get("error") or "快照缺票"}
            )
            continue
        rs = dict(zip(r["date"], r["close"]))
        ss = dict(zip(s["date"], s["close"]))
        common = sorted(set(rs) & set(ss))
        ratios = [rs[d] / ss[d] for d in common if ss[d]]
        lo, hi = (min(ratios), max(ratios)) if ratios else (None, None)
        rows.append(
            {
                "code": c,
                "n_common": len(common),
                "only_real": len(set(rs) - set(ss)),
                "only_snap": len(set(ss) - set(rs)),
                "ratio_min": lo,
                "ratio_max": hi,
                "identical": bool(ratios) and all(rs[d] == ss[d] for d in common),
                "constant_ratio": bool(ratios)
                and (hi - lo) <= 1e-6 * max(abs(hi), 1e-12),
            }
        )
    diff = [x for x in rows if not x.get("identical")]
    return {
        "n_codes": len(rows),
        "n_identical": len(rows) - len(diff),
        "n_constant_ratio_only": sum(1 for x in diff if x.get("constant_ratio")),
        "n_nonconstant": sum(
            1 for x in diff if "error" not in x and not x.get("constant_ratio")
        ),
        "n_error": sum(1 for x in rows if "error" in x),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="#116 输入层对拍：前复权日线真实 vs 快照")
    ap.add_argument("--snapshot", default="", help="快照目录（真实侧运行时必填）")
    ap.add_argument(
        "--codes", default="", help="逗号分隔代码；缺省按 --sample 从全宇宙抽"
    )
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--seed", type=int, default=116)
    ap.add_argument("--out", default="")
    ap.add_argument(
        "--dump", default="", help="（内部）子进程模式：加载并 pickle 到此路径"
    )
    a = ap.parse_args(argv)
    codes = [c for c in a.codes.split(",") if c]
    if a.dump:
        Path(a.dump).write_bytes(pickle.dumps(_load(codes)))
        return 0
    if not a.snapshot:
        print("[ERR] 需要 --snapshot", file=sys.stderr)
        return 2
    if not codes:
        from custos.research import universe  # noqa: PLC0415

        ns = argparse.Namespace(
            codes_file=None, universe_local=False, universe_sample=0, codes="", seed=0
        )
        allc = sorted(universe.resolve(ns, ap))
        codes = sorted(random.Random(a.seed).sample(allc, min(a.sample, len(allc))))
    real = _load(codes)
    tmp = Path("artifacts/cache/snap_input_diff.pkl")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "CUSTOS_DATA_SNAPSHOT": a.snapshot,
        "PYTHONIOENCODING": "utf-8",
    }
    subprocess.run(
        [sys.executable, __file__, "--dump", str(tmp), "--codes", ",".join(codes)],
        env=env,
        check=True,
    )
    snap = pickle.loads(tmp.read_bytes())
    rep = compare(real, snap)
    for x in rep["rows"]:
        if not x.get("identical") and "error" not in x:
            x["holdout_events_real"] = _holdout_events(x["code"])
    diff = [x for x in rep["rows"] if not x.get("identical") and "error" not in x]
    with_ev = sum(1 for x in diff if x.get("holdout_events_real", 0) > 0)
    print(
        f"[snap-input] {rep['n_codes']} 票：逐位一致 {rep['n_identical']}，常数比 {rep['n_constant_ratio_only']}，"
        f"非常数比 {rep['n_nonconstant']}，错误 {rep['n_error']}；不一致票中有 holdout 权息事件 {with_ev}/{len(diff)}"
    )
    for x in [x for x in diff if not x.get("constant_ratio")][:15]:
        print(f"  非常数比: {x}")
    if a.out:
        Path(a.out).write_text(
            json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
