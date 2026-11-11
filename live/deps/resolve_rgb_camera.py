"""같은 D435의 RGB 인터페이스만 확인해 이 실행용 파생 설정을 만든다."""
import errno
import hashlib
import json
import os
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parent
EXPECTED={'vendor':'8086','product':'0b07','serial':'236223023645','interface':'03','fourcc':'YUYV'}

def identity(path):
    node=Path(path).resolve(strict=True)
    if node.parent!=Path('/dev') or not node.name.startswith('video'):raise ValueError('V4L 노드 아님')
    interface=(Path('/sys/class/video4linux')/node.name/'device').resolve()
    result={'interface':(interface/'bInterfaceNumber').read_text().strip()}
    usb=next(p for p in (interface,*interface.parents) if (p/'idVendor').is_file())
    result.update(vendor=(usb/'idVendor').read_text().strip(),product=(usb/'idProduct').read_text().strip(),serial=(usb/'serial').read_text().strip())
    return result

def formats(path):
    import fcntl
    fd=os.open(str(path),os.O_RDONLY|os.O_NONBLOCK)
    try:
        result=[]
        for i in range(16):
            fmt=bytearray(64);struct.pack_into('II',fmt,0,i,1)
            try:fcntl.ioctl(fd,0xc0405602,fmt,True)
            except OSError as error:
                if error.errno==errno.EINVAL:break
                raise
            result.append(bytes(fmt[44:48]).decode('ascii'))
        return result
    finally:os.close(fd)

def main():
    source=Path.home()/'nov_live_prep_20261104/vla_observation_config.json'
    out=ROOT/'resolved_camera_config.json'
    if out.exists():raise RuntimeError('파생 설정 이미 존재: 덮어쓰기/자동 재선택 거부')
    config=json.loads(source.read_text())
    candidates=[]
    for p in Path('/dev/v4l/by-path').glob('*:1.3-video-index0'):
        ident=identity(p)
        if ident!={k:v for k,v in EXPECTED.items() if k!='fourcc'}:continue
        supported=formats(p)
        if supported!=['YUYV']:raise RuntimeError('확인된 D435 RGB YUYV 단일 형식과 다름')
        candidates.append(p)
    if len(candidates)!=1:raise RuntimeError('검증 가능한 동일 D435 RGB 경로가 정확히 1개가 아님')
    config['cameras']['ceiling_oblique']=str(candidates[0])
    config['camera_identity']={'ceiling_oblique':EXPECTED}
    config['derived_from']={'path':'nov_live_prep_20261104/vla_observation_config.json','sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    with out.open('x',encoding='utf-8') as f:json.dump(config,f,ensure_ascii=False,indent=2)
    print(json.dumps({'status':'SAME_D435_RGB_RESOLVED','node':str(candidates[0].resolve()),'identity':EXPECTED,'source_config_unchanged':True}))

if __name__=='__main__':main()
