# -*- coding: utf-8 -*-
"""提案中继（--agent-relay）：agent 提案、脚本决策 的第二提案通道。

动机：进化引擎的 LLM 触点全部收敛在 ``ChatLLM.chat()``（operators.propose /
mutate / crossover / interpret）。API 直连模式（``llm_client.LLMClient``，
CUSTOS_LLM_* 环境变量）之外，本模块提供第二种提案来源——**文件中继**：

    脚本把请求落盘 → 退出码 3（EXIT_PAUSE）→ 外部 agent（Kimi Work 会话 /
    定时任务）读请求、写应答 → 脚本从断点续跑。

判定链、DSL 白名单门、双窗物理隔离**全程不动**——「LLM 只提案，decision 由
确定性函数给出」的纪律在两种提案来源下完全一致；提案来源只在 summary
config 块的 ``llm_source`` 标注（谱系可比性：relay 轨迹的提案来自 agent，
与 API 模型提案的轨迹对照时请按 llm_source 分组解读）。

握手协议（单槽位；目录默认 ``{out_dir}/relay/``，--relay-dir 可改）：

- ``pending_request.json``  脚本写：当前待答请求
  （schema_version / request_id / request_hash / messages / json_mode /
  json_schema）；
- ``response.json``  agent 写：``{"request_id": ..., "content": ...}``；
- ``answered/{request_id}.json``  应答被消费后归档（防二次消费）。

断点文件 ``{out_dir}/relay_checkpoint.json``：循环位标 + 该候选起步时的
rng 状态 + **当前候选段**的已应答 transcript（跨候选不保留——已完成候选
的调用不会再发生）+ 待答请求 + 配置指纹。配置指纹不符（换了参数续跑）/
应答 request_id 不符 / 断点损坏 → 一律 fail-closed。

确定性基石 = **transcript 重放**：续跑进程的第 n 次 chat() 校验请求哈希与
transcript[n] 一致后直接返回已录应答——同一断点无论重启多少次，循环看见
的 LLM 产出逐位相同；哈希不符说明断点被换芯，立即 RelayError（不被
_step 的 LLMError 兜底吞掉，直接终止运行）。

成本注记：退出模式下每次 LLM 往返 = 一次进程重启 + 重载 K 线 + 重算暂停候选。
v0.449 起有**常驻模式**（``--relay-wait SECONDS``）：脚本落请求后原地轮询
response.json，进程与已加载数据全程不退出；每次待答照样先落断点，等待超时
或进程被杀都能用同一命令行按退出模式续跑（两种模式共用同一断点协议）。
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1

CHECKPOINT_NAME = "relay_checkpoint.json"
RELAY_DIR_NAME = "relay"
PENDING_NAME = "pending_request.json"
RESPONSE_NAME = "response.json"
ANSWERED_DIR = "answered"

# 暂停退出码：脚本落完请求与断点后以此码退出（区别于参数错 2 / 护栏 2）。
EXIT_PAUSE = 3


class RelayError(RuntimeError):
    """中继协议违例（断点损坏 / 指纹不符 / 应答错号 / transcript 重放不符）。

    刻意不继承 LLMError：loop._step 只兜底 LLMError（记事件跳过候选），
    协议违例必须终止整个运行——静默跳过会让断点续跑产出与单次运行分叉。
    """


class RelayPause(Exception):
    """LLM 请求已就绪、等待 agent 应答：携带请求与断点要素，由 CLI 落盘退出。

    ``position`` / ``rng_state`` 由 run_loop 在候选边界快照后挂上（本候选
    起步时的 rng 状态——续跑从候选起点重进，transcript 重放已应答调用，
    下游全确定性）。
    """

    def __init__(self, request: dict[str, Any]) -> None:
        super().__init__(f"等待 agent 应答: {request.get('request_id')}")
        self.request = request
        self.position: tuple[int, int, int] | None = None
        self.rng_state: Any = None


@dataclass(frozen=True)
class ResumeState:
    """断点续跑状态：候选位标 (direction_i, round_i, cand_i) + 该候选起步
    时的 rng 状态（JSONable）。位标之前的候选一律跳过（轨迹已在池里）。"""

    position: tuple[int, int, int]
    rng_state: Any


# ── 规范化与指纹 ─────────────────────────────────────────────────────────────


def _canon(obj: Any) -> str:
    """确定性 JSON 序列化（哈希输入）：键排序、无空格、不允许 NaN。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_hash(
    messages: list[dict], json_mode: bool, json_schema: dict | None
) -> str:
    """一次 chat() 调用的内容指纹（重放校验的唯一依据）。"""
    return hashlib.sha256(
        _canon(
            {"messages": messages, "json_mode": json_mode, "json_schema": json_schema}
        ).encode("utf-8")
    ).hexdigest()


