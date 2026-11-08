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


class ReviewedStartStop:
    def __init__(self,root,*,home=None):
        self.home=(Path.home() if home is None else Path(home)).resolve()
        self.root=Path(root).resolve()
        if self.root!=self.home/held.PROJECT_FOLDER/"operator_candidate":raise Hold("시작 검토 작업 폴더 불일치")
        self.result=self.home/RESULT_FOLDER/"RESULT.json"
        self.stop=self.root/held.NEW_STOP
        self.claim=self.root/"hold_usage"/(held.HOLD_SHA+".json")
        self.stop_sha=POST_SHA
        self.check()
        data=json.loads(self.result.read_text(encoding="utf-8"))
        if data.get("status")!="HOLD" or data.get("motor_goal_packets")!=0 or data.get("torque_register_writes")!=0 or data.get("targets") not in (None,[]) or data.get("cleanup_errors")!=[] or data.get("original_stop_preserved") is not True or data.get("ports_closed")!={"probe":True}:
            raise Hold("검토 대상이 쓰기0·정리 완료의 시작 중단이 아님")
        inspection=data.get("inspection",{})
        if data.get("error",{}).get("reason")!="현재 목표 피드백·현재 위치·보정 범위 불일치" or inspection.get("bounds_contain_current_and_active_target") is not True or inspection.get("active_target_exact_match") is not False:
            raise Hold("검토 대상의 중단 종류 불일치")
        sensor_gate(inspection["snapshot"],Limits(),expected_torque=1,starting=True)
        agreement=assess(inspection["present_raw"],inspection["target_feedback_raw"],inspection["snapshot"]["state"],inspection["target_feedback_state"],inspection["joint_bounds"])
        if agreement["current_raw_exact"] or not agreement["start_check_passed"]:raise Hold("1칸 시작 차이 검토 범위를 벗어남")
        prior=json.loads(self.claim.read_text(encoding="utf-8"))
        if prior.get("field_stop_sha256")!=held.STOP_SHA or prior.get("operator_review") is not True or prior.get("scope")!="FIRST_MOTION_FROM_HELD_POSE":raise Hold("앞선 현장 확인 기록 불일치")
        self.summary=dict(scope="REVIEWED_ZERO_WRITE_START_HOLD",result_sha256=RESULT_SHA,
                          post_hold_stop_sha256=POST_SHA,prior_usage_sha256=CLAIM_SHA,
                          max_raw_difference=1,max_joint_degrees=.1,max_gripper_points=.1,
                          fresh_operator_review_required=True,live_authorized=False)

    def check(self):
        if (self.root/NEXT_STOP).exists():raise Hold("재개 후 새 중단 기록 존재; 자동 반복 금지")
        for path,digest in ((self.result,RESULT_SHA),(self.stop,POST_SHA),(self.claim,CLAIM_SHA)):
            if path.is_symlink() or path.parent.is_symlink() or held.sha(path)!=digest:raise Hold("시작 중단 검토 원본 변경")
