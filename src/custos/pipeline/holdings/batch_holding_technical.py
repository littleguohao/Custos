# -*- coding: utf-8 -*-
"""批量计算持仓技术面 → {date}_holding_technical_summary.json。

**为什么改成进程内计算**：原实现对每一只持仓 fork 一个 `uv run python
technical_monitor.py` 子进程（N 只持仓 = N 次解释器启动 + N 次 pandas/mootdx
导入），持仓十来只时光是进程与导入开销就是秒级，且每次失败只能靠 stderr 尾巴归因。
现在默认在进程内调 technical_monitor.analyze，并按代码 memoize（同一代码重复出现
在 mapping 里不重复算）。缓存**可失效可注入**：clear_analysis_cache() 显式清理，
analyze_code 是模块级函数便于替换；`--subprocess` 保留旧的逐股 fork 路径作为兜底。
"""

from __future__ import annotations
import argparse, json, os, subprocess, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import BASE, HOLDINGS_DIR, TRADES_DIR  # noqa: E402
from custos.core.paths import read_json as load  # noqa: E402
from custos.core.contracts import require  # noqa: E402
import sys

PY = Path(sys.executable)
TECH = BASE / "src" / "custos" / "pipeline" / "market_timing" / "technical_monitor.py"
HOLD = HOLDINGS_DIR
TRADES = TRADES_DIR / "current_positions.json"

# code -> analysis dict（或 {"__error__": msg}）。一次运行内的 memo，避免重复计算。
_ANALYSIS_CACHE: dict[str, dict] = {}


def clear_analysis_cache() -> None:
    """显式失效技术面分析缓存（测试 / 长驻进程用）。"""
    _ANALYSIS_CACHE.clear()


def pos_to_row(p):
    code = str(p.get("代码", "")).split(".")[0]
    return {
        "code": code,
        "name": p.get("名称", ""),
        "holding_amount": p.get("持有金额"),
        "holding_pnl": p.get("持有盈亏"),
        "holding_pnl_pct": p.get("持有盈亏率"),
        "position_pct": p.get("仓位占比"),
        "holding_days": p.get("持仓天数"),
        "industry": p.get("关联板块") or "",
        "concepts": [],
        "industry_chain": "",
        "primary_themes": [],
    }


def analyze_code(code: str, name: str = "") -> dict:
    """进程内计算单只代码的技术面（返回 technical_monitor.analyze 的 analysis 段）。"""
    from custos.pipeline.market_timing import (
        technical_monitor as tm,
    )  # 延迟导入：只在真的要算时才付 pandas 代价
    from custos.core.code_utils import norm_code

    tcode = norm_code(code)
    return tm.analyze(tm.read_vipdoc(tcode), tcode)


