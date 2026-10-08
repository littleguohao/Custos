# -*- coding: utf-8 -*-
"""受影响测试选择器——本地迭代只跑「修改影响到的模块 + 基础守卫」，全量交 GitHub CI。

动机：全量套件本机 ~5.3 分钟/6283 例（2026-10-08 实测），迭代期每次提交前
全量跑太重；GitHub CI（ubuntu+windows 双平台门禁）本来就在每个 push/PR 上
跑全量。本地改为受影响集，CI 保持全量硬门。

选择规则：
  1. 变更集 = 工作区未提交改动（git diff HEAD + untracked）∪ --since REF 的
     已提交 diff（三点，merge-base）；
  2. 变更的 src/custos 模块 → **反向 import 闭包**（谁直接或间接 import 它，
     ast 解析，含相对导入与 `from pkg import submodule` 形态）= 受影响模块集；
  3. 选中测试 = import 了受影响模块的测试 ∪ 命名直配（test_<模块 basename>，
     兜住 subprocess/CLI 等间接消费）∪ 变更的测试文件 ∪ **基础守卫集**
     （架构分层/路径深度/重复定义/共享助手/top-level 共享——项目级元规则，
     334 例 ~13.5s）；
  4. **全量触发**（影响面无法局部化）：conftest.py / tests/helpers_*.py /
     pyproject.toml / uv.lock / .github/**；只改文档/治理（非代码）⇒
     只跑基础守卫集；工作区干净 ⇒ 基础守卫集；
  5. --module PREFIX：模块划分模式——跑 import 了该包前缀下模块的全部测试
     （不基于变更，如 `custos.research`）。

用法：
  uv run python scripts/dev/test_affected.py              # 受影响集+守卫（本地迭代默认）
  uv run python scripts/dev/test_affected.py --since origin/main
  uv run python scripts/dev/test_affected.py --module custos.research
  uv run python scripts/dev/test_affected.py --dry-run    # 只看选择不跑
  uv run python scripts/dev/test_affected.py --full       # 全量（推送前可选；CI 必跑）
  其余参数原样传给 pytest（如 -x --lf）。
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
TESTS = ROOT / "tests"

#: 基础守卫集——项目级元规则测试，任何改动都跑
BASE_GUARD = (
    "test_architecture_layers.py",
    "test_base_path_depth.py",
    "test_no_shadowed_defs.py",
    "test_shared_helpers.py",
    "test_top_level_shared.py",
)

#: 治理守卫集——钉住治理纪律的测试（AGENTS §4「全部有测试钉着」）。它们大多
#: **不 import custos**（读的是 governance/文档/JSON 本体），靠 import 闭包永远
#: 选不中——v0.300 教训：R39/R40 文档不合规本地绿、推上去 CI 才红（9 例失败）。
#: 规则：governance/** 或根治理文件（CHANGELOG/TODO/README/AGENTS）或非数据
#: 通路的 *.json 有变动 ⇒ 追加本集。
GOVERNANCE_GUARD = (
    "test_research_units.py",
    "test_changelog_format.py",
    "test_todo_list.py",
    "test_contracts.py",
    "test_contracts_layer.py",
    "test_factor_registry.py",
    "test_screening_registry.py",
    "test_strategy_index.py",
    "test_strategy_grid.py",
)

#: 治理触发：路径前缀 / 根文件名（JSON 限治理与根目录——data/artifacts 是
#: 运行时产物不算）
GOVERNANCE_PREFIXES = ("governance/",)
GOVERNANCE_FILES = {"CHANGELOG.md", "TODO.md", "README.md", "AGENTS.md"}

#: 全量触发（影响面无法局部化）
FULL_TRIGGER_FILES = {"pyproject.toml", "uv.lock"}
FULL_TRIGGER_PREFIXES = (".github/", "tests/helpers_")


def _mod_of(path: Path, src_root: Path) -> str:
    """src 下 .py 路径 → 点分模块名（__init__ 归并为包）。"""
    parts = list(path.relative_to(src_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports_of(path: Path, mod: str | None, all_mods: set[str]) -> set[str]:
    """ast 解析一个文件的 custos import 集合（含相对导入；from X import y
    时 X.y 若是已知模块也计入）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    if mod is None:
        pkg_parts: list[str] = []
    elif path.name == "__init__.py":
        pkg_parts = mod.split(".")
    else:
        pkg_parts = mod.split(".")[:-1]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("custos"):
                    out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg_parts[: len(pkg_parts) - node.level + 1]
                parts = base + (node.module.split(".") if node.module else [])
            else:
                parts = node.module.split(".") if node.module else []
            base_mod = ".".join(parts)
            if not base_mod.startswith("custos"):
                continue
            out.add(base_mod)
            for a in node.names:
                cand = f"{base_mod}.{a.name}"
                if cand in all_mods:
                    out.add(cand)
    return out


