"""현장 실행기용 보조 안전 처리. import 시 장치/GPU 접근 없음."""
from contextlib import contextmanager
import math
from pathlib import Path
import queue
import threading
import time


class Hold(RuntimeError):
    pass


def check_stop_record(root=None):
    root = Path.home() / "nov_live_prep_20261104" if root is None else Path(root)
    if (root / "SERIAL_STOP.json").exists() or next(root.glob("SERIAL_STOP.cleared_*.json"), None) is not None:
        raise Hold("중단 기록 또는 이름 변경 보존본 존재: 현장 재무장 근거 미확인; 추가 접근/목표 전송 거부")


def monitored_inference(predict, monitor, *, deadline, timeout, poll=0.05,
                        max_monitor_gap=0.25, cancelled=None):
    """정책만 별도 스레드. 호출자 스레드만 serial 소유. 시간 초과 결과 폐기.

    협력적 소프트웨어 감시이며 GIL/SDK 정지에 독립적인 watchdog이 아니다.
    GPU 작업을 강제 취소할 수 없지만 그 스레드에는 모터 전송 권한이 없다.
    """
    values = (deadline, timeout, poll, max_monitor_gap)
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
        raise ValueError("감시 시간 설정 오류")
    if timeout <= 0 or poll <= 0 or poll > max_monitor_gap or max_monitor_gap > 0.25:
        raise ValueError("감시 간격/상한 오류")
    cancelled = threading.Event() if cancelled is None else cancelled
    output = queue.Queue(maxsize=1)
    def watch():
        started = time.monotonic()
        if cancelled.is_set() or started >= deadline:
            raise Hold("정지 요청 또는 전체 실행 시간 상한")
        monitor()
        ended = time.monotonic()
        if ended - started > max_monitor_gap:
            raise Hold("상태 조회 지연: 추가 목표 전송 금지")
        if ended >= deadline:
            raise Hold("상태 조회 중 전체 실행 시간 상한")
    watch()
    start = time.monotonic()
    def worker():
        try:
            value = predict()
            if not cancelled.is_set():
                output.put_nowait((True, value))
        except BaseException as error:
            if not cancelled.is_set():
                output.put_nowait((False, error))
    thread = threading.Thread(target=worker, name="policy-no-device-access", daemon=True)
    thread.start()
    try:
        while True:
            remaining = min(deadline - time.monotonic(), timeout - (time.monotonic() - start))
            if remaining <= 0:
                raise Hold("추론 timeout/전체 시간 상한: 결과 폐기, 추가 목표 전송 금지")
            try:
                ok, value = output.get(timeout=min(poll, remaining))
            except queue.Empty:
                watch()
                continue
            if time.monotonic() - start > timeout:
                raise Hold("추론 timeout: 늦은 결과 폐기")
            watch()
            if not ok:
                raise value
            return value
    except BaseException:
        cancelled.set()
        raise


