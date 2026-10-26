# -*- coding: utf-8 -*-
"""Centralized path configuration for strategy_team.

All modules should import from here instead of hardcoding paths.
TDX_ROOT can be overridden via environment variables.

Also the single source of truth for "what day is it" — see cn_now/cn_today.
"""

from __future__ import annotations
import os
import sys
from datetime import date, datetime
from typing import Any, Optional
from pathlib import Path
from zoneinfo import ZoneInfo

# The market clock. Every date/time that ends up in a filename, an `as_of`
# field, or a freshness comparison must come from here.
#
# Modules used to call date.today() / datetime.now() (host timezone) while
# hardcoding "+08:00" into the strings they emitted. On a UTC host — which is
# what CI and most Linux boxes are — 08:50 CST is still the previous calendar
# day in UTC, so the same trading day's data split across two filenames and
# every as_of/stale comparison shifted by 8 hours.
CN_TZ = ZoneInfo("Asia/Shanghai")


def cn_now() -> datetime:
    """Timezone-aware current time in the exchange's timezone."""
    return datetime.now(CN_TZ)


def cn_today() -> date:
    """Current trading-calendar date in the exchange's timezone."""
    return cn_now().date()


# Project root: strategy_team/（paths.py 在 src/custos/core/ 下，向上三级）
BASE = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# 开发机挖掘段数据快照（TODO #116，设计稿 governance/data/MINING_SNAPSHOT_DESIGN.md）
#
# 设了 `CUSTOS_DATA_SNAPSHOT=<dir>` 即进入**快照模式**：DATA 与 TDX_ROOT 单点
# 改道到快照目录（镜像生产机的 data/ 与 vipdoc/ 布局），其余模块零感知。
# 快照只含 挖掘A（≤2016-12-31）∪ 预热（2020-10-01~2021-12-31）∪ 挖掘B
# （≤2026-09-04）——判定段（2017~2021）与前向 holdout（≥2026-09-05）**物理上
# 不出生产机**。配套硬闸：record_use / judgment_batch / live 五入口在快照模式
# 一律拒（见各模块顶部 TODO #116 注记），加载器读到禁带日期 ⇒ 报错
# （snapshot_bar_violation，防「把完整 vipdoc 误当快照用」的污染）。
#
# ⚠️ 只对**读**改道；写方（write_json 等）不特殊处理——live 写链已被硬闸拦在入口。
# ⚠️ L0 约束：SNAPSHOT_GAP/SNAPSHOT_BAR_MAX 是 research/load_window 的
#    SEG_MINING_A[1]/SEG_MINING_B[1] 的同值**镜像**（本模块不许 import research），
#    两边相等由 tests/test_snapshot_mode.py 钉住。
# ---------------------------------------------------------------------------
SNAPSHOT_ROOT: Optional[Path] = (
    Path(os.environ["CUSTOS_DATA_SNAPSHOT"].strip()).resolve()
    if os.environ.get("CUSTOS_DATA_SNAPSHOT", "").strip()
    else None
)

#: 快照禁带（双开端点）：判定段 + 判定段至预热段之间的断口。
SNAPSHOT_GAP = ("2016-12-31", "2020-10-01")
#: 快照允许的最后 bar 日期（= 挖掘 B 段末）；≥ 2026-09-05 是前向 holdout。
SNAPSHOT_BAR_MAX = "2026-09-04"

# Data directories
DATA = SNAPSHOT_ROOT / "data" if SNAPSHOT_ROOT else BASE / "data"
GOVERNANCE = BASE / "governance"
TOOLS = BASE / "src" / "custos"

# artifacts/ —— 2026-08-11 目录重组：原 03_daily_plans + 04_reviews + 06_logs 三合为一。
ARTIFACTS = BASE / "artifacts"
REPORTS = ARTIFACTS / "reports"
DAILY_REPORTS = REPORTS / "daily"  # 每日计划与日报
ARTIFACT_LOGS = ARTIFACTS / "logs"  # 运行日志与诊断输出

PLANS = DAILY_REPORTS
REVIEWS = REPORTS
LOGS = ARTIFACT_LOGS


