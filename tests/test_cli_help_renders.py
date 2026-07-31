# -*- coding: utf-8 -*-
"""所有 CLI 的 --help 必须能渲染 —— 防回归。

2026-07-31 踩过:`launch_point_study.py` 的 `--exclude-zero-ret` help 里写了单个 `%`
("实测 100% 是正常成交的"),argparse 把它当格式符解析 ⇒ **`--help` 整个崩掉**
(ValueError: unsupported format character)。help 文本里的百分号必须写 `%%`。
这类错误只在有人执行 --help 时暴露,单测不覆盖就会一直躺在仓库里。
"""
from __future__ import annotations

import importlib
import re

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


# 匹配一个 Python 字符串字面量(含转义),带前导空白 —— 用于收集隐式拼接的相邻段
_STR_SEG = re.compile(r'\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')', re.S)


def _unescaped_percents_in_help(src: str) -> list[str]:
    """扫描源码里所有 help= 文本,返回含未转义 % 的片段列表。

    必须收集 help= 后**所有相邻字符串段**(隐式拼接):只取第一段会漏检后续段
    —— 2026-07-31 那个 bug("实测 100% 是正常成交的")就藏在第二段里。
    """
    bad = []
    for m in re.finditer(r"help\s*=", src):
        pos, parts = m.end(), []
        while True:
            sm = _STR_SEG.match(src, pos)
            if not sm:
                break
            parts.append(sm.group(1))
            pos = sm.end()
        text = "".join(parts)
        # 去掉合法的 %% 后若仍有 %,即为未转义
        if "%" in text.replace("%%", ""):
            bad.append(text[:70])
    return bad


@pytest.mark.parametrize("modname", CLI_MODULES)
def test_no_unescaped_percent_in_help_strings(modname):
    """静态兜底:help 文本里出现单个 % 就是隐患(即便当前恰好没触发)。"""
    import inspect
    mod = importlib.import_module(modname)
    try:
        src = inspect.getsource(mod)
    except OSError:
        pytest.skip("取不到源码")
    bad = _unescaped_percents_in_help(src)
    assert not bad, f"{modname} 的 help 里有未转义的 %: {bad}"


def test_scanner_catches_percent_in_continuation_segment():
    """实证:未转义的 % 藏在隐式拼接的**后续段**里也必须被抓到(原 bug 正是如此)。"""
    src = ('ap.add_argument("--x", action="store_true",\n'
           '                    help="第一段没问题"\n'
           '                         "实测 100% 是正常成交的")\n')
    bad = _unescaped_percents_in_help(src)
    assert bad, "后续字符串段里的未转义 % 漏检了 —— 扫描器退化成只看第一段"
    # 正确转义(%%)则放行
    ok_src = src.replace("100% 是", "100%% 是")
    assert _unescaped_percents_in_help(ok_src) == []
