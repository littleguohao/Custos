"""因子实现层：**live 选股链与研究回测器共同依赖的下层**。

因子 = 判别维度，不含完整进出场规则，可被多个策略/消费方引用。
文档侧对应 `governance/strategy/_factors/`（跨策略因子）与各策略目录下的规则文档。

## ⚠️ 现存三套接口（2026-08-06 清点，尚未统一）

    compute_xxx(df) -> dict        s_shape / sector_phase / b1_dual 的一部分
    detect_xxx(df)  -> dict|None   b2_surge / main_rally / platform_pullback / b1_dual 的一部分
    _sc_xxx(df,code) -> dict|None  `research/backtest_factors.SCORERS` 里的适配层
                                   （统一返回 {score, suggestion, aux, components}）

## ⚠️ 两种消费方式

    live 标注   `screening/signal_labels.py` → 把因子结果转成候选表上的标签
    研究打分    `research/backtest_factors.SCORERS` → 横截面排序与回测

其中 `b1_dual_factor` / `b2_surge_factor` / `main_rally_factor` / `rsi_state`
**同时被两处消费、各自包装一遍** ⇒ 新增因子要在两处各写一个适配，
而两处的判据可能不一致。

**统一接口是语义改动**（会改 live 选股行为），必须单独立项 + 回测，
不能搭在目录重构里 —— 所以本次只做位置统一。

## 因子之间的依赖

    b1_dual_factor  → s_shape, platform_pullback
    main_rally_factor → rsi_state
    sector_phase    → sector_mainstream

⇒ 同目录内保持包式绝对 import（`from custos.core.factors.xxx import ...`）；
2026-08-11 包式化（阶段 4b）后不再有任何 sys.path 注入，custos 可编辑安装即可解析。
"""


# ─────────────────────────── 注册表 ───────────────────────────
# 每个因子模块声明 `FACTOR` 元数据 + `score()`/`detect()`。模板见 `_template.py`。

import importlib as _il  # noqa: E402
import pkgutil as _pk  # noqa: E402
import pathlib as _pl  # noqa: E402
import sys as _sys  # noqa: E402

_SKIP = {"_template", "_util", "_shares", "__init__"}

# ⚠️ **没有 "falsified" 这一档，是刻意的。**
# 2026-08-06 我一度把 alpha101/mcap/reversal_quality 等标成 falsified，owner 纠正：
# **不要随便证伪。** 而且那个标注自相矛盾 —— R2 的证伪结论本身就在重跑清单里：
#   · 决定性翻转（+69.4%→−11.9%）**同时换了宇宙与数据源**，归因未分离
#   · 那些净值终审窗口**全落在已弃用的 qlib bundle 上**（加法调整，收益放大 13~21%）
# ⇒ 证据强度已降级，判定却写得比证据硬。改用 `needs_work`：
#   「按现有证据不可用，但证据本身待重跑」——这才是当前真实的知识状态。
STATUSES = ("active", "candidate", "needs_work", "untested")
KINDS = ("selector", "pattern", "state", "control")

#: **live 里允许怎么用** —— 与 status 是两个正交维度。
#: `evidence_only` 是 R2 对 `b1_pullback`/`perfect_b1_fit` 的原话「仅描述性，不作买入依据」
#: 的编码：它们确实被 live 链计算并落候选表，但**不驱动分层/gate/排序**。
#: 只看 status 会把这种合法用法误判成违规（2026-08-06 第一版守卫就误报了）。
LIVE_USES = ("none", "evidence_only", "gate", "scorer")

#: **是否已上线** —— 第三个维度，与 status/live_use 正交。
#: `release` = 18:00 选股链真的在跑它；`debug` = 只在研究/回测里用。
#: 三个维度各答不同的问题：status「证据够不够」/ live_use「允许怎么用」/ stage「现在真的在跑吗」。
#: ⚠️ 由测试对着 import 图核对，不靠手写维护 ——
#: 「以为上线了其实没有」比没有标记更糟。
STAGES = ("release", "debug")

#: **已知矛盾的显式白名单**：`status` 说证据不够，而 live 确实拿它驱动决策。
#: 不静默放过、也不擅自改 live —— 改分层是策略决策。列在这里是为了
#: ①矛盾可见 ②新出现的矛盾会被测试挡住（ratchet）。
KNOWN_STATUS_USE_CONFLICTS = {
    "s_shape": "R2 说无 alpha，但 score_candidates.technical_score 主路径用它出技术层级"
    "（参与 A/B/C/D 分层）。待 owner 定，见 TODO。",
}

#: `status` 在这个集合里的因子**不得进入 live 选股链**。
#: 拦的理由是「**未通过验证**」，不是「已被证伪」—— 两者对 live 的后果相同
#: （都不许用），但对研究的后果完全不同：证伪意味着不必再看，
#: 待优化意味着**证据本身要重跑**（见 R2 重跑清单 P1）。
NOT_FOR_LIVE = frozenset({"needs_work", "untested"})


def registry() -> dict[str, dict]:
    """扫本包，收集所有声明了 `FACTOR` 的模块。

    返回 ``{id: {"meta": FACTOR, "module": mod, "score": fn|None, "detect": fn|None}}``。
    单个模块导入失败：跳过该模块并 print `[WARN]` 到 stderr（fail-open 但**不静默**）。
    """
    out: dict[str, dict] = {}
    failed: list[str] = []
    for m in _pk.iter_modules([str(_pl.Path(__file__).resolve().parent)]):
        if m.name in _SKIP:
            continue
        try:
            mod = _il.import_module(f".{m.name}", package=__name__)
        except Exception as exc:  # noqa: BLE001 —— 单个因子坏了不该让注册表整体失效
            # ⚠️ 但**不得静默**：2026-08-06 `_shares` 漏 import json 就是这类 fail-open
            # 吞掉的 —— 空注册表和全员坏掉无法区分。失败照常跳过，但必须留痕到 stderr
            # （项目惯例 `[WARN] ...`，同 enrich_candidates.build_stock_theme_map）。
            failed.append(f"{m.name}: {type(exc).__name__}: {exc}")
            continue
        meta = getattr(mod, "FACTOR", None)
        if not isinstance(meta, dict) or meta.get("id") in (None, "template"):
            continue
        out[meta["id"]] = {
            "meta": meta,
            "module": mod,
            "score": getattr(mod, "score", None),
            "detect": getattr(mod, "detect", None),
        }
    if failed:
        print(
            f"[WARN] 因子注册表：{len(failed)} 个因子模块导入失败被跳过 —— "
            + "; ".join(failed),
            file=_sys.stderr,
        )
    return out


def live_allowed() -> dict[str, dict]:
    """可在 live 里**驱动决策**的因子：status 通过 且 live_use 是 gate/scorer。"""
    return {
        k: v
        for k, v in registry().items()
        if v["meta"].get("status") not in NOT_FOR_LIVE
        and v["meta"].get("live_use") in ("gate", "scorer")
    }


def released() -> dict[str, dict]:
    """已上线的因子（`stage == "release"`）。"""
    return {k: v for k, v in registry().items() if v["meta"].get("stage") == "release"}


def live_evidence_only() -> dict[str, dict]:
    """只能作**描述性证据**的因子 —— 可以出现在 live 链，但不得驱动决策。"""
    return {
        k: v
        for k, v in registry().items()
        if v["meta"].get("live_use") == "evidence_only"
    }
