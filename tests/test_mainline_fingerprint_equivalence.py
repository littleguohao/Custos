# -*- coding: utf-8 -*-
"""mainline_fingerprint 拆分+性能优化(C19)的逐位等价钉测。

将 v0.72 之前的原始实现原样拷贝为 _reference_mainline_fingerprint,
对多组合成输入逐字段(含键序、text 文案)比对拆分后的实现。
钉住的两处优化:
1. 单趟统计(per_sec 计数与 n_classified 合并为一趟循环);
2. name_map=None 时板块名称表只在首个幸存行加载一次(hoist 出逐板块循环,
   原本每个板块都重复读盘解析 tdxzs.cfg)。
"""

import random

from custos.core.factors import sector_mainstream as sm


def _reference_mainline_fingerprint(
    codes,
    code2secs,
    sizes=None,
    top_k=8,
    min_size=8,
    name_map=None,
    sort_by="density",
):
    """拆分前 mainline_fingerprint 的原始实现(逐字拷贝,仅改名)。"""
    per_sec: dict = {}
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
            continue  # 过小板块(如3只)密度虚高→过滤
        rows.append(
            {
                "sector": s,
                "name": sm.sector_name(s, name_map),
                "n": n,
                "size": sz,
                "density": (round(n / sz, 4) if sz else None),
                "share": round(n / total_attr, 4),
            }
        )
    if sort_by == "n":
        rows.sort(key=lambda r: (r["n"], r["density"] or 0), reverse=True)
    else:
        rows.sort(
            key=lambda r: (
                r["density"] if r["density"] is not None else r["n"] / 1e9,
                r["n"],
            ),
            reverse=True,
        )
    top = rows[:top_k]
    top5c = sorted(rows, key=lambda x: x["n"], reverse=True)[:5]
    top5_share = (
        round(sum(r["n"] for r in top5c) / total_attr, 3) if total_attr else None
    )
    txt = (
        (
            "主线指纹(密度榜): "
            + "; ".join(
                f"{r['name']}({r['n']}只{('/' + str(r['size'])) if r['size'] else ''})"
                for r in top[:6]
            )
        )
        if top
        else "无"
    )
    return {
        "n": len(codes),
        "n_classified": n_cls,
        "distinct_sectors": len(rows),
        "top5_count_share": top5_share,
        "top": top,
        "text": txt,
    }


def _synthetic(seed, n_codes=60, n_secs=25):
    rng = random.Random(seed)
    secs = [f"880{i:03d}.SH" for i in range(n_secs)]
    code2secs = {}
    for i in range(n_codes):
        k = rng.randint(0, 4)
        code2secs[f"{600000 + i}"] = rng.sample(secs, k)
    codes = [f"{600000 + i}" for i in range(n_codes)]
    codes += ["999999"] * rng.randint(0, 3)  # 无归属票
    rng.shuffle(codes)
    sizes = {s: rng.choice([0, 3, 8, 50, 200]) for s in secs}
    name_map = {s.split(".")[0]: {"name": f"板块{s}", "tdx_type": "2"} for s in secs}
    return codes, code2secs, sizes, name_map


def _assert_bit_equal(ref, new):
    assert list(ref.keys()) == list(new.keys())  # 键序一致
    assert ref == new


def test_equivalence_density_and_n_sort():
    for seed in range(6):
        codes, code2secs, sizes, name_map = _synthetic(seed)
        for sort_by in ("density", "n"):
            for sizes_arg in (sizes, None):
                kw = dict(
                    sizes=sizes_arg,
                    top_k=5,
                    min_size=8,
                    name_map=name_map,
                    sort_by=sort_by,
                )
                ref = _reference_mainline_fingerprint(codes, code2secs, **kw)
                new = sm.mainline_fingerprint(codes, code2secs, **kw)
                _assert_bit_equal(ref, new)


def test_equivalence_edge_cases():
    # 空候选 / 全无归属 → early return
    for codes, c2s in (
        ([], {}),
        (["999999", "888888"], {"999999": []}),
    ):
        ref = _reference_mainline_fingerprint(codes, c2s)
        new = sm.mainline_fingerprint(codes, c2s)
        _assert_bit_equal(ref, new)
    # 全部板块被 min_size 过滤 → top 为空,text="无"
    codes, code2secs, sizes, name_map = _synthetic(99)
    tiny = {s: 2 for s in sizes}
    kw = dict(sizes=tiny, min_size=8, name_map=name_map)
    ref = _reference_mainline_fingerprint(codes, code2secs, **kw)
    new = sm.mainline_fingerprint(codes, code2secs, **kw)
    _assert_bit_equal(ref, new)


def test_equivalence_name_map_lazy_load(monkeypatch):
    """name_map=None:名称表经 tq_sector.load_sector_names 懒加载(monkeypatch 通道不变)。"""
    from custos.datasource.local_tdx import tq_sector

    codes, code2secs, sizes, _ = _synthetic(7)
    table = {s.split(".")[0]: {"name": f"懒加载{s}", "tdx_type": "2"} for s in sizes}
    monkeypatch.setattr(tq_sector, "load_sector_names", lambda path=None: table)
    kw = dict(sizes=sizes, name_map=None)
    ref = _reference_mainline_fingerprint(codes, code2secs, **kw)
    new = sm.mainline_fingerprint(codes, code2secs, **kw)
    _assert_bit_equal(ref, new)
    assert any(r["name"].startswith("懒加载") for r in new["top"])
