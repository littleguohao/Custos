# -*- coding: utf-8 -*-
"""Pytest configuration: make 07_tools packages importable from tests/."""
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "07_tools"
sys.path.insert(0, str(TOOLS))
for sub in TOOLS.iterdir():
    if sub.is_dir() and (sub / "__init__.py").exists():
        sys.path.insert(0, str(sub))


@pytest.fixture(autouse=True)
def _block_name_resolution_network(monkeypatch):
    """全局阻断股票名称解析的真实网络请求。

    ``formula_screen.screen_formulas`` 结束前会调 ``stock_names.resolve_names_for``
    用东财 ulist 刷新候选名称（ST 判定真正依赖的一步）。任何间接触发它的测试都会去打
    东财接口：慢、不稳定，而且**连续请求会触发限流并影响后续用例**（实测全量耗时从
    27s 涨到 45s，且报 RemoteDisconnected）。

    默认替身返回"名称无变化 + st_filter=ok"，不改变既有断言。需要验证刷新行为的用例
    自行注入 ``name_resolver=``（screen_formulas 的注入点）或 monkeypatch 覆盖本替身。
    """
    try:
        import stock_names
    except ImportError:                      # 该模块不可用时无需阻断
        return
    monkeypatch.setattr(
        stock_names, "resolve_names_for",
        lambda codes, **kw: ({}, {"st_filter": "ok", "requested": len(list(codes)),
                                  "name_map_size": 0, "missing_count": 0,
                                  "name_map_source": "test_stub"}),
        raising=False)
    # 网络层也堵死：万一有别的路径绕过 resolve_names_for，直接失败而不是静默走网络
    def _no_net(*a, **kw):
        raise AssertionError(
            "测试试图发起真实 HTTP 请求（stock_names.fetch_names_for）；"
            "请注入 name_resolver 或 monkeypatch 替身")
    monkeypatch.setattr(stock_names, "fetch_names_for", _no_net, raising=False)
    monkeypatch.setattr(stock_names, "fetch_all_from_clist", _no_net, raising=False)
