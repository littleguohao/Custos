#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R43 · 0AMV 区间段的板块与股票风格（描述性条件化研究，**不接交易引擎**）。

预注册 = `governance/research/R43_regime_sector_style.md`（v0.366）+ 修订注记 ×5
（v0.367）：判据/维度清单/段切分/零假设全部写死，本文件是它们的机械实现。
**判定段（2017~2021）的段集不读**（攒批协议未拍板，报告注明）——只有挖掘 A/B
两段进统计；本工具因此**不登记** window_usage（只读挖掘段，纪律见 README）。

## 内存账（TODO #93 定案①，写死防回退）

C4 的所有伪段起点用固定种子**在加载数据前**预抽（确定性），得到「需要 as-of
特征日」的全集 D（真实段起点 ∪ 全部伪段起点）；随后**逐股流式**处理：rolling
特征算完全序列、只保留 D∩该股日期 的行（float32），再按日期主序组织截面
（``{date: (codes, X)}``）。最坏情况 D ≈ 两挖掘窗全部交易日：3200 日 × 5500 票
× 26 维 × 4B ≈ 1GB 量级（外加每股留存的 dates/close/ret1 三数组 ~300MB，
供段收益与全市场等权收益序列用）。stderr 末尾打 ``[MEM]`` 峰值（复用
``backtest_factors.peak_rss_mb``）。

## 41 维可得性（记缺=该 (段,维) 不参与；整维缺源 ⇒ 该维所有段记缺 ⇒ C1 不过 untested）

- 纯行情 26 维恒可得（df 由 ``bt._load_bars_local`` 读入，qfq + holdout 截断内置）；
- **价格水平类 7 维改未复权收盘（v0.372，TODO #94 owner review 必修）**：
  ``price``/``mcap_float``/``mcap_total``/``pe_ttm``/``pb``/``ps_ttm``/``dy``
  的段起点取值用 ``raw_close``（qfq 前复权价含未来除权信息 = look-ahead）；
  取数辅助 ``raw = df["raw_close"] if "raw_close" in df.columns else df["close"]``
  （qfq 失败 adjust="none" 无 raw_close 列、测试注入帧也不带——fallback 时两者
  本就相等，口径不变）。其余收益/比率类维度维持 qfq——**免疫论证**：as-of 窗内
  的事后复权因子对窗内所有历史bar是公共乘子，收益/比值/均线相对位置不变，
  look-ahead 只污染价格**水平**（市值/估值/分红率都含价格水平）。
- **``turnover20`` 用还原原始量（v0.373，#94 追加 owner review）**：apply_qfq 把
  成交量 ÷ 因子，量水平同样含未来送转信息（10 送 10 后段起点换手率 1%→2%）；
  还原 ``raw_vol = volume × close / raw_close`` 再除流通股本。``vr20``/``amt_chg20``
  是量/额的窗内比值（公共乘子免疫），``amt20``/``amt_cv60``/``amihud20`` 用成交额
  （apply_qfq 不调成交额）——均不受影响。
- ``mcap_float``/``mcap_total``/``turnover20`` 依赖股本事件（xdxr 缓存，
  ``adjust_factors.get_shares_events``），单票缺事件 ⇒ 该票记缺；
- 基本面 10 维依赖 `data/fundamentals/pit_financials.jsonl`：文件缺失 ⇒ 整组记缺；
  其中 **``debt_ratio`` 恒记缺**（PIT 库 VALUE_FIELDS 无资产负债率字段）；
  ``dy`` 依赖 xdxr 分红记录（``load_xdxr_cache`` 的 fenhong，覆盖不可全得），
  单票缺缓存 ⇒ 该票记缺——生产机覆盖不全时记缺即可，**不 fail**；
- board 拆 4 个独立维（v0.372，修订注记 8）：``board_main``/``board_cyb``/
  ``board_kcb``/``board_bj``，段内统计量 = 该组等权超额 − 其余组等权超额
  （**signed，可为负**）——股票静态属性，不在特征矩阵；原「最高组−最低组」
  恒 ≥0 构造已废（C2 同号检验对它恒成立=失效）。价格族池 28→31。

用法（生产机）::

    uv run python -m custos.research regime_style_study \
        --out artifacts/logs/regime_style_study.json
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# GBK（cp936）终端/管道打不了 ⚠️/⛔ 等符号 —— 不 reconfigure 会 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from custos.core.code_utils import market_of, price_limit_pct  # noqa: E402
from custos.core.paths import LOGS  # noqa: E402
from custos.research import criteria_kit as kit  # noqa: E402
from custos.research import load_window as lw  # noqa: E402
from custos.research import universe as _universe  # noqa: E402

# ── 判据参数（预注册写死值=默认；CLI 覆盖是诊断通道，报告 params 块自述）──
MIN_SEG_DAYS = 5  # 段内交易日 < 5 丢弃
SUSP_BAR_FRAC = 0.8  # 段内有 bar 天数 < 段长×0.8 ⇒ 当段剔除（停牌伪抗跌）
MIN_DIM_STOCKS = 150  # 段起点截面有值票下限，不足 ⇒ 该段该维记缺
MIN_GROUP_N = 30  # 五分位每组 / ma_bull 两组 / board 各组下限
MIN_SEGS_PER_WINDOW = 8  # C1：每（窗×方向）合格段下限
N_RANDOM = 200  # C4 伪段抽样次数
C4_SEED = 20261014  # C4 种子（每（窗×方向）= seed + 窗序×1000 + 方向序）
C4_MIN_POOL = 20  # 池 < 20 ⇒ provisional（criteria_kit 小池护栏语义）
C2_SAME_SIGN_FRAC = 0.6  # C2：窗内与均值同号的段占比下限
PSEUDO_MAX_RETRY = 100  # 伪段放不下的重试上限（超出丢弃并记数）

DIRECTIONS = ("bear", "bull")  # 空头 / 多头
STATE_OF_DIR = {"bear": "空头", "bull": "做多"}
WINDOWS = {"A": lw.SEG_MINING_A, "B": lw.SEG_MINING_B}

#: 特征矩阵 37 列（board 是静态属性不在矩阵；顺序即报告列序）
MATRIX_DIMS = (
    # 规模/流动性（股本依赖前三项）
    "mcap_float",
    "mcap_total",
    "amt20",
    "turnover20",
    "amihud20",
    "amt_cv60",
    # 价格 / 波动与风险
    "price",
    "vol60",
    "downvol60",
    "beta250",
    "ivol60",
    "mdd60",
    # 动量与反转 / 趋势位置
    "ret5",
    "ret20",
    "ret60",
    "ret120",
    "ret250",
    "dist_high250",
    "dist_low250",
    "bias60",
    "bias250",
    "ma_bull",
    # 量能 / 题材活跃度 / 上市属性
    "vr20",
    "amt_chg20",
    "limitup60",
    "bigup60",
    "age",
    # 基本面 10 维（PIT；debt_ratio 恒记缺）
    "pe_ttm",
    "pb",
    "ps_ttm",
    "roe_ttm",
    "gross_margin",
    "debt_ratio",
    "cfo_to_np",
    "rev_yoy",
    "np_yoy",
    "dy",
)
#: board 拆 4 个独立维（v0.372，修订注记 8）：静态属性，统计量 = 该组等权超额
#: − 其余组等权超额（signed）；北交所早段常态空 ⇒ 记缺路径（组 < min_group）。
BOARD_DIMS = ("board_main", "board_cyb", "board_kcb", "board_bj")
BOARD_DIM_GROUP = {
    "board_main": "主板",
    "board_cyb": "创业板",
    "board_kcb": "科创板",
    "board_bj": "北交所",
}

PRICE_FAMILY = (
    tuple(d for d in MATRIX_DIMS[:27]) + BOARD_DIMS
)  # 31（max-of-31，v0.372）
FUND_FAMILY = MATRIX_DIMS[27:]  # 10（max-of-10）
ALL_DIMS = tuple(PRICE_FAMILY) + tuple(FUND_FAMILY)  # 41
FAMILY_OF = {d: ("price" if d in PRICE_FAMILY else "fund") for d in ALL_DIMS}
NEG_GROUP_DIMS = ("pe_ttm", "pb", "ps_ttm")  # 负值单列一组、价差剔除

#: 板块类型字段（tdxzs3.cfg 第 3 字段，经 tq_sector.load_sector_names 读入）→ 报告组；
#: "3"（地区）按预注册排除记数，缺/其他 ⇒ 未知组。
SECTOR_TYPE_GROUP = {
    "2": "行业",
    "12": "行业",
    "4": "概念",
    "5": "风格",
}


# ---------------------------------------------------------------------------
# 区间段切分（regime 单源 = backtest_factors.load_amv_regime 的逐日 dict）
# ---------------------------------------------------------------------------


def split_regime_segments(regime: dict[str, str]) -> list[dict[str, Any]]:
    """逐日 regime dict → 区间段清单（连续同状态交易日=一段）。

    段起点=状态翻转当日，段终点=下一次翻转前一交易日；**首段丢弃**（台账起点
    状态未知）。只保留空头/做多两段方向；<MIN_SEG_DAYS 交易日的段丢弃由
    ``assign_windows`` 完成（本函数只做切分+首段丢弃，便于钉测）。
    """
    dates = sorted(d for d, v in regime.items() if v in ("空头", "做多", "中性"))
    if not dates:
        return []
    segs: list[dict[str, Any]] = []
    cur_state = regime[dates[0]]
    cur = [dates[0]]
    for d in dates[1:]:
        st = regime[d]
        if st == cur_state:
            cur.append(d)
        else:
            segs.append({"state": cur_state, "days": cur})
            cur_state, cur = st, [d]
    segs.append({"state": cur_state, "days": cur})
    # 首段丢弃（状态未知起点）
    segs = segs[1:]
    out = []
    for s in segs:
        if s["state"] not in ("空头", "做多"):
            continue
        out.append(
            {
                "dir": "bear" if s["state"] == "空头" else "bull",
                "start": s["days"][0],
                "end": s["days"][-1],
                "days": list(s["days"]),
                "n_days": len(s["days"]),
            }
        )
    return out


