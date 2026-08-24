# -*- coding: utf-8 -*-
"""run_bear_to_long_study 驱动脚本测试:数据护栏、命令构造、断点续跑、Pass2 编排。"""

from __future__ import annotations

import json
from pathlib import Path

from custos.research import run_bear_to_long_study as rb


def _pair(sig=("2022-03-01", "2022-06-01"), lab=("2022-06-02", "2022-07-22")):
    return {
        "signal_start": sig[0],
        "signal_end": sig[1],
        "label_start": lab[0],
        "label_end": lab[1],
        "bear_days": 60,
        "long_days": 35,
        "signal_days": 60,
    }


_RECS = [{"code": "600000", "ret": 0.1, "days": [["2022-06-01", 0.0, {"f_x": 1.0}]]}]


def _firings_text(**over):
    """与 launch_point_study 写盘口径一致的 firings 头部(可用 over 制造参数漂移)。

    默认带**一条有信号日的记录**:空 firings 现在会被 firings_reusable 判为"未完成"
    (审计 E9——0 信号的产物是数据源没挂上,不是研究结论),故指纹类测试须用非空样本。
    """
    head = {
        "start": "2022-03-01",
        "end": "2022-06-01",
        "ret_start": "2022-06-02",
        "ret_end": "2022-07-22",
        "entry_filter": "reversal_k",
        "rank_score": "none",
        "feature_scores": rb.DEFAULT_FEATURES,
        "delisted_ret": -1.0,
        "universe": "local",
        "records": list(_RECS),
    }
    head.update(over)
    return json.dumps(head, ensure_ascii=False)


class TestGapGuards:
    def test_clean_pair_kept(self):
        keep, drop = rb.usable_pairs([_pair()])
        assert len(keep) == 1 and drop == []

    def test_market_cap_guard_off_by_default(self):
        """默认不开:否则 2015~2017 的窗口会被静默剔掉,12 窗池凭空缩水。"""
        early = _pair(
            sig=("2016-01-04", "2016-03-01"), lab=("2016-03-02", "2016-05-01")
        )
        keep, drop = rb.usable_pairs([early])
        assert len(keep) == 1 and drop == []

    def test_market_cap_guard_drops_pre_2018_signal_window(self):
        early = _pair(
            sig=("2016-01-04", "2016-03-01"), lab=("2016-03-02", "2016-05-01")
        )
        keep, drop = rb.usable_pairs([early], require_market_cap=True)
        assert keep == [] and len(drop) == 1
        assert rb.MV_START in drop[0]["reason"] and "市值" in drop[0]["reason"]

    def test_market_cap_guard_keeps_post_2018(self):
        keep, drop = rb.usable_pairs([_pair()], require_market_cap=True)
        assert len(keep) == 1 and drop == []

    def test_market_cap_guard_boundary_is_inclusive(self):
        """信号窗恰好起于 MV_START 当日应保留(该日已有数据)。"""
        p = _pair(sig=(rb.MV_START, "2018-03-01"), lab=("2018-03-02", "2018-05-01"))
        keep, _ = rb.usable_pairs([p], require_market_cap=True)
        assert len(keep) == 1


