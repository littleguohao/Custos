"""累积状态与读-改-写文件必须原子落盘。

2026-08-06 review `market_timing/` 时发现：`trades/incremental_ledger` 早就有一份
私有 `_write_atomic`（它踩过「持仓已加、台账没记 ⇒ 下次导入重复计账、**持仓静默翻倍**」
这个真实缺陷），但同样是累积/读改写的地方还在裸写 `write_text(json.dumps(...))`。

⚠️ **不是所有 JSON 写入都要原子化**（全仓 62 处，只有一小部分需要）。判据两条：

    ① 累积状态      —— 损坏就丢历史，无法从当日数据重建
                       `0amv_regime_history.json`（全历史 regime，驱动加仓授权）
                       `position_confirmations.json`（人工确认记录）
    ② 读-改-写共享  —— 多个 stage 依次改写同一份
                       `{date}_market_timing_input.json`：collector → merge → amv_state
                       写坏要重跑整链，而**盘中快照那种数据窗口已经过去、重跑也拿不回**

纯产物（报告 / run log / 门控 JSON / 采集输出）不必原子化：下次跑会重写。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "07_tools"))
import paths  # noqa: E402


class TestWriteJsonAtomic:
    def test_writes_and_reads_back(self, tmp_path):
        f = tmp_path / "a" / "b.json"
        paths.write_json_atomic(f, {"k": [1, 2]})
        assert json.loads(f.read_text(encoding="utf-8")) == {"k": [1, 2]}

    def test_no_tmp_left_behind(self, tmp_path):
        f = tmp_path / "x.json"
        paths.write_json_atomic(f, {"a": 1})
        assert list(tmp_path.iterdir()) == [f], "tmp 文件未被 replace 掉"

    def test_reader_never_sees_partial(self, tmp_path, monkeypatch):
        """核心保证：**写到一半崩溃，目标文件仍是上一版完整内容**。

        裸 `write_text` 会先截断再写 ⇒ 崩在中间读者看到残文件；
        而残 JSON 解析会抛异常，上游可能把它当成「数据缺失」而走降级路径 ——
        比直接失败更糟。
        """
        f = tmp_path / "s.json"
        paths.write_json_atomic(f, {"v": 1})

        real = pathlib.Path.write_text

        def boom(self, *a, **k):
            if self.suffix == ".tmp":
                real(self, *a, **k)          # tmp 写成功
                raise OSError("disk full")   # 但 replace 之前崩
            return real(self, *a, **k)

        monkeypatch.setattr(pathlib.Path, "write_text", boom)
        with pytest.raises(OSError):
            paths.write_json_atomic(f, {"v": 2})
        assert json.loads(f.read_text(encoding="utf-8")) == {"v": 1}, \
            "崩溃后目标文件应仍是上一版完整内容"

    def test_non_ascii_and_non_serializable(self, tmp_path):
        from datetime import date
        f = tmp_path / "u.json"
        paths.write_json_atomic(f, {"名称": "测试", "d": date(2026, 8, 6)})
        got = json.loads(f.read_text(encoding="utf-8"))
        assert got["名称"] == "测试" and got["d"] == "2026-08-06"


class TestAccumulativeStateUsesAtomic:
    """两类必须原子写的地方，不许退回裸 write_text。"""

    CASES = [
        ("market_timing/amv_state.py", "0amv_regime_history 是全历史 regime，驱动加仓授权"),
        ("runtime_guards.py", "position_confirmations 是人工确认记录"),
        ("market_timing/merge_incremental_market.py", "market_timing_input 被多 stage 读改写"),
    ]

    @pytest.mark.parametrize("rel,why", CASES, ids=lambda x: x if isinstance(x, str) else "")
    def test_uses_atomic_writer(self, rel, why):
        s = (ROOT / "07_tools" / rel).read_text(encoding="utf-8")
        assert "write_json_atomic" in s, f"{rel} 应用原子写：{why}"

    def test_amv_state_regime_history_atomic(self):
        """regime 历史那一行必须是原子写 —— 它损坏会让 regime 判定失去全部历史。"""
        s = (ROOT / "07_tools" / "market_timing" / "amv_state.py").read_text(encoding="utf-8")
        assert "write_json_atomic(STATE" in s
        assert "STATE.write_text" not in s

    def test_import_not_swallowed_by_noqa_comment(self):
        """⚠️ 导入名不许被塞进 `# noqa` 注释里。

        2026-08-06 同一形状犯了两次：脚本把新名字追加到导入行末尾，
        而那行以 `# noqa: E402` 结尾 ⇒ 变成
        `from paths import BASE  # noqa: E402, write_json_atomic`，
        名字进了注释、导入没生效，直到测试报 NameError 才发现。
        """
        import re
        bad = []
        for p in (ROOT / "07_tools").rglob("*.py"):
            for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"#\s*noqa:\s*E\d+,\s*[a-z_]", ln):
                    bad.append(f"{p.relative_to(ROOT)}:{i}")
        assert not bad, f"导入名被塞进 noqa 注释：{bad}"


class TestRegimeLockRespectedEverywhere:
    """0AMV 的**锁定语义**必须在所有写 `effective_state` 的地方一致。

    `amv_state` 模块 docstring 定的不变量：

        进入空头后须有 **confirmed 的 >+4%** 才能出来；
        **阈值之间的读数不得把 regime 重置为中性**。

    ⚠️ 2026-08-06 查出 `merge_incremental_market` 的兜底违反了它：
    `effective_state = amv_zone or ("空头" if <-2.3 else "做多" if >4 else "中性")`
    —— 读数在阈值之间时直接写「中性」，**把锁定的空头重置了**。
    而「中性」在加仓白名单 `{做多, 中性}` 里 ⇒ 空头期可能被授予加仓权。

    该兜底存在的原因是链上 merge 跑在 amv_state 之前，`effective_state` 此时还没有；
    它只是临时占位，但**占位也不能违反不变量** —— 中间有任何读者（门控、评分器）
    读到它，就会拿到一个被解锁的 regime。
    """

    SRC = (ROOT / "07_tools" / "market_timing" / "merge_incremental_market.py").read_text(encoding="utf-8")

    def test_fallback_does_not_reset_lock_to_neutral(self):
        assert 'else "中性")' not in self.SRC, \
            "兜底又在阈值之间写「中性」—— 那会重置锁定的空头"

    def test_fallback_carries_prior_locked_state(self):
        seg = self.SRC[self.SRC.index("prior_effective_state"):][:600]
        assert '"空头", "做多"' in seg, "阈值之间应延续已知锁定前态"

    def test_unknown_prior_stays_unknown(self):
        """前态未知就留「未知」，让下游 fail-closed。

        `normalize_regime("未知")` 不在加仓白名单里 ⇒ 风控优先于买入。
        """
        seg = self.SRC[self.SRC.index("prior_effective_state"):][:600]
        assert '"未知"' in seg, "前态未知时不得凭空给方向"

    def test_amv_state_invariant_still_documented(self):
        """不变量本身必须留在 amv_state 的 docstring 里 —— 它是这条约束的来源。"""
        s = (ROOT / "07_tools" / "market_timing" / "amv_state.py").read_text(encoding="utf-8")
        head = s[:s.index("from __future__")]
        assert "must not reset the regime to neutral" in head

    def test_unknown_not_in_increase_whitelist(self):
        """反面确认：「未知」确实拿不到加仓权（否则上面的 fail-closed 是空话）。"""
        sys.path.insert(0, str(ROOT / "07_tools"))
        import runtime_guards as rg
        assert rg.normalize_regime("未知") == "未知"
        assert "未知" not in rg._REGIME_ALLOW_INCREASE
        assert rg.normalize_regime("") == "未知", "空值也必须归到未知，不能漏成可加仓"
