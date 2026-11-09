"""Read-only home monitor and locally confirmed SO-101 torque recovery."""

import argparse
import contextlib
import json
from pathlib import Path
import statistics
import sys
import threading
import time

from first_trial_core import JOINTS, Hold, Limits, sensor_gate
from hold_continuation import CAL_SHA, HOME_SHA
from servo_step_response import (ELBOW_TRAVEL, READS, REGISTERS, Cadence, MAX_SAMPLE_GAP,
                                 active_goals, digest, inspect_limits, read_bus_snapshot,
                                 verify_stops)


SETUP_TIMEOUT = 90
REGISTER = {"Goal_Position": (42,2), "Torque_Enable": (40,1)}


def home_rows(snapshot, home):
    rows = {}
    for index, joint in enumerate(JOINTS):
        position = snapshot["state"][index]
        mean = home["state"][index]
        delta = position-mean
        rows[joint] = dict(current=position,home=mean,delta=delta,inside=abs(delta)<=2,
                           unit="점" if index==5 else "도",positive_physical_direction="UNVERIFIED")
    return rows


def check_state(row, protection, expected_torque):
    sensor_gate(row,Limits(),starting=True,expected_torque=expected_torque)
    if any(abs(row["currents"][j]) >= protection[j] for j in JOINTS):
        raise Hold("전류 보호 상한")
    if abs(row["state"][2]) > ELBOW_TRAVEL:
        raise Hold("팔꿈치 가동범위 이탈")


def guarded_recovery_permission(bus, allow_write, emit):
    from live_safety_runtime import PacketPermission
    reads = {**READS, 42:2}

    class RecoveryPermission(PacketPermission):
        READS = reads

        def __init__(self, port):
            super().__init__(port,allow_goal=False)
            self.expected = None
            self.writes = []
            self.used = set()

        def inspect(self, port, packet):
            if len(packet) > 4 and packet[4] == 131:
                actual = bytes(packet)
                expected = self.expected
                if (not allow_write or self.closed or threading.get_ident()!=self.owner or
                    port is not self.port or expected is None or actual!=expected[0] or
                    time.monotonic() > expected[2]):
                    self.blocked_packets += 1
                    raise Hold("승인된 목표·토크 쓰기 이외 패킷 차단")
                self.expected = None
                record = dict(expected[1],stage="ATTEMPTED",unix=time.time())
                self.writes.append(record)
                emit(record)
                return "recovery_write"
            return super().inspect(port,packet)

        @contextlib.contextmanager
        def authorize(self, name, values, phase):
            if not allow_write or self.expected is not None or phase in self.used or tuple(values)!=JOINTS:
                raise Hold("복구 쓰기 권한 또는 관절 순서 불일치")
            address,width = REGISTER[name]
            if name=="Goal_Position":
                if phase!="match_present" or any(type(v) is not int or not 0<=v<=4095 for v in values.values()):
                    raise Hold("목표 쓰기 범위 오류")
            elif phase not in ("enable", "safety_off") or any(
                type(v) is not int or v!=int(phase=="enable") for v in values.values()
            ):
                raise Hold("토크 쓰기 범위 오류")
            params = bytes((address,width))+b"".join(bytes((i,))+values[j].to_bytes(width,"little")
                                                      for i,j in enumerate(JOINTS,1))
            head=bytes((255,255,254,len(params)+2,131))+params
            packet=head+bytes(((~sum(head[2:]))&255,))
            self.used.add(phase)
            self.expected = packet,dict(register=name,address=address,phase=phase,values=values.copy()),time.monotonic()+.1
            try:
                yield
                if self.expected is not None:
                    raise Hold("지정 패킷을 송신하지 못함")
                self.writes[-1]["stage"]="TRANSMITTED"
                emit(self.writes[-1])
            finally:
                self.expected=None

    return RecoveryPermission(bus.port_handler).install()


def write(bus, guard, name, values, phase):
    with guard.authorize(name,values,phase):
        bus.sync_write(name,values,normalize=False,num_retry=0)


def read_rate(read, report):
    cadence=Cadence()
    count=0
    try:
        for _ in range(121):
            cadence.sample(read)
            count+=1
    finally:
        report.update(samples=count,max_gap_ms=(max(cadence.gaps)*1000 if cadence.gaps else None),
                      average_hz=(len(cadence.gaps)/sum(cadence.gaps) if cadence.gaps else None))


def startup_max_delta(row, baseline):
    return {joint:abs(row["state"][i]-baseline["state"][i]) for i,joint in enumerate(JOINTS)}


