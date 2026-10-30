#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生产机挖掘段数据快照导出（TODO #116 导出半边；设计稿 governance/data/MINING_SNAPSHOT_DESIGN.md）。

用法（**生产机**上跑；开发机无通达信数据，由 tests/test_export_mining_snapshot.py 合成数据钉住）::

    uv run python scripts/dev/export_mining_snapshot.py --out /path/to/snap [--tar]
    uv run python scripts/dev/export_mining_snapshot.py --verify /path/to/snap

干什么：把挖掘 A（≤2016-12-31）∪ 预热段（≥2020-10-01）∪ 挖掘 B（≤2026-09-04）
需要的数据按生产机**原目录、原格式**导出成镜像目录 + MANIFEST.json（逐文件
sha256、记录数、段边界、已知局限）；判定段 2017~2021（预热/PIT/权息豁免除外）
与前向 holdout（≥2026-09-05）**物理不出生产机**。只读生产数据，不改任何台账。

保留规则（写死为本模块常量，消费侧钉测核对）：

- K 线 / 板块指数行 / 0AMV regime 序列：
  ``d <= MINING_A_END`` 或 ``WARMUP_B_START <= d <= SNAPSHOT_END``；
- xdxr 权息 events 与股本 shares：事件日 ``<= SNAPSHOT_END`` **全保留
  （含判定段 2017~2021）**——缺判定段事件前复权会算错；
- PIT：``notice_date <= MINING_A_END`` 或
  ``PIT_INCREMENT_START <= notice_date <= SNAPSHOT_END``（挖掘 B 起点 as-of TTM）；
- share_changes：``observed_on <= SNAPSHOT_END`` 全保留（as-of 只可能 stale）。

护栏：``--out`` resolve 后位于仓库内（``custos.core.paths.BASE``）且不在
``artifacts/cache/`` 之下 ⇒ 非零退出（防误入库）；0AMV 序列过滤后为空 ⇒
非零退出且不写 MANIFEST（生产机台账必然非空）；``--verify`` 只读核对，
任何 sha256 不符/缺失返非零并逐一列出。MANIFEST.json 最后写（先数据后清单）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
import tarfile
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.paths import BASE, DATA, TDX_ROOT, cn_now

# ---------------------------------------------------------------------------
# 保留规则常量（与消费侧的契约，钉测核对——改动=语义变更，须先改设计稿）
# ---------------------------------------------------------------------------
MINING_A_START = "2000-01-01"
MINING_A_END = "2016-12-31"
JUDGMENT_START = "2017-01-01"
JUDGMENT_END = "2021-12-31"
WARMUP_B_START = "2020-10-01"  # 预热段起点（挖掘 B 前 ≥320 根，设计稿 (a)）
PIT_INCREMENT_START = "2020-01-01"  # PIT 增量起点（挖掘 B 起点 as-of TTM，(b)）
MINING_B_START = "2022-01-01"
SNAPSHOT_END = "2026-09-04"
HOLDOUT_START = "2026-09-05"

_A_END8 = MINING_A_END.replace("-", "")
_WARM8 = WARMUP_B_START.replace("-", "")
_END8 = SNAPSHOT_END.replace("-", "")
_PIT8 = PIT_INCREMENT_START.replace("-", "")
_A_END8I = int(_A_END8)
_WARM8I = int(_WARM8)
_END8I = int(_END8)

DAY_RECORD_SIZE = 32  # vipdoc .day 定长记录（<IIIIIfII：date_int 为首字段）
MANIFEST_SCHEMA = "mining_snapshot/v1"
AMV_SCHEMA = "amv_regime_series/v1"