def daily_report_dir(day: str, base: Path | None = None) -> Path:
    """单日报告目录：``{base}/{day}/``（``base`` 默认 ``DAILY_REPORTS``）。

    2026-08-12 起废除 `_supporting/` 双套结构：当天的全部报告（日报/择时评分/
    题材/1445/盘后复盘/候选表…）都归这一天目录；模板 `DAILY_PLAN_TEMPLATE.md`
    留在根。文件名保留 `{day}_` 前缀；三份日报告另带时点标记（v0.141）：
    `{day}_0905_daily_report.md` / `{day}_1700_final_review.md|.json` /
    `{day}_1800_candidate_table.md`。

    ⚠️ 调用方传**自己模块的路径常量**作 ``base``（如 `daily_report_dir(date, PLANS)`
    ——注意传的是调用方模块 globals 里的那个名字）：测试靠 monkeypatch 模块常量
    改道 tmp（见 test_pipeline_orchestration「必须 patch 全部路径常量」的事故
    注释），直接读这里的 DAILY_REPORTS 会绕过改道、写进真实仓库。
    本函数只拼路径不建目录；写方负责 `mkdir(parents=True, exist_ok=True)`。
    """
    return (base if base is not None else DAILY_REPORTS) / str(day)


# src/custos/pipeline/ 下的 stage 子目录 —— 编排层用它拼 subprocess 命令。
# ⚠️ 必须在这里定义：`daily_pipeline` 曾自己拼 `TOOLS / "market_timing"`，
#    2026-08-07 把 holdings/ 从 market_timing/ 拆出时它没跟着改，
#    四个持仓 stage 全部指向不存在的文件（其中三个 required=True ⇒ 整链硬失败）。
MARKET_TIMING = TOOLS / "pipeline" / "market_timing"
HOLDINGS = TOOLS / "pipeline" / "holdings"  # 工具目录；持仓**数据**目录是 HOLDINGS_DIR
SCREENING = TOOLS / "pipeline" / "screening"

# Subdirectories under data/
HOLDINGS_DIR = DATA / "holdings"
MARKET_DIR = DATA / "market"
NEWS_DIR = DATA / "news"
QUALITY_DIR = DATA / "quality"
TRADES_DIR = DATA / "trades"
# 持仓止盈/止损计划（v0.82，core/trades/position_plans 读写）：买入成交导入时落盘，
# 补上「选股 stop_loss_ref 买入后丢失」的断裂链；消费方是 Phase C 的影子判定。
POSITION_PLANS_FILE = TRADES_DIR / "position_plans.json"
# 持仓计划影子判定台账（v0.310，pipeline/holdings/plan_shadow_ledger 唯一写
# 入口径）：append-only + 幂等 (date,code,stage)——#60 并轨判据的证据链。
PLAN_SHADOW_LEDGER = TRADES_DIR / "plan_shadow_observations.jsonl"
# 选股练习台账（TODO #101，append-only；唯一写入口 research.pick_practice.append_picks）
PICK_PRACTICE_LEDGER = TRADES_DIR / "pick_practice.jsonl"
SECTORS_DIR = DATA / "sectors"
# 板块指数日线 CSV（fetch_sector_index_history 落盘，{code}.csv 或 {code}.SH.csv）
SECTOR_INDEX_DIR = MARKET_DIR / "sector_index"
# 板块 → 成员股票映射（fetch_sector_index_history --members 落盘）
SECTOR_MEMBERS_FILE = MARKET_DIR / "sector_members.json"
RISK_DIR = DATA / "risk"
STOCK_POOL_DIR = DATA / "stock_pool"
CACHE_DIR = DATA / "cache"  # 采集缓存（如 tdx_affair 权息缓存）

# 复盘机器接口 JSON（v0.179 起）：日/周/月报告的 .md 是人读报告，留
# artifacts/reports/；同名的 .json 是跨链路消费的数据接口（次日 09:05 链、
# 周六周报链、复盘校验都按路径读它），归 data/review/ 与报告分层。
# 读方一律「新路径优先 + 旧路径（reports 下同位置）回退」——历史产物不搬。
REVIEW_DIR = DATA / "review"
REVIEW_WEEKLY_DIR = REVIEW_DIR / "weekly"
REVIEW_MONTHLY_DIR = REVIEW_DIR / "monthly"

# ---------------------------------------------------------------------------
# governance/ 的四个子目录（2026-08-06 分类重构）
#
# 按**生命周期**分，而不是按主题分——这是分开的理由：
#   strategy/   规则。人与 LLM 读；改动要进版本记录（根目录 CHANGELOG.md）
#   data/       数据层现状与接口能力。随数据源变动
#   research/   回测研究。只增，结论会被推翻
#   contracts/  契约 + 运行时配置。**代码直接依赖**，改错直接影响运行
#
# ⚠️ 配置文件此前与 22 份文档平铺在一起，其中 CN_TRADING_CALENDAR.json 有 7 处代码
# 依赖 —— 改文档和改代码依赖项的风险完全不对等，混放让人无法区分。
# 所有配置路径**只在这里定义一次**，模块不要再自己拼 `BASE / "governance" / ...`。
# ---------------------------------------------------------------------------
STRATEGY_DIR = GOVERNANCE / "strategy"
DATA_DOCS_DIR = GOVERNANCE / "data"  # 命名区别于 DATA(顶层 data/ 运行时数据)
RESEARCH_DIR = GOVERNANCE / "research"
CONTRACTS_DIR = GOVERNANCE / "contracts"

