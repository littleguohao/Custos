# -*- coding: utf-8 -*-
"""进场信号缓存 + 出场重放（--signals-out / --from-signals）测试。

存在理由：M2 扫描里「只改出场参数」的方案组，逐 bar 进场扫描（占评估耗时 ~99%，
实测 1000 只票评估 1238s）被原样重跑 N 遍。信号序列与出场参数无关 ⇒ 扫一次落盘、
其余方案 --from-signals 重放。

两条红线都在这里钉死：

  ① **重放必须逐位一致**：同参数全量跑 vs 重放的 trades 全字段相等——
     两阶段（扫描→发射）与单遍循环有任何分叉都会在这里炸出来。
  ② **签名覆盖必须全**：改动任一信号轴参数，signals_signature 必须变；
     改动任一出场参数，必须不变（否则复用永远被拒、机制失效）。
"""

import json as _json
import os as _os
import types as _types
from pathlib import Path as _Path

import pandas as pd
import pytest

from custos.research import backtest_factors as bt
from custos.research import m2_stop_sweep as m2


def _mk(closes, seed_jitter=0.0):
    """合成日线：close 序列 + 窄幅 high/low。长度 > min_bars(30)+2 才会有交易。"""
    n = len(closes)
    closes = [float(x) for x in closes]
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
            "amount": [0.0] * n,
        }
    )


def _wave(n, lo=9.0, hi=12.0):
    """锯齿波收盘价：有涨有跌，能走出 stop/bbi_exit/open_end 多种离场。"""
    out, v, up = [], lo, True
    for _ in range(n):
        out.append(v)
        v += 0.3 if up else -0.25
        if v >= hi:
            up = False
        elif v <= lo:
            up = True
    return out


BARS = {
    "600000": _mk(_wave(80)),
    "000001": _mk(_wave(70, lo=20.0, hi=23.0)),
    "300750": _mk([10.0 + 0.1 * i for i in range(60)]),  # 单调上行
}

_STUB = lambda s, code: {"score": 100, "suggestion": "可买"}  # noqa: E731

# 覆盖的出场参数组合：每套都要「全量 == 重放」逐位一致
_EXIT_COMBOS = [
    {},  # 基线（low 止损 + bbi_consec 2）
    {"stop_mode": "pct", "stop_pct": 5.0},
    {"stop_mode": "pct", "stop_pct": 12.0, "trail_pct": 0.08},
    {"bbi_exit_consec": 4, "time_stop_bars": 7},
    {"scale_out_frac": 0.5},
    {"scale_out_frac": 0.0},
    {"breakeven_trigger": 0.05, "stop_trigger": "intraday"},
    {"stop_tick_buffer": 3},
    {"stop_buffer": "pct", "stop_pct_buffer": 0.5},
    {"stop_buffer": "atr", "stop_atr_buffer": 0.3},
    {"cost_zone_bars": 3, "cost_zone_pct": 2.0},
    {"cost_bps": 25.0},  # 成本不进信号、重放时按本次参数扣
    {"collect_all": True},  # 全候选口径（--top-n）
    {"max_signals_per_code": 2},  # 单股上限在发射阶段生效，不进信号
]


def _replay_signals(sink):
    """``--signals-out`` 落盘口径 → ``signals_in`` 的 {code: [...]} 映射。"""
    by_code = {}
    for s in sink:
        by_code.setdefault(s["code"], []).append(s)
    return by_code


