"""검증된 소프트웨어 묶음만 고정 작업 폴더에 배포한다. 기본값은 확인만."""
import argparse
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import re
import tarfile
import time

INPUT_DIRECTORY=Path(__file__).resolve().parent
PROTECTED={'SERIAL_STOP.json','.env','credentials','model.safetensors'}
PROTECTED_DIRS={'raw','data','datasets','checkpoints','pretrained_model','.venv','venvs','.ssh'}


def validate_archive(blob,expected,root):
    if not re.fullmatch(r'[0-9a-f]{64}',expected) or hashlib.sha256(blob).hexdigest()!=expected:
        raise ValueError('배포 묶음 해시 불일치')
    if Path(root).is_symlink():raise ValueError('작업 루트 symlink 거부')
    root=Path(root).resolve();plan={}
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as archive:
        members=archive.getmembers();names=[m.name for m in members]
        if len(names)!=len(set(names)) or len(members)>256 or sum(m.size for m in members)>20_000_000:
            raise ValueError('중복/초과 묶음 거부')
        indexed={m.name:m for m in members};manifest_member=indexed.get('DEPLOY_MANIFEST.json')
        if manifest_member is None or not manifest_member.isfile() or manifest_member.size>1_000_000:
            raise ValueError('배포 manifest 오류')
        manifest=json.load(archive.extractfile(manifest_member))
        if set(indexed)!=(set(manifest)|{'DEPLOY_MANIFEST.json'}):raise ValueError('묶음 목록 불일치')
        for name,member in indexed.items():
            parts=PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or '\\' in name or not member.isfile() or member.size>5_000_000:
                raise ValueError('경로/링크/크기 거부')
            if parts.name in PROTECTED or set(parts.parts)&PROTECTED_DIRS:
                raise ValueError('중단 기록/원본/환경 경로 수정 거부')
            raw_path=Path(root)/name;path=raw_path.resolve()
            if root not in path.parents or raw_path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError('배포 대상 범위/형식 거부')
            data=archive.extractfile(member).read()
            if name in manifest and hashlib.sha256(data).hexdigest()!=manifest[name]:raise ValueError('파일 해시 불일치')
            plan[name]=data
    return plan


def apply_plan(plan,root):
    if Path(root).is_symlink():raise ValueError('작업 루트 symlink 거부')
    root=Path(root).resolve();stamp=datetime.now().strftime('%Y%m%d_%H%M%S');result=[]
    for name,data in plan.items():
        path=root/name;backup=None
        if root not in path.resolve().parents or path.is_symlink():raise ValueError('배포 대상 변경 감지')
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            original=path.read_bytes()
            if original==data:result.append(dict(file=name,status='UNCHANGED'));continue
            backup=path.with_name(path.name+'.bak_'+stamp+'_'+str(time.time_ns()))
            with backup.open('xb') as stream:stream.write(original)
        temp=path.with_name(path.name+'.tmp_'+str(time.time_ns()))
        with temp.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
        result.append(dict(file=name,status='DEPLOYED',backup=str(backup.relative_to(root)) if backup else None))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('archive');parser.add_argument('sha256');parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    if not re.fullmatch(r'bundle_software_[A-Za-z0-9_.-]+\.tar\.gz',args.archive):raise ValueError('묶음 파일명 거부')
    root=Path.home()/'nov_live_prep_20261104'
    plan=validate_archive((INPUT_DIRECTORY/args.archive).read_bytes(),args.sha256,root)
    if not args.execute:
        print(json.dumps(dict(status='DEPLOY_CHECK_ONLY',files=len(plan),writes=0)));return
    result=apply_plan(plan,root);reports=root/'reports';reports.mkdir(exist_ok=True)
    receipt=reports/('SOFTWARE_DEPLOY_'+str(time.time_ns())+'.json')
    with receipt.open('x',encoding='utf-8') as stream:json.dump(dict(archive_sha256=args.sha256,files=result),stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(status='SOFTWARE_DEPLOYED',changed=sum(x['status']=='DEPLOYED' for x in result),
                          backups=sum(bool(x.get('backup')) for x in result),receipt=str(receipt.relative_to(root))),ensure_ascii=False))


if __name__=='__main__':main()
