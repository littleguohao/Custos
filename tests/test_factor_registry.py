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


def _bars_dt(n=120, seed=5):
    """DatetimeIndex 版（weekly 重采样路径要真实日期类型 + amount 列）。"""
    df = _bars(n, seed)
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = df["close"] * df["volume"]
    return df


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
        v0.185：− main_rally_factor（R8 H4 + R27 双证 0 触发，撤 1800 标注，
        转 debug/none，研究侧 gate 保留留证）。
        v0.217（TODO #67 B0）：+ qsx_resonance（登记补齐——一直被 signal_labels
        引用（观察记录标签），此前无 FACTOR 元数据；R23 结论 ⇒ needs_work +
        evidence_only）。
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
            "rsi_state",
            # v0.50（#37 阶段 A）定案，见各自模块 FACTOR 注释
            "s_shape",
            "sector_phase",
            # v0.56：底部形态（W底/红肥绿瘦，25chuhuo 底部镜像）——证据层
            "bottom_patterns",
            # v0.84：基本面因子化（evidence_only，不进分，行为不变）
            "fundamentals",
            # v0.217（TODO #67 B0）：登记补齐（原注册表外模块，signal_labels
            # 引用观察记录标签；R23 ⇒ needs_work + evidence_only）
            "qsx_resonance",
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
        enrich_candidates 迁入/补登记，零行为变化；
        v0.185：20 → 19，main_rally_factor 撤标注下线转 debug（R8 H4 + R27
        双证 0 触发；研究侧 gate 保留留证）。
        v0.217：19 → 20，qsx_resonance 登记补齐（TODO #67 B0——原注册表外
        模块，一直被 signal_labels 引用作观察记录标签；R23 结论 ⇒
        needs_work + evidence_only，行为不变）。）
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


class TestLineageFields:
    """TODO #73：谱系字段（research_ref / trajectory_ref）形态由测试强制。

    对齐 needs_work→evidence 的既有模式（文件存在性检查）：可选字段空/缺省
    无约束，一旦给出就必须形态合法且指向真实存在的研究单元文档。
    """

    def test_research_ref_shape_and_doc_exists(self):
        import re

        for fid, e in factors.registry().items():
            refs = e["meta"].get("research_ref")
            if not refs:
                continue
            assert isinstance(refs, list), f"{fid} 的 research_ref 必须是 list"
            for r in refs:
                assert isinstance(r, str) and re.fullmatch(r"R\d+", r), (
                    f"{fid} 的 research_ref 条目形态非法: {r!r}（须 ^R\\d+$）"
                )
                hits = list((ROOT / "governance" / "research").glob(f"{r}_*.md"))
                assert hits, f"{fid} 引用了 {r}，但 governance/research/ 无对应文档"

    def test_trajectory_ref_shape(self):
        import re

        for fid, e in factors.registry().items():
            t = e["meta"].get("trajectory_ref")
            if not t:
                continue
            assert isinstance(t, str) and re.fullmatch(r"t_[0-9a-f]{10}", t), (
                f"{fid} 的 trajectory_ref={t!r} 形态非法（须 ^t_[0-9a-f]{{10}}$）"
            )

    def test_evidence_r_doc_backfilled(self):
        """evidence 指向 R 文档的因子必须回填 research_ref（防漏回填漂移）。

        v0.214（TODO #73）机械回填 31 个：R2×15 / R31×9 / R8×2 / R1/R3/R4/R6/R7×1。
        """
        import re

        n = 0
        for fid, e in factors.registry().items():
            ev = e["meta"].get("evidence") or ""
            m = re.search(r"governance/research/(R\d+)_", ev)
            if not m:
                continue
            n += 1
            assert m.group(1) in (e["meta"].get("research_ref") or []), (
                f"{fid} 的 evidence 指向 {m.group(1)} 但 research_ref 未回填"
            )
        assert n >= 31, f"回填基数变了（{n}）——若是有意删减 R 引用请同步本断言"


