# -*- coding: utf-8 -*-
"""统计功效估算（MDE，owner 方法论 review #2，v0.326；#3 返修 v0.330）。

跑数前不估统计功效 ⇒ untested 成为最常见的结局（R36-C5 / R37-C5 /
R39 a1/a2 / R37 b1 都是跑完才发现桶太薄、CI 太宽）。本模块把「最小可
检测效应（MDE）」变成预注册前的机械一算：**MDE 大于合理效应时，跑数
前就合并桶或改问法**——预注册「功效」节（R42 起新单元必备）的输入
数字由此处出。

公式（C5 γ 分析同式，owner 核过）：

    SE(Δmargin) ≈ sqrt(wr·(1−wr)/n) · sqrt(2·(1−ρ_pair)) · sqrt(deff)

- wr：胜率；n：样本交易数（**每档**，不是总窗）；
- **ρ_pair：两比较边的配对相关**（同信号两变体的逐笔收益相关）——越高
  SE **越小**（配对把共同噪声对消掉）。配对（同信号两出场）取 ~0.75
  （C5 γ 分析用值）；分档 A/B 非配对但同窗给 0.3~0.5 情景区间；
- **deff：日簇设计效应**（= 1+(m−1)·ICC，m=同日簇内交易数）——同日进场
  的交易受同一市场冲击彼此相关，把有效样本数打折 ⇒ SE **变大**。这正是
  v0.324 把 bootstrap 改日簇重抽的原因。实测口径：**deff = SE²（日簇
  bootstrap）/ SE²（iid bootstrap）**（同数据两口径各跑一次）；无实测时
  用 ``DEFF_SCENARIOS`` 情景区间并注明（deff=1 是「无日簇效应」下界）。
- ⚠️ v0.330 返修（owner review #3）：原式把两个**方向相反**的相关混成
  一个 ρ 且 docstring 误标「同日市场相关」——同日市场相关走 deff 通道
  让 SE 变大，与配对相关方向相反；缺 deff 项 ⇒ **MDE 被低估**（R42 结论
  方向不受影响：原结论就是「MDE 已远大于合理效应」，真实 MDE 只会更大）。
- ⚠️ **本式忽略 margin 的 payoff 噪声 ⇒ SE 是下界**——真实 MDE 只会
  更大（写功效节时连带本注记，不得只报数不报偏）。

MDE = k × SE（k=2 默认 ≈ 双侧 95% 检测门槛）。
"""

from __future__ import annotations

import argparse
import math
from typing import Optional

#: ρ_pair 场景锚（配对=C5 γ 分析用值 0.75；分档非配对同窗给区间）
RHO_PAIRED = 0.75
RHO_TIER_SCENARIOS = (0.3, 0.5, 0.75)
#: deff 场景锚（无实测时的情景区间；1.0=无日簇效应下界——有同口径
#: 日簇/iid bootstrap SE² 比值时必须用实测值替换并注明出处）
DEFF_SCENARIOS = (1.0, 1.5, 2.0)


def se_margin(wr: float, n: int, rho_pair: float, deff: float = 1.0) -> float:
    """Δmargin 的标准误（下界——payoff 噪声忽略）。

    ``rho_pair``=配对相关（越高 SE 越小）；``deff``=日簇设计效应
    （越高 SE 越大，≥1）——两个相关方向相反，不许再混成一个参数。
    """
    if n <= 0:
        raise ValueError(f"n 必须 >0: {n}")
    if not (0.0 < wr < 1.0):
        raise ValueError(f"wr 必须在 (0,1): {wr}")
    if not (0.0 <= rho_pair < 1.0):
        raise ValueError(f"rho_pair 必须在 [0,1): {rho_pair}")
    if deff < 1.0:
        raise ValueError(f"deff 必须 ≥1（设计效应只放大有效噪声）: {deff}")
    return (
        math.sqrt(wr * (1.0 - wr) / n)
        * math.sqrt(2.0 * (1.0 - rho_pair))
        * math.sqrt(deff)
    )


def mde(
    wr: float, n: int, rho_pair: float, deff: float = 1.0, *, k: float = 2.0
) -> float:
    """最小可检测效应 = k × SE（k=2 默认 ≈ 双侧 95%）。"""
    return k * se_margin(wr, n, rho_pair, deff)


def table(
    wr: float,
    n: int,
    *,
    rhos: tuple[float, ...] = RHO_TIER_SCENARIOS,
    deffs: tuple[float, ...] = DEFF_SCENARIOS,
    k: float = 2.0,
) -> list[dict[str, float]]:
    """逐 (ρ_pair × deff) 场景的 SE/MDE 表（功效节贴表用）。"""
    return [
        {
            "rho_pair": rho,
            "deff": deff,
            "se": se_margin(wr, n, rho, deff),
            "mde": mde(wr, n, rho, deff, k=k),
        }
        for rho in rhos
        for deff in deffs
    ]


def main(argv: Optional[list[str]] = None) -> int:
    """MDE 估算 CLI：`--wr 0.46 --n 133 [--rho 0.3,0.5,0.75] [--deff 1,1.5,2] [--k 2]`。"""
    ap = argparse.ArgumentParser(
        description="统计功效估算（MDE=k×SE；SE=sqrt(wr(1−wr)/n)·sqrt(2(1−ρ_pair))·sqrt(deff)，下界——payoff 噪声忽略）"
    )
    ap.add_argument("--wr", type=float, required=True, help="胜率（0,1）")
    ap.add_argument("--n", type=int, required=True, help="样本交易数（每档）")
    ap.add_argument(
        "--rho",
        default=",".join(str(r) for r in RHO_TIER_SCENARIOS),
        help="配对相关 ρ_pair（逗号多值；同信号两变体配对用 0.75——越高 SE 越小）",
    )
    ap.add_argument(
        "--deff",
        default=",".join(str(d) for d in DEFF_SCENARIOS),
        help="日簇设计效应（逗号多值；实测=日簇 bootstrap SE²÷iid SE²，"
        "缺省 1.0 是无日簇效应下界——越高 SE 越大）",
    )
    ap.add_argument(
        "--k", type=float, default=2.0, help="MDE 倍数（默认 2 ≈ 双侧 95%%）"
    )
    args = ap.parse_args(argv)
    rhos = tuple(float(x) for x in str(args.rho).split(",") if x.strip())
    deffs = tuple(float(x) for x in str(args.deff).split(",") if x.strip())
    if not rhos or not deffs:
        ap.error("--rho/--deff 为空")
    print(
        f"MDE 估算（SE 下界——payoff 噪声忽略，真实 MDE 只会更大）：wr={args.wr} n={args.n} k={args.k}"
    )
    for row in table(args.wr, args.n, rhos=rhos, deffs=deffs, k=args.k):
        print(
            f"  ρ_pair={row['rho_pair']:.2f} deff={row['deff']:.1f}"
            f"  SE={row['se']:.4f}  MDE={row['mde']:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
