"""score_pool（v0.447）：批量 V0 打分与串行逐位一致、落盘缓存命中 / 失效 / 损坏容错、真进程池、warm 接入等价。"""

import contextlib

import numpy as np
import pandas as pd
import pytest

from custos.research import score_pool as sp
from custos.research import score_return_study as srs


def _df(n=320, seed=0):
    rng = np.random.RandomState(seed)
    d = pd.bdate_range("2015-01-01", periods=n)
    c = 10 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    return pd.DataFrame(
        {
            "date": d,
            "open": c,
            "high": c * 1.02,
            "low": c * 0.98,
            "close": c,
            "volume": 1e6,
            "amount": 1e7,
        }
    )


@pytest.fixture
def inline(monkeypatch):
    class _Ex:
        def map(self, fn, it, chunksize=1):
            return map(fn, it)

    def factory(n, initargs):
        sp._w_init(*initargs)
        return contextlib.nullcontext(_Ex())

    monkeypatch.setattr(sp, "EXECUTOR_FACTORY", factory)


@pytest.fixture
def fake_score(monkeypatch):
    calls = []

    def fake(df, idx, i, code):
        calls.append((code, i))
        if i % 7 == 0:
            raise RuntimeError("打分失败")
        return float(i) + (code == "000002"), "中", {}

    monkeypatch.setattr(srs, "asof_technical_score", fake)
    return calls


def test_serial_vs_pool_and_chunking(inline, fake_score, monkeypatch):
    monkeypatch.setattr(sp, "CHUNK", 3)  # 强制切块
    df = _df()
    tasks = [("000001", df, list(range(280, 300))), ("000002", df, [300, 301, 280])]
    a = sp.score_tasks(tasks, df, 1)
    b = sp.score_tasks(tasks, df, 4)
    assert a == b and [len(x) for x in a] == [20, 3]
    assert a[0][0] == (False, None)  # 280 % 7 == 0 ⇒ 失败
    assert a[1][0] == (True, 301.0)


def test_disk_cache_hit_invalidate_and_corrupt(
    inline, fake_score, monkeypatch, tmp_path
):
    monkeypatch.setenv(sp.ENV, str(tmp_path))
    df = _df()
    tasks = [("000001", df, [280, 290, 295])]
    first = sp.score_tasks(tasks, df, 2)
    n0 = len(fake_score)
    assert n0 == 3
    assert sp.score_tasks(tasks, df, 2) == first and len(fake_score) == n0, (
        "全命中缓存零新增计算（含失败缓存）"
    )
    # 帧内容变（例如前复权漂移）⇒ 换键重算
    df2 = df.assign(close=df["close"] * 1.0001)
    sp.score_tasks([("000001", df2, [280])], df2, 1)
    assert len(fake_score) == n0 + 1
    # 版本变 ⇒ 新目录、重算
    monkeypatch.setattr(sp, "_VERSION", "deadbeef")
    sp.score_tasks(tasks, df, 1)
    assert len(fake_score) == n0 + 4
    # 损坏分片 ⇒ 当未命中，不抛
    shard = sp._shard(tmp_path, "000001")
    shard.write_text("{not json", encoding="utf-8")
    assert sp.score_tasks(tasks, df, 1) == first


def test_cache_off_writes_nothing(inline, fake_score, monkeypatch, tmp_path):
    monkeypatch.setenv(sp.ENV, "off")
    assert sp.cache_root() is None
    df = _df()
    sp.score_tasks([("000001", df, [281])], df, 1)
    assert not any(tmp_path.iterdir())


def test_real_process_pool_matches_direct(tmp_path, monkeypatch):
    """真进程池 × 真打分函数 × 落盘缓存 = 进程内直算（spawn 也可：数据经任务 / initializer 下发）。"""
    monkeypatch.setenv(sp.ENV, str(tmp_path))
    df = _df(seed=3)
    idxs = [300, 305, 310, 315]
    res = sp.score_tasks([("000001", df, idxs)], df, 2)
    for i, (ok, sc) in zip(idxs, res[0]):
        assert ok and sc == srs.asof_technical_score(df, df, i, "000001")[0]
    assert sp.score_tasks([("000001", df, idxs)], df, 2) == res


def test_code_version_tracks_sources():
    v = sp.code_version()
    assert len(v) == 16 and v == sp.code_version()


def test_fill_signal_scores_drops_failures(inline, fake_score):
    df = _df()
    packs = {
        "000001": {"df": df, "scores": {}, "_to_score": [("d280", 280), ("d281", 281)]}
    }
    sp.fill_signal_scores(packs, df, 2)
    assert (
        packs["000001"]["scores"] == {"d281": 281.0}
        and "_to_score" not in packs["000001"]
    )


def test_warm_v0_signals_score_workers_bit_identical(inline, monkeypatch):
    """warm 接入：score_workers=4（批量）与 =1 读数逐位一致（同 test_load_workers 夹具）。"""
    import test_load_workers as tl

    from custos.research import exit_campaign as ec

    tl._patch_warm(monkeypatch)
    kw = dict(count=100, cost_bps=25.0, start="2022-01-01", end="2022-12-31")
    a = ec.warm_v0_signals(tl.CODES, {}, None, score_workers=1, **kw)
    b = ec.warm_v0_signals(tl.CODES, {}, None, score_workers=4, load_workers=4, **kw)
    tl._packs_equal(a, b)
    assert all("_to_score" not in p for p in a.values())
    # 与原逐信号语义一致：每票唯一信号日的分 = 假打分值
    assert all(p["scores"] == {"2022-05-05": float(int(c[-2:]))} for c, p in a.items())


def test_warm_gates_score_workers_bit_identical(inline, monkeypatch):
    import test_load_workers as tl

    from custos.research import backtest_factors as bt
    from custos.research import bear_regime_study as brs

    tl._patch_warm(monkeypatch)
    monkeypatch.setattr(bt, "ENTRY_GATES", {"g1": None, "g2": None})
    kw = dict(
        gates=["g1", "g2"],
        count=100,
        cost_bps=25.0,
        start="2022-01-01",
        end="2022-12-31",
    )
    a = brs.warm_gates(tl.CODES, {}, None, score_workers=1, **kw)
    b = brs.warm_gates(tl.CODES, {}, None, score_workers=4, load_workers=4, **kw)
    tl._packs_equal(a, b)
    assert a["600005"]["scores"] == {} and a["600001"]["scores"] == {"2022-05-05": 1.0}
