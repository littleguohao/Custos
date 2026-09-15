# -*- coding: utf-8 -*-
"""指标盘缓存钉测（v0.236+，审计建议的研究侧加速）。

铁律：开/关缓存两路 ``evaluate_trades`` 的 trades **逐位一致**；失效路径
（vipdoc 尾字节 / xdxr 内容 / 包版本 bump / 窗口变化）回现算且结果仍一致；
NaN warmup 往返一致；损坏 npz 现算不报错（读松）；形状违规不写（写严）。
无网络无通达信：合成数据 + tmp_path 缓存根 + 注入的指纹段提供器。
"""

import numpy as np
import pandas as pd
import pytest

from custos.research import backtest_factors as bf
from custos.research import indicator_cache as ic_mod
from custos.research.indicator_cache import IndicatorCache


def _mk(seed=11, n=300, start="2022-01-01", code_mark="600000"):
    """合成日线（随机游走 + 足够长度让 gate/scorer 电池全部起算）。"""
    rng = np.random.default_rng(seed)
    close = 20 + np.cumsum(rng.normal(0, 0.4, n))
    close = np.maximum(close, 1.0)
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=n),
            "open": close + rng.normal(0, 0.1, n),
            "high": close + abs(rng.normal(0.3, 0.1, n)),
            "low": close - abs(rng.normal(0.3, 0.1, n)),
            "close": close,
            "volume": abs(rng.normal(1e6, 3e5, n)),
        }
    )


def _cache(tmp_path, vipdoc_seg=None, xdxr_seg=None) -> IndicatorCache:
    """注入合成指纹段的缓存实例（无 vipdoc/xdxr 依赖）。"""
    return IndicatorCache(
        tmp_path / "icache",
        adjust="qfq",
        vipdoc_segment=vipdoc_seg or (lambda code: (1000, 9999, "tailA")),
        xdxr_segment=xdxr_seg or (lambda code: "xdxrA"),
    )


def _trades(df, cache):
    """同参数 evaluate_trades（j_low gate + kdj_j scorer——gate_pre/scorer_pre
    双电池都走到；trad/bbi 恒在）。"""
    return bf.evaluate_trades(
        {"600000": df},
        scorer=bf.SCORERS["kdj_j"],
        entry_gate=bf.j_low_gate,
        collect_all=True,
        indicator_cache=cache,
    )


class TestEquivalence:
    def test_cache_on_off_bitwise_identical(self, tmp_path):
        """核心：关 / 开（冷 miss）/ 开（热 hit）三路 trades 逐位一致。"""
        df = _mk()
        cache = _cache(tmp_path)
        off = _trades(df, None)
        on_cold = _trades(df, cache)  # 冷：写缓存
        on_hot = _trades(df, cache)  # 热：读缓存
        assert off == on_cold == on_hot
        assert off, "对拍不能空转（j_low 在随机游走上有信号）"
        assert list(cache.root.glob("*.npz")), "冷跑后缓存目录应有 npz"

    def test_s_shape_family_battery_cached_and_identical(self, tmp_path):
        """s_shape 家族 scorer_pre（嵌套 dict 负载）的缓存往返逐位一致。"""
        df = _mk()
        cache = _cache(tmp_path)

        def run(ic):
            return bf.evaluate_trades(
                {"600000": df},
                scorer=bf.SCORERS["s_shape"],
                collect_all=True,
                indicator_cache=ic,
            )

        off, on_cold, on_hot = run(None), run(cache), run(cache)
        assert off == on_cold == on_hot
        # scorer_pre 电池确实落了盘（嵌套 dict 拍平 + Series 还原路径被走到）
        assert any("scorer_pre" in p.name for p in cache.root.glob("*.npz"))


class TestInvalidation:
    """失效路径：键的任何一段变了 → 回现算且结果仍一致（读松不误命中）。"""

    def _three_runs(self, tmp_path, **kw):
        df = _mk()
        off = _trades(df, None)
        c1 = _trades(df, _cache(tmp_path, **kw))
        return off, c1

    def test_vipdoc_tail_change_invalidates(self, tmp_path):
        df = _mk()
        off = _trades(df, None)
        c_old = _trades(df, _cache(tmp_path))
        # vipdoc 尾记录变化（新 bar/重下载）→ 新键 → 现算，结果仍一致
        new_seg = (1000, 9999, "tailB")
        c_new = _trades(df, _cache(tmp_path, vipdoc_seg=lambda code: new_seg))
        assert off == c_old == c_new

    def test_xdxr_change_invalidates(self, tmp_path):
        df = _mk()
        off = _trades(df, None)
        c_old = _trades(df, _cache(tmp_path))
        # xdxr 内容变（分红送转 ⇒ 前复权回溯改历史）→ 新键 → 现算
        c_new = _trades(df, _cache(tmp_path, xdxr_seg=lambda code: "xdxrB"))
        assert off == c_old == c_new

    def test_pack_version_bump_invalidates(self, tmp_path, monkeypatch):
        df = _mk()
        off = _trades(df, None)
        c_old = _trades(df, _cache(tmp_path))
        # 指标实现改动 ⇒ bump 包版本 ⇒ 旧缓存全部失效（防旧实现被当新读）
        monkeypatch.setattr(ic_mod, "INDICATOR_PACK_VERSION", 999)
        c_new = _trades(df, _cache(tmp_path))
        assert off == c_old == c_new

    def test_window_change_invalidates(self, tmp_path):
        """同一文件不同加载窗口（首/末根/n_bars 不同）→ 不同键，互不污染。"""
        df_full = _mk(n=300)
        df_short = _mk(n=300).iloc[40:].reset_index(drop=True)
        cache = _cache(tmp_path)
        a = _trades(df_full, cache)
        b = _trades(df_short, cache)
        assert _trades(df_full, None) == a
        assert _trades(df_short, None) == b
        assert a != b  # 窗口不同结果当然不同——关键是没串缓存


