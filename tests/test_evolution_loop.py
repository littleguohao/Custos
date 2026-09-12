# -*- coding: utf-8 -*-
"""LLM 因子进化引擎（循环层）钉测：无网络、无通达信，全合成数据 + 脚本化假 LLM。

覆盖：
  ① 端到端 run_loop：phase 轮换 / 失败轨迹在池 / 血统（mutation 恰 1 父、
     crossover ≥2 父）/ pass 轨迹带数值 rank_ic_mean；
  ② 双窗隔离钉：篡改 mining_end 之后的数据，循环产出**逐位不变**；
  ③ final_judgment：窗口重叠 ValueError；正常路径用判定窗数据；
  ④ judge_mining 阈值边界逐个；parse_llm_json 四种形态；
  ⑤ llm_client：_post 可 monkeypatch（URL/payload/降级重试/LLMError/token 计数）、
     from_env 缺变量 → None；
  ⑥ CLI 冒烟：mock 数据加载 + --mock-llm 在 tmp_path 落产物；空结果护栏
     非零退出不写产物；--final-judge 重新加载数据；token 预算提前收敛。
"""

from __future__ import annotations

import json
import random

import pandas as pd
import pytest

from custos.research import evolution_loop as el
from custos.research.evolution import genome
from custos.research.evolution import llm_client as lc
from custos.research.evolution import loop as loop_mod
from custos.research.evolution import operators as ops
from custos.research.evolution.dual_window import DualWindowResult, Window
from custos.research.evolution.expr_dsl import Complexity
from custos.research.evolution.ic_eval import ICStats
from custos.research.evolution.llm_client import LLMError
from custos.research.evolution.mock_llm import MockLLM
from custos.research.evolution.trajectory import Trajectory, TrajectoryPool, make_id

N_DAYS = 300
DATES = pd.date_range("2024-01-01", periods=N_DAYS, freq="B")
MINING = Window(str(DATES[0].date()), str(DATES[199].date()))
JUDGMENT = Window(str(DATES[220].date()), str(DATES[299].date()))


def make_bars(n_stocks: int = 10) -> dict[str, pd.DataFrame]:
    """合成宇宙：每股日漂移按代码序递增 ⇒ ROC 类表达式与未来收益秩强正相关。

    确定性噪声（模运算，无 RNG）让逐日 IC 有 std（ICIR 有定义）；
    每股成交量恒定 ⇒ TS_RANK(VOLUME,n) 截面零方差 ⇒ 有效日 0 必然 fail。
    """
    out = {}
    for i in range(n_stocks):
        r = 0.002 + 0.001 * i
        price, closes = 100.0, []
        for t in range(N_DAYS):
            eps = 0.005 * (((i * 3 + t * 7) % 4) - 1.5)
            price *= (1 + r) * (1 + eps)
            closes.append(price)
        out[f"S{i:03d}"] = pd.DataFrame(
            {
                "date": DATES,
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": [1000.0 + 10.0 * i] * N_DAYS,
                "amount": [0.0] * N_DAYS,
            }
        )
    return out


class ScriptedLLM:
    """脚本化假 LLM：json_mode 调用按队列弹出产出；非 json_mode 返回固定解读。

    队列项：dict → JSON 字符串；str → 原文返回（坏输出用例）；Exception → 抛出。
    """

    def __init__(self, queue: list) -> None:
        self.queue = list(queue)
        self.calls: list[tuple[list[dict], bool, dict | None]] = []
        self.total_tokens = 0

    def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = True,
        json_schema: dict | None = None,
    ) -> str:
        self.calls.append((list(messages), json_mode, json_schema))
        self.total_tokens += 1
        if not json_mode:
            return "（假解读）读数已如实记录。"
        if not self.queue:
            raise LLMError("脚本耗尽")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return item
        return json.dumps(item, ensure_ascii=False)


class RaisingLLM:
    """永远失败的 LLM（LLM 全挂场景）。"""

    def __init__(self) -> None:
        self.total_tokens = 0

    def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = True,
        json_schema: dict | None = None,
    ) -> str:
        raise LLMError("always down")


def _cand(expr: str, hypothesis: str = "假设") -> dict:
    return {"hypothesis": hypothesis, "expression": expr, "rationale": "机制"}


def _mk_traj(
    expr: str,
    *,
    direction: str = "d",
    ric: float | None = 0.05,
    ricir: float = 0.5,
) -> Trajectory:
    created = "2026-01-01T00:00:00+00:00"
    metrics = (
        {} if ric is None else {"rank_ic_mean": ric, "rank_icir": ricir, "n_days": 100}
    )
    return Trajectory(
        id=make_id(
            direction=direction,
            hypothesis="h_" + expr,
            expression=expr,
            created_at=created,
        ),
        direction=direction,
        phase="origin",
        hypothesis="h_" + expr,
        expression=expr,
        complexity={},
        mining_metrics=metrics,
        decision="pass",
        feedback="",
        parent_ids=(),
        created_at=created,
        run_tag="t",
    )


def _cfg(**kw) -> loop_mod.LoopConfig:
    base = dict(
        directions=("动量",),
        rounds=1,
        candidates_per_round=1,
        mining_start=MINING.start,
        mining_end=MINING.end,
    )
    base.update(kw)
    return loop_mod.LoopConfig(**base)


# ---------- llm_client ----------