def _analysis_via_subprocess(code: str, name: str, date: str, out: Path) -> dict:
    """旧路径：逐股 fork technical_monitor.py（--subprocess 兜底用）。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run(
        [
            str(PY),
            str(TECH),
            "--code",
            code,
            "--name",
            name,
            "--date",
            date,
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if p.returncode != 0:
        return {"available": False, "error": (p.stderr or "")[-1000:]}
    return load(out, {}).get("analysis", {})


def _analysis_for(code: str, name: str, date: str, use_subprocess: bool) -> dict:
    if code in _ANALYSIS_CACHE:
        return _ANALYSIS_CACHE[code]
    out = HOLD / f"{date}_technical_{code}.json"
    if use_subprocess:
        an = _analysis_via_subprocess(code, name, date, out)
    else:
        try:
            an = analyze_code(code, name)
        except Exception as e:  # 单只失败只降级这一只，不影响其余持仓
            an = {"available": False, "error": f"{type(e).__name__}: {e}"}
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {"code": code, "name": name, "analysis": an},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            print(f"[WARN] 单股技术面落盘失败 {code}: {e}", file=sys.stderr)
    _ANALYSIS_CACHE[code] = an
    return an


def _trend_fields(an: dict) -> dict:
    trend = an.get("trend") or {}
    return {
        "latest_date": an.get("latest_date"),
        "trend_state": trend.get("state"),
        "close": trend.get("close"),
        "ma25": trend.get("ma25"),
        "ma60": trend.get("ma60"),
        "ma144": trend.get("ma144"),
        "ma240": trend.get("ma240"),
        "above_ma25": trend.get("above_ma25"),
        "above_ma60": trend.get("above_ma60"),
        "above_ma144": trend.get("above_ma144"),
        "above_ma240": trend.get("above_ma240"),
    }


def _bbi_fields(an: dict) -> dict:
    bbi = an.get("bbi") or {}
    return {
        "bbi": bbi.get("value"),
        "above_bbi": bbi.get("close_above"),
        "bbi_distance_pct": bbi.get("distance_pct"),
        "consecutive_closes_below_bbi": bbi.get("consecutive_closes_below"),
    }


def _n_structure_fields(an: dict) -> dict:
    ns = an.get("n_structure") or {}
    return {
        "n_structure": an.get("n_structure") or {"available": False},
        "descending_n_structure": an.get("descending_n_structure")
        or {"available": False},
        "n_structure_prior_low": ns.get("prior_low"),
        "n_structure_prior_low_date": ns.get("prior_low_date"),
        "n_structure_origin_extreme_low": ns.get("origin_extreme_low"),
        "n_structure_pullback_low": ns.get("pullback_low"),
        "n_structure_pullback_low_date": ns.get("pullback_low_date"),
        "n_structure_breakout_level": ns.get("breakout_level"),
        "n_structure_confirmed_date": ns.get("confirmed_date"),
    }


def _box_fields(an: dict) -> dict:
    box20 = an.get("box_20d") or {}
    box60 = an.get("box_60d") or {}
    return {
        "box20_upper": box20.get("upper"),
        "box20_lower": box20.get("lower"),
        "box20_mid": box20.get("mid"),
        "box20_position": box20.get("position"),
        "box60_upper": box60.get("upper"),
        "box60_lower": box60.get("lower"),
        "box60_mid": box60.get("mid"),
        "box60_position": box60.get("position"),
    }


def _oscillator_fields(an: dict) -> dict:
    daily_kdj = (an.get("daily") or {}).get("kdj") or {}
    daily_macd = (an.get("daily") or {}).get("macd") or {}
    weekly_kdj = (an.get("weekly") or {}).get("kdj") or {}
    weekly_macd = (an.get("weekly") or {}).get("macd") or {}
    monthly_kdj = (an.get("monthly") or {}).get("kdj") or {}
    monthly_macd = (an.get("monthly") or {}).get("macd") or {}
    return {
        "daily_j": daily_kdj.get("j"),
        "daily_kdj_golden_cross": daily_kdj.get("golden_cross"),
        "daily_kdj_death_cross": daily_kdj.get("death_cross"),
        "daily_kdj_state": daily_kdj.get("state"),
        "daily_macd_hist": daily_macd.get("hist"),
        "daily_macd_hist_direction": daily_macd.get("hist_direction"),
        "daily_macd_golden_cross": daily_macd.get("golden_cross"),
        "daily_macd_death_cross": daily_macd.get("death_cross"),
        "weekly_j": weekly_kdj.get("j"),
        "weekly_kdj_state": weekly_kdj.get("state"),
        "weekly_macd_hist": weekly_macd.get("hist"),
        "weekly_macd_hist_direction": weekly_macd.get("hist_direction"),
        "monthly_j": monthly_kdj.get("j"),
        "monthly_kdj_state": monthly_kdj.get("state"),
        "monthly_macd_hist": monthly_macd.get("hist"),
        "monthly_macd_hist_direction": monthly_macd.get("hist_direction"),
    }


def _row_from_analysis(it: dict, code: str, an: dict) -> dict:
    if not an.get("available"):
        return {
            **it,
            "code": code,
            "technical_available": False,
            "technical_error": an.get("error"),
        }
    return {
        **it,
        "code": code,
        "technical_available": True,
        **_trend_fields(an),
        **_bbi_fields(an),
        **_n_structure_fields(an),
        **_box_fields(an),
        **_oscillator_fields(an),
        "price_volume": an.get("price_volume") or {"available": False},
    }


def build_summary(
    items: list[dict], date: str, use_subprocess: bool = False
) -> list[dict]:
    """按持仓列表构建技术面 summary 行（同一代码只算一次）。"""
    summary = []
    for it in items:
        code = str(it["code"]).split(".")[0]
        name = it.get("name", "")
        an = _analysis_for(code, name, date, use_subprocess)
        summary.append(_row_from_analysis(it, code, an))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--mapping", default="")
    ap.add_argument(
        "--subprocess",
        action="store_true",
        help="逐股 fork technical_monitor.py（旧路径，进程内计算异常时兜底）",
    )
    a = ap.parse_args(argv)
    mapping = (
        Path(a.mapping)
        if a.mapping
        else HOLD / f"{a.date}_holding_sector_mapping_enriched.json"
    )
    if mapping.exists():
        items = load(mapping, [])
    else:
        from custos.core.code_utils import is_a_share_position  # noqa: PLC0415

        items = [
            pos_to_row(x)
            for x in load(TRADES, [])
            if x.get("代码") and is_a_share_position(x)
        ]
    if not items:
        raise SystemExit("no current holdings or mapping")
    summary = build_summary(items, a.date, use_subprocess=a.subprocess)
    dest = HOLD / f"{a.date}_holding_technical_summary.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # ⚠️ 落盘前校验：11 个消费者，其中 8 处读 latest_date 做陈旧判定。
    require("holding_technical_summary", summary)
    dest.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
