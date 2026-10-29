"""v0.461 逐笔重放备忘：fes.replay_signals 开 / 关备忘逐位一致；封顶后照常正确。"""

import random

import numpy as np
import pandas as pd
import pytest

from custos.research import factor_exit_study as fes
from custos.research import plan_rules_replay as prr


def _df(seed, n=900):
    rng = np.random.RandomState(seed)
    d = pd.bdate_range("2010-01-01", periods=n).strftime("%Y-%m-%d")
    c = 10 * np.exp(np.cumsum(rng.normal(0, 0.025, n)))
    return pd.DataFrame(
        {
            "date": d,
            "open": c * (1 + rng.normal(0, 0.004, n)),
            "high": c * 1.025,
            "low": c * 0.975,
            "close": c,
            "volume": 1e6,
            "amount": 1e7,
        }
    )


@pytest.fixture(autouse=True)
def _off():
    fes.enable_replay_memo(0)
    yield
    fes.enable_replay_memo(0)


def _world():
    return {f"{k:06d}": {"df": _df(k)} for k in range(5)}


def _subset(per_code, rng, n=120, stop=False):
    out = []
    for _ in range(n):
        c = rng.choice(sorted(per_code))
        df = per_code[c]["df"]
        i = rng.randrange(250, len(df) + 3)  # 含越界 bar（不出交易）
        i = min(i, len(df) - 1)
        sig = {"i": i, "date": df["date"][i]}
        if stop and rng.random() < 0.5:
            sig["stop_override"] = float(df["low"][i]) * 0.95
        out.append(
            {
                "code": c,
                "date": df["date"][i],
                "i": i,
                "sig": sig,
                "score": rng.random() * 100,
            }
        )
    return out


RG: dict = {}  # regime 按对象身份进键——生产路径整轮同一对象
PARAMS = [prr.LIVE_PARAMS, {**prr.LIVE_PARAMS, "stop_pct": 5.0}]


@pytest.mark.parametrize("stop", [False, True])
def test_memo_bit_identical_across_calls(stop):
    pc = _world()
    rng = random.Random(7)
    calls = [(_subset(pc, rng, stop=stop), p) for _ in range(6) for p in PARAMS]
    ref = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    memo = fes.enable_replay_memo(1_000_000)
    got = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    assert got == ref
    assert memo.hits > 0 and memo.misses > 0
    again = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    assert again == ref  # 全命中路径同样一致


def test_cap_reached_still_correct():
    pc = _world()
    rng = random.Random(3)
    calls = [(_subset(pc, rng), PARAMS[0]) for _ in range(5)]
    ref = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    memo = fes.enable_replay_memo(50)
    got = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    assert got == ref and memo.n_entries == 50


def test_distinct_df_objects_not_shared():
    """同 code 不同 df 对象（v2 分段 per_code）不得串用。"""
    a = {"000001": {"df": _df(1)}}
    b = {"000001": {"df": _df(2)}}
    s = [
        {
            "code": "000001",
            "date": a["000001"]["df"]["date"][400],
            "i": 400,
            "sig": {"i": 400, "date": a["000001"]["df"]["date"][400]},
            "score": 1.0,
        }
    ]
    ra = fes.replay_signals(a, s, PARAMS[0], RG, 25.0)
    rb = fes.replay_signals(b, s, PARAMS[0], RG, 25.0)
    fes.enable_replay_memo(1000)
    assert fes.replay_signals(a, s, PARAMS[0], RG, 25.0) == ra
    assert fes.replay_signals(b, s, PARAMS[0], RG, 25.0) == rb


# ── v0.464 落盘层 ─────────────────────────────────────────────


def _calls(pc, seed=11, stop=True):
    rng = random.Random(seed)
    return [(_subset(pc, rng, stop=stop), p) for _ in range(4) for p in PARAMS]


def test_disk_roundtrip_bit_identical_across_processes(tmp_path):
    """第一轮落盘，第二轮（新备忘、新 df 对象 = 模拟新进程）全部从盘读出，结果逐位一致。"""
    pc = _world()
    calls = _calls(pc)
    ref = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    m1 = fes.enable_replay_memo(1_000_000, tmp_path)
    assert [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls] == ref
    fes.enable_replay_memo(0)  # 换掉前自动 flush
    assert m1.disk.written > 0 and list(tmp_path.rglob("*.jsonl"))
    pc2 = _world()  # 内容相同的新对象
    rg2: dict = {}
    m2 = fes.enable_replay_memo(1_000_000, tmp_path)
    got = [fes.replay_signals(pc2, s, p, rg2, 25.0) for s, p in calls]
    assert got == ref
    assert m2.misses == 0 and m2.disk.loaded > 0