class TestLLMClient:
    def test_from_env_missing_returns_none(self, monkeypatch):
        for v in (lc.ENV_BASE_URL, lc.ENV_API_KEY, lc.ENV_MODEL, lc.ENV_TIMEOUT):
            monkeypatch.delenv(v, raising=False)
        assert lc.LLMConfig.from_env() is None

    def test_from_env_ok_and_bad_timeout(self, monkeypatch):
        monkeypatch.setenv(lc.ENV_BASE_URL, "https://x/v1/")
        monkeypatch.setenv(lc.ENV_API_KEY, "k")
        monkeypatch.setenv(lc.ENV_MODEL, "m")
        monkeypatch.delenv(lc.ENV_TIMEOUT, raising=False)
        cfg = lc.LLMConfig.from_env()
        assert cfg is not None
        assert cfg.base_url == "https://x/v1"  # 尾部斜杠去掉
        assert cfg.timeout == 60
        monkeypatch.setenv(lc.ENV_TIMEOUT, "abc")
        assert lc.LLMConfig.from_env() is None  # 非法超时 fail-closed

    def test_chat_payload_and_token_count(self, monkeypatch):
        calls = []

        def fake_post(config, payload):
            calls.append(payload)
            return {
                "choices": [{"message": {"content": "你好"}}],
                "usage": {"total_tokens": 11},
            }

        monkeypatch.setattr(lc, "_post", fake_post)
        client = lc.LLMClient(
            lc.LLMConfig(base_url="https://x/v1", api_key="k", model="m")
        )
        out = client.chat([{"role": "user", "content": "hi"}], json_mode=True)
        assert out == "你好" and client.total_tokens == 11
        assert calls[0]["response_format"] == {"type": "json_object"}
        assert calls[0]["model"] == "m"
        # json_mode=False 不带 response_format；usage 缺失不计 token
        monkeypatch.setattr(
            lc, "_post", lambda c, p: {"choices": [{"message": {"content": "x"}}]}
        )
        assert client.chat([{"role": "user", "content": "hi"}], json_mode=False) == "x"
        assert client.total_tokens == 11

    def test_chat_retry_degrades_json_mode(self, monkeypatch):
        seen = []

        def flaky(config, payload):
            seen.append("response_format" in payload)
            if len(seen) == 1:
                # 400 类失败才触发降级（带 status_code，模拟真实 HTTP 400）
                raise LLMError(
                    "HTTP 400: response_format not supported", status_code=400
                )
            return {"choices": [{"message": {"content": "ok"}}]}

        monkeypatch.setattr(lc, "_post", flaky)
        client = lc.LLMClient(
            lc.LLMConfig(base_url="https://u/v1", api_key="k", model="m", max_retry=3)
        )
        client.sleep = lambda s: None
        assert client.chat([{"role": "user", "content": "x"}]) == "ok"
        assert seen == [True, False]  # 首轮带 response_format，重试降级不带

    def test_chat_exhausts_retries(self, monkeypatch):
        def always_fail(config, payload):
            raise LLMError("down")

        monkeypatch.setattr(lc, "_post", always_fail)
        client = lc.LLMClient(
            lc.LLMConfig(base_url="https://u/v1", api_key="k", model="m", max_retry=3)
        )
        client.sleep = lambda s: None
        with pytest.raises(LLMError, match="重试 3 次"):
            client.chat([{"role": "user", "content": "x"}])

    def test_post_url_headers_http_error(self, monkeypatch):
        captured = {}

        class Resp:
            status_code = 500
            text = "server error"

            def json(self):
                return {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(url=url, headers=headers, timeout=timeout)
            return Resp()

        monkeypatch.setattr(lc.requests, "post", fake_post)
        cfg = lc.LLMConfig(
            base_url="https://api.example.com/v1", api_key="sk", model="m", timeout=9
        )
        with pytest.raises(LLMError, match="HTTP 500"):
            lc._post(cfg, {"model": "m", "messages": []})
        assert captured["url"] == "https://api.example.com/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer sk"
        assert captured["timeout"] == 9


# ---------- parse_llm_json ----------


class TestParseLLMJson:
    def test_pure_json(self):
        assert ops.parse_llm_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        raw = '```json\n{"a": 1, "b": "x"}\n```'
        assert ops.parse_llm_json(raw) == {"a": 1, "b": "x"}

    def test_prefix_chatter_and_brace_in_string(self):
        raw = '好的，结果如下：{"hypothesis": "a{b}方案", "expression": "ROC(CLOSE,5)"} 以上。'
        out = ops.parse_llm_json(raw)
        assert out["hypothesis"] == "a{b}方案"

    def test_bad_json_raises(self):
        with pytest.raises(LLMError):
            ops.parse_llm_json("没有任何 JSON 对象")
        with pytest.raises(LLMError):
            ops.parse_llm_json('{"a": {"b": 1')  # 括号不闭合
        with pytest.raises(LLMError):
            ops.parse_llm_json("[1, 2]")  # 顶层不是对象 → 每一级都拒绝
        with pytest.raises(LLMError):
            ops.parse_llm_json('{"a": }')  # 有平衡块但 JSON 坏

    def test_level1_direct_loads(self):
        assert ops._lvl_direct('  {"a": 1} \n') == {"a": 1}

    def test_level2_raw_decode_skips_chatter(self):
        assert ops._lvl_raw_decode('前缀废话 {"a": 1} 尾部') == {"a": 1}

    def test_level3_fenced_multiple_candidates(self):
        # 多个代码块逐个试：第一块坏、第二块好（第二级整文扫会先命中第二块，
        # 这里钉第三级函数自身的多候选语义）
        raw = '```json\n{"a": }\n```\n说明\n```json\n{"b": 2}\n```'
        assert ops._lvl_fenced(raw) == {"b": 2}

    def test_level4_python_literals(self):
        # LLM 把 Python repr 当 JSON：True/None 大写 → 第四级 tokenize 修正
        raw = '{"hypothesis": "动量", "expression": "ROC(CLOSE,5)", "ok": True, "n": None}'
        out = ops.parse_llm_json(raw)
        assert out["ok"] is True and out["n"] is None

    def test_level4_preserves_literals_inside_strings(self):
        # tokenize 方案的全部意义：字符串里的 "True" 绝不能被误改
        raw = '{"hypothesis": "True 是字符串内容", "expression": "ROC(CLOSE,5)", "f": True}'
        out = ops.parse_llm_json(raw)
        assert out["hypothesis"] == "True 是字符串内容"
        assert out["f"] is True

    def test_level4_via_brace_span(self):
        raw = '解释如下：{"a": True}，以上'  # 前缀 + Python 字面量
        assert ops.parse_llm_json(raw) == {"a": True}


# ---------- operators ----------


class TestOperators:
    def test_propose_injects_rules_and_pool(self):
        pool = TrajectoryPool()
        pool.add(_mk_traj("ROC(CLOSE,5)"))
        llm = ScriptedLLM([_cand("MA(CLOSE,10)")])
        out = ops.propose("量价背离", pool, llm, rng=random.Random(0))
        assert out == {
            "hypothesis": "假设",
            "expression": "MA(CLOSE,10)",
            "rationale": "机制",
        }
        prompt = llm.calls[0][0][-1]["content"]
        assert "ROC(CLOSE,5)" in prompt  # 池内已有表达式（正交性去重列表）
        for v in ("open", "high", "low", "close", "volume"):
            assert v in prompt
        assert "TS_RANK" in prompt and "≤300" in prompt  # 算子签名 + 复杂度上限
        assert "0.0500" in prompt  # 池摘要注入指标读数

    def test_mutate_injects_parent_digest(self):
        parent = _mk_traj("ROC(CLOSE,5)")
        pool = TrajectoryPool()
        pool.add(parent)
        llm = ScriptedLLM([_cand("MA(CLOSE,10)")])
        ops.mutate(parent, pool, llm)
        prompt = llm.calls[0][0][-1]["content"]
        assert "机制级变异" in prompt and "ROC(CLOSE,5)" in prompt

    def test_crossover_requires_two_parents(self):
        with pytest.raises(ValueError, match="≥2"):
            ops.crossover([_mk_traj("ROC(CLOSE,5)")], TrajectoryPool(), ScriptedLLM([]))

    def test_crossover_prompt_has_both_parents(self):
        a = _mk_traj("ROC(CLOSE,5)", direction="d1")
        b = _mk_traj("DELTA(CLOSE,3)", direction="d2")
        pool = TrajectoryPool()
        pool.add(a)
        pool.add(b)
        llm = ScriptedLLM([_cand("ROC(CLOSE,5)*TS_RANK(VOLUME,10)")])
        ops.crossover([a, b], pool, llm)
        prompt = llm.calls[0][0][-1]["content"]
        assert "ROC(CLOSE,5)" in prompt and "DELTA(CLOSE,3)" in prompt
        assert "杂交" in prompt

    def test_propose_passes_json_schema(self):
        # structured output：三键契约的 JSON Schema 传给 llm.chat
        llm = ScriptedLLM([_cand("ROC(CLOSE,5)")])
        ops.propose("d", TrajectoryPool(), llm)
        assert llm.calls[0][1] is True  # json_mode
        assert llm.calls[0][2] is ops.PROPOSAL_SCHEMA
        assert ops.PROPOSAL_SCHEMA["required"] == list(ops.REQUIRED_KEYS)

    def test_chat_json_retries_with_error_feedback(self):
        llm = ScriptedLLM(["这不是 JSON", _cand("ROC(CLOSE,5)")])
        out = ops.propose("d", TrajectoryPool(), llm)
        assert out["expression"] == "ROC(CLOSE,5)"
        assert len(llm.calls) == 2
        assert "不合规" in llm.calls[1][0][-1]["content"]  # 错误原因回注

    def test_chat_json_exhausts(self):
        llm = ScriptedLLM(["坏1", "坏2", "坏3"])
        with pytest.raises(LLMError, match="连续 3 次"):
            ops.propose("d", TrajectoryPool(), llm)
        assert len(llm.calls) == 3

    def test_validate_payload_missing_key(self):
        with pytest.raises(LLMError, match="缺失"):
            ops._validate_payload({"hypothesis": "h", "expression": "x"})

    def test_interpret_llm_text(self):
        class TextLLM:
            total_tokens = 0

            def chat(self, messages, *, json_mode=True, json_schema=None):
                assert json_mode is False and json_schema is None
                return "  这段解读不错。  "

        assert (
            ops.interpret(_mk_traj("ROC(CLOSE,5)"), None, TextLLM()) == "这段解读不错。"
        )

    def test_interpret_fallback_never_raises(self):
        fb = ops.interpret(
            _mk_traj("ROC(CLOSE,5)", ric=0.05, ricir=0.5), None, RaisingLLM()
        )
        assert "确定性模板" in fb and "0.0500" in fb and "0.5000" in fb

    def test_interpret_fallback_includes_sota(self):
        sota = _mk_traj("DELTA(CLOSE,3)", ric=0.08, ricir=0.9)
        fb = ops.interpret(_mk_traj("ROC(CLOSE,5)"), sota, RaisingLLM())
        assert "DELTA(CLOSE,3)" in fb and "0.0800" in fb


# ---------- judge_mining ----------


def _stats(n_days: int = 100, ric: float = 0.05, ricir: float = 0.5) -> ICStats:
    return ICStats(
        n_days=n_days,
        ic_mean=ric,
        icir=ricir,
        rank_ic_mean=ric,
        rank_icir=ricir,
        horizon=5,
        start=None,
        end=None,
    )


GOOD_COMP = Complexity(
    symbol_len=12, free_params=1, base_features=1, repeat_subtrees=0, depth=2
)

# 前后半窗均值均正的逐日 RankIC 序列（半窗门不挡路，单独钉其它门用）。
GOOD_SERIES = pd.Series([0.05] * 100)


def _judge(stats, comp=GOOD_COMP, series=GOOD_SERIES):
    return ops.judge_mining(stats, comp, rank_ic_series=series)


class TestJudgeMining:
    def test_complexity_violations_fail_fast(self):
        bad = Complexity(
            symbol_len=301, free_params=0, base_features=0, repeat_subtrees=0, depth=1
        )
        decision, reasons = _judge(_stats(), bad)
        assert decision == "fail" and any("symbol_len" in r for r in reasons)

    def test_boundaries(self):
        assert _judge(_stats(n_days=19))[0] == "fail"
        assert _judge(_stats(n_days=20))[0] == "pass"
        assert _judge(_stats(ric=0.02))[0] == "pass"
        assert _judge(_stats(ric=0.0199))[0] == "fail"
        assert _judge(_stats(ricir=0.1))[0] == "pass"
        assert _judge(_stats(ricir=0.099))[0] == "fail"

    def test_nan_fails_closed(self):
        assert _judge(_stats(ric=float("nan")))[0] == "fail"
        assert _judge(_stats(ricir=float("nan")))[0] == "fail"

    def test_half_window_flip_fails_despite_pooled_gates(self):
        # 池化门全过（n/rank_ic/rank_icir 达标），但后半窗均值为负 → R3 半窗门拒
        # （n//2=50 切半：后半 = 10 天 +0.06 + 40 天 -0.04 → 均值 -0.02）
        series = pd.Series([0.06] * 60 + [-0.04] * 40)
        stats = _stats(n_days=100, ric=0.02, ricir=0.41)
        decision, reasons = _judge(stats, series=series)
        assert decision == "fail"
        assert any("半窗" in r and "前半=0.0600 后半=-0.0200" in r for r in reasons)

    def test_half_window_split_convention(self):
        # n//2 按日期序切半：奇数序列后半多一天（前半 50 天正、后半 51 天负）
        series = pd.Series([0.05] * 50 + [-0.05] * 51)
        _decision, reasons = _judge(_stats(n_days=101), series=series)
        assert any("前半=0.0500 后半=-0.0500" in r for r in reasons)

    def test_half_window_both_positive_passes(self):
        series = pd.Series([0.01] * 50 + [0.09] * 50)  # 两半都正但不同量级
        assert _judge(_stats(), series=series)[0] == "pass"

    def test_half_window_nan_or_empty_fails_closed(self):
        assert _judge(_stats(), series=pd.Series(dtype=float))[0] == "fail"
        one = pd.Series([0.05])  # n//2 = 0 → 前半窗空 → fail-closed
        assert _judge(_stats(), series=one)[0] == "fail"
        nan_half = pd.Series([float("nan")] * 50 + [0.05] * 50)  # 前半全 NaN
        assert _judge(_stats(), series=nan_half)[0] == "fail"


# ---------- run_loop ----------


class TestRunLoop:
    def test_end_to_end_phases_lineage_failures(self):
        queue = [
            _cand("ROC(CLOSE,5)", "动量5日"),  # r0c0 origin → pass
            _cand("DELTA(CLOSE,3)", "价差3日"),  # r0c1 origin → pass
            _cand("EMA(CLOSE,5)", "h2"),  # r1c0 mutation → 非白名单 → 回注重试
            _cand("ROC(CLOSE,10)", "动量10日"),  # r1c0 重试 → pass
            _cand(  # r1c1 mutation → 7 种数值字面量 → 复杂度 fail
                "MA(CLOSE,1)+MA(CLOSE,2)+MA(CLOSE,3)+MA(CLOSE,4)"
                "+MA(CLOSE,5)+MA(CLOSE,6)+MA(CLOSE,7)",
                "超复杂度",
            ),
            _cand("ROC(CLOSE,20)", "动量20日"),  # r2c0 crossover → pass
            _cand("TS_RANK(ROC(CLOSE,5),10)", "动量分位"),  # r2c1 crossover
        ]
        llm = ScriptedLLM(queue)
        pool = TrajectoryPool()
        events: list[dict] = []
        loop_mod.run_loop(
            _cfg(rounds=3, candidates_per_round=2),
            make_bars(),
            llm,
            pool,
            on_event=events.append,
        )
        all_t = pool.all()
        assert len(all_t) == 6  # 每个候选都落池（失败也落，教训数据）
        assert [t.phase for t in all_t] == [
            "origin",
            "origin",
            "mutation",
            "mutation",
            "crossover",
            "crossover",
        ]
        assert all_t[0].parent_ids == () and all_t[1].parent_ids == ()
        for t in all_t[2:4]:  # mutation 恰 1 父且引用池内轨迹
            assert len(t.parent_ids) == 1
            assert pool.get(t.parent_ids[0]) is not None
        for t in all_t[4:]:  # crossover ≥2 父
            assert len(t.parent_ids) >= 2
            assert len(set(t.parent_ids)) == len(t.parent_ids)
        # 失败轨迹在池：超复杂度 → fail，mining_metrics 留空（不浪费回测）
        cx = all_t[3]
        assert cx.decision == "fail" and cx.mining_metrics == {}
        assert "free_params" in cx.feedback
        # pass 轨迹带数值 rank_ic_mean，feedback 含 interpret 解读
        passes = [t for t in all_t if t.decision == "pass"]
        assert len(passes) >= 3
        for t in passes:
            assert isinstance(t.mining_metrics["rank_ic_mean"], float)
            assert "假解读" in t.feedback
        # DSL 错误确实回注了 LLM（某次调用的 prompt 带着 ExprError 原因）
        assert any(
            "上次表达式未通过 DSL 白名单" in call[0][-1]["content"]
            for call in llm.calls
        )
        # 事件流：每候选一条，键齐全
        assert len(events) == 6
        for ev in events:
            for k in ("phase", "direction", "expression", "decision", "rank_ic_mean"):
                assert k in ev

    def test_mutation_parent_is_pool_best(self):
        a = _mk_traj("ROC(CLOSE,5)", direction="动量", ric=0.05, ricir=0.3)
        b = _mk_traj("DELTA(CLOSE,3)", direction="动量", ric=0.06, ricir=0.9)
        pool = TrajectoryPool()
        pool.add(a)
        pool.add(b)
        loop_mod.run_loop(
            _cfg(phase_schedule=("mutation",)),
            make_bars(),
            ScriptedLLM([_cand("ROC(CLOSE,10)", "子代")]),
            pool,
        )
        child = [t for t in pool.all() if t.phase == "mutation"][0]
        assert child.parent_ids == (b.id,)  # best 按 rank_icir 降序 → b

    def test_crossover_parents_prefer_two_directions(self):
        a = _mk_traj("ROC(CLOSE,5)", direction="d1", ricir=0.5)
        b = _mk_traj("DELTA(CLOSE,3)", direction="d2", ricir=0.4)
        pool = TrajectoryPool()
        pool.add(a)
        pool.add(b)
        loop_mod.run_loop(
            _cfg(directions=("d1",), phase_schedule=("crossover",)),
            make_bars(),
            ScriptedLLM([_cand("ROC(CLOSE,10)", "杂交子代")]),
            pool,
        )
        child = pool.all()[-1]
        assert child.phase == "crossover"
        assert set(child.parent_ids) == {a.id, b.id}  # 两方向优先

    def test_degrades_to_origin_when_pool_empty(self):
        pool = TrajectoryPool()
        loop_mod.run_loop(
            _cfg(phase_schedule=("mutation",)),
            make_bars(),
            ScriptedLLM([_cand("ROC(CLOSE,5)")]),
            pool,
        )
        t = pool.all()[0]
        assert t.phase == "origin" and t.parent_ids == ()

    def test_dsl_fail_twice_records_raw_expression(self):
        llm = ScriptedLLM(
            [
                _cand("EMA(CLOSE,5)", "非白名单"),  # ExprError → 回注重试
                _cand("SELECT CLOSE FROM bars", "语法错"),  # 仍 ExprError → 记 fail
            ]
        )
        pool = TrajectoryPool()
        loop_mod.run_loop(_cfg(), make_bars(), llm, pool)
        t = pool.all()[0]
        assert t.decision == "fail"
        assert t.expression == "SELECT CLOSE FROM bars"  # 原文落池
        assert t.feedback.startswith("表达式未通过 DSL 白名单")
        assert t.mining_metrics == {} and t.complexity == {}

    def test_llm_down_no_trajectory_but_events(self):
        pool = TrajectoryPool()
        events: list[dict] = []
        loop_mod.run_loop(
            _cfg(candidates_per_round=2),
            make_bars(),
            RaisingLLM(),
            pool,
            on_event=events.append,
        )
        assert len(pool) == 0
        assert [e["decision"] for e in events] == ["llm_error", "llm_error"]

    def test_mining_end_isolation(self, monkeypatch):
        """篡改 mining_end 之后的数据 ⇒ 循环产出逐位不变（双窗硬隔离钉测）。"""
        monkeypatch.setattr(loop_mod, "_now_iso", lambda: "2026-09-09T00:00:00+00:00")
        queue = [_cand("ROC(CLOSE,5)", "h0"), _cand("DELTA(CLOSE,3)", "h1")]
        cfg = _cfg(candidates_per_round=2)
        bars = make_bars()
        pool_a = loop_mod.run_loop(cfg, bars, ScriptedLLM(queue), TrajectoryPool())

        tampered = {c: df.copy() for c, df in bars.items()}
        for df in tampered.values():
            after = pd.to_datetime(df["date"]) > pd.Timestamp(MINING.end)
            assert after.any()
            df.loc[after, "close"] = df.loc[after, "close"] * 100.0
            df.loc[after, "volume"] = -1.0
        pool_b = loop_mod.run_loop(cfg, tampered, ScriptedLLM(queue), TrajectoryPool())

        dump_a = json.dumps(
            [t.to_dict() for t in pool_a.all()], sort_keys=True, allow_nan=True
        )
        dump_b = json.dumps(
            [t.to_dict() for t in pool_b.all()], sort_keys=True, allow_nan=True
        )
        assert dump_a == dump_b

    def test_config_validation(self):
        with pytest.raises(ValueError):
            loop_mod.run_loop(
                _cfg(directions=()), make_bars(), RaisingLLM(), TrajectoryPool()
            )
        with pytest.raises(ValueError):
            loop_mod.run_loop(
                _cfg(phase_schedule=("badphase",)),
                make_bars(),
                RaisingLLM(),
                TrajectoryPool(),
            )


# ---------- 超长/递归表达式健壮性（RecursionError 不许杀循环） ----------


class TestRecursionRobustness:
    def test_oversized_expression_rejected_loop_survives(self):
        # ~6000 字符平铺链：旧实现在 parse/_validate 递归里 RecursionError 杀循环；
        # 现在规模上限按白名单违规拒收（失败轨迹落池），后续候选照常跑。
        giant = "+".join(["CLOSE"] * 1000)
        queue = [
            _cand(giant, "胡言"),
            _cand(giant, "胡言重试"),  # 回注重试仍是它 → 记 fail
            _cand("ROC(CLOSE,5)", "正常候选"),
        ]
        pool = TrajectoryPool()
        loop_mod.run_loop(
            _cfg(candidates_per_round=2), make_bars(), ScriptedLLM(queue), pool
        )
        fails = [t for t in pool.all() if t.decision == "fail"]
        assert len(fails) == 1
        assert fails[0].expression == giant  # 原文落池（教训数据）
        assert fails[0].feedback.startswith("表达式未通过 DSL 白名单")
        # 循环存活：第二个候选正常判定落池
        assert any(t.expression == "ROC(CLOSE,5)" for t in pool.all())

    def test_recursion_error_treated_as_invalid_candidate(self, monkeypatch):
        # 规模上限应已拦住，但防御到底：parse 万一漏出 RecursionError，
        # 同样按无效候选处理（与 ExprError 同路），绝不炸掉 run_loop。
        real_parse = loop_mod.parse
        calls: list[str] = []

        def bomb(expr):
            calls.append(expr)
            if len(calls) <= 2:
                raise RecursionError("maximum recursion depth exceeded")
            return real_parse(expr)

        monkeypatch.setattr(loop_mod, "parse", bomb)
        queue = [
            _cand("MA(CLOSE,5)", "第一次"),
            _cand("MA(CLOSE,5)", "回注重试"),
            _cand("ROC(CLOSE,5)", "后续候选"),
        ]
        pool = TrajectoryPool()
        loop_mod.run_loop(
            _cfg(candidates_per_round=2), make_bars(), ScriptedLLM(queue), pool
        )
        fails = [t for t in pool.all() if t.decision == "fail"]
        assert len(fails) == 1 and "DSL 白名单" in fails[0].feedback
        assert any(t.expression == "ROC(CLOSE,5)" for t in pool.all())


# ---------- R3 半窗同正门（前后半窗 RankIC 均值都必须 > 0） ----------


class TestHalfWindowGate:
    @staticmethod
    def make_flip_bars(n_stocks: int = 10) -> tuple[dict[str, pd.DataFrame], str, str]:
        """前段稳态正漂移、尾段 5 日块交替反转的宇宙（无 RNG，模运算噪声）。

        挖掘窗取全程：逐日 RankIC 前半段 ~+0.9、尾段 ~-0.9 —— 池化 rank_ic_mean /
        rank_icir 仍过旧阈值，但后半窗均值为负（R3 半窗门专杀的形态）。
        """
        n_days, flip_start = 170, 100
        dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
        out = {}
        for i in range(n_stocks):
            r = 0.002 + 0.001 * i
            price, closes = 100.0, []
            for t in range(n_days):
                drift = r if t < flip_start or (t // 5) % 2 == 0 else -r
                eps = 0.005 * (((i * 3 + t * 7) % 4) - 1.5)
                price *= (1 + drift) * (1 + eps)
                closes.append(price)
            out[f"S{i:03d}"] = pd.DataFrame(
                {
                    "date": dates,
                    "open": closes,
                    "high": [c * 1.01 for c in closes],
                    "low": [c * 0.99 for c in closes],
                    "close": closes,
                    "volume": [1000.0 + 10.0 * i] * n_days,
                    "amount": [0.0] * n_days,
                }
            )
        return out, str(dates[0].date()), str(dates[-1].date())

    def test_ic_flip_between_halves_fails(self):
        bars, start, end = self.make_flip_bars()
        pool = TrajectoryPool()
        loop_mod.run_loop(
            _cfg(mining_start=start, mining_end=end),
            bars,
            ScriptedLLM([_cand("ROC(CLOSE,5)", "动量")]),
            pool,
        )
        t = pool.all()[0]
        assert t.decision == "fail"
        assert "半窗" in t.feedback
        # 池化门（rank_ic_mean/rank_icir）其实是过的 —— 失败 solely 归因半窗门
        assert "rank_ic_mean=" not in t.feedback
        assert "rank_icir=" not in t.feedback


# ---------- final_judgment ----------


class TestFinalJudgment:
    def test_overlapping_windows_rejected(self):
        pool = TrajectoryPool()
        pool.add(_mk_traj("ROC(CLOSE,5)"))
        with pytest.raises(ValueError, match="不重叠"):
            loop_mod.final_judgment(
                pool,
                make_bars(),
                mining=Window("2024-01-01", "2024-06-01"),
                judgment=Window("2024-06-01", "2024-12-01"),
            )

    def test_uses_judgment_window_data(self):
        pool = TrajectoryPool()
        pool.add(_mk_traj("ROC(CLOSE,5)"))
        out = loop_mod.final_judgment(
            pool, make_bars(), mining=MINING, judgment=JUDGMENT, top_n=5, horizon=5
        )
        assert len(out) == 1
        r = out[0]
        assert isinstance(r, DualWindowResult)
        assert r.judgment.start == JUDGMENT.start and r.judgment.end == JUDGMENT.end
        assert r.mining.start == MINING.start and r.mining.end == MINING.end
        assert r.passed  # 漂移宇宙：两窗强正 IC 且同号


# ---------- CLI ----------


class TestCLI:
    @staticmethod
    def _setup(tmp_path):
        bars = make_bars()
        codes_file = tmp_path / "codes.txt"
        codes_file.write_text("\n".join(sorted(bars)), encoding="utf-8")
        return bars, codes_file

    @staticmethod
    def _loader(bars, calls):
        def load(codes, count):
            calls.append(list(codes))
            return {c: df.copy() for c, df in bars.items() if c in set(codes)}

        return load

    @staticmethod
    def _argv(tmp_path, codes_file, *extra):
        return [
            "--direction",
            "动量",
            "--rounds",
            "1",
            "--candidates-per-round",
            "5",
            "--mining-start",
            MINING.start,
            "--mining-end",
            MINING.end,
            "--codes-file",
            str(codes_file),
            "--tag",
            "t1",
            "--out-dir",
            str(tmp_path / "out"),
            *extra,
        ]

    def test_mock_llm_end_to_end(self, tmp_path, capsys):
        bars, codes_file = self._setup(tmp_path)
        calls: list = []
        rc = el.main(
            self._argv(tmp_path, codes_file, "--mock-llm"),
            loader=self._loader(bars, calls),
        )
        assert rc == 0
        tag_dir = tmp_path / "out" / "t1"
        pool_file = tag_dir / "trajectory_pool.json"
        summary_file = tag_dir / "_summary__t1.json"
        assert pool_file.exists() and summary_file.exists()
        summary = json.loads(summary_file.read_text("utf-8"))
        assert summary["pool_size"] == 5 and summary["directions"] == ["动量"]
        assert summary["mock_llm"] is True and summary["total_tokens"] > 0
        assert summary["n_pass"] >= 1 and summary["n_fail"] >= 1
        # 必败对照：CLOSE/CLOSE 恒值 → 截面零方差 → 有效日 0 → fail
        pool = TrajectoryPool.load(pool_file)
        cc = [t for t in pool.all() if t.expression == "CLOSE/CLOSE"]
        assert cc and cc[0].decision == "fail" and cc[0].mining_metrics["n_days"] == 0
        out = capsys.readouterr().out
        assert "[evo]" in out and "进化汇总" in out

    def test_final_judge_reloads_from_disk(self, tmp_path):
        bars, codes_file = self._setup(tmp_path)
        calls: list = []
        rc = el.main(
            self._argv(
                tmp_path,
                codes_file,
                "--mock-llm",
                "--final-judge",
                "--judgment-start",
                JUDGMENT.start,
                "--judgment-end",
                JUDGMENT.end,
            ),
            loader=self._loader(bars, calls),
        )
        assert rc == 0
        assert len(calls) == 2  # 判定窗重新从磁盘加载（物理隔离到加载层）
        summary = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )
        fj = summary["final_judgment"]
        assert fj and fj[0]["judgment"]["start"] == JUDGMENT.start

    def test_empty_pool_guard_no_artifacts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(el, "MockLLM", RaisingLLM)
        bars, codes_file = self._setup(tmp_path)
        rc = el.main(
            self._argv(tmp_path, codes_file, "--mock-llm"),
            loader=self._loader(bars, []),
        )
        assert rc == 2
        assert not (tmp_path / "out" / "t1").exists()  # 空结果不写产物

    def test_no_llm_config_fails_closed(self, tmp_path, monkeypatch):
        for v in (lc.ENV_BASE_URL, lc.ENV_API_KEY, lc.ENV_MODEL):
            monkeypatch.delenv(v, raising=False)
        bars, codes_file = self._setup(tmp_path)
        with pytest.raises(SystemExit) as exc:
            el.main(self._argv(tmp_path, codes_file), loader=self._loader(bars, []))
        assert exc.value.code == 2

    def test_final_judge_requires_judgment_window(self, tmp_path):
        bars, codes_file = self._setup(tmp_path)
        with pytest.raises(SystemExit) as exc:
            el.main(
                self._argv(tmp_path, codes_file, "--mock-llm", "--final-judge"),
                loader=self._loader(bars, []),
            )
        assert exc.value.code == 2

    def test_token_budget_early_stop(self, tmp_path):
        bars, codes_file = self._setup(tmp_path)
        calls: list = []
        rc = el.main(
            self._argv(tmp_path, codes_file, "--mock-llm", "--max-tokens-budget", "2"),
            loader=self._loader(bars, calls),
        )
        assert rc == 0  # 提前收敛仍落盘已有轨迹
        summary = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )
        assert summary["pool_size"] == 1  # 第一个候选后就超预算


