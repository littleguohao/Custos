# -*- coding: utf-8 -*-
"""Pytest configuration：custos 包已可编辑安装，import 无需任何 sys.path 注入。"""

import pytest


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
        from custos.datasource.local_tdx import stock_names
    except ImportError:  # 该模块不可用时无需阻断
        return
    monkeypatch.setattr(
        stock_names,
        "resolve_names_for",
        lambda codes, **kw: (
            {},
            {
                "st_filter": "ok",
                "requested": len(list(codes)),
                "name_map_size": 0,
                "missing_count": 0,
                "name_map_source": "test_stub",
            },
        ),
        raising=False,
    )

    # 网络层也堵死：万一有别的路径绕过 resolve_names_for，直接失败而不是静默走网络
    def _no_net(*a, **kw):
        raise AssertionError(
            "测试试图发起真实 HTTP 请求（stock_names.fetch_names_for）；"
            "请注入 name_resolver 或 monkeypatch 替身"
        )

    monkeypatch.setattr(stock_names, "fetch_names_for", _no_net, raising=False)
    monkeypatch.setattr(stock_names, "fetch_all_from_clist", _no_net, raising=False)
    # TDX 协议源同样要堵：2026-08-04 起它是名称表主路径，走的是通达信 TCP 协议
    # （不是 HTTP，但一样是真实网络，会让测试变慢且结果依赖外部服务可用性）
    monkeypatch.setattr(stock_names, "fetch_from_tdx_protocol", _no_net, raising=False)
    monkeypatch.setattr(stock_names, "fetch_from_mootdx", _no_net, raising=False)


@pytest.fixture
def reversal_thresholds():
    """按依赖顺序重载 B1 反转 K 阈值链，并在退出时**完整还原**。

    ⚠️ 为什么必须有这个 fixture：`importlib.reload(b1_thresholds)` 会造出**新的
    函数对象**，而 `enrich_candidates` / `technical_monitor` 里的
    `change_in_range` 仍绑在旧对象上 —— 于是

        assert ec.change_in_range is bt.change_in_range

    在「某个测试 reload 过 bt 但没 reload ec」之后就变 False。
    2026-08-07 实测：单文件跑通过、全量跑失败，就是这个顺序污染。

    用法::

        def test_x(reversal_thresholds):
            mods = reversal_thresholds(B1_REVK_CHG_PCT="1.0")
            assert mods["b1_thresholds"].REVERSAL_CHANGE_MAX_PCT == 1.0

    不传参数时只是把整条链刷新到当前环境（用于消除前序测试的残留）。
    """
    import importlib
    import os

    # 依赖顺序：阈值 → 读它的三个 live 模块
    names = [
        "custos.core.b1_thresholds",
        "custos.pipeline.screening.enrich_candidates",
        "custos.pipeline.market_timing.technical_monitor",
        "custos.pipeline.holdings.b1_holding_state",
    ]
    saved = {
        k: os.environ.get(k)
        for k in (
            "B1_REVK_CHG_PCT",
            "B1_REVK_CHG_MIN",
            "B1_REVK_CHG_MAX",
            "B1_REVK_AMP_PCT",
            "B1_J_LOW",
        )
    }

    def _apply(**env):
        for k, v in env.items():
            os.environ[k] = str(v)
        out = {}
        for n in names:
            mod = importlib.import_module(n)
            out[n.split(".")[-1]] = importlib.reload(mod)
        return out

    _apply()  # 先刷新，消除前序测试残留
    yield _apply
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _apply()  # 还原
