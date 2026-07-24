# -*- coding: utf-8 -*-
"""三项后续测试：② 资金流多日累计 ① 因子分位 lift/流动性 ③ 财务代理逻辑。"""
import json

import pandas as pd

from screening import enrich_candidates as ec
from screening import backtest_factors as bt
from screening import financials as fin


# ---------- ② 资金流多日累计 ----------

def _write_ff(mdir, date, stocks, sectors=None):
    payload = {"date": date, "stock_rank": stocks, "sector_rank": {"concept": sectors or [], "industry": []}}
    (mdir / f"{date}_fund_flow_rank.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_fund_flow_cumulative_sums(tmp_path):
    for d, inflow in [("2026-07-20", 1e8), ("2026-07-21", 2e8), ("2026-07-22", -0.5e8)]:
        _write_ff(tmp_path, d, [{"code": "600000", "name": "浦发", "main_net_inflow": inflow, "main_net_pct": 1.0}],
                  sectors=[{"name": "银行", "main_net_inflow": inflow}])
    r = ec.load_fund_flow("2026-07-22", cumulative_days=3, market_dir=tmp_path)
    assert r["available"] and r["cumulative_days"] == 3
    e = r["by_code"]["600000"]
    assert abs(e["main_net_inflow"] - 2.5e8) < 1 and e["days"] == 3   # 1+2-0.5 亿
    assert e["main_net_pct"] is None                                   # 多日累计不报日内占比
    assert len(r["files_used"]) == 3
    # 板块累计
    sec = {s["name"]: s for s in r["sectors"]}
    assert abs(sec["银行"]["main_net_inflow"] - 2.5e8) < 1


def test_fund_flow_single_day_unchanged(tmp_path):
    _write_ff(tmp_path, "2026-07-22", [{"code": "600000", "main_net_inflow": 3e8, "main_net_pct": 2.0}])
    r = ec.load_fund_flow("2026-07-22", cumulative_days=1, market_dir=tmp_path)
    assert r["by_code"]["600000"]["main_net_inflow"] == 3e8 and r["files_used"] == ["2026-07-22"]
    assert r["by_code"]["600000"]["main_net_pct"] == 2.0             # 单日保留占比


# ---------- ① 因子分位 lift + 流动性 ----------

def test_liquidity_yi_helper():
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=25, freq="B"),
                       "open": [10.0] * 25, "high": [10.1] * 25, "low": [9.9] * 25,
                       "close": [10.0] * 25, "volume": [1000.0] * 25, "amount": [1.5e8] * 25})
    assert abs(bt._liquidity_yi(df) - 1.5) < 1e-6


def test_factor_lift_quantiles():
    # 构造：高字段值 → 高前向收益（正向 lift），验证分位分组
    recs = []
    for i in range(40):
        recs.append({"c_liquidity": float(i), "ret10": (i - 20) * 0.001,
                     "mfe10": 0.05, "mae10": -0.05})
    r = bt.factor_lift(recs, "c_liquidity", horizon=10, quantiles=4)
    q = r["quantiles"]
    assert len(q) == 4
    assert q[-1]["avg_return"] > q[0]["avg_return"]   # 高分位收益更高
    assert "text" in r


def test_factor_lift_insufficient():
    r = bt.factor_lift([{"c_liquidity": 1.0, "ret10": 0.01}], "c_liquidity", horizon=10)
    assert "样本不足" in r.get("note", "") or r.get("n", 0) < 20


# ---------- ③ 财务代理逻辑（注入数据） ----------

_FIN_DF = pd.DataFrame([
    {"c_code": "600000", "c_np": 5e8, "c_npyoy": 150.0, "c_ocf": 3e8, "c_roe": 12.0, "c_rev": 2e9, "c_shares": 1e9},
    {"c_code": "000002", "c_np": -1e8, "c_npyoy": -30.0, "c_ocf": -2e8, "c_roe": -5.0, "c_rev": 1e9, "c_shares": 5e8},
])
_COLMAP = {"code": "c_code", "net_profit": "c_np", "op_cashflow": "c_ocf",
           "net_profit_yoy": "c_npyoy", "roe": "c_roe", "revenue": "c_rev", "total_shares": "c_shares"}


def test_financial_factor_dixi_hit():
    r = fin.financial_factor("600000", _FIN_DF, _COLMAP, price=10.0)
    assert r["available"] and r["cashflow_available"] is True
    assert r["dixi_proxy"]["perf_surge_ge_100"] is True      # 净利同比150%≥100
    assert r["dixi_proxy"]["net_profit_positive"] is True
    assert r["dixi_proxy"]["op_cashflow_positive"] is True
    assert r["dixi_proxy"]["real_earnings_cashflow"] is True  # 净利>0 且 现金流>0
    assert r["dixi_proxy"]["roe_positive"] is True
    assert abs(r["market_cap"] - 1e10) < 1                   # 10亿股 × 10元
    assert r["market_cap_yi"] == 100.0                       # 亿元便于展示
    assert set(r["hits"]) == {"perf_surge_ge_100", "net_profit_positive",
                              "op_cashflow_positive", "real_earnings_cashflow", "roe_positive"}


