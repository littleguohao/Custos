"""result_report（v0.427）：明细 kind 在真实产物上零缺失、报告可渲染；仓库内报告 = 摘要的纯函数（防手改）。"""

import json

import pytest

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


def test_unknown_kind_refused(tmp_path):
    p = _dump(
        tmp_path, "x", {"kind": "r40", "path": "results/R40/x.json", "fields": {}}
    )
    assert rr.main([str(p)]) == 2


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


@pytest.mark.parametrize("v,exp", [(0.0123, "+1.23pp"), (None, "—")])
def test_pp(v, exp):
    assert rr._pp(v) == exp
