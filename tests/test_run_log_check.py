# -*- coding: utf-8 -*-
"""run_log_check（五 runner 日度 run_log 例行核对）测试。"""

import json

import pytest

from custos.pipeline import run_log_check as rlc

TAGS = ["0850", "0905", "1445", "1700", "1800"]


def _stage(name, ok=True, **kw):
    s = {
        "name": name,
        "ok": ok,
        "returncode": 0 if ok else 1,
        "timeout": False,
        "duration_sec": 1.0,
    }
    s.update(kw)
    return s


def _write_log(log_dir, date, tag, status="completed", stages=()):
    log = {
        "date": date,
        "script": f"run_{tag}",
        "status": status,
        "started_at": f"{date}T08:50:00",
        "finished_at": f"{date}T08:51:00",
        "duration_sec": 60.0,
        "stages": list(stages),
    }
    p = log_dir / f"{date}_{tag}_run_log.json"
    p.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")


def _full_tree(log_dir, date, **overrides):
    for tag in TAGS:
        kw = overrides.get(tag, {})
        _write_log(log_dir, date, tag, **kw)


@pytest.fixture()
def trading_day(monkeypatch, tmp_path):
    """默认目标日是交易日；LOG_DIR 指到合成假树。"""
    monkeypatch.setattr(rlc, "trading_day_status", lambda d: {"is_trading_day": True})
    monkeypatch.setattr(rlc, "LOG_DIR", tmp_path)


def test_all_ok(tmp_path, trading_day, capsys):
    _full_tree(tmp_path, "2026-08-07")
    rc = rlc.main(["--date", "2026-08-07"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "✅ 正常" in out and "run_0850: completed" in out


def test_missing_log_on_trading_day_alerts(tmp_path, trading_day, capsys):
    """交易日缺日志 = runner 没跑成，必须报警且 exit 1（不许静默）。"""
    _full_tree(tmp_path, "2026-08-07")
    (tmp_path / "2026-08-07_1700_run_log.json").unlink()
    rc = rlc.main(["--date", "2026-08-07"])
    assert rc == 1
    assert "run log 缺失" in capsys.readouterr().out


def test_non_trading_day_skips(tmp_path, monkeypatch, capsys):
    """非交易日五份日志天然不存在：报跳过、exit 0，不得报缺失。"""
    monkeypatch.setattr(
        rlc,
        "trading_day_status",
        lambda d: {"is_trading_day": False, "reason": "周末"},
    )
    rc = rlc.main(["--date", "2026-08-08"])  # 周六，日志一份都没有
    assert rc == 0
    out = capsys.readouterr().out
    assert "非交易日" in out and "缺失" not in out


def test_calendar_check_failure_is_fail_closed(tmp_path, monkeypatch):
    """日历判定出错时不许猜「今天休市」——否则 runner 全挂那天会被静默跳过。"""

    def boom(d):
        raise RuntimeError("日历缓存缺失")

    monkeypatch.setattr(rlc, "trading_day_status", boom)
    assert rlc.main(["--date", "2026-08-07"]) == 1


def test_expected_failures_do_not_raise_exit(
    tmp_path, trading_day, monkeypatch, capsys
):
    """#53 记录的两类预期内失败：best-effort note、历史日期 1445 快照 fresh 校验。"""
    monkeypatch.setattr(
        rlc, "cn_today", lambda: __import__("datetime").date(2026, 8, 10)
    )
    _full_tree(
        tmp_path,
        "2026-08-07",
        **{
            "1445": {
                "status": "degraded",
                "stages": [
                    _stage("collect_holding_quotes"),
                    _stage(
                        "close_review",
                        ok=False,
                        stderr_tail="captured_at 2026-08-06 非目标日",
                    ),
                ],
            },
            "1700": {
                "status": "degraded",
                "stages": [
                    _stage(
                        "collect_fund_flow", ok=False, note="best-effort，失败不中断"
                    ),
                ],
            },
        },
    )
    rc = rlc.main(["--date", "2026-08-07"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[预期内]" in out and "✅ 正常" in out


def test_unexpected_failure_raises_exit(tmp_path, trading_day, monkeypatch, capsys):
    """意外失败必须抬退出码——这是 cron/人工告警的唯一通道。"""
    monkeypatch.setattr(
        rlc, "cn_today", lambda: __import__("datetime").date(2026, 8, 7)
    )
    _full_tree(
        tmp_path,
        "2026-08-07",
        **{
            "0905": {
                "status": "failed",
                "stages": [_stage("daily_pipeline", ok=False, returncode=3)],
            }
        },
    )
    rc = rlc.main(["--date", "2026-08-07"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "[意外]" in out and "status=failed" in out


def test_historical_1445_rule_scoped_to_snapshot_stages(
    tmp_path, trading_day, monkeypatch
):
    """⚠️ 历史日期豁免只覆盖 1445 的快照类 stage——1445 的 runtime_gate 失败
    不在 #53 记录里，放宽会吞真回归。"""
    monkeypatch.setattr(
        rlc, "cn_today", lambda: __import__("datetime").date(2026, 8, 10)
    )
    _full_tree(
        tmp_path,
        "2026-08-07",
        **{
            "1445": {
                "status": "degraded",
                "stages": [_stage("runtime_gate", ok=False, returncode=4)],
            }
        },
    )
    assert rlc.main(["--date", "2026-08-07"]) == 1


def test_json_output_written(tmp_path, trading_day):
    _full_tree(tmp_path, "2026-08-07")
    rc = rlc.main(["--date", "2026-08-07", "--json"])
    assert rc == 0
    out = json.loads(
        (tmp_path / "2026-08-07_run_log_check.json").read_text(encoding="utf-8")
    )
    assert out["verdict"] == "ok" and len(out["runners"]) == 5