def test_financial_factor_weak_stock():
    r = fin.financial_factor("000002", _FIN_DF, _COLMAP, price=10.0)
    assert r["available"] and r["hits"] == []               # 亏损+现金流负+ROE负 → 全不命中


def test_financial_factor_cashflow_missing_degrades():
    # 复现 2026Q1：现金流量表未入(op_cashflow=null)，净利/同比/ROE 有值 → 优雅降级
    df = pd.DataFrame([{"c_code": "600000", "c_np": 1.8e10, "c_ocf": None,
                        "c_npyoy": 120.0, "c_roe": 8.0}])
    cm = {"code": "c_code", "net_profit": "c_np", "op_cashflow": "c_ocf",
          "net_profit_yoy": "c_npyoy", "roe": "c_roe"}
    r = fin.financial_factor("600000", df, cm)
    assert r["available"] and r["cashflow_available"] is False
    assert r["dixi_proxy"]["net_profit_positive"] is True
    assert r["dixi_proxy"]["op_cashflow_positive"] is None       # 现金流缺失→未确认(非 False)
    assert r["dixi_proxy"]["real_earnings_cashflow"] is False     # 不冒充成立
    assert r["dixi_proxy"]["perf_surge_ge_100"] is True and r["dixi_proxy"]["roe_positive"] is True
    assert "net_profit_positive" in r["hits"] and "real_earnings_cashflow" not in r["hits"]
    assert "op_cashflow_positive" not in r["hits"]               # None 不计入命中


def test_financial_factor_degrades():
    assert fin.financial_factor("600000", None, _COLMAP, price=10.0)["available"] is False              # 无财务数据(本机)
    assert fin.financial_factor("600000", _FIN_DF, {}, price=10.0)["available"] is False           # 无colmap
    assert fin.financial_factor("600000", _FIN_DF, {"code": "c_code"}, price=10.0)["available"] is False  # 必需列缺
    assert fin.financial_factor("999999", _FIN_DF, _COLMAP)["available"] is False                  # 代码不在表


# ---------- ③ 财务自动列映射 + 行索引定位 ----------

def test_auto_colmap_matches_chinese_columns():
    cols = ["证券代码", "report_date", "归属于母公司股东的净利润", "净利润同比增长率",
            "营业总收入", "营业收入同比增长率", "经营活动产生的现金流量净额",
            "净资产收益率(加权)", "总股本", "流通股本", "基本每股收益"]
    cm = fin.auto_colmap(cols)
    assert cm["code"] == "证券代码"
    assert cm["net_profit"] == "归属于母公司股东的净利润"
    assert cm["net_profit_yoy"] == "净利润同比增长率"
    assert cm["revenue"] == "营业总收入"
    assert cm["revenue_yoy"] == "营业收入同比增长率"
    assert cm["op_cashflow"] == "经营活动产生的现金流量净额"
    assert cm["roe"] == "净资产收益率(加权)"
    assert cm["total_shares"] == "总股本"   # 不是流通股本


def test_auto_colmap_index_fallback():
    cm = fin.auto_colmap(["净利润", "经营活动产生的现金流量净额"])
    assert cm["code"] == "__index__"   # 无代码列 → 用行索引


def test_financial_factor_index_lookup():
    df = pd.DataFrame({"净利润": [5e8, -1e8],
                       "经营活动产生的现金流量净额": [3e8, -2e8]},
                      index=["600000", "000002"])
    cm = {"code": "__index__", "net_profit": "净利润", "op_cashflow": "经营活动产生的现金流量净额"}
    r = fin.financial_factor("600000", df, cm)
    assert r["available"] and r["dixi_proxy"]["real_earnings_cashflow"] is True
    r2 = fin.financial_factor("000002", df, cm)
    assert r2["available"] and r2["dixi_proxy"]["real_earnings_cashflow"] is False


def test_auto_colmap_revenue_skips_ratio_column():
    # 复现 --inspect 反馈：revenue 不应匹配到 "EBITDA/营业总收入(%)" 比率列
    cols = ["EBITDA/营业总收入(%)", "营业总收入(万元)", "营业收入增长率(%)", "扣非净利润同比(%)"]
    cm = fin.auto_colmap(cols)
    assert cm["revenue"] == "营业总收入(万元)"        # 金额列，非比率
    assert cm["revenue_yoy"] == "营业收入增长率(%)"
    assert cm["net_profit_yoy"] == "扣非净利润同比(%)"  # 扣非同比(用户确认可用)


# ---------- 回归：财务 auto_map 不得覆盖候选合并字典 merged（变量冲突 bug） ----------

