# -*- coding: utf-8 -*-
"""前复权因子：基于通达信权息数据（xdxr）本地计算，不依赖 HTTP 爬取。

owner 2026-08-04 拍板：**全链统一用前复权**。

为什么用 xdxr 而不是爬前复权 K 线：
  · `Quotes.xdxr()` 走通达信协议，返回标准 gbbq 权息数据（分红/送转/配股/缩股），
    每只票只有几十条记录、且**是历史事实不会变**，可长期缓存，只需增量补新事件。
  · 爬东财/腾讯的前复权 K 线要逐股全量拉、还得应付反爬与域名不稳
    （本机实测 push2his/push2 全部 RemoteDisconnected，push2delay 返回空 klines）。
  · 更关键：xdxr 让复权**可解释可复核**——每个因子都能追到具体的分红送转事件，
    而爬来的前复权价格是黑箱，对不上时无法定位原因。

复权口径（通达信/同花顺标准的「除权除息」前复权）：

    除权参考价 = (前收盘 − 每股现金红利 + 配股价 × 配股比例) / (1 + 送股比例 + 配股比例)
    ratio_d    = 除权参考价 / 前收盘                      # 该除权日的价格缩放比
    前复权因子(t) = Π ratio_d   for all 除权日 d > t        # 只累乘 t 之后的除权

于是**最新一天因子恒为 1**（它后面没有除权日）⇒ 前复权价 = 实际盘面价。
这正是「统一用前复权」可行的原因：当日买入价/止损价与盘面天然一致，
不需要把展示价与指标价分开维护。

xdxr 字段口径（每 **10 股**）：
    fenhong=现金分红(元)  songzhuangu=送股+转股  peigu=配股数  peigujia=配股价(元/股)
    suogu=缩股比例        category: 1=除权除息  5=股本变化(不影响价格,忽略)
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Optional

import numpy as np
import pandas as pd

# 与 local_tdx_data.py 同一套路径处理：本模块既被当包内模块 import，也被直接当脚本跑

from custos.core.code_utils import market_of  # noqa: E402
from custos.core.paths import cn_now, MARKET_DIR  # noqa: E402

CACHE_DIR = MARKET_DIR / "xdxr"

# 只有这些 category 影响价格；5=股本变化(增发/回购)不改变单股权益，不复权
PRICE_AFFECTING_CATEGORY = {1}  # 1 = 除权除息（分红/送转/配股）
MIN_RATIO = 0.01  # 因子下限：ratio<1% 视为数据异常，跳过该事件
MAX_EVENTS_SANE = 500  # 单票权息事件数上限（超出视为数据异常）


class AdjustError(Exception):
    """复权数据不可用（调用方决定是降级还是中断）。"""


# ---------------------------------------------------------------- 权息数据


def _cache_path(code: str) -> pathlib.Path:
    return CACHE_DIR / f"{str(code)[:6]}.json"


def load_xdxr_cache(code: str) -> Optional[list[dict[str, Any]]]:
    """读权息缓存。返回 None 表示「没有可信缓存，请去取」。

    ⚠️ **缓存里必须记 `market`，缺 `market` 的空事件缓存一律作废。**
    2026-08-06 之前的实现用 `q.xdxr()` 的内部推断，把 `920xxx` 判成沪市 ⇒
    查到 0 条并**把这个空结果缓存了下来**。而缓存策略是「除权是历史事实，不会变」
    ⇒ 永不过期 ⇒ 那些票会**永远**按未复权处理，且被标成已前复权。

    只作废「缺 market **且** 事件为空」的条目：非空的老缓存是真查到的事件，
    没必要丢；真的从未除权的票会在下次取数后补上 market 字段。
    """
    p = _cache_path(code)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] xdxr 缓存损坏 {p.name}: {e}", file=sys.stderr)
        return None
    ev = d.get("events")
    if not isinstance(ev, list):
        return None
    if not ev and d.get("market") is None:
        print(
            f"[WARN] {code} 的空权息缓存缺 market 标记（可能是 920xxx 判错市场时写下的），"
            f"作废并重取",
            file=sys.stderr,
        )
        return None
    return ev


def save_xdxr_cache(
    code: str,
    events: list[dict[str, Any]],
    fetched_at: str = "",
    shares: Optional[list[dict[str, Any]]] = None,
) -> None:
    """一份缓存同时装权息事件与股本事件——两者来自同一次 xdxr 调用，分开存会多跑一次网络。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _cache_path(code)
    c6 = str(code)[:6]
    try:
        mkt = _tdx_market(c6)
    except AdjustError:
        mkt = None
    payload = {
        "code": c6,
        "events": events,
        "market": mkt,
        "fetched_at": fetched_at,
        "n": len(events),
    }
    if shares is not None:
        payload["shares"] = shares
    elif p.exists():  # 别把已有的股本数据覆盖没了
        try:
            old = json.loads(p.read_text(encoding="utf-8")).get("shares")
            if isinstance(old, list):
                payload["shares"] = old
        except Exception:  # noqa: BLE001, S110
            pass
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # 原子替换


