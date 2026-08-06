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
    """取 tdx 前复权序列，**并带上 `raw_close`（未复权收盘）作第三方基准**。

    ⚠️ `raw_close` 是定位「谁错」的关键：**非事件日的未复权收益必须等于复权收益**
    （两者只差一个当日相同的因子）。所以谁的日收益偏离 `ret_raw`，就是谁错。
    没有它只能说「两边不一致」，说不出方向。
    """
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
    cols = ["date", "close"] + (["raw_close"] if "raw_close" in df.columns else [])
    out = df[cols].copy()
    out["date"] = out["date"].astype(str).str[:10]
    return out, f"adjust_events={n_ev}"


def _limit_pct(code: str) -> float:
    """按代码前缀推断涨跌幅限制 —— **日收益超过它就是数据错**（物理不可能）。"""
    c = str(code).strip()[:6]
    if c.startswith(("688", "300", "301")):
        return 20.0
    if c.startswith(("920", "83", "87", "43")):
        return 30.0
    return 10.0


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
        # ⚠️ **只算落在对账窗口内的事件。** 第一版按全历史打分，结果 20 只里有 7 只的
        # 事件全在窗口之前 ⇒ `adjust_events=0`、复权因子恒为 1 ⇒ 两边比的其实是未复权价，
        # 判"一致"对我们的公式**零信息量**。挑票必须挑窗口内真有除权的。
        ev = [e for e in ev if WIN_START <= str(e.get("date"))[:10] <= WIN_END]
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