# ---------- pre2019 untouched 终审段硬拒绝（反过拟合纪律） ----------


class TestPre2019Guard:
    @staticmethod
    def _parse(*extra):
        ap = el._build_parser()
        return ap, ap.parse_args(list(extra))

    @pytest.mark.parametrize(
        "start,end",
        [
            ("2015-01-01", "2020-12-31"),  # 跨越终审段右缘
            ("2010-01-01", "2016-12-31"),  # 恰好覆盖整段
            ("2009-01-01", "2010-01-01"),  # 端点相接也算碰
            ("2012-06-01", "2013-06-30"),  # 整窗落在段内
        ],
    )
    def test_mining_overlap_rejected(self, capsys, start, end):
        ap, args = self._parse(
            "--direction", "动量", "--mining-start", start, "--mining-end", end
        )
        with pytest.raises(SystemExit) as exc:
            el._validate_args(args, ap)
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert (
            "⛔ 反过拟合纪律" in err and "pre2019 untouched 终审段（2010-2016）" in err
        )

    def test_judgment_overlap_rejected(self, capsys):
        # 挖掘窗干净（2009 年底结束）、判定窗碰段 —— 两个窗各自独立检查
        ap, args = self._parse(
            "--direction",
            "动量",
            "--mining-start",
            "2005-01-01",
            "--mining-end",
            "2009-12-31",
            "--final-judge",
            "--judgment-start",
            "2012-01-01",
            "--judgment-end",
            "2013-12-31",
        )
        with pytest.raises(SystemExit) as exc:
            el._validate_args(args, ap)
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "⛔ 反过拟合纪律" in err and "判定窗" in err

    def test_clean_windows_accepted(self):
        ap, args = self._parse(
            "--direction",
            "动量",
            "--mining-start",
            "2020-01-01",
            "--mining-end",
            "2021-12-31",
            "--final-judge",
            "--judgment-start",
            "2022-01-01",
            "--judgment-end",
            "2022-12-31",
        )
        el._validate_args(args, ap)  # 不抛即通过

    def test_overlap_helper_boundaries(self):
        assert el._overlaps_pre2019("2016-12-31", "2020-01-01")  # 左缘相接
        assert el._overlaps_pre2019("2000-01-01", "2010-01-01")  # 右缘相接
        assert not el._overlaps_pre2019("2017-01-01", "2020-12-31")
        assert not el._overlaps_pre2019("2000-01-01", "2009-12-31")


