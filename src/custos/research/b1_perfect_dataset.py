# -*- coding: utf-8 -*-
"""完美 B1 正例数据集接入与审计（R36 Phase 0，owner 拍板方向的数据底座）。

数据：`/home/gh/agent/ZGNB/B1_DATA/` 10 个 CSV（``{code}-{start}-{end}.csv``，
列 ``Date,Code,Amount,Close,ForwardFactor,High,Low,Volume,Open``，首行可能有
BOM），~61-78 行 ≈ 3 个月日线，**最后一天是买点**（owner 口径）。

⚠️ 方法论红线（R 系五轮证伪的教训，R36 预注册写死）：这 10 个正例点是
**发现级材料（L1）**——全部 2025 年案例（单一近期 regime）+「最后一天=买点」
是事后标注（选择偏差，必须明示）；它们只用于发现（假设生成/特征提案/诊断），
**任何验证必须切换到全宇宙双窗交易语义**，结论不许落在这 10 个点上。

列约定：``date/open/high/low/close/volume/amount``（小写，与
``backtest_factors._load_bars_local`` 及 ``evolution/expr_dsl.BASE_VARIABLES``
对齐——bars 可直接喂 ``ic_eval.score_frame`` / ``expr_dsl.evaluate`` 在这些
案例上算 DSL 特征；``amount`` 附带（expr_dsl 白名单外，不影响）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from custos.core.code_utils import price_limit_pct

DEFAULT_DATA_DIR = Path("/home/gh/agent/ZGNB/B1_DATA")

#: 大写列名 → 我们的小写 bars 约定（ForwardFactor 附带保留为 forward_factor，
#: 全 0.0 的复权因子列——不映射进 bars 约定，防误用；审计也不读它）。
_COLUMN_MAP = {
    "Date": "date",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
    "Amount": "amount",
}
_REQUIRED_COLS = tuple(_COLUMN_MAP)

_FILE_RE = re.compile(
    r"^(?P<full>(?P<code>\d{6})\.(?P<suffix>SH|SZ))-(?P<start>\d{8})-(?P<end>\d{8})\.csv$"
)


@dataclass(frozen=True)
class PerfectB1Case:
    """一个完美 B1 正例（窗口日线 + 买点标注）。frozen——只读材料。

    ⚠️ 案例身份（owner 2026-09-16 拍板）：**(code, buy_date) 二元组，权威且仅此**；
    ``bars`` 是 owner 截取的 CSV 材料片段（~3 个月），留存作参考，**不再是观察窗
    定义**——观察窗自由（买点前任意周期，数据允许为限），评估用 bars 走
    ``resolve_bars`` 解析（provider 全历史优先，excerpt 回退）。
    """

    code: str  # 6 位数字代码（去后缀，与全项目 bars 键一致）
    code_full: str  # 原始带后缀（如 002074.SZ；板块/涨跌幅制度判定用）
    start: str  # 窗口首根 YYYY-MM-DD（文件名口径）
    end: str  # 窗口末根 YYYY-MM-DD（文件名口径）
    buy_date: str  # 买点 = 最后交易日（bars 末根；owner 口径，事后标注）
    bars: pd.DataFrame  # 小写列 date/open/high/low/close/volume/amount（CSV 片段）
    n_bars: int

    @property
    def excerpt_bars(self) -> pd.DataFrame:
        """CSV 片段的显式别名（原名 ``bars`` 语义保留；身份口径见类 docstring）。"""
        return self.bars


def _norm_code(code_full: str) -> str:
    """002074.SZ → 002074（去后缀；code_utils.market_of 的前缀口径兼容）。"""
    return code_full.split(".", 1)[0].zfill(6)


def load_cases(dir: Path | str = DEFAULT_DATA_DIR) -> list[PerfectB1Case]:
    """加载全部案例 CSV（fail-closed：坏目录/坏文件名/缺列/坏行一律 ValueError）。

    列校验：必需列缺一即拒（报文件名与缺列清单）；Code 列与文件名代码不一致
    → ValueError（张冠李戴的拼接文件不允许混进发现材料）；date 排序+无重复
    （发现材料的时序完整性是后续一切计算的底座）。
    """
    root = Path(dir)
    if not root.is_dir():
        raise ValueError(f"案例目录不存在: {root}")
    files = sorted(root.glob("*.csv"))
    if not files:
        raise ValueError(f"案例目录 0 个 CSV: {root}（数据未就位，拒跑 Phase 0）")
    cases: list[PerfectB1Case] = []
    for p in files:
        m = _FILE_RE.match(p.name)
        if not m:
            raise ValueError(
                f"文件名形态非法: {p.name}（须 {{code}}-{{start}}-{{end}}.csv）"
            )
        df = pd.read_csv(p, encoding="utf-8-sig")
        missing = [c for c in _REQUIRED_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"{p.name} 缺列: {missing}（实际列: {list(df.columns)}）")
        df = df.rename(columns=_COLUMN_MAP)
        for col in ("open", "high", "low", "close", "volume", "amount"):
            df[col] = pd.to_numeric(df[col], errors="raise")
        df["date"] = pd.to_datetime(df["date"], errors="raise")
        if not len(df):
            raise ValueError(f"{p.name} 0 数据行")
        if df["date"].duplicated().any():
            raise ValueError(f"{p.name} 存在重复交易日")
        df = df.sort_values("date").reset_index(drop=True)
        codes_in_file = {str(c) for c in df["Code"].unique()}
        if codes_in_file != {m.group("full")}:
            raise ValueError(
                f"{p.name} Code 列 {sorted(codes_in_file)} 与文件名 {m.group('full')} 不一致"
            )
        start, end = m.group("start"), m.group("end")
        buy_date = str(df["date"].iloc[-1].date())
        if buy_date != f"{end[:4]}-{end[4:6]}-{end[6:]}":
            raise ValueError(
                f"{p.name} 末根交易日 {buy_date} 与文件名窗口末根 {end} 不一致"
                "（买点口径=最后一天，数据与文件名对不上即拒收）"
            )
        cases.append(
            PerfectB1Case(
                code=_norm_code(m.group("full")),
                code_full=m.group("full"),
                start=f"{start[:4]}-{start[4:6]}-{start[6:]}",
                end=f"{end[:4]}-{end[4:6]}-{end[6:]}",
                buy_date=buy_date,
                bars=df[["date", "open", "high", "low", "close", "volume", "amount"]],
                n_bars=len(df),
            )
        )
    return cases


# ---------------------------------------------------------------------------
# 观察窗解析（owner 2026-09-16 拍板口径：案例=(code, buy_date)，观察窗自由）
# ---------------------------------------------------------------------------

#: provider 契约：``Callable[[str], DataFrame | None]``——按 code 取该股**尽量长**
#: 的历史日线（生产机 = local_tdx 全历史 loader；None/空帧 → 回退 excerpt）。
BarsProvider = Any  # Callable[[str], pd.DataFrame | None]


def resolve_bars(
    case: PerfectB1Case, provider: BarsProvider = None
) -> tuple[pd.DataFrame, str]:
    """解析案例的评估用 bars：provider 全历史优先，excerpt 回退。

    - provider 给出非空帧 → **物理截到 buy_date（含当日）** 后返回，
      ``bars_source="provider"``（观察窗 = 买点前任意周期，数据允许为限；
      截断是防未来函数纪律——买点之后的数据不得进入任何求值）；
    - provider 为 None / 返回 None / 空帧 → 回退 CSV 片段，
      ``bars_source="excerpt"``——口径差异如实写明：excerpt 只有 ~3 个月，
      **观察窗受限是数据妥协不是设计**（案例窗口不再定义观察窗）。
    """
    if provider is not None:
        df = provider(case.code)
        if df is not None and len(df):
            d = df[df["date"].astype(str).str[:10] <= case.buy_date]
            if len(d):
                return d.reset_index(drop=True), "provider"
    return case.excerpt_bars, "excerpt"


def audit_cases(cases: list[PerfectB1Case]) -> dict[str, Any]:
    """Phase 0 审计报告（dict，研究产物，不进 live）。

    每案例一行：行数/买卖日/窗口首尾收盘/涨幅（首根→买点）/买点当日特征
    （涨跌幅 vs 前收、量比 vs 前 5 日均量、是否涨停附近——涨停附近=当日涨幅
    ≥ 涨跌幅制度 −0.5pp，制度用 code_utils.price_limit_pct 唯一来源）。
    """
    if not cases:
        raise ValueError("0 案例无可审计（load_cases 的护栏不该放它走到这）")
    per_case: list[dict[str, Any]] = []
    for c in cases:
        bars = c.bars
        first_close = float(bars["close"].iloc[0])
        buy_close = float(bars["close"].iloc[-1])
        prev_close = float(bars["close"].iloc[-2]) if c.n_bars >= 2 else float("nan")
        gain_pct = (buy_close / first_close - 1) * 100 if first_close else float("nan")
        buy_chg = (buy_close / prev_close - 1) * 100 if prev_close else float("nan")
        vol_tail = bars["volume"].iloc[-6:-1]
        buy_vr5 = (
            float(bars["volume"].iloc[-1] / vol_tail.mean())
            if len(vol_tail) >= 1 and float(vol_tail.mean())
            else float("nan")
        )
        limit = price_limit_pct(c.code)
        per_case.append(
            {
                "code": c.code,
                "code_full": c.code_full,
                "start": c.start,
                "end": c.end,
                "n_bars": c.n_bars,
                "buy_date": c.buy_date,
                "first_close": round(first_close, 4),
                "buy_close": round(buy_close, 4),
                "gain_pct": round(gain_pct, 1),
                "buy_day_change_pct": round(buy_chg, 2),
                "buy_vol_ratio5": round(buy_vr5, 2),
                "buy_near_limit": bool(
                    buy_chg == buy_chg and buy_chg >= limit - 0.5
                ),  # NaN 安全：NaN>=x 恒 False
                "price_limit": limit,
            }
        )
    gains = [r["gain_pct"] for r in per_case]
    return {
        "n_cases": len(per_case),
        "regime_note": "全部 2025 年案例（2025-03~2025-09，单一近期 regime）",
        "label_note": "最后一天=买点是事后标注（选择偏差）——只作发现材料（L1）",
        "per_case": per_case,
        "all_gain_positive": all(g > 0 for g in gains),
        "min_gain_pct": min(gains),
        "max_gain_pct": max(gains),
        "buy_day_negative_count": sum(
            1 for r in per_case if r["buy_day_change_pct"] < 0
        ),
        "buy_near_limit_count": sum(1 for r in per_case if r["buy_near_limit"]),
    }


# ---------------------------------------------------------------------------
# 审计打印小面（TOOLS 登记用——research/ 顶层每个 .py 都必须是可发现入口）
# ---------------------------------------------------------------------------


def _build_parser():
    """⚠️ add_argument 定义必须留在**本文件**内（`_modes()` 用 AST 抽取）。"""
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(
        description="完美 B1 正例数据集 Phase 0 审计打印（R36 数据底座；"
        "发现级材料 L1——后视标注，验证走全宇宙双窗，详见 R36 预注册）"
    )
    ap.add_argument(
        "--dir",
        default=str(DEFAULT_DATA_DIR),
        help=f"案例目录（默认 {DEFAULT_DATA_DIR}）",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    rep = audit_cases(load_cases(args.dir))
    print(
        f"完美 B1 正例审计：{rep['n_cases']} 例｜{rep['regime_note']}｜{rep['label_note']}"
    )
    print(
        f"涨幅：全正={rep['all_gain_positive']}（{rep['min_gain_pct']:+.1f}%~"
        f"{rep['max_gain_pct']:+.1f}%）｜买点当日收跌 {rep['buy_day_negative_count']}/"
        f"{rep['n_cases']}｜涨停附近 {rep['buy_near_limit_count']}/{rep['n_cases']}"
    )
    for r in rep["per_case"]:
        print(
            f"  {r['code']} {r['start']}~{r['buy_date']} n={r['n_bars']} "
            f"{r['first_close']}→{r['buy_close']} {r['gain_pct']:+.1f}% "
            f"当日{r['buy_day_change_pct']:+.2f}% 量比{r['buy_vol_ratio5']:.2f} "
            f"{'⚡涨停附近' if r['buy_near_limit'] else ''}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