class TestReplayBitIdentical:
    """红线①：同参数全量跑 vs 信号重放，trades 必须**逐位一致**。"""

    @pytest.mark.parametrize("kw", _EXIT_COMBOS, ids=[str(k) for k in _EXIT_COMBOS])
    def test_full_vs_replay_identical(self, kw):
        full = bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30, **kw)
        assert full, f"{kw} 下应有交易，否则钉测形同虚设"
        sink = []
        bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30, signals_out=sink)  # 生产
        replay = bt.evaluate_trades(
            BARS, scorer=_STUB, min_bars=30, signals_in=_replay_signals(sink), **kw
        )
        assert replay == full, f"{kw}：重放与全量不逐位一致"

    def test_producer_trades_identical_to_plain_run(self):
        """生产方（两阶段：全 bar 扫描 + 发射）自己的 trades 也要与单遍全量逐位一致。"""
        plain = bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30)
        sink = []
        produced = bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30, signals_out=sink)
        assert produced == plain
        # 信号记录带齐重放所需字段
        assert sink and all({"code", "i", "date", "score"} <= set(s) for s in sink)

    def test_producer_collect_all_identical(self):
        plain = bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30, collect_all=True)
        sink = []
        produced = bt.evaluate_trades(
            BARS, scorer=_STUB, min_bars=30, collect_all=True, signals_out=sink
        )
        assert produced == plain and len(sink) == len(plain)  # collect_all 全候选

    def test_replay_with_gate_and_weekly(self):
        """gate/weekly 不是装饰：重放路径同样适用（扫描跳过，发射不变）。"""
        gate = lambda s: len(s) % 2 == 0  # noqa: E731
        full = bt.evaluate_trades(
            BARS, scorer=_STUB, min_bars=30, entry_gate=gate, weekly=True
        )
        sink = []
        bt.evaluate_trades(
            BARS,
            scorer=_STUB,
            min_bars=30,
            entry_gate=gate,
            weekly=True,
            signals_out=sink,
        )
        replay = bt.evaluate_trades(
            BARS,
            scorer=_STUB,
            min_bars=30,
            entry_gate=gate,
            weekly=True,
            signals_in=_replay_signals(sink),
        )
        assert replay == full

    def test_step_not_1_rejected(self):
        """step>1 时续扫起点离网，两阶段无法逐位复现 ⇒ fail-closed，不猜。"""
        with pytest.raises(ValueError, match="step=1"):
            bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30, step=2, signals_out=[])
        with pytest.raises(ValueError, match="step=1"):
            bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30, step=2, signals_in={})


# ---------------------------------------------------------------------------
# signals_signature（红线②）
# ---------------------------------------------------------------------------


def _args(**kw):
    base = dict(
        scorer="b1_dual",
        weekly=False,
        step=1,
        entry_filter="j_low",
        amv_long_only=False,
        sector_filter=False,
        start="",
        end="",
        count=500,
        top_n=0,
    )
    base.update(kw)
    return _types.SimpleNamespace(**base)


CODES = ["600000", "000001", "300750"]


class TestSignalsSignature:
    def test_signal_axis_params_change_signature(self):
        """信号轴参数任一改动 ⇒ 签名必须变（漏一个 = 静默错用别的口径的信号）。"""
        base = bt.signals_signature(_args(), CODES)
        for kw in (
            {"scorer": "s_shape"},
            {"entry_filter": "none"},
            {"weekly": True},
            {"step": 2},
            {"amv_long_only": True},
            {"sector_filter": True},
            {"start": "2022-01-01"},
            {"end": "2024-12-31"},
            {"count": 1500},
            {"top_n": 2},  # collect_all 口径变
        ):
            assert bt.signals_signature(_args(**kw), CODES) != base, f"{kw} 未进签名"

    def test_exit_params_keep_signature(self):
        """出场/组合层参数**不能**进签名——否则「只改出场」的方案组永远复用不上。"""
        base = bt.signals_signature(_args(), CODES)
        for kw in (
            {"stop_pct": 5.0},
            {"trail": 0.08},
            {"bbi_consec": 4},
            {"scale_out": 0.0},
            {"cost_bps": 25.0},
            {"risk_pct": 2.0},
            {"max_concurrent": 2},
        ):
            assert bt.signals_signature(_args(**kw), CODES) == base, f"{kw} 不该进签名"

    def test_topn_value_irrelevant_but_presence_matters(self):
        """top_n 的取值不改信号（都是全候选），>0 与否改口径。"""
        assert bt.signals_signature(_args(top_n=2), CODES) == bt.signals_signature(
            _args(top_n=3), CODES
        )
        assert (
            bt.signals_signature(_args(top_n=0), CODES)["collect_all"]
            != bt.signals_signature(_args(top_n=2), CODES)["collect_all"]
        )

    def test_universe_in_signature(self):
        assert bt.signals_signature(_args(), CODES) != bt.signals_signature(
            _args(), CODES + ["600519"]
        )


