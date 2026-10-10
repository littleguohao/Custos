# -*- coding: utf-8 -*-
"""power_mde（统计功效估算，v0.326，owner 方法论 review #2）钉测。

锁的契约：SE 公式（sqrt(wr(1−wr)/n)·sqrt(2(1−ρ))）逐值、MDE=k×SE、
参数边界 ValueError、ρ 场景表确定性、CLI 输出含「下界」注记。
"""

from __future__ import annotations

import pytest

from custos.research import power_mde as pm


class TestSeFormula:
    def test_value(self):
        # wr=0.46, n=133, ρ=0.75：sqrt(0.46×0.54/133)×sqrt(0.5)
        got = pm.se_margin(0.46, 133, 0.75)
        assert got == pytest.approx(0.0306, abs=1e-3)

    def test_rho_independence_bound(self):
        # ρ→0 时 SE 最大（独立性最差）；ρ→1 时 SE→0（完全相关配对）
        assert pm.se_margin(0.5, 100, 0.0) > pm.se_margin(0.5, 100, 0.5)
        # sqrt(2(1−0.99))/sqrt(2(1−0)) = 0.1 ⇒ ρ=0.99 时 SE 恰为 ρ=0 的 1/10
        assert pm.se_margin(0.5, 100, 0.99) == pytest.approx(
            0.1 * pm.se_margin(0.5, 100, 0.0)
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
            pm.se_margin(0.5, 100, 1.0)  # rho 出界

    def test_table_deterministic(self):
        a = pm.table(0.46, 133)
        b = pm.table(0.46, 133)
        assert a == b
        assert [r["rho"] for r in a] == list(pm.RHO_TIER_SCENARIOS)


class TestCli:
    def test_main_prints_lower_bound_note(self, capsys):
        rc = pm.main(["--wr", "0.46", "--n", "133"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "下界" in out and "MDE" in out
        assert "ρ=0.75" in out

    def test_main_custom_rho(self, capsys):
        rc = pm.main(["--wr", "0.46", "--n", "400", "--rho", "0.75"])
        out = capsys.readouterr().out
        assert rc == 0 and "ρ=0.75" in out
