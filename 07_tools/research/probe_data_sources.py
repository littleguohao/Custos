#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据源探针：实测各接口的**可用性、耗时、返回形状**，产出报告供治理文档填数。

## 为什么需要它

`00_governance/data/` 下的文档要写「现状」，但现状里的性能与稳定性**没有任何实测数据**：

- `tests/test_tq_http.py` 150 行、**0 处 monkeypatch、0 处网络** —— 只测了纯函数，
  而 `tq_http` 定义的三种错误码（`tdxw_not_running` / `connection_failed` / `timeout`）
  产生路径一条都没测。
- `tests/test_s_data.py` 107 行，同样 0/0。
- 全仓没有任何测试断言过接口耗时。

⇒ 「哪个接口慢、哪个不稳」全靠印象。而这三周的判断已经被印象坑过一次
（我断定「加载是回测瓶颈」，实测 `加载占 1%`）。

## 与单元测试的分工

    tests/                 离线契约测试。跑在 CI/Linux，不碰真实接口
    probe_data_sources.py  真实环境探针。**必须在装了通达信的机器上手动跑**，
                           产出报告落盘，由人回填治理文档

所以本脚本不进 pytest —— 它的结果依赖宿主环境，不能作为断言。

## 安全约束（硬编码，不是约定）

- **只读**：不写任何业务数据，只写 `06_logs/data_probe/`。
- **绝不 raise**：逐项独立，一个接口挂掉不影响其余（否则探针本身成了单点）。
- **禁止危险接口**：`download_file` 的 `down_type` 1/5/6 实测可打挂 TdxW 服务，
  本脚本只探 4；`tq_http.call` 侧也已加代码级拦截。
- **短超时**：默认 15s，避免探针自己卡死在一个挂掉的服务上。

用法：
    uv run python 07_tools/research/probe_data_sources.py                # 全部
    uv run python 07_tools/research/probe_data_sources.py --only tq      # 只探 TQ
    uv run python 07_tools/research/probe_data_sources.py --repeat 5     # 每项跑 5 次
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time
import traceback
from typing import Any, Callable, Optional

BASE = pathlib.Path(__file__).resolve().parents[2]
for _p in ("07_tools", "07_tools/local_tdx", "07_tools/screening",
           "07_tools/market_timing"):
    sys.path.insert(0, str(BASE / _p))

from paths import LOGS  # noqa: E402

OUTDIR = LOGS / "data_probe"

# 探测用样本：覆盖三个交易所 + 一个指数。故意包含 BJ —— 它**曾是** xdxr 唯一
# 不覆盖的市场（mootdx 把 920 段判错 market）；84ee19f 修好 market=2 后，
# 920808 实测有 24 条权息（8 条影响价格 + 16 条股本变化），BJ 前复权已可用。
SAMPLE_SH = "600000"        # 浦发银行
# TQ 要求带市场后缀（裸 6 位 → ErrorId=2 stock_code error，实测踩到）
TQ_SH = "600000.SH"
SAMPLE_SZ = "000001"        # 平安银行
SAMPLE_CY = "300750"        # 宁德时代（创业板，20% 涨跌幅）
SAMPLE_BJ = "920808"        # 北交所（84ee19f 修好 market=2 后 xdxr 已覆盖，实测 24 条权息）
SAMPLE_INDEX = "999999"     # 上证指数（注意：vipdoc 里 sh000001 不是上证指数）


class Probe:
    """一次探测的结果容器。**任何异常都收进 error 字段，不向上抛。**"""

    def __init__(self, group: str, name: str, note: str = "", wired: bool = True):
        self.group = group
        self.name = name
        self.note = note
        self.wired = wired          # 是否已接入生产链（区别于「探过但没接」）
        self.samples: list[float] = []
        self.shape: Any = None
        self.error: Optional[str] = None
        self.ok_count = 0
        self.empty_count = 0          # 没抛异常但返回空 —— 单独计，不算成功

    def run(self, fn: Callable[[], Any], repeat: int) -> "Probe":
        for _ in range(repeat):
            t0 = time.perf_counter()
            try:
                out = fn()
            except Exception as exc:                          # noqa: BLE001
                self.error = f"{type(exc).__name__}: {exc}"
                self.samples.append(time.perf_counter() - t0)
                continue
            self.samples.append(time.perf_counter() - t0)
            if _is_empty(out):
                # ⚠️ 探针自己也差点犯「静默降级」：`get_online_bars` 返回 0行×0列
                # 却被记成 3/3 成功。**「没抛异常」不等于「拿到数据」** ——
                # 而这正是本脚本要暴露的那类问题，不能自己也犯。
                self.empty_count += 1
            else:
                self.ok_count += 1
            if self.shape is None:
                self.shape = _describe(out)
        return self

    def as_dict(self) -> dict:
        n = len(self.samples) or 1
        return {
            "group": self.group, "name": self.name, "note": self.note,
            "wired": self.wired,
            "attempts": len(self.samples), "ok": self.ok_count,
            "empty": self.empty_count,
            "success_rate": round(self.ok_count / n, 3),
            "ms_p50": round(statistics.median(self.samples) * 1000, 1) if self.samples else None,
            "ms_max": round(max(self.samples) * 1000, 1) if self.samples else None,
            "shape": self.shape, "error": self.error,
        }


