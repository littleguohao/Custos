# -*- coding: utf-8 -*-
"""股本事件索引 —— 因子层的**唯一**所有者。

2026-08-06 从 `research/backtest_factors.py` 移来。为什么必须移：

`factors/mcap.py` 需要它，而原先它在回测器里 ⇒ 因子层要**反向依赖**回测器。
更糟的是**同一文件被加载成两个模块**的老陷阱当场发作：
测试用 `from research import backtest_factors as bt` 打桩 `bt._SHARE_IDX`，
而 mcap 用扁平 `import backtest_factors` ⇒ 两个模块对象，patch 不互通、mcap 拿不到桩数据。
（见 `00_governance/data/DATA_SOURCE_PRINCIPLE.md`「模块级常量 + 运行时替换 = 陷阱」变体①。）

⇒ 移到因子层后：一个所有者、依赖方向朝下。

## ⚠️ 本模块必须用**包限定**导入：`from factors._shares import ...`

移过来之后陷阱换了个样子又发作一次：`07_tools` 与 `07_tools/factors` **都在 sys.path 上**，
所以同一文件有两条可导路径（`_shares` 与 `factors._shares`），Python 会建**两个模块对象**。
而本模块持有**可变的模块级缓存** `_SHARE_IDX` ⇒ 两个对象各存一份，
测试打桩其中一个、生产读另一个。

⇒ **规则：持有可变模块级状态的模块，一律包限定导入。**
无状态的纯函数模块（如 `_util`）用扁平 import 无妨。
"""
from __future__ import annotations

from typing import Optional

_SHARE_IDX: Optional[dict] = None


def shares_idx() -> dict:
    """股本事件索引 code → [(observed_on, total_shares)](01_data/fundamentals/share_changes.jsonl,
    东财 F10 全史回填,2018 前亦有;as-of 取值只可能 stale 不会 look-ahead)。加载失败 → {}。"""
    global _SHARE_IDX
    if _SHARE_IDX is None:
        idx: dict[str, list] = {}
        try:
            from paths import BASE  # noqa: PLC0415
            p = BASE / "01_data" / "fundamentals" / "share_changes.jsonl"
            if p.is_file():
                for line in p.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    e = json.loads(line)
                    if e.get("code") and e.get("observed_on") and e.get("total_shares"):
                        idx.setdefault(e["code"], []).append((e["observed_on"], e["total_shares"]))
                for c in idx:
                    idx[c].sort()
        except Exception:  # noqa: BLE001
            idx = {}
        _SHARE_IDX = idx
    return _SHARE_IDX


# 兼容别名：`backtest_factors` 与既有调用方仍用下划线名
_shares_idx = shares_idx
