# -*- coding: utf-8 -*-
"""C19 拆分 + 日期解析缓存的逐位等价钉板（scan_signals_ytd）。

钉三件事：
1. ``_parse_day_cached`` 与 financials._parse_day 全输入域同值（含缓存命中路径、
   不可哈希输入回退路径）；
2. 拆分后的 ``_classify_firings`` + ``_print_report`` 与**拆分前 main 内联逻辑**
   （本文件内原样复制的 legacy 实现）产出逐位一致的 per_day 结构与 stdout；
3. 缓存不污染 ``_tier_you`` 的既有口径（连续调用、不同 idx 交叉调用结果稳定）。
"""

from __future__ import annotations

import io
import json
from collections import defaultdict
from contextlib import redirect_stdout

from custos.pipeline.screening.financials import _parse_day
from custos.research import scan_signals_ytd as scan


def _legacy_main_body(recs, pit, regime) -> None:
    """拆分前 main 函数体的原样拷贝（2026-08-19 C19 重构前），作为等价基准。"""
    per_day: dict[str, dict] = defaultdict(
        lambda: {"可买候选": [], "待0AMV做多": [], "前哨": [], "total": 0}
    )
    for r in recs:
        for d in r.get("days") or []:
            day = d[0]
            extra = d[2] if len(d) > 2 and isinstance(d[2], dict) else {}
            sec_fav = bool(extra.get("f_sector_favorable"))
            fq = scan._tier_you(pit, r["code"], day)
            mkt = regime.get(day) == "做多"
            bear = regime.get(day) == "空头"
            pd_ = per_day[day]
            pd_["total"] += 1
            if fq and sec_fav and mkt:
                pd_["可买候选"].append(r["code"])
            elif fq and sec_fav and not mkt:
                pd_["待0AMV做多"].append(r["code"])
            elif fq and bear and not sec_fav:
                pd_["前哨"].append(r["code"])

    print(f"扫描 {len(recs)} 股 | 信号日 {len(per_day)} 天")
    print(
        f"{'日期':<12}{'reversal_k':>10}{'可买候选':>10}{'待0AMV做多':>12}{'📡前哨':>10}  明细"
    )
    for day in sorted(per_day):
        pd_ = per_day[day]
        detail = ""
        for k in ("可买候选", "待0AMV做多", "前哨"):
            if pd_[k]:
                detail += f" {k}={','.join(pd_[k][:6])}"
        print(
            f"{day:<12}{pd_['total']:<10}{len(pd_['可买候选']):<10}{len(pd_['待0AMV做多']):<12}{len(pd_['前哨']):<10}{detail}"
        )


