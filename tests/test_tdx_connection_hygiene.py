# -*- coding: utf-8 -*-
"""TDX 连接卫生检查——**可执行的规范**，而不是又一份文档。

## 为什么需要这个文件

2026-08-04 我修了 `local_tdx_data._get_client()` 的「永不重连」问题（commit 503b77d），
把它作为反模式写进 `governance/data/DATA_SOURCE_PRINCIPLE.md`，还在那里立了规范：

> 所有走 TDX 协议的调用**必须**经 `local_tdx_data._with_client_retry()`

**然后在同一天创建 `market_timing/tdx_ext_quotes.py` 时写了字面上一模一样的代码**
（commit 3c7c833），也没用 `_with_client_retry`。次日审查（aeb3e25）才发现。
再查一遍全仓，又发现**第三处**：`collect_holding_quotes.py` —— 而它是 14:45 / 17:00
采集持仓行情的必经之路，连接死了整条链的行情就没了。

结论：**写进文档不等于内化**。同一个错误在同一天犯第二次，说明文档只是「记录了一个
历史问题」，没有变成「写新代码时的检查项」。所以补救措施必须是能自动跑的检查。

## 反模式长什么样

```python
_client = None
def _get_client():
    global _client
    if _client is None:          # ← 连接一断就永久失效
        _client = Quotes.factory(...)
    return _client
```

危害不是崩溃，而是**静默失效**：mootdx 的 `stocks()` 内部是 `if counts > 0`，
`stock_count()` 失败返回 None ⇒ `None > 0` ⇒ 抛看不懂的 `'>' NoneType`。
当年就因此把「连接层 bug」误判成「上游接口失效」，改用了更不稳的 HTTP 源绕过。
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "src" / "custos"

# 允许豁免的文件及理由（豁免必须写理由，不能空着）。
# 2026-08-24 数据层解耦后为空：原豁免的 calc_mfe_mae.py 已不再自建 mootdx
# client（在线/本地兜底都改走 local_tdx_data），豁免随违规一并清除。
EXEMPT: dict[str, str] = {}

# 认可的重连机制标志（任一即可）
RECONNECT_MARKERS = (
    "_with_client_retry",  # 复用 local_tdx_data 的统一实现
    "_client_call",  # 模块内的等价包装
    "force_new",  # 支持强制重建
    "CLIENT_MAX_AGE",  # 连接时效上限
    "_MAX_AGE",
    "reconnect",
)


def _modules_creating_tdx_clients() -> list[pathlib.Path]:
    out = []
    for p in sorted(TOOLS.rglob("*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        # 只看真正的调用，排除文档字符串里提到的（形如 `Quotes.factory(market='ext')` 能取…）
        if re.search(r"Quotes\.factory\s*\([^)]*\)\s*$", src, re.M) or re.search(
            r"=\s*Quotes\.factory\s*\(", src
        ):
            out.append(p)
    return out


def _client_getters(src: str, *, path: str = "<str>"):
    """找出「用 global 缓存客户端」的函数（即单例 getter）。

    用 AST 而不是字符串匹配。第一版检查只搜模块里有没有 `CLIENT_MAX_AGE` 之类的
    标志字符串，结果**一个残留的模块级常量就骗过了它**——我把 `tdx_ext_quotes`
    的反模式故意还原做反向验证时，检查照样全绿。
    教训：写完检查必须做反向验证，否则不知道它是否真的有效。

    ⚠️ 语法错误**不静默跳过**（第二版曾 `except SyntaxError: return []`，
    于是文件一坏就等于放行——反向验证时正是因此再次全绿）。
    """
    tree = ast.parse(src)  # 故意不捕获：语法错误本身就是要报出来的问题
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        gvars = {n for g in ast.walk(fn) if isinstance(g, ast.Global) for n in g.names}
        if not any("client" in g.lower() for g in gvars):
            continue
        # 函数体里确实给该全局变量赋过值（真的是 getter/缓存点）
        vals = [
            a.value
            for a in ast.walk(fn)
            if isinstance(a, ast.Assign)
            for t in a.targets
            if isinstance(t, ast.Name) and t.id in gvars
        ]
        if not vals:
            continue
        # 排除 invalidate/drop 型函数：只把客户端置为 None。
        # 那本身**就是**重连机制的一部分（失败后丢弃缓存、下次重建），
        # 要求它自己带重建逻辑是误报——实测 `_drop_ext_client` 就这样被误判过。
        if all(isinstance(v, ast.Constant) and v.value is None for v in vals):
            continue
        out.append(fn)
    return out


def _getter_can_rebuild(fn) -> bool:
    """该 getter 是否存在「除了 is None 之外」的重建路径。

    认可两类：
      · 有 force_new / refresh 之类的形参（调用方可强制重建）
      · 函数体里有时效判断（time.time() / MAX_AGE / created_at 比较）
      · 直接委托给已知带重连的实现（local_tdx_data._get_client）
    """
    args = {a.arg for a in list(fn.args.args) + list(fn.args.kwonlyargs)}
    if any(
        k in a.lower() for a in args for k in ("force", "new", "refresh", "rebuild")
    ):
        return True
    body = ast.unparse(fn)
    if re.search(r"time\.time\(\)|MAX_AGE|created_at|_age|monotonic", body):
        return True
    if "_ltd._get_client" in body or "local_tdx_data._get_client" in body:
        return True
    return False


class TestNoUnreconnectableSingletons:
    def test_found_some_modules(self):
        """保证扫描本身有效——一个都没扫到说明正则失效了。"""
        assert _modules_creating_tdx_clients(), "没扫到任何创建 TDX 客户端的模块"

    @pytest.mark.parametrize(
        "path", _modules_creating_tdx_clients(), ids=lambda p: p.name
    )
    def test_singleton_has_reconnect(self, path: pathlib.Path):
        """模块级单例必须带重连机制（AST 判定，常量残留骗不过去）。

        这条检查若在 2026-08-04 就存在，`tdx_ext_quotes` 与 `collect_holding_quotes`
        两处反模式当天就会被挡下来。
        """
        src = path.read_text(encoding="utf-8", errors="replace")
        if path.name in EXEMPT:
            assert EXEMPT[path.name].strip(), f"{path.name} 的豁免必须写明理由"
            return
        for fn in _client_getters(src):
            assert _getter_can_rebuild(fn), (
                f"{path.relative_to(TOOLS)}::{fn.name} 用 global 缓存 TDX 客户端，"
                f"但**没有除 `is None` 之外的重建路径**。\n"
                f"连接一断（TCP 空闲超时/服务器踢连接）之后每次调用都失败，且报的是\n"
                f"看不懂的 \"'>' NoneType\"（mootdx 内部 `if counts > 0` 撞上 None）。\n"
                f"修法：加 force_new 形参 + 连接时效，或直接委托 "
                f"local_tdx_data._get_client(force_new=)。\n"
                f"见 governance/data/DATA_SOURCE_PRINCIPLE.md「连接管理要求」。"
            )


class TestKnownFixesStayFixed:
    """三处已修的地方各钉一条，防止回退。"""

    def test_local_tdx_data(self):
        src = (TOOLS / "datasource" / "local_tdx" / "local_tdx_data.py").read_text(
            encoding="utf-8"
        )
        assert "force_new" in src and "CLIENT_MAX_AGE_SEC" in src
        assert "_with_client_retry" in src

    def test_tdx_ext_quotes(self):
        src = (TOOLS / "datasource" / "tdx_ext_quotes.py").read_text(encoding="utf-8")
        assert any(m in src for m in RECONNECT_MARKERS), (
            "tdx_ext_quotes 的重连机制被移除了（aeb3e25 修过）"
        )

    def test_collect_holding_quotes(self):
        src = (
            TOOLS / "datasource" / "collect" / "collect_holding_quotes.py"
        ).read_text(encoding="utf-8")
        assert "_client_call" in src, "持仓行情采集的重连包装被移除了"
        # 三处协议调用都必须走包装，不能直连
        assert not re.search(r"_get_client\(\)\.(bars|index|quotes)\(", src), (
            "有协议调用绕过了 _client_call（连接失效时不会重试）"
        )


class TestGovernanceDocStaysAligned:
    """文档与代码必须一致——否则规范就成了摆设（本文件存在的起因）。"""

    def test_principle_doc_states_requirement(self):
        doc = (
            pathlib.Path(__file__).resolve().parents[1]
            / "governance"
            / "data"
            / "DATA_SOURCE_PRINCIPLE.md"
        ).read_text(encoding="utf-8")
        assert "_with_client_retry" in doc
        assert "永不重连" in doc, "反模式的描述被删了"

    def test_doc_points_to_this_check(self):
        """文档应指向这份可执行检查，而不是只描述规范。"""
        doc = (
            pathlib.Path(__file__).resolve().parents[1]
            / "governance"
            / "data"
            / "DATA_SOURCE_PRINCIPLE.md"
        ).read_text(encoding="utf-8")
        assert "test_tdx_connection_hygiene" in doc, (
            "DATA_SOURCE_PRINCIPLE.md 应指向 tests/test_tdx_connection_hygiene.py——"
            "光有文字规范挡不住重犯，实测同一天犯了两次"
        )


# ---------------------------------------------------------------------------
# 检查函数自身的单元测试
#
# 用合成样本，**不破坏生产文件**。第一次做反向验证时我直接用正则改真实文件，
# 结果把文件改成了语法错误，而当时的实现 `except SyntaxError: return []` 静默放行
# ⇒ 验证再次全绿、误以为检查有效。两个教训：
#   ① 检查函数要像普通函数一样有单元测试；
#   ② 反向验证要用可控样本，不要动生产代码。
# ---------------------------------------------------------------------------

BAD_SINGLETON = """
from mootdx.quotes import Quotes
_client = None
def _get_client():
    global _client
    if _client is None:
        _client = Quotes.factory(market="std")
    return _client
