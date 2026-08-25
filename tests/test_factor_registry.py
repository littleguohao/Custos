"""因子注册表与**「待优化不得进 live」**约束。

2026-08-06 把 `backtest_factors` 里 9 个自包含 scorer 抽成 `factors/` 下各自的模块，
每个模块声明 `FACTOR` 元数据（模板见 `factors/_template.py`）。

⚠️ **元数据里最要紧的是 `status`。**
它把 R2「选股章节正式关闭、所有价量选择器证伪」这个结论**变成机器可执行的约束** ——
否则半年后有人看到 `alpha101` 就拿去用了，而文档里那条否决没人会重读。

抽取原则同前两轮：**零行为变化**，逐个用数值等价验证（见 TestNumericEquivalence）。
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.core import factors  # noqa: E402

#: 从 `backtest_factors.SCORERS` 抽出的 9 个自包含 scorer
EXTRACTED = [
    "baseline",
    "alpha101",
    "alpha_pvcorr",
    "low_vol",
    "momentum",
    "reversal_quality",
    "reversal_quality_inv",
    "mcap",
    "kdj_j",
]

#: 从 `enrich_candidates` 抽出的 11 个内联因子（pattern/state，**不在 SCORERS 里**）
INLINE_EXTRACTED = [
    "wave_type",
    "perfect_b1_fit",
    "b1_pullback_fit",
    "distribution",
    # v0.86（因子化批 A）：check_macd_technics 及 _macd_* 族迁入 factors/，
    # enrich 保留同名 re-export（tests 的 monkeypatch 通道）。
    "macd_technics",
    # v0.86（因子化批 B）：量能族（volume_sustain/leader/bottom）+ 结构族
    # （non_one_wave/repair/five_day/liquidity/_stop_ref）+ 周线 J 迁入 factors/，
    # enrich 保留同名 re-export。
    "volume_detectors",
    "b1_structure",
    "weekly_j",
    # v0.86（因子化批 C）：点火族（ignition/pullback_shrink/b1_ignition 复合）、
    # patterns 五单项判定、J<13 进池硬门槛（gate 登记）迁入 factors/，
    # enrich 保留同名 re-export。
    "ignition",
    "entry_patterns",
    "j_low_gate",
]


def _bars(n=80, seed=5):
    rng = np.random.default_rng(seed)
    c = 10 + np.cumsum(rng.normal(0, 0.2, n))
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=n).astype(str),
            "open": c + rng.normal(0, 0.05, n),
            "close": c,
            "high": c + abs(rng.normal(0, 0.15, n)),
            "low": c - abs(rng.normal(0, 0.15, n)),
            "volume": abs(rng.normal(1e6, 2e5, n)),
        }
    )


class TestRegistry:
    def test_all_extracted_registered(self):
        reg = factors.registry()
        missing = [f for f in EXTRACTED if f not in reg]
        assert not missing, f"未注册：{missing}"

    def test_template_not_registered(self):
        assert "template" not in factors.registry(), "模板不该进注册表"

    @pytest.mark.parametrize("fid", EXTRACTED)
    def test_metadata_complete(self, fid):
        m = factors.registry()[fid]["meta"]
        for k in ("id", "name", "kind", "status", "note", "min_bars"):
            assert m.get(k) not in (None, ""), f"{fid} 缺元数据 {k}"
        assert m["status"] in factors.STATUSES
        assert m["kind"] in factors.KINDS

    @pytest.mark.parametrize("fid", EXTRACTED)
    def test_needs_work_must_cite_evidence(self, fid):
        """判「待优化」必须给出处 —— 否则下次有人会以为是拍脑袋否掉的。"""
        m = factors.registry()[fid]["meta"]
        if m["status"] == "needs_work":
            assert m["evidence"].startswith("governance/research/"), (
                f"{fid} 标 needs_work 但没给 research 出处"
            )
            assert (ROOT / m["evidence"]).exists(), f"{fid} 的 evidence 路径不存在"

    @pytest.mark.parametrize("fid", EXTRACTED)
    def test_selector_has_score(self, fid):
        e = factors.registry()[fid]
        if e["meta"]["kind"] in ("selector", "control"):
            assert callable(e["score"]), f"{fid} 是 selector 但没有 score()"


class TestNotForLive:
    """**待优化/未验证的因子不得进 live 选股链。**

    这是本次抽取最有价值的产出：把研究结论变成机器约束。
    """

    def test_live_allowed_excludes_needs_work(self):
        allowed = factors.live_allowed()
        for fid, e in factors.registry().items():
            if e["meta"]["status"] in factors.NOT_FOR_LIVE:
                assert fid not in allowed, (
                    f"{fid}({e['meta']['status']}) 不该在 live 白名单里"
                )

    def test_known_needs_work_are_marked(self):
        """R2/R3 明确否决过的这几个，状态必须是 needs_work。"""
        reg = factors.registry()
        for fid in (
            "alpha101",
            "reversal_quality",
            "reversal_quality_inv",
            "mcap",
            "kdj_j",
        ):
            assert reg[fid]["meta"]["status"] == "needs_work", (
                f"{fid} 在 research 里已被否决，状态却不是 needs_work"
            )

    def test_live_chain_only_uses_allowed_factors(self):
        """live 链可以**计算**待优化因子作证据，但不得让它**驱动决策**。

        ⚠️ 我的第一版守卫禁止 live 链 import 任何 `needs_work` 因子，
        **当场误报**：`enrich_candidates` 确实 import `b1_pullback_fit`，
        而 R2 的原话是「**仅描述性，不作买入依据**」—— 它落候选表供人看，
        不驱动分层/gate/排序。

        ⇒ `status`（证据够不够）与 `live_use`（允许怎么用）是**两个正交维度**，
        只看前者会把合法用法判成违规。
        """
        reg = factors.registry()
        live = [
            "pipeline/screening/enrich_candidates.py",
            "pipeline/screening/score_candidates.py",
            "pipeline/screening/candidate_table.py",
            "pipeline/screening/signal_labels.py",
        ]
        bad = []
        for rel in live:
            s = (ROOT / "src" / "custos" / rel).read_text(encoding="utf-8")
            for fid, e in reg.items():
                imported = f"import {fid}" in s or f"from {fid} import" in s
                if not imported:
                    continue
                use = e["meta"].get("live_use")
                if use == "none":
                    bad.append(
                        f"{rel} → {fid}（live_use=none，根本不该出现在 live 链）"
                    )
        assert not bad, f"live 链用了不该用的因子：{bad}"

    def test_every_factor_declares_live_use(self):
        for fid, e in factors.registry().items():
            use = e["meta"].get("live_use")
            assert use in factors.LIVE_USES, f"{fid} 的 live_use={use!r} 不合法"

    def test_needs_work_cannot_be_gate_or_scorer(self):
        """证据不够的因子不许被声明成 gate/scorer —— 那等于绕过 status 约束。"""
        for fid, e in factors.registry().items():
            m = e["meta"]
            if m["status"] in factors.NOT_FOR_LIVE:
                if fid in factors.KNOWN_STATUS_USE_CONFLICTS:
                    continue  # 已知矛盾，显式登记过（见白名单里的原因）
                assert m.get("live_use") in ("none", "evidence_only"), (
                    f"{fid} status={m['status']} 却声明 live_use={m.get('live_use')}"
                )

    def test_evidence_only_factors_are_the_documented_ones(self):
        """`evidence_only` 集合的每次变动都必须是有意识的决定。

        v0.50（#37 阶段 A）：+ s_shape（定案：移出分层、降为展示/证据列）
        + sector_phase（live 侧本就只是 enrich hint，live_use="gate" 自 v0.25
        起名不副实，随可买定义移出订正）；− b1_pullback_fit（证伪下线，
        转 debug/none）。
        v0.84（Phase D 因子化）：+ fundamentals（基本面 CZ 抄底代理 +
        fundamental_quality 品质档——只进 🐂 展示与四面共振基本面腿，
        不进分不驱动分层，行为不变）。
        """
        got = set(factors.live_evidence_only())
        assert got == {
            # R2 明确说「仅描述性，不作买入依据」的
            "perfect_b1_fit",
            "platform_pullback",
            # signal_labels 出标签落候选表；该模块头部已声明「标注不是交易依据，
            # 尤其不得据标注数决定仓位」
            "b1_dual_factor",
            "b2_surge_factor",
            "main_rally_factor",
            "rsi_state",
            # v0.50（#37 阶段 A）定案，见各自模块 FACTOR 注释
            "s_shape",
            "sector_phase",
            # v0.56：底部形态（W底/红肥绿瘦，25chuhuo 底部镜像）——证据层
            "bottom_patterns",
            # v0.84：基本面因子化（evidence_only，不进分，行为不变）
            "fundamentals",
        }, f"evidence_only 集合变了：{got}"


class TestNumericEquivalence:
    """抽取必须**零行为变化**：新 `score` 与原 `SCORERS[fid]` 逐点相同。"""

    @pytest.mark.parametrize("fid", [f for f in EXTRACTED if f != "mcap"])
    def test_matches_scorers_entry(self, fid):
        from custos.research import backtest_factors as BF

        df = _bars()
        assert fid in BF.SCORERS, f"{fid} 不在 SCORERS 里"
        assert factors.registry()[fid]["score"](df, "600000") == BF.SCORERS[fid](
            df, "600000"
        )

    def test_scorers_keys_unchanged(self):
        """SCORERS 的键集合不能因为重构而变 —— CLI `--scorer` 参数依赖它。"""
        from custos.research import backtest_factors as BF

        for fid in EXTRACTED:
            assert fid in BF.SCORERS


class TestSharedMutableStateImportRule:
    """持有**可变模块级状态**的模块必须包限定导入。

    ⚠️ 2026-08-06 当场发作：`src` 与 `src/custos/core/factors` 都在 sys.path 上 ⇒
    同一文件有两条可导路径（`_shares` / `factors._shares`），Python 建**两个模块对象**，
    而 `_shares` 持有可变缓存 `_SHARE_IDX` ⇒ 测试打桩一个、生产读另一个。
    """

    def test_shares_imported_package_qualified(self):
        for rel in ("core/factors/mcap.py", "research/backtest_factors.py"):
            s = (ROOT / "src" / "custos" / rel).read_text(encoding="utf-8")
            assert "from custos.core.factors._shares import" in s, (
                f"{rel} 应包限定导入 _shares"
            )
            assert "\nfrom _shares import" not in s, (
                f"{rel} 有扁平导入 _shares（会产生两份状态）"
            )

    def test_rule_documented(self):
        s = (ROOT / "src" / "custos" / "core" / "factors" / "_shares.py").read_text(
            encoding="utf-8"
        )
        assert "包限定" in s and "两个模块对象" in s


class TestInlineFactorsExtracted:
    """从 `enrich_candidates` 抽出的 11 个内联因子。

    抽出的动因（owner 2026-08-06）：**因子实现必须全项目唯一一份，其他模块通过调用访问。**
    内联在选股链主流程里，既无法单独回测，也无法防止别处再写一份 ——
    今天已经查出 J（4 份）、BBI（4 处）、DKS（2 份）三个指标各自重复过。

    抽取原则：**零行为变化**。四个因子在合成数据上的返回值与抽取前**逐字段相同**
    （用 /tmp 基线对比验证，见提交信息）。常量随因子走：`WAVE_*` 归 wave_type、
    `DIST_*` 归 distribution，等等 —— 需要它们的地方从因子模块导入，不再在 enrich 里抄一份。
    """

    @pytest.mark.parametrize("fid", INLINE_EXTRACTED)
    def test_registered(self, fid):
        assert fid in factors.registry()

    @pytest.mark.parametrize("fid", INLINE_EXTRACTED)
    def test_not_in_scorers(self, fid):
        """它们是 pattern/state，不是横截面 scorer —— 不该混进 SCORERS。"""
        from custos.research import backtest_factors as BF

        assert fid not in BF.SCORERS

    @pytest.mark.parametrize("fid", INLINE_EXTRACTED)
    def test_module_file_exists(self, fid):
        assert (ROOT / "src" / "custos" / "core" / "factors" / f"{fid}.py").exists()

    def test_enrich_no_longer_defines_them(self):
        """`enrich_candidates` 里不许再有本地定义 —— 那就成了第二份。"""
        import re

        s = (
            ROOT / "src" / "custos" / "pipeline" / "screening" / "enrich_candidates.py"
        ).read_text(encoding="utf-8")
        for fn in (
            "detect_wave_type",
            "compute_perfect_b1_fit",
            "detect_distribution",
            # v0.86（因子化批 A）：check_macd_technics 迁入 factors/macd_technics.py，
            # enrich 只保 import re-export
            "check_macd_technics",
            # v0.86（因子化批 B）：量能/结构族 + 周线 J 迁入 factors/
            # volume_detectors.py / b1_structure.py / weekly_j.py，enrich 只保 re-export
            "weekly_j_state",
            "j_below_threshold",
            "check_volume_sustain",
            "check_leader_volume",
            "check_bottom_volume",
            "check_non_one_wave",
            "check_repair_signals",
            "check_five_day_entry",
            "check_liquidity",
            "_stop_ref",
            # v0.86（因子化批 C）：点火族 / patterns 五单项判定 / J<13 门槛入口
            # 迁入 factors/ignition.py、entry_patterns.py、j_low_gate.py，
            # enrich 只保 import re-export
            "check_ignition",
            "check_pullback_shrink",
            "zx_recent_golden",
            "b1_ignition_hit",
            "reversal_flags",
            "bbi_above",
            "relative_strength_strong",
            "j_low_gate_hit",
            # compute_b1_pullback_fit 已于 v0.50（#37 阶段 A）随证伪下线移出 live 链，
            # 不在本清单（enrich 里应**不再出现**它——由下方断言保证）
        ):
            assert not re.search(rf"^def {fn}\(", s, re.M), f"enrich 又定义了本地 {fn}"
            assert fn in s, f"enrich 应通过导入访问 {fn}"
        assert "compute_b1_pullback_fit" not in s, (
            "v0.50：证伪因子不得留在 live 链（连 import 都不该有）"
        )

    def test_constants_moved_with_their_factor(self):
        """常量必须跟着因子走，`enrich_candidates` 里不该再有它们的定义。"""
        import re

        s = (
            ROOT / "src" / "custos" / "pipeline" / "screening" / "enrich_candidates.py"
        ).read_text(encoding="utf-8")
        for pfx in (
            "WAVE_",
            "FIT_",
            "B1PB_",
            "DIST_",
            "MACD_",
            # v0.86（因子化批 B）：常量随因子迁走
            "NOW_",
            "REPAIR_",
            "FIVE_DAY_",
            "VOLUME_SUSTAIN_",
            "LEADER_VOL_",
            "BOTTOM_",
            "CZ_",
            "STOP_",
            # v0.86（因子化批 C）：常量随因子迁走
            "ZX_",
            "IGNITION_",
            "PULLBACK_",
            "RS_",
        ):
            defs = re.findall(rf"^({pfx}[A-Z0-9_]+) *=", s, re.M)
            assert not defs, f"{pfx}* 常量应随因子迁走，enrich 里还剩：{defs}"
        # 同前缀只有部分常量迁走的，按名单钉（THREE_LOWS_VOL_RATIO
        # 只被留在 enrich 的检测器/score 层用，留在本地）。
        for name in ("THREE_LOWS_DRAWDOWN_PCT", "LIQUIDITY_WIN"):
            assert not re.search(rf"^{name} *=", s, re.M), (
                f"{name} 应随因子迁走，enrich 里还有定义"
            )

    def test_factors_own_their_constants(self):
        """反面：常量确实在因子模块里。"""
        import re

        pairs = [
            ("wave_type", "WAVE_"),
            ("perfect_b1_fit", "FIT_"),
            ("b1_pullback_fit", "B1PB_"),
            ("distribution", "DIST_"),
            ("macd_technics", "MACD_"),
            # v0.86（因子化批 B）
            ("volume_detectors", "VOLUME_SUSTAIN_"),
            ("volume_detectors", "LEADER_VOL_"),
            ("volume_detectors", "BOTTOM_"),
            ("volume_detectors", "CZ_"),
            ("b1_structure", "NOW_"),
            ("b1_structure", "REPAIR_"),
            ("b1_structure", "FIVE_DAY_"),
            ("b1_structure", "LIQUIDITY_"),
            ("b1_structure", "STOP_"),
            # v0.86（因子化批 C）
            ("ignition", "ZX_"),
            ("ignition", "IGNITION_"),
            ("ignition", "PULLBACK_"),
            ("entry_patterns", "RS_"),
        ]
        for fid, pfx in pairs:
            s = (ROOT / "src" / "custos" / "core" / "factors" / f"{fid}.py").read_text(
                encoding="utf-8"
            )
            assert re.search(rf"^{pfx}[A-Z0-9_]+ *=", s, re.M), f"{fid} 缺 {pfx}* 常量"


class TestKnownConflicts:
    """已知矛盾必须**显式登记**，不许静默放过、也不许悄悄变多。

    v0.50（2026-08-12，#37 阶段 A，owner 拍板）：原唯一登记项 `s_shape`
    （R2 说无 alpha，而 live 的 `score_candidates.technical_score` 主路径是它）
    已定案消解——s_shape 移出分层、降为展示/证据列。**集合当前必须为空**；
    新矛盾出现时连带原因登记进来（本类就是拦「悄悄变多」的 ratchet）。
    """

    def test_conflict_set_is_empty_after_v050(self):
        assert set(factors.KNOWN_STATUS_USE_CONFLICTS) == set(), (
            "已知矛盾集合应随 v0.50 定案清空——新出现的矛盾必须是有意识的决定，"
            "并在 KNOWN_STATUS_USE_CONFLICTS 里写清原因"
        )

    def test_conflict_documented_in_module(self):
        """定案记录写在因子模块自己的元数据里 —— 读那个文件的人才看得到。"""
        s = (ROOT / "src" / "custos" / "core" / "factors" / "s_shape.py").read_text(
            encoding="utf-8"
        )
        assert "定案" in s and "v0.50" in s and "score_candidates" in s


class TestStageMatchesReality:
    """`stage` 必须与**实际 import 图**一致，不靠手写维护。

    owner 2026-08-06 要求给因子加 release/debug 标记表示是否已上线。
    做成**可自验证**的：`stage="release"` ⇔ 18:00 选股链真的引用它。

    ⚠️ 为什么必须自验证：手写标签会很快与事实脱节，而
    **「以为上线了其实没有」比没有标记更糟** —— 前者会让人拿一个没跑的因子去解释线上结果。

    三个维度各答不同的问题，别混：
        status    证据够不够？      —— 研究结论
        live_use  允许怎么用？      —— 规则约束
        stage     现在真的在跑吗？  —— 部署事实
    """

    LIVE_FILES = [
        "pipeline/screening/enrich_candidates.py",
        "pipeline/screening/score_candidates.py",
        "pipeline/screening/candidate_table.py",
        "pipeline/screening/signal_labels.py",
        "pipeline/screening/formula_screen.py",
        # 2026-08-09 扩：盘中监控与持仓链也是 live 消费者 —— 它们若哪天开始
        # import 因子模块，stage 标记必须与事实一致。当前它们不引用任何因子，
        # 扫描结果不变，只是把网织在事发之前。
        "pipeline/market_timing/technical_monitor.py",
        "pipeline/holdings/b1_holding_state.py",
        "pipeline/holdings/batch_holding_technical.py",
    ]

    def _referenced(self) -> set[str]:
        import re

        srcs = [
            (ROOT / "src" / "custos" / f).read_text(encoding="utf-8")
            for f in self.LIVE_FILES
        ]
        out = set()
        for fid in factors.registry():
            if any(
                re.search(
                    rf"\bfrom [\w.]*\.{fid} import|\bfrom {fid} import|\bimport [\w.]*\b{fid}\b",
                    s,
                )
                for s in srcs
            ):
                out.add(fid)
        return out

    def test_every_factor_declares_stage(self):
        for fid, e in factors.registry().items():
            assert e["meta"].get("stage") in factors.STAGES, (
                f"{fid} 的 stage={e['meta'].get('stage')!r} 不合法"
            )

    def test_release_means_actually_referenced(self):
        """标 release 的必须真被 live 链引用 —— 否则是虚假的「已上线」。"""
        ref = self._referenced()
        bad = [
            f
            for f, e in factors.registry().items()
            if e["meta"]["stage"] == "release" and f not in ref
        ]
        assert not bad, f"标了 release 但 live 链没引用：{bad}"

    def test_debug_means_not_in_live(self):
        """标 debug 的不许被 live 链引用 —— 那说明它其实上线了，标记撒谎。"""
        ref = self._referenced()
        bad = [
            f
            for f, e in factors.registry().items()
            if e["meta"]["stage"] == "debug" and f in ref
        ]
        assert not bad, f"标了 debug 却在 live 链里：{bad}"

    def test_release_set_is_the_known_twenty(self):
        """已上线集合当前 20 个。变动必须是有意识的 —— 上线/下线都该被看见。

        （v0.50：12 → 11，b1_pullback_fit 证伪下线转 debug；
        v0.56：11 → 12，bottom_patterns 证据层进 live 链；
        v0.79：12 → 11，sector_mainstream 主线指纹节删除转 debug；
        v0.84：11 → 13，Phase D 因子化——capital_intent（scorer，分层第二轴，
        score_candidates 迁入）与 fundamentals（evidence_only，展示/共振腿）登记；
        v0.86：13 → 14，因子化批 A——macd_technics（scorer，7 条技术分腿 +
        2 条 cap 判定的唯一生产者，enrich_candidates 迁入，零行为变化）；
        v0.86：14 → 17，因子化批 B——volume_detectors（scorer，bottom/leader
        打分腿 + capital_intent 证据，sustain 另喂 retreat cap）、b1_structure
        （scorer，five_day/repair/non_one_wave 打分腿 + revoked 封顶 C cap，
        liquidity 仅 flag）、weekly_j（scorer，weekly_j_low +5 腿）自
        enrich_candidates 迁入，零行为变化；
        v0.86：17 → 20，因子化批 C——ignition（scorer，ignition/pullback_shrink/
        b1_ignition 3 条技术分腿 + capital_intent 证据 + 门内提醒判据）、
        entry_patterns（scorer，patterns 五单项 5 条技术分腿 + capital_intent
        证据）、j_low_gate（gate，18:00 进池硬门槛登记，判定本体复用
        weekly_j.j_below_threshold，执行点在 enrich _apply_j_gate）自
        enrich_candidates 迁入/补登记，零行为变化。）
        """
        got = set(factors.released())
        assert len(got) == 20, f"已上线因子数变了（{len(got)}）：{sorted(got)}"

    def test_debug_factors_are_research_only(self):
        """未上线的因子 live_use 应为 none —— 既没上线又声明可用是自相矛盾。"""
        for fid, e in factors.registry().items():
            m = e["meta"]
            if m["stage"] == "debug":
                assert m["live_use"] == "none", (
                    f"{fid} stage=debug 却声明 live_use={m['live_use']}"
                )