def enable_and_record(bus, guard, read, read_goal, home, report, emit):
    baseline=read(0)
    if not all(r["inside"] for r in home_rows(baseline,home).values()):
        raise Hold("home 허용 범위 밖. 6축 ±2 안에 맞춘 뒤 실행")
    raw=baseline["ticks"]
    # Both the programmed target and the device's active target must agree with fresh feedback.
    try:
        write(bus,guard,"Goal_Position",raw,"match_present")
        verified=read(0)
        control=read_goal()
        active=active_goals(bus)
        fresh=read(0)
        report["goal_verification"]=dict(target=raw,control=control,active=active,present=fresh["ticks"])
        if not (raw==verified["ticks"]==control==active==fresh["ticks"]):
            raise Hold("목표와 현재 원시값 재확인 불일치")
        write(bus,guard,"Torque_Enable",dict.fromkeys(JOINTS,1),"enable")
        started=time.monotonic()
        maxima=dict.fromkeys(JOINTS,0.)
        while True:
            row=read(1)
            elapsed=time.monotonic()-started
            delta=startup_max_delta(row,baseline)
            maxima={j:max(maxima[j],delta[j]) for j in JOINTS}
            report["startup_max_delta"]=maxima
            emit(dict(sample="startup",seconds=elapsed,delta=delta,feedback=row))
            if any(delta[j]>(3 if j=="gripper" else 1) for j in JOINTS):
                raise Hold("토크 활성화 후 이동 상한 초과")
            if elapsed>=1:
                break
        started=time.monotonic()
        snapshots=[]
        while time.monotonic()-started<10:
            row=read(1)
            if any(delta>(3 if j=="gripper" else 1) for j,delta in startup_max_delta(row,baseline).items()):
                raise Hold("home 부하 계측 중 이동 상한 초과")
            snapshots.append(row)
            emit(dict(sample="home_load",seconds=time.monotonic()-started,feedback=row))
            time.sleep(.02)
        report["load_window"]=dict(seconds=time.monotonic()-started,samples=len(snapshots),
                                   joints={j:dict(load_min=min(r["loads"][j] for r in snapshots),
                                                  load_median=statistics.median(r["loads"][j] for r in snapshots),
                                                  load_max=max(r["loads"][j] for r in snapshots),
                                                  current_max_abs=max(abs(r["currents"][j]) for r in snapshots),
                                                  temp_max_c=max(r["temperatures"][j] for r in snapshots))
                                           for j in JOINTS})
        report["rate_on"]={}
        def read_on():
            row=read(1)
            if any(delta>(3 if j=="gripper" else 1) for j,delta in startup_max_delta(row,baseline).items()):
                raise Hold("토크 ON 읽기 계측 중 이동 상한 초과")
            return row
        try:
            read_rate(read_on,report["rate_on"])
            report["rate_on"]["status"]="PASS"
        except Hold as error:
            if not str(error).startswith("50Hz 계측 기준 미달"):
                raise
            report["rate_on"].update(status="HOLD",reason=str(error))
    except BaseException:
        if guard.writes:
            try:
                write(bus,guard,"Torque_Enable",dict.fromkeys(JOINTS,0),"safety_off")
                report["safety_off_confirmed"]=all(v==0 for v in read(0)["torque"].values())
            except BaseException as error:
                report.update(safety_off_confirmed=False,safety_off_error=str(error))
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",choices=("rate-off","monitor","arm"))
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--supported",action="store_true")
    args=p.parse_args()
    if args.mode=="arm" and (not args.supported or not sys.stdin.isatty()):
        raise Hold("현장 지지 및 직접 입력 필요")
    root=Path(__file__).resolve().parent
    verify_stops(root)
    home_file=Path.home()/"home_state_so101.json"
    calibration=Path.home()/".cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json"
    if digest(home_file)!=HOME_SHA or digest(calibration)!=CAL_SHA:
        raise Hold("home 또는 보정 파일 해시 불일치")
    home=json.loads(home_file.read_bytes())
    from first_trial_dependencies import prepare
    prepare()
    from torque_readonly_diagnosis import port_owners, service_gate, PORT
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    from reply_integrity import StrictSyncReplies
    import fcntl
    import os
    import termios
    service_gate()
    port_owners(set())
    robot=SO101Follower(SO101FollowerConfig(port=PORT,id="so-101",calibration_dir=calibration.parent,
                         cameras={},use_degrees=True,disable_torque_on_disconnect=False))
    bus=robot.bus
    table=bus.model_ctrl_table["sts3215"]
    if (tuple(bus.motors)!=JOINTS or [bus.motors[j].id for j in JOINTS]!=list(range(1,7)) or
        bus.protocol_version!=0 or any(bus.motors[j].model!="sts3215" for j in JOINTS) or
        any(tuple(table.get(k,()))!=v for k,v in REGISTERS.items())):
        raise Hold("모터 ID 또는 SDK 레지스터 범위 불일치")
    args.out.mkdir(parents=True,exist_ok=False)
    report=dict(mode=args.mode,status="HOLD",writes=[],port_closed=None)
    if args.mode=="arm":
        print("팔을 지지하고 비상 정지 수단을 확보하세요. 움직임 상한 초과 시 토크를 끕니다.",flush=True)
        if input("현장에서 TORQUE_ON 입력: ").strip()!="TORQUE_ON":
            raise Hold("현장 입력 불일치")
        service_gate()
        port_owners(set())
    with (args.out/"events.jsonl").open("x",encoding="utf-8") as events:
        def emit(event):
            events.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+"\n")
            events.flush()
        guard=guarded_recovery_permission(bus,args.mode=="arm",emit)
        replies=StrictSyncReplies(bus.sync_reader,bus.packet_handler).install()
        try:
            bus.connect(handshake=False)
            fcntl.ioctl(bus.port_handler.ser.fileno(),termios.TIOCEXCL)
            port_owners({os.getpid()})
            axes,protection=inspect_limits(bus)
            def read(torque):
                row=read_bus_snapshot(bus,axes)
                check_state(row,protection,torque)
                return row
            first=read_bus_snapshot(bus,axes)
            report["first"]=first
            report["first_home"]=home_rows(first,home)
            check_state(first,protection,0)
            if args.mode=="rate-off":
                report["rate_off"]={}
                read_rate(lambda:read(0),report["rate_off"])
            elif args.mode=="monitor":
                started=time.monotonic()
                while time.monotonic()-started<SETUP_TIMEOUT:
                    sample_start=time.monotonic()
                    rows=home_rows(read(0),home)
                    marker="OK" if all(r["inside"] for r in rows.values()) else "맞추는 중"
                    print(marker+" | "+" | ".join(
                        f"{j} {r['current']:+.2f} {r['unit']} (Δ {r['delta']:+.2f}, {'안' if r['inside'] else '밖'})"
                        for j,r in rows.items()),flush=True)
                    time.sleep(max(0.,.2-(time.monotonic()-sample_start)))
            else:
                def read_goal():
                    replies.begin("Goal_Position")
                    return bus.sync_read("Goal_Position",normalize=False,num_retry=0)
                enable_and_record(bus,guard,read,read_goal,home,report,emit)
            report["status"]="HOLD" if report.get("rate_on",{}).get("status")=="HOLD" else "PASS"
        except KeyboardInterrupt:
            report["status"]="CLOSED" if args.mode=="monitor" else "HOLD"
            report["error"]="운영자 중단"
        except Exception as error:
            report["error"]=str(error)
        finally:
            guard.seal()
            try:
                if bus.is_connected:
                    bus.disconnect(disable_torque=False)
            except Exception as error:
                report.update(status="HOLD",close_error=str(error))
            serial=getattr(bus.port_handler,"ser",None)
            if getattr(serial,"is_open",False):
                try:
                    serial.close()
                except Exception as error:
                    report.update(status="HOLD",close_error=str(error))
            report["port_closed"]=not getattr(serial,"is_open",False)
            guard.restore()
            replies.restore()
            report["writes"]=guard.writes
            report["register_counts"]={reg:sum(w["register"]==reg for w in guard.writes) for reg in REGISTER}
            verify_stops(root)
            if args.mode=="arm" and report["status"]=="PASS" and report["port_closed"]:
                rate_receipt=dict(status="READ_RATE_PASS",joint="elbow_flex",goal_packets=0,
                                  port_closed=True,read_max_gap_seconds=report["rate_on"]["max_gap_ms"]/1000,
                                  source_sha256=digest(root/"servo_step_response.py"))
                if rate_receipt["read_max_gap_seconds"]>MAX_SAMPLE_GAP:
                    raise Hold("토크 ON 읽기 속도 확인 실패")
                with (args.out/"RATE_ON_B.json").open("x",encoding="utf-8") as file:
                    json.dump(rate_receipt,file,indent=2)
            with (args.out/"RESULT.json").open("x",encoding="utf-8") as file:
                json.dump(report,file,ensure_ascii=False,allow_nan=False,indent=2)
            print(json.dumps(report,ensure_ascii=False,allow_nan=False),flush=True)
    return 0 if report["status"] in ("PASS","CLOSED") and report["port_closed"] else 2


if __name__=="__main__":
    raise SystemExit(main())
