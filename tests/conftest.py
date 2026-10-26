# -*- coding: utf-8 -*-
"""Pytest configuration：custos 包已可编辑安装，import 无需任何 sys.path 注入。"""

# 快照环境变量防呆（TODO #116 review）：``paths.SNAPSHOT_ROOT`` 在 import
# custos.core.paths 时按 ``CUSTOS_DATA_SNAPSHOT`` 定死——shell 里 export 了它
# 再跑 pytest，整个套件会在快照模式下跑：台账类测试被硬闸拦、加载类测试读到
# 真实快照，报错难懂。conftest 是 pytest 最先加载的点（本文件顶层不 import
# 任何 custos 模块，pop 一定先于全会话内任何 custos import）。快照模式的专项
# 测试用 monkeypatch 切 ``paths.SNAPSHOT_ROOT``，不经环境变量，不受影响。
import os as _os

if _os.environ.pop("CUSTOS_DATA_SNAPSHOT", None) is not None:
    print(
        "[conftest] 检测到 CUSTOS_DATA_SNAPSHOT，已从测试环境移除"
        "（快照专项测试走 monkeypatch，见 tests/test_snapshot_mode.py）"
    )

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


@pytest.fixture(autouse=True)
def _pin_company_position_cache_empty(monkeypatch):
    """把 enrich_candidates 的公司地位台账缓存钉成 {}（2026-08-16 review 修复）。

    compute_metrics 会读真实的 data/fundamentals/company_profile.jsonl——该文件
    只在有 F10 台账的机器上存在、未被 git 跟踪 ⇒ 不钉的话，任何对
    compute_metrics 输出的精确断言都变成机器相关。需要真实台账行为的用例自行
    设置 ``enrich_candidates._COMPANY_POSITION_CACHE``（见 test_company_profile.py）。
    """
    from custos.pipeline.screening import enrich_candidates

    monkeypatch.setattr(enrich_candidates, "_COMPANY_POSITION_CACHE", {})


@pytest.fixture(autouse=True)
def _redirect_position_plans_tmp(monkeypatch, tmp_path):
    """把持仓计划文件改道 tmp（v0.82）。

    ``incremental_ledger.apply_positions`` / ``main`` 现在会同步写
    ``data/trades/position_plans.json``（并读 ``data/stock_pool/`` 找候选止损价）。
    不钉的话，任何走这两处的测试都会写真实仓库数据、且断言依赖本机候选池内容。
    需要真实路径行为的用例自行 monkeypatch 覆盖（见 test_position_plans.py）。
    """
    from custos.core.trades import position_plans

    monkeypatch.setattr(position_plans, "PLANS_FILE", tmp_path / "position_plans.json")
    monkeypatch.setattr(position_plans, "POOL_DIR", tmp_path / "stock_pool")


@pytest.fixture(autouse=True)
def _redirect_plan_shadow_ledger_tmp(monkeypatch, tmp_path):
    """把 plan 影子判定台账改道 tmp（v0.310）。

    ``review_core`` / ``final_close_review`` 的重估流程现在每持仓追加一行
    ``data/trades/plan_shadow_observations.jsonl``——不钉的话，走这两处的
    测试会写真实仓库台账。需要真实路径行为的用例自行传 ``path=`` 或
    monkeypatch 覆盖（见 test_plan_shadow_ledger.py）。
    """
    from custos.pipeline.holdings import plan_shadow_ledger

    monkeypatch.setattr(
        plan_shadow_ledger,
        "PLAN_SHADOW_LEDGER",
        tmp_path / "plan_shadow_observations.jsonl",
    )


@pytest.fixture(autouse=True)
def _redirect_pick_practice_ledger_tmp(monkeypatch, tmp_path):
    """选股练习台账改道 tmp（TODO #101）——测试不得写真实 data/trades/pick_practice.jsonl。"""
    from custos.research import pick_practice

    monkeypatch.setattr(
        pick_practice, "PICK_PRACTICE_LEDGER", tmp_path / "pick_practice.jsonl"
    )


