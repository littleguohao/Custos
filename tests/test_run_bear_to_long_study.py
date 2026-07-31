# -*- coding: utf-8 -*-
"""run_bear_to_long_study 驱动脚本测试:数据护栏、命令构造、断点续跑、Pass2 编排。"""
from __future__ import annotations

import json
from pathlib import Path

from screening import run_bear_to_long_study as rb


def _pair(sig=("2022-03-01", "2022-06-01"), lab=("2022-06-02", "2022-07-22")):
    return {"signal_start": sig[0], "signal_end": sig[1],
            "label_start": lab[0], "label_end": lab[1],
            "bear_days": 60, "long_days": 35, "signal_days": 60}


def _firings_text(**over):
    """与 launch_point_study 写盘口径一致的 firings 头部(可用 over 制造参数漂移)。"""
    head = {"start": "2022-03-01", "end": "2022-06-01",
            "ret_start": "2022-06-02", "ret_end": "2022-07-22",
            "entry_filter": "reversal_k", "rank_score": "none",
            "feature_scores": rb.DEFAULT_FEATURES, "delisted_ret": -1.0,
            "universe": "sdata", "records": []}
    head.update(over)
    return json.dumps(head, ensure_ascii=False)


class TestGapGuards:
    def test_overlaps_gap_boundaries(self):
        assert rb.overlaps_gap("2020-09-01", "2020-09-28") is True     # 触到缺口起点
        assert rb.overlaps_gap("2021-07-30", "2021-08-10") is True     # 触到缺口终点
        assert rb.overlaps_gap("2020-01-01", "2020-09-27") is False
        assert rb.overlaps_gap("2021-07-31", "2021-12-31") is False
        assert rb.overlaps_gap("2020-01-01", "2022-01-01") is True     # 完全跨越

    def test_signal_window_in_gap_dropped(self):
        keep, drop = rb.usable_pairs([_pair(sig=("2020-07-16", "2020-11-20"),
                                            lab=("2020-11-23", "2021-01-25"))])
        assert keep == [] and "缺口" in drop[0]["reason"]

    def test_label_window_beyond_data_end_dropped(self):
        keep, drop = rb.usable_pairs([_pair(sig=("2026-02-02", "2026-04-21"),
                                            lab=("2026-04-22", "2026-06-05"))])
        assert keep == [] and "数据末尾" in drop[0]["reason"]

    def test_clean_pair_kept(self):
        keep, drop = rb.usable_pairs([_pair()])
        assert len(keep) == 1 and drop == []