SEGMENTS = {
    "mining_a": {"start": MINING_A_START, "end": MINING_A_END},
    "judgment_excluded": {"start": JUDGMENT_START, "end": JUDGMENT_END},
    "mining_b": {"start": MINING_B_START, "end": SNAPSHOT_END},
    "holdout_excluded": {"start": HOLDOUT_START, "end": None},
}
KEEP_RULES = {
    "kline": {
        "rule": f"d <= {MINING_A_END} or {WARMUP_B_START} <= d <= {SNAPSHOT_END}",
        "applies_to": ["vipdoc", "sector_index", "amv_regime_series"],
    },
    "xdxr": {
        "rule": f"event_date <= {SNAPSHOT_END}（含判定段 2017~2021——缺判定段事件前复权会算错）",
        "applies_to": ["xdxr.events", "xdxr.shares"],
    },
    "pit": {
        "rule": (
            f"notice_date <= {MINING_A_END} or "
            f"{PIT_INCREMENT_START} <= notice_date <= {SNAPSHOT_END}"
        ),
    },
    "share_changes": {
        "rule": f"observed_on <= {SNAPSHOT_END}（as-of 只可能 stale 不会 look-ahead）",
    },
}
# 设计稿「已知局限」四条，逐字
KNOWN_LIMITATIONS = [
    "序列在 2017 ~ 2020-09 断开：R43 `age`（上市第几根）类特征在 B 窗失真，快照上该维不可用；",
    "前复权价格水平与生产机差一个常数（holdout 之后的权息被截）——收益比例与 `raw_close` 口径不受影响；",
    "板块成分为当前快照（与生产机同样的前视偏差）；",
    "通达信数据仅限个人自用，快照不外传、不入库。",
]


# ---------------------------------------------------------------------------
# 日期归一与保留判定（纯函数）
# ---------------------------------------------------------------------------
def _d8(value: Any) -> str:
    """任意日期形态（"YYYY-MM-DD" / "YYYYMMDD" / int）→ 纯数字串（取数字比对）。"""
    return "".join(ch for ch in str(value) if ch.isdigit())


def _keep_kline_d8(d8: str) -> bool:
    """K 线保留规则：d ≤ 2016-12-31 或 2020-10-01 ≤ d ≤ 2026-09-04。"""
    return bool(d8) and (d8 <= _A_END8 or (_WARM8 <= d8 <= _END8))


def _keep_kline_int(date_int: int) -> bool:
    """同上，YYYYMMDD 整型入口（vipdoc date_int）。"""
    return date_int <= _A_END8I or (_WARM8I <= date_int <= _END8I)


def _le_end(d8: str) -> bool:
    """事件日 ≤ 2026-09-04（xdxr / share_changes 口径）。"""
    return bool(d8) and d8 <= _END8


def _keep_pit(d8: str) -> bool:
    """PIT：notice_date ≤ 2016-12-31 或 2020-01-01 ≤ notice_date ≤ 2026-09-04。"""
    return bool(d8) and (d8 <= _A_END8 or (_PIT8 <= d8 <= _END8))


# ---------------------------------------------------------------------------
# 按文件类型的过滤纯函数
# ---------------------------------------------------------------------------
def filter_day_file(src: Path, dst: Path) -> tuple[int, int]:
    """vipdoc .day 按日期过滤：保留记录**原样 32 字节切片**写回（不 unpack/repack，
    防浮点漂移——验收要求与生产机逐位一致）。date_int==0 跳过（同读取方惯例，
    计入丢弃）；过滤后 0 条的文件不写出。返回 (保留条数, 丢弃条数)。"""
    buf = src.read_bytes()
    parts: list[bytes] = []
    n_kept = n_dropped = 0
    for off in range(0, len(buf) - DAY_RECORD_SIZE + 1, DAY_RECORD_SIZE):
        date_int = struct.unpack_from("<I", buf, off)[0]
        if date_int == 0 or not _keep_kline_int(date_int):
            n_dropped += 1
            continue
        parts.append(buf[off : off + DAY_RECORD_SIZE])
        n_kept += 1
    if n_kept:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"".join(parts))
    return n_kept, n_dropped


def attach_gap_ratios(
    events: list[dict], bars: Optional[tuple[list[str], list[float]]]
) -> int:
    """对「真实前一根 K 线落在快照断口」的事件写入 ``qfq_ratio``（v0.457，验收①回修）。

    ``bars`` = 生产机真实未复权 (dates YYYY-MM-DD 升序, closes)，与 ``compute_qfq_factors``
    同口径定位：idx = 首个 ≥ 事件日的 bar，前收 = closes[idx-1]；该前收 bar 不在快照保留集
    ⇒ 快照侧会取错前收 ⇒ 预先算好比例（``event_ratio`` 同函数；None 原样写 null = 生产机也跳过）。
    事件落在样本外（idx ≤ 0 或越界）不处理。返回写入条数。"""
    import bisect  # noqa: PLC0415

    from custos.datasource.local_tdx.adjust_factors import event_ratio  # noqa: PLC0415

    if not bars or not bars[0]:
        return 0
    dates, closes = bars
    n = 0
    for e in events:
        # 与 compute_qfq_factors 完全同口径：原样 ev["date"] 与 str(date)[:10] 比较（不归一格式——
        # 归一会让导出侧与复权侧定位到不同 bar）
        idx = bisect.bisect_left(dates, e.get("date"))
        if idx <= 0 or idx >= len(dates):
            continue
        if _keep_kline_d8(_d8(dates[idx - 1])):
            continue  # 前收 bar 在快照里，快照侧算得与生产机一致
        e["qfq_ratio"] = event_ratio(float(closes[idx - 1]), e)
        n += 1
    return n


