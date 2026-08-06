# -*- coding: utf-8 -*-
"""三项后续测试：② 资金流多日累计 ① 因子分位 lift/流动性 ③ 财务代理逻辑。"""
import json

import pandas as pd

from screening import enrich_candidates as ec
from screening import backtest_factors as bt
from factors import _shares
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


# ---------- B1 交易模拟(止损=买入K最低 / 站上BBI后连破2日卖出) ----------

def _mk(closes, lows=None, opens=None):
    n = len(closes)
    lows = lows or [c - 0.05 for c in closes]
    opens = opens or list(closes)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame({"date": dates, "open": opens, "high": [c + 0.05 for c in closes],
                         "low": lows, "close": closes, "volume": [1e6] * n})


def test_simulate_trade_stop():
    """止损触发口径（2026-08-04 按 B1_w.pdf 修正为收盘价判定）。

    材料原文：「设止损…**看上下区间，看收盘价**」「破掉止损价格，拍掉！（**收盘时**）」
    「**忽略盘中的冲高回落**」。所以「盘中跌破、收盘收回」**不算**破位——
    旧实现按盘中最低判定，会把这类假破全记成止损。
    """
    # 进场 idx5(收10, 止损=low[5]=9.9)；day6 盘中最低 9.0 但收盘仍 10.0
    closes = [10.0] * 10
    lows = [9.9] * 10; lows[6] = 9.0
    df = _mk(closes, lows=lows, opens=[10.0] * 10)
    bbi = pd.Series([float("nan")] * 10)

    # 默认(收盘口径)：盘中假破不出场
    r = bt.simulate_b1_trade(df, 5, bbi)
    assert r["reason"] != "stop", "收盘未破位不该止损"

    # 旧口径仍可复现，用于对照
    r_intra = bt.simulate_b1_trade(df, 5, bbi, stop_trigger="intraday")
    assert r_intra["reason"] == "stop" and r_intra["ret"] < 0 and r_intra["exit_idx"] == 6

    # 收盘也跌破 → 两种口径都止损
    closes2 = [10.0] * 10; closes2[6] = 9.5
    lows2 = [9.9] * 10; lows2[6] = 9.4
    df2 = _mk(closes2, lows=lows2, opens=[10.0] * 10)
    r2 = bt.simulate_b1_trade(df2, 5, bbi)
    assert r2["reason"] == "stop" and r2["ret"] < 0 and r2["exit_idx"] == 6


def test_simulate_trade_bbi_exit_win():
    # 进场 idx5(收10)，涨到12(站上BBI)，随后连续2日收盘跌破BBI → 止盈(仍高于进场→盈)
    closes = [10, 10, 10, 10, 10, 10, 12, 12, 10.9, 10.8]
    lows = [c - 0.05 for c in closes]           # 全程不破止损 9.95
    df = _mk(closes, lows=lows)
    bbi = pd.Series([float("nan")] * 6 + [9.0, 9.0, 11.0, 11.0])   # idx6,7 收>BBI；idx8,9 收<BBI
    r = bt.simulate_b1_trade(df, 5, bbi)
    assert r["reason"] == "bbi_exit" and r["exit_idx"] == 9 and r["ret"] > 0


def test_evaluate_and_summarize_trades():
    df = _mk([10.0 + 0.1 * i for i in range(40)])   # 单调上行 40 根
    stub = lambda s, code: {"score": 100, "suggestion": "可买"}
    trades = bt.evaluate_trades({"T": df}, scorer=stub, min_bars=30)
    assert trades and all(t["reason"] in ("stop", "bbi_exit", "open_end") for t in trades)
    s = bt.summarize_trades([{"ret": 0.2, "reason": "bbi_exit", "holding": 8},
                             {"ret": -0.05, "reason": "stop", "holding": 3}])
    assert s["n"] == 2 and s["win_rate"] == 0.5 and s["payoff_ratio"] == 4.0   # 0.2 / 0.05


def test_baseline_scorer_and_cost():
    assert "baseline" in bt.SCORERS
    assert bt.SCORERS["baseline"](_mk([10.0] * 5), "T")["suggestion"] == "可买"
    df = _mk([10.0 + 0.1 * i for i in range(40)])
    stub = lambda s, code: {"score": 100, "suggestion": "可买"}
    gross = bt.evaluate_trades({"T": df}, scorer=stub, min_bars=30, cost_bps=0)
    net = bt.evaluate_trades({"T": df}, scorer=stub, min_bars=30, cost_bps=30)
    # 同样交易，扣 30bps 后每笔净收更低
    assert net and gross and net[0]["ret"] == round(gross[0]["ret"] - 0.003, 4)


