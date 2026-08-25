# -*- coding: utf-8 -*-
r"""Unified stock-code normalization helpers for strategy_team.

Consolidates the four divergent code-normalization implementations found in
src/custos/core/trades/incremental_ledger.py, src/custos/core/trades/standardize_trades.py,
src/custos/pipeline/market_timing/technical_monitor.py and
src/custos/pipeline/holdings/holding_sector_mapper.py.

Semantics are locked by tests/test_pipeline_kit.py.
"""

from __future__ import annotations

import math


def clean_code(v) -> str:
    """Ledger semantics: normalize a trade code to a 6-digit zero-padded string.

    Baseline: incremental_ledger.clean_code (verbatim). Known behavior
    differences vs the standardize_trades.clean_code variant:

    - ".0" handling: this version strips every ".0" occurrence globally
      (``str.replace('.0','')``), so e.g. "10.05" -> "105" -> "000105";
      standardize_trades only strips a single trailing ".0"
      ("10.05" stays "10.05").
    - zfill condition: this version applies zfill(6) unconditionally when the
      head segment is all digits; standardize_trades only pads when
      ``len(s) < 6`` (identical result for len >= 6, since zfill is a no-op).
    - empty input: ``None``/falsy values become "" here; standardize_trades
      would stringify None to "None".
    """
    s = str(v or "").strip().replace(".0", "")
    return s.split(".")[0].zfill(6) if s.split(".")[0].isdigit() else s.split(".")[0]


def price_limit_pct(code) -> float:
    """按代码前缀推断**日涨跌幅限制**（百分数）。全项目唯一来源。

    | 板块 | 前缀 | 限制 |
    |---|---|---|
    | 科创板（含 CDR） | 688 / **689** | 20% |
    | 创业板 | 300 / 301 | 20% |
    | 北交所 | 920 / 83 / 87 / 43 | **30%** |
    | 沪深主板 | 其余 | 10% |

    ⚠️ 2026-08-07 收敛 4 份实现时发现它们**对同一输入给出不一致的答案**：

        backtest_factors._limit_pct        北交所 → 30 ✅
        reconcile_qfq._limit_pct           北交所 → 30 ✅
        technical_monitor._infer_price_limit  北交所 → **20** ⛔（且只认 920，漏 83/87/43）
        s_shape 的 fallback                   北交所 → **20** ⛔

    北交所竞价交易的涨跌幅比例是 **30%**（上市首日不设限）。写 20 的那两份是
    **live 路径**：`technical_monitor` 的结果经 `price_limit / 2` 变成
    「中大阳/中大阴门槛」，BJ 股按 20% 算出 10% 门槛（应为 15%），于是

        10~15% 的涨/跌被误判成中大阳/中大阴
          → b1_holding_state 的 `two_bull_profit_take`（P2 分批止盈）
            与 `heavy_large_bear`（P1 减仓）**在不该触发时触发**

    而 `technical_monitor` 的数据自纠只能把 10 升到 20、**永远到不了 30**，
    所以这个偏差不会被历史波动纠正回来。

    ⚠️ **为什么不含 `88` 前缀**：北交所老代码里有 88xxxx，但通达信的板块指数是
    **880xxx 系列**（本项目用它算市场宽度）。把 `88` 算进北交所会让 880863
    这类指数被判 30% 限制。`launch_point_study.BOARDS` 含 `88` 是因为它只用于
    板块归类、不参与涨跌幅判定。需要区分指数时用 `is_index()`。
    """
    raw = str(code or "").strip().upper().split(".")[0]
    if raw.startswith(("688", "689", "300", "301")):
        return 20.0
    if raw.startswith(("920", "83", "87", "43")):
        return 30.0
    return 10.0


def bare_code(code) -> str:
    """去掉交易所后缀，返回裸代码：`"600000.SH"` → `"600000"`。

    ⚠️ **与 `clean_code()` 不是一回事，两个都要留**：

        bare_code("1")   → "1"        只切后缀，不补位
        clean_code("1")  → "000001"   台账语义：补足 6 位

    `bare_code` 的用途是**跨数据源对齐同一只票**（技术面表用裸码、
    chief_decision 用带后缀码，要能互相查到）。这里不能顺手改用 `clean_code`：
    补位是台账口径，对指数代码（`880863`）和潜在的非 6 位标识会改变结果，
    而这些调用点从没验证过补位是否安全。

    2026-08-07 从 6 份逐字相同的私有 `bare()` 收敛而来
    （close_review 三份 + generate_risk_and_sectors + chief_decision_report + rss_filter）。
    """
    return str(code or "").split(".")[0]


