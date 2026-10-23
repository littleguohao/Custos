# -*- coding: utf-8 -*-
"""提案中继（--agent-relay）钉测：无网络、无通达信，全合成数据。

覆盖：
  ① RelayLLM：未应答调用 raise RelayPause（请求形状钉死）；transcript 重放
     逐位返回；哈希不符 → RelayError（**不是** LLMError，不被候选级兜底吞掉）；
  ② rng 状态 JSON 往返：setstate 后随机序列逐位一致；
  ③ 断点 / 应答文件协议：round-trip、缺字段、坏位标、应答 request_id 不符、
     空应答、归档防二次消费；
  ④ run_loop 断点续跑**等价性**（核心钉）：同一串 LLM 应答下，「暂停/续跑
     分段跑」与「单进程一次跑」的轨迹池逐位一致（含 DSL 回注重试跨暂停）；
  ⑤ CLI 端到端：--agent-relay 首跑退出码 3 + 落请求/断点；缺应答 / 错号
     应答 → exit 2；逐轮应答续跑到 rc=0，断点清除、summary llm_source 标注；
     --agent-relay 与 --mock-llm / --plan 互斥。
"""

from __future__ import annotations

import json
import random

import pytest

from custos.research import evolution_loop as el
from custos.research.evolution import loop as loop_mod
from custos.research.evolution import relay
from custos.research.evolution.relay import (
    RelayError,
    RelayLLM,
    RelayPause,
    ResumeState,
)

from test_evolution_loop import MINING, ScriptedLLM, _cand, _cfg, make_bars  # noqa: E402

MSGS = [{"role": "user", "content": "给个因子"}]


def _fixed_now(monkeypatch):
    """钉死轨迹 created_at（make_id 含它——两次运行逐位对比的前提）。"""
    monkeypatch.setattr(loop_mod, "_now_iso", lambda: "2026-10-23T00:00:00+00:00")


# ---------- RelayLLM 单元 ----------


class TestRelayLLM:
    def test_first_call_pauses_with_request_shape(self):
        llm = RelayLLM()
        with pytest.raises(RelayPause) as ei:
            llm.chat(MSGS, json_mode=True, json_schema={"type": "object"})
        req = ei.value.request
        assert req["schema_version"] == relay.SCHEMA_VERSION
        assert req["messages"] == MSGS and req["json_mode"] is True
        assert req["json_schema"] == {"type": "object"}
        assert req["request_id"].startswith("00000_")
        assert req["request_hash"] == relay.request_hash(MSGS, True, {"type": "object"})
        assert llm.total_tokens == 0

    def test_replay_returns_recorded_response(self):
        entry = {
            "request_hash": relay.request_hash(MSGS, True, None),
            "messages": MSGS,
            "json_mode": True,
            "json_schema": None,
            "response": '{"expression": "ROC(CLOSE,5)"}',
        }
        llm = RelayLLM([entry])
        assert llm.chat(MSGS) == entry["response"]

    def test_replay_hash_mismatch_raises_relay_error(self):
        entry = {"request_hash": "0" * 64, "response": "x"}
        llm = RelayLLM([entry])
        with pytest.raises(RelayError):
            llm.chat(MSGS)

    def test_transcript_then_pause_at_next(self):
        entry = {
            "request_hash": relay.request_hash(MSGS, True, None),
            "response": "ok",
        }
        llm = RelayLLM([entry])
        assert llm.chat(MSGS) == "ok"
        with pytest.raises(RelayPause) as ei:
            llm.chat(MSGS)  # 第 2 次调用无记录 → 暂停
        assert ei.value.request["request_id"].startswith("00001_")

    def test_load_transcript_after_serve_refused(self):
        llm = RelayLLM()
        with pytest.raises(RelayPause):
            llm.chat(MSGS)
        with pytest.raises(RelayError):
            llm.load_transcript([])


# ---------- rng / 指纹 / 断点文件 ----------


