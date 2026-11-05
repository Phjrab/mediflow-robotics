"""읽기 결과로 목표 초기화 검토표만 작성한다. serial/모터 쓰기 구현 없음."""
import argparse
import json
import math
from pathlib import Path

JOINTS=("shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper")
CAL_HASH="2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e"

def create_plan(snapshot):
    if snapshot.get("status")!="GOAL_READ_CROSSCHECK_COMPLETE" or snapshot.get("register_writes")!=0:raise ValueError("읽기 교차검증 성공 근거 없음")
    if snapshot.get("calibration_sha256")!=CAL_HASH or snapshot.get("records_preserved") is not True:raise ValueError("보정/중단 기록 미확인")
    rows=snapshot.get("joints",[])
    if [row.get("joint") for row in rows]!=list(JOINTS):raise ValueError("관절 순서 불일치")
    result=[]
    for row in rows:
        if type(row.get("torque")) is not int or row["torque"]!=0:raise ValueError("토크OFF 필요")
        if row.get("mode")!=0 or row.get("moving")!=0 or row.get("status")!=0:raise ValueError("정지/위치모드/상태 미확인")
        temp=row.get("temperature")
        if type(temp) not in (int,float) or not math.isfinite(temp) or not 0<=temp<45:raise ValueError("온도45도 미만 필요")
        load=row.get("load")
        if type(load) not in (int,float) or not math.isfinite(load) or abs(load)>=200:raise ValueError("부하 상한/미확인")
        current=row.get("present_raw");goal=row.get("goal_individual_raw");group=row.get("goal_sync_raw")
        if any(type(v) is not int or not 0<=v<=4095 for v in (current,goal,group)):raise ValueError("원시 위치 형식 오류")
        if goal!=group:raise ValueError("개별/동기 목표 읽기 불일치")
        bounds=row.get("calibration_raw_range",[])
        if len(bounds)!=2 or any(type(v) is not int for v in bounds) or not 0<=bounds[0]<bounds[1]<=4095 or not bounds[0]<=current<=bounds[1]:raise ValueError("현재 위치/보정 범위 오류")
        result.append(dict(joint=row["joint"],present_raw=current,existing_goal_raw=goal,proposed_goal_raw=current,
                           change_needed=current!=goal,calibration_raw_range=bounds))
    return dict(status="PLAN_ONLY_FIELD_REVIEW_REQUIRED",execution_authorized=False,register_writes=0,
                joints=result,goal_register=dict(address=42,width=2),torque_register=dict(address=40,width=1),
                source="읽기 교차검증 결과",same_goal_count=sum(not row["change_needed"] for row in result),
                warning="제안값은 실행 명령이 아님. 목표 쓰기와 토크의 현장 상호작용은 미확인. 기존 재무장 검사는 유지.",
                required_before_execution=["현장 입회·팔 지지·즉시 전원 차단 수단","새 읽기 점검과 SDK 패킷 검토","별도 승인된 초기화 시험과 토크 상태 확인","정확한 목표 일치 및 기존 재무장 조건 재검증"])

def main():
    p=argparse.ArgumentParser();p.add_argument("--input",type=Path,required=True);p.add_argument("--out",type=Path,required=True);args=p.parse_args()
    result=create_plan(json.loads(args.input.read_text(encoding="utf-8")))
    with args.out.open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(status=result["status"],same_goal_count=result["same_goal_count"],register_writes=0,execution_authorized=False),ensure_ascii=False))

if __name__=="__main__":main()