def is_index(code: str) -> bool:
    """代码是否为指数（而非可交易个股）。复权与 ST 判定都要靠它。

    指数**没有除权除息**，对它取权息数据是白费网络请求（880/881 系列光细分行业就有
    467 个），且拿不到数据后还要走一遍失败回退。所以复权前必须先排除指数。

    识别的代码段：
      · ``999999``          通达信上证指数
      · ``880xxx``/``881xxx``  通达信板块/细分行业统计指数
      · ``399xxx``          深证指数系列（399001 成指 / 399006 创业板指 …）
      · ``000688``/``000300``/``000905``/``000852``/``000016``/``000010``
                            沪市指数（注意与深市个股 000xxx 同形，故用白名单而非前缀）
      · ``899xxx``          北证指数（899050）
      · ``H`` / ``B`` 开头   通达信自定义指数

    ⚠️ 沪市指数与深市个股都是 ``000xxx``（000001 既是上证指数也是平安银行），
    无法靠前缀区分，所以这里只认常用沪指白名单 + ``.SH`` 后缀。带后缀时以后缀为准。
    """
    s = str(code).strip().upper()
    if not s:
        return False
    bare, suf = (s.rsplit(".", 1) + [""])[:2] if "." in s else (s, "")
    if bare[:1] in {"H", "B"} and not bare.isdigit():
        return True
    if not bare.isdigit():
        return False
    b = bare.zfill(6)
    if b == "999999" or b.startswith(("880", "881", "399", "899")):
        return True
    # 沪市指数白名单：与深市 000xxx 个股同形，必须靠后缀或白名单区分
    SH_INDEX = {
        "000001",
        "000010",
        "000016",
        "000300",
        "000688",
        "000852",
        "000903",
        "000905",
        "000906",
    }
    if b in SH_INDEX and suf == "SH":
        return True
    return False


# 沪市指数裸码白名单（单一定义处，local_tdx_data 的 SH 指数直读共用）。
# 沪市指数与深市 000xxx 个股同形（000001 既是上证指数也是平安银行）。上证指数在通达信
# 用 999999 表示，000001 按深市个股处理（与历史测试一致）；下列无歧义的常用沪市指数
# 强制 SH，否则会被 "0" 前缀误判成 SZ（2026-08-25：000688 科创50 被误读成深市个股
# sz000688，收盘 30.97/-8.64% 实际是国城矿业，不是科创50）。
# ⚠️ 碰撞：000688 同时也是深市个股国城矿业——白名单让**裸码** 000688 永远=科创50
# 指数，国城矿业必须显式带 .SZ 后缀（显式后缀优先级最高，不受影响）。
SH_INDEX_BARE = {
    "000010",
    "000016",
    "000300",
    "000688",
    "000852",
    "000903",
    "000905",
    "000906",
}


def market_of(code: str) -> str:
    """Single source of truth for exchange classification: "SH" | "SZ" | "BJ" | "".

    Rules (applied in order):

    - Explicit suffix wins: "xxx.BJ" -> "BJ", "xxx.SH"/"xxx.SZ" likewise, even
      when the bare code's prefix heuristic would disagree (e.g. 880005.SH is an
      SH statistics index, not a BJ stock).
    - Suffix-less digit codes are zero-padded to 6 digits, then:
      - "880" -> "SH" (通达信沪市统计指数系列，必须排在 "8" 前缀之前)
      - "920" / "8" / "4" -> "BJ" (北交所)
      - "6" / "5" / "9" -> "SH"
      - "0" / "1" / "2" / "3" -> "SZ"
    - Anything else -> "".
    """
    s = str(code).strip().upper()
    if "." in s:
        suf = s.rsplit(".", 1)[1]
        return suf if suf in {"SH", "SZ", "BJ"} else ""
    if not s.isdigit():
        return ""
    s = s.zfill(6)
    # 880/881 均为通达信板块指数,同属沪市。881xxx 是 tdxzs3.cfg 里的 467 个细分行业,
    # 若漏掉会落进下面的 "8" 前缀被判成北交所,读 bj881xxx.day 得到空数据且无告警(审计 B11)。
    if s.startswith(("880", "881")):
        return "SH"
    # 沪市指数白名单（定义见本模块 SH_INDEX_BARE，2026-08-25 科创50 误读国城矿业事故）。
    if s in SH_INDEX_BARE:
        return "SH"
    if s.startswith(("920", "8", "4")):
        return "BJ"
    if s.startswith(("6", "5", "9")):
        return "SH"
    if s.startswith(("0", "1", "2", "3")):
        return "SZ"
    return ""


