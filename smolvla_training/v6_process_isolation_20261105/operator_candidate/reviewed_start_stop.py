"""쓰기가 없었던 특정 시작 중단만 검토한다. 새 현장 확인 전 장치 권한 없음."""
import json
from pathlib import Path
from first_trial_core import Hold,JOINTS,Limits,sensor_gate
import hold_continuation as held
from start_goal_agreement import assess

RESULT_FOLDER="first_vla_from_hold_1794123713212153170"
RESULT_SHA="f2e4c81cb18473a4f091f49d9b12ed4944bce7a296e11bf1ee4caaaaf0bd9291"
POST_SHA="3ed9dc4110dfbdcfb79b352a96e3b13be3bd5ec22c758fe0efd3efea973511bf"
CLAIM_SHA="971e05d27fa1a192500d189ec6c8c26b5c62254a7ca38f4343f1b10a4d28f189"
NEXT_STOP="POST_START_REVIEW_STOP.json"
ACTION_RESULT_FOLDER="first_vla_from_hold_1794125680799432750"
ACTION_RESULT_SHA="8268fd1008190afbe45d25e45aa1acbbac84257d29f08f230920be1d86f219d2"
ACTION_POST_SHA="c70c2909aacf31ce763b6a1b1fff0d072b42092af5a37c31c6156bdc95311ae7"
ACTION_CLAIM_SHA="8c0028758824993cce426477e75302d89b5eee45bcd8d31cb753cfc0e7d4b7a5"
ACTION_NEXT_STOP="POST_ACTION_REVIEW_STOP.json"
SUCCESS_FOLDER="first_vla_from_hold_1794129083418129557"
SUCCESS_SHA="4909135423bb0788c3f0da7ac329f892a92f582911a7a7b85fcd8a45b247936c"
SUCCESS_CLAIM_SHA="1e2c050ffa16522902bee46de044e1de356104690d52b5cbb7b088c00f3a5e7d"


