# -*- coding: utf-8 -*-
"""涨跌家数口径的真值来源。

**为什么单独一个模块**:两处采集脚本(market_timing_collector / refresh_market_indices)
原先各自硬编码 ``TOTAL_STOCKS_APPROX = 5530``，用 ``down = 总数 - 涨家数`` 推算跌家数。
这个推算把**平盘、停牌、当日未成交**的股票全部计入"下跌"，使 ``up_down_ratio``
**系统性偏低**；而 market_timing_scorer.score_breadth 直接吃这个比值给分
（ratio ≥2 得 15 分、<0.5 只得 2 分），于是宽度这一项长期被低估。
近似总数还会随上市/退市漂移，没人会去改那个常量。

现在的原则：**能拿到真实总数就用真值，拿不到就把比值标为不可用**（scorer 见到
down_count=None 会走 7.5 中性），绝不用一个来源不明的近似值去驱动打分。

2026-08-29（v0.137，owner 拍板方案③）：**跌/平/停家数改由 vipdoc 本地自算**
（:func:`compute_breadth_from_vipdoc`）—— 遍历 A 股宇宙逐只读 ``.day`` 尾部两根
比较收盘，涨/跌/平三桶，末日落后于全宇宙最新交易日的进停牌/陈旧桶。实测与
880005 官方涨家数差 ~0.3%（宇宙边界），>2% 标 warning 不阻断。自算成功即
**真值口径**（``up_down_ratio_status="vipdoc_self_compute"``），不再
derived_from_total；自算失败才回落下面的总数推算/不可用两档。

总数推算的真值来源（回落路径，按优先级）：
1. 环境变量 ``A_SHARE_TOTAL_STOCKS`` —— 运维显式给定的可核对数字；
2. ``data/market/a_share_universe.json`` 的 ``total`` 字段 —— 由外部流程写入。

两者都没有就返回 ``(None, reason)``。**不**拿本地 vipdoc 文件数当总数：那里含
已退市标的，会把总数抬高、跌家数推得更多，正好加重要修的这个偏差。
"""

from __future__ import annotations

import json
import os
import struct

from pathlib import Path


from custos.core.paths import MARKET_DIR  # noqa: E402

UNIVERSE_FILE = MARKET_DIR / "a_share_universe.json"
ENV_KEY = "A_SHARE_TOTAL_STOCKS"
# 合理区间:A 股上市家数在 4000~7000 之间;超出即视为脏值,宁可标不可用。
MIN_TOTAL, MAX_TOTAL = 4000, 7000

UNAVAILABLE_NOTE = (
    "跌家数无真实数据源（880005 vipdoc 只给涨家数），且拒绝用硬编码总数推算"
    "（会把平盘/停牌计入下跌，使涨跌比系统性偏低）；up_down_ratio 标记 unavailable，"
    f"评分按中性处理。可通过环境变量 {ENV_KEY} 或 {UNIVERSE_FILE.name} 提供真实总数。"
)
DERIVED_NOTE = (
    "跌家数由「真实总数 - 涨家数」推算：平盘/停牌/未成交个股会被计入下跌，"
    "涨跌比偏保守，仅作方向参考。"
)


def _sane(value) -> int | None:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if MIN_TOTAL <= n <= MAX_TOTAL else None


def resolve_total_stocks() -> tuple[int | None, str]:
    """返回 ``(总数, 来源说明)``；拿不到真值返回 ``(None, 原因)``。"""
    raw = os.environ.get(ENV_KEY)
    if raw is not None:
        n = _sane(raw)
        if n is not None:
            return n, f"env:{ENV_KEY}"
        return (
            None,
            f"env:{ENV_KEY} 取值非法（{raw!r}，要求 {MIN_TOTAL}~{MAX_TOTAL} 整数）",
        )
    try:
        if UNIVERSE_FILE.is_file():
            data = json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))
            n = _sane((data or {}).get("total"))
            if n is not None:
                return n, f"universe_file:{UNIVERSE_FILE.name}"
            return None, f"universe_file:{UNIVERSE_FILE.name} 的 total 非法或缺失"
    except (OSError, json.JSONDecodeError) as e:
        return None, f"universe_file 读取失败: {e}"
    return None, "无真实总数来源（未设 env，且 a_share_universe.json 不存在）"


