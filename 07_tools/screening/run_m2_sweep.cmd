@echo off
rem ============================================================================
rem M2 扫描：可复现的后台跑批（Windows）
rem
rem 启动（在 strategy_team 根目录下，PowerShell）：
rem     Start-Process -WindowStyle Hidden -FilePath "07_tools\screening\run_m2_sweep.cmd"
rem 看进度：
rem     Get-Content -Wait -Tail 40 06_logs\m2_sweep\sweep_run.log
rem 想中止：
rem     Get-Process python | Stop-Process        (会杀掉所有 python，注意别误伤)
rem
rem 为什么要这个脚本，而不是直接在 PowerShell 里跑：
rem   1. **Windows 没有 nohup**，而关掉 PowerShell 窗口会给同一控制台里的子进程发
rem      CTRL_CLOSE_EVENT ⇒ 扫描会被一起带走。`Start-Process` 起的是独立进程，能活下来。
rem   2. **stdout 与 stderr 必须合并**。`[TIME] 加载/评估`、`[MEM] 峰值`、
rem      `[INFO] universe=...`、`[WARN] ...` 全部走 stderr；只重定向 stdout 会把它们丢掉，
rem      而这几行正是判断「加载占比 / 内存峰值 / 宇宙有没有变」的依据。
rem      这里用 `>> log 2>&1` 合并且保序。
rem   3. **第一步单进程焐热 xdxr 缓存**。前复权要经通达信协议逐票取权息；--sample 3000
rem      里约 2000 只是上一轮没碰过的（上一轮抽的是 1000 只），缓存全冷。
rem      若一上来就并行，N 个进程各开一条连接同时取权息，可能被限流甚至拒连
rem      ⇒ 并行不会更快，只会一起失败。
rem   4. **`&&` 串联 = 免费的冒烟测试**。第一步跑通才继续，它已经覆盖了钉宇宙
rem      (--dump-codes → --codes-file)、钉窗口、落盘、报表全链路；配置写错的话 1 小时内
rem      就失败，而不是 6 小时后才发现整夜白跑。
rem ============================================================================

setlocal

rem ---- 可改参数 --------------------------------------------------------------
rem 窗口**两端都要给**：只给 END 钉不住 —— get_ohlcv_table(local_tdx_data:674) 先做
rem df.tail(count)，_load_bars_local 才在之后按日期过滤 ⇒ .day 文件被追加新 bar 时，
rem 窗口既缩水又向前滑动。见 m2_stop_sweep._base_args 注释。
rem 2024-08-01~2026-08-05 约 490 根 K 线，与之前 --count 500 的实际窗口基本等长。
set SAMPLE=3000
set WIN_START=2024-08-01
set WIN_END=2026-08-05
rem JOBS 会被自动收敛：先按 CPU 核数，再按可用内存 / MEM_PER_JOB_MB（留 20% 余量），
rem 降路数时会在日志里打出原因。想更保守就把这里改小。
set JOBS=6
rem ---------------------------------------------------------------------------

set SWEEP=07_tools/screening/m2_stop_sweep.py
set LOG=06_logs\m2_sweep\sweep_run.log
set COMMON=--sample %SAMPLE% --window %WIN_START% %WIN_END% --pin-universe

if not exist 06_logs\m2_sweep mkdir 06_logs\m2_sweep

echo ============================================================>> "%LOG%" 2>&1
echo [%DATE% %TIME%] START  sample=%SAMPLE%  window=%WIN_START%~%WIN_END%  jobs=%JOBS%>> "%LOG%" 2>&1
echo   STEP1: uv run python %SWEEP% %COMMON% --only 00_baseline -j 1>> "%LOG%" 2>&1
echo   STEP2: uv run python %SWEEP% %COMMON% -j %JOBS%>> "%LOG%" 2>&1
echo ============================================================>> "%LOG%" 2>&1

echo [%DATE% %TIME%] STEP1  焐热 xdxr 缓存 + 跑基准（单进程，约 1 小时）>> "%LOG%" 2>&1
uv run python %SWEEP% %COMMON% --only 00_baseline -j 1 >> "%LOG%" 2>&1
set RC1=%errorlevel%
if not "%RC1%"=="0" (
    echo [%DATE% %TIME%] STEP1 失败 exit=%RC1% —— 已中止，未进入并行阶段>> "%LOG%" 2>&1
    echo    排查：宇宙落盘(_universe__*.txt)、TDX_ROOT、xdxr 取权息是否被限流>> "%LOG%" 2>&1
    exit /b %RC1%
)

echo [%DATE% %TIME%] STEP2  全部方案（并行 %JOBS% 路，实际路数见日志里的 [INFO]）>> "%LOG%" 2>&1
uv run python %SWEEP% %COMMON% -j %JOBS% >> "%LOG%" 2>&1
set RC2=%errorlevel%

echo [%DATE% %TIME%] DONE   exit=%RC2%>> "%LOG%" 2>&1
echo    重出报表（不重跑回测）：>> "%LOG%" 2>&1
echo    uv run python %SWEEP% %COMMON% --report-only>> "%LOG%" 2>&1
exit /b %RC2%
