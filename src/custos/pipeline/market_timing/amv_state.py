# -*- coding: utf-8 -*-
"""Persistent 0AMV regime state machine.

Once the regime enters 空头 it remains 空头 until a later confirmed daily
0AMV change is strictly greater than +4%. A daily reading between thresholds
must not reset the regime to neutral. Only readings with
quality == "confirmed" drive regime transitions; candidate/unconfirmed
readings crossing a threshold are recorded but keep the prior locked state.
"""

from __future__ import annotations
import argparse, json, os, sys


from custos.core.paths import cn_now, write_json_atomic, MARKET_DIR  # noqa: E402
from custos.core.paths import read_json as load  # noqa: E402
from custos.core.contracts import require  # noqa: E402

MARKET = MARKET_DIR
STATE = MARKET / "0amv_regime_history.json"
LEDGER = MARKET / "0amv_observations.jsonl"


def append_observation(day: str, amv: dict):
    value = amv.get("amv_change_pct")
    if value is None:
        return None
    record = {
        "date": day,
        "amv_change_pct": float(value),
        "as_of": amv.get("as_of") or day,
        "quality": amv.get("quality") or "candidate",
        "source": amv.get("source") or "market_timing_input",
        "recorded_at": cn_now().isoformat(timespec="seconds"),
    }
    existing = []
    if LEDGER.exists():
        existing = [
            json.loads(line)
            for line in LEDGER.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    # v0.156：quality 强弱序（candidate < confirmed）——升级必须落盘，不得被去重吞掉。
    # 先记 candidate 值 V、后报 confirmed 同值 V，旧判据会把 confirmed 吞掉，而读侧
    # 只采 confirmed，该日从 regime 重放消失（V 跨阈值时回测与 live 分叉）。
    _QUALITY_RANK = {"candidate": 0, "confirmed": 1}
    same = [
        x
        for x in existing
        if x.get("date") == day
        and x.get("amv_change_pct") == record["amv_change_pct"]
        # v0.156：superseded 记录已作废，与它的同值不构成去重理由——否则重报等于
        # 旧值的数被吞，生效值仍是冲突的新值。
        and not x.get("superseded")
        # v0.156：既有记录 quality 弱于新记录（candidate→confirmed）不算 same。
        and _QUALITY_RANK.get(x.get("quality") or "candidate", 0)
        >= _QUALITY_RANK.get(record["quality"], 0)
        # v0.152：去重不再看 source——同值同日从两个入口（人工 + market_timing_input
        # 自动回填）各记一遍，攒出 23 条同值重复（2026-08-30 去重清理过一轮）。
    ]
    if same:
        return same[-1]
    # v0.153（owner 拍板「每天只能有一个值」）：同日**不同值** = 纠错——
    # 旧记录标 superseded 留痕（事实台账不静默改写，作废标记即审计轨迹），
    # 新值照常追加；读侧（regime 构建）本就「后写覆盖先写」，作废标记防误读。
    conflicted = [
        x
        for x in existing
        if x.get("date") == day
        and x.get("amv_change_pct") != record["amv_change_pct"]
        and not x.get("superseded")
    ]
    if conflicted:
        for x in conflicted:
            x["superseded"] = True
            x["superseded_reason"] = (
                f"同日不同值纠错：{x.get('amv_change_pct')} 被 {record['amv_change_pct']} 覆盖"
            )
            x["superseded_at"] = record["recorded_at"]
        tmp = LEDGER.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in existing) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, LEDGER)
        print(
            f"[WARN] 0AMV {day} 纠错：旧值 {[x.get('amv_change_pct') for x in conflicted]}"
            f" → 新值 {record['amv_change_pct']}（旧记录已标 superseded 留痕）",
            file=sys.stderr,
        )
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _prior_state(hist: dict, day: str, initial: str | None, amv: dict) -> str:
    prior_dates = sorted(k for k in hist if k < day)
    return (
        hist[prior_dates[-1]]["effective_state"]
        if prior_dates
        else (initial or amv.get("prior_effective_state") or "未知")
    )


def _resolve_state(prior: str, value, quality: str, confirmed: bool) -> tuple[str, str]:
    if value is None:
        return prior, "缺值，延续前态"
    if float(value) > 4:
        if confirmed:
            return "做多", "单日涨幅>4%（confirmed），切换/维持做多"
        return (
            prior if prior in ("空头", "做多") else "中性"
        ), f"单日涨幅>4%但读数未确认（quality={quality}），不驱动状态转移"
    if float(value) < -2.3:
        if confirmed:
            return "空头", "单日跌幅<-2.3%（confirmed），切换/维持空头"
        return (
            prior if prior in ("空头", "做多") else "中性"
        ), f"单日跌幅<-2.3%但读数未确认（quality={quality}），不驱动状态转移"
    if prior == "空头":
        return "空头", "空头锁定；未达到>4%，继续空头"
    if prior == "做多":
        return "做多", "做多延续；未触发空头阈值"
    return "中性", "无已知锁定前态，处于阈值之间"


def _daily_zone(value) -> str:
    if value is not None and float(value) > 4:
        return "做多触发"
    if value is not None and float(value) < -2.3:
        return "空头触发"
    return "阈值内"


def compute(day: str, initial: str | None = None):
    hist = load(STATE, {})
    market_path = MARKET / f"{day}_market_timing_input.json"
    d = load(market_path, {})
    amv = d.setdefault("amv_0", {})
    value = amv.get("amv_change_pct")
    append_observation(day, amv)
    prior = _prior_state(hist, day, initial, amv)
    quality = amv.get("quality") or "candidate"
    confirmed = quality == "confirmed"
    state, transition = _resolve_state(prior, value, quality, confirmed)
    rec = {
        "date": day,
        "daily_change_pct": value,
        "prior_state": prior,
        "effective_state": state,
        "transition_reason": transition,
        "confirmed": confirmed,
    }
    hist[day] = rec
    # 累积状态：regime 全历史，损坏丢历史 ⇒ 原子写（见 paths.write_json_atomic）
    write_json_atomic(STATE, hist)
    amv.update(
        {
            "daily_zone": _daily_zone(value),
            "prior_effective_state": prior,
            "effective_state": state,
            "state_transition_reason": transition,
        }
    )
    # 读-改-写的共享文件：collector → merge → amv_state 依次改写同一份
    # 落盘前校验：本模块只写 amv_0 的 state 字段，其中 effective_state 的
    # 枚举域是审计 B1 的所在（见 merge_incremental_market 同款校验）。
    require("market_timing_input", d, only=("amv_0.effective_state",))
    write_json_atomic(market_path, d)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--initial-state", choices=["空头", "做多", "中性"])
    a = ap.parse_args()
    print(json.dumps(compute(a.date, a.initial_state), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
