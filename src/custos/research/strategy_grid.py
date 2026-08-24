#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""因子 × 出场 联合寻优驱动器（因子×止盈×止损架构 Phase E，v0.85）。

## 默认研究基底（owner 2026-08-21 定，v0.93）

**0AMV 做多区间 + J<13 是默认研究因子，钉死、不再作为扫描变量**：
每个格子默认带 ``--amv-long-only``（``--no-amv-pin`` 可显式解除，仅用于
对照实验），gate 轴默认只含 ``j_low`` 及其**叠加变体**（j_low_adx25 /
j_low_rsi_strong 等「J<13 ∧ 其他因子」组合）——扫描的是基底之上的其他
因子 × 止损 × 止盈搭配，不是基底本身。

网格 = 因子轴 ``{scorer × entry_gate}`` × 出场轴
``{stop_mode / stop_pct / trail / breakeven / scale_out / cost_zone /
time_stop / bbi_consec}``，
每个格子是一次 ``backtest_factors --trade-sim`` 子进程（驱动模式复用
``m2_stop_sweep``：串行子进程 + 结果文件落盘 + 签名复用跳过）。

## 研究→live 回流通道

出场轴参数的键名与 ``simulate_b1_trade`` 形参、``governance/contracts/
EXIT_RULES.json`` 的 params 键名**三者一致**（``breakeven_trigger`` /
``trail_pct`` / ``time_stop_bars`` / ``cost_zone_bars`` / ``cost_zone_pct`` …）。
报告里每组优胜配置附带 ``exit_rules`` 块（rule_id/enabled/params 结构），
可直接拷入 EXIT_RULES.json 对应节。

## 复用机制

结果文件名带 ``cell_signature``——该格子**完整 CLI 参数 + 宇宙摘要**的
sha1 短哈希。CLI 参数相同 ⇒ 逐笔口径相同 ⇒ 已存在的结果文件直接复用跳过
（m2 的 ``trades_signature`` 思路，提升到驱动层：连组合层参数也进哈希，更保守）。

⚠️ **隐式窗口/宇宙转显式**（m2 文档化过三次的「同签名静默混口径」事故类）：
默认 ``--count 500`` 是滚动窗口，随新 K 线每天漂移；``--universe-sample``
抽样基数随新票上市漂移。所以未显式给 ``--start/--end`` 时，驱动先按数据最后
交易日把窗口**换算成具体日期**再进 CLI 与签名；未给 ``--codes-file`` 时跑一次
``--dump-codes`` 探针，把宇宙 codes 的 sha1 短哈希（口径同 backtest_factors
的 ``codes_digest``）混进签名。⇒ 隔天数据漂移 ⇒ 签名变 ⇒ 旧格不被误复用。
解析失败（无数据环境）降级为不钉 + 报告 ``data_quality`` 明示警告，不拦着实跑。

``--force`` 强制重跑。

## 剪枝

- ``--max-runs N``：实际子进程调用次数的预算上限（复用跳过不占预算）；
  网格展开超过预算时按顺序截断，被截断的格子在报告中列明。
- 两阶段（``--top-k K``）：第一阶段只跑因子轴 × 出场轴**第一档**（粗网格，
  出场轴第一项应为基准配置），按目标函数取 top-K 个因子组合；第二阶段只对
  这 K 个组合展开全部出场档（粗 ⇒ 细）。K ≥ 因子组合数时退化为单阶段全网格。
  ⚠️ 两阶段是「出场参数对因子排序影响不大」的近似——加速手段，不是无损失剪枝。

## 目标函数

``objective = w_margin·margin + w_expR·expectancy_R + w_rdd·(return/maxdd)``
（权重 ``--obj-weights``，默认 1,1,0.05）。margin = 胜率 − 盈亏平衡胜率
``1/(1+payoff)``，复用 m2 的 ``_margin``/``_breakeven_wr`` 口径；
return/maxdd 复用 m2 的 ``_ret_over_dd``（每格带 ``--portfolio`` 跑出组合块）。
三项任一缺失 ⇒ objective=None **垫底**（缺失按 0 会把缺失格排在真实负值之上）。
⚠️ 三项量纲不同，objective 是**排序启发式**，不是经济含义明确的指标。

⚠️ **scorer 轴只在 ``--top-n > 0`` 时有区分度**：非 collect_all 的 trade-sim
逐笔由 entry_gate 决定，scorer 只影响 top-N 横截面择优的排序。

⚠️ **R11 未决**：基准已实现口径为负 ⇒ 目标函数读数可信度受限
（报告头部固定标注，措辞见 ``R11_WARNING``）。

用法：
    uv run python src/custos/research/strategy_grid.py --sample 300 --max-runs 8
    uv run python src/custos/research/strategy_grid.py \\
        --scorers b1_dual,kdj_j --gates j_low,j_macd_turn \\
        --exit-grid my_exits.json --top-k 2 --max-runs 20
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time
from typing import Any, Optional

from custos.core.paths import LOGS
from custos.research.m2_stop_sweep import (
    SCRIPT,
    _breakeven_wr,
    _load,
    _margin,
    _ret_over_dd,
)

BASE = pathlib.Path(__file__).resolve().parents[3]
OUTDIR = LOGS / "strategy_grid"

