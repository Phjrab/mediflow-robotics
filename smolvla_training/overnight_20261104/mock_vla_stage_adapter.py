"""기존 RobotController 단계 API와 연결하는 모의 어댑터. 물리 backend는 없음."""
from types import SimpleNamespace
from cached_action_replay import InMemoryRobot,SoftwareOnlyController


class MockVLAStageAdapter:
    physical=False

    def __init__(self,state,packets):
        self.backend=InMemoryRobot(state);self.controller=SoftwareOnlyController(self.backend)
        self.packets=list(packets);self.events=[];self.results=[];self.connected=False;self.prepared=False

    def connect(self):
        if self.connected or self.backend.closed:raise RuntimeError('모의 연결 상태 오류')
        self.events.append('connect_mock_only');self.connected=True

    def read_state(self):
        self.events.append('read_mock_state')
        return SimpleNamespace(connected=self.connected,moving=False,torque_enabled=None,temperatures_c=())

    def prepare(self):
        if not self.connected:raise RuntimeError('모의 연결 필요')
        self.prepared=True;self.events.append('prepare_mock_only')

    def execute_stage(self,stage):
        if stage!='vla_chunk_mock' or not self.prepared or self.backend.stopped:raise RuntimeError('모의 VLA 단계 거부')
        self.events.append('execute_vla_chunk_mock')
        for packet in self.packets:
            result=self.controller.step(packet);self.results.append(result)
            if result['decision']!='VALIDATED_TARGET_ONLY':raise RuntimeError('VLA_MOCK_HOLD: '+result['reason'])

    def stop_new_commands(self):
        self.events.append('stop_new_commands_no_write');self.backend.hold()

    def close(self):
        self.events.append('close_mock_only');self.controller.close();self.connected=False