def build_graph(src_root: Path = SRC) -> dict[str, set[str]]:
    """src/custos 模块 → 其 import 的 custos 模块集合。"""
    mods = {_mod_of(p, src_root): p for p in src_root.rglob("*.py")}
    all_mods = set(mods)
    return {
        m: {d for d in _imports_of(p, m, all_mods) if d in all_mods and d != m}
        for m, p in mods.items()
    }


def reverse_closure(graph: dict[str, set[str]], seeds: set[str]) -> set[str]:
    """反向传递闭包：seeds ∪ 所有（直接或间接）import 它们的模块。"""
    rev: dict[str, set[str]] = {}
    for m, deps in graph.items():
        for d in deps:
            rev.setdefault(d, set()).add(m)
    seen = set(seeds)
    stack = list(seeds)
    while stack:
        for up in rev.get(stack.pop(), ()):
            if up not in seen:
                seen.add(up)
                stack.append(up)
    return seen


def build_test_map(
    all_mods: set[str], tests_root: Path = TESTS
) -> dict[Path, set[str]]:
    """test_*.py → 其 import 的 custos 模块集合。"""
    return {
        p: {d for d in _imports_of(p, None, all_mods) if d in all_mods}
        for p in sorted(tests_root.glob("test_*.py"))
    }