class PacketPermission:
    """개별/동기 READ와 명시적으로 허용한 Goal_Position 1패킷만 허용.

    serial 소유 스레드 외 전송 금지. 토크/모드/보정/Lock 쓰기 금지.
    """
    READS = {40: 1, 63: 1, 33: 1, 56: 2, 9: 2, 11: 2, 31: 2, 60: 2, 65: 1}

    def __init__(self, port, *, allow_goal=False):
        if type(allow_goal) is not bool:
            raise ValueError("모터 쓰기 허용값 미확인")
        self.port = port
        self.allow_goal = allow_goal
        self.owner = threading.get_ident()
        self.phase = "read"
        self.goal_packets = 0
        self.read_packets = 0
        self.blocked_packets = 0
        self.closed = False
        self._allowance = 0
        self._original = None
        self._port_class = None

    def inspect(self, port, packet):
        def refuse():
            self.blocked_packets += 1
            raise Hold("허가되지 않은 모터 패킷/스레드/포트; 전송 거부")
        if self.closed or threading.get_ident() != self.owner or port is not self.port:
            refuse()
        try:
            data = bytes(packet)
        except (ValueError, TypeError):
            refuse()
        if len(data) < 6 or data[:2] != b"\xff\xff" or len(data) != data[3] + 4 or sum(data[2:]) % 256 != 255:
            refuse()
        motor_id, instruction = data[2], data[4]
        if instruction == 2 and motor_id in range(1, 7) and len(data) == 8 and self.READS.get(data[5]) == data[6]:
            self.read_packets += 1
            return "read"
        if instruction == 0x82 and motor_id == 254 and self.READS.get(data[5]) == data[6]:
            ids = list(data[7:-1])
            if ids and len(set(ids)) == len(ids) and set(ids) <= set(range(1, 7)):
                self.read_packets += 1
                return "read"
        # Feetech protocol 0: sync_write(addr=42, len=2, ids 1..6).
        if instruction == 0x83 and motor_id == 254 and len(data) == 26 and data[5:7] == bytes((42, 2)):
            ids = list(data[7:-1:3])
            if self.allow_goal and self.phase == "goal" and self._allowance == 1 and len(ids) == 6 and set(ids) == set(range(1, 7)):
                self._allowance = 0
                return "goal"
        refuse()

    def install(self):
        cls = type(self.port)
        if self._original is not None:
            raise Hold("패킷 보호 중복 설치 거부")
        self._port_class, self._original = cls, cls.writePort
        guard, original = self, self._original
        def write(port, packet):
            kind = guard.inspect(port, packet)
            # 부분 전송 뒤에도 전송 시도로 기록하고 추가 명령을 내리지 않는다.
            if kind == "goal":
                guard.goal_packets += 1
            written = original(port, packet)
            if written != len(packet):
                raise Hold("모터 패킷 부분 전송; 추가 쓰기 금지")
            return written
        cls.writePort = write
        return self

    @contextmanager
    def goal_once(self):
        if not self.allow_goal or self.closed or self.phase != "read" or threading.get_ident() != self.owner:
            raise Hold("목표 전송 소유권/상태 오류")
        self.phase, self._allowance = "goal", 1
        try:
            yield
            if self._allowance != 0:
                raise Hold("허용한 목표 패킷이 전송되지 않음")
        finally:
            self.phase, self._allowance = "read", 0

    def seal(self):
        self.closed = True
        self.phase, self._allowance = "closed", 0

    def restore(self):
        if self._original is not None:
            self._port_class.writePort = self._original
            self._original = None


def close_without_writes(robot, cameras, connected, permission=None):
    """새 HOLD 목표·토크 변경 없이 포트를 먼저 닫는다. 현재 목표는 하드웨어에 남음.

    현재 위치를 새로 잡아준다는 뜻이 아니며, 남은 목표/토크로 움직일 수 있다.
    """
    errors = []
    if permission is not None:
        permission.seal()
    port = getattr(robot.bus, "port_handler", None)
    serial = getattr(port, "ser", None)
    if connected or (serial is not None and getattr(serial, "is_open", False)):
        try:
            robot.bus.disconnect(disable_torque=False)
        except BaseException as exc:
            errors.append("버스 종료 오류: " + type(exc).__name__)
        finally:
            if serial is not None and getattr(serial, "is_open", False):
                try:
                    serial.close()
                    port.is_open = False
                except BaseException as exc:
                    errors.append("포트 직접 닫기 오류: " + type(exc).__name__)
            if serial is not None and getattr(serial, "is_open", False):
                errors.append("포트 해제 미확인")
    for camera in reversed(cameras):
        try:
            camera.disconnect()
        except BaseException as exc:
            errors.append("카메라 종료 오류: " + type(exc).__name__)
    if permission is not None and (serial is None or not getattr(serial, "is_open", False)):
        permission.restore()
    return errors
