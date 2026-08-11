# -*- coding: utf-8 -*-
"""Append daily transactions to the immutable master ledger.

The ledger is append-only. Existing rows are never edited or removed. New rows
are identified by a stable fingerprint. Buy/sell additions incrementally update
current position quantity and unit cost; market value/P&L remain pending until
the next close revaluation.

Two invariants this module must never break (both were real defects):

1. **Ledger and positions must move together.** The old code called
   ``apply_positions`` (writing current_positions.json) *before*
   ``merged.to_csv(LEDGER)``. If the CSV write failed — Excel holding the file
   open, disk full, Ctrl-C — positions had already been increased while the
   ledger had no record of it, so the next run saw an unknown fingerprint and
   applied the very same fills a second time: **positions silently doubled**.
   Now both files are staged to temp files first and committed back to back
   with ``os.replace`` (see ``_commit``), and positions are computed in memory
   *before* anything is written so an oversell aborts the run with both files
   untouched.

2. **Re-importing the same file must be a no-op.** Deduplication is a
   *multiset* count difference, not a set membership test. Set membership
   dropped genuinely repeated fills (same second, same price, same size — a
   split order), and the ``--allow-identical`` escape hatch was file-wide, so
   re-importing one broker export double-counted every row in it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

TOOLS_DIR = Path(__file__).resolve().parents[1]
for _bp in (TOOLS_DIR, TOOLS_DIR.parent / "core"):  # core/: paths 等 L0 模块
    if str(_bp) not in sys.path:
        sys.path.insert(0, str(_bp))

from paths import BASE, cn_now, TRADES_DIR  # noqa: E402
from code_utils import clean_code, finite  # noqa: E402

TD = TRADES_DIR
LEDGER = TD / 'master_trade_ledger.csv'
AUDIT = TD / 'ledger_append_audit.jsonl'
POS = TD / 'current_positions.json'
CONFIRM = TD / 'position_confirmations.json'
STOCK_JSON = TD / 'trades_stock.json'

FIELDS = ['成交日期', '成交时间', '代码', '名称', '交易类别', '成交数量', '成交价格',
          '成交金额', '发生金额', '费用', '备注']
KEY = ['成交日期', '成交时间', '代码', '名称', '交易类别', '成交数量', '成交价格',
       '成交金额', '发生金额', '费用']
NUMERIC = ['成交数量', '成交价格', '成交金额', '发生金额', '费用']
TRADE_CATEGORIES = {'买入', '卖出'}

SNAPSHOT_PENDING = 'pending_close_revaluation'
SNAPSHOT_NOTE = '数量/成本已按增量成交更新；市值、盈亏、仓位须用最新收盘价重估'


def norm(df):
    """Normalize a raw broker export into the canonical ledger schema."""
    df = df.copy()
    for f in FIELDS:
        if f not in df:
            df[f] = ''
    df = df[FIELDS]
    df['成交日期'] = pd.to_datetime(df['成交日期'], errors='coerce').dt.strftime('%Y-%m-%d')
    df['成交时间'] = df['成交时间'].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
    df['代码'] = df['代码'].map(clean_code)
    for f in NUMERIC:
        df[f] = pd.to_numeric(df[f], errors='coerce')
    if df['成交日期'].isna().any() or df['代码'].eq('').any():
        raise ValueError('新增记录存在无效成交日期或代码')
    return df


def fingerprint(row):
    vals = []
    for k in KEY:
        v = row.get(k, '')
        v = '' if pd.isna(v) else v
        vals.append(str(v))
    return hashlib.sha256('|'.join(vals).encode('utf-8')).hexdigest()[:20]


def read_input(p):
    """读导入文件；`p is None` 视为空输入（仅 `--confirm-no-trades` 会走到）。

    ⚠️ 支持 None 是为了**不再逼操作者造空文件**：`--confirm-no-trades` 本就要求
    输入为空，此前 `--input` 却是必需的，于是每次无交易确认都要 `echo {} > x.json`。
    那些文件留在 CWD 被目标机自动提交扫进仓库（2026-08-10 清理了三个）。
    """
    if p is None:
        return pd.DataFrame()
    suffix = p.suffix.lower()
    if suffix == '.csv':
        return pd.read_csv(p, dtype={'代码': str})
    if suffix in {'.xlsx', '.xls'}:
        return pd.read_excel(p, dtype={'代码': str})
    if suffix == '.json':
        return pd.DataFrame(json.loads(p.read_text(encoding='utf-8')))
    raise ValueError('仅支持 csv/xlsx/json')


def select_new_rows(incoming, existing_fingerprints, *, force: bool = False):
    """Pick the rows of ``incoming`` not yet represented in the ledger.

    Multiset semantics: the k-th occurrence of a fingerprint in ``incoming`` is
    new only when the ledger holds fewer than k rows with that fingerprint.
    Re-importing an unchanged export therefore selects nothing (idempotent),
    while a genuine split order (identical fills in the same second) is still
    appended because its second occurrence exceeds the ledger's count.

    ``force`` bypasses the check entirely — a manual repair escape hatch that
    *will* double-count if used on an already-imported file.
    """
    if force:
        return incoming.copy()
    remaining = defaultdict(int)
    for fp in existing_fingerprints:
        remaining[fp] += 1
    keep = []
    for idx, fp in zip(incoming.index, incoming['_fingerprint']):
        if remaining.get(fp, 0) > 0:
            remaining[fp] -= 1          # this incoming row is already on file
            continue
        keep.append(idx)
    return incoming.loc[keep].copy()


def compute_positions(new, current_rows):
    """Apply buy/sell rows onto a position snapshot **in memory**.

    Pure with respect to the filesystem so the caller can validate (oversell
    raises here) before any file is touched. Returns the new snapshot list.
    """
    by = {clean_code(x.get('代码')): dict(x) for x in current_rows}
    for _, t in new.iterrows():
        if t['交易类别'] not in TRADE_CATEGORIES:
            continue
        code = clean_code(t['代码'])
        qty = finite(t['成交数量'])
        price = finite(t['成交价格'])
        fee = finite(t['费用'])
        pos = by.get(code)
        if t['交易类别'] == '买入':
            if pos is None:
                pos = {'代码': code, '名称': t['名称'], '持有数量': 0.0, '单位成本': 0.0}
                by[code] = pos
            old_qty = finite(pos.get('持有数量'))
            old_cost = finite(pos.get('单位成本'))
            new_qty = old_qty + qty
            pos['持有数量'] = new_qty
            pos['单位成本'] = ((old_qty * old_cost) + (qty * price) + fee) / new_qty if new_qty else 0
            pos['名称'] = t['名称'] or pos.get('名称')
        else:
            if pos is None or finite(pos.get('持有数量')) < qty:
                raise ValueError(f'{code}卖出数量超过台账持仓')
            pos['持有数量'] = finite(pos.get('持有数量')) - qty
            if pos['持有数量'] <= 0:
                del by[code]
    for pos in by.values():
        pos['snapshot_status'] = SNAPSHOT_PENDING
        pos['snapshot_note'] = SNAPSHOT_NOTE
    return list(by.values())


def _read_positions():
    return json.loads(POS.read_text(encoding='utf-8')) if POS.exists() else []


def apply_positions(new):
    """Read → compute → write the position snapshot (kept for direct callers)."""
    rows = compute_positions(new, _read_positions())
    _write_atomic(POS, json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    return rows


def _write_atomic(path: Path, text: str) -> None:
    """Write via temp file + os.replace so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


