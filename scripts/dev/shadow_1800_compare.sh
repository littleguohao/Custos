#!/usr/bin/env bash
# 1800 候选表标注口径影子对照（TODO #76① 收集机制；逻辑在 shadow_1800_compare.py，
# 用法与共享数据约束见其 docstring）。生产机每日 1800 跑完后执行：
#   bash scripts/dev/shadow_1800_compare.sh --date YYYY-MM-DD
set -euo pipefail
exec uv run python "$(dirname "$0")/shadow_1800_compare.py" "$@"