def cfg_fingerprint(cfg_dict: dict[str, Any]) -> str:
    """LoopConfig asdict 的指纹（断点只能被同配置的运行续跑）。"""
    return hashlib.sha256(_canon(cfg_dict).encode("utf-8")).hexdigest()


# ── rng 状态 <-> JSON ─────────────────────────────────────────────────────────


def rng_to_json(state: Any) -> Any:
    """random.Random.getstate() → JSONable（嵌套 tuple 递归转 list）。"""
    if isinstance(state, tuple):
        return [rng_to_json(x) for x in state]
    return state


def rng_from_json(obj: Any) -> Any:
    """rng_to_json 的逆变换（list 递归转回 tuple），供 rng.setstate()。"""
    if isinstance(obj, list):
        return tuple(rng_from_json(x) for x in obj)
    return obj


def snapshot_rng(rng: random.Random) -> Any:
    """候选边界的 rng 快照（断点存的就是它）。"""
    return rng_to_json(rng.getstate())


# ── RelayLLM ──────────────────────────────────────────────────────────────────


class RelayLLM:
    """文件中继 proposer：与 LLMClient / MockLLM 同构（chat + total_tokens）。

    transcript 是**当前候选段**的已应答记录（跨候选不保留——已完成候选的
    调用不会再发生，其应答已随轨迹落盘，归档在 relay/answered/）：

    - run_loop 在每个候选起步调 ``begin_candidate()`` 开新段（清空段记录）；
      断点续跑的目标候选除外——其段记录由 ``load_transcript`` 装配；
    - 段内第 n 次 chat()：n < len(transcript) → 校验 request_hash 后返回
      已录应答（重放，逐位确定）；n == len(transcript) → raise RelayPause；
    - total_tokens 恒 0：中继模式拿不到 token 计数，--max-tokens-budget
      在本模式下不生效（CLI 层打横幅明示）。
    """

    def __init__(
        self,
        transcript: list[dict[str, Any]] | None = None,
        *,
        waiter: Callable[[dict[str, Any]], str] | None = None,
    ) -> None:
        self.transcript: list[dict[str, Any]] = list(transcript or [])
        self._served = 0
        self.total_tokens = 0
        # 常驻模式（v0.449）：新请求交给 waiter 原地等应答（waiter 超时可 raise RelayPause
        # 回落退出模式）；None = 退出模式（raise RelayPause）
        self.waiter = waiter
        # 当前候选位标与起步 rng 快照（run_loop 每候选起步经 mark_candidate 挂上，
        # 常驻模式 waiter 落断点用）
        self.position: tuple[int, int, int] | None = None
        self.rng_state: Any = None
        # 断点里的 LLM 级错误累计（v0.449：跨暂停累加；CLI 挂上取数函数）
        self.error_count_fn: Callable[[], int] | None = None

    def mark_candidate(self, position: tuple[int, int, int], rng_state: Any) -> None:
        self.position = position
        self.rng_state = rng_state

    def begin_candidate(self) -> None:
        """开新候选段：清空段记录（run_loop 在每个候选起步调用）。

        断点续跑的目标候选**不调**本方法——段记录已由 load_transcript 装配。
        """
        self.transcript = []
        self._served = 0

    def load_transcript(self, transcript: list[dict[str, Any]]) -> None:
        """断点续跑时装配本候选段的已应答 transcript（须在段内首次 chat 前）。"""
        if self._served:
            raise RelayError("load_transcript 必须在段内首次 chat 之前调用")
        self.transcript = list(transcript)

    def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = True,
        json_schema: dict | None = None,
    ) -> str:
        msgs = [dict(m) for m in messages]
        h = request_hash(msgs, json_mode, json_schema)
        n = self._served
        self._served += 1
        if n < len(self.transcript):
            entry = self.transcript[n]
            if entry.get("request_hash") != h:
                raise RelayError(
                    f"transcript 重放不符（第 {n} 次调用）：断点记录 "
                    f"{entry.get('request_hash')!r}，本次请求 {h!r}——"
                    "断点被换芯/配置漂移，拒绝继续"
                )
            return str(entry["response"])
        request = {
            "schema_version": SCHEMA_VERSION,
            "request_id": f"{n:05d}_{h[:12]}",
            "request_hash": h,
            "messages": msgs,
            "json_mode": json_mode,
            "json_schema": json_schema,
        }
        if self.waiter is None:
            raise RelayPause(request)
        content = self.waiter(request)  # 超时 ⇒ waiter 自己 raise RelayPause
        self.transcript.append({**request, "response": content})
        return content


# ── 断点与请求/应答文件 ───────────────────────────────────────────────────────


def checkpoint_path(out_dir: Path) -> Path:
    return out_dir / CHECKPOINT_NAME


