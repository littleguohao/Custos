# -*- coding: utf-8 -*-
"""门控建议区块回归测试（2026-08-03 裁定的设计边界）。

**18:00 是纯粹的选股流程，门控不得影响选股结果。** 它只在备选表里单独给出建议，
不改 bucket、不改 next_step、不改分层、不筛掉任何候选。

三个理由（写进 _gate_advisory_section docstring）：
  ① 选股结果保持与回测同口径——门控若改写分层，live 候选就无法与回测对照，
     "策略本身选出了什么"变得不可回溯；
  ② 职责分离：选股逻辑不混入运行时数据质量判断；
  ③ 可复现：同一天重跑，候选表不因数据新鲜度而变。

本文件的核心是最后一个 class：无论门控说什么，选股输出必须逐字节一致。
"""
from __future__ import annotations

import json

import pytest

import candidate_table as ct


def _gate(status="degraded", amv_ok=False, limitations=None, allow_increase=False,
          pg_limits=None):
    return {
        "market_quality": {"status": status, "amv_ok": amv_ok, "quality_score": 0.65,
                           "limitations": limitations if limitations is not None
                           else ["0AMV=candidate：regime 未知，不得据此加仓"]},
        "position_gate": {"allow_position_increase": allow_increase,
                          "limitations": pg_limits or ["regime=未知不在加仓白名单"]},
    }


def _pool(amv_state="做多", status="ok"):
    """一个最小但完整的 stock_pool，含 A/B 池与四面共振候选。"""
    def cand(code, bucket, aligned, bull):
        return {
            "code": code, "name": f"股{code}", "bucket": bucket,
            "next_step": "generate_buy_plan" if bucket == "A" else "observe_price",
            "fundamental_quality": {"tier": "优"},
            "resonance_4leg": {"sector": True, "technical": True, "market": True,
                               "aligned": aligned, "bull_candidate": bull},
            "score_detail": {"total": 80 if bucket == "A" else 60},
            "sector": "半导体", "sector_state": "主升",
        }
    return {
        "date": "2026-08-03", "status": status, "amv_state": amv_state,
        "market_permission": "允许",
        "bucket_counts": {"A": 1, "B": 1},
        "candidates": [cand("600000", "A", 4, True), cand("000001", "B", 3, True)],
    }


class TestAdvisoryRendering:
    def test_pass_takes_no_space(self):
        """数据齐全时不占版面。"""
        assert ct._gate_advisory_section("2026-08-03",
                                         _gate(status="pass", amv_ok=True,
                                               limitations=[])) == []

    def test_degraded_shows_status_and_limitations(self):
        out = "\n".join(ct._gate_advisory_section("2026-08-03", _gate()))
        assert "数据可信度提示" in out
        assert "degraded" in out
        assert "0AMV 新鲜：**否**" in out
        assert "regime 未知" in out

    def test_warns_regime_may_be_stale(self):
        """0AMV 不新鲜时必须点明"上方那个 regime 值可能是过期的"。"""
        out = "\n".join(ct._gate_advisory_section("2026-08-03", _gate(amv_ok=False)))
        assert "regime 值可能来自过期数据" in out
        assert "空头不买" in out and "待0AMV做多" in out

    def test_no_regime_warning_when_amv_fresh(self):
        out = "\n".join(ct._gate_advisory_section(
            "2026-08-03", _gate(status="degraded", amv_ok=True,
                                limitations=["turnover=stale(as_of=2026-08-01)"])))
        assert "regime 值可能来自过期数据" not in out
        assert "turnover=stale" in out

    def test_shows_position_gate_denial(self):
        out = "\n".join(ct._gate_advisory_section("2026-08-03",
                                                 _gate(allow_increase=False)))
        assert "加仓授权：**未授予**" in out

    def test_omits_position_gate_when_granted(self):
        out = "\n".join(ct._gate_advisory_section("2026-08-03",
                                                 _gate(allow_increase=True)))
        assert "加仓授权" not in out

    def test_always_states_it_does_not_rewrite(self):
        """区块必须自我声明"未改写选股结果"，避免读者误以为候选被门控过滤过。"""
        out = "\n".join(ct._gate_advisory_section("2026-08-03", _gate()))
        assert "未被门控改写" in out
        assert "不影响上方选股结果" in out

    def test_missing_gate_file_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ct, "QUALITY_DIR", tmp_path)
        out = "\n".join(ct._gate_advisory_section("2026-08-03"))
        assert "运行门控结论缺失" in out
        assert "仍为策略选股结果" in out

    def test_corrupt_gate_file_does_not_break(self, tmp_path, monkeypatch):
        (tmp_path / "2026-08-03_runtime_gate.json").write_text("{bad", encoding="utf-8")
        monkeypatch.setattr(ct, "QUALITY_DIR", tmp_path)
        out = "\n".join(ct._gate_advisory_section("2026-08-03"))
        assert "运行门控结论缺失" in out          # 降级为"缺失"，不抛异常

    def test_reads_from_disk_when_not_injected(self, tmp_path, monkeypatch):
        (tmp_path / "2026-08-03_runtime_gate.json").write_text(
            json.dumps(_gate()), encoding="utf-8")
        monkeypatch.setattr(ct, "QUALITY_DIR", tmp_path)
        out = "\n".join(ct._gate_advisory_section("2026-08-03"))
        assert "degraded" in out


