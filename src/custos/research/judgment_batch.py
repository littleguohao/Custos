#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判定段批次锁（TODO #95，owner 2026-10-14 拍板「判定段攒批协议」的代码闸门）。

规范出处 = `governance/research/README.md`「判定段攒批协议」：一批只读一次、
候选冻结配置哈希预注册后入批、同批一次读出判定段 2017~2021、读后关批、
**批外不得零散补读**。本模块把协议机械化：

- 登记文件 ``governance/research/judgment_batches.jsonl``（**入库**、
  append-only 事件日志、每日首写快照——与 window_usage.jsonl 同纪律：
  生产机写入并提交；测试经 conftest 改道 tmp）；
- 事件行：``open``（batch_id/candidates[{id,config_hash,unit,note}]/m）/
  ``claim``（**过闸即占位**，v0.375 #99②——候选/哈希/claim_at；此后再次过闸
  一律拒绝，**宁可错杀：崩溃/中断也算已消耗**，需重读由 owner 手动开新批）/
  ``read``（跑完登记，candidate_id/tool/tag/read_at）/``close``（手动关 +
  末位候选读完自动关——按 read 计；claimed-未读 的候选会挡住自动关批）；
- 门禁：7+1 个研究终端在**数据加载之前**调 ``gate_judgment_read``——
  合成运行（注入件非 None，入口快照）不查批；判定窗与 SEG_JUDGMENT 不相交
  （legacy 旧窗）不查批；否则要求 --batch-id/--candidate-id 且
  ``claim_read`` 六路校验全过（过闸即写 claim）；收尾 record_use 处补
  ``mark_read``（read 是完成记录，自身已堵重读，故不要求先有 claim）。

fail-closed：所有状态机违例 raise ValueError（中文错因），CLI/各终端 ap.error
转 exit 2。登记文件损坏行跳过不炸（同台账容错），但**坏行会导致重放状态不可信
的方向是 fail-closed 的**（找不到 open ⇒ 批不存在 ⇒ 拒读）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

# GBK（cp936）终端/管道打不了 ⚠️/⛔ 等符号 —— 不 reconfigure 会 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from custos.core.paths import RESEARCH_DIR, cn_now  # noqa: E402

#: 批次登记文件（治理数据，**入库**；测试由 conftest autouse 改道 tmp）
REGISTRY = RESEARCH_DIR / "judgment_batches.jsonl"

#: 配置哈希剔除的簿记键（不影响口径的参数不进指纹）
_BOOKKEEP_KEYS = {
    "out",
    "cmdline",
    "batch_id",
    "candidate_id",
    "print_config_hash",
    "func",
    # 「count」（v0.375，#99①）：缺省时 resolve_count 按「窗口起点→今天」的
    # 工作日数推算，**随日漂移**——冻结次日即失配。count 只影响起点可达性、
    # 不影响读数（基线 runbook 同口径），可达性由 check_reach 兜底 ⇒ 不进指纹。
    "count",
}


# ---------------------------------------------------------------------------
# 事件日志读写（append-only + 每日首写快照，同 window_usage 纪律）
# ---------------------------------------------------------------------------


def _load_events(path: Optional[Path]) -> list[dict]:
    p = Path(path) if path else REGISTRY
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            print(f"[WARN] 批次登记坏行跳过: {ln[:80]}", file=sys.stderr)
            continue
        if isinstance(ev, dict) and ev.get("ev") in ("open", "claim", "read", "close"):
            out.append(ev)
    return out


