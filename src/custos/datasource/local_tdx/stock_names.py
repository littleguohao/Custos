# -*- coding: utf-8 -*-
"""股票名称表：多源获取 + 带时效元数据的缓存。

名称是**硬排除 ST 的唯一依据**（enrich_candidates 用 `"ST" in name.upper()`），
仓库里没有别的 ST 判据可用（tq_sector 的板块分类不含 ST/风险警示分类）。所以这张表
的可用性与新鲜度直接决定 ST 股会不会进候选池。

数据源优先级（2026-08-04 改定，owner 原则「尽量用本地 TDX 接口，HTTP 不稳定」）::

    ① TDX 协议 client.stocks —— **主路径**。实测 50725 条（沪深）/ **1.6 秒**，
       含 name 列，ST 识别正确（221 只）。一次拿全市场再本地筛，比分批 HTTP 又快又稳。
       ⚠️ 不含北交所：TDX 服务器只给沪深（stocks() 硬校验 market in [0,1]；绕过校验
       直接调 client.get_security_list(market=2) 实测也是空返回）。
    ② 东财 push2 ulist.np —— **只补 TDX 拿不到的票**（北交所 92/83/87/43 段）
    ③ TQ-Local get_stock_info —— 需 TdxW.exe 运行，逐只取
    ④ 本地缓存 —— 兜底，读取时判时效

为什么把 ① 从"已失效、仅最后尝试"提回主路径：它此前被判定
「2026-07 起持续失败（'>' NoneType）」，真实原因是 `local_tdx_data._get_client()`
**永不重连** —— 连接一断，`stock_count()` 返回 None，mootdx 内部 `if counts > 0`
就抛 `'>' NoneType`。接口本身完好。给客户端加上时效与自动重建后，本地协议全面可用，
而当时的应对（引入东财 HTTP 当主路径）反而把系统绑到了更不稳的源上。

为什么不用东财 clist 拉全市场：实测它单页最多 100 条，且连续翻页约 10 页（1000 条）
后即 RemoteDisconnected，拉不完 5888 只。全市场缓存构建仍保留 clist 路径但只是
尽力而为（:func:`fetch_all_from_clist`），真正的全量构建应走 ① TDX 协议
（``--source auto`` 的第一顺位，与主路径说法一致）。
**凡是要落盘当全量缓存的路径**（:func:`fetch_name_map` / ``--source clist``）都强制
``CLIST_MIN_COVERAGE`` 覆盖率门槛：残缺表落盘会覆盖完整缓存并把 generated_at 刷新成
当天（30 天时效计时被重置），比"没更新"更危险，宁可回退旧缓存。

为什么原实现危险：它只有 ④，而 ④ 已经死了，于是系统长期靠一份手动跑
build_name_cache.py 生成、**永不更新、且读取时不校验时效**的缓存在跑。这比"名称表
不可用"更隐蔽——缓存非空时 st_filter 报 "ok"，系统认为 ST 过滤正常工作，而一只新被
ST 的股票在旧缓存里名字还是正常的，照样通过硬排除（审计 B5 的延伸）。

缓存格式（新）::

    {"generated_at": "2026-08-03", "source": "eastmoney", "count": 5888,
     "names": {"000001": "平安银行", ...}}

旧格式是扁平的 ``{code: name}``。:func:`load_cache` 两种都认，但旧格式没有
``generated_at`` ⇒ 时效未知 ⇒ 按 stale 处理（不假定新鲜）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

_TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(_TOOLS), str(_TOOLS.parent / "core"), str(_TOOLS / "local_tdx")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Windows 生产链（run_1800 起子进程）默认 GBK 控制台：本模块的 WARN 里有 "⇒" 等
# 字符，不 reconfigure 会在**拒绝落盘那条最关键的路径**上抛 UnicodeEncodeError。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from custos.core.code_utils import market_of  # noqa: E402
from custos.core.paths import DATA, cn_today  # noqa: E402

CACHE = DATA / "market" / "stock_name_map.json"

# 名称表时效上限（日）。ST 变更集中在年报后（4-5 月）但随时可能发生；30 天保证一个月内
# 必然重建一次。超过即标 stale 并传导给调用方，而不是继续当 "ok" 用。
NAME_MAP_MAX_AGE_DAYS = 30

# 东财 push2 接口。
# ulist.np：按 secids 批量查（主路径）。secid 前缀 1.=沪市 0.=深市/北交所（北交所实测
#   0.920819 正常返回）。
# clist：全市场分页列表（受限流，单页≤100 且连续约 10 页即断连，仅作尽力而为的兜底）。
#
# **多域名轮询**：东财有多个 push2 域名，实测同一时刻可用性不同——本机 push2 /
# 82.push2 / push2his 全部 RemoteDisconnected，而 push2delay 稳定 HTTP 200。名称查询
# 不需要实时行情，延时域名完全够用，所以把它排在最前。单域名故障不该让整个数据源失效。
EM_HOSTS = ("push2delay.eastmoney.com", "push2.eastmoney.com", "82.push2.eastmoney.com")
EM_ULIST_PATH = "/api/qt/ulist.np/get"
EM_CLIST_PATH = "/api/qt/clist/get"
EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
EM_BATCH = 200                  # ulist 单次代码数（实测 200 正常）
EM_CLIST_PAGE_SIZE = 100        # clist 硬上限
EM_MAX_PAGES = 60
EM_PAGE_SLEEP = 0.6             # 翻页/分批限速
EM_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
              "Referer": "https://quote.eastmoney.com/"}

# clist 当全量表用的最低覆盖率（取到条数 / 接口报告的 total）。低于此值宁可当失败
# 回退旧缓存：残缺表 save_cache 后会覆盖完整缓存且 generated_at 刷新为当天。
CLIST_MIN_COVERAGE = 0.8

# ⚠️ **落盘覆盖门槛**：新表小于既有缓存的这个比例时，拒绝覆盖。
# 2026-08-07 实测的事故形态：TQ 只成功取到 3 只（TdxW 忙/连接抖动），
# 那 3 只照样落盘覆盖了 5000 只的完整缓存、`generated_at` 刷新成当天
# （30 天时效计时重置），而 `st_filter` 报 **ok** ⇒ ST 硬排除静默失效。
#
# 原先门槛只加在 `fetch_all_from_clist` 上，可它在源顺序里**排最后**；
# 排在前面的 `fetch_from_tdx_protocol`（沪深两市，一市失败就剩一半）与
# `fetch_from_tq`（逐只 RPC，部分失败照样返回）都没有门槛。
# ⇒ 门槛该在**落盘边界**，不是在每个源里各写一份。落盘边界有两条——
# `resolve_name_map`（链内调用）与 `main()`（生产每日刷新走这条）——
# 两条都在 `save_cache` 之前过 `_refuses_overwrite`，缺一条等于没挂。
NAME_MAP_MIN_OVERWRITE_RATIO = 0.8

# 本次运行内记住可用域名，避免每批都从头重试已知不通的域名
_working_host: Optional[str] = None


class NameFetchIncomplete(RuntimeError):
    """分页/分批未取全。**不得**把残缺样本当完整名称表落盘。

    残缺表比没有表更危险：缺失的那些票 name 为空 → `"ST" in ""` 为假 → 静默通过
    ST 硬排除，而 st_filter 仍报 ok（与审计 C4 的东财分页残缺同类）。
    """


def _new_session():
    import requests
    s = requests.Session()
    s.trust_env = False
    s.headers.update(EM_HEADERS)
    return s


def _secid(code6: str) -> str:
    """code6 → 东财 secid（沪市 ``1.``，深市/北交所 ``0.``）。

    市场归属复用 :func:`code_utils.market_of` 这一份定义，**不要**在这里手写前缀判断：
    "9 开头即沪市"会把北交所 920xxx 误判成沪 B（920819 颖泰生物 → 1.920819 → 东财查不到，
    表现为该票取不到名称、ST 状态未知）。这与审计 B11（881xxx 细分行业指数被判成北交所）
    是同一类错误——代码段规则散落多处必然漂移。
    """
    c = str(code6).strip().split(".")[0].zfill(6)
    return f"1.{c}" if market_of(c) == "SH" else f"0.{c}"


def reset_host_cache() -> None:
    """清空已记住的可用域名（供测试与长驻进程用）。"""
    global _working_host
    _working_host = None


def _em_get(session, path: str, params, tries: int = 2):
    """带域名轮询 + 退避重试的 GET。

    先试上次成功的域名，再按 EM_HOSTS 顺序试其余。每个域名内部重试 ``tries`` 次
    （东财会在连续请求后 RemoteDisconnected，需要退避而非立刻放弃）。
    """
    global _working_host
    hosts = list(EM_HOSTS)
    if _working_host and _working_host in hosts:
        hosts.remove(_working_host)
        hosts.insert(0, _working_host)
    errors: list[str] = []
    for host in hosts:
        for k in range(tries):
            try:
                r = session.get(f"https://{host}{path}", params=params, timeout=25,
                                proxies={"http": None, "https": None})
                r.raise_for_status()
                _working_host = host
                return (r.json() or {}).get("data")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{host}: {type(exc).__name__}")
                if k < tries - 1:
                    time.sleep(1.2 * (k + 1))
    raise NameFetchIncomplete(f"东财全部域名不可用（{'; '.join(errors[:6])}）")


def fetch_names_for(codes, session=None, batch: int = EM_BATCH) -> dict[str, str]:
    """东财 ulist 批量查指定代码的名称 → {code6: name}。**主路径。**

    只查传入的代码，请求数 = ceil(len(codes)/200)。返回里缺失的代码说明东财没有该
    标的（已退市/非 A 股），调用方据此判断覆盖率——**不要**把缺失当作"名称为空"。
    """
    wanted = sorted({str(c).split(".")[0].zfill(6) for c in codes
                     if str(c).split(".")[0].strip()})
    if not wanted:
        return {}
    s = session or _new_session()
    out: dict[str, str] = {}
    for i in range(0, len(wanted), batch):
        chunk = wanted[i:i + batch]
        data = _em_get(s, EM_ULIST_PATH,
                       {"secids": ",".join(_secid(c) for c in chunk),
                        "fields": "f12,f14", "fltt": 2, "invt": 2})
        if not isinstance(data, dict):
            raise NameFetchIncomplete(
                f"第 {i // batch + 1} 批无 data 段（限流/异常），已取 {len(out)}/{len(wanted)}")
        for x in (data.get("diff") or []):
            code = str(x.get("f12") or "").strip()
            name = str(x.get("f14") or "").strip()
            if len(code) == 6 and code.isdigit() and name:
                out[code] = name
        if i + batch < len(wanted):
            time.sleep(EM_PAGE_SLEEP)
    return out


def fetch_all_from_clist(session=None, max_pages: int = EM_MAX_PAGES,
                         min_coverage: float = 0.0) -> dict[str, str]:
    """东财 clist 全市场分页（**尽力而为**）。

    实测限制：单页最多 100 条，且连续翻页约 10 页后 RemoteDisconnected。所以它
    通常拉不完全市场，默认取到多少算多少并由调用方判断是否够用；一条都没取到才抛。
    真正的全量构建请走 TDX 协议（:func:`fetch_from_tdx_protocol`，``--source auto``
    的第一顺位），TQ-Local 只是其后的兜底。

    ``min_coverage`` > 0 时启用覆盖率门槛：接口报告了 total 且
    ``len(out) / total < min_coverage`` 即抛 :class:`NameFetchIncomplete`——
    要把结果**落盘当全量缓存**的调用方必须设它（残缺表落盘比不更新更危险）。
    """
    s = session or _new_session()
    out: dict[str, str] = {}
    total = 0
    for page in range(1, max_pages + 1):
        try:
            data = _em_get(s, EM_CLIST_PATH,
                           {"pn": page, "pz": EM_CLIST_PAGE_SIZE, "po": 0, "np": 1,
                            "fltt": 2, "invt": 2, "fid": "f12", "fs": EM_FS,
                            "fields": "f12,f14"})
        except NameFetchIncomplete:
            break                       # 被限流断连:保留已取部分
        if not isinstance(data, dict):
            break
        total = int(data.get("total") or total)
        rows = data.get("diff") or []
        if not rows:
            break
        for x in rows:
            code = str(x.get("f12") or "").strip()
            name = str(x.get("f14") or "").strip()
            if len(code) == 6 and code.isdigit() and name:
                out[code] = name
        if total and len(out) >= total:
            break
        time.sleep(EM_PAGE_SLEEP)
    if not out:
        raise NameFetchIncomplete("clist 一条未取到")
    if total and len(out) < total * min_coverage:
        raise NameFetchIncomplete(
            f"clist 覆盖率不足: {len(out)}/{total} (<{min_coverage:.0%})，"
            "拒绝当全量表落盘（全量构建请用 --source auto，TDX 协议为第一顺位）")
    if total and len(out) < total:
        print(f"[WARN] clist 受限流只取到 {len(out)}/{total} 条"
              f"（全量构建请用 --source auto，TDX 协议为第一顺位）",
              file=sys.stderr)
    return out


def fetch_from_tq(codes=None, progress_every: int = 500) -> dict[str, str]:
    """TQ-Local 逐只取名称（需 TdxW.exe 运行）。拿不到返回 {}，绝不 raise。"""
    try:
        from custos.datasource.local_tdx import local_tdx_data
        from custos.datasource.local_tdx import tq_sector
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] TQ 名称源导入失败: {exc}", file=sys.stderr)
        return {}
    if not tq_sector.is_tdxw_running():
        print("[WARN] TdxW.exe 未运行，跳过 TQ 名称源", file=sys.stderr)
        return {}
    try:
        codes = list(codes) if codes is not None else local_tdx_data.list_local_vipdoc_codes()
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 无法枚举本地 universe: {exc}", file=sys.stderr)
        return {}
    if not codes:
        return {}
    out: dict[str, str] = {}
    failed = 0
    t0 = time.monotonic()
    tq = tq_sector._import_tq()
    tq.initialize(str(Path(__file__).resolve()))
    try:
        for i, c in enumerate(codes, 1):
            try:
                info = tq.get_stock_info(local_tdx_data.normalize_code(c)) or {}
                name = str(info.get("Name") or "").strip()
                if name:
                    out[c] = name
                else:
                    failed += 1
            except Exception:  # noqa: BLE001
                failed += 1
            if progress_every and i % progress_every == 0:
                rate = i / max(time.monotonic() - t0, 0.1)
                print(f"[INFO] TQ {i}/{len(codes)} 命中 {len(out)} 失败 {failed} "
                      f"({rate:.0f}只/s)", file=sys.stderr, flush=True)
    finally:
        try:
            tq.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def fetch_from_tdx_protocol() -> dict[str, str]:
    """TDX 协议全市场名称表（沪深）。**主路径**——本地协议优于 HTTP。

    实测 2026-08-04：50725 条 / **1.6 秒**，ST 识别正确（221 只）。
    比东财分批 HTTP 更快也更稳（东财 clist 单页 100 条、翻约 10 页即
    RemoteDisconnected；ulist 批量查要分多次请求且受限流）。

    ⚠️ 不含北交所（TDX 服务器只给沪深），BJ 候选须由东财补。
    """
    try:
        from custos.datasource.local_tdx import local_tdx_data
        return local_tdx_data.get_stock_name_map() or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] TDX 协议名称源失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return {}


def fetch_from_mootdx() -> dict[str, str]:
    """旧名保留（曾被判定失效，实为连接不重连所致）。等价于 fetch_from_tdx_protocol。"""
    return fetch_from_tdx_protocol()


def fetch_name_map(session=None) -> tuple[dict[str, str], str]:
    """取**全市场**名称表，返回 (name_map, source)。用于构建缓存，不是主路径。

    顺序（2026-08-04 改为 TDX 优先）：TDX 协议（沪深 50725 条 / 1.6s）→ TQ-Local
    （需 TdxW）→ 东财 clist（受限流，尽力而为）。全部失败返回 ({}, "unavailable")。
    绝不 raise。
    """
    m = fetch_from_tdx_protocol()
    if m:
        return m, "tdx_protocol"
    m = fetch_from_tq()
    if m:
        return m, "tq_local"
    try:
        # 结果会被调用方落盘当全量缓存 → 强制覆盖率门槛，残缺表宁可回退旧缓存
        m = fetch_all_from_clist(session=session, min_coverage=CLIST_MIN_COVERAGE)
        if m:
            return m, "eastmoney_clist"
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 东财 clist 名称源失败: {type(exc).__name__}: {exc}", file=sys.stderr)
    return {}, "unavailable"


def resolve_names_for(codes, session=None) -> tuple[dict[str, str], dict[str, Any]]:
    """**按需**取候选股名称并如实报告 ST 判定可信度。这是选股链该用的入口。

    顺序（2026-08-04 改，owner 原则「尽量用本地 TDX 接口，HTTP 不稳定」）::

        ① TDX 协议全市场表  50725 条 / 1.6s，沪深全覆盖，ST 识别正确
        ② 东财 ulist 批量    **仅用于 TDX 拿不到的票**（北交所 92/83/87/43 段）
        ③ TQ-Local          需 TdxW.exe 运行
        ④ 本地缓存           兜底，读取时判时效

    改动理由：原顺序把东财 ulist 当主路径，是因为当时认为 mootdx 名称源已失效。
    实际是 `_get_client()` 永不重连所致（连接一断，`stock_count()` 返回 None，
    mootdx 内部 `None > 0` 抛 `'>' NoneType`），接口本身好得很。修好重连后
    TDX 一次全量只要 1.6s，比东财分批 HTTP 又快又稳。

    返回 (names, diag)，``diag["st_filter"]``：

      ok           —— 全部候选都拿到了名称，ST 硬排除可信
      partial      —— 部分候选没拿到（missing_codes 列出），那些票的 ST 状态未知
      stale        —— 全靠缓存且缓存已过期，新被 ST 的票可能不在表内
      unavailable  —— 一个都没拿到，ST 硬排除完全失效

    调用方**必须**消费 st_filter：ok 之外都意味着不能声称已排除 ST。
    """
    wanted = sorted({str(c).split(".")[0].zfill(6) for c in codes
                     if str(c).split(".")[0].strip()})
    diag: dict[str, Any] = {"requested": len(wanted)}
    if not wanted:
        diag.update(st_filter="ok", name_map_source="empty_request", name_map_size=0)
        return {}, diag

    names: dict[str, str] = {}
    source = "unavailable"

    # ① TDX 协议（主路径）：一次拿全市场再本地筛，沪深全覆盖
    try:
        full = fetch_from_tdx_protocol()
        hit = {c: full[c] for c in wanted if c in full}
        if hit:
            names.update(hit)
            source = "tdx_protocol"
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] TDX 协议名称源失败（回退 HTTP）: {type(exc).__name__}: {exc}",
              file=sys.stderr)

    # ② 东财 ulist：只补 TDX 拿不到的（主要是北交所——TDX 服务器不提供 BJ）
    missing = [c for c in wanted if c not in names]
    if missing:
        try:
            em = fetch_names_for(missing, session=session)
            if em:
                names.update(em)
                source = f"{source}+eastmoney_ulist" if source != "unavailable" \
                    else "eastmoney_ulist"
                missing = [c for c in wanted if c not in names]
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 东财批量名称失败（回退 TQ-Local）: {type(exc).__name__}: {exc}",
                  file=sys.stderr)

    if missing:
        tq = fetch_from_tq(missing, progress_every=0)
        if tq:
            names.update(tq)
            source = f"{source}+tq_local" if source != "unavailable" else "tq_local"
            missing = [c for c in wanted if c not in names]

    cache_meta: dict[str, Any] = {}
    if missing:
        cached, cache_meta = load_cache()
        filled = {c: cached[c] for c in missing if c in cached}
        if filled:
            names.update(filled)
            source = f"{source}+cache" if source != "unavailable" else "cache"
            missing = [c for c in wanted if c not in names]

    diag.update(name_map_source=source, name_map_size=len(names),
                missing_count=len(missing), missing_codes=missing[:20])
    if not names:
        diag["st_filter"] = "unavailable"
        print(f"[WARN] 候选名称全部取不到（{len(wanted)} 只）：ST 硬排除失效",
              file=sys.stderr)
    elif missing:
        diag["st_filter"] = "partial"
        print(f"[WARN] {len(missing)}/{len(wanted)} 只候选取不到名称："
              f"这些票的 ST 状态未知（{'、'.join(missing[:8])}）", file=sys.stderr)
    elif source == "cache" and cache_meta.get("stale"):
        diag["st_filter"] = "stale"
        diag["name_map_generated_at"] = cache_meta.get("generated_at")
        diag["name_map_age_days"] = cache_meta.get("age_days")
        print(f"[WARN] 候选名称全部来自陈旧缓存（age="
              f"{cache_meta.get('age_days')}天）：新被 ST 的票可能不在表内", file=sys.stderr)
    else:
        diag["st_filter"] = "ok"
    return names, diag


def save_cache(name_map: dict[str, str], source: str, path: Path = None) -> Path:
    """原子落盘（含 generated_at / source / count），空表不落盘。"""
    if not name_map:
        raise ValueError("refuse to cache an empty name map")
    out = Path(path or CACHE)
    payload = {"generated_at": cn_today().isoformat(), "source": source,
               "count": len(name_map), "names": name_map}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    return out


def load_cache(path: Path = None) -> tuple[dict[str, str], dict[str, Any]]:
    """读缓存，返回 (names, meta)。新旧两种格式都认。

    meta: {available, generated_at, source, age_days, stale, reason}
    旧扁平格式没有 generated_at ⇒ age_days=None ⇒ stale=True（不假定新鲜）。
    """
    p = Path(path or CACHE)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, {"available": False, "stale": True,
                    "reason": f"cache_unreadable:{type(exc).__name__}"}
    if not isinstance(raw, dict) or not raw:
        return {}, {"available": False, "stale": True, "reason": "cache_malformed"}
    if isinstance(raw.get("names"), dict):                 # 新格式
        names = {str(k): str(v) for k, v in raw["names"].items()}
        gen = str(raw.get("generated_at") or "")
        source = str(raw.get("source") or "cache")
    else:                                                  # 旧扁平格式
        names = {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}
        gen, source = "", "legacy_cache"
    if not names:
        return {}, {"available": False, "stale": True, "reason": "cache_empty"}
    age = _age_days(gen)
    return names, {"available": True, "generated_at": gen or None, "source": source,
                   "age_days": age,
                   "stale": age is None or age > NAME_MAP_MAX_AGE_DAYS,
                   "reason": "no_generated_at" if age is None else None}


def _age_days(generated_at: str) -> Optional[int]:
    from datetime import date
    try:
        return (cn_today() - date.fromisoformat(str(generated_at)[:10])).days
    except (ValueError, TypeError):
        return None


def _refuses_overwrite(new_map: dict, source: str, diag: dict, path: Path = None) -> bool:
    """新表是否**过小以致不该覆盖**既有缓存。返回 True = 拒绝。

    判据：既有缓存存在且非空，而 `len(new) < len(old) * NAME_MAP_MIN_OVERWRITE_RATIO`。
    首次构建（无缓存）一律放行。

    ``path`` 是**实际落盘目标**（缺省 = :data:`CACHE`）：main() 支持 ``--out``
    自定义路径，门槛必须对着将被覆盖的那份文件判，而不是固定对着默认缓存。

    ⚠️ 这里**不区分既有缓存是否陈旧**：陈旧但完整的表仍比新鲜但残缺的表适合做
    ST 排除（ST 名称变动不频繁，残缺表会让大部分票查不到名字）。
    陈旧本身会被 `load_cache` 判出来并让 `st_filter="stale"`，信息不会丢。
    """
    try:
        old, _meta = load_cache(path)
    except Exception:                                              # noqa: BLE001
        return False
    if not old:
        return False
    floor = len(old) * NAME_MAP_MIN_OVERWRITE_RATIO
    if len(new_map) >= floor:
        return False
    diag.update(name_map_rejected_source=source,
                name_map_rejected_size=len(new_map),
                name_map_rejected_reason=(
                    f"新表 {len(new_map)} 只 < 既有缓存 {len(old)} 只的 "
                    f"{NAME_MAP_MIN_OVERWRITE_RATIO:.0%}（{floor:.0f}）⇒ 拒绝覆盖"))
    print(f"[WARN] 名称表拒绝落盘：{source} 只取到 {len(new_map)} 只，"
          f"既有缓存 {len(old)} 只 —— 残缺表覆盖完整缓存会让 ST 硬排除静默失效，"
          f"已回退缓存", file=sys.stderr)
    return True


def resolve_name_map(session=None, allow_fetch: bool = True) -> tuple[dict[str, str], dict[str, Any]]:
    """取名称表并如实报告质量：在线成功即刷新缓存，失败回退缓存并判时效。

    返回 (name_map, diag)，diag.st_filter ∈ {ok, stale, unavailable} —— 调用方必须
    消费它：``ok`` 之外都意味着 ST 硬排除不完全可信。
    """
    diag: dict[str, Any] = {}
    if allow_fetch:
        name_map, source = fetch_name_map(session=session)
        if name_map and _refuses_overwrite(name_map, source, diag):
            # 新表相对既有缓存过小 ⇒ 不落盘、不当在线结果用，回退缓存路径。
            # 一份**陈旧但完整**的表比一份**新鲜但残缺**的表更适合做 ST 排除：
            # ST 名称变动不频繁，而残缺表会让 90% 的票根本查不到名字。
            name_map = {}
        if name_map:
            try:
                save_cache(name_map, source)
            except (OSError, ValueError) as exc:
                print(f"[WARN] 名称缓存落盘失败（本次仍用在线表）: {exc}", file=sys.stderr)
            diag.update(name_map_source=source, name_map_size=len(name_map),
                        name_map_age_days=0, st_filter="ok")
            return name_map, diag
    names, meta = load_cache()
    if not names:
        diag.update(name_map_source="unavailable", name_map_size=0,
                    st_filter="unavailable", name_map_reason=meta.get("reason"))
        print("[WARN] 名称表不可用（东财/TQ/mootdx/缓存均失败）：ST 硬排除失效",
              file=sys.stderr)
        return {}, diag
    diag.update(name_map_source=meta.get("source") or "cache", name_map_size=len(names),
                name_map_generated_at=meta.get("generated_at"),
                name_map_age_days=meta.get("age_days"),
                st_filter="stale" if meta.get("stale") else "ok")
    if meta.get("stale"):
        print(f"[WARN] 名称表陈旧（generated_at={meta.get('generated_at')}, "
              f"age={meta.get('age_days')}天 > {NAME_MAP_MAX_AGE_DAYS}）："
              f"新被 ST 的股票可能不在表内，ST 硬排除不完全可信", file=sys.stderr)
    return names, diag


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="构建股票名称缓存（TDX 协议优先，TQ/clist 兜底）")
    ap.add_argument("--source", choices=["auto", "clist", "tq", "mootdx"], default="auto",
                    help="auto=TDX协议→TQ→clist；clist 受限流通常拉不全，全量构建走 auto（TDX 协议）")
    ap.add_argument("--out", default=str(CACHE))
    ap.add_argument("--limit", type=int, default=0, help="仅 tq 源：只处理前 N 只（排障用）")
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    if args.source == "auto":
        name_map, source = fetch_name_map()
    elif args.source == "clist":
        try:
            # 结果直接落盘当全量缓存 → 强制覆盖率门槛，残缺表不落盘
            name_map, source = (fetch_all_from_clist(min_coverage=CLIST_MIN_COVERAGE),
                                "eastmoney_clist")
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR] 东财 clist 名称源失败: {exc}")
            return 2
    elif args.source == "tq":
        codes = None
        if args.limit:
            from custos.datasource.local_tdx import local_tdx_data
            codes = local_tdx_data.list_local_vipdoc_codes()[:args.limit]
        name_map, source = fetch_from_tq(codes), "tq_local"
    else:
        name_map, source = fetch_from_mootdx(), "mootdx"

    if not name_map:
        print(json.dumps({"ok": False, "source": source, "count": 0,
                          "reason": "all_sources_failed"}, ensure_ascii=False))
        return 2
    # ⚠️ 落盘边界门槛（与 resolve_name_map 同一条，见 NAME_MAP_MIN_OVERWRITE_RATIO）。
    # 生产每日刷新走的就是 main()，而不是 resolve_name_map——门槛只挂后者时，
    # 「TQ 只取到 3 只照样覆盖 5000 只完整缓存」的事故形态在这条路径上原样可复现。
    # 放在 save_cache 之前 ⇒ 覆盖全部 source 分支；传 --out ⇒ 对着实际落盘目标判。
    guard_diag: dict[str, Any] = {}
    if _refuses_overwrite(name_map, source, guard_diag, path=Path(args.out)):
        print(json.dumps({"ok": False, "source": source, "count": len(name_map),
                          "reason": "overwrite_refused",
                          "detail": guard_diag.get("name_map_rejected_reason")},
                         ensure_ascii=False))
        return 2
    out = save_cache(name_map, source, Path(args.out))
    st_count = sum(1 for n in name_map.values() if "ST" in n.upper())
    print(json.dumps({"ok": True, "source": source, "count": len(name_map),
                      "st_count": st_count, "output": str(out),
                      "elapsed_sec": round(time.monotonic() - t0, 1)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