def relay_dir_for(out_dir: Path, relay_dir_arg: str = "") -> Path:
    return Path(relay_dir_arg) if relay_dir_arg else out_dir / RELAY_DIR_NAME


def _write_json(path: Path, payload: Any) -> None:
    """原子写（tmp + replace），与轨迹池同款式。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _read_json(path: Path, what: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise RelayError(f"{what}读取/解析失败: {path}: {exc}") from exc


def save_checkpoint(
    path: Path,
    *,
    tag: str,
    cfg_fp: str,
    position: tuple[int, int, int],
    rng_state: Any,
    transcript: list[dict[str, Any]],
    pending: dict[str, Any],
    llm_errors: int = 0,
) -> None:
    """落断点（pause 时）：位标 + rng 快照 + transcript + 待答请求 + LLM 级错误累计。"""
    _write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "tag": tag,
            "cfg_fingerprint": cfg_fp,
            "position": list(position),
            "rng_state": rng_state,
            "transcript": transcript,
            "pending": pending,
            "llm_errors": int(llm_errors),  # v0.449：跨暂停累加（旧断点无此键按 0）
        },
    )


def load_checkpoint(path: Path) -> dict[str, Any] | None:
    """无断点 → None；损坏 / 形状不符 → RelayError（fail-closed）。"""
    if not path.exists():
        return None
    ck = _read_json(path, "断点")
    if not isinstance(ck, dict) or ck.get("schema_version") != SCHEMA_VERSION:
        raise RelayError(f"断点版本不符或顶层不是对象: {path}")
    for key in (
        "tag",
        "cfg_fingerprint",
        "position",
        "rng_state",
        "transcript",
        "pending",
    ):
        if key not in ck:
            raise RelayError(f"断点缺字段 {key!r}: {path}")
    pos = ck["position"]
    if (
        not isinstance(pos, list)
        or len(pos) != 3
        or any(isinstance(v, bool) or not isinstance(v, int) for v in pos)
    ):
        raise RelayError(f"断点位标形状非法: {pos!r}")
    if not isinstance(ck["transcript"], list) or not isinstance(ck["pending"], dict):
        raise RelayError("断点 transcript/pending 形状非法")
    return ck


def clear_checkpoint(path: Path) -> None:
    """运行正常收尾后清断点（missing_ok——首跑无断点可清）。"""
    path.unlink(missing_ok=True)


def write_pending(relay_dir: Path, request: dict[str, Any]) -> Path:
    """落待答请求（覆盖式单槽位：同一时刻只有一个待答请求）。"""
    path = relay_dir / PENDING_NAME
    _write_json(path, request)
    return path


def clear_pending(relay_dir: Path) -> None:
    (relay_dir / PENDING_NAME).unlink(missing_ok=True)


def read_response(relay_dir: Path, pending: dict[str, Any]) -> str:
    """读 agent 应答并校验：缺文件 / 形状非法 / request_id 不符 → RelayError。

    通过校验后把 response.json 归档到 answered/{request_id}.json（防二次
    消费：同一应答被两次续跑吃进去会让 transcript 错位且全程无报错）。
    返回 content 文本（非空字符串，空应答 fail-closed）。
    """
    path = relay_dir / RESPONSE_NAME
    want_id = pending.get("request_id")
    archived = relay_dir / ANSWERED_DIR / f"{want_id}.json"
    if not path.exists() and want_id and archived.exists():
        # v0.449 崩溃窗口自愈：上次续跑已消费并归档该应答、但在落新断点前进程死了——
        # 断点 pending 仍指向它，同 id 应答可安全复用（内容即上次喂给循环的那份）
        resp = _read_json(archived, "归档应答")
        content = resp.get("content") if isinstance(resp, dict) else None
        if (
            isinstance(resp, dict)
            and resp.get("request_id") == want_id
            and isinstance(content, str)
            and content.strip()
        ):
            return content
        raise RelayError(f"归档应答损坏: {archived}")
    if not path.exists():
        raise RelayError(
            f"断点存在但缺应答文件 {path}——请先按 {relay_dir / PENDING_NAME} "
            "写出 response.json 再续跑"
        )
    resp = _read_json(path, "应答")
    if not isinstance(resp, dict):
        raise RelayError(f"应答顶层必须是对象: {path}")
    want = pending.get("request_id")
    if resp.get("request_id") != want:
        raise RelayError(
            f"应答 request_id 不符: 得到 {resp.get('request_id')!r}，"
            f"待答 {want!r}——请确认回答的是当前 pending_request.json"
        )
    content = resp.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RelayError("应答 content 为空或非字符串（fail-closed）")
    archive = relay_dir / ANSWERED_DIR / f"{want}.json"
    _write_json(archive, resp)
    path.unlink()
    return content
