# -*- coding: utf-8 -*-
"""规划层（TODO #72，QuantaAlpha planning 的 Custos 版）：种子方向 → N 条差异化探索方向。

原则：

- LLM 只做方向**扩展**（机制正交：动量/反转/量能/波动/结构等不同维度），
  prompt 注入种子方向 + 池内已有方向/表达式清单（去重，正交约束）+ DSL 能力面
  简述 + JSON 输出契约；产出经 ``operators.parse_llm_json`` 四级解析 + 结构校验，
  失败把错误原因回注 prompt 重试（≤ ``MAX_ATTEMPTS`` 次，同 operators 惯例）。
- LLM 全挂 / 产出持续不合规 / 不足 n 条 → **fallback**：内置确定性模板补足
  （``FALLBACK_TEMPLATES``）。fallback 是正常路径**不 raise**——别让 LLM 故障
  堵死整个进化循环；每条方向带 ``source`` 标记（``"llm"``/``"fallback"``），
  事件与汇总里可区分。
- 模块不读环境、不发网络（ll 注入；``ChatLLM`` Protocol 见 llm_client）。
"""

from __future__ import annotations

from dataclasses import dataclass

from custos.research.evolution.llm_client import ChatLLM, LLMError
from custos.research.evolution.operators import (
    DSL_RULES,
    parse_llm_json,
)
from custos.research.evolution.trajectory import TrajectoryPool

MAX_ATTEMPTS = 3  # 规划输出的 产出→解析→校验 重试上限（错误回注，同 operators）

# 内置方向模板（确定性 fallback；LLM 失败时按序取，skip 与池内/已选重复的）。
FALLBACK_TEMPLATES: tuple[str, ...] = (
    "{seed} + 量能确认",
    "{seed} + 波动率过滤",
    "{seed} + 趋势强度过滤",
    "{seed} + 均值回复变体",
    "{seed} + 动量加速度变体",
    "{seed} + 高低点结构变体",
    "{seed} + 量价背离变体",
    "{seed} + 时间衰减变体",
)


@dataclass(frozen=True)
class PlannedDirection:
    """一条规划产出的探索方向及其来源（``"llm"`` / ``"fallback"``）。"""

    direction: str
    source: str


_PLAN_SYSTEM = "你是量化研究规划助手，只输出严格 JSON。"


def _plan_prompt(seed: str, n: int, pool: TrajectoryPool | None) -> list[dict]:
    """规划 prompt（中文，注入正交约束与 DSL 能力面）。"""
    if pool is None or not len(pool):
        existing = "（池为空，尚无已有轨迹）"
    else:
        dirs = sorted({t.direction for t in pool.all()})
        exprs = list(dict.fromkeys(t.expression for t in pool.all()))
        existing = (
            "已有方向: "
            + "；".join(dirs)
            + "\n已有表达式（去重）:\n"
            + "\n".join(f"- {e}" for e in exprs[:16])
        )
    user = f"""请把以下种子探索方向扩展成恰好 {n} 条**机制不同**的差异化探索方向
（例如动量/反转/量能/波动/结构等不同维度；换窗口参数不算机制不同）。

种子方向：{seed}

{DSL_RULES}

池内已有产出（正交约束：不得与已有方向/表达式机制重复）:
{existing}

输出契约（严格遵守）：
- 只输出一个 JSON 对象：{{"directions": ["方向一", "方向二", ...]}}，恰好 {n} 条；
- 每条是一句非空中文方向描述；不要 markdown 围栏、不要任何 JSON 之外的文字。"""
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": user},
    ]


def _validate_directions(data: dict, seen: set[str]) -> list[str]:
    """契约校验：directions 为 list、每条非空 str；去空白、批内与池内去重。"""
    if not isinstance(data, dict) or not isinstance(data.get("directions"), list):
        raise LLMError("规划输出缺 directions 列表")
    out: list[str] = []
    for d in data["directions"]:
        if not isinstance(d, str) or not d.strip():
            raise LLMError(f"方向条目不是非空 str: {d!r}")
        d = d.strip()
        if d not in seen and d not in out:
            out.append(d)
    if not out:
        raise LLMError("去重/剔除池内已有后无新方向")
    return out


def _chat_directions(
    llm: ChatLLM, messages: list[dict], seen: set[str], n: int
) -> tuple[list[str], str | None]:
    """chat → parse → 校验；不足 n 条或失败回注重试。返回（方向们, 最后错误）。"""
    convo = list(messages)
    last: str | None = None
    out: list[str] = []
    for _ in range(MAX_ATTEMPTS):
        try:
            out = _validate_directions(parse_llm_json(llm.chat(convo)), seen)
            if len(out) >= n:
                return out, None
            last = f"方向不足 {n} 条（去重后实际 {len(out)} 条）"
        except LLMError as exc:
            last = str(exc)
        convo = convo + [
            {
                "role": "user",
                "content": f"上一次输出不合规：{last}\n请严格按输出契约重新输出 JSON。",
            }
        ]
    return out, last  # 尽力而为：valid 前缀保留给调用方补足


def _fallback_variants(seed: str, taken: set[str], need: int) -> list[str]:
    """确定性模板变体：skip 与池内/已选重复的；模板不够用编号变体兜底。"""
    out: list[str] = []
    for tmpl in FALLBACK_TEMPLATES:
        d = tmpl.format(seed=seed)
        if d not in taken and d not in out:
            out.append(d)
    i = 1
    while len(out) < need:  # 极端情形（模板全撞重）：编号变体无限可续，必然终止
        d = f"{seed}（变体 {i}）"
        i += 1
        if d not in taken and d not in out:
            out.append(d)
    return out


def generate_directions(
    seed_direction: str,
    n: int,
    llm: ChatLLM,
    *,
    pool: TrajectoryPool | None = None,
) -> list[PlannedDirection]:
    """把一句话种子方向扩成恰好 n 条差异化探索方向（截断多余）。

    来源标记：LLM 产出的 ``source="llm"``；LLM 失败/不足 n 条时用
    ``FALLBACK_TEMPLATES`` 确定性模板补足的部分 ``source="fallback"``
    （fallback 是正常路径，不 raise）。
    """
    if n < 1:
        raise ValueError(f"n 必须 >= 1，实际 {n}")
    seed = seed_direction.strip()
    if not seed:
        raise ValueError("种子方向不能为空")
    seen = {t.direction.strip() for t in pool.all()} if pool is not None else set()
    llm_dirs, _last = _chat_directions(llm, _plan_prompt(seed, n, pool), seen, n)
    out = [PlannedDirection(d, "llm") for d in llm_dirs[:n]]
    if len(out) < n:  # fallback 补足（skip 池内已有 + LLM 已产出的）
        taken = seen | {p.direction for p in out}
        for d in _fallback_variants(seed, taken, n - len(out)):
            out.append(PlannedDirection(d, "fallback"))
    return out[:n]
