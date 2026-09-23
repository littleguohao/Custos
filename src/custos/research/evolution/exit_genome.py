# -*- coding: utf-8 -*-
"""R37 出场轴基因组：机制开关 × 参数档位的合法空间与规则化变异算子。

预注册 = ``governance/research/R37_exit_axis_evolution_campaign.md``
（跑数前写死；本模块是它的基因空间代码化身）。入场钉死 V0+j_low 基底
不动，基因组只覆盖出场；变异算子规则化（参数扰动/开关翻转/档位迁移），
LLM 不碰数值。

空间策展（R10/R16 已闭环的轴不进 Phase 1，写死）：

- ``stop_mode`` 固定 ``"pct"``（R10：low 是不可执行基准；platform 依赖
  detector 上下文）；
- ``stop_trigger`` 固定 ``"close"``（R16 收盘口径，intraday 旧口径退役）；
- ``stop_buffer``/``bbi_exit_consec`` 固定 simulate 默认（R10 #20 tick 对照
  已判；BBI 两连破是现行止盈清仓主通道，Phase 1 不动）；
- 六个可开关家族（CTL-2 的关闭单位）：trail / breakeven / scale_out /
  cost_zone / qsx / time_stop；``stop_pct`` 恒开（基础止损水平轴，R10
  「5% 是崖」的量尺）。

off 的编码 = ``simulate_b1_trade`` 的关闭语义（对应参数 = 0），归一化后的
基因组 dict 可直接 ``**`` 展开进 ``evaluate_trades``。
"""

from __future__ import annotations

from typing import Any, Optional

#: 基础止损水平轴（恒开，stop_mode="pct" 固定——R10「5% 是崖」两侧的探针档）。
STOP_PCT_LEVELS: tuple[float, ...] = (4.0, 5.0, 6.0, 8.0, 10.0, 12.0)

#: 各参数的合法档位（升序）。档位外取值 = 非法基因组（validate 拦下）。
LEVELS: dict[str, tuple[float, ...]] = {
    "stop_pct": STOP_PCT_LEVELS,
    "trail_pct": (0.06, 0.08, 0.10, 0.12, 0.15, 0.18),
    "breakeven_trigger": (0.03, 0.05, 0.08, 0.10),
    "scale_out_frac": (0.3, 0.5, 0.7),
    "cost_zone_bars": (2.0, 3.0, 5.0, 8.0),
    "cost_zone_pct": (2.0, 3.0, 5.0),
    "qsx_exit_consec": (1.0, 2.0, 3.0),
    "time_stop_bars": (5.0, 10.0, 20.0, 40.0),
}

#: 家族 → 其参数名（顺序固定，台账/钉测对账用）。开关语义见 FAMILY_SWITCH。
FAMILIES: dict[str, tuple[str, ...]] = {
    "trail": ("trail_pct",),
    "breakeven": ("breakeven_trigger",),
    "scale_out": ("scale_out_frac",),
    "cost_zone": ("cost_zone_bars", "cost_zone_pct"),
    "qsx": ("qsx_exit_consec",),
    "time_stop": ("time_stop_bars",),
}

#: 家族 → 开关参数（=0 即整个家族关闭，simulate 的既有语义）。
FAMILY_SWITCH: dict[str, str] = {
    "trail": "trail_pct",
    "breakeven": "breakeven_trigger",
    "scale_out": "scale_out_frac",
    "cost_zone": "cost_zone_bars",
    "qsx": "qsx_exit_consec",
    "time_stop": "time_stop_bars",
}

#: cost_zone 关闭时 cost_zone_pct 的占位值（simulate 默认；bars=0 时该参无效）。
_COST_ZONE_PCT_OFF: float = 3.0

#: 基因组固定求值参数（不进基因组、逐位钉死；策展理由见模块 docstring）。
FIXED_PARAMS: dict[str, Any] = {
    "stop_mode": "pct",
}


def all_families() -> list[str]:
    """全部家族名（排序固定——台账/CTL 状态可复现）。"""
    return sorted(FAMILIES)


def normalize(g: dict[str, Any]) -> dict[str, Any]:
    """归一成**全键**基因组：stop_pct 恒在；关闭家族的参数归零/占位。

    输入允许省略关闭家族的键（如 ``{"stop_pct": 5, "trail_pct": 0.08}``）；
    输出键序固定（LEVELS 声明序），可直接 ``**`` 展开进 evaluate_trades。
    """
    out: dict[str, Any] = {"stop_pct": float(g["stop_pct"])}
    for fam in all_families():
        sw = FAMILY_SWITCH[fam]
        on = float(g.get(sw, 0) or 0) > 0
        for p in FAMILIES[fam]:
            if on:
                out[p] = float(g[p])
            else:
                out[p] = 0.0 if p != "cost_zone_pct" else _COST_ZONE_PCT_OFF
    return out


def validate(g: dict[str, Any]) -> list[str]:
    """返回非法项清单（空 = 合法）：缺参/档位外取值/开关与档位矛盾。

    契约是**返回清单**，对任何输入都不许抛——半开家族（如 cost_zone 有
    bars 缺 pct，唯一双参家族才暴露）先报缺参，否则 normalize 会 KeyError。
    """
    bad: list[str] = []
    if "stop_pct" not in g:
        return ["缺 stop_pct"]
    for fam in all_families():
        sw = FAMILY_SWITCH[fam]
        if float(g.get(sw, 0) or 0) > 0:
            for p in FAMILIES[fam]:
                if p not in g:
                    bad.append(f"{fam} 开启但缺参数 {p}")
    if bad:
        return bad
    n = normalize(g)
    for p, levels in LEVELS.items():
        v = n[p]
        if p == "stop_pct":
            if v not in levels:
                bad.append(f"stop_pct={v} 不在档位 {levels}")
            continue
        fam = _family_of(p)
        # cost_zone_pct 关闭时是占位值（不在档位也合法）；开启才校验档位
        if fam is not None and n[FAMILY_SWITCH[fam]] > 0 and v not in levels:
            bad.append(f"{p}={v} 不在档位 {levels}")
    return bad