def breadth_counts(
    up_count, total: int | None = None, source: str | None = None
) -> dict:
    """由涨家数推导 down_count / up_down_ratio 及其口径标记。

    ``total`` / ``source`` 可由调用方注入（调用方自己 resolve，便于单测替换真值源）；
    不传则内部调用 :func:`resolve_total_stocks`。

    返回的键固定为 ``down_count`` / ``up_down_ratio`` / ``up_down_ratio_status`` /
    ``total_stocks`` / ``total_stocks_source`` / ``note``，供两个采集脚本共用。
    """
    if total is None and source is None:
        total, source = resolve_total_stocks()
    if up_count is None or total is None:
        return {
            "down_count": None,
            "up_down_ratio": None,
            "up_down_ratio_status": "unavailable",
            "total_stocks": None,
            "total_stocks_source": source,
            "note": UNAVAILABLE_NOTE,
        }
    down = total - int(up_count)
    if down <= 0:
        return {
            "down_count": None,
            "up_down_ratio": None,
            "up_down_ratio_status": "unavailable",
            "total_stocks": total,
            "total_stocks_source": source,
            "note": f"涨家数 {up_count} ≥ 总数 {total}，推算跌家数不成立；"
            + UNAVAILABLE_NOTE,
        }
    return {
        "down_count": int(down),
        "up_down_ratio": round(int(up_count) / down, 4),
        "up_down_ratio_status": "derived_from_total",
        "total_stocks": total,
        "total_stocks_source": source,
        "note": DERIVED_NOTE,
    }


# ========== vipdoc 本地自算涨跌平停四桶（v0.137 方案③，首选真值来源） ==========

# vipdoc .day 一根 32 字节：date, open, high, low, close（×100 整数）,
# amount(float), volume, reserved。逐只只读尾部两根。
_BAR = struct.Struct("<IIIIIfII")
_BAR_SIZE = 32

#: 自算口径的状态词（区别于 "derived_from_total" / "unavailable"）。
VIPDOC_STATUS = "vipdoc_self_compute"
#: 自算涨家数与 880005 官方值差异超过该比例 → crosscheck 标 warning（不阻断）。
CROSSCHECK_WARN_PCT = 2.0


def _day_file_map(root: Path) -> dict[str, Path]:
    """扫 vipdoc/{sh,sz,bj}/lday → {code6: 路径}（与 list_local_vipdoc_codes 同盘口径）。"""
    out: dict[str, Path] = {}
    for mkt in ("sh", "sz", "bj"):
        d = root / "vipdoc" / mkt / "lday"
        if not d.is_dir():
            continue
        for p in d.glob(f"{mkt}*.day"):
            code6 = p.stem[len(mkt) :]
            if len(code6) == 6 and code6.isdigit():
                out[code6] = p
    return out


def _tail_bars(path: Path, n: int = 2) -> list[tuple[int, float]]:
    """读 .day 尾部至多 n 根 → [(date:YYYYMMDD int, close)]，不足则返回已有的。

    ⚠️ 空文件/不足一根必须返回 [] 而不是 seek 负数报错（2026-08-28 实测原型踩点）。
    """
    try:
        size = path.stat().st_size
    except OSError:
        return []
    take = min(n, size // _BAR_SIZE)
    if take <= 0:
        return []
    try:
        with path.open("rb") as f:
            f.seek(size - take * _BAR_SIZE)
            buf = f.read(take * _BAR_SIZE)
    except OSError:
        return []
    out = []
    for off in range(0, take * _BAR_SIZE, _BAR_SIZE):
        d, _o, _h, _l, c, _a, _v, _r = _BAR.unpack_from(buf, off)
        out.append((d, c / 100.0))
    return out


def _official_up_880005(ltd) -> tuple[int | None, str]:
    """880005.SH vipdoc 末日 close = 官方涨家数（对照校验用），失败返回 (None, '')。"""
    try:
        df = ltd.get_ohlcv_table("880005.SH", count=3, prefer="vipdoc")
        if df.empty:
            return None, ""
        row = df.iloc[-1]
        close = row.get("close")
        d = row.get("date")
        d = d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d)
        return (int(close) if close is not None else None), d
    except Exception:  # noqa: BLE001 —— 对照校验是 best-effort，挂了不拖垮自算
        return None, ""


