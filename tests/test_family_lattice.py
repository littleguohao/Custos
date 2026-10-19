"""R46 家族级配比工具（TODO #110）：家族划分、矩阵化等价、选格/邻格、簇级置换、端到端、零假设校准。"""

import json
import random

import numpy as np
import pandas as pd
import pytest

from custos.pipeline.screening import score_candidates as sc
from custos.research import family_lattice_study as fl
from custos.research import score_return_study as srs
from custos.research.score_calibration_study import CONTRIB_LEG_KEYS
from custos.research.score_evolution_study import _v0_mult_overrides

LEGS = list(CONTRIB_LEG_KEYS)


def test_families_partition_all_legs():
    flat = [leg for f in fl.FAMILY_NAMES for leg in fl.FAMILIES[f]]
    assert sorted(flat) == sorted(LEGS) and len(flat) == len(set(flat)) == 30
    assert len(fl.GRID) == 729 and fl.BASE in fl.GRID and len(fl.ONOFF) == 64


def _bars(seed, n=400):
    r = np.random.RandomState(seed)
    d = [x.strftime("%Y-%m-%d") for x in pd.bdate_range("2021-01-04", periods=n)]
    c = 10 * np.exp(np.cumsum(r.normal(0, 0.025, n)))
    o = c * (1 + r.normal(0, 0.01, n))
    return pd.DataFrame(
        {
            "date": d,
            "open": o,
            "high": np.maximum(o, c) * 1.02,
            "low": np.minimum(o, c) * 0.98,
            "close": c,
            "volume": r.uniform(1e6, 5e6, n),
            "amount": r.uniform(1e7, 5e7, n),
        }
    )


def test_matrix_scores_equal_technical_score():
    idx = _bars(99)
    fam_of = {leg: k for k, f in enumerate(fl.FAMILY_NAMES) for leg in fl.FAMILIES[f]}
    n = 0
    for s in range(3):
        df = _bars(s)
        for i in range(150, 400, 50):
            cand = srs.asof_candidate(df, idx, i, f"60000{s}")
            S = fl.family_subscores(cand, LEGS)
            for m in random.Random(s * 100 + i).sample(fl.GRID, 20) + [fl.BASE]:
                mult = [float(m[fam_of[leg]]) for leg in LEGS]
                assert sc.technical_score(cand, _v0_mult_overrides(mult, LEGS))[
                    0
                ] == float(fl.scores_of(S[None, :], m)[0])
                n += 1
    assert n >= 300
    # 全 1 = live 默认权重
    assert sc.technical_score(cand)[0] == float(fl.scores_of(S[None, :], fl.BASE)[0])


def test_neighbors():
    assert len(fl.neighbors(fl.BASE)) == 12
    assert len(fl.neighbors((0,) * 6)) == 6


def test_permute_clusters_blocks():
    items = [{"code": "000001", "i": i, "_seg": "A"} for i in (1, 2, 3, 20, 21, 40)]
    items += [{"code": "000002", "i": i, "_seg": "A"} for i in (5, 6, 7)]
    S = np.arange(len(items) * 6, dtype=float).reshape(len(items), 6)
    out = fl.permute_clusters(items, S, random.Random(1))
    assert sorted(map(tuple, out)) == sorted(map(tuple, S))  # 多重集不变
    # 长度 3 的两簇（000001 i1-3 / 000002 i5-7）整体互换或不换；长度 2、1 的簇唯一 ⇒ 不动
    assert np.array_equal(out[3:5], S[3:5]) and np.array_equal(out[5], S[5])
    blk = {tuple(map(tuple, out[0:3])), tuple(map(tuple, out[6:9]))}
    assert blk == {tuple(map(tuple, S[0:3])), tuple(map(tuple, S[6:9]))}


# ---------------------------------------------------------------------------
# 端到端（注入 collector；缩小网格控制耗时）
# ---------------------------------------------------------------------------


