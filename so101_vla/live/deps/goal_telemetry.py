"""기존 전송 경로에서 요청·SDK 반환·허용된 패킷을 기록한다. 장치 접근 없음."""
from first_trial_core import JOINTS,Hold,vector

def decode_goal(packet):
    data=bytes(packet)
    if len(data)!=26 or data[:2]!=b'\xff\xff' or data[2:7]!=bytes((254,22,131,42,2)) or sum(data[2:])%256!=255:
        raise Hold('목표 계측 패킷 형식 불일치')
    values={}
    for offset in range(7,25,3):
        motor_id=data[offset]
        if motor_id not in range(1,7) or motor_id in values:raise Hold('목표 계측 모터ID 불일치')
        values[motor_id]=data[offset+1]+256*data[offset+2]
    if set(values)!=set(range(1,7)):raise Hold('목표 계측6축 누락')
    return values

class GoalTelemetry:
    def __init__(self):self.rows=[];self.active=None
    def begin(self,requested):
        if self.active is not None:raise Hold('목표 계측 중첩 호출')
        row=dict(index=len(self.rows),requested=vector(requested),status='REQUESTED',packet_captured=False)
        self.rows.append(row);self.active=row
    def capture(self,packet,normalize):
        if self.active is None or self.active['packet_captured']:raise Hold('목표 계측 권한/중복 패킷 오류')
        raw=decode_goal(packet);normal=normalize(dict(raw))
        if set(normal)!=set(range(1,7)):raise Hold('목표 계측 정규화ID 불일치')
        values=vector([normal[i] for i in range(1,7)])
        self.active.update(packet_captured=True,wire_raw={name:raw[i+1] for i,name in enumerate(JOINTS)},
            wire_normalized=values,packet_hex=bytes(packet).hex(),status='PACKET_VALIDATED_BEFORE_WRITE')
    def finish(self,returned):
        if self.active is None or not self.active['packet_captured']:raise Hold('목표 계측 전송 패킷 없음')
        if not isinstance(returned,dict) or set(returned)!={name+'.pos' for name in JOINTS}:raise Hold('SDK 반환6축 키 불일치')
        values=vector([returned[name+'.pos'] for name in JOINTS])
        self.active.update(sdk_returned=values,status='SDK_RETURNED',
            sdk_minus_requested=[a-b for a,b in zip(values,self.active['requested'])],
            wire_minus_sdk=[a-b for a,b in zip(self.active['wire_normalized'],values)],
            motor_acknowledgement='UNVERIFIED')
        row=self.active;self.active=None;return row
    def finish_encoded(self):
        if self.active is None or not self.active["packet_captured"]:
            raise Hold("목표 계측 전송 패킷 없음")
        row=self.active
        row.update(status="SDK_WRITE_RETURNED",motor_acknowledgement="UNVERIFIED")
        self.active=None
        return row
    def fail(self,error):
        if self.active is not None:
            self.active.update(status='ERROR',error_kind=type(error).__name__);self.active=None