def _default_bars_provider(code: str) -> Optional[tuple[list[str], list[float]]]:
    """生产机真实未复权日线（与研究加载链同一读取函数 ⇒ 前收浮点逐位一致）。"""
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    try:
        df = local_tdx_data.read_vipdoc_daily(code)
    except Exception:  # noqa: BLE001 — 读不到 ⇒ 不加字段（与生产机该票无 K 线同况）
        return None
    if df is None or df.empty:
        return None
    return (
        [str(x)[:10] for x in df["date"].tolist()],
        df["close"].astype(float).tolist(),
    )


def filter_xdxr(
    src: Path, dst: Path, bars: Optional[tuple[list[str], list[float]]] = None
) -> tuple[int, int]:
    """xdxr 权息 JSON：事件日 ≤ 2026-09-04 全保留（含判定段）；date 兼容
    "YYYY-MM-DD"/"YYYYMMDD"（str 化取数字比对）。过滤后重写 n，其余字段原样。
    ``bars`` 给出时对断口期事件写 ``qfq_ratio``（见 attach_gap_ratios）。
    返回 (保留事件数, 丢弃事件数)。"""
    obj = json.loads(src.read_text(encoding="utf-8-sig"))
    events = obj.get("events") or []
    kept = [e for e in events if _le_end(_d8(e.get("date")))]
    if bars is not None:
        attach_gap_ratios(kept, bars)
    dropped = len(events) - len(kept)
    obj["events"] = kept
    shares = obj.get("shares")
    if isinstance(shares, list):  # shares 可缺
        obj["shares"] = [s for s in shares if _le_end(_d8(s.get("date")))]
    obj["n"] = len(kept)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(kept), dropped


def filter_sector_csv(src: Path, dst: Path) -> tuple[int, int]:
    """板块指数日线 CSV（列：date,close，YYYY-MM-DD）按 K 线保留规则逐行过滤；
    0 保留行的文件不写出。返回 (保留行数, 丢弃行数)。"""
    lines = src.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        return 0, 0
    rows = [ln for ln in lines[1:] if ln.strip()]
    kept = [ln for ln in rows if _keep_kline_d8(_d8(ln.split(",", 1)[0]))]
    if kept:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("\n".join([lines[0], *kept]) + "\n", encoding="utf-8")
    return len(kept), len(rows) - len(kept)


def filter_pit(src: Path, dst: Path) -> tuple[int, int, int]:
    """PIT jsonl 按 notice_date 过滤（见 _keep_pit）；缺 notice_date 的行丢弃并
    单独计数。原始行文本透写。返回 (保留, 丢弃, 缺 notice_date 丢弃)。"""
    kept_lines: list[str] = []
    n_dropped = n_missing = 0
    for ln in src.read_text(encoding="utf-8-sig").splitlines():
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            n_dropped += 1
            continue
        if not _d8(row.get("notice_date")):
            n_missing += 1
            continue
        if _keep_pit(_d8(row.get("notice_date"))):
            kept_lines.append(ln)
        else:
            n_dropped += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("".join(ln + "\n" for ln in kept_lines), encoding="utf-8")
    return len(kept_lines), n_dropped, n_missing


def filter_share_changes(src: Path, dst: Path) -> tuple[int, int]:
    """share_changes jsonl：observed_on ≤ 2026-09-04 全保留（判定段年份必须留）。
    原始行文本透写。返回 (保留, 丢弃)。"""
    kept_lines: list[str] = []
    n_dropped = 0
    for ln in src.read_text(encoding="utf-8-sig").splitlines():
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            n_dropped += 1
            continue
        if _le_end(_d8(row.get("observed_on"))):
            kept_lines.append(ln)
        else:
            n_dropped += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("".join(ln + "\n" for ln in kept_lines), encoding="utf-8")
    return len(kept_lines), n_dropped


