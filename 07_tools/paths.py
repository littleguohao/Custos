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

# TongDaXin installation root (overridable via env)
TDX_ROOT = Path(os.environ.get("TDX_ROOT", r"E:\new_tdx64"))

# TDX sub-paths
TDX_VIPDOC = TDX_ROOT / "vipdoc"
TDX_PYPLUGINS = TDX_ROOT / "PYPlugins" / "user"

# Python executable (overridable via env, defaults to sys.executable)
PYTHON = os.environ.get("STRATEGY_PYTHON", None)

# Calendar
CALENDAR_FILE = GOVERNANCE / "CN_TRADING_CALENDAR.json"
