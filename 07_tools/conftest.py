# -*- coding: utf-8 -*-
"""Pytest configuration: make 07_tools importable as both packages and bare modules."""
import sys
from pathlib import Path

# Add 07_tools and each subpackage to sys.path so bare module imports work
# alongside package-qualified imports.
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
for sub in TOOLS.iterdir():
    if sub.is_dir() and (sub / "__init__.py").exists():
        sys.path.insert(0, str(sub))