def test_amv_regime_machine():
    recs = [{"date": "2025-01-01", "change_pct": None},
            {"date": "2025-01-02", "change_pct": 5.0},    # >4 → 做多
            {"date": "2025-01-03", "change_pct": 1.0},    # 之间 → 维持做多
            {"date": "2025-01-06", "change_pct": -3.0},   # <-2.3 → 空头
            {"date": "2025-01-07", "change_pct": 2.0}]    # 之间 → 维持空头(粘滞)
    r = bt._amv_regime_from_records(recs)
    assert r["2025-01-01"] == "中性" and r["2025-01-02"] == "做多"
    assert r["2025-01-03"] == "做多" and r["2025-01-06"] == "空头" and r["2025-01-07"] == "空头"


def test_evaluate_trades_amv_long_only_gate():
    df = _mk([10.0 + 0.1 * i for i in range(45)])
    dates = [str(d)[:10] for d in df["date"]]
    stub = lambda s, code: {"score": 100, "suggestion": "可买"}
    regime = {dates[i]: ("做多" if i >= 35 else "空头") for i in range(45)}
    gated = bt.evaluate_trades({"T": df}, scorer=stub, min_bars=30, amv_regime=regime)
    ungated = bt.evaluate_trades({"T": df}, scorer=stub, min_bars=30)
    assert gated and all(t["entry_date"] >= dates[35] for t in gated)   # 只在做多区间进场
    assert any(t["entry_date"] < dates[35] for t in ungated)            # 无门槛时更早进场


def test_simulate_time_stop_and_risk_frac():
    closes = [10.0] * 12
    lows = [9.9] * 12; lows[5] = 9.5            # 进场 idx5 → 止损9.5,其后低点9.9不破
    df = _mk(closes, lows=lows, opens=[10.0] * 12)
    bbi = pd.Series([float("nan")] * 12)        # BBI 不参与 → 只可能 time_stop
    r = bt.simulate_b1_trade(df, 5, bbi, time_stop_bars=5)
    assert r["reason"] == "time_stop" and r["holding"] == 5 and abs(r["risk_frac"] - 0.05) < 1e-9


def test_summarize_expectancy_R():
    s = bt.summarize_trades([{"ret": 0.10, "reason": "bbi_exit", "holding": 8, "r_multiple": 3.0},
                             {"ret": -0.05, "reason": "stop", "holding": 3, "r_multiple": -1.0}])
    assert s["expectancy_R"] == 1.0 and s["total_R"] == 2.0
    assert s["avg_win_R"] == 3.0 and s["avg_loss_R"] == 1.0


_PF_TRADES = [
    {"entry_date": "2025-01-01", "exit_date": "2025-01-10", "ret": 0.10, "risk_frac": 0.05},
    {"entry_date": "2025-01-02", "exit_date": "2025-01-05", "ret": -0.05, "risk_frac": 0.05},
    {"entry_date": "2025-01-03", "exit_date": "2025-01-20", "ret": 0.20, "risk_frac": 0.10},
]


def test_portfolio_equity_and_drawdown():
    p = bt.simulate_portfolio(_PF_TRADES, risk_pct=0.01, max_concurrent=5,
                              max_pos_frac=0.2, max_gross=1.0)
    assert p["n_taken"] == 3 and p["n_skipped"] == 0
    assert abs(p["final_equity"] - 1.03) < 1e-6      # +0.2*(-.05)+0.2*.10+0.1*.20
    assert abs(p["max_drawdown"] - 0.01) < 1e-6      # t2 先平,权益 1.0→0.99


def test_portfolio_concurrency_cap():
    p = bt.simulate_portfolio(_PF_TRADES, risk_pct=0.01, max_concurrent=1,
                              max_pos_frac=0.2, max_gross=1.0)
    # t1 持有至 01-10，其间 t2/t3 因并发上限被跳过
    assert p["n_taken"] == 1 and p["n_skipped"] == 2
    assert abs(p["final_equity"] - 1.02) < 1e-6