def _is_empty(out: Any) -> bool:
    """返回值是否「空」——空 DataFrame / 空容器 / tq_http 的 ok=False。"""
    try:
        import pandas as pd
        if isinstance(out, pd.DataFrame):
            return out.empty
    except Exception:                                          # noqa: BLE001
        pass
    if isinstance(out, dict):
        if "ok" in out and "error" in out:
            return not out.get("ok")
        return not out
    if isinstance(out, (list, tuple, set, str)):
        return len(out) == 0
    return out is None


def _describe(out: Any) -> Any:
    """把返回值压成一行可读形状——报告要能看出「有没有真数据」。"""
    try:
        import pandas as pd
        if isinstance(out, pd.DataFrame):
            cols = list(out.columns)[:8]
            last = ""
            for c in ("date", "datetime"):
                if c in out.columns and len(out):
                    last = f" 末日={str(out[c].iloc[-1])[:10]}"
                    break
            return f"DataFrame {len(out)}行 × {len(out.columns)}列 {cols}{last}"
    except Exception:                                          # noqa: BLE001
        pass
    if isinstance(out, dict):
        if "ok" in out and "error" in out:                     # tq_http 的统一返回
            if not out.get("ok"):
                return f"失败 error={out.get('error')}"
            v = out.get("value")
            if isinstance(v, dict):
                return f"ok, value {len(v)} 字段: {list(v)[:6]}"
            if isinstance(v, list):
                return f"ok, value {len(v)} 条"
            return f"ok, value={type(v).__name__}"
        return f"dict {len(out)} 键: {list(out)[:6]}"
    if isinstance(out, (list, tuple, set)):
        return f"{type(out).__name__} {len(out)} 项: {list(out)[:3]}"
    # ⚠️ 兜底分支要包 try：`str(out)` 会执行对象的 `__str__`/`__repr__`，那可能抛。
    # 而 `_describe` 在 `Probe.run` 里是**在 try 之外**调用的 ⇒ 抛上去就打破
    # 本模块最明确的那条契约「任何异常都收进 error 字段，不向上抛」，
    # 并中断整轮探测（拿不到其余数据源的现状）。
    try:
        return f"{type(out).__name__}: {str(out)[:60]}"
    except Exception as exc:                                       # noqa: BLE001
        return f"{type(out).__name__}: <无法描述: {type(exc).__name__}>"


