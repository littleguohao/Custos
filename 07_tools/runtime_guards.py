# -*- coding: utf-8 -*-
"""Shared P0 runtime guards: trading calendar, freshness and data quality."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from paths import BASE, CN_TZ, CONTRACTS_DIR, cn_now

DATA = BASE / "01_data"
CALENDAR_CONFIG = CONTRACTS_DIR / "CN_TRADING_CALENDAR.json"
CALENDAR_CACHE = DATA / "market" / "CN_TRADING_CALENDAR_CACHE.json"


def load_json(path: Path, default: Any):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


# 日历 JSON 缓存。为什么需要:trading_day_status 每次调用都要读
# CN_TRADING_CALENDAR.json + CN_TRADING_CALENDAR_CACHE.json 两个文件,而
# previous_confirmed_trading_day 会连着调它最多 14 次 ⇒ 单次门控约 28 次磁盘读
# (长假回溯时更多),而这两个文件在一次运行内根本不会变。
# 缓存**必须可失效、可注入**,不能变成测试里的隐形全局状态,故:
#   ① key 带 (mtime_ns, size) —— trading_calendar.py 刷新日历后自动失效;
#   ② key 带当前 load_json 函数对象 —— 测试 monkeypatch load_json 时自成一档,
#      patch 结束后那条记录自然不可达,不会污染后续测试;
#   ③ 暴露 clear_calendar_cache() 供显式清理。
_CALENDAR_JSON_CACHE: dict[tuple, Any] = {}
_CALENDAR_CACHE_MAX = 32


def clear_calendar_cache() -> None:
    """显式失效日历 JSON 缓存（测试 / 长驻进程用）。"""
    _CALENDAR_JSON_CACHE.clear()


def _load_calendar_json(path: Path, default: Any):
    loader = load_json          # late-bound: 尊重调用方对 load_json 的 monkeypatch
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size, loader)
    except OSError:
        # 文件缺失也缓存(mtime=None):日历缓存文件常常不存在,否则每次调用还要多一次
        # exists() + 分支。文件后来出现时 stat 成功 → key 不同 → 自动重新加载。
        key = (str(path), None, None, loader)
    if key not in _CALENDAR_JSON_CACHE:
        if len(_CALENDAR_JSON_CACHE) >= _CALENDAR_CACHE_MAX:
            _CALENDAR_JSON_CACHE.clear()
        _CALENDAR_JSON_CACHE[key] = loader(path, default)
    return _CALENDAR_JSON_CACHE[key]


def official_year_status(d: date, cfg: dict[str, Any]) -> dict[str, Any] | None:
    year = (cfg.get("official_years") or {}).get(str(d.year))
    if not year:
        return None
    source = year.get("source_url") or str(CALENDAR_CONFIG)
    if d.weekday() >= 5:
        return {"is_trading_day": False, "reason": "周末休市", "quality": "confirmed", "source": source}
    day = d.isoformat()
    for item in year.get("closed_ranges") or []:
        if item.get("start") <= day <= item.get("end"):
            return {"is_trading_day": False, "reason": f"交易所官方{item.get('name', '节假日')}休市安排", "quality": "confirmed", "source": source}
    return {"is_trading_day": True, "reason": "交易所官方年度安排：周一至周五且不在休市区间", "quality": "confirmed", "source": source}


def trading_day_status(day: str) -> dict[str, Any]:
    d = date.fromisoformat(day)
    cfg = _load_calendar_json(CALENDAR_CONFIG, {})
    cache = _load_calendar_json(CALENDAR_CACHE, {})
    overrides = cfg.get("overrides", {})
    if day in overrides:
        item = overrides[day]
        return {"date": day, "is_trading_day": bool(item["is_trading_day"]), "reason": item.get("reason", "配置覆盖"), "quality": "confirmed", "source": str(CALENDAR_CONFIG)}
    official = official_year_status(d, cfg)
    if official is not None:
        return {"date": day, **official}
    if day in set(cache.get("trading_days", [])):
        return {"date": day, "is_trading_day": True, "reason": "本地通达信交易日历缓存", "quality": "confirmed", "source": str(CALENDAR_CACHE)}
    if day in set(cache.get("non_trading_days", [])):
        return {"date": day, "is_trading_day": False, "reason": "本地通达信日历覆盖范围内非交易日", "quality": "confirmed", "source": str(CALENDAR_CACHE)}
    if d.weekday() >= 5:
        return {"date": day, "is_trading_day": False, "reason": "周末", "quality": "confirmed", "source": "weekday_rule"}
    return {"date": day, "is_trading_day": None, "reason": "工作日但不在通达信缓存覆盖范围；禁止自动假定开市", "quality": "unknown", "source": str(CALENDAR_CONFIG)}


def previous_confirmed_trading_day(day: str) -> str | None:
    """Return the latest confirmed trading day before day, or fail closed."""
    cursor = date.fromisoformat(day) - timedelta(days=1)
    for _ in range(14):
        status = trading_day_status(cursor.isoformat())
        if status["is_trading_day"] is True:
            return cursor.isoformat()
        if status["is_trading_day"] is None:
            return None
        cursor -= timedelta(days=1)
    return None


CLOSE_TIME = time(15, 0)          # 收盘时刻:导入时间晚于它才算"收盘后快照"


def _as_cn_datetime(text: str) -> datetime:
    """Parse an ISO timestamp and express it on the exchange clock.

    Historic records were written with a naive ``datetime.now()`` on whatever
    timezone the host happened to use, so a naive value must be *interpreted*
    as Shanghai time (that was always the intent) rather than compared as-is —
    otherwise the 15:00 cutoff below is off by the host's UTC offset.
    """
    d = datetime.fromisoformat(text)
    if d.tzinfo is None:
        return d.replace(tzinfo=CN_TZ)
    return d.astimezone(CN_TZ)


def position_freshness(day: str) -> dict[str, Any]:
    meta = load_json(DATA / "trades" / "_import_meta.json", {})
    imported_at = meta.get("imported_at")
    source_mtime = meta.get("source_mtime")
    status = "stale"
    reason = "缺少导入元数据"
    expected_close_date = previous_confirmed_trading_day(day)
    snapshot_date = meta.get("snapshot_date")
    if imported_at:
        try:
            imported = _as_cn_datetime(imported_at)
            effective_snapshot_date = snapshot_date or imported.date().isoformat()
            if effective_snapshot_date == expected_close_date:
                status = "confirmed"
                reason = f"使用最近已确认交易日 {expected_close_date} 的收盘持仓快照"
            elif effective_snapshot_date == day and imported.time() >= CLOSE_TIME:
                status = "confirmed"
                reason = "当日收盘后已导入持仓快照"
            elif effective_snapshot_date == day:
                status = "uncertain"
                reason = "当日已导入，但无法确认导入后是否发生盘中交易"
            else:
                reason = f"最近快照日期为 {effective_snapshot_date}，预期为 {expected_close_date or '无法确认'}"
        except ValueError:
            reason = "导入时间格式无效"
    return {
        "date": day, "status": status, "confirmed": status == "confirmed",
        "imported_at": imported_at, "source_mtime": source_mtime,
        "snapshot_date": snapshot_date or (imported_at[:10] if imported_at else None),
        "expected_close_date": expected_close_date,
        "reason": reason, "source": str(DATA / "trades" / "_import_meta.json"),
    }


def confirm_position_snapshot(day: str, note: str = "user_confirmed") -> dict[str, Any]:
    path = DATA / "trades" / "position_confirmations.json"
    records = load_json(path, {})
    records[day] = {"confirmed_at": cn_now().isoformat(timespec="seconds"), "note": note}
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return records[day]


def ledger_trades_on(day: str) -> list[dict[str, str]]:
    """Buy/sell rows recorded in the master ledger for day (empty if none)."""
    import csv

    ledger = DATA / "trades" / "master_trade_ledger.csv"
    if not ledger.exists():
        return []
    rows: list[dict[str, str]] = []
    with ledger.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("成交日期") == day and row.get("交易类别") in {"买入", "卖出"}:
                rows.append(row)
    return rows


def position_freshness_with_confirmation(day: str) -> dict[str, Any]:
    result = position_freshness(day)
    confirmations = load_json(DATA / "trades" / "position_confirmations.json", {})

    # 成交台账出现目标日成交时，立即覆盖"沿用无交易"基线
    day_trades = ledger_trades_on(day)
    if day_trades:
        summary = "；".join(f"{r.get('交易类别')}{r.get('名称')}({r.get('代码')}) {r.get('成交数量')}股@{r.get('成交价格')}" for r in day_trades)
        result.update({
            "status": "confirmed", "confirmed": True,
            "inherited": False, "ledger_trades": len(day_trades),
            "reason": f"成交台账记录 {day} 当日交易 {len(day_trades)} 笔，持仓已按增量成交更新：{summary}",
            "source": str(DATA / "trades" / "master_trade_ledger.csv"),
        })
        result.pop("assumption", None)
        return result

    if day in confirmations:
        result.update({"status": "confirmed", "confirmed": True, "reason": "用户已确认当日持仓快照", "confirmation": confirmations[day]})
        return result

    eligible = [d for d in confirmations if d <= day]
    if eligible:
        confirmed_day = max(eligible)
        confirmation = confirmations[confirmed_day]
        no_trades = confirmation.get("no_trades") is True or "无交易" in str(confirmation.get("note", ""))
        if no_trades:
            result.update({
                "status": "confirmed",
                "confirmed": True,
                "inherited": True,
                "inherited_from": confirmed_day,
                "assumption": "B1盘中默认不交易；若用户告知或成交台账出现目标日成交，则立即覆盖此基线",
                "reason": f"默认 {day} 盘中无交易，沿用 {confirmed_day} 已确认无交易后的收盘持仓作为14:45尾盘建议基线",
                "confirmation": confirmation,
            })
    return result


_QUALITY_LEVELS = {"confirmed", "auto", "candidate", "partial", "degraded", "raw_only", "stale", "missing"}


def _quality(value: Any, section: dict[str, Any], default: str = "candidate") -> str:
    """归一化 section 自报的 quality。

    ``degraded`` 必须在白名单里:collector 会主动写它表示"取到了但滞后",若不认它就会
    fallback 到 default 而被**升格**为 candidate,反而逃过 stale 判定。
    """
    if value is None or value == "":
        return "missing"
    q = str(section.get("quality") or default)
    return q if q in _QUALITY_LEVELS else default


def _latest_market_section(day: str, section_name: str, value_key: str) -> tuple[dict[str, Any], str | None]:
    for path in sorted((DATA / "market").glob("*_market_timing_input.json"), reverse=True):
        source_day = path.name[:10]
        if source_day >= day:
            continue
        section = load_json(path, {}).get(section_name, {})
        if isinstance(section, dict) and section.get(value_key) not in {None, ""}:
            return section, source_day
    return {}, None


# 各检查项的**关键性权重**。0AMV 决定 regime(做多/空头),缺它等于不知道方向;
# 海外行情只是背景参考。此前 score 是无权重算术平均(5 项各 0.2),导致
# 「0AMV 全缺 + 其余齐全」= 4/5 = 0.8 **恰好判 pass 并授予加仓权**(已实测复现)。
# 改为加权后同一场景为 0.65 → degraded,且 0AMV 不新鲜时一律不得 pass。
_CHECK_WEIGHT = {"0AMV": 35, "market_breadth": 20, "turnover": 20, "sentiment": 15, "overseas": 10}
_DEFAULT_WEIGHT = 10
# 允许加仓的 regime 白名单。**不能写成 `!= "空头"`**:0AMV 缺失时 effective_state 是
# None → 空串 → 空串 != "空头" 为真 → 未知 regime 被当成可加仓(这就是修掉的漏洞)。
_REGIME_ALLOW_INCREASE = {"做多", "中性"}


def normalize_regime(raw: str) -> str:
    """把 regime 文本归一到 {做多, 中性, 空头, 未知}。

    存在三套并行词表:amv_state 写 `做多/中性/空头`;`amv_zone` 是 `做多触发/空头触发/阈值内`,
    而 merge_incremental_market 会用 amv_zone 兜底填 effective_state;README 又写作"多头"。
    归一后再判白名单,避免任何一套词表漏进"未知"分支被误当可加仓。
    """
    s = str(raw or "").strip()
    if not s:
        return "未知"
    if "空头" in s:
        return "空头"
    if "做多" in s or "多头" in s:
        return "做多"
    if "中性" in s or "阈值内" in s:
        return "中性"
    return "未知"


def market_quality_gate(market: dict[str, Any], day: str, expected_day: str | None = None) -> dict[str, Any]:
    """expected_day:该 session 期望的数据日(盘前/盘中=T-1,盘后=T;缺省=day,保持既有行为)。"""
    exp = expected_day or day
    checks = []
    specs = [
        ("0AMV", "amv_0", "amv_change_pct"),
        ("market_breadth", "market_breadth", "up_count"),
        ("sentiment", "sentiment", "limit_up_count"),
        ("turnover", "turnover", "turnover_change_pct"),
    ]
    inherited: dict[str, Any] = {}
    for field, section_name, value_key in specs:
        section = market.get(section_name, {})
        source_day = day
        if not isinstance(section, dict) or section.get(value_key) in {None, ""}:
            prior, prior_day = _latest_market_section(day, section_name, value_key)
            if prior_day:
                section = prior
                source_day = prior_day
                inherited[section_name] = {"as_of": prior_day, "data": prior}
        # 缺 quality 字段一律按"未确认"处理。**不能给 0AMV 特权默认 confirmed**:
        # collector 与 --amv 人工读数写入的 section 都没有 quality 键,一旦默认 confirmed,
        # amv_ok=True → 加权分 ≥0.8 判 pass → 授予 allow_position_increase,与本文件
        # 声明的「0AMV 非 confirmed/auto 时一律不得 pass」正好相反。这与 07-31 修掉的
        # 空串 regime 是同一个不变量:"没说它是可信的" ≠ "它是可信的"。
        quality = _quality(section.get(value_key), section, "candidate")
        # 陈旧判定必须看 as_of,不能只看"来自哪个文件":当日文件里也可能装着 T-1 的
        # 宽度/成交额(TdxW 未刷新时 collect 取了上一根 K 线),那同样不是当日数据。
        # 对比基准是 session 期望数据日 exp(盘前/盘中=T-1,不应用日历日误伤正常盘前)。
        section_as_of = str(section.get("as_of") or "")[:10]
        stale_as_of = bool(section_as_of) and section_as_of != exp
        # 继承分支同样按 exp 比,而不是按日历日 day 比。原写法 `source_day != day` 与
        # expected_day 机制自相矛盾:盘前 session 期望 T-1,继承自 T-1 文件的 section
        # 明明就是**期望的那份数据**,却因"不是当日文件"被标 stale —— 四个核心块全被
        # 这么标就触发 blocked,正是 README 记的 2026-07-30 盘后链被误阻断的方向。
        # 只在 source_day 既非 day 也非 exp 时才算陈旧(纯放宽:不新增任何 stale/blocked)。
        stale_source = source_day != day and source_day != exp
        if (stale_source or stale_as_of) and quality in {"confirmed", "auto"}:
            quality = "stale"
        checks.append({
            "field": field,
            "quality": quality,
            "as_of": section.get("as_of") or source_day,
            "inherited": source_day != day,
            "stale_source": stale_source,
            "stale_as_of": stale_as_of,
        })
    overseas = market.get("overseas_market", {})
    overseas_values = [overseas.get(k) for k in ("nasdaq_change_pct", "sp500_change_pct", "sox_change_pct", "nikkei_change_pct", "kospi_change_pct", "hstech_change_pct")]
    checks.append({"field": "overseas", "quality": "confirmed" if any(v is not None for v in overseas_values) and overseas.get("as_of") else ("candidate" if any(v is not None for v in overseas_values) else "missing"), "as_of": overseas.get("as_of")})
    rank = {"confirmed": 1.0, "auto": 1.0, "candidate": 0.5, "partial": 0.4, "degraded": 0.4,
            "raw_only": 0.0, "stale": 0.0, "missing": 0.0}
    weights = {x["field"]: _CHECK_WEIGHT.get(x["field"], _DEFAULT_WEIGHT) for x in checks}
    total_w = sum(weights.values()) or 1
    score = sum(rank[x["quality"]] * weights[x["field"]] for x in checks) / total_w
    # blocked 用**显式覆盖率规则**而不是分数阈值:加权会让"只剩一个次要块新鲜"的场景掉到
    # 0.4 以下,凭空多出 24 种阻断场景(实测)。而 blocked 会经 --require-quality /
    # --require-gate 真正中断链路——README 门控节记着 2026-07-30 悄悄收紧硬闸导致 17:00
    # 盘后复盘直接失败的教训,所以这里精确保持原语义:**四个核心块全废才算大面积缺数**。
    core = [x for x in checks if x["field"] != "overseas"]
    core_bad = [x for x in core if x["quality"] in {"stale", "missing", "raw_only"}]
    if core and len(core_bad) == len(core):
        status = "blocked"
    else:
        status = "pass" if score >= 0.8 else "degraded"
    amv_chk = next((x for x in checks if x["field"] == "0AMV"), None)
    amv_quality = amv_chk["quality"] if amv_chk else "missing"
    amv_ok = amv_quality in {"confirmed", "auto"}
    limitations: list[str] = []
    if not amv_ok:
        # regime 未知就不该给"数据齐全"的结论。只把 pass 降到 degraded,**不新增阻断场景**
        # (blocked 会经 --require-quality / --require-gate 真正中断链路,详见 README 门控节)。
        if status == "pass":
            status = "degraded"
        limitations.append(f"0AMV={amv_quality}：regime 未知，不得据此加仓")
    for x in checks:
        if x["quality"] in {"stale", "missing", "raw_only"} and x["field"] != "0AMV":
            limitations.append(f"{x['field']}={x['quality']}(as_of={x.get('as_of')})")
    return {
        "date": day, "expected_day": exp,
        "status": status,
        "quality_score": round(score, 3), "checks": checks, "inherited_sections": inherited,
        "weights": weights, "amv_ok": amv_ok, "limitations": limitations,
        "rule": "盘中缺少盘后指标时沿用最近有效交易日并标明日期；继承值仅供状态判断，不单独授予加仓权限；"
                "评分按关键性加权（0AMV 权重最高），0AMV 不新鲜时一律不得 pass",
    }


def position_increase_decision(market: dict[str, Any], *, reduction_ready: bool,
                               technical_current: bool, quotes_current: bool,
                               market_quality: dict[str, Any]) -> dict[str, Any]:
    """是否授予**加仓**权限。抽成纯函数以便单测——这是钱的路径,不能只靠端到端覆盖。

    历史漏洞:曾写作 `market_regime != "空头"`,而 0AMV 缺失时 effective_state 是 None →
    空串 → `"" != "空头"` 为真 ⇒ **regime 未知却授予加仓权**。现改为白名单 + 要求 0AMV 本身
    新鲜(market_quality.amv_ok),任一不满足都不放行(风控优先于买入,DECISION_PRIORITY_RULES)。
    """
    amv_section = market.get("amv_0") or {}
    regime_raw = str(amv_section.get("effective_state") or amv_section.get("amv_zone") or "")
    regime = normalize_regime(regime_raw)
    regime_ok = regime in _REGIME_ALLOW_INCREASE
    amv_ok = bool(market_quality.get("amv_ok"))
    allow = (reduction_ready and technical_current
             and market_quality.get("status") == "pass" and regime_ok and amv_ok)
    limits = list(market_quality.get("limitations") or [])
    if not regime_ok:
        limits.append(f"regime={regime}(原始值 {regime_raw!r})不在加仓白名单 "
                      f"{sorted(_REGIME_ALLOW_INCREASE)}")
    if not technical_current:
        limits.append("持仓技术指标未更新至目标日")
    if not quotes_current:
        limits.append("持仓行情未覆盖全部持仓")
    return {"allow": allow, "regime": regime, "regime_raw": regime_raw,
            "regime_ok": regime_ok, "limitations": limits}


def write_runtime_gate(day: str, expected_day: str | None = None) -> dict[str, Any]:
    market_path = DATA / "market" / f"{day}_market_timing_input.json"
    market = load_json(market_path, {})
    positions = load_json(DATA / "trades" / "current_positions.json", [])
    freshness = position_freshness_with_confirmation(day)
    market_quality = market_quality_gate(market, day, expected_day=expected_day)
    quote_path = DATA / "market" / f"{day}_holding_quotes.json"
    quote_snapshot = load_json(quote_path, {})
    quotes = quote_snapshot.get("quotes", []) if isinstance(quote_snapshot, dict) else []
    position_codes = {str(x.get("代码", "")).split(".")[0] for x in positions}
    quote_codes = {str(x.get("code", "")).split(".")[0] for x in quotes if x.get("date") == day and x.get("price") is not None}
    quotes_current = bool(position_codes) and position_codes.issubset(quote_codes)
    # 行情日期是否**经数据自证**。快照源(tq_http / 东财 push2)没有日期字段,采集侧只能
    # 把目标日写进去,这会消解 collect 里 `q["date"] != target` 那唯一一道陈旧检测
    # (审计 C1)。此处仅**如实报告**、不改变 quotes_current 的既有判定:门控收紧必须
    # 先跑几个交易日校准,README 记着 2026-07-30 悄悄收紧硬闸导致盘后复盘失败的教训。
    unverified = sorted({str(x.get("code", "")).split(".")[0] for x in quotes
                         if x.get("date") == day and x.get("date_verified") is False})
    technical = load_json(DATA / "holdings" / f"{day}_holding_technical_summary.json", [])
    technical_dates = sorted({str(x.get("latest_date")) for x in technical if x.get("latest_date")})
    technical_current = bool(technical_dates) and technical_dates == [day]
    technical_freshness = {
        "status": "confirmed" if technical_current else ("stale" if technical_dates else "missing"),
        "latest_dates": technical_dates,
        "expected_date": day,
        "reason": "持仓技术行情已更新至目标日" if technical_current else "持仓技术指标未更新至目标日，不得据此提高仓位；精确减仓数量另由当日行情快照授权",
    }
    reduction_ready = freshness.get("status") == "confirmed" and quotes_current
    decision = position_increase_decision(market, reduction_ready=reduction_ready,
                                          technical_current=technical_current,
                                          quotes_current=quotes_current,
                                          market_quality=market_quality)
    increase_ready = decision["allow"]
    market_regime, regime_raw = decision["regime"], decision["regime_raw"]
    regime_ok, gate_limits = decision["regime_ok"], decision["limitations"]
    if unverified:
        gate_limits = gate_limits + [
            f"行情日期未经数据自证(快照源无日期字段)：{'、'.join(unverified)}"
            f"——postclose 时若 TdxW 未刷新可能实为 T-1 价"]
    position_gate = {
        "status": "pass" if increase_ready else ("degraded" if reduction_ready else "blocked"),
        "allow_precise_quantity": reduction_ready,
        "allow_position_reduction": reduction_ready,
        "allow_position_increase": increase_ready,
        "position_count": len(positions),
        "quote_snapshot": str(quote_path),
        "quote_date": quote_snapshot.get("as_of_date") if isinstance(quote_snapshot, dict) else None,
        "quotes_current": quotes_current,
        "quotes_date_unverified": unverified,
        "market_regime": market_regime,
        "market_regime_raw": regime_raw,
        "regime_allows_increase": regime_ok,
        "limitations": gate_limits,
        "rule": "B1默认盘中不交易；最近确认无交易后的收盘持仓可作为14:45尾盘建议基线。持仓基线+当日全持仓行情可授予精确减仓数量权限；加仓另需当日技术、市场质量 pass、0AMV 新鲜且 regime 属白名单（做多/中性）——regime 未知一律不放行",
    }
    result = {
        "date": day,
        "calendar": trading_day_status(day),
        "position_freshness": freshness,
        "technical_freshness": technical_freshness,
        "position_gate": position_gate,
        "market_quality": market_quality,
        "generated_at": cn_now().isoformat(timespec="seconds"),
    }
    out = DATA / "quality" / f"{day}_runtime_gate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