"""

GOOD_FORCE_NEW = """
from mootdx.quotes import Quotes
_client = None
def _get_client(force_new: bool = False):
    global _client
    if force_new or _client is None:
        _client = Quotes.factory(market="std")
    return _client
"""

GOOD_MAX_AGE = """
import time
from mootdx.quotes import Quotes
_client = None
_created = 0.0
MAX_AGE = 600.0
def _get_client(timeout: int = 12):
    global _client, _created
    now = time.monotonic()
    if _client is None or (now - _created) > MAX_AGE:
        _client = Quotes.factory(market="ext", timeout=timeout)
        _created = now
    return _client
"""

GOOD_DELEGATES = """
from mootdx.quotes import Quotes
_client = None
def _get_client(force_new: bool = False):
    global _client
    try:
        import local_tdx_data as _ltd
        return _ltd._get_client(force_new=force_new)
    except Exception:
        if force_new or _client is None:
            _client = Quotes.factory(market="std")
        return _client
"""

PURE_DELEGATE = """
def _get_client(x: bool = False):
    import local_tdx_data as _ltd
    return _ltd._get_client(force_new=x)
"""

DROP_ONLY = """
_client = None
def _drop_client():
    global _client
    _client = None
"""

NOT_A_SINGLETON = """
from mootdx.quotes import Quotes
def fetch():
    q = Quotes.factory(market="std")
    return q.bars(symbol="600000")