def test_portfolio_topn_picks_highest_score():
    # 同日两只候选：高分(+10%) vs 低分(-10%)。top_n=1 应只选高分 → 权益 1.02
    cA = {"code": "A", "entry_date": "2025-01-01", "exit_date": "2025-01-10",
          "ret": 0.10, "risk_frac": 0.05, "score": 90}
    cB = {"code": "B", "entry_date": "2025-01-01", "exit_date": "2025-01-10",
          "ret": -0.10, "risk_frac": 0.05, "score": 10}
    p1 = bt.simulate_portfolio_topn([cA, cB], top_n=1, risk_pct=0.01, max_concurrent=5, max_pos_frac=0.2)
    assert p1["n_taken"] == 1 and abs(p1["final_equity"] - 1.02) < 1e-6   # 选了高分A
    assert p1["selected_expectancy"] == 0.10 and p1["selected_win_rate"] == 1.0  # 被选子集=A(+10%)
    p2 = bt.simulate_portfolio_topn([cA, cB], top_n=2, risk_pct=0.01, max_concurrent=5, max_pos_frac=0.2)
    assert p2["n_taken"] == 2 and abs(p2["final_equity"] - 1.0) < 1e-6    # 两只都进,净0


def test_collect_all_yields_more_candidates():
    df = _mk([10.0 + 0.1 * i for i in range(45)])
    stub = lambda s, code: {"score": 100, "suggestion": "可买"}
    nonoverlap = bt.evaluate_trades({"T": df}, scorer=stub, min_bars=30, collect_all=False)
    allc = bt.evaluate_trades({"T": df}, scorer=stub, min_bars=30, collect_all=True)
    assert len(allc) > len(nonoverlap) >= 1     # 收集全部候选 > 非重叠去重


def test_evaluate_trades_entry_gate():
    df = _mk([10.0 + 0.1 * i for i in range(45)])
    stub = lambda s, code: {"score": 100, "suggestion": "可买"}
    # 只放行切片长度为偶数的 as-of 日(模拟 J<13 之类硬门槛)
    gate = lambda s: len(s) % 2 == 0
    trades = bt.evaluate_trades({"T": df}, scorer=stub, min_bars=30, collect_all=True, entry_gate=gate)
    assert trades and all(bt._dt.date.fromisoformat(t["entry_date"]) for t in trades)
    # 与无门槛相比,进场数应减少(门槛过滤掉一半as-of日)
    nogate = bt.evaluate_trades({"T": df}, scorer=stub, min_bars=30, collect_all=True)
    assert len(trades) < len(nogate)


def test_stop_mode_pct_gives_fixed_room():
    # 进场收盘贴低(close≈low)→ low止损几乎0空间;pct止损给固定8%空间
    closes = [10.0] * 12
    lows = [9.99] * 12          # 收盘10、最低9.99 → low止损空间仅0.1%
    df = _mk(closes, lows=lows, opens=[10.0] * 12)
    bbi = pd.Series([float("nan")] * 12)
    r_low = bt.simulate_b1_trade(df, 5, bbi, stop_mode="low")
    r_pct = bt.simulate_b1_trade(df, 5, bbi, stop_mode="pct", stop_pct=8.0)
    assert abs(r_low["risk_frac"] - 0.001) < 1e-6      # low: 0.1% 空间
    assert abs(r_pct["risk_frac"] - 0.08) < 1e-6       # pct: 固定 8%


def test_reversal_k_gate_excludes_falling_knife():
    import numpy as np
    n = 25
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    # 落刀:最后一根大跌收盘贴低、放量、大振幅 → 应被 reversal_k 拒(即便 J<13)
    closes = [20.0 - 0.4 * i for i in range(n)]         # 单边下跌
    df = pd.DataFrame({"date": dates, "open": [c + 0.3 for c in closes],
                       "high": [c + 0.35 for c in closes], "low": [c - 0.05 for c in closes],
                       "close": closes, "volume": [2e6] * (n - 1) + [5e6]})   # 末日放量
    assert bt.reversal_k_gate(df) is False
    assert "reversal_k" in bt.ENTRY_GATES


def test_alpha101_prefers_strong_close():
    strong = _mk([10.0] * 5)   # helper sets open=close, high=+.05, low=-.05
    # 构造强收盘 vs 弱收盘的末根
    import pandas as _pd
    base = _mk([10.0] * 6)
    base.loc[base.index[-1], ["open", "high", "low", "close"]] = [9.6, 10.1, 9.5, 10.0]  # 收在上沿
    weak = _mk([10.0] * 6)
    weak.loc[weak.index[-1], ["open", "high", "low", "close"]] = [10.4, 10.5, 9.9, 10.0]  # 收在下沿
    s_strong = bt.SCORERS["alpha101"](base, "T")["score"]
    s_weak = bt.SCORERS["alpha101"](weak, "T")["score"]
    assert s_strong > s_weak and bt.SCORERS["alpha101"](base, "T")["suggestion"] == "可买"


