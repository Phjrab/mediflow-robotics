"""6축 온도·전압 원문 진단. 명령2(READ)만 전송, 토크ON/55°C/응답 오류에서 종료."""
import argparse
import json
import os
from pathlib import Path
import time

from thermal_diagnostics import ThermalDiagnostics

JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')
FOLLOWER = '/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00'


class ReadFault(RuntimeError):
    def __init__(self, reason, **observed):
        super().__init__(reason)
        self.observed = observed


def read_request(mid, address, width):
    if type(mid) is not int or not 1 <= mid <= 6 or (address, width) not in ((40, 1), (56, 15)):
        raise ValueError('검토한 READ 주소·길이·ID만 허용')
    data = bytes((255, 255, mid, 4, 2, address, width))
    return data + bytes(((~sum(data[2:])) & 255,))


def decode_reply(raw, mid, width):
    context = dict(expected_id=mid, expected_bytes=width, raw_hex=raw.hex())
    if len(raw) != width + 6 or raw[:4] != bytes((255, 255, mid, width + 2)):
        raise ReadFault('응답 ID·길이·헤더 불일치', **context)
    if sum(raw[2:]) % 256 != 255:
        raise ReadFault('체크섬 실패', **context)
    if raw[4] != 0:
        raise ReadFault('모터 응답 상태 오류', status=raw[4], **context)
    return raw[5:-1]


class LinuxReadTransport:
    """Linux 포트를 단독 소유한다. READ 패킷 이외 전송, 입력 flush, 재시도는 없다."""
    def __init__(self, port):
        if str(port) != FOLLOWER:
            raise ValueError('원래 팔로워 by-id 이외 포트 사용 금지')
        self.port = port
        self.fd = None
        self.original = None
        self.cleanup_errors = []
        self.read_packets = 0

    def open(self):
        import fcntl
        import subprocess
        import termios
        owner = subprocess.run(['sudo', '-n', 'fuser', '--', str(Path(self.port).resolve(strict=True))],
                               text=True, capture_output=True, timeout=5)
        if owner.returncode != 1 or owner.stdout.strip() or owner.stderr.strip():
            raise ReadFault('포트 비점유 확인 실패; 장치 미연결',
                            code=owner.returncode, owners=owner.stdout.strip(), error=owner.stderr.strip())
        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            self.original = termios.tcgetattr(self.fd)
            attrs = termios.tcgetattr(self.fd)
            attrs[0] = attrs[1] = attrs[3] = 0
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attrs[4] = attrs[5] = termios.B1000000
            attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        except BaseException:
            self.close()
            raise

    def request(self, packet, width):
        import array
        import fcntl
        import select
        import termios
        if self.fd is None or packet != read_request(packet[2], packet[5], width):
            raise ReadFault('READ 전송 계약 불일치')
        pending = array.array('i', [0])
        fcntl.ioctl(self.fd, termios.FIONREAD, pending, True)
        if pending[0]:
            raise ReadFault('이전 수신 잔여; 입력을 지우지 않고 중단', queued_bytes=pending[0])
        if os.write(self.fd, packet) != len(packet):
            raise ReadFault('READ 송신 불완전; 재시도 없음')
        self.read_packets += 1
        end, raw = time.monotonic() + .15, bytearray()
        while len(raw) < width + 6:
            left = end - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                raise ReadFault('READ 통신 timeout; 재시도 없음', raw_hex=raw.hex(), communication=-6)
            data = os.read(self.fd, width + 6 - len(raw))
            if not data:
                raise ReadFault('USB EOF; 재시도 없음', raw_hex=raw.hex())
            raw.extend(data)
        return bytes(raw)

    def close(self):
        if self.fd is None:
            return
        import fcntl
        import termios
        try:
            if self.original is not None:
                try:
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
                except OSError as error:
                    self.cleanup_errors.append(str(error))
            try:
                fcntl.ioctl(self.fd, termios.TIOCNXCL)
            except OSError as error:
                self.cleanup_errors.append(str(error))
        finally:
            try:
                os.close(self.fd)
            except OSError as error:
                self.cleanup_errors.append(str(error))
            self.fd = None