def detail(code: str, top: int = 25) -> int:
    """单只票的分歧明细：把数字摊开，**区分两种完全不同的分歧形态**。

    ## 为什么必须区分

    · **事件日阶梯**：比值分段恒定、只在除权日跳变 ⇒ 两边对**某个事件的比例公式**处理不同。
      分歧日数应当 ≈ 事件数。
    · **弥散噪声**：比值连续游走、分歧日散布在非事件日 ⇒ **原始价格本身有差异**
      （数据源口径不同、精度、停牌处理…），与我们的复权公式无关。

    实测（2026-08-06）两种都出现了：`600622` 1 个事件 3 天分歧（像阶梯），
    而 `600519` 9 个事件却 134 天分歧、起始日是连续交易日（茅台除权一年一次 ⇒ 不可能是阶梯）。
    **不分开看就会把数据源差异误判成自己的公式 bug，或反之。**
    """
    import pandas as pd
    t, tnote = _load_tdx(code)
    q, qnote = _load_qlib(code)
    if t is None or q is None:
        print(f"{code}: 取数失败 tdx={tnote!r} qlib={qnote!r}")
        return 2
    m = t.merge(q, on="date", how="inner", suffixes=("_tdx", "_qlib"))
    m = m[(m["date"] >= WIN_START) & (m["date"] <= WIN_END)]
    m = m[(m["close_tdx"] > 0) & (m["close_qlib"] > 0)].reset_index(drop=True)
    m["ratio"] = m["close_tdx"] / m["close_qlib"]
    m["ret_tdx"] = m["close_tdx"].pct_change()
    m["ret_qlib"] = m["close_qlib"].pct_change()
    m["ret_diff"] = (m["ret_tdx"] - m["ret_qlib"]).abs()

    # ★ 第三方基准：未复权收益。**非事件日的未复权收益必须等于复权收益**
    #   ⇒ 谁偏离 ret_raw 谁错。这是唯一能定方向的判据。
    has_raw = "raw_close" in m.columns
    if has_raw:
        m["ret_raw"] = m["raw_close"].pct_change()
        m["d_tdx_raw"] = (m["ret_tdx"] - m["ret_raw"]).abs()
        m["d_qlib_raw"] = (m["ret_qlib"] - m["ret_raw"]).abs()

    lim = _limit_pct(code) / 100.0

    # 事件日期（窗口内）
    ev_dates: set[str] = set()
    try:
        import adjust_factors as A
        for e in A.get_xdxr(code):
            d = str(e.get("date"))[:10]
            if WIN_START <= d <= WIN_END:
                ev_dates.add(d)
    except Exception as exc:                                   # noqa: BLE001
        print(f"[WARN] 取不到 {code} 的事件表: {exc}", file=sys.stderr)

    dates = m["date"].tolist()
    idx_of = {d: i for i, d in enumerate(dates)}

    def _near_event(d: str, win: int = 2) -> str:
        i = idx_of.get(d)
        if i is None:
            return ""
        for ed in ev_dates:
            j = idx_of.get(ed)
            if j is not None and abs(i - j) <= win:
                return f"事件 {ed} ±{abs(i - j)}根"
        return ""

    bad = m[m["ret_diff"] > RET_TOL].copy()
    bad["near"] = bad["date"].map(_near_event)
    n_ev_aligned = int((bad["near"] != "").sum())

    print(f"\n{'=' * 104}")
    print(f"{code} 分歧明细   窗口内事件 {len(ev_dates)} 个   重叠 {len(m)} 根   "
          f"比值离散 {m['ratio'].max() / m['ratio'].min() - 1:.4%}")
    print("=" * 104)
    print(f"分歧日 {len(bad)} 天，其中**落在事件日 ±2 根内**的 {n_ev_aligned} 天、"
          f"非事件日 {len(bad) - n_ev_aligned} 天")
    if len(bad):
        share = n_ev_aligned / len(bad)
        if share >= 0.8:
            print("  ⇒ 形态判定：**事件日阶梯** —— 分歧集中在除权日，"
                  "指向复权比例公式（event_ratio）两边不同")
        elif share <= 0.2:
            print("  ⇒ 形态判定：**弥散噪声** —— 分歧散布在非事件日，"
                  "指向**原始价格本身有差异**（数据源口径），与复权公式无关")
        else:
            print("  ⇒ 形态判定：**混合** —— 两类问题同时存在，先看下表逐日数字")

    # ★ 定方向：非事件日谁偏离未复权收益，谁错
    if has_raw and len(bad):
        nb = bad[bad["near"] == ""]                    # 只看非事件日
        if len(nb):
            t_off = int((nb["d_tdx_raw"] > RET_TOL).sum())
            q_off = int((nb["d_qlib_raw"] > RET_TOL).sum())
            print(f"\n  ★ **谁错**（非事件日 {len(nb)} 天，与未复权收益比对）：")
            print(f"      tdx  偏离未复权收益 > {RET_TOL:.1%} 的：{t_off} 天")
            print(f"      qlib 偏离未复权收益 > {RET_TOL:.1%} 的：{q_off} 天")
            print(f"      判据：非事件日复权只是乘同一个当日因子 ⇒ 收益必须与未复权一致。")
            # ⚠️ 不用「≥N 天」这种机械阈值：**一边一天都不偏离**本身就足够定性。
            if t_off == 0 and q_off > 0:
                print(f"      ⇒ **qlib 侧有问题**：tdx 与未复权收益完全一致（0 天偏离），"
                      f"qlib 偏离 {q_off} 天")
            elif q_off == 0 and t_off > 0:
                print(f"      ⇒ **我们的自算前复权有问题**：qlib 与未复权收益完全一致，"
                      f"tdx 偏离 {t_off} 天")
            elif q_off > t_off * 3:
                print(f"      ⇒ 倾向 **qlib 侧有问题**（偏离 {q_off} 天 vs tdx {t_off} 天）")
            elif t_off > q_off * 3:
                print(f"      ⇒ 倾向 **我们有问题**（偏离 {t_off} 天 vs qlib {q_off} 天）")
            else:
                print(f"      ⇒ 两边都偏离，需再看逐日数字（可能是第三个原因："
                      f"两边的交易日集合或停牌处理不同）")

    # ★ 涨跌停越界：日收益超过限制是物理不可能，直接证伪那一侧
    over_t = m[m["ret_tdx"].abs() > lim * 1.005]
    over_q = m[m["ret_qlib"].abs() > lim * 1.005]
    print(f"\n  ★ **涨跌停越界**（{code} 限制 ±{lim:.0%}，超过即物理不可能）：")
    print(f"      tdx  越界 {len(over_t)} 天   qlib 越界 {len(over_q)} 天")
    if len(over_q) and not len(over_t):
        print(f"      ⇒ **只有 qlib 越界** ⇒ 那些天 qlib 的价格是错的")
        for _, r in over_q.nlargest(min(5, len(over_q)), "ret_qlib").iterrows():
            print(f"        {r['date']}  qlib {r['ret_qlib']:+.4%}  vs  tdx {r['ret_tdx']:+.4%}")
    elif len(over_t) and not len(over_q):
        print(f"      ⇒ **只有 tdx 越界** ⇒ 我们的复权把收益放大了")

    if has_raw:
        print(f"\n{'日期':<12}{'tdx复权':>10}{'tdx未复权':>11}{'qlib':>10}{'比值':>9}"
              f"{'ret_tdx':>10}{'ret_raw':>10}{'ret_qlib':>10}"
              f"{'|t-raw|':>9}{'|q-raw|':>9}  近邻事件")
        print("-" * 122)
        for _, r in bad.nlargest(top, "ret_diff").iterrows():
            print(f"{r['date']:<12}{r['close_tdx']:>10.4f}{r['raw_close']:>11.4f}"
                  f"{r['close_qlib']:>10.4f}{r['ratio']:>9.5f}"
                  f"{r['ret_tdx']:>+10.4%}{r['ret_raw']:>+10.4%}{r['ret_qlib']:>+10.4%}"
                  f"{r['d_tdx_raw']:>9.4%}{r['d_qlib_raw']:>9.4%}  {r['near']}")
    else:
        print(f"\n{'日期':<12}{'tdx收盘':>11}{'qlib收盘':>11}{'比值':>10}"
              f"{'ret_tdx':>10}{'ret_qlib':>10}{'差':>9}  近邻事件")
        print("-" * 104)
        for _, r in bad.nlargest(top, "ret_diff").iterrows():
            print(f"{r['date']:<12}{r['close_tdx']:>11.4f}{r['close_qlib']:>11.4f}"
                  f"{r['ratio']:>10.5f}{r['ret_tdx']:>+10.4%}{r['ret_qlib']:>+10.4%}"
                  f"{r['ret_diff']:>+9.4%}  {r['near']}")

    # 比值的阶梯结构：把比值按 0.1% 粒度分箱，看有几个"平台"
    lvl = (m["ratio"] / m["ratio"].iloc[0]).round(3)
    plateaus = int(lvl.nunique())
    print(f"\n比值平台数（按 0.1% 粒度）：{plateaus}"
          f"   {'⇒ 分段恒定，像事件阶梯' if plateaus <= max(3, len(ev_dates) + 2) else '⇒ 连续游走，不是事件阶梯'}")
    print("事件日当天的比值跳变：")
    for ed in sorted(ev_dates):
        i = idx_of.get(ed)
        if i is None or i == 0:
            continue
        jump = m["ratio"].iloc[i] / m["ratio"].iloc[i - 1] - 1
        print(f"  {ed}  比值 {m['ratio'].iloc[i - 1]:.5f} → {m['ratio'].iloc[i]:.5f}"
              f"  ({jump:+.4%})")
    return 0


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
    # ⚠️ adjust_events=0 的"一致"是**零信息量**：因子恒为 1，两边比的其实是未复权价
    vacuous = [r for r in ok if "adjust_events=0" in (r.get("note") or "")]
    print(f"\n一致 {len(ok)} / 分歧 {len(bad)} / 跳过 {len(skip)}"
          f"（阈值：比值离散 ≤{RATIO_TOL:.1%}，日收益差 ≤{RET_TOL:.1%}）")
    if vacuous:
        print(f"⚠️ 其中 {len(vacuous)} 只是 **adjust_events=0**（窗口内无除权 ⇒ 因子恒为 1）"
              f"——它们的\"一致\"只说明原始价一致，**对复权公式零信息量**。")
        print(f"   有效样本 = {len(ok) - len(vacuous)} 一致 + {len(bad)} 分歧"
              f" = {len(ok) - len(vacuous) + len(bad)} 只")
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
    ap.add_argument("--detail", default="",
                    help="单只票的分歧明细：逐日数字 + 区分「事件日阶梯」与「弥散噪声」")
    ap.add_argument("--top", type=int, default=25, help="--detail 列出最差的 N 天")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    if a.detail:
        return detail(a.detail.strip()[:6], a.top)

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
