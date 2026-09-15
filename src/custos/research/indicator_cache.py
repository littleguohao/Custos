# -*- coding: utf-8 -*-
"""指标盘缓存（indicator_cache）：研究侧逐股「电池」的磁盘复用层（v0.236+）。

**服务范围（红线，审计结论）**：只服务「从第 0 根前缀」语义的研究侧路径
（``evaluate_trades`` 的 ``_prepare_stock`` 电池恒如此——电池在**整帧**上算好，
主循环做点查询；前缀末点 ≡ 全序列同点的既有钉测链同样覆盖它们）。
as-of 重播种判例（``score_return_study.py:294``）：EMA 系递归指标 tail 重播种后
与 live 口径不逐位相等——本缓存**不得**用于 live、不得用于 score_return_study
的 as-of 帧（那里每根信号日的 EMA warmup 起点不同，缓存整帧电池会是错口径）。

**形态**：``data/cache/indicators/``（运行时数据，`.gitignore` 的 ``data/**``
已覆盖——确认于 v0.236），numpy ``np.savez``（不新增依赖；**不用 JSON**——
``paths.write_json`` 的 allow_nan=False 与指标 warmup NaN 冲突）。**读松**
（文件缺失/损坏/指纹任何一段不匹配 → None 现算，绝不报错）；**写严**
（叶子形状校验 + tmp+replace 原子写）。

**键设计（fail-closed 缺段算段）**：``(code, INDICATOR_PACK_VERSION, adjust 口径,
首根日期, 末根日期, n_bars, vipdoc .day 的 (mtime_ns, size, 尾记录 32B 摘要),
xdxr JSON 内容摘要)``——前复权会回溯改历史 ⇒ xdxr digest 必须进键（分红送
转一到，历史 K 线全变）；vipdoc mtime+尾记录感知新 bar/重下载。任何一段取不到
→ 该段落哨兵值（"missing"/"no-xdxr"），键照常可算且**不会**与数据齐全时的键
相撞（缺段环境内部自洽，跨环境自然隔离）。

**指标包版本纪律**：``INDICATOR_PACK_VERSION`` 进每个键——任何电池
（bbi/qsx/atr/bull_flags/trad/gate_pre/scorer_pre:*）的实现改动都必须 bump
本常量（不改 = 旧缓存被当新实现读，静默错口径）。改动指标实现的人负责 bump；
本文件只承载机制，不追实现（实现散在各因子/indicators 模块）。

挂载：``backtest_factors._prepare_stock`` 的电池 seam（默认关，
``--indicator-cache`` 开；live 链不调 evaluate_trades 此旗标）。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

#: 指标包版本：**任何电池实现改动都要 bump**（见模块 docstring 的纪律段）。
INDICATOR_PACK_VERSION = 1

#: 默认缓存根（运行时数据目录；CLI 默认挂这里，测试注入 tmp_path）。
DEFAULT_CACHE_ROOT = Path("data/cache/indicators")

#: npz 内元数据键（指纹 + Series 叶子清单，读时校验用）。
_META_KEY = "__meta__"


def default_vipdoc_segment(code: str) -> tuple:
    """vipdoc .day 段（生产实现）：(mtime_ns, size, 尾记录 32B 的 sha1 短哈希)。

    尾记录感知：通达信只追加/重写尾部 ⇒ mtime+size+尾记录三者同变才真变
    （mtime 可被 touch 伪造、size 追加不变时才靠尾记录兜底）。文件缺失/
    读失败 → ("missing",) 哨兵（fail-closed 缺段算段：缺段环境内部自洽）。
    """
    from custos.core.code_utils import market_of  # noqa: PLC0415
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    mkt = market_of(code)
    if not mkt:
        return ("no-market",)
    p = (
        local_tdx_data.TDX_ROOT
        / "vipdoc"
        / mkt.lower()
        / "lday"
        / f"{mkt.lower()}{code}.day"
    )
    try:
        st = p.stat()
        with p.open("rb") as fh:  # .day 定长 32B/记录
            fh.seek(max(0, st.st_size - 32))
            tail = fh.read(32)
        return (st.st_mtime_ns, st.st_size, hashlib.sha1(tail).hexdigest()[:12])
    except OSError:
        return ("missing",)


def default_xdxr_segment(code: str) -> str:
    """xdxr 段（生产实现）：权息缓存 JSON 的**内容** sha1 短哈希。

    用内容摘要而非 mtime：复权因子由权息事件驱动，内容变才算变（touch 不误伤）。
    文件缺失 → "no-xdxr"（该票从未除权，口径自洽）。
    """
    from custos.datasource.local_tdx import adjust_factors  # noqa: PLC0415

    try:
        return hashlib.sha1(adjust_factors._cache_path(code).read_bytes()).hexdigest()[
            :16
        ]
    except OSError:
        return "no-xdxr"


class IndicatorCache:
    """逐股电池的 npz 盘缓存。``vipdoc_segment``/``xdxr_segment`` 可注入
    （测试给合成桩；生产用 default_*）。读松写严（见模块 docstring）。"""

    def __init__(
        self,
        root: Path | str = DEFAULT_CACHE_ROOT,
        *,
        adjust: str = "qfq",
        vipdoc_segment: Optional[Callable[[str], tuple]] = None,
        xdxr_segment: Optional[Callable[[str], str]] = None,
    ):
        self.root = Path(root)
        self.adjust = adjust
        self._vipdoc_segment = vipdoc_segment or default_vipdoc_segment
        self._xdxr_segment = xdxr_segment or default_xdxr_segment

    # ---- 键 ----
    @staticmethod
    def pack_version() -> int:
        """当前指标包版本（日志/审计读数用；进每个缓存键）。"""
        return INDICATOR_PACK_VERSION

    def fingerprint(self, code: str, df: pd.DataFrame) -> tuple:
        """全键元组（缺段算段哨兵见模块 docstring）；date 列取首/末根 YYYY-MM-DD。"""
        dates = df["date"].astype(str)
        return (
            str(code),
            f"v{INDICATOR_PACK_VERSION}",
            self.adjust,
            str(dates.iloc[0])[:10],
            str(dates.iloc[-1])[:10],
            int(len(df)),
            self._vipdoc_segment(code),
            self._xdxr_segment(code),
        )

    def _path(self, code: str, battery: str, fp: tuple) -> Path:
        digest = hashlib.sha1(repr(fp).encode("utf-8")).hexdigest()[:16]
        safe = battery.replace(":", "@").replace("/", "_")
        return self.root / f"{code}__{safe}__{digest}.npz"

    # ---- 读（松）----
    def get(self, code: str, battery: str, fp: tuple) -> Optional[dict]:
        """读缓存；任何不匹配/损坏/形状违规 → None（现算不报错）。"""
        path = self._path(code, battery, fp)
        try:
            with np.load(path, allow_pickle=False) as z:
                meta = json.loads(str(z[_META_KEY].item()))
                if meta.get("fp") != _fp_jsonable(fp):  # 指纹不一致 → 现算
                    return None
                if meta.get("n_bars") != fp[5]:
                    return None
                series_keys = set(meta.get("series_keys") or [])
                series_names = meta.get("series_names") or {}
                payload: dict[str, Any] = {}
                for key in z.files:
                    if key == _META_KEY:
                        continue
                    arr = z[key]
                    if arr.ndim != 1 or len(arr) not in (fp[5], fp[5] - 1):
                        return None  # 形状违规（写严的镜像校验）
                    _assign_nested(
                        payload,
                        key,
                        pd.Series(arr, name=series_names.get(key))
                        if key in series_keys
                        else arr,
                    )
                return payload
        except Exception:  # noqa: BLE001  # 读松：缺文件/坏 npz/脏 meta 全走现算
            return None

    # ---- 写（严）----
    def put(self, code: str, battery: str, fp: tuple, payload: dict) -> None:
        """写缓存；叶子非 1-D 数组/形状违规 → 放弃写（不报错），仍返回算出的值。"""
        try:
            flat: dict[str, np.ndarray] = {}
            series_keys: list[str] = []
            series_names: dict[str, Any] = {}
            _flatten_into(payload, "", flat, series_keys, series_names)
            n = fp[5]
            for key, arr in flat.items():
                if arr.ndim != 1 or len(arr) not in (n, n - 1):
                    return  # 写严：形状不符的电池不缓存（如 NaN 标量/非标量叶子）
            meta = {
                "fp": _fp_jsonable(fp),
                "n_bars": n,
                "series_keys": sorted(series_keys),
                "series_names": series_names,
            }
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(code, battery, fp)
            tmp = path.with_name(path.name + ".tmp")
            with tmp.open("wb") as fh:
                np.savez(fh, **flat, **{_META_KEY: np.asarray(json.dumps(meta))})
            os.replace(tmp, path)  # 原子写：读者要么见旧要么见新，绝不见半截
        except Exception:  # noqa: BLE001  # 写失败不拦计算（缓存是加速层不是正确性层）
            return

    # ---- 读或算 ----
    def get_or_compute(
        self,
        code: str,
        battery: str,
        df: pd.DataFrame,
        compute: Callable[[], Optional[dict]],
    ) -> Optional[dict]:
        """命中 → 缓存负载；未中 → 现算 + 落盘（compute 返 None 不缓存）。"""
        fp = self.fingerprint(code, df)
        hit = self.get(code, battery, fp)
        if hit is not None:
            return hit
        out = compute()
        if out is not None:
            self.put(code, battery, fp, out)
        return out


def _fp_jsonable(fp: tuple) -> list:
    """指纹 → JSON 可序列化形态（嵌套 tuple 递归转 list）。"""
    return [list(x) if isinstance(x, tuple) else x for x in fp]


def _flatten_into(
    payload: dict,
    prefix: str,
    flat: dict[str, np.ndarray],
    series_keys: list[str],
    series_names: dict[str, Any],
) -> None:
    """嵌套 dict 拍平成点分键；叶子只收 np.ndarray / pd.Series（Series 记键名
    与 name，读时原样包回）。其他叶子类型 → TypeError（写严：该电池不缓存）。"""
    for k, v in payload.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            _flatten_into(v, key + ".", flat, series_keys, series_names)
        elif isinstance(v, pd.Series):
            series_keys.append(key)
            series_names[key] = v.name
            flat[key] = v.to_numpy()
        elif isinstance(v, np.ndarray):
            flat[key] = v
        else:
            raise TypeError(f"电池含非数组叶子 {key}: {type(v).__name__}（不缓存）")


def _assign_nested(payload: dict[str, Any], key: str, value: Any) -> None:
    """点分键还原嵌套 dict（_flatten_into 的逆）。"""
    parts = key.split(".")
    cur = payload
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


# ---------------------------------------------------------------------------
# 运维小工具面（TOOLS 登记用——research/ 顶层每个 .py 都必须是可发现入口）
# ---------------------------------------------------------------------------


def _build_parser() -> "argparse.ArgumentParser":
    """⚠️ add_argument 定义必须留在**本文件**内（`_modes()` 用 AST 抽取）。"""
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(
        description="指标盘缓存盘点：条目数/占用/按电池与代码分布（运维读数，"
        "缓存本体由 backtest_factors --indicator-cache 生产）"
    )
    ap.add_argument(
        "--root",
        default=str(DEFAULT_CACHE_ROOT),
        help=f"缓存根目录（默认 {DEFAULT_CACHE_ROOT}）",
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    root = Path(args.root)
    files = sorted(root.glob("*.npz")) if root.is_dir() else []
    by_battery: dict[str, int] = {}
    total = 0
    for p in files:
        total += p.stat().st_size
        battery = p.name.split("__")[1] if "__" in p.name else "?"
        by_battery[battery] = by_battery.get(battery, 0) + 1
    print(
        f"指标盘缓存盘点：{root}｜条目 {len(files)}｜占用 {total / 1e6:.1f}MB｜"
        f"包版本 v{INDICATOR_PACK_VERSION}"
    )
    for b, n in sorted(by_battery.items()):
        print(f"  {b:<28} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
