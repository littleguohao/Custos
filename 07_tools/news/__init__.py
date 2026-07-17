# -*- coding: utf-8 -*-
"""news package — re-export public API from rss_filter.py."""
from news.rss_filter import (
    load,
    dump,
    norm_text,
    canonical_url,
    parse_dt,
    bare,
    premarket_window,
    entities,
    dedupe,
)

__all__ = [
    "load",
    "dump",
    "norm_text",
    "canonical_url",
    "parse_dt",
    "bare",
    "premarket_window",
    "entities",
    "dedupe",
]
