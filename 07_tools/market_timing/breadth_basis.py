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

真值来源（按优先级）：
1. 环境变量 ``A_SHARE_TOTAL_STOCKS`` —— 运维显式给定的可核对数字；
2. ``01_data/market/a_share_universe.json`` 的 ``total`` 字段 —— 由外部流程写入。

两者都没有就返回 ``(None, reason)``。**不**拿本地 vipdoc 文件数当总数：那里含
已退市标的，会把总数抬高、跌家数推得更多，正好加重要修的这个偏差。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
UNIVERSE_FILE = BASE / "01_data" / "market" / "a_share_universe.json"
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
        return None, f"env:{ENV_KEY} 取值非法（{raw!r}，要求 {MIN_TOTAL}~{MAX_TOTAL} 整数）"
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


def breadth_counts(up_count, total: int | None = None, source: str | None = None) -> dict:
    """由涨家数推导 down_count / up_down_ratio 及其口径标记。

    ``total`` / ``source`` 可由调用方注入（调用方自己 resolve，便于单测替换真值源）；
    不传则内部调用 :func:`resolve_total_stocks`。

    返回的键固定为 ``down_count`` / ``up_down_ratio`` / ``up_down_ratio_status`` /
    ``total_stocks`` / ``total_stocks_source`` / ``note``，供两个采集脚本共用。
    """
    if total is None and source is None:
        total, source = resolve_total_stocks()
    if up_count is None or total is None:
        return {"down_count": None, "up_down_ratio": None,
                "up_down_ratio_status": "unavailable",
                "total_stocks": None, "total_stocks_source": source,
                "note": UNAVAILABLE_NOTE}
    down = total - int(up_count)
    if down <= 0:
        return {"down_count": None, "up_down_ratio": None,
                "up_down_ratio_status": "unavailable",
                "total_stocks": total, "total_stocks_source": source,
                "note": f"涨家数 {up_count} ≥ 总数 {total}，推算跌家数不成立；" + UNAVAILABLE_NOTE}
    return {"down_count": int(down),
            "up_down_ratio": round(int(up_count) / down, 4),
            "up_down_ratio_status": "derived_from_total",
            "total_stocks": total, "total_stocks_source": source,
            "note": DERIVED_NOTE}