def _world(seed, n_days=60, per_day=25, edge=0.0):
    """候选 = 每日 per_day 只；家族子分随机；收益 = edge × S·w + 噪声（edge=0 ⇒ 零假设）。"""
    rng = np.random.RandomState(seed)
    w_true = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0])  # 信息只在被扫描的 F1
    out = {}
    for seg, y0 in (("A", 2010), ("B", 2023), ("J", 2018)):
        items = []
        days = pd.bdate_range(f"{y0}-01-04", periods=n_days)
        for di, d in enumerate(days):
            for k in range(per_day):
                S = rng.uniform(0, 12, 6)
                ret = edge * (S @ w_true - 6) / 100 + rng.normal(0, 0.05)
                ds = d.strftime("%Y-%m-%d")
                items.append(
                    {
                        "code": f"{k:06d}",
                        "i": di,
                        "S": S,
                        "trade": {
                            "code": f"{k:06d}",
                            "entry_date": ds,
                            "exit_date": (d + pd.Timedelta(days=3)).strftime(
                                "%Y-%m-%d"
                            ),
                            "ret": float(ret),
                            "reason": "stop",
                            "risk_frac": 0.05,
                            "r_multiple": float(ret) / 0.05,
                            "holding": 3,
                        },
                    }
                )
        out[seg] = items
    return out


@pytest.fixture
def small_grid(monkeypatch):
    names = fl.FAMILY_NAMES
    grid = [m for m in fl.GRID if all(x == 1 for x in m[2:])]  # 前两家族 3×3 = 9 格
    monkeypatch.setattr(fl, "GRID", grid)
    monkeypatch.setattr(fl, "ONOFF", [m for m in grid if all(x in (0, 1) for x in m)])
    # 用 items 里预置的 S 代替 family_subscores（矩阵化等价已由上面钉测）
    monkeypatch.setattr(fl, "family_subscores", lambda cand, legs: cand["S"])
    monkeypatch.setattr(
        fl,
        "neighbors",
        lambda m: [x for x in grid if sum(abs(a - b) for a, b in zip(x, m)) == 1],
    )
    return names


def _collector(world):
    spans = {"A": "2010", "B": "2023", "J": "2018"}

    def col(window):
        seg = next(
            k
            for k, y in spans.items()
            if window.start <= f"{y}-12-31" and window.end >= f"{y}-01-01"
        )
        return [
            {
                "trade": it["trade"],
                "cand": {"S": it["S"]},
                "code": it["code"],
                "i": it["i"],
            }
            for it in world[seg]
        ]

    return col


def _args(tmp_path, *extra):
    return [
        "--out",
        str(tmp_path / "r46.json"),
        "--n-random",
        "12",
        "--c4-min-pool",
        "8",
        "--tie-seeds",
        "3",
        "--top-n",
        "5",
        *extra,
    ]


def test_end_to_end_report(tmp_path, small_grid):
    rc = fl.main(_args(tmp_path), collector=_collector(_world(0, edge=3.0)))
    assert rc == 0
    rep = json.loads((tmp_path / "r46.json").read_text(encoding="utf-8"))
    assert rep["unit"] == "R46" and rep["n_cells"] == 9  # 缩小网格夹具
    c = rep["criteria"]
    assert set(c) == {"C1", "C2", "C3", "C4"} and rep["judgment_batch"] is None
    assert (
        c["C2"]["g_star"] is not None and c["C2"]["g_star"][0] == 2
    )  # 加重有信息的家族
    assert len(c["C4"]["pool"]) >= 1


def test_batch_judgment_read(tmp_path, small_grid, monkeypatch):
    from custos.research import judgment_batch as jb

    reg = tmp_path / "jb.jsonl"
    jb.open_batch("b1", [{"id": "r46", "config_hash": "t:x", "unit": "R46"}], path=reg)
    monkeypatch.setattr(jb, "REGISTRY", reg)
    rc = fl.main(
        _args(tmp_path, "--batch-id", "b1"), collector=_collector(_world(1, edge=3.0))
    )
    assert rc == 0
    jbk = json.loads((tmp_path / "r46.json").read_text(encoding="utf-8"))[
        "judgment_batch"
    ]
    assert jbk["line"]["m"] == 1 and jbk["verdict"] in {
        "candidate",
        "provisional",
        "falsified",
        "untested",
    }


