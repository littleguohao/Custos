# -*- coding: utf-8 -*-
"""研究长跑的**代码冻结**：启动时把 ``src/custos/**/*.py`` 源码快照到一个文件，
本进程与所有子进程之后的 custos import 一律从快照取，不读磁盘。

为什么（2026-11-05 R40 事故）：长跑期间生产机 ``git pull``，旧进程在报告装配阶段
惰性 import 了新版 ``provenance``，它要的 ``paths.is_snapshot_mode`` 在内存里的旧
``paths`` 中不存在 ⇒ ImportError，数小时结果全丢。冻结后跑数期间 pull / 改代码
都不影响在跑的进程；跑完 provenance 记下冻结摘要与 git HEAD，结论可追溯到代码。

机制：
- ``freeze(src_root, out_dir)``：读全部 .py → ``{相对路径: 源码}`` JSON（按内容摘要命名，
  幂等），返回路径；调用方把它放进环境变量 ``CUSTOS_CODE_FREEZE``。
- ``install(path)``：在 ``sys.meta_path`` 最前插一个 finder，``custos`` 及其子模块从快照
  编译执行；``__file__`` 仍是原磁盘路径（``paths.BASE`` 等按 ``__file__`` 推路径的逻辑不变）。
  快照里没有的模块（冻结后新增的文件）落回磁盘并 WARN。
- ``custos/__init__.py`` 见到环境变量就调 ``install``——spawn 出的 worker、subprocess
  子工具继承环境变量，同样走快照。
- ``read_source(path)``：做源码哈希的地方（重放备忘版本、V0 分缓存版本、因子版本表）
  须读**在跑的**代码而不是磁盘新代码，冻结时返回快照内容。

只用 stdlib（L0；``custos/__init__`` 在任何 custos import 之前执行它）。
契约 JSON 等非 .py 文件不在快照内（运行中改契约仍会被读到——跑数期间同样勿动）。
"""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.util
import json
import linecache
import os
import sys
from pathlib import Path
from typing import Any, Optional

ENV = "CUSTOS_CODE_FREEZE"
KEEP = 30
PKG = "custos"

_STATE: dict[str, Any] = {"root": None, "files": None, "digest": None, "path": None}


def snapshot(src_root: Path) -> dict[str, str]:
    """``src_root``（= .../src/custos）下全部 .py → {posix 相对路径: 源码}。"""
    out = {}
    for p in sorted(src_root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        # 原字节解码、不做换行翻译：read_bytes 哈希与磁盘逐字节一致（Windows CRLF）
        out[p.relative_to(src_root).as_posix()] = p.read_bytes().decode("utf-8")
    return out


def digest_of(files: dict[str, str]) -> str:
    h = hashlib.blake2b(digest_size=8)
    for k in sorted(files):
        h.update(k.encode())
        h.update(b"\0")
        h.update(files[k].encode())
        h.update(b"\0")
    return h.hexdigest()


def freeze(src_root: Path, out_dir: Path) -> Path:
    """快照落盘（``<out_dir>/<digest>.json``，同内容幂等复用），返回路径。"""
    files = snapshot(src_root)
    dg = digest_of(files)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{dg}.json"
    if not path.exists():
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"root": str(src_root), "digest": dg, "files": files}),
            encoding="utf-8",
        )
        tmp.replace(path)
    # 只留最近 KEEP 份（每份约 6 MB）；在跑进程已把快照读进内存，删旧文件不影响它们，
    # 但 spawn 新 worker 要读文件——KEEP 取大，覆盖同时在跑的多个长任务
    olds = sorted(out_dir.glob("*.json"), key=lambda q: q.stat().st_mtime, reverse=True)
    for q in olds[KEEP:]:
        if q != path:
            q.unlink(missing_ok=True)
    return path


