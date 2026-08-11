#!/usr/bin/env bash
# 静态检查套件 —— 每次大改之后跑一遍。
# 报告输出到 reports/（已 gitignore，不入库）。工具经 `uv run --with` 临时注入。
#
# 用法：
#   scripts/audit.sh              # 全量（含 mypy，首次约 5-10 分钟）
#   scripts/audit.sh --skip-mypy  # 快速版
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p reports

SKIP_MYPY=0
[ "${1:-}" = "--skip-mypy" ] && SKIP_MYPY=1

echo "[0/6] ruff format --check（代码风格门槛）..."
if ! uv run --with ruff ruff format src/ tests/ --check > reports/ruff_format.txt 2>&1; then
    echo "      ✗ 格式不统一 —— 先跑 \`uv run --with ruff ruff format src/ tests/\`"
    echo "      明细 → reports/ruff_format.txt"
    exit 1
fi
echo "      ✓ 格式统一"

echo "[1/6] pylint（W/E/design）..."
uv run --with pylint pylint src/ --disable=C,R --enable=W,E,design --reports=y \
    > reports/pylint_design.txt 2>&1
echo "      exit=$?（非零代表有发现，正常）→ reports/pylint_design.txt"

echo "[2/6] radon cc（圈复杂度，C 级以上）..."
uv run --with radon radon cc src -s -n C > reports/radon_cc.txt 2>&1

echo "[3/6] radon mi（可维护性指数）..."
uv run --with radon radon mi src > reports/radon_mi.txt 2>&1

echo "[4/6] vulture（疑似死代码）..."
uv run --with vulture vulture src > reports/vulture_deadcode.txt 2>&1
echo "      exit=$?（3=有疑似项，需人工甄别，paths 常量等 API 面是误报）"

if [ "$SKIP_MYPY" -eq 0 ]; then
    echo "[5/6] mypy（类型检查，首次较慢）..."
    uv run --with mypy mypy src/ > reports/mypy_type.txt 2>&1
    echo "      exit=$? → reports/mypy_type.txt"
else
    echo "[5/6] mypy —— 跳过（--skip-mypy）"
fi

echo "[6/6] pydeps（依赖图）..."
if command -v dot >/dev/null 2>&1; then
    uv run --with pydeps pydeps src/custos -o reports/dependency_graph.svg --noshow \
        > /dev/null 2>&1
    echo "      → reports/dependency_graph.svg"
else
    uv run --with pydeps pydeps src/custos --show-deps --noise-level 0 \
        > reports/dependency_graph.json 2>/dev/null
    echo "      无 graphviz dot，只导出依赖数据 → reports/dependency_graph.json"
fi

echo "完成。报告在 reports/ 下。"
