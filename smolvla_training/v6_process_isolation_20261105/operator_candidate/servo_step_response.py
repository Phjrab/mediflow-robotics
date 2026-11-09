"""Model-free, single-axis SO-101 diagnostic. Default mode never opens a device."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import threading
import time

from data_motion import Axis, ELBOW_TRAVEL
from first_trial_core import JOINTS, Hold, Limits, home_gate, sensor_gate
from hold_continuation import CAL_SHA, HOME_SHA, STOP_SHA


AXES = ("elbow_flex", "shoulder_lift")
SEQUENCE = (3, 0, -3, 0, 6, 0, -6, 0)
WORK_SEQUENCE = (-3, 0, -6, 0)
WORK_DEGREES = 89.2
TRANSFER_SLEW = 8.
RATE = 60
MAX_SAMPLE_GAP = .020
HOLD_SECONDS = 2.
SLEW = 12.
EXPECTED_STOP = "ece37e900296f1ebea9b873296e602114c41b554c6713a1b0bc6dd6443137f0d"
READS = {9: 2, 11: 2, 28: 2, 31: 2, 33: 8, 56: 15, 67: 2}
REGISTERS = {"Min_Position_Limit": (9, 2), "Max_Position_Limit": (11, 2),
             "Protection_Current": (28, 2), "Homing_Offset": (31, 2),
             "Operating_Mode": (33, 1), "Torque_Enable": (40, 1),
             "Goal_Position": (42, 2), "Present_Position": (56, 2),
             "Present_Load": (60, 2), "Present_Temperature": (63, 1),
             "Status": (65, 1), "Present_Current": (69, 2)}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def response_bytes(packet, comm, motor_id, size):
    if type(comm) is not int or comm != 0 or not isinstance(packet, (list, tuple, bytes, bytearray)):
        raise Hold("계측 응답 없음 또는 통신 오류")
    raw = bytes(packet)
    if (len(raw) != size+6 or raw[:2] != b"\xff\xff" or raw[2] != motor_id or
        raw[3] != size+2 or sum(raw[2:]) % 256 != 255 or raw[4] != 0):
        raise Hold("계측 응답 ID·길이·체크섬·상태 이상")
    return raw[5:-1]


def single_goal(packet, motor, raw):
    if type(raw) is not int or not 0 <= raw <= 4095:
        return False
    data = bytes(packet)
    prefix = bytes((255, 255, 254, 7, 131, 42, 2, motor, raw & 255, raw >> 8))
    return len(data) == 11 and data[:-1] == prefix and sum(data[2:]) % 256 == 255


def bounded_goal(axis, previous, destination, seconds, bounds, *, speed=SLEW):
    allowed = min(2., speed * seconds)
    low, high = max(bounds[0], previous-allowed), min(bounds[1], previous+allowed)
    if low > high:
        raise Hold("계측 정수 목표 범위 없음")
    return axis.encode(min(high, max(low, destination)), low, high)


def phase_metrics(rows, offset, start_raw, end_raw, axis, target_reached):
    if not rows or not target_reached:
        return dict(offset_deg=offset, status="INCOMPLETE")
    baseline = axis.decode(start_raw)
    target = axis.decode(end_raw)
    requested = target-baseline
    if offset == 0 or abs(requested) < abs(axis.scale):
        return dict(offset_deg=offset, status="RETURN_BASELINE")
    direction = 1 if requested > 0 else -1
    t0 = rows[0]["seconds"]
    signed = lambda row: direction * (row["position_deg"]-baseline)
    threshold = .63 * abs(requested)
    crossing = None
    prev = None
    for row in rows:
        if signed(row) >= threshold:
            if prev is None:
                crossing = row["seconds"]-t0
            else:
                fraction = (threshold-signed(prev))/(signed(row)-signed(prev))
                crossing = prev["seconds"] + fraction*(row["seconds"]-prev["seconds"])-t0
            break
        prev = row
    final = statistics.median([row["position_ticks"] for row in rows
                               if row["seconds"] >= rows[-1]["seconds"]-.25])
    final_deg = axis.decode(int(final)) + (final-int(final))*axis.scale
    onset = next((i for i, row in enumerate(rows)
                  if (row["position_ticks"]-start_raw) * direction * (1 if axis.scale > 0 else -1) >= 2), None)
    return dict(offset_deg=offset, direction="positive" if direction > 0 else "negative", status="MEASURED",
                start_ticks=start_raw, transmitted_target_ticks=end_raw, target_deg=target,
                load_max_abs_raw=max(abs(row["load"]) for row in rows),
                load_min_raw=min(row["load"] for row in rows), load_max_raw=max(row["load"] for row in rows),
                current_max_abs_raw=max(abs(row["current"]) for row in rows),
                temperature_max_c=max(row["temperature_c"] for row in rows),
                steady_error_ticks=end_raw-final, steady_error_deg=target-final_deg,
                time_to_63_seconds=crossing, final_reach_ratio=(final_deg-baseline)/requested,
                deadzone_estimate_ticks=None if onset is None or onset == 0 else
                    abs(rows[onset-1]["goal_ticks"]-rows[onset-1]["position_ticks"]),
                movement_onset_observed=onset is not None)


class Cadence:
    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.next = clock()
        self.last = None
        self.gaps = []

    def sample(self, read):
        self.sleep(max(0., self.next-self.clock()))
        row = read()
        now = self.clock()
        if self.last is not None:
            gap = now-self.last
            self.gaps.append(gap)
            if not 0 < gap <= MAX_SAMPLE_GAP:
                raise Hold(f"50Hz 계측 기준 미달: 표본 간격 {gap:.4f}초")
        self.last = now
        self.next = max(self.next+1/RATE, now)
        return now, row


def make_permission(bus, joint, allow_goal):
    from live_safety_runtime import PacketPermission
    motor = bus.motors[joint].id

    class DiagnosticPermission(PacketPermission):
        READS = globals()["READS"]
        expected = None
        guard_until = None

        def inspect(self, port, packet):
            if len(packet) > 4 and packet[4] == 0x83:
                if (self.closed or threading.get_ident() != self.owner or port is not self.port
                    or not self.allow_goal or self.phase != "goal" or self._allowance != 1
                    or not single_goal(packet, motor, self.expected) or time.monotonic() >= self.guard_until):
                    self.blocked_packets += 1
                    raise Hold("계측 단일축 목표 패킷 또는 최신성 위반")
                self._allowance = 0
                return "goal"
            return super().inspect(port, packet)

    return DiagnosticPermission(bus.port_handler, allow_goal=allow_goal).install()


def block(bus, address, width, motors):
    ids = [bus.motors[j].id for j in motors]
    data = {}
    try:
        if READS.get(address) != width or bus.packet_handler.syncReadTx(bus.port_handler, address, width, ids, len(ids)) != 0:
            raise Hold("일괄 읽기 송신 실패")
        for name, motor in zip(motors, ids):
            packet, comm = bus.packet_handler.rxPacket(bus.port_handler)
            data[name] = response_bytes(packet, comm, motor, width)
    finally:
        bus.port_handler.is_using = False
    return data


def sample_bus(bus, axes, protection, starting=False):
    mode = block(bus, 33, 8, JOINTS)
    feedback = block(bus, 56, 15, JOINTS)
    def number(payload, offset, width=1):
        return int.from_bytes(payload[offset:offset+width], "little")
    def signed(register, name, offset):
        mid = bus.motors[name].id
        return bus._decode_sign(register, {mid:number(feedback[name],offset,2)})[mid]
    ticks = {j:signed("Present_Position",j,0) for j in JOINTS}
    row = dict(ticks=ticks, state=[axes[j].decode(ticks[j]) for j in JOINTS],
               torque={j:mode[j][7] for j in JOINTS}, modes={j:mode[j][0] for j in JOINTS},
               loads={j:signed("Present_Load",j,4) for j in JOINTS},
               currents={j:signed("Present_Current",j,13) for j in JOINTS},
               temperatures={j:feedback[j][7] for j in JOINTS}, statuses={j:feedback[j][9] for j in JOINTS})
    sensor_gate(row, Limits(), starting=starting)
    if any(abs(row["currents"][j]) >= protection[j] for j in JOINTS):
        raise Hold("전류 보호 상한")
    if abs(row["state"][JOINTS.index("elbow_flex")]) > ELBOW_TRAVEL:
        raise Hold("팔꿈치 가동범위 이탈")
    return row


def inspect_limits(bus):
    from individual_reply_guard import StrictIndividualReplies
    from data_motion_evidence import SERVO_SHA
    with StrictIndividualReplies(bus.packet_handler):
        if not bus.is_calibrated:
            raise Hold("파일과 서보 보정 불일치")
        protection = {j:bus.read("Protection_Current", j, normalize=False, num_retry=0) for j in JOINTS}
    report = Path.home()/"servo_readonly_1794202279784287777/RESULT.json"
    if digest(report) != SERVO_SHA or json.loads(report.read_bytes())["values"]["Protection_Current"] != protection:
        raise Hold("검토한 전류 한계와 일치하지 않음")
    axes = {}
    for j in JOINTS:
        cal, mid = bus.calibration[j], bus.motors[j].id
        axes[j] = Axis(cal.range_min, cal.range_max,
                       bus._normalize({mid:cal.range_min})[mid],bus._normalize({mid:cal.range_max})[mid])
    return axes, protection


def verify_stops(root):
    expected = {"FIELD_STOP.json":STOP_SHA, "POST_DATA_LIVE_50_STOP.json":EXPECTED_STOP}
    for name, sha in expected.items():
        if digest(root/name) != sha:
            raise Hold("기존 현장 중단 기록 불일치")


def transmit(bus, guard, joint, raw, now):
    guard.expected, guard.guard_until = raw, now+MAX_SAMPLE_GAP
    try:
        with guard.goal_once():
            bus.sync_write("Goal_Position",{joint:raw},normalize=False,num_retry=0)
    finally:
        guard.expected, guard.guard_until = None, None


def measurements(snapshot, axis, joint, current_goal, elapsed, phase, offset):
    actual = snapshot["ticks"][joint]
    return dict(phase=phase, offset_deg=offset, seconds=elapsed,
                goal_ticks=current_goal, goal_deg=axis.decode(current_goal),
                position_ticks=actual, position_deg=axis.decode(actual),
                load=snapshot["loads"][joint], current=snapshot["currents"][joint],
                temperature_c=snapshot["temperatures"][joint], sent_ticks=None, sent_goal_deg=None)


def active_goals(bus):
    return {j:int.from_bytes(data,"little") for j,data in block(bus,67,2,JOINTS).items()}


def ensure_other_axes(snapshot, reference, idx):
    if any(abs(value-reference[i]) > (1. if i<5 else 2.)
           for i,value in enumerate(snapshot["state"]) if i != idx):
        raise Hold("비대상 관절 이동 감지")


def run_motion(bus, guard, cadence, axes, joint, read, origin, rows, metrics,
               *, site="A", other_reference=None):
    axis = axes[joint]
    idx = JOINTS.index(joint)
    now, initial = cadence.sample(read)
    if origin[0] is None:
        origin[0] = now
    start = now
    baseline_raw = initial["ticks"][joint]
    baseline = axis.decode(baseline_raw)
    lower, upper = axis.bounds
    fraction = (baseline-lower)/(upper-lower)
    if site=="A" and not .4 <= fraction <= .6:
        raise Hold(f"중앙 40–60% 밖: {joint} {fraction:.1%}")
    if site=="B" and (joint!="elbow_flex" or not 88 <= baseline <= 90):
        raise Hold("작업 구간 B 팔꿈치 88–90° 밖")
    sequence = SEQUENCE if site=="A" else WORK_SEQUENCE
    bounds = (max(lower,-ELBOW_TRAVEL),min(upper,ELBOW_TRAVEL)) if joint=="elbow_flex" else (lower,upper)
    goals = [axis.encode(baseline+offset,*bounds) for offset in sequence]
    if not all(bounds[0] <= baseline+offset <= bounds[1] for offset in sequence):
        raise Hold("시험 전체 경로가 가동범위를 벗어남")
    active_raw = active_goals(bus)
    if any(abs(axes[j].decode(active_raw[j])-initial["state"][i]) > (0.5 if i<5 else 1)
           for i,j in enumerate(JOINTS)):
        raise Hold("시작 활성 목표와 현재 위치 불일치")
    if other_reference is None:
        other_reference = list(initial["state"])
    previous_raw = active_raw[joint]
    last_sent = now
    for phase, (offset, goal) in enumerate(zip(sequence, goals)):
        phase_rows = []
        phase_start, phase_initial = None, None
        reached = None
        while True:
            now, snapshot = cadence.sample(read)
            if now-start > 25:
                raise Hold("단일 관절 시험 시간 상한")
            ensure_other_axes(snapshot, other_reference, idx)
            actual = snapshot["ticks"][joint]
            if phase_start is None:
                phase_start, phase_initial = now, actual
            if abs(axis.decode(previous_raw)-axis.decode(actual)) > 8.:
                raise Hold("계측 목표 추종 차이 상한")
            row = measurements(snapshot,axis,joint,previous_raw,now-origin[0],phase+1,offset)
            phase_rows.append(row)
            rows.append(row)
            if previous_raw != goal:
                proposed = bounded_goal(axis,axis.decode(previous_raw),axis.decode(goal),
                                        min(now-last_sent,1/RATE),bounds)
                if proposed != previous_raw:
                    transmit(bus,guard,joint,proposed,now)
                    previous_raw, last_sent = proposed, cadence.clock()
                    row.update(sent_ticks=proposed, sent_goal_deg=axis.decode(proposed))
            if previous_raw == goal and reached is None:
                reached = max(now,last_sent)
            if reached is not None and now-reached >= HOLD_SECONDS:
                break
            if reached is None and now-phase_start > 2:
                raise Hold("저속 램프가 목표에 도달하지 못함")
        metrics.append(phase_metrics(phase_rows,offset,phase_initial,goal,axis,True))


def transfer(bus, guard, cadence, axes, joint, read, destination, reference, origin, rows, phase):
    axis = axes[joint]
    idx = JOINTS.index(joint)
    lo, hi = axis.bounds
    if joint=="elbow_flex":
        lo,hi=max(lo,-ELBOW_TRAVEL),min(hi,ELBOW_TRAVEL)
    target = axis.decode(destination)
    if not lo <= target <= hi:
        raise Hold("이동 목표가 보정 한계 밖")
    previous = active_goals(bus)[joint]
    start = last_send = cadence.clock()
    stable_since = None
    progressed_at = start
    progress_reference = None
    while True:
        now, snapshot = cadence.sample(read)
        ensure_other_axes(snapshot,reference,idx)
        actual = snapshot["state"][idx]
        remaining = abs(target-actual)
        if progress_reference is None or remaining < progress_reference-.2:
            progress_reference,progressed_at=remaining,now
        if now-start>32 or (remaining>.45 and now-progressed_at>2):
            raise Hold("자세 이동 시간 또는 추종 정체 상한")
        current_goal = axis.decode(previous)
        if abs(current_goal-actual)>2:
            raise Hold("자세 이동 중 현재 위치와 활성 목표 차이 2° 초과")
        row = measurements(snapshot,axis,joint,previous,now-origin,phase,None)
        rows.append(row)
        if previous != destination:
            candidate=bounded_goal(axis,current_goal,target,min(now-last_send,1/RATE),(lo,hi),speed=TRANSFER_SLEW)
            if candidate!=previous and abs(axis.decode(candidate)-actual)<=2:
                transmit(bus,guard,joint,candidate,now)
                previous,last_send=candidate,cadence.clock()
                row.update(sent_ticks=candidate,sent_goal_deg=axis.decode(candidate))
        elif remaining<=.45:
            if stable_since is None:
                stable_since=now
            if now-stable_since>=.3:
                return
        else:
            stable_since=None


def home_load_record(samples, active, joint):
    values=[s["loads"][joint] for s in samples]
    current=[s["currents"][joint] for s in samples]
    positions=[s["ticks"][joint] for s in samples]
    return dict(samples=len(samples),position_ticks_min=min(positions),position_ticks_max=max(positions),
                active_goal_ticks=active[joint],load_median_raw=statistics.median(values),
                load_min_raw=min(values),load_max_raw=max(values),current_max_abs_raw=max(map(abs,current)),
                physical_direction="UNVERIFIED_NO_CONTACT_OR_GRAVITY_REFERENCE")


def run_site(bus, guard, cadence, axes, joint, read, home, site, rows, metrics, report):
    active=active_goals(bus)
    now,snapshot=cadence.sample(read)
    home_gate(snapshot["state"],home)
    reference=snapshot["state"]
    if any(abs(axes[j].decode(active[j])-reference[i]) > (.5 if i<5 else 1)
           for i,j in enumerate(JOINTS)):
        raise Hold("home에서 현재 위치와 활성 목표 불일치; 토크·목표 변경 없음")
    baseline=snapshot["ticks"][joint]
    report["home_before_motion"]=home_load_record([snapshot],active,joint)
    axis=axes[joint]
    destination=axis.encode(WORK_DEGREES if site=="B" else sum(axis.bounds)/2,*axis.bounds)
    report["start_route"]=dict(home_ticks=baseline,site_ticks=destination,site_degrees=axis.decode(destination),
                               transfer_speed_deg_s=TRANSFER_SLEW)
    transfer(bus,guard,cadence,axes,joint,read,destination,reference,now,rows,"from_home")
    run_motion(bus,guard,cadence,axes,joint,read,[now],rows,metrics,site=site,other_reference=reference)
    transfer(bus,guard,cadence,axes,joint,read,baseline,reference,now,rows,"to_home")
    report["returned_to_home"]=True


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--joint",choices=AXES,default=AXES[0])
    p.add_argument("--site",choices=("A","B"),default="B")
    mode=p.add_mutually_exclusive_group()
    mode.add_argument("--read-rate",action="store_true")
    mode.add_argument("--motion",action="store_true")
    p.add_argument("--operator-present",action="store_true")
    p.add_argument("--clear-path",action="store_true")
    p.add_argument("--stop-ready",action="store_true")
    p.add_argument("--rate-receipt",type=Path)
    p.add_argument("--work-receipt",type=Path)
    p.add_argument("--out",type=Path)
    args=p.parse_args(argv)
    if args.joint != "elbow_flex":
        raise Hold("현재 현장 승인 범위는 팔꿈치만")
    if not (args.read_rate or args.motion):
        print(json.dumps(dict(status="PLAN_ONLY",joint=args.joint,site=args.site,
                              offsets_deg=SEQUENCE if args.site=="A" else WORK_SEQUENCE,hold_seconds=HOLD_SECONDS,
                              goal_slew_deg_s=SLEW,minimum_hz=50,hardware_access=False)))
        return 0
    if not (args.operator_present and args.clear_path and args.stop_ready and args.out and sys.stdin.isatty()):
        raise Hold("현장 직접 확인과 신규 출력 폴더 필요; 장치 미연결")
    out = args.out.expanduser()
    if out.exists():
        raise Hold("출력 폴더가 이미 있음")
    root=Path(__file__).resolve().parent
    verify_stops(root)
    source_sha=digest(Path(__file__))
    claim=root/f"SERVO_STEP_{args.joint}_{args.site}_CLAIM.json"
    home_path=Path.home()/"home_state_so101.json"
    if digest(home_path)!=HOME_SHA:
        raise Hold("학습 시작 home 파일 해시 불일치")
    home=json.loads(home_path.read_bytes())
    if args.motion:
        if args.rate_receipt is None:
            raise Hold("먼저 쓰기 0의 현장 50Hz 읽기 계측 필요")
        rate=json.loads(args.rate_receipt.expanduser().read_bytes())
        gap=rate.get("read_max_gap_seconds")
        if (rate.get("status")!="READ_RATE_PASS" or rate.get("joint")!=args.joint
            or rate.get("source_sha256")!=source_sha or rate.get("goal_packets")!=0
            or rate.get("port_closed") is not True or type(gap) not in (int,float) or not 0 < gap <= MAX_SAMPLE_GAP):
            raise Hold("50Hz 읽기 결과가 이번 코드·관절과 일치하지 않음")
        if claim.exists() or (root/"SERVO_STEP_STOP.json").exists():
            raise Hold("이전 계측 사용/중단 기록 검토 필요; 재실행 금지")
        if args.site=="A":
            if args.work_receipt is None:
                raise Hold("중앙으로 큰 이동하기 전에 B 완료 기록과 home 복귀 필요")
            work=json.loads(args.work_receipt.expanduser().read_bytes())
            if (work.get("status")!="SERVO_STEP_COMPLETE" or work.get("site")!="B"
                or work.get("joint")!=args.joint or work.get("source_sha256")!=source_sha
                or work.get("port_closed") is not True or work.get("returned_to_home") is not True):
                raise Hold("B 완료·home 복귀 미확인")
    from first_trial_dependencies import prepare
    prepare()
    import device_preflight as devices
    import probe_current_state as probe
    from operator_arm import confirm
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    import fcntl
    import termios
    calibration=Path.home()/".cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json"
    if digest(calibration) != CAL_SHA:
        raise Hold("보정 해시 불일치")
    robot=SO101Follower(SO101FollowerConfig(port=probe.PORT,id="so-101",calibration_dir=calibration.parent,
                         cameras={},use_degrees=True,disable_torque_on_disconnect=False))
    bus=robot.bus
    if [bus.motors[j].id for j in JOINTS] != list(range(1,7)) or bus.protocol_version != 0:
        raise Hold("계측 모터 ID 또는 프로토콜 불일치")
    table=bus.model_ctrl_table["sts3215"]
    if any(bus.motors[j].model!="sts3215" for j in JOINTS) or any(tuple(table.get(k,()))!=v for k,v in REGISTERS.items()):
        raise Hold("설치 SDK 레지스터 범위 불일치")
    devices.require()
    confirm(f"SERVO_STEP_{args.site}" if args.motion else "READ_RATE",
            "home에서 지정 위치까지 저속으로 이동하고 팔꿈치만 시험합니다. 전체 이동 경로와 정지 수단을 확인하세요."
            if args.motion else "home 위치·부하 및 표본 주기를 읽습니다. 모터 목표·설정 쓰기 없음.")
    devices.require()
    verify_stops(root)
    out.mkdir(parents=True,exist_ok=False)
    if args.motion:
        with claim.open("x",encoding="utf-8") as stream:
            json.dump(dict(joint=args.joint,site=args.site,unix=time.time(),source_sha256=source_sha,output=out.name),stream)
    guard=make_permission(bus,args.joint,args.motion)
    rows, metrics=[], []
    report=dict(status="HOLD",joint=args.joint,site=args.site,configuration_register_writes=0,torque_register_writes=0,
                source_sha256=source_sha)
    cadence=None
    try:
        bus.connect(handshake=False)
        fcntl.ioctl(bus.port_handler.ser.fileno(),termios.TIOCEXCL)
        axes,protection=inspect_limits(bus)
        def read():
            return sample_bus(bus,axes,protection)
        before=sample_bus(bus,axes,protection,starting=True)
        home_gate(before["state"],home)
        active=active_goals(bus)
        report["home_readonly"]=home_load_record([before],active,args.joint)
        cadence=Cadence()
        home_samples=[]
        for _ in range(121):
            _, sample=cadence.sample(read)
            home_samples.append(sample)
        report["home_readonly"]=home_load_record(home_samples,active,args.joint)
        if args.motion:
            sample_bus(bus,axes,protection,starting=True)
            verify_stops(root)
            motion_cadence=Cadence()
            try:
                run_site(bus,guard,motion_cadence,axes,args.joint,read,home,args.site,rows,metrics,report)
            finally:
                cadence.gaps.extend(motion_cadence.gaps)
        report["status"]="SERVO_STEP_COMPLETE" if args.motion else "READ_RATE_PASS"
    except (Exception,KeyboardInterrupt) as error:
        report["error"]=str(error) or type(error).__name__
    finally:
        guard.seal()
        serial=getattr(bus.port_handler,"ser",None)
        try:
            if bus.is_connected:
                bus.disconnect(disable_torque=False)
        except Exception as error:
            report.update(status="HOLD",cleanup_error=str(error))
        finally:
            if getattr(serial,"is_open",False):
                try:
                    serial.close()
                except Exception as error:
                    report.update(status="HOLD",cleanup_error=str(error))
        report.update(port_closed=not getattr(serial,"is_open",False),goal_packets=guard.goal_packets,
                      blocked_packets=guard.blocked_packets,metrics=metrics,
                      read_max_gap_seconds=max(cadence.gaps,default=None) if cadence else None)
        if report["port_closed"]:
            guard.restore()
        if rows:
            with (out/"samples.csv").open("x",encoding="utf-8",newline="") as stream:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        with (out/"RESULT.json").open("x",encoding="utf-8") as stream:
            json.dump(report,stream,ensure_ascii=False,allow_nan=False,indent=2)
        if args.motion and (report["status"]=="HOLD" or not report["port_closed"]):
            with (root/"SERVO_STEP_STOP.json").open("x",encoding="utf-8") as stream:
                json.dump(dict(status="HOLD",joint=args.joint,site=args.site,output=out.name,goal_packets=guard.goal_packets),stream)
        print(json.dumps(report,ensure_ascii=False))
    return 0 if report["status"] != "HOLD" and report["port_closed"] else 2


if __name__=="__main__":
    raise SystemExit(main())
