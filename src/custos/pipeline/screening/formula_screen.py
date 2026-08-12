# -*- coding: utf-8 -*-
"""Screening 链第 1 段：公式初筛（formula_screen）。

对全 A 批量执行注册表（governance/contracts/SCREEN_FORMULA_REGISTRY.json）中 enabled
的 TQ 选股公式（formula_process_mul_xg），汇总当日命中清单。

降级规则（绝不 raise、绝不阻塞主链）：
- TdxW 未运行 → status=unavailable，degraded_reason=tdxw_not_running。
- 单公式调用超时 15s；失败计入该公式 error。
- 连续 2 个公式失败 → 熔断，剩余公式标记 circuit_open_skipped。

CLI::

    uv run python src/custos/pipeline/screening/formula_screen.py --date YYYY-MM-DD

输出 ``data/screening/{date}_formula_hits.json``，并打印一行 JSON 摘要。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import DATA, SCREEN_FORMULA_REGISTRY_FILE  # noqa: E402
from custos.core.contracts import require  # noqa: E402
from custos.datasource.local_tdx import tq_http  # noqa: E402
from custos.datasource.local_tdx.tq_sector import is_tdxw_running  # noqa: E402
from custos.datasource.local_tdx import local_tdx_data  # noqa: E402
from custos.datasource.local_tdx import stock_names  # noqa: E402
from custos.pipeline.screening import manual_pools  # noqa: E402

SCREENING_DIR = DATA / "screening"
REGISTRY_PATH = SCREEN_FORMULA_REGISTRY_FILE

FORMULA_TIMEOUT = 15  # 单公式调用超时（秒）
CIRCUIT_BREAK_AFTER = 2  # 连续失败熔断阈值
FORMULA_COUNT = 60  # 每股回溯 K 线根数（供公式内部指标计算，非返回序列长度）
FORMULA_BATCH = 1000  # 每次 formula_process_mul_xg 调用携带的最大股票数
# (全市场 6k+ 只×60根单调用会让 TQ 服务端 OOM;分批逐块合并命中)

# 沪深 A 股代码前缀（mootdx stocks 返回全品类证券，含指数/基金/债券，必须过滤）
_A_SHARE_RE = re.compile(r"^(60[0-5]|688|00[0-3]|30[0-3])\d{3}$")


def load_registry(path: Optional[Path] = None) -> dict:
    """加载公式注册表；文件缺失/损坏时返回空注册表（调用方据此降级）。"""
    p = Path(path) if path else REGISTRY_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": "", "universe": {}, "formulas": []}
    if not isinstance(data, dict) or not isinstance(data.get("formulas"), list):
        return {"version": "", "universe": {}, "formulas": []}
    return data


def _strip_suffix(code: str) -> str:
    return str(code).strip().upper().split(".")[0].zfill(6)


def _is_bj(code: str) -> bool:
    s = str(code).strip().upper()
    if "." in s:
        return s.split(".")[1] == "BJ"
    return _strip_suffix(s).startswith(("4", "8", "920"))


# 名称缓存路径已收敛到 local_tdx/stock_names.CACHE（单一定义）


def _load_name_map(diag: Optional[dict] = None) -> dict[str, str]:
    """universe 阶段的名称表：只读本地缓存并判时效，不在此拉全市场在线表。

    候选名称由 :func:`_refresh_candidate_names` 在拿到命中后用东财 ulist 批量刷新
    （那才是 ST 判定真正依赖的一步）。这里只需要给 universe/自选池填个初始名字，
    所以走缓存即可——全市场在线表拉不动（东财 clist 受限流）、mootdx 已失效
    （2026-07 起），硬拉只会每次都白等一轮超时。

    缓存陈旧时 st_filter 报 "stale" 而不是 "ok"：旧缓存里新被 ST 的票名字仍是正常的，
    报 ok 等于声称 ST 过滤有效（审计 B5 的延伸）。
    """
    names, meta = stock_names.load_cache()
    if diag is not None:
        if not names:
            diag["name_map_source"] = "unavailable"
            diag["name_map_size"] = 0
            diag["st_filter"] = "unavailable"
        else:
            diag["name_map_source"] = meta.get("source") or "cache"
            diag["name_map_size"] = len(names)
            diag["name_map_generated_at"] = meta.get("generated_at")
            diag["name_map_age_days"] = meta.get("age_days")
            diag["st_filter"] = "stale" if meta.get("stale") else "ok"
    if not names:
        print(
            "[WARN] 名称缓存不可用：universe 阶段无名，ST 判定改由候选名称刷新兜底",
            file=sys.stderr,
        )
    return names


def build_universe(
    universe_cfg: Optional[dict] = None, diag: Optional[dict] = None
) -> tuple[list[str], dict[str, str]]:
    """全 A 股票列表（6 位代码）+ 名称表。exclude_bj 在此过滤；ST/上市天数
    在 enrich 段按名称与本地日线过滤。失败时返回空列表（调用方降级）。

    **本地 vipdoc 优先**:直接枚举磁盘上实有的日线文件(list_local_vipdoc_codes),
    仅在本地为空时才回退 mootdx 在线全代码表。2026-07-30 事故即因只走在线:
    mootdx online 报 `'>' not supported between 'NoneType' and 'int'` → universe 空 →
    formula_screen 提前 return → **全市场公式初筛整段跳过**,当日只剩自选池命中,
    报告看起来"只有 D 池",实则从未扫过市场。
    """
    cfg = universe_cfg or {}
    source = "vipdoc"
    try:
        raw = local_tdx_data.list_local_vipdoc_codes(ashare_only=True)
    except Exception:  # noqa: BLE001 —— 绝不 raise
        raw = []
    if not raw:
        source = "online"
        try:
            raw = local_tdx_data.get_stock_list()
        except Exception:  # noqa: BLE001
            raw = []
    codes: list[str] = []
    seen: set[str] = set()
    for c in raw or []:
        if cfg.get("exclude_bj", True) and _is_bj(c):
            continue
        code6 = _strip_suffix(c)
        if _A_SHARE_RE.match(code6) and code6 not in seen:
            seen.add(code6)
            codes.append(code6)
    name_map = _load_name_map(diag)
    if diag is not None:
        diag["universe_source"] = source if codes else "unavailable"
        diag["universe_size"] = len(codes)
    return codes, name_map


def _extract_hits(value: Any, date: str, name_map: dict[str, str]) -> list[dict]:
    """从 formula_process_mul_xg 返回值提取当日命中。

    返回形态：{code_with_suffix: {序列名: ['0'/'1', ...]}}，序列最后一个
    元素为最新交易日（盘后跑即为当日）。序列为空或末位非 '1' 则不命中。
    """
    hits: list[dict] = []
    if not isinstance(value, dict):
        return hits
    for raw_code, series in value.items():
        if raw_code == "ErrorId" or not isinstance(series, dict):
            continue
        hit = False
        for seq in series.values():
            if isinstance(seq, (list, tuple)) and seq and str(seq[-1]) == "1":
                hit = True
                break
        if not hit:
            continue
        code6 = _strip_suffix(raw_code)
        hits.append(
            {
                "code": code6,
                "name": name_map.get(code6, ""),
                "signal_date": date,
            }
        )
    hits.sort(key=lambda x: x["code"])
    return hits


def _load_manual_pools(
    registry: dict, date: str, name_map: dict[str, str]
) -> tuple[list[dict], int, list[str]]:
    """自选池（manual_pools）→ 与公式条目同构的伪公式列表。本地文件，不依赖 TQ。

    第三个返回值是失败池的诊断串。自选池是公式之外的**独立候选通道**：blk 文件被改名、
    TDX_ROOT 指错、权限丢失时这条通道整条归零，而状态判定此前把 manual_pool 显式排除
    （`category != "manual_pool"`）→ status 仍是 ok（审计 B6），报告读成"自选池今天没有
    符合条件的票"，与"根本没读到池"无法区分。
    """
    entries: list[dict] = []
    hit_total = 0
    errors: list[str] = []
    for p in registry.get("manual_pools", []):
        entry: dict[str, Any] = {
            "id": p.get("id", ""),
            "tq_name": f"自选池:{p.get('block_name', '')}",
            "enabled": bool(p.get("enabled", True)),
            "category": "manual_pool",
            "hits": [],
            "error": None,
        }
        entries.append(entry)
        if not entry["enabled"]:
            continue
        pool = manual_pools.load_pool(p.get("block_name", ""), date, name_map=name_map)
        entry["hits"] = pool["hits"]
        if pool.get("excluded"):
            # 非 A 股标的（ETF/可转债/B股）不进候选，但要留痕，
            # 否则"池里 20 只只出来 3 只"无从解释
            entry["excluded_non_a_share"] = pool["excluded"]
        if pool.get("error"):
            entry["error"] = pool["error"]
            errors.append(f"{entry['id'] or p.get('block_name', '')}:{pool['error']}")
        hit_total += len(pool["hits"])
    return entries, hit_total, errors


def screen_formulas(
    date: str,
    registry: Optional[dict] = None,
    stock_list: Optional[list[str]] = None,
    name_map: Optional[dict[str, str]] = None,
    name_resolver=None,
    call: Optional[Callable[..., dict]] = None,
    running_check: Optional[Callable[[], bool]] = None,
    timeout: int = FORMULA_TIMEOUT,
) -> dict:
    """逐公式对全 A 批跑并汇总命中。所有失败都结构化落盘，绝不 raise。"""
    registry = registry if registry is not None else load_registry()
    call_fn = call if call is not None else tq_http.call
    is_running = running_check if running_check is not None else is_tdxw_running

    result: dict[str, Any] = {
        "date": date,
        "status": "ok",
        "degraded_reason": "",
        "universe_size": 0,
        "formulas": [],
    }

    # 先建 universe/名称表（本地 vipdoc，不依赖 TQ），再加载自选池（本地文件），
    # 最后才做 TQ 门控：TdxW 关闭时池内候选仍可进入充实段，且池内股票有名。
    diag: dict[str, Any] = {}
    if stock_list is None:
        stock_list, name_map = build_universe(registry.get("universe"), diag=diag)
    name_map = name_map or {}
    result["universe_size"] = len(stock_list)
    result["universe_source"] = diag.get("universe_source", "injected")
    result["name_map_source"] = diag.get("name_map_source", "injected")
    result["st_filter"] = diag.get("st_filter", "ok" if name_map else "unavailable")

    # 降级备注在**所有** return 路径上统一追加：早退分支（TdxW 关闭 / universe 空）用 `=`
    # 覆盖 degraded_reason，若在分支之前直接写就会被冲掉。
    pending_notes: list[str] = []

    def _finalize(res: dict) -> dict:
        for note in pending_notes:
            if res["status"] == "ok":
                res["status"] = "partial"
            res["degraded_reason"] = (
                f"{res['degraded_reason']};{note}" if res["degraded_reason"] else note
            )
        return res

    if result["st_filter"] != "ok":
        # 名称表是 ST 硬排除的唯一依据；它挂了这批命中就**不能声称筛选正常**（审计 B5）。
        # 下游 enrich 读 st_filter 走 fail-closed（无名候选按 st_unverified 剔除）。
        pending_notes.append(
            "st_filter_unavailable(名称表在线+缓存均不可用 → ST 硬排除失效,"
            "下游按 st_unverified 剔除无名候选)"
        )

    pool_entries, pool_hits, pool_errors = _load_manual_pools(registry, date, name_map)
    result["formulas"].extend(pool_entries)
    enabled_pools = [e for e in pool_entries if e.get("enabled")]
    if not enabled_pools:
        result["manual_pool_status"] = "none"
    elif not pool_errors:
        result["manual_pool_status"] = "ok"
    else:
        result["manual_pool_status"] = (
            "unavailable" if len(pool_errors) == len(enabled_pools) else "partial"
        )
        # 自选池读不到 ≠ 池里今天没票（审计 B6）
        pending_notes.append(
            f"manual_pool_{result['manual_pool_status']}:{','.join(pool_errors)}"
            "(自选池通道读取失败,候选缺口不代表池内无票)"
        )

    if not is_running():
        result["status"] = "partial" if pool_hits else "unavailable"
        result["degraded_reason"] = "tdxw_not_running"
        for f in registry.get("formulas", []):
            result["formulas"].append(
                {
                    "id": f.get("id", ""),
                    "tq_name": f.get("tq_name", ""),
                    "enabled": bool(f.get("enabled")),
                    "hits": [],
                    "error": "tdxw_not_running" if f.get("enabled") else None,
                }
            )
        return _finalize(result)

    if not stock_list:
        result["status"] = "partial" if pool_hits else "unavailable"
        result["degraded_reason"] = (
            "universe_unavailable(本地 vipdoc 与在线全代码表均为空 → "
            "**全市场公式初筛整段跳过**,当日命中仅来自自选池,不代表市场无标的)"
        )
        print(
            "[WARN] universe 为空:本次未扫全市场,只有自选池命中。检查 TDX_ROOT/vipdoc 是否可读",
            file=sys.stderr,
        )
        return _finalize(result)

    tq_codes = [local_tdx_data.normalize_code(c) for c in stock_list]

    consecutive_failures = 0
    attempted = succeeded = 0
    for f in registry.get("formulas", []):
        entry: dict[str, Any] = {
            "id": f.get("id", ""),
            "tq_name": f.get("tq_name", ""),
            "enabled": bool(f.get("enabled")),
            "hits": [],
            "error": None,
        }
        result["formulas"].append(entry)
        if not f.get("enabled"):
            continue
        if consecutive_failures >= CIRCUIT_BREAK_AFTER:
            entry["error"] = "circuit_open_skipped"
            continue
        attempted += 1
        # TQ formula_process_mul_xg 参数语义（2026-07-20 接口摸底实测：UPN arg=3 / 1d / count=60）：
        #   count        —— 每股回溯 K 线根数，供公式内部指标计算，非返回长度；
        #   return_count —— 每股返回的最新结果个数（取 1＝仅最新交易日那一列）；
        #   return_date=False —— 结果不带日期轴，故命中日期由调用时点（盘后即当日）决定，
        #                        下游 enrich 再用本地日线 last_date==date 二次校验一致性。
        # ⚠️ stock_list 必须分批:全市场 6k+ 只×count 根单调用会让 TQ 服务端 OOM(2026-07-31 实测)。
        hits_all: list = []
        chunk_fail = 0
        last_err: dict = {}
        n_chunks = 0
        for k in range(0, len(tq_codes), FORMULA_BATCH):
            n_chunks += 1
            params = {
                "formula_name": f.get("tq_name", ""),
                "formula_arg": str(f.get("args", "") or ""),
                "return_count": 1,
                "return_date": False,
                "stock_list": tq_codes[k : k + FORMULA_BATCH],
                "stock_period": f.get("stock_period", "1d") or "1d",
                "count": FORMULA_COUNT,
                "dividend_type": 1,
            }
            resp = call_fn("formula_process_mul_xg", params, timeout=timeout)
            if resp.get("ok"):
                hits_all.extend(_extract_hits(resp.get("value"), date, name_map))
            else:
                chunk_fail += 1
                last_err = resp.get("error") or {}
        if chunk_fail == 0:
            succeeded += 1
            consecutive_failures = 0
            entry["hits"] = hits_all
        elif chunk_fail < n_chunks:  # 部分批次成功:命中保留,错误显式记录
            succeeded += 1
            consecutive_failures = 0
            entry["hits"] = hits_all
            entry["error"] = "partial_chunks_failed"
            entry["error_detail"] = (
                f"{chunk_fail}/{n_chunks} 批失败: {last_err.get('code', 'unknown')}"
            )
        else:
            consecutive_failures += 1
            entry["error"] = last_err.get("code", "unknown")
            if last_err.get("detail"):
                entry["error_detail"] = str(last_err["detail"])[:200]

    if attempted == 0:
        result["status"] = "partial" if pool_hits else "unavailable"
        result["degraded_reason"] = "no_enabled_formula" + (
            ";manual_pool_only" if pool_hits else ""
        )
    elif succeeded == 0:
        result["status"] = "partial" if pool_hits else "unavailable"
        result["degraded_reason"] = "all_formulas_failed" + (
            ";manual_pool_only" if pool_hits else ""
        )
    elif succeeded < attempted or any(
        e.get("error")
        for e in result["formulas"]
        if e.get("enabled") and e.get("category") != "manual_pool"
    ):
        result["status"] = "partial"
        result["degraded_reason"] = "some_formulas_failed"
    # 注：上面这个 any() 之所以排除 manual_pool，是因为自选池失败有自己的 manual_pool_status
    # 与专门的降级备注（见 _finalize/pending_notes），不与"公式失败"混同；但它**必须**同样
    # 让 status 脱离 ok —— 原实现只做了排除、没做替代，于是自选池整条通道归零仍报 ok（审计 B6）。

    # 候选名称按需刷新（东财 ulist 批量 → TQ → 缓存）。
    # 为什么放在这里而不是 universe 阶段：ST 判定只需要知道**候选股**是不是 ST，候选池
    # 通常几十到几百只，一次 200 只即可，几乎不触发限流；而全市场表拉不动（东财 clist
    # 单页≤100 且连续约 10 页即断连）且只能靠手动跑脚本、永不更新（审计 B5 的延伸）。
    # 这里拿到的名称会**覆盖** universe 阶段来自缓存的旧名字——新被 ST 的票必须能被发现。
    _refresh_candidate_names(result, pending_notes, name_resolver)
    return _finalize(result)


def _candidate_codes(result: dict) -> list[str]:
    """result 里全部候选代码（公式命中 + 自选池命中）。"""
    codes: list[str] = []
    for f in result.get("formulas") or []:
        for h in f.get("hits") or []:
            c = str(h.get("code") or "").split(".")[0]
            if c:
                codes.append(c)
    return sorted(set(codes))


def _refresh_candidate_names(
    result: dict, pending_notes: list[str], resolver=None
) -> None:
    """用最新名称覆盖候选的 name，并据覆盖率重判 st_filter。就地修改 result。

    ``resolver(codes) -> (names, diag)`` 可注入：默认走
    :func:`stock_names.resolve_names_for`（会发网络请求），单测必须注入替身——
    否则测试会真的去打东财接口（慢、不稳定、还会触发限流影响后续用例）。
    """
    codes = _candidate_codes(result)
    if not codes:
        return
    fn = resolver or stock_names.resolve_names_for
    try:
        names, diag = fn(codes)
    except Exception as exc:  # noqa: BLE001 —— 名称刷新失败不得中断初筛
        print(f"[WARN] 候选名称刷新失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    for f in result.get("formulas") or []:
        for h in f.get("hits") or []:
            c = str(h.get("code") or "").split(".")[0]
            if c in names:
                h["name"] = names[c]
    result["name_map_source"] = diag.get(
        "name_map_source", result.get("name_map_source")
    )
    result["candidate_name_coverage"] = {
        "requested": diag.get("requested"),
        "resolved": diag.get("name_map_size"),
        "missing_count": diag.get("missing_count"),
        "missing_codes": diag.get("missing_codes"),
        "generated_at": diag.get("name_map_generated_at"),
        "age_days": diag.get("name_map_age_days"),
    }
    prev, now = result.get("st_filter"), diag.get("st_filter", "unavailable")
    # 只在变差时改写：universe 阶段若已判 unavailable，这里 ok 也不该把它洗白到 ok，
    # 因为 universe 的名称缺失影响的是"哪些票进了初筛"，与候选名称是两件事。
    rank = {"ok": 0, "stale": 1, "partial": 2, "unavailable": 3}
    result["st_filter"] = now if rank.get(now, 3) > rank.get(prev or "", 0) else prev
    if now == "partial":
        pending_notes.append(
            f"st_filter_partial({diag.get('missing_count')}/{diag.get('requested')} "
            f"只候选取不到名称,其 ST 状态未知)"
        )
    elif now == "stale":
        pending_notes.append(
            f"st_filter_stale(候选名称全部来自陈旧缓存,age={diag.get('name_map_age_days')}天 "
            f"> {stock_names.NAME_MAP_MAX_AGE_DAYS}天,新被 ST 的票可能不在表内)"
        )
    elif now == "unavailable":
        pending_notes.append(
            "st_filter_unavailable(候选名称全部取不到 → ST 硬排除失效)"
        )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="screening 链第 1 段：TQ 公式初筛（干净降级，不阻塞主链）"
    )
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    result = screen_formulas(args.date)

    SCREENING_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SCREENING_DIR / f"{args.date}_formula_hits.json"
    require("formula_hits", result)
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "date": args.date,
        "status": result["status"],
        "degraded_reason": result["degraded_reason"],
        "universe_size": result["universe_size"],
        "universe_source": result.get("universe_source", ""),
        "st_filter": result.get("st_filter", ""),
        "manual_pool_status": result.get("manual_pool_status", ""),
        "hit_total": sum(len(f.get("hits", [])) for f in result["formulas"]),
        "output": str(out_path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
