# -*- coding: utf-8 -*-
"""sector_daily_rank 离线测试 —— 全部合成 fixture（tmp_path 注入指数 CSV/成员/名称表/
资金流；个股日线经 ctx.read_daily 注入假数据），不碰真实 data/ 与 vipdoc。"""

from __future__ import annotations

import csv
import json
import pathlib

import pytest

from custos.core.paths import write_json_atomic
from custos.pipeline.market_timing import sector_daily_rank as sdr

D0, D1, D2 = "2026-08-14", "2026-08-17", "2026-08-18"  # 三个连续“交易日”

NAME_MAP = {
    "880001": {"name": "半导体", "tdx_type": "2"},
    "880002": {"name": "人工智能", "tdx_type": "4"},
    "880003": {"name": "江西板块", "tdx_type": "3"},  # 地区：默认不进宇宙
    "881001": {"name": "细分行业甲", "tdx_type": "12"},  # 细分：默认不进宇宙
}

MEMBERS = {
    "880001": ["600001", "600005"],  # 600005 是 ST
    "880002": ["600001", "920004"],  # 920004 北交所 30%
}
STOCK_NAMES = {"600001": "平安测试", "600005": "ST测试", "920004": "北证测试"}

# 板块指数收盘：D1 涨/跌分明；D2 反向（供活跃名单多次上榜用）
INDEX = {
    "880001": [(D0, 100.0), (D1, 105.0), (D2, 99.0)],
    "880002": [(D0, 100.0), (D1, 97.0), (D2, 103.0)],
    "880003": [(D0, 100.0), (D1, 110.0)],
    "881001": [(D0, 100.0), (D1, 120.0)],
}