class TestFreeParamsAdmission:
    """TODO #74：准入自由度惩罚——参数多的因子晋级必须有研究背书。

    KNOWN_STATUS_USE_CONFLICTS 同风格的 ratchet：存量不强制回填 free_params，
    但有声明的就受规则约束；新出现的「active 多参数无背书」会被拦住。
    """

    def test_free_params_shape(self):
        for fid, e in factors.registry().items():
            fp = e["meta"].get("free_params")
            if fp is None:
                continue  # 未声明视为 0，无约束（存量逐步来）
            assert isinstance(fp, int) and not isinstance(fp, bool) and fp >= 0, (
                f"{fid} 的 free_params={fp!r} 必须是非负 int（bool 拒）"
            )

    def test_param_heavy_active_needs_research_ref(self):
        """free_params >= 4 且 status=active ⇒ 必须有 research_ref。"""
        for fid, e in factors.registry().items():
            m = e["meta"]
            fp = m.get("free_params") or 0
            if m.get("status") == "active" and fp >= 4:
                assert m.get("research_ref"), (
                    f"{fid} free_params={fp} 且 active，但缺 research_ref —— "
                    "参数多的因子晋级必须有在案研究单元背书（TODO #74）"
                )

    def test_demo_declarations(self):
        """示范声明钉住（v0.214）：active 多参数由回填的 research_ref 满足规则。"""
        reg = factors.registry()
        assert reg["wave_type"]["meta"]["free_params"] == 9
        assert reg["distribution"]["meta"]["free_params"] == 17
        assert reg["macd_technics"]["meta"]["free_params"] == 4
        # macd_technics 是 candidate：声明即合法，active 规则不适用
        assert reg["macd_technics"]["meta"]["status"] == "candidate"
        # active 两个都有背书（#73 回填的 R2）
        assert reg["wave_type"]["meta"]["research_ref"] == ["R2"]
        assert reg["distribution"]["meta"]["research_ref"] == ["R2"]


