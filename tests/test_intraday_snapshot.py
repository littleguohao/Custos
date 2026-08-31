# -*- coding: utf-8 -*-
"""collect_intraday_snapshot 单测：mock tq_http.snapshot 覆盖 ok/partial/unavailable。"""

from __future__ import annotations

import unittest
from unittest import mock

from custos.datasource.collect import collect_intraday_snapshot as cis


def _ok(value: dict) -> dict:
    return {"ok": True, "value": value, "error": None}


def _bad(code: str = "tdxw_not_running") -> dict:
    return {"ok": False, "value": None, "error": {"code": code}}


class CollectTest(unittest.TestCase):
    def test_all_ok(self) -> None:
        def fake_snapshot(code: str, timeout: int = 15) -> dict:
            return _ok(
                {
                    "Now": "3764.15",
                    "Max": "206.00",
                    "Min": "5.00",
                    "UpHome": "202",
                    "DownHome": "2119",
                    "LastClose": "3882.41",
                    "Amount": "124644544.00",
                }
            )

        with mock.patch.object(cis.tq_http, "snapshot", side_effect=fake_snapshot):
            result = cis.collect()
        self.assertEqual(result["quality"], "ok")
        self.assertEqual(result["indices_ok"], 4)
        self.assertIsNone(result["error"])
        sh = result["indices"]["999999.SH"]
        self.assertEqual(sh["now"], 3764.15)
        self.assertEqual(sh["up_home"], 202.0)
        zt = result["indices"]["880006.SH"]
        self.assertEqual(zt["limit_up"], 3764.15)  # Now 字段映射到 limit_up
        self.assertEqual(zt["ever_limit_up"], 206.0)
        self.assertEqual(zt["limit_down"], 5.0)

    def test_partial(self) -> None:
        def fake_snapshot(code: str, timeout: int = 15) -> dict:
            if code == "880006.SH":
                return _bad("connection_failed")
            return _ok({"Now": "1.0", "Amount": "2.0"})

        with mock.patch.object(cis.tq_http, "snapshot", side_effect=fake_snapshot):
            result = cis.collect()
        self.assertEqual(result["quality"], "partial")
        self.assertEqual(result["indices_ok"], 3)
        self.assertFalse(result["indices"]["880006.SH"]["ok"])
        self.assertEqual(
            result["indices"]["880006.SH"]["error"]["code"], "connection_failed"
        )

    def test_unavailable_when_tdxw_down(self) -> None:
        with mock.patch.object(cis.tq_http, "snapshot", return_value=_bad()):
            result = cis.collect()
        self.assertEqual(result["quality"], "unavailable")
        self.assertEqual(result["indices_ok"], 0)
        self.assertEqual(result["error"]["code"], "tdxw_not_running")

    def test_non_numeric_values_kept(self) -> None:
        with mock.patch.object(
            cis.tq_http, "snapshot", return_value=_ok({"Now": "-", "Amount": ""})
        ):
            result = cis.collect()
        self.assertEqual(result["indices"]["999999.SH"]["now"], "-")

    def test_main_writes_file_and_exit_zero(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cis.tq_http, "snapshot", return_value=_bad()),
            mock.patch.object(cis, "MARKET_DIR", Path(tmp)),
        ):
            rc = cis.main(["--date", "2026-07-19"])
            out = json.loads(
                (Path(tmp) / "2026-07-19_intraday_snapshot.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(rc, 0)  # best-effort：失败也 exit 0
        self.assertEqual(out["quality"], "unavailable")
        self.assertEqual(out["source"], "tq_http_snapshot")
        self.assertIn("as_of", out)


def _snapshot(sh_entry: dict) -> dict:
    """只有 999999.SH 与 a_share_indices 重叠；其余统计码不在回填范围。"""
    return {
        "as_of": "2026-07-19T14:45:00+08:00",
        "indices": {"999999.SH": sh_entry},
    }


def _write_mkt(tmp: str, date: str = "2026-07-19") -> "Path":
    """最小 market_timing_input：merge 的责任范围只有 a_share_indices。"""
    import json
    from pathlib import Path

    path = Path(tmp) / f"{date}_market_timing_input.json"
    path.write_text(
        json.dumps(
            {
                "a_share_indices": {
                    "上证指数": {
                        "available": True,
                        "latest_close": 3882.41,
                        "daily_change_pct": -0.5,
                        "intraday": {"available": False, "note": "盘前占位"},
                    },
                    "创业板指": {
                        "available": True,
                        "daily_change_pct": 0.3,
                        "intraday": {"available": False, "note": "盘前占位"},
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


class MergeIntoMarketTimingInputTest(unittest.TestCase):
    """14:45 快照回填 a_share_indices[*].intraday —— 盘中腿的真实写方。

    消费端是 market_timing_scorer 的盘中腿与 final_close_review 的盘中优先分支；
    不回填时它们恒 None / 恒走 daily_change_pct 兜底。"""

    def test_merge_fills_intraday(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cis, "MARKET_DIR", Path(tmp)),
        ):
            _write_mkt(tmp)
            ok = cis.merge_into_market_timing_input(
                "2026-07-19",
                _snapshot({"ok": True, "now": 3764.15, "last_close": 3882.41}),
            )
            mkt = json.loads(
                (Path(tmp) / "2026-07-19_market_timing_input.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertTrue(ok)
        intraday = mkt["a_share_indices"]["上证指数"]["intraday"]
        self.assertTrue(intraday["available"])
        self.assertEqual(intraday["now"], 3764.15)
        self.assertEqual(intraday["last_close"], 3882.41)
        # 判定路径统一 round-2（TODO #56 口径）
        self.assertEqual(
            intraday["intraday_change_pct"], round((3764.15 / 3882.41 - 1) * 100, 2)
        )
        self.assertEqual(intraday["source"], cis.SOURCE)
        # 快照没有覆盖的指数保持占位，如实缺测
        self.assertFalse(mkt["a_share_indices"]["创业板指"]["intraday"]["available"])

    def test_missing_market_file_skips(self) -> None:
        import tempfile
        from pathlib import Path

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cis, "MARKET_DIR", Path(tmp)),
        ):
            ok = cis.merge_into_market_timing_input(
                "2026-07-19", _snapshot({"ok": True, "now": 1.0, "last_close": 1.0})
            )
        self.assertFalse(ok)  # 08:50 collector 没跑成 ⇒ 无处回填，不编造

    def test_failed_index_keeps_placeholder(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cis, "MARKET_DIR", Path(tmp)),
        ):
            _write_mkt(tmp)
            ok = cis.merge_into_market_timing_input(
                "2026-07-19",
                _snapshot({"ok": False, "error": {"code": "tdxw_not_running"}}),
            )
            mkt = json.loads(
                (Path(tmp) / "2026-07-19_market_timing_input.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertFalse(ok)
        self.assertFalse(mkt["a_share_indices"]["上证指数"]["intraday"]["available"])

    def test_non_numeric_now_skips(self) -> None:
        import tempfile
        from pathlib import Path

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cis, "MARKET_DIR", Path(tmp)),
        ):
            _write_mkt(tmp)
            ok = cis.merge_into_market_timing_input(
                "2026-07-19", _snapshot({"ok": True, "now": "-", "last_close": 3882.41})
            )
        self.assertFalse(ok)  # 转不成数的快照值不算盘中值

    def test_scorer_intraday_leg_eats_merged_value(self) -> None:
        """端到端：merge 后 scorer 的盘中腿不再恒 0 分。"""
        import tempfile
        from pathlib import Path

        from custos.pipeline.market_timing import market_timing_scorer as ms

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cis, "MARKET_DIR", Path(tmp)),
        ):
            _write_mkt(tmp)
            cis.merge_into_market_timing_input(
                "2026-07-19",
                _snapshot({"ok": True, "now": 3764.15, "last_close": 3882.41}),
            )
            import json

            mkt = json.loads(
                (Path(tmp) / "2026-07-19_market_timing_input.json").read_text(
                    encoding="utf-8"
                )
            )
        merged_score = ms.score_indices(mkt)[0]
        # 对照：无盘中值（占位形态）时同一份数据的分
        for item in mkt["a_share_indices"].values():
            item["intraday"] = {"available": False}
        baseline_score = ms.score_indices(mkt)[0]
        # intraday_change_pct≈-3.05 ⇒ _intraday_pts=-1.0，分数必须真的移动
        self.assertLess(merged_score, baseline_score)

    def test_main_merges_after_snapshot_write(self) -> None:
        """main 全流程：快照落盘 + 回填 market_timing_input，exit 恒 0。"""
        import json
        import tempfile
        from pathlib import Path

        def fake_snapshot(code: str, timeout: int = 15) -> dict:
            if code == "999999.SH":
                return _ok({"Now": "3764.15", "LastClose": "3882.41"})
            return _bad()

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cis.tq_http, "snapshot", side_effect=fake_snapshot),
            mock.patch.object(cis, "MARKET_DIR", Path(tmp)),
        ):
            _write_mkt(tmp)
            rc = cis.main(["--date", "2026-07-19"])
            mkt = json.loads(
                (Path(tmp) / "2026-07-19_market_timing_input.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(rc, 0)
        intraday = mkt["a_share_indices"]["上证指数"]["intraday"]
        self.assertTrue(intraday["available"])
        self.assertIsNotNone(intraday["intraday_change_pct"])

    def test_main_survives_market_file_missing(self) -> None:
        """market_timing_input 缺失时回填跳过，快照照常落盘、exit 0。"""
        import tempfile
        from pathlib import Path

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                cis.tq_http,
                "snapshot",
                return_value=_ok({"Now": "3764.15", "LastClose": "3882.41"}),
            ),
            mock.patch.object(cis, "MARKET_DIR", Path(tmp)),
        ):
            rc = cis.main(["--date", "2026-07-19"])
            snapshot_written = (
                Path(tmp) / "2026-07-19_intraday_snapshot.json"
            ).exists()
        self.assertEqual(rc, 0)
        self.assertTrue(snapshot_written)


if __name__ == "__main__":
    unittest.main()
