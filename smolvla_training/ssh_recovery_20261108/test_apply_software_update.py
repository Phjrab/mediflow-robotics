import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import apply_software_update as update


class Tests(unittest.TestCase):
    def fixture(self, root):
        target = root / "operator_candidate"
        target.mkdir()
        stop = target / "FIELD_STOP.json"
        stop.write_bytes(b'{"original":"HOLD"}\n')
        rows = []
        archive = root / "source.zip"
        with zipfile.ZipFile(archive, "x") as bundle:
            for index, name in enumerate(sorted(update.FILES)):
                before = b'VALUE = "before"\n' if index < 4 else None
                after = b'VALUE = "after"\n'
                if before is not None:
                    (target / name).write_bytes(before)
                row = dict(path="operator_candidate/" + name, before_sha256=update.digest(before) if before else None,
                           after_sha256=update.digest(after))
                rows.append(row)
                bundle.writestr(row["path"], after)
            bundle.writestr("deployment_manifest.json", json.dumps(dict(files=rows)))
        return archive, update.digest(stop.read_bytes()), update.digest(archive.read_bytes())

    def test_check_only_is_unchanged(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive, stop_sha, zip_sha = self.fixture(root)
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with patch.object(update, "STOP_SHA", stop_sha), patch.object(update, "ARCHIVE_SHA", zip_sha):
                self.assertEqual(update.apply(root, archive)["writes"], 0)
            self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_wrong_baseline_refuses_before_any_backup_or_write(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive, stop_sha, zip_sha = self.fixture(root)
            (root / "operator_candidate/first_trial.py").write_bytes(b"changed\n")
            with patch.object(update, "STOP_SHA", stop_sha), patch.object(update, "ARCHIVE_SHA", zip_sha), self.assertRaises(RuntimeError):
                update.apply(root, archive, True)
            self.assertFalse((root / "software_backups_20261108").exists())

    def test_stop_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive, stop_sha, zip_sha = self.fixture(root)
            with patch.object(update, "STOP_SHA", "wrong"), patch.object(update, "ARCHIVE_SHA", zip_sha), self.assertRaises(RuntimeError):
                update.apply(root, archive, True)
            self.assertFalse((root / "software_backups_20261108").exists())

    def test_execute_backs_up_and_preserves_stop(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive, stop_sha, zip_sha = self.fixture(root)
            with patch.object(update, "STOP_SHA", stop_sha), patch.object(update, "ARCHIVE_SHA", zip_sha):
                result = update.apply(root, archive, True)
            backup = root / result["backup_relative"]
            self.assertEqual(len(list(backup.glob("*.py"))), 4)
            self.assertEqual(result["files_written"], 8)
            self.assertEqual(update.digest((root / "operator_candidate/FIELD_STOP.json").read_bytes()), stop_sha)
            for p in backup.glob("*.py"):
                self.assertEqual(p.read_bytes(), b'VALUE = "before"\n')


if __name__ == "__main__":
    unittest.main()
