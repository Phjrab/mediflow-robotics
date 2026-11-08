"""ON 유지 상태 시작 경로의 소프트웨어 패키지. 장치 접근 없음."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
SOURCE=ROOT/"reports/v6_process_isolation_20261105/operator_candidate"
CHANGED=("trial_runtime.py","arm_source.py","operator_arm.py")
ADDED=("hold_continuation.py","first_trial_from_hold.py","run_first_from_hold.sh","test_hold_continuation.py",
       "test_first_trial_from_hold.py","test_device_continuation_guard.py","test_goal_sdk_integration.py")
def sha(data):return hashlib.sha256(data).hexdigest()

def main():
    rows=[]
    for name in CHANGED+ADDED:
        content=(SOURCE/name).read_bytes()
        if name.endswith(".py"):ast.parse(content.decode("utf-8"),feature_version=(3,10))
        rows.append(dict(path="operator_candidate/"+name,before_sha256=sha((HERE/"before"/name).read_bytes()) if name in CHANGED else None,
                         after_sha256=sha(content),bytes=len(content)))
    manifest=dict(base_commit="b5ef9a4",hardware_access=False,operator_live_required=True,files=rows)
    package=HERE/"from_hold_patch_final.zip"
    with zipfile.ZipFile(package,"x",compression=zipfile.ZIP_DEFLATED) as bundle:
        for row in rows:bundle.write(SOURCE/Path(row["path"]).name,row["path"])
        bundle.writestr("deployment_manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(package) as bundle:
        assert bundle.testzip() is None
        assert all(sha(bundle.read(row["path"]))==row["after_sha256"] for row in rows)
    (HERE/"deployment_manifest_final.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    installer=(ROOT/"reports/torque_tx_fix_20261108/apply_torque_tx_patch.py").read_text(encoding="utf-8")
    replacements=[]
    for node in ast.parse(installer).body:
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id=="FILES":replacements.append((node.lineno,node.end_lineno,"FILES = "+repr(set(CHANGED+ADDED))))
                if isinstance(target,ast.Name) and target.id=="ARCHIVE_SHA":replacements.append((node.lineno,node.end_lineno,"ARCHIVE_SHA = "+repr(sha(package.read_bytes()))))
    lines=installer.splitlines()
    for start,end,replacement in sorted(replacements,reverse=True):lines[start-1:end]=[replacement]
    (HERE/"apply_from_hold_patch_final.py").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(dict(files=len(rows),zip_verified=True,sha256=sha(package.read_bytes()))))

if __name__=="__main__":main()
