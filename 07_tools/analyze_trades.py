# -*- coding: utf-8 -*-
"""交易记录复盘分析。

从 master_trade_ledger.csv 读取交易流水，从 closed_positions.json 和
current_positions.json 读取持仓/清仓数据，输出多维度复盘分析 Excel。

用法:
    uv run python -m tools.analyze_trades              # 默认输出到 04_reviews/
    uv run python -m tools.analyze_trades --preview    # 仅打印数据概览
    uv run python tools/analyze_trades.py              # 直接运行
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── 路径 ──────────────────────────────────────────────
from paths import BASE

TRADES_DIR = BASE / "01_data" / "trades"
REVIEWS_DIR = BASE / "04_reviews" / "trade_review"

LEDGER_CSV = TRADES_DIR / "master_trade_ledger.csv"
CLOSED_JSON = TRADES_DIR / "closed_positions.json"
POSITIONS_JSON = TRADES_DIR / "current_positions.json"

# JSON 文件中字段名的中文映射（原始 JSON 可能存在 GBK 乱码）
# 通过 CSV 的列名作为权威来源，JSON 用位置索引解析
CLOSED_FIELDS = [
    "清仓日期", "代码", "名称", "总盈亏", "盈亏比",
    "同期大盘", "跑赢大盘", "买入均价", "卖出均价",
    "清仓距今", "持仓天数", "交易费用", "建仓日期",
]
POS_FIELDS = [
    "代码", "名称", "持有金额", "当日盈亏", "当日盈亏率",
    "关联板块", "板块涨幅", "组合盈亏", "组合涨幅",
    "持有盈亏", "持有盈亏率", "累计盈亏", "累计盈亏率",
    "本周盈亏", "本月盈亏", "今年盈亏", "仓位占比",
    "持有数量", "持仓天数", "最新涨幅", "最新价",
    "单位成本", "回本涨幅", "近1月涨幅", "近3月涨幅",
    "近6月涨幅", "近1年涨幅",
]


def load_closed() -> pd.DataFrame:
    """加载已清仓数据。JSON 可能乱码，用字段列表映射。"""
    if not CLOSED_JSON.exists():
        return pd.DataFrame()
    raw = json.loads(CLOSED_JSON.read_text(encoding="utf-8"))
    if not raw:
        return pd.DataFrame()
    # 检测是否乱码：第一个 key 是否在已知字段中
    first_key = next(iter(raw[0])) if raw[0] else ""
    if first_key in CLOSED_FIELDS:
        df = pd.DataFrame(raw)
    else:
        # 乱码模式：按位置映射
        df = pd.DataFrame(raw)
        if len(df.columns) == len(CLOSED_FIELDS):
            df.columns = CLOSED_FIELDS
        # 如果列数不匹配，尝试按顺序尽可能映射
        else:
            mapping = {old: new for old, new in zip(df.columns, CLOSED_FIELDS)}
            df = df.rename(columns=mapping)
    return df


def load_positions() -> pd.DataFrame:
    """加载当前持仓数据。"""
    if not POSITIONS_JSON.exists():
        return pd.DataFrame()
    raw = json.loads(POSITIONS_JSON.read_text(encoding="utf-8"))
    if not raw:
        return pd.DataFrame()
    first_key = next(iter(raw[0])) if raw[0] else ""
    if first_key in POS_FIELDS:
        df = pd.DataFrame(raw)
    else:
        df = pd.DataFrame(raw)
        if len(df.columns) == len(POS_FIELDS):
            df.columns = POS_FIELDS
        else:
            mapping = {old: new for old, new in zip(df.columns, POS_FIELDS)}
            df = df.rename(columns=mapping)
    return df


def load_trades() -> pd.DataFrame:
    """加载交易流水 CSV（权威数据源，编码干净）。"""
    if not LEDGER_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(LEDGER_CSV, dtype={"代码": str})
    df["成交日期"] = pd.to_datetime(df["成交日期"], errors="coerce")
    for c in ["成交数量", "成交价格", "成交金额", "发生金额", "费用"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def to_num(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def to_dt(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


# ── 分析 ──────────────────────────────────────────────

def build_summary(closed: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """总览指标。"""
    rows = []

    def add(k, v):
        rows.append({"指标": k, "数值": v})

    if not closed.empty:
        pnl = closed["总盈亏"].dropna()
        add("已清仓笔数", len(closed))
        add("已清仓总盈亏", pnl.sum())
        add("已清仓盈利笔数", int((pnl > 0).sum()))
        add("已清仓亏损笔数", int((pnl < 0).sum()))
        add("已清仓胜率", (pnl > 0).mean() if len(pnl) else np.nan)
        add("平均单笔盈利", pnl[pnl > 0].mean() if (pnl > 0).any() else 0)
        add("平均单笔亏损", pnl[pnl < 0].mean() if (pnl < 0).any() else 0)
        avg_win = pnl[pnl > 0].mean() if (pnl > 0).any() else 0
        avg_loss = abs(pnl[pnl < 0].mean()) if (pnl < 0).any() else 0
        add("盈亏比", avg_win / avg_loss if avg_loss else np.nan)
        add("已清仓最大单笔盈利", pnl.max())
        add("已清仓最大单笔亏损", pnl.min())
        add("已清仓盈亏中位数", pnl.median())
        if "持仓天数" in closed:
            add("已清仓平均持仓天数", closed["持仓天数"].mean())
        if "跑赢大盘" in closed:
            add("已清仓跑赢大盘比例", (closed["跑赢大盘"] > 0).mean())
        if "交易费用" in closed:
            add("已清仓交易费用合计", closed["交易费用"].sum())

    if not positions.empty:
        hold_pnl = positions["持有盈亏"].dropna() if "持有盈亏" in positions else pd.Series(dtype=float)
        add("当前持仓数量", len(positions))
        if "持有金额" in positions:
            add("当前持仓市值合计", positions["持有金额"].sum())
        if len(hold_pnl):
            add("当前浮动盈亏合计", hold_pnl.sum())
            add("当前持仓盈利数量", int((hold_pnl > 0).sum()))
            add("当前持仓亏损数量", int((hold_pnl < 0).sum()))
        if "仓位占比" in positions:
            add("当前持仓最大仓位占比", positions["仓位占比"].max())
            add("当前持仓前三仓位合计", positions.sort_values("仓位占比", ascending=False)["仓位占比"].head(3).sum())

    return pd.DataFrame(rows)


def build_yearly(closed: pd.DataFrame) -> pd.DataFrame:
    """年度清仓表现。"""
    if closed.empty or "清仓日期" not in closed:
        return pd.DataFrame()
    df = closed.dropna(subset=["清仓日期"]).copy()
    df["年份"] = df["清仓日期"].dt.year
    return df.groupby("年份", dropna=True).agg(
        笔数=("代码", "count"),
        总盈亏=("总盈亏", "sum"),
        胜率=("总盈亏", lambda s: (s > 0).mean()),
        平均盈亏比=("盈亏比", "mean"),
        平均持仓天数=("持仓天数", "mean"),
        跑赢大盘比例=("跑赢大盘", lambda s: (s > 0).mean() if "跑赢大盘" in s else np.nan),
    ).reset_index()


def build_period(closed: pd.DataFrame) -> pd.DataFrame:
    """按持仓周期分组表现。"""
    if closed.empty or "持仓天数" not in closed:
        return pd.DataFrame()
    df = closed.copy()
    bins = [-1, 5, 20, 60, 120, 250, 99999]
    labels = ["≤5天", "6-20天", "21-60天", "61-120天", "121-250天", ">250天"]
    df["持仓周期"] = pd.cut(df["持仓天数"], bins=bins, labels=labels)
    return df.groupby("持仓周期", observed=False).agg(
        笔数=("代码", "count"),
        总盈亏=("总盈亏", "sum"),
        胜率=("总盈亏", lambda s: (s > 0).mean() if len(s) else np.nan),
        平均盈亏比=("盈亏比", "mean"),
        平均跑赢大盘=("跑赢大盘", "mean"),
    ).reset_index()


def build_flow(trades: pd.DataFrame) -> pd.DataFrame:
    """按年度和交易类别汇总交易流水。"""
    if trades.empty:
        return pd.DataFrame()
    df = trades.copy()
    df["年份"] = df["成交日期"].dt.year
    return df.groupby(["年份", "交易类别"], dropna=False).agg(
        次数=("交易类别", "count"),
        成交金额=("成交金额", "sum"),
        发生金额=("发生金额", "sum"),
        费用=("费用", "sum"),
    ).reset_index()


def build_top(closed: pd.DataFrame, n: int = 15, ascending: bool = False) -> pd.DataFrame:
    """盈亏 Top N。"""
    if closed.empty:
        return pd.DataFrame()
    cols = ["清仓日期", "代码", "名称", "总盈亏", "盈亏比", "跑赢大盘", "持仓天数"]
    cols = [c for c in cols if c in closed.columns]
    return closed.sort_values("总盈亏", ascending=ascending)[cols].head(n)


def build_hold_risk(positions: pd.DataFrame) -> pd.DataFrame:
    """当前持仓风险排序（按持有盈亏升序，亏损在前）。"""
    if positions.empty:
        return pd.DataFrame()
    cols = ["代码", "名称", "持有金额", "持有盈亏", "持有盈亏率", "仓位占比",
            "持仓天数", "本周盈亏", "本月盈亏", "今年盈亏"]
    cols = [c for c in cols if c in positions.columns]
    sort_col = "持有盈亏" if "持有盈亏" in positions else cols[0] if cols else None
    if sort_col:
        return positions.sort_values(sort_col, ascending=True)[cols]
    return positions[cols]


# ── 主流程 ────────────────────────────────────────────

def preview(trades, closed, positions):
    """打印数据概览。"""
    print("=== 交易流水 ===")
    print(f"  行数: {len(trades)}")
    if not trades.empty:
        print(f"  日期范围: {trades['成交日期'].min()} ~ {trades['成交日期'].max()}")
        print(f"  列: {list(trades.columns)}")
        print(trades.head(3).to_string(index=False))

    print("\n=== 已清仓 ===")
    print(f"  行数: {len(closed)}")
    print(f"  列: {list(closed.columns)}")
    if not closed.empty:
        print(closed.head(3).to_string(index=False))

    print("\n=== 当前持仓 ===")
    print(f"  行数: {len(positions)}")
    print(f"  列: {list(positions.columns)}")
    if not positions.empty:
        print(positions.head(3).to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description="交易记录复盘分析")
    parser.add_argument("--preview", action="store_true", help="仅打印数据概览，不输出 Excel")
    parser.add_argument("--output", type=str, default=None, help="输出 Excel 路径")
    args = parser.parse_args()

    trades = load_trades()
    closed = load_closed()
    positions = load_positions()

    # 类型转换
    if not closed.empty:
        closed = to_dt(closed, ["清仓日期", "建仓日期"])
        closed = to_num(closed, ["总盈亏", "盈亏比", "同期大盘", "跑赢大盘",
                                  "买入均价", "卖出均价", "持仓天数", "交易费用"])
        closed = closed[closed["代码"].notna()].copy()

    if not positions.empty:
        positions = to_num(positions, ["持有金额", "当日盈亏", "当日盈亏率",
                                        "持有盈亏", "持有盈亏率", "累计盈亏",
                                        "累计盈亏率", "本周盈亏", "本月盈亏",
                                        "今年盈亏", "仓位占比", "持有数量", "持仓天数"])
        positions = positions[positions["代码"].notna()].copy()

    if not trades.empty:
        trades = trades[trades["代码"].notna()].copy()

    if args.preview:
        preview(trades, closed, positions)
        return

    # 生成分析
    summary = build_summary(closed, positions)
    yearly = build_yearly(closed)
    period = build_period(closed)
    top_profit = build_top(closed, n=15, ascending=False)
    top_loss = build_top(closed, n=15, ascending=True)
    hold_risk = build_hold_risk(positions)
    flow = build_flow(trades)

    # 输出
    out_path = Path(args.output) if args.output else REVIEWS_DIR / "trade_review.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="总览", index=False)
        yearly.to_excel(w, sheet_name="年度清仓表现", index=False)
        period.to_excel(w, sheet_name="持仓周期表现", index=False)
        top_profit.to_excel(w, sheet_name="清仓盈利Top15", index=False)
        top_loss.to_excel(w, sheet_name="清仓亏损Top15", index=False)
        hold_risk.to_excel(w, sheet_name="当前持仓风险", index=False)
        flow.to_excel(w, sheet_name="交易流水汇总", index=False)

    print(f"输出: {out_path}")
    print("\n=== 总览 ===")
    print(summary.to_string(index=False))
    if not yearly.empty:
        print("\n=== 年度清仓表现 ===")
        print(yearly.to_string(index=False))
    if not period.empty:
        print("\n=== 持仓周期表现 ===")
        print(period.to_string(index=False))
    if not top_loss.empty:
        print("\n=== 清仓亏损 Top10 ===")
        cols = [c for c in ["清仓日期", "代码", "名称", "总盈亏", "盈亏比", "持仓天数"] if c in top_loss.columns]
        print(top_loss[cols].head(10).to_string(index=False))
    if not hold_risk.empty:
        print("\n=== 当前持仓风险 ===")
        print(hold_risk.to_string(index=False))


if __name__ == "__main__":
    main()
