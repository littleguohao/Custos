# -*- coding: utf-8 -*-
"""五个 runner 的 run_log 例行核对（TODO #53 的例行化缺口）。

每交易日盘后（建议 cron 18:30）对当天 `artifacts/logs/{date}_{tag}_run_log.json`
五份日志做一次机械核对：**该在不在、stage 失败是预期内还是意外**。

## 退出码语义（cron/人工告警只用它）

- 0：全部正常，或只有**预期内**失败（见下），或当天非交易日（日志天然不存在，跳过）。
- 1：意外——交易日缺日志（runner 没跑成）、status=failed/calendar_failed、
  交易日日志却写 closed、或有意料之外的 stage 失败。
- 交易日历判定本身失败：fail-closed，报错误并 exit 1（不猜「今天是交易日」，
  否则 runner 全挂的那天会被当成休市静默跳过）。

## 「预期内」失败的识别规则（TODO #53 记录的既有事实）

1. **note 含 `best-effort` 的 stage**（如 17:00 的 `collect_fund_flow` 网络失败、
   14:45 的 `collect_intraday_snapshot`）：设计上就不阻断，失败记 tolerated。
2. **历史日期复现 14:45 的盘中快照类 stage**（`close_review` /
   `collect_intraday_snapshot` / `collect_holding_quotes`，且核对日期 < 今天）：
   盘中快照的 fresh 校验（captured_at 必须是当日）对历史日期必然失败——
   这是校验在正确工作，不是回归。

⚠️ 第二条刻意**只覆盖 1445 的快照类 stage**：其他 runner 用历史日期复现失败
不在 #53 的记录里，放宽会吞掉真回归。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from custos.core.paths import LOGS, cn_today
from custos.core.runtime_guards import trading_day_status

RUNNER_TAGS = ["0850", "0905", "1445", "1700", "1800"]

# 14:45 的盘中快照类 stage（历史日期复现时时新鲜度校验必然失败，见模块 docstring）
_1445_SNAPSHOT_STAGES = {
    "close_review",
    "collect_intraday_snapshot",
    "collect_holding_quotes",
}

# 模块级常量便于测试 monkeypatch（同 run_* 的 LOG_DIR 惯例）
LOG_DIR = LOGS


def _classify_stage(tag: str, stage: dict, target: str, today: str) -> tuple[bool, str]:
    """stage 失败是否预期内。返回 (is_expected, reason)。"""
    note = stage.get("note") or ""
    if "best-effort" in note:
        return True, f"best-effort stage（{note}），失败不阻断是设计"
    if tag == "1445" and stage.get("name") in _1445_SNAPSHOT_STAGES and target < today:
        return (
            True,
            "历史日期复现：盘中快照 fresh 校验（captured_at 非目标日）必然失败，非回归",
        )
    return False, ""


def check_run_logs(base_log_dir: Path, target: str) -> dict[str, Any]:
    """读 target 日五份 run log，逐 runner 逐 stage 汇总并分类失败。"""
    today = cn_today().isoformat()
    runners: list[dict[str, Any]] = []
    unexpected: list[str] = []
    tolerated: list[str] = []

    for tag in RUNNER_TAGS:
        path = base_log_dir / f"{target}_{tag}_run_log.json"
        if not path.exists():
            runners.append({"tag": tag, "status": "missing", "stages": []})
            unexpected.append(
                f"run_{tag}: 交易日但 run log 缺失（runner 没跑成）: {path.name}"
            )
            continue
        log = json.loads(path.read_text(encoding="utf-8"))
        status = log.get("status")
        entry: dict[str, Any] = {
            "tag": tag,
            "status": status,
            "duration_sec": log.get("duration_sec"),
            "stages": [],
        }
        if status in ("failed", "calendar_failed"):
            unexpected.append(f"run_{tag}: status={status}")
        elif status == "closed":
            # 交易日历说今天是交易日，runner 却记了休市 —— 两边必有一边错
            unexpected.append(
                f"run_{tag}: 交易日但 status=closed（日历与 runner 判定不一致）"
            )
        for st in log.get("stages") or []:
            failed = (not st.get("ok", False)) or bool(st.get("timeout"))
            if not failed:
                entry["stages"].append(
                    {
                        "name": st.get("name"),
                        "ok": True,
                        "duration_sec": st.get("duration_sec"),
                    }
                )
                continue
            expected, reason = _classify_stage(tag, st, target, today)
            rec = {
                "name": st.get("name"),
                "ok": False,
                "timeout": bool(st.get("timeout")),
                "returncode": st.get("returncode"),
                "duration_sec": st.get("duration_sec"),
                "expected": expected,
                "reason": reason,
            }
            entry["stages"].append(rec)
            msg = f"run_{tag}/{st.get('name')}: 失败（rc={st.get('returncode')}, timeout={bool(st.get('timeout'))}）"
            if expected:
                tolerated.append(f"{msg}——预期内：{reason}")
            else:
                unexpected.append(f"{msg}——意外失败")
        runners.append(entry)

    return {
        "date": target,
        "checked_at": today,
        "verdict": "fail" if unexpected else "ok",
        "unexpected": unexpected,
        "tolerated": tolerated,
        "runners": runners,
    }


def render_text(result: dict[str, Any]) -> str:
    lines = [f"run_log 核对｜{result['date']}"]
    for r in result["runners"]:
        n_fail = sum(1 for s in r["stages"] if not s["ok"])
        lines.append(
            f"  run_{r['tag']}: {r['status']}"
            + (f"（{n_fail} 个 stage 失败）" if n_fail else "")
            + (
                f"，耗时 {r['duration_sec']}s"
                if r.get("duration_sec") is not None
                else ""
            )
        )
    for msg in result["tolerated"]:
        lines.append(f"  [预期内] {msg}")
    for msg in result["unexpected"]:
        lines.append(f"  [意外] {msg}")
    lines.append(f"结论：{'❌ 有意外' if result['verdict'] == 'fail' else '✅ 正常'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    # 2026-08-29：GBK 控制台（Windows 默认/cron 环境）打印 ✅ 会 UnicodeEncodeError
    # ——崩溃的退出码 1 与「发现异常」语义撞车（假告警）。与其他 runner 同规：
    # 入口先 reconfigure UTF-8。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="五 runner 日度 run_log 例行核对")
    ap.add_argument(
        "--date", default=cn_today().isoformat(), help="YYYY-MM-DD，默认今天"
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="结果落盘 artifacts/logs/{date}_run_log_check.json",
    )
    args = ap.parse_args(argv)
    target = args.date

    # 非交易日：五份日志天然不存在，跳过 ≠ 缺失
    try:
        td = trading_day_status(target)
    except Exception as exc:  # noqa: BLE001 —— fail-closed：判不出来就告警，不猜
        print(
            f"run_log 核对｜{target}\n  [意外] 交易日历判定失败: {exc!r}（不猜，按异常处理）"
        )
        return 1
    if td.get("is_trading_day") is not True:
        print(
            f"run_log 核对｜{target}\n  非交易日（{td.get('reason') or '休市'}），跳过。"
        )
        return 0

    result = check_run_logs(LOG_DIR, target)
    print(render_text(result))

    if args.json:
        out = LOG_DIR / f"{target}_run_log_check.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"check written: {out}")
    return 1 if result["verdict"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