class ReviewedStartStop:
    def __init__(self,root,*,home=None,review_action=False,profile_success=False):
        if profile_success:review_action=True
        self.home=(Path.home() if home is None else Path(home)).resolve()
        self.root=Path(root).resolve()
        if self.root!=self.home/held.PROJECT_FOLDER/"operator_candidate":raise Hold("시작 검토 작업 폴더 불일치")
        self.result=self.home/RESULT_FOLDER/"RESULT.json"
        self.stop=self.root/held.NEW_STOP
        self.claim=self.root/"hold_usage"/(held.HOLD_SHA+".json")
        self.stop_sha=POST_SHA
        self.review_action=review_action
        self.original_paths=((self.result,RESULT_SHA),(self.stop,POST_SHA),(self.claim,CLAIM_SHA))
        self.next_stop_name=ACTION_NEXT_STOP if review_action else NEXT_STOP
        self.operator_token="REARM_ACTION" if review_action else "REARM_START"
        self.operator_message=(
            "앞선 쓰기0의 관절 범위 중단을 보존합니다. 유한한 정책 목표를 보정 범위와0.5도·속도 제한에 맞춰 자릅니다. 큰 점프·과도한 반복 보정·온도·HOLD는 유지합니다. 팔 지지·즉시 전원 차단 수단을 확인하세요." if review_action else
            "앞선 목표·토크 쓰기0의 시작 중단을 보존합니다. 시작 차이1칸 및 관절0.1도 이내만 허용하며 이동·온도·HOLD는 유지합니다. 팔 지지·즉시 전원 차단 수단을 현장에서 확인하세요.")
        if review_action:
            self.result=self.home/ACTION_RESULT_FOLDER/"RESULT.json"
            self.stop=self.root/NEXT_STOP
            self.claim=self.root/"start_review_usage"/(POST_SHA+".json")
            self.stop_sha=ACTION_POST_SHA
        self.check()
        data=json.loads(self.result.read_text(encoding="utf-8"))
        expected_ports={"io":True,"probe":True} if review_action else {"probe":True}
        if data.get("status")!="HOLD" or data.get("motor_goal_packets")!=0 or data.get("torque_register_writes")!=0 or data.get("targets") not in (None,[]) or data.get("cleanup_errors")!=[] or data.get("original_stop_preserved") is not True or data.get("ports_closed")!=expected_ports:
            raise Hold("검토 대상이 쓰기0·정리 완료의 시작 중단이 아님")
        inspection=data.get("inspection",{})
        expected_reason="관절 한계/형식 이탈" if review_action else "현재 목표 피드백·현재 위치·보정 범위 불일치"
        if data.get("error",{}).get("reason")!=expected_reason or inspection.get("bounds_contain_current_and_active_target") is not True:
            raise Hold("검토 대상의 중단 종류 불일치")
        if review_action:
            if "prepared_no_write" not in data.get("events",[]) or inspection.get("arm_ready") is not True:raise Hold("LIVE 준비 완료·쓰기0 범위 중단 근거 부족")
        elif inspection.get("active_target_exact_match") is not False:raise Hold("완전일치 시작 중단 근거 불일치")
        sensor_gate(inspection["snapshot"],Limits(),expected_torque=1,starting=True)
        agreement=assess(inspection["present_raw"],inspection["target_feedback_raw"],inspection["snapshot"]["state"],inspection["target_feedback_state"],inspection["joint_bounds"])
        if (not review_action and agreement["current_raw_exact"]) or not agreement["start_check_passed"]:raise Hold("1칸 시작 차이 검토 범위를 벗어남")
        prior=json.loads(self.claim.read_text(encoding="utf-8"))
        if prior.get("field_stop_sha256")!=held.STOP_SHA or prior.get("operator_review") is not True or prior.get("scope")!="FIRST_MOTION_FROM_HELD_POSE":raise Hold("앞선 현장 확인 기록 불일치")
        self.summary=dict(scope="REVIEWED_ZERO_WRITE_ACTION_HOLD" if review_action else "REVIEWED_ZERO_WRITE_START_HOLD",result_sha256=ACTION_RESULT_SHA if review_action else RESULT_SHA,
                          post_hold_stop_sha256=self.stop_sha,prior_usage_sha256=ACTION_CLAIM_SHA if review_action else CLAIM_SHA,
                          max_raw_difference=1,max_joint_degrees=.1,max_gripper_points=.1,
                          fresh_operator_review_required=True,live_authorized=False)
        self.profile_only=profile_success
        if profile_success:
            self.profile_paths=((self.home/SUCCESS_FOLDER/"RESULT.json",SUCCESS_SHA),
                (self.root/"start_review_usage"/(ACTION_POST_SHA+".json"),SUCCESS_CLAIM_SHA))
            self.next_stop_name="POST_TIMING_PROFILE_STOP.json"
            self.stop_sha="timing_"+SUCCESS_SHA
            self.operator_token="READ_TIMING"
            self.operator_message="모터 목표·토크 쓰기 없이 센서와 카메라 읽기 시간을10회 계측합니다. 토크는 변경하지 않습니다. 현장에서 상태를 확인한 뒤 진행하세요."
            self.usage_scope="READ_ONLY_CONTROL_TIMING"
            self.summary.update(scope=self.usage_scope,success_sha256=SUCCESS_SHA,goal_writes_allowed=False)
            self.check()
            success=json.loads(self.profile_paths[0][0].read_text(encoding="utf-8"))
            if success.get("status")!="FIRST_TRIAL_COMPLETE" or success.get("motor_goal_packets")!=10 or success.get("torque_register_writes")!=0 or success.get("cleanup_errors")!=[] or success.get("ports_closed")!={"io":True,"probe":True}:raise Hold("첫 live 완료 근거 불일치")

    def check(self):
        if hasattr(self,"profile_paths") and (self.root/ACTION_NEXT_STOP).exists():raise Hold("첫 live 이후 새 중단 기록 존재")
        if (self.root/self.next_stop_name).exists():raise Hold("재개 후 새 중단 기록 존재; 자동 반복 금지")
        paths=self.original_paths
        if self.review_action:paths=paths+((self.result,ACTION_RESULT_SHA),(self.stop,ACTION_POST_SHA),(self.claim,ACTION_CLAIM_SHA))
        paths=paths+getattr(self,"profile_paths",())
        for path,digest in paths:
            if path.is_symlink() or path.parent.is_symlink() or held.sha(path)!=digest:raise Hold("시작 중단 검토 원본 변경")
