"""Do not stop an arm-owning process during a Goal_Position-only replay."""
import json
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
from config_runtime import load
from servo_thermal_readonly import require_unowned_port

def main():
    for name in ('teleoperation','recording','inference'):
        try:
            with urlopen('http://127.0.0.1:8022/'+name+'-status',timeout=3) as response:r=json.load(response)
        except URLError:continue
        if r.get(name+'_active'):
            raise RuntimeError('녹화 재생은 토크 쓰기 금지: LeLab 작업을 직접 종료한 뒤 실행하세요: '+name)
    require_unowned_port(Path(load()['port']).resolve(strict=True))

if __name__=='__main__':main()
