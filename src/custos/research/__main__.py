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
    "evolution_loop": (
        "engine",
        "LLM 因子进化循环：DSL 白名单 + 挖掘/判定双窗 + 轨迹池"
        "（CUSTOS_LLM_* 配置 LLM，或 --mock-llm 演示；别名 evolution）",
    ),
    "exit_campaign": (
        "driver",
        "R37 出场轴进化战役（战役壳）：出场基因组×批次进化 + CTL-1~5 双向停止"
        " + 台账多重比较记账（基准 pct5_trail08，随机臂随行；--resume 续跑）",
    ),
    "exit_c5_terminal": (
        "driver",
        "R37-C5 pre2019 终审终端（判据 v0.273 定稿）：冻结候选 vs pct5_trail08"
        " 配对 bootstrap SE+n 前置，只接受 pre2019 段内窗口（硬拒绝镜像），"
        "只能杀不能确认",
    ),
    "factor_exit_study": (
        "driver",
        "R39 因子×出场交互研究终端：因子连续值×分桶×映射→出场档 80 格全枚举"
        "（首对象=信号日 ADX(14)，档集 K=4 象限策展），主基准=uniform-best，"
        "C1~C4 判据跑数前写死（C4=随机分桶臂 N=50 同预算），pre2019 硬拒绝镜像",
    ),
    "bear_regime_study": (
        "driver",
        "R40 0AMV 空头区间做多全栈研究终端：全量 46 门 ENTRY_GATES × 出场 5 档"
        " =230 格全枚举（_amv_checker 反转仅空头日放行，无映射不放行）；基准="
        "随机入场臂（对等纪律：挖掘窗选型冻结配置带判定窗读数）；C1 不过="
        "untested；pre2019 硬拒绝镜像",
    ),
    "plan_rules_replay": (
        "driver",
        "R41 持仓计划规则离线回放：计划止损 stop_loss_ref（as-of 重算，引擎"
        "信号级 stop_override 钩子）+计划止盈 vs 现行 EXIT_RULES 配对双窗"
        "（现行版主读数 pct10 忠实 hard_loss/副读数 pct7 只报告；两版共用"
        " scale_out_frac=0.5）；C1 不过=untested/C2=Δmargin 双窗正+rdd 相对门"
        "（参照=同窗现行版）/C3=lookback 扰动新子集两版重跑/C4=随机止损价臂"
        " N=50（预算对等=两边都不挑选）；pre2019 硬拒绝+check_reach 到达校验",
    ),
    "window_usage": (
        "diagnostic",
        "判定窗/pre2019 使用台账（v0.321，owner 方法论 review #1）："
        "record_use 每读一次记一行（append-only+每日快照+写失败不炸研究），"
        "报告必写「该窗第 k 次被读」（k 大判读打折——分岔路径下多轮使用的"
        "判定窗不再是样本外）；CLI 查窗口被读次数",
    ),
    "criteria_kit": (
        "diagnostic",
        "判据件单一来源（v0.322，owner 方法论 review #7）：q95（campaign "
        "语义）/ verdict 四态（C1 untested 优先）/ c4_state_of（空池 "
        "indeterminate）/ assemble_c4_pool（v0.317 族重抽至过门臂满 N）——"
        "factor_exit/bear_regime/plan_rules/exit_campaign/score_filter 已"
        "全迁移；rdd 门=strategy_grid、C5 判决=exit_c5_terminal 单源不动",
    ),
    "provenance": (
        "diagnostic",
        "研究产物溯源块（v0.325，owner 方法论 review #8）：git sha+dirty / "
        "OBJECTIVE_VERSION / 判据版本 / 预注册文档 blob hash / 宇宙文件 "
        "sha256 / 数据最后日期 / 完整命令行——各终端报告统一带 provenance "
        "块（溯源失败不炸研究）；CLI 打印当前仓库溯源",
    ),
    "power_mde": (
        "diagnostic",
        "统计功效估算 MDE（v0.326，owner 方法论 review #2）：SE="
        "sqrt(wr(1−wr)/n)·sqrt(2(1−ρ))（下界——payoff 噪声忽略）+ MDE=k×SE——"
        "预注册「功效」节（R42 起新单元必备）的输入件；MDE > 合理效应 ⇒ "
        "跑数前合并桶或改问法",
    ),
    "load_window": (
        "diagnostic",
        "--count 缺省自动推算（v0.328，owner 方法论 review #9）：按窗口起点"
        " busday 交易日+300 预热推算每股加载根数（高估=fail-closed 方向，"
        "check_reach 实测兜底），显式 --count 仍是覆盖通道；纯函数叶子模块"
        "（exit_campaign 等导入不成环）；CLI 打印某起点的推算根数",
    ),
    "cost_sensitivity": (
        "diagnostic",
        "成本副读数（v0.329，owner 方法论 review #6）：25/50bps 双报——"
        "cost_bps 是往返总成本（逐笔 ret 直扣），换档=逐笔 ret 平移解析重算"
        "（不重跑引擎）；Δ 或绝对 margin 跟成本翻号 ⇒ flip=True 标「成本"
        "敏感」判读降权（绝对口径 R40 C2/pre2019 薄 margin 是主战场）",
    ),
    "plan_shadow_review": (
        "driver",
        "#60 持仓计划影子事后打分（判据 C）：台账 1700 口径不一致事件（agree="
        "False 且来源非 default）的 plan vs 现行 N 日持仓层 Δret（N=5 主/10 副；"
        "P0=T+1 首可卖日开盘清仓/P1(P2 同档)=卖半仓/P3=不动，跌停停牌顺延"
        "引擎单源）；均值/符号计数 + plan 更防守事件的 live MAE 副读数；"
        "0 事件非零退出不写产物",
    ),
    "score_c5_terminal": (
        "driver",
        "R36-C5 pre2019 终审终端（score 侧，exit_c5_terminal 同族镜像）：冻结"
        " v0-lattice 基因组（产物自含读取）vs 等倍率基准，日簇配对 bootstrap"
        " + v0.281 CI 三分，只接受 pre2019 段内窗口，只能杀不能确认",
    ),
    "score_filter_study": (
        "study",
        "R36 Phase 4 排序器→过滤器判据：前置剔除尾部 X%（X 网格与 4 测试对象"
        "写死）再让 V0 选 top20——P4-C1~C4 机械读数 + 随机过滤器真零假设"
        "对照 + 位移数/缺值率必报，pre2019 硬拒绝",
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
    "amv_formula_check": (
        "diagnostic",
        "0AMV 论文公式验证：SMA(成交额,10,1)×动量项/1e7×0.835 能否复现 vdat"
        "（CLOSE 变体逐个试；可对上 ⇒ 可摆脱指南针客户端依赖）",
    ),
    "resonance3_study": (
        "study",
        "三面共振（基本面优∧技术强∧0AMV做多）交易层验证：两臂对照"
        "（j_low 基底 vs 共振 gate，PIT as-of；⚠️ R21：画像≠可交易）",
    ),
    "signal_context_study": (
        "study",
        "8 研究信号 × 三上下文维度（三面共振/空头前哨/技术高分）组内外加值验证："
        "离线读 strategy_grid 格子逐笔（不重跑回测），两窗同向预注册判读",
    ),
    "score_calibration_study": (
        "study",
        "R24 打分校准：逐腿边际分析（池内命中率/add-one/LOO margin）——"
        "离线读 trades JSON 零回测（--ablation --from-trades；⚠️ pre2019 只读不调参）",
    ),
    "score_stability_study": (
        "study",
        "R29 打分重建（预注册）：篮子胜率稳定≥40% 且盈亏比≥2.4——W1-W4 四候选"
        "（权重向 pre2019 不萎缩腿倾斜）× R29-C1~C4 判据（--phase2/--phase3 "
        "--from-trades；⚠️ pre2019 终审前不许碰，CLI 硬拒绝）",
    ),
    "score_combo_search_study": (
        "study",
        "R30 打分权重有界组合搜索（预注册）：5 正腿×5 档×负腿块二态 = 5702 组合"
        "（gcd 排序等价去重）× 加严筛选线 45%/2.6 + 灵敏度零翻转，幸存者 top 3 进"
        " pre2019 终审（--search/--final --from-trades；⚠️ 搜索族证据等级封顶 L3−）",
    ),
    "score_evolution_study": (
        "study",
        "R34 打分基因组裁决（TODO #61 预注册）：腿集合×权重格基因组编译成单条 DSL "
        "表达式（TS_RANK 归一加权和）复用 strategy_grid 单元格——双窗/±50% 灵敏度/"
        "随机臂对照全确定性；增量在新轴不在调权（⚠️ pre2019 终审段相交即拒跑）；"
        "--v0-lattice = R36 Phase 3 调权路（V0 计分键 × 倍率格，collect/score 拆分）",
    ),
    "b1_marks_v0_study": (
        "study",
        "R36 完美 B1 买点的 live 八段（V0 技术分）口径对照——全历史 as-of V0 "
        "× 同日全宇宙分布分位（诊断指标非判据；V0 臂机制同 score_evolution_study）",
    ),
    "b1_perfect_dataset": (
        "study",
        "R36 完美 B1 正例数据集 Phase 0 审计打印（B1_DATA 10 例接入审计；"
        "发现级材料 L1，验证走全宇宙双窗——详见 R36 预注册）",
    ),
    "indicator_cache": (
        "diagnostic",
        "指标盘缓存盘点（条目/占用/按电池分布；缓存本体由 backtest_factors "
        "--indicator-cache 生产——研究侧加速，live 不可用）",
    ),
    "factor_ic_profile": (
        "study",
        "因子 IC 画像（分诊镜）：SCORERS 19 键 + DSL 表达式的截面 RankIC/ICIR 与 "
        "horizon 衰减全因子可比表——回答「还有没有截面信号、值不值得花 trade-sim "
        "预算」（⚠️ 非晋级判据：晋级走双窗+三轴；读数 L3− 带幸存者偏差）",
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
    "random_baseline_study": (
        "study",
        "随机 DSL 表达式 baseline 裁决实验（R32/#71：LLM 增量证据的对照组；"
        "纯确定性不烧 token）",
    ),
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

# 短名别名：包目录 research/evolution/ 已占用 "evolution"（注册键必须等于
# 文件名，见 _listing 的存在性检查），CLI 文件叫 evolution_loop.py，给入口留短名。
ALIASES = {
    "evolution": "evolution_loop",
    "random_baseline": "random_baseline_study",
    "b1_marks_v0": "b1_marks_v0_study",
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
    name, rest = ALIASES.get(args[0], args[0]), args[1:]
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
