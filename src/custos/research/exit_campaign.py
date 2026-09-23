# -*- coding: utf-8 -*-
"""R37 出场轴进化战役 runner（战役壳）：批次进化 + CTL 确定性控制 + 台账记账。

预注册 = ``governance/research/R37_exit_axis_evolution_campaign.md``
（判据 C1~C5、战役控制 CTL-1~5 跑数前写死；本文件是它的代码化身，
数值与文档一一对应，改动任一侧 = 语义变更）。

- **内层批次**：top-k 幸存者 × 规则化变异（evolution.exit_genome）+ 随机臂
  随行（同空间采样，C4 难度标尺滚动记账）；
- **外层控制器（确定性，LLM 不参与）**：
  CTL-1 继续（批 top Δmargin 改善则同家族继续——默认动作）；
  CTL-2 转向（家族全部变体连续 2 批挖掘窗 Δmargin ≤ 0 ⇒ 关闭，台账记死刑）；
  CTL-3 证伪停（连续 3 批无候选过 C2，或全家族关闭 ⇒ 结局②归档）；
  CTL-4 过线停（候选过 C2+C3+C4 ⇒ candidate，C5 pre2019 单独终步）；
  CTL-5 预算帽（总基因组评估数 ≤ 预算，逐批记账防「跑到出正为止」）；
- **台账**：``{out_dir}/{tag}/campaign_ledger.json`` 每批原子重写
  （tmp+replace），被杀重启 ``--resume`` 从台账状态续跑（v0.258 教训）；
- **评估器协议注入**：``evaluate(params, *, start, end) -> readings | None``；
  生产默认 = V0 重放评估器（信号缓存重放 + V0 as-of 技术分 + topn 组合 +
  objective_of——与 score_evolution_study V0 臂同引擎同公式）；测试注入
  fake，控制器与台账全程不碰数据。

读数口径（R11 纪律）：margin/objective **只相对排序**（vs 基准档
pct5_trail08 的 Δmargin；vs 合并随机臂分布 95% 分位——v0.266 口径，
分位随样本收敛，替代随样本发散的累积最大值棘轮），绝对读数引用时
连带 R11 声明（报告 notes 已写死）。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.paths import write_json_atomic
from custos.research.evolution import exit_genome as eg

#: 评估器协议：出场参数（基因组，不含 FIXED 轴）× 窗口 → 读数 | None。
Evaluator = Callable[..., Optional[dict]]

#: V0 组合层参数（与 score_evolution_study._V0_PORTFOLIO 同值，钉测对账防漂移）。
V0_PORTFOLIO: dict[str, Any] = {
    "risk_pct": 0.01,
    "max_concurrent": 5,
    "max_pos_frac": 0.20,
}

#: 读数块固定键（与 V0 臂同形状；台账/报告对账用，测试钉住）。
READING_KEYS = (
    "objective",
    "margin",
    "expectancy_R",
    "payoff_ratio",
    "win_rate",
    "n",
    "n_taken",
    "n_candidates",
    "ret_over_dd",
)

_R11_NOTE = (
    "R11 纪律：margin/objective 只相对排序，绝对读数不可引用（基准边际崩塌在案）"
)


class LedgerSpaceChanged(ValueError):
    """台账幸存者基因组与当前档位空间不符（LEVELS 战役启动后变更仍 resume）。

    resume 只保证**同空间**续跑：幸存者会进变异算子（``_step_level`` 按
    档位索引取值），档位变更后不拦会在批次中途随机裸崩 ValueError
    （实测 40/200）——长跑战役几小时后才炸，比不跑还糟。fail-fast 于
    加载时，提示换 ``--tag`` 开新战役。
    """


@dataclass
class CampaignConfig:
    """战役配置（预注册 CTL 数值的代码化身；CLI 默认值 = R37 写死值）。"""

    mining_start: str = "2022-01-01"
    mining_end: str = "2024-07-31"
    judgment_start: str = "2024-08-01"
    judgment_end: str = "2026-09-04"
    batch_size: int = 16  # 每批进化臂基因组数
    n_random: int = 8  # 每批随机臂条数（C4 标尺）
    budget_cap: int = 500  # CTL-5 预算帽（总基因组评估数，进化+随机）
    family_death_streak: int = 2  # CTL-2：连死几批关闭家族
    falsify_streak: int = 3  # CTL-3：连续几批无 C2 证伪停
    n_survivors: int = 4  # CTL-1：幸存者数（变异母体）
    c3_draws: int = 4  # C3 灵敏度扰动臂数（±50%×4 零翻转，R36 同族）
    min_n_taken: int = 100  # C1：单窗最小选中笔数
    seed: int = 37  # 全局种子（逐批派生，resume 可复现）
    max_batches: int = 0  # 0=不限（冒烟/预算护栏用）

    def windows(self) -> dict[str, tuple[str, str]]:
        return {
            "mining": (self.mining_start, self.mining_end),
            "judgment": (self.judgment_start, self.judgment_end),
        }


@dataclass
class CampaignState:
    """战役状态（台账持久化=resume 来源；全字段 JSON 可序列化）。"""

    open_families: list[str] = field(default_factory=eg.all_families)
    family_dead_streak: dict[str, int] = field(default_factory=dict)
    family_deaths: list[str] = field(default_factory=list)
    consecutive_no_c2: int = 0
    total_genomes: int = 0
    random_pool: list[float] = field(
        default_factory=list
    )  # 合并随机臂分布（C4 分位标尺）
    population: list[dict] = field(default_factory=list)  # [{genome, mining}]
    baseline: dict[str, dict] = field(default_factory=dict)  # window -> readings
    best_candidate: Optional[dict] = None
    status: str = "running"  # running/falsified/candidate_found/budget_exhausted
    batch_id: int = 0


# ---------------------------------------------------------------------------
# 批次构建与评估
# ---------------------------------------------------------------------------


def _batch_rng(cfg: CampaignConfig, batch_id: int) -> random.Random:
    """逐批派生种子（resume 后同批同序列，可复现）。"""
    return random.Random(cfg.seed * 100003 + batch_id)


def build_batch(
    cfg: CampaignConfig, state: CampaignState, rng: random.Random
) -> tuple[list[dict], list[dict]]:
    """构建一批基因组：(进化臂, 随机臂)。

    进化臂：population 为空（首批/全灭后）→ 开放空间随机采样自举；否则
    幸存者轮流变异填满。批内 genome_key 去重（重试 8 次后放行——预算记账
    照算，重复现场留痕）。随机臂：同批开放空间采样（C4 标尺与搜索空间一致）。
    """
    evolve: list[dict] = []
    seen: set[str] = set()
    pop = [p["genome"] for p in state.population]
    tries = 0
    while len(evolve) < cfg.batch_size and tries < cfg.batch_size * 9:
        tries += 1
        if pop:
            parent = pop[(len(evolve) + tries) % len(pop)]
            g = eg.mutate(parent, rng, state.open_families)
        else:
            g = eg.random_genome(rng, state.open_families)
        k = eg.genome_key(g)
        if k in seen and tries % 8:
            continue
        seen.add(k)
        evolve.append(g)
    rand = [eg.random_genome(rng, state.open_families) for _ in range(cfg.n_random)]
    return evolve, rand


def _eval_windows(
    evaluator: Evaluator, params: dict, cfg: CampaignConfig
) -> dict[str, Optional[dict]]:
    """一个基因组 × 双窗读数（FIXED 轴在此并入）。"""
    full = {**eg.FIXED_PARAMS, **params}
    out: dict[str, Optional[dict]] = {}
    for wname, (start, end) in cfg.windows().items():
        try:
            out[wname] = evaluator(full, start=start, end=end)
        except Exception as exc:  # noqa: BLE001 — 单格失败不拖垮批次，留痕
            print(
                f"[WARN] 评估失败（{wname} {eg.genome_key(params)}）: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            out[wname] = None
    return out


def _d_margin(readings: Optional[dict], base: Optional[dict]) -> Optional[float]:
    """Δmargin = 基因组 margin − 基准 margin（缺任一读数 → None，不作数）。"""
    if not readings or not base:
        return None
    m, b = readings.get("margin"), base.get("margin")
    if m is None or b is None:
        return None
    return m - b


def _n_taken_ok(readings: Optional[dict], cfg: CampaignConfig) -> bool:
    return bool(readings) and (readings.get("n_taken") or 0) >= cfg.min_n_taken


def _q95(pool: list[float]) -> Optional[float]:
    """合并随机分布的 95% 分位（inclusive 线性插值）——C4 的标尺。

    **分位随样本数收敛**；它替代的「累积最大值」随样本数发散（棘轮——
    同一个好基因组的 C4 通过率取决于第几批被发现：8 抽样 21.3% → 264
    抽样 1.6%，owner review 实测）。预注册分位口径（v0.266 修订）下，
    零假设基因组各批恒 ~5% 假过线（真多重比较控制），真优势基因组各批
    恒真过线——与发现时机无关。len 1 退化为该点本身；空池 → None
    （n_random=0 时不拦，预注册战役 n_random≥1 总有臂）。
    """
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]
    return statistics.quantiles(pool, n=100, method="inclusive")[94]


def _c2_pass(
    dm_m: Optional[float], dm_j: Optional[float], r_m: dict, r_j: dict, cfg
) -> bool:
    """C2 晋级线：Δmargin 双窗同向为正 ∧ C1 双窗 n_taken ≥ 门槛。"""
    if dm_m is None or dm_j is None:
        return False
    return dm_m > 0 and dm_j > 0 and _n_taken_ok(r_m, cfg) and _n_taken_ok(r_j, cfg)


def _c3_check(
    genome: dict,
    base: dict[str, dict],
    cfg: CampaignConfig,
    evaluator: Evaluator,
    rng: random.Random,
) -> dict:
    """C3 灵敏度：±50%×c3_draws 双窗重估，**零翻转**才过。

    翻转 = 扰动臂 Δmargin 在任一窗 ≤ 0 或读数缺失（保守即严格，R36 同族）。
    """
    draws: list[dict] = []
    flips = 0
    for _ in range(cfg.c3_draws):
        pg = eg.perturb_50(genome, rng)
        rw = _eval_windows(evaluator, pg, cfg)
        dm_m = _d_margin(rw["mining"], base.get("mining"))
        dm_j = _d_margin(rw["judgment"], base.get("judgment"))
        flipped = dm_m is None or dm_j is None or dm_m <= 0 or dm_j <= 0
        flips += int(flipped)
        draws.append(
            {
                "genome": pg,
                "key": eg.genome_key(pg),
                "d_margin_mining": dm_m,
                "d_margin_judgment": dm_j,
                "flipped": flipped,
            }
        )
    return {"draws": draws, "flips": flips, "pass": flips == 0}


# ---------------------------------------------------------------------------
# CTL 控制器（确定性状态机；预注册 CTL-1~5 的代码化身）
# ---------------------------------------------------------------------------


def ctl_step(
    cfg: CampaignConfig,
    state: CampaignState,
    evolve: list[dict],
    rand: list[dict],
    rw_evolve: list[dict[str, Optional[dict]]],
    rw_rand: list[Optional[dict]],
    evaluator: Evaluator,
    rng: random.Random,
) -> dict:
    """执行一批的 CTL 裁决：更新 state 并返回台账批记录。

    检查次序（写死，与 R37 同步）：CTL-4 过线停 > CTL-2 家族关闭 >
    CTL-3 证伪停 > CTL-5 预算帽 > CTL-1 继续（更新幸存者）。
    **次序即语义**：CTL-3 含「或全部家族关闭」，必须 CTL-2 先跑，家族
    团灭的一批才能同批触发证伪（而不是白跑一批空家族）。
    家族连死记账在任何结局下都落台账（死刑档案不断更）。
    """
    base = state.baseline
    rows: list[dict] = []
    for g, rw in zip(evolve, rw_evolve):
        dm_m = _d_margin(rw["mining"], base.get("mining"))
        dm_j = _d_margin(rw["judgment"], base.get("judgment"))
        rows.append(
            {
                "genome": g,
                "key": eg.genome_key(g),
                "families": sorted(eg.families_on(g)),
                "mining": rw["mining"],
                "judgment": rw["judgment"],
                "d_margin_mining": dm_m,
                "d_margin_judgment": dm_j,
                "c2": _c2_pass(
                    dm_m, dm_j, rw["mining"] or {}, rw["judgment"] or {}, cfg
                ),
            }
        )
    rand_rows = [
        {"genome": g, "key": eg.genome_key(g), "mining": rw}
        for g, rw in zip(rand, rw_rand)
    ]
    n_evals = len(evolve) + len(rand)
    state.total_genomes += n_evals

    # 随机臂读数并入合并分布（含本批——先更新再判 C4，从严；分位标尺见 _q95）
    batch_rand_best: Optional[float] = None
    for r in rand_rows:
        obj = (r["mining"] or {}).get("objective")
        if obj is None:
            continue
        batch_rand_best = obj if batch_rand_best is None else max(batch_rand_best, obj)
        state.random_pool.append(obj)

    actions: list[dict] = []
    c2_passers = [r for r in rows if r["c2"]]

    # ── CTL-4 过线停：C2 过线者按挖掘窗 objective 逐个过 C3+C4 ──
    c3_record: Optional[dict] = None
    c4_bar = _q95(state.random_pool)
    passers = sorted(
        (r for r in c2_passers if (r["mining"] or {}).get("objective") is not None),
        key=lambda r: r["mining"]["objective"],
        reverse=True,
    )
    for r in passers:
        c3 = _c3_check(r["genome"], base, cfg, evaluator, rng)
        c3_record = {"candidate_key": r["key"], **c3}
        c4_ok = c4_bar is None or r["mining"]["objective"] > c4_bar
        if c3["pass"] and c4_ok:
            state.status = "candidate_found"
            state.best_candidate = {
                "genome": r["genome"],
                "key": r["key"],
                "mining": r["mining"],
                "judgment": r["judgment"],
                "d_margin_mining": r["d_margin_mining"],
                "d_margin_judgment": r["d_margin_judgment"],
                "c3": c3_record,
                "c4_bar": c4_bar,
                "random_pool_size": len(state.random_pool),
                "note": "C2+C3+C4 全过；C5 pre2019 终审单独终步（一票否决）",
            }
            actions.append({"type": "candidate_found", "key": r["key"]})
            break
        actions.append(
            {
                "type": "near_miss",
                "key": r["key"],
                "why": "c3_flipped" if not c3["pass"] else "c4_below_random_q95",
            }
        )

    # ── C2 挂零计数（CTL-3 的输入）──
    state.consecutive_no_c2 = 0 if c2_passers else state.consecutive_no_c2 + 1

    # ── CTL-2 转向：家族全部变体连续 N 批挖掘窗 Δmargin ≤ 0 ⇒ 关闭 ──
    newly_dead: list[str] = []
    for fam in list(state.open_families):
        variants = [
            r for r in rows if fam in r["families"] and r["d_margin_mining"] is not None
        ]
        if not variants:
            continue  # 本批无该家族变体读数：无证据，连死计数不变
        if all(r["d_margin_mining"] <= 0 for r in variants):
            streak = state.family_dead_streak.get(fam, 0) + 1
        else:
            streak = 0
        state.family_dead_streak[fam] = streak
        if streak >= cfg.family_death_streak:
            state.open_families.remove(fam)
            state.family_deaths.append(fam)
            newly_dead.append(fam)
            actions.append({"type": "family_closed", "family": fam, "streak": streak})

    # ── CTL-3 证伪停 ──
    if state.status == "running" and (
        state.consecutive_no_c2 >= cfg.falsify_streak or not state.open_families
    ):
        state.status = "falsified"
        actions.append(
            {
                "type": "falsified",
                "consecutive_no_c2": state.consecutive_no_c2,
                "open_families": list(state.open_families),
            }
        )

    # ── CTL-5 预算帽 ──
    if state.status == "running" and state.total_genomes >= cfg.budget_cap:
        state.status = "budget_exhausted"
        actions.append(
            {
                "type": "budget_exhausted",
                "total_genomes": state.total_genomes,
                "budget_cap": cfg.budget_cap,
            }
        )

    # ── CTL-1 继续：更新幸存者（top-k 挖掘窗 objective）──
    scored = [
        r
        for r in rows
        if (r["mining"] or {}).get("objective") is not None
        and (r["mining"] or {}).get("margin") is not None
    ]
    scored.sort(key=lambda r: r["mining"]["objective"], reverse=True)
    if state.status == "running":
        state.population = [
            {"genome": r["genome"], "mining": r["mining"]}
            for r in scored[: cfg.n_survivors]
        ]
        if not actions:
            actions.append({"type": "continue"})

    top = passers[0] if passers else (scored[0] if scored else None)
    return {
        "batch_id": state.batch_id,
        "open_families": list(state.open_families),
        "n_evals": n_evals,
        "evolve": rows,
        "random_arm": rand_rows,
        "random_best": batch_rand_best,
        "random_q95": c4_bar,
        "random_pool_size": len(state.random_pool),
        "c2_pass_keys": [r["key"] for r in c2_passers],
        "c3": c3_record,
        "top_key": (top or {}).get("key"),
        "ctl_actions": actions,
        "family_deaths": list(state.family_deaths),
        "consecutive_no_c2": state.consecutive_no_c2,
        "total_genomes": state.total_genomes,
    }


# ---------------------------------------------------------------------------
# 战役主循环 + 台账
# ---------------------------------------------------------------------------

LEDGER_SCHEMA = "exit_campaign_ledger/v2"  # v2：random_ceiling 棘轮 → random_pool 合并分布 95% 分位（v0.266）


def ledger_path_for(out_dir: Path, tag: str) -> Path:
    return out_dir / tag / "campaign_ledger.json"


def save_ledger(
    path: Path, tag: str, cfg: CampaignConfig, state: CampaignState, batches: list[dict]
) -> None:
    """整本原子重写（tmp+replace；每批一次，被杀只丢当批——v0.258 教训）。"""
    write_json_atomic(
        path,
        {
            "schema": LEDGER_SCHEMA,
            "campaign": tag,
            "config": asdict(cfg),
            "state": asdict(state),
            "batches": batches,
        },
    )


def load_ledger(path: Path) -> tuple[CampaignConfig, CampaignState, list[dict], str]:
    """读台账 → (config, state, batches, tag)；schema 不符即报错（不猜）。"""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != LEDGER_SCHEMA:
        raise ValueError(
            f"台账 schema 不符: {doc.get('schema')!r}（期望 {LEDGER_SCHEMA}）"
        )
    cfg = CampaignConfig(**doc["config"])
    st = doc["state"]
    state = CampaignState(
        open_families=list(st["open_families"]),
        family_dead_streak=dict(st["family_dead_streak"]),
        family_deaths=list(st["family_deaths"]),
        consecutive_no_c2=st["consecutive_no_c2"],
        total_genomes=st["total_genomes"],
        random_pool=list(st["random_pool"]),
        population=list(st["population"]),
        baseline=dict(st["baseline"]),
        best_candidate=st["best_candidate"],
        status=st["status"],
        batch_id=st["batch_id"],
    )
    return cfg, state, list(doc["batches"]), doc["campaign"]


def _ensure_baseline(
    cfg: CampaignConfig, state: CampaignState, evaluator: Evaluator
) -> None:
    """基准档 pct5_trail08 双窗读数（战役一次；resume 已有则跳过）。"""
    if all(w in state.baseline for w in cfg.windows()):
        return
    rw = _eval_windows(evaluator, eg.baseline_genome(), cfg)
    missing = [w for w, r in rw.items() if r is None]
    if missing:
        raise RuntimeError(
            f"基准档评估失败（{missing}）——战役无法建立参照系，检查数据/宇宙"
        )
    state.baseline = {w: r for w, r in rw.items() if r is not None}


def run_campaign(
    cfg: CampaignConfig,
    evaluator: Evaluator,
    ledger_path: Path,
    *,
    resume: bool = False,
    tag: str = "",
) -> dict:
    """战役主循环：批次 → CTL → 台账，直到 CTL 给出结局（或 max_batches 冒烟停）。

    返回最终报告 dict（同时落 ``_exit_campaign__{tag}.json`` 于台账同目录）。
    ``max_batches > 0`` 是冒烟护栏：跑满即停，status 仍 running，不算结局。
    """
    batches: list[dict] = []
    state = CampaignState()
    if resume and ledger_path.exists():
        cfg_l, state, batches, tag_l = load_ledger(ledger_path)
        if cfg_l != cfg:
            print(
                f"[WARN] --resume：CLI 配置与台账不一致，以**台账**为准（tag={tag_l}）",
                file=sys.stderr,
            )
        cfg = cfg_l
        tag = tag or tag_l
        if state.status != "running":
            print(
                f"[INFO] 台账已有结局（{state.status}），直接出报告不续跑",
                file=sys.stderr,
            )
        # 档位空间守卫：幸存者要进变异算子，LEVELS 变更后续跑会在批次中途
        # 随机裸崩（LedgerSpaceChanged docstring 有实测）——加载时 fail-fast。
        stale = {
            eg.genome_key(p["genome"]): bad
            for p in state.population
            if (bad := eg.validate(p["genome"]))
        }
        if stale:
            raise LedgerSpaceChanged(
                "台账幸存者基因组与当前档位空间不符——exit_genome.LEVELS 已在"
                "战役启动后变更，resume 只保证同空间续跑；请换 --tag 开新战役。"
                f"非法项: {stale}"
            )
    _ensure_baseline(cfg, state, evaluator)

    while state.status == "running":
        if cfg.max_batches and state.batch_id >= cfg.max_batches:
            break
        state.batch_id += 1
        rng = _batch_rng(cfg, state.batch_id)
        evolve, rand = build_batch(cfg, state, rng)
        rw_evolve: list[dict[str, Optional[dict]]] = []
        for i, g in enumerate(evolve, 1):
            print(
                f"[campaign] 批 {state.batch_id} 进化臂 {i}/{len(evolve)} "
                f"{eg.genome_key(g)}",
                file=sys.stderr,
            )
            rw_evolve.append(_eval_windows(evaluator, g, cfg))
        rw_rand: list[Optional[dict]] = []
        for i, g in enumerate(rand, 1):
            print(
                f"[campaign] 批 {state.batch_id} 随机臂 {i}/{len(rand)} "
                f"{eg.genome_key(g)}",
                file=sys.stderr,
            )
            full = {**eg.FIXED_PARAMS, **g}
            try:
                rw_rand.append(
                    evaluator(full, start=cfg.mining_start, end=cfg.mining_end)
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[WARN] 随机臂评估失败 {eg.genome_key(g)}: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                rw_rand.append(None)
        record = ctl_step(cfg, state, evolve, rand, rw_evolve, rw_rand, evaluator, rng)
        batches.append(record)
        save_ledger(ledger_path, tag, cfg, state, batches)
        print(
            f"[campaign] 批 {state.batch_id} 毕：top={record['top_key']} "
            f"C2过={len(record['c2_pass_keys'])} 开放家族={len(state.open_families)} "
            f"连无C2={state.consecutive_no_c2} 累计={state.total_genomes}/{cfg.budget_cap} "
            f"随机q95={_q95(state.random_pool)}(池{len(state.random_pool)}) "
            f"→ {record['ctl_actions'][-1]['type']}",
            file=sys.stderr,
        )

    report = {
        "schema": "exit_campaign_report/v1",
        "campaign": tag,
        "status": state.status,
        "verdict": {
            "running": "🔄 冒烟/暂停（max_batches 护栏），非结局",
            "falsified": "❌ 结局②：出场参数路线证伪收口（CTL-3）",
            "candidate_found": "✅ 候选过线（CTL-4）——C5 pre2019 终审单独终步",
            "budget_exhausted": "❌ 预算帽耗尽无候选（CTL-5）——按证伪读",
        }[state.status],
        "config": asdict(cfg),
        "baseline": state.baseline,
        "best_candidate": state.best_candidate,
        "family_deaths": state.family_deaths,
        "total_genomes": state.total_genomes,
        "n_batches": len(batches),
        "random_q95": _q95(state.random_pool),
        "random_pool_size": len(state.random_pool),
        "ledger": str(ledger_path),
        "notes": [_R11_NOTE],
    }
    write_json_atomic(ledger_path.parent / f"_exit_campaign__{tag}.json", report)
    return report


# ---------------------------------------------------------------------------
# 生产评估器：V0 重放（信号缓存 + V0 as-of 技术分 + topn + objective_of）
# ---------------------------------------------------------------------------


def make_v0_replay_evaluator(args: Any, ap: argparse.ArgumentParser) -> Evaluator:
    """构造 V0 重放评估器（生产机专用；测试注入 fake）。

    与 score_evolution_study V0 对照臂**同引擎同公式**，仅把出场档从固定
    exit_spec 换成基因组参数，并用信号缓存重放摊薄成本（进场信号与 V0
    分数只算一次——二者都与出场参数无关）：

      ① 预热（每窗一次）：逐股 ``_load_one_bars`` →
         ``evaluate_trades(signals_out=…)``（baseline 恒可买 + j_low gate +
         0AMV 做多区间）扫进场信号 → 逐信号 ``asof_technical_score`` 算
         V0 as-of 技术分；
      ② 评估（每基因组）：逐股 ``evaluate_trades(signals_in=…)`` 重放出场
         → 改写 V0 分 → ``summarize_trades`` + ``simulate_portfolio_topn``
         → ``objective_of(DEFAULT_OBJ_WEIGHTS)``（读数块与 V0 臂同形状）。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    ns = argparse.Namespace(
        codes_file=args.codes_file,
        universe_local=args.universe_local,
        universe_sample=args.universe_sample,
        seed=args.universe_seed,
        codes=args.codes,
    )
    codes = bt._resolve_universe(ns, ap)
    starts = [args.mining_start, args.judgment_start]
    regime = bt.load_amv_regime(since=min(starts))
    if not regime:
        ap.error("0AMV regime 读不到（compass_amv）——本机无数据？")

    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    index_df = (
        local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )

    cache: dict[tuple[str, str], dict[str, dict]] = {}

    def _warm(start: str, end: str) -> dict[str, dict]:
        key = (start, end)
        if key in cache:
            return cache[key]
        per_code: dict[str, dict] = {}
        for i, code in enumerate(codes, 1):
            if i % 200 == 0:
                print(f"[warmup] {start}~{end} {i}/{len(codes)}", file=sys.stderr)
            df = bt._load_one_bars(code, args.count, start, end)
            if df is None or not len(df):
                continue
            sigs: list[dict] = []
            bt.evaluate_trades(
                {code: df},
                scorer=bt.SCORERS["baseline"],
                entry_gate=bt.j_low_gate,
                amv_regime=regime,
                cost_bps=args.cost_bps,
                collect_all=True,
                signals_out=sigs,
            )
            if not sigs:
                continue
            scores: dict[str, float] = {}
            for s in sigs:
                try:
                    score, _level, _contrib = srs.asof_technical_score(
                        df, index_df, s["i"], code
                    )
                except Exception:  # noqa: BLE001 — 单信号评分失败丢该信号
                    continue
                scores[s["date"]] = score
            per_code[code] = {"df": df, "signals": sigs, "scores": scores}
        cache[key] = per_code
        return per_code

    def evaluate(params: dict, *, start: str, end: str) -> Optional[dict]:
        per_code = _warm(start, end)
        cands: list[dict] = []
        for code, pack in per_code.items():
            trades = bt.evaluate_trades(
                {code: pack["df"]},
                signals_in={code: pack["signals"]},
                amv_regime=regime,
                cost_bps=args.cost_bps,
                collect_all=True,
                **params,
            )
            for tr in trades:
                score = pack["scores"].get(tr["entry_date"])
                if score is None:
                    continue
                cands.append({**tr, "score": score})
        if not cands:
            return None
        tsum = bt.summarize_trades(cands)
        pf = bt.simulate_portfolio_topn(cands, top_n=args.top_n, **V0_PORTFOLIO)
        margin = sg._margin(
            {"win": tsum.get("win_rate"), "payoff": tsum.get("payoff_ratio")}
        )
        ret_dd = sg._ret_over_dd(pf)
        row = {
            "margin": margin,
            "expectancy_R": tsum.get("expectancy_R"),
            "ret_over_dd": ret_dd,
        }
        return {
            "objective": sg.objective_of(row, sg.DEFAULT_OBJ_WEIGHTS),
            "margin": margin,
            "expectancy_R": tsum.get("expectancy_R"),
            "payoff_ratio": tsum.get("payoff_ratio"),
            "win_rate": tsum.get("win_rate"),
            "n": tsum.get("n"),
            "n_taken": pf.get("n_taken"),
            "n_candidates": len(cands),
            "ret_over_dd": ret_dd,
        }

    return evaluate


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R37 出场轴进化战役（战役壳）：批次进化 + CTL-1~5 确定性控制 + 台账记账"
    )
    ap.add_argument("--tag", required=True, help="战役标签（产物目录名）")
    # ---- 宇宙 ----
    ap.add_argument("--codes", default="", help="逗号分隔代码（宇宙解析的兜底分支）")
    ap.add_argument("--codes-file", default="", help="从文件读代码（钉死宇宙，优先）")
    ap.add_argument(
        "--universe-sample",
        type=int,
        default=0,
        help="全市场抽样 N 只（默认 0=不抽；与 --codes/--codes-file 三选一）",
    )
    ap.add_argument(
        "--universe-local",
        action="store_true",
        help="用本地 vipdoc 代码清单作抽样母体（同 backtest_factors）",
    )
    ap.add_argument("--universe-seed", type=int, default=42, help="宇宙抽样种子")
    # ---- 评估口径（V0 臂同值）----
    ap.add_argument("--top-n", type=int, default=20, help="横截面择优（默认 20）")
    ap.add_argument("--count", type=int, default=2000, help="每股回溯 K 线根数")
    ap.add_argument("--cost-bps", type=float, default=25.0, help="往返成本基点")
    # ---- 窗口（R37 写死默认）----
    ap.add_argument("--mining-start", default="2022-01-01", help="挖掘窗起点")
    ap.add_argument("--mining-end", default="2024-07-31", help="挖掘窗终点")
    ap.add_argument("--judgment-start", default="2024-08-01", help="判定窗起点")
    ap.add_argument("--judgment-end", default="2026-09-04", help="判定窗终点")
    # ---- CTL 数值（R37 写死默认；跑数前可经 flag 修订一次，启动后锁死）----
    ap.add_argument("--batch-size", type=int, default=16, help="每批进化臂基因组数")
    ap.add_argument("--n-random", type=int, default=8, help="每批随机臂条数")
    ap.add_argument("--budget", type=int, default=500, help="CTL-5 预算帽")
    ap.add_argument("--survivors", type=int, default=4, help="CTL-1 幸存者数")
    ap.add_argument("--seed", type=int, default=37, help="全局种子")
    ap.add_argument("--c3-draws", type=int, default=4, help="C3 扰动臂数")
    ap.add_argument("--min-n-taken", type=int, default=100, help="C1 单窗最小选中笔数")
    ap.add_argument("--family-death-streak", type=int, default=2, help="CTL-2 连死批数")
    ap.add_argument("--falsify-streak", type=int, default=3, help="CTL-3 连无 C2 批数")
    ap.add_argument(
        "--max-batches", type=int, default=0, help="冒烟护栏：跑满即停（0=不限）"
    )
    # ---- 运行 ----
    ap.add_argument(
        "--out-dir",
        default="artifacts/logs/exit_campaign",
        help="产物根目录（默认 artifacts/logs/exit_campaign）",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="从台账状态续跑（配置以台账为准；v0.258 增量落盘教训）",
    )
    return ap


