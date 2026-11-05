"""장치·모델 없는 transport 시험 fixture. 실제 실행기에서는 참조하지 않는다."""
import time

class Echo:
    config={"fixture":True}
    def call(self,command,payload):
        if command=="echo":return payload
        if command=="fail":raise ValueError("합성 정책 오류")
        if command=="hardware":
            with open("/dev/ttyMOCK_FORBIDDEN"):pass
        raise ValueError("합성 명령 오류")

def factory(options):return Echo()
def slow_factory(options):time.sleep(2);return Echo()
