"""trade_counterfactual（v0.424）：回合切分、实际收益、规则推演、操作问题归类、空结果护栏。"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from custos.research import trade_counterfactual as tc

HDR = "成交日期,成交时间,代码,名称,交易类别,成交数量,成交价格,成交金额,费用\n"


def _ledger(tmp_path, rows):
    p = tmp_path / "ledger.csv"
    p.write_text(
        HDR + "".join(",".join(map(str, r)) + "\n" for r in rows), encoding="utf-8"
    )
    return p


def _bars(closes, start="2026-09-01"):
    dates = pd.bdate_range(start, periods=len(closes))
    c = np.array(closes, dtype=float)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": c,
            "high": c * 1.005,
            "low": c * 0.995,
            "close": c,
            "volume": 1e6,
            "amount": 1e7,
        }
    )


def _crash():
    """买入后一路跌：第 5 根收盘跌破 −10% ⇒ 规则收盘止损。"""
    return [10.0] * 40 + [10.0, 9.8, 9.6, 9.4, 9.2, 8.8, 8.5, 8.3, 8.2, 8.1, 8.0, 8.0]


def _d(df, k):
    return df["date"].iloc[k]


def test_episodes_and_actual_return():
    rows = tc.parse_ledger(
        HDR + "2026-01-02,09:30,1,甲,买入,100,10,1000,5\n"
        "2026-01-05,10:00,000001,甲,买入,100,9,900,5\n"
        "2026-01-08,10:00,000001,甲,卖出,200,11,2200,10\n"
        "2026-01-09,10:00,000002,乙,买入,100,10,1000,5\n"
    )
    eps = tc.build_episodes(rows)
    assert (
        [e["code"] for e in eps] == ["000001", "000002"]
        and eps[0]["closed"]
        and not eps[1]["closed"]
    )
    a = tc.actual_result(eps[0], None)
    assert a["ret"] == pytest.approx((2200 - 10 - 1910) / 1910)
    assert "亏损补仓" in tc.flags_of(eps[0], None)
    assert tc.actual_result(eps[1], 12.0)["ret"] == pytest.approx((1200 - 1005) / 1005)


def test_stop_delay_classified(tmp_path):
    df = _bars(_crash())
    buy, sell = _d(df, 40), _d(df, 51)  # 实际扛到最后才卖
    lp = _ledger(
        tmp_path,
        [
            (buy, "14:50", "000001", "甲", "买入", 1000, 10.0, 10000, 5),
            (sell, "10:00", "000001", "甲", "卖出", 1000, 8.0, 8000, 5),
        ],
    )
    out = tmp_path / "o.json"
    rc = tc.main(
        [
            "--ledger",
            str(lp),
            "--plans",
            str(tmp_path / "none.json"),
            "--out",
            str(out),
        ],
        loader=lambda c, d: df,
        regime={buy: "空头"},
    )
    assert rc == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    e = rep["episodes"][0]
    assert e["rule"]["reason"] == "stop" and e["rule"]["exit_date"] < sell
    assert e["rule"]["category"] == "止损拖延" and e["rule"]["delta_pp"] < 0
    assert e["plan"] == {"unable": "无买入计划止损"}
    assert any(f.startswith("非做多期开仓") for f in e["flags"])
    s = rep["summary"]["rule"]
    assert s["categories"]["止损拖延"]["share_of_negative"] == pytest.approx(1.0)
    md = (tmp_path / "o.md").read_text(encoding="utf-8")
    assert "止损拖延" in md and "Δ 最差 10 笔" in md


def test_plan_stop_and_early_sell(tmp_path):
    closes = [10.0] * 40 + [10.0 + 0.15 * k for k in range(30)]  # 买入后单边涨
    df = _bars(closes)
    buy, sell = _d(df, 40), _d(df, 42)  # 两天就卖
    lp = _ledger(
        tmp_path,
        [
            (buy, "14:50", "000001", "甲", "买入", 1000, 10.0, 10000, 5),
            (sell, "10:00", "000001", "甲", "卖出", 1000, 10.3, 10300, 5),
        ],
    )
    plans = tmp_path / "plans.json"
    plans.write_text(
        json.dumps(
            {
                "positions": {},
                "archive": {
                    "000001": [
                        {"entry_date": buy, "entry_price": 10.0, "stop": {"price": 9.5}}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "o.json"
    assert (
        tc.main(
            [
                "--ledger",
                str(lp),
                "--plans",
                str(plans),
                "--out",
                str(out),
                "--no-regime",
            ],
            loader=lambda c, d: df,
            regime={},
        )
        == 0
    )
    e = json.loads(out.read_text(encoding="utf-8"))["episodes"][0]
    assert e["rule"]["category"] == "过早卖出"
    assert e["plan"]["stop_price"] == 9.5 and e["plan"]["category"] is not None


def test_period_filter_and_empty_guard(tmp_path):
    df = _bars(_crash())
    buy, sell = _d(df, 40), _d(df, 51)
    lp = _ledger(
        tmp_path,
        [
            (buy, "14:50", "000001", "甲", "买入", 1000, 10.0, 10000, 5),
            (sell, "10:00", "000001", "甲", "卖出", 1000, 8.0, 8000, 5),
        ],
    )
    out = tmp_path / "o.json"
    argv = [
        "--ledger",
        str(lp),
        "--plans",
        str(tmp_path / "n.json"),
        "--out",
        str(out),
        "--no-regime",
    ]
    assert (
        tc.main(
            argv + ["--period", "2000-01-01", "2000-01-31"],
            loader=lambda c, d: df,
            regime={},
        )
        == 2
    )
    assert not out.exists()
    assert (
        tc.main(argv + ["--period", sell, sell], loader=lambda c, d: df, regime={}) == 0
    )


def test_section_fail_soft_and_embed(tmp_path, monkeypatch):
    from custos.pipeline.close_review import trade_cf_section as sec

    assert sec.section(tmp_path, "2026-01-01", "2026-01-31", "s") == ""  # conftest 跳过
    monkeypatch.delenv("CUSTOS_SKIP_TRADE_CF")
    assert "unavailable" in sec.section(
        tmp_path, "2026-01-01", "2026-01-31", "s"
    )  # 无台账

    class CP:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        out = Path(cmd[cmd.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".md").write_text("# 标题\n\n## 总览\n正文\n", encoding="utf-8")
        assert cmd[cmd.index("--period") + 1 : cmd.index("--period") + 3] == [
            "2026-01-01",
            "2026-01-31",
        ]
        return CP()

    (tmp_path / "data" / "trades").mkdir(parents=True)
    (tmp_path / "data" / "trades" / "master_trade_ledger.csv").write_text(
        HDR, encoding="utf-8"
    )
    monkeypatch.setattr(sec.subprocess, "run", fake_run)
    md = sec.section(tmp_path, "2026-01-01", "2026-01-31", "s")
    assert md.startswith("\n" + sec.HEADER) and "#### 总览" in md and "# 标题" not in md
    CP.returncode = 2
    CP.stderr = "[FAIL] 0 个可推演回合"
    assert "0 个可推演回合" in sec.section(tmp_path, "2026-01-01", "2026-01-31", "s")


def test_v0426_adjust_bias_and_exclusions(tmp_path):
    """前复权 K 线（价格被压低 30%）下，规则收益不得把复权缺口当亏损；港股撞号 / ETF 剔除。"""
    closes = [7.0] * 40 + [7.0] * 12  # 复权后 7 元；实际成交价 10 元（缺口 30%）
    df = _bars(closes)
    buy, sell = _d(df, 40), _d(df, 51)
    lp = _ledger(
        tmp_path,
        [
            (buy, "14:50", "600000", "浦发", "买入", 1000, 10.0, 10000, 0),
            (sell, "10:00", "600000", "浦发", "卖出", 1000, 10.0, 10000, 0),
            (buy, "14:50", "002158", "医渡科技", "买入", 100, 10.0, 1000, 0),
            (buy, "14:50", "159938", "医药ETF", "买入", 100, 1.0, 100, 0),
            (buy, "15:00", "600000", "浦发", "红利入账", 0, 0, 0, 0),
        ],
    )
    out = tmp_path / "o.json"
    assert (
        tc.main(
            [
                "--ledger",
                str(lp),
                "--plans",
                str(tmp_path / "n.json"),
                "--out",
                str(out),
                "--no-regime",
            ],
            loader=lambda c, d: df,
            regime={},
        )
        == 0
    )
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert [e["code"] for e in rep["episodes"]] == ["600000"]
    assert {x.split()[0] for x in rep["excluded_instruments"]} == {"002158", "159938"}
    r = rep["episodes"][0]["rule"]
    assert r["ret"] > -0.05  # 横盘：规则收益≈ −成本，不是 −30%
    assert rep["ledger_categories"]["红利入账"] == 1


def test_split_credit_keeps_episode_open_until_all_sold():
    rows = tc.parse_ledger(
        HDR + "2026-01-02,09:30,600000,浦发,买入,1000,10,10000,5\n"
        "2026-01-05,15:00,600000,浦发,拆股,1000,0,0,0\n"
        "2026-01-08,10:00,600000,浦发,卖出,1000,6,6000,5\n"
        "2026-01-09,10:00,600000,浦发,卖出,1000,6,6000,5\n"
    )
    eps = tc.build_episodes(rows)
    assert len(eps) == 1 and eps[0]["closed"] and eps[0]["exit_date"] == "2026-01-09"
    assert tc.actual_result(eps[0], None)["ret"] == pytest.approx(
        (11990 - 10005) / 10005
    )


def test_v0430_first_lot_basis_and_add_on(tmp_path):
    """补仓不再计入出场差：首笔按规则 vs 首笔实际；加仓盈亏单列；B1 入场分组。"""
    df = _bars(_crash())
    b0, b1, sell = _d(df, 40), _d(df, 46), _d(df, 51)
    lp = _ledger(
        tmp_path,
        [
            (b0, "14:50", "600000", "浦发", "买入", 1000, 10.0, 10000, 0),
            (b1, "14:50", "600000", "浦发", "买入", 1000, 8.5, 8500, 0),
            (sell, "10:00", "600000", "浦发", "卖出", 2000, 8.0, 16000, 0),
        ],
    )
    out = tmp_path / "o.json"
    assert (
        tc.main(
            [
                "--ledger",
                str(lp),
                "--plans",
                str(tmp_path / "n.json"),
                "--out",
                str(out),
            ],
            loader=lambda c, d: df,
            regime={b0: "做多"},
        )
        == 0
    )
    rep = json.loads(out.read_text(encoding="utf-8"))
    e = rep["episodes"][0]
    assert e["first_lot_ret"] == pytest.approx(-0.2) and e["first_lot_exit"] == sell
    assert e["add_on_pnl"] == pytest.approx(16000 - 18500 - (8000 - 10000))
    assert e["rule"]["delta_amount"] == pytest.approx(
        (e["first_lot_ret"] - e["rule"]["ret"]) * 10000
    )
    s = rep["summary"]["rule"]
    assert s["n_with_add_on"] == 1 and "by_entry" in s and s["best"]
    md = (tmp_path / "o.md").read_text(encoding="utf-8")
    assert "按入场类型" in md and "Δ 最好 10 笔" in md and "加仓 / 补仓部分盈亏" in md