def _tdx_market(code: str) -> int:
    """6 位代码 → 通达信协议的 market 整数：0=深、1=沪、**2=北**。

    ⚠️ **必须自己判，不能用 mootdx 的推断。** `mootdx.utils.get_stock_market` 的规则是
    「'5'/'6'/'9' 开头为 sh，其余为 sz」，于是北交所的新代码段 `920xxx` 被判成**沪市**：

        920808 → market=1 (sh)   ← 错
        830799 → market=2 (bj)   ← 老 BJ 段判对了

    而 `q.xdxr(symbol=...)` 内部就是用它推断 market 的 ⇒ 查的是 `SH:920808` 的权息，
    服务器返回空。实测对照（2026-08-06）：

        get_xdxr_info(1, "920808") → 0 条
        get_xdxr_info(2, "920808") → **24 条** ✅

    后果曾经是：`get_xdxr(BJ)` 返回 `[]` 而不报错 ⇒ `qfq_table` 走成功路径 ⇒
    `apply_qfq` 盖章 `adjust="qfq"` 而价格一字未改 ⇒ **未复权数据被标成已前复权**，
    BJ 约占 universe 4.8%，每轮全市场回测约 5% 样本带着除权假跳空在跑。

    `code_utils.market_of` 是本仓库的市场判定唯一来源（含 `880` → SH 这类例外），
    这里只做一次映射。
    """
    m = market_of(code)
    if m == "BJ":
        return 2
    if m == "SH":
        return 1
    if m == "SZ":
        return 0
    raise AdjustError(f"无法判定 {code} 的交易所，拒绝用错市场查权息")


def _to_records(df: Any) -> list[dict[str, Any]]:
    """把 mootdx 的返回规整成 list[dict]，兼容 DataFrame 与原始 list。

    ⚠️ 需要兼容两种形态：`q.xdxr()` 返回 DataFrame，而**直接调
    `q.client.get_xdxr_info(market, code)` 返回 list[OrderedDict]**。
    我们改走后者是为了自己指定 market——mootdx 的 `get_stock_market` 把 `920xxx`
    判成沪市（见 `_tdx_market`）。原实现只做 `df.to_dict("records")`，
    喂 list 会异常并 `return []` ⇒ 又是一次静默降级。
    """
    if df is None:
        return []
    if isinstance(df, list):
        return [dict(r) for r in df if hasattr(r, "keys")]
    try:
        if len(df) == 0:
            return []
    except TypeError:
        return []
    try:
        return df.to_dict("records")
    except Exception:  # noqa: BLE001
        return []


