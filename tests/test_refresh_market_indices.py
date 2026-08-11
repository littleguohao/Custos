# -*- coding: utf-8 -*-
"""`refresh_market_indices` —— 17:00 链在 `final_close_review` 之前补齐指数与成交额。

存在的理由（源码 docstring）：08:50 采集可能失败（TdxW 那时没开），
盘后必须有一次兜底刷新，否则 `market_timing_scorer` 会拿着缺失/陈旧的指数打分。

⚠️ 它是**就地改写** `market_timing_input.json` 的 stage ——
一个渐进填充产物，19 个消费者。改写时保留什么、覆盖什么，是本模块的全部风险。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.datasource import refresh_market_indices as rmi  # noqa: E402

DAY = "2026-08-11"


def _bars(n=260, close=3000.0, amount=5e11, last_date="20260811"):
    """造 n 根日线；日期递增到 last_date。"""
    end = pd.Timestamp(last_date)
    dates = pd.date_range(end=end, periods=n, freq="B")
    return pd.DataFrame(
        {
            "date": dates,
            "close": [close + i * 0.1 for i in range(n)],
            "amount": [amount] * n,
            "volume": [1e9] * n,
        }
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    """隔离全部 Path 常量 + 打桩 vipdoc 读取。

    ⚠️ patch **全部**大写 Path 常量 —— 只挑想到的那几个已经踩过两次
    （08-07 漏 `REVIEWS`、08-10 漏 `AUDIT`，测试往真实目录写）。
    """
    for attr in dir(rmi):
        v = getattr(rmi, attr, None)
        if attr.isupper() and isinstance(v, pathlib.Path):
            monkeypatch.setattr(rmi, attr, tmp_path)
    # MARKET_DIR 是 paths 常量（= BASE/data/market），打平 patch 成 tmp_path 后
    # 与下面的 fixture 树（tmp/data/market）对不上，单独指到子目录。
    monkeypatch.setattr(rmi, "MARKET_DIR", tmp_path / "data" / "market")
    # ⚠️ main() 的 breadth 分支会经 resolve_total_stocks() 读**真实**的
    # data/.../a_share_universe.json（breadth_basis 的模块常量不在上面的
    # patch 范围内）——只读但也有真实文件依赖，打桩断掉（2026-08-11 评审指出）。
    monkeypatch.setattr(rmi, "resolve_total_stocks", lambda: (5538, "test_stub"))
    (tmp_path / "data" / "market").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_market(base, payload):
    p = base / "data" / "market" / f"{DAY}_market_timing_input.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _run(monkeypatch, base, bars_by_code=None, default=None):
    calls = []

    def fake(code, count=260, prefer="vipdoc"):
        calls.append(code)
        if bars_by_code and code in bars_by_code:
            return bars_by_code[code]
        return default if default is not None else _bars()

    monkeypatch.setattr(rmi.ltd, "get_ohlcv_table", fake)
    monkeypatch.setattr(sys, "argv", ["x", "--date", DAY])
    rmi.main()
    p = base / "data" / "market" / f"{DAY}_market_timing_input.json"
    return json.loads(p.read_text(encoding="utf-8")), calls


class TestStaleDetection:
    """`_is_stale` 要同时认 `2026-07-17` 与 `20260717` 两种形态。"""

    @pytest.mark.parametrize("as_of", ["2026-08-10", "20260810", "2026/08/10"])
    def test_earlier_date_is_stale(self, as_of):
        assert rmi._is_stale(as_of, DAY) is True

    @pytest.mark.parametrize("as_of", ["2026-08-11", "20260811"])
    def test_same_date_is_fresh(self, as_of):
        assert rmi._is_stale(as_of, DAY) is False

    def test_missing_as_of_is_stale(self):
        """⚠️ 缺 as_of 判**陈旧**（需刷新）—— 方向安全：宁可多刷一次，
        不能把「不知道是哪天」当成新鲜。与 `market_timing_scorer.is_stale`
        的取向一致（后者判「不新鲜」）。"""
        assert rmi._is_stale(None, DAY) is True
        assert rmi._is_stale("", DAY) is True


class TestComputeIndex:
    def test_empty_frame_is_unavailable(self, monkeypatch):
        monkeypatch.setattr(rmi.ltd, "get_ohlcv_table", lambda *a, **k: pd.DataFrame())
        r = rmi.compute_index("000001.SH")
        assert r == {"available": False, "source": "vipdoc_day"}

    def test_single_bar_is_unavailable(self, monkeypatch):
        """只有一根 K 线算不出涨跌幅 ⇒ unavailable，**不是** available+None。"""
        monkeypatch.setattr(rmi.ltd, "get_ohlcv_table", lambda *a, **k: _bars(n=1))
        assert rmi.compute_index("000001.SH")["available"] is False

    def test_full_history_fills_all_mas(self, monkeypatch):
        monkeypatch.setattr(rmi.ltd, "get_ohlcv_table", lambda *a, **k: _bars(260))
        r = rmi.compute_index("000001.SH")
        for k in ("ma25", "ma60", "ma144", "ma240"):
            assert r[k] is not None, k
        for k in ("above_ma25", "above_ma60", "above_ma144", "above_ma240"):
            assert isinstance(r[k], bool), k

    def test_short_history_leaves_long_mas_none_not_false(self, monkeypatch):
        """⚠️ 数据不够长时 `ma240` / `above_ma240` 必须是 **None 而不是 False**。

        `False` 会被 `market_timing_scorer.score_indices` 当成「在 MA240 下方」
        扣 0.5 分 —— 把「算不出」当成「跌破」，方向偏空。
        这与 `ma_flag(None)` 曾显示「下MA240」是同一类失真。
        """
        monkeypatch.setattr(rmi.ltd, "get_ohlcv_table", lambda *a, **k: _bars(30))
        r = rmi.compute_index("000001.SH")
        assert r["ma25"] is not None, "25 根够了"
        assert r["ma240"] is None and r["above_ma240"] is None
        assert r["ma60"] is None and r["above_ma60"] is None

    def test_latest_date_is_the_max_not_the_first_row(self, monkeypatch):
        """行内顺序不可靠（源码显式 sort），`latest_date` 必须是最大日期。"""
        df = _bars(10, last_date="20260811").iloc[::-1].reset_index(drop=True)
        monkeypatch.setattr(rmi.ltd, "get_ohlcv_table", lambda *a, **k: df)
        assert rmi.compute_index("000001.SH")["latest_date"] == "20260811"


class TestMainRefreshPolicy:
    def test_missing_market_file_skips_quietly(self, env, monkeypatch, capsys):
        """⚠️ 输入文件不存在时 `[SKIP]` 返回，**不抛** ——
        它在 17:00 链里是 best-effort stage，崩了会拖垮整条链。"""
        monkeypatch.setattr(rmi.ltd, "get_ohlcv_table", lambda *a, **k: _bars())
        monkeypatch.setattr(sys, "argv", ["x", "--date", DAY])
        rmi.main()
        assert "[SKIP]" in capsys.readouterr().out

    def test_fresh_index_is_not_refetched(self, env, monkeypatch):
        """⚠️ 已 available + 有 daily_change_pct + latest_date 不早于当日 ⇒ **跳过**。

        不跳过的代价：盘后刷新会用 vipdoc 收盘价盖掉 14:45 已算好的读数，
        而 vipdoc 在盘中/刚收盘时可能还没更新。
        """
        fresh = {
            name: {
                "available": True,
                "daily_change_pct": 1.0,
                "latest_date": "20260811",
            }
            for name in rmi.INDICES
        }
        _write_market(
            env,
            {
                "date": DAY,
                "a_share_indices": fresh,
                "turnover": {
                    "quality": "auto",
                    "as_of": DAY,
                    "turnover_change_pct": 3.0,
                },
            },
        )
        _, calls = _run(monkeypatch, env)
        assert not [c for c in calls if c in rmi.INDICES.values()], (
            f"新鲜数据被重新抓取了：{calls}"
        )

    def test_stale_index_is_refreshed(self, env, monkeypatch):
        stale = {
            name: {
                "available": True,
                "daily_change_pct": 1.0,
                "latest_date": "20260807",
            }
            for name in rmi.INDICES
        }
        _write_market(env, {"date": DAY, "a_share_indices": stale})
        out, calls = _run(monkeypatch, env)
        assert calls, "陈旧数据没有触发刷新"
        assert out["a_share_indices"]["上证指数"]["latest_date"] == "20260811"

    def test_intraday_is_preserved_across_refresh(self, env, monkeypatch):
        """⚠️⚠️ **`intraday` 必须在刷新后保留** —— 它来自 14:45 的实时快照，
        vipdoc 里没有这个字段。冲掉它等于让盘后报告失去盘中读数，
        而 `score_indices` 的 intraday 分项会静默变成「无」。
        """
        _write_market(
            env,
            {
                "date": DAY,
                "a_share_indices": {
                    "上证指数": {
                        "available": False,
                        "intraday": {"intraday_change_pct": 1.23},
                    }
                },
            },
        )
        out, _ = _run(monkeypatch, env)
        sh = out["a_share_indices"]["上证指数"]
        assert sh["available"] is True, "应已刷新"
        assert sh["intraday"] == {"intraday_change_pct": 1.23}, "盘中读数被冲掉了"

    def test_unavailable_fetch_does_not_erase_existing(self, env, monkeypatch):
        """抓取失败（empty frame）时**不得**把已有条目改成 unavailable ——
        宁可留旧值，也不要用「没抓到」覆盖「抓到过」。"""
        _write_market(
            env,
            {
                "date": DAY,
                "a_share_indices": {
                    "上证指数": {
                        "available": True,
                        "daily_change_pct": 0.5,
                        "latest_date": "20260807",
                        "latest_close": 3200.0,
                    }
                },
            },
        )
        out, _ = _run(monkeypatch, env, default=pd.DataFrame())
        sh = out["a_share_indices"]["上证指数"]
        assert sh["available"] is True and sh["latest_close"] == 3200.0


class TestTurnoverFallback:
    def test_full_market_880001_wins_over_sh_narrow_scope(self, env, monkeypatch):
        """⚠️ 成交额是**两段回填**：先用上证指数 amount 兜底，再用 880001 覆盖。

        最终留下的必须是 **880001（全市场口径）** —— 上证 amount 只是窄口径兜底
        （源码自己的 note 写「全市场口径需另采880001」）。
        我第一版按「上证版存活」写断言，实测才发现有第二段覆盖 ——
        **两段填同一个键**这件事只有真跑一遍才看得见。
        """
        _write_market(env, {"date": DAY, "a_share_indices": {}})
        out, _ = _run(monkeypatch, env)
        t = out.get("turnover") or {}
        assert t.get("source") == "vipdoc_880001_amount", f"应由 880001 定案：{t}"
        assert t.get("total_turnover"), "全市场成交额未回填"
        assert t.get("turnover_change_pct") is not None, (
            "环比变化率是 score_turnover 的唯一输入，缺了就只能给半分"
        )

    def test_sh_fallback_survives_when_880001_unavailable(self, env, monkeypatch):
        """880001 抓不到时，上证窄口径兜底应留下**并带口径警示**。"""
        _write_market(env, {"date": DAY, "a_share_indices": {}})
        out, _ = _run(monkeypatch, env, bars_by_code={"880001.SH": pd.DataFrame()})
        t = out.get("turnover") or {}
        assert t.get("source") == "vipdoc_000001_amount", f"上证兜底未留下：{t}"
        assert "全市场口径需另采880001" in (t.get("note") or ""), (
            "窄口径必须留痕，否则下游会当成全市场成交额"
        )

    def test_existing_good_turnover_is_not_overwritten(self, env, monkeypatch):
        """⚠️ 已有 `quality=auto` 的成交额不得被上证口径覆盖 ——
        后者是**窄口径**（源码 note 自己说了「全市场口径需另采880001」）。"""
        _write_market(
            env,
            {
                "date": DAY,
                "a_share_indices": {},
                "turnover": {
                    "quality": "auto",
                    "as_of": DAY,
                    "value": 9.99e11,
                    "turnover_change_pct": 5.0,
                    "source": "880001",
                },
            },
        )
        out, _ = _run(monkeypatch, env)
        assert out["turnover"]["source"] == "880001"
        assert out["turnover"]["value"] == 9.99e11
