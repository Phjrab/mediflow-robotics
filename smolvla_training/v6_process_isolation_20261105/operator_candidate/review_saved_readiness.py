"""저장된 읽기 증거만 검토한다. live 허가·장치 접근·모터 쓰기 기능 없음."""
import argparse
import hashlib
import json
from pathlib import Path

JOINTS=("shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper")
NEXT_READ="sudo -v && bash ~/v6_process_1793887709179/operator_candidate/run_torque_readonly.sh --execute --inspect-position-goal --inspect-goal-feedback --operator-present --supported --stop-ready"


def decode_reply(reply):
    raw=bytes.fromhex(reply["raw_hex"])
    length=reply["expected_bytes"];motor=reply["expected_id"]
    if type(length) is not int or length not in (1,2,4) or type(motor) is not int or motor not in range(1,7):
        raise ValueError("요청 형식 오류")
    if type(reply.get("communication")) is not int or reply["communication"]!=0 or len(raw)!=length+6 or raw[:2]!=b"\xff\xff" or raw[2]!=motor or raw[3]!=length+2 or raw[4]!=0 or sum(raw[2:])%256!=255:
        raise ValueError("응답 무결성 오류")
    return int.from_bytes(raw[5:-1],"little")


def review(records,stop):
    result=dict(kind="SAVED_EVIDENCE_REVIEW",live_authorized=False,hardware_access=False,
                state_scope="마지막 저장 기록이며 현재 실시간 상태가 아님",blockers=[],findings=[],latest_run=None)
    if Path(stop).is_file():
        result["field_stop_sha256"]=hashlib.sha256(Path(stop).read_bytes()).hexdigest()
        result["blockers"].append("FIELD_STOP_REVIEW_REQUIRED")
    else:
        result["blockers"].append("FIELD_STOP_STATE_UNVERIFIED")
    paths=sorted(Path(records).glob("*/RESULT.json"),key=lambda p:int(p.parent.name) if p.parent.name.isdigit() else -1)
    if not paths:
        result["blockers"].append("NO_SAVED_RESULT")
        result["next_operator_read_command"]=NEXT_READ
        return result
    path=paths[-1];result["latest_run"]=path.parent.name
    result["result_sha256"]=hashlib.sha256(path.read_bytes()).hexdigest()
    try:data=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        result["blockers"].append("INVALID_RESULT_JSON");return result
    result["recorded_status"]=data.get("status")
    if data.get("status") not in ("NO_MISMATCH_WITHOUT_WRITES","READONLY_POSITION_GOAL_REVIEW") or data.get("error"):
        result["blockers"].append("LATEST_DIAGNOSIS_HOLD")
        result["recorded_error"]=data.get("error")
        return result
    if type(data.get("motor_register_writes")) is not int or data["motor_register_writes"]!=0:result["blockers"].append("UNEXPECTED_RECORDED_WRITE")
    if data.get("port_closed") is not True or data.get("cleanup_errors"):
        result["blockers"].append("PORT_OR_CLEANUP_UNVERIFIED")
    if data.get("field_stop_preserved") is not True:result["blockers"].append("STOP_PRESERVATION_UNVERIFIED")
    frames={}
    try:
        with (path.parent/"responses.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():continue
                item=json.loads(line);reply=item["reply"]
                frames[(reply["register"],reply["expected_id"])]=decode_reply(reply)
        result["findings"].append("SAVED_REPLY_INTEGRITY_CHECKED")
    except (OSError,ValueError,KeyError,TypeError):
        result["blockers"].append("RAW_REPLY_EVIDENCE_INVALID")
    report=data.get("position_goal_review") or {};rows=report.get("joints",[])
    if len(rows)!=6 or [r.get("joint") for r in rows]!=list(JOINTS):
        result["blockers"].append("POSITION_GOAL_REVIEW_MISSING")
        result["next_operator_read_command"]=NEXT_READ
        return result
    if any(type(row.get(key)) is not int for row in rows for key in ("present_raw","goal_raw")):
        result["blockers"].append("POSITION_GOAL_VALUE_INVALID")
        return result
    raw_ok=True
    for index,row in enumerate(rows,1):
        for register,key in (("Present_Position","present_raw"),("Goal_Position","goal_raw")):
            raw_ok=raw_ok and frames.get((register,index))==row.get(key)
        raw_ok=raw_ok and frames.get(("Torque_Enable",index))==0
        raw_ok=raw_ok and frames.get(("Operating_Mode",index))==0
        temperature=frames.get(("Present_Temperature",index))
        raw_ok=raw_ok and type(temperature) is int and 0<=temperature<45
    if not raw_ok:result["blockers"].append("SUMMARY_RAW_REPLY_MISMATCH")
    if report.get("home_within_5_degrees") is not True:result["blockers"].append("HOME_NOT_VERIFIED")
    result["joints"]=rows
    feedback_present=all(type(row.get("goal_feedback_raw")) is int for row in rows)
    if not feedback_present:
        result["blockers"].append("GOAL_FEEDBACK_67_NOT_MEASURED")
        result["next_operator_read_command"]=NEXT_READ
        return result
    if any(frames.get(("Goal_Feedback_67",i))!=row["goal_feedback_raw"] for i,row in enumerate(rows,1)):
        result["blockers"].append("FEEDBACK_RAW_REPLY_MISMATCH")
    control_match=all(row["goal_raw"]==row["present_raw"] for row in rows)
    feedback_match=all(row["goal_feedback_raw"]==row["present_raw"] for row in rows)
    result.update(control_goal_matches_current=control_match,feedback_matches_current=feedback_match)
    if not control_match and not feedback_match:
        result["blockers"].append("GOAL_STATE_MISMATCH")
    elif control_match!=feedback_match:
        result["blockers"].append("REGISTER_SEMANTICS_REVIEW_REQUIRED")
    result["findings"].append("NO_AUTOMATIC_REARM_OR_LIVE_AUTHORIZATION")
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    root=Path(__file__).resolve().parent.parent
    parser.add_argument("--records",type=Path,default=root/"readonly_diagnostics")
    parser.add_argument("--stop",type=Path,default=root/"operator_candidate/FIELD_STOP.json")
    parser.add_argument("--out",type=Path)
    args=parser.parse_args(argv)
    result=review(args.records,args.stop)
    if args.out:
        args.out.mkdir(parents=True,exist_ok=False)
        with (args.out/"READINESS.json").open("x",encoding="utf-8") as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
        lines=["# 저장 증거 기반 준비 상태","","실시간 모터 상태가 아닌 마지막 저장 결과 검토입니다. live 허가가 아닙니다.","",
               "마지막 기록: "+str(result["latest_run"]),"","## 남은 항목",""]
        lines += ["- "+code for code in result["blockers"]]
        lines += ["","모터·카메라·serial 접근, 추론, 레지스터 쓰기, 재무장, live: NOT_RUN."]
        (args.out/"READINESS.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="joints"},ensure_ascii=False))
    return 0


if __name__=="__main__":raise SystemExit(main())