def normalize_xdxr(df: Any) -> list[dict[str, Any]]:
    """把 mootdx xdxr 返回规整成 [{date, fenhong, songzhuangu, peigu, peigujia, suogu}]。

    只保留影响价格的 category，并丢弃字段全空的行——通达信数据里
    「股本变化」行的价格字段都是 NaN，混进来会把 ratio 算成 1 或 NaN。
    """
    rows = _to_records(df)
    if not rows:
        return []
    out = []
    for r in rows:
        try:
            cat = int(r.get("category") or 0)
        except Exception:  # noqa: BLE001
            continue
        if cat not in PRICE_AFFECTING_CATEGORY:
            continue
        try:
            y, m, d = int(r.get("year")), int(r.get("month")), int(r.get("day"))
            date = f"{y:04d}-{m:02d}-{d:02d}"
        except Exception:  # noqa: BLE001
            continue

        def _f(k: str) -> float:
            v = r.get(k)
            try:
                x = float(v)
            except (TypeError, ValueError):
                return 0.0
            return 0.0 if x != x else x  # NaN → 0

        ev = {
            "date": date,
            "fenhong": _f("fenhong"),
            "songzhuangu": _f("songzhuangu"),
            "peigu": _f("peigu"),
            "peigujia": _f("peigujia"),
            "suogu": _f("suogu"),
        }
        if any(abs(ev[k]) > 0 for k in ("fenhong", "songzhuangu", "peigu", "suogu")):
            out.append(ev)
    out.sort(key=lambda e: e["date"])
    # 超限截断保留**最新**的：新事件才影响近期复权（此前保留最旧 500 条,方向反了）
    return out[-MAX_EVENTS_SANE:]


def normalize_shares(df: Any) -> list[dict[str, Any]]:
    """从 xdxr 提取**股本变化事件** → [{date, total_shares, float_shares}]（单位：股）。

    通达信 `category=5`「股本变化」行带 `houzongguben`（后总股本）与
    `panhouliutong`（后流通股本），单位是**万股**，这里换算成股。

    实测 2026-08-04 与真实值对照：万科 A 总股本 119.31 亿（分毫不差）、
    流通 97.17 亿（正确扣掉了不流通的 B 股）；茅台 12.50 亿；浦发 333.06 亿
    （转债转股后）。

    为什么这条路比东财 `RPT_VALUEANALYSIS_DET` 更合适：`fetch_market_cap.py` 自己的
    设计就是「总股本只在增发/回购/送转/解禁时变，极稀疏 ⇒ 只在观测到变化时写一行」，
    而 xdxr **天生是事件驱动**的；东财那边要逐交易日拉全市场再压成事件，
    既慢又要处理采样频率与限流。

    PIT 性质：股本是**当日事实**（某天就是那么多股），不存在财务数据那种
    「次年重算」的重述问题，所以按日期取「不晚于该日的最后一条」即可。
    """
    rows = _to_records(df)
    if not rows:
        return []
    out = []
    for r in rows:
        try:
            y, m, d = int(r.get("year")), int(r.get("month")), int(r.get("day"))
        except Exception:  # noqa: BLE001
            continue

        def _f(k: str) -> float:
            v = r.get(k)
            try:
                x = float(v)
            except (TypeError, ValueError):
                return 0.0
            return 0.0 if x != x else x  # NaN → 0

        total = _f("houzongguben") * 10000.0  # 万股 → 股
        flt = _f("panhouliutong") * 10000.0
        if total <= 0 and flt <= 0:
            continue
        out.append(
            {
                "date": f"{y:04d}-{m:02d}-{d:02d}",
                "total_shares": total or None,
                "float_shares": flt or None,
            }
        )
    out.sort(key=lambda e: e["date"])
    return out


def get_shares_events(
    code: str, *, refresh: bool = False, timeout: int = 10
) -> list[dict[str, Any]]:
    """股本变化事件（优先缓存）。缓存与权息同一份文件，避免两次取数。"""
    if not refresh:
        p = _cache_path(code)
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                sh = d.get("shares")
                if isinstance(sh, list):
                    return sh
            except Exception:  # noqa: BLE001
                pass
    try:
        from mootdx.quotes import Quotes

        c6 = str(code)[:6]
        q = Quotes.factory(market="std", timeout=timeout)
        # 显式传 market —— 见 _tdx_market（mootdx 把 920xxx 判成沪市 ⇒ 取到空）
        raw = q.client.get_xdxr_info(_tdx_market(c6), c6)
    except Exception as e:  # noqa: BLE001
        raise AdjustError(f"xdxr({code}) 取数失败: {e}") from e
    ev, sh = normalize_xdxr(raw), normalize_shares(raw)
    save_xdxr_cache(
        code, ev, fetched_at=cn_now().isoformat(timespec="seconds"), shares=sh
    )
    return sh


