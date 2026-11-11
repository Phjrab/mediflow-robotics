"""동기 읽기 응답을 해석하기 전에 검증한다. 설치된 SDK 파일은 수정하지 않는다."""
import threading
import time


class ReplyFault(RuntimeError):
    def __init__(self, reason, observed):
        super().__init__(reason)
        self.observed = observed


class StrictSyncReplies:
    """GroupSyncRead의 수신부만 교체. 오류 후 재수신·추가 송신 없음."""
    def __init__(self, reader, packet_handler):
        self.reader = reader
        self.original = packet_handler
        self.owner = threading.get_ident()
        self.history = []
        self.fault = None
        self.register = None
        self.installed = False

    def install(self):
        if self.installed or self.reader.ph is not self.original:
            raise RuntimeError("동기 읽기 응답 보호의 소유권 불일치")
        self.reader.ph = self
        self.installed = True
        return self

    def begin(self, register):
        self._ready()
        self.register = register

    def _ready(self):
        if self.fault is not None:
            raise ReplyFault("이전 응답 오류로 추가 통신 거부", self.fault)
        if threading.get_ident() != self.owner:
            raise RuntimeError("응답 처리 스레드 소유권 불일치")

    def _refuse(self, reason, row):
        self.fault = dict(reason=reason, **row, recent_replies=list(self.history))
        raise ReplyFault(reason, self.fault)

    def syncReadTx(self, *args):
        self._ready()
        return self.original.syncReadTx(*args)

    def readRx(self, port, motor_id, length):
        self._ready()
        if type(motor_id) is not int or motor_id not in range(1, 7) or type(length) is not int or length not in (1, 2, 4):
            self._refuse("요청 ID/길이 미확인", dict(register=self.register))
        row = dict(unix=time.time(), register=self.register,
                   expected_id=motor_id, expected_bytes=length)
        try:
            packet, comm = self.original.rxPacket(port)
        except Exception as error:
            self._refuse("SDK 수신 예외", dict(**row, error_kind=type(error).__name__))
        valid_type = isinstance(packet, (list, tuple, bytes, bytearray))
        valid_type = valid_type and all(type(v) is int and 0 <= v <= 255 for v in packet)
        raw = bytes(packet) if valid_type else b""
        row.update(communication=comm if type(comm) is int else repr(comm),
                   raw_hex=raw.hex()[:512], received_bytes=len(raw),
                   received_id=raw[2] if len(raw) > 2 else None,
                   status=raw[4] if len(raw) > 4 else None)
        self.history.append(row)
        self.history = self.history[-32:]
        if type(comm) is not int or comm != 0:
            self._refuse("SDK 통신 실패; 재시도 안 함", row)
        if not valid_type:
            self._refuse("응답 바이트 형식 오류", row)
        if len(raw) != length + 6 or raw[:2] != b"\xff\xff" or raw[3] != length + 2:
            self._refuse("요청과 응답 길이/헤더 불일치", row)
        if raw[2] != motor_id:
            self._refuse("요청과 응답 모터 ID 불일치", row)
        if sum(raw[2:]) % 256 != 255:
            self._refuse("응답 체크섬 불일치", row)
        if raw[4] != 0:
            self._refuse("모터 오류 상태 응답; 추가 통신 중단", row)
        return list(raw[5:-1]), comm, 0

    def restore(self):
        if not self.installed:
            return
        if self.reader.ph is not self:
            raise RuntimeError("응답 보호가 다른 코드에 의해 변경됨")
        self.reader.ph = self.original
        self.installed = False