def norm_code(code: str) -> str:
    """Market-data semantics: ensure a .SH/.SZ/.BJ suffix (technical_monitor version).

    Note: holding_sector_mapper.norm_code has different semantics (6-digit
    zero-padding, no exchange suffix) and intentionally stays in its original
    file; it is NOT merged here.
    """
    s = str(code).strip().upper()
    if s.endswith((".SH", ".SZ", ".BJ")):
        return s
    market = market_of(s)
    return f"{s}.{market}" if market else s


def split_code(tdx_code: str) -> tuple[str, str]:
    """Split a tdx code into (lowercase exchange prefix, bare code).

    Verbatim from technical_monitor.split_code; relies on norm_code.
    """
    s = norm_code(tdx_code)
    code, suf = s.split(".")
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suf, "")
    return prefix, code


def suffix(code: str) -> str:
    """Exchange suffix for a bare 6-digit code; delegates to market_of."""
    market = market_of(code)
    return f".{market}" if market else ""


def finite(v, d: float = 0.0) -> float:
    """转 float 并**保证结果有限**；失败 / NaN / ±inf 一律返回默认值 d。

    用于**参与计算**的场景。要区分「缺数」与「读数是 0」时用 `fnum()`。

    ⚠️ 2026-08-07 补上 `isinf`：此前只判 `isnan`，`float("inf")` 会原样返回。
    后果有两层 ——
      ① `json.dumps` 默认把它写成 `Infinity`，**这不是合法 JSON**（RFC 7159 不允许），
         严格解析器（JS `JSON.parse`、`allow_nan=False`）会拒收或崩；
      ② 它会污染一切下游算术，且不像 NaN 那样在比较中恒为 False——
         `inf > 阈值` 恒真，能把任意阈值判定骗过去。
    """
    try:
        x = float(v)
    except (TypeError, ValueError):
        return d
    return d if not math.isfinite(x) else x


def fnum(v) -> float | None:
    """转 float，失败返回 **None**。

    ⚠️ **与上面的 `finite()` 语义不同，两个都必须留**：

        finite(v, d=0.0)  失败/NaN → 返回默认值 d   —— 用于**参与计算**的场景
        fnum(v)           失败     → 返回 None      —— 用于**区分「缺数」与「读数是 0」**

    后者的必要性来自 `collect_incremental_market` 的一条教训：
    **`0.0` 是合法读数**（成交额为 0、涨跌幅为 0 都真实存在），
    所以判定必须用 `is not None` 而不能用真值判定 —— 用 `finite` 的 0.0 默认值
    会让「没取到数」和「取到 0」变得无法区分。

    2026-08-06 从 `collect_holding_quotes`（10 处调用）与
    `collect_incremental_market`（4 处）收敛而来。

    ⚠️ 2026-08-07 补上有限性判定：此前 NaN / ±inf **原样穿过**，而名字与
    `finite()` 这个兄弟都暗示它会拦。这不是理论风险，两个常见守卫都拦不住 NaN：

        _fnum(x) or 0.0          # bool(nan) 是 **True** ⇒ 得到 nan 而不是 0.0
        if v is None or v <= 0   # nan <= 0 是 **False** ⇒ NaN 当合法价格穿过

    pandas 的缺失值就是 NaN，采集层大量用它。NaN 落进 JSON 后，
    `allow_nan=False` 的写入方会直接崩，不带该参数的会写出非法 JSON。
    """
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None
