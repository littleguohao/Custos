# -*- coding: utf-8 -*-
"""研究长跑代码冻结（v0.497，core/code_freeze）钉测。

事故背景：R40 长跑中 git pull，报告阶段惰性 import 了新版 provenance ⇒ ImportError。
这里用「改过的快照」模拟「磁盘代码已变」：冻结进程必须执行快照版，而不是磁盘版。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from custos.core import code_freeze as cf

SRC = Path(cf.__file__).resolve().parents[1]  # src/custos
SENTINEL = "\nFROZEN_SENTINEL_V0497 = 42\n"


def _frozen_snapshot(tmp_path: Path) -> Path:
    files = cf.snapshot(SRC)
    files["core/fmt.py"] += SENTINEL  # 快照与磁盘不同 ⇒ 能看出执行的是哪一版
    files.pop("core/indicators.py")  # 冻结后「新增」的文件：须落回磁盘 + WARN
    p = tmp_path / "snap.json"
    p.write_text(
        json.dumps({"root": str(SRC), "digest": "test", "files": files}),
        encoding="utf-8",
    )
    return p


PROBE = r"""
import inspect, json, multiprocessing as mp
from pathlib import Path
import custos.core.fmt as fmt
import custos.core.indicators as ind
from custos.core import code_freeze as cf
ctx = mp.get_context("spawn")
with ctx.Pool(1) as pool:
    worker = pool.apply(cf.active)
print(json.dumps({
    "sentinel": getattr(fmt, "FROZEN_SENTINEL_V0497", None),
    "file": fmt.__file__,
    "getsource": "FROZEN_SENTINEL_V0497" in inspect.getsource(fmt),
    "read_source": "FROZEN_SENTINEL_V0497" in cf.read_source(Path(fmt.__file__)),
    "ind_file": ind.__file__,
    "active": cf.active(),
    "worker": worker,
}))
"""


def _run(env_extra: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, "-c", PROBE],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )


def test_frozen_process_and_spawn_worker_use_snapshot(tmp_path):
    snap = _frozen_snapshot(tmp_path)
    r = _run({cf.ENV: str(snap), cf.ENV + "_HEAD": "abc1234"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["sentinel"] == 42  # 执行的是快照版，不是磁盘版
    assert (
        Path(out["file"]) == SRC / "core" / "fmt.py"
    )  # __file__ 仍是磁盘路径（BASE 推导不变）
    assert out["getsource"] and out["read_source"]  # 源码哈希 / inspect 读在跑的代码
    assert Path(out["ind_file"]) == SRC / "core" / "indicators.py"
    assert "不在冻结快照内" in r.stderr  # 快照外模块：落回磁盘 + WARN
    assert out["active"] == {"digest": "test", "path": str(snap), "git_head": "abc1234"}
    assert out["worker"] and out["worker"]["digest"] == "test"  # spawn worker 继承冻结


def test_unfrozen_reads_disk(tmp_path):
    r = _run({cf.ENV: "off"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["sentinel"] is None and out["active"] is None and out["worker"] is None


def test_snapshot_bytes_match_disk_for_hashing():
    """read_bytes 冻结版与磁盘逐字节一致 ⇒ 冻结 / 非冻结运行的缓存版本哈希相同。"""
    files = cf.snapshot(SRC)
    p = SRC / "core" / "paths.py"
    assert files["core/paths.py"].encode("utf-8") == p.read_bytes()
    assert cf.digest_of(files) == cf.digest_of(dict(files))


def test_freeze_is_idempotent(tmp_path):
    a = cf.freeze(SRC, tmp_path)
    b = cf.freeze(SRC, tmp_path)
    assert a == b and a.exists() and a.stem == cf.digest_of(cf.snapshot(SRC))


def test_dispatcher_launches_tool_frozen_via_m(monkeypatch, tmp_path):
    """真实 CLI 入口（freeze=True）：子工具以 -m 启动且带冻结环境；测试调用缺省不冻结。"""
    from custos.research import __main__ as rm

    calls = []

    class _R:
        returncode = 0

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[:2] == ["git", "rev-parse"]:
            r = _R()
            r.stdout = "abc1234\n"
            return r
        if cmd[:2] == ["git", "status"]:
            r = _R()
            r.stdout = ""
            return r
        return _R()

    monkeypatch.delenv(cf.ENV, raising=False)
    monkeypatch.setattr(rm.subprocess, "run", fake_run)
    monkeypatch.setattr(cf, "freeze", lambda src, out: tmp_path / "d1.json")
    name = next(n for n, v in rm.TOOLS.items() if v[0] != "stale")
    assert rm.main([name, "--help"], freeze=True) == 0
    cmd, kw = calls[-1]
    assert cmd[1:3] == ["-m", f"custos.research.{name}"]
    assert kw["env"][cf.ENV] == str(tmp_path / "d1.json")
    assert kw["env"][cf.ENV + "_HEAD"] == "abc1234"
    calls.clear()
    assert rm.main([name, "--help"]) == 0  # 缺省不冻结：按路径启动，无冻结环境
    cmd, kw = calls[-1]
    assert cmd[1].endswith(f"{name}.py") and "env" not in kw


def test_snapshot_gate_only_blocks_judgment_reads(monkeypatch):
    """快照硬闸只拦触及判定段 2017~2021 的读取；第二窗放挖掘 B 的预演照常放行（v0.497）。"""
    import argparse

    import pytest

    from custos.research import judgment_batch as jb

    monkeypatch.setattr(jb, "is_snapshot_mode", lambda: True)
    ap = argparse.ArgumentParser()
    args = argparse.Namespace()
    assert (
        jb.gate_judgment_read(
            args,
            ap,
            synthetic=False,
            judgment_span=("2022-01-01", "2026-09-04"),
            tool="t",
        )
        is False
    )
    with pytest.raises(SystemExit):
        jb.gate_judgment_read(
            args,
            ap,
            synthetic=False,
            judgment_span=("2017-01-01", "2021-12-31"),
            tool="t",
        )
