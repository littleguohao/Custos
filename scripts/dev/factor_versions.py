# -*- coding: utf-8 -*-
"""因子版本表维护（``governance/contracts/FACTOR_VERSIONS.json`` 唯一写入口）。

    uv run python scripts/dev/factor_versions.py --check
    uv run python scripts/dev/factor_versions.py --bump macd_technics --reason "小绿分三类（owner 2026-11-xx 标注 #12/#31）"
    uv run python scripts/dev/factor_versions.py --refactor ignition --reason "拆函数，判定逐位不变（钉测 test_x）"
    uv run python scripts/dev/factor_versions.py --holdout-use macd_technics --window 2026-09-05~2026-11-03 --reason "用 1800 结果标注校准"
    uv run python scripts/dev/factor_versions.py --init      # 仅首次：全部因子登记为 v1

--bump：判定语义变了（版本 +1，记 history）；--refactor：纯重构（版本不变，只刷新摘要，记原因）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from custos.core import factor_versions as fv


def _save(d: dict) -> None:
    fv.VERSIONS_FILE.write_text(
        json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check() -> list[str]:
    d = fv.load().get("factors") or {}
    files = fv.tracked_files()
    bad = [f"未登记: {k}" for k in sorted(set(files) - set(d))]
    bad += [f"已删除仍在表: {k}" for k in sorted(set(d) - set(files))]
    for k in sorted(set(files) & set(d)):
        if d[k].get("source_digest") != fv.source_digest(files[k]):
            bad.append(
                f"源码变了但版本未处理: {k}（v{d[k].get('version')}）——语义变 --bump / 纯重构 --refactor"
            )
    return bad


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="因子版本表维护")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--init", action="store_true")
    g.add_argument("--bump", metavar="FACTOR")
    g.add_argument("--refactor", metavar="FACTOR")
    g.add_argument("--holdout-use", metavar="FACTOR")
    ap.add_argument("--reason", default="")
    ap.add_argument("--window", default="")
    a = ap.parse_args(argv)
    today = dt.date.today().isoformat()
    if a.check:
        bad = check()
        for b in bad:
            print(f"[BAD] {b}")
        print(f"[factor-versions] {len(fv.tracked_files())} 个因子，问题 {len(bad)} 个")
        return 1 if bad else 0
    d = fv.load() or {"schema": "factor_versions/v1"}
    d.setdefault("factors", {})
    d.setdefault("calibration_holdout_use", [])
    files = fv.tracked_files()
    if a.init:
        for k, p in files.items():
            d["factors"].setdefault(
                k,
                {
                    "version": 1,
                    "source_digest": fv.source_digest(p),
                    "history": [
                        {
                            "version": 1,
                            "date": today,
                            "change": "初始版本（定义校准前基线，TODO #121）",
                        }
                    ],
                },
            )
        _save(d)
        print(f"[factor-versions] 登记 {len(d['factors'])} 个因子")
        return 0
    if not a.reason:
        print("[ERR] 需要 --reason", file=sys.stderr)
        return 2
    name = a.bump or a.refactor or a.holdout_use
    if name not in files:
        print(f"[ERR] 未知因子 {name}", file=sys.stderr)
        return 2
    if a.holdout_use:
        if not a.window:
            print("[ERR] --holdout-use 需要 --window", file=sys.stderr)
            return 2
        ent = d["factors"][name]
        d["calibration_holdout_use"].append(
            {
                "factor": name,
                "version": ent["version"],
                "window": a.window,
                "date": today,
                "reason": a.reason,
            }
        )
        _save(d)
        print(
            f"[factor-versions] 记 holdout 校准使用：{name} v{ent['version']} {a.window}"
        )
        return 0
    ent = d["factors"].setdefault(name, {"version": 0, "history": []})
    ent["source_digest"] = fv.source_digest(files[name])
    if a.bump:
        ent["version"] = int(ent.get("version", 0)) + 1
        ent["history"].append(
            {"version": ent["version"], "date": today, "change": a.reason}
        )
    else:
        ent["history"].append(
            {"version": ent["version"], "date": today, "refactor": a.reason}
        )
    _save(d)
    print(
        f"[factor-versions] {name} → v{ent['version']}（{'bump' if a.bump else 'refactor'}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
