# -*- coding: utf-8 -*-
"""进化算子：propose / mutate / crossover / interpret + 确定性判定 judge_mining。

职责切分（不可越界）：

- **LLM 算子只产出候选**（假设 + 表达式 + 机制解释），不做任何数值判定；
  ``decision`` 只由 ``judge_mining``（确定性规则）给出，LLM 输出永远不改写判定。
  ``judge_mining`` 判据 = 复杂度门 + 有效日数 / rank_ic_mean / rank_icir 阈值
  + **前后半窗 RankIC 均值同正**（R3 纪律：单窗正不作数，逐日 IC 序列按日期序
  n//2 切半，两半均值都必须 > 0，NaN/空半窗 fail-closed）。
- prompt 用中文，模块级常量模板注入：BASE_VARIABLES 清单、OPERATORS 签名、
  复杂度上限、父代/池摘要（假设+表达式+rank_ic_mean/rank_icir+decision+feedback）、
  正交性要求（与父代及池内已有表达式**机制不同**，附去重列表）。
- LLM 输出契约：JSON ``{"hypothesis": str, "expression": str, "rationale": str}``
  （schema 常量为 ``PROPOSAL_SCHEMA``，调 chat 时传给 client 的结构化输出）；
  ``parse_llm_json`` 四级容错降级（直接 loads → raw_decode 取首个对象 →
  ```json 代码块抽取 → tokenize 修 Python 字面量后 parse）。不合约 → 把错误原因
  回注 prompt 重试（每算子最多 ``MAX_ATTEMPTS`` 次），仍失败 raise LLMError。
- ``interpret`` 是唯一允许"失败降级"的算子：LLM 挂了返回确定性模板字符串
  （含指标数值），**绝不 raise** —— 反馈解读缺失不该拖死整个进化循环。
"""

from __future__ import annotations

import io
import json
import random
import re
import tokenize
from typing import Any

import pandas as pd

from custos.research.evolution.expr_dsl import (
    BASE_VARIABLES,
    MAX_WINDOW,
    OPERATORS,
    Complexity,
    violations,
)
from custos.research.evolution.ic_eval import ICStats
from custos.research.evolution.llm_client import ChatLLM, LLMError
from custos.research.evolution.trajectory import Trajectory, TrajectoryPool

# 每个算子的"产出→解析→校验"重试上限（错误原因回注 prompt）。
MAX_ATTEMPTS = 3

# LLM 输出契约的三键（缺一/非 str → 回注重试）。
REQUIRED_KEYS: tuple[str, ...] = ("hypothesis", "expression", "rationale")

# 三键契约的 JSON Schema：propose/mutate/crossover 调 chat 时传给 client 的
# 结构化输出（client 降级链在不认 json_schema 的端点上自动退回 json_object）。
PROPOSAL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string"},
        "expression": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": list(REQUIRED_KEYS),
    "additionalProperties": False,
}

# 注入 prompt 的复杂度上限（与 expr_dsl.violations 默认值一致，改阈值要同步）。
_COMPLEXITY_CAPS = (
    "去空白长度 ≤300 / 数值字面量 ≤6 种 / 基础变量 ≤6 种 / 重复子树 ≤8 / 嵌套深度 ≤12"
)

_OP_DOC: dict[str, str] = {
    "REF": "REF(x, n)：n 周期前的值",
    "MA": "MA(x, n)：n 周期移动平均",
    "SUM": "SUM(x, n)：n 周期求和",
    "STD": "STD(x, n)：n 周期标准差",
    "MAX": "MAX(x, n)：n 周期最大值",
    "MIN": "MIN(x, n)：n 周期最小值",
    "DELTA": "DELTA(x, n)：x − REF(x, n)",
    "ROC": "ROC(x, n)：x/REF(x, n)−1（变化率）",
    "TS_RANK": "TS_RANK(x, n)：当前值在过去 n 周期中的分位，(0, 1]",
    "ABS": "ABS(x)：绝对值",
    "LOG": "LOG(x)：自然对数（非正值 → NaN）",
}


def _operator_catalog() -> str:
    """OPERATORS 签名清单文本（按注册表动态生成，防与实现漂移）。"""
    return "\n".join(f"- {_OP_DOC.get(name, name)}" for name in sorted(OPERATORS))


