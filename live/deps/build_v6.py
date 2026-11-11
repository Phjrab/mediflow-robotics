"""검증한 v4에서 새 파일만 생성. 원본/기존 v6 덮어쓰기 없음."""
import ast
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = ROOT / "source_v4.py"
if hashlib.sha256(source.read_bytes()).hexdigest() != "af72d76b992c237211c576796219694503986609277da9b877913b550df6ec56":
    raise SystemExit("원본 v4 해시 불일치")
text = source.read_text(encoding="utf-8")
def replace(old, new):
    global text
    if text.count(old) != 1:
        raise RuntimeError("수정 기준이 유일하지 않음: " + old[:65])
    text = text.replace(old, new)

replace('import traceback\n', '''import traceback
from live_safety_runtime import (Hold, PacketPermission, check_stop_record,
                                 monitored_inference, close_without_writes)
''')
replace('default=10.0)\n    parser.add_argument("--step-cap"', 'default=5.0)\n    parser.add_argument("--step-cap"')
replace('color_mode="rgb", warmup_s=1, backend=200)', 'color_mode="rgb", warmup_s=3, backend=200)')
# 기존 파일의 현재 위치 HOLD 함수와 종료 함수를 제거한다.
tree = ast.parse(text)
lines = text.splitlines(keepends=True)
targets = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("hold_measured_position", "close_session")]
for node in sorted(targets, key=lambda n: n.lineno, reverse=True):
    replacement = []
    if node.name == "close_session":
        replacement = ['''def close_session(robot, cameras, live, attempted, connected):
    errors = close_without_writes(robot, cameras, connected, getattr(robot, "_v6_permission", None))
    print("종료: 추가 Goal/토크 쓰기 없음. 기존 목표/토크 유지이며 즉시 물리 정지 보장은 아님.")
    for error in errors:
        print(error, file=sys.stderr)

''']
    lines[node.lineno-1:node.end_lineno] = replacement
text = ''.join(lines)
replace('''    robot.bus.connect(handshake=False)
    try:
''', '''    try:
        robot.bus.connect(handshake=False)
        import fcntl, termios
        fcntl.ioctl(robot.bus.port_handler.ser.fileno(), termios.TIOCEXCL)
''')
replace('''    args = parse_args()
''', '''    args = parse_args()
    # 기존 무인 중단 기록을 자동 해제/무시하지 않는다. 모델/장치 import보다 먼저 거부.
    check_stop_record()
    if args.max_seconds > 5 or args.fps != 30 or args.step_cap > 0.5 or args.max_deg_per_second > 20 or args.first_action_limit > 10 or args.load_limit > 200 or args.temperature_every > 5:
        raise SystemExit("v6 첫 시험 기준 완화 거부: 5초/30Hz/0.5도/20도초/첫10도/부하200/간격5")
''')
replace('''    home = json.loads(args.home_state.expanduser().read_text()) if args.home_state else None
''', '''    home = json.loads(args.home_state.expanduser().read_text()) if args.home_state else None
    if args.live and (not home or isinstance(home.get("tolerance_deg"), bool) or not isinstance(home.get("tolerance_deg"), (float, int)) or not 0 < home["tolerance_deg"] <= 5):
        raise Hold("HOME 허용범위 5도 조건 미확인/완화 거부")
''')
replace('''    frame_watch = FrameWatch()
''', '''    frame_watch = FrameWatch()
    from collections import deque
    recent_clips = deque(maxlen=20)
    # 정책 스레드에는 robot/bus를 전달하지 않는다. 패킷 보호는 소유 스레드만 허용.
    permission = PacketPermission(robot.bus.port_handler, allow_goal=args.live).install()
    robot._v6_permission = permission
    def monitor():
        check_stop_record()
        torque, modes = read_motor_state(robot.bus)
        motor_mode_gate(torque, modes, args.live)
        read_health(robot.bus, load_limit=args.load_limit)
''')
replace('''        print("관절 버스 연결 및 보정 읽기 완료", flush=True)
''', '''        print("관절 버스 연결 및 보정 읽기 완료", flush=True)
        torque, modes = read_motor_state(robot.bus)
        motor_mode_gate(torque, modes, args.live)
        read_health(robot.bus, starting=True, load_limit=args.load_limit)
''')
replace('''            if input("확인 문자열: ").strip() != "LIVE":
''', '''            confirmation = monitored_inference(lambda: input("확인 문자열: "), monitor,
                deadline=time.monotonic() + 60, timeout=60)
            if confirmation.strip() != "LIVE":
''')
replace('''            torch.cuda.synchronize()
            infer_started = time.perf_counter()
            state, actions, _ = calculate_only(
                robot, lambda r: observed,
                lambda obs: infer_chunk(policy, preprocess, postprocess, dict(obs, task=[args.task]), torch),
            )
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            infer_seconds = time.perf_counter() - infer_started
''', '''            state, packed, _ = observed
            # GPU 동기화도 정책 전용 스레드 안에서 처리하고 메인은 serial 감시를 계속한다.
            def predict_only():
                torch.cuda.synchronize()
                result = infer_chunk(policy, preprocess, postprocess, dict(packed, task=[args.task]), torch)
                torch.cuda.synchronize()
                return result
            infer_started = time.perf_counter()
            remaining = args.max_seconds - (time.perf_counter() - run_started)
            actions = monitored_inference(predict_only, monitor,
                deadline=time.monotonic() + max(0, remaining), timeout=50.0 / args.fps)
            infer_seconds = time.perf_counter() - infer_started
''')
replace('''                    target = safe_target(action, measured, previous_goal, args.step_cap, args.fps, args.max_deg_per_second)
                    action_attempted = True
                    step_start = time.perf_counter()
                    sent = robot.send_action({joint + ".pos": target[i] for i, joint in enumerate(JOINTS)})
''', '''                    # 매 전송 직전 토크/모드를 확인. OFF 상태를 Goal 쓰기로 재활성화하지 않음.
                    check_stop_record()
                    torque, modes = read_motor_state(robot.bus)
                    motor_mode_gate(torque, modes, True)
                    if any(abs(action[i] - measured[i]) > 10 or abs(action[i] - previous_goal[i]) > 10 for i in range(6)):
                        raise Hold("큰 점프: clip으로 감추지 않고 중단")
                    target = safe_target(action, measured, previous_goal, args.step_cap, args.fps, args.max_deg_per_second)
                    recent_clips.append(any(abs(target[i] - action[i]) > 1e-6 for i in range(6)))
                    if len(recent_clips) == 20 and sum(recent_clips) / 20 > 0.5:
                        raise Hold("정책-제어 불일치: 누적 clip 비율 초과")
                    if time.perf_counter() - run_started >= args.max_seconds:
                        raise Hold("전송 직전 전체 시간 상한")
                    action_attempted = True
                    step_start = time.perf_counter()
                    with permission.goal_once():
                        sent = robot.send_action({joint + ".pos": target[i] for i, joint in enumerate(JOINTS)})
''')
replace('''        print("Ctrl+C 감지. 마지막 목표 유지 절차로 이동합니다.")''', '''        print("Ctrl+C 감지. 추가 목표/토크 쓰기 없이 포트를 닫습니다.")''')
ast.parse(text, feature_version=(3, 10))
target = ROOT / "so101_first_action_test_safe_v6.py"
with target.open("x", encoding="utf-8", newline="\n") as stream:
    stream.write(text)
print("V6_CREATED", hashlib.sha256(target.read_bytes()).hexdigest())
