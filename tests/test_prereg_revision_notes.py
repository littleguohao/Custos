# -*- coding: utf-8 -*-
"""判据节变更 ⇒ 修订注记的机械核对（owner 方法论 review #11，v0.327）。

研究产物 provenance 记录 ``pre_reg_doc`` + ``pre_reg_blob_hash``（v0.325/#8
落地、v0.327 补文档路径）。本测试核对：文档**当前**内容的 blob hash 与产物
引用的 hash 不一致时，文档里必须留「修订」注记——「修订是在看到读数之前
还是之后」由此可查证，不靠自觉声明。

- 无产物的环境（CI/开发机——artifacts/logs 不入库）空扫描 ⇒ 通过；
  生产机（产物落盘处）实质守卫。
- 口径已知局限：blob hash 是整文档内容寻址，改判据节以外的错别字同样
  触发注记要求——当作「被引用文档的任何改动都留一行注记」的纪律接受，
  换来实现的机械简单。
"""

from __future__ import annotations

import json
from pathlib import Path

from custos.core.paths import BASE
from custos.research import provenance

LOGS_DIR = BASE / "artifacts" / "logs"
REVISION_MARK = "修订"
_SCAN_CAP = 500
_MAX_BYTES = 10 * 1024 * 1024


def _iter_provenance(logs_dir: Path):
    """产出扫描到的 provenance 块（坏 JSON/超大文件/无块一律跳过，不炸）。"""
    if not logs_dir.is_dir():
        return
    scanned = 0
    for path in sorted(logs_dir.rglob("*.json")):
        if scanned >= _SCAN_CAP:
            return
        scanned += 1
        try:
            if path.stat().st_size > _MAX_BYTES:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        prov = payload.get("provenance") if isinstance(payload, dict) else None
        if isinstance(prov, dict):
            yield path, prov


def stale_prereg_refs(logs_dir: Path) -> list[dict]:
    """列出「产物引用的预注册文档已变」的条目（doc 缺失/字段缺 ⇒ 跳过）。

    返回项：artifact / doc / recorded_hash / current_hash / has_revision_note。
    """
    stale: dict[tuple[str, str], dict] = {}
    for artifact, prov in _iter_provenance(logs_dir) or []:
        doc_raw = prov.get("pre_reg_doc")
        recorded = prov.get("pre_reg_blob_hash")
        if not doc_raw or not recorded:
            continue
        doc = Path(doc_raw)
        if not doc.is_absolute():
            doc = BASE / doc
        if not doc.is_file():
            continue
        key = (str(doc), recorded)
        if key not in stale:
            current = provenance.git_blob_hash(doc)
            if current is None or current == recorded:
                stale[key] = None  # 一致（或无法核对）⇒ 占位，不算违规
                continue
            try:
                text = doc.read_text(encoding="utf-8")
            except OSError:
                text = ""
            stale[key] = {
                "doc": str(doc),
                "recorded_hash": recorded,
                "current_hash": current,
                "has_revision_note": REVISION_MARK in text,
                "artifacts": [],
            }
        if stale[key] is not None:
            stale[key]["artifacts"].append(str(artifact))
    return [v for v in stale.values() if v is not None]


def test_stale_prereg_refs_require_revision_note():
    """仓库实扫：产物引用的文档变了 ⇒ 文档必须留修订注记。"""
    offenders = [v for v in stale_prereg_refs(LOGS_DIR) if not v["has_revision_note"]]
    assert not offenders, (
        "以下预注册文档在产物落盘后内容已变，但文档里找不到「修订」注记"
        "（判据修订必须在跑数前留痕，owner 方法论 review #11）："
        + json.dumps(offenders, ensure_ascii=False, indent=2)
    )


def _write_artifact(logs: Path, name: str, doc: Path, blob_hash: str) -> None:
    logs.mkdir(parents=True, exist_ok=True)
    (logs / name).write_text(
        json.dumps(
            {
                "provenance": {
                    "pre_reg_doc": str(doc),
                    "pre_reg_blob_hash": blob_hash,
                }
            }
        ),
        encoding="utf-8",
    )


def test_stale_detection_unchanged_doc_passes(tmp_path):
    doc = tmp_path / "R99.md"
    doc.write_text("## 判据\nC1 …\n", encoding="utf-8")
    old = provenance.git_blob_hash(doc)
    _write_artifact(tmp_path / "logs", "_x__t1.json", doc, old)
    assert stale_prereg_refs(tmp_path / "logs") == []


def test_stale_detection_changed_doc_with_note_passes(tmp_path):
    doc = tmp_path / "R99.md"
    doc.write_text("## 判据\nC1 旧口径\n", encoding="utf-8")
    old = provenance.git_blob_hash(doc)
    _write_artifact(tmp_path / "logs", "_x__t2.json", doc, old)
    doc.write_text(
        "## 判据\nC1 新口径\n\n**C1 口径修订（2026-10-10，跑数前补记）**……\n",
        encoding="utf-8",
    )
    refs = stale_prereg_refs(tmp_path / "logs")
    assert len(refs) == 1 and refs[0]["has_revision_note"] is True


def test_stale_detection_changed_doc_without_note_flagged(tmp_path):
    doc = tmp_path / "R99.md"
    doc.write_text("## 判据\nC1 旧口径\n", encoding="utf-8")
    old = provenance.git_blob_hash(doc)
    _write_artifact(tmp_path / "logs", "_x__t3.json", doc, old)
    doc.write_text("## 判据\nC1 新口径（悄悄改的）\n", encoding="utf-8")
    refs = stale_prereg_refs(tmp_path / "logs")
    assert len(refs) == 1
    assert refs[0]["has_revision_note"] is False
    assert refs[0]["artifacts"] and refs[0]["recorded_hash"] == old


def test_stale_detection_skips_missing_doc_and_fields(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "_x__t4.json").write_text(
        json.dumps({"provenance": {"unit": "R99"}}), encoding="utf-8"
    )
    _write_artifact(logs, "_x__t5.json", tmp_path / "gone.md", "0" * 40)
    assert stale_prereg_refs(logs) == []
