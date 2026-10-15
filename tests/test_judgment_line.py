"""攒批判决件（v0.389，owner 选 B）：m 只从名册读；池 = 冻结臂判定段重读。"""

import pytest

from custos.research import judgment_batch as jb


def _open(tmp_path, n):
    reg = tmp_path / "jb.jsonl"
    jb.open_batch(
        "b1",
        [{"id": f"c{k}", "config_hash": f"t:{k}", "unit": "R0"} for k in range(n)],
        path=reg,
    )
    return reg


def test_batch_m_from_roster(tmp_path):
    reg = _open(tmp_path, 4)
    assert jb.batch_m("b1", path=reg) == 4
    with pytest.raises(ValueError):
        jb.batch_m("b9", path=reg)


def test_judgment_line_states(tmp_path):
    reg = _open(tmp_path, 4)
    pool = [float(k) for k in range(200)]  # α=0.0125 ⇒ 第 2 大 = 198
    out = jb.judgment_line("b1", 198.5, pool, min_pool=200, path=reg)
    assert (
        out["m"] == 4 and out["threshold"] == 198.0 and out["state"] == "confirmed_pass"
    )
    assert (
        jb.judgment_line("b1", 197.0, pool, min_pool=200, path=reg)["state"]
        == "confirmed_fail"
    )
    assert (
        jb.judgment_line("b1", 999.0, pool[:50], min_pool=200, path=reg)["state"]
        == "provisional"
    )
    assert (
        jb.judgment_line("b1", 1.0, [], min_pool=200, path=reg)["state"]
        == "indeterminate"
    )
    assert (
        jb.judgment_line("b1", None, pool, min_pool=200, path=reg)["state"]
        == "confirmed_fail"
    )
