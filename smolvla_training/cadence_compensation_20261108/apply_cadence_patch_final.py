"""코드 ZIP만 배포한다. 장치·추론·서비스·레지스터 접근 없음."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import zipfile

ARCHIVE_SHA = 'c1c886811db25408c774c8f6202449b5a302dc64bc39d6885dffbc17299933b3'
STOP_SHA = "e8a2bf9eb72c676198483aa8b4e150058a4595f37c7b35f6015216798deee56c"
FILES = {'profile_control_timing.py', 'run_control_timing_compensated.sh', 'test_compensated_timing.py', 'reviewed_start_stop.py'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check(root, archive):
    root = root.resolve(strict=True)
    stop = root / "operator_candidate/FIELD_STOP.json"
    if stop.is_symlink() or digest(stop.read_bytes()) != STOP_SHA:
        raise RuntimeError("기존 FIELD_STOP 해시 불일치; 적용 금지")
    if digest(archive.read_bytes()) != ARCHIVE_SHA:
        raise RuntimeError("ZIP 해시 불일치")
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("deployment_manifest.json"))
        rows = manifest["files"]
        expected = {"operator_candidate/" + name for name in FILES}
        if len(rows) != len(FILES) or {row["path"] for row in rows} != expected:
            raise RuntimeError("배포 파일 목록 불일치")
        if len(bundle.namelist()) != len(FILES) + 1 or set(bundle.namelist()) != expected | {"deployment_manifest.json"}:
            raise RuntimeError("예상 밖 ZIP 항목")
        prepared = []
        for row in rows:
            target = root / row["path"]
            if target.is_symlink() or target.parent.resolve(strict=True) != root / "operator_candidate":
                raise RuntimeError("심볼릭 링크/외부 경로 금지")
            payload = bundle.read(row["path"])
            if digest(payload) != row["after_sha256"]:
                raise RuntimeError("코드 해시 불일치")
            if target.suffix == ".py": ast.parse(payload.decode("utf-8"), feature_version=(3, 10))
            before = row["before_sha256"]
            if before is None:
                if target.exists():
                    raise RuntimeError("신규 파일이 이미 존재; 덮어쓰기 금지: " + target.name)
            elif not target.is_file() or digest(target.read_bytes()) != before:
                raise RuntimeError("기존 코드 기준 해시 불일치: " + target.name)
            prepared.append((row, target, payload))
    return root, stop, prepared


def apply(root, archive, execute=False):
    root, stop, prepared = check(root, archive)
    if not execute:
        return dict(status="SOFTWARE_PATCH_READY", files=len(prepared), writes=0, hardware_access=False)
    backup = root / "software_backups_20261108" / str(time.time_ns())
    backup.mkdir(parents=True, exist_ok=False)
    for row, target, payload in prepared:
        if row["before_sha256"] is not None:
            saved = backup / target.name
            shutil.copy2(target, saved)
            if digest(saved.read_bytes()) != row["before_sha256"]:
                raise RuntimeError("백업 해시 불일치")
    receipt = dict(files=[row for row, _, _ in prepared], field_stop_before=STOP_SHA,
                   hardware_access=False, live="NOT_RUN", backup_relative=str(backup.relative_to(root)))
    (backup / "BEFORE.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    for row, target, payload in prepared:
        # 백업 뒤에도 파일 상태를 재확인한다. 불일치하면 쓰기를 시작하지 않는다.
        before = row["before_sha256"]
        if (before is None and target.exists()) or (before is not None and digest(target.read_bytes()) != before):
            raise RuntimeError("적용 직전 코드 변경 감지")
    for row, target, payload in prepared:
        if digest(stop.read_bytes()) != STOP_SHA:
            raise RuntimeError("중단 기록 변경 감지; 추가 적용 중단")
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".patch_", dir=target.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        if digest(target.read_bytes()) != row["after_sha256"]:
            raise RuntimeError("적용 후 코드 해시 불일치")
    if digest(stop.read_bytes()) != STOP_SHA:
        raise RuntimeError("적용 후 중단 기록 변경 감지")
    receipt.update(status="SOFTWARE_PATCH_APPLIED", field_stop_after=STOP_SHA, files_written=len(prepared))
    (backup / "APPLIED.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / "v6_process_1793887709179")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = apply(args.root, args.archive, args.execute)
    print(json.dumps({key: value for key, value in result.items() if key != "files" or isinstance(value, int)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