def changed_files(since: str | None = None) -> list[str]:
    """仓库相对路径变更集：工作区未提交改动（+ untracked）∪ --since REF."""

    def git(*a: str) -> str:
        return subprocess.run(
            ["git", *a], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout

    paths = set(git("diff", "--name-only", "HEAD").splitlines())
    paths |= set(git("ls-files", "--others", "--exclude-standard").splitlines())
    if since:
        paths |= set(git("diff", "--name-only", f"{since}...HEAD").splitlines())
    paths.discard("")
    return sorted(paths)


def _is_governance(p: str) -> bool:
    """治理文件判定：governance/** 或根治理文件或非数据通路的 *.json。"""
    if p.startswith(GOVERNANCE_PREFIXES) or p in GOVERNANCE_FILES:
        return True
    return p.endswith(".json") and not p.startswith(("artifacts/", "data/"))


def select(
    changed: list[str], src_root: Path = SRC, tests_root: Path = TESTS
) -> tuple[str, list[Path], list[str]]:
    """变更集 → (mode, 测试文件列表, 选择理由)。

    mode ∈ full（无法局部化）/ affected（受影响集）/ guard-only（非代码或空）。
    """
    why: list[str] = []
    for p in changed:
        if (
            p in FULL_TRIGGER_FILES
            or p.endswith("conftest.py")
            or p.startswith(FULL_TRIGGER_PREFIXES)
        ):
            return (
                "full",
                sorted(tests_root.glob("test_*.py")),
                [f"{p} 影响面无法局部化 ⇒ 全量"],
            )

    graph = build_graph(src_root)
    all_mods = set(graph)
    test_map = build_test_map(all_mods, tests_root)
    guard = [tests_root / n for n in BASE_GUARD if (tests_root / n).exists()]

    seeds: set[str] = set()
    selected: set[Path] = set(guard)
    if any(_is_governance(p) for p in changed):
        gov = [tests_root / n for n in GOVERNANCE_GUARD if (tests_root / n).exists()]
        selected |= set(gov)
        why.append(f"治理文件变更 ⇒ 追加治理守卫集 {len(gov)} 个")
    for p in changed:
        if p.startswith("src/") and p.endswith(".py"):
            fp = src_root / Path(p).relative_to("src")
            if fp.exists():
                seeds.add(_mod_of(fp, src_root))
            else:  # 删除的模块：图里没了，命名直配兜底
                hit = tests_root / f"test_{Path(p).stem}.py"
                if hit.exists():
                    selected.add(hit)
                    why.append(f"{p} 已删除 ⇒ 命名直配 {hit.name}")
        elif p.startswith("tests/") and p.endswith(".py"):
            fp = tests_root / Path(p).relative_to("tests")
            if fp.exists():
                selected.add(fp)
                why.append(f"{p} 测试本体变更")

    # ── 文件名提及扫描（v0.302 owner review，通用免维护清单）：变更文件名
    # 出现在哪个测试文本里就选哪个——live 配置 JSON 的钉测试既不 import
    # custos 也不满足命名直配（EXIT_RULES.json⇒test_exit_rules.py 实测漏选；
    # scripts/dev 下的脚本测试同理——它不在 custos 闭包里）──
    names = {
        Path(p).name
        for p in changed
        if not p.startswith("tests/") and len(Path(p).name) >= 5
    }
    if names:
        for t in sorted(tests_root.glob("test_*.py")):
            if t in selected:
                continue
            try:
                txt = t.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            hit = [n for n in names if n in txt]
            if hit:
                selected.add(t)
                why.append(f"{t.name} 提及 {sorted(hit)} ⇒ 选中")

    affected = reverse_closure(graph, seeds)
    for tfile, deps in test_map.items():
        if deps & affected:
            selected.add(tfile)
    for m in affected:
        hit = tests_root / f"test_{m.split('.')[-1]}.py"
        if hit.exists():
            selected.add(hit)

    if seeds:
        why.append(
            f"种子模块 {len(seeds)} → 反向闭包受影响模块 {len(affected)}："
            + ", ".join(sorted(affected)[:8])
            + (" ..." if len(affected) > 8 else "")
        )
    mode = "affected" if seeds else "guard-only"
    if mode == "guard-only":
        why.append("无 src 变更（文档/治理或干净工作区）⇒ 只跑基础守卫集")
    return mode, sorted(selected), why


def select_module(prefix: str, tests_root: Path = TESTS) -> list[Path]:
    """模块划分模式：import 了 prefix 包下模块的全部测试 + 基础守卫集。"""
    graph = build_graph()
    all_mods = set(graph)
    under = {m for m in all_mods if m == prefix or m.startswith(prefix + ".")}
    if not under:
        raise SystemExit(f"--module 无前缀匹配模块: {prefix}")
    test_map = build_test_map(all_mods, tests_root)
    out = {t for t, deps in test_map.items() if deps & under}
    out |= {tests_root / n for n in BASE_GUARD if (tests_root / n).exists()}
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=None, help="并入 REF...HEAD 的已提交 diff")
    ap.add_argument("--module", default=None, help="模块划分模式（如 custos.research）")
    ap.add_argument("--full", action="store_true", help="全量（推送前可选；CI 必跑）")
    ap.add_argument("--dry-run", action="store_true", help="只打印选择不跑")
    ap.add_argument("pytest_args", nargs=argparse.REMAINDER, help="原样传给 pytest")
    args = ap.parse_args(argv)

    if args.full:
        files, mode, why = (
            sorted(TESTS.glob("test_*.py")),
            "full",
            ["--full 显式指定"],
        )
    elif args.module:
        files, mode, why = (
            select_module(args.module),
            "module",
            [f"--module {args.module}"],
        )
    else:
        changed = changed_files(args.since)
        print(f"[select] 变更 {len(changed)} 文件")
        for p in changed[:12]:
            print(f"    {p}")
        if len(changed) > 12:
            print(f"    ... 另 {len(changed) - 12}")
        mode, files, why = select(changed)

    for w in why:
        print(f"[select] {w}")
    print(f"[select] mode={mode} ⇒ {len(files)} 个测试文件")
    if args.dry_run:
        for f in files:
            print(f"    {f.relative_to(ROOT)}")
        return 0

    import pytest

    return pytest.main(["-q", *[str(f) for f in files], *args.pytest_args])


if __name__ == "__main__":
    raise SystemExit(main())
