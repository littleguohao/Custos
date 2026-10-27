"""回填协议（result_digest，v0.413）：①每个 kind 的字段清单在真实产物上零缺失；②写入/回填往返；
③**守卫**：仓库里每个回填块都必须能由 results/ JSON 逐字重渲染（手改数字即红），每份 results JSON
都必须被某个回填块引用。"""

import json
import re

import numpy as np
import pandas as pd
import pytest

import helpers_null as hn
from custos.core.paths import RESEARCH_DIR
from custos.research import result_digest as rd


def _dump(tmp_path, name, rep):
    p = tmp_path / f"{name}.json"
    p.write_text(
        json.dumps(rep, ensure_ascii=False, default=str, allow_nan=True),
        encoding="utf-8",
    )
    return p


def _assert_complete(kind, product):
    d = rd.build_digest(kind, product, "t")
    assert d["missing"] == [], (kind, d["missing"])
    assert d["fields"], kind
    return d


# ---------------------------------------------------------------------------
# ① 字段清单 × 真实产物（各终端合成夹具）
# ---------------------------------------------------------------------------


def test_bear_r40_and_r45(tmp_path):
    import test_null_calibration as tn

    _assert_complete("r40", _dump(tmp_path, "r40", tn._bear_run(hn.null_replay)(0)))
    _assert_complete("r45", _dump(tmp_path, "r45", tn._r45_run(hn.null_replay)(0)))


def test_r39(tmp_path, monkeypatch):
    import test_factor_exit_study as fxt

    rep = fxt._run(
        monkeypatch,
        fxt._three_level_spec(180),
        hn.null_replay,
        n_random=5,
        c4_min_pool=3,
    )
    _assert_complete("r39", _dump(tmp_path, "r39", rep))


def test_r44_scan_and_judgment(tmp_path):
    import test_plan_rules_replay as tp

    _assert_complete("r44_scan", _dump(tmp_path, "scan", tp._scan_run(0, False)))
    rep = tp._run(
        tp._mk_per_code(),
        tp._replay_candidate,
        stop_anchor="signal_low",
        stop_buffer_pct=8.0,
    )
    _assert_complete("r44_judgment", _dump(tmp_path, "pj", rep))


def test_r43_mining_and_judgment(tmp_path):
    import test_regime_style_study as trs

    _assert_complete("r43", _dump(tmp_path, "r43", trs._calib_run(0, False)))
    from custos.research import regime_style_study as rs

    regime, frames = trs._judgment_fixture()
    out = tmp_path / "r43j.json"
    rc = rs.main(
        [
            "--out",
            str(out),
            "--judgment-read",
            "bear:price:+",
            "--min-segs",
            "3",
            "--min-stocks",
            "6",
            "--min-group",
            "1",
            "--n-random",
            "8",
        ],
        **trs._jinj(regime, frames),
    )
    assert rc == 0
    _assert_complete("r43_judgment", out)


def test_r46(tmp_path, monkeypatch):
    import test_family_lattice as tfl
    from custos.research import family_lattice_study as fl

    grid = [m for m in fl.GRID if all(x == 1 for x in m[2:])]
    monkeypatch.setattr(fl, "GRID", grid)
    monkeypatch.setattr(fl, "ONOFF", [m for m in grid if all(x in (0, 1) for x in m)])
    monkeypatch.setattr(fl, "family_subscores", lambda cand, legs: cand["S"])
    monkeypatch.setattr(
        fl,
        "neighbors",
        lambda m: [x for x in grid if sum(abs(a - b) for a, b in zip(x, m)) == 1],
    )
    out = tmp_path / "r46.json"
    assert (
        fl.main(
            [
                "--out",
                str(out),
                "--n-random",
                "6",
                "--c4-min-pool",
                "3",
                "--tie-seeds",
                "2",
                "--top-n",
                "5",
            ],
            collector=tfl._collector(tfl._world(0, edge=3.0)),
        )
        == 0
    )
    _assert_complete("r46", out)


def test_r36_frozen(tmp_path):
    import test_score_evolution_study as tse
    from custos.research import score_evolution_study as ses

    t = tse.TestFrozenRead()
    frozen = t._frozen_report(tmp_path)
    assert (
        ses.main(
            t._argv(tmp_path, frozen),
            cell_runner=tse.FakeRunner(default=tse._reading(0.2)),
            v0l_collector=tse.SpyCollector(
                {tse.JUDGMENT: tse._v0l_collected_judgment()}
            ),
        )
        == 0
    )
    _assert_complete("r36_frozen", tmp_path / "fz" / "_score_evolution__fz.json")


