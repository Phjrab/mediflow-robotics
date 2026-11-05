import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


HERE=Path(__file__).resolve().parents[1]
INSTALLER=HERE/'install_software_bundle_20261105.py'
if not INSTALLER.is_file():INSTALLER=HERE.parent/'install_software_bundle_20261105.py'
spec=importlib.util.spec_from_file_location('_software_installer',INSTALLER)
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


def bundle(entries,symlink=False):
    payload={name:data for name,data in entries};manifest=json.dumps({name:hashlib.sha256(data).hexdigest() for name,data in payload.items()}).encode();stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w:gz') as archive:
        for name,data in [('DEPLOY_MANIFEST.json',manifest)]+entries:
            item=tarfile.TarInfo(name);item.size=len(data)
            if symlink and name!='DEPLOY_MANIFEST.json':item.type=tarfile.SYMTYPE;item.linkname='../outside';item.size=0;archive.addfile(item)
            else:archive.addfile(item,io.BytesIO(data))
    return stream.getvalue()


class Tests(unittest.TestCase):
    def test_bad_hash_protected_stop_and_traversal_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('SERIAL_STOP.json','../outside.py','raw/data.json','pretrained_model/config.json'):
                blob=bundle([(name,b'bad')])
                with self.subTest(name=name),self.assertRaises(ValueError):installer.validate_archive(blob,hashlib.sha256(blob).hexdigest(),Path(tmp))
            with self.assertRaises(ValueError):installer.validate_archive(bundle([('ok.py',b'ok')]),'0'*64,Path(tmp))

    def test_symlink_and_duplicate_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for blob in (bundle([('ok.py',b'')],True),bundle([('ok.py',b'1'),('ok.py',b'2')])):
                with self.assertRaises(ValueError):installer.validate_archive(blob,hashlib.sha256(blob).hexdigest(),Path(tmp))

    def test_backup_and_atomic_replace_preserve_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'app.py').write_bytes(b'old');(root/'SERIAL_STOP.json').write_bytes(b'latched')
            blob=bundle([('app.py',b'new')]);plan=installer.validate_archive(blob,hashlib.sha256(blob).hexdigest(),root)
            result=installer.apply_plan(plan,root)
            self.assertEqual((root/'app.py').read_bytes(),b'new');self.assertEqual((root/result[1]['backup']).read_bytes(),b'old')
            self.assertEqual((root/'SERIAL_STOP.json').read_bytes(),b'latched')

    def test_default_cli_check_only_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);blob=bundle([('software_test_fixture.py',b'new')]);name='bundle_software_test.tar.gz';(root/name).write_bytes(blob)
            with patch.object(installer,'INPUT_DIRECTORY',root),patch('sys.argv',['installer',name,hashlib.sha256(blob).hexdigest()]),patch.object(installer,'apply_plan') as apply,contextlib.redirect_stdout(io.StringIO()) as output:
                installer.main()
            apply.assert_not_called();self.assertIn('DEPLOY_CHECK_ONLY',output.getvalue())


if __name__=='__main__':unittest.main()