def _bucket_counts(tails: dict, latest: int) -> tuple[int, int, int, int]:
    """按末日 vs 前日收盘分 涨/跌/平/停 四桶；尾部不足两根或末日陈旧归停牌桶。"""
    up = down = flat = susp = 0
    for t in tails.values():
        if len(t) < 2 or t[-1][0] < latest:
            susp += 1
        elif t[-1][1] > t[-2][1]:
            up += 1
        elif t[-1][1] < t[-2][1]:
            down += 1
        else:
            flat += 1
    return up, down, flat, susp


def _crosscheck_up(ltd, up: int) -> dict:
    """自算涨家数与 880005 官方值对照，差异 >2% 标 warning（不阻断）。"""
    official, official_date = _official_up_880005(ltd)
    crosscheck: dict = {"official_up_880005": official, "as_of": official_date}
    if official:
        diff_pct = round((up - official) / official * 100, 2)
        crosscheck["diff_pct"] = diff_pct
        crosscheck["status"] = (
            "ok" if abs(diff_pct) <= CROSSCHECK_WARN_PCT else "warning"
        )
    else:
        crosscheck["status"] = "skipped"
    return crosscheck


def _breadth_note(
    universe_size: int,
    latest: int,
    up: int,
    down: int,
    flat: int,
    susp: int,
    crosscheck: dict,
    stale: bool,
    expected: str,
) -> str:
    """组装人类可读 note：四桶计数 + 880005 对照结论 + stale 告警。"""
    note = (
        f"vipdoc 本地自算（宇宙 {universe_size} 只，数据日 {latest}）："
        f"涨 {up} / 跌 {down} / 平 {flat} / 停 {susp}"
    )
    if crosscheck.get("official_up_880005"):
        note += (
            f"；对照 880005 官方涨家数 {crosscheck['official_up_880005']}"
            f"，差 {crosscheck['diff_pct']}%"
        )
        note += (
            "（宇宙边界差，正常）"
            if crosscheck["status"] == "ok"
            else "（>2%，warning）"
        )
    else:
        note += "；880005 对照缺失"
    if stale:
        note += f"；⚠️ 数据日 {latest} ≠ 期望 {expected}（vipdoc 未更新到当日）"
    return note