def _family_of(param: str) -> Optional[str]:
    for fam, ps in FAMILIES.items():
        if param in ps:
            return fam
    return None


def families_on(g: dict[str, Any]) -> frozenset[str]:
    """基因组当前开启的家族集合。"""
    n = normalize(g)
    return frozenset(f for f in all_families() if n[FAMILY_SWITCH[f]] > 0)


def baseline_genome() -> dict[str, Any]:
    """基准档 pct5_trail08（R34/R36 终审同口径；C2 的参照点，必在空间内）。"""
    return normalize({"stop_pct": 5.0, "trail_pct": 0.08})


def genome_key(g: dict[str, Any]) -> str:
    """稳定紧凑键（批内去重/台账对账；归一化后生成，键序无关）。"""
    n = normalize(g)
    parts = [f"sp{n['stop_pct']:g}"]
    for fam in all_families():
        sw = FAMILY_SWITCH[fam]
        if n[sw] <= 0:
            parts.append(f"{fam}=off")
        else:
            vs = "x".join(f"{n[p]:g}" for p in FAMILIES[fam])
            parts.append(f"{fam}={vs}")
    return "|".join(parts)


def _sample_level(rng: Any, param: str) -> float:
    return float(rng.choice(LEVELS[param]))


def random_genome(
    rng: Any, open_families: Optional[list[str]] = None
) -> dict[str, Any]:
    """空间内均匀采样（随机臂同款分布；``open_families`` 外的家族恒关）。

    每个开放家族以 1/2 概率开启，开启后档位均匀；stop_pct 档位均匀。
    """
    fams = all_families() if open_families is None else sorted(open_families)
    g: dict[str, Any] = {"stop_pct": _sample_level(rng, "stop_pct")}
    for fam in fams:
        if rng.random() < 0.5:
            for p in FAMILIES[fam]:
                g[p] = _sample_level(rng, p)
    return normalize(g)


def _enabled_params(n: dict[str, Any]) -> list[str]:
    """当前开启的参数（stop_pct 恒开 + 开启家族的全部参数）。"""
    out = ["stop_pct"]
    for fam in all_families():
        if n[FAMILY_SWITCH[fam]] > 0:
            out.extend(FAMILIES[fam])
    return out


def _step_level(rng: Any, param: str, value: float) -> float:
    """档位扰动：相邻档 ±1（端点只能向内）。"""
    levels = LEVELS[param]
    i = levels.index(value)
    moves = [d for d in (-1, 1) if 0 <= i + d < len(levels)]
    return float(levels[i + rng.choice(moves)])


def _jump_level(rng: Any, param: str, value: float) -> float:
    """档位迁移：均匀跳到**不同**档（单档参数退化为保持不变）。"""
    levels = [x for x in LEVELS[param] if x != value]
    if not levels:
        return value
    return float(rng.choice(levels))


def mutate(
    g: dict[str, Any], rng: Any, open_families: Optional[list[str]] = None
) -> dict[str, Any]:
    """规则化变异（三算子等概率）：参数扰动 / 开关翻转 / 档位迁移。

    - 参数扰动：随机开启参数移相邻档；
    - 开关翻转：随机家族开↔关（开=随机档位）；只能**开启** open_families
      内的家族（CTL-2 关闭的家族不可复活），关闭任何家族都合法；
    - 档位迁移：随机开启参数跳到随机不同档。
    """
    n = normalize(g)
    fams = all_families() if open_families is None else sorted(open_families)
    op = rng.choice(("perturb", "toggle", "jump"))
    if op == "toggle":
        candidates = sorted(set(fams) | set(families_on(n)))
        fam = rng.choice(candidates)
        sw = FAMILY_SWITCH[fam]
        if n[sw] > 0:
            for p in FAMILIES[fam]:
                n[p] = 0.0 if p != "cost_zone_pct" else _COST_ZONE_PCT_OFF
        else:
            if fam not in fams:
                return mutate(n, rng, open_families)  # 关闭家族不可复活，重抽
            for p in FAMILIES[fam]:
                n[p] = _sample_level(rng, p)
        return normalize(n)
    params = _enabled_params(n)
    p = rng.choice(params)
    n[p] = _step_level(rng, p, n[p]) if op == "perturb" else _jump_level(rng, p, n[p])
    return normalize(n)


def perturb_50(g: dict[str, Any], rng: Any) -> dict[str, Any]:
    """C3 灵敏度臂：全部开启参数 ×U(0.5,1.5) 后吸附最近档（R36 ±50% 同族）。

    吸附回本档时向乘数方向挪一档（端点挪不动则留原档）——离散档位空间的
    ±50% 语义等价物，保证可动必动；关闭家族保持关闭。
    """
    n = normalize(g)
    for p in _enabled_params(n):
        levels = LEVELS[p]
        v = n[p]
        u = rng.uniform(0.5, 1.5)
        target = v * u
        snapped = min(levels, key=lambda x: (abs(x - target), x))
        if snapped == v:
            i = levels.index(v)
            j = i + (1 if u > 1.0 else -1)
            if 0 <= j < len(levels):
                snapped = levels[j]
        n[p] = float(snapped)
    return normalize(n)
