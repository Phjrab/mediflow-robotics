"""All evaluation observations below are SYNTHETIC; no robot success evidence."""
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import vla_eval_report as ev


def row(**changes):
    result = dict(zip(ev.FIELDS, ('2026-10-29T09:00:00+09:00', 'a'*64, 'A:red', '1',
                                 '1', '1', '1', '1', '', 'SYNTHETIC fixture', 'synthetic-reviewer')))
    result.update(changes)
    return result


def csv_bytes(*rows):
    text = io.StringIO(newline='')
    writer = csv.writer(text, lineterminator='\n')
    writer.writerow(ev.FIELDS)
    for item in rows:
        writer.writerow([item[k] for k in ev.FIELDS])
    return text.getvalue().encode()


class SchemaTests(unittest.TestCase):
    def test_committed_template_header_only(self):
        self.assertEqual((ROOT / 'data/vla_eval.csv').read_bytes(), ev.HEADER)

    def test_empty_header(self): self.assertEqual(ev.parse(ev.HEADER), [])
    def test_success(self): self.assertEqual(ev.parse(csv_bytes(row())), [row()])
    def test_date_only(self): ev.validate_row(row(date='2026-10-29'))
    def test_timezone(self): ev.validate_row(row(date='2026-10-29T00:00:00Z'))
    def test_invalid_dates(self):
        for value in ('2026-02-30', '20261029', '2026-10-29T00:00:00', ''):
            with self.subTest(value=value), self.assertRaises(ev.LedgerError): ev.validate_row(row(date=value))
    def test_invalid_hashes(self):
        for value in ('a'*63, 'G'*64, 'A'*64, '', 'file.safetensors'):
            with self.subTest(value=value), self.assertRaises(ev.LedgerError): ev.validate_row(row(checkpointhash=value))
    def test_invalid_combo(self):
        with self.assertRaises(ev.LedgerError): ev.validate_row(row(combo='D:red'))
    def test_invalid_trials(self):
        for value in ('0', '6', '01', '1.0', 'N/A'):
            with self.subTest(value=value), self.assertRaises(ev.LedgerError): ev.validate_row(row(trial=value))
    def test_invalid_bits(self):
        for value in ('true', 'yes', '', 'N/A', '2', True, 1):
            with self.subTest(value=value), self.assertRaises(ev.LedgerError): ev.validate_row(row(loaded=value))
    def test_stage_contradictions(self):
        for stage in ('loaded', 'moved', 'grasped'):
            with self.subTest(stage=stage), self.assertRaises(ev.LedgerError): ev.validate_row(row(**{stage: '0'}))
    def test_success_failure_contradiction(self):
        with self.assertRaises(ev.LedgerError): ev.validate_row(row(failure_type='drop'))
    def test_failure_required(self):
        with self.assertRaises(ev.LedgerError): ev.validate_row(row(correct_bin_placed='0'))
    def test_failure_enum(self):
        with self.assertRaises(ev.LedgerError): ev.validate_row(row(correct_bin_placed='0', failure_type='unknown'))
    def test_all_failure_categories(self):
        for failure in ev.FAILURES:
            r = row(correct_bin_placed='0', failure_type=failure)
            if failure == 'grasp_failure': r['grasped'] = '0'
            ev.validate_row(r)
    def test_failure_stage_contradictions(self):
        for failure in ('drop', 'wrong_bin'):
            with self.subTest(failure=failure), self.assertRaises(ev.LedgerError):
                ev.validate_row(row(grasped='0', correct_bin_placed='0', failure_type=failure))
        with self.assertRaises(ev.LedgerError): ev.validate_row(row(correct_bin_placed='0', failure_type='grasp_failure'))
        with self.assertRaises(ev.LedgerError):
            ev.validate_row(row(moved='0', grasped='0', correct_bin_placed='0', failure_type='wrong_bottle'))
    def test_load_failure_is_recordable(self):
        ev.validate_row(row(loaded='0', moved='0', grasped='0', correct_bin_placed='0', failure_type='other'))
    def test_reviewer_required(self):
        for value in ('', '  ', 'one\ntwo'):
            with self.subTest(value=value), self.assertRaises(ev.LedgerError): ev.validate_row(row(reviewer=value))
    def test_formula_and_controls(self):
        for value in ('=1+1', ' +cmd', '-cmd', '@foo', '\tcmd', 'ab\x00cd'):
            with self.subTest(value=value), self.assertRaises(ev.LedgerError): ev.validate_row(row(notes=value))
    def test_long_notes(self):
        with self.assertRaises(ev.LedgerError): ev.validate_row(row(notes='x'*4001))
    def test_quoted_unicode_newlines_preserved(self):
        r = row(notes='SYNTHETIC: "약통",\n두 번째 줄')
        self.assertEqual(ev.parse(csv_bytes(r)), [r])
    def test_wrong_header(self):
        with self.assertRaises(ev.LedgerError): ev.parse(ev.HEADER.replace(b'failure_type', b'failuretype'))
    def test_extra_fields(self):
        with self.assertRaises(ev.LedgerError): ev.validate_row(dict(row(), extra='x'))
    def test_missing_field(self):
        r = row(); del r['loaded']
        with self.assertRaises(ev.LedgerError): ev.validate_row(r)
    def test_invalid_csv(self):
        for raw in (b'', ev.HEADER.rstrip(), ev.HEADER+b'\n', ev.HEADER+b'\xff\n', ev.HEADER+b'"broken\n'):
            with self.subTest(raw=raw), self.assertRaises(ev.LedgerError): ev.parse(raw)
    def test_correction_date_order(self):
        with self.assertRaises(ev.LedgerError): ev.parse(csv_bytes(row(), row(date='2026-10-28')))
    def test_correction_notes_required(self):
        with self.assertRaises(ev.LedgerError): ev.parse(csv_bytes(row(), row(notes='')))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'synthetic.csv'
        self.path.write_bytes(ev.HEADER)
    def append(self, r=None, **kwargs):
        return ev.append_record(self.path, r or row(), expected_sha256=ev.digest(self.path.read_bytes()), **kwargs)
    def test_append_preserves_bytes_and_mode(self):
        self.path.chmod(0o640)
        original = self.path.read_bytes(); result = self.append()
        self.assertTrue(self.path.read_bytes().startswith(original))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(result['sha256'], ev.digest(self.path.read_bytes()))
    def test_correction_keeps_history(self):
        self.append(); original = self.path.read_bytes()
        self.append(row(correct_bin_placed='0', failure_type='drop', notes='SYNTHETIC correction'), correction=True)
        self.assertTrue(self.path.read_bytes().startswith(original))
        self.assertEqual(len(ev.parse(self.path.read_bytes())), 2)
    def test_duplicate_requires_explicit_correction(self):
        self.append(); original = self.path.read_bytes()
        with self.assertRaises(ev.LedgerError): self.append()
        self.assertEqual(self.path.read_bytes(), original)
    def test_new_trial_rejects_correction(self):
        with self.assertRaises(ev.LedgerError): self.append(correction=True)
        self.assertEqual(self.path.read_bytes(), ev.HEADER)
    def test_stale_sha(self):
        with self.assertRaises(ev.LedgerError): ev.append_record(self.path, row(), expected_sha256='0'*64)
        self.assertEqual(self.path.read_bytes(), ev.HEADER)
    def test_invalid_row_unchanged(self):
        with self.assertRaises(ev.LedgerError): self.append(row(loaded='0'))
        self.assertEqual(self.path.read_bytes(), ev.HEADER)
    def test_corrupt_source_unchanged(self):
        self.path.write_bytes(b'broken')
        with self.assertRaises(ev.LedgerError): self.append()
        self.assertEqual(self.path.read_bytes(), b'broken')
    def test_atomic_replace_failure(self):
        with patch.object(ev.os, 'replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError): self.append()
        self.assertEqual(self.path.read_bytes(), ev.HEADER)
        self.assertEqual(list(self.path.parent.glob('.synthetic.csv.*')), [])
    def test_fsync_before_replace_failure(self):
        with patch.object(ev.os, 'fsync', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError): self.append()
        self.assertEqual(self.path.read_bytes(), ev.HEADER)
    def test_directory_sync_ambiguous_commit_reported(self):
        with patch.object(ev.os, 'fsync', side_effect=[None, OSError('synthetic dir failure')]):
            with self.assertRaisesRegex(ev.LedgerError, 'row replaced'): self.append()
        self.assertEqual(len(ev.parse(self.path.read_bytes())), 1)
    def test_concurrent_same_revision_one_winner(self):
        token = ev.digest(ev.HEADER); results = []; barrier = threading.Barrier(2)
        def worker(trial):
            barrier.wait()
            try:
                ev.append_record(self.path, row(trial=trial), expected_sha256=token); results.append('ok')
            except ev.LedgerError: results.append('stale')
        threads = [threading.Thread(target=worker, args=(str(i),)) for i in (1, 2)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)
        self.assertEqual(sorted(results), ['ok', 'stale'])
        self.assertEqual(len(ev.parse(self.path.read_bytes())), 1)
    def test_symlink_ledger_rejected(self):
        link = self.path.parent / 'link.csv'; link.symlink_to(self.path)
        with self.assertRaises(OSError): ev.append_record(link, row(), expected_sha256=ev.digest(ev.HEADER))
        self.assertEqual(self.path.read_bytes(), ev.HEADER)
    def test_symlink_lock_rejected(self):
        self.path.with_name(self.path.name+'.lock').symlink_to(self.path)
        with self.assertRaises(OSError): self.append()
        self.assertEqual(self.path.read_bytes(), ev.HEADER)
    def test_missing_file_not_created(self):
        absent = self.path.parent / 'absent.csv'
        with self.assertRaises(FileNotFoundError): ev.append_record(absent, row(), expected_sha256=ev.digest(ev.HEADER))
        self.assertFalse(absent.exists())


class ReportTests(unittest.TestCase):
    def report(self, *rows): return ev.summarize(csv_bytes(*rows), synthetic=True)
    def test_empty_is_na_not_zero(self):
        r = self.report(); self.assertEqual(r['checkpoints'], {}); self.assertIsNone(r['overall_success_rate'])
        self.assertEqual(ev.markdown(r).count('| N/A | N/A | N/A | N/A | N/A |'), 9)
    def test_requested_empty_checkpoint_matrix(self):
        r = ev.summarize(ev.HEADER, checkpoints=['b'*64], synthetic=True)['checkpoints']['b'*64]
        self.assertEqual(r['missing'], 45); self.assertIsNone(r['success_rate'])
        self.assertEqual(len(r['combos']), 9)
        self.assertTrue(all(all(t is None for t in c['trials'].values()) for c in r['combos'].values()))
    def test_invalid_requested_hash(self):
        with self.assertRaises(ev.LedgerError): ev.summarize(ev.HEADER, checkpoints=['invalid'])
    def test_stage_counts_separate_from_success(self):
        r = self.report(row(), row(trial='2', correct_bin_placed='0', failure_type='drop'))['checkpoints']['a'*64]
        self.assertEqual(r['stage_counts'], dict(loaded=2, moved=2, grasped=2, correct_bin_placed=1))
        self.assertEqual(r['success_rate'], .5); self.assertEqual(r['missing'], 43)
    def test_last_correction_only_counted(self):
        r = self.report(row(), row(correct_bin_placed='0', failure_type='drop'))
        self.assertEqual((r['history_rows'], r['unique_trials'], r['superseded_rows']), (2, 1, 1))
        g = r['checkpoints']['a'*64]; self.assertEqual(g['success_rate'], 0)
        self.assertEqual(g['combos']['A:red']['trials']['1']['history_row'], 2)
        self.assertEqual(g['combos']['A:red']['failures']['drop'], 1)
    def test_checkpoints_separate(self):
        r = self.report(row(), row(checkpointhash='b'*64, correct_bin_placed='0', failure_type='drop'))
        self.assertEqual(r['checkpoints']['a'*64]['success_rate'], 1)
        self.assertEqual(r['checkpoints']['b'*64]['success_rate'], 0)
        self.assertIsNone(r['overall_success_rate'])
    def test_all_45_trials(self):
        r = self.report(*(row(combo=c, trial=str(t)) for c in ev.COMBOS for t in range(1, 6)))
        g = r['checkpoints']['a'*64]
        self.assertEqual(g['recorded'], 45); self.assertEqual(g['missing'], 0); self.assertEqual(g['success_rate'], 1)
    def test_load_failure_in_denominator(self):
        r = self.report(row(), row(trial='2', loaded='0', moved='0', grasped='0', correct_bin_placed='0', failure_type='other'))
        self.assertEqual(r['checkpoints']['a'*64]['stage_rates']['loaded'], .5)
        self.assertEqual(r['checkpoints']['a'*64]['success_rate'], .5)
    def test_empty_vs_zero_combo(self):
        g = self.report(row(correct_bin_placed='0', failure_type='drop'))['checkpoints']['a'*64]['combos']
        self.assertEqual(g['A:red']['success_rate'], 0); self.assertIsNone(g['A:blue']['success_rate'])
    def test_labels_and_no_personal_notes_in_render(self):
        r = self.report(row(notes='SYNTHETIC <script>example</script>'))
        rendered = ev.markdown(r)
        self.assertIn('SYNTHETIC_NOT_ROBOT_RESULTS', rendered); self.assertNotIn('<script>', rendered)
    def test_source_is_unchanged(self):
        raw = csv_bytes(row()); copy = bytes(raw); ev.summarize(raw); self.assertEqual(raw, copy)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.csv = self.root / 'synthetic.csv'; self.csv.write_bytes(ev.HEADER)
    def cli(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream): result = ev.main(['--csv', str(self.csv), *map(str, args)])
        return result, json.loads(stream.getvalue())
    def test_validate_no_mutation(self):
        code, result = self.cli('validate'); self.assertEqual(code, 0); self.assertEqual(result['history_rows'], 0)
        self.assertEqual(self.csv.read_bytes(), ev.HEADER)
    def test_new_report_and_no_overwrite(self):
        out = self.root / 'report'
        code, _ = self.cli('report', '--out', out, '--synthetic'); self.assertEqual(code, 0)
        original = (out/'report.json').read_bytes()
        code, _ = self.cli('report', '--out', out); self.assertEqual(code, 2)
        self.assertEqual((out/'report.json').read_bytes(), original)
        self.assertEqual(self.csv.read_bytes(), ev.HEADER)
    def test_append_cli(self):
        payload = self.root/'row.json'; payload.write_text(json.dumps(row()))
        code, result = self.cli('append', '--row-json', payload, '--expected-sha256', ev.digest(ev.HEADER))
        self.assertEqual(code, 0); self.assertEqual(result['history_rows'], 1)
    def test_invalid_input_no_output_directory(self):
        self.csv.write_bytes(b'broken'); out = self.root/'bad'
        code, _ = self.cli('report', '--out', out)
        self.assertEqual(code, 2); self.assertFalse(out.exists())
    def test_report_source_collision_rejected(self):
        code, _ = self.cli('report', '--out', self.csv)
        self.assertEqual(code, 2); self.assertEqual(self.csv.read_bytes(), ev.HEADER)


if __name__ == '__main__': unittest.main()
