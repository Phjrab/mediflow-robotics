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
    def __init__(self,root,*,home=None,review_action=False,profile_success=False,compensated_profile=False,repeat_live=False,quantized_repeat=False,continue_clipped=False,work_segment=False,work_session=False):
        if work_session:work_segment=True
        if work_segment:continue_clipped=True
        if continue_clipped:quantized_repeat=True
        self.quantized_repeat=quantized_repeat
        if quantized_repeat:repeat_live=True
        if repeat_live:profile_success=True
        if compensated_profile:profile_success=True
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
            if compensated_profile:
                baseline=self.home/"control_timing_1794130259923524372/TIMING_RESULT.json"
                self.profile_paths+=((baseline,"68b4451c3add733babd22f676be2cf012843c48bdfee77c5aa70fc67f6452023"),)
                self.next_stop_name="POST_COMPENSATED_TIMING_STOP.json"
                self.stop_sha="timing_compensated_"+SUCCESS_SHA
                self.usage_scope="READ_ONLY_COMPENSATED_TIMING"
                self.summary.update(scope=self.usage_scope,baseline_sha256=self.profile_paths[-1][1])
                self.operator_message="완료된 읽기 계측과 비교해 작업 후 남은 주기만 기다립니다. 모터 목표·토크 쓰기와 추론은 없으며 같은 읽기를10회 수행합니다."
            self.check()
            success=json.loads(self.profile_paths[0][0].read_text(encoding="utf-8"))
            if success.get("status")!="FIRST_TRIAL_COMPLETE" or success.get("motor_goal_packets")!=10 or success.get("torque_register_writes")!=0 or success.get("cleanup_errors")!=[] or success.get("ports_closed")!={"io":True,"probe":True}:raise Hold("첫 live 완료 근거 불일치")
            if repeat_live:
                self.profile_only=False;self.repeat_live=True
                self.profile_paths+=((self.home/"control_timing_1794130957872490741/TIMING_RESULT.json","90e6ba8ac729421074d0ef03d7c50ddc98997c61b830a74108bdcf01d05ff316"),)
                self.next_stop_name="POST_REPEAT_LIVE_STOP.json";self.stop_sha="repeat_"+SUCCESS_SHA
                self.operator_token="REARM_REPEAT";self.usage_scope="SHORT_LIVE_CADENCE_REPEAT"
                self.operator_message="첫 live 성공과 읽기 주기 개선 기록을 보존합니다. 직전 목표와 현재 피드백을 대조한 뒤 최대10스텝/5초를 반복합니다. 팔 지지·즉시 전원 차단 수단을 현장에서 확인하세요."
                self.expected_last_goal=success["targets"][-1]["target"]
                self.summary.update(scope=self.usage_scope,goal_writes_allowed=True,expected_last_goal=self.expected_last_goal)
                if quantized_repeat:
                    self.profile_paths+=((self.home/"first_vla_from_hold_1794131617431241184/RESULT.json","12ae262414e50f3e00caadf11117ad25697f047c6d67283d9448535c708e755d"),
                        (self.root/"POST_REPEAT_LIVE_STOP.json","debe50acb60dbc9878957fd8f580389e264730bf3e85d34a3ef14b94ebed14c8"))
                    self.next_stop_name="POST_QUANTIZED_REPEAT_STOP.json";self.stop_sha="quantized_repeat_"+SUCCESS_SHA
                    self.operator_token="REARM_QUANTIZED";self.usage_scope="QUANTIZATION_AWARE_SHORT_REPEAT"
                    self.operator_message="손목 정수 변환 오차 중단을 보존합니다. 직전 목표 일치를 확인하고 새 목표는 정수 변환 여유를 두어 이동·속도·보정 범위 안으로 제한합니다. 최대10스텝/5초이며 현장 정지 수단을 확인하세요."
                    self.summary.update(scope=self.usage_scope,encoder_margin_for_new_goals=True,tracking_allowance_one_count=True)
                    if continue_clipped:
                        latest=self.home/"first_vla_from_hold_1794132383185029292/RESULT.json"
                        self.profile_paths+=((latest,"b6e4f34528b8d312d73081166003c856c43fb4be3f4aad191f6203e072d6c7df"),
                            (self.root/"POST_QUANTIZED_REPEAT_STOP.json","e1b2559920274fa446fd991a457277d2dc9729db8eb796ad906648bd5867d882"))
                        self.next_stop_name="POST_CLIP_WARNING_TRIAL_STOP.json";self.stop_sha="clip_warning_"+SUCCESS_SHA
                        self.operator_token="REARM_CONTINUE";self.usage_scope="BOUNDED_CLIP_WARNING_TRIAL"
                        self.operator_message="최근9스텝 결과와 중단 기록을 보존합니다. 이번10스텝 시험에서는 높은 목표 보정 비율을 경고로 기록하며 이동·속도·온도·과부하·비정상 값 제한은 유지합니다. 현장에서 정지 수단을 확인하세요."
                        self.check()
                        recorded=json.loads(latest.read_text(encoding="utf-8"))
                        if recorded.get("motor_goal_packets")!=9 or recorded.get("torque_register_writes")!=0 or len(recorded.get("targets",[]))!=9 or recorded.get("error",{}).get("reason")!="정책-제어 clip 비율 초과":raise Hold("9스텝 중단 검토 범위 불일치")
                        self.expected_last_goal=recorded["targets"][-1]["target"]
                        self.summary.update(scope=self.usage_scope,clip_ratio_is_warning=True,expected_last_goal=self.expected_last_goal)
                        if work_segment:
                            completed=self.home/"first_vla_from_hold_1794132963959272373/RESULT.json"
                            self.profile_paths+=((completed,"83ed69d8c95706ce775c8a3d626ad80fd58f4919f5db48fa264ee7b79b0bbcad"),)
                            self.next_stop_name="POST_WORK_SEGMENT_STOP.json";self.stop_sha="work_83ed69d8c95706ce775c8a3d626ad80fd58f4919f5db48fa264ee7b79b0bbcad"
                            self.operator_token="REARM_WORK";self.usage_scope="SINGLE_CHUNK_WORK_SEGMENT"
                            self.operator_message="10스텝 완료 기록을 보존하고50스텝 작업 구간으로 진행합니다. 최대10초, 현재 위치 대비 목표2도, 연속 목표20도/초이며 정수 변환 여유·온도·과부하 제한은 유지합니다. 팔과 주변 공간·전원 차단 수단을 현장에서 확인하세요."
                            self.max_steps=50;self.max_seconds=10
                            self.check();latest_complete=json.loads(completed.read_text(encoding="utf-8"))
                            if latest_complete.get("status")!="FIRST_TRIAL_COMPLETE" or latest_complete.get("motor_goal_packets")!=10 or latest_complete.get("torque_register_writes")!=0 or latest_complete.get("cleanup_errors")!=[]:raise Hold("작업 구간 이전 완료 근거 불일치")
                            self.expected_last_goal=latest_complete["targets"][-1]["target"]
                            self.summary.update(scope=self.usage_scope,max_steps=50,max_seconds=10,relative_goal_cap_degrees=2,speed_degrees_per_second=20,expected_last_goal=self.expected_last_goal)
                            if work_session:
                                latest_work=self.home/"first_vla_from_hold_1794134086238144466/RESULT.json"
                                self.profile_paths+=((latest_work,"793b311068cdf7ed753ecbb025aa40f39a3a619c95966b6bb9ad399e7d5ef829"),)
                                self.next_stop_name="POST_WORK_SESSION_STOP.json";self.stop_sha="session_793b311068cdf7ed753ecbb025aa40f39a3a619c95966b6bb9ad399e7d5ef829"
                                self.operator_token="REARM_SESSION";self.usage_scope="SIX_CHUNKS_THIRTY_SECOND_SESSION"
                                self.operator_message="50스텝 완료 기록을 보존하고 모델을 재사용해 최대6구간/300스텝/30초를 실행합니다. 매 구간 새 영상·관절값으로 예측하며 상대 목표2도·속도20도/초·온도·과부하 제한은 유지합니다. 주변 공간과 즉시 전원 차단 수단을 확인하세요."
                                self.max_steps=300;self.max_seconds=30;self.tracking_cap_degrees=2.
                                self.check();work_record=json.loads(latest_work.read_text(encoding="utf-8"))
                                if work_record.get("status")!="WORK_SEGMENT_COMPLETE" or work_record.get("motor_goal_packets")!=50 or work_record.get("torque_register_writes")!=0 or work_record.get("cleanup_errors")!=[]:raise Hold("50스텝 완료 근거 불일치")
                                self.expected_last_goal=work_record["targets"][-1]["target"]
                                self.summary.update(scope=self.usage_scope,max_steps=300,max_seconds=30,max_chunks=6,tracking_cap_degrees=2,expected_last_goal=self.expected_last_goal)
                self.check()

    def check(self):
        if hasattr(self,"profile_paths") and (self.root/ACTION_NEXT_STOP).exists():raise Hold("첫 live 이후 새 중단 기록 존재")
        if (self.root/self.next_stop_name).exists():raise Hold("재개 후 새 중단 기록 존재; 자동 반복 금지")
        paths=self.original_paths
        if self.review_action:paths=paths+((self.result,ACTION_RESULT_SHA),(self.stop,ACTION_POST_SHA),(self.claim,ACTION_CLAIM_SHA))
        paths=paths+getattr(self,"profile_paths",())
        for path,digest in paths:
            if path.is_symlink() or path.parent.is_symlink() or held.sha(path)!=digest:raise Hold("시작 중단 검토 원본 변경")