def total_shares_at(
    code: str, date: str, *, field: str = "total_shares"
) -> Optional[float]:
    """给定日期的总股本/流通股本（取**不晚于该日**的最后一条事件）。

    ``field``: "total_shares"（总股本）| "float_shares"（流通股本）。
    没有该日之前的事件时返回 None —— 这种情况必须让调用方知道，
    不能悄悄拿一个更晚的股本去算历史市值（那是未来函数）。
    """
    try:
        evs = get_shares_events(code)
    except AdjustError:
        return None
    d = str(date)[:10]
    val = None
    for e in evs:
        if e["date"] <= d:
            v = e.get(field)
            if v:
                val = float(v)
        else:
            break
    return val


def fetch_xdxr(code: str, timeout: int = 10) -> list[dict[str, Any]]:
    """从通达信协议取权息数据（走 mootdx bestip）。失败 raise AdjustError。"""
    try:
        from mootdx.quotes import Quotes

        c6 = str(code)[:6]
        q = Quotes.factory(market="std", timeout=timeout)
        # 显式传 market —— 不走 q.xdxr() 的内部推断（它把 920xxx 判成沪市）
        df = q.client.get_xdxr_info(_tdx_market(c6), c6)
    except Exception as e:  # noqa: BLE001
        raise AdjustError(f"xdxr({code}) 取数失败: {e}") from e
    return normalize_xdxr(df)