def test_baseline(tmp_path):
    from custos.research import backtest_factors as bt

    rng = np.random.RandomState(0)
    n = 400
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2023-01-02", periods=n)]
    c = 10 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))
    df = pd.DataFrame(
        {
            "date": dates,
            "open": c,
            "high": c * 1.02,
            "low": c * 0.98,
            "close": c,
            "volume": rng.uniform(1e6, 2e6, n),
            "amount": rng.uniform(1e7, 2e7, n),
        }
    )
    out = tmp_path / "bl.json"
    rc = bt.main(
        [
            "--trade-sim",
            "--codes",
            "S000",
            "--entry-filter",
            "j_low",
            "--scorer",
            "baseline",
            "--count",
            "0",
            "--portfolio",
            "--out",
            str(out),
        ],
        loader=lambda codes, count: {k: df for k in codes},
    )
    assert rc == 0
    _assert_complete("baseline", out)


from test_data_coverage_probe import _write_ledger, vipdoc_root  # noqa: E402,F401  fixture 复用


def test_probe(vipdoc_root, tmp_path):  # noqa: F811
    from custos.research import data_coverage_probe as dcp

    ledger = tmp_path / "ledger.jsonl"
    _write_ledger(ledger)
    out = tmp_path / "probe.json"
    assert (
        dcp.main(
            [
                "--tdx-root",
                str(vipdoc_root),
                "--amv-ledger",
                str(ledger),
                "--out",
                str(out),
                "--sector-index-dir",
                str(tmp_path / "none"),
            ]
        )
        == 0
    )
    _assert_complete("probe", out)


# ---------------------------------------------------------------------------
# ② 往返
# ---------------------------------------------------------------------------


def test_write_backfill_roundtrip(tmp_path, monkeypatch):
    import test_null_calibration as tn

    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    doc = tmp_path / "R40_x.md"
    doc.write_text("# R40\n\n### 回填区（逐 Phase 填）\n\n- 旧内容\n", encoding="utf-8")
    prod = _dump(tmp_path, "r40", tn._bear_run(hn.null_replay)(0))
    assert (
        rd.main(["r40", str(prod), "--tag", "r40_v2_pre", "--write", "--backfill"]) == 0
    )
    saved = json.loads(
        (tmp_path / "results/R40/r40_v2_pre.json").read_text(encoding="utf-8")
    )
    assert rd.render_md(saved) in doc.read_text(encoding="utf-8")
    assert rd.main(["r40", str(prod), "--tag", "r40_v2_pre", "--write"]) == 2  # 不覆盖
    bad = _dump(tmp_path, "bad", {"verdict": "x"})
    assert rd.main(["r40", str(bad), "--tag", "b", "--write"]) == 2  # 缺必需字段拒写


# ---------------------------------------------------------------------------
# ③ 仓库守卫：回填块 ⇔ results JSON 逐字一致
# ---------------------------------------------------------------------------

_BLOCK = re.compile(r"<!-- digest:(results/[^ ]+\.json) -->.*?<!-- /digest -->", re.S)


def _docs():
    return sorted(RESEARCH_DIR.glob("*.md")) + sorted(
        (RESEARCH_DIR / "results").glob("*.md")
    )


def test_repo_blocks_match_results():
    seen = set()
    for doc in _docs():
        for m in _BLOCK.finditer(doc.read_text(encoding="utf-8")):
            rel = m.group(1)
            p = RESEARCH_DIR / rel
            assert p.exists(), f"{doc.name} 回填块引用的 {rel} 不存在"
            saved = json.loads(p.read_text(encoding="utf-8"))
            assert m.group(0) == rd.render_md(saved), (
                f"{doc.name} 的 {rel} 回填块与摘要 JSON 不一致（手改？）"
            )
            seen.add(rel)
    for p in sorted((RESEARCH_DIR / "results").rglob("*.json")):
        rel = p.relative_to(RESEARCH_DIR).as_posix()
        assert rel in seen, f"{rel} 未被任何回填块引用"


def test_r47_scan(tmp_path):
    import test_protect_scan as tps

    _assert_complete("r47_scan", _dump(tmp_path, "r47", tps._run(0)))


def test_r50(tmp_path):
    import test_swing_recall_study as tsr

    rep = tsr._world_run(0, min_n=50, n_random=6, c4_min_pool=3)
    d = _assert_complete("r50", _dump(tmp_path, "r50", rep))
    # 判据量（v0.458）：逐窗 lift_low / baseline_low 必填在案（objects.*.windows.* 全覆盖）
    low_keys = [k for k in d["fields"] if k.endswith(".lift_low")]
    assert len(low_keys) == len(rep["objects"]) * 2
    assert any(k.endswith(".baseline_low") for k in d["fields"])