def test_alpha_pvcorr_registered():
    assert "alpha101" in bt.SCORERS and "alpha_pvcorr" in bt.SCORERS
    df = _mk([10.0 + 0.1 * i for i in range(15)])
    df["volume"] = [1e6 + 1e4 * i for i in range(15)]   # 变动量,使相关系数有定义
    out = bt.SCORERS["alpha_pvcorr"](df, "T")
    assert out is not None and out["suggestion"] == "可买" and "score" in out


def test_low_vol_and_momentum_selectors():
    import numpy as np
    assert "low_vol" in bt.SCORERS and "momentum" in bt.SCORERS
    # 低波动：平稳序列得分应高于震荡序列
    calm = _mk([10.0 + 0.01 * i for i in range(30)])
    choppy = _mk([10.0 + (2.0 if i % 2 else -2.0) for i in range(30)])
    assert bt.SCORERS["low_vol"](calm, "T")["score"] > bt.SCORERS["low_vol"](choppy, "T")["score"]
    # 动量：上涨序列得分为正、下跌为负
    up = _mk([10.0 + 0.1 * i for i in range(130)])
    down = _mk([30.0 - 0.1 * i for i in range(130)])
    assert bt.SCORERS["momentum"](up, "T")["score"] > 0 > bt.SCORERS["momentum"](down, "T")["score"]
    assert bt.SCORERS["momentum"](up, "T")["suggestion"] == "可买"


def test_macd_hist_and_j_macd_turn_gate():
    # MACD 柱在底部拐头应上行
    down_then_up = _mk([20.0 - 0.5 * i for i in range(25)] + [7.5 + 0.3 * i for i in range(8)])
    h = bt._macd_hist(down_then_up["close"])
    assert h.iloc[-1] > h.iloc[-2]
    # gate 注册且返回布尔;上涨序列 J 不低 → False
    assert "j_macd_turn" in bt.ENTRY_GATES
    up = _mk([10.0 + 0.1 * i for i in range(40)])
    assert bt.ENTRY_GATES["j_macd_turn"](up) is False


def test_reversal_quality_selector():
    assert "reversal_quality" in bt.SCORERS
    import numpy as np
    n = 25
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    # 末根:缩量(量骤降)+小实体+小振幅 → 高质量反转分
    vol = [3e6] * (n - 1) + [1e5]
    closes = [10.0] * n
    df = pd.DataFrame({"date": dates, "open": [10.0] * n, "high": [10.05] * n,
                       "low": [9.95] * n, "close": closes, "volume": vol})
    q = bt.SCORERS["reversal_quality"](df, "T")
    assert q is not None and q["suggestion"] == "可买" and q["score"] >= 3   # 多数条件命中


def test_attribution_detects_robust_vs_noise():
    import random
    random.seed(0)
    trades = []
    for i in range(240):
        good = random.random()
        trades.append({
            "entry_date": f"2025-{1+i//40:02d}-{1+i % 27:02d}",
            "ret": (good - 0.5) * 0.2 + random.gauss(0, 0.01),   # ret 随 good 单调
            "features": {"good": good, "noise": random.random()}})
    r = bt.attribution_report(trades)
    assert "good" in r["robust_features"]        # train/test 同号大 lift → 稳健
    assert "noise" not in r["robust_features"]   # 噪声特征不应入选


def test_reversal_quality_inv():
    assert "reversal_quality_inv" in bt.SCORERS
    import numpy as np
    n = 25
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    df = pd.DataFrame({"date": dates, "open": [10.0] * n, "high": [10.05] * n,
                       "low": [9.95] * n, "close": [10.0] * n,
                       "volume": [3e6] * (n - 1) + [1e5]})   # 高质量反转(缩量小实体)
    q = bt.SCORERS["reversal_quality"](df, "T")["score"]
    qi = bt.SCORERS["reversal_quality_inv"](df, "T")["score"]
    assert abs((q + qi) - 4.0) < 1e-9   # 反向 = 4 - 原分


