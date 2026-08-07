# -*- coding: utf-8 -*-
"""E:\\S_DATA 数据接入(qlib bundle / 单票 CSV)——供回测用的只读 loader。

⚠️ 2026-08-07 从 `screening/` 移到 `07_tools/` 根层。它是**数据层**模块
（零内部依赖，只用 stdlib + numpy/pandas），放在选股目录里会让
`local_tdx/` 的探针与对账工具（数据层）反向依赖 `screening/`（L3）。
移动后也只剩**一条导入路径**（扁平 `import s_data`）——
此前只有 `tests/test_s_data.py` 用 `from screening import s_data`，
而 `s_data.list_universe` 是被 monkeypatch 的目标，
两个模块对象会让打桩静默失效。

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


def _warn_if_nothing_loaded(out: dict, codes: list, source: str, root) -> None:
    """请求了 N 只票却一只都没读到 → 出声。调用方(回测/研究)据此区分"数据没挂上"
    与"因子没命中";此前空 dict 静默返回,一路走成 exit 0 的空结论(审计 E9)。"""
    if codes and not out:
        _warn(f"{source} 加载 0/{len(codes)} 只(root={root}):一根 K 线都没读到,"
              "请确认数据根目录与代码列表")


def _exchange(code6: str) -> str:
    """6 位代码 → 交易所前缀(SH/SZ/BJ)。"""
    if code6[:2] in ("60", "68"):
        return "SH"
    if code6[:2] in ("00", "30"):
        return "SZ"
    return "BJ"          # 4xxxxx/8xxxxx/920xxx 北交所


def bundle_convention(bundle_dir: Path) -> str:
    """判定一个 bundle 的价格口径：``"multiplicative"`` / ``"unverified"`` / ``"unknown"``。

    ⚠️ **实测（2026-08-06）`E:\\S_DATA\\Q_DATA` 下两个 bundle 是两种口径**：

        2006_2020  字段含 factor + change
                   · change 与 close.pct_change() 一致率 **100%**
                   · 除权日 close 平滑 ⇒ close 已复权
                   · factor 分段常数、**21 个取值对应 20 个事件**、事件日阶梯上升
                   ⇒ **标准乘法复权** ✅

        2021_2026  只有 OHLCV，**没有 factor**
                   · `raw − close` 分段常数，段边界=除权日，
                     **相邻段之差恰好等于该次每股现金分红**
                     （600519: 194.99→173.31，差 21.68 = 2021 年报分红 21.675）
                   ⇒ **加法调整（减去累计现金分红）** ❌

    加法调整保留绝对价差但把分母减小 ⇒ **百分比收益系统性放大**（实测高分红股 13~21%），
    涨跌幅甚至能超过涨跌停限制。B1 的止损/J 值/涨跌幅全是百分比 ⇒ **不能用**。

    ⚠️ **判据只能做到「有 factor ⇒ 乘法」，反向不成立。** 缺 factor 只说明
    **口径无法从 bundle 内部验证**，不等于一定是加法——「2021_2026 是加法」这个结论
    来自对账（`raw − close` 分段常数、相邻段之差=每股分红），不是来自字段缺失。
    所以缺 factor 记作 `"unverified"` 而非 `"additive"`，别把推测写成结论。

    但对研究工具而言「无法验证口径」已足够危险：错的价格会静默产出错的结论。
    所以 `load_bars_qlib` 默认**跳过 unverified**（可显式放行）。
    完整检验用 `reconcile_qfq.py --qlib-selfcheck CODE`。
    """
    feat = bundle_dir / "features"
    if not feat.is_dir():
        return "unknown"
    for inst in feat.iterdir():                    # 抽第一只有数据的票看字段
        if not inst.is_dir():
            continue
        names = {p.name.split(".")[0] for p in inst.glob("*.bin")}
        if not names:
            continue
        return "multiplicative" if "factor" in names else "unverified"
    return "unknown"


def list_bundles(root: str | Path = DEFAULT_Q_ROOT) -> list[dict[str, Any]]:
    """扫描 root 下含 calendars/day.txt 的 qlib bundle,按日历首日期升序返回。
    每项 {dir, calendar(list[str]), start, end, convention}。异常 bundle 跳过。

    ⚠️ 空列表必须**出声**:静默返回 [] 会一路走成"0 只票→0 条信号→exit 0",
    最后被读成"因子无判别力"而不是"数据根本没挂上"(审计 E9)。"""
    root = Path(root)
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        _warn(f"qlib root 不存在: {root}")
        return out
    n_sub = 0
    for sub in sorted(root.iterdir()):
        n_sub += 1
        cal_path = sub / "calendars" / "day.txt"
        if not cal_path.is_file():
            continue
        try:
            cal = [ln.strip() for ln in cal_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if cal:
                out.append({"dir": sub, "calendar": cal, "start": cal[0], "end": cal[-1],
                            "convention": bundle_convention(sub)})
        except Exception as exc:  # noqa: BLE001
            _warn(f"读取日历失败 {cal_path}: {exc}")
    if not out:
        _warn(f"qlib root 存在但未发现任何 bundle(子项 {n_sub} 个,均缺 calendars/day.txt): {root}"
              " —— 后续加载必然全空,请先确认 S_DATA_ROOT / --s-data-root")
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


def _bundle_field_set(bundle_dir: Path, inst: str) -> frozenset[str]:
    """该 bundle 里这只票实有的字段名（去掉 `.day.bin` 后缀）。"""
    fdir = bundle_dir / "features" / inst
    if not fdir.is_dir():
        return frozenset()
    return frozenset(p.name.split(".")[0] for p in fdir.glob("*.bin"))


_MIXED_WARNED: set[str] = set()


def _warn_if_mixed_convention(code: str, hits: list[tuple[Path, str]]) -> None:
    """跨 bundle 拼接前检查各 bundle 的**字段集是否一致** —— 不一致就可能是不同价格口径。

    ⚠️ 2026-08-06 实测：`E:\\S_DATA\\Q_DATA` 下两个 bundle 的字段集**不同**：

        2006_2020   open/high/low/close/volume + **factor** + **change**
        2021_2026   只有 open/high/low/close/volume

    而对 2021_2026 的对账证明它的价格是「减去累计现金分红」的**加法调整**
    （`raw − qlib` 分段常数、相邻段之差恰好等于每股分红），不是乘法前复权。
    有 `factor` 的老 bundle 很可能是标准 qlib dump（乘法）。

    ⇒ **两个 bundle 可能是两种价格口径**，而 `load_bars_qlib` 会把它们直接 concat
    ——那 10 个月缺口正好在两者之间，**任何长窗口都会跨过去**，接出来的序列在缺口两侧
    口径不同，收益率在接缝处失真。这是静默的：拼接不报错、结果看起来像一条完整曲线。

    所以这里出声。每个代码只警告一次，避免全市场跑批时刷屏。
    """
    if len(hits) < 2 or code in _MIXED_WARNED:
        return
    sets = {bd.name: _bundle_field_set(bd, inst) for bd, inst in hits}
    uniq = {frozenset(v) for v in sets.values() if v}
    if len(uniq) > 1:
        _MIXED_WARNED.add(code)
        desc = "; ".join(f"{k}={sorted(v)}" for k, v in sets.items())
        _warn(f"{code} 跨 bundle 拼接，但各 bundle **字段集不同** ⇒ 可能是不同价格口径，"
              f"接缝处收益率会失真：{desc}"
              f" —— 详见 00_governance/data/QLIB_LOCAL_DATA.md「加法调整」")


_UNVERIFIED_SKIP_WARNED: set[str] = set()


def load_bars_qlib(codes: list[str], count: int, start: Optional[str] = None,
                   end: Optional[str] = None, root: str | Path = DEFAULT_Q_ROOT,
                   allow_unverified: bool = False) -> dict[str, pd.DataFrame]:
    """从 qlib bundle 读日线。start/end(YYYY-MM-DD)在 count 之前应用;跨 bundle 段拼接去重。

    ⚠️ **默认跳过「口径无法验证」的 bundle**（缺 `factor`，2026-08-06 起）。
    实测 `2021_2026`（正是缺 factor 的那个）的价格是
    「减去累计现金分红」的加法调整 ⇒ **百分比收益被系统性放大**（高分红股 13~21%），
    涨跌幅能超过涨跌停限制。而 B1 的止损/J 值/涨跌幅全是百分比 ⇒ 用它必然失真。

    好在**坏的那段（2021-08 起）恰好是本地 vipdoc 有数据的时段**，所以推荐组合是：

        1999-11 ~ 2020-09   老 bundle（含退市股 + 乘法复权 + 自带 factor）
        2020-09 ~ 2021-08   bundle 缺口，无数据
        2021-06 ~ 今        tdx vipdoc（乘法复权，已对账通过）

    ⇒ 不需要重做数据，只需要不用那一份。`allow_unverified=True` 可显式放行
    （比如只关心绝对价差、不算百分比收益的研究）。
    详见 `00_governance/data/QLIB_LOCAL_DATA.md`。
    """
    bundles = list_bundles(root)
    if not allow_unverified:
        unver = [b for b in bundles if b.get("convention") == "unverified"]
        if unver:
            key = ",".join(sorted(b["dir"].name for b in unver))
            if key not in _UNVERIFIED_SKIP_WARNED:
                _UNVERIFIED_SKIP_WARNED.add(key)
                _warn(f"跳过**口径无法验证**的 bundle {key}（缺 factor 字段）。"
                      f"实测缺 factor 的 2021_2026 是**加法调整**（价格=原始价−累计现金"
                      f"分红 ⇒ 百分比收益放大 13~21%，涨跌幅可超涨跌停）。"
                      f"该时段改用 tdx vipdoc；确需放行传 allow_unverified=True")
            bundles = [b for b in bundles if b.get("convention") != "unverified"]
    if not bundles:
        _warn("没有可用 bundle（可能全被判为口径无法验证而跳过）")
        # ⚠️ 必须走同一条空结果护栏（审计 E9）：静默 return {} 会让"0 只票→0 条信号
        # →exit 0"被读成"因子无判别力"。这条 early-return 第一版漏了它，
        # 由 test_audit_p3_research::test_s_data_warns_when_nothing_loaded 抓到。
        _warn_if_nothing_loaded({}, codes, "qlib", root)
        return {}
    # ⚠️ 请求窗口与可用 bundle **完全不相交**时必须出声。
    # 弃用 2021_2026 之后 qlib 只覆盖 1999-2020，而 `--cross-window` 用的
    # 2022-01~2024-12 落在其外 ⇒ 会返回空。若只靠"0 行"，又会被读成"因子无判别力"
    # （审计 E9 那个失效模式）。这里直接说清「请求的窗口没有数据」。
    if start or end:
        cov = [(b["start"], b["end"]) for b in bundles]
        w0, w1 = start or "0000-00-00", end or "9999-99-99"
        if not any(not (w1 < s0 or w0 > s1) for s0, s1 in cov):
            _warn(f"请求窗口 {w0}~{w1} 与可用 bundle 区间**完全不相交**"
                  f"（可用：{'; '.join(f'{a}~{b}' for a, b in cov)}）⇒ 必然返回空。"
                  f"该时段请改用 tdx vipdoc（--data-source tdx）")
    by_dir = {b["dir"]: b for b in bundles}
    out: dict[str, pd.DataFrame] = {}
    for c in codes:
        try:
            hits = code_to_qlib_dir(c, bundles)
            _warn_if_mixed_convention(c, hits)
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
    _warn_if_nothing_loaded(out, codes, "qlib", root)
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
    _warn_if_nothing_loaded(out, codes, "csv", root)
    return out


def list_universe(root: str | Path = DEFAULT_Q_ROOT, source: str = "qlib",
                  allow_unverified: bool = False) -> list[str]:
    """s_data 全市场宇宙(6 位代码)。qlib=各 bundle instruments/all.txt 并集;csv=列目录文件名。

    ⚠️ **必须与 `load_bars_qlib` 用同一套 bundle 过滤**（2026-08-06 加 `allow_unverified`）。
    否则：宇宙里含被跳过 bundle 的 instrument，而价格加载时那个 bundle 不读
    ⇒ 2020-09 之后上市的票**静默无数据**、被当成"这只票没信号"。
    实测 `2021_2026` 有 5484 只 instrument，跳过它却仍把这些票放进宇宙，
    就会产出一个「一半票拿不到数据」的宇宙而毫无提示。

    ⚠️ **不要用 `ln[-6:]` 取代码**（2026-08-06 实测修）。qlib 的 `instruments/all.txt` 是
    **制表符分隔**的 `SH600000\\t1999-11-10\\t2026-02-27`，末 6 字符取到的是结束日期尾巴
    ——实测宇宙里混进了 `'-06-09'`、`'-09-25'` 两条垃圾。

    改为：按空白切分取第 0 段、剥市场前缀、**校验必须是 6 位数字**。
    并且当剔除率超过 5% 时**大声告警** —— 若哪天 bundle 换了格式，
    `ln[-6:]` 那种写法会让整个宇宙静默变成日期碎片而函数照样"成功"返回，
    这正是本仓库反复踩的静默失效。
    """
    root = Path(root)
    codes: set[str] = set()
    n_lines = 0
    rejected: list[str] = []
    try:
        if source == "csv":
            for p in root.glob("*-all-latest.csv"):
                head = p.name.split("-")[0]           # 000001.SZ
                codes.add(head.split(".")[0])
        else:
            bundles = list_bundles(root)
            if not allow_unverified:
                skipped = [b["dir"].name for b in bundles
                           if b.get("convention") == "unverified"]
                if skipped:
                    _warn(f"宇宙也跳过口径无法验证的 bundle {','.join(skipped)}"
                          f"（与 load_bars_qlib 保持同一口径；否则宇宙里会有拿不到"
                          f"价格的票）")
                bundles = [b for b in bundles if b.get("convention") != "unverified"]
            for b in bundles:
                inst = b["dir"] / "instruments" / "all.txt"
                if not inst.is_file():
                    continue
                for ln in inst.read_text(encoding="utf-8").splitlines():
                    ln = ln.strip()
                    if not ln:
                        continue
                    n_lines += 1
                    tok = ln.split()[0]              # SH600000 / 600000.SH / 600000
                    digits = "".join(ch for ch in tok if ch.isdigit())
                    if len(digits) == 6:
                        codes.add(digits)
                    else:
                        if len(rejected) < 5:
                            rejected.append(ln[:40])
            if n_lines and len(rejected) / n_lines > 0.05:
                _warn(f"instruments/all.txt 有 {len(rejected)}/{n_lines} 行取不出 6 位代码"
                      f"（样例 {rejected}）—— bundle 格式可能变了，宇宙不可信")
    except Exception as exc:  # noqa: BLE001
        _warn(f"列 universe 失败: {exc}")
    return sorted(codes)