# ── 模块级 prompt 常量模板（中文）──────────────────────────────────────────

DSL_RULES = f"""DSL 白名单规则（违反即拒，没有例外）：
- 基础变量（大小写不敏感）: {", ".join(BASE_VARIABLES)}
- 算子（算子名大小写敏感，窗口参数 n 必须是正整数且 ≤ {MAX_WINDOW}）:
{_operator_catalog()}
- 只允许 + - * / 与一元负号；只允许 int/float 字面量；不允许关键字参数。
- 复杂度上限: {_COMPLEXITY_CAPS}。"""

OUTPUT_CONTRACT = """输出契约（严格遵守）：
- 只输出一个 JSON 对象；不要 markdown 围栏、不要任何 JSON 之外的文字。
- 形状: {"hypothesis": "一句话假设", "expression": "DSL 表达式", "rationale": "机制解释"}
- 三个键都必须是非空字符串。"""

_SYSTEM_PROMPT = (
    "你是量化因子研究助手，熟悉 A 股截面因子挖掘。只输出严格 JSON，"
    "表达式必须落在给定 DSL 白名单内。"
)


# ── prompt 组装（注入池摘要 / 父代 / 正交性约束）────────────────────────────


def _fmt_metric(v: Any) -> str:
    """指标读数格式化：数值 → 4 位小数；缺失/非数值 → '-'。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return "-"
    return f"{float(v):.4f}"


def _existing_expressions(pool: TrajectoryPool) -> str:
    """池内已有表达式去重列表（保持插入序），注入正交性约束。"""
    uniq = list(dict.fromkeys(t.expression for t in pool.all()))
    if not uniq:
        return "（无）"
    return "\n".join(f"- {e}" for e in uniq)


def _trajectory_digest(t: Trajectory) -> str:
    """单条轨迹的摘要行：phase/decision + 表达式 + 假设 + 指标 + 反馈。"""
    mm = t.mining_metrics
    fb = " ".join(t.feedback.split())[:120]
    return (
        f"- [{t.phase}/{t.decision}] {t.expression}\n"
        f"  假设: {t.hypothesis[:120]}\n"
        f"  挖掘窗 RankIC均值={_fmt_metric(mm.get('rank_ic_mean'))} "
        f"RankICIR={_fmt_metric(mm.get('rank_icir'))}\n"
        f"  反馈: {fb or '（无）'}"
    )


def _pool_digest(pool: TrajectoryPool, *, limit: int = 8) -> str:
    """池摘要（最近 limit 条轨迹），注入 prompt 供正交性/避坑参考。"""
    items = pool.all()
    if not items:
        return "（池为空，尚无已有轨迹）"
    return "\n".join(_trajectory_digest(t) for t in items[-limit:])


def _parent_block(parents: list[Trajectory]) -> str:
    return "\n".join(_trajectory_digest(p) for p in parents)


def _messages(user: str) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _propose_prompt(
    direction: str, pool: TrajectoryPool, rng: random.Random | None, hint: str | None
) -> list[dict]:
    emphasis = ""
    if rng is not None:
        # seeded 随机侧重：让同方向多次 propose 探索不同机制（确定性可复现）。
        focus = rng.choice(sorted(OPERATORS))
        emphasis = f"\n本次请侧重围绕算子 {focus} 构思机制（不是强制，仅作探索侧重）。"
    hint_block = f"\n修正要求：{hint}" if hint else ""
    user = f"""请为以下探索方向原创一个全新的截面因子候选。

探索方向：{direction}{emphasis}

{DSL_RULES}

正交性要求：新因子与池内已有表达式必须**机制不同**（换窗口参数/换写法不算不同）。
池内已有表达式（去重）:
{_existing_expressions(pool)}

池摘要（最近轨迹，含判定与反馈，失败教训不要重蹈）:
{_pool_digest(pool)}
{hint_block}

{OUTPUT_CONTRACT}"""
    return _messages(user)


def _mutate_prompt(
    parent: Trajectory, pool: TrajectoryPool, hint: str | None
) -> list[dict]:
    hint_block = f"\n修正要求：{hint}" if hint else ""
    user = f"""请对以下父代因子做**机制级变异**（不是微调窗口参数，而是换一个机制角度：
