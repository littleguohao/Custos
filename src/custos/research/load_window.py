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
from datetime import timedelta as _timedelta
from typing import Any, Iterable, Optional

#: 前向 holdout 冻结起点（v0.321，owner 方法论 review #1②）：≥此日期的
#: 新数据保留为下一轮判定窗（前向样本外），任何研究不得使用。**单源在此**
#: （原钉在 exit_c5_terminal——v0.330 下沉：exit_campaign/score_evolution/
#: evolution_loop/strategy_grid/backtest_factors 入口漏守被 owner review
#: #2 实测抓出）；exit_c5_terminal 等处 re-export 兼容。
FORWARD_HOLDOUT_START = "2026-09-05"

#: 新窗口布局段常量（owner 2026-10-13 拍板，TODO #90，v2 布局）：
#: 挖掘 A（原 pre2019 并入——起点待 TODO #91 数据核查，单源在此一行可调）/
#: 判定（攒一批候选**只读一次**，单独记台账）/ 挖掘 B（原挖掘窗+已读脏的
#: 原判定窗）/ 前向 holdout（冻结不变）。C5 pre2019 终审退役（已判结论不改）。
SEG_MINING_A = ("2000-01-01", "2016-12-31")
SEG_JUDGMENT = ("2017-01-01", "2021-12-31")
SEG_MINING_B = ("2022-01-01", "2026-09-04")
SEGMENTS = (SEG_MINING_A, SEG_JUDGMENT, SEG_MINING_B)
SEGMENT_NAMES = ("mining_a", "judgment", "mining_b")

#: 段边界禁开仓隔离带 = 段末单侧 90 自然日（≈最长持有期 3 个月——
#: owner 2026-10-13 拍板）：截止后不开仓，持仓就不会跨段读到下一段的
#: 结果；段首不滤（指标预热只读价格，定案豁免）。
SEGMENT_BUFFER_DAYS = 90


def entry_deadline(seg_end: str) -> str:
    """段的入场截止日 = 段末日 − SEGMENT_BUFFER_DAYS（YYYY-MM-DD）。"""
    return (
        _date.fromisoformat(str(seg_end)[:10]) - _timedelta(days=SEGMENT_BUFFER_DAYS)
    ).isoformat()


def clip_entries(recs: list, seg_end: str, *, key: Any = "date") -> list:
    """滤掉入场日 > entry_deadline(seg_end) 的记录（段末隔离带单源）。

    ``key``：记录里入场日字段名（信号 "date" / 交易 "entry_date"）或
    取日期的可调用；段首侧不滤（预热只读价格不算读判定段）。剔除数由
    调用方 len 差值自记（报告要求隔离带剔除数在案）。"""
    dl = entry_deadline(seg_end)
    get = key if callable(key) else lambda r: str(r.get(key) or "")[:10]
    return [r for r in recs if get(r) <= dl]


def segment_violation(start: str, end: str) -> Optional[str]:
    """窗口段检查（v2 布局单源）：[start,end] 须完整落在**某一段内**
    （挖掘 A / 判定 / 挖掘 B 之一；段内子窗合法——R40 旧窗 ⊆ 挖掘 B 自动
    合法，兼容通道在案），越段或触及前向 holdout ⇒ 错误文案；否则 None。"""
    s, e = str(start)[:10], str(end)[:10]
    if e < s:
        return f"窗口 {s}~{e} 起终颠倒"
    msg = forward_holdout_violation(s, e)
    if msg:
        return msg
    for ss, ee in SEGMENTS:
        if ss <= s and e <= ee:
            return None
    return (
        f"窗口 {s}~{e} 不在任何研究段内"
        f"（挖掘 A {SEG_MINING_A[0]}~{SEG_MINING_A[1]} /"
        f" 判定 {SEG_JUDGMENT[0]}~{SEG_JUDGMENT[1]} /"
        f" 挖掘 B {SEG_MINING_B[0]}~{SEG_MINING_B[1]}）"
        "——越段窗口禁止（v2 布局，owner 2026-10-13 拍板）"
    )


#: 出场落 holdout 的口径注记（owner review #2②：判定窗末尾前进场的交易，
#: 出场会落到 holdout 区间的 bar 上）。选择**注记**而非截数据：截到 09-04
#: 会把窗口末段持仓强平在最后 bar，改变出场语义、读数失真。泄漏是
#: 单侧机械的（holdout bar 只执行既定持仓的出场，不参与任何选择）。
EXIT_BARS_HOLDOUT_NOTE = (
    "判定窗末尾前进场的交易，出场执行会用到前向 holdout 区间的 bar（单侧"
    "机械泄漏：holdout 数据不参与选型/判据，只执行既定持仓的出场；截数据"
    "会把末段持仓强平失真——owner review #2② 注记在案）"
)


def forward_holdout_violation(start: str, end: str) -> Optional[str]:
    """窗口触及前向 holdout 冻结段 ⇒ 错误文案；否则 None。

    空 end（不限）⇒ None：引擎类 CLI（backtest_factors/strategy_grid）的
    默认形态就是不限终点，是否吃进 holdout 数据取决于本机数据末日——
    由调用方自行决定是否对空 end 另立纪律；研究终端的窗口都是显式的。
    """
    e = str(end)[:10]
    if e and e >= FORWARD_HOLDOUT_START:
        return (
            f"窗口 {start}~{e} 触及前向 holdout 冻结段"
            f"（{FORWARD_HOLDOUT_START} 起）——2026-09 以后的新数据保留为"
            "下一轮判定窗（前向样本外），任何研究不得使用（v0.321 owner 拍板）"
        )
    return None


def reject_forward_holdout(
    windows: Iterable[tuple[str, str, str]], ap: argparse.ArgumentParser
) -> None:
    """{(wname, start, end)} 逐个校验，触及 ⇒ ap.error（exit 2 硬拒绝）。"""
    for wname, s, e in windows:
        msg = forward_holdout_violation(s, e)
        if msg:
            ap.error(f"{wname} {msg}")


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