# 运行时配置（代码直接读）
SCREEN_FORMULA_REGISTRY_FILE = CONTRACTS_DIR / "SCREEN_FORMULA_REGISTRY.json"
EXIT_RULES_FILE = CONTRACTS_DIR / "EXIT_RULES.json"
RSS_SOURCE_REGISTRY_FILE = CONTRACTS_DIR / "RSS_SOURCE_REGISTRY.json"
RSS_FILTER_CONFIG_FILE = CONTRACTS_DIR / "RSS_FILTER_CONFIG.json"
RSSHUB_ROUTES_FILE = CONTRACTS_DIR / "RSSHUB_PRIVATE_ROUTE_CANDIDATES.json"
B1_DIR = STRATEGY_DIR / "b1"  # B1 主策略上下文
CZ_DIR = STRATEGY_DIR / "cz"  # CZ 辅策略上下文
FACTORS_DIR = STRATEGY_DIR / "_factors"  # 跨策略可复用因子
STRATEGY_REGISTRY_FILE = STRATEGY_DIR / "STRATEGY_REGISTRY.json"

# TongDaXin installation root (overridable via env)
# 快照模式（TODO #116）下指向快照根（vipdoc/ 镜像在其下）；否则取 TDX_ROOT 环境变量。
TDX_ROOT = (
    SNAPSHOT_ROOT
    if SNAPSHOT_ROOT
    else Path(os.environ.get("TDX_ROOT", r"E:\new_tdx64"))
)

# TDX sub-paths
TDX_PYPLUGINS = TDX_ROOT / "PYPlugins" / "user"

# Calendar
CALENDAR_FILE = CONTRACTS_DIR / "CN_TRADING_CALENDAR.json"

# 相对项目根的路径，供**需要注入 base 的调用方**用（如 weekly_review 为便于测试
# 接受 base 参数）。它们仍以这里为唯一来源——不要在模块里自己拼 "governance"。
CALENDAR_RELPATH = Path("governance") / "contracts" / "CN_TRADING_CALENDAR.json"


def read_json(path: Path, default: Any) -> Any:
    """读 JSON，文件不存在时返回 default。

    ⚠️ **编码必须是 `utf-8-sig`。** 2026-08-06 收敛 4 份重复实现时发现它们编码不一致：
    `trading_calendar` 用 `utf-8-sig`，而 `runtime_guards` / `daily_report` /
    `generate_risk_and_sectors` 用 `utf-8` —— **后者遇到带 BOM 的文件会解析失败**
    （BOM 被当成内容字符）。`utf-8-sig` 对无 BOM 文件同样正常，所以统一取它。

    这就是「统一重复实现时不能只看形状相同」的实例：四份代码长得几乎一样，
    但其中一份**修过一个别人没修的 bug**。合并时若取多数派的写法，等于把修复回退了。

    ⚠️ 放在 paths.py 是**权衡**：这四个调用方都已 import paths，
    新建模块会让它们各多一个依赖；代价是 paths 从「纯路径」变成「路径 + 基础工具」
    （它此前已有 cn_today / cn_now）。
    """
    import json as _json

    return (
        _json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default
    )


def write_json(path, obj, *, indent: int = 2) -> None:
    """写 JSON 产物：建父目录 + `ensure_ascii=False` + **`allow_nan=False`**。

    ⚠️ `allow_nan=False` 是这个函数存在的**主要理由**。收敛 3 份 `dump()` 时发现
    只有两份带它（`postclose_news_digest` / `rss_filter`），
    `generate_risk_and_sectors` 没带 —— 而它写的正是 RiskDecision 与 SectorState。

    不带这个参数时 `json.dumps` 会把 NaN 写成 `NaN`、inf 写成 `Infinity`，
    **两者都不是合法 JSON**（RFC 7159）。危害不止「格式不对」：

        sector_state[].score = NaN  →  下游 `score >= 60` 判定里 `nan >= 60` 恒为 False
                                    ⇒ 板块**静默降级**成「观察」，且没有任何告警

    带上它则在写入时就**当场崩**，把静默错误变成显式失败 —— 这是想要的方向。

    需要原子性（累积状态 / 读-改-写共享文件）时用 `write_json_atomic`。
    """
    import json as _json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _json.dumps(obj, ensure_ascii=False, indent=indent, allow_nan=False),
        encoding="utf-8",
    )