例如动量→反转、绝对量→相对分位、价格→量价结合）。

父代轨迹:
{_parent_block([parent])}

{DSL_RULES}

正交性要求：变异结果与父代及池内已有表达式必须机制不同。
池内已有表达式（去重）:
{_existing_expressions(pool)}
{hint_block}

{OUTPUT_CONTRACT}"""
    return _messages(user)


def _crossover_prompt(
    parents: list[Trajectory], pool: TrajectoryPool, hint: str | None
) -> list[dict]:
    hint_block = f"\n修正要求：{hint}" if hint else ""
    user = f"""请对以下 {len(parents)} 个父代因子做**机制级杂交**：取各自机制的互补部分
组合成一个新因子（例如一个父代的趋势腿 × 另一个父代的量能确认腿）。

父代轨迹:
{_parent_block(parents)}

{DSL_RULES}

正交性要求：杂交结果与任一父代及池内已有表达式必须机制不同。
池内已有表达式（去重）:
{_existing_expressions(pool)}
{hint_block}

{OUTPUT_CONTRACT}"""
    return _messages(user)


def _interpret_prompt(trajectory: Trajectory, sota: Trajectory | None) -> list[dict]:
    sota_block = (
        "当前尚无 pass 的最优轨迹。"
        if sota is None
        else f"当前最优（sota）轨迹:\n{_trajectory_digest(sota)}"
    )
    user = f"""请对以下轨迹的挖掘窗结果写一段中文解读（2-4 句）：
读数说明了什么、与当前最优相比如何、下一步值得往哪个机制方向走。
只输出解读文本本身，不要 JSON、不要列表。

待解读轨迹:
{_trajectory_digest(trajectory)}

