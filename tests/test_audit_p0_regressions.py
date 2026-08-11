# -*- coding: utf-8 -*-
"""P0 审计修复的回归测试——每条对应一个曾经会静默出错的不变量。

这些用例的共同主题是「缺数据/坏数据绝不能表现为好数据」。审计发现的系统性
失效模式是:检测能力充足,但降级信息不传导——检测到异常后落盘一个标记,
然后用默认值继续跑,下游把默认值当真值。以下每个 class 钉住一条传导链。
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from custos.core import runtime_guards as rg
from custos.datasource import trading_calendar as tc
from custos.datasource.news import rss_collector as rc
from custos.core.trades import incremental_ledger as il


class TestCalendarRefreshDoesNotOverreach:
    """A1: 刷新交易日历时,RPC 没答复的区间不得被推断为休市。

    default_range 请求到次年(today+370d),而交易所只公布当年安排。旧代码把整个
    **请求区间**当权威,于是次年每一天都被写成 confirmed 非交易日 →
    runtime_gate --require-trading-day 在真实交易日 exit 3 → 五个 cron 集体停摆。
    """

    def test_days_outside_answered_span_are_left_unknown(self):
        cfg = {"trading_days": [], "non_trading_days": [], "covered_ranges": []}
        # 请求 2026-07-01 ~ 2027-08-05,但 RPC 只答到 2026-07-03
        merged = tc.merge_range(
            cfg,
            date(2026, 7, 1),
            date(2027, 8, 5),
            ["2026-07-01", "2026-07-02", "2026-07-03"],
        )
        closed = set(merged["non_trading_days"])
        assert not any(d.startswith("2027") for d in closed), (
            "答复区间外的次年不得被标休市"
        )
        assert not any(d > "2026-07-03" for d in closed), "答复区间外一律不表态"

    def test_gaps_inside_answered_span_are_still_inferred(self):
        """区间内的缺口仍要推断为休市——这是本函数的正常职责,不能一并放弃。"""
        cfg = {"trading_days": [], "non_trading_days": [], "covered_ranges": []}
        merged = tc.merge_range(
            cfg, date(2026, 7, 1), date(2026, 12, 31), ["2026-07-01", "2026-07-03"]
        )
        assert "2026-07-02" in set(merged["non_trading_days"])
        assert set(merged["trading_days"]) == {"2026-07-01", "2026-07-03"}

    def test_covered_range_records_answered_span_not_requested(self):
        cfg = {"trading_days": [], "non_trading_days": [], "covered_ranges": []}
        merged = tc.merge_range(
            cfg, date(2026, 7, 1), date(2027, 8, 5), ["2026-07-01", "2026-07-03"]
        )
        rng = merged["covered_ranges"][-1]
        assert (rng["start"], rng["end"]) == ("2026-07-01", "2026-07-03")
        assert rng["requested"]["end"] == "2027-08-05"  # 请求区间仅作留痕

    def test_empty_answer_refuses_to_merge(self):
        """空答复必须抛错而不是把整个区间标成休市。"""
        with pytest.raises(RuntimeError, match="empty trading_days"):
            tc.merge_range(
                {"trading_days": [], "non_trading_days": []},
                date(2026, 7, 1),
                date(2026, 7, 31),
                [],
            )


class TestAmvQualityNoLongerDefaultsConfirmed:
    """A2: 0AMV 缺 quality 字段时不得默认 confirmed。

    collector 与 --amv 人工读数写入的 section 都没有 quality 键。旧代码给 0AMV
    特权默认 confirmed → amv_ok=True → 加权分≥0.8 判 pass → 授予加仓权,与
    「0AMV 非 confirmed/auto 时一律不得 pass」正好相反。
    """

    def _market(self, **amv):
        return {
            "amv_0": {"amv_change_pct": 1.2, "as_of": "2026-07-20", **amv},
            "market_breadth": {
                "up_count": 3000,
                "as_of": "2026-07-20",
                "quality": "confirmed",
            },
            "sentiment": {
                "limit_up_count": 50,
                "as_of": "2026-07-20",
                "quality": "confirmed",
            },
            "turnover": {
                "turnover_change_pct": 5.0,
                "as_of": "2026-07-20",
                "quality": "confirmed",
            },
            "overseas_market": {"nasdaq_change_pct": 1.0, "as_of": "2026-07-20"},
        }

    def test_missing_quality_is_not_confirmed(self):
        r = rg.market_quality_gate(self._market(), "2026-07-20")
        amv = next(x for x in r["checks"] if x["field"] == "0AMV")
        assert amv["quality"] == "candidate"
        assert r["amv_ok"] is False
        assert r["status"] != "pass", "regime 未知不得判 pass"

    def test_explicit_confirmed_still_passes(self):
        r = rg.market_quality_gate(self._market(quality="confirmed"), "2026-07-20")
        assert r["amv_ok"] is True and r["status"] == "pass"

    def test_missing_quality_denies_position_increase(self):
        mq = rg.market_quality_gate(self._market(), "2026-07-20")
        d = rg.position_increase_decision(
            self._market(effective_state="做多"),
            reduction_ready=True,
            technical_current=True,
            quotes_current=True,
            market_quality=mq,
        )
        assert d["allow"] is False, "0AMV 未确认时即便 regime 是做多也不得加仓"

    def test_degraded_is_recognized_not_upgraded(self):
        """collector 主动上报 degraded 时不能被 fallback 升格为 candidate。"""
        r = rg.market_quality_gate(self._market(quality="degraded"), "2026-07-20")
        amv = next(x for x in r["checks"] if x["field"] == "0AMV")
        assert amv["quality"] == "degraded"
        assert r["amv_ok"] is False


class TestLedgerIdempotence:
    """D2: 重复导入同一份成交文件必须是 no-op,而真实重复分笔仍要入账。"""

    def _incoming(self, rows):
        df = il.norm(pd.DataFrame(rows))
        df["_fingerprint"] = df.apply(il.fingerprint, axis=1)
        return df

    def _row(self, **kw):
        base = {
            "成交日期": "2026-07-20",
            "成交时间": "093000",
            "代码": "000001",
            "名称": "平安",
            "交易类别": "买入",
            "成交数量": 100,
            "成交价格": 10.0,
        }
        base.update(kw)
        return base

    def test_reimporting_same_file_selects_nothing(self):
        inc = self._incoming([self._row(), self._row(成交时间="094500")])
        already = list(inc["_fingerprint"])
        assert len(il.select_new_rows(inc, already)) == 0

    def test_genuine_split_order_is_appended(self):
        """同一秒同价同量的两笔真实分笔:台账已有 1 笔时应只补 1 笔,不是丢弃也不是翻倍。"""
        inc = self._incoming([self._row(), self._row()])
        picked = il.select_new_rows(inc, [inc["_fingerprint"].iloc[0]])
        assert len(picked) == 1

    def test_fresh_import_takes_everything(self):
        inc = self._incoming([self._row(), self._row(成交时间="094500")])
        assert len(il.select_new_rows(inc, [])) == 2

    def test_partial_overlap_appends_only_the_new_rows(self):
        inc = self._incoming(
            [self._row(), self._row(成交时间="094500"), self._row(成交时间="100000")]
        )
        picked = il.select_new_rows(inc, [inc["_fingerprint"].iloc[0]])
        assert list(picked["成交时间"]) == ["094500", "100000"]

    def test_force_bypasses_dedup(self):
        inc = self._incoming([self._row()])
        assert len(il.select_new_rows(inc, list(inc["_fingerprint"]), force=True)) == 1


class TestLedgerAtomicity:
    """D1: 台账写失败绝不能留下已被改动的持仓快照。

    旧顺序是 apply_positions(改持仓) → to_csv(写台账)。CSV 写失败后台账没有记录,
    重跑时指纹匹配不上 → 同一批成交被二次应用 → **持仓翻倍**。
    """

    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path, monkeypatch):
        self.pos = tmp_path / "current_positions.json"
        self.ledger = tmp_path / "master_trade_ledger.csv"
        self.audit = tmp_path / "audit.jsonl"
        self.stock = tmp_path / "trades_stock.json"
        self.src = tmp_path / "in.json"
        monkeypatch.setattr(il, "POS", self.pos)
        monkeypatch.setattr(il, "LEDGER", self.ledger)
        monkeypatch.setattr(il, "AUDIT", self.audit)
        monkeypatch.setattr(il, "STOCK_JSON", self.stock)

    def _write_input(self, rows):
        self.src.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    def _positions(self):
        if not self.pos.exists():
            return {}
        return {x["代码"]: x for x in json.loads(self.pos.read_text(encoding="utf-8"))}

    def test_oversell_leaves_both_files_untouched(self):
        self.pos.write_text(
            json.dumps(
                [
                    {
                        "代码": "000001",
                        "名称": "平安",
                        "持有数量": 100.0,
                        "单位成本": 10.0,
                    }
                ]
            ),
            encoding="utf-8",
        )
        self._write_input(
            [
                {
                    "成交日期": "2026-07-20",
                    "成交时间": "093000",
                    "代码": "000001",
                    "名称": "平安",
                    "交易类别": "卖出",
                    "成交数量": 500,
                    "成交价格": 11.0,
                }
            ]
        )
        with pytest.raises(ValueError, match="超过台账持仓"):
            il.main(["--input", str(self.src)])
        assert self._positions()["000001"]["持有数量"] == 100.0, "失败必须原样保留持仓"
        assert not self.ledger.exists(), "失败不得写出台账"

    def test_ledger_write_failure_does_not_move_positions(self, monkeypatch):
        """模拟 CSV 落盘失败(Excel 占用/磁盘满):持仓必须保持原值。"""
        self.pos.write_text("[]", encoding="utf-8")
        self._write_input(
            [
                {
                    "成交日期": "2026-07-20",
                    "成交时间": "093000",
                    "代码": "000001",
                    "名称": "平安",
                    "交易类别": "买入",
                    "成交数量": 100,
                    "成交价格": 10.0,
                }
            ]
        )
        orig = pd.DataFrame.to_csv

        def boom(self_df, *args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(pd.DataFrame, "to_csv", boom)
        with pytest.raises(OSError):
            il.main(["--input", str(self.src)])
        monkeypatch.setattr(pd.DataFrame, "to_csv", orig)
        assert self._positions() == {}, "台账没写成功,持仓就不能已经加上"

    def test_successful_run_commits_both(self):
        self.pos.write_text("[]", encoding="utf-8")
        self._write_input(
            [
                {
                    "成交日期": "2026-07-20",
                    "成交时间": "093000",
                    "代码": "000001",
                    "名称": "平安",
                    "交易类别": "买入",
                    "成交数量": 100,
                    "成交价格": 10.0,
                    "费用": 5.0,
                }
            ]
        )
        il.main(["--input", str(self.src)])
        assert self._positions()["000001"]["持有数量"] == 100
        assert len(pd.read_csv(self.ledger)) == 1

    def test_rerun_same_input_is_idempotent(self):
        """端到端幂等:同一份文件跑两次,持仓与台账都不变。"""
        self.pos.write_text("[]", encoding="utf-8")
        self._write_input(
            [
                {
                    "成交日期": "2026-07-20",
                    "成交时间": "093000",
                    "代码": "000001",
                    "名称": "平安",
                    "交易类别": "买入",
                    "成交数量": 100,
                    "成交价格": 10.0,
                }
            ]
        )
        il.main(["--input", str(self.src)])
        il.main(["--input", str(self.src)])
        assert self._positions()["000001"]["持有数量"] == 100, "重复导入不得翻倍"
        assert len(pd.read_csv(self.ledger)) == 1

    def test_no_temp_files_left_behind(self):
        self.pos.write_text("[]", encoding="utf-8")
        self._write_input(
            [
                {
                    "成交日期": "2026-07-20",
                    "成交时间": "093000",
                    "代码": "000001",
                    "名称": "平安",
                    "交易类别": "买入",
                    "成交数量": 100,
                    "成交价格": 10.0,
                }
            ]
        )
        il.main(["--input", str(self.src)])
        assert not list(self.ledger.parent.glob("*.tmp"))


class TestRssTransportTrust:
    """D6: 关闭 TLS 校验必须显式承担,且不得再被当作 confirmed 证据。"""

    def test_insecure_without_ack_is_rejected(self):
        with pytest.raises(ValueError, match="ssl_insecure_ack"):
            rc.build_ssl_context({"id": "gov_cn", "ssl_verify": False})

    def test_insecure_with_ack_is_marked_unverified(self):
        ctx, verified = rc.build_ssl_context(
            {"id": "x", "ssl_verify": False, "ssl_insecure_ack": True}
        )
        assert verified is False and ctx.verify_mode == rc.ssl.CERT_NONE

    def test_default_is_verified(self):
        ctx, verified = rc.build_ssl_context({"id": "x"})
        assert verified is True and ctx.verify_mode == rc.ssl.CERT_REQUIRED

    def test_unverified_transport_downgrades_tier_s_evidence(self):
        """tier S 说明机构权威,但传输不可信时这段字节未必来自该机构。"""
        assert rc._tier_quality("S", transport_verified=False) == "candidate"
        assert rc._tier_quality("S", transport_verified=True) == "confirmed"

    def test_registry_has_no_unacknowledged_insecure_source(self):
        """配置层防线:仓库里不得再出现未承担风险的关校验源。"""
        cfg = json.loads(rc.REG.read_text(encoding="utf-8-sig"))
        bad = [
            s["id"]
            for s in cfg["sources"]
            if s.get("ssl_verify") is False and not s.get("ssl_insecure_ack")
        ]
        assert bad == [], f"这些源关闭了 TLS 校验却未显式承担: {bad}"

    def test_oversized_feed_is_refused(self):
        class _Resp:
            def read(self, n):
                return b"x" * n  # 永远给满,模拟无限大响应

        with pytest.raises(ValueError, match="exceeds"):
            rc._read_limited(_Resp())

    def test_normal_feed_passes(self):
        class _Resp:
            def read(self, n):
                return b"<rss/>"

        assert rc._read_limited(_Resp()) == b"<rss/>"

    def test_unsafe_source_id_rejected_by_whitelist(self):
        assert rc.SAFE_ID.match("gov_cn")
        for bad in ("../../etc/passwd", "a/b", "x" * 65, "", "a b"):
            assert not rc.SAFE_ID.match(bad), f"{bad!r} 不该通过文件名白名单"

    def test_error_redacts_query_string(self):
        msg = rc._redact("HTTPError(url='https://x.com/rss?token=SECRET123&k=v')")
        assert "SECRET123" not in msg and "<redacted>" in msg
