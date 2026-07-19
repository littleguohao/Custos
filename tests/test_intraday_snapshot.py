# -*- coding: utf-8 -*-
"""collect_intraday_snapshot 单测：mock tq_http.snapshot 覆盖 ok/partial/unavailable。"""
from __future__ import annotations

import unittest
from unittest import mock

import collect_intraday_snapshot as cis


def _ok(value: dict) -> dict:
    return {"ok": True, "value": value, "error": None}


def _bad(code: str = "tdxw_not_running") -> dict:
    return {"ok": False, "value": None, "error": {"code": code}}


class CollectTest(unittest.TestCase):
    def test_all_ok(self) -> None:
        def fake_snapshot(code: str, timeout: int = 15) -> dict:
            return _ok({"Now": "3764.15", "Max": "206.00", "Min": "5.00",
                        "UpHome": "202", "DownHome": "2119",
                        "LastClose": "3882.41", "Amount": "124644544.00"})

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
        self.assertEqual(result["indices"]["880006.SH"]["error"]["code"], "connection_failed")

    def test_unavailable_when_tdxw_down(self) -> None:
        with mock.patch.object(cis.tq_http, "snapshot", return_value=_bad()):
            result = cis.collect()
        self.assertEqual(result["quality"], "unavailable")
        self.assertEqual(result["indices_ok"], 0)
        self.assertEqual(result["error"]["code"], "tdxw_not_running")

    def test_non_numeric_values_kept(self) -> None:
        with mock.patch.object(cis.tq_http, "snapshot", return_value=_ok({"Now": "-", "Amount": ""})):
            result = cis.collect()
        self.assertEqual(result["indices"]["999999.SH"]["now"], "-")

    def test_main_writes_file_and_exit_zero(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(cis.tq_http, "snapshot", return_value=_bad()), \
             mock.patch.object(cis, "MARKET_DIR", Path(tmp)):
            rc = cis.main(["--date", "2026-07-19"])
            out = json.loads((Path(tmp) / "2026-07-19_intraday_snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)  # best-effort：失败也 exit 0
        self.assertEqual(out["quality"], "unavailable")
        self.assertEqual(out["source"], "tq_http_snapshot")
        self.assertIn("as_of", out)


if __name__ == "__main__":
    unittest.main()
