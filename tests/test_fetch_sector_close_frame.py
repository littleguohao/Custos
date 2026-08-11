# -*- coding: utf-8 -*-
"""fetch_sector_index_history._to_close_frame / _suffixed 补充测试(真实 TQ 返回形态)。"""

import pandas as pd

from custos.datasource.local_tdx import fetch_sector_index_history as fsh


def test_field_keyed_shape_real_tq():
    # 实测形态:{Close: df(index=DatetimeIndex, 列='880001.SH')}
    df = pd.DataFrame(
        {"880001.SH": [10941.93, 11042.72, 10885.12]},
        index=pd.to_datetime(["2026-07-28", "2026-07-29", "2026-07-30"]),
    )
    out = fsh._to_close_frame({"Close": df}, "880001.SH")
    assert list(out["date"]) == ["2026-07-28", "2026-07-29", "2026-07-30"]
    assert list(out["close"]) == [10941.93, 11042.72, 10885.12]


def test_code_keyed_and_bare_df_still_work():
    df = pd.DataFrame(
        {"Close": [1.0, 2.0]}, index=pd.to_datetime(["2022-01-03", "2022-01-04"])
    )
    out = fsh._to_close_frame({"880201.SH": df}, "880201.SH")
    assert list(out["date"]) == ["2022-01-03", "2022-01-04"] and list(out["close"]) == [
        1.0,
        2.0,
    ]
    # RangeIndex 垃圾输入 → None(不得静默落盘)
    assert fsh._to_close_frame(pd.DataFrame({"Close": [1.0, 2.0]}), "X") is None
    assert fsh._to_close_frame({"X": None}, "X") is None


def test_suffixed():
    assert (
        fsh._suffixed("880001") == "880001.SH"
    )  # refresh_kline 必须带后缀(Codestr Error 教训)
    assert fsh._suffixed("880001.SH") == "880001.SH"
    assert fsh._suffixed("880201.sh") == "880201.SH"
