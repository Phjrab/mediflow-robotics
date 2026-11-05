"""과거 중단 보존본을 검토한 뒤 1회 읽기/추론만 재개한다. live 허가 기능 없음."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time
import uuid

import policy_api as api
import observation_contract as observation
from live_safety_runtime import Hold

STOP_NAME = "SERIAL_STOP.cleared_20261105_185629.json"
STOP_HASH = "16bf71d5f09ebb246a573a22cbad1faae57e5f50c0ae6ed945f2443c94797f5c"
CAL_HASH = "2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e"
MODEL_HASH = "b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(8*1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def stop_inventory(root=None):
    root = Path.home()/"nov_live_prep_20261104" if root is None else Path(root)
    return {p.name:sha(p) for p in sorted(root.glob("SERIAL_STOP*.json")) if p.is_file()}


def reviewed_reason(root=None):
    root = Path.home()/"nov_live_prep_20261104" if root is None else Path(root)
    if stop_inventory(root) != {STOP_NAME:STOP_HASH}:
        raise Hold("중단 기록 추가/누락/변경: 이번 읽기 전용 재개 검토 범위 밖")
    data = json.loads((root/STOP_NAME).read_text())
    if data.get("reason") != "토크ON 관측; 이후 serial 작업 중단" or data.get("observed") != {"joint":"shoulder_pan", "torque":{"shoulder_pan":1}}:
        raise Hold("과거 중단 사유/관측값 불일치")
    return data["reason"]


def safe_probe(probe, now):
    if probe.get("status") != "READ_ONLY_STATUS_COMPLETE_NOT_LIVE_READY" or probe.get("diagnostic_only") is not True:
        raise Hold("새 읽기 진단 성공 근거 없음")
    end = probe.get("end_unix")
    if type(end) not in (int,float) or not math.isfinite(end) or not 0 <= now-end <= 60:
        raise Hold("현재 상태 진단이 60초 이내가 아님")
    required = ("motor_calibration_matches", "home_body_within_5deg", "stop_record_unchanged")
    if any(probe.get(k) is not True for k in required) or probe.get("lelab_state") != "inactive":
        raise Hold("보정/home/기록/LeLab 진단 조건 실패")
    if any(probe.get(k,{}).get("free") is not True for k in ("owners_before","owners_after")):
        raise Hold("포트 비점유 확인 실패")
    if any(probe.get(k) != 0 or type(probe.get(k)) is not int for k in ("register_writes","goal_packets","blocked_packets")) or probe.get("cleanup_errors") != []:
        raise Hold("진단 쓰기/정리 상태 미확인")
    for key, expected in (("torque",0),("modes",0)):
        values=probe.get(key,{})
        if set(values)!=set(api.JOINTS) or any(type(v) is not int or v!=expected for v in values.values()):
            raise Hold("토크 모두OFF/위치모드0 조건 실패")
    temps=probe.get("temperatures",{})
    if set(temps)!=set(api.JOINTS) or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<45 for v in temps.values()):
        raise Hold("45도 미만 조건 실패")
    state=api.vector([probe.get("positions",{}).get(j) for j in api.JOINTS])
    return state


def relative(path):
    return str(Path(path).expanduser().resolve().relative_to(Path.home().resolve()))


def home_path(value):
    p=Path(value)
    if p.is_absolute() or ".." in p.parts:
        raise Hold("확인서 경로 형식 오류")
    return Path.home()/p


def issue(args):
    out=args.out.expanduser().resolve()
    probe_path=args.probe.expanduser().resolve()
    if out.exists() or out.parent!=Path(__file__).resolve().parent or probe_path.parent!=out.parent:
        raise Hold("확인서는 이 도구 폴더의 새 파일만 허용")
    now=time.time();probe=json.loads(probe_path.read_text());state=safe_probe(probe,now)
    reason=reviewed_reason()
    checkpoint=args.checkpoint.expanduser().resolve()
    units=args.unit_evidence.expanduser().resolve();home=args.home_state.expanduser().resolve()
    calibration=Path.home()/".cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json"
    if sha(calibration)!=CAL_HASH or sha(checkpoint/"model.safetensors")!=MODEL_HASH:
        raise Hold("검증한 모델/보정 해시 불일치")
    api.verify_units(units,checkpoint,calibration)
    home_data=json.loads(home.read_text())
    if home_data.get("joint_names")!=list(api.JOINTS) or home_data.get("tolerance_deg")!=5:
        raise Hold("home 근거 형식 불일치")
    api.home_gate(state,home_data)
    if api.inspect_checkpoint(checkpoint)["checkpoint_kind"]!="SO101_FINETUNED_CANDIDATE":
        raise Hold("학습 모델 미확인")
    config=args.camera_config.expanduser().resolve()
    contract=observation.load_contract(config);mapping=observation.saved_mapping(checkpoint)
    expected={"observation.images.end_effector":"observation.images.camera1",
              "observation.images.ceiling_vertical":"observation.images.camera2",
              "observation.images.ceiling_oblique":"observation.images.camera3"}
    if mapping!=expected:raise Hold("저장된 카메라 매핑이 확인된 학습 매핑과 다름")
    files={"unit_evidence":units,"home_state":home,"calibration":calibration,
           "camera_config":config,"model_config":checkpoint/"config.json",
           "preprocessor":checkpoint/"policy_preprocessor.json","postprocessor":checkpoint/"policy_postprocessor.json"}
    for path in checkpoint.glob("*processor*.safetensors"):
        files[path.name]=path
    receipt=dict(schema_version=1,scope="READ_ONLY_DRY_RUN",motor_writes_allowed=False,live_permitted=False,
                 run_id=uuid.uuid4().hex,issued_unix=now,expires_unix=now+300,reason_reviewed=reason,
                 cause_closed_for_live=False,stop_inventory=stop_inventory(),checkpoint=relative(checkpoint),
                 model_sha256=MODEL_HASH,probe_file=probe_path.name,probe_sha256=sha(probe_path),
                 files={k:dict(path=relative(p),sha256=sha(p)) for k,p in files.items()},cameras=contract["cameras"],
                 camera_mapping=mapping,unit="degree_x5_gripper_percent_0_100")
    if time.time()>=receipt["expires_unix"]:raise Hold("확인서 생성 중 유효시간 초과")
    with out.open("x",encoding="utf-8") as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(status="READ_ONLY_SESSION_ISSUED",file=out.name,scope=receipt["scope"],live_permitted=False),ensure_ascii=False))


class DrySession:
    def __init__(self, receipt, *, live=False):
        if live is not False:raise Hold("이 확인서는 live/토크 활성화에 사용할 수 없음")
        if receipt is None:raise Hold("1회 읽기 전용 재개 확인서 필요")
        self.path=Path(receipt).expanduser().resolve()
        if self.path.parent!=Path(__file__).resolve().parent:raise Hold("확인서 위치 불일치")
        self.data=json.loads(self.path.read_text());self.digest=sha(self.path)
        self.consumed=False;self.monitor_count=0
        self.check()

    def check(self):
        d=self.data;now=time.time()
        if sha(self.path)!=self.digest or d.get("schema_version")!=1 or d.get("scope")!="READ_ONLY_DRY_RUN" or d.get("live_permitted") is not False or d.get("motor_writes_allowed") is not False:
            raise Hold("확인서 변조/범위 불일치")
        issued=d.get("issued_unix");expires=d.get("expires_unix")
        if any(type(x) not in (int,float) or not math.isfinite(x) for x in (issued,expires)) or not issued<=now<expires or not 0<expires-issued<=300:
            raise Hold("재개 확인서 만료/시간 오류")
        reviewed_reason()
        if stop_inventory()!=d.get("stop_inventory"):raise Hold("중단 기록 집합/해시 변경")
        if d.get("model_sha256")!=MODEL_HASH:raise Hold("모델 확인서 해시 불일치")
        for item in d.get("files",{}).values():
            if sha(home_path(item["path"]))!=item["sha256"]:raise Hold("설정/보정/정규화 파일 변경")
        required={"unit_evidence","home_state","calibration","camera_config","model_config","preprocessor","postprocessor"}
        if not required.issubset(d.get("files",{})):raise Hold("필수 근거 파일 누락")
        probe=self.path.parent/d.get("probe_file","")
        if probe.parent!=self.path.parent or sha(probe)!=d.get("probe_sha256"):raise Hold("읽기 진단 근거 변경")
        marker=self.path.with_suffix(self.path.suffix+".consumed.json")
        if self.consumed:
            if not marker.is_file() or json.loads(marker.read_text()).get("receipt_sha256")!=self.digest:
                raise Hold("사용 기록 변경")
        elif marker.exists():raise Hold("이미 사용한 확인서; 자동 재사용 금지")

    def begin(self,args):
        self.check()
        if args.live:raise Hold("live 허가 없음")
        d=self.data
        for arg,key in (("unit_evidence","unit_evidence"),("home_state","home_state")):
            if getattr(args,arg) is None or Path(getattr(args,arg)).expanduser().resolve()!=home_path(d["files"][key]["path"]).resolve():
                raise Hold("실행 인자와 확인서 근거 파일 불일치")
        if Path(args.checkpoint).expanduser().resolve()!=home_path(d["checkpoint"]).resolve():raise Hold("체크포인트 경로 불일치")
        for role,attr in (("end_effector","end_effector_camera"),("ceiling_vertical","ceiling_vertical_camera"),("ceiling_oblique","ceiling_oblique_camera")):
            value=getattr(args,attr)
            if value is not None and str(value)!=d["cameras"][role]:raise Hold("카메라 인자/설정 불일치")
            setattr(args,attr,d["cameras"][role])
        marker=self.path.with_suffix(self.path.suffix+".consumed.json")
        with marker.open("x",encoding="utf-8") as f:json.dump(dict(receipt_sha256=self.digest,run_id=d["run_id"],time=time.time(),scope=d["scope"]),f)
        self.consumed=True


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--probe",type=Path,required=True);p.add_argument("--out",type=Path,required=True)
    p.add_argument("--checkpoint",type=Path,default=Path.home()/"smolvla_model/pretrained_model")
    p.add_argument("--unit-evidence",type=Path,default=Path.home()/"units_so101.json")
    p.add_argument("--home-state",type=Path,default=Path.home()/"home_state_so101.json")
    p.add_argument("--camera-config",type=Path,default=Path(__file__).resolve().parent/"resolved_camera_config.json")
    issue(p.parse_args())
