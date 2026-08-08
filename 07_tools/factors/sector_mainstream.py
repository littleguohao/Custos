# -*- coding: utf-8 -*-
"""回测板块族聚合分析——交易/候选的"板块合集"里,哪些是主流(资金共识),哪些是分散?

思路(不硬指派个股唯一板块):每只票带整个 TDX 板块族(多重归属),把回测交易的板块族倒进合集,
按"信号数/胜率/期望"聚合到板块,识别主流细分/概念;再检验**主流板块里的信号是否显著更赚**
(有≥1个主流板块归属的交易 vs 其余),回答"主流共振是否可交易"。

⚠️ 口径:一股多板块重复计(归属次数≠交易数);地区(3)/风格(5)板块默认排除,只看行业/概念/细分。
输入为 trade-sim 的 --out JSON(含逐笔 trades,即不带 --summary-only)。纯分析,绝不 raise。

用法::
    uv run python 07_tools/factors/sector_mainstream.py --trades 06_logs/x.json [--top-k 10]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

FACTOR: dict[str, Any] = {
    "id": "sector_mainstream",
    "name": "主线板块族密度",
    "kind": "state",
    "status": "candidate",
    "evidence": "00_governance/research/R2_selection_price_volume.md",
    "note": "R2：板块族+密度是准确的「窗口主线指纹」（归因工具），但「跟随主流」机械规则不成立",
    "min_bars": 1,
    "live_use": "evidence_only",
    "stage": "release",
}


TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(TOOLS), str(TOOLS / "screening"), str(TOOLS / "local_tdx")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

EXCLUDE_TDX_TYPES = {"3", "5"}     # 地区/风格板块不进合集(江西板块、保险重仓之类无"主线"语义)


def invert_members(members: dict, exclude_types: bool = True, name_map: Optional[dict] = None,
                   norm: Optional[Any] = None) -> dict[str, list[str]]:
    """{sector:[codes]} → code6 → [板块代码]。exclude_types=True 时剔除地区(3)/风格(5)板块。
    norm:code 归一函数(默认取前6位);sector_phase 传 _norm6 以统一口径。"""
    _n = norm or (lambda cc: str(cc)[:6])
    if exclude_types and name_map is None:
        try:
            import tq_sector  # noqa: PLC0415
            name_map = tq_sector.load_sector_names()
        except Exception:  # noqa: BLE001
            name_map = {}
        if not name_map:
            print("[WARN] sector_mainstream: 板块名称表(tdxzs.cfg)不可用,地区/风格剔除已关闭"
                  "——板块族口径随环境漂移", file=sys.stderr)
    code2secs: dict[str, list[str]] = {}
    for sec, codes in members.items():
        if exclude_types and name_map:
            t = name_map.get(str(sec).split(".")[0], {}).get("tdx_type")
            if t in EXCLUDE_TDX_TYPES:
                continue
        for cc in codes:
            code2secs.setdefault(_n(cc), []).append(sec)
    return code2secs


def load_code2secs(members_path, exclude_types: bool = True) -> dict[str, list[str]]:
    """sector_members.json 反转为 code6 → [板块代码](可选剔除地区/风格)。失败返回 {}。"""
    try:
        members = json.loads(Path(members_path).read_text(encoding="utf-8"))
        return invert_members(members, exclude_types=exclude_types)
    except Exception:  # noqa: BLE001
        return {}


def sector_name(sec: str, name_map: Optional[dict] = None) -> str:
    if name_map is None:
        try:
            import tq_sector  # noqa: PLC0415
            name_map = tq_sector.load_sector_names()
        except Exception:  # noqa: BLE001
            name_map = {}
    return name_map.get(str(sec).split(".")[0], {}).get("name", sec)


def aggregate(trades: list[dict], code2secs: dict[str, list[str]], top_k: int = 10) -> dict[str, Any]:
    """交易板块族聚合:板块维度信号数/胜率/期望 + 集中度 + 主流(top_k) vs 分散 对照。"""
    tagged = [(t, code2secs.get(str(t.get("code", ""))[:6], [])) for t in trades]
    classified = sum(1 for _, secs in tagged if secs)
    per_sec: dict[str, list[float]] = {}
    for t, secs in tagged:
        for s in secs:
            per_sec.setdefault(s, []).append(t["ret"])
    rows = []
    for s, rets in per_sec.items():
        wins = [r for r in rets if r > 0]
        rows.append({"sector": s, "n": len(rets), "win_rate": round(len(wins) / len(rets), 3),
                     "expectancy": round(statistics.mean(rets), 4),
                     "total_ret": round(sum(rets), 3)})
    rows.sort(key=lambda r: r["n"], reverse=True)
    total_attr = sum(r["n"] for r in rows)                      # 归属次数(一股多板块重复计)
    top5_share = round(sum(r["n"] for r in rows[:5]) / total_attr, 3) if total_attr else None
    hhi = round(sum((r["n"] / total_attr) ** 2 for r in rows), 4) if total_attr else None

    mainstream = {r["sector"] for r in rows[:top_k]}
    in_main = [t["ret"] for t, secs in tagged if mainstream & set(secs)]
    out_main = [t["ret"] for t, secs in tagged if secs and not (mainstream & set(secs))]

    def _stat(rets: list[float]) -> dict[str, Any]:
        if not rets:
            return {"n": 0}
        wins = [r for r in rets if r > 0]
        return {"n": len(rets), "win_rate": round(len(wins) / len(rets), 3),
                "expectancy": round(statistics.mean(rets), 4),
                "median": round(statistics.median(rets), 4)}

    out = {"n_trades": len(trades), "n_classified": classified, "distinct_sectors": len(rows),
           "attr_total": total_attr, "top5_share": top5_share, "hhi": hhi,
           "top_k": top_k, "mainstream_sectors": sorted(mainstream),
           "in_mainstream": _stat(in_main), "off_mainstream": _stat(out_main),
           "top_sectors": rows[:top_k], "rows": rows,
           "top_by_expectancy": sorted((r for r in rows if r["n"] >= 20),
                                       key=lambda r: r["expectancy"], reverse=True)[:top_k]}
    conc = ("集中" if (top5_share or 0) >= 0.5 else "偏分散" if (top5_share or 0) < 0.3 else "中等")
    im, om = out["in_mainstream"], out["off_mainstream"]
    lift = (im.get("expectancy", 0) - om.get("expectancy", 0)) if im.get("n") and om.get("n") else None
    out["mainstream_lift"] = round(lift, 4) if lift is not None else None
    lines = [f"交易 {len(trades)} 笔(有板块归属 {classified}), 覆盖 {len(rows)} 个板块; "
             f"前5板块占归属次数 {(top5_share or 0)*100:.0f}%({conc}), HHI {hhi}",
             f"主流(top{top_k}信号数)板块内交易: n={im.get('n')} 胜率 {(im.get('win_rate') or 0)*100:.1f}% "
             f"期望 {(im.get('expectancy') or 0)*100:+.2f}%/笔",
             f"分散(其余)交易:           n={om.get('n')} 胜率 {(om.get('win_rate') or 0)*100:.1f}% "
             f"期望 {(om.get('expectancy') or 0)*100:+.2f}%/笔",
             f"主流 vs 分散 期望差: {((lift or 0)*100):+.2f}pp/笔 "
             f"({'主流更赚→跟随资金共识有效' if (lift or 0) > 0.003 else '≈无差/更差→主流不可直接跟随'})",
             "  主流板块: " + "、".join(sorted(mainstream))]
    lines.append("  信号数 Top 板块: " + "; ".join(
        f"{r['sector']}({r['n']}笔,胜率{r['win_rate']*100:.0f}%,期望{r['expectancy']*100:+.2f}%)"
        for r in rows[:6]))
    lines.append("  期望 Top 板块(n≥20): " + ("; ".join(
        f"{r['sector']}({r['n']}笔,{r['expectancy']*100:+.2f}%)" for r in out["top_by_expectancy"][:6]) or "无"))
    out["text"] = "\n".join(lines)
    return out


def sector_sizes(members: dict) -> dict[str, int]:
    """{sector:[codes]} → {sector: 成员数}(用于密度归一)。"""
    return {s: len(v or []) for s, v in (members or {}).items()}


def mainline_fingerprint(codes: list[str], code2secs: dict[str, list[str]],
                         sizes: Optional[dict] = None, top_k: int = 8, min_size: int = 8,
                         name_map: Optional[dict] = None) -> dict[str, Any]:
    """当日候选/交易的板块族**密度榜(主线指纹)**:按密度(命中数/板块规模)排序,过滤过小板块防噪。
    density 归一避免大板块仅因体量占榜首;show 命中数供直觉。纯统计、绝不 raise。"""
    per_sec: dict[str, int] = {}
    for code in codes:
        for s in code2secs.get(str(code)[:6], []):
            per_sec[s] = per_sec.get(s, 0) + 1
    n_cls = sum(1 for c in codes if code2secs.get(str(c)[:6]))
    if not per_sec:
        return {"n": len(codes), "n_classified": 0, "top": [], "text": "无板块映射"}
    total_attr = sum(per_sec.values())
    rows = []
    for s, n in per_sec.items():
        sz = (sizes or {}).get(s, 0)
        if sz and sz < min_size:
            continue                                   # 过小板块(如3只)密度虚高→过滤
        rows.append({"sector": s, "name": sector_name(s, name_map), "n": n, "size": sz,
                     "density": (round(n / sz, 4) if sz else None), "share": round(n / total_attr, 4)})
    rows.sort(key=lambda r: (r["density"] if r["density"] is not None else r["n"] / 1e9, r["n"]),
              reverse=True)
    top = rows[:top_k]
    top5c = sorted(rows, key=lambda x: x["n"], reverse=True)[:5]
    top5_share = round(sum(r["n"] for r in top5c) / total_attr, 3) if total_attr else None
    txt = ("主线指纹(密度榜): " + "; ".join(
        f"{r['name']}({r['n']}只{('/'+str(r['size'])) if r['size'] else ''})" for r in top[:6])) if top else "无"
    return {"n": len(codes), "n_classified": n_cls, "distinct_sectors": len(rows),
            "top5_count_share": top5_share, "top": top, "text": txt}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="回测交易的板块族聚合:主流 vs 分散")
    ap.add_argument("--trades", required=True, help="trade-sim --out JSON(含逐笔 trades)")
    ap.add_argument("--sector-members",
                    default=str(TOOLS.parent / "01_data" / "market" / "sector_members.json"))
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    try:
        payload = json.loads(Path(args.trades).read_text(encoding="utf-8"))
        trades = payload.get("trades") or []
        if not trades:
            print("[ERR] JSON 中无逐笔 trades(是否用了 --summary-only?)", file=sys.stderr)
            return 1
        code2secs = load_code2secs(args.sector_members)
        res = aggregate(trades, code2secs, top_k=args.top_k)
        # 附上板块中文名便于阅读
        for r in res["top_sectors"] + res["top_by_expectancy"]:
            r["name"] = sector_name(r["sector"])
        res["mainstream_named"] = {s: sector_name(s) for s in res["mainstream_sectors"]}
        print("\n=== 板块族聚合(主流 vs 分散) ===")
        print(res["text"])
        if args.out:
            Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] 写出 {args.out}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