class TestStateRoundTrip:
    def test_rng_json_round_trip_identical_sequence(self):
        rng = random.Random(42)
        rng.random()  # 消耗若干，状态非初始
        state = relay.rng_to_json(rng.getstate())
        restored = random.Random(0)
        restored.setstate(relay.rng_from_json(json.loads(json.dumps(state))))
        assert [rng.random() for _ in range(5)] == [restored.random() for _ in range(5)]

    def test_cfg_fingerprint_stable_and_sensitive(self):
        a = relay.cfg_fingerprint({"rounds": 3, "directions": ["动量"]})
        assert a == relay.cfg_fingerprint({"directions": ["动量"], "rounds": 3})
        assert a != relay.cfg_fingerprint({"rounds": 4, "directions": ["动量"]})

    def test_checkpoint_round_trip(self, tmp_path):
        ck = tmp_path / "relay_checkpoint.json"
        relay.save_checkpoint(
            ck,
            tag="t1",
            cfg_fp="f" * 64,
            position=(1, 2, 0),
            rng_state=[3, [1, 2], None],
            transcript=[{"request_hash": "h", "response": "r"}],
            pending={"request_id": "00000_x"},
        )
        got = relay.load_checkpoint(ck)
        assert got["position"] == [1, 2, 0] and got["tag"] == "t1"
        assert got["transcript"][0]["response"] == "r"

    def test_checkpoint_missing_returns_none(self, tmp_path):
        assert relay.load_checkpoint(tmp_path / "nope.json") is None

    def test_checkpoint_corrupt_and_bad_shape(self, tmp_path):
        bad = tmp_path / "c.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(RelayError):
            relay.load_checkpoint(bad)
        bad.write_text(json.dumps({"schema_version": 1, "tag": "t"}), "utf-8")
        with pytest.raises(RelayError):  # 缺字段
            relay.load_checkpoint(bad)


class TestResponseProtocol:
    def _pending(self):
        return {"request_id": "00000_ab", "request_hash": "h"}

    def test_missing_response_refused(self, tmp_path):
        with pytest.raises(RelayError, match="缺应答文件"):
            relay.read_response(tmp_path, self._pending())

    def test_wrong_request_id_refused(self, tmp_path):
        (tmp_path / "response.json").write_text(
            json.dumps({"request_id": "99999_xx", "content": "x"}), encoding="utf-8"
        )
        with pytest.raises(RelayError, match="request_id 不符"):
            relay.read_response(tmp_path, self._pending())

    def test_empty_content_refused(self, tmp_path):
        (tmp_path / "response.json").write_text(
            json.dumps({"request_id": "00000_ab", "content": "  "}), encoding="utf-8"
        )
        with pytest.raises(RelayError, match="content 为空"):
            relay.read_response(tmp_path, self._pending())

    def test_ok_archives_and_removes(self, tmp_path):
        (tmp_path / "response.json").write_text(
            json.dumps({"request_id": "00000_ab", "content": "提案"}), encoding="utf-8"
        )
        assert relay.read_response(tmp_path, self._pending()) == "提案"
        assert not (tmp_path / "response.json").exists()  # 已消费
        archived = tmp_path / "answered" / "00000_ab.json"
        assert json.loads(archived.read_text("utf-8"))["content"] == "提案"


# ---------- run_loop 断点续跑等价性 ----------


def _run_relay_segmented(queue, monkeypatch, cfg=None):
    """模拟 agent 中继：每暂停一次，从 queue 取一条应答写进 transcript 续跑。

    返回 (pool, events, pause_count)。等价性钉：与 ScriptedLLM(queue) 单进程
    一次跑的轨迹池逐位一致。
    """
    _fixed_now(monkeypatch)
    pool = loop_mod.TrajectoryPool()
    events: list[dict] = []
    seg: list[dict] = []  # 当前候选段的已应答 transcript（跨候选不保留）
    last_pos: tuple | None = None
    resume: ResumeState | None = None
    pauses = 0
    answers = list(queue)
    for _ in range(50):  # 熔断：协议 bug 导致死循环时测试失败而非挂死
        llm = RelayLLM(seg)
        try:
            loop_mod.run_loop(
                cfg or _cfg(rounds=3, candidates_per_round=2),
                make_bars(),
                llm,
                pool,
                on_event=events.append,
                resume=resume,
            )
            return pool, events, pauses
        except RelayPause as pause:
            pauses += 1
            assert pause.position is not None and pause.rng_state is not None
            if last_pos is not None and pause.position != last_pos:
                seg = []  # 上一候选已闭环：其段记录归档（CLI 侧在 answered/）
            req = pause.request
            content = (
                "（假解读）读数已如实记录。"
                if not req["json_mode"]
                else json.dumps(answers.pop(0), ensure_ascii=False)
            )
            seg.append({**req, "response": content})
            last_pos = pause.position
            resume = ResumeState(position=pause.position, rng_state=pause.rng_state)
    raise AssertionError("中继 50 次仍未闭环——协议死循环")