def _config_of(args: Any) -> CampaignConfig:
    return CampaignConfig(
        mining_start=args.mining_start,
        mining_end=args.mining_end,
        judgment_start=args.judgment_start,
        judgment_end=args.judgment_end,
        batch_size=args.batch_size,
        n_random=args.n_random,
        budget_cap=args.budget,
        family_death_streak=args.family_death_streak,
        falsify_streak=args.falsify_streak,
        n_survivors=args.survivors,
        c3_draws=args.c3_draws,
        min_n_taken=args.min_n_taken,
        seed=args.seed,
        max_batches=args.max_batches,
    )


def main(
    argv: Optional[list[str]] = None, *, evaluator: Optional[Evaluator] = None
) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    if args.batch_size < 1 or args.n_random < 0:
        ap.error("--batch-size ≥1 且 --n-random ≥0")
    if args.budget < 1:
        ap.error("--budget ≥1")
    ev = evaluator if evaluator is not None else make_v0_replay_evaluator(args, ap)
    ledger = ledger_path_for(Path(args.out_dir), args.tag)
    if not args.resume and ledger.exists():
        ap.error(f"台账已存在: {ledger}（续跑加 --resume；新战役换 --tag）")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = run_campaign(
            _config_of(args), ev, ledger, resume=args.resume, tag=args.tag
        )
    except LedgerSpaceChanged as exc:
        ap.error(str(exc))  # 档位空间守卫：干净 exit 2，不留 traceback
    print(
        f"[campaign] 结局：{report['status']} —— {report['verdict']}\n"
        f"[campaign] 报告：{ledger.parent / f'_exit_campaign__{args.tag}.json'}\n"
        f"[campaign] 台账：{ledger}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
