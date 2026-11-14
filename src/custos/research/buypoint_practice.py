# -*- coding: utf-8 -*-
"""买点练习（v0.523，owner 2026-11-14）：B1 点盲判 → 该题选择后展示后 20 根。只描述，不作研究判据。

- ``--mode make``：从挖掘段（A 2000~2016 / B 2022~2026-09-04）的 B1 点（日线 J<13 ∧ 0AMV 做多）随机抽 N 个，
  出 KLineChart 图集（选择前只画到当根，不显示任何检测器结论），每图下点「买 / 不买 / 等」并写批语；
  该题选择后立即揭示随后 20 根，其他题不受影响；刷新后已选择题仍揭示。导出 labels.csv 留存，无需再跑 review。
  只抽前向 20 日仍落在同一挖掘段内的点（出题与揭示都不读判定段 / holdout）。
- ``--mode review``（兼容既有练习，可选）：读导出的 labels.csv，按 ``calibration_inventory.md`` 第 6 条「批语复盘口径」（含 2026-11-13
  两次修订）出 ``review.json`` / ``review.md``：
  ① 判断 × 当根买入 20 日表现与准确率（买 → 20 日收盘上涨；不买 → 不涨），附同批基准；
  ② 批语状态陈述 × 当根指标核实；
  ③ 「等」：批语里的等待条件 20 根内是否触发——触发则「触发日买 vs 当根买」配对比较，
     未触发 = 放弃，看放弃是否躲开亏损（当根买 20 日 ≤0 记放弃正确）；未识别出条件的「等」单列。
条件定义单一来源 ``research/remark_rules.py``。
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import random
import statistics as st
import sys
from pathlib import Path
from typing import Any, Optional

from custos.research import load_window as lw
from custos.research import remark_rules as rr

HEAD = ["id", "段", "代码", "日期", "J", "0AMV", "收盘"]
JUDGE = "判断(买/不买/等)"
NOTE = "批语"
SEGS = {"mining_a": lw.SEG_MINING_A, "mining_b": lw.SEG_MINING_B}


def _out_root() -> Path:
    from custos.core.paths import ARTIFACTS  # noqa: PLC0415

    return ARTIFACTS / "reports" / "calibration"


def _load(code: str, seg: str) -> Any:
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    s, e = SEGS[seg]
    return bt._load_one_bars(code, 100000, s, e, warmup_bars=300)


def b1_points(df: Any, regime: dict[str, str], seg: str) -> list[int]:
    """段内 B1 点下标：J<13 ∧ 当日 0AMV 做多 ∧ 前向 20 日仍在段内 ∧ 前有 ≥120 根（画图 / 0 轴摆幅）。"""
    from custos.core.b1_thresholds import J_LOW_THRESHOLD  # noqa: PLC0415
    from custos.core.indicators import j_series  # noqa: PLC0415

    s, e = SEGS[seg]
    ds = df["date"].astype(str).str[:10].tolist()
    j = j_series(df).to_numpy()
    last = max((k for k, d in enumerate(ds) if d <= e), default=-1)
    return [
        i
        for i, d in enumerate(ds)
        if s <= d <= e
        and i >= 120
        and i + rr.FWD_N <= last
        and j[i] < J_LOW_THRESHOLD
        and regime.get(d) == "做多"
    ]


def make(args: Any) -> Path:
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import calib_probe as cp  # noqa: PLC0415
    from custos.research import universe  # noqa: PLC0415
    from custos.core.indicators import j_series  # noqa: PLC0415

    for nm, (s, e) in SEGS.items():
        bad = lw.mining_violation(s, e)
        if bad:
            raise RuntimeError(f"窗口校验失败 {nm}: {bad}")
    segs = list(SEGS) if args.segments == "both" else [args.segments]
    regime = bt.load_amv_regime(since=lw.SEG_MINING_A[0])
    if not regime:
        raise RuntimeError("0AMV regime 读不到")
    codes = universe.default_codes()
    rng = random.Random(args.seed)
    rng.shuffle(codes)
    picks: list[tuple[str, str, Any, int]] = []
    for code in codes:
        if len(picks) >= args.n:
            break
        seg = segs[len(picks) % len(segs)]
        df = _load(code, seg)
        if df is None or len(df) < 200:
            continue
        pts = b1_points(df, regime, seg)
        if pts:
            picks.append((code, seg, df, rng.choice(pts)))
    if not picks:
        raise RuntimeError("空结果护栏：0 个 B1 点——不落盘")
    d = Path(args.out_dir) if args.out_dir else _out_root() / f"buypoint__{args.tag}"
    d.mkdir(parents=True, exist_ok=True)
    rows, parts, data = [], [], {}
    for n, (code, seg, df, i) in enumerate(picks, 1):
        fr = df.iloc[: i + 1].reset_index(drop=True)
        date = str(fr["date"].iloc[-1])[:10]
        row = [
            n,
            seg,
            code,
            date,
            round(float(j_series(fr).iloc[-1]), 2),
            regime.get(date, ""),
            round(float(fr["close"].iloc[-1]), 4),
        ]
        rows.append(row)
        # 初始只画到当根；选择后才由 kcReveal 追加同挖掘段内后 20 根。
        # 未来段的指标由截至各未来日的完整历史算出，页面只负责隐藏 / 揭示。
        future_full = cp.chart_payload(df.iloc[: i + rr.FWD_N + 1], "")
        future = {
            k: future_full[k][-rr.FWD_N :]
            for k in (
                "dates",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "j",
                "dif",
                "dea",
                "hist",
            )
        }
        data[str(n)] = {
            **cp.chart_payload(fr, ""),
            "code": code,
            "marks": [],
            "future": future,
        }
        parts.append(
            f"<div class='s' data-id='{n}'><div class='t'>#{n}（J={row[4]}，0AMV 做多）</div>"
            "<div class='kc'></div></div>"
        )
    with (d / "labels.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEAD + [JUDGE, NOTE])
        for r in rows:
            w.writerow([*r, "", ""])
    kc = cp.kc_section(data)
    if not kc:
        raise RuntimeError("KLineChart 资源缺失或摘要不符——买点练习需要交互图")
    page = (
        "<html><meta charset='utf-8'><title>买点练习</title><body style='font-family:sans-serif'>"
        f"<h1>买点练习 · {html.escape(args.tag)}（{len(rows)} 个 B1 点，挖掘段）</h1>"
        "<p>每张图都是 B1 点（日线 J&lt;13 且 0AMV 做多），选择前只画到当根。判断：<b>买</b> = 当根收盘买；<b>不买</b>；"
        "<b>等</b> = 现在不买，等条件出现再考虑（批语里写等什么，如「等绿柱缩短」「等底分型」「等双线回 0 轴附近」「等企稳」）。"
        "每题选择后立即展示随后 20 根；已选择题刷新后仍展示，未选择题继续盲判。顶部「导出 labels.csv」留存，无需再跑 review。</p>"
        + "".join(parts)
        + kc
        + cp.label_ui(
            rows,
            "buypoint",
            f"buypoint:{d.name}",
            head=HEAD,
            hint="选择后展示该题后 20 根；批语写看到什么、等什么",
        )
        + r"""<style>div.s.done{opacity:1}</style><script>