@pytest.fixture(autouse=True)
def _skip_trade_counterfactual_subprocess(monkeypatch):
    """周报/月报的操作反事实小节走子进程跑研究 CLI（v0.424）——测试默认跳过，
    专测 ``trade_cf_section`` 的用例自行 delenv。"""
    monkeypatch.setenv("CUSTOS_SKIP_TRADE_CF", "1")
    # v0.455（TODO #120）：月报 holdout 跟踪节同样子进程跑研究 CLI，测试默认跳过；
    # 专测 holdout_tracking 的用例自行 delenv + 注入假 runner
    monkeypatch.setenv("CUSTOS_SKIP_HOLDOUT_TRACKING", "1")
    # v0.447：V0 分落盘缓存测试中默认关（monkeypatch 的假打分不得写进 / 读出真缓存）
    monkeypatch.setenv("CUSTOS_V0_SCORE_CACHE", "off")


@pytest.fixture(autouse=True)
def _redirect_window_usage_ledger_tmp(monkeypatch, tmp_path):
    """把判定窗/pre2019 使用台账改道 tmp（v0.321）。

    研究工具的 run_study/main 现在会登记窗口读取（record_use）——不钉的
    话，走这些路径的测试会写真实 governance/research/window_usage.jsonl。
    """
    from custos.research import window_usage

    monkeypatch.setattr(window_usage, "LEDGER", tmp_path / "window_usage.jsonl")


@pytest.fixture(autouse=True)
def _redirect_judgment_batch_registry_tmp(monkeypatch, tmp_path):
    """把判定段批次锁登记文件改道 tmp（v0.374，TODO #95）。

    研究终端的判定段批次门（judgment_batch.gate_judgment_read/mark_read）
    会读写 ``governance/research/judgment_batches.jsonl``——不钉的话，生产
    方向钉测会把假批次写进真实登记文件（入库的 append-only 台账）。
    """
    from custos.research import judgment_batch

    monkeypatch.setattr(judgment_batch, "REGISTRY", tmp_path / "judgment_batches.jsonl")


@pytest.fixture(autouse=True)
def _redirect_xdxr_cache_tmp(monkeypatch, tmp_path):
    """把 xdxr 权息缓存目录改道 tmp（v0.370）。

    研究侧默认股本/分红读取（如 regime_style_study 的 ``_default_shares_fn``
    /``_default_xdxr_fn``）缓存未命中会尝试在线拉取，并把**失败墓碑**写进
    ``data/market/xdxr/``——不钉的话，测试会话往真实仓库写几十个垃圾缓存
    （v0.368 CI 双平台 test_repo_hygiene 红；本机全量曾误绿，因为这些墓碑
    已被更早的测试运行写进本机树，mtime 早于会话起点）。
    """
    from custos.datasource.local_tdx import adjust_factors

    monkeypatch.setattr(adjust_factors, "CACHE_DIR", tmp_path / "xdxr")


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

    # 依赖顺序：阈值 → 读它的 live/因子模块
    names = [
        "custos.core.b1_thresholds",
        # v0.86（因子化批 B）：j_below_threshold 迁入 weekly_j——它的默认参数
        # threshold=J_LOW_THRESHOLD 在 def 时绑定，必须在 enrich 之前一并 reload，
        # 否则 ec.j_below_threshold 仍按旧阈值判（test_j_vol_thresholds_also_single_source）。
        "custos.core.factors.weekly_j",
        # v0.86（因子化批 C）：patterns 五单项判定迁入 entry_patterns（读
        # J_LOW/VOL_* /REVERSAL_* 阈值）、J<13 门槛登记为 j_low_gate（默认参数
        # 同样在 def 时绑定）——都须在 enrich 之前 reload，否则选股链判定
        # 仍按旧阈值。
        "custos.core.factors.entry_patterns",
        "custos.core.factors.j_low_gate",
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


def pytest_collection_modifyitems(config, items):
    """测试分层（v0.454）：零假设校准两连测与回归哨兵（类名含 Calibration / Sentinel，及
    test_null_calibration 全模块）自动打 ``slow``——单例 20~100s，合计约 10 分钟。
    本地 ``test_affected`` 默认 ``-m "not slow"``；CI / ``--full`` / ``--with-slow`` 照跑。"""
    slow = pytest.mark.slow
    for it in items:
        cls = getattr(it, "cls", None)
        name = cls.__name__ if cls is not None else ""
        if (
            "Calibration" in name
            or "Sentinel" in name
            or it.module.__name__.endswith("test_null_calibration")
        ):
            it.add_marker(slow)
