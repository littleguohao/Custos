# -*- coding: utf-8 -*-
"""Centralized path configuration for strategy_team.

All modules should import from here instead of hardcoding paths.
TDX_ROOT and PYTHON can be overridden via environment variables.

Also the single source of truth for "what day is it" — see cn_now/cn_today.
"""
from __future__ import annotations
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# The market clock. Every date/time that ends up in a filename, an `as_of`
# field, or a freshness comparison must come from here.
#
# Modules used to call date.today() / datetime.now() (host timezone) while
# hardcoding "+08:00" into the strings they emitted. On a UTC host — which is
# what CI and most Linux boxes are — 08:50 CST is still the previous calendar
# day in UTC, so the same trading day's data split across two filenames and
# every as_of/stale comparison shifted by 8 hours.
CN_TZ = ZoneInfo("Asia/Shanghai")


def cn_now() -> datetime:
    """Timezone-aware current time in the exchange's timezone."""
    return datetime.now(CN_TZ)


def cn_today() -> date:
    """Current trading-calendar date in the exchange's timezone."""
    return cn_now().date()


# Project root: strategy_team/
BASE = Path(__file__).resolve().parent.parent

# Data directories
DATA = BASE / "01_data"
GOVERNANCE = BASE / "00_governance"
PLANS = BASE / "03_daily_plans"
REVIEWS = BASE / "04_reviews"
TOOLS = BASE / "07_tools"
LOGS = BASE / "06_logs"

# Subdirectories under 01_data/
HOLDINGS_DIR = DATA / "holdings"
MARKET_DIR = DATA / "market"
NEWS_DIR = DATA / "news"
QUALITY_DIR = DATA / "quality"
TRADES_DIR = DATA / "trades"
SECTORS_DIR = DATA / "sectors"
DECISIONS_DIR = DATA / "decisions"
RISK_DIR = DATA / "risk"
STOCK_POOL_DIR = DATA / "stock_pool"

# ---------------------------------------------------------------------------
# 00_governance/ 的四个子目录（2026-08-06 分类重构）
#
# 按**生命周期**分，而不是按主题分——这是分开的理由：
#   strategy/   规则。人与 LLM 读；改动要进 05_strategy_versions
#   data/       数据层现状与接口能力。随数据源变动
#   research/   回测研究。只增，结论会被推翻
#   contracts/  契约 + 运行时配置。**代码直接依赖**，改错直接影响运行
#
# ⚠️ 配置文件此前与 22 份文档平铺在一起，其中 CN_TRADING_CALENDAR.json 有 7 处代码
# 依赖 —— 改文档和改代码依赖项的风险完全不对等，混放让人无法区分。
# 所有配置路径**只在这里定义一次**，模块不要再自己拼 `BASE / "00_governance" / ...`。
# ---------------------------------------------------------------------------
STRATEGY_DIR = GOVERNANCE / "strategy"
DATA_DOCS_DIR = GOVERNANCE / "data"        # 命名区别于 DATA(01_data 运行时数据)
RESEARCH_DIR = GOVERNANCE / "research"
CONTRACTS_DIR = GOVERNANCE / "contracts"

# 运行时配置（代码直接读）
SCREEN_FORMULA_REGISTRY_FILE = CONTRACTS_DIR / "SCREEN_FORMULA_REGISTRY.json"
RSS_SOURCE_REGISTRY_FILE = CONTRACTS_DIR / "RSS_SOURCE_REGISTRY.json"
RSS_FILTER_CONFIG_FILE = CONTRACTS_DIR / "RSS_FILTER_CONFIG.json"
RSSHUB_ROUTES_FILE = CONTRACTS_DIR / "RSSHUB_PRIVATE_ROUTE_CANDIDATES.json"
B1_DIR = STRATEGY_DIR / "b1"                     # B1 主策略上下文
CZ_DIR = STRATEGY_DIR / "cz"                     # CZ 辅策略上下文
FACTORS_DIR = STRATEGY_DIR / "_factors"          # 跨策略可复用因子
STRATEGY_REGISTRY_FILE = STRATEGY_DIR / "STRATEGY_REGISTRY.json"
CZ_SECTOR_PREFERENCE_FILE = CZ_DIR / "CZ_SECTOR_PREFERENCE.json"

# TongDaXin installation root (overridable via env)
TDX_ROOT = Path(os.environ.get("TDX_ROOT", r"E:\new_tdx64"))

