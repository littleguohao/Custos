"""报告文本格式化 —— 把数值渲染成人读的文本，缺数时给**明确的占位符**。

为什么单独一个模块：`_number` 与 `pct_text` 各有两份实现，而 `pct_text` 的两份
**同名不同行为**，其中一份修过一个另一份没修的 bug（见下）。

⚠️ 刻意**不**把项目里全部十几个一次性格式化助手（`_fmt_num` / `_pct` / `num` / `pct` …）
都搬进来：它们的占位文本各随所在报告的行文（`待确认` / `缺失` / `unavailable`），
强行统一会改报告措辞而收益有限。这里只收敛**真正重复**的那几个，
占位文本做成参数而不是写死。
"""

from __future__ import annotations

import math


def _finite_or_none(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def num_text(value, digits: int = 2, missing: str = "待确认") -> str:
    """定点小数文本；缺失 / 非有限值渲染 `missing`。

    ⚠️ `close_review/holding_bbi.py` 与 `holding_structure.py` 里各有一份
    逐字相同的 `_number()`，**故意没有合并到这里**：那两个文件是不导入
    任何本地模块的纯库，为省 5 行重复而给它们加一层依赖，
    等于用一个新的耦合面换一点去重 —— 不划算。它们各自就地补了同样的有限性判定。
    """
    number = _finite_or_none(value)
    return missing if number is None else f"{number:.{digits}f}"


def pct_text(value, digits: int = 2, missing: str = "unavailable") -> str:
    """**带符号**百分比文本；缺失 / 非有限值渲染 `missing`。

    ⚠️ 这个函数是「同一逻辑两份实现、只有一份修过 bug」的实例。收敛前：

        final_close_review.pct_text  用 optional_finite ⇒ 缺数渲染 unavailable ✅
        review_core.pct_text         只判 `is None` ⇒ **NaN 渲染成 `+nan%`**、
                                     字符串直接抛 ValueError ⛔

    `final_close_review` 那份的 docstring 记着它修过什么：
    「此前用 finite(value)（缺失回落 0.0）直接格式化，指数涨跌缺数据会渲染成
    +0.00%，**把「不知道」伪装成「平盘」**」——这是最坏的一种错，
    因为读报告的人看不出区别。`review_core` 那份没跟上这个修复。

    `review_core` 的 5 个调用点都恰好在外层包了 `optional_finite`，所以没爆出来 ——
    但那意味着安全性依赖**每个调用者都记得包**，加第 6 个调用点就会漏。
    """
    number = _finite_or_none(value)
    return missing if number is None else f"{number:+.{digits}f}%"