def write_json_atomic(path, obj, *, indent: int = 2) -> None:
    """原子写 JSON：先写同目录 tmp，再 `os.replace` 换名。

    **只有两类写入需要它**（其余用普通 write_text 就好，别过度原子化）：

        ① **累积状态** —— 损坏就丢历史，无法从当日数据重建。
           例：`0amv_regime_history.json`（全历史 regime）、
               `position_confirmations.json`（人工确认记录）。
        ② **读-改-写的共享文件** —— 多个 stage 依次改写同一份。
           例：`{date}_market_timing_input.json` 被 collector → merge → amv_state
               依次读改写；写坏要重跑整链，而**盘中快照那种数据窗口已经过去、
               重跑也拿不回**。

    纯产物（报告、run log、门控 JSON、采集输出）不必原子化：下次跑会重写。

    与 `write_json` 一样带 **`allow_nan=False`**：NaN/Infinity 不是合法 JSON，
    带它在写入时当场崩，把静默错误变成显式失败（理由见 `write_json` 的 docstring，
    原子版写的恰恰是累积状态，更经不起静默降级）。
    `default=str` 只兜**不可序列化的类型**（date 等），管不到 NaN ——
    float 的 NaN 本来「可序列化」，没有 `allow_nan=False` 就被写成 `NaN` 混过去了。

    为什么单独抽出来：`trades/incremental_ledger` 早就有一份私有 `_write_atomic`
    （它踩过「持仓已加、台账没记 ⇒ 下次导入重复计账、持仓静默翻倍」的真实缺陷），
    但 `amv_state` / `merge_incremental_market` 这些同样是累积/读改写的地方还在裸写。
    2026-08-06 收敛到一处。
    """
    import json as _json
    import os as _os

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        _json.dumps(
            obj, ensure_ascii=False, indent=indent, allow_nan=False, default=str
        ),
        encoding="utf-8",
    )
    _os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 快照模式运行时查询（TODO #116）
#
# 下面这些 helper **都读模块全局 SNAPSHOT_ROOT**（而不是缓存 bool）——测试靠
# ``monkeypatch.setattr(paths, "SNAPSHOT_ROOT", tmp)`` 就地切换模式。
# ---------------------------------------------------------------------------


def snapshot_root() -> Optional[Path]:
    """快照根目录；非快照模式 ⇒ None。"""
    return SNAPSHOT_ROOT


def _d8n(d: object) -> str:
    """日期归一为 8 位数字串（剥掉非数字）——"2016-12-31" 与 "20161231" 同值。

    为什么不用 ``str(d)[:10]``：带横杠与不带横杠混比时边界日会错——
    "20161231" > "2016-12-31"（"1" 的码点大于 "-"），挖掘 A 最末日会被误判进
    禁带。数字串比较无此问题。
    """
    return "".join(ch for ch in str(d) if ch.isdigit())[:8]


def is_snapshot_mode() -> bool:
    """是否处于快照模式（CUSTOS_DATA_SNAPSHOT 已设置）。"""
    return SNAPSHOT_ROOT is not None


# MANIFEST 读取缓存（键 = str(root)；测试换 root 时键不同 ⇒ 缓存自然失效）
# 与 WARN 去重集（同一 root 缺清单只告警一次，不淹没日志）。
_MANIFEST_CACHE: dict[str, dict] = {}
_MANIFEST_WARNED: set[str] = set()


def snapshot_manifest() -> dict:
    """读 ``<root>/MANIFEST.json``；缺失/损坏 ⇒ {}（stderr WARN 一次）。"""
    root = SNAPSHOT_ROOT
    if root is None:
        return {}
    key = str(root)
    if key in _MANIFEST_CACHE:
        return _MANIFEST_CACHE[key]
    import json as _json

    manifest: dict = {}
    ok = False
    path = root / "MANIFEST.json"
    try:
        obj = _json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            manifest, ok = obj, True
    except Exception:  # noqa: BLE001 — 缺失/损坏按「无清单」处理（fail-soft：banner 已标 无MANIFEST）
        pass
    if not ok and key not in _MANIFEST_WARNED:
        _MANIFEST_WARNED.add(key)
        print(f"[WARN] 快照 MANIFEST.json 缺失或损坏: {path}", file=sys.stderr)
    _MANIFEST_CACHE[key] = manifest
    return manifest