# TDX sub-paths
TDX_VIPDOC = TDX_ROOT / "vipdoc"
TDX_PYPLUGINS = TDX_ROOT / "PYPlugins" / "user"

# Python executable (overridable via env, defaults to sys.executable)
PYTHON = os.environ.get("STRATEGY_PYTHON", None)

# Calendar
CALENDAR_FILE = CONTRACTS_DIR / "CN_TRADING_CALENDAR.json"

# 相对项目根的路径，供**需要注入 base 的调用方**用（如 weekly_review 为便于测试
# 接受 base 参数）。它们仍以这里为唯一来源——不要在模块里自己拼 "00_governance"。
CALENDAR_RELPATH = Path("00_governance") / "contracts" / "CN_TRADING_CALENDAR.json"


def read_json(path, default):
    """读 JSON，文件不存在时返回 default。

    ⚠️ **编码必须是 `utf-8-sig`。** 2026-08-06 收敛 4 份重复实现时发现它们编码不一致：
    `trading_calendar` 用 `utf-8-sig`，而 `runtime_guards` / `daily_report` /
    `generate_risk_and_sectors` 用 `utf-8` —— **后者遇到带 BOM 的文件会解析失败**
    （BOM 被当成内容字符）。`utf-8-sig` 对无 BOM 文件同样正常，所以统一取它。

    这就是「统一重复实现时不能只看形状相同」的实例：四份代码长得几乎一样，
    但其中一份**修过一个别人没修的 bug**。合并时若取多数派的写法，等于把修复回退了。

    ⚠️ 放在 paths.py 是**权衡**：这四个调用方都已 import paths，
    新建模块会让它们各多一个依赖；代价是 paths 从「纯路径」变成「路径 + 基础工具」
    （它此前已有 cn_today / cn_now）。
    """
    import json as _json
    return _json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default


def write_json(path, obj, *, indent: int = 2) -> None:
    """写 JSON 产物：建父目录 + `ensure_ascii=False` + **`allow_nan=False`**。

    ⚠️ `allow_nan=False` 是这个函数存在的**主要理由**。收敛 3 份 `dump()` 时发现
    只有两份带它（`postclose_news_digest` / `rss_filter`），
    `generate_risk_and_sectors` 没带 —— 而它写的正是 RiskDecision 与 SectorState。

    不带这个参数时 `json.dumps` 会把 NaN 写成 `NaN`、inf 写成 `Infinity`，
    **两者都不是合法 JSON**（RFC 7159）。危害不止「格式不对」：

        sector_state[].score = NaN  →  下游 `score >= 60` 判定里 `nan >= 60` 恒为 False
                                    ⇒ 板块**静默降级**成「观察」，且没有任何告警

    带上它则在写入时就**当场崩**，把静默错误变成显式失败 —— 这是想要的方向。

    需要原子性（累积状态 / 读-改-写共享文件）时用 `write_json_atomic`。
    """
    import json as _json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(obj, ensure_ascii=False, indent=indent, allow_nan=False),
                    encoding="utf-8")


def write_json_atomic(path, obj, *, indent: int = 2) -> None:
    """原子写 JSON：先写同目录 tmp，再 `os.replace` 换名。

    **只有两类写入需要它**（其余用普通 write_text 就好，别过度原子化）：

        ① **累积状态** —— 损坏就丢历史，无法从当日数据重建。
           例：`0amv_regime_history.json`（全历史 regime）、
               `position_confirmations.json`（人工确认记录）。
        ② **读-改-写的共享文件** —— 多个 stage 依次改写同一份。
           例：`{date}_market_timing_input.json` 被 collector → merge → amv_state
               依次读改写；写坏要重跑整链，而**盘中快照那种数据窗口已经过去、
               重跑也拿不回**。

    纯产物（报告、run log、门控 JSON、采集输出）不必原子化：下次跑会重写。

    为什么单独抽出来：`trades/incremental_ledger` 早就有一份私有 `_write_atomic`
    （它踩过「持仓已加、台账没记 ⇒ 下次导入重复计账、持仓静默翻倍」的真实缺陷），
    但 `amv_state` / `merge_incremental_market` 这些同样是累积/读改写的地方还在裸写。
    2026-08-06 收敛到一处。
    """
    import json as _json
    import os as _os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_json.dumps(obj, ensure_ascii=False, indent=indent, default=str),
                   encoding="utf-8")
    _os.replace(tmp, path)