class TestRobustness:
    def test_nan_warmup_roundtrip(self, tmp_path):
        """NaN warmup 往返一致（bbi 前 3 根 NaN；npz 原生承载，不走 JSON）。"""
        df = _mk(n=80)
        cache = _cache(tmp_path)
        payload = {"bbi": bf._bbi_series(df["close"])}
        fp = cache.fingerprint("600000", df)
        cache.put("600000", "bbi", fp, payload)
        back = cache.get("600000", "bbi", fp)
        assert back is not None
        pd.testing.assert_series_equal(back["bbi"], payload["bbi"])

    def test_corrupted_npz_falls_through(self, tmp_path):
        """损坏的 npz → 现算不报错（读松）。"""
        df = _mk(n=80)
        cache = _cache(tmp_path)
        payload = {"bbi": bf._bbi_series(df["close"])}
        fp = cache.fingerprint("600000", df)
        cache.put("600000", "bbi", fp, payload)
        path = cache._path("600000", "bbi", fp)
        path.write_bytes(b"not-a-npz")  # 损坏
        assert cache.get("600000", "bbi", fp) is None
        # get_or_compute 现算并**回写修好**
        out = cache.get_or_compute("600000", "bbi", df, lambda: payload)
        pd.testing.assert_series_equal(out["bbi"], payload["bbi"])
        assert cache.get("600000", "bbi", fp) is not None

    def test_meta_fingerprint_mismatch_falls_through(self, tmp_path):
        """文件名键对上但 meta 指纹不符（拼撞/手改）→ 现算（读松的双保险）。"""
        df = _mk(n=80)
        cache = _cache(tmp_path)
        payload = {"bbi": bf._bbi_series(df["close"])}
        fp = cache.fingerprint("600000", df)
        cache.put("600000", "bbi", fp, payload)
        path = cache._path("600000", "bbi", fp)
        import json as _json

        with np.load(path, allow_pickle=False) as z:
            flat = {k: z[k] for k in z.files if k != "__meta__"}
        meta = {"fp": ["tampered"], "n_bars": 80, "series_keys": ["bbi"]}
        with path.open("wb") as fh:
            np.savez(fh, **flat, **{"__meta__": np.asarray(_json.dumps(meta))})
        assert cache.get("600000", "bbi", fp) is None

    def test_bad_shape_payload_not_cached(self, tmp_path):
        """写严：2-D 叶子/长度与 n_bars 不符 ⇒ 不落盘（get 恒 None）。"""
        df = _mk(n=80)
        cache = _cache(tmp_path)
        fp = cache.fingerprint("600000", df)
        cache.put("600000", "bad", fp, {"m": np.zeros((80, 2))})  # 2-D 违规
        cache.put("600000", "bad2", fp, {"m": np.zeros(50)})  # 长度违规
        assert cache.get("600000", "bad", fp) is None
        assert cache.get("600000", "bad2", fp) is None
        assert not list(cache.root.glob("*.npz"))

    def test_missing_segments_are_sentinels(self, tmp_path):
        """fail-closed 缺段算段：vipdoc/xdxr 取不到 → 哨兵段，键照常可算且自洽。"""
        cache = IndicatorCache(
            tmp_path / "c",
            vipdoc_segment=lambda code: ("missing",),
            xdxr_segment=lambda code: "no-xdxr",
        )
        df = _mk(n=80)
        fp = cache.fingerprint("600000", df)
        assert fp[6] == ("missing",) and fp[7] == "no-xdxr"
        payload = {"bbi": bf._bbi_series(df["close"])}
        cache.put("600000", "bbi", fp, payload)
        assert cache.get("600000", "bbi", fp) is not None


class TestFingerprintDesign:
    def test_key_segments(self, tmp_path):
        """键段构成钉死：code/包版本/adjust/首末根/n_bars/vipdoc 段/xdxr 段。"""
        cache = _cache(tmp_path)
        df = _mk(n=100)
        fp = cache.fingerprint("600000", df)
        assert fp[0] == "600000"
        assert fp[1] == f"v{ic_mod.INDICATOR_PACK_VERSION}"
        assert fp[2] == "qfq"
        assert fp[3] == "2022-01-01" and fp[4] == "2022-04-10"  # 日频 date_range 首末
        assert fp[5] == 100
        assert fp[6] == (1000, 9999, "tailA")
        assert fp[7] == "xdxrA"
        # 文件名含 code+battery+摘要
        name = cache._path("600000", "gate_pre", fp).name
        assert name.startswith("600000__gate_pre__") and name.endswith(".npz")