# ---------------------------------------------------------------------------
# CLI 端到端（注入合成 loader）
# ---------------------------------------------------------------------------

_CLI = [
    "--codes",
    "600000,000001,300750",
    "--count",
    "500",
    "--trade-sim",
    "--scorer",
    "baseline",
    "--entry-filter",
    "none",
    "--allow-empty",  # 合成数据可能 0 笔（如 bbi 恒 NaN 的极端形状），不拦落盘
]


def _loader(codes, count):
    return {c: BARS[c].copy() for c in codes if c in BARS}


class TestSignalsCLI:
    def test_dump_then_replay_bit_identical(self, tmp_path):
        """端到端钉测：--signals-out 落盘 → --from-signals 重放，
        trades 与同参数全量跑逐位一致（含出场参数不同的组合）。"""
        sig = tmp_path / "sig.json"
        out_full = tmp_path / "full.json"
        out_prod = tmp_path / "prod.json"
        rc = bt.main(
            _CLI + ["--stop-mode", "pct", "--stop-pct", "5", "--out", str(out_full)],
            loader=_loader,
        )
        assert rc == 0
        # 生产方：基线出场参数全量跑 + 落信号
        rc = bt.main(
            _CLI + ["--signals-out", str(sig), "--out", str(out_prod)], loader=_loader
        )
        assert rc == 0
        d_sig = _json.loads(sig.read_text(encoding="utf-8"))
        assert d_sig["mode"] == "entry_signals"
        assert isinstance(d_sig["signals_signature"], dict) and d_sig["signals"]
        # 重放：stop_mode/stop_pct 与生产方不同（这正是重放的意义）
        out_replay = tmp_path / "replay.json"
        rc = bt.main(
            _CLI
            + [
                "--from-signals",
                str(sig),
                "--stop-mode",
                "pct",
                "--stop-pct",
                "5",
                "--out",
                str(out_replay),
            ],
            loader=_loader,
        )
        assert rc == 0
        full_trades = _json.loads(out_full.read_text(encoding="utf-8"))["trades"]
        assert full_trades, "全量跑 0 笔交易 ⇒ 钉测形同虚设"
        replay = _json.loads(out_replay.read_text(encoding="utf-8"))
        assert replay["trades"] == full_trades, "重放 trades 必须与全量逐位一致"
        assert replay["signals_reused_from"] == sig.name
        # 生产方自己的 trades 也要等于同参数全量
        plain = tmp_path / "plain.json"
        bt.main(_CLI + ["--out", str(plain)], loader=_loader)
        prod = _json.loads(out_prod.read_text(encoding="utf-8"))
        assert (
            prod["trades"] == _json.loads(plain.read_text(encoding="utf-8"))["trades"]
        )
        assert prod["signals_dumped_to"] == sig.name

    def test_replay_rejects_signal_axis_mismatch(self, tmp_path, capsys):
        """信号轴参数不同（entry_filter 变了）⇒ 拒绝复用、不落盘、非零退出。"""
        sig = tmp_path / "sig.json"
        assert bt.main(_CLI + ["--signals-out", str(sig)], loader=_loader) == 0
        out = tmp_path / "o.json"
        i = _CLI.index("--entry-filter")
        bad = _CLI[: i + 1] + ["j_low"] + _CLI[i + 2 :]  # 换掉 entry-filter 的值
        rc = bt.main(
            bad + ["--from-signals", str(sig), "--out", str(out)],
            loader=_loader,
        )
        assert rc != 0
        assert not out.exists()
        err = capsys.readouterr().err
        assert "口径" in err and "entry_filter" in err

    def test_replay_rejects_missing_and_unsigned(self, tmp_path, capsys):
        rc = bt.main(
            _CLI + ["--from-signals", str(tmp_path / "nope.json")], loader=_loader
        )
        assert rc != 0 and "不存在" in capsys.readouterr().err
        old = tmp_path / "old.json"
        old.write_text(_json.dumps({"signals": []}), encoding="utf-8")
        rc = bt.main(_CLI + ["--from-signals", str(old)], loader=_loader)
        assert rc != 0 and "signals_signature" in capsys.readouterr().err

    def test_from_signals_and_from_trades_mutex(self, tmp_path):
        with pytest.raises(SystemExit):
            bt.main(
                _CLI + ["--from-signals", "a.json", "--from-trades", "b.json"],
                loader=_loader,
            )

    def test_signals_require_step_1(self, tmp_path):
        with pytest.raises(SystemExit):
            bt.main(
                _CLI + ["--step", "2", "--signals-out", str(tmp_path / "s.json")],
                loader=_loader,
            )
        with pytest.raises(SystemExit):
            bt.main(
                _CLI + ["--step", "2", "--from-signals", str(tmp_path / "s.json")],
                loader=_loader,
            )