def test_disk_keys_on_content_cost_and_params(tmp_path):
    """数据内容 / 成本 / 参数任一不同 ⇒ 不同表，不得串用。"""
    pc = _world()
    calls = _calls(pc, stop=False)[:2]
    fes.enable_replay_memo(1_000_000, tmp_path)
    for s, p in calls:
        fes.replay_signals(pc, s, p, RG, 25.0)
    fes.enable_replay_memo(0)
    pc_alt = {
        c: {"df": v["df"].assign(close=v["df"]["close"] * 1.01)} for c, v in pc.items()
    }
    for world, cost in ((pc_alt, 25.0), (pc, 50.0)):
        ref = [fes.replay_signals(world, s, p, RG, cost) for s, p in calls]
        m = fes.enable_replay_memo(1_000_000, tmp_path)
        assert [fes.replay_signals(world, s, p, RG, cost) for s, p in calls] == ref
        assert m.disk.loaded == 0, "内容 / 成本不同 ⇒ 不同表"
        fes.enable_replay_memo(0)


def test_disk_truncated_line_tolerated(tmp_path):
    pc = _world()
    calls = _calls(pc)
    ref = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    fes.enable_replay_memo(1_000_000, tmp_path)
    for s, p in calls:
        fes.replay_signals(pc, s, p, RG, 25.0)
    fes.enable_replay_memo(0)
    for f in tmp_path.rglob("*.jsonl"):
        with f.open("a", encoding="utf-8") as fh:
            fh.write('["deadbeef", "12", "x')  # 崩溃截断
    fes.enable_replay_memo(1_000_000, tmp_path)
    assert [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls] == ref


def test_session_decorator_injected_no_disk_and_reentrant(tmp_path, monkeypatch):
    monkeypatch.setenv(fes.REPLAY_CACHE_ENV, str(tmp_path))
    seen = []

    @fes.replay_session
    def inner(args, **kw):
        seen.append(("inner", fes._REPLAY_MEMO))

    @fes.replay_session
    def outer(args, **kw):
        seen.append(("outer", fes._REPLAY_MEMO))
        inner(args)

    class A:
        replay_memo = 100

    outer(A())  # 生产路径（无注入件）⇒ 落盘
    assert seen[0][1] is not None and seen[0][1].disk is not None
    assert seen[1][1] is seen[0][1], "重入：内层复用外层备忘"
    assert fes._REPLAY_MEMO is None
    seen.clear()
    outer(A(), warm_fn=lambda s, e: {})  # 注入 ⇒ 只开内存
    assert seen[0][1] is not None and seen[0][1].disk is None
    A.replay_memo = 0
    seen.clear()
    outer(A())
    assert seen[0][1] is None


# ── v0.465：版本哈希收窄 + prune ─────────────────────────────


def test_version_closure_covers_engine_and_excludes_studies():
    names = {p.as_posix().split("src/custos/")[1] for p in fes.replay_source_files()}
    for must in (
        "research/backtest_factors.py",
        "core/indicators.py",
        "core/paths.py",
    ):
        assert must in names, must
    for mustnt in (
        "research/factor_exit_study.py",
        "research/bear_regime_study.py",
        "research/plan_rules_replay.py",
        "research/criteria_kit.py",
    ):
        assert mustnt not in names, mustnt


def test_version_tracks_replay_function_source(monkeypatch):
    v0 = fes.replay_code_version()
    monkeypatch.setattr(fes, "_REPLAY_CODE_VERSION", None)
    import inspect

    real = inspect.getsource
    monkeypatch.setattr(inspect, "getsource", lambda o: real(o) + "#changed")
    assert fes.replay_code_version() != v0
    monkeypatch.setattr(fes, "_REPLAY_CODE_VERSION", None)


def test_version_funcs_cover_disk_format():
    """落盘行格式（_ReplayDisk.key_out/key_in/行布局）改动必须让缓存版本失效（review 2026-10-29）。"""
    assert "_ReplayDisk" in fes._REPLAY_VERSION_FUNCS
    assert "ReplayMemo" in fes._REPLAY_VERSION_FUNCS


def test_prune_removes_stale_versions_and_dedups(tmp_path):
    pc = _world()
    calls = _calls(pc)
    ref = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    for _ in range(2):  # 两轮各写一遍 ⇒ 重复行（第二轮命中不写——手工制造重复）
        fes.enable_replay_memo(1_000_000, tmp_path)
        for s, p in calls:
            fes.replay_signals(pc, s, p, RG, 25.0)
        fes.enable_replay_memo(0)
    cur = tmp_path / fes.replay_code_version()
    f = next(cur.rglob("*.jsonl"))
    f.write_text(f.read_text(encoding="utf-8") * 2 + "garbage\n", encoding="utf-8")
    (tmp_path / "deadbeefdeadbeef" / "00").mkdir(parents=True)
    (tmp_path / "deadbeefdeadbeef" / "00" / "000001.jsonl").write_text(
        "x\n", encoding="utf-8"
    )
    dry = fes.prune_replay_cache(tmp_path, dry_run=True)
    assert dry["removed_versions"] == ["deadbeefdeadbeef"]
    assert (tmp_path / "deadbeefdeadbeef").exists()
    out = fes.prune_replay_cache(tmp_path)
    assert not (tmp_path / "deadbeefdeadbeef").exists()
    assert out["bytes_after"] < out["bytes_before"]
    m = fes.enable_replay_memo(1_000_000, tmp_path)
    assert [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls] == ref
    assert m.misses == 0