# ---------------------------------------------------------------------------
# 各数据源
# ---------------------------------------------------------------------------
def probe_mootdx(repeat: int) -> list[Probe]:
    """mootdx 三个入口：Reader(本地 vipdoc) / Quotes(在线协议) / Affair(财务)。

    ⚠️ `Quotes.factory()` 在全仓有 11 处调用，正是「连接永不重连」反模式的高发区
    （已修三处，见 tests/test_tdx_connection_hygiene.py）。这里顺便量它的建连耗时——
    如果建连很贵，那「每次调用都新建」和「缓存但不重连」就都不可取，
    唯一正确解是「缓存 + 可重建」。
    """
    out: list[Probe] = []
    try:
        import local_tdx_data as L
    except Exception as exc:                                   # noqa: BLE001
        p = Probe("mootdx", "import local_tdx_data")
        p.error = f"{type(exc).__name__}: {exc}"
        return [p]

    out.append(Probe("mootdx", "Reader.read_vipdoc_daily(SH)",
                     "本地 .day 二进制，回测/live 的主数据源").run(
        lambda: L.read_vipdoc_daily(SAMPLE_SH), repeat))
    out.append(Probe("mootdx", "Reader.read_vipdoc_daily(BJ)",
                     "BJ 是 xdxr 不覆盖的市场，用来暴露前复权降级").run(
        lambda: L.read_vipdoc_daily(SAMPLE_BJ), repeat))
    out.append(Probe("mootdx", "list_local_vipdoc_codes()",
                     "回测 universe 来源。⚠️ 会随通达信下载变动（实测 5535→5536）").run(
        lambda: L.list_local_vipdoc_codes(), repeat))
    out.append(Probe("mootdx", "get_ohlcv_table(qfq)",
                     "全链默认口径：vipdoc + 自算前复权").run(
        lambda: L.get_ohlcv_table(SAMPLE_SH, count=260), repeat))
    out.append(Probe("mootdx", "get_ohlcv_table(BJ, qfq)",
                     "预期：qfq 失败 → 静默降级为未复权（attrs['adjust']=='none'）").run(
        lambda: L.get_ohlcv_table(SAMPLE_BJ, count=260), repeat))
    out.append(Probe("mootdx", "Quotes.get_online_bars()",
                     "在线 TDX 协议；量建连成本").run(
        lambda: L.get_online_bars(SAMPLE_SZ, offset=120), repeat))
    out.append(Probe("mootdx", "Quotes.get_snapshot()",
                     "实时快照").run(
        lambda: L.get_snapshot(SAMPLE_SH), repeat))
    out.append(Probe("mootdx", "get_online_index()",
                     "指数在线行情").run(
        lambda: L.get_online_index(SAMPLE_INDEX), repeat))
    out.append(Probe("mootdx", "get_stock_list()",
                     "在线全代码表（与本地 vipdoc 枚举是两个口径）").run(
        lambda: L.get_stock_list(), repeat))
    out.append(Probe("mootdx", "Affair.get_financial_data()",
                     "财务专项文件；只在研究链用").run(
        lambda: L.get_financial_data(), 1))          # 下载量大，只跑一次
    return out


def probe_qfq(repeat: int) -> list[Probe]:
    """前复权链路：xdxr 取数 → 因子计算。

    ⚠️ **自算前复权从未与任何独立序列对账过**（grep 整个 adjust_factors.py 与其测试，
    无对账代码），而它 08-04 起成了全链默认口径、决定所有价格，且出过两个 bug
    （BJ 分支写反、`out[:500]` 截断保留最旧的权息事件）。
    这里只量可用性与耗时；**正确性对账是另一件事**（可拿 qlib 的前复权序列作独立参照）。
    """
    out: list[Probe] = []
    try:
        import adjust_factors as A
    except Exception as exc:                                   # noqa: BLE001
        p = Probe("qfq", "import adjust_factors")
        p.error = f"{type(exc).__name__}: {exc}"
        return [p]
    for code, tag in ((SAMPLE_SH, "SH"), (SAMPLE_CY, "创业板"), (SAMPLE_BJ, "BJ")):
        out.append(Probe("qfq", f"get_xdxr({tag})",
                         "权息事件；缓存冷时要经 TDX 协议取").run(
            lambda c=code: A.get_xdxr(c), repeat))
    out.append(Probe("qfq", "cache_age_days(SH)", "缓存新鲜度").run(
        lambda: A.cache_age_days(SAMPLE_SH), repeat))

    # ⚠️ 空权息事件会被盖章成 adjust="qfq"（实测）——`get_xdxr` 对取不到权息的票
    # 返回 [] 而非抛错，于是 qfq_table 走成功路径，把**未复权**数据标成「已前复权」。
    # 唯一线索是 attrs["adjust_events"]==0，而目前没有调用方检查它。
    # 注：BJ 曾是这样的票（mootdx 把 920 段判错 market ⇒ get_xdxr(BJ) 恒空）；
    # 84ee19f 修好 market=2 后 920808 实测有 24 条权息，本探针在 BJ 样本上可能
    # 不再触发暴露路径，但「空事件盖章 qfq」对任何无权息记录的票仍然存在。
    def _bj_adjust_stamp() -> Any:
        import pandas as pd  # noqa: PLC0415
        df = pd.DataFrame({"date": ["2026-08-04", "2026-08-05"],
                           "open": [10.0, 10.1], "high": [10.3, 10.4],
                           "low": [9.9, 10.0], "close": [10.2, 10.3],
                           "volume": [1e6, 1.1e6]})
        ev = A.get_xdxr(SAMPLE_BJ)
        out = A.apply_qfq(df, ev)
        return {"xdxr_events": len(ev), "adjust": out.attrs.get("adjust"),
                "adjust_events": out.attrs.get("adjust_events"),
                "价格未变": bool(out["close"].equals(df["close"]))}

    out.append(Probe("qfq", "BJ 复权标记是否可信",
                     "预期暴露：0 事件仍盖章 qfq ⇒ 未复权被当成已复权").run(
        _bj_adjust_stamp, 1))
    return out