def test_portfolio_sizing_uses_risk_floor():
    # risk_frac=0.001(周线贴低)：无地板时 alloc=0.01/0.001=10倍 → 被 gross 上限跳单；
    # 有 2% 地板时 alloc=0.01/0.02=0.5 → 正常成交,与 R 倍数同口径
    t = [{"entry_date": "2025-01-01", "exit_date": "2025-01-10", "ret": 0.10, "risk_frac": 0.001}]
    p = bt.simulate_portfolio(t, risk_pct=0.01, max_concurrent=5, max_pos_frac=1.0, max_gross=1.0)
    assert p["n_taken"] == 1 and abs(p["final_equity"] - 1.05) < 1e-6


def test_main_trade_sim_out_json_has_attribution_and_params(tmp_path):
    import json
    df = _mk([10.0 + 0.1 * i for i in range(45)])
    loader = lambda codes, count: {c: df.copy() for c in codes}
    out = tmp_path / "sim.json"
    rc = bt.main(["--codes", "T", "--count", "45", "--trade-sim", "--scorer", "baseline",
                  "--entry-filter", "none", "--attribution", "--bbi-consec", "3",
                  "--stop-mode", "pct", "--stop-pct", "6", "--out", str(out)], loader=loader)
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "attribution" in payload                     # 归因必须落盘(此前写在 out 之后,JSON 缺失)
    assert payload["bbi_consec"] == 3                    # 出场/止损参数可追溯
    assert payload["stop_mode"] == "pct" and payload["stop_pct"] == 6
    assert "time_stop" in payload


def test_amv_ledger_records_tail_merge(tmp_path):
    import json as _json
    lines = [
        {"date": "2026-07-20", "amv_change_pct": -1.35, "quality": "confirmed", "recorded_at": "2026-07-20T15:41:24+08:00"},
        {"date": "2026-07-24", "amv_change_pct": -4.23, "quality": "confirmed", "recorded_at": "2026-07-24T15:37:15+08:00"},
        # 同日冲突:先误报 -4.23,后更正 +1.67 → 后写入的覆盖
        {"date": "2026-07-27", "amv_change_pct": -4.23, "quality": "confirmed", "recorded_at": "2026-07-27T10:09:13+08:00"},
        {"date": "2026-07-27", "amv_change_pct": 1.67, "quality": "confirmed", "recorded_at": "2026-07-27T15:37:34+08:00"},
        # candidate 不采信;脏键(无横杠)跳过;vdat 范围内日期不重复
        {"date": "2026-07-28", "amv_change_pct": 9.9, "quality": "candidate", "recorded_at": "2026-07-28T15:00:00+08:00"},
        {"date": "20260729", "amv_change_pct": 5.0, "quality": "confirmed", "recorded_at": "2026-07-29T15:00:00+08:00"},
        {"date": "2026-07-17", "amv_change_pct": -5.84, "quality": "confirmed", "recorded_at": "2026-07-17T20:31:52+08:00"},
    ]
    lp = tmp_path / "ledger.jsonl"
    lp.write_text("\n".join(_json.dumps(r) for r in lines), encoding="utf-8")
    recs = bt._amv_ledger_records("2015-01-01", "2026-07-17", ledger_path=lp)
    assert [(r["date"], r["change_pct"]) for r in recs] == [
        ("2026-07-20", -1.35), ("2026-07-24", -4.23), ("2026-07-27", 1.67)]
    # 缺文件 → 空,不 raise
    assert bt._amv_ledger_records("2015-01-01", None, ledger_path=tmp_path / "nope.jsonl") == []


def test_kdj_j_scorer():
    assert "kdj_j" in bt.SCORERS
    down = _mk([20.0 - 0.3 * i for i in range(40)])
    r = bt.SCORERS["kdj_j"](down, "T")
    assert r is not None and r["suggestion"] == "可买" and r["score"] < 13   # 单边下跌 J 深度超卖
    up = _mk([10.0 + 0.3 * i for i in range(40)])
    assert bt.SCORERS["kdj_j"](up, "T")["score"] > 80                        # 单边上涨 J 高位


