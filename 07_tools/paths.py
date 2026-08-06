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
CZ_SECTOR_PREFERENCE_FILE = STRATEGY_DIR / "CZ_SECTOR_PREFERENCE.json"

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