DEFAULT_SAMPLE = 300  # 联合寻优是格子 × 单次回测，默认样本比 m2（1000）小
DEFAULT_SCORERS = ["b1_dual", "kdj_j"]
# v0.93（owner）：J<13 是默认研究基底 ⇒ gate 轴 = j_low（基底行）+「J<13 ∧
# 其他因子」叠加变体（其他因子的优化对象）；不再扫非 j_low 系 gate。
DEFAULT_GATES = ["j_low", "j_low_adx25", "j_low_rsi_strong"]

# 出场轴可扫参数：键名 == simulate_b1_trade 形参 == EXIT_RULES.json params 键名。
# 值 = 对应 backtest_factors CLI flag。
EXIT_PARAM_FLAGS: dict[str, str] = {
    "stop_mode": "--stop-mode",
    "stop_pct": "--stop-pct",
    "trail_pct": "--trail",
    "breakeven_trigger": "--breakeven",
    "scale_out_frac": "--scale-out",
    "cost_zone_bars": "--cost-zone-bars",
    "cost_zone_pct": "--cost-zone-pct",
    "time_stop_bars": "--time-stop",
    "bbi_exit_consec": "--bbi-consec",
}
# cost_zone_grace 是 EXIT_RULES schema 的一部分，但 backtest_factors CLI 没有
# 对应 flag（simulate_b1_trade 形参默认值 1）⇒ 只允许显式给默认 1，其余报错。
CLI_ABSENT_PARAMS: dict[str, Any] = {"cost_zone_grace": 1}

# 出场参数 → EXIT_RULES.json 的（节, rule_id, 启用判据参数）。
# 不在表内的参数（stop_mode/stop_pct/scale_out_frac/bbi_exit_consec）是
# **研究侧独有**——live 的 EXIT_RULES schema 没有对应节，回流时进
# exit_rules 块的 "research_only" 注记，不伪造 rule_id。
RULE_MAP: dict[str, tuple[str, str, str]] = {
    "breakeven_trigger": ("stop_rules", "breakeven_stop", "breakeven_trigger"),
    "trail_pct": ("stop_rules", "trailing_stop", "trail_pct"),
    "time_stop_bars": ("stop_rules", "time_stop", "time_stop_bars"),
    "cost_zone_bars": ("take_profit_rules", "cost_zone_flat", "cost_zone_bars"),
    "cost_zone_pct": ("take_profit_rules", "cost_zone_flat", "cost_zone_bars"),
    "cost_zone_grace": ("take_profit_rules", "cost_zone_flat", "cost_zone_bars"),
}

# 默认出场网格（粗网格；第一项 = 基准档，两阶段的第一阶段只跑它）。
# 口径参照 m2 实测结论：stop_mode=low 是不可执行的基准；pct 5% 是期望% 最优档。
DEFAULT_EXIT_GRID: list[dict[str, Any]] = [
    {"name": "base_low", "params": {}},  # stop_mode=low（backtest 默认），纯基准
    {"name": "pct5", "params": {"stop_mode": "pct", "stop_pct": 5}},
    {
        "name": "pct5_trail08",
        "params": {"stop_mode": "pct", "stop_pct": 5, "trail_pct": 0.08},
    },
    {
        "name": "pct5_be05",
        "params": {"stop_mode": "pct", "stop_pct": 5, "breakeven_trigger": 0.05},
    },
    {
        "name": "pct5_cz3",
        "params": {"stop_mode": "pct", "stop_pct": 5, "cost_zone_bars": 3},
    },
]

DEFAULT_OBJ_WEIGHTS = (1.0, 1.0, 0.05)  # margin / expectancy_R / return_over_maxdd

CELL_TIMEOUT_S = 1800  # 单格子进程超时（秒）；--timeout 可调，超时计 failed
# 隐式窗口换算的交易日历参照票：长历史、几乎不停牌（读本地 vipdoc，纯文件解析不联网）
CAL_REF_CODE = "600000"

# 报告头部固定标注（R11，见 governance/research/README.md：
# 「在 R11 的问题解决前，任何 CAGR/期望数字都不应被引用」）。
R11_WARNING = (
    "⚠️ **R11 未决**：基准已实现口径为负"
    "（governance/research/R11_baseline_margin_collapse.md）。"
    "在 R11 解决前，本报告的目标函数读数（margin / expectancy_R / 收益回撤比）"
    "**可信度受限，仅用于相对排序参考，不得引用绝对量级**（CAGR/期望数字）。"
)

SCHEMA_VERSION = "v2"  # v2：新增 data_quality 块（窗口/宇宙口径 + 警告）


# ---------- 网格展开与出场参数 ----------


