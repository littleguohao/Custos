# -*- coding: utf-8 -*-
"""sector_mainstream(板块族聚合:主流 vs 分散)测试。"""
import json

from custos.core.factors import sector_mainstream as sm


def _members(tmp_path):
    members = {"880201.SH": ["600000", "600001"], "880548.SH": ["600000", "600003"],
               "880222.SH": ["600000"]}                      # 880222=地区,应被剔除
    p = tmp_path / "members.json"
    p.write_text(json.dumps(members), encoding="utf-8")
    return p


def _trade(code, ret):
    return {"code": code, "ret": ret, "entry_date": "2025-01-02", "exit_date": "2025-01-10"}


def test_load_code2secs_inverts_and_excludes_region(tmp_path, monkeypatch):
    from custos.datasource.local_tdx import tq_sector
    monkeypatch.setattr(tq_sector, "load_sector_names",
                        lambda path=None: {"880222": {"name": "江西板块", "tdx_type": "3"},
                                           "880201": {"name": "白酒", "tdx_type": "4"},
                                           "880548": {"name": "商业航天", "tdx_type": "4"}})
    c2s = sm.load_code2secs(_members(tmp_path))
    assert sorted(c2s["600000"]) == ["880201.SH", "880548.SH"]   # 地区 880222 被剔除
    assert c2s["600001"] == ["880201.SH"]


def test_aggregate_mainstream_vs_off():
    code2secs = {"600000": ["A", "B"], "600001": ["A"], "600002": ["A"], "600003": ["Z"]}
    trades = [_trade("600000", 0.10), _trade("600001", 0.06),
              _trade("600002", -0.02), _trade("600003", -0.05)]
    r = sm.aggregate(trades, code2secs, top_k=2)
    assert r["n_classified"] == 4 and r["distinct_sectors"] == 3
    assert set(r["mainstream_sectors"]) == {"A", "B"}            # A(3次)+B(1次) 为 top2
    assert r["in_mainstream"]["n"] == 3 and abs(r["in_mainstream"]["expectancy"] - 0.0467) < 1e-3
    assert r["off_mainstream"]["n"] == 1 and r["off_mainstream"]["expectancy"] == -0.05
    assert r["mainstream_lift"] > 0                              # 主流显著更赚
    assert "主流" in r["text"]


def test_aggregate_unclassified_excluded_from_off():
    # 无板块归属的交易:既不算主流也不算"分散"(off 只统计有归属但不在主流的)
    r = sm.aggregate([_trade("600000", 0.05), _trade("999999", -0.09)], {"600000": ["A"]}, top_k=1)
    assert r["n_classified"] == 1 and r["in_mainstream"]["n"] == 1 and r["off_mainstream"]["n"] == 0


def test_invert_members_norm_and_exclude():
    from custos.core.factors import sector_mainstream as sm
    members = {"880201.SH": ["600000", "600001"], "880300.SH": ["600000"], "地区X.SH": ["600000"]}
    name_map = {"880201": {"tdx_type": "2", "name": "锂电"}, "880300": {"tdx_type": "4", "name": "细分"},
                "地区X": {"tdx_type": "3", "name": "江西板块"}}   # 3=地区,应剔除
    c2s = sm.invert_members(members, exclude_types=True, name_map=name_map, norm=lambda cc: str(cc)[:6])
    assert "地区X.SH" not in c2s.get("600000", [])          # 地区板块被剔除
    assert set(c2s["600000"]) == {"880201.SH", "880300.SH"}


def test_mainline_fingerprint_density():
    from custos.core.factors import sector_mainstream as sm
    # 板块A规模10(候选4→密度0.4);板块B规模100(候选5→密度0.05);板块C规模3(候选3→过滤:太小)
    members = {"A": ["A%d" % i for i in range(10)], "B": ["B%d" % i for i in range(100)],
               "C": ["C0", "C1", "C2"]}
    code2secs = {"A0": ["A"], "A1": ["A"], "A2": ["A"], "A3": ["A"],
                 "B0": ["B"], "B1": ["B"], "B2": ["B"], "B3": ["B"], "B4": ["B"],
                 "C0": ["C"], "C1": ["C"], "C2": ["C"]}
    codes = ["A0", "A1", "A2", "A3", "B0", "B1", "B2", "B3", "B4", "C0", "C1", "C2"]
    fp = sm.mainline_fingerprint(codes, code2secs, sizes=sm.sector_sizes(members), top_k=5, min_size=8)
    secs = [r["sector"] for r in fp["top"]]
    assert secs[0] == "A"                 # 密度归一:A(0.4) > B(0.05)
    assert "C" not in secs                # 过小板块被过滤
    assert fp["n"] == 12 and fp["n_classified"] == 12