class TestCharacterizationSnapshot:
    """TODO #67 B0：冻结行为的快照（迁移期等价性裁判，批次完成后保留作永久守卫）。

    固定合成输入（`_bars()` 族），逐因子调用其当前消费方实际使用的入口，
    输出规范化（sort_keys + default=str + allow_nan）后的 sha1[:12] 钉在这里。
    迁移批次重构时输出哈希必须不变 —— 变了就是行为漂移，当场红。
    生成器：/tmp 一次性脚本（v0.217 commit 信息有路径），口径写死在下面 DRIVER。
    """

    #: 无顶层 score/detect 的模块（compute_*/领域命名函数）的快照驱动：
    #: 调的就是 live/研究当前真实消费的入口（固定输入，确定性）。
    _DOMAIN = {
        "b2_surge_factor": lambda m, df: {
            "b2": m.detect_b2(df, "600000"),
            "bottom_surge": m.detect_bottom_surge(df, "600000"),
            "surge_then_b1": m.detect_surge_then_b1(df, "600000"),
        },
        "bottom_patterns": lambda m, df: {
            "w_bottom": m.detect_w_bottom(df),
            "red_fat": m.detect_red_fat_green_thin(df),
        },
        "distribution": lambda m, df: m.detect_distribution(df),
        "main_rally_factor": lambda m, df: m.detect_main_rally_start(df),
        "platform_pullback": lambda m, df: m.detect_platform_pullback(df),
        "wave_type": lambda m, df: m.detect_wave_type(df),
        "b1_dual_factor": lambda m, df: {
            "compute": m.compute_b1_dual(df, "600000"),
            "detect_bp": m.detect_breakout_pullback_b1(df, "600000"),
        },
        "b1_pullback_fit": lambda m, df: m.compute_b1_pullback_fit(df),
        "perfect_b1_fit": lambda m, df: m.compute_perfect_b1_fit(df, 5.0, {}, {}),
        "s_shape": lambda m, df: m.compute_s_shape(df),
        "sector_phase": lambda m, df: m.compute_sector_phase(df["close"]),
        "b1_structure": lambda m, df: m.check_non_one_wave(df),
        "capital_intent": lambda m, df: m.resolve_capital_weights(None),
        "entry_patterns": lambda m, df: m.reversal_flags(5.0, 1.5, 0.8, -3.0, 4.2),
        "fundamentals": lambda m, df: m.fundamental_quality(None),
        "ignition": lambda m, df: m.check_ignition(df),
        "j_low_gate": lambda m, df: m.j_low_gate_hit(5.0),
        "macd_technics": lambda m, df: m.check_macd_technics(df),
        "rsi_state": lambda m, df: m.rsi_regime(df),
        "sector_mainstream": lambda m, df: m._stat([0.01, -0.02, 0.03]),
        "volume_detectors": lambda m, df: m.check_volume_sustain(df),
        "weekly_j": lambda m, df: m.weekly_j_state(_bars_dt()),
        "qsx_resonance": lambda m, df: m.resonance_v2_snapshot(df),
    }

    _SNAPSHOTS = {
        "alpha101": "f16d205b43eb",
        "alpha_pvcorr": "effcb178ff26",
        "b1_dual_factor": "ac99097db6ce",
        "b1_pullback_fit": "fec88c598f8a",
        "b1_structure": "f08c812dd429",
        "b2_surge_factor": "9f3b7bcdc8e1",
        "baseline": "2bd7dd114d5c",
        "bottom_patterns": "76cf78a9d5be",  # v0.218…B2 更新（新增 detect 打包入口槽）
        "capital_intent": "315b2ee9f36b",
        "distribution": "c75399e5d2ad",
        "entry_patterns": "b46b697c5816",
        "fundamentals": "ac8fae92afec",
        "ignition": "951e4fd04fe5",
        "j_low_gate": "5ffe533b830f",
        "kdj_j": "fc95f211968d",
        "low_vol": "28cd5e0cd2b8",
        "macd_technics": "58b0c5f75b88",
        "main_rally_factor": "75fdb6c812e8",
        "mcap": "2be88ca4242c",
        "momentum": "841b02900b08",
        "perfect_b1_fit": "c48b169f824e",
        "platform_pullback": "2be88ca4242c",
        "qn_adx_extreme": "300de58cada0",
        "qn_box_target": "345870238d01",
        "qn_bullish_engulf": "9029887566ec",
        "qn_kdj_neg_day": "3c440352db25",
        "qn_ma144_launch": "0ff4c23c21c0",
        "qn_ma25_state": "71fb7511b6b5",
        "qn_ma_converge": "0ff4c23c21c0",
        "qn_macd_bar_shift": "6d0f0baa3254",
        "qn_shrink_limit_up": "43dd667bf1ed",
        "qn_three_red": "8f32d732bc1f",
        "qn_volume_surge_cut": "afa81ff488fd",
        "qn_weekly180_setup": "19cf989a39e2",
        "qsx_resonance": "81a45d06aeba",
        "reversal_quality": "a4c99ffdc5fd",
        "reversal_quality_inv": "7c8275d1a03f",
        "rsi_state": "a4e72ceb2532",  # v0.220…B3c 更新（score 规范入口槽上移接管）
        "s_shape": "e5ce9b0517c3",  # v0.218…B2 更新（score 规范入口槽上移接管）
        "sector_mainstream": "2f6b4e2841cc",
        "sector_phase": "3fb204571d9e",
        "volume_detectors": "b07995f01b26",
        "wave_type": "c700313d395f",
        "weekly_j": "4e64459f31a6",
    }

    @staticmethod
    def _canon(obj) -> str:
        import hashlib
        import json

        blob = json.dumps(
            obj, sort_keys=True, default=str, allow_nan=True, ensure_ascii=False
        )
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]

    def test_snapshot_covers_every_registered_factor(self):
        assert set(self._SNAPSHOTS) == set(factors.registry())

    @pytest.mark.parametrize("fid", sorted(_SNAPSHOTS), ids=lambda x: x)
    def test_output_bitwise_frozen(self, fid):
        e = factors.registry()[fid]
        df = _bars()
        if e["score"] is not None:
            r = e["score"](df, "600000")
        elif e["detect"] is not None:
            r = e["detect"](df)
        else:
            r = self._DOMAIN[fid](e["module"], df)
        assert self._canon(r) and self._canon(r) == self._SNAPSHOTS[fid], (
            f"{fid} 输出漂移：{self._canon(r)} != 快照 {self._SNAPSHOTS[fid]}"
            "（迁移期重构必须零行为变化；若是有意的语义改动，须 owner 拍板"
            " + 行为变更钉测 + 更新快照）"
        )


class TestRegistryDrivenScorers:
    """TODO #67 B1：纯研究侧 selector 的 SCORERS 注册 = 注册表直通。

    同一函数对象（身份不变 ⇒ _SCORER_PRECOMPUTE 身份查表不受影响）；
    键集合另有 test_scorers_keys_unchanged 钉住。
    """

    _TEN = (
        "baseline",
        "alpha101",
        "alpha_pvcorr",
        "low_vol",
        "momentum",
        "reversal_quality",
        "reversal_quality_inv",
        "mcap",
        "kdj_j",
    )

    def test_ten_selectors_are_registry_passthrough(self):
        from custos.research import backtest_factors as BF

        reg = factors.registry()
        for fid in self._TEN:
            a, b = BF.SCORERS[fid], reg[fid]["score"]
            # ⚠️ 不比 `is`：test_enrich_b1cz 会 importlib.reload 因子模块
            # （顺序污染是本仓库已知坑，见 conftest 的 reversal_thresholds 注释）
            # —— reload 后 registry() 拿新对象、SCORERS 持旧对象；同模块同名
            # 同行为才是可靠判定面（生产无 reload，对象恒同；行为等价由下方
            # test_passthrough_values_match_snapshot_path 钉）。
            assert (a.__module__, a.__name__) == (b.__module__, b.__name__), (
                f"{fid} 的 SCORERS 项不是注册表直通：{a.__module__}.{a.__name__} "
                f"vs {b.__module__}.{b.__name__}"
            )

    def test_passthrough_values_match_snapshot_path(self):
        """直通对象的输出 = B0 快照路径的输出（同一函数，同一输入）。"""
        from custos.research import backtest_factors as BF

        df = _bars()
        for fid in self._TEN:
            a = BF.SCORERS[fid](df, "600000")
            b = factors.registry()[fid]["score"](df, "600000")
            assert a == b


