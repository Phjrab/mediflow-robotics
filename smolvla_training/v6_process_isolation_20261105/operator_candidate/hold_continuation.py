"""검수한 토크 유지 기록 + 새 운영자 확인의 일회성 첫 동작 범위. 장치 API 없음."""
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid
from first_trial_core import Hold,JOINTS,vector

HOLD_SHA="4767b9cf28dce8ec3cbb7c0210d9ba8f0f73c74b18a00723bb6399c46f733183"
REVIEW_SHA="742dc63e6ba15b9b8f23f6d16f512decc21126b04b0798901580abfed2310488"
STOP_SHA="e8a2bf9eb72c676198483aa8b4e150058a4595f37c7b35f6015216798deee56c"
CAL_SHA="2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e"
HOME_SHA="73ed963208beebe7080a681302350d35a20fc0cda412676dd547b7fc9f7d6074"
NEW_STOP="POST_HOLD_STOP.json"
PROJECT_FOLDER="v6_process_1793887709179"

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def exclusive_json(path,data):
    with Path(path).open("x",encoding="utf-8") as stream:
        json.dump(data,stream,ensure_ascii=False,allow_nan=False,indent=2)
        stream.flush();os.fsync(stream.fileno())

class HoldContinuation:
    def __init__(self,root,log,*,home=None,start_review=None):
        self.home=Path.home() if home is None else Path(home)
        self.home=self.home.resolve();self.root=Path(root).resolve()
        requested=Path(log).expanduser()
        if requested.is_symlink() or requested.parent.is_symlink():raise Hold("토크 기록 심볼릭 링크 금지")
        self.log=requested.resolve(strict=True);self.review=self.log.with_name("HOLD_REVIEW.json")
        if self.review.is_symlink() or self.log.name!="events.jsonl" or self.log.parent.parent!=self.home or not self.log.parent.name.startswith("torque_guard_") or self.root!=self.home/PROJECT_FOLDER/"operator_candidate":
            raise Hold("토크 기록 위치 불일치")
        self.marker_dir=self.root/"hold_usage";self.marker=self.marker_dir/(HOLD_SHA+".json")
        self.start_review=start_review
        if start_review is not None:
            from reviewed_start_stop import ReviewedStartStop
            if type(start_review) is not ReviewedStartStop or start_review.root!=self.root or start_review.home!=self.home:
                raise Hold("시작 중단 검토 객체·위치 불일치")
            start_review.check()
            self.marker_dir=self.root/"start_review_usage";self.marker=self.marker_dir/(start_review.stop_sha+".json")
        self.claimed=False;self.operator_reviewed=False;self.bound=False;self.marker_sha=None
        self.run_id=uuid.uuid4().hex
        self.check()
        rows=[json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        results=[r for r in rows if r.get("event")=="RESULT"]
        if len(results)!=1:raise Hold("토크 결과 수 불일치")
        result=results[0];state=result.get("result",{})
        if result.get("status")!="TORQUE_ON_HOLD" or type(result.get("authorized_writes")) is not int or result["authorized_writes"]!=1 or type(result.get("goal_writes")) is not int or result["goal_writes"]!=0 or result.get("port_closed") is not True or result.get("live")!="NOT_RUN":
            raise Hold("토크 유지 통과 근거 불충족")
        for key,expected in (("torque",1),("modes",0)):
            values=state.get(key,{})
            if set(values)!=set(JOINTS) or any(type(v) is not int or v!=expected for v in values.values()):raise Hold("토크·모드 근거 불일치")
        vector([state.get("current",{}).get(j) for j in JOINTS])
        checks=[r for r in rows if r.get("event")=="STATE_AFTER_ON"]
        if len(checks)!=5 or [r.get("tick") for r in checks]!=list(range(5)):raise Hold("토크 안정 관측 누락")
        if any(set(r.get("delta",{}))!=set(JOINTS) or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>.5 for v in r["delta"].values()) for r in checks):raise Hold("토크 유지 자세 변화 상한")
        if sum(r.get("event")=="AUTHORIZED_WRITE" for r in rows)!=1 or sum(r.get("event")=="WRITE_TRANSMITTED" for r in rows)!=1 or any(r.get("event") in ("STOPPED","BLOCKED_COMMAND") for r in rows):raise Hold("토크 송신 이력 불일치")
        if not any(r.get("event")=="FIELD_STOP_PRESERVED" and r.get("preserved") is True for r in rows):raise Hold("중단 보존 근거 없음")
        review=json.loads(self.review.read_text(encoding="utf-8"))
        if review.get("scope")!="TORQUE_HOLD_ONLY" or review.get("live_authorized") is not False or review.get("operator_review") is not True or review.get("stop_preserved") is not True or review.get("stop_sha256")!=STOP_SHA:raise Hold("원래 토크 전용 검토 범위 불일치")
        self.summary=dict(hold_sha256=HOLD_SHA,hold_review_sha256=REVIEW_SHA,field_stop_sha256=STOP_SHA,
                          hold_only_receipt_is_not_live_permission=True,operator_live_review_required=True)
        if self.start_review is not None:self.summary["reviewed_start_stop"]=self.start_review.summary

    def check(self):
        if self.start_review is not None:self.start_review.check()
        elif (self.root/NEW_STOP).exists():raise Hold("새 현장 중단 기록 존재; 자동 재개 금지")
        stop=self.root/"FIELD_STOP.json"
        if stop.is_symlink() or sha(stop)!=STOP_SHA or sha(self.log)!=HOLD_SHA or sha(self.review)!=REVIEW_SHA:
            raise Hold("중단 또는 토크 유지 근거 변경")
        if self.marker_dir.is_symlink():raise Hold("사용 기록 경로 심볼릭 링크 금지")
        if self.claimed:
            if not self.operator_reviewed or sha(self.marker)!=self.marker_sha:raise Hold("일회 사용 기록 변경")
        elif self.marker.exists():raise Hold("이미 사용한 토크 유지 기록; 자동 재사용 금지")

    def bind_context(self,context):
        self.check()
        if sha(context["calibration"])!=CAL_SHA or sha(context["homefile"])!=HOME_SHA:raise Hold("토크 시험과 보정·home 근거 불일치")
        self.context_digest=hashlib.sha256(json.dumps(context["files"],sort_keys=True).encode("utf-8")).hexdigest()
        self.bound=True

    def confirm_operator(self,confirm):
        self.check()
        if not self.bound:raise Hold("실행 설정 검증 전 운영자 검토 금지")
        if self.start_review is None:
            confirm("REARM_HOLD","토크ON 성공 기록과 원래 중단 기록을 보존합니다. 자동 토크 변경 없이 첫10스텝/5초 시험을 검토합니다. 팔 지지·즉시 전원 차단 수단을 현장에서 확인하세요.")
        else:
            confirm(self.start_review.operator_token,self.start_review.operator_message)
        self.operator_reviewed=True

    def claim(self,output):
        self.check()
        if not self.bound or not self.operator_reviewed or self.claimed:raise Hold("새 운영자 검토 또는 일회 사용 조건 불충족")
        self.marker_dir.mkdir(exist_ok=True)
        receipt=dict(self.summary,scope="FIRST_MOTION_FROM_HELD_POSE",run_id=self.run_id,context_sha256=self.context_digest,
                     operator_review=True,unix=time.time(),max_steps=10,max_seconds=5,live_confirm_still_required=True)
        try:exclusive_json(self.marker,receipt)
        except FileExistsError as error:raise Hold("같은 토크 기록을 다른 실행이 이미 사용") from error
        self.marker_sha=sha(self.marker);self.claimed=True
        exclusive_json(Path(output)/"LIVE_OPERATOR_REVIEW.json",receipt)

    def require_claimed(self,context=None):
        self.check()
        if not self.claimed or not self.operator_reviewed or not self.bound:raise Hold("운영자 검토·일회 사용 완료 전 장치 접근 금지")
        if context is not None and hashlib.sha256(json.dumps(context["files"],sort_keys=True).encode("utf-8")).hexdigest()!=self.context_digest:
            raise Hold("운영자 검토 후 실행 설정 불일치")


def latch_new_stop(root,result,*,start_review=None):
    """원래 FIELD_STOP은 보존하고, 새 실패는 별도 기록으로 차단한다."""
    from run_receipts import json_value
    path=Path(root)/(NEW_STOP if start_review is None else start_review.next_stop_name)
    kind="POST_HOLD_FIRST_TRIAL_STOP" if start_review is None else "REVIEWED_ACTION_FIRST_TRIAL_STOP" if start_review.review_action else "REVIEWED_START_FIRST_TRIAL_STOP"
    data=dict(status="HOLD",kind=kind,unix=time.time(),
              error=result.get("error"),goal_packets=result.get("motor_goal_packets",0),
              cleanup_errors=result.get("cleanup_errors",[]),output=result.get("output"))
    try:
        exclusive_json(path,json_value(data))
        return dict(created=True,name=path.name,sha256=sha(path))
    except FileExistsError:
        return dict(created=False,existing_preserved=True,name=path.name,sha256=sha(path))
