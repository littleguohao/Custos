# -*- coding: utf-8 -*-
"""A50 Wind 固定指数与旧诊断源的历史对账（只读，不进 live）。

Wind 使用固定 EDB 代码 L5471360；旧诊断源使用自然语言接口，仅作历史参照，
不得作为 live 数据主源。旧 Yahoo CFF=A50/XIN9.FGI 的可用性也单独记录，
不把旧诊断源结果伪装成 Yahoo 期货结果。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

from custos.core.paths import ARTIFACT_LOGS, cn_now, cn_today
from custos.datasource import kimiwork_data as kd


def _number(text: Any) -> float:
    import re

    match = re.search(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", str(text))
    if not match:
        raise ValueError(f"无法解析 A50 数值: {text!r}")
    return float(match.group(0))


def _day(text: Any) -> str:
    value = str(text)
    if len(value) >= 8 and value[:8].isdigit() and "-" not in value[:8]:
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value[:10]


def wind_history(observation: int) -> list[dict[str, Any]]:
    raw = kd.call_api(
        "wind",
        "wind_query_economic_indicator_data",
        {
            "question": "L5471360",
            "observation": str(observation),
            "file_path": str(ARTIFACT_LOGS / "a50_proxy_shadow" / "wind.csv"),
        },
        timeout=180,
    )
    header, raw_rows = kd.csv_table(raw)
    return [
        {"date": _day(row["date"]), "close": _number(row["value"])}
        for row in (dict(zip(header, item)) for item in raw_rows)
    ]


def diagnostic_history(observation: int) -> list[dict[str, Any]]:
    raw = kd.call_api(
        "dongcai",
        "dongcai_mx_finance_data",
        {
            "query": f"富时中国A50期货最近{observation}个交易日收盘价，按日期升序",
            "indicators": "收盘价",
            "file_path": str(ARTIFACT_LOGS / "a50_proxy_shadow" / "diagnostic.csv"),
        },
        timeout=180,
    )
    header, raw_rows = kd.csv_table(raw)
    if "headName" not in header:
        raise ValueError("旧诊断源缺少日期表头")
    value_fields = [name for name in header if name != "headName"]
    if len(value_fields) != 1:
        raise ValueError(f"旧诊断源收盘字段不唯一: {value_fields}")
    value_field = value_fields[0]
    return [
        {"date": _day(row["headName"]), "close": _number(row[value_field])}
        for row in (dict(zip(header, item)) for item in raw_rows)
    ]


def _returns(rows: list[dict[str, Any]]) -> dict[str, float]:
    ordered = sorted(rows, key=lambda x: x["date"])
    return {
        ordered[i]["date"]: (ordered[i]["close"] / ordered[i - 1]["close"] - 1) * 100
        for i in range(1, len(ordered))
        if ordered[i - 1]["close"]
    }


def compare(
    wind: list[dict[str, Any]], diagnostic: list[dict[str, Any]]
) -> dict[str, Any]:
    w = {x["date"]: x["close"] for x in wind}
    d = {x["date"]: x["close"] for x in diagnostic}
    common = sorted(set(w) & set(d))
    if not common:
        raise ValueError("两源没有共同交易日期")
    ratios = [w[day] / d[day] for day in common]
    wr, dr = _returns(wind), _returns(diagnostic)
    return_days = sorted(set(wr) & set(dr))
    return_diffs = [wr[day] - dr[day] for day in return_days]
    if len(return_diffs) > 1:
        mean_x = statistics.mean(wr[day] for day in return_days)
        mean_y = statistics.mean(dr[day] for day in return_days)
        cov = statistics.mean(
            (wr[day] - mean_x) * (dr[day] - mean_y) for day in return_days
        )
        sx = math.sqrt(statistics.mean((wr[day] - mean_x) ** 2 for day in return_days))
        sy = math.sqrt(statistics.mean((dr[day] - mean_y) ** 2 for day in return_days))
        corr = cov / (sx * sy) if sx and sy else None
    else:
        corr = None
    return {
        "wind_rows": len(wind),
        "diagnostic_rows": len(diagnostic),
        "only_wind_dates": sorted(set(w) - set(d)),
        "only_diagnostic_dates": sorted(set(d) - set(w)),
        "common_days": len(common),
        "date_start": common[0],
        "date_end": common[-1],
        "ratio_min": min(ratios),
        "ratio_max": max(ratios),
        "ratio_mean": statistics.mean(ratios),
        "ratio_std": statistics.pstdev(ratios) if len(ratios) > 1 else 0.0,
        "exact_close_days": sum(w[day] == d[day] for day in common),
        "return_pairs": len(return_days),
        "return_exact_days": sum(x == 0 for x in return_diffs),
        "return_sign_agreement": sum(
            (wr[day] >= 0) == (dr[day] >= 0) for day in return_days
        ),
        "return_mae_pp": statistics.mean(abs(x) for x in return_diffs)
        if return_diffs
        else None,
        "return_max_abs_pp": max((abs(x) for x in return_diffs), default=None),
        "return_correlation": corr,
        "note": "旧诊断源不是 Yahoo；旧 Yahoo 当前无法提供历史序列，故本结果只能评估与旧诊断源的同口径关系。",
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=cn_today().isoformat())
    parser.add_argument("--observation", type=int, default=20)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if args.observation < 2:
        parser.error("--observation 至少为 2")
    wind = wind_history(args.observation)
    diagnostic = diagnostic_history(args.observation)
    report = {
        "date": args.date,
        "collected_at": cn_now().isoformat(),
        "wind_source": "Wind EDB L5471360（富时中国A50指数，非期货代理）",
        "diagnostic_source": "旧东财自然语言接口（仅诊断，非 live）",
        "yahoo_history": {
            "status": "unavailable",
            "detail": "CFF=A50 返回 404；XIN9.FGI 仅返回当前值且无历史序列",
        },
        "sample_limitations": [
            "本工具只比较 Wind 固定指数与旧东财诊断源，不是 Yahoo 期货长历史验证。",
            "本次 20 日请求仅取到有限共同交易日，结论不得外推为长期无偏差。",
            "更长自然语言窗口曾返回稀疏且错位序列，未纳入证据。",
        ],
        **compare(wind, diagnostic),
    }
    out = (
        Path(args.out)
        if args.out
        else ARTIFACT_LOGS / "a50_proxy_shadow" / f"{args.date}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "common_days",
                    "exact_close_days",
                    "return_pairs",
                    "return_exact_days",
                    "return_mae_pp",
                    "return_max_abs_pp",
                    "return_correlation",
                )
            },
            ensure_ascii=False,
        )
    )
    print(f"a50 proxy diff -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