def assign_windows(
    segs: list[dict[str, Any]],
    *,
    min_seg_days: int = MIN_SEG_DAYS,
) -> tuple[dict[str, dict[str, list]], dict[str, int]]:
    """段分进挖掘 A/B：须**完整落在某段内**（跨段丢弃不切断）、段长 ≥ min_seg_days。

    判定段（2017~2021）内的段天然落不进 A/B ⇒ 不读（攒批协议未拍板，报告注明）。
    返回 ({"A": {"bear": [...], "bull": [...]}, "B": ...}, drops 计数)。
    """
    kept: dict[str, dict[str, list]] = {w: {d: [] for d in DIRECTIONS} for w in WINDOWS}
    drops = {"short": 0, "crossing": 0}
    for s in segs:
        if s["n_days"] < min_seg_days:
            drops["short"] += 1
            continue
        placed = False
        for w, (ws, we) in WINDOWS.items():
            if ws <= s["start"] and s["end"] <= we:
                s2 = dict(s)
                s2["window"] = w
                kept[w][s2["dir"]].append(s2)
                placed = True
                break
        if not placed:
            drops["crossing"] += 1  # 跨段/落在判定段/holdout 侧的一律不读
    return kept, drops


# ---------------------------------------------------------------------------
# C4 伪段（修订注记 2：per（窗×方向）成池，两族共用同组伪段）
# ---------------------------------------------------------------------------


def draw_pseudo_placements(
    real_segs: list[dict[str, Any]],
    win_days: list[str],
    *,
    n_draws: int,
    seed: int,
    max_retry: int = PSEUDO_MAX_RETRY,
) -> dict[str, Any]:
    """伪段放置：段长有放回采自真实段长，起点在窗内交易日历上均匀、完整落窗内。

    每次抽样 = 放置 len(real_segs) 个伪段（保持 t 统计量的 n 与真实一致）；
    单个伪段 max_retry 次放不下 ⇒ 丢弃并记数。返回 {"draws": [[seg...]...],
    "n_dropped": int}；seg = {"start","end","days","n_days"}（dir=真实方向，仅标记用）。
    """
    rng = random.Random(seed)
    days = list(win_days)
    n_days = len(days)
    lens = [s["n_days"] for s in real_segs]
    draws: list[list[dict[str, Any]]] = []
    n_dropped = 0
    for _ in range(n_draws):
        placed: list[dict[str, Any]] = []
        for _j in range(len(real_segs)):
            ok = False
            for _r in range(max_retry):
                ln = rng.choice(lens)
                if ln > n_days:
                    continue
                i0 = rng.randrange(0, n_days - ln + 1)
                sub = days[i0 : i0 + ln]
                placed.append(
                    {
                        "dir": real_segs[0]["dir"] if real_segs else "bear",
                        "start": sub[0],
                        "end": sub[-1],
                        "days": sub,
                        "n_days": ln,
                    }
                )
                ok = True
                break
            if not ok:
                n_dropped += 1
        draws.append(placed)
    return {"draws": draws, "n_dropped": n_dropped}


# ---------------------------------------------------------------------------
# vipdoc 880/881 板块指数（reader 件复用 data_coverage_probe，不重写格式）
# ---------------------------------------------------------------------------