class TestStalePoolGuard:
    def test_stale_pool_does_not_bypass_empty_guard(self, tmp_path, monkeypatch):
        """上一轮产物在盘上 + 本 run 0 新轨迹 → 仍按空结果护栏拒（旧实现会放行）。"""
        monkeypatch.setattr(el, "MockLLM", RaisingLLM)
        bars, codes_file = TestCLI._setup(tmp_path)
        tag_dir = tmp_path / "out" / "t1"
        tag_dir.mkdir(parents=True)
        stale = TrajectoryPool(tag_dir / "trajectory_pool.json")
        stale.add(_mk_traj("ROC(CLOSE,5)"))
        stale.save()
        rc = el.main(
            TestCLI._argv(tmp_path, codes_file, "--mock-llm"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 2
        # 护栏在 save 之前：旧产物不被本 run 覆盖
        assert len(TrajectoryPool.load(tag_dir / "trajectory_pool.json")) == 1

    def test_stale_pool_grows_normally(self, tmp_path):
        """本 run 有产出 → 旧轨迹保留 + 新轨迹追加，正常落盘。"""
        bars, codes_file = TestCLI._setup(tmp_path)
        tag_dir = tmp_path / "out" / "t1"
        tag_dir.mkdir(parents=True)
        stale = TrajectoryPool(tag_dir / "trajectory_pool.json")
        stale.add(_mk_traj("MA(CLOSE,3)"))  # 与 MockLLM 脚本表达式都不撞
        stale.save()
        rc = el.main(
            TestCLI._argv(tmp_path, codes_file, "--mock-llm"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0
        assert (
            len(TrajectoryPool.load(tag_dir / "trajectory_pool.json")) == 6
        )  # 1 旧 + 5 新


# ---------- __main__ 注册 ----------


def test_main_registry_entry():
    from custos.research import __main__ as rm

    assert "evolution_loop" in rm.TOOLS
    assert rm.ALIASES["evolution"] == "evolution_loop"
    assert (rm.HERE / "evolution_loop.py").exists()


def test_mock_llm_script_rotation():
    llm = MockLLM()
    seen = {
        json.loads(llm.chat([{"role": "user", "content": "x"}]))["expression"]
        for _ in range(5)
    }
    assert "CLOSE/CLOSE" in seen  # 必败对照在轮换内
    assert llm.chat([{"role": "user", "content": "x"}], json_mode=False)


# ---------- --grid-judge：双窗 pass → strategy_grid 三轴终审 ----------


class _FakeGridRun:
    """mock strategy_grid 子进程：按 --scorers 落一份合成 _ranked 报告。"""

    def __init__(self) -> None:
        self.cmds: list[list[str]] = []

    def __call__(self, cmd, **kw):
        import pathlib
        import types

        from custos.research import strategy_grid as sg

        cmd = [str(x) for x in cmd]
        self.cmds.append(cmd)
        out_dir = pathlib.Path(cmd[cmd.index("--out-dir") + 1])
        tag = cmd[cmd.index("--tag") + 1]
        # 括号内逗号不分隔 —— 与 strategy_grid 主路径同一拆分口径
        scorers = sg._split_scorers(cmd[cmd.index("--scorers") + 1])
        results = [
            {
                "scorer": s,
                "gate": "j_low",
                "exit": "pct5",
                "params": {"stop_mode": "pct", "stop_pct": 5},
                "n": 10,
                "win_rate": 0.5,
                "expectancy": 0.01,
                "expectancy_R": 0.2,
                "payoff_ratio": 2.0,
                "margin": 0.1,
                "ret_over_dd": 1.0,
                "reused": False,
                "result_file": f"cell__{i:04d}abcd.json",
                "rank": i + 1,
                "objective": 0.3 - i * 0.1,
            }
            for i, s in enumerate(scorers)
        ]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"_ranked__{tag}.json").write_text(
            json.dumps({"results": results}, ensure_ascii=False), encoding="utf-8"
        )
        return types.SimpleNamespace(returncode=0)


def _grid_argv(tmp_path, codes_file, *extra):
    """带判定窗的 argv（TestCLI._argv 基础上加 --grid-judge 三元组 + --count）。"""
    return TestCLI._argv(
        tmp_path,
        codes_file,
        "--grid-judge",
        "--judgment-start",
        JUDGMENT.start,
        "--judgment-end",
        JUDGMENT.end,
        "--count",
        "800",  # v0.207 起 grid-judge/joint 必须显式 --count（TODO #69）
        *extra,
    )


class TestGridJudge:
    def test_end_to_end_pass_exprs_enter_grid(self, tmp_path, monkeypatch):
        fake = _FakeGridRun()
        monkeypatch.setattr(el.subprocess, "run", fake)
        bars, codes_file = TestCLI._setup(tmp_path)
        rc = el.main(
            _grid_argv(tmp_path, codes_file, "--mock-llm"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0
        assert len(fake.cmds) == 1  # 恰一次 strategy_grid 子进程
        cmd = fake.cmds[0]
        scorers = cmd[cmd.index("--scorers") + 1]
        # ① 只有双窗 pass 的表达式进 argv（漂移宇宙上恰好这两个 pass）
        from custos.research import strategy_grid as sg

        passed = {"expr:ROC(CLOSE,5)", "expr:CLOSE/MA(CLOSE,20)"}
        assert set(sg._split_scorers(scorers)) == passed  # 括号内逗号不分隔
        assert "CLOSE/CLOSE" not in scorers  # 必败对照不进终审
        # ② argv 带判定窗日期 + 宇宙钉死 + --count 透传（TODO #69 缺口回归钉测）
        assert cmd[cmd.index("--start") + 1] == JUDGMENT.start
        assert cmd[cmd.index("--end") + 1] == JUDGMENT.end
        assert "--codes-file" in cmd
        assert cmd[cmd.index("--count") + 1] == "800"
        # ④ summary 的 grid_judge schema
        summary = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )
        gj = summary["grid_judge"]
        assert gj["skipped"] is False and gj["returncode"] == 0
        first = gj["expressions"]["CLOSE/MA(CLOSE,20)"]  # 双窗 ICIR 最高 → 排第一
        assert first["objective"] == pytest.approx(0.3) and first["margin"] == 0.1
        assert first["expectancy_R"] == 0.2 and first["rank"] == 1
        assert first["cell_signature"] == "0000abcd"
        second = gj["expressions"]["ROC(CLOSE,5)"]
        assert second["objective"] == pytest.approx(0.2) and second["rank"] == 2

    def test_grid_judge_implies_final_judge(self, tmp_path, monkeypatch):
        # 只给 --grid-judge（不给 --final-judge）也自动先跑双窗终审
        fake = _FakeGridRun()
        monkeypatch.setattr(el.subprocess, "run", fake)
        bars, codes_file = TestCLI._setup(tmp_path)
        rc = el.main(
            _grid_argv(tmp_path, codes_file, "--mock-llm"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0 and len(fake.cmds) == 1
        summary = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )
        assert summary["final_judgment"]  # 双窗确实跑了（隐含语义）

    def test_zero_pass_skips_grid(self, tmp_path, monkeypatch):
        # 判定窗仅 11 个交易日 → min_days 不达标 → 双窗 0 pass → 零 spawn
        fake = _FakeGridRun()
        monkeypatch.setattr(el.subprocess, "run", fake)
        bars, codes_file = TestCLI._setup(tmp_path)
        argv = TestCLI._argv(
            tmp_path,
            codes_file,
            "--grid-judge",
            "--judgment-start",
            JUDGMENT.start,
            "--judgment-end",
            str(DATES[230].date()),  # 11 个交易日 < min_days(20)
            "--count",
            "800",
            "--mock-llm",
        )
        rc = el.main(argv, loader=TestCLI._loader(bars, []))
        assert rc == 0  # 前面已有真实产物，skip 不是错误
        assert fake.cmds == []
        summary = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )
        gj = summary["grid_judge"]
        assert gj["skipped"] is True and "0 pass" in gj["reason"]

    def test_grid_judge_requires_judgment_window(self, tmp_path):
        bars, codes_file = TestCLI._setup(tmp_path)
        with pytest.raises(SystemExit) as exc:
            el.main(
                TestCLI._argv(tmp_path, codes_file, "--mock-llm", "--grid-judge"),
                loader=TestCLI._loader(bars, []),
            )
        assert exc.value.code == 2

    def test_grid_judge_default_off(self, tmp_path, monkeypatch):
        fake = _FakeGridRun()
        monkeypatch.setattr(el.subprocess, "run", fake)
        bars, codes_file = TestCLI._setup(tmp_path)
        rc = el.main(
            TestCLI._argv(tmp_path, codes_file, "--mock-llm"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0 and fake.cmds == []
        summary = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )
        assert summary["grid_judge"]["skipped"] is True  # schema 常驻，未开则 skipped


# ---------- 联合演化第一档（TODO #68）：joint 基因组循环 ----------


class FakeCellRunner:
    """假三轴单元格执行器：记录调用，按配置返回适应度（None=格子失败）。"""

    def __init__(self, objective=0.5, none=False):
        self.calls: list[dict] = []
        self.objective = objective
        self.none = none

    def __call__(self, expr, gate, exit_params, *, start, end):
        self.calls.append(
            {
                "expr": expr,
                "gate": gate,
                "exit_params": dict(exit_params),
                "start": start,
                "end": end,
            }
        )
        if self.none:
            return None
        return {
            "objective": self.objective,
            "margin": 0.1,
            "expectancy_R": 0.2,
            "cell_signature": "sig123",
        }


def _joint_traj(expr, *, direction="动量", ric=0.05, ricir=0.5, gate=None, params=None):
    """joint 轨迹：gate/exit_params 落基因组分量（未给的分量取 default_params）。"""
    g, p = genome.default_params()
    if gate is not None:
        g = gate
    if params is not None:
        p = params
    t = _mk_traj(expr, direction=direction, ric=ric, ricir=ricir)
    return loop_mod.replace(t, gate=g, exit_params=dict(p))


class TestJointLoop:
    def test_joint_requires_cell_runner(self):
        with pytest.raises(ValueError, match="cell_runner"):
            loop_mod.run_loop(
                _cfg(joint=True), make_bars(), ScriptedLLM([]), TrajectoryPool()
            )

    def test_ic_fail_zero_cell_calls(self):
        # ① IC 门 fail（恒值表达式 n_days=0）→ 零 cell 调用（控成本）
        runner = FakeCellRunner()
        pool = TrajectoryPool()
        loop_mod.run_loop(
            _cfg(joint=True),
            make_bars(),
            ScriptedLLM([_cand("CLOSE/CLOSE", "恒值")]),
            pool,
            cell_runner=runner,
        )
        assert runner.calls == []
        assert pool.all()[0].decision == "fail"
        assert "objective" not in pool.all()[0].mining_metrics

    def test_cell_none_fails(self):
        # ②a IC 过门但格子失败（cell None）→ fail，reasons 记录第二层
        pool = TrajectoryPool()
        loop_mod.run_loop(
            _cfg(joint=True),
            make_bars(),
            ScriptedLLM([_cand("ROC(CLOSE,5)", "动量")]),
            pool,
            cell_runner=FakeCellRunner(none=True),
        )
        t = pool.all()[0]
        assert t.decision == "fail" and "cell_runner 返回 None" in t.feedback

    def test_objective_below_threshold_fails(self):
        # ②b objective 低于阈值 → fail；metrics 仍如实带 objective 读数
        pool = TrajectoryPool()
        loop_mod.run_loop(
            _cfg(joint=True, min_objective=0.3),
            make_bars(),
            ScriptedLLM([_cand("ROC(CLOSE,5)", "动量")]),
            pool,
            cell_runner=FakeCellRunner(objective=0.1),
        )
        t = pool.all()[0]
        assert t.decision == "fail"
        assert t.mining_metrics["objective"] == 0.1

    def test_pass_lands_genome_fields(self):
        # ②c 全过 → pass：trajectory 落 gate/exit_params + mining_metrics 新键
        runner = FakeCellRunner(objective=0.5)
        pool = TrajectoryPool()
        loop_mod.run_loop(
            _cfg(joint=True, min_objective=0.3),
            make_bars(),
            ScriptedLLM([_cand("ROC(CLOSE,5)", "动量")]),
            pool,
            cell_runner=runner,
        )
        t = pool.all()[0]
        assert t.decision == "pass"
        gate, params = genome.default_params()  # origin → 默认基因组分量
        assert t.gate == gate and t.exit_params == params
        mm = t.mining_metrics
        assert mm["objective"] == 0.5 and mm["margin"] == 0.1
        assert mm["expectancy_R"] == 0.2 and mm["cell_signature"] == "sig123"
        assert isinstance(mm["rank_ic_mean"], float)  # 主键仍在（轨迹校验不破）
        # cell 调用窗口 = 挖掘窗（mining_end 硬隔离线）
        assert runner.calls[0]["start"] == MINING.start
        assert runner.calls[0]["end"] == MINING.end

    def _ctx(self, cfg, seed):
        return loop_mod._RunCtx(
            cfg=cfg,
            mining_bars={},
            llm=ScriptedLLM([]),
            pool=TrajectoryPool(),
            rng=random.Random(seed),
            on_event=None,
            cell_runner=FakeCellRunner(),
        )

    def test_mutation_params_step_along_lattice(self):
        # ④ mutation 父代参数沿格点步进（种子钉死，与 genome 层逐位一致）
        cfg = _cfg(joint=True)
        parent = _joint_traj("ROC(CLOSE,5)")
        got = loop_mod._assemble_params("mutation", [parent], self._ctx(cfg, 42))
        want = genome.mutate_params(
            parent.gate, dict(parent.exit_params), random.Random(42)
        )
        assert got == want

    def test_crossover_params_swap_components(self):
        # ④ crossover 分量交换（种子钉死）
        cfg = _cfg(joint=True)
        pa = _joint_traj("ROC(CLOSE,5)", gate=genome.GATE_CHOICES[0])
        pb = _joint_traj("DELTA(CLOSE,3)", gate=genome.GATE_CHOICES[-1])
        got = loop_mod._assemble_params("crossover", [pa, pb], self._ctx(cfg, 7))
        want = genome.crossover_params(
            (pa.gate, dict(pa.exit_params)),
            (pb.gate, dict(pb.exit_params)),
            random.Random(7),
        )
        assert got == want

    def test_legacy_parent_falls_back_to_default(self):
        # ⑤ 老轨迹（无参数字段）作父代 → 先落 default 再变异
        cfg = _cfg(joint=True)
        legacy = _mk_traj("ROC(CLOSE,5)", direction="动量")  # gate="" exit_params={}
        got = loop_mod._assemble_params("mutation", [legacy], self._ctx(cfg, 42))
        want = genome.mutate_params(*genome.default_params(), random.Random(42))
        assert got == want

    def test_event_carries_gate_and_objective(self):
        runner = FakeCellRunner(objective=0.5)
        events: list[dict] = []
        loop_mod.run_loop(
            _cfg(joint=True),
            make_bars(),
            ScriptedLLM([_cand("ROC(CLOSE,5)", "动量")]),
            TrajectoryPool(),
            on_event=events.append,
            cell_runner=runner,
        )
        assert events[0]["gate"] == genome.GATE_CHOICES[0]
        assert events[0]["objective"] == 0.5


class TestJointFinalJudgment:
    def test_judgment_window_passed_to_cell_runner(self):
        # joint 轨迹重跑判定窗单元格；老轨迹（gate=""）不重跑
        runner = FakeCellRunner()
        pool = TrajectoryPool()
        pool.add(_joint_traj("ROC(CLOSE,5)", ricir=0.9))
        pool.add(_mk_traj("DELTA(CLOSE,3)", direction="动量", ricir=0.4))  # 老轨迹
        out = loop_mod.final_judgment(
            pool,
            make_bars(),
            mining=MINING,
            judgment=JUDGMENT,
            top_n=5,
            horizon=5,
            cell_runner=runner,
        )
        assert len(out) == 2 and all(isinstance(v, loop_mod.JointVerdict) for v in out)
        assert len(runner.calls) == 1  # 只有 gate 非空的轨迹重跑
        assert runner.calls[0]["start"] == JUDGMENT.start
        assert runner.calls[0]["end"] == JUDGMENT.end
        joint_row = [v for v in out if v.judgment_cell is not None][0]
        assert joint_row.judgment_cell["cell_signature"] == "sig123"
        legacy_row = [v for v in out if v.judgment_cell is None][0]
        assert legacy_row.dual.expression == "DELTA(CLOSE,3)"

    def test_without_cell_runner_returns_dual_results(self):
        # 既有行为不变：不给 cell_runner → 返回纯 DualWindowResult 列表
        pool = TrajectoryPool()
        pool.add(_mk_traj("ROC(CLOSE,5)"))
        out = loop_mod.final_judgment(
            pool, make_bars(), mining=MINING, judgment=JUDGMENT
        )
        assert isinstance(out[0], DualWindowResult)


class TestJointCLI:
    def test_joint_smoke(self, tmp_path, monkeypatch):
        runner = FakeCellRunner(objective=0.5)
        monkeypatch.setattr(el, "_make_cell_runner", lambda *a, **k: runner)
        bars, codes_file = TestCLI._setup(tmp_path)
        rc = el.main(
            TestCLI._argv(
                tmp_path, codes_file, "--mock-llm", "--joint", "--count", "800"
            ),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0
        summary = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )
        assert summary["config"]["joint"] is True
        assert runner.calls  # IC 过门的候选确实跑了三轴单元格
        best = summary["best"][0]
        assert best["gate"] and best["exit_params"]
        assert best["objective"] == 0.5
        pool = TrajectoryPool.load(tmp_path / "out" / "t1" / "trajectory_pool.json")
        passes = [t for t in pool.all() if t.decision == "pass"]
        assert passes and all(t.gate for t in passes)

    def test_joint_final_judge_verdict_schema(self, tmp_path, monkeypatch):
        runner = FakeCellRunner(objective=0.5)
        monkeypatch.setattr(el, "_make_cell_runner", lambda *a, **k: runner)
        bars, codes_file = TestCLI._setup(tmp_path)
        rc = el.main(
            _grid_argv(tmp_path, codes_file, "--mock-llm", "--joint"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0
        summary = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )
        fj = summary["final_judgment"][0]
        assert "dual" in fj and "judgment_cell" in fj  # JointVerdict 包装
        # 判定窗单元格也跑过（挖掘窗 + 判定窗两类窗口都在调用记录里）
        windows = {(c["start"], c["end"]) for c in runner.calls}
        assert (JUDGMENT.start, JUDGMENT.end) in windows


# ---------- TODO #69：--count 显式化（v0.207，R32 缺口） ----------


class TestCountRequired:
    """--joint/--grid-judge 必须显式 --count：单元格子进程不继承 loader 默认深度，
    缺省 500 只回溯约两年（R32 三轴终审 3 格全灭的根因）。"""

    def test_grid_judge_without_count_fails_closed(self, tmp_path, monkeypatch):
        fake = _FakeGridRun()
        monkeypatch.setattr(el.subprocess, "run", fake)
        bars, codes_file = TestCLI._setup(tmp_path)
        with pytest.raises(SystemExit) as exc:
            el.main(
                TestCLI._argv(
                    tmp_path,
                    codes_file,
                    "--mock-llm",
                    "--grid-judge",
                    "--judgment-start",
                    JUDGMENT.start,
                    "--judgment-end",
                    JUDGMENT.end,
                ),
                loader=TestCLI._loader(bars, []),
            )
        assert exc.value.code == 2
        assert fake.cmds == []  # 零 spawn

    def test_joint_without_count_fails_closed(self, tmp_path, monkeypatch):
        runner = FakeCellRunner(objective=0.5)
        monkeypatch.setattr(el, "_make_cell_runner", lambda *a, **k: runner)
        bars, codes_file = TestCLI._setup(tmp_path)
        with pytest.raises(SystemExit) as exc:
            el.main(
                TestCLI._argv(tmp_path, codes_file, "--mock-llm", "--joint"),
                loader=TestCLI._loader(bars, []),
            )
        assert exc.value.code == 2
        assert runner.calls == []


# ---------- TODO #70：单元格容量约束（--cell-top-n）与退化明示 ----------


class TestCellTopN:
    def test_namespace_carries_cell_top_n(self, tmp_path, monkeypatch):
        # top_n 进 Namespace：monkeypatch strategy_grid.run_cell 捕获 ns
        from custos.research import strategy_grid as sg

        captured = {}

        def fake_run_cell(ns, cell, out_dir, capture=False):
            captured["ns"] = ns
            captured["cell"] = cell
            return "failed", None, ""  # 失败即可，只看 Namespace

        monkeypatch.setattr(sg, "run_cell", fake_run_cell)
        args = el._build_parser().parse_args(
            [
                "--mining-start",
                "2024-01-01",
                "--mining-end",
                "2024-06-01",
                "--cell-top-n",
                "7",
            ]
        )
        runner = el._make_cell_runner(args, ["S000"], tmp_path)
        out = runner("ROC(CLOSE,5)", "j_low", {}, start="2024-01-01", end="2024-06-01")
        assert out is None  # 格子失败 → None（顺带钉住失败口径）
        assert captured["ns"].top_n == 7
        assert captured["cell"]["scorer"] == "expr:ROC(CLOSE,5)"

    def test_grid_command_carries_top_n(self, tmp_path):
        args = el._build_parser().parse_args(
            [
                "--mining-start",
                "2024-01-01",
                "--mining-end",
                "2024-06-01",
                "--judgment-start",
                "2024-08-01",
                "--judgment-end",
                "2024-10-01",
            ]
        )
        cmd = el._grid_command(args, tmp_path, ["ROC(CLOSE,5)"], ["S000"], "t__grid")
        assert cmd[cmd.index("--top-n") + 1] == "20"  # 默认 20（选择压力）

    def test_default_not_degenerate(self, tmp_path):
        bars, codes_file = TestCLI._setup(tmp_path)
        rc = el.main(
            TestCLI._argv(tmp_path, codes_file, "--mock-llm"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0
        cfg = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )["config"]
        assert cfg["cell_top_n"] == 20
        assert cfg["factor_axis_degenerate"] is False
        assert "factor_axis_note" not in cfg

    def test_zero_top_n_marked_degenerate(self, tmp_path, capsys, monkeypatch):
        # --cell-top-n 0 → 启动横幅（grid-judge 路径）+ summary config 标 true
        fake = _FakeGridRun()
        monkeypatch.setattr(el.subprocess, "run", fake)
        bars, codes_file = TestCLI._setup(tmp_path)
        rc = el.main(
            _grid_argv(tmp_path, codes_file, "--mock-llm", "--cell-top-n", "0"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "因子轴退化" in out and "factor_axis_degenerate" in out
        cfg = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )["config"]
        assert cfg["factor_axis_degenerate"] is True
        assert "gate×出场" in cfg["factor_axis_note"]
        # 退化口径同样透传进 argv（top_n=0 显式可见，不藏默认）
        assert fake.cmds[0][fake.cmds[0].index("--top-n") + 1] == "0"

    def test_positive_top_n_banner_info(self, tmp_path, capsys, monkeypatch):
        fake = _FakeGridRun()
        monkeypatch.setattr(el.subprocess, "run", fake)
        bars, codes_file = TestCLI._setup(tmp_path)
        rc = el.main(
            _grid_argv(tmp_path, codes_file, "--mock-llm"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0
        assert "选择压力" in capsys.readouterr().out


# ---------- TODO #72：规划层（--plan N） ----------


class TestPlanningCLI:
    def test_plan_expands_directions(self, tmp_path, capsys):
        bars, codes_file = TestCLI._setup(tmp_path)
        rc = el.main(
            TestCLI._argv(tmp_path, codes_file, "--mock-llm", "--plan", "2"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "[plan]" in out and "来源 llm,llm" in out  # planning 事件发出
        summary = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )
        plan = summary["config"]["plan"]
        assert plan["n"] == 2 and plan["seeds"] == ["动量"]
        assert len(plan["expanded"][0]["directions"]) == 2
        assert plan["expanded"][0]["source"] == ["llm", "llm"]
        # 方向数翻倍进 cfg.directions（MockLLM 规划应答 3 条固定方向，截断到 2）
        assert (
            list(summary["config"]["directions"]) == plan["expanded"][0]["directions"]
        )
        assert summary["pool_size"] == 2 * 1 * 5  # 方向 × rounds × candidates

    def test_plan_zero_bitwise_unchanged(self, tmp_path, capsys):
        bars, codes_file = TestCLI._setup(tmp_path)
        rc = el.main(
            TestCLI._argv(tmp_path, codes_file, "--mock-llm", "--plan", "0"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "[plan]" not in out  # 关闭时不发规划事件
        summary = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )
        assert summary["config"]["plan"] == {
            "n": 0,
            "seeds": ["动量"],
            "expanded": [],
        }
        assert list(summary["config"]["directions"]) == ["动量"]  # 原样
        assert summary["pool_size"] == 5  # 与不开 --plan 逐位一致

    def test_plan_fallback_via_mock_failure(self, tmp_path, monkeypatch, capsys):
        # LLM 规划失败 → 确定性模板兜底（来源 fallback），流程不炸
        from custos.research.evolution import planning

        class PlanningFailLLM(MockLLM):
            def chat(self, messages, *, json_mode=True, json_schema=None):
                last = str(messages[-1].get("content", "")) if messages else ""
                if '"directions"' in last:
                    raise LLMError("planning down")
                return super().chat(
                    messages, json_mode=json_mode, json_schema=json_schema
                )

        monkeypatch.setattr(el, "MockLLM", PlanningFailLLM)
        bars, codes_file = TestCLI._setup(tmp_path)
        rc = el.main(
            TestCLI._argv(tmp_path, codes_file, "--mock-llm", "--plan", "2"),
            loader=TestCLI._loader(bars, []),
        )
        assert rc == 0  # fallback 是正常路径
        out = capsys.readouterr().out
        assert "来源 fallback,fallback" in out
        summary = json.loads(
            (tmp_path / "out" / "t1" / "_summary__t1.json").read_text("utf-8")
        )
        exp = summary["config"]["plan"]["expanded"][0]
        assert exp["source"] == ["fallback", "fallback"]
        assert exp["directions"][0] == "动量 + 量能确认"  # 首个模板
        assert planning.MAX_ATTEMPTS == 3

    def test_plan_negative_rejected(self, tmp_path):
        bars, codes_file = TestCLI._setup(tmp_path)
        with pytest.raises(SystemExit) as exc:
            el.main(
                TestCLI._argv(tmp_path, codes_file, "--mock-llm", "--plan", "-1"),
                loader=TestCLI._loader(bars, []),
            )
        assert exc.value.code == 2