def test_enrich_financials_autotmap_does_not_clobber_candidates(monkeypatch):
    import pandas as pd
    from screening import enrich_candidates as ec

    date = "2026-07-23"
    # 注入 OHLCV：61 根、last_date==date（通过 no_today_bar / list_days 门槛）
    ohlcv = pd.DataFrame({
        "date": [f"2026-0{m}-{d:02d}" for m, d in [(5, i) for i in range(1, 29)]
                 + [(6, i) for i in range(1, 29)] + [(7, i) for i in range(20, 25)]][:61],
    })
    ohlcv["date"] = list(pd.date_range("2026-04-24", periods=61, freq="B").astype(str))
    ohlcv.loc[ohlcv.index[-1], "date"] = date  # 保证最后一根就是 date
    for c in ("open", "high", "low", "close", "volume", "amount"):
        ohlcv[c] = 10.0

    # 注入财务：以 code 作行索引，含中文财务列（auto_colmap 应识别）
    fin_df = pd.DataFrame(
        {"净利润": [5e8], "经营活动产生的现金流量净额": [3e8],
         "营业总收入(万元)": [1e9], "加权净资产收益率": [10.0]},
        index=["600000"])

    monkeypatch.setattr(ec.financials_mod, "load_financials", lambda rp="": fin_df)
    monkeypatch.setattr(ec, "compute_metrics", lambda df, idx, code=None: {"close": 10.0, "daily_j": 5.0})
    monkeypatch.setattr(ec, "build_stock_theme_map", lambda min_match=None: ({}, True))

    hits = {"date": date, "status": "ok",
            "formulas": [{"id": "F1", "hits": [{"code": "600000", "name": "浦发银行"}]}]}
    res = ec.enrich(
        date, hits_data=hits,
        ohlcv_loader=lambda c: ohlcv.copy(), index_loader=lambda: None,
        universe_cfg={"j_low_required": False, "min_list_days": 60},
        financials_cfg={"enabled": True, "auto_map": True, "columns": {"net_profit": "净利润"}},
    )
    # merged 未被覆盖 → 候选仍产出（bug 时循环会遍历 colmap 键并 TypeError）
    assert len(res["candidates"]) == 1
    cand = res["candidates"][0]
    assert cand["code"] == "600000" and "fund_flow" in cand
    # 财务已挂载且命中
    fin = cand.get("financials") or {}
    assert fin.get("available") is True
    assert fin["dixi_proxy"]["net_profit_positive"] is True
    assert fin["dixi_proxy"]["real_earnings_cashflow"] is True   # 注入了现金流>0
    assert "net_profit_positive" in fin["hits"]


# ---------- 完美B1 买弱指纹检测器 + 回测盈亏比 ----------

def _synth_uptrend_pullback():
    """合成：先涨~50%，再缩量小实体回踩~10%(收盘落到 MA5/MA10 下方、仍在 MA60 上方)。"""
    closes = [10.0 + 5.0 * i / 61 for i in range(62)]        # 10 → 15
    top = closes[-1]
    closes += [top * (1 - 0.10 * i / 8) for i in range(1, 9)]  # 15 → 13.5，8日回调
    n = len(closes)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    rows = []
    for i, cl in enumerate(closes):
        op = closes[i - 1] if i > 0 else cl
        rows.append({"date": dates[i], "open": op, "high": max(op, cl) * 1.005,
                     "low": min(op, cl) * 0.995, "close": cl,
                     "volume": 1_000_000 if i < 62 else 600_000, "amount": 1.0})
    return pd.DataFrame(rows)


def test_b1_pullback_fit_recognizes_fingerprint():
    from screening import enrich_candidates as ec
    r = ec.compute_b1_pullback_fit(_synth_uptrend_pullback())
    assert r["available"] and r["hit"] is True and r["score"] >= 6
    comp = r["components"]
    assert comp["trend_intact"] and comp["pullback_below_ma10"] and comp["volume_dryup"]


def test_b1_pullback_fit_rejects_downtrend():
    from screening import enrich_candidates as ec
    closes = [20.0 - 10.0 * i / 69 for i in range(70)]        # 单边下跌
    dates = pd.date_range("2025-01-01", periods=70, freq="B")
    df = pd.DataFrame([{"date": dates[i], "open": c, "high": c * 1.01, "low": c * 0.99,
                        "close": c, "volume": 1e6, "amount": 1.0} for i, c in enumerate(closes)])
    r = ec.compute_b1_pullback_fit(df)
    assert r["available"] and r["hit"] is False        # 趋势破 + 无前涨幅 → 不命中


def test_b1_pullback_scorer_registered():
    assert "b1_pullback" in bt.SCORERS
    out = bt.SCORERS["b1_pullback"](_synth_uptrend_pullback(), "TEST")
    assert out is not None and out["suggestion"] == "可买" and out["aux"]["hit"] is True


def test_stats_payoff_ratio():
    rows = [{"ret10": 0.10}, {"ret10": 0.05}, {"ret10": -0.02}, {"ret10": -0.03}]
    s = bt._stats(rows, 10)
    assert s["payoff_ratio"] == 3.0        # 均盈0.075 / 均亏0.025
    assert s["avg_win"] == 0.075 and s["avg_loss"] == 0.025
