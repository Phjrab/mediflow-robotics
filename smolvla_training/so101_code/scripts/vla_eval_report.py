"""Offline evaluation ledger. No robot, policy, GPU, image or network imports.

One CSV is one 9-combination x 5-trial evaluation round per checkpoint.
Corrections append history; only the last row for (checkpoint, combo, trial)
is counted. Use a new CSV for a new round, never recycle these trial IDs.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile

FIELDS = ('date', 'checkpointhash', 'combo', 'trial', 'loaded', 'moved',
          'grasped', 'correct_bin_placed', 'failure_type', 'notes', 'reviewer')
STAGES = ('loaded', 'moved', 'grasped', 'correct_bin_placed')
COMBOS = tuple(f'{b}:{c}' for b in 'ABC' for c in ('red', 'blue', 'green'))
FAILURES = ('wrong_bottle', 'wrong_bin', 'grasp_failure', 'drop', 'safety_stop', 'other')
FAILURE_KO = dict(zip(FAILURES, ('잘못된 약통', '잘못된 바구니', '파지 실패', '낙하', '안전 정지', '기타')))
HEADER = (','.join(FIELDS) + '\n').encode('utf-8')


class LedgerError(ValueError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def checkpoint_sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise LedgerError('checkpointhash must be a lowercase 64-character SHA256')
    return value


def record_time(value):
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            # A date-only observation is ordered at midnight in the documented KST zone.
            return datetime.fromisoformat(value + 'T00:00:00+09:00')
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or not re.match(r'^\d{4}-\d{2}-\d{2}T', value):
            raise ValueError()
        return stamp
    except (TypeError, ValueError):
        raise LedgerError('date must be YYYY-MM-DD (KST) or ISO timestamp with timezone') from None


def validate_row(row):
    if not isinstance(row, dict) or set(row) != set(FIELDS):
        raise LedgerError('row fields must match the CSV schema exactly')
    if any(not isinstance(v, str) for v in row.values()):
        raise LedgerError('CSV values must be strings; boolean fields use 0 or 1')
    record_time(row['date'])
    checkpoint_sha(row['checkpointhash'])
    if row['combo'] not in COMBOS or row['trial'] not in ('1', '2', '3', '4', '5'):
        raise LedgerError('combo must be one of the nine registered combinations; trial is 1..5')
    if any(row[s] not in ('0', '1') for s in STAGES):
        raise LedgerError('all stage values must be exactly 0 or 1; no unknown trial rows')
    bits = [int(row[s]) for s in STAGES]
    if any(b > a for a, b in zip(bits, bits[1:])):
        raise LedgerError('contradictory stages: placed requires grasped, moved and loaded')
    failure = row['failure_type']
    if bits[-1]:
        if failure:
            raise LedgerError('successful placement cannot also have a failure_type')
    elif failure not in FAILURES:
        raise LedgerError('an unsuccessful completed trial requires a known failure_type')
    if failure in ('wrong_bin', 'drop') and not bits[2]:
        raise LedgerError('wrong_bin/drop requires a recorded grasp')
    if failure == 'wrong_bottle' and not bits[1]:
        raise LedgerError('wrong_bottle requires movement toward the wrong bottle')
    if failure == 'grasp_failure' and (not bits[1] or bits[2]):
        raise LedgerError('grasp_failure requires movement and no successful grasp')
    for name, limit in (('notes', 4000), ('reviewer', 128)):
        text = row[name]
        if len(text) > limit or any(ord(c) < 32 and c != '\n' for c in text):
            raise LedgerError(f'{name}: invalid control characters or excessive length')
        if text.lstrip().startswith(('=', '+', '-', '@')):
            raise LedgerError(f'{name}: formula-like prefix is not allowed')
    if not row['reviewer'].strip() or '\n' in row['reviewer']:
        raise LedgerError('reviewer must be a nonempty single-line identifier')
    return dict(row)


def key(row):
    return (row['checkpointhash'], row['combo'], row['trial'])


def parse(raw):
    if not raw or not raw.endswith(b'\n'):
        raise LedgerError('missing header or final newline; refuse possibly incomplete data')
    try:
        reader = csv.reader(io.StringIO(raw.decode('utf-8'), newline=''), strict=True)
        if tuple(next(reader)) != FIELDS:
            raise LedgerError('CSV header/order does not match the evaluation schema')
        rows, latest = [], {}
        for number, values in enumerate(reader, 2):
            if len(values) != len(FIELDS):
                raise LedgerError(f'CSV record {number}: incorrect field count')
            row = validate_row(dict(zip(FIELDS, values)))
            old = latest.get(key(row))
            if old and (record_time(row['date']) < record_time(old['date']) or not row['notes'].strip()):
                raise LedgerError('correction needs notes and a nondecreasing date for that trial')
            rows.append(row)
            latest[key(row)] = row
        return rows
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise LedgerError(f'invalid CSV encoding/structure: {type(exc).__name__}') from None


def local_path(path):
    # Canonicalize the parent to share one lock across parent path aliases.
    path = Path(path)
    return path.parent.resolve(strict=True) / path.name


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise LedgerError('ledger must be a regular file')
        return stream.read()


def append_record(path, row, *, expected_sha256, correction=False):
    """Logical append via fsynced same-directory replacement under a stable lock.

    Cooperative writers must use this API; manual edits do not honor flock.
    Existing bytes are the exact prefix of the replacement. The SHA is an
    optimistic concurrency token, not authentication or tamper-proof storage.
    """
    path = local_path(path)
    row = validate_row(row)
    checkpoint_sha(expected_sha256)
    lock = path.with_name(path.name + '.lock')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise LedgerError('lock must be a regular file')
        fcntl.flock(stream, fcntl.LOCK_EX)
        original = read_regular(path)
        if digest(original) != expected_sha256:
            raise LedgerError('stale SHA: reread ledger and review before retrying')
        rows = parse(original)
        exists = any(key(old) == key(row) for old in rows)
        if exists != correction:
            raise LedgerError('existing trial requires --correction; a new trial must not use it')
        output = io.StringIO(newline='')
        csv.writer(output, lineterminator='\n').writerow([row[f] for f in FIELDS])
        updated = original + output.getvalue().encode('utf-8')
        parse(updated)
        temp_name = None
        try:
            temp_fd, temp_name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
            with os.fdopen(temp_fd, 'wb') as tmp:
                os.fchmod(tmp.fileno(), stat.S_IMODE(path.stat().st_mode))
                tmp.write(updated)
                tmp.flush()
                os.fsync(tmp.fileno())
            # Detect non-cooperating changes seen before commit (not a general CAS).
            if read_regular(path) != original:
                raise LedgerError('ledger changed outside the writer lock; refusing replacement')
            os.replace(temp_name, path)
            temp_name = None
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            except OSError as exc:
                raise LedgerError('row replaced but directory sync failed; inspect SHA before retry') from exc
            finally:
                os.close(directory_fd)
        finally:
            if temp_name is not None:
                os.unlink(temp_name)
    return {'sha256': digest(updated), 'history_rows': len(rows) + 1, 'correction': correction}


def summarize(raw, *, checkpoints=(), synthetic=False):
    rows = parse(raw)
    latest = {}
    for index, row in enumerate(rows, 1):
        latest[key(row)] = (index, row)
    hashes = sorted({checkpoint_sha(h) for h in checkpoints} | {r['checkpointhash'] for r in rows})
    groups = {}
    for checkpoint in hashes:
        combos = {}
        for combo in COMBOS:
            trials, counts, failures = {}, dict.fromkeys(STAGES, 0), dict.fromkeys(FAILURES, 0)
            recorded = 0
            for trial in range(1, 6):
                entry = latest.get((checkpoint, combo, str(trial)))
                if entry is None:
                    trials[str(trial)] = None
                    continue
                index, row = entry
                recorded += 1
                flags = {s: row[s] == '1' for s in STAGES}
                for s in STAGES:
                    counts[s] += flags[s]
                if row['failure_type']:
                    failures[row['failure_type']] += 1
                trials[str(trial)] = dict(flags, failure_type=row['failure_type'], history_row=index)
            combos[combo] = {'trials': trials, 'recorded': recorded, 'planned': 5,
                             'missing': 5 - recorded, 'stage_counts': counts,
                             'stage_rates': {s: counts[s] / recorded if recorded else None for s in STAGES},
                             'success_rate': counts['correct_bin_placed'] / recorded if recorded else None,
                             'failures': failures}
        total = sum(c['recorded'] for c in combos.values())
        counts = {s: sum(c['stage_counts'][s] for c in combos.values()) for s in STAGES}
        groups[checkpoint] = {'combos': combos, 'recorded': total, 'planned': 45, 'missing': 45-total,
                              'stage_counts': counts,
                              'stage_rates': {s: counts[s] / total if total else None for s in STAGES},
                              'success_rate': counts['correct_bin_placed'] / total if total else None}
    return {'schema_version': 1, 'evidence': 'SYNTHETIC_NOT_ROBOT_RESULTS' if synthetic else 'HUMAN_LEDGER_NOT_INDEPENDENTLY_VERIFIED',
            'source_sha256': digest(raw), 'history_rows': len(rows), 'unique_trials': len(latest),
            'superseded_rows': len(rows)-len(latest), 'checkpoints': groups,
            'counting_rule': 'last appended row per checkpoint/combo/trial; one round per CSV',
            'denominator': 'recorded unique completed trials, including load failures; missing excluded',
            'success_definition': 'correct bottle released into specified bin; correct_bin_placed only',
            'overall_success_rate': None, 'overall_note': 'No pooling across checkpoints',
            'hardware_access': 0}


def markdown(report):
    lines = ['# VLA 평가 기록 집계', '', f"근거: {report['evidence']}", '',
             f"원본 SHA256: `{report['source_sha256']}`", '',
             f"이력 {report['history_rows']}행 / 고유 시험 {report['unique_trials']}건 / 이전 수정행 {report['superseded_rows']}행.", '',
             '성공은 올바른 약통을 지정 바구니에 놓기까지 완료한 경우만입니다. ',
             '분모는 기록된 고유 시험 수이며, 로드 실패는 포함하고 미기록은 제외합니다.',
             '단계별 수치는 같은 분모를 사용합니다. N/A는 미실시이며 실패 0과 다릅니다.',
             '체크포인트 간 결과를 합치지 않습니다. 같은 trial 번호의 수정 이력은 마지막 행만 집계합니다.', '']
    if not report['checkpoints']:
        lines += ['체크포인트와 실제 평가 기록 없음 — 성공률 N/A.', '',
                  '| 조합 | 1 | 2 | 3 | 4 | 5 |', '|---|---|---|---|---|---|']
        lines += [f'| {c} | N/A | N/A | N/A | N/A | N/A |' for c in COMBOS]
    for checkpoint, group in report['checkpoints'].items():
        lines += [f'## 체크포인트 `{checkpoint}`', '',
                  '| 조합 | 1 | 2 | 3 | 4 | 5 | 기록/계획 | 로드 | 움직임 | 파지 | 지정 투입 | 성공률 |',
                  '|---|---|---|---|---|---|---|---|---|---|---|---|']
        for combo, c in group['combos'].items():
            cells = []
            for t in c['trials'].values():
                cells.append('N/A' if t is None else ('성공' if t['correct_bin_placed'] else FAILURE_KO[t['failure_type']]))
            counts = [f"{c['stage_counts'][s]}/{c['recorded']}" if c['recorded'] else 'N/A' for s in STAGES]
            rate = 'N/A' if c['success_rate'] is None else f"{c['success_rate']:.1%}"
            lines.append('| ' + ' | '.join([combo, *cells, f"{c['recorded']}/5", *counts, rate]) + ' |')
        total_rate = 'N/A' if group['success_rate'] is None else f"{group['success_rate']:.1%}"
        lines += ['', f"기록 {group['recorded']}/45, 미기록 {group['missing']}, 성공률 {total_rate}.", '']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, default=Path(__file__).resolve().parents[1] / 'data/vla_eval.csv')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('validate')
    append = sub.add_parser('append')
    append.add_argument('--row-json', required=True, type=Path)
    append.add_argument('--expected-sha256', required=True)
    append.add_argument('--correction', action='store_true')
    report = sub.add_parser('report')
    report.add_argument('--out', required=True, type=Path, help='NEW directory only')
    report.add_argument('--checkpoint', action='append', default=[])
    report.add_argument('--synthetic', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'append':
            row = json.loads(args.row_json.read_text(encoding='utf-8'))
            result = append_record(args.csv, row, expected_sha256=args.expected_sha256, correction=args.correction)
        else:
            raw = read_regular(local_path(args.csv))
            if args.command == 'validate':
                result = {'history_rows': len(parse(raw)), 'sha256': digest(raw), 'status': 'VALID_SCHEMA_ONLY'}
            else:
                result = summarize(raw, checkpoints=args.checkpoint, synthetic=args.synthetic)
                args.out.mkdir(parents=True, exist_ok=False)
                # Newly reserved directory: never overwrite old reports or source.
                with (args.out / 'report.json').open('x', encoding='utf-8') as file:
                    json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False)
                    file.write('\n')
                with (args.out / 'report.md').open('x', encoding='utf-8') as file:
                    file.write(markdown(result))
                result = {'status': 'REPORT_WRITTEN', 'out': str(args.out), 'history_rows': result['history_rows']}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'ERROR', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