def test_null_calibration(tmp_path, small_grid):
    """零假设（收益与子分独立）：C4 confirmed_pass 假过线率应 ≤ 名义附近。"""
    passes = 0
    trials = 16
    for t in range(trials):
        rc = fl.main(
            _args(tmp_path, "--seed", str(t)),
            collector=_collector(_world(100 + t, edge=0.0)),
        )
        assert rc == 0
        rep = json.loads((tmp_path / "r46.json").read_text(encoding="utf-8"))
        passes += rep["criteria"]["C4"]["state"] == "confirmed_pass"
    assert passes / trials <= 0.25, passes


# ---------------------------------------------------------------------------
# v0.416：主读数 = 每日 top-N 选中集（不过组合并发约束）
# ---------------------------------------------------------------------------


def _day_trades(n_days=3, per_day=8):
    out = []
    for d in range(n_days):
        for k in range(per_day):
            out.append(
                {
                    "code": f"{k:06d}",
                    "entry_date": f"2020-01-0{d + 1}",
                    "exit_date": "2020-01-09",  # 持有跨全期 ⇒ 并发约束必然咬住
                    "ret": 0.01 * (k - 3),
                    "risk_frac": 0.05,
                    "reason": "stop",
                    "score": float(k % 4),  # 有平分
                    "holding": 1,
                    "r_multiple": 0.2 * (k - 3),
                }
            )
    return out


def test_select_daily_topn_same_order_as_portfolio():
    from custos.research import backtest_factors as bt

    tr = _day_trades(n_days=1)
    taken: list = []
    bt.simulate_portfolio_topn(
        tr, top_n=5, max_concurrent=99, max_pos_frac=0.01, taken_out=taken, tie_seed=3
    )
    sel = bt.select_daily_topn(tr, 5, tie_seed=3)
    assert [t["code"] for t in sel] == [t["code"] for t in taken]


def test_reading_selected_not_bound_by_concurrency():
    import numpy as np

    tr = _day_trades()
    rd = fl._reading(tr, np.array([t["score"] for t in tr]), 5, 0, None)
    assert rd["n_selected"] == 15  # 3 日 × top5
    assert rd["n_taken"] <= 5  # V0 组合并发 ≤5、持有跨日
    assert rd["margin_portfolio"] is not None and "_sel" in rd


def test_c1_requires_selected_count(tmp_path, small_grid, monkeypatch):
    rc = fl.main(_args(tmp_path), collector=_collector(_world(0, edge=3.0)))
    assert rc == 0
    c1 = json.loads((tmp_path / "r46.json").read_text(encoding="utf-8"))["criteria"][
        "C1"
    ]
    assert min(c1["n_selected_base"].values()) < fl.MIN_N_SELECTED and c1["ok"] is False
    monkeypatch.setattr(fl, "MIN_N_SELECTED", 1)
    assert fl.main(_args(tmp_path), collector=_collector(_world(0, edge=3.0))) == 0
    c1 = json.loads((tmp_path / "r46.json").read_text(encoding="utf-8"))["criteria"][
        "C1"
    ]
    assert c1["ok"] is (min(c1["n_taken_base"].values()) >= fl.MIN_N_TAKEN)


def test_min_n_selected_anchored_to_power_line():
    from custos.research import power_mde as pm

    assert pm.mde(0.44, fl.MIN_N_SELECTED, 0.75, 5) <= 0.03
    assert pm.mde(0.44, fl.MIN_N_SELECTED - 100, 0.75, 5) > 0.03


def test_jobs_bitwise_equal_serial(tmp_path, small_grid):
    """v0.422：-j 2 与 -j 1 报告逐位一致（主网格分块 + 零假设臂按臂序消费）。"""
    outs = []
    for j in ("1", "2"):
        out = tmp_path / f"j{j}.json"
        argv = _args(tmp_path, "--jobs", j)
        argv[argv.index("--out") + 1] = str(out)
        assert fl.main(argv, collector=_collector(_world(3, edge=3.0))) == 0
        rep = json.loads(out.read_text(encoding="utf-8"))
        rep.pop("provenance")
        outs.append(rep)
    assert outs[0] == outs[1]
    assert outs[0]["criteria"]["C4"]["evaluated"] >= 1