class TestGateNeverAltersSelection:
    """**本文件的核心断言**：门控说什么都不能改变选股输出。"""

    GATES = [
        None,
        {},
        _gate(status="pass", amv_ok=True, limitations=[]),
        _gate(status="degraded", amv_ok=False),
        _gate(status="blocked", amv_ok=False,
              limitations=["0AMV=missing", "market_breadth=stale", "turnover=missing"]),
        _gate(status="pass", amv_ok=True, limitations=[], allow_increase=False),
    ]

    def _selection_part(self, text: str) -> str:
        """剥掉门控输出，只留选股部分。

        门控有两种形态：``## 🚦`` 区块（有门控数据时）与"运行门控结论缺失"单行
        （门控文件缺失时）。两种都要剥净，否则基准与对照本身就不可比。
        剥完归一化连续空行——区块前后的空行数不该影响"选股输出是否一致"的判定。
        """
        out, skipping = [], False
        for line in text.split("\n"):
            if line.startswith("## 🚦"):
                skipping = True
                continue
            if skipping:
                if line.startswith("## "):        # 下一个标题：区块结束
                    skipping = False
                else:
                    continue
            if "运行门控结论缺失" in line:
                continue
            out.append(line)
        normalized, prev_blank = [], False
        for line in out:
            blank = not line.strip()
            if blank and prev_blank:
                continue
            normalized.append(line)
            prev_blank = blank
        return "\n".join(normalized)

    @pytest.mark.parametrize("gate", GATES)
    def test_selection_output_is_byte_identical(self, gate, tmp_path, monkeypatch):
        monkeypatch.setattr(ct, "QUALITY_DIR", tmp_path)
        # 基准用"pass 且无受限项"的门控——该场景区块返回空，是最干净的对照
        clean = {"market_quality": {"status": "pass", "amv_ok": True,
                                    "quality_score": 1.0, "limitations": []}}
        baseline = self._selection_part(ct.render_table(_pool(), "2026-08-03", gate=clean))
        got = self._selection_part(ct.render_table(_pool(), "2026-08-03", gate=gate))
        assert got == baseline, "门控改变了选股输出——违反 18:00 纯选股的设计边界"

    @pytest.mark.parametrize("gate", GATES)
    def test_signal_summary_unchanged(self, gate, tmp_path, monkeypatch):
        """⭐ 信号一览的三档内容不得因门控而变。"""
        monkeypatch.setattr(ct, "QUALITY_DIR", tmp_path)
        text = ct.render_table(_pool(), "2026-08-03", gate=gate)
        assert "- **可买（A+四面共振）**：600000 股600000" in text

    @pytest.mark.parametrize("gate", GATES)
    def test_candidate_count_unchanged(self, gate, tmp_path, monkeypatch):
        """门控不得筛掉任何候选。"""
        monkeypatch.setattr(ct, "QUALITY_DIR", tmp_path)
        text = ct.render_table(_pool(), "2026-08-03", gate=gate)
        for code in ("600000", "000001"):
            assert code in text

    def test_bear_discipline_still_from_pool_not_gate(self, tmp_path, monkeypatch):
        """"空头不买"来自 pool.amv_state（选股链自己的风控腿），不是门控。"""
        monkeypatch.setattr(ct, "QUALITY_DIR", tmp_path)
        bear = ct.render_table(_pool(amv_state="空头触发"), "2026-08-03",
                               gate=_gate(status="pass", amv_ok=True, limitations=[]))
        assert "0AMV 空头：今日无可买信号" in bear
        long_ = ct.render_table(_pool(amv_state="做多"), "2026-08-03",
                                gate=_gate(status="blocked", amv_ok=False))
        assert "0AMV 空头：今日无可买信号" not in long_, \
            "门控 blocked 不该让做多日显示空头纪律"

    def test_advisory_placed_before_signal_summary(self, tmp_path, monkeypatch):
        """先知道数据可不可信，再看信号。"""
        monkeypatch.setattr(ct, "QUALITY_DIR", tmp_path)
        text = ct.render_table(_pool(), "2026-08-03", gate=_gate())
        assert text.index("🚦 数据可信度提示") < text.index("⭐ 今日信号一览")


class TestRun1800GateStage:
    """run_1800 必须落盘门控，且不能传任何 --require-* 开关。"""

    def test_stage_present_and_non_blocking(self):
        import inspect

        import run_1800
        src = inspect.getsource(run_1800.main)
        assert "runtime_gate.py" in src, "18:00 链需落盘门控供候选表引用"
        gate_call = src[src.index("runtime_gate.py") - 400:src.index("runtime_gate.py") + 400]
        for flag in ("--require-quality", "--require-position-gate", "--require-trading-day"):
            assert flag not in gate_call, f"选股链不得因 {flag} 失败"