def write_amv_series(out_root: Path, series: Any) -> int:
    """0AMV regime 序列按 K 线保留规则截断后写 data/market/amv_regime_series.json。

    不导原始台账（状态机须从台账首日重放，截断台账会算错）。过滤后为空 ⇒
    **不写文件、返回 0**（调用方据此非零退出且不写 MANIFEST）。
    """
    kept = {str(d): r for d, r in dict(series).items() if _keep_kline_d8(_d8(d))}
    if not kept:
        return 0
    dst = out_root / "data" / "market" / "amv_regime_series.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": AMV_SCHEMA, "series": dict(sorted(kept.items()))}
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(kept)


# ---------------------------------------------------------------------------
# 分节导出（各自把产物登记进 files/counts）
# ---------------------------------------------------------------------------
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _register(files: list[dict], out_root: Path, dst: Path, records: int) -> None:
    files.append(
        {
            "path": dst.relative_to(out_root).as_posix(),
            "sha256": _sha256_file(dst),
            "bytes": dst.stat().st_size,
            "records": records,
        }
    )


def _export_vipdoc(tdx_root: Path, out: Path, files: list[dict], counts: dict) -> None:
    n_files = n_kept = n_dropped = n_empty = 0
    for mkt in ("sh", "sz", "bj"):  # 三个市场目录全处理（含指数文件）
        src_dir = tdx_root / "vipdoc" / mkt / "lday"
        if not src_dir.is_dir():
            continue
        for src in sorted(src_dir.glob("*.day")):
            dst = out / "vipdoc" / mkt / "lday" / src.name
            k, d = filter_day_file(src, dst)
            n_kept += k
            n_dropped += d
            if k:
                n_files += 1
                _register(files, out, dst, k)
            else:
                n_empty += 1
    counts.update(
        vipdoc_files=n_files,
        vipdoc_records_kept=n_kept,
        vipdoc_records_dropped=n_dropped,
        vipdoc_empty_files_dropped=n_empty,
    )
    print(
        f"vipdoc: 写出 {n_files} 文件 / 保留 {n_kept} 条 / 丢弃 {n_dropped} 条 / 空文件丢弃 {n_empty}"
    )


def _export_tdx_cfg(tdx_root: Path, out: Path, files: list[dict], counts: dict) -> None:
    n = 0
    for name in ("tdxzs3.cfg", "tdxzs.cfg"):  # 原样字节拷贝；tdxzs.cfg 若存在也拷
        src = tdx_root / "T0002" / "hq_cache" / name
        if not src.is_file():
            continue
        dst = out / "T0002" / "hq_cache" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        _register(files, out, dst, 1)
        n += 1
    counts["tdx_cfg_files"] = n
    print(f"tdx cfg: {n} 个（原样字节拷贝）")


def _export_xdxr(
    data_root: Path,
    out: Path,
    files: list[dict],
    counts: dict,
    bars_provider: Optional[Callable[[str], Any]] = None,
) -> None:
    src_dir = data_root / "market" / "xdxr"
    n_files = n_kept = n_dropped = 0
    bp = bars_provider or _default_bars_provider
    if src_dir.is_dir():
        for src in sorted(src_dir.glob("*.json")):
            dst = out / "data" / "market" / "xdxr" / src.name
            k, d = filter_xdxr(src, dst, bars=bp(src.stem[:6]))
            _register(files, out, dst, k)
            n_files += 1
            n_kept += k
            n_dropped += d
    counts.update(
        xdxr_files=n_files, xdxr_events_kept=n_kept, xdxr_events_dropped=n_dropped
    )
    print(f"xdxr: {n_files} 文件 / 事件保留 {n_kept} / 丢弃 {n_dropped}")


