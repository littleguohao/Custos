# -*- coding: utf-8 -*-
"""Unified local TDX data access layer for strategy_team.

This module wraps mootdx (online + offline) and provides stable helpers for:
- stock/index/880-series K-line data (via mootdx Reader + online bars)
- real-time quotes (via mootdx quotes)
- financial data (via mootdx Affair)
- adjusted prices (via mootdx get_adjust_year)
- sector lists (via mootdx Reader.block)

Replaces the previous tqcenter/vipdoc binary parsing with community-maintained mootdx.
"""
from __future__ import annotations

import json
import math
import os
import sys
import warnings
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE, TDX_ROOT, cn_today  # noqa: E402
from code_utils import market_of, norm_code as _cu_norm_code  # noqa: E402

# --- mootdx lazy initialization ---
_reader = None
_client = None


def _get_reader():
    global _reader
    if _reader is None:
        from mootdx.reader import Reader
        _assert_tdx_root()
        _reader = Reader.factory(market="std", tdxdir=str(TDX_ROOT))
    return _reader


def _get_client():
    global _client
    if _client is None:
        from mootdx.quotes import Quotes
        _client = Quotes.factory(market="std", quiet=True)
    return _client


class LocalTdxError(RuntimeError):
    pass


# 已校验通过的 TDX_ROOT（按路径字符串缓存：校验只做一次，但改了 TDX_ROOT 会重新校验）
_tdx_root_verified: set[str] = set()


def _assert_tdx_root(root: Optional[Path] = None) -> Path:
    """校验 TDX_ROOT 真的指向通达信安装目录（存在且含 vipdoc），否则 raise。

    为什么必须显式校验：`paths.TDX_ROOT` 的默认值是 Windows 路径 ``E:\\new_tdx64``，
    在 Linux/容器里没设环境变量时它只是个不存在的路径。此时 mootdx Reader 与
    vipdoc 直读**都只返回空 DataFrame**，于是「装了通达信但这只票没数据」和
    「根本没配通达信路径」表现得一模一样：全市场初筛 universe 为空、
    技术指标全缺，报告却照常生成（2026-07-30 事故的症状）。
    配置错误必须当场炸出来，绝不能伪装成「市场今天没行情」。
    """
    root = Path(root) if root else TDX_ROOT
    key = str(root)
    if key in _tdx_root_verified:
        return root
    if not root.is_dir():
        raise LocalTdxError(
            f"TDX_ROOT 无效: {root} 不存在。默认值 E:\\new_tdx64 只是 Windows 占位，"
            f"非 Windows 环境必须设置环境变量 TDX_ROOT 指向通达信安装目录")
    if not (root / "vipdoc").is_dir():
        raise LocalTdxError(
            f"TDX_ROOT={root} 下没有 vipdoc 目录，不是有效的通达信安装目录"
            f"（本地日线全部读不到，会表现为『全市场无数据』）")
    _tdx_root_verified.add(key)
    return root


def _empty_with_reason(reason: str) -> pd.DataFrame:
    """带原因的空 DataFrame。

    空 DataFrame 本身有三义：文件不存在 / 解析失败 / 该票确实没有这一天的数据。
    调用方靠 ``df.attrs["missing_reason"]`` 区分，才能决定是回退在线源、
    还是把「本地数据缺失」这件事报给下游，而不是一律当成「没数据」。
    """
    df = pd.DataFrame()
    df.attrs["missing_reason"] = reason
    return df


def normalize_code(code: str) -> str:
    """Normalize code to TQ suffix format (delegates to code_utils.norm_code)."""
    s = str(code).strip().upper()
    if not s:
        return s
    return _cu_norm_code(s)


def _strip_suffix(code: str) -> str:
    """Return pure 6-digit code without suffix."""
    s = str(code).strip().upper()
    if "." in s:
        s = s.split(".")[0]
    return s.zfill(6)


def _get_market_code(code: str) -> int:
    """Return mootdx market int: 0=SZ, 1=SH (BJ handled separately via _is_bj_code)."""
    return 1 if market_of(code) == "SH" else 0


def _is_bj_code(code: str) -> bool:
    """Check if code is a Beijing Stock Exchange stock.

    Delegates to code_utils.market_of: explicit suffix wins (.BJ -> True;
    .SH/.SZ -> False, e.g. 880xxx.SH is an SH statistics index, not a BJ
    stock). Only suffix-less codes use the prefix heuristic, which excludes
    the 880 index series.
    """
    return market_of(code) == "BJ"