class _Loader(importlib.abc.Loader):
    def __init__(self, filename: str, source: str, is_pkg: bool):
        self.filename = filename
        self.source = source
        self._is_pkg = is_pkg

    def create_module(self, spec):  # noqa: ANN001 —— 用默认模块对象
        return None

    def get_code(self, fullname: str):  # noqa: ANN201 —— runpy（-m 启动）走这条
        # inspect.getsource / traceback 经 linecache 取源码，默认读磁盘（新代码）；
        # 预置 mtime=None 的条目 ⇒ checkcache 不会用磁盘版覆盖
        text = self.source.replace("\r\n", "\n").replace("\r", "\n")
        linecache.cache[self.filename] = (
            len(text),
            None,
            text.splitlines(keepends=True),
            self.filename,
        )
        return compile(self.source, self.filename, "exec", dont_inherit=True)

    def exec_module(self, module) -> None:  # noqa: ANN001
        module.__file__ = self.filename
        exec(self.get_code(module.__name__), module.__dict__)  # noqa: S102 —— 冻结的本仓库源码

    def get_source(self, fullname: str) -> str:
        return self.source

    def is_package(self, fullname: str) -> bool:
        return self._is_pkg

    def get_filename(self, fullname: str) -> str:
        return self.filename


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001
        if fullname != PKG and not fullname.startswith(PKG + "."):
            return None
        files: dict[str, str] = _STATE["files"]
        root = Path(_STATE["root"])
        parts = fullname.split(".")[1:]
        rel_mod = "/".join(parts)
        for rel, is_pkg in (
            (f"{rel_mod}/__init__.py" if rel_mod else "__init__.py", True),
            (f"{rel_mod}.py", False),
        ):
            if rel in files:
                fn = str(root / rel)
                spec = importlib.util.spec_from_loader(
                    fullname,
                    _Loader(fn, files[rel], is_pkg),
                    origin=fn,
                    is_package=is_pkg,
                )
                if spec is not None:
                    spec.has_location = True
                    if is_pkg:
                        spec.submodule_search_locations = [str(Path(fn).parent)]
                return spec
        print(
            f"[WARN] code-freeze: {fullname} 不在冻结快照内（冻结后新增？），从磁盘加载",
            file=sys.stderr,
        )
        return None


def install(path: Path) -> None:
    """装入快照 finder（幂等）。"""
    if _STATE["files"] is not None:
        return
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    _STATE.update(root=d["root"], files=d["files"], digest=d["digest"], path=str(path))
    sys.meta_path.insert(0, _Finder())


def install_from_env() -> None:
    p = os.environ.get(ENV)
    if p and p != "off":
        install(Path(p))


def active() -> Optional[dict[str, str]]:
    """冻结中 ⇒ ``{"digest", "path", "git_head"}``；否则 None。"""
    if _STATE["files"] is None:
        return None
    return {
        "digest": _STATE["digest"],
        "path": _STATE["path"],
        "git_head": os.environ.get(ENV + "_HEAD", ""),
    }


def read_source(path: Path) -> str:
    """源码文本：冻结中且该文件在快照内 ⇒ 快照版；否则读磁盘。"""
    files = _STATE["files"]
    if files is not None:
        try:
            rel = Path(path).resolve().relative_to(Path(_STATE["root"])).as_posix()
        except ValueError:
            rel = None
        if rel is not None and rel in files:
            # 与 read_text 的通用换行语义一致
            return files[rel].replace("\r\n", "\n").replace("\r", "\n")
    return Path(path).read_text(encoding="utf-8")


def read_bytes(path: Path) -> bytes:
    """同 ``read_source``，给按字节哈希的调用方（.py 以外的文件一律读磁盘）。"""
    files = _STATE["files"]
    if Path(path).suffix == ".py" and files is not None:
        try:
            rel = Path(path).resolve().relative_to(Path(_STATE["root"])).as_posix()
        except ValueError:
            rel = None
        if rel is not None and rel in files:
            return files[rel].encode("utf-8")
    return Path(path).read_bytes()