def run(transport, log, duration=120, hz=30, *, clock=time.monotonic, pause=time.sleep):
    observer = ThermalDiagnostics(JOINTS)
    result = dict(status='HOLD', cycles=0, motor_register_writes=0, live='NOT_RUN',
                  requested_seconds=duration, requested_hz=hz)
    started = clock()
    next_tick = started
    attempts = 0

    def read(mid, address, width):
        nonlocal attempts
        tx = read_request(mid, address, width)
        attempts += 1
        raw = transport.request(tx, width)
        data = decode_reply(raw, mid, width)
        return data, dict(id=mid, address=address, width=width, tx_hex=tx.hex(), rx_hex=raw.hex())

    try:
        transport.open()
        while clock() - started < duration:
            pause(max(0, next_tick - clock()))
            if clock() - started >= duration:
                break
            cycle = result['cycles']
            torque_rows = []
            for mid, joint in enumerate(JOINTS, 1):
                data, trace = read(mid, 40, 1)
                torque_rows.append(dict(joint=joint, torque=data[0], **trace))
                if data[0] != 0:
                    log(dict(event='torque_not_off', cycle=cycle, joint=joint, torque=data[0],
                             responses=torque_rows))
                    raise ReadFault('토크ON/미확인; 쓰기 없이 중단', joint=joint, torque=data[0])
            for mid, joint in enumerate(JOINTS, 1):
                data, trace = read(mid, 56, 15)
                now = clock()
                temp, volt, status = data[7], data[6] / 10, data[9]
                evidence = observer.observe_axis(joint, now, temp, volt, status)
                log(dict(event='thermal_readonly_sample', cycle=cycle, joint=joint,
                         monotonic_s=now, elapsed_s=now-started, temperature_raw_c=temp,
                         voltage_v=volt, status_register=status, torque=0, **trace,
                         same_packet=dict(position_ticks=int.from_bytes(data[0:2], 'little'),
                                          velocity_register_raw=int.from_bytes(data[2:4], 'little'),
                                          load_register_raw=int.from_bytes(data[4:6], 'little'),
                                          current_register_raw=int.from_bytes(data[13:15], 'little'),
                                          payload_hex=data.hex()), diagnostics=evidence,
                         torque_responses=torque_rows if mid == 1 else []))
                if temp >= 55 or status != 0:
                    raise ReadFault('원본 온도55°C/상태 오류; 보호 유지', joint=joint,
                                    temperature_c=temp, status=status, voltage_v=volt)
            result['cycles'] += 1
            next_tick = max(next_tick + 1 / hz, clock())
        result['status'] = 'READONLY_COMPLETE'
    except KeyboardInterrupt:
        result.update(status='STOPPED', error='Ctrl+C')
    except Exception as error:
        result['error'] = observer.record_error(error)
        log(dict(event='readonly_stop', **result['error']))
    finally:
        transport.close()
        result.update(elapsed_s=clock()-started, read_packet_attempts=attempts,
                      thermal_diagnostics=observer.summary(), port_closed=transport.fd is None,
                      cleanup_errors=list(transport.cleanup_errors))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--dc-on', action='store_true', help='운영자가 실제 팔로워 DC ON을 확인한 경우만')
    p.add_argument('--duration', type=float, default=120)
    p.add_argument('--hz', type=float, default=30)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if not a.execute or not a.dc_on:
        p.error('실제 DC ON 확인 후 --execute --dc-on 필요; 장치 미연결')
    if not 0 < a.duration <= 120 or not 0 < a.hz <= 30:
        p.error('시간은120초 이하, 읽기는30Hz 이하')
    if not Path(FOLLOWER).exists():
        p.error('원래 팔로워 USB by-id 없음; 리더 포트로 대체하지 않음')
    out = a.out.expanduser()
    out.mkdir(parents=True, exist_ok=False)
    with (out / 'readings.jsonl').open('x', encoding='utf-8', buffering=1) as f:
        def log(row):
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
        result = run(LinuxReadTransport(FOLLOWER), log, a.duration, a.hz)
    result['output'] = str(out)
    with (out / 'summary.json').open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, allow_nan=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'READONLY_COMPLETE' and result['port_closed'] and not result['cleanup_errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
