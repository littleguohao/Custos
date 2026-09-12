# -*- coding: utf-8 -*-
"""planning（TODO #72 规划层）钉测：LLM 扩展 / 错误回注重试 / fallback 补足 / 来源标记。

fallback 是正常路径（不 raise），但每条方向的来源（llm/fallback）必须可区分；
fallback 模板纯确定性（同种子恒等输出）。
"""

from __future__ import annotations

import json

import pytest

from custos.research.evolution.llm_client import LLMError
from custos.research.evolution.planning import (
    FALLBACK_TEMPLATES,
    generate_directions,
)
from custos.research.evolution.trajectory import Trajectory, TrajectoryPool, make_id


class FakeLLM:
    """队列假 LLM：str 原文返回 / dict 转 JSON / Exception 抛出。"""

    def __init__(self, queue: list) -> None:
        self.queue = list(queue)
        self.calls: list[list[dict]] = []
        self.total_tokens = 0

    def chat(self, messages, *, json_mode=True, json_schema=None):
        self.calls.append(list(messages))
        if not self.queue:
            raise LLMError("脚本耗尽")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)


def _pool_with_direction(direction: str) -> TrajectoryPool:
    created = "2026-09-12T00:00:00+00:00"
    pool = TrajectoryPool()
    pool.add(
        Trajectory(
            id=make_id(
                direction=direction,
                hypothesis="h",
                expression="ROC(CLOSE,5)",
                created_at=created,
            ),
            direction=direction,
            phase="origin",
            hypothesis="h",
            expression="ROC(CLOSE,5)",
            complexity={},
            mining_metrics={"rank_ic_mean": 0.05},
            decision="pass",
            feedback="",
            parent_ids=(),
            created_at=created,
            run_tag="t",
        )
    )
    return pool


class TestGenerateDirections:
    def test_llm_normal_truncates_to_n(self):
        llm = FakeLLM([{"directions": ["动量", "反转", "量能"]}])
        out = generate_directions("量价背离", 2, llm)
        assert [p.direction for p in out] == ["动量", "反转"]  # 恰好 n 条（截断）
        assert all(p.source == "llm" for p in out)

    def test_dedup_and_pool_existing_skipped(self):
        pool = _pool_with_direction("动量")  # 池内已有「动量」
        llm = FakeLLM([{"directions": ["动量", "  反转  ", "反转", "量能"]}])
        out = generate_directions("量价背离", 2, llm, pool=pool)
        assert [p.direction for p in out] == [
            "反转",
            "量能",
        ]  # 去空白+批内去重+剔除池内

    def test_prompt_carries_orthogonality_context(self):
        llm = FakeLLM([{"directions": ["a", "b"]}])
        generate_directions("量价背离", 2, llm, pool=_pool_with_direction("动量"))
        prompt = llm.calls[0][-1]["content"]
        assert "量价背离" in prompt and "动量" in prompt  # 种子 + 池内已有方向注入
        assert '"directions"' in prompt  # 输出契约
        assert "TS_RANK" in prompt  # DSL 能力面

    def test_bad_json_retries_then_fallback(self):
        llm = FakeLLM(["不是 JSON", LLMError("boom"), "还是坏"])
        out = generate_directions("量价背离", 3, llm)
        assert len(llm.calls) == 3  # 错误回注重试 ≤3 次
        assert len(out) == 3
        assert all(p.source == "fallback" for p in out)  # 来源可区分
        assert "不合规" in llm.calls[1][-1]["content"]  # 回注内容

    def test_partial_llm_topped_up_by_fallback(self):
        # LLM 每轮只产 1 条有效方向（不足 n=2）→ 重试后仍不足 → fallback 补足
        llm = FakeLLM(
            [
                {"directions": ["量能确认"]},
                {"directions": ["量能确认"]},
                {"directions": ["量能确认"]},
            ]
        )
        out = generate_directions("量价背离", 2, llm)
        assert len(out) == 2
        assert out[0].direction == "量能确认" and out[0].source == "llm"
        assert out[1].source == "fallback"  # 第二格由模板补足

    def test_fallback_deterministic(self):
        a = generate_directions("量价背离", 4, FakeLLM([LLMError("x")]))
        # 重试 3 次都要耗脚本——给 3 个异常
        b_llm = FakeLLM([LLMError("x"), LLMError("x"), LLMError("x")])
        b = generate_directions("量价背离", 4, b_llm)
        a2 = generate_directions(
            "量价背离", 4, FakeLLM([LLMError("x"), LLMError("x"), LLMError("x")])
        )
        assert a2 == b  # 同种子同输出（fallback 无随机）
        assert [p.direction for p in b][: len(FALLBACK_TEMPLATES)] == [
            t.format(seed="量价背离") for t in FALLBACK_TEMPLATES[:4]
        ]

    def test_fallback_skips_pool_directions(self):
        pool = _pool_with_direction("量价背离 + 量能确认")  # 首个模板已被池占用
        out = generate_directions(
            "量价背离", 2, FakeLLM([LLMError("x")] * 3), pool=pool
        )
        assert all(p.direction != "量价背离 + 量能确认" for p in out)
        assert out[0].direction == "量价背离 + 波动率过滤"  # 顺延到下一个模板

    def test_fallback_numbered_beyond_templates(self):
        # n 超过模板数 → 编号变体兜底，仍恰好 n 条
        n = len(FALLBACK_TEMPLATES) + 2
        out = generate_directions("量价背离", n, FakeLLM([LLMError("x")] * 3))
        assert len(out) == n
        assert out[-1].direction.startswith("量价背离（变体")

    def test_n_validation(self):
        for bad in (0, -1):
            with pytest.raises(ValueError, match="n 必须"):
                generate_directions("量价背离", bad, FakeLLM([]))

    def test_empty_seed_rejected(self):
        with pytest.raises(ValueError, match="不能为空"):
            generate_directions("   ", 2, FakeLLM([]))
