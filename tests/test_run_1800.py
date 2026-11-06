# -*- coding: utf-8 -*-
"""run_1800 阶段结论行钉测（合成 stage 结果，不跑真实子进程）。"""


def test_refresh_xdxr_partial_is_warned_not_ok(capsys):
    """v0.499：权息刷新被硬上限截断（stdout 有 [PARTIAL] 行）⇒ WARN，不显示 [OK]；
    out 是 stdout+stderr 合并，末行可能是 stderr，结论行须从 stdout 取。"""
    from custos.pipeline import run_1800 as r18

    def fake(cmd, name, note=""):
        return {
            "ok": True,
            "stdout": "[PARTIAL] 权息刷新到硬上限截断：处理 3/10",
            "stderr": "[WARN] 权息批量到达硬上限 480s",
            "out": "[PARTIAL] 权息刷新到硬上限截断：处理 3/10\n[WARN] 权息批量到达硬上限 480s",
        }

    r18._run_refresh_xdxr(fake)
    out = capsys.readouterr().out
    assert "[WARN] refresh_xdxr [PARTIAL]" in out and "[OK]" not in out

    def fake_ok(cmd, name, note=""):
        return {
            "ok": True,
            "stdout": "完成：5 只取到数据",
            "stderr": "x",
            "out": "完成：5 只取到数据\nx",
        }

    r18._run_refresh_xdxr(fake_ok)
    assert "[OK] 完成：5 只取到数据" in capsys.readouterr().out