def _export_sector(data_root: Path, out: Path, files: list[dict], counts: dict) -> None:
    src_dir = data_root / "market" / "sector_index"
    n_files = n_kept = n_dropped = n_empty = 0
    if src_dir.is_dir():
        for src in sorted(src_dir.glob("*.csv")):
            dst = out / "data" / "market" / "sector_index" / src.name
            k, d = filter_sector_csv(src, dst)
            n_kept += k
            n_dropped += d
            if k:
                n_files += 1
                _register(files, out, dst, k)
            else:
                n_empty += 1
    members_src = data_root / "market" / "sector_members.json"
    n_members = 0
    if members_src.is_file():  # 原样（本就是当前快照，前视偏差在各单元注记）
        dst = out / "data" / "market" / "sector_members.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(members_src, dst)
        _register(files, out, dst, 1)
        n_members = 1
    counts.update(
        sector_csv_files=n_files,
        sector_rows_kept=n_kept,
        sector_rows_dropped=n_dropped,
        sector_empty_files_dropped=n_empty,
        sector_members_files=n_members,
    )
    print(
        f"sector: csv {n_files} / 行保留 {n_kept} / 丢弃 {n_dropped} / members {n_members}"
    )


def _export_fundamentals(
    data_root: Path, out: Path, files: list[dict], counts: dict
) -> None:
    pit_src = data_root / "fundamentals" / "pit_financials.jsonl"
    k = d = m = n_pit = 0
    if pit_src.is_file():
        n_pit = 1
        dst = out / "data" / "fundamentals" / "pit_financials.jsonl"
        k, d, m = filter_pit(pit_src, dst)
        _register(files, out, dst, k)
    counts.update(
        pit_files=n_pit,
        pit_rows_kept=k,
        pit_rows_dropped=d,
        pit_missing_notice_dropped=m,
    )
    sc_src = data_root / "fundamentals" / "share_changes.jsonl"
    k2 = d2 = n_sc = 0
    if sc_src.is_file():
        n_sc = 1
        dst2 = out / "data" / "fundamentals" / "share_changes.jsonl"
        k2, d2 = filter_share_changes(sc_src, dst2)
        _register(files, out, dst2, k2)
    counts.update(
        share_changes_files=n_sc,
        share_changes_rows_kept=k2,
        share_changes_rows_dropped=d2,
    )
    print(
        f"fundamentals: pit 留 {k}/丢 {d}/缺 notice {m}；share_changes 留 {k2}/丢 {d2}"
    )


def _default_series_provider() -> Any:
    """0AMV 单源：backtest_factors.load_amv_regime（台账首日重放状态机）全序列。"""
    from custos.research.backtest_factors import load_amv_regime  # noqa: PLC0415

    return load_amv_regime(since="1900-01-01")


def _export_all(
    out: Path,
    tdx_root: Path,
    data_root: Path,
    series_provider: Callable[[], Any],
    bars_provider: Optional[Callable[[str], Any]] = None,
) -> Optional[tuple[list[dict], dict]]:
    """全部分节导出；0AMV 过滤后为空 ⇒ None（主调据此非零退出、不写 MANIFEST）。"""
    files: list[dict] = []
    counts: dict[str, int] = {}
    _export_vipdoc(tdx_root, out, files, counts)
    _export_tdx_cfg(tdx_root, out, files, counts)
    _export_xdxr(data_root, out, files, counts, bars_provider)
    _export_sector(data_root, out, files, counts)
    _export_fundamentals(data_root, out, files, counts)
    n_amv = write_amv_series(out, series_provider())
    if not n_amv:
        return None
    _register(files, out, out / "data" / "market" / "amv_regime_series.json", n_amv)
    counts["amv_regime_points"] = n_amv
    print(f"amv regime: {n_amv} 个点")
    return files, counts


# ---------------------------------------------------------------------------
# MANIFEST 与核对
# ---------------------------------------------------------------------------
def _manifest_sha(files: list[dict]) -> str:
    """sorted 的 "<relpath>:<sha256>" 行 "\\n".join 取 sha256 前 16 位。"""
    lines = sorted(f"{e['path']}:{e['sha256']}" for e in files)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def build_manifest(
    files: list[dict], counts: dict, source_roots: dict
) -> dict[str, Any]:
    """组装 MANIFEST 对象（git 溯源失败给 None 不炸，同 provenance 铁律）。"""
    try:
        from custos.research import provenance as _prov  # noqa: PLC0415

        sha, dirty = _prov.git_sha(), _prov.git_dirty()
    except Exception:  # noqa: BLE001
        sha = dirty = None
    entries = sorted(files, key=lambda e: e["path"])
    return {
        "schema": MANIFEST_SCHEMA,
        "manifest_sha": _manifest_sha(entries),
        "created_at": cn_now().isoformat(),
        "git_sha": sha,
        "git_dirty": dirty,
        "segments": SEGMENTS,
        "keep_rules": KEEP_RULES,
        "files": entries,
        "counts": counts,
        "known_limitations": KNOWN_LIMITATIONS,
        "source_roots": source_roots,
    }


