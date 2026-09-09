# -*- coding: utf-8 -*-
"""内置确定性 MockLLM：--mock-llm 演示与无 LLM 环境下跑通进化全流程。

5 条固定脚本**轮换**产出（确定性、无网络、不读环境）：4 条合法 DSL 表达式
+ 1 条必然 fail 的对照（``CLOSE/CLOSE`` 恒值 1.0 → 截面零方差 → 有效日 0，
过不了 judge_mining 的 min_days 门），端到端演示 pass/fail 两条轨迹路径。
``interpret`` 的非 JSON 调用（json_mode=False）返回固定中文解读。
"""

from __future__ import annotations

import json

SCRIPT: tuple[dict[str, str], ...] = (
    {
        "hypothesis": "短期动量延续：5 日涨幅高的股票未来 5 日继续走强",
        "expression": "ROC(CLOSE,5)",
        "rationale": "价格动量的截面排序",
    },
    {
        "hypothesis": "乖离回复：价格相对 20 日均线的偏离倾向回归",
        "expression": "CLOSE/MA(CLOSE,20)",
        "rationale": "价格-均线乖离机制",
    },
    {
        "hypothesis": "量能分位：近期放量的股票短期更受资金关注",
        "expression": "TS_RANK(VOLUME,10)",
        "rationale": "成交量的时序分位机制",
    },
    {
        "hypothesis": "振幅均值：日内振幅压缩后倾向扩张",
        "expression": "MA((HIGH-LOW)/CLOSE,10)",
        "rationale": "波动率的时序均值机制",
    },
    {
        "hypothesis": "（必败对照）恒值因子无截面区分度",
        "expression": "CLOSE/CLOSE",
        "rationale": "恒值 1.0 截面零方差，有效日 0 必然 fail",
    },
)


class MockLLM:
    """确定性脚本化 proposer：与 LLMClient 同构（chat + total_tokens）。"""

    def __init__(self) -> None:
        self._n = 0
        self.total_tokens = 0

    def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = True,
        json_schema: dict | None = None,
    ) -> str:
        # token 计数按消息字数粗估（仅供 --max-tokens-budget 演示提前收敛）。
        self.total_tokens += max(
            1, sum(len(str(m.get("content", ""))) for m in messages) // 4
        )
        if not json_mode:  # interpret 的解读调用
            return (
                "（MockLLM 解读）该候选的挖掘窗读数已如实记入轨迹；"
                "请结合 RankIC/ICIR、判定原因与父代血统研判其机制价值。"
            )
        item = SCRIPT[self._n % len(SCRIPT)]
        self._n += 1
        return json.dumps(item, ensure_ascii=False)
