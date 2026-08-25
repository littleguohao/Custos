# -*- coding: utf-8 -*-
"""研究/回测的**统一入口**。

用法（沿用项目既有约定「按路径调脚本」；custos 已可编辑安装，不需要设 PYTHONPATH）::

    uv run python src/custos/research/__main__.py                    # 列出全部工具与状态
    uv run python src/custos/research/__main__.py backtest_factors --help
    uv run python src/custos/research/__main__.py m2_stop_sweep --sample 300

也支持包形式（等价）::

    uv run python -m custos.research

## 为什么是「一个入口 + 分发」，而不是「合并成一个回测脚本」

owner 2026-08-07 问「总的回测和研究是否可以统一到一个入口」。实测 14 个脚本、
9002 行，其中两个引擎各 ~2000 行。**合并是错的**，三个理由：

  ① 合并 `backtest_factors`(2051) + `launch_point_study`(1898) = 一个 4000 行文件，
     比现在难读。
  ② `m2_stop_sweep` 与 `adjust_diagnostic` 是**故意用 subprocess** 调
     `backtest_factors` 的（内存隔离 —— 那个回测本来就常被 OOM Kill，
     见 `m2_stop_sweep` 的 `MEM_PER_JOB_MB` 注释）。合进一个进程会毁掉这层隔离。
  ③ 单进程入口要 import 全部依赖（pandas/numpy/factors 全套），
     启动变慢，且一个脚本的 import 错误会让**所有**研究工具用不了。

所以真正的痛点不是「入口太多」，而是：

  · **发现性** —— 14 个文件没有索引，得先知道该跑哪个
  · **模式藏在 flag 里** —— `launch_point_study` 有 **17 个** `store_true` 开关，
    `backtest_factors` 有 11 个，它们本质是**互斥的模式**却被塞进 flag，
    看 `--help` 分不清哪个是模式、哪个是选项
  · **存废不明** —— 曾有 3 个脚本覆盖率 0%；已定案**全部删除**
    （原待办 #44「先判存废」，owner 2026-08-12 定案，commit 6c290c6），
    stale 标记机制保留（标 stale 会在列表与运行时提醒）

这个入口解决前两条（列表 + 每个工具的模式清单），第三条靠下面的 `STATUS` 显式登记
—— 项目原则是「不可用的东西要标记出来，且标记要代码级生效」，
而 `TOOLS` 表就是那个代码级标记。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

# GBK（cp936）终端/管道打不了 ⚠️/⛔ 等符号 —— 不 reconfigure 会 UnicodeEncodeError
# 直接退出。惯例同 technical_monitor（stdout 与 stderr 都要，⚠️ 往 stderr 打）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
BASE = HERE.parents[2]

# ── 研究工具注册表。`status` 的含义：
#     engine    核心引擎，其他工具驱动它
#     driver    驱动引擎做批量/扫描
#     study     独立研究，产出结论进 governance/research/
#     diagnostic 诊断/对账工具，产出报告供治理文档填数
#     stale     **存废待定**（覆盖率 0% 或长期未动）——机制保留；首批三个已于 2026-08-12 按 #44 定案删除
TOOLS: dict[str, tuple[str, str]] = {
    "backtest_factors": (
        "engine",
        "S_shape 因子走查回测（walk-forward）；11 个模式开关",
    ),
    "launch_point_study": ("engine", "起涨点 vs 0AMV regime 研究；**17 个模式开关**"),
    "m2_stop_sweep": (
        "driver",
        "M2 机制类改进扫描：分组跑对照并自动判定（subprocess 调 engine）；"
        "宇宙/窗口默认已钉死（#17，--no-* 显式关）",
    ),
    "run_bear_to_long_study": (
        "driver",
        "空头段识别未来赢家：枚举窗口对 → Pass1 → 跨窗 Pass2",
    ),
    "strategy_grid": (
        "driver",
        "因子 × 出场联合寻优：网格 = {scorer × entry_gate} × 出场轴，"
        "两阶段 top-k + --max-runs 预算（subprocess 调 engine；"
        "出场参数与 EXIT_RULES.json 同 schema，优胜配置可拷回 live）",
    ),
    "backtest_0amv_bear_regime": (
        "study",
        "0AMV 空头区间「只卖不买 + 反弹减仓」历史回测",
    ),
    "analyze_winner_features": (
        "study",
        "赢家特征反向研究：MACD/KDJ/DMI 在信号当时的判别力",
    ),
    "scan_signals_ytd": ("study", "年内信号扫描（reversal_k 事件 + 板块相位）"),
    "analyze_trades": ("study", "交易记录复盘分析（台账统计）"),
    "b1_fingerprint_study": (
        "study",
        "优秀 B1 指纹证据层回测（B1_DATA 正例召回与后续收益；R18）",
    ),
    "sector_inflow_study": (
        "study",
        "#26 活跃板块（多次上榜）× J<13 池：命中 vs 未命中 forward 收益对照",
    ),
    "score_return_study": (
        "study",
        "0AMV做多区间 J<13 信号：live技术分 vs BBI止盈收益相关性"
        "（⚠️ R11：读数仅供相对排序）",
    ),
    "winner_factor_study": (
        "study",
        "赢家半场因子富集：top-50% 票的 J<13 信号日单因子命中面板"
        "（复用 score_return_study 基建；⚠️ R3 纪律：须过半窗一致性）",
    ),
    "score_variants_study": (
        "study",
        "打分重构：V0~V3 变体（反向腿取反/证据重构/负向证据）× 预注册判据"
        "——TOP20% 赢家能否在得分上浮现（⚠️ R21：以篮子实测为准）",
    ),
    "qsx_resonance_study": (
        "study",
        "QSX/DKX 两层过滤三臂：①QSX>DKS 多头 ②「跌线就反弹」共振"
        "（出场=stop12+保本05+双中大阳分批+跌破QSX清仓，不用 BBI 清仓；⚠️ R11）",
    ),
    "adjust_diagnostic": (
        "diagnostic",
        "复权口径诊断：量化未复权数据对回测与选股的影响",
    ),
    "probe_data_sources": ("diagnostic", "数据源探针：实测可用性/耗时/返回形状"),
    # ⚠️ stale 状态保留给未来用：首批三个（compare_signal_sets /
    #    scan_signal_backtest / m2_migrate_fingerprint）2026-08-12 已按
    #    待办 #44 owner 定案**删除**（机制保留：标 stale 会在列表与运行时提醒）。
}
ORDER = ["engine", "driver", "study", "diagnostic", "stale"]
LABEL = {
    "engine": "引擎",
    "driver": "驱动",
    "study": "研究",
    "diagnostic": "诊断",
    "stale": "⚠️ 存废待定",
}


def _modes(name: str) -> list[str]:
    """从源码里抽出 `store_true` 开关 —— 它们是这个工具的**模式**。

    ⚠️ 用 AST 而不是正则：正则 `[^)]*` 在格式化折行/嵌套括号下会漏匹配
    （ruff format 统一排版后模式清单曾整段消失）。
    """
    import ast

    tree = ast.parse((HERE / f"{name}.py").read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            continue
        if not any(
            kw.arg == "action"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value == "store_true"
            for kw in node.keywords
        ):
            continue
        for a in node.args:
            if (
                isinstance(a, ast.Constant)
                and isinstance(a.value, str)
                and a.value.startswith("--")
            ):
                out.add(a.value.removeprefix("--"))
    return sorted(out)


def _listing() -> int:
    print("研究 / 回测工具\n")
    print("  uv run python src/custos/research/__main__.py <名字> [参数...]\n")
    for kind in ORDER:
        names = [n for n, (k, _) in TOOLS.items() if k == kind]
        if not names:
            continue
        print(f"── {LABEL[kind]}")
        for n in names:
            missing = "" if (HERE / f"{n}.py").exists() else "   ⛔ 文件不存在"
            print(f"   {n:<28}{TOOLS[n][1]}{missing}")
            if (HERE / f"{n}.py").exists():
                ms = _modes(n)
                if len(ms) >= 4:
                    print(f"   {'':<28}模式（{len(ms)}）: {', '.join(ms)}")
        print()
    print("提示：模式开关是**互斥的运行模式**，不是普通选项 —— 先看这里再看 --help。")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in {"-h", "--help", "list"}:
        return _listing()
    name, rest = args[0], args[1:]
    if name not in TOOLS:
        print(f"未登记的工具: {name}\n", file=sys.stderr)
        _listing()
        return 2
    script = HERE / f"{name}.py"
    if not script.exists():
        print(f"⛔ 注册表里有 {name} 但文件不存在: {script}", file=sys.stderr)
        return 2
    if TOOLS[name][0] == "stale":
        print(
            f"⚠️ {name} 标记为**存废待定**（覆盖率 0% 或长期未动）——"
            f"结论不要直接采信。\n",
            file=sys.stderr,
        )
    # ⚠️ 用 subprocess 而不是 import：保住 m2_stop_sweep / adjust_diagnostic
    # 依赖的**内存隔离**（那个回测常被 OOM Kill），也让一个工具的 import 错误
    # 不会波及其余工具。cwd 固定到仓库根 —— 研究脚本的相对路径都以它为基准。
    return subprocess.run(
        [sys.executable, str(script), *rest], cwd=str(BASE)
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
