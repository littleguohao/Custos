"""label_trade_dist（v0.423）：重读 R27 格子逐笔 → 分布画像；命中/补集对照；holdout 剔除；空结果护栏。"""

import json
import random

from custos.research import label_trade_dist as ltd
from custos.research import result_digest as rd


def _trades(codes, days, rng, bias=0.0):
    out = []
    for d in days:
        for c in codes:
            r = rng.gauss(0.01 + bias, 0.08)
            out.append(
                {
                    "code": c,
                    "entry_date": d,
                    "exit_date": d,
                    "ret": round(r, 4),
                    "reason": "stop" if r < -0.05 else "bbi_exit",
                    "holding": 5,
                    "risk_frac": 0.05,
                    "r_multiple": r / 0.05,
                }
            )
    return out


def _grid(tmp_path):
    rng = random.Random(0)
    days = [f"2023-03-{d:02d}" for d in range(1, 29)] + ["2026-09-07"]  # 末日落 holdout
    codes = [f"{k:06d}" for k in range(20)]
    base = _trades(codes, days, rng)
    hit = [t for t in base if int(t["code"]) % 4 == 0]  # 标注 = 基底子集
    rows = []
    for gate, tr in (("j_low", base), ("rsi_strong", hit)):
        f = f"baseline__{gate}__pct12_so5_bbi2__abc.json"
        (tmp_path / f).write_text(json.dumps({"trades": tr}), encoding="utf-8")
        rows.append(
            {
                "scorer": "baseline",
                "gate": gate,
                "exit": "pct12_so5_bbi2",
                "result_file": f,
            }
        )
    rows.append(
        {
            "scorer": "baseline",
            "gate": "b2",
            "exit": "pct12_so5_bbi2",
            "result_file": "missing.json",
        }
    )
    rows.append(
        {
            "scorer": "baseline",
            "gate": "b2",
            "exit": "base_low",
            "result_file": "x.json",
        }
    )
    p = tmp_path / "_ranked__rsi_family_cw.json"
    p.write_text(json.dumps({"results": rows}), encoding="utf-8")
    return p, base, hit


def test_end_to_end(tmp_path):
    rp, base, hit = _grid(tmp_path)
    out = tmp_path / "o.json"
    assert ltd.main(["--ranked", str(rp), "--out", str(out)]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    c = rep["cells"]
    assert set(c) == {
        "rsi_family_cw__j_low__pct12_so5_bbi2",
        "rsi_family_cw__rsi_strong__pct12_so5_bbi2",
    }
    lab = c["rsi_family_cw__rsi_strong__pct12_so5_bbi2"]
    n_hold_hit = sum(1 for t in hit if t["entry_date"] >= "2026-09-05")
    assert lab["n_holdout_dropped"] == n_hold_hit and lab["n"] == len(hit) - n_hold_hit
    assert lab["n_overlap_with_base"] == lab["n"]
    assert (
        lab["vs_complement"]["n"] + lab["n"]
        == c["rsi_family_cw__j_low__pct12_so5_bbi2"]["n"]
    )
    assert rep["skipped"] == [
        {"ranked": rp.name, "gate": "b2", "why": "格子产物缺失或无逐笔"}
    ]
    assert "vs_base" not in c["rsi_family_cw__j_low__pct12_so5_bbi2"]
    d = rd.build_digest("label_dist", out, "t")
    assert d["missing"] == []


def test_empty_guard(tmp_path):
    rp, _b, _h = _grid(tmp_path)
    assert (
        ltd.main(
            [
                "--ranked",
                str(rp),
                "--exit",
                "no_such",
                "--out",
                str(tmp_path / "e.json"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "e.json").exists()