def probe_tq(repeat: int) -> list[Probe]:
    """TQ-Local：TdxW.exe 的本地 JSON-RPC 服务。

    实际接入的只有 6 个方法，分散在**四处**独立访问路径：
      tq_http.py(4 个薄封装) / concept_tags(download_file) /
      formula_screen(公式) / trading_calendar(自己拼请求，没走 tq_http)
    """
    out: list[Probe] = []
    try:
        import tq_http as T
        import tq_sector as S
    except Exception as exc:                                   # noqa: BLE001
        p = Probe("tq", "import tq_http")
        p.error = f"{type(exc).__name__}: {exc}"
        return [p]

    out.append(Probe("tq", "is_tdxw_running()", "进程级预检").run(
        lambda: S.is_tdxw_running(), repeat))
    out.append(Probe("tq", "ping/get_match_stkinfo", "连通性探测").run(
        lambda: T.ping(), repeat))
    out.append(Probe("tq", "get_market_snapshot", "持仓/盘中快照").run(
        lambda: T.snapshot(TQ_SH), repeat))
    out.append(Probe("tq", "get_stock_info", "股票名称（ST 判定唯一依据）").run(
        lambda: T.stock_info(TQ_SH), repeat))
    out.append(Probe("tq", "get_more_info", "扩展字段").run(
        lambda: T.more_info(TQ_SH), repeat))
    out.append(Probe("tq", "download_file(down_type=4)",
                     "概念标签 miscinfo。**只探 4**；1/5/6 实测可打挂服务").run(
        lambda: T.call("download_file", {"down_type": 4}, timeout=30), 1))
    # 危险 down_type 的**拦截**是否生效（不真的发请求）
    out.append(Probe("tq", "拦截 down_type=1",
                     "验证代码级防护生效：应返回 unsafe_down_type 且不发请求").run(
        lambda: T.call("download_file", {"down_type": 1}), 1))
    return out


def probe_qlib(repeat: int) -> list[Probe]:
    """S_DATA 的 qlib bundle —— 只服务研究链，live 不用。

    两个已知坑目前只写在 `s_data.py` 的 docstring 里、治理文档一字未提，
    而这三周所有「跨年 walk-forward」结论都依赖它：
      · 2020-09-28 → 2021-07-30 约 10 个月**缺口**
      · 数据到 **2026-02** 截止
    它同时是**含退市股**（point-in-time）+ **已前复权** ⇒ 可作 tdx 自算前复权的独立参照。
    """
    out: list[Probe] = []
    try:
        import s_data as Q
    except Exception as exc:                                   # noqa: BLE001
        p = Probe("qlib", "import s_data")
        p.error = f"{type(exc).__name__}: {exc}"
        return [p]
    out.append(Probe("qlib", "list_bundles()", "bundle 发现（含区间）").run(
        lambda: Q.list_bundles(), repeat))
    out.append(Probe("qlib", "list_universe(qlib)",
                     "point-in-time 宇宙，**含退市股** ⇒ 可去幸存者偏差").run(
        lambda: Q.list_universe(), repeat))
    out.append(Probe("qlib", "load_bars_qlib(SH)",
                     "已前复权 ⇒ 可作 tdx 自算 qfq 的独立参照").run(
        lambda: Q.load_bars_qlib([SAMPLE_SH], 500), repeat))
    out.append(Probe("qlib", "load_bars_csv(SH)", "CSV 冗余副本").run(
        lambda: Q.load_bars_csv([SAMPLE_SH], 500), repeat))
    return out


