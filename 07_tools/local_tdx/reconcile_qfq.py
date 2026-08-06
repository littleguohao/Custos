#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""前复权对账：拿 qlib 的前复权序列给 tdx 自算结果做**独立参照**。

## 为什么必须对账

`adjust_factors` 自 2026-08-04 起是**全链默认口径**（owner 拍板），决定所有价格。
而 grep 整个模块与其测试，**没有一行对账代码** —— 它出过两个 bug
（BJ 分支写反、`out[:500]` 截断保留最旧的权息事件，方向正好反了），
**修了 bug 不等于验证了正确性**。

`S_DATA` 的 qlib bundle **本身就是前复权**，是一个完全独立的实现和数据源
⇒ 现成的交叉验证，不需要任何新数据。

## 怎么比：比「比值是否恒定」，不比绝对价

两条都是前复权序列，但**基准日不同**（tdx 归一到最后一根，qlib 未必），
所以绝对价一定不等。数学上：

    tdx_t  = raw_t × f_t        f_t = ∏(t 之后的除权比)
    qlib_t = raw_t × g_t        g_t 同理，只是基准不同

若两边用了**同一套事件与同一个比例公式**，则 `f_t / g_t` 是常数
⇒ **`tdx_t / qlib_t` 应当在整个区间恒定**。

所以：
  · 比值的**相对离散度**（max/min − 1）小 ⇒ 两边一致
  · 比值在某一天**跳变** ⇒ 那天两边对某个事件的处理不同，**跳变幅度就是分歧比例**

同时给出**日收益**逐日对比：`ret_tdx` vs `ret_qlib`，差异超阈值的日子直接列出来
——它能把问题定位到具体日期，比一个总体指标有用。

⚠️ **只比 close。** 成交量的复权处理两边未必一致（我们除以因子，qlib 未必），
比它会引入与价格正确性无关的噪声。

## 已知的对账边界

qlib bundle 有两个坑（见 `00_governance/data/QLIB_LOCAL_DATA.md`）：
  · 2020-09-28 → 2021-07-30 约 **10 个月缺口**
  · 数据到 **2026-02** 截止
⇒ 默认对账窗口取 2021-08-01 ~ 2026-02-01，落在第二个 bundle 内部，避开缺口与右端。

用法：
    # 自动挑「除权影响最大」的票（累计因子偏离 1 最多）
    uv run python 07_tools/local_tdx/reconcile_qfq.py --auto 20

    # 指定代码
    uv run python 07_tools/local_tdx/reconcile_qfq.py --codes 600000,000001,920808
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Optional

BASE = pathlib.Path(__file__).resolve().parents[2]
for _p in ("07_tools", "07_tools/local_tdx", "07_tools/screening"):
    sys.path.insert(0, str(BASE / _p))

OUTDIR = BASE / "06_logs" / "qfq_reconcile"

# 对账窗口：落在 2021_2026 bundle 内部，避开 10 个月缺口与 2026-02 右端
WIN_START = "2021-08-02"
WIN_END = "2026-01-31"

RATIO_TOL = 0.005          # 比值离散度容忍：0.5%
RET_TOL = 0.002            # 日收益差容忍：0.2 个百分点


def _load_tdx(code: str) -> Any:
    import local_tdx_data as L
    df = L.get_ohlcv_table(code, count=2000)      # 默认 adjust="qfq"
    if df is None or df.empty:
        return None, "tdx 无数据"
    if "date" not in df.columns:
        return None, "tdx 缺 date 列"
    adj = df.attrs.get("adjust")
    n_ev = df.attrs.get("adjust_events")
    if adj != "qfq":
        return None, f"tdx 未复权（adjust={adj!r}）"
    out = df[["date", "close"]].copy()
    out["date"] = out["date"].astype(str).str[:10]
    return out, f"adjust_events={n_ev}"


def _load_qlib(code: str) -> Any:
    import s_data as Q
    d = Q.load_bars_qlib([code], 2000, start=WIN_START, end=WIN_END)
    df = d.get(code)
    if df is None or df.empty:
        return None, "qlib 无数据"
    out = df[["date", "close"]].copy()
    out["date"] = out["date"].astype(str).str[:10]
    return out, ""


def reconcile(code: str) -> dict:
    """对账单只票。**绝不 raise** —— 一只票的问题不该中断整轮。"""
    res: dict[str, Any] = {"code": code, "status": "", "note": ""}
    try:
        import pandas as pd
        t, tnote = _load_tdx(code)
        if t is None:
            res.update(status="skip", note=tnote)
            return res
        q, qnote = _load_qlib(code)
        if q is None:
            res.update(status="skip", note=qnote)
            return res
        m = t.merge(q, on="date", how="inner", suffixes=("_tdx", "_qlib"))
        m = m[(m["date"] >= WIN_START) & (m["date"] <= WIN_END)]
        m = m[(m["close_tdx"] > 0) & (m["close_qlib"] > 0)].reset_index(drop=True)
        if len(m) < 60:
            res.update(status="skip", note=f"重叠仅 {len(m)} 根，样本太少")
            return res

        # ① 比值恒定性：两条前复权序列只应差一个全局常数
        ratio = m["close_tdx"] / m["close_qlib"]
        spread = float(ratio.max() / ratio.min() - 1.0)

        # ② 日收益逐日对比：定位到具体哪一天分歧
        r_t = m["close_tdx"].pct_change()
        r_q = m["close_qlib"].pct_change()
        diff = (r_t - r_q).abs()
        bad = m.loc[diff > RET_TOL, "date"].tolist()
        worst = float(diff.max()) if len(diff.dropna()) else 0.0

        res.update(
            status="ok" if (spread <= RATIO_TOL and not bad) else "mismatch",
            bars=len(m), ratio_spread=round(spread, 6),
            worst_ret_diff=round(worst, 6),
            mismatch_days=bad[:10], n_mismatch=len(bad),
            note=tnote,
        )
        return res
    except Exception as exc:                                   # noqa: BLE001
        res.update(status="error", note=f"{type(exc).__name__}: {exc}")
        return res