def expand_grid(
    scorers: list[str], gates: list[str], exits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """网格 = 因子轴 × 出场轴的笛卡尔积（组合数 = 三轴长度相乘）。

    实现收敛在 ``_cells_for``（两阶段调度也用它），这里只是全组合的便捷封装——
    展开逻辑只此一份，避免改一漏一。
    """
    return _cells_for(_factor_combos(scorers, gates), exits)


def validate_exit_grid(exits: list[Any]) -> Optional[str]:
    """校验出场网格 schema；合法返回 None，否则返回错误文案。"""
    if not isinstance(exits, list) or not exits:
        return "出场网格必须是非空 list"
    seen: set[str] = set()
    for e in exits:
        if not isinstance(e, dict) or not e.get("name"):
            return "每个出场档必须是含 name 的 dict"
        if e["name"] in seen:
            return f"出场档 name 重复: {e['name']}"
        seen.add(e["name"])
        params = e.get("params") or {}
        if not isinstance(params, dict):
            return f"{e['name']}: params 必须是 dict"
        for k, v in params.items():
            if k in CLI_ABSENT_PARAMS:
                if v != CLI_ABSENT_PARAMS[k]:
                    return (
                        f"{e['name']}: {k}={v} 无对应 CLI 参数，"
                        f"只允许默认值 {CLI_ABSENT_PARAMS[k]}"
                    )
            elif k not in EXIT_PARAM_FLAGS:
                return f"{e['name']}: 未知出场参数 {k!r}（允许: {sorted(EXIT_PARAM_FLAGS)}）"
        if params.get("stop_mode") not in (None, "low", "pct", "platform"):
            return f"{e['name']}: stop_mode 只允许 low/pct/platform"
    return None


def exit_cli_args(params: dict[str, Any]) -> list[str]:
    """出场参数 → backtest_factors CLI flag 段（键序固定，保证签名稳定）。"""
    args: list[str] = []
    for k in EXIT_PARAM_FLAGS:  # 按定义序，不按 dict 序
        if k in params:
            args += [EXIT_PARAM_FLAGS[k], str(params[k])]
    return args


def exit_params_to_rules(params: dict[str, Any]) -> dict[str, Any]:
    """出场参数 → EXIT_RULES.json 同 schema 的块（研究→live 回流通道）。

    结构 ``{stop_rules: {rule_id: {rule_id, enabled, params}}, take_profit_rules:
    {...}, research_only: {...}}``。enabled = 该规则的启用判据参数非零
    （如 trail_pct>0 ⇒ trailing_stop enabled）。研究侧独有的参数
    （stop_mode/stop_pct/scale_out_frac/bbi_exit_consec）进 research_only
    注记——live schema 没有对应节，不伪造。
    """
    rules: dict[str, dict[str, Any]] = {}
    research_only: dict[str, Any] = {}
    for k, v in sorted(params.items()):
        mapping = RULE_MAP.get(k)
        if mapping is None:
            research_only[k] = v
            continue
        section, rule_id, enabler = mapping
        rule = rules.setdefault(
            rule_id,
            {"section": section, "rule_id": rule_id, "enabled": False, "params": {}},
        )
        rule["params"][k] = v
        if k == enabler and v:
            rule["enabled"] = True
    out: dict[str, Any] = {"stop_rules": {}, "take_profit_rules": {}}
    for rule in rules.values():
        section = rule.pop("section")
        out[section][rule["rule_id"]] = rule
    if research_only:
        out["research_only"] = research_only
    return out


# ---------- 驱动（复用 m2 模式：子进程 + 落盘 + 签名复用）----------


def _universe_args(a: argparse.Namespace) -> list[str]:
    """宇宙参数透传（--codes-file 钉死优先 > 默认 local vipdoc）。"""
    if a.codes_file:
        return ["--codes-file", a.codes_file]
    return ["--universe-local", "--universe-sample", str(a.sample)]


def _cell_args(a: argparse.Namespace, cell: dict[str, Any]) -> list[str]:
    """一个格子的完整 backtest_factors CLI（不含 --out；签名对全量求哈希）。"""
    args = [
        "--trade-sim",
        "--entry-filter",
        cell["gate"],
        "--scorer",
        cell["scorer"],
        "--cost-bps",
        str(a.cost_bps),
    ]
    # v0.93（owner）：0AMV 做多区间是默认研究基底，钉死进每个格子；
    # --no-amv-pin 仅用于对照实验时显式解除。
    if not a.no_amv_pin:
        args += ["--amv-long-only"]
    args += _universe_args(a)
    if a.start:
        args += ["--start", a.start]
    if a.end:
        args += ["--end", a.end]
    args += ["--count", str(a.count)]
    if a.top_n:
        args += ["--top-n", str(a.top_n)]
    args += exit_cli_args(cell["params"])
    args += ["--portfolio"]  # 目标函数要 return/maxdd ⇒ 每格都带组合层
    return args


def _udigest(a: argparse.Namespace) -> str:
    """main 解析出的宇宙摘要（未解析 ⇒ 空串，签名退化为纯 CLI 口径 + 报告警告）。"""
    return str(getattr(a, "universe_digest", "") or "")


def cell_signature(cli_args: list[str], universe_digest: str = "") -> str:
    """格子签名 = （完整 CLI 参数 + 宇宙摘要）的 sha1 短哈希。

    CLI 参数相同 ⇒ ``trades_signature`` 必然相同（前者是后者的超集，连组合层
    参数也在内）⇒ 已落盘的结果可直接复用。比 m2 的指纹更保守。
    ⚠️ 隐式窗口必须在进来**之前**已由 ``_pin_context`` 换算成显式 ``--start/--end``
    （否则 ``--count`` 滚动窗口隔天漂移而签名不变，旧口径结果被静默复用）；
    宇宙摘要同理（抽样基数随新票上市漂移）。
    """
    material = list(cli_args)
    if universe_digest:
        material += ["#universe", universe_digest]
    return hashlib.sha1("\0".join(material).encode("utf-8")).hexdigest()[:12]


_NAME_SAFE = re.compile(r"[^0-9A-Za-z._-]+")


def _safe_name(s: Any) -> str:
    """出场档名等进文件名前清洗（``/``、空格等非法字符替换为 ``_``）。

    只影响**文件名**——签名从 CLI 参数算，用的仍是原名/原参数。
    """
    return _NAME_SAFE.sub("_", str(s)).strip("_") or "_"


def cell_out_path(
    out_dir: pathlib.Path, cell: dict[str, Any], sig: str
) -> pathlib.Path:
    parts = [_safe_name(cell[k]) for k in ("scorer", "gate", "exit")]
    return out_dir / f"{parts[0]}__{parts[1]}__{parts[2]}__{sig}.json"


def _resolve_data_dates(a: argparse.Namespace) -> Optional[tuple[str, str]]:
    """按数据最后交易日把隐式窗口换算成具体日期，返回 ``(start, end)``。

    end = 参照票（``CAL_REF_CODE``）最后交易日（显式 ``--end`` 已给则以它截断），
    start = 往前 ``--count`` 根 K 线的日期——与子进程 ``tail(count)`` 的实际窗口
    基本等长（m2 DEFAULT_WINDOW 的口径约定）。读本地 vipdoc 日线，纯文件解析不联网。
    无数据环境读不到 ⇒ 返回 None，由调用方降级为「不钉窗口 + 报告警告」
    （m2 的「本轮不钉宇宙」口径）。
    """
    try:
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

        df = local_tdx_data.read_vipdoc_daily(CAL_REF_CODE)
    except Exception:  # noqa: BLE001 —— 无 TDX_ROOT/数据环境：降级，不炸驱动
        return None
    if df is None or df.empty:
        return None
    dates = [str(d)[:10] for d in df["date"].tolist()]
    if a.end:
        dates = [d for d in dates if d <= a.end]
    if not dates:
        return None
    win = dates[-a.count :] if a.count and len(dates) > a.count else dates
    return win[0], dates[-1]


def _resolve_universe_digest(
    a: argparse.Namespace, out_dir: pathlib.Path
) -> tuple[Optional[str], Optional[int]]:
    """解析宇宙 codes 摘要（sha1 短哈希，口径同 backtest_factors 的 codes_digest）。

    ``--codes-file`` 直接读文件；否则跑一次 ``--dump-codes`` 探针（只解析 universe
    做目录列举，不加载 K 线，很快——m2 ``_prepare_universe`` 的思路；不在本进程
    import 数据源，无数据的机器也能跑到降级分支）。失败 ⇒ ``(None, None)``。
    """
    codes: list[str] = []
    if a.codes_file:
        try:
            codes = [
                ln.strip()
                for ln in pathlib.Path(a.codes_file)
                .read_text(encoding="utf-8")
                .splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
        except OSError:
            return None, None
    else:
        path = out_dir / "_universe_probe.txt"
        probe = (
            [sys.executable, str(SCRIPT), "--trade-sim"]
            + _universe_args(a)
            + ["--dump-codes", str(path)]
        )
        try:
            r = subprocess.run(probe, cwd=str(BASE), timeout=a.timeout)
        except Exception:  # noqa: BLE001 —— 探针失败降级，不拦着实跑
            return None, None
        if r.returncode != 0 or not path.is_file():
            return None, None
        codes = [
            ln.strip()
            for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
    if not codes:
        return None, None
    digest = hashlib.sha1(",".join(codes).encode("utf-8")).hexdigest()[:12]
    return digest, len(codes)


def _pin_context(a: argparse.Namespace, out_dir: pathlib.Path) -> None:
    """隐式窗口/宇宙转显式并固化进签名上下文（结果写回 ``a`` 的扩展属性）。

    隔天数据漂移 ⇒ 解析结果变 ⇒ 签名变 ⇒ 旧格不被静默误复用（m2 三次事故类）。
    解析失败降级为「不钉 + ``data_quality`` 警告」，绝不拦着实跑。
    """
    warnings: list[str] = []
    if a.date and a.end:
        print(
            f"[WARN] --date {a.date} 与 --end {a.end} 同给：--date 被忽略"
            "（以 --end 为准）",
            file=sys.stderr,
        )
    elif a.date:
        a.end = a.date
    window_source = "explicit"
    if not (a.start and a.end):
        resolved = _resolve_data_dates(a)
        if resolved is not None:
            a.start = a.start or resolved[0]
            a.end = a.end or resolved[1]
            window_source = "resolved"
        elif a.end:
            # --end 钉死 + --count 定长 ⇒ 窗口已确定（tail(count) 到 end 为止）
            window_source = "explicit_end"
        else:
            window_source = "unpinned"
            warnings.append(
                "窗口未钉死（--count 滚动窗口随新 K 线漂移，且本机解析数据日历失败）："
                "本批结果隔天不可复现，且签名不含窗口 ⇒ 隔天续跑会同签名静默误复用"
                "旧格（口径已漂移）。显式 --start/--end 可钉死"
            )
    a.window_source = window_source
    a.universe_digest, a.universe_n = _resolve_universe_digest(a, out_dir)
    if a.universe_digest is None:
        warnings.append(
            "宇宙摘要解析失败 ⇒ 本批签名不含宇宙口径：新票上市导致抽样宇宙漂移时"
            "同签名旧格会被误复用（m2 文档化过的事故类）。显式 --codes-file 可钉死"
        )
    a.dq_warnings = warnings
    print(
        f"[INFO] 窗口 {a.start or '(隐式)'}~{a.end or '(隐式)'}[{window_source}]，"
        f"宇宙 digest={a.universe_digest or '未解析'}（{a.universe_n or '?'} 只）"
    )


def _result_complete(path: pathlib.Path) -> bool:
    """结果 JSON 完整性检查：可解析且含交易摘要块（口径同 m2 ``_load`` 主路径）。

    rc=0 但输出空/残缺是 m2 的「静默失效 ⇒ 报表少行」事故类——必须判 failed，
    不能当 ran 留一行全 None 进榜单。
    """
    try:
        d = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(d, dict):
        return False
    for k in ("trade_summary", "trade_sim", "summary", "trade_simulation"):
        blk = d.get(k)
        if isinstance(blk, dict) and ("expectancy" in blk or "n" in blk):
            return True
    return "expectancy" in d


def run_cell(
    a: argparse.Namespace,
    cell: dict[str, Any],
    out_dir: pathlib.Path,
) -> tuple[str, Optional[pathlib.Path]]:
    """跑一个格子。返回 ``(状态, 结果文件)``，状态 ∈ ran/reused/failed。"""
    cli = _cell_args(a, cell)
    out = cell_out_path(out_dir, cell, cell_signature(cli, _udigest(a)))
    tag = f"{cell['scorer']}/{cell['gate']}/{cell['exit']}"
    if out.exists() and not a.force:
        print(f"[SKIP] {out.name}（签名一致，复用）")
        return "reused", out
    cmd = [sys.executable, str(SCRIPT)] + cli + ["--out", str(out)]
    timeout = getattr(a, "timeout", 0) or CELL_TIMEOUT_S
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=str(BASE), timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[FAIL] {tag} 超时（>{timeout}s，--timeout 可调）")
        return "failed", None
    dt = time.time() - t0
    if r.returncode != 0 or not out.exists():
        print(f"[FAIL] {tag} exit={r.returncode} ({dt:.0f}s)")
        return "failed", None
    if not _result_complete(out):
        # 删掉毒文件：留着下轮会被「签名一致」复用，残缺口径就永远洗不掉
        print(f"[FAIL] {tag} rc=0 但结果 JSON 残缺/无交易摘要 ({dt:.0f}s)")
        out.unlink(missing_ok=True)
        return "failed", None
    print(f"[DONE] {tag} {dt:.0f}s")
    return "ran", out


# ---------- 结果读取与目标函数 ----------


def load_cell_row(
    cell: dict[str, Any], path: pathlib.Path, reused: bool
) -> dict[str, Any]:
    """读一个格子的结果文件，组装排名行（键名固定，测试钉住）。"""
    s = _load(path)
    pf = s.get("_portfolio") or {}
    return {
        "scorer": cell["scorer"],
        "gate": cell["gate"],
        "exit": cell["exit"],
        "params": cell["params"],
        "n": s.get("n"),
        "win_rate": s.get("win_rate"),
        "expectancy": s.get("expectancy"),
        "expectancy_R": s.get("expectancy_R"),
        "payoff_ratio": s.get("payoff_ratio"),
        "breakeven_wr": _breakeven_wr(s.get("payoff_ratio")),
        "margin": _margin({"win": s.get("win_rate"), "payoff": s.get("payoff_ratio")}),
        "total_return": pf.get("total_return"),
        "max_drawdown": pf.get("max_drawdown"),
        "ret_over_dd": _ret_over_dd(pf),
        "reused": reused,
        "result_file": path.name,
        "exit_rules": exit_params_to_rules(cell["params"]),
    }


def objective_of(
    row: dict[str, Any], weights: tuple[float, float, float]
) -> Optional[float]:
    """目标函数 = w_margin·margin + w_expR·expectancy_R + w_rdd·return/maxdd。

    margin / expectancy_R / ret_over_dd **任一缺失 ⇒ None**（该格不参与排名、垫底）。
    ⚠️ ret_over_dd 缺失不能按 0：0 排在真实负值之上，等于奖励了缺数据的格子。
    ⚠️ 三项量纲不同，这是排序启发式；且 R11 未决，读数可信度受限。
    """
    m, e, rdd = row.get("margin"), row.get("expectancy_R"), row.get("ret_over_dd")
    if m is None or e is None or rdd is None:
        return None
    return weights[0] * m + weights[1] * e + weights[2] * rdd


def rank_rows(
    rows: list[dict[str, Any]], weights: tuple[float, float, float]
) -> list[dict[str, Any]]:
    """按目标函数降序排名（objective=None 垫底；同分按格子名保序稳定）。"""
    for r in rows:
        r["objective"] = objective_of(r, weights)
    srt = sorted(
        rows,
        key=lambda r: (
            r["objective"] is not None,
            r["objective"] or 0.0,
        ),
        reverse=True,
    )
    for i, r in enumerate(srt, 1):
        r["rank"] = i
    return srt


# ---------- 两阶段调度 + 预算截断 ----------


def _factor_combos(scorers: list[str], gates: list[str]) -> list[tuple[str, str]]:
    return [(s, g) for s in scorers for g in gates]


def _cells_for(
    combos: list[tuple[str, str]], exits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s, g in combos:
        for e in exits:
            out.append(
                {
                    "scorer": s,
                    "gate": g,
                    "exit": e["name"],
                    "params": dict(e.get("params") or {}),
                }
            )
    return out


class _Runner:
    """预算记账 + 顺序执行。复用跳过不占预算；预算耗尽后剩余格子记 truncated。"""

    def __init__(self, a: argparse.Namespace, out_dir: pathlib.Path):
        self.a = a
        self.out_dir = out_dir
        self.budget = a.max_runs
        self.rows: list[dict[str, Any]] = []
        self.failed: list[str] = []
        self.truncated: list[str] = []
        self.ran = 0
        self.reused = 0

    def run_cells(self, cells: list[dict[str, Any]]) -> None:
        done = {(r["scorer"], r["gate"], r["exit"]) for r in self.rows}
        for cell in cells:
            key = (cell["scorer"], cell["gate"], cell["exit"])
            if key in done:
                continue  # 两阶段里阶段二会再遇到阶段一的基准档 ⇒ 去重，不重复入榜
            done.add(key)
            tag = f"{cell['scorer']}/{cell['gate']}/{cell['exit']}"
            cli = _cell_args(self.a, cell)
            out = cell_out_path(
                self.out_dir, cell, cell_signature(cli, _udigest(self.a))
            )
            status: str
            path: Optional[pathlib.Path]
            if out.exists() and not self.a.force:
                print(f"[SKIP] {out.name}（签名一致，复用）")
                status, path = "reused", out
            elif self.budget > 0:
                self.budget -= 1
                status, path = run_cell(self.a, cell, self.out_dir)
            else:
                self.truncated.append(tag)
                continue
            if status == "failed" or path is None:
                self.failed.append(tag)
                continue
            if status == "ran":
                self.ran += 1
            else:
                self.reused += 1
            self.rows.append(load_cell_row(cell, path, reused=(status == "reused")))


def run_grid(
    a: argparse.Namespace,
    scorers: list[str],
    gates: list[str],
    exits: list[dict[str, Any]],
    out_dir: pathlib.Path,
) -> _Runner:
    """两阶段（粗网格 → top-K 细化）或单阶段全网格。"""
    runner = _Runner(a, out_dir)
    combos = _factor_combos(scorers, gates)
    two_stage = 0 < a.top_k < len(combos) and len(exits) > 1
    if not two_stage:
        runner.run_cells(_cells_for(combos, exits))
        return runner
    # 阶段一：因子轴 × 出场轴第一档（粗）
    stage1 = _cells_for(combos, exits[:1])
    print(f"[STAGE1] 粗网格：{len(stage1)} 格（因子轴 × 基准出场档）")
    runner.run_cells(stage1)
    ranked1 = rank_rows(runner.rows, _obj_weights(a))
    top = {(r["scorer"], r["gate"]) for r in ranked1[: max(1, a.top_k)]}
    # 阶段二：top-K 因子组合 × 全部出场档（基准档经签名复用自动跳过）
    stage2 = _cells_for([c for c in combos if c in top], exits)
    print(f"[STAGE2] 细化：top-{a.top_k} 因子组合 × {len(exits)} 个出场档")
    runner.run_cells(stage2)
    return runner


# ---------- 报告 ----------


def _obj_weights(a: argparse.Namespace) -> tuple[float, float, float]:
    parts = [float(x) for x in str(a.obj_weights).split(",")]
    if len(parts) != 3:
        raise ValueError("--obj-weights 必须是三个逗号分隔的数（margin,expR,ret_dd）")
    return parts[0], parts[1], parts[2]


def _data_quality_block(a: argparse.Namespace) -> dict[str, Any]:
    """窗口/宇宙口径注记（``_pin_context`` 写入的扩展属性；缺省 = 未钉死）。"""
    digest = getattr(a, "universe_digest", None)
    return {
        "window": {
            "start": a.start or None,
            "end": a.end or None,
            "source": getattr(a, "window_source", "explicit"),
        },
        "universe": {
            "digest": digest,
            "n_codes": getattr(a, "universe_n", None),
            "pinned": bool(digest),
        },
        "warnings": list(getattr(a, "dq_warnings", None) or []),
    }


def build_json(
    a: argparse.Namespace,
    scorers: list[str],
    gates: list[str],
    exits: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    runner: _Runner,
) -> dict[str, Any]:
    """ranked JSON（schema 由 tests/test_strategy_grid.py 钉住）。"""
    return {
        "version": SCHEMA_VERSION,
        "tag": a.tag,
        "r11_warning": R11_WARNING,
        "data_quality": _data_quality_block(a),
        "grid": {
            "scorers": scorers,
            "gates": gates,
            "exits": exits,
            "n_cells": len(scorers) * len(gates) * len(exits),
        },
        "budget": {
            "max_runs": a.max_runs,
            "top_k": a.top_k,
            "ran": runner.ran,
            "reused": runner.reused,
            "failed": len(runner.failed),
            "truncated": len(runner.truncated),
        },
        "obj_weights": list(_obj_weights(a)),
        "results": ranked,
        "failed": runner.failed,
        "truncated": runner.truncated,
    }


def _fmt(v: Any, pct: bool = False, nd: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.{nd}f}%" if pct else f"{v:.{nd + 1}f}"


def _md_rank_table(ranked: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 排名 | scorer | gate | 出场档 | 笔数 | 胜率 | 期望% | 期望R | "
        "盈亏比 | margin | 收益/回撤 | objective |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in ranked:
        mg = r.get("margin")
        lines.append(
            f"| {r['rank']} | {r['scorer']} | {r['gate']} | {r['exit']} "
            f"| {r.get('n') or 0} | {_fmt(r.get('win_rate'), True, 1)} "
            f"| {_fmt(r.get('expectancy'), True)} | {_fmt(r.get('expectancy_R'), False, 3)} "
            f"| {_fmt(r.get('payoff_ratio'))} "
            f"| {f'{mg * 100:+.1f}pp' if mg is not None else '—'} "
            f"| {_fmt(r.get('ret_over_dd'))} | {_fmt(r.get('objective'), False, 3)} |"
        )
    return lines


def _md_config_details(ranked: list[dict[str, Any]]) -> list[str]:
    """每组配置的 EXIT_RULES 同 schema 块（拷入 live 配置的回流通道）。"""
    lines = ["\n## 配置明细（exit_rules 块与 EXIT_RULES.json 同 schema，可直接拷入）"]
    for r in ranked:
        lines.append(f"\n### #{r['rank']} {r['scorer']} × {r['gate']} × {r['exit']}")
        lines.append("```json")
        lines.append(json.dumps(r["exit_rules"], ensure_ascii=False, indent=2))
        lines.append("```")
        ro = r["exit_rules"].get("research_only")
        if ro:
            lines.append(
                "⚠️ 本配置含 live 无法表达的研究侧参数 "
                f"（research_only: {json.dumps(ro, ensure_ascii=False)}）——"
                "拷入 EXIT_RULES.json 后这些参数**不生效**（live schema 无对应节），"
                "需 live 侧先支持才有意义"
            )
    return lines


_WINDOW_SOURCE_LABEL = {
    "explicit": "显式钉死",
    "explicit_end": "显式 --end 钉死（--count 定长）",
    "resolved": "隐式窗口已转显式（按数据最后交易日换算）",
    "unpinned": "**未钉死，随数据漂移**",
}


def build_markdown(payload: dict[str, Any]) -> str:
    """markdown 报告：R11 固定标注 + 口径注记 + 预算行 + 排名表 + 每组配置明细。"""
    b = payload["budget"]
    g = payload["grid"]
    dq = payload.get("data_quality") or {}
    win = dq.get("window") or {}
    uni = dq.get("universe") or {}
    src = _WINDOW_SOURCE_LABEL.get(win.get("source") or "", win.get("source") or "?")
    lines = [
        f"# 策略联合寻优报告（因子 × 出场）  tag={payload['tag']}",
        "",
        f"> {R11_WARNING}",
        "",
        f"- 网格：{len(g['scorers'])} scorer × {len(g['gates'])} gate × "
        f"{len(g['exits'])} 出场档 = {g['n_cells']} 格",
        f"- 窗口：{win.get('start') or '?'} ~ {win.get('end') or '?'}（{src}）；"
        f"宇宙：{uni.get('n_codes') or '?'} 只，"
        f"digest={uni.get('digest') or '未解析'}",
        f"- 预算：max-runs {b['max_runs']}，实际跑 {b['ran']}，"
        f"复用跳过 {b['reused']}，失败 {b['failed']}，被截断 {b['truncated']}"
        f"（top-k={b['top_k']}，obj 权重 {payload['obj_weights']}）",
    ]
    for w in dq.get("warnings") or []:
        lines.append(f"- ⚠️ {w}")
    if b["truncated"]:
        lines.append(
            f"- ⚠️ **网格超出预算，{b['truncated']} 格被截断未跑**："
            + "、".join(payload["truncated"][:10])
        )
    if payload["failed"]:
        lines.append("- ⚠️ 失败：" + "、".join(payload["failed"]))
    lines += ["", "## 排名（目标函数降序；⚠️ 见头部 R11 标注）", ""]
    lines += _md_rank_table(payload["results"])
    lines += _md_config_details(payload["results"])
    return "\n".join(lines) + "\n"


# ---------- CLI ----------


def _load_exit_grid(spec: str) -> list[dict[str, Any]]:
    """--exit-grid：JSON 文件路径或内联 JSON；空 = 内置默认粗网格。"""
    if not spec:
        return copy.deepcopy(DEFAULT_EXIT_GRID)
    if spec.lstrip().startswith("["):
        data = json.loads(spec)
    else:
        data = json.loads(pathlib.Path(spec).read_text(encoding="utf-8"))
    return data


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="因子 × 出场 联合寻优驱动器（Phase E）")
    ap.add_argument(
        "--scorers",
        default=",".join(DEFAULT_SCORERS),
        help=f"逗号分隔（默认 {','.join(DEFAULT_SCORERS)}；全集见 backtest_factors SCORERS）",
    )
    ap.add_argument(
        "--gates",
        default=",".join(DEFAULT_GATES),
        help=f"逗号分隔（默认 {','.join(DEFAULT_GATES)}；全集见 backtest_factors ENTRY_GATES）",
    )
    ap.add_argument(
        "--exit-grid",
        default="",
        help="出场网格 JSON 文件或内联 JSON（list of {name, params}，"
        "params 键名同 simulate_b1_trade 形参/EXIT_RULES.json）；默认内置小网格",
    )
    ap.add_argument("--top-k", type=int, default=2, help="两阶段细化保留的因子组合数")
    ap.add_argument(
        "--max-runs",
        type=int,
        default=20,
        help="子进程调用预算上限（复用跳过不占）；超出截断并在报告注明",
    )
    ap.add_argument(
        "--obj-weights",
        default=",".join(str(x) for x in DEFAULT_OBJ_WEIGHTS),
        help="目标函数权重 margin,expectancy_R,return_over_maxdd（默认 1,1,0.05）",
    )
    # ---- 透传 backtest_factors ----
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    ap.add_argument("--start", default="", help="回测起点 YYYY-MM-DD（透传 --start）")
    ap.add_argument("--end", default="", help="回测终点 YYYY-MM-DD（透传 --end）")
    ap.add_argument("--date", default="", help="单日口径便捷项：等价于 --end")
    ap.add_argument(
        "--codes-file", default="", help="钉死宇宙（透传 --codes-file，优先级最高）"
    )
    ap.add_argument("--count", type=int, default=500, help="每股回溯 K 线根数（透传）")
    ap.add_argument(
        "--cost-bps", type=float, default=25.0, help="往返成本基点（默认 25）"
    )
    ap.add_argument(
        "--top-n",
        type=int,
        default=0,
        help="横截面择优（透传 --top-n）。⚠️ scorer 轴只在 top-n>0 时有区分度",
    )
    # ---- 驱动控制 ----
    ap.add_argument(
        "--no-amv-pin",
        action="store_true",
        help="解除默认研究基底的 0AMV 做多钉（v0.93 起默认每格带 --amv-long-only；"
        "仅对照实验用）",
    )
    ap.add_argument("--force", action="store_true", help="忽略已落盘结果重跑")
    ap.add_argument(
        "--timeout",
        type=int,
        default=CELL_TIMEOUT_S,
        help=f"单格子进程超时秒数（默认 {CELL_TIMEOUT_S}=30 分钟；超时计 failed）",
    )
    ap.add_argument("--tag", default="", help="批次标签（默认时间戳），进报告文件名")
    ap.add_argument("--out-dir", default="", help=f"结果目录（默认 {OUTDIR}）")
    return ap


def _validate_factors(scorers: list[str], gates: list[str]) -> Optional[str]:
    """scorer/gate 名对 backtest_factors 注册表校验；注册表不可导入时跳过。"""
    try:
        from custos.research.backtest_factors import ENTRY_GATES, SCORERS  # noqa: PLC0415
    except Exception:  # noqa: BLE001 —— 无数据环境下注册表可能导不进，交给子进程报错
        return None
    bad_s = [s for s in scorers if s not in SCORERS]
    bad_g = [g for g in gates if g not in ENTRY_GATES]
    if bad_s:
        return f"未知 scorer: {bad_s}（注册表: {sorted(SCORERS)}）"
    if bad_g:
        return f"未知 gate: {bad_g}（注册表: {sorted(ENTRY_GATES)}）"
    return None


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # GBK 终端
    ap = _build_parser()
    a = ap.parse_args(argv)
    scorers = [s.strip() for s in a.scorers.split(",") if s.strip()]
    gates = [g.strip() for g in a.gates.split(",") if g.strip()]
    try:
        exits = _load_exit_grid(a.exit_grid)
    except (OSError, ValueError) as e:
        print(f"[FAIL] --exit-grid 读不了: {e}", file=sys.stderr)
        return 2
    err = validate_exit_grid(exits) or _validate_factors(scorers, gates)
    if err:
        print(f"[FAIL] {err}", file=sys.stderr)
        return 2
    try:
        _obj_weights(a)
    except ValueError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 2
    if not a.tag:
        a.tag = time.strftime("%Y%m%d_%H%M%S")
    out_dir = pathlib.Path(a.out_dir) if a.out_dir else OUTDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    _pin_context(a, out_dir)  # 隐式窗口/宇宙转显式，固化进签名与 data_quality
    runner = run_grid(a, scorers, gates, exits, out_dir)
    ranked = rank_rows(runner.rows, _obj_weights(a))
    payload = build_json(a, scorers, gates, exits, ranked, runner)
    jpath = out_dir / f"_ranked__{a.tag}.json"
    mpath = out_dir / f"_report__{a.tag}.md"
    jpath.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    mpath.write_text(build_markdown(payload), encoding="utf-8")
    print(f"\n[REPORT] {jpath.name} / {mpath.name}（{len(ranked)} 格入榜）")
    if runner.truncated:
        print(f"⚠️ {len(runner.truncated)} 格超出 --max-runs 预算被截断，详见报告")
    if runner.failed:
        print(f"⚠️ {len(runner.failed)} 格失败：{'、'.join(runner.failed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
