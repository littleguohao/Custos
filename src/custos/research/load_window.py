# -*- coding: utf-8 -*-
"""加载窗口推算（owner 方法论 review #9，v0.328）——``--count`` 缺省自动化。

count 是「最新向前 N 根」滚动窗：过去每个研究工具各自补 ``--count`` 是
反复踩坑位（r36_c5 首跑 count=2000 只跑到 19 笔碎片宇宙；pre2019 又惯用
100000 全历史，加载白白变慢）。本模块按窗口起点推算缺省值，把该参数从
CLI 上拿掉；显式值永远是覆盖通道。实测到达仍由
``exit_c5_terminal.check_reach`` fail-closed 兜底（本模块是纯函数叶子，
不碰数据——放这里而非 exit_c5_terminal，是为让 exit_campaign 等可导入
而不成环：exit_c5_terminal 自身懒导入 exit_campaign）。
"""

from __future__ import annotations

import argparse
from datetime import date as _date
from typing import Any, Optional


def count_for_start(
    start: str, *, today: Optional[Any] = None, warmup_bars: int = 300
) -> int:
    """按窗口起点自动推算每股加载根数（--count 缺省值）。

    需覆盖 [start, today] 全部交易日，外加窗口起点前的指标预热根数
    （默认 300，覆盖 MA250 级预热）。交易日用 ``numpy.busday_count`` 估
    （周一~周五计数）：不扣法定节假日，每年多算 ~11 天——**高估是
    fail-closed 方向**（多加载无害），实测到达由 ``check_reach`` 兜底。
    ``today`` 可注入（测试确定性与生产环境无关）。start ≥ today ⇒
    ValueError（起点在未来没有可加载窗口，不猜）。
    """
    import numpy as np  # noqa: PLC0415

    today = today or _date.today()
    start_d = _date.fromisoformat(str(start)[:10])
    if start_d >= today:
        raise ValueError(
            f"count_for_start：窗口起点 {start_d} 不在今天 {today} 之前，"
            "没有可加载的历史窗口"
        )
    days = int(np.busday_count(start_d, today))
    return days + warmup_bars


def resolve_count(count: Optional[int], start: str) -> int:
    """--count 缺省（None）⇒ ``count_for_start`` 自动推算；显式值原样沿用
    （正数校验 fail-closed）。显式传值永远是覆盖通道（如 pre2019 终审惯用的
    100000 全历史），自动推算只接管「没填」的情况。"""
    if count is not None:
        if count <= 0:
            raise ValueError(f"--count 必须是正整数，得到 {count}")
        return count
    return count_for_start(start)


def main(argv: Optional[list[str]] = None) -> int:
    """诊断入口：打印某窗口起点的自动推算根数（不碰数据）。"""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="窗口起点 YYYY-MM-DD")
    ap.add_argument("--warmup-bars", type=int, default=300)
    args = ap.parse_args(argv)
    print(
        f"count_for_start({args.start}) = "
        f"{count_for_start(args.start, warmup_bars=args.warmup_bars)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