def verify(root: Path) -> int:
    """只读核对：逐文件重算 sha256 比对 MANIFEST。全部一致打印 OK 返 0；
    任何不符/缺失返非零并逐一列出。"""
    root = Path(root)
    mpath = root / "MANIFEST.json"
    if not mpath.is_file():
        print(f"❌ MANIFEST 不存在: {mpath}")
        return 1
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    files = manifest.get("files") or []
    problems: list[str] = []
    for entry in files:
        fp = root / entry["path"]
        if not fp.is_file():
            problems.append(f"缺失: {entry['path']}")
        elif _sha256_file(fp) != entry["sha256"]:
            problems.append(f"sha256 不符: {entry['path']}")
    digest = _manifest_sha(files)
    if manifest.get("manifest_sha") != digest:
        problems.append(
            f"manifest_sha 不符: 记录={manifest.get('manifest_sha')} 重算={digest}"
        )
    if problems:
        print(f"❌ 核对失败（{len(problems)} 项）：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"OK: {len(files)} 个文件 sha256 全部一致（manifest_sha={digest}）")
    return 0


def main(
    argv: Optional[list[str]] = None,
    *,
    series_provider: Optional[Callable] = None,
    bars_provider: Optional[Callable] = None,
) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out",
        default=str(BASE / "artifacts" / "cache" / "mining_snapshot"),
        help="快照落点（默认 <repo>/artifacts/cache/mining_snapshot）",
    )
    ap.add_argument("--tar", action="store_true", help="导出完成后打 <out>.tar.gz")
    ap.add_argument("--tdx-root", default=None, help="通达信根（默认 paths.TDX_ROOT）")
    ap.add_argument("--data-root", default=None, help="data 根（默认 paths.DATA）")
    ap.add_argument("--verify", default=None, help="只读核对指定快照目录后退出")
    args = ap.parse_args(argv)

    if args.verify:
        return verify(Path(args.verify))

    raw_out = Path(args.out)
    raw_cache = BASE / "artifacts" / "cache"
    out = raw_out.resolve()
    base_root = BASE.resolve()
    cache_root = raw_cache.resolve()
    # 原始路径与 resolve 后路径都要判：data/ 是指向仓外的 junction 时，前者能在
    # 任何导出前拦住仓库相对路径，后者兜住符号链接别名；两者缺一不可。
    if (
        raw_out.is_relative_to(BASE) and not raw_out.is_relative_to(raw_cache)
    ) or (out.is_relative_to(base_root) and not out.is_relative_to(cache_root)):
        print(f"❌ --out 位于仓库内且不在 artifacts/cache/ 之下（防误入库）: {out}")
        return 2

    tdx_root = Path(args.tdx_root) if args.tdx_root else Path(TDX_ROOT)
    data_root = Path(args.data_root) if args.data_root else DATA
    provider = series_provider or _default_series_provider
    print(f"导出挖掘段快照 → {out}\n  tdx_root={tdx_root}\n  data_root={data_root}")
    try:
        result = _export_all(out, tdx_root, data_root, provider, bars_provider)
    except Exception as exc:  # noqa: BLE001 — 运维脚本：失败留现场（无 MANIFEST）
        print(f"❌ 导出失败: {exc}")
        return 2
    if result is None:
        print("❌ 0AMV regime 序列按保留日期过滤后为空——非零退出，不写 MANIFEST")
        return 2
    files, counts = result

    # MANIFEST 最后写（先数据后清单）
    manifest = build_manifest(
        files, counts, {"tdx_root": str(tdx_root), "data_root": str(data_root)}
    )
    (out / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        f"MANIFEST.json 已写：{len(files)} 个文件，manifest_sha={manifest['manifest_sha']}"
    )

    if args.tar:
        tar_path = out.parent / f"{out.name}.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(out, arcname=out.name)
        print(f"tar 包: {tar_path}")
    print("done")
    return 0


if __name__ == "__main__":
    # 入口 stdout/stderr 钉 utf-8（带 hasattr 守卫，同 backtest_factors 口径）：
    # Windows GBK 控制台/重定向下 ⚠️/❌ 等字符会 UnicodeEncodeError 掩盖真实退出码。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