def probe_eastmoney(repeat: int) -> list[Probe]:
    """东财：三项无法本地化的依赖（PIT 财务 / 真市值 / BJ 行情与资金流）。"""
    out: list[Probe] = []
    try:
        import fetch_pit_financials as P
        # ⚠️ 必须用**生产默认** page_size：探针第一版传 page_size=5，于是接口自报
        # pages=11520/5=2304、max_pages=40 翻不完 ⇒ 报成「接口失败」，实际是我传错参数。
        # 探针的参数偏离生产就会量到假故障。
        out.append(Probe("eastmoney", "PIT 财务(RPT_LICO_FN_CPD)",
                         "以**公告日**为可见日；本地 TDX 无此能力。单期约 1.15 万行"
                         "（≈5400 只 × 多种报表类型），page_size=500 ⇒ 约 24 页").run(
            lambda: P.fetch_period("2025-12-31"), 1))
    except Exception as exc:                                   # noqa: BLE001
        p = Probe("eastmoney", "PIT 财务")
        p.error = f"{type(exc).__name__}: {exc}"
        out.append(p)
    try:
        import fetch_market_cap as M
        out.append(Probe("eastmoney", "真市值(RPT_VALUEANALYSIS_DET)",
                         "2018-01-02 起；替掉成交额代理").run(
            lambda: M.fetch_trade_date("2025-12-31"), 1))
    except Exception as exc:                                   # noqa: BLE001
        p = Probe("eastmoney", "真市值")
        p.error = f"{type(exc).__name__}: {exc}"
        out.append(p)
    return out


GROUPS: dict[str, Callable[[int], list[Probe]]] = {
    "mootdx": probe_mootdx,
    "qfq": probe_qfq,
    "tq": probe_tq,
    "qlib": probe_qlib,
    "eastmoney": probe_eastmoney,
}


def report(probes: list[Probe]) -> None:
    hdr = (f"{'组':<10}{'接口':<32}{'接入':>5}{'成功':>7}{'空返回':>7}"
           f"{'p50ms':>8}{'maxms':>8}  返回形状 / 错误")
    print("\n" + "=" * 118)
    print("数据源探针报告")
    print("=" * 118)
    print(hdr)
    print("-" * 118)
    for p in probes:
        d = p.as_dict()
        wired = "✓" if d["wired"] else "—"
        rate = f"{d['ok']}/{d['attempts']}"
        shape = d["shape"] if d["shape"] is not None else (d["error"] or "")
        print(f"{d['group']:<10}{d['name']:<32}{wired:>5}{rate:>7}"
              f"{d['empty'] or '':>7}"
              f"{d['ms_p50'] if d['ms_p50'] is not None else '—':>8}"
              f"{d['ms_max'] if d['ms_max'] is not None else '—':>8}  {str(shape)[:56]}")
        if d["error"] and d["ok"]:
            print(f"{'':>70}  ⚠️ 部分失败: {d['error'][:70]}")
    empties = [p for p in probes if p.empty_count and not p.error]
    if empties:
        print(f"\n⚠️ **{len(empties)} 项没抛异常但返回空**（比报错更危险：调用方看不出）：")
        for p in empties:
            print(f"   {p.group}/{p.name}: {p.shape}")
    fails = [p for p in probes if p.ok_count == 0]
    if fails:
        print(f"\n⚠️ **{len(fails)} 项完全失败**：")
        for p in fails:
            print(f"   {p.group}/{p.name}: {p.error}")
        print("   注意区分「环境没装」与「接口坏了」——前者在 Linux/CI 上是预期的。")
    print("\n⇒ 这份报告用于回填 00_governance/data/ 的性能与稳定性栏。")
    print("   它**不是**单元测试：结果依赖宿主环境，不能作为断言。")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="数据源探针（实测可用性/耗时/返回形状）")
    ap.add_argument("--only", default="", help=f"只探某组：{'/'.join(GROUPS)}")
    ap.add_argument("--repeat", type=int, default=3, help="每项重复次数（默认 3，取中位）")
    ap.add_argument("--out", default="", help="报告 JSON 路径（默认 06_logs/data_probe/）")
    a = ap.parse_args()

    todo = {k: v for k, v in GROUPS.items() if not a.only or a.only == k}
    if not todo:
        print(f"--only {a.only} 不认识；可选：{'/'.join(GROUPS)}")
        return 2

    probes: list[Probe] = []
    for name, fn in todo.items():
        print(f"[PROBE] {name} …", flush=True)
        try:
            probes += fn(a.repeat)
        except Exception:                                      # noqa: BLE001
            p = Probe(name, "(探测器自身异常)")
            p.error = traceback.format_exc(limit=3)
            probes.append(p)
    report(probes)

    from paths import cn_now  # noqa: PLC0415
    ts = cn_now().strftime("%Y%m%d_%H%M")
    out = pathlib.Path(a.out) if a.out else OUTDIR / f"{ts}_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "probed_at": cn_now().isoformat(),
        "platform": sys.platform,
        "repeat": a.repeat,
        "results": [p.as_dict() for p in probes],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] 报告已落盘 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