def test_mcap_scorer_prefers_small_cap(monkeypatch):
    # 小市值得高分;未来股本事件不得用;无数据 → None(不参与排序)
    monkeypatch.setattr(_shares, "_SHARE_IDX", {
        "600000": [("2020-01-01", 1e9)],       # 小盘
        "600001": [("2020-01-01", 1e11)],      # 大盘
    })
    df = _mk([10.0] * 5)
    s_small = bt.SCORERS["mcap"](df, "600000")["score"]
    s_big = bt.SCORERS["mcap"](df, "600001")["score"]
    assert s_small > s_big and bt.SCORERS["mcap"](df, "600000")["suggestion"] == "可买"
    assert bt.SCORERS["mcap"](df, "999999") is None
    # 事件在信号日之后 → 不可用(防 look-ahead)
    monkeypatch.setattr(_shares, "_SHARE_IDX", {"600000": [("2099-01-01", 1e9)]})
    assert bt.SCORERS["mcap"](df, "600000") is None


def test_financial_factor_duplicate_column_names():
    # TDX Affair 有重复列名(经营现金流×2):row.get 返回 Series,必须取首个非空值,
    # 否则 float(Series) 抛 → 现金流全场 None、tier优 结构性不可能(2026-08 实锤)
    df = pd.DataFrame(
        [[5e8, 3e8, 3e8]],
        columns=["c_np", "c_ocf", "c_ocf"],   # 重复列名
        index=pd.Index(["600000"], name="c_code"))
    colmap = {"code": "__index__", "net_profit": "c_np", "op_cashflow": "c_ocf"}
    r = fin.financial_factor("600000", df, colmap)
    assert r["op_cashflow"] == 3e8
    assert r["dixi_proxy"]["real_earnings_cashflow"] is True


def test_j_low_dif_pos_and_adx25_gates():
    assert "j_low_dif_pos" in bt.ENTRY_GATES and "j_low_adx25" in bt.ENTRY_GATES
    # 单边上涨:J 不低 → 两个 gate 都拒
    up = _mk([10.0 + 0.2 * i for i in range(60)])
    assert bt.ENTRY_GATES["j_low_dif_pos"](up) is False
    assert bt.ENTRY_GATES["j_low_adx25"](up) is False
    # 单边下跌:J 深度超卖 + ADX 趋势强 → adx25 放行;DIF<0 → dif_pos 拒
    down = _mk([30.0 - 0.3 * i for i in range(60)])
    j = bt._kdj(down)
    assert j["available"] and j["j"] < 13
    assert bt.ENTRY_GATES["j_low_adx25"](down) is True
    assert bt.ENTRY_GATES["j_low_dif_pos"](down) is False
    # ADX 数值合理性:单边趋势 ADX 高、横盘 ADX 低
    flat = _mk([10.0 + (0.05 if i % 2 else -0.05) for i in range(60)])
    assert bt._adx_last(down) > bt._adx_last(flat)


def test_platform_pullback_gate_and_platform_stop():
    import pandas as pd
    assert "platform_pullback" in bt.ENTRY_GATES
    # 合成形态:平台(60日 9.8~10.2 反复触上沿)→75日起突破收10.5→离开至11.5→近15日回踩至≈10.2 未破
    dates = pd.date_range("2025-01-01", periods=100, freq="B")
    closes = [10.0 + (0.2 if (i // 7) % 2 else -0.2) for i in range(60)]   # 平台震荡
    closes += [10.2 + 0.04 * i for i in range(16)]                         # 缓升至 ~10.8
    closes += [11.0, 11.2, 11.5, 11.4, 11.3]                               # 离开
    closes += [11.0, 10.8, 10.6, 10.45, 10.4, 10.35, 10.4, 10.35, 10.42,
               10.4, 10.38, 10.42, 10.4, 10.38, 10.42, 10.4, 10.42, 10.45, 10.42]  # 近15日回踩企稳
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    lows[92] = 10.20                                                       # 回踩低点≈平台高×0.985
    df = pd.DataFrame({"date": dates, "open": closes, "high": highs,
                       "low": lows, "close": closes, "volume": [1e6] * 100})
    assert bt.ENTRY_GATES["platform_pullback"](df) is True
    # 单边下跌(无平台无突破)→ 拒
    down = _mk([20.0 - 0.2 * i for i in range(100)])
    assert bt.ENTRY_GATES["platform_pullback"](down) is False
    # stop_override 生效且优先于 pct
    bbi = pd.Series([float("nan")] * 100)
    tr = bt.simulate_b1_trade(df, 96, bbi, stop_mode="pct", stop_pct=8.0,
                              stop_override=10.2 * 0.98)
    assert abs(tr["risk_frac"] - (closes[96] - 10.2 * 0.98) / closes[96]) < 1e-6
