"""交易分布画像（trade_dist，v0.421）：描述性副读数——口径钉测 + 扫描接入 + 摘要零缺失 + 不改判决。"""

import json

import pytest

from custos.research import result_digest as rd
from custos.research import trade_dist as td


def _t(code, d, ret, reason="bbi_exit", holding=5):
    return {
        "code": code,
        "entry_date": d,
        "ret": ret,
        "reason": reason,
        "holding": holding,
    }


def test_basic_stats_and_realized_only():
    tr = [
        _t("a", "2020-01-02", 0.10),
        _t("b", "2020-01-02", -0.05, "stop"),
        _t("c", "2021-01-04", 0.30),
        _t("d", "2021-01-04", 0.5, "open_end"),
    ]
    d = td.describe(tr)
    assert d["n"] == 3  # open_end 剔除
    assert d["win_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert d["avg_win"] == pytest.approx(0.2) and d["avg_loss"] == pytest.approx(-0.05)
    assert d["payoff_ratio"] == pytest.approx(4.0)
    assert d["margin"] == pytest.approx(2 / 3 - 1 / 5, abs=1e-6)
    assert d["big_win_share"]["ge_2x_avg_loss"] == pytest.approx(
        2 / 3, abs=1e-4
    )  # 0.10、0.30 ≥ 0.10
    assert d["big_win_share"]["ge_5x_avg_loss"] == pytest.approx(1 / 3, abs=1e-4)
    assert sum(b["n"] for b in d["buckets"]) == 3
    assert [y["year"] for y in d["by_year"]] == ["2020", "2021"]
    assert d["by_reason"][0]["reason"] == "bbi_exit"
    assert d["profit_concentration"]["top1pct_winners_profit_share"] == pytest.approx(
        0.75
    )


def test_paired_vs_ref_migration():
    ref = [_t("a", "d1", -0.10, "stop"), _t("b", "d1", 0.05), _t("c", "d1", 0.02)]
    here = [_t("a", "d1", 0.08), _t("b", "d1", -0.06, "stop"), _t("c", "d1", 0.02)]
    p = td.describe(here, ref)["paired_vs_ref"]
    assert p["n_matched"] == 3 and p["share_identical"] == pytest.approx(
        1 / 3, abs=1e-4
    )
    assert p["flip_ref_loss_to_win"] == 1 and p["flip_ref_win_to_loss"] == 1
    assert p["stopped_here_not_ref"]["n"] == 1 and p["stopped_here_not_ref"][
        "avg_ret_ref"
    ] == pytest.approx(0.05)
    assert p["stopped_ref_not_here"]["avg_ret_here"] == pytest.approx(0.08)


def test_empty():
    assert td.describe([]) == {"n": 0}


def test_scan_attaches_dist_without_changing_verdict(tmp_path):
    import test_plan_rules_replay as tp

    base = tp._scan_run(0, False)
    rep = tp._scan_run(0, False, trade_dist=True)
    assert rep["verdict"] == base["verdict"] and rep["criteria"] == base["criteria"]
    assert (
        "dist" in rep["merged"]["x7"] and "paired_vs_ref" in rep["merged"]["x7"]["dist"]
    )
    assert "paired_vs_ref" not in rep["merged"]["ref"]["dist"]
    assert "dist" not in base["merged"]["x7"]  # 缺省不附加（旧产物/金样不变）
    p = tmp_path / "s.json"
    p.write_text(json.dumps(rep, default=str), encoding="utf-8")
    d = rd.build_digest("r44_dist", p, "t")
    assert d["missing"] == []
    assert any(k.endswith("dist.buckets") for k in d["fields"])


def test_trade_dist_requires_scan():
    from custos.research import plan_rules_replay as prr

    with pytest.raises(SystemExit):
        prr.main(["--trade-dist"])
