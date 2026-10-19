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
