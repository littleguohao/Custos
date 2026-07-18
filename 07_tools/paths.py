# -*- coding: utf-8 -*-
"""Centralized path configuration for strategy_team.

All modules should import from here instead of hardcoding paths.
TDX_ROOT and PYTHON can be overridden via environment variables.
"""
from __future__ import annotations
import os
from pathlib import Path

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
