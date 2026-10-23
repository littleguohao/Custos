# -*- coding: utf-8 -*-
"""研究侧 as-of V0 技术分的批量打分：多进程 + 跨研究落盘缓存（v0.447，R40/R45/R44/R47~R50 warm 提速）。

生产机 profile（2026-10-23，199 票）：warm 5410s 中 99% 是 ``asof_technical_score``（46,196 次 compute_metrics），
线程并行受 GIL 无效。本模块把「(票, bar) → V0 分」做成：

- **多进程**：任务 = (code, df, [i...])，每任务 ≤ ``CHUNK`` 个 bar；df 逐任务 pickle 下发（Windows spawn 可用），
  上证指数经 initializer 一次下发；
- **落盘缓存**（默认开，``CUSTOS_V0_SCORE_CACHE=off`` 关；测试 conftest 关）：键 = blake2b(code | 三层 as-of 帧
  内容摘要)——与进程内 ``_CAND_CACHE`` 同一内容键，帧内容（含前复权漂移）任何变化都换键；版本目录 = 打分相关
  源码与契约 JSON 的内容哈希（``core/**``、``pipeline/screening/**``、``score_return_study.py``、
  ``governance/contracts/*.json``），代码一改自动失效，不靠人工 bump。只在主进程写盘（读-并-写，原子替换）。

结果语义与串行逐位一致：打分抛异常 ⇒ ``(False, None)``（调用方各自按原语义处理：warm 丢该信号、随机臂缓存 None）。
本模块只服务研究，不服务 live（同 indicator_cache 纪律）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

CHUNK = 64
ENV = "CUSTOS_V0_SCORE_CACHE"
_W: dict[str, Any] = {}
_VERSION: Optional[str] = None

#: 测试钩子：``(workers, initargs) -> 上下文管理的 executor``；None = ProcessPoolExecutor
EXECUTOR_FACTORY: Optional[Callable[[int, tuple], Any]] = None


def cache_root() -> Optional[Path]:
    v = os.environ.get(ENV, "")
    if v.lower() in ("off", "0", "no", "false"):
        return None
    if v:
        return Path(v)
    from custos.core.paths import ARTIFACTS  # noqa: PLC0415

    return ARTIFACTS / "cache" / "v0_score"


def code_version() -> str:
    """打分相关源码 + 契约 JSON 的内容哈希（进程内算一次）。"""
    global _VERSION
    if _VERSION is None:
        from custos.core.paths import BASE, CONTRACTS_DIR  # noqa: PLC0415

        src = Path(__file__).resolve().parents[1]  # src/custos
        files = sorted(
            [
                *(src / "core").rglob("*.py"),
                *(src / "pipeline" / "screening").rglob("*.py"),
                src / "research" / "score_return_study.py",
                *CONTRACTS_DIR.glob("*.json"),
            ]
        )
        h = hashlib.blake2b(digest_size=8)
        for p in files:
            h.update(
                str(p.resolve().relative_to(BASE.resolve())).replace("\\", "/").encode()
            )
            h.update(p.read_bytes().replace(b"\r\n", b"\n"))
        _VERSION = h.hexdigest()
    return _VERSION


def _shard(root: Path, code: str) -> Path:
    c = str(code)[:6]
    return root / code_version() / c[:2] / f"{c}.json"


def _read_shard(root: Optional[Path], code: str) -> dict[str, Any]:
    if root is None:
        return {}
    p = _shard(root, code)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}  # 缺失 / 损坏 ⇒ 当未命中


def _write_shard(root: Path, code: str, new: dict[str, Any]) -> None:
    p = _shard(root, code)
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = _read_shard(root, code)
    cur.update(new)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cur, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)


def _key(df: Any, index_df: Any, i: int, code: str) -> str:
    from custos.research import score_return_study as srs  # noqa: PLC0415

    a, b, c = srs.asof_frames(df, index_df, i)
    h = hashlib.blake2b(digest_size=16)
    h.update(str(code).encode())
    for f in (a, b, c):
        h.update(srs._frame_digest(f))
    return h.hexdigest()


def _w_init(index_df: Any, root: Optional[str]) -> None:
    _W.clear()
    _W.update(
        index_df=index_df, root=Path(root) if root else None, shard_code=None, shard={}
    )


def _w_score(
    task: tuple[str, Any, list[int]],
) -> tuple[list[tuple[bool, Any]], dict[str, Any]]:
    """单任务：逐 bar 先查缓存再算；返回 (结果列表, 新缓存条目)。"""
    from custos.research import score_return_study as srs  # noqa: PLC0415

    code, df, idxs = task
    root = _W.get("root")
    if root is not None and _W.get("shard_code") != code:
        _W["shard_code"], _W["shard"] = code, _read_shard(root, code)
    shard = _W.get("shard") or {}
    out: list[tuple[bool, Any]] = []
    new: dict[str, Any] = {}
    for i in idxs:
        k = _key(df, _W["index_df"], i, code) if root is not None else None
        if k is not None and k in shard:
            v = shard[k]
            out.append((False, None) if v == "ERR" else (True, v))
            continue
        try:
            score, _lv, _ct = srs.asof_technical_score(df, _W["index_df"], i, code)
            res: tuple[bool, Any] = (True, score)
        except Exception:  # noqa: BLE001 — 与串行同语义：失败记 (False, None)  # pylint: disable=broad-exception-caught
            res = (False, None)
        out.append(res)
        if k is not None:
            new[k] = res[1] if res[0] else "ERR"
    return out, new


def score_tasks(
    tasks: list[tuple[str, Any, list[int]]],
    index_df: Any,
    workers: int,
    *,
    label: str = "score",
) -> list[list[tuple[bool, Any]]]:
    """按任务批量打分；返回与 tasks 等长、逐 bar 对齐的 (ok, score) 列表。

    workers ≤ 1 ⇒ 进程内顺序执行（同一 worker 函数，仍走落盘缓存）。任务会被切成 ≤ CHUNK 个 bar 的子块
    （负载均衡），结果按原任务装配。
    """
    root = cache_root()
    chunks: list[tuple[int, tuple[str, Any, list[int]]]] = []
    for t, (code, df, idxs) in enumerate(tasks):
        for k0 in range(0, len(idxs), CHUNK):
            chunks.append((t, (code, df, list(idxs[k0 : k0 + CHUNK]))))
    results: list[list[tuple[bool, Any]]] = [[] for _ in tasks]
    pending: dict[str, dict[str, Any]] = {}
    total = sum(len(c[1][2]) for c in chunks)
    t0, done, last = time.time(), 0, 0
    initargs = (index_df, str(root) if root else None)

    def _collect(
        t: int, code: str, res: tuple[list[tuple[bool, Any]], dict[str, Any]]
    ) -> None:
        nonlocal done, last
        results[t].extend(res[0])
        if res[1]:
            pending.setdefault(code, {}).update(res[1])
        done += len(res[0])
        if total >= 1000 and (done - last >= 20000 or done == total):
            last = done
            print(
                f"[{label}] {done}/{total} bar  {time.time() - t0:.0f}s",
                file=sys.stderr,
            )

    if workers <= 1 or len(chunks) <= 1:
        saved = dict(_W)
        _w_init(*initargs)
        try:
            for t, task in chunks:
                _collect(t, task[0], _w_score(task))
        finally:
            _W.clear()
            _W.update(saved)
    else:
        if EXECUTOR_FACTORY is not None:
            ex_cm = EXECUTOR_FACTORY(workers, initargs)
        else:
            from concurrent.futures import ProcessPoolExecutor  # noqa: PLC0415

            ex_cm = ProcessPoolExecutor(
                max_workers=workers, initializer=_w_init, initargs=initargs
            )
        with ex_cm as ex:
            for (t, task), res in zip(chunks, ex.map(_w_score, [c[1] for c in chunks])):
                _collect(t, task[0], res)
    if root is not None:
        for code, new in pending.items():
            try:
                _write_shard(root, code, new)
            except OSError as e:  # 缓存写失败不影响读数
                print(f"[WARN] V0 分缓存写入失败 {code}: {e}", file=sys.stderr)
    return results


def fill_signal_scores(
    packs: dict[str, dict[str, Any]],
    index_df: Any,
    workers: int,
    *,
    label: str = "warm-score",
) -> None:
    """warm 用：packs = {code: {"df", "_to_score": [(date, i), ...], "scores": {}}}；按原串行语义填 scores
    （失败的信号不写入 = 原 ``except: continue``），并移除 ``_to_score``。"""
    codes = [c for c, p in packs.items() if p.get("_to_score")]
    tasks = [(c, packs[c]["df"], [i for _d, i in packs[c]["_to_score"]]) for c in codes]
    res = score_tasks(tasks, index_df, workers, label=label) if tasks else []
    for c, rs in zip(codes, res):
        for (d, _i), (ok, sc) in zip(packs[c]["_to_score"], rs):
            if ok:
                packs[c]["scores"][d] = sc
    for p in packs.values():
        p.pop("_to_score", None)
