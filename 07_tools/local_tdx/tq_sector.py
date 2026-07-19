# -*- coding: utf-8 -*-
"""TQ (tqcenter) sector data layer for strategy_team.

独立封装 tqcenter 的板块能力，不改動 local_tdx_data.py 等现有消费者：

- 前置检查 TdxW.exe 是否在运行（tasklist 子进程，免费方式）。
- 惰性 tq.initialize（使用本模块文件路径），显式 close。
- 所有失败模式转为结构化错误返回（error 字段），绝不 raise 到调用方。
- 板块名称来自本地 ``T0002/hq_cache/tdxzs3.cfg``（GBK、管道分隔，tdxzs.cfg 的超集，
  额外含 881xxx 细分行业），该文件同时提供官方板块类型字段
  （2=行业 3=地区 4=概念 5=风格/统计指数 12=细分行业）。

CLI::

    uv run python 07_tools/local_tdx/tq_sector.py --date YYYY-MM-DD [--limit N] [--progress]

输出 ``01_data/sectors/{date}_tq_sector_map.json``，并打印一行 JSON 摘要。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import SECTORS_DIR, TDX_PYPLUGINS, TDX_ROOT  # noqa: E402

SOURCE = "tq_tqcenter"
# tdxzs3.cfg 是 tdxzs.cfg 的超集（额外含 467 个 881xxx 细分行业），优先使用
TDXZS_CFG_CANDIDATES = (
    TDX_ROOT / "T0002" / "hq_cache" / "tdxzs3.cfg",
    TDX_ROOT / "T0002" / "hq_cache" / "tdxzs.cfg",
)

# tdxzs*.cfg 第 3 字段（官方板块类型）→ 分类
_TDX_TYPE_CATEGORY = {
    "2": "industry",
    "3": "region",
    "4": "concept",
    "5": "style",
    "12": "sub_industry",
}

_CODE_RE = re.compile(r"^(88[01]\d{3})(?:\.(?:SH|SZ|BJ))?$", re.IGNORECASE)


def _err(code: str, detail: str = "", **extra: Any) -> dict:
    """结构化错误返回。"""
    out = {"error": code}
    if detail:
        out["detail"] = str(detail)
    out.update(extra)
    return out


def is_tdxw_running() -> bool:
    """免费方式探测 TdxW.exe 是否在运行（tasklist 子进程）。"""
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq TdxW.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return False
    if proc.returncode != 0:
        return False
    return "TdxW.exe" in proc.stdout


def _strip_suffix(code: str) -> str:
    return str(code).strip().upper().split(".")[0]


def load_sector_names(path: Optional[Path] = None) -> dict:
    """解析 tdxzs*.cfg → {code6: {"name": str, "tdx_type": str}}。

    文件为 GBK 编码、管道分隔：``名称|代码|类型|?|?|尾字段``。
    默认按优先级尝试 tdxzs3.cfg（含 881 细分行业）→ tdxzs.cfg。
    文件缺失或不可解析时返回空 dict（调用方据此标注 names_unavailable）。
    """
    if path:
        candidates = (Path(path),)
    else:
        candidates = TDXZS_CFG_CANDIDATES
    for cfg in candidates:
        try:
            text = cfg.read_bytes().decode("gbk", errors="replace")
        except OSError:
            continue
        names: dict = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            name, code, tdx_type = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if not _CODE_RE.match(code):
                continue
            names[_strip_suffix(code)] = {"name": name, "tdx_type": tdx_type}
        if names:
            return names
    return {}


def classify_sector(code: str, tdx_type: Optional[str] = None) -> dict:
    """板块分类初版（保守）。

    优先使用 tdxzs.cfg 官方类型字段（heuristic=False）；
    缺失时退回号段启发式（heuristic=True，允许后续人工校准）。
    """
    code6 = _strip_suffix(code)
    if tdx_type in _TDX_TYPE_CATEGORY:
        category = _TDX_TYPE_CATEGORY[tdx_type]
        # 880001-880099 为统计指数，官方类型 5 在此号段语义为指数
        if tdx_type == "5" and code6.startswith("8800"):
            category = "stat_index"
        return {"category": category, "heuristic": False, "basis": "tdxzs_cfg_type"}
    # 号段启发式（无官方类型时的兜底）
    if code6.startswith("8800"):
        category = "stat_index"
    elif code6.startswith("8802"):
        category = "region"
    elif code6.startswith(("8803", "8804")):
        category = "industry"
    elif code6.startswith(("8805", "8806", "8807")):
        category = "concept"
    elif code6.startswith("881"):
        category = "sub_industry"
    else:  # 8808xx+ 及其他
        category = "concept_or_style"
    return {"category": category, "heuristic": True, "basis": "code_range"}


def _import_tq():
    """惰性导入 tqcenter（sys.path 注入 PYPlugins 目录）。"""
    plugin_dir = str(TDX_PYPLUGINS)
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    from tqcenter import tq  # noqa: PLC0415

    return tq


class TQSectorSession:
    """TQ 板块会话：惰性 initialize + 显式 close，错误全部结构化返回。"""

    def __init__(self, name_map: Optional[dict] = None) -> None:
        self._tq = None
        self._initialized = False
        self.error: Optional[dict] = None
        # 名称表（允许注入以便测试）；文件缺失时为空 dict → names_unavailable
        self._name_map = load_sector_names() if name_map is None else name_map

    # -- lifecycle ------------------------------------------------------
    def _ensure_initialized(self) -> Optional[dict]:
        """返回 None 表示就绪；否则返回结构化错误。"""
        if self._initialized:
            return None
        if self.error is not None:
            return self.error
        if not is_tdxw_running():
            self.error = _err("tdxw_not_running", "TdxW.exe 未运行，无法建立 TQ 连接")
            return self.error
        try:
            self._tq = _import_tq()
            self._tq.initialize(str(Path(__file__).resolve()))
            self._initialized = True
        except Exception as exc:  # noqa: BLE001 —— 绝不 raise 到调用方
            self.error = _err("initialize_failed", exc)
            return self.error
        return None

    def close(self) -> None:
        if self._initialized and self._tq is not None:
            try:
                self._tq.close()
            except Exception:  # noqa: BLE001
                pass
        self._initialized = False

    def __enter__(self) -> "TQSectorSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- data -----------------------------------------------------------
    def get_sector_list(self) -> Any:
        """返回板块代码列表；失败返回结构化错误 dict。"""
        init_err = self._ensure_initialized()
        if init_err is not None:
            return init_err
        try:
            codes = self._tq.get_sector_list()
        except Exception as exc:  # noqa: BLE001
            return _err("sector_list_failed", exc)
        if not isinstance(codes, (list, tuple)):
            return _err("sector_list_failed", f"unexpected result: {type(codes).__name__}")
        return list(codes)

    def get_sector_stocks(self, sector_code: str) -> Any:
        """返回单板块成分股列表；失败返回结构化错误 dict（标注板块）。"""
        init_err = self._ensure_initialized()
        if init_err is not None:
            return init_err
        try:
            stocks = self._tq.get_stock_list_in_sector(sector_code)
        except Exception as exc:  # noqa: BLE001
            return _err("sector_stocks_failed", exc, sector=sector_code)
        if not isinstance(stocks, (list, tuple)):
            return _err(
                "sector_stocks_failed",
                f"unexpected result: {type(stocks).__name__}",
                sector=sector_code,
            )
        return list(stocks)

    def build_sector_map(
        self,
        limit: Optional[int] = None,
        progress: bool = False,
    ) -> dict:
        """全量板块映射。任何失败都体现在 error/errors 字段，绝不 raise。"""
        started = time.monotonic()
        as_of = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

        init_err = self._ensure_initialized()
        if init_err is not None:
            return {
                "as_of": as_of,
                "source": SOURCE,
                "sector_count": 0,
                "stock_total": 0,
                "sectors": [],
                "errors": [],
                **init_err,
            }

        codes = self.get_sector_list()
        if isinstance(codes, dict):  # 结构化错误
            return {
                "as_of": as_of,
                "source": SOURCE,
                "sector_count": 0,
                "stock_total": 0,
                "sectors": [],
                "errors": [],
                **codes,
            }

        if limit is not None:
            codes = codes[:limit]

        sectors: list = []
        errors: list = []
        stock_total = 0
        named = 0
        for idx, raw_code in enumerate(codes, 1):
            code = str(raw_code).strip().upper()
            stocks = self.get_sector_stocks(code)
            if isinstance(stocks, dict):  # 单板块失败：记录并继续
                errors.append(stocks)
                stocks = []
            info = self._name_map.get(_strip_suffix(code), {})
            name = info.get("name", "")
            if name:
                named += 1
            cls = classify_sector(code, info.get("tdx_type"))
            sectors.append(
                {
                    "code": code,
                    "name": name,
                    "category": cls["category"],
                    "heuristic": cls["heuristic"],
                    "stock_count": len(stocks),
                    "stocks": stocks,
                }
            )
            stock_total += len(stocks)
            if progress and (idx % 50 == 0 or idx == len(codes)):
                print(
                    f"[tq_sector] {idx}/{len(codes)} sectors, "
                    f"{time.monotonic() - started:.1f}s",
                    flush=True,
                )

        quality = {
            "names_unavailable": not self._name_map,
            "named_sectors": named,
            "name_coverage": round(named / len(sectors), 4) if sectors else 0.0,
        }
        return {
            "as_of": as_of,
            "source": SOURCE,
            "sector_count": len(sectors),
            "stock_total": stock_total,
            "quality": quality,
            "sectors": sectors,
            "errors": errors,
            "duration_sec": round(time.monotonic() - started, 2),
        }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="TQ 板块映射采集（独立封装，不影响现有消费者）")
    parser.add_argument("--date", required=True, help="采集日期 YYYY-MM-DD，用于输出文件命名")
    parser.add_argument("--limit", type=int, default=None, help="只采集前 N 个板块（调试用）")
    parser.add_argument("--progress", action="store_true", help="打印采集进度")
    args = parser.parse_args(argv)

    started = time.monotonic()
    with TQSectorSession() as session:
        result = session.build_sector_map(limit=args.limit, progress=args.progress)
    result["date"] = args.date

    out_path = SECTORS_DIR / f"{args.date}_tq_sector_map.json"
    SECTORS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "date": args.date,
        "sector_count": result.get("sector_count", 0),
        "stock_total": result.get("stock_total", 0),
        "errors": len(result.get("errors", [])) + (1 if result.get("error") else 0),
        "name_coverage": result.get("quality", {}).get("name_coverage"),
        "duration_sec": round(time.monotonic() - started, 2),
        "output": str(out_path),
    }
    if result.get("error"):
        summary["error"] = result["error"]
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
