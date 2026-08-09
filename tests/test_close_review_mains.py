"""`final_close_review.main` 与 `review_core.main` —— 两份报告的**编排层**。

覆盖率清点（2026-08-07）：`final_close_review` 33%（`main` 210 行占大头）、
`review_core` 62%（`main` 125 行）。两者都是硬失败 stage
（`run_1700` / `run_1445` 里一挂就没有报告）。

编排层最该测的不是文案，而是**契约**：
① 强制输入缺失必须硬失败（而不是产出一份缺内容的报告）
② 可选输入缺失必须降级并留痕
③ 产物落到约定路径
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("07_tools", "07_tools/close_review", "07_tools/market_timing"):
    sys.path.insert(0, str(ROOT / _p))

from close_review import final_close_review as fcr  # noqa: E402
from close_review import review_core as rc  # noqa: E402

DAY = "2026-08-07"

# ── final_close_review 的 8 个强制输入（缺任一即 SystemExit）
MANDATORY = {
    "decisions/2026-08-07_chief_decision.json": {
        "date": DAY, "total_position_range": "20%-40%", "new_position_permission": "禁止",
        "risk_level": "普通", "holding_actions": [], "forbidden_actions": [],
        "market_quality": {"status": "pass", "checks": []}},
    "market/2026-08-07_market_timing_input.json": {
        # ⚠️ 0AMV 必须 confirmed + 有 regime + 有数值 —— 见 TestZeroAmvGate
        "date": DAY, "amv_0": {"effective_state": "中性", "quality": "confirmed",
                               "amv_change_pct": 0.5},
        "a_share_indices": {}, "breadth": {}, "sentiment": {}},
    "quality/2026-08-07_runtime_gate.json": {
        "date": DAY, "market_quality": {"status": "pass"},
        "position_gate": {"allow_position_increase": True}},
    "holdings/2026-08-07_holding_technical_summary.json": [],
    "sectors/2026-08-07_sector_technical_summary.json": [],
    "market/2026-08-07_holding_quotes.json": {"date": DAY, "quotes": []},
    "review_steps/2026-08-07_execution_review.json": {"date": DAY, "rows": []},
    "review_steps/2026-08-07_review_enrichment.json": {
        "date": DAY, "theme_lifecycles": [], "holding_diagnoses": [],
        "next_day_plan": {"holding_plans": []}, "rule_review": {}},
}


def _write(root, rel, obj):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def fcr_env(tmp_path, monkeypatch):
    data = tmp_path / "01_data"
    monkeypatch.setattr(fcr, "DATA", data)
    monkeypatch.setattr(fcr, "REV", tmp_path / "04_reviews" / "daily")
    for rel, obj in MANDATORY.items():
        _write(data, rel, obj)
    _write(data, "trades/current_positions.json", [])
    _write(data, "trades/trades_stock.json", [])
    return tmp_path


def _run_fcr(monkeypatch, extra=()):
    monkeypatch.setattr(sys, "argv", ["x", "--date", DAY, *extra])
    fcr.main()


class TestFinalCloseReviewInputContract:
    @pytest.mark.parametrize("rel", list(MANDATORY))
    def test_each_mandatory_input_is_enforced(self, fcr_env, monkeypatch, rel):
        """⚠️ 8 个强制输入**逐个**验证 —— 缺任一必须硬失败。

        为什么不能降级：这份报告是当日交易的最终留痕。缺 `chief_decision` 就没有
        当日权限、缺 `execution_review` 就不知道计划执行了没有 ——
        产出一份「看起来完整但少了一节」的报告，比明确失败更危险。
        """
        (fcr_env / "01_data" / rel).unlink()
        with pytest.raises(SystemExit) as e:
            _run_fcr(monkeypatch)
        assert "mandatory close-review input missing" in str(e.value)

    def test_news_is_optional_and_degrades(self, fcr_env, monkeypatch):
        """⚠️ 新闻**不在**强制清单里 —— 缺它降级并在报告里写「新闻数据缺失」。

        理由：新闻是证据层补充，缺它不影响「今天做了什么、权限是什么」这个核心。
        """
        _run_fcr(monkeypatch)
        body = (fcr_env / "04_reviews" / "daily" / f"{DAY}_final_review.md").read_text(
            encoding="utf-8")
        assert "新闻数据缺失" in body and "postclose_news_digest" in body

    def test_writes_md_and_json(self, fcr_env, monkeypatch):
        _run_fcr(monkeypatch)
        rev = fcr_env / "04_reviews" / "daily"
        assert (rev / f"{DAY}_final_review.md").exists()
        assert (rev / f"{DAY}_final_review.json").exists()

    def test_json_artifact_is_valid_json(self, fcr_env, monkeypatch):
        """产物必须是合法 JSON（NaN/Infinity 都不是）—— 下游要能解析。"""
        _run_fcr(monkeypatch)
        raw = (fcr_env / "04_reviews" / "daily" / f"{DAY}_final_review.json").read_text(
            encoding="utf-8")
        assert "NaN" not in raw and "Infinity" not in raw
        json.loads(raw)

    def test_no_trades_confirmed_flag_recorded(self, fcr_env, monkeypatch):
        """`--no-trades-confirmed` 是「当日确认无交易」，必须在报告里可辨识 ——
        否则「没有成交记录」与「没导入成交」分不开。"""
        _run_fcr(monkeypatch, extra=["--no-trades-confirmed"])
        body = (fcr_env / "04_reviews" / "daily" / f"{DAY}_final_review.md").read_text(
            encoding="utf-8")
        plain = (fcr_env / "04_reviews" / "daily" / f"{DAY}_final_review.json").read_text(
            encoding="utf-8")
        assert "无交易" in body or "no_trades" in plain


# ── review_core（14:45）
@pytest.fixture()
def rc_env(tmp_path, monkeypatch):
    for name, sub in [("TRADES", "01_data/trades"), ("HOLDINGS", "01_data/holdings"),
                      ("RISK", "01_data/risk"), ("MARKET", "01_data/market"),
                      ("QUALITY", "01_data/quality"), ("PLANS", "03_daily_plans"),
                      ("LOGS", "06_logs")]:
        d = tmp_path / sub
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rc, name, d)
    _write(tmp_path, "01_data/trades/current_positions.json",
           [{"代码": "600000", "名称": "浦发银行", "持有数量": 1000, "单位成本": 10.0}])
    _write(tmp_path, "01_data/market/2026-08-07_holding_quotes.json",
           {"date": DAY, "as_of": f"{DAY} 14:45:00",
            "quotes": [{"code": "600000", "price": 11.0, "change_pct": 1.5, "date": DAY}]})
    _write(tmp_path, "01_data/holdings/2026-08-07_holding_technical_summary.json",
           [{"code": "600000", "trend_state": "上涨", "box20_position": "箱体上半区",
             "latest_date": DAY}])
    _write(tmp_path, "01_data/market/2026-08-07_market_timing_input.json",
           {"date": DAY, "amv_0": {"effective_state": "中性"}})
    _write(tmp_path, "01_data/quality/2026-08-07_runtime_gate.json",
           {"date": DAY, "market_quality": {"status": "pass"}})
    return tmp_path


def _run_rc(monkeypatch, extra=()):
    monkeypatch.setattr(sys, "argv", ["x", "--date", DAY, *extra])
    rc.main()


class TestReviewCoreMain:
    def test_writes_report(self, rc_env, monkeypatch):
        _run_rc(monkeypatch)
        assert list((rc_env / "03_daily_plans").glob("*.md")), "应产出 14:45 报告"

    def test_missing_risk_decision_flagged_not_crashed(self, rc_env, monkeypatch):
        """⚠️ 无 risk_decision 时不崩，但报告里必须写明「按无风控依据处理」——
        14:45 常态就是当日 risk_decision 还没产出（它 17:00 才跑）。"""
        _run_rc(monkeypatch)
        body = next((rc_env / "03_daily_plans").glob("*.md")).read_text(encoding="utf-8")
        assert "风控依据数据日" in body
        assert "缺失" in body or "非当日" in body

    def test_stale_risk_decision_marked_non_current(self, rc_env, monkeypatch):
        """回退到旧 risk_decision 必须打「⚠️非当日」并写明不得据此放宽权限。"""
        _write(rc_env, "01_data/risk/2026-08-05_risk_decision.json",
               {"date": "2026-08-05", "stock_risks": []})
        _run_rc(monkeypatch)
        body = next((rc_env / "03_daily_plans").glob("*.md")).read_text(encoding="utf-8")
        assert "2026-08-05" in body and "非当日" in body
        assert "不得据此放宽" in body

    def test_emit_digest_prints_bounded_summary(self, rc_env, monkeypatch, capsys):
        _run_rc(monkeypatch, extra=["--emit-digest"])
        out = capsys.readouterr().out
        assert out.strip(), "--emit-digest 应输出摘要供投递"

    def test_emit_report_prints_body(self, rc_env, monkeypatch, capsys):
        _run_rc(monkeypatch, extra=["--emit-report"])
        assert "##" in capsys.readouterr().out, "--emit-report 应输出报告正文"

    def test_missing_quotes_does_not_produce_actions_from_stale_price(self, rc_env, monkeypatch):
        """⚠️ 当日行情缺失时，持仓动作必须是「等待当日行情」而非用旧价算出的动作。"""
        (rc_env / "01_data" / "market" / f"{DAY}_holding_quotes.json").unlink()
        _run_rc(monkeypatch)
        body = next((rc_env / "03_daily_plans").glob("*.md")).read_text(encoding="utf-8")
        assert "等待当日行情" in body


class TestReportAuditBlock:
    """可审计块（待办 #29）：`report_id` / 策略版本 / 数据截止 / 输入清单。

    原 MASTER_WORKFLOW §十二 第 8 条全仓零实现；出问题时无法定位
    「当时用的哪版规则、哪天的数据」（研究侧 R13 同类问题）。
    """

    def test_strategy_version_reads_latest_from_log(self):
        import re
        import report_audit
        version = report_audit.strategy_version()
        assert re.fullmatch(r"v\d+\.\d+", version), f"应取到版本日志最新版本号，得到 {version!r}"

    def test_build_fields_and_missing_input_marker(self, tmp_path):
        import report_audit
        present = tmp_path / "a.json"
        present.write_text("{}", encoding="utf-8")
        audit = report_audit.build("2026-08-07", "1445", [present, tmp_path / "gone.json"])
        assert audit["report_id"].startswith("2026-08-07_1445_")
        assert audit["strategy_version"] and audit["data_as_of"]
        assert audit["inputs"][0]["sha1"] and audit["inputs"][1]["sha1"] is None

    def test_same_inputs_same_report_id(self, tmp_path):
        """同一天同一份输入重跑 → 同一个 report_id（简单确定，不掺随机/时钟）。"""
        import report_audit
        present = tmp_path / "a.json"
        present.write_text("{}", encoding="utf-8")
        a1 = report_audit.build("2026-08-07", "1445", [present])
        a2 = report_audit.build("2026-08-07", "1445", [present])
        assert a1["report_id"] == a2["report_id"]
        present.write_text('{"x": 1}', encoding="utf-8")
        assert report_audit.build("2026-08-07", "1445", [present])["report_id"] != a1["report_id"]

    def test_1445_md_and_log_json_carry_audit(self, rc_env, monkeypatch):
        _run_rc(monkeypatch)
        body = next((rc_env / "03_daily_plans").glob("*.md")).read_text(encoding="utf-8")
        assert "report_id" in body and "策略版本" in body and "输入清单" in body
        log = json.loads(next((rc_env / "06_logs").glob("*_1445_review.json")).read_text(encoding="utf-8"))
        audit = log["audit"]
        assert audit["report_id"].startswith(f"{DAY}_1445_")
        assert audit["strategy_version"] and audit["data_as_of"] and audit["inputs"]

    def test_final_review_md_and_json_carry_audit(self, fcr_env, monkeypatch):
        _run_fcr(monkeypatch)
        rev = fcr_env / "04_reviews" / "daily"
        body = (rev / f"{DAY}_final_review.md").read_text(encoding="utf-8")
        assert "report_id" in body and "策略版本" in body and "输入清单" in body
        payload = json.loads((rev / f"{DAY}_final_review.json").read_text(encoding="utf-8"))
        audit = payload["audit"]
        assert audit["report_id"].startswith(f"{DAY}_close_review_")
        assert audit["strategy_version"] and audit["data_as_of"] and audit["inputs"]
        # 契约把 audit 钉住：四件齐全且 data_as_of 可 null 但存在
        from contracts import check
        result = check("final_review", payload)
        assert result["valid"], result["errors"]


class TestZeroAmvGate:
    """⚠️ 盘后复盘要求 **0AMV 必须是 confirmed 观测**，否则硬失败。

    这是文件存在性检查之外的**第 9 条强制要求**，且是语义级的 ——
    `market_timing_input.json` 存在但 0AMV 未确认，一样不放行。

    口径来自 v0.22：「0AMV regime 状态转移**显式 gate 在 `quality=='confirmed'`**
    —— 未确认的 0AMV 数值切换 regime 会让全链方向判断建立在猜测上」。
    盘后是当日 regime 的定稿时点，所以这里是硬闸而不是降级。
    """

    @pytest.mark.parametrize("amv,why", [
        ({"effective_state": "中性", "quality": "confirmed"}, "缺数值"),
        ({"effective_state": "中性", "quality": "candidate", "amv_change_pct": 0.5}, "非 confirmed"),
        ({"effective_state": "中性", "amv_change_pct": 0.5}, "无 quality 字段"),
        ({"effective_state": "", "quality": "confirmed", "amv_change_pct": 0.5}, "regime 为空串"),
        ({"quality": "confirmed", "amv_change_pct": 0.5}, "无 regime"),
        ({}, "整块缺失"),
    ])
    def test_unconfirmed_amv_is_refused(self, fcr_env, monkeypatch, amv, why):
        _write(fcr_env / "01_data", "market/2026-08-07_market_timing_input.json",
               {"date": DAY, "amv_0": amv, "a_share_indices": {},
                "breadth": {}, "sentiment": {}})
        with pytest.raises(SystemExit) as e:
            _run_fcr(monkeypatch)
        assert "0AMV" in str(e.value), f"{why} 应被拒"

    def test_confirmed_amv_passes(self, fcr_env, monkeypatch):
        _run_fcr(monkeypatch)
        assert (fcr_env / "04_reviews" / "daily" / f"{DAY}_final_review.md").exists()