def read_vipdoc_close_series(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """读 .day 文件 → (date_int 升序数组, close 元)；date_int==0 跳过、尾部残段
    自动截断（numpy fromfile）、holdout 帽截断（≥2026-09-05 不读）。"""
    from custos.research import data_coverage_probe as dcp  # noqa: PLC0415

    arr = np.fromfile(path, dtype=dcp._DAY_DTYPE)
    if not arr.size:
        return np.empty(0, dtype=np.uint32), np.empty(0, dtype=np.float64)
    cap_int = int(dcp.HOLDOUT_CAP.replace("-", ""))
    keep = (arr["date"] != 0) & (arr["date"] <= cap_int)
    dates = arr["date"][keep]
    close = arr["close"][keep].astype(np.float64) / 100.0
    order = np.argsort(dates, kind="stable")
    return dates[order], close[order]


def sector_type_groups(types: dict[str, str]) -> tuple[dict[str, str], int]:
    """{code: tdx_type} → ({code: 报告组}, n_excluded)。type "3"（地区）排除记数。"""
    groups: dict[str, str] = {}
    n_excluded = 0
    for code, t in types.items():
        if t == "3":
            n_excluded += 1
            continue
        groups[code] = SECTOR_TYPE_GROUP.get(str(t), "未知")
    return groups, n_excluded


# ---------------------------------------------------------------------------
# 特征（段起点 as-of；全部按交易日 rolling，最后只取 D∩该股日期 的行）
# ---------------------------------------------------------------------------


def _shares_series(
    dates: pd.Series, events: Optional[list[dict]], field: str
) -> pd.Series:
    """股本事件（[{date, total_shares, float_shares}]）→ 逐交易日 as-of 股本序列
    （不晚于当日的最后一条；无事件 ⇒ 全 NaN ⇒ 依赖维度记缺）。"""
    out = pd.Series(np.nan, index=dates.index, dtype="float64")
    if not events:
        return out
    pts = sorted(
        ((str(e.get("date"))[:10], e.get(field)) for e in events),
        key=lambda x: x[0],
    )
    pts = [(d, float(v)) for d, v in pts if v]
    if not pts:
        return out
    pdates = [d for d, _ in pts]
    pvals = [v for _, v in pts]
    ds = dates.astype(str).str[:10]
    idx = np.searchsorted(pdates, ds.to_numpy(), side="right") - 1
    vals = np.where(idx >= 0, np.array(pvals)[np.maximum(idx, 0)], np.nan)
    return pd.Series(vals, index=dates.index)


def _fundamental_frame(
    code: str,
    dates: pd.Series,
    close_raw: pd.Series,
    total_shares: pd.Series,
    pit_table: Optional[dict[str, Any]],
    xdxr_events: Optional[list[dict]],
) -> pd.DataFrame:
    """基本面 10 维（PIT 可见性=公告日 < 当日，即公告次一交易日起可见）。

    pit_table = per-code 预处理 {records, notices, rds}（见 _pit_tables；
    可见性查询 = 逐日二分，不建全量索引——D 上万日期 × 全宇宙的 dict 是内存坑）；
    None ⇒ 整组记缺（全 NaN）。debt_ratio 恒 NaN（PIT 库无字段）。
    dy = 过去 12 个月 fenhong 合计 / 收盘（xdxr 缓存缺 ⇒ NaN）。
    ``close_raw``（v0.372 TODO #94）：**未复权**收盘——pe/pb/ps 的市值与 dy
    的分母都是价格水平，qfq 含未来除权 = look-ahead。
    """
    n = len(dates)
    cols = {d: np.full(n, np.nan) for d in FUND_FAMILY}
    if pit_table is None and not xdxr_events:
        return pd.DataFrame(cols)
    recs = pit_table["records"] if pit_table else {}
    notices = pit_table["notices"] if pit_table else []
    rds_sorted = pit_table["rds"] if pit_table else []
    ds = list(dates.astype(str).str[:10])
    closes = close_raw.to_numpy(dtype="float64")
    shares = total_shares.to_numpy(dtype="float64")
    dy_events: list[tuple[str, float]] = []
    if xdxr_events:
        dy_events = sorted(
            (
                (str(e.get("date"))[:10], float(e.get("fenhong") or 0.0))
                for e in xdxr_events
                if e.get("fenhong")
            ),
            key=lambda x: x[0],
        )
        dy_dates = [d for d, _ in dy_events]
        dy_vals = np.array([v for _, v in dy_events], dtype="float64")
        dy_cum = np.cumsum(dy_vals) if len(dy_vals) else dy_vals
    else:
        dy_dates = []
        dy_cum = np.empty(0)

    def _val(rd: str, field: str) -> Optional[float]:
        r = recs.get(rd)
        v = r.get(field) if r else None
        return None if v is None else float(v)

    def _ttm(rd0: str, field: str) -> Optional[float]:
        """TTM = 本期累计 + 上年全年 − 上年同期累计（三期缺一 ⇒ None）。"""
        try:
            y, mmdd = int(rd0[:4]), rd0[5:]
            rd_fy = f"{y - 1}-12-31"
            rd_py = f"{y - 1}-{mmdd}"
        except (ValueError, IndexError):
            return None
        a, b, c = _val(rd0, field), _val(rd_fy, field), _val(rd_py, field)
        if a is None or b is None or c is None:
            return None
        return a + b - c

    for i, d in enumerate(ds):
        # 可见 = 公告日 < 当日（公告次一交易日起可用，同 fetch_pit_financials）
        j = bisect.bisect_left(notices, d) - 1
        rd0 = max(rds_sorted[: j + 1]) if j >= 0 else None
        if rd0 is not None:
            px = closes[i]
            sh = shares[i]
            mcap = px * sh if not (math.isnan(px) or math.isnan(sh)) else math.nan
            np_ttm = _ttm(rd0, "net_profit")
            rev_ttm = _ttm(rd0, "revenue")
            roe_ttm = _ttm(rd0, "roe_waa")  # 累计值可加近似（报告注明）
            ocf_ttm = _ttm(rd0, "ocf_ps")
            bps = _val(rd0, "bps")
            gm = _val(rd0, "gross_margin")
            rev0, rev_py = _val(rd0, "revenue"), None
            try:
                rev_py = _val(f"{int(rd0[:4]) - 1}-{rd0[5:]}", "revenue")
                np_py = _val(f"{int(rd0[:4]) - 1}-{rd0[5:]}", "net_profit")
            except (ValueError, IndexError):
                np_py = None
            np0 = _val(rd0, "net_profit")
            if np_ttm is not None and not math.isnan(mcap) and np_ttm != 0:
                cols["pe_ttm"][i] = mcap / np_ttm
            if bps is not None and bps != 0:
                cols["pb"][i] = px / bps
            if rev_ttm is not None and not math.isnan(mcap) and rev_ttm != 0:
                cols["ps_ttm"][i] = mcap / rev_ttm
            if roe_ttm is not None:
                cols["roe_ttm"][i] = roe_ttm
            if gm is not None:
                cols["gross_margin"][i] = gm
            if (
                ocf_ttm is not None
                and np_ttm is not None
                and np_ttm != 0
                and not math.isnan(sh)
            ):
                cols["cfo_to_np"][i] = (ocf_ttm * sh) / np_ttm
            if rev0 is not None and rev_py is not None and rev_py != 0:
                cols["rev_yoy"][i] = rev0 / rev_py - 1.0
            if np0 is not None and np_py is not None and np_py != 0:
                cols["np_yoy"][i] = np0 / np_py - 1.0
        # dy：过去 12 个月 fenhong 合计 / 收盘（与报告期可见性无关，按除息日计）
        if dy_dates and not math.isnan(closes[i]):
            j_hi = bisect.bisect_right(dy_dates, d)
            j_lo = bisect.bisect_right(dy_dates, f"{int(d[:4]) - 1}-{d[5:]}")
            if j_hi > j_lo:
                cols["dy"][i] = (
                    dy_cum[j_hi - 1] - (dy_cum[j_lo - 1] if j_lo else 0.0)
                ) / closes[i]
    return pd.DataFrame(cols)


def _pit_tables(
    pit_records: Optional[list[dict]], dates_all: list[str]
) -> Optional[dict[str, dict[str, Any]]]:
    """PIT 记录 → per-code {records, notices, rds}（notices/rds 按 (notice, rd)
    升序，可见性 = 逐日二分查（公告日 < 当日，同 fetch_pit_financials 的
    visible_next_day 语义）——不建全量 {日期: rd} 索引：D×全宇宙的 dict 是
    内存坑。同报告期多版本取公告日最新一条。文件缺失/空 ⇒ None（整组记缺）。
    ``dates_all`` 形参保留兼容（初版 vis_index 预构已随内存账移除）。
    """
    if not pit_records:
        return None
    by_code: dict[str, dict[str, dict]] = {}
    for r in pit_records:
        nd = str(r.get("notice_date") or "")[:10]
        rd = str(r.get("report_date") or "")[:10]
        if not nd or not rd:
            continue
        c = by_code.setdefault(str(r.get("code"))[:6], {})
        cur = c.get(rd)
        if cur is None or nd > str(cur.get("notice_date"))[:10]:
            c[rd] = r  # 同报告期取公告日最新版本
    out: dict[str, dict[str, Any]] = {}
    for code, recmap in by_code.items():
        items = sorted(
            ((str(r.get("notice_date"))[:10], rd) for rd, r in recmap.items()),
            key=lambda x: (x[0], x[1]),
        )
        out[code] = {
            "records": recmap,
            "notices": [x[0] for x in items],
            "rds": [x[1] for x in items],
        }
    return out


def _own_feature_frame(
    code: str,
    df: pd.DataFrame,
    *,
    shares_events: Optional[list[dict]],
    pit_table: Optional[dict[str, Any]],
    xdxr_events: Optional[list[dict]],
    limit_pct: float,
) -> pd.DataFrame:
    """单票全序列 as-of 特征（37 列；beta250/ivol60 留 NaN 由第二遍填）。

    价格水平类 7 维用**未复权** ``raw_close``（qfq 含未来除权 = look-ahead，
    v0.372 TODO #94）；``turnover20`` 用**还原原始量**（qfq 量 = 原始量 ÷ 因子，
    同为水平量被污染，v0.373 #94 追加：raw_vol = volume × close / raw_close）。
    qfq 失败/注入帧无 raw_close 列 ⇒ 回退 close（两者本就相等）。
    """
    dts = df["date"].astype(str).str[:10].reset_index(drop=True)
    close = df["close"].astype("float64").reset_index(drop=True)
    raw_close = (
        df["raw_close"].astype("float64").reset_index(drop=True)
        if "raw_close" in df.columns
        else close
    )
    amount = df["amount"].astype("float64").reset_index(drop=True)
    volume = df["volume"].astype("float64").reset_index(drop=True)
    # 还原未复权成交量：f = close_qfq / raw_close，volume_raw = volume_qfq × f
    # （apply_qfq：价 × f、量 ÷ f）。raw_close ≤ 0 ⇒ NaN ⇒ 记缺。
    raw_vol = volume * close / raw_close.where(raw_close > 0)
    r1 = close.pct_change()
    total_sh = _shares_series(dts, shares_events, "total_shares")
    float_sh = _shares_series(dts, shares_events, "float_shares")
    ma5, ma10, ma20 = (close.rolling(w).mean() for w in (5, 10, 20))
    ma60, ma250 = close.rolling(60).mean(), close.rolling(250).mean()
    amt20 = amount.rolling(20).mean()
    neg = r1.where(r1 < 0, 0.0)
    lim = limit_pct / 100.0 - 0.001  # 容差：收盘涨幅 ≥ 限制−0.1pp 记涨停
    cols: dict[str, Any] = {
        "mcap_float": raw_close * float_sh,
        "mcap_total": raw_close * total_sh,
        "amt20": amt20,
        "turnover20": (raw_vol / float_sh).rolling(20).mean(),
        "amihud20": (r1.abs() / amount.where(amount > 0)).rolling(20).mean(),
        "amt_cv60": amount.rolling(60).std() / amount.rolling(60).mean(),
        "price": raw_close,
        "vol60": r1.rolling(60).std(),
        "downvol60": (neg * neg).rolling(60).mean() ** 0.5,
        "beta250": np.nan,  # 第二遍（需全市场等权收益序列）
        "ivol60": np.nan,
        "mdd60": (close / close.rolling(60).max() - 1.0).rolling(60).min(),
        "ret5": close.pct_change(5),
        "ret20": close.pct_change(20),
        "ret60": close.pct_change(60),
        "ret120": close.pct_change(120),
        "ret250": close.pct_change(250),
        "dist_high250": close / close.rolling(250).max() - 1.0,
        "dist_low250": close / close.rolling(250).min() - 1.0,
        "bias60": close / ma60 - 1.0,
        "bias250": close / ma250 - 1.0,
        "ma_bull": ((ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)).astype("float64"),
        "vr20": volume.rolling(5).mean() / volume.rolling(20).mean(),
        "amt_chg20": amt20 / amt20.shift(20) - 1.0,
        "limitup60": (r1 >= lim).rolling(60).sum(),
        "bigup60": (r1 >= 0.05).rolling(60).sum(),
        "age": pd.Series(np.arange(1, len(dts) + 1), dtype="float64"),
    }
    out = pd.DataFrame(cols)
    # 基本面 10 维的价格口径 = raw_close（pe/pb/ps 的市值与 dy 的分母都是价格水平）
    fund = _fundamental_frame(code, dts, raw_close, total_sh, pit_table, xdxr_events)
    for c in FUND_FAMILY:
        out[c] = fund[c].to_numpy() if c in fund else np.nan
    out.insert(0, "date", dts)
    return out[["date", *MATRIX_DIMS]]


def _roll_cov_stats(
    r: np.ndarray, m: np.ndarray, w: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """前缀和向量化滚动 (var_r, var_m, cov, 窗口完整掩码)（ddof=1；窗口含 NaN ⇒ 掩码 False）。

    返回数组对齐**窗口末根**（out[i] = 窗口 [i-w+1, i] 的统计量，i < w-1 处无意义）。
    """
    ok = ~(np.isnan(r) | np.isnan(m))
    rn = np.where(ok, r, 0.0)
    mn = np.where(ok, m, 0.0)

    def _cs(x: np.ndarray) -> np.ndarray:
        return np.concatenate([[0.0], np.cumsum(x)])

    sr, sm = _cs(rn), _cs(mn)
    srr, smm, srm = _cs(rn * rn), _cs(mn * mn), _cs(rn * mn)
    sok = _cs(ok.astype("float64"))
    n = sok[w:] - sok[:-w]
    wr = sr[w:] - sr[:-w]
    wm = sm[w:] - sm[:-w]
    # 二阶矩：E[x²]−E[x]²（ddof=1 ⇒ /(w−1)）
    var_r = (srr[w:] - srr[:-w] - wr * wr / w) / (w - 1)
    var_m = (smm[w:] - smm[:-w] - wm * wm / w) / (w - 1)
    cov = (srm[w:] - srm[:-w] - wr * wm / w) / (w - 1)
    return var_r, var_m, cov, n == w


def _fill_beta_ivol(
    dates: list[str], ret1: np.ndarray, mkt_ret: dict[str, float], need: set[str]
) -> tuple[dict[str, float], dict[str, float]]:
    """第二遍：250 日 beta / 60 日残差波动（对全市场等权日收益），只回传 need 日。

    beta = cov250(r, mkt)/var250(mkt)；ivol60 = sqrt(max(var60(r) − cov60²/var60(m), 0))。
    窗口不完整（含首根 NaN 收益）⇒ 该日记缺。
    """
    m = np.array([mkt_ret.get(d, np.nan) for d in dates], dtype="float64")
    r = np.asarray(ret1, dtype="float64")
    var_r250, var_m250, cov250, full250 = _roll_cov_stats(r, m, 250)
    var_r60, var_m60, cov60, full60 = _roll_cov_stats(r, m, 60)
    beta_out: dict[str, float] = {}
    ivol_out: dict[str, float] = {}
    for i, d in enumerate(dates):
        if d not in need:
            continue
        if i >= 249 and full250[i - 249] and var_m250[i - 249] > 0:
            beta_out[d] = float(cov250[i - 249] / var_m250[i - 249])
        if i >= 59 and full60[i - 59] and var_m60[i - 59] > 0:
            resid = var_r60[i - 59] - cov60[i - 59] ** 2 / var_m60[i - 59]
            ivol_out[d] = math.sqrt(max(float(resid), 0.0))
    return beta_out, ivol_out


# ---------------------------------------------------------------------------
# 段评估（截面五分位价差；真实段/伪段共用同一函数——零假设同规则）
# ---------------------------------------------------------------------------


def _spearman(a: list[float], b: list[float]) -> Optional[float]:
    """秩相关（无 scipy：rank 后 Pearson）；n<3 或零方差 ⇒ None。"""
    if len(a) < 3 or len(b) < 3 or len(a) != len(b):
        return None
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    if float(np.std(ra)) == 0 or float(np.std(rb)) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _t_stat(spreads: list[float]) -> Optional[float]:
    """段均价差 / 段间标准误（se=std/√n）；n<2 或 std==0 ⇒ None（记缺）。"""
    n = len(spreads)
    if n < 2:
        return None
    sd = statistics.stdev(spreads)
    if sd == 0:
        return None
    return statistics.mean(spreads) / (sd / math.sqrt(n))


def eval_segment(
    seg: dict[str, Any],
    ctx: dict[str, Any],
    *,
    min_stocks: int = MIN_DIM_STOCKS,
    min_group: int = MIN_GROUP_N,
    early_days: tuple[int, ...] = (),
) -> Optional[dict[str, Any]]:
    """评估一个段：成员过滤（起点有 bar + 80% 成交天数）→ 超额收益 → 逐维价差。

    返回 None = 段不合格（成员 < min_stocks）；否则 {"spreads": {dim: v|None},
    "neg_groups": {...}, "susp_by_group": {...}, "n_members", "frac_pos",
    "early": {k: {dim: (spread_early, spread_rest)}}, drops 计数}。
    """
    stocks = ctx["stocks"]
    blocks = ctx["blocks"]
    s, e = seg["start"], seg["end"]
    block = blocks.get(s)
    if block is None:
        return None  # 段起点不在 D（伪段起点预抽已保证在——真实段起点必在）
    codes_s, X = block
    # 修订注记 1 计数：特征行只建在「当日有 bar」的票上 ⇒ 不在 block 里的票即
    # 段起点无 bar 票（block 的 codes 必 ⊆ stocks，差额=当段该规则剔除数）
    ctx["drops"]["no_start_bar"] += len(stocks) - len(codes_s)
    members = []
    for pos, code in enumerate(codes_s):
        st = stocks.get(code)
        if st is None:
            continue
        i0 = bisect.bisect_left(st["dates"], s)
        age = (
            bisect.bisect_right(ctx["vipdoc_dates"][code], s)
            if ctx["vipdoc_dates"] is not None and code in ctx["vipdoc_dates"]
            else i0 + 1
        )
        if age < 60:  # 段起点已满 60 根（as-of 资格，与 universe 同源）
            continue
        j1 = bisect.bisect_left(st["dates"], s)
        j2 = bisect.bisect_right(st["dates"], e)
        n_in = j2 - j1
        if n_in < seg["n_days"] * ctx["susp_bar_frac"]:
            ctx["drops"]["low_bars"] += 1
            continue  # 段内成交 < 80% 段长 ⇒ 剔除
        ret = float(st["close"][j2 - 1] / st["close"][j1] - 1.0)
        susp = 1.0 - n_in / max(seg["n_days"], 1)
        members.append((pos, code, ret, susp, j1, j2))
    if len(members) < min_stocks:
        return None
    rets = np.array([m[2] for m in members], dtype="float64")
    base = float(rets.mean())  # 当段合格宇宙等权
    excess = rets - base
    frac_pos = float((rets > 0).mean())
    Xs = X[[m[0] for m in members]]  # 成员特征行
    spreads: dict[str, Optional[float]] = {}
    neg_groups: dict[str, Any] = {}
    susp_by_group: dict[str, dict[str, float]] = {}
    early: dict[int, dict[str, tuple]] = {k: {} for k in early_days}

    def _group_stats(mask: np.ndarray, dim: str, gname: str) -> Optional[float]:
        g = int(mask.sum())
        susp_by_group.setdefault(dim, {})[gname] = (
            round(float(np.mean([members[i][3] for i in np.where(mask)[0]])), 4)
            if g
            else math.nan
        )
        return float(excess[mask].mean()) if g >= min_group else None

    for dim in ALL_DIMS:
        spreads[dim] = None
        if dim in BOARD_DIMS:
            # v0.372（修订注记 8）：board 拆 4 维 signed——该组等权超额 − 其余组
            # 等权超额；两组都 ≥ min_group 才可算（北交所早段常态空 ⇒ 记缺）
            g = BOARD_DIM_GROUP[dim]
            in_idx = [k for k, m in enumerate(members) if stocks[m[1]]["board"] == g]
            out_idx = [k for k, m in enumerate(members) if stocks[m[1]]["board"] != g]
            susp_by_group.setdefault(dim, {})["in"] = (
                round(float(np.mean([members[i][3] for i in in_idx])), 4)
                if in_idx
                else math.nan
            )
            if len(in_idx) >= min_group and len(out_idx) >= min_group:
                spreads[dim] = float(excess[in_idx].mean() - excess[out_idx].mean())
            continue
        col = MATRIX_DIMS.index(dim)
        v = Xs[:, col].astype("float64")
        ok = ~np.isnan(v)
        if dim in NEG_GROUP_DIMS:
            neg = ok & (v < 0)
            if neg.sum() >= min_group:
                neg_groups[dim] = {
                    "n": int(neg.sum()),
                    "mean_excess": float(excess[neg].mean()),
                }
            ok = ok & (v >= 0)  # 价差剔除负值组（单独报）
        if int(ok.sum()) < min_stocks:
            continue  # 该段该维记缺
        vv = v[ok]
        ex = excess[ok]
        mm = [m for k, m in enumerate(members) if ok[k]]
        if dim == "ma_bull":
            m1 = vv >= 0.5
            g1 = _group_stats_idx(ex, mm, m1, dim, "1", susp_by_group, min_group)
            g0 = _group_stats_idx(ex, mm, ~m1, dim, "0", susp_by_group, min_group)
            if g1 is not None and g0 is not None:
                spreads[dim] = g1 - g0
        else:
            order = np.argsort(vv, kind="stable")
            n_q = len(order)
            qs = np.array_split(order, 5)
            means_q = []
            ok_q = True
            for qi, q in enumerate(qs):
                if len(q) < min_group:
                    ok_q = False
                    break
                means_q.append(float(ex[q].mean()))
                susp_by_group.setdefault(dim, {})[f"Q{qi + 1}"] = round(
                    float(np.mean([mm[i][3] for i in q])), 4
                )
            if ok_q and len(means_q) == 5:
                spreads[dim] = means_q[4] - means_q[0]  # Q5 − Q1
                # 附带读数：段内前 k 日价差 vs 剩余价差（不作判据）
                for kk in early_days:
                    if seg["n_days"] > kk:
                        sp = _early_spread(seg, kk, mm, q_lo=qs[0], q_hi=qs[4], ctx=ctx)
                        if sp is not None:
                            early[kk][dim] = sp
    # 附带读数（不作判据）：原 board 分组均值表保留（v0.372 拆维后仅作展示）
    board_group_means: dict[str, Any] = {}
    for g in ("主板", "创业板", "科创板", "北交所", "其他"):
        idx = [k for k, m in enumerate(members) if stocks[m[1]]["board"] == g]
        if idx:
            board_group_means[g] = {
                "n": len(idx),
                "mean_excess": float(excess[idx].mean()),
            }
    return {
        "spreads": spreads,
        "neg_groups": neg_groups,
        "susp_by_group": susp_by_group,
        "board_group_means": board_group_means,
        "n_members": len(members),
        "frac_pos": frac_pos,
        "early": early,
    }


def _group_stats_idx(ex, mm, mask, dim, gname, susp_by_group, min_group):
    idx = np.where(mask)[0]
    susp_by_group.setdefault(dim, {})[gname] = (
        round(float(np.mean([mm[i][3] for i in idx])), 4) if len(idx) else math.nan
    )
    return float(ex[idx].mean()) if len(idx) >= min_group else None


def _early_spread(seg, k, mm, *, q_lo, q_hi, ctx):
    """段内前 k 日（段起点→段内第 k 个交易日收盘）的 Q5−Q1 价差 + 剩余段价差。"""
    days = seg["days"]
    dk = days[k - 1]
    r_early, r_rest = [], []
    for m in mm:
        st = ctx["stocks"][m[1]]
        _, _, _, _, j1, j2 = m
        dates, close = st["dates"], st["close"]
        jk = bisect.bisect_right(dates, dk, j1, j2) - 1
        if jk <= j1:
            return None  # 段内第 k 日前无后续 bar（该段该维跳过）
        r_early.append(float(close[jk] / close[j1] - 1.0))
        r_rest.append(float(close[j2 - 1] / close[jk] - 1.0))
    if not r_early:
        return None
    b1, b2 = float(np.mean(r_early)), float(np.mean(r_rest))
    lo_e = float(np.mean([r_early[i] for i in q_lo])) - b1
    hi_e = float(np.mean([r_early[i] for i in q_hi])) - b1
    lo_r = float(np.mean([r_rest[i] for i in q_lo])) - b2
    hi_r = float(np.mean([r_rest[i] for i in q_hi])) - b2
    return (hi_e - lo_e, hi_r - lo_r)


# ---------------------------------------------------------------------------
# 板块层（880/881 指数；不用成分股——历史成分不可得）
# ---------------------------------------------------------------------------


def eval_segment_sector(
    seg: dict[str, Any],
    sector: dict[str, dict[str, Any]],
    *,
    min_sectors: int = 30,
) -> Optional[dict[str, Any]]:
    """板块层单段：合格指数（起点有 bar ∧ 段内 bar ≥ 80% 段长）≥ min_sectors 个
    ⇒ {code: 段超额}（相对当段合格指数等权）；否则 None（该段不计入板块层）。"""
    s, e = seg["start"], seg["end"]
    rets: dict[str, float] = {}
    for code, st in sector.items():
        dates, close = st["dates"], st["close"]
        j1 = bisect.bisect_left(dates, s)
        if j1 >= len(dates) or dates[j1] != s:
            continue
        j2 = bisect.bisect_right(dates, e)
        if j2 - j1 < seg["n_days"] * SUSP_BAR_FRAC:
            continue
        rets[code] = float(close[j2 - 1] / close[j1] - 1.0)
    if len(rets) < min_sectors:
        return None
    base = float(np.mean(list(rets.values())))
    return {"excess": {c: r - base for c, r in rets.items()}, "n_sectors": len(rets)}


# ---------------------------------------------------------------------------
# 判据（C1~C4 → verdict_four_state；全部机械实现预注册原文）
# ---------------------------------------------------------------------------


def _window_dir_spreads(
    seg_results: list[tuple[dict, Optional[dict]]], dim: str
) -> list[float]:
    return [
        r["spreads"][dim]
        for _seg, r in seg_results
        if r is not None and r["spreads"].get(dim) is not None
    ]


def judge_style_dim(
    dim: str,
    per_win: dict[str, list[tuple[dict, Optional[dict]]]],
    c4_pools: dict[str, Optional[list[float]]],
    *,
    min_segs: int = MIN_SEGS_PER_WINDOW,
    c4_min_pool: int = C4_MIN_POOL,
) -> dict[str, Any]:
    """逐（方向×维度）判据：C1 段数+组规模 / C2 双窗同号+段占比 / C4 双窗规则
    （修订注记 2）→ 四态。``per_win`` = {"A": [(seg, result)], "B": [...]}。"""
    win_stats: dict[str, Any] = {}
    c1_wins = []
    for w in ("A", "B"):
        res = per_win.get(w, [])
        spreads = _window_dir_spreads(res, dim)
        n = len(spreads)
        mean = statistics.mean(spreads) if n else None
        t = _t_stat(spreads)
        frac_same = None
        if n and mean is not None:
            frac_same = sum(1 for x in spreads if (x >= 0) == (mean >= 0)) / n
        win_stats[w] = {
            "n_segs": n,
            "mean_spread": mean,
            "t": t,
            "frac_same_sign": frac_same,
        }
        c1_wins.append(n >= min_segs)
    c1_ok = all(c1_wins)
    m_a, m_b = win_stats["A"]["mean_spread"], win_stats["B"]["mean_spread"]
    if m_a is None or m_b is None:
        c2_ok: Optional[bool] = None
    else:
        c2_ok = (
            (m_a >= 0) == (m_b >= 0)
            and (win_stats["A"]["frac_same_sign"] or 0) >= C2_SAME_SIGN_FRAC
            and (win_stats["B"]["frac_same_sign"] or 0) >= C2_SAME_SIGN_FRAC
        )
    # C4：两窗各自 |t| vs 族池 q95（criteria_kit.c4_state_of 单源），再按双窗规则合并
    states = []
    for w in ("A", "B"):
        pool = c4_pools.get(w)
        t = win_stats[w]["t"]
        states.append(
            kit.c4_state_of(
                list(pool or []),
                min_pool=c4_min_pool,
                plan_delta=abs(t) if t is not None else None,
            )
        )
    if all(s == "confirmed_pass" for s in states):
        c4_state = "confirmed_pass"
    elif all(s == "confirmed_fail" for s in states):
        c4_state = "confirmed_fail"
    else:
        c4_state = "provisional"  # 一过一不过 / 池空 / 池未满
    verdict = kit.verdict_four_state(
        c1_ok=c1_ok,
        c2_ok=c2_ok,
        c3_ok=c2_ok,  # 风格层 C3 即 C2 段占比条件（预注册原文）
        c4_state=c4_state,
    )
    return {
        "c1_ok": c1_ok,
        "c2_ok": c2_ok,
        "c3_ok": c2_ok,
        "c4_state": c4_state,
        "c4_by_window": dict(zip(("A", "B"), states)),
        "verdict": verdict,
        "windows": win_stats,
    }


def judge_sector_c3(
    per_win_seg_results: dict[str, list[Optional[dict]]],
) -> Optional[bool]:
    """板块层层级 C3：相邻同向段板块段超额排名 Spearman，两窗均值都 > 0。

    任一窗同向段 < 2 ⇒ 无相邻对 ⇒ None（修订注记 4，provisional 通道）。
    """
    means = []
    for w in ("A", "B"):
        res = [r for r in per_win_seg_results.get(w, []) if r is not None]
        pairs = []
        for a, b in zip(res, res[1:]):
            common = sorted(set(a["excess"]) & set(b["excess"]))
            rho = _spearman(
                [a["excess"][c] for c in common], [b["excess"][c] for c in common]
            )
            if rho is not None:
                pairs.append(rho)
        if not pairs:
            return None
        means.append(float(np.mean(pairs)))
    return bool(all(m > 0 for m in means))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _board_of_stock(code: str) -> str:
    """R43 板块组（静态属性）：沪/深主板合并为「主板」。"""
    from custos.research.data_coverage_probe import _board_of  # noqa: PLC0415

    return {"沪主板": "主板", "深主板": "主板"}.get(
        _board_of(market_of(code).lower(), str(code)[:6]), "其他"
    )


def _build_ctx(
    codes: list[str],
    feature_dates: set[str],
    *,
    bars_loader: Callable,
    count: int,
    shares_fn: Optional[Callable],
    xdxr_fn: Optional[Callable],
    pit_records: Optional[list[dict]],
    susp_bar_frac: float,
    vipdoc_dates: Optional[dict[str, np.ndarray]],
) -> dict[str, Any]:
    """逐股流式装载：rolling 特征算全序列，只留 D∩该股日期 的行（float32）；
    留存每股 dates/close/ret1（段收益 + 全市场等权收益序列用）。"""
    from custos.research import load_window as _lw  # noqa: PLC0415

    bars = bars_loader(codes, count, None, _lw.SEG_MINING_B[1])
    pit_dates = sorted(feature_dates)
    pit_tables = _pit_tables(pit_records, pit_dates)
    stocks: dict[str, Any] = {}
    blocks_raw: dict[str, dict[str, np.ndarray]] = {}
    mkt_sum: dict[str, float] = {}
    mkt_cnt: dict[str, int] = {}
    for k, code in enumerate(codes, 1):
        df = bars.get(code)
        if df is None or not len(df):
            continue
        df = df.reset_index(drop=True)
        dts = df["date"].astype(str).str[:10]
        close = df["close"].astype("float64").to_numpy()
        ret1 = pd.Series(close).pct_change().to_numpy()
        dates_list = dts.tolist()
        for d, r in zip(dates_list, ret1):
            if not math.isnan(r):
                mkt_sum[d] = mkt_sum.get(d, 0.0) + float(r)
                mkt_cnt[d] = mkt_cnt.get(d, 0) + 1
        try:
            shares_events = shares_fn(code) if shares_fn else None
        except Exception:  # noqa: BLE001 — 单票股本缺 ⇒ 依赖维度记缺，不 fail
            shares_events = None
        try:
            xdxr_events = xdxr_fn(code) if xdxr_fn else None
        except Exception:  # noqa: BLE001
            xdxr_events = None
        frame = _own_feature_frame(
            code,
            df,
            shares_events=shares_events,
            pit_table=pit_tables.get(code) if pit_tables else None,
            xdxr_events=xdxr_events,
            limit_pct=price_limit_pct(code),
        )
        stocks[code] = {
            "dates": dates_list,
            "close": close,
            "ret1": ret1,
            "board": _board_of_stock(code),
            "frame": frame,
        }
        keep = dts.isin(feature_dates)
        if bool(keep.any()):
            sub = frame.loc[keep.to_numpy()]
            for d, row in zip(sub["date"], sub[list(MATRIX_DIMS)].to_numpy()):
                blocks_raw.setdefault(d, {})[code] = row.astype(np.float32)
        if k % 500 == 0:
            print(f"[load] {k}/{len(codes)} 票特征完成", file=sys.stderr)
    # 全市场等权日收益 → 第二遍 beta/ivol（用留存 ret1，不重读盘）
    mkt_ret = {d: mkt_sum[d] / mkt_cnt[d] for d in mkt_sum}
    for code, st in stocks.items():
        beta_map, ivol_map = _fill_beta_ivol(
            st["dates"], st["ret1"], mkt_ret, feature_dates
        )
        if not (beta_map or ivol_map):
            continue
        frame = st["frame"]
        ib = frame.index[frame["date"].isin(beta_map)]
        frame.loc[ib, "beta250"] = frame.loc[ib, "date"].map(beta_map)
        ii = frame.index[frame["date"].isin(ivol_map)]
        frame.loc[ii, "ivol60"] = frame.loc[ii, "date"].map(ivol_map)
        for d in beta_map:
            if code in blocks_raw.get(d, {}):
                blocks_raw[d][code][MATRIX_DIMS.index("beta250")] = np.float32(
                    beta_map[d]
                )
        for d in ivol_map:
            if code in blocks_raw.get(d, {}):
                blocks_raw[d][code][MATRIX_DIMS.index("ivol60")] = np.float32(
                    ivol_map[d]
                )
        st["frame"] = None  # 全序列帧释放（只留 D 行与三数组）
    blocks = {
        d: (np.array(sorted(m)), np.stack([m[c] for c in sorted(m)]))
        for d, m in blocks_raw.items()
    }
    return {
        "stocks": stocks,
        "blocks": blocks,
        "vipdoc_dates": vipdoc_dates,
        "susp_bar_frac": susp_bar_frac,
        "drops": {"no_start_bar": 0, "low_bars": 0},
        "mkt_ret": mkt_ret,
    }


def _seg_eval_batch(
    segs: list[dict[str, Any]],
    ctx: dict[str, Any],
    *,
    min_stocks: int,
    min_group: int,
    early_days: tuple[int, ...] = (),
) -> list[tuple[dict, Optional[dict]]]:
    return [
        (
            s,
            eval_segment(
                s,
                ctx,
                min_stocks=min_stocks,
                min_group=min_group,
                early_days=early_days,
            ),
        )
        for s in segs
    ]


def _family_max_t(
    draw_results: list[list[tuple[dict, Optional[dict]]]], family_dims: tuple
) -> list[float]:
    """每次抽样：逐维 t（该抽样的伪段集上）→ 族内 max|t|（池元素）。"""
    pool = []
    for res in draw_results:
        ts = [
            abs(t)
            for d in family_dims
            if (t := _t_stat(_window_dir_spreads(res, d))) is not None
        ]
        pool.append(max(ts) if ts else None)
    return [p for p in pool if p is not None]


def run_study(
    args: Any,
    *,
    bars_loader: Optional[Callable] = None,
    regime: Optional[dict[str, str]] = None,
    codes: Optional[list[str]] = None,
    vipdoc_dates: Optional[dict[str, np.ndarray]] = None,
    sector_bars: Optional[dict[str, pd.DataFrame]] = None,
    sector_types: Optional[dict[str, str]] = None,
    shares_fn: Optional[Callable] = None,
    xdxr_fn: Optional[Callable] = None,
    pit_records: Optional[list[dict]] = None,
    pseudo_draw_fn: Optional[Callable] = None,
) -> dict[str, Any]:
    """R43 主流程：段切分 → 伪段预抽（定 D）→ 逐股流式特征 → 段评估 → C1~C4。

    注入点（测试全走合成数据）：bars_loader / regime / codes / vipdoc_dates /
    sector_bars / sector_types / shares_fn / xdxr_fn / pit_records /
    pseudo_draw_fn（**回归哨兵注入口**——置 None 走生产实现）。生产路径不读
    判定段 ⇒ 不登记 window_usage。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import provenance as pv  # noqa: PLC0415

    min_stocks = args.min_stocks
    min_group = args.min_group
    min_segs = args.min_segs

    # ① 段切分（regime 注入或生产台账重放）
    if regime is None:
        regime = bt.load_amv_regime(since="1990-01-01")
    segs_all = split_regime_segments(regime)
    kept, drops = assign_windows(segs_all, min_seg_days=args.min_seg_days)
    regime_days = sorted(regime)
    win_days = {
        w: [d for d in regime_days if WINDOWS[w][0] <= d <= WINDOWS[w][1]]
        for w in WINDOWS
    }
    n_qualified = sum(len(kept[w][d]) for w in WINDOWS for d in DIRECTIONS)
    if n_qualified == 0:
        return {}  # 空结果护栏：两窗 0 合格段 ⇒ main 非零退出不落盘

    # ② 伪段预抽（加载数据前；确定性）→ 特征日全集 D
    draw_fn = pseudo_draw_fn or draw_pseudo_placements
    pseudo: dict[tuple[str, str], dict[str, Any]] = {}
    for wi, w in enumerate(WINDOWS):
        for di, dr in enumerate(DIRECTIONS):
            real = kept[w][dr]
            if not real or not win_days[w]:
                continue
            pseudo[(w, dr)] = draw_fn(
                real,
                win_days[w],
                n_draws=args.n_random,
                seed=args.c4_seed + wi * 1000 + di,
            )
    feature_dates = {
        s["start"] for w in WINDOWS for d in DIRECTIONS for s in kept[w][d]
    }
    for p in pseudo.values():
        for draw in p["draws"]:
            for s in draw:
                feature_dates.add(s["start"])

    # ③ 宇宙 + 逐股流式装载
    if codes is None:
        ns = argparse.Namespace(
            codes_file=args.codes_file,
            universe_local=args.universe_local,
            universe_sample=args.universe_sample,
            seed=args.seed,
            codes=args.codes,
        )
        codes = _universe.resolve(ns, _build_parser())
        args.universe_meta = ns.universe_meta
    else:
        args.universe_meta = _universe.universe_meta(codes, "injected")
    count = 0
    if bars_loader is None:
        from custos.research import backtest_factors as _bt  # noqa: PLC0415

        bars_loader = _bt._load_bars_local
        min_seg_start = min(
            s["start"] for w in WINDOWS for d in DIRECTIONS for s in kept[w][d]
        )
        count = lw.count_for_start(min_seg_start) + 260  # 特征回看 250 根余量
    shares_fn = shares_fn or _default_shares_fn
    xdxr_fn = xdxr_fn or _default_xdxr_fn
    if pit_records is None:
        pit_records = _default_pit_records()
    ctx = _build_ctx(
        codes,
        feature_dates,
        bars_loader=bars_loader,
        count=count,
        shares_fn=shares_fn,
        xdxr_fn=xdxr_fn,
        pit_records=pit_records,
        susp_bar_frac=args.susp_bar_frac,
        vipdoc_dates=vipdoc_dates,
    )
    if not ctx["stocks"]:
        return {}  # 0 票有数据 ⇒ 空结果护栏

    # ④ 板块层数据
    sector = _load_sectors(sector_bars, sector_types)

    # ⑤ 真实段评估（风格层 + 板块层）
    style_results: dict[str, dict[str, list]] = {}
    sector_results: dict[str, dict[str, list]] = {}
    for w in WINDOWS:
        style_results[w] = {}
        sector_results[w] = {}
        for dr in DIRECTIONS:
            style_results[w][dr] = _seg_eval_batch(
                kept[w][dr],
                ctx,
                min_stocks=min_stocks,
                min_group=min_group,
                early_days=(5, 10),
            )
            sector_results[w][dr] = [
                eval_segment_sector(s, sector, min_sectors=args.min_sectors)
                for s in kept[w][dr]
            ]

    # ⑥ C4 伪段评估（两族 + 板块族共用同组伪段）；伪段的成员剔除计数**不进**
    # 报告（member_drops 只核算真实段——报告的剔除数要回答「真实段剔了多少」）
    real_drops = ctx["drops"]
    c4_pools_style: dict[tuple[str, str, str], list[float]] = {}
    c4_pools_sector: dict[tuple[str, str], list[float]] = {}
    ctx["drops"] = {"no_start_bar": 0, "low_bars": 0}
    for (w, dr), p in pseudo.items():
        draw_results = [
            _seg_eval_batch(draw, ctx, min_stocks=min_stocks, min_group=min_group)
            for draw in p["draws"]
        ]
        for fam, dims in (("price", PRICE_FAMILY), ("fund", FUND_FAMILY)):
            c4_pools_style[(w, dr, fam)] = _family_max_t(draw_results, dims)
        if sector:
            sector_draws = [
                [
                    eval_segment_sector(s, sector, min_sectors=args.min_sectors)
                    for s in draw
                ]
                for draw in p["draws"]
            ]
            idx_codes = sorted(sector)
            pool = []
            for res in sector_draws:
                ts = []
                for c in idx_codes:
                    series = [r["excess"][c] for r in res if r and c in r["excess"]]
                    t = _t_stat(series)
                    if t is not None:
                        ts.append(abs(t))
                pool.append(max(ts) if ts else None)
            c4_pools_sector[(w, dr)] = [x for x in pool if x is not None]
    ctx["drops"] = real_drops

    # ⑦ 判据组装（风格层逐（方向×维度）；板块层逐（方向×指数）+ 层级 C3）
    style_out: dict[str, Any] = {}
    for dr in DIRECTIONS:
        per_win = {w: style_results[w][dr] for w in WINDOWS}
        n_qual = {
            w: sum(1 for _s, r in style_results[w][dr] if r is not None)
            for w in WINDOWS
        }
        direction_untested = any(n < min_segs for n in n_qual.values())
        dims_out: dict[str, Any] = {}
        for dim in ALL_DIMS:
            jd = judge_style_dim(
                dim,
                per_win,
                {w: c4_pools_style.get((w, dr, FAMILY_OF[dim]), []) for w in WINDOWS},
                min_segs=min_segs,
                c4_min_pool=args.c4_min_pool,
            )
            if direction_untested:
                # 功效节写死：任一挖掘窗同向合格段 < 8 ⇒ 该方向整体 untested
                jd["verdict"] = "untested"
                jd["note"] = "方向级 C1：挖掘窗同向合格段 < 8"
            dims_out[dim] = jd
        style_out[dr] = {
            "n_qualified_segs": n_qual,
            "direction_untested": direction_untested,
            "dims": dims_out,
        }

    sector_out: dict[str, Any] = {}
    for dr in DIRECTIONS:
        per_win_sec = {w: sector_results[w][dr] for w in WINDOWS}
        c3 = judge_sector_c3(per_win_sec)
        idx_out: dict[str, Any] = {}
        idx_codes = sorted(sector)
        for c in idx_codes:
            per_win_series = {}
            c1_wins = []
            for w in WINDOWS:
                series = [
                    r["excess"][c]
                    for r in sector_results[w][dr]
                    if r and c in r["excess"]
                ]
                per_win_series[w] = series
                c1_wins.append(len(series) >= min_segs)
            means = {
                w: (statistics.mean(s) if s else None)
                for w, s in per_win_series.items()
            }
            tstats = {w: _t_stat(s) for w, s in per_win_series.items()}
            c1_ok = all(c1_wins)
            ma_v, mb_v = means["A"], means["B"]
            c2_ok: Optional[bool]
            if ma_v is None or mb_v is None:
                c2_ok = None
            else:
                fracs = {}
                for w in WINDOWS:
                    sw = per_win_series[w]
                    mw = means[w]
                    fracs[w] = (
                        sum(1 for x in sw if (x >= 0) == (mw >= 0)) / len(sw)
                        if sw and mw is not None
                        else None
                    )
                c2_ok = (
                    (ma_v >= 0) == (mb_v >= 0)
                    and (fracs["A"] or 0) >= C2_SAME_SIGN_FRAC
                    and (fracs["B"] or 0) >= C2_SAME_SIGN_FRAC
                )
            states = []
            for w in WINDOWS:
                tw = tstats[w]
                states.append(
                    kit.c4_state_of(
                        c4_pools_sector.get((w, dr), []),
                        min_pool=args.c4_min_pool,
                        plan_delta=abs(tw) if tw is not None else None,
                    )
                )
            if all(s == "confirmed_pass" for s in states):
                c4_state = "confirmed_pass"
            elif all(s == "confirmed_fail" for s in states):
                c4_state = "confirmed_fail"
            else:
                c4_state = "provisional"
            idx_out[c] = {
                "group": sector[c]["group"],
                "c1_ok": c1_ok,
                "c2_ok": c2_ok,
                "c3_ok": c3,
                "c4_state": c4_state,
                "verdict": kit.verdict_four_state(
                    c1_ok=c1_ok, c2_ok=c2_ok, c3_ok=c3, c4_state=c4_state
                ),
                "windows": {
                    w: {
                        "n_segs": len(per_win_series[w]),
                        "mean_excess": means[w],
                        "t": tstats[w],
                    }
                    for w in WINDOWS
                },
            }
        sector_out[dr] = {"c3_ok": c3, "indices": idx_out}

    # ⑧ 附带读数（不作判据）
    extras = _extras(style_results)

    # ⑨ 校准/诊断镜像：criteria.C4 块（零假设校准套件的读取面）
    focus_dim, focus_dir = args.focus_dim, args.focus_dir
    focus = style_out.get(focus_dir, {}).get("dims", {}).get(focus_dim, {})
    criteria = {
        "C4": {"state": focus.get("c4_state", "indeterminate")},
        "focus": {"dim": focus_dim, "direction": focus_dir},
        "verdict_focus": focus.get("verdict"),
    }

    report: dict[str, Any] = {
        "tool": "regime_style_study",
        "unit": "R43",
        "params": {
            "min_seg_days": args.min_seg_days,
            "susp_bar_frac": args.susp_bar_frac,
            "min_stocks": min_stocks,
            "min_group": min_group,
            "min_segs": min_segs,
            "min_sectors": args.min_sectors,
            "n_random": args.n_random,
            "c4_seed": args.c4_seed,
            "c4_min_pool": args.c4_min_pool,
            "windows": {w: list(v) for w, v in WINDOWS.items()},
        },
        "n_qualified_segments": n_qualified,
        "segment_drops": drops,
        "member_drops": ctx["drops"],
        "segments": {
            w: {
                dr: [
                    {"start": s["start"], "end": s["end"], "n_days": s["n_days"]}
                    for s in kept[w][dr]
                ]
                for dr in DIRECTIONS
            }
            for w in WINDOWS
        },
        "pseudo_meta": {
            f"{w}/{dr}": {"n_draws": len(p["draws"]), "n_dropped": p["n_dropped"]}
            for (w, dr), p in pseudo.items()
        },
        "c4_pools": {
            f"style/{w}/{dr}/{fam}": {"n": len(v), "q95": kit.q95(v)}
            for (w, dr, fam), v in c4_pools_style.items()
        }
        | {
            f"sector/{w}/{dr}": {"n": len(v), "q95": kit.q95(v)}
            for (w, dr), v in c4_pools_sector.items()
        },
        "style": style_out,
        "sector": sector_out,
        "extras": extras,
        "criteria": criteria,
        "notes": [
            "描述性条件化研究，不接交易引擎；cost_sensitivity 不适用（无交易）",
            "口径：price/mcap_float/mcap_total/pe_ttm/pb/ps_ttm/dy 七维 = raw_price"
            "（未复权收盘，TODO #94）；其余维度 = qfq（免疫论证见模块 docstring）",
            "判定段 2017~2021 的段集未读（攒批协议未拍板）；本工具不登记 window_usage",
            "停牌伪抗跌：每组平均停牌比例见 extras.susp_ratio_by_group；"
            "debt_ratio 恒记缺（PIT 库无资产负债率字段）；dy 依赖 xdxr 缓存覆盖",
            "幸存者偏差方向性：空头段低质量/小市值组的「抗跌」按偏乐观解读（R14/R15）",
        ],
        "provenance": pv.build(
            args,
            unit="R43",
            criteria_version="v0.366/v0.367",
            pre_reg_doc=Path("governance/research/R43_regime_sector_style.md"),
            data_last_date=max((d for d in ctx["mkt_ret"]), default=None),
        ),
    }
    return report


def parse_judgment_specs(text: str) -> list[dict[str, Any]]:
    """判定段候选清单解析：逗号分隔 ``方向:维度:符号``（风格维）或
    ``方向:sector:指数代码:符号``（板块指数）；符号 ∈ {+,-} = 挖掘段过线方向。"""
    out: list[dict[str, Any]] = []
    for raw in [x.strip() for x in text.split(",") if x.strip()]:
        parts = raw.split(":")
        if len(parts) == 3 and parts[0] in DIRECTIONS and parts[1] in ALL_DIMS:
            kind, key, sg = "style", parts[1], parts[2]
        elif len(parts) == 4 and parts[0] in DIRECTIONS and parts[1] == "sector":
            kind, key, sg = "sector", parts[2], parts[3]
        else:
            raise ValueError(
                f"判定段候选格式非法: {raw!r}（方向:维度:± 或 方向:sector:代码:±）"
            )
        if sg not in ("+", "-"):
            raise ValueError(f"候选 {raw!r} 符号须为 + 或 -")
        out.append(
            {
                "id": raw,
                "dir": parts[0],
                "kind": kind,
                "key": key,
                "sign": 1 if sg == "+" else -1,
            }
        )
    if not out:
        raise ValueError("--judgment-read 候选清单为空")
    return out


def _signed_t_and_frac(
    series: list[float], sign: int
) -> tuple[Optional[float], Optional[float]]:
    t = _t_stat(series)
    if t is None or not series:
        return None, None
    frac = sum(1 for x in series if (x >= 0) == (sign > 0)) / len(series)
    return sign * t, frac


def run_judgment_read(
    args: Any,
    cands: list[dict[str, Any]],
    *,
    bars_loader: Optional[Callable] = None,
    regime: Optional[dict[str, str]] = None,
    codes: Optional[list[str]] = None,
    vipdoc_dates: Optional[dict[str, np.ndarray]] = None,
    sector_bars: Optional[dict[str, pd.DataFrame]] = None,
    sector_types: Optional[dict[str, str]] = None,
    shares_fn: Optional[Callable] = None,
    xdxr_fn: Optional[Callable] = None,
    pit_records: Optional[list[dict]] = None,
    pseudo_draw_fn: Optional[Callable] = None,
) -> dict[str, Any]:
    """判定段读数（v0.395，owner 2026-10-15 选 B；R43 入批）。

    候选 = 挖掘段过线、冻结了方向与符号的（维度/指数），**判定段不再挑选**：
    统计量 = 符号 × 判定段同向区间段价差（超额）的 t；零假设池 = 同次运行在判定段
    按同规则放置的伪区间段（段长采自判定段真实段长），**同一维度**的符号化 t
    （不取族内 max——候选已冻结，两边都不挑选）。判决：段数 < --min-segs ⇒ untested；
    统计量 ≤0 或同号段占比 < C2_SAME_SIGN_FRAC ⇒ falsified；带 --batch-id ⇒
    judgment_line confirmed_pass ⇒ candidate，否则 provisional；无批次号 ⇒ no_batch。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    js, je = lw.SEG_JUDGMENT
    if regime is None:
        regime = bt.load_amv_regime(since="1990-01-01")
    segs_all = split_regime_segments(regime)
    real: dict[str, list[dict[str, Any]]] = {d: [] for d in DIRECTIONS}
    for sg_ in segs_all:
        if (
            sg_["n_days"] >= args.min_seg_days
            and js <= sg_["start"]
            and sg_["end"] <= je
        ):
            real[sg_["dir"]].append({**sg_, "window": "J"})
    j_days = [d for d in sorted(regime) if js <= d <= je]
    if not any(real.values()) or not j_days:
        return {}
    draw_fn = pseudo_draw_fn or draw_pseudo_placements
    pseudo = {
        dr: draw_fn(
            real[dr], j_days, n_draws=args.n_random, seed=args.c4_seed + 7000 + di
        )
        for di, dr in enumerate(DIRECTIONS)
        if real[dr]
    }
    feature_dates = {sg_["start"] for d in DIRECTIONS for sg_ in real[d]}
    for pz in pseudo.values():
        for draw in pz["draws"]:
            feature_dates.update(sg_["start"] for sg_ in draw)
    if codes is None:
        ns = argparse.Namespace(
            codes_file=args.codes_file,
            universe_local=args.universe_local,
            universe_sample=args.universe_sample,
            seed=args.seed,
            codes=args.codes,
        )
        codes = _universe.resolve(ns, _build_parser())
        args.universe_meta = ns.universe_meta
    else:
        args.universe_meta = _universe.universe_meta(codes, "injected")
    count = 0
    if bars_loader is None:
        bars_loader = bt._load_bars_local
        count = lw.count_for_start(min(feature_dates)) + 260
    ctx = _build_ctx(
        codes,
        feature_dates,
        bars_loader=bars_loader,
        count=count,
        shares_fn=shares_fn or _default_shares_fn,
        xdxr_fn=xdxr_fn or _default_xdxr_fn,
        pit_records=pit_records if pit_records is not None else _default_pit_records(),
        susp_bar_frac=args.susp_bar_frac,
        vipdoc_dates=vipdoc_dates,
    )
    if not ctx["stocks"]:
        return {}
    need_sector = any(c["kind"] == "sector" for c in cands)
    sector = _load_sectors(sector_bars, sector_types) if need_sector else {}

    def _style_series(res: list, dim: str) -> list[float]:
        return _window_dir_spreads(res, dim)

    def _sector_series(res: list, code: str) -> list[float]:
        return [r["excess"][code] for r in res if r and code in r["excess"]]

    style_real = {
        dr: _seg_eval_batch(
            real[dr], ctx, min_stocks=args.min_stocks, min_group=args.min_group
        )
        for dr in DIRECTIONS
    }
    style_pseudo = {
        dr: [
            _seg_eval_batch(
                d, ctx, min_stocks=args.min_stocks, min_group=args.min_group
            )
            for d in pz["draws"]
        ]
        for dr, pz in pseudo.items()
    }
    sector_real: dict[str, list] = {}
    sector_pseudo: dict[str, list] = {}
    if sector:
        for dr in DIRECTIONS:
            sector_real[dr] = [
                eval_segment_sector(x, sector, min_sectors=args.min_sectors)
                for x in real[dr]
            ]
            sector_pseudo[dr] = [
                [
                    eval_segment_sector(x, sector, min_sectors=args.min_sectors)
                    for x in d
                ]
                for d in (pseudo.get(dr) or {"draws": []})["draws"]
            ]

    batch_id = str(getattr(args, "batch_id", "") or "")
    results: list[dict[str, Any]] = []
    for c in cands:
        dr, sign = c["dir"], c["sign"]
        if c["kind"] == "style":
            series = _style_series(style_real[dr], c["key"])
            draws = [_style_series(r, c["key"]) for r in style_pseudo.get(dr, [])]
        else:
            series = _sector_series(sector_real.get(dr, []), c["key"])
            draws = [_sector_series(r, c["key"]) for r in sector_pseudo.get(dr, [])]
        stat, frac = _signed_t_and_frac(series, sign)
        pool = [v for d in draws if (v := _signed_t_and_frac(d, sign)[0]) is not None]
        line = None
        if batch_id:
            from custos.research import judgment_batch as jb  # noqa: PLC0415

            line = jb.judgment_line(batch_id, stat, pool, min_pool=args.c4_min_pool)
        if len(series) < args.min_segs:
            verdict = "untested"
        elif stat is None or stat <= 0 or (frac or 0) < C2_SAME_SIGN_FRAC:
            verdict = "falsified"
        elif line is None:
            verdict = "no_batch"
        else:
            verdict = (
                "candidate" if line["state"] == "confirmed_pass" else "provisional"
            )
        results.append(
            {
                **c,
                "n_segs": len(series),
                "mean": statistics.mean(series) if series else None,
                "signed_t": stat,
                "frac_same_sign": frac,
                "pool": pool,
                "line": line,
                "verdict": verdict,
            }
        )
    return {
        "schema": "regime_style_judgment/v1",
        "mode": "judgment_read",
        "window": {"start": js, "end": je},
        "n_real_segs": {d: len(real[d]) for d in DIRECTIONS},
        "pseudo_dropped": {d: pz["n_dropped"] for d, pz in pseudo.items()},
        "candidates": results,
        "universe": args.universe_meta,
        "rule": "统计量 = 符号×判定段同向段价差 t；池 = 判定段同次伪段同维度符号化 t（不取族 max）；"
        "段数 < --min-segs ⇒ untested；统计量≤0 或同号占比<0.6 ⇒ falsified；"
        "带 --batch-id ⇒ judgment_line（m 来自名册）confirmed_pass ⇒ candidate 否则 provisional",
    }


def _default_shares_fn(code: str) -> Optional[list[dict]]:
    from custos.datasource.local_tdx import adjust_factors  # noqa: PLC0415

    try:
        return adjust_factors.get_shares_events(code)
    except Exception:  # noqa: BLE001 — 缓存/网络缺 ⇒ 股本依赖维度记缺
        return None


def _default_xdxr_fn(code: str) -> Optional[list[dict]]:
    from custos.datasource.local_tdx import adjust_factors  # noqa: PLC0415

    return adjust_factors.load_xdxr_cache(code)


def _default_pit_records() -> Optional[list[dict]]:
    from custos.datasource.local_tdx import fetch_pit_financials as fpf  # noqa: PLC0415

    recs = fpf.load_ledger()
    return recs or None


def _load_sectors(
    sector_bars: Optional[dict[str, pd.DataFrame]],
    sector_types: Optional[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """880/881 指数 → {code: {dates, close, group}}；生产路径读 vipdoc + tdxzs3.cfg。"""
    if sector_bars is None:
        from custos.core.paths import TDX_ROOT  # noqa: PLC0415
        from custos.datasource.local_tdx import tq_sector  # noqa: PLC0415

        lday = TDX_ROOT / "vipdoc" / "sh" / "lday"
        sector_bars = {}
        if lday.is_dir():
            for p in sorted(lday.glob("sh88[01]*.day")):
                dates_i, close = read_vipdoc_close_series(p)
                if dates_i.size:
                    sector_bars[p.stem[2:]] = pd.DataFrame(
                        {
                            "date": [
                                f"{d // 10000:04d}-{(d // 100) % 100:02d}-{d % 100:02d}"
                                for d in dates_i
                            ],
                            "close": close,
                        }
                    )
        if sector_types is None:
            names = tq_sector.load_sector_names()
            sector_types = {c: v.get("tdx_type", "") for c, v in names.items()}
    groups, n_excluded = sector_type_groups(sector_types or {})
    out: dict[str, dict[str, Any]] = {}
    for code, df in sector_bars.items():
        dts = df["date"].astype(str).str[:10].tolist()
        out[code] = {
            "dates": dts,
            "close": df["close"].astype("float64").to_numpy(),
            "group": groups.get(code, "未知"),
        }
    if n_excluded:
        print(
            f"[INFO] 板块类型=地区(3) 排除 {n_excluded} 个（预注册口径）",
            file=sys.stderr,
        )
    return out


def _extras(style_results: dict[str, dict[str, list]]) -> dict[str, Any]:
    """附带读数（不作判据）：早期可识别同号率 / 段长加权价差 / 绝对收益为正占比 /
    每组平均停牌比例（风险注记 1 的判读原料）。"""
    early: dict[str, Any] = {}
    len_weighted: dict[str, Any] = {}
    frac_pos: dict[str, Any] = {}
    susp: dict[str, Any] = {}
    board_means: dict[str, Any] = {}
    for w in WINDOWS:
        for dr in DIRECTIONS:
            res = [(_s, r) for _s, r in style_results[w][dr] if r is not None]
            key = f"{w}/{dr}"
            fr = [r["frac_pos"] for _s, r in res]
            frac_pos[key] = round(float(np.mean(fr)), 4) if fr else None
            # 原 board 分组均值表（附带读数，v0.372 拆维后不作判据）
            for g in ("主板", "创业板", "科创板", "北交所", "其他"):
                vals = [
                    r["board_group_means"][g]["mean_excess"]
                    for _s, r in res
                    if g in r["board_group_means"]
                ]
                board_means.setdefault(g, {})[key] = (
                    round(float(np.mean(vals)), 6) if vals else None
                )
            for dim in ALL_DIMS:
                pairs5 = [
                    r["early"][5][dim]
                    for _s, r in res
                    if r["early"].get(5, {}).get(dim)
                ]
                pairs10 = [
                    r["early"][10][dim]
                    for _s, r in res
                    if r["early"].get(10, {}).get(dim)
                ]
                same5 = (
                    sum(1 for a, b in pairs5 if (a >= 0) == (b >= 0)) / len(pairs5)
                    if pairs5
                    else None
                )
                same10 = (
                    sum(1 for a, b in pairs10 if (a >= 0) == (b >= 0)) / len(pairs10)
                    if pairs10
                    else None
                )
                early.setdefault(dim, {})[key] = {
                    "same_sign_rate_5d": same5,
                    "same_sign_rate_10d": same10,
                    "n_5d": len(pairs5),
                    "n_10d": len(pairs10),
                }
                lw_pairs = [
                    (s["n_days"], r["spreads"][dim])
                    for s, r in res
                    if r["spreads"].get(dim) is not None
                ]
                len_weighted.setdefault(dim, {})[key] = (
                    round(
                        sum(n * x for n, x in lw_pairs) / sum(n for n, _ in lw_pairs), 6
                    )
                    if lw_pairs
                    else None
                )
                for gname, vals in _collect_susp(res, dim).items():
                    susp.setdefault(dim, {}).setdefault(gname, {})[key] = round(
                        float(np.mean(vals)), 4
                    )
    return {
        "early_same_sign_rate": early,
        "len_weighted_spread": len_weighted,
        "frac_abs_positive": frac_pos,
        "susp_ratio_by_group": susp,
        "board_group_means": board_means,
    }


def _collect_susp(res, dim) -> dict[str, list]:
    out: dict[str, list] = {}
    for _s, r in res:
        for gname, v in r["susp_by_group"].get(dim, {}).items():
            if not (isinstance(v, float) and math.isnan(v)):
                out.setdefault(gname, []).append(v)
    return out


def _print_summary(report: dict[str, Any]) -> None:
    print("\n=== R43 · 0AMV 区间段风格/板块（描述性，不作交易规则）===")
    for dr, label in (("bear", "空头"), ("bull", "多头")):
        st = report["style"][dr]
        counts: dict[str, int] = {}
        for dim, jd in st["dims"].items():
            counts[jd["verdict"]] = counts.get(jd["verdict"], 0) + 1
        print(
            f"[{label}] 合格段 A/B = {st['n_qualified_segs']['A']}/{st['n_qualified_segs']['B']}"
            f"{'（方向整体 untested）' if st['direction_untested'] else ''}；风格维判决: "
            + " / ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        )
    print(
        f"板块层: {sum(len(report['sector'][d]['indices']) for d in DIRECTIONS)} 指数×方向"
    )
    print(
        f"附带读数（不作判据）见 extras；provenance.git_sha={report['provenance']['git_sha']}"
    )


def _build_parser() -> argparse.ArgumentParser:
    """全部 CLI 参数定义（add_argument 留在本文件——__main__._modes() 用 AST 抽取）。"""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--codes", default="", help="逗号分隔 6 位代码（缺省=全量 as-of）")
    ap.add_argument("--codes-file", default="", help="显式钉死宇宙文件（兼容通道）")
    ap.add_argument("--universe-sample", type=int, default=0)
    ap.add_argument("--universe-local", action="store_true")
    ap.add_argument("--seed", type=int, default=0, help="宇宙抽样种子")
    ap.add_argument("--n-random", type=int, default=N_RANDOM, help="C4 伪段抽样次数")
    ap.add_argument("--c4-seed", type=int, default=C4_SEED)
    ap.add_argument("--c4-min-pool", type=int, default=C4_MIN_POOL)
    ap.add_argument("--min-seg-days", type=int, default=MIN_SEG_DAYS)
    ap.add_argument("--susp-bar-frac", type=float, default=SUSP_BAR_FRAC)
    ap.add_argument("--min-stocks", type=int, default=MIN_DIM_STOCKS)
    ap.add_argument("--min-group", type=int, default=MIN_GROUP_N)
    ap.add_argument("--min-segs", type=int, default=MIN_SEGS_PER_WINDOW)
    ap.add_argument(
        "--min-sectors",
        type=int,
        default=30,
        help="板块层段内合格指数下限（修订注记 4；不足 ⇒ 该段不计入板块层）",
    )
    ap.add_argument(
        "--focus-dim",
        default="price",
        help="criteria.C4 诊断镜像的维度（零假设校准套件读取面）",
    )
    ap.add_argument("--focus-dir", default="bear", choices=list(DIRECTIONS))
    ap.add_argument(
        "--out", default=str(LOGS / "regime_style_study.json"), help="产物 JSON 路径"
    )
    ap.add_argument(
        "--judgment-read",
        default="",
        help="判定段读数模式（v0.395，R43 入批）：逗号分隔冻结候选 方向:维度:± 或 "
        "方向:sector:指数代码:±（符号=挖掘段过线方向）；读 2017~2021 判定段，须过批次门",
    )
    ap.add_argument("--batch-id", default="", help="判定段批次号（攒批协议）")
    ap.add_argument("--candidate-id", default="", help="批内候选 id")
    ap.add_argument(
        "--print-config-hash",
        action="store_true",
        help="冻结入批：打印本次配置哈希后退出（不碰数据）",
    )
    return ap


def _main_judgment(
    args: Any, ap: argparse.ArgumentParser, argv: Optional[list[str]], injections: dict
) -> int:
    from custos.research import judgment_batch as jb  # noqa: PLC0415
    from custos.research import window_usage as wu  # noqa: PLC0415

    try:
        cands = parse_judgment_specs(args.judgment_read)
    except ValueError as exc:
        ap.error(str(exc))
    if args.print_config_hash:
        return jb.print_config_hash_and_exit(args, "regime_style_study")
    synthetic = any(v is not None for v in injections.values())  # 入口快照
    jb.gate_judgment_read(
        args,
        ap,
        synthetic=synthetic,
        judgment_span=lw.SEG_JUDGMENT,
        tool="regime_style_study",
    )
    args.cmdline = " ".join(sys.argv if argv is None else ["regime_style_study", *argv])
    report = run_judgment_read(args, cands, **injections)
    if not report:
        print(
            "[FAIL] 判定段 0 合格段（或 0 票有数据）——空结果不写产物", file=sys.stderr
        )
        return 2
    tag = Path(args.out).stem
    k = wu.record_use(
        "R43", "judgment_2017_2021", tag, "判定段读数（攒批）", synthetic=synthetic
    )
    if getattr(args, "_judgment_gated", False):
        jb.mark_read(
            args.batch_id, args.candidate_id, tool="regime_style_study", tag=tag
        )
    report["window_usage"] = {"window": "judgment_2017_2021", "k": k}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    for c in report["candidates"]:
        print(
            f"[R43 判定段] {c['id']}: 段 {c['n_segs']} t±={c['signed_t']} 池 {len(c['pool'])} "
            f"线 {(c['line'] or {}).get('threshold')} ⇒ {c['verdict']}"
        )
    print(f"[OK] 写出 {out}")
    return 0


def main(argv: Optional[list[str]] = None, **injections: Any) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    if args.judgment_read:
        return _main_judgment(args, ap, argv, injections)
    if args.batch_id or args.print_config_hash:
        ap.error(
            "--batch-id/--print-config-hash 只用于 --judgment-read（挖掘模式不读判定段）"
        )
    args.cmdline = " ".join(sys.argv if argv is None else ["regime_style_study", *argv])
    report = run_study(args, **injections)
    if not report:
        print(
            "[FAIL] 两挖掘窗 0 合格段（或 0 票有数据）——空结果不写产物",
            file=sys.stderr,
        )
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"[OK] 写出 {out}")
    _print_summary(report)
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    rss = bt.peak_rss_mb()
    print(
        f"[MEM] 峰值 {f'{rss:.0f}MB' if rss else '未知'}（内存账见模块 docstring）",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