class TestCanonicalEntryB2:
    """TODO #67 B2：evidence_only 因子的规范入口收口——等价性钉测。

    逐位等价：别名同对象 / 打包入口 == 原两次单调 / 上移映射 == 原适配层逐字逻辑。
    """

    def test_aliases_are_same_object(self):
        from custos.core.factors import platform_pullback, qsx_resonance

        assert platform_pullback.detect is platform_pullback.detect_platform_pullback
        assert qsx_resonance.detect is qsx_resonance.resonance_v2_snapshot

    def test_bottom_patterns_bundle_matches_two_calls(self):
        from custos.core.factors import bottom_patterns

        df = _bars()
        bundle = bottom_patterns.detect(df, "600000")
        assert bundle["w_bottom"] == bottom_patterns.detect_w_bottom(df, "600000")
        assert bundle[
            "red_fat_green_thin"
        ] == bottom_patterns.detect_red_fat_green_thin(df, "600000")

    def test_sector_phase_detect_delegates(self):
        from custos.core.factors import sector_phase

        df = _bars()
        assert sector_phase.detect(df) == sector_phase.compute_sector_phase(df["close"])

    def test_s_shape_score_matches_moved_mapping(self):
        """s_shape.score() == 原 backtest_factors._sc_s_shape 的映射（逐字段参考对拍）。"""
        from custos.core.factors import s_shape

        df = _bars()
        r = s_shape.compute_s_shape(df, "600000")
        got = s_shape.score(df, "600000")
        if not r.get("available"):
            assert got is None
            return
        want = {
            "score": r["s_star"],
            "suggestion": r["suggestion"],
            "aux": {
                "s_shape": r["s_shape"],
                "delta": r["delta"],
                "penalty": r["penalty"],
            },
            "components": {
                k: (v or {}).get("points")
                for k, v in (r.get("components") or {}).items()
            },
        }
        assert got == want

    def test_scorers_s_shape_is_factor_score(self):
        """SCORERS["s_shape"] 与因子模块 score 同模块同名（reload 污染见 B1 注记）。"""
        from custos.research import backtest_factors as BF

        a = BF.SCORERS["s_shape"]
        b = factors.registry()["s_shape"]["score"]
        assert (a.__module__, a.__name__) == (b.__module__, b.__name__)

    def test_live_call_sites_use_canonical_names(self):
        """live 只改 import 点名：enrich/signal_labels 的调用点走规范入口。"""
        enrich = (
            ROOT / "src/custos/pipeline/screening/enrich_candidates.py"
        ).read_text(encoding="utf-8")
        labels = (ROOT / "src/custos/pipeline/screening/signal_labels.py").read_text(
            encoding="utf-8"
        )
        assert "bottom_patterns_mod.detect(df, code)" in enrich
        assert "detect as detect_platform_pullback" in enrich
        assert "detect as resonance_v2_snapshot" in labels


class TestB3aB1DualPredicate:
    """TODO #67 B3 裁决点①（b1_dual 内联判据删除）：谓词单源化、语义不变钉测。"""

    def test_predicate_matches_old_inline_formula(self):
        """signal_labels 旧内联式 `ph and close >= ph*0.98 and daily_j < 13.0`
        与单源谓词逐点一致（边界值逐个过）。"""
        from custos.core.factors.b1_dual_factor import breakout_pullback_hit

        for ph in (0.0, 10.0, 10.465):
            for close in (10.3, 10.367, 10.393, 10.465):
                for j in (12.99, 13.0, 13.01):
                    old = bool(ph and close >= ph * 0.98 and j < 13.0)
                    assert breakout_pullback_hit(ph, close, j) == old

    def test_detect_uses_predicate(self):
        """detect_breakout_pullback_b1 的 hit 与谓词输出一致（同输入）。"""
        from custos.core.factors.b1_dual_factor import (
            breakout_pullback_hit,
            detect_breakout_pullback_b1,
        )

        r = detect_breakout_pullback_b1(_bars(), "600000")
        if not r.get("available") or r.get("platform_high") is None:
            return  # 合成数据无平台形：hit=False 路径已由上一用例钉住公式
        assert r["hit"] == breakout_pullback_hit(r["platform_high"], r["close"], r["j"])

    def test_signal_labels_calls_predicate(self):
        s = (ROOT / "src/custos/pipeline/screening/signal_labels.py").read_text(
            encoding="utf-8"
        )
        assert "breakout_pullback_hit(ph, close, daily_j)" in s
        assert "close >= ph * 0.98 and daily_j < 13.0" not in s  # 手写判据已删


