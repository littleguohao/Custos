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


def gap_report(sample: int = 200,
               root: Optional[pathlib.Path] = None) -> int:
    """量化 bundle 缺口的真实代价，并判断有没有可行的修法。

    ## 缺口是什么

    两个 bundle 之间不连续（实测）：

        2006_2020   1999-11-10 ~ 2020-09-25
        2021_2026   2021-08-02 ~ 2026-02-06
        ⇒ 缺口约 10 个月（2020-09-28 ~ 2021-07-30）

    ## 三个要量的事

    **① vipdoc 的深度是不是「下载设置」而非硬限制。** 实测 600000 只有 1214 根
    （约 5 年，2021-06 起）。若**全市场票的根数都差不多**，说明是通达信客户端的
    下载范围设置 ⇒ **重新下载完整历史就能让 vipdoc 覆盖缺口及更早**，
    这比"补 bundle"简单得多，而且顺带解决"跨年 walk-forward 只有 5 年"的限制。
    若各票根数差异很大，则是逐票数据可得性问题，另说。

    **② 缺口的真实代价 = 那段时间退市的票。** 仍在市的票，缺口期的行情可以由
    vipdoc 补（前提是①成立）。**真正补不回来的只有在缺口期间退市的票** ——
    而它们恰恰是"去幸存者偏差"要的样本。用「在老 bundle 的 all.txt 里、
    但不在新 bundle 里」近似识别。

    **③ 缺口对现有窗口是否构成实际约束。** `--cross-window` 用的 2022-01~2024-12
    落在缺口之后，不受影响；只有跨 2020-09~2021-08 的窗口才会踩到。
    """
    import s_data as Q
    # ⚠️ `root` 显式传参：`list_bundles(root=DEFAULT_Q_ROOT)` 的默认值在**函数定义时**
    # 就绑定了，monkeypatch 模块常量对它无效（与 `pick_auto` 的 cache_dir 同一个坑，
    # 也是 DATA_SOURCE_PRINCIPLE「同一文件两个模块」那类问题的近亲）。
    bundles = Q.list_bundles(root) if root is not None else Q.list_bundles()
    if len(bundles) < 2:
        print(f"只发现 {len(bundles)} 个 bundle，无缺口可言")
        return 0

    print(f"\n{'=' * 92}")
    print("bundle 缺口代价诊断")
    print("=" * 92)
    print(f"{'bundle':<14}{'口径':<16}{'起':<13}{'止':<13}{'交易日':>8}")
    for b in bundles:
        print(f"{b['dir'].name:<14}{b.get('convention', '?'):<16}"
              f"{b['start']:<13}{b['end']:<13}{len(b['calendar']):>8}")
    # ⚠️ 不能只判 `a.end < b.start` —— 那对任何不重叠的相邻 bundle 都成立，
    # 连"上一份到 08-01、下一份从 08-02 开始"这种**无缝衔接**也会被报成缺口。
    # 用日历天数阈值：超过一周才算真缺口（跨周末/长假不算）。
    from datetime import date
    GAP_MIN_DAYS = 7
    gaps = []
    for a, b in zip(bundles, bundles[1:]):
        try:
            d0, d1 = date.fromisoformat(a["end"]), date.fromisoformat(b["start"])
        except ValueError:
            continue
        if (d1 - d0).days > GAP_MIN_DAYS:
            gaps.append((a["end"], b["start"], a["dir"].name, b["dir"].name))
    if not gaps:
        print("\n✅ 无缺口")
    for g0, g1, n0, n1 in gaps:
        span = (date.fromisoformat(g1) - date.fromisoformat(g0)).days
        print(f"\n⚠️ 缺口：{g0} → {g1}（{span} 个日历日，{n0} 结束 ~ {n1} 开始）")

    # ① vipdoc 深度分布 —— 判断是不是下载设置
    print(f"\n{'─' * 92}\n① vipdoc 深度分布（判断能否靠重新下载补齐）")
    try:
        import local_tdx_data as L
        codes = L.list_local_vipdoc_codes()
        import random
        pick = random.Random(0).sample(codes, min(sample, len(codes)))
        depths, firsts = [], []
        for c in pick:
            try:
                df = L.read_vipdoc_daily(c)
            except Exception:                                  # noqa: BLE001
                continue
            if df is None or df.empty or "date" not in df.columns:
                continue
            depths.append(len(df))
            firsts.append(str(df["date"].iloc[0])[:10])
        if depths:
            depths.sort(), firsts.sort()
            n = len(depths)
            print(f"   抽样 {n} 只：根数 min {depths[0]} / 中位 {depths[n // 2]} / "
                  f"max {depths[-1]}")
            print(f"   最早日期：min {firsts[0]} / 中位 {firsts[n // 2]} / max {firsts[-1]}")
            spread = depths[-1] / max(depths[n // 2], 1)
            if spread < 1.5:
                print("   ⇒ 各票根数接近 ⇒ **是通达信的下载范围设置**，"
                      "在客户端重新下载完整历史即可覆盖缺口及更早")
            else:
                print("   ⇒ 各票根数差异大 ⇒ 更像逐票可得性/上市时间差异，"
                      "重新下载能补多少要再看")
        else:
            print("   读不到 vipdoc（本机无通达信？）")
    except Exception as exc:                                   # noqa: BLE001
        print(f"   跳过：{type(exc).__name__}: {exc}")

    # ② 缺口的真实代价：那段时间退市的票
    print(f"\n{'─' * 92}\n② 缺口的真实代价 = 缺口期间退市的票（仍在市的可由 vipdoc 补）")
    try:
        sets = {}
        for b in bundles:
            inst = b["dir"] / "instruments" / "all.txt"
            if not inst.is_file():
                continue
            s = set()
            for ln in inst.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                digits = "".join(ch for ch in ln.split()[0] if ch.isdigit())
                if len(digits) == 6:
                    s.add(digits)
            sets[b["dir"].name] = s
        names = list(sets)
        if len(names) >= 2:
            old, new = sets[names[0]], sets[names[-1]]
            only_old = old - new
            print(f"   {names[0]}: {len(old)} 只   {names[-1]}: {len(new)} 只")
            print(f"   **只在老 bundle 里**（= 新 bundle 开始前已退市）：{len(only_old)} 只")
            try:
                import local_tdx_data as L
                live = set(L.list_local_vipdoc_codes())
                gone = only_old - live
                print(f"   其中本地 vipdoc 也没有的：{len(gone)} 只"
                      f" ⇒ **这些票的缺口期行情无处可补**")
            except Exception:                                  # noqa: BLE001
                pass
            print("   ⇒ 这个数就是缺口对「去幸存者偏差」的实际代价上限")

        # ★ 每个 bundle 的**去偏价值** = 它有多少票是本地 vipdoc 没有的（≈ 已退市）
        #   这一步能回答两件事：
        #   ① 某个 bundle 到底值不值得留（去偏价值为 0 就是纯负债）
        #   ② 它是不是**从 vipdoc 生成的** —— 若 instrument 几乎全在 vipdoc 里、
        #      且起始日与 vipdoc 一致，那它的价格口径问题就是**我们自己转换脚本的 bug**
        try:
            import local_tdx_data as L
            live = set(L.list_local_vipdoc_codes())
            print(f"\n   本地 vipdoc: {len(live)} 只")
            print(f"   {'bundle':<14}{'票数':>7}{'不在 vipdoc':>12}{'占比':>8}  去偏价值")
            for b in bundles:
                nm = b["dir"].name
                s0 = sets.get(nm)
                if not s0:
                    continue
                miss = s0 - live
                pct = len(miss) / max(len(s0), 1)
                verdict = ("**为 0 ⇒ 纯负债**（universe≈在市股，无退市票）"
                           if not miss else f"{len(miss)} 只退市票可用于去偏")
                print(f"   {nm:<14}{len(s0):>7}{len(miss):>12}{pct:>7.1%}  {verdict}")
            print("   ⇒ 去偏价值为 0 且价格口径有问题的 bundle，应当直接不用")
        except Exception as exc:                               # noqa: BLE001
            print(f"   （跳过 vipdoc 对比：{type(exc).__name__}）")
    except Exception as exc:                                   # noqa: BLE001
        print(f"   跳过：{type(exc).__name__}: {exc}")

    # ③ 现有窗口是否踩到缺口
    print(f"\n{'─' * 92}\n③ 现有窗口是否踩缺口")
    for label, w0, w1 in (("m2 --cross-window", "2022-01-01", "2024-12-31"),
                          ("qfq 对账默认窗口", WIN_START, WIN_END)):
        hit = any(not (w1 < g0 or w0 > g1) for g0, g1, _, _ in gaps)
        print(f"   {label:<22}{w0}~{w1}   {'⚠️ 跨缺口' if hit else '✅ 不跨'}")
    print("\n⇒ 只有跨 2020-09~2021-08 的窗口会踩到；避开即可。")
    return 0


def qlib_selfcheck(code: str) -> int:
    """**不依赖 tdx** 判定每个 bundle 自己的价格口径 —— 用它自带的 `factor` / `change`。

    ## 为什么需要这条路

    本地 vipdoc 只有约 **1214 根 K 线（约 5 年，2021-06 起）**，
    与 `2006_2020` bundle **没有重叠期** ⇒ 那个 bundle 根本没法用 tdx 对账
    （`--convention --win 2018-01-02 2020-09-25` 实测「重叠仅 0 根」）。

    但老 bundle 自带两个字段可以自洽检验：

        change  日收益率 ⇒ 与 `close.pct_change()` 比对，可知 close 的收益口径
        factor  复权因子 ⇒ 若 close 是原始价，`close × factor` 应在除权日连续；
                          若 close 已复权，`close / factor` 应还原出除权日的跳空

    再配合我们自己的 xdxr 事件日，就能判断：**close 到底是原始价还是已复权价、
    以及复权是乘法还是加法。**
    """
    import numpy as np
    import pandas as pd
    import s_data as Q
    bundles = Q.list_bundles()
    if not bundles:
        print("没有发现 bundle（检查 S_DATA_ROOT）")
        return 2

    ev: list[str] = []
    try:
        import adjust_factors as A
        ev = sorted({str(e.get("date"))[:10] for e in A.get_xdxr(code)})
    except Exception as exc:                                   # noqa: BLE001
        print(f"[WARN] 取不到 {code} 的 xdxr 事件表: {exc}", file=sys.stderr)

    for bd, inst in Q.code_to_qlib_dir(code, bundles):
        cal = next(b["calendar"] for b in bundles if b["dir"] == bd)
        fdir = bd / "features" / inst
        have = {p.name.split(".")[0] for p in fdir.glob("*.bin")}
        print(f"\n{'=' * 96}")
        print(f"{code} @ {bd.name}   日历 {cal[0]}~{cal[-1]} ({len(cal)} 日)   "
              f"字段 {sorted(have)}")
        print("=" * 96)
        cols: dict[str, Any] = {}
        for f in ("close", "factor", "change"):
            a = Q._read_field_bin(fdir, f, len(cal))
            if a is not None:
                cols[f] = a
        if "close" not in cols:
            print("  没有 close，跳过")
            continue
        df = pd.DataFrame({"date": cal, **cols}).dropna(subset=["close"])
        df = df[df["close"] > 0].reset_index(drop=True)
        print(f"  有效 close {len(df)} 根，{df['date'].iloc[0]}~{df['date'].iloc[-1]}")

        # ① close 的收益 vs change 字段
        if "change" in df.columns:
            r = df["close"].pct_change()
            d = (r - df["change"]).abs().dropna()
            hit = float((d < 1e-4).mean()) if len(d) else float("nan")
            print(f"\n  ① close.pct_change() 与 change 字段一致率: {hit:.2%}"
                  f"   中位差 {float(d.median()):.6%}" if len(d) else "")
            print(f"     ⇒ {'change 就是 close 的收益（同一口径）' if hit > 0.9 else 'change 与 close 不同口径 —— change 可能是**原始**收益而 close 已被调整'}")

        # ② 除权日：close 有没有跳空（原始价特征）
        in_win_ev = [d0 for d0 in ev if df["date"].iloc[0] <= d0 <= df["date"].iloc[-1]]
        if in_win_ev:
            idx = {d0: i for i, d0 in enumerate(df["date"])}
            print(f"\n  ② 除权日当天 close 的日收益（窗口内 {len(in_win_ev)} 个事件）：")
            for d0 in in_win_ev[:12]:
                i = idx.get(d0)
                if not i:
                    continue
                r = df["close"].iloc[i] / df["close"].iloc[i - 1] - 1
                fac = ""
                if "factor" in df.columns:
                    f0, f1 = df["factor"].iloc[i - 1], df["factor"].iloc[i]
                    fac = f"  factor {f0:.6f}→{f1:.6f} ({f1 / f0 - 1:+.4%})" if f0 else ""
                print(f"     {d0}  close {df['close'].iloc[i - 1]:.4f}→"
                      f"{df['close'].iloc[i]:.4f}  ({r:+.4%}){fac}")
            print("     ⇒ 除权日出现**大幅负跳空** ⇒ close 是**原始价**（未复权）；"
                  "平滑 ⇒ close 已复权")
        else:
            print("\n  ② 该 bundle 区间内没有 xdxr 事件，无法用除权日判断")

        # ③ factor 的形态
        if "factor" in df.columns:
            fs = df["factor"].dropna()
            if len(fs):
                nuniq = int(fs.round(6).nunique())
                print(f"\n  ③ factor: 范围 {fs.min():.6f}~{fs.max():.6f}，"
                      f"不同取值 {nuniq} 个（事件数 {len(in_win_ev)}）")
                print(f"     ⇒ {'分段常数，像标准复权因子' if nuniq <= max(3, len(in_win_ev) + 2) else '取值过多，不是逐事件的复权因子'}")
    print("\n⚠️ 本地 vipdoc 只有约 1214 根（约 5 年，2021-06 起）"
          "⇒ 与 2006_2020 bundle **没有重叠期**，那个 bundle 只能这样自检。")
    return 0


def qlib_fields(code: str) -> int:
    """列出 qlib bundle 里该票**实有的 .bin 字段**。

    为什么要看：`s_data._FIELDS` 只读 `open/high/low/close/volume` 五个，
    **没读 `factor`**。而 2026-08-06 实测确认 qlib 的价格是「减去累计现金分红」的
    **加法调整**（见 `detect_convention`）—— 要还原真实价格就需要调整量，
    标准 qlib bundle 通常有 `factor` 字段。**有没有它决定 qlib 数据能否救回来。**
    """
    import s_data as Q
    bundles = Q.list_bundles()
    if not bundles:
        print("没有发现 bundle（检查 S_DATA_ROOT）")
        return 2
    for bd, inst in Q.code_to_qlib_dir(code, bundles):
        fdir = bd / "features" / inst
        print(f"\n── {bd.name} / {inst}   {fdir}")
        if not fdir.is_dir():
            print("   目录不存在")
            continue
        for p in sorted(fdir.glob("*.bin")):
            print(f"   {p.name:<28} {p.stat().st_size:>10,} B")
        extra = [p.name for p in fdir.glob("*.bin")
                 if p.stem.split(".")[0] not in
                 ("open", "high", "low", "close", "volume")]
        if extra:
            print(f"   ⇒ **s_data 未读的字段**: {extra}"
                  f"（若含 factor，可用来还原真实价格）")
        else:
            print("   ⇒ 只有 OHLCV，**没有 factor** ⇒ 无法从 bundle 内部还原真实价格")
    return 0


def detect_convention(code: str) -> int:
    """判定两边各自用的是**乘法前复权**还是**加法（减去累计分红）调整**。

    数学判据（在同一个"无事件段"内）：

        乘法前复权   adj_t = raw_t × f      ⇒ `adj/raw` 恒定，`raw−adj` 随价格变动
        加法调整     adj_t = raw_t − c      ⇒ `raw−adj` 恒定，`adj/raw` 随价格变动

    2026-08-06 实测结论（600519/600612/600622）：

        tdx  : adj/raw 恒定  ⇒ 乘法前复权 ✅（且与未复权收益 0 天偏离）
        qlib : **raw−qlib 恒定** ⇒ **加法调整**
               600519  194.99 → 173.31   差 21.68 元 = 2021 年报分红 21.675 元/股
               600612  6.91 → 5.46 → 4.00  逐段差 1.45 / 1.46
               600622  0.07 → 0（末段比值恰好 1.0）

    ⚠️ **加法调整会系统性放大百分比收益**（分母被减小）⇒ qlib 的涨跌幅能超过涨停限制
    （实测 600612 报出 +11.07%，而 600xxx 限 10%）。用它算百分比收益/止损一律偏大。
    """
    import pandas as pd
    t, tnote = _load_tdx(code)
    q, qnote = _load_qlib(code)
    if t is None or q is None or "raw_close" not in (t.columns if t is not None else []):
        print(f"{code}: 取数不全 tdx={tnote!r} qlib={qnote!r}（需要 raw_close）")
        return 2
    m = t.merge(q, on="date", how="inner", suffixes=("_tdx", "_qlib"))
    m = m[(m["date"] >= WIN_START) & (m["date"] <= WIN_END)]
    m = m[(m["close_tdx"] > 0) & (m["close_qlib"] > 0) & (m["raw_close"] > 0)]
    m = m.reset_index(drop=True)
    if len(m) < 60:
        # ⚠️ 只说「样本太少」看不出原因。实测踩到：本地 vipdoc 只有约 1214 根
        # （约 5 年，2021-06 起），与 2006_2020 bundle **没有重叠期** ⇒ 0 根。
        # 报出两侧真实区间，读的人立刻知道是窗口选错还是数据不够深。
        def _rng(d):
            return f"{d['date'].min()}~{d['date'].max()} ({len(d)} 根)" if len(d) else "空"
        print(f"{code}: 重叠仅 {len(m)} 根，样本太少（窗口 {WIN_START}~{WIN_END}）")
        print(f"   tdx : {_rng(t)}")
        print(f"   qlib: {_rng(q)}")
        if len(t) and len(q) and (t["date"].max() < q["date"].min()
                                 or q["date"].max() < t["date"].min()):
            print("   ⇒ 两侧**区间不相交**，不是窗口问题。本地 vipdoc 深度约 5 年，"
                  "老 bundle 请用 --qlib-selfcheck（用它自带的 factor/change 自洽检验）")
        return 2

    ev_dates: list[str] = []
    try:
        import adjust_factors as A
        ev_dates = sorted({str(e.get("date"))[:10] for e in A.get_xdxr(code)
                           if WIN_START <= str(e.get("date"))[:10] <= WIN_END})
    except Exception:                                          # noqa: BLE001
        pass

    # 切成"无事件段"，段内分别看两种不变量的离散度
    bounds = [0] + [i for i, d in enumerate(m["date"]) if d in ev_dates] + [len(m)]
    print(f"\n{'=' * 96}")
    print(f"{code} 复权约定探测   重叠 {len(m)} 根   窗口内事件 {len(ev_dates)} 个"
          f"   分 {len(bounds) - 1} 段")
    print("=" * 96)
    print(f"{'段':<4}{'起':<12}{'止':<12}{'根数':>6}"
          f"{'tdx: adj/raw 离散':>20}{'qlib: raw−adj 离散':>22}{'qlib: adj/raw 离散':>20}")
    print("-" * 96)

    def _spread(s) -> float:
        s = s.dropna()
        if len(s) < 2 or abs(s.median()) < 1e-12:
            return float("nan")
        return float(s.max() / s.min() - 1.0) if (s > 0).all() else float("nan")

    tdx_mult, qlib_add, qlib_mult = [], [], []
    for k in range(len(bounds) - 1):
        seg = m.iloc[bounds[k]:bounds[k + 1]]
        if len(seg) < 5:
            continue
        a = _spread(seg["close_tdx"] / seg["raw_close"])
        b = _spread(seg["raw_close"] - seg["close_qlib"])
        c = _spread(seg["close_qlib"] / seg["raw_close"])
        tdx_mult.append(a), qlib_add.append(b), qlib_mult.append(c)
        print(f"{k:<4}{seg['date'].iloc[0]:<12}{seg['date'].iloc[-1]:<12}{len(seg):>6}"
              f"{a:>19.5%}{b:>21.5%}{c:>19.5%}")

    def _med(v):
        vv = [x for x in v if x == x]
        return sum(vv) / len(vv) if vv else float("nan")

    print(f"\n段内离散度中位：tdx adj/raw {_med(tdx_mult):.5%}   "
          f"qlib raw−adj {_med(qlib_add):.5%}   qlib adj/raw {_med(qlib_mult):.5%}")
    print("\n判定：")
    print(f"  tdx  → {'**乘法前复权**（adj/raw 段内恒定）' if _med(tdx_mult) < 1e-4 else '不是干净的乘法复权'}")
    if _med(qlib_add) < 1e-4 and _med(qlib_mult) > 1e-3:
        print("  qlib → **加法调整（减去累计现金分红）**：raw−adj 段内恒定、adj/raw 不恒定")
        print("     ⚠️ 加法调整会**系统性放大百分比收益**（分母被减小）")
        print("     ⇒ 用 qlib 数据算百分比收益/止损一律偏大；涨跌幅可超过涨停限制")
        print("     ⇒ 要还原真实价格需要调整量 c：raw = qlib + c。仍在市的票可用 tdx 求 c，")
        print("        **退市股拿不到**（而「含退市股」正是用 qlib 的唯一理由）"
              "⇒ 见 --qlib-fields")
    elif _med(qlib_mult) < 1e-4:
        print("  qlib → **乘法前复权**（adj/raw 段内恒定）")
    else:
        print("  qlib → 两种都不恒定，需逐段看上表")
    # 每段的调整量绝对值，便于对照分红公告
    if _med(qlib_add) < 1e-4:
        print("\n每段的加法调整量 c = raw − qlib（相邻段之差应等于该次每股分红）：")
        prev = None
        for k in range(len(bounds) - 1):
            seg = m.iloc[bounds[k]:bounds[k + 1]]
            if len(seg) < 5:
                continue
            c = float((seg["raw_close"] - seg["close_qlib"]).median())
            delta = f"   Δ={prev - c:+.4f}" if prev is not None else ""
            print(f"  段{k} {seg['date'].iloc[0]}~{seg['date'].iloc[-1]}  c={c:.4f}{delta}")
            prev = c
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
    ap.add_argument("--convention", default="",
                    help="判定两边各用**乘法前复权**还是**加法（减分红）调整**")
    ap.add_argument("--qlib-fields", default="",
                    help="列出 qlib bundle 里该票实有的 .bin 字段（看有没有 factor）")
    ap.add_argument("--gap-report", action="store_true",
                    help="量化 bundle 缺口的代价：vipdoc 深度是否可扩、缺口期退市票多少、"
                         "现有窗口是否踩坑")
    ap.add_argument("--gap-sample", type=int, default=200,
                    help="--gap-report 抽样多少只票测 vipdoc 深度")
    ap.add_argument("--qlib-selfcheck", default="",
                    help="**不依赖 tdx** 判各 bundle 自己的口径：用它自带的 factor/change "
                         "+ 我们的 xdxr 事件日。老 bundle 与 vipdoc 无重叠期，只能这样查")
    ap.add_argument("--win", nargs=2, metavar=("START", "END"), default=None,
                    help="覆盖对账窗口。默认 2021-08-02~2026-01-31（落在 2021_2026 bundle "
                         "内部）。⚠️ 两个 bundle 字段集不同、可能是两种价格口径，"
                         "所以**必须分 bundle 分别对账**，不要跨缝比")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    if a.win:
        global WIN_START, WIN_END
        WIN_START, WIN_END = a.win[0], a.win[1]
        print(f"[INFO] 对账窗口覆盖为 {WIN_START} ~ {WIN_END}")

    if a.gap_report:
        return gap_report(a.gap_sample)
    if a.qlib_selfcheck:
        return qlib_selfcheck(a.qlib_selfcheck.strip()[:6])
    if a.qlib_fields:
        return qlib_fields(a.qlib_fields.strip()[:6])
    if a.convention:
        return detect_convention(a.convention.strip()[:6])
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
