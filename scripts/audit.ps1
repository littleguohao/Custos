# 静态检查套件（Windows）—— 每次大改之后跑一遍。
# 报告输出到 reports\（已 gitignore，不入库）。工具经 `uv run --with` 临时注入。
#
# 用法（PowerShell）：
#   scripts\audit.ps1              # 全量（含 mypy，首次约 5-10 分钟）
#   scripts\audit.ps1 -SkipMypy    # 快速版
param([switch]$SkipMypy)

Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force reports | Out-Null

Write-Host "[0/6] ruff format --check（代码风格门槛）..."
uv run --with ruff ruff format src/ tests/ --check 2>&1 |
    Out-File -Encoding utf8 "reports\ruff_format.txt"
if ($LASTEXITCODE -ne 0) {
    Write-Host "      ✗ 格式不统一 —— 先跑 ``uv run --with ruff ruff format src/ tests/``"
    Write-Host "      明细 -> reports\ruff_format.txt"
    exit 1
}
Write-Host "      ✓ 格式统一"

function Invoke-Audit($name, $outfile, [scriptblock]$cmd) {
    Write-Host "$name ..."
    & $cmd 2>&1 | Out-File -Encoding utf8 $outfile
    Write-Host "      -> $outfile"
}

Invoke-Audit "[1/6] pylint（W/E/design）" "reports\pylint_design.txt" {
    uv run --with pylint pylint src/ --disable=C,R --enable=W,E,design --reports=y
}
Invoke-Audit "[2/6] radon cc（圈复杂度，C 级以上）" "reports\radon_cc.txt" {
    uv run --with radon radon cc src -s -n C
}
Invoke-Audit "[3/6] radon mi（可维护性指数）" "reports\radon_mi.txt" {
    uv run --with radon radon mi src
}
Invoke-Audit "[4/6] vulture（疑似死代码，需人工甄别，paths 常量等 API 面是误报）" "reports\vulture_deadcode.txt" {
    uv run --with vulture vulture src
}

if ($SkipMypy) {
    Write-Host "[5/6] mypy —— 跳过（-SkipMypy）"
} else {
    Invoke-Audit "[5/6] mypy（类型检查，首次较慢）" "reports\mypy_type.txt" {
        uv run --with mypy mypy src/
    }
}

Write-Host "[6/6] pydeps（依赖图）..."
if (Get-Command dot -ErrorAction SilentlyContinue) {
    uv run --with pydeps pydeps src/custos -o reports/dependency_graph.svg --noshow | Out-Null
    Write-Host "      -> reports\dependency_graph.svg"
} else {
    uv run --with pydeps pydeps src/custos --show-deps --noise-level 0 2>$null |
        Out-File -Encoding utf8 "reports\dependency_graph.json"
    Write-Host "      无 graphviz dot，只导出依赖数据 -> reports\dependency_graph.json"
}

Write-Host "完成。报告在 reports\ 下。"
