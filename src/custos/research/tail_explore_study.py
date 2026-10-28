"""R49 B1 高盈亏比交易逆向画像（v0.443；预注册 governance/research/R49_tail_reverse_profile.md）。

**探索性单元**（不出 candidate / falsified，只产「线索」供后续预注册）：与 R48 同一交易集（0AMV 做多 + J<13 ×
``LIVE_PARAMS``，只读挖掘 A∪B），把信号日 as-of 能拿到的特征**铺宽**成格子面板，按「A 发现 → B 复现」筛线索，
再用簇级置换零假设给「整面板挑最好」定标尺。

面板（写死，逐窗内分档）：
- 连续量（R43 as-of 特征矩阵 37 维 + ``s_shape`` 分项 9 维 + 超卖原始量）：每维两格 ``dim@lo``（≤ 20 分位，含并列）
  与 ``dim@hi``（≥ 80 分位）；
- 布尔量（R20 因子命中面板 ~29 项 + R48 二值特征 + 回看档）：每项两格 ``name`` 与 ``!name``。

筛选流程（写死；零假设臂完全重复同一流程）：
1. A 合格：``n_hi_A ≥ MIN_N`` ∧ ``lift_big_A > LIFT_MIN`` ∧ ``tail_ratio_A > 1``；
2. A 合格单格按 ``lift_big_A`` 取前 ``TOP_K``，两两合取（AND）成对格，对格同样过 A 合格；
3. B 复现：``n_hi_B ≥ MIN_N`` ∧ ``lift_big_B > LIFT_MIN`` ∧ ``tail_ratio_B > 1``；线索统计量 = ``min(lift_A, lift_B)``；
4. 零假设：簇级置换（R48 ``cluster_perm``，两窗各自）N 臂，每臂统计量 = 复现格的 max 线索统计量（无复现记 0）；
   复现格统计量 > ``kit.q95`` ⇒ ``lead_robust``，否则 ``lead_replicated``。

同次落 R48 完整产物（``_tail_feature__<r48_tag>.json``，与单独跑 ``tail_feature_study`` 同参同种子 ⇒ 读数一致）。

另附流动性底线描述块（owner 2026-10-21：机构流程的「均成交额 ≥ X」是否是缺失的过滤条件）：``amt20`` ≥ 1/2/3/5 亿
子集的大单率 / 大亏率 / 均收益 / 胜率 / 盈亏比，两窗。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from custos.core.paths import LOGS, RESEARCH_DIR
from custos.research import tail_feature_study as tf

MIN_N = 1000
LIFT_MIN = 1.2
TOP_K = 10
N_RANDOM = 200
C4_MIN_POOL = 50
LIQ_FLOORS = (1e8, 2e8, 3e8, 5e8)
SSHAPE_KEYS = (
    "compression",
    "pivot",
    "volume",
    "pocket_pivot",
    "overhead_supply",
    "ma_structure",
)

# ---------------------------------------------------------------------------
# 面板
# ---------------------------------------------------------------------------


def default_panel_fn() -> Callable[[str, Any, int], dict[str, Any]]:
    """R20 因子命中面板（``winner_factor_study.build_factor_panel`` × ``srs.asof_candidate``，live 同源）
    + ``s_shape`` 分项（信号日 as-of 帧）。生产路径一次性读上证指数。"""
    from custos.core.factors import s_shape  # noqa: PLC0415
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415
    from custos.research import winner_factor_study as wfs  # noqa: PLC0415

    index_df = (
        local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )

    def fn(code: str, df_full: Any, i: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            cand = srs.asof_candidate(df_full, index_df, i, code)
            out.update({f"f_{k}": v for k, v in wfs.build_factor_panel(cand).items()})
        except Exception:  # noqa: BLE001 — 单笔失败记缺，不打死整窗
            pass
        try:
            df, _dl, _ix = srs.asof_frames(df_full, index_df, i)
            ss = s_shape.compute_s_shape(df, code)
            if ss.get("available"):
                comp = ss.get("components") or {}
                for k in SSHAPE_KEYS:
                    out[f"ss_{k}"] = float((comp.get(k) or {}).get("points", np.nan))
                for k in ("s_shape", "delta", "penalty", "s_star"):
                    out[f"ss_{k}"] = float(ss.get(k, np.nan))
        except Exception:  # noqa: BLE001
            pass
        return out

    return fn


def _as_float(v: Any) -> float:
    if v is None or isinstance(v, str):
        return float("nan")
    if isinstance(v, (bool, np.bool_)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def build_panel(
    win: dict[str, Any], panel_fn: Optional[Callable]
) -> tuple[list[str], np.ndarray, np.ndarray]:
    """窗内格子矩阵：返回 (格名, hi 布尔矩阵 n×m, valid 布尔矩阵 n×m)。分档逐窗独立。"""
    cont: dict[str, list[float]] = {}
    boo: dict[str, list[float]] = {}
    n = win["trades_n"]

    def _put(d: dict[str, list[float]], k: str, j: int, v: float) -> None:
        d.setdefault(k, [float("nan")] * n)[j] = v

    for j, rw in enumerate(win["raw"]):
        for k, v in (rw.get("raw") or {}).items():
            _put(cont, k, j, _as_float(v))
        for k, v in (rw.get("osc") or {}).items():
            (
                _put(boo, f"osc_{k}", j, _as_float(v))
                if isinstance(v, bool)
                else _put(cont, f"osc_{k}", j, _as_float(v))
            )
        _put(cont, "v0_score", j, _as_float(rw.get("score")))
        if rw.get("sector_fav") is not None:
            _put(boo, "sector_fav", j, float(bool(rw["sector_fav"])))
        if panel_fn is not None:
            rec = rw.get("rec") or {}
            pv = panel_fn(
                rec.get("code"), win["df_of"][rec.get("code")], rec.get("i", 0)
            )
            for k, v in pv.items():
                (
                    _put(boo, k, j, _as_float(v))
                    if k.startswith("f_")
                    else _put(cont, k, j, _as_float(v))
                )
    for f in tf.BINARY:
        boo[f"r48_{f}"] = list(win["feat"][f])
    names: list[str] = []
    his: list[np.ndarray] = []
    vals: list[np.ndarray] = []
    for k in sorted(cont):
        v = np.asarray(cont[k], dtype=float)
        ok = ~np.isnan(v)
        if ok.sum() < 5:
            continue
        lo_c, hi_c = np.nanpercentile(v[ok], [20, 80])
        if lo_c == hi_c:  # 退化维（几乎常数）只留一侧
            names.append(f"{k}@hi")
            his.append(ok & (v > lo_c))
            vals.append(ok)
            continue
        for tag, m in (("lo", v <= lo_c), ("hi", v >= hi_c)):
            names.append(f"{k}@{tag}")
            his.append(ok & m)
            vals.append(ok)
    for k in sorted(boo):
        v = np.asarray(boo[k], dtype=float)
        ok = ~np.isnan(v)
        for tag, m in (("", v > 0.5), ("!", v <= 0.5)):
            names.append(f"{tag}{k}")
            his.append(ok & m)
            vals.append(ok)
    if not names:
        return [], np.zeros((n, 0), bool), np.zeros((n, 0), bool)
    return names, np.column_stack(his), np.column_stack(vals)


# ---------------------------------------------------------------------------
# 统计（置换只重排结局行：n_hi / valid 计数不变，只需重算 big / loss 行的命中）
# ---------------------------------------------------------------------------


def cell_stats(
    hi: np.ndarray, valid: np.ndarray, big_rows: np.ndarray, loss_rows: np.ndarray
) -> dict[str, np.ndarray]:
    n_hi = hi.sum(0).astype(float)
    n_ok = valid.sum(0).astype(float)
    b_hi = hi[big_rows].sum(0)
    b_ok = valid[big_rows].sum(0)
    l_hi = hi[loss_rows].sum(0)
    l_ok = valid[loss_rows].sum(0)
    with np.errstate(divide="ignore", invalid="ignore"):
        lift_b = (b_hi / n_hi) / (b_ok / n_ok)
        lift_l = (l_hi / n_hi) / (l_ok / n_ok)
        tail = lift_b / lift_l
    return {"n_hi": n_hi, "lift_big": lift_b, "lift_loss": lift_l, "tail": tail}


def _qualify(st: dict[str, np.ndarray], min_n: int) -> np.ndarray:
    lb = np.nan_to_num(st["lift_big"], nan=0.0)
    tr = np.nan_to_num(st["tail"], nan=0.0, posinf=1e9)
    return (st["n_hi"] >= min_n) & (lb > LIFT_MIN) & (tr > 1)


def pipeline(
    pa: dict[str, Any],
    pb: dict[str, Any],
    perm_a: Optional[np.ndarray],
    perm_b: Optional[np.ndarray],
    min_n: int,
) -> list[dict[str, Any]]:
    """A 发现（单格 + 前 TOP_K 两两合取）→ B 复现。perm=None 为真实读数。"""
    ra = _rows(pa, perm_a)
    rb = _rows(pb, perm_b)
    sa = cell_stats(pa["hi"], pa["valid"], *ra)
    qa = np.flatnonzero(_qualify(sa, min_n))
    top = qa[np.argsort(-np.nan_to_num(sa["lift_big"][qa]))][:TOP_K]
    cands: list[tuple[str, tuple[int, ...]]] = [
        (pa["names"][int(j)], (int(j),)) for j in qa
    ]
    pairs = list(combinations(sorted(top.tolist()), 2))
    if pairs:
        ph = np.column_stack([pa["hi"][:, a] & pa["hi"][:, b] for a, b in pairs])
        pv = np.column_stack([pa["valid"][:, a] & pa["valid"][:, b] for a, b in pairs])
        sp = cell_stats(ph, pv, *ra)
        for kk in np.flatnonzero(_qualify(sp, min_n)):
            a, b = pairs[int(kk)]
            cands.append((f"{pa['names'][a]} & {pa['names'][b]}", (a, b)))
    if not cands:
        return []
    idx_b = {nm: j for j, nm in enumerate(pb["names"])}
    hb, vb, keep = [], [], []
    for nm, ids in cands:
        parts = [pa["names"][j] for j in ids]
        if not all(p in idx_b for p in parts):
            continue
        jb = [idx_b[p] for p in parts]
        hb.append(np.logical_and.reduce([pb["hi"][:, j] for j in jb]))
        vb.append(np.logical_and.reduce([pb["valid"][:, j] for j in jb]))
        keep.append((nm, ids))
    if not keep:
        return []
    sb = cell_stats(np.column_stack(hb), np.column_stack(vb), *rb)
    qb = _qualify(sb, min_n)
    out = []
    for k, (nm, ids) in enumerate(keep):
        ja = ids[0] if len(ids) == 1 else None
        la = float(sa["lift_big"][ja]) if ja is not None else None
        out.append(
            {
                "cell": nm,
                "order": len(ids),
                "replicated": bool(qb[k]),
                "lift_big_b": float(sb["lift_big"][k]),
                "lift_loss_b": float(sb["lift_loss"][k]),
                "n_hi_b": int(sb["n_hi"][k]),
                "_ids": ids,
                "_lift_a": la,
            }
        )
    return out


def _rows(
    p: dict[str, Any], perm: Optional[np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    """置换下结局对应的特征行：X' = X[perm] ⇒ X'[big] = X[perm[big]]。"""
    if perm is None:
        return np.flatnonzero(p["big"]), np.flatnonzero(p["loss"])
    return perm[p["big"]], perm[p["loss"]]


def _lead_stat(
    pa: dict[str, Any], leads: list[dict[str, Any]], perm_a: Optional[np.ndarray]
) -> float:
    """复现格的 max min(lift_A, lift_B)；A 侧对格 lift 现算。"""
    best = 0.0
    ra = _rows(pa, perm_a)
    for ld in leads:
        if not ld["replicated"]:
            continue
        ids = ld["_ids"]
        h = np.logical_and.reduce([pa["hi"][:, j] for j in ids])[:, None]
        v = np.logical_and.reduce([pa["valid"][:, j] for j in ids])[:, None]
        la = float(cell_stats(h, v, *ra)["lift_big"][0])
        ld["lift_big_a"] = la
        best = max(best, min(la, ld["lift_big_b"]))
    return best


def liquidity_block(win: dict[str, Any]) -> list[dict[str, Any]]:
    amt = np.array([_as_float((rw.get("raw") or {}).get("amt20")) for rw in win["raw"]])
    r = win["ret"]
    rows = []
    for fl in (0.0, *LIQ_FLOORS):
        m = (amt >= fl) if fl else ~np.isnan(amt)
        rows.append(
            {
                "floor_yi": fl / 1e8,
                "n": int(m.sum()),
                "share": float(m.mean()) if len(m) else None,
                "p_big": float(win["big"][m].mean()) if m.any() else None,
                "p_loss": float(win["loss"][m].mean()) if m.any() else None,
                **tf._ret_block(r, m),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _replay_session(fn: Any) -> Any:
    """v0.464：逐笔重放备忘会话（``factor_exit_study.replay_session``，惰性导入防环）。"""
    import functools  # noqa: PLC0415

    @functools.wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> Any:
        from custos.research import factor_exit_study as _fes  # noqa: PLC0415

        return _fes.replay_session(fn)(*a, **kw)

    return wrapper


@_replay_session
def run_study(
    args: Any,
    *,
    panel_fn: Optional[Callable] = None,
    no_panel: bool = False,
    **inj: Any,
) -> dict[str, Any]:
    from custos.research import criteria_kit as kit  # noqa: PLC0415
    from custos.research import provenance as pv  # noqa: PLC0415

    base = tf.run_study(args, keep_windows=True, **inj)
    wins = base.pop("_wins")
    if panel_fn is None and not no_panel:
        panel_fn = default_panel_fn()
    pnl: dict[str, dict[str, Any]] = {}
    for w, win in wins.items():
        names, hi, valid = build_panel(win, panel_fn)
        pnl[w] = {
            "names": names,
            "hi": hi,
            "valid": valid,
            "big": win["big"],
            "loss": win["loss"],
            "items": win["items"],
        }
    wa, wb = list(pnl)[:2]
    leads = pipeline(pnl[wa], pnl[wb], None, None, args.min_n)
    real_stat = _lead_stat(pnl[wa], leads, None)

    def _arm(i: int) -> Optional[float]:
        rng = random.Random(f"{args.seed}-x{i}")
        pa_, pb_ = (tf.cluster_perm(pnl[w]["items"], rng) for w in (wa, wb))
        ld = pipeline(pnl[wa], pnl[wb], pa_, pb_, args.min_n)
        return _lead_stat(pnl[wa], ld, pa_)

    pool_blk = kit.assemble_c4_pool(args.n_random, _arm)
    pool = pool_blk["pool"]
    q = kit.q95(pool)
    for ld in leads:
        stat = (
            min(ld.get("lift_big_a") or 0.0, ld["lift_big_b"])
            if ld["replicated"]
            else None
        )
        ld["stat"] = stat
        st = kit.c4_state_of(pool, min_pool=args.c4_min_pool, plan_delta=stat)
        ld["status"] = (
            ("lead_robust" if st == "confirmed_pass" else "lead_replicated")
            if ld["replicated"]
            else "a_only"
        )
        ld["c4_state"] = st
        ld.pop("_ids", None)
        ld.pop("_lift_a", None)
    leads.sort(
        key=lambda d: (
            d["status"] != "lead_robust",
            d["status"] != "lead_replicated",
            -(d.get("stat") or 0.0),
        )
    )
    robust = [d["cell"] for d in leads if d["status"] == "lead_robust"]
    return {
        "schema": "tail_explore_study/v1",
        "unit": "R49",
        "tag": args.tag,
        "verdict": "leads" if robust else "no_robust_lead",
        "robust_leads": robust,
        "n_cells": {w: len(p["names"]) for w, p in pnl.items()},
        "n_trades": base["n_trades"],
        "base_rates": base["base_rates"],
        "real_max_stat": real_stat,
        "leads": leads,
        "liquidity": {w: liquidity_block(win) for w, win in wins.items()},
        "c4_pool": {
            "q95": q,
            "pool": pool,
            **{k: pool_blk[k] for k in ("evaluated", "gate_pass_rate", "target_pool")},
        },
        "r48_side": {"verdict": base["verdict"], "candidates": base["candidates"]},
        # 同次 R48 完整产物（main 落成 _tail_feature__<r48_tag>.json，一条命令出两面——v0.444）
        "_r48_report": base,
        "window_usage": base["window_usage"],
        "provenance": pv.build(
            args,
            unit="R49",
            criteria_version="v0.443",
            pre_reg_doc=RESEARCH_DIR / "R49_tail_reverse_profile.md",
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R49 B1 高盈亏比交易逆向画像（探索，只读挖掘段）"
    )
    ap.add_argument("--codes", default="")
    ap.add_argument("--codes-file", default="")
    ap.add_argument("--universe-sample", type=int, default=0)
    ap.add_argument("--universe-local", action="store_true")
    ap.add_argument("--universe-seed", type=int, default=42)
    ap.add_argument(
        "--count",
        type=int,
        default=None,
        help="每股回溯 K 线根数（缺省按挖掘 A 起点推算）",
    )
    ap.add_argument("--cost-bps", type=float, default=25.0)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--n-random", type=int, default=N_RANDOM, help="簇级置换臂数")
    ap.add_argument("--c4-min-pool", type=int, default=C4_MIN_POOL)
    ap.add_argument(
        "--min-n",
        type=int,
        default=MIN_N,
        help="格子最小高档笔数（写死 1000，测试注入用）",
    )
    ap.add_argument(
        "--no-panel",
        action="store_true",
        help="不算 R20 因子面板与 s_shape 分项（省一半耗时，只剩 R43/超卖/R48 格）",
    )
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument(
        "--load-workers",
        type=int,
        default=1,
        help="warm 加载并行路数（同 tail_feature_study）",
    )
    ap.add_argument(
        "--score-workers",
        type=int,
        default=1,
        help="as-of V0 分批量打分进程数（v0.447 score_pool：多进程 + 跨研究落盘缓存，读数逐位不变）",
    )
    ap.add_argument("--tag", default="r49_v1")
    ap.add_argument(
        "--r48-tag",
        default="r48_v1",
        help="同次 R48 产物的 tag（v0.444：R49 内部完整跑一遍 R48 含 200 臂池，顺手落盘，免得 R48 再单独全宇宙重跑一次）",
    )
    ap.add_argument("--out-dir", default=str(LOGS / "tail_feature"))
    return ap


def main(argv: Optional[list[str]] = None, **inj: Any) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    args.cmdline = " ".join(sys.argv if argv is None else ["tail_explore_study", *argv])
    if not inj.get("warm_fn"):
        from custos.research import load_window as lw  # noqa: PLC0415
        from custos.research.exit_c5_terminal import check_reach  # noqa: PLC0415

        args.count = lw.resolve_count(args.count, lw.SEG_MINING_A[0])
        check_reach(args.count, lw.SEG_MINING_A[0])
    try:
        rep = run_study(args, no_panel=args.no_panel, **inj)
    except RuntimeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    r48 = rep.pop("_r48_report")
    r48["tag"] = args.r48_tag
    r48["produced_by"] = f"tail_explore_study --tag {args.tag}"
    out48 = Path(args.out_dir) / f"_tail_feature__{args.r48_tag}.json"
    out48.parent.mkdir(parents=True, exist_ok=True)
    out48.write_text(
        json.dumps(r48, ensure_ascii=False, indent=2, default=str, allow_nan=True),
        encoding="utf-8",
    )
    print(f"[OK] 同次 R48 产物 {out48}")
    out = Path(args.out_dir) / f"_tail_explore__{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str, allow_nan=True),
        encoding="utf-8",
    )
    print(
        f"[R49] {rep['verdict']}  robust={rep['robust_leads'][:10]}  q95={rep['c4_pool']['q95']}"
    )
    print(f"[OK] 写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
