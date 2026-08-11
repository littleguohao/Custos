# -*- coding: utf-8 -*-
"""所有 CLI 的 --help 必须能渲染 —— 防回归。

2026-07-31 踩过:`launch_point_study.py` 的 `--exclude-zero-ret` help 里写了单个 `%`
("实测 100% 是正常成交的"),argparse 把它当格式符解析 ⇒ **`--help` 整个崩掉**
(ValueError: unsupported format character)。help 文本里的百分号必须写 `%%`。
这类错误只在有人执行 --help 时暴露,单测不覆盖就会一直躺在仓库里。
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "src"


def _discover_cli() -> list[tuple[pathlib.Path, str, bool]]:
    """扫出所有「有 ArgumentParser + main()」的模块。

    ⚠️ 原先是**硬编码 7 个模块**的清单，而 src 里有 **69 个** CLI。
    2026-08-07 改为自动发现 —— 手维护的清单会漏，而漏掉的那些恰恰是
    没人跑 `--help` 的冷门脚本（`adjust_diagnostic` 的 `--help` 因未转义 `%`
    崩掉，当天是**手工**才发现的，清单里没有它）。

    返回 (文件, 模块名, main 是否接受 argv)。
    """
    out = []
    for f in sorted(TOOLS.rglob("*.py")):
        if f.name in ("__init__.py", "conftest.py"):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        if not any(isinstance(n, ast.Call) and "ArgumentParser" in ast.unparse(n.func)
                   for n in ast.walk(tree)):
            continue
        mn = next((n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
        if mn is None:
            continue
        modname = "custos." + f.relative_to(TOOLS / "custos").with_suffix("").as_posix().replace("/", ".")
        out.append((f, modname, bool(mn.args.args)))
    return out


_CLI = _discover_cli()
# `--help` 在进程内渲染：只对 `main(argv)` 形式的做 —— 无参数形式要动 sys.argv，
# 而部分 main 在 parse_args **之前**就有副作用（建目录、读盘），不宜在测试里跑。
CLI_MODULES = [m for _, m, takes_argv in _CLI if takes_argv]
# 静态 `%` 扫描不需要 import，所以覆盖**全部** CLI（含无法在 Linux 导入的）。
ALL_CLI_FILES = [(f, m) for f, m, _ in _CLI]


@pytest.mark.parametrize("modname", CLI_MODULES)
def test_help_renders_without_format_error(modname, capsys):
    mod = importlib.import_module(modname)
    main = getattr(mod, "main", None)
    # ⚠️ 不再 `pytest.skip` —— 清单是从 AST 里发现「确有 main()」才收进来的，
    #    运行时取不到就说明模块结构与源码不符（例如 main 被条件定义），是真问题。
    assert main is not None, f"{modname} 源码里有 main() 但运行时取不到"
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower() and len(out) > 100


def _unescaped_percents_in_help(src: str) -> list[str]:
    """扫描 `add_argument(help=...)` 文本，返回含未转义 % 的片段列表。

    ⚠️ 判据 2026-08-07 由正则改为 **AST**，原因是正则版有两个缺陷：

    ① **漏看 `% VAR` 格式化**：`help="...前 %d 天..." % OVERLAP_DAYS` 里的 `%d`
       在构造时就被消费掉了，最终 help 文本里没有 `%`。正则只收字符串字面量，
       把它误报成隐患（扩大覆盖面后第一个「命中」就是这个误报，
       实跑 `--help` 退出码 0、渲染成「末日期前 30 天」才确认）。
    ② 正则要专门处理**隐式拼接**（原 bug 藏在第二段里），而 AST 里
       `"a" "b"` 本来就是**一个** Constant —— 那份复杂度是白付的。

    仍然覆盖的形态：纯字符串（含隐式拼接）、f-string 的字面部分。
    `help=某变量` 无法静态判定，跳过。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and (getattr(node.func, "attr", None) == "add_argument"
                     or getattr(node.func, "id", None) == "add_argument")):
            continue
        kw = next((k for k in node.keywords if k.arg == "help"), None)
        if kw is None:
            continue
        v = kw.value
        # `"..." % X` —— % 被格式化消费，不是隐患
        if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Mod):
            continue
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            text = v.value
        elif isinstance(v, ast.JoinedStr):
            text = "".join(x.value for x in v.values
                           if isinstance(x, ast.Constant) and isinstance(x.value, str))
        else:
            continue                      # help=变量 / 调用，静态判不了
        if "%" in text.replace("%%", ""):
            bad.append(text[:70])
    return bad


@pytest.mark.parametrize("path,modname", ALL_CLI_FILES,
                         ids=[m for _, m in ALL_CLI_FILES])
def test_no_unescaped_percent_in_help_strings(path, modname):
    """静态兜底：help 文本里出现单个 % 就是隐患（即便当前恰好没触发）。

    ⚠️ 改为**读文件**而非 `inspect.getsource(导入的模块)`：
    ① 不需要能 import，所以覆盖得到只在 Windows 可用的模块；
    ② 原来的 `except OSError: pytest.skip("取不到源码")` 是个静默通过口子。
    覆盖面从 7 个模块扩到全部 CLI。
    """
    bad = _unescaped_percents_in_help(path.read_text(encoding="utf-8-sig"))
    assert not bad, f"{modname} 的 help 里有未转义的 %: {bad}"


def test_discovery_found_a_realistic_number_of_clis():
    """⚠️ 守卫自证：发现逻辑失效时上面两组测试会**静静地变成零个用例**。

    今天已经因为「测试静默变空转」踩过四次，不能再靠肉眼确认。
    """
    assert len(_CLI) >= 50, f"只发现 {len(_CLI)} 个 CLI —— 发现逻辑可能失效了"
    assert len(CLI_MODULES) >= 25, f"可进程内测 --help 的只有 {len(CLI_MODULES)} 个"


def test_scanner_catches_percent_in_continuation_segment():
    """实证：未转义的 % 藏在隐式拼接的**后续段**里也必须被抓到（原 bug 正是如此）。"""
    src = ('ap.add_argument("--x", action="store_true",\n'
           '                help="第一段没问题"\n'
           '                     "实测 100% 是正常成交的")\n')
    assert _unescaped_percents_in_help(src), \
        "后续字符串段里的未转义 % 漏检了"
    # 正确转义(%%)则放行
    assert _unescaped_percents_in_help(src.replace("100% 是", "100%% 是")) == []


def test_scanner_does_not_flag_percent_consumed_by_formatting():
    """⚠️ `help="... %d ..." % VAR` 不是隐患 —— % 在构造时已被消费。

    正则版把它误报了：扩大覆盖面后第一个「命中」就是
    `fetch_sector_index_history` 的 `--incremental`，实跑 `--help` 退出码 0、
    渲染成「末日期前 30 天」。**先验证再动手**，否则会去「修」一个没坏的东西。
    """
    src = ('ap.add_argument("--incremental", action="store_true",\n'
           '                help="缓存末日期前 %d 天起的增量并合并"\n'
           '                     "(不加则全量重拉)" % OVERLAP_DAYS)\n')
    assert _unescaped_percents_in_help(src) == [], "把已被 % 格式化消费的 %d 误报成隐患"


def test_scanner_checks_fstring_literal_parts():
    """f-string 的**字面部分**里的裸 % 同样是隐患。"""
    src = 'ap.add_argument("--x", help=f"阈值 {T} 时命中率 60% 左右")\n'
    assert _unescaped_percents_in_help(src), "f-string 字面部分的 % 漏检了"
