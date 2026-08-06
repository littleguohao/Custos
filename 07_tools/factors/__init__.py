"""因子实现层：**live 选股链与研究回测器共同依赖的下层**。

因子 = 判别维度，不含完整进出场规则，可被多个策略/消费方引用。
文档侧对应 `00_governance/strategy/_factors/`（跨策略因子）与各策略目录下的规则文档。

## ⚠️ 现存三套接口（2026-08-06 清点，尚未统一）

    compute_xxx(df) -> dict        s_shape / sector_phase / b1_dual 的一部分
    detect_xxx(df)  -> dict|None   b2_surge / main_rally / platform_pullback / b1_dual 的一部分
    _sc_xxx(df,code) -> dict|None  `screening/backtest_factors.SCORERS` 里的适配层
                                   （统一返回 {score, suggestion, aux, components}）

## ⚠️ 两种消费方式

    live 标注   `screening/signal_labels.py` → 把因子结果转成候选表上的标签
    研究打分    `screening/backtest_factors.SCORERS` → 横截面排序与回测

其中 `b1_dual_factor` / `b2_surge_factor` / `main_rally_factor` / `rsi_state`
**同时被两处消费、各自包装一遍** ⇒ 新增因子要在两处各写一个适配，
而两处的判据可能不一致。

**统一接口是语义改动**（会改 live 选股行为），必须单独立项 + 回测，
不能搭在目录重构里 —— 所以本次只做位置统一。

## 因子之间的依赖

    b1_dual_factor  → s_shape, platform_pullback
    main_rally_factor → rsi_state
    sector_phase    → sector_mainstream

⇒ 同目录内保持扁平 import；被外部消费时由消费方把本目录加进 `sys.path`
（与 `screening/`、`market_timing/`、`local_tdx/` 同一惯例）。
"""


# ─────────────────────────── 注册表 ───────────────────────────
# 每个因子模块声明 `FACTOR` 元数据 + `score()`/`detect()`。模板见 `_template.py`。

import importlib as _il                     # noqa: E402
import pkgutil as _pk                       # noqa: E402
import pathlib as _pl                       # noqa: E402

_SKIP = {"_template", "_util", "__init__"}

STATUSES = ("active", "candidate", "falsified", "untested")
KINDS = ("selector", "pattern", "state", "control")

#: `status` 在这个集合里的因子**不得进入 live 选股链**。
#: 把 R2「选股章节正式关闭、所有价量选择器证伪」变成机器可执行的约束 ——
#: 否则半年后有人看到 `alpha101` 就拿去用了，而文档里那条否决没人会重读。
NOT_FOR_LIVE = frozenset({"falsified", "untested"})


def registry() -> dict[str, dict]:
    """扫本包，收集所有声明了 `FACTOR` 的模块。

    返回 ``{id: {"meta": FACTOR, "module": mod, "score": fn|None, "detect": fn|None}}``。
    """
    out: dict[str, dict] = {}
    for m in _pk.iter_modules([str(_pl.Path(__file__).resolve().parent)]):
        if m.name in _SKIP:
            continue
        try:
            mod = _il.import_module(m.name)
        except Exception:                    # noqa: BLE001 —— 单个因子坏了不该让注册表整体失效
            continue
        meta = getattr(mod, "FACTOR", None)
        if not isinstance(meta, dict) or meta.get("id") in (None, "template"):
            continue
        out[meta["id"]] = {"meta": meta, "module": mod,
                           "score": getattr(mod, "score", None),
                           "detect": getattr(mod, "detect", None)}
    return out


def live_allowed() -> dict[str, dict]:
    """只返回允许进 live 的因子（`status` 不在 `NOT_FOR_LIVE` 里）。"""
    return {k: v for k, v in registry().items()
            if v["meta"].get("status") not in NOT_FOR_LIVE}