def _stage(path: Path, writer) -> Path:
    """Write content to ``path``'s temp sibling and return the temp path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    writer(tmp)
    return tmp


def _commit(staged: list[tuple[Path, Path]]) -> None:
    """Promote staged temp files to their destinations.

    All content is already on disk at this point, so the remaining work is a
    sequence of renames — the narrowest crash window we can achieve without a
    real transaction log. Ledger goes first: it is the deduplication oracle, so
    a crash between the renames leaves a recorded fill whose position update is
    missing (detectable, repairable) rather than an unrecorded fill that would
    be applied twice.
    """
    for tmp, dest in staged:
        os.replace(tmp, dest)


def _write_confirmation(day: str) -> None:
    confirmations = json.loads(CONFIRM.read_text(encoding='utf-8')) if CONFIRM.exists() else {}
    confirmations[day] = {
        'confirmed_at': cn_now().isoformat(timespec='seconds'),
        'no_trades': True,
        'note': f'用户确认：{day} 今日无交易动作',
    }
    _write_atomic(CONFIRM, json.dumps(confirmations, ensure_ascii=False, indent=2))


def _load_existing():
    if LEDGER.exists():
        existing = pd.read_csv(LEDGER, dtype={'代码': str})
    else:
        existing = pd.DataFrame(columns=FIELDS + ['_fingerprint', 'transaction_id'])
    if '_fingerprint' not in existing.columns:
        existing = existing.assign(_fingerprint=norm(existing).apply(fingerprint, axis=1)
                                   if len(existing) else [])
    return existing


def _assign_transaction_ids(existing, new):
    counts = existing.groupby('_fingerprint').size().to_dict() if len(existing) else {}
    ids = []
    for fp in new['_fingerprint']:
        counts[fp] = counts.get(fp, 0) + 1
        ids.append(f'{fp}-{counts[fp]:03d}')
    return ids


def main(argv=None):
    ap = argparse.ArgumentParser()
    # ⚠️ `--confirm-no-trades` 模式下 `--input` 不再必需。
    #    此前 required=True，而该模式又**要求输入为空**（下面 `and len(incoming)` 会抛），
    #    于是操作者必须造一个只含 `{}` 的空文件纯粹为了满足参数 ——
    #    那些文件留在 CWD 里被目标机的自动提交扫进仓库
    #    （`src/core/trades/_no_trades_2026080{5,6,7}.json`，2026-08-10 清理）。
    #    **是 CLI 设计逼出来的垃圾，不是操作者不小心。**
    ap.add_argument('--input')
    ap.add_argument('--confirm-no-trades', action='store_true')
    ap.add_argument('--date')
    ap.add_argument('--allow-identical', action='store_true',
                    help='跳过幂等去重强制全量追加（危险：对已导入文件会重复计账，仅用于人工修数）')
    a = ap.parse_args(argv)
    if not a.input:
        # 仅 `--confirm-no-trades` 允许省略 —— 见 `--input` 的注释。
        if not a.confirm_no_trades:
            raise SystemExit('--input 必需（除 --confirm-no-trades 外）')
        src = None
    else:
        src = Path(a.input)

    incoming = norm(read_input(src))
    incoming['_fingerprint'] = incoming.apply(fingerprint, axis=1) if len(incoming) else []
    existing = _load_existing()
    known = list(existing['_fingerprint']) if len(existing) else []

    if a.confirm_no_trades and len(incoming):
        raise ValueError('--confirm-no-trades 与非空输入冲突')

    new = select_new_rows(incoming, known, force=a.allow_identical)
    new['transaction_id'] = _assign_transaction_ids(existing, new)
    skipped = len(incoming) - len(new)

    if a.confirm_no_trades:
        if not a.date:
            raise ValueError('--confirm-no-trades 必须同时提供 --date')
        _write_confirmation(a.date)

    if len(new):
        # Compute positions first: an oversell must abort with both files intact.
        positions = compute_positions(new, _read_positions())
        merged = pd.concat([existing, new], ignore_index=True)
        merged = merged.sort_values(['成交日期', '成交时间', 'transaction_id'])
        stock = merged[merged['交易类别'].isin(TRADE_CATEGORIES)].copy()

        staged = [
            (_stage(LEDGER, lambda p: merged.to_csv(p, index=False, encoding='utf-8-sig')), LEDGER),
            (_stage(STOCK_JSON, lambda p: stock.to_json(p, orient='records', force_ascii=False,
                                                        indent=2)), STOCK_JSON),
            (_stage(POS, lambda p: p.write_text(json.dumps(positions, ensure_ascii=False,
                                                           indent=2, default=str),
                                                encoding='utf-8')), POS),
        ]
        _commit(staged)

    audit = {
        'appended_at': cn_now().isoformat(timespec='seconds'),
        # ⚠️ 不写 `str(src)` —— src 为 None 时会落成字符串 `"None"`，
        #    审计记录里出现一个看着像路径的假值（同形状的 `str(None)` 幽灵键
        #    2026-08-07 已在 `risk_map` 修过一次）。
        'source': str(src) if src is not None else '(无输入文件：--confirm-no-trades)',
        'requested_date': a.date,
        'incoming_rows': len(incoming),
        'appended_rows': len(new),
        'duplicate_rows_skipped': skipped,
        'allow_identical': a.allow_identical,
        'no_trades_confirmed': bool(a.confirm_no_trades),
        'transaction_ids': new['transaction_id'].tolist(),
    }
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open('a', encoding='utf-8') as f:
        f.write(json.dumps(audit, ensure_ascii=False) + '\n')
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return audit


if __name__ == '__main__':
    main()