def pick_auto(n: int, cache_dir: Optional[pathlib.Path] = None) -> list[str]:
    """自动挑「除权影响最大」的票——除权幅度大的最有判别力。

    从已有 xdxr 缓存里挑（不发网络请求）：只有除权幅度大的票才能暴露公式分歧，
    从未除权的票两边必然一致、对账毫无信息量。

    ⚠️ `cache_dir` 显式传参而不是读模块全局：`07_tools/` 下的模块既可能被
    `import adjust_factors` 也可能被 `from local_tdx import adjust_factors` 加载，
    **同一文件会成为两个模块对象**，模块级常量各存一份 —— monkeypatch 只影响其中一个，
    于是测试绿而生产失效（`DATA_SOURCE_PRINCIPLE.md`「同一文件不得被加载成两个模块」，
    写这份工具时当场撞到）。显式传参从根上避开它。
    """
    if cache_dir is None:
        import adjust_factors as A
        cache_dir = A.CACHE_DIR
    scored: list[tuple[float, str]] = []
    for p in sorted(pathlib.Path(cache_dir).glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            continue
        ev = d.get("events") or []
        if not ev:
            continue
        # 粗打分：送转与分红的总量（不必精确，只用于排序）
        score = sum(abs(float(e.get("songzhuangu") or 0)) * 10
                    + abs(float(e.get("fenhong") or 0)) for e in ev)
        scored.append((score, d.get("code") or p.stem))
    scored.sort(reverse=True)
    if not scored:
        print("[WARN] xdxr 缓存为空，无法自动挑票；请用 --codes 指定，"
              "或先跑一次带前复权的选股/回测把缓存焐热", file=sys.stderr)
    return [c for _, c in scored[:n]]


def report(rows: list[dict]) -> int:
    hdr = (f"{'代码':<10}{'状态':>8}{'重叠根数':>9}{'比值离散':>10}"
           f"{'最大日收益差':>13}{'分歧日数':>9}  备注")
    print("\n" + "=" * 100)
    print(f"前复权对账：tdx 自算 vs qlib（窗口 {WIN_START} ~ {WIN_END}）")
    print("=" * 100)
    print(hdr)
    print("-" * 100)
    for r in rows:
        spread = f"{r['ratio_spread']:.4%}" if "ratio_spread" in r else "—"
        worst = f"{r['worst_ret_diff']:.4%}" if "worst_ret_diff" in r else "—"
        print(f"{r['code']:<10}{r['status']:>8}{r.get('bars', '—'):>9}"
              f"{spread:>10}{worst:>13}{r.get('n_mismatch', '—'):>9}"
              f"  {r.get('note', '')[:30]}")
    ok = [r for r in rows if r["status"] == "ok"]
    bad = [r for r in rows if r["status"] == "mismatch"]
    skip = [r for r in rows if r["status"] in ("skip", "error")]
    print(f"\n一致 {len(ok)} / 分歧 {len(bad)} / 跳过 {len(skip)}"
          f"（阈值：比值离散 ≤{RATIO_TOL:.1%}，日收益差 ≤{RET_TOL:.1%}）")
    if bad:
        print("\n⚠️ **分歧明细**（比值跳变的日子就是两边对某个事件处理不同的日子）：")
        for r in bad:
            print(f"   {r['code']}: 比值离散 {r['ratio_spread']:.4%}，"
                  f"最大日收益差 {r['worst_ret_diff']:.4%}，"
                  f"分歧 {r['n_mismatch']} 天，前 10 天 {r['mismatch_days']}")
        print("\n   ⇒ 拿其中一天去查 `01_data/market/xdxr/{code}.json` 里那天附近的事件，"
              "对照 `event_ratio()` 的公式。")
    if skip:
        print(f"\n跳过原因分布：")
        from collections import Counter
        for k, v in Counter(r.get("note", "")[:40] for r in skip).most_common():
            print(f"   {v:>3} 只  {k}")
    print("\n⚠️ 只比 close：成交量的复权处理两边未必一致，比它会引入与价格正确性无关的噪声。")
    print("⚠️ 对账窗口刻意落在 2021_2026 bundle 内部 —— qlib 有 2020-09~2021-07 缺口、"
          "数据到 2026-02 截止。")
    return 1 if bad else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="前复权对账（tdx 自算 vs qlib 独立参照）")
    ap.add_argument("--codes", default="", help="逗号分隔的 6 位代码")
    ap.add_argument("--auto", type=int, default=0,
                    help="自动挑除权影响最大的 N 只（从 xdxr 缓存里挑，不发网络）")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    codes = [c.strip()[:6] for c in a.codes.split(",") if c.strip()]
    if a.auto:
        codes += [c for c in pick_auto(a.auto) if c not in codes]
    if not codes:
        print("需要 --codes 或 --auto N")
        return 2

    rows = []
    for i, c in enumerate(codes, 1):
        print(f"[{i}/{len(codes)}] {c} …", flush=True)
        rows.append(reconcile(c))
    rc = report(rows)

    from paths import cn_now  # noqa: PLC0415
    out = pathlib.Path(a.out) if a.out else \
        OUTDIR / f"{cn_now().strftime('%Y%m%d_%H%M')}_qfq_reconcile.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "reconciled_at": cn_now().isoformat(),
        "window": [WIN_START, WIN_END],
        "tolerance": {"ratio_spread": RATIO_TOL, "ret_diff": RET_TOL},
        "results": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] 报告已落盘 {out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