def _read_bj_vipdoc_daily(code: str) -> "pd.DataFrame":
    """Read BJ vipdoc .day file directly (mootdx Reader misroutes 920xxx to SH)."""
    import struct
    raw = _strip_suffix(code)
    path = TDX_ROOT / "vipdoc" / "bj" / "lday" / f"bj{raw}.day"
    if not path.exists():
        return _empty_with_reason(f"file_not_found: {path}")
    # TDX .day format: 32 bytes per record
    # int date, int open, int high, int low, int close, float amount, int volume, int reserved
    records = []
    with open(path, "rb") as f:
        while True:
            buf = f.read(32)
            if len(buf) < 32:
                break
            date_int, o, h, l, c, amt, vol, _ = struct.unpack("<IIIIIfII", buf[:32])
            if date_int == 0:
                continue
            dt = pd.Timestamp(year=date_int // 10000, month=(date_int // 100) % 100, day=date_int % 100)
            records.append({
                "date": dt,
                "open": o / 100.0,
                "high": h / 100.0,
                "low": l / 100.0,
                "close": c / 100.0,
                "amount": amt,
                "volume": vol,
            })
    if not records:
        return _empty_with_reason(f"empty_file: {path}")
    return pd.DataFrame(records)


# ========== K-line data ==========

def read_vipdoc_daily(code: str, strict: bool = False) -> pd.DataFrame:
    """Read local vipdoc daily K-line via mootdx Reader.

    Returns columns: date, open, high, low, close, amount, volume.

    读不到时返回**带 ``attrs["missing_reason"]`` 的空 DataFrame**（file_not_found /
    empty_file / reader_empty），调用方可据此区分「配置/文件缺失」与「确实无数据」。
    ``strict=True`` 时直接 raise ``LocalTdxError`` —— 给那些「拿不到本地数据就必须
    停下来」的调用方（回测 universe、EOD 校验）用；默认 False 保持老调用方行为
    （它们大多有在线回退，raise 会把回退路径一并跳掉）。
    TDX_ROOT 本身配错一律 raise（与单只票缺数据不是一回事，见 ``_assert_tdx_root``）。
    """
    _assert_tdx_root()
    # BJ stocks: mootdx Reader misroutes 920xxx to SH, parse .day directly
    if _is_bj_code(code):
        df = _read_bj_vipdoc_daily(code)
        if df.empty:
            if strict:
                raise LocalTdxError(
                    f"read_vipdoc_daily({code}) 无数据: {df.attrs.get('missing_reason')}")
            return df
        df["code"] = normalize_code(code)
        df["source"] = "vipdoc_bj_direct"
        return df[["date", "code", "open", "high", "low", "close", "amount", "volume", "source"]]

    reader = _get_reader()
    raw = _strip_suffix(code)
    try:
        df = reader.daily(symbol=raw)
    except Exception as e:
        raise LocalTdxError(f"Reader.daily({raw}) failed: {e}")
    if df is None or df.empty:
        if strict:
            raise LocalTdxError(f"read_vipdoc_daily({code}) 无数据: reader_empty")
        return _empty_with_reason(f"reader_empty:{raw}")
    df = df.copy()
    df["code"] = normalize_code(code)
    df["source"] = "mootdx_reader"
    df.index.name = "date"
    df = df.reset_index()
    return df[["date", "code", "open", "high", "low", "close", "amount", "volume", "source"]]


def read_e_odata_daily(code: str) -> pd.DataFrame:
    """Read downloaded CSV cache from E:\\O_DATA (kept for backward compat)."""
    tcode = normalize_code(code)
    path = Path(os.environ.get("TDX_E_ODATA", r"E:\O_DATA")) / f"{tcode}-all-latest.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame()
    rename = {"Date": "date", "Code": "code", "Open": "open", "High": "high",
              "Low": "low", "Close": "close", "Volume": "volume", "Amount": "amount"}
    df = df.rename(columns=rename)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["code"] = tcode
    df["source"] = "e_odata"
    cols = ["date", "code", "open", "high", "low", "close", "volume", "amount", "source"]
    return df[[c for c in cols if c in df.columns]].dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def get_online_bars(code: str, frequency: int = 9, offset: int = 120, adjust: str = "") -> pd.DataFrame:
    """Fetch K-line from mootdx online server.

    frequency: 0=5m, 1=15m, 2=30m, 3=1h, 9=day, 5=week, 6=month
    adjust: "" = no adjust, "qfq" = front, "hfq" = back
    """
    client = _get_client()
    raw = _strip_suffix(code)
    try:
        kwargs = {"symbol": raw, "frequency": frequency, "offset": offset}
        if adjust:
            kwargs["adjust"] = adjust
        df = client.bars(**kwargs)
    except Exception as e:
        raise LocalTdxError(f"online bars({raw}) failed: {e}")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["code"] = normalize_code(code)
    df["source"] = f"mootdx_online{'_'+adjust if adjust else ''}"
    df.index.name = "date"
    df = df.reset_index()
    return df


def get_online_index(code: str, market: int = 1, frequency: int = 9, offset: int = 120) -> pd.DataFrame:
    """Fetch index K-line (including 880 series) from mootdx online server.

    market: 0=SZ, 1=SH (880 series use SH)
    """
    client = _get_client()
    raw = _strip_suffix(code)
    try:
        df = client.index(frequency=frequency, market=market, symbol=raw, start=0, offset=offset)
    except Exception as e:
        raise LocalTdxError(f"online index({raw}) failed: {e}")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["code"] = raw
    df["source"] = "mootdx_index"
    df.index.name = "date"
    df = df.reset_index()
    return df


def get_adjusted_daily(code: str, year: str = "", factor: str = "01") -> pd.DataFrame:
    """Get adjusted (qfq/hfq) daily data via mootdx contrib.

    factor: "00"=不复权, "01"=前复权, "02"=后复权
    """
    from mootdx.contrib.adjust import get_adjust_year
    raw = _strip_suffix(code)
    if not year:
        from datetime import date
        year = str(cn_today().year)
    try:
        df = get_adjust_year(symbol=raw, year=year, factor=factor)
    except Exception as e:
        raise LocalTdxError(f"get_adjust_year({raw}) failed: {e}")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["code"] = normalize_code(code)
    df["source"] = f"mootdx_adjust_{factor}"
    df.index.name = "date"
    df = df.reset_index()
    return df


# ========== Real-time quotes ==========

def _clean_price(v: Any) -> Optional[float]:
    """行情价字段清洗：非数 / NaN / inf / 非正价一律 → None。

    为什么不能用 ``float(row.get("price", 0))``：mootdx quotes 在停牌、代码不存在、
    或服务端字段缺失时给出的是缺字段或 NaN，回落 0.0 会把「没有报价」变成
    「价格是 0」—— 下游拿它算涨跌幅得 -100%（直接触发风控止损条件），
    拿 last_close=0 当分母则除零。缺价必须表现为缺，不能表现为一个能参与运算的数。
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f) or f <= 0:
        return None
    return f


def _snapshot_fields(row: Any) -> dict[str, Any]:
    """一行行情 → 清洗后的字段；price 无效时返回 {}（整条快照作废）。"""
    price = _clean_price(row.get("price"))
    if price is None:
        return {}
    out: dict[str, Any] = {"price": price}
    for key in ("last_close", "open", "high", "low"):
        out[key] = _clean_price(row.get(key))     # 无效给 None，绝不给 0.0
    return out


def get_snapshot(code: str) -> dict[str, Any]:
    """Get real-time quote for a single stock. 无有效价时返回 {}（缺价 != 0 价）。"""
    client = _get_client()
    raw = _strip_suffix(code)
    try:
        df = client.quotes(symbol=[raw])
    except Exception as e:
        raise LocalTdxError(f"quotes({raw}) failed: {e}")
    if df is None or df.empty:
        return {}
    fields = _snapshot_fields(df.iloc[0])
    if not fields:
        print(f"[WARN] quotes({raw}) 无有效价（停牌/字段缺失），返回空快照而非 0 价",
              file=sys.stderr)
        return {}
    return {"code": raw, **fields}


def get_snapshots(codes: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Get real-time quotes for multiple stocks. 无有效价的代码**不出现在结果里**。

    宁可让调用方发现「这只票没拿到行情」（缺失可检测），也不能给它一条 0 价快照
    （0 价会被当真值参与涨跌幅/仓位计算）。
    """
    client = _get_client()
    raw_codes = [_strip_suffix(c) for c in codes]
    try:
        df = client.quotes(symbol=raw_codes)
    except Exception as e:
        raise LocalTdxError(f"quotes batch failed: {e}")
    if df is None or df.empty:
        return {}
    result = {}
    dropped = []
    for _, row in df.iterrows():
        code = str(row.get("code", ""))
        fields = _snapshot_fields(row)
        if not fields:
            dropped.append(code)
            continue
        result[code] = fields
    if dropped:
        print(f"[WARN] quotes batch 丢弃 {len(dropped)} 只无有效价的代码: "
              f"{','.join(dropped[:10])}", file=sys.stderr)
    return result


# ========== Financial data ==========

_financial_cache: dict[str, pd.DataFrame] = {}

# 财务 zip 缓存目录。此前是 `BASE / ".." / "tdx_affair_cache"` —— 写在**项目外**的兄弟目录:
# 不受项目 .gitignore 管、不随项目迁移、在只读父目录下直接失败。改到项目内的运行时数据区。
AFFAIR_CACHE_DIR = BASE / "01_data" / "cache" / "tdx_affair"


def latest_report_period(files: list[dict]) -> str:
    """从 Affair.files() 里挑最新**有内容**的 gpcw 期号;挑不出返回空串。"""
    gpcw = sorted([f for f in (files or []) if str(f.get("filename", "")).startswith("gpcw")],
                  key=lambda x: x["filename"], reverse=True)
    for f in gpcw:
        if f.get("filesize", 0) > 100000:      # 跳过尚未披露的空壳未来报告
            return str(f["filename"]).replace("gpcw", "").replace(".zip", "")
    return ""


def get_financial_data(report_period: str = "") -> pd.DataFrame:
    """Download and parse TDX financial data (gpcwYYYYMMDD).

    Returns DataFrame with 585 columns for ~5500 stocks.
    取不到期号或下载/解析失败时返回**空 DataFrame** 并打 WARN —— 调用方据 `df.empty`
    降级。此前期号为空会去 fetch `gpcw.zip`，fetch/parse 的异常直接冒泡打断整条链。
    """
    from mootdx.affair import Affair
    if not report_period:
        try:
            report_period = latest_report_period(Affair.files())
        except Exception as exc:  # noqa: BLE001 —— 网络/接口变更不得打断调用方
            print(f"[WARN] Affair.files() 失败，财务数据不可用: {exc}", file=sys.stderr)
            return pd.DataFrame()
    if not report_period:
        print("[WARN] Affair 未返回任何有效 gpcw 期号，财务数据不可用", file=sys.stderr)
        return pd.DataFrame()
    cache_key = report_period
    if cache_key in _financial_cache:
        return _financial_cache[cache_key]
    fname = f"gpcw{report_period}.zip"
    download_dir = str(AFFAIR_CACHE_DIR)
    AFFAIR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        Affair.fetch(downdir=download_dir, filename=fname)
        df = Affair.parse(downdir=download_dir, filename=fname)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 财务数据 {fname} 下载/解析失败: {exc}", file=sys.stderr)
        return pd.DataFrame()
    if df is not None and len(df):
        _financial_cache[cache_key] = df
    return df if df is not None else pd.DataFrame()


# ========== Sector data ==========

def get_sector_list() -> list[str]:
    """Get sector names from local TDX block files."""
    reader = _get_reader()
    try:
        blocks = reader.block(symbol="block_zs", group=False)
        if blocks is not None and not blocks.empty:
            return blocks["name"].tolist() if "name" in blocks.columns else []
    except Exception as e:
        print(f"[WARN] get_sector_list failed: {e}", file=sys.stderr)
    return []


def get_stock_list_in_sector(sector: str, block_type: int = 0) -> list[str]:
    """Get stock codes in a sector."""
    reader = _get_reader()
    try:
        blocks = reader.block(symbol="block_zs", group=False)
        if blocks is not None and not blocks.empty:
            mask = blocks["name"] == sector if "name" in blocks.columns else pd.Series([False] * len(blocks))
            subset = blocks[mask]
            return subset["code"].tolist() if "code" in subset.columns else []
    except Exception as e:
        print(f"[WARN] get_stock_list_in_sector({sector}) failed: {e}", file=sys.stderr)
    return []


def get_stock_list(pool_type: str = "5") -> list[str]:
    """Get stock list via mootdx online."""
    client = _get_client()
    from mootdx.consts import MARKET_SH, MARKET_SZ
    result = []
    for mkt in [MARKET_SH, MARKET_SZ]:
        try:
            stocks = client.stocks(market=mkt)
            if stocks is not None and not stocks.empty:
                result.extend(stocks["code"].tolist() if "code" in stocks.columns else [])
        except Exception as e:
            print(f"[WARN] get_stock_list market={mkt} failed: {e}", file=sys.stderr)
    return result


def get_stock_name_map(pool_type: str = "5") -> dict[str, str]:
    """Get {code6: name} for SH+SZ A-shares via mootdx online (best-effort)."""
    client = _get_client()
    from mootdx.consts import MARKET_SH, MARKET_SZ
    result: dict[str, str] = {}
    for mkt in [MARKET_SH, MARKET_SZ]:
        try:
            stocks = client.stocks(market=mkt)
            if stocks is not None and not stocks.empty and "code" in stocks.columns:
                for _, row in stocks.iterrows():
                    code6 = _strip_suffix(str(row["code"]))
                    name = str(row.get("name", "") or "").strip()
                    if code6 and name:
                        result[code6] = name
        except Exception as e:
            print(f"[WARN] get_stock_name_map market={mkt} failed: {e}", file=sys.stderr)
    return result


# ========== JSON/CSV helpers ==========

def _is_ashare_stock_file(market: str, code6: str) -> bool:
    """按 vipdoc 文件的市场目录 + 6位代码判定是否 A 股个股（排除指数/ETF/债券）。

    - sh: 600/601/603/605/688（排除 000/880 指数、5xx ETF/基金）
    - sz: 000/001/002/003/300/301（排除 15/16/18 ETF、399 指数）
    - bj: 43/83/87/88/920
    """
    if market == "sh":
        return code6.startswith(("600", "601", "603", "605", "688"))
    if market == "sz":
        return code6.startswith(("000", "001", "002", "003", "300", "301"))
    if market == "bj":
        return code6.startswith(("43", "83", "87", "88", "920"))
    return False


def list_local_vipdoc_codes(tdx_root: Optional["Path"] = None, ashare_only: bool = True) -> list[str]:
    """枚举本地 vipdoc 实有的日线文件 → 6 位代码列表（回测 universe 首选）。

    直接读磁盘上有什么（TDX_ROOT/vipdoc/{sh,sz,bj}/lday/{prefix}######.day），
    保证代码与 read_vipdoc_daily 能读到的完全一致，避免在线全代码表对不上本地文件。
    ashare_only=True 时仅保留 A 股个股（滤掉指数/ETF/债券）。

    用**默认** TDX_ROOT 时先校验安装目录（配错 → raise）：universe 返回空列表
    与「通达信路径没配」必须区分开，否则全市场初筛会静默缩到只剩自选池。
    显式传 ``tdx_root`` 的调用方（回测/测试指定目录）自己负责，仍返回空列表。
    """
    if tdx_root is None:
        _assert_tdx_root()
    root = Path(tdx_root) if tdx_root else TDX_ROOT
    out: set[str] = set()
    for mkt in ("sh", "sz", "bj"):
        d = root / "vipdoc" / mkt / "lday"
        if not d.exists():
            continue
        for p in d.glob(f"{mkt}*.day"):
            name = p.stem
            if not name.startswith(mkt):
                continue
            code6 = name[len(mkt):]
            if len(code6) != 6 or not code6.isdigit():
                continue
            if ashare_only and not _is_ashare_stock_file(mkt, code6):
                continue
            out.add(code6)
    return sorted(out)


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def save_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def get_ohlcv_table(code: str, count: int = 260, prefer: str = "vipdoc",
                    expect_last_date: str | None = None,
                    adjust: str = "qfq") -> pd.DataFrame:
    """Unified OHLCV reader: try local vipdoc first, fallback to online bars.

    ``expect_last_date`` (YYYY-MM-DD) turns on freshness checking. The local
    vipdoc fallback only triggers when the read *fails or returns nothing* —
    a successful read of a stale file (TongDaXin not yet downloaded after the
    close) is returned as-is, so BBI / N-structure / reversal-K all compute on
    old bars while looking perfectly healthy. When the caller states which day
    it expects, a stale answer is retried online and, if still stale, marked:

        df.attrs["stale"]      True when last bar < expect_last_date
        df.attrs["last_date"]  the last bar's date
        df.attrs["expected"]   what the caller asked for

    ``attrs`` is used rather than an exception so existing callers keep working
    while gaining the ability to detect the condition.

    ``adjust``（owner 2026-08-04 拍板：全链统一前复权）：

        "qfq"（**默认**）  前复权。价格连续，除权日不再有假跳空
        ""/ "none"        未复权原样返回（展示/下单口径；也可用 qfq 结果的 raw_close 列）

    默认设成 `qfq` 而不是保持原行为，是因为「统一前复权」正是要消除
    「哪个调用方记得传参」这类不确定性——漏改的地方自动获得正确口径。
    前复权最新一日因子恒为 1，所以**当日价格与盘面完全一致**，改默认值不会
    让买入价/止损价偏离盘面；受影响的只有历史价格，而那本就该连续。

    结果带 `attrs["adjust"]`（"qfq"/"none"）与 `raw_close` 列（未复权收盘）。
    """
    df = pd.DataFrame()
    if prefer == "vipdoc":
        try:
            df = read_vipdoc_daily(code)
        except Exception as e:
            print(f"[WARN] read_vipdoc_daily({code}) failed, fallback to online: {e}", file=sys.stderr)
            df = pd.DataFrame()
    if df.empty:
        # 空 DataFrame 的具体原因（TDX_ROOT 配错已在 read_vipdoc_daily 里 raise，
        # 这里剩下的是单只票的 file_not_found / reader_empty）留痕再回退在线源
        reason = df.attrs.get("missing_reason") if hasattr(df, "attrs") else None
        if reason:
            print(f"[WARN] {code} 本地 vipdoc 无数据（{reason}），回退在线源", file=sys.stderr)
        try:
            df = get_online_bars(code, offset=count)
        except Exception as e:
            print(f"[WARN] get_online_bars({code}) failed: {e}", file=sys.stderr)
            df = pd.DataFrame()
    elif expect_last_date and _last_bar_date(df) < expect_last_date:
        # 本地读到了,但不是期望的那天 → 再试在线源;拿不到更新的就保留本地并标 stale
        stale_local = df
        try:
            online = get_online_bars(code, offset=count)
        except Exception as e:
            print(f"[WARN] get_online_bars({code}) failed while refreshing stale local: {e}",
                  file=sys.stderr)
            online = pd.DataFrame()
        df = online if (not online.empty
                        and _last_bar_date(online) > _last_bar_date(stale_local)) else stale_local
    if not df.empty and len(df) > count:
        df = df.tail(count).reset_index(drop=True)
    if expect_last_date and not df.empty:
        last = _last_bar_date(df)
        df.attrs["last_date"] = last
        df.attrs["expected"] = expect_last_date
        df.attrs["stale"] = last < expect_last_date
        if df.attrs["stale"]:
            print(f"[WARN] {code} 数据陈旧: 末根 K 线 {last} < 期望 {expect_last_date}",
                  file=sys.stderr)
    if adjust == "qfq" and not df.empty:
        # owner 2026-08-04 拍板：全链统一前复权。未复权数据会把除权跳空当成真实暴跌
        # ⇒ 假止损、假 J<13 信号、假跌停（详见 B1_BACKTEST_FINDINGS「复权口径问题」）。
        # 权息取不到时按未复权返回并在 attrs 留痕（不 raise：一只票的权息拿不到
        # 不该让整条 18:00 选股链停摆），下游可查 attrs["adjust"] 判断。
        from code_utils import is_index                      # noqa: PLC0415
        if is_index(code):
            df.attrs["adjust"] = "n/a-index"                 # 指数不除权，无需复权
        else:
            try:
                from adjust_factors import qfq_table        # noqa: PLC0415
                df = qfq_table(code, df, strict=False)
            except Exception as e:                          # noqa: BLE001
                print(f"[WARN] {code} 前复权失败，按未复权使用: {e}", file=sys.stderr)
                df.attrs["adjust"] = "none"
                df.attrs["adjust_error"] = str(e)
    elif not df.empty:
        df.attrs.setdefault("adjust", "none")
    return df


def _last_bar_date(df: pd.DataFrame) -> str:
    """Last bar's date as YYYY-MM-DD ('' when unavailable)."""
    if df.empty or "date" not in df.columns:
        return ""
    return str(pd.to_datetime(df["date"]).max())[:10]


def get_market_data(*args, **kwargs):
    raise LocalTdxError("get_market_data is deprecated, use read_vipdoc_daily or get_online_bars")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="600150")
    ap.add_argument("--mode", choices=["daily", "online", "index", "adjust", "finance"], default="daily")
    ap.add_argument("--offset", type=int, default=10)
    args = ap.parse_args()
    if args.mode == "daily":
        df = read_vipdoc_daily(args.code)
    elif args.mode == "online":
        df = get_online_bars(args.code, offset=args.offset)
    elif args.mode == "index":
        df = get_online_index(args.code, offset=args.offset)
    elif args.mode == "adjust":
        df = get_adjusted_daily(args.code)
    elif args.mode == "finance":
        df = get_financial_data()
    print(df.tail(args.offset).to_string() if not df.empty else "No data")


if __name__ == "__main__":
    main()
