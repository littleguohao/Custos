# -*- coding: utf-8 -*-
"""E:\\S_DATA 数据接入(qlib bundle / 单票 CSV)——供回测用的只读 loader。

数据概况(2026-07 探明):
- Q_DATA 下若干 qlib bundle(2006_2020: 1999-11→2020-09; 2021_2026: 2021-08→2026-02),
  **含退市股**(point-in-time 宇宙,可消幸存者偏差),价格为**前复权**(与 tdx 未复权比价,因子随分红阶梯)。
  ⚠️ 两 bundle 间有约 10 个月缺口(2020-09-28 → 2021-07-30)。
- CSV_DATA 为 2021_2026 bundle 的单票 CSV 冗余副本(Date,Code,Open..Amount)。
- qlib bin 格式: np.fromfile(dtype='<f4'),首元素=start_index,其后与 calendars/day.txt 逐日对齐;
  停牌/未上市段为 NaN。

接口与 backtest_factors loader 约定一致: {6位代码: DataFrame[date,open,high,low,close,volume]}。
纯只读,绝不 raise(失败返回空 dict 并 stderr WARN)。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

# 根目录可用环境变量 S_DATA_ROOT 覆盖(默认 Windows 上的 E:\S_DATA),
# 否则研究链只能在那台机器上跑,walk-forward 无法在 CI/Linux 自动化。
S_DATA_ROOT = os.environ.get("S_DATA_ROOT") or r"E:\S_DATA"
DEFAULT_Q_ROOT = str(Path(S_DATA_ROOT) / "Q_DATA")
DEFAULT_CSV_ROOT = str(Path(S_DATA_ROOT) / "CSV_DATA")
_FIELDS = ("open", "high", "low", "close", "volume")


def _warn(msg: str) -> None:
    print(f"[WARN] s_data: {msg}", file=sys.stderr)


def _exchange(code6: str) -> str:
    """6 位代码 → 交易所前缀(SH/SZ/BJ)。"""
    if code6[:2] in ("60", "68"):
        return "SH"
    if code6[:2] in ("00", "30"):
        return "SZ"
    return "BJ"          # 4xxxxx/8xxxxx/920xxx 北交所


def list_bundles(root: str | Path = DEFAULT_Q_ROOT) -> list[dict[str, Any]]:
    """扫描 root 下含 calendars/day.txt 的 qlib bundle,按日历首日期升序返回。
    每项 {dir, calendar(list[str]), start, end}。异常 bundle 跳过。"""
    root = Path(root)
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        _warn(f"qlib root 不存在: {root}")
        return out
    for sub in sorted(root.iterdir()):
        cal_path = sub / "calendars" / "day.txt"
        if not cal_path.is_file():
            continue
        try:
            cal = [ln.strip() for ln in cal_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if cal:
                out.append({"dir": sub, "calendar": cal, "start": cal[0], "end": cal[-1]})
        except Exception as exc:  # noqa: BLE001
            _warn(f"读取日历失败 {cal_path}: {exc}")
    out.sort(key=lambda b: b["start"])
    return out


def code_to_qlib_dir(code6: str, bundles: list[dict[str, Any]]) -> list[tuple[Path, str]]:
    """6 位代码 → 各 bundle 内的 (bundle_dir, instrument_dir) 列表(跨 bundle 可多个,按日历升序)。
    先按交易所前缀规则定位,不存在则扫 features/ 后缀兜底。"""
    pref = _exchange(code6) + code6
    hits: list[tuple[Path, str]] = []
    for b in bundles:
        fdir = b["dir"] / "features"
        if (fdir / pref).is_dir():
            hits.append((b["dir"], pref))
            continue
        try:  # 兜底:任何前缀+该 6 位后缀
            for d in fdir.iterdir():
                if d.is_dir() and d.name.endswith(code6):
                    hits.append((b["dir"], d.name))
                    break
        except Exception:  # noqa: BLE001
            continue
    return hits


def _read_field_bin(fdir: Path, field: str, cal_len: int) -> Optional[np.ndarray]:
    p = fdir / f"{field}.day.bin"
    if not p.is_file():
        return None
    arr = np.fromfile(p, dtype="<f4")
    if arr.size < 2:
        return None
    si = int(arr[0])
    vals = arr[1:]
    out = np.full(cal_len, np.nan, dtype="<f8")
    lo = max(si, 0)
    hi = min(si + vals.size, cal_len)
    if hi > lo:
        out[lo:hi] = vals[lo - si: hi - si]
    return out


def _load_one_qlib(bundles_by_dir: dict[Path, dict], bundle_dir: Path, inst: str) -> Optional[pd.DataFrame]:
    cal = bundles_by_dir[bundle_dir]["calendar"]
    fdir = bundle_dir / "features" / inst
    cols: dict[str, np.ndarray] = {}
    for f in _FIELDS:
        a = _read_field_bin(fdir, f, len(cal))
        if a is None:
            return None
        cols[f] = a
    df = pd.DataFrame({"date": cal, **cols})
    return df.dropna(subset=["close"])          # 丢停牌/未上市/已退市段


def load_bars_qlib(codes: list[str], count: int, start: Optional[str] = None,
                   end: Optional[str] = None, root: str | Path = DEFAULT_Q_ROOT) -> dict[str, pd.DataFrame]:
    """从 qlib bundle 读日线。start/end(YYYY-MM-DD)在 count 之前应用;跨 bundle 段拼接去重。"""
    bundles = list_bundles(root)
    by_dir = {b["dir"]: b for b in bundles}
    out: dict[str, pd.DataFrame] = {}
    for c in codes:
        try:
            hits = code_to_qlib_dir(c, bundles)
            segs = [_load_one_qlib(by_dir, bd, inst) for bd, inst in hits]
            segs = [s for s in segs if s is not None and len(s)]
            if not segs:
                continue
            df = (pd.concat(segs).drop_duplicates(subset=["date"]).sort_values("date")
                    .reset_index(drop=True))
            if start:
                df = df[df["date"] >= start]
            if end:
                df = df[df["date"] <= end]
            if count:
                df = df.tail(count).reset_index(drop=True)
            if len(df):
                out[c] = df
        except Exception as exc:  # noqa: BLE001
            _warn(f"加载 {c} 失败(qlib): {exc}")
    return out


def load_bars_csv(codes: list[str], count: int, start: Optional[str] = None,
                  end: Optional[str] = None, root: str | Path = DEFAULT_CSV_ROOT) -> dict[str, pd.DataFrame]:
    """从单票 CSV 读日线({code6}.{EX}-all-latest.csv,列 Date,Code,Open..Amount)。"""
    root = Path(root)
    out: dict[str, pd.DataFrame] = {}
    for c in codes:
        p = root / f"{c}.{_exchange(c)}-all-latest.csv"
        if not p.is_file():
            continue
        try:
            df = pd.read_csv(p, dtype={"Code": str})
            df.columns = [x.lower() for x in df.columns]
            df = df[["date", "open", "high", "low", "close", "volume"]].dropna(subset=["close"])
            df["date"] = df["date"].astype(str).str[:10]
            df = df.sort_values("date").reset_index(drop=True)
            if start:
                df = df[df["date"] >= start]
            if end:
                df = df[df["date"] <= end]
            if count:
                df = df.tail(count).reset_index(drop=True)
            if len(df):
                out[c] = df
        except Exception as exc:  # noqa: BLE001
            _warn(f"加载 {c} 失败(csv): {exc}")
    return out


def list_universe(root: str | Path = DEFAULT_Q_ROOT, source: str = "qlib") -> list[str]:
    """s_data 全市场宇宙(6 位代码)。qlib=各 bundle instruments/all.txt 并集;csv=列目录文件名。"""
    root = Path(root)
    codes: set[str] = set()
    try:
        if source == "csv":
            for p in root.glob("*-all-latest.csv"):
                head = p.name.split("-")[0]           # 000001.SZ
                codes.add(head.split(".")[0])
        else:
            for b in list_bundles(root):
                inst = b["dir"] / "instruments" / "all.txt"
                if inst.is_file():
                    for ln in inst.read_text(encoding="utf-8").splitlines():
                        ln = ln.strip()
                        if ln:
                            codes.add(ln[-6:])        # SZ000001 → 000001
    except Exception as exc:  # noqa: BLE001
        _warn(f"列 universe 失败: {exc}")
    return sorted(codes)