def snapshot_manifest_sha() -> Optional[str]:
    """MANIFEST 顶层的 ``manifest_sha``（导出侧算好的全量指纹；不重算）。"""
    sha = snapshot_manifest().get("manifest_sha")
    return str(sha) if sha else None


def snapshot_banner() -> str:
    """快照模式启动横幅（纯函数，文案由测试钉住）；非快照模式 ⇒ 空串。"""
    root = SNAPSHOT_ROOT
    if root is None:
        return ""
    sha = snapshot_manifest_sha() or "无MANIFEST"
    line = (
        f"[SNAPSHOT] {root} sha={sha} 段=挖掘A∪预热∪挖掘B（判定段/holdout 不在快照内）"
    )
    if not root.is_dir():
        line += "（⚠️ 目录不存在）"
    return line


def snapshot_bar_violation(dates) -> Optional[str]:
    """K 线日期禁带检查（快照污染兜底）：任一日期落禁带 ⇒ 错误文案，否则 None。

    禁带 = (2016-12-31, 2020-10-01) ∪ ≥ 2026-09-05（判定段/holdout 不在快照内）。
    预热段 [2020-10-01, 2021-12-31] 是定案豁免——读到合法（挖掘 B 起点的指标
    预热只读价格），把它当信号窗由 load_window.mining_violation 另闸拒绝。
    日期经 ``_d8n`` 归一为数字串再比（横杠/纯数字两种形态边界日都精确）。
    """
    gap0, gap1, vmax = (
        _d8n(SNAPSHOT_GAP[0]),
        _d8n(SNAPSHOT_GAP[1]),
        _d8n(SNAPSHOT_BAR_MAX),
    )
    for d in dates:
        s = _d8n(d)
        if gap0 < s < gap1 or s > vmax:
            return (
                f"含禁带日期 {str(d)[:10]}"
                f"（快照只含 挖掘A≤{SNAPSHOT_GAP[0]}"
                f" ∪ 预热{SNAPSHOT_GAP[1]}~2021-12-31 ∪ 挖掘B≤{SNAPSHOT_BAR_MAX}；"
                "禁带=[2017-01-01,2020-09-30]∪≥2026-09-05）"
            )
    return None


#: PIT 快照禁带（review #2）：PIT 增量从 2020-01-01 起（挖掘 B 起点 as-of TTM
#: 需前两期公告），比 K 线预热段（2020-10-01）更早——与导出侧 ``_keep_pit``
#: 同值的镜像（tests/test_snapshot_mode.py 钉住）。
SNAPSHOT_PIT_GAP = ("2016-12-31", "2020-01-01")


def snapshot_pit_violation(dates) -> Optional[str]:
    """PIT notice_date 禁带检查：(2016-12-31, 2020-01-01) ∪ ≥ 2026-09-05 ⇒ 文案。"""
    gap0, gap1, vmax = (
        _d8n(SNAPSHOT_PIT_GAP[0]),
        _d8n(SNAPSHOT_PIT_GAP[1]),
        _d8n(SNAPSHOT_BAR_MAX),
    )
    for d in dates:
        s = _d8n(d)
        if s and (gap0 < s < gap1 or s > vmax):
            return (
                f"PIT 含禁带 notice_date {str(d)[:10]}（快照只含 ≤{SNAPSHOT_PIT_GAP[0]}"
                f" ∪ {SNAPSHOT_PIT_GAP[1]}~{SNAPSHOT_BAR_MAX}）"
            )
    return None


def snapshot_event_violation(dates) -> Optional[str]:
    """事件日上限检查（xdxr 权息 / share_changes 股本）：事件日 > 2026-09-04 ⇒
    文案。判定段年份的事件**合法**（前复权与 as-of 股本都需要它们），只禁
    holdout。空/无日期字段的记录不参与（``_d8n`` 归一出空串即跳过）。"""
    vmax = _d8n(SNAPSHOT_BAR_MAX)
    for d in dates:
        s = _d8n(d)
        if s and s > vmax:
            return (
                f"事件日晚于快照截止 {str(d)[:10]}"
                f"（> {SNAPSHOT_BAR_MAX}，holdout 泄漏）"
            )
    return None


# import 时打一行横幅（仅快照模式）——让任何进程一启动就知道自己读的是快照。
if SNAPSHOT_ROOT is not None:
    print(snapshot_banner(), file=sys.stderr)
