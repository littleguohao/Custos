"""因子注册表与**「待优化不得进 live」**约束。

2026-08-06 把 `backtest_factors` 里 9 个自包含 scorer 抽成 `factors/` 下各自的模块，
每个模块声明 `FACTOR` 元数据（模板见 `factors/_template.py`）。

⚠️ **元数据里最要紧的是 `status`。**
它把 R2「选股章节正式关闭、所有价量选择器证伪」这个结论**变成机器可执行的约束** ——
否则半年后有人看到 `alpha101` 就拿去用了，而文档里那条否决没人会重读。

抽取原则同前两轮：**零行为变化**，逐个用数值等价验证（见 TestNumericEquivalence）。
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("07_tools", "07_tools/factors", "07_tools/screening"):
    sys.path.insert(0, str(ROOT / _p))

import factors  # noqa: E402

EXTRACTED = ["baseline", "alpha101", "alpha_pvcorr", "low_vol", "momentum",
             "reversal_quality", "reversal_quality_inv", "mcap", "kdj_j"]


def _bars(n=80, seed=5):
    rng = np.random.default_rng(seed)
    c = 10 + np.cumsum(rng.normal(0, 0.2, n))
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n).astype(str),
        "open": c + rng.normal(0, 0.05, n), "close": c,
        "high": c + abs(rng.normal(0, 0.15, n)), "low": c - abs(rng.normal(0, 0.15, n)),
        "volume": abs(rng.normal(1e6, 2e5, n))})


class TestRegistry:
    def test_all_extracted_registered(self):
        reg = factors.registry()
        missing = [f for f in EXTRACTED if f not in reg]
        assert not missing, f"未注册：{missing}"

    def test_template_not_registered(self):
        assert "template" not in factors.registry(), "模板不该进注册表"

    @pytest.mark.parametrize("fid", EXTRACTED)
    def test_metadata_complete(self, fid):
        m = factors.registry()[fid]["meta"]
        for k in ("id", "name", "kind", "status", "note", "min_bars"):
            assert m.get(k) not in (None, ""), f"{fid} 缺元数据 {k}"
        assert m["status"] in factors.STATUSES
        assert m["kind"] in factors.KINDS

    @pytest.mark.parametrize("fid", EXTRACTED)
    def test_needs_work_must_cite_evidence(self, fid):
        """判「待优化」必须给出处 —— 否则下次有人会以为是拍脑袋否掉的。"""
        m = factors.registry()[fid]["meta"]
        if m["status"] == "needs_work":
            assert m["evidence"].startswith("00_governance/research/"), \
                f"{fid} 标 needs_work 但没给 research 出处"
            assert (ROOT / m["evidence"]).exists(), f"{fid} 的 evidence 路径不存在"

    @pytest.mark.parametrize("fid", EXTRACTED)
    def test_selector_has_score(self, fid):
        e = factors.registry()[fid]
        if e["meta"]["kind"] in ("selector", "control"):
            assert callable(e["score"]), f"{fid} 是 selector 但没有 score()"


class TestNotForLive:
    """**待优化/未验证的因子不得进 live 选股链。**

    这是本次抽取最有价值的产出：把研究结论变成机器约束。
    """

    def test_live_allowed_excludes_needs_work(self):
        allowed = factors.live_allowed()
        for fid, e in factors.registry().items():
            if e["meta"]["status"] in factors.NOT_FOR_LIVE:
                assert fid not in allowed, f"{fid}({e['meta']['status']}) 不该在 live 白名单里"

    def test_known_needs_work_are_marked(self):
        """R2/R3 明确否决过的这几个，状态必须是 needs_work。"""
        reg = factors.registry()
        for fid in ("alpha101", "reversal_quality", "reversal_quality_inv", "mcap", "kdj_j"):
            assert reg[fid]["meta"]["status"] == "needs_work", \
                f"{fid} 在 research 里已被否决，状态却不是 needs_work"

    def test_live_chain_does_not_import_needs_work(self):
        """live 选股链的源码里不得出现待优化因子的模块名。"""
        needs_work = {fid for fid, e in factors.registry().items()
                     if e["meta"]["status"] == "needs_work"}
        live = ["screening/enrich_candidates.py", "screening/score_candidates.py",
                "screening/candidate_table.py", "screening/signal_labels.py"]
        bad = []
        for rel in live:
            s = (ROOT / "07_tools" / rel).read_text(encoding="utf-8")
            for fid in needs_work:
                if f"import {fid}" in s or f"from {fid} import" in s:
                    bad.append(f"{rel} → {fid}")
        assert not bad, f"live 链引用了待优化因子：{bad}"


class TestNumericEquivalence:
    """抽取必须**零行为变化**：新 `score` 与原 `SCORERS[fid]` 逐点相同。"""

    @pytest.mark.parametrize("fid", [f for f in EXTRACTED if f != "mcap"])
    def test_matches_scorers_entry(self, fid):
        import backtest_factors as BF
        df = _bars()
        assert fid in BF.SCORERS, f"{fid} 不在 SCORERS 里"
        assert factors.registry()[fid]["score"](df, "600000") == BF.SCORERS[fid](df, "600000")

    def test_scorers_keys_unchanged(self):
        """SCORERS 的键集合不能因为重构而变 —— CLI `--scorer` 参数依赖它。"""
        import backtest_factors as BF
        for fid in EXTRACTED:
            assert fid in BF.SCORERS


class TestSharedMutableStateImportRule:
    """持有**可变模块级状态**的模块必须包限定导入。

    ⚠️ 2026-08-06 当场发作：`07_tools` 与 `07_tools/factors` 都在 sys.path 上 ⇒
    同一文件有两条可导路径（`_shares` / `factors._shares`），Python 建**两个模块对象**，
    而 `_shares` 持有可变缓存 `_SHARE_IDX` ⇒ 测试打桩一个、生产读另一个。
    """

    def test_shares_imported_package_qualified(self):
        for rel in ("factors/mcap.py", "screening/backtest_factors.py"):
            s = (ROOT / "07_tools" / rel).read_text(encoding="utf-8")
            assert "from factors._shares import" in s, f"{rel} 应包限定导入 _shares"
            assert "\nfrom _shares import" not in s, f"{rel} 有扁平导入 _shares（会产生两份状态）"

    def test_rule_documented(self):
        s = (ROOT / "07_tools" / "factors" / "_shares.py").read_text(encoding="utf-8")
        assert "包限定" in s and "两个模块对象" in s