def _write_index_csvs(index_dir):
    index_dir.mkdir(parents=True, exist_ok=True)
    for code, rows in INDEX.items():
        with open(index_dir / f"{code}.SH.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "close"])
            w.writerows(rows)


def make_ctx(tmp_path, bars, include_types=("2", "4"), members=None, stock_names=None):
    index_dir = tmp_path / "sector_index"
    _write_index_csvs(index_dir)
    mem = MEMBERS if members is None else members
    universe = sdr.discover_universe(index_dir, NAME_MAP, include_types)
    return sdr.Ctx(
        index_dir=index_dir,
        market_dir=tmp_path / "market",
        members=mem,
        code2secs=sdr.invert_members(mem, exclude_types=True, name_map=NAME_MAP),
        name_map=NAME_MAP,
        stock_names=STOCK_NAMES if stock_names is None else stock_names,
        universe=universe,
        include_types=include_types,
        read_daily=lambda code: bars.get(code, []),
    )


def bar(close_d0, close_d1, close_d2=None):
    rows = [(D0, close_d0), (D1, close_d1)]
    if close_d2 is not None:
        rows.append((D2, close_d2))
    return rows


# ---------------------------------------------------------------------------
# 榜本身
# ---------------------------------------------------------------------------


def test_gainers_and_losers_sorted(tmp_path):
    ctx = make_ctx(tmp_path, {})
    day = sdr.build_day(D1, ctx, top=10)
    assert day["date"] == D1
    assert [e["code"] for e in day["gainers_top"]] == ["880001", "880002"]
    assert [e["pct"] for e in day["gainers_top"]] == [5.0, -3.0]
    assert [e["code"] for e in day["losers_top"]] == [
        "880002",
        "880001",
    ]  # 跌幅最大在前
    g = day["gainers_top"][0]
    assert g["rank"] == 1 and g["name"] == "半导体" and g["tdx_type"] == "2"
    assert day["universe"] == {"sectors_total": 2, "types": ["2", "4"]}


def test_universe_type_filter(tmp_path):
    ctx = make_ctx(tmp_path, {})
    assert ctx.universe == ["880001", "880002"]  # 地区(3)/细分(12) 默认剔除
    ctx12 = make_ctx(tmp_path, {}, include_types=("2", "4", "12"))
    assert "881001" in ctx12.universe
    day = sdr.build_day(D1, ctx12, top=10)
    assert day["gainers_top"][0]["code"] == "881001"  # +20% 居首
    assert day["universe"]["types"] == ["12", "2", "4"]  # 字符串排序


def test_top_n_truncates(tmp_path):
    ctx = make_ctx(tmp_path, {})
    day = sdr.build_day(D1, ctx, top=1)
    assert len(day["gainers_top"]) == 1 and len(day["losers_top"]) == 1


def test_no_index_data_returns_none(tmp_path):
    ctx = make_ctx(tmp_path, {})
    assert sdr.build_day("2026-08-15", ctx) is None  # 周末：无板块有数据
    assert sdr.build_day(D0, ctx) is None  # 首日无前收，也算无数据


# ---------------------------------------------------------------------------
# 涨停/跌停家数
# ---------------------------------------------------------------------------


def test_limit_up_counts_exact_and_miss_by_one_fen(tmp_path):
    bars = {
        "600001": bar(10.00, 11.00),  # 主板 10%：刚好涨停价 → 计
        "600005": bar(10.00, 10.49),  # ST 5% 涨停价 10.50，差一分 → 不计
        "920004": bar(10.00, 12.99),  # BJ 30% 涨停价 13.00，差一分 → 不计
    }
    day = sdr.build_day(D1, make_ctx(tmp_path, bars))
    by_code = {e["code"]: e for e in day["gainers_top"] + day["losers_top"]}
    assert by_code["880001"]["limit_up_count"] == 1  # 只有 600001
    assert by_code["880002"]["limit_up_count"] == 1  # 600001 同属 880002
    assert by_code["880001"]["limit_down_count"] == 0


def test_limit_up_st_5pct_and_bj_30pct(tmp_path):
    bars = {
        "600001": bar(10.00, 10.50),  # 主板非 ST +5%：不是涨停
        "600005": bar(10.00, 10.50),  # ST 5%：刚好涨停 → 计
        "920004": bar(10.00, 13.00),  # BJ 30%：刚好涨停 → 计
    }
    day = sdr.build_day(D1, make_ctx(tmp_path, bars))
    by_code = {e["code"]: e for e in day["gainers_top"]}
    assert by_code["880001"]["limit_up_count"] == 1  # 600005（600001 +5% 不算）
    assert by_code["880002"]["limit_up_count"] == 1  # 920004


def test_limit_down_mirror(tmp_path):
    bars = {
        "600001": bar(10.00, 9.00),  # 主板 -10% 跌停
        "600005": bar(10.00, 9.50),  # ST -5% 跌停
        "920004": bar(10.00, 7.01),  # BJ 差一分未跌停
    }
    day = sdr.build_day(D1, make_ctx(tmp_path, bars))
    by_code = {e["code"]: e for e in day["gainers_top"]}
    assert by_code["880001"]["limit_down_count"] == 2  # 600001 + 600005
    assert by_code["880002"]["limit_down_count"] == 1  # 600001
    assert by_code["880001"]["limit_up_count"] == 0


def test_stock_counted_once_across_sectors(tmp_path):
    # 600001 同时属于 880001/880002，各计一次但每股只读一次盘
    calls = []
    bars = {"600001": bar(10.00, 11.00)}
    ctx = make_ctx(tmp_path, bars)
    orig = ctx.read_daily
    ctx.read_daily = lambda c: (calls.append(c), orig(c))[1]
    sdr.build_day(D1, ctx)
    assert calls.count("600001") == 1


def test_missing_stock_bars_recorded(tmp_path):
    day = sdr.build_day(D1, make_ctx(tmp_path, {}))  # 全部股票无数据
    assert sorted(day["data_quality"]["missing_stock_bars"]) == [
        "600001",
        "600005",
        "920004",
    ]


def test_suffixed_member_keys_normalized_for_limit_counts(tmp_path):
    """钉测（2026-09-04 涨跌停家数恒 0 bug）：sector_members.json 键带 .SH 后缀
    （``880001.SH``），而宇宙/members.get 用裸码 —— 不归一则 code2secs 与宇宙
    对不上、limit_counts 恒 0、missing_members 恒为全宇宙。_default_ctx 必须经
    normalize_member_keys 归一。"""
    suffixed = {
        "880001.SH": ["600001", "600005"],
        "880002.SH": ["600001", "920004"],
    }
    members = sdr.normalize_member_keys(suffixed)
    assert set(members) == {"880001", "880002"}  # 键归一为裸码
    bars = {"600001": bar(10.00, 11.00)}  # 主板 10% 涨停
    day = sdr.build_day(D1, make_ctx(tmp_path, bars, members=members))
    by_code = {e["code"]: e for e in day["gainers_top"]}
    assert by_code["880001"]["limit_up_count"] == 1  # 600001 计入（修复前恒 0）
    assert by_code["880002"]["limit_up_count"] == 1
    assert day["data_quality"]["missing_members"] == []  # 修复前恒为全宇宙


# ---------------------------------------------------------------------------
# 资金流
# ---------------------------------------------------------------------------


def _write_fund_flow(market_dir, date):
    market_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "sector_rank": {
            "concept": [{"name": "半导体", "main_net_inflow": 100.0}],
            "industry": [{"name": "半导体", "main_net_inflow": 23.0}],
        }
    }
    (market_dir / f"{date}_fund_flow_rank.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_fund_flow_aggregated_by_sector_name(tmp_path):
    ctx = make_ctx(tmp_path, {})
    _write_fund_flow(ctx.market_dir, D1)
    day = sdr.build_day(D1, ctx)
    by_code = {e["code"]: e for e in day["gainers_top"]}
    assert by_code["880001"]["main_net_inflow"] == 123.0  # concept+industry 同名合并
    assert by_code["880002"]["main_net_inflow"] is None  # 未命中 → null
    assert day["data_quality"]["fund_flow"] == "ok"


def test_fund_flow_missing_degrades_to_null(tmp_path):
    day = sdr.build_day(D1, make_ctx(tmp_path, {}))
    assert day["data_quality"]["fund_flow"] == "missing"
    assert all(
        e["main_net_inflow"] is None for e in day["gainers_top"] + day["losers_top"]
    )


# ---------------------------------------------------------------------------
# 回填 / 活跃名单 / 原子写
# ---------------------------------------------------------------------------


def _run_backfill(tmp_path, out_dir):
    bars = {"600001": bar(10.00, 11.00, 9.00)}
    ctx = make_ctx(tmp_path, bars)
    return sdr.run_dates(
        [D0, "2026-08-15", D1, D2], ctx, out_dir, top=10, window=40, min_hits=2
    )


def test_backfill_skips_no_data_days_and_is_idempotent(tmp_path):
    out_dir = tmp_path / "daily_rank"
    written, skipped = _run_backfill(tmp_path, out_dir)
    # D0 无前收（算不出涨跌幅）与 2026-08-15（周末）都跳过
    assert written == 2 and skipped == 2
    first = {
        p.name: json.loads(p.read_text(encoding="utf-8"))
        for p in out_dir.glob("*.json")
    }
    written2, skipped2 = _run_backfill(tmp_path, out_dir)
    assert (written2, skipped2) == (written, skipped)
    second = {
        p.name: json.loads(p.read_text(encoding="utf-8"))
        for p in out_dir.glob("*.json")
    }
    for name in first:  # 除 generated_at 外逐字节一致 → 幂等
        for payload in (first[name], second[name]):
            payload.pop("generated_at", None)
        assert first[name] == second[name]


def test_no_tmp_files_left(tmp_path):
    out_dir = tmp_path / "daily_rank"
    _run_backfill(tmp_path, out_dir)
    assert list(out_dir.glob("*.tmp")) == []
    assert (out_dir / f"{D1}.json").exists()


def test_active_list_min_hits_over_window(tmp_path):
    out_dir = tmp_path / "daily_rank"
    _run_backfill(tmp_path, out_dir)  # D0 跳空（首日无前收）→ 两板块都不上 D0 榜
    active = json.loads((out_dir / sdr.ACTIVE_FILE).read_text(encoding="utf-8"))
    assert active["window"] == 40 and active["min_hits"] == 2
    hits = {a["code"]: a for a in active["active"]}
    # D1、D2 两板块都在榜（涨/跌各一次）→ hits=2 ≥ 2
    assert set(hits) == {"880001", "880002"}
    assert hits["880001"]["hits"] == 2 and hits["880001"]["last_seen"] == D2
    assert hits["880001"]["name"] == "半导体"


def test_active_list_threshold_excludes_single_hit(tmp_path):
    out_dir = tmp_path / "daily_rank"
    out_dir.mkdir()
    # 只手造两天榜：880001 上两次，880002 只上一次
    for d, secs in [(D1, ["880001"]), (D2, ["880001", "880002"])]:
        payload = {
            "date": d,
            "gainers_top": [
                {
                    "code": c,
                    "name": NAME_MAP[c]["name"],
                    "tdx_type": NAME_MAP[c]["tdx_type"],
                }
                for c in secs
            ],
            "losers_top": [],
        }
        write_json_atomic(out_dir / f"{d}.json", payload)
    active = sdr.refresh_active(out_dir, D2, window=40, min_hits=2)
    assert [a["code"] for a in active["active"]] == ["880001"]
    assert active["files_used"] == 2


def test_active_list_respects_window(tmp_path):
    out_dir = tmp_path / "daily_rank"
    out_dir.mkdir()
    for i, d in enumerate(["2026-08-10", "2026-08-11", "2026-08-12"]):
        code = "880001" if i == 0 else "880002"
        write_json_atomic(
            out_dir / f"{d}.json",
            {
                "date": d,
                "gainers_top": [{"code": code, "name": code, "tdx_type": "2"}],
                "losers_top": [],
            },
        )
    # 窗口 2：最早的 880001 上榜被滚出窗口
    active = sdr.refresh_active(out_dir, "2026-08-12", window=2, min_hits=1)
    assert [a["code"] for a in active["active"]] == ["880002"]


def test_same_day_double_board_counts_once(tmp_path):
    out_dir = tmp_path / "daily_rank"
    out_dir.mkdir()
    write_json_atomic(
        out_dir / f"{D1}.json",
        {
            "date": D1,
            "gainers_top": [{"code": "880001", "name": "x", "tdx_type": "2"}],
            "losers_top": [{"code": "880001", "name": "x", "tdx_type": "2"}],
        },
    )
    active = sdr.refresh_active(out_dir, D1, window=40, min_hits=1)
    assert active["active"][0]["hits"] == 1


def test_cli_help_renders(capsys):
    with pytest.raises(SystemExit) as exc:
        sdr.main(["--help"])
    assert exc.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


class TestWiredIntoDailyChain:
    """采集器必须真的每天跑 —— 否则「主路径」还是停在设计上（owner 拍板接进每日链）。

    接在 17:00 链（`run_1700`）：final_close_review（§4 板块榜）是它的唯一读者，
    是 17:00 链的硬失败 stage。⚠️ 17:00 时点板块指数缓存只到昨日（唯一刷新方
    是 18:00 链），所以本链必须**先增量刷板块指数再跑采集器**，否则 build_day
    恒返回 None、产物永不落盘 —— 只接采集器不接刷新等于没接。
    """

    SRC = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "custos"
        / "pipeline"
        / "run_1700.py"
    ).read_text(encoding="utf-8")

    def test_stage_present(self):
        assert "sector_daily_rank.py" in self.SRC, "17:00 链未接入板块榜采集器"
        assert '"sector_daily_rank"' in self.SRC

    def test_index_refresh_wired_as_prerequisite(self):
        """采集器读 data/market/sector_index/*.csv —— 同链必须先有增量刷新 stage。"""
        i = self.SRC.index('"sector_daily_rank"')
        seg = self.SRC[max(0, i - 1500) : i]
        assert '"refresh_sector_index"' in seg, "缺前置板块指数刷新，采集器恒无当日数据"
        assert '"--incremental"' in seg, (
            "前置刷新必须是增量模式（全量会撞 600s stage 超时）"
        )

    def test_not_blocking(self):
        """best-effort：榜单挂了只告警，不该让整条盘后链失败（复盘还有缓存自算兜底）。"""
        i = self.SRC.index('"sector_daily_rank"')
        seg = self.SRC[i : i + 300]
        assert "不中断" in seg
        assert "return 1" not in seg, "采集器失败不该中断盘后链"