def compute_breadth_from_vipdoc(date: str | None = None, tdx_root=None) -> dict:
    """遍历本地 vipdoc A 股宇宙自算 涨/跌/平/停 四桶（owner 批准的方案③）。

    口径（2026-08-28 生产机实测原型，~0.3s / 5550 只）：
    - 逐只读 .day 尾部两根，末日收盘 vs 前日收盘 ⇒ 涨/跌/平；
    - 先扫一遍定**全宇宙最新交易日**，末日 < 它的进停牌/陈旧桶（当日无新 K 线）；
      尾部不足两根（空文件/新股）无法判定涨跌，也归入该桶 —— 四桶合计恒等于宇宙数；
    - 自算涨家数与 880005 官方值对照，差异 >2% 标 ``warning`` 写进 note，**不阻断**
      （宇宙边界差 ~0.3% 属正常）。

    已知偏差（除权除息日误计「跌」桶，2026-08-31 复核确认）：``.day`` 存的是
    **不复权价**，除息日收盘会机械性下跌（分红从股价中扣除），被本分桶口径计为「跌」；
    而 880005 官方口径按**调整后昨收**比较，不计为跌。量级约每天几十只，方向固定 —
    跌桶略偏大 ⇒ 市场温度略偏低（保守方向，不会把弱市读成强市）。暂不修：修正需把
    复权因子（xdxr 除权数据）与每只个股逐日对齐，成本与这部分偏差的收益不成比例，
    且偏保守方向对打分/渲染不构成误判风险。后续若做精确对齐可再议。

    ``date``（YYYY-MM-DD 或 YYYYMMDD）为期望数据日，仅用于标注 ``stale``，
    分桶永远按 vipdoc 实有的最新交易日算（数据日是几号就如实报几号）。
    失败返回 ``{"available": False, "note": 原因}``，调用方回落 breadth_counts。
    """
    try:
        from custos.datasource.local_tdx import local_tdx_data as ltd

        codes = ltd.list_local_vipdoc_codes(tdx_root=tdx_root)
    except Exception as e:  # noqa: BLE001 —— TDX_ROOT 未配/读盘失败都走回落
        return {"available": False, "note": f"vipdoc 宇宙枚举失败: {e!r}"}
    if not codes:
        return {"available": False, "note": "本地 vipdoc 无 A 股日线文件，无法自算"}
    root = Path(tdx_root) if tdx_root else ltd.TDX_ROOT
    files = _day_file_map(root)
    tails = {c: _tail_bars(files[c]) for c in codes if c in files}
    latest = max((t[-1][0] for t in tails.values() if t), default=0)
    if not latest:
        return {"available": False, "note": "vipdoc 日线全部不可读（空宇宙）"}

    up, down, flat, susp = _bucket_counts(tails, latest)

    crosscheck = _crosscheck_up(ltd, up)

    expected = str(date).replace("-", "")[:8] if date else ""
    stale = bool(expected) and str(latest) != expected

    note = _breadth_note(
        len(codes), latest, up, down, flat, susp, crosscheck, stale, expected
    )

    return {
        "available": True,
        "as_of": str(latest),
        "stale": stale,
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "suspended_count": susp,
        "universe_size": len(codes),
        "up_down_ratio": round(up / down, 4) if down else None,
        "crosscheck_880005": crosscheck,
        "note": note,
    }


def breadth_counts_real(up_count, date: str | None = None, tdx_root=None) -> dict:
    """真值口径优先：vipdoc 本地自算四桶；自算失败回落 :func:`breadth_counts`。

    返回 ``breadth_counts`` 同形键 + ``flat_count`` / ``suspended_count`` /
    ``vipdoc_as_of``（自算桶的数据日，机器可读；回落路径为 None —— note 文案
    里的人读日期不变）。``up_count`` 仍是 880005 官方涨家数（渲染/评分沿用），
    自算值只用于对照校验；``up_down_ratio`` 与写入 JSON 的 up_count/down_count
    同口径（官方涨 ÷ 自算跌）。``tdx_root`` 仅供测试注入，生产调用不传。
    """
    real = compute_breadth_from_vipdoc(date=date, tdx_root=tdx_root)
    if real.get("available"):
        up = int(up_count) if up_count else real["up_count"]
        down = real["down_count"]
        return {
            "down_count": down,
            "flat_count": real["flat_count"],
            "suspended_count": real["suspended_count"],
            "up_down_ratio": round(up / down, 4) if down else None,
            "up_down_ratio_status": VIPDOC_STATUS,
            "total_stocks": real["universe_size"],
            "total_stocks_source": "vipdoc_universe_self_compute",
            "vipdoc_as_of": real["as_of"],
            "note": real["note"],
        }
    fb = breadth_counts(up_count)
    fb["flat_count"] = None
    fb["suspended_count"] = None
    fb["vipdoc_as_of"] = None
    fb["note"] = f"{fb['note']}（vipdoc 自算不可用：{real.get('note')}）"
    return fb
