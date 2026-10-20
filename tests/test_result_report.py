"""result_report（v0.427）：明细 kind 在真实产物上零缺失、报告可渲染；仓库内报告 = 摘要的纯函数（防手改）。"""

import json

from custos.core.paths import RESEARCH_DIR
from custos.research import result_digest as rd
from custos.research import result_report as rr


def _dump(tmp_path, name, rep):
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, default=str), encoding="utf-8")
    return p


def test_r43_detail(tmp_path):
    import test_regime_style_study as trs

    d = rd.build_digest(
        "r43_detail", _dump(tmp_path, "r43", trs._calib_run(0, False)), "t"
    )
    assert d["missing"] == []
    md = rr.render(d)
    assert "风格维度" in md and "xychart-beta" in md and "pie title" in md


def test_r39_detail(tmp_path, monkeypatch):
    import helpers_null as hn
    import test_factor_exit_study as fxt

    rep = fxt._run(
        monkeypatch,
        fxt._three_level_spec(180),
        hn.null_replay,
        n_random=5,
        c4_min_pool=3,
    )
    d = rd.build_digest("r39_detail", _dump(tmp_path, "r39", rep), "t")
    assert d["missing"] == []
    md = rr.render(d)
    assert "映射矩阵" in md and "桶0 \\ 桶1" in md and md.count("| P") >= 4


def test_auto_fallback_on_every_repo_digest():
    """无规格的 kind 走自动兜底：仓库里每份摘要都能渲染（判决 / 标量表 / 同构族透视）。"""
    n = 0
    for p in sorted((RESEARCH_DIR / "results").rglob("*.json")):
        md = rr.render(json.loads(p.read_text(encoding="utf-8")))
        assert md.startswith("<!-- report-of:") and "# " in md
        n += 1
    assert n >= 1


def test_generic_sections_and_family_detection():
    d = {
        "kind": "zz",
        "unit": "RX",
        "tag": "t",
        "path": "results/RX/t.json",
        "fields": {
            "verdict": "falsified",
            "criteria.C1": {"ok": True, "rule": "n≥100"},
            "merged.x5.margin": 0.01,
            "merged.x7.margin": 0.02,
            "merged.x10.margin": -0.01,
            "merged.x5.verdict": "falsified",
            "merged.x7.verdict": "untested",
            "merged.x10.verdict": "falsified",
            "n": 5,
        },
    }
    md = rr.render(d)
    assert (
        "## merged.*" in md
        and "| x5 |" in md
        and md.index("| x5 |") < md.index("| x10 |")
    )  # 自然序
    assert "pie title" in md and "❌ falsified" in md and "`n`" in md
    piv = rr.sec_pivot(
        d,
        {"rows": "merged.*", "cols": [("margin", "m", "pp")], "sort": ("margin", True)},
    )
    assert piv[2].startswith("| x7 | +2.00pp")


def test_digest_write_emits_report(tmp_path, monkeypatch):
    import helpers_null as hn
    import test_null_calibration as tn

    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    prod = _dump(tmp_path, "r40", tn._bear_run(hn.null_replay)(0))
    assert rd.main(["r40", str(prod), "--tag", "x", "--write"]) == 0
    assert (tmp_path / "results/R40/x_report.md").exists()


def test_repo_reports_are_pure_functions_of_digests():
    for rp in sorted((RESEARCH_DIR / "results").rglob("*_report.md")):
        first = rp.read_text(encoding="utf-8").split("\n", 1)[0]
        assert first.startswith("<!-- report-of:"), rp
        src = RESEARCH_DIR / first[len("<!-- report-of:") : -len(" -->")]
        assert src.exists(), f"{rp.name} 引用的 {src} 不存在"
        d = json.loads(src.read_text(encoding="utf-8"))
        assert rp.read_text(encoding="utf-8") == rr.render(d), (
            f"{rp.name} 与摘要不一致（手改？重跑 result_report）"
        )


def test_r36_frozen_report_with_dist():
    dist = {
        "n": 9000,
        "win_rate": 0.42,
        "payoff_ratio": 1.7,
        "avg_win": 0.08,
        "avg_loss": -0.045,
        "q": {"p5": -0.09, "p50": -0.01, "p95": 0.2},
        "ge_3x_avg_loss": 0.12,
        "top5pct_profit_share": 0.31,
    }
    d = {
        "kind": "r36_frozen",
        "unit": "R36",
        "tag": "t",
        "path": "results/R36/t.json",
        "fields": {
            "verdict": "no_batch",
            "candidate.delta_median": 0.05,
            "candidate.equal_margin_median": -0.0157,
            "candidate.per_seed": [
                {
                    "top_sel_dist": dist,
                    "equal_sel_dist": dist,
                    "top_ret_over_dd": 1.2,
                    "equal_ret_over_dd": 0.8,
                }
            ],
            "arms": [
                {
                    "arm": 0,
                    "status": "ok",
                    "sel_margin": 0.03,
                    "ret_over_dd": 1.1,
                    "sel_dist": dist,
                },
                {
                    "arm": 1,
                    "status": "gate_fail",
                    "sel_margin": 0.05,
                    "ret_over_dd": 0.3,
                    "sel_dist": dist,
                },
            ],
        },
    }
    md = rr.render(d)
    assert "≥3×均亏占比" in md and "| 候选 |" in md and "pie title 零假设臂状态" in md
    assert md.index("| 臂 1 |") < md.index("| 臂 0 |")  # 按 Δ 降序
