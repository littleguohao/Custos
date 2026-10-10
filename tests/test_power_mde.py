# -*- coding: utf-8 -*-
"""power_mde（统计功效估算，v0.326，owner 方法论 review #2；v0.330 拆参）钉测。

锁的契约：SE 公式（sqrt(wr(1−wr)/n)·sqrt(2(1−ρ_pair))·sqrt(deff)）逐值、
MDE=k×SE、参数边界 ValueError、**两个相关方向相反不许再混**（ρ_pair 越高
SE 越小 / deff 越高 SE 越大——owner review #3 返修）、场景表确定性、
CLI 输出含「下界」注记。
"""

from __future__ import annotations

import pytest

from custos.research import power_mde as pm


class TestSeFormula:
    def test_value(self):
        # wr=0.46, n=133, ρ_pair=0.75, deff=1：sqrt(0.46×0.54/133)×sqrt(0.5)
        got = pm.se_margin(0.46, 133, 0.75)
        assert got == pytest.approx(0.0306, abs=1e-3)

    def test_rho_independence_bound(self):
        # ρ_pair→0 时 SE 最大（配对最差）；ρ_pair→1 时 SE→0（完全相关配对）
        assert pm.se_margin(0.5, 100, 0.0) > pm.se_margin(0.5, 100, 0.5)
        # sqrt(2(1−0.99))/sqrt(2(1−0)) = 0.1 ⇒ ρ_pair=0.99 时 SE 恰为 0 的 1/10
        assert pm.se_margin(0.5, 100, 0.99) == pytest.approx(
            0.1 * pm.se_margin(0.5, 100, 0.0)
        )

    def test_deff_inflates_se_opposite_direction(self):
        # owner review #3：deff（日簇设计效应）与 ρ_pair 方向**相反**——
        # deff 越高 SE 越大（同日市场冲击把有效样本数打折）
        assert pm.se_margin(0.5, 100, 0.5, 2.0) > pm.se_margin(0.5, 100, 0.5, 1.0)
        assert pm.se_margin(0.5, 100, 0.5, 4.0) == pytest.approx(
            2.0 * pm.se_margin(0.5, 100, 0.5, 1.0)
        )

    def test_mde_is_k_times_se(self):
        assert pm.mde(0.46, 133, 0.75, k=2.0) == pytest.approx(
            2.0 * pm.se_margin(0.46, 133, 0.75)
        )

    def test_invalid_params_rejected(self):
        with pytest.raises(ValueError):
            pm.se_margin(0.0, 100, 0.5)  # wr 出界
        with pytest.raises(ValueError):
            pm.se_margin(0.5, 0, 0.5)  # n=0
        with pytest.raises(ValueError):
            pm.se_margin(0.5, 100, 1.0)  # rho_pair 出界
        with pytest.raises(ValueError):
            pm.se_margin(0.5, 100, 0.5, 0.5)  # deff<1（设计效应只放大）

    def test_table_deterministic(self):
        a = pm.table(0.46, 133)
        b = pm.table(0.46, 133)
        assert a == b
        assert {r["rho_pair"] for r in a} == set(pm.RHO_TIER_SCENARIOS)
        assert {r["deff"] for r in a} == set(pm.DEFF_SCENARIOS)
        assert len(a) == len(pm.RHO_TIER_SCENARIOS) * len(pm.DEFF_SCENARIOS)


class TestCli:
    def test_main_prints_lower_bound_note(self, capsys):
        rc = pm.main(["--wr", "0.46", "--n", "133"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "下界" in out and "MDE" in out
        assert "ρ_pair=0.75" in out and "deff=" in out

    def test_main_custom_rho(self, capsys):
        rc = pm.main(["--wr", "0.46", "--n", "400", "--rho", "0.75", "--deff", "1.0"])
        out = capsys.readouterr().out
        assert rc == 0 and "ρ_pair=0.75" in out