def _append(ev: dict[str, Any], path: Optional[Path]) -> None:
    p = Path(path) if path else REGISTRY
    if p.is_file():  # 每日首写前快照（0AMV 事故纪律，同 window_usage）
        bak = p.with_name(f"{p.name}.bak_{cn_now().strftime('%Y%m%d')}")
        if not bak.exists():
            try:
                bak.write_bytes(p.read_bytes())
            except OSError as exc:
                print(f"[WARN] 批次登记快照失败: {exc}", file=sys.stderr)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _replay(events: list[dict]) -> dict[str, dict[str, Any]]:
    """事件日志 → {batch_id: 状态机}。重复 open 在 open_batch 前置校验里拦。"""
    batches: dict[str, dict[str, Any]] = {}
    for ev in events:
        bid = str(ev.get("batch_id") or "")
        if ev["ev"] == "open":
            batches[bid] = {
                "opened_at": ev.get("opened_at"),
                "candidates": {c["id"]: c for c in ev.get("candidates", [])},
                "m": ev.get("m"),
                "closed_at": None,
                "claims": {},
                "reads": {},
            }
        elif bid not in batches:
            continue  # claim/read/close 落在未知批上（坏行）——重放跳过，写入侧另行校验
        elif ev["ev"] == "claim":
            batches[bid]["claims"][str(ev.get("candidate_id"))] = ev
        elif ev["ev"] == "read":
            batches[bid]["reads"][str(ev.get("candidate_id"))] = ev
        else:  # close
            batches[bid]["closed_at"] = ev.get("closed_at")
    return batches


def _batch(batches: dict[str, dict], batch_id: str) -> dict[str, Any]:
    if batch_id not in batches:
        raise ValueError(
            f"批次 {batch_id!r} 不存在（登记文件无 open 记录）——先 open 开批再读"
        )
    return batches[batch_id]


# ---------------------------------------------------------------------------
# 配置哈希（冻结流程：打算跑数的那条命令加 --print-config-hash，贴进 open）
# ---------------------------------------------------------------------------


