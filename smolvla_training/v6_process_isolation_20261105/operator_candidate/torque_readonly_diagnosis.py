"""운영자 전용 응답 진단. 기본은 파일 검사만. 레지스터 쓰기·재무장 없음."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
PORT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00"
STOP_HASH = "e8a2bf9eb72c676198483aa8b4e150058a4595f37c7b35f6015216798deee56c"
CAL_HASH = "2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e"
HOME_HASH = "73ed963208beebe7080a681302350d35a20fc0cda412676dd547b7fc9f7d6074"


class DiagnosisStop(RuntimeError):
    def __init__(self, reason, code, observed=None):
        super().__init__(reason)
        self.code, self.observed = code, observed


def check_files():
    folder = Path(__file__).resolve().parent
    stop = folder / "FIELD_STOP.json"
    calibration = Path.home() / ".cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json"
    for path, digest in ((stop, STOP_HASH), (calibration, CAL_HASH)):
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("중단 기록 또는 보정 파일이 검토본과 다름; 장치 미연결")
    record = json.loads(stop.read_text(encoding="utf-8"))
    if record.get("status") != "HOLD" or record.get("goal_packets") != 1:
        raise RuntimeError("검토한 현장 중단 기록이 아님")
    return calibration


def port_owners(expected):
    if not Path(PORT).exists():
        raise RuntimeError("follower 장치 경로 없음")
    result = subprocess.run(["sudo", "-n", "fuser", PORT], capture_output=True,
                            text=True, timeout=5, env=dict(os.environ, LC_ALL="C"))
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        actual = set()
    elif result.returncode == 0 and result.stderr.strip() in ("", PORT + ":", str(Path(PORT).resolve()) + ":"):
        values = result.stdout.split()
        if not values or not all(v.isdigit() for v in values):
            raise RuntimeError("serial 점유 응답 형식 미확인")
        actual = {int(v) for v in values}
    else:
        raise RuntimeError("serial 점유 조회 실패; sudo -v 또는 점유 상태 확인 필요")
    if actual != set(expected):
        raise RuntimeError("serial 점유 불일치; 다른 프로그램을 자동 종료하지 않음")


def service_gate():
    result = subprocess.run(["systemctl", "--user", "is-active", "lelab-jetson-py310.service"],
                            capture_output=True, text=True, timeout=5)
    if result.stdout.strip() != "inactive":
        raise RuntimeError("LeLab inactive 확인 실패; 서비스를 자동 변경하지 않음")


def probe(bus, replies, emit, *, rounds=2):
    deadline = time.monotonic() + 8
    summaries = []
    for index in range(rounds):
        summary = dict(round=index, torque={}, temperatures={})
        summaries.append(summary)
        for register, target in (("Torque_Enable", summary["torque"]),
                                 ("Present_Temperature", summary["temperatures"])):
            for joint in JOINTS:
                if time.monotonic() > deadline:
                    raise DiagnosisStop("진단 시간 상한", "TIME_LIMIT", summaries)
                replies.begin(register)
                try:
                    result = bus.sync_read(register, motors=[joint], normalize=False, num_retry=0)
                except Exception as error:
                    raise DiagnosisStop(str(error), "RESPONSE_REJECTED",
                                        dict(response=getattr(error, "observed", None), summaries=summaries)) from error
                if not isinstance(result, dict) or set(result) != {joint} or type(result[joint]) is not int:
                    raise DiagnosisStop("단일 관절 응답 형식 불일치", "DECODE_MISMATCH", summaries)
                value = result[joint]
                target[joint] = value
                emit(dict(round=index, register=register, joint=joint, value=value,
                          reply=replies.history[-1] if replies.history else None))
                if register == "Torque_Enable" and value != 0:
                    raise DiagnosisStop("정상 형식의 0이 아닌 토크 응답; 추가 통신 중단", "NONZERO_TORQUE_REPLY", summaries)
                if register == "Present_Temperature" and not 0 <= value < 45:
                    raise DiagnosisStop("온도45도 이상 또는 잘못된 값; 추가 통신 중단", "TEMPERATURE_STOP", summaries)
        if index + 1 < rounds:
            time.sleep(0.25)
    return summaries


def known_home():
    path = Path.home() / "home_state_so101.json"
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != HOME_HASH:
        raise RuntimeError("검토한 home 파일과 다름; 장치 미연결")
    home = json.loads(data)
    if home.get("joint_names") != list(JOINTS) or len(home.get("state", [])) != 6 or home.get("tolerance_deg") != 5:
        raise RuntimeError("home 근거 형식 불일치")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in home["state"]):
        raise RuntimeError("home 근거 수치 오류")
    return home


def position_goal_review(bus, replies, emit, home):
    rows = []
    deadline = time.monotonic() + 8
    def read(register, joint):
        if time.monotonic() >= deadline:
            raise DiagnosisStop("위치 검토 시간 상한", "TIME_LIMIT", rows)
        replies.begin(register)
        try:
            values = bus.sync_read(register, motors=[joint], normalize=False, num_retry=0)
        except Exception as error:
            raise DiagnosisStop(str(error), "RESPONSE_REJECTED", getattr(error, "observed", None)) from error
        if not isinstance(values, dict) or set(values) != {joint} or type(values[joint]) is not int:
            raise DiagnosisStop("위치 검토 응답 형식 불일치", "DECODE_MISMATCH", rows)
        value = values[joint]
        emit(dict(stage="position_goal_review", register=register, joint=joint, value=value,
                  reply=replies.history[-1] if replies.history else None))
        if register == "Torque_Enable" and value != 0:
            raise DiagnosisStop("토크OFF가 아님; 추가 읽기 중단", "NONZERO_TORQUE_REPLY", rows)
        if register == "Present_Temperature" and not 0 <= value < 45:
            raise DiagnosisStop("온도 상한; 추가 읽기 중단", "TEMPERATURE_STOP", rows)
        if register == "Operating_Mode" and value != 0:
            raise DiagnosisStop("위치 모드가 아님; 추가 읽기 중단", "MODE_STOP", rows)
        return value
    for index, joint in enumerate(JOINTS):
        read("Torque_Enable", joint)
        read("Present_Temperature", joint)
        read("Operating_Mode", joint)
        present = read("Present_Position", joint)
        cal, motor = bus.calibration[joint], bus.motors[joint]
        if not cal.range_min <= present <= cal.range_max:
            raise DiagnosisStop("현재 위치가 보정 범위 밖; 추가 읽기 중단", "POSITION_RANGE_STOP", dict(joint=joint, present_raw=present))
        goal = read("Goal_Position", joint)
        current = float(bus._normalize({motor.id: present})[motor.id])
        if not math.isfinite(current) or (index == 5 and not 0 <= current <= 100):
            raise DiagnosisStop("현재 위치 정규화 오류", "NORMALIZATION_STOP", rows)
        rows.append(dict(joint=joint, present_raw=present, goal_raw=goal,
                         goal_matches_current=goal == present,
                         goal_in_calibration=cal.range_min <= goal <= cal.range_max,
                         current=current, home=home["state"][index],
                         home_delta=current - home["state"][index],
                         unit="percent_0_100" if index == 5 else "degree"))
    return dict(joints=rows, goal_exact_match=all(r["goal_matches_current"] for r in rows),
                home_within_5_degrees=all(abs(r["home_delta"]) <= 5 for r in rows[:5]),
                live_authorized=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--operator-present", action="store_true")
    parser.add_argument("--supported", action="store_true")
    parser.add_argument("--stop-ready", action="store_true")
    parser.add_argument("--inspect-position-goal", action="store_true")
    args = parser.parse_args(argv)
    calibration = check_files()
    home = known_home() if args.inspect_position_goal else None
    if not args.execute:
        print("READONLY_PLAN_READY: 중단 기록 보존 / 장치 연결0 / 레지스터 쓰기0")
        return 0
    if not all((args.operator_present, args.supported, args.stop_ready)):
        raise RuntimeError("현장 입회·안정된 지지·전원 차단 수단 필요; 장치 미연결")
    service_gate()
    port_owners(set())
    print("읽기 전용 진단입니다. 목표·토크·설정은 변경하지 않습니다.", flush=True)
    print("팔을 안정적으로 받치고 즉시 전원 차단 수단을 준비하세요. follower DC 전원이 이미 켜져 있으면 그대로 두고, 꺼져 있을 때만 켜세요.", flush=True)
    if input("전원ON 후 READ_ONLY 입력(중단은Ctrl+C): ").strip() != "READ_ONLY":
        raise RuntimeError("사용자 확인 불일치; 장치 미연결")
    check_files()
    service_gate()
    port_owners(set())
    folder = Path(__file__).resolve().parent.parent / "readonly_diagnostics" / str(time.time_ns())
    folder.mkdir(parents=True, exist_ok=False)
    result = dict(status="HOLD", live="NOT_RUN", motor_register_writes=0,
                  field_stop_preserved=False, summaries=[], cleanup_errors=[])
    bus = permission = strict = None
    serial = None
    try:
        from first_trial_dependencies import prepare
        prepare()
        from live_safety_runtime import PacketPermission
        from reply_integrity import StrictSyncReplies
        from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
        class ReadPermission(PacketPermission):
            READS = {40: 1, 63: 1, **({33: 1, 56: 2, 42: 2} if args.inspect_position_goal else {})}
        robot = SO101Follower(SO101FollowerConfig(port=PORT, id="so-101", calibration_dir=calibration.parent,
                              cameras={}, use_degrees=True, disable_torque_on_disconnect=False))
        bus = robot.bus
        for motor in bus.motors.values():
            contracts = [("Torque_Enable", (40, 1)), ("Present_Temperature", (63, 1))]
            if args.inspect_position_goal:
                contracts += [("Operating_Mode", (33, 1)), ("Present_Position", (56, 2)), ("Goal_Position", (42, 2))]
            for name, expected in contracts:
                if tuple(bus.model_ctrl_table[motor.model][name]) != expected:
                    raise RuntimeError("레지스터 계약 불일치")
        permission = ReadPermission(bus.port_handler, allow_goal=False).install()
        strict = StrictSyncReplies(bus.sync_reader, bus.packet_handler).install()
        bus.connect(handshake=False)
        serial = bus.port_handler.ser
        import fcntl, termios
        fcntl.ioctl(serial.fileno(), termios.TIOCEXCL)
        port_owners({os.getpid()})
        with (folder / "responses.jsonl").open("x", encoding="utf-8") as stream:
            def emit(row):
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
            result["summaries"] = probe(bus, strict, emit)
            if args.inspect_position_goal:
                result["position_goal_review"] = position_goal_review(bus, strict, emit, home)
        result["status"] = "READONLY_POSITION_GOAL_REVIEW" if args.inspect_position_goal else "NO_MISMATCH_WITHOUT_WRITES"
    except BaseException as error:
        result["error"] = dict(kind=type(error).__name__, code=getattr(error, "code", "SETUP_OR_USER_STOP"),
                               reason=str(error).replace(str(Path.home()), "~"), observed=getattr(error, "observed", None))
    finally:
        if permission is not None:
            permission.seal()
            result["read_packets"], result["blocked_packets"] = permission.read_packets, permission.blocked_packets
            result["motor_register_writes"] = permission.goal_packets
        if serial is None and bus is not None:
            serial = getattr(bus.port_handler, "ser", None)
        if bus is not None and bus.is_connected:
            try:
                bus.disconnect(disable_torque=False)
            except BaseException as error:
                result["cleanup_errors"].append(type(error).__name__)
        if serial is not None and getattr(serial, "is_open", False):
            try:
                serial.close()
                bus.port_handler.is_open = False
            except BaseException as error: result["cleanup_errors"].append(type(error).__name__)
        result["port_closed"] = serial is None or not getattr(serial, "is_open", True)
        if result["port_closed"]:
            if strict is not None: strict.restore()
            if permission is not None: permission.restore()
        if strict is not None:
            result["replies"] = strict.history
            result["reply_fault"] = strict.fault
        try:
            check_files()
            result["field_stop_preserved"] = True
            port_owners(set())
        except BaseException as error:
            result["cleanup_errors"].append(type(error).__name__)
        if result["cleanup_errors"] or not result["port_closed"]:
            result["status"] = "HOLD"
        with (folder / "RESULT.json").open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        summary = {k: result[k] for k in ("status", "live", "motor_register_writes", "port_closed", "field_stop_preserved", "cleanup_errors")}
        if "error" in result: summary["error"] = result["error"]
        if "position_goal_review" in result: summary["position_goal_review"] = result["position_goal_review"]
        summary["report"] = str(folder).replace(str(Path.home()), "~")
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if result["status"] in ("NO_MISMATCH_WITHOUT_WRITES", "READONLY_POSITION_GOAL_REVIEW") else 2


if __name__ == "__main__":
    raise SystemExit(main())
