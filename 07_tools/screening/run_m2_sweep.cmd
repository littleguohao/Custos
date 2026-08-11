@echo off
rem ============================================================================
rem M2 扫描：可复现的后台跑批（Windows）
rem
rem 启动（在 strategy_team 根目录下，PowerShell）：
rem     Start-Process -WindowStyle Hidden -FilePath "07_tools\screening\run_m2_sweep.cmd"
rem 看进度（**必须加 -Encoding UTF8**，见下方第 5 条）：
rem     Get-Content -Wait -Tail 40 -Encoding UTF8 artifacts\logs\m2_sweep\sweep_run.log
rem 想中止：
rem     Get-Process python | Stop-Process        (会杀掉所有 python，注意别误伤)
rem
rem ⚠️ **扫描运行期间不要 git pull、不要编辑本文件**：cmd.exe 执行批处理是按**字节偏移
rem    增量读取**的，文件在运行中被改写会导致它从新内容的旧偏移处继续解析，后半段变成
rem    乱命令。等跑完再更新。
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
rem   5. **编码**（实测踩过）：Python 侧 `sys.stdout.reconfigure(encoding="utf-8")` 写的是
rem      UTF-8，而中文 Windows 控制台默认代码页是 936(GBK) ⇒ 日志里混了两种编码，
rem      `Get-Content` 又默认按 ANSI 读 ⇒ 满屏乱码。两道措施：
rem        · 这里 `chcp 65001` 把控制台切成 UTF-8，让 cmd 的 echo 与 Python 输出同编码；
rem        · 本文件所有 **echo 行一律用纯 ASCII**（中文只留在 rem 注释里，rem 不会被打印）
rem          —— 批处理文件里带非 ASCII 本身就脆（GBK 的 trail byte 范围含 0x40-0x7E，
rem          UTF-8 多字节序列被按 GBK 解析时可能吞掉后面的 ASCII 字符）。
rem      读日志时仍要 `-Encoding UTF8`。
rem ============================================================================

chcp 65001 >nul 2>&1
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
set LOG=artifacts\logs\m2_sweep\sweep_run.log
set COMMON=--sample %SAMPLE% --window %WIN_START% %WIN_END% --pin-universe

if not exist artifacts\logs\m2_sweep mkdir artifacts\logs\m2_sweep

echo ============================================================>> "%LOG%" 2>&1
echo [%DATE% %TIME%] START  sample=%SAMPLE%  window=%WIN_START%..%WIN_END%  jobs=%JOBS%>> "%LOG%" 2>&1
echo   STEP1: uv run python %SWEEP% %COMMON% --only 00_baseline -j 1>> "%LOG%" 2>&1
echo   STEP2: uv run python %SWEEP% %COMMON% -j %JOBS%>> "%LOG%" 2>&1
echo ============================================================>> "%LOG%" 2>&1

echo [%DATE% %TIME%] STEP1  warm xdxr cache + baseline (single process, ~1h)>> "%LOG%" 2>&1
uv run python %SWEEP% %COMMON% --only 00_baseline -j 1 >> "%LOG%" 2>&1
set RC1=%errorlevel%
if not "%RC1%"=="0" (
    echo [%DATE% %TIME%] STEP1 FAILED exit=%RC1% - aborted, STEP2 not started>> "%LOG%" 2>&1
    echo    check: _universe__*.txt written? TDX_ROOT set? xdxr fetch throttled?>> "%LOG%" 2>&1
    exit /b %RC1%
)

echo [%DATE% %TIME%] STEP2  all schemes (jobs=%JOBS%; actual count see [INFO] lines)>> "%LOG%" 2>&1
uv run python %SWEEP% %COMMON% -j %JOBS% >> "%LOG%" 2>&1
set RC2=%errorlevel%

echo [%DATE% %TIME%] DONE   exit=%RC2%>> "%LOG%" 2>&1
echo    re-render report without re-running backtests:>> "%LOG%" 2>&1
echo    uv run python %SWEEP% %COMMON% --report-only>> "%LOG%" 2>&1
exit /b %RC2%