# ---------------------------------------------------------------------------
# m2 调度层：信号复用分组
# ---------------------------------------------------------------------------


class TestM2SignalReusePlan:
    def _todo(self):
        return [
            (g, n, e) for g, meta in m2.GROUPS.items() for n, e in meta["runs"].items()
        ]

    def test_exit_only_diffs_share_one_scan(self):
        """A 组 00_baseline 是生产者；只改出场参数的方案全部成为它的重放。"""
        plan = m2._plan_signal_reuse(self._todo(), "s1000")
        prod = {k for k, (r, _p) in plan.items() if r == "producer"}
        assert ("A_stop_low", "00_baseline") in prod
        for tag in (
            ("A_stop_low", "trail_08"),
            ("A_stop_low", "scale_out_0"),
            ("B_stop_pct", "pct_05"),  # 跨组共享：stop-mode 是出场参数，不改信号
            ("B_stop_pct", "pct_12"),
        ):
            assert plan[tag][0] == "replay", f"{tag} 应重放"
            assert plan[tag][1] == plan[("A_stop_low", "00_baseline")][1], (
                f"{tag} 与基准应共享同一份信号文件"
            )

    def test_entry_axis_change_splits_cluster(self):
        """--amv-long-only 改信号集 ⇒ 单独一簇（生产者 A/amv_long_only）。"""
        plan = m2._plan_signal_reuse(self._todo(), "s1000")
        amv_producer = ("A_stop_low", "amv_long_only")
        assert plan[amv_producer][0] == "producer"
        assert plan[("B_stop_pct", "pct_05_amv")][1] == plan[amv_producer][1]
        assert plan[amv_producer][1] != plan[("A_stop_low", "00_baseline")][1]

    def test_trades_layer_reuse_excluded(self):
        """C 组 --from-trades 复用是毫秒级，优先于信号层重放 ⇒ 不参与分组。"""
        plan = m2._plan_signal_reuse(self._todo(), "s1000")
        for name in m2.GROUPS["C_portfolio"]["reuse"]:
            assert ("C_portfolio", name) not in plan

    def test_topn_collect_all_own_cluster(self):
        """--top-n 走 collect_all（另一套信号口径）⇒ 不与非 top-n 同簇。"""
        plan = m2._plan_signal_reuse(self._todo(), "s1000")
        topn = ("C_portfolio", "pf_top2_c2_amv")  # 唯一非 reuse 的 top-n 方案
        assert topn not in plan, "单方案不成簇（没有复用对象）"
        base_key = m2._signal_key("A_stop_low", "00_baseline")
        topn_key = m2._signal_key(*topn)
        assert base_key != topn_key

    def test_singleton_no_plan(self):
        """--only 单跑一个方案时没有复用对象 ⇒ 不产信号文件，全量跑。"""
        plan = m2._plan_signal_reuse([("A_stop_low", "trail_08", [])], "s1000")
        assert plan == {}

    def test_signal_key_ignores_exit_values(self):
        """出场参数的**取值**不进 key（pct_05 与 pct_12 同簇）；开关取值进。"""
        assert m2._signal_key("B_stop_pct", "pct_05") == m2._signal_key(
            "B_stop_pct", "pct_12"
        )
        assert m2._signal_key("A_stop_low", "00_baseline") != m2._signal_key(
            "A_stop_low", "amv_long_only"
        )

    def test_path_pins_fingerprint(self):
        """信号文件名含批次指纹：跨样本量/窗口的信号缓存不会互相误用。"""
        k = m2._signal_key("A_stop_low", "00_baseline")
        assert m2._signals_path(k, "s1000") != m2._signals_path(k, "s3000")

    def test_run_all_wires_signals_out_then_from_signals(
        self, tmp_path, monkeypatch, capsys
    ):
        """端到端接线：生产者的子进程命令带 --signals-out，重放带 --from-signals，
        且生产者**先于**重放执行（信号文件必须先落盘）。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            out = next(x for i, x in enumerate(cmd) if cmd[i - 1] == "--out")
            _Path(out).write_text("{}", encoding="utf-8")
            return type("R", (), {"returncode": 0, "stdout": ""})()

        monkeypatch.setattr(m2.subprocess, "run", fake_run)
        m2._run_all(
            [
                ("A_stop_low", "00_baseline", []),
                ("A_stop_low", "be_03", ["--breakeven", "0.03"]),
                ("A_stop_low", "trail_08", ["--trail", "0.08"]),
            ],
            1000,
            False,
            False,
            1,
        )
        assert len(calls) == 3

        def _flag(cmd, name):
            return cmd[cmd.index(name) + 1] if name in cmd else None

        sig_path = _flag(calls[0], "--signals-out")
        assert sig_path and "_signals__s1000" in sig_path, "生产者必须落盘信号"
        assert "--from-signals" not in calls[0]
        for cmd in calls[1:]:
            assert _flag(cmd, "--from-signals") == sig_path, "重放必须指同一份信号"
            assert "--signals-out" not in cmd
        out = capsys.readouterr().out
        assert "信号层复用" in out and "2 个方案" in out


# ---------------------------------------------------------------------------
# review 缺口钉测：互斥 / 日期对账 / 缺条目 WARN / 缓存清理
# ---------------------------------------------------------------------------


class TestSignalsOutFromSignalsMutex:
    def test_signals_out_and_from_signals_mutex(self):
        """--signals-out 与 --from-signals 同给 ⇒ 互斥报错（非零退出）。

        同给时重放优先 ⇒ 扫描不执行、sink 为空却仍落盘；两旗标同路径会把
        源信号缓存覆盖成空文件（签名却合法，下游照用不误）——fail-closed。"""
        with pytest.raises(SystemExit) as exc:
            bt.main(
                _CLI + ["--signals-out", "a.json", "--from-signals", "b.json"],
                loader=_loader,
            )
        assert exc.value.code != 0


class TestReplayDateReconcile:
    """数据更新后同一 bar 索引可能已是别的交易日：重放必须对账信号 date，
    对不上 fail-closed（拼出从未存在的 trades 是最难发现的错法）。"""

    def test_date_mismatch_raises_with_context(self):
        """库层：信号 date != 现数据 dates[i] ⇒ ValueError，
        信息含 code / bar 索引 / 文件日期 / 现数据日期。"""
        sink = []
        bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30, signals_out=sink)
        sigs = _replay_signals(sink)
        sigs["600000"][0]["date"] = "1999-12-31"  # 绝不等于现数据日期
        with pytest.raises(
            ValueError, match=r"600000 bar \d+: 信号文件日期 1999-12-31"
        ):
            bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30, signals_in=sigs)

    def test_date_drift_cli_fail_closed(self, tmp_path, capsys):
        """CLI 层：整体日期漂移（数据已更新）⇒ [FAIL] 提示重新 --signals-out，
        非零退出、不落盘（m2 会把非零退出当失败自动退回全量扫描）。"""
        sig = tmp_path / "sig.json"
        assert bt.main(_CLI + ["--signals-out", str(sig)], loader=_loader) == 0

        def drifted_loader(codes, count):
            # 同价位、同长度，仅日期整体后移一天 ⇒ bar 索引不变、日期全漂移
            out = {}
            for c in codes:
                if c not in BARS:
                    continue
                df = BARS[c].copy()
                df["date"] = df["date"] + pd.Timedelta(days=1)
                out[c] = df
            return out

        out = tmp_path / "o.json"
        rc = bt.main(
            _CLI + ["--from-signals", str(sig), "--out", str(out)],
            loader=drifted_loader,
        )
        assert rc != 0
        assert not out.exists()
        err = capsys.readouterr().err
        assert "日期对不上" in err and "重新 --signals-out" in err

    def test_date_match_replay_unaffected(self):
        """日期一致 ⇒ 对账不误伤：重放照常，trades 与全量逐位一致。"""
        full = bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30)
        assert full, "全量 0 笔 ⇒ 钉测形同虚设"
        sink = []
        bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30, signals_out=sink)
        replay = bt.evaluate_trades(
            BARS, scorer=_STUB, min_bars=30, signals_in=_replay_signals(sink)
        )
        assert replay == full

    def test_out_of_range_signals_warn(self, capsys):
        """文件里 i<min_bars 或 i>=n-1 的条目被丢弃 ⇒ 一行 WARN（含 code 与
        条数），不再静默；区间内的信号照常发射、trades 不受影响。"""
        sink = []
        bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30, signals_out=sink)
        sigs = _replay_signals(sink)
        sigs["600000"] += [
            {"code": "600000", "i": 0, "date": "2023-12-29", "score": 1},  # i<30
            {"code": "600000", "i": 10**6, "date": "2099-01-01", "score": 1},  # i>=n-1
        ]
        replay = bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30, signals_in=sigs)
        err = capsys.readouterr().err
        assert "[WARN]" in err and "600000" in err and "2 条" in err
        full = bt.evaluate_trades(BARS, scorer=_STUB, min_bars=30)
        assert replay == full


class TestReplayMissingCodeWarn:
    def test_missing_code_warns_and_trades_clean(self, capsys):
        """股票加载成功但信号文件无条目 ⇒ WARN 列出该 code（与「本就零信号」
        区分开）；有条目的股票重放结果不受污染（与单股全量逐位一致）。"""
        only = {"600000": BARS["600000"]}
        sink = []
        bt.evaluate_trades(only, scorer=_STUB, min_bars=30, signals_out=sink)
        replay = bt.evaluate_trades(
            BARS, scorer=_STUB, min_bars=30, signals_in=_replay_signals(sink)
        )
        err = capsys.readouterr().err
        assert "[WARN]" in err and "000001" in err and "300750" in err
        assert "600000" not in err  # 有条目的不在 WARN 里
        solo = bt.evaluate_trades(only, scorer=_STUB, min_bars=30)
        assert replay == solo  # 缺条目股零交易，其余逐位一致

    def test_missing_codes_truncated_over_10(self, capsys):
        """缺条目股票超过 10 只 ⇒ WARN 只列前 10 只并注明总数。"""
        bars = {f"60{i:04d}": _mk(_wave(50)) for i in range(12)}
        bt.evaluate_trades(bars, scorer=_STUB, min_bars=30, signals_in={})
        err = capsys.readouterr().err
        assert "12 只股票" in err and "等共 12 只" in err
        assert "600009" in err  # 前 10 只列出
        assert "600010" not in err  # 第 11 只起截断


class TestSignalsFilePrune:
    def test_old_signal_files_pruned_to_keep_32(self, tmp_path):
        """信号缓存只增不减 ⇒ 写新文件后同目录同前缀旧文件按 mtime 留最新
        32 个（新文件不参与淘汰），其余清掉。"""
        for k in range(35):
            p = tmp_path / f"_signals__test__old{k:02d}.json"
            p.write_text("{}", encoding="utf-8")
            ts = 1_700_000_000 + k  # 递增 mtime，old00 最旧
            _os.utime(p, (ts, ts))
        new = tmp_path / "_signals__test__new.json"
        rc = bt.main(_CLI + ["--signals-out", str(new)], loader=_loader)
        assert rc == 0
        left = {p.name for p in tmp_path.glob("_signals__*.json")}
        assert len(left) == 33  # 32 个最新旧文件 + 1 个新文件
        assert new.name in left
        for k in range(3):  # 被清掉的是 mtime 最旧的 3 个
            assert f"_signals__test__old{k:02d}.json" not in left
        assert "_signals__test__old03.json" in left