def config_hash(tool_name: str, args: Any) -> str:
    """候选冻结配置指纹：vars(args) 去簿记键与 None 值，sort_keys json 的
    sha256 前 16 位。"""
    d = {
        k: v for k, v in vars(args).items() if k not in _BOOKKEEP_KEYS and v is not None
    }
    s = json.dumps(d, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 状态机 API（全部 fail-closed raise ValueError；CLI 层转 exit 2）
# ---------------------------------------------------------------------------


def open_batch(
    batch_id: str, candidates: list[dict[str, str]], *, path: Optional[Path] = None
) -> None:
    """开批：batch_id 历史上存在过即拒；空名册/缺字段即拒。"""
    batch_id = str(batch_id).strip()
    if not batch_id:
        raise ValueError("批次号不能为空")
    if not candidates:
        raise ValueError("候选名册为空——至少一个候选才允许开批")
    ids = [str(c.get("id") or "") for c in candidates]
    if any(not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError(f"候选名册 id 缺失或重复: {ids}")
    for c in candidates:
        if not str(c.get("config_hash") or "").strip():
            raise ValueError(
                f"候选 {c.get('id')!r} 缺 config_hash——先给跑数命令加 "
                "--print-config-hash 冻结配置再入批"
            )
    batches = _replay(_load_events(path))
    if batch_id in batches:
        raise ValueError(
            f"批次 {batch_id!r} 历史上已存在（批次号只增不复用）——换一个新批次号"
        )
    _append(
        {
            "ev": "open",
            "batch_id": batch_id,
            "opened_at": cn_now().isoformat(timespec="seconds"),
            "candidates": candidates,
            "m": len(candidates),
        },
        path,
    )


def check_read(
    batch_id: str,
    candidate_id: str,
    cfg_hash: str,
    *,
    path: Optional[Path] = None,
) -> bool:
    """六路校验（各一句中文错因，fail-closed raise ValueError）：

    批不存在 / 批已关 / 候选不在批 / 配置哈希不匹配 / 候选已读 / 候选已认领
    （claim 占位，含崩溃未读完的情形）。全过 ⇒ True。
    """
    batches = _replay(_load_events(path))
    b = _batch(batches, batch_id)
    if b["closed_at"]:
        raise ValueError(
            f"批次 {batch_id!r} 已于 {b['closed_at']} 关闭——批外不得零散补读"
            "（攒批协议：一批只读一次，新读数开新批）"
        )
    cand = b["candidates"].get(str(candidate_id))
    if cand is None:
        raise ValueError(
            f"候选 {candidate_id!r} 不在批次 {batch_id!r} 名册里"
            f"（名册: {sorted(b['candidates'])}）"
        )
    if str(cand.get("config_hash")) != str(cfg_hash):
        raise ValueError(
            f"候选 {candidate_id!r} 配置哈希不匹配：登记 {cand.get('config_hash')} "
            f"vs 本次 {cfg_hash}——配置冻结后有改动，须预注册修订并重新入批"
        )
    if str(candidate_id) in b["reads"]:
        raise ValueError(
            f"候选 {candidate_id!r} 已于 {b['reads'][str(candidate_id)].get('read_at')} "
            "读过判定段——一批只读一次"
        )
    if str(candidate_id) in b["claims"]:
        raise ValueError(
            f"候选 {candidate_id!r} 已于 "
            f"{b['claims'][str(candidate_id)].get('claim_at')} 过闸认领（claim）——"
            "宁可错杀：崩溃/中断也算已消耗本次读取，需重读由 owner 手动开新批次"
        )
    return True


def claim_read(
    batch_id: str,
    candidate_id: str,
    cfg_hash: str,
    tool: str,
    *,
    path: Optional[Path] = None,
) -> None:
    """过闸即占位（v0.375，#99②）：六路校验全过后立即写 ``claim`` 事件。

    为什么不在跑完才登记（旧 mark_read-only 形态）：过闸到 read 之间崩溃/
    Ctrl-C 会让候选保持「未读」⇒「一批只读一次」被绕开。宁可错杀——占位后
    再次过闸一律拒绝，确需重读由 owner 手动开新批次。
    """
    check_read(batch_id, candidate_id, cfg_hash, path=path)
    _append(
        {
            "ev": "claim",
            "batch_id": batch_id,
            "candidate_id": str(candidate_id),
            "config_hash": str(cfg_hash),
            "tool": str(tool),
            "claim_at": cn_now().isoformat(timespec="seconds"),
        },
        path,
    )


def mark_read(
    batch_id: str,
    candidate_id: str,
    tool: str,
    tag: str,
    *,
    path: Optional[Path] = None,
) -> None:
    """读完登记：追加 read 事件；**末位候选读完自动追加 close**（关批）。"""
    batches = _replay(_load_events(path))
    b = _batch(batches, batch_id)
    if b["closed_at"]:
        raise ValueError(f"批次 {batch_id!r} 已关闭，不能补登记 read")
    if str(candidate_id) not in b["candidates"]:
        raise ValueError(f"候选 {candidate_id!r} 不在批次 {batch_id!r} 名册里")
    if str(candidate_id) in b["reads"]:
        raise ValueError(f"候选 {candidate_id!r} 已登记过 read（重复登记）")
    _append(
        {
            "ev": "read",
            "batch_id": batch_id,
            "candidate_id": str(candidate_id),
            "tool": str(tool),
            "tag": str(tag),
            "read_at": cn_now().isoformat(timespec="seconds"),
        },
        path,
    )
    if len(b["reads"]) + 1 >= len(b["candidates"]):
        close_batch(batch_id, path=path)  # 末位候选读完 ⇒ 自动关批
        print(
            f"[INFO] 批次 {batch_id} 全部候选已读完，自动关批",
            file=sys.stderr,
        )


def close_batch(batch_id: str, *, path: Optional[Path] = None) -> None:
    """手动关批（owner 发令）。已关 ⇒ 拒。"""
    batches = _replay(_load_events(path))
    b = _batch(batches, batch_id)
    if b["closed_at"]:
        raise ValueError(f"批次 {batch_id!r} 已于 {b['closed_at']} 关闭（重复关批）")
    _append(
        {
            "ev": "close",
            "batch_id": batch_id,
            "closed_at": cn_now().isoformat(timespec="seconds"),
        },
        path,
    )


def status(batch_id: Optional[str] = None, *, path: Optional[Path] = None) -> dict:
    """批状态汇总：{batch_id: {m, read, claimed, unread, closed_at}}。"""
    batches = _replay(_load_events(path))
    out = {}
    for bid, b in batches.items():
        if batch_id and bid != batch_id:
            continue
        read = sorted(b["reads"])
        claimed = sorted(b["claims"])
        out[bid] = {
            "opened_at": b["opened_at"],
            "m": b["m"],
            "closed_at": b["closed_at"],
            "read": read,
            "claimed": claimed,
            "in_flight": sorted(set(claimed) - set(read)),  # 认领未读完（含崩溃）
            "unread": sorted(set(b["candidates"]) - set(read)),
        }
    return out


# ---------------------------------------------------------------------------
# 门禁辅助（各研究终端共用）：入口快照 synthetic → 不相交不查 → 查批
# ---------------------------------------------------------------------------


def gate_judgment_read(
    args: Any,
    ap: argparse.ArgumentParser,
    *,
    synthetic: bool,
    judgment_span: Optional[tuple[str, str]],
    tool: str,
) -> bool:
    """判定段批次门（fail-closed，ap.error ⇒ exit 2）。返回 True = 已过闸
    （**过闸即写 claim 占位**——v0.375 #99②，崩溃/中断也算已消耗；调用方
    收尾补 mark_read）；False = 不查（合成运行 / 判定窗与受保护段
    不相交——legacy 旧窗已并入挖掘 B，读挖掘段自由）。

    ``synthetic`` 必须在**函数入口快照**（任何注入件非 None ⇒ 合成 ⇒ 不查批
    也不写台账，同 record_use 的合成守卫语义）。"""
    from custos.research import load_window as lw  # noqa: PLC0415

    if synthetic:
        return False
    if judgment_span is None:
        return False
    if not lw.touches_judgment(judgment_span[0], judgment_span[1]):
        return False
    if not getattr(args, "batch_id", "") or not getattr(args, "candidate_id", ""):
        ap.error(
            "判定段 2017~2021 走攒批协议（README『判定段攒批协议』）：须带 "
            "--batch-id 与 --candidate-id（候选须先冻结配置哈希入批——给跑数命令加 "
            "--print-config-hash 打印指纹，judgment_batch open 入批后同批一次读出）"
        )
    try:
        claim_read(args.batch_id, args.candidate_id, config_hash(tool, args), tool)
    except ValueError as exc:
        ap.error(f"判定段批次锁拒绝：{exc}")
    args._judgment_gated = True  # 收尾 record_use 处凭此补 mark_read（跨函数传递）
    return True


def print_config_hash_and_exit(args: Any, tool: str) -> int:
    """--print-config-hash 的处理面：打印 <tool>:<hash>，return 0（不碰数据）。"""
    print(f"{tool}:{config_hash(tool, args)}")
    return 0


# ---------------------------------------------------------------------------
# CLI：open / close / status
# ---------------------------------------------------------------------------


def _parse_candidate(spec: str) -> dict[str, str]:
    """``id:config_hash:unit:note``（note 可含冒号；unit/note 可省）。"""
    parts = spec.split(":", 3)
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        raise ValueError(
            f"--candidate 形态非法: {spec!r}（须 id:config_hash[:unit[:note]]）"
        )
    return {
        "id": parts[0].strip(),
        "config_hash": parts[1].strip(),
        "unit": parts[2].strip() if len(parts) > 2 else "",
        "note": parts[3].strip() if len(parts) > 3 else "",
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["open", "close", "status"])
    ap.add_argument("--batch-id", default="")
    ap.add_argument(
        "--candidate",
        action="append",
        default=[],
        help="open 用：id:config_hash[:unit[:note]]（可多次）",
    )
    args = ap.parse_args(argv)
    try:
        if args.cmd == "open":
            cands = [_parse_candidate(s) for s in args.candidate]
            open_batch(args.batch_id, cands)
            print(f"[OK] 批次 {args.batch_id} 已开（m={len(cands)}）")
        elif args.cmd == "close":
            close_batch(args.batch_id)
            print(f"[OK] 批次 {args.batch_id} 已关")
        else:
            st = status(args.batch_id or None)
            if not st:
                print("（无批次记录）")
                return 0
            for bid, b in st.items():
                state = "已关" if b["closed_at"] else "开放中"
                inflight = f"，认领未读完 {b['in_flight']}" if b["in_flight"] else ""
                print(
                    f"{bid}: {state}，m={b['m']}，已读 {len(b['read'])}"
                    f"{inflight} / 未读 {b['unread'] or '无'}"
                )
        return 0
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
