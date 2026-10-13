# -*- coding: utf-8 -*-
"""研究宇宙单一来源（owner 2026-10-13 定案，TODO #89）——全量个股 + 信号日 as-of。

规则（`governance/research/README`「研究宇宙」）：

- 沪深京 A 股**个股**（不含指数/ETF/债券/B 股，判定与 live 同源——
  `code_utils.is_ashare_stock` / `ASHARE_STOCK_PREFIXES`）；
- **不冻结名单**：缺省 = 本地 vipdoc 全量枚举（``--codes-file`` 是显式钉死
  兼容通道——R40 及此前单元沿用 s3000 的复跑路径）；
- 信号日 as-of 资格 = 该日已满 ``UNIVERSE_MIN_BARS`` 根 K 线即可出信号
  （执行点 = backtest_factors 扫描起点 min_bars=60，与 live
  `universe.min_list_days: 60` 同源；深回看指标维持 available=False 降级语义）；
- **不过滤 ST**（live 按当前名称排除，无历史 ST 状态，as-of 复现不了——
  与 live 的已知差异）；
- 不设版本号：provenance 记宇宙规则 + n_codes + universe_sha256，跨运行
  累积的台账/轨迹池宇宙不一致即拒绝续跑；
- vipdoc 无退市股，幸存者偏差不变（R14）。
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from pathlib import Path
from typing import Any, Optional

#: 宇宙规则标识（provenance 记它而非版本号——名单随 vipdoc 变动，规则不变）。
UNIVERSE_RULE = "ashare_full_asof"

#: 信号日 as-of 资格下限：满 N 根 K 线即可出信号（= backtest_factors 扫描起点
#: min_bars 60，与 live universe.min_list_days: 60 同源——owner 2026-10-13 拍板）。
UNIVERSE_MIN_BARS = 60


def default_codes() -> list[str]:
    """缺省宇宙 = 本地 vipdoc 全量 A 股个股（规则构造，sorted 6 位裸码）。"""
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    return local_tdx_data.list_local_vipdoc_codes(ashare_only=True)


def sample_codes(all_codes: list[str], n: int, seed: int = 0) -> list[str]:
    """从全 A 代码列表随机抽 N 只（带 seed 可复现），用于代表性样本校准。

    n<=0 或 n>=总数 → 返回全部（去空、去重、排序）。**本体在此**（原
    backtest_factors.sample_codes——v0.356 下沉破环；bt 侧留逐位委托别名）。
    """
    codes = sorted({str(c).strip() for c in all_codes if str(c).strip()})
    if n <= 0 or n >= len(codes):
        return codes
    return sorted(random.Random(seed).sample(codes, n))


def resolve(args: Any, ap: argparse.ArgumentParser) -> list[str]:
    """解析股票宇宙（单一来源）：--codes-file（钉死，优先级最高）>
    --universe-local/--universe-sample > --codes > **缺省规则构造**（vipdoc 全量）。
    全部为空且规则构造也为空 ⇒ fail-closed（ap.error）。

    副作用：把 ``args.universe_meta`` 塞成 ``universe_meta(codes, rule)``——
    provenance.build 优先读它。stderr 的 [INFO] 行是 m2_stop_sweep 等扫描
    脚本的解析面，既有三行逐字保留；缺省路径新增一行。"""
    if args.codes_file:
        # 钉死宇宙：优先级最高，跳过所有抽样逻辑。
        # ⚠️ 为什么需要它：universe 来自 vipdoc 目录列举，会随通达信下载变动
        # （实测一轮扫描中 5535→5536）；`sample_codes(seed=0)` 的 seed 固定没用，
        # **被抽的池子变了** ⇒ 抽到的是另一组 1000 只。长时间扫描里各方案宇宙不同，
        # 跨方案比较就混进了「换了一批票」这个噪声。
        p = Path(args.codes_file)
        if not p.is_file():
            ap.error(f"--codes-file 不存在: {p}")
        codes = [
            ln.strip()
            for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        rule = "codes_file"
        print(
            f"[INFO] universe=codes_file({p.name}) {len(codes)} 只（**已钉死**）",
            file=sys.stderr,
        )
    elif args.universe_local or args.universe_sample > 0:
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

        if args.universe_local:
            base = local_tdx_data.list_local_vipdoc_codes()
            src = "local_vipdoc"
        else:
            base = local_tdx_data.get_stock_list()
            src = "online_get_stock_list"
        codes = (
            sample_codes(base, args.universe_sample, args.seed)
            if args.universe_sample > 0
            else list(base)
        )
        rule = (
            src if args.universe_sample <= 0 else f"{src}_sample{args.universe_sample}"
        )
        print(
            f"[INFO] universe={src} 共 {len(base)} 只，取 {len(codes)} 只（seed={args.seed}）",
            file=sys.stderr,
        )
    elif str(getattr(args, "codes", "") or "").strip():
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        rule = "codes"
    else:
        codes = default_codes()
        rule = UNIVERSE_RULE
        print(
            f"[INFO] universe={UNIVERSE_RULE} {len(codes)} 只"
            f"（规则构造：vipdoc 全量个股；信号日 as-of 满{UNIVERSE_MIN_BARS}根）",
            file=sys.stderr,
        )
    if not codes:
        ap.error(
            "宇宙为空：--codes-file / --universe-* / --codes 与缺省规则构造均无结果"
        )
    args.universe_meta = universe_meta(codes, rule)
    return codes


def universe_sha256(codes: list[str]) -> str:
    """解析后名单的指纹：sorted 去空裸码 "\\n" 连接的 sha256 前 16 位。

    哈希的是**解析出的名单**（与来源无关）——codes-file 与规则构造走同一
    函数，跨运行/续跑比较的是同一把尺；名单内容任一变化指纹即变。"""
    norm = sorted(str(c).strip() for c in codes if str(c).strip())
    return hashlib.sha256("\n".join(norm).encode("utf-8")).hexdigest()[:16]


def universe_meta(codes: list[str], rule: str) -> dict[str, Any]:
    """provenance/续跑守卫用的宇宙三件套（规则 + 数量 + 名单指纹）。"""
    return {
        "universe_rule": rule,
        "n_codes": len(codes),
        "universe_sha256": universe_sha256(codes),
    }


def main(argv: Optional[list[str]] = None) -> int:
    """诊断入口：打印缺省宇宙解析结果（规则/数量/指纹；不跑研究）。"""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--codes-file", default="", help="显式钉死宇宙文件（兼容通道）")
    args = ap.parse_args(argv)
    ns = argparse.Namespace(
        codes_file=args.codes_file,
        universe_local=False,
        universe_sample=0,
        seed=0,
        codes="",
    )
    codes = resolve(ns, ap)
    print(universe_meta(codes, ns.universe_meta["universe_rule"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
