# -*- coding: utf-8 -*-
"""所有 CLI 的 --help 必须能渲染 —— 防回归。

2026-07-31 踩过:`launch_point_study.py` 的 `--exclude-zero-ret` help 里写了单个 `%`
("实测 100% 是正常成交的"),argparse 把它当格式符解析 ⇒ **`--help` 整个崩掉**
(ValueError: unsupported format character)。help 文本里的百分号必须写 `%%`。
这类错误只在有人执行 --help 时暴露,单测不覆盖就会一直躺在仓库里。
"""
from __future__ import annotations

import importlib

import pytest

# (模块名, 是否需要 local_tdx 路径)
CLI_MODULES = [
    "launch_point_study",
    "run_bear_to_long_study",
    "backtest_factors",
    "fetch_pit_financials",
    "fetch_market_cap",
    "runtime_gate",
    "calc_mfe_mae",
]


@pytest.mark.parametrize("modname", CLI_MODULES)
def test_help_renders_without_format_error(modname, capsys):
    mod = importlib.import_module(modname)
    main = getattr(mod, "main", None)
    if main is None:
        pytest.skip(f"{modname} 无 main()")
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower() and len(out) > 100


@pytest.mark.parametrize("modname", CLI_MODULES)
def test_no_unescaped_percent_in_help_strings(modname):
    """静态兜底:help 文本里出现单个 % 就是隐患(即便当前恰好没触发)。"""
    import inspect
    import re
    mod = importlib.import_module(modname)
    try:
        src = inspect.getsource(mod)
    except OSError:
        pytest.skip("取不到源码")
    bad = []
    for m in re.finditer(r'help=\s*(".*?"|\'.*?\')', src, re.S):
        text = m.group(1)
        # 去掉合法的 %% 后若仍有 %,即为未转义
        if "%" in text.replace("%%", ""):
            bad.append(text[:70])
    assert not bad, f"{modname} 的 help 里有未转义的 %: {bad}"
