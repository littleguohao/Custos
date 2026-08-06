# -*- coding: utf-8 -*-
"""因子模块模板 —— 复制这个文件开始写一个新因子。

## 每个因子模块必须有的三样东西

    FACTOR      模块级元数据字典（下面逐字段说明）
    score()     `kind="selector"` 必须有：横截面排序用
    detect()    `kind="pattern"` / `"state"` 必须有：形态/状态识别用

两者都可以有（既能排序也能作 gate 的因子）。

## FACTOR 各字段

    id          注册名，与 `backtest_factors.SCORERS` 的键一致
    name        中文名，报表里用
    kind        selector  横截面排序（`score`）
                pattern   形态识别（`detect`）
                state     状态判定（`detect`）
                control   对照基线（不是真因子，用于证明信号本身有价值）
    status      **这是最要紧的一栏** ——
                active      已验证可用，允许进 live
                candidate   有迹象未终审，只能进研究
                needs_work  **按现有证据不可用，不得进 live**；但证据本身可能待重跑
                            （刻意不设 "falsified" 一档：不要随便证伪）
                untested    实现了但没跑过
    evidence    结论出处（`00_governance/research/RN_*.md`），needs_work 必填
    note        一句话说清死法或用法
    min_bars    最少需要多少根 K 线；不足时 score/detect 返回 None

⚠️ **`status` 在 `NOT_FOR_LIVE` 里的因子由测试强制不得进入 live 选股链。**
这是把 R2「价量选择器均未通过验证」这个结论**变成机器可执行的约束** ——
否则半年后有人看到 `alpha101` 就拿去用了，而文档里那条否决没人会重读。

## 返回约定

`score()` 返回 `None`（数据不足/不适用）或：

    {"score": float,          # 越大越优先（要反向就在因子内取负，别让调用方猜）
     "suggestion": str,        # "可买" / "观察" —— 纯选择器恒「可买」，靠 entry_filter 定池
     "aux": dict,              # 诊断字段，进报表
     "components": dict}       # 分项，用于归因

⚠️ **返回 None 与返回 score=0 语义不同**：None = 不参与排序（不误标），
0 = 参与排序但中性。混用会让「数据缺失」被算成「中性表现」。
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

FACTOR: dict[str, Any] = {
    "id": "template",
    "name": "模板（不要注册）",
    "kind": "selector",
    "status": "untested",
    "evidence": "",
    "note": "复制本文件开始写新因子；registry 会跳过 id=template",
    "min_bars": 1,
}


def score(df: pd.DataFrame, code: str) -> Optional[dict]:
    if len(df) < FACTOR["min_bars"]:
        return None
    return {"score": 0.0, "suggestion": "可买", "aux": {}, "components": {}}