document.addEventListener('click', ev => {
  const b = ev.target.closest('div.lbl button');
  if (!b) return;
  const el = b.closest('div.s');
  if (el && get(el.dataset.id).c) kcReveal(el.dataset.id);
});
M.rows.forEach(r => { if (get(r[0]).c) kcReveal(String(r[0])); });
</script>"""
        + "</body></html>"
    )
    (d / "gallery.html").write_text(page, encoding="utf-8")
    (d / "meta.json").write_text(
        json.dumps(
            {
                "tool": "buypoint_practice",
                "tag": args.tag,
                "seed": args.seed,
                "n": len(rows),
                "segments": segs,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return d


# ---------------------------------------------------------------------------
# 复盘
# ---------------------------------------------------------------------------


def _stat(xs: list[float]) -> dict[str, Any]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": round(st.mean(xs), 2),
        "median": round(st.median(xs), 2),
    }


def review_rows(labels: list[dict[str, str]], loader: Any = None) -> dict[str, Any]:
    """逐条复盘 + 汇总。``loader(code, seg) -> df``（测试注入）。"""
    loader = loader or _load
    cache: dict[tuple[str, str], Any] = {}
    per: list[dict[str, Any]] = []
    for x in labels:
        judge = (x.get(JUDGE) or "").strip()
        if judge not in ("买", "不买", "等"):
            continue
        key = (x["代码"], x["段"])
        if key not in cache:
            cache[key] = loader(*key)
        df = cache[key]
        ds = df["date"].astype(str).str[:10].tolist()
        i = ds.index(x["日期"])
        a = rr.arrays(df)
        limit = max(k for k, d in enumerate(ds) if d <= SEGS[x["段"]][1])
        note = x.get(NOTE) or ""
        tags = rr.tags_of(note)
        now = rr.forward(a, i, limit)
        rec: dict[str, Any] = {
            "id": x["id"],
            "code": x["代码"],
            "date": x["日期"],
            "judge": judge,
            "note": note,
            "tags": tags,
            "now": now,
        }
        rec["state"] = {t: rr.STATE_CHECK[t](a, i) for t in tags if t in rr.STATE_CHECK}
        rec["wait"] = {}
        for t in tags:
            if t in rr.WAIT_COND:
                jj = rr.first_trigger(a, i, rr.WAIT_COND[t], limit=limit)
                rec["wait"][t] = {
                    "day": None if jj is None else jj - i,
                    "fwd": rr.forward(a, jj, limit),
                }
        per.append(rec)
    if not per:
        raise RuntimeError(
            "空结果护栏：labels.csv 里 0 条有效判断（买 / 不买 / 等）——不落盘"
        )
    return {"per": per, "summary": summarize(per)}


def summarize(per: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [p for p in per if p["now"]]
    base = [p["now"]["r20"] for p in ok]
    by_judge: dict[str, Any] = {}
    for j in ("买", "不买", "等"):
        g = [p for p in ok if p["judge"] == j]
        by_judge[j] = {
            "r20": _stat([p["now"]["r20"] for p in g]),
            "up_rate": round(sum(p["now"]["r20"] > 0 for p in g) / len(g), 4)
            if g
            else None,
            "mfe": _stat([p["now"]["mfe"] for p in g]),
            "mae": _stat([p["now"]["mae"] for p in g]),
        }
    buy = [p for p in ok if p["judge"] == "买"]
    nob = [p for p in ok if p["judge"] == "不买"]
    hb = sum(p["now"]["r20"] > 0 for p in buy)
    hn = sum(p["now"]["r20"] <= 0 for p in nob)
    acc = {
        "buy_up": [hb, len(buy)],
        "nobuy_notup": [hn, len(nob)],
        "combined": [hb + hn, len(buy) + len(nob)],
        "base_up_rate": round(sum(r > 0 for r in base) / len(base), 4)
        if base
        else None,
    }
    state: dict[str, Any] = {}
    for t in rr.STATE_CHECK:
        v = [p["state"][t] for p in per if t in p["state"]]
        if v:
            state[t] = {"name": rr.TAG_NAME[t], "consistent": sum(v), "n": len(v)}
    wait: dict[str, Any] = {}
    for t in rr.WAIT_COND:
        g = [p for p in per if t in p["wait"] and p["now"]]
        if not g:
            continue
        trig = [p for p in g if p["wait"][t]["day"] is not None and p["wait"][t]["fwd"]]
        gave = [p for p in g if p["wait"][t]["day"] is None]
        wait[t] = {
            "name": rr.TAG_NAME[t],
            "n": len(g),
            "triggered": len(trig),
            "wait_days_median": st.median([p["wait"][t]["day"] for p in trig])
            if trig
            else None,
            "now_r20": _stat([p["now"]["r20"] for p in trig]),
            "trig_r20": _stat([p["wait"][t]["fwd"]["r20"] for p in trig]),
            "now_mae": _stat([p["now"]["mae"] for p in trig]),
            "trig_mae": _stat([p["wait"][t]["fwd"]["mae"] for p in trig]),
            "abandoned": len(gave),
            "abandon_right": sum(p["now"]["r20"] <= 0 for p in gave),
            "abandoned_now_r20": _stat([p["now"]["r20"] for p in gave]),
        }
    unk = [p for p in per if p["judge"] == "等" and not p["wait"]]
    return {
        "n": len(per),
        "n_forward": len(ok),
        "base_r20": _stat(base),
        "by_judge": by_judge,
        "accuracy": acc,
        "state_check": state,
        "wait": wait,
        "wait_unrecognized": [p["id"] for p in unk],
    }


def _fmt(s: dict[str, Any]) -> str:
    return (
        "—"
        if not s.get("n")
        else f"{s['mean']:+.1f}% / {s['median']:+.1f}%（n={s['n']}）"
    )


def render_md(rep: dict[str, Any]) -> str:
    s = rep["summary"]
    a = s["accuracy"]
    L = [
        "# 买点练习复盘（只描述，不作研究判据）",
        "",
        f"有效判断 {s['n']} 条；可算前向 20 日 {s['n_forward']} 条；同批基准 20 日 {_fmt(s['base_r20'])}，上涨比例 {s['accuracy']['base_up_rate']:.0%}。"
        if s["accuracy"]["base_up_rate"] is not None
        else f"有效判断 {s['n']} 条。",
        "",
        "## ① 判断 × 当根买入 20 日",
        "",
        "| 判断 | 20 日（均 / 中） | 上涨比例 | 20 日内最大涨 | 最大回撤 |",
        "|---|---|---|---|---|",
    ]
    for j, b in s["by_judge"].items():
        ur = "—" if b["up_rate"] is None else f"{b['up_rate']:.0%}"
        L.append(
            f"| {j} | {_fmt(b['r20'])} | {ur} | {_fmt(b['mfe'])} | {_fmt(b['mae'])} |"
        )
    L += [
        "",
        f"准确率：买 → 20 日上涨 {a['buy_up'][0]}/{a['buy_up'][1]}；不买 → 20 日不涨 {a['nobuy_notup'][0]}/{a['nobuy_notup'][1]}；合计 {a['combined'][0]}/{a['combined'][1]}。",
        "",
        "## ② 批语状态陈述 × 当根指标",
        "",
    ]
    for _t, v in s["state_check"].items():
        L.append(f"- {v['name']}：{v['consistent']}/{v['n']} 与指标一致")
    L += ["", "## ③ 等待条件（20 根内首个触发日）", ""]
    for _t, v in s["wait"].items():
        L.append(
            f"- **{v['name']}**：{v['n']} 条，触发 {v['triggered']}（中位等 {v['wait_days_median']} 天），"
            f"当根买 {_fmt(v['now_r20'])} → 触发买 {_fmt(v['trig_r20'])}；回撤 {_fmt(v['now_mae'])} → {_fmt(v['trig_mae'])}；"
            f"未触发即放弃 {v['abandoned']} 条，放弃正确 {v['abandon_right']}/{v['abandoned']}"
        )
    if s["wait_unrecognized"]:
        L.append(
            f"- 判「等」但批语里没识别出等待条件：#{'、#'.join(s['wait_unrecognized'])}（批语写明等什么才能复盘）"
        )
    L += [
        "",
        "## 逐条",
        "",
        "| # | 代码 | 日期 | 判断 | 批语 | 当根买 20 日 | 等待条件（触发天数 → 触发买 20 日） |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in rep["per"]:
        w = "；".join(
            f"{rr.TAG_NAME[t]} "
            + (
                "未触发"
                if v["day"] is None
                else f"{v['day']}天 → "
                + ("—" if not v["fwd"] else f"{v['fwd']['r20']:+.1f}%")
            )
            for t, v in p["wait"].items()
        )
        r = "—" if not p["now"] else f"{p['now']['r20']:+.1f}%"
        L.append(
            f"| {p['id']} | {p['code']} | {p['date']} | {p['judge']} | {p['note'].replace('|', '/')} | {r} | {w or '—'} |"
        )
    return "\n".join(L) + "\n"


def review(args: Any, loader: Any = None) -> Path:
    d = Path(args.dir)
    path = Path(args.labels) if args.labels else d / "labels.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        labels = list(csv.DictReader(fh))
    rep = review_rows(labels, loader)
    (d / "review.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (d / "review.md").write_text(render_md(rep), encoding="utf-8")
    return d / "review.md"


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="买点练习：B1 点盲判出题（make）/ 判断与批语复盘（review）。只描述，不作判据"
    )
    ap.add_argument("--mode", choices=("make", "review"), required=True)
    ap.add_argument("--n", type=int, default=100, help="make：题数")
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument(
        "--segments", choices=("both", "mining_a", "mining_b"), default="both"
    )
    ap.add_argument("--tag", default="v1")
    ap.add_argument(
        "--out-dir",
        default="",
        help="make：输出目录（缺省 artifacts/reports/calibration/buypoint__<tag>）",
    )
    ap.add_argument("--dir", default="", help="review：练习目录")
    ap.add_argument(
        "--labels",
        default="",
        help="review：导出的 labels.csv（缺省 = 目录内 labels.csv）",
    )
    return ap


def main(argv: Optional[list[str]] = None, **inj: Any) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    try:
        if args.mode == "make":
            print(f"[buypoint] 题目 ⇒ {make(args) / 'gallery.html'}")
        else:
            if not args.dir:
                raise ValueError("--mode review 需要 --dir")
            print(f"[buypoint] 复盘 ⇒ {review(args, inj.get('loader'))}")
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
