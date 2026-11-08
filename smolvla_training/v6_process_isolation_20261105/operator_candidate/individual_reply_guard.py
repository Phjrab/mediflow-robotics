"""개별 READ 응답을 슬라이스 전에 검사한다. 장치 연결·쓰기 기능 없음."""
import threading
import time


class ReplyRejected(RuntimeError):
    def __init__(self, reason, observed):
        super().__init__(reason)
        self.observed = observed


class StrictIndividualReplies:
    def __init__(self, handler):
        self.handler = handler
        self.owner = threading.get_ident()
        self.original = handler.txRxPacket
        self.previous = handler.__dict__.get("txRxPacket")
        self.had_instance_method = "txRxPacket" in handler.__dict__
        self.installed = False
        self.fault = None
        self.history = []

    def reject(self, reason, observed):
        self.fault = dict(reason=reason, **observed)
        raise ReplyRejected(reason, self.fault)

    def receive(self, port, request):
        if self.fault is not None:
            raise ReplyRejected("이전 응답 오류; 추가 통신 금지", self.fault)
        if threading.get_ident() != self.owner:
            self.reject("응답 소유 스레드 불일치", {})
        if len(request) != 8 or request[4] != 2 or type(request[2]) is not int or request[2] not in range(1, 7) or request[6] not in (1, 2, 4):
            self.reject("검토하지 않은 개별 READ 요청", {})
        motor, address, width = request[2], request[5], request[6]
        row = dict(unix=time.time(), expected_id=motor, address=address, expected_bytes=width)
        try:
            packet, communication, error = self.original(port, request)
        except BaseException as cause:
            self.reject("개별 READ 수신 예외", dict(row, error_kind=type(cause).__name__))
        valid = isinstance(packet, (list, tuple, bytes, bytearray)) and all(type(v) is int and 0 <= v <= 255 for v in packet)
        raw = bytes(packet) if valid else b""
        row.update(raw_hex=raw.hex()[:512], communication=communication, status=error)
        self.history.append(row)
        self.history = self.history[-32:]
        if type(communication) is not int or communication != 0:
            self.reject("개별 READ 통신 실패", row)
        if not valid or len(raw) != width + 6 or raw[:2] != b"\xff\xff" or raw[2] != motor or raw[3] != width + 2:
            self.reject("개별 READ ID·길이·헤더 불일치", row)
        if sum(raw[2:]) % 256 != 255:
            self.reject("개별 READ 체크섬 불일치", row)
        if raw[4] != 0 or type(error) is not int or error != 0:
            self.reject("개별 READ 모터 오류 상태", row)
        return packet, communication, error

    def install(self):
        if self.installed or getattr(self.handler, "_individual_reply_guard", None) is not None:
            raise RuntimeError("개별 응답 보호 중복 설치")
        self.handler.txRxPacket = self.receive
        self.handler._individual_reply_guard = self
        self.installed = True
        return self

    def restore(self):
        if not self.installed:
            return
        if getattr(self.handler, "_individual_reply_guard", None) is not self:
            raise RuntimeError("개별 응답 보호 소유권 불일치")
        if self.had_instance_method:
            self.handler.txRxPacket = self.previous
        else:
            del self.handler.txRxPacket
        del self.handler._individual_reply_guard
        self.installed = False

    def __enter__(self):
        return self.install()

    def __exit__(self, *args):
        self.restore()


def read_target_feedback(bus, joints):
    """공급사 ST 표의 주소67 현재 목표만 읽는다. 토크ON 허가를 반환하지 않는다."""
    strict = getattr(bus.packet_handler, "_individual_reply_guard", None)
    if strict is None or not strict.installed or strict.fault is not None:
        raise RuntimeError("개별 원시 응답 보호 없이 목표 피드백 조회 금지")
    if len(joints) != 6 or [bus.motors[j].id for j in joints] != list(range(1, 7)):
        raise RuntimeError("관절 순서·ID 불일치")
    values = {}
    for joint in joints:
        data, communication, error = bus.packet_handler.readTxRx(bus.port_handler, bus.motors[joint].id, 67, 2)
        if communication != 0 or error != 0 or len(data) != 2:
            raise RuntimeError("목표 피드백 디코드 불일치")
        values[joint] = int.from_bytes(bytes(data), "little")
    return values