class TestRelayResumeEquivalence:
    QUEUE = [
        _cand("ROC(CLOSE,5)", "动量5日"),  # r0c0 origin → pass
        _cand("DELTA(CLOSE,3)", "价差3日"),  # r0c1 origin → pass
        _cand("EMA(CLOSE,5)", "h2"),  # r1c0 mutation → 非白名单 → 回注重试
        _cand("ROC(CLOSE,10)", "动量10日"),  # r1c0 重试 → pass
        _cand(
            "MA(CLOSE,1)+MA(CLOSE,2)+MA(CLOSE,3)+MA(CLOSE,4)"
            "+MA(CLOSE,5)+MA(CLOSE,6)+MA(CLOSE,7)",
            "超复杂度",
        ),  # r1c1 → 复杂度 fail（无 interpret）
        _cand("ROC(CLOSE,20)", "动量20日"),  # r2c0 crossover → pass
        _cand("TS_RANK(ROC(CLOSE,5),10)", "动量分位"),  # r2c1 crossover
    ]

    def test_segmented_run_matches_single_process(self, monkeypatch):
        # 基准：ScriptedLLM 单进程一次跑
        _fixed_now(monkeypatch)
        pool_a = loop_mod.TrajectoryPool()
        events_a: list[dict] = []
        loop_mod.run_loop(
            _cfg(rounds=3, candidates_per_round=2),
            make_bars(),
            ScriptedLLM(list(self.QUEUE)),
            pool_a,
            on_event=events_a.append,
        )
        # 中继：同一串应答，暂停/续跑分段跑
        pool_b, events_b, pauses = _run_relay_segmented(self.QUEUE, monkeypatch)
        assert pauses >= 6  # 每候选至少 1 次提案暂停 + pass 候选的 interpret
        assert [t.to_dict() for t in pool_b.all()] == [
            t.to_dict() for t in pool_a.all()
        ]
        assert events_b == events_a

    def test_resume_out_of_range_position_refused(self, monkeypatch):
        _fixed_now(monkeypatch)
        pool = loop_mod.TrajectoryPool()
        rng = random.Random(0)
        with pytest.raises(ValueError, match="位标越界"):
            loop_mod.run_loop(
                _cfg(rounds=1, candidates_per_round=1),
                make_bars(),
                RelayLLM(),
                pool,
                resume=ResumeState(
                    position=(9, 9, 9), rng_state=relay.rng_to_json(rng.getstate())
                ),
            )


# ---------- CLI 端到端 ----------