def fetch_xdxr_batch(
    codes: list[str],
    *,
    timeout: int = 10,
    progress_every: int = 200,
    on_error: str = "skip",
) -> dict[str, list[dict[str, Any]]]:
    """批量取权息并写缓存，**复用同一个 Quotes 连接**。

    为什么必须批量：`Quotes.factory()` 每次都要选 bestip + 建 TCP 连接。18:00 选股链
    有几百只候选，逐只新建连接会把一个 <1s 的操作拖成几分钟。这里建一次连接跑完全部。

    ``on_error="skip"`` 单只失败跳过并计数；``"raise"`` 立即中断。
    """
    out: dict[str, list[dict[str, Any]]] = {}
    if not codes:
        return out
    try:
        from mootdx.quotes import Quotes

        q = Quotes.factory(market="std", timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise AdjustError(f"通达信连接建立失败: {e}") from e

    now = cn_now().isoformat(timespec="seconds")
    failed = 0
    for i, code in enumerate(codes, 1):
        c6 = str(code)[:6]
        try:
            raw = q.client.get_xdxr_info(_tdx_market(c6), c6)
            ev = normalize_xdxr(raw)
            out[c6] = ev
            # 同一次调用顺手把股本事件也存下(替代东财市值接口,见 normalize_shares)
            save_xdxr_cache(c6, ev, fetched_at=now, shares=normalize_shares(raw))
        except Exception as e:  # noqa: BLE001
            failed += 1
            if on_error == "raise":
                raise AdjustError(f"xdxr({c6}) 失败: {e}") from e
        if progress_every and i % progress_every == 0:
            print(f"[INFO] 权息 {i}/{len(codes)}（失败 {failed}）", file=sys.stderr)
    try:
        q.close()
    except Exception:  # noqa: BLE001, S110
        pass
    if failed:
        print(
            f"[WARN] 权息取数 {failed}/{len(codes)} 只失败（这些票将按未复权处理）",
            file=sys.stderr,
        )
    return out


def cache_age_days(code: str) -> Optional[float]:
    """缓存年龄（天）。取不到 fetched_at 时返回 None（视为需要刷新）。"""
    p = _cache_path(code)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        ts = d.get("fetched_at")
        if not ts:
            return None
        from datetime import datetime  # noqa: PLC0415

        t = datetime.fromisoformat(ts)
        now = cn_now()
        if t.tzinfo is None:
            t = t.replace(tzinfo=now.tzinfo)
        return (now - t).total_seconds() / 86400.0
    except Exception:  # noqa: BLE001
        return None


def stale_codes(codes: list[str], max_age_days: float = 7.0) -> list[str]:
    """挑出需要刷新权息的票。

    除权事件本身是历史事实不会变，但**新的除权会不断出现**。分红送转有预案公告
    提前期（通常 >2 周），7 天上限足以在除权日前拿到新事件。
    """
    return [c for c in codes if (a := cache_age_days(c)) is None or a > max_age_days]


def get_xdxr(
    code: str, *, refresh: bool = False, timeout: int = 10
) -> list[dict[str, Any]]:
    """取权息数据：默认优先缓存（除权是历史事实，不会变），refresh=True 强制重取。"""
    if not refresh:
        ev = load_xdxr_cache(code)
        if ev is not None:
            return ev
    try:
        from mootdx.quotes import Quotes

        q = Quotes.factory(market="std", timeout=timeout)
        # 显式传 market —— 不走 q.xdxr() 的内部推断（它把 920xxx 判成沪市 ⇒ 取到空）
        raw = q.client.get_xdxr_info(_tdx_market(str(code)[:6]), str(code)[:6])
    except Exception as e:  # noqa: BLE001
        raise AdjustError(f"xdxr({code}) 取数失败: {e}") from e
    ev = normalize_xdxr(raw)
    save_xdxr_cache(
        code,
        ev,
        fetched_at=cn_now().isoformat(timespec="seconds"),
        shares=normalize_shares(raw),
    )
    return ev


# ---------------------------------------------------------------- 因子计算


def event_ratio(prev_close: float, ev: dict[str, Any]) -> Optional[float]:
    """单个除权事件的价格缩放比 = 除权参考价 / 前收盘。

    除权参考价 = (前收盘 − 现金红利/股 + 配股价 × 配股比例) / (1 + 送股比例 + 配股比例)

    配股项是**加**：股东掏 `配股价 × 配股比例` 的钱换来 `配股比例` 的股，
    理论价格是加权平均。漏掉这一项会把配股当纯送股，因子偏小。

    缩股（suogu）单独处理：每 10 股缩为 suogu 股 ⇒ 价格放大 10/suogu。
    """
    if prev_close is None or prev_close <= 0:
        return None
    fh = float(ev.get("fenhong") or 0.0) / 10.0  # 每股现金红利
    sz = float(ev.get("songzhuangu") or 0.0) / 10.0  # 送转比例
    pg = float(ev.get("peigu") or 0.0) / 10.0  # 配股比例
    pgj = float(ev.get("peigujia") or 0.0)  # 配股价
    sg = float(ev.get("suogu") or 0.0)

    denom = 1.0 + sz + pg
    if denom <= 0:
        return None
    ref = (prev_close - fh + pgj * pg) / denom
    if sg > 0:  # 缩股：10 股 → sg 股
        ref = ref * 10.0 / sg
    if ref <= 0:
        return None
    ratio = ref / prev_close
    if not (MIN_RATIO <= ratio <= 100.0):
        return None  # 明显异常，跳过
    return float(ratio)


def compute_qfq_factors(
    dates: Any, closes: Any, events: list[dict[str, Any]], stats: Optional[dict] = None
) -> np.ndarray:
    """逐根 K 线的前复权因子（乘到未复权价上即得前复权价）。

    因子(t) = Π ratio_d for 除权日 d > t   ⇒ **最新一天恒为 1.0**。

    ``dates`` 按升序；``closes`` 是未复权收盘价。事件日不在 K 线里（停牌/数据缺失）
    时按「该日之后第一根 K 线」定位，取其前一根的收盘价作为前收盘。

    ``stats``（可选）：传入 dict 时回填 ``events_in_sample`` / ``events_dropped``
    （落在样本内但 ratio 求不出、被跳过的事件数）——跳过必须可观测，
    否则"部分除权事件没参与复权"会变成静默降级（审计反复批判的失效模式）。
    """
    d = np.asarray([str(x)[:10] for x in dates])
    c = np.asarray(closes, dtype=float)
    n = len(d)
    factors = np.ones(n, dtype=float)
    if stats is not None:
        stats["events_in_sample"] = 0
        stats["events_dropped"] = 0
    if n == 0 or not events:
        return factors

    for ev in events:
        ed = ev["date"]
        idx = int(np.searchsorted(d, ed, side="left"))  # 首个 >= 除权日
        if idx <= 0 or idx >= n:
            continue  # 落在样本外
        if stats is not None:
            stats["events_in_sample"] += 1
        prev_close = c[idx - 1]
        r = event_ratio(float(prev_close), ev)
        if r is None:
            if stats is not None:
                stats["events_dropped"] += 1
            continue
        factors[:idx] *= r  # 只缩放除权日之前
    return factors


def apply_qfq(
    df: pd.DataFrame,
    events: list[dict[str, Any]],
    *,
    price_cols: tuple[str, ...] = ("open", "high", "low", "close"),
    volume_col: str = "volume",
) -> pd.DataFrame:
    """返回前复权后的副本，并把因子与原始收盘写进 attrs / 列。

    · 价格列 × 因子
    · **成交量 ÷ 因子**：送转后股数增加，不调量会让「量比 / 20 日量底」在除权日断层。
      现金分红不改变股数，但它的 ratio 也 ≠1；这是标准前复权的已知近似
      （同花顺/通达信同样处理），换来的是价与量口径一致。
    · `raw_close` 列保留未复权收盘，展示与下单可用。
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    if "date" not in out.columns or "close" not in out.columns:
        # 无法复权却什么都不标,会被 qfq_table 盖章成 "qfq"——降级必须留痕
        out.attrs["adjust"] = "none"
        out.attrs["adjust_error"] = "missing date/close columns"
        return out
    st: dict = {}
    f = compute_qfq_factors(
        out["date"].to_numpy(), out["close"].astype(float).to_numpy(), events, stats=st
    )
    out["raw_close"] = out["close"].astype(float)
    for col in price_cols:
        if col in out.columns:
            out[col] = out[col].astype(float) * f
    if volume_col in out.columns:
        v = out[volume_col].astype(float).to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            out[volume_col] = np.where(f > 0, v / f, v)
    dropped = st.get("events_dropped", 0)
    # 有样本内事件被跳过 ⇒ 只复权了一部分,标 qfq_partial 而非 qfq
    out.attrs["adjust"] = "qfq_partial" if dropped else "qfq"
    if dropped:
        out.attrs["adjust_events_dropped"] = dropped
        print(
            f"[WARN] {dropped} 个除权事件未能参与复权（ratio 异常被跳过）,"
            f"结果仅为部分前复权",
            file=sys.stderr,
        )
    out.attrs["adjust_factor_first"] = float(f[0]) if len(f) else 1.0
    out.attrs["adjust_events"] = sum(
        1
        for ev in events
        if len(out)
        and str(out["date"].iloc[0])[:10]
        <= ev["date"]
        <= str(out["date"].iloc[-1])[:10]
    )
    return out


def qfq_table(
    code: str, df: pd.DataFrame, *, refresh: bool = False, strict: bool = False
) -> pd.DataFrame:
    """给未复权 K 线加前复权。取不到权息数据时按 ``strict`` 决定 raise 还是原样返回。

    默认不 raise：权息取数依赖网络，而 18:00 选股链不能因为一只票的权息拿不到就整体停摆。
    但**必须留痕**——`attrs["adjust"]` 会是 `"none"`，下游可据此判断这批数据未复权
    （审计反复出现的失效模式就是「降级了但没人知道」）。
    """
    try:
        ev = get_xdxr(code, refresh=refresh)
    except AdjustError as e:
        if strict:
            raise
        print(f"[WARN] {code} 权息数据不可用，按未复权返回: {e}", file=sys.stderr)
        out = df.copy() if df is not None else df
        if out is not None and not out.empty:
            out.attrs["adjust"] = "none"
            out.attrs["adjust_error"] = str(e)
        return out
    out = apply_qfq(df, ev)
    if out is not None and not out.empty:
        # apply_qfq 内部已按实际情况盖章（qfq / qfq_partial / none）,不得覆盖
        out.attrs.setdefault("adjust", "qfq")
    return out


__all__ = [
    "AdjustError",
    "apply_qfq",
    "cache_age_days",
    "compute_qfq_factors",
    "get_shares_events",
    "normalize_shares",
    "total_shares_at",
    "event_ratio",
    "fetch_xdxr",
    "fetch_xdxr_batch",
    "get_xdxr",
    "load_xdxr_cache",
    "normalize_xdxr",
    "qfq_table",
    "save_xdxr_cache",
    "stale_codes",
]


def main() -> int:
    """CLI：预热/刷新权息缓存。

    首次必须预热——否则回测跑 1000 只票时每只都要走一次网络取权息。
    预热后是纯本地 json 读取；权息只需按 --max-age 增量刷新。
    """
    import argparse

    ap = argparse.ArgumentParser(description="前复权权息缓存管理")
    ap.add_argument(
        "--warmup", action="store_true", help="批量拉取权息并缓存（首次必做）"
    )
    ap.add_argument(
        "--codes", default="", help="逗号分隔的代码；留空=本地全市场（配合 --warmup）"
    )
    ap.add_argument(
        "--max-age",
        type=float,
        default=7.0,
        help="缓存超过该天数才刷新（默认 7；除权公告有 >2 周提前期）",
    )
    ap.add_argument("--force", action="store_true", help="忽略缓存年龄，全部重取")
    ap.add_argument("--show", default="", help="打印某只票的权息事件与因子")
    ap.add_argument("--stats", action="store_true", help="缓存统计")
    a = ap.parse_args()

    if a.show:
        code = a.show[:6]
        try:
            ev = get_xdxr(code)
        except AdjustError as e:
            print(f"[ERR] {e}")
            return 2
        print(f"{code} 共 {len(ev)} 个影响价格的权息事件：")
        for e in ev:
            print(
                f"  {e['date']}  分红{e['fenhong']:>6.3f}  送转{e['songzhuangu']:>5.2f}  "
                f"配股{e['peigu']:>5.2f}@{e['peigujia']:>6.2f}  缩股{e['suogu']:>5.2f}"
            )
        age = cache_age_days(code)
        print(f"缓存年龄：{'—' if age is None else f'{age:.1f} 天'}")
        return 0

    if a.stats:
        if not CACHE_DIR.exists():
            print("缓存目录不存在，先跑 --warmup")
            return 1
        files = sorted(CACHE_DIR.glob("*.json"))
        ages = [x for x in (cache_age_days(p.stem) for p in files) if x is not None]
        n_ev = 0
        for p in files:
            try:
                n_ev += int(json.loads(p.read_text(encoding="utf-8")).get("n") or 0)
            except Exception:  # noqa: BLE001
                pass
        print(f"缓存 {len(files)} 只，累计 {n_ev} 个权息事件")
        if ages:
            print(
                f"缓存年龄：中位 {sorted(ages)[len(ages) // 2]:.1f} 天  "
                f"最旧 {max(ages):.1f} 天"
            )
            stale = sum(1 for x in ages if x > a.max_age)
            print(f"超过 {a.max_age} 天需刷新：{stale} 只")
        return 0

    if not a.warmup:
        ap.print_help()
        return 0

    if a.codes:
        codes = [c.strip()[:6] for c in a.codes.split(",") if c.strip()]
    else:
        try:
            from custos.datasource.local_tdx import local_tdx_data

            codes = sorted(local_tdx_data.list_local_vipdoc_codes())
        except Exception as e:  # noqa: BLE001
            print(f"[ERR] 读不到本地代码表: {e}")
            return 2
    # 指数不除权，跳过（880/881 光细分行业就有 467 个）
    try:
        from custos.core.code_utils import is_index

        codes = [c for c in codes if not is_index(c)]
    except Exception:  # noqa: BLE001
        pass
    todo = codes if a.force else stale_codes(codes, a.max_age)
    print(
        f"共 {len(codes)} 只，需要取数 {len(todo)} 只"
        f"{'（--force 全量）' if a.force else f'（缓存 ≤{a.max_age} 天的已跳过）'}"
    )
    if not todo:
        print("全部命中缓存，无需取数")
        return 0
    try:
        got = fetch_xdxr_batch(todo)
    except AdjustError as e:
        print(f"[ERR] {e}")
        return 2
    with_ev = sum(1 for v in got.values() if v)
    print(
        f"完成：{len(got)} 只取到数据，其中 {with_ev} 只有权息事件"
        f"（无事件的票也缓存，避免反复重试）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