class TestCommands:
    def test_pass1_decouples_windows_and_keeps_delisted(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            rb.bt, "load_amv_regime", lambda since=None: {"2022-01-03": "空头"}
        )
        monkeypatch.setattr(rb.lp, "bear_to_long_pairs", lambda *a, **k: [_pair()])
        rc = rb.main(["--dry-run", "--out-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "--start 2022-03-01 --end 2022-06-01" in out  # 信号窗
        assert "--ret-start 2022-06-02 --ret-end 2022-07-22" in out  # 赢家窗(解耦)
        assert "--delisted-ret -1.0" in out  # 去幸存者偏差
        assert "--universe-local" in out  # §5 含退市宇宙准入门槛
        assert "--label-basis winner" in out and "--per-window" in out

    def test_empty_regime_exits_2(self, monkeypatch, capsys):
        monkeypatch.setattr(rb.bt, "load_amv_regime", lambda since=None: {})
        assert rb.main(["--dry-run"]) == 2

    def test_pairs_file_reused(self, tmp_path, capsys):
        pf = tmp_path / "pairs.json"
        pf.write_text(json.dumps({"window_pairs": [_pair()]}), encoding="utf-8")
        rc = rb.main(["--dry-run", "--pairs-file", str(pf), "--out-dir", str(tmp_path)])
        assert rc == 0 and "1 对可用" in capsys.readouterr().out

    def test_few_windows_warns_about_statistical_power(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            rb.bt, "load_amv_regime", lambda since=None: {"2022-01-03": "空头"}
        )
        monkeypatch.setattr(rb.lp, "bear_to_long_pairs", lambda *a, **k: [_pair()])
        rb.main(["--dry-run", "--out-dir", str(tmp_path)])
        assert "统计力很弱" in capsys.readouterr().err


class TestFeaturePassthrough:
    """Pass1 特征开关必须透传到子命令 —— 否则驱动跑一遍等于没开(2026-07-31 踩过)。"""

    def _args(self, **over):
        base = dict(
            entry_filter="reversal_k",
            delisted_ret=-1.0,
            buffer_days=60,
            gate_window=120,
            feature_scores="a,b",
            progress=200,
            chunk_size=0,
            sector_features=False,
            style_features=False,
            trade_sim=False,
            pit_features=False,
            pit_ledger="",
            pit_visible_same_day=False,
            stop_pct=8.0,
            bbi_consec=2,
        )
        base.update(over)
        return type("A", (), base)()

    def _cmd(self, **over):
        return rb.pass1_cmd(_pair(), Path("/tmp/f.json"), self._args(**over))

    def test_pit_features_passed_through(self):
        assert "--pit-features" in self._cmd(pit_features=True)

    def test_pit_features_absent_by_default(self):
        assert "--pit-features" not in self._cmd()

    def test_style_and_trade_sim_passed_through(self):
        c = self._cmd(style_features=True, trade_sim=True)
        assert "--style-features" in c and "--trade-sim" in c

    def test_pit_ledger_and_visibility_passed_through(self):
        c = self._cmd(
            pit_features=True, pit_ledger="/tmp/led.jsonl", pit_visible_same_day=True
        )
        assert "--pit-ledger" in c and "/tmp/led.jsonl" in c
        assert "--pit-visible-same-day" in c

    def test_pit_ledger_omitted_when_empty(self):
        assert "--pit-ledger" not in self._cmd(pit_features=True)

    def test_ledger_flags_not_sent_without_pit_features(self):
        """没开 --pit-features 时不该单独把 --pit-ledger 塞进命令。"""
        c = self._cmd(pit_ledger="/tmp/led.jsonl", pit_visible_same_day=True)
        assert "--pit-ledger" not in c and "--pit-visible-same-day" not in c


class TestFingerprintLegacySafe:
    """新增布尔指纹键不得让**旧 firings** 被误判为参数不一致而全窗重跑(12 窗代价极大)。"""

    def _args(self, **over):
        base = dict(
            entry_filter="reversal_k",
            feature_scores=rb.DEFAULT_FEATURES,
            delisted_ret=-1.0,
            sector_features=False,
            style_features=False,
            trade_sim=False,
            pit_features=False,
            pit_visible_same_day=False,
        )
        base.update(over)
        return type("A", (), base)()

    def _legacy_firings(self, tmp_path):
        """旧格式:头部完全没有特征开关字段(records 必须非空——空产物另有专门的拒复用闸)。"""
        p = tmp_path / "old.json"
        p.write_text(
            json.dumps(
                {
                    "entry_filter": "reversal_k",
                    "rank_score": "none",
                    "feature_scores": rb.DEFAULT_FEATURES,
                    "delisted_ret": -1.0,
                    "universe": "local",
                    "records": _RECS,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return p

    def test_legacy_firings_still_reusable_when_flags_off(self, tmp_path, capsys):
        f = self._legacy_firings(tmp_path)
        assert rb.firings_reusable(f, self._args()) is True
        assert "参数与本次不一致" not in capsys.readouterr().err

    def test_legacy_firings_rejected_once_pit_enabled(self, tmp_path, capsys):
        """开了 --pit-features 后旧 firings 没有基本面特征,必须重跑而不是静默复用。"""
        f = self._legacy_firings(tmp_path)
        assert rb.firings_reusable(f, self._args(pit_features=True)) is False
        assert "pit_features" in capsys.readouterr().err

    def test_pit_firings_rejected_when_flag_turned_off(self, tmp_path, capsys):
        """反向也要成立:带 PIT 的 firings 在不开开关时也算不一致(特征集不同)。"""
        p = tmp_path / "pit.json"
        p.write_text(
            json.dumps(
                {
                    "entry_filter": "reversal_k",
                    "rank_score": "none",
                    "feature_scores": rb.DEFAULT_FEATURES,
                    "delisted_ret": -1.0,
                    "universe": "local",
                    "pit_features": True,
                    "records": _RECS,
                }
            ),
            encoding="utf-8",
        )
        assert rb.firings_reusable(p, self._args()) is False

    def test_visibility_switch_counted_as_mismatch(self, tmp_path):
        p = tmp_path / "pit.json"
        p.write_text(
            json.dumps(
                {
                    "entry_filter": "reversal_k",
                    "rank_score": "none",
                    "feature_scores": rb.DEFAULT_FEATURES,
                    "delisted_ret": -1.0,
                    "universe": "local",
                    "pit_features": True,
                    "pit_visible_same_day": False,
                    "records": _RECS,
                }
            ),
            encoding="utf-8",
        )
        ok = rb.firings_reusable(p, self._args(pit_features=True))
        bad = rb.firings_reusable(
            p, self._args(pit_features=True, pit_visible_same_day=True)
        )
        assert ok is True and bad is False

    def test_ledger_size_not_in_fingerprint(self):
        """台账每季增长,若进指纹会导致每次补数后全窗强制重跑。"""
        assert "pit_ledger_n" not in rb.expected_firings_header(self._args())


class TestZeroRetVolumeDiagnosis:
    """按 volume 判零收益成因。tdx loader 只按收盘价有效性过滤,volume 加载了却没用上,
    停牌日只要 bundle 记了前收价就会留在 frame 里 ⇒ ret 恰好 0。"""

    def _bars(self, dates, closes, volumes):
        import pandas as pd

        return pd.DataFrame({"date": dates, "close": closes, "volume": volumes})

    def test_all_zero_volume_is_suspended_all(self):
        b = self._bars(
            ["2026-06-01", "2026-06-02", "2026-06-03"], [10.0] * 3, [0, 0, 0]
        )
        got = rb.classify_zero_ret_bars(b, "2026-06-01", "2026-06-03")
        assert got["kind"] == "suspended_all" and got["n_zero_vol"] == 3

    def test_partial_zero_volume_is_suspended_part(self):
        b = self._bars(
            [f"2026-06-{d:02d}" for d in range(1, 8)],
            [10.0] * 7,
            [100, 0, 0, 0, 0, 0, 200],
        )
        got = rb.classify_zero_ret_bars(b, "2026-06-01", "2026-06-07")
        assert got["kind"] == "suspended_part" and got["n_zero_vol"] == 5

    def test_full_volume_flat_close_is_traded_flat(self):
        """有量却首末同价 —— 这类不是停牌僵尸,剔除它就剔错了对象。"""
        b = self._bars(
            [f"2026-06-{d:02d}" for d in range(1, 8)],
            [10.0, 10.5, 11.0, 9.8, 10.2, 10.4, 10.0],
            [100] * 7,
        )
        got = rb.classify_zero_ret_bars(b, "2026-06-01", "2026-06-07")
        assert got["kind"] == "traded_flat" and got["n_zero_vol"] == 0
        assert got["first_close"] == got["last_close"] == 10.0

    def test_few_bars_flagged_before_suspended_part(self):
        """窗内只有 3 根(新上市/末期退市)——不该按区间收益判赢家,优先报 few_bars。"""
        b = self._bars(
            ["2026-06-05", "2026-06-06", "2026-06-07"], [10.0] * 3, [100, 0, 100]
        )
        got = rb.classify_zero_ret_bars(b, "2026-06-01", "2026-06-07")
        assert got["kind"] == "few_bars" and got["n_bars"] == 3

    def test_bars_outside_window_excluded(self):
        b = self._bars(
            ["2026-05-01", "2026-06-01", "2026-06-02", "2026-07-01"],
            [50.0, 10.0, 10.0, 99.0],
            [100, 0, 0, 100],
        )
        got = rb.classify_zero_ret_bars(b, "2026-06-01", "2026-06-02")
        assert got["kind"] == "suspended_all" and got["n_bars"] == 2

    def test_missing_volume_column_reported_not_guessed(self):
        import pandas as pd

        b = pd.DataFrame({"date": ["2026-06-01", "2026-06-02"], "close": [10.0, 10.0]})
        assert (
            rb.classify_zero_ret_bars(b, "2026-06-01", "2026-06-02")["kind"]
            == "no_volume_col"
        )

    def test_no_bars_in_window(self):
        b = self._bars(["2026-05-01"], [10.0], [100])
        assert (
            rb.classify_zero_ret_bars(b, "2026-06-01", "2026-06-02")["kind"]
            == "no_bars"
        )
        assert (
            rb.classify_zero_ret_bars(None, "2026-06-01", "2026-06-02")["kind"]
            == "no_bars"
        )

    def test_diagnose_aggregates_and_verdicts_suspended(self, tmp_path):
        import pandas as pd

        f = tmp_path / "w1.json"
        f.write_text(
            json.dumps(
                {
                    "start": "2022-03-01",
                    "end": "2022-06-01",
                    "ret_start": "2022-06-02",
                    "ret_end": "2022-07-22",
                    "records": [
                        {"code": "830001", "ret": 0.0},
                        {"code": "830002", "ret": 0.0},
                        {"code": "600000", "ret": 0.25},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        def loader(codes, start, end):
            dates = [f"2022-06-{d:02d}" for d in range(2, 12)]
            return {
                c: pd.DataFrame(
                    {"date": dates, "close": [10.0] * 10, "volume": [0] * 10}
                )
                for c in codes
            }

        rep = rb.zero_ret_diagnose([f], loader)
        assert rep["kind_total"] == {"suspended_all": 2}  # 只诊断 ret==0 的两只
        assert rep["n_total"] == 2
        assert "剔除对象正确" in rep["text"]
        assert rep["windows"][0]["board_mix"] == {"北交所": 2}

    def test_diagnose_flags_traded_flat_majority(self, tmp_path):
        """有量收平占多数时必须给出"剔错了对象"的告警,而不是默认认定僵尸。"""
        import pandas as pd

        f = tmp_path / "w1.json"
        f.write_text(
            json.dumps(
                {
                    "ret_start": "2022-06-02",
                    "ret_end": "2022-07-22",
                    "records": [
                        {"code": "600001", "ret": 0.0},
                        {"code": "600002", "ret": 0.0},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        def loader(codes, start, end):
            dates = [f"2022-06-{d:02d}" for d in range(2, 12)]
            return {
                c: pd.DataFrame(
                    {"date": dates, "close": [10.0] * 10, "volume": [1000] * 10}
                )
                for c in codes
            }

        rep = rb.zero_ret_diagnose([f], loader)
        assert rep["kind_total"] == {"traded_flat": 2}
        assert "剔错了对象" in rep["text"]

    def test_diagnose_loader_failure_recorded_not_raised(self, tmp_path):
        f = tmp_path / "w1.json"
        f.write_text(
            json.dumps(
                {
                    "ret_start": "2022-06-02",
                    "ret_end": "2022-07-22",
                    "records": [{"code": "600001", "ret": 0.0}],
                }
            ),
            encoding="utf-8",
        )

        def boom(codes, start, end):
            raise RuntimeError("bundle 不可用")

        rep = rb.zero_ret_diagnose([f], boom)
        assert "重载 K 线失败" in rep["windows"][0]["error"]

    def test_diagnose_window_without_zero_ret_samples(self, tmp_path):
        f = tmp_path / "w1.json"
        f.write_text(
            json.dumps(
                {
                    "ret_start": "2022-06-02",
                    "ret_end": "2022-07-22",
                    "records": [{"code": "600001", "ret": 0.3}],
                }
            ),
            encoding="utf-8",
        )
        rep = rb.zero_ret_diagnose([f], lambda *a: {})
        assert rep["n_total"] == 0 and "无零收益样本" in rep["text"]

    def test_max_codes_caps_reload(self, tmp_path):
        """大窗(实跑 807 只)可先抽样,避免一次重载太多。"""
        import pandas as pd

        f = tmp_path / "w1.json"
        f.write_text(
            json.dumps(
                {
                    "ret_start": "2022-06-02",
                    "ret_end": "2022-07-22",
                    "records": [{"code": f"83000{i}", "ret": 0.0} for i in range(5)],
                }
            ),
            encoding="utf-8",
        )
        seen = {}

        def loader(codes, start, end):
            seen["n"] = len(codes)
            dates = [f"2022-06-{d:02d}" for d in range(2, 12)]
            return {
                c: pd.DataFrame(
                    {"date": dates, "close": [10.0] * 10, "volume": [0] * 10}
                )
                for c in codes
            }

        rb.zero_ret_diagnose([f], loader, max_codes=2)
        assert seen["n"] == 2


class TestZeroRetDiagnosis:
    """零收益样本识别(zero_ret_codes)与上市板分布(board_mix)的纯函数测试。"""

    def test_only_exact_zero_counted(self):
        """-1.0(退市按大亏计入)与 None(无收益)都不是零收益样本。"""
        recs = [
            {"code": "600000", "ret": 0.0},
            {"code": "000001", "ret": -1.0},
            {"code": "300001", "ret": None},
            {"code": "688001", "ret": 0.0001},
            {"code": "830001", "ret": "0"},
            {"ret": 0.0},
        ]  # 字符串"0"可解析;无 code 跳过
        assert rb.zero_ret_codes(recs) == {"600000", "830001"}

    def test_board_mix_sorted_desc(self):
        mix = rb.board_mix(["830001", "430002", "920003", "600000"])
        assert list(mix)[0] == "北交所" and mix["北交所"] == 3 and mix["沪主板"] == 1


class TestResumeAndPass2:
    def _setup(self, monkeypatch, pairs):
        monkeypatch.setattr(
            rb.bt, "load_amv_regime", lambda since=None: {"2022-01-03": "空头"}
        )
        monkeypatch.setattr(rb.lp, "bear_to_long_pairs", lambda *a, **k: pairs)

    def test_existing_firings_skipped_unless_force(self, tmp_path, monkeypatch, capsys):
        p = _pair()
        self._setup(monkeypatch, [p])
        (tmp_path / f"firings_{rb.tag_of(p)}.json").write_text(
            _firings_text(), encoding="utf-8"
        )
        calls = []
        rb.main(["--out-dir", str(tmp_path)], runner=lambda cmd: calls.append(cmd) or 0)
        assert len(calls) == 1 and "--from-firings" in calls[0]  # 只跑了 Pass2
        assert "[skip]" in capsys.readouterr().out

        calls.clear()
        rb.main(
            ["--out-dir", str(tmp_path), "--force"],
            runner=lambda cmd: calls.append(cmd) or 0,
        )
        assert len(calls) == 2 and "--emit-firings" in calls[0]  # 强制重跑 Pass1

    def test_param_mismatch_reruns_instead_of_reuse(
        self, tmp_path, monkeypatch, capsys
    ):
        """断点续跑不能只认文件名:头部关键参数不一致 → WARN + 重跑(旧参数结果不得复用)。"""
        p = _pair()
        self._setup(monkeypatch, [p])
        (tmp_path / f"firings_{rb.tag_of(p)}.json").write_text(
            _firings_text(entry_filter="kdj_j"), encoding="utf-8"
        )  # 上次用别的 entry_filter 跑的
        calls = []
        rc = rb.main(
            ["--out-dir", str(tmp_path)], runner=lambda cmd: calls.append(cmd) or 0
        )
        assert rc == 0
        assert len(calls) == 2 and "--emit-firings" in calls[0]  # Pass1 重跑
        err = capsys.readouterr().err
        assert "[WARN]" in err and "entry_filter" in err and "kdj_j" in err
        assert "[skip]" not in capsys.readouterr().out

    def test_feature_scores_mismatch_reruns(self, tmp_path, monkeypatch, capsys):
        p = _pair()
        self._setup(monkeypatch, [p])
        (tmp_path / f"firings_{rb.tag_of(p)}.json").write_text(
            _firings_text(feature_scores="momentum"), encoding="utf-8"
        )
        calls = []
        rb.main(["--out-dir", str(tmp_path)], runner=lambda cmd: calls.append(cmd) or 0)
        assert "--emit-firings" in calls[0]
        assert "feature_scores" in capsys.readouterr().err

    def test_truncated_firings_reruns_not_skipped_nor_crashed(
        self, tmp_path, monkeypatch, capsys
    ):
        """失败 Pass1 留下的半截 JSON:不得当完成 skip,也不得崩溃 —— WARN 后重跑并纳入 Pass2。"""
        p = _pair()
        self._setup(monkeypatch, [p])
        f = tmp_path / f"firings_{rb.tag_of(p)}.json"
        f.write_text(
            '{"start": "2022-03-01", "records": [{"code": "6000', encoding="utf-8"
        )

        def _runner(cmd):
            if "--emit-firings" in cmd:
                Path(cmd[cmd.index("--emit-firings") + 1]).write_text(
                    _firings_text(), encoding="utf-8"
                )
            return 0

        calls = []
        rc = rb.main(
            ["--out-dir", str(tmp_path)],
            runner=lambda cmd: (calls.append(cmd), _runner(cmd))[1],
        )
        assert rc == 0
        assert "--emit-firings" in calls[0]  # 重跑 Pass1
        err = capsys.readouterr().err
        assert "[WARN]" in err and "截断" in err
        pass2 = [c for c in calls if "--from-firings" in c][0]
        assert rb.tag_of(p) in pass2[pass2.index("--from-firings") + 1]

    def test_records_key_missing_treated_as_unfinished(
        self, tmp_path, monkeypatch, capsys
    ):
        """可解析但缺 records 键(如旧版/误写文件)同样视为未完成。"""
        p = _pair()
        self._setup(monkeypatch, [p])
        head = json.loads(_firings_text())
        del head["records"]
        (tmp_path / f"firings_{rb.tag_of(p)}.json").write_text(
            json.dumps(head, ensure_ascii=False), encoding="utf-8"
        )
        calls = []
        rb.main(["--out-dir", str(tmp_path)], runner=lambda cmd: calls.append(cmd) or 0)
        assert "--emit-firings" in calls[0]
        assert "records" in capsys.readouterr().err

    def test_dry_run_does_not_create_out_dir(self, tmp_path, monkeypatch):
        """--dry-run 只看计划:不得落目录。"""
        p = _pair()
        self._setup(monkeypatch, [p])
        target = tmp_path / "not_created"
        rc = rb.main(["--dry-run", "--out-dir", str(target)], runner=lambda cmd: 0)
        assert rc == 0 and not target.exists()

    def test_failed_pass1_excluded_from_pass2_but_others_continue(
        self, tmp_path, monkeypatch
    ):
        p1, p2 = (
            _pair(),
            _pair(sig=("2023-01-03", "2023-02-10"), lab=("2023-02-13", "2023-05-11")),
        )
        self._setup(monkeypatch, [p1, p2])

        def _runner(cmd):
            if "--emit-firings" in cmd:
                out = cmd[cmd.index("--emit-firings") + 1]
                if rb.tag_of(p1) in out:
                    return 1  # 第一窗失败
                Path(out).write_text("{}", encoding="utf-8")
            return 0

        calls = []
        rc = rb.main(
            ["--out-dir", str(tmp_path)],
            runner=lambda cmd: (calls.append(cmd), _runner(cmd))[1],
        )
        pass2 = [c for c in calls if "--from-firings" in c][0]
        files = pass2[pass2.index("--from-firings") + 1]
        assert rc == 1  # 整体标失败
        assert (
            rb.tag_of(p2) in files and rb.tag_of(p1) not in files
        )  # 失败窗被排除,其余继续

    def test_pass2_only_skips_pass1(self, tmp_path, monkeypatch):
        p = _pair()
        self._setup(monkeypatch, [p])
        (tmp_path / f"firings_{rb.tag_of(p)}.json").write_text("{}", encoding="utf-8")
        calls = []
        rb.main(
            ["--out-dir", str(tmp_path), "--pass2-only"],
            runner=lambda cmd: calls.append(cmd) or 0,
        )
        assert len(calls) == 1 and "--from-firings" in calls[0]


class TestStopPctBbiAndLedgerFingerprint:
    def _args(self, **over):
        base = dict(
            entry_filter="reversal_k",
            delisted_ret=-1.0,
            buffer_days=60,
            gate_window=120,
            feature_scores=rb.DEFAULT_FEATURES,
            progress=200,
            chunk_size=0,
            sector_features=False,
            style_features=False,
            trade_sim=False,
            pit_features=False,
            pit_ledger="",
            pit_visible_same_day=False,
            stop_pct=8.0,
            bbi_consec=2,
        )
        base.update(over)
        return type("A", (), base)()

    def test_stop_pct_bbi_passed_through_with_trade_sim(self):
        cmd = rb.pass1_cmd(
            _pair(),
            Path("/tmp/f.json"),
            self._args(trade_sim=True, stop_pct=6.0, bbi_consec=3),
        )
        assert "--stop-pct" in cmd and "6.0" in cmd
        assert "--bbi-consec" in cmd and "3" in cmd

    def test_fingerprint_tolerates_missing_stop_defaults(self, tmp_path):
        # 旧 firings 无 stop_pct/bbi_consec/pit_ledger 键(参数后加,旧文件必然按默认跑)→ 可复用
        p = tmp_path / "f.json"
        p.write_text(_firings_text(), encoding="utf-8")
        assert rb.firings_reusable(p, self._args()) is True

    def test_fingerprint_catches_stop_pct_and_ledger_change(self, tmp_path):
        p = tmp_path / "f.json"
        p.write_text(
            _firings_text(stop_pct=8.0, bbi_consec=2, pit_ledger=""), encoding="utf-8"
        )
        assert (
            rb.firings_reusable(p, self._args(stop_pct=6.0)) is False
        )  # 出场参数变了 → 重跑
        assert (
            rb.firings_reusable(p, self._args(pit_ledger="/o.json")) is False
        )  # 换台账 → 重跑
        assert rb.firings_reusable(p, self._args()) is True


def test_zero_ret_diagnose_missing_ret_window_no_fallback(tmp_path):
    # 缺 ret_start/ret_end 不得回退信号窗当赢家窗(整窗错位且无提示)——显式 error
    p = tmp_path / "old.json"
    p.write_text(
        json.dumps(
            {
                "start": "2022-05-06",
                "end": "2022-06-02",
                "records": [
                    {
                        "code": "600000",
                        "ret": 0.0,
                        "days": [["2022-06-01", 0.0, {"f_x": 1.0}]],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = rb.zero_ret_diagnose([p], loader=lambda codes, s, e: {})
    w = out["windows"][0]
    assert "ret_start" in w["error"] and "重跑" in w["error"]
