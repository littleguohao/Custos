# -*- coding: utf-8 -*-
"""运行门控 CLI:写 data/quality/{date}_runtime_gate.json 并按开关决定退出码。

退出码(供 run_*.py / cron 判定,门控必须能真正阻断,而非只写 JSON):
  0 通过
  3 --require-trading-day 且非交易日
  4 --require-quality 且 market_quality=blocked(核心市场数据大面积缺失/陈旧)
  5 --require-position-gate 且 position_gate=blocked(持仓基线或当日行情不可用)
多个条件同时不满足时,按 3 > 4 > 5 的优先级返回(先报最根本的原因)。
"""
from __future__ import annotations

import argparse
import json
import sys

from custos.core.runtime_guards import write_runtime_gate

EXIT_NOT_TRADING_DAY = 3
EXIT_QUALITY_BLOCKED = 4
EXIT_POSITION_BLOCKED = 5

# 放行白名单。**不能写成 `== "blocked"`**:gate 为空、字段拼错、JSON 截断时
# `status` 是 None,`None == "blocked"` 为假 ⇒ 门控自身坏掉却返回 0 放行(fail-open)。
# 风控组件的未知状态必须等于阻断,所以改成"只有明确 pass/degraded 才放行"。
_QUALITY_PASS = {"pass", "degraded"}
_POSITION_PASS = {"pass", "degraded"}


def decide_exit_code(gate: dict, *, require_trading_day: bool = False,
                     require_quality: bool = False,
                     require_position_gate: bool = False) -> int:
    """纯函数:据门控结果与开关算退出码(便于测试,不含 IO)。

    对每一项都采用 fail-closed 白名单判定:状态缺失/未知一律视为阻断。
    """
    if require_trading_day and (gate.get("calendar") or {}).get("is_trading_day") is not True:
        return EXIT_NOT_TRADING_DAY
    if require_quality and (gate.get("market_quality") or {}).get("status") not in _QUALITY_PASS:
        return EXIT_QUALITY_BLOCKED
    if require_position_gate and (gate.get("position_gate") or {}).get("status") not in _POSITION_PASS:
        return EXIT_POSITION_BLOCKED
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="运行门控:写 runtime_gate.json 并按开关返回退出码")
    p.add_argument("--date", required=True)
    p.add_argument("--data-session", choices=["preclose", "postclose"], default="postclose",
                   help="数据 session:preclose(盘前/盘中,期望数据日=T-1) / postclose(盘后,期望=T,默认)")
    p.add_argument("--require-trading-day", action="store_true", help=f"非交易日 exit {EXIT_NOT_TRADING_DAY}")
    p.add_argument("--require-quality", action="store_true",
                   help=f"market_quality=blocked 时 exit {EXIT_QUALITY_BLOCKED}")
    p.add_argument("--require-position-gate", action="store_true",
                   help=f"position_gate=blocked 时 exit {EXIT_POSITION_BLOCKED}")
    a = p.parse_args(argv)
    expected = None
    if a.data_session == "preclose":                     # 盘前/盘中:当日 K 线尚不存在,期望数据日=最近确认交易日
        from custos.core.runtime_guards import previous_confirmed_trading_day
        expected = previous_confirmed_trading_day(a.date) or a.date
    r = write_runtime_gate(a.date, expected_day=expected)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    code = decide_exit_code(r, require_trading_day=a.require_trading_day,
                            require_quality=a.require_quality,
                            require_position_gate=a.require_position_gate)
    if code:
        mq = (r.get("market_quality") or {}).get("status")
        pg = (r.get("position_gate") or {}).get("status")
        reason = {EXIT_NOT_TRADING_DAY: "非交易日",
                  EXIT_QUALITY_BLOCKED: f"market_quality={mq!r}(score={(r.get('market_quality') or {}).get('quality_score')})",
                  EXIT_POSITION_BLOCKED: f"position_gate={pg!r}"}[code]
        print(f"[GATE] {a.date} 阻断:{reason}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