class TestRelayCLI:
    @staticmethod
    def _setup(tmp_path):
        bars = make_bars()
        codes_file = tmp_path / "codes.txt"
        codes_file.write_text("\n".join(sorted(bars)), encoding="utf-8")
        return bars, codes_file

    @staticmethod
    def _loader(bars):
        def load(codes, count):
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
            "2",
            "--mining-start",
            MINING.start,
            "--mining-end",
            MINING.end,
            "--codes-file",
            str(codes_file),
            "--tag",
            "relay_t",
            "--out-dir",
            str(tmp_path / "out"),
            "--agent-relay",
            *extra,
        ]

    @staticmethod
    def _read_json_retry(path, attempts=50, delay=0.01):
        """读中继文件带毫秒级重试：Windows 下脚本侧 tmp+replace 原子写的瞬间，
        读者撞上 PermissionError（CI windows 偶发）——重试跨过替换窗口。"""
        import time

        for _ in range(attempts):
            try:
                return json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                time.sleep(delay)
        return json.loads(path.read_text("utf-8"))  # 最后一击：原样抛异常

    @staticmethod
    def _answer_pending(out_dir, content_iter):
        """读待答请求 → 写应答（模拟 agent 一轮；应答原子写，对齐协议建议）。"""
        rdir = out_dir / "relay"
        req = TestRelayCLI._read_json_retry(rdir / "pending_request.json")
        content = (
            "（假解读）中继模式读数已记录。"
            if not req["json_mode"]
            else json.dumps(next(content_iter), ensure_ascii=False)
        )
        tmp = rdir / "response.json.tmp"
        tmp.write_text(
            json.dumps(
                {"request_id": req["request_id"], "content": content},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(rdir / "response.json")  # 原子写：读者（waiter）不会读到半截
        return req["request_id"]

    def test_pause_resume_cycle_to_completion(self, tmp_path, capsys):
        bars, codes_file = self._setup(tmp_path)
        out_dir = tmp_path / "out" / "relay_t"
        argv = self._argv(tmp_path, codes_file)
        cands = iter(
            [_cand("ROC(CLOSE,5)", "动量5日"), _cand("DELTA(CLOSE,3)", "价差3日")]
        )
        pauses = 0
        for _ in range(20):
            rc = el.main(argv, loader=self._loader(bars))
            if rc == relay.EXIT_PAUSE:
                pauses += 1
                assert relay.load_checkpoint(relay.checkpoint_path(out_dir)) is not None
                self._answer_pending(out_dir, cands)
                continue
            assert rc == 0, f"第 {pauses} 次暂停后异常退出 rc={rc}"
            break
        else:
            raise AssertionError("20 轮仍未闭环")
        assert pauses == 4  # 2 候选 ×（提案 + interpret）
        # 收尾后断点与待答请求已清
        assert relay.load_checkpoint(relay.checkpoint_path(out_dir)) is None
        assert not (out_dir / "relay" / "pending_request.json").exists()
        pool = loop_mod.TrajectoryPool.load(out_dir / "trajectory_pool.json")
        assert len(pool.all()) == 2
        assert all(t.decision == "pass" for t in pool.all())
        summary = json.loads((out_dir / "_summary__relay_t.json").read_text("utf-8"))
        assert summary["config"]["llm_source"] == "agent_relay"
        assert summary["total_tokens"] == 0  # 中继模式无 token 计数
        out = capsys.readouterr().out
        assert '"relay": "pending"' in out  # 机器可读协议行

    def test_pause_without_response_refused(self, tmp_path, capsys):
        bars, codes_file = self._setup(tmp_path)
        argv = self._argv(tmp_path, codes_file)
        assert el.main(argv, loader=self._loader(bars)) == relay.EXIT_PAUSE
        rc = el.main(argv, loader=self._loader(bars))  # 无应答直接续跑
        assert rc == 2
        assert "缺应答文件" in capsys.readouterr().err

    def test_wrong_response_id_refused(self, tmp_path, capsys):
        bars, codes_file = self._setup(tmp_path)
        out_dir = tmp_path / "out" / "relay_t"
        argv = self._argv(tmp_path, codes_file)
        assert el.main(argv, loader=self._loader(bars)) == relay.EXIT_PAUSE
        rdir = out_dir / "relay"
        (rdir / "response.json").write_text(
            json.dumps({"request_id": "99999_wrong", "content": "x"}),
            encoding="utf-8",
        )
        assert el.main(argv, loader=self._loader(bars)) == 2
        assert "request_id 不符" in capsys.readouterr().err

    def test_config_drift_refused(self, tmp_path, capsys):
        bars, codes_file = self._setup(tmp_path)
        argv = self._argv(tmp_path, codes_file)
        assert el.main(argv, loader=self._loader(bars)) == relay.EXIT_PAUSE
        drifted = [a if a != "1" else "2" for a in argv]  # --rounds 1 → 2
        assert el.main(drifted, loader=self._loader(bars)) == 2
        assert "配置指纹不符" in capsys.readouterr().err

    def test_mutual_exclusion(self, tmp_path):
        bars, codes_file = self._setup(tmp_path)
        argv = self._argv(tmp_path, codes_file, "--mock-llm")
        with pytest.raises(SystemExit):
            el.main(argv, loader=self._loader(bars))
        argv = self._argv(tmp_path, codes_file, "--plan", "2")
        with pytest.raises(SystemExit):
            el.main(argv, loader=self._loader(bars))


# ---------- v0.449：崩溃窗口自愈 / 错误累计 / 常驻模式 ----------


class TestArchivedFallback:
    def test_reuse_archived_when_response_consumed(self, tmp_path):
        pend = {"request_id": "00000_ab", "request_hash": "h"}
        (tmp_path / "response.json").write_text(
            json.dumps({"request_id": "00000_ab", "content": "提案"}), encoding="utf-8"
        )
        assert relay.read_response(tmp_path, pend) == "提案"  # 消费 + 归档
        # 模拟：续跑进程消费后、落新断点前崩溃 ⇒ 断点 pending 仍是它
        assert relay.read_response(tmp_path, pend) == "提案"

    def test_corrupt_archive_refused(self, tmp_path):
        (tmp_path / "answered").mkdir()
        (tmp_path / "answered" / "00000_ab.json").write_text("{bad", encoding="utf-8")
        with pytest.raises(RelayError):
            relay.read_response(tmp_path, {"request_id": "00000_ab"})


class TestRelayV449(TestRelayCLI):
    def _single_mode_pool(self, tmp_path, bars, codes_file, extra=()):
        """退出模式跑完（对照）。"""
        out_dir = tmp_path / "out" / "relay_t"
        argv = self._argv(tmp_path, codes_file, *extra)
        cands = iter(
            [_cand("ROC(CLOSE,5)", "动量5日"), _cand("DELTA(CLOSE,3)", "价差3日")]
        )
        for _ in range(20):
            rc = el.main(argv, loader=self._loader(bars))
            if rc != relay.EXIT_PAUSE:
                break
            self._answer_pending(out_dir, cands)
        assert rc == 0
        return loop_mod.TrajectoryPool.load(out_dir / "trajectory_pool.json")

    def test_wait_mode_single_process_matches_exit_mode(self, tmp_path):
        import threading
        import time

        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        bars, codes_file = self._setup(tmp_path / "a")
        ref = self._single_mode_pool(tmp_path / "a", bars, codes_file)

        root = tmp_path / "b"
        bars, codes_file = self._setup(root)
        out_dir = root / "out" / "relay_t"
        loads = []
        base = self._loader(bars)

        def counting_loader(codes, count):
            loads.append(1)
            return base(codes, count)

        cands = iter(
            [_cand("ROC(CLOSE,5)", "动量5日"), _cand("DELTA(CLOSE,3)", "价差3日")]
        )
        stop = threading.Event()
        answered = []

        def agent():
            rdir = out_dir / "relay"
            seen = set()
            while not stop.is_set():
                p = rdir / "pending_request.json"
                if p.exists() and not (rdir / "response.json").exists():
                    rid = self._read_json_retry(p)["request_id"]
                    if rid not in seen:
                        seen.add(rid)
                        answered.append(self._answer_pending(out_dir, cands))
                time.sleep(0.01)

        th = threading.Thread(target=agent, daemon=True)
        th.start()
        try:
            argv = self._argv(
                root, codes_file, "--relay-wait", "30", "--relay-poll", "0.02"
            )
            assert el.main(argv, loader=counting_loader) == 0
        finally:
            stop.set()
            th.join(2)
        assert len(answered) == 4 and len(loads) == 1, (
            "4 次往返、K 线只加载一次（进程不退出）"
        )
        got = loop_mod.TrajectoryPool.load(out_dir / "trajectory_pool.json")
        assert [(t.expression, t.decision) for t in got.all()] == [
            (t.expression, t.decision) for t in ref.all()
        ]
        assert relay.load_checkpoint(relay.checkpoint_path(out_dir)) is None

    def test_wait_timeout_falls_back_and_resumes(self, tmp_path):
        bars, codes_file = self._setup(tmp_path)
        out_dir = tmp_path / "out" / "relay_t"
        argv = self._argv(
            tmp_path, codes_file, "--relay-wait", "0.05", "--relay-poll", "0.01"
        )
        cands = iter(
            [_cand("ROC(CLOSE,5)", "动量5日"), _cand("DELTA(CLOSE,3)", "价差3日")]
        )
        pauses = 0
        for _ in range(20):
            rc = el.main(argv, loader=self._loader(bars))
            if rc != relay.EXIT_PAUSE:
                break
            pauses += 1
            self._answer_pending(out_dir, cands)
        assert rc == 0 and pauses == 4  # 每次都超时 ⇒ 退化为退出模式，结果照常闭环

    def test_llm_errors_carried_across_pauses(self, tmp_path):
        """断点带 LLM 级错误累计；续跑 handler 从它起算。"""
        ck = tmp_path / "ck.json"
        relay.save_checkpoint(
            ck,
            tag="t",
            cfg_fp="f",
            position=(0, 0, 1),
            rng_state=None,
            transcript=[],
            pending={"request_id": "x"},
            llm_errors=3,
        )
        assert relay.load_checkpoint(ck)["llm_errors"] == 3
        h = el._make_on_event(type("A", (), {"max_tokens_budget": 0})(), object(), 3)
        h(
            {
                "decision": "llm_error",
                "round_i": 0,
                "cand_i": 1,
                "direction": "d",
                "phase": "p",
            }
        )
        assert h.llm_errors == 4