class TestCommands:
    def test_pass1_decouples_windows_and_keeps_delisted(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(rb.bt, "load_amv_regime", lambda since=None: {"2022-01-03": "空头"})
        monkeypatch.setattr(rb.lp, "bear_to_long_pairs", lambda *a, **k: [_pair()])
        rc = rb.main(["--dry-run", "--out-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "--start 2022-03-01 --end 2022-06-01" in out          # 信号窗
        assert "--ret-start 2022-06-02 --ret-end 2022-07-22" in out  # 赢家窗(解耦)
        assert "--delisted-ret -1.0" in out                          # 去幸存者偏差
        assert "--universe-sdata" in out                             # §5 含退市宇宙准入门槛
        assert "--label-basis winner" in out and "--per-window" in out

    def test_empty_regime_exits_2(self, monkeypatch, capsys):
        monkeypatch.setattr(rb.bt, "load_amv_regime", lambda since=None: {})
        assert rb.main(["--dry-run"]) == 2

    def test_pairs_file_reused(self, tmp_path, capsys):
        pf = tmp_path / "pairs.json"
        pf.write_text(json.dumps({"window_pairs": [_pair()]}), encoding="utf-8")
        rc = rb.main(["--dry-run", "--pairs-file", str(pf), "--out-dir", str(tmp_path)])
        assert rc == 0 and "1 对可用" in capsys.readouterr().out

    def test_few_windows_warns_about_statistical_power(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(rb.bt, "load_amv_regime", lambda since=None: {"2022-01-03": "空头"})
        monkeypatch.setattr(rb.lp, "bear_to_long_pairs", lambda *a, **k: [_pair()])
        rb.main(["--dry-run", "--out-dir", str(tmp_path)])
        assert "统计力很弱" in capsys.readouterr().err


class TestSurvivorshipReport:
    """去偏体检:判据是"样本里有多少当时在、今天已摘牌的票",**不是** n_delisted。

    n_delisted 只统计赢家窗内彻底没价格的票;A 股退市是慢流程,正好死在 20~70 交易日窗口内本就
    稀有 ⇒ 2019 年后各窗 n_delisted=0 是预期行为,不能推出"退市股被剔除"。
    """

    def _firings(self, tmp_path, name, codes, with_sig=(), delisted=()):
        recs = [{"code": c, "ret": 0.1,
                 "days": [["2022-06-01", 0.0, {"f_x": 1.0}]] if c in with_sig else [],
                 **({"delisted": True} if c in delisted else {})} for c in codes]
        p = tmp_path / name
        p.write_text(json.dumps({"start": "2022-05-06", "end": "2022-06-02",
                                 "records": recs}, ensure_ascii=False), encoding="utf-8")
        return p

    def test_gone_cohort_counted_per_window(self, tmp_path, monkeypatch):
        f = self._firings(tmp_path, "w1.json", ["600000", "000001", "900001"],
                          with_sig=("600000", "900001"))
        import s_data
        monkeypatch.setattr(s_data, "list_universe",
                            lambda root, source="qlib": ["600000", "000001", "900001"])
        rep = rb.survivorship_report([f], "/tmp/sroot", today_codes={"600000", "000001"})
        assert rep["gone_pool"] == 1                                  # 900001 今天已没有
        w = rep["windows"][0]
        assert w["n_codes"] == 3 and w["n_gone_in_sample"] == 1
        assert w["n_gone_with_signal"] == 1 and w["n_delisted_flag"] == 0
        assert "✅" in rep["text"] and "飞刀留在样本内" in rep["text"]

    def test_empty_gone_pool_flags_debias_invalid(self, tmp_path, monkeypatch):
        """宇宙里一只已摘牌股都没有 ⇒ 去偏无效,结论只能当乐观上界(§3 首条)。"""
        f = self._firings(tmp_path, "w1.json", ["600000"], with_sig=("600000",))
        import s_data
        monkeypatch.setattr(s_data, "list_universe", lambda root, source="qlib": ["600000"])
        rep = rb.survivorship_report([f], "/tmp/sroot", today_codes={"600000"})
        assert rep["gone_pool"] == 0
        assert "去偏无效" in rep["text"] and "乐观上界" in rep["text"]

    def test_window_without_gone_stock_flagged(self, tmp_path, monkeypatch):
        f1 = self._firings(tmp_path, "w1.json", ["600000", "900001"], with_sig=("900001",))
        f2 = self._firings(tmp_path, "w2.json", ["600000"], with_sig=("600000",))
        import s_data
        monkeypatch.setattr(s_data, "list_universe",
                            lambda root, source="qlib": ["600000", "900001"])
        rep = rb.survivorship_report([f1, f2], "/tmp/sroot", today_codes={"600000"})
        assert [w["n_gone_in_sample"] for w in rep["windows"]] == [1, 0]
        assert "1 个窗的样本里一只已摘牌股都没有" in rep["text"]

    def test_n_delisted_zero_is_explained_as_expected(self, tmp_path, monkeypatch):
        f = self._firings(tmp_path, "w1.json", ["600000", "900001"], with_sig=("900001",))
        import s_data
        monkeypatch.setattr(s_data, "list_universe",
                            lambda root, source="qlib": ["600000", "900001"])
        rep = rb.survivorship_report([f], "/tmp/sroot", today_codes={"600000"})
        assert "0 不代表去偏失效" in rep["text"]

    def test_unreadable_firings_reported_not_raised(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        import s_data
        monkeypatch.setattr(s_data, "list_universe", lambda root, source="qlib": ["600000"])
        rep = rb.survivorship_report([bad], "/tmp/sroot", today_codes=set())
        assert rep["windows"][0].get("error") and "读取失败" in rep["text"]

    def test_cli_requires_existing_firings(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(rb.bt, "load_amv_regime", lambda since=None: {"2022-01-03": "空头"})
        monkeypatch.setattr(rb.lp, "bear_to_long_pairs", lambda *a, **k: [_pair()])
        rc = rb.main(["--out-dir", str(tmp_path), "--survivorship-report"])
        assert rc == 2 and "没有可体检的 firings" in capsys.readouterr().err


class TestResumeAndPass2:
    def _setup(self, monkeypatch, pairs):
        monkeypatch.setattr(rb.bt, "load_amv_regime", lambda since=None: {"2022-01-03": "空头"})
        monkeypatch.setattr(rb.lp, "bear_to_long_pairs", lambda *a, **k: pairs)

    def test_existing_firings_skipped_unless_force(self, tmp_path, monkeypatch, capsys):
        p = _pair()
        self._setup(monkeypatch, [p])
        (tmp_path / f"firings_{rb.tag_of(p)}.json").write_text(_firings_text(), encoding="utf-8")
        calls = []
        rb.main(["--out-dir", str(tmp_path)], runner=lambda cmd: calls.append(cmd) or 0)
        assert len(calls) == 1 and "--from-firings" in calls[0]        # 只跑了 Pass2
        assert "[skip]" in capsys.readouterr().out

        calls.clear()
        rb.main(["--out-dir", str(tmp_path), "--force"],
                runner=lambda cmd: calls.append(cmd) or 0)
        assert len(calls) == 2 and "--emit-firings" in calls[0]        # 强制重跑 Pass1

    def test_param_mismatch_reruns_instead_of_reuse(self, tmp_path, monkeypatch, capsys):
        """断点续跑不能只认文件名:头部关键参数不一致 → WARN + 重跑(旧参数结果不得复用)。"""
        p = _pair()
        self._setup(monkeypatch, [p])
        (tmp_path / f"firings_{rb.tag_of(p)}.json").write_text(
            _firings_text(entry_filter="kdj_j"), encoding="utf-8")     # 上次用别的 entry_filter 跑的
        calls = []
        rc = rb.main(["--out-dir", str(tmp_path)], runner=lambda cmd: calls.append(cmd) or 0)
        assert rc == 0
        assert len(calls) == 2 and "--emit-firings" in calls[0]        # Pass1 重跑
        err = capsys.readouterr().err
        assert "[WARN]" in err and "entry_filter" in err and "kdj_j" in err
        assert "[skip]" not in capsys.readouterr().out

    def test_feature_scores_mismatch_reruns(self, tmp_path, monkeypatch, capsys):
        p = _pair()
        self._setup(monkeypatch, [p])
        (tmp_path / f"firings_{rb.tag_of(p)}.json").write_text(
            _firings_text(feature_scores="momentum"), encoding="utf-8")
        calls = []
        rb.main(["--out-dir", str(tmp_path)], runner=lambda cmd: calls.append(cmd) or 0)
        assert "--emit-firings" in calls[0]
        assert "feature_scores" in capsys.readouterr().err

    def test_truncated_firings_reruns_not_skipped_nor_crashed(self, tmp_path, monkeypatch, capsys):
        """失败 Pass1 留下的半截 JSON:不得当完成 skip,也不得崩溃 —— WARN 后重跑并纳入 Pass2。"""
        p = _pair()
        self._setup(monkeypatch, [p])
        f = tmp_path / f"firings_{rb.tag_of(p)}.json"
        f.write_text('{"start": "2022-03-01", "records": [{"code": "6000', encoding="utf-8")

        def _runner(cmd):
            if "--emit-firings" in cmd:
                Path(cmd[cmd.index("--emit-firings") + 1]).write_text(_firings_text(),
                                                                      encoding="utf-8")
            return 0

        calls = []
        rc = rb.main(["--out-dir", str(tmp_path)],
                     runner=lambda cmd: (calls.append(cmd), _runner(cmd))[1])
        assert rc == 0
        assert "--emit-firings" in calls[0]                            # 重跑 Pass1
        err = capsys.readouterr().err
        assert "[WARN]" in err and "截断" in err
        pass2 = [c for c in calls if "--from-firings" in c][0]
        assert rb.tag_of(p) in pass2[pass2.index("--from-firings") + 1]

    def test_records_key_missing_treated_as_unfinished(self, tmp_path, monkeypatch, capsys):
        """可解析但缺 records 键(如旧版/误写文件)同样视为未完成。"""
        p = _pair()
        self._setup(monkeypatch, [p])
        head = json.loads(_firings_text())
        del head["records"]
        (tmp_path / f"firings_{rb.tag_of(p)}.json").write_text(
            json.dumps(head, ensure_ascii=False), encoding="utf-8")
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

    def test_failed_pass1_excluded_from_pass2_but_others_continue(self, tmp_path, monkeypatch):
        p1, p2 = _pair(), _pair(sig=("2023-01-03", "2023-02-10"), lab=("2023-02-13", "2023-05-11"))
        self._setup(monkeypatch, [p1, p2])

        def _runner(cmd):
            if "--emit-firings" in cmd:
                out = cmd[cmd.index("--emit-firings") + 1]
                if rb.tag_of(p1) in out:
                    return 1                              # 第一窗失败
                Path(out).write_text("{}", encoding="utf-8")
            return 0

        calls = []
        rc = rb.main(["--out-dir", str(tmp_path)],
                     runner=lambda cmd: (calls.append(cmd), _runner(cmd))[1])
        pass2 = [c for c in calls if "--from-firings" in c][0]
        files = pass2[pass2.index("--from-firings") + 1]
        assert rc == 1                                    # 整体标失败
        assert rb.tag_of(p2) in files and rb.tag_of(p1) not in files   # 失败窗被排除,其余继续

    def test_pass2_only_skips_pass1(self, tmp_path, monkeypatch):
        p = _pair()
        self._setup(monkeypatch, [p])
        (tmp_path / f"firings_{rb.tag_of(p)}.json").write_text("{}", encoding="utf-8")
        calls = []
        rb.main(["--out-dir", str(tmp_path), "--pass2-only"],
                runner=lambda cmd: calls.append(cmd) or 0)
        assert len(calls) == 1 and "--from-firings" in calls[0]
