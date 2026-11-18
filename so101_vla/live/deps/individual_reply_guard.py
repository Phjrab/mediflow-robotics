"""개별 READ 응답 검증과 기존 송신 가드의 명시적 토크 허가 연동. 장치를 열지 않는다."""
import threading
import time
from communication_retry import RetryableWireError


class ReplyRejected(RuntimeError):
    def __init__(self, reason, observed):
        super().__init__(reason)
        self.observed = observed


class StrictIndividualReplies:
    def __init__(self, handler, *, write_guard=None, retry=None):
        self.handler = handler
        self.write_guard = write_guard
        self.owner = threading.get_ident()
        self.original = handler.txRxPacket
        self.previous = handler.__dict__.get("txRxPacket")
        self.had_instance_method = "txRxPacket" in handler.__dict__
        self.installed = False
        self.fault = None
        self.history = []
        self.retry=retry

    def reject(self, reason, observed):
        self.fault = dict(reason=reason, **observed)
        raise ReplyRejected(reason, self.fault)

    def receive(self, port, request):
        if self.retry is None:return self.receive_once(port,request)
        if len(request)!=8 or request[4]!=2:return self.receive_once(port,request)
        operation=f'read:{request[5]}:{request[6]}'
        return self.retry.perform([request[2]],operation,lambda:self.receive_once(port,request),port=port)

    def receive_once(self, port, request):
        if self.fault is not None:
            raise ReplyRejected("이전 응답 오류; 추가 통신 금지", self.fault)
        if threading.get_ident() != self.owner:
            self.reject("응답 소유 스레드 불일치", {})
        if isinstance(request,(list,tuple,bytes,bytearray)) and len(request)>4 and request[4]!=2:
            return self.forward_authorized_torque(port,request)
        if len(request) != 8 or request[4] != 2 or type(request[2]) is not int or request[2] not in range(1, 7) or (request[6] not in (1, 2, 4) and (request[5],request[6]) not in ((33,38),(56,15))):
            self.reject("검토하지 않은 개별 READ 요청", {})
        motor, address, width = request[2], request[5], request[6]
        row = dict(unix=time.time(),request_started_monotonic_s=time.monotonic(), expected_id=motor, address=address, expected_bytes=width)
        try:
            packet, communication, error = self.original(port, request)
        except BaseException as cause:
            self.reject("개별 READ 수신 예외", dict(row, error_kind=type(cause).__name__))
        valid = isinstance(packet, (list, tuple, bytes, bytearray)) and all(type(v) is int and 0 <= v <= 255 for v in packet)
        raw = bytes(packet) if valid else b""
        row.update(raw_hex=raw.hex()[:512], communication=communication, status=error,
                   received_id=raw[2] if len(raw)>2 else None,
                   checksum_valid=len(raw)>=6 and sum(raw[2:])%256==255,
                   observed_monotonic_s=time.monotonic())
        self.history.append(row)
        self.history = self.history[-32:]
        if self.retry is not None:
            self.retry.receive(motor,f'read:{address}:{width}',raw,communication)
            if (communication in (0,-6,-7) and valid and len(raw)==width+6 and raw[:2]==b'\xff\xff'
                    and raw[3]==width+2 and sum(raw[2:])%256==255 and raw[2]!=motor):
                raise RetryableWireError('개별 READ ID 불일치; 패킷 폐기',dict(row,kind='id_mismatch'))
            if communication in (-6,-7):raise RetryableWireError('개별 READ 통신 실패',row)
        if type(communication) is not int or communication != 0:
            self.reject("개별 READ 통신 실패", row)
        if not valid or len(raw) != width + 6 or raw[:2] != b"\xff\xff" or raw[2] != motor or raw[3] != width + 2:
            self.reject("개별 READ ID·길이·헤더 불일치", row)
        if sum(raw[2:]) % 256 != 255:
            self.reject("개별 READ 체크섬 불일치", row)
        if raw[4] != 0 or type(error) is not int or error != 0:
            self.reject("개별 READ 모터 오류 상태", row)
        return packet, communication, error

    def forward_authorized_torque(self, port, request):
        """SDK의 broadcast 쓰기는 응답이 없다. 기존 가드가 허가한 동일 토크 패킷만 전달."""
        guard=self.write_guard
        row=dict(unix=time.time(),request_kind="NON_READ",instruction=request[4],
                 address=request[5] if len(request)>5 else None,request_length=len(request))
        valid=isinstance(request,list) and len(request)==20 and all(type(v) is int and 0<=v<=255 for v in request)
        if not valid or request[2:7]!=[254,16,131,40,1] or request[:2] not in ([0,0],[255,255]):
            self.reject("검토하지 않은 비READ 요청; 송신 금지",row)
        head=bytes((255,255))+bytes(request[2:-1])
        canonical=head+bytes(((~sum(head[2:]))&255,))
        installed=getattr(getattr(type(port),"writePort",None),"_torque_write_guard",None)
        if guard is None or installed is not guard or getattr(guard,"port",None) is not port or guard.phase!="write_explicit_torque" or guard.expected!=canonical:
            self.reject("기존 송신 가드의 명시적 토크 허가 없음",row)
        if guard.mode not in ("enable","disable") or (guard.mode=="enable" and not guard.goal_verified):
            self.reject("토크 모드 또는 기존 목표 검증 불충족",row)
        target=1 if guard.mode=="enable" else 0
        if request[7:-1:2]!=list(range(1,7)) or request[8:-1:2]!=[target]*6:
            self.reject("토크 대상ID·값 불일치",row)
        before=guard.writes
        row.update(request_kind="AUTHORIZED_TORQUE_CANDIDATE",packet_hex=canonical.hex(),writes_before=before)
        try:
            result=self.original(port,request)
        except BaseException as cause:
            self.reject("허가된 토크 송신 경로 오류; 재시도 금지",dict(row,error_kind=type(cause).__name__,writes_after=guard.writes))
        if len(result)!=3 or type(result[1]) is not int or result[1]!=0 or type(result[2]) is not int or result[2]!=0 or guard.expected is not None or guard.writes!=before+1:
            self.reject("토크 송신 결과 또는 단일 허가 소비 불일치; 재시도 금지",dict(row,writes_after=guard.writes))
        self.history.append(dict(row,communication=result[1],writes_after=guard.writes))
        self.history=self.history[-32:]
        return result

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