{sota_block}"""
    return [
        {"role": "system", "content": "你是量化因子研究助手，用简体中文写简短解读。"},
        {"role": "user", "content": user},
    ]


# ── LLM 输出解析与校验 ─────────────────────────────────────────────────────

# ```json ...``` 代码块（json 标注可省；多个候选逐个试）。
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# 第四级修正映射：LLM 常把 Python repr 当 JSON 返回（字面量大写）。
_PY_LITERAL_TO_JSON = {"True": "true", "False": "false", "None": "null"}


def _loads_dict(text: str) -> dict | None:
    """json.loads 且顶层是 dict 才返回；坏 JSON / 顶层非 dict → None。"""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _lvl_direct(raw: str) -> dict | None:
    """第一级：直接 json.loads（理想情况：LLM 严格输出纯 JSON）。"""
    return _loads_dict(raw)


def _lvl_raw_decode(raw: str) -> dict | None:
    """第二级：raw_decode 在每个 '{' 位置试解析，取首个完整 JSON 对象。

    处理前后废话：字符串内的括号、转义、嵌套都由真正的 JSON 解析器处理，
    比手写平衡扫描可靠；围栏/前缀都在 '{' 之外，自然被跳过。
    """
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            obj, _end = decoder.raw_decode(raw, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _lvl_fenced(raw: str) -> dict | None:
    """第三级：```json ...``` 代码块抽取（多个候选逐个试）。"""
    for block in _JSON_FENCE_RE.findall(raw):
        data = _loads_dict(block.strip())
        if data is None:
            data = _lvl_raw_decode(block)
        if data is not None:
            return data
    return None


def _python_literals_to_json(text: str) -> str:
    """tokenize 把字符串字面量**之外**的 True/False/None 修成 true/false/null。

    只改 NAME token、绝不动 STRING token 内容 —— 这是 tokenize 方案相对
    正则替换的全部意义（字符串里的 "True" 不能被误改）。tokenize 失败
    （文本不是 Python 字面量形态）→ 原文返回，交给 json.loads 判死刑。
    """
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, ValueError):
        return text
    out = []
    for tok in toks:
        string = tok.string
        if tok.type == tokenize.NAME:
            string = _PY_LITERAL_TO_JSON.get(string, string)
        out.append(tokenize.TokenInfo(tok.type, string, tok.start, tok.end, tok.line))
    return tokenize.untokenize(out)


def _first_brace_span(raw: str) -> str | None:
    """首个 '{' 到末个 '}' 的文本跨度（第四级的"首个对象块"修正目标）。"""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    return raw[start : end + 1]


def _lvl_python_literals(raw: str) -> dict | None:
    """第四级：Python 字面量修正后 parse —— 依次作用于全文 / 首个对象块 / 代码块。"""
    targets = [raw]
    span = _first_brace_span(raw)
    if span is not None and span != raw:
        targets.append(span)
    targets.extend(block.strip() for block in _JSON_FENCE_RE.findall(raw))
    for text in targets:
        data = _loads_dict(_python_literals_to_json(text))
        if data is not None:
            return data
    return None


_LEVELS = (_lvl_direct, _lvl_raw_decode, _lvl_fenced, _lvl_python_literals)


def parse_llm_json(raw: str) -> dict:
    """容错解析 LLM 输出为 dict：四级策略降级链，全失败 raise LLMError。

    ① 直接 json.loads；② raw_decode 取首个完整 JSON 对象（前后废话）；
    ③ ```json``` 代码块抽取（多个候选逐个试）；④ tokenize 把字符串字面量
    之外的 True/False/None 修成 true/false/null 后 parse（作用于全文 /
    首个对象块 / 代码块）。非 dict 顶层在每一级都拒绝。
    """
    if not isinstance(raw, str):
        raise LLMError(f"LLM 输出不是 str: {type(raw).__name__}")
    for level in _LEVELS:
        data = level(raw)
        if data is not None:
            return data
    raise LLMError(f"LLM 输出四级策略均无法解析为 JSON 对象: {raw[:120]!r}")


def _validate_payload(data: dict) -> dict[str, str]:
    """三键齐全且为非空 str；违规 raise LLMError（调用方回注重试）。"""
    bad = [
        k
        for k in REQUIRED_KEYS
        if not isinstance(data.get(k), str) or not data.get(k, "").strip()
    ]
    if bad:
        raise LLMError(f"LLM 输出键缺失/非字符串/为空: {bad}（需要 {REQUIRED_KEYS}）")
    return {k: data[k].strip() for k in REQUIRED_KEYS}


def _chat_json(
    llm: ChatLLM, messages: list[dict], *, max_attempts: int = MAX_ATTEMPTS
) -> dict[str, str]:
    """chat(带 PROPOSAL_SCHEMA 结构化输出) → parse_llm_json → 三键校验，失败回注重试。"""
    convo = list(messages)
    last: Exception | None = None
    for _ in range(max_attempts):
        try:
            raw = llm.chat(convo, json_mode=True, json_schema=PROPOSAL_SCHEMA)
            return _validate_payload(parse_llm_json(raw))
        except LLMError as exc:
            last = exc
            convo = convo + [
                {
                    "role": "user",
                    "content": f"上一次输出不合规：{exc}\n请严格按输出契约重新输出 JSON。",
                }
            ]
    raise LLMError(f"LLM 输出连续 {max_attempts} 次不合规: {last}") from last


# ── 三个进化算子（只产出候选，不做数值判定）─────────────────────────────────


def propose(
    direction: str,
    pool: TrajectoryPool,
    llm: ChatLLM,
    *,
    rng: random.Random | None = None,
    hint: str | None = None,
) -> dict[str, str]:
    """origin 算子：按探索方向原创候选。``hint`` 供调用方回注上一轮的失败原因。"""
    return _chat_json(llm, _propose_prompt(direction, pool, rng, hint))


def mutate(
    parent: Trajectory, pool: TrajectoryPool, llm: ChatLLM, *, hint: str | None = None
) -> dict[str, str]:
    """mutation 算子：基于父代做机制级变异（prompt 注入父代摘要 + 正交性约束）。"""
    return _chat_json(llm, _mutate_prompt(parent, pool, hint))


def crossover(
    parents: list[Trajectory],
    pool: TrajectoryPool,
    llm: ChatLLM,
    *,
    hint: str | None = None,
) -> dict[str, str]:
    """crossover 算子：≥2 父代机制互补杂交（不足 2 父 → ValueError，调用方先降级）。"""
    if len(parents) < 2:
        raise ValueError(f"crossover 需要 ≥2 个父代，实际 {len(parents)} 个")
    return _chat_json(llm, _crossover_prompt(parents, pool, hint))


def _interpret_fallback(trajectory: Trajectory, sota: Trajectory | None) -> str:
    """确定性解读模板（LLM 失败时的兜底）：如实列出指标数值，绝不虚构结论。"""
    mm = trajectory.mining_metrics
    parts = [
        "（LLM 解读不可用，以下为确定性模板）"
        f"决策={trajectory.decision}；"
        f"挖掘窗有效截面日={mm.get('n_days', 0)}，"
        f"RankIC均值={_fmt_metric(mm.get('rank_ic_mean'))}，"
        f"RankICIR={_fmt_metric(mm.get('rank_icir'))}。"
    ]
    if sota is not None:
        parts.append(
            f"当前最优表达式 {sota.expression}（RankIC均值="
            f"{_fmt_metric(sota.mining_metrics.get('rank_ic_mean'))}，"
            f"RankICIR={_fmt_metric(sota.mining_metrics.get('rank_icir'))}）。"
        )
    return "".join(parts)


def interpret(trajectory: Trajectory, sota: Trajectory | None, llm: ChatLLM) -> str:
    """反馈解读：LLM 写一段中文解读；LLM 任何失败 → 确定性模板，绝不 raise。"""
    try:
        text = llm.chat(_interpret_prompt(trajectory, sota), json_mode=False)
        if isinstance(text, str) and text.strip():
            return text.strip()
    except Exception:  # noqa: BLE001 —— 反馈缺失不许拖死进化循环，模板兜底
        pass
    return _interpret_fallback(trajectory, sota)


# ── 确定性判定（唯一有权写 decision 的函数）─────────────────────────────────


def _half_window_means(rank_ic_series: pd.Series) -> tuple[float, float]:
    """逐日 RankIC 序列按日期序 n//2 切前后半窗，返回两半各自的均值。

    镜像 score_return_study.half_window_check 的「按日期序对半切」切法
    （R3 半窗纪律在 IC 序列形态下的对应物）；半窗内含 NaN（skipna=False）
    或半窗为空 → 该半均值 nan，由调用方 fail-closed（``nan > 0`` 为 False）。
    """
    first = rank_ic_series.iloc[: len(rank_ic_series) // 2]
    second = rank_ic_series.iloc[len(rank_ic_series) // 2 :]
    m1 = float(first.mean(skipna=False)) if len(first) else float("nan")
    m2 = float(second.mean(skipna=False)) if len(second) else float("nan")
    return m1, m2


def judge_mining(
    stats: ICStats,
    comp: Complexity,
    *,
    rank_ic_series: pd.Series,
    min_days: int = 20,
    min_rank_ic: float = 0.02,
    min_rank_icir: float = 0.1,
) -> tuple[str, list[str]]:
    """挖掘窗确定性判定 → (decision, reasons)。

    ``violations(comp)`` 非空 → 直接 fail（复杂度违规的候选不值得谈指标）；
    其余按有效日数 / rank_ic_mean / rank_icir 阈值 + **前后半窗 RankIC 均值
    同正**（R3 纪律：``rank_ic_series`` 按日期序 n//2 切半，两半均值都必须
    > 0 —— 池化均值为正但半窗翻转的因子是 regime 假象，本仓库 R10/R4/R22
    反复踩过）判。NaN 读数 / NaN·空半窗一律不达标（``nan >= x`` 为 False，
    fail-closed）。reasons 空 = pass。
    """
    viol = violations(comp)
    if viol:
        return "fail", [f"复杂度违规: {v}" for v in viol]
    reasons = []
    if stats.n_days < min_days:
        reasons.append(f"有效截面日数 {stats.n_days} < {min_days}")
    if not stats.rank_ic_mean >= min_rank_ic:
        reasons.append(f"rank_ic_mean={stats.rank_ic_mean:.4f} < {min_rank_ic}")
    if not stats.rank_icir >= min_rank_icir:
        reasons.append(f"rank_icir={stats.rank_icir:.4f} < {min_rank_icir}")
    m1, m2 = _half_window_means(rank_ic_series)
    if not (m1 > 0 and m2 > 0):
        reasons.append(
            f"前后半窗 RankIC 均值须同正（R3 纪律）: 前半={m1:.4f} 后半={m2:.4f}"
        )
    return ("fail" if reasons else "pass"), reasons
