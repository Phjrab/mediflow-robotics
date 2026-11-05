"""사용자가 요청한 현장 읽기 진단. 기존 중단 기록을 보존하며 재무장 기능 없음.

토크 하나라도 ON/온도45도 이상이면 추가 serial 읽기도 중단한다.
모든 쓰기 패킷은 차단한다. robot.connect/configure/calibrate 호출 없음.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

from live_safety_runtime import PacketPermission, close_without_writes

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
PORT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00"
CALIBRATION = Path.home() / ".cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json"
CALIBRATION_HASH = "2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e"
STOP = Path.home() / "nov_live_prep_20261104/SERIAL_STOP.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def owners():
    r = subprocess.run(["sudo", "-n", "fuser", "-v", PORT], capture_output=True, text=True, timeout=8)
    return dict(free=r.returncode == 1 and not (r.stdout + r.stderr).strip(),
                code=r.returncode, stdout_present=bool(r.stdout.strip()), stderr_present=bool(r.stderr.strip()))


def read_values(bus, home, result):
    result.update(torque={}, temperatures={}, modes={}, positions={}, home_delta={})
    for name in JOINTS:
        value = bus.read("Torque_Enable", name, normalize=False, num_retry=0)
        result["torque"][name] = value
        if type(value) is not int or value != 0:
            raise RuntimeError("토크ON 또는 미확인: 추가 serial 읽기 중단")
    for name in JOINTS:
        value = bus.read("Present_Temperature", name, normalize=False, num_retry=0)
        result["temperatures"][name] = value
        if type(value) not in (float, int) or not __import__("math").isfinite(value) or not 0 <= value < 45:
            raise RuntimeError("온도45도 이상/미확인: 추가 serial 읽기 중단")
    for index, name in enumerate(JOINTS):
        mode = bus.read("Operating_Mode", name, normalize=False, num_retry=0)
        result["modes"][name] = mode
        if type(mode) is not int or mode != 0:
            raise RuntimeError("위치 모드0 아님: 추가 serial 읽기 중단")
        raw = bus.read("Present_Position", name, normalize=False, num_retry=0)
        motor_id = bus.motors[name].id
        value = float(bus._normalize({motor_id: raw})[motor_id])
        if not __import__("math").isfinite(value) or (index == 5 and not 0 <= value <= 100):
            raise RuntimeError("현재 위치/그리퍼 수치 오류")
        result["positions"][name] = value
        if home:
            result["home_delta"][name] = value - home["state"][index]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    out = args.out.expanduser().resolve()
    if out.parent != Path(__file__).resolve().parent or out.exists() or out.name == "SERIAL_STOP.json":
        raise RuntimeError("출력은 이 도구 폴더의 새 JSON만 허용")
    stop_before = digest(STOP)
    result = dict(start_unix=time.time(), diagnostic_only=True, resumption_authorized=False,
                  stop_record_present=STOP.exists(), stop_sha256_before=stop_before,
                  register_writes=0, cameras_opened=0, status="BLOCKED")
    robot = permission = None
    try:
        service = subprocess.run(["systemctl", "--user", "is-active", "lelab-jetson-py310.service"],
                                 capture_output=True, text=True, timeout=8)
        result["lelab_state"] = service.stdout.strip()
        if service.returncode != 3 or result["lelab_state"] != "inactive":
            raise RuntimeError("LeLab inactive 확인 실패; 장치 미연결")
        result["owners_before"] = owners()
        if not result["owners_before"]["free"]:
            raise RuntimeError("포트 점유/점유 조회 오류; 장치 미연결")
        if digest(CALIBRATION) != CALIBRATION_HASH:
            raise RuntimeError("현재 보정 해시 불일치; 장치 미연결")
        home_file = Path.home() / "home_state_so101.json"
        home = json.loads(home_file.read_text()) if home_file.is_file() else None
        if home and (home.get("joint_names") != list(JOINTS) or len(home.get("state", [])) != 6 or home.get("tolerance_deg") != 5
                     or any(type(v) not in (int, float) or not math.isfinite(v) for v in home["state"])
                     or not 0 <= home["state"][5] <= 100):
            raise RuntimeError("home 형식 오류; 장치 미연결")
        from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
        robot = SO101Follower(SO101FollowerConfig(port=PORT, id="so-101", calibration_dir=CALIBRATION.parent,
                              cameras={}, use_degrees=True, disable_torque_on_disconnect=False))
        permission = PacketPermission(robot.bus.port_handler).install()
        robot.bus.connect(handshake=False)
        import fcntl, termios
        fcntl.ioctl(robot.bus.port_handler.ser.fileno(), termios.TIOCEXCL)
        read_values(robot.bus, home, result)
        if not robot.is_calibrated:
            raise RuntimeError("파일/모터 보정 레지스터 불일치")
        result["motor_calibration_matches"] = True
        result["home_body_within_5deg"] = all(abs(result["home_delta"].get(j, float("inf"))) <= 5 for j in JOINTS[:5])
        result["status"] = "READ_ONLY_STATUS_COMPLETE_NOT_LIVE_READY"
    except BaseException as error:
        result["error"] = dict(kind=type(error).__name__, reason=str(error).replace(str(Path.home()), "~"))
    finally:
        if robot is not None:
            result["cleanup_errors"] = close_without_writes(robot, [], True, permission)
        if permission is not None:
            result.update(read_packets=permission.read_packets, goal_packets=permission.goal_packets,
                          blocked_packets=permission.blocked_packets)
        result["stop_sha256_after"] = digest(STOP)
        result["stop_record_unchanged"] = result["stop_sha256_after"] == stop_before
        try:
            result["owners_after"] = owners()
        except Exception as error:
            result["owners_after"] = dict(free=False, error=type(error).__name__)
        result["end_unix"] = time.time()
        with out.open("x", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "READ_ONLY_STATUS_COMPLETE_NOT_LIVE_READY" and result["owners_after"]["free"] and result["stop_record_unchanged"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
