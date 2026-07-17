# -*- coding: utf-8 -*-
"""Pytest configuration: make 07_tools packages importable from tests/."""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "07_tools"
sys.path.insert(0, str(TOOLS))
for sub in TOOLS.iterdir():
    if sub.is_dir() and (sub / "__init__.py").exists():
        sys.path.insert(0, str(sub))
