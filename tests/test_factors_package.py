"""因子层独立成包：`src/custos/core/factors/`。

2026-08-06 依赖扫描显示 8 个因子模块其实是**live 选股链与研究回测器共同依赖的下层**：

    全部 8 个        被 backtest_factors（研究）依赖
    多数             被 enrich_candidates / score_candidates / signal_labels（live）依赖
    因子之间互相依赖  b1_dual→s_shape,platform_pullback；main_rally→rsi_state；
                     sector_phase→sector_mainstream

⇒ 它是一个层，不是 screening 的内部细节。而 `governance/strategy/_factors/`
（文档侧）早已存在，代码侧却没有对应位置 —— 文档与代码结构错位。

⚠️ **只做位置统一，不动接口。** 现存三套接口（`compute_*` / `detect_*` / `SCORERS` 的
`_sc_*`）+ 两种消费方式（`signal_labels` 出标签 / `SCORERS` 出打分），
统一接口会改 live 选股行为 ⇒ 属语义改动，必须单独立项 + 回测。
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FACTORS_DIR = ROOT / "src" / "custos" / "core" / "factors"
MODULES = ["s_shape", "b1_dual_factor", "b2_surge_factor", "main_rally_factor",
           "platform_pullback", "rsi_state", "sector_phase", "sector_mainstream"]


def test_package_exists_with_doc():
    init = FACTORS_DIR / "__init__.py"
    assert init.exists()
    s = init.read_text(encoding="utf-8")
    # 三套接口与两种消费方式必须写在包文档里 —— 那是将来统一接口立项的依据
    for kw in ("compute_xxx", "detect_xxx", "_sc_xxx", "signal_labels", "SCORERS"):
        assert kw in s, f"包文档缺 {kw} 的现状说明"
    assert "语义改动" in s, "必须写明统一接口是语义改动、需单独立项"


@pytest.mark.parametrize("m", MODULES)
def test_module_moved(m):
    assert (FACTORS_DIR / f"{m}.py").exists(), f"{m} 未在 factors/"
    for d in ("screening", "research"):
        assert not (ROOT / "src" / "custos" / d / f"{m}.py").exists(), \
            f"{m} 仍留在 {d}/（重复文件）"


class TestConsumersCanResolve:
    """消费方必须把 `factors/` 加进 sys.path —— 34 处扁平 import 才不用改。

    仓库既有惯例就是「扁平 import + 各自把目录加进 sys.path」
    （`screening/`、`market_timing/`、`local_tdx/` 都如此），
    改成包限定 import 要动 34 处、其中还有多行 `from X import (a, b,` —— 风险更高。
    """

    # ⚠️ 因子消费方**跨两个目录**：2026-08-07 研究脚本从 `screening/` 拆到 `research/`
    # （研究代码占了 screening/ 的 70%，性质与生产链不同）。
    # 这里存「目录/文件名」而不是只存文件名 —— 只存文件名的写法在拆分当天
    # 就让 6 条测试 FileNotFoundError。
    CONSUMERS = ["research/backtest_factors.py", "pipeline/screening/enrich_candidates.py",
                 "pipeline/screening/score_candidates.py", "pipeline/screening/signal_labels.py",
                 "research/launch_point_study.py", "pipeline/screening/candidate_table.py",
                 "research/scan_signals_ytd.py", "research/run_bear_to_long_study.py",
                 "research/compare_signal_sets.py", "research/scan_signal_backtest.py"]

    @pytest.mark.parametrize("f", CONSUMERS)
    def test_has_factors_bootstrap(self, f):
        s = (ROOT / "src" / "custos" / f).read_text(encoding="utf-8")
        assert "_FACTORS_DIR" in s, f"{f} 未把 factors/ 加进 sys.path"

    @pytest.mark.parametrize("f", CONSUMERS)
    def test_bootstrap_is_toplevel(self, f):
        """⚠️ 引导必须在**顶层**（列 0）。

        2026-08-06 实际踩到：脚本锚定「最后一处 sys.path.insert」，
        而 `launch_point_study` / `run_bear_to_long_study` 在**函数内部**做懒引导，
        于是引导被插进函数体 ⇒ `IndentationError`。
        """
        for ln in (ROOT / "src" / "custos" / f).read_text(encoding="utf-8").splitlines():
            if "_FACTORS_DIR = str(" in ln:
                assert not ln.startswith((" ", "\t")), f"{f} 的 factors 引导有缩进（在函数内）"


class TestImportsStillWork:
    @pytest.mark.parametrize("m", MODULES)
    def test_flat_import(self, m):
        """conftest 会把带 __init__.py 的子目录加进 sys.path ⇒ 扁平 import 仍可用。"""
        __import__(m)

    @pytest.mark.parametrize("m", MODULES)
    def test_package_qualified_import(self, m):
        """包限定 import 也要可用（测试里有几处这么写）。"""
        __import__(f"factors.{m}")

    def test_no_stale_screening_qualified_refs(self):
        """不许再有 `from screening import <因子>` —— 那会 ImportError。"""
        bad = []
        for p in list((ROOT / "tests").glob("*.py")) + list((ROOT / "src").rglob("*.py")):
            s = p.read_text(encoding="utf-8", errors="ignore")
            for m in MODULES:
                if re.search(rf"\bfrom screening import {m}\b|\bscreening\.{m}\b", s):
                    bad.append(f"{p.relative_to(ROOT)}: {m}")
        assert not bad, f"仍有指向 screening 的因子引用：{bad}"