class TestB3bB2ScoreSingleSource:
    """TODO #67 B3 裁决点②（b2 合成 score 上移）：公式单源化钉测。"""

    def test_b2_score_matches_old_synthesis(self):
        """b2_score() == 原 _sc_b2 内联合成（命中数×20 + 无上影线×20，round 1）。"""
        from custos.core.factors.b2_surge_factor import b2_score

        for r in (
            {
                "b1_before": True,
                "gain_ok": True,
                "vol_up": True,
                "j_ok": True,
                "no_upper_shadow": True,
            },
            {
                "b1_before": True,
                "gain_ok": False,
                "vol_up": True,
                "j_ok": False,
                "no_upper_shadow": False,
            },
            {
                "b1_before": False,
                "gain_ok": False,
                "vol_up": False,
                "j_ok": False,
                "no_upper_shadow": True,
            },
        ):
            hard = sum(
                int(bool(r[k])) for k in ("b1_before", "gain_ok", "vol_up", "j_ok")
            )
            want = round(hard * 20.0 + (20.0 if r.get("no_upper_shadow") else 0.0), 1)
            assert b2_score(r) == want

    def test_sc_b2_calls_factor_synthesis(self):
        """backtest 的 _sc_b2 调因子模块合成（同模块同名，见 B1 reload 注记）。"""
        from custos.research import backtest_factors as BF
        from custos.core.factors import b2_surge_factor

        a = BF._b2_score
        assert (a.__module__, a.__name__) == (
            "custos.core.factors.b2_surge_factor",
            "b2_score",
        )
        # 端到端：同一 detect 输出 → _sc_b2 的 score 与 b2_score 一致
        df = _bars()
        r = b2_surge_factor.detect_b2(df, "600000")
        if r.get("available"):
            got = BF.SCORERS["b2"](df, "600000")
            if got is not None:
                assert got["score"] == b2_surge_factor.b2_score(r)

    def test_no_live_b2_score_column(self):
        """裁决点②落地口径：live 候选表不加 b2_score 列（schema 不变）。"""
        s = (ROOT / "src/custos/pipeline/screening/signal_labels.py").read_text(
            encoding="utf-8"
        )
        assert "b2_score" not in s


class TestB3cRsiStateSingleSource:
    """TODO #67 B3 裁决点③（rsi_state 权重阈值单源化）：常量进因子模块钉测。"""

    def test_buy_threshold_lives_in_factor_module(self):
        from custos.core.factors import rsi_state

        assert rsi_state.RSI_STATE_SCORE_BUY_MIN == 60.0  # 与原 _sc_rsi_state 内联同值

    def test_factor_score_matches_old_scorer(self):
        """rsi_state.score() == 原 _sc_rsi_state 逐字段（同一输入对拍）。"""
        from custos.core.factors import rsi_state

        df = _bars()
        r = rsi_state.rsi_state_score(df, "600000")
        got = rsi_state.score(df, "600000")
        if not r.get("available"):
            assert got is None
            return
        want = {
            "score": r["score"],
            "suggestion": "可买" if r["score"] >= 60 else "不买",
            "aux": {
                "rsi_regime": r["regime"],
                "rsi": r["rsi"],
                "bullish_divergence": r["bullish_divergence"],
            },
            "components": {"regime": r["regime"]},
        }
        assert got == want

    def test_scorers_rsi_state_is_factor_score(self):
        """SCORERS["rsi_state"] 与因子 score 同模块同名（B1 reload 注记口径）。"""
        from custos.research import backtest_factors as BF

        a = BF.SCORERS["rsi_state"]
        b = factors.registry()["rsi_state"]["score"]
        assert (a.__module__, a.__name__) == (b.__module__, b.__name__)

    def test_live_ideal_b1_unchanged(self):
        """裁决点③：live 的 rsi_ideal_b1 仍是 strong∧deep 布尔合取（不碰）。"""
        s = (ROOT / "src/custos/pipeline/screening/signal_labels.py").read_text(
            encoding="utf-8"
        )
        assert 'reg.get("state") == "strong" and reg.get("deep_oversold")' in s
