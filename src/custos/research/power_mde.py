# -*- coding: utf-8 -*-
"""统计功效估算（MDE，owner 方法论 review #2，v0.326）。

跑数前不估统计功效 ⇒ untested 成为最常见的结局（R36-C5 / R37-C5 /
R39 a1/a2 / R37 b1 都是跑完才发现桶太薄、CI 太宽）。本模块把「最小可
检测效应（MDE）」变成预注册前的机械一算：**MDE 大于合理效应时，跑数
前就合并桶或改问法**——预注册「功效」节（R42 起新单元必备）的输入
数字由此处出。

公式（C5 γ 分析同式，owner 核过）：

    SE(Δmargin) ≈ sqrt(wr·(1−wr)/n) · sqrt(2·(1−ρ))

- wr：胜率；n：样本交易数（**每档**，不是总窗）；
- ρ：两比较边在同日市场冲击下的相关性——配对（同信号两出场）取 ~0.75
  （C5 分析用值）；非配对但同窗（分档 A 档 vs B 档）取 0.3~0.5 情景区间；
- ⚠️ **本式忽略 margin 的 payoff 噪声 ⇒ SE 是下界**——真实 MDE 只会
  更大（写功效节时连带本注记，不得只报数不报偏）。

MDE = k × SE（k=2 默认 ≈ 双侧 95% 检测门槛）。
"""

from __future__ import annotations

import argparse
import math
from typing import Optional

#: ρ 场景锚（配对=C5 γ 分析用值 0.75；分档非配对同窗给区间）
RHO_PAIRED = 0.75
RHO_TIER_SCENARIOS = (0.3, 0.5, 0.75)


def se_margin(wr: float, n: int, rho: float) -> float:
    """Δmargin 的标准误（下界——payoff 噪声忽略；ρ=同日市场相关）。"""
    if n <= 0:
        raise ValueError(f"n 必须 >0: {n}")
    if not (0.0 < wr < 1.0):
        raise ValueError(f"wr 必须在 (0,1): {wr}")
    if not (0.0 <= rho < 1.0):
        raise ValueError(f"rho 必须在 [0,1): {rho}")
    return math.sqrt(wr * (1.0 - wr) / n) * math.sqrt(2.0 * (1.0 - rho))


def mde(wr: float, n: int, rho: float, *, k: float = 2.0) -> float:
    """最小可检测效应 = k × SE（k=2 默认 ≈ 双侧 95%）。"""
    return k * se_margin(wr, n, rho)


def table(
    wr: float, n: int, *, rhos: tuple[float, ...] = RHO_TIER_SCENARIOS, k: float = 2.0
) -> list[dict[str, float]]:
    """逐 ρ 场景的 SE/MDE 表（功效节贴表用）。"""
    return [
        {"rho": rho, "se": se_margin(wr, n, rho), "mde": mde(wr, n, rho, k=k)}
        for rho in rhos
    ]


def main(argv: Optional[list[str]] = None) -> int:
    """MDE 估算 CLI：`--wr 0.46 --n 133 [--rho 0.3,0.5,0.75] [--k 2]`。"""
    ap = argparse.ArgumentParser(
        description="统计功效估算（MDE=k×SE；SE=sqrt(wr(1−wr)/n)·sqrt(2(1−ρ))，下界——payoff 噪声忽略）"
    )
    ap.add_argument("--wr", type=float, required=True, help="胜率（0,1）")
    ap.add_argument("--n", type=int, required=True, help="样本交易数（每档）")
    ap.add_argument(
        "--rho",
        default=",".join(str(r) for r in RHO_TIER_SCENARIOS),
        help="同日市场相关（逗号多值；配对用 0.75）",
    )
    ap.add_argument(
        "--k", type=float, default=2.0, help="MDE 倍数（默认 2 ≈ 双侧 95%）"
    )
    args = ap.parse_args(argv)
    rhos = tuple(float(x) for x in str(args.rho).split(",") if x.strip())
    if not rhos:
        ap.error("--rho 为空")
    print(
        f"MDE 估算（SE 下界——payoff 噪声忽略，真实 MDE 只会更大）：wr={args.wr} n={args.n} k={args.k}"
    )
    for row in table(args.wr, args.n, rhos=rhos, k=args.k):
        print(f"  ρ={row['rho']:.2f}  SE={row['se']:.4f}  MDE={row['mde']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
