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


class TestResumeAndPass2:
    def _setup(self, monkeypatch, pairs):
        monkeypatch.setattr(rb.bt, "load_amv_regime", lambda since=None: {"2022-01-03": "空头"})
        monkeypatch.setattr(rb.lp, "bear_to_long_pairs", lambda *a, **k: pairs)

    def test_existing_firings_skipped_unless_force(self, tmp_path, monkeypatch, capsys):
        p = _pair()
        self._setup(monkeypatch, [p])
        (tmp_path / f"firings_{rb.tag_of(p)}.json").write_text("{}", encoding="utf-8")
        calls = []
        rb.main(["--out-dir", str(tmp_path)], runner=lambda cmd: calls.append(cmd) or 0)
        assert len(calls) == 1 and "--from-firings" in calls[0]        # 只跑了 Pass2
        assert "[skip]" in capsys.readouterr().out

        calls.clear()
        rb.main(["--out-dir", str(tmp_path), "--force"],
                runner=lambda cmd: calls.append(cmd) or 0)
        assert len(calls) == 2 and "--emit-firings" in calls[0]        # 强制重跑 Pass1

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
