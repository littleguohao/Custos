"""R47 浮盈保护扫描（v0.434）：端到端结构、C3 右尾护栏、CLI 互斥、零假设校准两连测 + 哨兵。"""

import argparse
import hashlib

import pytest

import helpers_null as hn
from custos.research import plan_rules_replay as prr
from custos.research import protect_scan as ps


def _args(**kw):
    base = dict(
        tag="t",
        cost_bps=25.0,
        top_n=20,
        seed=7,
        scan_random=6,
        scan_c4_min_pool=4,
        codes="",
        codes_file="",
        count=2000,
        universe_sample=0,
        universe_local=False,
        universe_seed=42,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _replay_factory(seed, edge_cell=None, big_kill=False):
    def _replay(subset, params):
        import test_plan_rules_replay as tp

        out = []
        cell = (params.get("breakeven_trigger", 0.0), params.get("trail_pct", 0.0))
        for r in subset:
            key = f"{r['code']}|{r['date']}|{sorted(params.items())}|{seed}|prot1"
            h = int.from_bytes(hashlib.md5(key.encode()).digest()[:4], "little") / 2**32
            if edge_cell is not None and all(
                abs(a - b) < 1e-9 for a, b in zip(cell, edge_cell)
            ):
                ret = (0.25 if h < 0.2 else 0.06) if h < 0.75 else -0.02
                if big_kill:
                    ret = min(ret, 0.10)
            else:
                ret = (0.25 if h < 0.08 else 0.06) if h < 0.4 else -0.035
            out.append(tp._trade_of(r, ret))
        return out

    return _replay


def _run(seed, **kw):
    import test_plan_rules_replay as tp

    spec = tp._scan_fixture()
    replay = kw.pop("replay", None) or _replay_factory(seed)
    return ps.run_protect_scan(
        _args(seed=seed, **kw), warm_fn=lambda s, e: spec[(s, e)], replay_fn=replay
    )


def test_structure_and_ref_cell():
    rep = _run(0)
    assert rep["unit"] == "R47" and rep["mode"] == "protect_scan"
    assert len(rep["merged"]) == 16 and "be0_tr0" in rep["merged"]
    w = next(iter(rep["readings"].values()))
    assert {"n_big_win", "give_back_share"} <= set(w["be5_tr8"])
    assert set(rep["criteria"]) == {"C1", "C2", "C3", "C4"}
    assert rep["window_usage"]["window"] == "not_read"


def test_edge_cell_selected_and_tail_guard():
    good = _run(1, replay=_replay_factory(1, edge_cell=(0.08, 0.12)), scan_random=10)
    assert (
        good["criteria"]["C2"]["g_star"] == "be8_tr12" and good["criteria"]["C2"]["ok"]
    )
    assert good["criteria"]["C3"]["tail_ok"] is True
    killed = _run(
        1,
        replay=_replay_factory(1, edge_cell=(0.08, 0.12), big_kill=True),
        scan_random=10,
    )
    assert killed["criteria"]["C2"]["g_star"] == "be8_tr12"
    assert (
        killed["criteria"]["C3"]["tail_ok"] is False
    )  # 砍掉 ≥+20% 大单 ⇒ 右尾护栏不过
    assert killed["verdict"] != "candidate"


def test_cli_mutual_exclusion():
    for extra in (
        ["--scan-widths"],
        ["--stop-anchor", "signal_low", "--stop-buffer-pct", "8"],
        ["--mining-start", "2000-01-01"],
    ):
        with pytest.raises(SystemExit):
            prr.main(["--scan-protect", *extra])


class TestProtectScanCalibration:
    """零假设两连测：纯噪声 confirmed_pass 0/5；植入「be8_tr12 真更优」可识别。"""

    def test_noise(self):
        hn.assert_noise_calibration(
            "protect_scan", lambda s: _run(s, scan_random=25, scan_c4_min_pool=20)
        )

    def test_edge_detected(self):
        hn.assert_edge_detected(
            "protect_scan",
            lambda s: _run(
                s,
                replay=_replay_factory(s, edge_cell=(0.08, 0.12)),
                scan_random=25,
                scan_c4_min_pool=20,
            ),
        )


class TestSentinelProtectMaxOfOne:
    """回归哨兵：随机臂退化成 max-of-1（预算不对等）⇒ 噪声下校准必须失败。"""

    def test_calibration_catches_unequal_budget(self, monkeypatch):
        monkeypatch.setattr(ps, "C4_ARM_DRAWS", 1)
        with pytest.raises(AssertionError, match="零假设校准失败"):
            hn.assert_noise_calibration(
                "protect_scan[max-of-1 哨兵]",
                lambda s: _run(s, scan_random=25, scan_c4_min_pool=20),
            )