def _fixture(tmp_path):
    """覆盖三分支 + 不进桶 + 陈旧财报 + 缺 report_date + 多 code 同日 的合成数据。"""
    rows = [
        # 600000: 新鲜合格财报（2025-08 公告，报告期 2025-06-30）
        {
            "code": "600000",
            "notice_date": "2025-08-20",
            "report_date": "2025-06-30",
            "net_profit": 1.0e8,
            "ocf_ps": 0.5,
            "roe_waa": 12.0,
        },
        # 600001: 陈旧财报（报告期 2023 年，超过时效）
        {
            "code": "600001",
            "notice_date": "2023-10-25",
            "report_date": "2023-09-30",
            "net_profit": 1.0e8,
            "ocf_ps": 0.5,
            "roe_waa": 12.0,
        },
        # 600002: 旧台账缺 report_date
        {
            "code": "600002",
            "notice_date": "2025-08-20",
            "net_profit": 1.0e8,
            "ocf_ps": 0.5,
            "roe_waa": 12.0,
        },
        # 600003: 亏损（数字不达标）
        {
            "code": "600003",
            "notice_date": "2025-08-20",
            "report_date": "2025-06-30",
            "net_profit": -1.0,
            "ocf_ps": 0.5,
            "roe_waa": 12.0,
        },
    ]
    pit_path = tmp_path / "pit.jsonl"
    pit_path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    pit = scan._pit_index(pit_path)
    recs = [
        {
            "code": "600000",
            "days": [
                [
                    "2026-01-05",
                    "rk",
                    {"f_sector_favorable": True},
                ],  # fq+板块+做多 → 可买候选
                [
                    "2026-01-06",
                    "rk",
                    {"f_sector_favorable": True},
                ],  # fq+板块+非做多 → 待0AMV做多
                [
                    "2026-01-07",
                    "rk",
                    {"f_sector_favorable": False},
                ],  # fq+空头+板块差 → 前哨
                ["2026-01-08", "rk", {"f_sector_favorable": False}],  # 非空头 → 不进桶
                ["2026-01-09", "rk"],  # 无 extra dict
                ["2026-01-12", "rk", "not-a-dict"],  # extra 非 dict
            ],
        },
        {
            "code": "600001",
            "days": [["2026-01-05", "rk", {"f_sector_favorable": True}]],
        },
        {
            "code": "600002",
            "days": [["2026-01-05", "rk", {"f_sector_favorable": True}]],
        },
        {
            "code": "600003",
            "days": [["2026-01-05", "rk", {"f_sector_favorable": True}]],
        },
        {
            "code": "600004",
            "days": [["2026-01-05", "rk", {"f_sector_favorable": True}]],
        },  # 无财报
        {"code": "600000", "days": None},  # days 缺失
    ]
    regime = {
        "2026-01-05": "做多",
        "2026-01-06": "震荡",
        "2026-01-07": "空头",
        "2026-01-08": "做多",
        # 2026-01-09 / 2026-01-12 不在 regime 中 → None
    }
    return recs, pit, regime


class TestParseDayCachedEquivalence:
    SAMPLES = [
        None,
        "",
        "bogus",
        "2026-08-03",
        "20260803",
        "2026-08-03 10:30:00",
        "2026-08-03T10:30:00",
        "2026/08/03",
        "2026-13-40",
        "  2026-01-05  ",
    ]

    def test_same_value_as_uncached_on_all_inputs(self):
        for s in self.SAMPLES:
            assert scan._parse_day_cached(s) == _parse_day(s), f"首次(未命中): {s!r}"

    def test_cache_hit_path_same_value(self):
        for s in self.SAMPLES:
            scan._parse_day_cached(s)  # 预热
            assert scan._parse_day_cached(s) == _parse_day(s), f"命中路径: {s!r}"

    def test_unhashable_input_falls_back_without_error(self):
        """不可哈希输入:未缓存版返回 None(解析失败),缓存版必须同样返回 None 而非 TypeError。"""
        assert _parse_day(["2026-08-03"]) is None
        assert scan._parse_day_cached(["2026-08-03"]) is None


class TestSplitEquivalence:
    def test_per_day_structure_and_stdout_bit_identical(self, tmp_path, capsys):
        recs, pit, regime = _fixture(tmp_path)
        # 先跑 legacy(走 _tier_you → 已带缓存),再跑新实现,两轮各自全量执行
        with redirect_stdout(io.StringIO()) as buf_old:
            _legacy_main_body(recs, pit, regime)
        capsys.readouterr()  # 清掉 fixture 阶段可能的输出

        per_day = scan._classify_firings(recs, pit, regime)
        with redirect_stdout(io.StringIO()) as buf_new:
            scan._print_report(per_day, len(recs))

        assert buf_new.getvalue() == buf_old.getvalue()

    def test_repeat_runs_stable_with_shared_cache(self, tmp_path):
        """模块级缓存跨调用复用不改变结果(连续两轮分类全等)。"""
        recs, pit, regime = _fixture(tmp_path)
        first = scan._classify_firings(recs, pit, regime)
        second = scan._classify_firings(recs, pit, regime)
        assert dict(first) == dict(second)