"""


class TestCheckerItself:
    def test_detects_bad_singleton(self):
        fns = _client_getters(BAD_SINGLETON)
        assert len(fns) == 1
        assert _getter_can_rebuild(fns[0]) is False, (
            "永不重连的单例必须被判为无重建路径"
        )

    @pytest.mark.parametrize(
        "src,why",
        [
            (GOOD_FORCE_NEW, "force_new 形参"),
            (GOOD_MAX_AGE, "连接时效"),
            (GOOD_DELEGATES, "委托 + 带 force_new 的本地 fallback"),
        ],
    )
    def test_accepts_valid_patterns(self, src, why):
        fns = _client_getters(src)
        assert len(fns) == 1
        assert _getter_can_rebuild(fns[0]) is True, f"{why} 应被认可"

    def test_pure_delegate_not_flagged(self):
        """纯委托（不自己缓存）不是 getter，无需检查——它的重连由被委托方负责。"""
        assert _client_getters(PURE_DELEGATE) == []

    def test_drop_function_not_flagged(self):
        """只把客户端置 None 的函数是 invalidate，本身就是重连机制的一部分。"""
        assert _client_getters(DROP_ONLY) == []

    def test_local_client_not_flagged(self):
        """函数内局部 client（用完即弃）没有此风险。"""
        assert _client_getters(NOT_A_SINGLETON) == []

    def test_syntax_error_raises_not_silently_passes(self):
        """语法错误必须抛出——静默返回空等于放行（第二版就栽在这里）。"""
        with pytest.raises(SyntaxError):
            _client_getters("def broken(:\n    pass")


class TestOnlineQuotesMarkedUnavailable:
    """在线 TDX 行情（`client.bars` / `client.quotes`）**默认关闭**（owner 2026-08-06 拍板）。

    探针实测（--repeat 3）：

        get_online_bars()    p50 12949ms  →  DataFrame 0行×0列
        get_online_index()   p50  9992ms  →  DataFrame 0行×0列
        get_snapshot()       p50    70ms  →  dict 0 键

    三个都**不抛异常**，返回空值 ⇒ 调用方看不出「这只票没数据」与「在线源坏了」的区别。
    而 `get_ohlcv_table` 在本地 stale 时会走这条兜底：13 秒换一个空 DataFrame，
    14:45/17:00 采集 N 只持仓就是 N×13 秒纯等待。

    ⚠️ 这条测试的意义在于：把「标记为不可用」变成**代码里真的生效**。
    本仓库反复吃过「规范只写在文档里」的亏（同一个连接反模式跨两天犯了三次）。
    """

    def _fresh(self, monkeypatch):
        monkeypatch.delenv("TDX_ONLINE_QUOTES", raising=False)

    def test_disabled_by_default(self, monkeypatch):
        from custos.datasource.local_tdx import local_tdx_data as L

        self._fresh(monkeypatch)
        assert L._online_quotes_enabled() is False

    def test_bars_short_circuits_without_client(self, monkeypatch, capsys):
        """必须在 `_get_client()` 之前短路——建连本身就要花时间。"""
        from custos.datasource.local_tdx import local_tdx_data as L

        self._fresh(monkeypatch)
        monkeypatch.setattr(
            L, "_get_client", lambda: pytest.fail("短路失败：仍去建了连接")
        )
        assert L.get_online_bars("600000").empty
        assert "标记为不可用" in capsys.readouterr().err

    def test_index_short_circuits(self, monkeypatch):
        from custos.datasource.local_tdx import local_tdx_data as L

        self._fresh(monkeypatch)
        monkeypatch.setattr(
            L, "_get_client", lambda: pytest.fail("短路失败：仍去建了连接")
        )
        assert L.get_online_index("999999").empty

    def test_snapshot_short_circuits(self, monkeypatch):
        from custos.datasource.local_tdx import local_tdx_data as L

        self._fresh(monkeypatch)
        monkeypatch.setattr(
            L, "_get_client", lambda: pytest.fail("短路失败：仍去建了连接")
        )
        assert L.get_snapshot("600000") == {}

    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes"])
    def test_env_override_reenables(self, val, monkeypatch):
        """换了网络环境或服务端恢复时要能重新启用，而不是把能力删掉。"""
        from custos.datasource.local_tdx import local_tdx_data as L

        monkeypatch.setenv("TDX_ONLINE_QUOTES", val)
        assert L._online_quotes_enabled() is True

    def test_stock_list_not_affected(self, monkeypatch):
        """只关 bars/quotes 两族——`client.stocks()` 实测可用，不能一起关掉。"""
        import pandas as pd

        from custos.datasource.local_tdx import local_tdx_data as L

        self._fresh(monkeypatch)

        class _C:
            def stocks(self, market):
                return (
                    pd.DataFrame({"code": ["600000"]})
                    if market == 1
                    else pd.DataFrame()
                )

        # get_stock_list 已改走 `_with_client_retry`（会传 force_new 关键字），桩要吃下它
        monkeypatch.setattr(L, "_get_client", lambda force_new=False: _C())
        assert L.get_stock_list() == ["600000"]
