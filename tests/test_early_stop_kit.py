"""v0.466 criteria_kit 提前终止件：穷举四态映射核对 verdict_without_c4。"""

import itertools

from custos.research import criteria_kit as kit


def test_verdict_without_c4_exhaustive():
    for c1, c2, c3 in itertools.product(
        (True, False), (True, False, None), (True, False, None)
    ):
        vs = {
            kit.verdict_four_state(c1_ok=c1, c2_ok=c2, c3_ok=c3, c4_state=s)
            for s in kit.C4_STATES
        }
        got = kit.verdict_without_c4(c1_ok=c1, c2_ok=c2, c3_ok=c3)
        assert got == (next(iter(vs)) if len(vs) == 1 else None), (c1, c2, c3)
    assert kit.verdict_without_c4(c1_ok=False, c2_ok=True, c3_ok=True) == "untested"
    assert kit.verdict_without_c4(c1_ok=True, c2_ok=False, c3_ok=True) == "falsified"
    assert kit.verdict_without_c4(c1_ok=True, c2_ok=True, c3_ok=True) is None


def test_skip_never_evaluates_arms():
    calls = []
    out = kit.assemble_c4_pool_or_skip(
        5, lambda i: calls.append(i) or 1.0, skip_reason="x"
    )
    assert calls == [] and out["pool"] == [] and out["early_stop"]["skipped"]
    assert kit.c4_state_of(out["pool"], min_pool=3, plan_delta=1.0) == "indeterminate"
    out = kit.assemble_c4_pool_or_skip(5, lambda i: 1.0, skip_reason="x", enabled=False)
    assert len(out["pool"]) == 5 and out["early_stop"] == {"skipped": False}
    out = kit.assemble_c4_pool_or_skip(5, lambda i: 1.0, skip_reason=None)
    assert len(out["pool"]) == 5
